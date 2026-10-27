# ADR 0002 — Refresh tokens: renovación, rotación y revocación de sesión

**Estado:** aceptado
**Fecha:** 2026-10-27
**Supera:** la decisión humana del 2026-10-21 registrada en FEAT-002 ("sin refresh tokens, JWT de 30 min", D-3 y R-1) y en `DispensarioMedicoDocs/08-adrs.md` ADR-003.

## Contexto

FEAT-002 implementó autenticación con un JWT HS256 de 30 minutos y **sin revocación**: un token robado era válido hasta su `exp`, cambiar la contraseña no cerraba sesiones (D-3) y el `reset-password` de un administrador tampoco (R-4 de FEAT-004). Esa limitación se aceptó explícitamente en su momento por simplicidad.

Con el sistema en uso aparecieron dos costos concretos:

1. **Sesiones cortas y sin renovación**: el personal reloguea varias veces por jornada.
2. **Sin capacidad de cerrar una sesión comprometida**: no existe forma de invalidar una credencial antes de que expire sola.

Además, el camino HTTP ya no era stateless: `get_current_user` consulta la base en cada request para leer el rol y `is_active`. La principal ventaja teórica de "no persistir nada" ya no aplicaba.

## Decisión

Se incorporan **refresh tokens** con estas características:

| Aspecto | Decisión | Motivo |
|---|---|---|
| Formato | String opaco `secrets.token_urlsafe(64)` | Debe ser revocable: consultar la base es inevitable, así que firmar un JWT no aporta statelessness y sí suma superficie (secreto de firma reutilizado). |
| Persistencia | Solo el hash SHA-256 del token | Un volcado de la base no expone credenciales utilizables. La entrada es aleatoria de 512 bits: no hay diccionario ni fuerza bruta viable, y un hash rápido basta. |
| Vigencia | Access **15 min** / refresh **30 días** deslizantes | Sesión mensual para el usuario; ventana de exposición del access token reducida a la mitad. |
| Transporte | Body JSON, sin cookies | Consistente con `Authorization: Bearer` + `sessionStorage` y con `allow_credentials=False` de FEAT-006; sin cambios de CORS ni del frontend. |
| Rotación | Obligatoria en cada uso, con `family_id` y `replaced_by_id` | Permite detectar reuso y revocar la cadena completa sin afectar otras sesiones del usuario. |
| Reuso | Ventana de gracia de 10 s; fuera de ella se revoca la familia | Los retries de red y el doble click generan refrescos concurrentes legítimos; revocar ahí cerraría sesiones sanas. |
| Errores | 401 genérico único; la causa solo va al log | Sin oráculo de existencia ni de estado del token. |
| Revocación | Logout del token recibido + cascada total al cambiar/restablecer contraseña y al desactivar el usuario | Cierra D-3, R-1 y R-4: una contraseña comprometida o una cuenta desactivada ya no dejan sesiones vivas. |
| Access token | Sin blacklist | Con 15 min de `exp` y la verificación de `is_active` por request, una blacklist agregaría una consulta por request sin ganancia real. |

## Alternativas consideradas

- **Mantener solo JWT de 30 min (statu quo)**: sin costo de implementación, pero sigue sin revocación ni sesión larga. Se descarta por los dos costos operativos descriptos.
- **Refresh token como JWT firmado**: permitiría validarlo sin tocar la base, pero la revocación exige la consulta igual; se descarta.
- **Cookies `HttpOnly`**: mejor frente a XSS, pero exige `allow_credentials=True`, cambios de CORS (FEAT-006) y trabajo en el frontend Angular. Se descarta para v1; queda como evolución posible.
- **Blacklist de access tokens**: revocación inmediata, pero una consulta extra por request. Se descarta mientras el TTL sea corto y `is_active` se verifique por request.

## Consecuencias

- Nueva tabla `refresh_tokens` (migración `8c1f4d2a7b90`, aditiva) con `ON DELETE CASCADE` por usuario.
- `POST /auth/login` devuelve dos campos nuevos (`refresh_token`, `refresh_expires_in`); cambio aditivo.
- Nuevos endpoints públicos `POST /auth/refresh` y `POST /auth/logout` (el refresh token es la credencial).
- El default de `JWT_EXPIRE_MINUTES` baja de 30 a 15 y aparece `REFRESH_TOKEN_EXPIRE_DAYS` (30).
- `change-password`, `reset-password` y la desactivación de usuario revocan sesiones dentro de la misma transacción del cambio.
- Sin jobs de limpieza en v1: el login borra los tokens expirados de ese usuario.
- El frontend puede seguir usando solo el access token; adoptar el refresh es opcional y gradual.

## Referencias

- `docs/features/FEAT-020.md` — reglas, decisiones de diseño y criterios de aceptación.
- `docs/features/FEAT-002.md` — D-3 y R-1 quedan superados.
- `docs/features/FEAT-004.md` — R-4 queda superado.
