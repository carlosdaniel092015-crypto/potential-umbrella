"""Métricas de clasificación de intención.

El documento técnico fija "≥90% de acierto" para el enrutamiento sin decir con
qué métrica. Con clases desbalanceadas la exactitud global engaña: si el 40% de
los mensajes son `pedido`, un clasificador que solo acierte esa clase ya sale
con 40% y puede parecer aceptable ignorando por completo `soporte`.

Por eso la métrica primaria es el **macro-F1** (media de los F1 por clase, cada
clase pesa igual) y se reporta además el recall por clase, que es el que dice si
alguna intención está siendo ignorada.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class MetricasClase:
    etiqueta: str
    soporte: int  # ejemplos reales de esta clase
    verdaderos_positivos: int
    falsos_positivos: int
    falsos_negativos: int

    @property
    def precision(self) -> float:
        denominador = self.verdaderos_positivos + self.falsos_positivos
        return self.verdaderos_positivos / denominador if denominador else 0.0

    @property
    def recall(self) -> float:
        denominador = self.verdaderos_positivos + self.falsos_negativos
        return self.verdaderos_positivos / denominador if denominador else 0.0

    @property
    def f1(self) -> float:
        p, r = self.precision, self.recall
        return 2 * p * r / (p + r) if (p + r) else 0.0


@dataclass
class Evaluacion:
    total: int
    aciertos: int
    por_clase: dict[str, MetricasClase]
    confusion: dict[tuple[str, str], int] = field(default_factory=dict)

    @property
    def exactitud(self) -> float:
        return self.aciertos / self.total if self.total else 0.0

    @property
    def macro_f1(self) -> float:
        if not self.por_clase:
            return 0.0
        return sum(m.f1 for m in self.por_clase.values()) / len(self.por_clase)

    @property
    def peor_clase(self) -> MetricasClase | None:
        """La clase con menor F1: es la que delata una intención ignorada."""
        if not self.por_clase:
            return None
        return min(self.por_clase.values(), key=lambda m: m.f1)

    def confusiones_frecuentes(self, limite: int = 5) -> list[tuple[str, str, int]]:
        errores = [
            (real, predicha, n)
            for (real, predicha), n in self.confusion.items()
            if real != predicha
        ]
        errores.sort(key=lambda item: item[2], reverse=True)
        return errores[:limite]

    def informe(self) -> str:
        lineas = [
            f"exactitud: {self.exactitud:.1%}  macro-F1: {self.macro_f1:.3f}  "
            f"({self.aciertos}/{self.total})",
            f"{'intención':<20} {'sop':>4} {'prec':>7} {'rec':>7} {'F1':>7}",
        ]
        for etiqueta in sorted(self.por_clase):
            m = self.por_clase[etiqueta]
            lineas.append(
                f"{etiqueta:<20} {m.soporte:>4} {m.precision:>7.2f} "
                f"{m.recall:>7.2f} {m.f1:>7.2f}"
            )
        confusiones = self.confusiones_frecuentes()
        if confusiones:
            lineas.append("confusiones más frecuentes:")
            lineas.extend(f"  {real} → {predicha}: {n}" for real, predicha, n in confusiones)
        return "\n".join(lineas)


def evaluar(pares: list[tuple[str, str]]) -> Evaluacion:
    """`pares` son (etiqueta_real, etiqueta_predicha).

    Las clases se toman de la unión de reales y predichas: así una clase que el
    clasificador inventa (predice sin que exista) también aparece penalizada en
    precisión, en lugar de desaparecer del informe.
    """
    etiquetas = {real for real, _ in pares} | {pred for _, pred in pares}
    confusion: dict[tuple[str, str], int] = {}
    for real, predicha in pares:
        clave = (real, predicha)
        confusion[clave] = confusion.get(clave, 0) + 1

    por_clase: dict[str, MetricasClase] = {}
    for etiqueta in etiquetas:
        vp = sum(1 for real, pred in pares if real == etiqueta and pred == etiqueta)
        fp = sum(1 for real, pred in pares if real != etiqueta and pred == etiqueta)
        fn = sum(1 for real, pred in pares if real == etiqueta and pred != etiqueta)
        por_clase[etiqueta] = MetricasClase(
            etiqueta=etiqueta,
            soporte=vp + fn,
            verdaderos_positivos=vp,
            falsos_positivos=fp,
            falsos_negativos=fn,
        )

    return Evaluacion(
        total=len(pares),
        aciertos=sum(1 for real, pred in pares if real == pred),
        por_clase=por_clase,
        confusion=confusion,
    )
