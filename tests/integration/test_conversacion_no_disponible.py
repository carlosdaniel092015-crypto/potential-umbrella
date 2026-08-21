"""Qué pasa cuando la conversación no se puede tomar.

Dos causas: alguien retiene su bloqueo más de lo tolerado (con `lock_timeout`
configurado en Postgres, la sentencia se cancela), o se cerró entre que se
eligió y que se bloqueó. En ambos casos el mensaje no queda registrado, así que
la única respuesta honesta al proveedor es un 503 que le haga reintentar.
"""

import json

import pytest
from django.db import OperationalError
from fastapi.testclient import TestClient

from apps.core_api.config import Settings, get_settings
from apps.core_api.deps import get_pipeline
from apps.core_api.main import create_app
from apps.core_api.schemas.messages import IncomingMessage
from apps.core_api.services.conversation_service import (
    ConversacionNoDisponible,
    ConversationService,
)
from apps.shared.enums import ConversationStatus
from apps.shared.models import Agent, Contact, Conversation, Message
from tests.conftest import TEST_WEBHOOK_SECRET, sign_payload

pytestmark = pytest.mark.django_db(transaction=True)


@pytest.fixture(autouse=True)
def agente():
    return Agent.objects.create(name="ventas", system_prompt="Vendes.", is_active=True)


def entrante(external_id="wamid.X1") -> IncomingMessage:
    return IncomingMessage(
        external_id=external_id,
        contact_phone="+5215550001010",
        type="text",
        content="hola",
    )


async def test_una_espera_excesiva_por_el_bloqueo_no_revienta_el_registro(monkeypatch):
    """Simula lo que hace Postgres al agotarse `lock_timeout`: cancela la
    sentencia. El servicio lo traduce a un fallo con nombre propio."""
    servicio = ConversationService()

    def bloqueo_agotado(conversation_id):
        raise OperationalError("canceling statement due to lock timeout")

    monkeypatch.setattr(
        ConversationService, "_bloquear_conversacion", staticmethod(bloqueo_agotado)
    )

    with pytest.raises(ConversacionNoDisponible):
        await servicio.register_inbound(entrante())


async def test_una_conversacion_cerrada_en_medio_no_recibe_el_mensaje():
    """La conversación se elige antes del bloqueo. Si alguien la cierra en ese
    hueco, colgar el mensaje de ella partiría el historial."""
    servicio = ConversationService()
    contacto = await Contact.objects.acreate(phone="+5215550001010")
    conversacion = await Conversation.objects.acreate(
        contact=contacto, channel="whatsapp"
    )

    original = ConversationService._get_or_create_conversation

    def cerrarla_en_medio(contact, channel):
        elegida = original(contact, channel)
        Conversation.objects.filter(id=elegida.id).update(
            status=ConversationStatus.CLOSED
        )
        return elegida

    ConversationService._get_or_create_conversation = staticmethod(cerrarla_en_medio)
    try:
        with pytest.raises(ConversacionNoDisponible):
            await servicio.register_inbound(entrante())
    finally:
        ConversationService._get_or_create_conversation = staticmethod(original)

    assert await Message.objects.filter(conversation=conversacion).acount() == 0


def test_el_webhook_responde_503_para_que_el_proveedor_reintente(monkeypatch):
    settings = Settings(
        _env_file=None,
        ycloud_webhook_secret=TEST_WEBHOOK_SECRET,
        processing_mode="inline",
    )

    class PipelineQueNoPuede:
        async def register(self, incoming):
            raise ConversacionNoDisponible("bloqueo retenido")

        async def handle_inbound(self, incoming):
            raise ConversacionNoDisponible("bloqueo retenido")

    app = create_app()
    app.dependency_overrides[get_settings] = lambda: settings
    app.dependency_overrides[get_pipeline] = lambda: PipelineQueNoPuede()
    client = TestClient(app)

    payload = json.loads(
        (
            __import__("pathlib").Path("tests/fixtures/ycloud/inbound_text.json")
        ).read_text(encoding="utf-8")
    )
    raw = json.dumps(payload).encode()
    respuesta = client.post(
        "/webhooks/ycloud",
        content=raw,
        headers={
            "Content-Type": "application/json",
            "X-YCloud-Signature": sign_payload(raw),
        },
    )

    assert respuesta.status_code == 503, (
        "un 200 daría el mensaje por recibido y se perdería; un 500 mentiría "
        "sobre la causa"
    )


def test_la_configuracion_de_postgres_pone_tope_al_bloqueo():
    """Sin tope, una transacción ajena que retenga el bloqueo detiene la API
    entera, porque todas sus operaciones de base comparten un hilo."""
    import os

    from apps.admin_panel.settings.base import _database_config

    os.environ["DB_ENGINE"] = "postgres"
    try:
        config = _database_config()
    finally:
        os.environ["DB_ENGINE"] = "sqlite"

    assert "lock_timeout" in config["OPTIONS"]["options"]
