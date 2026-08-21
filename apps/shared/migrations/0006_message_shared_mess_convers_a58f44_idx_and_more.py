"""Índice y restricción de la posición del turno, con relleno de los mensajes
que ya estaban en la base.

El relleno no es opcional. Sin él, los entrantes anteriores al contador quedan
con posición nula, y en SQL `NULL < n` no es verdadero: serían invisibles para
los mensajes nuevos, que responderían antes que ellos. Es exactamente la
inversión de orden que la posición viene a evitar.
"""

from django.db import migrations, models


def rellenar_posiciones(apps, schema_editor):
    Message = apps.get_model("shared", "Message")
    Conversation = apps.get_model("shared", "Conversation")

    for conversation_id in (
        Conversation.objects.values_list("id", flat=True).order_by("id").iterator()
    ):
        entrantes = list(
            Message.objects.filter(
                conversation_id=conversation_id, direction="in", seq__isnull=True
            )
            .order_by("id")
            .values_list("id", flat=True)
        )
        if not entrantes:
            continue

        # Se cuenta desde la posición más alta ya asignada, para no chocar con
        # los mensajes que sí la tienen si la migración se aplica a medias.
        maximo = (
            Message.objects.filter(
                conversation_id=conversation_id, direction="in", seq__isnull=False
            ).aggregate(m=models.Max("seq"))["m"]
            or 0
        )

        for desplazamiento, message_id in enumerate(entrantes, start=1):
            Message.objects.filter(id=message_id).update(seq=maximo + desplazamiento)

        Conversation.objects.filter(id=conversation_id).update(
            next_seq=maximo + len(entrantes) + 1
        )


def vaciar_posiciones(apps, schema_editor):
    """Marcha atrás: deja las posiciones como estaban antes del relleno."""
    Message = apps.get_model("shared", "Message")
    Message.objects.filter(direction="in").update(seq=None)


class Migration(migrations.Migration):

    dependencies = [
        ("shared", "0005_conversation_next_seq_message_seq"),
    ]

    operations = [
        # El relleno va ANTES de la restricción: si hubiera posiciones
        # duplicadas de una versión anterior, es mejor que falle aquí.
        migrations.RunPython(rellenar_posiciones, vaciar_posiciones),
        migrations.AddIndex(
            model_name="message",
            index=models.Index(
                fields=["conversation", "seq"], name="shared_mess_convers_a58f44_idx"
            ),
        ),
        migrations.AddConstraint(
            model_name="message",
            constraint=models.UniqueConstraint(
                condition=models.Q(("direction", "in"), ("seq__isnull", False)),
                fields=("conversation", "seq"),
                name="una_posicion_por_conversacion",
            ),
        ),
    ]
