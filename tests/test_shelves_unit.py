"""Unitarias sin base de datos de FEAT-017 (schemas, `sort` y utilidades)."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from app.repositories.shelf_repository import escape_like
from app.schemas.shelf import (
    CellCreate,
    CellRead,
    CellUpdate,
    ShelfCreate,
    ShelfRead,
    ShelfUpdate,
)
from app.services.shelf_service import calculate_pages

NAME_FIELD = "name"
CELL_FIELDS = ("section", "cell")


def _locs(exc: ValidationError) -> set[tuple[object, ...]]:
    """Devuelve el `loc` de cada error de validación."""
    return {tuple(error["loc"]) for error in exc.errors()}


def _cell_payload(**overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {"section": "A", "cell": "1"}
    payload.update(overrides)
    return payload


def _shelf_payload(**overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {"name": "Estante A"}
    payload.update(overrides)
    return payload


def test_shelf_create_requires_name_and_defaults_active_without_cells() -> None:
    """`ShelfCreate`: `name` obligatorio 2..100, `is_active=true` y `cells=[]`."""
    created = ShelfCreate(**_shelf_payload())
    assert created.is_active is True
    assert created.cells == []
    assert created.description is None
    assert ShelfCreate(**_shelf_payload(is_active=False)).is_active is False

    with pytest.raises(ValidationError) as missing:
        ShelfCreate()  # type: ignore[call-arg]
    assert _locs(missing.value) == {(NAME_FIELD,)}

    for invalid in ("", "A", "B" * 101, None, 123):
        with pytest.raises(ValidationError) as exc:
            ShelfCreate(**_shelf_payload(**{NAME_FIELD: invalid}))
        assert _locs(exc.value) == {(NAME_FIELD,)}, invalid

    # El schema valida longitud, no contenido: recortar y normalizar es del servicio.
    assert ShelfCreate(**_shelf_payload(name="   ")).name == "   "

    assert ShelfCreate(**_shelf_payload(name="AB")).name == "AB"
    assert ShelfCreate(**_shelf_payload(name="B" * 100)).name == "B" * 100
    # La capitalización se conserva; el `CITEXT` de la base la compara sin caja.
    assert ShelfCreate(**_shelf_payload(name="aBc")).name == "aBc"

    with pytest.raises(ValidationError) as too_long_description:
        ShelfCreate(**_shelf_payload(description="D" * 201))
    assert _locs(too_long_description.value) == {("description",)}


def test_shelf_create_validates_nested_cells_and_forbids_extra_fields() -> None:
    """Las celdas anidadas se validan con su propio `loc` y no se acepta ruido."""
    created = ShelfCreate(**_shelf_payload(cells=[_cell_payload(description=None)]))
    assert len(created.cells) == 1
    assert created.cells[0].is_active is True
    assert created.cells[0].description is None

    with pytest.raises(ValidationError) as missing_section:
        ShelfCreate(**_shelf_payload(cells=[{"cell": "1"}]))
    assert _locs(missing_section.value) == {("cells", 0, "section")}

    for field in CELL_FIELDS:
        for invalid in ("", "B" * 51, None, 123):
            with pytest.raises(ValidationError) as exc:
                ShelfCreate(**_shelf_payload(cells=[_cell_payload(**{field: invalid})]))
            assert _locs(exc.value) == {("cells", 0, field)}, (field, invalid)

    with pytest.raises(ValidationError) as extra_cell:
        ShelfCreate(**_shelf_payload(cells=[_cell_payload(shelf_id=1)]))
    assert _locs(extra_cell.value) == {("cells", 0, "shelf_id")}

    with pytest.raises(ValidationError) as extra_shelf:
        ShelfCreate(**_shelf_payload(shelf="A", section="1", cell="2"))
    assert _locs(extra_shelf.value) == {("shelf",), ("section",), ("cell",)}


def test_duplicate_positions_are_rejected_case_insensitively() -> None:
    """R-11: dos celdas con la misma `(section, cell)` ⇒ error, sin importar la caja."""
    for duplicated in (
        {"section": "A", "cell": "1"},
        {"section": "a", "cell": "1"},
        {"section": "A", "cell": "1 "},
        {"section": " a ", "cell": "1"},
    ):
        with pytest.raises(ValidationError) as exc:
            ShelfCreate(**_shelf_payload(cells=[_cell_payload(), _cell_payload(**duplicated)]))
        assert "está repetida" in exc.value.errors()[0]["msg"]

        with pytest.raises(ValidationError) as update_exc:
            ShelfUpdate(
                **_shelf_payload(
                    is_active=True,
                    cells=[
                        {**_cell_payload(), "is_active": True},
                        {**_cell_payload(**duplicated), "is_active": True},
                    ],
                )
            )
        assert "está repetida" in update_exc.value.errors()[0]["msg"]

    allowed = ShelfCreate(
        **_shelf_payload(
            cells=[
                _cell_payload(section="A", cell="1"),
                _cell_payload(section="A", cell="2"),
                _cell_payload(section="B", cell="1"),
            ]
        )
    )
    assert len(allowed.cells) == 3


def test_shelf_update_requires_full_replacement_including_cells() -> None:
    """`ShelfUpdate` exige `name`, `is_active` y `cells`; `CellUpdate` exige `is_active`."""
    payload = ShelfUpdate(
        **{
            "name": "Estante A",
            "is_active": False,
            "cells": [{**_cell_payload(), "is_active": True}],
        }
    )
    assert payload.is_active is False
    assert payload.description is None
    assert payload.cells[0].is_active is True

    base: dict[str, object] = {
        "name": "Estante A",
        "is_active": True,
        "cells": [{**_cell_payload(), "is_active": True}],
    }
    for field in ("name", "is_active", "cells"):
        partial = {key: value for key, value in base.items() if key != field}
        with pytest.raises(ValidationError) as missing:
            ShelfUpdate(**partial)  # type: ignore[arg-type]
        assert _locs(missing.value) == {(field,)}

    with pytest.raises(ValidationError) as cell_missing_active:
        ShelfUpdate(**{**base, "cells": [_cell_payload()]})
    assert _locs(cell_missing_active.value) == {("cells", 0, "is_active")}

    with pytest.raises(ValidationError) as cell_extra:
        ShelfUpdate(**{**base, "cells": [{**_cell_payload(), "is_active": True, "id": 7}]})
    assert _locs(cell_extra.value) == {("cells", 0, "id")}


def test_read_schemas_serialize_exact_keys_with_nested_cells() -> None:
    """`ShelfRead` expone siete claves y cada celda las ocho de `CellRead`."""
    timestamp = datetime(2026, 10, 27, 12, 0, tzinfo=UTC)
    cell = CellRead(
        id=2,
        shelf_id=1,
        section="A",
        cell="1",
        description=None,
        is_active=True,
        created_at=timestamp,
        updated_at=timestamp,
    )
    body = ShelfRead(
        id=1,
        name="Estante A",
        description="Fondo",
        is_active=True,
        created_at=timestamp,
        updated_at=timestamp,
        cells=[cell],
    ).model_dump()

    assert set(body) == {
        "id",
        "name",
        "description",
        "is_active",
        "created_at",
        "updated_at",
        "cells",
    }
    assert set(body["cells"][0]) == {
        "id",
        "shelf_id",
        "section",
        "cell",
        "description",
        "is_active",
        "created_at",
        "updated_at",
    }
    assert body["cells"][0]["section"] == "A"


def test_cell_schemas_keep_empty_description_and_default_cell_active() -> None:
    """El schema deja `""` intacto: la normalización a `NULL` es del servicio (R-10)."""
    created = CellCreate(**_cell_payload(description=""))
    assert created.description == ""
    assert created.is_active is True

    updated = CellUpdate(**_cell_payload(description="   ", is_active=False))
    assert updated.description == "   "
    assert updated.is_active is False


def test_like_escape_makes_wildcards_literal() -> None:
    """`%`, `_` y `\\` se escapan para `ILIKE ... ESCAPE '\\'`."""
    assert escape_like("%") == "\\%"
    assert escape_like("_") == "\\_"
    assert escape_like("\\") == "\\\\"
    assert escape_like("100% algodon_trigo\\maiz") == "100\\% algodon\\_trigo\\\\maiz"
    assert escape_like("Estante A") == "Estante A"


@pytest.mark.parametrize(
    ("total", "size", "expected"),
    [(0, 20, 0), (0, 1, 0), (1, 20, 1), (20, 20, 1), (21, 20, 2), (100, 100, 1), (101, 100, 2)],
)
def test_pages_is_zero_without_rows_and_ceils_the_rest(
    total: int, size: int, expected: int
) -> None:
    """`pages = ceil(total/size)` y `0` cuando `total == 0`."""
    assert calculate_pages(total, size) == expected
