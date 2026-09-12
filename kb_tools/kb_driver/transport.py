"""Spawn, stream capture, watchdog, timeout, process-group kill/reap.

Mechanism only. This module knows how to start one ``claude`` process, record
everything it says, notice that it has stopped saying anything, and take its
whole process group down when a bound expires. It knows nothing about stages,
retries, or what a well-formed answer looks like: it classifies an outcome and
returns it. Retry policy, contract validation, and the one re-ask are
``call.py``'s — the transport/policy split.

Three behaviors here are **version-observed**, not documented contracts —
observed at CLI 2.1.220, and all three must be re-probed against any CLI
upgrade:

* **Capture reads to stream end, never to the first ``result``.** A seatless
  session that dispatches asynchronously emits a premature ``result: success``
  whose text is a waiting-for-the-agent placeholder, then a *second*
  ``init``/``result`` pair. Stopping at the first ``result`` records a
  completed wave with zero member artifacts — a false green. So the parse
  takes the **last** ``result``, and more than one ``init`` in a call is a
  discipline violation: logged at ERROR and surfaced on the result
  (:attr:`CallResult.discipline_violation`) for the step's contract check to
  fail on, never silently absorbed.
* **A positional argv prompt merges with the stdin brief** — exit 0, no
  warning, both texts in the turn. :func:`build_argv` therefore refuses to
  construct one, and :func:`require_no_positional_prompt` is available to
  anything that assembles argv another way. A brief travels on stdin, by path;
  there is no parameter on this module through which brief text could
  reach argv.
* **The error classifier is the zero-event test.** ``exit != 0`` with no
  stream event ever emitted is the CLI rejecting the invocation — a bad flag,
  a bad value, a fatal flag combination — which is a config-class failure that
  must never be retried (exit 13). Anything after an ``init`` is
  transport or contract territory. :attr:`Outcome.retryable` carries that
  distinction; the exit code is the caller's to choose, since this module sits
  below ``baton.py``.

The driver never passes ``--model``: an explicit ``--model`` overrides a
seat's frontmatter pin, so "the pin is authoritative" holds only for as long
as the flag is omitted. A boundary check enforces the
omission at spawn, where a config-supplied ``command`` prefix could otherwise
smuggle one in.

Only two things in the stream are parsed: the last ``result`` event and the
arrival of events (the watchdog measures silence as time since the last line,
which is the same signal as the last event's timestamp and costs nothing).
Everything else is bytes appended verbatim to the call's capture file.

Stdlib only.
"""

import json
import os
import queue
import signal
import subprocess
import threading
import time
from collections.abc import Iterator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Protocol

from . import runlog

_log = runlog.logger("transport")

# The flags print-mode stream capture requires. --verbose is load-bearing: its
# absence is a fatal combination with --output-format stream-json (observed).
STREAM_FLAGS: tuple[str, ...] = ("--output-format", "stream-json", "--verbose")

# Flags whose next token is a value rather than a positional prompt.
_VALUE_FLAGS = frozenset({"--agent", "--permission-mode", "--output-format"})

# SIGTERM to the group, then SIGKILL after this long.
DEFAULT_KILL_GRACE_SECONDS = 10.0

# How often the kill path re-asks whether anything is left in the group.
_GROUP_POLL_SECONDS = 0.05

# How long the post-expiry path waits for the capture pump and the stderr
# reader to come back once the group has been signalled. Bounds, not
# expectations: a reader still blocked after this is abandoned rather than
# waited on, because the thing it is blocked on is an orphan's lifetime.
_READER_JOIN_SECONDS = 5.0


class Outcome(Enum):
    """How a call ended, before any policy is applied."""

    OK = "ok"
    CLI_REJECTION = "cli-rejection"  # zero events + nonzero exit: config-class, never retried
    TRANSPORT_FAILURE = "transport-failure"  # died or ended incomplete after speaking
    SILENCE = "silence-watchdog"
    TIMEOUT = "total-timeout"

    @property
    def retryable(self) -> bool:
        """A CLI rejection retried is three identical failures and a misleading exit."""
        return self not in (Outcome.OK, Outcome.CLI_REJECTION)


@dataclass(frozen=True)
class Bounds:
    """The two per-call bounds. Both come from config; neither has a default here."""

    silence_seconds: float
    total_seconds: float


@dataclass(frozen=True)
class CallResult:
    """One attempt's classified outcome and the evidence behind it."""

    outcome: Outcome
    exit_status: int
    stream_path: Path
    event_count: int
    init_count: int
    unparsed_lines: int
    result_event: Mapping[str, object] | None
    stderr: str
    duration_seconds: float

    @property
    def ok(self) -> bool:
        return self.outcome is Outcome.OK

    @property
    def result_text(self) -> str:
        """The last ``result`` event's text, or empty if the stream carried none."""
        value = None if self.result_event is None else self.result_event.get("result")
        return value if isinstance(value, str) else ""

    @property
    def discipline_violation(self) -> bool:
        """More than one ``init``: the session dispatched asynchronously."""
        return self.init_count > 1


class Invocation(Protocol):
    """A live call. The seam's handle: an iterator of stdout lines, plus a way to stop it."""

    def lines(self) -> Iterator[str]:
        """Yield stdout lines as they arrive. Blocking; ends at stream end."""

    def kill(self) -> None:
        """Take the whole process group down and reap it."""

    def wait(self) -> int:
        """The exit status, negative for a signal, once the stream has ended."""

    def stderr_text(self) -> str:
        """Everything the call wrote to stderr."""


class Invoker(Protocol):
    """The ``Invoker.run(argv, cwd, brief)`` seam.

    Realized as a handle rather than a bare iterator because the watchdog needs
    a kill alongside the lines. :class:`SubprocessInvoker` is the real one;
    ``replay.ReplayInvoker`` is the no-inference one, and both are read through the
    same capture path in :func:`invoke`.
    """

    def run(
        self,
        *,
        argv: Sequence[str],
        cwd: Path,
        env: Mapping[str, str],
        brief_path: Path,
    ) -> Invocation:
        """Start the call with the brief at ``brief_path`` on stdin."""


# --- argv -------------------------------------------------------------------


def require_no_positional_prompt(tokens: Sequence[str]) -> None:
    """Refuse a positional argv prompt: it merges with the stdin brief.

    Checks the driver-added tail, not the config-supplied ``command`` prefix,
    which is opaque (a wrapper's own subcommands are legitimate). Only driver
    code can construct the hazard, so only driver-constructed tokens are
    checked. Violation is a boundary error — exit 15.
    """
    expecting_value = False
    for token in tokens:
        if expecting_value:
            expecting_value = False
            continue
        if token.startswith("-"):
            expecting_value = token in _VALUE_FLAGS
            continue
        runlog.require(
            False,
            "a positional argv prompt merges with the stdin brief instead of replacing it",
            token=token[:120],
        )


def build_argv(*, command: Sequence[str], permission_mode: str, agent: str | None = None) -> list[str]:
    """Assemble the call's argv. ``agent`` names a SINGLE's seat; a WAVE is seatless.

    The brief is never here — it is written to the run directory and delivered
    on stdin. No ``--model``, ever.
    """
    runlog.require(bool(command), "config [claude] command is empty")
    runlog.require(bool(permission_mode), "permission_mode is empty; config resolves it or defaults it")
    runlog.require("--model" not in command, "the driver never passes --model", command=" ".join(command))

    tail = ["-p"]
    if agent is not None:
        runlog.require(bool(agent), "a SINGLE's seat name is empty")
        tail += ["--agent", agent]
    tail += ["--permission-mode", permission_mode, *STREAM_FLAGS]
    require_no_positional_prompt(tail)

    return [*command, *tail]


# --- the real seam ----------------------------------------------------------


class SubprocessInvoker:
    """One ``claude`` process in its own session, brief on stdin, killed by group.

    ``start_new_session=True`` plus ``killpg`` is the whole point: killing the
    parent ``claude`` does not reap the workers its Agent tool dispatched.
    stdout and stderr are separate pipes with a reader on each, so a
    chatty stderr cannot deadlock the capture.
    """

    def __init__(self, *, kill_grace_seconds: float = DEFAULT_KILL_GRACE_SECONDS) -> None:
        self._kill_grace_seconds = kill_grace_seconds

    def run(
        self,
        *,
        argv: Sequence[str],
        cwd: Path,
        env: Mapping[str, str],
        brief_path: Path,
    ) -> "_ChildInvocation":
        # The brief is handed over as an open file rather than written down the
        # pipe: it is already on disk, and a 46 KB brief written to a
        # pipe nobody is draining yet is a deadlock waiting for a slow start.
        brief = brief_path.open("r", encoding="utf-8")
        try:
            child = subprocess.Popen(
                list(argv),
                cwd=cwd,
                env={**os.environ, **env},
                stdin=brief,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
                errors="replace",
                bufsize=1,
                start_new_session=True,
            )
        finally:
            brief.close()

        _log.debug(
            "spawned a call",
            extra={"context": {"pid": child.pid, "cwd": str(cwd), "argv": " ".join(argv)}},
        )
        return _ChildInvocation(child, kill_grace_seconds=self._kill_grace_seconds)


class _ChildInvocation:
    """The live ``claude`` process behind :class:`SubprocessInvoker`."""

    def __init__(self, child: subprocess.Popen, *, kill_grace_seconds: float) -> None:
        self._child = child
        self._kill_grace_seconds = kill_grace_seconds
        # Captured now, while the child is certainly unreaped: once ``wait()``
        # has collected it, ``os.getpgid(pid)`` raises and the group is
        # unaddressable. ``start_new_session=True`` makes this the child's own
        # group, so it is also just the pid — but reading it is what says so.
        try:
            self._pgid = os.getpgid(child.pid)
        except ProcessLookupError:  # pragma: no cover - the child cannot be reaped yet
            self._pgid = child.pid
        self._stdout_drained = threading.Event()
        self._stderr: list[str] = []
        self._stderr_reader = threading.Thread(target=self._drain_stderr, daemon=True)
        self._stderr_reader.start()

    def _drain_stderr(self) -> None:
        if self._child.stderr is not None:
            self._stderr.extend(self._child.stderr)

    def lines(self) -> Iterator[str]:
        if self._child.stdout is None:  # pragma: no cover - stdout is always a pipe here
            self._stdout_drained.set()
            return
        try:
            yield from self._child.stdout
        finally:
            self._stdout_drained.set()

    def _signal_group(self, sig: int) -> None:
        try:
            os.killpg(self._pgid, sig)
        except ProcessLookupError:
            # Already gone; nothing to signal. Reaping still happens in wait().
            pass

    def _group_is_alive(self) -> bool:
        """Is anything left in the call's process group?

        ``poll()`` first, because an unreaped parent is still a member of its
        own group and would read as a live group forever.
        """
        self._child.poll()
        try:
            os.killpg(self._pgid, 0)
        except ProcessLookupError:
            return False
        except PermissionError:  # pragma: no cover - needs a foreign-owned recycled pgid
            return True
        return True

    def _await_group_exit(self, timeout: float) -> bool:
        deadline = time.monotonic() + timeout
        while self._group_is_alive():
            if time.monotonic() >= deadline:
                return False
            time.sleep(_GROUP_POLL_SECONDS)
        return True

    def kill(self) -> None:
        """SIGTERM the group, then SIGKILL it after the grace period.

        There is deliberately no ``poll()`` guard on the way in. The parent
        exiting first is the *normal* shape of the failure this kill exists
        for — a seatless session dispatches asynchronously, emits its
        premature ``result`` and returns, while the worker it dispatched holds
        the inherited stdout pipe open (see this module's docstring). A guard
        reading "the parent is gone, so there is nothing to kill" skips
        ``killpg`` exactly when the group is all that is left. The only thing
        that means "nothing to signal" is ``ProcessLookupError`` on the group
        itself, and the grace period is measured against the **group**
        emptying rather than against the parent, for the same reason.
        """
        _log.warning(
            "killing the call's process group",
            extra={
                "context": {
                    "pid": self._child.pid,
                    "pgid": self._pgid,
                    "parent_exited": self._child.poll() is not None,
                    "grace_seconds": self._kill_grace_seconds,
                }
            },
        )
        self._signal_group(signal.SIGTERM)
        if self._await_group_exit(self._kill_grace_seconds):
            return
        _log.error(
            "the process group ignored SIGTERM; sending SIGKILL",
            extra={"context": {"pid": self._child.pid, "pgid": self._pgid}},
        )
        self._signal_group(signal.SIGKILL)
        self._await_group_exit(self._kill_grace_seconds)

    def wait(self) -> int:
        status = self._child.wait()
        self._stderr_reader.join(timeout=_READER_JOIN_SECONDS)
        self._close_streams()
        return status

    def _close_streams(self) -> None:
        """Close the pipes, but never one a reader thread is still blocked inside.

        ``BufferedReader.close()`` takes the same lock ``readline()`` holds, so
        closing stdout while the capture pump sits in it blocks for the
        orphan's whole lifetime — which is how a 2-second silence bound
        returned in 25 seconds. Both readers are daemon threads on a
        process-scoped pipe; leaving the descriptor to the interpreter is the
        bounded choice, and the abandonment is logged rather than silent.
        """
        pending = []
        if self._stdout_drained.is_set():
            if self._child.stdout is not None:
                self._child.stdout.close()
        else:
            pending.append("stdout")
        if not self._stderr_reader.is_alive():
            if self._child.stderr is not None:
                self._child.stderr.close()
        else:
            pending.append("stderr")
        if pending:
            _log.error(
                "a reader is still blocked on the call's pipe after the kill; abandoning it unclosed",
                extra={"context": {"pid": self._child.pid, "pgid": self._pgid, "streams": ",".join(pending)}},
            )

    def stderr_text(self) -> str:
        return "".join(self._stderr)


# --- one call at a time -------------------------------------------------------

_spawn_lock = threading.Lock()
_call_is_live = False


@contextmanager
def _single_flight() -> Iterator[None]:
    """Exactly one ``claude`` subprocess at a time. Parallelism lives inside a wave."""
    global _call_is_live
    with _spawn_lock:
        runlog.require(not _call_is_live, "a call would be spawned while another is still live")
        _call_is_live = True
    try:
        yield
    finally:
        with _spawn_lock:
            _call_is_live = False


# --- capture ----------------------------------------------------------------


class _StreamState:
    """What the capture parses: the last ``result``, and how many ``init``s went by."""

    def __init__(self) -> None:
        self.events = 0
        self.inits = 0
        self.unparsed = 0
        self.result: Mapping[str, object] | None = None

    def record(self, line: str) -> None:
        text = line.strip()
        if not text:
            return
        try:
            event = json.loads(text)
        except json.JSONDecodeError:
            self.unparsed += 1
            return
        if not isinstance(event, dict):
            self.unparsed += 1
            return

        self.events += 1
        kind = event.get("type")
        if kind == "system" and event.get("subtype") == "init":
            self.inits += 1
        elif kind == "result":
            # Last one wins: the first result can be a premature placeholder.
            self.result = event


def invoke(
    *,
    invoker: Invoker,
    argv: Sequence[str],
    cwd: Path,
    brief_path: Path,
    stream_path: Path,
    bounds: Bounds,
    env: Mapping[str, str] | None = None,
) -> CallResult:
    """Run one call to completion or to a bound, and classify how it ended.

    ``stream_path`` is the attempt's capture file
    (``calls/<seq>-<step>-a<attempt>.stream.jsonl``); every line the call emits
    is appended to it verbatim and flushed, so a wedged call's evidence is on
    disk while it is still wedged.
    """
    runlog.require(cwd.is_dir(), "the call's cwd does not exist", cwd=str(cwd))
    runlog.require(brief_path.is_file(), "the composed brief is not on disk", brief=str(brief_path))
    runlog.require(brief_path.stat().st_size > 0, "the composed brief is empty", brief=str(brief_path))
    runlog.require(stream_path.parent.is_dir(), "the capture directory does not exist", stream=str(stream_path))
    runlog.require("--model" not in argv, "the driver never passes --model", argv=" ".join(argv))
    runlog.require(bounds.silence_seconds > 0 and bounds.total_seconds > 0, "call bounds must be positive")

    with _single_flight():
        invocation = invoker.run(argv=argv, cwd=cwd, env=dict(env or {}), brief_path=brief_path)
        return _capture(invocation, stream_path=stream_path, bounds=bounds)


def _pump(invocation: Invocation, sink: "queue.Queue[str | None]") -> None:
    """Move the call's lines onto a queue so the watchdog can time the gaps between them."""
    try:
        for line in invocation.lines():
            sink.put(line)
    finally:
        sink.put(None)


def _capture(invocation: Invocation, *, stream_path: Path, bounds: Bounds) -> CallResult:
    started = time.monotonic()
    deadline = started + bounds.total_seconds
    sink: "queue.Queue[str | None]" = queue.Queue()
    pump = threading.Thread(target=_pump, args=(invocation, sink), daemon=True)
    pump.start()

    state = _StreamState()
    expired: Outcome | None = None

    with stream_path.open("a", encoding="utf-8") as capture:

        def write(line: str) -> None:
            capture.write(line if line.endswith("\n") else line + "\n")
            capture.flush()
            state.record(line)

        while True:
            budget = min(bounds.silence_seconds, deadline - time.monotonic())
            if budget <= 0:
                expired = Outcome.TIMEOUT
                break
            try:
                line = sink.get(timeout=budget)
            except queue.Empty:
                expired = Outcome.TIMEOUT if time.monotonic() >= deadline else Outcome.SILENCE
                break
            if line is None:
                break
            write(line)

        if expired is not None:
            _log.error(
                "call bound expired; killing the process group",
                extra={
                    "context": {
                        "bound": expired.value,
                        "silence_seconds": bounds.silence_seconds,
                        "total_seconds": bounds.total_seconds,
                        "stream": str(stream_path),
                    }
                },
            )
            invocation.kill()
            pump.join(timeout=_READER_JOIN_SECONDS)
            if pump.is_alive():
                # The kill did not free the pipe. Waiting further would make the
                # bound track whatever still holds it, which is the defect the
                # bound exists to prevent; the pump is a daemon and is left.
                _log.error(
                    "the capture pump is still blocked on the call's stdout after the kill",
                    extra={"context": {"bound": expired.value, "stream": str(stream_path)}},
                )
            # Whatever the call managed to say before the kill is still evidence.
            while True:
                try:
                    line = sink.get_nowait()
                except queue.Empty:
                    break
                if line is not None:
                    write(line)

    status = invocation.wait()
    duration = time.monotonic() - started

    if expired is not None:
        outcome = expired
    elif status != 0 and state.events == 0:
        # The CLI rejected the argv before making a call: config-class, never retried.
        outcome = Outcome.CLI_REJECTION
    elif status != 0:
        outcome = Outcome.TRANSPORT_FAILURE
    elif state.result is None:
        outcome = Outcome.TRANSPORT_FAILURE
    else:
        outcome = Outcome.OK

    result = CallResult(
        outcome=outcome,
        exit_status=status,
        stream_path=stream_path,
        event_count=state.events,
        init_count=state.inits,
        unparsed_lines=state.unparsed,
        result_event=state.result,
        stderr=invocation.stderr_text(),
        duration_seconds=duration,
    )

    if result.discipline_violation:
        _log.error(
            "more than one init event in one call: the session dispatched asynchronously",
            extra={"context": {"stream": str(stream_path), "inits": state.inits, "events": state.events}},
        )
    if outcome is Outcome.CLI_REJECTION:
        _log.error(
            "the CLI rejected the invocation; no stream event was ever emitted",
            extra={"context": {"exit_status": status, "stderr": result.stderr.strip()[:400]}},
        )
    _log.info(
        "call finished",
        extra={
            "context": {
                "outcome": outcome.value,
                "exit_status": status,
                "events": state.events,
                "duration_seconds": f"{duration:.3f}",
                "stream": str(stream_path),
            }
        },
    )
    return result
