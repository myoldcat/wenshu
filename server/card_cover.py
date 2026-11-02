"""Conservative, version-bound image selection for article cards.

Only saved raster assets enter the vision request. A program-owned shortlist
limits cost; the model may decline every image. No geometric or stock-image
fallback is published as a semantic selection.
"""
from __future__ import annotations

import base64
from io import BytesIO
import hashlib
import json
import math
import warnings

from .store import dumps, ident, now
from .model_gateway import default_model_id
from .prompts import snapshot, intent_signature, freeze, job_prompts, prompt_text

PROMPT_VERSION = 'wenshu-card-cover-1.0'
MIN_CONFIDENCE = .8
MAX_SOURCE_PIXELS = 24_000_000
SCHEMA = """
CREATE TABLE IF NOT EXISTS ai_card_covers(
 seq INTEGER PRIMARY KEY AUTOINCREMENT, id TEXT NOT NULL UNIQUE,
 job_id TEXT NOT NULL UNIQUE REFERENCES jobs(id),
 source_id TEXT NOT NULL REFERENCES sources(id),
 revision_id TEXT NOT NULL REFERENCES revisions(id),
 model_id TEXT NOT NULL, model_version INTEGER NOT NULL,
 prompt_version TEXT NOT NULL, candidates_hash TEXT NOT NULL,
 candidates_json TEXT NOT NULL, fingerprint TEXT NOT NULL,
 result_json TEXT, created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS card_cover_object ON ai_card_covers(revision_id,seq);
CREATE INDEX IF NOT EXISTS card_cover_fingerprint ON ai_card_covers(fingerprint);
"""


def _hash(value):
    return hashlib.sha256(dumps(value).encode()).hexdigest()


def _default(revision_id, status='not_processed'):
    return {'status': status, 'assetId': None, 'url': None, 'width': None, 'height': None,
            'revisionId': revision_id, 'modelId': None, 'modelVersion': None,
            'promptVersion': PROMPT_VERSION, 'candidatesHash': None,
            'confidence': None, 'reason': '', 'jobId': None}


def _dto(row):
    result = _default(row['revision_id'])
    result.update(status={'succeeded': 'completed'}.get(row['status'], row['status']),
                  modelId=row['model_id'], modelVersion=row['model_version'],
                  promptVersion=row['prompt_version'], candidatesHash=row['candidates_hash'],
                  jobId=row['job_id'])
    if row['status'] == 'succeeded' and row['result_json']:
        selected = json.loads(row['result_json'])
        result.update(confidence=selected['confidence'], reason=selected['reason'])
        candidate = next((item for item in json.loads(row['candidates_json'])
                          if item['assetId'] == selected.get('assetId')), None)
        if candidate and selected['confidence'] >= MIN_CONFIDENCE:
            result.update(assetId=candidate['assetId'], url='/api/assets/' + candidate['assetId'],
                          width=candidate['width'], height=candidate['height'])
    if row['status'] == 'failed':
        result['error'] = row['error'] or '图片选择失败，请检查模型视觉能力后手动重试'
    return result


def cover_dtos(store, revision_ids, *, db):
    """Pure batched list/search projection; never create tables or call models."""
    revisions = list(dict.fromkeys(revision_ids))
    result = {revision: {'cardCover': _default(revision), 'coverImage': None} for revision in revisions}
    if not revisions:
        return result
    # Changing an assigned model/configuration must not display the previous
    # model's decision while the new intent has yet to be scheduled.
    tables = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table' AND name IN ('ai_card_covers','ai_settings','ai_model_configs')")}
    preference = db.execute("SELECT value_json FROM ai_settings WHERE key='preferences'").fetchone() if 'ai_settings' in tables else None
    model_id = default_model_id(json.loads(preference['value_json']) if preference else {})
    config = db.execute('SELECT max(version) version FROM ai_model_configs WHERE id=?', (model_id,)).fetchone() if model_id and 'ai_model_configs' in tables else None
    placeholders = ','.join('?' for _ in revisions)
    current_candidates = {row['revision_id']: json.loads(row['candidates_json']) for row in db.execute(
        f'SELECT revision_id,candidates_json FROM revision_image_candidates WHERE revision_id IN ({placeholders})', revisions)}
    for revision in revisions:
        candidates = current_candidates.get(revision, [])
        result[revision]['cardCover'].update(modelId=model_id, modelVersion=config['version'] if config else None,
                                            candidatesHash=_hash(candidates), status='not_processed' if candidates else 'no_candidates')
    if 'ai_card_covers' not in tables:
        return result
    rows = db.execute(
        'SELECT c.*,j.status,j.error FROM ai_card_covers c JOIN jobs j ON j.id=c.job_id '
        f'WHERE c.seq IN (SELECT max(seq) FROM ai_card_covers WHERE revision_id IN ({placeholders}) GROUP BY revision_id)',
        revisions,
    ).fetchall()
    for row in rows:
        if (not config or row['model_id'] != model_id or row['model_version'] != config['version'] or row['prompt_version'] != PROMPT_VERSION
                or row['candidates_hash'] != _hash(current_candidates.get(row['revision_id'], []))):
            continue
        value = _dto(row)
        result[row['revision_id']] = {'cardCover': value, 'coverImage': value['url']}
    return result


def validate_selection(value, candidates):
    if not isinstance(value, dict) or set(value) != {'assetId', 'confidence', 'reason'}:
        raise ValueError('图片选择结果格式不正确，未展示配图')
    asset_id, confidence, reason = value['assetId'], value['confidence'], value['reason']
    if asset_id is not None and (not isinstance(asset_id, str) or asset_id not in {item['assetId'] for item in candidates}):
        raise ValueError('模型选择了候选集外的图片，未展示配图')
    if isinstance(confidence, bool) or not isinstance(confidence, (float, int)) or not math.isfinite(confidence) or not 0 <= confidence <= 1:
        raise ValueError('图片选择置信度无效，未展示配图')
    if not isinstance(reason, str) or not reason.strip() or len(reason) > 240:
        raise ValueError('图片选择理由无效，未展示配图')
    return {'assetId': asset_id if confidence >= MIN_CONFIDENCE else None,
            'confidence': float(confidence), 'reason': reason.strip()}


def thumbnail(store, candidate):
    """Decode, bound and re-encode without EXIF/profile or external resources."""
    from PIL import Image, ImageOps
    path, mime = store.asset(candidate['assetId'])
    path = path.resolve()
    if not path.is_relative_to(store.root / 'assets') or not path.is_file():
        raise ValueError('图片不属于已保存的本地资源，未发送模型')
    if mime not in ('image/png', 'image/jpeg', 'image/gif', 'image/webp') or path.stat().st_size > 16 * 1024 * 1024:
        raise ValueError('已保存图片过大或格式不受支持，未发送模型')
    with warnings.catch_warnings():
        warnings.simplefilter('error', Image.DecompressionBombWarning)
        with Image.open(path) as source:
            if source.width * source.height > MAX_SOURCE_PIXELS:
                raise ValueError('已保存图片像素过大，未发送模型')
            source.seek(0)
            image = ImageOps.exif_transpose(source)
            image.thumbnail((1024, 1024), Image.Resampling.LANCZOS)
            if image.mode in ('RGBA', 'LA') or 'transparency' in image.info:
                rgba = image.convert('RGBA')
                clean = Image.new('RGB', rgba.size, 'white')
                clean.paste(rgba, mask=rgba.getchannel('A'))
            else:
                clean = image.convert('RGB')
            output = BytesIO()
            clean.save(output, format='JPEG', quality=82, optimize=True)
    data = output.getvalue()
    if len(data) > 512 * 1024:
        raise ValueError('图片缩略图仍然过大，未发送模型')
    return 'data:image/jpeg;base64,' + base64.b64encode(data).decode('ascii')


class CardCoverService:
    def __init__(self, store, model_service=None):
        self.store = store
        if model_service is None:
            from .model_gateway import ModelService
            model_service = ModelService(store)
        self.models = model_service
        with store.connect() as db:
            db.executescript(SCHEMA)

    def detail(self, source_id, revision_id=None, model_id=None):
        document = self.store.detail(source_id, revision_id)
        revision_id = document['revisionId']
        selected = model_id or default_model_id(self.models.get_preferences())
        config = self.models.resolve(selected) if selected else None
        candidates = self.store.image_candidates(revision_id)[:6]
        expected_hash = _hash(candidates)
        with self.store.connect() as db:
            row = db.execute('SELECT c.*,j.status,j.error FROM ai_card_covers c JOIN jobs j ON j.id=c.job_id '
                             'WHERE c.source_id=? AND c.revision_id=? ORDER BY c.seq DESC LIMIT 1', (source_id, revision_id)).fetchone()
        if (not row or not config or row['model_id'] != config['id'] or row['model_version'] != config['version']
                or row['prompt_version'] != PROMPT_VERSION or row['candidates_hash'] != expected_hash):
            value = _default(revision_id, 'not_processed' if candidates else 'no_candidates')
            value.update(modelId=selected, modelVersion=config['version'] if config else None, candidatesHash=expected_hash)
            return {'cardCover': value, 'job': None}
        return {'cardCover': _dto(row), 'job': self.store.job(row['job_id'])}

    def queue(self, source_id, *, revision_id=None, model_id=None, force=False, schedule_job=None):
        document = self.store.detail(source_id)
        if revision_id is not None and revision_id != document['revisionId']:
            raise ValueError('阅读中的来源版本已过期，请刷新后重新选择图片')
        selected = model_id or default_model_id(self.models.get_preferences())
        if not selected:
            raise ValueError('请先在系统设置选择支持图片识别的默认模型')
        config = self.models.resolve(selected)
        candidates = self.store.image_candidates(document['revisionId'])[:6]
        if not candidates:
            return {'cardCover': _default(document['revisionId'], 'no_candidates'), 'job': None, 'enqueued': False}
        candidates_hash = _hash(candidates)
        with self.store.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            prompts = snapshot(db, ('card_cover',))
            fingerprint = _hash(intent_signature([source_id, document['revisionId'], config['id'], config['version'], PROMPT_VERSION, candidates_hash], prompts))
            current = db.execute('SELECT current_revision FROM sources WHERE id=?', (source_id,)).fetchone()
            if not current or current['current_revision'] != document['revisionId']:
                raise ValueError('来源版本刚发生变化，请重新选择图片')
            if schedule_job is not None:
                parent = db.execute('SELECT status,attempts FROM jobs WHERE id=?', (schedule_job['id'],)).fetchone()
                if not parent or parent['status'] != 'running' or parent['attempts'] != schedule_job['attempts'] + 1:
                    raise ValueError('自动加工任务已取消，未选择图片')
            existing = None if force else db.execute('SELECT c.*,j.status,j.error FROM ai_card_covers c JOIN jobs j ON j.id=c.job_id '
                                                     'WHERE fingerprint=? ORDER BY c.seq DESC LIMIT 1', (fingerprint,)).fetchone()
            if existing:
                return {'cardCover': _dto(existing), 'job': self.store.job_dto(db.execute('SELECT * FROM jobs WHERE id=?', (existing['job_id'],)).fetchone()), 'enqueued': False}
            job_id, timestamp = ident('job'), now()
            db.execute("UPDATE jobs SET status='cancelled',stage='superseded',error='已由新的图片选择任务替代',updated_at=? "
                       "WHERE kind='cover_selection' AND source_id=? AND revision_id=? AND status IN ('queued','running')",
                       (timestamp, source_id, document['revisionId']))
            payload = {'sourceId': source_id, 'revisionId': document['revisionId'], 'modelId': config['id'],
                       'modelVersion': config['version'], 'promptVersion': PROMPT_VERSION, 'candidatesHash': candidates_hash}
            db.execute("INSERT INTO jobs(id,batch_id,request_key,label,kind,input_json,status,stage,source_id,revision_id,created_at,updated_at) "
                       "VALUES(?,?,?,?,?,?,'queued','queued',?,?,?,?)",
                       (job_id, ident('cover_batch'), ident('cover_request'), '卡片配图 · ' + document['title'], 'cover_selection', dumps(payload),
                        source_id, document['revisionId'], timestamp, timestamp))
            db.execute('INSERT INTO ai_card_covers(id,job_id,source_id,revision_id,model_id,model_version,prompt_version,candidates_hash,candidates_json,fingerprint,created_at) '
                       'VALUES(?,?,?,?,?,?,?,?,?,?,?)', (ident('cover'), job_id, source_id, document['revisionId'], config['id'], config['version'],
                                                       PROMPT_VERSION, candidates_hash, dumps(candidates), fingerprint, timestamp))
            freeze(db, job_id, prompts)
        return {**self.detail(source_id, model_id=config['id']), 'enqueued': True}

    def _alive(self, db, run, attempt):
        job = db.execute('SELECT status,attempts FROM jobs WHERE id=?', (run['job_id'],)).fetchone()
        source = db.execute('SELECT current_revision FROM sources WHERE id=?', (run['source_id'],)).fetchone()
        newest = db.execute('SELECT max(seq) FROM ai_card_covers WHERE source_id=? AND revision_id=?', (run['source_id'], run['revision_id'])).fetchone()[0]
        return bool(job and job['status'] == 'running' and job['attempts'] == attempt and source
                    and source['current_revision'] == run['revision_id'] and newest == run['seq'] and run['prompt_version'] == PROMPT_VERSION)

    def _messages(self, document, candidates, config):
        system = prompt_text('card_cover')
        # Sample the beginning of the actual article; a summary, if present, is
        # context only. Keeping ample space for IDs also fits small model budgets.
        context = {'title': document['title'][:400], 'excerpt': '',
                   'candidates': [{key: item[key] for key in ('assetId', 'width', 'height', 'ordinal')} for item in candidates]}
        reserve = len(system) + len(dumps(context)) + sum(len('assetId=' + item['assetId']) for item in candidates)
        excerpt = '\n'.join(block['text'] for block in document['blocks'])
        # dumps() escapes some source characters, so measure the actual JSON.
        budget = max(0, min(1800, config['contextChars'] - reserve - 100))
        context['excerpt'] = excerpt[:budget]
        while len(system) + len(dumps(context)) + sum(len('assetId=' + item['assetId']) for item in candidates) > config['contextChars']:
            context['excerpt'] = context['excerpt'][:-100]
            if not context['excerpt']:
                raise ValueError('模型输入预算不足以识别候选图片')
        parts = [{'type': 'text', 'text': dumps(context)}]
        for candidate in candidates:
            parts.append({'type': 'text', 'text': 'assetId=' + candidate['assetId']})
            parts.append({'type': 'image_url', 'image_url': {'url': thumbnail(self.store, candidate)}})
        return [{'role': 'system', 'content': system}, {'role': 'user', 'content': parts}]

    @job_prompts
    def process(self, job):
        from .enrichment import _json
        from .model_gateway import ModelError
        with self.store.connect() as db:
            row = db.execute('SELECT * FROM ai_card_covers WHERE job_id=?', (job['id'],)).fetchone()
            if not row:
                raise ValueError('图片选择任务缺少版本记录')
            run = dict(row)
        attempt = job['attempts'] + 1
        try:
            with self.store.connect() as db:
                if not self._alive(db, run, attempt):
                    return self._supersede(job, attempt)
                db.execute("UPDATE jobs SET stage='selecting',updated_at=? WHERE id=?", (now(), job['id']))
            document = self.store.detail(run['source_id'], run['revision_id'])
            candidates = json.loads(run['candidates_json'])
            if _hash(self.store.image_candidates(run['revision_id'])[:6]) != run['candidates_hash']:
                return self._supersede(job, attempt)
            config = self.models.resolve(run['model_id'], run['model_version'])
            messages = self._messages(document, candidates, config)
            # Recheck after decoding but before an irreversible model call.
            with self.store.connect() as db:
                if not self._alive(db, run, attempt):
                    return self._supersede(job, attempt)
            response = self.models.complete_vision(config, messages)
            result = validate_selection(_json(response['text']), candidates)
            if config.get('apiKey') and config['apiKey'] in result['reason']:
                raise ValueError('图片选择结果包含凭证信息，已阻止保存')
            with self.store.connect() as db:
                db.execute('BEGIN IMMEDIATE')
                if not self._alive(db, run, attempt):
                    return self._supersede(job, attempt, db=db)
                db.execute('UPDATE ai_card_covers SET result_json=? WHERE id=?', (dumps(result), run['id']))
                db.execute("UPDATE jobs SET status='succeeded',stage='ready',error=NULL,updated_at=? WHERE id=? AND status='running' AND attempts=?",
                           (now(), job['id'], attempt))
        except Exception as error:
            # Never echo arbitrary provider errors, prompt text, paths or images.
            message = str(error) if isinstance(error, ModelError) else '图片选择未完成，未展示配图；请检查图片和模型视觉能力后手动重试'
            with self.store.connect() as db:
                db.execute("UPDATE jobs SET status='failed',error=?,updated_at=? WHERE id=? AND status='running' AND attempts=?",
                           (message[:500], now(), job['id'], attempt))

    def _supersede(self, job, attempt, db=None):
        if db is None:
            with self.store.connect() as connection:
                return self._supersede(job, attempt, connection)
        db.execute("UPDATE jobs SET status='cancelled',stage='superseded',error='来源或图片选择版本已更新',updated_at=? "
                   "WHERE id=? AND status='running' AND attempts=?", (now(), job['id'], attempt))
