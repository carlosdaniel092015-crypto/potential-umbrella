from django.core.exceptions import ImproperlyConfigured

from apps.admin_panel.settings.base import *  # noqa: F401,F403
from apps.admin_panel.settings.base import SECRET_KEY as _SECRET_KEY

DEBUG = False

# Fail-fast: arrancar producción sin clave (o con la de desarrollo) permitiría
# forjar sesiones de administrador del panel.
if not _SECRET_KEY or _SECRET_KEY == "inseguro-solo-dev":  # noqa: S105
    raise ImproperlyConfigured("DJANGO_SECRET_KEY es obligatoria en producción")
SECRET_KEY = _SECRET_KEY

# TLS terminado en el reverse proxy/ingress; Django asume HTTPS detrás de él.
SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
SESSION_COOKIE_SECURE = True
CSRF_COOKIE_SECURE = True
SECURE_HSTS_SECONDS = 31536000
SECURE_HSTS_INCLUDE_SUBDOMAINS = True

# Pendiente Fase 6 (docs/decisiones-fase-1.md): MFA (paquete concreto), bloqueo
# tras intentos fallidos, IP allowlist/VPN delante del panel.
