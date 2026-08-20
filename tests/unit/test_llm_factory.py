import pytest

from apps.core_api.config import Settings
from apps.core_api.services.llm_service import (
    FakeLLMProvider,
    OpenAILLMProvider,
    build_llm_provider,
)


def test_por_defecto_devuelve_fake():
    provider = build_llm_provider(Settings(_env_file=None))
    assert isinstance(provider, FakeLLMProvider)


def test_openai_sin_api_key_falla_explicito():
    settings = Settings(_env_file=None, llm_provider="openai", openai_api_key="")
    with pytest.raises(ValueError, match="OPENAI_API_KEY"):
        build_llm_provider(settings)


def test_openai_con_api_key_construye_provider():
    settings = Settings(_env_file=None, llm_provider="openai", openai_api_key="sk-test")
    provider = build_llm_provider(settings)
    assert isinstance(provider, OpenAILLMProvider)


async def test_fake_sin_canned_incluye_ultimo_mensaje():
    provider = FakeLLMProvider()
    reply = await provider.complete(
        system_prompt="s", messages=[{"role": "user", "content": "hola mundo"}], model="m"
    )
    assert "hola mundo" in reply
    assert provider.calls[0]["model"] == "m"
