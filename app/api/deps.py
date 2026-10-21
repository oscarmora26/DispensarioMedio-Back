"""Dependencias reutilizables de autenticacion y autorizacion."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Annotated

import jwt
from fastapi import Depends
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import ForbiddenError, UnauthorizedError
from app.core.security import decode_access_token
from app.db.session import get_session
from app.models.user import User
from app.repositories import user_repository

bearer_scheme = HTTPBearer(auto_error=False, scheme_name="BearerAuth")

ALLOWED_ROLES = frozenset({"ADMIN", "MEDICO"})

NOT_AUTHENTICATED_MESSAGE = "No autenticado"
INVALID_TOKEN_MESSAGE = "Token inválido"
INACTIVE_USER_MESSAGE = "El usuario está inactivo"
FORBIDDEN_MESSAGE = "No tiene permisos para realizar esta operación"


async def get_current_user(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer_scheme)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> User:
    """Resuelve el usuario del token leyendo rol y estado desde la base."""
    if credentials is None or credentials.scheme.lower() != "bearer":
        raise UnauthorizedError(NOT_AUTHENTICATED_MESSAGE, code="not_authenticated")

    try:
        payload = decode_access_token(credentials.credentials)
    except jwt.InvalidTokenError:
        raise UnauthorizedError(INVALID_TOKEN_MESSAGE, code="invalid_token") from None

    try:
        user_id = int(payload["sub"])
    except (KeyError, TypeError, ValueError):
        raise UnauthorizedError(INVALID_TOKEN_MESSAGE, code="invalid_token") from None

    user = await user_repository.get_user_by_id(session, user_id)
    if user is None:
        raise UnauthorizedError(INVALID_TOKEN_MESSAGE, code="invalid_token")

    # Un usuario desactivado pierde el acceso en la siguiente request (regla 13).
    if not user.is_active:
        raise ForbiddenError(INACTIVE_USER_MESSAGE, code="inactive_user")

    return user


CurrentUser = Annotated[User, Depends(get_current_user)]


def require_role(*roles: str) -> Callable[..., Awaitable[User]]:
    """Construye una dependencia que exige alguno de los roles indicados."""
    unknown = set(roles) - ALLOWED_ROLES
    if not roles or unknown:
        raise ValueError(f"Roles no válidos para require_role: {sorted(roles)}")

    async def dependency(user: CurrentUser) -> User:
        if user.role.name not in roles:
            raise ForbiddenError(FORBIDDEN_MESSAGE, code="forbidden")
        return user

    return dependency
