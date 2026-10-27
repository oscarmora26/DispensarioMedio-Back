# FEAT-018 — Exponer la asociación usuario↔médico en `/doctors`

**Estado:** in_progress
**Fecha:** 2026-10-27
**Pedido del humano (verbatim):** "El recurso medico no expone la asociacion con usuario. La relacion no deberia de estar en medico en vez de usuario?" → aprobado con "Exponer la asociación en /doctors".

## Contexto

La asociación existe desde FEAT-003/FEAT-004 y vive correctamente en `users.doctor_id` (`UNIQUE uq_users_doctor`, 1:0..1): la cuenta es la entidad derivada y el médico puede existir sin cuenta (FEAT-012 regla 24). El problema no era el modelo sino la **exposición**: `GET /users` (solo ADMIN) devuelve `doctor_id`, pero `GET /doctors` no decía nada de la cuenta, obligando a cruzar dos recursos a mano.

**Decisión de modelo:** no se mueve la FK a `doctors`. Razones: (a) el médico existe sin credencial, así que el sujeto no debe depender de la cuenta; (b) la coherencia con el rol vive en la misma fila (`role_id` + `doctor_id`), evitando validación cruzada y el estado inválido "médico apuntando a una cuenta que dejó de ser MEDICO"; (c) `token.doctor_id` es lookup directo por PK.

## Reglas

- **R-1** `DoctorRead` agrega `user` de solo lectura: `{id, username, is_active}` o `null` cuando el médico no tiene cuenta.
- **R-2** El usuario anidado **no** expone `email`, `password_hash` ni `role`: es un resumen de administración, no la vista del usuario.
- **R-3** Aplica al detalle y a cada ítem del listado.
- **R-4** La escritura de la asociación sigue viviendo **solo** en `/users` (una única fuente de verdad, sin doble camino).
- **R-5** No se relaja D-6: un `MEDICO` sigue viendo únicamente su propio perfil; la asociación no habilita leer médicos ajenos.
- **R-6** Sin migración: la columna `users.doctor_id` ya existe.

## Cambios

| Archivo | Cambio |
|---|---|
| `app/schemas/doctor.py` | `DoctorUserRead` (nuevo) y campo `user` en `DoctorRead` |
| `app/repositories/doctor_repository.py` | `selectinload(Doctor.user)` en detalle y listado |
| `app/services/doctor_service.py` | `_to_read` incluye `user`; las escrituras releen el médico para tener la relación cargada (`lazy="raise"`) |
| `tests/doctors_support.py` | `ITEM_KEYS` incluye `user`; se quita de `FORBIDDEN_KEYS` |
| `tests/test_doctors_user_association.py` | 6 casos nuevos (nuevo) |
| `tests/test_doctors_unit.py` | forma de lectura con `user` y resumen del usuario sin datos sensibles |

## Criterios de aceptación

- [x] **CA-1** Médico sin cuenta ⇒ `user: null`.
- [x] **CA-2** Médico con cuenta ⇒ `user` con exactamente `id`, `username`, `is_active`.
- [x] **CA-3** El resumen no expone `email`, `password_hash` ni `role`.
- [x] **CA-4** El listado incluye `user` en todos los ítems, incluidos los `null`.
- [x] **CA-5** Un `MEDICO` ve su propio resumen y sigue recibiendo `404` por otro médico.
- [x] **CA-6** Crear, actualizar y desactivar devuelven el `user` correcto (no fallan por `lazy="raise"`).

## Defecto evitado

`_to_read` construía `DoctorRead` campo por campo, así que agregar el campo al schema no bastaba: el valor salía siempre `null`. Y acceder a `doctor.user` en las escrituras lanzaba `InvalidRequestError: 'Doctor.user' is not available due to lazy='raise'`, porque el objeto recién creado no tiene la relación cargada. Se resolvió releyendo con `_get_doctor_or_404` (que usa `selectinload`), el mismo patrón de `shelf_service` y `cell_service`.
