from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from typing import Iterable, Mapping, Sequence


_WORD = re.compile(r"[\w]+", re.UNICODE)
_CJK = re.compile(r"[\u3400-\u9fff]")


@dataclass(frozen=True)
class OriginalityResult:
    passed: bool
    max_score: float
    evidence_id: str | None

    def as_dict(self) -> dict[str, bool | float | str | None]:
        return {"passed": self.passed, "max_score": self.max_score, "evidence_id": self.evidence_id}


def _normalized(value: str, limit: int) -> str:
    value = unicodedata.normalize("NFKC", value).casefold()[:limit]
    return " ".join(_WORD.findall(value))


def _shingles(value: str, limit: int) -> set[str]:
    normalized = _normalized(value, limit)
    if _CJK.search(normalized):
        compact = "".join(normalized.split())
        if len(compact) >= 5:
            return {compact[index : index + 3] for index in range(len(compact) - 2)}
    words = normalized.split()
    if len(words) >= 3:
        return {" ".join(words[index : index + 3]) for index in range(len(words) - 2)}
    return set()


class OriginalityGuard:
    def __init__(self, *, threshold: float = 0.72, max_evidence: int = 500, max_chars_per_text: int = 20_000) -> None:
        if not 0 <= threshold <= 1:
            raise ValueError("threshold must be between zero and one")
        self.threshold = threshold
        self.max_evidence = max(1, min(max_evidence, 1000))
        self.max_chars_per_text = max(100, min(max_chars_per_text, 100_000))

    def check(self, generated: Mapping[str, object], evidence: Sequence[tuple[str, str]]) -> OriginalityResult:
        values = [str(generated.get(field, "")) for field in ("head_titles", "specification", "instructions_for_buyers")]
        return self.check_texts(values, evidence)

    def check_texts(self, generated_texts: Iterable[str], evidence: Sequence[tuple[str, str]]) -> OriginalityResult:
        generated = set().union(*(_shingles(value, self.max_chars_per_text) for value in generated_texts))
        if not generated:
            return OriginalityResult(True, 0.0, None)
        maximum = 0.0
        matched_id: str | None = None
        for evidence_id, raw in list(evidence)[: self.max_evidence]:
            raw_shingles = _shingles(raw, self.max_chars_per_text)
            if not raw_shingles:
                continue
            score = len(generated & raw_shingles) / max(1, min(len(generated), len(raw_shingles)))
            if score > maximum:
                maximum, matched_id = score, evidence_id
        return OriginalityResult(maximum < self.threshold, round(maximum, 6), matched_id)
