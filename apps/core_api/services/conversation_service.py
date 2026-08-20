"""Persistencia conversacional sobre los modelos compartidos (ADR-06).

Todas las operaciones de ORM se ejecutan en funciones síncronas envueltas con
sync_to_async: nunca ORM síncrono directo dentro de un handler async.
"""

import logging
from dataclasses import dataclass
from typing import Any

from asgiref.sync import sync_to_async
from django.db import IntegrityError, transaction
from django.utils import timezone

from apps.core_api.schemas.messages import IncomingMessage
from apps.shared.enums import ConversationStatus, MessageDirection, MessageStatus
from apps.shared.models import Contact, Conversation, Message

logger = logging.getLogger(__name__)


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
                },
            )
            already_processed = not created and message.status in {
                MessageStatus.PROCESSING,
                MessageStatus.PROCESSED,
            }

            conversation.last_inbound_at = timezone.now()
            conversation.save(update_fields=["last_inbound_at", "updated_at"])

        return InboundRegistration(
            conversation_id=conversation.id,
            message_id=message.id,
            created=created,
            already_processed=already_processed,
        )

    async def register_inbound(self, incoming: IncomingMessage) -> InboundRegistration:
        return await sync_to_async(self._register_inbound_sync)(incoming)

    @staticmethod
    def _claim_sync(message_id: int) -> bool:
        """Reclama el mensaje para procesarlo. UPDATE condicional atómico: solo
        una ejecución concurrente puede pasar de RECEIVED/FAILED a PROCESSING."""
        updated = Message.objects.filter(
            id=message_id,
            status__in=[MessageStatus.RECEIVED, MessageStatus.FAILED],
        ).update(status=MessageStatus.PROCESSING)
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
            existing.content = content
            existing.status = MessageStatus.PENDING
            existing.agent_id = agent_id
            existing.save(update_fields=["content", "status", "agent"])
            return existing.id, False

    async def claim_outbound(
        self, inbound_id: int, conversation_id: int, content: str, agent_id: int | None = None
    ) -> tuple[int, bool]:
        return await sync_to_async(self._claim_outbound_sync)(
            inbound_id, conversation_id, content, agent_id
        )

    @staticmethod
    def _recent_history_sync(conversation_id: int, limit: int, exclude_message_id: int) -> list:
        rows = (
            Message.objects.filter(conversation_id=conversation_id)
            .exclude(id=exclude_message_id)
            .order_by("-created_at")[:limit]
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
