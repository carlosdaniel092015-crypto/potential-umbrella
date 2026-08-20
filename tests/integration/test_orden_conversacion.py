"""Orden y contexto dentro de una conversación.

Con la ráfaga ya resuelta (cada mensaje recibe su respuesta), queda la otra
mitad del problema: si los mensajes de un mismo contacto se procesan en
cualquier orden, cada respuesta se genera sin ver las anteriores. El cliente
recibe contestaciones descoordinadas, y en el peor caso desordenadas.
"""

import pytest
from asgiref.sync import async_to_sync

from apps.core_api.config import Settings
from apps.core_api.graphs.classifiers import RuleBasedClassifier
from apps.core_api.graphs.router_graph import AgentRouter
from apps.core_api.schemas.messages import IncomingMessage
from apps.core_api.services.agent_repository import AgentRepository
from apps.core_api.services.conversation_service import ConversationService
from apps.core_api.services.llm_service import FakeLLMProvider
from apps.core_api.services.pipeline import MessagePipeline
from apps.core_api.services.ycloud_service import FakeMessagingProvider
from apps.shared.enums import MessageStatus
from apps.shared.models import Agent, Message

pytestmark = pytest.mark.django_db(transaction=True)


@pytest.fixture(autouse=True)
def agente():
    return Agent.objects.create(name="ventas", system_prompt="Vendes.", is_active=True)


@pytest.fixture
def entorno():
    messaging = FakeMessagingProvider()
    llm = FakeLLMProvider()
    agents = AgentRepository()

    async def resolver(intent, confidence):
        agent = await agents.get_default_active()
        return agent.name if agent else None

    pipeline = MessagePipeline(
        settings=Settings(_env_file=None),
        conversations=ConversationService(),
        agents=agents,
        router=AgentRouter(RuleBasedClassifier(), resolver),
        llm=llm,
        messaging=messaging,
    )
    return pipeline, messaging, llm


def entrante(external_id: str, texto: str) -> IncomingMessage:
    return IncomingMessage(
        external_id=external_id,
        contact_phone="+5215550004444",
        type="text",
        content=texto,
    )


RAFAGA = [
    ("wamid.O1", "buenas tardes"),
    ("wamid.O2", "quiero levantar un pedido"),
    ("wamid.O3", "10 galones de aceite 1-2-3"),
]


def registrar_rafaga(pipeline):
    return [async_to_sync(pipeline.register)(entrante(eid, txt)) for eid, txt in RAFAGA]


def test_un_mensaje_no_se_procesa_antes_que_los_anteriores(entorno):
    """El worker toma el último mensaje de la ráfaga primero (Celery no garantiza
    orden). Ese mensaje debe esperar su turno, no adelantarse."""
    pipeline, messaging, _ = entorno
    registros = registrar_rafaga(pipeline)

    resultado = async_to_sync(pipeline.process_message)(registros[2].message_id)

    assert resultado.status == "esperando_turno", (
        "el tercer mensaje se procesó antes que los dos anteriores"
    )
    assert messaging.sent == []
    # No se reclamó: sigue disponible para cuando llegue su turno.
    tercero = Message.objects.get(id=registros[2].message_id)
    assert tercero.status == MessageStatus.RECEIVED


def test_procesados_en_orden_cada_respuesta_ve_las_anteriores(entorno):
    pipeline, messaging, llm = entorno
    registros = registrar_rafaga(pipeline)

    for r in registros:
        async_to_sync(pipeline.process_message)(r.message_id)

    assert len(messaging.sent) == 3

    # La tercera llamada al modelo debe llevar el ida y vuelta de las dos
    # primeras: sin eso el agente responde «10 galones» sin saber de qué.
    tercera = llm.calls[2]["messages"]
    contenidos = [m["content"] for m in tercera]
    assert "buenas tardes" in contenidos
    assert "quiero levantar un pedido" in contenidos
    assert contenidos[-1] == "10 galones de aceite 1-2-3"
    assert sum(1 for m in tercera if m["role"] == "assistant") == 2


def test_el_orden_de_las_respuestas_sigue_al_de_las_preguntas(entorno):
    pipeline, messaging, _ = entorno
    registros = registrar_rafaga(pipeline)
    for r in registros:
        async_to_sync(pipeline.process_message)(r.message_id)

    entrantes = list(
        Message.objects.filter(direction="in").order_by("id").values_list("id", flat=True)
    )
    respuestas = list(
        Message.objects.filter(direction="out")
        .order_by("id")
        .values_list("reply_to_id", flat=True)
    )
    assert respuestas == entrantes, "las respuestas salieron en otro orden"


def test_un_mensaje_abandonado_no_bloquea_la_conversacion_para_siempre(entorno):
    """Si un mensaje quedó a medias (el caso de fallo al persistir tras enviar),
    los siguientes no pueden quedar atascados eternamente detrás de él."""
    pipeline, messaging, _ = entorno
    registros = registrar_rafaga(pipeline)

    # El primero queda zombi: reclamado y nunca terminado.
    Message.objects.filter(id=registros[0].message_id).update(
        status=MessageStatus.PROCESSING
    )

    bloqueado = async_to_sync(pipeline.process_message)(registros[1].message_id)
    assert bloqueado.status == "esperando_turno"

    # Se envejece el zombi más allá del margen de espera.
    from datetime import timedelta

    from django.utils import timezone

    Message.objects.filter(id=registros[0].message_id).update(
        created_at=timezone.now() - timedelta(minutes=30)
    )

    resultado = async_to_sync(pipeline.process_message)(registros[1].message_id)
    assert resultado.status == "processed", "la conversación quedó bloqueada por un zombi"
    assert len(messaging.sent) == 1


def test_otra_conversacion_no_espera_por_esta(entorno):
    """El orden es por conversación. Un cliente distinto no puede quedar detrás
    de la cola de otro."""
    pipeline, messaging, _ = entorno
    registros = registrar_rafaga(pipeline)
    assert registros  # la ráfaga del primer contacto queda sin procesar

    otro = async_to_sync(pipeline.register)(
        IncomingMessage(
            external_id="wamid.OTRO",
            contact_phone="+5215559998888",
            type="text",
            content="tienen atun dolores?",
        )
    )
    resultado = async_to_sync(pipeline.process_message)(otro.message_id)

    assert resultado.status == "processed"
    assert len(messaging.sent) == 1
