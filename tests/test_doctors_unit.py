"""Unitarias sin base de datos de FEAT-012 (validadores y helpers)."""

from __future__ import annotations

import re

import pytest
from pydantic import ValidationError

from app.core.exceptions import AppError
from app.schemas.doctor import (
    CEDULA_FORMAT_MESSAGE,
    LAB_SHIFT_REQUIRED_MESSAGE,
    NAME_REQUIRED_MESSAGE,
    SORT_FIELDS,
    SORT_PATTERN,
    SPECIALTY_REQUIRED_MESSAGE,
    DoctorCreate,
    DoctorRead,
    DoctorUpdate,
    DoctorUserRead,
)
from app.services.doctor_service import calculate_pages, parse_sort


def _field_of(exc: ValidationError, field: str) -> None:
    """Comprueba que el unico error de validacion senala el campo indicado."""
    assert len(exc.errors()) == 1, exc.errors()
    assert tuple(exc.errors()[0]["loc"]) == (field,), exc.errors()


def _message_of(exc: ValidationError) -> str:
    """Devuelve el mensaje del unico error de validacion."""
    assert len(exc.errors()) == 1, exc.errors()
    return str(exc.errors()[0]["msg"])


def test_doctor_create_defaults_active_and_strips_text() -> None:
    """D-5 / regla 9: `is_active` por defecto `true`, texto recortado."""
    created = DoctorCreate(
        name="  Dra. Ana Pérez  ",
        cedula=" 00198765432 ",
        lab_shift=" Matutina ",
        specialty=" Medicina General ",
    )
    assert created.is_active is True
    assert created.name == "Dra. Ana Pérez"
    assert created.cedula == "00198765432"
    assert created.lab_shift == "Matutina"
    assert created.specialty == "Medicina General"
    assert (
        DoctorCreate(
            name="Ana",
            cedula="00198765432",
            lab_shift="Matutina",
            specialty="General",
            is_active=False,
        ).is_active
        is False
    )


def test_ca19_name_is_required_and_bounded() -> None:
    """CA-19: `name` vacio o de solo espacios ⇒ 422 en `name`; 150 ok, 151 no."""
    for invalid in ("", "   "):
        with pytest.raises(ValidationError) as empty:
            DoctorCreate(
                name=invalid,
                cedula="00198765432",
                lab_shift="Matutina",
                specialty="General",
            )
        _field_of(empty.value, "name")
        assert _message_of(empty.value) == NAME_REQUIRED_MESSAGE

    assert (
        DoctorCreate(
            name="a" * 150,
            cedula="00198765432",
            lab_shift="Matutina",
            specialty="General",
        ).name
        == "a" * 150
    )

    with pytest.raises(ValidationError) as too_long:
        DoctorCreate(
            name="a" * 151,
            cedula="00198765432",
            lab_shift="Matutina",
            specialty="General",
        )
    _field_of(too_long.value, "name")


def test_ca18_cedula_requires_exactly_eleven_ascii_digits() -> None:
    """CA-18: 11 digitos exactos con ceros iniciales; el resto ⇒ mensaje literal."""
    assert (
        DoctorCreate(
            name="Ana", cedula="00198765432", lab_shift="Matutina", specialty="General"
        ).cedula
        == "00198765432"
    )

    invalid_values = (
        "",
        "0019876543",
        "001987654321",
        "0019876543a",
        "001-9876543",
        "00198 65432",
        "٠٠١٩٨٧٦٥٤٣٢",
    )
    for invalid in invalid_values:
        with pytest.raises(ValidationError) as excinfo:
            DoctorCreate(name="Ana", cedula=invalid, lab_shift="Matutina", specialty="General")
        _field_of(excinfo.value, "cedula")
        assert _message_of(excinfo.value) == CEDULA_FORMAT_MESSAGE


def test_ca20_lab_shift_is_required_and_bounded() -> None:
    """CA-20: `lab_shift` vacio ⇒ 422 en `lab_shift`; 50 ok, 51 no."""
    for invalid in ("", "   "):
        with pytest.raises(ValidationError) as empty:
            DoctorCreate(name="Ana", cedula="00198765432", lab_shift=invalid, specialty="General")
        _field_of(empty.value, "lab_shift")
        assert _message_of(empty.value) == LAB_SHIFT_REQUIRED_MESSAGE

    assert (
        DoctorCreate(
            name="Ana",
            cedula="00198765432",
            lab_shift="t" * 50,
            specialty="General",
        ).lab_shift
        == "t" * 50
    )

    with pytest.raises(ValidationError) as too_long:
        DoctorCreate(name="Ana", cedula="00198765432", lab_shift="t" * 51, specialty="General")
    _field_of(too_long.value, "lab_shift")


def test_ca20_specialty_is_required_and_bounded() -> None:
    """CA-20: `specialty` vacio ⇒ 422 en `specialty`; 100 ok, 101 no."""
    for invalid in ("", "   "):
        with pytest.raises(ValidationError) as empty:
            DoctorCreate(name="Ana", cedula="00198765432", lab_shift="Matutina", specialty=invalid)
        _field_of(empty.value, "specialty")
        assert _message_of(empty.value) == SPECIALTY_REQUIRED_MESSAGE

    assert (
        DoctorCreate(
            name="Ana",
            cedula="00198765432",
            lab_shift="Matutina",
            specialty="e" * 100,
        ).specialty
        == "e" * 100
    )

    with pytest.raises(ValidationError) as too_long:
        DoctorCreate(name="Ana", cedula="00198765432", lab_shift="Matutina", specialty="e" * 101)
    _field_of(too_long.value, "specialty")


def test_update_requires_is_active_and_rejects_partial_body() -> None:
    """CA-27: `DoctorUpdate` exige `is_active`; un body parcial ⇒ 422."""
    with pytest.raises(ValidationError) as missing_active:
        DoctorUpdate(name="Ana", cedula="00198765432", lab_shift="Matutina", specialty="General")
    _field_of(missing_active.value, "is_active")

    updated = DoctorUpdate(
        name="Ana",
        cedula="00198765432",
        lab_shift="Vespertina",
        specialty="Pediatría",
        is_active=False,
    )
    assert updated.is_active is False


def test_ca5_calculate_pages_and_ca11_sort_catalog() -> None:
    """CA-9/CA-11: `pages` cero cuando no hay filas y catalogo de orden cerrado."""
    assert calculate_pages(0, 20) == 0
    assert calculate_pages(1, 20) == 1
    assert calculate_pages(20, 20) == 1
    assert calculate_pages(21, 20) == 2

    assert SORT_FIELDS == (
        "name",
        "cedula",
        "lab_shift",
        "specialty",
        "created_at",
        "is_active",
        "id",
    )
    assert re.fullmatch(SORT_PATTERN, "name")
    assert re.fullmatch(SORT_PATTERN, "-specialty")
    assert re.fullmatch(SORT_PATTERN, "id")
    assert not re.fullmatch(SORT_PATTERN, "")
    assert not re.fullmatch(SORT_PATTERN, "doctor")
    assert not re.fullmatch(SORT_PATTERN, "updated_at")
    assert not re.fullmatch(SORT_PATTERN, "name,cedula")

    assert parse_sort("name") == ("name", False)
    assert parse_sort("-lab_shift") == ("lab_shift", True)

    with pytest.raises(AppError) as invalid:
        parse_sort("doctor")
    assert invalid.value.code == "validation_error"
    assert invalid.value.errors[0]["field"] == "sort"


def test_read_serializes_exactly_seven_keys() -> None:
    """CA-14/D-2 + FEAT-018: la lectura es plana con `user` como unica anidada."""
    read = DoctorRead(
        id=5,
        name="Dra. Ana Pérez",
        cedula="00198765432",
        lab_shift="Matutina",
        specialty="Medicina General",
        is_active=True,
    )
    assert set(read.model_dump()) == {
        "id",
        "name",
        "cedula",
        "lab_shift",
        "specialty",
        "is_active",
        "user",
    }
    # Sin cuenta asociada el campo viaja explicito como `null`.
    assert read.user is None


def test_doctor_user_read_exposes_only_three_keys() -> None:
    """FEAT-018: el resumen del usuario no filtra email, rol ni hash."""
    account = DoctorUserRead(id=7, username="ana", is_active=True)

    assert set(account.model_dump()) == {"id", "username", "is_active"}
