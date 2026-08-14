from __future__ import annotations

from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from app.core.config import Settings
from app.db.models import Conversation, ExcelJob, Message
from app.employee.adapter import EmployeeUnavailableError
from app.excel_jobs.schemas import JobStatus
from app.main import create_app


class FakeEmployee:
    """A HermesAdapter stand-in that records availability probes and model calls."""

    def __init__(self, *, available: bool = True) -> None:
        self.available = available
        self.available_calls = 0
        self.send_calls = 0

    def check_available(self) -> None:
        self.available_calls += 1
        if not self.available:
            raise EmployeeUnavailableError("The employee service is unavailable.")

    async def send(self, *args, **kwargs):  # pragma: no cover - must never be called
        self.send_calls += 1
        raise AssertionError("status probing must not call the employee model")


@pytest.fixture
def api(tmp_path):
    fake = FakeEmployee()
    settings = Settings(
        data_dir=tmp_path / "data",
        database_url=f"sqlite:///{tmp_path / 'status.db'}",
    )
    app = create_app(settings=settings, employee=fake)
    with TestClient(app) as client:
        yield client, fake


def test_status_online_when_employee_available_and_no_tasks(api) -> None:
    client, fake = api

    response = client.get("/api/employee/status")

    assert response.status_code == 200
    assert response.json() == {"status": "online"}
    assert fake.available_calls >= 1
    assert fake.send_calls == 0


def test_status_offline_when_employee_unavailable(api) -> None:
    client, fake = api
    fake.available = False

    response = client.get("/api/employee/status")

    assert response.status_code == 200
    assert response.json() == {"status": "offline"}
    assert fake.available_calls >= 1
    assert fake.send_calls == 0


def test_status_offline_never_leaks_internal_error_body(api) -> None:
    client, fake = api
    secret = "secret-provider-path-C:/private/key"

    def explode() -> None:
        raise RuntimeError(secret)

    fake.check_available = explode

    response = client.get("/api/employee/status")

    assert response.status_code == 200
    assert response.json() == {"status": "offline"}
    assert secret not in response.text
    assert fake.send_calls == 0


def test_status_busy_when_chat_operation_is_running(api) -> None:
    client, fake = api
    with client.app.state.session_factory() as session:
        conversation = Conversation(title="Busy chat")
        session.add(conversation)
        session.flush()
        session.add(
            Message(
                conversation_id=conversation.id,
                role="user",
                content="in flight",
                operation_id="op-running",
                operation_status="running",
            )
        )
        session.commit()

    response = client.get("/api/employee/status")

    assert response.status_code == 200
    assert response.json() == {"status": "busy"}
    assert fake.send_calls == 0


@pytest.mark.parametrize("status", [JobStatus.QUEUED, JobStatus.RUNNING])
def test_status_busy_when_excel_job_is_active(api, status) -> None:
    client, fake = api
    with client.app.state.session_factory() as session:
        session.add(
            ExcelJob(
                public_id=str(uuid4()),
                source_filename="products.xlsx",
                source_sha256="a" * 64,
                source_size_bytes=123,
                status=status,
            )
        )
        session.commit()

    response = client.get("/api/employee/status")

    assert response.status_code == 200
    assert response.json() == {"status": "busy"}
    assert fake.send_calls == 0


def test_status_online_when_excel_job_is_terminal(api) -> None:
    client, fake = api
    with client.app.state.session_factory() as session:
        session.add(
            ExcelJob(
                public_id=str(uuid4()),
                source_filename="products.xlsx",
                source_sha256="b" * 64,
                source_size_bytes=123,
                status=JobStatus.COMPLETED,
            )
        )
        session.commit()

    response = client.get("/api/employee/status")

    assert response.status_code == 200
    assert response.json() == {"status": "online"}
    assert fake.send_calls == 0
