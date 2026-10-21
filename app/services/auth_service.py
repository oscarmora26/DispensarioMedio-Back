"""Casos de uso de autenticacion."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

import structlog
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import AppError, ForbiddenError, RateLimitError, UnauthorizedError
from app.core.rate_limit import LoginRateLimiter, build_key, get_login_rate_limiter
from app.core.security import create_access_token, hash_password, verify_password
from app.models.user import User
from app.repositories import user_repository

logger = structlog.get_logger(__name__)

INVALID_CREDENTIALS_MESSAGE = "Usuario o contraseña incorrectos"
INACTIVE_USER_MESSAGE = "El usuario está inactivo"
RATE_LIMITED_MESSAGE = "Demasiados intentos fallidos. Intente de nuevo más tarde"

# Hash Argon2id de relleno: se verifica cuando el usuario no existe para que el
# tiempo de respuesta no revele si la cuenta existe (mitiga la enumeracion).
_DUMMY_PASSWORD = "dummy-password-for-timing-only"
_DUMMY_HASH = hash_password(_DUMMY_PASSWORD)


@dataclass(frozen=True, slots=True)
class LoginResult:
    """Resultado de un login exitoso."""

    access_token: str
    expires_in: int
    token_type: str
    user: User


async def login(
    session: AsyncSession,
    *,
    username: str | None = None,
    email: str | None = None,
    password: str,
    client_ip: str,
    limiter: LoginRateLimiter | None = None,
) -> LoginResult:
    """Autentica por `username` o `email`, aplicando el rate limit del login."""
    if (username is None) == (email is None):
        raise ValueError("login requiere exactamente uno de 'username' o 'email'")

    rate_limiter = limiter if limiter is not None else get_login_rate_limiter()
    identifier = (email or username or "").strip()
    key = build_key(client_ip, identifier)

    retry_after = rate_limiter.retry_after(key)
    if retry_after is not None:
        logger.info("auth.rate_limited")
        raise RateLimitError(RATE_LIMITED_MESSAGE, retry_after=retry_after)

    if email is not None:
        user = await user_repository.get_user_by_email(session, identifier)
    else:
        user = await user_repository.get_user_by_username(session, identifier)
    if user is None:
        # Verificacion ficticia para igualar el coste de Argon2 (anti-enumeracion).
        verify_password(password, _DUMMY_HASH)
        rate_limiter.record_failure(key)
        logger.info("auth.login_failed", reason="unknown_user")
        raise UnauthorizedError(INVALID_CREDENTIALS_MESSAGE, code="invalid_credentials")

    if not verify_password(password, user.password_hash):
        rate_limiter.record_failure(key)
        logger.info("auth.login_failed", reason="bad_password")
        raise UnauthorizedError(INVALID_CREDENTIALS_MESSAGE, code="invalid_credentials")

    # El estado se evalua despues de la contrasena para no revelar cuentas inactivas (D-2).
    if not user.is_active:
        logger.info("auth.login_failed", reason="inactive")
        raise ForbiddenError(INACTIVE_USER_MESSAGE, code="inactive_user")

    role_name = user.role.name
    doctor_id = user.doctor_id

    # Se emite el token antes de confirmar el login: si fallara, no quedaria un
    # `last_login_at` registrado sin respuesta.
    access_token, expires_in = create_access_token(
        subject=str(user.id),
        role=role_name,
        doctor_id=doctor_id,
    )

    user.last_login_at = datetime.now(UTC)
    await session.commit()
    # Un login exitoso reinicia la ventana de esa clave (regla 25).
    rate_limiter.reset(key)

    logger.info("auth.login_succeeded", user_id=user.id, role=role_name)
    return LoginResult(
        access_token=access_token,
        expires_in=expires_in,
        token_type="bearer",
        user=user,
    )


async def change_password(
    session: AsyncSession,
    user: User,
    *,
    current_password: str,
    new_password: str,
) -> None:
    """Cambia la contrasena del propio usuario autenticado."""
    if not verify_password(current_password, user.password_hash):
        raise AppError(
            "La contraseña actual es incorrecta",
            status_code=400,
            code="invalid_current_password",
        )

    if new_password == current_password:
        raise AppError(
            "La nueva contraseña debe ser distinta de la actual",
            status_code=400,
            code="password_unchanged",
        )

    user.password_hash = hash_password(new_password)
    await session.commit()
    logger.info("auth.password_changed", user_id=user.id)
