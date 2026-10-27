"""Logs de FEAT-013 (CA-34, regla 37): eventos sin datos clinicos ni personales.

El actor de los tres eventos es el medico dueño de la visita (FEAT-019).
"""

from __future__ import annotations

import logging

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncConnection

from tests.db_support import unique_suffix
from tests.visits_support import (
    VISITS_PATH,
    annul_path,
    create_medication_row,
    doctor_row,
    medico_actor,
    past_visited_at,
    patient_row,
    visit_body,
    visit_path,
)


def _events(records: list[logging.LogRecord], name: str) -> list[logging.LogRecord]:
    """Filtra los registros de structlog por nombre de evento."""
    return [
        record
        for record in records
        if isinstance(record.msg, dict) and record.msg.get("event") == name
    ]


async def test_ca34_events_carry_ids_and_never_leak_clinical_texts(
    auth_client: AsyncClient,
    db_conn: AsyncConnection,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """CA-34: `visit.created/updated/annulled` con ids y sin sintomas ni motivo."""
    tag = unique_suffix()
    owner_doctor_id = await doctor_row(db_conn, name=f"Dr. Confidencial {tag}")
    actor_id, headers = await medico_actor(db_conn, doctor_id=owner_doctor_id)
    patient_id = await patient_row(db_conn, name=f"Paciente Confidencial {tag}")
    medication_id = await create_medication_row(db_conn, description=f"Medicina {tag}")

    symptoms = f"Sintoma secreto {tag}"
    updated_symptoms = f"Sintoma corregido {tag}"
    recommendations = f"Recomendacion secreta {tag}"
    reason = f"Motivo confidencial {tag}"

    caplog.set_level(logging.INFO)
    created = await auth_client.post(
        VISITS_PATH,
        headers=headers,
        json=visit_body(
            doctor_id=None,
            patient_id=patient_id,
            symptoms=symptoms,
            recommendations=recommendations,
            medications=[{"medication_id": medication_id, "instructions": f"Indicacion {tag}"}],
        ),
    )
    assert created.status_code == 201, created.text
    visit_id = created.json()["id"]

    updated = await auth_client.put(
        visit_path(visit_id),
        headers=headers,
        json=visit_body(
            doctor_id=None,
            patient_id=patient_id,
            visited_at=past_visited_at(),
            symptoms=updated_symptoms,
            recommendations=recommendations,
        ),
    )
    assert updated.status_code == 200, updated.text

    annulled = await auth_client.post(
        annul_path(visit_id),
        headers=headers,
        json={"reason": reason},
    )
    assert annulled.status_code == 200, annulled.text

    records = list(caplog.records)
    created_events = _events(records, "visit.created")
    updated_events = _events(records, "visit.updated")
    annulled_events = _events(records, "visit.annulled")

    assert len(created_events) == 1
    assert len(updated_events) == 1
    assert len(annulled_events) == 1

    for record in (*created_events, *updated_events, *annulled_events):
        payload = record.msg
        assert isinstance(payload, dict)
        assert payload.get("visit_id") == visit_id
        assert payload.get("actor_id") == actor_id

        text = f"{record.msg} {record.getMessage()}"
        for forbidden in (
            symptoms,
            updated_symptoms,
            recommendations,
            reason,
            tag,
            f"Dr. Confidencial {tag}",
            f"Paciente Confidencial {tag}",
        ):
            assert forbidden not in text
        assert {"event", "visit_id", "actor_id"} <= set(payload)
        for forbidden_key in (
            "symptoms",
            "recommendations",
            "reason",
            "annulment_reason",
            "patient_id",
            "doctor_id",
        ):
            assert forbidden_key not in payload
