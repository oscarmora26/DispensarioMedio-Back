# Revisión FEAT-007 — Gestión de tipos de fármacos

**Veredicto: APPROVED**

| Comprobación | Resultado |
|---|---|
| `uv run pytest -q` | **387 passed** (348 previos + 39 nuevos), ~111 s |
| `uv run ruff check .` | `All checks passed!` |
| `uv run ruff format --check .` | `121 files already formatted` |
| `uv run alembic check` | `No new upgrade operations detected.` |
| `git status --porcelain alembic/` | vacío (sin migraciones) |
| OpenAPI CA-26 (comando `[shell]` de la spec) | salida exacta esperada |

No se modificó código de producción: solo `tests/**` (6 archivos nuevos) y este informe.
Producción revisada (sin tocar): `app/schemas/drug_type.py`, `app/repositories/drug_type_repository.py`,
`app/services/drug_type_service.py`, `app/api/v1/routes/drug_types.py`, `app/api/v1/router.py` (registro).

## Hallazgos

- [MENOR] `app/repositories/drug_type_repository.py:30-47` — `find_drug_type_by_description`
  primero selecciona el `id` y luego recarga la fila completa con `get_drug_type_by_id`
  (2 consultas por cada chequeo de unicidad). Funciona y los tests lo cubren; se podría
  resolver en una sola consulta.
- [SUGERENCIA] `app/services/drug_type_service.py:105-118` — `_ensure_unique` carga la entidad
  completa y la descarta; un `SELECT 1` / `EXISTS` bastaría en el camino de escritura. Sin
  impacto funcional.
- [SUGERENCIA] `app/services/drug_type_service.py:185-198` — `update_drug_type` refresca
  `updated_at` y emite `drug_type.updated` aunque el body no cambie nada. Conforme a la spec
  (toda actualización no-desactivadora emite `updated`); solo constancia.

Sin hallazgos BLOQUEANTES ni MAYORES. Contrato de API, permisos, validaciones, envelope,
códigos, precedencia de errores (regla 23), idempotencia del DELETE, `clock_timestamp()`,
logs y OpenAPI verificados test por test.

## Criterios de aceptación

- [x] CA-1 → `test_ca01_medico_reads_but_never_writes`
- [x] CA-2 → `test_ca02_authentication_matrix`
- [x] CA-3 → `test_ca03_detail_of_missing_id_is_404_with_exact_envelope`
- [x] CA-4 → `test_ca04_defaults_and_pagination_formula` (+ `test_u6_...` para `pages`)
- [x] CA-5 → `test_ca05_page_and_size_limits_and_out_of_range_page`
- [x] CA-6 → `test_ca06_sort_catalog_direction_tiebreak_and_invalids` (+ `test_u4_...` catálogo)
- [x] CA-7 → `test_ca07_search_is_case_insensitive_partial_and_escapes_wildcards` (+ `test_u5_...` escape)
- [x] CA-8 → `test_ca08_is_active_filter_alone_and_combined_with_search`
- [x] CA-9 → `test_ca09_empty_search_results_have_zero_pages` (+ `test_u6_...` con `total=0`)
- [x] CA-10 → `test_ca10_items_never_leak_timestamps` (+ `test_u3_...`, y forma exacta en CA-11/14/15/16)
- [x] CA-11 → `test_ca11_create_defaults_to_active_and_honors_explicit_inactive`
- [x] CA-12 → `test_ca12_duplicates_collide_case_insensitively_including_spaces`
- [x] CA-13 → `test_ca13_create_validations_and_strip_normalization` (+ `test_u1_...`)
- [x] CA-14 → `test_ca14_create_response_hides_timestamps_and_logs_creation`
- [x] CA-15 → `test_ca15_detail_matches_list_and_medico_sees_the_same`
- [x] CA-16 → `test_ca16_put_replaces_and_preserves_created_at`
- [x] CA-17 → `test_ca17_put_reactivates_and_deactivates`
- [x] CA-18 → `test_ca18_put_distinguishes_foreign_from_own_description`
- [x] CA-19 → `test_ca19_put_validates_body_path_and_role` (+ `test_u2_...`; incluye precedencia regla 23)
- [x] CA-20 → `test_ca20_delete_is_a_logical_deactivation_without_body`
- [x] CA-21 → `test_ca21_delete_is_idempotent_and_guards_role_and_missing`
- [x] CA-22 → `test_ca22_delete_with_medications_keeps_the_reference`
- [x] CA-23 → `test_ca23_integrity_error_on_commit_becomes_409_and_session_survives`
- [x] CA-24 → `test_ca24_every_error_uses_the_exact_envelope`
- [x] CA-25 → `test_ca25_all_events_carry_ids_and_never_leak_secrets` + `test_ca25_put_deactivation_emits_deactivated_instead_of_updated`
- [x] CA-26 → `test_ca26_openapi_declares_drug_types_contract` + comando `[shell]` con salida exacta
- [x] CA-27 → `uv run alembic check` + `git status --porcelain alembic/` (evidencia arriba)
- [x] CA-28 → `uv run ruff check .` + `uv run ruff format --check .` (evidencia arriba)
- [x] CA-29 → `uv run pytest -q`: 387 passed, sin regresiones
