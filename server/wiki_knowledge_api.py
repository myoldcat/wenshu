"""Explicit canonical operations alongside the backwards-compatible source Wiki API."""
from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field

from .wiki_knowledge import KnowledgeConflict
from .graph_analytics import GraphAnalyticsUnavailable, GraphSnapshotConflict


class Resolve(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    sourceIds: list[str] = Field(min_length=1, max_length=10)
    force: bool = False
    modelId: str | None = Field(None, min_length=1, max_length=100)


class Decision(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    entryIds: list[str] = Field(min_length=2, max_length=50)
    expectedSnapshotId: str = Field(min_length=1, max_length=150)
    label: str = Field('', max_length=200)
    reason: str = Field('', max_length=1200)
    judgment: str = Field('distinct', pattern='^(distinct|related)$')
    proposalId: str | None = Field(None, max_length=150)


class Undo(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    decisionId: str = Field(min_length=1, max_length=100)
    expectedSnapshotId: str = Field(min_length=1, max_length=150)


class Synthesize(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    canonicalId: str = Field(min_length=1, max_length=100)
    force: bool = False


def router(knowledge, resolution):
    routes = APIRouter()

    @routes.get('/api/wiki/knowledge')
    def overview(topic_id: str | None = Query(None, max_length=150), limit: int = Query(100, ge=4, le=150)):
        return knowledge.overview(topic_id, limit)

    @routes.get('/api/wiki/knowledge/analytics')
    def analytics(scope: str = Query('topic', pattern='^(topic|all)$'),
                  topic_id: str | None = Query(None, min_length=1, max_length=150),
                  snapshot_id: str | None = Query(None, min_length=1, max_length=150)):
        try:
            return knowledge.analytics(scope, topic_id, snapshot_id)
        except GraphSnapshotConflict as exc:
            raise HTTPException(status_code=409, detail={'reason': 'snapshot_changed',
                'currentSnapshotId': exc.current_snapshot, 'reloadRequired': True, 'message': str(exc)}) from None
        except GraphAnalyticsUnavailable as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from None

    @routes.get('/api/wiki/knowledge/pages/{entry_id}')
    def page(entry_id: str, version: int | None = Query(None, ge=1), topic_id: str | None = Query(None, max_length=150)):
        return knowledge.page(entry_id, version, topic_id)

    @routes.get('/api/wiki/knowledge/search')
    def search(q: str = Query('', max_length=300), limit: int = Query(20, ge=1, le=50),
               hops: int = Query(1, ge=0, le=2), topic_id: str | None = Query(None, max_length=150)):
        return knowledge.search(q, limit, hops, topic_id)

    @routes.get('/api/wiki/knowledge/neighborhood')
    def neighborhood(entry_id: str = Query(..., min_length=1, max_length=100), hops: int = Query(1, ge=0, le=2),
                     limit: int = Query(100, ge=4, le=150), offset: int = Query(0, ge=0),
                     snapshot_id: str | None = Query(None, min_length=1, max_length=150),
                     topic_id: str | None = Query(None, max_length=150)):
        return knowledge.neighborhood(entry_id, hops, limit, offset, snapshot_id, topic_id)

    @routes.get('/api/wiki/resolution/proposals')
    def proposals():
        result = resolution.results()
        state = knowledge._state()
        existing = {tuple(sorted(p['entryIds'])) for p in result['proposals']}
        by_id = {p['id']: p for p in state['pages']}
        for candidate in state['canonical']['candidates']:
            ids = candidate['entryIds']
            if tuple(sorted(ids)) in existing or any(by_id[e].get('stale') for e in ids):
                continue
            entries = [{'id': e, 'sourceEntryId': e, 'label': by_id[e]['label'], 'sourceId': by_id[e]['sourceId'],
                        'sourceTitle': state['sources'][by_id[e]['sourceId']]['title']} for e in ids]
            result['proposals'].append({'id': 'lexical:' + ':'.join(ids), 'entryIds': ids, 'entries': entries,
                'label': entries[0]['label'], 'judgment': 'uncertain', 'reason': candidate['reason'],
                'snapshotId': state['canonical']['snapshotId'], 'status': 'pending', 'origin': 'lexical'})
        with knowledge.store.connect() as db:
            jobs = [knowledge.store.job_dto(row) for row in db.execute(
                "SELECT * FROM jobs WHERE kind IN ('wiki_resolve','wiki_synthesize') ORDER BY created_at DESC,id DESC LIMIT 50")]
        result.update(snapshotId=state['canonical']['snapshotId'], decisions=knowledge.decisions(), jobs=jobs)
        return result

    @routes.post('/api/wiki/resolve', status_code=202)
    def resolve(body: Resolve):
        return resolution.enqueue(body.sourceIds, force=body.force, model_id=body.modelId)

    @routes.post('/api/wiki/knowledge/synthesize', status_code=202)
    def synthesize(body: Synthesize):
        return resolution.enqueue_synthesis(body.canonicalId, force=body.force)

    def decision(action, body):
        try:
            result = knowledge.mutate(action, body.entryIds, body.expectedSnapshotId,
                                      body.label, body.reason, body.judgment)
        except (KnowledgeConflict, ValueError) as exc:
            if isinstance(exc, KnowledgeConflict) or any(word in str(exc) for word in ('快照', '已更新', '版本已')):
                raise HTTPException(status_code=409, detail=str(exc)) from None
            raise
        resolution.set_proposal_status(body.entryIds, 'confirmed' if action == 'merge'
                                       else 'related' if body.judgment == 'related' else 'rejected')
        if action == 'merge':
            state = knowledge._state()
            canonical_id = state['canonical']['entryToCanonical'][body.entryIds[0]]
            try:
                result.update(resolution.enqueue_synthesis(canonical_id))
            except ValueError as exc:
                # A valid identity decision remains useful without a model;
                # source claims and their evidence are already readable.
                result['synthesisNotice'] = str(exc)
        return result

    @routes.post('/api/wiki/knowledge/merge')
    def merge(body: Decision):
        return decision('merge', body)

    @routes.post('/api/wiki/knowledge/reject')
    def reject(body: Decision):
        return decision('reject', body)

    @routes.post('/api/wiki/knowledge/undo')
    def undo(body: Undo):
        try:
            return knowledge.mutate('undo', [], body.expectedSnapshotId, decision_id=body.decisionId)
        except ValueError as exc:
            if '已更新' in str(exc) or '快照' in str(exc):
                raise HTTPException(status_code=409, detail=str(exc)) from None
            raise

    return routes
