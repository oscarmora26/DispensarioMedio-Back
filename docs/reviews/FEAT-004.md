# Revisión FEAT-004 — Gestión de usuarios: CRUD, roles y restablecimiento

**Veredicto:** `APPROVED`
**Fecha:** 2026-10-21
**Feature:** FEAT-004 (`docs/features/FEAT-004.md`)
**Reviewer:** agente `reviewer`; cierre de evidencia completado por el `team-lead` (límite de pasos del subagente).

## Evidencia final

| Comando | Resultado |
|---|---|
| `uv run pytest -q` | **257 passed** en 94,07 s (207 previos + 50 nuevos) |
| `uv run ruff check .` | `All checks passed!` |
| `uv run ruff format --check .` | `96 files already formatted` |
| `uv run alembic check` | `No new upgrade operations detected.` |
| `git status --porcelain alembic/` | vacío |
| CA-38 (comando exacto de la spec) | salida idéntica a la esperada |

Salida de CA-38:

```text
['/api/v1/users', '/api/v1/users/{user_id}', '/api/v1/users/{user_id}/reset-password']
['users']
True
True True
['201', '400', '401', '403', '409', '422']
['204', '400', '401', '403', '404', '422']
```

## Hallazgos

### MENOR

1. **`app/services/user_service.py:79-88` — `_touch` usa `func.clock_timestamp()`** en lugar de confiar en `onupdate=func.now()` (regla 27).
   Verificado: `created_at` y `password_hash` no cambian; `updated_at` cambia en `PUT`, `reset-password` y el primer `DELETE`, y no cambia en el segundo; `UserRead` no expone `updated_at`, por lo que la expresión SQL no se serializa. Se acepta como desviación documentada en **D-14**.
   Sugerencia: alinear `UpdatedAtMixin` a `clock_timestamp()` en una feature posterior si se quiere una sola vía.

2. **`app/api/v1/routes/users.py:161-185` — el `204` no incluye `Content-Length`.**
   Starlette omite deliberadamente la cabecera en `204`/`304` y RFC 9110 la prohíbe en `204`. El cuerpo es `b""` y no hay JSON. Era un defecto de la redacción original de la regla 31; corregida en la spec.

### SUGERENCIA

3. **`app/api/v1/routes/users.py:54-57`** — `search` valida `max_length=100` antes del `strip`; 100 caracteres útiles + espacios ⇒ `422`. La spec no fija el orden; conviene aclararlo en una revisión futura.
4. **Catálogo de `sort` duplicado** entre `app/schemas/user.py` (`SORT_PATTERN`/`SORT_FIELDS`) y `app/services/user_service.py` (`parse_sort`). Mantener una única fuente evitaría divergencias.

No hay hallazgos **BLOCKER** ni **MAJOR**.

## Cobertura CA → test

| CA | Test / evidencia |
|---|---|
| CA-1, CA-2, CA-3, CA-36 | `tests/test_users_authz.py` |
| CA-4 a CA-10, CA-19 | `tests/test_users_list.py` |
| CA-11 a CA-18 | `tests/test_users_create.py` |
| CA-20 a CA-27 | `tests/test_users_update.py` |
| CA-28, CA-29 | `tests/test_users_delete.py` |
| CA-30 a CA-33 | `tests/test_users_reset.py` |
| CA-34, CA-35 | `tests/test_users_integrity.py` |
| CA-37 | `tests/test_users_logs.py` |
| CA-38 | `tests/test_users_openapi.py` + comando de la spec |
| CA-39 | `uv run alembic check` + `git status --porcelain alembic/` |
| CA-40 | `uv run ruff check .` + `uv run ruff format --check .` |
| CA-41 | `uv run pytest -q` → 257 passed |
| U-1 a U-8 | `tests/test_users_unit.py` |

## Aislamiento y datos

- `db_conn`/`db_session` con `join_transaction_mode="create_savepoint"`; todo se revierte al final de cada test.
- Usuarios creados con `tests/auth_support.create_auth_user` (hash Argon2id real) y médicos con `tests/db_support.create_doctor`.
- Tokens con `tests/auth_support.token_for` + `auth_headers`; rate limiter reiniciado por la fixture `autouse`.
- Sin datos personales reales; identificadores sintéticos con `unique_suffix()`.
- El escenario de último ADMIN neutraliza dentro de la transacción a los demás ADMIN y confía en el rollback; no se borran roles ni usuarios.

## Riesgos residuales

- **R-3/R-5**: `FOR UPDATE` sobre `roles` serializa las mutaciones de ADMIN; dos `PUT` concurrentes siguen siendo *last-write-wins* (sin bloqueo optimista). Aceptado en v1.
- **R-4**: `reset-password` no revoca tokens vigentes (heredado de FEAT-002/ADR-003).
- La prueba con dos transacciones reales compitiendo por el rol ADMIN queda fuera de la suite automática; CA-34 verifica el orden de bloqueo de forma estructural.
- `R-2`: el contrato devuelve `role` (nombre) pero la entrada es `role_id`; el frontend depende del seed fijo `ADMIN`/`MEDICO`.

## Archivos de test añadidos

`tests/users_support.py`, `tests/test_users_unit.py`, `tests/test_users_authz.py`, `tests/test_users_list.py`, `tests/test_users_create.py`, `tests/test_users_update.py`, `tests/test_users_delete.py`, `tests/test_users_reset.py`, `tests/test_users_integrity.py`, `tests/test_users_logs.py`, `tests/test_users_openapi.py`.
