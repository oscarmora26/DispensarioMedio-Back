# FEAT-007 — Gestión de tipos de fármacos: CRUD, permisos y desactivación lógica

**Estado:** `approved` (aprobada explícitamente por el humano el 2026-10-21)
**Fecha:** 2026-10-21
**Alcance:** `GET/POST /api/v1/drug-types`, `GET/PUT/DELETE /api/v1/drug-types/{drug_type_id}`, con lectura para `ADMIN` y `MEDICO` y escritura solo para `ADMIN`, reutilizando autenticación JWT, envelope de error `{detail, code, errors}` y desactivación lógica de FEAT-002/FEAT-004.
**Fuente de verdad funcional:** `DispensarioMedicoDocs/01-requerimientos-y-backlog.md` RF-04, US-07, E2, §3 (matriz: Tipos de fármacos CRUD ADMIN / Consulta MEDICO) y §7 (CA-CRUD); `DispensarioMedicoDocs/03-api-rest.md` §2 (paginación, envelope, códigos), §4 (matriz `/drug-types`: CRUD ADMIN / GET MEDICO), §5 (endpoints), §6 (parámetros de listado) y §8 (`POST /drug-types {"description","is_active"}`, descripciones únicas).

## Contexto

La tabla `drug_types` ya existe (FEAT-003, migración `5ae35a01c3f1_catalog_schema.py`): `description CITEXT` con `uq_drug_types_description`, `is_active BOOLEAN DEFAULT true`, y relación `medications` (FK `medications.drug_type_id → drug_types.id ON DELETE RESTRICT`). Hoy **no existe ninguna vía HTTP para crear, consultar, actualizar o desactivar tipos de fármacos**.

Sin esta feature, RF-04 (gestionar tipos de fármacos) y US-07 (CRUD de tipos de fármacos, Sprint 1) quedan sin implementar, y las futuras features de medicamentos (US-20, que exige `drug_type_id`) no tienen cómo poblar el catálogo por API. Es además el primer catálogo maestro (E2) y sirve de patrón para `/brands` y `/locations`.

## Alcance

- `app/schemas/drug_type.py` (nuevo): `DrugTypeCreate`, `DrugTypeUpdate`, `DrugTypeRead`, `DrugTypeListResponse`.
- `app/repositories/drug_type_repository.py` (nuevo): listado paginado con búsqueda/filtros/orden, `get_drug_type_by_id`, detección de duplicados case-insensitive (CITEXT).
- `app/services/drug_type_service.py` (nuevo): `list_drug_types`, `get_drug_type`, `create_drug_type`, `update_drug_type`, `deactivate_drug_type`, traducción de `IntegrityError` (`uq_drug_types_description` → `409`).
- `app/api/v1/routes/drug_types.py` (nuevo) con tag `drug-types`, registrado en `app/api/v1/router.py`.
- Reutilización de: `app/api/deps.py` (`get_current_user`, `require_role`), `app/core/exceptions.py` (`AppError`, `NotFoundError`, `ConflictError`, envelope; `_validation_field` de FEAT-004 D-13 ya descarta prefijos `query`/`path`/`header`), `app/models/drug_type.py`.
- Cinco endpoints descritos en "Contrato de API".
- Logs estructurados `drug_type.created`, `drug_type.updated`, `drug_type.deactivated`.
- Documentación OpenAPI completa (tag, `summary`, `description`, `response_model`, códigos).

### Estructura de archivos resultante

```text
app/
├── api/v1/
│   ├── router.py                 # modificado: incluye drug_types
│   └── routes/drug_types.py      # nuevo
├── repositories/
│   └── drug_type_repository.py   # nuevo
├── schemas/drug_type.py           # nuevo
└── services/drug_type_service.py  # nuevo
```

## Fuera de alcance

- CRUD de `/brands`, `/locations`, `/medications`, `/patients`, `/doctors`, `/visits`: esta feature solo deja `drug_types` consultable como prerrequisito de medicamentos (US-20).
- `GET /drug-types` filtrado por "solo activos" obligatorio: el filtro `is_active` es opcional; la restricción "no crear medicamentos con catálogos inactivos" pertenece a la feature de medicamentos (03-api-rest §8).
- Borrado físico de tipos: la única baja es lógica (`is_active = false`). No hay `DELETE` SQL en ningún camino.
- Bloqueo de desactivar un tipo con medicamentos asociados: la desactivación lógica **está permitida** aunque existan `medications` (el FK es `RESTRICT` solo contra borrado físico; ver D-7).
- Reactivación por endpoint dedicado: se reactiva con `PUT {"is_active": true}` (reemplazo completo, D-1).
- Paginación por cursor, exportación, `audit_log` (RF-22 fuera de v1), refresh tokens, cambios de esquema, migraciones, `docker-compose.yml`, `Dockerfile`, `.env.example` o settings nuevas. `uv run alembic check` debe seguir limpio.
- CORS, frontend Angular (tabla, formulario, toasts), pruebas E2E.
- Ordenación por campos fuera del catálogo de D-3.

## Reglas de negocio

### Autorización y alcance

1. Lectura (`GET /drug-types`, `GET /drug-types/{id}`): `Authorization: Bearer <token>` y rol `ADMIN` **o** `MEDICO`, leído de la base (`user.role.name`) mediante `require_role("ADMIN", "MEDICO")`. El claim `role` del token no se usa (FEAT-002 regla 12).
2. Escritura (`POST`, `PUT`, `DELETE`): rol `ADMIN` exclusivamente, mediante `require_role("ADMIN")`. Un `MEDICO` autenticado recibe `403` `forbidden` en las tres, sin efectos en la base (CA-CRUD 8).
3. Sin cabecera, con esquema distinto de `Bearer`, con token inválido/expirado o de un usuario inexistente ⇒ `401` (`not_authenticated` o `invalid_token`, FEAT-002 reglas 11 y 12).
4. Un `ADMIN` o `MEDICO` desactivado pierde el acceso en la siguiente request (`403` `inactive_user`), incluso si el token sigue vigente (FEAT-002 regla 13).
5. Todos los errores usan el envelope global `{"detail": str, "code": str, "errors": [{"field","message"}]}`; `errors` es `[]` cuando no aplica y la clave `details` no existe (FEAT-002 D-1, FEAT-004 D-13). Códigos nuevos: `drug_type_not_found` (404), `drug_type_exists` (409). Se reutilizan `not_authenticated` / `invalid_token` (401), `inactive_user` / `forbidden` (403) y `validation_error` (422).

### Listado `GET /drug-types`

6. Parámetros: `page` (entero ≥ 1, por defecto `1`), `size` (entero 1..100, por defecto `20`), `search` (texto, máximo 100 caracteres), `is_active` (booleano opcional) y `sort` (catálogo cerrado de D-3, por defecto `description`).
7. `page`, `size` y `sort` se validan antes de consultar. `page < 1`, `size < 1`, `size > 100`, `sort` fuera del catálogo, `sort` vacío o `search` de más de 100 caracteres ⇒ `422` `validation_error` con `field` igual al nombre del parámetro (gracias a D-13 de FEAT-004, ya implementado).
8. `search` se recorta (`strip`). Vacío o solo espacios ⇒ **sin filtro**. Con contenido, búsqueda **case-insensitive y parcial** en `drug_types.description` (columna `CITEXT`) con `ILIKE` y los caracteres `%`, `_` y `\` escapados literalmente: buscar `%` encuentra solo los tipos cuya descripción contiene un `%`, nunca todos.
9. `is_active` ausente ⇒ **sin filtro** (se devuelven activos e inactivos, CA-CRUD 1). `is_active=true` ⇒ solo activos; `is_active=false` ⇒ solo inactivos.
10. Orden: campo del catálogo de D-3, ascendente por defecto y descendente con prefijo `-`. Siempre se añade `drug_types.id ASC` como desempate estable, salvo cuando el campo ordenado ya es `id`. `created_at` usa `NULLS LAST` en ambos sentidos (nunca es `NULL` en la práctica; se conserva por coherencia con FEAT-004).
11. `total` es el número de filas que cumplen los filtros (sin `LIMIT`). `pages = ceil(total / size)` y exactamente `0` cuando `total == 0`. `page` se devuelve tal como se pidió aunque supere `pages` (en ese caso `items` es `[]`).
12. Cada elemento del listado y cada respuesta de tipo tiene **exactamente** `id`, `description`, `is_active` (D-4). Nunca se devuelven `created_at`/`updated_at` en la respuesta (CA-CRUD 4 exige **conservar** `created_at`, no exponerlo).

### Creación `POST /drug-types`

13. Body: `description` obligatoria; `is_active` opcional (D-5, por defecto `true`).
14. `description`: se recorta (`strip`); vacía tras el recorte ⇒ `422` `validation_error` con `field = "description"`. Longitud mínima 1 (tras `strip`) y máxima 100 caracteres (D-6). Mensajes en español (CA-CRUD 9).
15. Duplicados: `description` ya existente, comparada **case-insensitive** por `CITEXT` ⇒ `409` `drug_type_exists` ("El tipo de fármaco ya existe"). No se modifica la fila existente (no es *upsert*). La comparación ignora espacios laterales (el valor almacenado siempre va recortado, regla 14).
16. El tipo se crea con `created_at`/`updated_at` por la base y el `is_active` recibido (por defecto `true`). Crear un tipo inactivo está permitido.
17. Respuesta `201` con la forma de D-4. Si el `commit` viola `uq_drug_types_description` (carrera) ⇒ `409` `drug_type_exists` (protección de carrera, D-8).

### Detalle `GET /drug-types/{drug_type_id}`

18. `drug_type_id` debe ser un entero; un valor no entero ⇒ `422` (validación de path). Un entero sin fila ⇒ `404` `drug_type_not_found` ("El tipo de fármaco no existe").
19. Devuelve `200` con la forma de D-4, incluidos los tipos inactivos (`is_active: false`). Accesible para `ADMIN` y `MEDICO`.

### Actualización `PUT /drug-types/{drug_type_id}`

20. **Reemplazo completo** (D-1): el body exige `description` e `is_active` (este último presente, puede ser `false`). Un body parcial ⇒ `422` con `field` del campo ausente.
21. Permite reactivar (`is_active: true`) y desactivar (`is_active: false`), con las mismas validaciones de `description` que la creación (reglas 14–15). Los duplicados se comprueban **excluyendo al propio tipo**: reenviar su misma `description` (con otra capitalización o mismos espacios laterales) ⇒ `200`; la de otro tipo ⇒ `409` `drug_type_exists`.
22. La actualización conserva `created_at` y refresca `updated_at` explícitamente con `clock_timestamp()` (D-9, patrón vigente de FEAT-004 D-14). Responde `200` con la forma de D-4.
23. Precedencia de errores: (a) `401`/`403` de autenticación y rol; (b) `422` de Pydantic (path, query y body); (c) `404` del tipo de la ruta; (d) `409` de unicidad; (e) escritura.
24. Solo `ADMIN`. Un `MEDICO` recibe `403` `forbidden` sin que la fila cambie.

### Desactivación `DELETE /drug-types/{drug_type_id}`

25. Es una **desactivación lógica**: `is_active = false`, sin `DELETE` SQL (CA-CRUD 5, RF-17). La fila y sus referencias (`medications.drug_type_id`, `RESTRICT`) se conservan.
26. `204 No Content` sin cuerpo y sin JSON (D-2). Starlette omite la cabecera `Content-Length` en las respuestas `204` (RFC 9110; FEAT-004 regla 31); no se añade manualmente.
27. **Idempotente**: si el tipo ya está inactivo, vuelve a responder `204`, no escribe en la base (`updated_at` no cambia) y no emite un segundo `drug_type.deactivated`.
28. `DELETE` de un `drug_type_id` inexistente ⇒ `404` `drug_type_not_found`. Solo `ADMIN`; un `MEDICO` recibe `403` `forbidden` y la fila no cambia.
29. Desactivar un tipo con medicamentos asociados está **permitido** (D-7); la restricción "no operar con catálogos inactivos" se aplica en la feature de medicamentos, no aquí.

### Errores y códigos estables

30. Catálogo de códigos de la feature: `drug_type_not_found` (404), `drug_type_exists` (409). Reutilizados: `not_authenticated` / `invalid_token` (401), `inactive_user` / `forbidden` (403), `validation_error` (422).
31. Status HTTP de la feature: `200`, `201`, `204`, `401`, `403`, `404`, `409`, `422`. Un `500` solo puede provenir del fallback del framework.
32. Los mensajes de negocio van en español (CA-CRUD 9); los mensajes automáticos de Pydantic conservan su texto original en inglés (FEAT-002 regla 30). Los validadores propios de `description` usan mensajes en español.

### Registro (logs)

33. Eventos: `drug_type.created`, `drug_type.updated`, `drug_type.deactivated`; cada uno con `drug_type_id` (objetivo) y `actor_id` (usuario del token). `drug_type.updated` no incluye valores de campos. `drug_type.deactivated` se emite siempre que `is_active` pase de `true` a `false`, venga de `DELETE` o de `PUT`; el resto de actualizaciones emite `drug_type.updated`.
34. Nunca se registran tokens, contraseñas ni datos de pacientes. `description` es un nombre de catálogo (no es PHI) pero no se incluye en los logs por defecto para mantener el patrón mínimo de FEAT-004 D-9. El middleware sigue emitiendo `request.completed` con método, ruta, status, duración y `client_ip`.
35. Un `DELETE` idempotente sobre un tipo ya inactivo no emite `drug_type.deactivated` (no hubo cambio de estado).

## Supuestos

| ID | Supuesto | Justificación |
|---|---|---|
| **SUP-1** | `description` es el único campo editable del catálogo además de `is_active`; no hay código, abreviatura ni orden manual. | El §8 de `03-api-rest.md` define `POST /drug-types` con exactamente `{"description","is_active"}`; ningún otro documento pide más campos. |
| **SUP-2** | La longitud máxima de 100 para `description` es propuesta de esta spec (D-6), no regla de negocio externa; si el humano fija otro valor, se ajusta el `Field` y CA-13 sin romper el contrato. | Ninguna fuente fija min/max; el límite evita `CITEXT` sin cota en el contrato HTTP y coincide con el máximo de `search`. Ver pregunta abierta 1. |
| **SUP-3** | No se valida nada sobre `medications` al desactivar un tipo; la integridad referencial la garantiza el FK `RESTRICT` contra borrado físico, que nunca se ejecuta. | `04-base-de-datos.md` §8.3 y `03-api-rest.md` §8 sitúan la regla "no crear con catálogos inactivos" en medicamentos, no en el catálogo. |
| **SUP-4** | La lectura es para ambos roles porque el médico necesita el catálogo para prescribir/consultar; la escritura es solo ADMIN según la matriz del §4. | Matriz de permisos de `01-requerimientos-y-backlog.md` §3 y `03-api-rest.md` §4 (`/drug-types`: CRUD ADMIN / GET MEDICO). |

## Decisiones técnicas y desviaciones documentadas

| ID | Decisión | Justificación / impacto |
|---|---|---|
| **D-1** | `PUT /drug-types/{id}` es **reemplazo completo** de `description` e `is_active` (ambos obligatorios en el body). | Coherente con la semántica de `PUT` (RFC 9110) y con FEAT-004 D-1. Evita la ambigüedad de "campo ausente vs. valor actual" y hace el contrato verificable: body parcial ⇒ `422`. La reactivación es explícita con `is_active: true`. |
| **D-2** | `DELETE /drug-types/{id}` responde `204` sin cuerpo e idempotente si el tipo ya está inactivo (no reescribe `updated_at` ni reemite el log). | Es el código del §2 para "operación exitosa sin contenido" y el patrón ya aprobado de FEAT-004 D-2 (la alternativa `200` del CA-CRUD genérico se descarta para mantener un único patrón de bajas lógicas en la API). La idempotencia evita `409`/`404` inútiles en reintentos del cliente. |
| **D-3** | Listado: `page=1`, `size=20` (1..100); `sort` ∈ `description`, `created_at`, `is_active`, `id` con prefijo `-`; por defecto `description` ascendente; desempate `id ASC`; `created_at` con `NULLS LAST`; `search` case-insensitive sobre `description` con `%`/`_`/`\` escapados; `is_active` sin filtro por defecto; `pages = ceil(total/size)` o `0`. | El §6 fija los parámetros y el máximo de 100, pero no los valores por defecto ni el catálogo de orden. Se elige un orden alfabético determinista (el uso natural de un catálogo) y estable (sin él, `OFFSET` puede repetir u omitir filas). `sort` inválido ⇒ `422` en vez de ignorarse. |
| **D-4** | La respuesta de tipo (`DrugTypeRead`) tiene **exactamente** `id`, `description`, `is_active`. | Es la forma literal del ejemplo del §8 (`{"description","is_active"}` más el `id` generado). No se expone `created_at`/`updated_at` (el CA-CRUD 4 exige **conservar** `created_at`, no exponerlo). Misma forma única para listado, detalle, creación y actualización. |
| **D-5** | En `POST /drug-types`, `is_active` es opcional con valor por defecto `true`. | Es el ejemplo literal del §8 (`"is_active": true`) y coincide con el `server_default` de la base. Crear un tipo inactivo está permitido (útil para precargar catálogos sin habilitarlos). |
| **D-6** | `description`: `strip`, no vacía tras recortar, longitud 1..100, capitalización **conservada**; la unicidad es case-insensitive por `CITEXT` (incluye capitalización y espacios laterales ya normalizados). | `strip` + conservar capitalización replica el patrón de `username`/`email` de FEAT-004 D-12 y evita divergencias (`"  Pastilla  "` se guarda como `"Pastilla"` y el duplicado `"pastilla"` colisiona por `CITEXT`). El máximo de 100 es propuesta explícita (SUP-2, pregunta 1). |
| **D-7** | Desactivar un tipo con medicamentos asociados está permitido; nunca se emite `DELETE` físico, por lo que el FK `RESTRICT` no se viola. | RF-17 exige "activar y desactivar sin eliminar físicamente" (US-18: "Desactivar, no borrar"). Prohibir la desactivación con referencias dejaría catálogos erróneos inamovibles; la protección real ("no crear medicamentos con catálogos inactivos") vive en la feature de medicamentos. |
| **D-8** | Los duplicados se apoyan en el índice único `uq_drug_types_description` y se traducen desde `IntegrityError` (`409 drug_type_exists`); el pre-chequeo en el servicio solo mejora el mensaje. Sin `SELECT ... FOR UPDATE`: no hay regla de conteo global (a diferencia del "último ADMIN" de FEAT-004) que lo requiera. | La unicidad ya está garantizada por la base; el pre-chequeo evita el 90% de los 409 con buen mensaje y el `catch` cubre la carrera. No se introduce bloqueo porque no hay invariante multi-fila que proteger. |
| **D-9** | `updated_at` se refresca explícitamente con `func.clock_timestamp()` en `PUT` y en `DELETE` con cambio real. | Patrón vigente de FEAT-004 D-14: `onupdate=func.now()` es la hora de inicio de la transacción en PostgreSQL y no cambiaría dentro de una transacción larga de test. Se conserva la semántica sin tocar el esquema. `created_at` no se toca y ninguno de los dos se expone en `DrugTypeRead`. |
| **D-10** | OpenAPI: tag `drug-types`, `summary`, `description`, `response_model` (salvo el `204`), y `responses` con `400`/`401`/`403`/`404`/`409`/`422` según el endpoint. Sin `MessageResponse`: el `DELETE` es `204` sin cuerpo. | §14 del contrato y RNF-09. La seguridad aparece automáticamente al depender de `require_role(...)` → `HTTPBearer`. |
| **D-11** | Logs con `drug_type_id` y `actor_id` exclusivamente; sin `description` ni valores de campos. `drug_type.updated` no detalla el diff. | Coherente con FEAT-004 D-9 (mínimo necesario para rastrear quién operó sin escribir contenido de negocio en logs). `actor_id` permite trazabilidad sin `audit_log` (fuera de v1). |

## Modelo de datos

**No hay cambios de esquema ni migraciones nuevas.** `uv run alembic check` debe seguir imprimiendo `No new upgrade operations detected.` y `git status --porcelain alembic/` debe quedar vacío.

Columnas utilizadas (ya existentes, FEAT-003, migración `5ae35a01c3f1_catalog_schema.py`):

| Tabla | Columna | Uso en esta feature |
|---|---|---|
| `drug_types` | `id` (`BIGINT` identity) | Identificador de ruta y de respuesta. |
| `drug_types` | `description` (`CITEXT`, `uq_drug_types_description`) | Creación, actualización, búsqueda, orden y duplicados case-insensitive. |
| `drug_types` | `is_active` (`BOOLEAN`, default `true`) | Desactivación lógica y filtro del listado. |
| `drug_types` | `created_at` / `updated_at` (`TIMESTAMPTZ`) | `created_at` se conserva; `updated_at` lo refresca el servicio con `clock_timestamp()` en cada `UPDATE` real (D-9). |
| `medications` | `drug_type_id` (FK `RESTRICT`) | Solo lectura implícita: la desactivación lógica no toca esta tabla (D-7). |

Índices: `uq_drug_types_description` ya provee el índice único para la detección de duplicados y la protección de carrera. No se crea ningún índice nuevo: el catálogo es de volumen bajo (decenas de filas) y `ILIKE` con `LIMIT 100` no justifica un índice trigram en v1; si el volumen lo exige, se añade en una feature posterior sin cambiar el contrato.

Consultas previstas (`select()`, SQLAlchemy 2.x):

```python
# Listado (filtros opcionales) + conteo
pattern = f"%{escaped}%"  # escape de \, % y _
conditions = []
if search:
    conditions.append(DrugType.description.ilike(pattern, escape="\\"))
if is_active is not None:
    conditions.append(DrugType.is_active.is_(is_active))
select(DrugType).where(*conditions).order_by(*order).limit(size).offset(offset)
select(func.count(DrugType.id)).where(*conditions)

# Duplicado excluyendo al propio tipo (el filtro de id solo se añade si hay objetivo)
conditions = [DrugType.description == description]
if exclude_id is not None:
    conditions.append(DrugType.id != exclude_id)
select(DrugType.id).where(*conditions).limit(1)
```

### Contrato de repositorio (nombres que usa el servicio)

| Función | Firma resumida |
|---|---|
| `list_drug_types` | `(session, *, page, size, search, is_active, sort_field, sort_desc) -> tuple[list[DrugType], int]` |
| `find_drug_type_by_description` | `(session, description, *, exclude_id=None) -> DrugType \| None` |
| `get_drug_type_by_id` | `(session, drug_type_id) -> DrugType \| None` |

## Contrato de API

Prefijo `/api/v1`, `Content-Type: application/json`. Todos los errores usan el envelope de la regla 5: `{"detail", "code", "errors"}`.

### Autorización común

| Endpoint | Dependencia | Error del otro rol |
|---|---|---|
| `GET /drug-types`, `GET /drug-types/{id}` | `require_role("ADMIN", "MEDICO")` | — (ambos entran) |
| `POST`, `PUT`, `DELETE` | `require_role("ADMIN")` | `MEDICO` ⇒ `403` `forbidden`, sin escritura |

Errores comunes a los cinco endpoints:

| HTTP | `code` | Cuándo |
|---|---|---|
| `401` | `not_authenticated` | sin cabecera `Authorization` o esquema distinto de `Bearer` (`WWW-Authenticate: Bearer`) |
| `401` | `invalid_token` | firma inválida, token expirado, claims `sub`/`iat`/`exp` ausentes, `sub` no numérico o usuario inexistente |
| `403` | `inactive_user` | el usuario del token ya no está activo |
| `403` | `forbidden` | el usuario del token no tiene el rol exigido (p. ej. `MEDICO` escribiendo) |

### `GET /api/v1/drug-types`

- **Query params:**

| Parámetro | Tipo | Obligatorio | Restricciones |
|---|---|---|---|
| `page` | `int` | No | ≥ 1, por defecto `1` |
| `size` | `int` | No | 1..100, por defecto `20` |
| `search` | `str` | No | máximo 100 caracteres; `strip`; vacío ⇒ sin filtro |
| `is_active` | `bool` | No | sin filtro por defecto |
| `sort` | `str` | No | `description`, `created_at`, `is_active`, `id` y sus formas con `-`; por defecto `description` |

- **Respuesta `200`** (`DrugTypeListResponse`):

```json
{
  "items": [{ "id": 1, "description": "Pastilla", "is_active": true }],
  "total": 1,
  "page": 1,
  "size": 20,
  "pages": 1
}
```

- **Errores:** `422` `validation_error` (parámetros fuera de rango o `sort` inválido; `field` = nombre del parámetro) además de los comunes.

### `POST /api/v1/drug-types`

- **Body** (`DrugTypeCreate`):

| Campo | Tipo | Obligatorio | Restricciones |
|---|---|---|---|
| `description` | `str` | Sí | `strip`, 1..100 (D-6), capitalización conservada |
| `is_active` | `bool` | No (default `true`) | — |

```json
{ "description": "Pastilla", "is_active": true }
```

- **Respuesta `201`** (`DrugTypeRead`): `{ "id": 1, "description": "Pastilla", "is_active": true }`.
- **Errores:**

| HTTP | `code` | Cuándo |
|---|---|---|
| `403` | `forbidden` | el actor es `MEDICO` |
| `409` | `drug_type_exists` | `description` ya existe (CITEXT, con `strip` aplicado) |
| `422` | `validation_error` | `description` ausente, vacía tras `strip` o de más de 100 caracteres |

Precedencia: `422` → `403` de rol (evaluado por la dependencia antes del servicio) → `409`.

### `GET /api/v1/drug-types/{drug_type_id}`

- **Path param:** `drug_type_id: int`.
- **Respuesta `200`** (`DrugTypeRead`): misma forma que el `201`; incluye inactivos.
- **Errores:** `404` `drug_type_not_found`, `422` si el id no es entero, además de los comunes (ambos roles).

### `PUT /api/v1/drug-types/{drug_type_id}`

- **Path param:** `drug_type_id: int`. Solo `ADMIN` (`MEDICO` ⇒ `403`).
- **Body** (`DrugTypeUpdate`, reemplazo completo — D-1):

| Campo | Tipo | Obligatorio | Restricciones |
|---|---|---|---|
| `description` | `str` | **Sí** | igual que en `DrugTypeCreate` |
| `is_active` | `bool` | **Sí** | — (permite reactivar con `true`) |

- **Respuesta `200`** (`DrugTypeRead`) con el estado resultante.
- **Errores:**

| HTTP | `code` | Cuándo |
|---|---|---|
| `403` | `forbidden` | el actor es `MEDICO` |
| `404` | `drug_type_not_found` | el id no existe |
| `409` | `drug_type_exists` | `description` de otro tipo |
| `422` | `validation_error` | body parcial, tipos, longitudes, path no entero |

### `DELETE /api/v1/drug-types/{drug_type_id}`

- **Path param:** `drug_type_id: int`. Solo `ADMIN` (`MEDICO` ⇒ `403`).
- **Respuesta `204 No Content`** sin cuerpo. Idempotente (D-2).
- **Errores:**

| HTTP | `code` | Cuándo |
|---|---|---|
| `403` | `forbidden` | el actor es `MEDICO` |
| `404` | `drug_type_not_found` | el id no existe |
| `422` | `validation_error` | id no entero |

### OpenAPI

Cada endpoint declara `summary`, `description`, `response_model` (salvo el `204`), `responses` con los códigos de su tabla y el tag `drug-types`. El esquema `BearerAuth` ya existe (FEAT-002) y la seguridad es obligatoria en los cinco endpoints.

## Criterios de aceptación

Convención: `[test]` = aserción exacta que el reviewer implementa en `tests/**`; `[shell]` = comando con salida esperada. Los tests de integración reutilizan `db_session`, `db_conn`, `auth_client`/`auth_client_factory` y `auth_support` de FEAT-002 (ver "Plan de pruebas").

### Autorización y contrato

- [ ] **CA-1** `[test]` Con token de `MEDICO` activo: `GET /drug-types` y `GET /drug-types/{id}` ⇒ `200`; `POST`, `PUT`, `DELETE` ⇒ `403`, `code == "forbidden"`, `errors == []`, claves exactas `{detail, code, errors}` y la base no cambia (se compara `description`/`is_active` del objetivo antes y después, y el conteo de filas).
- [ ] **CA-2** `[test]` Sin cabecera ⇒ `401` `not_authenticated` y `WWW-Authenticate` empieza por `Bearer`; con `Authorization: Basic abc` ⇒ igual; con token basura o firmado con otro secreto ⇒ `401` `invalid_token`; con token de un `ADMIN` o `MEDICO` desactivado ⇒ `403` `inactive_user` (sobre `GET /drug-types` y `POST /drug-types`).
- [ ] **CA-3** `[test]` `GET /drug-types/{id_inexistente}` con `ADMIN` ⇒ `404`, claves exactas `{detail, code, errors}`, `code == "drug_type_not_found"`, `detail == "El tipo de fármaco no existe"`, `errors == []` y sin clave `details`.

### Listado

- [ ] **CA-4** `[test]` Sin parámetros: `page == 1`, `size == 20`, claves de nivel superior exactamente `{items, total, page, size, pages}`, `total == SELECT count(*) FROM drug_types` en la misma transacción, `len(items) == min(20, total)` y `pages == ceil(total / 20)`.
- [ ] **CA-5** `[test]` Límites: `size=1` y `size=100` ⇒ `200`; `size=0`, `size=101`, `size=-1`, `page=0`, `page=-1` ⇒ `422` con `field` igual a `"size"`/`"page"`; `page=9999` ⇒ `200`, `items == []`, `page == 9999`, `total` y `pages` sin cambios respecto a `page=1`.
- [ ] **CA-6** `[test]` Con tipos de descripción con prefijo único: `sort=description` ordena ascendentemente; `sort=-description` descendentemente; `sort=created_at`, `sort=-created_at`, `sort=is_active`, `sort=-is_active`, `sort=id`, `sort=-id` responden `200` y respetan el orden; `sort=description,description`, `sort=password_hash`, `sort=` ⇒ `422` con `field == "sort"`; sin `sort` el orden es `description ASC`; con dos tipos insertados con el mismo `description` imposible por unicidad, el desempate `id ASC` se verifica ordenando por `created_at` con valores iguales.
- [ ] **CA-7** `[test]` `search` es case-insensitive y parcial sobre `description`: `search=<mitad en mayúsculas>` encuentra el tipo; `search=%` devuelve solo tipos con `%` literal (se crean `Jarabe %50` y un `Jarabe 50` de control); `search=_` solo los que contienen `_`; `search=\` no rompe y busca la barra literal; `search="   "` no aplica filtro (`total` igual al de sin `search`); `search` de 101 caracteres ⇒ `422` con `field == "search"`.
- [ ] **CA-8** `[test]` `is_active=true` devuelve solo activos, `is_active=false` solo inactivos y la ausencia del parámetro devuelve ambos; combinado con `search`, los filtros se aplican en conjunto.
- [ ] **CA-9** `[test]` `search` sin coincidencias ⇒ `items == []`, `total == 0`, `pages == 0` y `page`/`size` con los valores pedidos.
- [ ] **CA-10** `[test]` Cada `item` y cada respuesta de tipo tiene exactamente `{id, description, is_active}`; ninguna respuesta contiene `created_at`, `updated_at` ni claves ajenas.

### Creación

- [ ] **CA-11** `[test]` `POST /drug-types {"description": "Pastilla"}` (sin `is_active`) con `ADMIN` ⇒ `201`, cuerpo exacto de tres claves con `is_active is True`; en base: `description` recortada, `created_at`/`updated_at` no nulos. `POST {"description": "Jarabe", "is_active": false}` ⇒ `201` con `is_active is False` y la fila nace inactiva (visible en `GET /{id}`).
- [ ] **CA-12** `[test]` Duplicado con otra capitalización (`"PASTILLA"` existiendo `"Pastilla"`) ⇒ `409` `drug_type_exists`, `detail == "El tipo de fármaco ya existe"`; `SELECT count(*) FROM drug_types WHERE description = 'Pastilla'` es `1`. Duplicado con espacios laterales (`"  Pastilla  "`) ⇒ `409` igualmente.
- [ ] **CA-13** `[test]` `422` con `field == "description"`: body `{}`, `description` ausente, `description: ""`, `description: "   "` y `description` de 101 caracteres. `description` de 1 y de 100 caracteres ⇒ `201`. `"  Pastilla  "` se almacena como `"Pastilla"` (recortada, capitalización conservada).
- [ ] **CA-14** `[test]` La respuesta del `201` no contiene `created_at`/`updated_at`; con `caplog` a `INFO`, la creación incluye `drug_type.created` con `drug_type_id` y `actor_id`.

### Detalle

- [ ] **CA-15** `[test]` `GET /drug-types/{id}` ⇒ `200` con los mismos valores que el `item` del listado; un tipo inactivo devuelve `200` con `is_active is False`; un `id` no entero (`"abc"`) ⇒ `422` con `field == "drug_type_id"`; accesible para `MEDICO` (200) sin diferencias respecto a `ADMIN`.

### Actualización

- [ ] **CA-16** `[test]` `PUT` completo (`{"description": "Tableta", "is_active": true}`) ⇒ `200` con el estado nuevo; en base cambia `description`; `created_at` **no** cambia, `updated_at` sí (posterior al valor previo leído en la misma transacción).
- [ ] **CA-17** `[test]` `PUT {"description": <misma>, "is_active": true}` sobre un tipo inactivo ⇒ `200`, `is_active is True` (reactivación). `PUT {"description": <misma>, "is_active": false}` sobre uno activo ⇒ `200`, `is_active is False`.
- [ ] **CA-18** `[test]` `PUT` con la `description` de otro tipo (otra capitalización incluida) ⇒ `409` `drug_type_exists` y la fila no cambia; reenviando su propia `description` (incluso con distinta capitalización o espacios laterales) ⇒ `200`.
- [ ] **CA-19** `[test]` `PUT` con `drug_type_id` inexistente ⇒ `404` `drug_type_not_found`; body sin `is_active` ⇒ `422` con `field == "is_active"`; body sin `description` ⇒ `422` con `field == "description"` (reemplazo completo, D-1); `PUT` de `MEDICO` ⇒ `403` y la fila no cambia.

### Desactivación

- [ ] **CA-20** `[test]` `DELETE /drug-types/{id}` de un tipo activo con `ADMIN` ⇒ `204`, `response.content == b""`; la fila sigue existiendo con `is_active = false`; el log contiene `drug_type.deactivated` con `drug_type_id` y `actor_id`.
- [ ] **CA-21** `[test]` Segundo `DELETE` sobre el mismo tipo ⇒ `204`, `is_active` sigue `false`, `updated_at` no cambia y no se emite un segundo `drug_type.deactivated`; `DELETE` de un `id` inexistente ⇒ `404`; `DELETE` con `MEDICO` ⇒ `403` y la fila sigue activa.
- [ ] **CA-22** `[test]` Desactivar un tipo referenciado por un `medication` (creado por SQL directo en la transacción) ⇒ `204` y `is_active = false`; la fila de `medications` sigue intacta (D-7).

### Integridad y errores

- [ ] **CA-23** `[test]` Un `IntegrityError` inyectado en el `commit` con el nombre `uq_drug_types_description` se traduce a `409` `drug_type_exists`; no queda fila nueva y la sesión sigue usable.
- [ ] **CA-24** `[test]` Todos los errores de la feature respetan el envelope exacto `{detail, code, errors}` (sin `details`) y los códigos del catálogo de la regla 30 para cada escenario de las tablas de contrato (al menos un caso por código).
- [ ] **CA-25** `[test]` Con `caplog` a `INFO`, tras crear, actualizar y desactivar: aparecen `drug_type.created`, `drug_type.updated` y `drug_type.deactivated`, cada uno con `drug_type_id` y `actor_id`; `caplog.text` no contiene tokens ni contraseñas.

### OpenAPI y calidad

- [ ] **CA-26** `[shell]` OpenAPI:

  ```powershell
  uv run python -c "from app.main import app; o=app.openapi(); p=o['paths']; u=sorted(k for k in p if k.startswith('/api/v1/drug-types')); print(u); print(sorted({t for k in u for m in p[k].values() for t in m.get('tags', [])})); print(all(m.get('security') and m.get('summary') and m.get('description') for k in u for m in p[k].values())); print('content' in p['/api/v1/drug-types']['get']['responses']['200'], 'content' in p['/api/v1/drug-types']['post']['responses']['201']); print(sorted(p['/api/v1/drug-types']['post']['responses'])); print(sorted(p['/api/v1/drug-types/{drug_type_id}']['delete']['responses']))"
  ```

  imprime:

  ```text
  ['/api/v1/drug-types', '/api/v1/drug-types/{drug_type_id}']
  ['drug-types']
  True
  True True
  ['201', '401', '403', '409', '422']
  ['204', '401', '403', '404', '422']
  ```

  Además, `GET /api/v1/drug-types` declara `['200', '401', '403', '422']`, `GET /{id}` declara `['200', '401', '403', '404', '422']` y `PUT` declara `['200', '401', '403', '404', '409', '422']`.

- [ ] **CA-27** `[shell]` Sin cambios de esquema: `uv run alembic check` imprime `No new upgrade operations detected.` y `git status --porcelain alembic/` no imprime nada.
- [ ] **CA-28** `[shell]` `uv run ruff check .` sin errores y `uv run ruff format --check .` sin cambios pendientes.
- [ ] **CA-29** `[shell]` `uv run pytest -q` en verde, incluidos los tests de FEAT-002/003/004 (sin regresiones).

### Trazabilidad

| Requisito / fuente | Criterios |
|---|---|
| RF-04 / US-07 / E2 (CRUD de tipos) | CA-4 a CA-22 |
| CA-CRUD 1–2 (listado paginado, `items/total/page/size/pages`) | CA-4, CA-5, CA-9 |
| CA-CRUD 3 (`201`, validación, `409` duplicados) | CA-11 a CA-14, CA-23 |
| CA-CRUD 4 (edición `200`, conserva `created_at`) | CA-16 |
| CA-CRUD 5 (baja lógica, sin borrado) | CA-20 a CA-22 |
| CA-CRUD 6 (`200`/`404` por ID) | CA-3, CA-15 |
| CA-CRUD 8 (escritura solo ADMIN, `403` a médico) | CA-1, CA-19, CA-21 |
| CA-CRUD 9 (validaciones y mensajes en español) | CA-5 a CA-7, CA-13 |
| CA-CRUD 10 (Swagger + pruebas) | CA-24 a CA-29 |
| §4 matriz (`/drug-types` CRUD ADMIN / GET MEDICO) | CA-1, CA-2, CA-15, CA-19, CA-21 |
| §8 (`POST` + descripciones únicas) | CA-11, CA-12, CA-18, CA-23 |
| RF-17 (activar/desactivar sin eliminar) | CA-17, CA-20 a CA-22 |

## Plan de pruebas

### Aislamiento

Se reutiliza la infraestructura de FEAT-002/FEAT-004 sin cambios:

1. `db_conn` (transacción revertida) y `db_session` (`join_transaction_mode="create_savepoint"`) de `tests/conftest.py`; ninguna escritura sobrevive al test.
2. `auth_client` / `auth_client_factory` sobrescriben `get_session` con `db_session`; limpiar en `finally` (ya lo hace la fixture).
3. Los usuarios se crean con `tests/auth_support.create_auth_user` y los tokens con `tests/auth_support.token_for(user_id, role=...)` + `auth_headers`. Se necesita un `ADMIN` actor y un `MEDICO` lector por test; no se depende de datos preexistentes (`total` se compara contra `SELECT count(*) FROM drug_types` en la transacción).
4. Las lecturas de verificación usan `db_conn` (misma conexión ⇒ ve los cambios no confirmados de `db_session`).
5. No se usan datos personales reales: descripciones sintéticas con `unique_suffix()` (p. ej. `f"Tipo {unique_suffix()}"`); los casos con `%`, `_`, `\` usan sufijos fijos controlados.
6. El `medication` de CA-22 se inserta por SQL directo con `brand_id`/`location_id` existentes o también insertados por SQL; todo dentro de la transacción revertida.
7. `get_login_rate_limiter()` no se toca en estos tests (ningún CA hace login salvo verificación opcional de que el actor sigue activo; máximo 1 login por test si se usa).

### Unitarias (sin base de datos)

| # | Test | CA |
|---|---|---|
| U-1 | `DrugTypeCreate`: `description` obligatoria, `strip`, 1..100, default `is_active=True` | CA-13 |
| U-2 | `DrugTypeUpdate`: ambos campos obligatorios, `is_active` acepta `false`, `description` con las mismas reglas | CA-19 |
| U-3 | `DrugTypeRead`: serializa exactamente las tres claves | CA-10 |
| U-4 | Catálogo de `sort`: las 8 variantes válidas y el rechazo de inválidas (incluida la cadena vacía) | CA-6 |
| U-5 | Función de escape de `LIKE`: `%`, `_` y `\` se convierten en literales | CA-7 |
| U-6 | Cálculo de `pages`: `0` con `total=0`, techo con resto y exacto sin resto | CA-4, CA-9 |

### Integración (PostgreSQL + httpx)

| # | Test | CA |
|---|---|---|
| I-1 | `MEDICO` lee (`200`) y no escribe (`403` × 3 sin cambios en base) | CA-1 |
| I-2 | Sin token / esquema ajeno / token inválido / usuario inactivo | CA-2 |
| I-3 | `404` con envelope y sin `details` | CA-3 |
| I-4 | Defaults y fórmula de paginación | CA-4 |
| I-5 | Límites de `page`/`size` y página fuera de rango | CA-5 |
| I-6 | Los 4 campos de `sort` en ambos sentidos, `NULLS LAST`, desempate e inválidos | CA-6 |
| I-7 | `search` case-insensitive, parcial, escapado de comodines y longitud | CA-7 |
| I-8 | Filtro `is_active` y combinación con `search` | CA-8 |
| I-9 | Sin resultados: `pages == 0` | CA-9 |
| I-10 | Forma exacta de `item` en las cuatro respuestas con tipo | CA-10, CA-15 |
| I-11 | Creación feliz activa/inactiva | CA-11 |
| I-12 | Duplicados `CITEXT` con capitalización y espacios | CA-12 |
| I-13 | Validaciones de `DrugTypeCreate` y normalización `strip` | CA-13 |
| I-14 | Log de creación con `drug_type_id`/`actor_id` | CA-14 |
| I-15 | Detalle activo/inactivo, `422` de path y lectura `MEDICO` | CA-15 |
| I-16 | `PUT` feliz con conservación de `created_at` y refresco de `updated_at` | CA-16 |
| I-17 | `PUT` reactiva y desactiva | CA-17 |
| I-18 | `PUT` duplicados ajenos vs. propios | CA-18 |
| I-19 | `PUT` errores de body, `404` y `403` de `MEDICO` | CA-19 |
| I-20 | `DELETE` feliz, idempotente, `404` y `403` de `MEDICO` | CA-20, CA-21 |
| I-21 | `DELETE` de tipo con medicamentos asociados | CA-22 |
| I-22 | Envelope uniforme por cada código del catálogo | CA-24 |

### Integridad

| # | Test | CA |
|---|---|---|
| C-1 | Traducción de `IntegrityError` por nombre de restricción (`uq_drug_types_description`) | CA-23 |

### Logs, OpenAPI y calidad

| # | Test | CA |
|---|---|---|
| S-1 | Los tres eventos con `drug_type_id`/`actor_id` y sin secretos | CA-14, CA-25 |
| O-1 | OpenAPI: paths, tag, seguridad, `summary`/`description` y códigos | CA-26 |
| Q-1 | `alembic check` y `git status --porcelain alembic/` | CA-27 |
| Q-2 | `ruff check` y `ruff format --check` | CA-28 |
| Q-3 | `pytest -q` completo | CA-29 |

## Riesgos y dependencias

| ID | Riesgo | Impacto | Mitigación / decisión |
|---|---|---|---|
| **R-1** | La longitud máxima de 100 para `description` es propuesta de la spec, no regla de negocio documentada (SUP-2). | Bajo | Pregunta abierta 1; el cambio es un `Field(max_length=...)` + CA-13, sin romper el contrato. |
| **R-2** | `PUT` es reemplazo completo: un cliente que envíe solo `description` recibe `422`; no hay bloqueo optimista (last write wins). | Bajo | D-1; el frontend hace `GET` antes de `PUT`. El volumen y la concurrencia de edición de catálogos son mínimos. |
| **R-3** | Desactivar un tipo no impide su uso hasta que la feature de medicamentos aplique "no crear con catálogos inactivos"; temporalmente se puede prescribir con un tipo inactivo. | Medio | D-7; la regla del §8 se implementa en US-20. El listado permite filtrar `is_active=true` desde el primer día. |
| **R-4** | `ILIKE` con comodines escapados y `CITEXT`: un escape mal construido convertiría `%` en comodín. | Bajo | Escapado explícito de `\`, `%` y `_` con `escape="\\"`; CA-7 lo verifica con tipos que contienen los tres caracteres. |
| **R-5** | Sin índice trigram, `search ILIKE '%...%'` es secuencial; irrelevante con decenas de filas pero costoso si el catálogo creciera a miles. | Bajo | Sin índice nuevo en v1 (sección "Modelo de datos"); si el volumen lo exige, índice trigram en feature posterior sin cambio de contrato. |
| **R-6** | `sort=is_active` ordena booleanos (`false < true`); el desempate `id ASC` lo hace determinista pero el orden entre grupos solo es útil combinado con `search`. | Bajo | Catálogo cerrado en D-3; CA-6 verifica ambos sentidos. |

**Dependencias**

- **FEAT-001 (`done`)**: FastAPI, sesión async, logging con redacción, envelope y middlewares.
- **FEAT-003 (`done`)**: tabla `drug_types` (`CITEXT`, `uq_drug_types_description`, `is_active` default `true`), FK `medications.drug_type_id` (`RESTRICT`).
- **FEAT-002 (`done`)**: `get_current_user`, `require_role` (incluye `require_role("ADMIN", "MEDICO")` — ambos roles están en `ALLOWED_ROLES`), envelope `{detail, code, errors}`.
- **FEAT-004 (`done`, patrón)**: paginación (`page/size/search/is_active/sort`), `_validation_field` (D-13), `204` idempotente (D-2), `clock_timestamp()` (D-14), estructura routes → services → repositories.
- **Bloquea**: la feature de medicamentos (US-20) necesita este CRUD para poblar `drug_type_id`; las de marcas/ubicaciones pueden reutilizar este CRUD como plantilla.
- **No modifica** `app/core/config.py`, `app/api/deps.py`, `app/core/security.py`, `app/models/`, `alembic/` ni dependencias de `pyproject.toml`.

**Datos sensibles**

- `description` es un nombre de catálogo (p. ej. "Pastilla"), no dato de paciente; aun así no se registra en logs (D-11).
- Los tests usan descripciones sintéticas dentro de una transacción revertida; no se usan datos personales reales.
- `audit_log` no se escribe en esta feature (RF-22 fuera de v1); el rastro queda en los eventos estructurados.

## Preguntas abiertas

1. **Longitud de `description`**: ninguna fuente fija mínimo ni máximo (el §8 solo muestra `"Pastilla"` y exige unicidad). La spec propone 1..100 tras `strip` (D-6/SUP-2). ¿Se confirma 100 o se fija otro valor (p. ej. 50, 150, 255)? **No bloquea**: el implementer usa 100 y el ajuste posterior es localizado.
2. **¿Debe normalizarse la capitalización al guardar** (p. ej. Title Case) o se conserva tal como se envía? La spec propone conservarla (D-6, patrón FEAT-004 D-12). **No bloquea**: el comportamiento propuesto es implementable y verificable (CA-13).

No hay más bloqueos: permisos (matriz §3/§4), forma del `POST` (§8), paginación (§6), unicidad case-insensitive (`CITEXT` en FEAT-003) y baja lógica (RF-17/CA-CRUD 5) están definidos en las fuentes.

## Historial

- 2026-10-21 — creada por spec-author a partir de `DispensarioMedicoDocs/01-requerimientos-y-backlog.md` (RF-04, US-07, E2, §3, §7) y `DispensarioMedicoDocs/03-api-rest.md` (§2, §4–§6, §8); `app/models/drug_type.py`, `app/models/medication.py`, `app/api/v1/routes/users.py`, `app/api/deps.py`, `app/core/exceptions.py` y el patrón de `docs/features/FEAT-004.md`.
