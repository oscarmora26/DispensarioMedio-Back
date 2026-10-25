# FEAT-012 — Gestión de médicos: CRUD, asociación usuario-médico y perfil

**Estado:** `approved` (aprobada explícitamente por el humano el 2026-10-24, en bloque con FEAT-011…FEAT-014)
**Fecha:** 2026-10-24
**Alcance:** `GET/POST /api/v1/doctors`, `GET/PUT/DELETE /api/v1/doctors/{doctor_id}`, con lectura para `ADMIN` y `MEDICO` (el médico solo su propio perfil, US-28) y escritura solo para `ADMIN`, reutilizando autenticación JWT, envelope `{detail, code, errors}` y desactivación lógica de FEAT-002/FEAT-004 y el patrón de catálogos de FEAT-007…FEAT-010 y de pacientes de FEAT-011.
**Fuente de verdad funcional:** `DispensarioMedicoDocs/01-requerimientos-y-backlog.md` RF-09, RF-10, RF-17, RF-18, US-26/US-27/US-28/US-29, E5, §3 (matriz: Médicos CRUD ADMIN / Consulta de su perfil MEDICO) y §7 (CA-CRUD); `DispensarioMedicoDocs/03-api-rest.md` §2 (paginación, envelope, códigos), §4 (matriz `/doctors`: CRUD ADMIN / GET propio MEDICO), §5 (endpoints), §6 (parámetros de listado) y §10 (`POST /doctors` con `name/cedula/lab_shift/specialty/is_active` + reglas: cédula única de 11 dígitos, no se elimina si tiene visitas, un médico puede existir sin usuario).

## Contexto

La tabla `doctors` ya existe (FEAT-003, `app/models/doctor.py`): `name VARCHAR(150)`, `cedula VARCHAR(11)` con `CHECK cedula ~ '^[0-9]{11}$'` (`ck_doctors_cedula_format`) y `UNIQUE uq_doctors_cedula`, `lab_shift VARCHAR(50)`, `specialty VARCHAR(100)`, `is_active BOOLEAN DEFAULT true`, índice trigram `ix_doctors_name_trgm` y `created_at`/`updated_at`. La asociación usuario↔médico también existe desde FEAT-003/FEAT-004: `users.doctor_id` con `UNIQUE uq_users_doctor` (uno a uno) y validación en `/users` (solo rol `MEDICO` puede tener `doctor_id`; un `ADMIN` no; `doctor_already_assigned` en duplicados). `GET /auth/me` y `POST /auth/login` ya devuelven `doctor_id`.

Hoy **no existe ninguna vía HTTP para crear, consultar, actualizar ni desactivar médicos**. Sin esta feature, RF-09, RF-10 y RF-17 y las historias US-26/US-27/US-28 (Sprint 6) quedan sin implementar, y FEAT-013 (visitas) no tiene médicos seleccionables ni puede resolver "el médico se obtiene del token" (`doctor_id` en `users`).

## Alcance

- `app/schemas/doctor.py` (nuevo): `DoctorCreate`, `DoctorUpdate`, `DoctorRead`, `DoctorListResponse`, `SORT_FIELDS`, `SORT_PATTERN`.
- `app/repositories/doctor_repository.py` (ampliado): conserva `get_doctor_by_id` (usado por FEAT-004) y añade listado paginado con búsqueda (`name`/`cedula`), filtro de estado, filtro de perfil propio (`doctor_id`), detección de duplicados de `cedula` y conteo de visitas.
- `app/services/doctor_service.py` (nuevo): `list_doctors`, `get_doctor`, `create_doctor`, `update_doctor`, `deactivate_doctor`; traducción de `IntegrityError` (`uq_doctors_cedula` / `ck_doctors_cedula_format`).
- `app/api/v1/routes/doctors.py` (nuevo) con tag `doctors`, registrado en `app/api/v1/router.py`.
- Reutilización de: `app/api/deps.py` (`get_current_user`, `require_role`), `app/core/exceptions.py` (`AppError`, `NotFoundError`, `ConflictError`, envelope), `app/models/doctor.py`, `app/models/visit.py` (solo conteo, D-7) y el patrón de `patients` (FEAT-011) como referencia estructural.
- Cinco endpoints descritos en "Contrato de API".
- Logs estructurados `doctor.created`, `doctor.updated`, `doctor.deactivated`.
- Documentación OpenAPI completa (tag, `summary`, `description`, `response_model`, códigos).
- Sin migraciones: `uv run alembic check` debe seguir limpio.

### Estructura de archivos resultante

```text
app/
├── api/v1/
│   ├── router.py                 # modificado: incluye doctors
│   └── routes/doctors.py         # nuevo
├── repositories/
│   └── doctor_repository.py      # ampliado (get_doctor_by_id ya existe)
├── schemas/doctor.py             # nuevo
└── services/doctor_service.py    # nuevo
```

## Fuera de alcance

- Visitas (`/visits`): FEAT-013. Esta feature solo lee la existencia de visitas para la regla de desactivación (D-7).
- Modificación de la asociación usuario↔médico: se gestiona por `/users` (FEAT-004) y queda como está; esta feature la documenta y la cubre con una prueba de regresión.
- Catálogos de tanda de labor o especialidad (pregunta abierta §14.7 resuelta: texto libre en v1, D-4).
- Datos de contacto, horarios o consultorio del médico: no están en el contrato del §10 y no se añaden.
- Búsqueda trigram/`pg_trgm`: el índice existe desde FEAT-003, pero la búsqueda usa `ILIKE` escapado, igual que FEAT-011.
- Frontend Angular.

## Reglas de negocio

### Autorización y alcance

1. Lectura (`GET /doctors`, `GET /doctors/{id}`): `Authorization: Bearer <token>` y rol `ADMIN` **o** `MEDICO`, leído de la base (`user.role.name`) mediante `require_role("ADMIN", "MEDICO")`. El claim `role` del token no se usa (FEAT-002 regla 12).
2. Escritura (`POST`, `PUT`, `DELETE`): rol `ADMIN` exclusivamente, mediante `require_role("ADMIN")`. Un `MEDICO` autenticado recibe `403` `forbidden` en las tres, sin efectos en la base (CA-CRUD 8).
3. Sin cabecera, con esquema distinto de `Bearer`, con token inválido/expirado o de un usuario inexistente ⇒ `401` (`not_authenticated` o `invalid_token`).
4. Un `ADMIN` o `MEDICO` desactivado pierde el acceso en la siguiente request (`403` `inactive_user`), incluso si el token sigue vigente (FEAT-002 regla 13).
5. Todos los errores usan el envelope global `{"detail": str, "code": str, "errors": [{"field","message"}]}`; `errors` es `[]` cuando no aplica. Códigos nuevos: `doctor_not_found` (404), `doctor_exists` (409, cédula duplicada), `doctor_has_visits` (409, desactivación con visitas). Reutilizados: `not_authenticated` / `invalid_token` (401), `inactive_user` / `forbidden` (403) y `validation_error` (422).

### Perfil del médico (US-28, §4 "GET propio")

6. Un `MEDICO` autenticado consulta **solo su propio perfil**:
   - `GET /doctors` con token `MEDICO`: el filtro `doctor_id == user.doctor_id` se aplica **siempre y además** de los filtros pedidos. Sin `doctor_id` asociado (US-27: la asociación es opcional por registro), la respuesta es una página vacía: `items: []`, `total: 0`, `pages: 0`.
   - `GET /doctors/{doctor_id}` con token `MEDICO`: `200` solo si `doctor_id == user.doctor_id`; cualquier otro identificador, exista o no, ⇒ `404` `doctor_not_found` (no se revela la existencia de otros médicos, D-6).
7. Un `ADMIN` no tiene restricción de propiedad: lista todos y consulta cualquier identificador.

### Forma de un médico (D-2)

8. Cada médico devuelto tiene **exactamente** `id`, `name`, `cedula`, `lab_shift`, `specialty`, `is_active`. Nunca se devuelven `created_at`/`updated_at` ni la asociación con `users` (CA-CRUD 4 exige conservar `created_at`, no exponerlo; el §10 no incluye usuario en la respuesta).

### Validación de campos (D-4, D-9, RF-18)

9. `name`: se recorta (`strip`); vacío tras el recorte ⇒ `422` `validation_error` con `field == "name"` y mensaje `"El nombre es obligatorio"`. Longitud máxima 150 caracteres. Capitalización conservada.
10. `cedula`: se recorta (`strip`); debe cumplir **exactamente 11 dígitos ASCII** (`^[0-9]{11}$`, se admiten ceros iniciales). Cualquier otro valor (9 o 10 dígitos, 12 dígitos, letras, guiones, espacios internos, símbolos, vacío) ⇒ `422` `validation_error` con `field == "cedula"` y el mensaje literal del §2 del documento de API: `"La cédula debe tener 11 dígitos"`. No se valida dígito verificador.
11. `lab_shift`: se recorta (`strip`); vacío tras el recorte ⇒ `422` `validation_error` con `field == "lab_shift"` y mensaje `"La tanda de trabajo es obligatoria"`. Longitud máxima 50 caracteres (coincide con `VARCHAR(50)`).
12. `specialty`: se recorta (`strip`); vacío tras el recorte ⇒ `422` `validation_error` con `field == "specialty"` y mensaje `"La especialidad es obligatoria"`. Longitud máxima 100 caracteres (coincide con `VARCHAR(100)`).
13. `is_active` es booleano; en `POST` es opcional con valor por defecto `true` (D-5 de FEAT-011); en `PUT` es obligatorio.
14. Los mensajes de las reglas 9–12 van en español (CA-CRUD 9) y viajan en `errors[0]` con el nombre del campo en `field`, sin el prefijo `body`.

### Listado `GET /doctors`

15. Parámetros: `page` (entero ≥ 1, por defecto `1`), `size` (entero 1..100, por defecto `20`), `search` (texto, máximo 150 caracteres), `is_active` (booleano opcional) y `sort` (catálogo cerrado de D-3, por defecto `name`).
16. `page < 1`, `size < 1`, `size > 100`, `sort` fuera del catálogo, `sort` vacío o `search` de más de 150 caracteres ⇒ `422` `validation_error` con `field` igual al nombre del parámetro.
17. `search` se recorta (`strip`). Vacío o solo espacios ⇒ **sin filtro**. Con contenido, búsqueda **case-insensitive y parcial** en `doctors.name` **o** `doctors.cedula` (condición `OR`) con `ILIKE` y `%`, `_` y `\` escapados mediante `escape_like`. Buscar `%` encuentra solo los médicos cuyo nombre o cédula contiene un `%`, nunca todos.
18. `is_active` ausente ⇒ **sin filtro** (CA-CRUD 1). `true` ⇒ solo activos; `false` ⇒ solo inactivos. Se combina en `AND` con `search` y, para `MEDICO`, con el filtro de propiedad de la regla 6.
19. Orden: campo del catálogo de D-3, ascendente por defecto y descendente con prefijo `-`. Siempre se añade `doctors.id ASC` como desempate estable, salvo cuando el campo ordenado ya es `id`. `created_at` usa `NULLS LAST` en ambos sentidos.
20. `total` es el número de filas que cumplen los filtros (sin `LIMIT`). `pages = ceil(total / size)` y exactamente `0` cuando `total == 0`. `page` se devuelve tal como se pidió aunque supere `pages` (en ese caso `items` es `[]`).

### Creación `POST /doctors`

21. Body: `name`, `cedula`, `lab_shift`, `specialty` obligatorios; `is_active` opcional. Ejemplo literal del §10: `{"name": "Dra. Ana Pérez", "cedula": "00198765432", "lab_shift": "Matutina", "specialty": "Medicina General", "is_active": true}`.
22. Se aplican las validaciones 9–13.
23. **Cédula única** (`uq_doctors_cedula`): una cédula ya registrada, comparada por igualdad exacta (11 dígitos ⇒ sin ambigüedad de capitalización), incluso si el médico existente está inactivo ⇒ `409` `doctor_exists` con mensaje `"El médico ya existe"`. No se modifica la fila existente (no es *upsert*).
24. **Un médico puede existir sin usuario** (§10): la creación no exige ni impide asociación con `users`; la asociación se crea después por `/users` (FEAT-004).
25. `201` con la forma de D-2. Crear un médico inactivo está permitido. Si el `commit` viola `uq_doctors_cedula` (carrera), se traduce al mismo `409` y la sesión queda usable.

### Detalle `GET /doctors/{doctor_id}`

26. `doctor_id` debe ser un entero positivo; un valor no entero ⇒ `422` (validación de path). Un entero sin fila ⇒ `404` `doctor_not_found` con mensaje `"El médico no existe"`.
27. Devuelve `200` con la forma de D-2, incluidos los médicos inactivos (`is_active: false`). Para `MEDICO` aplica la regla 6.

### Actualización `PUT /doctors/{doctor_id}`

28. **Reemplazo completo** (D-1): el body exige `name`, `cedula`, `lab_shift`, `specialty` e `is_active` (presente, puede ser `false`). Un body parcial ⇒ `422` con `field` del campo ausente.
29. Permite reactivar (`is_active: true`) y desactivar (`is_active: false`); aplica las mismas validaciones 9–13 y la regla de unicidad 23 **excluyendo al propio médico**: reenviar su misma cédula ⇒ `200`; la cédula de otro médico ⇒ `409` `doctor_exists`.
30. Precedencia de errores: (a) `401`/`403`; (b) `422` de Pydantic (path, query y body); (c) `404` del médico de la ruta; (d) `409` de cédula; (e) escritura.
31. Solo `ADMIN`. La actualización conserva `created_at` y refresca `updated_at` explícitamente con `clock_timestamp()`. Responde `200` con la forma de D-2. No modifica la asociación con `users` (fuera de alcance).

### Desactivación `DELETE /doctors/{doctor_id}`

32. Es una **desactivación lógica**: `is_active = false`, sin `DELETE` SQL (CA-CRUD 5, RF-17). La fila y sus referencias (`users.doctor_id`, `visits.doctor_id`) se conservan.
33. **No se elimina si tiene visitas** (§10): un médico con **al menos una visita** (cualquier estado, `REGISTRADA` o `ANULADA`) ⇒ `409` `doctor_has_visits` con mensaje `"El médico tiene visitas registradas"` y permanece activo, sin log de desactivación.
34. Es **idempotente**: desactivar un médico ya inactivo ⇒ `204` sin modificar la fila ni su `updated_at`, y sin emitir un segundo log de desactivación. La comprobación de visitas de la regla 33 se aplica **antes** de la idempotencia (un médico inactivo con visitas sigue respondiendo `409` si se reintenta; no hay segundo log).
35. Un `doctor_id` sin fila ⇒ `404` `doctor_not_found`. Un `doctor_id` no entero ⇒ `422`.
36. Solo `ADMIN`. Respuesta `204` **sin cuerpo** (sin `Content-Length`), como en FEAT-004 regla 31. Un médico asociado a un `users.doctor_id` (sin visitas) se desactiva normalmente; la asociación se conserva.

### Registro (logs)

37. Eventos `doctor.created`, `doctor.updated` y `doctor.deactivated` con `doctor_id` y `actor_id`, y **nunca** `name`, `cedula`, `lab_shift`, `specialty` ni ningún dato personal del médico. Un `PUT` que cambia `is_active` de `true` a `false` emite `doctor.deactivated`; cualquier otro `PUT` emite `doctor.updated`.

## Supuestos

- SUP-1: la base ya tiene aplicada la migración de FEAT-003 que crea `doctors` con sus constraints; no hay migraciones nuevas.
- SUP-2: `pg_trgm` y el índice trigram ya existen; esta feature no los usa.
- SUP-3: no hay datos de médicos en producción que violen los nuevos validadores; los `CHECK` de la base ya imponían las mismas reglas.
- SUP-4: la asociación usuario↔médico (uno a uno, solo `MEDICO`) ya se valida en `/users` desde FEAT-004; esta feature no la cambia.

## Decisiones técnicas y desviaciones documentadas

- D-1: `PUT` es **reemplazo completo** de los cinco campos (mismo criterio que FEAT-007…FEAT-011), no `PATCH` parcial.
- D-2: la forma de lectura es plana y exacta (`id`, `name`, `cedula`, `lab_shift`, `specialty`, `is_active`), sin `created_at`/`updated_at`, sin anidación ni asociación con `users` (el §10 no la incluye).
- D-3: catálogo cerrado de `sort`: `name`, `cedula`, `lab_shift`, `specialty`, `created_at`, `is_active`, `id`. `name` es el orden por defecto. `SORT_PATTERN = r"^-?(?:name|cedula|lab_shift|specialty|created_at|is_active|id)$"`.
- D-4: `lab_shift` y `specialty` son **texto libre** (pregunta abierta §14.7 resuelta: no son catálogos en v1), requeridos y con longitudes 1..50 y 1..100 respectivamente. El backend no normaliza mayúsculas/minúsculas de estos textos.
- D-5: `is_active` por defecto `true` en `POST`; obligatorio en `PUT`.
- D-6: el perfil del médico es de **solo lectura** (US-28) y se resuelve sobre los dos `GET` existentes: la lista se filtra a su propio registro y el detalle de un `doctor_id` ajeno responde `404` (oculta la existencia, sin fugas de información por diferencia 403/404).
- D-7: "no se elimina si tiene visitas" se implementa como conteo de filas de `visits` por `doctor_id` (cualquier estado) y `409` `doctor_has_visits`. El conteo usa solo la PK/FK ya indexadas (`ix_visits_doctor_visited`).
- D-8: la comprobación de visitas precede a la idempotencia de `DELETE` (regla 34).
- D-9: la cédula se compara por igualdad exacta (numérica de longitud fija); no se normaliza ni se compara case-insensitive. El `CHECK` `ck_doctors_cedula_format` se traduce a `422` con el mensaje literal de cédula por si llegara por otra vía.
- D-10: se reutiliza el helper `escape_like` de `app/repositories/drug_type_repository.py` (igual que FEAT-011 D-7).
- D-11: la unicidad se comprueba antes de escribir (consulta) **y** en el `commit` traduciendo `IntegrityError` por `constraint_name` (carrera). Tras el fallo se hace `rollback`.
- D-12: `updated_at` se refresca con `func.clock_timestamp()` (misma desviación documentada en FEAT-004 D-14 y FEAT-007…FEAT-011).
- D-13: el tag OpenAPI es `doctors` y el prefijo del router `/doctors` (plural, `snake_case` de tabla), igual que el §5 del documento de API.

## Contrato de repositorio (nombres que usa el servicio)

```python
# app/repositories/doctor_repository.py
SORT_COLUMNS: dict[str, InstrumentedAttribute[Any]]

async def get_doctor_by_id(session: AsyncSession, doctor_id: int) -> Doctor | None  # ya existente (FEAT-004)
async def find_doctor_by_cedula(
    session: AsyncSession, cedula: str, *, exclude_id: int | None = None
) -> Doctor | None
async def count_visits_by_doctor(session: AsyncSession, doctor_id: int) -> int
async def list_doctors(
    session: AsyncSession,
    *,
    page: int,
    size: int,
    search: str | None,
    is_active: bool | None,
    sort_field: str,
    sort_desc: bool,
    doctor_id: int | None = None,
) -> tuple[list[Doctor], int]
```

`list_doctors(..., doctor_id=X)` aplica el filtro de propiedad del perfil médico (regla 6); `None` lo omite. `count_visits_by_doctor` cuenta filas de `visits` por `doctor_id`, sin cargar entidades.

## Contrato de API

### Autorización común

| Situación | Respuesta |
|---|---|
| Sin token / token inválido / expirado | `401` `not_authenticated` o `invalid_token` |
| Usuario inactivo | `403` `inactive_user` |
| `MEDICO` en `POST`/`PUT`/`DELETE` | `403` `forbidden` |

### `GET /api/v1/doctors`

Query: `page` (≥1, defecto 1), `size` (1..100, defecto 20), `search` (≤150), `is_active` (bool opcional), `sort` (defecto `name`).

`200`:

```json
{
  "items": [
    {
      "id": 5,
      "name": "Dra. Ana Pérez",
      "cedula": "00198765432",
      "lab_shift": "Matutina",
      "specialty": "Medicina General",
      "is_active": true
    }
  ],
  "total": 1,
  "page": 1,
  "size": 20,
  "pages": 1
}
```

Para `MEDICO`, `items` contiene a lo sumo su propio registro. Errores: `401`, `403`, `422`.

### `POST /api/v1/doctors`

```json
{
  "name": "Dra. Ana Pérez",
  "cedula": "00198765432",
  "lab_shift": "Matutina",
  "specialty": "Medicina General",
  "is_active": true
}
```

`201` con la forma de D-2. Errores: `401`, `403`, `409` `doctor_exists`, `422` `validation_error`.

### `GET /api/v1/doctors/{doctor_id}`

`200` con la forma de D-2 (incluye inactivos). Para `MEDICO`, solo su propio `doctor_id` (ajeno ⇒ `404`). Errores: `401`, `403`, `404` `doctor_not_found`, `422`.

### `PUT /api/v1/doctors/{doctor_id}`

Body con los cinco campos (D-1): `name`, `cedula`, `lab_shift`, `specialty`, `is_active`.

`200` con la forma de D-2. Errores: `401`, `403`, `404`, `409` `doctor_exists`, `422`.

### `DELETE /api/v1/doctors/{doctor_id}`

`204` sin cuerpo, idempotente. Con visitas ⇒ `409` `doctor_has_visits`. Errores: `401`, `403`, `404`, `409`, `422`.

### OpenAPI

- `tags=["doctors"]`, `summary` y `description` en cada endpoint, en español.
- `response_model` en `GET`/`POST`/`PUT`; `204` con `response_class=Response` en `DELETE`.
- Códigos documentados en `responses` para cada endpoint.
- `SORT_PATTERN` reutilizado en el `Query` del `sort`.

## Criterios de aceptación

### Autorización

- CA-1: `GET /doctors` y `GET /doctors/{id}` responden `200` con token `ADMIN` y con token `MEDICO` (propio).
- CA-2: `POST`, `PUT` y `DELETE` responden `403` `forbidden` con token `MEDICO`, y la base no cambia (ni filas ni `updated_at`).
- CA-3: los cinco endpoints responden `401` sin cabecera `Authorization` y con un token inválido.
- CA-4: un usuario desactivado con token vigente recibe `403` `inactive_user` en los cinco endpoints.

### Perfil del médico (US-28)

- CA-5: `MEDICO` en `GET /doctors` ve solo su propio registro (`items` de tamaño 1, `total == 1`); `ADMIN` ve todos.
- CA-6: `MEDICO` sin `doctor_id` asociado recibe `items: []`, `total: 0`, `pages: 0`.
- CA-7: `MEDICO` en `GET /doctors/{id}` propio ⇒ `200`; con un id ajeno (exista o no) ⇒ `404` `doctor_not_found`.
- CA-8: `search`, `is_active` y `sort` se combinan en `AND` con el filtro de propiedad del médico.

### Listado

- CA-9: respuesta con `items`, `total`, `page`, `size`, `pages`; `pages == 0` cuando `total == 0` y `pages == ceil(total/size)` si no.
- CA-10: `size > 100` y `page < 1` ⇒ `422`; `page` fuera de rango ⇒ `200` con `items == []`.
- CA-11: `sort` por `name`, `-name`, `cedula`, `lab_shift`, `specialty`, `-created_at`, `is_active`, `id`; `sort` inválido ⇒ `422`.
- CA-12: `search` encuentra por nombre parcial (case-insensitive) y por cédula; `search="%"` no devuelve todos los médicos.
- CA-13: `is_active` ausente devuelve activos e inactivos; `true` solo activos; `false` solo inactivos; combinado con `search` aplica `AND`.
- CA-14: cada `item` tiene exactamente las seis claves de D-2.

### Creación

- CA-15: `POST` válido ⇒ `201` con la forma de D-2 y `is_active: true` por defecto.
- CA-16: `POST` con `is_active: false` ⇒ `201` con `is_active: false`.
- CA-17: cédula duplicada (incluido un médico inactivo) ⇒ `409` `doctor_exists`.
- CA-18: `cedula` con 10 u 12 dígitos, con letras o con guiones ⇒ `422` con `field == "cedula"` y el mensaje `"La cédula debe tener 11 dígitos"`.
- CA-19: `name` vacío o de solo espacios ⇒ `422` con `field == "name"`; `name` de 150 caracteres ⇒ `201`; de 151 ⇒ `422`.
- CA-20: `lab_shift` vacío ⇒ `422` con `field == "lab_shift"`; de 50 ⇒ `201`; de 51 ⇒ `422`. `specialty` vacío ⇒ `422` con `field == "specialty"`; de 100 ⇒ `201`; de 101 ⇒ `422`.
- CA-21: crear un médico sin usuario asociado ⇒ `201` (§10: un médico puede existir sin usuario).

### Detalle

- CA-22: `GET` de un médico existente ⇒ `200` con la forma de D-2; de uno inactivo ⇒ `200` con `is_active: false`.
- CA-23: `GET` de un id inexistente ⇒ `404` `doctor_not_found`; id no entero ⇒ `422`.

### Actualización

- CA-24: `PUT` completo ⇒ `200`, `created_at` se conserva y `updated_at` cambia.
- CA-25: `PUT` con la propia cédula ⇒ `200` sin `409`; con la cédula de otro médico ⇒ `409` `doctor_exists`.
- CA-26: `PUT` con `is_active: false` desactiva y emite `doctor.deactivated`; `PUT` que reactiva ⇒ `200` con `is_active: true`.
- CA-27: `PUT` de un id inexistente ⇒ `404`; body parcial ⇒ `422` con el campo ausente.
- CA-28: `PUT` con `lab_shift` o `specialty` vacíos ⇒ `422` con el campo correspondiente.

### Desactivación

- CA-29: `DELETE` ⇒ `204` sin cuerpo y el médico queda `is_active: false` en el `GET`.
- CA-30: segundo `DELETE` ⇒ `204` y `updated_at` no cambia.
- CA-31: `DELETE` de un id inexistente ⇒ `404`.
- CA-32: `DELETE` de un médico con al menos una visita (cualquier estado) ⇒ `409` `doctor_has_visits` y el médico sigue activo.
- CA-33: `DELETE` de un médico sin visitas pero con usuario asociado ⇒ `204`; la asociación `users.doctor_id` se conserva.

### Integridad y errores

- CA-34: el envelope de error siempre tiene `detail`, `code` y `errors`, y nunca la clave `details`.
- CA-35: una violación de unicidad provocada en el `commit` se traduce al `409` correspondiente y la sesión sigue usable (siguiente request `200`).
- CA-36: regresión FEAT-004: `PUT /users/{id}` con `doctor_id` ya asignado a otro usuario ⇒ `409` `doctor_already_assigned`; con rol `ADMIN` y `doctor_id` ⇒ `400` `admin_cannot_have_doctor` (suite existente, sigue en verde).

### OpenAPI y calidad

- CA-37: `/openapi.json` incluye las cinco rutas con `summary`, `description`, tag `doctors` y los códigos documentados.
- CA-38: `uv run ruff check .` y `uv run ruff format --check .` limpios; `uv run alembic check` sin diferencias; suite completa en verde.

### Trazabilidad

| CA | Fuente |
|---|---|
| CA-1…CA-4 | CA-CRUD 8, §4 matriz `/doctors`, FEAT-002 reglas 11–13 |
| CA-5…CA-8 | US-28, §4 "GET propio", §3 matriz (Médicos: consulta de su perfil) |
| CA-9…CA-14 | CA-CRUD 1–2, §6 |
| CA-15…CA-21 | CA-CRUD 3, 9; §10; US-26; RF-09, RF-18 |
| CA-22…CA-23 | CA-CRUD 6 |
| CA-24…CA-28 | CA-CRUD 4; §10 |
| CA-29…CA-33 | CA-CRUD 5, RF-17; §10 ("no se elimina si tiene visitas") |
| CA-34…CA-38 | §2, §14, §10; Definition of Done; US-27 |

## Plan de pruebas

### Aislamiento

Un test = una transacción con savepoint deshecho al final (mismo patrón que FEAT-007…FEAT-011 y `tests/db_support.py`). Los tests de integración usan `tests/doctors_support.py` con helpers de creación directa de médicos, usuarios y visitas, y limpieza por savepoint.

### Unitarias (sin base de datos)

- Validadores de `name`/`cedula`/`lab_shift`/`specialty` (CA-18…CA-20, CA-28).
- `parse_sort` y `calculate_pages` (CA-9, CA-11).
- `escape_like` a través del comportamiento de búsqueda (CA-12).

### Integración (PostgreSQL + httpx)

- CRUD completo con `ADMIN` (CA-15…CA-17, CA-21, CA-22, CA-24…CA-26, CA-29, CA-30).
- Autorización con `MEDICO` y sin token (CA-1…CA-4).
- Perfil del médico: lista filtrada, detalle propio/ajeno, sin asociación (CA-5…CA-8).
- Listado, paginación, búsqueda y filtros (CA-9…CA-14).
- Desactivación con visitas (CA-32, CA-33): se insertan visitas directas por ORM con `status REGISTRADA` y `ANULADA`.
- Traducción de `IntegrityError` y sesión usable (CA-35).
- Regresión de asociación usuario↔médico vía `/users` (CA-36).
- OpenAPI (CA-37).

### Logs, OpenAPI y calidad

`caplog`/`structlog` capturado: los tres eventos existen, llevan `doctor_id`/`actor_id` y no contienen `name`, `cedula`, `lab_shift` ni `specialty` (regla 37).

## Riesgos y dependencias

- Depende de FEAT-003 (`doctors`, `visits`, `users.doctor_id`) y FEAT-002/004 (auth, envelope, asociación usuario↔médico). Sin migraciones.
- FEAT-013 (visitas) depende de esta feature para `doctor_id` y para "el médico se obtiene del token".
- Riesgo bajo de datos: solo se añaden endpoints; no se modifican tablas ni datos existentes. La regla de visitas solo lee `visits`.

## Preguntas abiertas

Ninguna bloqueante. Resueltas por el documento de API o por decisión documentada: cédula sin dígito verificador (§14.5), tanda y especialidad como texto libre en v1 (D-4), perfil médico como los dos `GET` con filtro de propiedad (D-6).

## Historial

| Fecha | Cambio |
|---|---|
| 2026-10-24 | Spec inicial para implementación (aprobación humana del bloque FEAT-011…014). |
