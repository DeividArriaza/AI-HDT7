"""Recuperador local del FAQ de Parachute S.A. (heredado de HDT-5).

Determinista y sin red: puntúa por solapamiento léxico normalizado entre la
consulta y la pregunta registrada, y devuelve la ficha con su `faq_id` como cita.
"""

from __future__ import annotations

import json
import re
import unicodedata
from dataclasses import asdict, dataclass
from pathlib import Path

STOPWORDS = {
    "que", "qué", "como", "cómo", "cual", "cuál", "para", "por", "con", "los", "las",
    "del", "una", "uno", "unos", "unas", "hay", "puedo", "debo", "esta", "este", "son",
    "sus", "mis", "tengo", "donde", "dónde", "cuando", "cuándo", "the",
}


@dataclass(frozen=True)
class FAQEntry:
    faq_id: str
    question: str
    answer: str
    category: str = ""


@dataclass(frozen=True)
class FAQResult:
    found: bool
    answer: str
    faq_id: str | None = None
    question: str | None = None
    category: str | None = None
    score: float = 0.0

    def to_dict(self) -> dict:
        return asdict(self)


def _normalize(text: str) -> str:
    text = unicodedata.normalize("NFKD", text.lower())
    return "".join(ch for ch in text if not unicodedata.combining(ch))


def _tokens(text: str) -> set[str]:
    words = re.findall(r"[a-z0-9ñ]+", _normalize(text))
    return {w for w in words if len(w) > 2 and w not in STOPWORDS}


class FAQRetriever:
    def __init__(self, entries: list[FAQEntry], minimum_score: float = 0.35):
        self.entries = tuple(entries)
        self.minimum_score = minimum_score
        self._index = [(_tokens(e.question + " " + e.category), e) for e in self.entries]

    def search(self, question: str) -> FAQResult:
        query = _tokens(question)
        if not query:
            return FAQResult(False, "La consulta no contiene términos útiles para buscar en el FAQ.")
        best_score, best = 0.0, None
        for entry_tokens, entry in self._index:
            score = len(query & entry_tokens) / len(query)
            if score > best_score:
                best_score, best = score, entry
        if best is None or best_score < self.minimum_score:
            return FAQResult(False, "No tengo una respuesta sustentada en el FAQ para esa pregunta.",
                             score=round(best_score, 3))
        return FAQResult(True, best.answer, best.faq_id, best.question, best.category, round(best_score, 3))


def load_faq_json(path: str | Path) -> FAQRetriever:
    records = json.loads(Path(path).read_text(encoding="utf-8"))
    return FAQRetriever([FAQEntry(r["faq_id"], r["question"], r["answer"], r.get("category", ""))
                         for r in records])
