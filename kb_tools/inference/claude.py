"""One headless ``claude`` call, as a Python function.

Named parameters in, the response text and a result code out. Nothing here
knows what a KB is, what a seat is, or that this repository exists: the only
thing it is bound to is the ``claude`` CLI's own shape, and it does not pretend
otherwise. A harness-agnostic dispatch layer would be a different module with a
different argument vocabulary; this one spells ``--agent`` and
``--permission-mode`` because that is what it drives.

**Not a full CLI binding.** The parameters below are the options that have been
needed, and no others. Adding one is two lines — a keyword parameter and the
flag pair :func:`_build_argv` appends — so the set stays small rather than
being completed in advance against callers that do not exist.

**It does not promise one prompt, one response.** A prompt may instruct further
agentic dispatch, tool use, or a long interaction, and the process runs until
it stops speaking or a bound expires — so a caller reasoning about cost or
latency as though this were a single completion is reasoning about a different
function. What is bounded is wall-clock, by ``silence_seconds`` and
``total_seconds``; what is not bounded is what the model spends inside them.

**Four behaviours are preserved from the driver's transport because each was
paid for once already**, and each is commented at its site:

* the prompt reaches the process **on stdin, never on argv** — a positional
  argv prompt does not replace the stdin one, it merges with it, at exit 0 and
  with no warning;
* ``start_new_session=True`` plus ``killpg`` — killing the parent ``claude``
  does not reap the workers its Agent tool dispatched;
* separate stdout and stderr readers — one pipe drained while the other fills
  is a deadlock;
* SIGTERM then SIGKILL after a grace period, measured against the **group**
  emptying rather than the parent exiting.

**Three differences from that transport, deliberate.** More than one ``init``
event is not a violation here — the driver's step model requires one call to be
one turn, and this API explicitly does not, so a second ``init`` is logged and
returned as an ordinary success. There is no one-call-at-a-time lock: that is a
driver policy, and a caller wanting it owns it. The capture file is optional,
where the driver always has a run directory to put one in.

Reading the stream to its end, and taking the **last** ``result`` event, is not
optional: a session that dispatches asynchronously emits a premature
``result`` whose text is a waiting-for-the-agent placeholder and then a second
``init``/``result`` pair, so stopping at the first ``result`` returns the
placeholder as the answer.

Stdlib only.
"""

import json
import logging
import os
import queue
import signal
import subprocess
import tempfile
import threading
import time
from collections.abc import Iterator, Mapping, Sequence
from contextlib import ExitStack
from enum import Enum
from pathlib import Path
from typing import Protocol, TextIO

_log = logging.getLogger(__name__)

#: The command the call is spawned through. A list rather than a string so a
#: wrapper ("mise", "exec", "claude") is expressible without a shell.
DEFAULT_COMMAND: tuple[str, ...] = ("claude",)

#: Headless has nobody to answer a permission prompt, and a mode that gates a
#: tool the prompt needs wedges the call until a bound expires. What a call may
#: do is bounded by the environment it runs in instead.
DEFAULT_PERMISSION_MODE = "bypassPermissions"

#: The flags print-mode stream capture requires. ``--verbose`` is load-bearing:
#: its absence is a fatal combination with ``--output-format stream-json``.
STREAM_FLAGS: tuple[str, ...] = ("--output-format", "stream-json", "--verbose")

#: The outer edge of "something is wrong", not an expectation: the same pair
#: the driver defaults a single call to.
DEFAULT_SILENCE_SECONDS = 600.0
DEFAULT_TOTAL_SECONDS = 1800.0

#: SIGTERM to the group, then SIGKILL after this long.
DEFAULT_KILL_GRACE_SECONDS = 10.0

# How often the kill path re-asks whether anything is left in the group.
_GROUP_POLL_SECONDS = 0.05

# How long the post-kill path waits for the readers to come back. A bound, not
# an expectation: a reader still blocked after this is abandoned rather than
# waited on, because what it is blocked on is an orphan's lifetime.
_READER_JOIN_SECONDS = 5.0


class Outcome(Enum):
    """How a call ended. The second half of every return this module makes."""

    OK = "ok"
    CLI_REJECTION = "cli-rejection"  # zero events + nonzero exit: the invocation was refused
    TRANSPORT_FAILURE = "transport-failure"  # died, or ended without a result, after speaking
    SILENCE = "silence-watchdog"
    TIMEOUT = "total-timeout"

    @property
    def ok(self) -> bool:
        return self is Outcome.OK

    @property
    def retryable(self) -> bool:
        """A rejected invocation retried is three identical failures and a misleading diagnosis.

        Zero stream events with a nonzero exit is the CLI refusing the argv — a
        bad flag, a bad value, a fatal combination — which is a configuration
        fault no repetition resolves. Everything after an ``init`` may be
        transient.
        """
        return self not in (Outcome.OK, Outcome.CLI_REJECTION)


class Invocation(Protocol):
    """A live call: an iterator of stdout lines, plus a way to stop it."""

    def lines(self) -> Iterator[str]:
        """Yield stdout lines as they arrive. Blocking; ends at stream end."""

    def kill(self) -> None:
        """Take the whole process group down and reap it."""

    def wait(self) -> int:
        """The exit status, negative for a signal, once the stream has ended."""

    def stderr_text(self) -> str:
        """Everything the call wrote to stderr."""


class Invoker(Protocol):
    """The seam that makes this testable without a model.

    :class:`SubprocessInvoker` is the real one. A test supplies its own,
    returning scripted stream-json lines, and every line below the seam —
    capture, watchdog, classification — runs exactly as it does in production.
    The prompt crosses this seam as **text** rather than as a path, so a
    substitute can see what was asked without a file having to exist.
    """

    def run(
        self,
        *,
        argv: Sequence[str],
        cwd: Path,
        env: Mapping[str, str],
        prompt: str,
    ) -> Invocation:
        """Start the call with ``prompt`` on stdin."""


def _require(condition: object, message: str) -> None:
    """A cheap always-on check at this module's one public boundary."""
    if condition:
        return
    _log.error("headless call refused: %s", message)
    raise ValueError(message)


def _build_argv(*, command: Sequence[str], permission_mode: str, agent: str | None) -> list[str]:
    """Assemble the call's argv.

    **The prompt is not here and cannot be.** There is no parameter on this
    function through which prompt text could reach argv, which is the whole
    defence: a positional argv prompt merges with the stdin one instead of
    replacing it, and both texts reach the turn at exit 0 with no warning.
    """
    tail = ["-p"]
    if agent is not None:
        tail += ["--agent", agent]
    tail += ["--permission-mode", permission_mode, *STREAM_FLAGS]
    return [*command, *tail]


class SubprocessInvoker:
    """One ``claude`` process in its own session, prompt on stdin, killed by group."""

    def __init__(self, *, kill_grace_seconds: float = DEFAULT_KILL_GRACE_SECONDS) -> None:
        self._kill_grace_seconds = kill_grace_seconds

    def run(
        self,
        *,
        argv: Sequence[str],
        cwd: Path,
        env: Mapping[str, str],
        prompt: str,
    ) -> "_ChildInvocation":
        # The prompt is spooled to an unnamed temporary file and handed over as
        # an open descriptor rather than written down a pipe: a prompt larger
        # than the pipe buffer, written before the child has started reading,
        # deadlocks against a child that is meanwhile blocked writing stdout.
        # It is also why nothing has to name a file: the spool has no path.
        with tempfile.TemporaryFile("w+", encoding="utf-8") as spool:
            spool.write(prompt)
            spool.flush()
            spool.seek(0)
            child = subprocess.Popen(
                list(argv),
                cwd=cwd,
                env={**os.environ, **env},
                stdin=spool,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
                errors="replace",
                bufsize=1,
                # Killing the parent does not reap the workers its Agent tool
                # dispatched; its own session is what makes the group killable.
                start_new_session=True,
            )
        _log.debug("spawned a headless call: pid=%d cwd=%s argv=%s", child.pid, cwd, " ".join(argv))
        return _ChildInvocation(child, kill_grace_seconds=self._kill_grace_seconds)


class _ChildInvocation:
    """The live ``claude`` process behind :class:`SubprocessInvoker`."""

    def __init__(self, child: subprocess.Popen[str], *, kill_grace_seconds: float) -> None:
        self._child = child
        self._kill_grace_seconds = kill_grace_seconds
        # Captured now, while the child is certainly unreaped: once ``wait()``
        # has collected it, ``os.getpgid(pid)`` raises and the group is
        # unaddressable.
        try:
            self._pgid = os.getpgid(child.pid)
        except ProcessLookupError:  # pragma: no cover - the child cannot be reaped yet
            self._pgid = child.pid
        self._stdout_drained = threading.Event()
        self._stderr: list[str] = []
        # stdout and stderr are separate pipes with a reader on each: a chatty
        # stderr filling its buffer while nobody drains it wedges the child
        # mid-write, and the capture never sees another line.
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
        for — a session that dispatched asynchronously returns while the worker
        it dispatched holds the inherited stdout pipe open — so a guard reading
        "the parent is gone, there is nothing to kill" would skip ``killpg``
        exactly when the group is all that is left. The grace period is
        measured against the group emptying for the same reason.
        """
        _log.warning(
            "killing the call's process group: pid=%d pgid=%d parent_exited=%s grace=%.1fs",
            self._child.pid,
            self._pgid,
            self._child.poll() is not None,
            self._kill_grace_seconds,
        )
        self._signal_group(signal.SIGTERM)
        if self._await_group_exit(self._kill_grace_seconds):
            return
        _log.error("the process group ignored SIGTERM; sending SIGKILL: pgid=%d", self._pgid)
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
        closing stdout while the pump sits in it blocks for the orphan's whole
        lifetime — which is how a 2-second bound returns in 25 seconds. Both
        readers are daemon threads on a process-scoped pipe; leaving the
        descriptor to the interpreter is the bounded choice, and the
        abandonment is logged rather than silent.
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
                "a reader is still blocked on the call's pipe after the kill; abandoning it unclosed: pgid=%d streams=%s",
                self._pgid,
                ",".join(pending),
            )

    def stderr_text(self) -> str:
        return "".join(self._stderr)


class _StreamState:
    """What the capture parses: the last ``result``, and how many ``init``s went by."""

    def __init__(self) -> None:
        self.events = 0
        self.inits = 0
        self.result: Mapping[str, object] | None = None

    def record(self, line: str) -> None:
        text = line.strip()
        if not text:
            return
        try:
            event = json.loads(text)
        except json.JSONDecodeError:
            return
        if not isinstance(event, dict):
            return

        self.events += 1
        kind = event.get("type")
        if kind == "system" and event.get("subtype") == "init":
            self.inits += 1
        elif kind == "result":
            # Last one wins: the first result can be a premature placeholder.
            self.result = event

    @property
    def result_text(self) -> str:
        value = None if self.result is None else self.result.get("result")
        return value if isinstance(value, str) else ""


def _pump(invocation: Invocation, sink: "queue.Queue[str | None]") -> None:
    """Move the call's lines onto a queue so the watchdog can time the gaps between them."""
    try:
        for line in invocation.lines():
            sink.put(line)
    finally:
        sink.put(None)


def _capture(
    invocation: Invocation,
    *,
    capture_path: Path | None,
    silence_seconds: float,
    total_seconds: float,
) -> tuple[_StreamState, Outcome | None]:
    """Read the stream to its end or to a bound, killing the group when one expires."""
    deadline = time.monotonic() + total_seconds
    sink: "queue.Queue[str | None]" = queue.Queue()
    pump = threading.Thread(target=_pump, args=(invocation, sink), daemon=True)
    pump.start()

    state = _StreamState()
    expired: Outcome | None = None

    with ExitStack() as stack:
        capture: TextIO | None = None
        if capture_path is not None:
            capture = stack.enter_context(capture_path.open("a", encoding="utf-8"))

        def record(line: str) -> None:
            if capture is not None:
                # Flushed per line so a wedged call's evidence is on disk while
                # it is still wedged.
                capture.write(line if line.endswith("\n") else line + "\n")
                capture.flush()
            state.record(line)

        while True:
            budget = min(silence_seconds, deadline - time.monotonic())
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
            record(line)

        if expired is not None:
            _log.error(
                "call bound expired; killing the process group: bound=%s silence=%.1fs total=%.1fs",
                expired.value,
                silence_seconds,
                total_seconds,
            )
            invocation.kill()
            pump.join(timeout=_READER_JOIN_SECONDS)
            if pump.is_alive():
                # The kill did not free the pipe. Waiting further would make the
                # bound track whatever still holds it, which is the defect the
                # bound exists to prevent; the pump is a daemon and is left.
                _log.error("the capture pump is still blocked on the call's stdout after the kill")
            # Whatever the call managed to say before the kill is still evidence.
            while True:
                try:
                    line = sink.get_nowait()
                except queue.Empty:
                    break
                if line is not None:
                    record(line)

    return state, expired


def call_claude(
    *,
    prompt: str,
    cwd: Path,
    agent: str | None = None,
    permission_mode: str = DEFAULT_PERMISSION_MODE,
    silence_seconds: float = DEFAULT_SILENCE_SECONDS,
    total_seconds: float = DEFAULT_TOTAL_SECONDS,
    command: Sequence[str] = DEFAULT_COMMAND,
    env: Mapping[str, str] | None = None,
    capture_path: Path | None = None,
    invoker: Invoker | None = None,
) -> tuple[str, Outcome]:
    """Pose ``prompt`` to a headless ``claude`` and return what it said and how it ended.

    Synchronous, and **not a single completion**: the prompt may instruct tool
    use or further agentic dispatch, and this returns when the process stops
    speaking or a bound expires, whichever comes first. Budget against
    ``total_seconds``, not against an expectation of one turn.

    ``agent`` names a CLI subagent definition; ``capture_path`` is appended to
    with every stream line verbatim, and its directory must exist. ``invoker``
    replaces the subprocess seam, which is how a caller tests against no model
    at all.

    The returned text is the last ``result`` event's, which is empty when the
    call never produced one — a possibility on every outcome including a
    partial one, so read the :class:`Outcome` before the text.

    Raises :class:`ValueError` for an unusable argument, and whatever the
    invoker raises when it cannot spawn at all — a missing ``claude`` on the
    path is a ``FileNotFoundError``, not an outcome. Every way a call that
    *did* start can end is a returned :class:`Outcome`.
    """
    _require(prompt.strip(), "the prompt is empty")
    _require(cwd.is_dir(), f"the call's cwd does not exist: {cwd}")
    _require(bool(command), "the command is empty")
    _require(bool(permission_mode), "the permission mode is empty")
    _require(agent is None or bool(agent.strip()), "the agent name is empty")
    _require(silence_seconds > 0 and total_seconds > 0, "call bounds must be positive")
    _require(
        capture_path is None or capture_path.parent.is_dir(),
        f"the capture directory does not exist: {capture_path}",
    )

    argv = _build_argv(command=command, permission_mode=permission_mode, agent=agent)
    started = time.monotonic()
    invocation = (invoker or SubprocessInvoker()).run(argv=argv, cwd=cwd, env=dict(env or {}), prompt=prompt)
    state, expired = _capture(
        invocation,
        capture_path=capture_path,
        silence_seconds=silence_seconds,
        total_seconds=total_seconds,
    )
    status = invocation.wait()
    duration = time.monotonic() - started

    if expired is not None:
        outcome = expired
    elif status != 0 and state.events == 0:
        # The CLI refused the argv before making a call: never worth a retry.
        outcome = Outcome.CLI_REJECTION
        _log.error("the CLI rejected the invocation (exit %d): %s", status, invocation.stderr_text().strip())
    elif status != 0 or state.result is None:
        outcome = Outcome.TRANSPORT_FAILURE
    else:
        outcome = Outcome.OK

    _log.info(
        "headless call finished: outcome=%s exit=%d events=%d inits=%d duration=%.1fs",
        outcome.value,
        status,
        state.events,
        state.inits,
        duration,
    )
    return state.result_text, outcome
