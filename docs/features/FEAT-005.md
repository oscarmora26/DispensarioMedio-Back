# FEAT-005 — Login con email como identificador alternativo

**Estado:** `approved` (aprobada explícitamente por el humano el 2026-10-21)
**Fecha:** 2026-10-21
**Alcance:** `POST /api/v1/auth/login` acepta `email` **o** `username` (exactamente uno), sin cambios de respuesta, claims ni esquema.
**Fuente de verdad funcional:** `DispensarioMedicoDocs/03-api-rest.md` §2, §3 y §16; `01-requerimientos-y-backlog.md` RF-01 y US-04; `docs/features/FEAT-002.md` (login, rate limit, errores) y `docs/features/FEAT-004.md` (regex de email, normalización de identidad).

## Contexto

Hoy el login solo resuelve por `username` (FEAT-002 regla 2). El frontend necesita ofrecer «correo o usuario» y, en algunos despliegues, únicamente correo, porque es el dato que el usuario recuerda. `users.email` ya es `CITEXT`, `NOT NULL` y único (`uq_users_email`), de modo que puede usarse como identificador sin tocar el esquema.

La decisión humana ya tomada es: `email` opcional **o** `username`, exactamente uno, en el mismo `/api/v1/auth/login`. No se elimina `username`, no se crea `/api/v2` y no cambia la respuesta ni los claims del token. Es un cambio compatible según §16 (campo nuevo opcional).

## Alcance

- `app/schemas/auth.py`: `LoginRequest` con `email: str | None`, `username: str | None` y `password` obligatorio; validador de modelo «exactamente uno»; ejemplos OpenAPI.
- `app/services/auth_service.py`: `login` recibe `username` o `email` y resuelve la búsqueda correspondiente; misma verificación, mismos errores, mismo rate limit y mismo `last_login_at`.
- `app/repositories/user_repository.py`: nueva `get_user_by_email` (con `selectinload(User.role)`); `get_user_by_username` no se modifica.
- `app/api/v1/routes/auth.py`: pasar el identificador presente y actualizar `summary`/`description` del endpoint.
- Tests nuevos de login por email y ajuste puntual del caso `{}` de FEAT-002 CA-15 (ver D-2).

## Fuera de alcance

- Eliminar, renombrar o hacer opcional `username` en la respuesta, el token o el CRUD de usuarios.
- `/api/v2`, endpoints nuevos, cambios en `/auth/me` o `/auth/change-password`.
- Recuperación de contraseña por correo, login por teléfono/documento, MFA, refresh tokens o revocación.
- Normalizar el valor almacenado de `email`/`username` (se conserva la capitalización; FEAT-002 regla 1 y FEAT-004 D-12).
- Cambios de esquema, migraciones, settings, dependencias, `docker-compose.yml` o `.env.example`.
- Frontend Angular (formulario, guards, interceptor).
- Un contador de rate limit único por cuenta cuando se usan ambos identificadores (D-4).

## Reglas de negocio

1. El body de login exige `password` (1..128, sin `strip`) y **exactamente uno** de `email` o `username`. `null` y campo ausente son equivalentes.
2. `username`, si viene, se recorta (`strip`) y debe tener 1..50 caracteres (comportamiento actual, sin cambios). `email`, si viene, se recorta y debe tener 1..255 caracteres.
3. `email` debe cumplir `^[^@\s]+@[^@\s]+\.[^@\s]+$`. La comparación contra `users.email` es case-insensitive porque la columna es `CITEXT`; no se convierte el valor a minúsculas ni al almacenar ni al autenticar.
4. El identificador se resuelve contra una sola columna: `email` ⇒ `users.email`; `username` ⇒ `users.username`. Nunca se busca el email en `username` ni viceversa.
5. Credenciales válidas (usuario activo + contraseña correcta) ⇒ `200` con la misma `LoginResponse` de FEAT-002: `access_token`, `token_type`, `expires_in` y `user` con exactamente `{id, username, email, role, doctor_id}`. `user.email` sigue apareciendo aunque el login haya sido por `username`.
6. Los claims del token no cambian: `sub` (string del id), `role`, `doctor_id`, `iat`, `exp`. `expires_in` sigue siendo `JWT_EXPIRE_MINUTES * 60`.
7. Email inexistente, contraseña incorrecta y username inexistente producen el **mismo** `401` byte a byte: `code = "invalid_credentials"`, `detail = "Usuario o contraseña incorrectos"`, `errors = []`, `WWW-Authenticate: Bearer`. No se revela si la cuenta existe ni por qué identificador se buscó.
8. Usuario inactivo con contraseña correcta ⇒ `403` `inactive_user` (`detail = "El usuario está inactivo"`), independientemente del identificador usado. Con contraseña incorrecta ⇒ `401` genérico (se mantiene D-2 de FEAT-002).
9. Un login exitoso actualiza `users.last_login_at` con la hora UTC del servidor, también cuando se autenticó por email. Los intentos fallidos no la tocan.
10. La rate limit conserva la mecánica de FEAT-002: chequeo **antes** de la base de datos, `429` con `Retry-After`, éxito reinicia la clave y solo el `401` cuenta como fallo. La clave es `(IP, identificador enviado normalizado)`; ver D-4.
11. Orden de evaluación: validación Pydantic (`422`) → rate limit (`429`) → búsqueda/contraseña (`401`) → estado (`403`) → éxito. El endpoint es público: no hay comprobación de autenticación.
12. Nunca se registran `email`, `username`, contraseña, hash, token ni el cuerpo. Los eventos de FEAT-002 (`auth.login_succeeded`, `auth.login_failed` con `reason ∈ unknown_user|bad_password|inactive`, `auth.rate_limited`) se mantienen sin cambios.
13. No hay cambios de esquema, migraciones, settings ni dependencias.

## Decisiones

| ID | Decisión | Justificación / impacto |
|---|---|---|
| **D-1** | `LoginRequest` declara `email: str \| None = None`, `username: str \| None = None`, `password: str` obligatorio, y un `@model_validator(mode="after")` que exige exactamente uno y lanza `PydanticCustomError` con mensaje `Indique 'username' o 'email' (exactamente uno)`. Como el error es de modelo, el envelope lo reporta con `field = "body"` (el `loc` es `("body",)` y `_validation_field` lo deja vacío). Se acepta y documenta: el cliente corrige el body completo, no un campo. | Un `422` con `field = "body"` es exacto y verificable, evita inventar un campo ficticio y no requiere tocar el handler global. Si además hay un error de campo (email con formato inválido, password ausente), Pydantic reporta **solo** los errores de campo y el validador de modelo no se ejecuta. |
| **D-2** | Validación de email: `strip` en `mode="before"`; `max_length=255`; validador propio que devuelve `None` sin tocar, y para cadena vacía lanza `El correo electrónico no puede estar vacío`; para formato inválido, `El correo electrónico no tiene un formato válido` (mismo texto y regex que FEAT-004). Los mensajes automáticos de longitud máxima conservan el texto inglés de Pydantic (FEAT-002 regla 30). `username` y `password` mantienen sus validadores actuales. | Coherencia total con `UserCreate`/`UserUpdate`. **Impacto en un test existente:** FEAT-002 CA-15 afirmaba que `{}` devuelve `{password, username}`. Al ser `username` opcional, `{}` ahora devuelve solo `password` (el validador de modelo no corre si falla un campo). Es la única aserción preexistente que debe actualizarse; el resto de la suite no cambia. |
| **D-3** | Se añade `get_user_by_email(session, email) -> User \| None` en `user_repository.py`, con `selectinload(User.role)` y `where(User.email == email)`. No se generaliza ni se renombra `get_user_by_username`. | Mantiene intactos FEAT-002 y FEAT-004, evita una firma con «modo» ambiguo y aprovecha el índice único `uq_users_email`. La comparación case-insensitive la aporta `CITEXT`, igual que en `username`. |
| **D-4** | La clave del limitador sigue siendo `build_key(client_ip, identificador)` con el identificador **enviado** (email o username), normalizado por `strip().casefold()` dentro de `build_key`. Una cuenta con ambos identificadores tiene **dos contadores independientes**: 5 fallos por email no bloquean el login por username y viceversa. | Es la única opción que conserva el chequeo **antes** de la base de datos (FEAT-002 regla 23 y D-9): para unificar por cuenta habría que resolver primero el usuario. Limitación aceptada: un atacante que conozca ambos identificadores dispone de hasta 10 intentos por ventana para la misma cuenta. Se documenta en R-1. `build_key` no se renombra (su segundo parámetro recibe el identificador). |
| **D-5** | Si el cliente envía un email en el campo `username`, se busca en `users.username`; en general no autentica (`401`). Solo autenticaría si ese texto fuera el `username` real de alguna cuenta (el formato actual de `username` permite `@`). Lo mismo aplica al revés: un username en `email` se valida como email y, si no tiene forma de correo, es `422`. | Es la consecuencia literal de la decisión «resolver por la columna indicada». No se hace *fallback* entre columnas porque duplicaría la búsqueda, rompería la semántica del campo y facilitaría el sondeo de cuentas. |
| **D-6** | Precedencia: (a) `422` de Pydantic (campo o `body`); (b) `429`; (c) `401` genérico; (d) `403` de inactivo; (e) `200`. El endpoint es público, así que no hay `401` de autenticación previa. | Coincide con FEAT-002 (rate limit antes de BD, estado después de contraseña) y hace deterministas los casos mixtos: `{"email": "mal", "password": "..."}` es `422` aunque la clave esté bloqueada. |

## Modelo de datos

**Sin cambios de esquema ni migraciones.** `uv run alembic check` debe seguir limpio y `git status --porcelain alembic/` vacío.

| Tabla | Columna | Uso |
|---|---|---|
| `users` | `id` (`BIGINT`) | `sub` del token y `user.id`. |
| `users` | `username` (`CITEXT`, `uq_users_username`) | Búsqueda cuando se envía `username`. |
| `users` | `email` (`CITEXT`, `uq_users_email`) | Búsqueda cuando se envía `email`; el índice único garantiza como máximo una fila. |
| `users` | `password_hash` | Verificación Argon2id. |
| `users` | `role_id` → `roles.name` | Claim `role` y `user.role`. |
| `users` | `doctor_id` | Claim `doctor_id` y respuesta. |
| `users` | `is_active` | `403` tras contraseña correcta. |
| `users` | `last_login_at` | Se escribe solo en login exitoso. |

```python
# Login por email (nueva función del repositorio)
select(User).options(selectinload(User.role)).where(User.email == email)

# Login por username (sin cambios)
select(User).options(selectinload(User.role)).where(User.username == username)
```

## Contrato de API

### `POST /api/v1/auth/login`

- **Auth:** pública (sin Bearer).
- **Body** (`LoginRequest`):

| Campo | Tipo | Obligatorio | Restricciones |
|---|---|---|---|
| `email` | `str \| None` | No | `strip`, 1..255, `^[^@\s]+@[^@\s]+\.[^@\s]+$`; exactamente uno de `email`/`username` |
| `username` | `str \| None` | No | `strip`, 1..50; exactamente uno de `email`/`username` |
| `password` | `str` | Sí | 1..128, sin `strip` |

```json
{ "email": "aperez@dispensario.local", "password": "MiClaveSegura123" }
```

```json
{ "username": "aperez", "password": "MiClaveSegura123" }
```

- **Respuesta `200`** (`LoginResponse`, idéntica a FEAT-002):

```json
{
  "access_token": "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9...",
  "token_type": "bearer",
  "expires_in": 1800,
  "user": {
    "id": 12,
    "username": "aperez",
    "email": "aperez@dispensario.local",
    "role": "MEDICO",
    "doctor_id": 5
  }
}
```

- **Errores:**

| HTTP | `code` | Cuándo | `detail` |
|---|---|---|---|
| `401` | `invalid_credentials` | email/username inexistente o contraseña incorrecta | `Usuario o contraseña incorrectos` |
| `403` | `inactive_user` | credenciales correctas e `is_active = false` | `El usuario está inactivo` |
| `422` | `validation_error` | faltan ambos identificadores o vienen los dos (`field = "body"`); email vacío/formato inválido/`>255` (`field = "email"`); `username` vacío/`>50` (`field = "username"`); `password` ausente/`>128` (`field = "password"`) | `Datos inválidos` |
| `429` | `rate_limited` | 6.º intento fallido dentro de la ventana; `Retry-After` | `Demasiados intentos fallidos. Intente de nuevo más tarde` |

Ejemplo del `422` de identificador:

```json
{
  "detail": "Datos inválidos",
  "code": "validation_error",
  "errors": [{ "field": "body", "message": "Indique 'username' o 'email' (exactamente uno)" }]
}
```

Ejemplo de `422` de formato:

```json
{
  "detail": "Datos inválidos",
  "code": "validation_error",
  "errors": [{ "field": "email", "message": "El correo electrónico no tiene un formato válido" }]
}
```

Los códigos HTTP declarados del endpoint no cambian: `200`, `401`, `403`, `422`, `429`.

### OpenAPI

- `LoginRequest` documenta ambos campos como opcionales con descripción explícita («Envía exactamente uno: `email` o `username`»).
- `json_schema_extra` con dos ejemplos: uno con `email` y otro con `username`.
- `summary` y `description` del endpoint pasan a: «Autentica por nombre de usuario **o** correo electrónico y contraseña…».
- `LoginResponse`, `LoginUser` y el esquema `BearerAuth` no cambian.

## Criterios de aceptación

Convención: `[test]` = aserción exacta que el reviewer implementa en `tests/**`; `[shell]` = comando con salida esperada. Los tests usan `auth_client`, `db_session`, `db_conn`, `create_auth_user` y `token_for` de `tests/conftest.py` / `tests/auth_support.py`.

### Login por email

- [ ] **CA-1** `[test]` Con un usuario activo cuyo `email` es único, `POST /api/v1/auth/login` con `{"email": <email>, "password": DEFAULT_PASSWORD}` ⇒ `200`; claves de nivel superior exactamente `{access_token, token_type, expires_in, user}`, `token_type == "bearer"`, `expires_in == 1800`; `user` con exactamente `{id, username, email, role, doctor_id}` y `user.email` igual al almacenado (misma capitalización), `user.username` igual al almacenado.
- [ ] **CA-2** `[test]` Email `strip` y case-insensitive: almacenado `Mixed_<suffix>@Example.Test`, el body `"  mixed_<suffix>@example.test  "` ⇒ `200` y `user.email == "Mixed_<suffix>@Example.Test"` (valor almacenado). Un email con distinta capitalización en local y dominio también autentica.
- [ ] **CA-3** `[test]` Exactamente uno: `{"password": "..."}` y `{"email": <email>, "username": <username>, "password": "..."}` ⇒ `422`, `code == "validation_error"`, `errors == [{"field": "body", "message": "Indique 'username' o 'email' (exactamente uno)"}]`, `detail == "Datos inválidos"`; no se actualiza `last_login_at` y la clave del rate limiter no se crea.
- [ ] **CA-4** `[test]` Email inválido ⇒ `422` con `field == "email"` y `message` español: `"sin-arroba"`, `"a@b"`, `"a b@c.d"`, `"a@.c"`, `""`, `"   "` (vacío tras `strip`) y cadena de 256 caracteres. Un email de forma válida (`"a@b.c"`) con contraseña incorrecta ⇒ `401`, no `422`.
- [ ] **CA-5** `[test]` Email inexistente ⇒ `401`; el cuerpo es **idéntico byte a byte** al de una contraseña incorrecta por email y al de un username inexistente: `{detail, code, errors}` sin `details`, `code == "invalid_credentials"`, `errors == []`, `WWW-Authenticate: Bearer`.
- [ ] **CA-6** `[test]` Usuario inactivo: con email y contraseña correcta ⇒ `403` `inactive_user`, `errors == []`, `last_login_at` sigue `NULL`; con contraseña incorrecta ⇒ `401` `invalid_credentials`.
- [ ] **CA-7** `[test]` Login por email actualiza `last_login_at`: `NULL` antes, no `NULL` y a menos de 60 s de `now()` de la base después; permanece idéntico tras un `401` por email y tras un `403` de inactivo.
- [ ] **CA-8** `[test]` Contrato de respuesta y claims idéntico entre identificadores: dos usuarios equivalentes (mismo rol y `doctor_id`) autenticados uno por `username` y otro por `email` producen las mismas claves; el token decodificado tiene exactamente `{sub, role, doctor_id, iat, exp}`, `sub == str(user_id)`, `exp - iat == 1800`, y `expires_in == 1800` en ambos.

### Compatibilidad con username

- [ ] **CA-9** `[test]` Login por `username` intacto: `{"username": <username>, "password": DEFAULT_PASSWORD}` ⇒ `200`; mayúsculas y espacios (`"  USERNAME  "`) siguen autenticando (CITEXT + `strip`); `{"username": <username>}` sin `password` ⇒ `422` con `field == "password"`; `username` de 51 caracteres ⇒ `422` con `field == "username"`.
- [ ] **CA-10** `[test]` Un email enviado en el campo `username` **no** autentica a la cuenta de ese email (⇒ `401`), salvo que ese texto sea el `username` real de la cuenta, en cuyo caso autentica como username (D-5).
- [ ] **CA-11** `[test]` Un `username` enviado en el campo `email` sin forma de correo ⇒ `422` con `field == "email"`; con forma de correo pero sin coincidencia en `users.email` ⇒ `401` genérico.

### Rate limiting

- [ ] **CA-12** `[test]` Con el limiter reiniciado: 5 `401` por email y el 6.º intento **con contraseña correcta** ⇒ `429`, `code == "rate_limited"`, `errors == []`, `Retry-After` presente y `1 <= int(Retry-After) <= 900`; `last_login_at` sigue `NULL`. Variantes `EMAIL`, `email` y `"  email  "` comparten contador.
- [ ] **CA-13** `[test]` Un login correcto por email reinicia su clave (4 fallos + éxito + 4 fallos + éxito ⇒ los dos éxitos `200`); el `403` de inactivo y el `422` de email inválido no incrementan el contador (se comprueba con `get_login_rate_limiter().retry_after(build_key(ip, identificador)) is None`).
- [ ] **CA-14** `[test]` Doble contador documentado (D-4): 5 fallos por email dejan la clave del email limitada (`429` con contraseña correcta) mientras el login por `username` de la **misma** cuenta devuelve `200` y solo reinicia la clave del username.

### Logs

- [ ] **CA-15** `[test]` Con `caplog` a `INFO`, tras login correcto por email, login fallido por email inexistente y un `429`: `caplog.text` no contiene el email, el username, la contraseña, el `access_token` ni `$argon2`; aparecen `auth.login_succeeded`, `auth.login_failed` y `auth.rate_limited`.

### OpenAPI, esquema y calidad

- [ ] **CA-16** `[shell]` OpenAPI:

  ```powershell
  uv run python -c "from app.main import app; s=app.openapi()['components']['schemas']['LoginRequest']; print(sorted(s['required'])); print(len(s.get('examples', []))); print([sorted(e) for e in s.get('examples', [])]); print(s['properties']['email']['description'])"
  ```

  imprime `['password']`, `2`, `[['email', 'password'], ['password', 'username']]` y una descripción que contiene `email` y `username`. Además `POST /api/v1/auth/login` conserva las respuestas `['200', '401', '403', '422', '429']`.

- [ ] **CA-17** `[shell]` Sin cambios de esquema ni dependencias: `uv run alembic check` imprime `No new upgrade operations detected.`, `git status --porcelain alembic/ pyproject.toml uv.lock` no imprime nada y `uv run ruff check .` + `uv run ruff format --check .` salen limpios.
- [ ] **CA-18** `[shell]` `uv run pytest -q` en verde con los tests existentes (257, con la única actualización de la aserción de `{}` documentada en D-2) y los nuevos de email; `tests/test_auth_login.py`, `tests/test_auth_rate_limit_api.py` y los de `tests/test_users_*.py` sin regresiones.

### Trazabilidad

| Requisito | Criterios |
|---|---|
| RF-01 / US-04 (login, 401/403) | CA-1 a CA-11 |
| §15 (rate limiting, no registrar credenciales) | CA-12 a CA-15 |
| §16 / §14 (cambio compatible y OpenAPI) | CA-16 |
| Retrocompatibilidad FEAT-002/FEAT-004 | CA-8 a CA-11, CA-18 |

## Plan de pruebas

### Aislamiento

Se reutiliza la infraestructura de FEAT-002 sin cambios: `db_conn` (transacción revertida), `db_session` (savepoints), `auth_client`/`auth_client_factory`, la fixture `autouse` que reinicia el rate limiter y `create_auth_user` (acepta `email=`). Cada test crea sus usuarios con `unique_suffix()`; no se usan datos reales.

### Unitarias (sin base de datos)

| # | Test | CA |
|---|---|---|
| U-1 | `LoginRequest`: email/username opcionales, `password` obligatorio, exactamente uno, `field="body"` | CA-3 |
| U-2 | `LoginRequest`: `strip`, límites 1/255 y 1/50, regex de email y mensajes en español | CA-2, CA-4, CA-9 |
| U-3 | `login(...)` sin identificador o con ambos lanza `ValueError` (defensa de programación) | CA-3 |
| U-4 | `get_user_by_email` es case-insensitive y carga `role` (sin `MissingGreenlet`) | CA-1, CA-2 |

### Integración (PostgreSQL + httpx)

| # | Test | CA |
|---|---|---|
| I-1 | Login por email feliz: forma exacta de respuesta y claims | CA-1, CA-8 |
| I-2 | Email `strip`, mayúsculas y capitalización almacenada | CA-2 |
| I-3 | Ambos/ninguno identificador y email inválido | CA-3, CA-4 |
| I-4 | Email inexistente vs contraseña mala vs username inexistente | CA-5 |
| I-5 | Inactivo por email y `last_login_at` | CA-6, CA-7 |
| I-6 | Username intacto y email en el campo equivocado | CA-9, CA-10, CA-11 |
| I-7 | Rate limit por email, reinicio y doble contador | CA-12, CA-13, CA-14 |

### Seguridad

| # | Test | CA |
|---|---|---|
| S-1 | `401` genérico indistinguible; `WWW-Authenticate` presente | CA-5 |
| S-2 | Logs sin email, username, contraseña ni token | CA-15 |

## Riesgos y dependencias

| ID | Riesgo | Impacto | Mitigación |
|---|---|---|---|
| **R-1** | Doble contador: una cuenta con email y username permite 5 + 5 intentos por ventana. | Medio | Documentado (D-4). Unificar exigiría consultar la BD antes del limiter, lo que rompe FEAT-002 regla 23. Reevaluar si se migra a un limitador distribuido por cuenta. |
| **R-2** | Un `422` con `field="body"` obliga al frontend a tratar el error a nivel de formulario, no de input. | Bajo | Se documenta el mensaje exacto y el ejemplo OpenAPI. Los errores de formato sí señalan `email`, `username` o `password`. |
| **R-3** | El email es dato personal y podría acabar en logs si se registra el payload. | Medio | Regla 12 y CA-15; el middleware ya no registra cuerpos y la lista de redacción cubre `email`. |
| **R-4** | Enumeración de cuentas por `403` de inactivo o por `401` genérico. | Bajo | Sin cambios respecto de FEAT-002 D-2/R-4: el `403` exige contraseña correcta y el rate limit acota el sondeo. |
| **R-5** | Usuarios con `username` que parece un email pueden autenticarse enviándolo en `username`; no hay *fallback*. | Bajo | D-5 y CA-10; el comportamiento es determinista y no duplica búsquedas. |
| **R-6** | Un test existente de FEAT-002 CA-15 cambia (`{}` ya no reporta `username`). | Bajo | Única aserción afectada, documentada en D-2 y verificada en CA-18. No es un cambio de contrato para usuarios válidos. |

**Dependencias:** FEAT-002 (`done`, login/rate limit/errores), FEAT-003 (`done`, `users.email` CITEXT único) y FEAT-004 (`done`, regex de email y normalización). No bloquea a otras features. No hay migraciones ni datos sensibles nuevos.

## Preguntas abiertas

Ninguna que bloquee la implementación. El único efecto colateral detectado (la aserción de `{}` de FEAT-002 CA-15) se cierra en D-2 y se reporta al team-lead; el resto de la decisión humana está completo.

## Historial

- 2026-10-21 — creada por spec-author a partir de `03-api-rest.md` §2/§3/§16, `01-requerimientos-y-backlog.md` RF-01/US-04, FEAT-002, FEAT-004 y el código actual.
- 2026-10-21 — `proposed` → `approved` (humano).
