# FEAT-015 — Observabilidad: métricas Prometheus, agregación de logs y (opcional) tracing

**Estado:** `proposed` (registrada a pedido del humano el 2026-10-25; **solo propuesta, sin implementación** hasta aprobación explícita)
**Fecha:** 2026-10-25
**Alcance previsto:** endpoint `/api/v1/metrics` con Prometheus (métricas RED y de negocio), servicios `prometheus` + `grafana` en Docker Compose, agregación de logs con Loki/Promtail, y — diferido bajo condición — tracing OpenTelemetry. Todo en etapas independientes; nada se implementa hasta que el humano apruebe el alcance.
**Fuente de verdad funcional:** RNF-01/RNF-02/RNF-04 (criterios de rendimiento verificables), §3 de `01-requerimientos-y-backlog.md` (observabilidad operativa del dispensario) y decisión de arquitectura ADR-0001 (structlog). Esta propuesta se deriva del análisis de estado actual (2026-10-25): logging estructurado con `request_id` ya existe; métricas y trazas no.

## Contexto

Hoy el servicio tiene logging estructurado completo (structlog, JSON en producción, redacción de datos sensibles, `X-Request-ID` propagado, access log con `duration_ms` por request y eventos de negocio con `actor_id`). No tiene sistema de métricas ni de trazas distribuidas: los paquetes `opentelemetry-*`/`sentry-sdk` presentes en `uv.lock` son dependencias transitivas de `fastapi[standard]` sin configurar. La tabla `audit_log` existe (FEAT-003) pero RF-22 (auditoría genérica, prioridad *Could*) no está implementada.

La propuesta agrega observabilidad **sin reemplazar** lo existente: structlog queda como está; las etapas son aditivas.

## Alcance por etapas (cada etapa es una unidad de trabajo independiente)

### Etapa 1 — Métricas con Prometheus (núcleo de la feature)

- Dependencia: `prometheus-client` + `prometheus-fastapi-instrumentator` (middleware maduro, sin instrumentación manual).
- Endpoint `GET /api/v1/metrics` **protegido** (decisión P-1: rol `ADMIN` o solo red interna), excluido del access log para no ensuciar la telemetría propia.
- **Métricas RED** por endpoint: contador de requests (método/ruta/status), histograma de latencia, requests en vuelo.
- **Métricas de negocio** (decisión P-2): `visits_created_total`, `visits_annulled_total`, `login_failures_total` (aprovecha el rate limiter de FEAT-002), `report_generated_total`, usuarios activos (gauge), pool de SQLAlchemy (gauge opcional).
- Infraestructura: servicios `prometheus` (scrape de `/metrics`) y `grafana` (dashboard base RED + negocio) en `docker-compose`, solo desarrollo/local, sin dependencias de nube.
- Tests: `/metrics` exige auth (401/403), expone las series esperadas tras requests reales y los contadores de negocio se incrementan en el servicio.
- ADR-0002: decisiones de observabilidad (librerías, endpoint, retención, políticas de etiquetas).

### Etapa 2 — Agregación y alertas (config/infra, sin tocar código de la app)

- Loki + Promtail para los logs JSON (ya estructurados; es plug-and-play).
- Alertas base: tasa de 500, p95 por endpoint, login fallidos anormales, health de la base.
- Solo `docker-compose` + config + dashboards. Se descarta ELK por peso para este tamaño.

### Etapa 3 — Tracing OpenTelemetry (diferida, bajo condición)

- `opentelemetry-instrumentation-fastapi` + `-sqlalchemy` + exporter OTLP → Jaeger/Tempo.
- Se habilita **solo cuando se justifique**: varios servicios, necesidad de spans de DB por endpoint (N+1, queries lentas) o correlación con el frontend Angular. Hoy `request_id` ya da correlación de logs.
- Las librerías SDK ya llegan con `fastapi[standard]`; activarlas es barato cuando toque.

### Etapa 4 — Auditoría RF-22 (independiente, opcional)

- Servicio que escriba en `audit_log` las operaciones sensibles: login OK/fallido, reset-password, cambios de usuarios/roles, anulación de visitas.
- Es trazabilidad de negocio; complementa la observabilidad pero puede aprobarse por separado.

## Fuera de alcance

- Migrar de structlog (no hace falta) ni spans manuales por función.
- Exponer `/metrics` sin protección, ni loguear el scrape en el access log.
- ELK u observabilidad SaaS.
- Cambios en los endpoints de negocio existentes (solo instrumentación aditiva).

## Decisiones requeridas para aprobar (las resuelve el humano al pasar de `proposed` a `approved`)

- **P-1** — Protección de `/metrics`: rol `ADMIN` (reutiliza JWT) vs. solo red interna (docker network) vs. ambas.
- **P-2** — Métricas de negocio: cuáles entran en v1 (propuesta mínima: `visits_created_total`, `login_failures_total`, `report_generated_total`).
- **P-3** — Infra: ¿Grafana + Prometheus en `docker-compose` en v1, o solo el endpoint `/metrics` y la infra queda para después?
- **P-4** — OTel: ¿diferido (recomendado) o incluido en v1?

## Riesgos

- El endpoint `/metrics` suma una superficie de seguridad: debe quedar protegido y fuera del access log (P-1).
- El middleware de instrumentación agrega una capa ASGI: orden correcto con `RequestIDMiddleware`/`AccessLogMiddleware` y que el scrape de Prometheus no distorsione los p95 de la app.
- Sin riesgos de datos: no se tocan tablas ni datos existentes (excepto la etapa 4, que solo inserta en `audit_log`).

## Historial

| Fecha | Cambio |
|---|---|
| 2026-10-25 | Propuesta registrada como `proposed` a pedido del humano ("solo la propuesta, no la implementación"). |
