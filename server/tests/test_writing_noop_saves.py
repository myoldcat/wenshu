"""Business-identical saves retain completion, approvals and durable history."""
import copy

import pytest

from server.store import dumps
from server.tests.test_writing_document import document, ready as draft_ready
from server.tests.test_writing_image_tasks import images_env, accept
from server.tests.test_writing_integrity import add_source
from server.writing import WritingConflict


def persisted(service, project_id):
    with service.store.connect() as db:
        return service._load(db, project_id)


def ready(document, **options):
    project = draft_ready(document, **options)
    return document[2].confirm_step(project['id'], project['version'], 'brief')


@pytest.fixture
def completed(document):
    store, gateway, service, _ = document
    project = ready(document, words=(200,))
    queued = service.queue(project['id'], project['version'], 'section', project['sections'][0]['id'])
    service.process(store.claim())
    assert store.job(queued['job']['id'])['status'] == 'succeeded'
    project = service.get(project['id'])
    assert project['sections'][0]['manual'] is False
    project = service.confirm_step(project['id'], project['version'], 'body')
    project = service.complete_illustrations(project['id'], project['version'])
    project = service.review_confirm(project['id'], project['version'], all_issues=True)['project']
    assert project['outlineConfirmed'] and project['review']['authorConfirmed']
    return store, gateway, service, project


def save_identical(service, project, operation, expected=None):
    expected = project['version'] if expected is None else expected
    if operation == 'empty_patch':
        return service.patch(project['id'], expected, {})
    if operation == 'patch':
        return service.patch(project['id'], expected, {
            'title': ' ' + project['title'] + ' ', 'brief': copy.deepcopy(project['brief']),
            'mode': project['mode'], 'modelId': project['modelId'],
            'sourceIds': project['sourceIds'], 'materialSelection': copy.deepcopy(project['materialSelection'])})
    if operation == 'outline':
        sections = copy.deepcopy(project['sections'])
        for section in sections:
            section['title'] = ' ' + section['title'] + ' '
            section['goal'] = ' ' + section['goal'] + ' '
        return service.outline(project['id'], expected, sections)
    if operation == 'confirm':
        return service.confirm(project['id'], expected)
    if operation == 'section':
        section = project['sections'][0]
        return service.save_section(project['id'], section['id'], expected,
                                    {key: section[key] for key in ('content', 'mode', 'locked')})
    assert operation == 'images_complete'
    return service.complete_illustrations(project['id'], expected)


@pytest.mark.parametrize('operation', ['empty_patch', 'patch', 'outline', 'confirm', 'section', 'images_complete'])
def test_identical_save_preserves_entire_record_completion_and_history(completed, operation):
    _, gateway, service, project = completed
    before = persisted(service, project['id'])
    history = service.history(project['id'])
    result = save_identical(service, project, operation)
    assert persisted(service, project['id']) == before
    assert result['version'] == project['version'] and result['outlineConfirmed']
    assert result['review']['authorConfirmed'] is True
    assert result['illustrationReview'] == project['illustrationReview']
    assert service.history(project['id']) == history
    assert len(gateway.calls) == 1 and not gateway.matching_calls


@pytest.mark.parametrize('operation', ['empty_patch', 'patch', 'outline', 'confirm', 'section', 'images_complete'])
def test_identical_save_still_rejects_stale_version(completed, operation):
    _, _, service, project = completed
    before = persisted(service, project['id'])
    with pytest.raises(WritingConflict, match='变化'):
        save_identical(service, project, operation, expected=project['version'] - 1)
    assert persisted(service, project['id']) == before


@pytest.mark.parametrize('operation', ['title', 'brief', 'outline', 'body_whitespace', 'section_mode'])
def test_real_business_edit_is_saved_once_and_invalidates_prior_review(completed, operation):
    _, _, service, project = completed
    history = service.history(project['id'])
    section = project['sections'][0]
    if operation == 'title':
        result = service.patch(project['id'], project['version'], {'title': project['title'] + '修订'})
    elif operation == 'brief':
        result = service.patch(project['id'], project['version'], {'brief': {**project['brief'], 'purpose': '增加读者要求'}})
    elif operation == 'outline':
        sections = copy.deepcopy(project['sections'])
        sections[0]['goal'] += '并保留原始语境'
        result = service.outline(project['id'], project['version'], sections)
    elif operation == 'body_whitespace':
        result = service.save_section(project['id'], section['id'], project['version'], {'content': section['content'] + ' \n'})
        assert result['sections'][0]['content'] == section['content'] + ' \n'
        assert result['sections'][0]['manual'] is True
    else:
        assert section['mode'] == 'inherit'
        result = service.save_section(project['id'], section['id'], project['version'], {'mode': 'strict'})
    assert result['version'] == project['version'] + 1
    assert len(service.history(project['id'])['versions']) == len(history['versions']) + 1
    assert result['review'] is None
    assert result['illustrationReview']['status'] == 'stale'
    if operation != 'body_whitespace':
        assert result['outlineConfirmed'] is False


def test_same_selected_set_keeps_fixed_revisions_narrowed_blocks_and_recommendation(document):
    store, _, service, _ = document
    project = ready(document, words=(200,))
    second = add_source(store, url='https://example.test/noop-second-source')
    source = project['sources'][0]
    original = store.detail(source['sourceId'], source['revisionId'])
    selection = {'recommendationId': None, 'passages': [{'sourceId': source['sourceId'],
                 'revisionId': source['revisionId'], 'blockId': original['blocks'][0]['id']}]}
    project = service.patch(project['id'], project['version'],
                            {'sourceIds': [source['sourceId'], second], 'materialSelection': selection})
    project = service.confirm(project['id'], project['version'])
    with store.connect() as db:
        saved = service._load(db, project['id'])
        # An existing recommendation identity is display metadata here. An
        # identical source picker save must neither clear nor revalidate it.
        saved['materialSelection']['recommendationId'] = 'existing-recommendation'
        db.execute('UPDATE writing_projects SET content_json=? WHERE id=?', (dumps(saved), project['id']))
    before = persisted(service, project['id'])
    history = service.history(project['id'])
    result = service.patch(project['id'], project['version'], {'sourceIds': [second, source['sourceId'], second]})
    assert persisted(service, project['id']) == before
    assert result['sources'] == project['sources']
    assert result['materialSelection']['recommendationId'] == 'existing-recommendation'
    assert result['sources'][0]['blockIds'] == [original['blocks'][0]['id']]
    assert service.history(project['id']) == history


def test_empty_to_empty_is_not_a_manual_edit_but_real_clear_remains_manual(document):
    _, _, service, _ = document
    project = ready(document, words=(200,))
    section = project['sections'][0]
    unchanged = service.save_section(project['id'], section['id'], project['version'], {'content': ''})
    assert unchanged['version'] == project['version'] and not unchanged['sections'][0]['manual']
    written = service.save_section(project['id'], section['id'], project['version'], {'content': '作者曾经实际写入正文。'})
    cleared = service.save_section(project['id'], section['id'], written['version'], {'content': ''})
    assert cleared['version'] == project['version'] + 2 and cleared['sections'][0]['manual']
    assert service.save_section(project['id'], section['id'], cleared['version'], {'content': ''})['version'] == cleared['version']


def test_noop_save_during_generation_keeps_candidate_fences_valid(document):
    store, _, service, _ = document
    project = ready(document, words=(200,))
    queued = service.queue(project['id'], project['version'], 'section', project['sections'][0]['id'])
    job = store.claim()
    for operation in ('patch', 'outline', 'confirm', 'section'):
        unchanged = save_identical(service, project, operation)
        assert unchanged['version'] == project['version']
    service.process(job)
    assert store.job(queued['job']['id'])['status'] == 'succeeded'
    result = service.get(project['id'])
    assert result['sections'][0]['content'] and not result['sections'][0]['manual']


def test_noop_section_request_still_obeys_lock(completed):
    _, _, service, project = completed
    section = project['sections'][0]
    locked = service.save_section(project['id'], section['id'], project['version'], {'locked': True})
    before = persisted(service, project['id'])
    with pytest.raises(WritingConflict, match='锁定'):
        service.save_section(project['id'], section['id'], locked['version'], {'content': section['content']})
    assert persisted(service, project['id']) == before


@pytest.mark.parametrize('operation', ['picture', 'complete'])
def test_same_approved_picture_or_completion_keeps_approval_history_and_review(images_env, operation):
    env = images_env
    project = accept(env)
    project = env.service.complete_illustrations(env.project_id, project['version'])
    project = env.service.review_confirm(env.project_id, project['version'], all_issues=True)['project']
    before = persisted(env.service, env.project_id)
    history = env.service.history(env.project_id)
    if operation == 'complete':
        result = env.service.complete_illustrations(env.project_id, project['version'])
    else:
        figure = project['illustrations'][0]
        result = env.service.patch_illustration(env.project_id, figure['id'], project['version'], {
            'caption': ' ' + figure['caption'] + ' ', 'altText': figure['altText'],
            'paragraphIndex': figure['anchor']['paragraphIndex'], 'locked': figure['locked'], 'confirmUnverified': True})
    assert persisted(env.service, env.project_id) == before
    assert result['review']['authorConfirmed'] is True
    assert env.service.history(env.project_id) == history


def test_real_picture_caption_edit_requires_confirmation_and_invalidates_completion(images_env):
    env = images_env
    project = accept(env)
    project = env.service.complete_illustrations(env.project_id, project['version'])
    figure = project['illustrations'][0]
    with pytest.raises(WritingConflict, match='确认'):
        env.service.patch_illustration(env.project_id, figure['id'], project['version'], {'caption': figure['caption'] + '（原图）'})
    result = env.service.patch_illustration(env.project_id, figure['id'], project['version'],
                                           {'caption': figure['caption'] + '（原图）', 'confirmUnverified': True})
    assert result['version'] == project['version'] + 1
    assert result['illustrations'][0]['caption'] == figure['caption'] + '（原图）'
    assert result['illustrationReview']['status'] == 'stale'


def test_image_noop_does_not_bypass_active_task_guard(images_env):
    env = images_env
    project = accept(env)
    project = env.service.complete_illustrations(env.project_id, project['version'])
    queued = env.service.queue_image_search(env.project_id, project['version'], env.section_id, 0, '实验室设备')
    for operation in ('complete', 'picture'):
        with pytest.raises(WritingConflict):
            if operation == 'complete':
                env.service.complete_illustrations(env.project_id, project['version'])
            else:
                env.service.patch_illustration(env.project_id, project['illustrations'][0]['id'], project['version'], {'locked': False})
    assert env.store.job(queued['job']['id'])['status'] == 'queued'


def test_explicit_picture_confirmation_still_checks_asset_integrity_when_fields_match(images_env):
    env = images_env
    project = accept(env)
    figure = project['illustrations'][0]
    before = persisted(env.service, env.project_id)
    with env.store.connect() as db:
        path = db.execute('SELECT blob_path FROM assets WHERE id=?', (figure['assetId'],)).fetchone()[0]
    (env.store.root / path).write_bytes(b'damaged-isolated-test-image')
    with pytest.raises(ValueError, match='图片内容已变化'):
        env.service.patch_illustration(env.project_id, figure['id'], project['version'], {'confirmUnverified': True})
    assert persisted(env.service, env.project_id) == before
