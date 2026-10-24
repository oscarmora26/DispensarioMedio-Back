"""Logs de FEAT-011 (regla 36): eventos sin datos personales del paciente."""

from __future__ import annotations

import logging

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncConnection

from tests.db_support import unique_cedula, unique_suffix
from tests.patients_support import (
    PATIENTS_PATH,
    admin_actor,
    create_patient_row,
    patient_body,
    patient_path,
    set_patient_active,
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


async def test_rule36_events_carry_ids_and_never_leak_patient_data(
    auth_client: AsyncClient,
    db_conn: AsyncConnection,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Regla 36: `patient.created/updated/deactivated` con ids y sin datos personales."""
    actor_id, headers = await admin_actor(db_conn)
    tag = unique_suffix()
    cedula = unique_cedula()
    carnet = f"L{tag}"
    name = f"Confidencial {tag}"

    caplog.set_level(logging.INFO)
    created = await auth_client.post(
        PATIENTS_PATH,
        headers=headers,
        json={
            "name": name,
            "cedula": cedula,
            "carnet": carnet,
            "patient_type": "ESTUDIANTE",
            "is_active": True,
        },
    )
    assert created.status_code == 201, created.text
    patient_id = created.json()["id"]

    updated = await auth_client.put(
        patient_path(patient_id),
        headers=headers,
        json={
            "name": name,
            "cedula": cedula,
            "carnet": carnet,
            "patient_type": "ESTUDIANTE",
            "is_active": True,
        },
    )
    assert updated.status_code == 200, updated.text

    deleted = await auth_client.delete(patient_path(patient_id), headers=headers)
    assert deleted.status_code == 204, deleted.text

    records = list(caplog.records)
    created_events = _events(records, "patient.created")
    updated_events = _events(records, "patient.updated")
    deactivated_events = _events(records, "patient.deactivated")

    assert len(created_events) == 1
    assert len(updated_events) == 1
    assert len(deactivated_events) == 1

    for record in (*created_events, *updated_events, *deactivated_events):
        payload = record.msg
        assert isinstance(payload, dict)
        assert payload.get("patient_id") == patient_id
        assert payload.get("actor_id") == actor_id

        text = _payload(record)
        assert cedula not in text
        assert carnet.lower() not in text.lower()
        assert name not in text
        assert tag not in text


async def test_rule36_deleting_already_inactive_emits_no_second_event(
    auth_client: AsyncClient,
    db_conn: AsyncConnection,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Regla 33: un `DELETE` sobre un paciente ya inactivo no registra evento."""
    _, headers = await admin_actor(db_conn)
    patient_id = await create_patient_row(db_conn, name=f"Silencioso {unique_suffix()}")
    await set_patient_active(db_conn, patient_id, False)

    caplog.set_level(logging.INFO)
    response = await auth_client.delete(patient_path(patient_id), headers=headers)
    assert response.status_code == 204, response.text

    assert _events(list(caplog.records), "patient.deactivated") == []


async def test_rule36_put_that_deactivates_emits_deactivated_event(
    auth_client: AsyncClient,
    db_conn: AsyncConnection,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Regla 36: un `PUT` que pasa `is_active` a `false` emite `patient.deactivated`."""
    _, headers = await admin_actor(db_conn)
    tag = unique_suffix()
    cedula = unique_cedula()
    created = await auth_client.post(
        PATIENTS_PATH, headers=headers, json=patient_body(tag, cedula=cedula)
    )
    assert created.status_code == 201, created.text

    caplog.set_level(logging.INFO)
    response = await auth_client.put(
        patient_path(created.json()["id"]),
        headers=headers,
        json={
            "name": f"Paciente {tag}",
            "cedula": cedula,
            "patient_type": "EMPLEADO",
            "is_active": False,
        },
    )
    assert response.status_code == 200, response.text

    records = list(caplog.records)
    assert len(_events(records, "patient.deactivated")) == 1
    assert _events(records, "patient.updated") == []
