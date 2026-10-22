# Revisión FEAT-009 — Gestión de ubicaciones: CRUD, permisos y desactivación lógica

**Veredicto: APPROVED**

| Comprobación | Resultado |
|---|---|
| `uv run pytest -q` | **466 passed** (426 previos + 40 nuevos), ~136 s |
| `uv run ruff check .` | `All checks passed!` |
| `uv run ruff format --check .` | `145 files already formatted` |
| `uv run alembic check` | `No new upgrade operations detected.` |
| `git status --porcelain alembic/` | vacío (sin migraciones) |
| OpenAPI CA-27 (comando `[shell]` de la spec) | salida exacta esperada |

No se modificó código de producción: solo `tests/**` (6 archivos nuevos) y este informe.
Producción revisada (sin tocar): `app/schemas/location.py`, `app/repositories/location_repository.py`,
`app/services/location_service.py`, `app/api/v1/routes/locations.py`, `app/api/v1/router.py` (registro).

## Hallazgos

- [MENOR] `app/repositories/location_repository.py:33-58` — `find_location_by_position`
  primero selecciona el `id` y luego recarga la fila completa con `get_location_by_id`
  (2 consultas por cada chequeo de unicidad). Funciona y los tests lo cubren; se podría
  resolver en una sola consulta. Mismo patrón ya aceptado como MENOR en FEAT-007/FEAT-008.
- [SUGERENCIA] `app/services/location_service.py:108-125` — `_ensure_unique_position` carga la
  entidad completa y la descarta; un `SELECT 1` / `EXISTS` bastaría en el camino de escritura. Sin
  impacto funcional.
- [SUGERENCIA] `app/services/location_service.py:188-217` — `update_location` refresca
  `updated_at` y emite `location.updated` aunque el body no cambie nada. Conforme a la spec
  (toda actualización no-desactivadora emite `updated`; regla 33); solo constancia.
- [SUGERENCIA] `app/services/location_service.py:30-43` — `parse_sort` importa `AppError`
  dentro de la función en vez de a nivel de módulo. Funciona; solo estilo.

Sin hallazgos BLOQUEANTES ni MAYORES. Contrato de API, permisos, validaciones, envelope,
códigos, precedencia de errores (regla 23), unicidad compuesta CITEXT (R-1), idempotencia del
DELETE, `clock_timestamp()`, logs y OpenAPI verificados test por test.

## Criterios de aceptación

- [x] CA-1 → `test_ca01_medico_reads_but_never_writes`
- [x] CA-2 → `test_ca02_authentication_matrix`
- [x] CA-3 → `test_ca03_detail_of_missing_id_is_404_with_exact_envelope`
- [x] CA-4 → `test_ca04_defaults_and_pagination_formula` (+ `test_u6_...` para `pages`)
- [x] CA-5 → `test_ca05_page_and_size_limits_and_out_of_range_page`
- [x] CA-6 → `test_ca06_sort_catalog_direction_tiebreak_and_invalids` (+ `test_u4_...` catálogo de 14 variantes)
- [x] CA-7 → `test_ca07_search_covers_four_fields_case_insensitive_and_escapes_wildcards` (+ `test_u5_...` escape)
- [x] CA-8 → `test_ca08_is_active_filter_alone_and_combined_with_search`
- [x] CA-9 → `test_ca09_empty_search_results_have_zero_pages` (+ `test_u6_...` con `total=0`)
- [x] CA-10 → `test_ca10_items_never_leak_timestamps` (+ `test_u3_...`, y forma exacta en CA-11/15/16/17)
- [x] CA-11 → `test_ca11_create_defaults_to_active_and_honors_explicit_inactive`
- [x] CA-12 → `test_ca12_composite_duplicates_collide_case_insensitively_including_spaces`
- [x] CA-13 → `test_ca13_single_column_differences_do_not_collide`
- [x] CA-14 → `test_ca14_create_validations_and_strip_normalization` (+ `test_u1_...`)
- [x] CA-15 → `test_ca15_create_response_hides_timestamps_and_logs_creation`
- [x] CA-16 → `test_ca16_detail_matches_list_and_medico_sees_the_same`
- [x] CA-17 → `test_ca17_put_replaces_and_preserves_created_at`
- [x] CA-18 → `test_ca18_put_changes_position_reactivates_and_deactivates`
- [x] CA-19 → `test_ca19_put_distinguishes_foreign_from_own_position`
- [x] CA-20 → `test_ca20_put_validates_body_path_and_role` (+ `test_u2_...`; incluye precedencia regla 23)
- [x] CA-21 → `test_ca21_delete_is_a_logical_deactivation_without_body`
- [x] CA-22 → `test_ca22_delete_is_idempotent_and_guards_role_and_missing`
- [x] CA-23 → `test_ca23_delete_with_medications_keeps_the_reference`
- [x] CA-24 → `test_ca24_integrity_error_on_commit_becomes_409_and_session_survives`
- [x] CA-25 → `test_ca25_every_error_uses_the_exact_envelope`
- [x] CA-26 → `test_ca26_all_events_carry_ids_and_never_leak_secrets` + `test_ca26_put_deactivation_emits_deactivated_instead_of_updated`
- [x] CA-27 → `test_ca27_openapi_declares_locations_contract` + comando `[shell]` con salida exacta
- [x] CA-28 → `uv run alembic check` + `git status --porcelain alembic/` (evidencia arriba)
- [x] CA-29 → `uv run ruff check .` + `uv run ruff format --check .` (evidencia arriba)
- [x] CA-30 → `uv run pytest -q`: 466 passed, sin regresiones
