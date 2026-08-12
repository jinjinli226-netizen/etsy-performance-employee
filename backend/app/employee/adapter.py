from __future__ import annotations

import asyncio
import os
import re
import shutil
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol


@dataclass(frozen=True)
class EmployeeReply:
    text: str
    session_id: str


class HermesAdapter(Protocol):
    def check_available(self) -> None: ...

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


class EmployeeUnavailableError(HermesAdapterError):
    pass


class HermesTimeoutError(HermesAdapterError):
    pass


class HermesMalformedReplyError(HermesAdapterError):
    pass


class HermesCancelledError(asyncio.CancelledError):
    pass


_SESSION_LINE = re.compile(r"(?im)^\s*session_id:\s*([A-Za-z0-9][A-Za-z0-9._:-]{0,255})\s*$")


def resolve_hermes_profiles_root(*, platform_name: str | None = None) -> Path:
    """Resolve Hermes' named-profile root without importing the Hermes package."""
    explicit_home = os.environ.get("HERMES_HOME")
    if explicit_home:
        return (Path(explicit_home).expanduser() / "profiles").resolve()

    current_platform = (platform_name or sys.platform).lower()
    if current_platform.startswith("win"):
        local_app_data = os.environ.get("LOCALAPPDATA")
        if not local_app_data:
            raise EmployeeUnavailableError("The Hermes profile location is unavailable.")
        hermes_home = Path(local_app_data) / "hermes"
    else:
        hermes_home = Path.home() / ".hermes"
    return (hermes_home / "profiles").resolve()


class SubprocessHermesAdapter:
    def __init__(
        self,
        *,
        executable: str = "hermes",
        profile: str = "etsy-performance-us",
        timeout_seconds: float = 180,
        data_root: Path,
        max_turns: int = 12,
        profiles_root: Path | None = None,
    ) -> None:
        self.executable = executable
        self.profile = profile
        self.timeout_seconds = timeout_seconds
        self.data_root = data_root.resolve()
        self.max_turns = max_turns
        self.profiles_root = (
            profiles_root.resolve() if profiles_root else resolve_hermes_profiles_root()
        )

    def check_available(self) -> None:
        """Verify local prerequisites without starting Hermes or contacting a model."""
        if shutil.which(self.executable) is None:
            raise EmployeeUnavailableError("The employee service is unavailable.")
        if self.profile not in {"default", "custom"}:
            profile_dir = (self.profiles_root / self.profile).resolve()
            if not profile_dir.is_relative_to(self.profiles_root) or not profile_dir.is_dir():
                raise EmployeeUnavailableError("The configured employee profile is unavailable.")

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
