"""The one call implementation: argv shape, capture, bounds, classification, seats.

Every case here runs below the :class:`~kb_tools.inference.claude.Invoker`
seam, against scripted stream lines or a real ``python``/``sh`` child — **no
test in this file needs a model to be reachable**, which is the property the
seam exists for. The build driver reads the same classification through the
same :func:`~kb_tools.inference.claude.invoke`, so what is asserted here is
asserted for both callers at once.

Three cases use a real subprocess because nothing else proves them: that the
prompt arrives on stdin rather than argv, that the kill reaps a *grandchild* —
the sleeping worker a bare kill of the parent would leave behind — and that it
still reaps the group when the parent exited first.
"""

import json
import logging
import os
import signal
import sys
import threading
import time
from collections.abc import Iterator, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

import pytest

from kb_tools.inference import claude, seat

PROMPT = "state the third invariant\n"


def init_event(session: str = "s") -> str:
    return json.dumps({"type": "system", "subtype": "init", "session_id": session})


def result_event(text: str) -> str:
    return json.dumps({"type": "result", "subtype": "success", "result": text})


# The placeholder an async dispatch returns before the agent it dispatched has
# finished — the shape that makes "read to stream end" load-bearing.
WAITING_PLACEHOLDER = "I'm waiting for the agent to complete."


@dataclass(frozen=True)
class Recorded:
    """One call as it crossed the seam."""

    argv: tuple[str, ...]
    cwd: Path
    env: Mapping[str, str]
    prompt: str


class ScriptedCall:
    """A live call that says what it was told to and then stops — or stalls."""

    def __init__(self, *, lines: Sequence[str] = (), exit_status: int = 0, stderr: str = "", stall: bool = False):
        self._lines = tuple(lines)
        self._exit_status = exit_status
        self._stderr = stderr
        self._stall = stall
        self._released = threading.Event()
        self.killed = False

    def lines(self) -> Iterator[str]:
        yield from self._lines
        if self._stall:
            # Held until the watchdog kills it; the bound is what must end this.
            assert self._released.wait(timeout=10.0), "the watchdog never killed the stalled call"

    def kill(self) -> None:
        self.killed = True
        self._exit_status = -signal.SIGTERM
        self._released.set()

    def wait(self) -> int:
        return self._exit_status

    def stderr_text(self) -> str:
        return self._stderr


class ScriptedInvoker:
    """The seam, substituted: one scripted call, and a record of how it was asked for."""

    def __init__(self, call: ScriptedCall) -> None:
        self.call = call
        self.calls: list[Recorded] = []

    def run(self, *, argv: Sequence[str], cwd: Path, env: Mapping[str, str], prompt_path: Path) -> ScriptedCall:
        self.calls.append(
            Recorded(argv=tuple(argv), cwd=cwd, env=dict(env), prompt=prompt_path.read_text(encoding="utf-8"))
        )
        return self.call


class UnspawnableInvoker:
    """The seam, refusing to start: what ``Popen`` raises for a command that is not there."""

    def __init__(self, error: OSError | None = None) -> None:
        self.error = error or FileNotFoundError(2, "No such file or directory: 'claude'")
        self.calls = 0

    def run(self, *, argv: Sequence[str], cwd: Path, env: Mapping[str, str], prompt_path: Path) -> ScriptedCall:
        del argv, cwd, env, prompt_path
        self.calls += 1
        raise self.error


def _ask(invoker: object, directory: Path, **overrides: object) -> tuple[str, claude.Outcome]:
    arguments: dict[str, object] = {"prompt": PROMPT, "cwd": directory, "invoker": invoker}
    arguments.update(overrides)
    return claude.call_claude(**arguments)  # type: ignore[arg-type]


def _invoke(invoker: object, directory: Path, **overrides: object) -> claude.CallResult:
    """One call through the entry point the build driver uses: the whole result back."""
    prompt_path = directory / "prompt.md"
    prompt_path.write_text(PROMPT, encoding="utf-8")
    arguments: dict[str, object] = {
        "invoker": invoker,
        "argv": claude.build_argv(command=["claude"], permission_mode="acceptEdits", agent="architect"),
        "cwd": directory,
        "prompt_path": prompt_path,
        "capture_path": directory / "call.stream.jsonl",
        "silence_seconds": 5.0,
        "total_seconds": 30.0,
    }
    arguments.update(overrides)
    return claude.invoke(**arguments)  # type: ignore[arg-type]


def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    return True


# ---------------------------------------------------------------------------
# Capture and classification
# ---------------------------------------------------------------------------


def test_a_clean_call_returns_the_result_text_and_ok(tmp_path: Path) -> None:
    invoker = ScriptedInvoker(ScriptedCall(lines=(init_event(), result_event("DONE ok"))))

    text, outcome = _ask(invoker, tmp_path)

    assert text == "DONE ok"
    assert outcome is claude.Outcome.OK
    assert outcome.ok


def test_the_last_result_wins_and_a_second_init_is_not_a_failure_here(tmp_path: Path) -> None:
    # Stopping at the first `result` would return the placeholder as the answer.
    # A second `init` means the session dispatched asynchronously, which this
    # layer permits by construction — it promises no one-prompt-one-response.
    invoker = ScriptedInvoker(
        ScriptedCall(
            lines=(
                init_event("first"),
                result_event(WAITING_PLACEHOLDER),
                init_event("second"),
                result_event("DONE ok"),
            )
        )
    )

    text, outcome = _ask(invoker, tmp_path)

    assert text == "DONE ok"
    assert outcome is claude.Outcome.OK


def test_zero_events_and_a_nonzero_exit_is_an_unretryable_rejection(tmp_path: Path) -> None:
    invoker = ScriptedInvoker(ScriptedCall(exit_status=1, stderr="error: unknown option '--frobnicate'\n"))

    text, outcome = _ask(invoker, tmp_path)

    assert outcome is claude.Outcome.CLI_REJECTION
    assert not outcome.retryable  # three identical failures and a misleading diagnosis
    assert text == ""


def test_a_failure_after_an_init_is_retryable_transport_territory(tmp_path: Path) -> None:
    invoker = ScriptedInvoker(ScriptedCall(lines=(init_event(),), exit_status=1))

    _, outcome = _ask(invoker, tmp_path)

    assert outcome is claude.Outcome.TRANSPORT_FAILURE
    assert outcome.retryable


def test_exit_zero_without_a_result_event_is_not_a_success(tmp_path: Path) -> None:
    invoker = ScriptedInvoker(ScriptedCall(lines=(init_event(),)))

    text, outcome = _ask(invoker, tmp_path)

    assert outcome is claude.Outcome.TRANSPORT_FAILURE
    assert text == ""


def test_the_capture_file_holds_every_line_verbatim(tmp_path: Path) -> None:
    lines = (init_event(), "this line is not json at all", result_event("ok"))
    invoker = ScriptedInvoker(ScriptedCall(lines=lines))
    capture = tmp_path / "call.stream.jsonl"

    text, outcome = _ask(invoker, tmp_path, capture_path=capture)

    assert outcome is claude.Outcome.OK
    assert text == "ok"
    assert capture.read_text(encoding="utf-8") == "".join(f"{line}\n" for line in lines)


def test_the_result_carries_the_evidence_behind_its_own_classification(tmp_path: Path) -> None:
    """What ``invoke`` returns that the text-and-outcome pair drops: the counts and the stream."""
    lines = (init_event(), "this line is not json at all", result_event("ok"))

    result = _invoke(ScriptedInvoker(ScriptedCall(lines=lines)), tmp_path)

    assert result.outcome is claude.Outcome.OK
    assert result.ok
    assert result.exit_status == 0
    assert (result.event_count, result.init_count, result.unparsed_lines) == (2, 1, 1)
    assert result.result_event is not None and result.result_event["subtype"] == "success"
    assert result.capture_path is not None
    assert result.capture_path.read_text(encoding="utf-8") == "".join(f"{line}\n" for line in lines)


def test_a_second_init_is_counted_and_the_last_result_still_wins(tmp_path: Path) -> None:
    """The async-dispatch shape. Counted here; what it *means* is the caller's.

    A caller whose step model makes one call one turn fails the call on the
    count; this layer promises no such thing and returns an ordinary success.
    """
    lines = (
        init_event("first"),
        result_event(WAITING_PLACEHOLDER),
        init_event("second"),
        result_event("DONE ok"),
    )

    result = _invoke(ScriptedInvoker(ScriptedCall(lines=lines)), tmp_path)

    assert result.outcome is claude.Outcome.OK
    assert result.init_count == 2
    assert result.result_text == "DONE ok"
    # The premature result is still on disk: the capture read to stream end.
    captured = [json.loads(line) for line in result.capture_path.read_text(encoding="utf-8").splitlines()]
    assert [event["type"] for event in captured].count("result") == 2


@pytest.mark.parametrize("subtype", ["error_max_turns", "error_during_execution"])
def test_a_result_event_the_cli_marked_failed_is_not_a_success_at_exit_zero(tmp_path: Path, subtype: str) -> None:
    """The CLI reports its own failures at exit 0, on the event rather than the status.

    Read by status alone the call is a success, and whatever text the errored
    result carries goes on to be validated as the answer — so the failure
    surfaces, if at all, as a malformed return from a seat that never finished
    speaking.
    """
    errored = json.dumps({"type": "result", "subtype": subtype, "result": "Error: the turn budget was exhausted."})

    result = _invoke(ScriptedInvoker(ScriptedCall(lines=(init_event(), errored))), tmp_path)

    assert result.exit_status == 0, "the shape under test: the CLI said failed and exited clean"
    assert result.outcome is claude.Outcome.RESULT_ERROR
    assert not result.ok
    assert result.result_errored
    assert result.result_subtype == subtype


def test_an_is_error_flag_alone_is_enough_to_classify_a_failure(tmp_path: Path) -> None:
    """Both fields are read: ``subtype`` carries the reason, ``is_error`` the verdict."""
    errored = json.dumps({"type": "result", "subtype": "success", "is_error": True, "result": "Execution error"})

    result = _invoke(ScriptedInvoker(ScriptedCall(lines=(init_event(), errored))), tmp_path)

    assert result.outcome is claude.Outcome.RESULT_ERROR
    assert result.outcome.retryable  # mixed class: an error mid-call is what a retry recovers


def test_a_call_that_never_started_is_classified_rather_than_raised(tmp_path: Path) -> None:
    """An absent or mistyped command is an environment fault, not a defect in the caller.

    Unclassified it escapes as ``Popen``'s own ``FileNotFoundError`` — which
    every caller then reports as a bug in itself.
    """
    invoker = UnspawnableInvoker()

    result = _invoke(invoker, tmp_path)

    assert invoker.calls == 1
    assert result.outcome is claude.Outcome.SPAWN_FAILURE
    assert not result.outcome.retryable  # a command not on the path is not on it three times either
    assert result.exit_status == claude.NO_PROCESS_STATUS
    assert "No such file" in result.stderr


# ---------------------------------------------------------------------------
# Bounds
# ---------------------------------------------------------------------------


def test_a_stall_trips_the_silence_watchdog_and_kills_the_group(tmp_path: Path) -> None:
    call = ScriptedCall(lines=(init_event(),), stall=True)
    invoker = ScriptedInvoker(call)
    capture = tmp_path / "call.stream.jsonl"

    started = time.monotonic()
    _, outcome = _ask(invoker, tmp_path, silence_seconds=0.3, total_seconds=30.0, capture_path=capture)

    assert outcome is claude.Outcome.SILENCE
    assert outcome.retryable
    assert call.killed
    assert time.monotonic() - started < 5.0
    # What it said before wedging is evidence, and it is on disk.
    assert capture.read_text(encoding="utf-8").strip()


def test_the_total_bound_kills_a_call_the_silence_bound_would_not(tmp_path: Path) -> None:
    call = ScriptedCall(lines=(init_event(),), stall=True)

    started = time.monotonic()
    _, outcome = _ask(ScriptedInvoker(call), tmp_path, silence_seconds=30.0, total_seconds=0.4)

    assert outcome is claude.Outcome.TIMEOUT
    assert call.killed
    assert time.monotonic() - started < 5.0


# ---------------------------------------------------------------------------
# argv, and what may never reach it
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("agent", [None, "architect"])
def test_the_argv_carries_the_agent_and_the_stream_flags(tmp_path: Path, agent: str | None) -> None:
    invoker = ScriptedInvoker(ScriptedCall(lines=(init_event(), result_event("ok"))))

    _ask(invoker, tmp_path, agent=agent, permission_mode="acceptEdits")

    argv = invoker.calls[0].argv
    assert argv[0] == "claude"
    assert argv[1] == "-p"
    assert argv[-3:] == claude.STREAM_FLAGS
    assert argv[argv.index("--permission-mode") + 1] == "acceptEdits"
    assert ("--agent" in argv) == (agent is not None)


def test_the_prompt_reaches_the_call_on_stdin_and_never_on_argv(tmp_path: Path) -> None:
    # A positional argv prompt does not replace the stdin one: both texts reach
    # the turn, at exit 0, with no warning.
    invoker = ScriptedInvoker(ScriptedCall(lines=(init_event(), result_event("ok"))))

    _ask(invoker, tmp_path)

    recorded = invoker.calls[0]
    assert recorded.prompt == PROMPT
    assert not any(PROMPT.strip() in token for token in recorded.argv)


@pytest.mark.parametrize(
    ("overrides", "complaint"),
    [
        ({"prompt": "   "}, "prompt is empty"),
        ({"cwd": Path("/no/such/directory")}, "cwd does not exist"),
        ({"command": ()}, "command is empty"),
        ({"permission_mode": ""}, "permission mode is empty"),
        ({"agent": " "}, "agent name is empty"),
        ({"silence_seconds": 0.0}, "bounds must be positive"),
        ({"capture_path": Path("/no/such/directory/x.jsonl")}, "capture directory does not exist"),
    ],
)
def test_an_unusable_argument_is_refused_before_anything_is_spawned(
    tmp_path: Path, overrides: dict[str, object], complaint: str
) -> None:
    invoker = ScriptedInvoker(ScriptedCall(lines=(init_event(), result_event("ok"))))

    with pytest.raises(ValueError, match=complaint):
        _ask(invoker, tmp_path, **overrides)

    assert invoker.calls == []


# ---------------------------------------------------------------------------
# Seats
# ---------------------------------------------------------------------------


@pytest.fixture
def project(tmp_path: Path) -> Path:
    """A directory with one seat defined in it, the way an installed project has."""
    agents = tmp_path / seat.SEAT_DIRECTORY
    agents.mkdir(parents=True)
    (agents / "architect.md").write_text("---\nname: architect\n---\n", encoding="utf-8")
    return tmp_path


def test_ask_seat_spells_the_seat_as_the_agent_flag_and_returns_the_pair(project: Path) -> None:
    invoker = ScriptedInvoker(ScriptedCall(lines=(init_event(), result_event("DONE ok"))))

    text, outcome = seat.ask_seat(seat="architect", prompt=PROMPT, cwd=project, invoker=invoker)

    assert (text, outcome) == ("DONE ok", claude.Outcome.OK)
    argv = invoker.calls[0].argv
    assert argv[argv.index("--agent") + 1] == "architect"
    assert invoker.calls[0].cwd == project


def test_ask_seat_never_passes_model(project: Path) -> None:
    # A seat's model pin is authoritative only for as long as the flag is
    # omitted, and there is no parameter here through which one could arrive.
    invoker = ScriptedInvoker(ScriptedCall(lines=(init_event(), result_event("ok"))))

    seat.ask_seat(seat="architect", prompt=PROMPT, cwd=project, invoker=invoker)

    assert "--model" not in invoker.calls[0].argv


@pytest.mark.parametrize("name", ["", "  ", "architect.md", ".claude/agents/architect", "--agent"])
def test_a_seat_named_as_anything_but_a_stem_is_refused(project: Path, name: str) -> None:
    invoker = ScriptedInvoker(ScriptedCall(lines=(init_event(), result_event("ok"))))

    with pytest.raises(ValueError):
        seat.ask_seat(seat=name, prompt=PROMPT, cwd=project, invoker=invoker)

    assert invoker.calls == []


def test_a_seat_ask_whose_command_cannot_be_spawned_is_an_environment_fault(project: Path) -> None:
    """A missing ``claude`` is a fault in the environment the tool runs in.

    The tool path gets the same classification the build driver does: a returned
    outcome its caller can report and refuse to retry, rather than a
    ``FileNotFoundError`` escaping ``ask_seat`` to be narrated as a defect in
    the tool that asked.
    """
    invoker = UnspawnableInvoker()

    text, outcome = seat.ask_seat(seat="architect", prompt=PROMPT, cwd=project, invoker=invoker)

    assert outcome is claude.Outcome.SPAWN_FAILURE
    assert not outcome.ok and not outcome.retryable
    assert text == ""


def test_a_seat_ask_the_cli_marked_errored_is_not_graded_ok(project: Path) -> None:
    """Exit 0 and a result the CLI itself marked failed: the error text is not an answer.

    Graded ``OK``, the CLI's own error prose is what the caller then validates
    as what the seat said.
    """
    errored = json.dumps({"type": "result", "subtype": "error_during_execution", "result": "Error: execution stopped."})
    invoker = ScriptedInvoker(ScriptedCall(lines=(init_event(), errored)))

    text, outcome = seat.ask_seat(seat="architect", prompt=PROMPT, cwd=project, invoker=invoker)

    assert outcome is claude.Outcome.RESULT_ERROR
    assert not outcome.ok
    assert text == "Error: execution stopped.", "the text still comes back; what changes is how it is graded"


def test_a_seat_no_definition_can_be_found_for_warns_and_still_calls(
    project: Path, caplog: pytest.LogCaptureFixture
) -> None:
    invoker = ScriptedInvoker(ScriptedCall(lines=(init_event(), result_event("ok"))))

    with caplog.at_level(logging.WARNING, logger="kb_tools.inference.seat"):
        text, outcome = seat.ask_seat(seat="no-such-seat-here", prompt=PROMPT, cwd=project, invoker=invoker)

    assert outcome is claude.Outcome.OK and text == "ok"  # a warning, never a refusal
    assert "no-such-seat-here" in caplog.text


# ---------------------------------------------------------------------------
# The real subprocess
# ---------------------------------------------------------------------------


def test_a_real_child_reads_the_prompt_from_stdin(tmp_path: Path) -> None:
    program = (
        "import json, sys\n"
        "prompt = sys.stdin.read()\n"
        "print(json.dumps({'type': 'system', 'subtype': 'init', 'session_id': 's'}))\n"
        "print(json.dumps({'type': 'result', 'subtype': 'success', 'result': prompt.strip()}))\n"
    )

    text, outcome = claude.call_claude(
        prompt=PROMPT,
        cwd=tmp_path,
        command=[sys.executable, "-c", program],
        silence_seconds=20.0,
        total_seconds=30.0,
    )

    assert outcome is claude.Outcome.OK
    assert text == PROMPT.strip()


def test_the_watchdog_kills_the_process_group_and_reaps_a_sleeping_grandchild(tmp_path: Path) -> None:
    pid_file = tmp_path / "grandchild.pid"
    script = "\n".join(
        [
            "sleep 30 &",  # the grandchild a bare kill of the child would orphan
            'echo $! > "$1"',
            """echo '{"type":"system","subtype":"init","session_id":"s"}'""",
            "sleep 30",
        ]
    )

    _, outcome = claude.call_claude(
        prompt=PROMPT,
        cwd=tmp_path,
        command=["/bin/sh", "-c", script, "sh", str(pid_file)],
        silence_seconds=0.75,
        total_seconds=20.0,
        invoker=claude.SubprocessInvoker(kill_grace_seconds=0.5),
    )

    assert outcome is claude.Outcome.SILENCE

    grandchild = int(pid_file.read_text(encoding="utf-8").strip())
    deadline = time.monotonic() + 5.0
    while _alive(grandchild) and time.monotonic() < deadline:
        time.sleep(0.05)
    # Killing the parent alone would leave this sleeping worker behind.
    assert not _alive(grandchild)


def test_the_watchdog_still_reaps_the_group_when_the_parent_exited_first(tmp_path: Path) -> None:
    """The async-dispatch shape: the parent returns, its worker holds the pipe.

    This is the case a ``poll()``-guarded kill skipped, and the case that made a
    0.75s silence bound return in the worker's own time. Both halves are
    asserted: the orphan dies, and the capture comes back on the bound rather
    than tracking the orphan's lifetime.
    """
    pid_file = tmp_path / "orphan.pid"
    worker_seconds = 20
    script = "\n".join(
        [
            f"sleep {worker_seconds} &",  # inherits stdout, so the stream never reaches EOF
            'echo $! > "$1"',
            """echo '{"type":"system","subtype":"init","session_id":"s"}'""",
            """echo '{"type":"result","subtype":"success","result":"waiting for the agent"}'""",
            "exit 0",  # the parent is gone long before the watchdog fires
        ]
    )

    started = time.monotonic()
    _, outcome = claude.call_claude(
        prompt=PROMPT,
        cwd=tmp_path,
        command=["/bin/sh", "-c", script, "sh", str(pid_file)],
        silence_seconds=0.75,
        total_seconds=60.0,
        invoker=claude.SubprocessInvoker(kill_grace_seconds=0.5),
    )
    elapsed = time.monotonic() - started

    assert outcome is claude.Outcome.SILENCE
    # The bound is the bound. Anything near `worker_seconds` is the defect.
    assert elapsed < worker_seconds / 2

    orphan = int(pid_file.read_text(encoding="utf-8").strip())
    deadline = time.monotonic() + 5.0
    while _alive(orphan) and time.monotonic() < deadline:
        time.sleep(0.05)
    assert not _alive(orphan)


def test_a_command_that_is_not_on_the_path_comes_back_as_a_spawn_failure(tmp_path: Path) -> None:
    """The real ``Popen``, because the fault being classified is its own."""
    text, outcome = claude.call_claude(
        prompt=PROMPT,
        cwd=tmp_path,
        command=[str(tmp_path / "no-such-claude")],
        silence_seconds=5.0,
        total_seconds=10.0,
    )

    assert outcome is claude.Outcome.SPAWN_FAILURE
    assert text == ""
