"""Structure/organization overrides and fixed rule display use isolated data."""
import copy

import pytest

from server.prompts import PromptLibrary, REGISTRY
from server.prompt_supplements import extra_catalog_entries, supplemental_rules
from server.store import Store
from server import writing_composition as composition
from server import writing_preferences as preferences


def update(store, id, text):
    library = PromptLibrary(store)
    current = next(item for item in library.list()['items'] if item['id'] == id)
    return library.update(id, text, current['version'])


def test_all_existing_structures_and_organization_defaults_are_registered_exactly():
    entries = {item['id']: item for item in extra_catalog_entries()}
    assert len(entries) == 16
    for key, (_name, _parts, text) in preferences.STRUCTURES.items():
        entry = entries['writing_structure_' + key]
        assert entry['content'] == text and entry['category'] == 'structures'
    assert entries['writing_composition_outline']['content'] == composition.OUTLINE_INSTRUCTIONS
    assert entries['writing_composition_prose']['content'] == composition.PROSE_INSTRUCTIONS
    for key, text in composition._ROLES.items():
        assert entries['writing_role_' + key]['content'] == text
        assert entries['writing_role_' + key]['maxContentLength'] == 180
    assert all(REGISTRY[id]['content'] == entry['content'] for id, entry in entries.items())


@pytest.mark.parametrize('structure', preferences.STRUCTURES)
def test_selected_structure_override_freezes_text_without_changing_counts_or_titles(tmp_path, structure):
    store = Store(tmp_path / structure)
    brief = {'structureTemplate': structure, 'templateSections': ['自定第一部分', '自定第二部分']}
    original = preferences.snapshot(brief)
    before = copy.deepcopy(original)
    id = 'writing_structure_' + structure
    text = REGISTRY[id]['content'] + '\n隔离结构验收标记_' + structure
    saved = update(store, id, text)
    with store.connect() as db:
        current = preferences.snapshot(brief, db=db)
    assert current['structure']['instructions'].startswith(text)
    assert current['structurePromptSnapshot'] == {id: {'content': text, 'version': saved['version']}}
    assert current['structure']['sections'] == original['structure']['sections']
    assert current.get('outlineTemplateRequirement') == original.get('outlineTemplateRequirement')
    assert current['authorStructureInstructions'] == preferences.AUTHOR_STRUCTURE_INSTRUCTIONS
    assert current['authorIntroductionRequirement'] == preferences.AUTHOR_INTRODUCTION_REQUIREMENT
    packet = {'requirements': '现有要求', 'outputSchema': {}}
    system = preferences.apply('', packet, current)
    assert text in system and 'structurePromptSnapshot' not in packet['writingPreferences']
    assert original == before
    update(store, id, REGISTRY[id]['content'] + '\n后续版本不能替换旧快照')
    assert text in current['structure']['instructions'] and original == before
    with store.connect() as db:
        assert db.execute('SELECT count(*) FROM jobs').fetchone()[0] == 0
        assert db.execute('SELECT count(*) FROM sources').fetchone()[0] == 0


@pytest.mark.parametrize('id', ['writing_composition_outline', 'writing_composition_prose',
                               *['writing_role_' + role for role in composition._ROLES]])
def test_organization_override_keeps_fixed_boundaries_and_default_budget(tmp_path, id):
    store = Store(tmp_path / id)
    original = composition.snapshot()
    before = copy.deepcopy(original)
    text = REGISTRY[id]['content'] + '\n组织验收标记'
    saved = update(store, id, text)
    with store.connect() as db:
        current = composition.snapshot(db=db)
    assert current['compositionPromptSnapshot'][id] == {'content': text, 'version': saved['version']}
    field = ('outlineInstructions' if id == 'writing_composition_outline' else
             'proseInstructions' if id == 'writing_composition_prose' else None)
    actual = current[field] if field else current['roleInstructions'][id[len('writing_role_'):]]
    assert actual.startswith(text)
    if field is None:
        assert actual == text  # no fixed contract is silently cut inside the role slot
    assert current['strictInstructions'] == composition.STRICT_INSTRUCTIONS
    assert current['contextBoundary'] == composition.CONTEXT_BOUNDARY
    assert (current['contextLimit'], current['previousTailChars'], current['openingChars']) == (1200, 480, 160)
    assert original == before and composition.snapshot() == before


@pytest.mark.parametrize('role', composition._ROLES)
def test_role_limit_matches_existing_180_character_context_slot(tmp_path, role):
    store = Store(tmp_path / role)
    id = 'writing_role_' + role
    text = '角' * 180
    update(store, id, text)
    with pytest.raises(ValueError):
        update(store, id, text + '超')
    with store.connect() as db:
        current = composition.snapshot(db=db)
    count = 1 if role == 'whole' else 3
    index = {'whole': 0, 'opening': 0, 'development': 1, 'closing': 2}[role]
    project = {'title': '合成主题', 'sections': [
        {'id': str(i), 'title': '章', 'goal': '目标', 'content': ''} for i in range(count)]}
    context = composition.chapter_context(project, project['sections'][index], current)
    assert context['roleInstruction'] == text


def test_fixed_rules_show_current_semantics_and_scope_historical_compatibility():
    from server.year_context import INSTRUCTIONS as years
    from server.prompts import ARTICLE_TYPE_CONTRACT
    from server.writing_citation_matching import SELECTIVE_REVIEW_INSTRUCTION, TEMPORAL_REVIEW_INSTRUCTION
    from server.writing_outline_planning import ABSTRACT_OUTLINE_REQUIREMENT
    rules = supplemental_rules('writing_outline_strict')
    assert any(item['content'] == ABSTRACT_OUTLINE_REQUIREMENT and item['scope'] == 'current' for item in rules)
    assert all('历史任务兼容' in item['title'] for item in rules if item['scope'] == 'legacy')
    for id in ('wiki_compile', 'wiki_repair', 'wiki_synthesize'):
        assert any(item['content'] == years for item in supplemental_rules(id))
    assert {SELECTIVE_REVIEW_INSTRUCTION, TEMPORAL_REVIEW_INSTRUCTION}.issubset(
        {item['content'] for item in supplemental_rules('writing_citation_match')})
    assert any(item['content'] == ARTICLE_TYPE_CONTRACT for item in supplemental_rules('classification'))
    assert supplemental_rules('not_registered') == []


def test_legacy_snapshots_are_not_backfilled_when_rendering_requests():
    frozen = preferences.snapshot({'structureTemplate': 'opinion'})
    frozen.pop('structurePromptSnapshot')
    frozen['structure']['instructions'] = '旧任务已经冻结的方向'
    frozen['sectionSystemSuffix'] = '旧任务已经冻结的完整附加指令'
    before = copy.deepcopy(frozen)
    result = preferences.apply('', {'requirements': '', 'outputSchema': {}}, frozen,
                               section={'id': 'old'}, sections=[{'id': 'old'}])
    assert result == before['sectionSystemSuffix'] and frozen == before
