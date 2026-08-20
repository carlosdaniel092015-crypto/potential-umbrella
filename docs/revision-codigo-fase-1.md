# Revisión adversarial del código de Fase 1

Segunda ronda de revisión, esta vez sobre el código construido (no sobre el
documento). Tres revisores independientes — corrección, seguridad ofensiva y
QA — leyeron el código real. **26 hallazgos**: 1 crítico, 5 altos.

Todos los de severidad crítica, alta y media están corregidos, con test que los
cubre. Ver [decisiones-fase-1.md](decisiones-fase-1.md) D-16 a D-20.

## Crítico

**`SECRET_KEY` con default inseguro y sin guarda en producción.** `prod.py` no
definía ni validaba la clave, y todos los puntos de entrada caían a los settings
de desarrollo por defecto. Un despliegue sin fijar las dos variables arrancaba
con `DEBUG=True` y una clave publicada en el repositorio: suficiente para forjar
una cookie de sesión de administrador del panel.
→ **Corregido (D-18)**: sin default compartido, `prod.py` aborta si falta o si es
la de desarrollo, y declarar `ENVIRONMENT=production` con settings `.dev` también
aborta. Cuatro tests cubren las cuatro combinaciones.

## Altos

| Hallazgo | Corrección |
|---|---|
| **Doble respuesta al cliente por carrera.** La barrera de idempotencia solo detectaba duplicados si el original ya estaba `PROCESSED`, que se marca al final del pipeline. Un reintento llegado durante el procesamiento veía `RECEIVED` y ejecutaba todo otra vez. | Estado `PROCESSING` reclamado con UPDATE condicional atómico (D-16). Tests de reintento en vuelo y de reclamo perdido. |
| **DoS sin autenticar por cuerpo ilimitado.** El límite de tamaño se evaluaba *después* de bufferizar el cuerpo completo en memoria. | Rechazo por `Content-Length` y corte durante el streaming (D-20). Verificado en vivo: 300 KB → 413. |
| **Conexiones de base de datos nunca recicladas en el worker.** Celery no emite las señales de request de Django, así que una conexión caída dejaba el worker fallando indefinidamente. | `close_old_connections` en `task_prerun`/`task_postrun`. |
| **El cortacircuitos nunca abría en producción.** Se construía un pipeline nuevo por tarea, así que los contadores de fallo se reiniciaban siempre. | Pipeline cacheado por proceso de worker (`get_worker_pipeline`). |
| **Modo celery sin cobertura y con defecto latente.** `asyncio.run` dentro de un event loop ya activo rompe con `CELERY_TASK_ALWAYS_EAGER`. | `async_to_sync` en su lugar, más test de integración del branch celery. |

## Medios corregidos

- **PII en la cola**: los argumentos de la tarea llevaban teléfono y texto del cliente a Redis, que persiste a disco con AOF. Ahora se encola solo el id (D-17).
- **Reintentos sobre errores 4xx**: una clave inválida gastaba tres intentos por mensaje y abría el circuito por un error de configuración. Ahora solo se reintentan 5xx y 429.
- **Reenvío al cliente en reintentos**: un fallo posterior al envío hacía que el reintento enviara una segunda vez. Ahora se comprueba si ya hay respuesta enviada.
- **Dos conversaciones para un contacto nuevo**: sin constraint, dos mensajes concurrentes partían el historial de forma permanente. Ahora hay índice único parcial y manejo de `IntegrityError`.
- **`/internal/*` sin autenticación** en la misma app que el webhook público: ahora exige token fuera de dev.
- **Half-open sin límite de sondas**: al vencer la espera, todas las llamadas concurrentes pasaban a la vez. Ahora solo una sonda.
- **Test frágil por tiempo real** (ventana de 50 ms): reloj inyectable, sin sleeps.
- **Dobles de prueba divergentes del contrato real**: el fake de persistencia descartaba `agent_id` y no podía representar el estado de un reintento; ambos corregidos.
- **Composición real sin cobertura**: el fixture sustituía la raíz de composición, así que un fallo de wiring no se habría detectado. Añadido un test que arranca la app sin overrides.
- **Caminos del webhook sin test**: 413 por cuerpo grande y 500 ante fallo del proveedor (contrato necesario para que YCloud reintente).
- **CI sin mínimo privilegio**: `permissions: contents: read` a nivel de workflow.

## Encontrado en el diseño de la Fase 2 (defecto grave de la Fase 1)

**Las ráfagas de mensajes perdían respuestas en silencio.** La guarda que evita
reenviar tras un fallo posterior al envío preguntaba «¿existe alguna respuesta
enviada con id posterior a este mensaje?». En el camino real de producción, donde
el webhook registra los tres mensajes de una ráfaga y solo después el worker los
procesa, la respuesta al primero ya es posterior a los otros dos: ambos quedaban
marcados como procesados **sin contestar**. Reproducido con el pipeline en orden
de producción (`processed`, `already_replied`, `already_replied`) y contra el
servidor real por HTTP.

No lo detectó ninguna de las revisiones de la Fase 1: los tests recorrían el modo
en línea, donde los identificadores de entrante y saliente se intercalan y el
fallo no aparece.

→ **Corregido (D-27)**: cada respuesta apunta al mensaje entrante que contesta
(`Message.reply_to`), con restricción única sobre las respuestas: crear la fila
*es* el derecho a responder, así que dos ejecuciones concurrentes del mismo
mensaje no pueden escribirle dos veces al cliente, y un mensaje distinto de la
misma ráfaga sí recibe la suya. Cinco pruebas nuevas cubren la ráfaga en orden de
producción, la atadura respuesta→mensaje, el reintento del mismo mensaje y el
reintento tras fallo de envío.

## Tercera ronda: la revisión del propio arreglo

El turno por conversación (D-28) pasó por una revisión adversarial que reprodujo
**12 defectos ejecutando código**, dos de ellos críticos. El arreglo de un
problema había introducido otros peores. Vale la pena registrarlo porque el
patrón se repite: lo que parece una guarda razonable falla en el camino que los
tests no recorren.

| Defecto | Por qué era grave | Corrección |
|---|---|---|
| **El presupuesto de espera (60 s) era 15 veces menor que el margen de abandono (15 min)** | Al agotarse los reintentos la tarea moría y el mensaje quedaba sin responder, sin nadie que lo reencolara. Peor: un solo mensaje zombi provocaba un apagón de 15 minutos en el que **todo mensaje nuevo de ese cliente se descartaba en silencio**, y cada descarte alargaba el apagón. Más grave que el defecto que D-28 arreglaba. | Al agotar la espera se procesa sin el predecesor, que es la misma decisión que ya toma el margen. Nunca se muere sin responder. |
| **`self.retry(max_retries=30)` corrompía la política de reintentos de todo el proceso** | `Task.retry` fija `override_max_retries` en la instancia de la tarea, que es un singleton del worker, y con `autoretry_for` ese valor queda pegado. Reproducido: tras una sola espera de turno, un mensaje genuinamente roto reintentaba 30 veces en vez de 3, ocupando la cola de tiempo real ~25 minutos. La afirmación del commit anterior («sin gastar los reintentos de los fallos reales») era falsa en ambos sentidos: compartía el contador y rompía el límite. | Se publica una tarea nueva con `apply_async`, que trae su propio contador. La tarea ya no está enlazada (`bind`), así que `self.retry` no está al alcance; un test estructural lo vigila. |
| **Un predecesor en `FAILED` no bloqueaba** pero sí era reprocesable | Inversión de orden reproducida: el segundo mensaje respondía primero y el reintento del primero llegaba después. Justo el fallo que D-28 decía cerrar. | `FAILED` bloquea: su respuesta todavía puede salir. |
| **El margen se medía sobre `created_at`** | Al drenar un backlog tras una caída del worker, todos los mensajes son viejos, así que el turno se desactivaba por completo — justo cuando el orden más importa. | Nueva marca `processing_started_at`: el margen mide el reclamo, no la antigüedad. `RECEIVED` bloquea siempre. |
| **El historial incluía respuestas que nunca salieron** (defecto preexistente) | Una respuesta en `PENDING` o `SEND_FAILED` entraba al prompt como turno del asistente: el bot daba por leído algo que el cliente no recibió. | Solo entran los salientes `SENT`. Se ordena por id, no por `created_at`, que empata entre filas del mismo lote. |
| **El reintento regeneraba la respuesta con el modelo** | Si el envío falló de forma ambigua (un tiempo de espera que el proveedor sí entregó), el cliente veía dos versiones distintas del mismo mensaje y el texto realmente enviado desaparecía del registro. | Se reenvía el texto ya persistido. |
| **En modo inline, `esperando_turno` devolvía HTTP 200** | El proveedor daba el mensaje por entregado y se perdía para siempre. | Devuelve 503 para que el proveedor reintente. |
| **Mensajes con la tarea perdida no los rescataba nadie** | Un `RECEIVED` cuya tarea nunca se ejecutó bloqueaba su conversación indefinidamente. | Comando `reencolar_pendientes`, para programar cada pocos minutos. |

También quedaron corregidos una aserción vacua y un test que no distinguía «zombi»
de «aún no procesado», y la tarea del worker pasó de cobertura cero a seis
pruebas: los dos defectos críticos vivían enteros en esas ocho líneas.

**Sigue sin resolver**, y consta como pendiente: ordenar por `id` es ordenar por
INSERT, y en Postgres INSERT no es COMMIT, así que dos webhooks concurrentes del
mismo contacto pueden hacer invisible al predecesor. No se reproduce en la suite
porque los tests corren en SQLite, que serializa las escrituras. Requiere derivar
el turno de un valor asignado bajo bloqueo de la conversación.

## Encontrado ejecutando, no revisando

La ejecución en vivo destapó un fallo que ningún revisor había previsto: cuando
la persistencia falla **después** de un envío exitoso, el código propagaba un 500
—con el mensaje ya entregado al cliente— y dejaba el mensaje en un estado zombi
del que nunca salía. Corregido (D-19): el fallo se registra como crítico para
conciliación manual y se responde 200, porque reintentar reenviaría al cliente.
Tres tests de integración cubren ahora ese camino.

## Diferidos con fase asignada

- Inyección de prompt: impacto acotado hoy (sin herramientas ni acciones); los controles reales — validación de argumentos server-side, partner resuelto desde la sesión y confirmación humana en escrituras — corresponden a las Fases 2 y 3, antes de habilitar herramientas.
- Conciliación automática de mensajes en `PROCESSING`, breaker compartido en Redis, fijar las actions de CI por SHA: Fase 6.
