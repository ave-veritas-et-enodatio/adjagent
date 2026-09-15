"""Watch mode: the three exits, the render-plus-baton obligation, no writes.

Two layers here, deliberately:

* the poll loop is driven through its :class:`watch.Sensors` seam — no
  subprocess, no clock — because what is under test is the state machine, not
  ``show-status``, which ``test_kb_driver_ledger.py`` already exercises for
  real;
* the exits are driven through ``cli.main``, the shipped surface, because the
  property that matters to the relay is that *the invocation* prints the
  complete render and then its baton — a property no in-process call to the
  loop can demonstrate.

Renders are built with ``kb_pipeline``'s own formatters rather than pasted, so
a change to the checklist's shape reaches this file as a failure instead of as
a silently unparsed block.
"""

import logging
import os
import subprocess
import sys
from collections.abc import Callable, Iterator
from pathlib import Path

import pytest

from kb_tools import kb_pipeline, kb_util
from kb_tools.kb_driver import baton, cli, ledger, runlog, watch

_STAGES = kb_pipeline.STAGE_IDS
_NONE: frozenset[str] = frozenset()
_STARTED = frozenset(_STAGES[:1])
_ADVANCED = frozenset(_STAGES[:2])


@pytest.fixture(autouse=True)
def _detach_log_handlers() -> Iterator[None]:
    """Watch configures no handlers; drop any a neighbouring module's run left attached."""
    yield
    driver_log = logging.getLogger("kb_driver")
    for handler in list(driver_log.handlers):
        driver_log.removeHandler(handler)
        handler.close()


def _render(recorded: frozenset[str]) -> str:
    """A show-status render, from the tool's own formatters."""
    lines = [kb_pipeline.status_line(set(recorded)), *kb_pipeline.checklist_lines(set(recorded))]
    return "\n".join(lines) + "\n"


def _ok(recorded: frozenset[str]) -> ledger.Outcome:
    return ledger.Outcome(baton.EXIT_OK, stdout=_render(recorded))


def _run_dir(tmp_path: Path, *, pid: int | None) -> Path:
    """A run directory as ``runlog.prepare`` leaves it, pointed at by LATEST."""
    parent = tmp_path / "runs"
    run_dir = parent / "20260901T120000-1"
    run_dir.mkdir(parents=True)
    if pid is not None:
        (run_dir / "run.pid").write_text(f"{pid}\n", encoding="utf-8")
    (parent / "LATEST").write_text(f"{run_dir}\n", encoding="utf-8")
    return parent


def _dead_pid() -> int:
    """A pid that has exited and been reaped — a real one, not an invented number."""
    process = subprocess.Popen([sys.executable, "-c", ""])
    process.wait()
    return process.pid


class _Clock:
    """A monotonic clock that only moves when the loop sleeps."""

    def __init__(self) -> None:
        self.t = 0.0

    def sleep(self, seconds: float) -> None:
        self.t += seconds

    def monotonic(self) -> float:
        return self.t


def _script(outcomes: list[ledger.Outcome]) -> Callable[[], ledger.Outcome]:
    """Poll `n` returns ``outcomes[n]``; the last entry then repeats forever."""
    queue = list(outcomes)
    return lambda: queue.pop(0) if len(queue) > 1 else queue[0]


def _sensors(outcomes: list[ledger.Outcome], *, alive: bool = True) -> watch.Sensors:
    clock = _Clock()
    return watch.Sensors(
        status=_script(outcomes), alive=lambda _pid: alive, sleep=clock.sleep, monotonic=clock.monotonic
    )


# ---------------------------------------------------------------------------
# The poll loop
# ---------------------------------------------------------------------------


def test_ledger_growth_exits_0_naming_the_new_stage(tmp_path: Path) -> None:
    parent = _run_dir(tmp_path, pid=os.getpid())

    result = watch.watch(
        run_parent=parent,
        sensors=_sensors([_ok(_STARTED), _ok(_ADVANCED)]),
        timeout_seconds=600,
        poll_seconds=15,
    )

    assert result.exit_code == baton.EXIT_OK
    assert _STAGES[1] in result.detail[0]
    assert result.render == _render(_ADVANCED)


def test_no_growth_before_the_timeout_exits_21(tmp_path: Path) -> None:
    parent = _run_dir(tmp_path, pid=os.getpid())

    result = watch.watch(
        run_parent=parent,
        sensors=_sensors([_ok(_STARTED)]),
        timeout_seconds=60,
        poll_seconds=15,
    )

    assert result.exit_code == baton.EXIT_WATCH_TIMEOUT
    # Even a no-progress relay carries the checklist.
    assert result.render == _render(_STARTED)


def test_a_dead_driver_exits_22_before_growth_is_considered(tmp_path: Path) -> None:
    # Grew *and* gone: 22 wins, because the driver may have recorded a stage
    # and then stopped at a barrier, and only exit.json says which.
    parent = _run_dir(tmp_path, pid=os.getpid())

    result = watch.watch(
        run_parent=parent,
        sensors=_sensors([_ok(_ADVANCED)], alive=False),
        timeout_seconds=600,
        poll_seconds=15,
    )

    assert result.exit_code == baton.EXIT_WATCH_DRIVER_GONE
    assert result.run_dir is not None and result.run_dir.name == "20260901T120000-1"


def test_a_missing_run_pid_reads_as_a_gone_driver(tmp_path: Path) -> None:
    parent = _run_dir(tmp_path, pid=None)

    result = watch.watch(run_parent=parent, sensors=_sensors([_ok(_STARTED)]), timeout_seconds=600, poll_seconds=15)

    assert result.exit_code == baton.EXIT_WATCH_DRIVER_GONE
    assert "run.pid" in result.detail[0]


def test_no_latest_is_an_environment_fault_with_a_restore(tmp_path: Path) -> None:
    (tmp_path / "runs").mkdir()

    result = watch.watch(
        run_parent=tmp_path / "runs", sensors=_sensors([_ok(_NONE)]), timeout_seconds=600, poll_seconds=15
    )

    assert result.exit_code == baton.EXIT_ENVIRONMENT
    assert "restore:" in result.detail[0]
    assert f"{kb_util.DRIVER_INVOCATION} run --config" in result.detail[0]


def test_a_refused_show_status_passes_its_own_exit_through(tmp_path: Path) -> None:
    parent = _run_dir(tmp_path, pid=os.getpid())
    refused = ledger.Outcome(baton.EXIT_ENVIRONMENT, stdout="", detail=("show-status exited 2",))

    result = watch.watch(run_parent=parent, sensors=_sensors([refused]), timeout_seconds=600, poll_seconds=15)

    assert result.exit_code == baton.EXIT_ENVIRONMENT


def test_a_render_without_a_checklist_is_a_boundary_error(tmp_path: Path) -> None:
    # Otherwise output-shape drift presents as a watch that never sees growth.
    parent = _run_dir(tmp_path, pid=os.getpid())
    shapeless = ledger.Outcome(baton.EXIT_OK, stdout="[kb-build] status: in progress\n")

    with pytest.raises(runlog.BoundaryError):
        watch.watch(run_parent=parent, sensors=_sensors([shapeless]), timeout_seconds=600, poll_seconds=15)


@pytest.mark.parametrize("bad", [0, -1])
def test_a_nonpositive_bound_is_a_boundary_error(tmp_path: Path, bad: int) -> None:
    parent = _run_dir(tmp_path, pid=os.getpid())

    with pytest.raises(runlog.BoundaryError):
        watch.watch(run_parent=parent, sensors=_sensors([_ok(_STARTED)]), timeout_seconds=bad, poll_seconds=15)


# ---------------------------------------------------------------------------
# The no-write expectation check
# ---------------------------------------------------------------------------


def test_no_writes_refuses_a_write_mode_open(tmp_path: Path) -> None:
    with pytest.raises(runlog.BoundaryError):
        with watch.no_writes():
            open(tmp_path / "scratch", "w", encoding="utf-8").close()


def test_no_writes_refuses_the_pathlib_route_too(tmp_path: Path) -> None:
    # Path.write_text goes through io.open, not builtins.open: both names have
    # to be swapped or the check has a hole exactly where the driver writes.
    with pytest.raises(runlog.BoundaryError):
        with watch.no_writes():
            (tmp_path / "scratch").write_text("x", encoding="utf-8")


def test_no_writes_permits_reads_and_restores_open(tmp_path: Path) -> None:
    (tmp_path / "readable").write_text("content\n", encoding="utf-8")

    with watch.no_writes():
        assert (tmp_path / "readable").read_text(encoding="utf-8") == "content\n"

    (tmp_path / "after").write_text("restored\n", encoding="utf-8")
    assert (tmp_path / "after").is_file()


# ---------------------------------------------------------------------------
# Through the shipped surface: every exit is a render followed by its baton
# ---------------------------------------------------------------------------


@pytest.fixture
def _consuming_repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A cwd whose enclosing git root resolves, since watch anchors on it."""
    (tmp_path / ".git").mkdir()
    monkeypatch.chdir(tmp_path)
    return tmp_path


def _stub_status(monkeypatch: pytest.MonkeyPatch, outcomes: list[ledger.Outcome]) -> None:
    scripted = _script(outcomes)

    def fake(repo_root: Path, *, relay: bool = True) -> ledger.Outcome:
        assert relay is False, "a poll is not a stage transition; it must not re-print the render"
        return scripted()

    monkeypatch.setattr(ledger, "show_status", fake)


def test_growth_relays_the_render_then_its_baton(
    _consuming_repo: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    parent = _run_dir(_consuming_repo, pid=os.getpid())
    _stub_status(monkeypatch, [_ok(_STARTED), _ok(_ADVANCED)])

    code = cli.main(["watch", "--run-dir", str(parent), "--poll", "1", "--timeout", "600"])

    out = capsys.readouterr().out
    assert code == baton.EXIT_OK
    assert _render(_ADVANCED) in out
    assert out.index(_render(_ADVANCED)) < out.index(f"{baton.PREFIX} PLACE IN YOUR MESSAGE BODY")


def test_growth_is_relayed_as_progress_and_never_as_a_finished_build(
    _consuming_repo: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """The driver is alive and a stage landed, which is the only state exit 0 is returned in.

    Run mode's 0 is a finished build and shares the number, so a poll rendering
    that card told the relay to report a live build — hours from its last stage —
    as complete. The card this invocation prints is the watch table's.
    """
    parent = _run_dir(_consuming_repo, pid=os.getpid())
    _stub_status(monkeypatch, [_ok(_STARTED), _ok(_ADVANCED)])

    code = cli.main(["watch", "--run-dir", str(parent), "--poll", "1", "--timeout", "600"])

    out = capsys.readouterr().out
    assert code == baton.EXIT_OK
    assert "report completion" not in out
    assert "still running" in out
    assert f"{baton.PREFIX}   {kb_util.DRIVER_INVOCATION} watch" in out


def test_timeout_relays_render_and_the_watch_again_baton(
    _consuming_repo: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    parent = _run_dir(_consuming_repo, pid=os.getpid())
    _stub_status(monkeypatch, [_ok(_STARTED)])

    code = cli.main(["watch", "--run-dir", str(parent), "--poll", "1", "--timeout", "1"])

    out = capsys.readouterr().out
    assert code == baton.EXIT_WATCH_TIMEOUT
    assert _render(_STARTED) in out
    assert f"{baton.PREFIX}   {kb_util.DRIVER_INVOCATION} watch" in out


def test_a_gone_driver_relays_a_baton_pointing_at_exit_json(
    _consuming_repo: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    parent = _run_dir(_consuming_repo, pid=_dead_pid())
    _stub_status(monkeypatch, [_ok(_STARTED)])

    code = cli.main(["watch", "--run-dir", str(parent), "--poll", "1", "--timeout", "600"])

    out = capsys.readouterr().out
    assert code == baton.EXIT_WATCH_DRIVER_GONE
    assert _render(_STARTED) in out
    assert f"{parent / '20260901T120000-1'}/exit.json" in out
    assert "is no longer alive" in out


def test_a_watch_that_writes_exits_15_with_its_baton(
    _consuming_repo: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    # The expectation check has teeth: a sensor that writes — the way a
    # carelessly added cache or log file would — stops the invocation.
    parent = _run_dir(_consuming_repo, pid=os.getpid())
    scratch = _consuming_repo / "should-not-exist"

    def writing(repo_root: Path, *, relay: bool = True) -> ledger.Outcome:
        scratch.write_text("watch wrote this\n", encoding="utf-8")
        return _ok(_STARTED)

    monkeypatch.setattr(ledger, "show_status", writing)

    code = cli.main(["watch", "--run-dir", str(parent), "--poll", "1", "--timeout", "600"])

    out = capsys.readouterr().out
    assert code == baton.EXIT_INTERNAL
    assert "report as a driver defect" in out
    assert not scratch.exists()


def test_an_unresolvable_root_exits_14_with_a_baton(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    # No .git anywhere up the tree: there is no ledger to read. The walk itself
    # is kb_util's and tested there; what is under test is that watch turns its
    # refusal into exit 14 rather than a traceback.
    def unresolvable(start: Path | None = None) -> Path:
        raise kb_util.RepoRootError("no .git entry found walking up from /nowhere")

    monkeypatch.setattr(kb_util, "find_git_root", unresolvable)

    code = cli.main(["watch", "--run-dir", str(tmp_path / "runs")])

    out = capsys.readouterr().out
    assert code == baton.EXIT_ENVIRONMENT
    assert "no .git entry found" in out
    assert "relay the `restore:` lines as printed" in out
