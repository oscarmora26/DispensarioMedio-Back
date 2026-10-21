"""Endpoints de administracion de usuarios (solo ADMIN)."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Query, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import require_role
from app.db.session import get_session
from app.models.user import User
from app.schemas.auth import MessageResponse
from app.schemas.user import (
    SORT_PATTERN,
    UserCreate,
    UserListResponse,
    UserRead,
    UserResetPasswordRequest,
    UserUpdate,
)
from app.services import user_service

router = APIRouter(prefix="/users", tags=["users"])

SessionDep = Annotated[AsyncSession, Depends(get_session)]
AdminUser = Annotated[User, Depends(require_role("ADMIN"))]

_COMMON_RESPONSES = {
    status.HTTP_401_UNAUTHORIZED: {"description": "No autenticado o token invalido"},
    status.HTTP_403_FORBIDDEN: {"description": "Usuario inactivo o sin rol ADMIN"},
}


@router.get(
    "",
    response_model=UserListResponse,
    summary="Listar usuarios",
    description=(
        "Devuelve una pagina de usuarios con busqueda parcial y case-insensitive en "
        "username/email, filtro opcional por estado y orden por el catalogo permitido. "
        "Solo accesible para ADMIN."
    ),
    responses={
        **_COMMON_RESPONSES,
        status.HTTP_422_UNPROCESSABLE_CONTENT: {"description": "Parametros invalidos"},
    },
)
async def list_users(
    session: SessionDep,
    _actor: AdminUser,
    page: Annotated[int, Query(ge=1, description="Pagina, desde 1")] = 1,
    size: Annotated[int, Query(ge=1, le=100, description="Elementos por pagina")] = 20,
    search: Annotated[
        str | None,
        Query(max_length=100, description="Busqueda parcial en username o email"),
    ] = None,
    is_active: Annotated[bool | None, Query(description="Filtra por estado")] = None,
    sort: Annotated[
        str,
        Query(
            pattern=SORT_PATTERN,
            description="username, email, created_at, last_login_at o id; prefijo '-' descendente",
        ),
    ] = "username",
) -> UserListResponse:
    """Lista usuarios con paginacion y filtros."""
    return await user_service.list_users(
        session,
        page=page,
        size=size,
        search=search,
        is_active=is_active,
        sort=sort,
    )


@router.post(
    "",
    response_model=UserRead,
    status_code=status.HTTP_201_CREATED,
    summary="Crear usuario",
    description=(
        "Crea una cuenta con rol ADMIN o MEDICO. La contrasena se almacena como hash "
        "Argon2id y nunca se devuelve. Solo accesible para ADMIN."
    ),
    responses={
        **_COMMON_RESPONSES,
        status.HTTP_400_BAD_REQUEST: {"description": "Rol o medico invalido, o ADMIN con medico"},
        status.HTTP_409_CONFLICT: {"description": "Usuario duplicado o medico ya asignado"},
        status.HTTP_422_UNPROCESSABLE_CONTENT: {"description": "Datos invalidos"},
    },
)
async def create_user(
    session: SessionDep,
    actor: AdminUser,
    payload: UserCreate,
) -> UserRead:
    """Crea un usuario."""
    return await user_service.create_user(session, actor=actor, payload=payload)


@router.get(
    "/{user_id}",
    response_model=UserRead,
    summary="Obtener un usuario",
    description=(
        "Devuelve la identidad publica de un usuario, incluido si esta inactivo. "
        "Solo accesible para ADMIN."
    ),
    responses={
        **_COMMON_RESPONSES,
        status.HTTP_404_NOT_FOUND: {"description": "El usuario no existe"},
        status.HTTP_422_UNPROCESSABLE_CONTENT: {"description": "Identificador invalido"},
    },
)
async def get_user(
    user_id: int,
    session: SessionDep,
    _actor: AdminUser,
) -> UserRead:
    """Devuelve un usuario por identificador."""
    return await user_service.get_user(session, user_id=user_id)


@router.put(
    "/{user_id}",
    response_model=UserRead,
    summary="Reemplazar un usuario",
    description=(
        "Reemplazo completo de username, email, role_id, doctor_id e is_active. "
        "No modifica la contrasena; para eso use reset-password. Solo accesible para ADMIN."
    ),
    responses={
        **_COMMON_RESPONSES,
        status.HTTP_400_BAD_REQUEST: {
            "description": (
                "Rol o medico invalido, ADMIN con medico, auto-desactivacion o ultimo ADMIN"
            )
        },
        status.HTTP_404_NOT_FOUND: {"description": "El usuario no existe"},
        status.HTTP_409_CONFLICT: {"description": "Usuario duplicado o medico ya asignado"},
        status.HTTP_422_UNPROCESSABLE_CONTENT: {"description": "Datos invalidos"},
    },
)
async def update_user(
    user_id: int,
    payload: UserUpdate,
    session: SessionDep,
    actor: AdminUser,
) -> UserRead:
    """Reemplaza los campos editables de un usuario."""
    return await user_service.update_user(
        session,
        actor=actor,
        user_id=user_id,
        payload=payload,
    )


@router.delete(
    "/{user_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    response_class=Response,
    summary="Desactivar usuario",
    description=(
        "Desactiva logicamente un usuario sin borrar la fila ni sus referencias. "
        "Es idempotente: repetir la operacion no modifica ni registra de nuevo. "
        "Solo accesible para ADMIN."
    ),
    responses={
        **_COMMON_RESPONSES,
        status.HTTP_400_BAD_REQUEST: {"description": "Auto-desactivacion o ultimo ADMIN"},
        status.HTTP_404_NOT_FOUND: {"description": "El usuario no existe"},
        status.HTTP_422_UNPROCESSABLE_CONTENT: {"description": "Identificador invalido"},
    },
)
async def delete_user(
    user_id: int,
    session: SessionDep,
    actor: AdminUser,
) -> Response:
    """Desactiva un usuario."""
    await user_service.deactivate_user(session, actor=actor, user_id=user_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post(
    "/{user_id}/reset-password",
    response_model=MessageResponse,
    summary="Restablecer contrasena",
    description=(
        "Reemplaza la contrasena de un usuario, este o no activo, sin exigir la actual. "
        "No reactiva la cuenta ni revoca los tokens vigentes. Solo accesible para ADMIN."
    ),
    responses={
        **_COMMON_RESPONSES,
        status.HTTP_404_NOT_FOUND: {"description": "El usuario no existe"},
        status.HTTP_422_UNPROCESSABLE_CONTENT: {"description": "Contrasena invalida"},
    },
)
async def reset_password(
    user_id: int,
    payload: UserResetPasswordRequest,
    session: SessionDep,
    actor: AdminUser,
) -> MessageResponse:
    """Restablece la contrasena de un usuario."""
    await user_service.reset_password(
        session,
        actor=actor,
        user_id=user_id,
        new_password=payload.new_password,
    )
    return MessageResponse(message="Contraseña restablecida correctamente")
