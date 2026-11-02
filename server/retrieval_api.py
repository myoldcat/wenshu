"""Writing-reference discovery and knowledge retrieval settings."""
from fastapi import APIRouter
from pydantic import BaseModel, ConfigDict, Field


class StrictBody(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)


class Settings(StrictBody):
    resultLimit: int | None = Field(None, ge=1, le=100)
    passagesPerSource: int | None = Field(None, ge=1, le=5)
    candidateLimit: int | None = Field(None, ge=20, le=1000)
    queryExpansion: bool | None = None
    channels: dict[str, bool] | None = None
    embeddingModelId: str | None = Field(None, min_length=1, max_length=100)
    graphHops: int | None = Field(None, ge=0, le=2)
    minimumScore: float | None = Field(None, ge=0, le=1)
    rerankEnabled: bool | None = None


class Recommend(StrictBody):
    topic: str = Field(min_length=1, max_length=1000)
    brief: dict = Field(default_factory=dict)
    sourceIds: list[str] = Field(default_factory=list, max_length=100)
    modelId: str | None = Field(None, min_length=1, max_length=100)
    queries: list[str] = Field(default_factory=list, max_length=8)


def router(retrieval):
    routes = APIRouter()

    @routes.get('/api/retrieval/settings')
    def settings():
        return {'settings': retrieval.get_settings()}

    @routes.put('/api/retrieval/settings')
    def save_settings(body: Settings):
        return {'settings': retrieval.save_settings(body.model_dump(exclude_unset=True))}

    @routes.post('/api/retrieval/recommendations')
    def recommend(body: Recommend):
        return retrieval.recommend(body.topic, brief=body.brief, source_ids=body.sourceIds,
                                   model_id=body.modelId, queries=body.queries)

    return routes
