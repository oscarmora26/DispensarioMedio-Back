"""Middlewares ASGI para correlacion de requests y log de acceso."""

import time
import uuid
from typing import Any

import structlog
from starlette.types import ASGIApp, Message, Receive, Scope, Send

REQUEST_ID_HEADER = "x-request-id"
REQUEST_ID_HEADER_BYTES = b"x-request-id"

logger = structlog.get_logger("app.access")


class RequestIDMiddleware:
    """Propaga un request id a los logs y a la respuesta."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        headers = dict(scope.get("headers") or [])
        incoming = headers.get(REQUEST_ID_HEADER_BYTES, b"").decode("latin-1").strip()
        request_id = incoming or uuid.uuid4().hex

        structlog.contextvars.clear_contextvars()
        structlog.contextvars.bind_contextvars(request_id=request_id)

        async def send_with_request_id(message: Message) -> None:
            if message["type"] == "http.response.start":
                headers_list = list(message.get("headers") or [])
                headers_list.append((REQUEST_ID_HEADER_BYTES, request_id.encode("latin-1")))
                message["headers"] = headers_list
            await send(message)

        try:
            await self.app(scope, receive, send_with_request_id)
        finally:
            structlog.contextvars.clear_contextvars()


class AccessLogMiddleware:
    """Registra una linea estructurada por cada request HTTP."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        started_at = time.perf_counter()
        status_code: int | None = None

        async def send_with_status(message: Message) -> None:
            nonlocal status_code
            if message["type"] == "http.response.start":
                status_code = int(message["status"])
            await send(message)

        try:
            await self.app(scope, receive, send_with_status)
        except Exception:
            logger.exception(
                "request.failed",
                method=scope.get("method"),
                path=scope.get("path"),
                duration_ms=round((time.perf_counter() - started_at) * 1000, 2),
            )
            raise
        else:
            duration_ms = round((time.perf_counter() - started_at) * 1000, 2)
            client: Any = scope.get("client")
            logger.info(
                "request.completed",
                method=scope.get("method"),
                path=scope.get("path"),
                status=status_code,
                duration_ms=duration_ms,
                client_ip=client[0] if client else None,
            )
