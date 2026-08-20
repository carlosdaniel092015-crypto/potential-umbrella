from functools import lru_cache
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    environment: str = "dev"

    # YCloud (WhatsApp)
    ycloud_api_key: str = ""
    ycloud_webhook_secret: str = ""
    ycloud_base_url: str = "https://api.ycloud.com/v2"
    ycloud_whatsapp_from: str = ""  # número WABA emisor
    # NOTA: confirmar header y esquema exacto de firma contra la doc vigente de
    # YCloud antes de producción (docs/decisiones-fase-1.md, D-02).
    ycloud_signature_header: str = "X-YCloud-Signature"
    webhook_signature_required: bool = True
    webhook_max_body_bytes: int = 256 * 1024

    # Proveedor de mensajería: "fake" corre sin credenciales (dev/tests).
    messaging_provider: Literal["ycloud", "fake"] = "fake"

    # LLM (patrón Adapter)
    llm_provider: Literal["openai", "fake"] = "fake"
    llm_model: str = "gpt-4.1"
    openai_api_key: str = ""

    # Procesamiento del webhook: "inline" (dev/test) o "celery" (producción).
    processing_mode: Literal["inline", "celery"] = "inline"

    redis_url: str = "redis://localhost:6379/0"
    conversation_history_limit: int = 10

    # Turno por conversación: cuánto se espera a un mensaje anterior antes de
    # considerarlo abandonado y seguir sin él.
    orden_margen_minutos: int = 15
    # Segundos antes de reintentar un mensaje que está esperando su turno, y
    # techo de esa espera creciente.
    orden_reintento_segundos: float = 2
    orden_reintento_tope_segundos: float = 30

    # Endpoints /internal/*: fuera de dev exigen este token. No basta confiar en
    # que el ingress no los publique — core_api sí es público (recibe el webhook).
    internal_api_token: str = ""

    # Respuesta a tipos de mensaje no soportados en Fase 1 (nunca silencio).
    unsupported_type_reply: str = (
        "Por ahora solo puedo procesar mensajes de texto. "
        "¿Me lo puedes escribir? Gracias."
    )


@lru_cache
def get_settings() -> Settings:
    return Settings()
