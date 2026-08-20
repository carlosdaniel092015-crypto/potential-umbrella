"""Ejecución del agente conversacional.

Fase 1: un solo turno LLM (system_prompt del Agent + historial + mensaje).
Fase 2 convierte esto en grafos LangGraph por agente con tools acotadas
(allowed_tools) y validación post-generación antes de acciones críticas (§5.3).
"""

from apps.core_api.services.agent_repository import AgentConfig
from apps.core_api.services.providers import LLMProvider


async def run_agent(
    config: AgentConfig,
    user_message: str,
    history: list[dict],
    llm: LLMProvider,
) -> str:
    messages = [*history, {"role": "user", "content": user_message}]
    return await llm.complete(
        system_prompt=config.system_prompt,
        messages=messages,
        model=config.llm_model,
        temperature=config.temperature,
    )
