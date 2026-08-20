# Revisión multi-perspectiva del documento técnico

Cuatro revisores independientes (arquitectura, seguridad, QA, viabilidad de
implementación) auditaron `documento-tecnico-integral.md` antes del kickoff.
**46 hallazgos** (7 críticos, 17 altos). Los que afectaban la Fase 1 quedaron
resueltos en la implementación (columna Estado; detalle en
[decisiones-fase-1.md](decisiones-fase-1.md)).

## Críticos

| Hallazgo | Lente | Estado |
|---|---|---|
| El ejemplo del webhook procesa el LLM inline, contradiciendo la arquitectura event-driven de §1.1; con reintentos del proveedor produce procesamiento duplicado | Arquitectura, Viabilidad | ✅ D-04: modo dual inline/celery; compose usa celery |
| Falta total de idempotencia/deduplicación de webhooks — el propio doc lista "pedidos duplicados" como defecto crítico sin diseñar el control | Arquitectura, QA | ✅ D-03: `external_id` unique + estados de procesamiento |
| ADR-01 no resuelve CÓMO FastAPI async accede al ORM Django (el problema central del stack) | Arquitectura, Viabilidad | ✅ D-01/ADR-06: Django dueño único; `sync_to_async` obligatorio |
| Estado conversacional sin dueño: Redis vs checkpointer LangGraph vs Postgres, sin TTL ni política de concurrencia | Arquitectura | ✅ parcial D-05: canónico en Postgres; concurrencia por contacto pendiente Fase 2 |
| `verify_signature` recibe JSON re-parseado en vez del body crudo: la verificación HMAC no puede funcionar (o se desactivará "temporalmente") | Seguridad | ✅ D-02: HMAC sobre bytes crudos + `compare_digest` + límite de tamaño |
| Fase 1 no testeable sin credenciales: faltan fixtures YCloud, contrato de firma, stub de LLM y mock del envío | QA | ✅ fixtures versionadas, `sign_payload()`, `FakeLLMProvider`, `FakeMessagingProvider`, MockTransport |
| El flujo nunca genera ni envía la respuesta al cliente ni persiste Contact/Conversation/Message | Arquitectura, Viabilidad | ✅ pipeline completo implementado (persistir → rutear → generar → enviar → marcar) |

## Altos (resumen)

- **Anti-replay y comparación de firma**: sin timestamp ni ventana de tolerancia — ⚠️ pendiente confirmar el esquema real de YCloud (D-02); `compare_digest` ya aplicado.
- **Secretos**: contradicción §5.4 (Vault) vs compose con `.env` — ✅ D-09: `.env` solo dev, gitleaks en CI.
- **Panel Django como vector de prompt injection persistente** (`system_prompt`/`allowed_tools` sin control) — ✅ parcial D-15 (historial append-only, autor protegido); flujo de aprobación en dos pasos y MFA: Fase 6.
- **Defensa de prompt injection aspiracional** (§5.3 no implementable como está) — ⚠️ para Fase 2/3: validación server-side de argumentos de tools, partner resuelto desde la sesión (nunca del LLM), confirmación humana en acciones de escritura.
- **Confused deputy hacia Odoo** (credencial única, autorización por tool y no por argumento) — ⚠️ diseño para Fase 3 (contrato `ERPProvider` ya exige `idempotency_key`).
- **Redis "no crítico" en DR pese a ser broker Celery** — ✅ AOF + password en compose; RPO real: definir en Fase 6.
- **Imports rotos / empaquetado inconsistente** entre §2.1, §2.2 y Dockerfile — ✅ D-08: imports absolutos `apps.*`, Dockerfile multi-target no-root.
- **Dockerfile con poetry roto** (venv de poetry vs CMD directo) — ✅ D-08: uv + `uv.lock` congelado.
- **Sin lista de variables de entorno** — ✅ `.env.example` completo.
- **Cero versiones pineadas** (langgraph/openai con breaking changes entre majors) — ✅ matriz congelada en uv.lock y documentada.
- **`apps/shared/models.py` no puede ser módulo suelto** (necesita AppConfig) — ✅ `apps.shared` es app Django.
- **Contrato del payload YCloud sin especificar** — ⚠️ fixtures documentan lo asumido; confirmar con la cuenta real antes de producción.
- **Objetivo enrutamiento ≥90% no medible** (no existe dataset etiquetado) — ⚠️ entregable de Fase 2.
- **Objetivo p95<4s sin puntos de medición** — ⚠️ definir métrica/histograma al montar observabilidad (Fase 6).
- **Singletons globales impiden inyectar mocks** — ✅ app factory + `Depends` + overrides en tests.
- **Concurrencia por contacto (ráfagas) sin estrategia** — ⚠️ limitación conocida de Fase 1, documentada (D-05).
- **CI de seguridad roto tal como estaba escrito** (sin checkout, CodeQL sin init) — ✅ workflow corregido + gitleaks + pip-audit sobre lockfile.

## Medios/bajos relevantes ya incorporados

`postgres:16` sin pgvector → imagen `pgvector/pgvector:pg16` (D-11) · FK a
`auth.User` frágil → custom User (D-10) · `AgentPromptHistory` incompleto →
guarda anterior+nuevo con autor protegido (D-15) · default `gpt-4.1` quemado en
migración → default desde settings (D-13) · tipos no-texto ignorados en
silencio → mensaje de capacidad (D-07) · callbacks de estado al mismo endpoint
→ `ignored` sin pipeline · uvicorn `--workers 4` hardcodeado → parametrizado ·
`internal.py` sin autenticación → documentado no exponer en ingress público ·
ERD congela agente por conversación → `Message.agent` por mensaje · ventana 24h
ausente → `last_inbound_at` desde Fase 1 (D-12).

## Medios/bajos diferidos (con fase propuesta)

- Invalidación de configuración/prompts en caliente entre panel y workers (caché versionada) — Fase 2.
- Umbral de confianza del Determinador con fallback a humano — Fase 2.
- Notas de voz de WhatsApp (STT) e imágenes de listas de pedido — Fase 4+.
- Entidad Template + política de reintentos fuera de ventana de 24h — Fase 2/3.
- Circuit breaker con estado compartido (Redis), rate limiting dimensionado, logs inmutables, cifrado de campos, roles Postgres separados, MFA — Fase 6.
- Golden conversations / LLM-as-judge operativos (formato, rúbrica, presupuesto de tokens) — Fase 2.
