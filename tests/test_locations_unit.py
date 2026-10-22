"""Unitarias sin base de datos de FEAT-009 (U-1 a U-6 del plan de pruebas)."""

from __future__ import annotations

import re

import pytest
from pydantic import ValidationError

from app.core.exceptions import AppError
from app.repositories.location_repository import escape_like
from app.schemas.location import (
    SORT_FIELDS,
    SORT_PATTERN,
    LocationCreate,
    LocationRead,
    LocationUpdate,
)
from app.services.location_service import calculate_pages, parse_sort

TEXT_FIELDS = ("description", "shelf", "section", "cell")


def _field_of(exc: ValidationError, *fields: str) -> None:
    """Comprueba que los errores de validación señalan exactamente los campos indicados."""
    assert {tuple(error["loc"]) for error in exc.errors()} == {(field,) for field in fields}, (
        exc.errors()
    )


def _valid_payload(**overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "description": "Estante A - Tramo 1 - Celda 2",
        "shelf": "A",
        "section": "1",
        "cell": "2",
    }
    payload.update(overrides)
    return payload


def test_u1_create_requires_four_texts_strips_and_defaults_active() -> None:
    """U-1/CA-14: los cuatro textos obligatorios, `strip`, 1..100, default `true`."""
    created = LocationCreate(**_valid_payload())
    assert created.is_active is True
    assert LocationCreate(**_valid_payload(is_active=False)).is_active is False

    spaced = LocationCreate(**_valid_payload(description="  Estante A  ", shelf="  A  "))
    assert spaced.description == "Estante A"
    assert spaced.shelf == "A"
    # La capitalización se conserva (D-6).
    assert LocationCreate(**_valid_payload(shelf="aBc")).shelf == "aBc"

    with pytest.raises(ValidationError) as missing_all:
        LocationCreate()  # type: ignore[call-arg]
    _field_of(missing_all.value, *TEXT_FIELDS)

    for field in TEXT_FIELDS:
        payload = _valid_payload()
        del payload[field]
        with pytest.raises(ValidationError) as missing:
            LocationCreate(**payload)  # type: ignore[arg-type]
        _field_of(missing.value, field)

        for invalid in ("", "   "):
            with pytest.raises(ValidationError) as empty:
                LocationCreate(**_valid_payload(**{field: invalid}))
            _field_of(empty.value, field)

        with pytest.raises(ValidationError) as too_long:
            LocationCreate(**_valid_payload(**{field: "B" * 101}))
        _field_of(too_long.value, field)

        assert getattr(LocationCreate(**_valid_payload(**{field: "Q"})), field) == "Q"
        assert getattr(LocationCreate(**_valid_payload(**{field: "B" * 100})), field) == ("B" * 100)


def test_u2_update_requires_all_five_fields_and_accepts_false() -> None:
    """U-2/CA-20: reemplazo completo; los cinco campos obligatorios."""
    payload = LocationUpdate(**_valid_payload(is_active=False))
    assert payload.description == "Estante A - Tramo 1 - Celda 2"
    assert payload.shelf == "A"
    assert payload.is_active is False

    stripped = LocationUpdate(**_valid_payload(section="  1  ", is_active=True))
    assert stripped.section == "1"

    base = _valid_payload(is_active=True)
    for field in (*TEXT_FIELDS, "is_active"):
        partial = dict(base)
        del partial[field]
        with pytest.raises(ValidationError) as missing:
            LocationUpdate(**partial)  # type: ignore[arg-type]
        _field_of(missing.value, field)

    with pytest.raises(ValidationError) as empty:
        LocationUpdate(**_valid_payload(cell="   ", is_active=True))
    _field_of(empty.value, "cell")


def test_u3_read_serializes_exactly_six_keys() -> None:
    """U-3/CA-10: `LocationRead` expone exactamente las seis claves de D-4."""
    body = LocationRead(
        id=1,
        description="Estante A - Tramo 1 - Celda 2",
        shelf="A",
        section="1",
        cell="2",
        is_active=True,
    ).model_dump()
    assert set(body) == {"id", "description", "shelf", "section", "cell", "is_active"}
    assert body == {
        "id": 1,
        "description": "Estante A - Tramo 1 - Celda 2",
        "shelf": "A",
        "section": "1",
        "cell": "2",
        "is_active": True,
    }


def test_u4_sort_catalog_accepts_fourteen_variants_and_rejects_the_rest() -> None:
    """U-4/CA-6: catálogo de `sort` con las 14 variantes válidas (7 campos x 2)."""
    assert SORT_FIELDS == (
        "description",
        "shelf",
        "section",
        "cell",
        "created_at",
        "is_active",
        "id",
    )
    compiled = re.compile(SORT_PATTERN)
    valid = {
        "description": ("description", False),
        "-description": ("description", True),
        "shelf": ("shelf", False),
        "-shelf": ("shelf", True),
        "section": ("section", False),
        "-section": ("section", True),
        "cell": ("cell", False),
        "-cell": ("cell", True),
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
        "description,description",
        "shelf,shelf",
        "descriptionx",
        "-",
        "--id",
        "Description",
        "SHELF",
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
    assert escape_like("100% algodon_trigo\\maiz") == "100\\% algodon\\_trigo\\\\maiz"
    assert escape_like("Estante A") == "Estante A"


@pytest.mark.parametrize(
    ("total", "size", "expected"),
    [(0, 20, 0), (0, 1, 0), (1, 20, 1), (20, 20, 1), (21, 20, 2), (100, 100, 1), (101, 100, 2)],
)
def test_u6_pages_is_zero_without_rows_and_ceils_the_rest(
    total: int, size: int, expected: int
) -> None:
    """U-6/CA-4/CA-9: `pages = ceil(total/size)` y `0` cuando `total == 0`."""
    assert calculate_pages(total, size) == expected
