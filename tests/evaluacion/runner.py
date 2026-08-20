"""Ejecutor de la evaluación de enrutamiento.

Mide un clasificador contra el dataset etiquetado y devuelve las métricas. Es lo
que convierte el «≥90 % de acierto» del documento en un número comprobable.

    uv run python -m tests.evaluacion.runner
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from apps.core_api.graphs.classifiers import IntentClassifier
from tests.evaluacion.metricas import Evaluacion, evaluar

DATASET = Path(__file__).resolve().parents[1] / "fixtures" / "intenciones.json"


@dataclass(frozen=True)
class Ejemplo:
    texto: str
    intencion: str
    nota: str = ""

    @property
    def es_dificil(self) -> bool:
        """El dataset marca con nota los casos ambiguos a propósito."""
        return bool(self.nota)


def cargar_dataset(ruta: Path = DATASET) -> list[Ejemplo]:
    datos = json.loads(ruta.read_text(encoding="utf-8"))
    return [
        Ejemplo(texto=e["texto"], intencion=e["intencion"], nota=e.get("nota", ""))
        for e in datos["ejemplos"]
    ]


def objetivo(ruta: Path = DATASET) -> float:
    return float(json.loads(ruta.read_text(encoding="utf-8")).get("objetivo", 0.9))


def evaluar_clasificador(
    clasificador: IntentClassifier, ejemplos: list[Ejemplo] | None = None
) -> Evaluacion:
    ejemplos = ejemplos if ejemplos is not None else cargar_dataset()
    pares = [(e.intencion, clasificador.classify(e.texto)[0]) for e in ejemplos]
    return evaluar(pares)


def informe_completo(clasificador: IntentClassifier) -> str:
    ejemplos = cargar_dataset()
    faciles = [e for e in ejemplos if not e.es_dificil]
    dificiles = [e for e in ejemplos if e.es_dificil]

    partes = [
        f"=== {type(clasificador).__name__} · {len(ejemplos)} ejemplos ===",
        evaluar_clasificador(clasificador, ejemplos).informe(),
    ]
    # Separar los casos difíciles importa: una media global oculta que el
    # clasificador acierta lo obvio y falla justo donde el negocio se decide.
    if dificiles:
        partes += [
            "",
            f"--- casos directos ({len(faciles)}) ---",
            evaluar_clasificador(clasificador, faciles).informe().splitlines()[0],
            f"--- casos ambiguos a propósito ({len(dificiles)}) ---",
            evaluar_clasificador(clasificador, dificiles).informe().splitlines()[0],
        ]
    return "\n".join(partes)


def main() -> None:
    import sys

    from apps.core_api.graphs.classifiers import RuleBasedClassifier

    # La consola de Windows usa cp1252 y el informe lleva acentos.
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    print(informe_completo(RuleBasedClassifier()))
    print()
    print(f"objetivo del documento (macro-F1): {objetivo():.2f}")


if __name__ == "__main__":
    main()
