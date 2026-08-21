# Revisión adversarial del enrutamiento — Fase 2

La primera versión del enrutamiento por reglas (D-36..D-38) pasó por una
revisión con cuatro lentes y verificación adversarial de cada hallazgo (39
agentes). Encontró un defecto crítico real que ninguno de los tests de la
propia funcionalidad detectó, porque todos montaban sus reglas a mano en vez de
usar el seed real.

## El defecto crítico

**Un mensaje benigno («listo, sin problema») silenciaba la conversación entera
de forma permanente.** Tres piezas se combinaban:

1. `RuleBasedClassifier` clasificaba "problema" por subcadena suelta: "sin
   problema", "ningún problema" y "no hay problema" se leían como reclamo
   (`soporte`).
2. El umbral de `soporte` (0.85) está por encima del techo de confianza del
   clasificador de reglas (0.75) — a propósito, porque un reclamo mal atendido
   por un bot enfada más que la avería. La ruta era siempre "a una persona".
3. Pasar a una persona pone `Conversation.status = ESCALATED`, y desde ahí el
   pipeline calla para **todos** los mensajes siguientes de esa conversación,
   sin límite de tiempo y sin avisar a nadie del equipo.

**Y era peor de lo que parece**: el comodín (`*`, para cualquier intención sin
regla propia) tenía el mismo umbral alto (0.9) que las intenciones delicadas.
`RuleBasedClassifier` es, según su propio docstring, "la red de seguridad para
cuando el LLM falla" — pero como el clasificador con LLM todavía no existe, hoy
es el **único** clasificador en producción, así que "no reconocido" (confianza
fija 0.2) ocurre en una fracción grande de los mensajes (macro-F1 0.512). El
resultado: **cualquier mensaje sin palabra clave reconocible mataba la
conversación desde el primer turno**, no solo los reclamos mal etiquetados.

Reproducido con la configuración sembrada real:

```
hola buenas tardes                          -> processed   conv=active
quiero un pedido de 10 cajas del 500ml      -> processed   conv=active
listo, sin problema                          -> processed   conv=escalated   ← aquí moría
cuanto cuesta la caja de 12?                -> atendida_por_persona (silencio)
agrega 5 cajas mas al pedido                -> atendida_por_persona (silencio)
```

## El arreglo

Dos cambios, necesarios los dos juntos (uno solo no basta: ver por qué abajo):

1. **`classifiers.py`**: se neutralizan las formas negadas de "problema" antes
   de buscar palabras clave ("sin problema", "ningún problema", "no hay
   problema", ...), sin tocar el catálogo — "sin problema, pero llegó dañado"
   sigue siendo un reclamo. Se quitó "falto" como palabra suelta (dejaba
   "me falto agregar 2 cajas" como reclamo, cuando en este dominio es el
   vendedor corrigiendo su propio pedido); "me faltaron" (plural, cantidad
   recibida de menos) sigue cubierto.
2. **`seed_fase1.py`**: el comodín pasa de 0.9 a 0.0. "No reconocido" no es una
   señal deliberada de negocio como sí lo son `hablar_con_humano` o
   `fuera_de_dominio`; con el clasificador de reglas como único clasificador en
   producción, tratarlo como motivo de escalada equivale a tener la red de
   seguridad activada permanentemente. El agente responde con su propio prompt
   ("si no tienes un dato, dilo; nunca inventes"), que ya es la contención
   adecuada para la incertidumbre genuina.

Por qué hacía falta el segundo cambio y no solo el primero: incluso corrigiendo
el falso positivo de "problema", el mensaje reclasificado cae a `general`
(comodín), y con el comodín en 0.9 seguía escalando igual — solo cambiaba el
motivo registrado, no el resultado. El defecto de fondo era arquitectónico
(una decisión de un mensaje apaga la conversación entera, sin límite de
tiempo), no solo una palabra clave mal puesta.

## Verificado

El guion exacto del hallazgo, contra la configuración sembrada real: los cinco
mensajes recibieron respuesta, conversación activa todo el tiempo. Las
escaladas deliberadas siguen intactas: pedir una persona, un reclamo real y un
intento de manipulación del prompt siguen pasando a una persona.

Test permanente en `tests/integration/test_seed_no_silencia_conversacion.py`
— el primero de la funcionalidad que usa `seed_fase1` real en vez de reglas
fabricadas a mano, que es precisamente el hueco que dejó pasar el defecto.

169 tests (antes 154), mypy limpio.

## Lo que la revisión encontró y NO se arregló todavía

Confirmados como reales pero no corregidos en esta pasada (por alcance y
tiempo, no por desacuerdo con el hallazgo):

| Severidad | Hallazgo |
|---|---|
| Alta | El bot puede escribir después de que una persona tome la conversación si el traspaso ocurre mientras el modelo está generando la respuesta (segundos): el estado se lee una vez al principio y no se revalida antes de enviar. |
| Alta | Si el aviso de traspaso ("te va a atender una persona") falla al enviarse, el reintento entra por la rama de "conversación escalada" y lo marca procesado sin reenviarlo: el cliente nunca se entera de que lo escalaron. |
| Alta | `escalar_a_persona` no es idempotente: no hay resguardo explícito contra dos llamadas para el mismo traspaso. |
| Alta | `seed_fase1` no es idempotente frente a ediciones del panel: puede resucitar reglas que alguien apagó a mano. |
| Media | Apagar un agente no deja de enrutar hacia su intención: cae al comodín, que puede tener otro agente y otro umbral, sin dejar rastro de que cambió. |
| Media | `escalar_a_persona` sobre una conversación `CLOSED` escribe el evento de auditoría pero no cambia el estado: queda registrado como escalado un traspaso que no ocurrió. |
| Media | `Decision.SIN_DESTINO` nunca se produce en el camino real: es código muerto pese a que un commit anterior describe tres salidas. |
| Baja | `RoutingRule.min_confidence` no tiene validadores: el panel acepta valores fuera de `[0, 1]`. |
| Baja | `RoutingRule.intent` no tiene `choices` ni normalización: un espacio o una mayúscula esquivan la restricción única. |

El más importante de todos para decidir antes de seguir: **qué hacer con el
estado `ESCALATED` a largo plazo.** Hoy, una vez que una conversación pasa a
una persona, se queda así para siempre salvo que alguien la devuelva a mano
desde el panel — sin caducidad, sin alerta a nadie del equipo, sin asignación.
Con el defecto crítico corregido esto ya es mucho más raro (solo ocurre ante
señales deliberadas: petición explícita, reclamo real, intento de
manipulación), pero sigue siendo una decisión de producto pendiente: ¿caduca la
escalada tras un tiempo sin que nadie responda? ¿se notifica a alguien? ¿se
asigna a un vendedor concreto? Merece el mismo tratamiento que el resto de
decisiones de la Fase 2 (alternativas, verificación), no una corrección
apresurada.

## Higiene del propio proceso de revisión

Los agentes de esta revisión dejaron temporalmente varios artefactos en el
árbol real del repositorio en vez del scratchpad indicado: archivos de test
sueltos (`test_zzz*`), un proceso de servidor huérfano corriendo un módulo que
ya no existe en disco, y una mutación de código (`if False:` en la guarda de
escalada de `pipeline.py`) que ya estaba revertida por el propio agente que la
introdujo cuando se detectó. Todo quedó limpiado antes de este commit; no se
perdió ningún cambio real. Lección para la siguiente vez: exigir explícitamente
`isolation: worktree` en los agentes que ejecuten mutaciones o levanten
procesos, no solo pedirlo en el prompt.
