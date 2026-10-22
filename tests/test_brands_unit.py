"""Unitarias sin base de datos de FEAT-008 (U-1 a U-6 del plan de pruebas)."""

from __future__ import annotations

import re

import pytest
from pydantic import ValidationError

from app.core.exceptions import AppError
from app.repositories.brand_repository import escape_like
from app.schemas.brand import (
    SORT_FIELDS,
    SORT_PATTERN,
    BrandCreate,
    BrandRead,
    BrandUpdate,
)
from app.services.brand_service import calculate_pages, parse_sort


def _field_of(exc: ValidationError, field: str) -> None:
    """Comprueba que el unico error de validacion senala el campo indicado."""
    assert len(exc.errors()) == 1, exc.errors()
    assert tuple(exc.errors()[0]["loc"]) == (field,), exc.errors()


def test_u1_create_requires_description_strips_and_defaults_active() -> None:
    """U-1/CA-13: `description` obligatoria, `strip`, 1..100, default `true`."""
    assert BrandCreate(description="Bayer").is_active is True
    assert BrandCreate(description="  Bayer  ").description == "Bayer"
    assert BrandCreate(description="Bayer", is_active=False).is_active is False
    # La capitalizacion se conserva (D-6).
    assert BrandCreate(description="bAyEr").description == "bAyEr"

    with pytest.raises(ValidationError) as missing:
        BrandCreate()  # type: ignore[call-arg]
    _field_of(missing.value, "description")

    for invalid in ("", "   "):
        with pytest.raises(ValidationError) as empty:
            BrandCreate(description=invalid)
        _field_of(empty.value, "description")

    with pytest.raises(ValidationError) as too_long:
        BrandCreate(description="B" * 101)
    _field_of(too_long.value, "description")

    assert BrandCreate(description="B").description == "B"
    assert BrandCreate(description="B" * 100).description == "B" * 100


def test_u2_update_requires_both_fields_and_accepts_false() -> None:
    """U-2/CA-19: reemplazo completo; ambos campos obligatorios."""
    payload = BrandUpdate(description=" Bayer MX ", is_active=False)
    assert payload.description == "Bayer MX"
    assert payload.is_active is False

    with pytest.raises(ValidationError) as no_active:
        BrandUpdate(description="Bayer")  # type: ignore[call-arg]
    _field_of(no_active.value, "is_active")

    with pytest.raises(ValidationError) as no_description:
        BrandUpdate(is_active=True)  # type: ignore[call-arg]
    _field_of(no_description.value, "description")

    with pytest.raises(ValidationError) as empty:
        BrandUpdate(description="   ", is_active=True)
    _field_of(empty.value, "description")


def test_u3_read_serializes_exactly_three_keys() -> None:
    """U-3/CA-10: `BrandRead` expone exactamente `id`, `description`, `is_active`."""
    body = BrandRead(id=1, description="Bayer", is_active=True).model_dump()
    assert set(body) == {"id", "description", "is_active"}
    assert body == {"id": 1, "description": "Bayer", "is_active": True}


def test_u4_sort_catalog_accepts_eight_variants_and_rejects_the_rest() -> None:
    """U-4/CA-6: catalogo de `sort` con las 8 variantes validas."""
    assert SORT_FIELDS == ("description", "created_at", "is_active", "id")
    compiled = re.compile(SORT_PATTERN)
    valid = {
        "description": ("description", False),
        "-description": ("description", True),
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

    for invalid in ("", "password_hash", "description,description", "-", "--id", "Description"):
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
    assert escape_like("Bayer") == "Bayer"


@pytest.mark.parametrize(
    ("total", "size", "expected"),
    [(0, 20, 0), (0, 1, 0), (1, 20, 1), (20, 20, 1), (21, 20, 2), (100, 100, 1), (101, 100, 2)],
)
def test_u6_pages_is_zero_without_rows_and_ceils_the_rest(
    total: int, size: int, expected: int
) -> None:
    """U-6/CA-4/CA-9: `pages = ceil(total/size)` y `0` cuando `total == 0`."""
    assert calculate_pages(total, size) == expected
