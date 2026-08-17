from __future__ import annotations

import unicodedata
from collections.abc import Mapping, Sequence

from app.training.schemas import FactConflict, FactValue, MergedFacts, VisualAnalysis


VISIBLE_FIELDS = (
    "product_family",
    "colors",
    "silhouette",
    "garment_structure",
    "decorations",
    "visible_components",
    "visual_style",
)
TEXT_ONLY_FIELDS = frozenset(
    {
        "materials",
        "sizes",
        "bundle_contents",
        "unseen_accessories",
        "performance",
        "brand",
        "certification",
        "price",
        "inventory",
        "shipping",
    }
)
ALLOWED_TEXT_FIELDS = frozenset(VISIBLE_FIELDS) | TEXT_ONLY_FIELDS


def _key(value: str) -> str:
    return " ".join(unicodedata.normalize("NFKC", value).casefold().split())


def _clean_values(values: Sequence[str]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for raw in values:
        if not isinstance(raw, str):
            continue
        value = " ".join(unicodedata.normalize("NFKC", raw).strip().split())
        key = _key(value)
        if value and len(value) <= 200 and key not in seen:
            seen.add(key)
            result.append(value)
    return result[:20]


def merge_facts(
    text: Mapping[str, Sequence[str]],
    visual: VisualAnalysis,
) -> MergedFacts:
    facts: dict[str, list[FactValue]] = {}
    conflicts: list[FactConflict] = []
    visual_contributions: dict[str, list[str]] = {}

    text_values: dict[str, list[str]] = {}
    for field, values in text.items():
        if field not in ALLOWED_TEXT_FIELDS or isinstance(values, (str, bytes)):
            continue
        cleaned = _clean_values(values)
        if not cleaned:
            continue
        text_values[field] = cleaned
        facts[field] = [FactValue(value=value, source="text", source_field=field) for value in cleaned]

    if not visual.image_usable:
        return MergedFacts(facts=facts, conflicts=[], visual_contributions={})

    for field in VISIBLE_FIELDS:
        values = _clean_values(getattr(visual.visible_facts, field))
        if not values:
            continue
        authoritative = text_values.get(field, [])
        if not authoritative:
            facts[field] = [FactValue(value=value, source="visual", source_field=field) for value in values]
            visual_contributions[field] = values
            continue

        text_keys = {_key(value) for value in authoritative}
        visual_keys = {_key(value) for value in values}
        if text_keys.isdisjoint(visual_keys):
            conflicts.append(FactConflict(field=field, text_values=authoritative, visual_values=values))
            continue

        additions = [value for value in values if _key(value) not in text_keys]
        if additions:
            facts[field].extend(
                FactValue(value=value, source="visual", source_field=field) for value in additions
            )
            visual_contributions[field] = additions

    return MergedFacts(
        facts=facts,
        conflicts=conflicts,
        visual_contributions=visual_contributions,
    )
