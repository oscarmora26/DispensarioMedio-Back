"""Listado de FEAT-012 (CA-9 a CA-14)."""

from __future__ import annotations

from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncConnection

from tests.doctors_support import (
    DOCTORS_PATH,
    ITEM_KEYS,
    admin_actor,
    assert_error_envelope,
    create_doctor_row,
    set_doctor_active,
    unique_suffix,
)


async def test_ca9_pagination_envelope_and_pages_formula(
    auth_client: AsyncClient, db_conn: AsyncConnection
) -> None:
    """CA-9: envelope `items/total/page/size/pages` con la formula de paginas."""
    _, headers = await admin_actor(db_conn)
    await create_doctor_row(db_conn, name="A1")
    await create_doctor_row(db_conn, name="A2")

    response = await auth_client.get(DOCTORS_PATH, headers=headers, params={"size": 1})
    assert response.status_code == 200, response.text
    body = response.json()
    assert set(body) == {"items", "total", "page", "size", "pages"}
    assert body["total"] == 2
    assert body["page"] == 1
    assert body["size"] == 1
    assert body["pages"] == 2
    assert len(body["items"]) == 1


async def test_ca10_page_and_size_limits_and_out_of_range_page(
    auth_client: AsyncClient, db_conn: AsyncConnection
) -> None:
    """CA-10: `size > 100` y `page < 1` ⇒ 422; pagina fuera de rango ⇒ items vacios."""
    _, headers = await admin_actor(db_conn)
    await create_doctor_row(db_conn)

    too_big = await auth_client.get(DOCTORS_PATH, headers=headers, params={"size": 101})
    assert_error_envelope(too_big, 422, "validation_error")

    too_small = await auth_client.get(DOCTORS_PATH, headers=headers, params={"size": 0})
    assert_error_envelope(too_small, 422, "validation_error")

    zero_page = await auth_client.get(DOCTORS_PATH, headers=headers, params={"page": 0})
    assert_error_envelope(zero_page, 422, "validation_error")

    far = await auth_client.get(DOCTORS_PATH, headers=headers, params={"page": 999})
    assert far.status_code == 200, far.text
    body = far.json()
    assert body["items"] == []
    assert body["page"] == 999


async def test_ca11_sort_catalog_and_direction(
    auth_client: AsyncClient, db_conn: AsyncConnection
) -> None:
    """CA-11: orden por `name`, `-name`, `cedula`, `lab_shift`, `specialty` e `id`."""
    _, headers = await admin_actor(db_conn)
    await create_doctor_row(db_conn, name="Beta", lab_shift="Vespertina", specialty="Pediatría")
    await create_doctor_row(db_conn, name="Alfa", lab_shift="Matutina", specialty="Cardiología")
    await create_doctor_row(db_conn, name="Gamma", lab_shift="Nocturna", specialty="General")

    by_name = await auth_client.get(DOCTORS_PATH, headers=headers, params={"sort": "name"})
    names = [item["name"] for item in by_name.json()["items"]]
    assert names == sorted(names)

    desc = await auth_client.get(DOCTORS_PATH, headers=headers, params={"sort": "-name"})
    assert [item["name"] for item in desc.json()["items"]] == sorted(names, reverse=True)

    for field in ("cedula", "lab_shift", "specialty", "is_active", "id"):
        listing = await auth_client.get(DOCTORS_PATH, headers=headers, params={"sort": field})
        assert listing.status_code == 200, (field, listing.text)

    invalid = await auth_client.get(DOCTORS_PATH, headers=headers, params={"sort": "doctor"})
    assert_error_envelope(invalid, 422, "validation_error")

    empty = await auth_client.get(DOCTORS_PATH, headers=headers, params={"sort": ""})
    assert_error_envelope(empty, 422, "validation_error")


async def test_ca12_search_matches_name_and_cedula(
    auth_client: AsyncClient, db_conn: AsyncConnection
) -> None:
    """CA-12: `search` busca por nombre parcial (case-insensitive) y por cedula."""
    _, headers = await admin_actor(db_conn)
    target = await create_doctor_row(db_conn, name="Especialista Único", cedula="00111222333")
    await create_doctor_row(db_conn, name="Otro Médico", cedula="00999888777")

    by_name = await auth_client.get(
        DOCTORS_PATH, headers=headers, params={"search": "especialista"}
    )
    ids = [item["id"] for item in by_name.json()["items"]]
    assert ids == [target]

    by_cedula = await auth_client.get(DOCTORS_PATH, headers=headers, params={"search": "11222"})
    assert [item["id"] for item in by_cedula.json()["items"]] == [target]

    none = await auth_client.get(DOCTORS_PATH, headers=headers, params={"search": "inexistente"})
    assert none.json()["items"] == []
    assert none.json()["total"] == 0


async def test_ca12_search_escapes_like_wildcards(
    auth_client: AsyncClient, db_conn: AsyncConnection
) -> None:
    """CA-12: `search="%"` no devuelve todos los medicos."""
    _, headers = await admin_actor(db_conn)
    await create_doctor_row(db_conn, name="Normal Uno")
    await create_doctor_row(db_conn, name="Normal Dos")

    response = await auth_client.get(DOCTORS_PATH, headers=headers, params={"search": "%"})
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["total"] == 0
    assert body["items"] == []


async def test_ca13_is_active_filter_and_combination(
    auth_client: AsyncClient, db_conn: AsyncConnection
) -> None:
    """CA-13: filtro por estado, `AND` con `search`, ausente ⇒ ambos."""
    _, headers = await admin_actor(db_conn)
    active = await create_doctor_row(db_conn, name=f"Vigente {unique_suffix()}")
    inactive = await create_doctor_row(db_conn, name=f"Retirado {unique_suffix()}")
    await set_doctor_active(db_conn, inactive, False)

    all_list = await auth_client.get(DOCTORS_PATH, headers=headers)
    all_ids = {item["id"] for item in all_list.json()["items"]}
    assert {active, inactive} <= all_ids

    only_active = await auth_client.get(DOCTORS_PATH, headers=headers, params={"is_active": "true"})
    active_ids = {item["id"] for item in only_active.json()["items"]}
    assert active in active_ids
    assert inactive not in active_ids

    only_inactive = await auth_client.get(
        DOCTORS_PATH, headers=headers, params={"is_active": "false"}
    )
    inactive_ids = {item["id"] for item in only_inactive.json()["items"]}
    assert inactive in inactive_ids
    assert active not in inactive_ids

    combined = await auth_client.get(
        DOCTORS_PATH,
        headers=headers,
        params={"is_active": "false", "search": "Retirado"},
    )
    combined_ids = {item["id"] for item in combined.json()["items"]}
    assert combined_ids == {inactive}


async def test_ca14_item_shape_is_exact(auth_client: AsyncClient, db_conn: AsyncConnection) -> None:
    """CA-14: cada item tiene exactamente las seis claves de D-2."""
    _, headers = await admin_actor(db_conn)
    await create_doctor_row(db_conn)

    response = await auth_client.get(DOCTORS_PATH, headers=headers)
    assert response.status_code == 200, response.text
    items = response.json()["items"]
    assert items
    for item in items:
        assert set(item) == ITEM_KEYS, item
