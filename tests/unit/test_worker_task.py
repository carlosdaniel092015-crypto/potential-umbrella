"""La tarea del worker tenía cobertura cero, y ahí vivían los dos defectos más
graves del turno por conversación: el presupuesto de espera que mataba el
mensaje, y el `self.retry` que corrompía la política de reintentos del proceso.
"""

import pytest

from apps.core_api.config import Settings
from apps.core_api.schemas.messages import PipelineResult
from apps.workers import tasks


class PipelineFalso:
    def __init__(self, respuestas):
        self._respuestas = list(respuestas)
        self.llamadas: list[dict] = []

    async def process_message(self, message_id, forzar_turno=False):
        self.llamadas.append({"message_id": message_id, "forzar_turno": forzar_turno})
        return self._respuestas.pop(0)


@pytest.fixture
def entorno(monkeypatch):
    """Sustituye el pipeline y la publicación de tareas, sin broker."""
    encolados: list[dict] = []

    class ApplyAsyncFalso:
        @staticmethod
        def apply_async(args=None, countdown=None, **kwargs):
            encolados.append({"args": args, "countdown": countdown})

    monkeypatch.setattr(
        tasks.process_inbound_message, "apply_async", ApplyAsyncFalso.apply_async
    )
    return encolados


def preparar(monkeypatch, respuestas, **settings_extra):
    pipeline = PipelineFalso(respuestas)
    settings = Settings(_env_file=None, **settings_extra)
    monkeypatch.setattr("apps.core_api.deps.get_worker_pipeline", lambda: pipeline)
    monkeypatch.setattr("apps.core_api.config.get_settings", lambda: settings)
    return pipeline, settings


def test_procesado_normal_no_reencola(monkeypatch, entorno):
    preparar(monkeypatch, [PipelineResult(status="processed", agent_name="ventas")])

    resultado = tasks.process_inbound_message(7)

    assert resultado["status"] == "processed"
    assert entorno == []


def test_esperando_turno_publica_una_tarea_nueva(monkeypatch, entorno):
    """Una tarea nueva, no un retry: `self.retry` fija override_max_retries en
    la instancia de la tarea, que es un singleton del proceso worker, y con
    autoretry_for ese valor queda pegado y altera el límite de los fallos
    reales posteriores."""
    preparar(monkeypatch, [PipelineResult(status="esperando_turno")])

    resultado = tasks.process_inbound_message(7)

    assert resultado["status"] == "esperando_turno"
    assert len(entorno) == 1
    assert entorno[0]["args"] == (7, 1), "el contador de esperas no avanzó"
    assert entorno[0]["countdown"] > 0


def test_la_espera_crece_con_techo(monkeypatch, entorno):
    _, settings = preparar(
        monkeypatch,
        [PipelineResult(status="esperando_turno")] * 3,
        orden_reintento_segundos=2,
        orden_reintento_tope_segundos=10,
    )

    tasks.process_inbound_message(7, turno_intento=0)
    tasks.process_inbound_message(7, turno_intento=3)
    tasks.process_inbound_message(7, turno_intento=40)

    esperas = [e["countdown"] for e in entorno]
    assert esperas[0] == 2
    assert esperas[1] > esperas[0], "la espera no crece"
    assert esperas[2] == 10, "la espera no respeta el techo"


def test_al_agotar_el_presupuesto_se_procesa_sin_esperar(monkeypatch):
    """El defecto crítico: con un presupuesto más corto que el margen de
    abandono, el mensaje moría sin respuesta y nadie lo reencolaba. Ahora se
    procesa sin el predecesor, que es la misma decisión que toma el margen."""
    pipeline, _ = preparar(
        monkeypatch,
        [PipelineResult(status="processed", agent_name="ventas")],
        orden_margen_minutos=1,
        orden_reintento_segundos=2,
        orden_reintento_tope_segundos=10,
    )

    resultado = tasks.process_inbound_message(7, turno_intento=50)

    assert resultado["status"] == "processed"
    assert pipeline.llamadas[0]["forzar_turno"] is True, (
        "el mensaje no se procesó tras agotar la espera: queda sin responder"
    )


def test_el_presupuesto_cubre_el_margen_de_abandono(monkeypatch):
    """La espera acumulada tiene que alcanzar el margen: si el presupuesto es
    más corto, hay una ventana en la que el mensaje ni espera ni se procesa."""
    _, settings = preparar(monkeypatch, [], orden_margen_minutos=15)

    # Con el techo de 30 s, en 200 intentos se cubren de sobra los 900 s.
    acumulado = tasks._tiempo_esperado(200, settings)
    assert acumulado >= settings.orden_margen_minutos * 60


def test_la_tarea_no_esta_enlazada_a_su_propia_instancia():
    """Guarda estructural contra la regresión: una tarea sin `bind=True` no
    recibe `self`, así que no puede llamar a `self.retry(max_retries=...)`, que
    es lo que contaminaba el límite de reintentos del proceso worker entero.
    """
    import inspect

    parametros = list(inspect.signature(tasks.process_inbound_message.run).parameters)
    assert parametros == ["message_id", "turno_intento"], (
        f"la firma de la tarea cambió: {parametros}. Si se añadió bind=True, "
        "vuelve a estar al alcance el retry que corrompe el singleton."
    )
