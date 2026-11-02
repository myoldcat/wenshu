"""A loopback-only, session-authenticated host for the desktop web frontend.

The browser session is established by a one-use launch URL. Neither that URL
nor the session credential is written to access logs, application data or JSON
health responses. Existing API boundaries remain enabled inside this wrapper.
"""
from __future__ import annotations

from contextlib import contextmanager
import hmac
import os
from pathlib import Path
import secrets
import threading

from fastapi import Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse


COOKIE_NAME = "wenshu_desktop_session"
DEFAULT_PORT = 18787


@contextmanager
def _application_environment(data_dir: Path, origin: str):
    values = {
        "WENSHU_DATA_DIR": str(data_dir),
        "WENSHU_ALLOWED_HOSTS": "127.0.0.1",
        "WENSHU_ALLOWED_ORIGINS": origin,
    }
    previous = {key: os.environ.get(key) for key in values}
    os.environ.update(values)
    try:
        yield
    finally:
        for key, value in previous.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


def request_stop(control_dir: Path) -> None:
    """The launcher and HTTP host use the same harmless stop marker."""
    control_dir.mkdir(parents=True, exist_ok=True)
    (control_dir / "stop").touch(exist_ok=True)


def create_desktop_app(data_dir, frontend_dir, control_dir, token, *,
                       port: int = DEFAULT_PORT, model_service=None):
    if isinstance(port, bool) or not isinstance(port, int) or not 1024 <= port <= 65535:
        raise ValueError("桌面服务端口无效")
    if not isinstance(token, str) or not 32 <= len(token) <= 512 or any(c.isspace() for c in token):
        raise ValueError("桌面启动凭证无效")
    data_dir, frontend_dir, control_dir = map(
        lambda value: Path(value).resolve(), (data_dir, frontend_dir, control_dir))
    if not (frontend_dir / "index.html").is_file():
        raise ValueError("文枢界面文件缺失，请重新安装")
    if data_dir == frontend_dir or data_dir.is_relative_to(frontend_dir):
        raise ValueError("资料目录须独立于程序界面目录")
    control_dir.mkdir(parents=True, exist_ok=True)
    origin = f"http://127.0.0.1:{port}"
    host = f"127.0.0.1:{port}"
    # Delay importing server.app until its historical module-level app also
    # sees the explicitly selected desktop data directory.
    with _application_environment(data_dir, origin):
        from server.app import create_app
        app = create_app(data_dir=data_dir,
                         model_service=model_service, help_deployment='windows')
    session = secrets.token_urlsafe(48)
    launch_lock = threading.Lock()
    launch_used = False
    # Files are fixed at startup. No directory listing, source-map exposure,
    # symlink escape, SPA fallback to an arbitrary URL, or user-data serving.
    allowed_files = {
        path.relative_to(frontend_dir).as_posix()
        for path in frontend_dir.rglob("*")
        if path.is_file() and not path.is_symlink()
        and path.resolve().is_relative_to(frontend_dir)
        and not any(part.startswith(".") for part in path.relative_to(frontend_dir).parts)
        and path.suffix.lower() not in (".map", ".py", ".sqlite3", ".db", ".pem", ".key")
    }

    def error(message, status=403):
        return JSONResponse({"detail": message}, status_code=status)

    @app.middleware("http")
    async def desktop_boundary(request: Request, call_next):
        nonlocal launch_used
        if request.headers.get("host") != host:
            response = error("仅允许本机文枢窗口访问")
        elif request.headers.get("origin") not in (None, origin):
            response = error("仅允许本机文枢窗口访问")
        elif request.headers.get("sec-fetch-site") == "cross-site":
            response = error("仅允许本机文枢窗口访问")
        elif request.url.path == "/desktop/ready" and request.method == "GET":
            response = JSONResponse({"application": "wenshu-desktop", "status": "ready"})
        elif request.url.path == "/desktop/launch" and request.method == "GET":
            supplied = request.query_params.get("token", "")
            with launch_lock:
                accepted = not launch_used and hmac.compare_digest(supplied.encode("utf-8"), token.encode("utf-8"))
                if accepted:
                    launch_used = True
            if accepted:
                response = RedirectResponse("/", status_code=303)
                response.set_cookie(COOKIE_NAME, session, httponly=True, samesite="strict", path="/")
            else:
                response = error("文枢启动会话无效，请从桌面图标打开")
        elif not hmac.compare_digest(request.cookies.get(COOKIE_NAME, "").encode("utf-8"), session.encode("utf-8")):
            response = error("请从文枢桌面窗口访问")
        else:
            response = await call_next(request)
        response.headers["Cache-Control"] = "no-store"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["X-Content-Type-Options"] = "nosniff"
        # The fixed original-file preview explicitly permits same-origin
        # embedding while disabling scripts with its existing CSP sandbox.
        # Preserve that contract alongside the desktop window's frame guard.
        response.headers["X-Frame-Options"] = (
            "SAMEORIGIN" if "frame-ancestors 'self'" in response.headers.get("Content-Security-Policy", "") else "DENY")
        return response

    @app.post("/desktop/stop")
    def stop():
        # The original local-boundary middleware checks X-Wenshu-Client and
        # Origin before this endpoint is dispatched.
        request_stop(control_dir)
        return {"status": "stopping"}

    @app.get("/{path:path}", include_in_schema=False)
    def frontend(path: str):
        relative = "index.html" if not path else path
        if relative not in allowed_files:
            return error("未找到页面", 404)
        candidate = (frontend_dir / relative).resolve()
        if not candidate.is_relative_to(frontend_dir) or not candidate.is_file():
            return error("未找到页面", 404)
        if relative == "index.html":
            # The release deliberately contains no development corpus. Hide
            # its development-only shortcut without changing the shared web
            # frontend, document upload, URL import, or any theme styling.
            source = candidate.read_text(encoding="utf-8")
            desktop_style = '<style data-wenshu-desktop>.corpus-import{display:none!important}</style>'
            source = source.replace("</head>", desktop_style + "</head>", 1) if "</head>" in source else desktop_style + source
            return HTMLResponse(source)
        return FileResponse(candidate)

    return app
