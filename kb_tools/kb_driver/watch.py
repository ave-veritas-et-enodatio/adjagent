"""Watch mode: the bounded, read-only poll of a live run.

``/kb-build`` launches the driver backgrounded and holds no pipe to it, so the
session learns what the build is doing by re-invoking this mode: it polls the
ledger's recorded-stage set (via ``show-status``) and the liveness of
``run.pid``, and exits the moment it has something to relay. Every exit prints
the complete verbatim render followed by its baton — even the
no-progress one, because a relay that carries no checklist is a relay the
session must narrate from memory.

Mode-scoped exit codes::

    0   the recorded-stage set grew
    21  the poll timed out with no growth
    22  the driver process is no longer alive

**Ordering, stated explicitly.**
Liveness is tested before growth. The composition "grew *and* gone" is
otherwise ambiguous, and only 22 resolves it correctly: the driver may have
recorded a stage and then stopped at a barrier, and exit 0's baton says
"report completion". 22 sends the session to ``exit.json``, which names the
terminal code for every one of those endings.

**Watch writes nothing.** It touches no run directory, no KB, and no log file
— its only output is stdout. :func:`no_writes` is the always-on expectation
check for that: a write-mode ``open`` anywhere under a watch is a driver
defect and exits 15.

Stdlib only.
"""

import argparse
import builtins
import io
import re
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, replace
from pathlib import Path

from .. import kb_util
from . import baton, ledger, runlog

_log = runlog.logger("watch")

DEFAULT_TIMEOUT_SECONDS = 600
DEFAULT_POLL_SECONDS = 15

# The checklist block is the only thing in a render matching `^\[[x* ]\] `
# (kb_pipeline._print_report), which is what makes it liftable without knowing
# about the rest of the card. `[x]` is recorded; `[*]` and `[ ]` are not.
_CHECKLIST_RE = re.compile(r"^\[([x* ])\] (\S+)", re.MULTILINE)


@dataclass(frozen=True)
class Sensors:
    """The two things watch reads, plus the clock — the seam the tests drive.

    ``status`` returns the ledger adapter's outcome for a non-relaying
    ``show-status`` read; ``alive`` answers whether a pid is still running.
    Neither writes, which is what makes :func:`no_writes` a check on the
    driver's own code rather than on an unknown.
    """

    status: Callable[[], ledger.Outcome]
    alive: Callable[[int], bool]
    sleep: Callable[[float], None] = time.sleep
    monotonic: Callable[[], float] = time.monotonic


@dataclass(frozen=True)
class Result:
    """One watch invocation's outcome: the code, and the render that goes above its baton."""

    exit_code: int
    render: str = ""
    detail: tuple[str, ...] = ()
    run_dir: Path | None = None


# --- the no-write expectation check -----------------------------------------


@contextmanager
def no_writes() -> Iterator[None]:
    """Assert that nothing opens a file for writing while watch runs.

    Scope, stated honestly: this guards ``open`` — which is what ``pathlib``,
    ``logging``, and every ordinary write route through — and not raw
    ``os.open``/``os.write``. Cheap and always on beats thorough and switched
    off. ``io.open`` and ``builtins.open`` are the same function under two
    names, so both are swapped and both are restored.
    """
    real_open = builtins.open

    def guarded(file, mode="r", *args, **kwargs):  # type: ignore[no-untyped-def]
        if any(flag in mode for flag in "wxa+"):
            runlog.require(False, "watch mode opened a file for writing", file=str(file), mode=mode)
        return real_open(file, mode, *args, **kwargs)

    builtins.open = guarded  # type: ignore[assignment]
    io.open = guarded  # type: ignore[assignment]
    try:
        yield
    finally:
        builtins.open = real_open  # type: ignore[assignment]
        io.open = real_open  # type: ignore[assignment]


# --- reading the two sensors -------------------------------------------------


def recorded_stages(render: str) -> frozenset[str]:
    """The recorded stage ids in a ``show-status`` render — a format, not prose."""
    markers = _CHECKLIST_RE.findall(render)
    # A render with no checklist at all would leave the baseline permanently
    # empty and every watch would time out silently. That is drift in the
    # tool's output shape, not a pipeline outcome: exit 15.
    runlog.require(markers, "show-status printed no checklist block")
    return frozenset(stage for marker, stage in markers if marker == "x")


def latest_run_dir(parent: Path) -> Path | None:
    """The run directory ``LATEST`` names, or None when there is no run to watch."""
    try:
        named = Path((parent / "LATEST").read_text(encoding="utf-8").strip())
    except (OSError, ValueError):
        return None
    return named if named.is_dir() else None


def _gone(run_dir: Path, *, alive: Callable[[int], bool]) -> str | None:
    """Why the driver is no longer alive, or None while it still is."""
    pid_file = run_dir / "run.pid"
    try:
        pid = int(pid_file.read_text(encoding="utf-8").strip())
    except (OSError, ValueError):
        return f"{pid_file} is absent or unreadable, so no live driver holds this run directory"
    if alive(pid):
        return None
    return f"the driver process (pid {pid}) is no longer alive"


# --- the poll loop -----------------------------------------------------------


def watch(
    *,
    run_parent: Path,
    sensors: Sensors,
    timeout_seconds: int = DEFAULT_TIMEOUT_SECONDS,
    poll_seconds: int = DEFAULT_POLL_SECONDS,
) -> Result:
    """Poll until the run advances, the driver goes away, or the timeout expires."""
    runlog.require(timeout_seconds > 0, "watch timeout must be positive", timeout=timeout_seconds)
    runlog.require(poll_seconds > 0, "watch poll interval must be positive", poll=poll_seconds)

    status = sensors.status()
    if not status.ok:
        return Result(status.exit_code, render=status.stdout, detail=status.detail)
    baseline = recorded_stages(status.stdout)

    run_dir = latest_run_dir(run_parent)
    if run_dir is None:
        return Result(
            baton.EXIT_ENVIRONMENT,
            render=status.stdout,
            detail=(
                f"no run to watch: {run_parent / 'LATEST'} names no run directory — restore: launch the "
                f"driver ('{kb_util.DRIVER_INVOCATION} run --config <path>'), then watch",
            ),
        )

    deadline = sensors.monotonic() + timeout_seconds
    while True:
        recorded = recorded_stages(status.stdout)
        gone = _gone(run_dir, alive=sensors.alive)
        if gone is not None:
            return Result(baton.EXIT_WATCH_DRIVER_GONE, render=status.stdout, detail=(gone,), run_dir=run_dir)
        grew = recorded - baseline
        if grew:
            return Result(
                baton.EXIT_OK,
                render=status.stdout,
                detail=(f"recorded since this watch began: {', '.join(sorted(grew))}",),
                run_dir=run_dir,
            )
        if sensors.monotonic() >= deadline:
            return Result(
                baton.EXIT_WATCH_TIMEOUT,
                render=status.stdout,
                detail=(f"no stage was recorded in {timeout_seconds}s; the build is still running",),
                run_dir=run_dir,
            )
        sensors.sleep(poll_seconds)
        status = sensors.status()
        if not status.ok:
            return Result(status.exit_code, render=status.stdout, detail=status.detail, run_dir=run_dir)


# --- the mode entry point ----------------------------------------------------


def sensors_for(repo_root: Path) -> Sensors:
    """The real sensors: a non-relaying ``show-status`` and the pid probe.

    ``relay=False`` because a poll is not a stage transition — the render is
    printed once, at exit, above the baton.
    """
    return Sensors(
        status=lambda: ledger.show_status(repo_root, relay=False),
        # The lock's own liveness probe, reused rather than re-derived: a
        # second `os.kill(pid, 0)` here would be a second place to get
        # "PermissionError means alive" wrong. Worth promoting off the
        # underscore when ``runlog`` is next touched.
        alive=runlog._pid_alive,
    )


def mode(args: argparse.Namespace, ctx: baton.BatonContext) -> tuple[int, baton.BatonContext]:
    """``cli``'s ``watch`` mode: resolve the root, poll, relay the render."""
    try:
        repo_root = kb_util.find_git_root()
    except kb_util.RepoRootError as exc:
        return baton.EXIT_ENVIRONMENT, replace(ctx, detail=(str(exc),))

    _log.info(
        "watching",
        extra={"context": {"repo_root": str(repo_root), "run_parent": str(args.run_dir), "timeout": args.timeout}},
    )
    with no_writes():
        result = watch(
            run_parent=args.run_dir,
            sensors=sensors_for(repo_root),
            timeout_seconds=args.timeout,
            poll_seconds=args.poll,
        )

    if result.render:
        # The complete stdout of show-status, verbatim, above the baton.
        runlog.relay(result.render)
    return result.exit_code, replace(
        ctx,
        run_dir="" if result.run_dir is None else str(result.run_dir),
        detail=result.detail,
    )
