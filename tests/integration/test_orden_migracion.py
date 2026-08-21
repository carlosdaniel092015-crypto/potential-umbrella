"""Convivencia entre mensajes con posición y sin ella.

Al desplegar el orden por posición, los mensajes que ya estaban en la base no
tienen ninguna. En SQL `NULL < 1` no es verdadero, así que una fila sin posición
es invisible para cualquier comparación: el mensaje nuevo no vería a su
predecesor viejo y le responderría antes. Es la misma inversión de orden que
todo este trabajo persigue, reintroducida por la puerta de atrás.
"""

import pytest
from asgiref.sync import async_to_sync

from apps.core_api.schemas.messages import IncomingMessage
from apps.core_api.services.conversation_service import ConversationService
from apps.shared.models import Conversation, Message

pytestmark = pytest.mark.django_db(transaction=True)

TELEFONO = "+5215550008888"


def entrante(external_id: str) -> IncomingMessage:
    return IncomingMessage(
        external_id=external_id, contact_phone=TELEFONO, type="text", content="hola"
    )


@pytest.fixture
def servicio():
    return ConversationService()


def test_un_mensaje_viejo_sin_posicion_sigue_bloqueando_al_nuevo(servicio):
    """El escenario exacto del despliegue: un entrante quedó sin procesar antes
    de la migración, y el que llega después ya trae posición."""
    viejo = async_to_sync(servicio.register_inbound)(entrante("wamid.M1"))
    # Se simula el estado post-migración sin backfill.
    Message.objects.filter(id=viejo.message_id).update(seq=None)
    Conversation.objects.filter(id=viejo.conversation_id).update(next_seq=1)

    nuevo = async_to_sync(servicio.register_inbound)(entrante("wamid.M2"))
    assert Message.objects.get(id=nuevo.message_id).seq == 1

    espera = async_to_sync(servicio.tiene_anteriores_pendientes)(
        nuevo.conversation_id, nuevo.message_id
    )
    assert espera is True, (
        "el mensaje nuevo no ve al viejo sin posición y le responde antes"
    )


def test_dos_mensajes_no_pueden_compartir_posicion(servicio):
    """Si dos entrantes comparten posición, ninguno es anterior al otro y ambos
    se procesan a la vez, sin ninguna señal. La base tiene que impedirlo."""
    from django.db import IntegrityError

    r1 = async_to_sync(servicio.register_inbound)(entrante("wamid.N1"))
    r2 = async_to_sync(servicio.register_inbound)(entrante("wamid.N2"))

    with pytest.raises(IntegrityError):
        Message.objects.filter(id=r2.message_id).update(
            seq=Message.objects.get(id=r1.message_id).seq
        )


def test_el_historial_tambien_se_ordena_por_posicion(servicio):
    """Coherencia: si el id no sirve para ordenar el turno, tampoco sirve para
    ordenar el historial que se le pasa al modelo."""
    r1 = async_to_sync(servicio.register_inbound)(entrante("wamid.O1"))
    r2 = async_to_sync(servicio.register_inbound)(entrante("wamid.O2"))
    r3 = async_to_sync(servicio.register_inbound)(entrante("wamid.O3"))

    # Posiciones al revés de los ids: el historial debe seguir las posiciones.
    Message.objects.filter(id=r1.message_id).update(seq=30, content="tercero")
    Message.objects.filter(id=r2.message_id).update(seq=20, content="segundo")
    Message.objects.filter(id=r3.message_id).update(seq=10, content="primero")

    historial = async_to_sync(servicio.recent_history)(r1.conversation_id, limit=10)
    textos = [m["content"] for m in historial]
    assert textos == ["primero", "segundo", "tercero"], (
        f"el historial se ordenó por id y no por posición: {textos}"
    )


def test_una_posicion_de_otra_conversacion_no_cuenta(servicio):
    """La posición solo es comparable dentro de su conversación."""
    r1 = async_to_sync(servicio.register_inbound)(entrante("wamid.P1"))

    otra = async_to_sync(servicio.register_inbound)(
        IncomingMessage(
            external_id="wamid.P2",
            contact_phone="+5215550009999",
            type="text",
            content="hola",
        )
    )
    assert otra.conversation_id != r1.conversation_id

    # Pedir el turno con un mensaje que no pertenece a esa conversación no puede
    # inventarse un predecesor.
    espera = async_to_sync(servicio.tiene_anteriores_pendientes)(
        r1.conversation_id, otra.message_id
    )
    assert espera is False
