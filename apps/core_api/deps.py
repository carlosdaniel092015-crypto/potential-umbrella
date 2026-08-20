"""Composición de dependencias (inyección vía FastAPI Depends; los tests las
sustituyen con app.dependency_overrides — hallazgo QA sobre singletons)."""

from apps.core_api.config import Settings, get_settings
from apps.core_api.graphs.classifiers import RuleBasedClassifier
from apps.core_api.graphs.router_graph import AgentRouter
from apps.core_api.services.agent_repository import AgentRepository
from apps.core_api.services.conversation_service import ConversationService
from apps.core_api.services.llm_service import build_llm_provider
from apps.core_api.services.pipeline import MessagePipeline
from apps.core_api.services.providers import MessagingProvider
from apps.core_api.services.ycloud_service import (
    FakeMessagingProvider,
    YCloudMessagingProvider,
)


def build_messaging_provider(settings: Settings) -> MessagingProvider:
    if settings.messaging_provider == "ycloud":
        if not settings.ycloud_api_key or not settings.ycloud_whatsapp_from:
            raise ValueError(
                "MESSAGING_PROVIDER=ycloud requiere YCLOUD_API_KEY y YCLOUD_WHATSAPP_FROM"
            )
        return YCloudMessagingProvider(
            api_key=settings.ycloud_api_key,
            base_url=settings.ycloud_base_url,
            from_number=settings.ycloud_whatsapp_from,
        )
    return FakeMessagingProvider()


def build_default_pipeline(settings: Settings) -> MessagePipeline:
    agents = AgentRepository()

    async def resolve_agent(intent: str, confidence: float) -> str | None:
        # Fase 1: agente único. Fase 2: reglas intent→agente desde Configuration
        # + umbral de confianza con fallback a humano.
        agent = await agents.get_default_active()
        return agent.name if agent else None

    return MessagePipeline(
        settings=settings,
        conversations=ConversationService(),
        agents=agents,
        router=AgentRouter(RuleBasedClassifier(), resolve_agent),
        llm=build_llm_provider(settings),
        messaging=build_messaging_provider(settings),
    )


_pipeline: MessagePipeline | None = None
_worker_pipeline: MessagePipeline | None = None


def get_pipeline() -> MessagePipeline:
    global _pipeline
    if _pipeline is None:
        _pipeline = build_default_pipeline(get_settings())
    return _pipeline


def get_worker_pipeline() -> MessagePipeline:
    """Pipeline cacheado por proceso del worker. Construir uno nuevo en cada
    tarea reiniciaría los cortacircuitos, que así nunca alcanzarían su umbral y
    seguirían martilleando una integración caída."""
    global _worker_pipeline
    if _worker_pipeline is None:
        _worker_pipeline = build_default_pipeline(get_settings())
    return _worker_pipeline


def reset_pipeline() -> None:
    global _pipeline, _worker_pipeline
    _pipeline = None
    _worker_pipeline = None
