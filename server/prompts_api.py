"""Existing built-in prompts may be listed and edited, never added/deleted."""
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, ConfigDict, Field
from typing import Literal

from .prompts import PromptConflict


class PromptUpdate(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    content: str = Field(min_length=1, max_length=24000)
    expectedVersion: int = Field(ge=1)
    contentView: Literal['source', 'effective'] | None = None


def router(library):
    routes = APIRouter()

    @routes.get('/api/prompts')
    def list_prompts():
        return library.list()

    @routes.put('/api/prompts/{prompt_id}')
    def update_prompt(prompt_id: str, body: PromptUpdate):
        try:
            return library.update(prompt_id, body.content, body.expectedVersion, content_view=body.contentView)
        except PromptConflict as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from None

    return routes
