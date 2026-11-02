"""Synthetic help resources verify deployment selection without model calls."""
import hashlib
import json
import secrets

import pytest

from desktop.http_app import create_desktop_app
from server import presets
from server.model_gateway import ModelService
from server.presets_api import router
from server.store import Store


def write_manifest(root, value):
    (root / 'manifest.json').write_text(json.dumps(value, ensure_ascii=False), encoding='utf-8')


@pytest.fixture
def guide_pack(tmp_path, monkeypatch):
    monkeypatch.setattr(ModelService, 'complete', lambda *_a, **_k: pytest.fail('Help must not call a model'))
    root = tmp_path / 'help-pack'
    (root / 'manual').mkdir(parents=True)
    resources = {}
    for edition in ('legacy', 'server', 'windows'):
        name = f'manual/user-guide-{edition}.html'
        raw = (f'<!doctype html><html lang="zh"><meta charset="utf-8"><title>文枢使用指南</title>'
               f'<body><article><h1>文枢使用指南</h1><p>{edition} 版。'
               '从知源文库导入资料，使用搜索、分类和收藏找到需要的内容。进入写作空间，'
               '设定主题、选取资料，确认纲要与正文，再按需配图、检查校验及导出。'
               '帮助阅读不会添加资料、保存配置或开始生成。</p></article></body></html>').encode()
        (root / name).write_bytes(raw)
        resources[edition] = {'file': name, 'sha256': hashlib.sha256(raw).hexdigest()}
    item = {'id': 'wenshu-user-guide', 'title': '文枢使用指南', 'kind': 'manual',
            'language': 'zh', **resources['legacy'],
            'variants': {edition: resources[edition] for edition in ('server', 'windows')}}
    manifest = {'id': 'synthetic-help', 'version': '1', 'items': [item]}
    write_manifest(root, manifest)
    monkeypatch.setenv('WENSHU_PRESETS_DIR', str(root))
    return root, manifest, resources



def entries(routes):
    for route in routes:
        included = getattr(route, 'original_router', None)
        if included is not None:
            yield from entries(included.routes)
        else:
            yield route


def endpoint(routes, path):
    return next(route.endpoint for route in entries(routes) if getattr(route, 'path', None) == path)


@pytest.mark.parametrize('edition', ['server', 'windows'])
def test_api_selects_deployment_guide_with_read_only_routes(guide_pack, edition):
    root, _, resources = guide_pack
    routes = router(deployment=edition)
    assert all(route.methods <= {'GET', 'HEAD'} for route in routes.routes)
    items = endpoint(routes.routes, '/api/presets')()['items']
    assert len(items) == 1 and items[0]['helpEdition'] == edition
    assert 'installed' not in items[0]
    expected = (root / resources[edition]['file']).read_bytes()
    for suffix in ('view', 'download'):
        result = endpoint(routes.routes, '/api/presets/{item_id}/' + suffix)('wenshu-user-guide')
        assert result.body == expected
        assert result.headers['cache-control'] == 'no-store'


def test_reading_help_does_not_touch_existing_import_registry_or_user_files(tmp_path, guide_pack):
    root, _, resources = guide_pack
    store = Store(tmp_path / 'existing-library')
    with store.connect() as db:
        db.executescript(presets.SCHEMA)
        db.execute("INSERT INTO preset_packages VALUES('old-pack','1','complete','saved','saved')")
        db.execute("""INSERT INTO preset_imports
            (package_id,item_id,kind,content_sha256,version,status,updated_at)
            VALUES('old-pack','old-item','document',?,'1','complete','saved')""", ('0' * 64,))
    note = store.root / 'author-note.txt'
    note.write_bytes('作者资料与人工正文保持原样。'.encode())
    before = {p.relative_to(store.root).as_posix(): p.read_bytes()
              for p in store.root.rglob('*') if p.is_file()}
    for edition in ('server', 'windows'):
        current = presets.catalog(root, deployment=edition)['items'][0]
        assert 'installed' not in current and 'sourceId' not in current
        assert presets.document_resource(current['id'], root, deployment=edition)[1] == (root / resources[edition]['file']).read_bytes()
    after = {p.relative_to(store.root).as_posix(): p.read_bytes()
             for p in store.root.rglob('*') if p.is_file()}
    assert after == before


def test_legacy_help_resource_is_readable_for_either_application(guide_pack):
    root, manifest, resources = guide_pack
    manifest['items'][0].pop('variants')
    write_manifest(root, manifest)
    for edition in ('windows', 'server'):
        item, content = presets.document_resource('wenshu-user-guide', root, deployment=edition)
        assert 'helpEdition' not in item
        assert content == (root / resources['legacy']['file']).read_bytes()


@pytest.mark.parametrize('damage', ['missing', 'hash', 'escape', 'incomplete'])
def test_selected_variant_requires_complete_and_verified_resources(guide_pack, damage):
    root, manifest, _ = guide_pack
    selected = manifest['items'][0]['variants']['windows']
    if damage == 'missing':
        (root / selected['file']).unlink()
    elif damage == 'hash':
        selected['sha256'] = '0' * 64
    elif damage == 'escape':
        selected['file'] = '../outside.html'
    else:
        manifest['items'][0]['variants'].pop('windows')
    write_manifest(root, manifest)
    with pytest.raises(presets.PresetError):
        presets.document_resource('wenshu-user-guide', root, deployment='windows')


def test_hosting_identity_and_frozen_helper_fallback(monkeypatch):
    monkeypatch.setattr(presets.sys, 'platform', 'win32')
    monkeypatch.setattr(presets.sys, 'frozen', False, raising=False)
    assert presets.help_edition() == 'server'
    monkeypatch.setattr(presets.sys, 'frozen', True)
    assert presets.help_edition() == 'windows'
    assert presets.help_edition('server') == 'server'
    monkeypatch.setattr(presets.sys, 'platform', 'linux')
    assert presets.help_edition() == 'server'
    assert presets.help_edition('windows') == 'windows'
    with pytest.raises(presets.PresetError):
        presets.help_edition('browser-os')


def test_desktop_and_web_app_keep_independent_read_only_help(tmp_path, guide_pack, monkeypatch):
    root, _, resources = guide_pack
    monkeypatch.setenv('WENSHU_DATA_DIR', str(tmp_path / 'module-data'))
    from server.app import create_app
    frontend = tmp_path / 'frontend'
    frontend.mkdir()
    (frontend / 'index.html').write_bytes(b'<html>synthetic frontend</html>')
    desktop = create_desktop_app(tmp_path / 'desktop-data', frontend, tmp_path / 'control', secrets.token_urlsafe(48))
    web = create_app(tmp_path / 'web-data')
    for app, edition in ((desktop, 'windows'), (web, 'server')):
        before = app.state.store.db.read_bytes()
        item = endpoint(app.routes, '/api/presets')()['items'][0]
        assert item['helpEdition'] == edition
        response = endpoint(app.routes, '/api/presets/{item_id}/view')(item['id'])
        assert response.body == (root / resources[edition]['file']).read_bytes()
        assert app.state.store.db.read_bytes() == before
        assert not any(getattr(route, 'path', None) == '/api/presets/install' for route in entries(app.routes))
        with app.state.store.connect() as db:
            assert db.execute('SELECT count(*) FROM sources').fetchone()[0] == 0
            assert db.execute("SELECT name FROM sqlite_master WHERE name='preset_imports'").fetchone() is None
