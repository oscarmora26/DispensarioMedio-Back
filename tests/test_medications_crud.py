"""Creación, detalle, actualización y desactivación de FEAT-010 (CA-11 a CA-24)."""

from __future__ import annotations

import logging

import pytest
from httpx import AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

from tests.db_support import fetch_scalar, unique_suffix
from tests.medications_support import (
    FORBIDDEN_KEYS,
    ITEM_KEYS,
    MEDICATIONS_PATH,
    admin_actor,
    assert_error_envelope,
    count_identity,
    count_medications,
    create_medication_row,
    error_fields,
    fetch_medication_row,
    make_catalogs,
    medication_body,
    medication_path,
    medico_actor,
    nonexistent_catalog_id,
    nonexistent_medication_id,
)


async def test_ca11_create_defaults_to_active_and_honors_explicit_inactive(
    auth_client: AsyncClient, db_conn: AsyncConnection
) -> None:
    """CA-11: `POST` sin `is_active` nace activo; con `false` nace inactivo."""
    _, headers = await admin_actor(db_conn)
    drug_type_id, brand_id, cell_id = await make_catalogs(db_conn)
    tag = unique_suffix()

    created = await auth_client.post(
        MEDICATIONS_PATH,
        headers=headers,
        json={
            "description": f"Acetaminofén {tag}",
            "drug_type_id": drug_type_id,
            "brand_id": brand_id,
            "cell_id": cell_id,
            "dose": "500 mg",
        },
    )
    assert created.status_code == 201, created.text
    body = created.json()
    assert set(body) == ITEM_KEYS
    assert body["is_active"] is True
    assert body["description"] == f"Acetaminofén {tag}"

    row = await fetch_medication_row(db_conn, body["id"])
    assert row is not None
    assert row["description"] == f"Acetaminofén {tag}"
    assert row["dose"] == "500 mg"
    assert row["is_active"] is True
    assert row["created_at"] is not None
    assert row["updated_at"] is not None

    inactive = await auth_client.post(
        MEDICATIONS_PATH,
        headers=headers,
        json=medication_body(unique_suffix(), drug_type_id, brand_id, cell_id, is_active=False),
    )
    assert inactive.status_code == 201, inactive.text
    assert inactive.json()["is_active"] is False

    detail = await auth_client.get(medication_path(inactive.json()["id"]), headers=headers)
    assert detail.status_code == 200, detail.text
    assert detail.json()["is_active"] is False


async def test_ca12_composite_duplicates_collide_case_insensitively_including_spaces(
    auth_client: AsyncClient, db_conn: AsyncConnection
) -> None:
    """CA-12: la identidad repetida (otra caja o espacios) ⇒ 409; otra marca/dosis ⇒ 201."""
    _, headers = await admin_actor(db_conn)
    drug_type_id, brand_id, cell_id = await make_catalogs(db_conn)
    other_brand = await create_brand_for_test(db_conn)
    tag = unique_suffix()
    description = f"Acetaminofén {tag}"
    dose = f"500 mg {tag}"

    first = await auth_client.post(
        MEDICATIONS_PATH,
        headers=headers,
        json=medication_body(
            tag, drug_type_id, brand_id, cell_id, description=description, dose=dose
        ),
    )
    assert first.status_code == 201, first.text

    for variant in (
        {"description": description.upper(), "dose": dose.upper()},
        {"description": description.lower(), "dose": dose.lower()},
        {"description": f"  {description}  ", "dose": dose},
        {"description": description, "dose": f"  {dose}  "},
    ):
        response = await auth_client.post(
            MEDICATIONS_PATH,
            headers=headers,
            json=medication_body(
                unique_suffix(),
                drug_type_id,
                brand_id,
                cell_id,
                description=variant["description"],
                dose=variant["dose"],
            ),
        )
        body = assert_error_envelope(response, 409, "medication_exists")
        assert body["detail"] == "El medicamento ya existe"
        assert body["errors"] == []

    assert await count_identity(db_conn, description, brand_id, dose) == 1

    other_brand_ok = await auth_client.post(
        MEDICATIONS_PATH,
        headers=headers,
        json=medication_body(
            tag, drug_type_id, other_brand, cell_id, description=description, dose=dose
        ),
    )
    assert other_brand_ok.status_code == 201, other_brand_ok.text

    other_dose_ok = await auth_client.post(
        MEDICATIONS_PATH,
        headers=headers,
        json=medication_body(
            tag,
            drug_type_id,
            brand_id,
            cell_id,
            description=description,
            dose=f"750 mg {tag}",
        ),
    )
    assert other_dose_ok.status_code == 201, other_dose_ok.text


async def test_ca13_create_validations_and_strip_normalization(
    auth_client: AsyncClient, db_conn: AsyncConnection
) -> None:
    """CA-13: 422 con `field` del campo; 1 y 100 caracteres pasan; `strip` recorta."""
    _, headers = await admin_actor(db_conn)
    drug_type_id, brand_id, cell_id = await make_catalogs(db_conn)
    tag = unique_suffix()
    base = medication_body(tag, drug_type_id, brand_id, cell_id)

    assert error_fields(await auth_client.post(MEDICATIONS_PATH, headers=headers, json={})) == {
        "description",
        "drug_type_id",
        "brand_id",
        "cell_id",
        "dose",
    }

    for field in ("description", "drug_type_id", "brand_id", "cell_id", "dose"):
        payload = dict(base)
        del payload[field]
        response = await auth_client.post(MEDICATIONS_PATH, headers=headers, json=payload)
        assert error_fields(response) == {field}, (field, response.text)

    for field in ("description", "dose"):
        for invalid in ("", "   "):
            response = await auth_client.post(
                MEDICATIONS_PATH, headers=headers, json={**base, field: invalid}
            )
            assert error_fields(response) == {field}, (field, repr(invalid), response.text)

        too_long = await auth_client.post(
            MEDICATIONS_PATH, headers=headers, json={**base, field: "B" * 101}
        )
        assert error_fields(too_long) == {field}

    for field in ("drug_type_id", "brand_id", "cell_id"):
        for invalid in (0, -1, "abc"):
            response = await auth_client.post(
                MEDICATIONS_PATH, headers=headers, json={**base, field: invalid}
            )
            assert error_fields(response) == {field}, (field, invalid, response.text)

    for field in ("description", "dose"):
        one = await auth_client.post(
            MEDICATIONS_PATH,
            headers=headers,
            json={
                **medication_body(unique_suffix(), drug_type_id, brand_id, cell_id),
                field: "Q",
            },
        )
        assert one.status_code == 201, (field, one.text)
        assert one.json()[field] == "Q"

        hundred_tag = unique_suffix()
        if field == "description":
            hundred = "B" * (100 - len(hundred_tag) - 1) + f"-{hundred_tag}"
        else:
            hundred = "D" * 95 + " mg"
        assert len(hundred) <= 100
        long_ok = await auth_client.post(
            MEDICATIONS_PATH,
            headers=headers,
            json={
                **medication_body(hundred_tag, drug_type_id, brand_id, cell_id),
                field: hundred,
            },
        )
        assert long_ok.status_code == 201, (field, long_ok.text)

    spaced = await auth_client.post(
        MEDICATIONS_PATH,
        headers=headers,
        json={**base, "description": "  Espaciado  ", "dose": "  500 mg  "},
    )
    assert spaced.status_code == 201, spaced.text
    assert spaced.json()["description"] == "Espaciado"
    assert spaced.json()["dose"] == "500 mg"
    row = await fetch_medication_row(db_conn, spaced.json()["id"])
    assert row is not None
    assert row["description"] == "Espaciado"
    assert row["dose"] == "500 mg"


async def test_ca14_missing_catalogs_return_their_own_404_in_order(
    auth_client: AsyncClient, db_conn: AsyncConnection
) -> None:
    """CA-14: FK sin fila ⇒ 404 del catálogo; varios ⇒ `drug_type` primero."""
    _, headers = await admin_actor(db_conn)
    drug_type_id, brand_id, cell_id = await make_catalogs(db_conn)
    tag = unique_suffix()
    missing_drug = await nonexistent_catalog_id(db_conn, "drug_types")
    missing_brand = await nonexistent_catalog_id(db_conn, "brands")
    missing_cell = await nonexistent_catalog_id(db_conn, "cells")
    before = await count_medications(db_conn)

    cases = [
        (
            {"drug_type_id": missing_drug},
            404,
            "drug_type_not_found",
            "El tipo de fármaco no existe",
        ),
        ({"brand_id": missing_brand}, 404, "brand_not_found", "La marca no existe"),
        (
            {"cell_id": missing_cell},
            404,
            "cell_not_found",
            "La celda no existe",
        ),
    ]
    for override, status, code, detail in cases:
        payload = medication_body(tag + unique_suffix(), drug_type_id, brand_id, cell_id)
        payload.update(override)
        response = await auth_client.post(MEDICATIONS_PATH, headers=headers, json=payload)
        body = assert_error_envelope(response, status, code)
        assert body["detail"] == detail
        assert body["errors"] == []

    all_missing = medication_body(unique_suffix(), missing_drug, missing_brand, missing_cell)
    body = assert_error_envelope(
        await auth_client.post(MEDICATIONS_PATH, headers=headers, json=all_missing),
        404,
        "drug_type_not_found",
    )
    assert body["detail"] == "El tipo de fármaco no existe"
    assert await count_medications(db_conn) == before


async def test_ca15_inactive_catalogs_return_422_with_field_and_404_wins(
    auth_client: AsyncClient, db_conn: AsyncConnection
) -> None:
    """CA-15: FK inactivo ⇒ 422 con `field`; inexistente precede a inactivo."""
    _, headers = await admin_actor(db_conn)
    drug_type_id, brand_id, cell_id = await make_catalogs(db_conn)
    missing_drug = await nonexistent_catalog_id(db_conn, "drug_types")
    before = await count_medications(db_conn)

    await db_conn.execute(
        text("UPDATE drug_types SET is_active = false WHERE id = :id"), {"id": drug_type_id}
    )
    response = await auth_client.post(
        MEDICATIONS_PATH,
        headers=headers,
        json=medication_body(unique_suffix(), drug_type_id, brand_id, cell_id),
    )
    assert response.status_code == 422, response.text
    body = response.json()
    assert body["code"] == "validation_error"
    assert body["errors"] == [
        {"field": "drug_type_id", "message": "El tipo de fármaco está inactivo"}
    ]
    await db_conn.execute(
        text("UPDATE drug_types SET is_active = true WHERE id = :id"), {"id": drug_type_id}
    )

    await db_conn.execute(
        text("UPDATE brands SET is_active = false WHERE id = :id"), {"id": brand_id}
    )
    brand_response = await auth_client.post(
        MEDICATIONS_PATH,
        headers=headers,
        json=medication_body(unique_suffix(), drug_type_id, brand_id, cell_id),
    )
    assert brand_response.status_code == 422, brand_response.text
    assert brand_response.json()["errors"] == [
        {"field": "brand_id", "message": "La marca está inactiva"}
    ]
    await db_conn.execute(
        text("UPDATE brands SET is_active = true WHERE id = :id"), {"id": brand_id}
    )

    await db_conn.execute(
        text("UPDATE cells SET is_active = false WHERE id = :id"), {"id": cell_id}
    )
    cell_response = await auth_client.post(
        MEDICATIONS_PATH,
        headers=headers,
        json=medication_body(unique_suffix(), drug_type_id, brand_id, cell_id),
    )
    assert cell_response.status_code == 422, cell_response.text
    assert cell_response.json()["errors"] == [
        {"field": "cell_id", "message": "La celda está inactiva"}
    ]
    await db_conn.execute(text("UPDATE cells SET is_active = true WHERE id = :id"), {"id": cell_id})

    await db_conn.execute(
        text("UPDATE brands SET is_active = false WHERE id = :id"), {"id": brand_id}
    )
    precedence = await auth_client.post(
        MEDICATIONS_PATH,
        headers=headers,
        json=medication_body(unique_suffix(), missing_drug, brand_id, cell_id),
    )
    assert_error_envelope(precedence, 404, "drug_type_not_found")

    inactive_self = await auth_client.post(
        MEDICATIONS_PATH,
        headers=headers,
        json=medication_body(unique_suffix(), drug_type_id, brand_id, cell_id, is_active=False),
    )
    assert inactive_self.status_code == 422, inactive_self.text
    assert inactive_self.json()["errors"] == [
        {"field": "brand_id", "message": "La marca está inactiva"}
    ]
    await db_conn.execute(
        text("UPDATE brands SET is_active = true WHERE id = :id"), {"id": brand_id}
    )
    assert await count_medications(db_conn) == before


async def test_ca16_create_response_hides_timestamps_and_logs_creation(
    auth_client: AsyncClient,
    db_conn: AsyncConnection,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """CA-16: el 201 no expone fechas ni anidados y emite `medication.created`."""
    actor_id, headers = await admin_actor(db_conn)
    drug_type_id, brand_id, cell_id = await make_catalogs(db_conn)
    caplog.set_level(logging.INFO)

    response = await auth_client.post(
        MEDICATIONS_PATH,
        headers=headers,
        json=medication_body(unique_suffix(), drug_type_id, brand_id, cell_id),
    )
    assert response.status_code == 201, response.text
    body = response.json()
    assert set(body) == ITEM_KEYS
    assert not FORBIDDEN_KEYS & set(body)

    events = [
        record.msg
        for record in caplog.records
        if isinstance(record.msg, dict) and record.msg.get("event") == "medication.created"
    ]
    assert len(events) == 1
    assert events[0]["medication_id"] == body["id"]
    assert events[0]["actor_id"] == actor_id


async def test_ca17_detail_matches_list_and_medico_sees_the_same(
    auth_client: AsyncClient, db_conn: AsyncConnection
) -> None:
    """CA-17: detalle 200 igual al listado, inactivos visibles, 422 de path y lectura MEDICO."""
    _, admin_headers = await admin_actor(db_conn)
    _, medico_headers = await medico_actor(db_conn)
    drug_type_id, brand_id, cell_id = await make_catalogs(db_conn)
    tag = unique_suffix()
    active_id = await create_medication_row(
        db_conn,
        f"Detalle {tag}",
        drug_type_id=drug_type_id,
        brand_id=brand_id,
        cell_id=cell_id,
        is_active=True,
    )
    inactive_id = await create_medication_row(
        db_conn,
        f"Detalle Inactiva {tag}",
        drug_type_id=drug_type_id,
        brand_id=brand_id,
        cell_id=cell_id,
        is_active=False,
    )

    listed = await auth_client.get(
        MEDICATIONS_PATH, headers=admin_headers, params={"search": f"Detalle {tag}", "size": 100}
    )
    assert listed.status_code == 200, listed.text
    listed_item = next(item for item in listed.json()["items"] if item["id"] == active_id)

    detail = await auth_client.get(medication_path(active_id), headers=admin_headers)
    assert detail.status_code == 200, detail.text
    assert detail.json() == listed_item
    assert set(detail.json()) == ITEM_KEYS

    inactive = await auth_client.get(medication_path(inactive_id), headers=admin_headers)
    assert inactive.status_code == 200, inactive.text
    assert inactive.json()["is_active"] is False

    medico_detail = await auth_client.get(medication_path(active_id), headers=medico_headers)
    assert medico_detail.status_code == 200, medico_detail.text
    assert medico_detail.json() == listed_item

    bad = await auth_client.get(f"{MEDICATIONS_PATH}/abc", headers=admin_headers)
    assert bad.status_code == 422, bad.text
    assert error_fields(bad) == {"medication_id"}


async def test_ca18_put_replaces_and_preserves_created_at(
    auth_client: AsyncClient, db_conn: AsyncConnection
) -> None:
    """CA-18: `PUT` cambia los campos, conserva `created_at` y refresca `updated_at`."""
    _, headers = await admin_actor(db_conn)
    drug_type_id, brand_id, cell_id = await make_catalogs(db_conn)
    other_cell = await create_cell_for_test(db_conn)
    tag = unique_suffix()
    medication_id = await create_medication_row(
        db_conn,
        f"Vieja {tag}",
        drug_type_id=drug_type_id,
        brand_id=brand_id,
        cell_id=cell_id,
        dose=f"500 mg {tag}",
    )
    before = await fetch_medication_row(db_conn, medication_id)
    assert before is not None

    response = await auth_client.put(
        medication_path(medication_id),
        headers=headers,
        json={
            "description": f"Nueva {tag}",
            "drug_type_id": drug_type_id,
            "brand_id": brand_id,
            "cell_id": other_cell,
            "dose": f"750 mg {tag}",
            "is_active": True,
        },
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert set(body) == ITEM_KEYS
    assert body["description"] == f"Nueva {tag}"
    assert body["cell_id"] == other_cell
    assert body["dose"] == f"750 mg {tag}"

    after = await fetch_medication_row(db_conn, medication_id)
    assert after is not None
    assert after["description"] == f"Nueva {tag}"
    assert after["dose"] == f"750 mg {tag}"
    assert after["cell_id"] == other_cell
    assert after["created_at"] == before["created_at"]
    assert after["updated_at"] is not None
    assert before["updated_at"] is not None
    assert after["updated_at"] > before["updated_at"]


async def test_ca19_put_reactivates_deactivates_and_changes_fks(
    auth_client: AsyncClient, db_conn: AsyncConnection
) -> None:
    """CA-19: `PUT` reactiva/desactiva y cambia FK (activos ⇒ 200, resto ⇒ error)."""
    _, headers = await admin_actor(db_conn)
    drug_a, brand_a, cell_a = await make_catalogs(db_conn, suffix=unique_suffix())
    drug_b, brand_b, cell_b = await make_catalogs(db_conn, suffix=unique_suffix())
    tag = unique_suffix()
    inactive_id = await create_medication_row(
        db_conn,
        f"Baja {tag}",
        drug_type_id=drug_a,
        brand_id=brand_a,
        cell_id=cell_a,
        dose=f"500 mg {tag}",
        is_active=False,
    )
    active_id = await create_medication_row(
        db_conn,
        f"Alta {tag}",
        drug_type_id=drug_a,
        brand_id=brand_a,
        cell_id=cell_a,
        dose=f"501 mg {tag}",
        is_active=True,
    )
    inactive_row = await fetch_medication_row(db_conn, inactive_id)
    assert inactive_row is not None

    moved = await auth_client.put(
        medication_path(active_id),
        headers=headers,
        json={
            "description": f"Alta {tag}",
            "drug_type_id": drug_b,
            "brand_id": brand_b,
            "cell_id": cell_b,
            "dose": f"501 mg {tag}",
            "is_active": True,
        },
    )
    assert moved.status_code == 200, moved.text
    assert moved.json()["drug_type_id"] == drug_b

    reactivated = await auth_client.put(
        medication_path(inactive_id),
        headers=headers,
        json={
            "description": str(inactive_row["description"]),
            "drug_type_id": drug_a,
            "brand_id": brand_a,
            "cell_id": cell_a,
            "dose": str(inactive_row["dose"]),
            "is_active": True,
        },
    )
    assert reactivated.status_code == 200, reactivated.text
    assert reactivated.json()["is_active"] is True

    moved_row = await fetch_medication_row(db_conn, active_id)
    assert moved_row is not None
    deactivated = await auth_client.put(
        medication_path(active_id),
        headers=headers,
        json={
            "description": str(moved_row["description"]),
            "drug_type_id": int(moved_row["drug_type_id"]),
            "brand_id": int(moved_row["brand_id"]),
            "cell_id": int(moved_row["cell_id"]),
            "dose": str(moved_row["dose"]),
            "is_active": False,
        },
    )
    assert deactivated.status_code == 200, deactivated.text
    assert deactivated.json()["is_active"] is False

    missing_fk = await nonexistent_catalog_id(db_conn, "brands")
    bad_fk = await auth_client.put(
        medication_path(active_id),
        headers=headers,
        json={
            "description": f"Alta {tag}",
            "drug_type_id": drug_a,
            "brand_id": missing_fk,
            "cell_id": cell_a,
            "dose": f"501 mg {tag}",
            "is_active": True,
        },
    )
    assert_error_envelope(bad_fk, 404, "brand_not_found")

    await db_conn.execute(text("UPDATE cells SET is_active = false WHERE id = :id"), {"id": cell_a})
    inactive_fk = await auth_client.put(
        medication_path(active_id),
        headers=headers,
        json={
            "description": f"Alta {tag}",
            "drug_type_id": drug_a,
            "brand_id": brand_a,
            "cell_id": cell_a,
            "dose": f"501 mg {tag}",
            "is_active": True,
        },
    )
    assert inactive_fk.status_code == 422, inactive_fk.text
    assert inactive_fk.json()["errors"] == [
        {"field": "cell_id", "message": "La celda está inactiva"}
    ]


async def test_ca20_put_distinguishes_foreign_from_own_identity(
    auth_client: AsyncClient, db_conn: AsyncConnection
) -> None:
    """CA-20: la identidad ajena colisiona; la propia (otra caja/espacios) pasa."""
    _, headers = await admin_actor(db_conn)
    drug_type_id, brand_id, cell_id = await make_catalogs(db_conn)
    tag = unique_suffix()
    first_desc, first_dose = f"Primera {tag}", f"100 mg {tag}"
    second_desc, second_dose = f"Segunda {tag}", f"200 mg {tag}"
    first_id = await create_medication_row(
        db_conn,
        first_desc,
        drug_type_id=drug_type_id,
        brand_id=brand_id,
        cell_id=cell_id,
        dose=first_dose,
    )
    second_id = await create_medication_row(
        db_conn,
        second_desc,
        drug_type_id=drug_type_id,
        brand_id=brand_id,
        cell_id=cell_id,
        dose=second_dose,
    )
    second_before = await fetch_medication_row(db_conn, second_id)
    assert second_before is not None

    foreign = await auth_client.put(
        medication_path(second_id),
        headers=headers,
        json={
            "description": first_desc.upper(),
            "drug_type_id": drug_type_id,
            "brand_id": brand_id,
            "cell_id": cell_id,
            "dose": first_dose.upper(),
            "is_active": True,
        },
    )
    body = assert_error_envelope(foreign, 409, "medication_exists")
    assert body["detail"] == "El medicamento ya existe"
    unchanged = await fetch_medication_row(db_conn, second_id)
    assert unchanged is not None
    assert unchanged["description"] == second_before["description"]
    assert unchanged["dose"] == second_before["dose"]

    for own_desc, own_dose in (
        (first_desc.upper(), first_dose),
        (f"  {first_desc}  ", f"  {first_dose}  "),
    ):
        response = await auth_client.put(
            medication_path(first_id),
            headers=headers,
            json={
                "description": own_desc,
                "drug_type_id": drug_type_id,
                "brand_id": brand_id,
                "cell_id": cell_id,
                "dose": own_dose,
                "is_active": True,
            },
        )
        assert response.status_code == 200, (own_desc, response.text)

    only_active = await auth_client.put(
        medication_path(first_id),
        headers=headers,
        json={
            "description": first_desc,
            "drug_type_id": drug_type_id,
            "brand_id": brand_id,
            "cell_id": cell_id,
            "dose": first_dose,
            "is_active": False,
        },
    )
    assert only_active.status_code == 200, only_active.text


async def test_ca21_put_validates_body_path_precedence_and_role(
    auth_client: AsyncClient, db_conn: AsyncConnection
) -> None:
    """CA-21: 404 inexistente, 422 de reemplazo parcial, precedencia y 403 de MEDICO."""
    _, admin_headers = await admin_actor(db_conn)
    _, medico_headers = await medico_actor(db_conn)
    drug_type_id, brand_id, cell_id = await make_catalogs(db_conn)
    tag = unique_suffix()
    medication_id = await create_medication_row(
        db_conn,
        f"Fija {tag}",
        drug_type_id=drug_type_id,
        brand_id=brand_id,
        cell_id=cell_id,
        dose=f"500 mg {tag}",
    )
    target = await fetch_medication_row(db_conn, medication_id)
    assert target is not None
    missing_id = await nonexistent_medication_id(db_conn)

    not_found = await auth_client.put(
        medication_path(missing_id),
        headers=admin_headers,
        json=medication_body(unique_suffix(), drug_type_id, brand_id, cell_id),
    )
    assert_error_envelope(not_found, 404, "medication_not_found")

    not_found_dup = await auth_client.put(
        medication_path(missing_id),
        headers=admin_headers,
        json={
            "description": str(target["description"]),
            "drug_type_id": int(target["drug_type_id"]),
            "brand_id": int(target["brand_id"]),
            "cell_id": int(target["cell_id"]),
            "dose": str(target["dose"]),
            "is_active": True,
        },
    )
    assert_error_envelope(not_found_dup, 404, "medication_not_found")

    medico_bad_body = await auth_client.put(
        medication_path(medication_id),
        headers=medico_headers,
        json={"description": f"Cambiada {tag}"},
    )
    assert_error_envelope(medico_bad_body, 403, "forbidden")

    full = medication_body(unique_suffix(), drug_type_id, brand_id, cell_id)
    for field in ("description", "drug_type_id", "brand_id", "cell_id", "dose", "is_active"):
        partial = {k: v for k, v in full.items() if k != field}
        response = await auth_client.put(
            medication_path(medication_id), headers=admin_headers, json=partial
        )
        assert error_fields(response) == {field}, (field, response.text)

    medico_response = await auth_client.put(
        medication_path(medication_id),
        headers=medico_headers,
        json=medication_body(unique_suffix(), drug_type_id, brand_id, cell_id),
    )
    forbidden = assert_error_envelope(medico_response, 403, "forbidden")
    assert forbidden["errors"] == []
    row = await fetch_medication_row(db_conn, medication_id)
    assert row is not None
    assert row["description"] == target["description"]
    assert row["dose"] == target["dose"]
    assert row["is_active"] == target["is_active"]


async def test_ca22_delete_is_a_logical_deactivation_without_body(
    auth_client: AsyncClient,
    db_conn: AsyncConnection,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """CA-22: `DELETE` ⇒ 204 vacío, la fila queda con `is_active = false` y log."""
    actor_id, headers = await admin_actor(db_conn)
    medication_id = await create_medication_row(db_conn, f"Por Desactivar {unique_suffix()}")
    caplog.set_level(logging.INFO)

    response = await auth_client.delete(medication_path(medication_id), headers=headers)
    assert response.status_code == 204, response.text
    assert response.content == b""

    row = await fetch_medication_row(db_conn, medication_id)
    assert row is not None
    assert row["is_active"] is False

    events = [
        record.msg
        for record in caplog.records
        if isinstance(record.msg, dict) and record.msg.get("event") == "medication.deactivated"
    ]
    assert len(events) == 1
    assert events[0]["medication_id"] == medication_id
    assert events[0]["actor_id"] == actor_id


async def test_ca23_delete_is_idempotent_and_guards_role_and_missing(
    auth_client: AsyncClient,
    db_conn: AsyncConnection,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """CA-23: segundo `DELETE` ⇒ 204 sin reescribir ni reloguear; 404 y 403 MEDICO."""
    _, admin_headers = await admin_actor(db_conn)
    _, medico_headers = await medico_actor(db_conn)
    medication_id = await create_medication_row(db_conn, f"Idempotente {unique_suffix()}")

    first = await auth_client.delete(medication_path(medication_id), headers=admin_headers)
    assert first.status_code == 204, first.text
    stamped = await fetch_medication_row(db_conn, medication_id)
    assert stamped is not None

    caplog.set_level(logging.INFO)
    caplog.clear()
    second = await auth_client.delete(medication_path(medication_id), headers=admin_headers)
    assert second.status_code == 204, second.text
    assert second.content == b""

    again = await fetch_medication_row(db_conn, medication_id)
    assert again is not None
    assert again["is_active"] is False
    assert again["updated_at"] == stamped["updated_at"]
    assert not [
        record
        for record in caplog.records
        if isinstance(record.msg, dict) and record.msg.get("event") == "medication.deactivated"
    ]

    missing = await auth_client.delete(
        medication_path(await nonexistent_medication_id(db_conn)), headers=admin_headers
    )
    assert_error_envelope(missing, 404, "medication_not_found")

    guarded_id = await create_medication_row(db_conn, f"Protegida {unique_suffix()}")
    medico_response = await auth_client.delete(medication_path(guarded_id), headers=medico_headers)
    assert_error_envelope(medico_response, 403, "forbidden")
    guarded = await fetch_medication_row(db_conn, guarded_id)
    assert guarded is not None
    assert guarded["is_active"] is True


async def test_ca24_delete_with_visit_medications_keeps_the_reference(
    auth_client: AsyncClient, db_conn: AsyncConnection
) -> None:
    """CA-24: desactivar un medicamento con visitas está permitido y no borra nada."""
    _, headers = await admin_actor(db_conn)
    medication_id = await create_medication_row(db_conn, f"Referenciada {unique_suffix()}")
    visit_id = await create_visit_for_test(db_conn)

    await db_conn.execute(
        text(
            """
            INSERT INTO visit_medications (visit_id, medication_id, quantity, instructions)
            VALUES (:visit_id, :medication_id, 2, 'Tomar con agua')
            """
        ),
        {"visit_id": visit_id, "medication_id": medication_id},
    )

    response = await auth_client.delete(medication_path(medication_id), headers=headers)
    assert response.status_code == 204, response.text

    row = await fetch_medication_row(db_conn, medication_id)
    assert row is not None
    assert row["is_active"] is False

    reference = await db_conn.execute(
        text("SELECT id, medication_id FROM visit_medications WHERE medication_id = :id"),
        {"id": medication_id},
    )
    found = reference.mappings().one_or_none()
    assert found is not None
    assert found["medication_id"] == medication_id
    assert await count_medications(db_conn) >= 1
    assert int(await fetch_scalar(db_conn, "SELECT count(*) FROM medications")) >= 1


async def create_brand_for_test(conn: AsyncConnection) -> int:
    """Crea una marca extra para las pruebas de unicidad."""
    from tests.db_support import create_brand as _create_brand

    return await _create_brand(conn, f"Otra Marca {unique_suffix()}")


async def create_cell_for_test(conn: AsyncConnection) -> int:
    """Crea una celda extra (con su estante) para las pruebas de `PUT`."""
    from tests.db_support import create_cell as _create_cell

    return await _create_cell(conn, description=f"Otra Celda {unique_suffix()}")


async def create_visit_for_test(conn: AsyncConnection) -> int:
    """Crea una visita válida por SQL directo para CA-24."""
    from tests.db_support import create_visit as _create_visit

    return await _create_visit(conn)
