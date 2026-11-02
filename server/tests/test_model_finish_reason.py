"""A real local HTTP provider cannot publish explicitly interrupted output."""
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from server.model_gateway import ModelError, ModelService
from server.store import Store
from server.tests.test_model_gateway import TestCipher


@pytest.fixture
def local_provider():
    calls = []

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            payload = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
            calls.append({'path': self.path, 'payload': payload})
            reason = payload['messages'][0]['content']
            # The JSON shape is complete: a provider interruption must still
            # fail before writing output can treat the text as a usable draft.
            body = json.dumps({'model': 'local-stub', 'choices': [{
                'message': {'content': '{"paragraphs":[{"text":"Usable-looking body."}]}'},
                'finish_reason': reason}]}).encode('utf-8')
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, format, *args):
            pass

    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    worker = threading.Thread(target=server.serve_forever, kwargs={'poll_interval': .05}, daemon=True)
    worker.start()
    try:
        yield f'http://127.0.0.1:{server.server_port}/v1', calls
    finally:
        server.shutdown()
        server.server_close()
        worker.join(timeout=2)


@pytest.mark.parametrize('finish_reason', [
    'error', 'interrupted', 'cancelled', 'canceled', 'max_tokens', 'max_output_tokens', 'stop',
])
def test_real_http_finish_reason_rejects_interruption_and_accepts_stop(tmp_path, local_provider, finish_reason):
    base_url, calls = local_provider
    service = ModelService(Store(tmp_path / 'models'), cipher=TestCipher())
    model = service.save_config({'name': '本机隔离模型', 'baseUrl': base_url, 'model': 'stub'})
    config = service.resolve(model['id'], model['version'])
    messages = [{'role': 'user', 'content': finish_reason}]
    if finish_reason == 'stop':
        result = service.complete(config, messages)
        assert json.loads(result['text']) == {'paragraphs': [{'text': 'Usable-looking body.'}]}
    else:
        with pytest.raises(ModelError, match='异常中断.*未保存不完整结果'):
            service.complete(config, messages)
    assert len(calls) == 1
    assert calls[0]['path'] == '/v1/chat/completions'
    assert calls[0]['payload']['messages'] == messages
