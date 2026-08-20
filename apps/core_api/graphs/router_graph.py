"""Determinador Inteligente (ADR-04, LangGraph).

Fase 1: el grafo mantiene la interfaz completa (classify_intent → route_to_agent)
pero el resolver es trivial — siempre el agente único activo. Fase 2 sustituye
clasificador y resolver (reglas en Configuration, umbral de confianza, fallback
a humano) sin cambiar RouterState ni el contrato del pipeline.

Diseño para testabilidad (hallazgo QA): nada se compila a nivel de módulo; el
grafo se construye vía build_router_graph() con dependencias inyectadas. Los
nodos que tocan DB son async y usan el resolver async inyectado — nunca ORM
síncrono en el event loop (ADR-06).
"""

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import TypedDict

from langgraph.graph import END, START, StateGraph

from apps.core_api.graphs.classifiers import IntentClassifier

AgentResolver = Callable[[str, float], Awaitable[str | None]]


class RouterState(TypedDict, total=False):
    contact_id: str
    channel: str
    content: str
    intent: str
    confidence: float
    agent_name: str | None


@dataclass
class RouteResult:
    agent_name: str | None
    intent: str
    confidence: float


def build_router_graph(classifier: IntentClassifier, resolve_agent: AgentResolver):
    def classify_intent(state: RouterState) -> RouterState:
        intent, confidence = classifier.classify(state.get("content", ""))
        return {"intent": intent, "confidence": confidence}

    async def route_to_agent(state: RouterState) -> RouterState:
        agent_name = await resolve_agent(state["intent"], state["confidence"])
        return {"agent_name": agent_name}

    builder = StateGraph(RouterState)
    builder.add_node("classify_intent", classify_intent)
    builder.add_node("route_to_agent", route_to_agent)
    builder.add_edge(START, "classify_intent")
    builder.add_edge("classify_intent", "route_to_agent")
    builder.add_edge("route_to_agent", END)
    return builder.compile()


class AgentRouter:
    """Fachada del grafo para el pipeline."""

    def __init__(self, classifier: IntentClassifier, resolve_agent: AgentResolver) -> None:
        self._graph = build_router_graph(classifier, resolve_agent)

    async def route(self, contact_id: str, channel: str, content: str) -> RouteResult:
        result = await self._graph.ainvoke(
            {"contact_id": contact_id, "channel": channel, "content": content}
        )
        return RouteResult(
            agent_name=result.get("agent_name"),
            intent=result.get("intent", "general"),
            confidence=result.get("confidence", 0.0),
        )
