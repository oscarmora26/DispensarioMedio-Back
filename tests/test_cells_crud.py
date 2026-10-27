"""Creacion, detalle, actualizacion y desactivacion de celdas (FEAT-017, R-2, R-7, R-8, R-10)."""

from __future__ import annotations

from httpx import AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

from tests.db_support import unique_suffix
from tests.shelves_support import (
    CELL_HAS_MEDICATIONS_DETAIL,
    CELL_KEYS,
    CELL_NOT_FOUND_DETAIL,
    CELL_POSITION_EXISTS_DETAIL,
    CELLS_PATH,
    FORBIDDEN_KEYS,
    SHELF_NOT_FOUND_DETAIL,
    admin_actor,
    assert_error_envelope,
    cell_body,
    cell_path,
    count_cells,
    create_cell,
    create_medication_for_cell,
    create_shelf_with_cells,
    error_fields,
    fetch_cell_row,
    fetch_medication_cell,
    nonexistent_cell_id,
    nonexistent_shelf_id,
)


async def test_post_creates_cell_with_exact_shape_and_defaults(
    auth_client: AsyncClient, db_conn: AsyncConnection
) -> None:
    """`POST /cells` ⇒ 201 `CellRead` con defaults y `description: null`."""
    _, headers = await admin_actor(db_conn)
    tag = unique_suffix()
    shelf_id, _ = await create_shelf_with_cells(db_conn, tag=tag, positions=())

    created = await auth_client.post(
        CELLS_PATH, headers=headers, json=cell_body(tag, shelf_id=shelf_id)
    )
    assert created.status_code == 201, created.text
    body = created.json()
    assert set(body) == CELL_KEYS
    assert not FORBIDDEN_KEYS & set(body)
    assert body["shelf_id"] == shelf_id
    assert body["section"] == f"S-{tag}"
    assert body["cell"] == f"C-{tag}"
    assert body["description"] is None
    assert body["is_active"] is True

    row = await fetch_cell_row(db_conn, body["id"])
    assert row is not None
    assert row["shelf_id"] == shelf_id
    assert row["created_at"] is not None
    assert row["updated_at"] is not None

    # `is_active: false` se respeta en el alta.
    inactive = await create_cell(
        auth_client, headers, shelf_id=shelf_id, tag=unique_suffix(), is_active=False
    )
    assert inactive.status_code == 201, inactive.text
    assert inactive.json()["is_active"] is False

    detail = await auth_client.get(cell_path(body["id"]), headers=headers)
    assert detail.status_code == 200, detail.text
    assert detail.json() == body

    listed = await auth_client.get(
        CELLS_PATH, headers=headers, params={"search": f"C-{tag}", "size": 100}
    )
    assert listed.status_code == 200, listed.text
    assert body in listed.json()["items"]


async def test_r10_empty_description_is_stored_as_null(
    auth_client: AsyncClient, db_conn: AsyncConnection
) -> None:
    """R-10: `description: ""` y solo espacios se guardan como `NULL`, nunca `""`."""
    _, headers = await admin_actor(db_conn)
    tag = unique_suffix()
    shelf_id, _ = await create_shelf_with_cells(db_conn, tag=tag, positions=())

    for description in ("", "   "):
        created = await create_cell(
            auth_client,
            headers,
            shelf_id=shelf_id,
            tag=unique_suffix(),
            description=description,
        )
        assert created.status_code == 201, created.text
        assert created.json()["description"] is None
        row = await fetch_cell_row(db_conn, created.json()["id"])
        assert row is not None
        assert row["description"] is None

    # Lo mismo aplica al reemplazo completo.
    cell = await create_cell(auth_client, headers, shelf_id=shelf_id, tag=unique_suffix())
    updated = await auth_client.put(
        cell_path(cell.json()["id"]),
        headers=headers,
        json={
            "shelf_id": shelf_id,
            "section": cell.json()["section"],
            "cell": cell.json()["cell"],
            "description": "   ",
            "is_active": True,
        },
    )
    assert updated.status_code == 200, updated.text
    assert updated.json()["description"] is None


async def test_r2_duplicate_position_in_the_same_shelf_is_409(
    auth_client: AsyncClient, db_conn: AsyncConnection
) -> None:
    """R-2: la posición es única por estante sin distinguir mayúsculas ⇒ 409."""
    _, headers = await admin_actor(db_conn)
    tag = unique_suffix()
    shelf_id, _ = await create_shelf_with_cells(db_conn, tag=tag, positions=())
    section = f"A-{tag}"
    cell = f"1-{tag}"

    first = await auth_client.post(
        CELLS_PATH,
        headers=headers,
        json=cell_body(tag, shelf_id=shelf_id, section=section, cell=cell),
    )
    assert first.status_code == 201, first.text
    after_first = await count_cells(db_conn)

    for duplicate in (
        {"section": section.lower(), "cell": cell.lower()},
        {"section": section.upper(), "cell": cell},
        {"section": section, "cell": f"{cell} "},
        {"section": f" {section}", "cell": cell},
    ):
        response = await auth_client.post(
            CELLS_PATH, headers=headers, json=cell_body(tag, shelf_id=shelf_id, **duplicate)
        )
        body = assert_error_envelope(response, 409, "cell_position_exists")
        assert body["detail"] == CELL_POSITION_EXISTS_DETAIL
        assert body["errors"] == [{"field": "section", "message": CELL_POSITION_EXISTS_DETAIL}]

    assert await count_cells(db_conn) == after_first


async def test_same_position_in_a_different_shelf_is_accepted(
    auth_client: AsyncClient, db_conn: AsyncConnection
) -> None:
    """R-2: la unicidad es por estante; otra estantería con la misma posición ⇒ 201."""
    _, headers = await admin_actor(db_conn)
    tag = unique_suffix()
    first_shelf, _ = await create_shelf_with_cells(db_conn, tag=f"a{tag}", positions=())
    second_shelf, _ = await create_shelf_with_cells(db_conn, tag=f"b{tag}", positions=())
    section = f"A-{tag}"
    cell = f"1-{tag}"

    first = await auth_client.post(
        CELLS_PATH,
        headers=headers,
        json=cell_body(tag, shelf_id=first_shelf, section=section, cell=cell),
    )
    assert first.status_code == 201, first.text
    second = await auth_client.post(
        CELLS_PATH,
        headers=headers,
        json=cell_body(tag, shelf_id=second_shelf, section=section, cell=cell),
    )
    assert second.status_code == 201, second.text
    assert second.json()["id"] != first.json()["id"]
    assert second.json()["shelf_id"] == second_shelf


async def test_post_missing_shelf_is_404_without_creating_a_row(
    auth_client: AsyncClient, db_conn: AsyncConnection
) -> None:
    """`POST /cells` con `shelf_id` inexistente ⇒ 404 `shelf_not_found`."""
    _, headers = await admin_actor(db_conn)
    before = await count_cells(db_conn)
    missing_shelf = await nonexistent_shelf_id(db_conn)

    response = await auth_client.post(
        CELLS_PATH, headers=headers, json=cell_body(unique_suffix(), shelf_id=missing_shelf)
    )
    body = assert_error_envelope(response, 404, "shelf_not_found")
    assert body["detail"] == SHELF_NOT_FOUND_DETAIL
    assert body["errors"] == []
    assert await count_cells(db_conn) == before


async def test_post_validates_body_fields(
    auth_client: AsyncClient, db_conn: AsyncConnection
) -> None:
    """`POST /cells` exige `shelf_id`, `section` y `cell` y rechaza campos extra."""
    _, headers = await admin_actor(db_conn)
    shelf_id, _ = await create_shelf_with_cells(db_conn, tag=unique_suffix(), positions=())
    full = cell_body(unique_suffix(), shelf_id=shelf_id)

    for field in ("shelf_id", "section", "cell"):
        partial = {key: value for key, value in full.items() if key != field}
        response = await auth_client.post(CELLS_PATH, headers=headers, json=partial)
        assert error_fields(response) == {field}, (field, response.text)

    for field, invalid in (("section", ""), ("cell", ""), ("shelf_id", 0), ("shelf_id", None)):
        response = await auth_client.post(
            CELLS_PATH, headers=headers, json={**full, field: invalid}
        )
        assert error_fields(response) == {field}, (field, invalid, response.text)

    extra = await auth_client.post(
        CELLS_PATH, headers=headers, json={**full, "cell_id": 1, "updated_at": None}
    )
    assert error_fields(extra) == {"cell_id", "updated_at"}

    long_description = await auth_client.post(
        CELLS_PATH, headers=headers, json={**full, "description": "D" * 201}
    )
    assert error_fields(long_description) == {"description"}


async def test_put_replaces_all_fields_and_can_move_the_cell_to_another_shelf(
    auth_client: AsyncClient, db_conn: AsyncConnection
) -> None:
    """R-7: `PUT /cells/{id}` acepta `shelf_id` y mueve la celda conservando su `id`."""
    _, headers = await admin_actor(db_conn)
    tag = unique_suffix()
    origin, _ = await create_shelf_with_cells(db_conn, tag=f"o{tag}", positions=())
    target, _ = await create_shelf_with_cells(db_conn, tag=f"t{tag}", positions=())

    created = await create_cell(auth_client, headers, shelf_id=origin, tag=tag)
    assert created.status_code == 201, created.text
    cell_id = created.json()["id"]
    created_at = created.json()["created_at"]
    medication_id = await create_medication_for_cell(db_conn, cell_id, suffix=tag)

    response = await auth_client.put(
        cell_path(cell_id),
        headers=headers,
        json={
            "shelf_id": target,
            "section": f"Z-{tag}",
            "cell": f"9-{tag}",
            "description": f"Movida {tag}",
            "is_active": True,
        },
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert set(body) == CELL_KEYS
    assert body["id"] == cell_id
    assert body["shelf_id"] == target
    assert body["section"] == f"Z-{tag}"
    assert body["cell"] == f"9-{tag}"
    assert body["description"] == f"Movida {tag}"
    assert body["created_at"] == created_at

    row = await fetch_cell_row(db_conn, cell_id)
    assert row is not None
    assert row["shelf_id"] == target
    # La celda conserva su `id`, así que el medicamento sigue apuntando a ella.
    assert await fetch_medication_cell(db_conn, medication_id) == cell_id

    origin_detail = await auth_client.get(cell_path(cell_id), headers=headers)
    assert origin_detail.json() == body


async def test_put_keeping_its_own_position_is_accepted(
    auth_client: AsyncClient, db_conn: AsyncConnection
) -> None:
    """R-2/R-7: el reemplazo ignora la propia celda al validar la posición."""
    _, headers = await admin_actor(db_conn)
    tag = unique_suffix()
    shelf_id, _ = await create_shelf_with_cells(db_conn, tag=tag, positions=())

    created = await create_cell(auth_client, headers, shelf_id=shelf_id, tag=tag)
    assert created.status_code == 201, created.text
    cell_id = created.json()["id"]

    response = await auth_client.put(
        cell_path(cell_id),
        headers=headers,
        json={
            "shelf_id": shelf_id,
            "section": str(created.json()["section"]).upper(),
            "cell": str(created.json()["cell"]).upper(),
            "description": f"Actualizada {tag}",
            "is_active": False,
        },
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["id"] == cell_id
    assert body["description"] == f"Actualizada {tag}"
    assert body["is_active"] is False
    assert body["section"] == str(created.json()["section"]).upper()


async def test_put_colliding_in_the_target_shelf_is_409(
    auth_client: AsyncClient, db_conn: AsyncConnection
) -> None:
    """R-2: mover una celda a una posición ocupada del destino ⇒ 409 sin cambios."""
    _, headers = await admin_actor(db_conn)
    tag = unique_suffix()
    origin, _ = await create_shelf_with_cells(db_conn, tag=f"o{tag}", positions=())
    target, (occupied_id,) = await create_shelf_with_cells(
        db_conn, tag=f"t{tag}", positions=(("A", "1"),)
    )
    occupied = await fetch_cell_row(db_conn, occupied_id)
    assert occupied is not None

    created = await create_cell(auth_client, headers, shelf_id=origin, tag=tag)
    assert created.status_code == 201, created.text
    cell_id = created.json()["id"]
    before = await fetch_cell_row(db_conn, cell_id)
    assert before is not None

    response = await auth_client.put(
        cell_path(cell_id),
        headers=headers,
        json={
            "shelf_id": target,
            "section": str(occupied["section"]).upper(),
            "cell": str(occupied["cell"]).upper(),
            "description": None,
            "is_active": True,
        },
    )
    body = assert_error_envelope(response, 409, "cell_position_exists")
    assert body["detail"] == CELL_POSITION_EXISTS_DETAIL
    assert body["errors"] == [{"field": "section", "message": CELL_POSITION_EXISTS_DETAIL}]
    assert await fetch_cell_row(db_conn, cell_id) == before


async def test_put_guards_missing_cell_and_missing_shelf(
    auth_client: AsyncClient, db_conn: AsyncConnection
) -> None:
    """`PUT /cells/{id}`: 404 `cell_not_found` y 404 `shelf_not_found`."""
    _, headers = await admin_actor(db_conn)
    tag = unique_suffix()
    shelf_id, _ = await create_shelf_with_cells(db_conn, tag=tag, positions=())
    cell = await create_cell(auth_client, headers, shelf_id=shelf_id, tag=tag)
    assert cell.status_code == 201, cell.text
    body = cell.json()

    missing_cell = await auth_client.put(
        cell_path(await nonexistent_cell_id(db_conn)),
        headers=headers,
        json={
            "shelf_id": shelf_id,
            "section": f"S-{tag}",
            "cell": f"C-{tag}",
            "description": None,
            "is_active": True,
        },
    )
    not_found = assert_error_envelope(missing_cell, 404, "cell_not_found")
    assert not_found["detail"] == CELL_NOT_FOUND_DETAIL
    assert not_found["errors"] == []

    missing_shelf = await auth_client.put(
        cell_path(body["id"]),
        headers=headers,
        json={
            "shelf_id": await nonexistent_shelf_id(db_conn),
            "section": body["section"],
            "cell": body["cell"],
            "description": None,
            "is_active": True,
        },
    )
    shelf_body_error = assert_error_envelope(missing_shelf, 404, "shelf_not_found")
    assert shelf_body_error["detail"] == SHELF_NOT_FOUND_DETAIL
    assert shelf_body_error["errors"] == []
    assert await fetch_cell_row(db_conn, body["id"]) is not None


async def test_put_requires_full_replacement(
    auth_client: AsyncClient, db_conn: AsyncConnection
) -> None:
    """`PUT /cells/{id}` exige todos los campos y rechaza el ruido."""
    _, headers = await admin_actor(db_conn)
    tag = unique_suffix()
    shelf_id, _ = await create_shelf_with_cells(db_conn, tag=tag, positions=())
    cell = await create_cell(auth_client, headers, shelf_id=shelf_id, tag=tag)
    assert cell.status_code == 201, cell.text
    cell_id = cell.json()["id"]
    full = {
        "shelf_id": shelf_id,
        "section": cell.json()["section"],
        "cell": cell.json()["cell"],
        "description": None,
        "is_active": True,
    }

    for field in ("shelf_id", "section", "cell", "is_active"):
        partial = {key: value for key, value in full.items() if key != field}
        response = await auth_client.put(cell_path(cell_id), headers=headers, json=partial)
        assert error_fields(response) == {field}, (field, response.text)

    extra = await auth_client.put(
        cell_path(cell_id), headers=headers, json={**full, "medications": []}
    )
    assert error_fields(extra) == {"medications"}


async def test_put_preserves_created_at_and_reactivates(
    auth_client: AsyncClient, db_conn: AsyncConnection
) -> None:
    """`PUT` conserva `created_at` y reactiva una celda dada de baja."""
    _, headers = await admin_actor(db_conn)
    tag = unique_suffix()
    shelf_id, (cell_id,) = await create_shelf_with_cells(db_conn, tag=tag, positions=(("A", "1"),))
    inactive = await fetch_cell_row(db_conn, cell_id)
    assert inactive is not None
    await db_conn.execute(
        text("UPDATE cells SET is_active = false WHERE id = :id"), {"id": cell_id}
    )
    before = await fetch_cell_row(db_conn, cell_id)
    assert before is not None
    assert before["is_active"] is False

    response = await auth_client.put(
        cell_path(cell_id),
        headers=headers,
        json={
            "shelf_id": shelf_id,
            "section": str(inactive["section"]),
            "cell": str(inactive["cell"]),
            "description": f"Reactivada {tag}",
            "is_active": True,
        },
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["is_active"] is True
    assert body["description"] == f"Reactivada {tag}"

    after = await fetch_cell_row(db_conn, cell_id)
    assert after is not None
    assert after["created_at"] == before["created_at"]
    assert after["is_active"] is True


async def test_delete_is_a_logical_deactivation(
    auth_client: AsyncClient, db_conn: AsyncConnection
) -> None:
    """`DELETE /cells/{id}` ⇒ 204 vacío y la fila queda inactiva, no borrada."""
    _, headers = await admin_actor(db_conn)
    tag = unique_suffix()
    shelf_id, _ = await create_shelf_with_cells(db_conn, tag=tag, positions=())
    cell = await create_cell(auth_client, headers, shelf_id=shelf_id, tag=tag)
    assert cell.status_code == 201, cell.text
    cell_id = cell.json()["id"]

    response = await auth_client.delete(cell_path(cell_id), headers=headers)
    assert response.status_code == 204, response.text
    assert response.content == b""

    row = await fetch_cell_row(db_conn, cell_id)
    assert row is not None
    assert row["is_active"] is False

    detail = await auth_client.get(cell_path(cell_id), headers=headers)
    assert detail.status_code == 200, detail.text
    assert detail.json()["is_active"] is False


async def test_delete_with_medications_is_409_and_leaves_the_row_untouched(
    auth_client: AsyncClient, db_conn: AsyncConnection
) -> None:
    """R-8: una celda con medicamentos no se desactiva: 409 `cell_has_medications`."""
    _, headers = await admin_actor(db_conn)
    tag = unique_suffix()
    _, (cell_id,) = await create_shelf_with_cells(db_conn, tag=tag, positions=(("A", "1"),))
    medication_id = await create_medication_for_cell(db_conn, cell_id, suffix=tag)
    before = await fetch_cell_row(db_conn, cell_id)
    assert before is not None

    response = await auth_client.delete(cell_path(cell_id), headers=headers)
    body = assert_error_envelope(response, 409, "cell_has_medications")
    assert body["detail"] == CELL_HAS_MEDICATIONS_DETAIL
    assert body["errors"] == [{"field": "cell_id", "message": CELL_HAS_MEDICATIONS_DETAIL}]

    assert await fetch_cell_row(db_conn, cell_id) == before
    assert await fetch_medication_cell(db_conn, medication_id) == cell_id


async def test_delete_is_idempotent_and_guards_missing(
    auth_client: AsyncClient, db_conn: AsyncConnection
) -> None:
    """`DELETE` de una celda ya inactiva ⇒ 204 sin reescribir; inexistente ⇒ 404."""
    _, headers = await admin_actor(db_conn)
    tag = unique_suffix()
    shelf_id, _ = await create_shelf_with_cells(db_conn, tag=tag, positions=())
    cell = await create_cell(auth_client, headers, shelf_id=shelf_id, tag=tag)
    assert cell.status_code == 201, cell.text
    cell_id = cell.json()["id"]

    first = await auth_client.delete(cell_path(cell_id), headers=headers)
    assert first.status_code == 204, first.text
    stamped = await fetch_cell_row(db_conn, cell_id)
    assert stamped is not None

    second = await auth_client.delete(cell_path(cell_id), headers=headers)
    assert second.status_code == 204, second.text
    assert second.content == b""

    again = await fetch_cell_row(db_conn, cell_id)
    assert again is not None
    assert again["is_active"] is False
    assert again["updated_at"] == stamped["updated_at"]

    missing = await auth_client.delete(
        cell_path(await nonexistent_cell_id(db_conn)), headers=headers
    )
    not_found = assert_error_envelope(missing, 404, "cell_not_found")
    assert not_found["detail"] == CELL_NOT_FOUND_DETAIL
