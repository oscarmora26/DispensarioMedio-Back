"""Endpoints de autenticacion, sesion y cambio de contrasena."""

from typing import Annotated

from fastapi import APIRouter, Depends, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import CurrentUser
from app.core.rate_limit import LoginRateLimiter, get_login_rate_limiter
from app.db.session import get_session
from app.schemas.auth import (
    ChangePasswordRequest,
    LoginRequest,
    LoginResponse,
    LoginUser,
    MeResponse,
    MessageResponse,
)
from app.services import auth_service

router = APIRouter(prefix="/auth", tags=["auth"])

SessionDep = Annotated[AsyncSession, Depends(get_session)]
LoginRateLimiterDep = Annotated[LoginRateLimiter, Depends(get_login_rate_limiter)]


@router.post(
    "/login",
    response_model=LoginResponse,
    summary="Iniciar sesion",
    description=(
        "Autentica por nombre de usuario y contrasena y devuelve un JWT HS256. "
        "Aplica rate limiting por IP y usuario; el intento bloqueado responde 429."
    ),
    responses={
        status.HTTP_401_UNAUTHORIZED: {"description": "Credenciales invalidas"},
        status.HTTP_403_FORBIDDEN: {"description": "Usuario inactivo"},
        status.HTTP_422_UNPROCESSABLE_CONTENT: {"description": "Datos invalidos"},
        status.HTTP_429_TOO_MANY_REQUESTS: {"description": "Demasiados intentos fallidos"},
    },
)
async def login(
    request: Request,
    payload: LoginRequest,
    session: SessionDep,
    limiter: LoginRateLimiterDep,
) -> LoginResponse:
    """Devuelve un token Bearer para el usuario autenticado."""
    client_ip = request.client.host if request.client else "unknown"
    result = await auth_service.login(
        session,
        username=payload.username,
        password=payload.password,
        client_ip=client_ip,
        limiter=limiter,
    )
    user = result.user
    return LoginResponse(
        access_token=result.access_token,
        token_type="bearer",
        expires_in=result.expires_in,
        user=LoginUser(
            id=user.id,
            username=user.username,
            email=user.email,
            role=user.role.name,
            doctor_id=user.doctor_id,
        ),
    )


@router.get(
    "/me",
    response_model=MeResponse,
    summary="Obtener el usuario autenticado",
    description=(
        "Devuelve la identidad, el rol y el estado del usuario del token. "
        "El rol y el medico se leen de la base en cada request."
    ),
    responses={
        status.HTTP_401_UNAUTHORIZED: {"description": "No autenticado o token invalido"},
        status.HTTP_403_FORBIDDEN: {"description": "Usuario inactivo"},
        status.HTTP_422_UNPROCESSABLE_CONTENT: {"description": "Datos invalidos"},
    },
)
async def read_current_user(user: CurrentUser) -> MeResponse:
    """Devuelve los datos del usuario autenticado."""
    return MeResponse(
        id=user.id,
        username=user.username,
        email=user.email,
        role=user.role.name,
        doctor_id=user.doctor_id,
        is_active=user.is_active,
    )


@router.post(
    "/change-password",
    response_model=MessageResponse,
    summary="Cambiar la contrasena propia",
    description=(
        "Cambia la contrasena del usuario del token. Requiere la contrasena actual. "
        "La contrasena anterior deja de autenticar; los tokens vigentes no se revocan."
    ),
    responses={
        status.HTTP_400_BAD_REQUEST: {"description": "Contrasena actual incorrecta o sin cambios"},
        status.HTTP_401_UNAUTHORIZED: {"description": "No autenticado o token invalido"},
        status.HTTP_403_FORBIDDEN: {"description": "Usuario inactivo"},
        status.HTTP_422_UNPROCESSABLE_CONTENT: {"description": "Datos invalidos"},
    },
)
async def change_password(
    user: CurrentUser,
    payload: ChangePasswordRequest,
    session: SessionDep,
) -> MessageResponse:
    """Actualiza el hash de la contrasena del usuario autenticado."""
    await auth_service.change_password(
        session,
        user,
        current_password=payload.current_password,
        new_password=payload.new_password,
    )
    return MessageResponse(message="Contraseña actualizada correctamente")
