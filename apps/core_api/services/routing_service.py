"""Resolución de a quién le toca atender un mensaje.

Traduce (intención, confianza) en una de tres salidas: un agente concreto, el
paso a una persona, o nadie. Vive aparte del grafo porque es la regla de negocio
que la gente de ventas quiere poder cambiar sin desplegar.
"""

import logging
from dataclasses import dataclass
from enum import Enum

from asgiref.sync import sync_to_async

from apps.shared.models import RoutingRule

logger = logging.getLogger(__name__)

COMODIN = "*"


class Decision(str, Enum):
    AGENTE = "agente"
    PERSONA = "persona"
    SIN_DESTINO = "sin_destino"


@dataclass(frozen=True)
class Ruta:
    decision: Decision
    agent_name: str | None = None
    motivo: str = ""

    @property
    def a_persona(self) -> bool:
        return self.decision is Decision.PERSONA


class RoutingService:
    @staticmethod
    def _regla_sync(intent: str) -> tuple[str, float] | None:
        """Regla activa de la intención, o la del comodín si no hay específica."""
        for buscada in (intent, COMODIN):
            fila = (
                RoutingRule.objects.filter(intent=buscada, is_active=True)
                .select_related("agent")
                .filter(agent__is_active=True)
                .values_list("agent__name", "min_confidence")
                .first()
            )
            if fila:
                return fila
        return None

    async def resolver(self, intent: str, confidence: float) -> Ruta:
        regla = await sync_to_async(self._regla_sync)(intent)

        if regla is None:
            # Ninguna regla cubre esta intención. Pasar a una persona es la
            # única salida honesta: responder con un agente elegido al azar
            # sería peor que decir que no se sabe.
            logger.info("Sin regla para la intención %r: pasa a una persona", intent)
            return Ruta(
                decision=Decision.PERSONA,
                motivo=f"sin regla para la intención {intent}",
            )

        agent_name, minima = regla
        if confidence < minima:
            logger.info(
                "Confianza %.2f por debajo del mínimo %.2f para %r: pasa a una persona",
                confidence,
                minima,
                intent,
            )
            return Ruta(
                decision=Decision.PERSONA,
                motivo=(
                    f"confianza {confidence:.2f} por debajo del mínimo "
                    f"{minima:.2f} para {intent}"
                ),
            )

        return Ruta(decision=Decision.AGENTE, agent_name=agent_name)
