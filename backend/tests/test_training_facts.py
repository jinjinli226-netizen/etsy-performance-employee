from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.training.facts import merge_facts
from app.training.schemas import CandidateSet, ReviewSet, VisualAnalysis


EMPTY_VISIBLE = {
    "product_family": [],
    "colors": [],
    "silhouette": [],
    "garment_structure": [],
    "decorations": [],
    "visible_components": [],
    "visual_style": [],
}


def visual(**overrides) -> VisualAnalysis:
    fields = {**EMPTY_VISIBLE, **overrides}
    return VisualAnalysis.model_validate(
        {
            "schema_version": 1,
            "visible_facts": fields,
            "uncertain_observations": [],
            "forbidden_inferences": [],
            "image_usable": True,
        }
    )


def test_visual_schema_accepts_only_bounded_visible_fields() -> None:
    parsed = visual(
        product_family=["performance costume"],
        colors=["navy", "gold"],
        silhouette=["fitted bodice with flared skirt"],
        garment_structure=["long sleeves"],
        decorations=["sequin trim"],
        visible_components=["dress", "detached headpiece"],
        visual_style=["dramatic stagewear"],
    )

    assert parsed.schema_version == 1
    assert parsed.visible_facts.colors == ["navy", "gold"]

    with pytest.raises(ValidationError):
        VisualAnalysis.model_validate(
            {
                "schema_version": 1,
                "visible_facts": {**EMPTY_VISIBLE, "materials": ["silk"]},
                "uncertain_observations": [],
                "forbidden_inferences": [],
                "image_usable": True,
            }
        )


def test_visual_schema_rejects_control_characters_and_oversized_lists() -> None:
    with pytest.raises(ValidationError):
        visual(colors=["navy\u0000blue"])

    with pytest.raises(ValidationError):
        visual(colors=[f"color-{index}" for index in range(21)])


def test_merge_keeps_listing_text_when_image_conflicts() -> None:
    image = visual(colors=["black"], decorations=["sequins"])

    result = merge_facts(
        text={"colors": ["navy"], "materials": ["polyester"]},
        visual=image,
    )

    assert [(item.value, item.source) for item in result.facts["colors"]] == [("navy", "text")]
    assert [(item.value, item.source) for item in result.facts["materials"]] == [("polyester", "text")]
    assert [(item.value, item.source) for item in result.facts["decorations"]] == [("sequins", "visual")]
    assert [(item.field, item.text_values, item.visual_values) for item in result.conflicts] == [
        ("colors", ["navy"], ["black"])
    ]
    assert "materials" not in result.visual_contributions


def test_merge_adds_nonduplicate_compatible_visible_details() -> None:
    result = merge_facts(
        text={"colors": ["navy"], "visible_components": ["dress"]},
        visual=visual(colors=["navy"], visible_components=["dress"], decorations=["gold appliqué"]),
    )

    assert [(item.value, item.source) for item in result.facts["colors"]] == [("navy", "text")]
    assert result.visual_contributions == {"decorations": ["gold appliqué"]}
    assert result.conflicts == []


def test_unusable_image_contributes_nothing_and_forbidden_observations_never_merge() -> None:
    image = VisualAnalysis.model_validate(
        {
            "schema_version": 1,
            "visible_facts": {**EMPTY_VISIBLE, "decorations": ["beading"]},
            "uncertain_observations": ["possibly satin"],
            "forbidden_inferences": ["material resembles satin", "size appears small"],
            "image_usable": False,
        }
    )

    result = merge_facts(text={"colors": ["red"]}, visual=image)

    assert set(result.facts) == {"colors"}
    assert result.visual_contributions == {}


def test_candidate_and_review_contracts_are_exact_and_cover_five_kinds() -> None:
    candidates = CandidateSet.model_validate(
        {
            "schema_version": 1,
            "candidates": [
                {"kind": kind, "abstract": f"Reusable abstract strategy for {kind}", "confidence": 0.9}
                for kind in (
                    "title_structure",
                    "tag_taxonomy",
                    "occasion_vocabulary",
                    "buyer_instruction_style",
                    "category_mapping",
                )
            ],
        }
    )
    reviews = ReviewSet.model_validate(
        {
            "schema_version": 1,
            "reviews": [
                {
                    "kind": item.kind,
                    "decision": "approve",
                    "reason_code": "net_improvement",
                    "reason": "Adds a reusable and evidence-bound method.",
                    "risk_flags": [],
                    "confidence": 0.91,
                }
                for item in candidates.candidates
            ],
        }
    )

    assert len(candidates.candidates) == len(reviews.reviews) == 5

    with pytest.raises(ValidationError):
        CandidateSet.model_validate(
            {
                "schema_version": 1,
                "candidates": [
                    {
                        "kind": "material_inference",
                        "abstract": "Infer a material from the product image.",
                        "confidence": 0.9,
                    }
                ],
            }
        )

    with pytest.raises(ValidationError):
        ReviewSet.model_validate(
            {
                "schema_version": 1,
                "reviews": [
                    {
                        "kind": "title_structure",
                        "decision": "approve",
                        "reason_code": "net_improvement",
                        "reason": "Looks safe.",
                        "risk_flags": ["unverified_material"],
                        "confidence": 1.1,
                        "unexpected": True,
                    }
                ],
            }
        )
