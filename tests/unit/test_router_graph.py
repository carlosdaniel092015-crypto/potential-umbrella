from apps.core_api.graphs.classifiers import RuleBasedClassifier
from apps.core_api.graphs.router_graph import AgentRouter
from apps.core_api.services.routing_service import Decision, Ruta


async def test_router_devuelve_el_agente_que_decide_el_resolver():
    async def resolver(intent: str, confidence: float) -> Ruta:
        assert intent == "pedido"
        assert confidence >= 0.7
        return Ruta(decision=Decision.AGENTE, agent_name="ventas")

    router = AgentRouter(RuleBasedClassifier(), resolver)
    result = await router.route("+521555", "whatsapp", "quiero hacer un pedido")

    assert result.agent_name == "ventas"
    assert result.intent == "pedido"
    assert result.a_persona is False


async def test_el_router_propaga_el_paso_a_una_persona_con_su_motivo():
    async def resolver(intent: str, confidence: float) -> Ruta:
        return Ruta(decision=Decision.PERSONA, motivo="confianza insuficiente")

    router = AgentRouter(RuleBasedClassifier(), resolver)
    result = await router.route("+521555", "whatsapp", "xyzzy")

    assert result.a_persona is True
    assert result.motivo == "confianza insuficiente"
    assert result.agent_name is None


async def test_sin_destino_no_es_lo_mismo_que_pasar_a_una_persona():
    """Sin destino significa que no hay ningún agente configurado; pasar a una
    persona es una decisión deliberada. El pipeline los trata distinto."""

    async def resolver(intent: str, confidence: float) -> Ruta:
        return Ruta(decision=Decision.SIN_DESTINO)

    router = AgentRouter(RuleBasedClassifier(), resolver)
    result = await router.route("+521555", "whatsapp", "hola")

    assert result.agent_name is None
    assert result.a_persona is False


async def test_la_intencion_y_la_confianza_llegan_al_resolver():
    recibido = {}

    async def resolver(intent: str, confidence: float) -> Ruta:
        recibido["intent"] = intent
        recibido["confidence"] = confidence
        return Ruta(decision=Decision.AGENTE, agent_name="soporte")

    router = AgentRouter(RuleBasedClassifier(), resolver)
    await router.route("+521555", "whatsapp", "tengo un reclamo con mi entrega")

    assert recibido["intent"] == "soporte"
    assert recibido["confidence"] > 0
