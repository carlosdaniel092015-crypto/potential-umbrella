"""El aviso de traspaso ("te va a atender una persona") se pierde para siempre
si falla su envío.

Hallazgo de la revisión adversarial (docs/revision-enrutamiento-fase-2.md): la
escalada (`Conversation.status = ESCALATED`) se registra ANTES de que el envío
del aviso se intente. Si ese envío falla, el mensaje que causó la escalada
queda `FAILED` y el reintento vuelve a pasar por `_process` — pero como la
conversación ya está `ESCALATED`, el atajo de "el bot se calla" lo marcaba
procesado sin volver a intentar el envío. El cliente nunca se enteraba de que
lo habían escalado.
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
from apps.core_api.services.routing_service import RoutingService
from apps.core_api.services.ycloud_service import FakeMessagingProvider
from apps.shared.enums import ConversationStatus, MessageStatus
from apps.shared.models import Agent, Conversation, Message, RoutingRule, TraceEvent

pytestmark = pytest.mark.django_db(transaction=True)

TELEFONO = "+5215550004321"


@pytest.fixture
def ventas():
    return Agent.objects.create(name="ventas", system_prompt="Vendes.", is_active=True)


@pytest.fixture
def messaging_que_falla_una_vez():
    messaging = FakeMessagingProvider()
    fallos = {"n": 0}
    original = messaging.send_text

    async def falla_la_primera(to, text):
        fallos["n"] += 1
        if fallos["n"] == 1:
            raise RuntimeError("YCloud caído")
        return await original(to, text)

    messaging.send_text = falla_la_primera
    return messaging


@pytest.fixture
def pipeline(ventas, messaging_que_falla_una_vez):
    RoutingRule.objects.create(intent="hablar_con_humano", agent=ventas, min_confidence=0.99)
    agents = AgentRepository()
    routing = RoutingService()
    return MessagePipeline(
        settings=Settings(_env_file=None),
        conversations=ConversationService(),
        agents=agents,
        router=AgentRouter(RuleBasedClassifier(), routing.resolver),
        llm=FakeLLMProvider(),
        messaging=messaging_que_falla_una_vez,
    ), messaging_que_falla_una_vez


def entrante(eid: str) -> IncomingMessage:
    return IncomingMessage(
        external_id=eid, contact_phone=TELEFONO, type="text", content="pasame con un asesor"
    )


def test_el_reintento_reenvia_el_aviso_en_lugar_de_callarse(pipeline):
    p, messaging = pipeline

    r = async_to_sync(p.register)(entrante("wamid.T1"))

    with pytest.raises(RuntimeError):
        async_to_sync(p.process_message)(r.message_id)

    assert Conversation.objects.get().status == ConversationStatus.ESCALATED
    assert Message.objects.get(id=r.message_id).status == MessageStatus.FAILED
    assert messaging.sent == [], "el primer intento debía fallar sin enviar nada"

    resultado = async_to_sync(p.process_message)(r.message_id)

    assert resultado.status == "processed", (
        f"el reintento se calló en vez de reenviar el aviso: {resultado.status}"
    )
    assert len(messaging.sent) == 1
    assert "persona" in messaging.sent[0]["text"].lower()
    assert Message.objects.get(id=r.message_id).status == MessageStatus.PROCESSED


def test_el_reintento_no_escala_dos_veces(pipeline):
    """El retry entra por el atajo de reenvío (_agente_del_turno), no por
    _compose_reply: no debe volver a llamar a escalar_a_persona."""
    p, messaging = pipeline

    r = async_to_sync(p.register)(entrante("wamid.T2"))
    with pytest.raises(RuntimeError):
        async_to_sync(p.process_message)(r.message_id)
    async_to_sync(p.process_message)(r.message_id)

    assert TraceEvent.objects.filter(event_type="escalada_a_persona").count() == 1
    assert len(messaging.sent) == 1


def test_un_mensaje_posterior_en_conversacion_ya_escalada_sigue_en_silencio(pipeline):
    """El arreglo no debe hacer que CUALQUIER mensaje en una conversación
    escalada intente enviar algo: solo el que tiene su propio aviso pendiente."""
    p, messaging = pipeline

    r1 = async_to_sync(p.register)(entrante("wamid.T3"))
    with pytest.raises(RuntimeError):
        async_to_sync(p.process_message)(r1.message_id)  # falla, queda FAILED
    async_to_sync(p.process_message)(r1.message_id)  # reintento: envía el aviso

    enviados_tras_aviso = len(messaging.sent)
    assert enviados_tras_aviso == 1

    r2 = async_to_sync(p.register)(
        IncomingMessage(
            external_id="wamid.T4", contact_phone=TELEFONO, type="text", content="hola?"
        )
    )
    resultado = async_to_sync(p.process_message)(r2.message_id)

    assert resultado.status == "atendida_por_persona"
    assert len(messaging.sent) == enviados_tras_aviso, (
        "un mensaje sin aviso pendiente propio no debe generar ningún envío"
    )
