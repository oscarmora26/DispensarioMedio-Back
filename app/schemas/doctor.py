"""Schemas for the doctor management contract (FEAT-012 D-2, D-3, D-4)."""

from __future__ import annotations

import re

from pydantic import BaseModel, ConfigDict, Field, field_validator
from pydantic_core import PydanticCustomError

# Closed sort catalog of the doctor listing (FEAT-012 D-3).
SORT_FIELDS = ("name", "cedula", "lab_shift", "specialty", "created_at", "is_active", "id")
SORT_PATTERN = r"^-?(?:name|cedula|lab_shift|specialty|created_at|is_active|id)$"

# A cedula is exactly eleven ASCII digits; leading zeros are valid.
CEDULA_PATTERN = re.compile(r"^[0-9]{11}$")

NAME_REQUIRED_MESSAGE = "El nombre es obligatorio"
CEDULA_FORMAT_MESSAGE = "La cédula debe tener 11 dígitos"
LAB_SHIFT_REQUIRED_MESSAGE = "La tanda de trabajo es obligatoria"
SPECIALTY_REQUIRED_MESSAGE = "La especialidad es obligatoria"


class _DoctorFields(BaseModel):
    """Fields shared by creation and full replacement (D-1)."""

    name: str = Field(
        max_length=150,
        description="Nombre del médico, entre 1 y 150 caracteres",
    )
    cedula: str = Field(
        description="Cédula de exactamente 11 dígitos",
    )
    lab_shift: str = Field(
        max_length=50,
        description="Tanda de trabajo, entre 1 y 50 caracteres",
    )
    specialty: str = Field(
        max_length=100,
        description="Especialidad, entre 1 y 100 caracteres",
    )

    @field_validator("name", "cedula", "lab_shift", "specialty", mode="before")
    @classmethod
    def _strip_text(cls, value: object) -> object:
        return value.strip() if isinstance(value, str) else value

    @field_validator("name")
    @classmethod
    def _validate_name(cls, value: str) -> str:
        if not value:
            raise PydanticCustomError("name_empty", NAME_REQUIRED_MESSAGE)
        return value

    @field_validator("cedula")
    @classmethod
    def _validate_cedula(cls, value: str) -> str:
        if not CEDULA_PATTERN.fullmatch(value):
            raise PydanticCustomError("cedula_format", CEDULA_FORMAT_MESSAGE)
        return value

    @field_validator("lab_shift")
    @classmethod
    def _validate_lab_shift(cls, value: str) -> str:
        if not value:
            raise PydanticCustomError("lab_shift_empty", LAB_SHIFT_REQUIRED_MESSAGE)
        return value

    @field_validator("specialty")
    @classmethod
    def _validate_specialty(cls, value: str) -> str:
        if not value:
            raise PydanticCustomError("specialty_empty", SPECIALTY_REQUIRED_MESSAGE)
        return value


class DoctorCreate(_DoctorFields):
    """Doctor creation; `is_active` is optional and defaults to `true` (D-5)."""

    is_active: bool = Field(default=True, description="Estado inicial del médico")


class DoctorUpdate(_DoctorFields):
    """Full replacement of a doctor (D-1)."""

    is_active: bool = Field(description="Estado resultante del médico")


class DoctorRead(BaseModel):
    """Single doctor shape (D-2): exactly six flat fields."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    cedula: str
    lab_shift: str
    specialty: str
    is_active: bool


class DoctorListResponse(BaseModel):
    """Page of doctors."""

    items: list[DoctorRead]
    total: int
    page: int
    size: int
    pages: int
