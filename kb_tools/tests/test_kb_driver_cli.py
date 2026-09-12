"""Mode dispatch and exit-code translation, through the shipped entry point.

The property under test: no invocation terminates without a baton — including
the paths that never reach the sequencer, which are exactly the ones where
the driver is least able to explain itself.
"""

import json
import logging
import os
import subprocess
import sys
from collections.abc import Iterator
from pathlib import Path

import pytest

from kb_tools.kb_driver import baton, cli, config, runlog

_THIS_DIR = Path(__file__).resolve().parent
_PKG_PARENT = _THIS_DIR.parent.parent

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


# ``watch`` is the second registered mode; its dispatch, exits, and batons are
# exercised through this same entry point in test_kb_driver_watch.py.
