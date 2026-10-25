# Revisión FEAT-011 — Gestión de pacientes

Veredicto: **APPROVED**

Tests: **550 passed** (`uv run pytest -q`, 507 previos + 43 nuevos, ~232 s).
Ruff: `uv run ruff check .` → All checks passed. `uv run ruff format --check .` → limpio.
Alembic: `uv run alembic check` → No new upgrade operations detected.
OpenAPI CA-34 verificado por shell: las 5 rutas `/api/v1/patients*` con tag `patients`,
`summary`/`description` presentes y códigos por endpoint según spec (200/201/204 + 401/403/404/409/422).

## Cobertura por criterio

- CA-1 → `test_ca1_both_roles_can_read_list_and_detail`
- CA-2 → `test_ca2_medico_cannot_write_and_row_does_not_change`
- CA-3 → `test_ca3_missing_or_invalid_token_is_401`
- CA-4 → `test_ca4_inactive_user_loses_access_even_with_valid_token`
- CA-5 → `test_ca5_pagination_envelope_and_pages_formula` + `test_ca5_calculate_pages_and_ca7_sort_catalog`
- CA-6 → `test_ca6_page_and_size_limits_and_out_of_range_page`
- CA-7 → `test_ca7_sort_catalog_and_direction` + `test_ca5_calculate_pages_and_ca7_sort_catalog`
- CA-8 → `test_ca8_search_matches_name_cedula_and_carnet`
- CA-9 → `test_ca9_search_escapes_like_wildcards`
- CA-10 → `test_ca10_is_active_filter_and_combination`
- CA-11 → `test_ca11_read_shape_is_exact` + `test_ca11_item_shape_is_exact_and_medico_can_read`
- CA-12 → `test_ca12_create_student_with_carnet_returns_201_and_defaults_active` + `test_patient_create_defaults_active_and_strips_text`
- CA-13 → `test_ca13_create_explicit_inactive_is_allowed`
- CA-14 → `test_ca14_cedula_and_carnet_duplicates_collide_409`
- CA-15 → `test_ca15_two_patients_without_carnet_are_allowed`
- CA-16 → `test_ca16_cedula_requires_exactly_eleven_ascii_digits` + `test_rf18_cedula_check_is_enforced_by_the_database`
- CA-17 → `test_ca17_student_requires_carnet`
- CA-18 → `test_ca18_patient_type_is_a_closed_uppercase_catalog`
- CA-19 → `test_ca19_name_is_required_and_bounded`
- CA-20 → `test_ca20_carnet_optional_for_non_students` + `test_ca20_carnet_is_optional_for_non_students_and_blank_means_null`
- CA-21/CA-22 → `test_ca21_ca22_detail_returns_active_and_inactive_and_404`
- CA-23/CA-24 → `test_ca23_ca24_full_replacement_keeps_created_at_and_clears_carnet`
- CA-25 → `test_ca25_put_with_own_identity_does_not_conflict`
- CA-26 → `test_ca26_put_with_foreign_cedula_or_carnet_conflicts`
- CA-27 → `test_ca27_put_reactivates_and_put_partial_is_rejected`
- CA-28 → `test_ca28_update_requires_is_active_and_treats_absent_carnet_as_null`
- CA-29/CA-30 → `test_ca29_ca30_delete_is_idempotent_and_keeps_updated_at`
- CA-31 → `test_ca31_delete_missing_patient_is_404`
- CA-32 → `test_ca32_error_envelope_is_uniform`
- CA-33 → `test_ca33_integrity_error_on_commit_becomes_409_and_session_survives` + `test_ca33_check_violation_on_commit_becomes_422`
- CA-34 → `test_ca34_openapi_documents_patient_endpoints` + verificación por shell
- CA-35 → shell (ruff, alembic check) + suite completa 550 passed
- Regla 33/36 → `test_rule33_deactivate_already_inactive_does_not_touch_row`, `test_rule36_deleting_already_inactive_emits_no_second_event`, `test_rule36_events_carry_ids_and_never_leak_patient_data`, `test_rule36_put_that_deactivates_emits_deactivated_event`

Archivos nuevos de tests: `tests/patients_support.py`, `tests/test_patients_unit.py`,
`tests/test_patients_authz.py`, `tests/test_patients_list.py`, `tests/test_patients_crud.py`,
`tests/test_patients_integrity.py`, `tests/test_patients_logs.py`.

## Hallazgos

- [SUGERENCIA] Commit `a97fc94` concentra 2.591 líneas (código 716, tests 1.492, spec 357).
  Supera la guía de ~400 líneas por slice; está justificado en la spec (código y tests del
  mismo comportamiento no se separan) y la estrategia para FEAT-012…FEAT-014 ya quedó
  ajustada a slices más chicos (un commit por grupo de endpoints con sus tests).
- [SUGERENCIA] `AGENTS.md` y `.opencode/opencode.json` siguen modificados en el working
  tree y son ajenos a la feature; conviene mantenerlos fuera de los commits de features
  (mismo criterio que FEAT-006…FEAT-010).

## Riesgo

- Riesgo de datos bajo: solo se añadieron endpoints; no se modificaron tablas, migraciones
  ni datos existentes. La desactivación es lógica y conserva `visits.patient_id`.
- Los datos personales del paciente no se registran en logs (regla 36 verificada con
  `caplog` sobre eventos estructurados).
