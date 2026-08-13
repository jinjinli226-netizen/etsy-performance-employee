from __future__ import annotations

import sys
sys.dont_write_bytecode = True

import re
import hashlib
import unicodedata
from collections.abc import Iterable, Mapping, Sequence
from typing import Any


DEFAULT_THRESHOLD = 0.72
MAX_EVIDENCE_TEXTS = 500
MAX_GENERATED_TEXTS = 3
MAX_CHARS_PER_TEXT = 20_000
_WORDS = re.compile(r"[^\W_]+", re.UNICODE)
_CJK_CHAR = re.compile(r"[\u3400-\u9fff]")


def _normalize(value: str, max_chars: int) -> str:
    normalized = unicodedata.normalize("NFKC", value).casefold()[:max_chars]
    return " ".join(_WORDS.findall(normalized))


def _shingles(value: str, max_chars: int) -> set[str]:
    normalized = _normalize(value, max_chars)
    if not normalized:
        return set()
    tokens = normalized.split()
    shingles = {
        "word:" + " ".join(tokens[index : index + 3])
        for index in range(max(0, len(tokens) - 2))
    }
    cjk = "".join(_CJK_CHAR.findall(normalized))
    shingles.update("cjk:" + cjk[index : index + 3] for index in range(max(0, len(cjk) - 2)))
    if not shingles:
        shingles.add("short:" + normalized)
    return shingles


def check_originality(
    generated_texts: Iterable[str],
    evidence: Sequence[tuple[str, str]],
    *,
    threshold: float = DEFAULT_THRESHOLD,
    max_evidence: int = MAX_EVIDENCE_TEXTS,
    max_generated_texts: int = MAX_GENERATED_TEXTS,
    max_chars_per_text: int = MAX_CHARS_PER_TEXT,
) -> dict[str, bool | float | str | None]:
    if not 0 <= threshold <= 1:
        raise ValueError("threshold must be between zero and one")
    evidence_limit = max(1, min(max_evidence, 1000))
    generated_limit = max(1, min(max_generated_texts, MAX_GENERATED_TEXTS))
    char_limit = max(100, min(max_chars_per_text, 100_000))
    generated = set().union(*(_shingles(str(value), char_limit) for value in list(generated_texts)[:generated_limit]))
    if not generated:
        return {"passed": True, "score": 0.0, "evidence_id": None}
    maximum = 0.0
    matched_id: str | None = None
    for evidence_id, raw in list(evidence)[:evidence_limit]:
        raw_shingles = _shingles(raw, char_limit)
        if not raw_shingles:
            continue
        score = len(generated & raw_shingles) / max(1, min(len(generated), len(raw_shingles)))
        if score > maximum:
            maximum, matched_id = score, evidence_id
    return {"passed": maximum < threshold, "score": round(maximum, 6), "evidence_id": matched_id}


def fingerprint_texts(texts: Iterable[str], *, max_chars_per_text: int = MAX_CHARS_PER_TEXT) -> list[str]:
    char_limit = max(100, min(max_chars_per_text, 100_000))
    shingles = set().union(*(_shingles(str(value), char_limit) for value in texts))
    return sorted(hashlib.sha256(value.encode("utf-8")).hexdigest() for value in shingles)


def check_fingerprints(
    generated_texts: Iterable[str],
    evidence: Sequence[tuple[str, Sequence[str]]],
    *,
    threshold: float = DEFAULT_THRESHOLD,
) -> dict[str, bool | float | str | None]:
    generated = set(fingerprint_texts(list(generated_texts)[:MAX_GENERATED_TEXTS]))
    if not generated:
        return {"passed": True, "score": 0.0, "evidence_id": None}
    maximum = 0.0
    matched_id: str | None = None
    for evidence_id, raw_shingles in list(evidence)[:MAX_EVIDENCE_TEXTS]:
        fingerprints = set(raw_shingles)
        score = len(generated & fingerprints) / max(1, min(len(generated), len(fingerprints))) if fingerprints else 0
        if score > maximum:
            maximum, matched_id = score, evidence_id
    return {"passed": maximum < threshold, "score": round(maximum, 6), "evidence_id": matched_id}


def check_listing(
    generated: Mapping[str, Any],
    evidence: Sequence[tuple[str, str]],
    *,
    threshold: float = DEFAULT_THRESHOLD,
) -> dict[str, bool | float | str | None]:
    values = [str(generated.get(field, "")) for field in ("head_titles", "specification", "instructions_for_buyers")]
    return check_originality(values, evidence, threshold=threshold)
