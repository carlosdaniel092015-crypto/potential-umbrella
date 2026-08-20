# Imagen única multi-target (core_api / admin_panel / worker comparten deps).
# Hardening §5.2: usuario no-root, imagen slim, deps congeladas con uv.lock.

FROM python:3.12-slim AS base
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_PYTHON_DOWNLOADS=never
COPY --from=ghcr.io/astral-sh/uv:0.11.3 /uv /uvx /usr/local/bin/
WORKDIR /app
RUN useradd --create-home --uid 1000 appuser
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev
COPY apps ./apps
COPY manage.py ./
ENV PATH="/app/.venv/bin:$PATH"
USER appuser

FROM base AS core_api
EXPOSE 8000
# 1 worker por contenedor: el escalado es del orquestador (§4.4). Parametrizable.
CMD ["sh", "-c", "uvicorn apps.core_api.main:app --host 0.0.0.0 --port 8000 --workers ${UVICORN_WORKERS:-1}"]

FROM base AS admin_panel
EXPOSE 8000
CMD ["sh", "-c", "python manage.py collectstatic --noinput && gunicorn apps.admin_panel.wsgi:application --bind 0.0.0.0:8000 --workers ${GUNICORN_WORKERS:-2}"]

FROM base AS worker
CMD ["celery", "-A", "apps.workers.celery_app", "worker", "-l", "info", "-Q", "realtime,default"]
