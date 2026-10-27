"""Fixed audit metadata only: no event fields, file contents or exception text."""

import json
import os
import stat
import time
from collections.abc import Mapping
from typing import Any

from .runtime import Runtime
from .validation import integer


def record(
    runtime: Runtime,
    config: Mapping[str, Any],
    hook: str,
    outcome: str,
    started: float,
) -> None:
    name = config.get("path", ".guardrails/audit.jsonl")
    maximum = integer(config.get("max_bytes", 65_536), 256, 5_000_000)
    backups = integer(config.get("backups", 1), 1, 5)
    row = {
        "timestamp": round(time.time(), 3),
        "hook": hook,
        "outcome": outcome,
        "duration_ms": round((time.monotonic() - started) * 1000, 2),
    }
    data = (json.dumps(row, allow_nan=False) + "\n").encode("utf-8")
    if len(data) > maximum:
        raise ValueError("Audit record exceeds the size limit")
    paths = [runtime.files.path(name + (f".{i}" if i else "")) for i in range(6)]
    with runtime.files.lock(name + ".lock"):
        for path in paths:
            if path.is_symlink() or (path.exists() and not stat.S_ISREG(path.stat().st_mode)):
                raise ValueError("Expected regular audit files")
        for index, path in enumerate(paths):
            if path.exists() and (index > backups or path.stat().st_size > maximum):
                path.unlink()
        paths = paths[:backups + 1]
        if paths[0].exists() and paths[0].stat().st_size + len(data) > maximum:
            paths[-1].unlink(missing_ok=True)
            for index in range(backups - 1, -1, -1):
                source = paths[index]
                if source.exists():
                    if source.stat().st_size > maximum:
                        source.unlink()
                    else:
                        os.replace(source, paths[index + 1])
        fd = os.open(paths[0], os.O_CREAT | os.O_APPEND | os.O_WRONLY | os.O_NONBLOCK | os.O_NOFOLLOW, 0o600)
        try:
            if not stat.S_ISREG(os.fstat(fd).st_mode):
                raise ValueError("Expected a regular audit file")
            if os.write(fd, data) != len(data):
                raise OSError("Incomplete audit write")
        finally:
            os.close(fd)
