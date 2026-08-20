"""Settings de test: SQLite en archivo (no :memory:) porque los tests de
integración ejecutan la app FastAPI en un hilo distinto al del test
(TestClient) y una DB en memoria no se comparte entre hilos."""

from apps.admin_panel.settings.base import *  # noqa: F401,F403
from apps.admin_panel.settings.base import BASE_DIR

DEBUG = False
SECRET_KEY = "test-secret-key"  # noqa: S105 — solo tests

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": BASE_DIR / "test_db.sqlite3",
    }
}

PASSWORD_HASHERS = ["django.contrib.auth.hashers.MD5PasswordHasher"]
