"""CRUD de FEAT-012 (CA-15 a CA-28)."""

from __future__ import annotations

from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncConnection

from tests.db_support import unique_cedula
from tests.doctors_support import (
    DOCTORS_PATH,
    ITEM_KEYS,
    admin_actor,
    assert_error_envelope,
    create_doctor_row,
    doctor_body,
    doctor_path,
    fetch_doctor_row,
    nonexistent_doctor_id,
    set_doctor_active,
    unique_suffix,
)


async def test_ca15_create_defaults_to_active(
    auth_client: AsyncClient, db_conn: AsyncConnection
) -> None:
    """CA-15: `POST` valido ⇒ 201 con la forma de D-2 y `is_active: true`."""
    _, headers = await admin_actor(db_conn)
    tag = unique_suffix()

    response = await auth_client.post(DOCTORS_PATH, headers=headers, json=doctor_body(tag))
    assert response.status_code == 201, response.text
    item = response.json()
    assert set(item) == ITEM_KEYS
    assert item["is_active"] is True
    assert item["name"] == f"Dr. {tag}"

    row = await fetch_doctor_row(db_conn, item["id"])
    assert row is not None
    assert row["is_active"] is True


async def test_ca16_create_explicit_inactive(
    auth_client: AsyncClient, db_conn: AsyncConnection
) -> None:
    """CA-16: `POST` con `is_active: false` ⇒ 201 con `is_active: false`."""
    _, headers = await admin_actor(db_conn)

    response = await auth_client.post(
        DOCTORS_PATH, headers=headers, json=doctor_body(unique_suffix(), is_active=False)
    )
    assert response.status_code == 201, response.text
    assert response.json()["is_active"] is False


async def test_ca17_cedula_duplicate_collides_including_inactive(
    auth_client: AsyncClient, db_conn: AsyncConnection
) -> None:
    """CA-17: cedula duplicada (activa o inactiva) ⇒ 409 `doctor_exists`."""
    _, headers = await admin_actor(db_conn)
    cedula = unique_cedula()
    existing = await create_doctor_row(db_conn, cedula=cedula)
    await set_doctor_active(db_conn, existing, False)

    duplicate = await auth_client.post(
        DOCTORS_PATH, headers=headers, json=doctor_body(unique_suffix(), cedula=cedula)
    )
    body = assert_error_envelope(duplicate, 409, "doctor_exists")
    assert body["detail"] == "El médico ya existe"


async def test_ca20_field_validation_messages(
    auth_client: AsyncClient, db_conn: AsyncConnection
) -> None:
    """CA-19/CA-20: mensajes de campo en espanol para name/lab_shift/specialty."""
    _, headers = await admin_actor(db_conn)

    cases = [
        ({"name": "  "}, "name", "El nombre es obligatorio"),
        ({"cedula": "123"}, "cedula", "La cédula debe tener 11 dígitos"),
        ({"lab_shift": "   "}, "lab_shift", "La tanda de trabajo es obligatoria"),
        ({"specialty": " "}, "specialty", "La especialidad es obligatoria"),
    ]
    for overrides, field, message in cases:
        response = await auth_client.post(
            DOCTORS_PATH,
            headers=headers,
            json={**doctor_body(unique_suffix()), **overrides},
        )
        body = assert_error_envelope(response, 422, "validation_error")
        assert body["errors"][0]["field"] == field, body
        assert body["errors"][0]["message"] == message, body


async def test_ca21_doctor_without_user_is_creatable(
    auth_client: AsyncClient, db_conn: AsyncConnection
) -> None:
    """CA-21: un medico puede existir sin usuario asociado (§10)."""
    _, headers = await admin_actor(db_conn)

    response = await auth_client.post(
        DOCTORS_PATH, headers=headers, json=doctor_body(unique_suffix())
    )
    assert response.status_code == 201, response.text
    doctor_id = response.json()["id"]
    assigned = await db_conn.scalar(
        __import__("sqlalchemy").text("SELECT count(*) FROM users WHERE doctor_id = :doctor_id"),
        {"doctor_id": doctor_id},
    )
    assert assigned == 0


async def test_ca22_detail_returns_active_and_inactive_and_404(
    auth_client: AsyncClient, db_conn: AsyncConnection
) -> None:
    """CA-22/CA-23: detalle con 200 (incluido inactivo), 404 y 422."""
    _, headers = await admin_actor(db_conn)
    doctor_id = await create_doctor_row(db_conn)

    detail = await auth_client.get(doctor_path(doctor_id), headers=headers)
    assert detail.status_code == 200, detail.text
    assert detail.json()["id"] == doctor_id

    await set_doctor_active(db_conn, doctor_id, False)
    inactive = await auth_client.get(doctor_path(doctor_id), headers=headers)
    assert inactive.status_code == 200, inactive.text
    assert inactive.json()["is_active"] is False

    missing = await auth_client.get(
        doctor_path(await nonexistent_doctor_id(db_conn)), headers=headers
    )
    body = assert_error_envelope(missing, 404, "doctor_not_found")
    assert body["detail"] == "El médico no existe"

    invalid = await auth_client.get(f"{DOCTORS_PATH}/no-entero", headers=headers)
    assert_error_envelope(invalid, 422, "validation_error")


async def test_ca24_ca25_full_replacement_keeps_created_at_and_own_cedula(
    auth_client: AsyncClient, db_conn: AsyncConnection
) -> None:
    """CA-24/CA-25: `PUT` completo conserva `created_at`; cedula propia no colisiona."""
    _, headers = await admin_actor(db_conn)
    doctor_id = await create_doctor_row(db_conn, name="Original")
    before = await fetch_doctor_row(db_conn, doctor_id)
    assert before is not None

    payload = doctor_body(unique_suffix(), cedula=before["cedula"], lab_shift="Vespertina")
    updated = await auth_client.put(doctor_path(doctor_id), headers=headers, json=payload)
    assert updated.status_code == 200, updated.text
    assert updated.json()["lab_shift"] == "Vespertina"

    after = await fetch_doctor_row(db_conn, doctor_id)
    assert after is not None
    assert after["created_at"] == before["created_at"]
    assert after["updated_at"] > before["updated_at"]


async def test_ca25_ca27_foreign_cedula_conflicts_and_missing_is_404(
    auth_client: AsyncClient, db_conn: AsyncConnection
) -> None:
    """CA-25/CA-27: cedula de otro medico ⇒ 409; id inexistente ⇒ 404; parcial ⇒ 422."""
    _, headers = await admin_actor(db_conn)
    other = await create_doctor_row(db_conn, cedula=unique_cedula())
    target = await create_doctor_row(db_conn)

    other_row = await fetch_doctor_row(db_conn, other)
    assert other_row is not None
    conflict = await auth_client.put(
        doctor_path(target),
        headers=headers,
        json={**doctor_body(unique_suffix()), "cedula": other_row["cedula"]},
    )
    assert_error_envelope(conflict, 409, "doctor_exists")

    missing = await auth_client.put(
        doctor_path(await nonexistent_doctor_id(db_conn)),
        headers=headers,
        json=doctor_body(unique_suffix()),
    )
    assert_error_envelope(missing, 404, "doctor_not_found")

    partial = await auth_client.put(
        doctor_path(target),
        headers=headers,
        json={"name": "Sin el resto"},
    )
    assert_error_envelope(partial, 422, "validation_error")


async def test_ca26_put_reactivates_and_deactivates(
    auth_client: AsyncClient, db_conn: AsyncConnection
) -> None:
    """CA-26: `PUT` desactiva con `is_active: false` y reactiva con `true`."""
    _, headers = await admin_actor(db_conn)
    doctor_id = await create_doctor_row(db_conn)

    deactivate = await auth_client.put(
        doctor_path(doctor_id),
        headers=headers,
        json=doctor_body(unique_suffix(), is_active=False),
    )
    assert deactivate.status_code == 200, deactivate.text
    assert deactivate.json()["is_active"] is False

    row = await fetch_doctor_row(db_conn, doctor_id)
    assert row is not None and row["is_active"] is False

    reactivate = await auth_client.put(
        doctor_path(doctor_id),
        headers=headers,
        json=doctor_body(unique_suffix(), is_active=True),
    )
    assert reactivate.status_code == 200, reactivate.text
    assert reactivate.json()["is_active"] is True


async def test_ca28_put_with_blank_lab_shift_or_specialty_is_422(
    auth_client: AsyncClient, db_conn: AsyncConnection
) -> None:
    """CA-28: `PUT` con `lab_shift` o `specialty` vacios ⇒ 422 con el campo."""
    _, headers = await admin_actor(db_conn)
    doctor_id = await create_doctor_row(db_conn)

    for field in ("lab_shift", "specialty"):
        response = await auth_client.put(
            doctor_path(doctor_id),
            headers=headers,
            json={**doctor_body(unique_suffix()), field: "   "},
        )
        body = assert_error_envelope(response, 422, "validation_error")
        assert body["errors"][0]["field"] == field, body
