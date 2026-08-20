import pytest

from apps.core_api.graphs.classifiers import RuleBasedClassifier


@pytest.fixture
def classifier() -> RuleBasedClassifier:
    return RuleBasedClassifier()


@pytest.mark.parametrize(
    ("texto", "esperado"),
    [
        ("Quiero hacer un PEDIDO de 3 cajas", "pedido"),
        ("¿Cuánto cuesta el producto X?", "consulta_precio"),
        ("¿Hay stock disponible?", "consulta_stock"),
        ("Tengo un reclamo con mi entrega", "soporte"),
        ("hola buenas tardes", "saludo"),
    ],
)
def test_intenciones_por_keywords(classifier, texto, esperado):
    intent, confidence = classifier.classify(texto)
    assert intent == esperado
    assert confidence >= 0.9


def test_sin_keywords_cae_a_general_con_baja_confianza(classifier):
    intent, confidence = classifier.classify("xyzzy 12345")
    assert intent == "general"
    assert confidence < 0.5


def test_acentos_normalizados(classifier):
    intent, _ = classifier.classify("necesito una cotización")
    assert intent == "pedido"
