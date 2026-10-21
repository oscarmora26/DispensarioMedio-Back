# FEAT-006 — CORS configurable para el frontend (localhost:4200)

**Estado:** `approved` (aprobada explícitamente por el humano el 2026-10-21)
**Fecha:** 2026-10-21
**Alcance:** habilitar CORS en la API mediante `CORSMiddleware` con orígenes, métodos y cabeceras configurables por entorno; sin cambios de endpoints, esquema ni dependencias.
**Fuente de verdad funcional:** decisiones humanas registradas en `feature-list.md` (2026-10-21), `AGENTS.md`, `docs/features/FEAT-001.md` (app factory, `X-Request-ID`, access log), `docs/features/FEAT-002.md` (Bearer, `Retry-After`) y `docs/adr/0001-stack.md`.

## Contexto

El frontend Angular corre en `http://localhost:4200` y hoy recibe errores de CORS al llamar a la API. La API usa FastAPI con prefijo `/api/v1`, autenticación JWT por cabecera `Authorization: Bearer` y **no usa cookies**. El navegador exige que el servidor responda al *preflight* `OPTIONS` y que cada respuesta incluya `Access-Control-Allow-Origin` para el origen exacto del front.

La configuración ya está centralizada en `app/core/config.py` (`pydantic-settings`, `.env`, `.env.example`). La feature añade settings tipados y registra `CORSMiddleware` en `create_app()`, respetando el orden actual de middlewares (`RequestID` envolvente, `AccessLog` y luego el resto).

Decisión humana ya tomada y **no reabrible**: orígenes por variable de entorno `CORS_ALLOWED_ORIGINS`, con default `http://localhost:4200` para desarrollo, y `allow_credentials=False` porque el front solo envía `Authorization: Bearer`.

## Alcance

- `app/core/config.py`:
  - Constantes de defaults y de cabeceras expuestas.
  - Settings tipados `cors_allowed_origins`, `cors_allowed_methods`, `cors_allowed_headers` (`list[str]`), con parseo desde `.env`/entorno en formato coma-separado **o** JSON.
  - Validación y normalización de orígenes, métodos y cabeceras; reglas de comodín y de lista vacía; validación de producción.
- `app/main.py`: registrar `CORSMiddleware` en `create_app()` **antes** de `AccessLogMiddleware` y `RequestIDMiddleware`, que siguen siendo los más externos.
- `.env.example`: documentar `CORS_ALLOWED_ORIGINS`, `CORS_ALLOWED_METHODS` y `CORS_ALLOWED_HEADERS`, incluido el efecto de una lista vacía y la prohibición de `*` en producción.
- Tests nuevos (los escribe el reviewer) para settings, orden de middlewares, preflight, respuestas reales, errores, headers expuestos y escenarios de producción.

## Fuera de alcance

- Cookies, sesiones, CSRF o `allow_credentials=True`. La autenticación sigue siendo `Authorization: Bearer` (FEAT-002).
- `allow_origin_regex`, comodines parciales (`https://*.example.com`), `allow_private_network` o CORS dinámico desde base de datos.
- CORS en proxies/ingress (nginx, Traefik, Docker Compose, manifiestos de despliegue) o configuración del frontend Angular.
- Cambios en endpoints, schemas, servicios, repositorios, OpenAPI, autenticación, rate limiting, `docker-compose.yml`, `Dockerfile`, Alembic o base de datos.
- Compatibilidad con una variable previa `FRONTEND_ORIGIN`: no se usa; `extra="ignore"` ya descarta variables desconocidas.
- Actualización del `README.md` (el alcance documental de esta feature es `.env.example`; ver SUP-4).
- Tests de producción reales contra un navegador o un dominio desplegado.

## Reglas de negocio

1. `CORS_ALLOWED_ORIGINS` define los orígenes permitidos. Default de la clase: `["http://localhost:4200"]`. Los nombres de campo son `cors_allowed_origins`, `cors_allowed_methods` y `cors_allowed_headers`; `pydantic-settings` los mapea a `CORS_ALLOWED_ORIGINS`, `CORS_ALLOWED_METHODS` y `CORS_ALLOWED_HEADERS` (case-insensitive).
2. Cada setting de lista acepta **dos formatos** en la variable de entorno: coma-separado (`a,b,c`) o un array JSON de cadenas (`["a","b"]`). También acepta una `list[str]`/`tuple[str, ...]` cuando se construye `Settings(...)` programáticamente. Se recorta cada elemento y se descartan los elementos vacíos resultantes.
3. Los orígenes se normalizan: se elimina una barra final (`/`), se pasa `scheme` y `netloc` a minúsculas y se conserva el orden de primera aparición al deduplicar. El valor resultante debe ser `*` o un origen serializado `http(s)://host[:puerto]`.
4. Un origen configurado es inválido si: no usa `http`/`https`, no tiene `netloc`, incluye ruta, query, fragmento, usuario/contraseña, espacios o caracteres de control, tiene un puerto no numérico, contiene `*` sin ser exactamente `*`, o es `null`. Un origen inválido **impide arrancar** (`ValidationError`), no se ignora en silencio.
5. `*` solo se admite como único elemento de `cors_allowed_origins`. Combinarlo con orígenes explícitos (`"*,http://localhost:4200"`) es error en cualquier entorno. En `environment=prod` **no se admite** `*`; el arranque falla con un mensaje que menciona `CORS_ALLOWED_ORIGINS` y `produccion`. En `local`, `dev`, `test` y `staging` sí se admite.
6. `CORS_ALLOWED_ORIGINS` vacío (`""`, `"   "`, `"[]"`, `'["", " "]'` o `" , "`) se interpreta como lista vacía **y deshabilita CORS por completo**: no se registra `CORSMiddleware`. No se interpreta como «permitir todo».
7. `CORS_ALLOWED_METHODS` default: `["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"]`. Cada valor se recorta, se pasa a mayúsculas y se deduplica conservando el orden. Valores admitidos: `GET`, `HEAD`, `POST`, `PUT`, `PATCH`, `DELETE`, `OPTIONS`. `*` no se admite en métodos.
8. `CORS_ALLOWED_HEADERS` default: `["Authorization", "Content-Type", "Accept", "X-Request-ID"]`. Cada valor se recorta; los nombres se validan con el token de cabecera HTTP (sin espacios ni separadores); se deduplican case-insensitive conservando la primera grafía. `Content-Type`, `Content-Language`, `Accept` y `Accept-Language` se canonicalizan a su grafía de la safelist de Starlette para no duplicarse en la respuesta. `*` no se admite en cabeceras.
9. Si `cors_allowed_origins` no está vacío, `cors_allowed_methods` y `cors_allowed_headers` deben ser no vacíos; si están vacíos, el arranque falla. Si `cors_allowed_origins` está vacío, esos dos settings pueden quedar vacíos y se ignoran.
10. `create_app()` registra `CORSMiddleware` **primero**, después `AccessLogMiddleware` y por último `RequestIDMiddleware`. En Starlette el último middleware agregado es el más externo; el orden final debe ser `RequestID → AccessLog → CORS → rutas`. Así el preflight también recibe `X-Request-ID`, se registra en `request.completed` y CORS sigue interceptando antes del router.
11. Un request es preflight si y solo si trae `Origin`, método `OPTIONS` y cabecera `Access-Control-Request-Method` (comportamiento de `CORSMiddleware` de Starlette 1.9.0). El preflight se resuelve **antes** de routing y de dependencias: no requiere `Authorization` ni toca la base de datos.
12. Preflight con origen permitido, método permitido y cabeceras permitidas ⇒ `200`, cuerpo `text/plain` `OK`, `Access-Control-Allow-Origin: <origen exacto>`, `Access-Control-Allow-Methods` con la lista configurada, `Access-Control-Allow-Headers`, `Access-Control-Max-Age: 600`, `Vary` y **sin** `Access-Control-Allow-Credentials`.
13. Preflight con origen no permitido ⇒ `400`, cuerpo `text/plain` `Disallowed CORS origin`, **sin** `Access-Control-Allow-Origin` (Starlette incluye igualmente `Access-Control-Allow-Methods` y `Access-Control-Max-Age`; no son un fallo de la feature).
14. Preflight con origen permitido pero método no permitido ⇒ `400`, cuerpo `text/plain` `Disallowed CORS method`, **con** `Access-Control-Allow-Origin` del origen permitido. Preflight con origen y método permitidos pero alguna cabecera no permitida ⇒ `400`, cuerpo `text/plain` `Disallowed CORS headers`, también con `Access-Control-Allow-Origin`.
15. Los fallos de preflight son respuestas planas de Starlette: **no** usan el envelope `{detail, code, errors}`. El envelope se mantiene intacto para los endpoints.
16. Request real (no preflight) desde un origen permitido ⇒ la respuesta normal (`200`, `401`, `403`, `404`, `422`, `429`, etc.) añade `Access-Control-Allow-Origin: <origen exacto>`, `Access-Control-Expose-Headers: X-Request-ID, Retry-After` y `Vary: Origin`; el status, el cuerpo y el resto de cabeceras no cambian.
17. Request real desde un origen no permitido ⇒ la respuesta normal se produce igual, pero **sin** `Access-Control-Allow-Origin`; el navegador bloquea su lectura. No se devuelve `403` ni se corta la petición en el servidor.
18. `Access-Control-Expose-Headers` es fijo (no configurable por entorno): `X-Request-ID, Retry-After`. `X-Request-ID` permite al front correlacionar errores con los logs (FEAT-001) y `Retry-After` permite respetar el `429` del login (FEAT-002 §15). No se expone ningún dato sensible.
19. `allow_credentials=False` se pasa explícito, nunca se habilita. No se emite `Access-Control-Allow-Credentials` en ningún caso. Si en el futuro se habilitaran credenciales, el validador deberá rechazar `*` para no violar la política del navegador; hoy no aplica porque las credenciales están fijas en `False`.
20. Con CORS habilitado y un request sin `Origin`, la respuesta no lleva cabeceras CORS de origen ni de exposición, pero sí `Vary: Origin` (comportamiento de Starlette). Con CORS deshabilitado no hay ninguna cabecera CORS ni `Vary: Origin`.
21. Los settings se leen al construir la app (`create_app()`); un cambio en caliente no altera una app ya construida y requiere reinicio. `get_settings()` sigue cacheado con `lru_cache`.
22. El orden de evaluación no cambia: CORS es transporte, no autorización. Un request permitido sin token sigue devolviendo `401`; CORS solo añade cabeceras para que el navegador pueda leer ese `401`.
23. No se registran orígenes, cabeceras de autorización ni cookies en logs. El access log sigue registrando método, ruta, status y duración; el preflight aparece como `OPTIONS` con su status.
24. Sin cambios de esquema, migraciones, `pyproject.toml` ni `uv.lock`. `CORSMiddleware` ya viene con Starlette, dependencia transitiva de FastAPI. `uv run alembic check` debe seguir limpio.

## Decisiones

| ID | Decisión | Justificación / impacto |
|---|---|---|
| **D-1** | Se usa `CORS_ALLOWED_ORIGINS` (no `FRONTEND_ORIGIN`) con default `["http://localhost:4200"]`. Los orígenes se leen de `Settings`, no de `os.environ` directamente. | Decisión humana. Mantiene la configuración centralizada y testeable con `_env_file=None`; `FRONTEND_ORIGIN` queda ignorado por `extra="ignore"`. |
| **D-2** | Los tres settings son `Annotated[list[str], NoDecode]` y un `field_validator(mode="before")` compartido parsea coma-separado o JSON. | `pydantic-settings` intenta JSON por defecto para tipos complejos; `NoDecode` (disponible en 2.16.0, ya instalado) permite aceptar ambos formatos sin una variable auxiliar ni un tipo `str` con propiedades. |
| **D-3** | Normalización de orígenes: `strip`, quitar `/` final, `scheme`/`netloc` a minúsculas, deduplicar preservando orden; validación estricta de esquema, ausencia de ruta/query/fragmento/userinfo y puerto numérico. | El navegador serializa el origen en minúsculas y sin `/` final; sin normalizar, una configuración correcta `http://LOCALHOST:4200/` fallaría en silencio. La validación estricta convierte un error de despliegue en un fallo de arranque visible. |
| **D-4** | `*` solo como único origen y solo fuera de `prod`; en `prod` es `ValidationError`. `*` en métodos o cabeceras se rechaza siempre. | Cumple «sin comodines silenciosos»: producción obliga a enumerar orígenes. Rechazar `*` en métodos/cabeceras evita habilitar por accidente cabeceras no previstas y mantiene el contrato explícito. |
| **D-5** | Lista de orígenes vacía ⇒ CORS **deshabilitado** (no se añade el middleware). Métodos/cabeceras vacíos solo se toleran si los orígenes también lo están. | Es la opción *fail-closed*: un `CORS_ALLOWED_ORIGINS=` accidental no abre la API a todos los orígenes. Además evita un `CORSMiddleware` inútil que respondería `400` a todos los preflight. |
| **D-6** | Métodos en mayúsculas, deduplicados y restringidos a `GET/HEAD/POST/PUT/PATCH/DELETE/OPTIONS`. Cabeceras validadas con el token HTTP, deduplicadas case-insensitive y con canonicalización de la safelist. | Evita typos silenciosos (`Post`, `authorization `) y respuestas con cabeceras duplicadas por diferencias de capitalización. Los defaults cubren el front (Bearer + JSON + correlación). |
| **D-7** | `CORSMiddleware` se agrega antes que `AccessLogMiddleware` y `RequestIDMiddleware` (queda dentro de ambos). | El preflight no se pierde en logs y recibe `X-Request-ID`, sin dejar de interceptarse antes del router. La alternativa (CORS el más externo) haría que los preflight no se registraran ni tuvieran request id, y complicaría el diagnóstico. |
| **D-8** | `expose_headers=("X-Request-ID", "Retry-After")` es una constante de `config.py`, no una variable de entorno. | Es un contrato con el cliente, no una decisión de despliegue. Ambos headers ya existen en la API (FEAT-001 y FEAT-002) y sin exponerlos JavaScript no puede leerlos. |
| **D-9** | `max_age=600` fijo; sin `allow_origin_regex`, sin `allow_private_network`, sin `allow_credentials`. | 600 s es el default de Starlette y reduce preflight; no se añaden knobs que no pide el humano. Los cambios de origen tardan como máximo ese tiempo en reflejarse en el navegador (R-3). |
| **D-10** | Los settings se leen en `create_app()`; no hay recarga en caliente ni endpoint de configuración. | Coherente con el resto de `Settings`. En tests, el `app` de módulo no se muta: se construye una app nueva tras `get_settings.cache_clear()` (ver plan de pruebas). |
| **D-11** | En `prod`, dejar el default `http://localhost:4200` **no** impide arrancar; solo se prohíbe `*`. | No hay exposición a dominios reales y se evita un fallo de arranque por una variable ausente. Se documenta en `.env.example` y se acepta como riesgo R-1; si el humano prefiere *fail-closed* también para el default, es un cambio localizado en el validador. |
| **D-12** | Los errores de preflight son `text/plain` de Starlette y no pasan por `AppError`. | Ocurren antes del router y de los handlers; envolverlos exigiría un middleware propio y no aporta valor al navegador. Se documenta para que el reviewer no espere el envelope. |

## Supuestos

| ID | Supuesto |
|---|---|
| **SUP-1** | El frontend Angular de desarrollo usa exactamente `http://localhost:4200` (sin barra final, sin ruta). Cualquier otro origen debe declararse en `CORS_ALLOWED_ORIGINS`. |
| **SUP-2** | El front envía el JWT en `Authorization: Bearer`, nunca en cookie; por eso `allow_credentials=False` no rompe la autenticación. |
| **SUP-3** | El front necesita leer, como máximo, `X-Request-ID` (correlación) y `Retry-After` (reintento tras `429`). No se exponen otros headers. |
| **SUP-4** | El `README.md` no forma parte del alcance documental de esta feature; la fuente de variables es `.env.example`. |
| **SUP-5** | `staging` se trata como entorno no productivo a efectos de `*`; solo `environment=prod` rechaza el comodín. Si `staging` es internet-facing, ver R-2. |

## Modelo de datos

**Sin cambios.** No hay tablas, columnas, índices, constraints ni migraciones nuevas.

- `uv run alembic check` debe imprimir `No new upgrade operations detected.`
- `git status --porcelain alembic/ pyproject.toml uv.lock` no debe imprimir nada.

## Contrato de API

Esta feature no crea endpoints. Modifica el contrato HTTP transversal de cabeceras para requests con `Origin`. Los cuerpos y status de los endpoints existentes no cambian.

### Configuración propuesta

```python
# app/core/config.py
CORS_DEFAULT_ALLOWED_ORIGINS = ("http://localhost:4200",)
CORS_DEFAULT_ALLOWED_METHODS = ("GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS")
CORS_DEFAULT_ALLOWED_HEADERS = ("Authorization", "Content-Type", "Accept", "X-Request-ID")
CORS_EXPOSE_HEADERS = ("X-Request-ID", "Retry-After")
CORS_PREFLIGHT_MAX_AGE_SECONDS = 600
CORS_ALLOWED_METHOD_VALUES = frozenset({"GET", "HEAD", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"})


class Settings(BaseSettings):
    # ...
    cors_allowed_origins: Annotated[list[str], NoDecode] = Field(
        default_factory=lambda: list(CORS_DEFAULT_ALLOWED_ORIGINS)
    )
    cors_allowed_methods: Annotated[list[str], NoDecode] = Field(
        default_factory=lambda: list(CORS_DEFAULT_ALLOWED_METHODS)
    )
    cors_allowed_headers: Annotated[list[str], NoDecode] = Field(
        default_factory=lambda: list(CORS_DEFAULT_ALLOWED_HEADERS)
    )
```

El parseo y la validación pueden implementarse con funciones privadas (`_parse_cors_list`, `_normalize_origin`, `_validate_cors_methods`, `_validate_cors_headers`) y un `@model_validator(mode="after")` que comprueba las reglas 5 y 9 (comodín en producción y listas no vacías). Los mensajes de error deben mencionar la variable afectada (`CORS_ALLOWED_ORIGINS`, `CORS_ALLOWED_METHODS` o `CORS_ALLOWED_HEADERS`) y estar en español; se documentan en la tabla de errores de configuración.

### Registro del middleware

```python
# app/main.py
from fastapi.middleware.cors import CORSMiddleware

from app.core.config import (
    CORS_EXPOSE_HEADERS,
    CORS_PREFLIGHT_MAX_AGE_SECONDS,
    get_settings,
)

# ...
# El ultimo middleware agregado es el mas externo.
# CORS debe quedar dentro de AccessLog/RequestID para que el preflight
# tambien reciba X-Request-ID y se registre en el access log.
if settings.cors_allowed_origins:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_allowed_origins,
        allow_methods=settings.cors_allowed_methods,
        allow_headers=settings.cors_allowed_headers,
        allow_credentials=False,
        expose_headers=CORS_EXPOSE_HEADERS,
        max_age=CORS_PREFLIGHT_MAX_AGE_SECONDS,
    )
app.add_middleware(AccessLogMiddleware)
app.add_middleware(RequestIDMiddleware)
```

Orden resultante en `app.user_middleware` (de más externo a más interno):
`RequestIDMiddleware`, `AccessLogMiddleware`, `CORSMiddleware`. Si CORS está deshabilitado:
`RequestIDMiddleware`, `AccessLogMiddleware`.

### Matriz de comportamiento HTTP

| Caso | Request | Status | Cabeceras CORS relevantes |
|---|---|---|---|
| Simple permitido | `Origin: http://localhost:4200` | El normal del endpoint | `Access-Control-Allow-Origin: http://localhost:4200`, `Access-Control-Expose-Headers: X-Request-ID, Retry-After`, `Vary: Origin` |
| Simple no permitido | `Origin: http://evil.example` | El normal del endpoint (no se corta) | Sin `Access-Control-Allow-Origin`; `Vary: Origin` |
| Sin `Origin` | — | El normal | Sin cabeceras CORS de origen/exposición; `Vary: Origin` (CORS activo) |
| Preflight permitido | `OPTIONS` + `Origin` permitido + `Access-Control-Request-Method` permitido + cabeceras permitidas | `200` | `Access-Control-Allow-Origin: <origen>`, `Allow-Methods`, `Allow-Headers`, `Max-Age: 600`, `Vary` (4 valores), sin `Allow-Credentials` |
| Preflight origen no permitido | `OPTIONS` + `Origin: http://evil.example` | `400` | Sin `Access-Control-Allow-Origin`; sí `Allow-Methods` y `Max-Age` |
| Preflight método no permitido | Origen permitido + `Access-Control-Request-Method: TRACE` | `400` | `Access-Control-Allow-Origin: <origen>`; método no listado |
| Preflight cabecera no permitida | Origen y método permitidos + `Access-Control-Request-Headers: authorization, x-custom` | `400` | `Access-Control-Allow-Origin: <origen>` |
| CORS deshabilitado | Cualquiera | El normal; `OPTIONS` a una ruta GET ⇒ `405` | Ninguna cabecera CORS |

### Ejemplos

Preflight permitido:

```http
OPTIONS /api/v1/auth/login HTTP/1.1
Origin: http://localhost:4200
Access-Control-Request-Method: POST
Access-Control-Request-Headers: authorization,content-type
```

```http
HTTP/1.1 200 OK
Content-Type: text/plain; charset=utf-8
Access-Control-Allow-Origin: http://localhost:4200
Access-Control-Allow-Methods: GET, POST, PUT, PATCH, DELETE, OPTIONS
Access-Control-Allow-Headers: Accept, Accept-Language, Authorization, Content-Language, Content-Type, X-Request-ID
Access-Control-Max-Age: 600
Vary: Origin, Access-Control-Request-Method, Access-Control-Request-Headers, Access-Control-Request-Private-Network
X-Request-ID: 6f1c...

OK
```

Preflight con origen no permitido:

```http
HTTP/1.1 400 Bad Request
Content-Type: text/plain; charset=utf-8
Access-Control-Allow-Methods: GET, POST, PUT, PATCH, DELETE, OPTIONS
Access-Control-Max-Age: 600
Vary: Origin, Access-Control-Request-Method, Access-Control-Request-Headers, Access-Control-Request-Private-Network
X-Request-ID: 6f1c...

Disallowed CORS origin
```

Respuesta real permitida:

```http
HTTP/1.1 200 OK
Access-Control-Allow-Origin: http://localhost:4200
Access-Control-Expose-Headers: X-Request-ID, Retry-After
Vary: Origin
X-Request-ID: 6f1c...

{"status":"ok","version":"0.1.0","environment":"local"}
```

### Errores de configuración (arranque)

Todos se manifiestan como `pydantic.ValidationError` al instanciar `Settings`/`create_app`; la app no arranca.

| Variable | Condición | Mensaje (subcadena estable) |
|---|---|---|
| `CORS_ALLOWED_ORIGINS` | Origen no `http(s)`, con ruta/query/fragmento/userinfo, puerto no numérico, espacios, comodín parcial o `null` | `CORS_ALLOWED_ORIGINS contiene un origen invalido` |
| `CORS_ALLOWED_ORIGINS` | `*` mezclado con orígenes explícitos | `CORS_ALLOWED_ORIGINS no puede combinar '*' con origenes explicitos` |
| `CORS_ALLOWED_ORIGINS` | `*` en `environment=prod` | `CORS_ALLOWED_ORIGINS no puede contener '*' en produccion` |
| `CORS_ALLOWED_ORIGINS` | JSON mal formado, JSON que no es array o array con elementos no string | `CORS_ALLOWED_ORIGINS` + `JSON`/`lista`/`cadenas` |
| `CORS_ALLOWED_METHODS` | Método fuera de la lista permitida | `CORS_ALLOWED_METHODS contiene un metodo no permitido` |
| `CORS_ALLOWED_METHODS` | `*` | `CORS_ALLOWED_METHODS no admite '*'` |
| `CORS_ALLOWED_METHODS` | Vacío con orígenes no vacíos | `CORS_ALLOWED_METHODS no puede estar vacio` |
| `CORS_ALLOWED_HEADERS` | Nombre con caracteres no válidos | `CORS_ALLOWED_HEADERS contiene un nombre de cabecera invalido` |
| `CORS_ALLOWED_HEADERS` | `*` | `CORS_ALLOWED_HEADERS no admite '*'` |
| `CORS_ALLOWED_HEADERS` | Vacío con orígenes no vacíos | `CORS_ALLOWED_HEADERS no puede estar vacio` |

### `.env.example` (contenido esperado)

```dotenv
# CORS (frontend Angular). Origenes separados por comas o JSON: ["http://localhost:4200"]
# Vacio = CORS deshabilitado. En produccion no se admite "*": define los origenes explicitos.
CORS_ALLOWED_ORIGINS=http://localhost:4200
CORS_ALLOWED_METHODS=GET,POST,PUT,PATCH,DELETE,OPTIONS
CORS_ALLOWED_HEADERS=Authorization,Content-Type,Accept,X-Request-ID
```

`CORS_EXPOSE_HEADERS` y `CORS_PREFLIGHT_MAX_AGE_SECONDS` no son variables de entorno en esta feature (D-8, D-9).

## Criterios de aceptación

Convención: `[test]` = aserción que el reviewer implementa en `tests/**`; `[shell]` = comando con salida esperada. Los tests de settings usan `Settings(_env_file=None, ...)` y `monkeypatch.setenv`/`delenv`; los de integración construyen una app nueva con `create_app()` tras `get_settings.cache_clear()`. El `app` de módulo (`from app.main import app`) **no** se usa para probar configuraciones alternativas, porque sus middlewares se fijan en el import.

### Settings y parseo

- [ ] **CA-1** `[test]` Defaults en `local`: con `CORS_ALLOWED_ORIGINS`, `CORS_ALLOWED_METHODS` y `CORS_ALLOWED_HEADERS` ausentes del entorno, `Settings(_env_file=None)` devuelve exactamente `cors_allowed_origins == ["http://localhost:4200"]`, `cors_allowed_methods == ["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"]` y `cors_allowed_headers == ["Authorization", "Content-Type", "Accept", "X-Request-ID"]`. No lanza `ValidationError`.
- [ ] **CA-2** `[test]` Formato coma-separado: `CORS_ALLOWED_ORIGINS="http://localhost:4200, https://app.example.com "` ⇒ `["http://localhost:4200", "https://app.example.com"]`, en ese orden. `CORS_ALLOWED_METHODS="get, POST, options"` ⇒ `["GET", "POST", "OPTIONS"]`. `CORS_ALLOWED_HEADERS=" Authorization , Content-Type "` ⇒ `["Authorization", "Content-Type"]`.
- [ ] **CA-3** `[test]` Formato JSON: `CORS_ALLOWED_ORIGINS='["http://localhost:4200", "https://app.example.com"]'` produce la misma lista que CA-2; `CORS_ALLOWED_METHODS='["GET","POST"]'` ⇒ `["GET","POST"]`; `CORS_ALLOWED_HEADERS='["Authorization","X-Request-ID"]'` ⇒ `["Authorization","X-Request-ID"]`.
- [ ] **CA-4** `[test]` Normalización y deduplicación: orígenes `" HTTP://LOCALHOST:4200/ , http://localhost:4200 , https://App.Example.com/ "` ⇒ `["http://localhost:4200", "https://app.example.com"]`. Métodos `"get, POST,get, options"` ⇒ `["GET", "POST", "OPTIONS"]`. Cabeceras `"content-type, authorization, Content-Type, AUTHORIZATION"` ⇒ `["Content-Type", "authorization"]` (primera grafía, dedupe case-insensitive, safelist canónica).
- [ ] **CA-5** `[test]` Lista vacía: para cada valor `"", "   ", "[]", '["", "  "]', " , "` en `CORS_ALLOWED_ORIGINS`, `Settings(_env_file=None)` no lanza y `cors_allowed_origins == []`; `cors_allowed_methods` y `cors_allowed_headers` conservan sus defaults. `Settings(_env_file=None, cors_allowed_origins=[])` también es válido.
- [ ] **CA-6** `[test]` Orígenes inválidos: cada uno de `"localhost:4200"`, `"http://"`, `"http://localhost:4200/api"`, `"http://localhost:4200?x=1"`, `"ftp://example.com"`, `"http://user:pass@example.com"`, `"http://local host:4200"`, `"http://localhost:abc"`, `"https://*.example.com"` y `"null"` lanza `ValidationError` cuyo texto contiene `CORS_ALLOWED_ORIGINS`. Un origen válido con puerto (`"https://app.example.com:8443"`) se acepta sin cambios.
- [ ] **CA-7** `[test]` Comodín: en `local`, `dev`, `test` y `staging`, `CORS_ALLOWED_ORIGINS="*"` ⇒ `["*"]`; en `prod` con `JWT_SECRET` válido (≥32 caracteres y distinto del de ejemplo), `CORS_ALLOWED_ORIGINS="*"` lanza `ValidationError` con `produccion` en el texto; `CORS_ALLOWED_ORIGINS="*,http://localhost:4200"` lanza en `local` y en `prod` con el texto `combinar`. `CORS_ALLOWED_METHODS="*"` y `CORS_ALLOWED_HEADERS="*"` lanzan en cualquier entorno.
- [ ] **CA-8** `[test]` Métodos: `"GET, TRACE"` lanza con `CORS_ALLOWED_METHODS` y `TRACE`; con orígenes no vacíos, `CORS_ALLOWED_METHODS=""` lanza con `vacio`; con `CORS_ALLOWED_ORIGINS=""`, `CORS_ALLOWED_METHODS=""` es válido y devuelve `[]`; `"get, GET, Post"` ⇒ `["GET","POST"]`.
- [ ] **CA-9** `[test]` Cabeceras: `"Authorization, Bad Header"` lanza con `CORS_ALLOWED_HEADERS`; con orígenes no vacíos, `CORS_ALLOWED_HEADERS=""` lanza con `vacio`; con `CORS_ALLOWED_ORIGINS=""`, `CORS_ALLOWED_HEADERS=""` es válido; `"content-type, authorization, Content-Type, AUTHORIZATION"` ⇒ `["Content-Type", "authorization"]`.
- [ ] **CA-10** `[test]` `.env.example` contiene las tres variables con los valores por defecto de la sección «.env.example» (incluido `CORS_ALLOWED_ORIGINS=http://localhost:4200`) y comentarios que mencionan que vacío deshabilita CORS y que producción no admite `*`.

### Middleware, orden y respuestas reales

- [ ] **CA-11** `[test]` Con las tres variables fijadas a sus defaults (`local`), `create_app()` expone `[m.cls.__name__ for m in app.user_middleware] == ["RequestIDMiddleware", "AccessLogMiddleware", "CORSMiddleware"]`. Los `kwargs` del `Middleware` de CORS son `allow_origins == ["http://localhost:4200"]`, `allow_methods == ["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"]`, `allow_headers == ["Authorization", "Content-Type", "Accept", "X-Request-ID"]`, `allow_credentials is False`, `tuple(expose_headers) == ("X-Request-ID", "Retry-After")` y `max_age == 600`.
- [ ] **CA-12** `[test]` Simple permitido: `GET /api/v1/health` con `Origin: http://localhost:4200` ⇒ `200`, cuerpo con `status == "ok"`; `Access-Control-Allow-Origin == "http://localhost:4200"`, `Access-Control-Expose-Headers == "X-Request-ID, Retry-After"`, `Vary` contiene `Origin`, `X-Request-ID` presente.
- [ ] **CA-13** `[test]` Simple no permitido: el mismo GET con `Origin: http://evil.example` ⇒ `200` y cuerpo normal, **sin** `Access-Control-Allow-Origin`; `Vary` contiene `Origin`; `X-Request-ID` presente.
- [ ] **CA-14** `[test]` Errores con CORS: `GET /api/v1/auth/me` sin `Authorization` y con `Origin: http://localhost:4200` ⇒ `401`, `code == "not_authenticated"`, `WWW-Authenticate: Bearer`, `Access-Control-Allow-Origin` correcto, `Access-Control-Expose-Headers` correcto y `X-Request-ID`. Esto prueba que CORS envuelve a los handlers de excepción.
- [ ] **CA-15** `[test]` CORS deshabilitado: con `CORS_ALLOWED_ORIGINS=""`, `create_app()` no registra `CORSMiddleware` (`["RequestIDMiddleware", "AccessLogMiddleware"]`); `GET /api/v1/health` con `Origin` permitido ⇒ `200` sin `Access-Control-Allow-Origin`; `OPTIONS /api/v1/health` con `Origin` y `Access-Control-Request-Method` ⇒ `405` sin `Access-Control-Allow-Origin`. Repetir con `environment=prod` y `JWT_SECRET` válido: la app arranca y CORS sigue deshabilitado.
- [ ] **CA-16** `[test]` Sin `Origin`: `GET /api/v1/health` sin cabecera `Origin` ⇒ `200`, sin `Access-Control-Allow-Origin`, sin `Access-Control-Expose-Headers`, pero con `Vary: Origin` y `X-Request-ID`. La respuesta y el cuerpo son idénticos a los de antes de la feature salvo `Vary`.

### Preflight

- [ ] **CA-17** `[test]` Preflight permitido: `OPTIONS /api/v1/auth/login` con `Origin: http://localhost:4200`, `Access-Control-Request-Method: POST` y `Access-Control-Request-Headers: authorization,content-type`, **sin** token ⇒ `200`, cuerpo exactamente `OK`, `Access-Control-Allow-Origin == "http://localhost:4200"`, `Access-Control-Allow-Methods == "GET, POST, PUT, PATCH, DELETE, OPTIONS"`, `Access-Control-Allow-Headers` contiene `Authorization` y `Content-Type`, `Access-Control-Max-Age == "600"`, `Vary` contiene `Origin`, `Access-Control-Request-Method`, `Access-Control-Request-Headers` y `Access-Control-Request-Private-Network`, sin `Access-Control-Allow-Credentials`, y `X-Request-ID` presente.
- [ ] **CA-18** `[test]` Preflight con origen no permitido: `OPTIONS /api/v1/auth/login` con `Origin: http://evil.example` y método/cabeceras válidos ⇒ `400`, cuerpo `Disallowed CORS origin`, **sin** `Access-Control-Allow-Origin`, con `Access-Control-Allow-Methods` y `Access-Control-Max-Age` presentes, y `X-Request-ID` presente.
- [ ] **CA-19** `[test]` Preflight con método no permitido: origen permitido y `Access-Control-Request-Method: TRACE` ⇒ `400`, cuerpo `Disallowed CORS method`, **con** `Access-Control-Allow-Origin` del origen permitido, sin `Access-Control-Allow-Credentials`.
- [ ] **CA-20** `[test]` Preflight con cabecera no permitida: origen permitido, `POST` y `Access-Control-Request-Headers: authorization, x-custom` ⇒ `400`, cuerpo `Disallowed CORS headers`, con `Access-Control-Allow-Origin`; el mismo request con `Access-Control-Request-Headers: authorization` ⇒ `200`.
- [ ] **CA-21** `[test]` Preflight en logs: con `caplog` a `INFO` y una app nueva con defaults, un preflight permitido a `/api/v1/auth/login` produce el evento `request.completed` con `method == "OPTIONS"`, `path == "/api/v1/auth/login"` y `status == 200`; el texto de los logs no contiene el valor de `Origin`, ni `authorization`, ni cookies.

### Exposición, credenciales y entornos

- [ ] **CA-22** `[test]` `Access-Control-Expose-Headers` es exactamente `X-Request-ID, Retry-After` en una respuesta simple permitida y en el `401` de CA-14. En el preflight no se exige (no aplica).
- [ ] **CA-23** `[test]` `allow_credentials=False`: ninguna respuesta (simple, `401`, preflight permitido o fallido) incluye `Access-Control-Allow-Credentials`; el kwarg del middleware es exactamente `False`. Un request con cookie no cambia el comportamiento ni aparece `Access-Control-Allow-Credentials`.
- [ ] **CA-24** `[test]` Comodín en desarrollo: con `environment=local` y `CORS_ALLOWED_ORIGINS="*"`, un GET con `Origin: http://cualquier.example` devuelve `Access-Control-Allow-Origin: *`; un preflight desde ese origen ⇒ `200` con `Access-Control-Allow-Origin: *`; sin `Access-Control-Allow-Credentials`.
- [ ] **CA-25** `[test]` Producción con orígenes explícitos: con `environment=prod`, `JWT_SECRET` de 48 caracteres y `CORS_ALLOWED_ORIGINS=https://app.example.com`, un preflight desde `https://app.example.com` ⇒ `200` con `Access-Control-Allow-Origin: https://app.example.com`; un GET con `Origin: http://localhost:4200` no devuelve `Access-Control-Allow-Origin` (el default no se suma al valor explícito).
- [ ] **CA-26** `[test]` El preflight no toca la base ni exige autenticación: con `create_app()` y sin `dependency_overrides`, un `OPTIONS` a una ruta protegida (`/api/v1/auth/me` o `/api/v1/users`) con `Origin` permitido y `Access-Control-Request-Method: GET` ⇒ `200`, sin `Authorization` y sin error de sesión.

### Calidad y no regresiones

- [ ] **CA-27** `[shell]` `uv run pytest -q` en verde (suite existente + tests nuevos de CORS), `uv run ruff check .` sin errores y `uv run ruff format --check .` limpio.
- [ ] **CA-28** `[shell]` Sin cambios de esquema ni dependencias: `uv run alembic check` imprime `No new upgrade operations detected.`; `git status --porcelain alembic/ pyproject.toml uv.lock` no imprime nada.
- [ ] **CA-29** `[shell]` El diff de la feature se limita a `app/core/config.py`, `app/main.py`, `.env.example`, `docs/features/FEAT-006.md` y `tests/**`: `git status --porcelain app/api app/schemas app/services app/repositories` no imprime nada y `git status --porcelain` no lista rutas de OpenAPI ni de esquemas modificadas.

### Trazabilidad

| Requisito | Criterios |
|---|---|
| Orígenes configurables por env y default de desarrollo | CA-1, CA-2, CA-3, CA-10 |
| Parseo y validación de listas (vacías, duplicados, formatos) | CA-2 a CA-9 |
| `allow_credentials=False` y `Authorization: Bearer` | CA-14, CA-23, SUP-2 |
| Orden correcto de middlewares / preflight sin romper RequestID y AccessLog | CA-11, CA-12, CA-16, CA-17, CA-21 |
| Preflight permitido/no permitido | CA-17 a CA-20, CA-26 |
| `Access-Control-Expose-Headers` | CA-12, CA-14, CA-22 |
| Producción sin comodines y lista vacía deshabilita CORS | CA-5, CA-7, CA-15, CA-25 |
| `.env.example` y calidad | CA-10, CA-27 a CA-29 |

## Plan de pruebas

### Aislamiento y utilidades

- Tests unitarios de settings: `Settings(_env_file=None)` con `monkeypatch.setenv`/`delenv`; la fixture `isolated_settings` ya limpia `get_settings()` al terminar.
- Tests de integración: helper local `build_app(monkeypatch, **env)` que hace `monkeypatch.setenv` para cada clave, `get_settings.cache_clear()` y `create_app()`. Después se usa `httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver")`.
- **No** se parchea el `app` de módulo para CORS: sus middlewares ya están construidos. No se necesita `db_session` ni datos; las rutas usadas (`/health`, `/auth/login` en preflight, `/auth/me` sin token) no consultan la base.
- En tests de `prod` se pasa `JWT_SECRET` de 48 caracteres para aislar el error de CORS del error de secreto (FEAT-002 CA-3).
- Archivos sugeridos: `tests/test_cors_settings.py`, `tests/test_cors_middleware.py`, `tests/test_cors_preflight.py`, `tests/test_cors_env_example.py`.

### Unitarias (sin base de datos)

| # | Test | CA |
|---|---|---|
| U-1 | Defaults de los tres settings | CA-1 |
| U-2 | Parseo coma-separado y JSON | CA-2, CA-3 |
| U-3 | Normalización de orígenes, mayúsculas, `/` final y dedupe | CA-4 |
| U-4 | Listas vacías (orígenes y combinaciones) | CA-5 |
| U-5 | Orígenes inválidos parametrizados | CA-6 |
| U-6 | `*` por entorno, mezcla y `*` en métodos/cabeceras | CA-7 |
| U-7 | Validación de métodos y condicional de vacío | CA-8 |
| U-8 | Validación de cabeceras, dedupe y safelist canónica | CA-9 |
| U-9 | Contenido de `.env.example` | CA-10 |
| U-10 | Orden de `app.user_middleware` y kwargs de CORS | CA-11 |

### Integración (ASGI, sin base de datos)

| # | Test | CA |
|---|---|---|
| I-1 | Simple permitido y no permitido | CA-12, CA-13 |
| I-2 | `401` con cabeceras CORS | CA-14 |
| I-3 | CORS deshabilitado en `local` y `prod` | CA-15 |
| I-4 | Request sin `Origin` | CA-16 |
| I-5 | Preflight permitido con cabeceras exactas | CA-17 |
| I-6 | Preflight con origen, método y cabecera no permitidos | CA-18, CA-19, CA-20 |
| I-7 | Preflight no autenticado ni con base de datos | CA-26 |
| I-8 | Comodín en desarrollo y orígenes explícitos en producción | CA-24, CA-25 |

### Seguridad y observabilidad

| # | Test | CA |
|---|---|---|
| S-1 | Sin `Access-Control-Allow-Credentials` en ningún caso | CA-23 |
| S-2 | `prod` rechaza `*` al arrancar | CA-7 |
| S-3 | Logs de preflight sin origen ni cabeceras sensibles | CA-21 |
| S-4 | CORS no altera autenticación: sin token sigue `401` | CA-14 |
| S-5 | Cabeceras expuestas limitadas a `X-Request-ID` y `Retry-After` | CA-22 |

### Comandos de verificación

```powershell
uv run pytest -q
uv run ruff check .
uv run ruff format --check .
uv run alembic check
git status --porcelain alembic/ pyproject.toml uv.lock
git status --porcelain app/api app/schemas app/services app/repositories
```

## Riesgos y dependencias

| ID | Riesgo | Impacto | Mitigación |
|---|---|---|---|
| **R-1** | En `prod`, olvidar definir `CORS_ALLOWED_ORIGINS` deja permitido el default `http://localhost:4200`. | Bajo | No expone dominios reales y no es un comodín; `.env.example` lo documenta. Si el humano quiere *fail-closed* también para el default, ampliar el validador (D-11). |
| **R-2** | `staging` permite `*` (solo `prod` lo rechaza); si `staging` es internet-facing, la API quedaría abierta a cualquier origen. | Medio | Supuesto SUP-5 y regla 5. Confirmar con el team-lead si `staging` debe endurecerse; el cambio es una condición más en el validador. |
| **R-3** | `Access-Control-Max-Age: 600` hace que un cambio de orígenes tarde hasta 10 minutos en reflejarse en navegadores que cachearon el preflight. | Bajo | D-9 y documentación; para pruebas inmediatas, recargar con caché deshabilitada o limpiar el preflight en DevTools. |
| **R-4** | Un `500` no controlado lo genera `ServerErrorMiddleware`, que queda por fuera de CORS; el navegador puede reportarlo como error CORS y ocultar el status real. | Bajo | Los errores de dominio y validación sí pasan por CORS (CA-14). El `500` ya se registra en logs del servidor; no se añade un middleware nuevo por esta feature. |
| **R-5** | Un cambio futuro en el orden de `add_middleware` puede dejar CORS fuera de `RequestID`/`AccessLog` y romper el request id o el log del preflight. | Medio | Comentario en `create_app()`, CA-11 con orden exacto y CA-17/CA-21 que verifican `X-Request-ID` y el evento de log. |
| **R-6** | Configurar `CORS_ALLOWED_HEADERS` sin `Authorization` hace fallar el preflight del front en silencio (desde el servidor se ve un `400`). | Medio | Default incluye `Authorization`; `.env.example` lo muestra; CA-17 lo verifica. La validación no obliga a incluirlo para no impedir configuraciones solo públicas. |
| **R-7** | Un origen con comodín parcial (`https://*.example.com`) se aceptaría si solo se comprobara el esquema; Starlette hace comparación exacta y nunca coincidiría. | Medio | Regla 4 y CA-6 rechazan cualquier `*` que no sea el origen exacto `*`. |
| **R-8** | Tests que pretendan cambiar CORS sobre `from app.main import app` fallarán: los middlewares se fijan al importar. | Bajo | Plan de pruebas exige `create_app()` + `get_settings.cache_clear()` y prohíbe mutar el `app` de módulo (D-10). |

**Dependencias:** FEAT-001 (`done`: `create_app`, `RequestIDMiddleware`, `AccessLogMiddleware`, `X-Request-ID`), FEAT-002 (`done`: `Authorization: Bearer`, `Retry-After` en `429`) y Starlette 1.9.0 (ya instalado vía FastAPI). No hay dependencias de datos ni migraciones. No bloquea a otras features.

## Preguntas abiertas

Ninguna que bloquee la implementación: el comportamiento está completamente definido en las reglas y decisiones anteriores. Quedan dos confirmaciones **no bloqueantes** para el team-lead/humano:

1. ¿`staging` debe rechazar `*` igual que `prod`? Hoy se implementa como entorno no productivo (SUP-5, R-2). Si la respuesta es sí, basta con ampliar la condición del `model_validator`.
2. ¿`prod` debe fallar al arrancar si `CORS_ALLOWED_ORIGINS` conserva el default `http://localhost:4200`? Hoy no falla; solo se prohíbe `*` (D-11, R-1). Si se prefiere *fail-closed*, se añade una comprobación localizada.

## Historial

- 2026-10-21 — creada por spec-author a partir de las decisiones humanas de `feature-list.md`, `AGENTS.md`, FEAT-001, FEAT-002, FEAT-005 y el código actual (`app/main.py`, `app/core/config.py`, `app/middleware/http.py`, `.env.example`, `tests/`).
- 2026-10-21 — `approved` → `in_progress` (team-lead): spec revisada y aceptada con las decisiones humanas; las dos preguntas abiertas son no bloqueantes y se resuelven con SUP-5/D-11.
- 2026-10-21 — `in_progress` → `in_review` (team-lead): implementación entregada por el implementer; `ruff` limpio, 286 tests en verde, `alembic check` limpio y smoke real de preflight/GET correcto.
- 2026-10-21 — `in_review` → `done` (team-lead): veredicto `APPROVED` del reviewer, 62 tests nuevos (348 en total), `ruff check`/`format --check` y `alembic check` limpios. Informe en `docs/reviews/FEAT-006.md`. Los hallazgos MINOR/NIT quedan como deuda no bloqueante.
