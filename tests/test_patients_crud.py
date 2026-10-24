"""Creacion, detalle, actualizacion y desactivacion de FEAT-011 (CA-12 a CA-30)."""

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
    error_fields,
    fetch_patient_row,
    patient_body,
    patient_path,
    set_patient_active,
)


async def test_ca12_create_student_with_carnet_returns_201_and_defaults_active(
    auth_client: AsyncClient, db_conn: AsyncConnection
) -> None:
    """CA-12: `POST` valido de estudiante ⇒ 201 con forma D-4 y `is_active` por defecto."""
    _, headers = await admin_actor(db_conn)
    tag = unique_suffix()
    cedula = unique_cedula()
    body = {
        "name": "  Oscar Mora  ",
        "cedula": f" {cedula} ",
        "carnet": f" A{tag} ",
        "patient_type": "ESTUDIANTE",
    }

    response = await auth_client.post(PATIENTS_PATH, headers=headers, json=body)
    assert response.status_code == 201, response.text
    created = response.json()
    assert set(created) == ITEM_KEYS
    assert created["name"] == "Oscar Mora"
    assert created["cedula"] == cedula
    assert created["carnet"] == f"A{tag}"
    assert created["patient_type"] == "ESTUDIANTE"
    assert created["is_active"] is True

    row = await fetch_patient_row(db_conn, created["id"])
    assert row is not None
    assert row["created_at"] is not None
    assert row["updated_at"] is not None


async def test_ca13_create_explicit_inactive_is_allowed(
    auth_client: AsyncClient, db_conn: AsyncConnection
) -> None:
    """CA-13: `POST` con `is_active: false` ⇒ 201 y el paciente queda inactivo."""
    _, headers = await admin_actor(db_conn)
    tag = unique_suffix()

    response = await auth_client.post(
        PATIENTS_PATH, headers=headers, json=patient_body(tag, is_active=False)
    )
    assert response.status_code == 201, response.text
    assert response.json()["is_active"] is False

    detail = await auth_client.get(patient_path(response.json()["id"]), headers=headers)
    assert detail.status_code == 200
    assert detail.json()["is_active"] is False


async def test_ca14_cedula_and_carnet_duplicates_collide_409(
    auth_client: AsyncClient, db_conn: AsyncConnection
) -> None:
    """CA-14: cedula repetida ⇒ `patient_exists`; carnet repetido ⇒ `patient_carnet_exists`."""
    _, headers = await admin_actor(db_conn)
    tag = unique_suffix()
    cedula = unique_cedula()
    carnet = f"B{tag}"
    first = await auth_client.post(
        PATIENTS_PATH,
        headers=headers,
        json=patient_body(tag, cedula=cedula, carnet=carnet),
    )
    assert first.status_code == 201, first.text

    duplicate_cedula = await auth_client.post(
        PATIENTS_PATH, headers=headers, json=patient_body(f"{tag}b", cedula=cedula)
    )
    body = assert_error_envelope(duplicate_cedula, 409, "patient_exists")
    assert body["detail"] == "El paciente ya existe"

    duplicate_carnet = await auth_client.post(
        PATIENTS_PATH,
        headers=headers,
        json=patient_body(f"{tag}c", carnet=carnet.lower()),
    )
    carnet_body = assert_error_envelope(duplicate_carnet, 409, "patient_carnet_exists")
    assert carnet_body["detail"] == "El carnet ya está registrado"

    # Un paciente inactivo tambien bloquea la cedula y el carnet (reglas 22 y 23).
    inactive_cedula = unique_cedula()
    inactive_carnet = f"D{tag}"
    inactive_id = await create_patient_row(
        db_conn, cedula=inactive_cedula, carnet=inactive_carnet, is_active=False
    )
    assert inactive_id > 0

    assert_error_envelope(
        await auth_client.post(
            PATIENTS_PATH, headers=headers, json=patient_body(f"{tag}d", cedula=inactive_cedula)
        ),
        409,
        "patient_exists",
    )
    assert_error_envelope(
        await auth_client.post(
            PATIENTS_PATH, headers=headers, json=patient_body(f"{tag}e", carnet=inactive_carnet)
        ),
        409,
        "patient_carnet_exists",
    )


async def test_ca15_two_patients_without_carnet_are_allowed(
    auth_client: AsyncClient, db_conn: AsyncConnection
) -> None:
    """CA-15: dos pacientes con `carnet: null` conviven (indice unico parcial)."""
    _, headers = await admin_actor(db_conn)
    tag = unique_suffix()

    for suffix in ("a", "b"):
        response = await auth_client.post(
            PATIENTS_PATH, headers=headers, json=patient_body(f"{tag}{suffix}", carnet=None)
        )
        assert response.status_code == 201, response.text
        assert response.json()["carnet"] is None


async def test_ca16_ca17_ca18_ca19_field_validation_messages(
    auth_client: AsyncClient, db_conn: AsyncConnection
) -> None:
    """CA-16…CA-19: mensajes en espanol con el campo exacto en `errors[0].field`."""
    _, headers = await admin_actor(db_conn)
    tag = unique_suffix()
    valid = patient_body(tag)

    cases = [
        ("cedula", {"cedula": "12345"}, "La cédula debe tener 11 dígitos"),
        ("patient_type", {"patient_type": "estudiante"}, "El tipo de paciente no es válido"),
        ("patient_type", {"patient_type": "DESCONOCIDO"}, "El tipo de paciente no es válido"),
        ("name", {"name": "   "}, "El nombre es obligatorio"),
        (
            "carnet",
            {"patient_type": "ESTUDIANTE", "carnet": "   "},
            "El carnet es obligatorio para estudiantes",
        ),
    ]
    for field, override, message in cases:
        response = await auth_client.post(
            PATIENTS_PATH, headers=headers, json={**valid, **override}
        )
        body = assert_error_envelope(response, 422, "validation_error")
        assert body["errors"][0]["field"] == field, (override, body)
        assert body["errors"][0]["message"] == message, (override, body)

    missing_student_carnet = await auth_client.post(
        PATIENTS_PATH,
        headers=headers,
        json={"name": "Sin carnet", "cedula": unique_cedula(), "patient_type": "ESTUDIANTE"},
    )
    body = assert_error_envelope(missing_student_carnet, 422, "validation_error")
    assert error_fields(missing_student_carnet) == {"carnet"}
    assert body["errors"][0]["message"] == "El carnet es obligatorio para estudiantes"

    too_long_name = await auth_client.post(
        PATIENTS_PATH, headers=headers, json={**valid, "name": "x" * 151}
    )
    assert_error_envelope(too_long_name, 422, "validation_error")
    assert error_fields(too_long_name) == {"name"}

    longest_name = await auth_client.post(
        PATIENTS_PATH,
        headers=headers,
        json={**patient_body(f"{tag}long"), "name": "y" * 150},
    )
    assert longest_name.status_code == 201, longest_name.text


async def test_ca20_carnet_optional_for_non_students(
    auth_client: AsyncClient, db_conn: AsyncConnection
) -> None:
    """CA-20: `EMPLEADO`/`PROFESOR`/`OTROS` aceptan carnet ausente o en blanco."""
    _, headers = await admin_actor(db_conn)
    tag = unique_suffix()

    for patient_type in ("EMPLEADO", "PROFESOR", "OTROS"):
        without = await auth_client.post(
            PATIENTS_PATH,
            headers=headers,
            json=patient_body(f"{tag}{patient_type}", patient_type=patient_type),
        )
        assert without.status_code == 201, without.text
        assert without.json()["carnet"] is None

        blank = await auth_client.post(
            PATIENTS_PATH,
            headers=headers,
            json=patient_body(f"{tag}{patient_type}b", patient_type=patient_type, carnet="   "),
        )
        assert blank.status_code == 201, blank.text
        assert blank.json()["carnet"] is None
        row = await fetch_patient_row(db_conn, blank.json()["id"])
        assert row is not None and row["carnet"] is None


async def test_ca21_ca22_detail_returns_active_and_inactive_and_404(
    auth_client: AsyncClient, db_conn: AsyncConnection
) -> None:
    """CA-21 / CA-22: detalle de activo e inactivo; inexistente ⇒ 404."""
    _, headers = await admin_actor(db_conn)
    tag = unique_suffix()
    patient_id = await create_patient_row(db_conn, name=f"Detalle {tag}", is_active=False)

    response = await auth_client.get(patient_path(patient_id), headers=headers)
    assert response.status_code == 200, response.text
    body = response.json()
    assert set(body) == ITEM_KEYS
    assert body["is_active"] is False
    assert FORBIDDEN_KEYS.isdisjoint(body)

    missing = await auth_client.get(patient_path(patient_id + 999_999), headers=headers)
    missing_body = assert_error_envelope(missing, 404, "patient_not_found")
    assert missing_body["detail"] == "El paciente no existe"

    not_integer = await auth_client.get(f"{PATIENTS_PATH}/abc", headers=headers)
    assert_error_envelope(not_integer, 422, "validation_error")


async def test_ca23_ca24_full_replacement_keeps_created_at_and_clears_carnet(
    auth_client: AsyncClient, db_conn: AsyncConnection
) -> None:
    """CA-23 / CA-24: `PUT` completo conserva `created_at` y un `carnet` ausente lo borra."""
    _, headers = await admin_actor(db_conn)
    tag = unique_suffix()
    carnet = f"E{tag}"
    created = await auth_client.post(
        PATIENTS_PATH, headers=headers, json=patient_body(tag, carnet=carnet)
    )
    assert created.status_code == 201, created.text
    patient_id = created.json()["id"]
    before = await fetch_patient_row(db_conn, patient_id)
    assert before is not None

    new_cedula = unique_cedula()
    replaced = await auth_client.put(
        patient_path(patient_id),
        headers=headers,
        json={
            "name": "Nombre Nuevo",
            "cedula": new_cedula,
            "patient_type": "EMPLEADO",
            "is_active": True,
        },
    )
    assert replaced.status_code == 200, replaced.text
    body = replaced.json()
    assert set(body) == ITEM_KEYS
    assert body["name"] == "Nombre Nuevo"
    assert body["cedula"] == new_cedula
    assert body["carnet"] is None

    after = await fetch_patient_row(db_conn, patient_id)
    assert after is not None
    assert after["created_at"] == before["created_at"]
    assert after["updated_at"] > before["updated_at"]
    assert after["carnet"] is None


async def test_ca25_put_with_own_identity_does_not_conflict(
    auth_client: AsyncClient, db_conn: AsyncConnection
) -> None:
    """CA-25: reenviar la propia cedula y el propio carnet (otra capitalizacion) ⇒ 200."""
    _, headers = await admin_actor(db_conn)
    tag = unique_suffix()
    cedula = unique_cedula()
    carnet = f"F{tag}"
    created = await auth_client.post(
        PATIENTS_PATH,
        headers=headers,
        json=patient_body(tag, cedula=cedula, carnet=carnet, patient_type="ESTUDIANTE"),
    )
    assert created.status_code == 201, created.text
    patient_id = created.json()["id"]

    response = await auth_client.put(
        patient_path(patient_id),
        headers=headers,
        json={
            "name": f"Paciente {tag}",
            "cedula": cedula,
            "carnet": carnet.lower(),
            "patient_type": "ESTUDIANTE",
            "is_active": True,
        },
    )
    assert response.status_code == 200, response.text
    assert response.json()["cedula"] == cedula
    assert response.json()["carnet"] == carnet.lower()


async def test_ca26_put_with_foreign_cedula_or_carnet_conflicts(
    auth_client: AsyncClient, db_conn: AsyncConnection
) -> None:
    """CA-26: cedula o carnet de otro paciente ⇒ 409 con su codigo propio."""
    _, headers = await admin_actor(db_conn)
    tag = unique_suffix()
    other_cedula = unique_cedula()
    other_carnet = f"G{tag}"
    await create_patient_row(db_conn, cedula=other_cedula, carnet=other_carnet)

    target = await auth_client.post(PATIENTS_PATH, headers=headers, json=patient_body(f"{tag}t"))
    assert target.status_code == 201, target.text
    target_id = target.json()["id"]

    foreign_cedula = await auth_client.put(
        patient_path(target_id),
        headers=headers,
        json={
            "name": f"Paciente {tag}",
            "cedula": other_cedula,
            "patient_type": "EMPLEADO",
            "is_active": True,
        },
    )
    await_body = assert_error_envelope(foreign_cedula, 409, "patient_exists")
    assert await_body["detail"] == "El paciente ya existe"

    foreign_carnet = await auth_client.put(
        patient_path(target_id),
        headers=headers,
        json={
            "name": f"Paciente {tag}",
            "cedula": target.json()["cedula"],
            "carnet": other_carnet.upper(),
            "patient_type": "EMPLEADO",
            "is_active": True,
        },
    )
    carnet_body = assert_error_envelope(foreign_carnet, 409, "patient_carnet_exists")
    assert carnet_body["detail"] == "El carnet ya está registrado"


async def test_ca27_put_reactivates_and_put_partial_is_rejected(
    auth_client: AsyncClient, db_conn: AsyncConnection
) -> None:
    """CA-27 / CA-28: `PUT` reactiva con `is_active: true`; body parcial ⇒ 422."""
    _, headers = await admin_actor(db_conn)
    tag = unique_suffix()
    patient_id = await create_patient_row(db_conn, name=f"Reactiva {tag}", is_active=False)

    reactivated = await auth_client.put(
        patient_path(patient_id),
        headers=headers,
        json={
            "name": f"Reactiva {tag}",
            "cedula": (await fetch_patient_row(db_conn, patient_id))["cedula"],  # type: ignore[index]
            "patient_type": "EMPLEADO",
            "is_active": True,
        },
    )
    assert reactivated.status_code == 200, reactivated.text
    assert reactivated.json()["is_active"] is True

    partial = await auth_client.put(
        patient_path(patient_id), headers=headers, json={"name": "Solo nombre"}
    )
    body = assert_error_envelope(partial, 422, "validation_error")
    missing = {error["field"] for error in body["errors"]}
    assert {"cedula", "patient_type", "is_active"} <= missing

    missing_id = await auth_client.put(
        patient_path(patient_id + 999_999),
        headers=headers,
        json={
            "name": "Nadie",
            "cedula": unique_cedula(),
            "patient_type": "EMPLEADO",
            "is_active": True,
        },
    )
    assert_error_envelope(missing_id, 404, "patient_not_found")

    student_without_carnet = await auth_client.put(
        patient_path(patient_id),
        headers=headers,
        json={
            "name": f"Reactiva {tag}",
            "cedula": (await fetch_patient_row(db_conn, patient_id))["cedula"],  # type: ignore[index]
            "patient_type": "ESTUDIANTE",
            "is_active": True,
        },
    )
    body = assert_error_envelope(student_without_carnet, 422, "validation_error")
    assert body["errors"][0]["field"] == "carnet"


async def test_ca29_ca30_delete_is_idempotent_and_keeps_updated_at(
    auth_client: AsyncClient, db_conn: AsyncConnection
) -> None:
    """CA-29 / CA-30: `DELETE` ⇒ 204; repetirlo no cambia la fila ni `updated_at`."""
    _, headers = await admin_actor(db_conn)
    tag = unique_suffix()
    created = await auth_client.post(PATIENTS_PATH, headers=headers, json=patient_body(tag))
    assert created.status_code == 201, created.text
    patient_id = created.json()["id"]

    first = await auth_client.delete(patient_path(patient_id), headers=headers)
    assert first.status_code == 204, first.text
    assert first.content == b""
    row = await fetch_patient_row(db_conn, patient_id)
    assert row is not None
    assert row["is_active"] is False
    updated_at = row["updated_at"]

    second = await auth_client.delete(patient_path(patient_id), headers=headers)
    assert second.status_code == 204, second.text
    again = await fetch_patient_row(db_conn, patient_id)
    assert again is not None
    assert again["updated_at"] == updated_at

    detail = await auth_client.get(patient_path(patient_id), headers=headers)
    assert detail.status_code == 200
    assert detail.json()["is_active"] is False


async def test_ca31_delete_missing_patient_is_404(
    auth_client: AsyncClient, db_conn: AsyncConnection
) -> None:
    """CA-31: `DELETE` de un paciente inexistente ⇒ 404 `patient_not_found`."""
    _, headers = await admin_actor(db_conn)
    response = await auth_client.delete(patient_path(999_999_999), headers=headers)
    assert_error_envelope(response, 404, "patient_not_found")

    not_integer = await auth_client.delete(f"{PATIENTS_PATH}/abc", headers=headers)
    assert_error_envelope(not_integer, 422, "validation_error")


async def test_rule33_deactivate_already_inactive_does_not_touch_row(
    auth_client: AsyncClient, db_conn: AsyncConnection
) -> None:
    """Regla 33: desactivar un paciente ya inactivo no refresca `updated_at`."""
    _, headers = await admin_actor(db_conn)
    tag = unique_suffix()
    patient_id = await create_patient_row(db_conn, name=f"Inactivo {tag}")
    await set_patient_active(db_conn, patient_id, False)
    before = await fetch_patient_row(db_conn, patient_id)
    assert before is not None

    response = await auth_client.delete(patient_path(patient_id), headers=headers)
    assert response.status_code == 204, response.text

    after = await fetch_patient_row(db_conn, patient_id)
    assert after is not None
    assert after["updated_at"] == before["updated_at"]
    assert after["is_active"] is False
