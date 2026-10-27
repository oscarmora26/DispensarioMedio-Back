# FEAT-008 — Gestión de marcas: CRUD, permisos y desactivación lógica

> **Nota de vigencia (2026-10-27).** Este documento describe el contrato tal como se aprobó en su fecha y se
> conserva como registro histórico. Desde **FEAT-017** la tabla `locations` fue reemplazada por
> `shelves` + `cells`: `/api/v1/locations` ya no existe (ahora son `/api/v1/shelves` y `/api/v1/cells`) y
> `medications.location_id` pasó a llamarse `medications.cell_id` (FK a `cells.id`). El contrato vigente
> está en [FEAT-017](FEAT-017.md).

## Contexto

La tabla `brands` ya existe (FEAT-003): `description CITEXT` con `uq_brands_description`, `is_active BOOLEAN DEFAULT true`, y relación `medications` (FK `medications.brand_id → brands.id ON DELETE RESTRICT`). Hoy **no existe ninguna vía HTTP para crear, consultar, actualizar o desactivar marcas**.

Sin esta feature, RF-05 (gestionar marcas) y US-16 (CRUD de marcas, Sprint 3, 3 pts, CA-CRUD con descripción única) quedan sin implementar, y las futuras features de medicamentos (que exigen `brand_id`) no tienen cómo poblar el catálogo por API. Es el segundo catálogo maestro (E2) y clona el patrón ya aprobado de `/drug-types` (FEAT-007).

Fuente de verdad funcional: `DispensarioMedicoDocs/01-requerimientos-y-backlog.md` RF-05, US-16, E2, §3 (matriz: Marcas CRUD ADMIN / Consulta MEDICO) y §7 (CA-CRUD); `DispensarioMedicoDocs/03-api-rest.md` §2 (paginación, envelope, códigos), §4 (matriz `/brands`: CRUD ADMIN / GET MEDICO), §5 (endpoints), §6 (parámetros de listado) y §8 (`POST /brands {"description","is_active"}`, descripciones únicas).

## Alcance

- `app/schemas/brand.py` (nuevo): `BrandCreate`, `BrandUpdate`, `BrandRead`, `BrandListResponse`.
- `app/repositories/brand_repository.py` (nuevo): listado paginado con búsqueda/filtros/orden, `get_brand_by_id`, detección de duplicados case-insensitive (CITEXT).
- `app/services/brand_service.py` (nuevo): `list_brands`, `get_brand`, `create_brand`, `update_brand`, `deactivate_brand`, traducción de `IntegrityError` (`uq_brands_description` → `409`).
- `app/api/v1/routes/brands.py` (nuevo) con tag `brands`, registrado en `app/api/v1/router.py`.
- Reutilización de: `app/api/deps.py` (`get_current_user`, `require_role`), `app/core/exceptions.py` (`AppError`, `NotFoundError`, `ConflictError`, envelope; `_validation_field` ya descarta prefijos `query`/`path`/`header`), `app/models/brand.py`.
- Cinco endpoints descritos en "Contrato de API".
- Logs estructurados `brand.created`, `brand.updated`, `brand.deactivated`.
- Documentación OpenAPI completa (tag, `summary`, `description`, `response_model`, códigos).

### Estructura de archivos resultante

```text
app/
├── api/v1/
│   ├── router.py                 # modificado: incluye brands
│   └── routes/brands.py          # nuevo
├── repositories/
│   └── brand_repository.py       # nuevo
├── schemas/brand.py              # nuevo
└── services/brand_service.py     # nuevo
```

## Fuera de alcance

- CRUD de `/drug-types`, `/locations`, `/medications`, `/patients`, `/doctors`, `/visits`: esta feature solo deja `brands` consultable como prerrequisito de medicamentos.
- `GET /brands` filtrado por "solo activos" obligatorio: el filtro `is_active` es opcional; la restricción "no crear medicamentos con catálogos inactivos" pertenece a la feature de medicamentos (03-api-rest §8).
- Borrado físico de marcas: la única baja es lógica (`is_active = false`). No hay `DELETE` SQL en ningún camino.
- Bloqueo de desactivar una marca con medicamentos asociados: la desactivación lógica **está permitida** aunque existan `medications` (el FK es `RESTRICT` solo contra borrado físico — ver D-7).
- Reactivación por endpoint dedicado: se reactiva con `PUT {"is_active": true}` (reemplazo completo, D-1).
- Paginación por cursor, exportación, `audit_log` (RF-22 fuera de v1), refresh tokens, cambios de esquema, migraciones, `docker-compose.yml`, `Dockerfile`, `.env.example` o settings nuevas. `uv run alembic check` debe seguir limpio.
- CORS, frontend Angular (tabla, formulario, toasts), pruebas E2E.
- Ordenación por campos fuera del catálogo de D-3.

## Reglas de negocio

### Autorización y alcance

1. Lectura (`GET /brands`, `GET /brands/{id}`): `Authorization: Bearer <token>` y rol `ADMIN` **o** `MEDICO`, leído de la base (`user.role.name`) mediante `require_role("ADMIN", "MEDICO")`. El claim `role` del token no se usa (FEAT-002 regla 12).
2. Escritura (`POST`, `PUT`, `DELETE`): rol `ADMIN` exclusivamente, mediante `require_role("ADMIN")`. Un `MEDICO` autenticado recibe `403` `forbidden` en las tres, sin efectos en la base (CA-CRUD 8).
3. Sin cabecera, con esquema distinto de `Bearer`, con token inválido/expirado o de un usuario inexistente ⇒ `401` (`not_authenticated` o `invalid_token`, FEAT-002 reglas 11 y 12).
4. Un `ADMIN` o `MEDICO` desactivado pierde el acceso en la siguiente request (`403` `inactive_user`), incluso si el token sigue vigente (FEAT-002 regla 13).
5. Todos los errores usan el envelope global `{"detail": str, "code": str, "errors": [{"field","message"}]}`; `errors` es `[]` cuando no aplica y la clave `details` no existe (FEAT-002 D-1, FEAT-004 D-13). Códigos nuevos: `brand_not_found` (404), `brand_exists` (409). Se reutilizan `not_authenticated` / `invalid_token` (401), `inactive_user` / `forbidden` (403) y `validation_error` (422).

### Listado `GET /brands`

6. Parámetros: `page` (entero ≥ 1, por defecto `1`), `size` (entero 1..100, por defecto `20`), `search` (texto, máximo 100 caracteres), `is_active` (booleano opcional) y `sort` (catálogo cerrado de D-3, por defecto `description`).
7. `page`, `size` y `sort` se validan antes de consultar. `page < 1`, `size < 1`, `size > 100`, `sort` fuera del catálogo, `sort` vacío o `search` de más de 100 caracteres ⇒ `422` `validation_error` con `field` igual al nombre del parámetro (gracias a D-13 de FEAT-004, ya implementado).
8. `search` se recorta (`strip`). Vacío o solo espacios ⇒ **sin filtro**. Con contenido, búsqueda **case-insensitive y parcial** en `brands.description` (columna `CITEXT`) con `ILIKE` y los caracteres `%`, `_` y `\` escapados literalmente: buscar `%` encuentra solo las marcas cuya descripción contiene un `%`, nunca todas.
9. `is_active` ausente ⇒ **sin filtro** (se devuelven activas e inactivas, CA-CRUD 1). `is_active=true` ⇒ solo activas; `is_active=false` ⇒ solo inactivas.
10. Orden: campo del catálogo de D-3, ascendente por defecto y descendente con prefijo `-`. Siempre se añade `brands.id ASC` como desempate estable, salvo cuando el campo ordenado ya es `id`. `created_at` usa `NULLS LAST` en ambos sentidos (nunca es `NULL` en la práctica; se conserva por coherencia con FEAT-004).
11. `total` es el número de filas que cumplen los filtros (sin `LIMIT`). `pages = ceil(total / size)` y exactamente `0` cuando `total == 0`. `page` se devuelve tal como se pidió aunque supere `pages` (en ese caso `items` es `[]`).
12. Cada elemento del listado y cada respuesta de marca tiene **exactamente** `id`, `description`, `is_active` (D-4). Nunca se devuelven `created_at`/`updated_at` en la respuesta (CA-CRUD 4 exige **conservar** `created_at`, no exponerlo).

### Creación `POST /brands`

13. Body: `description` obligatoria; `is_active` opcional (D-5, por defecto `true`).
14. `description`: se recorta (`strip`); vacía tras el recorte ⇒ `422` `validation_error` con `field = "description"`. Longitud mínima 1 (tras `strip`) y máxima 100 caracteres (D-6). Mensajes en español (CA-CRUD 9).
15. Duplicados: `description` ya existente, comparada **case-insensitive** por `CITEXT` ⇒ `409` `brand_exists` ("La marca ya existe"). No se modifica la fila existente (no es *upsert*). La comparación ignora espacios laterales (el valor almacenado siempre va recortado, regla 14).
16. La marca se crea con `created_at`/`updated_at` por la base y el `is_active` recibido (por defecto `true`). Crear una marca inactiva está permitido.
17. Respuesta `201` con la forma de D-4. Si el `commit` viola `uq_brands_description` (carrera) ⇒ `409` `brand_exists` (protección de carrera, D-8).

### Detalle `GET /brands/{brand_id}`

18. `brand_id` debe ser un entero; un valor no entero ⇒ `422` (validación de path). Un entero sin fila ⇒ `404` `brand_not_found` ("La marca no existe").
19. Devuelve `200` con la forma de D-4, incluidas las marcas inactivas (`is_active: false`). Accesible para `ADMIN` y `MEDICO`.

### Actualización `PUT /brands/{brand_id}`

20. **Reemplazo completo** (D-1): el body exige `description` e `is_active` (este último presente, puede ser `false`). Un body parcial ⇒ `422` con `field` del campo ausente.
21. Permite reactivar (`is_active: true`) y desactivar (`is_active: false`), con las mismas validaciones de `description` que la creación (reglas 14–15). Los duplicados se comprueban **excluyendo a la propia marca**: reenviar su misma `description` (con otra capitalización o mismos espacios laterales) ⇒ `200`; la de otra marca ⇒ `409` `brand_exists`.
22. La actualización conserva `created_at` y refresca `updated_at` explícitamente con `clock_timestamp()` (D-9, patrón vigente de FEAT-004 D-14). Responde `200` con la forma de D-4.
23. Precedencia de errores: (a) `401`/`403` de autenticación y rol; (b) `422` de Pydantic (path, query y body); (c) `404` de la marca de la ruta; (d) `409` de unicidad; (e) escritura.
24. Solo `ADMIN`. Un `MEDICO` recibe `403` `forbidden` sin que la fila cambie.

### Desactivación `DELETE /brands/{brand_id}`

25. Es una **desactivación lógica**: `is_active = false`, sin `DELETE` SQL (CA-CRUD 5, RF-17). La fila y sus referencias (`medications.brand_id`, `RESTRICT`) se conservan.
26. `204 No Content` sin cuerpo y sin JSON (D-2). Starlette omite la cabecera `Content-Length` en las respuestas `204` (RFC 9110; FEAT-004 regla 31); no se añade manualmente.
27. **Idempotente**: si la marca ya está inactiva, vuelve a responder `204`, no escribe en la base (`updated_at` no cambia) y no emite un segundo `brand.deactivated`.
28. `DELETE` de un `brand_id` inexistente ⇒ `404` `brand_not_found`. Solo `ADMIN`; un `MEDICO` recibe `403` `forbidden` y la fila no cambia.
29. Desactivar una marca con medicamentos asociados está **permitido** (D-7); la restricción "no operar con catálogos inactivos" se aplica en la feature de medicamentos, no aquí.

### Errores y códigos estables

30. Catálogo de códigos de la feature: `brand_not_found` (404), `brand_exists` (409). Reutilizados: `not_authenticated` / `invalid_token` (401), `inactive_user` / `forbidden` (403), `validation_error` (422).
31. Status HTTP de la feature: `200`, `201`, `204`, `401`, `403`, `404`, `409`, `422`. Un `500` solo puede provenir del fallback del framework.
32. Los mensajes de negocio van en español (CA-CRUD 9); los mensajes automáticos de Pydantic conservan su texto original en inglés (FEAT-002 regla 30). Los validadores propios de `description` usan mensajes en español.

### Registro (logs)

33. Eventos: `brand.created`, `brand.updated`, `brand.deactivated`; cada uno con `brand_id` (objetivo) y `actor_id` (usuario del token). `brand.updated` no incluye valores de campos. `brand.deactivated` se emite siempre que `is_active` pase de `true` a `false`, venga de `DELETE` o de `PUT`; el resto de actualizaciones emite `brand.updated`.
34. Nunca se registran tokens, contraseñas ni datos de pacientes. `description` es un nombre de catálogo (no es PHI) pero no se incluye en los logs por defecto para mantener el patrón mínimo de FEAT-004 D-9. El middleware sigue emitiendo `request.completed` con método, ruta, status, duración y `client_ip`.
35. Un `DELETE` idempotente sobre una marca ya inactiva no emite `brand.deactivated` (no hubo cambio de estado).

## Supuestos

| ID | Supuesto | Justificación |
|---|---|---|
| **SUP-1** | `description` es el único campo editable del catálogo además de `is_active`; no hay código, abreviatura ni orden manual. | El §8 de `03-api-rest.md` define `POST /brands` con exactamente `{"description","is_active"}`; ningún otro documento pide más campos. |
| **SUP-2** | La longitud máxima de 100 para `description` es propuesta de esta spec (D-6), no regla de negocio externa; si el humano fija otro valor, se ajusta el `Field` y CA-13 sin romper el contrato. | Ninguna fuente fija min/max; el límite evita `CITEXT` sin cota en el contrato HTTP y coincide con el máximo de `search`. Ver pregunta abierta 1. |
| **SUP-3** | No se valida nada sobre `medications` al desactivar una marca; la integridad referencial la garantiza el FK `RESTRICT` contra borrado físico, que nunca se ejecuta. | `03-api-rest.md` §8 sitúa la regla "no crear con catálogos inactivos" en medicamentos, no en el catálogo. |
| **SUP-4** | La lectura es para ambos roles porque el médico necesita el catálogo para prescribir/consultar; la escritura es solo ADMIN según la matriz del §4. | Matriz de permisos de `01-requerimientos-y-backlog.md` §3 y `03-api-rest.md` §4 (`/brands`: CRUD ADMIN / GET MEDICO). |

## Decisiones técnicas y desviaciones documentadas

| ID | Decisión | Justificación / impacto |
|---|---|---|
| **D-1** | `PUT /brands/{id}` es **reemplazo completo** de `description` e `is_active` (ambos obligatorios en el body). | Coherente con la semántica de `PUT` (RFC 9110) y con FEAT-004 D-1. Evita la ambigüedad de "campo ausente vs. valor actual" y hace el contrato verificable: body parcial ⇒ `422`. La reactivación es explícita con `is_active: true`. |
| **D-2** | `DELETE /brands/{id}` responde `204` sin cuerpo e idempotente si la marca ya está inactiva (no reescribe `updated_at` ni reemite el log). | Es el código del §2 para "operación exitosa sin contenido" y el patrón ya aprobado de FEAT-004 D-2 (la alternativa `200` del CA-CRUD genérico se descarta para mantener un único patrón de bajas lógicas en la API). La idempotencia evita `409`/`404` inútiles en reintentos del cliente. |
| **D-3** | Listado: `page=1`, `size=20` (1..100); `sort` ∈ `description`, `created_at`, `is_active`, `id` con prefijo `-`; por defecto `description` ascendente; desempate `id ASC`; `created_at` con `NULLS LAST`; `search` case-insensitive sobre `description` con `%`/`_`/`\` escapados; `is_active` sin filtro por defecto; `pages = ceil(total/size)` o `0`. | El §6 fija los parámetros y el máximo de 100, pero no los valores por defecto ni el catálogo de orden. Se elige un orden alfabético determinista (el uso natural de un catálogo) y estable (sin él, `OFFSET` puede repetir u omitir filas). `sort` inválido ⇒ `422` en vez de ignorarse. |
| **D-4** | La respuesta de marca (`BrandRead`) tiene **exactamente** `id`, `description`, `is_active`. | Es la forma literal del ejemplo del §8 (`{"description","is_active"}` más el `id` generado). No se expone `created_at`/`updated_at` (el CA-CRUD 4 exige **conservar** `created_at`, no exponerlo). Misma forma única para listado, detalle, creación y actualización. |
| **D-5** | En `POST /brands`, `is_active` es opcional con valor por defecto `true`. | Es el ejemplo literal del §8 (`"is_active": true`) y coincide con el `server_default` de la base. Crear una marca inactiva está permitido (útil para precargar catálogos sin habilitarlos). |
| **D-6** | `description`: `strip`, no vacía tras recortar, longitud 1..100, capitalización **conservada**; la unicidad es case-insensitive por `CITEXT` (incluye capitalización y espacios laterales ya normalizados). | `strip` + conservar capitalización replica el patrón de `username`/`email` de FEAT-004 D-12 y evita divergencias (`"  Bayer  "` se guarda como `"Bayer"` y el duplicado `"bayer"` colisiona por `CITEXT`). El máximo de 100 es propuesta explícita (SUP-2, pregunta 1). |
| **D-7** | Desactivar una marca con medicamentos asociados está permitido; nunca se emite `DELETE` físico, por lo que el FK `RESTRICT` no se viola. | RF-17 exige "activar y desactivar sin eliminar físicamente" (US-18: "Desactivar, no borrar"). Prohibir la desactivación con referencias dejaría catálogos erróneos inamovibles; la protección real ("no crear medicamentos con catálogos inactivos") vive en la feature de medicamentos. |
| **D-8** | Los duplicados se apoyan en el índice único `uq_brands_description` y se traducen desde `IntegrityError` (`409 brand_exists`); el pre-chequeo en el servicio solo mejora el mensaje. Sin `SELECT ... FOR UPDATE`: no hay regla de conteo global (a diferencia del "último ADMIN" de FEAT-004) que lo requiera. | La unicidad ya está garantizada por la base; el pre-chequeo evita el 90% de los 409 con buen mensaje y el `catch` cubre la carrera. No se introduce bloqueo porque no hay invariante multi-fila que proteger. |
| **D-9** | `updated_at` se refresca explícitamente con `func.clock_timestamp()` en `PUT` y en `DELETE` con cambio real. | Patrón vigente de FEAT-004 D-14: `onupdate=func.now()` es la hora de inicio de la transacción en PostgreSQL y no cambiaría dentro de una transacción larga de test. Se conserva la semántica sin tocar el esquema. `created_at` no se toca y ninguno de los dos se expone en `BrandRead`. |
| **D-10** | OpenAPI: tag `brands`, `summary`, `description`, `response_model` (salvo el `204`), y `responses` con `400`/`401`/`403`/`404`/`409`/`422` según el endpoint. Sin `MessageResponse`: el `DELETE` es `204` sin cuerpo. | §14 del contrato y RNF-09. La seguridad aparece automáticamente al depender de `require_role(...)` → `HTTPBearer`. |
| **D-11** | Logs con `brand_id` y `actor_id` exclusivamente; sin `description` ni valores de campos. `brand.updated` no detalla el diff. | Coherente con FEAT-004 D-9 (mínimo necesario para rastrear quién operó sin escribir contenido de negocio en logs). `actor_id` permite trazabilidad sin `audit_log` (fuera de v1). |

## Modelo de datos
Tablas/columnas/índices/constraints nuevos o modificados, con tipos.

**No hay cambios de esquema ni migraciones nuevas.** `uv run alembic check` debe seguir imprimiendo `No new upgrade operations detected.` y `git status --porcelain alembic/` debe quedar vacío.

Columnas utilizadas (ya existentes, FEAT-003):

| Tabla | Columna | Uso en esta feature |
|---|---|---|
| `brands` | `id` (`BIGINT` identity) | Identificador de ruta y de respuesta. |
| `brands` | `description` (`CITEXT`, `uq_brands_description`) | Creación, actualización, búsqueda, orden y duplicados case-insensitive. |
| `brands` | `is_active` (`BOOLEAN`, default `true`) | Desactivación lógica y filtro del listado. |
| `brands` | `created_at` / `updated_at` (`TIMESTAMPTZ`) | `created_at` se conserva; `updated_at` lo refresca el servicio con `clock_timestamp()` en cada `UPDATE` real (D-9). |
| `medications` | `brand_id` (FK `RESTRICT`) | Solo lectura implícita: la desactivación lógica no toca esta tabla (D-7). |

Índices: `uq_brands_description` ya provee el índice único para la detección de duplicados y la protección de carrera. No se crea ningún índice nuevo: el catálogo es de volumen bajo (decenas de filas) e `ILIKE` con `LIMIT 100` no justifica un índice trigram en v1; si el volumen lo exige, se añade en una feature posterior sin cambiar el contrato.

Consultas previstas (`select()`, SQLAlchemy 2.x):

```python
# Listado (filtros opcionales) + conteo
pattern = f"%{escaped}%"  # escape de \, % y _
conditions = []
if search:
    conditions.append(Brand.description.ilike(pattern, escape="\\"))
if is_active is not None:
    conditions.append(Brand.is_active.is_(is_active))
select(Brand).where(*conditions).order_by(*order).limit(size).offset(offset)
select(func.count(Brand.id)).where(*conditions)

# Duplicado excluyendo a la propia marca (el filtro de id solo se añade si hay objetivo)
conditions = [Brand.description == description]
if exclude_id is not None:
    conditions.append(Brand.id != exclude_id)
select(Brand.id).where(*conditions).limit(1)
```

### Contrato de repositorio (nombres que usa el servicio)

| Función | Firma resumida |
|---|---|
| `list_brands` | `(session, *, page, size, search, is_active, sort_field, sort_desc) -> tuple[list[Brand], int]` |
| `find_brand_by_description` | `(session, description, *, exclude_id=None) -> Brand \| None` |
| `get_brand_by_id` | `(session, brand_id) -> Brand \| None` |

## Contrato de API
Para cada endpoint: método, ruta, auth/permisos, query params, body (Pydantic), respuestas 2xx y errores con códigos HTTP, paginación.

Prefijo `/api/v1`, `Content-Type: application/json`. Todos los errores usan el envelope de la regla 5: `{"detail", "code", "errors"}`.

### Autorización común

| Endpoint | Dependencia | Error del otro rol |
|---|---|---|
| `GET /brands`, `GET /brands/{id}` | `require_role("ADMIN", "MEDICO")` | — (ambos entran) |
| `POST`, `PUT`, `DELETE` | `require_role("ADMIN")` | `MEDICO` ⇒ `403` `forbidden`, sin escritura |

Errores comunes a los cinco endpoints:

| HTTP | `code` | Cuándo |
|---|---|---|
| `401` | `not_authenticated` | sin cabecera `Authorization` o esquema distinto de `Bearer` (`WWW-Authenticate: Bearer`) |
| `401` | `invalid_token` | firma inválida, token expirado, claims `sub`/`iat`/`exp` ausentes, `sub` no numérico o usuario inexistente |
| `403` | `inactive_user` | el usuario del token ya no está activo |
| `403` | `forbidden` | el usuario del token no tiene el rol exigido (p. ej. `MEDICO` escribiendo) |

### `GET /api/v1/brands`

- **Query params:**

| Parámetro | Tipo | Obligatorio | Restricciones |
|---|---|---|---|
| `page` | `int` | No | ≥ 1, por defecto `1` |
| `size` | `int` | No | 1..100, por defecto `20` |
| `search` | `str` | No | máximo 100 caracteres; `strip`; vacío ⇒ sin filtro |
| `is_active` | `bool` | No | sin filtro por defecto |
| `sort` | `str` | No | `description`, `created_at`, `is_active`, `id` y sus formas con `-`; por defecto `description` |

- **Respuesta `200`** (`BrandListResponse`):

```json
{
  "items": [{ "id": 1, "description": "Bayer", "is_active": true }],
  "total": 1,
  "page": 1,
  "size": 20,
  "pages": 1
}
```

- **Errores:** `422` `validation_error` (parámetros fuera de rango o `sort` inválido; `field` = nombre del parámetro) además de los comunes.

### `POST /api/v1/brands`

- **Body** (`BrandCreate`):

| Campo | Tipo | Obligatorio | Restricciones |
|---|---|---|---|
| `description` | `str` | Sí | `strip`, 1..100 (D-6), capitalización conservada |
| `is_active` | `bool` | No (default `true`) | — |

```json
{ "description": "Bayer", "is_active": true }
```

- **Respuesta `201`** (`BrandRead`): `{ "id": 1, "description": "Bayer", "is_active": true }`.
- **Errores:**

| HTTP | `code` | Cuándo |
|---|---|---|
| `403` | `forbidden` | el actor es `MEDICO` |
| `409` | `brand_exists` | `description` ya existe (CITEXT, con `strip` aplicado) |
| `422` | `validation_error` | `description` ausente, vacía tras `strip` o de más de 100 caracteres |

Precedencia: `422` → `403` de rol (evaluado por la dependencia antes del servicio) → `409`.

### `GET /api/v1/brands/{brand_id}`

- **Path param:** `brand_id: int`.
- **Respuesta `200`** (`BrandRead`): misma forma que el `201`; incluye inactivas.
- **Errores:** `404` `brand_not_found`, `422` si el id no es entero, además de los comunes (ambos roles).

### `PUT /api/v1/brands/{brand_id}`

- **Path param:** `brand_id: int`. Solo `ADMIN` (`MEDICO` ⇒ `403`).
- **Body** (`BrandUpdate`, reemplazo completo — D-1):

| Campo | Tipo | Obligatorio | Restricciones |
|---|---|---|---|
| `description` | `str` | **Sí** | igual que en `BrandCreate` |
| `is_active` | `bool` | **Sí** | — (permite reactivar con `true`) |

- **Respuesta `200`** (`BrandRead`) con el estado resultante.
- **Errores:**

| HTTP | `code` | Cuándo |
|---|---|---|
| `403` | `forbidden` | el actor es `MEDICO` |
| `404` | `brand_not_found` | el id no existe |
| `409` | `brand_exists` | `description` de otra marca |
| `422` | `validation_error` | body parcial, tipos, longitudes, path no entero |

### `DELETE /api/v1/brands/{brand_id}`

- **Path param:** `brand_id: int`. Solo `ADMIN` (`MEDICO` ⇒ `403`).
- **Respuesta `204 No Content`** sin cuerpo. Idempotente (D-2).
- **Errores:**

| HTTP | `code` | Cuándo |
|---|---|---|
| `403` | `forbidden` | el actor es `MEDICO` |
| `404` | `brand_not_found` | el id no existe |
| `422` | `validation_error` | id no entero |

### OpenAPI

Cada endpoint declara `summary`, `description`, `response_model` (salvo el `204`), `responses` con los códigos de su tabla y el tag `brands`. El esquema `BearerAuth` ya existe (FEAT-002) y la seguridad es obligatoria en los cinco endpoints.

## Criterios de aceptación
- [ ] Dado <contexto>, cuando <acción>, entonces <resultado observable>.
(uno por comportamiento, incluyendo casos borde y errores)

Convención: `[test]` = aserción exacta que el reviewer implementa en `tests/**`; `[shell]` = comando con salida esperada. Los tests de integración reutilizan `db_session`, `db_conn`, `auth_client`/`auth_client_factory` y `auth_support` de FEAT-002 (ver "Plan de pruebas").

### Autorización y contrato

- [ ] **CA-1** `[test]` Con token de `MEDICO` activo: `GET /brands` y `GET /brands/{id}` ⇒ `200`; `POST`, `PUT`, `DELETE` ⇒ `403`, `code == "forbidden"`, `errors == []`, claves exactas `{detail, code, errors}` y la base no cambia (se compara `description`/`is_active` del objetivo antes y después, y el conteo de filas).
- [ ] **CA-2** `[test]` Sin cabecera ⇒ `401` `not_authenticated` y `WWW-Authenticate` empieza por `Bearer`; con `Authorization: Basic abc` ⇒ igual; con token basura o firmado con otro secreto ⇒ `401` `invalid_token`; con token de un `ADMIN` o `MEDICO` desactivado ⇒ `403` `inactive_user` (sobre `GET /brands` y `POST /brands`).
- [ ] **CA-3** `[test]` `GET /brands/{id_inexistente}` con `ADMIN` ⇒ `404`, claves exactas `{detail, code, errors}`, `code == "brand_not_found"`, `detail == "La marca no existe"`, `errors == []` y sin clave `details`.

### Listado

- [ ] **CA-4** `[test]` Sin parámetros: `page == 1`, `size == 20`, claves de nivel superior exactamente `{items, total, page, size, pages}`, `total == SELECT count(*) FROM brands` en la misma transacción, `len(items) == min(20, total)` y `pages == ceil(total / 20)`.
- [ ] **CA-5** `[test]` Límites: `size=1` y `size=100` ⇒ `200`; `size=0`, `size=101`, `size=-1`, `page=0`, `page=-1` ⇒ `422` con `field` igual a `"size"`/`"page"`; `page=9999` ⇒ `200`, `items == []`, `page == 9999`, `total` y `pages` sin cambios respecto a `page=1`.
- [ ] **CA-6** `[test]` Con marcas de descripción con prefijo único: `sort=description` ordena ascendentemente; `sort=-description` descendentemente; `sort=created_at`, `sort=-created_at`, `sort=is_active`, `sort=-is_active`, `sort=id`, `sort=-id` responden `200` y respetan el orden; `sort=description,description`, `sort=password_hash`, `sort=` ⇒ `422` con `field == "sort"`; sin `sort` el orden es `description ASC`; con dos marcas insertadas con el mismo `description` imposible por unicidad, el desempate `id ASC` se verifica ordenando por `created_at` con valores iguales.
- [ ] **CA-7** `[test]` `search` es case-insensitive y parcial sobre `description`: `search=<mitad en mayúsculas>` encuentra la marca; `search=%` devuelve solo marcas con `%` literal (se crean `Lab %50` y un `Lab 50` de control); `search=_` solo los que contienen `_`; `search=\` no rompe y busca la barra literal; `search="   "` no aplica filtro (`total` igual al de sin `search`); `search` de 101 caracteres ⇒ `422` con `field == "search"`.
- [ ] **CA-8** `[test]` `is_active=true` devuelve solo activas, `is_active=false` solo inactivas y la ausencia del parámetro devuelve ambas; combinado con `search`, los filtros se aplican en conjunto.
- [ ] **CA-9** `[test]` `search` sin coincidencias ⇒ `items == []`, `total == 0`, `pages == 0` y `page`/`size` con los valores pedidos.
- [ ] **CA-10** `[test]` Cada `item` y cada respuesta de marca tiene exactamente `{id, description, is_active}`; ninguna respuesta contiene `created_at`, `updated_at` ni claves ajenas.

### Creación

- [ ] **CA-11** `[test]` `POST /brands {"description": "Bayer"}` (sin `is_active`) con `ADMIN` ⇒ `201`, cuerpo exacto de tres claves con `is_active is True`; en base: `description` recortada, `created_at`/`updated_at` no nulos. `POST {"description": "Pfizer", "is_active": false}` ⇒ `201` con `is_active is False` y la fila nace inactiva (visible en `GET /{id}`).
- [ ] **CA-12** `[test]` Duplicado con otra capitalización (`"BAYER"` existiendo `"Bayer"`) ⇒ `409` `brand_exists`, `detail == "La marca ya existe"`; `SELECT count(*) FROM brands WHERE description = 'Bayer'` es `1`. Duplicado con espacios laterales (`"  Bayer  "`) ⇒ `409` igualmente.
- [ ] **CA-13** `[test]` `422` con `field == "description"`: body `{}`, `description` ausente, `description: ""`, `description: "   "` y `description` de 101 caracteres. `description` de 1 y de 100 caracteres ⇒ `201`. `"  Bayer  "` se almacena como `"Bayer"` (recortada, capitalización conservada).
- [ ] **CA-14** `[test]` La respuesta del `201` no contiene `created_at`/`updated_at`; con `caplog` a `INFO`, la creación incluye `brand.created` con `brand_id` y `actor_id`.

### Detalle

- [ ] **CA-15** `[test]` `GET /brands/{id}` ⇒ `200` con los mismos valores que el `item` del listado; una marca inactiva devuelve `200` con `is_active is False`; un `id` no entero (`"abc"`) ⇒ `422` con `field == "brand_id"`; accesible para `MEDICO` (200) sin diferencias respecto a `ADMIN`.

### Actualización

- [ ] **CA-16** `[test]` `PUT` completo (`{"description": "Bayer MX", "is_active": true}`) ⇒ `200` con el estado nuevo; en base cambia `description`; `created_at` **no** cambia, `updated_at` sí (posterior al valor previo leído en la misma transacción).
- [ ] **CA-17** `[test]` `PUT {"description": <misma>, "is_active": true}` sobre una marca inactiva ⇒ `200`, `is_active is True` (reactivación). `PUT {"description": <misma>, "is_active": false}` sobre una activa ⇒ `200`, `is_active is False`.
- [ ] **CA-18** `[test]` `PUT` con la `description` de otra marca (otra capitalización incluida) ⇒ `409` `brand_exists` y la fila no cambia; reenviando su propia `description` (incluso con distinta capitalización o espacios laterales) ⇒ `200`.
- [ ] **CA-19** `[test]` `PUT` con `brand_id` inexistente ⇒ `404` `brand_not_found`; body sin `is_active` ⇒ `422` con `field == "is_active"`; body sin `description` ⇒ `422` con `field == "description"` (reemplazo completo, D-1); `PUT` de `MEDICO` ⇒ `403` y la fila no cambia.

### Desactivación

- [ ] **CA-20** `[test]` `DELETE /brands/{id}` de una marca activa con `ADMIN` ⇒ `204`, `response.content == b""`; la fila sigue existiendo con `is_active = false`; el log contiene `brand.deactivated` con `brand_id` y `actor_id`.
- [ ] **CA-21** `[test]` Segundo `DELETE` sobre la misma marca ⇒ `204`, `is_active` sigue `false`, `updated_at` no cambia y no se emite un segundo `brand.deactivated`; `DELETE` de un `id` inexistente ⇒ `404`; `DELETE` con `MEDICO` ⇒ `403` y la fila sigue activa.
- [ ] **CA-22** `[test]` Desactivar una marca referenciada por un `medication` (creado por SQL directo en la transacción) ⇒ `204` y `is_active = false`; la fila de `medications` sigue intacta (D-7).

### Integridad y errores

- [ ] **CA-23** `[test]` Un `IntegrityError` inyectado en el `commit` con el nombre `uq_brands_description` se traduce a `409` `brand_exists`; no queda fila nueva y la sesión sigue usable.
- [ ] **CA-24** `[test]` Todos los errores de la feature respetan el envelope exacto `{detail, code, errors}` (sin `details`) y los códigos del catálogo de la regla 30 para cada escenario de las tablas de contrato (al menos un caso por código).
- [ ] **CA-25** `[test]` Con `caplog` a `INFO`, tras crear, actualizar y desactivar: aparecen `brand.created`, `brand.updated` y `brand.deactivated`, cada uno con `brand_id` y `actor_id`; `caplog.text` no contiene tokens ni contraseñas.

### OpenAPI y calidad

- [ ] **CA-26** `[shell]` OpenAPI:

  ```powershell
  uv run python -c "from app.main import app; o=app.openapi(); p=o['paths']; u=sorted(k for k in p if k.startswith('/api/v1/brands')); print(u); print(sorted({t for k in u for m in p[k].values() for t in m.get('tags', [])})); print(all(m.get('security') and m.get('summary') and m.get('description') for k in u for m in p[k].values())); print('content' in p['/api/v1/brands']['get']['responses']['200'], 'content' in p['/api/v1/brands']['post']['responses']['201']); print(sorted(p['/api/v1/brands']['post']['responses'])); print(sorted(p['/api/v1/brands/{brand_id}']['delete']['responses']))"
  ```

  imprime:

  ```text
  ['/api/v1/brands', '/api/v1/brands/{brand_id}']
  ['brands']
  True
  True True
  ['201', '401', '403', '409', '422']
  ['204', '401', '403', '404', '422']
  ```

  Además, `GET /api/v1/brands` declara `['200', '401', '403', '422']`, `GET /{id}` declara `['200', '401', '403', '404', '422']` y `PUT` declara `['200', '401', '403', '404', '409', '422']`.

- [ ] **CA-27** `[shell]` Sin cambios de esquema: `uv run alembic check` imprime `No new upgrade operations detected.` y `git status --porcelain alembic/` no imprime nada.
- [ ] **CA-28** `[shell]` `uv run ruff check .` sin errores y `uv run ruff format --check .` sin cambios pendientes.
- [ ] **CA-29** `[shell]` `uv run pytest -q` en verde, incluidos los tests de FEAT-002/003/004/007 (sin regresiones).

### Trazabilidad

| Requisito / fuente | Criterios |
|---|---|
| RF-05 / US-16 / E2 (CRUD de marcas) | CA-4 a CA-22 |
| CA-CRUD 1–2 (listado paginado, `items/total/page/size/pages`) | CA-4, CA-5, CA-9 |
| CA-CRUD 3 (`201`, validación, `409` duplicados) | CA-11 a CA-14, CA-23 |
| CA-CRUD 4 (edición `200`, conserva `created_at`) | CA-16 |
| CA-CRUD 5 (baja lógica, sin borrado) | CA-20 a CA-22 |
| CA-CRUD 6 (`200`/`404` por ID) | CA-3, CA-15 |
| CA-CRUD 8 (escritura solo ADMIN, `403` a médico) | CA-1, CA-19, CA-21 |
| CA-CRUD 9 (validaciones y mensajes en español) | CA-5 a CA-7, CA-13 |
| CA-CRUD 10 (Swagger + pruebas) | CA-24 a CA-29 |
| §4 matriz (`/brands` CRUD ADMIN / GET MEDICO) | CA-1, CA-2, CA-15, CA-19, CA-21 |
| §8 (`POST` + descripciones únicas) | CA-11, CA-12, CA-18, CA-23 |
| RF-17 (activar/desactivar sin eliminar) | CA-17, CA-20 a CA-22 |

## Plan de pruebas
Casos que el reviewer debe cubrir: unitarios, integración, autorización, datos inválidos.

### Aislamiento

Se reutiliza la infraestructura de FEAT-002/FEAT-004 sin cambios:

1. `db_conn` (transacción revertida) y `db_session` (`join_transaction_mode="create_savepoint"`) de `tests/conftest.py`; ninguna escritura sobrevive al test.
2. `auth_client` / `auth_client_factory` sobrescriben `get_session` con `db_session`; limpiar en `finally` (ya lo hace la fixture).
3. Los usuarios se crean con `tests/auth_support.create_auth_user` y los tokens con `tests/auth_support.token_for(user_id, role=...)` + `auth_headers`. Se necesita un `ADMIN` actor y un `MEDICO` lector por test; no se depende de datos preexistentes (`total` se compara contra `SELECT count(*) FROM brands` en la transacción).
4. Las lecturas de verificación usan `db_conn` (misma conexión ⇒ ve los cambios no confirmados de `db_session`).
5. No se usan datos personales reales: descripciones sintéticas con `unique_suffix()` (p. ej. `f"Marca {unique_suffix()}"`); los casos con `%`, `_`, `\` usan sufijos fijos controlados.
6. El `medication` de CA-22 se inserta por SQL directo con `drug_type_id`/`location_id` existentes o también insertados por SQL; todo dentro de la transacción revertida.
7. `get_login_rate_limiter()` no se toca en estos tests (ningún CA hace login salvo verificación opcional de que el actor sigue activo; máximo 1 login por test si se usa).

### Unitarias (sin base de datos)

| # | Test | CA |
|---|---|---|
| U-1 | `BrandCreate`: `description` obligatoria, `strip`, 1..100, default `is_active=True` | CA-13 |
| U-2 | `BrandUpdate`: ambos campos obligatorios, `is_active` acepta `false`, `description` con las mismas reglas | CA-19 |
| U-3 | `BrandRead`: serializa exactamente las tres claves | CA-10 |
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
| I-10 | Forma exacta de `item` en las cuatro respuestas con marca | CA-10, CA-15 |
| I-11 | Creación feliz activa/inactiva | CA-11 |
| I-12 | Duplicados `CITEXT` con capitalización y espacios | CA-12 |
| I-13 | Validaciones de `BrandCreate` y normalización `strip` | CA-13 |
| I-14 | Log de creación con `brand_id`/`actor_id` | CA-14 |
| I-15 | Detalle activo/inactivo, `422` de path y lectura `MEDICO` | CA-15 |
| I-16 | `PUT` feliz con conservación de `created_at` y refresco de `updated_at` | CA-16 |
| I-17 | `PUT` reactiva y desactiva | CA-17 |
| I-18 | `PUT` duplicados ajenos vs. propios | CA-18 |
| I-19 | `PUT` errores de body, `404` y `403` de `MEDICO` | CA-19 |
| I-20 | `DELETE` feliz, idempotente, `404` y `403` de `MEDICO` | CA-20, CA-21 |
| I-21 | `DELETE` de marca con medicamentos asociados | CA-22 |
| I-22 | Envelope uniforme por cada código del catálogo | CA-24 |

### Integridad

| # | Test | CA |
|---|---|---|
| C-1 | Traducción de `IntegrityError` por nombre de restricción (`uq_brands_description`) | CA-23 |

### Logs, OpenAPI y calidad

| # | Test | CA |
|---|---|---|
| S-1 | Los tres eventos con `brand_id`/`actor_id` y sin secretos | CA-14, CA-25 |
| O-1 | OpenAPI: paths, tag, seguridad, `summary`/`description` y códigos | CA-26 |
| Q-1 | `alembic check` y `git status --porcelain alembic/` | CA-27 |
| Q-2 | `ruff check` y `ruff format --check` | CA-28 |
| Q-3 | `pytest -q` completo | CA-29 |

## Riesgos y dependencias
Dependencias entre features, migraciones destructivas, datos sensibles.

| ID | Riesgo | Impacto | Mitigación / decisión |
|---|---|---|---|
| **R-1** | La longitud máxima de 100 para `description` es propuesta de la spec, no regla de negocio documentada (SUP-2). | Bajo | Pregunta abierta 1; el cambio es un `Field(max_length=...)` + CA-13, sin romper el contrato. |
| **R-2** | `PUT` es reemplazo completo: un cliente que envíe solo `description` recibe `422`; no hay bloqueo optimista (last write wins). | Bajo | D-1; el frontend hace `GET` antes de `PUT`. El volumen y la concurrencia de edición de catálogos son mínimos. |
| **R-3** | Desactivar una marca no impide su uso hasta que la feature de medicamentos aplique "no crear con catálogos inactivos"; temporalmente se puede prescribir con una marca inactiva. | Medio | D-7; la regla del §8 se implementa en US-20. El listado permite filtrar `is_active=true` desde el primer día. |
| **R-4** | `ILIKE` con comodines escapados y `CITEXT`: un escape mal construido convertiría `%` en comodín. | Bajo | Escapado explícito de `\`, `%` y `_` con `escape="\\"`; CA-7 lo verifica con marcas que contienen los tres caracteres. |
| **R-5** | Sin índice trigram, `search ILIKE '%...%'` es secuencial; irrelevante con decenas de filas pero costoso si el catálogo creciera a miles. | Bajo | Sin índice nuevo en v1 (sección "Modelo de datos"); si el volumen lo exige, índice trigram en feature posterior sin cambio de contrato. |
| **R-6** | `sort=is_active` ordena booleanos (`false < true`); el desempate `id ASC` lo hace determinista pero el orden entre grupos solo es útil combinado con `search`. | Bajo | Catálogo cerrado en D-3; CA-6 verifica ambos sentidos. |

**Dependencias**

- **FEAT-001 (`done`)**: FastAPI, sesión async, logging con redacción, envelope y middlewares.
- **FEAT-003 (`done`)**: tabla `brands` (`CITEXT`, `uq_brands_description`, `is_active` default `true`), FK `medications.brand_id` (`RESTRICT`).
- **FEAT-002 (`done`)**: `get_current_user`, `require_role` (incluye `require_role("ADMIN", "MEDICO")` — ambos roles están en `ALLOWED_ROLES`), envelope `{detail, code, errors}`.
- **FEAT-004 (`done`, patrón)**: paginación (`page/size/search/is_active/sort`), `_validation_field` (D-13), `204` idempotente (D-2), `clock_timestamp()` (D-14), estructura routes → services → repositories.
- **FEAT-007 (`done`, plantilla directa)**: el implementer clona `drug_types` renombrando a `brand_*` (`brand_not_found` / `brand_exists`, eventos `brand.*`, sort default `description`).
- **Bloquea**: la feature de medicamentos (US-20) necesita este CRUD para poblar `brand_id`.
- **No modifica** `app/core/config.py`, `app/api/deps.py`, `app/core/security.py`, `app/models/`, `alembic/` ni dependencias de `pyproject.toml`.

**Datos sensibles**

- `description` es un nombre de catálogo (p. ej. "Bayer"), no dato de paciente; aun así no se registra en logs (D-11).
- Los tests usan descripciones sintéticas dentro de una transacción revertida; no se usan datos personales reales.
- `audit_log` no se escribe en esta feature (RF-22 fuera de v1); el rastro queda en los eventos estructurados.

## Preguntas abiertas

1. **Longitud de `description`**: ninguna fuente fija mínimo ni máximo (el §8 solo muestra `"Bayer"` y exige unicidad). La spec propone 1..100 tras `strip` (D-6/SUP-2). ¿Se confirma 100 o se fija otro valor (p. ej. 50, 150, 255)? **No bloquea**: el implementer usa 100 y el ajuste posterior es localizado.
2. **¿Debe normalizarse la capitalización al guardar** (p. ej. Title Case) o se conserva tal como se envía? La spec propone conservarla (D-6, patrón FEAT-004 D-12). **No bloquea**: el comportamiento propuesto es implementable y verificable (CA-13).

No hay más bloqueos: permisos (matriz §3/§4), forma del `POST` (§8), paginación (§6), unicidad case-insensitive (`CITEXT` en FEAT-003) y baja lógica (RF-17/CA-CRUD 5) están definidos en las fuentes.

## Historial
- 2026-10-21 — creada por spec-author.
- 2026-10-21 — `proposed` → `approved`: aprobación explícita del humano ("Sí, aprobar e iniciar").
- 2026-10-21 — `approved` → `in_progress`: inicio de implementación con patrón FEAT-007.
- 2026-10-21 — `in_progress` → `in_review`: implementación completa (schemas, repository, service, routes).
- 2026-10-21 — `in_review` → `done`: reviewer `APPROVED` (39 tests nuevos, 426 en verde, ruff/alembic limpios).
