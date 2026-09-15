"""D7: staging refuses to destroy a live driver run's workspace.

``stage-arxiv-paper`` (``kb-testing/justfile``) resets a staged paper to its
``cleared`` tag and ``rm -rf``s ``.claude-temp/`` and ``kb-root/``
unconditionally before every restage, and ``stage-arxiv-corpus`` reaches that
per id. If a driver run is live in the repository being restaged, this
destroys the tree it is building and whatever inference it already spent,
silently. ``guard-run-lock`` — a private recipe factored out of
``stage-arxiv-paper`` so any future recipe that wipes ``.claude-temp/`` or
``kb-root/`` can call it too — asks first, through ``kb_util``'s read-only
``show-run-lock`` op (D13), and refuses on a live holder.

Liveness has exactly one definition, ``kb_driver.runlog.lock_state``, and
these tests never judge a pid themselves: each one only fabricates the lock
file ``runlog.run_lock`` would have written and checks the recipe's response
to what that judgement reports.

Every fixture here lives under this repository's own ``.claude-temp/``, never
under the system temp directory pytest's own ``tmp_path`` would hand out —
this suite's own scratch discipline, not something under test.
"""

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from kb_tools.kb_driver import runlog

_REPO_ROOT = Path(__file__).resolve().parents[2]
_KB_TESTING = _REPO_ROOT / "kb-testing"
_INSTALLER = _REPO_ROOT / "gen-defs.py"

#: This suite's own scratch, wiped and rebuilt every run — never
#: `tmp_path`/`tmp_path_factory`, which pytest anchors under the system temp
#: directory.
_SCRATCH = _REPO_ROOT / ".claude-temp" / "d7-run-lock-guard-tests"

#: A pid `os.kill(pid, 0)` reliably reports `ProcessLookupError` for — not a
#: `PermissionError`, which `runlog._pid_alive` treats as alive. Confirmed
#: empirically rather than assumed: pid_max never reaches nine digits.
_DEAD_PID = 999999999


@pytest.fixture(scope="module", autouse=True)
def _clean_scratch():
    shutil.rmtree(_SCRATCH, ignore_errors=True)
    _SCRATCH.mkdir(parents=True)
    yield
    shutil.rmtree(_SCRATCH, ignore_errors=True)


def _git(root: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=root, check=True, capture_output=True)


def _make_consumer(root: Path) -> Path:
    """A committed repository with the toolchain installed — what `guard-run-lock` reads.

    Built directly with the installer rather than through arXiv staging: the
    guard's contract is about the lock file at this tree's own
    `.claude-temp/kb-driver.lock`, not about how the tree came to hold
    `.claude/agents/kb_tools`.
    """
    root.mkdir(parents=True)
    _git(root, "init", "-q")
    for key, value in (
        ("user.email", "fixture@example.invalid"),
        ("user.name", "fixture"),
        ("commit.gpgsign", "false"),
    ):
        _git(root, "config", key, value)
    claude = root / ".claude"
    claude.mkdir()
    installed = subprocess.run(
        [sys.executable, str(_INSTALLER), "install", str(claude)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
    )
    assert installed.returncode == 0, f"stdout:\n{installed.stdout}\nstderr:\n{installed.stderr}"
    _git(root, "add", "-A")
    _git(root, "commit", "-qm", "toolchain installed")
    return root


def _run_guard(dir_: Path) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["just", "guard-run-lock", str(dir_)],
        cwd=_KB_TESTING,
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
    )


@pytest.fixture(scope="module")
def consumer() -> Path:
    return _make_consumer(_SCRATCH / "consumer")


@pytest.fixture()
def lock_path(consumer: Path) -> Path:
    path = runlog.repo_lock_path(consumer)
    yield path
    path.unlink(missing_ok=True)


def test_guard_proceeds_when_no_toolchain_is_installed() -> None:
    """Nothing could ever have run under a directory nothing was installed into."""
    bare = _SCRATCH / "bare"
    bare.mkdir()
    result = _run_guard(bare)
    assert result.returncode == 0, result.stderr


def test_guard_proceeds_on_an_absent_lock(consumer: Path, lock_path: Path) -> None:
    assert not lock_path.exists()
    result = _run_guard(consumer)
    assert result.returncode == 0, result.stderr


def test_guard_proceeds_on_a_stale_lock(consumer: Path, lock_path: Path) -> None:
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    lock_path.write_text(json.dumps({"pid": _DEAD_PID, "run_id": "stale-run"}), encoding="utf-8")
    result = _run_guard(consumer)
    assert result.returncode == 0, result.stderr


def test_guard_refuses_on_a_live_lock_naming_the_holder(consumer: Path, lock_path: Path) -> None:
    """The pytest process's own pid stands in for a live driver run — it is alive for the test's duration."""
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    lock_path.write_text(
        json.dumps({"pid": os.getpid(), "run_id": "live-run-id", "started": "2026-09-01T00:00:00+00:00"}),
        encoding="utf-8",
    )
    result = _run_guard(consumer)
    assert result.returncode != 0
    assert str(os.getpid()) in result.stderr
    assert "live-run-id" in result.stderr


#: Enough of an installed `kb_util` to prove the point: an argparse op
#: vocabulary that does not include `show-run-lock`, so invoking it fails
#: exactly the way a genuinely older install would — rejected by the parser
#: itself, before any handler runs — without needing a real historical
#: revision of `kb_tools` to reproduce that failure.
_STALE_KB_UTIL_SOURCE = '''"""Stand-in for an installed kb_util predating show-run-lock."""

import argparse


def main(argv=None):
    parser = argparse.ArgumentParser(prog="python3 -m kb_tools.kb_util")
    subparsers = parser.add_subparsers(dest="op", required=True, metavar="<op>")
    subparsers.add_parser("preflight")
    parser.parse_args(argv)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
'''


def _make_stale_toolchain_dir(root: Path) -> Path:
    """A directory whose `.claude/agents/kb_tools` cannot answer `show-run-lock` at all."""
    package = root / ".claude" / "agents" / "kb_tools"
    package.mkdir(parents=True)
    (package / "__init__.py").write_text("", encoding="utf-8")
    (package / "kb_util.py").write_text(_STALE_KB_UTIL_SOURCE, encoding="utf-8")
    return root


def test_guard_proceeds_when_the_installed_toolchain_predates_the_op() -> None:
    """The case that cost a corpus wipe: an installed kb_util whose argparse rejects `show-run-lock` outright.

    Indistinguishable from a resolution failure by exit code alone — both are
    nonzero — so the guard has to read *how* it failed: argparse's own
    "invalid choice" refusal, raised before any op runs, opens with a
    `usage:` line that `show-run-lock`'s own failures never produce. Read
    correctly, this is the no-toolchain case, not "no lock": proceed.
    """
    stale = _make_stale_toolchain_dir(_SCRATCH / "stale-toolchain")
    result = _run_guard(stale)
    assert result.returncode == 0, result.stderr


def _make_tarball(path: Path) -> None:
    """A minimal e-print — one `.tex` file, tarred — enough for `stage-arxiv-paper` to unpack."""
    src = path.parent / "_src"
    src.mkdir()
    (src / "paper.tex").write_text(
        "\\documentclass{article}\n\\begin{document}\nx\n\\end{document}\n", encoding="utf-8"
    )
    subprocess.run(["tar", "czf", str(path), "-C", str(src), "paper.tex"], check=True, capture_output=True)
    shutil.rmtree(src)


def test_stage_arxiv_paper_refuses_to_restage_over_a_live_run_and_proceeds_once_it_clears() -> None:
    """The integration path: a live lock stops the real recipe before it destroys anything.

    Drives the real `stage-arxiv-paper` recipe with `ARXIV_TGZ`/`ARXIV_STAGE`
    overridden (the same `--set` mechanism the justfile already uses for
    `KB_DRIVER_ARXIV_DIR`) to point at this suite's own scratch — never at
    `test-data/transient/`, which this test never touches.
    """
    fake_id = "9999.99999v1"
    tgz_dir = _SCRATCH / "tgz"
    stage_dir = _SCRATCH / "stage"
    tgz_dir.mkdir()
    stage_dir.mkdir()
    _make_tarball(tgz_dir / f"{fake_id}.tar.gz")

    # stage-arxiv-paper's own install call rebuilds its target as
    # "kb-testing/${ARXIV_STAGE}/..." from the repo root, so the override must
    # be a path relative to kb-testing/, exactly like every path this justfile
    # already binds ARXIV_TGZ/ARXIV_STAGE to.
    tgz_rel = os.path.relpath(tgz_dir, _KB_TESTING)
    stage_rel = os.path.relpath(stage_dir, _KB_TESTING)

    def stage() -> subprocess.CompletedProcess:
        return subprocess.run(
            ["just", "--set", "ARXIV_TGZ", tgz_rel, "--set", "ARXIV_STAGE", stage_rel, "stage-arxiv-paper", fake_id],
            cwd=_KB_TESTING,
            capture_output=True,
            text=True,
            encoding="utf-8",
            check=False,
        )

    first = stage()
    assert first.returncode == 0, first.stderr
    paper_dir = stage_dir / fake_id
    assert (paper_dir / ".claude" / "agents" / "kb_tools").is_dir()

    # Evidence a live build would be writing, which the destructive branch
    # (git reset --hard cleared; rm -rf .claude-temp kb-root) would otherwise
    # erase unconditionally.
    (paper_dir / "kb-root").mkdir()
    (paper_dir / "kb-root" / "marker.txt").write_text("evidence", encoding="utf-8")
    (paper_dir / ".claude-temp").mkdir()
    (paper_dir / ".claude-temp" / "run.log").write_text("in-progress build\n", encoding="utf-8")

    lock_path = runlog.repo_lock_path(paper_dir)
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    lock_path.write_text(json.dumps({"pid": os.getpid(), "run_id": "arxiv-live-run"}), encoding="utf-8")

    refused = stage()
    assert refused.returncode != 0
    assert str(os.getpid()) in refused.stderr
    assert "arxiv-live-run" in refused.stderr
    assert (paper_dir / "kb-root" / "marker.txt").exists()
    assert (paper_dir / ".claude-temp" / "run.log").exists()

    lock_path.unlink()
    proceeded = stage()
    assert proceeded.returncode == 0, proceeded.stderr
    assert not (paper_dir / "kb-root" / "marker.txt").exists()
    assert not (paper_dir / ".claude-temp" / "run.log").exists()


def _configured_arxiv_ids() -> tuple[str, ...]:
    """The corpus `stage-arxiv-corpus` restages by default, read off the justfile.

    Read rather than hardcoded, so this test tracks the corpus without
    maintaining a second count of it.
    """
    result = subprocess.run(
        ["just", "--evaluate", "ARXIV_IDS"],
        cwd=_KB_TESTING,
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=True,
    )
    return tuple(result.stdout.split())


def test_stage_arxiv_corpus_stages_the_whole_corpus_unchanged() -> None:
    """The guard adds a check, not a side effect: restaging twice tallies the same corpus both times.

    Skips where the corpus tarballs are not cached — `prep-test-data-arxiv`
    is a deliberate, separate act (`stage-arxiv-paper`'s own header says so),
    and a fresh checkout has downloaded nothing.

    No wipe first, deliberately: whatever `kb_tools` the corpus's own
    `.claude/agents/kb_tools` currently carries — including one staged before
    `show-run-lock` existed, which `test_guard_proceeds_when_the_installed_
    toolchain_predates_the_op` covers directly — the guard reads it and either
    proceeds or refuses correctly, so restaging the real corpus in place is
    itself evidence the guard imposes no migration step of its own.
    """
    ids = _configured_arxiv_ids()
    tgz_dir = _KB_TESTING / "test-data" / "transient" / "arxiv-tgz"
    if not ids or not all((tgz_dir / f"{paper_id}.tar.gz").exists() for paper_id in ids):
        pytest.skip("arXiv corpus tarballs not staged — run `just prep-test-data-arxiv` first")

    stage_dir = _KB_TESTING / "test-data" / "transient" / "arxiv"

    def run_corpus() -> subprocess.CompletedProcess:
        return subprocess.run(
            ["just", "stage-arxiv-corpus"],
            cwd=_KB_TESTING,
            capture_output=True,
            text=True,
            encoding="utf-8",
            check=False,
        )

    first = run_corpus()
    assert first.returncode == 0, first.stderr
    before = sorted(p.name for p in stage_dir.iterdir())
    assert before == sorted(ids)
    assert f"{len(ids)} paper(s) staged" in first.stdout

    second = run_corpus()
    assert second.returncode == 0, second.stderr
    after = sorted(p.name for p in stage_dir.iterdir())
    assert after == before
    assert f"{len(ids)} paper(s) staged" in second.stdout
