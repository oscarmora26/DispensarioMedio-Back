# FEAT-002 — Autenticación JWT: login, sesión y cambio de contraseña

**Estado:** `approved` (aprobada explícitamente por el humano el 2026-10-21, alcance acotado a autenticación)
**Fecha:** 2026-10-21
**Alcance:** `POST /api/v1/auth/login`, `GET /api/v1/auth/me`, `POST /api/v1/auth/change-password`, emisión/validación de JWT, hash Argon2id, dependencias de autorización reutilizables, rate limiting del login y CLI `create-admin`.
**Fuente de verdad funcional:** `DispensarioMedicoDocs/03-api-rest.md` §2–§5 y §15; `01-requerimientos-y-backlog.md` RF-01, RF-02, RF-19, RNF-07, RNF-08, US-04 y matriz de permisos; `02-arquitectura.md` §9; `06-guia-despliegue.md` §7.3; `08-adrs.md` ADR-003.

## Contexto

FEAT-003 dejó el esquema `roles` / `doctors` / `users` migrado y con los roles `ADMIN` y `MEDICO` sembrados, pero **no existe ninguna forma de obtener un token ni de proteger un endpoint**. Sin autenticación, ninguna feature de negocio puede implementar autorización: `visits`, `patients`, `medications` y todas las rutas de la matriz de permisos dependen de saber quién llama y con qué rol.

Esta feature construye la única puerta de entrada al sistema (login), el endpoint de identidad (`/me`) que el frontend usa para restaurar sesión al recargar, y el cambio de contraseña propio (RF-19). Además entrega las dependencias `get_current_user` y `require_role(...)` que FEAT-004 en adelante reutilizarán sin volver a tocar seguridad.

El alcance está acotado por decisión humana: **sin refresh tokens, sin revocación, sin recuperación por correo y sin MFA**. El token es un JWT HS256 de 30 minutos configurables y el servidor no guarda sesión.

## Alcance

- `pyproject.toml` y `uv.lock`: dependencias `pyjwt` y `pwdlib[argon2]`.
- `app/core/config.py`: settings `JWT_SECRET`, `JWT_ALGORITHM`, `JWT_EXPIRE_MINUTES`, `LOGIN_RATE_LIMIT_MAX_ATTEMPTS`, `LOGIN_RATE_LIMIT_WINDOW_SECONDS`, con validación de secreto en producción.
- `.env.example`: las cinco claves anteriores y `ADMIN_PASSWORD` documentada (comentada).
- `app/core/security.py`: `hash_password`, `verify_password` (Argon2id vía `pwdlib`) y `create_access_token` / `decode_access_token` (PyJWT, HS256). `create_access_token` acepta `secret` y `expires_minutes` opcionales; `decode_access_token` acepta `secret` opcional. Por defecto leen `get_settings()` en el momento de la llamada.
- `app/core/rate_limit.py`: `LoginRateLimiter` en memoria (IP + usuario), reiniciable.
- `app/core/exceptions.py`: envelope de error `{detail, code, errors}`, subclases `UnauthorizedError`, `ForbiddenError`, `RateLimitError`, soporte de `headers` y handler para `RequestValidationError`.
- `app/repositories/user_repository.py`: `get_user_by_username`, `get_user_by_id` (con `selectinload(User.role)`).
- `app/services/auth_service.py`: caso de uso `login` y `change_password`.
- `app/services/user_service.py`: `create_admin` (provisionamiento del administrador inicial; FEAT-004 lo extiende).
- `app/api/deps.py`: `get_current_user`, alias `CurrentUser`, `require_role(...)`.
- `app/schemas/auth.py`: `LoginRequest`, `LoginUser`, `LoginResponse`, `MeResponse`, `ChangePasswordRequest`, `MessageResponse`.
- `app/api/v1/routes/auth.py` y registro en `app/api/v1/router.py`.
- `app/cli.py`: `python -m app.cli create-admin --username ... --email ...`.

### Estructura de archivos resultante

```text
app/
├── cli.py                          # nuevo
├── api/
│   ├── deps.py                     # nuevo
│   └── v1/
│       ├── router.py               # modificado: incluye auth
│       └── routes/auth.py          # nuevo
├── core/
│   ├── config.py                   # modificado: settings de JWT y rate limit
│   ├── exceptions.py               # modificado: envelope {detail, code, errors}
│   ├── rate_limit.py               # nuevo
│   └── security.py                 # nuevo
├── repositories/user_repository.py # nuevo
├── schemas/auth.py                 # nuevo
└── services/
    ├── auth_service.py             # nuevo
    └── user_service.py             # nuevo
```

## Fuera de alcance

- **FEAT-004**: `GET/POST/PUT/DELETE /users`, `POST /users/{id}/reset-password`, asignación de roles y `doctor_id` desde la API.
- Refresh tokens, rotación, revocación/blacklist, `logout` en servidor, recuperación de contraseña por correo, MFA, bloqueo permanente de cuenta.
- CORS y `FRONTEND_ORIGIN` (el contrato del §15 se cumple en una feature de despliegue).
- Cambios en Docker, `docker-compose.yml`, `Dockerfile`, `alembic/` y en el esquema de base de datos: `users`, `roles` y `doctors` ya existen y **no se modifican**.
- Escritura en `audit_log`: la tabla sigue vacía; el evento `LOGIN` se auditará en una feature posterior.
- Envelope uniforme para `500` y para errores generados por el framework (`405`, `404` de ruta). Ver D-4.
- Frontend Angular (guards, interceptor, `sessionStorage`).
- Política de contraseñas más allá del mínimo de 8 caracteres (complejidad, caducidad, historial).

## Reglas de negocio

### Identidad y login

1. `users.username` y `users.email` son `CITEXT`: la comparación es case-insensitive en la base. El valor almacenado **se conserva tal cual** se insertó; no se normaliza a minúsculas al crear ni al autenticar.
2. El login se resuelve por `username`; `email` no sirve como credencial. `username` se recorta (`strip`) antes de buscar. El `password` **no** se recorta: los espacios son parte de la contraseña.
3. Usuario activo + contraseña correcta ⇒ `200` con token. La contraseña se verifica con Argon2id (`pwdlib`).
4. Contraseña incorrecta o usuario inexistente ⇒ `401` con **el mismo cuerpo y mensaje** en ambos casos (`invalid_credentials`): no se distingue si el usuario existe.
5. Usuario inactivo: primero se verifica la contraseña y después el estado. Contraseña correcta + `is_active = false` ⇒ `403` (`inactive_user`). Contraseña incorrecta sobre un usuario inactivo ⇒ `401` (y cuenta como fallo de rate limit). Justificación en D-2.
6. Tras un login exitoso se actualiza `users.last_login_at` con la hora UTC del servidor. No se actualiza en intentos fallidos, ni en `GET /auth/me`, ni en `change-password`. (Supuesto SUP-1: la columna existe, pero el documento de BD no define cuándo se refresca.)
7. Un fallo de verificación por hash corrupto o de formato desconocido (`UnknownHashError` de `pwdlib`) se trata como contraseña incorrecta (`401`), nunca como `500`.

### Token

8. El token es un JWT firmado con HS256 y el secreto `JWT_SECRET`. Claims exactos: `sub` (**string** con el `id` del usuario), `role` (nombre del rol), `doctor_id` (entero o `null`), `iat` y `exp` (epoch UTC, enteros).
9. `exp - iat == JWT_EXPIRE_MINUTES * 60`. `expires_in` de la respuesta es `JWT_EXPIRE_MINUTES * 60` (1800 con el valor por defecto).
10. La respuesta usa `token_type: "bearer"` (minúsculas, literal del §3).
11. Un token se considera válido solo si: la firma es correcta, no está expirado, contiene `sub`, `iat` y `exp`, `sub` se convierte a entero y el usuario existe. Cualquier otra cosa ⇒ `401`.
12. **El claim `role` no es fuente de autorización.** En cada request el rol y el `doctor_id` se leen de la base (`users.role_id` → `roles.name`, `users.doctor_id`). Un token manipulado o antiguo con otro rol no eleva privilegios.
13. Un usuario desactivado después de emitirse el token recibe `403` (`inactive_user`) en cualquier endpoint autenticado en la siguiente request.
14. No hay revocación: un token sigue siendo válido hasta `exp` aunque el usuario cambie la contraseña (limitación aceptada, D-3 y ADR-003).

### Cambio de contraseña

15. Requiere autenticación (Bearer). Cualquier rol autenticado puede cambiar **su propia** contraseña; no recibe `id` ni `username` por parámetro.
16. Orden de evaluación: (a) autenticación `401`/`403`; (b) validación de payload `422`; (c) contraseña actual incorrecta ⇒ `400`; (d) nueva igual a la actual ⇒ `400`; (e) hash + `UPDATE`.
17. `current_password` debe coincidir con el hash almacenado. Si no ⇒ `400` `invalid_current_password`. No es `401`: el llamante **sí** está autenticado y un `401` haría que el frontend cerrara la sesión (US-13: interceptor 401 → logout), lo que sería incorrecto para un error de formulario.
18. `new_password` debe tener entre 8 y 128 caracteres (mínimo por decisión humana; máximo técnico para acotar el coste de Argon2). `current_password` y `confirm_password` admiten 1..128.
19. `new_password != confirm_password` ⇒ `422` (`validation_error`) con `field = "confirm_password"` y mensaje `Las contraseñas no coinciden`. Se valida en Pydantic porque depende solo del payload; el validador de `confirm_password` lee `info.data["new_password"]`, por lo que `new_password` se declara **antes** en el modelo.
20. `new_password` idéntica a `current_password` ⇒ `400` `password_unchanged`. La comparación es en texto plano: Argon2 usa sal aleatoria, así que comparar hashes no sirve. Caso distinto por mayúsculas o espacios ⇒ se acepta.
21. Tras el cambio, la contraseña anterior deja de autenticar y los tokens ya emitidos siguen funcionando (D-3).

### Rate limiting del login

22. Ventana deslizante **en memoria del proceso**, clave `(IP del cliente, username normalizado)`, donde normalizado = `strip().casefold()`. Se permiten `LOGIN_RATE_LIMIT_MAX_ATTEMPTS` fallos (5) dentro de `LOGIN_RATE_LIMIT_WINDOW_SECONDS` (900).
23. El chequeo ocurre **antes** de consultar la base y de verificar la contraseña: el intento número 6 dentro de la ventana devuelve `429` aunque la contraseña sea correcta.
24. `429` incluye cabecera `Retry-After` (segundos enteros, mínimo 1) = tiempo restante hasta que el fallo más antiguo salga de la ventana.
25. Cuentan como fallo: `401` de credenciales inválidas. **No** cuentan ni reinician la ventana: `403` de usuario inactivo, `422` de validación ni `429`. Un login exitoso borra la clave (ventana reiniciada).
26. La IP se toma de `request.client.host`. **No** se lee `X-Forwarded-For` (cabecera falsificable). Si no hay cliente, se usa `"unknown"`.
27. Limpieza: en cada consulta/registro se descartan los timestamps fuera de la ventana de esa clave y la clave se elimina si queda vacía. Si el mapa supera 1024 claves, se purgan todas las claves sin fallos vigentes. El estado se puede reiniciar en caliente (`reset`, `reset_all`, `configure`).
28. Cada proceso tiene su propio contador (D-5). Un reinicio del proceso lo borra.

### Errores (contrato transversal)

29. Todo error de negocio o de validación responde `{"detail": str, "code": str, "errors": [{"field": str, "message": str}]}`; `errors` es `[]` cuando no aplica. El campo `details` desaparece.
30. `422` de Pydantic se normaliza a ese envelope con `code = "validation_error"`, `detail = "Datos inválidos"` y un ítem por error; `field` es el `loc` sin el prefijo `body` unido por puntos (`"confirm_password"`, `"user.username"`), o `"body"` si no queda nada.
31. Todo `401` incluye `WWW-Authenticate: Bearer`. Todo `429` incluye `Retry-After`.
32. Códigos estables (`code`, no confundir con el status HTTP): `invalid_credentials`, `inactive_user`, `not_authenticated`, `invalid_token`, `forbidden`, `rate_limited`, `validation_error`, `invalid_current_password`, `password_unchanged`, `user_exists`, `admin_role_missing`.
33. Catálogo de status HTTP de la feature: `200`, `400`, `401`, `403`, `422` y `429`. El `500` es el fallback del framework y no se produce por diseño (D-4). No se añaden otros.

### Registro (logs)

34. Nunca se registran contraseñas, hashes, tokens ni el cuerpo de `/auth/login`. Tampoco `username` ni `email` (el `email` está en la lista de redacción y el `username` no; la responsabilidad es del código).
35. Eventos permitidos: `auth.login_succeeded` (`user_id`, `role`), `auth.login_failed` (`reason` ∈ `unknown_user|bad_password|inactive`), `auth.rate_limited` (sin clave ni IP), `auth.password_changed` (`user_id`). El resto del rastro lo aporta `request.completed` del middleware (método, ruta, status, duración, `client_ip`).
36. El CLI escribe el resultado en stdout y los errores en stderr; nunca imprime la contraseña ni el hash.

### CLI `create-admin`

37. Invocación: `python -m app.cli create-admin --username <u> --email <e>`; equivalente en Compose según `06-guia-despliegue.md` §7.3.
38. Contraseña: si `ADMIN_PASSWORD` existe y no está vacía se usa esa; si no, se pide con `getpass` dos veces (contraseña y confirmación) y deben coincidir. En ningún caso se acepta por argumento de línea de comandos.
39. Validaciones: `username` recortado y con `^\S{4,50}$`; `email` recortado, máximo 255 y con forma `^[^@\s]+@[^@\s]+\.[^@\s]+$`; contraseña de 8..128. Violación ⇒ salida `2`, mensaje en stderr, sin tocar la base.
40. Rol: se busca `roles.name = 'ADMIN'` (sembrado por FEAT-003). Si no existe ⇒ salida `3` con instrucción de ejecutar `alembic upgrade head`.
41. El usuario se crea con `is_active = true`, `doctor_id = NULL`, `last_login_at = NULL` y `password_hash` Argon2id.
42. Duplicado (mismo `username` **o** mismo `email`, comparación CITEXT) ⇒ salida `1`, mensaje `El usuario ya existe`, sin modificar la fila existente (no es un *upsert*: no se resetea la contraseña por accidente). La comprobación previa más la captura de `IntegrityError` cubren la carrera.
43. Códigos de salida: `0` creado, `1` duplicado, `2` entrada inválida (incluye `EOFError` al leer la contraseña), `3` rol `ADMIN` ausente, `4` error de base de datos.
44. El CLI no pasa por HTTP ni por el pipeline de logs; usa `SessionLocal`, hace `commit()` al final y dispone el engine al terminar. La firma es `async def main(argv: Sequence[str] | None = None) -> int` (por eso los tests la invocan con `await`); captura el `SystemExit` de `argparse` y devuelve su código, de modo que `--help` y los argumentos inválidos son verificables sin terminar el proceso.

## Supuestos

| ID | Supuesto | Justificación |
|---|---|---|
| **SUP-1** | `last_login_at` se actualiza solo tras un login exitoso. | La columna está documentada como "Último inicio de sesión" sin semántica de actualización; es el único uso coherente y no requiere cambios de esquema. |
| **SUP-2** | La validación estricta de `JWT_SECRET` aplica solo a `environment = "prod"`. | `local`, `test`, `dev` y `staging` deben poder arrancar con el placeholder para no romper la suite ni los entornos internos. Se reevalúa si `staging` pasa a tener datos reales. |

## Decisiones técnicas y desviaciones documentadas

| ID | Decisión | Justificación / impacto |
|---|---|---|
| **D-1** | Envelope de error `{detail, code, errors}`. Se sustituye `details: dict` por `errors: list[{"field","message"}]` y se añade un handler de `RequestValidationError`. | El §2 del contrato exige `detail` + `errors`; hoy `app/core/exceptions.py` devuelve `{detail, code, details}` y FastAPI devuelve `{"detail": [...]}` en los `422`. **Ningún test actual depende del formato** (verificado: `details` solo aparece como variable local en `tests/test_db_constraints.py`). Es un cambio de contrato para todas las features futuras: deben usar `errors`, no `details`. |
| **D-2** | El `403` de inactivo se evalúa **después** de verificar la contraseña. | Evita que un atacante descubra cuentas inactivas sin conocer la contraseña, y mantiene `401` genérico para credenciales malas. El contrato ("usuario inactivo: 403") se cumple con credenciales correctas. |
| **D-3** | Sin revocación: cambiar la contraseña no invalida tokens emitidos. | ADR-003 y decisión humana (sin refresh/blacklist). Mitigado por `exp` de 30 min. Documentado como limitación aceptada y verificado en CA-29. |
| **D-4** | `500` y errores de framework (`405`, `404` de ruta) **no** se envuelven. | No se enmascaran tracebacks (los tests y los logs necesitan la excepción) y está fuera del alcance. El §2 describe el formato de errores de negocio, no el fallback del servidor. |
| **D-5** | Rate limit por proceso, no distribuido. | Decisión humana explícita. Con N workers el límite efectivo es N×5. Se documenta en R-2. |
| **D-6** | `JWT_SECRET` con placeholder por defecto aceptado en `local`/`test` y rechazado en `prod`. | Evita romper imports y la suite (que corre sin `.env`) y a la vez impide arrancar producción con el secreto de ejemplo. Secreto mínimo 32 caracteres. |
| **D-7** | `JWT_ALGORITHM` restringido a `HS256` con `Literal`. | Decisión humana y ADR-003; un valor distinto falla al construir `Settings`, no en tiempo de request. |
| **D-8** | En `JWT`, `sub` es `str(user.id)`. | PyJWT ≥ 2.10 rechaza `sub` no string (`InvalidSubjectError`). Se convierte a `int` al decodificar y un `sub` no numérico es `401`. |
| **D-9** | El servicio expone `limiter` opcional y `get_login_rate_limiter()` devuelve un singleton configurable. | Permite tests unitarios con reloj falso y tests de integración reiniciando el estado, sin `sleep` ni procesos aparte. |
| **D-10** | `last_login_at` se actualiza en el login exitoso. | Supuesto SUP-1; la columna está documentada como "Último inicio de sesión" pero sin semántica de actualización. |

## Modelo de datos

**No hay cambios de esquema ni migraciones nuevas.** `uv run alembic check` debe seguir imprimiendo `No new upgrade operations detected.` y `git status --porcelain alembic/` debe quedar vacío.

Columnas utilizadas (ya existentes, FEAT-003):

| Tabla | Columna | Uso en esta feature |
|---|---|---|
| `users` | `id` (`BIGINT` identity) | `sub` del token y `user.id` de las respuestas. |
| `users` | `username` (`CITEXT`, `uq_users_username`) | Búsqueda del login, case-insensitive; el índice único resuelve la consulta. |
| `users` | `email` (`CITEXT`) | Solo lectura para las respuestas y detección de duplicados en el CLI. |
| `users` | `password_hash` (`VARCHAR(255)`) | Verificación y actualización. El hash Argon2id codificado ocupa ~97 caracteres. |
| `users` | `role_id` → `roles.name` | Rol efectivo, leído en cada request. |
| `users` | `doctor_id` (`BIGINT`, nullable) | Claim `doctor_id` y campo de las respuestas; `NULL` para `ADMIN`. |
| `users` | `is_active` (`BOOLEAN`) | `403` inmediato si es `false`. |
| `users` | `last_login_at` (`TIMESTAMPTZ`, nullable) | Se escribe solo en login exitoso. |
| `users` | `updated_at` (`TIMESTAMPTZ`) | Se refresca por `onupdate` del ORM al cambiar la contraseña. |

Consultas (SQLAlchemy 2.x, `select()`):

```python
# login: CITEXT hace la comparación case-insensitive, no se usa lower()
select(User).options(selectinload(User.role)).where(User.username == username)

# get_current_user / me
select(User).options(selectinload(User.role)).where(User.id == user_id)

# CLI: búsqueda de rol y de duplicados
select(Role).where(Role.name == "ADMIN")
select(User).where(or_(User.username == username, User.email == email))
```

`lazy="raise"` en `User.role` obliga a `selectinload`; sin él, `user.role.name` lanza `MissingGreenlet`/`InvalidRequestError`.

## Contrato de API

Prefijo `/api/v1` (de `settings.api_v1_prefix`), `Content-Type: application/json`, fechas ISO 8601 con zona. Errores con el envelope de la regla 29.

### `POST /api/v1/auth/login`

- **Auth:** pública (sin Bearer). Si llega un `Authorization` se ignora.
- **Body** (`LoginRequest`):

| Campo | Tipo | Obligatorio | Restricciones |
|---|---|---|---|
| `username` | `str` | Sí | `strip`, 1..50 caracteres |
| `password` | `str` | Sí | 1..128 caracteres, sin `strip` |

```json
{ "username": "admin", "password": "MiClaveSegura123" }
```

- **Respuesta `200`** (`LoginResponse`):

```json
{
  "access_token": "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9...",
  "token_type": "bearer",
  "expires_in": 1800,
  "user": {
    "id": 1,
    "username": "admin",
    "email": "admin@dispensario.local",
    "role": "ADMIN",
    "doctor_id": null
  }
}
```

`user` es `LoginUser` y tiene **exactamente** `id`, `username`, `email`, `role`, `doctor_id`. **No incluye `is_active`** (el §3 no lo lista; `is_active` es exclusivo de `/me`). `role` es `"ADMIN"` o `"MEDICO"`. `doctor_id` es `null` para `ADMIN`.

- **Errores:**

| HTTP | `code` | Cuándo | `detail` |
|---|---|---|---|
| `401` | `invalid_credentials` | usuario inexistente o contraseña incorrecta | `Usuario o contraseña incorrectos` |
| `403` | `inactive_user` | credenciales correctas y `is_active = false` | `El usuario está inactivo` |
| `422` | `validation_error` | falta un campo, `username` vacío o excede 50, `password` excede 128 | `Datos inválidos` |
| `429` | `rate_limited` | 6.º intento dentro de la ventana; cabecera `Retry-After` | `Demasiados intentos fallidos. Intente de nuevo más tarde` |

Ejemplo de `422`:

```json
{
  "detail": "Datos inválidos",
  "code": "validation_error",
  "errors": [{ "field": "password", "message": "Field required" }]
}
```

El `message` de los errores de Pydantic es el texto original del validador (inglés); el mensaje propio de `confirm_password` sí es español.

### `GET /api/v1/auth/me`

- **Auth:** `Authorization: Bearer <token>` obligatorio. Roles: `ADMIN` y `MEDICO`.
- **Respuesta `200`** (`MeResponse`):

```json
{
  "id": 12,
  "username": "aperez",
  "email": "aperez@dispensario.local",
  "role": "MEDICO",
  "doctor_id": 5,
  "is_active": true
}
```

Campos exactos: `id`, `username`, `email`, `role`, `doctor_id`, `is_active`. Los datos salen de la base en esta misma request (regla 12).

- **Errores:**

| HTTP | `code` | Cuándo |
|---|---|---|
| `401` | `not_authenticated` | sin cabecera `Authorization` o esquema distinto de `Bearer` (incluye `WWW-Authenticate: Bearer`) |
| `401` | `invalid_token` | firma inválida, token expirado, claims `sub`/`iat`/`exp` ausentes, `sub` no numérico o usuario inexistente |
| `403` | `inactive_user` | el usuario del token ya no está activo |

### `POST /api/v1/auth/change-password`

- **Auth:** `Authorization: Bearer <token>` obligatorio. Roles: `ADMIN` y `MEDICO`. Opera siempre sobre el usuario del token.
- **Body** (`ChangePasswordRequest`):

| Campo | Tipo | Obligatorio | Restricciones |
|---|---|---|---|
| `current_password` | `str` | Sí | 1..128 |
| `new_password` | `str` | Sí | 8..128 |
| `confirm_password` | `str` | Sí | 1..128, debe ser igual a `new_password` |

```json
{
  "current_password": "MiClaveActual123",
  "new_password": "MiClaveNueva456",
  "confirm_password": "MiClaveNueva456"
}
```

- **Respuesta `200`** (`MessageResponse`):

```json
{ "message": "Contraseña actualizada correctamente" }
```

- **Errores:**

| HTTP | `code` | Cuándo |
|---|---|---|
| `400` | `invalid_current_password` | `current_password` no coincide con el hash |
| `400` | `password_unchanged` | `new_password` es idéntica a `current_password` |
| `401` | `not_authenticated` / `invalid_token` | igual que `/me` |
| `403` | `inactive_user` | usuario inactivo |
| `422` | `validation_error` | campos faltantes, `new_password` < 8, `confirm_password` distinto (`field = "confirm_password"`) |

Ejemplo del `422` de confirmación:

```json
{
  "detail": "Datos inválidos",
  "code": "validation_error",
  "errors": [{ "field": "confirm_password", "message": "Las contraseñas no coinciden" }]
}
```

### Contrato de las dependencias reutilizables

```python
# app/api/deps.py
bearer_scheme = HTTPBearer(auto_error=False, scheme_name="BearerAuth")


async def get_current_user(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer_scheme)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> User: ...


CurrentUser = Annotated[User, Depends(get_current_user)]


def require_role(*roles: str) -> Callable[..., Awaitable[User]]: ...
```

- `get_current_user` devuelve el `User` con `role` cargado (`selectinload`) o lanza `UnauthorizedError` / `ForbiddenError`.
- `require_role("ADMIN")` devuelve una dependencia que reutiliza `get_current_user` y compara `user.role.name` (de la base) contra los roles permitidos; si no coincide ⇒ `403` `forbidden` (`No tiene permisos para realizar esta operación`).
- `require_role()` sin argumentos o con un rol fuera de `{"ADMIN","MEDICO"}` lanza `ValueError` al construir la dependencia (error de programación, no HTTP).
- Uso previsto por features posteriores: `user: Annotated[User, Depends(require_role("ADMIN"))]`.

### OpenAPI

Cada endpoint declara `summary`, `description`, `response_model`, `responses` con `401`, `403`, `422` y (`login`) `429`, y el tag `auth`. `HTTPBearer` aporta el esquema `BearerAuth` a Swagger.

## Criterios de aceptación

Convención: `[shell]` = comando ejecutable con salida esperada; `[test]` = aserción exacta que el reviewer implementa en `tests/**`. Los comandos se ejecutan desde la raíz del repo con la base migrada (`uv run alembic upgrade head`).

### Configuración y dependencias

- [ ] **CA-1** `[shell]` Las cinco claves nuevas están en `.env.example` y ningún secreto real está en el repo.

  ```powershell
  uv run python -c "from pathlib import Path; t=Path('.env.example').read_text(encoding='utf-8'); ks=['JWT_SECRET=','JWT_ALGORITHM=','JWT_EXPIRE_MINUTES=','LOGIN_RATE_LIMIT_MAX_ATTEMPTS=','LOGIN_RATE_LIMIT_WINDOW_SECONDS=']; print([k for k in ks if k not in t])"
  ```

  imprime `[]`. Además `uv run python -c "from pathlib import Path; print([str(p) for p in Path('app').rglob('*.py') if 'dev-only-insecure' in p.read_text(encoding='utf-8')])"` imprime exactamente `['app\\core\\config.py']` (el placeholder solo existe como constante por defecto).

- [ ] **CA-2** `[shell]` Defaults:

  ```powershell
  uv run python -c "from app.core.config import Settings; s=Settings(_env_file=None); print(s.jwt_algorithm, s.jwt_expire_minutes, s.login_rate_limit_max_attempts, s.login_rate_limit_window_seconds)"
  ```

  imprime `HS256 30 5 900`.

- [ ] **CA-3** `[shell]` `prod` rechaza el secreto de ejemplo o uno corto; `local` y `test` arrancan sin `.env`.

  ```powershell
  $env:ENVIRONMENT='prod'; uv run python -c "from app.core.config import Settings; Settings(_env_file=None)"
  ```

  termina con código distinto de 0 y el mensaje contiene `JWT_SECRET`. Con `$env:JWT_SECRET` de 48 caracteres aleatorios, el mismo comando termina con código 0. `uv run python -c "from app.core.config import Settings; print(Settings(_env_file=None, environment='test').jwt_expire_minutes)"` imprime `30` sin variables extra. `$env:JWT_ALGORITHM='RS256'` provoca error de validación (solo se admite `HS256`).

- [ ] **CA-4** `[shell]` Dependencias resueltas y Argon2id operativo (el implementer actualiza `uv.lock` en el mismo cambio; `--locked` falla si no lo hizo):

  ```powershell
  uv sync --locked
  uv run python -c "from importlib.metadata import version; print(version('pwdlib'), version('pyjwt'))"
  uv run python -c "from app.core.security import hash_password; print(hash_password('x')[:9])"
  ```

  `uv sync --locked` sale con 0, se imprimen ambas versiones y el prefijo es `$argon2id`.

### Primitivas de seguridad

- [ ] **CA-5** `[shell]` Hash con sal y verificación:

  ```powershell
  uv run python -c "from app.core.security import hash_password as h, verify_password as v; x=h('Secreta123'); d=chr(36); print(x.startswith(d+'argon2id'+d), v('Secreta123',x), v('otra',x), x!=h('Secreta123'))"
  ```

  imprime `True True False True` (dos hashes de la misma contraseña son distintos).

- [ ] **CA-6** `[shell]` Round-trip del token con los claims exactos:

  ```powershell
  uv run python -c "from app.core.security import create_access_token as c, decode_access_token as d; t,e=c(subject='7',role='MEDICO',doctor_id=5,expires_minutes=30,secret='s'*32); p=d(t,secret='s'*32); print(e, sorted(p), p['sub'], p['role'], p['doctor_id'], p['exp']-p['iat'])"
  ```

  imprime `1800 ['doctor_id', 'exp', 'iat', 'role', 'sub'] 7 MEDICO 5 1800`.

- [ ] **CA-7** `[test]` `decode_access_token` lanza `jwt.ExpiredSignatureError` para un token con `expires_minutes=-1` y `jwt.InvalidTokenError` para: firma con otro secreto, último carácter alterado, y token sin claim `exp` (firmado a mano con PyJWT).

- [ ] **CA-8** `[test]` `verify_password("x", "$2b$12$hash-invalido")` devuelve `False` y **no** propaga `UnknownHashError`.

- [ ] **CA-9** `[test]` `create_access_token(..., expires_minutes=45)` devuelve `expires_in == 2700` y `exp - iat == 2700`; el claim `sub` decodificado es `str` (no `int` aunque el `id` sea numérico).

### Login

- [ ] **CA-10** `[test]` Login correcto (`ADMIN`): `200`, claves de nivel superior exactamente `{access_token, token_type, expires_in, user}`, `token_type == "bearer"`, `expires_in == 1800`, `access_token.count(".") == 2`, claves de `user` exactamente `{id, username, email, role, doctor_id}`, `role == "ADMIN"` y `doctor_id is None` (sin `is_active`).

- [ ] **CA-11** `[test]` Login de `MEDICO` con `doctor_id` devuelve `doctor_id` igual al de la base.

- [ ] **CA-12** `[test]` Con usuario `admin` en base, `{"username": "AdMiN"}` autentica; la respuesta devuelve `username == "admin"` (valor almacenado, sin normalizar). Con `{"username": " admin "}` también autentica (`strip`).

- [ ] **CA-13** `[test]` Contraseña incorrecta ⇒ `401`, claves exactas `{detail, code, errors}`, `code == "invalid_credentials"`, `errors == []`. Usuario inexistente ⇒ cuerpo **idéntico byte a byte** al anterior. No existe la clave `details` en ninguna de las dos respuestas.

- [ ] **CA-14** `[test]` Usuario inactivo con contraseña correcta ⇒ `403` `inactive_user` y `last_login_at` sigue `NULL`. Usuario inactivo con contraseña incorrecta ⇒ `401` `invalid_credentials`.

- [ ] **CA-15** `[test]` Body `{"username": "admin"}` (sin `password`) ⇒ `422`, `code == "validation_error"`, `errors` contiene un ítem con `field == "password"`. Body `{}` ⇒ `422` con dos ítems (`password`, `username`). `username` de 51 caracteres ⇒ `422` con `field == "username"`.

- [ ] **CA-16** `[test]` Antes del login `last_login_at IS NULL`; tras un `200` la columna no es `NULL` y está a menos de 60 s del `now()` de la base. Tras un `401`, `GET /me` y `change-password`, la columna **no** cambia (se compara el valor exacto antes y después).

- [ ] **CA-17** `[test]` `JWT_EXPIRE_MINUTES` se respeta de punta a punta: con `monkeypatch.setenv("JWT_EXPIRE_MINUTES", "45")` y `get_settings.cache_clear()`, el login devuelve `expires_in == 2700` y el `exp - iat` del token decodificado es `2700`.

### Sesión y autorización

- [ ] **CA-18** `[test]` `GET /me` con token válido de `MEDICO`: `200` y claves exactamente `{id, username, email, role, doctor_id, is_active}` con `role == "MEDICO"`, `doctor_id` correcto e `is_active is True`.

- [ ] **CA-19** `[test]` `GET /me` sin cabecera ⇒ `401`, `code == "not_authenticated"`, `WWW-Authenticate` empieza por `Bearer`. Con `Authorization: Basic abc` ⇒ igual.

- [ ] **CA-20** `[test]` `GET /me` con `code == "invalid_token"` (`401`) en estos casos, todos con `WWW-Authenticate: Bearer`: token basura (`"abc.def.ghi"`), token firmado con otro secreto, token expirado (`expires_minutes=-1`), token sin `exp` (firmado a mano), token con `sub="abc"`, y token válido de un usuario borrado de la base dentro de la misma transacción.

- [ ] **CA-21** `[test]` Con un token emitido antes de desactivar al usuario, la siguiente request a `/me` devuelve `403` `inactive_user` (sin volver a autenticarse). Reactivarlo devuelve `200` con el mismo token.

- [ ] **CA-22** `[test]` El claim `role` no se usa: un token con `subject` de un `MEDICO` y `role="ADMIN"` devuelve `role == "MEDICO"` en `/me`. `require_role("ADMIN")` con ese usuario lanza `ForbiddenError` (`403`, `code == "forbidden"`) y con un `ADMIN` devuelve el usuario. `require_role()` y `require_role("SUPERVISOR")` lanzan `ValueError`.

### Cambio de contraseña

- [ ] **CA-23** `[test]` Cambio correcto ⇒ `200` y cuerpo exactamente `{"message": "Contraseña actualizada correctamente"}`. Después: login con la contraseña antigua ⇒ `401`; login con la nueva ⇒ `200`. En base, `password_hash` es distinto del anterior y empieza por `$argon2id$`.

- [ ] **CA-24** `[test]` `current_password` incorrecta ⇒ `400` `invalid_current_password` y el `password_hash` **no** cambia (el login con la contraseña antigua sigue devolviendo `200`).

- [ ] **CA-25** `[test]` `new_password == current_password` (con `confirm_password` igual) ⇒ `400` `password_unchanged` y hash intacto.

- [ ] **CA-26** `[test]` `new_password != confirm_password` ⇒ `422`, `code == "validation_error"`, `errors == [{"field": "confirm_password", "message": "Las contraseñas no coinciden"}]` y hash intacto.

- [ ] **CA-27** `[test]` `new_password` de 7 caracteres ⇒ `422` con `field == "new_password"`; de exactamente 8 ⇒ `200`. `current_password` de 129 caracteres ⇒ `422`.

- [ ] **CA-28** `[test]` `change-password` sin token ⇒ `401` y hash intacto; con token de usuario inactivo ⇒ `403` y hash intacto.

- [ ] **CA-29** `[test]` El token usado para cambiar la contraseña sigue siendo válido después (`GET /me` ⇒ `200`), y un segundo cambio con la contraseña ya actualizada funciona. Limitación D-3 verificada explícitamente.

### Rate limiting

- [ ] **CA-30** `[test]` Con el limiter reiniciado y `max_attempts=5`: cinco `401` consecutivos; el sexto intento **con la contraseña correcta** devuelve `429`, `code == "rate_limited"`, `errors == []`, `Retry-After` presente y `1 <= int(Retry-After) <= 900`, y `last_login_at` sigue `NULL`.

- [ ] **CA-31** `[test]` Aislamiento y reseteo: (a) 5 fallos del usuario A no bloquean al usuario B desde la misma IP; (b) 4 fallos + login correcto + 4 fallos ⇒ el siguiente intento correcto devuelve `200` (el éxito reinició); (c) el `403` de inactivo y el `422` de validación no incrementan el contador; (d) `"Admin"`, `"admin"` y `" ADMIN "` comparten contador.

- [ ] **CA-32** `[test]` Unitarias de `LoginRateLimiter` con reloj falso: `retry_after` es `None` con 4 fallos y `>= 1` con 5; al avanzar el reloj `window_seconds + 1` la clave queda libre y `len(limiter) == 0`; `reset(key)` libera solo esa clave; `reset_all()` vacía todo; con 1100 claves creadas en `t=0`, tras avanzar la ventana y registrar un fallo nuevo, `len(limiter) == 1` (purga). `build_key("1.2.3.4", " Admin ") == build_key("1.2.3.4", "admin")`.

- [ ] **CA-33** `[test]` `get_login_rate_limiter()` devuelve siempre la misma instancia; `configure(max_attempts=2, window_seconds=60)` reinicia el estado y el endpoint devuelve `429` en el tercer intento. Un `X-Forwarded-For: 10.0.0.9` distinto entre requests **no** cambia la clave: los fallos siguen acumulándose sobre `request.client.host` (ASGITransport con `client=("127.0.0.1", 1234)`).

### CLI

- [ ] **CA-34** `[shell]` `uv run python -m app.cli --help` sale con `0`, muestra `create-admin` y no contiene `--password` ni `--admin-password`. `uv run python -m app.cli create-admin --username ab --email a@b.co` (con `ADMIN_PASSWORD` definida) sale con `2` y no crea filas.

- [ ] **CA-35** `[test]` Con `ADMIN_PASSWORD` definida y `app.cli.SessionLocal` apuntando a la sesión de prueba: `await main(["create-admin", "--username", "cli_admin", "--email", "cli_admin@example.test"])` devuelve `0`; la fila existe con `role = ADMIN`, `is_active = true`, `doctor_id IS NULL`, `last_login_at IS NULL` y `password_hash LIKE '$argon2id$%'`; el login HTTP con `ADMIN_PASSWORD` devuelve `200`; `capsys` no contiene la contraseña ni el hash (stdout incluye `cli_admin`).

- [ ] **CA-36** `[test]` Duplicado: mismo `username` con otra capitalización ⇒ `1` y una sola fila; mismo `email` con otra capitalización y otro `username` ⇒ `1`; el `password_hash` original no cambió y el login con la contraseña original sigue funcionando.

- [ ] **CA-37** `[test]` Entradas inválidas ⇒ `2` y `SELECT count(*) FROM users WHERE username = :u` devuelve `0`: `username` de 3 caracteres, `username` con espacio (`"cli admin"`), `email` sin `@` o sin dominio, `ADMIN_PASSWORD` de 7 caracteres, `getpass` que lanza `EOFError`, y dos lecturas de `getpass` distintas (confirmación que no coincide). En este último caso se llama a `getpass` dos veces.

- [ ] **CA-38** `[test]` Rol ausente: con `roles` sin `ADMIN` (borrado dentro de la transacción de prueba, sin usuarios que lo referencien) `create_admin` lanza `admin_role_missing` y `main` devuelve `3`. Un `SQLAlchemyError` simulado en el commit devuelve `4` y el mensaje va a stderr.

### Logs y seguridad

- [ ] **CA-39** `[test]` Con `caplog` a `INFO`, tras un login correcto, uno fallido, un `429` y un `change-password` correcto: `caplog.text` no contiene la contraseña en claro, ni el `access_token`, ni el prefijo `$argon2`, ni el `username`, ni el `email`. Sí aparecen `auth.login_succeeded` y `request.completed`.

- [ ] **CA-40** `[test]` El body de `/auth/login` no se serializa a logs: el evento `request.completed` de esa ruta incluye `method`, `path`, `status`, `duration_ms` y `client_ip`, y **no** contiene las claves `username`, `password`, `body`, `headers` ni `authorization` (se permiten las claves que añade structlog: `event`, `level`, `logger`, `timestamp`, `request_id`).

### Calidad e integración

- [ ] **CA-41** `[shell]` OpenAPI:

  ```powershell
  uv run python -c "from app.main import app; o=app.openapi(); p=o['paths']; print(sorted(k for k in p if 'auth' in k)); print(bool(p['/api/v1/auth/me']['get'].get('security')), bool(p['/api/v1/auth/change-password']['post'].get('security')), 'security' in p['/api/v1/auth/login']['post']); print(sorted(p['/api/v1/auth/login']['post']['responses']))"
  ```

  imprime tres líneas: `['/api/v1/auth/change-password', '/api/v1/auth/login', '/api/v1/auth/me']`, `True True False`, `['200', '401', '403', '422', '429']`.

- [ ] **CA-42** `[shell]` Sin cambios de esquema: `uv run alembic check` imprime `No new upgrade operations detected.` y `git status --porcelain alembic/` no imprime nada.

- [ ] **CA-43** `[shell]` `uv run ruff check .` sin errores y `uv run ruff format --check .` sin cambios pendientes.

- [ ] **CA-44** `[shell]` `uv run pytest -q` en verde, incluidos `tests/test_health.py` y los tests de FEAT-003 (no hay regresiones por el nuevo envelope de errores).

- [ ] **CA-45** `[test]` El envelope es global: el `401` de `/me` y el `422` de `/auth/login` devuelven exactamente las claves `{detail, code, errors}` y no existe `details`; en el `422`, `errors` es una lista de objetos con `field` y `message`. Un `AppError` con `status_code = 404` conserva el mismo envelope por herencia del handler (se comprobará sobre un endpoint real en FEAT-004; aquí no se inventan rutas de prueba).

### Trazabilidad

| Requisito | Criterios |
|---|---|
| RF-01 / US-04 / RNF-07 / RNF-08 | CA-4, CA-5, CA-6, CA-10 a CA-21 |
| RF-02 (autorización en backend, rol no confiado) | CA-21, CA-22 |
| RF-19 / US-12 | CA-23 a CA-29 |
| §15 (rate limiting, no loguear tokens ni contraseñas) | CA-30 a CA-33, CA-39, CA-40 |
| §7.3 (CLI administrador inicial) | CA-34 a CA-38 |

## Plan de pruebas

### Aislamiento (requisito para que los tests sean posibles)

Los endpoints usan `get_session` y **hacen `commit()`**. Para no depender de usuarios residuales ni de la base de producción, el reviewer debe:

1. Añadir a `tests/conftest.py` una fixture `db_session` que dependa de `db_conn` y una `AsyncSession` unida a su transacción con savepoints:

   ```python
   @pytest.fixture
   async def db_session(db_conn):
       session = AsyncSession(
           bind=db_conn,
           expire_on_commit=False,
           join_transaction_mode="create_savepoint",
       )
       try:
           yield session
       finally:
           await session.close()
   ```

   `join_transaction_mode="create_savepoint"` hace que el `commit()` del servicio libere un SAVEPOINT y **no** la transacción externa; el `rollback()` de `db_conn` al final del test descarta todo.

2. Sobrescribir la dependencia en la app real (`app.main.app`), nunca crear una app distinta:

   ```python
   app.dependency_overrides[get_session] = lambda: db_session
   ```

   y limpiar en `finally` (`app.dependency_overrides.pop(get_session, None)`).

3. Reiniciar el rate limiter antes y después de cada test: `get_login_rate_limiter().reset_all()` (fixture `autouse`).

4. Para el CLI, monkeypatchear `app.cli.SessionLocal` con `lambda: AsyncSession(bind=db_conn, expire_on_commit=False, join_transaction_mode="create_savepoint")`; el `commit()` del CLI libera el savepoint y el rollback externo limpia. No se ejecuta `create-admin` contra la base real en la suite.

5. Los escenarios que simulan otra IP usan `AsyncClient(transport=ASGITransport(app=app, client=("203.0.113.7", 50000)), base_url="http://testserver")`.

6. `JWT_EXPIRE_MINUTES` y demás settings se cambian con `monkeypatch.setenv` + `get_settings.cache_clear()`; el código de request debe llamar a `get_settings()` y no capturarlo a nivel de módulo.

7. Los usuarios de prueba se insertan con un hash real: `hash_password("ClaveDePrueba123")`. El helper `tests/db_support.create_user` usa un hash ficticio y **no** sirve para autenticar; se debe ampliar con un parámetro `password` o insertar la fila desde el test.

8. `tests/conftest.py` no debe registrar usuarios permanentes ni depender de un `admin` preexistente: cada test crea el suyo dentro de la transacción.

### Unitarias (sin base de datos)

| # | Test | CA |
|---|---|---|
| U-1 | `hash_password` produce `$argon2id`, verifica y usa sal | CA-5 |
| U-2 | `verify_password` con hash desconocido devuelve `False` | CA-8 |
| U-3 | Round-trip y claims exactos del token | CA-6 |
| U-4 | Token expirado / firma ajena / alterado / sin `exp` | CA-7 |
| U-5 | `expires_in` y `exp - iat` con 30 y 45 minutos | CA-9, CA-17 |
| U-6 | `Settings`: defaults, `prod` rechaza placeholder/corto, `HS256` obligatorio | CA-2, CA-3 |
| U-7 | `LoginRateLimiter` con reloj falso: bloqueo, ventana, reset, purga | CA-32 |
| U-8 | `build_key` normaliza usuario y usa solo la IP del cliente | CA-32 |
| U-9 | `require_role`: permite, deniega y valida argumentos | CA-22 |
| U-10 | `LoginRequest`/`ChangePasswordRequest`: límites y confirmación | CA-15, CA-26, CA-27 |

### Integración (PostgreSQL + httpx)

| # | Test | CA |
|---|---|---|
| I-1 | Login `ADMIN` y `MEDICO`: forma exacta de la respuesta | CA-10, CA-11 |
| I-2 | Username case-insensitive y `strip` | CA-12 |
| I-3 | Credenciales inválidas e inexistente: cuerpos idénticos | CA-13 |
| I-4 | Inactivo con/sin contraseña correcta | CA-14 |
| I-5 | `422` por campos faltantes y longitudes | CA-15 |
| I-6 | `last_login_at` solo en éxito | CA-16 |
| I-7 | `/me` con token válido, sin token, token inválido y usuario borrado | CA-18, CA-19, CA-20 |
| I-8 | Desactivación inmediata con token vigente | CA-21 |
| I-9 | Claim `role` manipulado no eleva privilegios | CA-22 |
| I-10 | Cambio de contraseña feliz y contraseña antigua rechazada | CA-23 |
| I-11 | `current_password` incorrecta, sin cambios en hash | CA-24 |
| I-12 | Nueva igual a la actual | CA-25 |
| I-13 | Confirmación distinta y mínimo de 8 | CA-26, CA-27 |
| I-14 | Cambio sin token y con usuario inactivo | CA-28 |
| I-15 | Token previo sigue válido tras el cambio | CA-29 |
| I-16 | Rate limit: 5 fallos + 6.º `429` con `Retry-After` | CA-30 |
| I-17 | Rate limit: aislamiento, reseteo, `403`/`422` no cuentan | CA-31 |
| I-18 | `configure()` y `X-Forwarded-For` ignorado | CA-33 |
| I-19 | `create_admin` contra `db_session`: crea, duplica y rol ausente | CA-35, CA-36, CA-38 |

### Seguridad

| # | Test | CA |
|---|---|---|
| S-1 | Token firmado con otro secreto, alterado, expirado, `sub` no numérico | CA-7, CA-20 |
| S-2 | El rol sale de la base, no del claim | CA-22 |
| S-3 | Logs sin contraseña, token, hash, `username` ni `email` | CA-39, CA-40 |
| S-4 | `500` no expuesto por hash corrupto (hash inválido ⇒ `401`) | CA-8, CA-13 |
| S-5 | `Retry-After` presente y en rango; `WWW-Authenticate` en todo `401` | CA-19, CA-20, CA-30 |
| S-6 | `JWT_SECRET` de ejemplo rechazado en `prod` | CA-3 |

### CLI

| # | Test | CA |
|---|---|---|
| C-1 | `--help` y argumentos inválidos (sin base) | CA-34 |
| C-2 | Creación feliz + login posterior + stdout sin secretos | CA-35 |
| C-3 | Duplicados case-insensitive sin sobrescribir | CA-36 |
| C-4 | Validaciones, `EOFError` y confirmación distinta | CA-37 |
| C-5 | Rol ausente (`3`) y error de base (`4`) | CA-38 |

## Riesgos y dependencias

| ID | Riesgo | Impacto | Mitigación / decisión |
|---|---|---|---|
| **R-1** | Token robado válido hasta `exp`; no hay revocación. | Alto | ADR-003; expiración de 30 min configurable, HTTPS en despliegue, `sessionStorage` en Angular. Aceptado (D-3). |
| **R-2** | Rate limit en memoria: N workers ⇒ hasta N×5 intentos por ventana; un reinicio limpia el contador. | Medio | Decisión humana (D-5). Documentado; si se escala horizontalmente, sustituir por Redis en una feature posterior sin cambiar el contrato (`429` + `Retry-After`). |
| **R-3** | Detrás de un proxy inverso, `request.client.host` es la IP del proxy: todos comparten la componente IP de la clave. | Medio | La componente usuario sigue separando cuentas; no se confía en `X-Forwarded-For` porque es falsificable. Configurar el proxy y, si hace falta, `ProxyHeadersMiddleware` en despliegue. |
| **R-4** | Enumeración de cuentas: `403` para inactivo y `401` para inexistente permiten deducir existencia si se acierta la contraseña. | Bajo | El `403` exige credenciales correctas (D-2) y el rate limit acota el sondeo. El contrato exige `403`. |
| **R-5** | Cambiar contraseña no invalida sesiones abiertas. | Medio | Expiración corta; documentado (D-3) y verificado (CA-29). La revocación llega con refresh/blacklist si se aprueba. |
| **R-6** | El cambio de envelope (`details` → `errors`, `422` normalizado) afecta a todas las features futuras. | Medio | Ningún test actual lo usa; FEAT-004 debe construir sus errores con `errors`. Documentado en D-1 y cubierto por CA-45. |
| **R-7** | Argon2id con parámetros por defecto (~50–100 ms por hash) ralentiza la suite. | Bajo | Pocos hashes por test (~20–30 en total); se acepta. No se introduce configuración de coste para no debilitar producción. |
| **R-8** | `ENVIRONMENT` mal configurado permitiría arrancar producción con el secreto de ejemplo. | Alto | Validación en `Settings` (falla en `prod`), `.env.example` con placeholder y checklist de despliegue. `staging` no se valida (solo `prod`). |
| **R-9** | PyJWT ≥ 2.10 exige `sub` string; usar el `id` entero rompe la decodificación. | Medio | `sub=str(user.id)` y `int()` al leer; un `sub` no numérico es `401` (CA-20). |
| **R-10** | Crecimiento del mapa del rate limiter con IPs/usuarios aleatorios. | Bajo | Purga por ventana y purga global al superar 1024 claves (CA-32). |
| **R-11** | `tests/db_support.create_user` inserta hashes falsos con forma bcrypt; verificarlos lanzaría `UnknownHashError`. | Bajo | `verify_password` captura `UnknownHashError` y devuelve `False` (CA-8). |
| **R-12** | Un usuario `MEDICO` sin `doctor_id` (permitido por el esquema) emite tokens con `doctor_id: null`, lo que puede confundir a las features de visitas. | Bajo | Documentado: el claim es nullable; la regla "MEDICO sin doctor no registra visitas" se implementará en la feature de visitas. |
| **R-13** | `500`/`405` no usan el envelope estándar. | Bajo | Fuera de alcance (D-4); el cliente Angular solo consume `detail`/`errors` en errores de negocio. |

**Dependencias**

- **FEAT-001 (`done`)**: FastAPI, settings, logging con redacción, sesión async, `AppError` y middlewares.
- **FEAT-003 (`done`)**: tablas `users`, `roles`, `doctors`, `citext`, `last_login_at` y seed de `ADMIN`/`MEDICO`.
- **Dependencias nuevas**: `pyjwt>=2.10` y `pwdlib[argon2]>=0.2.1` (instaladas con `uv sync`).
- **Bloquea a FEAT-004**: el CRUD de usuarios y `reset-password` reutilizan `hash_password`, `get_current_user`, `require_role`, `user_repository` y `user_service.create_admin`.
- **Despliegue**: `JWT_SECRET` debe viajar en `.env` (el servicio `api` de Compose ya lo carga con `env_file: .env`). **No** se modifica `docker-compose.yml`.

**Datos sensibles**

- `password_hash` nunca se devuelve, imprime ni registra.
- El token solo aparece en la respuesta de login; los logs no lo contienen.
- El CLI no imprime contraseña, hash ni email.
- Las pruebas no usan datos reales: usuarios sintéticos dentro de una transacción revertida.

## Preguntas abiertas

Ninguna que bloquee la implementación. Los puntos que el contrato no resuelve se cerraron con las decisiones de esta spec y quedan verificados por criterios de aceptación:

- Secreto de JWT en `local`/`test` (D-6), `sub` string (D-8) y envelope de error (D-1) son decisiones técnicas, no reglas de negocio nuevas.
- `last_login_at` (SUP-1) y la precedencia `401`/`403` del login (D-2) se documentan como supuestos/decisiones verificables; si el humano prefiere otra semántica, se cambia sin afectar al resto del contrato.

## Historial

- 2026-10-21 — creada por spec-author a partir de `DispensarioMedicoDocs/03-api-rest.md` §2–§5 y §15, `01-requerimientos-y-backlog.md`, `02-arquitectura.md` §9, `06-guia-despliegue.md` §7.3, `08-adrs.md` ADR-003 y el código tras FEAT-003.
- 2026-10-21 — `proposed` → `approved` (humano, alcance acotado: `/auth/*`, JWT/roles y CLI `create-admin`; FEAT-004 separada).
- 2026-10-21 — Ajuste tras revisión (ciclo 2): `decode_access_token` ya no acepta `expires_minutes`; solo `create_access_token` lo usa.
