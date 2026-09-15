"""One headless ``claude`` call: spawn, stream capture, watchdog, kill, classify.

Named parameters in, how the call ended out. Nothing here knows what a KB is,
what a seat is, what a build stage is, or that this repository exists: the only
thing it is bound to is the ``claude`` CLI's own shape, and it does not pretend
otherwise. A harness-agnostic dispatch layer would be a different module with a
different argument vocabulary; this one spells ``--agent`` and
``--permission-mode`` because that is what it drives.

**One implementation answers "did this call succeed", and this is it.** Both
consumers read the same classification: the build driver, which spawns a seat
per step, and any tool that wants an answer from one
(:func:`kb_tools.inference.ask_seat`). Two entry points, one level apart —
:func:`invoke`, which takes a composed argv and a prompt already on disk and
returns the whole :class:`CallResult`, and :func:`call_claude`, which builds the
argv, spools the prompt and returns the pair a caller with no use for the
evidence wants.

**What is here, and what is deliberately not.** Here: the spawn, the capture,
the two bounds' enforcement, the process-group kill, and the classification of
how the call ended. Not here, because each belongs to the *caller's* own policy
and a package a tool imports must not acquire the driver's: retry and backoff,
a re-ask, where the bound values come from, one-call-at-a-time, and boundary
checks carrying an application's own exit codes. What a second ``init`` event
*means* is the same split — this module counts them
(:attr:`CallResult.init_count`) and returns an ordinary success, because it
promises no one-prompt-one-response; a caller whose step model requires one
call to be one turn fails the call on that count itself.

**It does not promise one prompt, one response.** A prompt may instruct further
agentic dispatch, tool use, or a long interaction, and the process runs until
it stops speaking or a bound expires — so a caller reasoning about cost or
latency as though this were a single completion is reasoning about a different
function. What is bounded is wall-clock, by ``silence_seconds`` and
``total_seconds``; what is not bounded is what the model spends inside them.

**Five behaviours are version-observed** — measured at CLI 2.1.220, not read
off a documented contract, and all five to be re-probed against any CLI
upgrade:

* the prompt reaches the process **on stdin, never on argv** — a positional
  argv prompt does not replace the stdin one, it merges with it, at exit 0 and
  with no warning. :func:`build_argv` therefore has no parameter through which
  prompt text could reach argv;
* ``start_new_session=True`` plus ``killpg`` — killing the parent ``claude``
  does not reap the workers its Agent tool dispatched;
* **the capture reads to stream end, never to the first ``result``.** A session
  that dispatches asynchronously emits a premature ``result`` whose text is a
  waiting-for-the-agent placeholder and then a second ``init``/``result`` pair,
  so stopping at the first ``result`` returns the placeholder as the answer;
* **the zero-event test is the error classifier.** ``exit != 0`` with no stream
  event ever emitted is the CLI rejecting the argv — a bad flag, a bad value, a
  fatal combination — which no repetition resolves. Anything after an ``init``
  may be transient;
* **the last ``result`` event carries its own verdict, and exit 0 is not it.** A
  ``result`` marked ``is_error``, or carrying any ``subtype`` other than
  ``success``, is the CLI reporting that the call failed — at exit 0, so a
  classifier reading the status alone records the failure as a success and
  hands the error text on as the answer.

A call that never started is classified rather than raised past the classifier:
an absent or mistyped command is a fault in the environment the caller was
launched in, and a defect in the caller is what an escaping
``FileNotFoundError`` would make it look like.

Separate stdout and stderr readers throughout: one pipe drained while the other
fills is a deadlock.

The prompt crosses the :class:`Invoker` seam **as a file** because a substituted
invoker answers per call, and the file a caller composed is what tells one call
from the next — a driver's per-step brief, named by the step it is for. A caller
holding only text gets the spool :func:`call_claude` makes for it.

This module configures no logging handlers (library discipline). An application
wanting these lines in its own log attaches its handlers to
``kb_tools.inference``.

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
from dataclasses import dataclass
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

#: The outer edge of "something is wrong", not an expectation.
DEFAULT_SILENCE_SECONDS = 600.0
DEFAULT_TOTAL_SECONDS = 1800.0

#: SIGTERM to the group, then SIGKILL after this long.
DEFAULT_KILL_GRACE_SECONDS = 10.0

#: The only ``result`` subtype that is not a reason the call failed.
RESULT_SUCCESS_SUBTYPE = "success"

#: :attr:`CallResult.exit_status` where no process ever ran. A real status is
#: 0-255, or the negation of a signal number; this is outside both, so nothing
#: reading one can mistake it for an answer some process gave.
NO_PROCESS_STATUS = -256

# How often the kill path re-asks whether anything is left in the group.
_GROUP_POLL_SECONDS = 0.05

# How long the post-kill path waits for the readers to come back. A bound, not
# an expectation: a reader still blocked after this is abandoned rather than
# waited on, because what it is blocked on is an orphan's lifetime.
_READER_JOIN_SECONDS = 5.0


class Outcome(Enum):
    """How a call ended, before any policy is applied."""

    OK = "ok"
    CLI_REJECTION = "cli-rejection"  # zero events + nonzero exit: the invocation was refused
    SPAWN_FAILURE = "spawn-failure"  # the command is not runnable: environment-class
    TRANSPORT_FAILURE = "transport-failure"  # died, or ended without a result, after speaking
    RESULT_ERROR = "result-error"  # exit 0, and the result event says it failed
    SILENCE = "silence-watchdog"
    TIMEOUT = "total-timeout"

    @property
    def ok(self) -> bool:
        return self is Outcome.OK

    @property
    def retryable(self) -> bool:
        """Would re-issuing the identical call plausibly end differently?

        A rejected invocation retried is three identical failures and a
        misleading diagnosis — zero stream events with a nonzero exit is the CLI
        refusing the argv, which is a configuration fault — and a command that
        is not on the path is not on it three times either. Everything after an
        ``init`` may be transient. :attr:`RESULT_ERROR` stays retryable because
        the class is mixed: an execution error mid-call is exactly what a retry
        recovers, and telling it from an exhausted turn budget would mean
        branching on ``subtype`` strings that move with the CLI.
        """
        return self not in (Outcome.OK, Outcome.CLI_REJECTION, Outcome.SPAWN_FAILURE)


@dataclass(frozen=True)
class CallResult:
    """One call's classified outcome and the evidence behind it."""

    outcome: Outcome
    exit_status: int
    capture_path: Path | None
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
        """The last ``result`` event's text, or empty where the stream carried none.

        Empty is possible on every outcome, a partial call included, so read the
        :class:`Outcome` before the text.
        """
        value = None if self.result_event is None else self.result_event.get("result")
        return value if isinstance(value, str) else ""

    @property
    def result_errored(self) -> bool:
        """The CLI's own verdict on the last ``result`` event, whatever the exit status."""
        return _result_errored(self.result_event)

    @property
    def result_subtype(self) -> str:
        """The last ``result`` event's ``subtype``, or empty where it carried none."""
        value = None if self.result_event is None else self.result_event.get("subtype")
        return value if isinstance(value, str) else ""


def _result_errored(event: Mapping[str, object] | None) -> bool:
    """Did the CLI itself say this ``result`` event reports a failure?

    Both fields are read, because each answers where the other is silent:
    ``is_error`` is the boolean a consumer is meant to branch on, and
    ``subtype`` carries the reason (``error_max_turns``,
    ``error_during_execution``). A ``subtype`` the event does not carry decides
    nothing, so a stream that stops emitting one degrades to the flag alone
    rather than failing every call.
    """
    if event is None:
        return False
    if event.get("is_error") is True:
        return True
    subtype = event.get("subtype")
    return isinstance(subtype, str) and subtype != RESULT_SUCCESS_SUBTYPE


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

    Realized as a handle rather than a bare iterator because the watchdog needs
    a kill alongside the lines. :class:`SubprocessInvoker` is the real one; a
    substitute returns scripted stream-json lines, and every line below the seam
    — capture, watchdog, classification — runs exactly as it does in production.
    """

    def run(
        self,
        *,
        argv: Sequence[str],
        cwd: Path,
        env: Mapping[str, str],
        prompt_path: Path,
    ) -> Invocation:
        """Start the call with the prompt at ``prompt_path`` on stdin."""


def _require(condition: object, message: str) -> None:
    """A cheap always-on check at this module's public boundaries."""
    if condition:
        return
    _log.error("headless call refused: %s", message)
    raise ValueError(message)


def build_argv(*, command: Sequence[str], permission_mode: str, agent: str | None = None) -> list[str]:
    """Assemble the call's argv. ``agent`` names a seat definition; omitting it is seatless.

    **The prompt is not here and cannot be.** There is no parameter on this
    function through which prompt text could reach argv, which is the whole
    defence: a positional argv prompt merges with the stdin one instead of
    replacing it, and both texts reach the turn at exit 0 with no warning.

    No ``--model`` either, and for the same structural reason: an explicit
    ``--model`` overrides a seat's frontmatter pin, so "the pin is
    authoritative" holds only for as long as the flag is omitted. A caller
    supplying its own ``command`` prefix owns what is in it.
    """
    _require(bool(command), "the command is empty")
    _require(bool(permission_mode), "the permission mode is empty")
    _require(agent is None or bool(agent.strip()), "the agent name is empty")

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
        prompt_path: Path,
    ) -> "_ChildInvocation":
        # The prompt is handed over as an open file rather than written down the
        # pipe: a 46 KB prompt written to a pipe nobody is draining yet
        # deadlocks against a child that is meanwhile blocked writing stdout.
        prompt = prompt_path.open("r", encoding="utf-8")
        try:
            child = subprocess.Popen(
                list(argv),
                cwd=cwd,
                env={**os.environ, **env},
                stdin=prompt,
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
        finally:
            prompt.close()

        _log.debug("spawned a headless call: pid=%d cwd=%s argv=%s", child.pid, cwd, " ".join(argv))
        return _ChildInvocation(child, kill_grace_seconds=self._kill_grace_seconds)


class _ChildInvocation:
    """The live ``claude`` process behind :class:`SubprocessInvoker`."""

    def __init__(self, child: subprocess.Popen[str], *, kill_grace_seconds: float) -> None:
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
        exactly when the group is all that is left. The only thing that means
        "nothing to signal" is ``ProcessLookupError`` on the group itself, and
        the grace period is measured against the **group** emptying for the same
        reason.
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


def _pump(invocation: Invocation, sink: "queue.Queue[str | None]") -> None:
    """Move the call's lines onto a queue so the watchdog can time the gaps between them."""
    try:
        for line in invocation.lines():
            sink.put(line)
    finally:
        sink.put(None)


def _spawn_failure(exc: OSError, *, argv: Sequence[str], capture_path: Path | None) -> CallResult:
    """The call that never started: a missing or mistyped command.

    An environment fault, classified here rather than left to escape as the
    ``FileNotFoundError`` ``Popen`` raises — which a caller would report as a
    defect in itself.
    """
    _log.error("the call could not be spawned: argv=%s error=%s", " ".join(argv), exc)
    return CallResult(
        outcome=Outcome.SPAWN_FAILURE,
        exit_status=NO_PROCESS_STATUS,
        capture_path=capture_path,
        event_count=0,
        init_count=0,
        unparsed_lines=0,
        result_event=None,
        stderr=str(exc),
        duration_seconds=0.0,
    )


def _capture(
    invocation: Invocation,
    *,
    capture_path: Path | None,
    silence_seconds: float,
    total_seconds: float,
) -> CallResult:
    """Read the stream to its end or to a bound, kill the group when one expires, classify."""
    started = time.monotonic()
    deadline = started + total_seconds
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

    status = invocation.wait()
    duration = time.monotonic() - started

    if expired is not None:
        outcome = expired
    elif status != 0 and state.events == 0:
        outcome = Outcome.CLI_REJECTION
    elif status != 0 or state.result is None:
        outcome = Outcome.TRANSPORT_FAILURE
    elif _result_errored(state.result):
        # Exit 0 and a result the CLI itself marked failed. The status alone
        # calls this a success and hands whatever the event carries on as the
        # answer, which a caller then validates as what it asked for.
        outcome = Outcome.RESULT_ERROR
    else:
        outcome = Outcome.OK

    result = CallResult(
        outcome=outcome,
        exit_status=status,
        capture_path=capture_path,
        event_count=state.events,
        init_count=state.inits,
        unparsed_lines=state.unparsed,
        result_event=state.result,
        stderr=invocation.stderr_text(),
        duration_seconds=duration,
    )

    if outcome is Outcome.CLI_REJECTION:
        _log.error("the CLI rejected the invocation (exit %d): %s", status, result.stderr.strip()[:400])
    if outcome is Outcome.RESULT_ERROR:
        _log.error(
            "the call's own result event reports a failure, at exit 0: subtype=%s result=%s",
            result.result_subtype,
            result.result_text.strip()[:400],
        )
    _log.info(
        "headless call finished: outcome=%s exit=%d events=%d inits=%d duration=%.1fs capture=%s",
        outcome.value,
        status,
        state.events,
        state.inits,
        duration,
        capture_path,
    )
    return result


def invoke(
    *,
    invoker: Invoker,
    argv: Sequence[str],
    cwd: Path,
    prompt_path: Path,
    silence_seconds: float,
    total_seconds: float,
    capture_path: Path | None = None,
    env: Mapping[str, str] | None = None,
) -> CallResult:
    """Run one call to completion or to a bound, and classify how it ended.

    The prompt is read from ``prompt_path`` and reaches the process on stdin.
    ``capture_path`` is appended to with every stream line verbatim and flushed,
    so a wedged call's evidence is on disk while it is still wedged; its
    directory must exist.

    Returns for every way a call can end, spawning included. The only
    exceptions it raises are :class:`ValueError` for an unusable argument.
    """
    _require(cwd.is_dir(), f"the call's cwd does not exist: {cwd}")
    _require(prompt_path.is_file(), f"the prompt is not on disk: {prompt_path}")
    _require(prompt_path.stat().st_size > 0, f"the prompt is empty: {prompt_path}")
    _require(silence_seconds > 0 and total_seconds > 0, "call bounds must be positive")
    _require(
        capture_path is None or capture_path.parent.is_dir(),
        f"the capture directory does not exist: {capture_path}",
    )

    try:
        invocation = invoker.run(argv=argv, cwd=cwd, env=dict(env or {}), prompt_path=prompt_path)
    except OSError as exc:
        # Caught at the seam rather than inside `SubprocessInvoker`, so that
        # every realization of it answers alike: what is being classified is
        # "the call never started", which is no more a defect in the caller for
        # one invoker than for another.
        return _spawn_failure(exc, argv=argv, capture_path=capture_path)
    return _capture(
        invocation,
        capture_path=capture_path,
        silence_seconds=silence_seconds,
        total_seconds=total_seconds,
    )


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
    partial one, so read the :class:`Outcome` before the text. A caller wanting
    the evidence behind the outcome calls :func:`invoke` instead.

    Raises :class:`ValueError` for an unusable argument. Every way a call can
    end, including never starting, is a returned :class:`Outcome`.
    """
    _require(prompt.strip(), "the prompt is empty")
    argv = build_argv(command=command, permission_mode=permission_mode, agent=agent)

    # The prompt goes to a spool file because that is what the seam carries, and
    # the spool is anonymous to everything but this call: nothing else names it,
    # and it is gone before the function returns.
    with tempfile.TemporaryDirectory(prefix="kb-inference-") as spool:
        prompt_path = Path(spool) / "prompt.md"
        prompt_path.write_text(prompt, encoding="utf-8")
        result = invoke(
            invoker=invoker or SubprocessInvoker(),
            argv=argv,
            cwd=cwd,
            prompt_path=prompt_path,
            silence_seconds=silence_seconds,
            total_seconds=total_seconds,
            capture_path=capture_path,
            env=env,
        )
    return result.result_text, result.outcome
