"""Logs de FEAT-012 (regla 37): eventos sin datos personales del medico."""

from __future__ import annotations

import logging

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncConnection

from tests.db_support import unique_cedula, unique_suffix
from tests.doctors_support import (
    DOCTORS_PATH,
    admin_actor,
    create_doctor_row,
    doctor_body,
    doctor_path,
    set_doctor_active,
    visit_for_doctor,
)


def _events(records: list[logging.LogRecord], name: str) -> list[logging.LogRecord]:
    """Filtra los registros de structlog por nombre de evento."""
    return [
        record
        for record in records
        if isinstance(record.msg, dict) and record.msg.get("event") == name
    ]


def _payload(record: logging.LogRecord) -> str:
    """Serializa el registro completo para inspeccionarlo como texto."""
    return f"{record.msg} {record.getMessage()}"


async def test_rule37_events_carry_ids_and_never_leak_doctor_data(
    auth_client: AsyncClient,
    db_conn: AsyncConnection,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Regla 37: `doctor.created/updated/deactivated` con ids y sin datos personales."""
    actor_id, headers = await admin_actor(db_conn)
    tag = unique_suffix()
    cedula = unique_cedula()
    name = f"Confidencial {tag}"
    lab_shift = f"Tanda {tag}"
    specialty = f"Especialidad {tag}"

    caplog.set_level(logging.INFO)
    created = await auth_client.post(
        DOCTORS_PATH,
        headers=headers,
        json={
            "name": name,
            "cedula": cedula,
            "lab_shift": lab_shift,
            "specialty": specialty,
            "is_active": True,
        },
    )
    assert created.status_code == 201, created.text
    doctor_id = created.json()["id"]

    updated = await auth_client.put(
        doctor_path(doctor_id),
        headers=headers,
        json={
            "name": name,
            "cedula": cedula,
            "lab_shift": lab_shift,
            "specialty": specialty,
            "is_active": True,
        },
    )
    assert updated.status_code == 200, updated.text

    deleted = await auth_client.delete(doctor_path(doctor_id), headers=headers)
    assert deleted.status_code == 204, deleted.text

    records = list(caplog.records)
    created_events = _events(records, "doctor.created")
    updated_events = _events(records, "doctor.updated")
    deactivated_events = _events(records, "doctor.deactivated")

    assert len(created_events) == 1
    assert len(updated_events) == 1
    assert len(deactivated_events) == 1

    for record in (*created_events, *updated_events, *deactivated_events):
        payload = record.msg
        assert isinstance(payload, dict)
        assert payload.get("doctor_id") == doctor_id
        assert payload.get("actor_id") == actor_id

        text = _payload(record)
        assert cedula not in text
        assert name not in text
        assert lab_shift not in text
        assert specialty not in text
        assert tag not in text


async def test_rule34_deleting_already_inactive_emits_no_second_event(
    auth_client: AsyncClient,
    db_conn: AsyncConnection,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Regla 34: un `DELETE` sobre un medico ya inactivo no registra evento."""
    _, headers = await admin_actor(db_conn)
    doctor_id = await create_doctor_row(db_conn, name=f"Silencioso {unique_suffix()}")
    await set_doctor_active(db_conn, doctor_id, False)

    caplog.set_level(logging.INFO)
    response = await auth_client.delete(doctor_path(doctor_id), headers=headers)
    assert response.status_code == 204, response.text

    assert _events(list(caplog.records), "doctor.deactivated") == []


async def test_rule37_put_that_deactivates_emits_deactivated_event(
    auth_client: AsyncClient,
    db_conn: AsyncConnection,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Regla 37: un `PUT` que pasa `is_active` a `false` emite `doctor.deactivated`."""
    _, headers = await admin_actor(db_conn)
    tag = unique_suffix()
    cedula = unique_cedula()
    created = await auth_client.post(
        DOCTORS_PATH, headers=headers, json=doctor_body(tag, cedula=cedula)
    )
    assert created.status_code == 201, created.text

    caplog.set_level(logging.INFO)
    response = await auth_client.put(
        doctor_path(created.json()["id"]),
        headers=headers,
        json={
            "name": f"Dr. {tag}",
            "cedula": cedula,
            "lab_shift": "Matutina",
            "specialty": "General",
            "is_active": False,
        },
    )
    assert response.status_code == 200, response.text

    records = list(caplog.records)
    assert len(_events(records, "doctor.deactivated")) == 1
    assert _events(records, "doctor.updated") == []


async def test_rule33_blocked_delete_emits_no_deactivation_event(
    auth_client: AsyncClient,
    db_conn: AsyncConnection,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Regla 33: un `DELETE` bloqueado por visitas no registra `doctor.deactivated`."""
    _, headers = await admin_actor(db_conn)
    doctor_id = await create_doctor_row(db_conn)
    await visit_for_doctor(db_conn, doctor_id)

    caplog.set_level(logging.INFO)
    response = await auth_client.delete(doctor_path(doctor_id), headers=headers)
    assert response.status_code == 409, response.text

    assert _events(list(caplog.records), "doctor.deactivated") == []
