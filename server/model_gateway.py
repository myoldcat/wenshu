"""Versioned local model configuration and a bounded Chat Completions client.

Only public() results belong in API responses. resolve() is an internal snapshot
which decrypts its key for one request; jobs must persist its id/version only.
"""
from __future__ import annotations

import base64
import ctypes
from contextlib import contextmanager
from contextvars import ContextVar
import json
import math
import os
import re
import time
from urllib.parse import urlsplit, urlunsplit

import httpx

from .store import dumps, ident, now
from .credential_cipher import CredentialError, FernetFileCipher


class ModelError(ValueError):
    """A safe-to-display failure; provider response bodies are never included."""


class DPAPICipher:
    """Current Windows user's DPAPI protection, with no plaintext fallback."""

    @staticmethod
    def _crypt(data: bytes, decrypt: bool) -> bytes:
        if os.name != 'nt':
            raise ModelError('当前系统无法使用 Windows 凭证保护，请使用无密钥的本地模型')

        class Blob(ctypes.Structure):
            _fields_ = [('size', ctypes.c_ulong), ('data', ctypes.POINTER(ctypes.c_ubyte))]

        def blob(value):
            buffer = ctypes.create_string_buffer(value)
            return Blob(len(value), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_ubyte))), buffer

        source, source_buffer = blob(data)
        entropy, entropy_buffer = blob(b'Wenshu.ModelConfig.v1')
        output = Blob()
        crypt32 = ctypes.WinDLL('crypt32', use_last_error=True)
        kernel32 = ctypes.WinDLL('kernel32', use_last_error=True)
        kernel32.LocalFree.argtypes = [ctypes.c_void_p]
        kernel32.LocalFree.restype = ctypes.c_void_p
        function = crypt32.CryptUnprotectData if decrypt else crypt32.CryptProtectData
        function.argtypes = [ctypes.POINTER(Blob), ctypes.c_void_p, ctypes.POINTER(Blob),
                             ctypes.c_void_p, ctypes.c_void_p, ctypes.c_ulong, ctypes.POINTER(Blob)]
        function.restype = ctypes.c_int
        if not function(ctypes.byref(source), None, ctypes.byref(entropy), None, None, 1, ctypes.byref(output)):
            raise ModelError('Windows 凭证保护失败，请在原 Windows 用户下重新保存模型密钥')
        try:
            return ctypes.string_at(output.data, output.size)
        finally:
            kernel32.LocalFree(ctypes.cast(output.data, ctypes.c_void_p))

    def encrypt(self, text: str) -> str:
        return 'dpapi:v1:' + base64.b64encode(self._crypt(text.encode('utf-8'), False)).decode('ascii')

    def decrypt(self, text: str) -> str:
        try:
            if not text.startswith('dpapi:v1:'):
                raise ValueError()
            return self._crypt(base64.b64decode(text[9:], validate=True), True).decode('utf-8')
        except Exception:
            raise ModelError('无法解密模型密钥，请在原 Windows 用户下重新保存密钥') from None


SCHEMA = """
CREATE TABLE IF NOT EXISTS ai_model_configs(
 id TEXT NOT NULL, version INTEGER NOT NULL, config_json TEXT NOT NULL,
 key_cipher TEXT NOT NULL DEFAULT '', created_at TEXT NOT NULL,
 PRIMARY KEY(id,version)
);
CREATE TABLE IF NOT EXISTS ai_settings(key TEXT PRIMARY KEY,value_json TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS ai_model_deleted(id TEXT PRIMARY KEY,deleted_at TEXT NOT NULL);
"""
DEFAULTS = {'contextChars': 24000, 'maxOutputTokens': 2048, 'timeoutSeconds': 90,
            'reasoningEffort': '', 'jsonMode': False}
LEGACY_MODEL_FIELDS = ('summaryModelId', 'translationModelId', 'classificationModelId')
PREFERENCES = {'defaultModelId': None, 'autoProcess': False, 'autoTranslate': True,
               'idleProcess': False, 'idleStart': '00:00', 'idleEnd': '08:00'}
MAX_RESPONSE_BYTES = 8 * 1024 * 1024
_REQUEST_GUARD = ContextVar('wenshu_model_request_guard', default=None)
_REQUEST_OBSERVER = ContextVar('wenshu_model_request_observer', default=None)


@contextmanager
def model_request_observer(observer):
    """Observe only requests made in an explicitly scoped writing operation."""
    token = _REQUEST_OBSERVER.set(observer)
    try:
        yield
    finally:
        _REQUEST_OBSERVER.reset(token)


@contextmanager
def model_request_guard(check):
    """Apply a cooperative check to every HTTP attempt, including negotiation."""
    token = _REQUEST_GUARD.set(check)
    try:
        yield
    finally:
        _REQUEST_GUARD.reset(token)


def default_model_id(preferences, kind='summary'):
    """Use one default; accept legacy injected gateways during the transition."""
    if 'defaultModelId' in preferences:
        return preferences['defaultModelId']
    return preferences.get(kind + 'ModelId')


def normalize_base_url(value):
    if not isinstance(value, str) or not value.strip() or len(value) > 2048:
        raise ModelError('请填写模型服务地址')
    value = value.strip()
    if re.search(r'[\x00-\x20\x7f\\]', value):
        raise ModelError('模型服务地址不能包含空白、控制字符或反斜杠')
    try:
        parts = urlsplit(value)
        if parts.scheme.lower() not in ('http', 'https') or not parts.hostname:
            raise ValueError()
        if parts.username is not None or parts.password is not None or parts.query or parts.fragment:
            raise ValueError()
        # Bare ?/# are also not useful endpoint prefixes and can obscure errors.
        if '?' in value or '#' in value:
            raise ValueError()
        host = parts.hostname.encode('idna').decode('ascii').lower()
        if ':' in host:
            host = '[' + host + ']'
        port = parts.port
        host += f':{port}' if port else ''
        path = parts.path.rstrip('/')
        if path.endswith('/chat/completions'):
            path = path[:-17].rstrip('/')
        return urlunsplit((parts.scheme.lower(), host, path, '', ''))
    except (ValueError, UnicodeError):
        raise ModelError('请使用不含账户、查询参数或片段的 HTTP/HTTPS 模型服务地址') from None


def bounded_int(value, name, low, high):
    if isinstance(value, bool) or not isinstance(value, int) or not low <= value <= high:
        raise ModelError(f'{name}须为 {low}–{high} 之间的整数')
    return value


class ModelService:
    def __init__(self, store, *, cipher=None, transport=None):
        self.store = store
        self.cipher = cipher if cipher is not None else (
            DPAPICipher() if os.name == 'nt' else FernetFileCipher(store.root, has_ciphertext=self._has_file_credentials))
        self.transport = transport
        with store.connect() as db:
            db.executescript(SCHEMA)
            db.execute('BEGIN IMMEDIATE')
            row = db.execute("SELECT value_json FROM ai_settings WHERE key='preferences'").fetchone()
            if row:
                prefs = self._preferences(db)
                db.execute("UPDATE ai_settings SET value_json=? WHERE key='preferences'", (dumps(prefs),))

    def _has_file_credentials(self):
        with self.store.connect() as db:
            return db.execute("SELECT 1 FROM ai_model_configs WHERE key_cipher LIKE 'fernet:v1:%' LIMIT 1").fetchone() is not None

    def _row(self, model_id, version=None, db=None):
        if db is None:
            with self.store.connect() as connection:
                return self._row(model_id, version, connection)
        if version is None:
            row = db.execute('''SELECT * FROM ai_model_configs WHERE id=?
                AND NOT EXISTS(SELECT 1 FROM ai_model_deleted WHERE id=ai_model_configs.id)
                ORDER BY version DESC LIMIT 1''', (model_id,)).fetchone()
        else:
            bounded_int(version, '模型配置版本', 1, 2**31-1)
            row = db.execute('SELECT * FROM ai_model_configs WHERE id=? AND version=?', (model_id, version)).fetchone()
        if row is None:
            raise ModelError('模型配置不存在，请重新选择模型')
        return row

    def public(self, row):
        value = json.loads(row['config_json'])
        result = dict(value, id=row['id'], version=row['version'], hasApiKey=bool(row['key_cipher']), createdAt=row['created_at'])
        with self.store.connect() as db:
            test = db.execute('SELECT value_json FROM ai_settings WHERE key=?', (f"test:{row['id']}:{row['version']}",)).fetchone()
        result['lastTest'] = json.loads(test['value_json']) if test else None
        return result

    def models(self):
        with self.store.connect() as db:
            rows = db.execute('''SELECT m.* FROM ai_model_configs m JOIN
                (SELECT id,MAX(version) version FROM ai_model_configs GROUP BY id) latest
                ON m.id=latest.id AND m.version=latest.version
                WHERE NOT EXISTS(SELECT 1 FROM ai_model_deleted WHERE id=m.id)
                ORDER BY m.created_at,m.id''').fetchall()
        return [self.public(row) for row in rows]

    def snapshot(self, model_id):
        """Freeze the public model version without opening its credential store."""
        return self.public(self._row(model_id))

    def save_config(self, data, id=None):
        if not isinstance(data, dict):
            raise ModelError('模型配置格式不正确')
        if 'clearApiKey' in data and not isinstance(data['clearApiKey'], bool):
            raise ModelError('清除密钥选项须为布尔值')
        if 'apiKey' in data and not isinstance(data['apiKey'], str):
            raise ModelError('密钥须为字符串；留空表示保留原密钥')
        api_key = data.get('apiKey', '')
        if len(api_key) > 8192 or re.search(r'[\x00-\x1f\x7f]', api_key):
            raise ModelError('密钥格式不正确')
        api_key = api_key.strip()
        if data.get('clearApiKey') and api_key:
            raise ModelError('不能同时填写并清除密钥')
        with self.store.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            old = self._row(id, db=db) if id else None
            config = json.loads(old['config_json']) if old else dict(DEFAULTS)
            for key in ('name', 'baseUrl', 'model', *DEFAULTS):
                if key in data:
                    config[key] = data[key]
            for key, title, max_len in [('name', '配置名称', 100), ('model', '模型名称', 200)]:
                value = config.get(key)
                if not isinstance(value, str) or not value.strip() or len(value) > max_len or re.search(r'[\x00-\x1f\x7f]', value):
                    raise ModelError(f'请填写有效的{title}（最多 {max_len} 字符）')
                config[key] = value.strip()
            config['baseUrl'] = normalize_base_url(config.get('baseUrl'))
            config['contextChars'] = bounded_int(config['contextChars'], '输入字符预算', 2000, 1000000)
            config['maxOutputTokens'] = bounded_int(config['maxOutputTokens'], '最大输出 Token', 128, 65536)
            config['timeoutSeconds'] = bounded_int(config['timeoutSeconds'], '超时秒数', 5, 300)
            if config.get('reasoningEffort', '') not in ('', 'none', 'minimal', 'low', 'medium', 'high', 'xhigh', 'max'):
                raise ModelError('请选择有效的思考强度，或留空使用服务默认值')
            if not isinstance(config.get('jsonMode', False), bool):
                raise ModelError('JSON 模式选项须为布尔值')
            encrypted = old['key_cipher'] if old and not data.get('clearApiKey') else ''
            if api_key:
                try:
                    encrypted = self.cipher.encrypt(api_key)
                    if not isinstance(encrypted, str) or not encrypted or encrypted == api_key:
                        raise ValueError()
                except (CredentialError, ModelError) as problem:
                    raise ModelError(f'密钥加密失败，配置未保存；{problem}') from None
                except Exception:
                    raise ModelError('密钥加密失败，配置未保存；请检查服务端密钥存储') from None
            model_id = old['id'] if old else ident('model')
            version = old['version'] + 1 if old else 1
            db.execute('INSERT INTO ai_model_configs VALUES(?,?,?,?,?)', (model_id, version, dumps(config), encrypted, now()))
            row = self._row(model_id, version, db)
        return self.public(row)

    def resolve(self, model_id=None, version=None):
        if model_id is None:
            model_id = self.get_preferences()['defaultModelId']
            if model_id is None:
                raise ModelError('请先在系统设置中选择默认模型')
        row = self._row(model_id, version)
        result = json.loads(row['config_json'])
        try:
            key = self.cipher.decrypt(row['key_cipher']) if row['key_cipher'] else ''
        except (CredentialError, ModelError) as problem:
            raise ModelError(str(problem)) from None
        except Exception:
            raise ModelError('无法解密模型密钥，请检查服务端密钥存储或重新填写 API Key 后保存') from None
        return dict(result, id=row['id'], version=row['version'], apiKey=key)

    def _preferences(self, db):
        row = db.execute("SELECT value_json FROM ai_settings WHERE key='preferences'").fetchone()
        saved = json.loads(row['value_json']) if row else {}
        # Older installations used separate capability bindings. Migrate in a
        # stable order, favouring the summary model already used by Wiki/writing.
        selected = saved.get('defaultModelId') if 'defaultModelId' in saved else next(
            (saved[key] for key in LEGACY_MODEL_FIELDS if saved.get(key)), None)
        if selected and not db.execute('''SELECT 1 FROM ai_model_configs WHERE id=?
                AND NOT EXISTS(SELECT 1 FROM ai_model_deleted WHERE id=ai_model_configs.id)''', (selected,)).fetchone():
            selected = None
        return {'defaultModelId': selected, 'autoProcess': bool(saved.get('autoProcess', False)) if selected else False,
                'autoTranslate': saved.get('autoTranslate', True) is not False,
                'idleProcess': bool(saved.get('idleProcess', False)) if selected else False,
                'idleStart': saved.get('idleStart', '00:00'), 'idleEnd': saved.get('idleEnd', '08:00')}

    @staticmethod
    def _public_preferences(prefs):
        # Compatibility aliases are projections only, never independent defaults.
        return dict(prefs, **{key: prefs['defaultModelId'] for key in LEGACY_MODEL_FIELDS})

    def get_preferences(self):
        with self.store.connect() as db:
            return self._public_preferences(self._preferences(db))

    def save_preferences(self, data):
        if not isinstance(data, dict) or set(data) - (set(PREFERENCES) | set(LEGACY_MODEL_FIELDS)):
            raise ModelError('模型偏好包含未知选项')
        changes = {key: value for key, value in data.items() if key in PREFERENCES}
        legacy = [data[key] for key in LEGACY_MODEL_FIELDS if key in data]
        if legacy:
            values = [value for value in legacy if value is not None]
            if any(value != values[0] for value in values[1:]):
                raise ModelError('所有功能统一使用一个默认模型，请重新选择')
            selected = values[0] if values else None
            if 'defaultModelId' in changes and changes['defaultModelId'] != selected:
                raise ModelError('所有功能统一使用一个默认模型，请重新选择')
            changes['defaultModelId'] = selected
        with self.store.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            prefs = self._preferences(db)
            prefs.update(changes)
            if any(not isinstance(prefs[key], bool) for key in ('autoProcess', 'autoTranslate', 'idleProcess')):
                raise ModelError('自动处理选项须为布尔值')
            from .idle_processing import validate_idle_window
            try:
                validate_idle_window(prefs)
            except ValueError as exc:
                raise ModelError(str(exc)) from None
            model_id = prefs['defaultModelId']
            if model_id is not None:
                if not isinstance(model_id, str) or not model_id:
                    raise ModelError('请选择有效的默认模型，或清空默认模型')
                self._row(model_id, db=db)
            if prefs['autoProcess'] and not model_id:
                raise ModelError('启用自动处理前请选择默认模型')
            if prefs['idleProcess'] and not model_id:
                raise ModelError('启用闲时处理前请选择默认模型')
            db.execute("INSERT OR REPLACE INTO ai_settings VALUES('preferences',?)", (dumps(prefs),))
        return self._public_preferences(prefs)

    def delete_config(self, model_id):
        with self.store.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            self._row(model_id, db=db)
            prefs = self._preferences(db)
            # Keep immutable versions for already queued/running jobs and their
            # evidence history; removal only disables new selection and editing.
            db.execute('INSERT INTO ai_model_deleted VALUES(?,?)', (model_id, now()))
            if prefs['defaultModelId'] == model_id:
                prefs.update(defaultModelId=None, autoProcess=False, idleProcess=False)
            db.execute("INSERT OR REPLACE INTO ai_settings VALUES('preferences',?)", (dumps(prefs),))
        return {'models': self.models(), 'preferences': self.get_preferences()}

    def complete(self, config_snapshot, messages):
        """No automatic retries for model failures; only one parameter negotiation.

        max_tokens is common to compatible servers. A provider explicitly rejecting
        that parameter may receive one retry with max_completion_tokens; failed
        credentials, rate limits, network errors, and other errors are never retried.
        """
        config = config_snapshot
        if not isinstance(messages, list) or not messages:
            raise ModelError('模型请求不能为空')
        if any(not isinstance(m, dict) or m.get('role') not in ('system', 'user', 'assistant') or not isinstance(m.get('content'), str) for m in messages):
            raise ModelError('当前模型网关仅接受文本消息')
        if sum(len(m['content']) for m in messages) > config['contextChars']:
            raise ModelError('输入超出配置的字符预算，请降低分块长度或调整模型配置')
        request_messages = [{'role': m['role'], 'content': m['content']} for m in messages]
        return self._complete_request(config, request_messages)

    def complete_vision(self, config_snapshot, messages):
        """A separate, bounded channel for locally generated raster thumbnails.

        Text complete() deliberately keeps its original text-only contract. This
        channel never accepts provider-fetchable URLs, arbitrary data MIME types,
        original documents or unbounded image payloads.
        """
        if not isinstance(messages, list) or not messages:
            raise ModelError('模型请求不能为空')
        cleaned, text_size, image_size, image_count = [], 0, 0, 0
        for message in messages:
            if not isinstance(message, dict) or message.get('role') not in ('system', 'user', 'assistant'):
                raise ModelError('视觉消息格式不正确')
            content = message.get('content')
            if isinstance(content, str):
                text_size += len(content)
                cleaned.append({'role': message['role'], 'content': content})
                continue
            if message['role'] != 'user' or not isinstance(content, list) or not content:
                raise ModelError('视觉内容只允许用户消息中的文本和本地缩略图')
            parts = []
            for part in content:
                if not isinstance(part, dict):
                    raise ModelError('视觉内容格式不正确')
                if part.get('type') == 'text' and isinstance(part.get('text'), str):
                    text_size += len(part['text'])
                    parts.append({'type': 'text', 'text': part['text']})
                elif part.get('type') == 'image_url' and isinstance(part.get('image_url'), dict):
                    url = part['image_url'].get('url')
                    if not isinstance(url, str) or len(url) > 750_000:
                        raise ModelError('视觉请求只接受有界的本地图片缩略图')
                    match = re.fullmatch(r'data:image/(jpeg|png|webp);base64,([A-Za-z0-9+/]+={0,2})', url)
                    if not match:
                        raise ModelError('视觉请求不允许远程图片地址或非图片数据')
                    try:
                        raw = base64.b64decode(match[2], validate=True)
                        from io import BytesIO
                        from PIL import Image
                        with Image.open(BytesIO(raw)) as image:
                            if image.format not in ('JPEG', 'PNG', 'WEBP') or image.format != {'jpeg': 'JPEG', 'png': 'PNG', 'webp': 'WEBP'}[match[1]]:
                                raise ValueError()
                            if not 0 < image.width <= 1024 or not 0 < image.height <= 1024:
                                raise ValueError()
                            image.verify()
                    except Exception:
                        raise ModelError('图片缩略图无效或超过 1024 像素限制') from None
                    if len(raw) > 512 * 1024:
                        raise ModelError('单张图片缩略图不能超过 512 KiB')
                    image_size += len(raw)
                    image_count += 1
                    parts.append({'type': 'image_url', 'image_url': {'url': url, 'detail': 'low'}})
                else:
                    raise ModelError('视觉请求包含不支持的内容类型')
            cleaned.append({'role': message['role'], 'content': parts})
        if not 1 <= image_count <= 6 or image_size > 3 * 1024 * 1024:
            raise ModelError('视觉请求限 1 至 6 张图片，合计不超过 3 MiB')
        if text_size > config_snapshot['contextChars']:
            raise ModelError('输入超出配置的字符预算，请降低分块长度或调整模型配置')
        return self._complete_request(config_snapshot, cleaned)

    def _complete_request(self, config, request_messages):
        headers = {'Accept': 'application/json', 'Content-Type': 'application/json'}
        key = config.get('apiKey', '')
        if key:
            headers['Authorization'] = 'Bearer ' + key
        url = normalize_base_url(config['baseUrl']) + '/chat/completions'
        payload = {'model': config['model'], 'messages': request_messages,
                   'max_tokens': config['maxOutputTokens'], 'stream': False}
        if config.get('reasoningEffort'):
            payload['reasoning_effort'] = config['reasoningEffort']
        if config.get('jsonMode'):
            payload['response_format'] = {'type': 'json_object'}
        started = time.monotonic()
        observer = _REQUEST_OBSERVER.get()
        call_id = None
        call_started = None
        raw = bytearray()
        body, status_code, truncated = None, None, False

        def finish(status, error=None):
            nonlocal call_id
            if observer and call_id:
                observer.finish(call_id, raw=bytes(raw) if raw else None, body=body,
                                status=status, http_status=status_code, error=error,
                                duration_ms=round((time.monotonic() - call_started) * 1000), truncated=truncated)
                call_id = None

        try:
            with httpx.Client(timeout=httpx.Timeout(config['timeoutSeconds'], connect=min(15, config['timeoutSeconds'])),
                              follow_redirects=False, trust_env=False, transport=self.transport) as client:
                for attempt in range(2):
                    guard = _REQUEST_GUARD.get()
                    if guard:
                        guard()
                    remaining = config['timeoutSeconds'] - (time.monotonic() - started)
                    if remaining <= 0:
                        raise ModelError('模型请求超时，请重试或调整超时设置')
                    raw, body, status_code, truncated = bytearray(), None, None, False
                    call_started = time.monotonic()
                    call_id = observer.start(payload, config, attempt + 1) if observer else None
                    with client.stream('POST', url, json=payload, headers=headers,
                                       timeout=httpx.Timeout(remaining, connect=min(15, remaining))) as response:
                        status_code = response.status_code
                        for chunk in response.iter_bytes():
                            if time.monotonic() - started > config['timeoutSeconds']:
                                raise ModelError('模型请求超时，请重试或调整超时设置')
                            raw.extend(chunk)
                            if len(raw) > MAX_RESPONSE_BYTES:
                                del raw[MAX_RESPONSE_BYTES:]
                                truncated = True
                                raise ModelError('模型返回内容过大，已停止接收')
                        try:
                            body = json.loads(raw)
                        except (ValueError, UnicodeError):
                            body = None
                        if response.status_code == 400 and attempt == 0 and self._needs_completion_tokens(body):
                            finish('negotiated')
                            payload['max_completion_tokens'] = payload.pop('max_tokens')
                            continue
                        self._check_status(response.status_code)
                        break
        except ModelError as exc:
            finish('failed', str(exc))
            raise
        except httpx.TimeoutException:
            finish('failed', '模型请求超时，请重试或调整超时设置')
            raise ModelError('模型请求超时，请重试或调整超时设置') from None
        except (httpx.HTTPError, OSError, ValueError):
            finish('failed', '无法连接模型服务，请检查服务地址、网络和本地服务状态')
            raise ModelError('无法连接模型服务，请检查服务地址、网络和本地服务状态') from None
        except BaseException:
            finish('failed', '模型请求未完成')
            raise
        try:
            choice = body['choices'][0]
            if choice.get('finish_reason') == 'length':
                raise ModelError('模型输出达到长度上限，未保存不完整结果；请提高输出预算或缩小分块')
            if choice.get('finish_reason') == 'content_filter':
                raise ModelError('模型服务未返回可用内容')
            finish_reason = choice.get('finish_reason')
            if isinstance(finish_reason, str) and finish_reason.lower() in {
                    'error', 'interrupted', 'cancelled', 'canceled', 'max_tokens', 'max_output_tokens'}:
                raise ModelError('模型输出异常中断，未保存不完整结果；请重新生成或调整输出预算')
            text = choice['message']['content']
            if not isinstance(text, str) or not text.strip():
                raise ModelError('模型未返回有效文本，请检查模型能力或输出预算')
            if key and key in text:
                raise ModelError('模型返回内容包含凭证信息，已阻止保存')
            usage = body.get('usage') or {}
            usage = {k: usage[k] for k in ('prompt_tokens', 'completion_tokens', 'total_tokens')
                     if isinstance(usage.get(k), int) and not isinstance(usage[k], bool) and usage[k] >= 0}
            actual_model = body.get('model')
            if not isinstance(actual_model, str) or len(actual_model) > 200 or (key and key in actual_model):
                actual_model = config['model']
        except ModelError as exc:
            finish('failed', str(exc))
            raise
        except (KeyError, TypeError, IndexError, AttributeError):
            finish('failed', '模型响应不符合 Chat Completions 文本协议')
            raise ModelError('模型响应不符合 Chat Completions 文本协议') from None
        finish('succeeded')
        return {'text': text, 'usage': usage, 'model': actual_model, 'durationMs': round((time.monotonic()-started)*1000)}

    @staticmethod
    def _needs_completion_tokens(body):
        if not isinstance(body, dict) or not isinstance(body.get('error'), dict):
            return False
        error = body['error']
        message = str(error.get('message', '')).lower()
        return (error.get('param') == 'max_tokens' or 'max_tokens' in message) and (
            'max_completion_tokens' in message or error.get('code') in ('unsupported_parameter', 'unsupported_value'))

    @staticmethod
    def _check_status(status):
        if 200 <= status < 300:
            return
        if status in (401, 403):
            raise ModelError('模型服务拒绝认证，请检查密钥和访问权限')
        if status == 429:
            raise ModelError('模型服务触发限流或额度不足，请稍后重试并检查额度')
        if status == 404:
            raise ModelError('模型服务路径或模型不存在，请核对服务地址（含所需 /v1）和模型名')
        if 300 <= status < 400:
            raise ModelError('模型服务要求重定向；为保护密钥已拒绝，请填写最终服务地址')
        raise ModelError(f'模型服务返回 HTTP {status}，请核对配置后重试')

    def _test_embedding_connection(self, config):
        """Probe one embedding without interpreting it as text generation."""
        headers = {'Accept': 'application/json', 'Content-Type': 'application/json'}
        if config.get('apiKey'):
            headers['Authorization'] = 'Bearer ' + config['apiKey']
        timeout = min(config['timeoutSeconds'], 30)
        payload = {'model': config['model'], 'encoding_format': 'float', 'input': [
            'Instruct: Given a web search query, retrieve relevant passages that answer the query\n'
            'Query: 中文资料检索连接测试']}
        started = time.monotonic()
        try:
            guard = _REQUEST_GUARD.get()
            if guard:
                guard()
            with httpx.Client(timeout=httpx.Timeout(timeout, connect=min(10, timeout)),
                              follow_redirects=False, trust_env=False, transport=self.transport) as client:
                with client.stream('POST', normalize_base_url(config['baseUrl']) + '/embeddings',
                                   json=payload, headers=headers) as response:
                    self._check_status(response.status_code)
                    raw = bytearray()
                    for chunk in response.iter_bytes():
                        if time.monotonic() - started > timeout:
                            raise ModelError('向量测试超时，请检查模型加载与本地服务状态')
                        raw.extend(chunk)
                        if len(raw) > MAX_RESPONSE_BYTES:
                            raise ModelError('向量测试返回内容过大，已停止接收')
            body = json.loads(raw)
        except ModelError:
            raise
        except httpx.TimeoutException:
            raise ModelError('向量测试超时，请检查模型加载与本地服务状态') from None
        except (httpx.HTTPError, OSError):
            raise ModelError('无法连接向量服务，请检查服务地址、网络和本地服务状态') from None
        except (ValueError, UnicodeError):
            raise ModelError('向量响应不符合 Embeddings 协议') from None
        try:
            data = body['data']
            if not isinstance(data, list) or len(data) != 1 or not isinstance(data[0], dict):
                raise ValueError()
            item = data[0]
            if isinstance(item.get('index'), bool) or not isinstance(item.get('index'), int) or item['index'] != 0:
                raise ValueError()
            vector = item['embedding']
            if not isinstance(vector, list) or not 1 <= len(vector) <= 8192 or any(
                    isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value)
                    for value in vector):
                raise ValueError()
            norm = math.hypot(*vector)
            if not math.isfinite(norm) or not norm:
                raise ValueError()
            usage = body.get('usage') or {}
            if not isinstance(usage, dict):
                usage = {}
            usage = {key: usage[key] for key in ('prompt_tokens', 'total_tokens')
                     if isinstance(usage.get(key), int) and not isinstance(usage[key], bool) and usage[key] >= 0}
        except (KeyError, TypeError, ValueError, OverflowError):
            raise ModelError('向量响应数量、序号、维度或数值无效') from None
        return {'dimensions': len(vector), 'usage': usage, 'durationMs': round((time.monotonic()-started)*1000)}

    def test_connection(self, id):
        config = self.resolve(id)
        config['maxOutputTokens'] = min(config['maxOutputTokens'], 256)
        test = {'modelId': id, 'configVersion': config['version'], 'textGeneration': False,
                'jsonParseable': False, 'structuredOutput': 'not_tested', 'testedAt': now(),
                'durationMs': 0}
        started = time.monotonic()
        try:
            if re.fullmatch(r'qwen3-embedding(?::[^\s:]+)?', config['model'], flags=re.IGNORECASE):
                test['embeddings'] = False
                result = self._test_embedding_connection(config)
                test.update(result, embeddings=True, message=f"向量连接成功 · 向量维度 {result['dimensions']}")
            else:
                result = self.complete(config, [{'role': 'user', 'content': 'Reply with exactly this JSON object and no other text: {"ok":true}'}])
                test['textGeneration'] = True
                test['durationMs'] = result['durationMs']
                test['usage'] = result['usage']
                try:
                    test['jsonParseable'] = isinstance(json.loads(result['text']), dict)
                except ValueError:
                    pass
        except ModelError as error:
            test['error'] = str(error)
            test['durationMs'] = round((time.monotonic()-started)*1000)
        with self.store.connect() as db:
            db.execute('INSERT OR REPLACE INTO ai_settings VALUES(?,?)', (f"test:{id}:{config['version']}", dumps(test)))
        return test
