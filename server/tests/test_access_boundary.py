"""Private-test-server opt-in must not weaken default or browser boundaries."""
import pytest
from fastapi.testclient import TestClient

from server.app import create_app


@pytest.fixture(autouse=True)
def clean_boundary_environment(monkeypatch):
    monkeypatch.delenv('WENSHU_ALLOWED_HOSTS',raising=False)
    monkeypatch.delenv('WENSHU_ALLOWED_ORIGINS',raising=False)


def test_default_rejects_app_test_host_and_origin_but_keeps_loopback(tmp_path):
    with TestClient(create_app(data_dir=tmp_path/'data')) as client:
        assert client.get('/api/health',headers={'Host':'app.test:4173'}).status_code==400
        assert client.get('/api/health',headers={'Host':'192.0.2.10:4173'}).status_code==400
        assert client.get('/api/health',headers={'Origin':'http://app.test:4173'}).status_code==403
        assert client.get('/api/health',headers={'Host':'127.0.0.1:8787','Origin':'http://127.0.0.1:4173'}).status_code==200


def test_explicit_app_test_and_ip_allow_same_origin_requests_without_cors(monkeypatch,tmp_path):
    monkeypatch.setenv('WENSHU_ALLOWED_HOSTS',' app.test, 192.0.2.10 ')
    monkeypatch.setenv('WENSHU_ALLOWED_ORIGINS','http://app.test:4173, http://192.0.2.10:4173')
    with TestClient(create_app(data_dir=tmp_path/'data')) as client:
        for host in ('app.test','192.0.2.10'):
            headers={'Host':host+':4173','Origin':'http://'+host+':4173'}
            response=client.get('/api/health',headers=headers)
            assert response.status_code==200
            assert 'access-control-allow-origin' not in response.headers
            assert client.put('/api/ai/preferences',headers=headers,json={'autoProcess':False}).status_code==403
            response=client.put('/api/ai/preferences',headers={**headers,'X-Wenshu-Client':'local'},json={'autoProcess':False})
            assert response.status_code==200
        # Vite changes only Host to the API target, preserving the browser Origin.
        assert client.get('/api/health',headers={'Host':'127.0.0.1:8787','Origin':'http://app.test:4173'}).status_code==200


@pytest.mark.parametrize('origin',[
    'http://app.test.evil.example:4173','http://evil.app.test:4173','https://app.test:4173',
    'http://app.test:4174','http://app.test:4173/','http://app.test:4173@evil.example','null',
])
def test_configured_host_never_accepts_a_different_origin(origin,monkeypatch,tmp_path):
    monkeypatch.setenv('WENSHU_ALLOWED_HOSTS','app.test,192.0.2.10')
    monkeypatch.setenv('WENSHU_ALLOWED_ORIGINS','http://app.test:4173,http://192.0.2.10:4173')
    with TestClient(create_app(data_dir=tmp_path/'data')) as client:
        response=client.put('/api/ai/preferences',headers={
            'Host':'app.test:4173','Origin':origin,'X-Wenshu-Client':'local',
        },json={'autoProcess':False})
        assert response.status_code==403


@pytest.mark.parametrize('host',['evil.app.test','app.test.evil.example','192.0.2.11'])
def test_configured_hosts_are_exact(host,monkeypatch,tmp_path):
    monkeypatch.setenv('WENSHU_ALLOWED_HOSTS','app.test,192.0.2.10')
    with TestClient(create_app(data_dir=tmp_path/'data')) as client:
        assert client.get('/api/health',headers={'Host':host+':4173'}).status_code==400


@pytest.mark.parametrize('name,value',[
    ('WENSHU_ALLOWED_HOSTS','*'),('WENSHU_ALLOWED_HOSTS','*.example.com'),
    ('WENSHU_ALLOWED_HOSTS','.example.com'),('WENSHU_ALLOWED_HOSTS','http://app.test'),
    ('WENSHU_ALLOWED_HOSTS','app.test:4173'),('WENSHU_ALLOWED_HOSTS','app.test,,localhost'),
    ('WENSHU_ALLOWED_HOSTS','app.test evil'),('WENSHU_ALLOWED_HOSTS','999.1.2.3'),
    ('WENSHU_ALLOWED_HOSTS','-app.test'),('WENSHU_ALLOWED_ORIGINS','*'),
    ('WENSHU_ALLOWED_ORIGINS','http://*.example.com:4173'),
    ('WENSHU_ALLOWED_ORIGINS','http://app.test:4173/'),
    ('WENSHU_ALLOWED_ORIGINS','http://user@app.test:4173'),
    ('WENSHU_ALLOWED_ORIGINS','http://app.test:4173?x=1'),
    ('WENSHU_ALLOWED_ORIGINS','http://app.test:4173#fragment'),
    ('WENSHU_ALLOWED_ORIGINS','ftp://app.test:4173'),
    ('WENSHU_ALLOWED_ORIGINS','http://app.test:99999'),
    ('WENSHU_ALLOWED_ORIGINS','http://app.test:'),
    ('WENSHU_ALLOWED_ORIGINS','http://app.test:4173,'),
    ('WENSHU_ALLOWED_ORIGINS','http://app.test\n:4173'),
])
def test_invalid_configuration_fails_before_data_directory_creation(name,value,monkeypatch,tmp_path):
    monkeypatch.setenv(name,value)
    data=tmp_path/'must-not-be-created'
    with pytest.raises(ValueError,match=name):
        create_app(data_dir=data)
    assert not data.exists()
