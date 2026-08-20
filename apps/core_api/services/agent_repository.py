from dataclasses import dataclass

from asgiref.sync import sync_to_async

from apps.shared.models import Agent


@dataclass
class AgentConfig:
    id: int
    name: str
    system_prompt: str
    llm_model: str
    temperature: float


def _to_config(agent: Agent) -> AgentConfig:
    return AgentConfig(
        id=agent.id,
        name=agent.name,
        system_prompt=agent.system_prompt,
        llm_model=agent.llm_model,
        temperature=agent.temperature,
    )


class AgentRepository:
    @staticmethod
    def _get_by_name_sync(name: str) -> AgentConfig | None:
        agent = Agent.objects.filter(name=name, is_active=True).first()
        return _to_config(agent) if agent else None

    async def get_active_by_name(self, name: str) -> AgentConfig | None:
        return await sync_to_async(self._get_by_name_sync)(name)

    @staticmethod
    def _get_default_sync() -> AgentConfig | None:
        agent = Agent.objects.filter(is_active=True).order_by("id").first()
        return _to_config(agent) if agent else None

    async def get_default_active(self) -> AgentConfig | None:
        """Fase 1: agente único — el primer agente activo es el default."""
        return await sync_to_async(self._get_default_sync)()
