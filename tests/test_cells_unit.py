"""Unitarias sin base de datos de celdas (FEAT-017: schemas y utilidades)."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from app.repositories.cell_repository import SORT_COLUMNS, escape_like
from app.schemas.shelf import CellCreate, CellRead, CellUpdate
from app.services.cell_service import calculate_pages, parse_sort

SHELF_ID_FIELD = "shelf_id"
POSITION_FIELDS = ("section", "cell")


def _locs(exc: ValidationError) -> set[tuple[object, ...]]:
    """Devuelve el `loc` de cada error de validación."""
    return {tuple(error["loc"]) for error in exc.errors()}


def _cell_payload(**overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {"shelf_id": 1, "section": "A", "cell": "1"}
    payload.update(overrides)
    return payload


def test_cell_create_requires_shelf_id_and_defaults_active() -> None:
    """`CellCreate`: `shelf_id` obligatorio (>=1) e `is_active=true`."""
    created = CellCreate(**_cell_payload())
    assert created.shelf_id == 1
    assert created.is_active is True
    assert created.description is None

    with pytest.raises(ValidationError) as missing:
        CellCreate(section="A", cell="1")  # type: ignore[call-arg]
    assert _locs(missing.value) == {(SHELF_ID_FIELD,)}

    for invalid in (0, -1, None, "abc"):
        with pytest.raises(ValidationError) as exc:
            CellCreate(**_cell_payload(**{SHELF_ID_FIELD: invalid}))
        assert _locs(exc.value) == {(SHELF_ID_FIELD,)}, invalid

    assert CellCreate(**_cell_payload(is_active=False)).is_active is False


def test_cell_create_validates_position_and_strips_it() -> None:
    """`section` y `cell` son obligatorios, 1..50 y se recortan en el schema."""
    assert CellCreate(**_cell_payload(section="  A  ", cell="  1  ")).section == "A"
    assert CellCreate(**_cell_payload(section="  A  ", cell=" 1 ")).cell == "1"
    assert CellCreate(**_cell_payload(section="B" * 50, cell="C" * 50)).section == "B" * 50

    for field in POSITION_FIELDS:
        for invalid in ("", "   ", "B" * 51, None, 123):
            with pytest.raises(ValidationError) as exc:
                CellCreate(**_cell_payload(**{field: invalid}))
            assert _locs(exc.value) == {(field,)}, (field, invalid)

    with pytest.raises(ValidationError) as too_long_description:
        CellCreate(**_cell_payload(description="D" * 201))
    assert _locs(too_long_description.value) == {("description",)}


def test_cell_write_schemas_forbid_extra_fields() -> None:
    """Ningún schema de escritura acepta campos de la representación de lectura."""
    with pytest.raises(ValidationError) as read_only_fields:
        CellCreate(**_cell_payload(id=7, created_at=None, updated_at=None))
    assert _locs(read_only_fields.value) == {("id",), ("created_at",), ("updated_at",)}

    with pytest.raises(ValidationError) as extra:
        CellUpdate(**_cell_payload(is_active=True, medications=[]))
    assert _locs(extra.value) == {("medications",)}


def test_cell_update_requires_the_full_replacement() -> None:
    """`CellUpdate` exige `shelf_id`, `section`, `cell` e `is_active`."""
    payload = CellUpdate(**_cell_payload(description="Fondo", is_active=False))
    assert payload.shelf_id == 1
    assert payload.description == "Fondo"
    assert payload.is_active is False

    base: dict[str, object] = {
        "shelf_id": 1,
        "section": "A",
        "cell": "1",
        "is_active": True,
    }
    for field in ("shelf_id", "section", "cell", "is_active"):
        partial = {key: value for key, value in base.items() if key != field}
        with pytest.raises(ValidationError) as missing:
            CellUpdate(**partial)  # type: ignore[arg-type]
        assert _locs(missing.value) == {(field,)}


def test_cell_schemas_keep_empty_description_and_default_cell_active() -> None:
    """El schema deja `""` intacto: la normalización a `NULL` es del servicio (R-10)."""
    created = CellCreate(**_cell_payload(description=""))
    assert created.description == ""
    assert created.is_active is True

    updated = CellUpdate(**_cell_payload(description="   ", is_active=False))
    assert updated.description == "   "
    assert updated.is_active is False


def test_cell_read_serializes_exact_keys() -> None:
    """`CellRead` expone las ocho claves del contrato."""
    timestamp = datetime(2026, 10, 27, 12, 0, tzinfo=UTC)
    body = CellRead(
        id=2,
        shelf_id=1,
        section="A",
        cell="1",
        description=None,
        is_active=True,
        created_at=timestamp,
        updated_at=timestamp,
    ).model_dump()

    assert set(body) == {
        "id",
        "shelf_id",
        "section",
        "cell",
        "description",
        "is_active",
        "created_at",
        "updated_at",
    }
    assert body["description"] is None
    assert body["created_at"] == timestamp


def test_cell_sort_catalog_and_parse_sort() -> None:
    """El catálogo de `sort` cubre las seis columnas y `parse_sort` separa el prefijo."""
    assert set(SORT_COLUMNS) == {"section", "cell", "shelf_id", "created_at", "is_active", "id"}

    for field in SORT_COLUMNS:
        assert parse_sort(field) == (field, False)
        assert parse_sort(f"-{field}") == (field, True)
    assert parse_sort("--id") == ("-id", True)


def test_like_escape_makes_wildcards_literal() -> None:
    """`%`, `_` y `\\` se escapan para `ILIKE ... ESCAPE '\\'`."""
    assert escape_like("%") == "\\%"
    assert escape_like("_") == "\\_"
    assert escape_like("\\") == "\\\\"
    assert escape_like("Sección_A 100%\\fondo") == "Sección\\_A 100\\%\\\\fondo"
    assert escape_like("Seccion A") == "Seccion A"


@pytest.mark.parametrize(
    ("total", "size", "expected"),
    [(0, 20, 0), (0, 1, 0), (1, 20, 1), (20, 20, 1), (21, 20, 2), (100, 100, 1), (101, 100, 2)],
)
def test_pages_is_zero_without_rows_and_ceils_the_rest(
    total: int, size: int, expected: int
) -> None:
    """`pages = ceil(total/size)` y `0` cuando `total == 0`."""
    assert calculate_pages(total, size) == expected
