"""Endpoints internos (salud y versión).

Viven en la misma app que el webhook, que sí es pública, así que fuera de `dev`
exigen un token de servicio: dejar la protección solo en manos del ingress
significa que un despliegue mal configurado los expone sin autenticación.
"""

from asgiref.sync import sync_to_async
from fastapi import APIRouter, Depends, Header, HTTPException

from apps.core_api.config import Settings, get_settings

router = APIRouter(prefix="/internal", tags=["internal"])


async def require_internal_access(
    x_internal_token: str | None = Header(default=None),
    settings: Settings = Depends(get_settings),
) -> None:
    if settings.environment == "dev":
        return
    if not settings.internal_api_token:
        raise HTTPException(status_code=503, detail="INTERNAL_API_TOKEN no configurado")
    if x_internal_token != settings.internal_api_token:
        raise HTTPException(status_code=401, detail="Token interno inválido")


@router.get("/health", dependencies=[Depends(require_internal_access)])
async def health():
    def _check_db() -> str:
        from django.db import connection

        try:
            connection.ensure_connection()
            return "ok"
        except Exception:
            return "error"

    return {"status": "ok", "db": await sync_to_async(_check_db)()}


@router.get("/version", dependencies=[Depends(require_internal_access)])
async def version():
    return {"name": "whatsapp-ai-platform", "version": "0.1.0"}
