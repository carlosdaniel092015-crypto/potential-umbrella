import json
import logging

from fastapi import APIRouter, Depends, HTTPException, Request

from apps.core_api.config import Settings, get_settings
from apps.core_api.deps import get_pipeline
from apps.core_api.services.conversation_service import ConversacionNoDisponible
from apps.core_api.services.pipeline import MessagePipeline
from apps.core_api.services.ycloud_service import parse_incoming_message, verify_signature

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/webhooks", tags=["whatsapp"])


async def _read_limited_body(request: Request, max_bytes: int) -> bytes:
    """Lee el cuerpo cortando en el límite.

    Se rechaza por Content-Length antes de leer y, para peticiones sin ese
    encabezado (chunked), se corta durante el streaming: `request.body()` no
    tiene tope y bufferizaría gigabytes en memoria antes de poder rechazarlos
    — un DoS sin autenticar.
    """
    declared = request.headers.get("content-length")
    if declared is not None:
        try:
            if int(declared) > max_bytes:
                raise HTTPException(status_code=413, detail="Payload demasiado grande")
        except ValueError as exc:
            raise HTTPException(status_code=400, detail="Content-Length inválido") from exc

    chunks: list[bytes] = []
    total = 0
    async for chunk in request.stream():
        total += len(chunk)
        if total > max_bytes:
            raise HTTPException(status_code=413, detail="Payload demasiado grande")
        chunks.append(chunk)
    return b"".join(chunks)


@router.post("/ycloud")
async def receive_whatsapp(
    request: Request,
    settings: Settings = Depends(get_settings),
    pipeline: MessagePipeline = Depends(get_pipeline),
):
    # Orden estricto (D-02): límite de tamaño → firma sobre bytes crudos →
    # recién entonces parsear JSON. Nada del cuerpo se procesa sin autenticar.
    raw = await _read_limited_body(request, settings.webhook_max_body_bytes)

    if settings.webhook_signature_required:
        signature = request.headers.get(settings.ycloud_signature_header)
        if not verify_signature(raw, signature, settings.ycloud_webhook_secret):
            raise HTTPException(status_code=401, detail="Firma inválida")

    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=400, detail="JSON inválido") from exc

    incoming = parse_incoming_message(payload)
    if incoming is None:
        # Callbacks de estado (delivered/read) y eventos desconocidos: 200 sin
        # invocar el pipeline, para que YCloud no reintente.
        return {"status": "ignored"}

    try:
        return await _procesar(incoming, settings, pipeline)
    except ConversacionNoDisponible as exc:
        # No se pudo tomar la conversación (bloqueo retenido, o se cerró en
        # medio). Un 503 hace que el proveedor reintente; un 200 perdería el
        # mensaje y un 500 daría a entender un fallo del sistema.
        logger.warning("Conversación no disponible: %s", exc)
        raise HTTPException(
            status_code=503, detail="La conversación no está disponible ahora mismo"
        ) from exc


async def _procesar(incoming, settings: Settings, pipeline: MessagePipeline):
    if settings.processing_mode == "celery":
        # ACK rápido (§1.1 event-driven): se persiste y deduplica aquí, y a la
        # cola solo va el id — nunca el teléfono ni el texto del cliente.
        from apps.workers.tasks import process_inbound_message

        registration = await pipeline.register(incoming)
        if registration.already_processed:
            return {"status": "duplicate"}
        process_inbound_message.delay(registration.message_id)
        return {"status": "queued"}

    result = await pipeline.handle_inbound(incoming)

    if result.status == "esperando_turno":
        # En modo inline no hay worker que lo reencole: si respondiéramos 200,
        # YCloud daría el mensaje por entregado y se perdería. Un 503 hace que
        # el proveedor lo reintente.
        raise HTTPException(
            status_code=503, detail="Otro mensaje de este contacto está en curso"
        )

    return {"status": result.status, "agent": result.agent_name}
