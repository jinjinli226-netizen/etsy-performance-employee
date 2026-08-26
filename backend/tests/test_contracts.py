from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.core.config import Settings
from app.excel_jobs.schemas import GeneratedListingFields, JobStatus
from app.knowledge.schemas import KnowledgeStatus


def test_settings_use_env_overrides_without_creating_runtime_directory(
    tmp_path, monkeypatch
) -> None:
    data_dir = tmp_path / "runtime"
    monkeypatch.setenv("ETSY_EMPLOYEE_DATA_DIR", str(data_dir))
    monkeypatch.setenv("ETSY_EMPLOYEE_DATABASE_URL", "sqlite:///custom.db")

    settings = Settings()

    assert settings.data_dir == data_dir
    assert settings.resolved_database_url == "sqlite:///custom.db"
    assert not data_dir.exists()

    settings.ensure_runtime_dirs()
    assert data_dir.is_dir()


def test_settings_default_database_is_inside_configured_data_directory(tmp_path) -> None:
    settings = Settings(data_dir=tmp_path / "data")

    assert settings.resolved_database_url == f"sqlite:///{(tmp_path / 'data/app.db').as_posix()}"


def test_excel_batch_timeout_allows_image_enriched_workbooks() -> None:
    settings = Settings()

    assert settings.excel_worker_timeout_seconds == 80 * 60


def test_excel_upload_default_allows_large_product_workbooks() -> None:
    settings = Settings()

    assert settings.max_excel_upload_bytes == 200 * 1024 * 1024


def test_job_status_values_are_stable() -> None:
    assert [status.value for status in JobStatus] == [
        "queued",
        "running",
        "needs_review",
        "completed",
        "failed",
        "cancelled",
    ]


def test_knowledge_status_values_are_stable() -> None:
    assert [status.value for status in KnowledgeStatus] == [
        "proposed",
        "testing",
        "active",
        "rejected",
        "rolled_back",
    ]


def test_generated_listing_fields_has_exact_business_contract() -> None:
    listing = GeneratedListingFields(
        head_titles="Velvet vampire cape",
        tags=["vampire cape", "gothic costume"],
        specification="Adult; velvet; black",
        category="Costumes",
        instructions_for_buyers="Send measurements after checkout.",
        confidence=0.8,
        rule_version="rules-1",
    )

    assert listing.model_dump() == {
        "head_titles": "Velvet vampire cape",
        "tags": ["vampire cape", "gothic costume"],
        "specification": "Adult; velvet; black",
        "category": "Costumes",
        "instructions_for_buyers": "Send measurements after checkout.",
        "confidence": 0.8,
        "fact_warnings": [],
        "quality_warnings": [],
        "rule_version": "rules-1",
    }

    other = GeneratedListingFields(
        head_titles="Other",
        tags=[],
        specification="Other",
        category="Other",
        instructions_for_buyers="Other",
        confidence=0,
        rule_version="rules-1",
    )
    listing.fact_warnings.append("Check material")
    assert other.fact_warnings == []


def test_generated_listing_fields_rejects_unexpected_employee_output() -> None:
    with pytest.raises(ValidationError):
        GeneratedListingFields(
            head_titles="Title",
            tags=[],
            specification="Spec",
            category="Category",
            instructions_for_buyers="Instructions",
            confidence=0.5,
            rule_version="rules-1",
            invented_claim="Handmade in Italy",
        )


@pytest.mark.parametrize("confidence", [-0.01, 1.01])
def test_generated_listing_fields_rejects_confidence_outside_unit_interval(
    confidence: float,
) -> None:
    with pytest.raises(ValidationError):
        GeneratedListingFields(
            head_titles="Title",
            tags=[],
            specification="Spec",
            category="Category",
            instructions_for_buyers="Instructions",
            confidence=confidence,
            rule_version="rules-1",
        )


@pytest.mark.parametrize("confidence", [0.0, 1.0])
def test_generated_listing_fields_accepts_confidence_boundaries(confidence: float) -> None:
    listing = GeneratedListingFields(
        head_titles="Title",
        tags=[],
        specification="Spec",
        category="Category",
        instructions_for_buyers="Instructions",
        confidence=confidence,
        rule_version="rules-1",
    )
    assert listing.confidence == confidence
