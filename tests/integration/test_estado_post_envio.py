"""Caminos descubiertos ejecutando el sistema de verdad: qué pasa cuando el
envío al cliente sale bien pero la persistencia del estado falla."""

import pytest
from asgiref.sync import async_to_sync

from apps.core_api.config import Settings
from apps.core_api.graphs.classifiers import RuleBasedClassifier
from apps.core_api.graphs.router_graph import AgentRouter
from apps.core_api.schemas.messages import IncomingMessage
from apps.core_api.services.agent_repository import AgentRepository
from apps.core_api.services.conversation_service import ConversationService
from apps.core_api.services.llm_service import FakeLLMProvider
from apps.core_api.services.pipeline import MessagePipeline
from apps.core_api.services.ycloud_service import FakeMessagingProvider
from apps.shared.enums import MessageStatus
from apps.shared.models import Agent, Conversation, Message

pytestmark = pytest.mark.django_db(transaction=True)


def build_pipeline(conversations=None, messaging=None):
    agents = AgentRepository()

    async def resolver(intent, confidence):
        agent = await agents.get_default_active()
        return agent.name if agent else None

    return MessagePipeline(
        settings=Settings(_env_file=None),
        conversations=conversations or ConversationService(),
        agents=agents,
        router=AgentRouter(RuleBasedClassifier(), resolver),
        llm=FakeLLMProvider(canned_reply="listo"),
        messaging=messaging or FakeMessagingProvider(),
    )


def incoming(external_id: str = "wamid.A") -> IncomingMessage:
    return IncomingMessage(
        external_id=external_id,
        contact_phone="+5215550002222",
        type="text",
        content="quiero un pedido",
    )


@pytest.fixture(autouse=True)
def agente():
    return Agent.objects.create(name="ventas", system_prompt="Vendes.", is_active=True)


def test_external_id_colisionado_no_rompe_el_envio():
    """Un id de mensaje saliente repetido no puede tumbar el flujo: es un dato
    de conciliación, no el estado del mensaje."""

    class MessagingColisionado:
        def __init__(self) -> None:
            self.sent: list[dict] = []

        async def send_text(self, to, text):
            self.sent.append({"to": to, "text": text})
            return "id-repetido"

    messaging = MessagingColisionado()
    pipeline = build_pipeline(messaging=messaging)

    primero = async_to_sync(pipeline.handle_inbound)(incoming("wamid.A"))
    segundo = async_to_sync(pipeline.handle_inbound)(incoming("wamid.B"))

    assert primero.status == "processed"
    assert segundo.status == "processed"
    assert len(messaging.sent) == 2
    salientes = Message.objects.filter(direction="out")
    assert salientes.count() == 2
    assert all(m.status == MessageStatus.SENT for m in salientes)
    # Solo uno conserva el id; el otro queda enviado sin él.
    assert salientes.filter(external_id="id-repetido").count() == 1


def test_fallo_al_persistir_estado_no_devuelve_error_tras_enviar():
    """El cliente ya recibió la respuesta: propagar el error haría que YCloud
    reintentara un mensaje entregado."""

    class PersistenciaRota(ConversationService):
        async def mark_outbound_sent(self, message_id, external_id):
            raise RuntimeError("base de datos caída")

    messaging = FakeMessagingProvider()
    pipeline = build_pipeline(conversations=PersistenciaRota(), messaging=messaging)

    result = async_to_sync(pipeline.handle_inbound)(incoming())

    assert result.status == "processed"  # no relanza
    assert len(messaging.sent) == 1
    # Queda para conciliación manual: el entrante no llegó a PROCESSED.
    assert Message.objects.get(direction="in").status == MessageStatus.PROCESSING


def test_conversacion_reutilizada_entre_mensajes():
    pipeline = build_pipeline()
    async_to_sync(pipeline.handle_inbound)(incoming("wamid.A"))
    async_to_sync(pipeline.handle_inbound)(incoming("wamid.B"))

    assert Conversation.objects.count() == 1
    historial = list(
        Message.objects.filter(conversation__isnull=False).order_by("id").values_list(
            "direction", flat=True
        )
    )
    assert historial == ["in", "out", "in", "out"]
