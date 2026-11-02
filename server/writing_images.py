"""Program-owned illustration anchors and immutable export resources."""
from __future__ import annotations

import base64
import copy
import hashlib
from io import BytesIO
import json
import re

from .store import dumps


PRESERVED_IMAGE_SCOPE = 'preserved-approved-image-scope-v1'


def _placement_approval_hash(placement):
    value = {key: val for key, val in placement.items()
             if key not in ('preservedSourceScope', 'scopeHash', 'locked', 'updatedAt')}
    if isinstance(value.get('imageSourceApproval'), dict):
        value['imageSourceApproval'] = {key: val for key, val in value['imageSourceApproval'].items()
                                        if key != 'scopeHash'}
    return content_hash(dumps(value))


def _preserved_image_source(placement):
    """One program-approved picture cannot authorize any other asset or prose."""
    saved = placement.get('preservedSourceScope')
    if (not isinstance(saved, dict) or saved.get('policy') != PRESERVED_IMAGE_SCOPE
            or saved.get('scopeHash') != placement.get('scopeHash')
            or saved.get('placementHash') != _placement_approval_hash(placement)
            or placement.get('status') != 'accepted' or placement.get('reviewed') is not True
            or placement.get('qualityPolicy') != 'writing-image-quality-1'):
        return None
    source = saved.get('source')
    if (not isinstance(source, dict) or any(not isinstance(source.get(key), str) or not source[key]
            or source[key] != placement.get(key) for key in ('sourceId', 'revisionId'))
            or 'blockIds' in source and (not isinstance(source['blockIds'], list)
                or any(not isinstance(value, str) or not value for value in source['blockIds']))):
        return None
    return source


def preserve_approved_illustrations(before, project, *, allow_source_change=False):
    """Keep checked old pictures when preferences change only a future run.

    Call before normal stale detection, only at the v2 options patch or frozen
    v2 material transition. Existing fingerprints and quality rules retain
    their original meaning; a saved approval is scoped to one unchanged image.
    """
    options = (project.get('brief') or {}).get('automaticOptions')
    if (not isinstance(options, dict) or not set(options) & {'useSelectedSources', 'useRecommendedSources', 'mode'}
            or set(options) - {'useSelectedSources', 'useRecommendedSources', 'mode', 'includeImages'}
            or any(type(options.get(key, True)) is not bool
                   for key in ('useSelectedSources', 'useRecommendedSources', 'includeImages'))
            or options.get('mode', 'strict') not in ('strict', 'extended')):
        return
    clean_brief = lambda value: {key: item for key, item in (value or {}).items()
                                 if key not in ('automaticOptions', 'automation')}
    if before.get('title') != project.get('title') or clean_brief(before.get('brief')) != clean_brief(project.get('brief')):
        return
    expected_mode = options.get('mode', 'strict') if options.get('useSelectedSources', True) or options.get('useRecommendedSources', True) else 'extended'
    if before.get('mode') != project.get('mode') and project.get('mode') != expected_mode:
        return
    if not allow_source_change and any(before.get(key) != project.get(key)
                                       for key in ('sources', 'sourceIds', 'materialSelection')):
        return
    originals = {item['id']: item for item in before.get('illustrations', [])}
    old_sections = {section['id']: section for section in before.get('sections', [])}
    current_sections = {section['id']: section for section in project.get('sections', [])}
    for placement in project.get('illustrations', []):
        original = originals.get(placement.get('id'))
        old = old_sections.get(placement.get('sectionId'))
        current = current_sections.get(placement.get('sectionId'))
        if (original is None or original != placement or old is None or current is None
                or original.get('status') != 'accepted' or original.get('reviewed') is not True
                or original.get('qualityPolicy') != 'writing-image-quality-1'
                or not valid_placement(before, original)
                or any(old.get(key) != current.get(key) for key in ('id', 'title', 'goal', 'mode', 'content', 'evidence'))):
            continue
        source = _preserved_image_source(original) or next((source for source in before.get('sources', [])
            if (source['sourceId'], source['revisionId']) == (original.get('sourceId'), original.get('revisionId'))), None)
        if source is None and original.get('sourceType') == 'library':
            source = {key: original[key] for key in ('sourceId', 'revisionId')}
        if source is None:
            continue
        placement['scopeHash'] = scope_hash(project, current)
        if original.get('sourceType') == 'library':
            placement['imageSourceApproval']['scopeHash'] = placement['scopeHash']
        placement['preservedSourceScope'] = {'policy': PRESERVED_IMAGE_SCOPE,
            'scopeHash': placement['scopeHash'], 'source': copy.deepcopy(source),
            'placementHash': _placement_approval_hash(placement)}
    review = before.get('illustrationReview') or {}
    if (review.get('status') in ('complete', 'skipped') and review.get('fingerprint') == review_fingerprint(before)
            and all(item.get('status') == 'accepted' and item.get('reviewed') is True
                    and valid_placement(project, item) for item in project.get('illustrations', []))):
        project['illustrationReview'] = {**review, 'fingerprint': review_fingerprint(project)}


def reanchor_citation_only_images(before, project):
    """Carry existing picture approval across marker-only citation relocation.

    Historical fingerprints keep their original definition. Only a placement
    valid immediately before this operation can receive an equivalent anchor;
    source scope, prose, paragraph order and existing stale states are fenced.
    """
    from .writing_sentence_citations import strip_citations

    old_sections = {section['id']: section for section in before.get('sections', [])}
    current_sections = {section['id']: section for section in project.get('sections', [])}
    if old_sections.keys() != current_sections.keys() or any(
            strip_citations(section.get('content', '')) != strip_citations(current_sections[sid].get('content', ''))
            or scope_hash(before, section) != scope_hash(project, current_sections[sid])
            for sid, section in old_sections.items()):
        return
    previous = {item['id']: item for item in before.get('illustrations', [])}
    for placement in project.get('illustrations', []):
        original = previous.get(placement.get('id'))
        if original is None or original.get('status') == 'stale' or not valid_placement(before, original):
            continue
        section = current_sections.get(placement.get('sectionId'))
        if section is None:
            continue
        try:
            anchor = anchor_for(section, original['anchor']['paragraphIndex'])
        except (ValueError, KeyError):
            continue
        if strip_citations(anchor['paragraphExact']) == strip_citations(original['anchor']['paragraphExact']):
            placement['anchor'] = anchor
            if _preserved_image_source(original) and placement.get('preservedSourceScope') == original.get('preservedSourceScope'):
                placement['preservedSourceScope']['placementHash'] = _placement_approval_hash(placement)
    review = before.get('illustrationReview') or {}
    if review.get('status') in ('complete', 'skipped') and review.get('fingerprint') == review_fingerprint(before):
        project['illustrationReview'] = {**review, 'fingerprint': review_fingerprint(project)}


def content_hash(value):
    return hashlib.sha256(value.encode('utf-8')).hexdigest()


def paragraphs(text):
    result, cursor = [], 0
    for match in re.finditer(r'\n\s*\n', text):
        part = text[cursor:match.start()]
        if part.strip():
            result.append({'index': len(result), 'text': part, 'start': cursor, 'end': match.start()})
        cursor = match.end()
    if text[cursor:].strip():
        result.append({'index': len(result), 'text': text[cursor:], 'start': cursor, 'end': len(text)})
    return result


def scope_hash(project, section):
    return content_hash(dumps({
        'title': project.get('title'), 'brief': project.get('brief'), 'mode': project.get('mode'),
        'sources': project.get('sources', []), 'sectionId': section.get('id'),
        'sectionTitle': section.get('title'), 'goal': section.get('goal'),
        'sectionMode': section.get('mode'), 'evidence': section.get('evidence', []),
    }))


def search_fingerprint(project, section):
    return content_hash(scope_hash(project, section) + content_hash(section.get('content', '')))


def anchor_for(section, index):
    values = paragraphs(section.get('content', ''))
    if isinstance(index, bool) or not isinstance(index, int) or not 0 <= index < len(values):
        raise ValueError('请选择当前章节中已有正文的段落')
    return {'paragraphIndex': index, 'paragraphExact': values[index]['text'],
            'contentHash': content_hash(section['content'])}


def valid_placement(project, placement):
    section = next((s for s in project.get('sections', []) if s['id'] == placement.get('sectionId')), None)
    if not section:
        return False
    try:
        anchor = placement.get('anchor', {})
        if anchor != anchor_for(section, anchor.get('paragraphIndex')):
            return False
    except ValueError:
        return False
    if placement.get('scopeHash') and placement['scopeHash'] != scope_hash(project, section):
        return False
    preserved_source = _preserved_image_source(placement)
    if 'preservedSourceScope' in placement and preserved_source is None:
        return False
    source = preserved_source or next((s for s in project.get('sources', [])
                   if (s['sourceId'], s['revisionId']) == (placement.get('sourceId'), placement.get('revisionId'))), None)
    if placement.get('sourceType') == 'library':
        # Program-created proof grants one image only. A direct human selection
        # has no model run and must retain its own exact selection identity.
        approval = placement.get('imageSourceApproval')
        delegated_quality = (placement.get('reviewMethod') == 'automatic_quality_checked'
            and placement.get('qualityPolicy') == 'writing-image-quality-1'
            and isinstance(approval, dict) and approval.get('method') == 'explicit_library_delegation')
        if (placement.get('librarySourceConfirmed') is not True or (placement.get('reviewMethod') != 'human' and not delegated_quality)
                or not placement.get('scopeHash') or not isinstance(approval, dict)
                or not isinstance(approval.get('confirmedAt'), str) or not approval['confirmedAt']
                or not preserved_source and any(s['sourceId'] == placement.get('sourceId') for s in project.get('sources', []))):
            return False
        manual_selection = approval.get('method') == 'manual_selection'
        if manual_selection and (placement.get('selectionMethod') != 'manual'
                or placement.get('reviewMethod') != 'human' or approval.get('selectionId') != placement.get('id')
                or not placement.get('id') or placement.get('runId') or placement.get('qualityPolicy')):
            return False
        keys = ('sourceId', 'revisionId', 'assetId', 'assetHash', 'scopeHash')
        if not manual_selection:
            keys += ('runId',)
        return all(approval.get(key) == placement.get(key) and bool(placement.get(key)) for key in keys)
    if placement.get('sourceType', 'reference') != 'reference':
        return False
    if source is None:
        return False
    if 'blockIds' in source:
        required = placement.get('scopeBlockIds') or []
        if not required or not set(required).issubset(set(source['blockIds'])):
            return False
    return True


def review_fingerprint(project):
    return content_hash(dumps({
        'sections': [search_fingerprint(project, s) for s in project.get('sections', [])],
        'figures': [{k: v for k, v in item.items() if k not in ('locked', 'updatedAt')}
                    for item in project.get('illustrations', [])],
    }))


def refresh_illustrations(project):
    for item in project.get('illustrations', []):
        if not valid_placement(project, item):
            item['status'] = 'stale'
            item['reviewed'] = False
    review = project.get('illustrationReview')
    if review and review.get('fingerprint') != review_fingerprint(project):
        review['status'] = 'stale'


def image_issues(db, project):
    issues = []
    def add(code, message, item=None, severity='error'):
        issues.append({'code': code, 'message': message, 'sectionId': item.get('sectionId') if item else None,
                       'paragraphIndex': item.get('anchor', {}).get('paragraphIndex') if item else None,
                       'evidenceId': None, 'severity': severity})
    for item in project.get('illustrations', []):
        if item.get('status') != 'accepted' or not item.get('reviewed') or not valid_placement(project, item):
            add('image_stale', '配图对应的正文或来源已变化，请重新核对位置与图注', item)
        if not asset_owned_by_source(db, item):
            add('image_source_missing', '配图原始素材或固定来源版本不可用', item)
        if not item.get('caption', '').strip():
            add('image_caption_missing', '配图尚无图注，请补充客观说明', item, 'warning')
    state = project.get('illustrationReview')
    if project.get('illustrations') and (not state or state.get('status') != 'complete'
                                       or state.get('fingerprint') != review_fingerprint(project)):
        add('images_unconfirmed', '请在配图步骤核对并完成本稿配图', severity='warning')
    return issues


def asset_owned_by_source(db, item):
    row = db.execute('SELECT a.revision_id,r.source_id FROM assets a JOIN revisions r ON r.id=a.revision_id '
                     'WHERE a.id=?', (item.get('assetId'),)).fetchone()
    return bool(row and row['revision_id'] == item.get('revisionId') and row['source_id'] == item.get('sourceId'))


def asset_bytes(store, db, asset_id, revision_id, expected_hash=None):
    from PIL import Image
    row = db.execute('SELECT blob_path,mime FROM assets WHERE id=? AND revision_id=?', (asset_id, revision_id)).fetchone()
    if not row:
        raise ValueError('图片素材或其固定版本已不可用')
    path = (store.root / row['blob_path']).resolve()
    if not path.is_relative_to((store.root / 'assets').resolve()) or not path.is_file():
        raise ValueError('图片文件不可用，请重新检查原资料')
    if not 0 < path.stat().st_size <= 8 * 1024 * 1024:
        raise ValueError('图片文件超出可用大小')
    data = path.read_bytes()
    digest = hashlib.sha256(data).hexdigest()
    if expected_hash and digest != expected_hash:
        raise ValueError('图片内容已变化，请重新选图并核对')
    try:
        with Image.open(BytesIO(data)) as image:
            actual = {'PNG': 'image/png', 'JPEG': 'image/jpeg', 'WEBP': 'image/webp', 'GIF': 'image/gif'}.get(image.format)
            width, height = image.size
            if actual != row['mime'] or width * height > 40_000_000:
                raise ValueError()
            image.verify()
    except Exception:
        raise ValueError('图片文件损坏或格式不适合导出') from None
    return {'data': base64.b64encode(data).decode('ascii'), 'mime': actual,
            'width': width, 'height': height, 'sha256': digest}, len(data)


def freeze_image_resources(store, db, project):
    resources, total = {}, 0
    figures = project.get('illustrations', [])
    if len(figures) > 40:
        raise ValueError('每篇文章最多使用 40 张图片')
    for item in figures:
        if item.get('status') != 'accepted' or not item.get('reviewed') or not valid_placement(project, item):
            raise ValueError('配图有待复核的位置、来源或图注，请先完成核对再导出')
        if not asset_owned_by_source(db, item):
            raise ValueError('配图原始素材或固定来源版本不可用')
        if item['assetId'] in resources:
            continue
        resource, size = asset_bytes(store, db, item['assetId'], item['revisionId'], item.get('assetHash'))
        total += size
        if total > 8 * 1024 * 1024:
            raise ValueError('本稿配图总大小超过 8 MiB，请减少图片或使用较小的原始素材')
        resources[item['assetId']] = resource
    # Private ephemeral bytes never enter manuscript persistence or model input.
    if resources:
        project['_imageResources'] = resources
