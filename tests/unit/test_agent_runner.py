from apps.core_api.graphs.agent_graph_factory import run_agent
from apps.core_api.services.agent_repository import AgentConfig
from apps.core_api.services.llm_service import FakeLLMProvider

CONFIG = AgentConfig(
    id=1,
    name="ventas",
    system_prompt="Eres el asistente de ventas.",
    llm_model="gpt-4.1",
    temperature=0.2,
)


async def test_arma_mensajes_con_historial_y_parametros_del_agente():
    llm = FakeLLMProvider(canned_reply="Claro, ¿qué producto necesitas?")
    history = [
        {"role": "user", "content": "hola"},
        {"role": "assistant", "content": "¡Hola! ¿En qué te ayudo?"},
    ]
    reply = await run_agent(CONFIG, "quiero 2 cajas", history, llm)

    assert reply == "Claro, ¿qué producto necesitas?"
    call = llm.calls[0]
    assert call["system_prompt"] == "Eres el asistente de ventas."
    assert call["model"] == "gpt-4.1"
    assert call["temperature"] == 0.2
    assert call["messages"] == [*history, {"role": "user", "content": "quiero 2 cajas"}]
