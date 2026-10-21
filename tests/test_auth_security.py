"""Tests unitarios de configuracion y primitivas de seguridad (FEAT-002)."""

from __future__ import annotations

import inspect
from datetime import UTC, datetime
from pathlib import Path

import jwt
import pytest
from pydantic import ValidationError

from app.core.config import DEFAULT_JWT_SECRET, Settings
from app.core.security import (
    create_access_token,
    decode_access_token,
    hash_password,
    verify_password,
)

ROOT = Path(__file__).resolve().parent.parent
JWT_KEYS = (
    "JWT_SECRET",
    "JWT_ALGORITHM",
    "JWT_EXPIRE_MINUTES",
    "LOGIN_RATE_LIMIT_MAX_ATTEMPTS",
    "LOGIN_RATE_LIMIT_WINDOW_SECONDS",
)


@pytest.fixture(autouse=True)
def _clean_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    """Aisla los tests de configuracion del entorno del desarrollador."""
    for key in (*JWT_KEYS, "ENVIRONMENT", "ADMIN_PASSWORD"):
        monkeypatch.delenv(key, raising=False)


def test_ca1_env_example_documents_jwt_and_rate_limit_keys() -> None:
    """CA-1: `.env.example` documenta las cinco claves nuevas."""
    content = (ROOT / ".env.example").read_text(encoding="utf-8")
    missing = [f"{key}=" for key in JWT_KEYS if f"{key}=" not in content]

    assert missing == []
    assert "ADMIN_PASSWORD" in content


def test_ca1_placeholder_secret_only_lives_in_config() -> None:
    """CA-1: ningun secreto real ni el placeholder aparecen fuera de `config.py`."""
    files = [
        path.name
        for path in (ROOT / "app").rglob("*.py")
        if DEFAULT_JWT_SECRET in path.read_text(encoding="utf-8")
    ]

    assert files == ["config.py"]


def test_ca2_settings_defaults() -> None:
    """CA-2: defaults de JWT y rate limiting."""
    settings = Settings(_env_file=None)

    assert settings.jwt_algorithm == "HS256"
    assert settings.jwt_expire_minutes == 30
    assert settings.login_rate_limit_max_attempts == 5
    assert settings.login_rate_limit_window_seconds == 900


@pytest.mark.parametrize(
    "overrides",
    [
        {"jwt_expire_minutes": 0},
        {"jwt_expire_minutes": -1},
        {"login_rate_limit_max_attempts": 0},
        {"login_rate_limit_max_attempts": -5},
        {"login_rate_limit_window_seconds": 0},
        {"login_rate_limit_window_seconds": -1},
    ],
)
def test_settings_reject_non_positive_values(overrides: dict[str, int]) -> None:
    """Minor ciclo 1: los settings numericos deben ser estrictamente positivos."""
    with pytest.raises(ValidationError):
        Settings(_env_file=None, **overrides)


def test_decode_access_token_does_not_accept_expires_minutes() -> None:
    """Minor ciclo 1: `decode_access_token` ya no expone un parametro inutil."""
    assert "expires_minutes" not in inspect.signature(decode_access_token).parameters

    token, _ = create_access_token(
        subject="7", role="ADMIN", doctor_id=None, expires_minutes=30, secret="s" * 32
    )
    with pytest.raises(TypeError):
        decode_access_token(token, expires_minutes=30)  # type: ignore[call-arg]


def test_ca3_prod_rejects_example_and_short_secrets() -> None:
    """CA-3: produccion exige un `JWT_SECRET` propio de al menos 32 caracteres."""
    with pytest.raises(ValidationError, match="JWT_SECRET"):
        Settings(_env_file=None, environment="prod", jwt_secret=DEFAULT_JWT_SECRET)

    with pytest.raises(ValidationError, match="JWT_SECRET"):
        Settings(_env_file=None, environment="prod", jwt_secret="corto")

    valid = Settings(_env_file=None, environment="prod", jwt_secret="x" * 48)
    assert valid.jwt_secret == "x" * 48


def test_ca3_local_and_test_start_without_env_file() -> None:
    """CA-3: `local` y `test` arrancan sin `.env`."""
    assert Settings(_env_file=None).environment == "local"
    assert Settings(_env_file=None, environment="test").jwt_expire_minutes == 30


def test_ca3_only_hs256_is_accepted() -> None:
    """CA-3: `JWT_ALGORITHM` restringido a HS256."""
    with pytest.raises(ValidationError):
        Settings(_env_file=None, jwt_algorithm="RS256")  # type: ignore[arg-type]


def test_ca5_hash_is_salted_and_verifies() -> None:
    """CA-5: Argon2id con sal aleatoria."""
    first = hash_password("Secreta123")
    second = hash_password("Secreta123")

    assert first.startswith("$argon2id$")
    assert verify_password("Secreta123", first) is True
    assert verify_password("otra", first) is False
    assert first != second


def test_ca8_unknown_hash_returns_false() -> None:
    """CA-8: un hash bcrypt desconocido no propaga `UnknownHashError`."""
    assert verify_password("x", "$2b$12$hash-invalido") is False


def test_ca6_token_round_trip_has_exact_claims() -> None:
    """CA-6: claims exactos del JWT."""
    token, expires_in = create_access_token(
        subject="7",
        role="MEDICO",
        doctor_id=5,
        expires_minutes=30,
        secret="s" * 32,
    )
    payload = decode_access_token(token, secret="s" * 32)

    assert expires_in == 1800
    assert sorted(payload) == ["doctor_id", "exp", "iat", "role", "sub"]
    assert payload["sub"] == "7"
    assert payload["role"] == "MEDICO"
    assert payload["doctor_id"] == 5
    assert payload["exp"] - payload["iat"] == 1800


def test_ca7_expired_token_raises_expired_signature() -> None:
    """CA-7: `expires_minutes=-1` produce un token expirado."""
    token, _ = create_access_token(
        subject="7", role="ADMIN", doctor_id=None, expires_minutes=-1, secret="s" * 32
    )

    with pytest.raises(jwt.ExpiredSignatureError):
        decode_access_token(token, secret="s" * 32)


@pytest.mark.parametrize(
    "mutator",
    ["other_secret", "tampered", "missing_exp"],
)
def test_ca7_invalid_tokens_raise_invalid_token_error(mutator: str) -> None:
    """CA-7: firma ajena, token alterado y token sin `exp` son invalidos."""
    secret = "s" * 32
    token, _ = create_access_token(
        subject="7", role="ADMIN", doctor_id=None, expires_minutes=30, secret=secret
    )

    if mutator == "other_secret":
        with pytest.raises(jwt.InvalidTokenError):
            decode_access_token(token, secret="t" * 32)
        return

    if mutator == "tampered":
        replacement = "A" if token[-1] != "A" else "B"
        tampered = token[:-1] + replacement
        with pytest.raises(jwt.InvalidTokenError):
            decode_access_token(tampered, secret=secret)
        return

    without_exp = jwt.encode(
        {"sub": "7", "role": "ADMIN", "iat": int(datetime.now(UTC).timestamp())},
        secret,
        algorithm="HS256",
    )
    with pytest.raises(jwt.InvalidTokenError):
        decode_access_token(without_exp, secret=secret)


def test_ca9_custom_expiry_and_string_subject() -> None:
    """CA-9: `expires_minutes=45` y `sub` siempre string."""
    token, expires_in = create_access_token(
        subject=42, role="ADMIN", doctor_id=None, expires_minutes=45, secret="s" * 32
    )
    payload = decode_access_token(token, secret="s" * 32)

    assert expires_in == 2700
    assert payload["exp"] - payload["iat"] == 2700
    assert payload["sub"] == "42"
    assert isinstance(payload["sub"], str)
