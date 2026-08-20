"""Adapter de YCloud (WhatsApp).

CONTRATO EXTERNO PENDIENTE DE CONFIRMAR (docs/decisiones-fase-1.md, D-02): el
esquema exacto de firma (header, algoritmo, si incluye timestamp anti-replay) y
el payload del webhook deben validarse contra https://docs.ycloud.com antes de
apuntar producción. Este módulo implementa el patrón estándar (HMAC-SHA256
sobre el body crudo) y un parser tolerante del evento
`whatsapp.inbound_message.received`; las fixtures de tests/fixtures/ycloud/
documentan la forma asumida.
"""

import hashlib
import hmac
import logging
import uuid

import httpx

from apps.core_api.schemas.messages import IncomingMessage
from apps.core_api.services.providers import MessagingProvider
from apps.core_api.services.resilience import CircuitBreaker, retry_async

logger = logging.getLogger(__name__)

INBOUND_EVENT_TYPE = "whatsapp.inbound_message.received"

_TYPE_MAP = {
    "text": "text",
    "image": "image",
    "audio": "audio",
    "voice": "audio",
    "document": "document",
    "location": "location",
    "interactive": "interactive",
}


def mask_phone(phone: str) -> str:
    """PII: nunca loguear el número completo (§5.5)."""
    if len(phone) <= 4:
        return "****"
    return f"{'*' * (len(phone) - 4)}{phone[-4:]}"


def verify_signature(raw_body: bytes, signature: str | None, secret: str) -> bool:
    """HMAC-SHA256 sobre los bytes crudos del body, comparación en tiempo constante.

    NUNCA verificar sobre JSON re-parseado: la re-serialización no reproduce los
    bytes firmados (hallazgo crítico de la revisión de seguridad).
    """
    if not signature or not secret:
        return False
    expected = hmac.new(secret.encode(), raw_body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, signature.strip())


def parse_incoming_message(payload: dict) -> IncomingMessage | None:
    """Normaliza el evento de YCloud. Devuelve None para eventos que no son
    mensajes entrantes (callbacks de estado delivered/read/failed, etc.)."""
    if not isinstance(payload, dict) or payload.get("type") != INBOUND_EVENT_TYPE:
        return None
    inbound = payload.get("whatsappInboundMessage")
    if not isinstance(inbound, dict):
        return None
    external_id = inbound.get("id")
    phone = inbound.get("from")
    if not external_id or not phone:
        return None
    raw_type = inbound.get("type", "text")
    msg_type = _TYPE_MAP.get(raw_type, "other")
    content = ""
    if msg_type == "text":
        content = (inbound.get("text") or {}).get("body", "")
    profile = inbound.get("customerProfile") or {}
    return IncomingMessage(
        external_id=str(external_id),
        contact_phone=str(phone),
        contact_name=str(profile.get("name") or ""),
        channel="whatsapp",
        type=msg_type,
        content=content,
        metadata={"ycloud_type": raw_type},
    )


class YCloudMessagingProvider(MessagingProvider):
    def __init__(
        self,
        api_key: str,
        base_url: str,
        from_number: str,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._api_key = api_key
        self._base_url = base_url.rstrip("/")
        self._from = from_number
        self._breaker = CircuitBreaker()
        # Inyectable para tests (httpx.MockTransport) sin red real.
        self._transport = transport

    async def send_text(self, to: str, text: str) -> str:
        async def _send() -> str:
            async with httpx.AsyncClient(timeout=10.0, transport=self._transport) as client:
                response = await client.post(
                    f"{self._base_url}/whatsapp/messages",
                    headers={"X-API-Key": self._api_key},
                    json={
                        "from": self._from,
                        "to": to,
                        "type": "text",
                        "text": {"body": text},
                    },
                )
                response.raise_for_status()
                data = response.json()
                message_id = str(data.get("id", ""))
                if not message_id:
                    # 200 sin id: el mensaje salió, pero no habrá con qué
                    # conciliar los callbacks de estado. Se registra y sigue.
                    logger.warning("YCloud respondió 200 sin id de mensaje")
                return message_id

        def _is_retryable(exc: BaseException) -> bool:
            # Un 4xx (clave inválida, número mal formado) es determinista:
            # reintentarlo solo gasta tiempo y abre el circuito por error de
            # configuración. 429 sí se reintenta.
            if isinstance(exc, httpx.HTTPStatusError):
                status = exc.response.status_code
                return status == 429 or status >= 500
            return True

        async def _send_with_retry() -> str:
            return await retry_async(
                _send,
                attempts=3,
                retry_on=(httpx.TransportError, httpx.HTTPStatusError),
                should_retry=_is_retryable,
            )

        message_id = await self._breaker.call(_send_with_retry)
        logger.info("Mensaje enviado a %s (id=%s)", mask_phone(to), message_id)
        return message_id


class FakeMessagingProvider(MessagingProvider):
    """Para tests y desarrollo sin credenciales: registra los envíos en memoria."""

    def __init__(self) -> None:
        self.sent: list[dict] = []

    async def send_text(self, to: str, text: str) -> str:
        self.sent.append({"to": to, "text": text})
        # uuid y no un contador: un contador reinicia en cada proceso y choca
        # con los external_id que dejó la ejecución anterior en la base de datos.
        return f"fake-out-{uuid.uuid4().hex[:12]}"
