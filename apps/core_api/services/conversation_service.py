"""Persistencia conversacional sobre los modelos compartidos (ADR-06).

Todas las operaciones de ORM se ejecutan en funciones síncronas envueltas con
sync_to_async: nunca ORM síncrono directo dentro de un handler async.
"""

import logging
from dataclasses import dataclass
from datetime import timedelta
from typing import Any

from asgiref.sync import sync_to_async
from django.db import IntegrityError, OperationalError, connection, transaction
from django.db.models import Case, IntegerField, Q, Value, When
from django.db.models.functions import Coalesce
from django.utils import timezone

from apps.core_api.schemas.messages import IncomingMessage
from apps.shared.enums import ConversationStatus, MessageDirection, MessageStatus
from apps.shared.models import Contact, Conversation, Message, TraceEvent

logger = logging.getLogger(__name__)


class ConversacionNoDisponible(RuntimeError):
    """La conversación no se pudo tomar para registrar el mensaje: alguien
    retiene su bloqueo más de lo tolerado, o se cerró mientras se registraba.

    El webhook lo traduce a 503 para que el proveedor reintente, en lugar de
    dejar el mensaje sin registrar con un 200 o tumbar la petición con un 500.
    """


@dataclass
class InboundRegistration:
    conversation_id: int
    message_id: int
    created: bool
    already_processed: bool


@dataclass
class InboundWork:
    """Trabajo rehidratado desde Postgres (fuente canónica): permite encolar
    solo el id del mensaje, sin PII en el broker."""

    message_id: int
    conversation_id: int
    contact_phone: str
    message_type: str
    content: str
    conversation_status: str = ConversationStatus.ACTIVE


class ConversationService:
    @staticmethod
    def _get_or_create_conversation(contact: Contact, channel: str) -> Conversation:
        existing = (
            Conversation.objects.filter(contact=contact, channel=channel)
            .exclude(status=ConversationStatus.CLOSED)
            .order_by("-created_at")
            .first()
        )
        if existing is not None:
            return existing
        try:
            # savepoint: si el constraint único salta por una creación
            # concurrente, la transacción externa sigue usable.
            with transaction.atomic():
                return Conversation.objects.create(contact=contact, channel=channel)
        except IntegrityError:
            found = (
                Conversation.objects.filter(contact=contact, channel=channel)
                .exclude(status=ConversationStatus.CLOSED)
                .order_by("-created_at")
                .first()
            )
            if found is None:  # pragma: no cover - contradicción del constraint
                raise
            return found

    @staticmethod
    def _bloquear_conversacion(conversation_id: int) -> Conversation:
        """Toma la fila de la conversación en exclusiva, y revalida que siga
        abierta.

        Serializa el registro de mensajes de un mismo contacto: el segundo
        webhook espera a que el primero confirme, así que la posición que se
        reparte a continuación respeta el orden real.

        Se revalida el estado porque la conversación se eligió antes del
        bloqueo: si alguien la cerró en medio, colgar el mensaje de ella
        partiría el historial en dos.

        En SQLite no hay bloqueo de fila. El reparto sigue siendo correcto
        porque el motor rechaza la segunda escritura concurrente en lugar de
        permitir un valor repetido, pero la garantía viene de ahí, no de una
        serialización ordenada.
        """
        query = Conversation.objects.filter(id=conversation_id).exclude(
            status=ConversationStatus.CLOSED
        )
        if connection.features.has_select_for_update:
            query = query.select_for_update()
        try:
            return query.get()
        except Conversation.DoesNotExist as exc:
            # Se cerró o desapareció entre la búsqueda y el bloqueo.
            raise ConversacionNoDisponible(
                f"La conversación {conversation_id} dejó de estar disponible"
            ) from exc

    @staticmethod
    def _register_inbound_sync(incoming: IncomingMessage) -> InboundRegistration:
        with transaction.atomic():
            contact, _ = Contact.objects.get_or_create(
                phone=incoming.contact_phone,
                defaults={"name": incoming.contact_name},
            )
            if incoming.contact_name and not contact.name:
                contact.name = incoming.contact_name
                contact.save(update_fields=["name"])

            conversation = ConversationService._get_or_create_conversation(
                contact, incoming.channel
            )
            # Desde aquí hasta el commit, ningún otro registro de esta
            # conversación avanza.
            conversation = ConversationService._bloquear_conversacion(conversation.id)

            # Idempotencia: external_id unique. Un webhook duplicado no crea
            # segunda fila. Solo FAILED es reprocesable; PROCESSING (otra
            # ejecución en curso) y PROCESSED se tratan como duplicado.
            message, created = Message.objects.get_or_create(
                external_id=incoming.external_id,
                defaults={
                    "conversation": conversation,
                    "direction": MessageDirection.INBOUND,
                    "message_type": incoming.type,
                    "content": incoming.content,
                    "status": MessageStatus.RECEIVED,
                    "metadata": incoming.metadata,
                    "seq": conversation.next_seq,
                },
            )
            already_processed = not created and message.status in {
                MessageStatus.PROCESSING,
                MessageStatus.PROCESSED,
            }

            conversation.last_inbound_at = timezone.now()
            campos = ["last_inbound_at", "updated_at"]
            if created:
                conversation.next_seq += 1
                campos.append("next_seq")
            conversation.save(update_fields=campos)

        return InboundRegistration(
            conversation_id=conversation.id,
            message_id=message.id,
            created=created,
            already_processed=already_processed,
        )

    async def register_inbound(self, incoming: IncomingMessage) -> InboundRegistration:
        try:
            return await sync_to_async(self._register_inbound_sync)(incoming)
        except OperationalError as exc:
            # Con `lock_timeout` configurado, una espera excesiva por el bloqueo
            # de la conversación llega aquí. Fallar rápido y que el proveedor
            # reintente es mejor que retener el único hilo del ORM.
            logger.warning(
                "No se pudo registrar el mensaje %s: %s", incoming.external_id, exc
            )
            raise ConversacionNoDisponible(str(exc)) from exc

    @staticmethod
    def _anteriores_pendientes_sync(
        conversation_id: int, message_id: int, margen_minutos: int
    ) -> bool:
        """¿Queda algún mensaje anterior de esta conversación sin terminar?

        Es el turno por conversación: sin él, dos workers procesan en paralelo
        dos mensajes del mismo cliente y cada respuesta se genera sin ver la
        otra, así que llegan descoordinadas y en cualquier orden.

        Bloquean tres estados, y el motivo de cada uno importa:

        - `RECEIVED`: aún no empezó. Bloquea siempre. Si su tarea se perdió, lo
          rescata `reencolar_pendientes`, no un tiempo de espera.
        - `PROCESSING`: en curso. Bloquea hasta que el reclamo caduca — medido
          desde `processing_started_at`, porque un backlog viejo no significa
          abandono.
        - `FAILED`: falló y va a reintentarse, así que su respuesta todavía
          puede salir. Si no bloqueara, su sucesor contestaría primero y el
          cliente recibiría las respuestas al revés.
        """
        limite = timezone.now() - timedelta(minutes=margen_minutos)
        # La posición se lee validando que el mensaje pertenezca a esta
        # conversación: solo es comparable dentro de ella.
        propia = list(
            Message.objects.filter(
                id=message_id, conversation_id=conversation_id
            ).values_list("seq", flat=True)[:1]
        )
        if not propia:
            logger.warning(
                "El mensaje %s no pertenece a la conversación %s: no se calcula turno",
                message_id,
                conversation_id,
            )
            return False

        propio = propia[0]
        anteriores = Message.objects.filter(
            conversation_id=conversation_id,
            direction=MessageDirection.INBOUND,
        ).exclude(id=message_id)
        if propio is None:
            # Este mensaje no tiene posición (quedó de antes del contador): se
            # ordena por id, que es lo que había.
            anteriores = anteriores.filter(id__lt=message_id)
        else:
            # `NULL < n` es NULL en SQL, no verdadero: sin la rama explícita, un
            # mensaje sin posición sería invisible y su sucesor le responderría
            # antes. Una posición ausente cuenta siempre como anterior, porque
            # el contador solo reparte hacia adelante.
            anteriores = anteriores.filter(
                Q(seq__lt=propio) | Q(seq__isnull=True)
            )
        sin_empezar = anteriores.filter(
            status__in=[MessageStatus.RECEIVED, MessageStatus.FAILED]
        )
        en_curso = anteriores.filter(status=MessageStatus.PROCESSING).filter(
            Q(processing_started_at__gte=limite) | Q(processing_started_at__isnull=True)
        )
        return sin_empezar.exists() or en_curso.exists()

    async def tiene_anteriores_pendientes(
        self, conversation_id: int, message_id: int, margen_minutos: int = 15
    ) -> bool:
        return await sync_to_async(self._anteriores_pendientes_sync)(
            conversation_id, message_id, margen_minutos
        )

    @staticmethod
    def _claim_sync(message_id: int) -> bool:
        """Reclama el mensaje para procesarlo. UPDATE condicional atómico: solo
        una ejecución concurrente puede pasar de RECEIVED/FAILED a PROCESSING.

        Sella el momento del reclamo: es la referencia desde la que se mide si
        un mensaje quedó abandonado.
        """
        updated = Message.objects.filter(
            id=message_id,
            status__in=[MessageStatus.RECEIVED, MessageStatus.FAILED],
        ).update(status=MessageStatus.PROCESSING, processing_started_at=timezone.now())
        return updated == 1

    async def claim_for_processing(self, message_id: int) -> bool:
        return await sync_to_async(self._claim_sync)(message_id)

    @staticmethod
    def _load_work_sync(message_id: int) -> InboundWork | None:
        row = (
            Message.objects.filter(id=message_id, direction=MessageDirection.INBOUND)
            .select_related("conversation__contact")
            .first()
        )
        if row is None:
            return None
        return InboundWork(
            message_id=row.id,
            conversation_id=row.conversation_id,
            contact_phone=row.conversation.contact.phone,
            message_type=row.message_type,
            content=row.content,
            conversation_status=row.conversation.status,
        )

    async def load_work(self, message_id: int) -> InboundWork | None:
        return await sync_to_async(self._load_work_sync)(message_id)

    @staticmethod
    def _claim_outbound_sync(
        inbound_id: int, conversation_id: int, content: str, agent_id: int | None
    ) -> tuple[int, bool]:
        """Reclama el derecho a responder a un entrante concreto.

        Devuelve (id del saliente, ya_enviado). Crear la fila es el reclamo: el
        índice único sobre `reply_to` impide que dos ejecuciones concurrentes
        respondan al mismo mensaje. Si ya existe una respuesta ENVIADA, se avisa
        para no escribirle al cliente por segunda vez; si existe pero nunca
        salió, se reutiliza y se reintenta el envío.
        """
        try:
            with transaction.atomic():
                message = Message.objects.create(
                    conversation_id=conversation_id,
                    direction=MessageDirection.OUTBOUND,
                    content=content,
                    status=MessageStatus.PENDING,
                    agent_id=agent_id,
                    reply_to_id=inbound_id,
                )
                return message.id, False
        except IntegrityError:
            existing = Message.objects.filter(
                reply_to_id=inbound_id, direction=MessageDirection.OUTBOUND
            ).first()
            if existing is None:  # pragma: no cover - contradice el constraint
                raise
            if existing.status == MessageStatus.SENT:
                return existing.id, True
            # Se conserva el texto que ya se había generado. Regenerarlo con el
            # modelo daría una respuesta distinta, y si el envío anterior falló
            # de forma ambigua (un tiempo de espera agotado que el proveedor sí
            # entregó) el cliente vería dos versiones diferentes de lo mismo.
            existing.status = MessageStatus.PENDING
            existing.save(update_fields=["status"])
            return existing.id, False

    async def claim_outbound(
        self, inbound_id: int, conversation_id: int, content: str, agent_id: int | None = None
    ) -> tuple[int, bool]:
        return await sync_to_async(self._claim_outbound_sync)(
            inbound_id, conversation_id, content, agent_id
        )

    @staticmethod
    def _texto_pendiente_sync(inbound_id: int) -> str | None:
        """Texto ya generado para este entrante y aún sin enviar, si existe."""
        row = (
            Message.objects.filter(
                reply_to_id=inbound_id, direction=MessageDirection.OUTBOUND
            )
            .exclude(status=MessageStatus.SENT)
            .first()
        )
        return row.content if row and row.content else None

    async def texto_pendiente(self, inbound_id: int) -> str | None:
        return await sync_to_async(self._texto_pendiente_sync)(inbound_id)

    @staticmethod
    def _escalar_sync(conversation_id: int, motivo: str, trace_id: str) -> None:
        Conversation.objects.filter(id=conversation_id).exclude(
            status=ConversationStatus.CLOSED
        ).update(status=ConversationStatus.ESCALATED)
        TraceEvent.objects.create(
            conversation_id=conversation_id,
            trace_id=trace_id,
            event_type="escalada_a_persona",
            payload={"motivo": motivo},
        )

    async def escalar_a_persona(
        self, conversation_id: int, motivo: str, trace_id: str = ""
    ) -> None:
        """Pasa la conversación a una persona y lo deja registrado.

        Desde este momento el bot no vuelve a responder en ella hasta que
        alguien la devuelva a la IA desde el panel.
        """
        await sync_to_async(self._escalar_sync)(conversation_id, motivo, trace_id)

    @staticmethod
    def _recent_history_sync(conversation_id: int, limit: int, exclude_message_id: int) -> list:
        # Solo entra en el historial lo que el cliente vio de verdad: una
        # respuesta que quedó sin enviar no es parte de la conversación, y
        # metida en el prompt hace que el bot dé por leído lo que nunca llegó.
        #
        # Se ordena por la posición del turno, con el id como desempate. Ordenar
        # por id sería incoherente: si el id no basta para decidir el turno
        # (porque es orden de INSERT y no de COMMIT), tampoco basta para decidir
        # en qué orden ve el modelo la conversación. Un saliente hereda la
        # posición del entrante que contesta, así queda justo detrás de él.
        rows = (
            Message.objects.filter(conversation_id=conversation_id)
            .filter(
                Q(direction=MessageDirection.INBOUND)
                | Q(direction=MessageDirection.OUTBOUND, status=MessageStatus.SENT)
            )
            .exclude(id=exclude_message_id)
            .annotate(
                orden=Coalesce("seq", "reply_to__seq"),
                es_respuesta=Case(
                    When(direction=MessageDirection.OUTBOUND, then=Value(1)),
                    default=Value(0),
                    output_field=IntegerField(),
                ),
            )
            .order_by("-orden", "-es_respuesta", "-id")[:limit]
        )
        history = [
            {
                "role": "user" if row.direction == MessageDirection.INBOUND else "assistant",
                "content": row.content,
            }
            for row in rows
        ]
        history.reverse()  # cronológico
        return history

    async def recent_history(
        self, conversation_id: int, limit: int = 10, exclude_message_id: int = 0
    ) -> list[dict]:
        return await sync_to_async(self._recent_history_sync)(
            conversation_id, limit, exclude_message_id
        )

    @staticmethod
    def _mark_sync(message_id: int, status: str, external_id: str | None) -> None:
        fields: dict[str, Any] = {"status": status}
        if external_id:
            fields["external_id"] = external_id
        try:
            with transaction.atomic():
                Message.objects.filter(id=message_id).update(**fields)
        except IntegrityError:
            # El external_id del saliente solo sirve para conciliar callbacks de
            # estado: si colisiona, el estado del mensaje pesa más que el id.
            logger.warning(
                "external_id %r ya existía: se marca el mensaje %s sin él",
                external_id,
                message_id,
            )
            Message.objects.filter(id=message_id).update(status=status)

    async def mark_outbound_sent(self, message_id: int, external_id: str) -> None:
        await sync_to_async(self._mark_sync)(message_id, MessageStatus.SENT, external_id)

    async def mark_outbound_failed(self, message_id: int) -> None:
        await sync_to_async(self._mark_sync)(message_id, MessageStatus.SEND_FAILED, None)

    async def mark_inbound_processed(self, message_id: int, agent_id: int | None = None) -> None:
        def _mark() -> None:
            fields: dict[str, Any] = {"status": MessageStatus.PROCESSED}
            if agent_id is not None:
                fields["agent_id"] = agent_id
            Message.objects.filter(id=message_id).update(**fields)

        await sync_to_async(_mark)()

    async def mark_inbound_failed(self, message_id: int) -> None:
        await sync_to_async(self._mark_sync)(message_id, MessageStatus.FAILED, None)
