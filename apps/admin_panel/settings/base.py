"""Settings base de Django. dev.py / test.py / prod.py heredan de aquí."""

import os
from pathlib import Path

from django.core.exceptions import ImproperlyConfigured

BASE_DIR = Path(__file__).resolve().parents[3]

# Sin default: dev.py/test.py ponen uno de desarrollo, prod.py aborta si falta.
# Un default inseguro compartido permitiría forjar cookies de sesión del panel.
SECRET_KEY = os.environ.get("DJANGO_SECRET_KEY", "")
DEBUG = os.environ.get("DJANGO_DEBUG", "false").lower() == "true"

# Guarda anti-despliegue-accidental: si el entorno se declara productivo, los
# settings de desarrollo no pueden estar activos (DEBUG expone secretos y PII).
if os.environ.get("ENVIRONMENT", "dev") in {"production", "prod", "staging"}:
    _module = os.environ.get("DJANGO_SETTINGS_MODULE", "")
    if _module.endswith((".dev", ".test")):
        raise ImproperlyConfigured(
            f"ENVIRONMENT={os.environ['ENVIRONMENT']} con DJANGO_SETTINGS_MODULE={_module}: "
            "usar apps.admin_panel.settings.prod"
        )
ALLOWED_HOSTS = [
    h.strip()
    for h in os.environ.get("DJANGO_ALLOWED_HOSTS", "localhost,127.0.0.1").split(",")
    if h.strip()
]

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    # Modelos compartidos (dueño único del esquema, ADR-06)
    "apps.shared",
    # Apps del panel (solo registran admin sobre los modelos compartidos)
    "apps.admin_panel.agents",
    "apps.admin_panel.conversations",
    "apps.admin_panel.training",
    "apps.admin_panel.config",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "apps.admin_panel.urls"
WSGI_APPLICATION = "apps.admin_panel.wsgi.application"
ASGI_APPLICATION = "apps.admin_panel.asgi.application"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    },
]


def _database_config() -> dict:
    engine = os.environ.get("DB_ENGINE", "postgres")
    if engine == "sqlite":
        return {
            "ENGINE": "django.db.backends.sqlite3",
            "NAME": BASE_DIR / "db.sqlite3",
        }
    return {
        "ENGINE": "django.db.backends.postgresql",
        "NAME": os.environ.get("DB_NAME", "whatsapp_ai"),
        "USER": os.environ.get("DB_USER", "whatsapp_ai"),
        "PASSWORD": os.environ.get("DB_PASSWORD", ""),
        "HOST": os.environ.get("DB_HOST", "localhost"),
        "PORT": os.environ.get("DB_PORT", "5432"),
    }


DATABASES = {"default": _database_config()}

AUTH_USER_MODEL = "shared.User"

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

LANGUAGE_CODE = "es"
TIME_ZONE = "UTC"
USE_I18N = True
USE_TZ = True

STATIC_URL = "static/"
STATIC_ROOT = BASE_DIR / "staticfiles"

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

# Default del modelo LLM por agente (Agent.llm_model) — no quemado en migración.
DEFAULT_LLM_MODEL = os.environ.get("LLM_MODEL", "gpt-4.1")

# Sesiones del panel: expiración corta (§5.2 Auth Failures).
SESSION_COOKIE_AGE = 60 * 60 * 8  # 8 horas
SESSION_COOKIE_HTTPONLY = True
