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
| D-05 | **Estado conversacional** | Historial canónico en Postgres (tabla Message); Redis solo caché caliente con TTL 24h (`redis_memory_service`, aún no en el camino de Fase 1). Checkpointer LangGraph: se decide en Fase 2. Concurrencia por contacto (ráfagas): **resuelta en D-28** con el turno por conversación. | Resuelto (ver D-28) |
| D-06 | **Determinador en Fase 1** | Grafo LangGraph completo (classify → route) con Strategy: `RuleBasedClassifier` (keywords) + resolver trivial al único agente activo. **Sustituido en Fase 2 por el enrutamiento por reglas** sin tocar la interfaz del grafo, como estaba previsto: ver [determinador-fase-2.md](determinador-fase-2.md). | Superado (Fase 2) |
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
| D-27 | **Una respuesta por mensaje, atada a él** | `Message.reply_to` apunta al entrante que se contesta, con restricción única sobre las respuestas. Crear la fila **es** el derecho a responder: dos ejecuciones del mismo mensaje no escriben dos veces, y otro mensaje de la misma ráfaga sí recibe la suya. Sustituye la guarda «¿hay respuesta posterior a este id?», que descartaba en silencio los mensajes 2..N de cada ráfaga en el camino de producción. | Implementado |
| D-28 | **Turno por conversación** | Un mensaje no se procesa mientras quede otro anterior del mismo cliente sin terminar (`RECEIVED`, `FAILED`, o `PROCESSING` con reclamo vigente): devuelve `esperando_turno` y el worker publica una tarea nueva con espera creciente hasta 30 s. Así cada respuesta ve las anteriores y salen en orden; el turno es por conversación, así que otro cliente nunca espera detrás. **El progreso lo garantiza el worker, no el margen**: al agotar su presupuesto de espera procesa sin el predecesor. Cierra el pendiente D-05. | Implementado |
| D-29 | **Nunca `self.retry` con `autoretry_for`** | El turno reencola con `apply_async` (tarea nueva, contador propio) y la tarea no está enlazada. Motivo: `Task.retry(max_retries=…)` fija `override_max_retries` en el singleton de la tarea del proceso worker y, combinado con `autoretry_for`, queda pegado y altera el límite de reintentos de los fallos reales posteriores. Un test estructural vigila la firma. | Implementado |
| D-30 | **`processing_started_at`** | El margen que libera un mensaje abandonado se mide desde el reclamo, no desde `created_at`: con `created_at`, drenar un backlog viejo desactivaba el turno para toda la ráfaga. | Implementado |
| D-31 | **El historial solo contiene lo que el cliente vio** | `recent_history` filtra los salientes por `SENT` y ordena por id. Una respuesta sin enviar metida en el prompt hacía que el bot diera por leído lo que nunca llegó. | Implementado |
| D-32 | **El reintento reenvía, no regenera** | Si ya existe un texto generado y sin enviar para ese mensaje, se reenvía tal cual. Regenerarlo con el modelo produciría otra redacción, y ante un fallo de envío ambiguo el cliente vería dos versiones distintas. | Implementado |
| D-33 | **Rescate de mensajes con la tarea perdida** | `manage.py reencolar_pendientes` devuelve a la cola los entrantes en `received` viejos y los `processing` abandonados. Debe programarse cada pocos minutos: sin él, un mensaje cuya tarea se perdió bloquea su conversación. | Implementado (requiere programarse) |
| D-35 | **Tope a la espera del bloqueo** | `lock_timeout` en la conexión de Postgres (3 s, configurable) y `ConversacionNoDisponible` → 503, para que el proveedor reintente. Sin tope, una transacción ajena que retuviera el bloqueo de una conversación habría detenido la API entera, porque todas sus operaciones de base comparten un único hilo del ejecutor de asgiref. El bloqueo revalida además que la conversación siga abierta: se elige antes de bloquearla, y colgar un mensaje de una que se cerró en medio partiría el historial. | Implementado |
| D-34 | **El turno ordena por posición, no por id** | `Conversation.next_seq` reparte `Message.seq` bajo bloqueo de la fila de la conversación (`select_for_update` donde el motor lo soporta). El id no sirve para ordenar: se asigna al INSERT y en Postgres el INSERT no es el COMMIT, así que dos webhooks concurrentes del mismo contacto podían obtener ids en un orden y confirmar en el otro, con lo que el sucesor no veía a su predecesor. Los mensajes sin posición (anteriores al contador) caen al id. Restricción única de posición por conversación, y la migración **rellena** los mensajes anteriores (sin el relleno, `NULL < n` no es verdadero en SQL y los viejos quedaban invisibles). Una posición ausente cuenta como anterior. El historial se ordena por la misma posición, no por id. **La carrera no es reproducible en la suite**: corre en SQLite, que serializa las escrituras; el test que la demostraría está escrito y se activa solo con un motor que tenga bloqueo de fila. | Implementado (sin verificar en Postgres) |

| D-36 | **Enrutamiento por reglas con tres salidas** | `RoutingRule` (intención → agente, confianza mínima) editable en el panel, con una regla activa por intención garantizada por la base y comodín `*`. La resolución devuelve agente, persona o nadie: «no hay agente configurado» es un problema de instalación y no se disfraza de traspaso. Sin caché: un cambio en el panel se aplica al mensaje siguiente. Umbrales repartidos por lo que cuesta equivocarse, no por igual. | Implementado |
| D-37 | **Mientras atiende una persona, el bot calla** | Con la conversación escalada, el pipeline registra el mensaje, lo marca atendido para no bloquear el turno, y no responde ni gasta una llamada al modelo. Sin esto, la IA y el vendedor contestaban encima el uno del otro. Devolverla a la IA es cambiar el estado en el panel. | Implementado |
| D-38 | **Al pasar a una persona se avisa al cliente** | Se le responde que le va a atender alguien del equipo, y el traspaso queda registrado con su motivo (`TraceEvent`). Dejarlo esperando en silencio es la peor opción. | Implementado |

## Matriz de versiones congelada (uv.lock)

Python 3.12+ · Django 6.1 · FastAPI 0.141 · langgraph 1.2 · openai 3.3 ·
celery 5.6 · redis-py 6.4 · psycopg 3.3 · pydantic 2.13 · gunicorn 26.1

## Pendientes que NO son de Fase 1 (no perder de vista)

- Confirmar contrato real de YCloud (firma + payload + endpoint de envío) con la cuenta real.
- MFA del panel (paquete concreto), rate limiting en el borde, roles Postgres separados, pseudonimización completa de PII (hoy: máscara en logs propios y cola sin PII).
- Agrupar una ráfaga en una sola respuesta (debounce). El turno por conversación (D-28) ya evita respuestas cruzadas y descoordinadas; agrupar además varios mensajes en una única contestación gasta presupuesto de latencia y no se decide sin medir el p95 primero.
- Suite de conversaciones de referencia (QA §3.2). El dataset etiquetado y el medidor de enrutamiento ya existen: ver [medicion-enrutamiento.md](medicion-enrutamiento.md).
- Conciliación automática de mensajes que quedan en `PROCESSING` por fallo de persistencia post-envío (hoy: alerta por log crítico).
- Breaker con estado compartido en Redis cuando haya varias réplicas (hoy: por proceso, cacheado por worker).
- **Verificar D-34 contra Postgres.** El orden ya se deriva de una posición repartida bajo bloqueo de la conversación, pero la carrera que motivó el cambio no se puede reproducir en SQLite. El test de registro concurrente está escrito y se salta solo; correr la suite contra Postgres lo activa.
- Programar `reencolar_pendientes` (Celery beat o cron) en el despliegue: sin él, un mensaje cuya tarea se perdió bloquea su conversación hasta que el worker agote el presupuesto de espera.

## Verificación (bucle ejecutado, no inferido)

- **155 tests** (72 unitarios + 70 de integración + 13 de evaluación) en verde. Uno se salta: el de registro concurrente, que necesita un motor con bloqueo de fila.
- **93 %** de cobertura de ramas.
- **mypy** limpio en 58 archivos con el plugin de django-stubs.
- **Ejecución real** con base limpia: salud OK, texto → `processed/ventas`, duplicado → `duplicate`, audio → mensaje de capacidad, callback de estado → `ignored`, JSON roto → 400, cuerpo de 300 KB → 413, webhook de voz → 501. Estado final en base sin mensajes zombi.

### Lo que NO se pudo verificar en esta máquina

- **ruff**: la política de reducción de superficie de ataque de Windows bloquea su ejecutable por prevalencia. Queda cubierto en CI.
- **docker compose**: `docker compose config` valida la sintaxis y los seis servicios, pero el daemon de Docker Desktop no llegó a arrancar, así que **el build de las imágenes y el arranque de la pila (Postgres + Redis + migrate + worker) están sin probar**. Antes de darlos por buenos hay que correr `docker compose up --build` en una máquina con Docker operativo. Los caminos de código sí están verificados con SQLite y ejecución real de la API.
