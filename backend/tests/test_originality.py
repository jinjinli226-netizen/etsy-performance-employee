from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

from app.knowledge.originality import OriginalityGuard


SCRIPTS = Path(__file__).resolve().parents[2] / "employee" / "skills" / "etsy-performance-listing" / "scripts"


def _load_employee_guard():
    path = SCRIPTS / "originality_guard.py"
    spec = importlib.util.spec_from_file_location("employee_originality_guard", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_originality_blocks_copied_listing_without_returning_raw_text() -> None:
    guard = OriginalityGuard(threshold=0.62)
    evidence = [
        (
            "ev-aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
            "Velvet Vampire Cape for Women Dramatic Gothic Halloween Costume",
        )
    ]

    result = guard.check(
        {
            "head_titles": "Velvet vampire cape for women, dramatic gothic Halloween costume!",
            "specification": "",
            "instructions_for_buyers": "",
        },
        evidence,
    )

    assert result.passed is False
    assert result.evidence_id == "ev-aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
    assert 0.62 <= result.max_score <= 1
    assert set(result.as_dict()) == {"passed", "max_score", "evidence_id"}
    assert "Velvet" not in repr(result.as_dict())
    assert not hasattr(result, "matched_text")


def test_originality_normalizes_unicode_case_punctuation_and_whitespace() -> None:
    guard = OriginalityGuard(threshold=0.6)

    result = guard.check_texts(
        ["ＦＡＮＣＹ   Dance—Costume, sparkle stage outfit"],
        [("ev-bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb", "fancy dance costume sparkle stage outfit")],
    )

    assert result.passed is False


def test_originality_handles_empty_short_and_chinese_text_without_leaking() -> None:
    guard = OriginalityGuard(threshold=0.7, max_evidence=2, max_chars_per_text=100)

    assert guard.check_texts([], []).passed is True
    assert guard.check_texts(["cape"], [("ev-a", "cape")]).passed is False
    copied = guard.check_texts(["舞台 表演 服装 闪亮 成人"], [("ev-c", "舞台表演服装闪亮成人")])
    assert copied.passed is False
    assert copied.as_dict()["evidence_id"] == "ev-c"


@pytest.mark.parametrize(
    ("generated", "evidence", "threshold"),
    [
        (["舞台表演服装闪亮成人"], [("ev-cn", "舞台表演服装闪亮成人")], 0.72),
        (["ＶＥＬＶＥＴ　ＣＡＰＥ"], [("ev-fw", "velvet cape")], 0.72),
        (["舞台 velvet 表演 cape 服装"], [("ev-mix", "舞台 VELVET 表演 CAPE 服装")], 0.72),
        (["dramatic velvet vampire cape for women"], [("ev-en", "Dramatic velvet vampire cape for women")], 0.72),
        (["cape"], [("ev-short", "CAPE")], 0.72),
        (["velvet stage cape"], [("ev-edge", "velvet stage cape")], 1.0),
    ],
)
def test_backend_and_employee_originality_implementations_are_identical(generated, evidence, threshold) -> None:
    employee = _load_employee_guard()
    backend = OriginalityGuard(threshold=threshold).check_texts(generated, evidence)
    skill = employee.check_originality(generated, evidence, threshold=threshold)

    assert skill == {
        "passed": backend.passed,
        "score": backend.max_score,
        "evidence_id": backend.evidence_id,
    }
