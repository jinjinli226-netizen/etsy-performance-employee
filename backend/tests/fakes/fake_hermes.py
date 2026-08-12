from __future__ import annotations

import asyncio


class FakeProcess:
    def __init__(
        self,
        *,
        stdout: bytes = b"Fake employee reply\n",
        stderr: bytes = b"\nsession_id: fake-session-1\n",
        returncode: int = 0,
        wait_forever: bool = False,
        pid: int = 4321,
    ) -> None:
        self._stdout = stdout
        self._stderr = stderr
        self.returncode = None if wait_forever else returncode
        self.wait_forever = wait_forever
        self.pid = pid
        self.terminated = False
        self.killed = False
        self._released = asyncio.Event()

    async def communicate(self) -> tuple[bytes, bytes]:
        if self.wait_forever:
            await self._released.wait()
        return self._stdout, self._stderr

    def terminate(self) -> None:
        self.terminated = True
        self.returncode = -15
        self._released.set()

    def kill(self) -> None:
        self.killed = True
        self.returncode = -9
        self._released.set()

    async def wait(self) -> int:
        if self.wait_forever and not (self.terminated or self.killed):
            await self._released.wait()
        return self.returncode
