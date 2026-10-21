# Revisión FEAT-002 — Autenticación JWT: login, sesión y cambio de contraseña

**Fecha:** 2026-10-21 (ciclo 1) / 2026-10-21 (ciclo 2)
**Estado revisado:** `in_review`
**Spec:** `docs/features/FEAT-002.md`
**Veredicto final:** **APPROVED**

---

## Resumen

Ciclo 1 emitió `CHANGES_REQUESTED` con 1 hallazgo MAJOR, 3 MINOR y 1 SUGERENCIA.
El implementer corrigió los cinco puntos y el reviewer volvió a verificar el
diff, retiró el `xfail` del caso de colisión y añadió tests para los cierres.
La suite completa pasa sin fallos ni xfails, `ruff` y `ruff format --check`
están limpios y `alembic check` no detecta desviaciones de esquema.

**No quedan hallazgos BLOCKER ni MAJOR.** Queda una nota MINOR documental
(la línea 21 de la spec sigue mencionando `expires_minutes` en
`decode_access_token`, parámetro que se retiró por indicación de esta revisión);
no afecta a ningún criterio de aceptación.

---

## Cierre de hallazgos del ciclo 1

| # | Severidad | Hallazgo ciclo 1 | Cambio verificado | Estado |
|---|---|---|---|---|
| 1 | **MAJOR** | `app/repositories/user_repository.py:42-45`: `scalar_one_or_none()` lanzaba `MultipleResultsFound` cuando el `username` coincidía con un usuario y el `email` con otro; el CLI devolvía 4 en lugar de 1 (CA-36). | `select(...).limit(1)` + `scalar_one_or_none()`; docstring explicativo. | **CERRADO** |
| 2 | **MINOR** | `app/services/auth_service.py:52-61`: usuario inexistente no ejecutaba Argon2 (enumeración por timing). | `_DUMMY_HASH` Argon2id de módulo verificado en la rama `user is None`; mismo `401` genérico. | **CERRADO** |
| 3 | **MINOR** | `app/core/security.py:60-71`: `decode_access_token` aceptaba y descartaba `expires_minutes`. | Parámetro eliminado de la firma; la expiración se valida solo con el claim `exp`. | **CERRADO** |
| 4 | **MINOR** | `app/core/config.py:43-47`: settings numéricos sin validación de positividad; `0`/negativo fallaba tarde (token vacío o 500 en login). | `Field(gt=0)` en `jwt_expire_minutes`, `login_rate_limit_max_attempts` y `login_rate_limit_window_seconds`. | **CERRADO** |
| 5 | **SUGERENCIA** | `app/services/auth_service.py:70-73`: `commit()` de `last_login_at` antes de crear el token. | El token se emite antes de asignar `last_login_at` y hacer `commit()`. | **CERRADO** |

### Verificación del MAJOR (evidencia real)

```text
$ uv run pytest tests/test_auth_cli.py::test_duplicate_username_colliding_with_other_user_email -v
tests/test_auth_cli.py::test_duplicate_username_colliding_with_other_user_email PASSED [100%]

============================== 1 passed in 0.23s ==============================
```

El test (sin `xfail`) crea dos usuarios, invoca
`create-admin --username <username-de-A en mayúsculas> --email <email-de-B en mayúsculas>`
y comprueba: salida **1**, stderr `El usuario ya existe`, una sola fila por cada
usuario y ninguna fila nueva.

### Contrato y envelope tras los cambios

- Los 3 endpoints, códigos (`200/400/401/403/422/429`), mensajes y el envelope
  `{detail, code, errors}` no cambiaron: los tests de contrato CA-10…CA-45
  siguen en verde sin modificaciones.
- El `401` de usuario inexistente sigue siendo **byte a byte** idéntico al de
  contraseña incorrecta (CA-13).
- El `403` de inactivo, el `422` normalizado, `WWW-Authenticate` y
  `Retry-After` se mantienen.
- `GET /health` y los tests de FEAT-001/FEAT-003 no se ven afectados.

---

## Hallazgos nuevos

### MINOR (documental, no bloqueante)

**[MINOR] `docs/features/FEAT-002.md:21`** — el alcance dice que "ambas
funciones de token aceptan `secret` y `expires_minutes` opcionales"; tras el
ciclo 2 `decode_access_token` ya no acepta `expires_minutes`. Ningún CA lo
exige y el cambio se hizo a petición de la revisión; conviene que el
spec-author actualice esa línea en la próxima pasada documental.

Sin más hallazgos BLOCKER, MAJOR ni MINOR de código.

---

## Tests añadidos/ajustados en el ciclo 2

- `tests/test_auth_cli.py::test_duplicate_username_colliding_with_other_user_email`
  — se retiró `xfail(strict=True)` y se amplió: código 1, mensaje
  `El usuario ya existe`, sin filas nuevas.
- `tests/test_auth_security.py::test_settings_reject_non_positive_values`
  (6 casos parametrizados: 0 y negativo en las tres settings) → `ValidationError`
  al construir `Settings`.
- `tests/test_auth_security.py::test_decode_access_token_does_not_accept_expires_minutes`
  — la firma ya no expone el parámetro y pasarlo produce `TypeError`.
- `tests/test_auth_login.py::test_unknown_user_runs_dummy_hash_and_keeps_generic_401`
  — espía `verify_password`: el usuario inexistente ejecuta **una** verificación
  contra un hash `$argon2id$` y la respuesta sigue siendo el `401`
  `invalid_credentials` genérico.

---

## Evidencia final de comandos

```text
$ uv run pytest -q
207 passed in 69.79s (0:01:09)

$ uv run ruff check .
All checks passed!

$ uv run ruff format --check .
81 files already formatted

$ uv run alembic check
No new upgrade operations detected.

$ git status --porcelain alembic/
(vacío)

$ uv sync --locked
Resolved 80 packages in 1ms
Checked 77 packages in 13ms
```

Ciclo 1 cerró CA-4 con `pwdlib 0.3.1`, `pyjwt 2.15.1` y prefijo `$argon2id`.

---

## Cobertura de criterios de aceptación

| CA | Test / evidencia | Estado |
|---|---|---|
| CA-1 | `test_auth_security.py::test_ca1_env_example_documents_jwt_and_rate_limit_keys`, `::test_ca1_placeholder_secret_only_lives_in_config` | ✅ |
| CA-2 | `test_auth_security.py::test_ca2_settings_defaults` (+ `::test_settings_reject_non_positive_values`) | ✅ |
| CA-3 | `test_auth_security.py::test_ca3_*` | ✅ |
| CA-4 | Shell: `uv sync --locked`, versiones, prefijo `$argon2id` | ✅ |
| CA-5 | `test_auth_security.py::test_ca5_hash_is_salted_and_verifies` | ✅ |
| CA-6 | `test_auth_security.py::test_ca6_token_round_trip_has_exact_claims` | ✅ |
| CA-7 | `test_auth_security.py::test_ca7_*` | ✅ |
| CA-8 | `test_auth_security.py::test_ca8_unknown_hash_returns_false` + `test_auth_login.py::test_corrupt_hash_is_401_not_500` | ✅ |
| CA-9 | `test_auth_security.py::test_ca9_custom_expiry_and_string_subject` | ✅ |
| CA-10 | `test_auth_login.py::test_ca10_admin_login_response_shape` | ✅ |
| CA-11 | `test_auth_login.py::test_ca11_medico_login_exposes_doctor_id` | ✅ |
| CA-12 | `test_auth_login.py::test_ca12_username_is_case_insensitive_and_stripped` | ✅ |
| CA-13 | `test_auth_login.py::test_ca13_invalid_credentials_are_indistinguishable` + `::test_unknown_user_runs_dummy_hash_and_keeps_generic_401` | ✅ |
| CA-14 | `test_auth_login.py::test_ca14_inactive_user_requires_correct_password` | ✅ |
| CA-15 | `test_auth_login.py::test_ca15_missing_fields_and_length_limits` | ✅ |
| CA-16 | `test_auth_login.py::test_ca16_last_login_at_only_changes_on_success` | ✅ |
| CA-17 | `test_auth_login.py::test_ca17_jwt_expire_minutes_is_honoured_end_to_end` | ✅ |
| CA-18 | `test_auth_session.py::test_ca18_me_returns_medico_identity` | ✅ |
| CA-19 | `test_auth_session.py::test_ca19_missing_or_foreign_scheme_is_not_authenticated` | ✅ |
| CA-20 | `test_auth_session.py::test_ca20_invalid_tokens_are_rejected`, `::test_ca20_deleted_user_token_is_invalid` | ✅ |
| CA-21 | `test_auth_session.py::test_ca21_deactivation_applies_immediately` | ✅ |
| CA-22 | `test_auth_session.py::test_ca22_role_claim_is_not_trusted` | ✅ |
| CA-23 | `test_auth_password.py::test_ca23_successful_change_invalidates_old_password` | ✅ |
| CA-24 | `test_auth_password.py::test_ca24_wrong_current_password_keeps_hash` | ✅ |
| CA-25 | `test_auth_password.py::test_ca25_new_password_must_differ_from_current` | ✅ |
| CA-26 | `test_auth_password.py::test_ca26_confirmation_mismatch_is_422` | ✅ |
| CA-27 | `test_auth_password.py::test_ca27_password_length_boundaries` | ✅ |
| CA-28 | `test_auth_password.py::test_ca28_change_requires_authentication_and_active_user` | ✅ |
| CA-29 | `test_auth_password.py::test_ca29_existing_token_stays_valid_after_change` | ✅ |
| CA-30 | `test_auth_rate_limit_api.py::test_ca30_fifth_failure_blocks_sixth_correct_attempt` | ✅ |
| CA-31 | `test_auth_rate_limit_api.py::test_ca31a_*`, `::test_ca31b_*`, `::test_ca31c_*`, `::test_ca31d_*` | ✅ |
| CA-32 | `test_auth_rate_limit_unit.py::test_ca32_*` | ✅ |
| CA-33 | `test_auth_rate_limit_api.py::test_ca33_singleton_configure_and_forwarded_for_ignored` | ✅ |
| CA-34 | `test_auth_cli.py::test_ca34_help_and_invalid_arguments` | ✅ |
| CA-35 | `test_auth_cli.py::test_ca35_creates_admin_and_allows_login` | ✅ |
| CA-36 | `test_auth_cli.py::test_ca36_duplicates_do_not_overwrite` + `::test_duplicate_username_colliding_with_other_user_email` (sin xfail) | ✅ |
| CA-37 | `test_auth_cli.py::test_ca37_invalid_inputs_exit_2_without_writing` | ✅ |
| CA-38 | `test_auth_cli.py::test_ca38_missing_admin_role_and_database_error` | ✅ |
| CA-39 | `test_auth_logs.py::test_ca39_logs_never_contain_secrets_or_identifiers` | ✅ |
| CA-40 | `test_auth_logs.py::test_ca40_login_body_is_not_logged` | ✅ |
| CA-41 | `test_auth_openapi.py::test_ca41_openapi_declares_auth_contract` | ✅ |
| CA-42 | Shell: `alembic check` | ✅ |
| CA-43 | Shell: `ruff check .` y `ruff format --check .` | ✅ |
| CA-44 | Shell: `pytest -q` (207 passed) | ✅ |
| CA-45 | `test_auth_openapi.py::test_ca45_error_envelope_is_uniform` + `::test_health_endpoint_is_unchanged` | ✅ |

**Criterios sin cobertura dedicada:** ninguno. CA-4, CA-42, CA-43 y CA-44 son
comprobaciones de shell por definición de la spec.

---

## Riesgos residuales (aceptados, sin cambios respecto al ciclo 1)

- **D-3 / R-5:** sin revocación; un token sigue válido tras cambiar la
  contraseña (verificado en CA-29). Mitigado por `exp` de 30 min.
- **D-5 / R-2:** rate limit por proceso; con N workers el límite efectivo es N×5.
- **R-3:** detrás de proxy, `request.client.host` es la IP del proxy; no se lee
  `X-Forwarded-For` (falsificable).
- **R-4:** la enumeración por timing se reduce con el hash ficticio, pero no es
  una garantía criptográfica de tiempo constante; el rate limit sigue siendo la
  defensa principal.
- **R-12:** un `MEDICO` sin `doctor_id` emite `doctor_id: null`; se resolverá en
  la feature de visitas.

---

## Conclusión

Los cinco hallazgos del ciclo 1 están cerrados y verificados con tests. La suite
pasa completa (207/207), el lint y el formato están limpios, no hay cambios de
esquema y el contrato HTTP no se alteró.

**Veredicto: APPROVED.**
