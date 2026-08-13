from __future__ import annotations

import json
import re
import shutil
from pathlib import Path
from uuid import uuid4

from fastapi import APIRouter, File, Form, HTTPException, Query, Request, UploadFile, status
from fastapi.responses import StreamingResponse

from app.chat.schemas import (
    AttachmentRead,
    ChatSendRequest,
    ConversationCreate,
    ConversationPage,
    ConversationSummary,
    MessageRead,
    OperationAccepted,
)
from app.chat.attachments import AttachmentValidationError, cleanup_staging, stage_upload
from app.chat.service import (
    AttachmentScopeError,
    ChatService,
    ConversationBusyError,
    ConversationNotFoundError,
)
from app.db.models import Attachment, Conversation
from app.employee.adapter import EmployeeUnavailableError

router = APIRouter(prefix="/api")

def service(request: Request) -> ChatService:
    return request.app.state.chat_service


@router.post("/conversations", response_model=ConversationSummary, status_code=201)
def create_conversation(payload: ConversationCreate, request: Request):
    return service(request).create_conversation(payload.title)


@router.get("/conversations", response_model=ConversationPage)
def list_conversations(
    request: Request,
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
):
    items, total = service(request).list_conversations(limit, offset)
    return ConversationPage(
        items=[ConversationSummary.model_validate(item) for item in items],
        total=total,
        limit=limit,
        offset=offset,
    )


@router.get("/conversations/{conversation_id}/messages", response_model=list[MessageRead])
def list_messages(conversation_id: int, request: Request):
    try:
        chat_service = service(request)
        messages = chat_service.list_messages(conversation_id)
        attachments = chat_service.message_attachments(messages)
        return [
            MessageRead(
                id=message.id,
                conversation_id=message.conversation_id,
                role=message.role,
                content=message.content,
                created_at=message.created_at,
                operation_id=message.operation_id,
                operation_status=message.operation_status,
                learning_mode=bool(message.learning_mode),
                attachments=[AttachmentRead.model_validate(item) for item in attachments.get(message.id, [])],
            )
            for message in messages
        ]
    except ConversationNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Conversation not found") from exc


@router.post(
    "/conversations/{conversation_id}/messages",
    response_model=OperationAccepted,
    status_code=status.HTTP_202_ACCEPTED,
)
async def send_message(conversation_id: int, payload: ChatSendRequest, request: Request):
    try:
        operation_id = await service(request).start_send(
            conversation_id, payload.content, payload.attachment_ids, learning_mode=payload.learning_mode
        )
    except ConversationNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Conversation not found") from exc
    except AttachmentScopeError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except ConversationBusyError as exc:
        raise HTTPException(status_code=409, detail="Conversation is already processing") from exc
    except EmployeeUnavailableError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    return OperationAccepted(operation_id=operation_id, status="running")


@router.post(
    "/conversations/{conversation_id}/messages/{message_id}/retry",
    response_model=OperationAccepted,
    status_code=status.HTTP_202_ACCEPTED,
)
async def retry_message(conversation_id: int, message_id: int, request: Request):
    chat_service = service(request)
    try:
        content, attachment_ids, learning_mode = chat_service.retry_payload(conversation_id, message_id)
        try:
            operation_id = await chat_service.start_send(
                conversation_id, content, attachment_ids, learning_mode=learning_mode,
                reuse_message_id=message_id,
            )
        except Exception:
            chat_service.release_retry_claim(conversation_id, message_id)
            raise
    except ConversationNotFoundError as exc:
        raise HTTPException(404, "Message not found") from exc
    except ConversationBusyError as exc:
        raise HTTPException(409, "Message is not retryable") from exc
    except (AttachmentScopeError, EmployeeUnavailableError) as exc:
        raise HTTPException(503, "Employee unavailable") from exc
    return OperationAccepted(operation_id=operation_id, status="running")


def _safe_filename(filename: str | None) -> str:
    name = Path((filename or "attachment").replace("\\", "/")).name
    cleaned = re.sub(r"[^A-Za-z0-9._ -]+", "_", name).strip(" .")
    return (cleaned or "attachment")[:255]


@router.post("/attachments", response_model=AttachmentRead, status_code=201)
async def upload_attachment(
    request: Request,
    conversation_id: int = Form(...),
    file: UploadFile = File(...),
):
    settings = request.app.state.settings
    filename = _safe_filename(file.filename)
    storage_dir = (settings.data_dir / "attachments" / str(conversation_id)).resolve()
    attachment_root = (settings.data_dir / "attachments").resolve()
    if not storage_dir.is_relative_to(attachment_root):
        raise HTTPException(status_code=422, detail="Invalid storage path")

    with request.app.state.session_factory() as session:
        if session.get(Conversation, conversation_id) is None:
            raise HTTPException(status_code=404, detail="Conversation not found")

    staging = attachment_root / ".staging" / uuid4().hex
    destination: Path | None = None
    try:
        staged = await stage_upload(file, staging_root=staging, filename=filename, max_bytes=settings.max_attachment_bytes)
        storage_dir.mkdir(parents=True, exist_ok=True)
        destination = storage_dir / f"{uuid4().hex}{staged.suffix}"
        shutil.move(str(staged.path), destination)
        with request.app.state.session_factory() as session:
            attachment = Attachment(
                conversation_id=conversation_id,
                filename=filename,
                path=str(destination),
                media_type=staged.media_type,
            )
            session.add(attachment)
            session.commit()
            return attachment
    except AttachmentValidationError as exc:
        raise HTTPException(exc.status_code, {"code": exc.code, "message": str(exc)}) from exc
    except Exception:
        if destination is not None:
            destination.unlink(missing_ok=True)
        raise
    finally:
        cleanup_staging(staging)


@router.post(
    "/conversations/{conversation_id}/messages/batch",
    response_model=OperationAccepted,
    status_code=status.HTTP_202_ACCEPTED,
)
async def send_message_batch(
    conversation_id: int,
    request: Request,
    content: str = Form(..., min_length=1, max_length=100_000),
    learning_mode: bool = Form(False),
    files: list[UploadFile] = File(default=[]),
):
    settings = request.app.state.settings
    staging = (settings.data_dir / "attachments" / ".staging" / uuid4().hex).resolve()
    staged = []
    try:
        if not content.strip():
            raise AttachmentValidationError("attachment_unsupported", "Message content cannot be blank.")
        if len(files) > 20:
            raise AttachmentValidationError("attachment_unsupported", "Too many attachments.")
        for file in files:
            staged.append(await stage_upload(
                file,
                staging_root=staging,
                filename=_safe_filename(file.filename),
                max_bytes=settings.max_attachment_bytes,
            ))
        operation_id = await service(request).start_send_staged(
            conversation_id,
            content.strip(),
            staged,
            learning_mode=learning_mode,
            attachment_root=(settings.data_dir / "attachments").resolve(),
        )
    except AttachmentValidationError as exc:
        raise HTTPException(exc.status_code, {"code": exc.code, "message": str(exc)}) from exc
    except ConversationNotFoundError as exc:
        raise HTTPException(404, "Conversation not found") from exc
    except AttachmentScopeError as exc:
        raise HTTPException(422, str(exc)) from exc
    except ConversationBusyError as exc:
        raise HTTPException(409, "Conversation is already processing") from exc
    except EmployeeUnavailableError as exc:
        raise HTTPException(503, "Employee unavailable") from exc
    finally:
        cleanup_staging(staging)
    return OperationAccepted(operation_id=operation_id, status="running")


@router.get("/events/{operation_id}")
async def stream_events(operation_id: str, request: Request):
    chat_service = service(request)
    raw_last_id = request.headers.get("last-event-id")
    if raw_last_id is None:
        raw_last_id = request.query_params.get("last_event_id")
    if raw_last_id is not None and not re.fullmatch(r"0|[1-9][0-9]*", raw_last_id):
        raise HTTPException(422, "Invalid Last-Event-ID")
    last_event_id = int(raw_last_id or "0")
    try:
        generator = chat_service.operation_events(operation_id, after_id=last_event_id)
        first = await anext(generator)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Operation not found") from exc
    except StopAsyncIteration:
        return StreamingResponse(iter(()), media_type="text/event-stream")

    async def generate():
        for event_id, event in [first]:
            yield f"id: {event_id}\nevent: operation\ndata: {json.dumps(event, separators=(',', ':'))}\n\n"
        async for event_id, event in generator:
            yield f"id: {event_id}\nevent: operation\ndata: {json.dumps(event, separators=(',', ':'))}\n\n"

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
