# Revisión FEAT-006 — CORS configurable para el frontend (localhost:4200)

**Veredicto:** `APPROVED`
**Fecha:** 2026-10-21
**Feature:** FEAT-006 (`docs/features/FEAT-006.md`)
**Reviewer:** agente `reviewer`

## Evidencia final

| Comando | Resultado |
|---|---|
| `uv run pytest -q` | **348 passed**, 0 failed, 0 skipped en 99,82 s (286 previos + **62 nuevos**) |
| `uv run ruff check .` | `All checks passed!` |
| `uv run ruff format --check .` | `109 files already formatted` |
| `uv run alembic check` | `No new upgrade operations detected.` |
| `git status --porcelain alembic/ pyproject.toml uv.lock` | vacío |
| `git status --porcelain app/api app/schemas app/services app/repositories` | vacío |

Sin `BLOCKER` ni `MAJOR`: los 26 CA con aserción tienen test verde y los 3 CA de comando
se verificaron con la salida de arriba. Los dos hallazgos `MINOR` son desviaciones de
validación/forma del mensaje sin impacto funcional ni de seguridad (ver abajo).

## Hallazgos

### BLOCKER / MAJOR

Ninguno.

### MINOR

1. **`app/core/config.py:54` — un JSON que no es array y no empieza por `[` no se detecta como JSON.**
   `_parse_cors_list` solo intenta `json.loads` cuando `raw.startswith("[")`. Por tanto
   `CORS_ALLOWED_ORIGINS="{}"` o `'"solo-un-string"'` caen en el camino coma-separado y fallan
   con `CORS_ALLOWED_ORIGINS contiene un origen invalido`, no con el mensaje
   `JSON`/`lista`/`cadenas` que exige la tabla de errores de configuración de la spec.
   Sigue siendo *fail-closed* (el arranque aborta con `ValidationError` que cita la variable),
   por eso es MINOR y no afecta a CA-3 (que solo cubre el formato JSON array). Evidencia:
   ```text
   '{}' -> ERROR: Value error, CORS_ALLOWED_ORIGINS contiene un origen invalido: '{}'
   '"solo-un-string"' -> ERROR: Value error, CORS_ALLOWED_ORIGINS contiene un origen invalido: '"solo-un-string"'
   ```
   → Se espera que cualquier JSON no-array produzca un mensaje con `JSON`, `lista` o `cadenas`.

2. **`app/core/config.py:86` — se acepta un origen sin host (`http://:4200`).**
   La comprobación usa `not parts.netloc`, y `":4200"` es un netloc no vacío, pero
   `parts.hostname` es `None`. La regla 3 exige que el valor resultante sea
   `http(s)://host[:puerto]`, así que este origen debería ser inválido. Evidencia:
   ```text
   'http://:4200' -> ['http://:4200']
   'http://:80'   -> ['http://:80']
   ```
   Impacto nulo en la práctica (un navegador nunca envía un `Origin` sin host, así que la
   comparación exacta de Starlette nunca coincidiría), pero contradice la validación estricta
   de D-3/R-7. → Añadir `if not parts.hostname: raise invalid` en `_normalize_origin`.

### NIT

3. **`app/core/config.py:106` — `origin.rstrip("/")` elimina *todas* las barras finales.**
   La regla 3 dice «se elimina una barra final»; `http://localhost:4200//` se acepta y se
   normaliza a `http://localhost:4200`. El valor serializado acaba siendo correcto, así que
   no hay impacto; solo es más laxo de lo escrito.
4. **`.opencode/opencode.json:13` — cambio ajeno a la feature en el working tree.**
   El diff añade la configuración MCP de PostgreSQL; no forma parte de FEAT-006 y no debe
   incluirse en el commit de la feature (`git status` lo lista como modificado).

## Cobertura CA → test

Los 62 casos nuevos viven en cuatro archivos y construyen settings/apps aisladas
(`Settings(_env_file=None)` + `monkeypatch`; `create_app()` tras `get_settings.cache_clear()`),
sin mutar el `app` de módulo ni usar `db_session`.

| CA | Test / evidencia |
|---|---|
| CA-1 | `tests/test_cors_settings.py::test_ca1_defaults_in_local` |
| CA-2 | `tests/test_cors_settings.py::test_ca2_comma_separated_format` |
| CA-3 | `tests/test_cors_settings.py::test_ca3_json_array_format`, `::test_ca3_malformed_json_is_a_configuration_error` (2 casos) |
| CA-4 | `tests/test_cors_settings.py::test_ca4_normalization_and_deduplication` |
| CA-5 | `tests/test_cors_settings.py::test_ca5_empty_origin_list_disables_cors` (5 variantes), `::test_ca5_programmatic_empty_list_is_valid` |
| CA-6 | `tests/test_cors_settings.py::test_ca6_invalid_origins_abort_startup` (10 orígenes), `::test_ca6_valid_origin_with_port_is_kept` |
| CA-7 | `tests/test_cors_settings.py::test_ca7_wildcard_is_accepted_outside_production` (4 entornos), `::test_ca7_wildcard_is_rejected_in_production`, `::test_ca7_wildcard_cannot_be_mixed_with_explicit_origins` (local, prod), `::test_ca7_wildcard_is_rejected_in_methods_and_headers` (2) |
| CA-8 | `tests/test_cors_settings.py::test_ca8_unknown_method_is_rejected`, `::test_ca8_empty_methods_rejected_when_origins_are_set`, `::test_ca8_empty_methods_allowed_when_origins_are_empty`, `::test_ca8_methods_are_uppercased_and_deduplicated` |
| CA-9 | `tests/test_cors_settings.py::test_ca9_invalid_header_name_is_rejected`, `::test_ca9_empty_headers_rejected_when_origins_are_set`, `::test_ca9_empty_headers_allowed_when_origins_are_empty`, `::test_ca9_headers_are_canonicalized_and_deduplicated` |
| CA-10 | `tests/test_cors_env_example.py::test_ca10_env_example_documents_the_three_cors_variables`, `::test_ca10_env_example_documents_empty_list_and_production_rule`, `::test_ca10_env_example_loads_with_the_documented_defaults` |
| CA-11 | `tests/test_cors_middleware.py::test_ca11_middleware_order_and_cors_kwargs` |
| CA-12 | `tests/test_cors_middleware.py::test_ca12_simple_request_from_allowed_origin` |
| CA-13 | `tests/test_cors_middleware.py::test_ca13_simple_request_from_disallowed_origin` |
| CA-14 | `tests/test_cors_middleware.py::test_ca14_unauthorized_response_carries_cors_headers` |
| CA-15 | `tests/test_cors_middleware.py::test_ca15_empty_origins_does_not_register_cors`, `::test_ca15_empty_origins_disables_cors_even_for_matching_origin`, `::test_ca15_production_with_empty_origins_still_starts` |
| CA-16 | `tests/test_cors_middleware.py::test_ca16_request_without_origin_has_vary_but_no_cors_headers` |
| CA-17 | `tests/test_cors_preflight.py::test_ca17_allowed_preflight_returns_exact_cors_headers` |
| CA-18 | `tests/test_cors_preflight.py::test_ca18_preflight_from_disallowed_origin` |
| CA-19 | `tests/test_cors_preflight.py::test_ca19_preflight_with_disallowed_method` |
| CA-20 | `tests/test_cors_preflight.py::test_ca20_preflight_with_disallowed_header` |
| CA-21 | `tests/test_cors_preflight.py::test_ca21_preflight_is_logged_without_leaking_origin` |
| CA-22 | `tests/test_cors_middleware.py::test_ca22_expose_headers_are_exactly_the_two_documented` |
| CA-23 | `tests/test_cors_middleware.py::test_ca23_credentials_are_never_allowed` |
| CA-24 | `tests/test_cors_middleware.py::test_ca24_wildcard_in_development` |
| CA-25 | `tests/test_cors_middleware.py::test_ca25_production_with_explicit_origins` |
| CA-26 | `tests/test_cors_preflight.py::test_ca26_preflight_to_protected_route_needs_no_auth_or_database` (`/api/v1/auth/me`, `/api/v1/users`) |
| CA-27 | `uv run pytest -q` → **348 passed**; `ruff check .` → limpio; `ruff format --check .` → 109 files |
| CA-28 | `uv run alembic check` → `No new upgrade operations detected.`; `git status --porcelain alembic/ pyproject.toml uv.lock` → vacío |
| CA-29 | `git status --porcelain app/api app/schemas app/services app/repositories` → vacío; sin rutas de OpenAPI/esquemas modificadas |

Sin CA pendientes de cobertura.

## Cambios en tests realizados por el reviewer

- `tests/test_cors_settings.py` (nuevo, 40 casos): CA-1 a CA-9. Cubre defaults, coma-separado,
  JSON array, JSON mal formado, normalización/dedupe, listas vacías, 10 orígenes inválidos,
  comodín por entorno y mezcla, métodos y cabeceras.
- `tests/test_cors_env_example.py` (nuevo, 3 casos): CA-10, incluida la carga real de
  `.env.example` con `Settings(_env_file=...)`.
- `tests/test_cors_middleware.py` (nuevo, 12 casos): CA-11 a CA-16 y CA-22 a CA-25 (orden y
  kwargs de `app.user_middleware`, simple permitido/no permitido, `401` con CORS, CORS
  deshabilitado en `local` y `prod`, `Vary` sin `Origin`, exposición exacta, credenciales,
  comodín en desarrollo y orígenes explícitos en producción).
- `tests/test_cors_preflight.py` (nuevo, 7 casos): CA-17 a CA-21 y CA-26 (200 `OK`, origen/
  método/cabecera rechazados, log sin fuga de `Origin`/`Authorization`/cookie y preflight a
  rutas protegidas sin token).
- No se modificó ningún test existente; la suite previa sigue en verde sin cambios.

## Aislamiento y datos

- Settings: `Settings(_env_file=None, ...)` y `monkeypatch.setenv`/`delenv`; el fixture
  autouse `isolated_settings` limpia la caché de `get_settings()` al terminar cada test.
- Integración: helper local `build_app(monkeypatch, **env)` que limpia las tres variables CORS,
  fija `ENVIRONMENT`, llama a `get_settings.cache_clear()` y a `create_app()`; el cliente usa
  `httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver")`.
- **No** se usa `db_session` ni `client` (el `app` de módulo) para configuraciones alternativas:
  las rutas ejercitadas (`/health`, `/auth/me` sin token, `OPTIONS` a `/auth/login`, `/auth/me`
  y `/users`) no consultan la base. En `prod` se pasa `JWT_SECRET` de 48 caracteres.
- CA-21 reengancha el handler de `caplog` después de `create_app()` porque `configure_logging()`
  limpia los handlers del root; se retira en `finally` para no filtrar estado entre tests.

## Riesgos residuales

- **R-1 (D-11)**: en `prod`, olvidar `CORS_ALLOWED_ORIGINS` deja el default
  `http://localhost:4200`. No expone dominios reales ni es comodín; aceptado por la spec.
- **R-2 (SUP-5)**: `staging` permite `*`. Si `staging` es internet-facing, endurecer el
  validador (confirmación pendiente del team-lead/humano).
- **R-3 (D-9)**: `Access-Control-Max-Age: 600` puede tardar hasta 10 minutos en reflejar un
  cambio de orígenes en navegadores que cachearon el preflight.
- **R-4**: un `500` no controlado lo genera `ServerErrorMiddleware`, por fuera de CORS; los
  errores de dominio/validación sí llevan cabeceras (CA-14).
- **R-6**: si se configura `CORS_ALLOWED_HEADERS` sin `Authorization`, el preflight del front
  fallará con `400`; los defaults y `.env.example` lo incluyen.
- **R-8**: cualquier test futuro que intente cambiar CORS sobre `from app.main import app`
  fallará; los tests nuevos siguen el patrón `create_app()` + `cache_clear()`.
- Los dos `MINOR` de validación (JSON no-array sin `[`, origen sin host) no bloquean la
  entrega; se recomienda corregirlos en una limpieza posterior de `app/core/config.py`.
