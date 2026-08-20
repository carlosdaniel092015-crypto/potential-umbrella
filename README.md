# whatsapp-ai-platform — Sued Fargesa

Sistema de IA conversacional multiagente para WhatsApp (pedidos de vendedores),
según el [documento técnico integral](docs/documento-tecnico-integral.md).
Estado: **Fase 1** — infraestructura base + webhook WhatsApp + 1 agente.

## Arquitectura (resumen)

- **FastAPI** (`apps/core_api`): webhook YCloud con firma HMAC sobre body crudo,
  idempotencia por `external_id`, Determinador (LangGraph + Strategy) y pipeline
  completo hasta el envío de la respuesta.
- **Django** (`apps/admin_panel` + `apps/shared`): dueño único de modelos y
  migraciones (ADR-06); panel admin con agentes, conversaciones, historial de
  prompts auditable y configuración versionada.
- **Celery** (`apps/workers`): procesamiento desacoplado del webhook en
  producción (`PROCESSING_MODE=celery`), cola `realtime`.
- **Adapters** (`services/providers.py`): YCloud/OpenAI intercambiables; los
  modos `fake` permiten correr todo sin credenciales.

Decisiones y desviaciones respecto al documento: [docs/decisiones-fase-1.md](docs/decisiones-fase-1.md).
Revisión del documento (46 hallazgos): [docs/revision-equipo-hallazgos.md](docs/revision-equipo-hallazgos.md).
Revisión del código (26 hallazgos): [docs/revision-codigo-fase-1.md](docs/revision-codigo-fase-1.md).

## Arranque rápido (dev local, sin Docker)

```bash
uv sync
cp .env.example .env          # completar; para dev sin Postgres: DB_ENGINE=sqlite
DB_ENGINE=sqlite uv run python manage.py migrate
DB_ENGINE=sqlite uv run python manage.py seed_fase1
DB_ENGINE=sqlite uv run python manage.py createsuperuser
# API (webhook):
DB_ENGINE=sqlite uv run python -m uvicorn apps.core_api.main:app --port 8000
# Panel (otro shell):
DB_ENGINE=sqlite uv run python manage.py runserver 8001
```

Con `LLM_PROVIDER=fake` y `MESSAGING_PROVIDER=fake` (defaults) todo corre
offline. Para producción: `openai`/`ycloud` + credenciales reales.

## Con Docker

```bash
docker compose up --build
```

Levanta Postgres (pgvector), Redis (AOF + password), migraciones + seed,
core_api (:8000, modo celery), panel (:8001) y worker.

> **Sin verificar todavía.** La sintaxis del compose y los seis servicios están
> validados con `docker compose config`, pero el daemon de Docker no arrancó en
> la máquina de desarrollo, así que el build de imágenes y el arranque de la
> pila completa siguen sin probarse. Los flujos de código sí están verificados
> con SQLite y la API corriendo de verdad.

## Tests

```bash
uv run python -m pytest --cov --cov-branch
```

78 tests (57 unitarios + 21 de integración end-to-end del webhook), 93% de
cobertura de ramas, 100% offline. `mypy apps/` limpio.
Nota Windows corporativo: `ruff.exe` puede estar bloqueado por política ASR;
el lint corre en CI.

## Variables de entorno

Ver [.env.example](.env.example). Claves mínimas de Fase 1: `DJANGO_SECRET_KEY`,
`DB_*`, `REDIS_URL`/`CELERY_*`, `YCLOUD_API_KEY`, `YCLOUD_WEBHOOK_SECRET`,
`YCLOUD_WHATSAPP_FROM`, `OPENAI_API_KEY`, `PROCESSING_MODE`.

## Roadmap

| Fase | Entregable | Estado |
|---|---|---|
| 1 | Infra base + webhook WhatsApp + 1 agente | ✅ esqueleto verificado |
| 2 | Determinador multiagente (LLM, reglas, fallback humano) | interfaz lista |
| 3 | Integración Odoo (pedidos) | contrato `ERPProvider` definido |
| 4 | Voz (ElevenLabs) | ruta reservada (501) |
| 5 | Panel conversaciones + auto-entrenamiento | modelos listos |
| 6 | Endurecimiento, observabilidad, pentest | pendiente |
