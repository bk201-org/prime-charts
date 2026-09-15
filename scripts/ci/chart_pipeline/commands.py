from __future__ import annotations

import shlex
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence

from . import log


@dataclass(frozen=True)
class CommandResult:
    args: tuple[str, ...]
    returncode: int
    stdout: str
    stderr: str


class CommandError(RuntimeError):
    def __init__(self, result: CommandResult):
        self.result = result
        command = shlex.join(result.args)
        super().__init__(
            f"Command failed with exit code {result.returncode}: {command}"
        )


class CommandRunner:
    def run(
        self,
        args: Sequence[str | Path],
        *,
        cwd: Path | None = None,
        check: bool = True,
        stdout_is_data: bool = False,
        quiet: bool = False,
    ) -> CommandResult:
        command = tuple(str(arg) for arg in args)
        log.debug(f"Running: {shlex.join(command)}")
        completed = subprocess.run(
            command,
            cwd=cwd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            check=False,
        )
        result = CommandResult(
            args=command,
            returncode=completed.returncode,
            stdout=completed.stdout,
            stderr=completed.stderr,
        )

        if not quiet:
            if not stdout_is_data:
                log.tool_output(result.stdout)
            log.tool_output(result.stderr)

        if check and result.returncode != 0:
            raise CommandError(result)
        return result
