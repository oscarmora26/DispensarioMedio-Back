"""Reporte JSON de visitas de FEAT-014 (CA-3 a CA-5, CA-7 a CA-12)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from httpx import AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

from app.schemas.report import (
    DATE_FROM_REQUIRED_MESSAGE,
    DATE_TO_REQUIRED_MESSAGE,
    PDF_FORMAT_MESSAGE,
)
from app.schemas.visit import DATE_RANGE_MESSAGE
from tests.db_support import unique_suffix
from tests.reports_support import (
    DOCTOR_TOTAL_KEYS,
    FILTER_KEYS,
    REPORT_EXPORT_PATH,
    REPORT_KEYS,
    REPORTS_PATH,
    SUMMARY_KEYS,
    WIDE_FROM,
    WIDE_TO,
    admin_actor,
    assert_error_envelope,
    assert_field_error,
    doctor_row,
    parse_csv,
    patient_row,
    register_visit,
    report_params,
)
from tests.visits_support import (
    DOCTOR_REF_KEYS,
    LIST_ITEM_KEYS,
    PATIENT_REF_KEYS,
    annul_visit_row,
    fetch_visit_row,
)

FIRST_VISIT_AT = datetime(2026, 1, 15, 14, 0, tzinfo=UTC)
SECOND_VISIT_AT = datetime(2026, 1, 16, 14, 0, tzinfo=UTC)
THIRD_VISIT_AT = datetime(2026, 1, 17, 14, 0, tzinfo=UTC)


async def _row(db_conn: AsyncConnection, query: str, **params: object) -> None:
    """Ejecuta una sentencia suelta dentro de la transaccion del test."""
    await db_conn.execute(text(query), params)


# --- CA-3 / CA-4: rango obligatorio e inclusivo --------------------------------


async def test_ca3_missing_dates_are_rejected_with_their_field_on_both_endpoints(
    auth_client: AsyncClient, db_conn: AsyncConnection
) -> None:
    """CA-3: sin `date_from` o sin `date_to` ⇒ 422 con el campo correspondiente."""
    _, headers = await admin_actor(db_conn)

    for path in (REPORTS_PATH, REPORT_EXPORT_PATH):
        missing_from = await auth_client.get(path, headers=headers, params={"date_to": WIDE_TO})
        assert_field_error(
            missing_from, 422, "validation_error", "date_from", DATE_FROM_REQUIRED_MESSAGE
        )

        missing_to = await auth_client.get(path, headers=headers, params={"date_from": WIDE_FROM})
        assert_field_error(missing_to, 422, "validation_error", "date_to", DATE_TO_REQUIRED_MESSAGE)


async def test_ca4_range_is_inclusive_naive_is_santo_domingo_and_inverted_is_rejected(
    auth_client: AsyncClient, db_conn: AsyncConnection
) -> None:
    """CA-4: rango inclusivo, bordes exactos, naive en RD y `date_from > date_to`."""
    admin_id, headers = await admin_actor(db_conn)
    doctor_id = await doctor_row(db_conn)
    patient_id = await patient_row(db_conn)

    first = await register_visit(
        db_conn,
        doctor_id=doctor_id,
        patient_id=patient_id,
        created_by=admin_id,
        visited_at=FIRST_VISIT_AT,
    )
    middle = await register_visit(
        db_conn,
        doctor_id=doctor_id,
        patient_id=patient_id,
        created_by=admin_id,
        visited_at=SECOND_VISIT_AT,
    )
    await register_visit(
        db_conn,
        doctor_id=doctor_id,
        patient_id=patient_id,
        created_by=admin_id,
        visited_at=THIRD_VISIT_AT,
    )
    before = await fetch_visit_row(db_conn, first)

    bounded = await auth_client.get(
        REPORTS_PATH,
        headers=headers,
        params=report_params(
            date_from=FIRST_VISIT_AT.isoformat(),
            date_to=SECOND_VISIT_AT.isoformat(),
            patient_id=patient_id,
            sort="visited_at",
        ),
    )
    assert bounded.status_code == 200, bounded.text
    assert [item["id"] for item in bounded.json()["items"]] == [first, middle]
    assert bounded.json()["summary"]["total_visits"] == 2

    exact_border = await auth_client.get(
        REPORTS_PATH,
        headers=headers,
        params=report_params(
            date_from=FIRST_VISIT_AT.isoformat(),
            date_to=FIRST_VISIT_AT.isoformat(),
            patient_id=patient_id,
        ),
    )
    assert [item["id"] for item in exact_border.json()["items"]] == [first]

    naive_santo_domingo = (SECOND_VISIT_AT - timedelta(hours=4)).replace(tzinfo=None)
    naive_report = await auth_client.get(
        REPORTS_PATH,
        headers=headers,
        params=report_params(
            date_from=naive_santo_domingo.isoformat(),
            date_to=naive_santo_domingo.isoformat(),
            patient_id=patient_id,
        ),
    )
    assert [item["id"] for item in naive_report.json()["items"]] == [middle]

    for path in (REPORTS_PATH, REPORT_EXPORT_PATH):
        inverted = await auth_client.get(
            path,
            headers=headers,
            params=report_params(
                date_from=THIRD_VISIT_AT.isoformat(),
                date_to=FIRST_VISIT_AT.isoformat(),
            ),
        )
        assert_field_error(inverted, 422, "validation_error", "date_from", DATE_RANGE_MESSAGE)

    assert await fetch_visit_row(db_conn, first) == before


# --- CA-5: parametros invalidos ------------------------------------------------


async def test_ca5_invalid_status_page_size_and_sort_are_rejected_with_their_field(
    auth_client: AsyncClient, db_conn: AsyncConnection
) -> None:
    """CA-5: `status`, `page`, `size` y `sort` invalidos ⇒ 422 con su `field`."""
    _, headers = await admin_actor(db_conn)
    cases = {
        "status": report_params(status="BORRADA"),
        "page": report_params(page=0),
        "size": report_params(size=101),
        "sort": report_params(sort="doctor"),
    }

    for path in (REPORTS_PATH, REPORT_EXPORT_PATH):
        for field, params in cases.items():
            response = await auth_client.get(path, headers=headers, params=params)
            assert_error_envelope(response, 422, "validation_error")
            assert response.json()["errors"][0]["field"] == field, (path, field, response.text)


# --- CA-7 / CA-8: filtros y combinacion en AND ---------------------------------


async def test_ca7_doctor_patient_and_status_filters_combine_in_and(
    auth_client: AsyncClient, db_conn: AsyncConnection
) -> None:
    """CA-7: `doctor_id`, `patient_id` y `status` filtran y combinan en `AND`."""
    admin_id, headers = await admin_actor(db_conn)
    first_doctor = await doctor_row(db_conn)
    second_doctor = await doctor_row(db_conn)
    first_patient = await patient_row(db_conn)
    second_patient = await patient_row(db_conn)

    first = await register_visit(
        db_conn,
        doctor_id=first_doctor,
        patient_id=first_patient,
        created_by=admin_id,
    )
    second = await register_visit(
        db_conn,
        doctor_id=first_doctor,
        patient_id=second_patient,
        created_by=admin_id,
    )
    third = await register_visit(
        db_conn,
        doctor_id=second_doctor,
        patient_id=second_patient,
        created_by=admin_id,
    )
    await annul_visit_row(db_conn, third, annulled_by=admin_id)

    by_doctor = await auth_client.get(
        REPORTS_PATH, headers=headers, params=report_params(doctor_id=first_doctor)
    )
    assert {item["id"] for item in by_doctor.json()["items"]} == {first, second}
    assert by_doctor.json()["summary"]["total_visits"] == 2

    by_patient = await auth_client.get(
        REPORTS_PATH, headers=headers, params=report_params(patient_id=second_patient)
    )
    assert {item["id"] for item in by_patient.json()["items"]} == {second, third}

    by_status = await auth_client.get(
        REPORTS_PATH, headers=headers, params=report_params(status="ANULADA")
    )
    assert [item["id"] for item in by_status.json()["items"]] == [third]
    assert by_status.json()["items"][0]["status"] == "ANULADA"

    combined = await auth_client.get(
        REPORTS_PATH,
        headers=headers,
        params=report_params(
            doctor_id=first_doctor,
            patient_id=second_patient,
            status="REGISTRADA",
        ),
    )
    assert [item["id"] for item in combined.json()["items"]] == [second]
    assert combined.json()["summary"]["total_visits"] == 1

    contradictory = await auth_client.get(
        REPORTS_PATH,
        headers=headers,
        params=report_params(doctor_id=second_doctor, status="REGISTRADA"),
    )
    assert contradictory.json()["items"] == []
    assert contradictory.json()["total"] == 0

    exported = await auth_client.get(
        REPORT_EXPORT_PATH,
        headers=headers,
        params=report_params(doctor_id=first_doctor, patient_id=second_patient),
    )
    assert [row["id"] for row in parse_csv(exported)] == [str(second)]


async def test_ca8_nonexistent_doctor_or_patient_yields_an_empty_report(
    auth_client: AsyncClient, db_conn: AsyncConnection
) -> None:
    """CA-8: ids inexistentes son filtros sin coincidencia: reporte vacio sin 404."""
    admin_id, headers = await admin_actor(db_conn)
    doctor_id = await doctor_row(db_conn)
    patient_id = await patient_row(db_conn)
    await register_visit(
        db_conn,
        doctor_id=doctor_id,
        patient_id=patient_id,
        created_by=admin_id,
    )
    missing_doctor = doctor_id + 100000
    missing_patient = patient_id + 100000

    for params in (
        report_params(doctor_id=missing_doctor),
        report_params(patient_id=missing_patient),
        report_params(doctor_id=missing_doctor, patient_id=missing_patient),
    ):
        report = await auth_client.get(REPORTS_PATH, headers=headers, params=params)
        assert report.status_code == 200, report.text
        body = report.json()
        assert body["summary"] == {"total_visits": 0, "by_doctor": []}
        assert body["items"] == []
        assert body["total"] == 0
        assert body["pages"] == 0

        export = await auth_client.get(REPORT_EXPORT_PATH, headers=headers, params=params)
        assert export.status_code == 200, export.text
        assert parse_csv(export) == []


# --- CA-9 / CA-10 / CA-11: forma del JSON --------------------------------------


async def test_ca9_filters_echo_the_literal_query(
    auth_client: AsyncClient, db_conn: AsyncConnection
) -> None:
    """CA-9: `filters` eco literal de la query, incluidos los `null`."""
    _, headers = await admin_actor(db_conn)
    doctor_id = await doctor_row(db_conn)
    patient_id = await patient_row(db_conn)
    date_from = "2026-01-15T00:00:00-04:00"
    date_to = "2026-01-17T23:59:59"

    response = await auth_client.get(
        REPORTS_PATH,
        headers=headers,
        params={
            "date_from": date_from,
            "date_to": date_to,
            "doctor_id": doctor_id,
            "patient_id": patient_id,
            "status": "REGISTRADA",
        },
    )
    assert response.status_code == 200, response.text
    filters = response.json()["filters"]
    assert set(filters) == FILTER_KEYS
    assert filters == {
        "date_from": date_from,
        "date_to": date_to,
        "doctor_id": doctor_id,
        "patient_id": patient_id,
        "status": "REGISTRADA",
    }

    without_filters = await auth_client.get(
        REPORTS_PATH,
        headers=headers,
        params=report_params(date_from=WIDE_FROM, date_to=WIDE_TO, patient_id=patient_id),
    )
    assert without_filters.json()["filters"] == {
        "date_from": WIDE_FROM,
        "date_to": WIDE_TO,
        "doctor_id": None,
        "patient_id": patient_id,
        "status": None,
    }

    without_ids = await auth_client.get(
        REPORTS_PATH, headers=headers, params=report_params(date_from=WIDE_FROM, date_to=WIDE_TO)
    )
    assert without_ids.json()["filters"] == {
        "date_from": WIDE_FROM,
        "date_to": WIDE_TO,
        "doctor_id": None,
        "patient_id": None,
        "status": None,
    }


async def test_ca10_summary_counts_group_by_current_name_and_sort(
    auth_client: AsyncClient, db_conn: AsyncConnection
) -> None:
    """CA-10: `total_visits` exacto, `by_doctor` con nombre actual y orden total/name."""
    admin_id, headers = await admin_actor(db_conn)
    suffix = unique_suffix()
    patient_id = await patient_row(db_conn)
    alpha = await doctor_row(db_conn, name=f"Dr. Alfa {suffix}")
    beta = await doctor_row(db_conn, name=f"Dr. Beta {suffix}")
    zeta = await doctor_row(db_conn, name=f"Dr. Zeta {suffix}")

    alpha_visit = await register_visit(
        db_conn, doctor_id=alpha, patient_id=patient_id, created_by=admin_id
    )
    beta_visit = await register_visit(
        db_conn, doctor_id=beta, patient_id=patient_id, created_by=admin_id
    )
    zeta_visits = [
        await register_visit(db_conn, doctor_id=zeta, patient_id=patient_id, created_by=admin_id)
        for _ in range(2)
    ]

    report = await auth_client.get(
        REPORTS_PATH, headers=headers, params=report_params(patient_id=patient_id)
    )
    assert report.status_code == 200, report.text
    body = report.json()
    assert set(body) == REPORT_KEYS
    assert set(body["summary"]) == SUMMARY_KEYS
    assert body["total"] == 4
    assert body["summary"]["total_visits"] == body["total"] == 4
    entries = body["summary"]["by_doctor"]
    for entry in entries:
        assert set(entry) == DOCTOR_TOTAL_KEYS
    assert [(entry["doctor_id"], entry["total"]) for entry in entries] == [
        (zeta, 2),
        (alpha, 1),
        (beta, 1),
    ]
    assert {entry["doctor_name"] for entry in entries} == {
        f"Dr. Alfa {suffix}",
        f"Dr. Beta {suffix}",
        f"Dr. Zeta {suffix}",
    }
    assert {item["id"] for item in body["items"]} == {alpha_visit, beta_visit, *zeta_visits}

    renamed = f"Dr. Alfa Actualizada {suffix}"
    await _row(
        db_conn,
        "UPDATE doctors SET name = :name WHERE id = :doctor_id",
        name=renamed,
        doctor_id=alpha,
    )
    after_rename = await auth_client.get(
        REPORTS_PATH, headers=headers, params=report_params(patient_id=patient_id)
    )
    renamed_totals = [
        (entry["doctor_id"], entry["total"])
        for entry in after_rename.json()["summary"]["by_doctor"]
    ]
    names = [entry["doctor_name"] for entry in after_rename.json()["summary"]["by_doctor"]]
    assert renamed in names, names
    assert renamed_totals == [(zeta, 2), (alpha, 1), (beta, 1)]


async def test_ca11_items_shape_default_order_and_empty_pages(
    auth_client: AsyncClient, db_conn: AsyncConnection
) -> None:
    """CA-11: `items` con la forma D-8, `-visited_at` por defecto y `pages == 0` vacio."""
    admin_id, headers = await admin_actor(db_conn)
    doctor_id = await doctor_row(db_conn)
    patient_id = await patient_row(db_conn)
    empty_patient = await patient_row(db_conn)

    first = await register_visit(
        db_conn,
        doctor_id=doctor_id,
        patient_id=patient_id,
        created_by=admin_id,
        visited_at=FIRST_VISIT_AT,
    )
    second = await register_visit(
        db_conn,
        doctor_id=doctor_id,
        patient_id=patient_id,
        created_by=admin_id,
        visited_at=SECOND_VISIT_AT,
    )
    third = await register_visit(
        db_conn,
        doctor_id=doctor_id,
        patient_id=patient_id,
        created_by=admin_id,
        visited_at=THIRD_VISIT_AT,
    )

    report = await auth_client.get(
        REPORTS_PATH, headers=headers, params=report_params(patient_id=patient_id)
    )
    assert report.status_code == 200, report.text
    body = report.json()
    assert [item["id"] for item in body["items"]] == [third, second, first]
    assert body["total"] == 3
    assert body["page"] == 1
    assert body["size"] == 20
    assert body["pages"] == 1

    item = body["items"][0]
    assert set(item) == LIST_ITEM_KEYS
    assert set(item["doctor"]) == DOCTOR_REF_KEYS
    assert set(item["patient"]) == PATIENT_REF_KEYS

    first_page = await auth_client.get(
        REPORTS_PATH,
        headers=headers,
        params=report_params(patient_id=patient_id, size=2, page=1),
    )
    assert len(first_page.json()["items"]) == 2
    assert first_page.json()["pages"] == 2
    assert [item["id"] for item in first_page.json()["items"]] == [third, second]

    empty = await auth_client.get(
        REPORTS_PATH, headers=headers, params=report_params(patient_id=empty_patient)
    )
    assert empty.json()["items"] == []
    assert empty.json()["total"] == 0
    assert empty.json()["pages"] == 0
    assert empty.json()["summary"] == {"total_visits": 0, "by_doctor": []}


# --- CA-12: formato ------------------------------------------------------------


async def test_ca12_json_is_the_default_and_pdf_is_rejected(
    auth_client: AsyncClient, db_conn: AsyncConnection
) -> None:
    """CA-12: ausente o `json` ⇒ JSON; `pdf` ⇒ 422 con el mensaje explicito."""
    admin_id, headers = await admin_actor(db_conn)
    doctor_id = await doctor_row(db_conn)
    patient_id = await patient_row(db_conn)
    await register_visit(
        db_conn,
        doctor_id=doctor_id,
        patient_id=patient_id,
        created_by=admin_id,
    )
    params = report_params(patient_id=patient_id)

    for query in (params, {**params, "format": "json"}):
        report = await auth_client.get(REPORTS_PATH, headers=headers, params=query)
        assert report.status_code == 200, report.text
        assert report.headers["content-type"].startswith("application/json")
        assert report.json()["total"] == 1

    for path in (REPORTS_PATH, REPORT_EXPORT_PATH):
        rejected = await auth_client.get(path, headers=headers, params={**params, "format": "pdf"})
        assert_field_error(rejected, 422, "validation_error", "format", PDF_FORMAT_MESSAGE)
