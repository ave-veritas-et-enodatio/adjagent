"""Tests for the installed runner fragments themselves — ``runner-snippets/``.

The delivered artifact is a justfile fragment and a Makefile fragment, so these
cases drive the real ``just`` and ``make`` over them, in a scratch consuming
repo whose ``.claude/agents`` symlinks to this repository's agents surface. That
is the shape ``install-targets`` creates and the only one the fragments
document; anything less exercises a copy of the recipe rather than the recipe.

The property under test is ``kb-verify``'s composite contract: all three
verifiers run, and the target carries the worst outcome. It is a gate contract
rather than a formatting one — the driver's validation gate stops the build on
this target's exit code and puts its captured stdout in the run log, so a report
covering one verifier's faults sends whoever reads it after a third of the
problem.
"""

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from kb_tools import kb_util

_AGENTS_SURFACE = Path(__file__).resolve().parent.parent.parent

# Each verifier's mark ON STDOUT. verify_md_links and verify_citations put
# their per-file findings on stdout and only their summary banners on stderr,
# and stdout is what the driver writes to the round's findings file — so these
# are what "one report covers all three" actually means downstream.
_RED_MARKS = ("[broken intra]", "[claim-quality] FAIL", "[referent] x.md:1")

_RUNNERS = [
    pytest.param(
        runner,
        marks=pytest.mark.skipif(shutil.which(runner) is None, reason=f"needs {runner} on PATH"),
    )
    for runner in ("just", "make")
]


def _consumer(root: Path) -> Path:
    """A consuming repo carrying the include line the installer manages."""
    (root / "kb-root").mkdir(parents=True)
    subprocess.run(["git", "init", "-q"], cwd=root, check=True, capture_output=True)
    (root / ".claude").mkdir()
    (root / ".claude" / "agents").symlink_to(_AGENTS_SURFACE, target_is_directory=True)
    (root / "justfile").write_text(f"{kb_util.INSTALL_LINE_JUST}\n", encoding="utf-8")
    (root / "Makefile").write_text(f"{kb_util.INSTALL_LINE_MAKE}\n", encoding="utf-8")
    return root


def _verify(runner: str, root: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run([runner, "kb-verify"], cwd=root, capture_output=True, text=True)


@pytest.fixture
def all_red(tmp_path: Path) -> Path:
    """A KB that fails every one of the three: a broken link, a file with no
    frontmatter, no ``.index/`` at all, and a citation that resolves to nothing."""
    root = _consumer(tmp_path / "consumer")
    (root / "kb-root" / "x.md").write_text("[a](missing.md)\n", encoding="utf-8")
    return root


@pytest.fixture
def green(tmp_path: Path) -> Path:
    """The same shape with an empty KB, refreshed so the ``.index/`` spine the
    metadata verifier requires exists. Nothing here needs ``init``: that verb
    runs preflight over a *consuming project's* whole state, which is a
    different contract from the one under test."""
    root = _consumer(tmp_path / "consumer")
    env = {**os.environ, "PYTHONPATH": str(root / ".claude" / "agents")}
    subprocess.run(
        [sys.executable, "-m", "kb_tools.refresh_kb_metadata"], cwd=root, env=env, check=True, capture_output=True
    )
    return root


@pytest.mark.parametrize("runner", _RUNNERS)
def test_kb_verify_reports_all_three_verifiers_in_one_round(runner: str, all_red: Path) -> None:
    """Three plain recipe lines stop at the first failure, and both runners do.

    An all-red KB then needs three passes to surface three verifiers' faults,
    each one looking to its reader like the last problem.
    """
    done = _verify(runner, all_red)

    assert done.returncode != 0
    assert [mark for mark in _RED_MARKS if mark not in done.stdout] == []


@pytest.mark.parametrize("runner", _RUNNERS)
def test_kb_verify_is_green_when_every_verifier_is(runner: str, green: Path) -> None:
    """Collecting the rcs must not make the gate unpassable."""
    done = _verify(runner, green)

    assert done.returncode == 0, done.stdout + done.stderr
