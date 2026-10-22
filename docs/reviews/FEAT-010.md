# Revisión FEAT-010 — Gestión de medicamentos

Veredicto: **APPROVED**

Tests: **507 passed** (`uv run pytest -q`, 466 previos + 41 nuevos, ~363 s).
Ruff: `uv run ruff check .` → All checks passed. `uv run ruff format --check .` → 157 files already formatted.
Alembic: `uv run alembic check` → No new upgrade operations detected. `git status --porcelain alembic/` → vacío.
OpenAPI CA-28 verificado por shell: paths, tag `medications`, `security`/`summary`/`description` en los 5 endpoints y códigos por endpoint según spec.

## Cobertura por criterio

- CA-1 → `test_ca01_medico_reads_but_never_writes`
- CA-2 → `test_ca02_authentication_matrix`
- CA-3 → `test_ca03_detail_of_missing_id_is_404_with_exact_envelope`
- CA-4 → `test_ca04_defaults_and_pagination_formula` + `test_u6_pages_*`
- CA-5 → `test_ca05_page_and_size_limits_and_out_of_range_page`
- CA-6 → `test_ca06_sort_catalog_direction_tiebreak_and_invalids` + `test_u4_sort_catalog_*`
- CA-7 → `test_ca07_search_covers_description_and_dose_escapes_wildcards` + `test_u5_like_escape_*`
- CA-8 → `test_ca08_filters_by_state_and_fks_combined_with_search`
- CA-9 → `test_ca09_empty_search_results_have_zero_pages`
- CA-10 → `test_ca10_items_never_leak_timestamps_or_nested` + `test_u3_read_serializes_exactly_seven_keys`
- CA-11 → `test_ca11_create_defaults_to_active_and_honors_explicit_inactive`
- CA-12 → `test_ca12_composite_duplicates_collide_case_insensitively_including_spaces`
- CA-13 → `test_ca13_create_validations_and_strip_normalization` + `test_u1_create_requires_*`
- CA-14 → `test_ca14_missing_catalogs_return_their_own_404_in_order`
- CA-15 → `test_ca15_inactive_catalogs_return_422_with_field_and_404_wins`
- CA-16 → `test_ca16_create_response_hides_timestamps_and_logs_creation`
- CA-17 → `test_ca17_detail_matches_list_and_medico_sees_the_same`
- CA-18 → `test_ca18_put_replaces_and_preserves_created_at`
- CA-19 → `test_ca19_put_reactivates_deactivates_and_changes_fks`
- CA-20 → `test_ca20_put_distinguishes_foreign_from_own_identity`
- CA-21 → `test_ca21_put_validates_body_path_precedence_and_role` + `test_u2_update_requires_*`
- CA-22 → `test_ca22_delete_is_a_logical_deactivation_without_body`
- CA-23 → `test_ca23_delete_is_idempotent_and_guards_role_and_missing`
- CA-24 → `test_ca24_delete_with_visit_medications_keeps_the_reference`
- CA-25 → `test_ca25_integrity_error_on_commit_becomes_409_and_session_survives`
- CA-26 → `test_ca26_every_error_uses_the_exact_envelope`
- CA-27 → `test_ca27_all_events_carry_ids_and_never_leak_secrets` + `test_ca27_put_deactivation_emits_deactivated_instead_of_updated`
- CA-28 → `test_ca28_openapi_declares_medications_contract` + shell CA-28
- CA-29 → shell (`alembic check`, `git status`, `ruff check`, `ruff format --check`)
- CA-30 → suite completa 507 passed

Archivos nuevos de tests: `tests/medications_support.py`, `tests/test_medications_unit.py`,
`tests/test_medications_authz.py`, `tests/test_medications_list.py`,
`tests/test_medications_crud.py`, `tests/test_medications_integrity.py`.

## Hallazgos

- [MENOR] `app/repositories/medication_repository.py:34-58` — `find_medication_by_identity`
  hace dos consultas (SELECT id + `get_medication_by_id`). Basta con devolver el id o
  una existencia; patrón ya observado en FEAT-007/008/009 sin impacto funcional.
- [MENOR] `app/services/medication_service.py:291` — `update_medication` llama `_touch()`
  siempre, incluso si el PUT no cambia ningún campo (refresca `updated_at` sin cambio real).
  Mismo patrón de FEAT-008/009; la spec solo exige refresco tras cambio (CA-18 lo verifica).
- [SUGERENCIA] `AGENTS.md:85` y `.opencode/opencode.json` aparecen modificados en el
  working tree y son ajenos al diff de la feature; conviene mantenerlos fuera del commit
  `feat(FEAT-010)` (igual que se hizo en FEAT-006/007/008/009).
