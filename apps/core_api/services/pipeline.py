"""Pipeline de un mensaje entrante (flujo completo que el documento omitía):

  registrar (idempotente) → reclamar → rutear → historial → generar respuesta →
  persistir saliente → enviar por el proveedor → marcar procesado.

Modo inline (dev/test): `handle_inbound` registra y procesa en el acto.
Modo celery (producción): el webhook llama a `register` y encola solo el id del
mensaje; el worker llama a `process_message`, que rehidrata desde Postgres. Así
no viaja PII en el broker y la deduplicación ocurre en el borde.
"""

import logging

from apps.core_api.config import Settings
from apps.core_api.graphs.agent_graph_factory import run_agent
from apps.core_api.graphs.router_graph import AgentRouter
from apps.core_api.schemas.messages import IncomingMessage, PipelineResult
from apps.core_api.services.agent_repository import AgentRepository
from apps.core_api.services.conversation_service import (
    ConversationService,
    InboundRegistration,
    InboundWork,
)
from apps.core_api.services.providers import LLMProvider, MessagingProvider
from apps.core_api.services.ycloud_service import mask_phone
from apps.shared.enums import ConversationStatus

logger = logging.getLogger(__name__)


class MessagePipeline:
    def __init__(
        self,
        settings: Settings,
        conversations: ConversationService,
        agents: AgentRepository,
        router: AgentRouter,
        llm: LLMProvider,
        messaging: MessagingProvider,
    ) -> None:
        self._settings = settings
        self._conversations = conversations
        self._agents = agents
        self._router = router
        self._llm = llm
        self._messaging = messaging

    async def register(self, incoming: IncomingMessage) -> InboundRegistration:
        return await self._conversations.register_inbound(incoming)

    async def handle_inbound(self, incoming: IncomingMessage) -> PipelineResult:
        registration = await self.register(incoming)
        if registration.already_processed:
            self._log_duplicate(incoming.contact_phone, incoming.external_id)
            return PipelineResult(
                status="duplicate", conversation_id=registration.conversation_id
            )
        # Se rehidrata desde la base para leer el estado de la conversación con
        # el mismo criterio que el worker, en vez de armar el trabajo a mano.
        work = await self._conversations.load_work(registration.message_id)
        if work is None:  # pragma: no cover - acabamos de registrarlo
            return PipelineResult(status="not_found")
        return await self._process(work)

    async def process_message(
        self, message_id: int, forzar_turno: bool = False
    ) -> PipelineResult:
        """Entrada del worker: el mensaje ya está persistido.

        `forzar_turno` salta la espera. El worker lo usa cuando agotó su
        presupuesto de espera: seguir sin el predecesor es peor que dejar el
        mensaje sin responder para siempre.
        """
        work = await self._conversations.load_work(message_id)
        if work is None:
            logger.error("Mensaje %s no encontrado: nada que procesar", message_id)
            return PipelineResult(status="not_found")
        return await self._process(work, forzar_turno=forzar_turno)

    async def _process(
        self, work: InboundWork, forzar_turno: bool = False
    ) -> PipelineResult:
        # Turno por conversación: si un mensaje anterior del mismo cliente
        # todavía no terminó, este espera. Sin esto, dos respuestas de la misma
        # ráfaga se generan sin verse y llegan descoordinadas.
        if not forzar_turno and await self._conversations.tiene_anteriores_pendientes(
            work.conversation_id,
            work.message_id,
            margen_minutos=self._settings.orden_margen_minutos,
        ):
            logger.info(
                "Mensaje %s espera su turno: hay anteriores sin terminar", work.message_id
            )
            return PipelineResult(
                status="esperando_turno", conversation_id=work.conversation_id
            )

        # Si una persona tomó la conversación, el bot se calla. El mensaje se
        # marca como atendido para que no bloquee el turno de los siguientes,
        # pero queda guardado: callarse no es ignorar, y el vendedor tiene que
        # verlo en el panel.
        if work.conversation_status == ConversationStatus.ESCALATED:
            logger.info(
                "Conversación %s atendida por una persona: no se responde al mensaje %s",
                work.conversation_id,
                work.message_id,
            )
            await self._conversations.claim_for_processing(work.message_id)
            await self._conversations.mark_inbound_processed(work.message_id)
            return PipelineResult(
                status="atendida_por_persona", conversation_id=work.conversation_id
            )

        # Reclamo atómico: cierra la ventana en la que dos ejecuciones
        # concurrentes del mismo mensaje respondían dos veces al cliente.
        if not await self._conversations.claim_for_processing(work.message_id):
            self._log_duplicate(work.contact_phone, str(work.message_id))
            return PipelineResult(status="duplicate", conversation_id=work.conversation_id)

        try:
            # Si un intento anterior ya generó la respuesta pero no logró
            # enviarla, se reutiliza ese texto en lugar de pedir otro al modelo.
            pendiente = await self._conversations.texto_pendiente(work.message_id)
            if pendiente is not None:
                logger.info(
                    "Reintento del mensaje %s: se reenvía el texto ya generado",
                    work.message_id,
                )
                reply, agent_config = pendiente, await self._agente_del_turno(work)
            else:
                reply, agent_config = await self._compose_reply(work)
        except Exception:
            await self._conversations.mark_inbound_failed(work.message_id)
            raise

        if reply is None:
            logger.warning("Sin agente activo: mensaje %s sin respuesta", work.message_id)
            await self._conversations.mark_inbound_processed(work.message_id)
            return PipelineResult(status="no_agent", conversation_id=work.conversation_id)

        agent_id = agent_config.id if agent_config else None
        # El reclamo va atado a ESTE entrante: es lo que distingue un reintento
        # del mismo mensaje (no se reenvía) de otro mensaje de la misma ráfaga
        # (sí se responde).
        outbound_id, ya_enviado = await self._conversations.claim_outbound(
            work.message_id, work.conversation_id, reply, agent_id=agent_id
        )
        if ya_enviado:
            logger.warning(
                "El mensaje %s ya tenía respuesta enviada: no se reenvía", work.message_id
            )
            await self._conversations.mark_inbound_processed(work.message_id, agent_id=agent_id)
            return PipelineResult(
                status="already_replied", conversation_id=work.conversation_id
            )
        try:
            external_id = await self._messaging.send_text(work.contact_phone, reply)
        except Exception:
            await self._conversations.mark_outbound_failed(outbound_id)
            await self._conversations.mark_inbound_failed(work.message_id)
            raise

        # El envío ya ocurrió. Un fallo al persistir el estado no puede marcar
        # SEND_FAILED (sería falso) ni propagarse: un 500 haría que YCloud
        # reintentara un mensaje ya entregado. Se registra para alertar — el
        # mensaje queda en PROCESSING y requiere revisión manual.
        try:
            await self._conversations.mark_outbound_sent(outbound_id, external_id)
            await self._conversations.mark_inbound_processed(work.message_id, agent_id=agent_id)
        except Exception:
            logger.critical(
                "Respuesta ENVIADA al cliente pero no se pudo persistir el estado "
                "(entrante=%s, saliente=%s): requiere conciliación manual",
                work.message_id,
                outbound_id,
                exc_info=True,
            )

        return PipelineResult(
            status="processed",
            agent_name=agent_config.name if agent_config else None,
            reply=reply,
            conversation_id=work.conversation_id,
        )

    async def _agente_del_turno(self, work: InboundWork):
        """Agente que corresponde a este mensaje, sin volver a llamar al modelo.
        Se usa al reenviar un texto ya generado."""
        if work.message_type != "text":
            return None
        route = await self._router.route(
            contact_id=work.contact_phone, channel="whatsapp", content=work.content
        )
        if not route.agent_name:
            return None
        return await self._agents.get_active_by_name(route.agent_name)

    async def _compose_reply(self, work: InboundWork):
        if work.message_type != "text":
            # Nunca silencio ante tipos no soportados (decisión D-07).
            return self._settings.unsupported_type_reply, None

        route = await self._router.route(
            contact_id=work.contact_phone,
            channel="whatsapp",
            content=work.content,
        )

        # El enrutamiento puede decidir que esto no lo atiende la IA: sin regla
        # para la intención, o con menos confianza de la exigida.
        if route.a_persona:
            await self._conversations.escalar_a_persona(
                work.conversation_id, motivo=route.motivo
            )
            logger.info(
                "Mensaje %s pasa a una persona: %s", work.message_id, route.motivo
            )
            # Se avisa al cliente: dejarlo sin respuesta mientras espera a
            # alguien es la peor de las opciones.
            return self._settings.handoff_reply, None

        agent_config = (
            await self._agents.get_active_by_name(route.agent_name) if route.agent_name else None
        )
        if agent_config is None:
            return None, None

        history = await self._conversations.recent_history(
            work.conversation_id,
            limit=self._settings.conversation_history_limit,
            exclude_message_id=work.message_id,
        )
        reply = await run_agent(
            config=agent_config,
            user_message=work.content,
            history=history,
            llm=self._llm,
        )
        return reply, agent_config

    @staticmethod
    def _log_duplicate(phone: str, reference: str) -> None:
        logger.info(
            "Mensaje duplicado o en curso, ignorado (contacto=%s, ref=%s)",
            mask_phone(phone),
            reference,
        )
