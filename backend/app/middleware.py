from __future__ import annotations

import re
import secrets
from collections.abc import Awaitable, Callable
from typing import Any
from uuid import uuid4

from fastapi import Request
from fastapi.responses import JSONResponse, Response
from starlette.middleware.base import BaseHTTPMiddleware

SESSION_COOKIE = "llm_test_session"
_HOST_PATTERN = re.compile(r"^(127\.0\.0\.1|localhost)(:\d+)?$")
_UNSAFE_METHODS = {"POST", "PATCH", "DELETE"}
_DEV_ORIGINS = {"http://127.0.0.1:5173", "http://localhost:5173"}


class BrowserSessionStore:
    def __init__(self) -> None:
        self._sessions: dict[str, str] = {}

    def create(self) -> tuple[str, str]:
        session_id = secrets.token_urlsafe(32)
        csrf = secrets.token_urlsafe(32)
        self._sessions[session_id] = csrf
        return session_id, csrf

    def csrf_for(self, session_id: str | None) -> str | None:
        return self._sessions.get(session_id or "")


browser_sessions = BrowserSessionStore()


def error_response(code: str, message: str, request_id: str, status: int = 403) -> JSONResponse:
    return JSONResponse(
        status_code=status,
        content={"code": code, "message": message, "details": {}, "request_id": request_id},
    )


class BrowserSafetyMiddleware(BaseHTTPMiddleware):
    async def dispatch(
        self,
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        request_id = str(uuid4())
        request.state.request_id = request_id
        host = request.headers.get("host", "")
        if not _HOST_PATTERN.fullmatch(host):
            return error_response("host_forbidden", "仅允许 loopback Host", request_id)
        origin = request.headers.get("origin")
        same_origins = {f"http://{host}", f"https://{host}"}
        allowed_origins = same_origins | _DEV_ORIGINS
        if origin and origin not in allowed_origins:
            return error_response("origin_forbidden", "Origin 不在允许列表", request_id)
        if request.method == "OPTIONS":
            response = Response(status_code=204)
        else:
            if request.method in _UNSAFE_METHODS:
                session_id = request.cookies.get(SESSION_COOKIE)
                expected_csrf = browser_sessions.csrf_for(session_id)
                supplied_csrf = request.headers.get("x-csrf-token")
                if not expected_csrf or not secrets.compare_digest(
                    expected_csrf, supplied_csrf or ""
                ):
                    return error_response("csrf_forbidden", "CSRF 校验失败", request_id)
            response = await call_next(request)
        response.headers["X-Request-ID"] = request_id
        if origin:
            response.headers["Access-Control-Allow-Origin"] = origin
            response.headers["Access-Control-Allow-Credentials"] = "true"
            response.headers["Vary"] = "Origin"
            response.headers["Access-Control-Allow-Headers"] = "Content-Type, X-CSRF-Token"
            response.headers["Access-Control-Allow-Methods"] = "GET, POST, PATCH, DELETE, OPTIONS"
        return response


def bootstrap_payload(response: Response) -> dict[str, Any]:
    session_id, csrf = browser_sessions.create()
    response.set_cookie(
        SESSION_COOKIE,
        session_id,
        httponly=True,
        samesite="strict",
        secure=False,
        path="/",
    )
    response.headers["Cache-Control"] = "no-store"
    return {"csrf_token": csrf}
