"""Serve deterministic graph fixtures, isolated from any application library.

Run from the project checkout after building frontend/dist/client. The
normal Wiki compiler validates synthetic citations; the gateway never uses a
network/provider. This is a disposable acceptance service, not a migration.
"""
import argparse
import json
import os
from pathlib import Path
import sys


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--root', default=str(Path(__file__).resolve().parents[1] / 'output/qa/wiki-exploration-ui'))
    parser.add_argument('--port', type=int, default=8788)
    parser.add_argument('--host', default=os.environ.get('WENSHU_QA_HOST', '127.0.0.1'))
    args = parser.parse_args()
    project = Path(__file__).resolve().parents[1]
    root = Path(args.root).resolve()
    qa_root = (project / 'output' / 'qa').resolve()
    if not root.is_relative_to(qa_root) or root == qa_root:
        raise ValueError('Fixture directory must be a dedicated child of output/qa')
    manifest_file = root / 'fixture-manifest.json'
    if root.exists() and any(root.iterdir()) and not manifest_file.exists():
        raise ValueError('Refusing to seed an existing unmarked directory')
    root.mkdir(parents=True, exist_ok=True)
    os.environ['WENSHU_DATA_DIR'] = str(root / 'data')
    os.environ['WENSHU_ALLOWED_HOSTS'] = os.environ.get('WENSHU_QA_ALLOWED_HOSTS', 'localhost,127.0.0.1')
    os.environ['WENSHU_ALLOWED_ORIGINS'] = os.environ.get('WENSHU_QA_ALLOWED_ORIGINS', f'http://localhost:{args.port},http://127.0.0.1:{args.port}')
    sys.path.insert(0, str(project))
    from server.tests.test_wiki_exploration import ExplorationBench, entry, relation
    from server.store import dumps
    bench = ExplorationBench(root / 'data')
    if not manifest_file.exists():
        members = []
        for number in range(1, 56):
            source, pages = bench.build(
                [entry('shared', 'QA同定义概念'), entry('detail', f'QA专属词条{number:02d}', 'entity')],
                [relation('shared', 'detail', '包含本来源条目')],
                title=f'图谱验收来源 {number:02d}',
            )
            members.append({'sourceId': source, 'entryId': pages['QA同定义概念']['id'],
                            'detailId': pages[f'QA专属词条{number:02d}']['id']})
            with bench.store.connect() as db:
                db.execute('UPDATE sources SET tags_json=? WHERE id=?', (dumps(['QA分页导航']), source))
        source, path_pages = bench.build(
            [entry('start', 'QA路径起点'), entry('middle', 'QA路径中间'), entry('end', 'QA路径终点')],
            [relation('middle', 'start', '上位包含', 'hierarchy'),
             relation('middle', 'end', '明确关联')], title='两跳路径验收来源',
        )
        with bench.store.connect() as db:
            db.execute('UPDATE sources SET tags_json=? WHERE id=?', (dumps(['QA两跳路径']), source))
        overview = bench.service.overview()
        graph_ids = {node['id'] for node in overview['nodes']}
        offscreen = next((item for item in members if item['detailId'] not in graph_ids), members[-1])
        detail = bench.service.page(offscreen['detailId'])
        manifest = {'fixture': 'wiki-exploration-ui-v1', 'syntheticSources': 56,
                    'syntheticPages': 113, 'realProviderCalls': 0,
                    'groupMembers': 55, 'members': members,
                    'pathIds': {label: page['id'] for label, page in path_pages.items()},
                    'offscreenQuery': detail['label'], 'offscreenId': detail['id']}
        manifest_file.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding='utf-8')
    else:
        manifest = json.loads(manifest_file.read_text(encoding='utf-8'))
        if manifest.get('fixture') != 'wiki-exploration-ui-v1':
            raise ValueError('Unexpected fixture marker')
    from server.app import create_app
    from fastapi.responses import JSONResponse
    from fastapi.staticfiles import StaticFiles
    import uvicorn
    app = create_app(data_dir=root / 'data')

    @app.middleware('http')
    async def keep_fixture_read_only(request, call_next):
        # Viewing a source may update its read flag through PATCH. Permit only
        # that harmless fixture flag; never builds, imports or source edits.
        if request.method == 'PATCH' and request.url.path.startswith('/api/documents/'):
            body = await request.json()
            if isinstance(body, dict) and set(body) == {'read'} and isinstance(body['read'], bool):
                return await call_next(request)
        if request.method not in ('GET', 'HEAD', 'OPTIONS'):
            return JSONResponse({'detail': '隔离验收样例只允许读取'}, status_code=403)
        return await call_next(request)

    app.mount('/', StaticFiles(directory=project / 'frontend' / 'dist' / 'client', html=True), name='qa-ui')
    print(json.dumps({'fixtureReady': str(manifest_file), 'port': args.port,
                      'sources': manifest['syntheticSources'], 'realProviderCalls': 0}), flush=True)
    uvicorn.run(app, host=args.host, port=args.port, log_level='warning')


if __name__ == '__main__':
    main()
