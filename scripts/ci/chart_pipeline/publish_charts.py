from __future__ import annotations

import hashlib
import json
import os
import re
import sys
import tarfile
import time
from dataclasses import dataclass
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Mapping

from . import log
from .commands import CommandError, CommandResult, CommandRunner
from .common import PipelineError, chart_metadata


PUSH_DIGEST = re.compile(r"Digest:\s*(sha256:[0-9a-f]{64})")


@dataclass(frozen=True)
class PreparedChart:
    path: Path
    name: str
    version: str
    archive: Path
    contents: Path


def unpack_chart(archive: Path, destination: Path) -> None:
    destination.mkdir(parents=True, exist_ok=True)
    with tarfile.open(archive, "r:gz") as package:
        package.extractall(destination, filter="data")


def directory_snapshot(root: Path) -> dict[str, tuple[str, str]]:
    snapshot: dict[str, tuple[str, str]] = {}
    for path in sorted(root.rglob("*")):
        relative = path.relative_to(root).as_posix()
        if path.is_symlink():
            snapshot[relative] = ("symlink", os.readlink(path))
        elif path.is_dir():
            snapshot[relative] = ("directory", "")
        elif path.is_file():
            digest = hashlib.sha256(path.read_bytes()).hexdigest()
            snapshot[relative] = ("file", digest)
        else:
            snapshot[relative] = ("other", "")
    return snapshot


def contents_match(local_contents: Path, remote_contents: Path) -> bool:
    local = directory_snapshot(local_contents)
    remote = directory_snapshot(remote_contents)
    if local == remote:
        return True

    if os.environ.get("VERBOSE", "false").lower() == "true":
        for path in sorted(local.keys() | remote.keys()):
            if local.get(path) != remote.get(path):
                log.debug(f"Chart content differs at {path}.")
    return False


def find_chart_archive(directory: Path) -> Path | None:
    return next(iter(sorted(directory.glob("*.tgz"))), None)


def show_result(result: CommandResult) -> None:
    log.tool_output(result.stdout)
    log.tool_output(result.stderr)


class Publisher:
    def __init__(
        self,
        *,
        root: Path,
        runner: CommandRunner,
        oci_base: str,
        package_directory: Path,
        comparison_directory: Path,
        verify_attempts: int,
        verify_delay_seconds: int,
    ) -> None:
        self.root = root
        self.runner = runner
        self.oci_base = oci_base
        self.package_directory = package_directory
        self.comparison_directory = comparison_directory
        self.verify_attempts = verify_attempts
        self.verify_delay_seconds = verify_delay_seconds

    def pull(
        self,
        name: str,
        version: str,
        destination: Path,
    ) -> CommandResult:
        destination.mkdir(parents=True, exist_ok=True)
        log.debug(
            f"Pulling {self.oci_base}/{name}:{version} into {destination}."
        )
        result = self.runner.run(
            [
                "helm",
                "pull",
                f"{self.oci_base}/{name}",
                "--version",
                version,
                "--destination",
                destination,
            ],
            cwd=self.root,
            check=False,
            quiet=True,
        )
        show_result(result)
        return result

    def prepare_chart(self, chart: Path) -> PreparedChart:
        metadata = chart_metadata(chart, self.runner, self.root)
        name = metadata["name"]
        version = metadata["version"]
        if chart.name != name:
            raise PipelineError(
                "Chart directory and chart name must match.",
                file=f"{chart}/Chart.yaml",
            )

        log.info(f"Preparing {name}@{version}.")
        log.debug(f"Packaging {chart} into {self.package_directory}.")
        self.runner.run(
            [
                "helm",
                "package",
                chart,
                "--destination",
                self.package_directory,
            ],
            cwd=self.root,
        )
        archive = self.package_directory / f"{name}-{version}.tgz"
        if not archive.is_file():
            raise PipelineError(f"Helm did not create the expected archive {archive}.")

        local_contents = (
            self.comparison_directory / f"{name}-{version}-local-contents"
        )
        unpack_chart(archive, local_contents)
        return PreparedChart(
            path=chart,
            name=name,
            version=version,
            archive=archive,
            contents=local_contents,
        )

    def verify(self, chart: PreparedChart) -> None:
        log.info(f"Verifying {chart.name}@{chart.version} after publication.")
        for attempt in range(1, self.verify_attempts + 1):
            with TemporaryDirectory(
                prefix=f"{chart.name}-{chart.version}-verification-",
                dir=self.comparison_directory,
            ) as verification_directory_name:
                verification_directory = Path(verification_directory_name)
                log.debug(
                    f"Verification attempt {attempt} of {self.verify_attempts} for "
                    f"{chart.name}@{chart.version}."
                )
                result = self.pull(
                    chart.name,
                    chart.version,
                    verification_directory,
                )
                if result.returncode == 0:
                    verification_archive = find_chart_archive(verification_directory)
                    if verification_archive is not None:
                        verification_contents = verification_directory / "contents"
                        unpack_chart(verification_archive, verification_contents)
                        if contents_match(chart.contents, verification_contents):
                            log.info(
                                f"Verified {self.oci_base}/{chart.name}:"
                                f"{chart.version}."
                            )
                            return
            if attempt < self.verify_attempts:
                time.sleep(attempt * self.verify_delay_seconds)

        raise PipelineError(
            f"Published {chart.name}:{chart.version} does not match the local chart."
        )

    def publish(self, charts: list[PreparedChart]) -> list[dict[str, str]]:
        published: list[dict[str, str]] = []
        for chart in charts:
            log.info(f"Pushing {chart.name}@{chart.version}.")
            result = self.runner.run(
                ["helm", "push", chart.archive, self.oci_base],
                cwd=self.root,
            )
            match = PUSH_DIGEST.search(f"{result.stdout}\n{result.stderr}")
            if match is None:
                raise PipelineError(
                    f"Could not determine the digest helm pushed for "
                    f"{chart.name}@{chart.version}."
                )
            published.append(
                {
                    "name": chart.name,
                    "version": chart.version,
                    "chart_ref": f"{chart.name}@{chart.version}",
                    "digest": match.group(1),
                }
            )
            self.verify(chart)
        return published


def write_github_output(
    environment: Mapping[str, str], published: list[dict[str, str]]
) -> None:
    output_path = environment.get("GITHUB_OUTPUT")
    if not output_path:
        return
    payload = json.dumps(published, separators=(",", ":"))
    with open(output_path, "a", encoding="utf-8") as handle:
        handle.write(f"published-charts={payload}\n")


def positive_integer(environment: Mapping[str, str], name: str, default: int) -> int:
    try:
        value = int(environment.get(name, str(default)))
    except ValueError as error:
        raise PipelineError(f"{name} must be an integer.") from error
    if value < 0 or (name == "VERIFY_ATTEMPTS" and value == 0):
        raise PipelineError(f"{name} must be positive.")
    return value


def run(environment: Mapping[str, str], root: Path) -> None:
    repository = environment.get("REPOSITORY", "").lower()
    registry = environment.get("REGISTRY", "")
    if not repository or not registry:
        raise PipelineError("REPOSITORY and REGISTRY must be set.")

    chart_paths = [
        Path(value)
        for value in environment.get("CHARTS", "").splitlines()
        if value
    ]
    oci_base = f"oci://{registry}/{repository}"
    verify_attempts = positive_integer(environment, "VERIFY_ATTEMPTS", 5)
    verify_delay_seconds = positive_integer(environment, "VERIFY_DELAY_SECONDS", 1)

    log.info(f"Preparing {len(chart_paths)} chart(s) for {oci_base}.")
    with (
        TemporaryDirectory() as package_directory_name,
        TemporaryDirectory() as comparison_directory_name,
    ):
        package_directory = Path(package_directory_name)
        comparison_directory = Path(comparison_directory_name)
        log.debug(f"Package directory: {package_directory}.")
        log.debug(f"Comparison directory: {comparison_directory}.")
        publisher = Publisher(
            root=root,
            runner=CommandRunner(),
            oci_base=oci_base,
            package_directory=package_directory,
            comparison_directory=comparison_directory,
            verify_attempts=verify_attempts,
            verify_delay_seconds=verify_delay_seconds,
        )
        prepared = [publisher.prepare_chart(chart) for chart in chart_paths]
        published = publisher.publish(prepared)
    write_github_output(environment, published)
    log.info("Chart publication completed successfully.")


def main() -> int:
    try:
        run(os.environ, Path.cwd())
    except PipelineError as error:
        log.error(str(error), file=error.file)
        return 1
    except (CommandError, OSError, tarfile.TarError) as error:
        log.error(str(error))
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
