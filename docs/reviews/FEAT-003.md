# Revisión FEAT-003 — Esquema de base de datos: modelos SQLAlchemy 2.0 y migraciones Alembic

**Ciclo:** 2/3
**Fecha:** 2026-10-21
**Reviewer:** agente reviewer
**Spec revisada:** `docs/features/FEAT-003.md` (incluye enmiendas R-12, R-13 y CA-4/16/17/24)

## Veredicto

**APPROVED**

La suite completa pasa, Ruff está limpio, `alembic check` no detecta diferencias y los dos
hallazgos del ciclo 1 están corregidos y verificados contra la base real. No quedan
hallazgos BLOQUEANTES ni MAYORES. Solo quedan observaciones NIT/SUGERENCIA.

## Evidencia

| Comando | Resultado |
|---|---|
| `uv run pytest -q` | **132 passed** (89,39 s; 0 failed) |
| `uv run ruff check .` | `All checks passed!` (exit 0) |
| `uv run ruff format --check .` | `57 files already formatted` (exit 0) |
| `uv run alembic check` | `No new upgrade operations detected.` (exit 0) |
| `uv run alembic current` | `42de9c3ddd3a (head)` |
| `uv run alembic heads` | una sola cabeza (`42de9c3ddd3a (head)`) |

Estado de la base al terminar (contenedor `dispensario-db`, base `dispensario`):

```text
tables=12
roles=2
users=0
idx=38
ext=citext,pg_trgm
version=42de9c3ddd3a
```

### Verificación directa de R-12 (transacción + ROLLBACK)

```sql
DO $$
DECLARE d bigint; p bigint; u bigint; r bigint; v bigint;
BEGIN
    -- refs: doctor, paciente y usuario ADMIN
    ...
    BEGIN
        INSERT INTO visits (... status 'ANULADA', annulled_by=u, annulled_at=now(),
                            annulment_reason=NULL);
        RAISE EXCEPTION 'FALLO: se acepto annulment_reason NULL';
    EXCEPTION WHEN check_violation THEN
        RAISE NOTICE 'OK: ANULADA con annulment_reason NULL rechazada';
    END;
    -- idem annulled_by NULL y annulled_at NULL
    INSERT INTO visits (... annulment_reason='Motivo valido') RETURNING id INTO v;
    RAISE NOTICE 'OK: ANULADA completa aceptada (id %)', v;
END $$;
ROLLBACK;
```

Salida real:

```text
BEGIN
NOTICE:  OK: ANULADA con annulment_reason NULL rechazada
NOTICE:  OK: ANULADA con annulled_by NULL rechazada
NOTICE:  OK: ANULADA con annulled_at NULL rechazada
DO
NOTICE:  OK: ANULADA completa aceptada (id 4)
ROLLBACK
```

Definición vigente en el catálogo:

```text
CHECK (((((status)::text = 'REGISTRADA'::text) AND (annulled_by IS NULL) AND (annulled_at IS NULL)
  AND (annulment_reason IS NULL)) OR (((status)::text = 'ANULADA'::text) AND (annulled_by IS NOT NULL)
  AND (annulled_at IS NOT NULL) AND (annulment_reason IS NOT NULL)
  AND (btrim((annulment_reason)::text) <> ''::text))))
```

### Verificación directa de R-13

- `app/db/session.py` pasa `native_inet_types=False` a `create_async_engine`; el diff contiene
  exactamente una línea añadida y ninguna eliminada.
- `uv run python -c "from app.db.session import engine; print(engine.dialect._native_inet_types)"`
  imprime `False`.
- `tests/test_db_constraints.py::test_audit_log_ip_is_returned_as_str` inserta con `str`, hace
  `refresh()` y comprueba que devuelve `str` usando un engine **sin** el flag; es decir, el
  procesador del modelo mantiene el contrato incluso fuera de `app/db/session.py`.
- `tests/test_db_constraints.py::test_app_engine_disables_native_inet_types` comprueba que el
  engine de la app tiene el flag desactivado.

## Hallazgos

### NIT — `app/models/audit_log.py:20-34` (`INETAsString`)

El engine ya devuelve `str` con `native_inet_types=False`; el `result_processor` es redundante
para el runtime de la app. **No es un defecto**: funciona como defensa en profundidad para
engines ad-hoc (Alembic, scripts, el engine del propio reviewer), mantiene el contrato
`Mapped[str | None]` y no altera el DDL (`alembic check` limpio; el tipo físico sigue siendo
`inet`). Es aceptable tal como está. Sugerencia: si se quiere reducir superficie, un
`TypeDecorator` sería más idiomático que heredar de `INET`, pero la subclase actual preserva
mejor la comparación/reflexión de Alembic. No requiere cambios.

### NIT — `app/models/audit_log.py:20`

`INETAsString` no declara `cache_ok`; no se observaron warnings al compilar/ejecutar
`select(AuditLog.ip)` (probado con `warnings.simplefilter("error")`). Si en el futuro se usa el
tipo en SQL cacheable y aparece un warning de cache key, añadir `cache_ok = True`.

### SUGERENCIA — higiene de archivos no versionados

`docs/mcp-postgres.md` sigue sin trackear y es ajeno a FEAT-003 (además tiene mojibake). No es
un hallazgo de esta feature; conviene decidir si se commitea en un cambio aparte o se ignora.

### Riesgos residuales aceptados (documentados en la spec)

- **R-1**: `ix_medications_description_trgm` indexa `((description)::text)`; las búsquedas
  deberán castear (`description::text ILIKE ...`). Verificado en `pg_indexes`.
- **R-2**: `audit_log` no tiene índice sobre `user_id`; solo `pk_audit_log` (verificado).
- **R-5**: `updated_at` se refresca solo desde el ORM; no hay triggers (verificado:
  `test_no_triggers_are_created_in_v1`).
- **R-10**: `citext` no impone longitudes; queda para Pydantic/servicios.
- **R-12**: corregido con `annulment_reason IS NOT NULL` en modelo y migración.
- **R-13**: corregido con el flag del dialecto y el procesador del modelo.
- La base quedó en `head`, con 12 tablas, 38 índices, 2 roles y 0 usuarios. No quedan datos de
  prueba (todos los tests de integración usan transacción + rollback).

## Criterios de aceptación

- [x] **CA-1** 11 tablas exactas → `test_ca01_metadata_contains_exactly_the_eleven_domain_tables`
- [x] **CA-2** mappers/configuración sin ambigüedad → `test_ca02_mappers_configure_without_ambiguity`
- [x] **CA-3** sin `create_all` ni ENUM → `test_ca03_no_create_all_in_app_and_no_enum_in_models`
- [x] **CA-4** 13 archivos y `__init__` con 11 modelos → `test_ca04_models_package_has_expected_files_and_exports`
- [x] **CA-5** `upgrade head` limpio y una sola cabeza → `test_ca05_alembic_reports_a_single_head`, `test_ca20_full_roundtrip_restores_the_expected_schema`
- [x] **CA-6** 6 revisiones lineales en orden → `test_ca06_migration_set_is_a_single_linear_chain`
- [x] **CA-7** 12 tablas físicas → `test_ca07_twelve_physical_tables_in_public_schema`
- [x] **CA-8** solo `citext` y `pg_trgm` → `test_ca08_only_citext_and_pg_trgm_extensions`
- [x] **CA-9** `c=9, f=12, p=11, u=11` (43) → `test_ca09_constraint_counts_by_type`
- [x] **CA-10** 12 FK con nombre y `confdeltype` → `test_ca10_foreign_keys_names_references_and_delete_actions`
- [x] **CA-11** 9 CHECK con nombre exacto → `test_ca11_check_constraint_names`
- [x] **CA-12** 38 índices, grupos y definiciones → `test_ca12_index_count_and_groups`, `test_ca12_visit_indexes_are_descending`, `test_ca12_trigram_indexes_use_gin`, `test_ca12_audit_log_only_has_primary_key_index`
- [x] **CA-13** `uq_patients_carnet` único/parcial → `test_ca13_carnet_unique_index_is_partial`
- [x] **CA-14** tipos, longitudes, precisión y defaults → `test_ca14_critical_column_types_and_defaults`, `test_ca14_timestamp_columns_default_now_only_on_creation`, `test_ca14_boolean_columns_default_true`
- [x] **CA-15** 11 PK identity `BY DEFAULT` → `test_ca15_primary_keys_are_identity_by_default`
- [x] **CA-16** seed 2 roles, 0 usuarios, 0 contraseñas en claro → `test_ca16_seed_roles_without_users_or_plaintext_passwords`
- [x] **CA-17** seed idempotente (mismos ids) y `downgrade -1`/`upgrade` → `test_seed_roles_is_idempotent_in_transaction`, `test_ca17_downgrade_one_and_upgrade_restores_seed`, `test_unseed_sql_keeps_roles_in_use`
- [x] **CA-18** `alembic check` limpio → `test_ca18_alembic_check_reports_no_new_operations`, `test_ca20_after_roundtrip_alembic_check_is_still_clean`
- [x] **CA-19** `downgrade base` deja solo `alembic_version` → `test_ca19_downgrade_base_leaves_only_alembic_version`
- [x] **CA-20** round-trip completo → `test_ca20_full_roundtrip_restores_the_expected_schema`
- [x] **CA-21** `upgrade head` idempotente → `test_ca21_upgrade_head_is_idempotent`
- [x] **CA-22** Ruff check/format limpios → verificado por comandos (tabla de evidencia)
- [x] **CA-23** `pytest -q` en verde → **132 passed**
- [x] **CA-24** infraestructura intacta salvo el flag de `session.py`; `feature-list.md` excluido → `test_ca24_shared_infrastructure_files_are_not_modified`, `test_ca24_feature_list_changes_are_owned_by_team_lead`

## Cobertura adicional (reglas de negocio y plan de pruebas)

- Constraints: `ck_roles_name_allowed`, `ck_doctors_cedula_format`, `ck_patients_cedula_format`,
  `ck_patients_patient_type_allowed`, `ck_patients_carnet_required_for_student`,
  `ck_visits_status_allowed`, `ck_visits_annulment_consistency` (incluido el caso NULL de R-12),
  `ck_visit_medications_quantity_positive` (incluye `0.004` redondeado a `0.00`),
  `ck_audit_log_action_allowed`.
- Uniques: roles, usuarios (`username`/`email`/`doctor_id`), cédulas, catálogos, posición,
  identidad de medicamento, carnet parcial y `(visit_id, medication_id)`.
- Integridad referencial: 6 escenarios `RESTRICT`, `CASCADE` de `visits` y `SET NULL` de
  `audit_log.user_id`.
- Migraciones: `downgrade -1`/`upgrade`, `downgrade base`/`upgrade`, round-trip doble,
  idempotencia y `alembic check`.
- Defaults de base: `created_at`/`updated_at`, `is_active`, ausencia de triggers.
- Contrato `str` de `audit_log.ip` con engine con y sin `native_inet_types=False`.

## Archivos de test

- `tests/conftest.py` — fixture `db_conn` con transacción y rollback por test.
- `tests/db_support.py` — helpers de inserción y aserciones de violación con SAVEPOINT.
- `tests/test_models_metadata.py` — metadata, naming, índices, relaciones, cadena de revisiones,
  seed SQL y CA-24.
- `tests/test_db_catalog.py` — CA-7 a CA-16, triggers y versión de Alembic.
- `tests/test_db_constraints.py` — constraints, uniques, integridad referencial, seed,
  defaults y contrato de `ip`.
- `tests/test_db_migrations.py` — CA-5 y CA-17 a CA-21 con la CLI real de Alembic.

## Conclusión

Los dos hallazgos del ciclo 1 quedaron resueltos y verificados de forma independiente:
el CHECK de anulación rechaza `NULL` en los tres campos y `audit_log.ip` respeta `str | None`
tanto en el engine de la app como en engines ad-hoc. La suite completa pasa y la base quedó
consistente con las migraciones. **APPROVED**.
