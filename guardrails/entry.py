"""POSIX command boundary: silence failures and commit one bounded JSON line."""

import os
import signal
import sys
import time


class DeadlineExceeded(BaseException):
    """Escape application exception handlers when the wall-clock budget expires."""


def _expire(signum: int, frame: object) -> None:
    raise DeadlineExceeded()


def main() -> None:
    started = time.monotonic()
    output_fd = -1
    timer_armed = False
    try:
        # File-descriptor redirection also contains accidental prints in extensions.
        output_fd = os.dup(1)
        null_fd = os.open(os.devnull, os.O_WRONLY)
        try:
            os.dup2(null_fd, 1)
            os.dup2(null_fd, 2)
        finally:
            os.close(null_fd)
        seconds = float(os.environ.get("GUARDRAILS_TIMEOUT_SECONDS", "1.2"))
        if not 0.01 <= seconds <= 60.0:
            return
        deadline = started + seconds
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return
        signal.signal(signal.SIGALRM, _expire)
        signal.setitimer(signal.ITIMER_REAL, remaining)
        timer_armed = True

        from .runner import execute

        payload = execute(sys.argv[1:], deadline, started)
        if payload is not None and time.monotonic() < deadline:
            # A nonblocking pipe write no larger than PIPE_BUF is all-or-nothing.
            limit = min(4096, os.fpathconf(output_fd, "PC_PIPE_BUF"))
            if len(payload) <= limit:
                os.set_blocking(output_fd, False)
                os.write(output_fd, payload)
    except BaseException:
        pass
    finally:
        if timer_armed:
            signal.setitimer(signal.ITIMER_REAL, 0)
        if output_fd >= 0:
            os.close(output_fd)
