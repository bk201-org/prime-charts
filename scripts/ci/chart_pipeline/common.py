from __future__ import annotations

from pathlib import Path

from .commands import CommandRunner


class PipelineError(RuntimeError):
    def __init__(self, message: str, *, file: str | None = None):
        self.file = file
        super().__init__(message)


def chart_metadata(
    chart: Path,
    runner: CommandRunner,
    root: Path,
) -> dict[str, str]:
    result = runner.run(
        ["helm", "show", "chart", chart],
        cwd=root,
        stdout_is_data=True,
    )
    fields: dict[str, list[str]] = {"name": [], "version": []}
    for line in result.stdout.splitlines():
        if line.startswith("name:") or line.startswith("version:"):
            key, value = line.split(":", 1)
            fields[key].append(value.strip().strip("'\""))

    metadata: dict[str, str] = {}
    for key, values in fields.items():
        if len(values) != 1 or not values[0]:
            raise PipelineError(
                f"Unable to read chart {key} from {chart}/Chart.yaml.",
                file=f"{chart}/Chart.yaml",
            )
        metadata[key] = values[0]
    return metadata
