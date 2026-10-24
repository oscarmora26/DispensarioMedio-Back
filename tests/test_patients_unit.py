"""Unitarias sin base de datos de FEAT-011 (validadores y helpers)."""

from __future__ import annotations

import re

import pytest
from pydantic import ValidationError

from app.core.exceptions import AppError
from app.schemas.patient import (
    CARNET_REQUIRED_MESSAGE,
    CEDULA_FORMAT_MESSAGE,
    NAME_REQUIRED_MESSAGE,
    PATIENT_TYPE_MESSAGE,
    PATIENT_TYPE_VALUES,
    SORT_FIELDS,
    SORT_PATTERN,
    PatientCreate,
    PatientRead,
    PatientUpdate,
)
from app.services.patient_service import calculate_pages, parse_sort


def _field_of(exc: ValidationError, field: str) -> None:
    """Comprueba que el unico error de validacion senala el campo indicado."""
    assert len(exc.errors()) == 1, exc.errors()
    assert tuple(exc.errors()[0]["loc"]) == (field,), exc.errors()


def _message_of(exc: ValidationError) -> str:
    """Devuelve el mensaje del unico error de validacion."""
    assert len(exc.errors()) == 1, exc.errors()
    return str(exc.errors()[0]["msg"])


def test_patient_create_defaults_active_and_strips_text() -> None:
    """D-5 / regla 7: `is_active` por defecto `true`, texto recortado."""
    created = PatientCreate(
        name="  Oscar Mora  ",
        cedula=" 00112345678 ",
        carnet=" a00112548 ",
        patient_type="ESTUDIANTE",
    )
    assert created.is_active is True
    assert created.name == "Oscar Mora"
    assert created.cedula == "00112345678"
    assert created.carnet == "a00112548"
    assert (
        PatientCreate(
            name="Oscar", cedula="00112345678", patient_type="EMPLEADO", is_active=False
        ).is_active
        is False
    )


def test_ca19_name_is_required_and_bounded() -> None:
    """CA-19: `name` vacio o de solo espacios ⇒ 422 en `name`; 150 ok, 151 no."""
    for invalid in ("", "   "):
        with pytest.raises(ValidationError) as empty:
            PatientCreate(name=invalid, cedula="00112345678", patient_type="EMPLEADO")
        _field_of(empty.value, "name")
        assert _message_of(empty.value) == NAME_REQUIRED_MESSAGE

    assert (
        PatientCreate(name="a" * 150, cedula="00112345678", patient_type="EMPLEADO").name
        == "a" * 150
    )

    with pytest.raises(ValidationError) as too_long:
        PatientCreate(name="a" * 151, cedula="00112345678", patient_type="EMPLEADO")
    _field_of(too_long.value, "name")


def test_ca16_cedula_requires_exactly_eleven_ascii_digits() -> None:
    """CA-16: 11 digitos exactos con ceros iniciales; el resto ⇒ mensaje literal."""
    assert (
        PatientCreate(name="Oscar", cedula="00112345678", patient_type="EMPLEADO").cedula
        == "00112345678"
    )

    invalid_values = (
        "",
        "0011234567",
        "001123456789",
        "0011234567a",
        "001-1234567",
        "00112 34567",
        "٠٠١١٢٣٤٥٦٧٨",
    )
    for invalid in invalid_values:
        with pytest.raises(ValidationError) as excinfo:
            PatientCreate(name="Oscar", cedula=invalid, patient_type="EMPLEADO")
        _field_of(excinfo.value, "cedula")
        assert _message_of(excinfo.value) == CEDULA_FORMAT_MESSAGE


def test_ca18_patient_type_is_a_closed_uppercase_catalog() -> None:
    """CA-18: `patient_type` fuera del catalogo o en minusculas ⇒ 422."""
    assert PATIENT_TYPE_VALUES == ("ESTUDIANTE", "EMPLEADO", "PROFESOR", "OTROS")
    for value in ("estudiante", "Paciente", "ADMIN", ""):
        with pytest.raises(ValidationError) as excinfo:
            PatientCreate(name="Oscar", cedula="00112345678", patient_type=value)
        _field_of(excinfo.value, "patient_type")
        assert _message_of(excinfo.value) == PATIENT_TYPE_MESSAGE


def test_ca17_student_requires_carnet() -> None:
    """CA-17: `ESTUDIANTE` sin carnet o con solo espacios ⇒ 422 en `carnet`."""
    with pytest.raises(ValidationError) as missing:
        PatientCreate(name="Oscar", cedula="00112345678", patient_type="ESTUDIANTE")
    _field_of(missing.value, "carnet")
    assert _message_of(missing.value) == CARNET_REQUIRED_MESSAGE

    with pytest.raises(ValidationError) as blank:
        PatientCreate(
            name="Oscar",
            cedula="00112345678",
            patient_type="ESTUDIANTE",
            carnet="   ",
        )
    _field_of(blank.value, "carnet")
    assert _message_of(blank.value) == CARNET_REQUIRED_MESSAGE


def test_ca20_carnet_is_optional_for_non_students_and_blank_means_null() -> None:
    """CA-20: carnet opcional fuera de `ESTUDIANTE`; espacios ⇒ `null`."""
    for patient_type in ("EMPLEADO", "PROFESOR", "OTROS"):
        created = PatientCreate(name="Oscar", cedula="00112345678", patient_type=patient_type)
        assert created.carnet is None

        blank = PatientCreate(
            name="Oscar",
            cedula="00112345678",
            patient_type=patient_type,
            carnet="   ",
        )
        assert blank.carnet is None

    with pytest.raises(ValidationError) as too_long:
        PatientCreate(
            name="Oscar",
            cedula="00112345678",
            patient_type="EMPLEADO",
            carnet="c" * 51,
        )
    _field_of(too_long.value, "carnet")


def test_ca28_update_requires_is_active_and_treats_absent_carnet_as_null() -> None:
    """D-1 / CA-28: reemplazo completo; `carnet` ausente se interpreta `null`."""
    updated = PatientUpdate(
        name="Oscar", cedula="00112345678", patient_type="EMPLEADO", is_active=True
    )
    assert updated.carnet is None

    with pytest.raises(ValidationError) as missing:
        PatientUpdate(name="Oscar", cedula="00112345678", patient_type="EMPLEADO")  # type: ignore[call-arg]
    _field_of(missing.value, "is_active")


def test_ca11_read_shape_is_exact() -> None:
    """CA-11 / D-4: la forma de lectura tiene exactamente seis claves planas."""
    read = PatientRead(
        id=1,
        name="Oscar",
        cedula="00112345678",
        carnet=None,
        patient_type="EMPLEADO",
        is_active=True,
    )
    assert set(read.model_dump()) == {
        "id",
        "name",
        "cedula",
        "carnet",
        "patient_type",
        "is_active",
    }


def test_ca5_calculate_pages_and_ca7_sort_catalog() -> None:
    """CA-5 / CA-7: formula de paginas y catalogo cerrado de orden."""
    assert calculate_pages(0, 20) == 0
    assert calculate_pages(1, 20) == 1
    assert calculate_pages(20, 20) == 1
    assert calculate_pages(21, 20) == 2

    assert SORT_FIELDS == ("name", "cedula", "created_at", "is_active", "id")
    assert parse_sort("name") == ("name", False)
    assert parse_sort("-cedula") == ("cedula", True)
    assert re.fullmatch(SORT_PATTERN, "-created_at") is not None
    assert re.fullmatch(SORT_PATTERN, "password_hash") is None

    with pytest.raises(AppError) as invalid:
        parse_sort("password_hash")
    assert invalid.value.status_code == 422
    assert invalid.value.code == "validation_error"
    assert invalid.value.errors == [{"field": "sort", "message": "Valor de orden no permitido"}]
