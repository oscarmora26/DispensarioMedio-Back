# FEAT-020 — Refresh tokens: tracking ODD

Feature aprobada por el humano el 2026-10-27 (P-1…P-4). Spec ejecutiva: `docs/features/FEAT-020.md`. Rama: `feat/FEAT-020-refresh-tokens`. Commits locales permitidos; push prohibido (humano).

## Specs

- **S1** (reglas 1–3, CA-29/30): "`Settings.jwt_expire_minutes` cambia su default de `30` a `15`" + "`refresh_token_expire_days: int = Field(default=30, gt=0)`" + `.env.example` (`JWT_EXPIRE_MINUTES=15`, `REFRESH_TOKEN_EXPIRE_DAYS=30`).
- **S2** (reglas 4–5, CA-28): tabla `refresh_tokens` (columnas e índices de la regla 4, verbatim en la spec) + migración aditiva encadenada a `4ad3df9e109d`.
- **S3** (reglas 6–7): "`generate_refresh_token()` devuelve `secrets.token_urlsafe(64)`" y "`hash_refresh_token(token)` devuelve el SHA-256 hex"; solo el hash se persiste.
- **S4** (reglas 8–11, CA-1…CA-4): login emite `refresh_token` + `refresh_expires_in` en el mismo commit que `last_login_at`; `family_id` nuevo por login; borrado perezoso de expirados del usuario.
- **S5** (reglas 12–21, CA-5…CA-17): `POST /auth/refresh` público, `FOR UPDATE`, 401 genérico, rotación con `family_id`/`replaced_by_id`, ventana de gracia 10 s, reuso fuera de ventana revoca familia, usuario inactivo revoca familia, claims actuales desde la base.
- **S6** (reglas 22–24, CA-18…CA-21): `POST /auth/logout` público, 204 siempre, revoca solo ese token (`logout`).
- **S7** (reglas 25–27, CA-22…CA-25): cascada `password_change` (change-password), `password_reset` (reset admin), `user_deactivated` (desactivación), en la misma transacción del cambio.
- **S8** (reglas 29–31): eventos `auth.refresh_succeeded` / `auth.refresh_failed` / `auth.refresh_reuse_detected` / `auth.logout`; jamás el token plano ni su hash.
- **S9** (reglas 32–35, CA-27): ADR-0002, notas de vigencia en FEAT-002 (D-3/R-1/regla 14) y FEAT-004 (R-4), OpenAPI con las rutas nuevas.

## Tasks

| ID | Tarea | Specs | Ruta | Commit |
|---|---|---|---|---|
| T1 | Config + `.env.example` (default 15 min, `refresh_token_expire_days`) | S1 | inline | f8afbdd (config); `.env.example` bloqueado |
| T2 | Modelo `RefreshToken` + migración Alembic | S2 | inline | f8afbdd |
| T3 | Helpers de seguridad + `refresh_token_repository` | S3 | inline | f8afbdd |
| T4 | Schemas + `auth_service` (login dual, refresh, logout) + rutas | S4, S5, S6, S8 | inline | 21f8764 |
| T5 | Cascada en `change_password`, `reset_password`, `deactivate_user` | S7 | inline | 21f8764 |
| T6 | Tests del flujo completo (CA-1…CA-30) | S4–S7 | inline (test-first donde aplica) | 21f8764 |
| T7 | ADR-0002 + notas de vigencia FEAT-002/FEAT-004 | S9 | inline | df168b7 |
| T8 | Gates (ruff, alembic check, pytest), commits, verificación independiente | todas | inline | 775 verdes; 2 rojos por `.env.example` |
| T9 | Review nativa (`review-b3f56e1087841b93`) | todas | facade | BLOCKER R1-001 corregido (`d13b2c1`) |
| T10 | Corrección acotada R1-001 (plan 16 líneas, 20 reales) | regla 23 | inline | `d13b2c1` |
| T11 | Hallazgos del verificador (cascada PUT, mensajes, CA-14 real) | reglas 23/27 | worker `mvr597li-2-4epz` | `fd06c6c` |
| T12 | Validación dirigida de la review | — | facade | **bloqueada**: binding rechazado 2 veces |

## Log

- **L1** (2026-10-27, pedido original del humano): "Realiza la propuesta para la implementacion".
- **L2** (2026-10-27, aprobación): P-1 aprobar e iniciar (revierte ADR-003); P-2 "15 min / 30 días"; P-3 "Body JSON"; P-4 "Logout + cascada".
- **L3** (2026-10-27, cierre de implementación): 7 commits (`c9e7c8f`, `f8afbdd`, `21f8764`, `df168b7`, `26259cd`, `d13b2c1`, `fd06c6c`). Gates: ruff limpio, `alembic check` limpio, **778 passed**. **Pendiente humano**: (1) editar `.env.example` (`JWT_EXPIRE_MINUTES=15`, `REFRESH_TOKEN_EXPIRE_DAYS=30`), único motivo de los 2 rojos; (2) decidir el destino de la autoridad de review parkeada en `targeted_validation_required`.
