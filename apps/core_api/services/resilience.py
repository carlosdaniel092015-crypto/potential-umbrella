"""Retry con backoff exponencial + circuit breaker (§1.1).

Limitación conocida (documentada): el estado del breaker es por proceso. El
breaker compartido en Redis está planificado para cuando haya múltiples
réplicas (revisión: hallazgo 'circuit breaker sin diseño')."""

import asyncio
import time
from collections.abc import Awaitable, Callable
from typing import TypeVar

T = TypeVar("T")


class CircuitOpenError(RuntimeError):
    pass


class CircuitBreaker:
    def __init__(
        self,
        failure_threshold: int = 5,
        reset_timeout: float = 30.0,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.failure_threshold = failure_threshold
        self.reset_timeout = reset_timeout
        # Reloj inyectable: permite testear las transiciones sin dormir.
        self._clock = clock
        self._failures = 0
        self._opened_at: float | None = None
        self._probing = False

    @property
    def is_open(self) -> bool:
        if self._opened_at is None:
            return False
        if self._clock() - self._opened_at >= self.reset_timeout:
            # Vencido el tiempo de espera: half-open. Solo la primera llamada
            # pasa como sonda; el resto sigue rechazado hasta que resuelva, para
            # no lanzar una avalancha contra un servicio aún caído.
            return self._probing
        return True

    def record_success(self) -> None:
        self._failures = 0
        self._opened_at = None
        self._probing = False

    def record_failure(self) -> None:
        self._failures += 1
        self._probing = False
        if self._failures >= self.failure_threshold:
            self._opened_at = self._clock()

    async def call(self, fn: Callable[[], Awaitable[T]]) -> T:
        if self.is_open:
            raise CircuitOpenError("Circuito abierto: integración degradada")
        if self._opened_at is not None:
            self._probing = True  # esta llamada es la sonda de half-open
        try:
            result = await fn()
        except Exception:
            self.record_failure()
            raise
        self.record_success()
        return result


async def retry_async(
    fn: Callable[[], Awaitable[T]],
    attempts: int = 3,
    base_delay: float = 0.5,
    retry_on: tuple[type[Exception], ...] = (Exception,),
    should_retry: Callable[[BaseException], bool] | None = None,
) -> T:
    """`should_retry` filtra por instancia: permite reintentar un 5xx y no un
    4xx, que es determinista y solo gastaría intentos."""
    last_exc: Exception | None = None
    for attempt in range(attempts):
        try:
            return await fn()
        except retry_on as exc:  # noqa: PERF203
            last_exc = exc
            if should_retry is not None and not should_retry(exc):
                raise
            if attempt < attempts - 1:
                await asyncio.sleep(base_delay * (2**attempt))
    assert last_exc is not None
    raise last_exc
