"""Bounded regular-file access and project-local atomic state updates."""

import fcntl
import json
import os
import stat
import tempfile
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

FILE_LIMIT = 65_536


def _invalid_constant(value: str) -> None:
    raise ValueError("Non-finite JSON number")


def decode_json(data: bytes) -> Any:
    return json.loads(data.decode("utf-8"), parse_constant=_invalid_constant)


def read_bytes(path: Path, limit: int = FILE_LIMIT) -> bytes:
    fd = os.open(path, os.O_RDONLY | os.O_NONBLOCK | os.O_NOFOLLOW)
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            raise ValueError("Expected a regular file")
        with os.fdopen(fd, "rb", closefd=False) as stream:
            data = stream.read(limit + 1)
        if len(data) > limit:
            raise ValueError("File exceeds the size limit")
        return data
    finally:
        os.close(fd)


class ProjectFiles:
    def __init__(self, root: Path) -> None:
        self.root = root.resolve(strict=True)
        if not self.root.is_dir():
            raise ValueError("Expected a project directory")

    def path(self, name: str) -> Path:
        if not isinstance(name, str) or not name or Path(name).is_absolute():
            raise ValueError("Expected a relative project path")
        path = self.root / name
        if not path.resolve().is_relative_to(self.root):
            raise ValueError("Path leaves the project")
        return path

    def read_text(self, name: str) -> str:
        return read_bytes(self.path(name)).decode("utf-8")

    def read_json(self, name: str) -> Any:
        return decode_json(read_bytes(self.path(name)))

    def write_json(self, name: str, value: Any) -> None:
        data = json.dumps(value, ensure_ascii=False, allow_nan=False).encode("utf-8")
        if len(data) > FILE_LIMIT:
            raise ValueError("State exceeds the size limit")
        path = self.path(name)
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, temporary = tempfile.mkstemp(prefix=".pending-", dir=path.parent)
        try:
            with os.fdopen(fd, "wb") as stream:
                stream.write(data)
            self.path(name)
            os.replace(temporary, path)
        finally:
            Path(temporary).unlink(missing_ok=True)

    @contextmanager
    def lock(self, name: str) -> Iterator[None]:
        path = self.path(name)
        path.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(path, os.O_CREAT | os.O_RDWR | os.O_NONBLOCK | os.O_NOFOLLOW, 0o600)
        try:
            if not stat.S_ISREG(os.fstat(fd).st_mode):
                raise ValueError("Expected a regular lock file")
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            yield
        finally:
            os.close(fd)
