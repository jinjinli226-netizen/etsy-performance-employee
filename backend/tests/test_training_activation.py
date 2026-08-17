from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest
from sqlalchemy import func, select

from app.db.init_db import init_db
from app.db.models import AuditEvent, KnowledgeCandidate, KnowledgePattern, RuleVersion, TrainingReview
from app.db.session import create_engine_for_url, create_session_factory
from app.knowledge.schemas import CandidateInput, EvidenceInput, KnowledgeStatus
from app.knowledge.service import KnowledgeService
from app.training.repository import TrainingRepository
from app.training.schemas import ActiveToken, CandidateSet, ReviewSet


KINDS = (
    "title_structure",
    "tag_taxonomy",
    "occasion_vocabulary",
    "buyer_instruction_style",
    "category_mapping",
)
SAFE_ABSTRACTS = {
    "title_structure": "Lead with the core product type, then add visible style and use context.",
    "tag_taxonomy": "Cover distinct product, style, occasion, audience, and use-intent search entries.",
    "occasion_vocabulary": "Use an occasion term only when the supplied product facts explicitly support it.",
    "buyer_instruction_style": "Ask buyers to verify supplied measurements and visible options before ordering.",
    "category_mapping": "Choose the deepest category by primary wearable function before decorative theme.",
}


@pytest.fixture
def services(tmp_path: Path):
    engine = create_engine_for_url(f"sqlite:///{tmp_path / 'activation.db'}")
    init_db(engine)
    factory = create_session_factory(engine)
    repository = TrainingRepository(factory)
    run = repository.create_run(
        source_workbook_hash="a" * 64,
        source_workbook_name="shops.xlsx",
        requested_limit=1,
    )
    sample = repository.claim_sample(
        run_id=run.id,
        shop_url="https://www.etsy.com/shop/StageWear",
        listing_id="123456",
        canonical_url="https://www.etsy.com/listing/123456",
    )
    repository.transition_sample(sample.id, "fetching")
    repository.transition_sample(
        sample.id,
        "image_ready",
        listing_snapshot_hash="b" * 64,
        main_image_hash="c" * 64,
        main_image_path="training-evidence/example.jpg",
    )
    repository.transition_sample(
        sample.id,
        "facts_ready",
        visual_facts={"schema_version": 1},
        merged_facts={"facts": {}},
        conflicts=[],
    )
    repository.transition_sample(sample.id, "reviewing")
    service = KnowledgeService(factory, export_dir=tmp_path / "trust")
    try:
        yield service, factory, sample.id
    finally:
        engine.dispose()


def evidence() -> EvidenceInput:
    return EvidenceInput(
        url="https://www.etsy.com/listing/123456",
        title="Navy performance costume",
        snapshot="A navy performance costume with visible gold appliqué and long sleeves.",
        tags=["dance costume"],
        source_timestamp=datetime(2026, 8, 17, tzinfo=UTC),
    )


def candidates(kinds=KINDS, *, overrides: dict[str, str] | None = None) -> CandidateSet:
    replacements = overrides or {}
    return CandidateSet.model_validate(
        {
            "schema_version": 1,
            "candidates": [
                {
                    "kind": kind,
                    "abstract": replacements.get(kind, SAFE_ABSTRACTS[kind]),
                    "confidence": 0.5,
                }
                for kind in kinds
            ],
        }
    )


def reviews(kinds=KINDS, *, decision="approve", confidence=0.91, risk_flags=None) -> ReviewSet:
    return ReviewSet.model_validate(
        {
            "schema_version": 1,
            "reviews": [
                {
                    "kind": kind,
                    "decision": decision,
                    "reason_code": "net_improvement" if decision == "approve" else "not_supported",
                    "reason": "The proposal is reusable and evidence bounded." if decision == "approve" else "The proposal is not a net improvement.",
                    "risk_flags": list(risk_flags or []),
                    "confidence": confidence,
                }
                for kind in kinds
            ],
        }
    )


def empty_tokens(kinds=KINDS) -> dict[str, ActiveToken]:
    return {kind: ActiveToken(active_rule_public_id=None, pattern_revision=None) for kind in kinds}


def apply(service, sample_id, candidate_set, review_set, tokens):
    return service.apply_reviewed_training_batch(
        sample_id=sample_id,
        evidence=evidence(),
        candidates=candidate_set,
        reviews=review_set,
        reviewed_active_tokens=tokens,
        reviewer_version="reviewer-v1",
        trace_id="11111111-1111-4111-8111-111111111111",
    )


def test_ai_approved_five_kind_batch_activates_with_review_lineage(services) -> None:
    service, factory, sample_id = services

    results = apply(service, sample_id, candidates(), reviews(), empty_tokens())

    assert {item.status for item in results} == {KnowledgeStatus.ACTIVE}
    assert all(item.activated_rule_version for item in results)
    with factory() as session:
        assert session.scalar(select(func.count()).select_from(TrainingReview)) == 5
        assert session.scalar(select(func.count()).select_from(KnowledgePattern)) == 5
        assert session.scalar(
            select(func.count()).select_from(RuleVersion).where(RuleVersion.status == KnowledgeStatus.ACTIVE)
        ) == 5
        activation_audits = list(
            session.scalars(
                select(AuditEvent).where(
                    AuditEvent.actor == "system:ai-review",
                    AuditEvent.action == "candidate_activated",
                )
            )
        )
        assert len(activation_audits) == 5
        review_ids = set(session.scalars(select(TrainingReview.public_id)))
        assert all(event.details.get("review_id") in review_ids for event in activation_audits)


def test_ai_reject_stays_proposed(services) -> None:
    service, factory, sample_id = services
    one_kind = ("title_structure",)
    rejected = apply(
        service,
        sample_id,
        candidates(one_kind),
        reviews(one_kind, decision="reject"),
        empty_tokens(one_kind),
    )

    assert rejected[0].status is KnowledgeStatus.PROPOSED
    assert rejected[0].not_activated_reason == "ai_rejected"
    with factory() as session:
        stored = session.scalar(select(TrainingReview).where(TrainingReview.training_sample_id == sample_id))
        assert stored is not None
        assert stored.not_activated_reason == "ai_rejected"
        assert session.scalar(select(func.count()).select_from(KnowledgePattern)) == 0


@pytest.mark.parametrize(
    ("review_set", "reason"),
    [
        (reviews(("title_structure",), confidence=0.84), "review_confidence"),
        (reviews(("title_structure",), risk_flags=["unverified_claim"]), "review_risk_flags"),
    ],
)
def test_low_confidence_or_risky_ai_approval_stays_proposed(services, review_set, reason) -> None:
    service, factory, sample_id = services

    result = apply(
        service,
        sample_id,
        candidates(("title_structure",)),
        review_set,
        empty_tokens(("title_structure",)),
    )[0]

    assert result.status is KnowledgeStatus.PROPOSED
    assert result.not_activated_reason == reason
    with factory() as session:
        assert session.scalar(select(func.count()).select_from(KnowledgePattern)) == 0


def test_deterministically_unsafe_approved_candidate_becomes_rejected(services) -> None:
    service, factory, sample_id = services
    one_kind = ("title_structure",)
    unsafe = candidates(one_kind, overrides={"title_structure": "Always claim guaranteed genuine silk material for every product."})

    result = apply(service, sample_id, unsafe, reviews(one_kind), empty_tokens(one_kind))[0]

    assert result.status is KnowledgeStatus.REJECTED
    assert result.not_activated_reason == "policy_fact_conflict"
    with factory() as session:
        candidate = session.get(KnowledgeCandidate, result.candidate_id)
        assert candidate is not None and candidate.status is KnowledgeStatus.REJECTED
        assert session.scalar(select(func.count()).select_from(KnowledgePattern)) == 0


def test_stale_review_token_does_not_overwrite_current_active_rule(services) -> None:
    service, factory, sample_id = services
    seed = service.ingest_candidate(
        CandidateInput(
            kind="title_structure",
            abstract="Put the verified product type before secondary style descriptors.",
            confidence=0.5,
            evidence_refs=[],
        ),
        actor="owner",
        trace_id="seed",
    )
    pattern = service.approve_candidate(seed.id, actor="owner")
    with factory() as session:
        active_before = session.scalar(
            select(RuleVersion).where(
                RuleVersion.pattern_id == pattern.id,
                RuleVersion.status == KnowledgeStatus.ACTIVE,
            )
        )
        assert active_before is not None
        active_version = active_before.version

    result = apply(
        service,
        sample_id,
        candidates(("title_structure",)),
        reviews(("title_structure",)),
        empty_tokens(("title_structure",)),
    )[0]

    assert result.status is KnowledgeStatus.PROPOSED
    assert result.not_activated_reason == "stale_review"
    with factory() as session:
        active_after = session.scalar(
            select(RuleVersion).where(
                RuleVersion.pattern_id == pattern.id,
                RuleVersion.status == KnowledgeStatus.ACTIVE,
            )
        )
        assert active_after is not None and active_after.version == active_version


def test_reviewed_batch_is_idempotent_for_same_sample(services) -> None:
    service, factory, sample_id = services
    one_kind = ("title_structure",)
    first = apply(service, sample_id, candidates(one_kind), reviews(one_kind), empty_tokens(one_kind))
    second = apply(service, sample_id, candidates(one_kind), reviews(one_kind), empty_tokens(one_kind))

    assert second == first
    with factory() as session:
        assert session.scalar(select(func.count()).select_from(TrainingReview)) == 1
        assert session.scalar(select(func.count()).select_from(KnowledgeCandidate)) == 1
        assert session.scalar(select(func.count()).select_from(RuleVersion)) == 1
