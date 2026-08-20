"""Casos borde del turno por conversación, todos reproducidos por la revisión
adversarial del cambio anterior.

Cada prueba de aquí falló antes de su arreglo: son los agujeros que la primera
versión del turno dejó abiertos.
"""

from datetime import timedelta

import pytest
from asgiref.sync import async_to_sync
from django.utils import timezone

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

TELEFONO = "+5215550005555"


@pytest.fixture(autouse=True)
def agente():
    return Agent.objects.create(name="ventas", system_prompt="Vendes.", is_active=True)


def construir(messaging=None, llm=None):
    messaging = messaging or FakeMessagingProvider()
    llm = llm or FakeLLMProvider()
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
        external_id=external_id, contact_phone=TELEFONO, type="text", content=texto
    )


def test_un_predecesor_fallido_pendiente_de_reintento_bloquea(entorno=None):
    """Un mensaje que falló al enviar sigue en cola de reintento. Si no bloquea,
    su sucesor responde primero y el cliente recibe las respuestas al revés."""
    messaging = FakeMessagingProvider()
    fallos = {"n": 0}
    original = messaging.send_text

    async def falla_la_primera(to, text):
        fallos["n"] += 1
        if fallos["n"] == 1:
            raise RuntimeError("YCloud caído")
        return await original(to, text)

    messaging.send_text = falla_la_primera
    pipeline, _, _ = construir(messaging=messaging)

    r1 = async_to_sync(pipeline.register)(entrante("wamid.F1", "hola"))
    r2 = async_to_sync(pipeline.register)(entrante("wamid.F2", "quiero pedir"))

    with pytest.raises(RuntimeError):
        async_to_sync(pipeline.process_message)(r1.message_id)
    assert Message.objects.get(id=r1.message_id).status == MessageStatus.FAILED

    resultado = async_to_sync(pipeline.process_message)(r2.message_id)

    assert resultado.status == "esperando_turno", (
        "el segundo mensaje adelantó a uno que aún va a reintentarse"
    )
    assert messaging.sent == []


def test_un_backlog_viejo_sigue_respetando_el_orden():
    """Al drenar la cola tras una caída del worker, todos los mensajes son
    viejos. Es justo cuando el orden más importa, y la primera versión lo
    desactivaba por completo."""
    pipeline, messaging, _ = construir()

    r1 = async_to_sync(pipeline.register)(entrante("wamid.G1", "hola"))
    r2 = async_to_sync(pipeline.register)(entrante("wamid.G2", "quiero pedir"))
    r3 = async_to_sync(pipeline.register)(entrante("wamid.G3", "10 galones"))

    # El worker estuvo caído 20 minutos: la ráfaga entera envejeció.
    viejo = timezone.now() - timedelta(minutes=20)
    Message.objects.filter(
        id__in=[r1.message_id, r2.message_id, r3.message_id]
    ).update(created_at=viejo)

    resultado = async_to_sync(pipeline.process_message)(r3.message_id)

    assert resultado.status == "esperando_turno", (
        "un backlog viejo desactivó el turno y las respuestas salen sin verse"
    )
    assert messaging.sent == []


def test_un_mensaje_reclamado_y_abandonado_libera_la_conversacion():
    """Contrapartida del anterior: lo que caduca es el reclamo, no la espera.
    Un mensaje que quedó reclamado y nunca terminó deja de bloquear."""
    pipeline, messaging, _ = construir()

    r1 = async_to_sync(pipeline.register)(entrante("wamid.H1", "hola"))
    r2 = async_to_sync(pipeline.register)(entrante("wamid.H2", "quiero pedir"))

    # Zombi: reclamado hace 30 minutos y nunca terminado.
    Message.objects.filter(id=r1.message_id).update(
        status=MessageStatus.PROCESSING,
        processing_started_at=timezone.now() - timedelta(minutes=30),
    )

    resultado = async_to_sync(pipeline.process_message)(r2.message_id)

    assert resultado.status == "processed"
    assert len(messaging.sent) == 1
    # Y el zombi queda señalado para conciliación, no olvidado.
    assert Message.objects.get(id=r1.message_id).status == MessageStatus.PROCESSING


def test_el_modelo_no_ve_respuestas_que_nunca_salieron():
    """Una respuesta que quedó sin enviar no es parte de la conversación: si
    entra al prompt, el bot da por leído algo que el cliente nunca recibió."""
    messaging = FakeMessagingProvider()
    fallos = {"n": 0}
    original = messaging.send_text

    async def falla_la_primera(to, text):
        fallos["n"] += 1
        if fallos["n"] == 1:
            raise RuntimeError("YCloud caído")
        return await original(to, text)

    messaging.send_text = falla_la_primera
    llm = FakeLLMProvider()
    pipeline, _, _ = construir(messaging=messaging, llm=llm)

    r1 = async_to_sync(pipeline.register)(entrante("wamid.I1", "hola"))
    with pytest.raises(RuntimeError):
        async_to_sync(pipeline.process_message)(r1.message_id)

    saliente = Message.objects.get(direction="out")
    assert saliente.status == MessageStatus.SEND_FAILED

    # Se procesa otro mensaje: el historial no debe incluir la respuesta perdida.
    r2 = async_to_sync(pipeline.register)(entrante("wamid.I2", "quiero pedir"))
    Message.objects.filter(id=r1.message_id).update(status=MessageStatus.PROCESSED)
    async_to_sync(pipeline.process_message)(r2.message_id)

    ultimo_prompt = llm.calls[-1]["messages"]
    roles_asistente = [m for m in ultimo_prompt if m["role"] == "assistant"]
    assert roles_asistente == [], (
        "el prompt incluye una respuesta que nunca llegó al cliente: "
        f"{roles_asistente}"
    )


def test_el_reintento_reenvia_el_texto_ya_generado_no_uno_nuevo():
    """Si el envío falló de forma ambigua, regenerar la respuesta con el modelo
    puede producir un texto distinto del que quizá sí llegó al cliente."""
    messaging = FakeMessagingProvider()
    fallos = {"n": 0}
    original = messaging.send_text

    async def falla_la_primera(to, text):
        fallos["n"] += 1
        if fallos["n"] == 1:
            raise RuntimeError("tiempo de espera agotado")
        return await original(to, text)

    messaging.send_text = falla_la_primera

    respuestas = iter(["Primera versión de la respuesta", "Segunda versión distinta"])

    class LLMQueCambia:
        def __init__(self):
            self.calls = []

        async def complete(self, system_prompt, messages, model, temperature=0.4):
            self.calls.append({"messages": messages})
            return next(respuestas)

    llm = LLMQueCambia()
    pipeline, _, _ = construir(messaging=messaging, llm=llm)

    r1 = async_to_sync(pipeline.register)(entrante("wamid.J1", "quiero pedir"))
    with pytest.raises(RuntimeError):
        async_to_sync(pipeline.process_message)(r1.message_id)

    async_to_sync(pipeline.process_message)(r1.message_id)

    assert len(messaging.sent) == 1
    assert messaging.sent[0]["text"] == "Primera versión de la respuesta", (
        "el reintento envió un texto distinto del que ya se había generado"
    )
