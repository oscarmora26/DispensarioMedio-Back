"""Schemas del contrato de autenticacion."""

from __future__ import annotations

import re
from typing import Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    ValidationInfo,
    field_validator,
    model_validator,
)
from pydantic_core import PydanticCustomError

_EMAIL_PATTERN = re.compile(r"[^@\s]+@[^@\s]+\.[^@\s]+")
_IDENTIFIER_DESCRIPTION = "Envía exactamente uno: `email` o `username`"


class LoginRequest(BaseModel):
    """Credenciales de acceso por nombre de usuario o correo electronico."""

    model_config = ConfigDict(
        json_schema_extra={
            "examples": [
                {"email": "aperez@dispensario.local", "password": "MiClaveSegura123"},
                {"username": "aperez", "password": "MiClaveSegura123"},
            ]
        }
    )

    email: str | None = Field(
        default=None,
        max_length=255,
        description=f"Correo electronico. {_IDENTIFIER_DESCRIPTION}",
    )
    username: str | None = Field(
        default=None,
        min_length=1,
        max_length=50,
        description=f"Nombre de usuario. {_IDENTIFIER_DESCRIPTION}",
    )
    password: str = Field(min_length=1, max_length=128, description="Contrasena")

    @field_validator("email", "username", mode="before")
    @classmethod
    def _strip_identifier(cls, value: object) -> object:
        return value.strip() if isinstance(value, str) else value

    @field_validator("email")
    @classmethod
    def _validate_email(cls, value: str | None) -> str | None:
        if value is None:
            return None
        if not value:
            raise PydanticCustomError(
                "email_empty",
                "El correo electrónico no puede estar vacío",
            )
        if not _EMAIL_PATTERN.fullmatch(value):
            raise PydanticCustomError(
                "email_format",
                "El correo electrónico no tiene un formato válido",
            )
        return value

    @model_validator(mode="after")
    def _exactly_one_identifier(self) -> LoginRequest:
        if (self.email is None) == (self.username is None):
            raise PydanticCustomError(
                "identifier_required",
                "Indique 'username' o 'email' (exactamente uno)",
            )
        return self


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
