"""Determinador Inteligente (ADR-04, LangGraph).

Clasifica la intención del mensaje y decide quién lo atiende. La clasificación es
intercambiable (Strategy): reglas en Fase 1, LLM en Fase 2. La decisión de a quién
va la atiende `RoutingService`, con las reglas que se editan en el panel.

Diseño para testabilidad: nada se compila a nivel de módulo; el grafo se
construye vía build_router_graph() con las dependencias inyectadas. Los nodos que
tocan la base son async — nunca ORM síncrono en el event loop (ADR-06).
"""

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import TypedDict

from langgraph.graph import END, START, StateGraph

from apps.core_api.graphs.classifiers import IntentClassifier
from apps.core_api.services.routing_service import Decision, Ruta

AgentResolver = Callable[[str, float], Awaitable[Ruta]]


class RouterState(TypedDict, total=False):
    contact_id: str
    channel: str
    content: str
    intent: str
    confidence: float
    ruta: Ruta


@dataclass
class RouteResult:
    agent_name: str | None
    intent: str
    confidence: float
    a_persona: bool = False
    motivo: str = ""


def build_router_graph(classifier: IntentClassifier, resolve_agent: AgentResolver):
    def classify_intent(state: RouterState) -> RouterState:
        intent, confidence = classifier.classify(state.get("content", ""))
        return {"intent": intent, "confidence": confidence}

    async def route_to_agent(state: RouterState) -> RouterState:
        ruta = await resolve_agent(state["intent"], state["confidence"])
        return {"ruta": ruta}

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
        ruta: Ruta | None = result.get("ruta")
        if ruta is None:  # pragma: no cover - el grafo siempre pasa por el resolver
            ruta = Ruta(decision=Decision.SIN_DESTINO)
        return RouteResult(
            agent_name=ruta.agent_name,
            intent=result.get("intent", "general"),
            confidence=result.get("confidence", 0.0),
            a_persona=ruta.a_persona,
            motivo=ruta.motivo,
        )
