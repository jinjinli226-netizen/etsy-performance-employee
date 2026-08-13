from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Protocol


class PolicyValidationError(RuntimeError):
    pass


class PolicyValidatorProtocol(Protocol):
    def validate(self, kind: str, abstract: str) -> None: ...


_FACT_CLAIM = re.compile(
    r"\b(?:always\s+claim|guaranteed?|certified|medical|genuine|authentic|100%|free\s+shipping|"
    r"ships?\s+(?:in|within)|silk|cotton|polyester|leather|gold|silver|brand(?:ed)?|price|discount|"
    r"size\s+(?:is|runs)|material\s+(?:is|made))\b",
    re.IGNORECASE,
)
_FORBIDDEN = re.compile(
    r"https?://|ignore\s+(?:all\s+)?(?:previous|prior)|system\s+prompt|developer\s+message|"
    r"(?:api[_ -]?key|access[_ -]?token|cookie|authorization)\s*[:=]",
    re.IGNORECASE,
)


class PolicyValidator:
    """Deterministic production gate for low-risk abstract listing strategies."""

    def validate(self, kind: str, abstract: str) -> None:
        if not re.fullmatch(r"[a-z][a-z0-9_-]{0,126}", kind or ""):
            raise PolicyValidationError("policy_invalid_kind")
        if not isinstance(abstract, str) or not 12 <= len(abstract.strip()) <= 2000:
            raise PolicyValidationError("policy_invalid_abstract")
        if _FORBIDDEN.search(abstract) or _FACT_CLAIM.search(abstract):
            raise PolicyValidationError("policy_fact_conflict")
        # Fixed deterministic regression cases: a safe structure must remain accepted,
        # while raw URLs, instructions, and invented product facts stay rejected.
        if _FORBIDDEN.search("https://www.etsy.com/listing/1") is None:
            raise PolicyValidationError("policy_regression_failed")
        if _FACT_CLAIM.search("Always claim genuine silk material") is None:
            raise PolicyValidationError("policy_regression_failed")


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
