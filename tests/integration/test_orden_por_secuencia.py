"""El turno se ordena por una posición repartida bajo bloqueo, no por el id.

Motivo: el id autoincremental se asigna al INSERT, y en Postgres el INSERT no
es el COMMIT. Dos webhooks concurrentes del mismo contacto podían obtener ids
en un orden y confirmar en el otro, y entonces el sucesor no veía a su
predecesor y respondía primero.

Limitación de estas pruebas, dicha sin rodeos: corren en SQLite, que serializa
las escrituras, así que **no reproducen la carrera**. Lo que verifican es el
mecanismo — que la posición existe, que es consecutiva, que el turno la usa y
que el registro pasa por el bloqueo de la conversación.
"""

import threading

import pytest
from asgiref.sync import async_to_sync
from django.db import connection

from apps.core_api.schemas.messages import IncomingMessage
from apps.core_api.services.conversation_service import ConversationService
from apps.shared.enums import MessageStatus
from apps.shared.models import Conversation, Message

pytestmark = pytest.mark.django_db(transaction=True)

TELEFONO = "+5215550007777"


def entrante(external_id: str, texto: str = "hola") -> IncomingMessage:
    return IncomingMessage(
        external_id=external_id, contact_phone=TELEFONO, type="text", content=texto
    )


@pytest.fixture
def servicio():
    return ConversationService()


def test_cada_mensaje_recibe_una_posicion_consecutiva(servicio):
    for i in range(1, 4):
        async_to_sync(servicio.register_inbound)(entrante(f"wamid.Q{i}"))

    posiciones = list(
        Message.objects.filter(direction="in").order_by("id").values_list("seq", flat=True)
    )
    assert posiciones == [1, 2, 3]
    assert Conversation.objects.get().next_seq == 4


def test_un_duplicado_no_consume_posicion(servicio):
    async_to_sync(servicio.register_inbound)(entrante("wamid.R1"))
    async_to_sync(servicio.register_inbound)(entrante("wamid.R1"))
    async_to_sync(servicio.register_inbound)(entrante("wamid.R2"))

    posiciones = list(
        Message.objects.filter(direction="in").order_by("id").values_list("seq", flat=True)
    )
    assert posiciones == [1, 2], "un webhook repetido gastó una posición"
    assert Conversation.objects.get().next_seq == 3


def test_el_turno_usa_la_posicion_y_no_el_id(servicio):
    """Se fabrica el caso que la carrera produciría: ids en un orden y
    posiciones en el contrario. El turno tiene que seguir la posición."""
    r1 = async_to_sync(servicio.register_inbound)(entrante("wamid.S1"))
    r2 = async_to_sync(servicio.register_inbound)(entrante("wamid.S2"))

    # Posiciones invertidas respecto a los ids.
    Message.objects.filter(id=r1.message_id).update(seq=2)
    Message.objects.filter(id=r2.message_id).update(seq=1)

    # El de id menor (posición 2) ahora tiene un predecesor: el de id mayor.
    espera_el_primero = async_to_sync(servicio.tiene_anteriores_pendientes)(
        r1.conversation_id, r1.message_id
    )
    espera_el_segundo = async_to_sync(servicio.tiene_anteriores_pendientes)(
        r2.conversation_id, r2.message_id
    )

    assert espera_el_primero is True, "el turno sigue mirando el id"
    assert espera_el_segundo is False


def test_un_mensaje_sin_posicion_cae_al_id(servicio):
    """Filas registradas antes de que existiera el contador: el turno no puede
    dejar de funcionar para ellas."""
    r1 = async_to_sync(servicio.register_inbound)(entrante("wamid.T1"))
    r2 = async_to_sync(servicio.register_inbound)(entrante("wamid.T2"))
    Message.objects.filter(id__in=[r1.message_id, r2.message_id]).update(seq=None)

    assert (
        async_to_sync(servicio.tiene_anteriores_pendientes)(
            r2.conversation_id, r2.message_id
        )
        is True
    )
    assert (
        async_to_sync(servicio.tiene_anteriores_pendientes)(
            r1.conversation_id, r1.message_id
        )
        is False
    )


def test_el_registro_pasa_por_el_bloqueo_de_la_conversacion(servicio):
    """En Postgres el bloqueo es lo que serializa a dos webhooks del mismo
    contacto. Aquí solo se comprueba que la ruta se toma cuando el motor lo
    soporta, porque SQLite no tiene bloqueo de fila."""
    llamadas = []
    original = ConversationService._bloquear_conversacion

    def espia(conversation_id):
        llamadas.append(conversation_id)
        return original(conversation_id)

    ConversationService._bloquear_conversacion = staticmethod(espia)
    try:
        r = async_to_sync(servicio.register_inbound)(entrante("wamid.U1"))
    finally:
        ConversationService._bloquear_conversacion = staticmethod(original)

    assert llamadas == [r.conversation_id]
    # Y en un motor con bloqueo de fila, la consulta lo pediría de verdad.
    if connection.features.has_select_for_update:
        sql = str(
            Conversation.objects.filter(id=r.conversation_id).select_for_update().query
        )
        assert "FOR UPDATE" in sql.upper()


@pytest.mark.skipif(
    not connection.features.has_select_for_update,
    reason=(
        "SQLite bloquea la tabla ante escrituras concurrentes desde varios "
        "hilos, así que la prueba no puede correr aquí. Queda escrita para "
        "cuando la suite se ejecute contra Postgres, que es donde la carrera "
        "es posible y donde el bloqueo de fila la evita."
    ),
)
def test_registros_concurrentes_no_reparten_la_misma_posicion(servicio):
    """La prueba que de verdad demostraría el arreglo. Solo tiene sentido en un
    motor con bloqueo de fila."""
    errores: list[Exception] = []

    def registrar(indice: int):
        try:
            async_to_sync(servicio.register_inbound)(entrante(f"wamid.V{indice}"))
        except Exception as exc:  # pragma: no cover - solo si algo revienta
            errores.append(exc)

    hilos = [threading.Thread(target=registrar, args=(i,)) for i in range(5)]
    for h in hilos:
        h.start()
    for h in hilos:
        h.join()

    assert errores == [], f"el registro concurrente falló: {errores}"
    posiciones = sorted(
        Message.objects.filter(direction="in").values_list("seq", flat=True)
    )
    assert posiciones == [1, 2, 3, 4, 5], f"posiciones repartidas mal: {posiciones}"


def test_el_estado_del_mensaje_no_depende_de_la_posicion(servicio):
    """La posición ordena; no decide si un mensaje es duplicado."""
    r1 = async_to_sync(servicio.register_inbound)(entrante("wamid.W1"))
    Message.objects.filter(id=r1.message_id).update(status=MessageStatus.PROCESSED)

    repetido = async_to_sync(servicio.register_inbound)(entrante("wamid.W1"))
    assert repetido.already_processed is True
    assert repetido.message_id == r1.message_id
