from __future__ import annotations

import asyncio
import importlib.util
import json
import subprocess
from pathlib import Path

from app.employee.adapter import EmployeeReply, HermesAdapter
from app.excel_jobs.runner import ExcelRunner, RunnerRequest, WorkerResult


ROOT = Path(__file__).resolve().parents[3]


def load_employee_runner():
    path = ROOT / "employee" / "skills" / "etsy-performance-listing" / "scripts" / "run_task.py"
    spec = importlib.util.spec_from_file_location("mvp_employee_run_task", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def listing_for_prompt(prompt: str) -> dict[str, object]:
    envelope = json.loads(prompt[prompt.index("{"):])
    context = envelope["merged_product_context"]
    fields = {field["header"]: str(field["value"]) for field in context["candidate_fields"]}
    sku = fields.get("SKU", "Costume")
    color = "Blue" if sku.endswith("001") else "Red"
    return {
        "head_titles": f"{color} Sequin Stage Dance Costume",
        "tags": [
            "dance costume", "stage outfit", f"{color.lower()} costume", "sequin costume",
            "performance wear", "dancewear", "recital costume", "stage costume",
            "competition wear", "show costume", "costume outfit", "dancer gift", "theater costume",
        ],
        "specification": f"{color} performance costume for SKU {sku}.",
        "category": "Costumes",
        "instructions_for_buyers": "Review the supplied measurements before ordering.",
        "confidence": 0.93,
        "fact_warnings": [],
        "quality_warnings": [],
        "rule_version": envelope["rules"]["rule_version"],
    }


class StaticHermes(HermesAdapter):
    def check_available(self) -> None:
        return None

    async def send(self, prompt, session_id, image_path, source):
        return EmployeeReply(text=f"已收到并保存：{prompt}", session_id=session_id or "mvp-session")


class TeachingHermes(StaticHermes):
    async def send(self, prompt, session_id, image_path, source):
        url = "https://www.etsy.com/listing/700001/example"
        payload = {
            "evidence_items": [{
                "url": url,
                "title": "Protected competitor crystal fringe wording",
                "snapshot": "Protected competitor crystal fringe wording for a theatrical stage garment.",
                "tags": ["crystal fringe"],
                "source_timestamp": "2026-08-14T00:00:00Z",
            }],
            "candidates": [{
                "kind": "title_structure",
                "summary": "Lead with the occasion, garment type, silhouette, and intended audience.",
                "confidence": 0.91,
                "evidence_urls": [url],
                "evidence_ids": [],
            }],
        }
        return EmployeeReply(
            text="已提炼为待审批规则。\n" + json.dumps({"event": "learning_batch", "payload": payload}),
            session_id=session_id or "mvp-session",
        )


class EmployeeSkillRunner(ExcelRunner):
    """Run the real employee-owned workbook skill with deterministic model output."""

    async def run(self, request: RunnerRequest, emit) -> WorkerResult:
        employee = load_employee_runner()
        rules = json.loads(request.rules_path.read_text(encoding="utf-8"))
        loop = asyncio.get_running_loop()

        def sync_emit(event: dict) -> None:
            asyncio.run_coroutine_threadsafe(emit(event), loop).result(timeout=5)

        def fake_model(command: list[str], prompt: str):
            assert command[:4] == ["hermes", "-p", "etsy-performance-us", "chat"]
            assert "--resume" not in command and "--yolo" not in command
            if "--image" in command:
                visual = {
                    "schema_version": 1,
                    "visible_facts": {
                        "product_family": ["performance costume"],
                        "colors": ["blue"],
                        "silhouette": ["fitted"],
                        "garment_structure": ["one-piece"],
                        "decorations": ["sequins"],
                        "visible_components": ["costume"],
                        "visual_style": ["stagewear"],
                    },
                    "uncertain_observations": [],
                    "forbidden_inferences": [],
                    "image_usable": True,
                }
                return subprocess.CompletedProcess(command, 0, json.dumps(visual), "")
            return subprocess.CompletedProcess(command, 0, json.dumps(listing_for_prompt(prompt)), "")

        report = await asyncio.to_thread(
            employee.run_task,
            request.source_path,
            request.operation_dir,
            knowledge_path=request.knowledge_path,
            rules=rules,
            command_runner=fake_model,
            emit=sync_emit,
            expected_knowledge_export_id=request.knowledge_export_id,
            expected_knowledge_payload_sha256=request.knowledge_payload_sha256,
            expected_knowledge_file_sha256=request.knowledge_file_sha256,
            guard_path=request.guard_path,
            expected_guard_export_id=request.guard_export_id,
            expected_guard_payload_sha256=request.guard_payload_sha256,
            expected_guard_file_sha256=request.guard_file_sha256,
        )
        return WorkerResult(Path(report["output_path"]), report["output_sha256"])

    async def cancel(self, public_id: str) -> None:
        return None

    async def shutdown(self) -> None:
        return None
