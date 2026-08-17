from __future__ import annotations

import sys

sys.dont_write_bytecode = True

import json
import re
import unicodedata
from typing import Any


VISIBLE_FIELDS = (
    "product_family",
    "colors",
    "silhouette",
    "garment_structure",
    "decorations",
    "visible_components",
    "visual_style",
)
ROOT_FIELDS = {
    "schema_version",
    "visible_facts",
    "uncertain_observations",
    "forbidden_inferences",
    "image_usable",
}
MAX_ITEMS = 20
MAX_TEXT_CHARS = 200
MAX_CONTEXT_BYTES = 32 * 1024
_CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
_PATH_OR_URL = re.compile(r"https?://|(?:^|\s)[A-Za-z]:[\\/]|(?:^|\s)/(?:home|users|tmp)/", re.IGNORECASE)


class VisualContextError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def _text(value: Any, field: str) -> str:
    if not isinstance(value, str):
        raise VisualContextError("invalid_visual_context", f"{field} must contain strings.")
    cleaned = " ".join(unicodedata.normalize("NFKC", value).split())
    if not cleaned or len(cleaned) > MAX_TEXT_CHARS or _CONTROL.search(cleaned):
        raise VisualContextError("invalid_visual_context", f"{field} contains invalid text.")
    if cleaned.startswith(("=", "+", "-", "@")) or _PATH_OR_URL.search(cleaned):
        raise VisualContextError("invalid_visual_context", f"{field} contains unsafe text.")
    return cleaned


def _text_list(value: Any, field: str) -> list[str]:
    if not isinstance(value, list) or len(value) > MAX_ITEMS:
        raise VisualContextError("invalid_visual_context", f"{field} must be a bounded list.")
    result = [_text(item, field) for item in value]
    normalized = [item.casefold() for item in result]
    if len(normalized) != len(set(normalized)):
        raise VisualContextError("invalid_visual_context", f"{field} must not contain duplicates.")
    return result


def validate_visual_context(payload: Any) -> dict[str, Any]:
    if not isinstance(payload, dict) or set(payload) != ROOT_FIELDS:
        raise VisualContextError("invalid_visual_context", "The visual response has an invalid root schema.")
    if payload.get("schema_version") != 1 or not isinstance(payload.get("image_usable"), bool):
        raise VisualContextError("invalid_visual_context", "The visual response version or usability flag is invalid.")
    facts = payload.get("visible_facts")
    if not isinstance(facts, dict) or set(facts) != set(VISIBLE_FIELDS):
        raise VisualContextError("invalid_visual_context", "The visual facts have an invalid schema.")
    result = {
        "schema_version": 1,
        "visible_facts": {field: _text_list(facts[field], f"visible_facts.{field}") for field in VISIBLE_FIELDS},
        "uncertain_observations": _text_list(payload.get("uncertain_observations"), "uncertain_observations"),
        "forbidden_inferences": _text_list(payload.get("forbidden_inferences"), "forbidden_inferences"),
        "image_usable": payload["image_usable"],
    }
    encoded = json.dumps(result, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    if len(encoded) > MAX_CONTEXT_BYTES:
        raise VisualContextError("invalid_visual_context", "The visual context exceeds its safe size limit.")
    return result


def schema() -> dict[str, Any]:
    string_list = {
        "type": "array",
        "maxItems": MAX_ITEMS,
        "uniqueItems": True,
        "items": {"type": "string", "minLength": 1, "maxLength": MAX_TEXT_CHARS},
    }
    return {
        "type": "object",
        "additionalProperties": False,
        "required": sorted(ROOT_FIELDS),
        "properties": {
            "schema_version": {"const": 1},
            "visible_facts": {
                "type": "object",
                "additionalProperties": False,
                "required": list(VISIBLE_FIELDS),
                "properties": {field: dict(string_list) for field in VISIBLE_FIELDS},
            },
            "uncertain_observations": dict(string_list),
            "forbidden_inferences": dict(string_list),
            "image_usable": {"type": "boolean"},
        },
    }


def merge_product_context(row_context: dict[str, Any], visual: dict[str, Any]) -> dict[str, Any]:
    validated = validate_visual_context(visual)
    return {
        "candidate_fields": row_context["candidate_fields"],
        "row_warnings": row_context["row_warnings"],
        "visual_context": validated,
        "conflict_policy": "candidate_fields_override_visual_observations",
        "fact_policy": "visual_context_may_supply_visible_attributes_only",
    }
