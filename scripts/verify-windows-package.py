"""Exercise bundled help, ordinary imports and PDF using an isolated library."""
from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
from pathlib import Path
import secrets
import sqlite3
import subprocess
import tempfile
import time

import httpx
from pypdf import PdfReader


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--bundle', type=Path, required=True)
    parser.add_argument('--output', type=Path, default=Path('output/qa/windows-package'))
    parser.add_argument('--port', type=int, default=18789)
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    output = args.output.resolve()
    if not output.is_relative_to(root / 'output'):
        raise SystemExit('Acceptance output must stay inside this workspace/output')
    output.mkdir(parents=True, exist_ok=True)
    run = Path(tempfile.mkdtemp(prefix='package-', dir=output))
    data, control = run / 'data', run / 'control'
    data.mkdir()
    control.mkdir()
    helper = args.bundle.resolve() / 'WenshuService.exe'
    frontend = helper.parent / '_internal/frontend'
    preset_root = helper.parent / '_internal/presets'
    manifest_file = preset_root / 'manifest.json'
    manifest = json.loads(manifest_file.read_text(encoding='utf-8-sig')) if manifest_file.is_file() else None
    entries = manifest.get('items', []) if manifest else []
    if not manifest or manifest.get('example') or len(entries) != 1 or entries[0].get('kind') != 'manual' or entries[0].get('id') != 'wenshu-user-guide':
        raise SystemExit('Verify a new application built with the manual-only resource pack.')
    token = secrets.token_urlsafe(48)
    env = os.environ.copy()
    # Verify the actual bundled resources even when the invoking development
    # shell happens to have a source-tree resource override.
    env.pop('WENSHU_PRESETS_DIR', None)
    env.update(WENSHU_DATA_DIR=str(data), WENSHU_DESKTOP_TOKEN=token,
               WENSHU_DESKTOP_CONTROL_DIR=str(control), WENSHU_DESKTOP_PARENT_PID=str(os.getpid()),
               PYTHONUTF8='1', PYTHONIOENCODING='utf-8')
    processes, logs, checks = [], [], []

    def check(name, condition):
        if not condition:
            raise AssertionError(name)
        checks.append(name)

    def start(role):
        log = open(run / (role + '.log'), 'ab', buffering=0)
        logs.append(log)
        process = subprocess.Popen([str(helper), '--' + role, '--data-dir', str(data),
            '--control-dir', str(control), '--frontend-dir', str(frontend), '--port', str(args.port)],
            env=env, cwd=helper.parent, stdin=subprocess.DEVNULL, stdout=log, stderr=log,
            creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        processes.append(process)
        return process

    base = f'http://127.0.0.1:{args.port}'
    client = httpx.Client(base_url=base, timeout=10, trust_env=False,
                         headers={'X-Wenshu-Client': 'local', 'Origin': base})
    result = {'checks': checks, 'dataDirectory': str(data), 'modelsCalled': 0,
              'bundledHelpDocuments': len(entries), 'initialLibraryDocuments': 0}

    def ready(api):
        deadline = time.monotonic() + 90
        while time.monotonic() < deadline:
            if api.poll() is not None:
                raise AssertionError('Frozen API exited during startup: ' + (run / 'api.log').read_text(errors='replace')[-3000:])
            try:
                if client.get('/desktop/ready').status_code == 200:
                    break
            except httpx.HTTPError:
                pass
            time.sleep(.2)
        else:
            raise AssertionError('Frozen API readiness timeout')

    def stopped(label):
        check(label + ' shutdown accepted', client.post('/desktop/stop').status_code == 200)
        for process in processes:
            process.wait(timeout=45)
        check(label + ' services exit cleanly', all(process.returncode == 0 for process in processes))

    def worker_ready(worker, label):
        deadline = time.monotonic() + 45
        state_file = control / 'worker-state.json'
        while time.monotonic() < deadline:
            if worker.poll() is not None:
                raise AssertionError(label + ' worker exited before readiness')
            if state_file.is_file():
                state = json.loads(state_file.read_text(encoding='utf-8'))
                if state['status'] in ('ready', 'running'):
                    check(label + ' worker completed recovery and is ready', True)
                    return
            time.sleep(.2)
        raise AssertionError(label + ' worker readiness timeout')

    def no_automatic_ai(database, label):
        check(label + ' no scheduled model jobs', database.execute(
            "SELECT COUNT(*) FROM jobs WHERE kind='ai_schedule' OR kind LIKE 'ai_%' OR kind LIKE 'writing_%'"
        ).fetchone()[0] == 0)
        for table in ('ai_runs', 'ai_calls', 'writing_runs', 'writing_model_interactions'):
            check(label + ' no ' + table, database.execute('SELECT COUNT(*) FROM ' + table).fetchone()[0] == 0)

    try:
        api = start('api')
        ready(api)
        check('frozen API starts', True)
        check('unauthenticated API denied', client.get('/api/documents').status_code == 403)
        if manifest:
            check('preset catalog requires desktop session', client.get('/api/presets').status_code == 403)
            check('preset view requires desktop session', client.get('/api/presets/' + entries[0]['id'] + '/view').status_code == 403)
            check('preset download requires desktop session', client.get('/api/presets/' + entries[0]['id'] + '/download').status_code == 403)
        check('one-use session established', client.get('/desktop/launch', params={'token': token}).status_code == 303)
        page = client.get('/')
        check('frontend includes current app', page.status_code == 200)
        check('desktop frontend explicitly marks its edition', 'data-wenshu-desktop' in page.text)
        check('independent operations page', client.get('/operations.html').status_code == 200)
        check('outside origin denied', client.get('/api/documents', headers={'Origin': 'https://example.invalid'}).status_code == 403)
        check('new independent library starts empty', client.get('/api/documents').json()['total'] == 0)
        if manifest:
            catalog = client.get('/api/presets').json()
            check('read-only catalog exposes the bundled help',
                  {item['id'] for item in catalog['items']} == {item['id'] for item in entries})
            for entry in entries:
                item = next(item for item in catalog['items'] if item['id'] == entry['id'])
                if entry.get('variants'):
                    check(entry['id'] + ' desktop selects Windows help', item.get('helpEdition') == 'windows')
                    entry = {**entry, **entry['variants']['windows']}
                resource = (preset_root / entry['file']).resolve()
                check(entry['id'] + ' resource stays within package', resource.is_relative_to(preset_root.resolve()))
                raw_preset = resource.read_bytes()
                digest = entry.get('sha256') or entry.get('contentSha256')
                check(entry['id'] + ' bundled hash matches manifest', hashlib.sha256(raw_preset).hexdigest() == digest)
                view = client.get(item['viewUrl'])
                check(entry['id'] + ' view bytes and iframe security', view.status_code == 200 and view.content == raw_preset and
                      view.headers.get('x-frame-options') == 'SAMEORIGIN' and
                      "frame-ancestors 'self'" in view.headers.get('content-security-policy', '') and
                      'sandbox' in view.headers.get('content-security-policy', ''))
                download = client.get(item['downloadUrl'])
                check(entry['id'] + ' preset download is exact attachment', download.status_code == 200 and
                      download.content == raw_preset and 'attachment;' in download.headers.get('content-disposition', ''))
            check('reading help leaves the library empty', client.get('/api/documents').json()['total'] == 0)
            with sqlite3.connect(data / 'library.sqlite3') as database:
                check('no bundled writing manuscripts', database.execute('SELECT COUNT(*) FROM writing_projects').fetchone()[0] == 0)
                no_automatic_ai(database, 'first start')
        worker = start('worker')
        worker_ready(worker, 'first run')
        raw = ('<!doctype html><html><head><meta charset="utf-8"><title>Windows安装验收资料</title></head>'
               '<body><article><h1>Windows安装验收资料</h1><p>这是一篇独立编写的合成文档，用于验证文枢桌面版的入库、'
               '原文快照和全文检索。验收只处理这份临时资料，不调用任何模型服务。'
               '正文包含足够的中文内容，便于结构提取和字数统计。</p></article></body></html>').encode('utf-8')
        response = client.post('/api/imports/files', files=[('files', ('安装验收.html', raw, 'text/html'))])
        check('HTML import accepted', response.status_code == 202)
        job_id = response.json()['jobs'][0]['id']
        deadline = time.monotonic() + 45
        while time.monotonic() < deadline:
            job = client.get('/api/jobs/' + job_id).json()['job']
            if job['status'] in ('succeeded', 'failed', 'cancelled'):
                break
            if worker.poll() is not None:
                raise AssertionError('Frozen worker stopped: ' + (run / 'worker.log').read_text(errors='replace')[-3000:])
            time.sleep(.2)
        check('frozen worker publishes import', job['status'] == 'succeeded')
        original = client.get(f"/api/documents/{job['source_id']}/original", params={'revision_id': job['revision_id']})
        check('fixed-version download bytes', original.status_code == 200 and original.content == raw)
        check('download attachment disposition', 'attachment;' in original.headers.get('content-disposition', ''))
        search = client.get('/api/search', params={'q': '合成文档'}).json()
        check('Chinese full-text search finds synthetic document', any(item['id'] == job['source_id'] for item in search['results']))
        with httpx.Client(base_url=base, trust_env=False) as anonymous:
            check('download requires desktop session', anonymous.get(str(original.url)).status_code == 403)
        source = ('<!doctype html><meta charset="utf-8"><style>body{font-family:FandolSong;font-size:16pt}</style>'
                  '<h1>文枢桌面版</h1><p>安装验收：中文正文与标点完整保留。</p>').encode('utf-8')
        pdf = subprocess.run([str(helper), '--pdf'], input=source, capture_output=True, env=env,
                             cwd=helper.parent, timeout=60, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        check('frozen PDF helper succeeds', pdf.returncode == 0 and pdf.stdout.startswith(b'%PDF-'))
        reader = PdfReader(io.BytesIO(pdf.stdout), strict=True)
        text = ''.join(page.extract_text() or '' for page in reader.pages)
        check('Chinese PDF text survives', '文枢桌面版' in text and '安装验收：中文正文与标点完整保留。' in text)
        (run / 'acceptance.pdf').write_bytes(pdf.stdout)
        stopped('first run')
        # The second process startup must reuse the saved collection and draft;
        # browsing and repeated starts must not create new histories or tasks.
        control = run / 'control-restart'
        control.mkdir()
        token = secrets.token_urlsafe(48)
        env.update(WENSHU_DESKTOP_TOKEN=token, WENSHU_DESKTOP_CONTROL_DIR=str(control))
        client.cookies.clear()
        api = start('api')
        ready(api)
        check('restart requires a new desktop session', client.get('/api/documents').status_code == 403)
        check('restart session established', client.get('/desktop/launch', params={'token': token}).status_code == 303)
        check('restart retains exactly the explicitly imported document', client.get('/api/documents').json()['total'] == 1)
        if manifest:
            catalog = client.get('/api/presets').json()
            check('restart retains read-only help', {item['id'] for item in catalog['items']} == {item['id'] for item in entries})
        worker = start('worker')
        worker_ready(worker, 'restart')
        stopped('restart')
        with sqlite3.connect(data / 'library.sqlite3') as database:
            check('library integrity', database.execute('PRAGMA integrity_check').fetchone()[0] == 'ok')
            check('only the explicitly imported synthetic document is saved', database.execute('SELECT COUNT(*) FROM sources').fetchone()[0] == 1)
            check('no model configuration or credentials', database.execute('SELECT COUNT(*) FROM ai_model_configs').fetchone()[0] == 0)
            no_automatic_ai(database, 'after restart')
            check('no automatic writing project is created', database.execute('SELECT COUNT(*) FROM writing_projects').fetchone()[0] == 0)
            result['modelsCalled'] = sum(database.execute('SELECT COUNT(*) FROM ' + table).fetchone()[0] for table in ('ai_calls', 'writing_model_interactions'))
        result.update(passed=True, pdfBytes=len(pdf.stdout), pdfSha256=hashlib.sha256(pdf.stdout).hexdigest())
    finally:
        (control / 'stop').touch(exist_ok=True)
        for process in processes:
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                # These are exclusively this acceptance run's isolated children;
                # no real library, model configuration or user process is used.
                process.terminate()
                process.wait(timeout=10)
        client.close()
        for log in logs:
            log.close()
        (run / 'report.json').write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
        print(json.dumps({'report': str(run / 'report.json'), 'passed': result.get('passed', False), 'checks': len(checks)}, ensure_ascii=False))


if __name__ == '__main__':
    main()
