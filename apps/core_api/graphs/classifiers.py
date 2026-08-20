"""Patrón Strategy del Determinador (§1.1): la clasificación de intención es
intercambiable — reglas simples (Fase 1) → clasificador ligero → LLM (Fase 2)
— sin reescribir el router."""

import unicodedata
from abc import ABC, abstractmethod


class IntentClassifier(ABC):
    @abstractmethod
    def classify(self, content: str) -> tuple[str, float]:
        """Devuelve (intent, confidence en [0, 1])."""


def _normalize(text: str) -> str:
    text = unicodedata.normalize("NFKD", text.lower())
    return "".join(c for c in text if not unicodedata.combining(c))


class RuleBasedClassifier(IntentClassifier):
    """Fase 1: keywords en español. Suficiente para un agente único; la matriz
    de intenciones vs dataset etiquetado (QA §3.2) llega con el LLM en Fase 2."""

    KEYWORDS: dict[str, tuple[str, ...]] = {
        "pedido": ("pedido", "pedir", "comprar", "orden", "ordenar", "cotizar", "cotizacion"),
        "consulta_precio": ("precio", "cuanto cuesta", "cuanto vale", "costo", "tarifa"),
        "consulta_stock": ("stock", "disponible", "disponibilidad", "hay ", "queda"),
        "soporte": ("queja", "reclamo", "problema", "error", "devolucion", "ayuda"),
        "saludo": ("hola", "buenas", "buenos dias", "buenas tardes", "buenas noches"),
    }

    def classify(self, content: str) -> tuple[str, float]:
        normalized = _normalize(content)
        for intent, keywords in self.KEYWORDS.items():
            if any(kw in normalized for kw in keywords):
                return intent, 0.9
        return "general", 0.3


class LLMClassifier(IntentClassifier):
    """Fase 2: clasificación con LLM sobre catálogo de intenciones configurable."""

    def classify(self, content: str) -> tuple[str, float]:
        raise NotImplementedError("Clasificador LLM: Fase 2 del roadmap")
