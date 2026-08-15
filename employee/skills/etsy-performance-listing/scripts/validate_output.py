from __future__ import annotations

import sys
sys.dont_write_bytecode = True

import argparse
import json
import math
import re
import unicodedata
from pathlib import Path
from typing import Any
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
del _stream


FIELDS = {
    "head_titles", "tags", "specification", "category", "instructions_for_buyers",
    "confidence", "fact_warnings", "quality_warnings", "rule_version",
}
DEFAULT_RULES = {
    "title_min_words": 3,
    "title_max_words": 14,
    "tag_count": 13,
    "tag_max_chars": 20,
}
_CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
_FORMULA_PREFIX = ("=", "+", "-", "@")
EXCEL_CELL_MAX_CHARS = 32_767
MAX_TITLE_CHARS = 140
MAX_SPECIFICATION_CHARS = 4_000
MAX_CATEGORY_CHARS = 200
MAX_INSTRUCTIONS_CHARS = 4_000
MAX_RULE_VERSION_CHARS = 128
MAX_WARNINGS_PER_FIELD = 20
MAX_WARNING_CHARS = 500
MAX_WARNINGS_TOTAL_CHARS = 5_000
MAX_CONFIGURED_COUNT = 100
_TEXT_LIMITS = {
    "head_titles": MAX_TITLE_CHARS,
    "specification": MAX_SPECIFICATION_CHARS,
    "category": MAX_CATEGORY_CHARS,
    "instructions_for_buyers": MAX_INSTRUCTIONS_CHARS,
    "rule_version": MAX_RULE_VERSION_CHARS,
}


class OutputValidationError(ValueError):
    def __init__(self, issues: list[dict[str, str]]) -> None:
        super().__init__("Generated listing output is invalid.")
        self.issues = issues

    def as_dict(self) -> dict[str, Any]:
        return {"error": {"code": "invalid_generated_output", "message": str(self), "details": {"issues": self.issues}}}


def _safe_text(value: Any, field: str, issues: list[dict[str, str]], *, nonempty: bool = True, max_chars: int = EXCEL_CELL_MAX_CHARS) -> str | None:
    if not isinstance(value, str):
        issues.append({"field": field, "message": "must be a string"})
        return None
    normalized = unicodedata.normalize("NFKC", value).strip()
    if nonempty and not normalized:
        issues.append({"field": field, "message": "must not be empty"})
    if _CONTROL.search(normalized):
        issues.append({"field": field, "message": "must not contain control characters"})
    if normalized.startswith(_FORMULA_PREFIX):
        issues.append({"field": field, "message": "must not begin with an Excel formula prefix"})
    if len(normalized) > min(max_chars, EXCEL_CELL_MAX_CHARS):
        issues.append({"field": field, "message": f"must not exceed {min(max_chars, EXCEL_CELL_MAX_CHARS)} characters"})
    return normalized


def _positive_int(rules: dict[str, Any], name: str, default: int, issues: list[dict[str, str]]) -> int:
    value = rules.get(name, default)
    if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= MAX_CONFIGURED_COUNT:
        issues.append({"field": f"rules.{name}", "message": f"must be an integer from 1 to {MAX_CONFIGURED_COUNT}"})
        return default
    return value


def validate_generated(payload: Any, rules: dict[str, Any] | None = None) -> dict[str, Any]:
    issues: list[dict[str, str]] = []
    if not isinstance(payload, dict):
        raise OutputValidationError([{"field": "$", "message": "must be a JSON object"}])
    unknown = sorted(set(payload) - FIELDS)
    missing = sorted(FIELDS - set(payload))
    if unknown:
        issues.append({"field": "$", "message": f"extra fields are forbidden: {', '.join(unknown)}"})
    if missing:
        issues.append({"field": "$", "message": f"missing fields: {', '.join(missing)}"})
    active_rules = rules if isinstance(rules, dict) else {}
    title_min = _positive_int(active_rules, "title_min_words", DEFAULT_RULES["title_min_words"], issues)
    title_max = _positive_int(active_rules, "title_max_words", DEFAULT_RULES["title_max_words"], issues)
    tag_count = _positive_int(active_rules, "tag_count", DEFAULT_RULES["tag_count"], issues)
    tag_max = _positive_int(active_rules, "tag_max_chars", DEFAULT_RULES["tag_max_chars"], issues)
    if title_min > title_max:
        issues.append({"field": "rules", "message": "title_min_words must not exceed title_max_words"})

    title = _safe_text(payload.get("head_titles"), "head_titles", issues, max_chars=MAX_TITLE_CHARS)
    if title:
        words = re.findall(r"[^\W_]+(?:[-'][^\W_]+)*", title, re.UNICODE)
        if not title_min <= len(words) <= title_max:
            issues.append({"field": "head_titles", "message": f"must contain {title_min}-{title_max} words"})

    tags_value = payload.get("tags")
    cleaned_tags: list[str] = []
    if not isinstance(tags_value, list):
        issues.append({"field": "tags", "message": "must be a list"})
    else:
        if len(tags_value) != tag_count:
            issues.append({"field": "tags", "message": f"must contain exactly {tag_count} tags"})
        for index, tag in enumerate(tags_value):
            cleaned = _safe_text(tag, f"tags[{index}]", issues)
            if cleaned is not None:
                if len(cleaned) > tag_max:
                    issues.append({"field": f"tags[{index}]", "message": f"must not exceed {tag_max} characters"})
                cleaned_tags.append(cleaned)
        normalized_tags = [re.sub(r"\s+", " ", tag).casefold() for tag in cleaned_tags]
        if len(normalized_tags) != len(set(normalized_tags)):
            issues.append({"field": "tags", "message": "must be distinct after normalization"})

    cleaned: dict[str, Any] = {
        "head_titles": title,
        "tags": cleaned_tags,
        "specification": _safe_text(payload.get("specification"), "specification", issues, max_chars=MAX_SPECIFICATION_CHARS),
        "category": _safe_text(payload.get("category"), "category", issues, max_chars=MAX_CATEGORY_CHARS),
        "instructions_for_buyers": _safe_text(payload.get("instructions_for_buyers"), "instructions_for_buyers", issues, max_chars=MAX_INSTRUCTIONS_CHARS),
    }
    confidence = payload.get("confidence")
    if isinstance(confidence, bool) or not isinstance(confidence, (int, float)) or not math.isfinite(float(confidence)) or not 0 <= float(confidence) <= 1:
        issues.append({"field": "confidence", "message": "must be a finite number from 0 to 1"})
    else:
        cleaned["confidence"] = float(confidence)
    for field in ("fact_warnings", "quality_warnings"):
        values = payload.get(field)
        if not isinstance(values, list):
            issues.append({"field": field, "message": "must be a list of strings"})
            cleaned[field] = []
        else:
            if len(values) > MAX_WARNINGS_PER_FIELD:
                issues.append({"field": field, "message": f"must not contain more than {MAX_WARNINGS_PER_FIELD} warnings"})
            cleaned[field] = [_safe_text(value, f"{field}[{index}]", issues, max_chars=MAX_WARNING_CHARS) for index, value in enumerate(values)]
    warning_total = sum(len(value) for field in ("fact_warnings", "quality_warnings") for value in cleaned.get(field, []) if isinstance(value, str))
    if warning_total > MAX_WARNINGS_TOTAL_CHARS:
        issues.append({"field": "warnings", "message": f"combined warning text must not exceed {MAX_WARNINGS_TOTAL_CHARS} characters"})
    cleaned["rule_version"] = _safe_text(payload.get("rule_version"), "rule_version", issues, max_chars=MAX_RULE_VERSION_CHARS)
    expected_version = active_rules.get("rule_version")
    if expected_version is not None:
        expected = _safe_text(expected_version, "rules.rule_version", issues)
        if cleaned["rule_version"] != expected:
            issues.append({"field": "rule_version", "message": "must match the active rule version"})
    if issues:
        raise OutputValidationError(issues)
    return cleaned


def load_json(path: str | Path) -> Any:
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise OutputValidationError([{"field": "$", "message": "could not safely parse JSON input"}]) from exc


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("payload")
    parser.add_argument("--rules")
    args = parser.parse_args()
    try:
        result = validate_generated(load_json(args.payload), load_json(args.rules) if args.rules else {})
        print(json.dumps(result, ensure_ascii=False))
        return 0
    except OutputValidationError as exc:
        print(json.dumps(exc.as_dict(), ensure_ascii=False))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
