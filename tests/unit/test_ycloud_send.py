import json

import httpx
import pytest

from apps.core_api.services.resilience import CircuitOpenError
from apps.core_api.services.ycloud_service import YCloudMessagingProvider


def make_provider(handler) -> YCloudMessagingProvider:
    return YCloudMessagingProvider(
        api_key="test-key",
        base_url="https://api.ycloud.example/v2",
        from_number="+5215550009999",
        transport=httpx.MockTransport(handler),
    )


async def test_envio_arma_el_request_correcto():
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["api_key"] = request.headers.get("X-API-Key")
        captured["body"] = json.loads(request.content)
        return httpx.Response(200, json={"id": "wamid.OUT-1"})

    provider = make_provider(handler)
    message_id = await provider.send_text("+5215550001111", "Hola")

    assert message_id == "wamid.OUT-1"
    assert captured["url"] == "https://api.ycloud.example/v2/whatsapp/messages"
    assert captured["api_key"] == "test-key"
    assert captured["body"] == {
        "from": "+5215550009999",
        "to": "+5215550001111",
        "type": "text",
        "text": {"body": "Hola"},
    }


async def test_reintenta_ante_500_y_termina_bien():
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] < 3:
            return httpx.Response(500, json={"error": "interno"})
        return httpx.Response(200, json={"id": "wamid.OUT-2"})

    provider = make_provider(handler)
    assert await provider.send_text("+521555", "Hola") == "wamid.OUT-2"
    assert calls["n"] == 3


async def test_no_reintenta_errores_4xx():
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(401, json={"error": "api key inválida"})

    provider = make_provider(handler)
    with pytest.raises(httpx.HTTPStatusError):
        await provider.send_text("+521555", "Hola")
    assert calls["n"] == 1  # determinista: reintentar solo gastaría intentos


async def test_reintenta_429():
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] == 1:
            return httpx.Response(429, json={"error": "demasiadas peticiones"})
        return httpx.Response(200, json={"id": "wamid.OUT-3"})

    provider = make_provider(handler)
    assert await provider.send_text("+521555", "Hola") == "wamid.OUT-3"
    assert calls["n"] == 2


async def test_respuesta_200_sin_id_devuelve_cadena_vacia():
    # Contrato documentado: el mensaje salió; no habrá id para conciliar
    # callbacks de estado, y eso se registra sin romper el envío.
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"status": "accepted"})

    provider = make_provider(handler)
    assert await provider.send_text("+521555", "Hola") == ""


async def test_breaker_abre_tras_fallos_persistentes():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503, json={"error": "caído"})

    provider = make_provider(handler)
    # Cada send_text agota 3 intentos = 1 fallo para el breaker (umbral 5).
    for _ in range(5):
        with pytest.raises(httpx.HTTPStatusError):
            await provider.send_text("+521555", "Hola")
    with pytest.raises(CircuitOpenError):
        await provider.send_text("+521555", "Hola")
