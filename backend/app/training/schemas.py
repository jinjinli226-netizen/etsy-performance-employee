from __future__ import annotations

import re
import unicodedata
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.knowledge.schemas import KnowledgeStatus


_CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
KnowledgeKind = Literal[
    "title_structure",
    "tag_taxonomy",
    "occasion_vocabulary",
    "buyer_instruction_style",
    "category_mapping",
]


def _clean_text(value: str, *, maximum: int = 200) -> str:
    if not isinstance(value, str):
        raise ValueError("must be a string")
    cleaned = unicodedata.normalize("NFKC", value).strip()
    if not cleaned or len(cleaned) > maximum or _CONTROL.search(cleaned):
        raise ValueError("contains invalid text")
    return cleaned


def _clean_list(values: list[str], *, maximum_items: int = 20, maximum_chars: int = 200) -> list[str]:
    if len(values) > maximum_items:
        raise ValueError("contains too many items")
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        cleaned = _clean_text(value, maximum=maximum_chars)
        key = cleaned.casefold()
        if key not in seen:
            seen.add(key)
            result.append(cleaned)
    return result


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class VisibleFacts(StrictModel):
    product_family: list[str] = Field(max_length=20)
    colors: list[str] = Field(max_length=20)
    silhouette: list[str] = Field(max_length=20)
    garment_structure: list[str] = Field(max_length=20)
    decorations: list[str] = Field(max_length=20)
    visible_components: list[str] = Field(max_length=20)
    visual_style: list[str] = Field(max_length=20)

    @field_validator("*")
    @classmethod
    def safe_visible_items(cls, values: list[str]) -> list[str]:
        return _clean_list(values)


class VisualAnalysis(StrictModel):
    schema_version: Literal[1]
    visible_facts: VisibleFacts
    uncertain_observations: list[str] = Field(max_length=20)
    forbidden_inferences: list[str] = Field(max_length=20)
    image_usable: bool

    @field_validator("uncertain_observations", "forbidden_inferences")
    @classmethod
    def safe_observations(cls, values: list[str]) -> list[str]:
        return _clean_list(values)


class FactValue(StrictModel):
    value: str = Field(min_length=1, max_length=200)
    source: Literal["text", "visual"]
    source_field: str = Field(min_length=1, max_length=80)

    @field_validator("value", "source_field")
    @classmethod
    def safe_text(cls, value: str) -> str:
        return _clean_text(value)


class FactConflict(StrictModel):
    field: str = Field(min_length=1, max_length=80)
    text_values: list[str] = Field(max_length=20)
    visual_values: list[str] = Field(max_length=20)

    @field_validator("field")
    @classmethod
    def safe_field(cls, value: str) -> str:
        return _clean_text(value, maximum=80)

    @field_validator("text_values", "visual_values")
    @classmethod
    def safe_values(cls, values: list[str]) -> list[str]:
        return _clean_list(values)


class MergedFacts(StrictModel):
    facts: dict[str, list[FactValue]]
    conflicts: list[FactConflict] = Field(max_length=50)
    visual_contributions: dict[str, list[str]]


class CandidateProposal(StrictModel):
    kind: KnowledgeKind
    abstract: str = Field(min_length=12, max_length=2000)
    confidence: float = Field(ge=0, le=1)

    @field_validator("abstract")
    @classmethod
    def safe_abstract(cls, value: str) -> str:
        return _clean_text(value, maximum=2000)


class CandidateSet(StrictModel):
    schema_version: Literal[1]
    candidates: list[CandidateProposal] = Field(max_length=5)

    @model_validator(mode="after")
    def distinct_kinds(self) -> "CandidateSet":
        kinds = [candidate.kind for candidate in self.candidates]
        if len(kinds) != len(set(kinds)):
            raise ValueError("candidate kinds must be distinct")
        return self


class ReviewItem(StrictModel):
    kind: KnowledgeKind
    decision: Literal["approve", "reject"]
    reason_code: str = Field(min_length=1, max_length=63, pattern=r"^[a-z][a-z0-9_]*$")
    reason: str = Field(min_length=8, max_length=500)
    risk_flags: list[str] = Field(max_length=20)
    confidence: float = Field(ge=0, le=1)

    @field_validator("reason")
    @classmethod
    def safe_reason(cls, value: str) -> str:
        return _clean_text(value, maximum=500)

    @field_validator("risk_flags")
    @classmethod
    def safe_risks(cls, values: list[str]) -> list[str]:
        cleaned = _clean_list(values, maximum_chars=63)
        if any(not re.fullmatch(r"[a-z][a-z0-9_]*", value) for value in cleaned):
            raise ValueError("risk flags must be stable codes")
        return cleaned


class ReviewSet(StrictModel):
    schema_version: Literal[1]
    reviews: list[ReviewItem] = Field(max_length=5)

    @model_validator(mode="after")
    def distinct_kinds(self) -> "ReviewSet":
        kinds = [review.kind for review in self.reviews]
        if len(kinds) != len(set(kinds)):
            raise ValueError("review kinds must be distinct")
        return self


class ActiveToken(StrictModel):
    active_rule_public_id: str | None = Field(default=None, max_length=36)
    pattern_revision: int | None = Field(default=None, ge=0)


class TrainingActivationResult(StrictModel):
    candidate_id: int
    candidate_public_id: str
    kind: KnowledgeKind
    status: KnowledgeStatus
    review_public_id: str
    activated_rule_version: str | None = None
    not_activated_reason: str | None = None
