"""隔离第三方库日志，守住 Worker JSONL stdout。"""

from __future__ import annotations

import contextlib
import io
import os
import sys
from collections.abc import Iterator


@contextlib.contextmanager
def library_output_to_stderr() -> Iterator[None]:
    """把调用期间的 Python/C stdout 都导向 stderr，结束后恢复协议通道。"""
    try:
        stdout_fd = sys.stdout.fileno()
        stderr_fd = sys.stderr.fileno()
    except (AttributeError, io.UnsupportedOperation):
        with contextlib.redirect_stdout(sys.stderr):
            yield
        return

    saved_stdout = os.dup(stdout_fd)
    try:
        sys.stdout.flush()
        sys.stderr.flush()
        os.dup2(stderr_fd, stdout_fd)
        yield
    finally:
        sys.stdout.flush()
        os.dup2(saved_stdout, stdout_fd)
        os.close(saved_stdout)
