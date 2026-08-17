from __future__ import annotations

import asyncio
import hashlib
from datetime import UTC, datetime
from pathlib import Path

import pytest
from openpyxl import Workbook
from sqlalchemy import select

from app.db.init_db import init_db
from app.db.models import TrainingRun, TrainingSample
from app.db.session import create_engine_for_url, create_session_factory
from app.knowledge.schemas import EvidenceInput, KnowledgeStatus
from app.training.browser import _looks_real
from app.training.etsy import ImageEvidence
from app.training.repository import TrainingRepository
from app.training.schemas import ActiveToken, CandidateSet, ReviewSet, TrainingActivationResult, VisualAnalysis
from app.training.service import VisionTrainingService


KINDS = (
    "title_structure",
    "tag_taxonomy",
    "occasion_vocabulary",
    "buyer_instruction_style",
    "category_mapping",
)


def test_browser_content_check_accepts_product_jsonld_but_rejects_challenges() -> None:
    listing = "https://www.etsy.com/listing/123"

    assert _looks_real(listing, '<script type="application/ld+json">{"@type": "Product"}</script>')
    assert not _looks_real(listing, "Verify you are human before continuing")


def make_workbook(path: Path, shops: list[str]) -> str:
    workbook = Workbook()
    sheet = workbook.active
    for row, shop in enumerate(shops, 1):
        sheet.cell(row, 1, shop)
    workbook.save(path)
    return hashlib.sha256(path.read_bytes()).hexdigest()


def listing_html(listing_id: str, image_url: str = "https://i.etsystatic.com/123/main.jpg") -> str:
    return f"""
      <html><head><title>Navy Costume - Etsy</title>
      <script type="application/ld+json">
      {{"@type":"Product","name":"Navy Costume {listing_id}","description":"Long sleeve stage costume", "image":["{image_url}"], "category":"Costumes"}}
      </script></head></html>
    """


class FakeBrowser:
    def __init__(self, pages: dict[str, str | Exception]) -> None:
        self.pages = pages
        self.calls: list[str] = []

    def fetch(self, url: str) -> str:
        self.calls.append(url)
        value = self.pages[url]
        if isinstance(value, Exception):
            raise value
        return value


class FakeModel:
    def __init__(self, *, image_usable: bool = True) -> None:
        self.image_usable = image_usable
        self.visual_calls: list[tuple[Path, dict]] = []
        self.candidate_calls = []
        self.review_calls = []

    async def extract_visual_facts(self, image: Path, listing_text: dict) -> VisualAnalysis:
        self.visual_calls.append((image, listing_text))
        return VisualAnalysis.model_validate(
            {
                "schema_version": 1,
                "visible_facts": {
                    "product_family": ["costume"],
                    "colors": ["navy"],
                    "silhouette": ["fitted"],
                    "garment_structure": ["long sleeves"],
                    "decorations": ["gold appliqué"],
                    "visible_components": ["dress"],
                    "visual_style": ["stagewear"],
                },
                "uncertain_observations": [],
                "forbidden_inferences": [],
                "image_usable": self.image_usable,
            }
        )

    async def generate_candidates(self, merged, evidence_ref) -> CandidateSet:
        self.candidate_calls.append((merged, evidence_ref))
        return CandidateSet.model_validate(
            {
                "schema_version": 1,
                "candidates": [
                    {
                        "kind": kind,
                        "abstract": f"Reusable evidence-bound strategy for {kind} output",
                        "confidence": 0.5,
                    }
                    for kind in KINDS
                ],
            }
        )

    async def review_candidates(self, candidates, active_rules, merged) -> ReviewSet:
        self.review_calls.append((candidates, active_rules, merged))
        return ReviewSet.model_validate(
            {
                "schema_version": 1,
                "reviews": [
                    {
                        "kind": kind,
                        "decision": "approve",
                        "reason_code": "net_improvement",
                        "reason": "The strategy is reusable and evidence bounded.",
                        "risk_flags": [],
                        "confidence": 0.91,
                    }
                    for kind in KINDS
                ],
            }
        )


class FakeKnowledge:
    def __init__(self) -> None:
        self.applied = []

    def training_review_context(self, kinds):
        tokens = {kind: ActiveToken(active_rule_public_id=None, pattern_revision=None) for kind in kinds}
        rules = {kind: {"abstract": "", **tokens[kind].model_dump()} for kind in kinds}
        return tokens, rules

    def apply_reviewed_training_batch(self, **kwargs):
        assert isinstance(kwargs["evidence"], EvidenceInput)
        self.applied.append(kwargs)
        return [
            TrainingActivationResult(
                candidate_id=index,
                candidate_public_id=f"kc-{'a' * 31}{index}",
                kind=candidate.kind,
                status=KnowledgeStatus.ACTIVE,
                review_public_id=f"00000000-0000-4000-8000-{index:012d}",
                activated_rule_version=f"knowledge-{index}-v1",
            )
            for index, candidate in enumerate(kwargs["candidates"].candidates, 1)
        ]


@pytest.fixture
def runtime(tmp_path: Path):
    engine = create_engine_for_url(f"sqlite:///{tmp_path / 'training.db'}")
    init_db(engine)
    factory = create_session_factory(engine)
    repository = TrainingRepository(factory)
    try:
        yield tmp_path, factory, repository
    finally:
        engine.dispose()


def downloader(tmp_path: Path, *, digest: str = "d" * 64):
    calls: list[str] = []

    def download(url, destination_root, _client):
        calls.append(url)
        image = Path(destination_root) / f"{digest}.jpg"
        image.parent.mkdir(parents=True, exist_ok=True)
        image.write_bytes(b"normalized image")
        return ImageEvidence(
            source_url=url,
            path=image,
            sha256=digest,
            width=100,
            height=120,
            media_type="image/jpeg",
        )

    return download, calls


def seed_completed(repository: TrainingRepository, *, listing_id: str, image_hash: str) -> None:
    run = repository.create_run(
        source_workbook_hash="f" * 64,
        source_workbook_name="prior.xlsx",
        requested_limit=1,
    )
    sample = repository.claim_sample(
        run_id=run.id,
        shop_url="https://www.etsy.com/shop/Prior",
        listing_id=listing_id,
        canonical_url=f"https://www.etsy.com/listing/{listing_id}",
    )
    repository.transition_sample(sample.id, "fetching")
    repository.transition_sample(
        sample.id,
        "image_ready",
        listing_snapshot_hash="e" * 64,
        main_image_hash=image_hash,
        main_image_path="training-evidence/prior.jpg",
    )
    repository.transition_sample(sample.id, "facts_ready", visual_facts={}, merged_facts={}, conflicts=[])
    repository.transition_sample(sample.id, "completed")
    repository.complete_run(run.id)


def test_pipeline_selects_first_untrained_listing_and_persists_full_flow(runtime) -> None:
    tmp_path, factory, repository = runtime
    workbook = tmp_path / "shops.xlsx"
    original_hash = make_workbook(workbook, ["https://www.etsy.com/shop/StageWear"])
    seed_completed(repository, listing_id="100", image_hash="a" * 64)
    shop = "https://www.etsy.com/shop/StageWear"
    listing = "https://www.etsy.com/listing/200"
    browser = FakeBrowser(
        {
            shop: '<a href="/listing/100/old">old</a><a href="/listing/200/new">new</a><a href="/listing/201/other">other</a>',
            listing: listing_html("200"),
        }
    )
    model = FakeModel()
    knowledge = FakeKnowledge()
    download, download_calls = downloader(tmp_path)
    sleeps: list[float] = []
    service = VisionTrainingService(
        repository=repository,
        knowledge=knowledge,
        model=model,
        browser=browser,
        image_client=object(),
        evidence_root=tmp_path / "evidence",
        image_downloader=download,
        sleeper=sleeps.append,
        clock=lambda: datetime(2026, 8, 17, tzinfo=UTC),
    )

    summary = asyncio.run(service.run(workbook, limit=1, delay=20))

    assert summary.status == "completed"
    assert summary.counts == {"completed": 1}
    assert browser.calls == [shop, listing]
    assert sleeps == [20, 20]
    assert download_calls == ["https://i.etsystatic.com/123/main.jpg"]
    assert model.visual_calls[0][0].name == f"{'d' * 64}.jpg"
    assert len(model.candidate_calls) == len(model.review_calls) == len(knowledge.applied) == 1
    assert hashlib.sha256(workbook.read_bytes()).hexdigest() == original_hash
    with factory() as session:
        sample = session.scalar(select(TrainingSample).where(TrainingSample.listing_id == "200"))
        assert sample is not None
        assert sample.status == "completed"
        assert sample.main_image_hash == "d" * 64
        assert sample.visual_facts["image_usable"] is True
        assert sample.merged_facts["facts"]["product_family"][0]["source"] == "text"


def test_bad_shop_does_not_stop_next_shop(runtime) -> None:
    tmp_path, _factory, repository = runtime
    workbook = tmp_path / "shops.xlsx"
    first = "https://www.etsy.com/shop/Blocked"
    second = "https://www.etsy.com/shop/Working"
    listing = "https://www.etsy.com/listing/300"
    make_workbook(workbook, [first, second])
    browser = FakeBrowser(
        {
            first: RuntimeError("blocked"),
            second: '<a href="/listing/300/good">good</a>',
            listing: listing_html("300"),
        }
    )
    download, _calls = downloader(tmp_path)
    service = VisionTrainingService(
        repository=repository,
        knowledge=FakeKnowledge(),
        model=FakeModel(),
        browser=browser,
        image_client=object(),
        evidence_root=tmp_path / "evidence",
        image_downloader=download,
        sleeper=lambda _delay: None,
        clock=lambda: datetime(2026, 8, 17, tzinfo=UTC),
    )

    summary = asyncio.run(service.run(workbook, limit=1, delay=15))

    assert summary.counts == {"completed": 1}
    assert browser.calls == [first, second, listing]


def test_duplicate_image_skips_model_and_unusable_image_fails_before_candidates(runtime) -> None:
    tmp_path, factory, repository = runtime
    duplicate_hash = "d" * 64
    seed_completed(repository, listing_id="999", image_hash=duplicate_hash)
    workbook = tmp_path / "shops.xlsx"
    shop = "https://www.etsy.com/shop/StageWear"
    listing = "https://www.etsy.com/listing/400"
    make_workbook(workbook, [shop])
    browser = FakeBrowser({shop: '<a href="/listing/400/new">new</a>', listing: listing_html("400")})
    model = FakeModel()
    download, _calls = downloader(tmp_path, digest=duplicate_hash)
    service = VisionTrainingService(
        repository=repository,
        knowledge=FakeKnowledge(),
        model=model,
        browser=browser,
        image_client=object(),
        evidence_root=tmp_path / "evidence",
        image_downloader=download,
        sleeper=lambda _delay: None,
        clock=lambda: datetime(2026, 8, 17, tzinfo=UTC),
    )
    duplicate_summary = asyncio.run(service.run(workbook, limit=1, delay=20))
    assert duplicate_summary.counts == {"skipped": 1}
    assert model.visual_calls == []

    other_workbook = tmp_path / "other.xlsx"
    other_shop = "https://www.etsy.com/shop/Other"
    other_listing = "https://www.etsy.com/listing/500"
    make_workbook(other_workbook, [other_shop])
    unusable_browser = FakeBrowser(
        {other_shop: '<a href="/listing/500/new">new</a>', other_listing: listing_html("500")}
    )
    unusable_model = FakeModel(image_usable=False)
    other_download, _calls = downloader(tmp_path, digest="e" * 64)
    unusable = VisionTrainingService(
        repository=repository,
        knowledge=FakeKnowledge(),
        model=unusable_model,
        browser=unusable_browser,
        image_client=object(),
        evidence_root=tmp_path / "evidence",
        image_downloader=other_download,
        sleeper=lambda _delay: None,
        clock=lambda: datetime(2026, 8, 17, tzinfo=UTC),
    )
    unusable_summary = asyncio.run(unusable.run(other_workbook, limit=1, delay=20))
    assert unusable_summary.counts == {"failed": 1}
    assert unusable_model.candidate_calls == []
    with factory() as session:
        failed = session.scalar(select(TrainingSample).where(TrainingSample.listing_id == "500"))
        assert failed is not None and failed.error_code == "image_unusable"


@pytest.mark.parametrize("delay", [14.9, 25.1])
def test_delay_outside_safe_range_is_rejected_before_state_change(runtime, delay) -> None:
    tmp_path, factory, repository = runtime
    workbook = tmp_path / "shops.xlsx"
    make_workbook(workbook, ["https://www.etsy.com/shop/StageWear"])
    service = VisionTrainingService(
        repository=repository,
        knowledge=FakeKnowledge(),
        model=FakeModel(),
        browser=FakeBrowser({}),
        image_client=object(),
        evidence_root=tmp_path / "evidence",
        image_downloader=lambda *_args: None,
        sleeper=lambda _delay: None,
        clock=lambda: datetime(2026, 8, 17, tzinfo=UTC),
    )

    with pytest.raises(ValueError, match="15 and 25"):
        asyncio.run(service.run(workbook, limit=1, delay=delay))
    with factory() as session:
        assert list(session.scalars(select(TrainingRun))) == []
