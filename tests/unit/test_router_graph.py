from apps.core_api.graphs.classifiers import RuleBasedClassifier
from apps.core_api.graphs.router_graph import AgentRouter


async def test_router_devuelve_agente_del_resolver():
    async def resolver(intent: str, confidence: float) -> str | None:
        assert intent == "pedido"
        assert confidence >= 0.7
        return "ventas"

    router = AgentRouter(RuleBasedClassifier(), resolver)
    result = await router.route("+521555", "whatsapp", "quiero hacer un pedido")
    assert result.agent_name == "ventas"
    assert result.intent == "pedido"


async def test_router_propaga_none_si_no_hay_agente():
    async def resolver(intent: str, confidence: float) -> str | None:
        return None

    router = AgentRouter(RuleBasedClassifier(), resolver)
    result = await router.route("+521555", "whatsapp", "hola")
    assert result.agent_name is None
