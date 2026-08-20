from django.db import models


class Channel(models.TextChoices):
    WHATSAPP = "whatsapp", "WhatsApp"
    VOICE = "voice", "Voz"


class MessageDirection(models.TextChoices):
    INBOUND = "in", "Entrante"
    OUTBOUND = "out", "Saliente"


class MessageType(models.TextChoices):
    TEXT = "text", "Texto"
    IMAGE = "image", "Imagen"
    AUDIO = "audio", "Audio"
    DOCUMENT = "document", "Documento"
    LOCATION = "location", "Ubicación"
    INTERACTIVE = "interactive", "Interactivo"
    OTHER = "other", "Otro"


class MessageStatus(models.TextChoices):
    # Entrantes: RECEIVED -> PROCESSING -> PROCESSED | FAILED.
    # Salientes: PENDING -> SENT | SEND_FAILED.
    #
    # PROCESSING se reclama con un UPDATE condicional atómico: cierra la ventana
    # en la que dos webhooks concurrentes del mismo mensaje veían RECEIVED y
    # ambos ejecutaban el pipeline (doble respuesta al cliente). Solo FAILED es
    # reprocesable; PROCESSING/PROCESSED se descartan como duplicado.
    RECEIVED = "received", "Recibido"
    PROCESSING = "processing", "En proceso"
    PROCESSED = "processed", "Procesado"
    FAILED = "failed", "Fallido"
    PENDING = "pending", "Pendiente de envío"
    SENT = "sent", "Enviado"
    SEND_FAILED = "send_failed", "Envío fallido"


class ConversationStatus(models.TextChoices):
    ACTIVE = "active", "Activa (bot)"
    ESCALATED = "escalated", "Escalada a humano"
    CLOSED = "closed", "Cerrada"


class OrderStatus(models.TextChoices):
    DRAFT = "draft", "Borrador"
    CONFIRMED = "confirmed", "Confirmado"
    SYNCED = "synced", "Sincronizado con Odoo"
    CANCELLED = "cancelled", "Cancelado"
