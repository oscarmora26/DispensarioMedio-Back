"""Listado paginado de estantes de FEAT-017 (R-5)."""

from __future__ import annotations

from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncConnection

from tests.db_support import fetch_scalar, unique_suffix
from tests.shelves_support import (
    CELL_KEYS,
    FORBIDDEN_KEYS,
    ITEM_KEYS,
    SHELVES_PATH,
    admin_actor,
    create_shelf_with_cells,
    error_fields,
    medico_actor,
)


async def _list(client: AsyncClient, headers: dict[str, str], **params: object) -> dict:
    response = await client.get(SHELVES_PATH, headers=headers, params=params)
    assert response.status_code == 200, response.text
    return response.json()


async def test_defaults_and_pagination_formula(
    auth_client: AsyncClient, db_conn: AsyncConnection
) -> None:
    """Sin parámetros `page=1`, `size=20` y `pages = ceil(total/20)`."""
    _, headers = await admin_actor(db_conn)
    for _ in range(3):
        await create_shelf_with_cells(db_conn)

    total_db = int(await fetch_scalar(db_conn, "SELECT count(*) FROM shelves"))
    body = await _list(auth_client, headers)

    assert set(body) == {"items", "total", "page", "size", "pages"}
    assert body["page"] == 1
    assert body["size"] == 20
    assert body["total"] == total_db
    assert len(body["items"]) == min(20, total_db)
    assert body["pages"] == ((total_db + 19) // 20 if total_db else 0)
    for item in body["items"]:
        assert set(item) == ITEM_KEYS
        assert not FORBIDDEN_KEYS & set(item)
        for cell in item["cells"]:
            assert set(cell) == CELL_KEYS


async def test_page_and_size_limits_and_out_of_range_page(
    auth_client: AsyncClient, db_conn: AsyncConnection
) -> None:
    """Límites 1..100 en `size`, 422 con `field` correcto y `page=9999` vacía."""
    _, headers = await admin_actor(db_conn)

    assert (
        await auth_client.get(SHELVES_PATH, headers=headers, params={"size": 1})
    ).status_code == 200
    assert (
        await auth_client.get(SHELVES_PATH, headers=headers, params={"size": 100})
    ).status_code == 200

    for params, field in (
        ({"size": 0}, "size"),
        ({"size": 101}, "size"),
        ({"size": -1}, "size"),
        ({"page": 0}, "page"),
        ({"page": -1}, "page"),
    ):
        response = await auth_client.get(SHELVES_PATH, headers=headers, params=params)
        assert response.status_code == 422, (params, response.text)
        assert error_fields(response) == {field}

    first = await _list(auth_client, headers, page=1)
    far = await _list(auth_client, headers, page=9999)

    assert far["items"] == []
    assert far["page"] == 9999
    assert far["size"] == 20
    assert far["total"] == first["total"]
    assert far["pages"] == first["pages"]


async def test_sort_catalog_direction_and_tiebreak(
    auth_client: AsyncClient, db_conn: AsyncConnection
) -> None:
    """`sort` admite `name`, `created_at`, `is_active`, `id` en ambos sentidos."""
    _, headers = await admin_actor(db_conn)
    tag = unique_suffix()
    prefix = f"zs{tag}"
    letters = "abcde"
    states = [True, False, True, False, True]
    for letter, is_active in zip(letters, states, strict=True):
        await create_shelf_with_cells(
            db_conn,
            tag=f"{prefix}{letter}",
            name=f"{prefix}{letter}",
            is_active=is_active,
        )

    def only(body: dict, field: str) -> list[str]:
        return [
            str(item[field]).lower()
            for item in body["items"]
            if str(item["name"]).startswith(prefix)
        ]

    asc = await _list(auth_client, headers, search=prefix, sort="name", size=100)
    assert only(asc, "name") == sorted(only(asc, "name"))
    desc = await _list(auth_client, headers, search=prefix, sort="-name", size=100)
    assert only(desc, "name") == sorted(only(desc, "name"), reverse=True)

    for sort in ("created_at", "-created_at", "is_active", "-is_active", "id", "-id"):
        body = await _list(auth_client, headers, search=prefix, sort=sort, size=100)
        assert len(only(body, "name")) == len(letters), sort

    is_asc = await _list(auth_client, headers, search=prefix, sort="is_active", size=100)
    flags = [item["is_active"] for item in is_asc["items"] if str(item["name"]).startswith(prefix)]
    assert flags == sorted(flags)
    is_desc = await _list(auth_client, headers, search=prefix, sort="-is_active", size=100)
    flags_desc = [
        item["is_active"] for item in is_desc["items"] if str(item["name"]).startswith(prefix)
    ]
    assert flags_desc == sorted(flags_desc, reverse=True)

    # Desempate `id ASC` dentro de cada grupo de `is_active`.
    groups: dict[bool, list[int]] = {}
    for item in is_asc["items"]:
        if str(item["name"]).startswith(prefix):
            groups.setdefault(item["is_active"], []).append(item["id"])
    assert groups
    for ids in groups.values():
        assert ids == sorted(ids)

    # Sin `sort` el orden es `name ASC`.
    default = await _list(auth_client, headers, search=prefix, size=100)
    assert only(default, "name") == sorted(only(default, "name"))

    # Un `sort` fuera del catálogo se rechaza con 422 en vez de caer a `name`.
    for unknown in ("password_hash", "-password_hash", "cells"):
        response = await auth_client.get(SHELVES_PATH, headers=headers, params={"sort": unknown})
        assert response.status_code == 422, (unknown, response.text)
        assert error_fields(response) == {"sort"}


async def test_search_covers_name_and_description_case_insensitive_and_escapes_wildcards(
    auth_client: AsyncClient, db_conn: AsyncConnection
) -> None:
    """`search` parcial y case-insensitive en `name` y `description`, con comodines literales."""
    _, headers = await admin_actor(db_conn)
    tag = unique_suffix()
    target_id, _ = await create_shelf_with_cells(
        db_conn, tag=tag, name=f"Depósito Central {tag}", description=f"Estante del fondo {tag}"
    )

    name_hit = await _list(auth_client, headers, search=f"pósito central {tag}")
    assert target_id in {item["id"] for item in name_hit["items"]}

    description_hit = await _list(auth_client, headers, search=f"DEL FONDO {tag}".lower())
    assert target_id in {item["id"] for item in description_hit["items"]}

    partial = await _list(auth_client, headers, search=f"central {tag}")
    assert target_id in {item["id"] for item in partial["items"]}

    percent_tag = unique_suffix()
    percent_id, _ = await create_shelf_with_cells(
        db_conn, tag=percent_tag, name=f"Est %50 {percent_tag}"
    )
    control_id, _ = await create_shelf_with_cells(
        db_conn, tag=percent_tag, name=f"Est 50 {percent_tag}"
    )
    percent_ids = {
        item["id"]
        for item in (await _list(auth_client, headers, search=f"%50 {percent_tag}"))["items"]
    }
    assert percent_id in percent_ids
    assert control_id not in percent_ids

    underscore_tag = unique_suffix()
    underscore_id, _ = await create_shelf_with_cells(
        db_conn, tag=underscore_tag, name=f"Lab_sub {underscore_tag}"
    )
    underscore_body = await _list(auth_client, headers, search=f"b_sub {underscore_tag}")
    assert underscore_id in {item["id"] for item in underscore_body["items"]}

    backslash_tag = unique_suffix()
    backslash_id, _ = await create_shelf_with_cells(
        db_conn, tag=backslash_tag, name=f"Lab\\base {backslash_tag}"
    )
    backslash_body = await _list(auth_client, headers, search=f"b\\base {backslash_tag}")
    assert backslash_id in {item["id"] for item in backslash_body["items"]}

    # `search` se recorta antes de filtrar: un término de solo espacios no filtra nada.
    blank = await _list(auth_client, headers, search="   ")
    unfiltered = await _list(auth_client, headers)
    assert blank["total"] == unfiltered["total"]
    assert blank["total"] > 0

    long_response = await auth_client.get(
        SHELVES_PATH, headers=headers, params={"search": "x" * 101}
    )
    assert error_fields(long_response) == {"search"}


async def test_is_active_filter_alone_and_combined_with_search(
    auth_client: AsyncClient, db_conn: AsyncConnection
) -> None:
    """`is_active` filtra por estado y se combina con `search`."""
    _, headers = await admin_actor(db_conn)
    tag = unique_suffix()
    active_id, _ = await create_shelf_with_cells(
        db_conn, tag=f"act{tag}", name=f"Filtro Activo {tag}", is_active=True
    )
    inactive_id, _ = await create_shelf_with_cells(
        db_conn, tag=f"ina{tag}", name=f"Filtro Inactivo {tag}", is_active=False
    )

    active = await _list(auth_client, headers, search=tag, is_active=True)
    assert {item["id"] for item in active["items"]} == {active_id}
    assert all(item["is_active"] is True for item in active["items"])

    inactive = await _list(auth_client, headers, search=tag, is_active=False)
    assert {item["id"] for item in inactive["items"]} == {inactive_id}
    assert all(item["is_active"] is False for item in inactive["items"])

    both = await _list(auth_client, headers, search=tag)
    assert {item["id"] for item in both["items"]} == {active_id, inactive_id}

    only_active = await _list(auth_client, headers, is_active=True, size=100)
    assert only_active["items"]
    assert all(item["is_active"] is True for item in only_active["items"])
    assert inactive_id not in {item["id"] for item in only_active["items"]}


async def test_empty_search_results_have_zero_pages(
    auth_client: AsyncClient, db_conn: AsyncConnection
) -> None:
    """Sin coincidencias ⇒ `items == []`, `total == 0` y `pages == 0`."""
    _, headers = await admin_actor(db_conn)
    body = await _list(auth_client, headers, search=f"sincoincidencias{unique_suffix()}", page=2)

    assert body["items"] == []
    assert body["total"] == 0
    assert body["pages"] == 0
    assert body["page"] == 2


async def test_items_expose_nested_cells_for_both_roles(
    auth_client: AsyncClient, db_conn: AsyncConnection
) -> None:
    """Cada `item` tiene las siete claves de `ShelfRead` y sus celdas anidadas."""
    _, admin_headers = await admin_actor(db_conn)
    _, medico_headers = await medico_actor(db_conn)
    shelf_id, cell_ids = await create_shelf_with_cells(db_conn, positions=(("A", "1"), ("A", "2")))

    for headers in (admin_headers, medico_headers):
        body = await _list(auth_client, headers, size=100)
        item = next(entry for entry in body["items"] if entry["id"] == shelf_id)
        assert set(item) == ITEM_KEYS
        assert {cell["id"] for cell in item["cells"]} == set(cell_ids)
