from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol


@dataclass(frozen=True)
class EmployeeReply:
    text: str
    session_id: str


class HermesAdapter(Protocol):
    async def send(
        self,
        prompt: str,
        session_id: str | None,
        image_path: Path | None,
        source: str,
    ) -> EmployeeReply: ...


class HermesAdapterError(RuntimeError):
    """A safe, user-presentable Hermes integration error."""


class HermesProcessError(HermesAdapterError):
    pass


class HermesTimeoutError(HermesAdapterError):
    pass


class HermesMalformedReplyError(HermesAdapterError):
    pass


class HermesCancelledError(asyncio.CancelledError):
    pass


_SESSION_LINE = re.compile(r"(?im)^\s*session_id:\s*([A-Za-z0-9][A-Za-z0-9._:-]{0,255})\s*$")


class SubprocessHermesAdapter:
    def __init__(
        self,
        *,
        executable: str = "hermes",
        profile: str = "etsy-performance-us",
        timeout_seconds: float = 180,
        data_root: Path,
        max_turns: int = 12,
    ) -> None:
        self.executable = executable
        self.profile = profile
        self.timeout_seconds = timeout_seconds
        self.data_root = data_root.resolve()
        self.max_turns = max_turns

    async def send(
        self,
        prompt: str,
        session_id: str | None,
        image_path: Path | None,
        source: str,
    ) -> EmployeeReply:
        if source not in {"app", "tool"}:
            raise ValueError("source must be 'app' or 'tool'")

        args = [
            self.executable,
            "-p",
            self.profile,
            "chat",
            "-Q",
            "--source",
            source,
            "--max-turns",
            str(self.max_turns),
            "--pass-session-id",
            "-q",
            prompt,
        ]
        if session_id:
            args.extend(["--resume", session_id])
        if image_path is not None:
            resolved_image = image_path.resolve(strict=True)
            if not resolved_image.is_file() or not resolved_image.is_relative_to(self.data_root):
                raise ValueError("image path must be a file under the application data root")
            args.extend(["--image", str(resolved_image)])

        try:
            process = await asyncio.create_subprocess_exec(
                *args,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
        except OSError as exc:
            raise HermesProcessError("The employee service is unavailable; please retry.") from exc
        try:
            stdout, stderr = await asyncio.wait_for(
                process.communicate(), timeout=self.timeout_seconds
            )
        except TimeoutError as exc:
            await self._stop(process)
            raise HermesTimeoutError("The employee timed out; please retry.") from exc
        except asyncio.CancelledError as exc:
            await self._stop(process)
            raise HermesCancelledError("The employee request was cancelled.") from exc

        if process.returncode != 0:
            raise HermesProcessError("The employee could not complete the request; please retry.")

        text = stdout.decode("utf-8", errors="replace").strip()
        session_match = _SESSION_LINE.search(stderr.decode("utf-8", errors="replace"))
        if not text or session_match is None:
            raise HermesMalformedReplyError("The employee returned an incomplete response.")
        return EmployeeReply(text=text, session_id=session_match.group(1))

    @staticmethod
    async def _stop(process) -> None:
        if process.returncode is not None:
            return
        process.terminate()
        try:
            await asyncio.wait_for(process.wait(), timeout=2)
        except TimeoutError:
            process.kill()
            await process.wait()
