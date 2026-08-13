from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.core.config import Settings
from app.db.models import AuditEvent, CompetitorEvidence, Conversation, KnowledgeCandidate, Message
from app.employee.adapter import (
    EmployeeReply,
    EmployeeUnavailableError,
    HermesAdapter,
    HermesCancelledError,
)
from app.employee.events import MAX_ENVELOPE_BYTES, parse_final_envelopes
from app.main import create_app


class FakeHermes(HermesAdapter):
    def __init__(self) -> None:
        self.calls: list[dict] = []
        self.release = asyncio.Event()
        self.block = False
        self.available = True

    def check_available(self) -> None:
        if not self.available:
            raise EmployeeUnavailableError("The employee service is unavailable.")

    async def send(self, prompt, session_id, image_path, source):
        self.calls.append(
            {
                "prompt": prompt,
                "session_id": session_id,
                "image_path": image_path,
                "source": source,
            }
        )
        if self.block:
            await self.release.wait()
        return EmployeeReply(text=f"Answered: {prompt}", session_id=session_id or "session-new")


@pytest.fixture
def api(tmp_path):
    fake = FakeHermes()
    settings = Settings(
        data_dir=tmp_path / "data",
        database_url=f"sqlite:///{tmp_path / 'api.db'}",
    )
    app = create_app(settings=settings, employee=fake)
    with TestClient(app) as client:
        yield client, fake, settings


def create_conversation(client: TestClient, title: str = "Listing help") -> int:
    response = client.post("/api/conversations", json={"title": title})
    assert response.status_code == 201
    return response.json()["id"]


def wait_for_final(client: TestClient, operation_id: str) -> list[dict]:
    with client.stream("GET", f"/api/events/{operation_id}") as response:
        assert response.status_code == 200
        payloads = []
        for line in response.iter_lines():
            if line.startswith("data: "):
                payloads.append(json.loads(line[6:]))
        return payloads


def test_create_list_and_get_empty_messages(api) -> None:
    client, _, _ = api
    first = create_conversation(client, "First")
    create_conversation(client, "Second")

    listing = client.get("/api/conversations", params={"limit": 1, "offset": 0})
    messages = client.get(f"/api/conversations/{first}/messages")

    assert listing.status_code == 200
    assert listing.json()["total"] == 2
    assert len(listing.json()["items"]) == 1
    assert messages.status_code == 200
    assert messages.json() == []
    assert client.get("/api/conversations/999/messages").status_code == 404


def test_create_conversation_rejects_whitespace_only_title(api) -> None:
    client, _, _ = api
    response = client.post("/api/conversations", json={"title": "   \t"})

    assert response.status_code == 422
    assert client.get("/api/conversations").json()["total"] == 0


def test_send_persists_messages_emits_final_and_resumes_session(api) -> None:
    client, fake, _ = api
    conversation_id = create_conversation(client)

    first = client.post(
        f"/api/conversations/{conversation_id}/messages", json={"content": "Review A"}
    )
    assert first.status_code == 202
    first_events = wait_for_final(client, first.json()["operation_id"])
    assert first_events[-1]["type"] == "final"
    assert first_events[-1]["status"] == "completed"

    second = client.post(
        f"/api/conversations/{conversation_id}/messages", json={"content": "Review B"}
    )
    wait_for_final(client, second.json()["operation_id"])

    stored = client.get(f"/api/conversations/{conversation_id}/messages").json()
    assert [(message["role"], message["content"]) for message in stored] == [
        ("user", "Review A"),
        ("assistant", "Answered: Review A"),
        ("user", "Review B"),
        ("assistant", "Answered: Review B"),
    ]
    assert fake.calls[0]["session_id"] is None
    assert fake.calls[1]["session_id"] == "session-new"
    assert fake.calls[0]["source"] == "app"


def test_attachment_upload_is_scoped_and_only_image_is_passed_as_image(api) -> None:
    client, fake, settings = api
    first = create_conversation(client, "One")
    second = create_conversation(client, "Two")

    image = client.post(
        "/api/attachments",
        data={"conversation_id": str(first)},
        files={"file": ("../dress.png", b"image", "image/png")},
    )
    spreadsheet = client.post(
        "/api/attachments",
        data={"conversation_id": str(first)},
        files={
            "file": (
                "listings.xlsx",
                b"PK fake workbook",
                "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            )
        },
    )
    assert image.status_code == 201
    assert spreadsheet.status_code == 201
    assert "path" not in image.json()
    assert ".." not in image.json()["filename"]

    isolated = client.post(
        f"/api/conversations/{second}/messages",
        json={"content": "wrong", "attachment_ids": [image.json()["id"]]},
    )
    assert isolated.status_code == 422

    sent = client.post(
        f"/api/conversations/{first}/messages",
        json={
            "content": "Review files",
            "attachment_ids": [image.json()["id"], spreadsheet.json()["id"]],
        },
    )
    wait_for_final(client, sent.json()["operation_id"])
    call = fake.calls[-1]
    assert Path(call["image_path"]).is_relative_to(settings.data_dir)
    assert "BEGIN UNTRUSTED ATTACHMENTS" in call["prompt"]
    assert "listings.xlsx" in call["prompt"]
    assert ".xlsx" in call["prompt"]


def test_send_rejects_more_than_one_image_before_persisting_or_running(api) -> None:
    client, fake, _ = api
    conversation_id = create_conversation(client)
    image_ids = []
    for name in ("front.png", "back.png"):
        uploaded = client.post(
            "/api/attachments",
            data={"conversation_id": str(conversation_id)},
            files={"file": (name, b"image", "image/png")},
        )
        image_ids.append(uploaded.json()["id"])

    response = client.post(
        f"/api/conversations/{conversation_id}/messages",
        json={"content": "Review both", "attachment_ids": image_ids},
    )

    assert response.status_code == 422
    assert fake.calls == []
    assert client.get(f"/api/conversations/{conversation_id}/messages").json() == []


@pytest.mark.parametrize(
    ("filename", "size", "media_type"),
    [
        ("bad.exe", 1, "application/x-msdownload"),
        ("fake.png", 5 * 1024 * 1024 + 1, "image/png"),
    ],
)
def test_attachment_rejects_disallowed_type_or_oversize(api, filename, size, media_type) -> None:
    client, _, _ = api
    conversation_id = create_conversation(client)
    response = client.post(
        "/api/attachments",
        data={"conversation_id": str(conversation_id)},
        files={"file": (filename, b"x" * size, media_type)},
    )
    assert response.status_code == 422


def test_valid_knowledge_envelope_is_stripped_and_unknown_json_is_visible(api) -> None:
    client, fake, _ = api
    conversation_id = create_conversation(client)

    async def envelope_reply(prompt, session_id, image_path, source):
        return EmployeeReply(
            text=(
                'Visible answer\n'
                '{"event":"not_allowed","payload":{"text":"keep me"}}\n'
                '{"event":"knowledge_candidate","payload":'
                '{"kind":"tag_pattern","summary":"Prefer buyer terms",'
                '"confidence":0.8,"evidence_ids":["msg-1"]}}'
            ),
            session_id="envelope-session",
        )

    fake.send = envelope_reply
    sent = client.post(
        f"/api/conversations/{conversation_id}/messages", json={"content": "Learn this"}
    )
    wait_for_final(client, sent.json()["operation_id"])
    stored = client.get(f"/api/conversations/{conversation_id}/messages").json()

    assert stored[-1]["content"] == (
        'Visible answer\n{"event":"not_allowed","payload":{"text":"keep me"}}'
    )


def test_invalid_oversize_control_frame_is_never_visible_or_persisted_raw(api) -> None:
    client, fake, _ = api
    conversation_id = create_conversation(client)
    secret = "RAWSECRET" * 1200

    async def invalid_control(*args, **kwargs):
        return EmployeeReply(
            text='Visible answer\n{"event":"learning_batch","payload":{"evidence_items":[],"candidates":[],"extra":"' + secret + '"}}',
            session_id="invalid-control",
        )

    fake.send = invalid_control
    sent = client.post(f"/api/conversations/{conversation_id}/messages", json={"content": "normal"})
    assert wait_for_final(client, sent.json()["operation_id"])[-1]["status"] == "completed"
    stored = client.get(f"/api/conversations/{conversation_id}/messages").json()
    serialized = json.dumps(stored)
    assert stored[-1]["content"] == "Visible answer"
    assert secret not in serialized
    with client.app.state.session_factory() as session:
        audit = session.query(AuditEvent).filter_by(action="candidate_event_rejected").one()
        assert audit.details["new"] == "envelope_invalid"
        assert secret not in json.dumps(audit.details)


def test_valid_learning_control_frame_accepts_bounded_snapshot_and_is_not_visible() -> None:
    snapshot = "x" * 20_000
    raw = (
        'Visible answer\n{"event":"learning_batch","payload":{"evidence_items":[{'
        '"url":"https://www.etsy.com/listing/123/sample","title":"Safe title",'
        f'"snapshot":"{snapshot}","tags":[],"source_timestamp":"2026-08-10T12:00:00Z"}}],'
        '"candidates":[]}}'
    )
    assert len(raw.splitlines()[-1].encode("utf-8")) < MAX_ENVELOPE_BYTES
    parsed = parse_final_envelopes(raw)
    assert parsed.visible_text == "Visible answer"
    assert len(parsed.envelopes) == 1
    assert parsed.control_errors == []


def test_learning_batch_is_atomic_when_second_candidate_is_invalid(api) -> None:
    client, fake, _ = api
    conversation_id = create_conversation(client)

    async def invalid_second(*args, **kwargs):
        return EmployeeReply(text=(
            "Visible answer\n"
            '{"event":"learning_batch","payload":{"evidence_items":[{'
            '"url":"https://www.etsy.com/listing/123/sample","title":"Safe title",'
            '"snapshot":"Safe public snapshot for a performance costume.","tags":[],"source_timestamp":"2026-08-10T12:00:00Z"}],'
            '"candidates":[{"kind":"title_structure","summary":"Lead with occasion and garment type for search clarity.",'
            '"confidence":0.9,"evidence_urls":["https://www.etsy.com/listing/123/sample"]},'
            '{"kind":"tag_taxonomy","summary":"Safe public snapshot for a performance costume.",'
            '"confidence":0.9,"evidence_urls":["https://www.etsy.com/listing/123/sample"]}]}}'
        ), session_id="atomic-batch")

    fake.send = invalid_second
    sent = client.post(f"/api/conversations/{conversation_id}/messages", json={
        "content": "learn https://www.etsy.com/listing/123/sample", "learning_mode": True,
    })
    assert wait_for_final(client, sent.json()["operation_id"])[-1]["status"] == "completed"
    with client.app.state.session_factory() as session:
        assert session.query(CompetitorEvidence).count() == 0
        assert session.query(KnowledgeCandidate).count() == 0
        assert session.query(AuditEvent).filter_by(action="candidate_event_rejected").count() == 1


def test_explicit_learning_mode_ingests_whitelisted_evidence_then_bound_candidate(api) -> None:
    client, fake, _ = api
    conversation_id = create_conversation(client)
    timestamp = "2026-08-10T12:00:00Z"

    async def learning_reply(prompt, session_id, image_path, source):
        assert "LEARNING_MODE" in prompt
        return EmployeeReply(text=(
            "Learned safely.\n"
            '{"event":"learning_batch","payload":{"evidence_items":[{'
            '"url":"https://www.etsy.com/listing/123/sample","title":"Competitor stage cape",'
            '"snapshot":"Public stage cape snapshot with dramatic costume details.",'
            '"tags":["stage cape"],"source_timestamp":"2026-08-10T12:00:00Z"}],'
            '"candidates":[{"kind":"title_structure","summary":"Lead with occasion, garment type, silhouette, and intended audience.",'
            '"confidence":0.9,"evidence_urls":["https://www.etsy.com/listing/123/sample"]}]}}'
        ), session_id="learning-session")

    fake.send = learning_reply
    sent = client.post(f"/api/conversations/{conversation_id}/messages", json={
        "content": "学习这个 https://www.etsy.com/listing/123/sample",
        "learning_mode": True,
    })
    assert wait_for_final(client, sent.json()["operation_id"])[-1]["status"] == "completed"
    assert client.get(f"/api/conversations/{conversation_id}/messages").json()[-1]["content"] == "Learned safely."
    with client.app.state.session_factory() as session:
        assert session.query(CompetitorEvidence).count() == 1
        assert session.query(KnowledgeCandidate).count() == 1
        audit = json.dumps([row.details for row in session.query(AuditEvent).all()])
        assert "Competitor stage cape" not in audit and "etsy.com" not in audit


def test_learning_envelopes_are_ignored_without_server_learning_mode(api) -> None:
    client, fake, _ = api
    conversation_id = create_conversation(client)

    async def untrusted_reply(prompt, session_id, image_path, source):
        return EmployeeReply(text=(
            "Normal answer.\n"
            '{"event":"learning_batch","payload":{"evidence_items":[{'
            '"url":"https://www.etsy.com/listing/123/sample","title":"Raw title",'
            '"snapshot":"Raw snapshot should never be learned.","tags":[],"source_timestamp":"2026-08-10T12:00:00Z"}],'
            '"candidates":[{"kind":"title_structure","summary":"Lead with occasion and garment type for search clarity.",'
            '"confidence":0.9,"evidence_urls":["https://www.etsy.com/listing/123/sample"]}]}}'
        ), session_id="normal-session")

    fake.send = untrusted_reply
    sent = client.post(f"/api/conversations/{conversation_id}/messages", json={
        "content": "chat normally about this", "learning_mode": False,
    })
    assert wait_for_final(client, sent.json()["operation_id"])[-1]["status"] == "completed"
    with client.app.state.session_factory() as session:
        assert session.query(CompetitorEvidence).count() == 0
        assert session.query(KnowledgeCandidate).count() == 0
        assert session.query(AuditEvent).filter_by(action="learning_event_rejected").count() == 1


def test_learning_mode_requires_canonical_etsy_listing_url_before_employee_call(api) -> None:
    client, fake, _ = api
    conversation_id = create_conversation(client)
    response = client.post(f"/api/conversations/{conversation_id}/messages", json={
        "content": "learn https://evil.test/listing/123", "learning_mode": True,
    })
    assert response.status_code == 422
    assert fake.calls == []


def test_concurrent_send_rejected_and_failed_operation_can_retry(api) -> None:
    client, fake, _ = api
    conversation_id = create_conversation(client)
    fake.block = True

    first = client.post(
        f"/api/conversations/{conversation_id}/messages", json={"content": "slow"}
    )
    second = client.post(
        f"/api/conversations/{conversation_id}/messages", json={"content": "overlap"}
    )
    assert first.status_code == 202
    assert second.status_code == 409

    fake.release.set()
    assert wait_for_final(client, first.json()["operation_id"])[-1]["status"] == "completed"


def test_failed_operation_is_persisted_and_allows_retry(api) -> None:
    client, fake, _ = api
    conversation_id = create_conversation(client)

    async def fail(*args, **kwargs):
        raise RuntimeError("private provider failure")

    fake.send = fail
    failed = client.post(
        f"/api/conversations/{conversation_id}/messages", json={"content": "fail"}
    )
    events = wait_for_final(client, failed.json()["operation_id"])
    assert events[-1]["status"] == "failed"

    async def recover(prompt, session_id, image_path, source):
        return EmployeeReply(text="Recovered", session_id="retry-session")

    fake.send = recover
    retry = client.post(
        f"/api/conversations/{conversation_id}/messages", json={"content": "retry"}
    )
    assert retry.status_code == 202
    assert wait_for_final(client, retry.json()["operation_id"])[-1]["status"] == "completed"


def test_cancelled_operation_is_persisted_and_replayed_after_restart(tmp_path) -> None:
    settings = Settings(
        data_dir=tmp_path / "data",
        database_url=f"sqlite:///{tmp_path / 'cancel.db'}",
    )
    fake = FakeHermes()

    async def cancel(*args, **kwargs):
        raise HermesCancelledError("cancelled")

    fake.send = cancel
    app = create_app(settings=settings, employee=fake)
    with TestClient(app) as client:
        conversation_id = create_conversation(client)
        sent = client.post(
            f"/api/conversations/{conversation_id}/messages", json={"content": "cancel me"}
        )
        operation_id = sent.json()["operation_id"]
        assert wait_for_final(client, operation_id)[-1]["status"] == "cancelled"

    restarted = create_app(settings=settings, employee=FakeHermes())
    with TestClient(restarted) as client:
        replay = wait_for_final(client, operation_id)
        assert replay == [
            {
                "type": "final",
                "status": "cancelled",
                "operation_id": operation_id,
                "message_id": replay[0]["message_id"],
            }
        ]


def test_send_returns_503_when_employee_preflight_is_unavailable(api) -> None:
    client, fake, _ = api
    conversation_id = create_conversation(client)
    fake.available = False

    response = client.post(
        f"/api/conversations/{conversation_id}/messages", json={"content": "hello"}
    )

    assert response.status_code == 503
    assert client.get(f"/api/conversations/{conversation_id}/messages").json() == []


@pytest.mark.parametrize("missing", ["executable", "profile"])
def test_real_adapter_preflight_returns_503_without_model_call(
    tmp_path, monkeypatch, missing
) -> None:
    hermes_home = tmp_path / "hermes-home"
    monkeypatch.setenv("HERMES_HOME", str(hermes_home))
    settings = Settings(
        data_dir=tmp_path / "data",
        database_url=f"sqlite:///{tmp_path / 'preflight.db'}",
        hermes_executable="definitely-missing-hermes" if missing == "executable" else sys.executable,
        hermes_profile="missing-profile",
    )
    app = create_app(settings=settings)

    with TestClient(app) as client:
        conversation_id = create_conversation(client)
        response = client.post(
            f"/api/conversations/{conversation_id}/messages", json={"content": "hello"}
        )

        assert response.status_code == 503
        assert client.get(f"/api/conversations/{conversation_id}/messages").json() == []


def test_missing_operation_is_404(api) -> None:
    client, _, _ = api
    assert client.get("/api/events/not-found").status_code == 404


def test_startup_reconciles_running_operation_once_and_sse_replays(tmp_path) -> None:
    settings = Settings(
        data_dir=tmp_path / "data",
        database_url=f"sqlite:///{tmp_path / 'interrupted.db'}",
    )
    first_app = create_app(settings=settings, employee=FakeHermes())
    with TestClient(first_app) as client:
        factory = client.app.state.session_factory
        with factory() as session:
            conversation = Conversation(title="Interrupted")
            session.add(conversation)
            session.flush()
            session.add(
                Message(
                    conversation_id=conversation.id,
                    role="user",
                    content="unfinished",
                    operation_id="interrupted-op",
                    operation_status="running",
                )
            )
            session.commit()

    second_app = create_app(settings=settings, employee=FakeHermes())
    with TestClient(second_app) as client:
        events = wait_for_final(client, "interrupted-op")
        assert events[-1]["status"] == "failed"
        stored = client.get("/api/conversations/1/messages").json()
        assert [(row["role"], row["content"]) for row in stored] == [
            ("user", "unfinished"),
            ("system", "The app restarted before the employee completed the request. Please retry."),
        ]

    third_app = create_app(settings=settings, employee=FakeHermes())
    with TestClient(third_app) as client:
        stored = client.get("/api/conversations/1/messages").json()
        assert len(stored) == 2


def test_completed_operation_is_evicted_from_broker_and_replays_from_db(api) -> None:
    client, _, _ = api
    conversation_id = create_conversation(client)
    sent = client.post(
        f"/api/conversations/{conversation_id}/messages", json={"content": "complete"}
    )
    operation_id = sent.json()["operation_id"]
    assert wait_for_final(client, operation_id)[-1]["status"] == "completed"

    assert operation_id not in client.app.state.chat_service.operations
    assert wait_for_final(client, operation_id)[-1]["status"] == "completed"


def test_unconsumed_terminal_operation_brokers_are_bounded(api) -> None:
    client, _, _ = api
    service = client.app.state.chat_service
    service.operation_broker_limit = 2

    for number in range(3):
        conversation_id = create_conversation(client, f"Conversation {number}")
        sent = client.post(
            f"/api/conversations/{conversation_id}/messages",
            json={"content": f"message {number}"},
        )
        assert sent.status_code == 202

    client.get("/api/conversations")
    assert len(service.operations) <= 2
