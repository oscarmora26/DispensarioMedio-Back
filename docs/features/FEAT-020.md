# FEAT-020 — Refresh tokens: renovación de sesión, rotación y revocación

**Estado:** `approved` (aprobada explícitamente por el humano el 2026-10-27 con las decisiones P-1…P-4)
**Fecha:** 2026-10-27
**Alcance:** emisión de refresh tokens opacos persistidos (hash SHA-256), `POST /auth/refresh` con rotación y detección de reuso, `POST /auth/logout` idempotente, y revocación en cascada al cambiar/restablecer contraseña o desactivar el usuario. **Supera la decisión "sin refresh tokens" de FEAT-002 (2026-10-21, ADR-003)**, D-3/R-1 de FEAT-002 y R-4 de FEAT-004.
**Fuente de verdad funcional:** esta spec; decisiones humanas del 2026-10-27 (sección Decisiones); análisis del código de FEAT-002/004/005/006.

## Decisiones aprobadas (humano, 2026-10-27)

| Decisión | Resultado |
|---|---|
| **P-1** | Se revierte la decisión "sin refresh tokens" de FEAT-002 (ADR-003); esta feature la supera. |
| **P-2** | Access token **15 minutos** (`JWT_EXPIRE_MINUTES`, nuevo default); refresh token **30 días** (`REFRESH_TOKEN_EXPIRE_DAYS`). |
| **P-3** | Refresh token viaja por **body JSON** (sin cookies; consistente con Bearer + `sessionStorage` y FEAT-006). |
| **P-4** | Revocación **logout + cascada**: change-password, reset-password (admin) y desactivación de usuario revocan todas las sesiones del usuario. |

## Reglas

### Configuración

1. `Settings.jwt_expire_minutes` cambia su default de `30` a `15` (sigue `Field(gt=0)`); `.env.example` pasa a `JWT_EXPIRE_MINUTES=15`.
2. Nueva setting `refresh_token_expire_days: int = Field(default=30, gt=0)`; `.env.example` documenta `REFRESH_TOKEN_EXPIRE_DAYS=30`.
3. Sin settings nuevas de secretos: el refresh token es opaco y no se firma; no necesita `JWT_SECRET`.

### Modelo y migración

4. Nueva tabla `refresh_tokens` (migración aditiva encadenada a `4ad3df9e109d`; no toca datos ni tablas existentes):

| Columna | Tipo | Regla |
|---|---|---|
| `id` | `BigInteger` + `Identity` | PK |
| `user_id` | `BigInteger FK users.id ON DELETE CASCADE` | NOT NULL |
| `token_hash` | `String(64)` | NOT NULL, `uq_refresh_tokens_token_hash` |
| `family_id` | `UUID` | NOT NULL (la genera la app con `uuid4`) |
| `created_at` | `DateTime(timezone=True)` | `server_default=func.now()` (CreatedAtMixin) |
| `expires_at` | `DateTime(timezone=True)` | NOT NULL |
| `last_used_at` | `DateTime(timezone=True)` | NULL |
| `revoked_at` | `DateTime(timezone=True)` | NULL |
| `revoked_reason` | `String(40)` | NULL; valores: `logout`, `password_change`, `password_reset`, `user_deactivated`, `reuse_detected` |
| `replaced_by_id` | `BigInteger FK refresh_tokens.id ON DELETE RESTRICT` | NULL |
| `created_by_ip` | `String(45)` | NULL |
| `user_agent` | `String(255)` | NULL (truncado a 255) |

Índices: `ix_refresh_tokens_user_id (user_id)`, `ix_refresh_tokens_family_id (family_id)`.
5. El modelo expone la relación `user` con `lazy="raise"` (patrón del repo); `User` no carga la colección de tokens.

### Primitivas de seguridad

6. `generate_refresh_token()` devuelve `secrets.token_urlsafe(64)` (~86 caracteres, ≥512 bits de entropía codificada). **Nunca un JWT.**
7. `hash_refresh_token(token)` devuelve el SHA-256 hex (64 chars) del token; **solo el hash se persiste**. El token plano jamás se guarda ni se loguea.

### Login (reglas 8–11)

8. `POST /auth/login` mantiene su contrato y agrega dos campos a la respuesta: `refresh_token` (string) y `refresh_expires_in` (segundos). Cambio aditivo: el front actual sigue funcionando.
9. El refresh token se crea con un `family_id` nuevo (`uuid4`), `expires_at = now + refresh_token_expire_days`, `created_by_ip` del request y `user_agent` truncado.
10. El login persiste el refresh token en el **mismo commit** que `last_login_at` (una sola transacción: o se emite todo o nada).
11. En cada login se ejecuta el borrado perezoso: `DELETE` de los refresh tokens **expirados** del usuario (cota de crecimiento; no toca sesiones vigentes).

### Refresh (reglas 12–21)

12. `POST /auth/refresh` recibe `{"refresh_token": str}` (1..512 chars; 422 si falta o excede). Endpoint **público** (el refresh token es la credencial); sin rate limiting propio (la entropía lo hace inviable de adivinar; el reuso ya está instrumentado).
13. La búsqueda es por `token_hash = sha256(token)` con `SELECT ... FOR UPDATE` (serializa rotaciones concurrentes del mismo token).
14. Toda falla responde el **mismo** `401 {"detail": "Token inválido", "code": "invalid_token"}`: desconocido, expirado, revocado o rotado. No hay oráculo de causa para el cliente; la causa va solo al log (`auth.refresh_failed`, `reason`).
15. Token **expirado** (`expires_at <= now`) o **revocado** (`revoked_at` no nulo) ⇒ 401, sin efectos colaterales.
16. Token **ya rotado** (`replaced_by_id` no nulo) = reuso:
    - **Dentro de la ventana de gracia** (`last_used_at` a ≤ 10 segundos): 401 con log `auth.refresh_failed` `reason="reused_within_grace"`, **sin revocar la familia** (carrera benigna del cliente: retry de red o doble click; debe reintentar con su token más nuevo).
    - **Fuera de la ventana**: se revoca **toda la familia** (`revoked_reason="reuse_detected"`), log `auth.refresh_reuse_detected` con `user_id` y `family_id` (nunca el token), y 401.
17. Si el `user` del token está inactivo: se revoca la familia (`reason="user_deactivated"` por si la cascada de la regla 25 no alcanzó), 401.
18. Rotación exitosa (todo en una transacción): se crea el token sucesor (mismo `family_id`, nuevo `expires_at` desde ahora), el actual queda con `replaced_by_id = sucesor.id` y `last_used_at = now`, se emite el access token con los claims **actuales** del usuario leídos de la base (`role`, `doctor_id`) y un solo commit.
19. Respuesta 200: `{access_token, token_type, expires_in, refresh_token, refresh_expires_in, user: LoginUser}` (el `user` permite al front refrescar rol/médico sin llamar a `/me`).
20. `expires_in` del access y `refresh_expires_in` se calculan desde las settings vigentes en el momento de la llamada (patrón de `create_access_token`).
21. El refresh rota `expires_at` (vigencia deslizante de 30 días desde el último uso); la familia muere por inactividad, no por antigüedad absoluta del primer token.

### Logout (reglas 22–24)

22. `POST /auth/logout` recibe `{"refresh_token": str}` y es **público** (el access token pudo expirar; la credencial revocada es el propio refresh token).
23. Si el hash existe y no está revocado: `revoked_at = now`, `revoked_reason = "logout"`. Responde **204 siempre** (exista, esté revocado, rotado o expirado): idempotente y sin oráculo.
24. Un token revocado por logout no puede refrescar (regla 15). Los demás tokens del usuario (otras sesiones/dispositivos) no se afectan.

### Revocación en cascada (reglas 25–27)

25. `change-password` (FEAT-002): tras validar y antes del commit, revoca **todas** las sesiones del usuario (`revoked_reason="password_change"`) en la misma transacción. Supera D-3: la contraseña anterior deja de autenticar **y** las sesiones mueren; el access token vigente expira solo (≤15 min).
26. `reset-password` de admin (FEAT-004): misma cascada con `revoked_reason="password_reset"`. Supera R-4.
27. `deactivate_user` (FEAT-004): misma cascada con `revoked_reason="user_deactivated"`. Además `get_current_user` ya corta el access token por `is_active` (doble cierre). La desactivación idempotente (usuario ya inactivo) no revoca de nuevo.

### Limpieza

28. Solo borrado perezoso en login (regla 11); sin jobs ni CLI en esta feature. Las filas revocadas se conservan para auditoría; las expiradas se borran por usuario en cada login suyo.

### Logging y privacidad (reglas 29–31)

29. Eventos nuevos: `auth.refresh_succeeded` (user_id, family_id), `auth.refresh_failed` (reason), `auth.refresh_reuse_detected` (user_id, family_id), `auth.logout` (user_id). Todos con `structlog` y el pipeline raíz (redacción heredada).
30. **Jamás** se loguea el refresh token plano ni su hash completo; ningún dato de paciente en estos eventos.
31. El log de login exitoso se mantiene igual; no se agrega el refresh token a ninguna respuesta de log.

### Compatibilidad y documentación (reglas 32–35)

32. Nada cambia para los endpoints de negocio ni para `get_current_user` (sigue leyendo rol e `is_active` de la base por request).
33. No hay blacklist de access tokens: con `exp` de 15 min y la verificación de `is_active` por request no aporta (documentado en el ADR).
34. Se crea `docs/adr/0002-refresh-tokens.md` (supera la decisión de 2026-10-21 referenciada en ADR-003 de `DispensarioMedicoDocs/08-adrs.md`).
35. Notas de vigencia: FEAT-002 (D-3, R-1 y regla 14 quedan superadas por FEAT-020) y FEAT-004 (R-4 superado), con el patrón usado en FEAT-017.

## Decisiones de diseño (spec-author)

| ID | Decisión | Motivo |
|---|---|---|
| **D-1** | Refresh token opaco (`secrets.token_urlsafe(64)`), no JWT. | Debe ser revocable ⇒ hay que consultar la base igual; el JWT no aporta statelessness y sí riesgo de reuso del secreto de firma. |
| **D-2** | Solo se persiste el hash SHA-256. | Un volcado de la tabla no expone credenciales utilizables. SHA-256 basta: la entrada es aleatoria de alta entropía (no hay diccionario). |
| **D-3** | Rotación con `family_id` y `replaced_by_id` (self-FK). | Permite detectar reuso y revocar la cadena completa sin tocar otras sesiones del usuario. |
| **D-4** | Ventana de gracia de 10 s en la detección de reuso. | Los retries de red / doble click del front generan refrescos concurrentes legítimos; revocar la familia ahí cerraría sesiones sanas. Fuera de la ventana se trata como robo (patrón Auth0/OAuth BCP). |
| **D-5** | `SELECT ... FOR UPDATE` en la rotación. | Serializa dos refrescos simultáneos del mismo token: el segundo observa el estado ya rotado y cae en la regla 16, no rota dos veces. |
| **D-6** | Errores genéricos 401 en refresh; causa solo en logs. | Sin oráculo de existencia/estado del token (misma disciplina que el login, D-2 de FEAT-002). |
| **D-7** | `expires_at` deslizante (se renueva en cada rotación). | Una sesión activa no expira; la inactividad de 30 días cierra la sesión. |
| **D-8** | La cascada revoca dentro de la misma transacción del cambio que la motiva. | Atomicidad: no puede quedar contraseña cambiada con sesiones vivas ni sesiones revocadas con contraseña vieja. |
| **D-9** | Logout solo revoca el token recibido, no la familia ni el usuario. | "Cerrar esta sesión"; las demás sesiones se cierran con la cascada de las reglas 25–27 o con un logout por sesión. |
| **D-10** | Sin rate limiting propio en `/auth/refresh`. | La credencial es de alta entropía; el reuso está instrumentado; un límite por IP castigaría redes compartidas sin ganancia. |
| **D-11** | Sin `ON DELETE CASCADE` en `replaced_by_id` (RESTRICT) y sí en `user_id`. | Borrar un usuario borra su historial de sesiones; la cadena de rotación no se borra por accidente al limpiar filas expiradas sueltas. |

## Criterios de aceptación

### Login y emisión

- **CA-1** `POST /auth/login` válido responde 200 con `access_token`, `token_type="bearer"`, `expires_in=900` (default nuevo), `refresh_token` no vacío y `refresh_expires_in=2592000` (30 días), además del `user` de siempre.
- **CA-2** El `refresh_token` del login no aparece en la base: solo existe su SHA-256 hex de 64 chars en `refresh_tokens.token_hash`, con `family_id` no nulo, `expires_at ≈ now+30d` y `created_by_ip` del cliente.
- **CA-3** Un login crea exactamente una fila nueva con `family_id` distinto al de logins previos del mismo usuario.
- **CA-4** Tras dos logins del mismo usuario y expirar manualmente las filas de uno, un tercer login borra solo las filas expiradas de ese usuario.

### Refresh feliz

- **CA-5** `POST /auth/refresh` con el token del login responde 200 con access token nuevo (`exp - iat = 900`), refresh token nuevo **distinto** del anterior, `refresh_expires_in` y `user` con el rol actual.
- **CA-6** El token usado queda con `replaced_by_id` apuntando al sucesor y `last_used_at` no nulo; el sucesor conserva el `family_id`.
- **CA-7** El access token del refresh funciona en `GET /auth/me` (200).
- **CA-8** Refrescar N veces en cadena funciona; la cadena comparte un solo `family_id` y cada eslabón apunta al siguiente.
- **CA-9** Un cambio de rol o `doctor_id` del usuario en la base se refleja en el access token emitido por el refresh siguiente (claims leídos de la base, no del token viejo).

### Refresh: errores y seguridad

- **CA-10** Token desconocido ⇒ 401 `invalid_token` con el mismo cuerpo de error para expirado, revocado y rotado (se verifica la igualdad literal de los cuatro cuerpos).
- **CA-11** Token expirado (`expires_at` en el pasado) ⇒ 401 y la fila queda sin cambios (sin `last_used_at`, sin `replaced_by_id`).
- **CA-12** Reuso del token viejo **inmediato** (dentro de 10 s de rotado) ⇒ 401 y la familia sigue viva: el token sucesor puede refrescar después.
- **CA-13** Reuso del token viejo **fuera de ventana** (`last_used_at` forzado a 11 s atrás) ⇒ 401, toda la familia queda `revoked_at`/`reuse_detected` y el sucesor legítimo también recibe 401 después.
- **CA-14** Dos refrescos concurrentes del mismo token (segundo request encolado tras el `FOR UPDATE`): uno rota y el otro recibe 401; solo existe un sucesor.
- **CA-15** Refresh de un token cuyo usuario fue desactivado ⇒ 401 y la familia queda revocada.
- **CA-16** `refresh_token` ausente, vacío o de más de 512 chars ⇒ 422 con el envelope estándar `{detail, code, errors}`.
- **CA-17** `/auth/refresh` no exige `Authorization` (un request sin cabecera Bearer funciona igual).

### Logout

- **CA-18** `POST /auth/logout` con el token vigente ⇒ 204 y el token queda `revoked_at`/`logout`; usarlo en `/auth/refresh` después ⇒ 401.
- **CA-19** Logout dos veces con el mismo token ⇒ 204 ambas; logout con token inventado ⇒ 204.
- **CA-20** El logout no afecta otras sesiones del usuario: un segundo login previo sigue pudiendo refrescar.
- **CA-21** `/auth/logout` no exige `Authorization`.

### Cascada

- **CA-22** `change-password` correcto ⇒ todas las sesiones del usuario revocadas con `password_change`; refrescar cualquiera de ellas ⇒ 401.
- **CA-23** `reset-password` de admin ⇒ idéntico efecto con `password_reset`.
- **CA-24** `DELETE /users/{id}` (desactivación) ⇒ sesiones del usuario revocadas con `user_deactivated`; además su access token vivo recibe 403 en la siguiente request (regla 13 de FEAT-002, intacta).
- **CA-25** Desactivar un usuario ya inactivo (idempotente) no revoca nada nuevo ni falla.

### Regresión y contrato

- **CA-26** El login con contraseña mala / usuario inexistente / rate limit responde exactamente como antes (no se emite refresh token en fallos).
- **CA-27** `OpenAPI` registra `POST /auth/refresh` y `POST /auth/logout` bajo el tag `auth` con sus códigos (200/401/422 y 204/422).
- **CA-28** `alembic check` sin diferencias tras la migración nueva; `downgrade` deja la base sin `refresh_tokens`.
- **CA-29** `.env.example` documenta `JWT_EXPIRE_MINUTES=15` y `REFRESH_TOKEN_EXPIRE_DAYS=30`; el test de paridad con `.env.example` sigue en verde.
- **CA-30** Los tests existentes que fijaban el default de 30 min quedan actualizados a 15; toda la suite en verde.

## Riesgos

| ID | Riesgo | Mitigación |
|---|---|---|
| R-1 | Falso positivo de robo por refrescos concurrentes legítimos. | Ventana de gracia de 10 s (D-4) + `FOR UPDATE` (D-5); CA-12/CA-14 lo fijan. |
| R-2 | Crecimiento de `refresh_tokens`. | Borrado perezoso por login (regla 11) + `ON DELETE CASCADE` por usuario. |
| R-3 | Regresión por el cambio de default 30 → 15 min. | CA-29/CA-30; es cambio de contrato visible aprobado (P-2). |
| R-4 | Fuga del token por logs. | Reglas 29–31; el pipeline de redacción ya existe y los eventos no incluyen el token. |

## Fuera de alcance

- Cookies `HttpOnly` (P-3 eligió body JSON), gestión de "sesiones activas" en API, límite de sesiones por usuario, blacklist de access tokens, job de limpieza (queda el borrado perezoso), cambios en el frontend.

## Historial

| Fecha | Cambio |
|---|---|
| 2026-10-27 | Propuesta registrada como `proposed` a pedido del humano ("Realiza la propuesta para la implementacion"). |
| 2026-10-27 | **Aprobada explícitamente por el humano** con P-1…P-4 (reversión de la decisión de FEAT-002; 15 min/30 días; body JSON; logout + cascada). Spec ejecutiva: reglas 1–35, D-1…D-11, CA-1…CA-30. |
