"""A shared deadline and a supervised, output-bounded subprocess helper."""

import os
import selectors
import signal
import subprocess
import time
from collections.abc import Sequence
from pathlib import Path

from .entry import DeadlineExceeded
from .storage import ProjectFiles


class Runtime:
    def __init__(self, root: Path, deadline: float) -> None:
        self.files = ProjectFiles(root)
        self.deadline = deadline

    def remaining(self) -> float:
        remaining = self.deadline - time.monotonic()
        if remaining <= 0:
            raise DeadlineExceeded()
        return remaining

    def run(
        self,
        command: Sequence[str],
        *,
        timeout: float | None = None,
        output_limit: int = 65_536,
    ) -> bytes:
        """Run an explicit argument list, killing its process group on every exit."""
        if isinstance(command, (str, bytes)) or not command:
            raise ValueError("Expected a command argument list")
        if output_limit < 0 or (timeout is not None and timeout <= 0):
            raise ValueError("Invalid subprocess bounds")
        end = time.monotonic() + min(self.remaining(), timeout or self.remaining())
        child = subprocess.Popen(
            command,
            cwd=self.files.root,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
        try:
            assert child.stdout is not None
            output = bytearray()
            with selectors.DefaultSelector() as selector:
                selector.register(child.stdout, selectors.EVENT_READ)
                while selector.get_map():
                    remaining = min(self.remaining(), end - time.monotonic())
                    if remaining <= 0:
                        raise DeadlineExceeded()
                    for key, _ in selector.select(remaining):
                        chunk = os.read(key.fd, min(8192, output_limit - len(output) + 1))
                        if not chunk:
                            selector.unregister(key.fileobj)
                        output.extend(chunk)
                        if len(output) > output_limit:
                            raise ValueError("Subprocess output exceeds the size limit")
            remaining = min(self.remaining(), end - time.monotonic())
            if remaining <= 0:
                raise DeadlineExceeded()
            if child.wait(timeout=remaining) != 0:
                raise RuntimeError("Subprocess failed")
            return bytes(output)
        finally:
            try:
                os.killpg(child.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            child.wait()
            if child.stdout is not None:
                child.stdout.close()
