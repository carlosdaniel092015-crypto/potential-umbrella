# Decisiones de implementación — Fase 1

Complementan los ADR-01..05 del [documento técnico](documento-tecnico-integral.md).
Cada decisión resuelve una ambigüedad o contradicción detectada en la
[revisión multi-perspectiva](revision-equipo-hallazgos.md) del documento.

| # | Decisión | Detalle | Estado |
|---|---|---|---|
| D-01 (ADR-06) | **Django dueño único de modelos y migraciones** | `apps.shared` es una app Django real; FastAPI y Celery consumen el ORM solo vía `sync_to_async`/métodos async. Migraciones solo con `manage.py migrate` (job `migrate` en compose). Prohibido ORM síncrono directo en handlers async. | Implementado |
| D-02 | **Firma HMAC sobre el body crudo** | `verify_signature(raw_bytes, header, secret)` con `hmac.compare_digest`; límite de tamaño y verificación ANTES de parsear JSON. **Pendiente**: confirmar contra docs.ycloud.com el header exacto y si la firma incluye timestamp anti-replay; las fixtures de `tests/fixtures/ycloud/` documentan el contrato asumido. | Implementado (contrato por confirmar) |
| D-03 | **Idempotencia de webhook** | `Message.external_id` unique + `get_or_create`; duplicado con estado `processed` → respuesta `duplicate` sin reprocesar. Un fallo a mitad de pipeline deja el mensaje en `received`/`failed` y el reintento lo continúa. Segunda barrera (SETNX Redis) cuando haya réplicas. | Implementado |
| D-04 | **Webhook con modo dual** | `PROCESSING_MODE=inline` (dev/tests: pipeline await directo) / `celery` (producción: ACK inmediato + task en cola `realtime`, coherente con §1.1 event-driven). El compose usa `celery`. | Implementado |
| D-05 | **Estado conversacional** | Historial canónico en Postgres (tabla Message); Redis solo caché caliente con TTL 24h (`redis_memory_service`, aún no en el camino de Fase 1). Checkpointer LangGraph: se decide en Fase 2. Concurrencia por contacto (ráfagas): **limitación conocida de Fase 1**, serialización por contacto planificada para Fase 2. | Parcial (documentado) |
| D-06 | **Determinador en Fase 1** | Grafo LangGraph completo (classify → route) con Strategy: `RuleBasedClassifier` (keywords) + resolver trivial al único agente activo. Fase 2 sustituye clasificador/resolver (reglas en Configuration, umbral de confianza, fallback a humano) sin tocar la interfaz. | Implementado |
| D-07 | **Tipos no soportados: nunca silencio** | Audio/imagen/documento reciben mensaje de capacidad no disponible (configurable). Callbacks de estado de YCloud → 200 `ignored` sin pipeline. | Implementado |
| D-08 | **Toolchain** | `uv` (poetry no; el doc permitía ambos), Python 3.12 en contenedores, imports absolutos `apps.*`, Dockerfile único multi-target con usuario no-root, 1 worker uvicorn parametrizable. | Implementado |
| D-09 | **Secretos** | `.env` SOLO dev (gitignored, con `.env.example`); staging/prod vía gestor de secretos. `gitleaks` en CI. | Implementado |
| D-10 | **Custom User** | `shared.User` (AbstractUser) + `AUTH_USER_MODEL` antes de la primera migración; FKs de auditoría con `on_delete=PROTECT`. | Implementado |
| D-11 | **Base de datos** | Imagen `pgvector/pgvector:pg16` desde el día 1 (ADR-02). Roles Postgres separados core_api/panel: pendiente (Fase 6, hallazgo de seguridad). | Parcial |
| D-12 | **Ventana 24h WhatsApp** | `Conversation.last_inbound_at` se registra desde Fase 1; verificación de ventana + plantillas antes de enviar: Fase 2+. | Parcial |
| D-13 | **Default de modelo LLM** | `Agent.llm_model` con default leído de settings (`DEFAULT_LLM_MODEL`), no quemado en la migración. Proveedor por Adapter: `openai` \| `fake` (dev/tests corren 100% offline). | Implementado |
| D-14 | **Resiliencia** | Retry con backoff + circuit breaker propios en envíos YCloud y llamadas OpenAI. Limitación conocida: estado del breaker por proceso; breaker compartido en Redis cuando haya réplicas. | Implementado |
| D-15 | **AgentPromptHistory completo** | Guarda prompt anterior Y nuevo, poblado automáticamente desde el admin, append-only, autor protegido. | Implementado |
| D-16 | **Reclamo atómico del mensaje** | Estado `PROCESSING` reclamado con `UPDATE ... WHERE status IN (received, failed)`: cierra la ventana en la que dos ejecuciones concurrentes del mismo mensaje respondían dos veces. Solo `FAILED` es reprocesable. | Implementado |
| D-17 | **Cola sin PII** | En modo celery el webhook persiste y deduplica, y encola **solo el id**; el worker rehidrata desde Postgres. El broker Redis (con AOF a disco) nunca almacena teléfonos ni texto de clientes. | Implementado |
| D-18 | **Fail-fast de configuración** | `SECRET_KEY` sin default compartido: `prod.py` aborta si falta o si es la de desarrollo, y `ENVIRONMENT=production` con settings `.dev` también aborta. `/internal/*` exige token fuera de dev. | Implementado |
| D-19 | **Semántica de errores del webhook** | Fallo antes o durante el envío → 500 para que YCloud reintente. Fallo **después** de un envío exitoso → se registra como crítico y se responde 200: propagar el error haría reenviar un mensaje ya entregado. El mensaje queda en `PROCESSING` para conciliación manual. | Implementado |
| D-20 | **Límite de cuerpo antes de bufferizar** | Rechazo por `Content-Length` y corte durante el streaming: `request.body()` no tiene tope y permitía agotar memoria sin autenticarse. | Implementado |

## Matriz de versiones congelada (uv.lock)

Python 3.12+ · Django 6.1 · FastAPI 0.141 · langgraph 1.2 · openai 3.3 ·
celery 5.6 · redis-py 6.4 · psycopg 3.3 · pydantic 2.13 · gunicorn 26.1

## Pendientes que NO son de Fase 1 (no perder de vista)

- Confirmar contrato real de YCloud (firma + payload + endpoint de envío) con la cuenta real.
- MFA del panel (paquete concreto), rate limiting en el borde, roles Postgres separados, pseudonimización completa de PII (hoy: máscara en logs propios y cola sin PII).
- Serialización/debounce por contacto para ráfagas de mensajes.
- Dataset etiquetado de intenciones + suite de golden conversations (QA §3.2) al llegar el Determinador LLM.
- Conciliación automática de mensajes que quedan en `PROCESSING` por fallo de persistencia post-envío (hoy: alerta por log crítico).
- Breaker con estado compartido en Redis cuando haya varias réplicas (hoy: por proceso, cacheado por worker).

## Verificación (bucle ejecutado, no inferido)

- **78 tests** (57 unitarios + 21 de integración) en verde, 100 % offline.
- **93 %** de cobertura de ramas.
- **mypy** limpio en 58 archivos con el plugin de django-stubs.
- **Ejecución real** con base limpia: salud OK, texto → `processed/ventas`, duplicado → `duplicate`, audio → mensaje de capacidad, callback de estado → `ignored`, JSON roto → 400, cuerpo de 300 KB → 413, webhook de voz → 501. Estado final en base sin mensajes zombi.
- **ruff** no pudo ejecutarse en la máquina local (política ASR de Windows bloquea el ejecutable por prevalencia); queda cubierto en CI.
