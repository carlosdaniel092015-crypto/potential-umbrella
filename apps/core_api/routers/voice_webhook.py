from fastapi import APIRouter, HTTPException

router = APIRouter(prefix="/webhooks", tags=["voz"])


@router.post("/elevenlabs")
async def receive_voice():
    # Fase 4 del roadmap. El endpoint existe para reservar la ruta y devolver
    # un error explícito (nunca 404 ambiguo) si se configura antes de tiempo.
    raise HTTPException(status_code=501, detail="Integración de voz: Fase 4 del roadmap")
