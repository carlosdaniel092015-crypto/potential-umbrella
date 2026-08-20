"""Guardas de configuración: un despliegue mal parametrizado debe abortar, no
arrancar con la clave de desarrollo (que permitiría forjar sesiones del panel)."""

import importlib
import sys

import pytest
from django.core.exceptions import ImproperlyConfigured


def _reload_settings(module: str):
    sys.modules.pop(module, None)
    return importlib.import_module(module)


def test_prod_sin_secret_key_aborta(monkeypatch):
    monkeypatch.delenv("DJANGO_SECRET_KEY", raising=False)
    _reload_settings("apps.admin_panel.settings.base")
    with pytest.raises(ImproperlyConfigured, match="DJANGO_SECRET_KEY"):
        _reload_settings("apps.admin_panel.settings.prod")


def test_prod_con_la_clave_de_desarrollo_aborta(monkeypatch):
    monkeypatch.setenv("DJANGO_SECRET_KEY", "inseguro-solo-dev")
    _reload_settings("apps.admin_panel.settings.base")
    with pytest.raises(ImproperlyConfigured):
        _reload_settings("apps.admin_panel.settings.prod")


def test_prod_con_clave_real_arranca(monkeypatch):
    monkeypatch.setenv("DJANGO_SECRET_KEY", "clave-larga-y-unica-de-produccion")
    _reload_settings("apps.admin_panel.settings.base")
    prod = _reload_settings("apps.admin_panel.settings.prod")
    assert prod.SECRET_KEY == "clave-larga-y-unica-de-produccion"
    assert prod.DEBUG is False
    assert prod.SESSION_COOKIE_SECURE is True


def test_entorno_productivo_con_settings_dev_aborta(monkeypatch):
    monkeypatch.setenv("ENVIRONMENT", "production")
    monkeypatch.setenv("DJANGO_SETTINGS_MODULE", "apps.admin_panel.settings.dev")
    with pytest.raises(ImproperlyConfigured, match="settings.prod"):
        _reload_settings("apps.admin_panel.settings.base")


def teardown_module() -> None:
    # Deja los settings del proceso de test como estaban.
    _reload_settings("apps.admin_panel.settings.base")
    _reload_settings("apps.admin_panel.settings.test")
