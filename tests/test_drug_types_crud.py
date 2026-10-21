"""Creacion, detalle, actualizacion y desactivacion de FEAT-007 (CA-11 a CA-22)."""

from __future__ import annotations

import logging

import pytest
from httpx import AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

from tests.db_support import fetch_scalar, unique_suffix
from tests.drug_types_support import (
    DRUG_TYPES_PATH,
    FORBIDDEN_KEYS,
    ITEM_KEYS,
    admin_actor,
    assert_error_envelope,
    count_drug_types,
    create_drug_type_row,
    create_medication_for_drug_type,
    drug_type_path,
    error_fields,
    fetch_drug_type_row,
    medico_actor,
    nonexistent_drug_type_id,
)


async def test_ca11_create_defaults_to_active_and_honors_explicit_inactive(
    auth_client: AsyncClient, db_conn: AsyncConnection
) -> None:
    """CA-11: `POST` sin `is_active` nace activo; con `false` nace inactivo."""
    _, headers = await admin_actor(db_conn)
    tag = unique_suffix()

    created = await auth_client.post(
        DRUG_TYPES_PATH, headers=headers, json={"description": f"Tipo {tag}"}
    )
    assert created.status_code == 201, created.text
    body = created.json()
    assert set(body) == ITEM_KEYS
    assert body["description"] == f"Tipo {tag}"
    assert body["is_active"] is True

    row = await fetch_drug_type_row(db_conn, body["id"])
    assert row is not None
    assert row["description"] == f"Tipo {tag}"
    assert row["is_active"] is True
    assert row["created_at"] is not None
    assert row["updated_at"] is not None

    inactive = await auth_client.post(
        DRUG_TYPES_PATH,
        headers=headers,
        json={"description": f"Jarabe {tag}", "is_active": False},
    )
    assert inactive.status_code == 201, inactive.text
    assert inactive.json()["is_active"] is False

    detail = await auth_client.get(drug_type_path(inactive.json()["id"]), headers=headers)
    assert detail.status_code == 200, detail.text
    assert detail.json()["is_active"] is False


async def test_ca12_duplicates_collide_case_insensitively_including_spaces(
    auth_client: AsyncClient, db_conn: AsyncConnection
) -> None:
    """CA-12: duplicados CITEXT con otra capitalizacion o espacios ⇒ 409."""
    _, headers = await admin_actor(db_conn)
    tag = unique_suffix()
    description = f"Pastilla {tag}"
    created = await auth_client.post(
        DRUG_TYPES_PATH, headers=headers, json={"description": description}
    )
    assert created.status_code == 201, created.text

    for variant in (description.upper(), f"  {description}  "):
        response = await auth_client.post(
            DRUG_TYPES_PATH, headers=headers, json={"description": variant}
        )
        body = assert_error_envelope(response, 409, "drug_type_exists")
        assert body["detail"] == "El tipo de fármaco ya existe"
        assert body["errors"] == []

    count = await fetch_scalar(
        db_conn,
        "SELECT count(*) FROM drug_types WHERE description = :description",
        {"description": description},
    )
    assert int(count) == 1


async def test_ca13_create_validations_and_strip_normalization(
    auth_client: AsyncClient, db_conn: AsyncConnection
) -> None:
    """CA-13: 422 con `field == description`; 1 y 100 caracteres pasan; `strip` guarda recortado."""
    _, headers = await admin_actor(db_conn)
    tag = unique_suffix()

    for payload in (
        {},
        {"is_active": True},
        {"description": ""},
        {"description": "   "},
        {"description": "D" * 101},
        {"description": None},
        {"description": 123},
    ):
        response = await auth_client.post(DRUG_TYPES_PATH, headers=headers, json=payload)
        assert error_fields(response) == {"description"}, (payload, response.text)

    one = None
    for candidate in ("Q", "W", "X", "Z"):
        attempt = await auth_client.post(
            DRUG_TYPES_PATH, headers=headers, json={"description": candidate}
        )
        if attempt.status_code == 201:
            one = attempt
            break
        assert attempt.status_code == 409, attempt.text
    assert one is not None, "ni Q/W/X/Z libres para la prueba de 1 caracter"
    assert one.json()["description"] in ("Q", "W", "X", "Z")

    hundred = "D" * (100 - len(tag)) + tag
    assert len(hundred) == 100
    long_ok = await auth_client.post(
        DRUG_TYPES_PATH, headers=headers, json={"description": hundred}
    )
    assert long_ok.status_code == 201, long_ok.text

    spaced = f"  Espaciado {tag}  "
    stored = await auth_client.post(DRUG_TYPES_PATH, headers=headers, json={"description": spaced})
    assert stored.status_code == 201, stored.text
    assert stored.json()["description"] == f"Espaciado {tag}"
    row = await fetch_drug_type_row(db_conn, stored.json()["id"])
    assert row is not None
    assert row["description"] == f"Espaciado {tag}"


async def test_ca14_create_response_hides_timestamps_and_logs_creation(
    auth_client: AsyncClient,
    db_conn: AsyncConnection,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """CA-14: el 201 no expone fechas y emite `drug_type.created` con ids."""
    actor_id, headers = await admin_actor(db_conn)
    caplog.set_level(logging.INFO)

    response = await auth_client.post(
        DRUG_TYPES_PATH, headers=headers, json={"description": f"Tipo {unique_suffix()}"}
    )
    assert response.status_code == 201, response.text
    body = response.json()
    assert set(body) == ITEM_KEYS
    assert not FORBIDDEN_KEYS & set(body)

    events = [
        record.msg
        for record in caplog.records
        if isinstance(record.msg, dict) and record.msg.get("event") == "drug_type.created"
    ]
    assert len(events) == 1
    assert events[0]["drug_type_id"] == body["id"]
    assert events[0]["actor_id"] == actor_id


async def test_ca15_detail_matches_list_and_medico_sees_the_same(
    auth_client: AsyncClient, db_conn: AsyncConnection
) -> None:
    """CA-15: detalle 200 igual al listado, inactivos visibles, 422 de path y lectura MEDICO."""
    _, admin_headers = await admin_actor(db_conn)
    _, medico_headers = await medico_actor(db_conn)
    tag = unique_suffix()
    active_id = await create_drug_type_row(db_conn, f"Detalle {tag}", is_active=True)
    inactive_id = await create_drug_type_row(db_conn, f"Detalle Inactivo {tag}", is_active=False)

    listed = await auth_client.get(
        DRUG_TYPES_PATH, headers=admin_headers, params={"search": f"Detalle {tag}"}
    )
    assert listed.status_code == 200, listed.text
    listed_item = next(item for item in listed.json()["items"] if item["id"] == active_id)

    detail = await auth_client.get(drug_type_path(active_id), headers=admin_headers)
    assert detail.status_code == 200, detail.text
    assert detail.json() == listed_item
    assert set(detail.json()) == ITEM_KEYS

    inactive = await auth_client.get(drug_type_path(inactive_id), headers=admin_headers)
    assert inactive.status_code == 200, inactive.text
    assert inactive.json()["is_active"] is False

    medico_detail = await auth_client.get(drug_type_path(active_id), headers=medico_headers)
    assert medico_detail.status_code == 200, medico_detail.text
    assert medico_detail.json() == listed_item

    bad = await auth_client.get(f"{DRUG_TYPES_PATH}/abc", headers=admin_headers)
    assert bad.status_code == 422, bad.text
    assert error_fields(bad) == {"drug_type_id"}


async def test_ca16_put_replaces_and_preserves_created_at(
    auth_client: AsyncClient, db_conn: AsyncConnection
) -> None:
    """CA-16: `PUT` cambia `description`, conserva `created_at` y refresca `updated_at`."""
    _, headers = await admin_actor(db_conn)
    tag = unique_suffix()
    drug_type_id = await create_drug_type_row(db_conn, f"Viejo {tag}")
    before = await fetch_drug_type_row(db_conn, drug_type_id)
    assert before is not None

    response = await auth_client.put(
        drug_type_path(drug_type_id),
        headers=headers,
        json={"description": f"Tableta {tag}", "is_active": True},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert set(body) == ITEM_KEYS
    assert body == {"id": drug_type_id, "description": f"Tableta {tag}", "is_active": True}

    after = await fetch_drug_type_row(db_conn, drug_type_id)
    assert after is not None
    assert after["description"] == f"Tableta {tag}"
    assert after["created_at"] == before["created_at"]
    assert after["updated_at"] is not None
    assert before["updated_at"] is not None
    assert after["updated_at"] > before["updated_at"]


async def test_ca17_put_reactivates_and_deactivates(
    auth_client: AsyncClient, db_conn: AsyncConnection
) -> None:
    """CA-17: `PUT` reactiva con `true` y desactiva con `false`."""
    _, headers = await admin_actor(db_conn)
    tag = unique_suffix()
    inactive_id = await create_drug_type_row(db_conn, f"Baja {tag}", is_active=False)
    active_id = await create_drug_type_row(db_conn, f"Alta {tag}", is_active=True)

    reactivated = await auth_client.put(
        drug_type_path(inactive_id),
        headers=headers,
        json={"description": f"Baja {tag}", "is_active": True},
    )
    assert reactivated.status_code == 200, reactivated.text
    assert reactivated.json()["is_active"] is True

    deactivated = await auth_client.put(
        drug_type_path(active_id),
        headers=headers,
        json={"description": f"Alta {tag}", "is_active": False},
    )
    assert deactivated.status_code == 200, deactivated.text
    assert deactivated.json()["is_active"] is False


async def test_ca18_put_distinguishes_foreign_from_own_description(
    auth_client: AsyncClient, db_conn: AsyncConnection
) -> None:
    """CA-18: la `description` ajena colisiona; la propia (otra caja/espacios) pasa."""
    _, headers = await admin_actor(db_conn)
    tag = unique_suffix()
    first = f"Primero {tag}"
    second = f"Segundo {tag}"
    first_id = await create_drug_type_row(db_conn, first)
    second_id = await create_drug_type_row(db_conn, second)

    foreign = await auth_client.put(
        drug_type_path(second_id),
        headers=headers,
        json={"description": first.upper(), "is_active": True},
    )
    body = assert_error_envelope(foreign, 409, "drug_type_exists")
    assert body["detail"] == "El tipo de fármaco ya existe"
    unchanged = await fetch_drug_type_row(db_conn, second_id)
    assert unchanged is not None
    assert unchanged["description"] == second

    for own in (first.upper(), f"  {first}  "):
        response = await auth_client.put(
            drug_type_path(first_id),
            headers=headers,
            json={"description": own, "is_active": True},
        )
        assert response.status_code == 200, (own, response.text)


async def test_ca19_put_validates_body_path_and_role(
    auth_client: AsyncClient, db_conn: AsyncConnection
) -> None:
    """CA-19: 404 inexistente, 422 de reemplazo parcial y 403 de MEDICO sin cambios."""
    _, admin_headers = await admin_actor(db_conn)
    _, medico_headers = await medico_actor(db_conn)
    tag = unique_suffix()
    drug_type_id = await create_drug_type_row(db_conn, f"Fijo {tag}")
    missing_id = await nonexistent_drug_type_id(db_conn)

    not_found = await auth_client.put(
        drug_type_path(missing_id),
        headers=admin_headers,
        json={"description": f"Otro {tag}", "is_active": True},
    )
    assert_error_envelope(not_found, 404, "drug_type_not_found")

    # Precedencia de la regla 23: el 404 del path precede al 409 de unicidad.
    not_found_dup = await auth_client.put(
        drug_type_path(missing_id),
        headers=admin_headers,
        json={"description": f"Fijo {tag}", "is_active": True},
    )
    assert_error_envelope(not_found_dup, 404, "drug_type_not_found")

    # Precedencia de la regla 23: el 403 de rol precede al 422 del body.
    medico_bad_body = await auth_client.put(
        drug_type_path(drug_type_id),
        headers=medico_headers,
        json={"description": f"Cambiado {tag}"},
    )
    assert_error_envelope(medico_bad_body, 403, "forbidden")

    without_active = await auth_client.put(
        drug_type_path(drug_type_id),
        headers=admin_headers,
        json={"description": f"Otro {tag}"},
    )
    assert error_fields(without_active) == {"is_active"}

    without_description = await auth_client.put(
        drug_type_path(drug_type_id), headers=admin_headers, json={"is_active": True}
    )
    assert error_fields(without_description) == {"description"}

    medico_response = await auth_client.put(
        drug_type_path(drug_type_id),
        headers=medico_headers,
        json={"description": f"Cambiado {tag}", "is_active": False},
    )
    forbidden = assert_error_envelope(medico_response, 403, "forbidden")
    assert forbidden["errors"] == []
    row = await fetch_drug_type_row(db_conn, drug_type_id)
    assert row is not None
    assert row["description"] == f"Fijo {tag}"
    assert row["is_active"] is True


async def test_ca20_delete_is_a_logical_deactivation_without_body(
    auth_client: AsyncClient,
    db_conn: AsyncConnection,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """CA-20: `DELETE` ⇒ 204 vacio, la fila queda con `is_active = false` y log."""
    actor_id, headers = await admin_actor(db_conn)
    drug_type_id = await create_drug_type_row(db_conn, f"Por Desactivar {unique_suffix()}")
    caplog.set_level(logging.INFO)

    response = await auth_client.delete(drug_type_path(drug_type_id), headers=headers)
    assert response.status_code == 204, response.text
    assert response.content == b""

    row = await fetch_drug_type_row(db_conn, drug_type_id)
    assert row is not None
    assert row["is_active"] is False

    events = [
        record.msg
        for record in caplog.records
        if isinstance(record.msg, dict) and record.msg.get("event") == "drug_type.deactivated"
    ]
    assert len(events) == 1
    assert events[0]["drug_type_id"] == drug_type_id
    assert events[0]["actor_id"] == actor_id


async def test_ca21_delete_is_idempotent_and_guards_role_and_missing(
    auth_client: AsyncClient,
    db_conn: AsyncConnection,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """CA-21: segundo `DELETE` ⇒ 204 sin reescribir ni reloguear; 404 y 403 MEDICO."""
    _, admin_headers = await admin_actor(db_conn)
    _, medico_headers = await medico_actor(db_conn)
    drug_type_id = await create_drug_type_row(db_conn, f"Idempotente {unique_suffix()}")

    first = await auth_client.delete(drug_type_path(drug_type_id), headers=admin_headers)
    assert first.status_code == 204, first.text
    stamped = await fetch_drug_type_row(db_conn, drug_type_id)
    assert stamped is not None

    caplog.set_level(logging.INFO)
    caplog.clear()
    second = await auth_client.delete(drug_type_path(drug_type_id), headers=admin_headers)
    assert second.status_code == 204, second.text
    assert second.content == b""

    again = await fetch_drug_type_row(db_conn, drug_type_id)
    assert again is not None
    assert again["is_active"] is False
    assert again["updated_at"] == stamped["updated_at"]
    assert not [
        record
        for record in caplog.records
        if isinstance(record.msg, dict) and record.msg.get("event") == "drug_type.deactivated"
    ]

    missing = await auth_client.delete(
        drug_type_path(await nonexistent_drug_type_id(db_conn)), headers=admin_headers
    )
    assert_error_envelope(missing, 404, "drug_type_not_found")

    guarded_id = await create_drug_type_row(db_conn, f"Protegido {unique_suffix()}")
    medico_response = await auth_client.delete(drug_type_path(guarded_id), headers=medico_headers)
    assert_error_envelope(medico_response, 403, "forbidden")
    guarded = await fetch_drug_type_row(db_conn, guarded_id)
    assert guarded is not None
    assert guarded["is_active"] is True


async def test_ca22_delete_with_medications_keeps_the_reference(
    auth_client: AsyncClient, db_conn: AsyncConnection
) -> None:
    """CA-22: desactivar un tipo con medicamentos esta permitido y no borra nada."""
    _, headers = await admin_actor(db_conn)
    tag = unique_suffix()
    drug_type_id = await create_drug_type_row(db_conn, f"Referenciado {tag}")
    medication_id = await create_medication_for_drug_type(db_conn, drug_type_id, suffix=tag)

    response = await auth_client.delete(drug_type_path(drug_type_id), headers=headers)
    assert response.status_code == 204, response.text

    row = await fetch_drug_type_row(db_conn, drug_type_id)
    assert row is not None
    assert row["is_active"] is False

    medication = await db_conn.execute(
        text("SELECT id, drug_type_id FROM medications WHERE id = :medication_id"),
        {"medication_id": medication_id},
    )
    found = medication.mappings().one_or_none()
    assert found is not None
    assert found["drug_type_id"] == drug_type_id
    assert await count_drug_types(db_conn) >= 1
