from __future__ import annotations

import os
import re
import sys
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator, Mapping

from . import log
from .commands import CommandError, CommandRunner
from .common import PipelineError, chart_metadata


STABLE_BRANCH = re.compile(r"v[0-9]+\.[0-9]+")
OVERWRITE_REQUEST = re.compile(r"([a-z0-9][a-z0-9._-]*)@([^/@\s]+)")
CHART_PATH = re.compile(r"charts/[^/]+")
SHARED_PATHS = ("tests", ".github/workflows/ci.yaml", "scripts/ci")


@dataclass(frozen=True)
class EventContext:
    event_name: str
    branch: str
    head_sha: str
    before_sha: str
    pull_request_base_sha: str
    overwrite_existing: str
    run_id: str

    @classmethod
    def from_environment(cls, environment: Mapping[str, str]) -> "EventContext":
        branch = environment.get("BASE_REF") or environment.get("REF_NAME", "")
        return cls(
            event_name=environment.get("EVENT_NAME", ""),
            branch=branch,
            head_sha=environment.get("GITHUB_SHA", ""),
            before_sha=environment.get("BEFORE_SHA", ""),
            pull_request_base_sha=environment.get("PR_BASE_SHA", ""),
            overwrite_existing=environment.get("OVERWRITE_EXISTING", ""),
            run_id=environment.get("GITHUB_RUN_ID", "local"),
        )


@dataclass
class ChartSelection:
    test_charts: list[Path]
    publish_charts: list[Path]
    stable_branch: bool
    base_sha: str = ""
    test_all: bool = False


def chart_paths(root: Path) -> list[Path]:
    return sorted(
        chart_file.parent.relative_to(root)
        for chart_file in (root / "charts").glob("*/Chart.yaml")
    )


@contextmanager
def temporary_remote_ref(
    runner: CommandRunner,
    root: Path,
    reference: str,
    revision: str,
) -> Iterator[None]:
    runner.run(["git", "update-ref", reference, revision], cwd=root, quiet=True)
    try:
        yield
    finally:
        result = runner.run(
            ["git", "update-ref", "-d", reference],
            cwd=root,
            check=False,
            quiet=True,
        )
        if result.returncode != 0:
            log.warning(f"Unable to remove temporary Git reference {reference}.")


def changed_chart_paths(
    root: Path,
    runner: CommandRunner,
    base_sha: str,
    head_sha: str,
    run_id: str,
) -> list[Path]:
    temporary_remote = "ct-event-base"
    temporary_branch = f"run-{run_id}-{os.getpid()}"
    temporary_ref = f"refs/remotes/{temporary_remote}/{temporary_branch}"

    log.info("Discovering changed charts with chart-testing.")
    log.debug(f"Chart comparison base={base_sha} head={head_sha}.")
    log.debug(f"Creating temporary Git reference {temporary_ref}.")

    with temporary_remote_ref(runner, root, temporary_ref, base_sha):
        try:
            result = runner.run(
                [
                    "ct",
                    "list-changed",
                    "--config",
                    "tests/ct.yaml",
                    "--remote",
                    temporary_remote,
                    "--target-branch",
                    temporary_branch,
                    "--since",
                    head_sha,
                ],
                cwd=root,
                stdout_is_data=True,
            )
        except CommandError as error:
            log.tool_output(error.result.stdout)
            raise

    log.debug(f"Removed temporary Git reference {temporary_ref}.")
    charts: set[Path] = set()
    for value in result.stdout.splitlines():
        if not value:
            continue
        chart = Path(value)
        if not CHART_PATH.fullmatch(value) or not (
            root / chart / "Chart.yaml"
        ).is_file():
            raise PipelineError(f"Unexpected output from ct list-changed: {value}")
        charts.add(chart)
    return sorted(charts)


def revision_exists(root: Path, runner: CommandRunner, revision: str) -> bool:
    result = runner.run(
        ["git", "cat-file", "-e", f"{revision}^{{commit}}"],
        cwd=root,
        check=False,
        quiet=True,
    )
    return result.returncode == 0


def shared_files_changed(
    root: Path,
    runner: CommandRunner,
    base_sha: str,
    head_sha: str,
) -> bool:
    result = runner.run(
        ["git", "diff", "--quiet", base_sha, head_sha, "--", *SHARED_PATHS],
        cwd=root,
        check=False,
        quiet=True,
    )
    if result.returncode not in (0, 1):
        raise CommandError(result)
    return result.returncode == 1


def select_overwrite_chart(
    root: Path,
    runner: CommandRunner,
    request: str,
    stable_branch: bool,
) -> Path:
    match = OVERWRITE_REQUEST.fullmatch(request)
    if not match:
        raise PipelineError(
            "overwrite_existing must use the format chart-name@version."
        )
    if not stable_branch:
        raise PipelineError(
            "Existing charts can only be overwritten from a vX.Y branch."
        )

    chart_name, _ = match.groups()
    chart = Path("charts") / chart_name
    if not (root / chart / "Chart.yaml").is_file():
        raise PipelineError(f"Chart {chart_name} does not exist.")

    metadata = chart_metadata(chart, runner, root)
    actual = f"{metadata['name']}@{metadata['version']}"
    if actual != request:
        raise PipelineError(f"Requested {request}, but the chart is {actual}.")

    log.info(f"Manual overwrite selected {request}.")
    return chart


def select_charts(
    context: EventContext,
    root: Path,
    runner: CommandRunner,
) -> ChartSelection:
    stable = STABLE_BRANCH.fullmatch(context.branch) is not None
    base_sha = ""
    test_all = False
    publish_all = False
    manual_chart: Path | None = None

    log.info(
        f"Selecting charts for {context.event_name} on branch {context.branch}."
    )
    if context.event_name == "pull_request":
        base_sha = context.pull_request_base_sha
        log.debug(f"Using pull request base {base_sha}.")
    elif context.event_name == "push":
        if not context.before_sha or set(context.before_sha) == {"0"}:
            log.info("The previous push commit is unavailable; selecting all charts.")
            test_all = True
            publish_all = True
        else:
            base_sha = context.before_sha
            log.debug(f"Using previous push commit {base_sha}.")
    elif context.event_name == "workflow_dispatch":
        if context.overwrite_existing:
            manual_chart = select_overwrite_chart(
                root,
                runner,
                context.overwrite_existing,
                stable,
            )
        else:
            log.info("Manual workflow selected all charts.")
            test_all = True
            publish_all = True
    else:
        raise PipelineError(f"Unsupported event: {context.event_name}")

    if base_sha and not revision_exists(root, runner, base_sha):
        log.info(f"Base commit {base_sha} is unavailable; selecting all charts.")
        base_sha = ""
        test_all = True
        publish_all = True

    if base_sha and shared_files_changed(root, runner, base_sha, context.head_sha):
        log.info("Shared CI files changed; selecting all charts for testing.")
        test_all = True

    if manual_chart:
        publish_charts = [manual_chart]
    elif publish_all:
        log.debug("Selecting all charts for publication.")
        publish_charts = chart_paths(root)
    else:
        publish_charts = changed_chart_paths(
            root,
            runner,
            base_sha,
            context.head_sha,
            context.run_id,
        )

    if manual_chart:
        test_charts = [manual_chart]
    elif test_all:
        log.debug("Selecting all charts for testing.")
        test_charts = chart_paths(root)
    else:
        test_charts = list(publish_charts)

    return ChartSelection(
        test_charts=test_charts,
        publish_charts=publish_charts,
        stable_branch=stable,
        base_sha=base_sha,
        test_all=test_all,
    )


def write_github_outputs(selection: ChartSelection, output_file: Path) -> None:
    test_values = "\n".join(str(chart) for chart in selection.test_charts)
    publish_values = "\n".join(str(chart) for chart in selection.publish_charts)
    log.info(
        f"Selected {len(selection.test_charts)} chart(s) for testing and "
        f"{len(selection.publish_charts)} chart(s) for publication."
    )
    for chart in selection.test_charts:
        log.debug(f"Test chart: {chart}")
    for chart in selection.publish_charts:
        log.debug(f"Publish chart: {chart}")

    with output_file.open("a", encoding="utf-8") as output:
        output.write("charts<<CHARTS_EOF\n")
        output.write(f"{test_values}\n")
        output.write("CHARTS_EOF\n")
        output.write(f"has-charts={'true' if selection.test_charts else 'false'}\n")
        output.write("publish-charts<<PUBLISH_CHARTS_EOF\n")
        output.write(f"{publish_values}\n")
        output.write("PUBLISH_CHARTS_EOF\n")
        output.write(
            "has-publish-charts="
            f"{'true' if selection.publish_charts else 'false'}\n"
        )
        stable_branch = "true" if selection.stable_branch else "false"
        output.write(f"stable-branch={stable_branch}\n")
        output.write(f"base-sha={selection.base_sha}\n")
        output.write(f"test-all={'true' if selection.test_all else 'false'}\n")


def run(environment: Mapping[str, str], root: Path) -> None:
    context = EventContext.from_environment(environment)
    output_path = environment.get("GITHUB_OUTPUT")
    if not output_path:
        raise PipelineError("GITHUB_OUTPUT is not set.")
    selection = select_charts(context, root, CommandRunner())
    write_github_outputs(selection, Path(output_path))


def main() -> int:
    try:
        run(os.environ, Path.cwd())
    except (PipelineError, CommandError, OSError) as error:
        log.error(str(error), file=getattr(error, "file", None))
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
