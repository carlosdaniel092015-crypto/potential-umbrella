from fastapi import FastAPI

from apps.core_api.bootstrap_django import setup_django


def create_app() -> FastAPI:
    # El ORM debe estar listo antes de importar routers (que importan servicios
    # que importan apps.shared.models) — ADR-06.
    setup_django()

    from apps.core_api.routers import internal, voice_webhook, whatsapp_webhook

    app = FastAPI(
        title="Plataforma IA WhatsApp — Sued Fargesa",
        version="0.1.0",
        docs_url=None,  # sin Swagger público: superficie mínima (§5.2)
        redoc_url=None,
    )
    app.include_router(whatsapp_webhook.router)
    app.include_router(voice_webhook.router)
    app.include_router(internal.router)
    return app


app = create_app()
