"""Read-only help and removed demonstration import routes use synthetic data."""
from tempfile import SpooledTemporaryFile

from fastapi import HTTPException
import pytest
from starlette.datastructures import UploadFile
from starlette.requests import Request

from server import presets
from server.presets_api import router
from server.tests.test_help_editions import guide_pack, write_manifest


def endpoint(routes, path):
    return next(route.endpoint for route in routes if getattr(route, 'path', None) == path)


def request_scope(path, method='GET'):
    return {'type': 'http', 'asgi': {'version': '3.0'}, 'http_version': '1.1',
            'method': method, 'scheme': 'http', 'path': path, 'raw_path': path.encode(),
            'query_string': b'', 'root_path': '', 'server': ('testserver', 80),
            'client': ('127.0.0.1', 1234), 'headers': [(b'host', b'testserver')]}


def run_in_memory(coroutine):
    """These endpoints only await in-memory ASGI/file operations, never sockets."""
    try:
        coroutine.send(None)
    except StopIteration as complete:
        return complete.value
    finally:
        coroutine.close()
    raise AssertionError('An in-memory endpoint unexpectedly requested asynchronous I/O')


def route_status(routes, path, method='POST'):
    async def invoke():
        sent = []
        async def receive():
            return {'type': 'http.request', 'body': b'', 'more_body': False}
        async def send(message):
            sent.append(message)
        await routes(request_scope(path, method), receive, send)
        return next(message['status'] for message in sent if message['type'] == 'http.response.start')
    return run_in_memory(invoke())


def test_full_legacy_pack_is_used_only_for_help_without_opening_other_files(guide_pack):
    root, manifest, _ = guide_pack
    manifest['items'].append({'id': 'removed-document', 'title': '旧资料',
                              'file': 'missing/article.html', 'sha256': '0' * 64})
    manifest['example'] = {'id': 'removed-writing', 'file': 'missing/writing.json', 'sha256': '0' * 64}
    write_manifest(root, manifest)
    selected = presets.load_manifest(root)
    assert [item['id'] for item in selected['items']] == [presets.USER_GUIDE_ID]
    assert 'example' not in selected
    current = presets.catalog(root)
    assert set(current) == {'version', 'items'}
    assert len(current['items']) == 1 and current['items'][0]['kind'] == 'manual'
    for item_id in ('removed-document', 'removed-writing'):
        with pytest.raises(KeyError):
            presets.document_resource(item_id, root)


def test_missing_help_never_substitutes_a_demo_document(guide_pack):
    root, manifest, _ = guide_pack
    manifest['items'] = [{'id': 'other-document', 'title': '不是使用指南', 'file': 'not-read.html'}]
    write_manifest(root, manifest)
    assert presets.catalog(root)['items'] == []
    with pytest.raises(KeyError):
        presets.document_resource(presets.USER_GUIDE_ID, root)


@pytest.mark.parametrize('name', ['../outside.html', '/absolute.html', 'manual\\guide.html'])
def test_help_path_escape_is_rejected(guide_pack, name):
    root, manifest, _ = guide_pack
    manifest['items'][0]['variants']['server']['file'] = name
    write_manifest(root, manifest)
    with pytest.raises(presets.PresetError):
        presets.document_resource(presets.USER_GUIDE_ID, root)


def test_changed_help_hash_is_rejected(guide_pack):
    root, manifest, _ = guide_pack
    path = root / manifest['items'][0]['variants']['server']['file']
    path.write_bytes(b'<html>changed</html>')
    with pytest.raises(presets.PresetError, match='校验失败'):
        presets.document_resource(presets.USER_GUIDE_ID, root)


def test_unknown_help_and_removed_install_route_have_no_write_handler(guide_pack):
    routes = router(deployment='server')
    assert route_status(routes, '/api/presets/install') == 404
    assert route_status(routes, '/api/presets/install', 'GET') == 404
    with pytest.raises(HTTPException) as failure:
        endpoint(routes.routes, '/api/presets/{item_id}/view')('removed-document')
    assert failure.value.status_code == 404


@pytest.mark.parametrize('path', ['/api/imports/corpus', '/api/import/workspace', '/api/imports/workspace'])
def test_demonstration_import_routes_are_absent_and_enqueue_nothing(tmp_path, guide_pack, path):
    from server.app import create_app
    app = create_app(data_dir=tmp_path / 'isolated-app')
    assert route_status(app.router, path) == 404
    assert route_status(app.router, '/api/presets/install') == 404
    assert not hasattr(app.state.store, 'enqueue_workspace')
    assert not hasattr(app.state.store, 'enqueue_corpus')
    assert app.state.store.jobs()['counts']['total'] == 0
    assert app.state.store.documents()['total'] == 0


def test_normal_upload_and_url_queue_endpoints_remain_available(tmp_path, guide_pack):
    from server.app import create_app
    app = create_app(data_dir=tmp_path / 'normal-imports')
    files = endpoint(app.routes, '/api/imports/files')
    raw = b'<html><body><article><h1>Author document</h1><p>New author input.</p></article></body></html>'
    upload = SpooledTemporaryFile(max_size=1024 * 1024)
    upload.write(raw)
    upload.seek(0)
    result = run_in_memory(files(Request(request_scope('/api/imports/files', 'POST')),
                                 [UploadFile(filename='author.html', file=upload)]))
    assert len(result['jobs']) == 1 and result['jobs'][0]['kind'] == 'file'
    urls = endpoint(app.routes, '/api/imports/urls')
    body_type = urls.__annotations__['body']
    queued = urls(body_type(urls=['https://example.org/author']), Request(request_scope('/api/imports/urls', 'POST')))
    assert len(queued['jobs']) == 1 and queued['jobs'][0]['kind'] == 'url'
    assert app.state.store.jobs()['counts']['total'] == 2
    assert app.state.store.documents()['total'] == 0
