"""Unitarias sin base de datos de FEAT-010 (U-1 a U-6 del plan de pruebas)."""

from __future__ import annotations

import re

import pytest
from pydantic import ValidationError

from app.core.exceptions import AppError
from app.repositories.medication_repository import escape_like
from app.schemas.medication import (
    SORT_FIELDS,
    SORT_PATTERN,
    MedicationCreate,
    MedicationRead,
    MedicationUpdate,
)
from app.services.medication_service import calculate_pages, parse_sort


def _field_of(exc: ValidationError, *fields: str) -> None:
    """Comprueba que los errores de validación señalan exactamente los campos indicados."""
    assert {tuple(error["loc"]) for error in exc.errors()} == {(field,) for field in fields}, (
        exc.errors()
    )


def _valid_payload(**overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "description": "Acetaminofén",
        "drug_type_id": 1,
        "brand_id": 3,
        "location_id": 7,
        "dose": "500 mg",
    }
    payload.update(overrides)
    return payload


def test_u1_create_requires_five_fields_strips_and_defaults_active() -> None:
    """U-1/CA-13: cinco campos obligatorios, `strip`, 1..100, `is_active` default `true`."""
    created = MedicationCreate(**_valid_payload())  # type: ignore[arg-type]
    assert created.is_active is True
    assert MedicationCreate(**_valid_payload(is_active=False)).is_active is False  # type: ignore[arg-type]

    spaced = MedicationCreate(**_valid_payload(description="  Acetaminofén  ", dose="  500 mg  "))  # type: ignore[arg-type]
    assert spaced.description == "Acetaminofén"
    assert spaced.dose == "500 mg"
    assert MedicationCreate(**_valid_payload(description="aCeTa")).description == "aCeTa"  # type: ignore[arg-type]

    with pytest.raises(ValidationError) as missing_all:
        MedicationCreate()  # type: ignore[call-arg]
    _field_of(missing_all.value, "description", "drug_type_id", "brand_id", "location_id", "dose")

    base = _valid_payload()
    for field in ("description", "drug_type_id", "brand_id", "location_id", "dose"):
        payload = dict(base)
        del payload[field]
        with pytest.raises(ValidationError) as missing:
            MedicationCreate(**payload)  # type: ignore[arg-type]
        _field_of(missing.value, field)

    for field in ("description", "dose"):
        for invalid in ("", "   "):
            with pytest.raises(ValidationError) as empty:
                MedicationCreate(**_valid_payload(**{field: invalid}))  # type: ignore[arg-type]
            _field_of(empty.value, field)

        with pytest.raises(ValidationError) as too_long:
            MedicationCreate(**_valid_payload(**{field: "B" * 101}))  # type: ignore[arg-type]
        _field_of(too_long.value, field)

        assert getattr(MedicationCreate(**_valid_payload(**{field: "Q"})), field) == "Q"  # type: ignore[arg-type]
        assert getattr(MedicationCreate(**_valid_payload(**{field: "B" * 100})), field) == "B" * 100  # type: ignore[arg-type]

    for field in ("drug_type_id", "brand_id", "location_id"):
        for invalid in (0, -1):
            with pytest.raises(ValidationError) as bad_fk:
                MedicationCreate(**_valid_payload(**{field: invalid}))  # type: ignore[arg-type]
            _field_of(bad_fk.value, field)


def test_u2_update_requires_all_six_fields_and_accepts_false() -> None:
    """U-2/CA-21: reemplazo completo; los seis campos obligatorios."""
    payload = MedicationUpdate(**_valid_payload(is_active=False))  # type: ignore[arg-type]
    assert payload.description == "Acetaminofén"
    assert payload.dose == "500 mg"
    assert payload.is_active is False

    stripped = MedicationUpdate(**_valid_payload(description="  Med  ", is_active=True))  # type: ignore[arg-type]
    assert stripped.description == "Med"

    base = _valid_payload(is_active=True)
    for field in ("description", "drug_type_id", "brand_id", "location_id", "dose", "is_active"):
        partial = dict(base)
        del partial[field]
        with pytest.raises(ValidationError) as missing:
            MedicationUpdate(**partial)  # type: ignore[arg-type]
        _field_of(missing.value, field)

    with pytest.raises(ValidationError) as empty:
        MedicationUpdate(**_valid_payload(dose="   ", is_active=True))  # type: ignore[arg-type]
    _field_of(empty.value, "dose")


def test_u3_read_serializes_exactly_seven_keys() -> None:
    """U-3/CA-10: `MedicationRead` expone exactamente las siete claves planas de D-4."""
    body = MedicationRead(
        id=8,
        description="Acetaminofén",
        drug_type_id=1,
        brand_id=3,
        location_id=7,
        dose="500 mg",
        is_active=True,
    ).model_dump()
    assert set(body) == {
        "id",
        "description",
        "drug_type_id",
        "brand_id",
        "location_id",
        "dose",
        "is_active",
    }
    assert body == {
        "id": 8,
        "description": "Acetaminofén",
        "drug_type_id": 1,
        "brand_id": 3,
        "location_id": 7,
        "dose": "500 mg",
        "is_active": True,
    }


def test_u4_sort_catalog_accepts_sixteen_variants_and_rejects_the_rest() -> None:
    """U-4/CA-6: catálogo de `sort` con las 16 variantes válidas (8 campos x 2)."""
    assert SORT_FIELDS == (
        "description",
        "dose",
        "drug_type_id",
        "brand_id",
        "location_id",
        "created_at",
        "is_active",
        "id",
    )
    compiled = re.compile(SORT_PATTERN)
    valid = {
        "description": ("description", False),
        "-description": ("description", True),
        "dose": ("dose", False),
        "-dose": ("dose", True),
        "drug_type_id": ("drug_type_id", False),
        "-drug_type_id": ("drug_type_id", True),
        "brand_id": ("brand_id", False),
        "-brand_id": ("brand_id", True),
        "location_id": ("location_id", False),
        "-location_id": ("location_id", True),
        "created_at": ("created_at", False),
        "-created_at": ("created_at", True),
        "is_active": ("is_active", False),
        "-is_active": ("is_active", True),
        "id": ("id", False),
        "-id": ("id", True),
    }
    for raw, expected in valid.items():
        assert compiled.fullmatch(raw), raw
        assert parse_sort(raw) == expected

    for invalid in (
        "",
        "password_hash",
        "dose,dose",
        "descriptionx",
        "dose,description",
        "-",
        "--id",
        "Description",
        "DOSE",
    ):
        assert not compiled.fullmatch(invalid), invalid
        with pytest.raises(AppError) as exc:
            parse_sort(invalid)
        assert exc.value.status_code == 422
        assert exc.value.code == "validation_error"
        assert exc.value.errors == [{"field": "sort", "message": "Valor de orden no permitido"}]


def test_u5_like_escape_makes_wildcards_literal() -> None:
    """U-5/CA-7: `%`, `_` y `\\` se escapan para `ILIKE ... ESCAPE '\\'`."""
    assert escape_like("%") == "\\%"
    assert escape_like("_") == "\\_"
    assert escape_like("\\") == "\\\\"
    assert escape_like("100% dosis_trigo\\maiz") == "100\\% dosis\\_trigo\\\\maiz"
    assert escape_like("Acetaminofén") == "Acetaminofén"


@pytest.mark.parametrize(
    ("total", "size", "expected"),
    [(0, 20, 0), (0, 1, 0), (1, 20, 1), (20, 20, 1), (21, 20, 2), (100, 100, 1), (101, 100, 2)],
)
def test_u6_pages_is_zero_without_rows_and_ceils_the_rest(
    total: int, size: int, expected: int
) -> None:
    """U-6/CA-4/CA-9: `pages = ceil(total/size)` y `0` cuando `total == 0`."""
    assert calculate_pages(total, size) == expected
