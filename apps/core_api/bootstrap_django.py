"""Arranque del ORM de Django dentro de FastAPI/Celery (ADR-06).

Debe llamarse ANTES de importar cualquier módulo que importe apps.shared.models.
Regla del proyecto: en contexto async, el ORM se usa solo vía métodos async
(aget, acreate, ...) o sync_to_async; nunca llamadas síncronas directas en un
handler async (bloquean el event loop y Django lanza SynchronousOnlyOperation).
"""

import os

import django
from django.apps import apps as django_apps


def setup_django() -> None:
    if django_apps.ready:
        return
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "apps.admin_panel.settings.dev")
    django.setup()
