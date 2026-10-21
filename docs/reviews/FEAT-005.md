# Revisión FEAT-005 — Login con email como identificador alternativo

**Veredicto:** `APPROVED`
**Fecha:** 2026-10-21
**Feature:** FEAT-005 (`docs/features/FEAT-005.md`)
**Reviewer:** agente `reviewer`

## Evidencia final

| Comando | Resultado |
|---|---|
| `uv run pytest -q` | **286 passed** en 97,26 s (257 previos + 29 nuevos) |
| `uv run ruff check .` | `All checks passed!` |
| `uv run ruff format --check .` | `104 files already formatted` |
| `uv run alembic check` | `No new upgrade operations detected.` |
| `git status --porcelain alembic/ pyproject.toml uv.lock` | vacío |
| CA-16 (comando exacto de la spec) | salida esperada (ver abajo) |
| Login manual final | `admin` y `medico` por email y por username ⇒ `200` |
| `uv run pytest tests/test_db_migrations.py -q` | 9 passed; cuentas 100/101 intactas |

Salida de CA-16:

```text
['password']
2
[['email', 'password'], ['password', 'username']]
Correo electronico. Envía exactamente uno: `email` o `username`
```

`POST /api/v1/auth/login` conserva `['200', '401', '403', '422', '429']`.

## Hallazgos

No hay hallazgos **BLOQUEANTES** ni **MAYORES** abiertos en el código de producción de FEAT-005.
Los dos hallazgos MAYORES de infraestructura de tests se corrigieron durante la revisión
(ámbito permitido: `tests/**`).

### MAYOR — corregido

1. **`tests/test_db_migrations.py` — la suite completa borraba las cuentas permanentes del humano.**
   `test_ca19_downgrade_base_leaves_only_alembic_version` y `test_ca20_full_roundtrip_restores_the_expected_schema`
   ejecutan `alembic downgrade base`, que hace `DROP` de `users`. La primera ejecución completa del reviewer
   dejó `SELECT count(*) FROM users` en **0**: las cuentas `admin` (id 100) y `medico` (id 101) desaparecieron.
   Se añadió el fixture `preserve_permanent_dev_accounts` (autouse del módulo), que guarda y restaura
   `users` y `doctors` antes/después de cada test de migraciones. Verificado: tras la suite final las
   cuentas 100/101 siguen presentes con su rol, email y `is_active`.
   **Riesgo residual:** el fixture protege usuarios y médicos, no el resto de tablas de dominio. Si la BD
   de desarrollo acumula pacientes/visitas, `downgrade base` sigue siendo destructivo. Recomendación:
   mover los tests de downgrade a una base desechable creada por el propio test.

2. **Tests que asumían una BD vacía fallaban de forma determinista con las cuentas permanentes.**
   El implementer lo describió como «interferencia transitoria»; no lo es. Evidencia:
   `downgrade -1` ejecuta `UNSEED_ROLES_SQL`, que solo borra roles **sin** usuarios; con `admin`/`medico`
   ambos roles están en uso, así que `roles` sigue en 2 y el test esperaba 0.
   Corregidos en tests, sin tocar producción:
   - `tests/test_db_catalog.py::test_ca16_seed_roles_without_users_or_plaintext_passwords`: `users == 0` → el seed no crea usuarios (se reejecuta el seed dentro de la transacción y se compara el conteo).
   - `tests/test_db_constraints.py::test_unseed_sql_keeps_roles_in_use`: se comprueba la invariante «todo rol en uso sobrevive» en vez de asumir que solo queda ADMIN.
   - `tests/test_db_migrations.py::test_ca17_downgrade_one_and_upgrade_restores_seed`: los roles en uso sobreviven al `downgrade -1` y `upgrade head` deja los dos roles.
   - `tests/test_users_list.py::test_ca04_defaults_and_pagination_formula`: `total == 4` → `total == total_db` (incluye las cuentas permanentes).

3. **Incidente operativo — cuentas restauradas.**
   Como consecuencia del punto 1, las cuentas se restauraron con `id 100/101`, `username`/`email`
   originales, rol ADMIN/MEDICO, `is_active = true` y contraseña temporal **`MiClaveSegura123`**.
   El `team-lead` **restauró después las contraseñas aleatorias originales** entregadas al humano y
   verificó login `200` por email y por username; `MiClaveSegura123` ya no es válida.
   `last_login_at` se actualizó con esas verificaciones.
   Además, ejecutar `downgrade base` con el servidor de desarrollo vivo puede producir `500`
   transitorios por conexiones cacheadas a tablas recreadas; el servidor se recupera al reintentar.
   Recomendación: detener el servidor o usar una BD desechable para los tests de migraciones.

### MENOR

4. **`app/api/v1/routes/auth.py:30` — `summary` no usa la redacción de la spec.**
   La spec indica que `summary` y `description` pasen a «Autentica por nombre de usuario **o** correo
   electrónico y contraseña…». El `description` sí lo cumple; el `summary` es
   `"Iniciar sesion con usuario o correo"`. CA-16 no lo exige y no afecta al contrato; solo a la
   documentación. Sugerencia: alinear el texto (y las tildes) en una limpieza posterior.

### SUGERENCIA

5. **`app/services/auth_service.py:53` — `identifier = (email or username or "").strip()`.**
   Con `email=""` y `username="u"` (inalcanzable vía HTTP porque el schema rechaza el email vacío)
   el identificador del rate limit sería `"u"` aunque la búsqueda use la columna email. Es una
   ambigüedad defensiva, no un fallo funcional. Sugerencia: `identifier = email if email is not None else username`.
6. **`tests/test_db_migrations.py`** conserva la dependencia de la BD de desarrollo migrada. El
   aislamiento por transacción de `db_conn` no cubre los tests que lanzan la CLI de Alembic en
   subprocesos. Una BD temporal por test eliminaría el riesgo residual del hallazgo 1.

## Cobertura CA → test

| CA | Test / evidencia |
|---|---|
| CA-1 | `tests/test_auth_login_email.py::test_ca1_login_by_email_returns_the_exact_login_response` |
| CA-2 | `tests/test_auth_login_email.py::test_ca2_email_is_stripped_and_case_insensitive_but_keeps_stored_value`; `test_auth_login_email_unit.py::test_u2_*` |
| CA-3 | `tests/test_auth_login_email.py::test_ca3_exactly_one_identifier_is_required`; `test_auth_login_email_unit.py::test_u1_*`, `test_u3_*` |
| CA-4 | `tests/test_auth_login_email.py::test_ca4_invalid_email_returns_422_on_the_email_field` (6 casos), `::test_ca4_email_longer_than_255_returns_422_on_the_email_field`, `::test_ca4_well_formed_email_with_wrong_password_is_401` |
| CA-5 | `tests/test_auth_login_email.py::test_ca5_unknown_email_bad_password_and_unknown_username_are_identical` |
| CA-6 | `tests/test_auth_login_email.py::test_ca6_inactive_user_by_email_requires_the_correct_password` |
| CA-7 | `tests/test_auth_login_email.py::test_ca7_last_login_at_is_updated_only_on_a_successful_email_login` |
| CA-8 | `tests/test_auth_login_email.py::test_ca8_response_and_claims_are_identical_for_both_identifiers` |
| CA-9 | `tests/test_auth_login_email.py::test_ca9_username_login_remains_unchanged` |
| CA-10 | `tests/test_auth_login_email.py::test_ca10_email_in_the_username_field_does_not_authenticate_by_email` |
| CA-11 | `tests/test_auth_login_email.py::test_ca11_username_in_the_email_field_is_validated_as_email` |
| CA-12 | `tests/test_auth_rate_limit_email.py::test_ca12_fifth_email_failure_blocks_the_sixth_correct_attempt`, `::test_ca12_email_case_and_padding_variants_share_the_counter` |
| CA-13 | `tests/test_auth_rate_limit_email.py::test_ca13_successful_email_login_resets_its_window`, `::test_ca13_forbidden_and_validation_do_not_consume_email_attempts` |
| CA-14 | `tests/test_auth_rate_limit_email.py::test_ca14_email_and_username_use_independent_counters` |
| CA-15 | `tests/test_auth_logs_email.py::test_ca15_email_login_logs_do_not_leak_identifiers_or_secrets` |
| CA-16 | `tests/test_auth_openapi_email.py::test_ca16_openapi_documents_both_identifiers_and_examples` + comando exacto de la spec |
| CA-17 | `uv run alembic check` + `git status --porcelain alembic/ pyproject.toml uv.lock` + `ruff check`/`format --check` |
| CA-18 | `uv run pytest -q` → **286 passed** |
| U-4 | `tests/test_auth_login_email_unit.py::test_u4_get_user_by_email_is_case_insensitive_and_loads_role` |

Sin CA pendientes de cobertura.

## Cambios en tests realizados por el reviewer

- `tests/test_auth_login.py::test_ca15_missing_fields_and_length_limits`: la aserción de `{}` pasa a
  esperar solo `password` (D-2) y se añade `{"password": ...}` → `422 field="body"` con el mensaje exacto.
- `tests/test_auth_login_email.py` (nuevo): CA-1 a CA-11, integración con PostgreSQL.
- `tests/test_auth_login_email_unit.py` (nuevo): U-1 a U-4.
- `tests/test_auth_rate_limit_email.py` (nuevo): CA-12 a CA-14.
- `tests/test_auth_logs_email.py` (nuevo): CA-15.
- `tests/test_auth_openapi_email.py` (nuevo): CA-16.
- `tests/test_db_catalog.py`, `tests/test_db_constraints.py`, `tests/test_db_migrations.py`,
  `tests/test_users_list.py`: aislamiento frente a las cuentas permanentes (hallazgos 1 y 2).

## Aislamiento y datos

- Los tests de FEAT-005 usan `db_conn`/`db_session` (transacción revertida + savepoints),
  `auth_client`/`auth_client_factory`, `create_auth_user`, `token_for`/`token_payload` y `unique_suffix()`.
- No se usan datos reales ni `sleep`; el rate limiter se reinicia con la fixture `autouse`.
- Las cuentas permanentes `admin` (100) y `medico` (101) no se leen ni se modifican en los tests de
  FEAT-005. El único test que toca la BD global es el de migraciones, mitigado con el fixture de preservación.
- El login de los tests actualiza `last_login_at` dentro de la transacción revertida.

## Riesgos residuales

- **R-1 (D-4)**: dos contadores independientes por cuenta (email y username) permiten 5 + 5 intentos por
  ventana. Documentado y verificado en CA-14; aceptado por la spec.
- **R-2**: el `422` de identificador usa `field="body"`; el cliente debe tratarlo a nivel de formulario.
  Verificado en CA-3.
- **Infraestructura**: los tests de `downgrade base` siguen operando sobre la BD de desarrollo; solo se
  preservan usuarios y médicos. Ver hallazgo 1 y sugerencia 6. Con el servidor de desarrollo vivo,
  el `downgrade` puede dejar conexiones cacheadas y provocar un `500` transitorio hasta reintentar;
  para evitarlo, detener el servidor o usar una BD desechable.
- **Operación**: contraseñas de `admin`/`medico` restauradas a los valores aleatorios originales
  entregados al humano; `MiClaveSegura123` ya no es válida. Los tests de migraciones preservan
  usuarios y médicos, pero siguen operando sobre la BD de desarrollo.
