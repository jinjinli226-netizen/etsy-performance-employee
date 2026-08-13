from __future__ import annotations

import asyncio
import io
import json
import sys
import zipfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from openpyxl import Workbook
from PIL import Image
from sqlalchemy import func, select

from app.core.config import Settings
from app.db.models import Attachment, AuditEvent, CompetitorEvidence, Conversation, KnowledgeCandidate, Message
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


def png_bytes() -> bytes:
    output = io.BytesIO()
    Image.new("RGB", (2, 2), "red").save(output, format="PNG")
    return output.getvalue()


def xlsx_bytes() -> bytes:
    output = io.BytesIO()
    workbook = Workbook()
    workbook.active["A1"] = "title"
    workbook.save(output)
    workbook.close()
    return output.getvalue()


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
        files={"file": ("../dress.png", png_bytes(), "image/png")},
    )
    spreadsheet = client.post(
        "/api/attachments",
        data={"conversation_id": str(first)},
        files={
            "file": (
                "listings.xlsx",
                xlsx_bytes(),
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
            files={"file": (name, png_bytes(), "image/png")},
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


@pytest.mark.parametrize(
    ("filename", "content", "media_type"),
    [
        ("fake.png", b"not an image", "image/png"),
        ("polyglot.png", png_bytes() + b"PK\x03\x04hidden", "image/png"),
        ("renamed.xlsx", png_bytes(), "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"),
        ("photo.png", png_bytes(), "image/jpeg"),
        ("nul.txt", b"safe\x00unsafe", "text/plain"),
    ],
)
def test_attachment_rejects_content_or_declared_type_mismatch(api, filename, content, media_type) -> None:
    client, _, settings = api
    conversation_id = create_conversation(client)
    response = client.post(
        "/api/attachments",
        data={"conversation_id": str(conversation_id)},
        files={"file": (filename, content, media_type)},
    )
    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "attachment_content_mismatch"
    with client.app.state.session_factory() as session:
        assert session.scalar(select(func.count()).select_from(Attachment)) == 0
    assert not list((settings.data_dir / "attachments" / str(conversation_id)).glob("*"))


def test_attachment_accepts_valid_tiny_image_xlsx_pdf_and_utf8_text(api) -> None:
    client, _, _ = api
    conversation_id = create_conversation(client)
    samples = [
        ("photo.png", png_bytes(), "image/png"),
        ("listing.xlsx", xlsx_bytes(), "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"),
        ("brief.pdf", b"%PDF-1.4\n1 0 obj\n<<>>\nendobj\n%%EOF", "application/pdf"),
        ("notes.txt", "舞台服\nnotes".encode(), "text/plain"),
        ("data.json", b'{"safe":true}', "application/json"),
    ]
    for filename, content, media_type in samples:
        response = client.post(
            "/api/attachments",
            data={"conversation_id": str(conversation_id)},
            files={"file": (filename, content, media_type)},
        )
        assert response.status_code == 201, response.text


def test_atomic_batch_rejects_second_bad_file_without_rows_files_or_employee_call(api) -> None:
    client, fake, settings = api
    conversation_id = create_conversation(client)
    response = client.post(
        f"/api/conversations/{conversation_id}/messages/batch",
        data={"content": "Review files", "learning_mode": "false"},
        files=[
            ("files", ("valid.png", png_bytes(), "image/png")),
            ("files", ("bad.xlsx", b"PK fake workbook", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")),
        ],
    )
    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "attachment_content_mismatch"
    with client.app.state.session_factory() as session:
        assert session.scalar(select(func.count()).select_from(Attachment)) == 0
        assert session.scalar(select(func.count()).select_from(Message)) == 0
    assert fake.calls == []
    assert not list((settings.data_dir / "attachments" / str(conversation_id)).glob("*"))


def test_attachment_rejects_xlsx_zip_bomb_before_employee_access(api) -> None:
    client, fake, _ = api
    conversation_id = create_conversation(client)
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("[Content_Types].xml", "<Types/>")
        archive.writestr("xl/workbook.xml", "<workbook/>")
        archive.writestr("xl/worksheets/sheet1.xml", b"A" * (11 * 1024 * 1024))
    response = client.post(
        f"/api/conversations/{conversation_id}/messages/batch",
        data={"content": "unsafe", "learning_mode": "false"},
        files=[("files", ("bomb.xlsx", output.getvalue(), "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"))],
    )
    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "attachment_content_mismatch"
    assert fake.calls == []


def test_atomic_batch_persists_claimed_valid_attachments_and_message(api) -> None:
    client, fake, _ = api
    conversation_id = create_conversation(client)
    response = client.post(
        f"/api/conversations/{conversation_id}/messages/batch",
        data={"content": "Review these", "learning_mode": "false"},
        files=[
            ("files", ("valid.png", png_bytes(), "image/png")),
            ("files", ("notes.txt", b"safe notes", "text/plain")),
        ],
    )
    assert response.status_code == 202, response.text
    wait_for_final(client, response.json()["operation_id"])
    stored = client.get(f"/api/conversations/{conversation_id}/messages").json()
    assert [item["filename"] for item in stored[0]["attachments"]] == ["valid.png", "notes.txt"]
    with client.app.state.session_factory() as session:
        attachments = list(session.scalars(select(Attachment).order_by(Attachment.id)))
        assert all(item.claimed_by_message_id == stored[0]["id"] for item in attachments)
    assert Path(fake.calls[-1]["image_path"]).name.endswith(".png")


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


def test_control_parser_decodes_unicode_event_and_pretty_multiline_frames() -> None:
    raw = (
        "Visible before\n"
        "{\n"
        '  "event": "learning\\u005fbatch",\n'
        '  "payload": {"evidence_items": [], "candidates": []}\n'
        "}\n"
        "Visible after"
    )
    parsed = parse_final_envelopes(raw)
    assert parsed.visible_text == "Visible before\nVisible after"
    assert parsed.envelopes == [{"event": "learning_batch", "payload": {"evidence_items": [], "candidates": []}}]
    assert parsed.control_errors == []


def test_control_parser_keeps_valid_noncontrol_json_but_strips_invalid_object_block() -> None:
    raw = (
        'Visible\n{"example":{"answer":42}}\n'
        '{\n  "event": "learning_batch",\n  "payload": {"evidence_items": [], BAD}\n}\n'
        "Visible after"
    )
    parsed = parse_final_envelopes(raw)
    assert parsed.visible_text == 'Visible\n{"example":{"answer":42}}\nVisible after'
    assert parsed.envelopes == []
    assert parsed.control_errors == ["envelope_invalid"]


def test_unclosed_json_tail_is_removed_fail_closed() -> None:
    parsed = parse_final_envelopes('Visible before\n{"event":"learning_batch","payload":{\nRAWSECRET visible-looking tail')
    assert parsed.visible_text == "Visible before"
    assert parsed.control_errors == ["envelope_invalid"]


def test_oversized_pretty_control_block_is_removed_as_one_frame() -> None:
    raw = 'Visible before\n{\n  "event":"learning_batch",\n  "payload":{"evidence_items":[],"candidates":[],"extra":"' + ("x" * 132_000) + '"}\n}'
    parsed = parse_final_envelopes(raw)
    assert parsed.visible_text == "Visible before"
    assert parsed.control_errors == ["envelope_invalid"]


def test_unindented_pretty_control_tracks_nested_arrays_and_braces_inside_strings() -> None:
    raw = (
        "Visible before\n{\n"
        '"event":"learning_batch",\n'
        '"payload":{\n'
        '"evidence_items":[],\n'
        '"candidates":[]\n'
        "}\n}\n"
        "Visible after"
    )
    parsed = parse_final_envelopes(raw)
    assert parsed.visible_text == "Visible before\nVisible after"
    assert len(parsed.envelopes) == 1

    noncontrol = '{\n"example":[{"text":"brace } and escaped \\\" quote"}]\n}'
    parsed = parse_final_envelopes(noncontrol)
    assert parsed.visible_text == noncontrol
    assert parsed.control_errors == []


@pytest.mark.parametrize(
    "wrapped",
    [
        '[{"event":"learning_batch","payload":{"evidence_items":[],"candidates":[],"raw":"RAWSECRET"}}]',
        '{"wrapper":{"items":[{"event":"learning\\u005fbatch","payload":{"raw":"RAWSECRET"}}]}}',
        '{"event":"learning_private","payload":{"raw":"RAWSECRET"}}',
        '{"type":"operation_override","payload":{"raw":"RAWSECRET"}}',
    ],
)
def test_nested_or_reserved_control_shapes_are_fail_closed(wrapped) -> None:
    parsed = parse_final_envelopes("Visible\n" + wrapped)
    assert parsed.visible_text == "Visible"
    assert parsed.envelopes == []
    assert parsed.control_errors == ["envelope_invalid"]
    assert "RAWSECRET" not in parsed.visible_text


def test_regular_arrays_and_business_event_json_remain_visible() -> None:
    raw = '[{"event":"order_created","payload":{"id":42}},[1,2,3]]'
    parsed = parse_final_envelopes(raw)
    assert parsed.visible_text == raw
    assert parsed.control_errors == []


def test_json_control_shape_recursion_cap_fails_closed() -> None:
    wrapped = "[" * 80 + '{"event":"learning_batch","payload":{"raw":"RAWSECRET"}}' + "]" * 80
    parsed = parse_final_envelopes(wrapped)
    assert parsed.visible_text == ""
    assert parsed.control_errors == ["envelope_invalid"]


def test_json_decoder_recursion_error_is_fail_closed_without_raw() -> None:
    wrapped = "[" * 1100 + '"RAWSECRET"' + "]" * 1100
    parsed = parse_final_envelopes("Visible\n" + wrapped)
    assert parsed.visible_text == "Visible"
    assert parsed.envelopes == []
    assert parsed.control_errors == ["envelope_invalid"]
    assert "RAWSECRET" not in parsed.visible_text


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
                "type": "progress",
                "status": "running",
                "operation_id": operation_id,
            },
            {
                    "type": "final",
                    "status": "cancelled",
                    "operation_id": operation_id,
                    "message_id": replay[1]["message_id"],
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


def test_sse_uses_persisted_message_ids_and_reconnect_replays_only_new_events(api) -> None:
    client, _, _ = api
    conversation_id = create_conversation(client)
    sent = client.post(
        f"/api/conversations/{conversation_id}/messages", json={"content": "stable ids"}
    )
    operation_id = sent.json()["operation_id"]
    with client.stream("GET", f"/api/events/{operation_id}") as response:
        body = "\n".join(response.iter_lines())
    assert "event: operation" in body
    ids = [int(line[4:]) for line in body.splitlines() if line.startswith("id: ")]
    assert ids == sorted(set(ids))
    assert len(ids) == 2
    assert ids[0] == 1
    assert ids[1] == 2

    with client.stream(
        "GET", f"/api/events/{operation_id}", headers={"Last-Event-ID": str(ids[0])}
    ) as response:
        replay = "\n".join(response.iter_lines())
    assert f"id: {ids[0]}" not in replay
    assert f"id: {ids[1]}" in replay

    with client.stream(
        "GET", f"/api/events/{operation_id}", headers={"Last-Event-ID": str(ids[1])}
    ) as response:
        assert "\n".join(response.iter_lines()) == ""


@pytest.mark.parametrize("value", ["-1", "1.5", "abc", "+1", " 1"])
def test_sse_rejects_invalid_last_event_id(api, value) -> None:
    client, _, _ = api
    conversation_id = create_conversation(client)
    sent = client.post(f"/api/conversations/{conversation_id}/messages", json={"content": "x"})
    assert client.get(
        f"/api/events/{sent.json()['operation_id']}", headers={"Last-Event-ID": value}
    ).status_code == 422


@pytest.mark.parametrize(
    ("url", "accepted"),
    [
        ("https://www.etsy.com/listing/123456/valid-slug?utm_source=x", True),
        ("https://etsy.com/listing/123456", True),
        ("http://www.etsy.com/listing/123456", False),
        ("https://www.etsy.com.evil/listing/123456", False),
        ("https://user@www.etsy.com/listing/123456", False),
        ("https://www.etsy.com:443/listing/123456", False),
        ("https://www.etsy.com/listing/123.evil", False),
        ("https://www.etsy.com/listing/123/slug#fragment", False),
    ],
)
def test_learning_mode_uses_strict_canonical_etsy_urls(api, url, accepted) -> None:
    client, _, _ = api
    conversation_id = create_conversation(client)
    response = client.post(
        f"/api/conversations/{conversation_id}/messages",
        json={"content": f"learn {url}", "learning_mode": True},
    )
    assert (response.status_code == 202) is accepted


def test_failed_message_retry_reuses_persisted_attachments_without_reupload(api) -> None:
    client, fake, _ = api
    conversation_id = create_conversation(client)
    uploaded = client.post(
        "/api/attachments",
        data={"conversation_id": str(conversation_id)},
        files={"file": ("notes.txt", b"facts", "text/plain")},
    ).json()

    async def fail(*args, **kwargs):
        raise RuntimeError("fail")

    fake.send = fail
    sent = client.post(
        f"/api/conversations/{conversation_id}/messages",
        json={"content": "retry with file", "attachment_ids": [uploaded["id"]]},
    )
    wait_for_final(client, sent.json()["operation_id"])
    stored = client.get(f"/api/conversations/{conversation_id}/messages").json()
    user = next(row for row in stored if row["role"] == "user")
    assert user["attachments"][0]["filename"] == "notes.txt"

    success = FakeHermes()
    fake.send = success.send
    retried = client.post(
        f"/api/conversations/{conversation_id}/messages/{user['id']}/retry"
    )
    assert retried.status_code == 202
    wait_for_final(client, retried.json()["operation_id"])
    assert "notes.txt" in success.calls[-1]["prompt"]

    assert client.post(
        f"/api/conversations/{conversation_id}/messages/{user['id']}/retry"
    ).status_code == 409


def test_candidate_status_endpoint_returns_only_safe_status_metadata(api) -> None:
    client, fake, _ = api
    conversation_id = create_conversation(client)

    async def learned(*args, **kwargs):
        return EmployeeReply(
            text=(
                "学习完成\n"
                '{"event":"learning_batch","payload":{"evidence_items":[],"candidates":[]}}'
            ),
            session_id="learned",
        )

    fake.send = learned
    sent = client.post(
        f"/api/conversations/{conversation_id}/messages",
        json={
            "content": "learn https://www.etsy.com/listing/123456/sample",
            "learning_mode": True,
        },
    )
    operation_id = sent.json()["operation_id"]
    wait_for_final(client, operation_id)
    response = client.get("/api/knowledge/candidates/status", params={"trace_id": operation_id})
    assert response.status_code == 200
    assert response.json() == []
    assert client.get("/api/knowledge/candidates/status", params={"trace_id": "not-a-uuid"}).status_code == 422


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
