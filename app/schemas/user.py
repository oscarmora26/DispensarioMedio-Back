"""Schemas del contrato de administracion de usuarios."""

from __future__ import annotations

import re

from pydantic import BaseModel, ConfigDict, Field, field_validator
from pydantic_core import PydanticCustomError

# Catalogo cerrado de ordenacion del listado (FEAT-004 D-3).
SORT_FIELDS = ("username", "email", "created_at", "last_login_at", "id")
SORT_PATTERN = r"^-?(?:username|email|created_at|last_login_at|id)$"

_USERNAME_PATTERN = re.compile(r"\S{4,50}")
_EMAIL_PATTERN = re.compile(r"[^@\s]+@[^@\s]+\.[^@\s]+")


class _UserIdentity(BaseModel):
    """Campos de identidad compartidos por alta y reemplazo completo."""

    username: str = Field(
        min_length=4,
        max_length=50,
        description="Nombre de usuario, sin espacios, entre 4 y 50 caracteres",
    )
    email: str = Field(max_length=255, description="Correo electronico")

    @field_validator("username", "email", mode="before")
    @classmethod
    def _strip_identity(cls, value: object) -> object:
        return value.strip() if isinstance(value, str) else value

    @field_validator("username")
    @classmethod
    def _validate_username(cls, value: str) -> str:
        if not _USERNAME_PATTERN.fullmatch(value):
            raise PydanticCustomError(
                "username_format",
                "El nombre de usuario no puede contener espacios",
            )
        return value

    @field_validator("email")
    @classmethod
    def _validate_email(cls, value: str) -> str:
        if not _EMAIL_PATTERN.fullmatch(value):
            raise PydanticCustomError(
                "email_format",
                "El correo electrónico no tiene un formato válido",
            )
        return value


class UserCreate(_UserIdentity):
    """Alta de usuario; `password` nunca se devuelve ni se registra."""

    password: str = Field(min_length=8, max_length=128, description="Contrasena")
    role_id: int = Field(description="Identificador del rol")
    doctor_id: int | None = Field(default=None, description="Medico asociado, si aplica")
    is_active: bool = Field(default=True, description="Estado inicial de la cuenta")


class UserUpdate(_UserIdentity):
    """Reemplazo completo de un usuario (FEAT-004 D-1)."""

    role_id: int = Field(description="Identificador del rol")
    doctor_id: int | None = Field(description="Medico asociado; obligatorio en el body")
    is_active: bool = Field(description="Estado resultante de la cuenta")


class UserResetPasswordRequest(BaseModel):
    """Restablecimiento administrativo de la contrasena."""

    new_password: str = Field(min_length=8, max_length=128, description="Nueva contrasena")


class UserRead(BaseModel):
    """Forma publica de un usuario (FEAT-004 D-4)."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    username: str
    email: str
    role: str
    doctor_id: int | None
    is_active: bool


class UserListResponse(BaseModel):
    """Pagina de usuarios."""

    items: list[UserRead]
    total: int
    page: int
    size: int
    pages: int
