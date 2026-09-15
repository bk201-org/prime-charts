from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from .helpers import (
    REPOSITORY_ROOT,
    create_chart,
    output_block,
    run_command,
    run_module,
)


class FindChartsTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary_directory.cleanup)
        self.root = Path(self.temporary_directory.name)
        self.repository = self.root / "repo"
        self.repository.mkdir()
        self.bin_directory = self.root / "bin"
        self.bin_directory.mkdir()

        self.real_ct = shutil.which("ct")
        self.real_helm = shutil.which("helm")
        if not self.real_ct or not self.real_helm:
            self.fail("The find-charts tests require ct and helm on PATH.")
        fake_ct = (
            REPOSITORY_ROOT
            / "scripts/ci/chart_pipeline/tests/fakes/ct"
        )
        destination = self.bin_directory / "ct"
        shutil.copy2(fake_ct, destination)
        destination.chmod(0o755)

        run_command(["git", "init", "-q", "-b", "main"], cwd=self.repository)
        run_command(
            ["git", "config", "user.name", "CI Tests"],
            cwd=self.repository,
        )
        run_command(
            ["git", "config", "user.email", "ci-tests@example.com"],
            cwd=self.repository,
        )
        create_chart(self.repository / "charts", "alpha")
        create_chart(self.repository / "charts", "beta")
        tests_directory = self.repository / "tests"
        tests_directory.mkdir()
        (tests_directory / "ct.yaml").write_text(
            "chart-dirs:\n  - charts\n",
            encoding="utf-8",
        )
        (tests_directory / "shared.yaml").write_text(
            "shared: original\n",
            encoding="utf-8",
        )
        run_command(["git", "add", "."], cwd=self.repository)
        run_command(
            ["git", "-c", "commit.gpgsign=false", "commit", "-qm", "Initial"],
            cwd=self.repository,
        )
        self.initial_sha = self.revision()
        self.output_counter = 0

    def revision(self) -> str:
        return run_command(["git", "rev-parse", "HEAD"], cwd=self.repository)

    def commit(self, relative_path: str, message: str) -> str:
        run_command(["git", "add", relative_path], cwd=self.repository)
        run_command(
            ["git", "-c", "commit.gpgsign=false", "commit", "-qm", message],
            cwd=self.repository,
        )
        return self.revision()

    def run_find(
        self,
        *,
        event: str,
        before: str,
        branch: str,
        head: str,
        overwrite: str = "",
        extra_environment: dict[str, str] | None = None,
    ) -> tuple[subprocess.CompletedProcess[str], str]:
        self.output_counter += 1
        output_file = self.root / f"github-output-{self.output_counter}"
        output_file.touch()
        environment = {
            "PATH": f"{self.bin_directory}{os.pathsep}{os.environ['PATH']}",
            "REAL_CT": self.real_ct or "",
            "EVENT_NAME": event,
            "BEFORE_SHA": before,
            "PR_BASE_SHA": before,
            "BASE_REF": branch if event == "pull_request" else "",
            "REF_NAME": branch,
            "OVERWRITE_EXISTING": overwrite,
            "GITHUB_SHA": head,
            "GITHUB_OUTPUT": str(output_file),
            "VERBOSE": "false",
            "FAKE_CT_STDERR_MESSAGE": "",
            "FAKE_CT_STDOUT_NOISE": "",
        }
        if extra_environment:
            environment.update(extra_environment)
        result = run_module(
            "scripts.ci.chart_pipeline.find_charts",
            cwd=self.repository,
            environment=environment,
        )
        return result, output_file.read_text(encoding="utf-8")

    def test_manual_overwrite_selects_exact_chart(self) -> None:
        result, output = self.run_find(
            event="workflow_dispatch",
            before="",
            branch="v1.9",
            head=self.initial_sha,
            overwrite="alpha@1.0.0",
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, "")
        self.assertEqual(
            output_block(output, "charts<<CHARTS_EOF", "CHARTS_EOF"),
            "charts/alpha",
        )
        self.assertEqual(
            output_block(
                output,
                "publish-charts<<PUBLISH_CHARTS_EOF",
                "PUBLISH_CHARTS_EOF",
            ),
            "charts/alpha",
        )

    def test_manual_overwrite_validation(self) -> None:
        result, _ = self.run_find(
            event="workflow_dispatch",
            before="",
            branch="main",
            head=self.initial_sha,
            overwrite="alpha@1.0.0",
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("only be overwritten from a vX.Y branch", result.stderr)

        result, _ = self.run_find(
            event="workflow_dispatch",
            before="",
            branch="v1.9",
            head=self.initial_sha,
            overwrite="alpha@2.0.0",
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("the chart is alpha@1.0.0", result.stderr)

    def test_changed_chart_and_output_channels(self) -> None:
        values = self.repository / "charts/alpha/values.yaml"
        values.write_text("value: changed\n", encoding="utf-8")
        chart_sha = self.commit("charts/alpha/values.yaml", "Change alpha")
        result, output = self.run_find(
            event="pull_request",
            before=self.initial_sha,
            branch="v1.9",
            head=chart_sha,
            extra_environment={
                "VERBOSE": "true",
                "FAKE_CT_STDERR_MESSAGE": "ct diagnostic message",
            },
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, "")
        self.assertIn("ct diagnostic message", result.stderr)
        self.assertIn("DEBUG: Chart comparison", result.stderr)
        self.assertEqual(
            output_block(output, "charts<<CHARTS_EOF", "CHARTS_EOF"),
            "charts/alpha",
        )
        temporary_refs = run_command(
            [
                "git",
                "for-each-ref",
                "--format=%(refname)",
                "refs/remotes/ct-event-base",
            ],
            cwd=self.repository,
        )
        self.assertEqual(temporary_refs, "")

    def test_unexpected_ct_stdout_is_rejected(self) -> None:
        values = self.repository / "charts/alpha/values.yaml"
        values.write_text("value: changed\n", encoding="utf-8")
        chart_sha = self.commit("charts/alpha/values.yaml", "Change alpha")
        result, _ = self.run_find(
            event="push",
            before=self.initial_sha,
            branch="v1.9",
            head=chart_sha,
            extra_environment={"FAKE_CT_STDOUT_NOISE": "unexpected ct output"},
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn(
            "Unexpected output from ct list-changed: unexpected ct output",
            result.stderr,
        )

    def test_shared_change_tests_all_without_publishing(self) -> None:
        shared = self.repository / "tests/shared.yaml"
        shared.write_text("shared: changed\n", encoding="utf-8")
        shared_sha = self.commit("tests/shared.yaml", "Change shared tests")
        result, output = self.run_find(
            event="push",
            before=self.initial_sha,
            branch="v1.9",
            head=shared_sha,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(
            output_block(output, "charts<<CHARTS_EOF", "CHARTS_EOF"),
            "charts/alpha\ncharts/beta",
        )
        self.assertEqual(
            output_block(
                output,
                "publish-charts<<PUBLISH_CHARTS_EOF",
                "PUBLISH_CHARTS_EOF",
            ),
            "",
        )
        self.assertIn("has-publish-charts=false", output)

    def test_manual_and_missing_base_select_all(self) -> None:
        result, output = self.run_find(
            event="workflow_dispatch",
            before="",
            branch="v1.9",
            head=self.initial_sha,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(
            output_block(output, "charts<<CHARTS_EOF", "CHARTS_EOF"),
            "charts/alpha\ncharts/beta",
        )

        result, output = self.run_find(
            event="push",
            before="deadbeef",
            branch="v1.9",
            head=self.initial_sha,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("Base commit deadbeef is unavailable", result.stderr)
        self.assertIn("has-publish-charts=true", output)


if __name__ == "__main__":
    unittest.main()
