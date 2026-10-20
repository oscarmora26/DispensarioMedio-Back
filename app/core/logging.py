"""Configuracion de logging estructurado con structlog."""

import logging
import sys
from typing import Any

import structlog
from structlog.types import EventDict, Processor

SENSITIVE_KEYS = frozenset(
    {
        "authorization",
        "cookie",
        "password",
        "passwd",
        "secret",
        "token",
        "access_token",
        "refresh_token",
        "api_key",
        "document_number",
        "documento",
        "cedula",
        "phone",
        "telefono",
        "email",
    }
)

REDACTED = "***"


def redact_sensitive(_logger: Any, _method_name: str, event_dict: EventDict) -> EventDict:
    """Reemplaza valores sensibles antes de emitir el log."""
    for key in list(event_dict):
        if key.lower() in SENSITIVE_KEYS:
            event_dict[key] = REDACTED
    return event_dict


def _shared_processors(include_source: bool) -> list[Processor]:
    processors: list[Processor] = [
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.add_log_level,
        structlog.stdlib.add_logger_name,
        structlog.processors.TimeStamper(fmt="iso", utc=True),
        redact_sensitive,
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
    ]
    if include_source:
        processors.insert(
            0,
            structlog.processors.CallsiteParameterAdder(
                parameters=[
                    structlog.processors.CallsiteParameter.FILENAME,
                    structlog.processors.CallsiteParameter.LINENO,
                    structlog.processors.CallsiteParameter.FUNC_NAME,
                ]
            ),
        )
    return processors


def configure_logging(
    *,
    level: str = "INFO",
    json_logs: bool = False,
    include_source: bool = False,
) -> None:
    """Configura structlog y puentea el logging estandar (uvicorn, sqlalchemy, alembic)."""
    shared_processors = _shared_processors(include_source)

    renderer: Processor = (
        structlog.processors.JSONRenderer()
        if json_logs
        else structlog.dev.ConsoleRenderer(colors=sys.stdout.isatty())
    )

    structlog.configure(
        processors=[
            *shared_processors,
            structlog.stdlib.ProcessorFormatter.wrap_for_formatter,
        ],
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        cache_logger_on_first_use=True,
    )

    formatter = structlog.stdlib.ProcessorFormatter(
        foreign_pre_chain=shared_processors,
        processors=[
            structlog.stdlib.ProcessorFormatter.remove_processors_meta,
            renderer,
        ],
    )

    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(formatter)

    root_logger = logging.getLogger()
    root_logger.handlers.clear()
    root_logger.addHandler(handler)
    root_logger.setLevel(level)

    # uvicorn emite por sus propios loggers: los redirigimos al pipeline raiz.
    for logger_name in ("uvicorn", "uvicorn.error"):
        logger = logging.getLogger(logger_name)
        logger.handlers.clear()
        logger.propagate = True
    # El log de acceso lo emite nuestro middleware, no uvicorn.
    logging.getLogger("uvicorn.access").disabled = True

    # SQLAlchemy solo loguea queries si se habilita explicitamente.
    logging.getLogger("sqlalchemy.engine").setLevel(
        logging.INFO if include_source else logging.WARNING
    )
    logging.getLogger("alembic").setLevel(logging.INFO)
