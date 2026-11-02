"""One worker coordinator, concurrent articles and durable queued publication."""
import argparse
from concurrent.futures import ThreadPoolExecutor
import json
import os
from pathlib import Path
import time
from .store import Store, MAX_FILE, DEFAULT_WRITING_CONCURRENCY, now
from .model_gateway import default_model_id


def finish_schedule(store, job, status, stage, error=None):
    with store.connect() as db:
        db.execute("UPDATE jobs SET status=?,stage=?,error=?,updated_at=? WHERE id=? AND status='running' AND attempts=?",
                   (status,stage,error,now(),job['id'],job['attempts']+1))


def schedule_revision(store, enrichment, job):
    payload=json.loads(job['input_json'])
    source_id=payload['sourceId']
    revision_id=payload.get('revisionId') or job.get('revision_id')
    with store.connect() as db:
        source=db.execute('SELECT current_revision FROM sources WHERE id=?',(source_id,)).fetchone()
    # Legacy failed schedule jobs did not freeze a source version. They must not
    # silently process whatever happens to be current after an upgrade.
    if not revision_id or not source or source['current_revision']!=revision_id:
        finish_schedule(store,job,'cancelled','superseded','来源版本已更新，请对最新文档重新生成')
        return
    preferences = enrichment.models.get_preferences()
    if not preferences.get('autoProcess', False) and not preferences.get('autoTranslate', True):
        finish_schedule(store,job,'cancelled','auto_disabled','自动处理已关闭，未调用模型')
        return
    try:
        # queue() rechecks revision_id inside its own publication transaction,
        # closing the race if a source changes after the check above.
        # The same outbox intent schedules a conservative cover selection only
        # for this new revision. It never scans or backfills the existing library.
        if preferences.get('autoProcess', False) and default_model_id(preferences):
            from .card_cover import CardCoverService
            CardCoverService(store,enrichment.models).queue(source_id,revision_id=revision_id,schedule_job=job)
        enrichment.maybe_queue_auto(source_id,revision_id=revision_id,schedule_job=job)
    except Exception:
        with store.connect() as db:
            current=db.execute('SELECT current_revision FROM sources WHERE id=?',(source_id,)).fetchone()
        if not current or current['current_revision']!=revision_id:
            finish_schedule(store,job,'cancelled','superseded','来源版本已更新，请对最新文档重新生成')
        else:
            finish_schedule(store,job,'failed','scheduling','自动加工未排队，请检查模型配置后重试')
        return
    finish_schedule(store,job,'succeeded','ready')


def run_one(store, enrichment=None, wiki=None, writing=None, card_covers=None):
    from .document_deletion import DocumentDeletionService
    cleaned, _ = DocumentDeletionService(store).collect_pending()
    advanced = advance_wiki_all(store, enrichment, wiki)
    job=store.claim()
    if job is None:
        # Only successful source publications created after this feature write
        # this outbox. Installing it never schedules historical material.
        if refresh_wiki_outbox(store, enrichment):
            return True
        from .enrichment import EnrichmentService
        enrichment=enrichment or EnrichmentService(store)
        enrichment.maybe_queue_idle()
        job=store.claim()
        if job is None:return bool(cleaned or advanced)
    return process_job(store, job, enrichment, wiki, writing, card_covers)


def process_job(store, job, enrichment=None, wiki=None, writing=None, card_covers=None):
    """Execute an already claimed job; each dispatch owns its WritingService."""
    try:
        from .enrichment import EnrichmentService
        enrichment=enrichment or EnrichmentService(store)
        if job['kind'] in ('wiki_resolve','wiki_synthesize'):
            from .wiki_resolution import WikiResolutionService
            WikiResolutionService(store,enrichment.models).process(job)
            return True
        if job['kind']=='cover_selection':
            from .card_cover import CardCoverService
            (card_covers or CardCoverService(store,enrichment.models)).process(job)
            return True
        if job['kind'].startswith('writing_'):
            from .writing import WritingService
            (writing or WritingService(store,enrichment.models)).process(job)
            return True
        if job['kind']=='wiki_build':
            from .wiki import WikiService
            (wiki or WikiService(store,enrichment.models)).process(job)
            return True
        if job['kind']=='ai_schedule':
            schedule_revision(store,enrichment,job)
            return True
        if job['kind'].startswith('ai_'):
            enrichment.process(job)
            return True
        from .parser import PARSER_VERSION, parse_html
        payload=json.loads(job['input_json'])
        final_url=payload.get('url','')
        if job['kind']=='url':
            from .fetcher import fetch_html
            fetched=fetch_html(payload['url'])
            raw=fetched['data'];final_url=fetched['final_url']
        else:
            path=Path(payload['path'])
            if path.stat().st_size>MAX_FILE:raise ValueError('文件超过 64 MiB 上限')
            raw=path.read_bytes()
        if len(raw)>MAX_FILE:raise ValueError('文件超过 64 MiB 上限')
        if store.job(job['id'])['status']=='cancelled':return True
        if store.reuse_revision(job,payload,raw,PARSER_VERSION):return True
        store.stage(job['id'],'parsing')
        parsed=parse_html(raw,source_url=final_url,title_hint=payload.get('title',''))
        store.stage(job['id'],'indexing')
        store.publish(job,payload,raw,parsed,final_url)
    except Exception as exc:
        # Unexpected AI adapter errors can contain headers or provider payloads.
        # ModelService/Enrichment own useful sanitized diagnostics; this fallback
        # deliberately avoids echoing exception text for AI jobs.
        message='AI 任务执行异常，请检查模型配置后重试' if job['kind'].startswith(('ai_','writing_','wiki_')) or job['kind']=='cover_selection' else f'{type(exc).__name__}: {exc}'
        if job['kind'].startswith('wiki_') or job['kind']=='cover_selection' or job['kind'].startswith('writing_'):
            # A cancelled/retried attempt may still return from a model call.
            # It must not fail a newer attempt of the same durable task.
            with store.connect() as db:
                db.execute("UPDATE jobs SET status='failed',error=?,updated_at=? WHERE id=? AND status='running' AND attempts=?",
                           (message,now(),job['id'],job['attempts']+1))
        else:
            store.fail(job['id'],message)
    return True


class ConcurrentWorker:
    """Bound writing by articles and keep other foreground work in one lane.

    The coordinator owns all futures. Terminal database status does not release
    a running HTTP request's article or slot; only its finished future does.
    Each automatic parent runs its children in its own thread, retaining the
    frozen request and per-article service context across every step.
    """

    def __init__(self, store, enrichment=None, *, writing_limit=DEFAULT_WRITING_CONCURRENCY, processor=None):
        if isinstance(writing_limit, bool) or not isinstance(writing_limit, int) or writing_limit < 1:
            raise ValueError('写作并发上限须为正整数')
        from .enrichment import EnrichmentService
        self.store = store
        self.enrichment = enrichment or EnrichmentService(store)
        self.writing_limit = writing_limit
        self.processor = processor
        self._pool = ThreadPoolExecutor(max_workers=writing_limit + 1, thread_name_prefix='wenshu')
        self._futures = {}

    def _execute(self, job):
        if self.processor is not None:
            return self.processor(self.store, job)
        # Sharing the stateless model gateway is safe. WritingService holds
        # mutable automatic-parent context and is constructed inside each job.
        return process_job(self.store, job, enrichment=self.enrichment)

    def _submit(self, job):
        future = self._pool.submit(self._execute, job)
        self._futures[future] = job

    def tick(self):
        from .document_deletion import DocumentDeletionService
        cleaned, _ = DocumentDeletionService(self.store).collect_pending()
        worked = bool(cleaned)
        for future in list(self._futures):
            if future.done():
                try:
                    future.result()
                except Exception:
                    # Contain one failed executor lane without abandoning other
                    # articles. Neither provider payloads nor exception text
                    # belong in the durable user-facing failure message.
                    job = self._futures[future]
                    with self.store.connect() as db:
                        db.execute("UPDATE jobs SET status='failed',error=?,updated_at=? WHERE id=? AND status='running' AND attempts=?",
                                   ('任务执行异常，请重试', now(), job['id'], job['attempts'] + 1))
                del self._futures[future]
                worked = True
        if advance_wiki_all(self.store, self.enrichment):
            worked = True
        physical_writing = [job for job in self._futures.values() if job['kind'].startswith('writing_')]
        projects = {job['writing_project_id'] for job in physical_writing}
        for _ in range(self.writing_limit - len(physical_writing)):
            job = self.store.claim(scope='writing', writing_limit=self.writing_limit, excluded_project_ids=projects)
            if job is None:
                break
            self._submit(job)
            projects.add(job['writing_project_id'])
            worked = True
        if not any(not job['kind'].startswith('writing_') for job in self._futures.values()):
            allow_idle = not any(job['kind'].startswith('writing_') for job in self._futures.values())
            job = self.store.claim(scope='other', writing_limit=self.writing_limit, allow_idle=allow_idle)
            if job is None:
                if refresh_wiki_outbox(self.store, self.enrichment):
                    worked = True
                elif allow_idle:
                    self.enrichment.maybe_queue_idle()
                    job = self.store.claim(scope='other', writing_limit=self.writing_limit)
            if job is not None:
                self._submit(job)
                worked = True
        return worked

    def close(self):
        self._pool.shutdown(wait=True)


def advance_wiki_all(store, enrichment=None, wiki=None):
    # Parent coordination is short database work, not a physical source lane.
    # No historical data is scanned or scheduled without an explicit parent.
    with store.connect() as db:
        if not db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='wiki_all_runs'").fetchone():
            return False
        if not db.execute("SELECT 1 FROM jobs WHERE kind='wiki_build_all' AND status IN ('queued','running') LIMIT 1").fetchone():
            return False
    from .wiki import WikiService
    from .enrichment import EnrichmentService
    service = wiki or WikiService(store, (enrichment or EnrichmentService(store)).models)
    return service.advance_all()


def refresh_wiki_outbox(store, enrichment=None):
    """Consume one directed publication event, never scan or retry old failures."""
    with store.connect() as db:
        if not db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='wiki_unified_outbox'").fetchone():
            return False
        row=db.execute("SELECT * FROM wiki_unified_outbox WHERE status='pending' ORDER BY created_at,source_id LIMIT 1").fetchone()
        if row is None:
            return False
        event=dict(row)
        publication=db.execute('SELECT run_id FROM wiki_publications WHERE source_id=?',(event['source_id'],)).fetchone()
    error=None
    try:
        if publication and publication['run_id']==event['run_id']:
            from .wiki_resolution import WikiResolutionService
            from .enrichment import EnrichmentService
            enrichment=enrichment or EnrichmentService(store)
            WikiResolutionService(store,enrichment.models).refresh_source(event['source_id'])
    except Exception:
        error='统一词条增量更新未排队，请在图谱中对该组手动重试综合'
    with store.connect() as db:
        db.execute("UPDATE wiki_unified_outbox SET status=?,error=? WHERE source_id=? AND run_id=? AND status='pending'",
                   ('failed' if error else 'succeeded',error,event['source_id'],event['run_id']))
    return True

def main():
    def positive_integer(value):
        try:
            parsed = int(value)
        except (TypeError, ValueError):
            raise argparse.ArgumentTypeError('写作并发上限须为正整数') from None
        if parsed < 1:
            raise argparse.ArgumentTypeError('写作并发上限须为正整数')
        return parsed

    parser=argparse.ArgumentParser()
    parser.add_argument('--once',action='store_true')
    parser.add_argument('--writing-concurrency', type=positive_integer,
                        default=os.environ.get('WENSHU_WRITING_CONCURRENCY', str(DEFAULT_WRITING_CONCURRENCY)))
    args=parser.parse_args()
    store=Store()
    from .enrichment import EnrichmentService
    enrichment=EnrichmentService(store)
    lock=open(store.root/'worker.lock','a+b')
    try:
        lock.seek(0);lock.write(b'0');lock.flush();lock.seek(0)
        if os.name=='nt':
            import msvcrt
            msvcrt.locking(lock.fileno(),msvcrt.LK_NBLCK,1)
        else:
            import fcntl
            fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
    except OSError:
        raise SystemExit('文枢 Worker 已在运行')
    store.recover()
    print('Wenshu worker ready',flush=True)
    try:
        if args.once:
            run_one(store,enrichment)
        else:
            runner = ConcurrentWorker(store, enrichment, writing_limit=args.writing_concurrency)
            try:
                while True:
                    if not runner.tick():time.sleep(.5)
            finally:
                runner.close()
    finally:lock.close()

if __name__=='__main__':main()
