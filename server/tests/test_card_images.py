"""Article-card candidates use actual saved image dimensions and immutable versions."""
from concurrent.futures import ThreadPoolExecutor
import base64
import struct
import zlib

import pytest

from server.image_metadata import card_image_eligible, ranked_candidates, raster_dimensions
from server.parser import parse_html
from server.store import Store


PROSE = '文章卡片只展示实际保存的合适配图，小图标不会作为封面，正文和历史版本的素材仍然完整保留。'


def png(width, height):
    def chunk(kind, payload):
        return struct.pack('>I', len(payload)) + kind + payload + struct.pack('>I', zlib.crc32(kind + payload))
    scanline = b'\0' + b'\x23\x75\x61' * width
    return (b'\x89PNG\r\n\x1a\n' + chunk(b'IHDR', struct.pack('>IIBBBBB', width, height, 8, 2, 0, 0, 0))
            + chunk(b'IDAT', zlib.compress(scanline * height)) + chunk(b'IEND', b''))


def page(dimensions=(), title='卡片配图', attrs=''):
    images = ''.join(f'<img alt="图{i}" {attrs} src="data:image/png;base64,{base64.b64encode(png(*size)).decode()}">'
                     for i, size in enumerate(dimensions))
    return f'<html><head><title>{title}</title></head><body><article><h1>{title}</h1><p>{PROSE}</p>{images}</article></body></html>'.encode()


def publish(store, dimensions=(), title='卡片配图', url='https://example.org/card', attrs=''):
    raw = page(dimensions, title, attrs)
    queued = store.queue('file', {'url': url}, title)
    job = store.claim()
    assert job['id'] == queued['id']
    store.publish(job, {'url': url}, raw, parse_html(raw))
    return store.job(job['id'])


@pytest.fixture
def store(tmp_path):
    return Store(tmp_path / 'data')


def test_empty_and_icon_assets_have_no_card_candidate_but_keep_reader_materials(store):
    empty = publish(store)
    doc = store.detail(empty['source_id'])
    assert doc['coverCandidates'] == [] and doc['ruleCoverImage'] is None
    assert doc['image'] is None and doc['assets'] == []
    icon = publish(store, [(48, 48)], title='小图标', attrs='width="2400" height="1600"')
    doc = store.detail(icon['source_id'])
    assert len(doc['assets']) == 1 and doc['image'] == doc['assets'][0]['url']
    assert doc['coverCandidates'] == [] and doc['ruleCoverImage'] is None
    assert doc['coverImage'] is None and doc['cardCover']['status'] == 'no_candidates'


def test_similar_images_favor_early_but_substantially_larger_later_image_can_win(store):
    first = publish(store, [(640, 400), (660, 400)])
    doc = store.detail(first['source_id'])
    assert doc['ruleCoverImage'] == doc['assets'][0]['url']
    assert [item['ordinal'] for item in doc['coverCandidates']] == [0, 1]
    larger = publish(store, [(48, 48), (640, 400), (660, 400), (1600, 900)], title='大图优先')
    doc = store.detail(larger['source_id'])
    assert doc['ruleCoverImage'] == doc['assets'][3]['url']
    assert doc['coverCandidates'][0] == {'assetId': doc['assets'][3]['id'], 'url': doc['assets'][3]['url'],
                                         'width': 1600, 'height': 900, 'ordinal': 3}
    assert len(doc['coverCandidates']) == 3
    # Geometry only proposes; it must never fabricate a model-approved cover.
    assert doc['coverImage'] is None


@pytest.mark.parametrize(('width', 'height', 'expected'), [
    (1, 1, False), (159, 2000, False), (300, 300, False),
    (320, 239, False), (320, 240, True), (240, 320, True),
    (2000, 180, False), (1920, 1080, True),
])
def test_small_images_and_decorative_strips_are_filtered(width, height, expected):
    assert card_image_eligible(width, height) is expected


def test_candidates_are_bounded_and_large_image_scores_saturate():
    images = [{'assetId': str(i), 'url': '/api/assets/' + str(i), 'width': 1600, 'height': 900, 'ordinal': i} for i in range(12)]
    images.append({'assetId': 'poster', 'url': '/api/assets/poster', 'width': 20_000, 'height': 15_000, 'ordinal': 12})
    assert [item['assetId'] for item in ranked_candidates(images)] == ['0', '1', '2', '3', '4', '5']


def test_header_dimensions_ignore_html_and_reject_invalid_png_crc(tmp_path):
    path = tmp_path / 'image.png'
    path.write_bytes(png(640, 480))
    assert raster_dimensions(path) == (640, 480)
    data = bytearray(path.read_bytes())
    data[16:20] = struct.pack('>I', 4096)
    path.write_bytes(data)
    assert raster_dimensions(path) == (0, 0)
    path.write_bytes(b'not a raster image')
    assert raster_dimensions(path) == (0, 0)
    assert raster_dimensions(tmp_path / 'missing.png') == (0, 0)


def test_gif_jpeg_and_webp_headers_are_read_without_decoding_or_dependencies(tmp_path):
    path = tmp_path / 'image'
    path.write_bytes(base64.b64decode('R0lGODlhAQABAIAAAAAAAP///yH5BAEAAAAALAAAAAABAAEAAAIBRAA7'))
    assert raster_dimensions(path) == (1, 1)
    # EXIF payload is skipped; dimensions come from the actual SOF frame header.
    path.write_bytes(b'\xff\xd8\xff\xe1\x00\x06Exif' + b'\xff\xc0\x00\x0b\x08\x01\xe0\x02\x80\x01\x01\x11\x00\xff\xd9')
    assert raster_dimensions(path) == (640, 480)
    for kind, payload in [
        (b'VP8X', b'\0\0\0\0' + (639).to_bytes(3, 'little') + (479).to_bytes(3, 'little')),
        (b'VP8 ', b'\0\0\0\x9d\x01\x2a' + struct.pack('<HH', 640, 480)),
        (b'VP8L', b'\x2f' + (639 | (479 << 14)).to_bytes(4, 'little')),
    ]:
        chunk = kind + struct.pack('<I', len(payload)) + payload + (b'\0' if len(payload) % 2 else b'')
        path.write_bytes(b'RIFF' + struct.pack('<I', len(chunk) + 4) + b'WEBP' + chunk)
        assert raster_dimensions(path) == (640, 480)


def test_list_search_detail_pagination_and_historical_versions_agree(store):
    first = publish(store, [(640, 480)], title='A 卡片配图')
    old = store.detail(first['source_id'])
    second = publish(store, [(960, 540)], title='B 卡片配图')
    other = publish(store, [(800, 600)], title='C 卡片配图', url='https://example.org/other')
    current = store.detail(first['source_id'])
    assert current['revisionId'] == second['revision_id']
    assert current['coverCandidates'][0]['assetId'] != old['coverCandidates'][0]['assetId']
    historical = store.detail(first['source_id'], first['revision_id'])
    assert historical['coverCandidates'] == old['coverCandidates']
    listed = {doc['id']: doc for doc in store.documents()['documents']}
    searched = {doc['id']: doc for doc in store.search('卡片配图')['results']}
    for source_id in (first['source_id'], other['source_id']):
        assert listed[source_id]['coverCandidates'] == searched[source_id]['coverCandidates']
        assert store.detail(source_id)['coverCandidates'] == listed[source_id]['coverCandidates']
    pages = [store.documents(limit=1, offset=i)['documents'][0] for i in range(2)]
    assert {doc['id'] for doc in pages} == set(listed)
    assert store.image_candidates(first['revision_id']) == old['coverCandidates']
    with pytest.raises(KeyError):
        store.image_candidates('missing-revision')


def test_lazy_backfill_of_legacy_assets_is_durable_and_does_not_rescan_blobs(store, monkeypatch):
    first = publish(store, [(48, 48), (640, 480)])
    empty = publish(store, [], title='空卡片', url='https://example.org/empty')
    # A pre-feature database contains the original assets but no cache tables.
    with store.connect() as db:
        db.execute('DROP TABLE revision_image_candidates')
        db.execute('DROP TABLE image_dimensions')
    reopened = Store(store.root)
    calls = []
    original = raster_dimensions
    def probe(path):
        calls.append(path)
        return original(path)
    monkeypatch.setattr('server.store.raster_dimensions', probe)
    page_one = reopened.documents(limit=1)
    assert len(calls) in (0, 2)  # only the actual page's revisions may be probed
    assert reopened.detail(first['source_id'])['coverCandidates']
    assert reopened.detail(empty['source_id'])['coverCandidates'] == []
    assert len(calls) == 2
    def must_not_probe(path):
        pytest.fail('a cached revision must not reopen saved assets')
    monkeypatch.setattr('server.store.raster_dimensions', must_not_probe)
    assert len(Store(store.root).documents()['documents']) == 2
    assert reopened.search('卡片')['total'] == 2
    with store.connect() as db:
        assert db.execute('SELECT count(*) FROM revision_image_candidates').fetchone()[0] == 2
        assert db.execute('SELECT count(*) FROM assets').fetchone()[0] == 2


def test_legacy_locators_fall_back_to_saved_order_and_shared_blobs_are_probed_once(store, monkeypatch):
    first = publish(store, [(640, 480), (640, 480), (640, 480)])
    with store.connect() as db:
        assets = db.execute('SELECT id FROM assets WHERE revision_id=? ORDER BY rowid', (first['revision_id'],)).fetchall()
        for asset, locator in zip(assets, ('{}', 'not-json', '[]')):
            db.execute('UPDATE assets SET locator_json=? WHERE id=?', (locator, asset['id']))
    calls = []
    def probe(path):
        calls.append(path)
        return raster_dimensions(path)
    monkeypatch.setattr('server.store.raster_dimensions', probe)
    candidates = store.image_candidates(first['revision_id'])
    assert [item['ordinal'] for item in candidates] == [0, 1, 2]
    assert len(calls) == 1  # all three assets share one content-addressed blob
    second = publish(store, [(640, 480)], title='新版本')
    assert store.image_candidates(second['revision_id'])
    assert len(calls) == 1  # persistent blob metadata is reusable across versions


def test_asset_path_outside_immutable_asset_storage_is_not_a_candidate(store, tmp_path):
    document = publish(store, [(640, 480)])
    outside = tmp_path / 'outside.png'
    outside.write_bytes(png(640, 480))
    with store.connect() as db:
        db.execute('UPDATE assets SET blob_path=? WHERE revision_id=?', (str(outside), document['revision_id']))
    assert store.image_candidates(document['revision_id']) == []


def test_concurrent_store_start_and_lazy_backfill_are_safe(store):
    document = publish(store, [(640, 480)])
    def initialize_and_read(_):
        instance = Store(store.root)
        return instance.detail(document['source_id'])['coverCandidates']
    with ThreadPoolExecutor(max_workers=3) as pool:
        results = list(pool.map(initialize_and_read, range(3)))
    assert results[0] and all(result == results[0] for result in results)
