"""Three synthetic sources, real HTTP/UI flows, and zero external providers.

Run from the project checkout after building frontend/dist/client. This server
is restricted to one marked output/qa directory and deliberately never starts
the ordinary worker, idle processing, imports, or corpus scanners.
"""
import argparse
from contextlib import asynccontextmanager
import json
import os
from pathlib import Path
import re
import sys
import threading


FIXTURE = 'wiki-unified-ui-v1'
EXPECTED_ROOT = Path(__file__).resolve().parents[1] / 'output/qa/wiki-unified-ui'


def write_json(path, value):
    temporary = path.with_suffix(path.suffix + '.tmp')
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding='utf-8')
    temporary.replace(path)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--root', default=str(EXPECTED_ROOT))
    parser.add_argument('--port', type=int, default=8789)
    parser.add_argument('--host', default=os.environ.get('WENSHU_QA_HOST', '127.0.0.1'))
    args = parser.parse_args()
    project = Path(__file__).resolve().parents[1]
    root = Path(args.root).resolve()
    if root != EXPECTED_ROOT:
        raise ValueError('Use the dedicated project output/qa/wiki-unified-ui fixture directory')
    if not 1024 <= args.port <= 65535:
        raise ValueError('QA port must be between 1024 and 65535')
    marker_file = root / '.wiki-unified-qa.json'
    manifest_file = root / 'fixture-manifest.json'
    if marker_file.is_symlink() or manifest_file.is_symlink():
        raise ValueError('QA marker and manifest must be local regular files')
    if root.exists() and any(root.iterdir()) and not marker_file.is_file():
        raise ValueError('Refusing an existing unmarked directory')
    root.mkdir(parents=True, exist_ok=True)
    if marker_file.exists():
        marker = json.loads(marker_file.read_text(encoding='utf-8'))
        if marker != {'fixture': FIXTURE, 'root': str(EXPECTED_ROOT)}:
            raise ValueError('Unexpected QA marker; no database was opened')
        if not manifest_file.exists():
            raise ValueError('Incomplete marked fixture; inspect the dedicated QA directory before reseeding')
    else:
        write_json(marker_file, {'fixture': FIXTURE, 'root': str(EXPECTED_ROOT)})
    # A marked root must never redirect its database through a symlink.
    data_root = root / 'data'
    if data_root.is_symlink() or data_root.resolve() != root / 'data':
        raise ValueError('QA data directory must not be a symbolic link')
    if data_root.exists() and any(path.is_symlink() for path in data_root.rglob('*')):
        raise ValueError('QA database and stored fixtures must not contain symbolic links')
    os.environ['WENSHU_DATA_DIR'] = str(data_root)
    os.environ['WENSHU_ALLOWED_HOSTS'] = os.environ.get('WENSHU_QA_ALLOWED_HOSTS', 'localhost,127.0.0.1')
    os.environ['WENSHU_ALLOWED_ORIGINS'] = os.environ.get('WENSHU_QA_ALLOWED_ORIGINS', f'http://localhost:{args.port},http://127.0.0.1:{args.port}')
    sys.path.insert(0, str(project))

    import httpx
    import uvicorn
    from fastapi.responses import JSONResponse
    from fastapi.staticfiles import StaticFiles
    from server.app import create_app
    from server.model_gateway import ModelService
    from server.store import Store, now
    from server.tests.test_wiki_exploration import ExplorationBench, entry, relation

    if not manifest_file.exists():
        bench = ExplorationBench(data_root)
        with bench.store.connect() as db:
            if db.execute('SELECT count(*) FROM sources').fetchone()[0]:
                raise ValueError('Refusing to seed a nonempty fixture database')

        llm = entry('llm', '大语言模型', summary='大语言模型也称LLM，用于生成文本，本来源只讨论语言建模能力。')
        llm['aliases'] = ['LLM']
        zhang_engineer = entry('zhang', '张伟', 'entity', summary='张伟是虚构的语言模型工程师，在示例甲中负责模型评估，与同名医生不是同一个人。')
        a, pa = bench.build([llm, zhang_engineer], title='隔离样例甲：语言模型与工程师')

        llm_alias = entry('llm', 'LLM', summary='LLM即大语言模型，本来源补充其自然语言任务及使用条件，不把计划写成既成事实。')
        llm_alias['aliases'] = ['大语言模型']
        zhang_doctor = entry('zhang', '张伟', 'entity', summary='张伟是虚构的临床医生，在示例乙中负责病例评审，他与示例甲中的同名工程师身份不同。')
        agent = entry('agent', 'AI智能体', summary='AI智能体是使用语言模型并调用工具完成任务的系统，与描述其执行步骤的智能体执行机制不同。')
        link = relation('llm', 'agent', '用于智能体语言推理', quote='本示例明确记载，LLM用于AI智能体的语言推理，工具执行仍需单独控制。')
        b, pb = bench.build([llm_alias, zhang_doctor, agent], [link], title='隔离样例乙：应用条件与同名医生')

        mechanism = entry('mechanism', '智能体执行机制', summary='智能体执行机制描述AI智能体调用工具、记录检查点和继续任务的步骤，与AI智能体这一系统概念相关但含义不同。')
        c, pc = bench.build([mechanism], title='隔离样例丙：智能体执行机制')
        for source_id, second, third in [(a, '语言模型', '大语言模型'),
                                          (b, '语言模型', '大语言模型'),
                                          (c, 'AI智能体', '智能体执行机制')]:
            bench.store.patch(source_id, {'classificationPaths': [{
                'level1Id': 'artificial_intelligence', 'level2': second, 'level3': third}]})
        entries = {'llmChinese': pa['大语言模型']['id'], 'llmAlias': pb['LLM']['id'],
                   'zhangEngineer': pa['张伟']['id'], 'zhangDoctor': pb['张伟']['id'],
                   'agent': pb['AI智能体']['id'], 'mechanism': pc['智能体执行机制']['id']}
        manifest = {'fixture': FIXTURE, 'syntheticSources': 3, 'syntheticPages': 6,
                    'sourceIds': [a, b, c], 'entryIds': entries,
                    'sources': [{'id': a, 'title': '隔离样例甲：语言模型与工程师'},
                                {'id': b, 'title': '隔离样例乙：应用条件与同名医生'},
                                {'id': c, 'title': '隔离样例丙：智能体执行机制'}],
                    'sameAsEntryIds': [entries['llmChinese'], entries['llmAlias']],
                    'distinctEntryIds': [entries['zhangEngineer'], entries['zhangDoctor']],
                    'relatedEntryIds': [entries['agent'], entries['mechanism']],
                    'providerMockCalls': 0, 'providerMockResolveCalls': 0,
                    'providerMockSynthesisCalls': 0, 'realProviderCalls': 0,
                    'workerErrors': 0}
        write_json(manifest_file, manifest)
    else:
        manifest = json.loads(manifest_file.read_text(encoding='utf-8'))
        if manifest.get('fixture') != FIXTURE or manifest.get('syntheticSources') != 3:
            raise ValueError('Unexpected fixture manifest; no seeding was performed')

    store = Store(data_root)
    with store.connect() as db:
        source_ids = {row['id'] for row in db.execute('SELECT id FROM sources')}
        entry_ids = {row['entry_id'] for row in db.execute('SELECT p.entry_id FROM wiki_pages p JOIN wiki_publications u ON u.run_id=p.run_id')}
        if source_ids != set(manifest['sourceIds']) or entry_ids != set(manifest['entryIds'].values()):
            raise ValueError('Fixture database differs from its three synthetic sources')
        # Recover only this server's own two durable job kinds after an abrupt
        # stop. All other tasks, including publication outbox events, stay idle.
        db.execute("UPDATE jobs SET status='queued',stage='queued',error='隔离验收服务重启，恢复同一冻结任务',updated_at=? WHERE status='running' AND kind IN ('wiki_resolve','wiki_synthesize')", (now(),))

    manifest_lock = threading.RLock()

    def provider(request):
        if (request.method != 'POST' or request.url.host != 'wiki-unified-fixture.invalid'
                or request.url.path != '/v1/chat/completions'):
            raise RuntimeError('QA MockTransport rejected an unexpected endpoint')
        envelope = json.loads(request.content)
        payload = json.loads(envelope['messages'][1]['content'])
        task = payload.get('task')
        if task == 'wiki_resolve':
            left = payload['entry']['label']
            judgments = []
            for candidate in payload['candidates']:
                right = candidate['label']
                if left in ('LLM', '大语言模型') and right in ('LLM', '大语言模型'):
                    judgment, reason = 'same_as', '两个来源明确使用互为别名的名称，指向同一语言模型概念，需人工确认。'
                elif left == right == '张伟':
                    judgment, reason = 'distinct', '来源甲是工程师，来源乙是临床医生，资料明确记载二者身份不同。'
                else:
                    judgment, reason = 'related', '样例存在语言模型或智能体主题关联，系统对象与执行机制仍需分别保留。'
                judgments.append({'candidateId': candidate['id'], 'judgment': judgment, 'reason': reason})
            result = {'judgments': judgments}
        elif task == 'wiki_synthesize':
            # One claim per original source, each retaining original attribution
            # and an actual registry ID supplied by this task, never a Wiki ID.
            claims = []
            seen_sources = set()
            for reference in payload['evidence']:
                source_id = reference['sourceId']
                if source_id in seen_sources:
                    continue
                seen_sources.add(source_id)
                sentence = re.split(r'[。！？!?]', reference['text'], maxsplit=1)[0].strip()
                attribution = reference.get('sourceTitle') or '该来源'
                claims.append({'text': attribution + '记载：' + sentence + '。',
                               'evidence': [{'evidenceId': reference['evidenceId']}]})
            result = {'sections': [{'title': '定义与来源补充', 'claims': claims[:8]}], 'issues': []}
        else:
            raise RuntimeError('QA provider supports only explicit resolve and synthesis')
        with manifest_lock:
            manifest['providerMockCalls'] += 1
            key = 'providerMockResolveCalls' if task == 'wiki_resolve' else 'providerMockSynthesisCalls'
            manifest[key] += 1
            write_json(manifest_file, manifest)
        return httpx.Response(200, json={
            'id': 'wiki-unified-qa', 'model': 'wiki-unified-mock',
            'choices': [{'message': {'role': 'assistant', 'content': json.dumps(result, ensure_ascii=False)},
                         'finish_reason': 'stop'}], 'usage': {'total_tokens': 1}})

    models = ModelService(store, transport=httpx.MockTransport(provider))
    model_id = manifest.get('modelId')
    if model_id:
        config = models.resolve(model_id)
        if config['baseUrl'] != 'http://wiki-unified-fixture.invalid/v1' or config.get('apiKey'):
            raise ValueError('Fixture model must remain keyless and confined to MockTransport')
    else:
        existing = models.models()
        if existing:
            if len(existing) != 1 or existing[0]['model'] != 'wiki-unified-mock':
                raise ValueError('Unexpected model configuration in fixture database')
            model_id = existing[0]['id']
        else:
            model_id = models.save_config({'name': '统一图谱隔离模拟',
                'baseUrl': 'http://wiki-unified-fixture.invalid/v1', 'model': 'wiki-unified-mock',
                'contextChars': 64000, 'maxOutputTokens': 8192, 'timeoutSeconds': 10,
                'jsonMode': True, 'apiKey': ''})['id']
        with manifest_lock:
            manifest['modelId'] = model_id
            write_json(manifest_file, manifest)
    models.save_preferences({'defaultModelId': model_id, 'autoProcess': False,
                             'autoTranslate': False, 'idleProcess': False})
    app = create_app(data_dir=data_root, model_service=models)
    stop = threading.Event()

    def claim_resolution():
        with store.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute("SELECT * FROM jobs WHERE status='queued' AND kind IN ('wiki_resolve','wiki_synthesize') ORDER BY created_at,id LIMIT 1").fetchone()
            if row is None:
                return None
            db.execute("UPDATE jobs SET status='running',stage='preparing',error=NULL,attempts=attempts+1,updated_at=? WHERE id=?", (now(), row['id']))
            return dict(row)

    def work():
        while not stop.is_set():
            job = claim_resolution()
            if job is None:
                stop.wait(.15)
                continue
            try:
                app.state.wiki_resolution.process(job)
            except Exception:
                # Deliberately omit exception/provider bodies from diagnostics.
                with store.connect() as db:
                    db.execute("UPDATE jobs SET status='failed',stage='failed',error='隔离模拟任务异常',updated_at=? WHERE id=? AND status='running' AND attempts=?", (now(), job['id'], job['attempts'] + 1))
                with manifest_lock:
                    manifest['workerErrors'] += 1
                    write_json(manifest_file, manifest)

    thread = threading.Thread(target=work, name='wiki-unified-qa-only', daemon=False)

    @asynccontextmanager
    async def lifespan(application):
        thread.start()
        try:
            yield
        finally:
            stop.set()
            thread.join(timeout=15)
            if thread.is_alive():
                raise RuntimeError('QA mock worker did not stop normally')

    app.router.lifespan_context = lifespan

    @app.middleware('http')
    async def restrict_fixture_writes(request, call_next):
        path = request.url.path
        if request.method == 'PATCH' and path.startswith('/api/documents/'):
            body = await request.json()
            if (path.rsplit('/', 1)[-1] in manifest['sourceIds'] and isinstance(body, dict)
                    and set(body) == {'read'} and isinstance(body['read'], bool)):
                return await call_next(request)
        allowed = {'/api/wiki/resolve', '/api/wiki/knowledge/merge', '/api/wiki/knowledge/reject',
                   '/api/wiki/knowledge/undo', '/api/wiki/knowledge/synthesize'}
        if request.method == 'POST' and path in allowed:
            return await call_next(request)
        if request.method == 'POST' and re.fullmatch(r'/api/jobs/[^/]+/(?:cancel|retry)', path):
            job_id = path.split('/')[3]
            try:
                job = store.job(job_id)
            except KeyError:
                return JSONResponse({'detail': '未找到隔离验收任务'}, status_code=404)
            if job['kind'] in ('wiki_resolve', 'wiki_synthesize'):
                return await call_next(request)
        if request.method not in ('GET', 'HEAD', 'OPTIONS'):
            return JSONResponse({'detail': '隔离验收仅允许图谱辨析、人工归并、相关、撤销及本任务取消重试'}, status_code=403)
        return await call_next(request)

    @app.get('/qa/manifest')
    def qa_manifest():
        with manifest_lock:
            public = json.loads(json.dumps(manifest))
        with store.connect() as db:
            public['resolutionJobCounts'] = {row['status']: row['n'] for row in db.execute(
                "SELECT status,count(*) n FROM jobs WHERE kind IN ('wiki_resolve','wiki_synthesize') GROUP BY status")}
        public.update(dataDirectory=str(data_root), workerAlive=thread.is_alive(), realProviderCalls=0)
        return public

    frontend = project / 'frontend' / 'dist' / 'client'
    if not (frontend / 'index.html').is_file():
        raise ValueError('Build frontend/dist/client before starting the QA service')
    app.mount('/', StaticFiles(directory=frontend, html=True), name='unified-qa-ui')
    print(json.dumps({'fixtureReady': str(manifest_file), 'port': args.port,
                      'syntheticSources': 3, 'syntheticPages': 6, 'realProviderCalls': 0}), flush=True)
    uvicorn.run(app, host=args.host, port=args.port, log_level='warning')


if __name__ == '__main__':
    main()
