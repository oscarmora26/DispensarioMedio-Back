"""Tests unitarios de los settings de CORS (FEAT-006, CA-1 a CA-9).

Los settings se construyen siempre con `Settings(_env_file=None)` y el entorno se
controla con `monkeypatch`; nunca se toca el `app` de modulo.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.core.config import (
    CORS_DEFAULT_ALLOWED_HEADERS,
    CORS_DEFAULT_ALLOWED_METHODS,
    CORS_DEFAULT_ALLOWED_ORIGINS,
    Settings,
)

CORS_ENV_VARS = (
    "CORS_ALLOWED_ORIGINS",
    "CORS_ALLOWED_METHODS",
    "CORS_ALLOWED_HEADERS",
)

# FEAT-002 CA-3: en `prod` el secreto debe tener >=32 caracteres y no ser el de ejemplo.
PROD_JWT_SECRET = "cors-review-secret-0123456789abcdefghijklmnop"  # 48 caracteres


def _settings(
    monkeypatch: pytest.MonkeyPatch,
    *,
    environ: dict[str, str] | None = None,
    **overrides: object,
) -> Settings:
    """Construye `Settings` sin `.env` y con solo las variables CORS indicadas."""
    for name in CORS_ENV_VARS:
        monkeypatch.delenv(name, raising=False)
    for name, value in (environ or {}).items():
        monkeypatch.setenv(name, value)
    return Settings(_env_file=None, **overrides)


# CA-1 -------------------------------------------------------------------------


def test_ca1_defaults_in_local(monkeypatch: pytest.MonkeyPatch) -> None:
    """CA-1: sin variables CORS, los tres settings devuelven los defaults documentados."""
    settings = _settings(monkeypatch)

    assert settings.environment == "local"
    assert settings.cors_allowed_origins == ["http://localhost:4200"]
    assert settings.cors_allowed_methods == ["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"]
    assert settings.cors_allowed_headers == [
        "Authorization",
        "Content-Type",
        "Accept",
        "X-Request-ID",
    ]
    # Los defaults son copias: mutar el resultado no altera las constantes del modulo.
    settings.cors_allowed_origins.append("http://otro.example")
    assert list(CORS_DEFAULT_ALLOWED_ORIGINS) == ["http://localhost:4200"]


# CA-2 / CA-3 ------------------------------------------------------------------


def test_ca2_comma_separated_format(monkeypatch: pytest.MonkeyPatch) -> None:
    """CA-2: el formato coma-separado recorta cada elemento y conserva el orden."""
    settings = _settings(
        monkeypatch,
        environ={
            "CORS_ALLOWED_ORIGINS": "http://localhost:4200, https://app.example.com ",
            "CORS_ALLOWED_METHODS": "get, POST, options",
            "CORS_ALLOWED_HEADERS": " Authorization , Content-Type ",
        },
    )

    assert settings.cors_allowed_origins == ["http://localhost:4200", "https://app.example.com"]
    assert settings.cors_allowed_methods == ["GET", "POST", "OPTIONS"]
    assert settings.cors_allowed_headers == ["Authorization", "Content-Type"]


def test_ca3_json_array_format(monkeypatch: pytest.MonkeyPatch) -> None:
    """CA-3: un array JSON de cadenas produce la misma lista que el formato coma-separado."""
    settings = _settings(
        monkeypatch,
        environ={
            "CORS_ALLOWED_ORIGINS": '["http://localhost:4200", "https://app.example.com"]',
            "CORS_ALLOWED_METHODS": '["GET","POST"]',
            "CORS_ALLOWED_HEADERS": '["Authorization","X-Request-ID"]',
        },
    )

    assert settings.cors_allowed_origins == ["http://localhost:4200", "https://app.example.com"]
    assert settings.cors_allowed_methods == ["GET", "POST"]
    assert settings.cors_allowed_headers == ["Authorization", "X-Request-ID"]


@pytest.mark.parametrize(
    "raw",
    ["[", '["ok", 3]'],
)
def test_ca3_malformed_json_is_a_configuration_error(
    monkeypatch: pytest.MonkeyPatch, raw: str
) -> None:
    """CA-3: JSON mal formado o con elementos no string falla el arranque.

    Nota del reviewer: los JSON que no son array y no empiezan por `[` (p. ej. `{}`)
    se tratan como valor coma-separado y se rechazan como origen invalido; ver el
    hallazgo MINOR del informe (el mensaje no cita JSON/lista/cadenas).
    """
    with pytest.raises(ValidationError) as excinfo:
        _settings(monkeypatch, environ={"CORS_ALLOWED_ORIGINS": raw})

    text = str(excinfo.value)
    assert "CORS_ALLOWED_ORIGINS" in text
    assert "JSON" in text or "lista" in text or "cadenas" in text


# CA-4 -------------------------------------------------------------------------


def test_ca4_normalization_and_deduplication(monkeypatch: pytest.MonkeyPatch) -> None:
    """CA-4: origenes en minusculas y sin barra final, metodos en mayusculas y dedupe."""
    settings = _settings(
        monkeypatch,
        environ={
            "CORS_ALLOWED_ORIGINS": (
                " HTTP://LOCALHOST:4200/ , http://localhost:4200 , https://App.Example.com/ "
            ),
            "CORS_ALLOWED_METHODS": "get, POST,get, options",
            "CORS_ALLOWED_HEADERS": "content-type, authorization, Content-Type, AUTHORIZATION",
        },
    )

    assert settings.cors_allowed_origins == ["http://localhost:4200", "https://app.example.com"]
    assert settings.cors_allowed_methods == ["GET", "POST", "OPTIONS"]
    # Dedupe case-insensitive conservando la primera grafia y canonicalizando la safelist.
    assert settings.cors_allowed_headers == ["Content-Type", "authorization"]


# CA-5 -------------------------------------------------------------------------


@pytest.mark.parametrize("raw", ["", "   ", "[]", '["", "  "]', " , "])
def test_ca5_empty_origin_list_disables_cors(monkeypatch: pytest.MonkeyPatch, raw: str) -> None:
    """CA-5: cualquier variante vacia produce `[]` y conserva los defaults de metodos/cabeceras."""
    settings = _settings(monkeypatch, environ={"CORS_ALLOWED_ORIGINS": raw})

    assert settings.cors_allowed_origins == []
    assert settings.cors_allowed_methods == list(CORS_DEFAULT_ALLOWED_METHODS)
    assert settings.cors_allowed_headers == list(CORS_DEFAULT_ALLOWED_HEADERS)


def test_ca5_programmatic_empty_list_is_valid(monkeypatch: pytest.MonkeyPatch) -> None:
    """CA-5: `Settings(_env_file=None, cors_allowed_origins=[])` tambien es valido."""
    settings = _settings(monkeypatch, cors_allowed_origins=[])

    assert settings.cors_allowed_origins == []


# CA-6 -------------------------------------------------------------------------


@pytest.mark.parametrize(
    "origin",
    [
        "localhost:4200",
        "http://",
        "http://localhost:4200/api",
        "http://localhost:4200?x=1",
        "ftp://example.com",
        "http://user:pass@example.com",
        "http://local host:4200",
        "http://localhost:abc",
        "https://*.example.com",
        "null",
    ],
)
def test_ca6_invalid_origins_abort_startup(monkeypatch: pytest.MonkeyPatch, origin: str) -> None:
    """CA-6: un origen invalido impide arrancar y el mensaje cita la variable."""
    with pytest.raises(ValidationError) as excinfo:
        _settings(monkeypatch, environ={"CORS_ALLOWED_ORIGINS": origin})

    assert "CORS_ALLOWED_ORIGINS" in str(excinfo.value)


def test_ca6_valid_origin_with_port_is_kept(monkeypatch: pytest.MonkeyPatch) -> None:
    """CA-6: un origen valido con puerto explicito se acepta sin cambios."""
    settings = _settings(
        monkeypatch, environ={"CORS_ALLOWED_ORIGINS": "https://app.example.com:8443"}
    )

    assert settings.cors_allowed_origins == ["https://app.example.com:8443"]


# CA-7 -------------------------------------------------------------------------


@pytest.mark.parametrize("environment", ["local", "dev", "test", "staging"])
def test_ca7_wildcard_is_accepted_outside_production(
    monkeypatch: pytest.MonkeyPatch, environment: str
) -> None:
    """CA-7: `*` se admite en local/dev/test/staging."""
    settings = _settings(
        monkeypatch,
        environ={"ENVIRONMENT": environment, "CORS_ALLOWED_ORIGINS": "*"},
    )

    assert settings.cors_allowed_origins == ["*"]


def test_ca7_wildcard_is_rejected_in_production(monkeypatch: pytest.MonkeyPatch) -> None:
    """CA-7: en `prod` el comodin falla el arranque con un mensaje que lo explica."""
    with pytest.raises(ValidationError) as excinfo:
        _settings(
            monkeypatch,
            environ={
                "ENVIRONMENT": "prod",
                "JWT_SECRET": PROD_JWT_SECRET,
                "CORS_ALLOWED_ORIGINS": "*",
            },
        )

    text = str(excinfo.value)
    assert "CORS_ALLOWED_ORIGINS" in text
    assert "produccion" in text


@pytest.mark.parametrize("environment", ["local", "prod"])
def test_ca7_wildcard_cannot_be_mixed_with_explicit_origins(
    monkeypatch: pytest.MonkeyPatch, environment: str
) -> None:
    """CA-7: `*` solo vale como unico elemento, en cualquier entorno."""
    with pytest.raises(ValidationError) as excinfo:
        _settings(
            monkeypatch,
            environ={
                "ENVIRONMENT": environment,
                "JWT_SECRET": PROD_JWT_SECRET,
                "CORS_ALLOWED_ORIGINS": "*,http://localhost:4200",
            },
        )

    assert "combinar" in str(excinfo.value)


@pytest.mark.parametrize(
    ("variable", "value"),
    [
        ("CORS_ALLOWED_METHODS", "*"),
        ("CORS_ALLOWED_HEADERS", "*"),
    ],
)
def test_ca7_wildcard_is_rejected_in_methods_and_headers(
    monkeypatch: pytest.MonkeyPatch, variable: str, value: str
) -> None:
    """CA-7: `*` no se admite en metodos ni en cabeceras."""
    with pytest.raises(ValidationError) as excinfo:
        _settings(monkeypatch, environ={variable: value})

    text = str(excinfo.value)
    assert variable in text
    assert "no admite '*'" in text


# CA-8 -------------------------------------------------------------------------


def test_ca8_unknown_method_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    """CA-8: un metodo fuera de la lista blanca falla citando variable y valor."""
    with pytest.raises(ValidationError) as excinfo:
        _settings(monkeypatch, environ={"CORS_ALLOWED_METHODS": "GET, TRACE"})

    text = str(excinfo.value)
    assert "CORS_ALLOWED_METHODS" in text
    assert "TRACE" in text


def test_ca8_empty_methods_rejected_when_origins_are_set(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """CA-8: con origenes no vacios, una lista de metodos vacia impide arrancar."""
    with pytest.raises(ValidationError) as excinfo:
        _settings(monkeypatch, environ={"CORS_ALLOWED_METHODS": ""})

    assert "vacio" in str(excinfo.value)


def test_ca8_empty_methods_allowed_when_origins_are_empty(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """CA-8: si CORS esta deshabilitado, los metodos vacios se ignoran."""
    settings = _settings(
        monkeypatch,
        environ={"CORS_ALLOWED_ORIGINS": "", "CORS_ALLOWED_METHODS": ""},
    )

    assert settings.cors_allowed_origins == []
    assert settings.cors_allowed_methods == []


def test_ca8_methods_are_uppercased_and_deduplicated(monkeypatch: pytest.MonkeyPatch) -> None:
    """CA-8: `"get, GET, Post"` => `["GET", "POST"]`."""
    settings = _settings(monkeypatch, environ={"CORS_ALLOWED_METHODS": "get, GET, Post"})

    assert settings.cors_allowed_methods == ["GET", "POST"]


# CA-9 -------------------------------------------------------------------------


def test_ca9_invalid_header_name_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    """CA-9: un nombre de cabecera con espacios no es un token HTTP valido."""
    with pytest.raises(ValidationError) as excinfo:
        _settings(monkeypatch, environ={"CORS_ALLOWED_HEADERS": "Authorization, Bad Header"})

    assert "CORS_ALLOWED_HEADERS" in str(excinfo.value)


def test_ca9_empty_headers_rejected_when_origins_are_set(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """CA-9: con origenes no vacios, una lista de cabeceras vacia impide arrancar."""
    with pytest.raises(ValidationError) as excinfo:
        _settings(monkeypatch, environ={"CORS_ALLOWED_HEADERS": ""})

    assert "vacio" in str(excinfo.value)


def test_ca9_empty_headers_allowed_when_origins_are_empty(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """CA-9: si CORS esta deshabilitado, las cabeceras vacias se ignoran."""
    settings = _settings(
        monkeypatch,
        environ={"CORS_ALLOWED_ORIGINS": "", "CORS_ALLOWED_HEADERS": ""},
    )

    assert settings.cors_allowed_origins == []
    assert settings.cors_allowed_headers == []


def test_ca9_headers_are_canonicalized_and_deduplicated(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """CA-9: safelist canonica, primera grafia conservada y dedupe case-insensitive."""
    settings = _settings(
        monkeypatch,
        environ={
            "CORS_ALLOWED_HEADERS": "content-type, authorization, Content-Type, AUTHORIZATION"
        },
    )

    assert settings.cors_allowed_headers == ["Content-Type", "authorization"]
