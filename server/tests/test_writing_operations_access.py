"""Exercise the private host guard directly, without a library or HTTP server."""
import pytest
from fastapi import HTTPException
from starlette.requests import Request

from server.writing_operations_api import private_operations


def request(host, origin=None, forwarded=None):
    headers = [(b'host', host.encode())]
    if origin is not None:
        headers.append((b'origin', origin.encode()))
    if forwarded:
        headers.append((b'x-forwarded-host', forwarded.encode()))
    return Request({'type': 'http', 'headers': headers})


@pytest.mark.parametrize('host', ['127.0.0.1', 'localhost:4173', 'testserver'])
def test_default_operations_hosts_remain_local(monkeypatch, host):
    monkeypatch.delenv('WENSHU_OPERATIONS_ALLOWED_HOSTS', raising=False)
    assert private_operations(request(host)) is None


@pytest.mark.parametrize('host', ['public.example', 'app.test', '192.0.2.10'])
def test_main_app_hosts_never_enable_private_operations(monkeypatch, host):
    monkeypatch.delenv('WENSHU_OPERATIONS_ALLOWED_HOSTS', raising=False)
    monkeypatch.setenv('WENSHU_ALLOWED_HOSTS', 'public.example,app.test,192.0.2.10')
    with pytest.raises(HTTPException) as failure:
        private_operations(request(host, forwarded='localhost'))
    assert failure.value.status_code == 403


def test_private_hosts_require_the_separate_explicit_allowlist(monkeypatch):
    monkeypatch.setenv('WENSHU_OPERATIONS_ALLOWED_HOSTS', ' APP.TEST, 192.0.2.10,app.test ')
    assert private_operations(request('APP.TEST:4173', 'http://app.test:4173')) is None
    assert private_operations(request('192.0.2.10', 'https://192.0.2.10')) is None
    assert private_operations(request('testserver')) is None
    for host, origin in [('public.example', None), ('app.test.public.example', None), ('192.0.2.11', None),
                         ('app.test', 'https://public.example'), ('app.test', 'null')]:
        with pytest.raises(HTTPException) as failure:
            private_operations(request(host, origin))
        assert failure.value.status_code == 403


@pytest.mark.parametrize('value', ['*', '*.example.com', '.example.com', 'http://app.test', 'app.test:4173',
                                 'app.test,,localhost', '999.1.2.3', 'app.test evil', '-app.test'])
def test_invalid_private_host_configuration_fails_closed(monkeypatch, value):
    monkeypatch.setenv('WENSHU_OPERATIONS_ALLOWED_HOSTS', value)
    with pytest.raises(HTTPException) as failure:
        private_operations(request('localhost'))
    assert failure.value.status_code == 403


@pytest.mark.parametrize(('host', 'origin'), [
    ('user@localhost', None), ('localhost.', None), ('localhost:0', None), ('localhost:70000', None),
    ('localhost:4173/operations', None), ('localhost', 'http://user@localhost'),
    ('localhost', 'http://localhost/'), ('localhost', 'http://localhost:0'),
    ('localhost', 'http://localhost?next=private'), ('localhost', 'http://localhost#private'),
])
def test_authorities_and_origins_cannot_smuggle_an_allowed_host(monkeypatch, host, origin):
    monkeypatch.delenv('WENSHU_OPERATIONS_ALLOWED_HOSTS', raising=False)
    with pytest.raises(HTTPException) as failure:
        private_operations(request(host, origin))
    assert failure.value.status_code == 403
