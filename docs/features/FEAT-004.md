# FEAT-004 — Gestión de usuarios: CRUD, roles y restablecimiento de contraseña

**Estado:** `approved` (aprobada explícitamente por el humano el 2026-10-21)
**Fecha:** 2026-10-21
**Alcance:** `GET/POST /api/v1/users`, `GET/PUT/DELETE /api/v1/users/{user_id}` y `POST /api/v1/users/{user_id}/reset-password`, exclusivamente para `ADMIN`, reutilizando la autenticación, el envelope de error y el hash Argon2id de FEAT-002.
**Fuente de verdad funcional:** `DispensarioMedicoDocs/03-api-rest.md` §2 (paginación, envelope, códigos), §4 (matriz `/users`: solo ADMIN), §5 (endpoints), §6 (parámetros de listado), §7 (POST `/users`, reglas, `reset-password`), §15 y §16; `01-requerimientos-y-backlog.md` RF-03, RF-10, RF-17, RF-18, RF-20, RF-22 (auditoría, fuera de v1), US-10, US-11, §3 (matriz de permisos) y §7 (CA-CRUD); `04-base-de-datos.md` §6.2 (`users`), §8.3 (reglas de aplicación) y §9.2 (índices únicos); `05-plan-de-pruebas.md` CP-003 y §7 (usuarios de prueba); `02-arquitectura.md` §9 (RBAC en backend).

## Contexto

FEAT-003 dejó el esquema `roles` / `doctors` / `users` y FEAT-002 dejó la autenticación, el envelope de error `{detail, code, errors}`, `hash_password`, `require_role("ADMIN")` y `create_admin` para el administrador inicial. Hoy **no existe ninguna vía HTTP para crear, consultar, actualizar o desactivar usuarios**, ni para restablecer una contraseña olvidada. El único administrador se crea por CLI, y un `MEDICO` no puede existir como cuenta de acceso sin intervención manual en la base.

Sin esta feature, el sistema no puede operar con más de una cuenta: RF-03 (gestionar usuarios, roles y estados), RF-10 (asociar un usuario médico con un registro de médico), RF-17 (desactivar sin borrar), RF-20 (restablecer contraseña) y US-10/US-11 quedan sin implementar. Además es la feature que cierra la deuda explícita de FEAT-002 CA-45: comprobar el envelope de error sobre un endpoint real con `404`.

El alcance está acotado por decisión humana: **no incluye el CRUD de médicos**. Esta feature solo consume `doctors.id` ya existentes; la creación y edición de médicos es otra feature.

## Alcance

- `app/schemas/user.py`: `UserRead`, `UserCreate`, `UserUpdate`, `UserResetPasswordRequest`, `UserListResponse`; `MessageResponse` se reutiliza de `app/schemas/auth.py`.
- `app/repositories/user_repository.py` (ampliación): listado paginado, búsqueda, duplicados con exclusión de un `id`, asignación de `doctor_id`, bloqueo del objetivo y del rol `ADMIN`, conteo de administradores activos.
- `app/repositories/doctor_repository.py` (nuevo): `get_doctor_by_id` (solo lectura de `doctors.id`).
- `app/services/user_service.py` (ampliación): `list_users`, `get_user`, `create_user`, `update_user`, `deactivate_user`, `reset_password`. `create_admin` se conserva sin cambios.
- `app/api/v1/routes/users.py` (nuevo) con tag `users`, registrado en `app/api/v1/router.py`.
- Reutilización de: `app/api/deps.py` (`get_current_user`, `require_role`), `app/core/security.py` (`hash_password`), `app/core/exceptions.py` (`AppError`, `NotFoundError`, `ConflictError`, envelope; se amplía `_validation_field` para descartar también los prefijos `query`/`path`/`header`, ver D-13), `app/models/user.py`, `app/models/role.py`, `app/models/doctor.py`.
- Seis endpoints descritos en "Contrato de API".
- Logs estructurados `user.created`, `user.updated`, `user.deactivated`, `user.password_reset`.
- Documentación OpenAPI completa (tag, `summary`, `description`, `response_model`, códigos).

### Estructura de archivos resultante

```text
app/
├── api/v1/
│   ├── router.py                   # modificado: incluye users
│   └── routes/users.py             # nuevo
├── repositories/
│   ├── doctor_repository.py        # nuevo
│   └── user_repository.py          # ampliado
├── schemas/user.py                 # nuevo
└── services/user_service.py        # ampliado
```

## Fuera de alcance

- CRUD de `/doctors` (crear, editar, desactivar médicos): esta feature solo verifica que el `doctor_id` exista. La validación de `doctors.is_active` queda para la feature de médicos.
- `GET /roles` o cualquier endpoint de catálogo de roles: los roles son un catálogo fijo sembrado por FEAT-003.
- Escritura en `audit_log` (RF-22 es `Could` y queda fuera de v1; la tabla sigue vacía).
- Borrado físico de usuarios: la única baja es lógica (`is_active = false`).
- Refresh tokens, revocación de sesiones, recuperación por correo, MFA, cambio de la contraseña propia (`POST /auth/change-password`, ya en FEAT-002).
- Asignación de permisos dinámicos, roles distintos de `ADMIN` y `MEDICO`.
- Cambios de esquema, migraciones, `docker-compose.yml`, `Dockerfile`, `.env.example` o settings nuevas. `uv run alembic check` debe seguir limpio.
- CORS, frontend Angular (tabla, formulario, toasts), pruebas E2E.
- Paginación por cursor, exportación de usuarios, ordenación por campos fuera del catálogo de D-3.

## Reglas de negocio

### Autorización y alcance

1. Los seis endpoints exigen `Authorization: Bearer <token>` y rol `ADMIN` leído de la base (`user.role.name`), mediante `require_role("ADMIN")`. El claim `role` del token no se usa (FEAT-002 regla 12).
2. Un `MEDICO` autenticado recibe `403` `forbidden` en los seis endpoints, sin efectos en la base (CP-003).
3. Sin cabecera, con esquema distinto de `Bearer`, con token inválido/expirado o de un usuario inexistente ⇒ `401` (`not_authenticated` o `invalid_token`, FEAT-002 reglas 11 y 12).
4. Un `ADMIN` desactivado pierde el acceso en la siguiente request (`403` `inactive_user`), incluso si el token sigue vigente (FEAT-002 regla 13). Esto incluye al administrador que se acaba de desactivar por otra vía.
5. Todos los errores usan el envelope global `{"detail": str, "code": str, "errors": [{"field","message"}]}`; `errors` es `[]` cuando no aplica y la clave `details` no existe (FEAT-002 D-1). Los códigos `not_authenticated`, `invalid_token`, `inactive_user`, `forbidden`, `validation_error` y `user_exists` se reutilizan sin cambios. En los `422` generados por Pydantic, `field` es el `loc` sin su primer elemento cuando este es `body`, `query`, `path` o `header` (D-13): `field` vale `"size"`, `"sort"`, `"search"`, `"user_id"` o `"doctor_id"`, nunca `"query.size"` ni `"path.user_id"`.

### Listado `GET /users`

6. Parámetros: `page` (entero ≥ 1, por defecto `1`), `size` (entero 1..100, por defecto `20`), `search` (texto, máximo 100 caracteres), `is_active` (booleano opcional) y `sort` (catálogo cerrado de D-3, por defecto `username`).
7. `page`, `size` y `sort` se validan antes de consultar. `page < 1`, `size < 1`, `size > 100`, `sort` fuera del catálogo, `sort` vacío o `search` de más de 100 caracteres ⇒ `422` `validation_error` con `field` igual al nombre del parámetro.
8. `search` se recorta (`strip`). Vacío o solo espacios ⇒ **sin filtro**. Si tiene contenido, se busca de forma **case-insensitive y parcial** en `users.username` **o** `users.email` (las dos columnas son `CITEXT`), con `ILIKE` y los caracteres `%`, `_` y `\` escapados literalmente: buscar `%` encuentra solo los usuarios cuyo nombre/email contiene un `%`, nunca todos.
9. `is_active` ausente ⇒ **sin filtro** (se devuelven activos e inactivos). `is_active=true` ⇒ solo activos; `is_active=false` ⇒ solo inactivos.
10. Orden: campo del catálogo de D-3, ascendente por defecto y descendente con prefijo `-`. Siempre se añade `users.id ASC` como desempate estable, salvo cuando el campo ordenado ya es `id`. `last_login_at` usa `NULLS LAST` en ambos sentidos.
11. `total` es el número de filas que cumplen los filtros (sin `LIMIT`). `pages = ceil(total / size)` y exactamente `0` cuando `total == 0`. `page` se devuelve tal como se pidió aunque supere `pages` (en ese caso `items` es `[]`).
12. Cada elemento del listado y cada respuesta de usuario tiene **exactamente** `id`, `username`, `email`, `role` (nombre del rol), `doctor_id` e `is_active` (D-4). Nunca se devuelven `password_hash`, `role_id`, `last_login_at` ni `created_at`.

### Creación `POST /users`

13. Body: `username`, `email`, `password`, `role_id` obligatorios; `doctor_id` e `is_active` opcionales (D-11). `password` se almacena como hash Argon2id con `hash_password` y **nunca** se devuelve ni se registra.
14. `username`: se recorta (`strip`), entre 4 y 50 caracteres, sin espacios (`^\S{4,50}$`), se conserva la capitalización. `email`: se recorta, máximo 255 caracteres, forma `^[^@\s]+@[^@\s]+\.[^@\s]+$`, se conserva la capitalización. `password`: 8..128 caracteres, **sin** `strip`.
15. `role_id` debe existir en `roles` ⇒ si no existe, `400` `invalid_role`. `doctor_id`, si no es `NULL`, debe existir en `doctors` ⇒ si no existe, `400` `invalid_doctor`.
16. Coherencia rol/médico: si el rol es `ADMIN`, `doctor_id` debe ser `NULL` ⇒ `400` `admin_cannot_have_doctor`. Si el rol es `MEDICO`, `doctor_id` es opcional (puede ser `NULL`, FEAT-003 R-12) y, si se informa, no puede estar asignado a otro usuario ⇒ `409` `doctor_already_assigned`.
17. Duplicados: `username` o `email` ya existentes, comparados **case-insensitive** por `CITEXT` ⇒ `409` `user_exists`. No se modifica la fila existente (no es *upsert*).
18. El usuario se crea con `last_login_at = NULL`, `created_at`/`updated_at` por la base y el `is_active` recibido (por defecto `true`). Crear un usuario inactivo está permitido y no afecta a la regla del último administrador.
19. Respuesta `201` con la forma de D-4. Si el `commit` viola `uq_users_username`/`uq_users_email` ⇒ `409` `user_exists`; si viola `uq_users_doctor` ⇒ `409` `doctor_already_assigned` (protección de carrera, D-8).

### Detalle `GET /users/{user_id}`

20. `user_id` debe ser un entero; un valor no entero ⇒ `422` (validación de path). Un entero sin fila ⇒ `404` `user_not_found` ("El usuario no existe").
21. Devuelve `200` con la forma de D-4, incluidos los usuarios inactivos (`is_active: false`). No hay restricción adicional por estado del objetivo.

### Actualización `PUT /users/{user_id}`

22. **Reemplazo completo** (D-1): el body exige `username`, `email`, `role_id`, `doctor_id` (presente, puede ser `null`) e `is_active`. Un body parcial ⇒ `422` con `field` del campo ausente. `password` no forma parte del contrato: si se envía, se ignora y el hash **no** cambia.
23. Permite reactivar (`is_active: true`) y desactivar (`is_active: false`), aplicar las mismas validaciones de D-3 a `username`/`email` que en la creación, y cambiar de rol. `role_id` y `doctor_id` se validan con las reglas 15 y 16 **usando el rol nuevo**.
24. Los duplicados se comprueban **excluyendo al propio usuario**: reenviar su mismo `username`, `email` o `doctor_id` (con otra capitalización incluida) ⇒ `200`; los de otro usuario ⇒ `409` `user_exists` / `doctor_already_assigned`.
25. Desactivación de uno mismo: `is_active: false` sobre el usuario del token ⇒ `400` `cannot_deactivate_self` ("No puede desactivar su propio usuario"). Se evalúa **antes** que la regla del último administrador.
26. Regla del último administrador (D-6): si el objetivo es un `ADMIN` **activo** y la operación lo convertiría en no-ADMIN-activo (por `is_active: false` o por un `role_id` cuyo nombre no es `ADMIN`), y no queda ningún **otro** `ADMIN` activo, ⇒ `400` `last_admin` ("No se puede dejar el sistema sin un administrador activo"). El cambio de rol de uno mismo que deja otros administradores activos está permitido; el actor pierde el acceso a `/users` en la siguiente request (regla 4).
27. La actualización conserva `created_at` y `password_hash`, y refresca `updated_at` de forma explícita con `clock_timestamp()` (D-14), manteniendo la semántica del `onupdate` del ORM (FEAT-003 regla 17). Responde `200` con la forma de D-4.
28. Precedencia de errores: (a) `401`/`403` de autenticación; (b) `422` de Pydantic (path, query y body); (c) `404` del usuario de la ruta; (d) `400` de reglas del body (`invalid_role` → `invalid_doctor` → `admin_cannot_have_doctor` → `cannot_deactivate_self` → `last_admin`); (e) `409` de unicidad (`user_exists` → `doctor_already_assigned`); (f) escritura. Un payload con `role_id` inexistente y `username` duplicado devuelve `400` `invalid_role`.
29. La fila objetivo se bloquea con `SELECT ... FOR UPDATE` al inicio de la operación y, si la operación retira un `ADMIN` activo, se bloquea además la fila del rol `ADMIN` antes de contar (D-8). Orden de bloqueo fijo: usuario → rol.

### Desactivación `DELETE /users/{user_id}`

30. Es una **desactivación lógica**: `is_active = false`, sin `DELETE`. La fila y sus referencias (`visits.created_by`, `visits.annulled_by`) se conservan (FEAT-003 §8.1, `RESTRICT`).
31. `204 No Content` sin cuerpo y sin JSON. Starlette omite la cabecera `Content-Length` en las respuestas `204` (RFC 9110); no se añade manualmente.
32. **Idempotente**: si el usuario ya está inactivo, vuelve a responder `204`, no escribe en la base (`updated_at` no cambia) y no emite un segundo `user.deactivated`.
33. `DELETE` de sí mismo ⇒ `400` `cannot_deactivate_self` y la fila no cambia. `DELETE` de un `ADMIN` activo que dejaría el sistema sin administradores ⇒ `400` `last_admin` (defensa; ver D-6 sobre su alcanzabilidad). `DELETE` de un `user_id` inexistente ⇒ `404` `user_not_found`.
34. La comprobación del último administrador usa la misma definición y el mismo bloqueo que la regla 26.

### Restablecimiento de contraseña `POST /users/{user_id}/reset-password`

35. Body `{"new_password": "..."}`; `new_password` es obligatorio, de 8 a 128 caracteres, sin `strip`. Fuera de rango o ausente ⇒ `422` `validation_error` con `field = "new_password"`.
36. `user_id` inexistente ⇒ `404` `user_not_found`. Permitido sobre usuarios **inactivos** (no cambia `is_active`) y sobre la **propia cuenta** del `ADMIN` (D-7).
37. Reemplaza `password_hash` por `hash_password(new_password)` y refresca `updated_at`. Responde `200` con exactamente `{"message": "Contraseña restablecida correctamente"}`. Nunca devuelve la contraseña, el hash ni un token.
38. No se compara la contraseña nueva con la anterior (el servidor no conoce la anterior en claro). No se revocan tokens vigentes: la limitación D-3 de FEAT-002 sigue aplicando.
39. La contraseña anterior deja de autenticar de inmediato. Si el usuario está inactivo, el login con la nueva contraseña sigue devolviendo `403` `inactive_user` (FEAT-002 regla 5).

### Coherencia rol/médico (transversal)

40. `ADMIN` ⇒ `doctor_id IS NULL`, en creación y actualización.
41. `MEDICO` ⇒ `doctor_id` opcional. Un `MEDICO` sin médico es válido y no podrá registrar visitas hasta que se le asocie uno (FEAT-003 §8.3); esa restricción se implementa en la feature de visitas.
42. `doctor_id` es único entre usuarios (`uq_users_doctor`): un médico tiene como máximo una cuenta. El `NULL` no colisiona.
43. El médico debe existir; su `is_active` **no** se valida en esta feature (SUP-1).

### Errores y códigos estables

44. Catálogo de códigos nuevos: `user_not_found` (404), `doctor_already_assigned` (409), `invalid_role` (400), `invalid_doctor` (400), `admin_cannot_have_doctor` (400), `cannot_deactivate_self` (400), `last_admin` (400). Se reutilizan `user_exists` (409), `validation_error` (422) y los cuatro de autenticación de FEAT-002.
45. Status HTTP de la feature: `200`, `201`, `204`, `400`, `401`, `403`, `404`, `409`, `422`. Un `500` solo puede provenir del fallback del framework o de la semilla de roles ausente (D-8, nota).
46. Los mensajes de negocio van en español; los mensajes automáticos de Pydantic conservan su texto original en inglés (FEAT-002 regla 30). Los validadores propios de `username`, `email` y `new_password` usan mensajes en español.

### Registro (logs)

47. Eventos: `user.created`, `user.updated`, `user.deactivated`, `user.password_reset`; cada uno con `user_id` (objetivo) y `actor_id` (administrador del token). `user.updated` no incluye valores de campos. `user.deactivated` se emite siempre que `is_active` pase de `true` a `false`, venga de `DELETE` o de `PUT`; el resto de actualizaciones emite `user.updated`.
48. Nunca se registran `password`, `password_hash`, el prefijo `$argon2`, `email` ni `username`, tampoco en el cuerpo de la request o en mensajes de error. El middleware sigue emitiendo `request.completed` con método, ruta, status, duración y `client_ip`.
49. Un `DELETE` idempotente sobre un usuario ya inactivo no emite `user.deactivated` (no hubo cambio de estado).

## Supuestos

| ID | Supuesto | Justificación |
|---|---|---|
| **SUP-1** | No se valida `doctors.is_active` al asociar un usuario; solo su existencia. | El §6.2/§8.3 de `04-base-de-datos.md` exige que el médico exista y que la asociación sea 1:1, pero no condiciona la asociación a que esté activo. La desactivación de médicos pertenece a otra feature y endurecerlo aquí sería inventar una regla. |
| **SUP-2** | `role_id` es el identificador de `roles.id` y el cliente (frontend) lo conoce a partir del seed `ADMIN`/`MEDICO`. | Es el contrato literal del §7 (`"role_id": 2`). El listado devuelve el nombre del rol, no el `id` (D-4). Ver R-2. |
| **SUP-3** | El actor autenticado solo puede ser `ADMIN` en estos endpoints, por lo que siempre existe al menos un `ADMIN` activo durante la request. | Consecuencia de `require_role("ADMIN")` + `get_current_user` (que exige `is_active`). Determina la alcanzabilidad de `last_admin` (D-6). |

## Decisiones técnicas y desviaciones documentadas

| ID | Decisión | Justificación / impacto |
|---|---|---|
| **D-1** | `PUT /users/{user_id}` es **reemplazo completo** de `username`, `email`, `role_id`, `doctor_id` (presente y nullable) e `is_active`. | Es la semántica de `PUT` (RFC 9110) y evita la ambigüedad de "campo ausente vs. `null`" en `doctor_id`. El detalle (`GET /users/{id}`) ofrece el estado actual para reenviarlo. Un body parcial devuelve `422`, lo que hace el contrato verificable. La reactivación es explícita con `is_active: true`. `password` no se actualiza por esta vía (solo `reset-password`). |
| **D-2** | `DELETE /users/{user_id}` responde `204` sin cuerpo y es idempotente si el usuario ya está inactivo. | `204` es el código del §2 para "operación exitosa sin contenido". La idempotencia evita `409`/`404` inútiles en reintentos del cliente y no altera `updated_at`. El segundo `DELETE` no emite log de desactivación. |
| **D-3** | Listado: `page=1`, `size=20` (1..100); `sort` ∈ `username`, `email`, `created_at`, `last_login_at`, `id` con prefijo `-`; por defecto `username` ascendente; desempate `id ASC`; `last_login_at` con `NULLS LAST`; `search` case-insensitive sobre `username`/`email` con `%`/`_`/`\` escapados; `is_active` sin filtro por defecto; `pages = ceil(total/size)` o `0`. | El §6 fija los parámetros y el máximo de 100, pero no los valores por defecto ni el catálogo de orden. Se elige un orden determinista y estable (sin él, la paginación por `OFFSET` puede repetir u omitir filas). `sort` inválido ⇒ `422` en vez de ignorarse, para que el cliente detecte un error de contrato. |
| **D-4** | La respuesta de usuario (`UserRead`) tiene **exactamente** `id`, `username`, `email`, `role` (nombre), `doctor_id`, `is_active`. | Es la forma literal de la respuesta `201` del §7 y de `MeResponse` (FEAT-002). No se devuelven `password_hash` (prohibido por el §7), `role_id` (el contrato habla de `role`), `last_login_at` (no lo pide ningún documento) ni `created_at` (el CA-CRUD 4 exige **conservarlo**, no exponerlo). Se mantiene una única forma para listado, detalle, creación y actualización. |
| **D-5** | Los errores de **ruta** son `404`; las referencias inválidas del **body** son `400` (`invalid_role`, `invalid_doctor`, `admin_cannot_have_doctor`); los conflictos de unicidad son `409` (`user_exists`, `doctor_already_assigned`). Precedencia de la regla 28. | Separa "el recurso de la URL no existe" de "el payload referencia algo que no existe". `user_exists` se reutiliza del CLI/FEAT-002 para no partir el contrato. `doctor_already_assigned` es un conflicto de la restricción `uq_users_doctor`, no un error de formato. |
| **D-6** | "Último `ADMIN` activo" = único usuario con `role.name = 'ADMIN'` e `is_active = true`, **excluyendo al objetivo**. Se prohíbe la operación que dejaría **cero** administradores activos (`400 last_admin`) y la auto-desactivación (`400 cannot_deactivate_self`). Se aplica a `DELETE`, a `PUT is_active=false` y a `PUT` con rol distinto de `ADMIN`. `cannot_deactivate_self` se evalúa antes. | Es la traducción directa de las reglas del §7 y de RF-17. **Alcanzabilidad:** como el actor es siempre un `ADMIN` activo (SUP-3), para un objetivo distinto del actor el contador de "otros administradores activos" es ≥ 1, así que `last_admin` solo puede dispararse en el cambio de rol del propio actor cuando es el único `ADMIN` activo. La comprobación se implementa igualmente para todos los caminos como invariante defensivo y para cuando cambie el modelo de actores. |
| **D-7** | `reset-password` se permite sobre usuarios inactivos y sobre la propia cuenta del `ADMIN`; no exige la contraseña actual. | El §7 no restringe por estado ni por identidad. Bloquear la propia cuenta no aporta seguridad: el `ADMIN` ya puede restablecer la de cualquier otro administrador. El camino recomendado para la propia sigue siendo `POST /auth/change-password`. El restablecimiento no reactiva ni revoca tokens (FEAT-002 D-3). |
| **D-8** | Concurrencia: se bloquea la fila del usuario objetivo con `SELECT ... FOR UPDATE` y, si la operación retira un `ADMIN` activo, se bloquea la fila del rol `ADMIN` (`SELECT id FROM roles WHERE name = 'ADMIN' FOR UPDATE`) **antes** del conteo. Orden fijo usuario → rol. Los duplicados se apoyan en los índices únicos y se traducen desde `IntegrityError`. | El conteo "último admin" es check-then-update. Sin bloqueo, dos administradores podrían desactivarse mutuamente y dejar cero activos. La fila de `roles` actúa como mutex de todas las mutaciones de pertenencia a `ADMIN` (operación poco frecuente; el coste es aceptable). Los duplicados ya están garantizados por `uq_users_*`; el pre-chequeo solo mejora el mensaje. El `FOR UPDATE` del objetivo evita además dos `PUT` simultáneos sobre la misma fila. Si la fila `ADMIN` no existiera (semilla ausente), `lock_admin_role` devuelve `None` y el servicio lanza `AdminRoleMissingError` (`500 admin_role_missing`, FEAT-002) como error de configuración del servidor; el repositorio no importa excepciones de servicio. |
| **D-9** | Logs con `user_id` y `actor_id` exclusivamente; sin `username`, `email`, contraseña, hash ni valores de campos. `user.updated` no detalla el diff. | Coherente con FEAT-002 (§15: no registrar datos personales ni credenciales). `actor_id` permite rastrear quién ejecutó la operación sin escribir en `audit_log` (fuera de v1). |
| **D-10** | OpenAPI: tag `users`, `summary`, `description`, `response_model`, y `responses` con `400`, `401`, `403`, `404`, `409`, `422` según el endpoint (más `201` en el `POST` y `204` en el `DELETE`). El esquema `BearerAuth` es el de FEAT-002. | §14 del contrato y RNF-09. La seguridad aparece automáticamente al depender de `require_role("ADMIN")` → `HTTPBearer`. |
| **D-11** | En `POST /users`, `is_active` es opcional con valor por defecto `true` y `doctor_id` es opcional con valor por defecto `null`. | El ejemplo del §7 incluye `is_active: true`, y el default `true` coincide con la base (`server_default`). El §8.3 permite `MEDICO` sin médico, por lo que `doctor_id` no puede ser obligatorio para todos los roles. |
| **D-12** | `username` y `email` se recortan y **conservan** su capitalización; `password` no se recorta. Se aplican las mismas reglas que el CLI de FEAT-002 (regla 39) y que el login. | Evita divergencias entre el CLI, el login y el CRUD: `" Admin "` se guarda como `"Admin"` y el login lo encuentra por `CITEXT`. La contraseña admite espacios porque son parte del secreto. |
| **D-13** | Se amplía `_validation_field` de `app/core/exceptions.py` para descartar el primer elemento del `loc` cuando sea `body`, `query`, `path` o `header`. El envelope, `code` y mensajes de FEAT-002 no cambian. | Sin esta ampliación, un `size=0` reportaría `field = "query.size"` y un `user_id="abc"` reportaría `"path.user_id"`, lo que rompe la convención del §2 (`field` = nombre del campo) y obliga al frontend a conocer la ubicación del parámetro. Los `422` de body siguen reportando exactamente lo mismo que en FEAT-002. |
| **D-14** | `updated_at` se refresca explícitamente con `func.clock_timestamp()` en `PUT`, `DELETE` con cambio real y `reset-password`. | `UpdatedAtMixin` usa `onupdate=func.now()`; en PostgreSQL `now()` es la hora de inicio de la transacción y no cambia dentro de una transacción larga, lo que haría fallar CA-20 en la transacción de test. `clock_timestamp()` conserva la semántica "cada `UPDATE` refresca la columna" sin tocar el esquema. `created_at` y `password_hash` no se tocan, y `updated_at` no se expone en `UserRead`. |

## Modelo de datos

**No hay cambios de esquema ni migraciones nuevas.** `uv run alembic check` debe seguir imprimiendo `No new upgrade operations detected.` y `git status --porcelain alembic/` debe quedar vacío.

Columnas utilizadas (ya existentes, FEAT-003):

| Tabla | Columna | Uso en esta feature |
|---|---|---|
| `users` | `id` (`BIGINT` identity) | Identificador de ruta y de respuesta. |
| `users` | `username` (`CITEXT`, `uq_users_username`) | Creación, actualización, búsqueda, orden y duplicados case-insensitive. |
| `users` | `email` (`CITEXT`, `uq_users_email`) | Creación, actualización, búsqueda, orden y duplicados case-insensitive. |
| `users` | `password_hash` (`VARCHAR(255)`) | Escritura en `POST` y `reset-password`; nunca lectura hacia la respuesta. |
| `users` | `role_id` → `roles.name` | Entrada del contrato; se resuelve a nombre para la respuesta. |
| `users` | `doctor_id` (`BIGINT`, nullable, `uq_users_doctor`) | Asociación 1:1 con `doctors.id`; `NULL` para `ADMIN`. |
| `users` | `is_active` (`BOOLEAN`) | Desactivación lógica y filtro del listado. |
| `users` | `last_login_at` (`TIMESTAMPTZ`, nullable) | Orden del listado; no se modifica en esta feature. |
| `users` | `created_at` / `updated_at` (`TIMESTAMPTZ`) | `created_at` se conserva; `updated_at` lo refresca el servicio con `clock_timestamp()` en cada `UPDATE` (D-14). |
| `roles` | `id`, `name`, `description` | Resolución de `role_id` y bloqueo de la fila `ADMIN`. |
| `doctors` | `id` | Verificación de existencia del médico (solo lectura). |

Consultas previstas (`select()`, SQLAlchemy 2.x):

```python
# Listado (filtros opcionales) + conteo
pattern = f"%{escaped}%"  # escape de \, % y _
conditions = []
if search:
    conditions.append(
        or_(User.username.ilike(pattern, escape="\\"), User.email.ilike(pattern, escape="\\"))
    )
if is_active is not None:
    conditions.append(User.is_active.is_(is_active))
select(User).options(selectinload(User.role)).where(*conditions).order_by(*order).limit(
    size
).offset(offset)
select(func.count(User.id)).where(*conditions)

# Duplicados excluyendo al propio usuario (el filtro de id solo se añade si hay objetivo)
conditions = [or_(User.username == username, User.email == email)]
if exclude_id is not None:
    conditions.append(User.id != exclude_id)
select(User).where(*conditions).limit(1)

conditions = [User.doctor_id == doctor_id]
if exclude_id is not None:
    conditions.append(User.id != exclude_id)
select(User.id).where(*conditions).limit(1)

# Bloqueo del objetivo y del rol ADMIN
select(User).options(selectinload(User.role)).where(User.id == user_id).with_for_update()
select(Role.id).where(Role.name == "ADMIN").with_for_update()

# Conteo de otros administradores activos
select(func.count(User.id)).join(Role, Role.id == User.role_id).where(
    Role.name == "ADMIN", User.is_active.is_(True), User.id != user_id
)
```

`lazy="raise"` en `User.role` obliga a `selectinload` en todas las lecturas que construyan `UserRead` (`role.name`). Al cambiar `role_id` en un `PUT`, el servicio debe asignar también la relación (`user.role = rol_nuevo`) o recargar la fila antes de construir la respuesta, para que `role` refleje el rol nuevo y no el anterior que quedó cargado.

### Contrato de repositorio (nombres que usa el servicio)

| Función | Firma resumida |
|---|---|
| `list_users` | `(session, *, page, size, search, is_active, sort_field, sort_desc) -> tuple[list[User], int]` |
| `find_user_by_username_or_email` | `(session, *, username, email, exclude_id=None) -> User | None` |
| `find_user_by_doctor_id` | `(session, doctor_id, *, exclude_id=None) -> int | None` |
| `get_user_by_id_for_update` | `(session, user_id) -> User | None` (con `selectinload(User.role)` y `with_for_update`) |
| `get_role_by_id` | `(session, role_id) -> Role | None` |
| `lock_admin_role` | `(session) -> int | None` (id del rol `ADMIN`, o `None` si no existe; el servicio lanza `AdminRoleMissingError`) |
| `count_other_active_admins` | `(session, user_id) -> int` |
| `doctor_repository.get_doctor_by_id` | `(session, doctor_id) -> Doctor | None` |

## Contrato de API

Prefijo `/api/v1`, `Content-Type: application/json`, fechas ISO 8601 con zona (ninguna se expone en esta feature). Todos los errores usan el envelope de la regla 5.

### Autorización común

`Authorization: Bearer <token>` obligatorio, rol `ADMIN` (regla 1). Errores comunes a los seis endpoints:

| HTTP | `code` | Cuándo |
|---|---|---|
| `401` | `not_authenticated` | sin cabecera `Authorization` o esquema distinto de `Bearer` (`WWW-Authenticate: Bearer`) |
| `401` | `invalid_token` | firma inválida, token expirado, claims `sub`/`iat`/`exp` ausentes, `sub` no numérico o usuario inexistente |
| `403` | `inactive_user` | el usuario del token ya no está activo |
| `403` | `forbidden` | el usuario del token no es `ADMIN` |

### `GET /api/v1/users`

- **Query params:**

| Parámetro | Tipo | Obligatorio | Restricciones |
|---|---|---|---|
| `page` | `int` | No | ≥ 1, por defecto `1` |
| `size` | `int` | No | 1..100, por defecto `20` |
| `search` | `str` | No | máximo 100 caracteres; `strip`; vacío ⇒ sin filtro |
| `is_active` | `bool` | No | sin filtro por defecto |
| `sort` | `str` | No | `username`, `email`, `created_at`, `last_login_at`, `id` y sus formas con `-`; por defecto `username` |

- **Respuesta `200`** (`UserListResponse`):

```json
{
  "items": [
    {
      "id": 12,
      "username": "aperez",
      "email": "aperez@dispensario.local",
      "role": "MEDICO",
      "doctor_id": 5,
      "is_active": true
    }
  ],
  "total": 1,
  "page": 1,
  "size": 20,
  "pages": 1
}
```

- **Errores:** `422` `validation_error` (parámetros fuera de rango o `sort` inválido; `field` = nombre del parámetro) además de los comunes.

### `POST /api/v1/users`

- **Body** (`UserCreate`):

| Campo | Tipo | Obligatorio | Restricciones |
|---|---|---|---|
| `username` | `str` | Sí | `strip`, `^\S{4,50}$`, capitalización conservada |
| `email` | `str` | Sí | `strip`, ≤ 255, `^[^@\s]+@[^@\s]+\.[^@\s]+$` |
| `password` | `str` | Sí | 8..128, sin `strip` |
| `role_id` | `int` | Sí | debe existir en `roles` |
| `doctor_id` | `int \| null` | No (default `null`) | debe existir en `doctors`; `null` si el rol es `ADMIN` |
| `is_active` | `bool` | No (default `true`) | — |

```json
{
  "username": "aperez",
  "email": "aperez@dispensario.local",
  "password": "MiClaveSegura123",
  "role_id": 2,
  "doctor_id": 5,
  "is_active": true
}
```

- **Respuesta `201`** (`UserRead`):

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

- **Errores:**

| HTTP | `code` | Cuándo |
|---|---|---|
| `400` | `invalid_role` | `role_id` no existe |
| `400` | `invalid_doctor` | `doctor_id` no existe |
| `400` | `admin_cannot_have_doctor` | rol `ADMIN` con `doctor_id` no nulo |
| `409` | `user_exists` | `username` o `email` ya existe (CITEXT) |
| `409` | `doctor_already_assigned` | `doctor_id` ya asignado a otro usuario |
| `422` | `validation_error` | campos faltantes, longitudes, formatos, `role_id`/`doctor_id` no enteros |

Precedencia: `422` → `400` (`invalid_role` → `invalid_doctor` → `admin_cannot_have_doctor`) → `409` (`user_exists` → `doctor_already_assigned`).

### `GET /api/v1/users/{user_id}`

- **Path param:** `user_id: int`.
- **Respuesta `200`** (`UserRead`): misma forma que el `201` del `POST`; incluye usuarios inactivos.
- **Errores:** `404` `user_not_found`, `422` si `user_id` no es entero, además de los comunes.

### `PUT /api/v1/users/{user_id}`

- **Path param:** `user_id: int`.
- **Body** (`UserUpdate`, reemplazo completo — D-1):

| Campo | Tipo | Obligatorio | Restricciones |
|---|---|---|---|
| `username` | `str` | Sí | igual que en `UserCreate` |
| `email` | `str` | Sí | igual que en `UserCreate` |
| `role_id` | `int` | Sí | debe existir |
| `doctor_id` | `int \| null` | **Sí** (puede ser `null`) | debe existir; `null` si el rol nuevo es `ADMIN` |
| `is_active` | `bool` | **Sí** | — |

```json
{
  "username": "aperez",
  "email": "aperez@dispensario.local",
  "role_id": 2,
  "doctor_id": 5,
  "is_active": true
}
```

- **Respuesta `200`** (`UserRead`) con el estado resultante.
- **Errores:**

| HTTP | `code` | Cuándo |
|---|---|---|
| `400` | `invalid_role` | `role_id` no existe |
| `400` | `invalid_doctor` | `doctor_id` no existe |
| `400` | `admin_cannot_have_doctor` | rol resultante `ADMIN` con `doctor_id` no nulo |
| `400` | `cannot_deactivate_self` | el actor se envía `is_active: false` |
| `400` | `last_admin` | la operación dejaría cero `ADMIN` activos |
| `404` | `user_not_found` | `user_id` no existe |
| `409` | `user_exists` | `username`/`email` de otro usuario |
| `409` | `doctor_already_assigned` | `doctor_id` de otro usuario |
| `422` | `validation_error` | body parcial, tipos, longitudes, formatos, path no entero |

### `DELETE /api/v1/users/{user_id}`

- **Path param:** `user_id: int`.
- **Respuesta `204 No Content`** sin cuerpo. Idempotente (D-2).
- **Errores:**

| HTTP | `code` | Cuándo |
|---|---|---|
| `400` | `cannot_deactivate_self` | el actor se desactiva a sí mismo |
| `400` | `last_admin` | defensa: dejaría cero `ADMIN` activos (D-6) |
| `404` | `user_not_found` | `user_id` no existe |
| `422` | `validation_error` | `user_id` no entero |

### `POST /api/v1/users/{user_id}/reset-password`

- **Path param:** `user_id: int`.
- **Body** (`UserResetPasswordRequest`):

| Campo | Tipo | Obligatorio | Restricciones |
|---|---|---|---|
| `new_password` | `str` | Sí | 8..128, sin `strip` |

```json
{ "new_password": "Temporal12345" }
```

- **Respuesta `200`** (`MessageResponse`):

```json
{ "message": "Contraseña restablecida correctamente" }
```

- **Errores:**

| HTTP | `code` | Cuándo |
|---|---|---|
| `404` | `user_not_found` | `user_id` no existe |
| `422` | `validation_error` | `new_password` ausente o fuera de 8..128; `user_id` no entero |

### OpenAPI

Cada endpoint declara `summary`, `description`, `response_model` (salvo el `204`), `responses` con los códigos de su tabla y el tag `users`. El esquema `BearerAuth` ya existe (FEAT-002) y la seguridad es obligatoria en los seis endpoints.

## Criterios de aceptación

Convención: `[shell]` = comando ejecutable con salida esperada; `[test]` = aserción exacta que el reviewer implementa en `tests/**`. Los comandos se ejecutan desde la raíz del repo con la base migrada (`uv run alembic upgrade head`). Los tests de integración reutilizan `db_session`, `db_conn`, `auth_client`/`auth_client_factory` y `auth_support` de FEAT-002 (ver "Plan de pruebas").

### Autorización y contrato

- [ ] **CA-1** `[test]` Con token de un `MEDICO` activo, los seis endpoints (`GET /users`, `POST /users`, `GET/PUT/DELETE /users/{id}`, `POST /users/{id}/reset-password`) devuelven `403`, `code == "forbidden"`, `errors == []` y claves exactas `{detail, code, errors}`; la base no cambia (se compara el `password_hash` y el `is_active` del objetivo antes y después).
- [ ] **CA-2** `[test]` Sin cabecera ⇒ `401` `not_authenticated` y `WWW-Authenticate` empieza por `Bearer`; con `Authorization: Basic abc` ⇒ igual; con token basura o firmado con otro secreto ⇒ `401` `invalid_token`; con token de un `ADMIN` desactivado ⇒ `403` `inactive_user` (sobre `GET /users` y `POST /users`).
- [ ] **CA-3** `[test]` `GET /users/{id_inexistente}` ⇒ `404`, claves exactas `{detail, code, errors}`, `code == "user_not_found"`, `errors == []`, `detail == "El usuario no existe"`; la respuesta **no** contiene la clave `details` (cierra la deuda CA-45 de FEAT-002 sobre un `404` real).

### Listado

- [ ] **CA-4** `[test]` Sin parámetros: `page == 1`, `size == 20`, claves de nivel superior exactamente `{items, total, page, size, pages}`, `total == SELECT count(*) FROM users` en la misma transacción, `len(items) == min(20, total)` y `pages == ceil(total / 20)`.
- [ ] **CA-5** `[test]` Límites: `size=1` y `size=100` ⇒ `200`; `size=0`, `size=101`, `size=-1`, `page=0`, `page=-1` ⇒ `422` con `field` igual a `"size"`/`"page"`; `page=9999` ⇒ `200`, `items == []`, `page == 9999`, `total` y `pages` sin cambios respecto a `page=1`.
- [ ] **CA-6** `[test]` Con un conjunto de usuarios cuyo `username` empieza por un sufijo único (filtrado con `search`): `sort=username` ordena ascendentemente; `sort=-username` descendentemente; `sort=email`, `sort=-email`, `sort=created_at`, `sort=-created_at`, `sort=id`, `sort=-id` responden `200` y respetan el orden; `sort=-last_login_at` deja los `NULL` al final; `sort=password_hash`, `sort=username,email` y `sort=` ⇒ `422` con `field == "sort"`; sin `sort` el orden es `username ASC`; con `sort=created_at` y dos usuarios insertados con el mismo `created_at`, el desempate es `id ASC`.
- [ ] **CA-7** `[test]` `search` es case-insensitive y parcial en `username` **y** `email`: `search=<mitad en mayúsculas>` encuentra el usuario; `search=%` devuelve solo usuarios con `%` literal en `username`/`email` (se crean `ab%cd` y un `abcd` de control) y `search=_` solo los que contienen `_`; `search=\` no rompe y busca la barra literal; `search="   "` no aplica filtro (`total` igual al de la petición sin `search`); `search` de 101 caracteres ⇒ `422` con `field == "search"`.
- [ ] **CA-8** `[test]` `is_active=true` devuelve solo activos, `is_active=false` solo inactivos y la ausencia del parámetro devuelve ambos; combinado con `search`, los filtros se aplican en conjunto.
- [ ] **CA-9** `[test]` `search` sin coincidencias ⇒ `items == []`, `total == 0`, `pages == 0` y `page`/`size` con los valores pedidos.
- [ ] **CA-10** `[test]` Cada `item` tiene exactamente `{id, username, email, role, doctor_id, is_active}`; ninguna respuesta (listado, detalle, creación o actualización) contiene `password_hash`, `role_id`, `last_login_at`, `created_at` ni `password`.

### Creación

- [ ] **CA-11** `[test]` `POST /users` con rol `MEDICO`, `doctor_id` existente e `is_active` omitido ⇒ `201`, cuerpo exacto de seis claves con `role == "MEDICO"`, `doctor_id` correcto e `is_active is True`; en base: `password_hash LIKE '$argon2id$%'`, `role_id` correcto, `created_at`/`updated_at` no nulos y `last_login_at IS NULL`; el login con esa contraseña devuelve `200` y `GET /me` devuelve `role == "MEDICO"`.
- [ ] **CA-12** `[test]` `POST` con rol `ADMIN` y `doctor_id` omitido ⇒ `201` con `doctor_id is None`; `POST` con `is_active=false` ⇒ `201` con `is_active is False`, la fila nace inactiva y el login con esa contraseña devuelve `403` `inactive_user`.
- [ ] **CA-13** `[test]` Duplicado de `username` con otra capitalización ⇒ `409` `user_exists`; duplicado solo de `email` con otra capitalización y otro `username` ⇒ `409` `user_exists`; en ambos casos `SELECT count(*) FROM users WHERE username = :u` es `1` y el `password_hash` original no cambia.
- [ ] **CA-14** `[test]` `POST` con un `doctor_id` ya asignado a otro usuario ⇒ `409` `doctor_already_assigned` y el usuario nuevo **no** se crea.
- [ ] **CA-15** `[test]` `POST` con rol `ADMIN` y `doctor_id` no nulo ⇒ `400` `admin_cannot_have_doctor` y no se crea la fila.
- [ ] **CA-16** `[test]` `POST` con `role_id` inexistente ⇒ `400` `invalid_role`; con `doctor_id` inexistente ⇒ `400` `invalid_doctor`; con `role_id` inexistente **y** `username` duplicado ⇒ `400` `invalid_role` (precedencia de D-5).
- [ ] **CA-17** `[test]` `422` con `field` correcto: `password` de 7 caracteres, `password` de 129, `username` de 3, `username` de 51, `username` con espacio (`"ab cd"`), `email` sin `@`, `email` de 256 caracteres, body `{}` (cuatro ítems: `username`, `email`, `password`, `role_id`). `password` de 8 y de 128 ⇒ `201`. `username`/`email` enviados como `"  Aperez  "` / `"  X@Y.TEST  "` se almacenan como `"Aperez"` / `"X@Y.TEST"` (recortados y con capitalización conservada).
- [ ] **CA-18** `[test]` La respuesta del `201` no contiene la contraseña ni el hash; con `caplog` a `INFO`, el log de la creación incluye `user.created` con `user_id` y `actor_id` y **no** contiene la contraseña, el prefijo `$argon2`, el `email` ni el `username`.

### Detalle

- [ ] **CA-19** `[test]` `GET /users/{id}` ⇒ `200` con la misma forma y los mismos valores que el `item` del listado; un usuario inactivo devuelve `200` con `is_active is False`; un `id` no entero (`"abc"`) ⇒ `422` con `field == "user_id"`.

### Actualización

- [ ] **CA-20** `[test]` `PUT` completo sobre un `MEDICO` (nuevo `username`, nuevo `email`, `role_id` de `ADMIN`, `doctor_id: null`, `is_active: true`) ⇒ `200` con el estado nuevo; en base cambian `username`, `email` y `role_id`; `created_at` **no** cambia, `updated_at` sí y `password_hash` permanece idéntico; el login con la contraseña original sigue devolviendo `200`.
- [ ] **CA-21** `[test]` `PUT` con `is_active: true` sobre un usuario inactivo ⇒ `200`, `is_active is True`; el login pasa de `403` `inactive_user` a `200`.
- [ ] **CA-22** `[test]` `PUT` con `is_active: false` sobre otro usuario ⇒ `200`, `is_active is False`; el login devuelve `403` `inactive_user` y un token emitido antes de la desactivación recibe `403` `inactive_user` al llamar a `GET /users`.
- [ ] **CA-23** `[test]` `PUT` del actor sobre sí mismo con `is_active: false` ⇒ `400` `cannot_deactivate_self`, la fila conserva `is_active = true` y el `password_hash` no cambia.
- [ ] **CA-24** `[test]` Con el actor como **único** `ADMIN` activo (el test desactiva dentro de la transacción a cualquier otro `ADMIN`): `PUT` sobre sí mismo con el `role_id` de `MEDICO` e `is_active: true` ⇒ `400` `last_admin` y el rol sigue siendo `ADMIN`; con un segundo `ADMIN` activo, la misma operación ⇒ `200` y el rol cambia a `MEDICO`.
- [ ] **CA-25** `[test]` `PUT` con el `username` de otro usuario cambiando solo la capitalización ⇒ `409` `user_exists`; con el `email` de otro ⇒ `409` `user_exists`; con el `doctor_id` de otro ⇒ `409` `doctor_already_assigned`; reenviando sus propios `username`, `email` y `doctor_id` ⇒ `200`.
- [ ] **CA-26** `[test]` `PUT` con `role_id` inexistente ⇒ `400` `invalid_role`; `doctor_id` inexistente ⇒ `400` `invalid_doctor`; rol `ADMIN` con `doctor_id` no nulo ⇒ `400` `admin_cannot_have_doctor`; `user_id` inexistente ⇒ `404` `user_not_found`; body sin `is_active` ⇒ `422` con `field == "is_active"` (reemplazo completo, D-1); `PUT` con `"password"` extra ⇒ se ignora y el hash no cambia.
- [ ] **CA-27** `[test]` Desactivar (`is_active: false`) a otro `ADMIN` cuando el actor y un tercero siguen activos ⇒ `200` y el objetivo queda inactivo; el conteo de administradores activos baja en uno (verifica que la comprobación excluye al objetivo y no bloquea de más).

### Desactivación

- [ ] **CA-28** `[test]` `DELETE /users/{id}` de un usuario activo ajeno ⇒ `204`, `response.content == b""`; la fila sigue existiendo con `is_active = false`; el login devuelve `403` `inactive_user`; el log contiene `user.deactivated` con `user_id` y `actor_id`.
- [ ] **CA-29** `[test]` Segundo `DELETE` sobre el mismo usuario ⇒ `204`, `is_active` sigue `false`, `updated_at` no cambia y no se emite un segundo `user.deactivated`; `DELETE` de un `id` inexistente ⇒ `404` `user_not_found`; `DELETE` del propio actor ⇒ `400` `cannot_deactivate_self` y el actor sigue activo.

### Restablecimiento de contraseña

- [ ] **CA-30** `[test]` `POST /users/{id}/reset-password` con 8 caracteres ⇒ `200` y cuerpo exactamente `{"message": "Contraseña restablecida correctamente"}`; el `password_hash` cambia y empieza por `$argon2id$`; el login con la contraseña anterior ⇒ `401`; con la nueva ⇒ `200`; la respuesta no contiene la contraseña.
- [ ] **CA-31** `[test]` Reset sobre un usuario inactivo ⇒ `200` y el hash cambia; el login con la contraseña nueva sigue devolviendo `403` `inactive_user` (`is_active` no cambia).
- [ ] **CA-32** `[test]` Reset sobre la propia cuenta del actor ⇒ `200` (D-7) y el login con la contraseña nueva ⇒ `200`.
- [ ] **CA-33** `[test]` Reset con `user_id` inexistente ⇒ `404` `user_not_found`; `new_password` de 7 y de 129 caracteres, o ausente ⇒ `422` con `field == "new_password"`; reset con token de `MEDICO` ⇒ `403` `forbidden` y el hash no cambia.

### Concurrencia y errores de integridad

- [ ] **CA-34** `[test]` Con espías sobre el repositorio, un `PUT` que retira un `ADMIN` activo invoca en orden `get_user_by_id_for_update` → `lock_admin_role` → `count_other_active_admins`; un `PUT` que no retira un `ADMIN` (p. ej. cambiar el email de un `MEDICO`) **no** invoca `lock_admin_role`. `DELETE` de un `ADMIN` activo sigue el mismo orden.
- [ ] **CA-35** `[test]` Un `IntegrityError` inyectado en el `commit` con el nombre `uq_users_username` o `uq_users_email` se traduce a `409` `user_exists`; con `uq_users_doctor`, a `409` `doctor_already_assigned`; en ambos casos no queda fila nueva y la sesión sigue usable.
- [ ] **CA-36** `[test]` Todos los errores de la feature respetan el envelope exacto `{detail, code, errors}` (sin `details`) y los códigos del catálogo de la regla 44 para cada escenario de las tablas de contrato (al menos un caso por código).

### Logs

- [ ] **CA-37** `[test]` Con `caplog` a `INFO`, tras crear, actualizar, desactivar y resetear: aparecen `user.created`, `user.updated`, `user.deactivated` y `user.password_reset`, cada uno con `user_id` y `actor_id`; `caplog.text` **no** contiene la contraseña en claro, ni `$argon2`, ni el `email`, ni el `username` del objetivo, ni el `password_hash`.

### OpenAPI y calidad

- [ ] **CA-38** `[shell]` OpenAPI:

  ```powershell
  uv run python -c "from app.main import app; o=app.openapi(); p=o['paths']; u=sorted(k for k in p if k.startswith('/api/v1/users')); print(u); print(sorted({t for k in u for m in p[k].values() for t in m.get('tags', [])})); print(all(m.get('security') and m.get('summary') and m.get('description') for k in u for m in p[k].values())); print('content' in p['/api/v1/users']['get']['responses']['200'], 'content' in p['/api/v1/users']['post']['responses']['201']); print(sorted(p['/api/v1/users']['post']['responses'])); print(sorted(p['/api/v1/users/{user_id}']['delete']['responses']))"
  ```

  imprime:

  ```text
  ['/api/v1/users', '/api/v1/users/{user_id}', '/api/v1/users/{user_id}/reset-password']
  ['users']
  True
  True True
  ['201', '400', '401', '403', '409', '422']
  ['204', '400', '401', '403', '404', '422']
  ```

  Además, `GET /api/v1/users` declara `['200', '401', '403', '422']`, `GET /api/v1/users/{user_id}` declara `['200', '401', '403', '404', '422']`, `PUT` declara `['200', '400', '401', '403', '404', '409', '422']` y `reset-password` declara `['200', '401', '403', '404', '422']`.

- [ ] **CA-39** `[shell]` Sin cambios de esquema: `uv run alembic check` imprime `No new upgrade operations detected.` y `git status --porcelain alembic/` no imprime nada.
- [ ] **CA-40** `[shell]` `uv run ruff check .` sin errores y `uv run ruff format --check .` sin cambios pendientes.
- [ ] **CA-41** `[shell]` `uv run pytest -q` en verde, incluidos `tests/test_health.py`, los tests de FEAT-002 y los de FEAT-003 (sin regresiones).

### Trazabilidad

| Requisito / fuente | Criterios |
|---|---|
| RF-03 / US-10 / §7 (crear, editar, desactivar, roles) | CA-1, CA-4 a CA-29 |
| RF-10 / §8.3 (asociación usuario-médico, 1:1) | CA-11, CA-14, CA-15, CA-25, CA-26 |
| RF-17 / CA-CRUD 5 (baja lógica, sin borrado físico) | CA-28, CA-29 |
| RF-18 / CA-CRUD 3, 9 (validaciones y mensajes) | CA-13, CA-17, CA-33 |
| RF-20 / US-11 / §7 (`reset-password`) | CA-30 a CA-33, CA-37 |
| RF-02 / §4 / CA-CRUD 8 / CP-003 (solo ADMIN) | CA-1, CA-2, CA-33 |
| §2 (paginación `items/total/page/size/pages`, envelope) | CA-3, CA-4, CA-5, CA-9, CA-36 |
| §6 (`page`, `size`, `search`, `is_active`, `sort`) | CA-4 a CA-10 |
| §15 (no registrar credenciales) | CA-18, CA-37 |
| §16 / §14 (OpenAPI) | CA-38 |
| RF-22 / `audit_log` | Fuera de alcance (declarado) |

## Plan de pruebas

### Aislamiento

Se reutiliza la infraestructura de FEAT-002 sin cambios:

1. `db_conn` (transacción revertida) y `db_session` (`join_transaction_mode="create_savepoint"`) de `tests/conftest.py`; ninguna escritura sobrevive al test.
2. `auth_client` / `auth_client_factory` sobrescriben `get_session` con `db_session`; limpiar en `finally` (ya lo hace la fixture).
3. Los usuarios se crean con `tests/auth_support.create_auth_user` (hash Argon2id real) y los tokens con `tests/auth_support.token_for(user_id, role=..., doctor_id=...)` + `auth_headers`. Para médicos, `tests/db_support.create_doctor`.
4. Las lecturas de verificación usan `db_conn` (misma conexión ⇒ ve los cambios no confirmados de `db_session`).
5. Cada test crea su propio `ADMIN` actor; no se depende de un administrador preexistente ni del estado del listado completo (las aserciones de `total` se comparan contra `SELECT count(*) FROM users` dentro de la transacción).
6. Para el escenario del último administrador, el test neutraliza dentro de la transacción a cualquier otro `ADMIN` activo (`UPDATE users SET is_active = false WHERE role_id = (SELECT id FROM roles WHERE name = 'ADMIN') AND id <> :actor`) y lo restaura con el rollback final. No se borra ningún rol ni usuario.
7. No se usan datos personales reales: `username`/`email` sintéticos con `unique_suffix()`.
8. `get_login_rate_limiter()` se reinicia por la fixture `autouse` existente; estos tests no deben agotar el límite de login (máximo 2 logins por test, o `reset_all()` explícito cuando se superen 5).

### Unitarias (sin base de datos)

| # | Test | CA |
|---|---|---|
| U-1 | `UserCreate`: límites de `username` (4/50), ausencia de espacios, email (255, forma), password (8/128), defaults `doctor_id=None`/`is_active=True`, `strip` y capitalización | CA-17 |
| U-2 | `UserUpdate`: los cinco campos son obligatorios, `doctor_id` acepta `null`, `is_active` obligatorio | CA-26 |
| U-3 | `UserResetPasswordRequest`: 8..128, sin `strip` | CA-33 |
| U-4 | `UserRead`: serializa exactamente las seis claves | CA-10 |
| U-5 | Catálogo de `sort`: las 10 variantes válidas y el rechazo de inválidas (incluida la cadena vacía) | CA-6 |
| U-6 | Función de escape de `LIKE`: `%`, `_` y `\` se convierten en literales | CA-7 |
| U-7 | Cálculo de `pages`: `0` con `total=0`, techo con resto y exacto sin resto | CA-4, CA-9 |
| U-8 | Orden de comprobaciones del servicio con espías: rol → médico → coherencia → auto-desactivación → último admin → duplicados | CA-16, CA-34 |

### Integración (PostgreSQL + httpx)

| # | Test | CA |
|---|---|---|
| I-1 | `MEDICO` en los seis endpoints: `403` y sin escritura | CA-1 |
| I-2 | Sin token / esquema ajeno / token inválido / `ADMIN` inactivo | CA-2 |
| I-3 | `404` con envelope y sin `details` | CA-3 |
| I-4 | Defaults y fórmula de paginación | CA-4 |
| I-5 | Límites de `page`/`size` y página fuera de rango | CA-5 |
| I-6 | Los 5 campos de `sort` en ambos sentidos, `NULLS LAST`, desempate e inválidos | CA-6 |
| I-7 | `search` case-insensitive, parcial, escapado de comodines y longitud | CA-7 |
| I-8 | Filtro `is_active` y combinación con `search` | CA-8 |
| I-9 | Sin resultados: `pages == 0` | CA-9 |
| I-10 | Forma exacta de `item` en las cuatro respuestas con usuario | CA-10, CA-19 |
| I-11 | Creación `MEDICO` feliz + login + `/me` | CA-11 |
| I-12 | Creación `ADMIN` y creación inactiva | CA-12 |
| I-13 | Duplicados `CITEXT` de `username` y `email` | CA-13 |
| I-14 | `doctor_id` ya asignado | CA-14 |
| I-15 | `ADMIN` con `doctor_id` | CA-15 |
| I-16 | `role_id`/`doctor_id` inexistentes y precedencia | CA-16 |
| I-17 | Validaciones de `UserCreate` y normalización `strip` | CA-17 |
| I-18 | `PUT` feliz con conservación de `created_at` y `password_hash` | CA-20 |
| I-19 | `PUT` reactiva y desactiva | CA-21, CA-22 |
| I-20 | `PUT` auto-desactivación | CA-23 |
| I-21 | `PUT` último administrador (self-demotion) | CA-24 |
| I-22 | `PUT` duplicados propios vs. ajenos | CA-25 |
| I-23 | `PUT` errores de body, `404` y body parcial | CA-26 |
| I-24 | `PUT` desactivar a otro `ADMIN` con tercero activo | CA-27 |
| I-25 | `DELETE` feliz, idempotente, `404` y auto-`DELETE` | CA-28, CA-29 |
| I-26 | Reset feliz y contraseñas | CA-30 |
| I-27 | Reset sobre inactivo y sobre la propia cuenta | CA-31, CA-32 |
| I-28 | Reset: `404`, longitudes y `MEDICO` | CA-33 |
| I-29 | Envelope uniforme por cada código del catálogo | CA-36 |

### Concurrencia e integridad

| # | Test | CA |
|---|---|---|
| C-1 | Orden de bloqueo con espías (`get_user_by_id_for_update` → `lock_admin_role` → `count_other_active_admins`) y ausencia del bloqueo cuando no se retira un `ADMIN` | CA-34 |
| C-2 | Traducción de `IntegrityError` por nombre de restricción (`uq_users_username`, `uq_users_email`, `uq_users_doctor`) | CA-35 |

> La prueba de dos transacciones reales compitiendo por el mismo rol queda fuera de la suite automática (requeriría una segunda conexión y limpieza manual). La mitigación de D-8 se verifica estructuralmente en C-1 y se documenta como riesgo residual R-5.

### Logs, OpenAPI y calidad

| # | Test | CA |
|---|---|---|
| S-1 | Los cuatro eventos con `user_id`/`actor_id` y sin secretos ni identificadores | CA-18, CA-37 |
| O-1 | OpenAPI: paths, tag, seguridad, `summary`/`description` y códigos | CA-38 |
| Q-1 | `alembic check` y `git status --porcelain alembic/` | CA-39 |
| Q-2 | `ruff check` y `ruff format --check` | CA-40 |
| Q-3 | `pytest -q` completo | CA-41 |

## Riesgos y dependencias

| ID | Riesgo | Impacto | Mitigación / decisión |
|---|---|---|---|
| **R-1** | `last_admin` solo es alcanzable en la autodemoción de rol cuando el actor es el único `ADMIN` activo; para objetivos distintos nunca se dispara porque el actor siempre es un `ADMIN` activo. | Bajo | Se implementa como invariante defensivo (D-6) y se verifica con CA-24. Si en el futuro el actor puede no ser administrador (p. ej. un proceso interno), la regla ya cubre el caso. |
| **R-2** | El contrato devuelve `role` (nombre) pero la entrada es `role_id`; no hay `GET /roles` en el alcance. El frontend necesita conocer los `id` del seed. | Medio | SUP-2: el seed `ADMIN`/`MEDICO` de FEAT-003 es fijo y sus `id` son estables por despliegue. Si el frontend no puede asumirlos, se añade un catálogo de roles en una feature posterior sin romper este contrato. |
| **R-3** | `PUT` es reemplazo completo: un cliente que envíe solo un campo recibe `422`; no hay bloqueo optimista, dos `PUT` concurrentes se pisan (last write wins). | Medio | D-1 y D-8 (bloqueo de fila serializa las escrituras, pero no detecta versiones). El frontend debe hacer `GET` antes de `PUT`. Se acepta en v1; una columna de versión sería un cambio de esquema. |
| **R-4** | Restablecer la contraseña no revoca tokens vigentes: una sesión abierta del usuario sigue activa hasta `exp`. | Medio | Limitación heredada de FEAT-002 (D-3/ADR-003), aceptada. Expiración de 30 min y HTTPS en despliegue. |
| **R-5** | `FOR UPDATE` sobre `roles` serializa todas las mutaciones de administradores; con muchas operaciones concurrentes puede haber esperas. | Bajo | Las mutaciones de usuarios son de baja frecuencia (RNF-05: 20 usuarios concurrentes). El bloqueo es de una sola fila y de duración de una transacción corta. |
| **R-6** | `ILIKE` con comodines escapados y `CITEXT`: un escape mal construido podría convertir `%` en comodín y permitir búsquedas no intencionadas. | Bajo | Escapado explícito de `\`, `%` y `_` con `escape="\\"`; CA-7 lo verifica con usuarios que contienen los tres caracteres. |
| **R-7** | Desactivar un usuario con visitas no borra nada (la baja es lógica) y `visits.created_by`/`annulled_by` usan `RESTRICT`; un `DELETE` físico accidental fallaría, pero no está implementado. | Bajo | La feature nunca emite `DELETE`; CA-28 verifica que la fila persiste. |
| **R-8** | Un `ADMIN` puede restablecer su propia contraseña sin conocer la actual (D-7), lo que debilita el flujo de cambio propio. | Bajo | Exige un token de `ADMIN` válido; el camino recomendado sigue siendo `change-password`. Se documenta; si el humano lo prohíbe, basta devolver `400` sobre `user_id == actor.id`. |
| **R-9** | No se valida `doctors.is_active` al asociar (SUP-1): se puede asignar un médico inactivo. | Bajo | El documento no lo exige. La feature de médicos/visitas decidirá si un médico inactivo puede registrar visitas. |
| **R-10** | Semilla de roles ausente ⇒ `AdminRoleMissingError` (`500 admin_role_missing`). | Bajo | Error de configuración detectado en el primer `POST`/`PUT`; la migración `seed_roles` de FEAT-003 lo previene. |
| **R-11** | El listado completo puede ser costoso si la tabla crece mucho y se usa `OFFSET`. | Bajo | `size` máximo 100, índices únicos de `username`/`email` y `LIMIT/OFFSET`; el volumen esperado son decenas de usuarios. Para volúmenes mayores, paginación por cursor en otra feature. |
| **R-12** | El test del último administrador manipula el estado de otros administradores dentro de la transacción; un fallo de aislamiento podría filtrarse. | Bajo | Todo ocurre en la transacción revertida de `db_conn`; no se borran roles ni usuarios y no se ejecuta contra la base real. |

**Dependencias**

- **FEAT-001 (`done`)**: FastAPI, sesión async, logging con redacción, envelope y middlewares.
- **FEAT-003 (`done`)**: tablas `users`, `roles`, `doctors`, `CITEXT`, `uq_users_username`, `uq_users_email`, `uq_users_doctor`, `ix_users_role_id` y seed `ADMIN`/`MEDICO`.
- **FEAT-002 (`done`)**: `hash_password`, `get_current_user`, `require_role`, `MessageResponse`, envelope `{detail, code, errors}` y `AdminRoleMissingError`.
- **Bloquea**: la feature de médicos (`/doctors`) podrá reutilizar este CRUD como referencia, pero no depende de él; la de visitas depende de que existan usuarios `MEDICO` asociados a médicos (RF-10), que esta feature ya permite crear por API.
- **No modifica** `app/core/config.py`, `app/api/deps.py`, `app/core/security.py`, `alembic/` ni dependencias de `pyproject.toml`.

**Datos sensibles**

- `password_hash` nunca se devuelve, imprime ni registra; las contraseñas solo viven en memoria durante el hash.
- `email` y `username` no se registran en logs (el `email` además está en la lista de redacción del logger).
- Los tests usan usuarios sintéticos dentro de una transacción revertida; no se usan datos personales reales.
- `audit_log` no se escribe en esta feature (RF-22 fuera de v1); el rastro queda en los eventos estructurados.

## Preguntas abiertas

Ninguna que bloquee la implementación. Las ambigüedades del contrato se cerraron con decisiones verificables:

- `PUT` completo (D-1), `DELETE` `204` idempotente (D-2) y catálogo de `sort`/búsqueda (D-3) son decisiones de contrato, no reglas de negocio nuevas.
- La alcanzabilidad de `last_admin` (D-6) y la ausencia de validación de `doctors.is_active` (SUP-1) se documentan como comportamiento explícito; si el humano prefiere otra semántica, se cambia sin afectar al resto del contrato.
- La obtención de `role_id` por el frontend (R-2) es una limitación del contrato del §7, no un bloqueo del backend.

## Historial

- 2026-10-21 — creada por spec-author a partir de `DispensarioMedicoDocs/03-api-rest.md` §2, §4–§7, §15 y §16; `01-requerimientos-y-backlog.md` RF-03, RF-10, RF-17, RF-18, RF-20, RF-22, US-10, US-11, §3 y §7; `04-base-de-datos.md` §6.2, §8.3 y §9.2; `05-plan-de-pruebas.md` CP-003 y §7; `02-arquitectura.md` §9; y el código de FEAT-002/FEAT-003.
- 2026-10-21 — proposed → approved (humano).
- 2026-10-21 — Cierre de revisión: se documentan D-14 (`updated_at` con `clock_timestamp()`) y la regla 31 (204 sin `Content-Length`, RFC 9110) tras verificar la implementación y la suite completa (257 tests).
