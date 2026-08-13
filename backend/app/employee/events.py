from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from app.knowledge.schemas import KnowledgeKind


MAX_ENVELOPE_BYTES = 128 * 1024
_CONTROL_KEY = re.compile(r'"(?:event|type)"', re.IGNORECASE)
_CONTROL_VALUE = re.compile(r'(?:knowledge_candidate|learning_batch)', re.IGNORECASE)


class KnowledgeCandidatePayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: KnowledgeKind
    summary: str = Field(min_length=1, max_length=2000)
    confidence: float = Field(ge=0, le=1)
    evidence_ids: list[str] = Field(default_factory=list, max_length=100)
    source_timestamps: dict[str, str] = Field(default_factory=dict)


class LearningEvidencePayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    url: str = Field(min_length=20, max_length=2048)
    title: str = Field(min_length=1, max_length=500)
    snapshot: str = Field(min_length=1, max_length=20_000)
    tags: list[str] = Field(default_factory=list, max_length=50)
    source_timestamp: str = Field(min_length=10, max_length=64)


class LearningCandidatePayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: KnowledgeKind
    summary: str = Field(min_length=1, max_length=2000)
    confidence: float = Field(ge=0, le=1)
    evidence_urls: list[str] = Field(default_factory=list, max_length=100)
    evidence_ids: list[str] = Field(default_factory=list, max_length=100)


class LearningBatchPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    evidence_items: list[LearningEvidencePayload] = Field(default_factory=list, max_length=5)
    candidates: list[LearningCandidatePayload] = Field(default_factory=list, max_length=5)


@dataclass(frozen=True)
class ParsedEmployeeReply:
    visible_text: str
    envelopes: list[dict[str, Any]]
    control_errors: list[str]


def parse_final_envelopes(text: str) -> ParsedEmployeeReply:
    """Strip every suspected control frame, including malformed or oversized frames."""
    visible: list[str] = []
    envelopes: list[dict[str, Any]] = []
    errors: list[str] = []
    for line in text.rstrip().splitlines():
        raw = line.strip()
        suspected_control = raw.startswith("{") and _CONTROL_KEY.search(raw) and _CONTROL_VALUE.search(raw)
        if not suspected_control:
            visible.append(line)
            continue
        if len(raw.encode("utf-8")) > MAX_ENVELOPE_BYTES:
            errors.append("envelope_invalid")
            continue
        try:
            value = json.loads(raw)
        except (json.JSONDecodeError, UnicodeError):
            errors.append("envelope_invalid")
            continue
        if not isinstance(value, dict) or value.get("event") not in {"knowledge_candidate", "learning_batch"}:
            errors.append("envelope_invalid")
            continue
        payload = value.get("payload")
        if not isinstance(payload, dict):
            errors.append("envelope_invalid")
            continue
        try:
            model = KnowledgeCandidatePayload if value["event"] == "knowledge_candidate" else LearningBatchPayload
            validated = model.model_validate(payload)
        except ValidationError:
            errors.append("envelope_invalid")
            continue
        envelopes.append({"event": value["event"], "payload": validated.model_dump(mode="json")})
    return ParsedEmployeeReply(visible_text="\n".join(visible).strip(), envelopes=envelopes, control_errors=errors)
