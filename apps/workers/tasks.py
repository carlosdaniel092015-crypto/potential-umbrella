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


def _espera_del_turno(intento: int, base: float, tope: float) -> float:
    """Espera creciente con techo: sondear cada 2 s durante minutos amplifica la
    cola de tiempo real (una publicación por mensaje en espera y por intento)."""
    return min(base * (1.5**intento), tope)


@app.task(
    name="process_inbound_message",
    autoretry_for=(Exception,),
    retry_backoff=True,
    retry_backoff_max=60,
    max_retries=3,
)
def process_inbound_message(message_id: int, turno_intento: int = 0) -> dict:
    """Procesa un mensaje ya persistido, fuera del request del webhook (§1.1).

    Recibe solo el id: el contenido y el teléfono se rehidratan desde Postgres,
    así el broker no almacena PII. Idempotente: el reclamo atómico del pipeline
    descarta un redelivery cuyo mensaje ya está en curso o procesado (D-03).
    """
    from apps.core_api.config import get_settings
    from apps.core_api.deps import get_worker_pipeline

    settings = get_settings()
    presupuesto = settings.orden_margen_minutos * 60
    # Al agotarse la espera se procesa igual, sin el predecesor: es la misma
    # decisión que toma el margen. Morir aquí dejaría el mensaje sin responder
    # y sin nadie que lo reencole.
    forzar = _tiempo_esperado(turno_intento, settings) >= presupuesto

    # async_to_sync (no asyncio.run): reutiliza el executor de asgiref y
    # funciona también con CELERY_TASK_ALWAYS_EAGER en pruebas.
    result = async_to_sync(get_worker_pipeline().process_message)(
        message_id, forzar_turno=forzar
    )

    if result.status == "esperando_turno":
        # Hay un mensaje anterior del mismo cliente sin terminar. Se publica una
        # tarea NUEVA en lugar de usar el retry de Celery: `self.retry` fija
        # `override_max_retries` en la instancia de la tarea, que es un
        # singleton del proceso worker, y con `autoretry_for` ese valor queda
        # pegado y altera el límite de reintentos de los fallos reales
        # posteriores. Una tarea nueva trae su propio contador.
        espera = _espera_del_turno(
            turno_intento,
            settings.orden_reintento_segundos,
            settings.orden_reintento_tope_segundos,
        )
        process_inbound_message.apply_async(
            args=(message_id, turno_intento + 1), countdown=espera
        )
        logger.info(
            "Mensaje %s espera su turno (intento %s, %.0f s)",
            message_id,
            turno_intento + 1,
            espera,
        )
        return {"status": "esperando_turno", "reencolado_en": espera}

    return result.model_dump()


def _tiempo_esperado(intentos: int, settings) -> float:
    """Tiempo acumulado de espera tras N reencolados."""
    return sum(
        _espera_del_turno(i, settings.orden_reintento_segundos, settings.orden_reintento_tope_segundos)
        for i in range(intentos)
    )
