"""Cross-tree consistency tests for the single-sourced package version.

The package version lives in exactly one place — ``kb_tools.__version__`` —
and every argparse entry point's ``--version`` action must report it via the
uniform ``<prog> (kb_tools <version>)`` format. These tests invoke each entry
point as a subprocess (``sys.executable -m kb_tools.<module> --version``) and
pin the reported version to the package attribute, so a stale hardcoded
version string in any CLI fails loudly here.
"""

import os
import re
import subprocess
import sys
import unittest
from pathlib import Path

from kb_tools import __version__

_THIS_DIR = Path(__file__).resolve().parent
# The directory containing the ``kb_tools`` package — used only to point the
# subprocess PYTHONPATH at the package, never to derive a consumer repo root.
_PKG_PARENT = _THIS_DIR.parent.parent

# Every argparse entry point in the package (module form, runnable via -m).
ENTRY_POINTS = (
    "kb_tools.kb_util",
    "kb_tools.kb_cmd",
    "kb_tools.refresh_kb_metadata",
    "kb_tools.verify_kb_metadata",
    "kb_tools.verify_md_links",
)


def _run_version(module: str) -> subprocess.CompletedProcess:
    env = {**os.environ, "PYTHONPATH": str(_PKG_PARENT), "PYTHONDONTWRITEBYTECODE": "1"}
    return subprocess.run(
        [sys.executable, "-m", module, "--version"],
        capture_output=True,
        text=True,
        env=env,
        check=False,
    )


class TestVersionSingleSourced(unittest.TestCase):
    """--version output is pinned to ``kb_tools.__version__`` everywhere."""

    def test_version_is_semver(self):
        self.assertRegex(__version__, r"^\d+\.\d+\.\d+$")

    def test_every_entry_point_reports_package_version(self):
        for module in ENTRY_POINTS:
            with self.subTest(module=module):
                result = _run_version(module)
                self.assertEqual(result.returncode, 0, msg=result.stderr)
                out = result.stdout.strip()
                # Uniform format: "<prog> (kb_tools <version>)". The prog part
                # varies by entry point (argparse derives it); the version part
                # must be exactly the package attribute — no drift possible.
                self.assertTrue(
                    out.endswith(f"(kb_tools {__version__})"),
                    msg=f"unexpected --version output: {out!r}",
                )
                self.assertEqual(re.findall(r"\d+\.\d+\.\d+", out), [__version__], msg=out)


if __name__ == "__main__":
    unittest.main()
