from apps.core_api.services.ycloud_service import parse_incoming_message


def test_parsea_mensaje_de_texto(ycloud_inbound_text):
    msg = parse_incoming_message(ycloud_inbound_text)
    assert msg is not None
    assert msg.external_id == "wamid.TEST-TEXT-001"
    assert msg.contact_phone == "+5215550001111"
    assert msg.contact_name == "Juan Vendedor"
    assert msg.type == "text"
    assert msg.content == "Hola, quiero hacer un pedido de 2 cajas"


def test_parsea_audio_como_tipo_audio_sin_contenido(ycloud_inbound_audio):
    msg = parse_incoming_message(ycloud_inbound_audio)
    assert msg is not None
    assert msg.type == "audio"
    assert msg.content == ""


def test_callback_de_estado_devuelve_none(ycloud_status_update):
    assert parse_incoming_message(ycloud_status_update) is None


def test_payload_sin_estructura_devuelve_none():
    assert parse_incoming_message({}) is None
    assert parse_incoming_message({"type": "whatsapp.inbound_message.received"}) is None
    assert parse_incoming_message("no es un dict") is None  # type: ignore[arg-type]


def test_mensaje_sin_id_o_telefono_devuelve_none(ycloud_inbound_text):
    sin_id = {**ycloud_inbound_text}
    sin_id["whatsappInboundMessage"] = {
        **ycloud_inbound_text["whatsappInboundMessage"],
        "id": None,
    }
    assert parse_incoming_message(sin_id) is None
