"""Configuracion central de la aplicacion basada en variables de entorno."""

from functools import lru_cache
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict


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

    @property
    def is_production(self) -> bool:
        """Indica si la aplicacion corre en produccion."""
        return self.environment == "prod"


@lru_cache
def get_settings() -> Settings:
    """Devuelve una instancia cacheada de la configuracion."""
    return Settings()
