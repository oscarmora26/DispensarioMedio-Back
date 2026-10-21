"""Configuracion central de la aplicacion basada en variables de entorno."""

import json
import re
from functools import lru_cache
from typing import Annotated, Literal
from urllib.parse import urlsplit

from pydantic import Field, ValidationInfo, field_validator, model_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

DEFAULT_JWT_SECRET = "dev-only-insecure-change-me-in-production-000000"
MIN_JWT_SECRET_LENGTH = 32

# CORS (frontend Angular). El default solo aplica a desarrollo.
CORS_DEFAULT_ALLOWED_ORIGINS = ("http://localhost:4200",)
CORS_DEFAULT_ALLOWED_METHODS = ("GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS")
CORS_DEFAULT_ALLOWED_HEADERS = ("Authorization", "Content-Type", "Accept", "X-Request-ID")
CORS_EXPOSE_HEADERS = ("X-Request-ID", "Retry-After")
CORS_PREFLIGHT_MAX_AGE_SECONDS = 600
CORS_ALLOWED_METHOD_VALUES = frozenset({"GET", "HEAD", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"})

CORS_WILDCARD = "*"

_CORS_ENV_VARS = {
    "cors_allowed_origins": "CORS_ALLOWED_ORIGINS",
    "cors_allowed_methods": "CORS_ALLOWED_METHODS",
    "cors_allowed_headers": "CORS_ALLOWED_HEADERS",
}

# Grafia canonica de la safelist de Starlette: evita cabeceras duplicadas en la respuesta.
_CORS_CANONICAL_HEADERS = {
    header.lower(): header
    for header in ("Accept", "Accept-Language", "Content-Language", "Content-Type")
}

# Token HTTP (RFC 9110 §5.6.2): sin espacios ni separadores.
_HEADER_TOKEN_RE = re.compile(r"^[!#$%&'*+\-.^_`|~0-9A-Za-z]+$")


def _parse_cors_list(value: object, env_var: str) -> list[str]:
    """Convierte el valor crudo de una variable CORS en lista de cadenas recortadas.

    Acepta coma-separado (`a,b`), un array JSON de cadenas (`["a","b"]`) y una
    lista o tupla de cadenas cuando se construye `Settings` programaticamente.
    """
    if value is None:
        return []

    if isinstance(value, str):
        raw = value.strip()
        if not raw:
            return []
        if raw.startswith("["):
            try:
                parsed = json.loads(raw)
            except json.JSONDecodeError as exc:
                raise ValueError(
                    f"{env_var} contiene un JSON invalido: usa una lista JSON de cadenas "
                    "o valores separados por comas"
                ) from exc
            if not isinstance(parsed, list) or not all(isinstance(item, str) for item in parsed):
                raise ValueError(f"{env_var} debe ser una lista JSON de cadenas")
            candidates: list[str] = parsed
        else:
            candidates = raw.split(",")
    elif isinstance(value, (list, tuple)):
        candidates = list(value)
        if not all(isinstance(item, str) for item in candidates):
            raise ValueError(f"{env_var} debe ser una lista de cadenas")
    else:
        raise ValueError(f"{env_var} debe ser una lista de cadenas o una cadena separada por comas")

    return [item.strip() for item in candidates if item.strip()]


def _normalize_origin(origin: str) -> str:
    """Normaliza y valida un origen serializado `http(s)://host[:puerto]`."""
    invalid = ValueError(f"CORS_ALLOWED_ORIGINS contiene un origen invalido: '{origin}'")
    if not origin or CORS_WILDCARD in origin:
        raise invalid
    if any(char.isspace() or ord(char) < 32 or ord(char) == 127 for char in origin):
        raise invalid

    parts = urlsplit(origin)
    if parts.scheme not in {"http", "https"} or not parts.netloc:
        raise invalid
    if parts.path or parts.query or parts.fragment:
        raise invalid
    if parts.username is not None or parts.password is not None:
        raise invalid
    try:
        port = parts.port
    except ValueError:
        raise invalid from None
    if port is not None and not 0 <= port <= 65535:
        raise invalid

    return f"{parts.scheme.lower()}://{parts.netloc.lower()}"


def _normalize_origins(origins: list[str]) -> list[str]:
    """Normaliza los origenes y deduplica conservando el orden de aparicion."""
    normalized: list[str] = []
    for origin in origins:
        candidate = origin.rstrip("/")
        value = CORS_WILDCARD if candidate == CORS_WILDCARD else _normalize_origin(candidate)
        if value not in normalized:
            normalized.append(value)
    return normalized


def _validate_cors_methods(methods: list[str]) -> list[str]:
    """Pasa los metodos a mayusculas, valida la lista blanca y deduplica."""
    validated: list[str] = []
    for method in methods:
        normalized = method.upper()
        if normalized == CORS_WILDCARD:
            raise ValueError("CORS_ALLOWED_METHODS no admite '*'")
        if normalized not in CORS_ALLOWED_METHOD_VALUES:
            raise ValueError(
                f"CORS_ALLOWED_METHODS contiene un metodo no permitido: '{normalized}'"
            )
        if normalized not in validated:
            validated.append(normalized)
    return validated


def _validate_cors_headers(headers: list[str]) -> list[str]:
    """Valida los nombres de cabecera, canonicaliza la safelist y deduplica."""
    validated: list[str] = []
    seen: set[str] = set()
    for header in headers:
        if header == CORS_WILDCARD:
            raise ValueError("CORS_ALLOWED_HEADERS no admite '*'")
        if not _HEADER_TOKEN_RE.fullmatch(header):
            raise ValueError(
                f"CORS_ALLOWED_HEADERS contiene un nombre de cabecera invalido: '{header}'"
            )
        canonical = _CORS_CANONICAL_HEADERS.get(header.lower(), header)
        key = canonical.lower()
        if key not in seen:
            seen.add(key)
            validated.append(canonical)
    return validated


class Settings(BaseSettings):
    """Settings cargados desde `.env` y variables de entorno."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    app_name: str = "Dispensario Medico API"
    version: str = "0.1.0"
    environment: Literal["local", "dev", "test", "staging", "prod"] = "local"
    debug: bool = False
    api_v1_prefix: str = "/api/v1"

    # Base de datos
    database_url: str = "postgresql+asyncpg://dispensario:dispensario@localhost:5433/dispensario"
    database_echo: bool = False
    database_pool_size: int = 5
    database_max_overflow: int = 10

    # Logging
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"] = "INFO"
    log_json: bool = False
    log_include_source: bool = False

    # JWT
    jwt_secret: str = DEFAULT_JWT_SECRET
    jwt_algorithm: Literal["HS256"] = "HS256"
    jwt_expire_minutes: int = Field(default=30, gt=0)

    # Rate limiting del login
    login_rate_limit_max_attempts: int = Field(default=5, gt=0)
    login_rate_limit_window_seconds: int = Field(default=900, gt=0)

    # CORS: lista vacia en `cors_allowed_origins` deshabilita el middleware.
    cors_allowed_origins: Annotated[list[str], NoDecode] = Field(
        default_factory=lambda: list(CORS_DEFAULT_ALLOWED_ORIGINS)
    )
    cors_allowed_methods: Annotated[list[str], NoDecode] = Field(
        default_factory=lambda: list(CORS_DEFAULT_ALLOWED_METHODS)
    )
    cors_allowed_headers: Annotated[list[str], NoDecode] = Field(
        default_factory=lambda: list(CORS_DEFAULT_ALLOWED_HEADERS)
    )

    @field_validator(
        "cors_allowed_origins",
        "cors_allowed_methods",
        "cors_allowed_headers",
        mode="before",
    )
    @classmethod
    def _parse_cors_setting(cls, value: object, info: ValidationInfo) -> list[str]:
        """Parsea y valida los settings de CORS segun el campo recibido."""
        field_name = info.field_name or ""
        env_var = _CORS_ENV_VARS.get(field_name, field_name.upper())
        values = _parse_cors_list(value, env_var)
        if field_name == "cors_allowed_origins":
            return _normalize_origins(values)
        if field_name == "cors_allowed_methods":
            return _validate_cors_methods(values)
        return _validate_cors_headers(values)

    @model_validator(mode="after")
    def _validate_jwt_secret(self) -> "Settings":
        """Impide arrancar produccion con el secreto de ejemplo o uno demasiado corto."""
        if self.is_production:
            if self.jwt_secret == DEFAULT_JWT_SECRET:
                raise ValueError("JWT_SECRET no puede ser el valor de ejemplo en produccion")
            if len(self.jwt_secret) < MIN_JWT_SECRET_LENGTH:
                raise ValueError(
                    f"JWT_SECRET debe tener al menos {MIN_JWT_SECRET_LENGTH} "
                    "caracteres en produccion"
                )
        return self

    @model_validator(mode="after")
    def _validate_cors_config(self) -> "Settings":
        """Aplica las reglas de comodin y de listas vacias de CORS."""
        origins = self.cors_allowed_origins
        if CORS_WILDCARD in origins:
            if len(origins) > 1:
                raise ValueError(
                    "CORS_ALLOWED_ORIGINS no puede combinar '*' con origenes explicitos"
                )
            if self.is_production:
                raise ValueError("CORS_ALLOWED_ORIGINS no puede contener '*' en produccion")
        if origins:
            if not self.cors_allowed_methods:
                raise ValueError("CORS_ALLOWED_METHODS no puede estar vacio")
            if not self.cors_allowed_headers:
                raise ValueError("CORS_ALLOWED_HEADERS no puede estar vacio")
        return self

    @property
    def is_production(self) -> bool:
        """Indica si la aplicacion corre en produccion."""
        return self.environment == "prod"


@lru_cache
def get_settings() -> Settings:
    """Devuelve una instancia cacheada de la configuracion."""
    return Settings()
