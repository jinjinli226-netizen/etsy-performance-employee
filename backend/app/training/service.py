from __future__ import annotations

import hashlib
import inspect
import json
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Awaitable, Callable, Protocol

from app.knowledge.schemas import EvidenceInput
from app.training.browser import BrowserFetchError
from app.training.etsy import (
    HttpClient,
    ImageEvidence,
    ImageEvidenceError,
    download_main_image,
    extract_listing_snapshot,
    extract_listing_urls,
    extract_shop_urls,
    select_main_image_url,
)
from app.training.facts import merge_facts
from app.training.model import TrainingModel, TrainingModelError
from app.training.repository import TERMINAL_SAMPLE_STATUSES, TrainingRepository


KNOWLEDGE_KINDS = (
    "title_structure",
    "tag_taxonomy",
    "occasion_vocabulary",
    "buyer_instruction_style",
    "category_mapping",
)


class Browser(Protocol):
    def fetch(self, url: str) -> str: ...


class KnowledgeTrainingService(Protocol):
    def training_review_context(self, kinds): ...
    def apply_reviewed_training_batch(self, **kwargs): ...


@dataclass(frozen=True)
class TrainingRunSummary:
    public_id: str
    status: str
    counts: dict[str, int]


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _snapshot_hash(snapshot) -> str:
    payload = {
        "canonical_url": snapshot.canonical_url,
        "title": snapshot.title,
        "description": snapshot.description,
        "tags": snapshot.tags,
        "text_facts": snapshot.text_facts,
    }
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


class VisionTrainingService:
    def __init__(
        self,
        *,
        repository: TrainingRepository,
        knowledge: KnowledgeTrainingService,
        model: TrainingModel,
        browser: Browser,
        image_client: HttpClient,
        evidence_root: Path,
        image_downloader: Callable[[str, Path, HttpClient], ImageEvidence] = download_main_image,
        sleeper: Callable[[float], Awaitable[None] | None],
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self.repository = repository
        self.knowledge = knowledge
        self.model = model
        self.browser = browser
        self.image_client = image_client
        self.evidence_root = evidence_root.resolve()
        self.image_downloader = image_downloader
        self.sleeper = sleeper
        self.clock = clock

    async def _sleep(self, seconds: float) -> None:
        result = self.sleeper(seconds)
        if inspect.isawaitable(result):
            await result

    async def _fetch(self, url: str, delay: float) -> str:
        try:
            return self.browser.fetch(url)
        finally:
            await self._sleep(delay)

    async def run(
        self,
        workbook_path: str | Path,
        *,
        limit: int | None = 1,
        delay: float = 20,
        shop_substr: str | None = None,
    ) -> TrainingRunSummary:
        if not 15 <= delay <= 25:
            raise ValueError("delay must be between 15 and 25 seconds")
        if limit is not None and (isinstance(limit, bool) or limit <= 0):
            raise ValueError("limit must be a positive integer")
        source = Path(workbook_path).resolve(strict=True)
        if source.suffix.casefold() != ".xlsx" or not source.is_file():
            raise ValueError("training source must be an existing .xlsx workbook")
        source_hash = _sha256_file(source)
        shops = extract_shop_urls(source)
        if shop_substr:
            shops = [shop for shop in shops if shop_substr.casefold() in shop.casefold()]
        if not shops:
            raise ValueError("no Etsy shop URLs were found")

        run = self.repository.create_run(
            source_workbook_hash=source_hash,
            source_workbook_name=source.name,
            requested_limit=limit,
        )
        historical_listings = self.repository.successful_listing_ids()
        historical_images = self.repository.successful_image_hashes()
        claimed_this_run: set[str] = set()
        attempts = 0

        for shop in shops:
            if limit is not None and attempts >= limit:
                break
            try:
                shop_html = await self._fetch(shop, delay)
            except Exception:
                continue
            picked_url: str | None = None
            picked_id: str | None = None
            for listing_url in extract_listing_urls(shop_html):
                listing_id = listing_url.rsplit("/", 1)[1]
                if listing_id not in historical_listings and listing_id not in claimed_this_run:
                    picked_url, picked_id = listing_url, listing_id
                    break
            if picked_url is None or picked_id is None:
                continue
            claimed_this_run.add(picked_id)
            try:
                sample = self.repository.claim_sample(
                    run_id=run.id,
                    shop_url=shop,
                    listing_id=picked_id,
                    canonical_url=picked_url,
                )
            except Exception:
                continue
            attempts += 1
            terminal = False
            try:
                self.repository.transition_sample(sample.id, "fetching")
                listing_html = await self._fetch(picked_url, delay)
                fetched_at = self.clock()
                if fetched_at.tzinfo is None:
                    raise ValueError("training clock must return an aware time")
                snapshot = extract_listing_snapshot(listing_html, picked_url, fetched_at)
                image_url = select_main_image_url(listing_html, picked_url)
                image = self.image_downloader(image_url, self.evidence_root, self.image_client)
                snapshot_digest = _snapshot_hash(snapshot)
                try:
                    evidence_path = str(image.path.resolve().relative_to(self.evidence_root.parent))
                except ValueError:
                    evidence_path = image.path.name
                if image.sha256 in historical_images:
                    self.repository.transition_sample(
                        sample.id,
                        "skipped",
                        listing_snapshot_hash=snapshot_digest,
                        main_image_hash=image.sha256,
                        main_image_path=evidence_path,
                        error_code="duplicate_image",
                    )
                    terminal = True
                    continue
                self.repository.transition_sample(
                    sample.id,
                    "image_ready",
                    source_timestamp=snapshot.source_timestamp,
                    listing_snapshot_hash=snapshot_digest,
                    main_image_hash=image.sha256,
                    main_image_path=evidence_path,
                )
                visual = await self.model.extract_visual_facts(
                    image.path,
                    {
                        "title": snapshot.title,
                        "description": snapshot.description,
                        "tags": snapshot.tags,
                        "text_facts": snapshot.text_facts,
                    },
                )
                merged = merge_facts(snapshot.text_facts, visual)
                self.repository.transition_sample(
                    sample.id,
                    "facts_ready",
                    visual_facts=visual.model_dump(mode="json"),
                    merged_facts=merged.model_dump(mode="json"),
                    conflicts=[item.model_dump(mode="json") for item in merged.conflicts],
                )
                if not visual.image_usable:
                    self.repository.transition_sample(sample.id, "failed", error_code="image_unusable")
                    terminal = True
                    continue
                candidate_set = await self.model.generate_candidates(
                    merged,
                    {"listing_id": snapshot.listing_id, "evidence_hash": snapshot_digest},
                )
                self.repository.transition_sample(sample.id, "candidates_ready")
                tokens, active_rules = self.knowledge.training_review_context(KNOWLEDGE_KINDS)
                self.repository.transition_sample(sample.id, "reviewing")
                review_set = await self.model.review_candidates(candidate_set, active_rules, merged)
                self.repository.transition_sample(sample.id, "activating")
                self.knowledge.apply_reviewed_training_batch(
                    sample_id=sample.id,
                    evidence=EvidenceInput(
                        url=snapshot.canonical_url,
                        title=snapshot.title,
                        snapshot=snapshot.compact_text(),
                        tags=snapshot.tags,
                        source_timestamp=snapshot.source_timestamp,
                    ),
                    candidates=candidate_set,
                    reviews=review_set,
                    reviewed_active_tokens=tokens,
                    reviewer_version=getattr(self.model, "reviewer_version", "independent-review-v1"),
                    trace_id=sample.public_id,
                )
                self.repository.transition_sample(sample.id, "completed")
                terminal = True
                historical_images.add(image.sha256)
                historical_listings.add(picked_id)
            except Exception as exc:
                if not terminal:
                    code = self._error_code(exc)
                    try:
                        self.repository.transition_sample(sample.id, "failed", error_code=code)
                    except Exception:
                        pass

        completed = self.repository.complete_run(run.id)
        if _sha256_file(source) != source_hash:
            raise RuntimeError("training source workbook changed during the run")
        return TrainingRunSummary(
            public_id=completed.public_id,
            status=completed.status,
            counts=completed.counts,
        )

    @staticmethod
    def _error_code(error: Exception) -> str:
        if isinstance(error, (TrainingModelError, BrowserFetchError)):
            return error.code
        if isinstance(error, ImageEvidenceError):
            return "image_evidence_failed"
        if isinstance(error, ValueError):
            return "invalid_listing_data"
        return "training_sample_failed"
