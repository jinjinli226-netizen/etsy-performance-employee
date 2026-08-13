from __future__ import annotations

import asyncio
import json
import re
from dataclasses import dataclass
from pathlib import Path
from uuid import UUID, uuid4

from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload, sessionmaker

from app.core.config import Settings
from app.db.models import Artifact, ExcelJob, JobEvent
from app.excel_jobs.runner import (
    ExcelRunner,
    RunnerRequest,
    WorkerProtocolError,
    WorkerResult,
    WorkerUnavailableError,
)
from app.excel_jobs.schemas import JobStatus
from app.excel_jobs.storage import (
    StorageError,
    StoredSource,
    create_operation_dir,
    ensure_default_rules,
    ensure_empty_knowledge_export,
    file_sha256,
    publish_artifact,
    remove_operation_dir,
    safe_path,
    validate_artifact,
)
from app.knowledge.service import KnowledgeCapacityError, KnowledgeService
from app.knowledge.service import KnowledgeValidationError


TERMINAL = {JobStatus.COMPLETED, JobStatus.FAILED, JobStatus.CANCELLED}
_SAFE_CODE = re.compile(r"^[a-z][a-z0-9_]{0,62}$")
_WARNING_CONTROL = re.compile(r"[\x00-\x1f\x7f]")
_WARNING_URL = re.compile(r"(?:https?://|www\.)", re.IGNORECASE)
MAX_JOB_WARNINGS = 40
MAX_JOB_WARNING_CHARS = 500
MAX_JOB_WARNING_TOTAL_CHARS = 5_000


class JobNotFoundError(RuntimeError):
    pass


class JobConflictError(RuntimeError):
    pass


@dataclass(frozen=True)
class ArtifactView:
    id: int
    kind: str
    filename: str
    sha256: str
    size_bytes: int
    created_at: object


@dataclass(frozen=True)
class JobView:
    id: str
    source_filename: str
    source_sha256: str
    source_size_bytes: int
    status: JobStatus
    progress_percent: int
    warnings: list[str]
    error: dict[str, str] | None
    created_at: object
    updated_at: object
    artifact: ArtifactView | None


class ExcelJobService:
    def __init__(self, factory: sessionmaker[Session], runner: ExcelRunner, settings: Settings, knowledge_service: KnowledgeService | None = None) -> None:
        self.factory = factory
        self.runner = runner
        self.settings = settings
        self.knowledge_service = knowledge_service
        self.root = (settings.data_dir / "excel-jobs").resolve()
        self._tasks: dict[str, asyncio.Task] = {}
        self._lock = asyncio.Lock()
        self._wakeups: dict[str, set[asyncio.Event]] = {}

    def reconcile_interrupted_jobs(self) -> None:
        with self.factory() as session:
            jobs = session.scalars(
                select(ExcelJob).where(ExcelJob.status.in_([JobStatus.QUEUED, JobStatus.RUNNING]))
            ).all()
            for job in jobs:
                job.status = JobStatus.FAILED
                job.error = "Job interrupted by application restart."
                job.error_code = "interrupted"
                job.error_message = "The job was interrupted by an application restart."
                job.events.append(
                    JobEvent(
                        event_type="failed",
                        payload={"status": "failed", "error": {"code": "interrupted", "message": job.error_message}},
                    )
                )
            session.commit()
        for job in jobs:
            if not job.public_id:
                continue
            operations = self.root / job.public_id / "operations"
            if not operations.is_dir() or operations.is_symlink():
                continue
            for operation in operations.iterdir():
                if operation.is_dir():
                    try:
                        remove_operation_dir(operation, self.root / job.public_id)
                    except (OSError, StorageError):
                        pass

    def create_job(self, stored: StoredSource, source_filename: str) -> JobView:
        if self.knowledge_service is not None:
            self.knowledge_service.require_capacity_ready()
        public_id = stored.workspace.name
        with self.factory() as session:
            job = ExcelJob(
                public_id=public_id,
                source_filename=_display_filename(source_filename),
                source_sha256=stored.sha256,
                source_size_bytes=stored.size_bytes,
                status=JobStatus.QUEUED,
                progress_percent=0,
            )
            job.events.append(JobEvent(event_type="queued", payload={"status": "queued", "progress_percent": 0}))
            session.add(job)
            session.commit()
            session.refresh(job)
            return self._view(job)

    async def start_job(self, public_id: str) -> None:
        async with self._lock:
            if public_id in self._tasks and not self._tasks[public_id].done():
                raise JobConflictError("The job is already running.")
            task = asyncio.create_task(self._execute(public_id), name=f"excel-job-{public_id}")
            self._tasks[public_id] = task
            task.add_done_callback(lambda _: self._tasks.pop(public_id, None))

    def list_jobs(self, limit: int, offset: int) -> tuple[list[JobView], int]:
        with self.factory() as session:
            query = select(ExcelJob).where(ExcelJob.public_id.is_not(None))
            total = session.scalar(select(func.count()).select_from(query.subquery())) or 0
            jobs = session.scalars(
                query.options(selectinload(ExcelJob.artifacts), selectinload(ExcelJob.events)).order_by(ExcelJob.id.desc()).limit(limit).offset(offset)
            ).all()
            return [self._view(job) for job in jobs], total

    def get_job(self, public_id: str) -> JobView:
        with self.factory() as session:
            job = self._get(session, public_id, artifacts=True, events=True)
            return self._view(job)

    def events_after(self, public_id: str, after_id: int) -> tuple[list[JobEvent], bool]:
        with self.factory() as session:
            job = self._get(session, public_id)
            events = session.scalars(
                select(JobEvent)
                .where(JobEvent.excel_job_id == job.id, JobEvent.id > after_id)
                .order_by(JobEvent.id)
                .limit(1000)
            ).all()
            return list(events), job.status in TERMINAL

    def subscribe(self, public_id: str) -> asyncio.Event:
        wakeup = asyncio.Event()
        self._wakeups.setdefault(public_id, set()).add(wakeup)
        return wakeup

    def unsubscribe(self, public_id: str, wakeup: asyncio.Event) -> None:
        subscribers = self._wakeups.get(public_id)
        if subscribers is None:
            return
        subscribers.discard(wakeup)
        if not subscribers:
            self._wakeups.pop(public_id, None)

    async def cancel(self, public_id: str) -> JobView:
        with self.factory() as session:
            job = self._get(session, public_id)
            if job.status in TERMINAL:
                return self._view(job)
            job.status = JobStatus.CANCELLED
            job.error = None
            job.error_code = None
            job.error_message = None
            job.events.append(JobEvent(event_type="cancelled", payload={"status": "cancelled"}))
            session.commit()
        await self.runner.cancel(public_id)
        async with self._lock:
            task = self._tasks.get(public_id)
            if task is not None and not task.done():
                task.cancel()
        self._signal(public_id)
        return self.get_job(public_id)

    def download(self, public_id: str) -> tuple[Path, str]:
        with self.factory() as session:
            job = self._get(session, public_id, artifacts=True)
            if job.status is not JobStatus.COMPLETED or len(job.artifacts) != 1:
                raise JobConflictError("No completed artifact is available for this job.")
            artifact = job.artifacts[0]
            workspace = self.root / public_id
            try:
                path = safe_path(Path(artifact.path), workspace, must_exist=True)
                if artifact.sha256 is None or file_sha256(path) != artifact.sha256:
                    raise JobConflictError("The completed artifact failed its integrity check.")
            except (FileNotFoundError, OSError, StorageError) as exc:
                raise JobConflictError("The completed artifact is missing or unsafe.") from exc
            return path, artifact.filename or "etsy-listings.xlsx"

    async def shutdown(self) -> None:
        await self.runner.shutdown()
        tasks = list(self._tasks.values())
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    async def _execute(self, public_id: str) -> None:
        workspace = self.root / public_id
        operation: Path | None = None
        try:
            if self.knowledge_service is not None:
                self.knowledge_service.require_capacity_ready()
            with self.factory() as session:
                job = self._get(session, public_id)
                if job.status is not JobStatus.QUEUED:
                    return
                job.status = JobStatus.RUNNING
                job.progress_percent = 1
                job.events.append(JobEvent(event_type="running", payload={"status": "running", "progress_percent": 1}))
                source_sha = job.source_sha256
                session.commit()
            self._signal(public_id)
            if source_sha is None:
                raise StorageError("invalid_source", "The job source digest is missing.")
            workspace = workspace.resolve(strict=True)
            source = safe_path(workspace / "source" / "source.xlsx", workspace, must_exist=True)
            operation = create_operation_dir(workspace, uuid4().hex)
            rules = ensure_default_rules(workspace)
            trust = (
                self.knowledge_service.export_active_knowledge(operation / "active-knowledge.json")
                if self.knowledge_service is not None
                else ensure_empty_knowledge_export(self.settings.data_dir)
            )
            guard_trust = (
                self.knowledge_service.export_evidence_guard(operation / "evidence-guard.json")
                if self.knowledge_service is not None
                else ensure_empty_knowledge_export(self.settings.data_dir)
            )
            request = RunnerRequest(
                public_id=public_id,
                source_path=source,
                operation_dir=operation,
                rules_path=rules,
                knowledge_path=trust.path,
                knowledge_export_id=trust.export_id,
                knowledge_payload_sha256=trust.payload_sha256,
                knowledge_file_sha256=trust.file_sha256,
                guard_path=guard_trust.path,
                guard_export_id=guard_trust.export_id,
                guard_payload_sha256=guard_trust.payload_sha256,
                guard_file_sha256=guard_trust.file_sha256,
            )

            async def emit(event: dict) -> None:
                await self._persist_worker_event(public_id, event)

            result = await self.runner.run(request, emit)
            if not isinstance(result, WorkerResult):
                raise WorkerProtocolError("The worker returned an invalid result.")
            digest, size = await asyncio.to_thread(
                validate_artifact,
                result.output_path,
                operation_dir=operation,
                source_path=source,
                source_sha256=source_sha,
            )
            if result.output_sha256 != digest:
                raise StorageError("invalid_artifact", "The artifact digest did not match the worker report.")
            if self.knowledge_service is not None:
                await asyncio.to_thread(self.knowledge_service.validate_generated_workbook, result.output_path)
            published = await asyncio.to_thread(
                publish_artifact,
                result.output_path,
                workspace=workspace,
                public_id=public_id,
                expected_sha256=digest,
            )
            with self.factory() as session:
                job = self._get(session, public_id)
                if job.status is JobStatus.CANCELLED:
                    published.unlink(missing_ok=True)
                    return
                if job.status is not JobStatus.RUNNING:
                    raise JobConflictError("The job state changed while processing.")
                job.artifacts.append(
                    Artifact(
                        kind="generated_workbook",
                        path=str(published),
                        filename=published.name,
                        sha256=digest,
                        size_bytes=size,
                    )
                )
                job.status = JobStatus.COMPLETED
                job.progress_percent = 100
                job.events.append(JobEvent(event_type="completed", payload={"status": "completed", "progress_percent": 100}))
                session.commit()
            self._signal(public_id)
        except asyncio.CancelledError:
            self._mark_cancelled_if_needed(public_id)
        except WorkerUnavailableError:
            self._fail(public_id, "worker_unavailable", "The Excel employee is unavailable.")
        except WorkerProtocolError:
            self._fail(public_id, "invalid_worker_event", "The Excel employee returned an invalid progress event.")
        except StorageError as exc:
            code = exc.code if _SAFE_CODE.fullmatch(exc.code) else "invalid_artifact"
            self._fail(public_id, code, _safe_storage_message(code))
        except KnowledgeCapacityError:
            self._fail(public_id, "knowledge_capacity_exceeded", "Knowledge evidence capacity must be resolved before generation.")
        except KnowledgeValidationError:
            self._fail(public_id, "originality_failed", "The generated workbook was too similar to protected evidence.")
        except Exception:
            try:
                source_changed = (
                    'source' in locals()
                    and 'source_sha' in locals()
                    and await asyncio.to_thread(file_sha256, source) != source_sha
                )
            except OSError:
                source_changed = True
            if source_changed:
                self._fail(public_id, "source_modified", "The source workbook changed during processing.")
            else:
                self._fail(public_id, "worker_failed", "The Excel employee could not complete this job.")
        finally:
            if operation is not None and operation.exists():
                try:
                    remove_operation_dir(operation, workspace)
                except (OSError, StorageError):
                    self._record_cleanup_failure(public_id)

    async def _persist_worker_event(self, public_id: str, event: dict) -> None:
        if not isinstance(event, dict) or not isinstance(event.get("event"), str):
            raise WorkerProtocolError("Malformed worker event.")
        if len(json.dumps(event, ensure_ascii=False).encode("utf-8")) > self.settings.max_worker_event_bytes:
            raise WorkerProtocolError("Worker event exceeded its limit.")
        kind = event["event"]
        if kind not in {"started", "row_started", "row_completed", "row_failed", "completed", "failed"}:
            raise WorkerProtocolError("Unknown worker event.")
        payload: dict = {"status": "running"}
        if kind.startswith("row_"):
            row_id = event.get("row_id")
            row_number = event.get("row_number")
            if not isinstance(row_id, str) or len(row_id) > 128:
                raise WorkerProtocolError("Invalid row event.")
            payload["row_id"] = row_id
            if row_number is not None:
                if isinstance(row_number, bool) or not isinstance(row_number, int) or not 1 <= row_number <= 20_000:
                    raise WorkerProtocolError("Invalid row event.")
                payload["row_number"] = row_number
        if kind == "row_completed":
            payload["warnings"] = _safe_worker_warnings(event.get("warnings", []))
        await asyncio.to_thread(self._persist_worker_event_sync, public_id, kind, payload)
        self._signal(public_id)

    def _persist_worker_event_sync(self, public_id: str, kind: str, payload: dict) -> None:
        with self.factory() as session:
            job = self._get(session, public_id)
            if job.status is not JobStatus.RUNNING:
                raise JobConflictError("Job is no longer running.")
            if kind == "row_completed":
                job.progress_percent = min(99, max(job.progress_percent, job.progress_percent + 1))
            payload["progress_percent"] = job.progress_percent
            job.events.append(JobEvent(event_type=f"worker_{kind}", payload=payload))
            session.commit()

    def _record_cleanup_failure(self, public_id: str) -> None:
        message = "Temporary operation cleanup failed."
        with self.factory() as session:
            try:
                job = self._get(session, public_id)
            except JobNotFoundError:
                return
            if job.status is JobStatus.COMPLETED:
                for artifact in list(job.artifacts):
                    try:
                        Path(artifact.path).unlink(missing_ok=True)
                    except OSError:
                        pass
                    session.delete(artifact)
            job.status = JobStatus.FAILED
            job.error = message
            job.error_code = "cleanup_failed"
            job.error_message = message
            job.events.append(JobEvent(event_type="cleanup_failed", payload={"status": "failed", "error": {"code": "cleanup_failed", "message": message}}))
            session.commit()
        self._signal(public_id)

    def _fail(self, public_id: str, code: str, message: str) -> None:
        with self.factory() as session:
            try:
                job = self._get(session, public_id)
            except JobNotFoundError:
                return
            if job.status in TERMINAL:
                return
            job.status = JobStatus.FAILED
            job.error = message
            job.error_code = code
            job.error_message = message
            job.events.append(JobEvent(event_type="failed", payload={"status": "failed", "error": {"code": code, "message": message}}))
            session.commit()
        self._signal(public_id)

    def _mark_cancelled_if_needed(self, public_id: str) -> None:
        with self.factory() as session:
            try:
                job = self._get(session, public_id)
            except JobNotFoundError:
                return
            if job.status not in TERMINAL:
                job.status = JobStatus.CANCELLED
                job.events.append(JobEvent(event_type="cancelled", payload={"status": "cancelled"}))
                session.commit()
        self._signal(public_id)

    def _get(self, session: Session, public_id: str, *, artifacts: bool = False, events: bool = False) -> ExcelJob:
        query = select(ExcelJob).where(ExcelJob.public_id == public_id)
        options = []
        if artifacts:
            options.append(selectinload(ExcelJob.artifacts))
        if events:
            options.append(selectinload(ExcelJob.events))
        if options:
            query = query.options(*options)
        job = session.scalar(query)
        if job is None:
            raise JobNotFoundError(public_id)
        return job

    def _view(self, job: ExcelJob) -> JobView:
        artifacts = list(job.artifacts) if "artifacts" in job.__dict__ else []
        events = list(job.events) if "events" in job.__dict__ else []
        warnings = list(dict.fromkeys(
            warning
            for event in events
            for warning in _safe_worker_warnings(event.payload.get("warnings", []), reject=False)
        ))[:MAX_JOB_WARNINGS]
        artifact_model = artifacts[0] if len(artifacts) == 1 and job.status is JobStatus.COMPLETED else None
        artifact = None
        if artifact_model is not None and artifact_model.sha256 and artifact_model.size_bytes is not None:
            artifact = ArtifactView(
                id=artifact_model.id,
                kind=artifact_model.kind,
                filename=artifact_model.filename or "etsy-listings.xlsx",
                sha256=artifact_model.sha256,
                size_bytes=artifact_model.size_bytes,
                created_at=artifact_model.created_at,
            )
        return JobView(
            id=job.public_id or "",
            source_filename=job.source_filename,
            source_sha256=job.source_sha256 or "",
            source_size_bytes=job.source_size_bytes or 0,
            status=job.status,
            progress_percent=job.progress_percent,
            warnings=warnings,
            error={"code": job.error_code, "message": job.error_message} if job.error_code and job.error_message else None,
            created_at=job.created_at,
            updated_at=job.updated_at,
            artifact=artifact,
        )

    def _signal(self, public_id: str) -> None:
        for event in tuple(self._wakeups.get(public_id, ())):
            event.set()


def _display_filename(filename: str) -> str:
    name = Path(filename or "workbook.xlsx").name.strip()
    if not name.casefold().endswith(".xlsx") or not name:
        return "workbook.xlsx"
    safe = re.sub(r"[^\w .()\-\u4e00-\u9fff]", "_", name, flags=re.UNICODE)
    return safe[:255] or "workbook.xlsx"


def _safe_worker_warnings(value: object, *, reject: bool = True) -> list[str]:
    invalid = not isinstance(value, list) or len(value) > MAX_JOB_WARNINGS
    warnings: list[str] = []
    total = 0
    if not invalid:
        for item in value:
            if (
                not isinstance(item, str)
                or not item.strip()
                or len(item) > MAX_JOB_WARNING_CHARS
                or _WARNING_CONTROL.search(item)
                or _WARNING_URL.search(item)
            ):
                invalid = True
                break
            cleaned = item.strip()
            total += len(cleaned)
            if total > MAX_JOB_WARNING_TOTAL_CHARS:
                invalid = True
                break
            warnings.append(cleaned)
    if invalid:
        if reject:
            raise WorkerProtocolError("Invalid worker warning payload.")
        return []
    return warnings


def _safe_storage_message(code: str) -> str:
    return {
        "source_modified": "The source workbook changed during processing.",
        "invalid_artifact": "The generated workbook failed final validation.",
        "unsafe_workbook": "The generated workbook failed package safety validation.",
        "invalid_workbook": "The generated workbook could not be opened safely.",
        "unsafe_path": "The employee returned an unsafe artifact path.",
    }.get(code, "The generated workbook failed final validation.")
