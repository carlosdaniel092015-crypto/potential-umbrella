"""Las métricas se verifican contra casos calculados a mano: si el medidor está
mal, todas las conclusiones sobre el clasificador lo estarán también."""

import pytest

from tests.evaluacion.metricas import evaluar


def test_clasificador_perfecto():
    pares = [("pedido", "pedido"), ("soporte", "soporte"), ("saludo", "saludo")]
    ev = evaluar(pares)
    assert ev.exactitud == 1.0
    assert ev.macro_f1 == 1.0
    assert ev.confusiones_frecuentes() == []


def test_clasificador_que_siempre_dice_lo_mismo_no_pasa_el_macro_f1():
    # 8 de 10 son 'pedido': la exactitud premia adivinar la clase mayoritaria,
    # el macro-F1 lo delata. Este es el motivo de elegir macro-F1.
    pares = [("pedido", "pedido")] * 8 + [("soporte", "pedido"), ("saludo", "pedido")]
    ev = evaluar(pares)

    assert ev.exactitud == pytest.approx(0.8)
    # pedido: prec 0.8, rec 1.0 -> F1 0.888...; soporte y saludo: F1 0
    assert ev.macro_f1 == pytest.approx(0.888888 / 3, abs=1e-4)
    assert ev.macro_f1 < 0.3
    peor = ev.peor_clase
    assert peor is not None
    assert peor.f1 == 0.0


def test_metricas_por_clase_calculadas_a_mano():
    pares = [
        ("pedido", "pedido"),  # VP pedido
        ("pedido", "pedido"),  # VP pedido
        ("pedido", "consulta_precio"),  # FN pedido, FP consulta_precio
        ("consulta_precio", "consulta_precio"),  # VP consulta_precio
        ("consulta_precio", "pedido"),  # FN consulta_precio, FP pedido
    ]
    ev = evaluar(pares)

    pedido = ev.por_clase["pedido"]
    assert (pedido.verdaderos_positivos, pedido.falsos_positivos, pedido.falsos_negativos) == (
        2,
        1,
        1,
    )
    assert pedido.soporte == 3
    assert pedido.precision == pytest.approx(2 / 3)
    assert pedido.recall == pytest.approx(2 / 3)
    assert pedido.f1 == pytest.approx(2 / 3)

    precio = ev.por_clase["consulta_precio"]
    assert precio.precision == pytest.approx(0.5)
    assert precio.recall == pytest.approx(0.5)


def test_clase_inventada_penaliza_precision_y_aparece_en_el_informe():
    # El clasificador devuelve una etiqueta que no existe en el dataset.
    pares = [("pedido", "pedido"), ("pedido", "intencion_fantasma")]
    ev = evaluar(pares)

    assert "intencion_fantasma" in ev.por_clase
    fantasma = ev.por_clase["intencion_fantasma"]
    assert fantasma.soporte == 0
    assert fantasma.precision == 0.0
    assert fantasma.f1 == 0.0
    assert "intencion_fantasma" in ev.informe()


def test_confusiones_ordenadas_por_frecuencia():
    pares = (
        [("pedido", "consulta_precio")] * 3
        + [("soporte", "pedido")] * 5
        + [("saludo", "saludo")]
    )
    ev = evaluar(pares)

    confusiones = ev.confusiones_frecuentes()
    assert confusiones[0] == ("soporte", "pedido", 5)
    assert confusiones[1] == ("pedido", "consulta_precio", 3)


def test_dataset_vacio_no_revienta():
    ev = evaluar([])
    assert ev.exactitud == 0.0
    assert ev.macro_f1 == 0.0
    assert ev.peor_clase is None


def test_informe_incluye_metrica_primaria_y_soportes():
    pares = [("pedido", "pedido"), ("soporte", "pedido")]
    informe = evaluar(pares).informe()
    assert "macro-F1" in informe
    assert "pedido" in informe
    assert "soporte" in informe
