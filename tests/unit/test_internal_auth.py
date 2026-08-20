"""Los endpoints internos viven en la app pública (la que recibe el webhook):
fuera de dev tienen que exigir token propio."""

import pytest
from fastapi import HTTPException

from apps.core_api.config import Settings
from apps.core_api.routers.internal import require_internal_access


async def test_dev_no_exige_token():
    await require_internal_access(None, Settings(_env_file=None, environment="dev"))


async def test_produccion_sin_token_configurado_falla():
    settings = Settings(_env_file=None, environment="production", internal_api_token="")
    with pytest.raises(HTTPException) as exc:
        await require_internal_access(None, settings)
    assert exc.value.status_code == 503


async def test_produccion_con_token_incorrecto_da_401():
    settings = Settings(
        _env_file=None, environment="production", internal_api_token="secreto"
    )
    with pytest.raises(HTTPException) as exc:
        await require_internal_access("equivocado", settings)
    assert exc.value.status_code == 401


async def test_produccion_con_token_correcto_pasa():
    settings = Settings(
        _env_file=None, environment="production", internal_api_token="secreto"
    )
    await require_internal_access("secreto", settings)
