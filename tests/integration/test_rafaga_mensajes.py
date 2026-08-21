"""Ráfaga de mensajes del mismo contacto: el caso cotidiano en WhatsApp
(«hola» / «quiero pedir» / «2 cajas» en tres segundos).

Cada mensaje entrante debe recibir su respuesta. Ninguno puede quedar marcado
como procesado sin haber sido contestado.
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
from apps.core_api.services.routing_service import Decision, Ruta
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
    agents = AgentRepository()

    async def resolver(intent, confidence):
        agent = await agents.get_default_active()
        if agent is None:
            return Ruta(decision=Decision.SIN_DESTINO)
        return Ruta(decision=Decision.AGENTE, agent_name=agent.name)

    pipeline = MessagePipeline(
        settings=Settings(_env_file=None),
        conversations=ConversationService(),
        agents=agents,
        router=AgentRouter(RuleBasedClassifier(), resolver),
        llm=FakeLLMProvider(),
        messaging=messaging,
    )
    return pipeline, messaging


def entrante(external_id: str, texto: str) -> IncomingMessage:
    return IncomingMessage(
        external_id=external_id,
        contact_phone="+5215550003333",
        type="text",
        content=texto,
    )


def test_rafaga_de_tres_mensajes_recibe_tres_respuestas(entorno):
    pipeline, messaging = entorno

    resultados = [
        async_to_sync(pipeline.handle_inbound)(entrante("wamid.R1", "hola")),
        async_to_sync(pipeline.handle_inbound)(entrante("wamid.R2", "quiero pedir")),
        async_to_sync(pipeline.handle_inbound)(entrante("wamid.R3", "2 cajas de atún")),
    ]

    estados = [r.status for r in resultados]
    assert estados == ["processed", "processed", "processed"], (
        f"algún mensaje de la ráfaga no se procesó: {estados}"
    )

    # Lo esencial: tres preguntas, tres respuestas enviadas.
    assert len(messaging.sent) == 3, (
        "el cliente escribió tres veces y recibió "
        f"{len(messaging.sent)} respuestas: {messaging.sent}"
    )

    entrantes = Message.objects.filter(direction="in").order_by("id")
    assert entrantes.count() == 3
    assert all(m.status == MessageStatus.PROCESSED for m in entrantes)

    salientes = Message.objects.filter(direction="out").order_by("id")
    assert salientes.count() == 3, "falta la respuesta de algún mensaje de la ráfaga"
    assert all(m.status == MessageStatus.SENT for m in salientes)


def test_rafaga_en_modo_produccion_responde_a_todos(entorno):
    """El camino real de producción: el webhook registra los tres mensajes de la
    ráfaga y confirma, y solo después el worker los procesa. Los tres entrantes
    existen ya cuando se genera la primera respuesta."""
    pipeline, messaging = entorno

    registros = [
        async_to_sync(pipeline.register)(entrante("wamid.P1", "hola")),
        async_to_sync(pipeline.register)(entrante("wamid.P2", "quiero pedir")),
        async_to_sync(pipeline.register)(entrante("wamid.P3", "2 cajas de atún")),
    ]

    resultados = [
        async_to_sync(pipeline.process_message)(r.message_id) for r in registros
    ]

    estados = [r.status for r in resultados]
    assert estados == ["processed", "processed", "processed"], (
        f"la ráfaga se descartó a medias: {estados}"
    )
    assert len(messaging.sent) == 3, (
        "el cliente escribió tres veces y recibió "
        f"{len(messaging.sent)} respuestas: {messaging.sent}"
    )
    assert Message.objects.filter(direction="out").count() == 3


def test_cada_respuesta_queda_atada_a_su_mensaje(entorno):
    """Sin una atadura explícita entrante→saliente no se puede saber si un
    mensaje concreto ya fue respondido, que es lo que decide si un reintento
    debe reenviar o callarse."""
    pipeline, messaging = entorno

    async_to_sync(pipeline.handle_inbound)(entrante("wamid.S1", "hola"))
    async_to_sync(pipeline.handle_inbound)(entrante("wamid.S2", "quiero pedir"))

    primero = Message.objects.get(external_id="wamid.S1")
    segundo = Message.objects.get(external_id="wamid.S2")

    respuestas = {m.reply_to_id: m for m in Message.objects.filter(direction="out")}
    assert primero.id in respuestas, "la respuesta al primer mensaje no lo referencia"
    assert segundo.id in respuestas, "la respuesta al segundo mensaje no lo referencia"


def test_reintento_del_mismo_mensaje_no_manda_segunda_respuesta(entorno):
    """Un redelivery de Celery del mismo mensaje ya respondido no puede
    escribirle otra vez al cliente."""
    pipeline, messaging = entorno

    async_to_sync(pipeline.handle_inbound)(entrante("wamid.T1", "quiero pedir"))
    assert len(messaging.sent) == 1

    inbound = Message.objects.get(external_id="wamid.T1")
    # El worker reejecuta la tarea sobre el mismo mensaje ya persistido.
    resultado = async_to_sync(pipeline.process_message)(inbound.id)

    assert resultado.status in {"duplicate", "already_replied"}
    assert len(messaging.sent) == 1, "el cliente recibió la respuesta dos veces"
    assert Message.objects.filter(direction="out").count() == 1


def test_mensaje_fallido_se_reintenta_y_se_envia_una_sola_vez(entorno):
    """Si el envío falló, el reintento debe completar el envío (no descartarlo)
    y dejar una sola respuesta."""
    pipeline, messaging = entorno

    fallos = {"n": 0}
    original = messaging.send_text

    async def falla_la_primera(to, text):
        fallos["n"] += 1
        if fallos["n"] == 1:
            raise RuntimeError("YCloud caído")
        return await original(to, text)

    messaging.send_text = falla_la_primera

    with pytest.raises(RuntimeError):
        async_to_sync(pipeline.handle_inbound)(entrante("wamid.U1", "quiero pedir"))

    inbound = Message.objects.get(external_id="wamid.U1")
    assert inbound.status == MessageStatus.FAILED

    resultado = async_to_sync(pipeline.process_message)(inbound.id)

    assert resultado.status == "processed"
    assert len(messaging.sent) == 1
    assert Message.objects.filter(direction="out", status=MessageStatus.SENT).count() == 1
