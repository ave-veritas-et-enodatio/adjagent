"""The two inference layers: argv shape, capture, bounds, classification, seats.

Every case here runs below the :class:`~kb_tools.inference.claude.Invoker`
seam, against scripted stream lines or a real ``python``/``sh`` child — **no
test in this file needs a model to be reachable**, which is the property the
seam exists for.

Two cases use a real subprocess because nothing else proves them: that the
prompt arrives on stdin rather than argv, and that the kill reaps a
*grandchild* — the sleeping worker a bare kill of the parent would leave
behind.
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

    def run(self, *, argv: Sequence[str], cwd: Path, env: Mapping[str, str], prompt: str) -> ScriptedCall:
        self.calls.append(Recorded(argv=tuple(argv), cwd=cwd, env=dict(env), prompt=prompt))
        return self.call


def _ask(invoker: ScriptedInvoker, directory: Path, **overrides: object) -> tuple[str, claude.Outcome]:
    arguments: dict[str, object] = {"prompt": PROMPT, "cwd": directory, "invoker": invoker}
    arguments.update(overrides)
    return claude.call_claude(**arguments)  # type: ignore[arg-type]


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
