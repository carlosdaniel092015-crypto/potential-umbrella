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
