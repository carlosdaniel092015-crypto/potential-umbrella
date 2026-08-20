# DOCUMENTO TÉCNICO INTEGRAL — Sistema de IA Conversacional Multiagente para WhatsApp

> Este documento amplía el brief anterior (`prompt_sistema_ia_whatsapp.md`) incorporando la mirada de un **equipo de tecnología completo**: Arquitecto de Sistemas Senior, Desarrollador Full Stack Senior, QA Senior, Infraestructura/DevOps Senior y Ciberseguridad Senior. Cada sección refleja las decisiones y entregables que ese rol produciría en un proyecto real.

---

## 0. Cómo usar este documento

Está pensado como **paquete de entrada para un equipo de desarrollo o para un agente de IA de programación** (ej. Claude Code). Cada rol aporta:

- **Arquitecto** → decisiones estructurales y por qué (ADRs).
- **Full Stack** → estructura de proyecto y esqueletos de código reales.
- **QA** → estrategia y casos de prueba, incluyendo pruebas específicas de IA.
- **Infraestructura** → entornos, CI/CD, escalabilidad, observabilidad, DR.
- **Ciberseguridad** → modelo de amenazas, controles OWASP, cumplimiento.

---

## 1. ARQUITECTO DE SISTEMAS SENIOR

### 1.1 Estilo arquitectónico

- **Monolito modular** en fase inicial (no microservicios completos): reduce complejidad operativa mientras el equipo es pequeño, pero cada módulo (WhatsApp, Voz, Odoo, Agentes, Determinador) se diseña como **servicio desacoplado internamente** (carpeta/paquete propio, interfaz clara), para poder extraerlo a microservicio si el volumen lo justifica.
- **Patrón Adapter** para cada integración externa (YCloud, ElevenLabs, Odoo, OpenAI): una interfaz común (`MessagingProvider`, `VoiceProvider`, `ERPProvider`, `LLMProvider`) para poder reemplazar proveedores sin tocar el core.
- **Patrón Strategy** para el Determinador Inteligente: la lógica de clasificación es intercambiable (reglas simples → clasificador ligero → LLM), permitiendo evolucionar sin reescribir el router.
- **Circuit Breaker + Retry con backoff exponencial** en todas las llamadas a APIs externas (Odoo, ElevenLabs, OpenAI, YCloud), para evitar fallos en cascada.
- **Event-driven interno**: los eventos clave (mensaje recibido, pedido creado, conversación escalada a humano) se publican en una cola (Celery/Redis Streams) para desacoplar procesamiento y facilitar auditoría/replay.

### 1.2 Registro de decisiones arquitectónicas (ADR — resumen)

| # | Decisión | Justificación | Alternativa descartada |
|---|---|---|---|
| ADR-01 | FastAPI para tiempo real, Django para panel admin, DB compartida | Aprovecha fortalezas de cada framework sin duplicar modelos | Un solo framework (Django puro con Channels) → menor rendimiento async |
| ADR-02 | PostgreSQL como base única con `pgvector` para RAG | Evita mantener un vector store aparte en etapas iniciales | Vector DB dedicado (Pinecone/Weaviate) — se evalúa si el volumen crece |
| ADR-03 | Redis para memoria de sesión + Celery broker | Un solo componente cumple dos funciones, reduce infraestructura | Memoria en Postgres — demasiado lento para contexto conversacional |
| ADR-04 | LangGraph para el Determinador y flujos de agentes | Da checkpoints, estado explícito y trazabilidad nativa | LangChain puro (cadenas) — más difícil de auditar decisiones |
| ADR-05 | Monolito modular inicial | Menor complejidad operativa para el equipo actual | Microservicios desde el día 1 — sobreingeniería prematura |

### 1.3 Modelo de datos (ERD textual, ampliado)

```
Contact 1---N Conversation N---1 Agent
Conversation 1---N Message
Conversation 1---N Order
Agent 1---N AgentPromptHistory
Conversation 1---N Log/Trace
Message 1---1 TrainingExample (opcional, cuando se marca para corrección)
Configuration (clave-valor global, versionado)
User (panel) N---N Role
```

### 1.4 Diagrama de contexto (nivel C4 — Contexto)

```
[Cliente WhatsApp] --mensajes--> [YCloud] --webhook--> [Plataforma IA]
[Cliente teléfono] --llamada--> [ElevenLabs] --webhook/stream--> [Plataforma IA]
[Plataforma IA] --API--> [Odoo ERP]
[Plataforma IA] --API--> [OpenAI]
[Supervisor humano] --panel web--> [Plataforma IA]
```

---

## 2. DESARROLLADOR FULL STACK SENIOR

### 2.1 Estructura de proyecto (monorepo)

```
whatsapp-ai-platform/
├── apps/
│   ├── core_api/                # FastAPI: webhooks, agentes, tiempo real
│   │   ├── main.py
│   │   ├── routers/
│   │   │   ├── whatsapp_webhook.py
│   │   │   ├── voice_webhook.py
│   │   │   └── internal.py
│   │   ├── services/
│   │   │   ├── ycloud_service.py
│   │   │   ├── elevenlabs_service.py
│   │   │   ├── odoo_service.py
│   │   │   ├── llm_service.py
│   │   │   └── redis_memory_service.py
│   │   ├── graphs/
│   │   │   ├── router_graph.py      # Determinador Inteligente (LangGraph)
│   │   │   └── agent_graph_factory.py
│   │   └── schemas/
│   ├── admin_panel/              # Django: configuración, agentes, logs, chats
│   │   ├── settings/
│   │   ├── agents/
│   │   ├── conversations/
│   │   ├── training/
│   │   └── config/
│   ├── shared/                   # Modelos y utilidades compartidas
│   │   ├── models.py              # Usado por Django ORM y accedido por FastAPI
│   │   └── enums.py
│   └── workers/                  # Celery tasks (envíos, entrenamiento, reportes)
├── infra/
│   ├── docker/
│   ├── k8s/
│   └── terraform/
├── tests/
│   ├── unit/
│   ├── integration/
│   ├── e2e/
│   └── load/
├── docker-compose.yml
├── pyproject.toml
└── README.md
```

### 2.2 Ejemplo — Webhook de WhatsApp (FastAPI)

```python
# apps/core_api/routers/whatsapp_webhook.py
from fastapi import APIRouter, Request, HTTPException, Depends
from services.ycloud_service import verify_signature, parse_incoming_message
from graphs.router_graph import determinador_inteligente

router = APIRouter(prefix="/webhooks", tags=["whatsapp"])

@router.post("/ycloud")
async def receive_whatsapp(request: Request):
    payload = await request.json()
    signature = request.headers.get("X-YCloud-Signature")
    if not verify_signature(payload, signature):
        raise HTTPException(status_code=401, detail="Firma inválida")
    message = parse_incoming_message(payload)
    if message is None:
        return {"status": "ignored"}
    # Delega al Determinador Inteligente (LangGraph)
    result = await determinador_inteligente.ainvoke({
        "contact_id": message.contact_id,
        "channel": "whatsapp",
        "content": message.content,
        "message_type": message.type,
    })
    return {"status": "processed", "agent": result.get("agent_name")}
```

### 2.3 Ejemplo — Modelo Django (Agente)

```python
# apps/shared/models.py
from django.db import models

class Agent(models.Model):
    name = models.CharField(max_length=100, unique=True)
    description = models.TextField(blank=True)
    system_prompt = models.TextField()
    llm_model = models.CharField(max_length=50, default="gpt-4.1")
    temperature = models.FloatField(default=0.4)
    allowed_tools = models.JSONField(default=list)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return self.name

class AgentPromptHistory(models.Model):
    agent = models.ForeignKey(Agent, on_delete=models.CASCADE, related_name="history")
    previous_prompt = models.TextField()
    changed_by = models.ForeignKey("auth.User", on_delete=models.SET_NULL, null=True)
    changed_at = models.DateTimeField(auto_now_add=True)
```

### 2.4 Ejemplo — Nodo del Determinador Inteligente (LangGraph)

```python
# apps/core_api/graphs/router_graph.py
from langgraph.graph import StateGraph, END
from typing import TypedDict

class RouterState(TypedDict):
    contact_id: str
    channel: str
    content: str
    intent: str
    agent_name: str
    confidence: float

def classify_intent(state: RouterState) -> RouterState:
    # Llamada ligera al LLM o clasificador propio
    intent, confidence = clasificar(state["content"])
    state["intent"] = intent
    state["confidence"] = confidence
    return state

def route_to_agent(state: RouterState) -> RouterState:
    reglas = obtener_reglas_activas()
    state["agent_name"] = resolver_agente(state["intent"], reglas, state["confidence"])
    return state

builder = StateGraph(RouterState)
builder.add_node("classify_intent", classify_intent)
builder.add_node("route_to_agent", route_to_agent)
builder.set_entry_point("classify_intent")
builder.add_edge("classify_intent", "route_to_agent")
builder.add_edge("route_to_agent", END)
determinador_inteligente = builder.compile()
```

### 2.5 Convenciones de código

- **Formateo/lint:** `ruff` + `black`, ejecutados en pre-commit.
- **Tipado estático:** `mypy` en CI (no bloqueante al inicio, luego estricto).
- **Gestión de dependencias:** `poetry` o `uv` con lockfile versionado.
- **Commits:** Conventional Commits (`feat:`, `fix:`, `refactor:`) para changelog automático.
- **Revisión de código:** mínimo 1 aprobación + checklist de PR (tests, migraciones, documentación).

---

## 3. QA SENIOR

### 3.1 Pirámide de pruebas

```
        ┌───────────────┐
        │   E2E (10%)     │  Flujo completo WhatsApp → Odoo, llamada → transcripción
        ├───────────────┤
        │ Integración(30%)│  Servicios ↔ APIs externas (mockeadas), Redis, DB
        ├───────────────┤
        │ Unitarias (60%) │  Servicios, parsers, reglas del Determinador
        └───────────────┘
```

### 3.2 Pruebas específicas de IA conversacional (críticas — suelen omitirse)

| Tipo de prueba | Qué valida | Herramienta/enfoque |
|---|---|---|
| Regresión de prompts | Que un cambio de prompt no rompa respuestas ya validadas | Suite de "golden conversations" con aserciones semánticas |
| Evaluación LLM-as-judge | Calidad/tono/precisión de la respuesta generada | Segundo LLM evalúa contra rúbrica definida |
| Detección de alucinación | El agente no inventa datos de pedidos/stock | Casos con datos de Odoo mockeados y verificación exacta |
| Enrutamiento del Determinador | % de aciertos de intención vs. dataset etiquetado | Matriz de confusión, objetivo ≥ 90% |
| Fallback ante caída de integración | El sistema responde con mensaje de contingencia si Odoo/ElevenLabs/OpenAI fallan | Chaos testing (simular timeout/500) |
| Latencia end-to-end | Tiempo entre mensaje recibido y respuesta enviada | Umbral objetivo (ej. p95 < 4s texto, < 2s voz por turno) |
| Seguridad de prompts (prompt injection) | El agente no sigue instrucciones maliciosas embebidas en el mensaje del usuario | Casos de prueba con intentos de "ignora tus instrucciones..." |

### 3.3 Pruebas funcionales estándar

- **Unitarias:** cada `Service` (YCloud, Odoo, ElevenLabs, LLM) con mocks de la API externa (`pytest` + `pytest-asyncio` + `responses`/`httpx.MockTransport`).
- **Integración:** flujo webhook → Determinador → agente → respuesta, con Redis y Postgres reales en contenedores de test (`testcontainers`).
- **Contrato de API:** validación de esquemas de request/response con `schemathesis` o Postman/Newman en CI.
- **Carga:** `k6` o `Locust` simulando picos de mensajes concurrentes (ej. 500 mensajes/minuto) verificando degradación controlada.
- **Seguridad:** integración de `OWASP ZAP` (DAST) en pipeline de staging.

### 3.4 Gestión de defectos

| Severidad | Ejemplo | SLA de corrección |
|---|---|---|
| Crítica | El sistema crea pedidos duplicados en Odoo | Inmediata (hotfix) |
| Alta | Un agente responde fuera de su dominio | 24-48h |
| Media | Latencia por encima del umbral en horas pico | Próximo sprint |
| Baja | Mensaje de log mal formateado | Backlog |

### 3.5 Definition of Done — QA

- [ ] Cobertura de pruebas unitarias ≥ 80% en `services/` y `graphs/`.
- [ ] Suite de "golden conversations" ejecutándose en cada PR que toque prompts.
- [ ] Prueba de carga ejecutada antes de cada release a producción.
- [ ] Cero vulnerabilidades críticas/altas abiertas (según escaneo DAST/SAST) antes de release.

---

## 4. INFRAESTRUCTURA / DEVOPS SENIOR

### 4.1 Entornos

| Entorno | Propósito | Datos |
|---|---|---|
| `dev` | Desarrollo local (docker-compose) | Sintéticos/mock |
| `staging` | Pruebas de integración y QA, réplica de prod | Anonimizados |
| `production` | Operación real | Reales, con backups |

### 4.2 Contenerización (ejemplo base)

```dockerfile
# infra/docker/core_api.Dockerfile
FROM python:3.11-slim
WORKDIR /app
COPY pyproject.toml poetry.lock ./
RUN pip install poetry && poetry install --no-root --only main
COPY apps/core_api ./apps/core_api
COPY apps/shared ./apps/shared
CMD ["uvicorn", "apps.core_api.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "4"]
```

```yaml
# docker-compose.yml (extracto)
services:
  core_api:
    build:
      context: .
      dockerfile: infra/docker/core_api.Dockerfile
    env_file: .env
    depends_on: [postgres, redis]
    ports: ["8000:8000"]
  admin_panel:
    build:
      context: .
      dockerfile: infra/docker/admin_panel.Dockerfile
    env_file: .env
    depends_on: [postgres, redis]
    ports: ["8001:8000"]
  worker:
    build:
      context: .
      dockerfile: infra/docker/worker.Dockerfile
    command: celery -A apps.workers worker -l info
    depends_on: [redis, postgres]
  postgres:
    image: postgres:16
    volumes: ["pgdata:/var/lib/postgresql/data"]
    environment:
      POSTGRES_DB: whatsapp_ai
      POSTGRES_USER: ${DB_USER}
      POSTGRES_PASSWORD: ${DB_PASSWORD}
  redis:
    image: redis:7-alpine
    volumes: ["redisdata:/data"]
volumes:
  pgdata:
  redisdata:
```

### 4.3 CI/CD (ejemplo GitHub Actions, resumido)

```yaml
name: ci-cd
on: [push, pull_request]
jobs:
  test:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - run: pip install poetry && poetry install
      - run: poetry run ruff check .
      - run: poetry run mypy apps/
      - run: poetry run pytest --cov=apps --cov-fail-under=80
  security-scan:
    needs: test
    runs-on: ubuntu-latest
    steps:
      - run: pip install pip-audit && pip-audit
      - uses: github/codeql-action/analyze@v3   # SAST
  deploy-staging:
    needs: [test, security-scan]
    if: github.ref == 'refs/heads/develop'
    runs-on: ubuntu-latest
    steps:
      - run: echo "Deploy a staging (ArgoCD/Helm/K8s)"
  deploy-production:
    needs: [test, security-scan]
    if: github.ref == 'refs/heads/main'
    environment: production
    runs-on: ubuntu-latest
    steps:
      - run: echo "Deploy a producción (requiere aprobación manual)"
```

### 4.4 Escalabilidad y disponibilidad

- **Horizontal Pod Autoscaler (K8s)** en `core_api` basado en CPU/latencia.
- **Redis** en modo cluster o con réplica + Sentinel para alta disponibilidad de memoria conversacional.
- **PgBouncer** para pooling de conexiones a Postgres bajo carga alta.
- **Colas separadas** en Celery (prioridad alta: respuestas en tiempo real; prioridad baja: entrenamiento, reportes).
- **Objetivo de SLO inicial:** 99.5% disponibilidad, p95 de respuesta < 4s en texto.

### 4.5 Observabilidad

- **Métricas:** Prometheus + Grafana (dashboards: latencia por integración, tasa de error por proveedor, uso de tokens OpenAI, mensajes/minuto).
- **Trazas:** OpenTelemetry, correlacionando `trace_id` desde el webhook hasta la respuesta.
- **Logs centralizados:** Loki o ELK, con retención definida (ej. 90 días operativos, luego archivado).
- **Alertas:** caída de integración externa, error rate > umbral, cola de Celery creciendo sin procesarse.

### 4.6 Backup y recuperación ante desastres

| Componente | Backup | RPO | RTO |
|---|---|---|---|
| PostgreSQL | Snapshot diario + WAL continuo | 15 min | 1 hora |
| Redis | Snapshot (no crítico, es memoria efímera) | N/A | Minutos |
| Configuración/Secrets | Versionado en gestor de secretos | Inmediato | Minutos |

---

## 5. CIBERSEGURIDAD SENIOR

### 5.1 Modelo de amenazas (resumen STRIDE)

| Amenaza | Ejemplo en este sistema | Mitigación |
|---|---|---|
| Spoofing | Webhook falso simulando ser YCloud/ElevenLabs | Verificación de firma HMAC + IP allowlist |
| Tampering | Modificación de payload en tránsito | HTTPS/TLS obligatorio, validación de esquema estricta |
| Repudiation | Negar que un agente/humano tomó una acción | Logs firmados/inmutables con `trace_id` y usuario |
| Information Disclosure | Exposición de datos de clientes/pedidos | Cifrado en reposo, control de acceso por rol, minimización de datos en logs |
| Denial of Service | Flood de mensajes o llamadas al webhook | Rate limiting, WAF, autoscaling con límites |
| Elevation of Privilege | Un agente humano accede a funciones de admin | RBAC estricto en Django, revisión de permisos |

### 5.2 OWASP Top 10 aplicado

| Riesgo OWASP | Control aplicado |
|---|---|
| Broken Access Control | RBAC en Django Admin y en endpoints internos de FastAPI; principio de mínimo privilegio |
| Cryptographic Failures | TLS 1.2+, cifrado de campos sensibles (tokens, datos de pago si existieran) con AES-256 |
| Injection | Uso de ORM (Django) y queries parametrizadas; sanitización de inputs antes de pasarlos a Odoo/LLM |
| Insecure Design | Threat modeling documentado antes de codear cada módulo crítico |
| Security Misconfiguration | Hardening de contenedores (usuario no-root, imágenes mínimas), escaneo de configuración con `kube-bench` si aplica |
| Vulnerable Components | `pip-audit`/`Dependabot` en CI, actualización periódica de dependencias |
| Auth Failures | MFA para el panel admin, expiración de sesión, bloqueo tras intentos fallidos |
| Software/Data Integrity | Firmas de webhooks, verificación de integridad en despliegues (imágenes firmadas) |
| Logging Failures | Logs estructurados con alertas ante intentos de acceso anómalos |
| SSRF | Validación estricta de URLs salientes (ej. si un agente pudiera generar links dinámicos) |

### 5.3 Prompt Injection y seguridad específica de IA

- **Aislamiento de instrucciones:** el prompt del sistema de cada agente se marca como no-modificable; el contenido del usuario nunca se concatena de forma que pueda "sobrescribir" reglas del sistema.
- **Filtrado de salidas sensibles:** el agente nunca debe exponer prompts internos, claves, ni datos de otros clientes aunque se le pida explícitamente.
- **Límites de acciones (tools):** cada agente solo puede invocar las herramientas explícitamente permitidas (ej. el agente de "Soporte" no puede ejecutar `crear_pedido()`).
- **Validación post-generación:** antes de ejecutar una acción crítica (crear pedido, cobrar, cancelar), se valida contra reglas de negocio, no solo se confía en la salida del LLM.

### 5.4 Gestión de secretos

- Credenciales de YCloud, ElevenLabs, Odoo y OpenAI en **Vault / AWS Secrets Manager / Kubernetes Secrets**, nunca en variables de entorno planas en el repo.
- Rotación periódica de tokens (ej. cada 90 días o ante sospecha de compromiso).
- Principio de mínimo privilegio: credenciales de Odoo con permisos acotados solo a los modelos necesarios (`sale.order`, `res.partner`, `stock.quant`).

### 5.5 Cumplimiento y privacidad de datos

- Definir **política de retención** de conversaciones (ej. 12 meses) y proceso de anonimización/eliminación bajo solicitud.
- Registrar **consentimiento** del usuario si sus conversaciones se usan para entrenamiento/mejora del sistema.
- Evaluar aplicabilidad de normativas locales de protección de datos según el país de operación (ej. equivalentes a GDPR en Latinoamérica).
- Minimizar PII en logs (enmascarar números de teléfono completos, no loguear contenido de tarjetas si existiera).

### 5.6 Pruebas de seguridad continuas

- **SAST** (CodeQL/Bandit) en cada PR.
- **DAST** (OWASP ZAP) en staging antes de cada release.
- **Dependency scanning** (pip-audit/Snyk) automatizado semanalmente.
- **Pentest externo** recomendado al menos una vez antes de ir a producción y luego anualmente.
- **Plan de respuesta a incidentes:** runbook con pasos (contención, erradicación, recuperación, comunicación) y responsables definidos.

---

## 6. ROADMAP CONJUNTO CON RESPONSABLES

| Fase | Entregable | Rol líder | Roles de apoyo |
|---|---|---|---|
| 1 | Infraestructura base + webhook WhatsApp + 1 agente | Infraestructura | Full Stack |
| 2 | Determinador Inteligente + multiagente | Arquitecto | Full Stack, QA |
| 3 | Integración Odoo (pedidos) | Full Stack | QA, Ciberseguridad (permisos) |
| 4 | Integración de voz (ElevenLabs) | Full Stack | QA (latencia), Infraestructura (streaming) |
| 5 | Panel de conversaciones + auto-entrenamiento | Full Stack | QA, Ciberseguridad (privacidad de datos) |
| 6 | Endurecimiento, observabilidad, pentest | Ciberseguridad | Infraestructura, QA |

---

## 7. RIESGOS Y MITIGACIONES

| Riesgo | Probabilidad | Impacto | Mitigación |
|---|---|---|---|
| Cambios en API de YCloud/ElevenLabs sin aviso | Media | Alto | Capa Adapter + monitoreo de contratos de API + versionado |
| Costos elevados de OpenAI por mal control de tokens | Media | Medio | Límite de tokens por conversación, caché de respuestas frecuentes |
| Respuestas incorrectas del agente afectando ventas/confianza | Media | Alto | QA de prompts + revisión humana + fallback a agente humano |
| Caída de Odoo durante horario pico | Baja | Alto | Circuit breaker + cola de reintentos + mensaje de contingencia al cliente |
| Filtración de datos de clientes | Baja | Muy alto | Cifrado, RBAC, auditoría, pentest |

---

## 8. DEFINITION OF DONE GLOBAL (consolidado)

- [ ] Arquitectura documentada (ADRs) y revisada por el equipo.
- [ ] Código con cobertura de pruebas ≥ 80% y sin vulnerabilidades críticas/altas abiertas.
- [ ] Pipeline CI/CD funcional con despliegue a staging automático y a producción con aprobación manual.
- [ ] Observabilidad activa (métricas, logs, trazas, alertas) antes del primer release a producción.
- [ ] Threat model documentado y controles OWASP verificados.
- [ ] Panel administrativo operativo (agentes, configuración, conversaciones, logs, entrenamiento).
- [ ] Plan de backup/DR probado (restauración ensayada al menos una vez).
- [ ] Aprobación conjunta de Arquitectura, QA, Infraestructura y Ciberseguridad antes de salir a producción.

---

## 9. Notas finales

- Este documento es la **versión de equipo completo** del brief original; úsalo como fuente única de verdad para kickoff de proyecto, estimaciones y definición de sprints.
- Los ejemplos de código son **esqueletos ilustrativos**, no producción lista para deploy: deben adaptarse a la versión exacta de las APIs de YCloud, ElevenLabs, Odoo y al SDK de OpenAI vigente al momento de construir, ya que estas plataformas actualizan sus contratos con frecuencia.
