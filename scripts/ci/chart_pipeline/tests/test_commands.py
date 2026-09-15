from __future__ import annotations

import contextlib
import io
import os
import sys
import unittest
from unittest import mock

from scripts.ci.chart_pipeline.commands import CommandError, CommandRunner


class CommandRunnerTest(unittest.TestCase):
    def setUp(self) -> None:
        verbose = mock.patch.dict(os.environ, {"VERBOSE": "false"})
        verbose.start()
        self.addCleanup(verbose.stop)

    def test_data_stdout_is_returned_while_stderr_is_logged(self) -> None:
        stderr = io.StringIO()
        with contextlib.redirect_stderr(stderr):
            result = CommandRunner().run(
                [
                    sys.executable,
                    "-c",
                    "import sys; print('data'); print('diagnostic', file=sys.stderr)",
                ],
                stdout_is_data=True,
            )
        self.assertEqual(result.stdout, "data\n")
        self.assertNotIn("data", stderr.getvalue())
        self.assertIn("diagnostic", stderr.getvalue())

    def test_visible_stdout_is_routed_to_stderr(self) -> None:
        stderr = io.StringIO()
        with contextlib.redirect_stderr(stderr):
            CommandRunner().run([sys.executable, "-c", "print('visible')"])
        self.assertEqual(stderr.getvalue(), "visible\n")

    def test_failure_contains_exit_status(self) -> None:
        with self.assertRaisesRegex(CommandError, "exit code 7"):
            CommandRunner().run([sys.executable, "-c", "raise SystemExit(7)"])


if __name__ == "__main__":
    unittest.main()
