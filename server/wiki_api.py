"""Revision-bound Wiki compilation, graph exploration and evidence retrieval."""
from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field

from .graph_analytics import GraphAnalyticsUnavailable, GraphSnapshotConflict


class BuildWiki(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)

    # An explicit, small scope keeps a click from scheduling the whole corpus.
    sourceIds: list[str] = Field(min_length=1, max_length=20)
    force: bool = False
    modelId: str | None = Field(None, min_length=1, max_length=100)


class WikiBuildScope(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)

    # Null is the whole library; an explicit empty selection remains empty.
    sourceIds: list[str] | None = Field(None, max_length=10000)


def router(wiki):
    routes = APIRouter()

    @routes.get('/api/wiki')
    def overview(topic_id: str | None = Query(None, max_length=150),
                 limit: int = Query(100, ge=2, le=150)):
        return wiki.overview(topic_id=topic_id, limit=limit)

    @routes.get('/api/wiki/analytics')
    def analytics(scope: str = Query('topic', pattern='^(topic|all)$'),
                  topic_id: str | None = Query(None, min_length=1, max_length=150),
                  snapshot_id: str | None = Query(None, min_length=1, max_length=150)):
        try:
            return wiki.analytics(scope, topic_id, snapshot_id)
        except GraphSnapshotConflict as exc:
            raise HTTPException(status_code=409, detail={'reason': 'snapshot_changed',
                'currentSnapshotId': exc.current_snapshot, 'reloadRequired': True, 'message': str(exc)}) from None
        except GraphAnalyticsUnavailable as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from None

    @routes.get('/api/wiki/search')
    def search(q: str = Query('', max_length=300),
               limit: int = Query(20, ge=1, le=50),
               hops: int = Query(1, ge=0, le=2)):
        return wiki.search(q, limit=limit, hops=hops)

    @routes.get('/api/wiki/neighborhood')
    def neighborhood(entry_id: str = Query(..., min_length=1, max_length=100),
                     hops: int = Query(1, ge=0, le=2),
                     limit: int = Query(100, ge=4, le=150),
                     offset: int = Query(0, ge=0),
                     snapshot_id: str | None = Query(None, min_length=1, max_length=100)):
        return wiki.neighborhood(entry_id, hops=hops, limit=limit, offset=offset, snapshot_id=snapshot_id)

    @routes.get('/api/wiki/groups/{group_id}')
    def group(group_id: str, offset: int = Query(0, ge=0), limit: int = Query(20, ge=1, le=100)):
        return wiki.group(group_id, offset=offset, limit=limit)

    @routes.get('/api/wiki/pages/{entry_id}')
    def page(entry_id: str, version: int | None = Query(None, ge=1)):
        return wiki.page(entry_id, version=version)

    @routes.post('/api/wiki/build', status_code=202)
    def build(body: BuildWiki):
        if any(not value.strip() or len(value) > 100 for value in body.sourceIds):
            raise ValueError('文档标识不能为空且不能超过 100 字符')
        return wiki.queue(body.sourceIds, force=body.force, model_id=body.modelId)

    @routes.post('/api/wiki/build-scope')
    def build_scope(body: WikiBuildScope):
        return wiki.build_scope(body.sourceIds)

    @routes.post('/api/wiki/build-all', status_code=202)
    def build_all(body: WikiBuildScope):
        return wiki.queue_all(body.sourceIds)

    @routes.get('/api/wiki/build-all')
    def all_builds():
        return wiki.all_runs()

    @routes.get('/api/wiki/build-all/{job_id}')
    def all_build(job_id: str):
        return {'run': wiki.all_run(job_id)}

    return routes
