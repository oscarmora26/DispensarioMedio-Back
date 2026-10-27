"""Creacion, detalle, actualizacion y desactivacion de estantes (FEAT-017, R-1, R-6 a R-10)."""

from __future__ import annotations

from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncConnection

from tests.db_support import unique_suffix
from tests.shelves_support import (
    CELL_KEYS,
    FORBIDDEN_KEYS,
    ITEM_KEYS,
    SHELF_EXISTS_DETAIL,
    SHELVES_PATH,
    admin_actor,
    assert_error_envelope,
    count_cells,
    count_shelves,
    create_cell_row,
    create_medication_for_cell,
    create_shelf_with_cells,
    error_fields,
    fetch_cell_row,
    fetch_medication_cell,
    fetch_shelf_row,
    medico_actor,
    nonexistent_shelf_id,
    shelf_body,
    shelf_path,
)


async def test_ca01_post_creates_only_the_shelf_and_returns_empty_cells(
    auth_client: AsyncClient, db_conn: AsyncConnection
) -> None:
    """CA-1/R-6: `POST /shelves` crea solo el estante y responde `cells: []`."""
    _, headers = await admin_actor(db_conn)
    tag = unique_suffix()
    before_cells = await count_cells(db_conn)

    created = await auth_client.post(SHELVES_PATH, headers=headers, json=shelf_body(tag))
    assert created.status_code == 201, created.text
    body = created.json()
    assert set(body) == ITEM_KEYS
    assert not FORBIDDEN_KEYS & set(body)
    assert body["name"] == f"Estante {tag}"
    assert body["description"] is None
    assert body["is_active"] is True
    assert body["cells"] == []

    row = await fetch_shelf_row(db_conn, body["id"])
    assert row is not None
    assert row["name"] == f"Estante {tag}"
    assert row["created_at"] is not None
    assert row["updated_at"] is not None
    assert await count_cells(db_conn) == before_cells

    # `is_active: false` en el body se respeta y el estante sigue naciendo vacio.
    inactive = await auth_client.post(
        SHELVES_PATH, headers=headers, json=shelf_body(unique_suffix(), is_active=False)
    )
    assert inactive.status_code == 201, inactive.text
    assert inactive.json()["is_active"] is False
    assert inactive.json()["cells"] == []


async def test_r6_post_rejects_a_cells_field_without_creating_anything(
    auth_client: AsyncClient, db_conn: AsyncConnection
) -> None:
    """R-6: `cells` en el body de `/shelves` ⇒ 422 `validation_error` y cero filas."""
    _, headers = await admin_actor(db_conn)
    tag = unique_suffix()
    before_shelves = await count_shelves(db_conn)
    before_cells = await count_cells(db_conn)

    payload = {**shelf_body(tag), "cells": [{"section": "A", "cell": "1"}]}
    response = await auth_client.post(SHELVES_PATH, headers=headers, json=payload)
    body = assert_error_envelope(response, 422, "validation_error")
    assert error_fields(response) == {"cells"}
    assert [item["field"] for item in body["errors"]] == ["cells"]
    assert "Extra inputs are not permitted" in body["errors"][0]["message"]

    assert await count_shelves(db_conn) == before_shelves
    assert await count_cells(db_conn) == before_cells


async def test_r1_duplicate_name_collides_case_insensitively(
    auth_client: AsyncClient, db_conn: AsyncConnection
) -> None:
    """R-1: `shelves.name` es único (CITEXT) ⇒ 409 `shelf_exists` sin fila nueva."""
    _, headers = await admin_actor(db_conn)
    tag = unique_suffix()
    name = f"Estante {tag}"
    before = await count_shelves(db_conn)

    created = await auth_client.post(SHELVES_PATH, headers=headers, json=shelf_body(tag))
    assert created.status_code == 201, created.text

    for variant in (name.lower(), name.upper()):
        response = await auth_client.post(
            SHELVES_PATH,
            headers=headers,
            json=shelf_body(unique_suffix(), name=variant),
        )
        body = assert_error_envelope(response, 409, "shelf_exists")
        assert body["detail"] == SHELF_EXISTS_DETAIL
        assert body["errors"] == []

    assert await count_shelves(db_conn) == before + 1


async def test_ca06_empty_description_is_stored_as_null(
    auth_client: AsyncClient, db_conn: AsyncConnection
) -> None:
    """CA-6/R-10: `description: ""` se normaliza a `NULL` al crear y al reemplazar."""
    _, headers = await admin_actor(db_conn)
    tag = unique_suffix()

    created = await auth_client.post(
        SHELVES_PATH, headers=headers, json=shelf_body(tag, description="")
    )
    assert created.status_code == 201, created.text
    body = created.json()
    assert body["description"] is None

    row = await fetch_shelf_row(db_conn, body["id"])
    assert row is not None
    assert row["description"] is None

    # Un `PUT` con descripción vacía o solo espacios vuelve a dejar `NULL`.
    updated = await auth_client.put(
        shelf_path(body["id"]),
        headers=headers,
        json={"name": f"Estante {tag}", "description": "   ", "is_active": True},
    )
    assert updated.status_code == 200, updated.text
    assert updated.json()["description"] is None

    blank = await auth_client.post(
        SHELVES_PATH, headers=headers, json=shelf_body(unique_suffix(), description="   ")
    )
    assert blank.status_code == 201, blank.text
    assert blank.json()["description"] is None


async def test_r7_put_does_not_touch_existing_cells(
    auth_client: AsyncClient, db_conn: AsyncConnection
) -> None:
    """R-7: `PUT /shelves/{id}` reemplaza el estante y deja sus celdas intactas."""
    _, headers = await admin_actor(db_conn)
    tag = unique_suffix()
    shelf_id, cell_ids = await create_shelf_with_cells(
        db_conn, tag=tag, positions=(("A", "1"), ("A", "2"))
    )
    before = [await fetch_cell_row(db_conn, cell_id) for cell_id in cell_ids]
    medication_id = await create_medication_for_cell(db_conn, cell_ids[0], suffix=tag)

    response = await auth_client.put(
        shelf_path(shelf_id),
        headers=headers,
        json={
            "name": f"Renombrado {tag}",
            "description": f"Nueva {tag}",
            "is_active": False,
        },
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["name"] == f"Renombrado {tag}"
    assert body["description"] == f"Nueva {tag}"
    assert body["is_active"] is False
    assert {cell["id"] for cell in body["cells"]} == set(cell_ids)
    for cell in body["cells"]:
        assert set(cell) == CELL_KEYS

    # Las filas de las celdas no cambian: ni ids, ni estado, ni posiciones.
    after = [await fetch_cell_row(db_conn, cell_id) for cell_id in cell_ids]
    assert after == before
    assert await fetch_medication_cell(db_conn, medication_id) == cell_ids[0]

    detail = await auth_client.get(shelf_path(shelf_id), headers=headers)
    assert detail.status_code == 200, detail.text
    assert {cell["id"] for cell in detail.json()["cells"]} == set(cell_ids)


async def test_ca02_detail_matches_list_and_medico_sees_the_same(
    auth_client: AsyncClient, db_conn: AsyncConnection
) -> None:
    """CA-2: detalle 200 igual al listado, inactivos visibles y lectura MEDICO."""
    _, admin_headers = await admin_actor(db_conn)
    _, medico_headers = await medico_actor(db_conn)
    tag = unique_suffix()
    active_id, _ = await create_shelf_with_cells(db_conn, tag=tag, is_active=True)
    inactive_id, _ = await create_shelf_with_cells(db_conn, tag=unique_suffix(), is_active=False)

    listed = await auth_client.get(
        SHELVES_PATH, headers=admin_headers, params={"search": f"Estante {tag}", "size": 100}
    )
    assert listed.status_code == 200, listed.text
    listed_item = next(item for item in listed.json()["items"] if item["id"] == active_id)

    detail = await auth_client.get(shelf_path(active_id), headers=admin_headers)
    assert detail.status_code == 200, detail.text
    assert detail.json() == listed_item
    assert set(detail.json()) == ITEM_KEYS

    inactive = await auth_client.get(shelf_path(inactive_id), headers=admin_headers)
    assert inactive.status_code == 200, inactive.text
    assert inactive.json()["is_active"] is False

    medico_detail = await auth_client.get(shelf_path(active_id), headers=medico_headers)
    assert medico_detail.status_code == 200, medico_detail.text
    assert medico_detail.json() == listed_item

    bad = await auth_client.get(f"{SHELVES_PATH}/abc", headers=admin_headers)
    assert bad.status_code == 422, bad.text
    assert error_fields(bad) == {"shelf_id"}


async def test_put_validates_body_path_and_role(
    auth_client: AsyncClient, db_conn: AsyncConnection
) -> None:
    """`PUT` exige reemplazo completo, rechaza `cells` y respeta 404 y 403."""
    _, admin_headers = await admin_actor(db_conn)
    _, medico_headers = await medico_actor(db_conn)
    tag = unique_suffix()
    shelf_id, _ = await create_shelf_with_cells(db_conn, tag=tag)
    target = await fetch_shelf_row(db_conn, shelf_id)
    assert target is not None
    missing_id = await nonexistent_shelf_id(db_conn)

    not_found = await auth_client.put(
        shelf_path(missing_id), headers=admin_headers, json=shelf_body(unique_suffix())
    )
    assert_error_envelope(not_found, 404, "shelf_not_found")

    full = shelf_body(tag)
    for field in ("name", "is_active"):
        partial = {key: value for key, value in full.items() if key != field}
        response = await auth_client.put(shelf_path(shelf_id), headers=admin_headers, json=partial)
        assert error_fields(response) == {field}, (field, response.text)

    # `description` es opcional en el reemplazo; `cells` y el ruido se rechazan.
    without_description = {key: value for key, value in full.items() if key != "description"}
    accepted = await auth_client.put(
        shelf_path(shelf_id), headers=admin_headers, json=without_description
    )
    assert accepted.status_code == 200, accepted.text
    assert accepted.json()["description"] is None

    with_cells = await auth_client.put(
        shelf_path(shelf_id),
        headers=admin_headers,
        json={**full, "cells": [{"section": "A", "cell": "1"}]},
    )
    assert error_fields(with_cells) == {"cells"}

    extra = await auth_client.put(
        shelf_path(shelf_id), headers=admin_headers, json={**full, "shelf": "A"}
    )
    assert error_fields(extra) == {"shelf"}

    medico_response = await auth_client.put(
        shelf_path(shelf_id),
        headers=medico_headers,
        json=shelf_body(unique_suffix(), is_active=False),
    )
    forbidden = assert_error_envelope(medico_response, 403, "forbidden")
    assert forbidden["errors"] == []
    row = await fetch_shelf_row(db_conn, shelf_id)
    assert row is not None
    assert row["name"] == target["name"]
    assert row["is_active"] is True


async def test_r9_delete_is_a_logical_deactivation_of_shelf_and_cells(
    auth_client: AsyncClient, db_conn: AsyncConnection
) -> None:
    """R-9: `DELETE` ⇒ 204 vacío y desactiva el estante con todas sus celdas."""
    _, headers = await admin_actor(db_conn)
    tag = unique_suffix()
    shelf_id, cell_ids = await create_shelf_with_cells(
        db_conn, tag=tag, positions=(("A", "1"), ("A", "2"))
    )
    medication_id = await create_medication_for_cell(db_conn, cell_ids[0], suffix=tag)

    response = await auth_client.delete(shelf_path(shelf_id), headers=headers)
    assert response.status_code == 204, response.text
    assert response.content == b""

    row = await fetch_shelf_row(db_conn, shelf_id)
    assert row is not None
    assert row["is_active"] is False
    for cell_id in cell_ids:
        cell_row = await fetch_cell_row(db_conn, cell_id)
        assert cell_row is not None
        assert cell_row["is_active"] is False
    # La baja es lógica: el medicamento conserva su celda.
    assert await fetch_medication_cell(db_conn, medication_id) == cell_ids[0]


async def test_r9_delete_is_idempotent_and_guards_role_and_missing(
    auth_client: AsyncClient, db_conn: AsyncConnection
) -> None:
    """R-9: segundo `DELETE` ⇒ 204 sin reescribir; 404 de inexistente y 403 de MEDICO."""
    _, admin_headers = await admin_actor(db_conn)
    _, medico_headers = await medico_actor(db_conn)
    shelf_id, _ = await create_shelf_with_cells(db_conn, tag=unique_suffix())

    first = await auth_client.delete(shelf_path(shelf_id), headers=admin_headers)
    assert first.status_code == 204, first.text
    stamped = await fetch_shelf_row(db_conn, shelf_id)
    assert stamped is not None

    second = await auth_client.delete(shelf_path(shelf_id), headers=admin_headers)
    assert second.status_code == 204, second.text
    assert second.content == b""

    again = await fetch_shelf_row(db_conn, shelf_id)
    assert again is not None
    assert again["is_active"] is False
    assert again["updated_at"] == stamped["updated_at"]

    missing = await auth_client.delete(
        shelf_path(await nonexistent_shelf_id(db_conn)), headers=admin_headers
    )
    assert_error_envelope(missing, 404, "shelf_not_found")

    guarded_id, _ = await create_shelf_with_cells(db_conn, tag=unique_suffix())
    medico_response = await auth_client.delete(shelf_path(guarded_id), headers=medico_headers)
    assert_error_envelope(medico_response, 403, "forbidden")
    guarded = await fetch_shelf_row(db_conn, guarded_id)
    assert guarded is not None
    assert guarded["is_active"] is True


async def test_put_preserves_created_at_reactivates_and_keeps_cells_untouched(
    auth_client: AsyncClient, db_conn: AsyncConnection
) -> None:
    """`PUT` conserva `created_at`, reactiva el estante y no cambia sus celdas."""
    _, headers = await admin_actor(db_conn)
    tag = unique_suffix()
    shelf_id, (cell_id,) = await create_shelf_with_cells(
        db_conn, tag=tag, is_active=False, positions=(("A", "1"),)
    )
    inactive_cell_id = await create_cell_row(
        db_conn,
        shelf_id=shelf_id,
        section=f"B-{tag}",
        cell=f"2-{tag}",
        is_active=False,
    )
    before = await fetch_shelf_row(db_conn, shelf_id)
    assert before is not None
    cell_before = await fetch_cell_row(db_conn, cell_id)
    assert cell_before is not None
    inactive_before = await fetch_cell_row(db_conn, inactive_cell_id)
    assert inactive_before is not None

    response = await auth_client.put(
        shelf_path(shelf_id),
        headers=headers,
        json={"name": f"Estante {tag}", "description": f"Nueva {tag}", "is_active": True},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["id"] == shelf_id
    assert body["is_active"] is True
    assert body["description"] == f"Nueva {tag}"
    assert {cell["id"] for cell in body["cells"]} == {cell_id, inactive_cell_id}
    assert next(cell for cell in body["cells"] if cell["id"] == cell_id)["is_active"] is True
    assert (
        next(cell for cell in body["cells"] if cell["id"] == inactive_cell_id)["is_active"] is False
    )

    after = await fetch_shelf_row(db_conn, shelf_id)
    assert after is not None
    assert after["description"] == f"Nueva {tag}"
    assert after["created_at"] == before["created_at"]
    assert after["updated_at"] is not None
    assert await fetch_cell_row(db_conn, cell_id) == cell_before
    assert await fetch_cell_row(db_conn, inactive_cell_id) == inactive_before
