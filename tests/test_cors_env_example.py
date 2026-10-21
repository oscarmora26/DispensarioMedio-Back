"""Tests del contenido documental de `.env.example` (FEAT-006, CA-10)."""

from __future__ import annotations

from pathlib import Path

import pytest

from app.core.config import CORS_DEFAULT_ALLOWED_HEADERS, CORS_DEFAULT_ALLOWED_METHODS, Settings

ENV_EXAMPLE = Path(__file__).resolve().parents[1] / ".env.example"


def test_ca10_env_example_documents_the_three_cors_variables() -> None:
    """CA-10: las tres variables aparecen con los valores por defecto de la spec."""
    lines = {line.strip() for line in ENV_EXAMPLE.read_text(encoding="utf-8").splitlines()}

    assert "CORS_ALLOWED_ORIGINS=http://localhost:4200" in lines
    assert ("CORS_ALLOWED_METHODS=" + ",".join(CORS_DEFAULT_ALLOWED_METHODS)) in lines
    assert ("CORS_ALLOWED_HEADERS=" + ",".join(CORS_DEFAULT_ALLOWED_HEADERS)) in lines


def test_ca10_env_example_documents_empty_list_and_production_rule() -> None:
    """CA-10: los comentarios explican que vacio deshabilita CORS y que prod no admite `*`."""
    content = ENV_EXAMPLE.read_text(encoding="utf-8")
    lowered = content.lower()

    assert "vacio = cors deshabilitado" in lowered
    assert "produccion no se admite" in lowered
    assert '"*"' in content


def test_ca10_env_example_loads_with_the_documented_defaults(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """CA-10: el ejemplo es un `.env` valido y produce exactamente los defaults."""
    for name in ("CORS_ALLOWED_ORIGINS", "CORS_ALLOWED_METHODS", "CORS_ALLOWED_HEADERS"):
        monkeypatch.delenv(name, raising=False)

    settings = Settings(_env_file=ENV_EXAMPLE)

    assert settings.cors_allowed_origins == ["http://localhost:4200"]
    assert settings.cors_allowed_methods == list(CORS_DEFAULT_ALLOWED_METHODS)
    assert settings.cors_allowed_headers == list(CORS_DEFAULT_ALLOWED_HEADERS)
