"""Excepciones de dominio y manejadores globales."""

from collections.abc import Mapping
from typing import Any

import structlog
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

logger = structlog.get_logger(__name__)

ErrorItem = dict[str, str]


class AppError(Exception):
    """Error de negocio con codigo estable para el cliente."""

    status_code: int = 400
    code: str = "app_error"

    def __init__(
        self,
        message: str,
        *,
        status_code: int | None = None,
        code: str | None = None,
        errors: list[ErrorItem] | None = None,
        headers: Mapping[str, str] | None = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        if status_code is not None:
            self.status_code = status_code
        if code is not None:
            self.code = code
        self.errors = errors or []
        self.headers = dict(headers or {})
        if self.status_code == 401:
            self.headers.setdefault("WWW-Authenticate", "Bearer")


class NotFoundError(AppError):
    status_code = 404
    code = "not_found"


class ConflictError(AppError):
    status_code = 409
    code = "conflict"


class UnauthorizedError(AppError):
    """El llamante no esta autenticado o su token no es valido."""

    status_code = 401
    code = "not_authenticated"


class ForbiddenError(AppError):
    """El llamante esta autenticado pero no tiene permisos."""

    status_code = 403
    code = "forbidden"


class RateLimitError(AppError):
    """Se supero el limite de intentos permitido."""

    status_code = 429
    code = "rate_limited"

    def __init__(
        self,
        message: str,
        *,
        retry_after: int | None = None,
        code: str | None = None,
        errors: list[ErrorItem] | None = None,
        headers: Mapping[str, str] | None = None,
    ) -> None:
        merged_headers = dict(headers or {})
        if retry_after is not None:
            merged_headers["Retry-After"] = str(max(1, int(retry_after)))
        super().__init__(message, code=code, errors=errors, headers=merged_headers)


def _validation_field(loc: tuple[Any, ...]) -> str:
    """Convierte el `loc` de Pydantic en un nombre de campo legible."""
    parts = list(loc)
    if parts and parts[0] == "body":
        parts = parts[1:]
    return ".".join(str(part) for part in parts) or "body"


def register_exception_handlers(app: FastAPI) -> None:
    """Registra los handlers de excepciones de dominio y validacion."""

    @app.exception_handler(AppError)
    async def app_error_handler(request: Request, exc: AppError) -> JSONResponse:
        # No se loguea el mensaje: puede contener datos clinicos o personales.
        logger.warning(
            "app_error",
            code=exc.code,
            status=exc.status_code,
            method=request.method,
            path=request.url.path,
        )
        return JSONResponse(
            status_code=exc.status_code,
            content={"detail": exc.message, "code": exc.code, "errors": exc.errors},
            headers=exc.headers,
        )

    @app.exception_handler(RequestValidationError)
    async def validation_error_handler(
        request: Request, exc: RequestValidationError
    ) -> JSONResponse:
        errors: list[ErrorItem] = [
            {
                "field": _validation_field(tuple(error.get("loc", ()))),
                "message": str(error.get("msg", "")),
            }
            for error in exc.errors()
        ]
        logger.warning(
            "validation_error",
            method=request.method,
            path=request.url.path,
            error_count=len(errors),
        )
        return JSONResponse(
            status_code=422,
            content={"detail": "Datos inválidos", "code": "validation_error", "errors": errors},
        )
