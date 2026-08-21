"""El bot no puede escribir encima de una persona si el traspaso ocurre
mientras se está generando la respuesta.

Hallazgo de la revisión adversarial: `work.conversation_status` es una foto de
antes de llamar al modelo. Componer la respuesta tarda segundos de red real; en
ese hueco, una persona puede tomar la conversación (desde el panel, o porque
otro mensaje del mismo cliente la escaló por su cuenta). Sin revalidar el
estado justo antes de enviar, la respuesta del bot —generada con información
ya vieja— se escribía encima de lo que la persona acababa de decir.
"""

import pytest
from asgiref.sync import async_to_sync, sync_to_async

from apps.core_api.config import Settings
from apps.core_api.graphs.classifiers import RuleBasedClassifier
from apps.core_api.graphs.router_graph import AgentRouter
from apps.core_api.schemas.messages import IncomingMessage
from apps.core_api.services.agent_repository import AgentRepository
from apps.core_api.services.conversation_service import ConversationService
from apps.core_api.services.llm_service import LLMProvider
from apps.core_api.services.pipeline import MessagePipeline
from apps.core_api.services.routing_service import Decision, Ruta
from apps.core_api.services.ycloud_service import FakeMessagingProvider
from apps.shared.enums import ConversationStatus, MessageStatus
from apps.shared.models import Agent, Conversation, Message

pytestmark = pytest.mark.django_db(transaction=True)

TELEFONO = "+5215550005566"


class LLMQueEscalaAMedioCamino(LLMProvider):
    """Simula el hueco real: mientras el modelo "genera" la respuesta, alguien
    (una persona, u otro mensaje resuelto antes) toma la conversación."""

    def __init__(self, conversation_id: int) -> None:
        self.conversation_id = conversation_id
        self.llamadas = 0

    async def complete(self, system_prompt, messages, model, temperature=0.4):
        self.llamadas += 1

        def escalar():
            Conversation.objects.filter(id=self.conversation_id).update(
                status=ConversationStatus.ESCALATED
            )

        await sync_to_async(escalar)()
        return "Respuesta generada con información ya vieja"


@pytest.fixture
def agente():
    return Agent.objects.create(name="ventas", system_prompt="Vendes.", is_active=True)


def entrante(eid: str, texto: str = "quiero un pedido") -> IncomingMessage:
    return IncomingMessage(external_id=eid, contact_phone=TELEFONO, type="text", content=texto)


def test_la_respuesta_generada_durante_el_traspaso_no_se_envia(agente):
    messaging = FakeMessagingProvider()
    agents = AgentRepository()

    async def resolver(intent, confidence):
        return Ruta(decision=Decision.AGENTE, agent_name="ventas")

    conversation_service = ConversationService()

    # Se registra primero para conocer el conversation_id antes de construir
    # el LLM falso, que necesita saber a qué conversación afectar.
    registro = async_to_sync(conversation_service.register_inbound)(entrante("wamid.C1"))
    llm = LLMQueEscalaAMedioCamino(registro.conversation_id)

    pipeline = MessagePipeline(
        settings=Settings(_env_file=None),
        conversations=conversation_service,
        agents=agents,
        router=AgentRouter(RuleBasedClassifier(), resolver),
        llm=llm,
        messaging=messaging,
    )

    resultado = async_to_sync(pipeline.process_message)(registro.message_id)

    assert llm.llamadas == 1, "el modelo debía llegar a generar la respuesta"
    assert resultado.status == "atendida_por_persona", (
        f"la respuesta generada durante el traspaso se envió igual: {resultado.status}"
    )
    assert messaging.sent == [], (
        f"el bot escribió encima de la persona: {messaging.sent}"
    )
    assert Message.objects.filter(direction="out").count() == 0, (
        "no debe quedar ni rastro de la respuesta descartada como saliente"
    )
    assert Message.objects.get(id=registro.message_id).status == MessageStatus.PROCESSED
    assert Conversation.objects.get().status == ConversationStatus.ESCALATED


def test_una_conversacion_que_sigue_activa_envia_con_normalidad(agente):
    """Contraprueba: sin la carrera, el arreglo no debe bloquear el camino
    normal."""
    messaging = FakeMessagingProvider()
    agents = AgentRepository()

    async def resolver(intent, confidence):
        return Ruta(decision=Decision.AGENTE, agent_name="ventas")

    class LLMNormal(LLMProvider):
        async def complete(self, system_prompt, messages, model, temperature=0.4):
            return "Respuesta normal"

    pipeline = MessagePipeline(
        settings=Settings(_env_file=None),
        conversations=ConversationService(),
        agents=agents,
        router=AgentRouter(RuleBasedClassifier(), resolver),
        llm=LLMNormal(),
        messaging=messaging,
    )

    resultado = async_to_sync(pipeline.handle_inbound)(entrante("wamid.C2"))

    assert resultado.status == "processed"
    assert messaging.sent == [{"to": TELEFONO, "text": "Respuesta normal"}]


def test_el_aviso_de_traspaso_propio_no_lo_bloquea_su_propia_escalada(agente):
    """El agent_config de un aviso de traspaso es None: la revalidación no debe
    aplicarle la regla (si aplicara, ningún aviso saldría nunca, porque la
    conversación YA está escalada por la propia llamada que lo generó)."""
    messaging = FakeMessagingProvider()
    agents = AgentRepository()

    async def resolver(intent, confidence):
        return Ruta(decision=Decision.PERSONA, motivo="pidió hablar con alguien")

    class LLMQueNoDeberiaLlamarse(LLMProvider):
        async def complete(self, system_prompt, messages, model, temperature=0.4):
            raise AssertionError("un traspaso no debe llamar al modelo")

    pipeline = MessagePipeline(
        settings=Settings(_env_file=None),
        conversations=ConversationService(),
        agents=agents,
        router=AgentRouter(RuleBasedClassifier(), resolver),
        llm=LLMQueNoDeberiaLlamarse(),
        messaging=messaging,
    )

    resultado = async_to_sync(pipeline.handle_inbound)(entrante("wamid.C3"))

    assert resultado.status == "processed"
    assert len(messaging.sent) == 1
    assert "persona" in messaging.sent[0]["text"].lower()
    assert Conversation.objects.get().status == ConversationStatus.ESCALATED
