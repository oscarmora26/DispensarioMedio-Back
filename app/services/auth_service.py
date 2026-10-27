"""Casos de uso de autenticacion."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import structlog
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.exceptions import AppError, ForbiddenError, RateLimitError, UnauthorizedError
from app.core.rate_limit import LoginRateLimiter, build_key, get_login_rate_limiter
from app.core.security import (
    create_access_token,
    generate_refresh_token,
    hash_password,
    hash_refresh_token,
    verify_password,
)
from app.models.refresh_token import RefreshToken
from app.models.user import User
from app.repositories import refresh_token_repository, user_repository

logger = structlog.get_logger(__name__)

INVALID_CREDENTIALS_MESSAGE = "Usuario o contraseña incorrectos"
INACTIVE_USER_MESSAGE = "El usuario está inactivo"
RATE_LIMITED_MESSAGE = "Demasiados intentos fallidos. Intente de nuevo más tarde"
INVALID_REFRESH_TOKEN_MESSAGE = "Token inválido"

#: Ventana en la que dos refrescos concurrentes del mismo token se consideran
#: una carrera benigna del cliente y no se revoca la familia (FEAT-020 D-4).
REFRESH_REUSE_GRACE_SECONDS = 10

# Hash Argon2id de relleno: se verifica cuando el usuario no existe para que el
# tiempo de respuesta no revele si la cuenta existe (mitiga la enumeracion).
_DUMMY_PASSWORD = "dummy-password-for-timing-only"
_DUMMY_HASH = hash_password(_DUMMY_PASSWORD)


def refresh_ttl_seconds() -> int:
    """Vigencia del refresh token en segundos, leida de las settings vigentes."""
    return get_settings().refresh_token_expire_days * 86400


async def _issue_refresh_token(
    session: AsyncSession,
    *,
    user_id: int,
    family_id: uuid.UUID,
    client_ip: str | None,
    user_agent: str | None,
    now: datetime,
) -> tuple[str, RefreshToken]:
    """Crea un refresh token nuevo en la sesion y devuelve `(plano, fila)`.

    Solo el hash se persiste: el valor plano existe unicamente en la respuesta.
    """
    plain = generate_refresh_token()
    row = RefreshToken(
        user_id=user_id,
        token_hash=hash_refresh_token(plain),
        family_id=family_id,
        expires_at=now + timedelta(days=get_settings().refresh_token_expire_days),
        created_by_ip=client_ip,
        user_agent=user_agent[:255] if user_agent else None,
    )
    refresh_token_repository.add(session, row)
    return plain, row


@dataclass(frozen=True, slots=True)
class LoginResult:
    """Resultado de un login exitoso."""

    access_token: str
    expires_in: int
    token_type: str
    refresh_token: str
    refresh_expires_in: int
    user: User


@dataclass(frozen=True, slots=True)
class RefreshResult:
    """Resultado de una renovacion exitosa."""

    access_token: str
    expires_in: int
    token_type: str
    refresh_token: str
    refresh_expires_in: int
    user: User


async def login(
    session: AsyncSession,
    *,
    username: str | None = None,
    email: str | None = None,
    password: str,
    client_ip: str,
    user_agent: str | None = None,
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

    now = datetime.now(UTC)
    user.last_login_at = now

    # Limpieza perezosa: los tokens expirados de este usuario no se necesitan (regla 11).
    await refresh_token_repository.delete_expired_for_user(session, user_id=user.id, now=now)

    # El refresh token se persiste en el mismo commit que `last_login_at` (regla 10).
    refresh_plain, _ = await _issue_refresh_token(
        session,
        user_id=user.id,
        family_id=uuid.uuid4(),
        client_ip=client_ip,
        user_agent=user_agent,
        now=now,
    )

    await session.commit()
    # Un login exitoso reinicia la ventana de esa clave (regla 25).
    rate_limiter.reset(key)

    logger.info("auth.login_succeeded", user_id=user.id, role=role_name)
    return LoginResult(
        access_token=access_token,
        expires_in=expires_in,
        token_type="bearer",
        refresh_token=refresh_plain,
        refresh_expires_in=refresh_ttl_seconds(),
        user=user,
    )


async def refresh(
    session: AsyncSession,
    *,
    refresh_token: str,
    client_ip: str | None = None,
    user_agent: str | None = None,
) -> RefreshResult:
    """Rota un refresh token vigente y devuelve un par nuevo (reglas 12-21)."""
    now = datetime.now(UTC)
    token_hash = hash_refresh_token(refresh_token)
    row = await refresh_token_repository.get_by_hash_for_update(session, token_hash)

    if row is None:
        logger.info("auth.refresh_failed", reason="unknown_token")
        raise UnauthorizedError(INVALID_REFRESH_TOKEN_MESSAGE, code="invalid_token")

    if row.revoked_at is not None:
        logger.info("auth.refresh_failed", reason="revoked", user_id=row.user_id)
        raise UnauthorizedError(INVALID_REFRESH_TOKEN_MESSAGE, code="invalid_token")

    if row.expires_at <= now:
        logger.info("auth.refresh_failed", reason="expired", user_id=row.user_id)
        raise UnauthorizedError(INVALID_REFRESH_TOKEN_MESSAGE, code="invalid_token")

    if row.replaced_by_id is not None:
        await _handle_reuse(session, row=row, now=now)
        raise UnauthorizedError(INVALID_REFRESH_TOKEN_MESSAGE, code="invalid_token")

    user = await user_repository.get_user_by_id(session, row.user_id)
    if user is None:
        logger.info("auth.refresh_failed", reason="unknown_user", user_id=row.user_id)
        raise UnauthorizedError(INVALID_REFRESH_TOKEN_MESSAGE, code="invalid_token")

    if not user.is_active:
        await refresh_token_repository.revoke_family(
            session,
            family_id=row.family_id,
            reason="user_deactivated",
            now=now,
        )
        await session.commit()
        logger.info("auth.refresh_failed", reason="inactive", user_id=user.id)
        raise UnauthorizedError(INVALID_REFRESH_TOKEN_MESSAGE, code="invalid_token")

    # Claims actuales leidos de la base: un cambio de rol o de medico viaja
    # en el access token siguiente, no en el token viejo (regla 18).
    access_token, expires_in = create_access_token(
        subject=str(user.id),
        role=user.role.name,
        doctor_id=user.doctor_id,
    )

    successor_plain, successor = await _issue_refresh_token(
        session,
        user_id=user.id,
        family_id=row.family_id,
        client_ip=client_ip,
        user_agent=user_agent,
        now=now,
    )
    await session.flush()

    row.replaced_by_id = successor.id
    row.last_used_at = now

    await session.commit()

    logger.info(
        "auth.refresh_succeeded",
        user_id=user.id,
        family_id=str(row.family_id),
    )
    return RefreshResult(
        access_token=access_token,
        expires_in=expires_in,
        token_type="bearer",
        refresh_token=successor_plain,
        refresh_expires_in=refresh_ttl_seconds(),
        user=user,
    )


async def _handle_reuse(session: AsyncSession, *, row: RefreshToken, now: datetime) -> None:
    """Resuelve el uso de un token ya rotado (regla 16).

    Dentro de la ventana de gracia se asume una carrera benigna del cliente y no
    se toca la familia. Fuera de ella se trata como robo y se revoca la cadena.
    """
    within_grace = (
        row.last_used_at is not None
        and (now - row.last_used_at).total_seconds() <= REFRESH_REUSE_GRACE_SECONDS
    )
    if within_grace:
        logger.info(
            "auth.refresh_failed",
            reason="reused_within_grace",
            user_id=row.user_id,
            family_id=str(row.family_id),
        )
        return

    revoked = await refresh_token_repository.revoke_family(
        session,
        family_id=row.family_id,
        reason="reuse_detected",
        now=now,
    )
    await session.commit()
    logger.warning(
        "auth.refresh_reuse_detected",
        user_id=row.user_id,
        family_id=str(row.family_id),
        revoked_count=revoked,
    )


async def logout(session: AsyncSession, *, refresh_token: str) -> None:
    """Revoca el refresh token recibido; idempotente y sin oraculo (reglas 22-24)."""
    token_hash = hash_refresh_token(refresh_token)
    row = await refresh_token_repository.get_by_hash_for_update(session, token_hash)

    # Token inexistente, ya revocado o ya rotado: mismo 204 sin distinguir el caso.
    if row is None or row.revoked_at is not None or row.replaced_by_id is not None:
        return

    row.revoked_at = datetime.now(UTC)
    row.revoked_reason = "logout"
    await session.commit()
    logger.info("auth.logout", user_id=row.user_id)


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
    # Cascada: la contrasena anterior deja de autenticar y las sesiones mueren (regla 25).
    await refresh_token_repository.revoke_all_for_user(
        session,
        user_id=user.id,
        reason="password_change",
        now=datetime.now(UTC),
    )
    await session.commit()
    logger.info("auth.password_changed", user_id=user.id)
