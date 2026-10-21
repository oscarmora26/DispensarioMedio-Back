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

Ninguna. El backlog queda sin features aprobadas pendientes: el humano debe aprobar la siguiente (catálogos, médicos, pacientes o visitas).

## Backlog

| ID | Feature | Estado | Spec | Depende de | Actualizado |
|---|---|---|---|---|---|
| FEAT-001 | Fundaciones de la API: scaffold, config, logging, PostgreSQL y Docker | `done` | [FEAT-001](docs/features/FEAT-001.md) | — | 2026-10-20 |
| FEAT-002 | Autenticación JWT: login, sesión y cambio de contraseña | `done` | [FEAT-002](docs/features/FEAT-002.md) | FEAT-001, FEAT-003 | 2026-10-21 |
| FEAT-003 | Esquema de base de datos: modelos SQLAlchemy 2.0 y migraciones Alembic | `done` | [FEAT-003](docs/features/FEAT-003.md) | FEAT-001 | 2026-10-21 |
| FEAT-004 | Gestión de usuarios: CRUD, roles y restablecimiento de contraseña | `done` | [FEAT-004](docs/features/FEAT-004.md) | FEAT-002, FEAT-003 | 2026-10-21 |

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
