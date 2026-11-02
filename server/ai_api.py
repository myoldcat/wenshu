"""Local model settings and revision-bound document enrichment endpoints."""
from typing import Literal

from fastapi import APIRouter
from pydantic import BaseModel, ConfigDict, Field

from .enrichment import taxonomy


class StrictBody(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)


class ModelConfig(StrictBody):
    name: str | None = Field(None, min_length=1, max_length=100)
    baseUrl: str | None = Field(None, min_length=1, max_length=2048)
    model: str | None = Field(None, min_length=1, max_length=200)
    apiKey: str | None = Field(None, max_length=8192)
    clearApiKey: bool = False
    contextChars: int | None = Field(None, ge=2000, le=1000000)
    maxOutputTokens: int | None = Field(None, ge=128, le=65536)
    timeoutSeconds: int | None = Field(None, ge=5, le=300)
    reasoningEffort: Literal['', 'none', 'minimal', 'low', 'medium', 'high', 'xhigh', 'max'] | None = None
    jsonMode: bool | None = None


class Preferences(StrictBody):
    defaultModelId: str | None = Field(None, max_length=100)
    summaryModelId: str | None = Field(None, max_length=100)
    translationModelId: str | None = Field(None, max_length=100)
    classificationModelId: str | None = Field(None, max_length=100)
    autoProcess: bool = False
    autoTranslate: bool = True
    idleProcess: bool = False
    idleStart: str = Field('00:00', pattern=r'^(?:[01][0-9]|2[0-3]):[0-5][0-9]$')
    idleEnd: str = Field('08:00', pattern=r'^(?:[01][0-9]|2[0-3]):[0-5][0-9]$')


class WritingActivity(StrictBody):
    sessionId: str = Field(min_length=1, max_length=100, pattern=r'^[A-Za-z0-9_-]+$')
    active: bool
    sequence: int | None = Field(None, ge=0, le=9007199254740991)


class Process(StrictBody):
    kinds: list[Literal['summary', 'translation', 'classification']] | None = Field(None, min_length=1, max_length=3)
    force: bool = False
    modelId: str | None = Field(None, max_length=100)
    targetLanguage: Literal['zh-CN'] = 'zh-CN'
    revisionId: str | None = Field(None, max_length=100)


class TranslationEdit(StrictBody):
    blockId: str = Field(min_length=1, max_length=150)
    text: str = Field(min_length=1, max_length=100000)
    revisionId: str | None = Field(None, max_length=100)
    locked: bool = True


def router(models, enrichment):
    routes = APIRouter()

    @routes.get('/api/models')
    def list_models():
        return {'models': models.models(), 'preferences': models.get_preferences()}

    @routes.post('/api/models', status_code=201)
    def add_model(body: ModelConfig):
        return {'model': models.save_config(body.model_dump(exclude_unset=True, exclude_none=True))}

    @routes.put('/api/models/{model_id}')
    def update_model(model_id: str, body: ModelConfig):
        return {'model': models.save_config(body.model_dump(exclude_unset=True, exclude_none=True), id=model_id)}

    @routes.post('/api/models/{model_id}/test')
    def test_model(model_id: str):
        return {'result': models.test_connection(model_id)}

    @routes.delete('/api/models/{model_id}')
    def delete_model(model_id: str):
        return models.delete_config(model_id)

    @routes.put('/api/ai/preferences')
    def preferences(body: Preferences):
        return {'preferences': models.save_preferences(body.model_dump(exclude_unset=True))}

    @routes.get('/api/ai/preferences')
    def read_preferences():
        return {'preferences': models.get_preferences()}

    @routes.post('/api/ai/writing-activity')
    def writing_activity(body: WritingActivity):
        return enrichment.store.writing_activity(body.sessionId, body.active, body.sequence)

    @routes.get('/api/ai/taxonomy')
    def categories():
        return taxonomy()

    @routes.get('/api/documents/{source_id}/ai')
    def detail(source_id: str, revision_id: str | None = None):
        return {'ai': enrichment.detail(source_id, revision_id)}

    @routes.post('/api/documents/{source_id}/ai', status_code=202)
    def process(source_id: str, body: Process):
        return {'jobs': enrichment.queue(source_id, kinds=body.kinds, force=body.force,
                                        model_id=body.modelId, target_language=body.targetLanguage,
                                        revision_id=body.revisionId)}

    @routes.patch('/api/documents/{source_id}/ai/translation')
    def edit_translation(source_id: str, body: TranslationEdit):
        return {'ai': enrichment.edit_translation(source_id, body.blockId, body.text,
                                                 revision_id=body.revisionId, locked=body.locked)}

    return routes
