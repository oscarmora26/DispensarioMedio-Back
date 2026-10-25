# Revisión FEAT-013 — Registro y gestión de visitas

Veredicto: **APPROVED**

Tests: **650 passed** (`uv run pytest -q`, 595 previos + 55 nuevos; suite completa ~245 s y
`--ignore-glob=tests/test_visits_*.py` con 595 passed confirma baseline intacto).
Ruff: `uv run ruff check app/ tests/` → All checks passed. `uv run ruff format --check app/ tests/` → 158 files already formatted.
Alembic: `uv run alembic check` → No new upgrade operations detected (sin migraciones, SUP-1).
OpenAPI CA-35 verificado por test (`unit::test_ca35_openapi_documents_the_five_operations`): las 5 rutas con tag `visits`, summaries y códigos; el total de rutas pasó de 20 a 23 solo con las nuevas.

## Cobertura por criterio

- CA-1 → `authz::test_ca1_admin_and_medico_read_and_register` + `authz::test_ca1_medico_cannot_edit_or_annul_and_row_does_not_change`
- CA-2 → `authz::test_ca2_all_endpoints_reject_missing_or_invalid_token[sin-token|token-invalido]`
- CA-3 → `authz::test_ca3_inactive_user_is_rejected_on_every_endpoint`
- CA-4 → `create::test_ca4_medico_registers_with_its_own_doctor`
- CA-5 → `create::test_ca5_medico_without_associated_doctor_is_forbidden`
- CA-6 → `create::test_ca6_admin_needs_a_valid_active_doctor` + `test_ca6_medico_with_inactive_doctor_cannot_register`
- CA-7 → `create::test_ca7_visited_at_cannot_be_in_the_future` + `test_ca7_naive_visited_at_is_assumed_in_santo_domingo`
- CA-8 → `create::test_ca8_symptoms_length_is_enforced` + `unit::test_ca8_symptoms_are_trimmed_and_bounded`
- CA-9 → `create::test_ca9_ca14_recommendations_optional_and_bounded` + `unit::test_ca9_ca14_*`
- CA-10 → `create::test_ca10_patient_must_exist_and_be_active`
- CA-11 → `create::test_ca11_medications_must_exist_be_active_and_be_valid` + `unit::test_ca11_*`
- CA-12 → `create::test_ca12_create_without_medications_returns_the_detail_shape`
- CA-13 → `create::test_ca13_create_with_medications_persists_the_details`
- CA-14 → `create::test_ca9_ca14_recommendations_optional_and_bounded`
- CA-15 → `create::test_ca15_response_never_includes_annulment_fields`
- CA-16 → `list::test_ca16_pagination_envelope_and_empty_page` + `unit::test_ca16_calculate_pages_formula`
- CA-17 → `list::test_ca17_filters_and_combinations`
- CA-18 → `list::test_ca18_q_searches_symptoms_and_recommendations`
- CA-19 → `list::test_ca19_date_range_is_inclusive_and_normalized_to_utc`
- CA-20 → `list::test_ca20_sort_catalog_and_default_descending_visited_at` + `unit::test_ca20_*`
- CA-21 → `list::test_ca21_medico_only_sees_own_visits`
- CA-22 → `list::test_ca22_list_item_shape_is_exactly_d8`
- CA-23 → `detail::test_ca23_detail_returns_d7_shape_with_medications` + `test_ca23_annulled_visit_detail_includes_annulment_fields`
- CA-24 → `detail::test_ca24_missing_and_invalid_identifiers` + `test_ca24_medico_gets_404_for_foreign_visit`
- CA-25 → `update::test_ca25_full_put_preserves_created_at_and_refreshes_updated_at`
- CA-26 → `update::test_ca26_put_replaces_the_medication_list`
- CA-27 → `update::test_ca27_put_of_annulled_visit_is_conflict_without_changes`
- CA-28 → `update::test_ca28_partial_body_is_rejected_field_by_field` + `test_ca28_put_applies_the_same_validations_as_post`
- CA-29 → `annul::test_ca29_annul_sets_audit_fields_and_returns_documented_shape`
- CA-30 → `annul::test_ca30_reason_is_required_and_bounded` + `test_ca30_annul_missing_visit_is_404_and_invalid_id_is_422` + `unit::test_ca30_*`
- CA-31 → `annul::test_ca31_annulment_is_not_idempotent`
- CA-32 → `authz::test_ca32_error_envelope_has_no_details_key`
- CA-33 → `create::test_ca33_unique_violation_at_commit_is_translated_and_session_stays_usable` + `unit::test_ca33_*`
- CA-34 / regla 37 → `logs::test_ca34_events_carry_ids_and_never_leak_clinical_texts`
- CA-35 → `unit::test_ca35_openapi_documents_the_five_operations`
- CA-36 → gates T2 (pytest 55/650, ruff check/format, alembic check)

Archivos nuevos de tests: `tests/visits_support.py`, `tests/test_visits_unit.py`,
`tests/test_visits_authz.py`, `tests/test_visits_create.py`, `tests/test_visits_list.py`,
`tests/test_visits_detail.py`, `tests/test_visits_update.py`, `tests/test_visits_annul.py`,
`tests/test_visits_logs.py`.

## Desviaciones revisadas y aprobadas (documentadas en la spec)

1. Offset fijo UTC-4 etiquetado `America/Santo_Domingo` (RD sin DST, `tzdata` no es dependencia) — semántica idéntica a D-3/RNF-12.
2. Obligatoriedad de `doctor_id`/`patient_id`/`visited_at`/`symptoms` validada en el servicio (mensajes exactos en español de las reglas 6–11).
3. `PUT` vacía la colección y hace `flush` antes de insertar los detalles nuevos (bug real detectado por CA-26: insertar antes de borrar viola `uq_visit_medications`).
4. Regla 7 extendida al camino del token: `MEDICO` con médico asociado inactivo ⇒ 422 `doctor_id` — coherente con "el médico debe estar activo".
5. `model_serializer` para ocultar solo claves de anulación nulas (un `exclude_none` habría roto CA-14).
6. CA-33 no alcanzable por API (la validación rechaza duplicados antes del commit); fijada con test unitario del traductor + end-to-end de `quantity_positive` por redondeo de `0.004 → 0.00`.

## Hallazgos

- [MENOR] `update_visit` no distingue "sin cambios" de "cambios": siempre refresca `updated_at` y emite `visit.updated` (mismo criterio que FEAT-008…FEAT-012; la spec no exige lo contrario).
- [SUGERENCIA] `odd/tasks/FEAT-013-visitas.md` es un puntero ODD de una sola página hacia la spec canónica; mantenerlo actualizado solo si el harness lo exige para delegaciones futuras.
- [SUGERENCIA] `AGENTS.md` y `.opencode/opencode.json` siguen fuera de los commits de la feature (correcto).

## Slices de entrega

Estrategia aplicada en dos commits por grupo de endpoints con sus tests: `adf0e55` (código + spec + grupo lectura: unit/authz/list/detail) y `05343d8` (grupo escritura: create/update/annul/logs). El slice acumulado de la rama sigue creciendo (FEAT-011+012+013 en la misma rama); la división en PRs al push es decisión humana (`chained-pr`).

## Riesgo

- Riesgo de datos bajo: solo se añadieron endpoints; sin migraciones ni cambios de datos. La anulación es auditada e inmutable (D-12); el borrado de detalles por `PUT` es reemplazo explícito del documento (D-10).
- Sin fuga de datos clínicos en logs (regla 37 verificada) ni de existencia entre médicos (404 uniforme).
- Rendimiento del listado: forma resumida D-8 sin `medications` y filtros por índices existentes (`ix_visits_doctor_visited`, `ix_visits_patient_visited`, `ix_visits_visited`, `ix_visits_status_visited`).
