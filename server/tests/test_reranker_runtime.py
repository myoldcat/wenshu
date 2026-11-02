"""Exercise resource lifecycle and the offline contract without CUDA/models."""
from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path
import threading
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer

import pytest

from server import reranker_runtime as runtime


def request():
    return {'query': '香港网络安全', 'documents': [{'id': 'one', 'documentParts': [{'kind': 'original', 'text': '香港网络安全事件及调查。'}]}]}


class Connection:
    def __init__(self, responses=(), poll=True):
        self.responses, self.readable = list(responses), poll
        self.sent, self.closed = [], False

    def poll(self, timeout):
        return self.readable and bool(self.responses)

    def recv(self):
        return self.responses.pop(0)

    def send(self, value):
        self.sent.append(value)

    def close(self):
        self.closed = True


class Process:
    def __init__(self):
        self.alive = self.started = self.terminated = self.closed = False

    def start(self):
        self.started = self.alive = True

    def is_alive(self):
        return self.alive

    def terminate(self):
        self.terminated, self.alive = True, False

    def kill(self):
        self.alive = False

    def join(self, timeout):
        pass

    def close(self):
        self.closed = True


class Context:
    def __init__(self, responses):
        self.parent, self.child = Connection(responses), Connection()
        self.process, self.kwargs = Process(), None

    def Pipe(self):
        return self.parent, self.child

    def Process(self, **kwargs):
        self.kwargs = kwargs
        return self.process


def ready_result():
    return {'ready': True, 'loadSeconds': 2.0}


def score_result():
    return {'scores': [{'id': 'one', 'score': 0.9, 'logitDiff': 2.0, 'inputTokens': 100, 'truncated': False}], 'timing': {'inferenceSeconds': 0.1, 'prepareSeconds': 0.01}}


def manager(context, **kwargs):
    result = runtime.RuntimeManager(Path('/unused'), context=context, **kwargs)
    result.verified = True
    return result


def test_lazy_load_reuse_and_idle_process_release():
    clock = [20.0]
    context = Context([ready_result(), score_result(), score_result()])
    service = manager(context, idle_seconds=60, clock=lambda: clock[0])
    assert not service.status()['loaded']
    assert not context.process.started
    first = service.score(request())
    assert first['coldStart'] is True
    assert first['timing']['loadSeconds'] == 2.0
    assert context.child.closed and context.kwargs['daemon'] is True
    second = service.score(request())
    assert second['coldStart'] is False
    assert second['timing']['loadSeconds'] == 0
    assert not service.unload_if_idle()
    clock[0] += 60
    assert service.unload_if_idle()
    assert context.process.terminated and context.process.closed
    assert context.parent.closed
    assert service.process is None and service.status()['loaded'] is False


@pytest.mark.parametrize('responses,code,status', [([], 'deadline_exceeded', 504), ([{'error': 'gpu_memory_unavailable', 'status': 503}], 'gpu_memory_unavailable', 503), ([ready_result()], 'deadline_exceeded', 504)])
def test_failed_load_or_request_terminates_only_dedicated_worker(responses, code, status):
    context = Context(responses)
    service = manager(context)
    with pytest.raises(runtime.RuntimeFailure) as caught:
        service.score(request())
    assert (caught.value.code, caught.value.status) == (code, status)
    assert context.process.terminated and context.process.closed
    assert service.process is None and not service.lock.locked()


def test_busy_rejected_without_queue_or_new_worker():
    context = Context([])
    service = manager(context)
    service.lock.acquire()
    try:
        with pytest.raises(runtime.RuntimeFailure, match='reranker_busy'):
            service.score(request())
        assert not context.process.started
        assert service.unload_if_idle() is False
    finally:
        service.lock.release()


def test_health_does_not_report_crashed_worker_as_loaded():
    context = Context([ready_result(), score_result()])
    service = manager(context)
    service.score(request())
    assert service.status()['loaded']
    context.process.alive = False
    assert not service.status()['loaded']
    service.close()


@pytest.mark.parametrize('patch', [
    {'query': ''}, {'query': 'x' * 6001}, {'documents': []}, {'documents': [{}]},
    {'documents': [{'id': 'x', 'documentParts': []}]},
    {'documents': [{'id': 'x', 'documentParts': [{'kind': 'x\ny', 'text': 'ok'}]}]},
    {'documents': [{'id': 'x', 'documentParts': [{'text': 'ok', 'minTokens': True}]}]},
    {'timeoutSeconds': float('inf')}, {'timeoutSeconds': 121}, {'timeoutSeconds': True},
])
def test_invalid_contract_rejected_before_worker_allocation(patch):
    context = Context([])
    service = manager(context)
    with pytest.raises(runtime.RuntimeFailure) as caught:
        service.score({**request(), **patch})
    assert caught.value.status == 400 and not context.process.started


def test_duplicate_ids_and_excess_candidates_rejected():
    packet = request()
    packet['documents'] *= 2
    with pytest.raises(runtime.RuntimeFailure, match='invalid_document_id'):
        runtime.validate_request(packet)
    packet['documents'] = [{'id': str(i), 'document': 'ok'} for i in range(1001)]
    with pytest.raises(runtime.RuntimeFailure, match='invalid_documents'):
        runtime.validate_request(packet)


def test_missing_or_changed_model_files_fail_verification(tmp_path, monkeypatch):
    model = tmp_path / 'config.json'
    model.write_bytes(b'good')
    monkeypatch.setattr(runtime, 'MODEL_FILES', {'config.json': (4, hashlib.sha256(b'good').hexdigest())})
    runtime.verify_model_files(tmp_path)
    model.write_bytes(b'evil')
    with pytest.raises(runtime.RuntimeFailure, match='model_checksum_mismatch'):
        runtime.verify_model_files(tmp_path)
    model.unlink()
    with pytest.raises(runtime.RuntimeFailure, match='model_unavailable'):
        runtime.verify_model_files(tmp_path)


class CharacterTokenizer:
    def encode(self, text, add_special_tokens=False):
        return [ord(c) for c in text]

    def decode(self, ids, skip_special_tokens=False):
        return ''.join(chr(c) for c in ids)


def test_balanced_context_preserves_original_parts_and_official_suffix():
    tokenizer = CharacterTokenizer()
    parts = [{'kind': f'original-{i}', 'text': chr(65 + i) * 2000, 'minTokens': 96} for i in range(6)]
    ids, meta = runtime.prepare_pair(tokenizer, '香港网络安全', parts)
    effective = tokenizer.decode(ids)
    assert len(ids) <= 4096 and meta['truncated']
    assert effective.startswith(runtime.PREFIX) and effective.endswith(runtime.SUFFIX)
    for i in range(6):
        assert f'[original-{i}]' in effective and chr(65 + i) * 20 in effective
    assert effective.count('[…truncated…]') == 6


def test_overlong_query_cannot_silently_remove_fragments_or_suffix():
    with pytest.raises(runtime.RuntimeFailure, match='packet_exceeds_context'):
        runtime.prepare_pair(CharacterTokenizer(), 'x' * 5000, [{'kind': 'original', 'text': 'one', 'minTokens': 96}])


def test_http_health_does_not_load_model_and_invalid_post_is_safe():
    path = Path(__file__).resolve().parents[2] / 'scripts' / 'serve-qwen3-reranker.py'
    spec = importlib.util.spec_from_file_location('reranker_http', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    context = Context([])
    service = manager(context)
    server = ThreadingHTTPServer(('127.0.0.1', 0), module.make_handler(service))
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    url = f'http://127.0.0.1:{server.server_port}'
    try:
        with urllib.request.urlopen(url + '/ready') as response:
            status = json.load(response)
        assert status['ready'] and not status['loaded'] and not context.process.started
        invalid = urllib.request.Request(url + '/v1/rerank', data=b'{', headers={'Content-Type': 'application/json'})
        with pytest.raises(urllib.error.HTTPError) as caught:
            urllib.request.urlopen(invalid)
        assert caught.value.code == 400
        assert json.load(caught.value) == {'error': 'invalid_json'}
        assert not context.process.started
    finally:
        server.shutdown()
        server.server_close()
        worker.join(timeout=2)
