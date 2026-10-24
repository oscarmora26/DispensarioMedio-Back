"""Listado, paginacion, busqueda y filtros de FEAT-011 (CA-5 a CA-11)."""

from __future__ import annotations

from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncConnection

from tests.db_support import unique_cedula, unique_suffix
from tests.patients_support import (
    FORBIDDEN_KEYS,
    ITEM_KEYS,
    PATIENTS_PATH,
    admin_actor,
    assert_error_envelope,
    create_patient_row,
    medico_actor,
)


async def test_ca5_pagination_envelope_and_pages_formula(
    auth_client: AsyncClient, db_conn: AsyncConnection
) -> None:
    """CA-5: envelope paginado; `pages` sigue `ceil(total/size)` y `0` sin filas."""
    _, headers = await admin_actor(db_conn)
    tag = unique_suffix()
    for _ in range(3):
        await create_patient_row(db_conn, name=f"Listado {tag}")

    response = await auth_client.get(
        PATIENTS_PATH, headers=headers, params={"page": 1, "size": 2, "search": tag}
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert set(body) == {"items", "total", "page", "size", "pages"}
    assert body["total"] == 3
    assert body["page"] == 1
    assert body["size"] == 2
    assert body["pages"] == 2
    assert len(body["items"]) == 2

    second = await auth_client.get(
        PATIENTS_PATH, headers=headers, params={"page": 2, "size": 2, "search": tag}
    )
    assert len(second.json()["items"]) == 1

    empty = await auth_client.get(PATIENTS_PATH, headers=headers, params={"search": f"nadie-{tag}"})
    assert empty.status_code == 200
    assert empty.json()["items"] == []
    assert empty.json()["total"] == 0
    assert empty.json()["pages"] == 0


async def test_ca6_page_and_size_limits_and_out_of_range_page(
    auth_client: AsyncClient, db_conn: AsyncConnection
) -> None:
    """CA-6: `size` fuera de 1..100 o `page` < 1 ⇒ 422; pagina fuera de rango ⇒ vacia."""
    _, headers = await admin_actor(db_conn)

    for params in ({"size": 0}, {"size": 101}, {"page": 0}, {"page": -1}):
        response = await auth_client.get(PATIENTS_PATH, headers=headers, params=params)
        assert response.status_code == 422, (params, response.text)
        assert response.json()["code"] == "validation_error"
        assert {error["field"] for error in response.json()["errors"]} == set(params)

    beyond = await auth_client.get(
        PATIENTS_PATH, headers=headers, params={"page": 999999, "size": 100}
    )
    assert beyond.status_code == 200
    assert beyond.json()["items"] == []


async def test_ca7_sort_catalog_and_direction(
    auth_client: AsyncClient, db_conn: AsyncConnection
) -> None:
    """CA-7: orden por catalogo con `-` descendente; fuera del catalogo ⇒ 422."""
    _, headers = await admin_actor(db_conn)
    tag = unique_suffix()
    ids = [
        await create_patient_row(db_conn, name=f"Orden {tag} B"),
        await create_patient_row(db_conn, name=f"Orden {tag} A"),
    ]

    ascending = await auth_client.get(
        PATIENTS_PATH, headers=headers, params={"search": tag, "sort": "name"}
    )
    assert ascending.status_code == 200, ascending.text
    assert [item["id"] for item in ascending.json()["items"]] == [ids[1], ids[0]]

    descending = await auth_client.get(
        PATIENTS_PATH, headers=headers, params={"search": tag, "sort": "-name"}
    )
    assert [item["id"] for item in descending.json()["items"]] == [ids[0], ids[1]]

    by_id = await auth_client.get(
        PATIENTS_PATH, headers=headers, params={"search": tag, "sort": "-id"}
    )
    assert [item["id"] for item in by_id.json()["items"]] == sorted(ids, reverse=True)

    for field in ("cedula", "-created_at", "is_active", "id"):
        allowed = await auth_client.get(PATIENTS_PATH, headers=headers, params={"sort": field})
        assert allowed.status_code == 200, (field, allowed.text)

    invalid = await auth_client.get(
        PATIENTS_PATH, headers=headers, params={"sort": "password_hash"}
    )
    body = assert_error_envelope(invalid, 422, "validation_error")
    assert body["errors"][0]["field"] == "sort"


async def test_ca8_search_matches_name_cedula_and_carnet(
    auth_client: AsyncClient, db_conn: AsyncConnection
) -> None:
    """CA-8: busqueda parcial por nombre, exacta por cedula y por carnet."""
    _, headers = await admin_actor(db_conn)
    tag = unique_suffix()
    cedula = unique_cedula()
    carnet = f"A{tag.upper()}"
    patient_id = await create_patient_row(
        db_conn,
        name=f"Oscar {tag} Mora",
        cedula=cedula,
        carnet=carnet,
        patient_type="ESTUDIANTE",
    )

    for term in (tag.upper(), tag.lower(), cedula, carnet.lower(), carnet.upper()):
        response = await auth_client.get(PATIENTS_PATH, headers=headers, params={"search": term})
        assert response.status_code == 200, (term, response.text)
        assert patient_id in [item["id"] for item in response.json()["items"]], term


async def test_ca9_search_escapes_like_wildcards(
    auth_client: AsyncClient, db_conn: AsyncConnection
) -> None:
    """CA-9: `%` y `_` se buscan literalmente, nunca como comodines."""
    _, headers = await admin_actor(db_conn)
    tag = unique_suffix()
    literal = await create_patient_row(db_conn, name=f"Pct 100% {tag}")
    plain = await create_patient_row(db_conn, name=f"Plano {tag}")
    underscore = await create_patient_row(db_conn, name=f"Guion {tag}_x")

    percent = await auth_client.get(PATIENTS_PATH, headers=headers, params={"search": "100%"})
    returned = {item["id"] for item in percent.json()["items"]}
    assert literal in returned
    assert plain not in returned

    underscore_search = await auth_client.get(
        PATIENTS_PATH, headers=headers, params={"search": f"{tag}_"}
    )
    assert {item["id"] for item in underscore_search.json()["items"]} == {underscore}

    all_rows = await auth_client.get(
        PATIENTS_PATH, headers=headers, params={"search": "_", "size": 100}
    )
    assert plain not in {item["id"] for item in all_rows.json()["items"]}


async def test_ca10_is_active_filter_and_combination(
    auth_client: AsyncClient, db_conn: AsyncConnection
) -> None:
    """CA-10: estado ausente ⇒ todos; `true`/`false` filtran; combinable con search."""
    _, headers = await admin_actor(db_conn)
    tag = unique_suffix()
    active_id = await create_patient_row(db_conn, name=f"Activo {tag}")
    inactive_id = await create_patient_row(db_conn, name=f"Inactivo {tag}", is_active=False)

    unfiltered = await auth_client.get(PATIENTS_PATH, headers=headers, params={"search": tag})
    assert {item["id"] for item in unfiltered.json()["items"]} == {active_id, inactive_id}

    only_active = await auth_client.get(
        PATIENTS_PATH, headers=headers, params={"search": tag, "is_active": "true"}
    )
    assert {item["id"] for item in only_active.json()["items"]} == {active_id}

    only_inactive = await auth_client.get(
        PATIENTS_PATH, headers=headers, params={"search": tag, "is_active": "false"}
    )
    assert {item["id"] for item in only_inactive.json()["items"]} == {inactive_id}

    combined = await auth_client.get(
        PATIENTS_PATH,
        headers=headers,
        params={"search": f"Inactivo {tag}", "is_active": "true"},
    )
    assert combined.json()["items"] == []


async def test_ca11_item_shape_is_exact_and_medico_can_read(
    auth_client: AsyncClient, db_conn: AsyncConnection
) -> None:
    """CA-11 / CA-1: forma exacta de seis claves y lectura por `MEDICO`."""
    _, headers = await medico_actor(db_conn)
    tag = unique_suffix()
    patient_id = await create_patient_row(db_conn, name=f"Forma {tag}", carnet=f"c{tag}")

    response = await auth_client.get(PATIENTS_PATH, headers=headers, params={"search": tag})
    assert response.status_code == 200, response.text
    items = response.json()["items"]
    assert len(items) == 1
    item = items[0]
    assert set(item) == ITEM_KEYS
    assert item["id"] == patient_id
    assert item["carnet"] == f"c{tag}"
    assert FORBIDDEN_KEYS.isdisjoint(item)
