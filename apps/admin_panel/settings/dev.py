from apps.admin_panel.settings.base import *  # noqa: F401,F403
from apps.admin_panel.settings.base import SECRET_KEY as _SECRET_KEY

DEBUG = True

# Clave de desarrollo: solo aquí, nunca en base.py (donde prod la heredaría).
SECRET_KEY = _SECRET_KEY or "inseguro-solo-dev"  # noqa: S105
