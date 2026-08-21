"""Modelos compartidos (ERD §1.3 del documento técnico).

Django es el dueño único de estos modelos y de sus migraciones (ADR-06,
docs/decisiones-fase-1.md). FastAPI y los workers Celery los consumen vía el
ORM async / sync_to_async, nunca ejecutan migraciones.
"""

from django.conf import settings
from django.contrib.auth.models import AbstractUser
from django.db import models

from apps.shared.enums import (
    Channel,
    ConversationStatus,
    MessageDirection,
    MessageStatus,
    MessageType,
    OrderStatus,
)


class User(AbstractUser):
    """Usuario del panel. Custom desde el día 1: cambiar AUTH_USER_MODEL después
    de la primera migración es prohibitivo, y los requisitos de MFA/RBAC (§5.2)
    lo van a necesitar."""

    class Meta:
        verbose_name = "usuario"
        verbose_name_plural = "usuarios"


class Contact(models.Model):
    phone = models.CharField(max_length=32, unique=True)
    name = models.CharField(max_length=255, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self) -> str:
        return self.name or self.phone


def default_llm_model() -> str:
    # Leído de settings para no quemar un nombre de modelo en la migración.
    return getattr(settings, "DEFAULT_LLM_MODEL", "gpt-4.1")


class Agent(models.Model):
    name = models.CharField(max_length=100, unique=True)
    description = models.TextField(blank=True)
    system_prompt = models.TextField()
    llm_model = models.CharField(max_length=50, default=default_llm_model)
    temperature = models.FloatField(default=0.4)
    allowed_tools = models.JSONField(default=list, blank=True)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self) -> str:
        return self.name


class AgentPromptHistory(models.Model):
    agent = models.ForeignKey(Agent, on_delete=models.CASCADE, related_name="history")
    previous_prompt = models.TextField()
    new_prompt = models.TextField()
    # PROTECT: borrar un usuario no puede borrar la autoría del cambio (§5.1 Repudiation).
    changed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, null=True, blank=True
    )
    changed_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-changed_at"]
        verbose_name_plural = "agent prompt histories"


class RoutingRule(models.Model):
    """Qué agente atiende cada intención, y con cuánta confianza mínima.

    Vive en la base y se edita desde el panel: cambiar a qué agente va una
    intención, o subir el listón de confianza de una intención delicada, no
    debería requerir desplegar código.
    """

    intent = models.CharField(
        max_length=50,
        help_text="Intención del catálogo, o * para el resto de los casos.",
    )
    agent = models.ForeignKey(Agent, on_delete=models.PROTECT, related_name="rules")
    min_confidence = models.FloatField(
        default=0.5,
        help_text=(
            "Por debajo de esta confianza la conversación pasa a una persona "
            "en lugar de responderse. Súbelo en las intenciones donde "
            "equivocarse cuesta."
        ),
    )
    is_active = models.BooleanField(default=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            # Una sola regla activa por intención: dos reglas activas para la
            # misma intención harían que el agente elegido dependiera del orden
            # en que la base devuelva las filas.
            models.UniqueConstraint(
                fields=["intent"],
                condition=models.Q(is_active=True),
                name="una_regla_activa_por_intencion",
            )
        ]

    def __str__(self) -> str:
        return f"{self.intent} → {self.agent_id} (≥{self.min_confidence})"


class Conversation(models.Model):
    contact = models.ForeignKey(Contact, on_delete=models.CASCADE, related_name="conversations")
    agent = models.ForeignKey(
        Agent, on_delete=models.SET_NULL, null=True, blank=True, related_name="conversations"
    )
    channel = models.CharField(max_length=16, choices=Channel.choices, default=Channel.WHATSAPP)
    status = models.CharField(
        max_length=16, choices=ConversationStatus.choices, default=ConversationStatus.ACTIVE
    )
    # Ventana de 24h de WhatsApp Business: fuera de ella solo se permiten plantillas.
    # Se registra desde Fase 1; el enforcement de plantillas llega en fases posteriores.
    last_inbound_at = models.DateTimeField(null=True, blank=True)
    # Siguiente posición a asignar dentro de la conversación. Se reparte bajo
    # bloqueo de esta fila, así que refleja el orden en que los mensajes se
    # confirmaron, no el orden en que se insertaron: el id autoincremental se
    # asigna al INSERT y en Postgres el INSERT no es el COMMIT, de modo que dos
    # webhooks concurrentes del mismo contacto podían quedar al revés.
    next_seq = models.PositiveIntegerField(default=1)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            # Una sola conversación no cerrada por contacto y canal: sin esto,
            # dos mensajes concurrentes de un contacto nuevo creaban dos
            # conversaciones y el historial quedaba partido para siempre.
            models.UniqueConstraint(
                fields=["contact", "channel"],
                condition=~models.Q(status=ConversationStatus.CLOSED),
                name="unica_conversacion_abierta_por_contacto_canal",
            )
        ]

    def __str__(self) -> str:
        return f"{self.contact} [{self.channel}] ({self.status})"


class Message(models.Model):
    conversation = models.ForeignKey(
        Conversation, on_delete=models.CASCADE, related_name="messages"
    )
    direction = models.CharField(max_length=4, choices=MessageDirection.choices)
    message_type = models.CharField(
        max_length=16, choices=MessageType.choices, default=MessageType.TEXT
    )
    content = models.TextField(blank=True)
    # ID del proveedor (YCloud). Unique = barrera de idempotencia ante webhooks
    # reintentados/duplicados. Nullable porque los salientes lo reciben tras el envío.
    external_id = models.CharField(max_length=128, unique=True, null=True, blank=True)
    status = models.CharField(
        max_length=16, choices=MessageStatus.choices, default=MessageStatus.RECEIVED
    )
    # Agente que generó/atendió este mensaje (el enrutamiento es por mensaje, no
    # se congela a nivel de conversación — hallazgo ERD de la revisión).
    agent = models.ForeignKey(Agent, on_delete=models.SET_NULL, null=True, blank=True)
    # Un saliente apunta al entrante que responde. Sin esta atadura no se puede
    # saber si un mensaje CONCRETO ya fue contestado: la comprobación anterior
    # ("¿hay alguna respuesta posterior a este id?") descartaba en silencio los
    # mensajes 2..N de cada ráfaga, porque la respuesta al primero ya era
    # posterior a todos ellos.
    reply_to = models.ForeignKey(
        "self", on_delete=models.SET_NULL, null=True, blank=True, related_name="replies"
    )
    # Cuándo se reclamó para procesar. El margen que libera un mensaje
    # abandonado se mide desde aquí y no desde `created_at`: con `created_at`,
    # drenar un backlog viejo (worker caído) desactivaba el turno para toda la
    # ráfaga, que es justo cuando el orden más importa.
    processing_started_at = models.DateTimeField(null=True, blank=True)
    # Posición dentro de la conversación, repartida bajo bloqueo de la fila de
    # Conversation. Es lo que ordena el turno; el id no sirve porque se asigna
    # al INSERT y el orden de INSERT no es el orden de COMMIT.
    seq = models.PositiveIntegerField(null=True, blank=True)
    metadata = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["created_at"]
        indexes = [
            models.Index(fields=["conversation", "created_at"]),
            # El turno filtra por conversación y posición en cada mensaje.
            models.Index(fields=["conversation", "seq"]),
        ]
        constraints = [
            # Como máximo una respuesta por mensaje entrante. Crear la fila ES
            # el derecho a responder: si dos ejecuciones concurrentes llegan
            # aquí, la segunda choca con el índice y no le escribe al cliente.
            models.UniqueConstraint(
                fields=["reply_to"],
                condition=models.Q(direction=MessageDirection.OUTBOUND),
                name="una_respuesta_por_mensaje_entrante",
            ),
            # Dos entrantes con la misma posición no serían anteriores el uno al
            # otro, así que ambos se procesarían a la vez y sin ninguna señal.
            # Hoy el reparto bajo bloqueo lo evita, pero eso es una promesa del
            # código; esto lo garantiza la base.
            models.UniqueConstraint(
                fields=["conversation", "seq"],
                condition=models.Q(
                    direction=MessageDirection.INBOUND, seq__isnull=False
                ),
                name="una_posicion_por_conversacion",
            ),
        ]

    def __str__(self) -> str:
        return f"[{self.direction}] {self.content[:40]}"


class Order(models.Model):
    conversation = models.ForeignKey(Conversation, on_delete=models.CASCADE, related_name="orders")
    # client_order_ref para idempotencia hacia Odoo (Fase 3).
    idempotency_key = models.CharField(max_length=64, unique=True)
    odoo_order_id = models.CharField(max_length=64, blank=True)
    status = models.CharField(max_length=16, choices=OrderStatus.choices, default=OrderStatus.DRAFT)
    data = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)


class TrainingExample(models.Model):
    message = models.OneToOneField(Message, on_delete=models.CASCADE, related_name="training")
    corrected_response = models.TextField()
    notes = models.TextField(blank=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, null=True, blank=True
    )
    created_at = models.DateTimeField(auto_now_add=True)


class Configuration(models.Model):
    key = models.CharField(max_length=100, unique=True)
    value = models.JSONField(default=dict)
    version = models.PositiveIntegerField(default=1)
    updated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, null=True, blank=True
    )
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self) -> str:
        return f"{self.key} (v{self.version})"


class TraceEvent(models.Model):
    """Auditoría de eventos clave por conversación (§1.1 event-driven / §5.1 Repudiation)."""

    conversation = models.ForeignKey(
        Conversation, on_delete=models.CASCADE, related_name="trace_events"
    )
    trace_id = models.CharField(max_length=64, db_index=True)
    event_type = models.CharField(max_length=64)
    payload = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["created_at"]
