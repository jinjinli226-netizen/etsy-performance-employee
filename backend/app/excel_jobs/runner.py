from __future__ import annotations

import asyncio
import json
import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Awaitable, Callable, Protocol


class WorkerProtocolError(RuntimeError):
    pass


class WorkerUnavailableError(RuntimeError):
    pass


@dataclass(frozen=True)
class RunnerRequest:
    public_id: str
    source_path: Path
    operation_dir: Path
    rules_path: Path
    knowledge_path: Path
    knowledge_export_id: str
    knowledge_payload_sha256: str
    knowledge_file_sha256: str
    guard_path: Path | None = None
    guard_export_id: str | None = None
    guard_payload_sha256: str | None = None
    guard_file_sha256: str | None = None


@dataclass(frozen=True)
class WorkerResult:
    output_path: Path
    output_sha256: str


EventEmitter = Callable[[dict], Awaitable[None]]


class ExcelRunner(Protocol):
    async def run(self, request: RunnerRequest, emit: EventEmitter) -> WorkerResult: ...

    async def cancel(self, public_id: str) -> None: ...

    async def shutdown(self) -> None: ...


def build_employee_command(
    request: RunnerRequest,
    *,
    repository_root: Path,
    python_executable: str = sys.executable,
) -> list[str]:
    entry = repository_root / "employee" / "skills" / "etsy-performance-listing" / "scripts" / "run_task.py"
    command = [
        python_executable,
        str(entry.resolve()),
        str(request.source_path.resolve()),
        str(request.operation_dir.resolve()),
        "--rules",
        str(request.rules_path.resolve()),
        "--knowledge",
        str(request.knowledge_path.resolve()),
        "--expected-knowledge-export-id",
        request.knowledge_export_id,
        "--expected-knowledge-payload-sha256",
        request.knowledge_payload_sha256,
        "--expected-knowledge-file-sha256",
        request.knowledge_file_sha256,
    ]
    if request.guard_path is not None:
        if not all(isinstance(value, str) for value in (request.guard_export_id, request.guard_payload_sha256, request.guard_file_sha256)):
            raise WorkerProtocolError("Evidence guard detached trust is incomplete.")
        command.extend([
            "--guard", str(request.guard_path.resolve()),
            "--expected-guard-export-id", request.guard_export_id,
            "--expected-guard-payload-sha256", request.guard_payload_sha256,
            "--expected-guard-file-sha256", request.guard_file_sha256,
        ])
    return command


async def _bounded_stream(stream: asyncio.StreamReader, *, max_bytes: int) -> bytes:
    data = bytearray()
    while True:
        chunk = await stream.read(min(8192, max_bytes + 1 - len(data)))
        if not chunk:
            return bytes(data)
        data.extend(chunk)
        if len(data) > max_bytes:
            raise WorkerProtocolError("Worker stderr exceeded its limit.")


class SubprocessExcelRunner:
    _EVENTS = {"started", "row_started", "row_completed", "row_skipped", "row_failed", "completed", "failed"}

    def __init__(
        self,
        *,
        repository_root: Path,
        max_event_bytes: int,
        cancel_timeout_seconds: float,
        worker_timeout_seconds: float = 4800,
    ) -> None:
        self.repository_root = repository_root.resolve()
        self.max_event_bytes = max_event_bytes
        self.cancel_timeout_seconds = cancel_timeout_seconds
        self.worker_timeout_seconds = worker_timeout_seconds
        self._processes: dict[str, asyncio.subprocess.Process] = {}
        self._lock = asyncio.Lock()

    async def run(self, request: RunnerRequest, emit: EventEmitter) -> WorkerResult:
        command = build_employee_command(request, repository_root=self.repository_root)
        try:
            process = await asyncio.create_subprocess_exec(
                *command,
                stdin=asyncio.subprocess.DEVNULL,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                limit=self.max_event_bytes + 1,
            )
        except OSError as exc:
            raise WorkerUnavailableError("The Excel employee worker could not be started.") from exc
        async with self._lock:
            if request.public_id in self._processes:
                await self._terminate(process)
                raise WorkerProtocolError("A worker is already running for this job.")
            self._processes[request.public_id] = process
        assert process.stdout is not None and process.stderr is not None
        completed: WorkerResult | None = None

        async def consume_stdout() -> None:
            nonlocal completed
            while True:
                try:
                    line = await process.stdout.readline()
                except (ValueError, asyncio.LimitOverrunError) as exc:
                    raise WorkerProtocolError("A worker event exceeded its line limit.") from exc
                if not line:
                    return
                if len(line) > self.max_event_bytes:
                    raise WorkerProtocolError("A worker event exceeded its line limit.")
                try:
                    event = json.loads(line.decode("utf-8"))
                except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                    raise WorkerProtocolError("The worker emitted malformed JSONL.") from exc
                if not isinstance(event, dict) or event.get("event") not in self._EVENTS:
                    raise WorkerProtocolError("The worker emitted an unknown event.")
                if event["event"] == "completed":
                    if completed is not None or not isinstance(event.get("output_path"), str) or not isinstance(event.get("output_sha256"), str):
                        raise WorkerProtocolError("The worker emitted an invalid completion event.")
                    completed = WorkerResult(Path(event["output_path"]), event["output_sha256"])
                await emit(event)

        stdout_task = asyncio.create_task(consume_stdout(), name=f"excel-stdout-{request.public_id}")
        stderr_task = asyncio.create_task(
            _bounded_stream(process.stderr, max_bytes=self.max_event_bytes),
            name=f"excel-stderr-{request.public_id}",
        )
        wait_task = asyncio.create_task(process.wait(), name=f"excel-wait-{request.public_id}")
        protocol_tasks = (stdout_task, stderr_task, wait_task)
        try:
            try:
                await asyncio.wait_for(
                    asyncio.gather(*protocol_tasks),
                    timeout=self.worker_timeout_seconds,
                )
            except TimeoutError as exc:
                raise WorkerProtocolError("The worker exceeded its time limit.") from exc
            except WorkerProtocolError:
                raise
            except (OSError, UnicodeError, ValueError) as exc:
                raise WorkerProtocolError("A worker output stream could not be read safely.") from exc
            return_code = wait_task.result()
            if return_code != 0:
                raise RuntimeError("Excel worker failed.")
            if completed is None:
                raise WorkerProtocolError("The worker exited without a completion event.")
            return completed
        except BaseException:
            if process.returncode is None:
                await self._terminate(process)
            try:
                await asyncio.wait_for(
                    asyncio.gather(*protocol_tasks, return_exceptions=True),
                    timeout=self.cancel_timeout_seconds,
                )
            except TimeoutError:
                for task in protocol_tasks:
                    if not task.done():
                        task.cancel()
                await asyncio.gather(*protocol_tasks, return_exceptions=True)
            raise
        finally:
            async with self._lock:
                self._processes.pop(request.public_id, None)
            transport = getattr(process, "_transport", None)
            if transport is not None:
                transport.close()
                await asyncio.sleep(0)

    async def _terminate(self, process: asyncio.subprocess.Process) -> None:
        if process.returncode is not None:
            return
        if os.name == "nt":
            try:
                killer = await asyncio.create_subprocess_exec(
                    "taskkill.exe",
                    "/PID",
                    str(process.pid),
                    "/T",
                    "/F",
                    stdin=asyncio.subprocess.DEVNULL,
                    stdout=asyncio.subprocess.DEVNULL,
                    stderr=asyncio.subprocess.DEVNULL,
                )
                await asyncio.wait_for(killer.wait(), timeout=self.cancel_timeout_seconds)
            except (OSError, TimeoutError):
                pass
        if process.returncode is None:
            process.terminate()
            try:
                await asyncio.wait_for(process.wait(), timeout=self.cancel_timeout_seconds)
            except TimeoutError:
                process.kill()
                await asyncio.wait_for(process.wait(), timeout=self.cancel_timeout_seconds)

    async def cancel(self, public_id: str) -> None:
        async with self._lock:
            process = self._processes.get(public_id)
        if process is not None:
            await self._terminate(process)

    async def shutdown(self) -> None:
        async with self._lock:
            processes = list(self._processes.values())
        if processes:
            await asyncio.gather(*(self._terminate(process) for process in processes), return_exceptions=True)
