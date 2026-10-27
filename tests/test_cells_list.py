"""Listado paginado de celdas (FEAT-017, R-5 y R-11)."""

from __future__ import annotations

from typing import Any

from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncConnection

from tests.db_support import fetch_scalar, unique_suffix
from tests.shelves_support import (
    CELL_KEYS,
    CELLS_PATH,
    FORBIDDEN_KEYS,
    admin_actor,
    create_cell_row,
    create_shelf_with_cells,
    error_fields,
    medico_actor,
)


async def _list(client: AsyncClient, headers: dict[str, str], **params: object) -> dict:
    response = await client.get(CELLS_PATH, headers=headers, params=params)
    assert response.status_code == 200, response.text
    return response.json()


def _values(body: dict, field: str, prefix: str) -> list[Any]:
    """Valores de `field` de los items cuya `section` empieza por `prefix`."""
    return [item[field] for item in body["items"] if str(item["section"]).startswith(prefix)]


async def test_defaults_and_pagination_formula(
    auth_client: AsyncClient, db_conn: AsyncConnection
) -> None:
    """Sin parámetros `page=1`, `size=20` y `pages = ceil(total/20)`."""
    _, headers = await admin_actor(db_conn)
    shelf_id, _ = await create_shelf_with_cells(db_conn, tag=unique_suffix(), positions=())
    for _ in range(3):
        await create_cell_row(db_conn, shelf_id=shelf_id)

    total_db = int(await fetch_scalar(db_conn, "SELECT count(*) FROM cells"))
    body = await _list(auth_client, headers)

    assert set(body) == {"items", "total", "page", "size", "pages"}
    assert body["page"] == 1
    assert body["size"] == 20
    assert body["total"] == total_db
    assert len(body["items"]) == min(20, total_db)
    assert body["pages"] == ((total_db + 19) // 20 if total_db else 0)
    for item in body["items"]:
        assert set(item) == CELL_KEYS
        assert not FORBIDDEN_KEYS & set(item)


async def test_page_and_size_limits_and_out_of_range_page(
    auth_client: AsyncClient, db_conn: AsyncConnection
) -> None:
    """Límites 1..100 en `size`, 422 con `field` correcto y `page=9999` vacía."""
    _, headers = await admin_actor(db_conn)

    assert (
        await auth_client.get(CELLS_PATH, headers=headers, params={"size": 1})
    ).status_code == 200
    assert (
        await auth_client.get(CELLS_PATH, headers=headers, params={"size": 100})
    ).status_code == 200

    for params, field in (
        ({"size": 0}, "size"),
        ({"size": 101}, "size"),
        ({"size": -1}, "size"),
        ({"page": 0}, "page"),
        ({"page": -1}, "page"),
        ({"shelf_id": 0}, "shelf_id"),
        ({"shelf_id": -3}, "shelf_id"),
    ):
        response = await auth_client.get(CELLS_PATH, headers=headers, params=params)
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
    """`sort` admite `section`, `cell`, `shelf_id`, `created_at`, `is_active` e `id`."""
    _, headers = await admin_actor(db_conn)
    tag = unique_suffix()
    prefix = f"zs{tag}"
    shelf_id, _ = await create_shelf_with_cells(db_conn, tag=f"a{tag}", positions=())
    letters = "abcde"
    states = [True, False, True, False, True]
    for letter, is_active in zip(letters, states, strict=True):
        await create_cell_row(
            db_conn,
            shelf_id=shelf_id,
            section=f"{prefix}{letter}",
            cell=f"{prefix}{letter}",
            is_active=is_active,
        )

    for field in ("section", "cell", "shelf_id", "created_at", "is_active", "id"):
        for sort in (field, f"-{field}"):
            body = await _list(auth_client, headers, search=prefix, sort=sort, size=100)
            assert len(_values(body, "section", prefix)) == len(letters), sort

    asc = await _list(auth_client, headers, search=prefix, sort="section", size=100)
    assert _values(asc, "section", prefix) == sorted(_values(asc, "section", prefix))
    desc = await _list(auth_client, headers, search=prefix, sort="-section", size=100)
    assert _values(desc, "section", prefix) == sorted(
        _values(desc, "section", prefix), reverse=True
    )

    is_asc = await _list(auth_client, headers, search=prefix, sort="is_active", size=100)
    flags = _values(is_asc, "is_active", prefix)
    assert flags == sorted(flags)
    is_desc = await _list(auth_client, headers, search=prefix, sort="-is_active", size=100)
    flags_desc = _values(is_desc, "is_active", prefix)
    assert flags_desc == sorted(flags_desc, reverse=True)

    # Desempate `id ASC` dentro de cada grupo de `is_active`.
    groups: dict[bool, list[int]] = {}
    for item in is_asc["items"]:
        if str(item["section"]).startswith(prefix):
            groups.setdefault(item["is_active"], []).append(item["id"])
    assert groups
    for ids in groups.values():
        assert ids == sorted(ids)

    # Sin `sort` el orden por defecto es `id ASC`.
    default = await _list(auth_client, headers, search=prefix, size=100)
    assert _values(default, "id", prefix) == sorted(_values(default, "id", prefix))

    # Un `sort` fuera del catálogo se rechaza con 422.
    for unknown in ("password_hash", "-password_hash", "name", "shelves"):
        response = await auth_client.get(CELLS_PATH, headers=headers, params={"sort": unknown})
        assert response.status_code == 422, (unknown, response.text)
        assert error_fields(response) == {"sort"}


async def test_search_covers_section_cell_and_description_without_accents(
    auth_client: AsyncClient, db_conn: AsyncConnection
) -> None:
    """`search` parcial, case-insensitive e insensible a acentos en tres columnas."""
    _, headers = await admin_actor(db_conn)
    tag = unique_suffix()
    shelf_id, _ = await create_shelf_with_cells(db_conn, tag=f"a{tag}", positions=())

    target_id = await create_cell_row(
        db_conn,
        shelf_id=shelf_id,
        section=f"Sección {tag}",
        cell=f"Célula {tag}",
        description=f"Área {tag}",
    )

    for term in (f"seccion {tag}", f"celula {tag}", f"area {tag}", f"SECCIÓN {tag}".lower()):
        body = await _list(auth_client, headers, search=term, size=100)
        assert target_id in {item["id"] for item in body["items"]}, term

    percent_tag = unique_suffix()
    percent_id = await create_cell_row(
        db_conn, shelf_id=shelf_id, section=f"Est %50 {percent_tag}", cell=f"c-{percent_tag}"
    )
    control_id = await create_cell_row(
        db_conn, shelf_id=shelf_id, section=f"Est 50 {percent_tag}", cell=f"c2-{percent_tag}"
    )
    percent_ids = {
        item["id"]
        for item in (await _list(auth_client, headers, search=f"%50 {percent_tag}"))["items"]
    }
    assert percent_id in percent_ids
    assert control_id not in percent_ids

    underscore_tag = unique_suffix()
    underscore_id = await create_cell_row(
        db_conn, shelf_id=shelf_id, section=f"Lab_sub {underscore_tag}", cell=f"c-{underscore_tag}"
    )
    underscore_body = await _list(auth_client, headers, search=f"b_sub {underscore_tag}")
    assert underscore_id in {item["id"] for item in underscore_body["items"]}

    backslash_tag = unique_suffix()
    backslash_id = await create_cell_row(
        db_conn, shelf_id=shelf_id, section=f"Lab\\base {backslash_tag}", cell=f"c-{backslash_tag}"
    )
    backslash_body = await _list(auth_client, headers, search=f"b\\base {backslash_tag}")
    assert backslash_id in {item["id"] for item in backslash_body["items"]}

    # `search` se recorta antes de filtrar: un término de solo espacios no filtra nada.
    blank = await _list(auth_client, headers, search="   ")
    unfiltered = await _list(auth_client, headers)
    assert blank["total"] == unfiltered["total"]
    assert blank["total"] > 0

    long_response = await auth_client.get(CELLS_PATH, headers=headers, params={"search": "x" * 101})
    assert error_fields(long_response) == {"search"}


async def test_shelf_id_and_is_active_filters(
    auth_client: AsyncClient, db_conn: AsyncConnection
) -> None:
    """`shelf_id` filtra por estante y se combina con `is_active` y `search`."""
    _, headers = await admin_actor(db_conn)
    tag = unique_suffix()
    shelf_a, (active_a, second_a) = await create_shelf_with_cells(
        db_conn, tag=f"a{tag}", positions=(("A", "1"), ("A", "2"))
    )
    shelf_b, (cell_b,) = await create_shelf_with_cells(
        db_conn, tag=f"b{tag}", positions=(("A", "1"),)
    )
    inactive_a = await create_cell_row(
        db_conn, shelf_id=shelf_a, section=f"Z-{tag}", cell=f"9-{tag}", is_active=False
    )

    only_a = await _list(auth_client, headers, shelf_id=shelf_a, size=100)
    assert {item["id"] for item in only_a["items"]} == {active_a, second_a, inactive_a}
    assert all(item["shelf_id"] == shelf_a for item in only_a["items"])

    only_b = await _list(auth_client, headers, shelf_id=shelf_b, size=100)
    assert {item["id"] for item in only_b["items"]} == {cell_b}

    active_a_items = await _list(auth_client, headers, shelf_id=shelf_a, is_active=True, size=100)
    assert {item["id"] for item in active_a_items["items"]} == {active_a, second_a}
    assert all(item["is_active"] is True for item in active_a_items["items"])

    inactive_items = await _list(auth_client, headers, shelf_id=shelf_a, is_active=False, size=100)
    assert {item["id"] for item in inactive_items["items"]} == {inactive_a}

    combined = await _list(auth_client, headers, shelf_id=shelf_a, search=f"Z-{tag}", size=100)
    assert {item["id"] for item in combined["items"]} == {inactive_a}

    missing_shelf = await _list(auth_client, headers, shelf_id=shelf_b + shelf_a + 1_000_000)
    assert missing_shelf["items"] == []
    assert missing_shelf["total"] == 0


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


async def test_items_are_exposed_to_both_roles(
    auth_client: AsyncClient, db_conn: AsyncConnection
) -> None:
    """Cada `item` tiene las ocho claves de `CellRead`, para ADMIN y MEDICO."""
    _, admin_headers = await admin_actor(db_conn)
    _, medico_headers = await medico_actor(db_conn)
    shelf_id, cell_ids = await create_shelf_with_cells(
        db_conn, tag=unique_suffix(), positions=(("A", "1"), ("A", "2"))
    )

    for headers in (admin_headers, medico_headers):
        body = await _list(auth_client, headers, shelf_id=shelf_id, size=100)
        assert {item["id"] for item in body["items"]} == set(cell_ids)
        for item in body["items"]:
            assert set(item) == CELL_KEYS
