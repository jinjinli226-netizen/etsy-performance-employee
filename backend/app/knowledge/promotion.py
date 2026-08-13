from __future__ import annotations

from dataclasses import dataclass


AUTO_PROMOTION_CONFIDENCE = 0.85
AUTO_PROMOTION_SUPPORT = 3


@dataclass(frozen=True)
class PromotionDecision:
    eligible: bool
    reason: str


def decide_promotion(
    *, confidence: float, independent_evidence: int, accepted_edits: int, hard_conflict: bool, regression_passed: bool, originality_passed: bool
) -> PromotionDecision:
    if hard_conflict:
        return PromotionDecision(False, "hard_rule_conflict")
    if not regression_passed:
        return PromotionDecision(False, "regression_failed")
    if not originality_passed:
        return PromotionDecision(False, "raw_similarity")
    if confidence < AUTO_PROMOTION_CONFIDENCE:
        return PromotionDecision(False, "confidence_below_threshold")
    if max(independent_evidence, accepted_edits) < AUTO_PROMOTION_SUPPORT:
        return PromotionDecision(False, "insufficient_independent_support")
    return PromotionDecision(True, "eligible")
