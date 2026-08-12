from __future__ import annotations

import asyncio
import hashlib
import json
import shutil
import stat
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from openpyxl import load_workbook

from app.core.config import Settings
from app.excel_jobs.runner import RunnerRequest, WorkerResult, build_employee_command
from app.main import create_app
from app.db.init_db import init_db
from app.db.models import ExcelJob
from app.db.session import create_engine_for_url, create_session_factory
from app.excel_jobs.schemas import JobStatus


FIXTURE = Path(__file__).parent / "fixtures" / "performance-listing-template.xlsx"


class FakeExcelRunner:
    def __init__(self, *, fail: bool = False, malformed: bool = False, block: bool = False) -> None:
        self.fail = fail
        self.malformed = malformed
        self.block = block
        self.calls: list[RunnerRequest] = []
        self.cancelled: set[str] = set()

    async def run(self, request: RunnerRequest, emit) -> WorkerResult:
        self.calls.append(request)
        await emit({"event": "started"})
        if self.malformed:
            await emit({"not_event": "bad"})
        if self.block:
            while request.public_id not in self.cancelled:
                await asyncio.sleep(0.01)
            raise asyncio.CancelledError
        output = request.operation_dir / "generated.xlsx"
        shutil.copyfile(request.source_path, output)
        if self.fail:
            raise RuntimeError("private stderr secret")
        return WorkerResult(output_path=output, output_sha256=sha256(output))

    async def cancel(self, public_id: str) -> None:
        self.cancelled.add(public_id)

    async def shutdown(self) -> None:
        return None


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


@pytest.fixture
def api(tmp_path):
    runner = FakeExcelRunner()
    settings = Settings(
        data_dir=tmp_path / "data",
        database_url=f"sqlite:///{tmp_path / 'api.db'}",
        max_excel_upload_bytes=5 * 1024 * 1024,
    )
    app = create_app(settings=settings, excel_runner=runner)
    with TestClient(app) as client:
        yield client, runner, settings, app


def upload(client: TestClient, name: str = "products.xlsx"):
    return client.post(
        "/api/excel-jobs",
        files={
            "file": (
                name,
                FIXTURE.read_bytes(),
                "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            )
        },
    )


def wait_terminal(client: TestClient, public_id: str) -> dict:
    for _ in range(200):
        payload = client.get(f"/api/excel-jobs/{public_id}").json()
        if payload["status"] in {"completed", "failed", "cancelled"}:
            return payload
        import time

        time.sleep(0.01)
    raise AssertionError("job did not reach a terminal state")


def test_upload_runs_employee_job_persists_events_and_downloads_new_artifact(api) -> None:
    client, runner, settings, _ = api
    source_digest = sha256(FIXTURE)

    created = upload(client, "../../unsafe name.xlsx")
    assert created.status_code == 202
    job = created.json()
    assert job["status"] in {"queued", "running", "completed"}
    assert len(job["id"]) == 36
    assert "path" not in json.dumps(job).casefold()

    terminal = wait_terminal(client, job["id"])
    assert terminal["status"] == "completed"
    assert terminal["source_sha256"] == source_digest
    assert terminal["progress_percent"] == 100
    assert terminal["artifact"]["sha256"]

    listing = client.get("/api/excel-jobs", params={"limit": 10, "offset": 0})
    assert listing.status_code == 200
    assert listing.json()["total"] == 1
    assert listing.json()["items"][0]["id"] == job["id"]

    downloaded = client.get(f"/api/excel-jobs/{job['id']}/download")
    assert downloaded.status_code == 200
    assert downloaded.content != b""
    assert "attachment" in downloaded.headers["content-disposition"].casefold()
    assert ".." not in downloaded.headers["content-disposition"]

    call = runner.calls[0]
    assert call.source_path.parent.name == "source"
    assert call.source_path.name == "source.xlsx"
    assert sha256(call.source_path) == source_digest
    assert call.source_path.read_bytes() == FIXTURE.read_bytes()
    assert not call.source_path.stat().st_mode & stat.S_IWRITE
    assert call.source_path.is_relative_to(settings.data_dir / "excel-jobs")
    assert call.knowledge_path.is_relative_to(settings.data_dir)


@pytest.mark.parametrize(
    ("filename", "content", "expected"),
    [
        ("products.xlsm", b"PK bad", 415),
        ("products.xlsx", b"not an OOXML package", 422),
        ("products.xlsx.exe", b"PK bad", 415),
    ],
)
def test_upload_rejects_unsupported_or_disguised_files(api, filename, content, expected) -> None:
    client, _, _, _ = api
    response = client.post("/api/excel-jobs", files={"file": (filename, content)})
    assert response.status_code == expected
    assert client.get("/api/excel-jobs").json()["total"] == 0


def test_upload_streaming_hard_cap_returns_413_and_leaves_no_job(tmp_path) -> None:
    runner = FakeExcelRunner()
    settings = Settings(
        data_dir=tmp_path / "data",
        database_url=f"sqlite:///{tmp_path / 'api.db'}",
        max_excel_upload_bytes=64,
    )
    with TestClient(create_app(settings=settings, excel_runner=runner)) as client:
        response = upload(client)
        assert response.status_code == 413
        assert client.get("/api/excel-jobs").json()["total"] == 0
        assert not list((settings.data_dir / "excel-jobs").glob("*"))


def test_failed_worker_hides_internal_error_and_never_exposes_partial(tmp_path) -> None:
    runner = FakeExcelRunner(fail=True)
    settings = Settings(data_dir=tmp_path / "data", database_url=f"sqlite:///{tmp_path / 'api.db'}")
    app = create_app(settings=settings, excel_runner=runner)
    with TestClient(app) as client:
        job = upload(client).json()
        terminal = wait_terminal(client, job["id"])
        assert terminal["status"] == "failed"
        assert terminal["error"]["code"] == "worker_failed"
        assert "secret" not in json.dumps(terminal).casefold()
        assert client.get(f"/api/excel-jobs/{job['id']}/download").status_code == 409
        workspace = settings.data_dir / "excel-jobs" / job["id"]
        assert not list((workspace / "artifacts").glob("*.xlsx"))
        assert (workspace / "source" / "source.xlsx").exists()


def test_malformed_progress_event_fails_job_safely(tmp_path) -> None:
    runner = FakeExcelRunner(malformed=True)
    settings = Settings(data_dir=tmp_path / "data", database_url=f"sqlite:///{tmp_path / 'api.db'}")
    with TestClient(create_app(settings=settings, excel_runner=runner)) as client:
        job = upload(client).json()
        terminal = wait_terminal(client, job["id"])
        assert terminal["status"] == "failed"
        assert terminal["error"]["code"] == "invalid_worker_event"


def test_sse_replays_persisted_events_after_last_event_id(api) -> None:
    client, _, _, _ = api
    job = upload(client).json()
    wait_terminal(client, job["id"])

    with client.stream("GET", f"/api/excel-jobs/{job['id']}/events") as response:
        assert response.status_code == 200
        lines = list(response.iter_lines())
    ids = [int(line[4:]) for line in lines if line.startswith("id: ")]
    assert ids == sorted(ids)
    assert len(ids) >= 3

    with client.stream(
        "GET",
        f"/api/excel-jobs/{job['id']}/events",
        headers={"Last-Event-ID": str(ids[-2])},
    ) as response:
        replay = list(response.iter_lines())
    assert [int(line[4:]) for line in replay if line.startswith("id: ")] == [ids[-1]]


def test_running_cancel_is_idempotent_and_preserves_source(tmp_path) -> None:
    runner = FakeExcelRunner(block=True)
    settings = Settings(data_dir=tmp_path / "data", database_url=f"sqlite:///{tmp_path / 'api.db'}")
    with TestClient(create_app(settings=settings, excel_runner=runner)) as client:
        job = upload(client).json()
        for _ in range(100):
            detail = client.get(f"/api/excel-jobs/{job['id']}").json()
            if detail["status"] == "running":
                break
            import time

            time.sleep(0.01)
        first = client.post(f"/api/excel-jobs/{job['id']}/cancel")
        second = client.post(f"/api/excel-jobs/{job['id']}/cancel")
        assert first.status_code == second.status_code == 200
        assert wait_terminal(client, job["id"])["status"] == "cancelled"
        assert sha256(runner.calls[0].source_path) == sha256(FIXTURE)


def test_real_runner_command_invokes_only_employee_entry_and_trusted_arguments(tmp_path) -> None:
    repository = Path(__file__).parents[2]
    request = RunnerRequest(
        public_id="00000000-0000-4000-8000-000000000000",
        source_path=tmp_path / "source.xlsx",
        operation_dir=tmp_path / "operation",
        rules_path=tmp_path / "rules.json",
        knowledge_path=tmp_path / "knowledge.json",
        knowledge_export_id="kx-" + "a" * 32,
        knowledge_payload_sha256="b" * 64,
        knowledge_file_sha256="c" * 64,
    )
    command = build_employee_command(request, repository_root=repository, python_executable="python")
    assert command[:2] == ["python", str(repository / "employee/skills/etsy-performance-listing/scripts/run_task.py")]
    assert command[2:4] == [str(request.source_path), str(request.operation_dir)]
    assert "inspect_workbook.py" not in " ".join(command)
    assert "--knowledge" in command
    assert "--expected-knowledge-export-id" in command
    assert "--expected-knowledge-payload-sha256" in command
    assert "--expected-knowledge-file-sha256" in command


def test_artifact_contract_rejects_duplicate_fixed_header(tmp_path) -> None:
    runner = FakeExcelRunner()

    class DuplicateRunner(FakeExcelRunner):
        async def run(self, request, emit):
            result = await super().run(request, emit)
            workbook = load_workbook(result.output_path)
            worksheet = workbook.active
            worksheet.cell(1, worksheet.max_column + 1, "head titles")
            workbook.save(result.output_path)
            return WorkerResult(result.output_path, sha256(result.output_path))

    settings = Settings(data_dir=tmp_path / "data", database_url=f"sqlite:///{tmp_path / 'api.db'}")
    with TestClient(create_app(settings=settings, excel_runner=DuplicateRunner())) as client:
        job = upload(client).json()
        terminal = wait_terminal(client, job["id"])
        assert terminal["status"] == "failed"
        assert terminal["error"]["code"] == "invalid_artifact"


def test_unknown_job_and_invalid_pagination_are_rejected(api) -> None:
    client, _, _, _ = api
    assert client.get("/api/excel-jobs/not-a-uuid").status_code == 422
    assert client.get("/api/excel-jobs/00000000-0000-4000-8000-000000000000").status_code == 404
    assert client.get("/api/excel-jobs", params={"limit": 101}).status_code == 422


def test_worker_artifact_path_escape_is_rejected_without_deleting_external_file(tmp_path) -> None:
    outside = tmp_path / "outside.xlsx"
    shutil.copyfile(FIXTURE, outside)

    class EscapeRunner(FakeExcelRunner):
        async def run(self, request, emit):
            self.calls.append(request)
            await emit({"event": "started"})
            return WorkerResult(outside, sha256(outside))

    settings = Settings(data_dir=tmp_path / "data", database_url=f"sqlite:///{tmp_path / 'api.db'}")
    with TestClient(create_app(settings=settings, excel_runner=EscapeRunner())) as client:
        job = upload(client).json()
        terminal = wait_terminal(client, job["id"])
        assert terminal["status"] == "failed"
        assert terminal["error"]["code"] == "unsafe_path"
        assert outside.exists()
        assert outside.read_bytes() == FIXTURE.read_bytes()


def test_worker_source_mutation_is_detected_and_source_is_never_downloadable(tmp_path) -> None:
    class MutatingRunner(FakeExcelRunner):
        async def run(self, request, emit):
            self.calls.append(request)
            await emit({"event": "started"})
            request.source_path.chmod(stat.S_IWRITE | stat.S_IREAD)
            request.source_path.write_bytes(b"mutated")
            raise RuntimeError("failed after mutation")

    settings = Settings(data_dir=tmp_path / "data", database_url=f"sqlite:///{tmp_path / 'api.db'}")
    with TestClient(create_app(settings=settings, excel_runner=MutatingRunner())) as client:
        job = upload(client).json()
        terminal = wait_terminal(client, job["id"])
        assert terminal["status"] == "failed"
        assert terminal["error"]["code"] == "source_modified"
        assert client.get(f"/api/excel-jobs/{job['id']}/download").status_code == 409


def test_worker_source_deletion_is_detected_without_task_crash(tmp_path) -> None:
    class DeletingRunner(FakeExcelRunner):
        async def run(self, request, emit):
            self.calls.append(request)
            await emit({"event": "started"})
            request.source_path.chmod(stat.S_IWRITE | stat.S_IREAD)
            request.source_path.unlink()
            raise RuntimeError("failed after deleting source")

    settings = Settings(data_dir=tmp_path / "data", database_url=f"sqlite:///{tmp_path / 'api.db'}")
    with TestClient(create_app(settings=settings, excel_runner=DeletingRunner())) as client:
        job = upload(client).json()
        terminal = wait_terminal(client, job["id"])
        assert terminal["status"] == "failed"
        assert terminal["error"]["code"] == "source_modified"


def test_worker_hardlink_to_source_is_not_a_new_artifact(tmp_path) -> None:
    class HardlinkRunner(FakeExcelRunner):
        async def run(self, request, emit):
            self.calls.append(request)
            await emit({"event": "started"})
            output = request.operation_dir / "generated.xlsx"
            request.source_path.chmod(stat.S_IWRITE | stat.S_IREAD)
            try:
                output.hardlink_to(request.source_path)
            except OSError:
                pytest.skip("hard links are unavailable on this filesystem")
            return WorkerResult(output, sha256(output))

    settings = Settings(data_dir=tmp_path / "data", database_url=f"sqlite:///{tmp_path / 'api.db'}")
    with TestClient(create_app(settings=settings, excel_runner=HardlinkRunner())) as client:
        job = upload(client).json()
        terminal = wait_terminal(client, job["id"])
        assert terminal["status"] == "failed"
        assert terminal["error"]["code"] == "invalid_artifact"


def test_startup_marks_persisted_queued_and_running_jobs_failed(tmp_path) -> None:
    database = tmp_path / "restart.db"
    engine = create_engine_for_url(f"sqlite:///{database}")
    init_db(engine)
    factory = create_session_factory(engine)
    with factory() as session:
        session.add_all(
            [
                ExcelJob(public_id="00000000-0000-4000-8000-000000000001", source_filename="queued.xlsx", source_sha256="a" * 64, source_size_bytes=1, status=JobStatus.QUEUED),
                ExcelJob(public_id="00000000-0000-4000-8000-000000000002", source_filename="running.xlsx", source_sha256="b" * 64, source_size_bytes=1, status=JobStatus.RUNNING),
            ]
        )
        session.commit()
    engine.dispose()
    settings = Settings(data_dir=tmp_path / "data", database_url=f"sqlite:///{database}")
    with TestClient(create_app(settings=settings, excel_runner=FakeExcelRunner())) as client:
        for public_id in ("00000000-0000-4000-8000-000000000001", "00000000-0000-4000-8000-000000000002"):
            detail = client.get(f"/api/excel-jobs/{public_id}")
            assert detail.status_code == 200
            assert detail.json()["status"] == "failed"
            assert detail.json()["error"]["code"] == "interrupted"
