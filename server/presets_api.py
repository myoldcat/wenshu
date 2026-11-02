"""Read-only help endpoints, retaining the existing view and download URLs."""
from urllib.parse import quote

from fastapi import APIRouter, HTTPException
from fastapi.responses import HTMLResponse, Response
from .presets import PresetError, catalog, document_resource, help_edition


def router(*, deployment=None):
    edition = help_edition(deployment)
    routes = APIRouter(prefix='/api/presets', tags=['使用帮助'])

    @routes.get('')
    def list_presets():
        try:
            return catalog(deployment=edition)
        except PresetError as exc:
            raise HTTPException(400, str(exc)) from None

    def resource(item_id):
        try:
            return document_resource(item_id, deployment=edition)
        except KeyError:
            raise HTTPException(404, '未找到预置资料') from None
        except PresetError as exc:
            raise HTTPException(400, str(exc)) from None

    @routes.get('/{item_id}/view')
    def view(item_id: str):
        _, content = resource(item_id)
        return HTMLResponse(content, headers={
            'Content-Security-Policy': "sandbox allow-same-origin allow-popups allow-popups-to-escape-sandbox; default-src 'none'; img-src data:; style-src 'unsafe-inline'; font-src data:; base-uri 'none'; form-action 'none'; frame-ancestors 'self'",
            'X-Frame-Options': 'SAMEORIGIN', 'Cache-Control': 'no-store',
            'Referrer-Policy': 'no-referrer', 'X-Content-Type-Options': 'nosniff'})

    @routes.get('/{item_id}/download')
    def download(item_id: str):
        item, content = resource(item_id)
        filename = item['title'].replace('/', '_').replace('\\', '_') + '.html'
        return Response(content, media_type='text/html; charset=utf-8', headers={
            'Content-Disposition': "attachment; filename*=UTF-8''" + quote(filename, safe=''),
            'Cache-Control': 'no-store', 'X-Content-Type-Options': 'nosniff'})

    return routes
