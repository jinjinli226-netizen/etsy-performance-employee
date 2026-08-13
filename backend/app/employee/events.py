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
        if not isinstance(value, dict) or value.get("event") != "knowledge_candidate":
            break
        payload = value.get("payload")
        if not isinstance(payload, dict):
            break
        try:
            validated = KnowledgeCandidatePayload.model_validate(payload)
        except ValidationError:
            break
        envelopes.insert(
            0,
            {"event": "knowledge_candidate", "payload": validated.model_dump()},
        )
        lines.pop()
    return ParsedEmployeeReply(visible_text="\n".join(lines).strip(), envelopes=envelopes)
