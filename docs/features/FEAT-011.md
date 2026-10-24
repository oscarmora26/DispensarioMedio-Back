# FEAT-011 — Gestión de pacientes: CRUD, validación de cédula/carnet y búsqueda rápida

**Estado:** `approved` (aprobada explícitamente por el humano el 2026-10-24)
**Fecha:** 2026-10-24
**Alcance:** `GET/POST /api/v1/patients`, `GET/PUT/DELETE /api/v1/patients/{patient_id}`, con lectura para `ADMIN` y `MEDICO` y escritura solo para `ADMIN`, reutilizando autenticación JWT, envelope de error `{detail, code, errors}` y desactivación lógica de FEAT-002/FEAT-004 y el patrón de catálogos de FEAT-007…FEAT-010.
**Fuente de verdad funcional:** `DispensarioMedicoDocs/01-requerimientos-y-backlog.md` RF-08, RF-17, RF-18, US-23/US-24/US-25, E4, §3 (matriz: Pacientes CRUD ADMIN / Consulta MEDICO) y §7 (CA-CRUD); `DispensarioMedicoDocs/03-api-rest.md` §2 (paginación, envelope, códigos), §4 (matriz `/patients`: CRUD ADMIN / GET MEDICO), §5 (endpoints), §6 (parámetros de listado) y §9 (`POST /patients` con `name/cedula/carnet/patient_type/is_active` + reglas: cédula de 11 dígitos y única, carnet único cuando existe, carnet obligatorio para estudiantes, `patient_type` en `ESTUDIANTE`/`EMPLEADO`/`PROFESOR`/`OTROS`, búsqueda por cédula, carnet o nombre).

## Contexto

La tabla `patients` ya existe (FEAT-003, `app/models/patient.py`): `name VARCHAR(150)`, `cedula VARCHAR(11)` con `CHECK cedula ~ '^[0-9]{11}$'` y `UNIQUE uq_patients_cedula`, `carnet CITEXT NULL` con índice único parcial `uq_patients_carnet`, `patient_type VARCHAR(20)` con `CHECK patient_type IN ('ESTUDIANTE','EMPLEADO','PROFESOR','OTROS')`, `CHECK carnet_required_for_student`, `is_active BOOLEAN DEFAULT true`, índice trigram `ix_patients_name_trgm` y `created_at`/`updated_at`. Hoy **no existe ninguna vía HTTP para crear, consultar, actualizar ni desactivar pacientes**.

Sin esta feature, RF-08, RF-17 y RF-18 y las historias US-23/US-24/US-25 (Sprint 5) quedan sin implementar, y FEAT-013 (visitas) no tiene pacientes seleccionables: el `POST /visits` exige `patient_id` de un paciente **activo**.

## Alcance

- `app/schemas/patient.py` (nuevo): `PatientCreate`, `PatientUpdate`, `PatientRead`, `PatientListResponse`, `PATIENT_TYPE_VALUES`, `SORT_FIELDS`, `SORT_PATTERN`.
- `app/repositories/patient_repository.py` (nuevo): listado paginado con búsqueda (`name`/`cedula`/`carnet`) y filtro de estado, `get_patient_by_id`, detección de duplicados de `cedula` y de `carnet`.
- `app/services/patient_service.py` (nuevo): `list_patients`, `get_patient`, `create_patient`, `update_patient`, `deactivate_patient`; traducción de `IntegrityError` (`uq_patients_cedula` / `uq_patients_carnet` / `cedula_format` / `carnet_required_for_student` / `patient_type_allowed`).
- `app/api/v1/routes/patients.py` (nuevo) con tag `patients`, registrado en `app/api/v1/router.py`.
- Reutilización de: `app/api/deps.py` (`get_current_user`, `require_role`), `app/core/exceptions.py` (`AppError`, `NotFoundError`, `ConflictError`, envelope), `app/models/patient.py`, y el patrón de `drug_types` (FEAT-007) como referencia estructural.
- Cinco endpoints descritos en "Contrato de API".
- Logs estructurados `patient.created`, `patient.updated`, `patient.deactivated`.
- Documentación OpenAPI completa (tag, `summary`, `description`, `response_model`, códigos).
- Sin migraciones: `uv run alembic check` debe seguir limpio.

### Estructura de archivos resultante

```text
app/
├── api/v1/
│   ├── router.py                 # modificado: incluye patients
│   └── routes/patients.py        # nuevo
├── repositories/
│   └── patient_repository.py     # nuevo
├── schemas/patient.py            # nuevo
└── services/patient_service.py   # nuevo
```

## Fuera de alcance

- Visitas (`/visits`): FEAT-013.
- Asociación paciente↔visita o historial clínico: FEAT-013/FEAT-014.
- Teléfono, correo, dirección o fecha de nacimiento del paciente: no están en el contrato del §9 y no se añaden.
- Búsqueda trigram/`pg_trgm` en la consulta: el índice existe desde FEAT-003, pero la búsqueda usa `ILIKE` con patrón escapado, igual que los catálogos previos.
- Frontend Angular.

## Reglas de negocio

### Autorización y alcance

1. Lectura (`GET /patients`, `GET /patients/{id}`): `Authorization: Bearer <token>` y rol `ADMIN` **o** `MEDICO`, leído de la base (`user.role.name`) mediante `require_role("ADMIN", "MEDICO")`. El claim `role` del token no se usa (FEAT-002 regla 12).
2. Escritura (`POST`, `PUT`, `DELETE`): rol `ADMIN` exclusivamente, mediante `require_role("ADMIN")`. Un `MEDICO` autenticado recibe `403` `forbidden` en las tres, sin efectos en la base (CA-CRUD 8).
3. Sin cabecera, con esquema distinto de `Bearer`, con token inválido/expirado o de un usuario inexistente ⇒ `401` (`not_authenticated` o `invalid_token`).
4. Un `ADMIN` o `MEDICO` desactivado pierde el acceso en la siguiente request (`403` `inactive_user`), incluso si el token sigue vigente (FEAT-002 regla 13).
5. Todos los errores usan el envelope global `{"detail": str, "code": str, "errors": [{"field","message"}]}`; `errors` es `[]` cuando no aplica. Códigos nuevos: `patient_not_found` (404), `patient_exists` (409, cédula duplicada), `patient_carnet_exists` (409, carnet duplicado). Reutilizados: `not_authenticated` / `invalid_token` (401), `inactive_user` / `forbidden` (403) y `validation_error` (422).

### Forma de un paciente (D-4)

6. Cada paciente devuelto tiene **exactamente** `id`, `name`, `cedula`, `carnet`, `patient_type`, `is_active`. `carnet` es `null` cuando no existe. Nunca se devuelven `created_at`/`updated_at` (CA-CRUD 4 exige **conservar** `created_at`, no exponerlo).

### Validación de campos (D-6, RF-18)

7. `name`: se recorta (`strip`); vacío tras el recorte ⇒ `422` `validation_error` con `field == "name"` y mensaje `"El nombre es obligatorio"`. Longitud máxima 150 caracteres. Capitalización conservada.
8. `cedula`: se recorta (`strip`); debe cumplir **exactamente 11 dígitos ASCII** (`^[0-9]{11}$`, se admiten ceros iniciales). Cualquier otro valor (9 o 10 dígitos, 12 dígitos, letras, guiones, espacios internos, símbolos, vacío) ⇒ `422` `validation_error` con `field == "cedula"` y el mensaje literal del §2 del documento de API: `"La cédula debe tener 11 dígitos"`. No se valida dígito verificador (pregunta abierta §14.5 resuelta: solo longitud y dígitos).
9. `carnet`: se recorta (`strip`); vacío o solo espacios ⇒ `null` (D-2). Con contenido: longitud máxima 50 caracteres; el valor se conserva tal cual (comparado case-insensitive por `CITEXT` en la base).
10. `patient_type`: debe ser exactamente uno de `ESTUDIANTE`, `EMPLEADO`, `PROFESOR`, `OTROS` (mayúsculas). Cualquier otro valor, minúsculas incluidas ⇒ `422` `validation_error` con `field == "patient_type"` y mensaje `"El tipo de paciente no es válido"`.
11. **Carnet obligatorio para estudiantes** (`CHECK carnet_required_for_student`, §9): `patient_type == "ESTUDIANTE"` y `carnet` `null` ⇒ `422` `validation_error` con `field == "carnet"` y mensaje `"El carnet es obligatorio para estudiantes"`. Para `EMPLEADO`, `PROFESOR` y `OTROS` el carnet es opcional (pregunta abierta §14.6 resuelta: solo estudiantes).
12. `is_active` es booleano; en `POST` es opcional con valor por defecto `true` (D-5); en `PUT` es obligatorio.
13. Los mensajes de las reglas 7–11 van en español (CA-CRUD 9) y viajan en `errors[0]` con el nombre del campo en `field`, sin el prefijo `body` (D-13 de FEAT-004 ya implementado).

### Listado `GET /patients`

14. Parámetros: `page` (entero ≥ 1, por defecto `1`), `size` (entero 1..100, por defecto `20`), `search` (texto, máximo 150 caracteres), `is_active` (booleano opcional) y `sort` (catálogo cerrado de D-3, por defecto `name`).
15. `page < 1`, `size < 1`, `size > 100`, `sort` fuera del catálogo, `sort` vacío o `search` de más de 150 caracteres ⇒ `422` `validation_error` con `field` igual al nombre del parámetro.
16. `search` se recorta (`strip`). Vacío o solo espacios ⇒ **sin filtro**. Con contenido, búsqueda **case-insensitive y parcial** en `patients.name` **o** `patients.cedula` **o** `patients.carnet` (condición `OR`) con `ILIKE` y los caracteres `%`, `_` y `\` escapados literalmente mediante el helper `escape_like` del repositorio de catálogos. Buscar `%` encuentra solo los pacientes cuyo nombre, cédula o carnet contiene un `%`, nunca todos. US-25 (búsqueda rápida por cédula, carnet o nombre) se satisface con `search` + `is_active=true`.
17. `is_active` ausente ⇒ **sin filtro** (devuelve activos e inactivos, CA-CRUD 1). `is_active=true` ⇒ solo activos; `is_active=false` ⇒ solo inactivos. Se combina en `AND` con `search`.
18. Orden: campo del catálogo de D-3, ascendente por defecto y descendente con prefijo `-`. Siempre se añade `patients.id ASC` como desempate estable, salvo cuando el campo ordenado ya es `id`. `created_at` usa `NULLS LAST` en ambos sentidos.
19. `total` es el número de filas que cumplen los filtros (sin `LIMIT`). `pages = ceil(total / size)` y exactamente `0` cuando `total == 0`. `page` se devuelve tal como se pidió aunque supere `pages` (en ese caso `items` es `[]`).

### Creación `POST /patients`

20. Body: `name`, `cedula`, `patient_type` obligatorios; `carnet` e `is_active` opcionales. Ejemplo literal del §9: `{"name": "Oscar Mora", "cedula": "00112345678", "carnet": "A00112548", "patient_type": "ESTUDIANTE", "is_active": true}`.
21. Se aplican las validaciones 7–12.
22. **Cédula única** (`uq_patients_cedula`): una cédula ya registrada, comparada por igualdad exacta (11 dígitos ⇒ sin ambigüedad de capitalización), incluso si el paciente existente está inactivo ⇒ `409` `patient_exists` con mensaje `"El paciente ya existe"`. No se modifica la fila existente (no es *upsert*).
23. **Carnet único cuando existe** (`uq_patients_carnet`, índice único parcial): un `carnet` no nulo ya registrado, comparado **case-insensitive** por `CITEXT`, incluso si el paciente existente está inactivo ⇒ `409` `patient_carnet_exists` con mensaje `"El carnet ya está registrado"`. Dos pacientes sin carnet (`null`) nunca colisionan entre sí.
24. **Precedencia de duplicados**: primero `cedula` (409 `patient_exists`), después `carnet` (409 `patient_carnet_exists`).
25. `201` con la forma de D-4. Crear un paciente inactivo está permitido. Si el `commit` viola `uq_patients_cedula` o `uq_patients_carnet` (carrera), se traduce al mismo `409` correspondiente (D-8) y la sesión queda usable.

### Detalle `GET /patients/{patient_id}`

26. `patient_id` debe ser un entero positivo; un valor no entero ⇒ `422` (validación de path). Un entero sin fila ⇒ `404` `patient_not_found` con mensaje `"El paciente no existe"`.
27. Devuelve `200` con la forma de D-4, incluidos los pacientes inactivos (`is_active: false`). Accesible para `ADMIN` y `MEDICO`.

### Actualización `PUT /patients/{patient_id}`

28. **Reemplazo completo** (D-1): el body exige `name`, `cedula`, `patient_type` e `is_active` (presente, puede ser `false`); `carnet` es opcional, y su ausencia **borra** el carnet (`null`) porque el reemplazo es completo. Un body parcial ⇒ `422` con `field` del campo ausente.
29. Permite reactivar (`is_active: true`) y desactivar (`is_active: false`); aplica las mismas validaciones 7–12 y las mismas reglas de unicidad 22–24, comprobando los duplicados **excluyendo al propio paciente**: reenviar su misma cédula o su mismo carnet (con otra capitalización) ⇒ `200`; la cédula de otro paciente ⇒ `409` `patient_exists`; el carnet de otro paciente ⇒ `409` `patient_carnet_exists`.
30. Precedencia de errores: (a) `401`/`403`; (b) `422` de Pydantic (path, query y body); (c) `404` del paciente de la ruta; (d) `409` de cédula; (e) `409` de carnet; (f) escritura.
31. Solo `ADMIN`. La actualización conserva `created_at` y refresca `updated_at` explícitamente con `clock_timestamp()` (D-9). Responde `200` con la forma de D-4.

### Desactivación `DELETE /patients/{patient_id}`

32. Es una **desactivación lógica**: `is_active = false`, sin `DELETE` SQL (CA-CRUD 5, RF-17). La fila y sus referencias (`visits.patient_id`) se conservan.
33. Es **idempotente**: desactivar un paciente ya inactivo ⇒ `204` sin modificar la fila ni su `updated_at`, y sin emitir un segundo log de desactivación.
34. Un `patient_id` sin fila ⇒ `404` `patient_not_found`. Un `patient_id` no entero ⇒ `422`.
35. Solo `ADMIN`. Respuesta `204` **sin cuerpo** (sin `Content-Length`), como en FEAT-004 regla 31.

### Registro (logs)

36. Eventos `patient.created`, `patient.updated` y `patient.deactivated` con `patient_id` y `actor_id`, y **nunca** `name`, `cedula`, `carnet` ni ningún dato personal del paciente (AGENTS.md: ningún dato de paciente en logs). Un `PUT` que cambia `is_active` de `true` a `false` emite `patient.deactivated`; cualquier otro `PUT` emite `patient.updated`.

## Supuestos

- SUP-1: la base ya tiene aplicada la migración de FEAT-003 que crea `patients` con sus constraints; no hay migraciones nuevas.
- SUP-2: `pgcrypto`/`pg_trgm` y el índice trigram ya existen; esta feature no los usa.
- SUP-3: no hay datos de pacientes en producción que violen los nuevos validadores; el `CHECK` de la base ya imponía las mismas reglas.
- SUP-4: los tokens de los actores de prueba (`ADMIN`/`MEDICO`) existen en la suite desde FEAT-002.

## Decisiones técnicas y desviaciones documentadas

- D-1: `PUT` es **reemplazo completo** de los cinco campos (mismo criterio que FEAT-007…FEAT-010), no `PATCH` parcial. La ausencia de `carnet` en el body lo borra.
- D-2: `carnet` vacío o de solo espacios se normaliza a `null` **antes** de la validación de estudiante, para que `{"carnet": "   "}` cuente como ausente.
- D-3: catálogo cerrado de `sort`: `name`, `cedula`, `created_at`, `is_active`, `id`. `name` es el orden por defecto. `SORT_PATTERN = r"^-?(?:name|cedula|created_at|is_active|id)$"`.
- D-4: la forma de lectura es plana y exacta (`id`, `name`, `cedula`, `carnet`, `patient_type`, `is_active`), sin `created_at`/`updated_at` ni anidación, igual que FEAT-007…FEAT-010.
- D-5: `is_active` por defecto `true` en `POST`; obligatorio en `PUT`.
- D-6: longitudes: `name` 1..150 (coincide con `VARCHAR(150)`), `carnet` 1..50 (la columna es `CITEXT` sin límite; se acota en el schema). `cedula` 11 exactos.
- D-7: se reutiliza el helper `escape_like` exportado por `app/repositories/drug_type_repository.py` en lugar de duplicarlo; si se prefiere aislarlo, se mueve a `app/repositories/__init__.py` sin cambiar su firma (el implementer puede elegir, documentándolo).
- D-8: la unicidad se comprueba antes de escribir (consulta) **y** en el `commit` traduciendo `IntegrityError` por `constraint_name` (carrera). Tras el fallo se hace `rollback` para que la sesión quede usable.
- D-9: `updated_at` se refresca con `func.clock_timestamp()` en lugar de depender de `onupdate=func.now()` (misma desviación documentada en FEAT-004 D-14 y FEAT-007…FEAT-010).
- D-10: la cédula se compara por igualdad exacta porque es numérica de longitud fija; no se normaliza ni se compara case-insensitive.
- D-11: la regla de carnet obligatorio se valida en el schema (`model_validator` sobre el modelo creado) y la base la reimpone con `carnet_required_for_student`; el servicio traduce esa constraint a `422` si llegara por otra vía.
- D-12: el tag OpenAPI es `patients` y el prefijo del router `/patients` (plural, `snake_case` de tabla), igual que el §5 del documento de API.

## Contrato de repositorio (nombres que usa el servicio)

```python
# app/repositories/patient_repository.py
SORT_COLUMNS: dict[str, InstrumentedAttribute[Any]]

async def get_patient_by_id(session: AsyncSession, patient_id: int) -> Patient | None
async def find_patient_by_cedula(
    session: AsyncSession, cedula: str, *, exclude_id: int | None = None
) -> Patient | None
async def find_patient_by_carnet(
    session: AsyncSession, carnet: str, *, exclude_id: int | None = None
) -> Patient | None
async def list_patients(
    session: AsyncSession,
    *,
    page: int,
    size: int,
    search: str | None,
    is_active: bool | None,
    sort_field: str,
    sort_desc: bool,
) -> tuple[list[Patient], int]
```

`find_patient_by_carnet` compara con igualdad (`CITEXT` ya es case-insensitive) y devuelve la entidad completa para el mensaje de conflicto.

## Contrato de API

### Autorización común

| Situación | Respuesta |
|---|---|
| Sin token / token inválido / expirado | `401` `not_authenticated` o `invalid_token` |
| Usuario inactivo | `403` `inactive_user` |
| `MEDICO` en `POST`/`PUT`/`DELETE` | `403` `forbidden` |

### `GET /api/v1/patients`

Query: `page` (≥1, defecto 1), `size` (1..100, defecto 20), `search` (≤150), `is_active` (bool opcional), `sort` (defecto `name`).

`200`:

```json
{
  "items": [
    {
      "id": 100,
      "name": "Oscar Mora",
      "cedula": "00112345678",
      "carnet": "A00112548",
      "patient_type": "ESTUDIANTE",
      "is_active": true
    }
  ],
  "total": 1,
  "page": 1,
  "size": 20,
  "pages": 1
}
```

Errores: `401`, `403`, `422`.

### `POST /api/v1/patients`

```json
{
  "name": "Oscar Mora",
  "cedula": "00112345678",
  "carnet": "A00112548",
  "patient_type": "ESTUDIANTE",
  "is_active": true
}
```

`201` con la forma de D-4. Errores: `401`, `403`, `409` `patient_exists` / `patient_carnet_exists`, `422` `validation_error`.

Ejemplo de `422` (literal del §2):

```json
{
  "detail": "Datos inválidos",
  "code": "validation_error",
  "errors": [
    { "field": "cedula", "message": "La cédula debe tener 11 dígitos" }
  ]
}
```

### `GET /api/v1/patients/{patient_id}`

`200` con la forma de D-4 (incluye inactivos). Errores: `401`, `403`, `404` `patient_not_found`, `422`.

### `PUT /api/v1/patients/{patient_id}`

Body con los cinco campos (D-1): `name`, `cedula`, `carnet` (opcional; ausente ⇒ `null`), `patient_type`, `is_active`.

`200` con la forma de D-4. Errores: `401`, `403`, `404`, `409`, `422`.

### `DELETE /api/v1/patients/{patient_id}`

`204` sin cuerpo, idempotente. Errores: `401`, `403`, `404`, `422`.

### OpenAPI

- `tags=["patients"]`, `summary` y `description` en cada endpoint, en español.
- `response_model` en `GET`/`POST`/`PUT`; `204` con `response_class=Response` en `DELETE`.
- Códigos documentados en `responses` para cada endpoint.
- `SORT_PATTERN` reutilizado en el `Query` del `sort`.

## Criterios de aceptación

### Autorización

- CA-1: `GET /patients` y `GET /patients/{id}` responden `200` con token `ADMIN` y con token `MEDICO`.
- CA-2: `POST`, `PUT` y `DELETE` responden `403` `forbidden` con token `MEDICO`, y la base no cambia (ni filas ni `updated_at`).
- CA-3: los cinco endpoints responden `401` sin cabecera `Authorization` y con un token inválido.
- CA-4: un usuario desactivado con token vigente recibe `403` `inactive_user` en los cinco endpoints.

### Listado

- CA-5: respuesta con `items`, `total`, `page`, `size`, `pages`; `pages == 0` cuando `total == 0` y `pages == ceil(total/size)` si no.
- CA-6: `size > 100` y `page < 1` ⇒ `422`; `page` fuera de rango ⇒ `200` con `items == []`.
- CA-7: `sort` por `name`, `-name`, `cedula`, `-created_at`, `is_active`, `id`; `sort` inválido ⇒ `422`.
- CA-8: `search` encuentra por nombre parcial (case-insensitive), por cédula exacta y por carnet (case-insensitive).
- CA-9: `search="%"` no devuelve todos los pacientes; solo los que contienen `%` literal.
- CA-10: `is_active` ausente devuelve activos e inactivos; `true` solo activos; `false` solo inactivos; combinado con `search` aplica `AND`.
- CA-11: cada `item` tiene exactamente las seis claves de D-4.

### Creación

- CA-12: `POST` válido con `ESTUDIANTE` + carnet ⇒ `201` con la forma de D-4 y `is_active: true` por defecto.
- CA-13: `POST` con `is_active: false` ⇒ `201` con `is_active: false`.
- CA-14: cédula duplicada (incluido un paciente inactivo) ⇒ `409` `patient_exists`; carnet duplicado con distinta capitalización ⇒ `409` `patient_carnet_exists`.
- CA-15: dos pacientes con `carnet: null` ⇒ ambos `201`.
- CA-16: `cedula` con 10 u 12 dígitos, con letras o con guiones ⇒ `422` con `field == "cedula"` y el mensaje `"La cédula debe tener 11 dígitos"`.
- CA-17: `ESTUDIANTE` sin carnet, o con carnet de solo espacios ⇒ `422` con `field == "carnet"` y el mensaje `"El carnet es obligatorio para estudiantes"`.
- CA-18: `patient_type` fuera del catálogo o en minúsculas ⇒ `422` con `field == "patient_type"`.
- CA-19: `name` vacío o de solo espacios ⇒ `422` con `field == "name"`; `name` de 150 caracteres ⇒ `201`; de 151 ⇒ `422`.
- CA-20: `carnet` en `EMPLEADO`/`PROFESOR`/`OTROS` es opcional; `{"carnet": "  "}` se guarda como `null`.

### Detalle

- CA-21: `GET` de un paciente existente ⇒ `200` con la forma de D-4; de uno inactivo ⇒ `200` con `is_active: false`.
- CA-22: `GET` de un id inexistente ⇒ `404` `patient_not_found`; id no entero ⇒ `422`.

### Actualización

- CA-23: `PUT` completo ⇒ `200`, `created_at` se conserva y `updated_at` cambia.
- CA-24: `PUT` sin `carnet` borra el carnet de un `EMPLEADO` ⇒ `200` con `carnet: null`.
- CA-25: `PUT` con la propia cédula y el propio carnet (distinta capitalización) ⇒ `200`, sin `409`.
- CA-26: `PUT` con la cédula de otro paciente ⇒ `409` `patient_exists`; con el carnet de otro ⇒ `409` `patient_carnet_exists`.
- CA-27: `PUT` con `is_active: false` desactiva y emite `patient.deactivated`; `PUT` que reactiva ⇒ `200` con `is_active: true`.
- CA-28: `PUT` de un id inexistente ⇒ `404`; body parcial ⇒ `422` con el campo ausente; `PUT` que deja a un `ESTUDIANTE` sin carnet ⇒ `422`.

### Desactivación

- CA-29: `DELETE` ⇒ `204` sin cuerpo y el paciente queda `is_active: false` en el `GET`.
- CA-30: segundo `DELETE` ⇒ `204` y `updated_at` no cambia.
- CA-31: `DELETE` de un id inexistente ⇒ `404`.

### Integridad y errores

- CA-32: el envelope de error siempre tiene `detail`, `code` y `errors`, y nunca la clave `details`.
- CA-33: una violación de unicidad provocada en el `commit` se traduce al `409` correspondiente y la sesión sigue usable (siguiente request `200`).

### OpenAPI y calidad

- CA-34: `/openapi.json` incluye las cinco rutas con `summary`, `description`, tag `patients` y los códigos documentados.
- CA-35: `uv run ruff check .` y `uv run ruff format --check .` limpios; `uv run alembic check` sin diferencias; suite completa en verde.

### Trazabilidad

| CA | Fuente |
|---|---|
| CA-1…CA-4 | CA-CRUD 8, §4 matriz `/patients`, FEAT-002 reglas 11–13 |
| CA-5…CA-11 | CA-CRUD 1–2, §6, US-25 |
| CA-12…CA-20 | CA-CRUD 3, 9; §9; US-24; RF-18 |
| CA-21…CA-22 | CA-CRUD 6 |
| CA-23…CA-28 | CA-CRUD 4; §9 |
| CA-29…CA-31 | CA-CRUD 5, RF-17 |
| CA-32…CA-35 | §2, §14, §10; Definition of Done |

## Plan de pruebas

### Aislamiento

Un test = una transacción con savepoint deshecho al final (mismo patrón que FEAT-007…FEAT-010 y `tests/db_support.py`). Los tests de integración usan `tests/patients_support.py` con helpers de creación directa y limpieza por savepoint.

### Unitarias (sin base de datos)

- Validadores de `name`/`cedula`/`carnet`/`patient_type` y la regla de carnet obligatorio (CA-16…CA-20, CA-28).
- `parse_sort` y `calculate_pages` (CA-5, CA-7).
- `escape_like` a través del comportamiento de búsqueda (CA-9).

### Integración (PostgreSQL + httpx)

- CRUD completo con `ADMIN` (CA-12…CA-15, CA-21, CA-23…CA-27, CA-29, CA-30).
- Autorización con `MEDICO` y sin token (CA-1…CA-4).
- Listado, paginación, búsqueda y filtros (CA-5…CA-11).
- Detalle y errores (CA-22, CA-31).
- Traducción de `IntegrityError` y sesión usable (CA-33).
- OpenAPI (CA-34).

### Logs, OpenAPI y calidad

`caplog`/`structlog` capturado: los tres eventos existen, llevan `patient_id`/`actor_id` y no contienen `name`, `cedula` ni `carnet` (regla 36).

## Riesgos y dependencias

- Depende de FEAT-003 (`patients`) y FEAT-002/004 (auth y envelope). Sin migraciones.
- FEAT-013 (visitas) depende de esta feature para `patient_id`.
- Riesgo bajo de datos: solo se añaden endpoints; no se modifican tablas ni datos existentes.

## Preguntas abiertas

Ninguna bloqueante. Resueltas por el documento de API: cédula sin dígito verificador (§14.5), carnet obligatorio solo para estudiantes (§14.6), `patient_type` como catálogo cerrado (§9).

## Historial

| Fecha | Cambio |
|---|---|
| 2026-10-24 | Spec inicial para implementación (aprobación humana del bloque FEAT-011…014). |
| 2026-10-24 | Entrega en un único commit local (`feat(FEAT-011)`): 2.591 líneas — código 716, tests 1.492, spec 357. **Boundary de slice**: supera la guía de ~400 líneas por commit; el slice se justifica porque código y tests del mismo comportamiento no se separan, y queda registrado aquí para no fragmentar a posteriori. Para FEAT-012…FEAT-014 la estrategia será: un commit por grupo de endpoints con sus tests (p. ej. `CRUD de médicos` y `asociación usuario-médico` por separado), manteniendo juntos comportamiento y tests. |
