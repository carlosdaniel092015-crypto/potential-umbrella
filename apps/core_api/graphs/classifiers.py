"""Patrón Strategy del Determinador (§1.1): la clasificación de intención es
intercambiable — reglas simples (Fase 1) → clasificador con LLM (Fase 2) — sin
reescribir el router."""

import unicodedata
from abc import ABC, abstractmethod

# Catálogo de intenciones del negocio. Es el mismo espacio de etiquetas del
# dataset de evaluación (tests/fixtures/intenciones.json): si el clasificador no
# puede producir una etiqueta que el dataset contiene, esa clase es
# estructuralmente inalcanzable y su recall es cero por construcción.
INTENCIONES = (
    "pedido",
    "consulta_precio",
    "consulta_stock",
    "estado_pedido",
    "soporte",
    "hablar_con_humano",
    "saludo",
    "fuera_de_dominio",
)

# Etiqueta de "no reconozco", fuera del catálogo: dispara el camino de baja
# confianza (contención o escalado), no es una intención de negocio.
SIN_RECONOCER = "general"


class IntentClassifier(ABC):
    @abstractmethod
    def classify(self, content: str) -> tuple[str, float]:
        """Devuelve (intent, confidence en [0, 1])."""


def _normalize(text: str) -> str:
    text = unicodedata.normalize("NFKD", text.lower())
    return "".join(c for c in text if not unicodedata.combining(c))


class RuleBasedClassifier(IntentClassifier):
    """Fase 1 y red de seguridad cuando el LLM falla.

    El orden del diccionario ES la precedencia: gana la primera intención con
    coincidencia. Está ordenado por lo que más cuesta equivocar:

    1. Intentos de manipulación del prompt — seguridad antes que negocio.
    2. Petición explícita de una persona — nunca debe quedarse en el bot.
    3. Estado de un pedido antes que pedido nuevo («¿y mi pedido?» no es pedir).
    4. Reclamos antes que pedido, para que una queja no se lea como venta.
    """

    KEYWORDS: dict[str, tuple[str, ...]] = {
        "fuera_de_dominio": (
            "ignora tus instrucciones",
            "ignora las instrucciones",
            "ignora tus reglas",
            "olvida tus instrucciones",
            "sin restricciones",
            "tu prompt",
            "system prompt",
            "numero equivocado",
            "te equivocaste de numero",
            "reenvia este mensaje",
            "cadena de oracion",
        ),
        "hablar_con_humano": (
            "hablar con una persona",
            "hablar con alguien",
            "con un humano",
            "una persona real",
            "asesor",
            "supervisor",
            "encargado",
            "gerente",
            "comunicame con",
            "pasame con",
            "no eres humano",
            "eres un robot",
            "eres un bot",
        ),
        "estado_pedido": (
            "mi pedido",
            "el pedido que",
            "donde va",
            "donde viene",
            "ya salio",
            "cuando llega",
            "cuando me llega",
            "ya lo mandaron",
            "ya lo enviaron",
            "estatus",
            "seguimiento",
            "factura",
            "facturar",
            "guia",
            "rastreo",
        ),
        "soporte": (
            "queja",
            "reclamo",
            "problema",
            "devolucion",
            "devolver",
            "danado",
            "dañado",
            "abollad",
            "roto",
            "caducad",
            "me faltaron",
            "falto",
            "equivocad",
            "mal estado",
        ),
        "pedido": (
            "pedido",
            "pedir",
            "comprar",
            "orden",
            "ordenar",
            "cotizar",
            "cotizacion",
            "mandame",
            "manda me",
            "envienme",
            "surtir",
            "surteme",
            "apuntame",
            "anotame",
            "agrega",
            "agregame",
            "quitale",
            "cancelame",
            "duplicame",
            "levantar",
        ),
        "consulta_precio": (
            "precio",
            "cuanto cuesta",
            "cuanto sale",
            "cuanto vale",
            "cuanto me sale",
            "costo",
            "tarifa",
            "descuento",
            "lista de precios",
        ),
        "consulta_stock": (
            "stock",
            "existencia",
            "disponible",
            "disponibilidad",
            "hay ",
            "queda",
            "agotado",
            "en almacen",
        ),
        "saludo": (
            "hola",
            "buenas",
            "buen dia",
            "buenos dias",
            "buenas tardes",
            "buenas noches",
            "gracias",
            "hasta luego",
        ),
    }

    def classify(self, content: str) -> tuple[str, float]:
        normalized = _normalize(content)
        for intent, keywords in self.KEYWORDS.items():
            if any(kw in normalized for kw in keywords):
                # Confianza fija y modesta: son reglas, no una estimación real.
                # El clasificador con LLM entrega una confianza calibrada.
                return intent, 0.75
        return SIN_RECONOCER, 0.2


class LLMClassifier(IntentClassifier):
    """Fase 2: clasificación con LLM sobre el catálogo de intenciones."""

    def classify(self, content: str) -> tuple[str, float]:
        raise NotImplementedError("Clasificador LLM: Fase 2 del roadmap")
