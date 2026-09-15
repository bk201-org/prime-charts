from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from .helpers import REPOSITORY_ROOT, run_module


class PublishChartsTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary_directory.cleanup)
        self.root = Path(self.temporary_directory.name)
        self.bin_directory = self.root / "bin"
        self.bin_directory.mkdir()
        self.registry = self.root / "registry"
        self.registry.mkdir()
        self.real_helm = shutil.which("helm")
        if not self.real_helm:
            self.fail("The publish-charts tests require helm on PATH.")

        fake_helm = (
            REPOSITORY_ROOT
            / "scripts/ci/chart_pipeline/tests/fakes/helm"
        )
        destination = self.bin_directory / "helm"
        shutil.copy2(fake_helm, destination)
        destination.chmod(0o755)

        fixture = (
            REPOSITORY_ROOT
            / "scripts/ci/chart_pipeline/tests/fixtures/example"
        )
        self.chart = self.root / "example"
        shutil.copytree(fixture, self.chart)

    def run_publish(
        self,
        *,
        registry: Path | None = None,
        charts: list[Path] | None = None,
        extra_environment: dict[str, str] | None = None,
    ) -> subprocess.CompletedProcess[str]:
        environment = {
            "PATH": f"{self.bin_directory}{os.pathsep}{os.environ['PATH']}",
            "REAL_HELM": self.real_helm or "",
            "FAKE_REGISTRY": str(registry or self.registry),
            "FAKE_HELM_PUSH_MODE": "copy",
            "FAKE_HELM_STDERR_MESSAGE": "",
            "FAKE_HELM_SHOW_STDOUT_NOISE": "",
            "FAKE_HELM_PULL_ERROR": "",
            "FAKE_HELM_POST_PUSH_PULL_FAILURES": "0",
            "REPOSITORY": "example/prime-charts",
            "REGISTRY": "registry.test",
            "VERIFY_ATTEMPTS": "1",
            "VERIFY_DELAY_SECONDS": "0",
            "VERBOSE": "false",
            "CHARTS": "\n".join(str(chart) for chart in (charts or [self.chart])),
        }
        if extra_environment:
            environment.update(extra_environment)
        return run_module(
            "scripts.ci.chart_pipeline.publish_charts",
            cwd=REPOSITORY_ROOT,
            environment=environment,
        )

    def published_archive(
        self,
        name: str = "example",
        registry: Path | None = None,
    ) -> Path:
        return (registry or self.registry) / name / "1.0.0.tgz"

    def test_publish_and_verify(self) -> None:
        result = self.run_publish()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, "")
        self.assertIn(
            "Pushed: oci://registry.test/example/prime-charts/example:1.0.0",
            result.stderr,
        )
        self.assertIn(
            "Verified oci://registry.test/example/prime-charts/example:1.0.0",
            result.stderr,
        )
        self.assertTrue(self.published_archive().is_file())

    def test_republishes_identical_content(self) -> None:
        self.assertEqual(self.run_publish().returncode, 0)

        result = self.run_publish()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn(
            "Pushed: oci://registry.test/example/prime-charts/example:1.0.0",
            result.stderr,
        )

    def test_failed_verification_is_rejected(self) -> None:
        self.assertEqual(self.run_publish().returncode, 0)
        template = self.chart / "templates/configmap.yaml"
        template.write_text(
            template.read_text(encoding="utf-8") + "# verification failure\n",
            encoding="utf-8",
        )
        result = self.run_publish(
            extra_environment={"FAKE_HELM_PUSH_MODE": "noop"},
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("does not match the local chart", result.stderr)

    def test_logging_does_not_pollute_stdout(self) -> None:
        result = self.run_publish(
            extra_environment={
                "VERBOSE": "true",
                "FAKE_HELM_STDERR_MESSAGE": "helm diagnostic message",
                "FAKE_HELM_SHOW_STDOUT_NOISE": "helm informational output",
            }
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, "")
        self.assertIn("helm diagnostic message", result.stderr)
        self.assertIn("DEBUG: Packaging", result.stderr)
        self.assertTrue(self.published_archive().is_file())

    def test_verification_retries(self) -> None:
        result = self.run_publish(
            extra_environment={
                "VERIFY_ATTEMPTS": "2",
                "FAKE_HELM_POST_PUSH_PULL_FAILURES": "1",
                "VERBOSE": "true",
            }
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("Verification attempt 2 of 2", result.stderr)

    def test_published_charts_output(self) -> None:
        output_file = self.root / "github_output"
        result = self.run_publish(
            extra_environment={"GITHUB_OUTPUT": str(output_file)}
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        match = re.match(
            r"published-charts=(.*)\n", output_file.read_text(encoding="utf-8")
        )
        self.assertIsNotNone(match)
        assert match is not None
        published = json.loads(match.group(1))
        self.assertEqual(
            published,
            [
                {
                    "name": "example",
                    "version": "1.0.0",
                    "chart_ref": "example@1.0.0",
                    "digest": published[0]["digest"],
                }
            ],
        )
        self.assertRegex(published[0]["digest"], r"^sha256:[0-9a-f]{64}$")


if __name__ == "__main__":
    unittest.main()
