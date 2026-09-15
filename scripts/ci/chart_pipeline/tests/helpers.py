from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from typing import Mapping, Sequence


REPOSITORY_ROOT = Path(__file__).resolve().parents[4]


def run_command(args: Sequence[str | Path], *, cwd: Path) -> str:
    result = subprocess.run(
        [str(arg) for arg in args],
        cwd=cwd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        check=True,
    )
    return result.stdout.strip()


def run_module(
    module: str,
    *,
    cwd: Path,
    environment: Mapping[str, str],
) -> subprocess.CompletedProcess[str]:
    process_environment = os.environ.copy()
    process_environment.update(environment)
    python_path = process_environment.get("PYTHONPATH")
    process_environment["PYTHONPATH"] = str(REPOSITORY_ROOT)
    if python_path:
        process_environment["PYTHONPATH"] += os.pathsep + python_path
    return subprocess.run(
        [sys.executable, "-m", module],
        cwd=cwd,
        env=process_environment,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        check=False,
    )


def create_chart(directory: Path, name: str, version: str = "1.0.0") -> Path:
    chart = directory / name
    (chart / "templates").mkdir(parents=True)
    (chart / "Chart.yaml").write_text(
        "\n".join(
            (
                "apiVersion: v2",
                f"name: {name}",
                f"version: {version}",
                "",
            )
        ),
        encoding="utf-8",
    )
    (chart / "values.yaml").write_text("value: original\n", encoding="utf-8")
    (chart / "templates" / "configmap.yaml").write_text(
        "apiVersion: v1\nkind: ConfigMap\nmetadata:\n  name: example\n",
        encoding="utf-8",
    )
    return chart


def output_block(content: str, start: str, end: str) -> str:
    lines = content.splitlines()
    start_index = lines.index(start) + 1
    end_index = lines.index(end, start_index)
    return "\n".join(lines[start_index:end_index])
