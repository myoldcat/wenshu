"""Two frozen entries share one private runtime; never collect project data."""
from pathlib import Path
import os
import sys
import importlib.metadata
import hashlib
import json
from PyInstaller.utils.hooks import collect_data_files

ROOT = Path(SPECPATH).resolve().parents[1]
FRONTEND = ROOT / 'frontend' / 'dist' / 'client'
NATIVE = Path(os.environ.get('WENSHU_NATIVE_DIR', ROOT / 'output' / 'windows-native'))
PRESETS = ROOT / 'resources' / 'presets'
for required in (FRONTEND / 'index.html', FRONTEND / 'operations.html', NATIVE / 'libpango-1.0-0.dll', PRESETS / 'manifest.json',
                 PRESETS / 'manual/user-guide.html', PRESETS / 'manual/user-guide-server.html', PRESETS / 'manual/user-guide-windows.html'):
    if not required.is_file():
        raise SystemExit(f'Missing release resource: {required}')

# Release inputs are an explicit allowlist. Libraries are analyzed separately;
# data/, knowledge_base/, output/, secrets, node_modules and source maps are not
# recursively collected from the checkout.
assets = [(str(FRONTEND), 'frontend'), (str(ROOT / 'deploy' / 'server' / 'fonts'), 'fonts')]
# Include only help files named by the manifest. A leftover sample directory or
# an older starter pack must never be collected recursively into a new release.
manifest = json.loads((PRESETS / 'manifest.json').read_text(encoding='utf-8-sig'))
items = manifest.get('items', [])
if manifest.get('example') or len(items) != 1 or items[0].get('kind') != 'manual' or items[0].get('id') != 'wenshu-user-guide':
    raise SystemExit('Release resources must contain the manual-only manifest')
preset_files = {'manifest.json'}


def include_preset(relative, expected_sha256=None):
    path = (PRESETS / relative).resolve()
    if not path.is_relative_to(PRESETS.resolve()) or not path.is_file():
        raise SystemExit(f'Invalid help resource: {relative}')
    if expected_sha256 and hashlib.sha256(path.read_bytes()).hexdigest() != expected_sha256:
        raise SystemExit(f'Help resource hash mismatch: {relative}')
    preset_files.add(path.relative_to(PRESETS.resolve()).as_posix())


for item in items:
    include_preset(item['file'], item.get('sha256'))
    include_preset(item['licenseFile'])
    for variant in item.get('variants', {}).values():
        include_preset(variant['file'], variant.get('sha256'))
for relative in sorted(preset_files):
    parent = Path(relative).parent.as_posix()
    assets.append((str(PRESETS / relative), 'presets' if parent == '.' else 'presets/' + parent))
# Retain dependency notices while keeping dependency source trees out of the
# release. This also carries the PyInstaller bootloader license exception.
for distribution in importlib.metadata.distributions():
    for relative in distribution.files or ():
        name = Path(str(relative)).name.lower()
        if name.startswith(('license', 'copying', 'copyright', 'notice')):
            source = Path(distribution.locate_file(relative))
            if source.is_file():
                safe_parent = '/'.join(part for part in Path(str(relative)).parent.parts if part not in ('.', '..'))
                assets.append((str(source), 'licenses/python/' + distribution.metadata['Name'] + '/' + safe_parent))
for filename in ('LICENSE.txt', 'LICENSE'):
    license_file = Path(sys.base_prefix) / filename
    if license_file.is_file():
        assets.append((str(license_file), 'licenses/python-runtime'))
modules = ROOT / 'frontend' / 'node_modules'
if modules.is_dir():
    for source in modules.rglob('*'):
        if source.is_file() and source.name.lower().startswith(('license', 'copying', 'copyright', 'notice')):
            relative = source.relative_to(modules)
            if 'node_modules' not in relative.parts and source.suffix.lower() not in ('.js', '.ts', '.map'):
                assets.append((str(source), 'licenses/npm/' + str(relative.parent)))
native_data = [(str(item), 'native/' + str(item.relative_to(NATIVE).parent))
               for item in NATIVE.rglob('*') if item.is_file() and item.suffix.lower() != '.dll']
native_binaries = [(str(item), 'native') for item in NATIVE.glob('*.dll')]
business_data = []
for package in ('trafilatura', 'justext', 'courlan', 'htmldate', 'dateparser', 'opencc', 'tld'):
    business_data += collect_data_files(package)
excluded = ['pytest', '_pytest', 'tkinter', 'matplotlib', 'pandas', 'IPython',
            'jupyter', 'PyQt5', 'PyQt6', 'PySide2', 'PySide6', 'cefpython3',
            'webview.platforms.gtk', 'webview.platforms.qt', 'webview.platforms.cocoa',
            'webview.platforms.cef', 'webview.platforms.android', 'server.tests']

gui = Analysis([str(ROOT / 'desktop' / 'gui.py')], pathex=[str(ROOT)],
               binaries=[], datas=[], hiddenimports=['webview.platforms.winforms'],
               excludes=excluded, noarchive=False)
service = Analysis([str(ROOT / 'desktop' / 'service.py')], pathex=[str(ROOT)],
                   binaries=native_binaries, datas=assets + native_data + business_data,
                   hiddenimports=['uvicorn.logging', 'uvicorn.loops.asyncio',
                                  'uvicorn.protocols.http.h11_impl',
                                  'uvicorn.lifespan.on', 'scipy.special._ufuncs'],
                   excludes=excluded, noarchive=False)

icon = ROOT / 'deploy' / 'windows' / 'wenshu.ico'
icon_arg = str(icon) if icon.is_file() else None
gui_exe = EXE(PYZ(gui.pure), gui.scripts, [], exclude_binaries=True,
              name='Wenshu', debug=False, bootloader_ignore_signals=False,
              strip=False, upx=False, console=False, icon=icon_arg)
service_exe = EXE(PYZ(service.pure), service.scripts, [], exclude_binaries=True,
                  name='WenshuService', debug=False, bootloader_ignore_signals=False,
                  strip=False, upx=False, console=True, icon=icon_arg)
release = COLLECT(gui_exe, service_exe, gui.binaries, gui.datas,
                  service.binaries, service.datas, strip=False, upx=False,
                  name='Wenshu')
