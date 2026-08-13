from __future__ import annotations

import re
import hashlib
import unicodedata
from dataclasses import dataclass
from typing import Iterable, Mapping, Sequence


DEFAULT_THRESHOLD = 0.72
MAX_EVIDENCE_TEXTS = 500
MAX_GENERATED_TEXTS = 3
MAX_CHARS_PER_TEXT = 20_000
_WORD = re.compile(r"[^\W_]+", re.UNICODE)
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
    if not normalized:
        return set()
    words = normalized.split()
    shingles = {
        "word:" + " ".join(words[index : index + 3])
        for index in range(max(0, len(words) - 2))
    }
    cjk = "".join(_CJK.findall(normalized))
    shingles.update("cjk:" + cjk[index : index + 3] for index in range(max(0, len(cjk) - 2)))
    if not shingles:
        shingles.add("short:" + normalized)
    return shingles


class OriginalityGuard:
    def __init__(self, *, threshold: float = DEFAULT_THRESHOLD, max_evidence: int = MAX_EVIDENCE_TEXTS, max_generated_texts: int = MAX_GENERATED_TEXTS, max_chars_per_text: int = MAX_CHARS_PER_TEXT) -> None:
        if not 0 <= threshold <= 1:
            raise ValueError("threshold must be between zero and one")
        self.threshold = threshold
        self.max_evidence = max(1, min(max_evidence, 1000))
        self.max_generated_texts = max(1, min(max_generated_texts, MAX_GENERATED_TEXTS))
        self.max_chars_per_text = max(100, min(max_chars_per_text, 100_000))

    def check(self, generated: Mapping[str, object], evidence: Sequence[tuple[str, str]]) -> OriginalityResult:
        values = [str(generated.get(field, "")) for field in ("head_titles", "specification", "instructions_for_buyers")]
        return self.check_texts(values, evidence)

    def check_texts(self, generated_texts: Iterable[str], evidence: Sequence[tuple[str, str]]) -> OriginalityResult:
        generated = set().union(*(_shingles(str(value), self.max_chars_per_text) for value in list(generated_texts)[: self.max_generated_texts]))
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

    def fingerprint_texts(self, texts: Iterable[str]) -> list[str]:
        shingles = set().union(*(_shingles(str(value), self.max_chars_per_text) for value in texts))
        return sorted(hashlib.sha256(value.encode("utf-8")).hexdigest() for value in shingles)

    def check_fingerprints(self, generated_texts: Iterable[str], evidence: Sequence[tuple[str, Sequence[str]]]) -> OriginalityResult:
        generated = set(self.fingerprint_texts(list(generated_texts)[: self.max_generated_texts]))
        if not generated:
            return OriginalityResult(True, 0.0, None)
        maximum = 0.0
        matched_id: str | None = None
        for evidence_id, raw_shingles in list(evidence)[: self.max_evidence]:
            fingerprints = set(raw_shingles)
            score = len(generated & fingerprints) / max(1, min(len(generated), len(fingerprints))) if fingerprints else 0
            if score > maximum:
                maximum, matched_id = score, evidence_id
        return OriginalityResult(maximum < self.threshold, round(maximum, 6), matched_id)
