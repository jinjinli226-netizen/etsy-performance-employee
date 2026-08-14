from __future__ import annotations

from fastapi import APIRouter, Request

from app.employee.status import resolve_employee_status

router = APIRouter(prefix="/api/employee")


@router.get("/status")
def get_employee_status(request: Request) -> dict[str, str]:
    employee = request.app.state.employee
    return {"status": resolve_employee_status(employee, request.app.state.session_factory)}
