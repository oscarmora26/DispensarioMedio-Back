"""Listado paginado de ubicaciones de FEAT-009 (CA-4 a CA-10)."""

from __future__ import annotations

from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncConnection

from tests.db_support import fetch_scalar, unique_suffix
from tests.locations_support import (
    FORBIDDEN_KEYS,
    ITEM_KEYS,
    LOCATIONS_PATH,
    admin_actor,
    create_location_row,
    error_fields,
    medico_actor,
)


async def _list(client: AsyncClient, headers: dict[str, str], **params: object) -> dict:
    response = await client.get(LOCATIONS_PATH, headers=headers, params=params)
    assert response.status_code == 200, response.text
    return response.json()


async def test_ca04_defaults_and_pagination_formula(
    auth_client: AsyncClient, db_conn: AsyncConnection
) -> None:
    """CA-4: sin parámetros `page=1`, `size=20` y `pages = ceil(total/20)`."""
    _, headers = await admin_actor(db_conn)
    for _ in range(3):
        await create_location_row(db_conn)

    total_db = int(await fetch_scalar(db_conn, "SELECT count(*) FROM locations"))
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


async def test_ca05_page_and_size_limits_and_out_of_range_page(
    auth_client: AsyncClient, db_conn: AsyncConnection
) -> None:
    """CA-5: límites 1..100, 422 con `field` correcto y `page=9999` vacía."""
    _, headers = await admin_actor(db_conn)

    assert (
        await auth_client.get(LOCATIONS_PATH, headers=headers, params={"size": 1})
    ).status_code == 200
    assert (
        await auth_client.get(LOCATIONS_PATH, headers=headers, params={"size": 100})
    ).status_code == 200

    for params, field in (
        ({"size": 0}, "size"),
        ({"size": 101}, "size"),
        ({"size": -1}, "size"),
        ({"page": 0}, "page"),
        ({"page": -1}, "page"),
    ):
        response = await auth_client.get(LOCATIONS_PATH, headers=headers, params=params)
        assert response.status_code == 422, (params, response.text)
        assert error_fields(response) == {field}

    first = await _list(auth_client, headers, page=1)
    far = await _list(auth_client, headers, page=9999)

    assert far["items"] == []
    assert far["page"] == 9999
    assert far["size"] == 20
    assert far["total"] == first["total"]
    assert far["pages"] == first["pages"]


async def test_ca06_sort_catalog_direction_tiebreak_and_invalids(
    auth_client: AsyncClient, db_conn: AsyncConnection
) -> None:
    """CA-6: los siete campos en ambos sentidos, desempate e inválidos."""
    _, headers = await admin_actor(db_conn)
    tag = unique_suffix()
    prefix = f"zs{tag}"
    letters = "abcde"
    states = [True, False, True, False, True]
    for letter, is_active in zip(letters, states, strict=True):
        await create_location_row(
            db_conn,
            f"Orden {prefix}{letter}",
            shelf=f"{prefix}{letter}",
            section=f"{prefix}s{letter}",
            cell=f"{prefix}c{letter}",
            is_active=is_active,
        )

    def only(body: dict, field: str) -> list[str]:
        return [item[field] for item in body["items"] if str(item["shelf"]).startswith(prefix)]

    asc = await _list(auth_client, headers, search=prefix, sort="description", size=100)
    assert only(asc, "description") == sorted(only(asc, "description"))
    desc = await _list(auth_client, headers, search=prefix, sort="-description", size=100)
    assert desc and only(desc, "description") == sorted(only(desc, "description"), reverse=True)

    for field in ("shelf", "section", "cell"):
        ordered = await _list(auth_client, headers, search=prefix, sort=field, size=100)
        assert only(ordered, field) == sorted(only(ordered, field)), field
        ordered_desc = await _list(auth_client, headers, search=prefix, sort=f"-{field}", size=100)
        assert only(ordered_desc, field) == sorted(only(ordered_desc, field), reverse=True), field

    for sort in ("created_at", "-created_at", "is_active", "-is_active", "id", "-id"):
        body = await _list(auth_client, headers, search=prefix, sort=sort, size=100)
        assert len(only(body, "description")) == len(letters), sort

    is_asc = await _list(auth_client, headers, search=prefix, sort="is_active", size=100)
    flags = [item["is_active"] for item in is_asc["items"] if str(item["shelf"]).startswith(prefix)]
    assert flags == sorted(flags)
    is_desc = await _list(auth_client, headers, search=prefix, sort="-is_active", size=100)
    flags_desc = [
        item["is_active"] for item in is_desc["items"] if str(item["shelf"]).startswith(prefix)
    ]
    assert flags_desc == sorted(flags_desc, reverse=True)

    # Desempate `id ASC`: con `created_at` iguales (mismo `now()` transaccional)
    # los ids crecen; también dentro de cada grupo de `is_active`.
    created = await _list(auth_client, headers, search=prefix, sort="created_at", size=100)
    created_ids = [item["id"] for item in created["items"] if str(item["shelf"]).startswith(prefix)]
    assert created_ids == sorted(created_ids)
    created_desc = await _list(auth_client, headers, search=prefix, sort="-created_at", size=100)
    created_desc_ids = [
        item["id"] for item in created_desc["items"] if str(item["shelf"]).startswith(prefix)
    ]
    assert created_desc_ids == sorted(created_desc_ids)

    groups: dict[bool, list[int]] = {}
    for item in is_asc["items"]:
        if str(item["shelf"]).startswith(prefix):
            groups.setdefault(item["is_active"], []).append(item["id"])
    assert groups
    for ids in groups.values():
        assert ids == sorted(ids)

    # Sin `sort` el orden es `description ASC`.
    default = await _list(auth_client, headers, search=prefix, size=100)
    assert only(default, "description") == sorted(only(default, "description"))

    for invalid in ("password_hash", "descriptionx", "shelf,shelf", ""):
        response = await auth_client.get(LOCATIONS_PATH, headers=headers, params={"sort": invalid})
        assert error_fields(response) == {"sort"}, (invalid, response.text)


async def test_ca07_search_covers_four_fields_case_insensitive_and_escapes_wildcards(
    auth_client: AsyncClient, db_conn: AsyncConnection
) -> None:
    """CA-7: `search` parcial y case-insensitive en los 4 campos, con comodines literales."""
    _, headers = await admin_actor(db_conn)
    tag = unique_suffix()
    target_id = await create_location_row(
        db_conn,
        f"Depósito Central {tag}",
        shelf=f"Est{tag}",
        section=f"Tra{tag}",
        cell=f"Cel{tag}",
    )

    description_hit = await _list(auth_client, headers, search=f"PÓSITO CENTRAL {tag}".lower())
    assert target_id in {item["id"] for item in description_hit["items"]}

    shelf_hit = await _list(auth_client, headers, search=f"est{tag}")
    assert target_id in {item["id"] for item in shelf_hit["items"]}

    section_hit = await _list(auth_client, headers, search=f"TRA{tag}".lower())
    assert target_id in {item["id"] for item in section_hit["items"]}

    cell_hit = await _list(auth_client, headers, search=f"cel{tag}")
    assert target_id in {item["id"] for item in cell_hit["items"]}

    partial = await _list(auth_client, headers, search=f"central {tag}")
    assert target_id in {item["id"] for item in partial["items"]}

    percent_tag = unique_suffix()
    percent_id = await create_location_row(
        db_conn, f"Est %50 {percent_tag}", shelf=f"A%{percent_tag}"
    )
    control_id = await create_location_row(db_conn, f"Est 50 {percent_tag}")
    percent_body = await _list(auth_client, headers, search=f"%50 {percent_tag}")
    percent_ids = {item["id"] for item in percent_body["items"]}
    assert percent_id in percent_ids
    assert control_id not in percent_ids

    underscore_tag = unique_suffix()
    underscore_id = await create_location_row(db_conn, f"Lab_sub {underscore_tag}")
    underscore_body = await _list(auth_client, headers, search=f"b_sub {underscore_tag}")
    assert underscore_id in {item["id"] for item in underscore_body["items"]}

    backslash_tag = unique_suffix()
    backslash_id = await create_location_row(db_conn, f"Lab\\base {backslash_tag}")
    backslash_body = await _list(auth_client, headers, search=f"b\\base {backslash_tag}")
    assert backslash_id in {item["id"] for item in backslash_body["items"]}

    plain = await _list(auth_client, headers)
    blank = await _list(auth_client, headers, search="   ")
    assert blank["total"] == plain["total"]

    long_response = await auth_client.get(
        LOCATIONS_PATH, headers=headers, params={"search": "x" * 101}
    )
    assert error_fields(long_response) == {"search"}


async def test_ca08_is_active_filter_alone_and_combined_with_search(
    auth_client: AsyncClient, db_conn: AsyncConnection
) -> None:
    """CA-8: `is_active` filtra por estado y se combina con `search`."""
    _, headers = await admin_actor(db_conn)
    tag = unique_suffix()
    active_id = await create_location_row(db_conn, f"Filtro Activa {tag}", is_active=True)
    inactive_id = await create_location_row(db_conn, f"Filtro Inactiva {tag}", is_active=False)

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


async def test_ca09_empty_search_results_have_zero_pages(
    auth_client: AsyncClient, db_conn: AsyncConnection
) -> None:
    """CA-9: sin coincidencias ⇒ `items == []`, `total == 0` y `pages == 0`."""
    _, headers = await admin_actor(db_conn)
    body = await _list(auth_client, headers, search=f"sincoincidencias{unique_suffix()}", page=2)

    assert body["items"] == []
    assert body["total"] == 0
    assert body["pages"] == 0
    assert body["page"] == 2


async def test_ca10_items_never_leak_timestamps(
    auth_client: AsyncClient, db_conn: AsyncConnection
) -> None:
    """CA-10: cada `item` tiene exactamente las seis claves de D-4."""
    _, admin_headers = await admin_actor(db_conn)
    _, medico_headers = await medico_actor(db_conn)
    await create_location_row(db_conn)

    for headers in (admin_headers, medico_headers):
        body = await _list(auth_client, headers)
        assert body["items"], headers
        for item in body["items"]:
            assert set(item) == ITEM_KEYS
