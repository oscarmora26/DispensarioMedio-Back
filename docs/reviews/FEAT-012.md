# Revisión FEAT-012 — Gestión de médicos

Veredicto: **APPROVED**

Tests: **595 passed** (`uv run pytest -q`, 550 previos + 45 nuevos, ~220 s).
Ruff: `uv run ruff check .` → All checks passed. `uv run ruff format --check .` → 145 files already formatted.
Alembic: `uv run alembic check` → No new upgrade operations detected.
OpenAPI CA-37 verificado por shell: las 5 rutas `/api/v1/doctors*` con tag `doctors`,
`summary`/`description` presentes y códigos por endpoint según spec (200/201/204 + 401/403/404/409/422).

## Cobertura por criterio

- CA-1 → `test_ca1_both_roles_can_read_list_and_detail`
- CA-2 → `test_ca2_medico_cannot_write_and_row_does_not_change`
- CA-3 → `test_ca3_missing_or_invalid_token_is_401`
- CA-4 → `test_ca4_inactive_user_loses_access_even_with_valid_token`
- CA-5 → `test_ca5_medico_list_shows_only_own_profile`
- CA-6 → `test_ca6_medico_without_association_gets_empty_page`
- CA-7 → `test_ca7_medico_detail_is_own_only_and_foreign_is_404`
- CA-8 → `test_ca8_medico_filters_combine_with_ownership`
- CA-9 → `test_ca9_pagination_envelope_and_pages_formula` + `test_ca5_calculate_pages_and_ca11_sort_catalog`
- CA-10 → `test_ca10_page_and_size_limits_and_out_of_range_page`
- CA-11 → `test_ca11_sort_catalog_and_direction` + `test_ca5_calculate_pages_and_ca11_sort_catalog`
- CA-12 → `test_ca12_search_matches_name_and_cedula` + `test_ca12_search_escapes_like_wildcards`
- CA-13 → `test_ca13_is_active_filter_and_combination`
- CA-14 → `test_ca14_item_shape_is_exact` + `test_read_serializes_exactly_six_keys`
- CA-15 → `test_ca15_create_defaults_to_active` + `test_doctor_create_defaults_active_and_strips_text`
- CA-16 → `test_ca16_create_explicit_inactive`
- CA-17 → `test_ca17_cedula_duplicate_collides_including_inactive`
- CA-18 → `test_ca18_cedula_requires_exactly_eleven_ascii_digits`
- CA-19 → `test_ca19_name_is_required_and_bounded` + `test_ca20_field_validation_messages`
- CA-20 → `test_ca20_lab_shift_is_required_and_bounded` + `test_ca20_specialty_is_required_and_bounded` + `test_ca20_field_validation_messages`
- CA-21 → `test_ca21_doctor_without_user_is_creatable`
- CA-22/CA-23 → `test_ca22_detail_returns_active_and_inactive_and_404`
- CA-24/CA-25 → `test_ca24_ca25_full_replacement_keeps_created_at_and_own_cedula`
- CA-25/CA-27 → `test_ca25_ca27_foreign_cedula_conflicts_and_missing_is_404` + `test_update_requires_is_active_and_rejects_partial_body`
- CA-26 → `test_ca26_put_reactivates_and_deactivates`
- CA-28 → `test_ca28_put_with_blank_lab_shift_or_specialty_is_422`
- CA-29/CA-30 → `test_ca29_ca30_delete_is_idempotent_and_keeps_updated_at`
- CA-31 → `test_ca31_delete_missing_is_404_and_invalid_is_422`
- CA-32 → `test_ca32_delete_with_visits_is_409_and_doctor_stays_active` + `test_ca32_retry_on_inactive_doctor_with_visits_still_409`
- CA-33 → `test_ca33_delete_with_user_association_keeps_it`
- CA-34 → `test_ca34_error_envelope_is_uniform`
- CA-35 → `test_ca35_integrity_error_on_commit_becomes_409_and_session_survives`
- CA-36 → `test_ca36_users_association_regressions_hold`
- CA-37 → shell OpenAPI + cobertura por spec
- CA-38 → shell (ruff, format, alembic) + suite completa 595 passed
- Regla 33/34/37 → `test_rule33_blocked_delete_emits_no_deactivation_event`, `test_rule34_deleting_already_inactive_emits_no_second_event`, `test_rule37_events_carry_ids_and_never_leak_doctor_data`, `test_rule37_put_that_deactivates_emits_deactivated_event`

Archivos nuevos de tests: `tests/doctors_support.py`, `tests/test_doctors_unit.py`,
`tests/test_doctors_authz.py`, `tests/test_doctors_list.py`, `tests/test_doctors_profile.py`,
`tests/test_doctors_crud.py`, `tests/test_doctors_integrity.py`, `tests/test_doctors_logs.py`.

## Hallazgos

- [SUGERENCIA] `app/repositories/doctor_repository.py` conserva el patrón de doble consulta en
  `find_doctor_by_cedula` (SELECT id + `get_doctor_by_id`), ya observado en FEAT-007…FEAT-011
  sin impacto funcional.
- [SUGERENCIA] `PUT` sin cambios de valor refresca `updated_at` (mismo comportamiento que
  FEAT-008…FEAT-011); la spec solo exige refresco tras cambio real.
- [SUGERENCIA] `AGENTS.md` y `.opencode/opencode.json` siguen modificados en el working tree
  y quedaron fuera de los commits de la feature (correcto).

## Slices de entrega

La estrategia de la spec se aplicó en dos commits por grupo de endpoints con sus tests:
`7a82565` (grupo lectura: listado, detalle, perfil, authz, unit) y `2066dd0` (grupo
escritura: CRUD, visitas, integridad, logs). El slice acumulado de la rama sigue grande
porque la rama `feat/FEAT-011-pacientes` contiene FEAT-011 y FEAT-012; la división en PRs
al momento del push es decisión humana (habilidad `chained-pr`).

## Riesgo

- Riesgo de datos bajo: solo se añadieron endpoints; no se modificaron tablas, migraciones
  ni datos existentes. La desactivación es lógica, bloqueada con visitas y conserva
  `users.doctor_id`.
- Los datos personales del médico no se registran en logs (regla 37 verificada con
  `caplog` sobre eventos estructurados).
- El perfil del médico oculta la existencia de registros ajenos con `404` (D-6), sin
  diferencia 403/404 explotable.
