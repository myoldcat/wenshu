"""Read-only help resources with deployment selection and verified local HTML."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import sys
from urllib.parse import urlsplit

from .store import ROOT

MAX_ITEM_BYTES = 32 * 1024 * 1024
MAX_PACKAGE_BYTES = 128 * 1024 * 1024
HELP_EDITIONS = frozenset({'server', 'windows'})
USER_GUIDE_ID = 'wenshu-user-guide'

# Retained for existing databases and backups. Help readers never execute it.
SCHEMA = '''
CREATE TABLE IF NOT EXISTS preset_packages(
 package_id TEXT PRIMARY KEY, version TEXT NOT NULL,
 status TEXT NOT NULL, started_at TEXT NOT NULL, completed_at TEXT
);
CREATE TABLE IF NOT EXISTS preset_imports(
 package_id TEXT NOT NULL, item_id TEXT NOT NULL, kind TEXT NOT NULL,
 content_sha256 TEXT NOT NULL, version TEXT NOT NULL, status TEXT NOT NULL,
 job_id TEXT, source_id TEXT, revision_id TEXT, project_id TEXT,
 error TEXT, updated_at TEXT NOT NULL,
 PRIMARY KEY(package_id,item_id,kind)
);
'''


class PresetError(ValueError):
    pass


def help_edition(deployment=None):
    """Use application identity, never browser OS or URL hints."""
    edition = deployment if deployment is not None else (
        'windows' if sys.platform == 'win32' and getattr(sys, 'frozen', False) else 'server')
    if not isinstance(edition, str) or edition not in HELP_EDITIONS:
        raise PresetError('使用指南部署类型无效')
    return edition


def resource_directory():
    """The optional override selects help resources, never an import source."""
    configured = os.environ.get('WENSHU_PRESETS_DIR')
    if configured:
        return Path(configured).resolve()
    if getattr(sys, 'frozen', False):
        return Path(sys._MEIPASS).resolve() / 'presets'
    return ROOT / 'resources' / 'presets'


def _resource(root, name, *, suffix):
    if not isinstance(name, str) or not name or '\\' in name:
        raise PresetError('帮助资源文件名称无效')
    relative = Path(name)
    path = (root / relative).resolve()
    if (relative.is_absolute() or '..' in relative.parts or path == root or
            not path.is_relative_to(root) or path.suffix.lower() != suffix or
            not path.is_file()):
        raise PresetError('帮助资源文件缺失或超出资源目录')
    if path.stat().st_size > MAX_ITEM_BYTES:
        raise PresetError('帮助资源超过单篇容量限制')
    return path


def _checked_bytes(root, item, suffix):
    path = _resource(root, item.get('file'), suffix=suffix)
    expected = item.get('sha256') or item.get('contentSha256')
    if not isinstance(expected, str) or not re.fullmatch('[a-fA-F0-9]{64}', expected):
        raise PresetError('帮助资源缺少有效校验摘要')
    raw = path.read_bytes()
    if hashlib.sha256(raw).hexdigest() != expected.lower():
        raise PresetError('帮助资源校验失败，请更新完整帮助资源')
    return raw


def load_manifest(resource_root=None, *, verify_files=True, deployment=None):
    edition = help_edition(deployment)
    root = Path(resource_root or resource_directory()).resolve()
    manifest_path = root / 'manifest.json'
    if not manifest_path.is_file():
        return None
    if manifest_path.stat().st_size > 512 * 1024:
        raise PresetError('帮助资源目录清单超过容量限制')
    try:
        manifest = json.loads(manifest_path.read_text(encoding='utf-8-sig'))
    except (ValueError, UnicodeError):
        raise PresetError('帮助资源目录清单格式无效') from None
    if not isinstance(manifest, dict) or not isinstance(manifest.get('version'), str) or not manifest['version']:
        raise PresetError('帮助资源版本无效')
    items = manifest.get('items')
    if not isinstance(items, list) or len(items) > 100:
        raise PresetError('帮助资源目录清单格式无效')
    # A former full resource pack may still be selected as a help directory.
    # Only the canonical help entry is read; other files are never processed.
    guides = [item for item in items if isinstance(item, dict) and item.get('id') == USER_GUIDE_ID]
    if len(guides) > 1:
        raise PresetError('使用指南标识重复')
    total = 0
    selected = []
    for original in guides:
        item = dict(original)
        if not isinstance(item.get('title'), str) or not item['title'].strip():
            raise PresetError('使用指南标题无效')
        url = item.get('sourceUrl', '')
        if url and (not isinstance(url, str) or urlsplit(url).scheme not in ('http', 'https') or
                    not urlsplit(url).hostname or urlsplit(url).username or urlsplit(url).password):
            raise PresetError('使用指南来源地址无效')
        if verify_files:
            total += len(_checked_bytes(root, item, '.html'))
        variants = item.get('variants')
        if variants is not None:
            if not isinstance(variants, dict) or set(variants) != HELP_EDITIONS:
                raise PresetError('使用指南部署版本清单无效')
            for variant in variants.values():
                if not isinstance(variant, dict) or set(variant) != {'file', 'sha256'}:
                    raise PresetError('使用指南部署版本清单无效')
                if verify_files:
                    total += len(_checked_bytes(root, variant, '.html'))
            item.update(variants[edition], helpEdition=edition)
        item['kind'] = 'manual'
        selected.append(item)
    if total > MAX_PACKAGE_BYTES:
        raise PresetError('帮助资源超过容量限制')
    package_id = manifest.get('id') or 'wenshu-starter-library'
    if not isinstance(package_id, str) or not re.fullmatch('[A-Za-z0-9][A-Za-z0-9_.-]{0,99}', package_id):
        raise PresetError('帮助资源标识无效')
    return {'id': package_id, 'version': manifest['version'], 'items': selected}


def catalog(resource_root=None, *, deployment=None):
    manifest = load_manifest(resource_root, verify_files=False, deployment=deployment)
    if manifest is None:
        return {'version': None, 'items': []}
    result = []
    for item in manifest['items']:
        entry = {key: item.get(key, '') for key in (
            'id', 'title', 'language', 'category', 'sourceUrl', 'license',
            'licenseUrl', 'attribution', 'description', 'sourceName', 'kind')}
        if item.get('helpEdition'):
            entry['helpEdition'] = item['helpEdition']
        entry.update(viewUrl=f"/api/presets/{item['id']}/view",
                     downloadUrl=f"/api/presets/{item['id']}/download")
        result.append(entry)
    return {'version': manifest['version'], 'items': result}


def document_resource(item_id, resource_root=None, *, deployment=None):
    root = Path(resource_root or resource_directory()).resolve()
    manifest = load_manifest(root, verify_files=False, deployment=deployment)
    item = next((entry for entry in (manifest or {}).get('items', []) if entry['id'] == item_id), None)
    if item is None:
        raise KeyError(item_id)
    return item, _checked_bytes(root, item, '.html')
