"""Creación, detalle, actualización y desactivación de FEAT-009 (CA-11 a CA-23)."""

from __future__ import annotations

import logging

import pytest
from httpx import AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

from tests.db_support import fetch_scalar, unique_suffix
from tests.locations_support import (
    FORBIDDEN_KEYS,
    ITEM_KEYS,
    LOCATIONS_PATH,
    admin_actor,
    assert_error_envelope,
    count_locations,
    create_location_row,
    create_medication_for_location,
    error_fields,
    fetch_location_row,
    location_body,
    location_path,
    medico_actor,
    nonexistent_location_id,
)


async def test_ca11_create_defaults_to_active_and_honors_explicit_inactive(
    auth_client: AsyncClient, db_conn: AsyncConnection
) -> None:
    """CA-11: `POST` sin `is_active` nace activo; con `false` nace inactivo."""
    _, headers = await admin_actor(db_conn)
    tag = unique_suffix()

    created = await auth_client.post(
        LOCATIONS_PATH,
        headers=headers,
        json={
            "description": f"Estante A - Tramo 1 - Celda {tag}",
            "shelf": f"A-{tag}",
            "section": f"1-{tag}",
            "cell": f"2-{tag}",
        },
    )
    assert created.status_code == 201, created.text
    body = created.json()
    assert set(body) == ITEM_KEYS
    assert body["is_active"] is True

    row = await fetch_location_row(db_conn, body["id"])
    assert row is not None
    assert row["description"] == f"Estante A - Tramo 1 - Celda {tag}"
    assert row["shelf"] == f"A-{tag}"
    assert row["is_active"] is True
    assert row["created_at"] is not None
    assert row["updated_at"] is not None

    inactive = await auth_client.post(
        LOCATIONS_PATH, headers=headers, json=location_body(unique_suffix(), is_active=False)
    )
    assert inactive.status_code == 201, inactive.text
    assert inactive.json()["is_active"] is False

    detail = await auth_client.get(location_path(inactive.json()["id"]), headers=headers)
    assert detail.status_code == 200, detail.text
    assert detail.json()["is_active"] is False


async def test_ca12_composite_duplicates_collide_case_insensitively_including_spaces(
    auth_client: AsyncClient, db_conn: AsyncConnection
) -> None:
    """CA-12: la tripleta repetida (otra caja o espacios) ⇒ 409; la descripción puede repetirse."""
    _, headers = await admin_actor(db_conn)
    tag = unique_suffix()
    shelf, section, cell = f"A-{tag}", f"1-{tag}", f"2-{tag}"
    created = await auth_client.post(
        LOCATIONS_PATH,
        headers=headers,
        json={
            "description": f"Estante A - Tramo 1 - Celda {tag}",
            "shelf": shelf,
            "section": section,
            "cell": cell,
        },
    )
    assert created.status_code == 201, created.text

    for variant in (
        {"shelf": shelf.lower(), "section": section, "cell": cell},
        {"shelf": f"  {shelf}  ", "section": section, "cell": cell},
        {"shelf": shelf.upper(), "section": section.upper(), "cell": cell.upper()},
    ):
        response = await auth_client.post(
            LOCATIONS_PATH,
            headers=headers,
            json={"description": f"Otra {unique_suffix()}", **variant},
        )
        body = assert_error_envelope(response, 409, "location_exists")
        assert body["detail"] == "La ubicación ya existe"
        assert body["errors"] == []

    # La misma descripción con otra posición está permitida.
    allowed = await auth_client.post(
        LOCATIONS_PATH,
        headers=headers,
        json={
            "description": f"Estante A - Tramo 1 - Celda {tag}",
            "shelf": f"B-{tag}",
            "section": section,
            "cell": cell,
        },
    )
    assert allowed.status_code == 201, allowed.text

    count = await fetch_scalar(
        db_conn,
        "SELECT count(*) FROM locations WHERE shelf = :shelf AND section = :section"
        " AND cell = :cell",
        {"shelf": shelf, "section": section, "cell": cell},
    )
    assert int(count) == 1


async def test_ca13_single_column_differences_do_not_collide(
    auth_client: AsyncClient, db_conn: AsyncConnection
) -> None:
    """CA-13: diferir en una sola columna ⇒ 201; solo la tripleta idéntica colisiona."""
    _, headers = await admin_actor(db_conn)
    tag = unique_suffix()
    shelf, section, cell = f"A-{tag}", f"1-{tag}", f"2-{tag}"
    first = await auth_client.post(
        LOCATIONS_PATH,
        headers=headers,
        json={"description": f"Base {tag}", "shelf": shelf, "section": section, "cell": cell},
    )
    assert first.status_code == 201, first.text

    for variant in (
        {"shelf": shelf, "section": section, "cell": f"3-{tag}"},
        {"shelf": shelf, "section": f"2-{tag}", "cell": cell},
        {"shelf": f"B-{tag}", "section": section, "cell": cell},
    ):
        response = await auth_client.post(
            LOCATIONS_PATH,
            headers=headers,
            json={"description": f"Vecina {unique_suffix()}", **variant},
        )
        assert response.status_code == 201, (variant, response.text)

    identical = await auth_client.post(
        LOCATIONS_PATH,
        headers=headers,
        json={"description": f"Gemela {tag}", "shelf": shelf, "section": section, "cell": cell},
    )
    assert_error_envelope(identical, 409, "location_exists")


async def test_ca14_create_validations_and_strip_normalization(
    auth_client: AsyncClient, db_conn: AsyncConnection
) -> None:
    """CA-14: 422 con `field` del campo; 1 y 100 caracteres pasan; `strip` recorta."""
    _, headers = await admin_actor(db_conn)
    fields = ("description", "shelf", "section", "cell")

    assert error_fields(await auth_client.post(LOCATIONS_PATH, headers=headers, json={})) == set(
        fields
    )

    for field in fields:
        base = location_body(unique_suffix())
        for payload in (
            {k: v for k, v in base.items() if k != field},
            {**base, field: ""},
            {**base, field: "   "},
            {**base, field: "B" * 101},
            {**base, field: None},
            {**base, field: 123},
        ):
            response = await auth_client.post(LOCATIONS_PATH, headers=headers, json=payload)
            assert error_fields(response) == {field}, (field, payload, response.text)

    single_tag = unique_suffix()
    one = await auth_client.post(
        LOCATIONS_PATH, headers=headers, json=location_body(single_tag, shelf="Q")
    )
    assert one.status_code == 201, one.text
    assert one.json()["shelf"] == "Q"

    long_tag = unique_suffix()
    hundred = "B" * (100 - len(long_tag) - 1) + f"-{long_tag}"
    assert len(hundred) == 100
    long_ok = await auth_client.post(
        LOCATIONS_PATH, headers=headers, json=location_body(long_tag, shelf=hundred)
    )
    assert long_ok.status_code == 201, long_ok.text

    spaced = await auth_client.post(
        LOCATIONS_PATH,
        headers=headers,
        json=location_body(unique_suffix(), shelf="  Espaciado  "),
    )
    assert spaced.status_code == 201, spaced.text
    assert spaced.json()["shelf"] == "Espaciado"
    row = await fetch_location_row(db_conn, spaced.json()["id"])
    assert row is not None
    assert row["shelf"] == "Espaciado"


async def test_ca15_create_response_hides_timestamps_and_logs_creation(
    auth_client: AsyncClient,
    db_conn: AsyncConnection,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """CA-15: el 201 no expone fechas y emite `location.created` con ids."""
    actor_id, headers = await admin_actor(db_conn)
    caplog.set_level(logging.INFO)

    response = await auth_client.post(
        LOCATIONS_PATH, headers=headers, json=location_body(unique_suffix())
    )
    assert response.status_code == 201, response.text
    body = response.json()
    assert set(body) == ITEM_KEYS
    assert not FORBIDDEN_KEYS & set(body)

    events = [
        record.msg
        for record in caplog.records
        if isinstance(record.msg, dict) and record.msg.get("event") == "location.created"
    ]
    assert len(events) == 1
    assert events[0]["location_id"] == body["id"]
    assert events[0]["actor_id"] == actor_id


async def test_ca16_detail_matches_list_and_medico_sees_the_same(
    auth_client: AsyncClient, db_conn: AsyncConnection
) -> None:
    """CA-16: detalle 200 igual al listado, inactivos visibles, 422 de path y lectura MEDICO."""
    _, admin_headers = await admin_actor(db_conn)
    _, medico_headers = await medico_actor(db_conn)
    tag = unique_suffix()
    active_id = await create_location_row(db_conn, f"Detalle {tag}", is_active=True)
    inactive_id = await create_location_row(db_conn, f"Detalle Inactiva {tag}", is_active=False)

    listed = await auth_client.get(
        LOCATIONS_PATH, headers=admin_headers, params={"search": f"Detalle {tag}", "size": 100}
    )
    assert listed.status_code == 200, listed.text
    listed_item = next(item for item in listed.json()["items"] if item["id"] == active_id)

    detail = await auth_client.get(location_path(active_id), headers=admin_headers)
    assert detail.status_code == 200, detail.text
    assert detail.json() == listed_item
    assert set(detail.json()) == ITEM_KEYS

    inactive = await auth_client.get(location_path(inactive_id), headers=admin_headers)
    assert inactive.status_code == 200, inactive.text
    assert inactive.json()["is_active"] is False

    medico_detail = await auth_client.get(location_path(active_id), headers=medico_headers)
    assert medico_detail.status_code == 200, medico_detail.text
    assert medico_detail.json() == listed_item

    bad = await auth_client.get(f"{LOCATIONS_PATH}/abc", headers=admin_headers)
    assert bad.status_code == 422, bad.text
    assert error_fields(bad) == {"location_id"}


async def test_ca17_put_replaces_and_preserves_created_at(
    auth_client: AsyncClient, db_conn: AsyncConnection
) -> None:
    """CA-17: `PUT` cambia los campos, conserva `created_at` y refresca `updated_at`."""
    _, headers = await admin_actor(db_conn)
    tag = unique_suffix()
    location_id = await create_location_row(
        db_conn, f"Vieja {tag}", shelf=f"A-{tag}", section=f"1-{tag}", cell=f"2-{tag}"
    )
    before = await fetch_location_row(db_conn, location_id)
    assert before is not None

    response = await auth_client.put(
        location_path(location_id),
        headers=headers,
        json={
            "description": f"Nueva {tag}",
            "shelf": f"A-{tag}",
            "section": f"1-{tag}",
            "cell": f"9-{tag}",
            "is_active": True,
        },
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert set(body) == ITEM_KEYS
    assert body == {
        "id": location_id,
        "description": f"Nueva {tag}",
        "shelf": f"A-{tag}",
        "section": f"1-{tag}",
        "cell": f"9-{tag}",
        "is_active": True,
    }

    after = await fetch_location_row(db_conn, location_id)
    assert after is not None
    assert after["description"] == f"Nueva {tag}"
    assert after["cell"] == f"9-{tag}"
    assert after["created_at"] == before["created_at"]
    assert after["updated_at"] is not None
    assert before["updated_at"] is not None
    assert after["updated_at"] > before["updated_at"]


async def test_ca18_put_changes_position_reactivates_and_deactivates(
    auth_client: AsyncClient, db_conn: AsyncConnection
) -> None:
    """CA-18: `PUT` cambia la posición, reactiva con `true` y desactiva con `false`."""
    _, headers = await admin_actor(db_conn)
    tag = unique_suffix()
    inactive_id = await create_location_row(
        db_conn,
        f"Baja {tag}",
        shelf=f"A-{tag}",
        section=f"1-{tag}",
        cell=f"2-{tag}",
        is_active=False,
    )
    active_id = await create_location_row(
        db_conn,
        f"Alta {tag}",
        shelf=f"B-{tag}",
        section=f"1-{tag}",
        cell=f"2-{tag}",
        is_active=True,
    )

    moved = await auth_client.put(
        location_path(active_id),
        headers=headers,
        json={
            "description": f"Alta {tag}",
            "shelf": f"B-{tag}",
            "section": f"1-{tag}",
            "cell": f"7-{tag}",
            "is_active": True,
        },
    )
    assert moved.status_code == 200, moved.text
    assert moved.json()["cell"] == f"7-{tag}"

    reactivated = await auth_client.put(
        location_path(inactive_id),
        headers=headers,
        json={
            "description": f"Baja {tag}",
            "shelf": f"A-{tag}",
            "section": f"1-{tag}",
            "cell": f"2-{tag}",
            "is_active": True,
        },
    )
    assert reactivated.status_code == 200, reactivated.text
    assert reactivated.json()["is_active"] is True

    deactivated = await auth_client.put(
        location_path(active_id),
        headers=headers,
        json={
            "description": f"Alta {tag}",
            "shelf": f"B-{tag}",
            "section": f"1-{tag}",
            "cell": f"7-{tag}",
            "is_active": False,
        },
    )
    assert deactivated.status_code == 200, deactivated.text
    assert deactivated.json()["is_active"] is False


async def test_ca19_put_distinguishes_foreign_from_own_position(
    auth_client: AsyncClient, db_conn: AsyncConnection
) -> None:
    """CA-19: la posición ajena colisiona; la propia (otra caja/espacios) pasa."""
    _, headers = await admin_actor(db_conn)
    tag = unique_suffix()
    first = await create_location_row(
        db_conn, f"Primera {tag}", shelf=f"A-{tag}", section=f"1-{tag}", cell=f"2-{tag}"
    )
    second = await create_location_row(
        db_conn, f"Segunda {tag}", shelf=f"B-{tag}", section=f"1-{tag}", cell=f"2-{tag}"
    )
    second_before = await fetch_location_row(db_conn, second)
    assert second_before is not None

    foreign = await auth_client.put(
        location_path(second),
        headers=headers,
        json={
            "description": f"Segunda {tag}",
            "shelf": f"a-{tag}",
            "section": f"1-{tag}",
            "cell": f"2-{tag}",
            "is_active": True,
        },
    )
    body = assert_error_envelope(foreign, 409, "location_exists")
    assert body["detail"] == "La ubicación ya existe"
    unchanged = await fetch_location_row(db_conn, second)
    assert unchanged is not None
    assert unchanged["shelf"] == second_before["shelf"]
    assert unchanged["description"] == second_before["description"]

    for own_shelf in (f"A-{tag}".upper(), f"  A-{tag}  "):
        response = await auth_client.put(
            location_path(first),
            headers=headers,
            json={
                "description": f"Primera {tag}",
                "shelf": own_shelf,
                "section": f"1-{tag}",
                "cell": f"2-{tag}",
                "is_active": True,
            },
        )
        assert response.status_code == 200, (own_shelf, response.text)

    only_description = await auth_client.put(
        location_path(first),
        headers=headers,
        json={
            "description": f"Primera Renombrada {tag}",
            "shelf": f"A-{tag}",
            "section": f"1-{tag}",
            "cell": f"2-{tag}",
            "is_active": True,
        },
    )
    assert only_description.status_code == 200, only_description.text
    assert only_description.json()["description"] == f"Primera Renombrada {tag}"


async def test_ca20_put_validates_body_path_and_role(
    auth_client: AsyncClient, db_conn: AsyncConnection
) -> None:
    """CA-20: 404 inexistente, 422 de reemplazo parcial y 403 de MEDICO sin cambios."""
    _, admin_headers = await admin_actor(db_conn)
    _, medico_headers = await medico_actor(db_conn)
    tag = unique_suffix()
    location_id = await create_location_row(
        db_conn, f"Fija {tag}", shelf=f"A-{tag}", section=f"1-{tag}", cell=f"2-{tag}"
    )
    target = await fetch_location_row(db_conn, location_id)
    assert target is not None
    missing_id = await nonexistent_location_id(db_conn)

    not_found = await auth_client.put(
        location_path(missing_id),
        headers=admin_headers,
        json={
            "description": f"Otra {tag}",
            "shelf": f"Z-{tag}",
            "section": f"9-{tag}",
            "cell": f"9-{tag}",
            "is_active": True,
        },
    )
    assert_error_envelope(not_found, 404, "location_not_found")

    # Precedencia de la regla 23: el 404 del path precede al 409 de unicidad.
    not_found_dup = await auth_client.put(
        location_path(missing_id),
        headers=admin_headers,
        json={
            "description": f"Fija {tag}",
            "shelf": f"A-{tag}",
            "section": f"1-{tag}",
            "cell": f"2-{tag}",
            "is_active": True,
        },
    )
    assert_error_envelope(not_found_dup, 404, "location_not_found")

    # Precedencia de la regla 23: el 403 de rol precede al 422 del body.
    medico_bad_body = await auth_client.put(
        location_path(location_id),
        headers=medico_headers,
        json={"description": f"Cambiada {tag}"},
    )
    assert_error_envelope(medico_bad_body, 403, "forbidden")

    full = location_body(unique_suffix())
    for field in ("description", "shelf", "section", "cell", "is_active"):
        partial = {k: v for k, v in full.items() if k != field}
        response = await auth_client.put(
            location_path(location_id), headers=admin_headers, json=partial
        )
        assert error_fields(response) == {field}, (field, response.text)

    medico_response = await auth_client.put(
        location_path(location_id),
        headers=medico_headers,
        json={**location_body(unique_suffix()), "is_active": False},
    )
    forbidden = assert_error_envelope(medico_response, 403, "forbidden")
    assert forbidden["errors"] == []
    row = await fetch_location_row(db_conn, location_id)
    assert row is not None
    assert row["description"] == target["description"]
    assert row["shelf"] == target["shelf"]
    assert row["is_active"] is True


async def test_ca21_delete_is_a_logical_deactivation_without_body(
    auth_client: AsyncClient,
    db_conn: AsyncConnection,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """CA-21: `DELETE` ⇒ 204 vacío, la fila queda con `is_active = false` y log."""
    actor_id, headers = await admin_actor(db_conn)
    location_id = await create_location_row(db_conn, f"Por Desactivar {unique_suffix()}")
    caplog.set_level(logging.INFO)

    response = await auth_client.delete(location_path(location_id), headers=headers)
    assert response.status_code == 204, response.text
    assert response.content == b""

    row = await fetch_location_row(db_conn, location_id)
    assert row is not None
    assert row["is_active"] is False

    events = [
        record.msg
        for record in caplog.records
        if isinstance(record.msg, dict) and record.msg.get("event") == "location.deactivated"
    ]
    assert len(events) == 1
    assert events[0]["location_id"] == location_id
    assert events[0]["actor_id"] == actor_id


async def test_ca22_delete_is_idempotent_and_guards_role_and_missing(
    auth_client: AsyncClient,
    db_conn: AsyncConnection,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """CA-22: segundo `DELETE` ⇒ 204 sin reescribir ni reloguear; 404 y 403 MEDICO."""
    _, admin_headers = await admin_actor(db_conn)
    _, medico_headers = await medico_actor(db_conn)
    location_id = await create_location_row(db_conn, f"Idempotente {unique_suffix()}")

    first = await auth_client.delete(location_path(location_id), headers=admin_headers)
    assert first.status_code == 204, first.text
    stamped = await fetch_location_row(db_conn, location_id)
    assert stamped is not None

    caplog.set_level(logging.INFO)
    caplog.clear()
    second = await auth_client.delete(location_path(location_id), headers=admin_headers)
    assert second.status_code == 204, second.text
    assert second.content == b""

    again = await fetch_location_row(db_conn, location_id)
    assert again is not None
    assert again["is_active"] is False
    assert again["updated_at"] == stamped["updated_at"]
    assert not [
        record
        for record in caplog.records
        if isinstance(record.msg, dict) and record.msg.get("event") == "location.deactivated"
    ]

    missing = await auth_client.delete(
        location_path(await nonexistent_location_id(db_conn)), headers=admin_headers
    )
    assert_error_envelope(missing, 404, "location_not_found")

    guarded_id = await create_location_row(db_conn, f"Protegida {unique_suffix()}")
    medico_response = await auth_client.delete(location_path(guarded_id), headers=medico_headers)
    assert_error_envelope(medico_response, 403, "forbidden")
    guarded = await fetch_location_row(db_conn, guarded_id)
    assert guarded is not None
    assert guarded["is_active"] is True


async def test_ca23_delete_with_medications_keeps_the_reference(
    auth_client: AsyncClient, db_conn: AsyncConnection
) -> None:
    """CA-23: desactivar una ubicación con medicamentos está permitido y no borra nada."""
    _, headers = await admin_actor(db_conn)
    tag = unique_suffix()
    location_id = await create_location_row(db_conn, f"Referenciada {tag}")
    medication_id = await create_medication_for_location(db_conn, location_id, suffix=tag)

    response = await auth_client.delete(location_path(location_id), headers=headers)
    assert response.status_code == 204, response.text

    row = await fetch_location_row(db_conn, location_id)
    assert row is not None
    assert row["is_active"] is False

    medication = await db_conn.execute(
        text("SELECT id, location_id FROM medications WHERE id = :medication_id"),
        {"medication_id": medication_id},
    )
    found = medication.mappings().one_or_none()
    assert found is not None
    assert found["location_id"] == location_id
    assert await count_locations(db_conn) >= 1
