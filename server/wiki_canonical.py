"""Durable, evidence-preserving identities above immutable source Wiki pages.

This layer never edits a source page. Automatic equivalence is deliberately
limited to fresh concepts with identical normalized names, subtypes and
definitions. Explicit decisions, input snapshots and synthesized page versions
are separate append-only records so a merge can be reviewed and undone.
"""
from __future__ import annotations

import hashlib
import itertools
import json
import re
import unicodedata

from .store import dumps, ident, now, tokenize


SCHEMA = """
CREATE TABLE IF NOT EXISTS wiki_canonical_state(
 id INTEGER PRIMARY KEY CHECK(id=1), decision_version INTEGER NOT NULL DEFAULT 0,
 generation INTEGER NOT NULL DEFAULT 0, snapshot_id TEXT NOT NULL DEFAULT ''
);
INSERT OR IGNORE INTO wiki_canonical_state(id) VALUES(1);
CREATE TABLE IF NOT EXISTS wiki_canonical_entities(
 id TEXT PRIMARY KEY, anchor_entry_id TEXT NOT NULL, created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS wiki_canonical_memberships(
 member_key TEXT PRIMARY KEY, canonical_id TEXT NOT NULL, created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS wiki_canonical_decisions(
 seq INTEGER PRIMARY KEY AUTOINCREMENT, id TEXT NOT NULL UNIQUE,
 kind TEXT NOT NULL, payload_json TEXT NOT NULL, input_snapshot TEXT NOT NULL,
 created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS wiki_canonical_snapshots(
 id TEXT PRIMARY KEY, generation INTEGER NOT NULL UNIQUE, input_hash TEXT NOT NULL,
 decision_version INTEGER NOT NULL, content_json TEXT NOT NULL, created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS wiki_canonical_pages(
 canonical_id TEXT NOT NULL, version INTEGER NOT NULL, snapshot_id TEXT NOT NULL,
 input_hash TEXT NOT NULL, content_json TEXT NOT NULL, created_at TEXT NOT NULL,
 PRIMARY KEY(canonical_id,version)
);
CREATE TABLE IF NOT EXISTS wiki_canonical_contributions(
 snapshot_id TEXT NOT NULL, canonical_id TEXT NOT NULL, entry_id TEXT NOT NULL,
 page_version INTEGER, run_id TEXT NOT NULL, revision_id TEXT NOT NULL,
 content_json TEXT NOT NULL, PRIMARY KEY(snapshot_id,entry_id)
);
CREATE TABLE IF NOT EXISTS wiki_canonical_syntheses(
 canonical_id TEXT NOT NULL, version INTEGER NOT NULL, snapshot_id TEXT NOT NULL,
 input_hash TEXT NOT NULL, frozen_json TEXT NOT NULL, content_json TEXT NOT NULL,
 created_at TEXT NOT NULL, PRIMARY KEY(canonical_id,version)
);
CREATE VIRTUAL TABLE IF NOT EXISTS wiki_canonical_search_index USING fts5(
 canonical_id UNINDEXED, title_tokens, body_tokens, tokenize='unicode61'
);
"""


def _hash(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                    separators=(',', ':')).encode()).hexdigest()


def _copy(value):
    return json.loads(dumps(value))


def _name(value):
    return re.sub(r'\s+', ' ', unicodedata.normalize('NFKC', str(value or ''))).strip().casefold()


def _pair(a, b):
    return tuple(sorted((a, b)))


def _reference(reference):
    # Annotation state is computed against current sources, never authored by a model.
    if not isinstance(reference, dict):
        raise ValueError('Wiki 综合引用格式不正确')
    return {key: value for key, value in reference.items() if key != 'stale'}


def _binding(page):
    return {'entryId': page['id'], 'version': page.get('version'),
            'runId': page.get('runId', ''), 'revisionId': page.get('revisionId', ''),
            'sourceId': page.get('sourceId', '')}


class WikiCanonicalService:
    def __init__(self, store):
        self.store = store
        with store.connect() as db:
            db.executescript(SCHEMA)

    @staticmethod
    def _table(db, name):
        return bool(db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)).fetchone())

    def _inputs(self, db, pages):
        if not isinstance(pages, list) or any(not isinstance(page, dict) or not page.get('id') for page in pages):
            raise ValueError('Wiki 统一词条输入格式不正确')
        if len({page['id'] for page in pages}) != len(pages):
            raise ValueError('Wiki 统一词条输入包含重复来源词条')
        result = sorted(_copy(pages), key=lambda page: page['id'])
        if self._table(db, 'wiki_pages'):
            live = {row['entry_id']: dict(row) for row in db.execute('''
                SELECT p.entry_id,p.version,p.run_id,p.revision_id,p.source_id,p.content_json,s.current_revision
                FROM wiki_pages p JOIN wiki_publications u ON u.run_id=p.run_id
                JOIN sources s ON s.id=p.source_id''')}
            if set(live) != {page['id'] for page in result}:
                raise ValueError('Wiki 来源已更新，请刷新统一词条后重试')
            canonical_inputs = []
            current_revisions = {row['id']: row['current_revision'] for row in db.execute('SELECT id,current_revision FROM sources')}
            for page in result:
                row = live[page['id']]
                if any(page.get(field) != row[column] for field, column in
                       [('version', 'version'), ('revisionId', 'revision_id'), ('sourceId', 'source_id')]):
                    raise ValueError('Wiki 来源版本已更新，请刷新统一词条后重试')
                # Read the immutable publication, not UI-decorated copies.
                # Cross-source navigation adds issues/relations dynamically;
                # those must not make a model worker's input snapshot differ
                # from the browser's snapshot of the same source versions.
                page = json.loads(row['content_json'])
                page['runId'] = row['run_id']
                references = (page.get('evidence', []) + [reference for section in page.get('sections', [])
                    for claim in section.get('claims', []) for reference in claim.get('evidence', [])]
                    + [reference for relation in page.get('relations', []) for reference in relation.get('evidence', [])])
                for reference in references:
                    reference['stale'] = current_revisions.get(reference.get('sourceId')) != reference.get('revisionId')
                page['stale'] = row['current_revision'] != row['revision_id'] or any(reference['stale'] for reference in references)
                page['archived'] = False
                canonical_inputs.append(page)
            result = canonical_inputs
        # Navigation previews depend on current graph projection and do not form source input.
        keys = ('id', 'sourceId', 'revisionId', 'version', 'runId', 'label', 'type', 'subtype',
                'aliases', 'summary', 'sections', 'issues', 'evidence', 'stale', 'archived')
        input_hash = _hash([{key: page.get(key) for key in keys} for page in result])
        return result, input_hash

    @staticmethod
    def _decision_rows(db):
        rows = [dict(row) for row in db.execute('SELECT * FROM wiki_canonical_decisions ORDER BY seq')]
        undone = {json.loads(row['payload_json'])['targetDecisionId'] for row in rows if row['kind'] == 'undo'}
        return rows, undone

    def decisions(self):
        with self.store.connect() as db:
            rows, undone = self._decision_rows(db)
        return [{'id': row['id'], 'kind': row['kind'], **json.loads(row['payload_json']),
                 'inputSnapshot': row['input_snapshot'], 'createdAt': row['created_at'],
                 'undone': row['id'] in undone} for row in reversed(rows)]

    @staticmethod
    def _default_synthesis(members):
        sections, issues = [], []
        for page in members:
            for section in page.get('sections', []):
                claims = []
                for claim in section.get('claims', []):
                    claims.append({**_copy(claim), 'contributions': [_binding(page)]})
                if claims:
                    sections.append({'title': section.get('title', '来源陈述'), 'sourceId': page.get('sourceId'),
                                     'entryId': page['id'], 'claims': claims})
            issues.extend(page.get('issues', []))
        if len(members) > 1:
            issues.append('多来源陈述按贡献保留；不同条件、时间或结论的差异请结合原文核对。')
        return {'sections': sections, 'issues': list(dict.fromkeys(issues)), 'mode': 'source_claims'}

    def _derive(self, db, pages, previous, persist=True):
        by_id = {page['id']: page for page in pages}
        parent = {entry_id: entry_id for entry_id in by_id}

        def root(entry_id):
            while parent[entry_id] != entry_id:
                parent[entry_id] = parent[parent[entry_id]]
                entry_id = parent[entry_id]
            return entry_id

        def component(entry_id):
            return {item for item in parent if root(item) == root(entry_id)}

        rows, undone = self._decision_rows(db)
        active = [(row, json.loads(row['payload_json'])) for row in rows
                  if row['kind'] != 'undo' and row['id'] not in undone]
        # Last explicit decision on each pair wins. Automatic equivalence never
        # overrides a rejection, including after a source page is rebuilt.
        pair_decisions = {}
        for row, payload in active:
            for a, b in itertools.combinations(payload['entryIds'], 2):
                pair_decisions[_pair(a, b)] = (row['kind'], row['seq'])
        rejected = {pair for pair, decision in pair_decisions.items() if decision[0] == 'reject'}

        def union(a, b):
            if root(a) == root(b):
                return
            if any(_pair(x, y) in rejected for x in component(a) for y in component(b)):
                return
            parent[root(b)] = root(a)

        for pair, (kind, seq) in sorted(pair_decisions.items(), key=lambda item: -item[1][1]):
            if kind == 'merge' and all(entry_id in parent for entry_id in pair):
                union(*pair)
        exact = {}
        for page in pages:
            if page.get('type') == 'concept' and not page.get('stale') and not page.get('archived') and _name(page.get('summary')):
                signature = (_name(page['label']), page.get('subtype', ''), _name(page.get('summary')))
                exact.setdefault(signature, []).append(page['id'])
        for entries in exact.values():
            for a, b in itertools.combinations(entries, 2):
                union(a, b)
        components = {}
        for entry_id in parent:
            components.setdefault(root(entry_id), []).append(entry_id)
        entities = {row['id']: row['anchor_entry_id'] for row in db.execute('SELECT * FROM wiki_canonical_entities')}
        registry = {row['member_key']: row['canonical_id'] for row in db.execute('SELECT * FROM wiki_canonical_memberships')}
        previous_groups = previous.get('groups', {}) if previous else {}
        groups, used = {}, set()
        def exact_key(members):
            values = {(_name(page.get('label')), page.get('subtype', ''), _name(page.get('summary')))
                      for page in members if page.get('type') == 'concept' and not page.get('stale')}
            return next(iter(values)) if len(values) == 1 and all(page.get('type') == 'concept' and not page.get('stale') for page in members) else None
        previous_exact = {cid: exact_key(group['members']) for cid, group in previous_groups.items() if not group.get('manual')}
        # Components retaining an old identity's anchor get first choice after a split.
        def priority(entries):
            identity = exact_key([by_id[entry_id] for entry_id in entries])
            compatible = sum(1 for cid, signature in previous_exact.items() if identity and signature == identity
                             and set(previous_groups[cid]['entryIds']) & set(entries))
            return (-compatible, -sum(1 for cid in previous_groups if entities.get(cid) in entries), tuple(entries))
        for entries in sorted(components.values(), key=priority):
            entries.sort()
            entry_set, key = set(entries), _hash(entries)
            manual = [(row, payload) for row, payload in active if row['kind'] == 'merge'
                      and set(payload['entryIds']) <= entry_set]
            manual.sort(key=lambda item: -item[0]['seq'])
            choices = [payload.get('canonicalId') for row, payload in manual]
            choices.append(registry.get(key))
            identity = exact_key([by_id[entry_id] for entry_id in entries])
            choices.extend(cid for cid, signature in previous_exact.items() if identity and signature == identity
                           and set(previous_groups[cid]['entryIds']) & entry_set)
            choices.extend(cid for cid in previous_groups if entities.get(cid) in entry_set)
            choices.extend(cid for cid, group in sorted(previous_groups.items(),
                           key=lambda item: (-len(set(item[1]['entryIds']) & entry_set), item[0]))
                           if set(group['entryIds']) & entry_set)
            choices.extend('canonical_' + _hash(entry_id)[:32] for entry_id in entries)
            canonical_id = next((cid for cid in choices if cid and cid not in used), None)
            if canonical_id is None:
                canonical_id = 'canonical_' + _hash(entries)[:32]
            used.add(canonical_id)
            if persist:
                db.execute('INSERT OR IGNORE INTO wiki_canonical_entities VALUES(?,?,?)', (canonical_id, entries[0], now()))
                db.execute('INSERT OR IGNORE INTO wiki_canonical_memberships VALUES(?,?,?)', (key, canonical_id, now()))
            members = [by_id[entry_id] for entry_id in entries]
            label = next((payload['canonicalLabel'] for row, payload in manual if payload.get('canonicalLabel')), members[0]['label'])
            groups[canonical_id] = {'id': canonical_id, 'label': label, 'type': members[0]['type'],
                'subtype': members[0].get('subtype', ''), 'members': members, 'entryIds': entries,
                'sourceIds': sorted({page['sourceId'] for page in members}),
                'manual': bool(manual), 'stale': any(page.get('stale') for page in members),
                'synthesis': self._default_synthesis(members)}
        mapping = {entry_id: cid for cid, group in groups.items() for entry_id in group['entryIds']}
        candidates = []
        # Bounded, deterministic lexical suggestions are never equivalence decisions.
        by_name = {}
        for page in pages:
            for value in [page.get('label', ''), *page.get('aliases', [])]:
                if _name(value):
                    by_name.setdefault((_name(value), page['type'], page.get('subtype', '')), set()).add(page['id'])
        seen = set()
        for entries in by_name.values():
            for a, b in itertools.combinations(sorted(entries), 2):
                if mapping[a] != mapping[b] and (a, b) not in rejected and (a, b) not in seen:
                    candidates.append({'entryIds': [a, b], 'reason': '名称或别名相同，定义及对象身份仍需核对', 'status': 'pending'})
                    seen.add((a, b))
                if len(candidates) >= 200:
                    break
            if len(candidates) >= 200:
                break
        return groups, mapping, candidates, [list(pair) for pair in sorted(rejected)]

    def _sync(self, db, pages, persist=True):
        pages, input_hash = self._inputs(db, pages)
        state = db.execute('SELECT * FROM wiki_canonical_state WHERE id=1').fetchone()
        previous_row = db.execute('SELECT * FROM wiki_canonical_snapshots WHERE id=?', (state['snapshot_id'],)).fetchone()
        previous = json.loads(previous_row['content_json']) if previous_row else None
        if previous_row and previous_row['input_hash'] == input_hash and previous_row['decision_version'] == state['decision_version']:
            return self._with_synthesis(db, previous)
        generation = state['generation'] + 1
        snapshot_id = 'canonical_snapshot_' + _hash([input_hash, state['decision_version'], generation])[:32]
        groups, mapping, candidates, rejected = self._derive(db, pages, previous, persist=persist)
        for canonical_id, group in groups.items():
            group_hash = _hash({key: group[key] for key in ('label', 'type', 'subtype', 'members', 'manual')})
            last = db.execute('SELECT * FROM wiki_canonical_pages WHERE canonical_id=? ORDER BY version DESC LIMIT 1', (canonical_id,)).fetchone()
            version = last['version'] if last and last['input_hash'] == group_hash else (last['version'] + 1 if last else 1)
            group['version'] = version
            group['inputHash'] = group_hash
            # A synthesis belongs to precisely these member bindings and label.
            synth = db.execute('SELECT * FROM wiki_canonical_syntheses WHERE canonical_id=? AND input_hash=? ORDER BY version DESC LIMIT 1', (canonical_id, group_hash)).fetchone()
            if synth:
                group['synthesis'] = {**json.loads(synth['content_json']), 'version': synth['version'], 'mode': 'grounded_synthesis'}
            if persist and (not last or last['input_hash'] != group_hash):
                db.execute('INSERT INTO wiki_canonical_pages VALUES(?,?,?,?,?,?)',
                    (canonical_id, version, snapshot_id, group_hash, dumps(group), now()))
            for page in group['members'] if persist else []:
                db.execute('''INSERT INTO wiki_canonical_contributions
                    (snapshot_id,canonical_id,entry_id,page_version,run_id,revision_id,content_json)
                    VALUES(?,?,?,?,?,?,?)''',
                    (snapshot_id, canonical_id, page['id'], page.get('version'), page.get('runId', ''), page.get('revisionId', ''), dumps(page)))
        result = {'snapshotId': snapshot_id, 'generation': generation, 'decisionVersion': state['decision_version'],
                  'groups': groups, 'entryToCanonical': mapping, 'candidates': candidates, 'rejectedPairs': rejected}
        if persist:
            db.execute('DELETE FROM wiki_canonical_search_index')
            for group in groups.values():
                self._index(db, group)
            db.execute('INSERT INTO wiki_canonical_snapshots VALUES(?,?,?,?,?,?)',
                (snapshot_id, generation, input_hash, state['decision_version'], dumps(result), now()))
            db.execute('UPDATE wiki_canonical_state SET generation=?,snapshot_id=? WHERE id=1', (generation, snapshot_id))
        return result

    @staticmethod
    def _index(db, group):
        aliases = [alias for page in group['members'] for alias in page.get('aliases', [])]
        body = ' '.join(claim['text'] for section in group['synthesis']['sections'] for claim in section['claims'])
        db.execute('DELETE FROM wiki_canonical_search_index WHERE canonical_id=?', (group['id'],))
        db.execute('INSERT INTO wiki_canonical_search_index VALUES(?,?,?)',
                   (group['id'], tokenize(' '.join([group['label'], *aliases])), tokenize(body)))

    @staticmethod
    def _with_synthesis(db, snapshot):
        # Synthesis publication must not mutate the immutable source snapshot.
        result = _copy(snapshot)
        for canonical_id, group in result['groups'].items():
            row = db.execute('SELECT * FROM wiki_canonical_syntheses WHERE canonical_id=? AND input_hash=? ORDER BY version DESC LIMIT 1',
                             (canonical_id, group['inputHash'])).fetchone()
            if row:
                group['synthesis'] = {**json.loads(row['content_json']), 'version': row['version']}
            last = db.execute('SELECT version FROM wiki_canonical_pages WHERE canonical_id=? AND input_hash=? ORDER BY version DESC LIMIT 1',
                              (canonical_id, group['inputHash'])).fetchone()
            if last:
                group['version'] = last['version']
        return result

    def snapshot(self, pages, db=None, readonly=False):
        if db is not None:
            return self._sync(db, pages, persist=not readonly)
        with self.store.connect() as db:
            # Analytics must be usable before the first overview without
            # publishing canonical pages, memberships, snapshots or FTS rows.
            db.execute('BEGIN' if readonly else 'BEGIN IMMEDIATE')
            return self._sync(db, pages, persist=not readonly)

    @staticmethod
    def _expected(snapshot, expected):
        expected = expected.get('snapshotId') if isinstance(expected, dict) else expected
        if not isinstance(expected, str) or not expected or snapshot['snapshotId'] != expected:
            raise ValueError('Wiki 统一词条快照已更新，请刷新后重试')

    @staticmethod
    def _entries(entry_ids, snapshot):
        if (not isinstance(entry_ids, list) or not 2 <= len(entry_ids) <= 50
                or any(not isinstance(item, str) or item not in snapshot['entryToCanonical'] for item in entry_ids)
                or len(set(entry_ids)) != len(entry_ids)):
            raise ValueError('Wiki 归并或拒绝须选择 2 至 50 个不同的当前来源词条')
        return sorted(entry_ids)

    def _decision(self, kind, entry_ids, expected_snapshot, pages, canonical_label='', reason=''):
        if not isinstance(reason, str) or len(reason) > 1200:
            raise ValueError('Wiki 归并理由不能超过 1200 字符')
        with self.store.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            current = self._sync(db, pages)
            self._expected(current, expected_snapshot)
            entries = self._entries(entry_ids, current)
            groups = [current['groups'][current['entryToCanonical'][entry_id]] for entry_id in entries]
            if kind == 'merge' and len({(group['type'], group['subtype']) for group in groups}) > 1:
                raise ValueError('Wiki 不同类型的词条不能合并，请保留关联')
            if kind == 'merge' and (not isinstance(canonical_label, str) or not canonical_label.strip() or len(canonical_label) > 200):
                raise ValueError('Wiki 统一名称须为 1 至 200 字符')
            decision_id = ident('canonical_decision')
            payload = {'entryIds': entries, 'reason': reason.strip(),
                       'bindings': [_binding(page) for group in current['groups'].values() for page in group['members'] if page['id'] in entries]}
            if kind == 'merge':
                payload.update(canonicalLabel=canonical_label.strip(), canonicalId=current['entryToCanonical'][entries[0]])
            db.execute('INSERT INTO wiki_canonical_decisions(id,kind,payload_json,input_snapshot,created_at) VALUES(?,?,?,?,?)',
                       (decision_id, kind, dumps(payload), current['snapshotId'], now()))
            db.execute('UPDATE wiki_canonical_state SET decision_version=decision_version+1 WHERE id=1')
            result = self._sync(db, pages)
            return {**result, 'decisionId': decision_id, 'decision': {'id': decision_id, 'kind': kind, **payload, 'undone': False}}

    def merge(self, entry_ids, canonical_label, expected_snapshot, pages, reason=''):
        return self._decision('merge', entry_ids, expected_snapshot, pages, canonical_label, reason)

    def reject(self, entry_ids, expected_snapshot, pages, reason=''):
        return self._decision('reject', entry_ids, expected_snapshot, pages, reason=reason)

    def undo(self, decision_id, expected_snapshot, pages):
        with self.store.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            current = self._sync(db, pages)
            self._expected(current, expected_snapshot)
            row = db.execute('SELECT * FROM wiki_canonical_decisions WHERE id=?', (decision_id,)).fetchone()
            rows, undone = self._decision_rows(db)
            if not row or row['kind'] == 'undo' or decision_id in undone:
                raise ValueError('Wiki 归并决策不存在或已经撤销')
            payload = {'targetDecisionId': decision_id, 'entryIds': json.loads(row['payload_json'])['entryIds']}
            undo_id = ident('canonical_decision')
            db.execute('INSERT INTO wiki_canonical_decisions(id,kind,payload_json,input_snapshot,created_at) VALUES(?,?,?,?,?)',
                       (undo_id, 'undo', dumps(payload), current['snapshotId'], now()))
            db.execute('UPDATE wiki_canonical_state SET decision_version=decision_version+1 WHERE id=1')
            return {**self._sync(db, pages), 'decisionId': undo_id, 'decision': {'id': undo_id, 'kind': 'undo', **payload}}

    def page(self, canonical_id, pages, version=None):
        current = self.snapshot(pages)
        group = current['groups'].get(canonical_id)
        archived = canonical_id not in current['groups']
        with self.store.connect() as db:
            if version is not None:
                if isinstance(version, bool) or not isinstance(version, int) or version < 1:
                    raise ValueError('Wiki 统一词条版本须为正整数')
                row = db.execute('SELECT * FROM wiki_canonical_pages WHERE canonical_id=? AND version=?', (canonical_id, version)).fetchone()
                if not row:
                    raise KeyError(canonical_id)
                group = json.loads(row['content_json'])
            elif group is None:
                row = db.execute('SELECT * FROM wiki_canonical_pages WHERE canonical_id=? ORDER BY version DESC LIMIT 1', (canonical_id,)).fetchone()
                if row:
                    group = json.loads(row['content_json'])
            if group is None:
                raise KeyError(canonical_id)
            group = _copy(group)
            revisions = {row['id']: row['current_revision'] for row in db.execute('SELECT id,current_revision FROM sources')}
            for member in group['members']:
                member['stale'] = revisions.get(member.get('sourceId')) != member.get('revisionId')
                for section in member.get('sections', []):
                    for claim in section.get('claims', []):
                        for reference in claim.get('evidence', []):
                            reference['stale'] = revisions.get(reference.get('sourceId')) != reference.get('revisionId')
            group['stale'] = any(member['stale'] for member in group['members'])
            references = [reference for member in group['members'] for reference in member.get('evidence', [])]
            references.extend(reference for section in group['synthesis']['sections']
                              for claim in section['claims'] for reference in claim['evidence'])
            for reference in references:
                reference['stale'] = revisions.get(reference.get('sourceId')) != reference.get('revisionId')
            history = [{'version': row['version'], 'snapshotId': row['snapshot_id'], 'createdAt': row['created_at']}
                       for row in db.execute('SELECT * FROM wiki_canonical_pages WHERE canonical_id=? ORDER BY version DESC', (canonical_id,))]
        redirects = {current['entryToCanonical'][entry_id] for entry_id in group['entryIds'] if entry_id in current['entryToCanonical']}
        archived = archived or (version is not None and version != current['groups'][canonical_id]['version'])
        return {**group, 'snapshotId': current['snapshotId'], 'generation': current['generation'],
                'history': history, 'archived': archived,
                'redirectId': next(iter(redirects)) if archived and len(redirects) == 1 and canonical_id not in redirects else None}

    def _validate_evidence(self, db, members, evidence):
        sources = {page.get('sourceId'): page.get('revisionId') for page in members}
        for reference in evidence:
            if (not isinstance(reference, dict) or not isinstance(reference.get('start'), int)
                    or isinstance(reference.get('start'), bool) or not isinstance(reference.get('end'), int)
                    or isinstance(reference.get('end'), bool) or reference['start'] < 0
                    or reference['end'] <= reference['start'] or not reference.get('quote')
                    or sources.get(reference.get('sourceId')) != reference.get('revisionId') or reference.get('stale')):
                raise ValueError('Wiki 综合引用已失效或格式不正确')
            row = db.execute('SELECT b.text,r.source_id,s.current_revision FROM blocks b JOIN revisions r ON r.id=b.revision_id JOIN sources s ON s.id=r.source_id WHERE b.id=? AND b.revision_id=?',
                             (reference.get('blockId'), reference.get('revisionId'))).fetchone()
            if (not row or row['source_id'] != reference['sourceId'] or row['current_revision'] != reference['revisionId']
                    or row['text'][reference['start']:reference['end']] != reference['quote']):
                raise ValueError('Wiki 综合引用已失效，请刷新来源')

    def prepare_synthesis(self, canonical_id, pages):
        with self.store.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            current = self._sync(db, pages)
            group = current['groups'].get(canonical_id)
            if not group:
                raise KeyError(canonical_id)
            if group['stale']:
                raise ValueError('Wiki 来源版本已更新，请重新构建来源词条')
            evidence = {}
            for page in group['members']:
                for section in page.get('sections', []):
                    for claim in section.get('claims', []):
                        for reference in claim.get('evidence', []):
                            reference = _reference(reference)
                            evidence[_hash(reference)] = reference
            self._validate_evidence(db, group['members'], list(evidence.values()))
            latest = db.execute('SELECT max(version) FROM wiki_canonical_syntheses WHERE canonical_id=?', (canonical_id,)).fetchone()[0] or 0
            return {'canonicalId': canonical_id, 'snapshotId': current['snapshotId'], 'generation': current['generation'],
                    'decisionVersion': current['decisionVersion'], 'inputHash': group['inputHash'],
                    'expectedSynthesisVersion': latest, 'label': group['label'],
                    'members': [_binding(page) for page in group['members']], 'evidence': list(evidence.values()),
                    'sections': self._default_synthesis(group['members'])['sections']}

    def publish_synthesis(self, canonical_id, frozen, content, pages, job_id=None, attempt=None):
        if not isinstance(frozen, dict) or not isinstance(content, dict):
            raise ValueError('Wiki 综合内容格式不正确')
        with self.store.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            from .year_context import (POLICY_VERSION as YEAR_CONTEXT_POLICY_VERSION,
                                       load_contexts as load_year_contexts)
            policy = frozen.get('yearContextPolicyVersion')
            if policy is not None and policy != YEAR_CONTEXT_POLICY_VERSION:
                raise ValueError('Wiki 年份上下文策略不受支持，未发布')
            from .wiki_pruning import POLICY_VERSION as SELECTION_POLICY_VERSION
            selection = frozen.get('selectionPolicyVersion')
            if selection is not None and (selection != SELECTION_POLICY_VERSION
                    or not isinstance(frozen.get('selectionInstructions'), str) or not frozen['selectionInstructions'].strip()):
                raise ValueError('Wiki 综合明确性策略冻结信息不受支持，未发布')
            current = self._sync(db, pages)
            self._expected(current, frozen.get('snapshotId'))
            group = current['groups'].get(canonical_id)
            if (not group or frozen.get('canonicalId') != canonical_id or frozen.get('inputHash') != group['inputHash']
                    or frozen.get('decisionVersion') != current['decisionVersion']
                    or frozen.get('members') != [_binding(page) for page in group['members']]):
                raise ValueError('Wiki 综合输入已更新，请重新生成')
            latest = db.execute('SELECT max(version) FROM wiki_canonical_syntheses WHERE canonical_id=?', (canonical_id,)).fetchone()[0] or 0
            if latest != frozen.get('expectedSynthesisVersion'):
                raise ValueError('Wiki 综合页面已有更新，请刷新后重试')
            if job_id is not None:
                job = db.execute('SELECT status,attempts FROM jobs WHERE id=?', (job_id,)).fetchone()
                if not job or job['status'] != 'running' or job['attempts'] != attempt:
                    raise ValueError('Wiki 综合任务已取消或已被替代')
                if self._table(db, 'wiki_resolution_runs') and db.execute('''SELECT 1 FROM wiki_resolution_runs cur
                    JOIN wiki_resolution_runs old ON old.job_id=? WHERE cur.kind=old.kind
                    AND cur.target_id=old.target_id AND cur.seq>old.seq LIMIT 1''', (job_id,)).fetchone():
                    raise ValueError('Wiki 综合任务已由更新任务替代')
            registered = {_hash(_reference(reference)) for reference in frozen.get('evidence', [])}
            # Rebuild the registry from current claims as well as the frozen
            # input: callers cannot mint provenance by editing a frozen object.
            live_registered = {_hash(_reference(reference)) for page in group['members']
                               for section in page.get('sections', []) for claim in section.get('claims', [])
                               for reference in claim.get('evidence', [])}
            if not registered <= live_registered:
                raise ValueError('Wiki 综合引用不属于冻结来源')
            sections = content.get('sections')
            if not isinstance(sections, list) or len(sections) > 30 or (not selection and not sections):
                raise ValueError('Wiki 综合须包含 1 至 30 个章节')
            if selection:
                # These are program-normalized claims, including reused new-
                # policy checkpoints. Recheck each exact original in the same
                # transaction as the membership/version/cancellation fences.
                sanitized = []
                for section in sections:
                    if (not isinstance(section, dict) or not isinstance(section.get('title'), str)
                            or not section['title'].strip() or len(section['title']) > 200
                            or not isinstance(section.get('claims'), list) or len(section['claims']) > 60):
                        continue
                    claims = []
                    for claim in section['claims']:
                        if (not isinstance(claim, dict) or ('clear' in claim and claim['clear'] is not True)
                                or not isinstance(claim.get('text'), str) or not claim['text'].strip()
                                or len(claim['text']) > 4000 or not isinstance(claim.get('evidence'), list)
                                or not 1 <= len(claim['evidence']) <= 20):
                            continue
                        try:
                            evidence = [_reference(reference) for reference in claim['evidence']]
                            if any(_hash(reference) not in registered for reference in evidence):
                                continue
                            from .wiki import _check_years
                            contexts = load_year_contexts(self.store, evidence, db=db) if policy else None
                            _check_years(claim['text'], evidence, contexts)
                            self._validate_evidence(db, group['members'], evidence)
                        except ValueError:
                            continue
                        claims.append({'text': claim['text'].strip(), 'evidence': evidence})
                    if not claims:
                        continue
                    try:
                        evidence = [reference for claim in claims for reference in claim['evidence']]
                        contexts = load_year_contexts(self.store, evidence, db=db) if policy else None
                        _check_years(section['title'], evidence, contexts)
                    except ValueError:
                        continue
                    sanitized.append({'title': section['title'].strip(), 'claims': claims})
                if not sanitized:
                    # An evaluated empty result is a successful no-op. It must
                    # not replace the prior synthesis or its search projection.
                    return {'sections': [], 'issues': [], 'empty': True, 'canonicalId': canonical_id,
                            'version': latest, 'snapshotId': current['snapshotId'], 'inputHash': group['inputHash']}
                sections = sanitized
            sanitized, all_evidence = [], []
            for section in sections:
                if (not isinstance(section, dict) or not isinstance(section.get('title'), str)
                        or not section['title'].strip() or len(section['title']) > 200
                        or not isinstance(section.get('claims'), list) or not 1 <= len(section['claims']) <= 60):
                    raise ValueError('Wiki 综合章节格式不正确')
                claims = []
                for claim in section['claims']:
                    if (not isinstance(claim, dict) or not isinstance(claim.get('text'), str) or not claim['text'].strip()
                            or len(claim['text']) > 4000 or not isinstance(claim.get('evidence'), list)
                            or not 1 <= len(claim['evidence']) <= 20):
                        raise ValueError('Wiki 综合陈述必须携带原文证据')
                    evidence = [_reference(reference) for reference in claim['evidence']]
                    if any(_hash(reference) not in registered for reference in evidence):
                        raise ValueError('Wiki 综合引用不属于冻结来源')
                    from .wiki import _check_years
                    contexts = load_year_contexts(self.store, evidence, db=db) if policy else None
                    _check_years(claim['text'], evidence, contexts)
                    claims.append({'text': claim['text'].strip(), 'evidence': evidence})
                    all_evidence.extend(evidence)
                sanitized.append({'title': section['title'].strip(), 'claims': claims})
                section_evidence = [reference for claim in claims for reference in claim['evidence']]
                contexts = load_year_contexts(self.store, section_evidence, db=db) if policy else None
                _check_years(section['title'], section_evidence, contexts)
            self._validate_evidence(db, group['members'], all_evidence)
            issues = [] if selection else content.get('issues', [])
            if not isinstance(issues, list) or any(not isinstance(issue, str) or len(issue) > 1200 for issue in issues) or len(issues) > 30:
                raise ValueError('Wiki 综合待核对信息格式不正确')
            clean = {'sections': sanitized, 'issues': issues, 'mode': 'grounded_synthesis'}
            db.execute('INSERT INTO wiki_canonical_syntheses VALUES(?,?,?,?,?,?,?)',
                (canonical_id, latest + 1, current['snapshotId'], group['inputHash'], dumps(frozen), dumps(clean), now()))
            page_version = db.execute('SELECT max(version) FROM wiki_canonical_pages WHERE canonical_id=?', (canonical_id,)).fetchone()[0] + 1
            published = {**group, 'version': page_version, 'synthesis': {**clean, 'version': latest + 1}}
            db.execute('INSERT INTO wiki_canonical_pages VALUES(?,?,?,?,?,?)',
                       (canonical_id, page_version, current['snapshotId'], group['inputHash'], dumps(published), now()))
            self._index(db, published)
            if job_id is not None:
                db.execute("UPDATE jobs SET status='succeeded',stage='ready',error=NULL,updated_at=? WHERE id=? AND status='running' AND attempts=?",
                           (now(), job_id, attempt))
            # The source snapshot remains immutable; this is a separately
            # versioned artifact published against its frozen source bindings.
            return {**clean, 'canonicalId': canonical_id, 'version': latest + 1,
                    'snapshotId': current['snapshotId'], 'inputHash': group['inputHash']}

    def get_synthesis(self, canonical_id, pages):
        current = self.snapshot(pages)
        group = current['groups'].get(canonical_id)
        if not group:
            raise KeyError(canonical_id)
        with self.store.connect() as db:
            row = db.execute('SELECT * FROM wiki_canonical_syntheses WHERE canonical_id=? AND input_hash=? ORDER BY version DESC LIMIT 1',
                             (canonical_id, group['inputHash'])).fetchone()
        return ({**json.loads(row['content_json']), 'version': row['version'], 'canonicalId': canonical_id,
                 'snapshotId': row['snapshot_id']} if row else _copy(group['synthesis']))
