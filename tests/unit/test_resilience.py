import pytest

from apps.core_api.services.resilience import CircuitBreaker, CircuitOpenError, retry_async


class FakeClock:
    """Reloj controlado: evita depender de tiempos reales (y de sleeps)."""

    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


async def test_retry_reintenta_y_termina_bien():
    intentos = {"n": 0}

    async def falla_dos_veces() -> str:
        intentos["n"] += 1
        if intentos["n"] < 3:
            raise ValueError("boom")
        return "ok"

    result = await retry_async(falla_dos_veces, attempts=3, base_delay=0.001)
    assert result == "ok"
    assert intentos["n"] == 3


async def test_retry_agota_y_relanza():
    async def siempre_falla() -> None:
        raise ValueError("boom")

    with pytest.raises(ValueError):
        await retry_async(siempre_falla, attempts=2, base_delay=0.001)


async def test_should_retry_false_no_reintenta():
    intentos = {"n": 0}

    async def falla() -> None:
        intentos["n"] += 1
        raise ValueError("determinista")

    with pytest.raises(ValueError):
        await retry_async(
            falla, attempts=3, base_delay=0.001, should_retry=lambda exc: False
        )
    assert intentos["n"] == 1


async def test_breaker_abre_tras_umbral():
    clock = FakeClock()
    breaker = CircuitBreaker(failure_threshold=2, reset_timeout=30, clock=clock)

    async def falla() -> None:
        raise RuntimeError("integración caída")

    for _ in range(2):
        with pytest.raises(RuntimeError):
            await breaker.call(falla)

    async def ok() -> str:
        return "ok"

    with pytest.raises(CircuitOpenError):
        await breaker.call(ok)


async def test_half_open_solo_permite_una_sonda():
    clock = FakeClock()
    breaker = CircuitBreaker(failure_threshold=1, reset_timeout=30, clock=clock)

    async def falla() -> None:
        raise RuntimeError("caída")

    with pytest.raises(RuntimeError):
        await breaker.call(falla)

    clock.advance(31)  # vence el tiempo de espera: entra en half-open

    # La primera llamada pasa como sonda; mientras esté en vuelo, el resto no.
    async def lenta_y_falla() -> None:
        assert breaker.is_open  # otra llamada concurrente sería rechazada
        raise RuntimeError("sigue caída")

    with pytest.raises(RuntimeError):
        await breaker.call(lenta_y_falla)

    # La sonda falló: circuito reabierto.
    async def ok() -> str:
        return "ok"

    with pytest.raises(CircuitOpenError):
        await breaker.call(ok)


async def test_sonda_exitosa_cierra_el_circuito():
    clock = FakeClock()
    breaker = CircuitBreaker(failure_threshold=1, reset_timeout=30, clock=clock)

    async def falla() -> None:
        raise RuntimeError("caída")

    with pytest.raises(RuntimeError):
        await breaker.call(falla)

    clock.advance(31)

    async def ok() -> str:
        return "ok"

    assert await breaker.call(ok) == "ok"
    assert not breaker.is_open
    assert await breaker.call(ok) == "ok"
