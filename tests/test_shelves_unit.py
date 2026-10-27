"""Unitarias sin base de datos de estantes (FEAT-017: schemas, `sort` y utilidades)."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from app.repositories.shelf_repository import escape_like
from app.schemas.shelf import CellRead, ShelfCreate, ShelfRead, ShelfUpdate
from app.services.shelf_service import calculate_pages

NAME_FIELD = "name"
SHELF_WRITE_FIELDS = ("name", "is_active")


def _locs(exc: ValidationError) -> set[tuple[object, ...]]:
    """Devuelve el `loc` de cada error de validación."""
    return {tuple(error["loc"]) for error in exc.errors()}


def _shelf_payload(**overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {"name": "Estante A"}
    payload.update(overrides)
    return payload


def test_shelf_create_requires_name_and_defaults_active() -> None:
    """`ShelfCreate`: `name` obligatorio 2..100 e `is_active=true`."""
    created = ShelfCreate(**_shelf_payload())
    assert created.is_active is True
    assert created.description is None
    assert not hasattr(created, "cells")
    assert ShelfCreate(**_shelf_payload(is_active=False)).is_active is False

    with pytest.raises(ValidationError) as missing:
        ShelfCreate()  # type: ignore[call-arg]
    assert _locs(missing.value) == {(NAME_FIELD,)}

    for invalid in ("", "A", "   ", "B" * 101, None, 123):
        with pytest.raises(ValidationError) as exc:
            ShelfCreate(**_shelf_payload(**{NAME_FIELD: invalid}))
        assert _locs(exc.value) == {(NAME_FIELD,)}, invalid

    # El schema recorta antes de validar la longitud; la capitalización se conserva
    # porque el `CITEXT` de la base compara sin caja.
    assert ShelfCreate(**_shelf_payload(name="  AB  ")).name == "AB"

    assert ShelfCreate(**_shelf_payload(name="AB")).name == "AB"
    assert ShelfCreate(**_shelf_payload(name="B" * 100)).name == "B" * 100
    # La capitalización se conserva; el `CITEXT` de la base la compara sin caja.
    assert ShelfCreate(**_shelf_payload(name="aBc")).name == "aBc"

    with pytest.raises(ValidationError) as too_long_description:
        ShelfCreate(**_shelf_payload(description="D" * 201))
    assert _locs(too_long_description.value) == {("description",)}


def test_shelf_write_schemas_forbid_cells_and_extra_fields() -> None:
    """R-6: `cells` ya no es parte del body de escritura; el ruido se rechaza."""
    with pytest.raises(ValidationError) as nested_cells:
        ShelfCreate(**_shelf_payload(cells=[{"section": "A", "cell": "1"}]))
    assert _locs(nested_cells.value) == {("cells",)}

    with pytest.raises(ValidationError) as nested_update:
        ShelfUpdate(**{**_shelf_payload(is_active=True), "cells": []})
    assert _locs(nested_update.value) == {("cells",)}

    with pytest.raises(ValidationError) as extra_shelf:
        ShelfCreate(**_shelf_payload(shelf="A", section="1", cell="2"))
    assert _locs(extra_shelf.value) == {("shelf",), ("section",), ("cell",)}


def test_shelf_update_requires_name_and_is_active() -> None:
    """`ShelfUpdate` exige el reemplazo completo de los campos del estante."""
    payload = ShelfUpdate(**{"name": "Estante A", "is_active": False})
    assert payload.is_active is False
    assert payload.description is None
    con_description = ShelfUpdate(
        **{"name": "Estante A", "is_active": True, "description": "Fondo"}
    )
    assert con_description.description == "Fondo"

    base: dict[str, object] = {"name": "Estante A", "is_active": True}
    for field in SHELF_WRITE_FIELDS:
        partial = {key: value for key, value in base.items() if key != field}
        with pytest.raises(ValidationError) as missing:
            ShelfUpdate(**partial)  # type: ignore[arg-type]
        assert _locs(missing.value) == {(field,)}


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

    # `cells` tiene valor por defecto: un estante recién creado serializa `[]`.
    bare = ShelfRead(
        id=1,
        name="Estante A",
        description=None,
        is_active=True,
        created_at=timestamp,
        updated_at=timestamp,
    )
    assert bare.model_dump()["cells"] == []


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
