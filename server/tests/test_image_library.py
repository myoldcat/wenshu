"""The image library preserves source associations and immutable version scope."""
import base64
from html import escape
import struct
import zlib

import pytest
from fastapi.testclient import TestClient

from server.app import create_app
from server.image_metadata import library_image_eligible, raster_dimensions
from server.parser import parse_html
from server.store import Store


# A one-pixel GIF remains in originals but cannot enter either image view.
GIF = 'R0lGODlhAQABAIAAAAAAAP///yH5BAEAAAAALAAAAAABAAEAAAIBRAA7'
PROSE = '图片库保留文章的可用配图和各自来源，原始素材及旧版本的图片仍然完整保留。'


def png(width, height):
    def chunk(kind, payload):
        return struct.pack('>I', len(payload)) + kind + payload + struct.pack('>I', zlib.crc32(kind + payload))
    scanline = b'\0' + b'\x23\x75\x61' * width
    return (b'\x89PNG\r\n\x1a\n' + chunk(b'IHDR', struct.pack('>IIBBBBB', width, height, 8, 2, 0, 0, 0))
            + chunk(b'IDAT', zlib.compress(scanline * height)) + chunk(b'IEND', b''))


def publish(store, title='图片文章', alts=(), url='https://example.org/images', dimensions=None, image_data=None, attrs=''):
    if image_data is None:
        image_data = [png(*size) for size in (dimensions or [(200, 200)] * len(alts))]
    assert len(image_data) == len(alts)
    images = ''.join(f'<img alt="{escape(alt, quote=True)}" {attrs} '
                     f'src="data:image/png;base64,{base64.b64encode(data).decode()}">'
                     for alt, data in zip(alts, image_data))
    raw = (f'<html><head><title>{escape(title)}</title></head><body><article>'
           f'<h1>{escape(title)}</h1><p>{PROSE}</p>{images}</article></body></html>').encode()
    queued = store.queue('file', {'url': url}, title)
    job = store.claim()
    assert job['id'] == queued['id']
    store.publish(job, {'url': url}, raw, parse_html(raw))
    return store.job(job['id'])


@pytest.fixture
def store(tmp_path):
    return Store(tmp_path / 'isolated-image-library')


@pytest.fixture
def workbench(tmp_path):
    app = create_app(data_dir=tmp_path / 'isolated-image-api')
    with TestClient(app) as client:
        yield client, app.state.store


def test_empty_library_and_documents_without_images(store):
    expected = {'items': [], 'total': 0, 'documentCount': 0, 'limit': 48, 'offset': 0}
    assert store.images() == expected
    publish(store, alts=())
    assert store.images() == expected


def test_usable_images_keep_associations_and_reuse_durable_blob_metadata(store, monkeypatch):
    first = publish(store, title='第一篇', alts=['微小插图', '同一图片再次出现'])
    second = publish(store, title='第二篇', alts=['共享图片'], url='')
    with store.connect() as db:
        # Blob storage deduplicates the bytes, while assets retain associations.
        assert db.execute('SELECT count(DISTINCT blob_path) FROM assets').fetchone()[0] == 1
        jobs_before = [dict(row) for row in db.execute('SELECT * FROM jobs ORDER BY id')]
    probed = []
    def record_probe(path):
        probed.append(path)
        return raster_dimensions(path)
    monkeypatch.setattr('server.store.raster_dimensions', record_probe)
    result = store.images()
    assert len(probed) == 1
    assert result['total'] == 3 and result['documentCount'] == 2
    assert len({item['id'] for item in result['items']}) == 3
    assert {item['sourceId'] for item in result['items']} == {first['source_id'], second['source_id']}
    item = next(item for item in result['items'] if item['sourceId'] == first['source_id'])
    assert item == {'id': item['id'], 'url': '/api/assets/' + item['id'], 'alt': '微小插图',
                    'mime': 'image/png', 'width': 200, 'height': 200,
                    'sourceId': first['source_id'], 'revisionId': first['revision_id'],
                    'documentTitle': '第一篇', 'originalTitle': '第一篇', 'source': 'example.org',
                    'date': item['date'], 'ordinal': 0}
    assert len(item['date']) == 10
    assert next(item for item in result['items'] if item['sourceId'] == second['source_id'])['source'] == '本地导入'
    # A fresh Store also reuses persisted dimensions, and labels do not decide
    # eligibility. These square illustrations are too small for card covers.
    monkeypatch.setattr('server.store.raster_dimensions', lambda _: pytest.fail('cached blob was reopened'))
    assert Store(store.root).images() == result
    assert store.detail(first['source_id'])['coverCandidates'] == []
    with store.connect() as db:
        assert [dict(row) for row in db.execute('SELECT * FROM jobs ORDER BY id')] == jobs_before
        assert db.execute('SELECT count(*) FROM image_dimensions').fetchone()[0] == 1


def test_only_current_published_revisions_appear_and_history_stays_accessible(store):
    old = publish(store, title='历史标题', alts=['历史图片'])
    latest = publish(store, title='当前标题', alts=['当前图片'])
    detached = publish(store, title='未发布资料', alts=['未发布图片'], url='https://example.org/detached')
    with store.connect() as db:
        db.execute('UPDATE sources SET current_revision=NULL WHERE id=?', (detached['source_id'],))
    result = store.images()
    assert result['total'] == 1 and result['documentCount'] == 1
    assert result['items'][0]['revisionId'] == latest['revision_id']
    assert result['items'][0]['alt'] == '当前图片'
    assert store.images(q='历史')['items'] == []
    assert store.images(source_id=detached['source_id'])['items'] == []
    assert store.detail(old['source_id'], old['revision_id'])['assets'][0]['alt'] == '历史图片'


def test_search_original_chinese_title_alt_and_literal_wildcards_with_exact_source_filter(store):
    first = publish(store, title='Original Research', alts=['普通图', '预算 50%_增长\\路径'])
    second = publish(store, title='其他文章', alts=['仅此一张'], url='https://example.org/other')
    with store.connect() as db:
        db.execute('INSERT INTO document_chinese VALUES(?,?,?,?,?)',
                   (first['revision_id'], '中文研究标题', '', 'original', '[]'))
    for query in ('original', '中文研究', '  Research  '):
        result = store.images(q=query)
        assert result['total'] == 2 and result['documentCount'] == 1
        assert all(item['documentTitle'] == '中文研究标题' and item['originalTitle'] == 'Original Research'
                   for item in result['items'])
    for query in ('50%', '%_', '\\路径'):
        result = store.images(q=query)
        assert result['total'] == 1
        assert result['items'][0]['ordinal'] == 1
    assert store.images(q='%')['total'] == 1
    assert store.images(q='仅此', source_id=first['source_id'])['total'] == 0
    assert store.images(q='仅此', source_id=second['source_id'])['total'] == 1
    assert store.images(source_id=first['source_id'][:-1])['total'] == 0
    assert store.images(source_id='missing')['documentCount'] == 0


def test_pagination_is_complete_stable_and_counts_all_matching_documents(store):
    first = publish(store, title='较早文章', alts=[f'早图{i}' for i in range(60)])
    second = publish(store, title='较新文章', alts=[f'新图{i}' for i in range(50)], url='https://example.org/new')
    with store.connect() as db:
        db.execute("UPDATE sources SET created_at='2026-10-20T00:00:00+00:00' WHERE id=?", (first['source_id'],))
        db.execute("UPDATE sources SET created_at='2026-10-21T00:00:00+00:00' WHERE id=?", (second['source_id'],))
    pages = [store.images(limit=48, offset=offset) for offset in (0, 48, 96)]
    assert [len(page['items']) for page in pages] == [48, 48, 14]
    assert all(page['total'] == 110 and page['documentCount'] == 2 for page in pages)
    items = [item for page in pages for item in page['items']]
    assert len({item['id'] for item in items}) == 110
    assert [item['sourceId'] for item in items] == [second['source_id']] * 50 + [first['source_id']] * 60
    assert [item['ordinal'] for item in items] == list(range(50)) + list(range(60))
    assert store.images(limit=48, offset=48) == pages[1]
    past_end = store.images(limit=100, offset=200)
    assert past_end == {'items': [], 'total': 110, 'documentCount': 2, 'limit': 100, 'offset': 200}


@pytest.mark.parametrize('locator', ['{}', 'not-json', '[]', '{"ordinal":-1}', '{"ordinal":true}'])
def test_legacy_locator_falls_back_to_unfiltered_saved_order(store, locator):
    document = publish(store, alts=['前图', '后图'])
    with store.connect() as db:
        db.execute('UPDATE assets SET locator_json=? WHERE revision_id=?', (locator, document['revision_id']))
    assert store.images(q='后图')['items'][0]['ordinal'] == 1


def test_api_page_search_and_original_asset_delivery(workbench):
    client, store = workbench
    document = publish(store, alts=['可访问图片', '第二张图'])
    response = client.get('/api/images', params={'q': '可访问图片', 'source_id': document['source_id'], 'limit': 1})
    assert response.status_code == 200
    result = response.json()
    assert result['total'] == 1 and result['documentCount'] == 1 and result['limit'] == 1 and result['offset'] == 0
    image = client.get(result['items'][0]['url'])
    assert image.status_code == 200 and image.headers['content-type'] == 'image/png'
    assert image.content == png(200, 200)


@pytest.mark.parametrize(('width', 'height', 'expected'), [
    (0, 0, False), (1, 1, False), (48, 48, False), (159, 1000, False),
    (160, 249, False), (160, 250, True), (250, 160, True),
    (200, 200, True), (160, 160, False), (160, 1000, True),
])
def test_library_geometry_boundaries_keep_squares_and_long_charts(width, height, expected):
    assert library_image_eligible(width, height) is expected


def test_detail_image_assets_and_library_share_real_dimension_filter_but_originals_stay_intact(workbench):
    client, store = workbench
    sizes = [(48, 48), (159, 1000), (160, 249), (160, 250), (200, 200), (160, 160), (160, 1000)]
    data = [base64.b64decode(GIF), *[png(*size) for size in sizes]]
    document = publish(store, alts=['像素图', '装饰图', '窄图', '略小', '边界图', '正方图', '小方图', '长图'],
                       image_data=data, attrs='width="2400" height="1600"')
    with store.connect() as db:
        jobs_before = [dict(row) for row in db.execute('SELECT * FROM jobs ORDER BY id')]
    detail = client.get('/api/documents/' + document['source_id']).json()['document']
    assert len(detail['assets']) == 8
    assert [asset['alt'] for asset in detail['imageAssets']] == ['边界图', '正方图', '长图']
    assert [(asset['width'], asset['height']) for asset in detail['imageAssets']] == [(160, 250), (200, 200), (160, 1000)]
    result = store.images()
    assert result['total'] == 3 and result['documentCount'] == 1
    assert [asset['id'] for asset in detail['imageAssets']] == [asset['id'] for asset in result['items']]
    for asset, original in zip(detail['assets'], data):
        response = client.get(asset['url'])
        assert response.status_code == 200 and response.content == original
    with store.connect() as db:
        assert [dict(row) for row in db.execute('SELECT * FROM jobs ORDER BY id')] == jobs_before


def test_filtering_precedes_pagination_and_excludes_icon_only_sources_from_counts(store):
    mixed = publish(store, title='混合文章', alts=[f'图{i}' for i in range(15)],
                    dimensions=[(200, 200) if i % 2 == 0 else (48, 48) for i in range(15)])
    icons = publish(store, title='仅有图标', alts=['图标'], dimensions=[(48, 48)], url='https://example.org/icon')
    pages = [store.images(limit=3, offset=offset) for offset in (0, 3, 6)]
    assert [len(page['items']) for page in pages] == [3, 3, 2]
    assert all(page['total'] == 8 and page['documentCount'] == 1 for page in pages)
    assert [item['ordinal'] for page in pages for item in page['items']] == list(range(0, 15, 2))
    assert len(store.detail(mixed['source_id'])['imageAssets']) == 8
    assert len(store.detail(mixed['source_id'])['assets']) == 15
    assert store.detail(icons['source_id'])['imageAssets'] == []
    assert len(store.detail(icons['source_id'])['assets']) == 1
    assert store.images(q='仅有图标')['total'] == 0
    assert store.images(source_id=icons['source_id'])['documentCount'] == 0


def test_missing_and_broken_headers_are_cached_as_empty_and_originals_are_kept(store, monkeypatch):
    broken = b'\x89PNG\r\n\x1a\nnot-a-valid-IHDR'
    document = publish(store, alts=['损坏图', '缺失图'], image_data=[broken, png(200, 200)])
    with store.connect() as db:
        rows = db.execute('SELECT blob_path FROM assets ORDER BY rowid').fetchall()
    (store.root / rows[1]['blob_path']).unlink()
    assert store.images()['items'] == []
    with store.connect() as db:
        assert [tuple(row) for row in db.execute('SELECT width,height FROM image_dimensions')] == [(0, 0), (0, 0)]
    monkeypatch.setattr('server.store.raster_dimensions', lambda _: pytest.fail('failed blob was reopened'))
    assert Store(store.root).images()['total'] == 0
    detail = store.detail(document['source_id'])
    assert detail['imageAssets'] == [] and len(detail['assets']) == 2


def test_lazy_metadata_backfill_is_limited_to_requested_current_scope(store, monkeypatch):
    old = publish(store, title='历史文章', alts=['旧图'], dimensions=[(300, 300)])
    latest = publish(store, title='当前文章', alts=['匹配图'], dimensions=[(200, 200)])
    other = publish(store, title='另一文章', alts=['其他图'], dimensions=[(210, 210)], url='https://example.org/other')
    detached = publish(store, title='未发布文章', alts=['孤立图'], dimensions=[(220, 220)], url='https://example.org/detached')
    with store.connect() as db:
        db.execute('UPDATE sources SET current_revision=NULL WHERE id=?', (detached['source_id'],))
    probed = []
    def record_probe(path):
        probed.append(path)
        return raster_dimensions(path)
    monkeypatch.setattr('server.store.raster_dimensions', record_probe)
    result = store.images(q='匹配图', source_id=latest['source_id'])
    assert result['total'] == 1 and len(probed) == 1
    with store.connect() as db:
        assert db.execute('SELECT count(*) FROM image_dimensions').fetchone()[0] == 1
    assert store.images(q='匹配图', source_id=other['source_id'])['total'] == 0
    assert len(probed) == 1
    history = store.detail(old['source_id'], old['revision_id'])
    assert len(history['assets']) == len(history['imageAssets']) == 1
    assert history['imageAssets'][0]['width'] == 300
    assert len(probed) == 2


@pytest.mark.parametrize('params', [
    {'limit': 0}, {'limit': 101}, {'limit': 'bad'}, {'offset': -1}, {'offset': 'bad'},
    {'q': '图' * 501}, {'source_id': ''}, {'source_id': 's' * 101},
])
def test_api_rejects_out_of_range_parameters(workbench, params):
    client, _ = workbench
    assert client.get('/api/images', params=params).status_code == 422


def test_api_accepts_page_and_query_boundaries(workbench):
    client, _ = workbench
    response = client.get('/api/images', params={'limit': 100, 'offset': 0, 'q': '图' * 500, 'source_id': 's' * 100})
    assert response.status_code == 200
    assert response.json() == {'items': [], 'total': 0, 'documentCount': 0, 'limit': 100, 'offset': 0}
