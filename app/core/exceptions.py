"""Excepciones de dominio y manejadores globales."""

from collections.abc import Mapping
from typing import Any

import structlog
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from sqlalchemy.exc import IntegrityError

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


_PARAM_LOCATIONS = frozenset({"body", "query", "path", "header"})


def _validation_field(loc: tuple[Any, ...]) -> str:
    """Convierte el `loc` de Pydantic en un nombre de campo legible.

    Se descarta la ubicacion del parametro (`body`, `query`, `path`, `header`)
    para que el cliente reciba solo el nombre del campo (FEAT-004 D-13).
    """
    parts = list(loc)
    if parts and parts[0] in _PARAM_LOCATIONS:
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

    @app.exception_handler(IntegrityError)
    async def integrity_error_handler(request: Request, exc: IntegrityError) -> JSONResponse:
        """Traduce una violacion de integridad a un envelope JSON estable.

        Nunca se expone el mensaje del motor: puede incluir nombres de
        constraints, valores de columnas o datos personales. El detalle queda
        en el log de servidor, asociado al `request_id` de la respuesta.
        """
        logger.error(
            "integrity_error",
            method=request.method,
            path=request.url.path,
            sqlstate=getattr(getattr(exc, "orig", None), "sqlstate", None),
            constraint=getattr(getattr(exc, "orig", None), "constraint_name", None),
        )
        return JSONResponse(
            status_code=409,
            content={
                "detail": "La operación entra en conflicto con el estado actual de los datos",
                "code": "integrity_error",
                "errors": [],
            },
        )

    @app.exception_handler(Exception)
    async def unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
        """Ultimo recurso: responde el envelope de error en vez de un traceback."""
        logger.exception(
            "unhandled_error",
            method=request.method,
            path=request.url.path,
            error_type=type(exc).__name__,
        )
        return JSONResponse(
            status_code=500,
            content={
                "detail": "Error interno del servidor",
                "code": "internal_error",
                "errors": [],
            },
        )
