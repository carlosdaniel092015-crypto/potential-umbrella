"""Cuando una persona toma la conversación, el bot se calla.

Sin esto, un vendedor y la IA contestan al mismo cliente a la vez, encima el uno
del otro. Es lo peor que puede hacer un sistema así: el cliente recibe dos voces
que no se coordinan y el vendedor descubre que le están pisando lo que acaba de
prometer.
"""

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
from apps.core_api.services.routing_service import Decision, Ruta
from apps.core_api.services.ycloud_service import FakeMessagingProvider
from apps.shared.enums import ConversationStatus, MessageStatus
from apps.shared.models import Agent, Conversation, Message

pytestmark = pytest.mark.django_db(transaction=True)

TELEFONO = "+5215550002020"


@pytest.fixture(autouse=True)
def agente():
    return Agent.objects.create(name="ventas", system_prompt="Vendes.", is_active=True)


@pytest.fixture
def entorno():
    messaging = FakeMessagingProvider()
    llm = FakeLLMProvider()
    agents = AgentRepository()

    async def resolver(intent, confidence):
        agent = await agents.get_default_active()
        if agent is None:
            return Ruta(decision=Decision.SIN_DESTINO)
        return Ruta(decision=Decision.AGENTE, agent_name=agent.name)

    pipeline = MessagePipeline(
        settings=Settings(_env_file=None),
        conversations=ConversationService(),
        agents=agents,
        router=AgentRouter(RuleBasedClassifier(), resolver),
        llm=llm,
        messaging=messaging,
    )
    return pipeline, messaging, llm


def entrante(external_id: str, texto: str = "hola") -> IncomingMessage:
    return IncomingMessage(
        external_id=external_id, contact_phone=TELEFONO, type="text", content=texto
    )


def test_el_bot_no_responde_si_una_persona_tomo_la_conversacion(entorno):
    pipeline, messaging, llm = entorno

    # Primer mensaje: responde la IA con normalidad.
    async_to_sync(pipeline.handle_inbound)(entrante("wamid.E1", "hola"))
    assert len(messaging.sent) == 1
    llamadas_antes = len(llm.calls)

    # Una persona toma la conversación desde el panel.
    Conversation.objects.update(status=ConversationStatus.ESCALATED)

    # El cliente sigue escribiendo.
    resultado = async_to_sync(pipeline.handle_inbound)(entrante("wamid.E2", "y el precio?"))

    assert resultado.status == "atendida_por_persona", (
        f"el bot respondió encima de la persona: {resultado.status}"
    )
    assert len(messaging.sent) == 1, (
        "el bot escribió al cliente mientras lo atendía una persona: "
        f"{messaging.sent}"
    )
    assert len(llm.calls) == llamadas_antes, (
        "se gastó una llamada al modelo para un mensaje que no se iba a responder"
    )


def test_el_mensaje_se_guarda_para_que_la_persona_lo_vea(entorno):
    """Callarse no es ignorar: el mensaje tiene que quedar registrado y marcado
    como atendido, o el vendedor no sabe que el cliente escribió."""
    pipeline, _, _ = entorno
    Conversation.objects.all().delete()

    async_to_sync(pipeline.handle_inbound)(entrante("wamid.F1", "hola"))
    Conversation.objects.update(status=ConversationStatus.ESCALATED)
    async_to_sync(pipeline.handle_inbound)(entrante("wamid.F2", "sigo esperando"))

    guardado = Message.objects.get(external_id="wamid.F2")
    assert guardado.content == "sigo esperando"
    assert guardado.status == MessageStatus.PROCESSED, (
        "el mensaje quedó pendiente y bloqueará el turno de los siguientes"
    )


def test_una_rafaga_durante_la_atencion_humana_no_atasca_la_conversacion(entorno):
    """Los mensajes que llegan mientras atiende una persona no pueden quedarse
    en cola bloqueando el turno."""
    pipeline, messaging, _ = entorno
    Conversation.objects.all().delete()

    async_to_sync(pipeline.handle_inbound)(entrante("wamid.G1", "hola"))
    Conversation.objects.update(status=ConversationStatus.ESCALATED)

    for i in range(2, 5):
        async_to_sync(pipeline.handle_inbound)(entrante(f"wamid.G{i}", f"mensaje {i}"))

    pendientes = Message.objects.filter(
        direction="in", status__in=[MessageStatus.RECEIVED, MessageStatus.PROCESSING]
    ).count()
    assert pendientes == 0, f"{pendientes} mensajes quedaron atascados"
    assert len(messaging.sent) == 1, "el bot respondió durante la atención humana"


def test_al_devolver_la_conversacion_a_la_ia_vuelve_a_responder(entorno):
    pipeline, messaging, _ = entorno
    Conversation.objects.all().delete()

    async_to_sync(pipeline.handle_inbound)(entrante("wamid.H1", "hola"))
    Conversation.objects.update(status=ConversationStatus.ESCALATED)
    async_to_sync(pipeline.handle_inbound)(entrante("wamid.H2", "silencio"))
    assert len(messaging.sent) == 1

    # El vendedor la devuelve al bot.
    Conversation.objects.update(status=ConversationStatus.ACTIVE)
    resultado = async_to_sync(pipeline.handle_inbound)(entrante("wamid.H3", "quiero pedir"))

    assert resultado.status == "processed"
    assert len(messaging.sent) == 2
