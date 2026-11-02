"""Create and verify a portable, local-only code and user-document snapshot.

No dependencies, service calls, source deletion, or overwrite of existing targets.
Run from any working directory. The output must remain within this project root.
"""
from __future__ import annotations

import argparse
import csv
from datetime import datetime, timedelta, timezone
import hashlib
import io
import json
import os
from pathlib import Path
import shutil
import sys

ROOT = Path(__file__).resolve().parents[1]
FOLDERS = (
    'frontend', 'server', 'desktop', 'deploy', 'scripts', 'resources',
    'docs', 'tools',
)
ROOT_FILES = ('README.md', 'AGENTS.md', '.gitignore', '.gitattributes', '.dockerignore')
SKIP_DIRS = {
    '.git', 'node_modules', '.venv', 'venv', '__pycache__', '.pytest_cache',
    '.mypy_cache', '.ruff_cache', '.cache', '.local-run', '.uv-cache',
    '.playwright-cli', '.wrangler', '.secrets', 'profile', 'dist', 'build', 'tmp', 'output', 'data',
}
INVENTORIES = (
    'DIRECTORY-TREE.txt', 'FILE-LIST.txt', 'FILE-LIST.csv',
    'MANIFEST.json', 'SHA256SUMS.txt',
)
OPTIONAL_FOLDERS = {'docs'}
EXCLUDED_ROOT_DIRS = {'docs-dev', 'research', 'docs.by_claude.sonet5.5'}
SOURCE_HELP_FILES = {
    'manifest.json', 'licenses/wenshu-original.txt', 'manual/user-guide.html',
    'manual/user-guide-server.html', 'manual/user-guide-windows.html',
}
CSV_FIELDS = ('path', 'category', 'bytes', 'sha256', 'source')
CATEGORIES = {
    'frontend': 'frontend', 'server': 'backend', 'desktop': 'desktop',
    'deploy': 'deployment', 'scripts': 'engineering', 'resources': 'resources',
    'docs': 'user-documentation', 'tools': 'document-tools',
}


def local_date() -> str:
    return datetime.now(timezone(timedelta(hours=8))).date().isoformat()


def digest(path: Path) -> str:
    h = hashlib.sha256()
    with path.open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            h.update(chunk)
    return h.hexdigest()


def safe_relative(root: Path, name: str) -> Path:
    relative = Path(name)
    if relative.is_absolute() or '..' in relative.parts or '\\' in name:
        raise ValueError(f'Unsafe relative path: {name}')
    resolved = (root / relative).resolve()
    if not resolved.is_relative_to(root.resolve()):
        raise ValueError(f'Path escapes root: {name}')
    current = root
    for part in relative.parts:
        current = current / part
        if current.is_symlink() or (hasattr(current, 'is_junction') and current.is_junction()):
            raise ValueError(f'Links are not allowed: {name}')
    return resolved


def ignored_file(path: Path) -> bool:
    name = path.name.lower()
    return (
        name.endswith(('.pyc', '.pyo', '.log', '.tmp', '.bak', '.key', '.pem',
                       '.pfx', '.p12', '.ppk', '.p8', '.sqlite', '.sqlite3', '.db',
                       '.sqlite-wal', '.sqlite-shm', '.sqlite3-wal', '.sqlite3-shm',
                       '.db-wal', '.db-shm', '.exe', '.msi', '.zip'))
        or (name.startswith('.env') and not name.endswith(('.example', '.template')))
        or name.endswith('.local') or '.local.' in name
        or name in {'wrangler.jsonc', 'agents.local.md'}
        or name in {'id_rsa', 'id_ed25519', '.ds_store', 'thumbs.db'}
    )


def collect_sources() -> list[tuple[Path, str]]:
    collected: list[tuple[Path, str]] = []
    for folder in FOLDERS:
        source_dir = safe_relative(ROOT, folder)
        if not source_dir.is_dir():
            if folder in OPTIONAL_FOLDERS:
                continue
            raise ValueError(f'Missing required source folder: {folder}')
        for current, dirs, files in os.walk(source_dir, followlinks=False):
            base = Path(current)
            for name in list(dirs):
                candidate = base / name
                if candidate.is_symlink() or (hasattr(candidate, 'is_junction') and candidate.is_junction()):
                    raise ValueError(f'Linked source directory: {candidate.relative_to(ROOT)}')
            dirs[:] = sorted(
                name for name in dirs
                if name not in SKIP_DIRS and not name.startswith('.tmp')
                and not (base == ROOT / 'deploy' and name not in {'server', 'windows', 'cloudflare-demo'})
            )
            for name in sorted(files):
                candidate = base / name
                if candidate.is_symlink():
                    raise ValueError(f'Linked source file: {candidate.relative_to(ROOT)}')
                if not ignored_file(candidate):
                    target = candidate.relative_to(ROOT).as_posix()
                    if target.startswith('resources/presets/') and target.removeprefix('resources/presets/') not in SOURCE_HELP_FILES:
                        continue
                    collected.append((candidate, target))
    for name in ROOT_FILES:
        candidate = safe_relative(ROOT, name)
        if not candidate.is_file():
            raise ValueError(f'Missing root file: {name}')
        collected.append((candidate, name))
    destinations = [target for _, target in collected]
    if len(set(destinations)) != len(destinations):
        raise ValueError('Duplicate destination path in source selection')
    return sorted(collected, key=lambda item: item[1])


def validate_resources(root: Path) -> dict:
    count = 0
    licenses = set()
    preset_root = root / 'resources/presets'
    manifest = json.loads((preset_root / 'manifest.json').read_text(encoding='utf-8-sig'))
    if (manifest.get('example') or len(manifest.get('items', [])) != 1 or
            manifest['items'][0].get('id') != 'wenshu-user-guide' or
            manifest['items'][0].get('kind') != 'manual'):
        raise ValueError('Source resources must contain only the user guide')

    def visit(value):
        nonlocal count
        if isinstance(value, dict):
            if 'file' in value and 'sha256' in value:
                candidate = safe_relative(preset_root, value['file'])
                if digest(candidate) != value['sha256']:
                    raise ValueError(f'Preset hash mismatch: {value["file"]}')
                count += 1
            if value.get('licenseFile'):
                candidate = safe_relative(preset_root, value['licenseFile'])
                if not candidate.is_file():
                    raise ValueError(f'Missing license: {value["licenseFile"]}')
                licenses.add(value['licenseFile'])
            for nested in value.values():
                visit(nested)
        elif isinstance(value, list):
            for nested in value:
                visit(nested)

    visit(manifest)
    font_root = root / 'deploy/server/fonts'
    font_manifest = json.loads((font_root / 'PROVENANCE.json').read_text(encoding='utf-8-sig'))
    fonts = font_manifest['files']
    for entry in fonts:
        candidate = safe_relative(font_root, entry['file'])
        if candidate.stat().st_size != entry['bytes'] or digest(candidate) != entry['sha256']:
            raise ValueError(f'Font hash/size mismatch: {entry["file"]}')
    return {'presetReferences': count, 'licenseFiles': len(licenses), 'fontFiles': len(fonts)}


def category(name: str) -> str:
    return CATEGORIES.get(name.split('/')[0], 'project')




def tree_text(names: list[str]) -> str:
    tree = {}
    for name in names:
        node = tree
        for part in name.split('/'):
            node = node.setdefault(part, {})
    lines = ['KooHub-source/']

    def walk(node, prefix=''):
        children = sorted(node, key=lambda name: (not bool(node[name]), name.lower()))
        for index, name in enumerate(children):
            last = index == len(children) - 1
            branch = '└── ' if last else '├── '
            lines.append(prefix + branch + name + ('/' if node[name] else ''))
            if node[name]:
                walk(node[name], prefix + ('    ' if last else '│   '))

    walk(tree)
    return '\n'.join(lines) + '\n'


def csv_text(entries: list[dict]) -> str:
    stream = io.StringIO(newline='')
    writer = csv.DictWriter(stream, fieldnames=CSV_FIELDS, lineterminator='\n')
    writer.writeheader()
    inventory = [dict(path=name, category='inventory', bytes='', sha256='', source='generated') for name in INVENTORIES]
    writer.writerows(sorted(entries + inventory, key=lambda item: item['path']))
    return stream.getvalue()


def metadata_texts(entries: list[dict]) -> dict[str, str]:
    names = sorted([item['path'] for item in entries] + list(INVENTORIES))
    return {
        'DIRECTORY-TREE.txt': tree_text(names),
        'FILE-LIST.txt': '\n'.join(names) + '\n',
        'FILE-LIST.csv': csv_text(entries),
        'SHA256SUMS.txt': ''.join(item['sha256'] + '  ' + item['path'] + '\n' for item in entries),
    }


def verify(target: Path) -> dict:
    target = target.resolve()
    manifest_path = safe_relative(target, 'MANIFEST.json')
    manifest = json.loads(manifest_path.read_text(encoding='utf-8'))
    if manifest.get('schemaVersion') != 1 or manifest.get('inventoryFiles') != list(INVENTORIES):
        raise ValueError('Unsupported manifest schema or inventory policy')
    entries = manifest['files']
    if entries != sorted(entries, key=lambda item: item['path']):
        raise ValueError('Manifest paths are not sorted')
    expected = set(INVENTORIES)
    total = 0
    for entry in entries:
        name = entry['path']
        if name in expected:
            raise ValueError(f'Duplicate manifest path: {name}')
        expected.add(name)
        path = safe_relative(target, name)
        if path.stat().st_size != entry['bytes'] or digest(path) != entry['sha256']:
            raise ValueError(f'File size/hash mismatch: {name}')
        total += entry['bytes']
    actual = set()
    for current, dirs, files in os.walk(target, followlinks=False):
        base = Path(current)
        dirs[:] = sorted(name for name in dirs if name not in SKIP_DIRS
                         and not name.startswith('.tmp')
                         and not (base == target and name in EXCLUDED_ROOT_DIRS))
        for name in dirs:
            safe_relative(target, (base / name).relative_to(target).as_posix())
        for name in files:
            path = base / name
            if name == '.git' or ignored_file(path):
                continue
            relative = path.relative_to(target).as_posix()
            safe_relative(target, relative)
            actual.add(relative)
    if expected != actual:
        raise ValueError(f'Inventory differs: missing={sorted(expected - actual)}, extra={sorted(actual - expected)}')
    if manifest['totalFiles'] != len(expected) or manifest['payloadBytes'] != total:
        raise ValueError('Manifest statistics mismatch')
    for name, content in metadata_texts(entries).items():
        encoding = 'utf-8-sig' if name.endswith('.csv') else 'utf-8'
        if (target / name).read_text(encoding=encoding) != content:
            raise ValueError(f'Generated inventory content mismatch: {name}')
    resources = validate_resources(target)
    if manifest['resourceValidation'] != resources:
        raise ValueError('Resource validation statistics mismatch')
    return {'verified': True, 'directory': str(target), 'totalFiles': len(expected),
            'payloadFiles': len(entries), 'payloadBytes': total, **resources}


def refresh_inventory() -> dict:
    """Explicitly accept local source edits and refresh this root's inventories."""
    manifest_path = safe_relative(ROOT, 'MANIFEST.json')
    if manifest_path.is_file():
        manifest = json.loads(manifest_path.read_text(encoding='utf-8'))
        if manifest.get('schemaVersion') != 1 or manifest.get('inventoryFiles') != list(INVENTORIES):
            raise ValueError('Unsupported manifest schema or inventory policy')
    else:
        manifest = dict(schemaVersion=1, baselineDate=local_date(), onlineCompared=False,
                        gitRevision=None, inventoryFiles=list(INVENTORIES), files=[])
    previous = {entry['path']: entry for entry in manifest['files']}
    resources = validate_resources(ROOT)
    entries = []
    for path, name in collect_sources():
        if path != ROOT / name:
            raise ValueError('Refresh must run in the independent source root')
        entries.append(dict(path=name, category=category(name), bytes=path.stat().st_size,
                            sha256=digest(path), source=previous.get(name, {}).get('source', name)))
    entries.sort(key=lambda entry: entry['path'])
    manifest.update(updatedAt=local_date(), source='KooHub-source-worktree', gitRevision=None,
                    totalFiles=len(entries) + len(INVENTORIES),
                    payloadBytes=sum(entry['bytes'] for entry in entries),
                    excludedDirectories=sorted(SKIP_DIRS | EXCLUDED_ROOT_DIRS | {'knowledge_base'}),
                    resourceValidation=resources, files=entries)
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    for name, content in metadata_texts(entries).items():
        encoding = 'utf-8-sig' if name.endswith('.csv') else 'utf-8'
        (ROOT / name).write_text(content, encoding=encoding, newline='')
    return verify(ROOT)


def build(target: Path) -> dict:
    raw_target = target.absolute()
    # Validate before creating anything; no delete, overwrite, or parent escape.
    if not raw_target.is_relative_to(ROOT):
        raise ValueError('Output must be a new directory within the project root')
    safe_relative(ROOT, raw_target.relative_to(ROOT).as_posix())
    target = target.resolve()
    if target == ROOT or not target.is_relative_to(ROOT):
        raise ValueError('Output must be a new directory within the project root')
    if raw_target.exists() or raw_target.is_symlink():
        raise ValueError(f'Output already exists; choose a fresh --output: {raw_target}')
    safe_relative(ROOT, target.relative_to(ROOT).as_posix())
    if target.relative_to(ROOT).parts[0] in set(FOLDERS) | EXCLUDED_ROOT_DIRS:
        raise ValueError('Output cannot be inside a source or excluded documentation directory')
    sources = collect_sources()
    validate_resources(ROOT)
    target.mkdir(parents=True, exist_ok=False)
    entries = []
    for source, name in sources:
        destination = safe_relative(target, name)
        destination.parent.mkdir(parents=True, exist_ok=True)
        before = digest(source)
        shutil.copy2(source, destination)
        if digest(destination) != before or digest(source) != before:
            raise ValueError(f'Source changed while copying: {name}')
        entries.append(dict(path=name, category=category(name), bytes=destination.stat().st_size,
                            sha256=before, source=source.relative_to(ROOT).as_posix()))
    entries.sort(key=lambda item: item['path'])
    resources = validate_resources(target)
    manifest = dict(schemaVersion=1, baselineDate=local_date(), source='local-workspace-snapshot',
                    onlineCompared=False, gitRevision=None, totalFiles=len(entries) + len(INVENTORIES),
                    payloadBytes=sum(item['bytes'] for item in entries), inventoryFiles=list(INVENTORIES),
                    excludedDirectories=sorted(SKIP_DIRS | EXCLUDED_ROOT_DIRS | {'knowledge_base'}),
                    resourceValidation=resources, files=entries)
    (target / 'MANIFEST.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    for name, content in metadata_texts(entries).items():
        encoding = 'utf-8-sig' if name.endswith('.csv') else 'utf-8'
        (target / name).write_text(content, encoding=encoding, newline='')
    return verify(target)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, default=ROOT / 'output/KooHub-source', help='New directory inside project root')
    actions = parser.add_mutually_exclusive_group()
    actions.add_argument('--verify', type=Path, metavar='DIRECTORY', help='Verify an existing organized snapshot, without writing')
    actions.add_argument('--refresh-inventory', action='store_true', help='Explicitly refresh inventories in this independent source root')
    args = parser.parse_args()
    try:
        if args.refresh_inventory:
            result = refresh_inventory()
        else:
            result = verify(args.verify) if args.verify is not None else build(args.output)
    except (OSError, ValueError, KeyError, TypeError) as error:
        print(f'Source organization failed: {error}', file=sys.stderr)
        return 1
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
