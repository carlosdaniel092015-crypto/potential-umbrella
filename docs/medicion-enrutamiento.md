# Medición del enrutamiento

El documento técnico fija «≥90 % de acierto» para el Determinador sin decir con
qué métrica ni contra qué. Esto es lo que hacía falta para que ese número
significara algo, y el resultado de medirlo.

## Qué se mide

| Pieza | Dónde | Qué es |
|---|---|---|
| Dataset etiquetado | [`tests/fixtures/intenciones.json`](../tests/fixtures/intenciones.json) | 120 mensajes reales de WhatsApp en español de México, 8 intenciones, con 52 casos ambiguos a propósito e intentos de manipulación del prompt |
| Métricas | [`tests/evaluacion/metricas.py`](../tests/evaluacion/metricas.py) | Precisión, recall y F1 por clase, matriz de confusión, macro-F1 |
| Ejecutor | [`tests/evaluacion/runner.py`](../tests/evaluacion/runner.py) | `uv run python -m tests.evaluacion.runner` |
| Gate | [`tests/evaluacion/test_enrutamiento.py`](../tests/evaluacion/test_enrutamiento.py) | Corre en cada suite |

**La métrica primaria es el macro-F1**, no la exactitud global. Con clases
desbalanceadas, un clasificador que solo acierte `pedido` saca buena exactitud
mientras ignora `soporte` por completo; el macro-F1 pesa igual cada intención y
lo delata. Se reporta además el recall por clase, que es el que dice si alguna
intención está siendo abandonada.

## Resultado del clasificador de reglas

Medido el 20 de agosto de 2026 sobre los 120 ejemplos:

```
exactitud: 49.2%   macro-F1: 0.512   (59/120)

intención             sop    prec     rec      F1
consulta_precio        16    1.00    0.44    0.61
consulta_stock         15    0.82    0.60    0.69
estado_pedido          15    0.62    0.53    0.57
fuera_de_dominio       14    0.75    0.21    0.33
hablar_con_humano      16    1.00    0.56    0.72
pedido                 17    0.52    0.71    0.60
saludo                 11    0.89    0.73    0.80
soporte                16    0.60    0.19    0.29

confusiones más frecuentes:
  fuera_de_dominio -> general: 11
  soporte -> general: 7
  consulta_precio -> general: 5
  consulta_stock -> general: 5
  consulta_precio -> pedido: 4

casos directos (68):            macro-F1 0.527
casos ambiguos a propósito (52): macro-F1 0.462
```

### Qué dice este número

**El objetivo de 0.90 no se alcanza con reglas, y no va a alcanzarse.** 0.512
está a mitad de camino. Esto deja de ser una opinión sobre si hace falta un
clasificador con LLM y pasa a ser un dato.

**La forma del error es la correcta, aunque el número sea bajo.** La confusión
dominante es hacia `general`, la etiqueta de «no reconozco»: 28 de los 61 fallos
son el clasificador admitiendo que no sabe, en lugar de inventar una intención.
Con el umbral de confianza y su fallback, esos mensajes van a contención o a una
persona, que es el comportamiento seguro. Un clasificador que fallara *afirmando*
intenciones equivocadas sería peor con el mismo macro-F1.

**`hablar_con_humano` tiene precisión perfecta y recall de 0.56.** Cuando dice que
el cliente pide una persona, acierta siempre; se le escapa el 44 %. Para esa clase
el recall es lo que cuesta dinero, y es el primer objetivo del clasificador con
LLM.

**`soporte` (F1 0.29) y `fuera_de_dominio` (0.33) son los peores.** Un reclamo se
expresa de mil formas que no contienen la palabra «reclamo», y detectar por
palabras clave que un mensaje no tiene nada que ver con el negocio es
prácticamente imposible.

## Defecto que la medición destapó

Antes de poder medir, el clasificador de reglas **solo podía emitir 5 de las 8
intenciones**: `estado_pedido`, `hablar_con_humano` y `fuera_de_dominio` eran
inalcanzables por construcción, con recall cero garantizado. La más grave era
`hablar_con_humano`: como las reglas son la degradación cuando el LLM falla, un
cliente pidiendo una persona nunca habría sido detectado justo en el momento en
que el sistema está peor.

Corregido reconciliando catálogo y reglas, con precedencias explícitas: seguridad
(intentos de manipulación) antes que negocio, petición de persona antes que
cualquier venta, estado de un pedido antes que pedido nuevo, y reclamo antes que
venta. El gate incluye un test estructural que vuelve a fallar si catálogo y
dataset se separan otra vez, porque es una regresión que la media global no
delata.

## Pendiente

- Clasificador con LLM y una corrida `--live` que congele su resultado con fecha, modelo y versión del dataset.
- Curva de acierto contra umbral de confianza, para elegir el punto de fallback con datos.
- El dataset es sintético pero verosímil: cuando haya conversaciones reales, sustituir por mensajes anonimizados y volver a medir. Un dataset generado puede ser sistemáticamente más fácil que la realidad.
