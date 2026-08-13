from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from app.knowledge.schemas import KnowledgeKind


MAX_ENVELOPE_BYTES = 128 * 1024
MAX_REPLY_BYTES = 1024 * 1024
MAX_ENVELOPE_LINES = 256
MAX_JSON_NODES = 2048
MAX_JSON_DEPTH = 32
_KNOWN_CONTROL = {"knowledge_candidate", "learning_batch"}
_RESERVED_PREFIXES = ("control", "learning", "profile", "operation")


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
    """Decode bounded JSON objects before deciding whether they are control frames."""
    if len(text.encode("utf-8")) > MAX_REPLY_BYTES:
        return ParsedEmployeeReply(visible_text="", envelopes=[], control_errors=["envelope_invalid"])
    lines = text.rstrip().splitlines()
    visible: list[str] = []
    envelopes: list[dict[str, Any]] = []
    errors: list[str] = []
    index = 0
    while index < len(lines):
        line = lines[index]
        raw = line.strip()
        if not raw.startswith(("{", "[")):
            visible.append(line)
            index += 1
            continue
        block, consumed, complete, oversized = _bounded_json_object(lines, index)
        index += consumed
        if oversized or not complete:
            errors.append("envelope_invalid")
            if not complete:
                index = len(lines)
            continue
        try:
            value = json.loads(block)
        except (json.JSONDecodeError, UnicodeError, RecursionError):
            errors.append("envelope_invalid")
            continue
        classification = _classify_control_shapes(value)
        if classification in {"nested", "reserved", "limit"}:
            errors.append("envelope_invalid")
            continue
        if not isinstance(value, dict):
            visible.extend(block.splitlines())
            continue
        event = value.get("event")
        fallback_type = value.get("type")
        if event not in {"knowledge_candidate", "learning_batch"}:
            if fallback_type in {"knowledge_candidate", "learning_batch"}:
                errors.append("envelope_invalid")
            else:
                visible.extend(block.splitlines())
            continue
        payload = value.get("payload")
        if not isinstance(payload, dict):
            errors.append("envelope_invalid")
            continue
        try:
            model = KnowledgeCandidatePayload if event == "knowledge_candidate" else LearningBatchPayload
            validated = model.model_validate(payload)
        except ValidationError:
            errors.append("envelope_invalid")
            continue
        envelopes.append({"event": event, "payload": validated.model_dump(mode="json")})
    return ParsedEmployeeReply(visible_text="\n".join(visible).strip(), envelopes=envelopes, control_errors=errors)


def _classify_control_shapes(value: Any) -> str:
    nodes = 0
    found: list[tuple[int, str]] = []
    stack: list[tuple[Any, int]] = [(value, 0)]
    while stack:
        current, depth = stack.pop()
        nodes += 1
        if nodes > MAX_JSON_NODES or depth > MAX_JSON_DEPTH:
            return "limit"
        if isinstance(current, dict):
            for key in ("event", "type"):
                name = current.get(key)
                if not isinstance(name, str):
                    continue
                normalized = name.casefold()
                if normalized in _KNOWN_CONTROL:
                    found.append((depth, "known"))
                elif normalized.startswith(_RESERVED_PREFIXES):
                    found.append((depth, "reserved"))
            stack.extend((item, depth + 1) for item in current.values() if isinstance(item, (dict, list)))
        elif isinstance(current, list):
            stack.extend((item, depth + 1) for item in current if isinstance(item, (dict, list)))
    if any(kind == "reserved" for _, kind in found):
        return "reserved"
    if any(depth > 0 for depth, _ in found) or (found and not isinstance(value, dict)):
        return "nested"
    return "top" if found else "none"


def _bounded_json_object(lines: list[str], start: int) -> tuple[str, int, bool, bool]:
    block: list[str] = []
    depth = 0
    in_string = False
    escaped = False
    total = 0
    for index in range(start, len(lines)):
        line = lines[index]
        if index - start >= MAX_ENVELOPE_LINES:
            return "\n".join(block), len(block), False, True
        encoded_length = len(line.encode("utf-8"))
        total += encoded_length + (1 if block else 0)
        if encoded_length > MAX_ENVELOPE_BYTES or total > MAX_ENVELOPE_BYTES:
            consumed = len(block) + _drain_json_block(lines, index, depth, in_string, escaped)
            return "\n".join(block), consumed, False, True
        block.append(line)
        for char in line:
            if in_string:
                if escaped:
                    escaped = False
                elif char == "\\":
                    escaped = True
                elif char == '"':
                    in_string = False
                continue
            if char == '"':
                in_string = True
            elif char in "{[":
                depth += 1
            elif char in "}]":
                depth -= 1
                if depth < 0:
                    return "\n".join(block), len(block), True, False
        if depth == 0 and not in_string:
            return "\n".join(block), len(block), True, False
    return "\n".join(block), len(block), False, False


def _drain_json_block(lines: list[str], start: int, depth: int, in_string: bool, escaped: bool) -> int:
    """Count the rest of an oversized frame without retaining its raw content."""
    for index in range(start, len(lines)):
        line = lines[index]
        if index - start >= MAX_ENVELOPE_LINES:
            return index - start
        for char in line:
            if in_string:
                if escaped:
                    escaped = False
                elif char == "\\":
                    escaped = True
                elif char == '"':
                    in_string = False
                continue
            if char == '"':
                in_string = True
            elif char in "{[":
                depth += 1
            elif char in "}]":
                depth -= 1
        if depth <= 0 and not in_string:
            return index - start + 1
    return len(lines) - start
