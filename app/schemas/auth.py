"""Schemas del contrato de autenticacion."""

from typing import Literal

from pydantic import BaseModel, Field, ValidationInfo, field_validator
from pydantic_core import PydanticCustomError


class LoginRequest(BaseModel):
    """Credenciales de acceso."""

    username: str = Field(min_length=1, max_length=50, description="Nombre de usuario")
    password: str = Field(min_length=1, max_length=128, description="Contrasena")

    @field_validator("username", mode="before")
    @classmethod
    def _strip_username(cls, value: object) -> object:
        return value.strip() if isinstance(value, str) else value


class LoginUser(BaseModel):
    """Identidad publica devuelta por el login."""

    id: int
    username: str
    email: str
    role: str
    doctor_id: int | None


class LoginResponse(BaseModel):
    """Respuesta de un login exitoso."""

    access_token: str
    token_type: Literal["bearer"] = "bearer"
    expires_in: int = Field(description="Vigencia del token en segundos")
    user: LoginUser


class MeResponse(BaseModel):
    """Identidad del usuario autenticado."""

    id: int
    username: str
    email: str
    role: str
    doctor_id: int | None
    is_active: bool


class ChangePasswordRequest(BaseModel):
    """Cambio de contrasena del propio usuario autenticado."""

    current_password: str = Field(min_length=1, max_length=128)
    new_password: str = Field(min_length=8, max_length=128)
    confirm_password: str = Field(min_length=1, max_length=128)

    @field_validator("confirm_password")
    @classmethod
    def _passwords_match(cls, value: str, info: ValidationInfo) -> str:
        new_password = info.data.get("new_password")
        if new_password is not None and value != new_password:
            raise PydanticCustomError("password_mismatch", "Las contraseñas no coinciden")
        return value


class MessageResponse(BaseModel):
    """Mensaje informativo de una operacion exitosa."""

    message: str
