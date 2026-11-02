"""Opt-in acceptance guard: permit mocked ranking IO, never a live service."""
import pytest

from server.model_gateway import ModelError
from server.retrieval_rerank import FALLBACK_WARNING, RerankClient


@pytest.fixture(autouse=True)
def require_explicit_rerank_transport(monkeypatch):
    score = RerankClient.score

    def controlled(self, *args, **kwargs):
        if self.transport is None:
            raise ModelError(FALLBACK_WARNING)
        return score(self, *args, **kwargs)

    monkeypatch.setattr(RerankClient, 'score', controlled)
