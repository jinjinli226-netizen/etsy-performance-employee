from __future__ import annotations

from pathlib import Path

import pytest
from sqlalchemy import inspect, text
from sqlalchemy.exc import IntegrityError

from app.core.config import Settings
from app.db.init_db import init_db
from app.db.models import TrainingReview, TrainingRun, TrainingSample
from app.db.session import create_engine_for_url, create_session_factory
from app.training.repository import TrainingRepository, TrainingStateConflict


@pytest.fixture
def database(tmp_path: Path):
    engine = create_engine_for_url(f"sqlite:///{tmp_path / 'training.db'}")
    init_db(engine)
    factory = create_session_factory(engine)
    try:
        yield engine, factory
    finally:
        engine.dispose()


def test_repository_persists_transitions_and_success_deduplication(database) -> None:
    _engine, factory = database
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
        main_image_path="training-evidence/cc/example.jpg",
    )
    repository.transition_sample(
        sample.id,
        "facts_ready",
        visual_facts={"schema_version": 1, "visible_facts": {"colors": ["navy"]}},
        merged_facts={"facts": {"colors": [{"value": "navy", "source": "visual"}]}},
        conflicts=[],
    )
    repository.transition_sample(sample.id, "completed")
    completed = repository.complete_run(run.id)

    assert completed.status == "completed"
    assert completed.counts == {"completed": 1}
    assert repository.successful_listing_ids() == {"123456"}
    assert repository.successful_image_hashes() == {"c" * 64}
    assert repository.resumable_samples(run.id) == []

    with pytest.raises(TrainingStateConflict):
        repository.transition_sample(sample.id, "failed", error_code="late_failure")


def test_repository_rejects_duplicate_listing_claim_in_one_run(database) -> None:
    _engine, factory = database
    repository = TrainingRepository(factory)
    run = repository.create_run(
        source_workbook_hash="a" * 64,
        source_workbook_name="shops.xlsx",
        requested_limit=None,
    )
    kwargs = {
        "run_id": run.id,
        "shop_url": "https://www.etsy.com/shop/StageWear",
        "listing_id": "123456",
        "canonical_url": "https://www.etsy.com/listing/123456",
    }
    repository.claim_sample(**kwargs)

    with pytest.raises(TrainingStateConflict):
        repository.claim_sample(**kwargs)


def test_training_schema_has_lineage_indexes_and_database_guards(database) -> None:
    engine, factory = database
    tables = set(inspect(engine).get_table_names())
    assert {"training_runs", "training_samples", "training_reviews"} <= tables
    sample_indexes = {item["name"] for item in inspect(engine).get_indexes("training_samples")}
    review_indexes = {item["name"] for item in inspect(engine).get_indexes("training_reviews")}
    assert {"ix_training_samples_listing_id", "ix_training_samples_main_image_hash"} <= sample_indexes
    assert "ix_training_reviews_candidate_id" in review_indexes

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
    with engine.begin() as connection:
        with pytest.raises(IntegrityError):
            connection.execute(text("UPDATE training_runs SET public_id='bad' WHERE id=:id"), {"id": run.id})
        with pytest.raises(IntegrityError):
            connection.execute(
                text("UPDATE training_samples SET main_image_hash='bad' WHERE id=:id"),
                {"id": sample.id},
            )


def test_models_round_trip_review_lineage(database) -> None:
    _engine, factory = database
    with factory() as session:
        run = TrainingRun(
            public_id="11111111-1111-4111-8111-111111111111",
            source_workbook_hash="a" * 64,
            source_workbook_name="shops.xlsx",
            requested_limit=1,
            status="running",
            counts={},
        )
        sample = TrainingSample(
            public_id="22222222-2222-4222-8222-222222222222",
            run=run,
            shop_url="https://www.etsy.com/shop/StageWear",
            listing_id="123456",
            canonical_url="https://www.etsy.com/listing/123456",
            schema_version=1,
            status="reviewing",
        )
        review = TrainingReview(
            public_id="33333333-3333-4333-8333-333333333333",
            sample=sample,
            kind="title_structure",
            reviewer_version="reviewer-v1",
            prompt_schema_version=1,
            decision="approve",
            reason_code="net_improvement",
            reason="Adds a reusable title strategy.",
            risk_flags=[],
            confidence=0.91,
        )
        session.add(review)
        session.commit()
        review_id = review.id

    with factory() as session:
        stored = session.get(TrainingReview, review_id)
        assert stored is not None
        assert stored.sample.run.source_workbook_name == "shops.xlsx"
        assert stored.sample.reviews == [stored]


def test_migration_14_recreates_training_tables_from_version_13(tmp_path: Path) -> None:
    engine = create_engine_for_url(f"sqlite:///{tmp_path / 'upgrade.db'}")
    try:
        init_db(engine)
        with engine.begin() as connection:
            connection.execute(text("DROP TABLE training_reviews"))
            connection.execute(text("DROP TABLE training_samples"))
            connection.execute(text("DROP TABLE training_runs"))
            connection.execute(text("DELETE FROM schema_migrations WHERE version=14"))
        init_db(engine)
        with engine.connect() as connection:
            assert connection.execute(text("SELECT max(version) FROM schema_migrations")).scalar_one() == 14
            tables = {row[0] for row in connection.execute(text("SELECT name FROM sqlite_master WHERE type='table'"))}
            assert {"training_runs", "training_samples", "training_reviews"} <= tables
    finally:
        engine.dispose()


def test_settings_create_training_evidence_directory(tmp_path: Path) -> None:
    settings = Settings(data_dir=tmp_path / "runtime")

    settings.ensure_runtime_dirs()

    assert (settings.data_dir / "training-evidence").is_dir()
