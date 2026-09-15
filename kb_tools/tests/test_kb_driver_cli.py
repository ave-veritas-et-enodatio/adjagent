"""Mode dispatch and exit-code translation, through the shipped entry point.

The property under test: no invocation terminates without a baton — including
the paths that never reach the sequencer, which are exactly the ones where
the driver is least able to explain itself.

Its second half, once a run directory exists: no run terminates without its
report either. ``exit.json`` and ``cadence.jsonl`` are what a backgrounded
session reads after watch says the driver is gone, and the endings that most
need them — a kill, a boundary check, an exception nobody named — are the ones
that used to leave the directory with a pid file and nothing else.
"""

import json
import logging
import os
import shutil
import signal
import subprocess
import sys
import time
from collections.abc import Iterator
from dataclasses import replace
from pathlib import Path

import pytest

from kb_tools import kb_util
from kb_tools.kb_driver import baton, cli, config, ledger, runlog

_THIS_DIR = Path(__file__).resolve().parent
_PKG_PARENT = _THIS_DIR.parent.parent
_MINI_KB = _THIS_DIR / "fixtures" / "mini-kb"

MINIMAL = """
[run]
sources = ["AcmeWidgets.tex"]
permission_mode = "acceptEdits"
"""


@pytest.fixture(autouse=True)
def _detach_log_handlers() -> Iterator[None]:
    """``cli.main`` attaches handlers to a run log inside tmp_path; drop them after."""
    yield
    driver_log = logging.getLogger("kb_driver")
    for handler in list(driver_log.handlers):
        driver_log.removeHandler(handler)
        handler.close()


def _config(tmp_path: Path, body: str = MINIMAL) -> Path:
    path = tmp_path / "driver-run.toml"
    path.write_text(body, encoding="utf-8")
    return path


def _run_args(tmp_path: Path, *extra: str) -> list[str]:
    return ["run", "--config", str(_config(tmp_path)), "--run-dir", str(tmp_path / "runs"), *extra]


# ---------------------------------------------------------------------------
# Through the shipped surface
# ---------------------------------------------------------------------------


def test_missing_config_exits_13_with_its_baton(tmp_path: Path) -> None:
    result = subprocess.run(
        [sys.executable, "-m", "kb_tools.kb_driver", "run", "--config", "absent.toml"],
        cwd=tmp_path,
        env={**os.environ, "PYTHONPATH": str(_PKG_PARENT), "PYTHONDONTWRITEBYTECODE": "1"},
        capture_output=True,
        text=True,
    )
    assert result.returncode == baton.EXIT_CONFIG
    assert "config file not found: absent.toml" in result.stdout
    assert "nothing until the config is fixed" in result.stdout


# ---------------------------------------------------------------------------
# Exit translation
# ---------------------------------------------------------------------------


def test_a_run_outside_a_repository_reports_the_environment_and_still_lays_out_its_run_dir(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """The run directory is laid out and the exit names the environment fault.

    ``chdir`` is load-bearing: the driver resolves its repo root from the
    working directory, and a test that left it at the source tree would drive
    the sequencer against the live repository.
    """
    monkeypatch.chdir(tmp_path)

    code = cli.main(_run_args(tmp_path))

    out = capsys.readouterr().out
    assert code == baton.EXIT_ENVIRONMENT
    assert "re-run after the restore" in out

    run_dir = Path((tmp_path / "runs" / "LATEST").read_text(encoding="utf-8").strip())
    assert run_dir.is_dir()
    payload = json.loads((run_dir / "exit.json").read_text(encoding="utf-8"))
    assert payload["exit_code"] == baton.EXIT_ENVIRONMENT
    assert payload["barrier_record"] is None
    # No repository, so there was nothing to lock — and nothing was written
    # anywhere pretending otherwise.
    assert not list(tmp_path.rglob("kb-driver.lock"))


def test_a_flags_only_run_launches_naming_no_permission_mode_and_says_which_one_it_took(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """One flag is the whole specification, and the run starts and states its mode.

    It ends at the environment fault (no repository under ``tmp_path``), which
    is past config load and past the run-directory layout — the two things a
    file was previously required for. Two assertions matter: a launch that names
    no permission mode reaches that point at all, and the mode it took is on the
    console for an operator who did not know the flag existed.
    """
    monkeypatch.chdir(tmp_path)

    code = cli.main(["run", "--source", "AcmeWidgets.tex", "--run-dir", str(tmp_path / "runs")])

    out = capsys.readouterr().out
    assert code == baton.EXIT_ENVIRONMENT
    assert "re-run after the restore" in out
    assert config.DEFAULT_PERMISSION_MODE in out
    assert config.PERMISSION_MODE_FLAG in out
    assert not list(tmp_path.rglob("*.toml")), "nothing was composed on the way in"
    run_dir = Path((tmp_path / "runs" / "LATEST").read_text(encoding="utf-8").strip())
    assert json.loads((run_dir / "exit.json").read_text(encoding="utf-8"))["exit_code"] == baton.EXIT_ENVIRONMENT


def test_unconsumed_decisions_reach_exit_json_and_the_baton(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Unconsumed decisions are named on every terminal path, including one that never raised a barrier."""
    monkeypatch.chdir(tmp_path)

    cli.main(_run_args(tmp_path, "--decide", "spine-seed.runner-choice=just"))

    assert "spine-seed.runner-choice=just" in capsys.readouterr().out
    run_dir = Path((tmp_path / "runs" / "LATEST").read_text(encoding="utf-8").strip())
    payload = json.loads((run_dir / "exit.json").read_text(encoding="utf-8"))
    assert payload["unconsumed_decisions"] == ["spine-seed.runner-choice=just"]


def test_an_inadmissible_decision_is_refused_at_load(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """Both doors validate against the registry, so a typo is exit 13 before anything runs."""
    code = cli.main(_run_args(tmp_path, "--decide", "spine-seed.runner-choice=jus"))

    assert code == baton.EXIT_CONFIG
    assert "not admissible" in capsys.readouterr().out
    assert not (tmp_path / "runs").exists()


def test_malformed_decide_exits_13_before_the_run_directory_exists(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    code = cli.main(_run_args(tmp_path, "--decide", "phase-1b-design-gate"))

    assert code == baton.EXIT_CONFIG
    assert "malformed" in capsys.readouterr().out
    assert not (tmp_path / "runs").exists()


def test_a_live_run_lock_exits_16_for_every_run_dir_under_the_repo(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """The lock binds the repository, so `--run-dir` cannot get around it.

    The second half is the finding. While the lock sat beside LATEST at the
    run-directory parent, a second invocation naming a different `--run-dir` —
    which kb-testing relies on — took a *different* lock and ran on, while
    exit 16's baton claimed a guarantee nothing had checked.
    """
    repo = tmp_path / "repo"
    (repo / ".git").mkdir(parents=True)
    monkeypatch.chdir(repo)
    lock = runlog.repo_lock_path(repo)
    lock.parent.mkdir(parents=True, exist_ok=True)
    lock.write_text(json.dumps({"pid": os.getpid(), "run_id": "other"}), encoding="utf-8")

    code = cli.main(_run_args(tmp_path))

    out = capsys.readouterr().out
    assert code == baton.EXIT_LOCKED
    assert "another driver run is live" in out
    assert "report the live pid" in out

    elsewhere = ["run", "--config", str(_config(tmp_path)), "--run-dir", str(tmp_path / "other-runs")]
    assert cli.main(elsewhere) == baton.EXIT_LOCKED
    assert not (tmp_path / "other-runs").exists()


def test_a_usage_error_still_leaves_a_card(capsys: pytest.CaptureFixture[str]) -> None:
    code = cli.main(["run", "--source"])  # the flag's own value is missing

    assert code != 0
    assert "report this output verbatim and stop; do not interpret it" in capsys.readouterr().out


def test_a_run_specifying_nothing_names_both_doors_for_the_field_with_no_default(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """No config and no flags is a config error, not a usage error — and it says how to fix it.

    Both spellings, because either one answers it: a run is specified by a
    file, by flags, or by both, and a refusal naming only the key would read as
    though the file were still the only door.
    """
    code = cli.main(["run"])

    out = capsys.readouterr().out
    assert code == baton.EXIT_CONFIG
    assert "[run] sources" in out
    assert config.SOURCE_FLAG in out


def test_an_unhandled_exception_is_exit_15_with_a_baton_not_a_traceback(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """The last row: a failure the driver has no handler for still leaves a card.

    A bare ``RuntimeError`` out of a mode function is the shape of every defect
    the three named handlers do not cover; without the catch-all it exits 1 with
    a traceback and the relay has nothing to read.
    """

    def _boom(args: object, ctx: object) -> tuple[int, object]:
        raise RuntimeError("the sequencer lost its footing")

    monkeypatch.setitem(cli._MODES, "run", _boom)

    code = cli.main(_run_args(tmp_path))

    out = capsys.readouterr().out
    assert code == baton.EXIT_INTERNAL
    assert "RuntimeError: the sequencer lost its footing" in out
    assert "report as a driver defect" in out


def test_an_unhandled_exception_inside_the_run_leaves_its_traceback_in_the_run_log(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """EXIT_INTERNAL's baton calls the run directory the bug report; this is what makes it one.

    Inside a repository, so the run really does take the lock — the release
    is ownership-checked and this is the path on which it would be easiest to
    leave a live-looking lock behind.
    """
    repo = tmp_path / "repo"
    (repo / ".git").mkdir(parents=True)
    monkeypatch.chdir(repo)

    def _boom(**kwargs: object) -> object:
        raise KeyError("REVIEW_CYCLES")

    monkeypatch.setattr(cli.run, "execute", _boom)

    code = cli.main(_run_args(tmp_path))
    capsys.readouterr()

    assert code == baton.EXIT_INTERNAL
    run_dir = Path((tmp_path / "runs" / "LATEST").read_text(encoding="utf-8").strip())
    records = [json.loads(line) for line in (run_dir / "run.log").read_text(encoding="utf-8").splitlines()]
    tracebacks = [record["exception"] for record in records if "exception" in record]
    assert len(tracebacks) == 1
    assert "KeyError: 'REVIEW_CYCLES'" in tracebacks[0]
    # The lock is released even on this path, so the next run is not wedged.
    assert not runlog.repo_lock_path(repo).exists()


# ---------------------------------------------------------------------------
# A run that dies abnormally still leaves its report
# ---------------------------------------------------------------------------


def _git(root: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=root, check=True, capture_output=True)


@pytest.fixture
def consumer(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A repository a run really starts in, with a KB under it.

    ``fixtures/mini-kb`` is the tree, so these cases drive a run over a KB
    somebody wrote rather than over an empty directory, and every one of them
    passes ``--dry-run``: whatever the walk reaches, no call this driver
    dispatches can be spawned. What it reaches is ``pre.preflight``, the first
    row that leaves the driver's own process — which is where each case below
    substitutes the ending it means to test.
    """
    repo = tmp_path / "consumer"
    repo.mkdir()
    _git(repo, "init", "-q")
    for key, value in (
        ("user.email", "fixture@example.invalid"),
        ("user.name", "fixture"),
        ("commit.gpgsign", "false"),
    ):
        _git(repo, "config", key, value)
    shutil.copytree(_MINI_KB, kb_util.kb_root(repo))
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "the KB this run is over")
    monkeypatch.chdir(repo)
    return repo


def _drive(consumer: Path) -> tuple[int, Path]:
    """One ``--dry-run`` invocation through ``main``; its exit code and its run parent.

    The parent is outside the repository, which is where a run's evidence
    belongs and which is also what makes the resume line below carry a flag.
    """
    runs = consumer.parent / "runs"
    code = cli.main(["run", "--source", "AcmeWidgets.tex", config.DRY_RUN_FLAG, config.RUN_DIR_FLAG, str(runs)])
    return code, runs


def _run_dir(runs: Path) -> Path:
    return Path((runs / "LATEST").read_text(encoding="utf-8").strip())


def _report(runs: Path) -> dict[str, object]:
    """The run's own account of how it ended, read back off disk."""
    run_dir = _run_dir(runs)
    assert (run_dir / "cadence.jsonl").is_file(), "the run left no cadence record"
    return json.loads((run_dir / "exit.json").read_text(encoding="utf-8"))


def test_a_killed_run_still_leaves_its_report(
    consumer: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """A real SIGTERM, inside a real step, and both files are still there.

    This is the shape of every kill made on a backgrounded run, and the one the
    run directory has the least to say about: on the default disposition the
    process ends between two bytecodes, leaving a pid file and no account of
    itself, while ``exit.json`` is the only channel the session that launched it
    has for learning that it ended at all.
    """

    def _killed(repo_root: Path) -> ledger.Outcome:
        del repo_root
        os.kill(os.getpid(), signal.SIGTERM)
        time.sleep(1)  # the handler raises at the next bytecode, not inside the call above
        raise AssertionError("the signal never reached the run")

    monkeypatch.setattr(ledger, "preflight", _killed)

    code, runs = _drive(consumer)
    out = capsys.readouterr().out

    assert code == runlog.SIGNAL_EXIT_BASE + signal.SIGTERM
    assert _report(runs)["exit_code"] == code
    # A code on neither ladder, so the card that prints is the fallback — which
    # is the right one: nothing in the build decided this ending, and the one
    # thing a session must not do with it is narrate it.
    assert "do not interpret it" in out
    # The lock goes back even here, so the kill costs the next run nothing.
    assert not runlog.repo_lock_path(consumer).exists()


def test_a_boundary_error_mid_run_still_leaves_its_report(
    consumer: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Exit 15's card calls the run directory the bug report; this is what puts a report in it.

    A boundary check is the driver catching itself, and it fires from inside the
    walk — so before this the one ending whose card sends an operator to the run
    directory was an ending that wrote nothing there but a log.
    """

    def _boundary(repo_root: Path) -> ledger.Outcome:
        del repo_root
        runlog.require(False, "the tool answered with an rc outside its own vocabulary")
        raise AssertionError("require did not raise")

    monkeypatch.setattr(ledger, "preflight", _boundary)

    code, runs = _drive(consumer)
    out = capsys.readouterr().out

    assert code == baton.EXIT_INTERNAL
    assert _report(runs)["exit_code"] == baton.EXIT_INTERNAL
    assert "the run directory is the bug report" in out
    assert "rc outside its own vocabulary" in out


def test_a_card_from_a_real_run_hands_back_the_run_directory_it_was_launched_with(
    consumer: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """The resume line an operator actually reads, off a run that actually ran.

    ``start.proceed`` is the first barrier a launch raises, and its card is a
    resume line with an answer substituted into it. Run that line without
    ``--run-dir`` and the resumed run files its evidence under the default
    parent while the first run's stays where it was.
    """
    monkeypatch.setattr(ledger, "preflight", lambda repo_root: ledger.Outcome(baton.EXIT_OK))

    code, runs = _drive(consumer)
    out = capsys.readouterr().out

    assert code == baton.EXIT_BARRIER
    resume = [line for line in out.splitlines() if f"{kb_util.DRIVER_INVOCATION} run " in line]
    assert resume, "the barrier card offered no resume line"
    for line in resume:
        assert f"{config.RUN_DIR_FLAG} {runs}" in line


def test_every_command_a_card_offers_names_the_run_directory(tmp_path: Path) -> None:
    """Closed over both ladders, so no exit code can offer a command that looks elsewhere.

    Resume lines and watch lines alike: a resume carries the directory in the
    invocation it hands back, and a watch — which takes no ``--config`` and no
    ``--source`` — carries it in the only flag it has. Each mode is rendered
    over its own ladder, because a code means one thing in each. The guard above
    the loop is what keeps the claim from passing by matching nothing.
    """
    runs = tmp_path / "outside" / "runs"
    cfg = config.load(None, run_overrides={"sources": ("a.tex",)}, run_dir=runs)
    context = baton.BatonContext(
        invocation=cfg.invocation,
        pair="start.proceed",
        question="proceed?",
        run_dir=str(runs / "20260101T000000-1"),
        run_dir_parent=config.run_dir_parent(runs),
    )

    offered = [
        (mode, code, line)
        for mode, codes in (
            (baton.MODE_RUN, baton.RUN_MODE_EXIT_CODES),
            (baton.MODE_WATCH, baton.WATCH_MODE_EXIT_CODES),
        )
        for code in codes
        for line in baton.render(code, replace(context, mode=mode)).splitlines()
        if kb_util.DRIVER_INVOCATION in line
    ]

    assert offered, "no card in either ladder offered a command"
    assert {mode for mode, _, _ in offered} == {baton.MODE_RUN, baton.MODE_WATCH}, "one mode offered none"
    for mode, code, line in offered:
        assert f"{config.RUN_DIR_FLAG} {runs}" in line, f"{mode} exit {code}: {line}"


# ``watch`` is the second registered mode; its dispatch, exits, and batons are
# exercised through this same entry point in test_kb_driver_watch.py.
