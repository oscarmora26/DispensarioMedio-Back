"""Configuracion central de la aplicacion basada en variables de entorno."""

from functools import lru_cache
from typing import Literal

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

DEFAULT_JWT_SECRET = "dev-only-insecure-change-me-in-production-000000"
MIN_JWT_SECRET_LENGTH = 32


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

    @property
    def is_production(self) -> bool:
        """Indica si la aplicacion corre en produccion."""
        return self.environment == "prod"


@lru_cache
def get_settings() -> Settings:
    """Devuelve una instancia cacheada de la configuracion."""
    return Settings()
