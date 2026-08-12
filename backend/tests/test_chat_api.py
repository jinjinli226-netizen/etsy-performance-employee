from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.core.config import Settings
from app.employee.adapter import EmployeeReply, HermesAdapter
from app.main import create_app


class FakeHermes(HermesAdapter):
    def __init__(self) -> None:
        self.calls: list[dict] = []
        self.release = asyncio.Event()
        self.block = False

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


def test_missing_operation_is_404(api) -> None:
    client, _, _ = api
    assert client.get("/api/events/not-found").status_code == 404
