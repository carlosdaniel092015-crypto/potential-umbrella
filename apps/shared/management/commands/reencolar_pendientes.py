"""Rescata mensajes cuya tarea se perdió.

Un mensaje queda en `received` si el worker murió entre el registro y el
procesamiento, o en `processing` si murió a mitad. Nada los recupera solo, y
mientras sigan ahí bloquean el turno de su conversación. Este comando es la
red de seguridad; conviene programarlo cada pocos minutos:

    python manage.py reencolar_pendientes --minutos 5

`--dry-run` lista lo que haría sin tocar nada.
"""

from datetime import timedelta

from django.core.management.base import BaseCommand
from django.utils import timezone

from apps.shared.enums import MessageDirection, MessageStatus
from apps.shared.models import Message


class Command(BaseCommand):
    help = "Reencola mensajes entrantes que quedaron sin procesar."

    def add_arguments(self, parser):
        parser.add_argument(
            "--minutos",
            type=int,
            default=5,
            help="Antigüedad mínima para considerar que la tarea se perdió (5).",
        )
        parser.add_argument(
            "--limite", type=int, default=200, help="Máximo de mensajes por corrida (200)."
        )
        parser.add_argument("--dry-run", action="store_true")

    def handle(self, *args, **options):
        limite_tiempo = timezone.now() - timedelta(minutes=options["minutos"])

        # `received` viejo: la tarea nunca llegó a ejecutarse.
        olvidados = Message.objects.filter(
            direction=MessageDirection.INBOUND,
            status=MessageStatus.RECEIVED,
            created_at__lt=limite_tiempo,
        ).order_by("id")[: options["limite"]]

        # `processing` viejo: se reclamó y no terminó. Se devuelve a `failed`,
        # que es reprocesable, en lugar de reencolarlo tal cual: el reclamo
        # atómico rechazaría un mensaje que sigue en `processing`.
        abandonados = Message.objects.filter(
            direction=MessageDirection.INBOUND,
            status=MessageStatus.PROCESSING,
            processing_started_at__lt=limite_tiempo,
        ).order_by("id")[: options["limite"]]

        ids_olvidados = list(olvidados.values_list("id", flat=True))
        ids_abandonados = list(abandonados.values_list("id", flat=True))

        if options["dry_run"]:
            self.stdout.write(f"olvidados (received): {ids_olvidados}")
            self.stdout.write(f"abandonados (processing): {ids_abandonados}")
            return

        if ids_abandonados:
            Message.objects.filter(id__in=ids_abandonados).update(
                status=MessageStatus.FAILED
            )

        from apps.workers.tasks import process_inbound_message

        for message_id in ids_olvidados + ids_abandonados:
            process_inbound_message.delay(message_id)

        total = len(ids_olvidados) + len(ids_abandonados)
        self.stdout.write(
            self.style.SUCCESS(
                f"reencolados {total} mensajes "
                f"({len(ids_olvidados)} sin empezar, {len(ids_abandonados)} abandonados)"
            )
            if total
            else "nada que reencolar"
        )
