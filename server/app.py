"""Local API with explicit host/origin opt-in for a private test environment."""
import hashlib
import ipaddress
import os
from pathlib import Path
import re
import tempfile
from typing import Annotated, Literal
from urllib.parse import urlsplit
from fastapi import FastAPI, File, UploadFile, HTTPException, Request, Query
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, Response
from fastapi.exceptions import RequestValidationError
from pydantic import BaseModel, Field, ConfigDict
from starlette.middleware.trustedhost import TrustedHostMiddleware
from .store import Store, MAX_FILE, ident, normalized_url


def _exact_host(value):
    """Only literal IPv4 addresses and DNS names, never host-pattern syntax."""
    value=value.lower()
    if len(value)>253 or not re.fullmatch(r'[a-z0-9](?:[a-z0-9.-]*[a-z0-9])?',value):
        raise ValueError('请填写明确的 IPv4 地址或主机名，不含协议、端口或通配符')
    if re.fullmatch(r'[0-9.]+',value):
        try:ipaddress.IPv4Address(value)
        except ValueError:raise ValueError('IPv4 地址格式不正确') from None
    elif any(not re.fullmatch(r'[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?',part) for part in value.split('.')):
        raise ValueError('主机名格式不正确')
    return value


def _exact_origin(value):
    try:
        parsed=urlsplit(value)
        if parsed.scheme not in ('http','https') or not parsed.hostname or parsed.username is not None or parsed.password is not None:
            raise ValueError()
        host=_exact_host(parsed.hostname)
        port=parsed.port
        if port is not None and not 1<=port<=65535:raise ValueError()
        # A browser Origin is a scheme/host/port tuple, without a trailing slash.
        if value.lower()!=f'{parsed.scheme}://{parsed.netloc}'.lower():raise ValueError()
        if parsed.netloc.endswith(':') or re.search(r'[\x00-\x20\x7f]',value):raise ValueError()
        suffix=f':{port}' if port is not None and port!={'http':80,'https':443}[parsed.scheme] else ''
        return f'{parsed.scheme}://{host}{suffix}'
    except ValueError:
        raise ValueError('请填写明确的 HTTP/HTTPS 来源，不含路径、账户或通配符') from None


def _environment_allowlist(name,defaults,validate):
    raw=os.environ.get(name,'').strip()
    additions=[]
    if raw:
        for item in raw.split(','):
            try:additions.append(validate(item.strip()))
            except ValueError as exc:raise ValueError(f'{name} 配置无效：{exc}') from None
    return list(dict.fromkeys([*defaults,*additions]))


def create_app(data_dir=None, *, model_service=None, help_deployment='server'):
    # Fail before opening the data directory when deployment boundaries are invalid.
    allowed_hosts=_environment_allowlist('WENSHU_ALLOWED_HOSTS',['127.0.0.1','localhost','testserver'],_exact_host)
    allowed_origins=_environment_allowlist('WENSHU_ALLOWED_ORIGINS',[
        'http://127.0.0.1:4173','http://localhost:4173','http://127.0.0.1:8787','http://localhost:8787',
    ],_exact_origin)
    store=Store(data_dir)
    from .model_gateway import ModelService
    from .enrichment import EnrichmentService
    from .ai_api import router as ai_router
    from .wiki import WikiService
    from .wiki_api import router as wiki_router
    from .writing import WritingService
    from .writing_api import router as writing_router
    from .writing_operations_api import router as writing_operations_router
    from .retrieval import RetrievalService
    from .retrieval_api import router as retrieval_router
    from .home_api import router as home_router
    from .card_cover import CardCoverService
    from .prompts import PromptLibrary
    from .prompts_api import router as prompts_router
    from .presets_api import router as presets_router
    models=model_service or ModelService(store)
    enrichment=EnrichmentService(store,models)
    wiki=WikiService(store,models)
    from .wiki_knowledge import WikiKnowledgeService
    from .wiki_resolution import WikiResolutionService
    from .wiki_knowledge_api import router as wiki_knowledge_router
    knowledge=WikiKnowledgeService(wiki)
    resolution=WikiResolutionService(store,models,knowledge.canonical)
    writing=WritingService(store,models)
    retrieval=RetrievalService(store,models,operations=writing.operations)
    card_covers=CardCoverService(store,models)
    app=FastAPI(title='文枢本地文档服务',version='0.2.1')
    app.state.store=store
    app.state.models=models
    app.state.enrichment=enrichment
    app.state.wiki=wiki
    app.state.wiki_knowledge=knowledge
    app.state.wiki_resolution=resolution
    app.state.writing=writing
    app.state.retrieval=retrieval
    app.state.card_covers=card_covers
    app.state.prompts=PromptLibrary(store)
    app.include_router(prompts_router(app.state.prompts))
    app.include_router(presets_router(deployment=help_deployment))
    app.include_router(ai_router(models,enrichment))
    app.include_router(wiki_router(wiki))
    app.include_router(wiki_knowledge_router(knowledge,resolution))
    app.include_router(writing_router(writing))
    app.include_router(writing_operations_router(writing.operations))
    app.include_router(retrieval_router(retrieval))
    app.include_router(home_router(store))
    app.add_middleware(TrustedHostMiddleware,allowed_hosts=allowed_hosts,www_redirect=False)

    @app.middleware('http')
    async def local_boundary(request:Request,call_next):
        origin=request.headers.get('origin')
        if origin and origin not in allowed_origins:
            return JSONResponse({'detail':'仅允许已配置的工作台来源访问'},status_code=403)
        if request.method in ('POST','PATCH','PUT','DELETE') and request.headers.get('x-wenshu-client')!='local':
            return JSONResponse({'detail':'缺少本地工作台请求标识'},status_code=403)
        try:
            if int(request.headers.get('content-length','0'))>256*1024*1024:
                return JSONResponse({'detail':'单次上传总量不能超过 256 MiB，请分批导入'},status_code=413)
        except ValueError:
            return JSONResponse({'detail':'无效的请求长度'},status_code=400)
        response=await call_next(request)
        response.headers['X-Content-Type-Options']='nosniff'
        response.headers['Cache-Control']='no-store'
        return response

    @app.exception_handler(KeyError)
    async def missing(request,exc):return JSONResponse({'detail':'未找到对应文档、版本或任务'},status_code=404)

    @app.exception_handler(ValueError)
    async def invalid(request,exc):return JSONResponse({'detail':str(exc)},status_code=422)

    @app.exception_handler(RequestValidationError)
    async def invalid_request(request,exc):
        # Pydantic's default response echoes invalid input, which may contain a key.
        fields=', '.join('.'.join(str(part) for part in e['loc']) for e in exc.errors())
        return JSONResponse({'detail':'请求字段格式或范围不正确：'+fields},status_code=422)

    @app.get('/api/health')
    def health():
        with store.connect() as db:
            count=db.execute('SELECT count(*) FROM sources WHERE current_revision IS NOT NULL').fetchone()[0]
        retrieval_settings = retrieval.get_settings()
        semantic_status = 'configured' if retrieval_settings['channels']['vector'] and retrieval_settings.get('embeddingModelId') else 'not_configured'
        return {'application':'wenshu','data_dir':str(store.root),'status':'ok','storage':'SQLite FTS5','documents':count,'search':'Chinese fulltext','semantic_search':semantic_status}

    @app.get('/api/documents')
    def documents(limit:int=Query(500,ge=1,le=1000),offset:int=Query(0,ge=0)):
        return store.documents(limit,offset)

    @app.get('/api/images')
    def images(q:str=Query('',max_length=500),limit:int=Query(48,ge=1,le=100),
               offset:int=Query(0,ge=0),source_id:str|None=Query(None,min_length=1,max_length=100)):
        return store.images(q,limit,offset,source_id)

    @app.get('/api/documents/{source_id}')
    def detail(source_id:str,revision_id:str|None=None):return {'document':store.detail(source_id,revision_id)}

    @app.get('/api/documents/{source_id}/deletion-impact')
    def document_deletion_impact(source_id:str):
        from .document_deletion import DocumentDeletionService
        return {'impact': DocumentDeletionService(store).impact(source_id)}

    class DeleteDocumentRequest(BaseModel):
        model_config=ConfigDict(extra='forbid',strict=True)
        expectedRevisionId:str=Field(min_length=1,max_length=150)
        impactToken:str=Field(min_length=64,max_length=64,pattern=r'^[a-f0-9]{64}$')
        confirmReferences:bool=False

    @app.delete('/api/documents/{source_id}')
    def delete_document(source_id:str,body:DeleteDocumentRequest):
        from .document_deletion import DocumentDeletionService, DocumentDeletionConflict
        try:
            return DocumentDeletionService(store).delete(source_id,body.expectedRevisionId,
                body.impactToken,body.confirmReferences)
        except DocumentDeletionConflict as exc:
            raise HTTPException(409,{'message':str(exc),'impact':exc.impact}) from exc

    class CardCoverRequest(BaseModel):
        model_config=ConfigDict(extra='forbid',strict=True)
        revisionId:str|None=Field(None,max_length=100)
        modelId:str|None=Field(None,max_length=100)
        force:bool=False

    @app.get('/api/documents/{source_id}/card-cover')
    def card_cover(source_id:str,revision_id:str|None=None,model_id:str|None=None):
        return card_covers.detail(source_id,revision_id,model_id)

    @app.post('/api/documents/{source_id}/card-cover',status_code=202)
    def select_card_cover(source_id:str,body:CardCoverRequest):
        return card_covers.queue(source_id,revision_id=body.revisionId,model_id=body.modelId,force=body.force)

    class Patch(BaseModel):
        model_config=ConfigDict(extra='forbid')
        favorite:bool|None=None
        read:bool|None=None
        selected:bool|None=None
        tags:list[str]|None=Field(None,max_length=30)
        classificationPaths:list[dict]|None=Field(None,max_length=3)
        articleType:Literal['fact','opinion','technical']|None=None
        revisionId:str|None=Field(None,min_length=1,max_length=150)

    @app.patch('/api/documents/{source_id}')
    def patch(source_id:str,body:Patch):
        changes=body.model_dump(exclude_none=True)
        if 'articleType' in body.model_fields_set:
            changes['articleType']=body.articleType
        if any(len(t)>80 for t in changes.get('tags',[])):raise ValueError('分类名称不能超过 80 字符')
        from .article_type import ArticleTypeRevisionConflict
        try:
            return {'document':store.patch(source_id,changes)}
        except ArticleTypeRevisionConflict as exc:
            raise HTTPException(409,str(exc)) from exc

    @app.get('/api/documents/{source_id}/original')
    def original(source_id:str,revision_id:str|None=None):
        path,title=store.original(source_id,revision_id)
        safe=re.sub(r'[<>:"/\\|?*\x00-\x1f]','_',title)[:100]
        return FileResponse(path,media_type='application/octet-stream',filename=safe+'.html',headers={'Content-Security-Policy':"sandbox; default-src 'none'"})

    @app.api_route('/api/documents/{source_id}/preview',methods=['GET','HEAD'])
    def preview(source_id:str,request:Request,revision_id:str|None=None):
        from .preview import PREVIEW_HEADERS,render_preview
        path,assets=store.preview_source(source_id,revision_id,include_assets=request.method!='HEAD')
        if request.method=='HEAD':
            return Response(media_type='text/html',headers=PREVIEW_HEADERS)
        return HTMLResponse(render_preview(path.read_bytes(),assets),headers=PREVIEW_HEADERS)

    @app.get('/api/assets/{asset_id}')
    def asset(asset_id:str):
        path,mime=store.asset(asset_id)
        return FileResponse(path,media_type=mime)

    @app.get('/api/search')
    def search(q:str=Query('',max_length=500),limit:int=Query(100,ge=1,le=500),offset:int=Query(0,ge=0),category:str='',source:str='',filter:str='all',since:str='',sort:str='relevance'):
        return store.search(q,limit,offset,category,source,filter,since,sort)

    class ScopedSearch(BaseModel):
        model_config=ConfigDict(extra='forbid')
        q:str=Field('',max_length=500)
        limit:int=Field(100,ge=1,le=500)
        offset:int=Field(0,ge=0)
        category:str=''
        source:str=''
        filter:str='all'
        since:str=''
        sort:str='relevance'
        sourceIds:list[str]=Field(max_length=10000)

    @app.post('/api/search')
    def scoped_search(body:ScopedSearch):
        if any(len(value)>100 for value in body.sourceIds):raise ValueError('文档标识无效')
        return store.search(body.q,body.limit,body.offset,body.category,body.source,body.filter,body.since,body.sort,body.sourceIds)

    @app.post('/api/imports/files',status_code=202)
    async def files(request:Request,files:Annotated[list[UploadFile],File()]):
        if len(files)>50:raise ValueError('每批最多 50 份文件，请分批上传')
        batch=ident('batch');jobs=[]
        for file in files:
            name=Path(file.filename or 'document.html').name
            fd,tmp=tempfile.mkstemp(dir=store.root/'staging',suffix='.html');size=0
            try:
                with os.fdopen(fd,'wb') as output:
                    while chunk:=await file.read(1024*1024):
                        size+=len(chunk)
                        if size>MAX_FILE:raise ValueError(f'{name} 超过 64 MiB 上限')
                        output.write(chunk)
                if Path(name).suffix.lower() not in ('.html','.htm'):
                    raise ValueError(f'{name} 不是 HTML 文件')
                raw=Path(tmp).read_bytes()
                digest,rel=store.put_blob(raw)
                jobs.append(store.queue('file',{'path':str(store.root/rel),'filename':name},name,batch,
                    request_key=(request.headers.get('idempotency-key')+':'+digest) if request.headers.get('idempotency-key') else None))
            except ValueError as exc:
                # Preserve independent successes in a mixed batch and expose each failure.
                failed=store.queue('file',{'filename':name,'retryable':False},name,batch)
                with store.connect() as db:
                    db.execute("UPDATE jobs SET status='failed',error=? WHERE id=?",(str(exc)+'；请重新上传符合要求的 HTML 文件',failed['id']))
                jobs.append(store.job(failed['id']))
            finally:
                await file.close()
                Path(tmp).unlink(missing_ok=True)
        return {'batch_id':batch,'jobs':jobs}

    class Urls(BaseModel):
        urls:list[str]=Field(min_length=1,max_length=50)

    @app.post('/api/imports/urls',status_code=202)
    def urls(body:Urls,request:Request):
        normalized=list(dict.fromkeys(normalized_url(u) for u in body.urls))
        batch=ident('batch');key=request.headers.get('idempotency-key')
        return {'batch_id':batch,'jobs':[store.queue('url',{'url':u},u,batch,(key+':'+hashlib.sha256(u.encode()).hexdigest()) if key else None) for u in normalized]}

    @app.get('/api/jobs')
    def jobs(limit:int=Query(100,ge=1,le=1000),scope:Literal['all','ingest','writing']='all'):
        return store.jobs(limit,scope)

    @app.get('/api/jobs/{job_id}')
    def job(job_id:str):return {'job':store.job(job_id)}

    @app.post('/api/jobs/{job_id}/retry')
    def retry(job_id:str):return {'job':store.retry(job_id)}

    @app.post('/api/jobs/{job_id}/cancel')
    def cancel(job_id:str):return {'job':store.cancel(job_id)}

    return app

app=create_app()
