"""Creación, detalle, actualización y desactivación de FEAT-017 (CA-1 a CA-7, R-6 a R-11)."""

from __future__ import annotations

from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncConnection

from tests.db_support import unique_suffix
from tests.shelves_support import (
    CELL_KEYS,
    FORBIDDEN_KEYS,
    ITEM_KEYS,
    SHELVES_PATH,
    admin_actor,
    assert_error_envelope,
    cell_body,
    cell_positions,
    count_cells,
    count_shelves,
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


async def test_ca01_create_returns_nested_cells_and_defaults(
    auth_client: AsyncClient, db_conn: AsyncConnection
) -> None:
    """CA-1/R-6: `POST` crea estante y celdas en un body y las devuelve anidadas."""
    _, headers = await admin_actor(db_conn)
    tag = unique_suffix()
    payload = shelf_body(
        tag,
        cells=[cell_body(tag, section=f"A-{tag}", cell=f"1-{tag}", description=f"Celda {tag}")],
    )

    created = await auth_client.post(SHELVES_PATH, headers=headers, json=payload)
    assert created.status_code == 201, created.text
    body = created.json()
    assert set(body) == ITEM_KEYS
    assert not FORBIDDEN_KEYS & set(body)
    assert body["name"] == f"Estante {tag}"
    assert body["description"] is None
    assert body["is_active"] is True
    assert len(body["cells"]) == 1
    cell = body["cells"][0]
    assert set(cell) == CELL_KEYS
    assert cell["shelf_id"] == body["id"]
    assert cell["section"] == f"A-{tag}"
    assert cell["cell"] == f"1-{tag}"
    assert cell["description"] == f"Celda {tag}"
    assert cell["is_active"] is True

    row = await fetch_shelf_row(db_conn, body["id"])
    assert row is not None
    assert row["name"] == f"Estante {tag}"
    assert row["created_at"] is not None
    assert row["updated_at"] is not None

    # Sin `cells` el estante nace vacío; con `is_active: false` nace inactivo.
    empty = await auth_client.post(
        SHELVES_PATH,
        headers=headers,
        json={"name": f"Vacío {tag}", "cells": [], "is_active": False},
    )
    assert empty.status_code == 201, empty.text
    assert empty.json()["cells"] == []
    assert empty.json()["is_active"] is False


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
        assert body["detail"] == "El estante ya existe"
        assert body["errors"] == []

    assert await count_shelves(db_conn) == before + 1


async def test_ca06_empty_description_is_stored_as_null(
    auth_client: AsyncClient, db_conn: AsyncConnection
) -> None:
    """CA-6/R-10: `description: ""` se normaliza a `NULL`, en el estante y en la celda."""
    _, headers = await admin_actor(db_conn)
    tag = unique_suffix()
    payload = shelf_body(
        tag,
        description="",
        cells=[cell_body(tag, description="")],
    )

    created = await auth_client.post(SHELVES_PATH, headers=headers, json=payload)
    assert created.status_code == 201, created.text
    body = created.json()
    assert body["description"] is None
    assert body["cells"][0]["description"] is None

    row = await fetch_shelf_row(db_conn, body["id"])
    assert row is not None
    assert row["description"] is None
    cell_row = await fetch_cell_row(db_conn, body["cells"][0]["id"])
    assert cell_row is not None
    assert cell_row["description"] is None

    # Un `PUT` con descripción vacía vuelve a dejar `NULL`.
    updated = await auth_client.put(
        shelf_path(body["id"]),
        headers=headers,
        json={
            "name": f"Estante {tag}",
            "description": "   ",
            "is_active": True,
            "cells": [
                {
                    "section": f"S-{tag}",
                    "cell": f"C-{tag}",
                    "description": "   ",
                    "is_active": True,
                }
            ],
        },
    )
    assert updated.status_code == 200, updated.text
    assert updated.json()["description"] is None
    assert updated.json()["cells"][0]["description"] is None


async def test_ca07_duplicate_positions_in_payload_are_rejected(
    auth_client: AsyncClient, db_conn: AsyncConnection
) -> None:
    """CA-7/R-11: posiciones repetidas en el payload ⇒ 422 antes de tocar la base."""
    _, headers = await admin_actor(db_conn)
    tag = unique_suffix()
    before_shelves = await count_shelves(db_conn)
    before_cells = await count_cells(db_conn)

    payload = shelf_body(
        tag,
        cells=[
            cell_body(tag, section=f"A-{tag}", cell=f"1-{tag}"),
            cell_body(tag, section=f"a-{tag}", cell=f"1-{tag}"),
        ],
    )
    response = await auth_client.post(SHELVES_PATH, headers=headers, json=payload)
    assert response.status_code == 422, response.text
    body = response.json()
    assert body["code"] == "validation_error"
    assert "está repetida" in body["errors"][0]["message"]
    assert await count_shelves(db_conn) == before_shelves
    assert await count_cells(db_conn) == before_cells

    shelf_id, _ = await create_shelf_with_cells(
        db_conn, tag=tag, positions=(("A", "1"), ("A", "2"))
    )
    put_response = await auth_client.put(
        shelf_path(shelf_id),
        headers=headers,
        json={
            "name": f"Estante {tag}",
            "description": None,
            "is_active": True,
            "cells": [
                cell_body(tag, section=f"A-{tag}", cell=f"1-{tag}"),
                cell_body(tag, section=f"A-{tag}", cell=f"1-{tag}"),
            ],
        },
    )
    assert put_response.status_code == 422, put_response.text
    assert put_response.json()["code"] == "validation_error"
    assert len(await cell_positions(db_conn, shelf_id)) == 2


async def test_ca04_put_keeps_position_and_preserves_cell_id(
    auth_client: AsyncClient, db_conn: AsyncConnection
) -> None:
    """CA-4/R-7: la celda conservada mantiene su `id` y el body refleja el estado real."""
    _, headers = await admin_actor(db_conn)
    tag = unique_suffix()
    shelf_id, (kept_id,) = await create_shelf_with_cells(db_conn, tag=tag, positions=(("A", "1"),))
    kept = await fetch_cell_row(db_conn, kept_id)
    assert kept is not None
    medication_id = await create_medication_for_cell(db_conn, kept_id, suffix=tag)

    response = await auth_client.put(
        shelf_path(shelf_id),
        headers=headers,
        json={
            "name": f"Estante {tag}",
            "description": None,
            "is_active": True,
            "cells": [
                # La misma posición con otra caja actualiza en el lugar.
                cell_body(
                    tag,
                    section=str(kept["section"]).upper(),
                    cell=str(kept["cell"]).upper(),
                    description=f"Actualizada {tag}",
                ),
                cell_body(tag, section=f"B-{tag}", cell=f"2-{tag}"),
            ],
        },
    )
    assert response.status_code == 200, response.text
    body = response.json()

    # La respuesta no trae celdas obsoletas del identity map.
    assert len(body["cells"]) == 2
    returned_kept = next(cell for cell in body["cells"] if cell["id"] == kept_id)
    assert returned_kept["description"] == f"Actualizada {tag}"
    new_ids = {cell["id"] for cell in body["cells"]} - {kept_id}
    assert len(new_ids) == 1

    positions = await cell_positions(db_conn, shelf_id)
    assert len(positions) == 2
    assert positions[(str(kept["section"]), str(kept["cell"]))] == kept_id
    assert positions[(f"B-{tag}", f"2-{tag}")] == new_ids.pop()
    assert await fetch_medication_cell(db_conn, medication_id) == kept_id


async def test_ca05_put_removing_a_cell_with_medications_returns_409(
    auth_client: AsyncClient, db_conn: AsyncConnection
) -> None:
    """CA-3/CA-5/R-8: no se elimina una celda con medicamentos ni se anula la FK."""
    _, headers = await admin_actor(db_conn)
    tag = unique_suffix()
    shelf_id, cell_ids = await create_shelf_with_cells(
        db_conn, tag=tag, positions=(("A", "1"), ("A", "2"))
    )
    keep_id, drop_id = cell_ids
    drop = await fetch_cell_row(db_conn, drop_id)
    assert drop is not None
    medication_id = await create_medication_for_cell(db_conn, drop_id, suffix=tag)

    response = await auth_client.put(
        shelf_path(shelf_id),
        headers=headers,
        json={
            "name": f"Renombrado {tag}",
            "description": None,
            "is_active": True,
            "cells": [cell_body(tag, section=f"A-{tag}", cell=f"1-{tag}")],
        },
    )
    body = assert_error_envelope(response, 409, "cell_has_medications")
    assert body["detail"] == "La celda tiene medicamentos asociados"
    assert body["errors"] == [
        {"field": "cells", "message": "La celda tiene medicamentos asociados"}
    ]

    # La celda sigue existiendo y el medicamento sigue asociado: ni `NULL` ni 500.
    assert await fetch_medication_cell(db_conn, medication_id) == drop_id
    assert (await fetch_cell_row(db_conn, drop_id)) is not None
    assert (await fetch_cell_row(db_conn, keep_id)) is not None
    positions = await cell_positions(db_conn, shelf_id)
    assert len(positions) == 2
    assert set(positions.values()) == {keep_id, drop_id}


async def test_r7_put_creates_and_deletes_cells_without_medications(
    auth_client: AsyncClient, db_conn: AsyncConnection
) -> None:
    """R-7: las celdas ausentes sin medicamentos se eliminan y las nuevas se crean."""
    _, headers = await admin_actor(db_conn)
    tag = unique_suffix()
    shelf_id, (removed_id,) = await create_shelf_with_cells(
        db_conn, tag=tag, positions=(("A", "1"),)
    )

    response = await auth_client.put(
        shelf_path(shelf_id),
        headers=headers,
        json={
            "name": f"Estante {tag}",
            "description": None,
            "is_active": True,
            "cells": [cell_body(tag, section=f"Z-{tag}", cell=f"9-{tag}")],
        },
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert [(cell["section"], cell["cell"]) for cell in body["cells"]] == [(f"Z-{tag}", f"9-{tag}")]

    assert await fetch_cell_row(db_conn, removed_id) is None
    positions = await cell_positions(db_conn, shelf_id)
    assert set(positions) == {(f"Z-{tag}", f"9-{tag}")}
    assert body["cells"][0]["id"] == positions[(f"Z-{tag}", f"9-{tag}")]


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
    """`PUT` exige reemplazo completo (`is_active`, `cells`), 404 del path y 403 de MEDICO."""
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
    for field in ("name", "is_active", "cells"):
        partial = {key: value for key, value in full.items() if key != field}
        response = await auth_client.put(shelf_path(shelf_id), headers=admin_headers, json=partial)
        assert error_fields(response) == {field}, (field, response.text)

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


async def test_put_preserves_created_at_and_reactivates(
    auth_client: AsyncClient, db_conn: AsyncConnection
) -> None:
    """`PUT` conserva `created_at` y reactiva un estante dado de baja."""
    _, headers = await admin_actor(db_conn)
    tag = unique_suffix()
    shelf_id, (cell_id,) = await create_shelf_with_cells(
        db_conn, tag=tag, is_active=False, positions=(("A", "1"),)
    )
    before = await fetch_shelf_row(db_conn, shelf_id)
    assert before is not None
    cell_before = await fetch_cell_row(db_conn, cell_id)
    assert cell_before is not None

    response = await auth_client.put(
        shelf_path(shelf_id),
        headers=headers,
        json={
            "name": f"Estante {tag}",
            "description": f"Nueva {tag}",
            "is_active": True,
            "cells": [
                cell_body(
                    tag,
                    section=str(cell_before["section"]),
                    cell=str(cell_before["cell"]),
                    is_active=True,
                )
            ],
        },
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["id"] == shelf_id
    assert body["is_active"] is True
    assert body["description"] == f"Nueva {tag}"
    assert body["cells"][0]["id"] == cell_id
    assert body["cells"][0]["is_active"] is True

    after = await fetch_shelf_row(db_conn, shelf_id)
    assert after is not None
    assert after["description"] == f"Nueva {tag}"
    assert after["created_at"] == before["created_at"]
    assert after["updated_at"] is not None
