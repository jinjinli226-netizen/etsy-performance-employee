from __future__ import annotations

from enum import StrEnum

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from app.db.models import ExcelJob, Message
from app.employee.adapter import HermesAdapter
from app.excel_jobs.schemas import JobStatus


class EmployeeAvailability(StrEnum):
    ONLINE = "online"
    BUSY = "busy"
    OFFLINE = "offline"


def resolve_employee_status(
    employee: HermesAdapter,
    session_factory: sessionmaker[Session],
) -> str:
    """Derive the employee's live status from local availability and durable work.

    Availability comes only from the local adapter preflight (executable plus
    named profile); no model is ever contacted. In-flight work is read from the
    persistent database rather than process-local state, so a restarted process
    still reports busy accurately. Failures to determine availability resolve to
    offline and never surface internal exception details.
    """
    try:
        employee.check_available()
    except Exception:
        return EmployeeAvailability.OFFLINE.value

    with session_factory() as session:
        running_chat = session.scalar(
            select(Message.id)
            .where(
                Message.operation_id.is_not(None),
                Message.operation_status == "running",
            )
            .limit(1)
        )
        active_job = session.scalar(
            select(ExcelJob.id)
            .where(ExcelJob.status.in_([JobStatus.QUEUED, JobStatus.RUNNING]))
            .limit(1)
        )

    if running_chat is not None or active_job is not None:
        return EmployeeAvailability.BUSY.value
    return EmployeeAvailability.ONLINE.value
