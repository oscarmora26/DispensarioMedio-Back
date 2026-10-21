"""Primitivas de seguridad: hashing de contrasenas y tokens JWT."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import jwt
from pwdlib import PasswordHash
from pwdlib.exceptions import UnknownHashError

from app.core.config import get_settings

_password_hash = PasswordHash.recommended()


def hash_password(password: str) -> str:
    """Genera un hash Argon2id con sal aleatoria para la contrasena indicada."""
    return _password_hash.hash(password)


def verify_password(password: str, password_hash: str) -> bool:
    """Comprueba una contrasena contra su hash; un hash desconocido o corrupto es `False`."""
    try:
        return _password_hash.verify(password, password_hash)
    except UnknownHashError:
        return False


def create_access_token(
    subject: str | int,
    role: str,
    doctor_id: int | None,
    *,
    expires_minutes: int | None = None,
    secret: str | None = None,
) -> tuple[str, int]:
    """Crea un JWT HS256 y devuelve `(token, expires_in)` en segundos.

    `secret` y `expires_minutes` permiten inyectar configuracion en pruebas; si no
    se indican se leen de `get_settings()` en el momento de la llamada.
    """
    settings = get_settings()
    token_secret = secret if secret is not None else settings.jwt_secret
    minutes = expires_minutes if expires_minutes is not None else settings.jwt_expire_minutes

    issued_at = int(datetime.now(UTC).timestamp())
    expires_in = minutes * 60
    payload = {
        "sub": str(subject),
        "role": role,
        "doctor_id": doctor_id,
        "iat": issued_at,
        "exp": issued_at + expires_in,
    }
    token = jwt.encode(payload, token_secret, algorithm=settings.jwt_algorithm)
    return token, expires_in


def decode_access_token(
    token: str,
    *,
    secret: str | None = None,
) -> dict[str, Any]:
    """Decodifica y valida un JWT, exigiendo `sub`, `iat` y `exp`.

    La expiracion se valida a partir del claim `exp` del propio token.
    """
    settings = get_settings()
    token_secret = secret if secret is not None else settings.jwt_secret

    payload: dict[str, Any] = jwt.decode(token, token_secret, algorithms=[settings.jwt_algorithm])
    for claim in ("sub", "iat", "exp"):
        if claim not in payload:
            raise jwt.InvalidTokenError(f"missing claim: {claim}")
    return payload
