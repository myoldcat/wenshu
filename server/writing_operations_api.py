"""Dedicated private writing inspection and deleted-manuscript cleanup."""
import ipaddress
import os
import re
from typing import Literal
from urllib.parse import urlsplit

from fastapi import APIRouter, Depends, HTTPException, Query, Request

from .writing import WritingConflict
from .writing_api import Version


PRIVATE_HOSTS = {'127.0.0.1', 'localhost', 'testserver'}


def _exact_private_host(value):
    host = value.lower()
    if (len(host) > 253 or not host or
            any(not re.fullmatch(r'[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?', part) for part in host.split('.'))):
        raise ValueError('Invalid operations hostname')
    if re.fullmatch(r'[0-9.]+', host):
        ipaddress.IPv4Address(host)
    return host


def _private_hosts():
    raw = os.environ.get('WENSHU_OPERATIONS_ALLOWED_HOSTS', '').strip()
    additions = {_exact_private_host(value.strip()) for value in raw.split(',')} if raw else set()
    return PRIVATE_HOSTS | additions


def _authority_host(value):
    match = re.fullmatch(r'([^:\s/?#@]+)(?::([0-9]{1,5}))?', value)
    if not match or (match[2] and not 1 <= int(match[2]) <= 65535):
        raise ValueError('Invalid operations authority')
    return _exact_private_host(match[1])


def private_operations(request: Request):
    """Prompt content stays on explicitly allowed private service entrances."""
    try:
        allowed = _private_hosts()
        host = _authority_host(request.headers.get('host', ''))
        origin = request.headers.get('origin')
        origin_host = None
        if origin:
            parsed = urlsplit(origin)
            if (parsed.scheme not in ('http', 'https') or parsed.username is not None or
                    parsed.password is not None or parsed.path or parsed.query or parsed.fragment or
                    re.search(r'\s', origin)):
                raise ValueError('Invalid operations origin')
            origin_host = _authority_host(parsed.netloc)
    except ValueError:
        raise HTTPException(403, '运维记录仅可通过内网地址访问') from None
    if host not in allowed or (origin and origin_host not in allowed):
        raise HTTPException(403, '运维记录仅可通过内网地址访问')


def router(service):
    api = APIRouter(prefix='/api/operations/writing', tags=['writing-operations'], dependencies=[Depends(private_operations)])

    @api.get('/projects')
    def projects(limit: int = Query(30, ge=1, le=100), offset: int = Query(0, ge=0), state: Literal['all', 'active', 'deleted'] = 'all'):
        return service.projects(limit, offset, state)

    @api.delete('/projects/{project_id}')
    def purge(project_id: str, body: Version):
        try:
            return service.purge(project_id, body.expectedVersion)
        except WritingConflict as exc:
            raise HTTPException(409, str(exc)) from None

    @api.get('/projects/{project_id}/runs')
    def runs(project_id: str, limit: int = Query(30, ge=1, le=100), offset: int = Query(0, ge=0)):
        return service.runs(project_id, limit, offset)

    @api.get('/runs/{run_id}/calls')
    def calls(run_id: str, limit: int = Query(30, ge=1, le=100), offset: int = Query(0, ge=0)):
        return service.calls(run_id, limit, offset)

    @api.get('/runs/{run_id}')
    def run(run_id: str):
        return service.run(run_id)

    @api.get('/calls/{call_id}')
    def call(call_id: str):
        return service.call(call_id)

    return api
