"""Flujo completo: POST firmado al webhook → pipeline inline → ORM real
(SQLite de test) → envío por FakeMessagingProvider. Verifica firma,
idempotencia, persistencia y la respuesta al cliente."""

import json

import pytest
from fastapi.testclient import TestClient

from apps.core_api.config import Settings, get_settings
from apps.core_api.deps import get_pipeline
from apps.core_api.graphs.classifiers import RuleBasedClassifier
from apps.core_api.graphs.router_graph import AgentRouter
from apps.core_api.main import create_app
from apps.core_api.services.agent_repository import AgentRepository
from apps.core_api.services.conversation_service import ConversationService
from apps.core_api.services.llm_service import FakeLLMProvider
from apps.core_api.services.pipeline import MessagePipeline
from apps.core_api.services.ycloud_service import FakeMessagingProvider
from apps.shared.enums import MessageStatus
from apps.shared.models import Agent, Contact, Conversation, Message
from tests.conftest import TEST_WEBHOOK_SECRET, sign_payload

pytestmark = pytest.mark.django_db(transaction=True)


def make_settings(**overrides) -> Settings:
    base = {
        "ycloud_webhook_secret": TEST_WEBHOOK_SECRET,
        "webhook_signature_required": True,
        "processing_mode": "inline",
        "messaging_provider": "fake",
        "llm_provider": "fake",
    }
    return Settings(_env_file=None, **{**base, **overrides})


@pytest.fixture
def entorno():
    settings = make_settings()
    messaging = FakeMessagingProvider()
    llm = FakeLLMProvider(canned_reply="¡Hola! Soy el asistente de ventas.")
    agents = AgentRepository()

    async def resolver(intent, confidence):
        agent = await agents.get_default_active()
        return agent.name if agent else None

    pipeline = MessagePipeline(
        settings=settings,
        conversations=ConversationService(),
        agents=agents,
        router=AgentRouter(RuleBasedClassifier(), resolver),
        llm=llm,
        messaging=messaging,
    )
    app = create_app()
    app.dependency_overrides[get_settings] = lambda: settings
    app.dependency_overrides[get_pipeline] = lambda: pipeline
    client = TestClient(app)
    return client, messaging, llm


def post_signed(client: TestClient, payload: dict, secret: str = TEST_WEBHOOK_SECRET):
    raw = json.dumps(payload).encode()
    return client.post(
        "/webhooks/ycloud",
        content=raw,
        headers={
            "Content-Type": "application/json",
            "X-YCloud-Signature": sign_payload(raw, secret),
        },
    )


def crear_agente() -> Agent:
    return Agent.objects.create(
        name="ventas",
        system_prompt="Eres el asistente de ventas de Sued Fargesa.",
        is_active=True,
    )


def test_flujo_completo_texto(entorno, ycloud_inbound_text):
    client, messaging, llm = entorno
    agente = crear_agente()

    response = post_signed(client, ycloud_inbound_text)

    assert response.status_code == 200
    assert response.json() == {"status": "processed", "agent": "ventas"}

    # El prompt persistido en el panel y el texto del cliente llegan al modelo.
    assert llm.calls[0]["system_prompt"] == agente.system_prompt
    assert llm.calls[0]["messages"][-1] == {
        "role": "user",
        "content": "Hola, quiero hacer un pedido de 2 cajas",
    }

    # Envío real al contacto vía el provider
    assert messaging.sent == [
        {"to": "+5215550001111", "text": "¡Hola! Soy el asistente de ventas."}
    ]

    # Persistencia: contacto, conversación y ambos mensajes
    contact = Contact.objects.get(phone="+5215550001111")
    assert contact.name == "Juan Vendedor"
    conversation = Conversation.objects.get(contact=contact)
    assert conversation.last_inbound_at is not None
    inbound = Message.objects.get(external_id="wamid.TEST-TEXT-001")
    assert inbound.status == MessageStatus.PROCESSED
    outbound = Message.objects.get(direction="out")
    assert outbound.status == MessageStatus.SENT
    assert outbound.agent.name == "ventas"


def test_webhook_duplicado_es_idempotente(entorno, ycloud_inbound_text):
    client, messaging, _ = entorno
    crear_agente()

    first = post_signed(client, ycloud_inbound_text)
    second = post_signed(client, ycloud_inbound_text)

    assert first.json()["status"] == "processed"
    assert second.json()["status"] == "duplicate"
    assert len(messaging.sent) == 1  # una sola respuesta al cliente
    assert Message.objects.filter(direction="in").count() == 1


def test_firma_invalida_da_401_y_no_procesa(entorno, ycloud_inbound_text):
    client, messaging, _ = entorno
    crear_agente()

    response = post_signed(client, ycloud_inbound_text, secret="secreto-equivocado")

    assert response.status_code == 401
    assert messaging.sent == []
    assert Message.objects.count() == 0


def test_firma_ausente_da_401(entorno, ycloud_inbound_text):
    client, _, _ = entorno
    raw = json.dumps(ycloud_inbound_text).encode()
    response = client.post(
        "/webhooks/ycloud", content=raw, headers={"Content-Type": "application/json"}
    )
    assert response.status_code == 401


def test_json_invalido_da_400(entorno):
    client, _, _ = entorno
    raw = b"esto no es json"
    response = client.post(
        "/webhooks/ycloud",
        content=raw,
        headers={
            "Content-Type": "application/json",
            "X-YCloud-Signature": sign_payload(raw),
        },
    )
    assert response.status_code == 400


def test_callback_de_estado_se_ignora_sin_pipeline(entorno, ycloud_status_update):
    client, messaging, _ = entorno
    response = post_signed(client, ycloud_status_update)
    assert response.status_code == 200
    assert response.json()["status"] == "ignored"
    assert messaging.sent == []


def test_audio_recibe_mensaje_de_capacidad_no_disponible(entorno, ycloud_inbound_audio):
    client, messaging, _ = entorno
    crear_agente()

    response = post_signed(client, ycloud_inbound_audio)

    assert response.json()["status"] == "processed"
    assert len(messaging.sent) == 1
    assert "solo puedo procesar mensajes de texto" in messaging.sent[0]["text"]


def test_health_interno(entorno):
    client, _, _ = entorno
    response = client.get("/internal/health")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"


def test_payload_demasiado_grande_da_413_sin_persistir(entorno):
    client, messaging, _ = entorno
    raw = b'{"relleno": "' + b"x" * (256 * 1024) + b'"}'
    response = client.post(
        "/webhooks/ycloud",
        content=raw,
        headers={
            "Content-Type": "application/json",
            "X-YCloud-Signature": sign_payload(raw),
        },
    )
    assert response.status_code == 413
    assert Message.objects.count() == 0
    assert messaging.sent == []


def test_fallo_del_proveedor_da_500_para_que_ycloud_reintente(ycloud_inbound_text):
    # Contrato HTTP: si el pipeline falla, el webhook NO debe devolver 200,
    # o YCloud daría el mensaje por entregado y se perdería.
    class MessagingRoto:
        async def send_text(self, to, text):
            raise RuntimeError("YCloud caído")

    settings = make_settings()
    agents = AgentRepository()

    async def resolver(intent, confidence):
        agent = await agents.get_default_active()
        return agent.name if agent else None

    pipeline = MessagePipeline(
        settings=settings,
        conversations=ConversationService(),
        agents=agents,
        router=AgentRouter(RuleBasedClassifier(), resolver),
        llm=FakeLLMProvider(canned_reply="hola"),
        messaging=MessagingRoto(),
    )
    app = create_app()
    app.dependency_overrides[get_settings] = lambda: settings
    app.dependency_overrides[get_pipeline] = lambda: pipeline
    client = TestClient(app, raise_server_exceptions=False)
    crear_agente()

    response = post_signed(client, ycloud_inbound_text)

    assert response.status_code == 500
    inbound = Message.objects.get(direction="in")
    assert inbound.status == MessageStatus.FAILED
    outbound = Message.objects.get(direction="out")
    assert outbound.status == MessageStatus.SEND_FAILED


def test_reintento_en_vuelo_no_responde_dos_veces(entorno, ycloud_inbound_text):
    """El original está PROCESSING (otra ejecución en curso): el reintento de
    YCloud no debe disparar una segunda respuesta al cliente."""
    client, messaging, _ = entorno
    crear_agente()

    contact = Contact.objects.create(phone="+5215550001111")
    conversation = Conversation.objects.create(contact=contact, channel="whatsapp")
    Message.objects.create(
        conversation=conversation,
        direction="in",
        content="Hola, quiero hacer un pedido de 2 cajas",
        external_id="wamid.TEST-TEXT-001",
        status=MessageStatus.PROCESSING,
    )

    response = post_signed(client, ycloud_inbound_text)

    assert response.json()["status"] == "duplicate"
    assert messaging.sent == []
    assert Message.objects.filter(direction="in").count() == 1


def test_reintento_de_mensaje_fallido_si_se_reprocesa(entorno, ycloud_inbound_text):
    """Un mensaje que quedó FAILED sí es reprocesable: el reintento lo completa."""
    client, messaging, _ = entorno
    crear_agente()

    contact = Contact.objects.create(phone="+5215550001111")
    conversation = Conversation.objects.create(contact=contact, channel="whatsapp")
    Message.objects.create(
        conversation=conversation,
        direction="in",
        content="Hola, quiero hacer un pedido de 2 cajas",
        external_id="wamid.TEST-TEXT-001",
        status=MessageStatus.FAILED,
    )

    response = post_signed(client, ycloud_inbound_text)

    assert response.json()["status"] == "processed"
    assert len(messaging.sent) == 1
    assert Message.objects.get(direction="in").status == MessageStatus.PROCESSED


def test_una_sola_conversacion_abierta_por_contacto(entorno, ycloud_inbound_text):
    client, _, _ = entorno
    crear_agente()

    post_signed(client, ycloud_inbound_text)
    segundo = {**ycloud_inbound_text}
    segundo["whatsappInboundMessage"] = {
        **ycloud_inbound_text["whatsappInboundMessage"],
        "id": "wamid.TEST-TEXT-002",
    }
    post_signed(client, segundo)

    assert Conversation.objects.count() == 1
    assert Message.objects.filter(direction="in").count() == 2


def test_modo_celery_encola_solo_el_id_y_no_responde_en_el_webhook(
    entorno, ycloud_inbound_text, monkeypatch
):
    """En producción el webhook confirma y delega: sin PII en la cola."""
    client, messaging, _ = entorno
    crear_agente()

    encolados: list = []

    class FakeTask:
        @staticmethod
        def delay(message_id):
            encolados.append(message_id)

    import apps.workers.tasks as tasks_module

    monkeypatch.setattr(tasks_module, "process_inbound_message", FakeTask)
    celery_settings = make_settings(processing_mode="celery")
    client.app.dependency_overrides[get_settings] = lambda: celery_settings

    response = post_signed(client, ycloud_inbound_text)

    assert response.json() == {"status": "queued"}
    assert messaging.sent == []  # el envío ocurre en el worker
    inbound = Message.objects.get(direction="in")
    assert inbound.status == MessageStatus.RECEIVED
    assert encolados == [inbound.id]  # solo el id: ni teléfono ni texto


def test_composicion_real_sin_overrides(ycloud_inbound_text, monkeypatch):
    """Ejercita deps.build_default_pipeline (la raíz de composición que el
    fixture normalmente sustituye), para que un fallo de wiring no pase."""
    from apps.core_api import deps

    for key, value in {
        "YCLOUD_WEBHOOK_SECRET": TEST_WEBHOOK_SECRET,
        "MESSAGING_PROVIDER": "fake",
        "LLM_PROVIDER": "fake",
        "PROCESSING_MODE": "inline",
        "ENVIRONMENT": "dev",
    }.items():
        monkeypatch.setenv(key, value)
    get_settings.cache_clear()
    deps.reset_pipeline()
    crear_agente()

    try:
        client = TestClient(create_app())
        response = post_signed(client, ycloud_inbound_text)
        assert response.status_code == 200
        assert response.json()["status"] == "processed"
        assert Message.objects.filter(direction="out", status=MessageStatus.SENT).exists()
    finally:
        deps.reset_pipeline()
        get_settings.cache_clear()
