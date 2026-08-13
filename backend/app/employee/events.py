from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, ValidationError


MAX_ENVELOPE_BYTES = 8 * 1024


class KnowledgeCandidatePayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: str = Field(min_length=1, max_length=127)
    summary: str = Field(min_length=1, max_length=2000)
    confidence: float = Field(ge=0, le=1)
    evidence_ids: list[str] = Field(default_factory=list, max_length=100)
    source_timestamps: dict[str, str] = Field(default_factory=dict)


class LearningEvidencePayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    url: str = Field(min_length=20, max_length=2048)
    title: str = Field(min_length=1, max_length=500)
    snapshot: str = Field(min_length=1, max_length=100_000)
    tags: list[str] = Field(default_factory=list, max_length=100)
    source_timestamp: str = Field(min_length=10, max_length=64)


class LearningCandidatePayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: str = Field(min_length=1, max_length=127)
    summary: str = Field(min_length=1, max_length=2000)
    confidence: float = Field(ge=0, le=1)
    evidence_urls: list[str] = Field(default_factory=list, max_length=100)
    evidence_ids: list[str] = Field(default_factory=list, max_length=100)


class LearningBatchPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    evidence_items: list[LearningEvidencePayload] = Field(default_factory=list, max_length=20)
    candidates: list[LearningCandidatePayload] = Field(default_factory=list, max_length=20)


@dataclass(frozen=True)
class ParsedEmployeeReply:
    visible_text: str
    envelopes: list[dict[str, Any]]


def parse_final_envelopes(text: str) -> ParsedEmployeeReply:
    """Strip only a valid, consecutive allowlisted envelope suffix."""
    lines = text.rstrip().splitlines()
    envelopes: list[dict[str, Any]] = []
    while lines:
        raw = lines[-1].strip()
        if len(raw.encode("utf-8")) > MAX_ENVELOPE_BYTES:
            break
        try:
            value = json.loads(raw)
        except (json.JSONDecodeError, UnicodeError):
            break
        if not isinstance(value, dict) or value.get("event") not in {"knowledge_candidate", "learning_batch"}:
            break
        payload = value.get("payload")
        if not isinstance(payload, dict):
            break
        try:
            model = KnowledgeCandidatePayload if value["event"] == "knowledge_candidate" else LearningBatchPayload
            validated = model.model_validate(payload)
        except ValidationError:
            break
        envelopes.insert(
            0,
            {"event": value["event"], "payload": validated.model_dump()},
        )
        lines.pop()
    return ParsedEmployeeReply(visible_text="\n".join(lines).strip(), envelopes=envelopes)
