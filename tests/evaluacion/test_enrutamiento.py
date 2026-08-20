"""Gate de enrutamiento: mide el clasificador contra el dataset etiquetado.

Fija tres propiedades. La primera es estructural y no se negocia; las otras dos
son una línea base para que el clasificador no empeore sin que nadie se dé
cuenta, no el objetivo del documento (0.90), que solo el clasificador con LLM de
la Fase 2 puede acercar.
"""

import pytest

from apps.core_api.graphs.classifiers import (
    INTENCIONES,
    SIN_RECONOCER,
    RuleBasedClassifier,
)
from tests.evaluacion.runner import cargar_dataset, evaluar_clasificador, objetivo

# Línea base observada del clasificador de reglas (macro-F1 0.512). El umbral va
# por debajo para no romper con un reajuste menor de palabras clave, pero sí si
# alguien rompe una familia de reglas.
BASE_MACRO_F1 = 0.45


@pytest.fixture(scope="module")
def evaluacion():
    return evaluar_clasificador(RuleBasedClassifier())


def test_el_catalogo_del_dataset_y_el_del_clasificador_coinciden():
    """La regresión más grave posible: una intención que el clasificador no
    puede emitir tiene recall cero por construcción, y nadie lo nota porque la
    media global apenas se mueve. Fue el caso de `hablar_con_humano`."""
    etiquetas_dataset = {e.intencion for e in cargar_dataset()}
    emitibles = set(RuleBasedClassifier.KEYWORDS)

    assert etiquetas_dataset <= emitibles, (
        "el clasificador no puede producir estas intenciones del dataset: "
        f"{sorted(etiquetas_dataset - emitibles)}"
    )
    assert etiquetas_dataset == set(INTENCIONES), (
        "el catálogo declarado y el dataset se separaron: "
        f"{sorted(etiquetas_dataset ^ set(INTENCIONES))}"
    )


def test_toda_intencion_del_catalogo_es_alcanzable():
    """No basta declararla: alguna frase real tiene que llegar a ella."""
    clasificador = RuleBasedClassifier()
    alcanzadas = {clasificador.classify(e.texto)[0] for e in cargar_dataset()}
    inalcanzables = set(INTENCIONES) - alcanzadas
    assert not inalcanzables, f"nunca se emiten: {sorted(inalcanzables)}"


def test_macro_f1_no_baja_de_la_linea_base(evaluacion):
    assert evaluacion.macro_f1 >= BASE_MACRO_F1, (
        f"el enrutamiento empeoró: macro-F1 {evaluacion.macro_f1:.3f} "
        f"< {BASE_MACRO_F1}\n{evaluacion.informe()}"
    )


def test_pedir_una_persona_no_se_confunde_con_otra_cosa(evaluacion):
    """Escalar a un humano de más es barato; no escalar cuando el cliente lo
    pide es lo que quema la relación. Por eso aquí se vigila la precisión."""
    humano = evaluacion.por_clase["hablar_con_humano"]
    assert humano.precision >= 0.9, (
        f"precisión {humano.precision:.2f}: se está escalando a personas "
        "conversaciones que no lo pidieron"
    )


def test_lo_no_reconocido_no_se_disfraza_de_intencion(evaluacion):
    """Cuando las reglas no reconocen, deben decirlo (y que el umbral de
    confianza lo mande a contención o a una persona), en lugar de adivinar una
    intención de negocio."""
    assert SIN_RECONOCER not in INTENCIONES
    _, confianza = RuleBasedClassifier().classify("xyzzy qwerty 12345")
    assert confianza < 0.5, "un mensaje incomprensible no puede salir con confianza alta"


def test_el_objetivo_del_documento_sigue_lejos(evaluacion):
    """Prueba deliberadamente incómoda: documenta que el objetivo de ≥0.90 NO
    se cumple con reglas, para que nadie dé la Fase 2 por hecha. Cuando el
    clasificador con LLM lo supere, este test falla y se borra con gusto."""
    assert evaluacion.macro_f1 < objetivo(), (
        "las reglas ya alcanzan el objetivo del documento: revisa si el dataset "
        "se volvió trivial o si ya hay clasificador con LLM"
    )
