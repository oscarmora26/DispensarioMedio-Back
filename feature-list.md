# Feature List — Dispensario Médico Backend

> **Fuente de verdad del backlog.** Solo el `team-lead` actualiza estados en este archivo.
> **Solo el humano aprueba** la transición `proposed → approved`.
> Se ejecuta **una feature a la vez**.

## Estados

| Estado | Significado | Quién lo asigna |
|---|---|---|
| `proposed` | Idea registrada, sin aprobar | `team-lead` / humano |
| `approved` | Autorizada para implementarse | **solo humano** |
| `in_progress` | En implementación | `team-lead` |
| `in_review` | Implementada, esperando veredicto del reviewer | `team-lead` |
| `changes_requested` | Revisión con hallazgos bloqueantes/mayores | `team-lead` |
| `done` | Revisada, testeada y aprobada | `team-lead` |
| `blocked` | Dependencia externa, ambigüedad o 3 iteraciones sin converger | `team-lead` |
| `rejected` | Descartada | **solo humano** |

## Feature activa

Ninguna. El backlog queda sin features aprobadas; el humano decide la siguiente.

## Backlog

| ID | Feature | Estado | Spec | Depende de | Actualizado |
|---|---|---|---|---|---|
| FEAT-001 | Fundaciones de la API: scaffold, config, logging, PostgreSQL y Docker | `done` | [FEAT-001](docs/features/FEAT-001.md) | — | 2026-10-20 |
| FEAT-002 | Autenticación JWT: login, sesión y cambio de contraseña | `done` | [FEAT-002](docs/features/FEAT-002.md) | FEAT-001, FEAT-003 | 2026-10-21 |
| FEAT-003 | Esquema de base de datos: modelos SQLAlchemy 2.0 y migraciones Alembic | `done` | [FEAT-003](docs/features/FEAT-003.md) | FEAT-001 | 2026-10-21 |
| FEAT-004 | Gestión de usuarios: CRUD, roles y restablecimiento de contraseña | `done` | [FEAT-004](docs/features/FEAT-004.md) | FEAT-002, FEAT-003 | 2026-10-21 |
| FEAT-005 | Login con email como identificador alternativo | `done` | [FEAT-005](docs/features/FEAT-005.md) | FEAT-002, FEAT-003 | 2026-10-21 |
| FEAT-006 | CORS configurable para el frontend (localhost:4200) | `done` | [FEAT-006](docs/features/FEAT-006.md) | FEAT-001 | 2026-10-21 |
| FEAT-007 | Gestión de tipos de fármacos: CRUD, permisos y desactivación lógica | `done` | [FEAT-007](docs/features/FEAT-007.md) | FEAT-003 | 2026-10-21 |
| FEAT-008 | Gestión de marcas: CRUD, permisos y desactivación lógica | `done` | [FEAT-008](docs/features/FEAT-008.md) | FEAT-003 | 2026-10-21 |

> FEAT-001 se ejecutó por instrucción directa del humano como línea base del proyecto, fuera del ciclo spec → implement → review.

## Cómo aprobar una feature

El humano edita este archivo y cambia el estado de `proposed` a `approved`, o se lo pide explícitamente al `team-lead`. Sin esa acción, el equipo no empieza a trabajar.

## Reglas de transición

```text
proposed ──(humano)──> approved
approved ──(team-lead)──> in_progress ──> in_review ──> done
                                              └──> changes_requested ──> in_progress
cualquiera ──(team-lead)──> blocked
proposed/approved ──(humano)──> rejected
```

- No se salta de `proposed` a `in_progress`.
- No se marca `done` sin veredicto `APPROVED` del reviewer.
- Una feature `blocked` necesita intervención humana para desbloquearse.
- Máximo 3 ciclos de `changes_requested`; después pasa a `blocked`.

## Historial

- 2026-10-20 — FEAT-001 registrada como `done` (bootstrap por instrucción humana).
- 2026-10-20 — FEAT-002 registrada como `proposed`.
- 2026-10-21 — FEAT-003 registrada como `proposed` a partir de `DispensarioMedicoDocs/04-base-de-datos.md`. FEAT-002 pasa a depender de FEAT-003 (necesita `users` y `roles`).
- 2026-10-21 — FEAT-003 **aprobada explícitamente por el humano** (incluye `audit_log`). `pg_stat_statements`/`hypopg` quedan fuera de v1.
- 2026-10-21 — FEAT-003 `done`: reviewer emite `APPROVED` (ciclo 2/3) con 132 tests en verde, `ruff check`/`format --check` limpios y `alembic check` sin diferencias. Informe en `docs/reviews/FEAT-003.md`. Commit local `feat(FEAT-003)`. FEAT-002 queda desbloqueada.
- 2026-10-21 — FEAT-002 **aprobada explícitamente por el humano** con alcance acotado a autenticación (`/auth/login`, `/auth/me`, `/auth/change-password`, JWT/roles y CLI `create-admin`). El CRUD de usuarios y `reset-password` se separan a FEAT-004 (`proposed`).
- 2026-10-21 — Decisiones humanas para FEAT-002: sin refresh tokens (JWT de 30 min, ADR-003); rate limiting en login de 5 fallos/15 min por IP+usuario, en memoria y reiniciable; contraseña nueva con mínimo 8 caracteres.
- 2026-10-21 — FEAT-002 `approved` → `in_progress`. Spec en `docs/features/FEAT-002.md` (45 CA, envelope `{detail, code, errors}`, CLI `create-admin`, plan de pruebas con aislamiento por savepoints).
- 2026-10-21 — FEAT-002 `in_progress` → `in_review`. Implementación completa (PyJWT 2.15.1, pwdlib 0.3.1, rate limiter, CLI); `ruff` limpio, `alembic check` limpio y 131/132 tests verdes. El único fallo es el guard de FEAT-003 que prohíbe modificar `app/core/config.py`, archivo que FEAT-002 debe tocar; el reviewer lo actualiza y lo justifica.
- 2026-10-21 — FEAT-002 `in_review` → `changes_requested` (ciclo 1/3). Reviewer escribe 67 tests nuevos (`198 passed, 1 xfailed`) y emite `CHANGES_REQUESTED`: **MAJOR** en `user_repository.find_user_by_username_or_email` (colisión username/email → `MultipleResultsFound`, el CLI devuelve 4 en vez de 1). Informe en `docs/reviews/FEAT-002.md`.
- 2026-10-21 — FEAT-002 `changes_requested` → `in_review` (ciclo 2/3). Implementer corrige el MAJOR (`.limit(1)`), iguala el coste de Argon2 para usuarios inexistentes, elimina el parámetro muerto de `decode_access_token`, valida positividad de settings y crea el token antes del `commit()`. Suite: `198 passed, 1 failed` (solo el XPASS estricto esperado).
- 2026-10-21 — FEAT-002 `in_review` → `done`: reviewer emite `APPROVED` (ciclo 2/3), cierra los 5 hallazgos y deja **207 tests en verde**, `ruff check`/`format --check` limpios y `alembic check` sin diferencias. Informe en `docs/reviews/FEAT-002.md`. Commit local `feat(FEAT-002)`.
- 2026-10-21 — FEAT-004 **aprobada explícitamente por el humano** ("Sí" a la propuesta de iniciar gestión de usuarios). Alcance: `/users` CRUD, asignación de rol/médico y `reset-password`; no incluye catálogo de médicos.
- 2026-10-21 — FEAT-004 `approved` → `in_progress`. Spec en `docs/features/FEAT-004.md` (41 CA, D-1…D-13, PUT de reemplazo completo, DELETE 204 idempotente, protección del último ADMIN y bloqueo `FOR UPDATE`).
- 2026-10-21 — FEAT-004 `in_progress` → `in_review`. Implementados los 6 endpoints, repositorios, servicio y schemas; `ruff` limpio, `alembic check` limpio y 207 tests existentes en verde (aún sin tests de FEAT-004). Se corrigió el formato del bloque Python de la spec. El implementer usa `clock_timestamp()` para `updated_at` (desviación documentada de `onupdate`).
- 2026-10-21 — FEAT-004 `in_review` → `done`: reviewer emite `APPROVED`; 50 tests nuevos y **257 tests en verde**, `ruff check`/`format --check` limpios y `alembic check` sin diferencias. Se documentan D-14 (`clock_timestamp()`) y la corrección de la regla 31 (204 sin `Content-Length`). Informe en `docs/reviews/FEAT-004.md`. Commit local `feat(FEAT-004)`.
- 2026-10-21 — FEAT-005 registrada y **aprobada explícitamente por el humano**: el login acepta `email` opcional o `username`, exactamente uno, manteniendo `/api/v1` y la retrocompatibilidad. Decisión: opción "email opcional + username".
- 2026-10-21 — FEAT-005 `approved` → `in_progress`. Spec en `docs/features/FEAT-005.md` (18 CA, D-1…D-6; `LoginRequest` con email/username opcionales y modelo "exactamente uno").
- 2026-10-21 — FEAT-005 `in_progress` → `in_review`. Implementado login por `email` o `username` (exactamente uno), retrocompatible; `ruff`/format limpios, `alembic check` limpio y `256 passed, 1 failed` (solo el test de FEAT-002 que espera `username` obligatorio en `{}`; lo actualiza el reviewer). Smoke real: email y username devuelven `200`. Se restauraron las cuentas `admin`/`medico` que el smoke del implementer había borrado.
- 2026-10-21 — FEAT-005 `in_review` → `done`: reviewer emite `APPROVED`; 29 tests nuevos y **286 tests en verde**, `ruff check`/`format --check` limpios y `alembic check` sin diferencias. Se corrigió el fixture destructivo de migraciones que borraba las cuentas permanentes. Informe en `docs/reviews/FEAT-005.md`. Commit local `feat(FEAT-005)`; contraseñas originales de `admin`/`medico` restauradas y verificadas.
- 2026-10-21 — FEAT-006 registrada como `proposed` a pedido del humano: habilitar CORS para el frontend Angular en `http://localhost:4200`. Pendiente de aprobación explícita y de definir si los orígenes se configuran por variable de entorno y si aplica en producción.
- 2026-10-21 — FEAT-006 **aprobada explícitamente por el humano**. Decisiones: orígenes por variable de entorno `CORS_ALLOWED_ORIGINS` (default `http://localhost:4200`) y sin cookies (`allow_credentials=False`, el front usa `Authorization: Bearer`). Queda `approved`, lista para especificación.
- 2026-10-21 — FEAT-006 `approved` → `in_progress`. Spec en `docs/features/FEAT-006.md` (29 CA, D-1…D-12, SUP-1…SUP-5): settings `cors_allowed_origins/methods/headers` con parseo coma-separado o JSON, `CORSMiddleware` dentro de `RequestID`/`AccessLog`, lista vacía deshabilita CORS y `*` prohibido en `prod`. Preguntas no bloqueantes: endurecer `staging` y el default de `prod` (R-1, R-2).
- 2026-10-21 — FEAT-006 `in_progress` → `in_review`. Implementados `app/core/config.py` (constantes, settings `NoDecode`, validadores de orígenes/métodos/cabeceras), `app/main.py` (`CORSMiddleware` antes de AccessLog/RequestID) y `.env.example`. `ruff check`/`format --check` limpios, **286 tests en verde**, `alembic check` limpio y smoke real de preflight (`OPTIONS` ⇒ 200 con `Access-Control-Allow-Origin: http://localhost:4200`) y GET con cabeceras CORS. Sin migraciones ni dependencias nuevas.
- 2026-10-21 — FEAT-006 `in_review` → `done`: reviewer emite `APPROVED`; 62 tests nuevos y **348 tests en verde**, `ruff check`/`format --check` limpios y `alembic check` sin diferencias. Hallazgos menores no bloqueantes documentados en `docs/reviews/FEAT-006.md` (validación de JSON no-array, `http://:4200`, `rstrip("/")`). Commit local `feat(FEAT-006)`. El cambio ajeno en `.opencode/opencode.json` queda fuera del commit.
- 2026-10-21 — FEAT-007 registrada como `proposed` a pedido del humano: gestión de tipos de fármacos (US-07, RF-04, E2). Alcance previsto: CRUD `/drug-types` con CA-CRUD, descripción única (CITEXT), permisos ADMIN escritura / MEDICO lectura y desactivación lógica. Pendiente de aprobación explícita del humano.
- 2026-10-21 — FEAT-007 **aprobada explícitamente por el humano** ("Sí, aprobar e iniciar" vía pregunta de aprobación). Queda `approved`, lista para especificación.
- 2026-10-21 — FEAT-007 `approved` → `in_progress`. Spec en `docs/features/FEAT-007.md` (35 reglas, D-1…D-11, CA-1…CA-29, SUP-1…SUP-4): CRUD `/drug-types` con CA-CRUD, descripción única CITEXT 1..100, ADMIN escritura / MEDICO lectura, DELETE 204 idempotente y `clock_timestamp()` para `updated_at`. Preguntas no bloqueantes: longitud máxima 100 y conservación de capitalización.
- 2026-10-21 — FEAT-007 `in_progress` → `in_review`. Implementación completa (schemas, repository, service, routes + registro en router); `ruff` limpio, `alembic check` limpio y 348 tests existentes en verde (aún sin tests de FEAT-007). Sin migraciones.
- 2026-10-21 — FEAT-007 `in_review` → `done`: reviewer emite `APPROVED`; 39 tests nuevos y **387 tests en verde**, `ruff check`/`format --check` limpios y `alembic check` sin diferencias. Hallazgos menores no bloqueantes en `docs/reviews/FEAT-007.md` (doble consulta en `find_by_description`, `_ensure_unique` carga entidad). Commit local `feat(FEAT-007)`. El cambio ajeno en `.opencode/opencode.json` queda fuera del commit.
- 2026-10-21 — FEAT-008 registrada como `proposed` a pedido del humano: gestión de marcas (US-16, RF-05, E2). Alcance previsto: CRUD `/brands` con CA-CRUD, descripción única (CITEXT), permisos ADMIN escritura / MEDICO lectura y desactivación lógica. Pendiente de aprobación explícita del humano.
- 2026-10-21 — FEAT-008 **aprobada explícitamente por el humano** ("Sí, aprobar e iniciar" vía pregunta de aprobación). Queda `approved`, lista para especificación.
- 2026-10-21 — FEAT-008 `approved` → `in_progress`. Spec en `docs/features/FEAT-008.md` (35 reglas, D-1…D-11, CA-1…CA-29): CRUD `/brands` con CA-CRUD, descripción única CITEXT 1..100, ADMIN escritura / MEDICO lectura, DELETE 204 idempotente y `clock_timestamp()` para `updated_at`. Preguntas no bloqueantes: longitud máxima 100 y conservación de capitalización.
- 2026-10-21 — FEAT-008 `in_progress` → `in_review`. Implementación completa (schemas, repository, service, routes + registro en router); `ruff` limpio, `alembic check` limpio y 387 tests existentes en verde (aún sin tests de FEAT-008). Sin migraciones.
- 2026-10-21 — FEAT-008 `in_review` → `done`: reviewer emite `APPROVED`; 39 tests nuevos y **426 tests en verde**, `ruff check`/`format --check` limpios y `alembic check` sin diferencias. Hallazgos menores no bloqueantes en `docs/reviews/FEAT-008.md` (doble consulta en `find_by_description`, `_ensure_unique` carga entidad, `PUT` sin cambios refresca `updated_at`). Commit local `feat(FEAT-008)`. El cambio ajeno en `.opencode/opencode.json` queda fuera del commit.
