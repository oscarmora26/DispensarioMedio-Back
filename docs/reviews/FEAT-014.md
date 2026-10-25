# Revisión FEAT-014 — Reporte de visitas (JSON/CSV) y exportación

Veredicto: **APPROVED**

Tests: **680 passed** (`uv run pytest -q`, 650 previos + 30 nuevos, ~159 s).
Ruff: `uv run ruff check app/ tests/` → All checks passed. `uv run ruff format --check app/ tests/` → 167 files already formatted.
Alembic: `uv run alembic check` → No new upgrade operations detected (sin migraciones, SUP-2).
OpenAPI CA-17 verificado por test (`unit::test_ca17_openapi_documents_the_report_routes`): las 2 rutas con tag `reports`, summaries y códigos.

## Cobertura por criterio

- CA-1 → `authz::test_ca1_both_roles_get_the_report_and_export`
- CA-2 → `authz::test_ca2_missing_or_invalid_token_is_401_and_inactive_user_403`
- CA-3 → `unit::test_ca3_missing_dates_are_rejected_with_field` + `json::test_ca3_missing_dates_are_rejected`
- CA-4 → `unit::test_ca4_range_order_and_inclusive_bounds` + `json::test_ca4_inverted_range_is_422_and_bounds_are_inclusive`
- CA-5 → `unit::test_ca5_status_and_query_parameters` + `json::test_ca5_status_catalog_and_pagination_limits`
- CA-6 → `authz::test_ca6_medico_only_gets_own_report` (+ sin asociación ⇒ vacío)
- CA-7 → `json::test_ca7_filters_combine_in_and`
- CA-8 → `json::test_ca8_unknown_ids_yield_empty_report_without_404`
- CA-9 → `json::test_ca9_filters_echo_the_received_query`
- CA-10 → `json::test_ca10_summary_totals_and_doctor_ordering`
- CA-11 → `json::test_ca11_items_shape_and_pagination_envelope`
- CA-12 → `unit::test_ca12_format_catalog` + `json::test_ca12_pdf_is_rejected_and_json_is_default`
- CA-13 → `unit::test_ca13_csv_header_bom_and_filename` + `csv::test_ca13_export_returns_csv_with_disposition`
- CA-14 → `unit::test_ca14_csv_rows_and_quoting` + `csv::test_ca14_rows_are_correct_and_not_paginated`
- CA-15 → `csv::test_ca15_format_csv_on_report_endpoint_matches_export`
- CA-16 → `logs::test_ca16_report_generated_log_has_no_clinical_texts`
- CA-17 → `unit::test_ca17_openapi_documents_the_report_routes`
- CA-18 → gates T2 (pytest 30/680, ruff check/format, alembic check)

Archivos nuevos de tests: `tests/reports_support.py`, `tests/test_reports_unit.py`,
`tests/test_reports_json.py`, `tests/test_reports_csv.py`, `tests/test_reports_authz.py`,
`tests/test_reports_logs.py`.

## Desviaciones revisadas y aprobadas (documentadas en la spec)

1. Columna `fecha` del CSV en ISO 8601 en `America/Santo_Domingo` (RNF-12); la regla solo exigía ISO 8601.
2. `/reports/visits/export` acepta `page`/`size`/`sort` (contrato "Query: igual") pero no los aplica (D-7: CSV completo); queda explícito en OpenAPI.
3. `date_from`/`date_to` como `str` parseados en el servicio (eco literal de D-4); fechas ausentes producen mensajes españoles con el `field` correcto.
4. `summarize_visits` añade `doctor_id ASC` como tercer desempate de `by_doctor` (solo homónimos).
5. `status` validado dos veces (patrón en la ruta para OpenAPI, catálogo en el servicio con mensaje español).

## Hallazgos

- [MENOR] `export_report_csv` carga el reporte completo con `page=1, size=total`; con 10.000 visitas (~RNF-04) la carga en memoria es de megabytes y el requisito <5 s se apoya en los índices de FEAT-003. Aceptable para v1; si se midiera carga real, paginar el CSV en bloques sería la mejora.
- [SUGERENCIA] `odd/tasks/FEAT-014-reportes.md` es un puntero ODD de una página; mantener solo si el harness lo exige.
- [SUGERENCIA] `AGENTS.md` y `.opencode/opencode.json` siguen fuera de los commits (correcto).
- [PENDIENTE HUMANO] Pregunta abierta §14.2 (PDF/Excel): v1 exporta CSV y rechaza `pdf` con `422` explícito; si el humano aprueba PDF, reabrir esta feature o crear una nueva.

## Slices de entrega

Dos commits por grupo: `f8b64e5` (código + spec + unit/authz) y `9f13c49` (json/csv/logs). El slice acumulado de la rama `feat/FEAT-011-pacientes` (~11.800 líneas) contiene FEAT-011…014; la división en PRs al push es decisión humana (`chained-pr`).

## Riesgo

- Riesgo de datos bajo: endpoints de solo lectura; sin migraciones ni cambios de datos.
- Sin fuga de datos clínicos en logs (CA-16) ni entre médicos (propiedad aplicada en ambos endpoints).
- PDF diferido sin dependencias nuevas (D-1).
