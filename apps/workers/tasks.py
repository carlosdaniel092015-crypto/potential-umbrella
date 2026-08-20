import logging

from asgiref.sync import async_to_sync
from celery.signals import task_postrun, task_prerun

from apps.workers.celery_app import app

logger = logging.getLogger(__name__)


def _close_stale_connections() -> None:
    """Celery no emite las señales de request de Django, así que nadie recicla
    las conexiones del hilo del ORM: una conexión caída (idle timeout, reinicio
    de Postgres) dejaría al worker fallando indefinidamente."""
    from asgiref.sync import sync_to_async
    from django.db import close_old_connections

    # Debe correr en el mismo hilo que usa el ORM vía sync_to_async.
    async_to_sync(sync_to_async(close_old_connections))()


@task_prerun.connect
def _on_task_prerun(**kwargs) -> None:
    _close_stale_connections()


@task_postrun.connect
def _on_task_postrun(**kwargs) -> None:
    _close_stale_connections()


@app.task(
    name="process_inbound_message",
    autoretry_for=(Exception,),
    retry_backoff=True,
    retry_backoff_max=60,
    max_retries=3,
)
def process_inbound_message(message_id: int) -> dict:
    """Procesa un mensaje ya persistido, fuera del request del webhook (§1.1).

    Recibe solo el id: el contenido y el teléfono se rehidratan desde Postgres,
    así el broker no almacena PII. Idempotente: el reclamo atómico del pipeline
    descarta un redelivery cuyo mensaje ya está en curso o procesado (D-03).
    """
    from apps.core_api.deps import get_worker_pipeline

    # async_to_sync (no asyncio.run): reutiliza el executor de asgiref y
    # funciona también con CELERY_TASK_ALWAYS_EAGER en pruebas.
    result = async_to_sync(get_worker_pipeline().process_message)(message_id)
    return result.model_dump()
