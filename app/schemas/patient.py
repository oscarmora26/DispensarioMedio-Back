"""Schemas for the patient management contract (FEAT-011 D-4, D-5, D-6)."""

from __future__ import annotations

import re

from pydantic import BaseModel, ConfigDict, Field, ValidationInfo, field_validator
from pydantic_core import PydanticCustomError

# Closed patient type catalog (API spec section 9).
PATIENT_TYPE_VALUES = ("ESTUDIANTE", "EMPLEADO", "PROFESOR", "OTROS")

# Closed sort catalog of the patient listing (FEAT-011 D-3).
SORT_FIELDS = ("name", "cedula", "created_at", "is_active", "id")
SORT_PATTERN = r"^-?(?:name|cedula|created_at|is_active|id)$"

# A cedula is exactly eleven ASCII digits; leading zeros are valid.
CEDULA_PATTERN = re.compile(r"^[0-9]{11}$")

NAME_REQUIRED_MESSAGE = "El nombre es obligatorio"
CEDULA_FORMAT_MESSAGE = "La cédula debe tener 11 dígitos"
CARNET_REQUIRED_MESSAGE = "El carnet es obligatorio para estudiantes"
PATIENT_TYPE_MESSAGE = "El tipo de paciente no es válido"


class _PatientFields(BaseModel):
    """Fields shared by creation and full replacement.

    `patient_type` is declared before `carnet` on purpose: the carnet rule
    (rule 11) needs the already validated type inside a field validator so the
    error keeps `field == "carnet"` instead of a body-level location.
    """

    name: str = Field(
        max_length=150,
        description="Nombre del paciente, entre 1 y 150 caracteres",
    )
    cedula: str = Field(
        description="Cédula de exactamente 11 dígitos",
    )
    patient_type: str = Field(
        description="ESTUDIANTE, EMPLEADO, PROFESOR u OTROS",
    )
    carnet: str | None = Field(
        default=None,
        max_length=50,
        validate_default=True,
        description="Carnet; único cuando existe y obligatorio para estudiantes",
    )

    @field_validator("name", "cedula", "patient_type", "carnet", mode="before")
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

    @field_validator("patient_type")
    @classmethod
    def _validate_patient_type(cls, value: str) -> str:
        if value not in PATIENT_TYPE_VALUES:
            raise PydanticCustomError("patient_type_allowed", PATIENT_TYPE_MESSAGE)
        return value

    @field_validator("carnet", mode="after")
    @classmethod
    def _validate_carnet(cls, value: str | None, info: ValidationInfo) -> str | None:
        if value is not None and not value:
            value = None
        if value is None and info.data.get("patient_type") == "ESTUDIANTE":
            raise PydanticCustomError("carnet_required", CARNET_REQUIRED_MESSAGE)
        return value


class PatientCreate(_PatientFields):
    """Patient creation; `is_active` is optional and defaults to `true` (D-5)."""

    is_active: bool = Field(default=True, description="Estado inicial del paciente")


class PatientUpdate(_PatientFields):
    """Full replacement of a patient (D-1); `carnet` absent clears it (rule 28)."""

    is_active: bool = Field(description="Estado resultante del paciente")


class PatientRead(BaseModel):
    """Single patient shape (D-4): exactly six flat fields."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    cedula: str
    carnet: str | None
    patient_type: str
    is_active: bool


class PatientListResponse(BaseModel):
    """Page of patients."""

    items: list[PatientRead]
    total: int
    page: int
    size: int
    pages: int
