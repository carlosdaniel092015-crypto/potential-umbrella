import os

from celery import Celery

from apps.core_api.bootstrap_django import setup_django

# El worker usa el ORM (vía el pipeline): Django debe estar configurado antes
# de registrar tasks (ADR-06).
setup_django()

app = Celery(
    "whatsapp_ai_platform",
    broker=os.environ.get("CELERY_BROKER_URL", "redis://localhost:6379/1"),
    backend=os.environ.get("CELERY_RESULT_BACKEND", "redis://localhost:6379/2"),
)

app.conf.update(
    task_acks_late=True,  # con pipeline idempotente (D-03) un redelivery es seguro
    task_default_queue="default",
    # Colas separadas (§4.4): tiempo real vs trabajos de fondo.
    task_routes={"process_inbound_message": {"queue": "realtime"}},
    timezone="UTC",
)

app.autodiscover_tasks(["apps.workers"])
