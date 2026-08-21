"""Enrutamiento por reglas: quién atiende cada intención y cuándo pasa a una
persona.

Es la pieza central del Determinador multiagente. Lo que se decide aquí es
también dónde el sistema admite que no sabe, que es la diferencia entre un bot
útil y uno que contesta cualquier cosa con seguridad.
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
from apps.core_api.services.routing_service import Decision, RoutingService
from apps.core_api.services.ycloud_service import FakeMessagingProvider
from apps.shared.enums import ConversationStatus
from apps.shared.models import Agent, Conversation, RoutingRule, TraceEvent

pytestmark = pytest.mark.django_db(transaction=True)


@pytest.fixture
def ventas():
    return Agent.objects.create(name="ventas", system_prompt="Vendes.", is_active=True)


@pytest.fixture
def soporte():
    return Agent.objects.create(name="soporte", system_prompt="Ayudas.", is_active=True)


@pytest.fixture
def entorno():
    messaging = FakeMessagingProvider()
    llm = FakeLLMProvider()
    agents = AgentRepository()
    routing = RoutingService()

    pipeline = MessagePipeline(
        settings=Settings(_env_file=None),
        conversations=ConversationService(),
        agents=agents,
        router=AgentRouter(RuleBasedClassifier(), routing.resolver),
        llm=llm,
        messaging=messaging,
    )
    return pipeline, messaging, llm


def entrante(texto: str, external_id: str = "wamid.R1") -> IncomingMessage:
    return IncomingMessage(
        external_id=external_id,
        contact_phone="+5215550003030",
        type="text",
        content=texto,
    )


# ---------------------------------------------------------------- resolución


def test_cada_intencion_va_a_su_agente(ventas, soporte):
    RoutingRule.objects.create(intent="pedido", agent=ventas, min_confidence=0.5)
    RoutingRule.objects.create(intent="soporte", agent=soporte, min_confidence=0.5)

    ruta_pedido = async_to_sync(RoutingService().resolver)("pedido", 0.8)
    ruta_soporte = async_to_sync(RoutingService().resolver)("soporte", 0.8)

    assert ruta_pedido.agent_name == "ventas"
    assert ruta_soporte.agent_name == "soporte"


def test_por_debajo_del_umbral_va_a_una_persona(ventas):
    RoutingRule.objects.create(intent="pedido", agent=ventas, min_confidence=0.8)

    ruta = async_to_sync(RoutingService().resolver)("pedido", 0.7)

    assert ruta.decision is Decision.PERSONA
    assert "0.70" in ruta.motivo and "0.80" in ruta.motivo, (
        f"el motivo no dice qué confianza faltó: {ruta.motivo}"
    )


def test_sin_regla_para_la_intencion_va_a_una_persona(ventas):
    """Responder con un agente cualquiera sería peor que admitir que no se sabe."""
    ruta = async_to_sync(RoutingService().resolver)("estado_pedido", 0.99)

    assert ruta.decision is Decision.PERSONA
    assert "sin regla" in ruta.motivo


def test_el_comodin_cubre_las_intenciones_sin_regla_propia(ventas):
    RoutingRule.objects.create(intent="*", agent=ventas, min_confidence=0.6)

    ruta = async_to_sync(RoutingService().resolver)("estado_pedido", 0.8)
    assert ruta.agent_name == "ventas"

    # El umbral del comodín también se respeta.
    baja = async_to_sync(RoutingService().resolver)("estado_pedido", 0.3)
    assert baja.decision is Decision.PERSONA


def test_la_regla_especifica_gana_al_comodin(ventas, soporte):
    RoutingRule.objects.create(intent="*", agent=ventas, min_confidence=0.5)
    RoutingRule.objects.create(intent="soporte", agent=soporte, min_confidence=0.5)

    ruta = async_to_sync(RoutingService().resolver)("soporte", 0.9)
    assert ruta.agent_name == "soporte"


def test_una_regla_desactivada_no_enruta(ventas):
    RoutingRule.objects.create(
        intent="pedido", agent=ventas, min_confidence=0.5, is_active=False
    )

    ruta = async_to_sync(RoutingService().resolver)("pedido", 0.9)
    assert ruta.decision is Decision.PERSONA


def test_una_regla_hacia_un_agente_apagado_no_enruta(ventas):
    """Apagar el agente tiene que bastar: no hay que acordarse de desactivar
    también todas las reglas que apuntan a él."""
    RoutingRule.objects.create(intent="pedido", agent=ventas, min_confidence=0.5)
    Agent.objects.filter(id=ventas.id).update(is_active=False)

    ruta = async_to_sync(RoutingService().resolver)("pedido", 0.9)
    assert ruta.decision is Decision.PERSONA


def test_no_puede_haber_dos_reglas_activas_para_la_misma_intencion(ventas, soporte):
    """Con dos, el agente elegido dependería del orden en que la base devuelva
    las filas."""
    from django.db import IntegrityError

    RoutingRule.objects.create(intent="pedido", agent=ventas, min_confidence=0.5)
    with pytest.raises(IntegrityError):
        RoutingRule.objects.create(intent="pedido", agent=soporte, min_confidence=0.5)


# ---------------------------------------------------------------- pipeline


def test_el_cliente_recibe_aviso_cuando_pasa_a_una_persona(entorno, ventas):
    """Dejarlo esperando en silencio mientras alguien llega es lo peor."""
    pipeline, messaging, _ = entorno
    RoutingRule.objects.create(intent="pedido", agent=ventas, min_confidence=0.99)

    resultado = async_to_sync(pipeline.handle_inbound)(entrante("quiero un pedido"))

    assert resultado.status == "processed"
    assert len(messaging.sent) == 1
    assert "persona" in messaging.sent[0]["text"].lower()

    conversacion = Conversation.objects.get()
    assert conversacion.status == ConversationStatus.ESCALATED


def test_el_paso_a_una_persona_queda_registrado_con_su_motivo(entorno, ventas):
    pipeline, _, _ = entorno
    RoutingRule.objects.create(intent="pedido", agent=ventas, min_confidence=0.99)

    async_to_sync(pipeline.handle_inbound)(entrante("quiero un pedido"))

    evento = TraceEvent.objects.get(event_type="escalada_a_persona")
    assert "confianza" in evento.payload["motivo"]


def test_tras_pasar_a_una_persona_el_bot_ya_no_responde(entorno, ventas):
    pipeline, messaging, _ = entorno
    RoutingRule.objects.create(intent="pedido", agent=ventas, min_confidence=0.99)

    async_to_sync(pipeline.handle_inbound)(entrante("quiero un pedido", "wamid.X1"))
    assert len(messaging.sent) == 1

    siguiente = async_to_sync(pipeline.handle_inbound)(
        entrante("y cuanto cuesta?", "wamid.X2")
    )

    assert siguiente.status == "atendida_por_persona"
    assert len(messaging.sent) == 1, "el bot siguió respondiendo tras el traspaso"


def test_pedir_una_persona_pasa_a_una_persona(entorno, ventas):
    """El caso que no puede fallar: el cliente lo pide explícitamente."""
    pipeline, messaging, _ = entorno
    RoutingRule.objects.create(
        intent="hablar_con_humano", agent=ventas, min_confidence=0.99
    )

    async_to_sync(pipeline.handle_inbound)(entrante("pasame con un asesor"))

    assert Conversation.objects.get().status == ConversationStatus.ESCALATED
    assert "persona" in messaging.sent[0]["text"].lower()


def test_con_confianza_suficiente_responde_el_agente(entorno, ventas):
    pipeline, messaging, llm = entorno
    RoutingRule.objects.create(intent="pedido", agent=ventas, min_confidence=0.5)

    resultado = async_to_sync(pipeline.handle_inbound)(entrante("quiero un pedido"))

    assert resultado.agent_name == "ventas"
    assert Conversation.objects.get().status == ConversationStatus.ACTIVE
    assert len(llm.calls) == 1
    assert len(messaging.sent) == 1


def test_cambiar_una_regla_en_el_panel_tiene_efecto_al_instante(entorno, ventas, soporte):
    """No hay caché: la regla se lee de la base en cada mensaje, así que un
    cambio desde el panel se aplica sin reiniciar nada."""
    pipeline, _, llm = entorno
    RoutingRule.objects.create(intent="pedido", agent=ventas, min_confidence=0.5)

    primero = async_to_sync(pipeline.handle_inbound)(
        entrante("quiero un pedido", "wamid.Y1")
    )
    assert primero.agent_name == "ventas"

    # Alguien reasigna la intención a otro agente desde el panel.
    RoutingRule.objects.filter(intent="pedido").update(agent=soporte)

    segundo = async_to_sync(pipeline.handle_inbound)(
        entrante("quiero otro pedido", "wamid.Y2")
    )
    assert segundo.agent_name == "soporte"
    assert llm.calls[-1]["system_prompt"] == "Ayudas.", (
        "el prompt sigue siendo el del agente anterior"
    )
