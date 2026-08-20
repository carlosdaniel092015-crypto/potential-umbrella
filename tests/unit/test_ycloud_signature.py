from apps.core_api.services.ycloud_service import mask_phone, verify_signature
from tests.conftest import TEST_WEBHOOK_SECRET, sign_payload


def test_firma_valida_sobre_bytes_crudos():
    raw = b'{"type": "whatsapp.inbound_message.received", "acentos": "\xc3\xa1\xc3\xa9"}'
    assert verify_signature(raw, sign_payload(raw), TEST_WEBHOOK_SECRET)


def test_firma_invalida():
    raw = b'{"a": 1}'
    assert not verify_signature(raw, "deadbeef" * 8, TEST_WEBHOOK_SECRET)


def test_firma_de_otro_body_no_valida():
    raw = b'{"a": 1}'
    otra = sign_payload(b'{"a": 2}')
    assert not verify_signature(raw, otra, TEST_WEBHOOK_SECRET)


def test_header_ausente():
    assert not verify_signature(b"{}", None, TEST_WEBHOOK_SECRET)


def test_secreto_vacio_nunca_valida():
    raw = b"{}"
    assert not verify_signature(raw, sign_payload(raw, ""), "")


def test_mask_phone_no_expone_numero_completo():
    assert mask_phone("+5215550001111") == "**********1111"
    assert mask_phone("123") == "****"
