from __future__ import annotations

from app.knowledge.originality import OriginalityGuard


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
    assert guard.check_texts(["cape"], [("ev-a", "cape")]).passed is True
    copied = guard.check_texts(["舞台 表演 服装 闪亮 成人"], [("ev-c", "舞台表演服装闪亮成人")])
    assert copied.passed is False
    assert copied.as_dict()["evidence_id"] == "ev-c"

