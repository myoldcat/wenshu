"""Archive a clean portable staging tree, never a running profile or library."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from zipfile import ZipFile, ZIP_DEFLATED


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('stage', type=Path)
    parser.add_argument('archive', type=Path)
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    output = (root / 'output').resolve()
    stage, archive = args.stage.resolve(), args.archive.resolve()
    if not stage.is_relative_to(output) or not archive.is_relative_to(output):
        parser.error('Portable staging and archive paths must stay inside output/.')
    for folder in ('data', 'profile'):
        if any((stage / folder).iterdir()):
            parser.error('Portable packages must contain empty data and profile directories.')
    preset_root = stage / 'app/_internal/presets'
    manifest = json.loads((preset_root / 'manifest.json').read_text(encoding='utf-8-sig'))
    items = manifest.get('items', [])
    if manifest.get('example') or len(items) != 1 or items[0].get('kind') != 'manual' or items[0].get('id') != 'wenshu-user-guide':
        parser.error('Portable releases must use the manual-only resource pack.')
    allowed = {'manifest.json'}
    for item in items:
        references = [item, *item.get('variants', {}).values(), {'file': item['licenseFile']}]
        for reference in references:
            path = (preset_root / reference['file']).resolve()
            if not path.is_relative_to(preset_root.resolve()) or not path.is_file():
                parser.error('Portable help resource is missing or outside its package.')
            if reference.get('sha256') and hashlib.sha256(path.read_bytes()).hexdigest() != reference['sha256']:
                parser.error('Portable help resource hash does not match its manifest.')
            allowed.add(path.relative_to(preset_root.resolve()).as_posix())
    actual = {path.relative_to(preset_root).as_posix() for path in preset_root.rglob('*') if path.is_file()}
    if actual != allowed:
        parser.error('Portable help resources contain unlisted files or bundled examples; rebuild the application.')
    files = sorted(stage.rglob('*'))
    for path in files:
        if path.is_symlink() or path.is_junction():
            parser.error('Portable inputs must not contain symlinks or junctions.')
        if path.name in {'.env', 'model.key', 'id_rsa', 'id_ed25519'} or path.suffix.lower() in {'.sqlite', '.sqlite3', '.db', '.map'}:
            parser.error('Portable input contains a forbidden user-data or development file.')
    count = size = 0
    with ZipFile(archive, 'w', compression=ZIP_DEFLATED, compresslevel=6) as package:
        for path in files:
            name = 'Wenshu/' + path.relative_to(stage).as_posix()
            if path.is_dir():
                if not any(path.iterdir()):
                    package.writestr(name + '/', b'')
                continue
            package.write(path, name)
            count += 1
            size += path.stat().st_size
    print(json.dumps({'files': count, 'expandedBytes': size, 'bytes': archive.stat().st_size,
                      'sha256': hashlib.file_digest(archive.open('rb'), 'sha256').hexdigest()}))


if __name__ == '__main__':
    main()
