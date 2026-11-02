"""The explicit citation-only action uses the saved-version API contract."""
from fastapi import FastAPI
from fastapi.testclient import TestClient

from server.writing import WritingConflict
from server.writing_api import router


class CitationQueue:
    def __init__(self):
        self.calls = []

    def queue_citation_match(self, project_id, expected):
        self.calls.append((project_id, expected))
        if expected != 7:
            raise WritingConflict('文稿已更新，请重新载入')
        return {'runId': 'matching-run', 'job': {'id': 'matching-job', 'status': 'queued'}}


def client():
    service = CitationQueue()
    app = FastAPI()
    app.include_router(router(service))
    return TestClient(app), service


def test_match_citations_queues_only_the_explicit_saved_version():
    http, service = client()
    response = http.post('/api/writing/projects/project-one/match-citations', json={'expectedVersion': 7})
    assert response.status_code == 202
    assert response.json()['runId'] == 'matching-run'
    assert service.calls == [('project-one', 7)]


def test_match_citations_does_not_retry_or_override_version_conflicts():
    http, service = client()
    response = http.post('/api/writing/projects/project-one/match-citations', json={'expectedVersion': 6})
    assert response.status_code == 409
    assert service.calls == [('project-one', 6)]


def test_match_citations_refuses_body_rewrites_and_missing_or_coerced_versions():
    for payload in ({}, {'expectedVersion': '7'}, {'expectedVersion': 7, 'content': 'replacement'}):
        http, service = client()
        response = http.post('/api/writing/projects/project-one/match-citations', json=payload)
        assert response.status_code == 422
        assert not service.calls
