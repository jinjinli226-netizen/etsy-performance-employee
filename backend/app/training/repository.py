from __future__ import annotations

import re
import threading
from collections import Counter
from pathlib import Path
from typing import Any
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from app.db.models import TrainingRun, TrainingSample, utc_now


class TrainingStateConflict(RuntimeError):
    pass


TERMINAL_SAMPLE_STATUSES = frozenset({"completed", "skipped", "failed"})
_TRANSITIONS = {
    "claimed": {"fetching", "skipped", "failed"},
    "fetching": {"image_ready", "skipped", "failed"},
    "image_ready": {"facts_ready", "skipped", "failed"},
    "facts_ready": {"candidates_ready", "reviewing", "completed", "skipped", "failed"},
    "candidates_ready": {"reviewing", "failed"},
    "reviewing": {"activating", "completed", "failed"},
    "activating": {"completed", "failed"},
}
_UPDATE_FIELDS = frozenset(
    {
        "source_timestamp",
        "listing_snapshot_hash",
        "main_image_hash",
        "main_image_path",
        "visual_facts",
        "merged_facts",
        "conflicts",
        "error_code",
    }
)
_SAFE_ERROR = re.compile(r"^[a-z][a-z0-9_]{0,62}$")


class TrainingRepository:
    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self.session_factory = session_factory
        self._lock = threading.RLock()

    def _begin(self) -> Session:
        session = self.session_factory()
        if session.bind is not None and session.bind.dialect.name == "sqlite":
            session.connection().exec_driver_sql("BEGIN IMMEDIATE")
        return session

    def create_run(
        self,
        *,
        source_workbook_hash: str,
        source_workbook_name: str,
        requested_limit: int | None,
    ) -> TrainingRun:
        if not re.fullmatch(r"[0-9a-f]{64}", source_workbook_hash):
            raise ValueError("invalid source workbook hash")
        filename = Path(source_workbook_name).name.strip()
        if not filename or len(filename) > 255 or (requested_limit is not None and requested_limit <= 0):
            raise ValueError("invalid training run input")
        with self._lock:
            session = self._begin()
            try:
                run = TrainingRun(
                    public_id=str(uuid4()),
                    source_workbook_hash=source_workbook_hash,
                    source_workbook_name=filename,
                    requested_limit=requested_limit,
                    status="running",
                    counts={},
                )
                session.add(run)
                session.commit()
                return run
            except Exception:
                session.rollback()
                raise
            finally:
                session.close()

    def claim_sample(
        self,
        *,
        run_id: int,
        shop_url: str,
        listing_id: str,
        canonical_url: str,
    ) -> TrainingSample:
        if not re.fullmatch(r"[0-9]+", listing_id):
            raise ValueError("invalid listing id")
        with self._lock:
            session = self._begin()
            try:
                sample = TrainingSample(
                    public_id=str(uuid4()),
                    training_run_id=run_id,
                    shop_url=shop_url,
                    listing_id=listing_id,
                    canonical_url=canonical_url,
                    schema_version=1,
                    status="claimed",
                )
                session.add(sample)
                session.commit()
                return sample
            except IntegrityError as exc:
                session.rollback()
                raise TrainingStateConflict("listing is already claimed for this run") from exc
            except Exception:
                session.rollback()
                raise
            finally:
                session.close()

    def transition_sample(self, sample_id: int, status: str, **updates: Any) -> TrainingSample:
        unknown = set(updates) - _UPDATE_FIELDS
        if unknown:
            raise ValueError(f"unknown training sample fields: {sorted(unknown)}")
        error_code = updates.get("error_code")
        if error_code is not None and (not isinstance(error_code, str) or not _SAFE_ERROR.fullmatch(error_code)):
            raise ValueError("invalid training error code")
        with self._lock:
            session = self._begin()
            try:
                sample = session.get(TrainingSample, sample_id)
                if sample is None:
                    raise LookupError("training sample not found")
                if sample.status in TERMINAL_SAMPLE_STATUSES or status not in _TRANSITIONS.get(sample.status, set()):
                    raise TrainingStateConflict(f"invalid sample transition {sample.status} -> {status}")
                for key, value in updates.items():
                    setattr(sample, key, value)
                sample.status = status
                session.commit()
                return sample
            except Exception:
                session.rollback()
                raise
            finally:
                session.close()

    def complete_run(self, run_id: int) -> TrainingRun:
        with self._lock:
            session = self._begin()
            try:
                run = session.get(TrainingRun, run_id)
                if run is None:
                    raise LookupError("training run not found")
                statuses = list(session.scalars(select(TrainingSample.status).where(TrainingSample.training_run_id == run_id)))
                if any(status not in TERMINAL_SAMPLE_STATUSES for status in statuses):
                    raise TrainingStateConflict("training run still has active samples")
                run.counts = dict(sorted(Counter(statuses).items()))
                run.status = "completed" if any(status == "completed" for status in statuses) else "failed"
                run.completed_at = utc_now()
                session.commit()
                return run
            except Exception:
                session.rollback()
                raise
            finally:
                session.close()

    def resumable_samples(self, run_id: int) -> list[TrainingSample]:
        with self.session_factory() as session:
            return list(session.scalars(
                select(TrainingSample)
                .where(
                    TrainingSample.training_run_id == run_id,
                    TrainingSample.status.not_in(TERMINAL_SAMPLE_STATUSES),
                )
                .order_by(TrainingSample.id)
            ))

    def successful_listing_ids(self) -> set[str]:
        with self.session_factory() as session:
            return set(session.scalars(
                select(TrainingSample.listing_id).where(TrainingSample.status == "completed")
            ))

    def successful_image_hashes(self) -> set[str]:
        with self.session_factory() as session:
            return set(session.scalars(
                select(TrainingSample.main_image_hash).where(
                    TrainingSample.status == "completed",
                    TrainingSample.main_image_hash.is_not(None),
                )
            ))
