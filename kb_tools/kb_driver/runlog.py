"""Logging, the run-directory layout, and the boundary check.

All driver output goes through this module: JSONL to ``<run-dir>/run.log``,
tee'd to the console at the same level. Writing to a console stream directly is
permitted in the driver for exactly two things, and both live here. :func:`relay`
is stdout's — relayed tool stdout and the relay baton, written verbatim, because
a card the session must paste cannot carry a log prefix. :func:`notify` is
stderr's — a notice about the invocation itself, kept off the stream a session
pastes from. Each records its own text in the JSONL log as evidence and each
suppresses the console tee's copy, so the bytes reach a console exactly once.

Run-directory layout — the run directory is a sibling of
``.claude-temp/kb-build/``, not a subdirectory: that layout is a contract
governing build *artifacts*, while these are *evidence* nothing in the
pipeline reads::

    <repo-root>/.claude-temp/kb-driver.lock   the run lock (one per REPO)

    <parent>/                            default .claude-temp/kb-driver
      LATEST                             (at the parent)
      <run-id>/
        run.log · run.pid · exit.json
        briefs/ · calls/ · barriers/
        deviations.jsonl · cadence.jsonl

The lock is deliberately *not* under ``<parent>``. LATEST is about this run's
evidence and belongs beside it; the lock is about the repository, and anchoring
it at the run-directory parent made it per-``--run-dir`` — two invocations
passing different ``--run-dir`` (which kb-testing is required to do) would take
different locks and both proceed against one KB.

Whether a lock is live, stale or absent is one judgement — :func:`lock_state` —
read by the acquire path here and, from outside the driver, by ``kb_util``'s
read-only ``show-run-lock`` op. A caller that needs the answer asks it; a second
pid test is a second definition of a live run.

Turning a terminating signal into an ordinary unwind lives here too
(:func:`terminating_signals`), beside the directory it exists to protect: a run
killed on the default disposition writes nothing, and what it would have written
is the only account of itself it leaves.

Stdlib only.
"""

import json
import logging
import os
import re
import signal
import sys
import tempfile
import uuid
from collections.abc import Iterator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from .. import inference

_LOGGER_NAME = "kb_driver"

# Console lines carry a word in their brackets, per the [kb-build] / [card] /
# [preflight] convention, so the checklist block stays the only thing a parser
# can confuse with them.
_CONSOLE_FORMAT = "[kb-driver] %(levelname)s %(message)s"


class BoundaryError(RuntimeError):
    """A boundary check failed: a driver defect, never a pipeline outcome. Exit 15."""


class LockedError(RuntimeError):
    """Another driver run is alive in this repo. Exit 16."""

    def __init__(self, message: str, *, pid: int | None = None) -> None:
        super().__init__(message)
        self.pid = pid


#: What a shell reports for a process a signal killed: ``128 + n``.
SIGNAL_EXIT_BASE = 128

#: The signals a run turns into an unwind — the two an operator or a supervisor
#: sends. ``SIGKILL`` is absent because it cannot be caught: no code promises a
#: report after one, and listing it would read as though some did.
TERMINATING_SIGNALS: tuple[signal.Signals, ...] = (signal.SIGINT, signal.SIGTERM)


class Terminated(RuntimeError):
    """A terminating signal reached a run, raised so the run still unwinds."""

    def __init__(self, signum: int) -> None:
        super().__init__(f"terminated by signal {signum} ({signal.Signals(signum).name})")
        self.signum = signum

    @property
    def exit_code(self) -> int:
        """``128 + n``: the shell's own convention, deliberately not a driver rung.

        Neither mode's ladder has a code for this and borrowing one would state
        a verdict nothing in the build reached — a kill is not a driver defect
        and not a failed stage. An unrecognized code is what the fallback card
        exists for.
        """
        return SIGNAL_EXIT_BASE + self.signum


@contextmanager
def terminating_signals() -> Iterator[None]:
    """Raise :class:`Terminated` on a terminating signal, restoring the handlers after.

    Scoped to the run rather than installed for the process's life: outside the
    region the run directory either does not exist yet or is already written, so
    a handler standing there would only delay a death nobody learns anything
    from. Restoring is what keeps an in-process caller — the test suite — from
    inheriting a disposition it never asked for.
    """

    def _raise(signum: int, frame: object) -> None:
        raise Terminated(signum)

    previous = [(number, signal.signal(number, _raise)) for number in TERMINATING_SIGNALS]
    try:
        yield
    finally:
        for number, handler in previous:
            signal.signal(number, handler)


def logger(name: str) -> logging.Logger:
    """The driver logger for a module. Library code never configures handlers."""
    return logging.getLogger(f"{_LOGGER_NAME}.{name}")


_log = logger("runlog")


def require(condition: object, message: str, **context: object) -> None:
    """Cheap, always-on boundary check: log at ERROR and raise on violation."""
    if condition:
        return
    logger("boundary").error(message, extra={"context": context})
    raise BoundaryError(message)


# --- run directory ----------------------------------------------------------


@dataclass(frozen=True)
class RunPaths:
    """Every path the run directory contract names."""

    parent: Path
    run_id: str

    @property
    def run_dir(self) -> Path:
        return self.parent / self.run_id

    @property
    def latest(self) -> Path:
        return self.parent / "LATEST"

    @property
    def run_log(self) -> Path:
        return self.run_dir / "run.log"

    @property
    def run_pid(self) -> Path:
        return self.run_dir / "run.pid"

    @property
    def exit_json(self) -> Path:
        return self.run_dir / "exit.json"

    @property
    def briefs(self) -> Path:
        return self.run_dir / "briefs"

    @property
    def calls(self) -> Path:
        return self.run_dir / "calls"

    @property
    def values(self) -> Path:
        """Where the driver's own write-op values files land — evidence, beside the briefs.

        Not the build scratch: a values file is the exact input to one tool
        call, so it belongs with the other records of what this run asked for,
        and it outlives the restage that wipes scratch.
        """
        return self.run_dir / "values"

    @property
    def barriers(self) -> Path:
        return self.run_dir / "barriers"

    @property
    def deviations(self) -> Path:
        return self.run_dir / "deviations.jsonl"

    @property
    def cadence(self) -> Path:
        return self.run_dir / "cadence.jsonl"


#: A capture's double suffix. ``Path.stem`` strips one component, so the
#: filename grammar below is read with this spelled out rather than guessed at.
CALL_STREAM_SUFFIX = ".stream.jsonl"

#: ``<seq>-<step-id>[-reask]-a<attempt>``. The step id contains hyphens
#: (``pre.kb-root``) and so does the re-ask marker, so the only reading
#: that cannot confuse the two is one anchored at both ends — exactly the
#: reasoning ``prompt_templates.step_of`` carries for the brief grammar.
_CAPTURE_NAME = re.compile(r"^(?P<seq>\d+)-(?P<step>.+?)(?P<reask>-reask)?-a(?P<attempt>\d+)$")


def call_stream_path(paths: RunPaths, *, seq: int, label: str, attempt: int) -> Path:
    """One attempt's capture file: ``calls/<seq>-<label>-a<attempt>.stream.jsonl``.

    The grammar is written here and read back by :func:`read_cadence`, so the
    composer and the reader cannot drift apart — a rename that only edited the
    composer would leave the cadence pass silently matching nothing.
    """
    return paths.calls / f"{seq:03d}-{label}-a{attempt}{CALL_STREAM_SUFFIX}"


def _last_result(capture: Path) -> dict[str, object] | None:
    """The capture's final ``result`` event, or None when it holds none.

    The LAST one, not the first: a headless call can emit a premature result
    followed by a second init and the real one, and the first would report a
    fraction of the call's cost. A line that is not JSON is skipped rather than
    fatal — this reads evidence a wedged call left behind, and half a capture is
    the normal shape of the interesting case.
    """
    found: dict[str, object] | None = None
    with capture.open(encoding="utf-8", errors="replace") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            try:
                event = json.loads(line)
            except ValueError:
                continue
            if isinstance(event, dict) and event.get("type") == "result":
                found = event
    return found


def read_cadence(paths: RunPaths, *, stages: Mapping[str, str]) -> list[dict[str, object]]:
    """One record per capture: which step it was, and what it cost.

    ``stages`` maps step id to stage id. It is a parameter rather than an import
    because this module is the one every other imports and holds no stage
    knowledge; the sequencer, which does, supplies it.

    A capture with no ``result`` event contributes nothing: the call died in
    transport, and its duration is the watchdog's story rather than the model's.
    A capture whose name does not parse is a driver defect, but this runs on the
    way out of a finished run — it is logged and skipped, never raised, because
    an evidence pass must not be able to turn a completed build into exit 15.
    """
    records: list[dict[str, object]] = []
    for capture in sorted(paths.calls.glob(f"*{CALL_STREAM_SUFFIX}")):
        name = capture.name[: -len(CALL_STREAM_SUFFIX)]
        match = _CAPTURE_NAME.match(name)
        if match is None:
            _log.warning("capture filename does not parse", extra={"context": {"capture": capture.name}})
            continue
        try:
            event = _last_result(capture)
        except OSError as exc:
            _log.warning("capture could not be read", extra={"context": {"capture": capture.name, "error": str(exc)}})
            continue
        if event is None:
            continue
        step = match["step"]
        records.append(
            {
                "seq": int(match["seq"]),
                "step": step,
                "stage": stages.get(step),
                "attempt": int(match["attempt"]),
                "re_ask": bool(match["reask"]),
                "duration_ms": event.get("duration_ms"),
                "cost_usd": event.get("total_cost_usd"),
            }
        )
    return records


def write_cadence(paths: RunPaths, *, stages: Mapping[str, str]) -> Path:
    """Write ``cadence.jsonl`` — the run's per-call timing and cost record.

    Called once, on the way out, beside ``exit.json``: every capture is complete
    by then and the pass is one read of each. The file is always written, so
    "absent" never has to be distinguished from "the extraction did not run" —
    a run that made no calls leaves an empty one.
    """
    lines = [json.dumps(record, ensure_ascii=False) for record in read_cadence(paths, stages=stages)]
    paths.cadence.write_text("".join(f"{line}\n" for line in lines), encoding="utf-8")
    _log.debug("cadence written", extra={"context": {"records": len(lines), "path": str(paths.cadence)}})
    return paths.cadence


def new_run_id() -> str:
    """A run id that sorts chronologically and cannot collide with a same-second retry."""
    return f"{datetime.now(UTC).strftime('%Y%m%dT%H%M%S')}-{os.getpid()}"


def prepare(parent: Path, run_id: str) -> RunPaths:
    """Create the run directory tree, stamp ``run.pid``, and point ``LATEST`` at it."""
    paths = RunPaths(parent=parent, run_id=run_id)
    require(not paths.run_dir.exists(), "run directory already exists", run_dir=str(paths.run_dir))
    for directory in (paths.run_dir, paths.briefs, paths.calls, paths.barriers):
        directory.mkdir(parents=True)
    paths.run_pid.write_text(f"{os.getpid()}\n", encoding="utf-8")
    paths.latest.write_text(f"{paths.run_dir}\n", encoding="utf-8")
    return paths


def write_exit_json(
    paths: RunPaths,
    *,
    exit_code: int,
    barrier_record: Path | None = None,
    unconsumed_decisions: Sequence[str] = (),
) -> Path:
    """Record the terminal code, the barrier record path, and unconsumed decisions.

    This is what a session reads after watch mode reports the driver gone: the
    terminal code and, for a barrier, the path to the record it must paste. The
    record's own object belongs to the record, not here.
    """
    payload = {
        "exit_code": exit_code,
        "barrier_record": None if barrier_record is None else str(barrier_record),
        "unconsumed_decisions": list(unconsumed_decisions),
    }
    paths.exit_json.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return paths.exit_json


# --- the lock ---------------------------------------------------------------

#: Where the driver's scratch lives inside a consuming repo. ``steps`` names a
#: build-artifact subdirectory of the same tree; this is the tree itself, and
#: it is spelled here because ``runlog`` is the module every other one imports.
SCRATCH_DIRNAME = ".claude-temp"

LOCK_FILENAME = "kb-driver.lock"


def repo_lock_path(repo_root: Path) -> Path:
    """The run lock's anchor: one driver run per **repository**."""
    return repo_root / SCRATCH_DIRNAME / LOCK_FILENAME


def _pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        # Alive, owned by somebody else — still a live run.
        return True
    return True


#: The three answers to "does a run hold this lock?". Named because two callers
#: report them — the acquire path below, and the read-only op that answers the
#: question for a recipe about to wipe a workspace — and a spelling each would
#: be two vocabularies for one judgement.
LOCK_LIVE = "live"
LOCK_STALE = "stale"
LOCK_ABSENT = "absent"


@dataclass(frozen=True)
class LockState:
    """What a lock file says, and whether the holder it names is alive.

    ``state`` is one of :data:`LOCK_LIVE` / :data:`LOCK_STALE` /
    :data:`LOCK_ABSENT`. The holder fields carry the payload
    :func:`run_lock` wrote and are all ``None`` wherever there is nobody to
    name: an absent lock, or a stale one whose payload will not parse, which is
    a lock nothing can attribute rather than a lock with no holder.
    """

    state: str
    pid: int | None = None
    run_id: str | None = None
    started: str | None = None


def lock_state(path: Path) -> LockState:
    """Judge ``path`` — live, stale or absent — naming its holder where there is one.

    **The one definition of a holder's liveness.** The acquire path reads it
    through :func:`_holder` and every caller outside this package reads it
    directly, so a second pid test anywhere — in another module, in a shell
    recipe — is a second definition and not a second opinion.

    Reads and judges; it neither takes the lock nor clears a stale one. An
    unparseable payload is :data:`LOCK_STALE` for the reason the acquire path
    treats it as one: a lock nobody can be read out of is a lock no run can be
    shown to hold.
    """
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return LockState(LOCK_ABSENT)
    except OSError:
        return LockState(LOCK_STALE)
    try:
        record = json.loads(text)
        pid = int(record["pid"])
    except (ValueError, KeyError, TypeError):
        return LockState(LOCK_STALE)
    state = LOCK_LIVE if _pid_alive(pid) else LOCK_STALE
    return LockState(state, pid=pid, run_id=record.get("run_id"), started=record.get("started"))


def _holder(path: Path) -> int | None:
    """The live pid holding ``path``, or None if the lock is stale or unreadable."""
    state = lock_state(path)
    if state.pid is None:
        # Nothing could be read out of it. Logged here rather than in the
        # judgement itself, because this is the caller about to break the lock:
        # a read-only report of the same state is an answer, not a warning.
        _log.warning("lock file is unreadable; treating it as stale", extra={"context": {"lock": str(path)}})
    return state.pid if state.state == LOCK_LIVE else None


def _publish(path: Path, payload: str) -> bool:
    """Make ``path`` exist already holding ``payload``, or report that it exists.

    The payload is written to a private staging file first and ``os.link``
    publishes it under the lock's name; ``link`` fails with ``FileExistsError``
    if anything is there, so testing and taking the lock are one atomic act
    from any other starter's point of view. A create-then-write shape
    (``O_EXCL`` open, *then* write the pid) would leave a zero-byte lock
    visible between the two syscalls, which a second starter reads as
    "unreadable; treating it as stale", unlinks, and takes — two drivers on one
    repo.
    """
    fd, staging_name = tempfile.mkstemp(dir=path.parent, prefix=f"{path.name}.", suffix=".staging")
    staging = Path(staging_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(payload + "\n")
        os.chmod(staging, 0o644)
        try:
            os.link(staging, path)
        except FileExistsError:
            return False
        return True
    finally:
        staging.unlink(missing_ok=True)


def _clear_stale(path: Path, *, judged: str) -> bool:
    """Remove a lock judged stale — but only if it is still that same lock.

    Renaming aside and then reading is what makes this safe: a plain
    ``unlink()`` here is the check-then-act that lets two starters recovering
    one stale lock each delete the other's freshly published one. If
    what moved is not the content that was judged stale, a live holder arrived
    in the window and it is put straight back.
    """
    breaker = path.with_name(f"{path.name}.stale-{uuid.uuid4().hex}")
    try:
        os.rename(path, breaker)
    except OSError:
        return True  # somebody else cleared it first; the acquire retry decides
    moved = breaker.read_text(encoding="utf-8")
    if moved.strip() != judged.strip():
        os.rename(breaker, path)
        _log.warning(
            "the lock changed while it was being judged stale; leaving it to its holder",
            extra={"context": {"lock": str(path)}},
        )
        return False
    breaker.unlink(missing_ok=True)
    _log.warning("removed a stale run lock", extra={"context": {"lock": str(path)}})
    return True


def _read(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except OSError:
        return ""


@contextmanager
def run_lock(path: Path, *, run_id: str) -> Iterator[Path]:
    """Hold ``path`` for the run; a live holder raises LockedError (exit 16).

    ``path`` is the repository's lock — :func:`repo_lock_path`. It is passed in
    rather than derived because the anchor is a decision about *scope*, and the
    module that resolves the repo root is the one that should make it.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    # A token, not the pid: pids recycle, and run_id embeds one. This is what
    # release compares against, so that "is this still my lock" is answerable
    # without trusting either.
    token = uuid.uuid4().hex
    payload = json.dumps(
        {
            "pid": os.getpid(),
            "run_id": run_id,
            "token": token,
            "started": datetime.now(UTC).isoformat(timespec="seconds"),
        },
        ensure_ascii=False,
    )

    for attempt in (1, 2):
        if _publish(path, payload):
            break
        judged = _read(path)
        pid = _holder(path)
        if pid is not None:
            raise LockedError(f"another driver run is live: pid {pid} holds {path}", pid=pid)
        if attempt == 2 or not _clear_stale(path, judged=judged):
            # Lost the race to another starter that has since gone stale too,
            # or a live holder arrived mid-recovery; refusing beats spinning.
            raise LockedError(f"{path} is contended by repeatedly stale holders")

    try:
        yield path
    finally:
        _release(path, token=token)


def _release(path: Path, *, token: str) -> None:
    """Unlink the lock only while it still names this run.

    Releasing by path would delete whatever happened to be there — a run whose
    lock had already been recovered as stale by a *live* successor would delete
    that successor's lock on its way out, and a third starter could then acquire
    freely.
    """
    try:
        record = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        _log.warning("the run lock was gone or unreadable at release", extra={"context": {"lock": str(path)}})
        return
    if not isinstance(record, dict) or record.get("token") != token:
        _log.warning(
            "the run lock is no longer this run's; leaving it for its holder",
            extra={"context": {"lock": str(path), "holder_pid": str(record.get("pid"))}},
        )
        return
    path.unlink(missing_ok=True)


# --- logging ----------------------------------------------------------------


class _JsonlFormatter(logging.Formatter):
    """One JSON object per line: the run log is evidence, read by machines."""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, object] = {
            "ts": datetime.fromtimestamp(record.created, UTC).isoformat(timespec="milliseconds"),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        context = getattr(record, "context", None)
        if isinstance(context, dict) and context:
            payload["context"] = {key: str(value) for key, value in context.items()}
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=False)


#: Set on a record whose text its writer has already put on a console stream.
#: The console tee drops those records, so the bytes appear exactly once. The
#: key keeps the spelling it was written under: run logs on disk already carry
#: it, and a reader of one is reading a format rather than a name in this file.
CONSOLE_WRITTEN = "relay"


def _not_relayed(record: logging.LogRecord) -> bool:
    """Keep out of the console tee what its own writer already put on a console stream."""
    context = getattr(record, "context", None)
    return not (isinstance(context, dict) and context.get(CONSOLE_WRITTEN))


def configure(*, run_log: Path, level: str) -> logging.Logger:
    """Attach the JSONL file handler and the console tee. Called once, from ``cli``.

    Both logger trees the run writes get them: this driver's, and the shared
    inference layer's, which configures no handler of its own — a call's spawn,
    kill and classification lines are this run's evidence wherever the code
    that emits them lives.
    """
    file_handler = logging.FileHandler(run_log, encoding="utf-8")
    file_handler.setFormatter(_JsonlFormatter())
    console = logging.StreamHandler(sys.stdout)
    console.setFormatter(logging.Formatter(_CONSOLE_FORMAT))
    console.addFilter(_not_relayed)

    driver_log = logging.getLogger(_LOGGER_NAME)
    for log in (driver_log, logging.getLogger(inference.LOGGER_NAME)):
        log.setLevel(level)
        log.propagate = False
        for handler in list(log.handlers):
            log.removeHandler(handler)
            handler.close()
        log.addHandler(file_handler)
        log.addHandler(console)

    return driver_log


def relay(text: str) -> None:
    """Write verbatim relayed output — tool stdout or a baton — to stdout.

    The one sanctioned bypass of the console formatter, and the only place the
    driver writes to stdout directly. The same text is recorded in the run log
    at INFO; the console tee drops that copy so the bytes appear exactly once.
    """
    sys.stdout.write(text if text.endswith("\n") else text + "\n")
    sys.stdout.flush()
    _log.info(text, extra={"context": {CONSOLE_WRITTEN: True}})


def notify(text: str) -> None:
    """Write an operator notice to stderr and record it in the run log at WARNING.

    Stderr's counterpart to :func:`relay`, and the stream is the point.
    Stdout carries what a session pastes — a ledger render, a baton, a failing
    tool's report — so a remark about the *invocation* goes to the other stream
    rather than into the middle of a block somebody is about to copy. The
    console tee drops its copy for :func:`relay`'s reason: the bytes have
    already reached a console.
    """
    sys.stderr.write(text if text.endswith("\n") else text + "\n")
    sys.stderr.flush()
    _log.warning(text, extra={"context": {CONSOLE_WRITTEN: True}})
