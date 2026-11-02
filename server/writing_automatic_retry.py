"""Frozen, durable retries for one automatic model request, never user edits."""
from __future__ import annotations

import json
import threading

import httpx

from .store import dumps, now
from .model_gateway import model_request_guard
from .writing_operations import _clean

POLICY = 'automatic-model-interruption-retry-v1'
SCHEMA = """
CREATE TABLE IF NOT EXISTS writing_automatic_retries(
 owner_run_id TEXT NOT NULL REFERENCES writing_runs(id) ON DELETE CASCADE,
 logical_key TEXT NOT NULL, attempt INTEGER NOT NULL, status TEXT NOT NULL,
 category TEXT, error TEXT, result_json TEXT, details_json TEXT NOT NULL,
 started_at TEXT NOT NULL, finished_at TEXT,
 PRIMARY KEY(owner_run_id,logical_key,attempt)
);
"""
_ACTIVE = set()
_LOCK = threading.Lock()


def retry_policy():
    return {'version': POLICY, 'maxRetries': 2, 'maxAttempts': 3,
            'categories': ['format_error', 'interrupted', 'network'],
            # Persist the structural-error addition in new parent requests only;
            # an older frozen list retains its original retry behavior.
            'formatErrors': ['模型输出不是完整有效的 JSON，未发布', '模型 JSON 必须为对象', '模型响应不是文本',
                '模型未返回可用正文，请重新生成'],
            'interruptedErrors': ['生成内容异常中断，未返回完整结果，请重新生成',
                '模型输出达到长度上限，未保存不完整结果；请提高输出预算或缩小分块',
                '模型输出异常中断，未保存不完整结果；请重新生成或调整输出预算'],
            'networkErrors': ['模型请求超时，请重试或调整超时设置',
                '无法连接模型服务，请检查服务地址、网络和本地服务状态',
                *[f'模型服务返回 HTTP {code}，请核对配置后重试' for code in (408, 500, 502, 503, 504)]],
            'reasonLabels': {'format_error': '返回格式错误', 'interrupted': '生成结果异常中断',
                             'network': '模型连接中断或请求超时'}}


def enabled(frozen):
    policy = frozen.get('automaticRetryPolicy') if isinstance(frozen, dict) else None
    return isinstance(policy, dict) and policy.get('version') == POLICY


def classify_failure(exc, policy):
    if not isinstance(policy, dict) or policy.get('version') != POLICY:
        return None
    explicit = getattr(exc, 'automaticRetryCategory', None)
    if explicit in policy['categories']:
        return explicit
    chain, seen = [], set()
    current = exc
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        chain.append(current)
        current = current.__cause__ or current.__context__
    if any(isinstance(item, AutomaticRetryCancelled) or type(item).__name__ == 'WritingConflict' for item in chain):
        return None
    # The wrapper's interruption text can conceal the original Extra data /
    # malformed JSON. Preserve that more precise business reason.
    if any(isinstance(item, json.JSONDecodeError) or str(item) in policy['formatErrors'] for item in chain):
        return 'format_error'
    if any(isinstance(item, (httpx.TimeoutException, httpx.NetworkError, httpx.RemoteProtocolError, ConnectionError, TimeoutError))
           or str(item) in policy['networkErrors'] for item in chain):
        return 'network'
    if any(str(item) in policy['interruptedErrors'] for item in chain):
        return 'interrupted'
    return None


class AutomaticRetryExhausted(ValueError):
    def __init__(self, category, count, reason):
        super().__init__(reason)
        self.automaticRetryManaged = True
        self.automaticRetryCategory = category
        self.automaticRetryCount = count


class AutomaticRetryCancelled(ValueError):
    pass


def _guard(store, owner, guard):
    with store.connect() as db:
        row = db.execute('SELECT j.status FROM writing_runs r JOIN jobs j ON j.id=r.job_id WHERE r.id=?', (owner,)).fetchone()
    if not row or row['status'] != 'running':
        raise AutomaticRetryCancelled('全自动写作已停止，未继续模型请求')
    if guard is not None:
        guard()


def retry_call(store, frozen, logical_key, operation, *, guard=None, secret='', details=None, result_guard=None):
    """Run at most three requests; persist results before the caller applies them.

    ``operation`` includes response parsing/shape validation and returns JSON.
    A recovered durable result is returned without issuing another request.
    Business rejections and cancelled/version-fenced work are never retried.
    """
    if not enabled(frozen):
        return operation()
    policy = frozen['automaticRetryPolicy']
    owner = frozen.get('automaticRetryOwnerRunId')
    if not isinstance(owner, str) or not owner or not isinstance(logical_key, str) or not 0 < len(logical_key) <= 512:
        raise ValueError('自动重试缺少有效的固定请求标识')
    identity = (str(store.root), owner, logical_key)
    with _LOCK:
        if identity in _ACTIVE:
            raise ValueError('本次模型请求仍在执行，请等待完成')
        _ACTIVE.add(identity)
    metadata = {key: value for key, value in (details or {}).items() if key in ('stage', 'sectionId', 'sectionTitle', 'label', 'runId')}
    metadata = _clean(metadata, secret)
    try:
        with store.connect() as db:
            db.executescript(SCHEMA)
        while True:
            _guard(store, owner, None)
            with store.connect() as db:
                cached_row = db.execute('SELECT status FROM writing_automatic_retries WHERE owner_run_id=? AND logical_key=? ORDER BY attempt DESC LIMIT 1',
                                        (owner, logical_key)).fetchone()
            if not cached_row or cached_row['status'] != 'succeeded':
                _guard(store, owner, guard)
            cached = None
            exhausted = None
            with store.connect() as db:
                db.execute('BEGIN IMMEDIATE')
                rows = db.execute('SELECT * FROM writing_automatic_retries WHERE owner_run_id=? AND logical_key=? ORDER BY attempt',
                                  (owner, logical_key)).fetchall()
                latest = rows[-1] if rows else None
                if latest and latest['status'] == 'succeeded':
                    cached = latest['result_json']
                if cached is not None:
                    pass
                elif latest and latest['status'] == 'managed':
                    category = latest['category']
                    count = json.loads(latest['details_json']).get('managedRetryCount', 2)
                    exhausted = AutomaticRetryExhausted(category, count, policy['reasonLabels'].get(category, '本次请求已完成异常重试'))
                elif latest and latest['status'] == 'started':
                    # Only recovery can reach an abandoned in-flight record;
                    # the process-local reservation excludes a live duplicate.
                    db.execute("UPDATE writing_automatic_retries SET status='failed',category='interrupted',error=?,finished_at=? WHERE owner_run_id=? AND logical_key=? AND attempt=?",
                               ('上次模型请求中断，已恢复处理', now(), owner, logical_key, latest['attempt']))
                    category = 'interrupted'
                elif cached is None:
                    category = latest['category'] if latest else None
                if cached is None and exhausted is None and latest and (category is None or len(rows) >= policy['maxAttempts']):
                    reason = policy['reasonLabels'].get(category, '上次请求未满足业务条件')
                    exhausted = AutomaticRetryExhausted(category, max(0, len(rows) - 1), reason)
                if cached is None and exhausted is None:
                    ordinal = len(rows) + 1
                    db.execute('INSERT INTO writing_automatic_retries VALUES(?,?,?,?,?,?,?,?,?,?)',
                               (owner, logical_key, ordinal, 'started', None, None, None, dumps(metadata), now(), None))
            if exhausted is not None:
                raise exhausted
            if cached is not None:
                _guard(store, owner, result_guard if result_guard is not None else guard)
                return json.loads(cached)
            try:
                _guard(store, owner, guard)
                with model_request_guard(lambda: _guard(store, owner, guard)):
                    value = operation()
                _guard(store, owner, result_guard if result_guard is not None else guard)
                safe = _clean(value, secret)
                encoded = dumps(safe)
            except Exception as exc:
                category = classify_failure(exc, policy)
                managed = getattr(exc, 'automaticRetryManaged', False)
                failure_metadata = ({**metadata, 'managedRetryCount': getattr(exc, 'automaticRetryCount', 2)} if managed else metadata)
                with store.connect() as db:
                    db.execute("UPDATE writing_automatic_retries SET status=?,category=?,error=?,details_json=?,finished_at=? WHERE owner_run_id=? AND logical_key=? AND attempt=?",
                               ('managed' if managed else 'failed', category, _clean(str(exc), secret)[:1000], dumps(failure_metadata), now(), owner, logical_key, ordinal))
                if managed:
                    raise
                if category is None or ordinal >= policy['maxAttempts']:
                    if category is not None:
                        exc.automaticRetryManaged = True
                        exc.automaticRetryCategory = category
                        exc.automaticRetryCount = ordinal - 1
                    raise
                continue
            with store.connect() as db:
                db.execute("UPDATE writing_automatic_retries SET status='succeeded',result_json=?,finished_at=? WHERE owner_run_id=? AND logical_key=? AND attempt=?",
                           (encoded, now(), owner, logical_key, ordinal))
            return safe
    finally:
        with _LOCK:
            _ACTIVE.discard(identity)


def retry_events(db, owner, *, running=False):
    if not db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='writing_automatic_retries'").fetchone():
        return []
    groups = {}
    for row in db.execute('SELECT * FROM writing_automatic_retries WHERE owner_run_id=? ORDER BY logical_key,attempt', (owner,)):
        groups.setdefault(row['logical_key'], []).append(row)
    parent = db.execute('SELECT input_json FROM writing_runs WHERE id=?', (owner,)).fetchone()
    policy = json.loads(parent['input_json']).get('automaticRetryPolicy', {}) if parent else {}
    labels = policy.get('reasonLabels', retry_policy()['reasonLabels'])
    events = []
    for rows in groups.values():
        latest = rows[-1]
        if latest['status'] == 'managed':
            continue
        if len(rows) <= 1:
            continue
        if latest['status'] == 'started' and not running:
            continue
        failures = [row for row in rows if row['category'] in labels]
        if not failures:
            continue
        category = failures[-1]['category']
        metadata = json.loads(latest['details_json'])
        status = 'retrying' if latest['status'] == 'started' else 'recovered' if latest['status'] == 'succeeded' else 'exhausted'
        event = {key: metadata[key] for key in ('stage', 'sectionId', 'sectionTitle') if metadata.get(key)}
        event.update(retryCount=len(rows) - 1, maxRetries=policy.get('maxRetries', 2), status=status, category=category, reason=labels[category])
        events.append(event)
    return events
