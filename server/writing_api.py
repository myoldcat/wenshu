"""Local writing API; all mutable requests carry a project base version."""
from typing import Literal
from urllib.parse import quote

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import Response
from pydantic import BaseModel, ConfigDict, Field

from .writing import WritingConflict
from .writing_pdf import PDFExportError
from .writing_review import review_requires_draft


class Body(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)


class MaterialPassage(Body):
    sourceId: str = Field(min_length=1, max_length=100)
    revisionId: str = Field(min_length=1, max_length=100)
    blockId: str = Field(min_length=1, max_length=100)


class MaterialSelection(Body):
    recommendationId: str | None = Field(None, min_length=1, max_length=100)
    passages: list[MaterialPassage] = Field(default_factory=list, max_length=1000)
    queries: list[str] = Field(default_factory=list, max_length=8)
    items: list[dict] = Field(default_factory=list, max_length=100)
    userSourceIds: list[str] = Field(default_factory=list, max_length=100)
    confirmed: bool = False
    assistanceIds: list[str] = Field(default_factory=list, max_length=100)


class CreateProject(Body):
    title: str = Field('', max_length=200)
    brief: dict = Field(default_factory=dict)
    mode: Literal['strict', 'extended'] = 'strict'
    modelId: str | None = Field(None, min_length=1, max_length=100)
    sourceIds: list[str] = Field(default_factory=list, max_length=100)
    materialSelection: MaterialSelection | None = None


class Version(Body):
    expectedVersion: int = Field(ge=1)


class PatchProject(Version):
    title: str | None = Field(None, min_length=1, max_length=200)
    brief: dict | None = None
    mode: Literal['strict', 'extended'] | None = None
    modelId: str | None = Field(None, min_length=1, max_length=100)
    sourceIds: list[str] | None = Field(None, max_length=100)
    materialSelection: MaterialSelection | None = None


class SectionOutline(Body):
    id: str | None = Field(None, min_length=1, max_length=100)
    title: str = Field(min_length=1, max_length=200)
    goal: str = Field('', max_length=2000)
    mode: Literal['strict', 'extended', 'inherit'] = 'inherit'
    targetWords: int = Field(600, ge=100, le=5000)


class Outline(Version):
    sections: list[SectionOutline] = Field(min_length=1, max_length=20)


class SectionEdit(Version):
    content: str | None = Field(None, max_length=60000)
    locked: bool | None = None
    mode: Literal['strict', 'extended', 'inherit'] | None = None


class Apply(Version):
    replaceManual: bool = False


class GenerateDocument(Version):
    confirmOutline: bool = False
    remainingFromSectionId: str | None = Field(None, min_length=1, max_length=100)


class AutomaticInterrupt(Version):
    runId: str = Field(min_length=1, max_length=100)


class ExportDocument(Version):
    format: Literal['docx', 'html', 'pdf'] = 'docx'
    allowIssues: bool = False


class ReviewConfirm(Version):
    issueIds: list[str] | None = Field(None, max_length=2000)
    all: bool = False


class ReviewExport(Version):
    format: Literal['html', 'json'] = 'html'


class WritingAssist(Body):
    topic: str = Field(min_length=1, max_length=1000)
    purpose: str = Field('', max_length=6000)
    title: str = Field('', max_length=200)
    targetWords: int = Field(6000, ge=100, le=100000)
    modelId: str | None = Field(None, min_length=1, max_length=100)
    candidateCount: int | None = Field(None, ge=3, le=3)
    structureTemplate: str | None = Field(None, max_length=100)
    writingStyle: str | None = Field(None, max_length=100)


class Selection(Body):
    start: int = Field(ge=0, le=120000)
    end: int = Field(ge=1, le=120000)
    selectedExact: str = Field(min_length=1, max_length=20000)
    unit: Literal['utf16']


class ImageSearch(Version):
    sectionId: str = Field(min_length=1, max_length=100)
    paragraphIndex: int = Field(0, ge=0, le=60000)
    query: str = Field('', max_length=500)
    modelId: str | None = Field(None, min_length=1, max_length=100)
    searchScope: Literal['references', 'library'] = 'references'
    placementMode: Literal['paragraph', 'chapter'] = 'paragraph'


class AddIllustration(Version):
    runId: str = Field(min_length=1, max_length=100)
    assetId: str = Field(min_length=1, max_length=100)
    caption: str | None = Field(None, min_length=1, max_length=2000)
    altText: str | None = Field(None, min_length=1, max_length=2000)
    confirmUnverified: bool = False
    confirmLibrarySource: bool = False
    paragraphIndex: int | None = Field(None, ge=0, le=60000)


class PatchIllustration(Version):
    paragraphIndex: int | None = Field(None, ge=0, le=60000)
    caption: str | None = Field(None, min_length=1, max_length=2000)
    altText: str | None = Field(None, min_length=1, max_length=2000)
    locked: bool | None = None
    confirmUnverified: bool = False


class CompleteIllustrations(Version):
    skip: bool = False
    clearExisting: bool = False


class ImageBatch(Version):
    sectionIds: list[str] = Field(min_length=1, max_length=20)
    searchScope: Literal['references', 'library'] = 'references'
    modelId: str | None = Field(None, min_length=1, max_length=100)
    query: str = Field('', max_length=500)
    confirmLibrarySource: bool = False


class ManualImage(Version):
    sectionId: str = Field(min_length=1, max_length=100)
    paragraphIndex: int = Field(ge=0, le=60000)
    paragraphExact: str | None = Field(None, min_length=1, max_length=60000)
    contentHash: str | None = Field(None, pattern='^[0-9a-f]{64}$')
    assetId: str = Field(min_length=1, max_length=100)
    searchScope: Literal['references', 'library'] = 'references'
    modelId: str | None = Field(None, min_length=1, max_length=100)
    caption: str | None = Field(None, min_length=1, max_length=2000)
    altText: str | None = Field(None, min_length=1, max_length=2000)
    confirmLibrarySource: bool = False


class EditSelection(Version):
    expectedSectionVersion: int = Field(ge=1)
    baseContentHash: str = Field(pattern='^[0-9a-f]{64}$')
    operation: Literal['polish', 'rewrite', 'shorten', 'expand']
    selection: Selection
    instruction: str = Field('', max_length=2000)


def router(writing):
    routes = APIRouter(prefix='/api/writing/projects')
    assist_routes = APIRouter(prefix='/api/writing/assist')

    def call(operation, *args, **kwargs):
        try:
            result = operation(*args, **kwargs)
            if isinstance(result, dict):
                project = result.get('project') if 'project' in result else result
                if isinstance(project, dict) and 'runs' in project:
                    writing.public_image_targets(project)
            return result
        except WritingConflict as exc:
            raise HTTPException(status_code=409, detail=getattr(exc, 'detail', str(exc))) from None
        except PDFExportError as exc:
            raise HTTPException(status_code=exc.status_code, detail=exc.detail) from None

    @assist_routes.post('/title')
    def assist_title(body: WritingAssist):
        return call(writing.assist, 'title', body.model_dump())

    @assist_routes.post('/material-queries')
    def assist_material_queries(body: WritingAssist):
        return call(writing.assist, 'material-queries', body.model_dump())

    @routes.get('')
    def list_projects():
        return {'projects': writing.list()}

    @routes.post('', status_code=201)
    def create(body: CreateProject):
        return {'project': call(writing.create, body.model_dump(exclude_unset=True))}

    @routes.get('/{project_id}')
    def project(project_id: str):
        return {'project': call(writing.get, project_id)}

    @routes.delete('/{project_id}')
    def delete(project_id: str, body: Version):
        return call(writing.delete, project_id, body.expectedVersion)

    @routes.patch('/{project_id}')
    def patch(project_id: str, body: PatchProject):
        changes = body.model_dump(exclude_unset=True, exclude={'expectedVersion'})
        if any(value is None for key, value in changes.items() if key not in ('modelId', 'materialSelection')):
            raise ValueError('标题、需求、模式和资料范围不能为 null')
        return {'project': call(writing.patch, project_id, body.expectedVersion, changes)}

    @routes.put('/{project_id}/outline')
    def outline(project_id: str, body: Outline):
        return {'project': call(writing.outline, project_id, body.expectedVersion, [s.model_dump(exclude_none=True) for s in body.sections])}

    @routes.post('/{project_id}/confirm-outline')
    def confirm(project_id: str, body: Version):
        return {'project': call(writing.confirm, project_id, body.expectedVersion)}

    @routes.post('/{project_id}/steps/{step}/confirm')
    def confirm_step(project_id: str, step: Literal['brief', 'materials', 'body'], body: Version):
        return {'project': call(writing.confirm_step, project_id, body.expectedVersion, step)}

    @routes.post('/{project_id}/generate-outline', status_code=202)
    def generate_outline(project_id: str, body: Version):
        return call(writing.queue, project_id, body.expectedVersion, 'outline')

    @routes.post('/{project_id}/automatic', status_code=202)
    def automatic(project_id: str, body: Version):
        return call(writing.queue_automatic, project_id, body.expectedVersion)

    @routes.post('/{project_id}/automatic/interrupt')
    def interrupt_automatic(project_id: str, body: AutomaticInterrupt):
        return call(writing.interrupt_automatic, project_id, body.expectedVersion, body.runId)

    @routes.get('/{project_id}/generation-plan')
    def generation_plan(project_id: str):
        return {'plan': call(writing.generation_plan, project_id)}

    @routes.post('/{project_id}/generate-document', status_code=202)
    def generate_document(project_id: str, body: GenerateDocument):
        return call(writing.queue_document, project_id, body.expectedVersion, body.confirmOutline,
                    body.remainingFromSectionId)

    @routes.post('/{project_id}/match-citations', status_code=202)
    def match_citations(project_id: str, body: Version):
        return call(writing.queue_citation_match, project_id, body.expectedVersion)

    @routes.post('/{project_id}/image-search', status_code=202)
    def image_search(project_id: str, body: ImageSearch):
        return call(writing.queue_image_search, project_id, body.expectedVersion, body.sectionId, body.paragraphIndex,
                    body.query, body.modelId, search_scope=body.searchScope, placement_mode=body.placementMode)

    @routes.get('/{project_id}/image-assets')
    def image_assets(project_id: str, searchScope: Literal['references', 'library'] = 'references',
                     q: str = Query('', max_length=500), limit: int = Query(60, ge=1, le=100), offset: int = Query(0, ge=0)):
        return call(writing.image_assets, project_id, searchScope, q, limit, offset)

    @routes.post('/{project_id}/image-batch', status_code=202)
    def image_batch(project_id: str, body: ImageBatch):
        return call(writing.queue_image_batch, project_id, body.expectedVersion, body.sectionIds,
                    search_scope=body.searchScope, model_id=body.modelId, query=body.query,
                    confirm_library_source=body.confirmLibrarySource)

    @routes.post('/{project_id}/manual-image')
    def manual_image(project_id: str, body: ManualImage):
        return call(writing.insert_manual_image, project_id, body.expectedVersion,
                    body.model_dump(exclude_none=True, exclude={'expectedVersion'}))

    @routes.post('/{project_id}/illustrations')
    def add_illustration(project_id: str, body: AddIllustration):
        return {'project': call(writing.add_illustration, project_id, body.expectedVersion,
                                body.model_dump(exclude_none=True, exclude={'expectedVersion'}))}

    @routes.patch('/{project_id}/illustrations/{figure_id}')
    def patch_illustration(project_id: str, figure_id: str, body: PatchIllustration):
        changes = body.model_dump(exclude_unset=True, exclude={'expectedVersion'})
        if any(value is None for value in changes.values()):
            raise ValueError('配图内容与状态不能为 null')
        return {'project': call(writing.patch_illustration, project_id, figure_id, body.expectedVersion, changes)}

    @routes.delete('/{project_id}/illustrations/{figure_id}')
    def remove_illustration(project_id: str, figure_id: str, body: Version):
        return {'project': call(writing.remove_illustration, project_id, figure_id, body.expectedVersion)}

    @routes.post('/{project_id}/illustrations/complete')
    def complete_illustrations(project_id: str, body: CompleteIllustrations):
        return {'project': call(writing.complete_illustrations, project_id, body.expectedVersion, body.skip, body.clearExisting)}

    @routes.post('/{project_id}/runs/{run_id}/sections/{section_id}/apply')
    def apply_document_section(project_id: str, run_id: str, section_id: str, body: Version):
        return {'project': call(writing.apply_document_section, project_id, run_id, section_id, body.expectedVersion)}

    @routes.post('/{project_id}/sections/{section_id}/generate', status_code=202)
    def generate_section(project_id: str, section_id: str, body: Version):
        return call(writing.queue, project_id, body.expectedVersion, 'section', section_id)

    @routes.patch('/{project_id}/sections/{section_id}')
    def save_section(project_id: str, section_id: str, body: SectionEdit):
        changes = body.model_dump(exclude_none=True, exclude={'expectedVersion'})
        return {'project': call(writing.save_section, project_id, section_id, body.expectedVersion, changes)}

    @routes.post('/{project_id}/sections/{section_id}/confirm')
    def confirm_section(project_id: str, section_id: str, body: Version):
        return {'project': call(writing.confirm_section, project_id, section_id, body.expectedVersion)}

    @routes.post('/{project_id}/sections/{section_id}/edit', status_code=202)
    def edit_selection(project_id: str, section_id: str, body: EditSelection):
        return call(writing.queue_edit, project_id, body.expectedVersion, section_id,
                    body.model_dump(exclude={'expectedVersion'}))

    @routes.post('/{project_id}/runs/{run_id}/apply')
    def apply(project_id: str, run_id: str, body: Apply):
        return {'project': call(writing.apply, project_id, run_id, body.expectedVersion, body.replaceManual)}

    @routes.post('/{project_id}/review')
    def review(project_id: str, body: Version):
        return call(writing.review, project_id, body.expectedVersion)

    @routes.post('/{project_id}/review/confirm')
    def review_confirm(project_id: str, body: ReviewConfirm):
        return call(writing.review_confirm, project_id, body.expectedVersion, body.issueIds, body.all)

    @routes.post('/{project_id}/review/skip')
    def review_skip(project_id: str, body: Version):
        return call(writing.review_skip, project_id, body.expectedVersion)

    @routes.post('/{project_id}/review/export')
    def review_export(project_id: str, body: ReviewExport):
        result = call(writing.export_review, project_id, body.expectedVersion, body.format)
        extension = '.' + body.format
        headers = {'Content-Disposition': 'attachment; filename=writing-review' + extension + "; filename*=UTF-8''" + quote(result['title'][:70] + '-核对报告' + extension, safe=''),
                   'X-Wenshu-Project-Version': str(result['version']), 'Cache-Control': 'no-store',
                   'X-Content-Type-Options': 'nosniff'}
        if body.format == 'html':
            headers['Content-Security-Policy'] = "sandbox; default-src 'none'; style-src 'unsafe-inline'"
        return Response(result['content'], media_type='application/json' if body.format == 'json' else 'text/html; charset=utf-8', headers=headers)

    @routes.get('/{project_id}/history')
    def history(project_id: str):
        return writing.history(project_id)

    @routes.get('/{project_id}/history/{version}')
    def historical(project_id: str, version: int):
        return {'project': writing.historical(project_id, version)}

    @routes.get('/{project_id}/export')
    def export(project_id: str, format: str = Query('html', pattern='^html$')):
        content, title = writing.export(project_id)
        return Response(content, media_type='text/html; charset=utf-8', headers={
            'Content-Disposition': "attachment; filename=writing-draft.html; filename*=UTF-8''" + quote(title[:80] + '.html', safe=''),
            'Content-Security-Policy': "sandbox; default-src 'none'; style-src 'unsafe-inline'"})

    @routes.post('/{project_id}/export')
    def export_document(project_id: str, body: ExportDocument):
        snapshot = call(writing.export_snapshot, project_id, body.expectedVersion, body.format, body.allowIssues)
        extension = '.' + body.format
        headers = {'Content-Disposition': 'attachment; filename=writing-draft' + extension + "; filename*=UTF-8''" + quote(snapshot['title'][:80] + extension, safe=''),
                   'X-Wenshu-Project-Version': str(snapshot['version']), 'X-Wenshu-Review-Status': snapshot['review']['status'],
                   'X-Wenshu-Draft-Required': str(review_requires_draft(snapshot['review'])).lower(),
                   'Cache-Control': 'no-store', 'X-Content-Type-Options': 'nosniff'}
        if body.format == 'html':
            headers['Content-Security-Policy'] = "sandbox; default-src 'none'; style-src 'unsafe-inline'"
        media_type = {'docx': 'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
                      'pdf': 'application/pdf', 'html': 'text/html; charset=utf-8'}[body.format]
        return Response(snapshot['content'], media_type=media_type, headers=headers)

    combined = APIRouter()
    combined.include_router(assist_routes)
    combined.include_router(routes)
    return combined
