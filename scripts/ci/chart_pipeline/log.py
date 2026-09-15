from __future__ import annotations

import os
import sys


def _write(message: str) -> None:
    print(message, file=sys.stderr, flush=True)


def info(message: str) -> None:
    _write(f"INFO: {message}")


def debug(message: str) -> None:
    if os.environ.get("VERBOSE", "false").lower() == "true":
        _write(f"DEBUG: {message}")


def tool_output(output: str) -> None:
    if output:
        sys.stderr.write(output)
        if not output.endswith("\n"):
            sys.stderr.write("\n")
        sys.stderr.flush()


def error(message: str, *, file: str | None = None) -> None:
    if file:
        _write(f"::error file={file}::{message}")
    else:
        _write(f"::error::{message}")


def warning(message: str) -> None:
    _write(f"::warning::{message}")
