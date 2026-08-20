"""Pipeline aislado de la DB: la persistencia se sustituye por un fake en
memoria que respeta el contrato de ConversationService (mismos estados y
mismos argumentos, para que una regresión de wiring no pase inadvertida)."""

import pytest

from apps.core_api.config import Settings
from apps.core_api.graphs.classifiers import RuleBasedClassifier
from apps.core_api.graphs.router_graph import AgentRouter
from apps.core_api.schemas.messages import IncomingMessage
from apps.core_api.services.agent_repository import AgentConfig
from apps.core_api.services.conversation_service import InboundRegistration, InboundWork
from apps.core_api.services.llm_service import FakeLLMProvider
from apps.core_api.services.pipeline import MessagePipeline
from apps.core_api.services.ycloud_service import FakeMessagingProvider

AGENT = AgentConfig(
    id=1, name="ventas", system_prompt="Vendes.", llm_model="gpt-4.1", temperature=0.4
)


class FakeConversationService:
    def __init__(
        self,
        already_processed: bool = False,
        created: bool = True,
        claim_ok: bool = True,
        ya_respondido: bool = False,
        history: list[dict] | None = None,
        anteriores_pendientes: bool = False,
    ) -> None:
        self.already_processed = already_processed
        self.created = created
        self.claim_ok = claim_ok
        self.ya_respondido = ya_respondido
        self.history = history or []
        self.anteriores_pendientes = anteriores_pendientes
        self.outbound: list[dict] = []
        self.marks: list[tuple] = []

    async def tiene_anteriores_pendientes(
        self, conversation_id: int, message_id: int, margen_minutos: int = 15
    ) -> bool:
        return self.anteriores_pendientes

    async def register_inbound(self, incoming: IncomingMessage) -> InboundRegistration:
        return InboundRegistration(
            conversation_id=1,
            message_id=10,
            created=self.created,
            already_processed=self.already_processed,
        )

    async def claim_for_processing(self, message_id: int) -> bool:
        self.marks.append(("claim", message_id, self.claim_ok))
        return self.claim_ok

    async def claim_outbound(
        self, inbound_id: int, conversation_id: int, content: str, agent_id=None
    ) -> tuple[int, bool]:
        self.outbound.append(
            {"content": content, "agent_id": agent_id, "reply_to": inbound_id}
        )
        return 20, self.ya_respondido

    async def load_work(self, message_id: int) -> InboundWork | None:
        return InboundWork(
            message_id=message_id,
            conversation_id=1,
            contact_phone="+5215550001111",
            message_type="text",
            content="quiero un pedido",
        )

    async def recent_history(self, conversation_id, limit=10, exclude_message_id=0):
        self.marks.append(("history", conversation_id, limit, exclude_message_id))
        return self.history

    async def mark_outbound_sent(self, message_id, external_id):
        self.marks.append(("sent", message_id, external_id))

    async def mark_outbound_failed(self, message_id):
        self.marks.append(("send_failed", message_id))

    async def mark_inbound_processed(self, message_id, agent_id=None):
        self.marks.append(("processed", message_id, agent_id))

    async def mark_inbound_failed(self, message_id):
        self.marks.append(("failed", message_id))


class FakeAgentRepository:
    def __init__(self, agent: AgentConfig | None = AGENT) -> None:
        self._agent = agent

    async def get_active_by_name(self, name):
        return self._agent if self._agent and self._agent.name == name else None

    async def get_default_active(self):
        return self._agent


def build_pipeline(
    conversations=None,
    agent: AgentConfig | None = AGENT,
    messaging=None,
    llm=None,
):
    conversations = conversations or FakeConversationService()
    messaging = messaging or FakeMessagingProvider()
    llm = llm or FakeLLMProvider(canned_reply="¡Anotado tu pedido!")
    repo = FakeAgentRepository(agent)

    async def resolver(intent, confidence):
        found = await repo.get_default_active()
        return found.name if found else None

    pipeline = MessagePipeline(
        settings=Settings(_env_file=None),
        conversations=conversations,
        agents=repo,
        router=AgentRouter(RuleBasedClassifier(), resolver),
        llm=llm,
        messaging=messaging,
    )
    return pipeline, conversations, messaging, llm


def incoming(**overrides) -> IncomingMessage:
    base = {
        "external_id": "wamid.X1",
        "contact_phone": "+5215550001111",
        "type": "text",
        "content": "quiero un pedido",
    }
    return IncomingMessage(**{**base, **overrides})


async def test_flujo_feliz_envia_y_marca_procesado():
    pipeline, conversations, messaging, _ = build_pipeline()
    result = await pipeline.handle_inbound(incoming())

    assert result.status == "processed"
    assert result.agent_name == "ventas"
    assert messaging.sent == [{"to": "+5215550001111", "text": "¡Anotado tu pedido!"}]
    marcado_enviado = next(m for m in conversations.marks if m[0] == "sent")
    assert marcado_enviado[1] == 20
    assert marcado_enviado[2].startswith("fake-out-")
    # El agente queda registrado en el mensaje entrante y en el saliente.
    assert ("processed", 10, 1) in conversations.marks
    # La respuesta queda atada al entrante que la originó.
    assert conversations.outbound == [
        {"content": "¡Anotado tu pedido!", "agent_id": 1, "reply_to": 10}
    ]


async def test_duplicado_no_reprocesa_ni_envia():
    pipeline, conversations, messaging, _ = build_pipeline(
        conversations=FakeConversationService(already_processed=True, created=False)
    )
    result = await pipeline.handle_inbound(incoming())

    assert result.status == "duplicate"
    assert messaging.sent == []
    assert conversations.outbound == []


async def test_reclamo_perdido_es_duplicado_en_curso():
    # Otra ejecución concurrente ya reclamó el mensaje: no se responde dos veces.
    pipeline, conversations, messaging, _ = build_pipeline(
        conversations=FakeConversationService(created=False, claim_ok=False)
    )
    result = await pipeline.handle_inbound(incoming())

    assert result.status == "duplicate"
    assert messaging.sent == []


async def test_no_reenvia_si_ya_habia_respuesta_enviada():
    # Reintento del MISMO mensaje tras un fallo posterior al envío: el cliente
    # no recibe la respuesta dos veces.
    pipeline, conversations, messaging, _ = build_pipeline(
        conversations=FakeConversationService(ya_respondido=True)
    )
    result = await pipeline.handle_inbound(incoming())

    assert result.status == "already_replied"
    assert messaging.sent == []
    assert ("processed", 10, 1) in conversations.marks


async def test_historial_llega_al_modelo():
    history = [
        {"role": "user", "content": "hola"},
        {"role": "assistant", "content": "¿en qué te ayudo?"},
    ]
    pipeline, conversations, _, llm = build_pipeline(
        conversations=FakeConversationService(history=history)
    )
    await pipeline.handle_inbound(incoming())

    assert llm.calls[0]["system_prompt"] == "Vendes."
    assert llm.calls[0]["messages"] == [
        *history,
        {"role": "user", "content": "quiero un pedido"},
    ]
    assert ("history", 1, 10, 10) in conversations.marks


async def test_tipo_no_soportado_responde_mensaje_de_capacidad():
    pipeline, _, messaging, llm = build_pipeline()
    result = await pipeline.handle_inbound(incoming(type="audio", content=""))

    assert result.status == "processed"
    assert result.agent_name is None
    assert len(messaging.sent) == 1
    assert "solo puedo procesar mensajes de texto" in messaging.sent[0]["text"]
    assert llm.calls == []  # no se gasta una llamada al modelo


async def test_sin_agente_activo_no_envia_nada():
    pipeline, conversations, messaging, _ = build_pipeline(agent=None)
    result = await pipeline.handle_inbound(incoming())

    assert result.status == "no_agent"
    assert messaging.sent == []
    assert ("processed", 10, None) in conversations.marks


async def test_fallo_de_envio_marca_estados_y_relanza():
    class MessagingRoto:
        async def send_text(self, to, text):
            raise RuntimeError("YCloud caído")

    pipeline, conversations, _, _ = build_pipeline(messaging=MessagingRoto())
    with pytest.raises(RuntimeError):
        await pipeline.handle_inbound(incoming())

    assert ("send_failed", 20) in conversations.marks
    assert ("failed", 10) in conversations.marks  # reintento posible


async def test_fallo_del_modelo_marca_entrante_fallido():
    class LLMRoto:
        async def complete(self, **kwargs):
            raise RuntimeError("OpenAI caído")

    pipeline, conversations, messaging, _ = build_pipeline(llm=LLMRoto())
    with pytest.raises(RuntimeError):
        await pipeline.handle_inbound(incoming())

    assert ("failed", 10) in conversations.marks
    assert messaging.sent == []


async def test_process_message_rehidrata_desde_la_persistencia():
    # Camino del worker: solo recibe el id.
    pipeline, conversations, messaging, _ = build_pipeline()
    result = await pipeline.process_message(10)

    assert result.status == "processed"
    assert messaging.sent[0]["to"] == "+5215550001111"


async def test_espera_su_turno_si_hay_anteriores_sin_terminar():
    pipeline, conversations, messaging, llm = build_pipeline(
        conversations=FakeConversationService(anteriores_pendientes=True)
    )
    result = await pipeline.handle_inbound(incoming())

    assert result.status == "esperando_turno"
    assert messaging.sent == []
    assert llm.calls == [], "no se gasta una llamada al modelo mientras espera"
    # No se reclamó: el mensaje sigue disponible para su turno.
    assert not any(m[0] == "claim" for m in conversations.marks)


async def test_process_message_con_id_inexistente():
    class SinMensaje(FakeConversationService):
        async def load_work(self, message_id):
            return None

    pipeline, _, messaging, _ = build_pipeline(conversations=SinMensaje())
    result = await pipeline.process_message(999)

    assert result.status == "not_found"
    assert messaging.sent == []
