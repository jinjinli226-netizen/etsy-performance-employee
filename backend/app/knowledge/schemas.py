from __future__ import annotations

import re
import unicodedata
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator


class KnowledgeStatus(StrEnum):
    PROPOSED = "proposed"
    TESTING = "testing"
    ACTIVE = "active"
    REJECTED = "rejected"
    ROLLED_BACK = "rolled_back"


class KnowledgeKind(StrEnum):
    TITLE_STRUCTURE = "title_structure"
    TAG_TAXONOMY = "tag_taxonomy"
    OCCASION_VOCABULARY = "occasion_vocabulary"
    BUYER_INSTRUCTION_STYLE = "buyer_instruction_style"
    CATEGORY_MAPPING = "category_mapping"
    MATERIAL_INFERENCE = "material_inference"
    SIZE_INFERENCE = "size_inference"
    BUNDLE_CONTENTS = "bundle_contents"
    ACCESSORY_INFERENCE = "accessory_inference"
    PRICING = "pricing"
    SHIPPING = "shipping"
    GUARANTEE = "guarantee"
    CERTIFICATION = "certification"


_CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
_SECRET = re.compile(
    r"(?:api[_ -]?key|access[_ -]?token|refresh[_ -]?token|cookie|authorization|bearer)\s*[:=]|\bsk-[A-Za-z0-9_-]{12,}",
    re.IGNORECASE,
)
_INJECTION = re.compile(
    r"ignore\s+(?:all\s+)?(?:previous|prior)|system\s+prompt|developer\s+message|follow\s+these\s+instructions|reveal\s+(?:the\s+)?prompt",
    re.IGNORECASE,
)
_RAW_MARKER = re.compile(r"https?://|\b(?:competitor|evidence|snapshot|raw\s+listing|source\s+url)\b", re.IGNORECASE)


def normalized_text(value: str, *, reject_unsafe: bool = False) -> str:
    value = unicodedata.normalize("NFKC", value).strip()
    if _CONTROL.search(value):
        raise ValueError("control characters are not allowed")
    if reject_unsafe and (_SECRET.search(value) or _INJECTION.search(value) or _RAW_MARKER.search(value)):
        raise ValueError("raw, secret, or instructional content is not allowed in generation knowledge")
    return value


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class EvidenceInput(StrictModel):
    url: str = Field(min_length=20, max_length=2048)
    title: str = Field(min_length=1, max_length=500)
    snapshot: str = Field(min_length=1, max_length=20_000)
    tags: list[str] = Field(default_factory=list, max_length=50)
    source_timestamp: datetime

    @field_validator("url")
    @classmethod
    def etsy_https_listing_only(cls, value: str) -> str:
        from urllib.parse import urlsplit

        value = normalized_text(value)
        parsed = urlsplit(value)
        if parsed.scheme != "https" or parsed.hostname not in {"etsy.com", "www.etsy.com"}:
            raise ValueError("only https Etsy listing URLs are accepted")
        if parsed.username or parsed.password or parsed.port not in {None, 443}:
            raise ValueError("URL credentials and custom ports are not accepted")
        if not re.fullmatch(r"/listing/[0-9]+(?:/[^/?#]*)?/?", parsed.path):
            raise ValueError("an Etsy listing URL is required")
        return value

    @field_validator("title", "snapshot")
    @classmethod
    def clean_raw_text(cls, value: str) -> str:
        return normalized_text(value)

    @field_validator("tags")
    @classmethod
    def clean_tags(cls, values: list[str]) -> list[str]:
        cleaned = [normalized_text(value) for value in values]
        if any(not value or len(value) > 100 for value in cleaned):
            raise ValueError("invalid evidence tag")
        return cleaned

    @field_validator("source_timestamp")
    @classmethod
    def aware_timestamp(cls, value: datetime) -> datetime:
        if value.tzinfo is None:
            raise ValueError("source timestamp must include a timezone")
        return value.astimezone(UTC)


class EvidenceReference(StrictModel):
    evidence_id: str = Field(pattern=r"^ev-[0-9a-f]{32}$")
    source_timestamp: datetime

    @field_validator("source_timestamp")
    @classmethod
    def aware_timestamp(cls, value: datetime) -> datetime:
        if value.tzinfo is None:
            raise ValueError("source timestamp must include a timezone")
        return value.astimezone(UTC)


class CandidateInput(StrictModel):
    kind: str = Field(min_length=1, max_length=127, pattern=r"^[a-z][a-z0-9_-]*$")
    abstract: str = Field(min_length=12, max_length=2000)
    confidence: float = Field(ge=0, le=1)
    evidence_refs: list[EvidenceReference] = Field(default_factory=list, max_length=100)

    @field_validator("kind", "abstract")
    @classmethod
    def safe_generation_text(cls, value: str) -> str:
        return normalized_text(value, reject_unsafe=True)


class ActivePatternRead(StrictModel):
    id: str
    kind: str
    abstract: str
    rule_version: str


class CandidateRead(StrictModel):
    id: int
    public_id: str
    kind: str
    abstract: str
    confidence: float
    evidence_count: int
    accepted_edit_count: int
    status: KnowledgeStatus
    created_at: datetime
    updated_at: datetime


class PatternPage(StrictModel):
    items: list[ActivePatternRead]
    total: int
    limit: int
    offset: int


class CandidatePage(StrictModel):
    items: list[CandidateRead]
    total: int
    limit: int
    offset: int


class CandidateStatusRead(StrictModel):
    id: str
    status: KnowledgeStatus


class PatternTransitionRead(StrictModel):
    id: int
    public_id: str
    kind: str
    abstract: str
    rule_version: str
    status: KnowledgeStatus


class KnowledgeCandidateCreate(BaseModel):
    title: str
    proposal: dict[str, Any]


class KnowledgeCandidateRead(KnowledgeCandidateCreate):
    model_config = ConfigDict(from_attributes=True)

    id: int
    status: KnowledgeStatus
    created_at: datetime
    updated_at: datetime


class RuleVersionRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    version: str
    rules: dict[str, Any]
    status: KnowledgeStatus
    created_at: datetime
