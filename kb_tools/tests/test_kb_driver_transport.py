"""Transport: capture, watchdog, process-group kill, and the classifier.

Every replayed case here runs through the same ``transport.invoke`` a real
call runs through — that is the point of shipping ``replay.py`` as a
combinator below the stream parser rather than as a fixture library.

Two cases use a real subprocess because nothing else proves them: that the
brief arrives on stdin rather than argv, and that killing the process group
reaps a *grandchild* — the sleeping worker a bare kill of ``claude`` would
leave behind.
"""

import json
import os
import signal
import sys
import time
from pathlib import Path

import pytest

from kb_tools.kb_driver import replay, runlog, transport

BRIEF_TEXT = "compose the taxonomy\n"


@pytest.fixture
def call_paths(tmp_path: Path) -> tuple[Path, Path]:
    """A composed brief on disk and this attempt's capture path, as ``call.py`` supplies them."""
    briefs = tmp_path / "briefs"
    calls = tmp_path / "calls"
    briefs.mkdir()
    calls.mkdir()
    brief = briefs / "003-p1.design.md"
    brief.write_text(BRIEF_TEXT, encoding="utf-8")
    return brief, calls / "003-p1.design-a1.stream.jsonl"


def _replay(
    scenario: replay.Scenario,
    call_paths: tuple[Path, Path],
    *,
    silence: float = 5.0,
    total: float = 30.0,
) -> transport.CallResult:
    brief, stream = call_paths
    return transport.invoke(
        invoker=replay.ReplayInvoker(scenario),
        argv=transport.build_argv(command=["claude"], permission_mode="acceptEdits", agent="architect"),
        cwd=brief.parent,
        brief_path=brief,
        stream_path=stream,
        bounds=transport.Bounds(silence_seconds=silence, total_seconds=total),
    )


def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    return True


# ---------------------------------------------------------------------------
# Capture
# ---------------------------------------------------------------------------


def test_capture_appends_every_line_verbatim(call_paths: tuple[Path, Path]) -> None:
    lines = (
        replay.init_event(session_id="s"),
        "this line is not json at all",
        replay.result_event("ok", session_id="s"),
    )
    result = _replay(lambda context: replay.Response(lines=lines), call_paths)

    assert result.stream_path.read_text(encoding="utf-8") == "".join(f"{line}\n" for line in lines)
    assert result.event_count == 2
    assert result.unparsed_lines == 1
    assert result.result_text == "ok"
    assert result.outcome is transport.Outcome.OK


def test_a_clean_call_parses_its_result_event(call_paths: tuple[Path, Path]) -> None:
    result = _replay(replay.clean("DONE ok"), call_paths)

    assert result.ok
    assert result.exit_status == 0
    assert result.result_text == "DONE ok"
    assert result.result_event is not None and result.result_event["subtype"] == "success"
    assert result.init_count == 1
    assert not result.discipline_violation


def test_the_last_result_wins_and_a_second_init_is_a_discipline_violation(
    call_paths: tuple[Path, Path],
) -> None:
    # The async-dispatch shape: reading to the first `result` would record a
    # completed wave whose members never ran.
    result = _replay(replay.premature_dispatch("DONE ok"), call_paths)

    assert result.result_text == "DONE ok"
    assert replay.WAITING_PLACEHOLDER not in result.result_text
    assert result.init_count == 2
    assert result.discipline_violation
    # The premature result is still on disk: capture read to stream end.
    captured = [json.loads(line) for line in result.stream_path.read_text(encoding="utf-8").splitlines()]
    assert [event["type"] for event in captured].count("result") == 2


# ---------------------------------------------------------------------------
# Bounds
# ---------------------------------------------------------------------------


def test_a_stall_trips_the_silence_watchdog(call_paths: tuple[Path, Path]) -> None:
    result = _replay(replay.stall(), call_paths, silence=0.3, total=30.0)

    assert result.outcome is transport.Outcome.SILENCE
    assert result.outcome.retryable
    assert result.duration_seconds < 5.0
    # What it said before wedging is evidence, and it is on disk.
    assert result.event_count == 1
    assert result.stream_path.read_text(encoding="utf-8").strip()


def test_the_total_timeout_kills_a_call_the_silence_bound_would_not(call_paths: tuple[Path, Path]) -> None:
    result = _replay(replay.stall(), call_paths, silence=30.0, total=0.4)

    assert result.outcome is transport.Outcome.TIMEOUT
    assert result.duration_seconds < 5.0


# ---------------------------------------------------------------------------
# Classification
# ---------------------------------------------------------------------------


def test_zero_events_and_a_nonzero_exit_is_an_unretryable_cli_rejection(call_paths: tuple[Path, Path]) -> None:
    stderr = "error: unknown option '--frobnicate-widget'\n"
    result = _replay(replay.cli_rejection(stderr), call_paths)

    assert result.outcome is transport.Outcome.CLI_REJECTION
    assert not result.outcome.retryable  # three identical failures and a misleading exit 12
    assert result.event_count == 0
    assert result.stderr == stderr


def test_a_failure_after_an_init_is_retryable_transport_territory(call_paths: tuple[Path, Path]) -> None:
    result = _replay(replay.transport_die(exit_status=1), call_paths)

    assert result.outcome is transport.Outcome.TRANSPORT_FAILURE
    assert result.outcome.retryable
    assert result.event_count == 1


def test_exit_zero_without_a_result_event_is_not_a_success(call_paths: tuple[Path, Path]) -> None:
    result = _replay(lambda context: replay.Response(lines=(replay.init_event(),)), call_paths)

    assert result.outcome is transport.Outcome.TRANSPORT_FAILURE
    assert result.result_text == ""


# ---------------------------------------------------------------------------
# argv
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("agent", [None, "architect"])
def test_build_argv_carries_the_seat_and_the_stream_flags(agent: str | None) -> None:
    argv = transport.build_argv(command=["claude"], permission_mode="acceptEdits", agent=agent)

    assert argv[0] == "claude"
    assert argv[1] == "-p"
    assert argv[-3:] == ["--output-format", "stream-json", "--verbose"]
    assert argv[argv.index("--permission-mode") + 1] == "acceptEdits"
    assert ("--agent" in argv) == (agent is not None)
    # The pin is authoritative only for as long as the driver omits the flag.
    assert "--model" not in argv


def test_no_brief_text_reaches_argv(call_paths: tuple[Path, Path]) -> None:
    brief, _ = call_paths
    argv = transport.build_argv(command=["claude"], permission_mode="acceptEdits", agent="architect")

    assert not any(brief.read_text(encoding="utf-8").strip() in token for token in argv)


def test_a_positional_prompt_beside_the_stdin_brief_is_refused() -> None:
    # A positional prompt does not replace the stdin brief — both are delivered
    # to the model, at exit 0, with no warning.
    transport.require_no_positional_prompt(["-p", "--permission-mode", "acceptEdits", *transport.STREAM_FLAGS])
    with pytest.raises(runlog.BoundaryError, match="positional argv prompt"):
        transport.require_no_positional_prompt(["-p", "--permission-mode", "acceptEdits", "do the thing"])


def test_a_command_prefix_carrying_model_is_refused() -> None:
    with pytest.raises(runlog.BoundaryError, match="--model"):
        transport.build_argv(command=["claude", "--model", "haiku"], permission_mode="acceptEdits")


def test_invoke_refuses_an_empty_brief(call_paths: tuple[Path, Path]) -> None:
    brief, _ = call_paths
    brief.write_text("", encoding="utf-8")
    with pytest.raises(runlog.BoundaryError, match="brief is empty"):
        _replay(replay.clean(), call_paths)


# ---------------------------------------------------------------------------
# Replay combinators and single-flight
# ---------------------------------------------------------------------------


def test_sequence_scripts_successive_rounds(call_paths: tuple[Path, Path]) -> None:
    brief, stream = call_paths
    invoker = replay.ReplayInvoker(replay.sequence(replay.clean("red"), replay.clean("green")))
    argv = transport.build_argv(command=["claude"], permission_mode="acceptEdits")
    bounds = transport.Bounds(silence_seconds=5.0, total_seconds=30.0)

    answers = [
        transport.invoke(
            invoker=invoker,
            argv=argv,
            cwd=brief.parent,
            brief_path=brief,
            stream_path=stream.with_name(f"round-{round_number}.stream.jsonl"),
            bounds=bounds,
        ).result_text
        for round_number in (1, 2, 3)
    ]

    assert answers == ["red", "green", "green"]  # the last scenario repeats
    assert invoker.calls == 3


def test_a_call_spawned_while_another_is_live_is_a_boundary_error(call_paths: tuple[Path, Path]) -> None:
    brief, stream = call_paths
    nested: list[runlog.BoundaryError] = []

    def reentrant(context: replay.ReplayContext) -> replay.Response:
        try:
            _replay(replay.clean("inner"), (brief, stream.with_name("inner.stream.jsonl")))
        except runlog.BoundaryError as exc:
            nested.append(exc)
        return replay.clean("outer")(context)

    assert _replay(reentrant, call_paths).result_text == "outer"
    assert nested  # exactly one claude subprocess at a time
    # …and the guard released, so the next call is not poisoned by the last.
    assert _replay(replay.clean("after"), (brief, stream.with_name("after.stream.jsonl"))).result_text == "after"


# ---------------------------------------------------------------------------
# The real subprocess
# ---------------------------------------------------------------------------


def test_the_brief_arrives_on_stdin_and_never_on_argv(call_paths: tuple[Path, Path]) -> None:
    brief, stream = call_paths
    program = (
        "import json, sys\n"
        "brief = sys.stdin.read()\n"
        "print(json.dumps({'type': 'system', 'subtype': 'init', 'session_id': 's'}))\n"
        "print(json.dumps({'type': 'result', 'subtype': 'success', 'result': brief.strip()}))\n"
    )
    argv = [sys.executable, "-c", program]
    assert not any(BRIEF_TEXT.strip() in token for token in argv)

    result = transport.invoke(
        invoker=transport.SubprocessInvoker(),
        argv=argv,
        cwd=brief.parent,
        brief_path=brief,
        stream_path=stream,
        bounds=transport.Bounds(silence_seconds=20.0, total_seconds=30.0),
    )

    assert result.ok
    assert result.result_text == BRIEF_TEXT.strip()


def test_the_watchdog_kills_the_process_group_and_reaps_a_sleeping_grandchild(
    call_paths: tuple[Path, Path], tmp_path: Path
) -> None:
    brief, stream = call_paths
    pid_file = tmp_path / "grandchild.pid"
    script = "\n".join(
        [
            "sleep 30 &",  # the grandchild a bare kill of the child would orphan
            'echo $! > "$1"',
            """echo '{"type":"system","subtype":"init","session_id":"s"}'""",
            "sleep 30",
        ]
    )
    argv = ["/bin/sh", "-c", script, "sh", str(pid_file)]

    result = transport.invoke(
        invoker=transport.SubprocessInvoker(kill_grace_seconds=0.5),
        argv=argv,
        cwd=brief.parent,
        brief_path=brief,
        stream_path=stream,
        bounds=transport.Bounds(silence_seconds=0.75, total_seconds=20.0),
    )

    assert result.outcome is transport.Outcome.SILENCE
    assert result.exit_status in (-signal.SIGTERM, -signal.SIGKILL)

    grandchild = int(pid_file.read_text(encoding="utf-8").strip())
    deadline = time.monotonic() + 5.0
    while _alive(grandchild) and time.monotonic() < deadline:
        time.sleep(0.05)
    # Killing the parent alone would leave this sleeping worker behind.
    assert not _alive(grandchild)


def test_the_watchdog_still_reaps_the_group_when_the_parent_exited_first(
    call_paths: tuple[Path, Path], tmp_path: Path
) -> None:
    """The async-dispatch shape: the parent returns, its worker holds the pipe.

    This is the case a ``poll()``-guarded kill skipped, and the case that made
    a 0.75s silence bound return in the worker's own time.
    Both halves are asserted here: the orphan dies, and capture comes back on
    the bound rather than tracking the orphan's lifetime.
    """
    brief, stream = call_paths
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
    argv = ["/bin/sh", "-c", script, "sh", str(pid_file)]

    started = time.monotonic()
    result = transport.invoke(
        invoker=transport.SubprocessInvoker(kill_grace_seconds=0.5),
        argv=argv,
        cwd=brief.parent,
        brief_path=brief,
        stream_path=stream,
        bounds=transport.Bounds(silence_seconds=0.75, total_seconds=60.0),
    )
    elapsed = time.monotonic() - started

    assert result.outcome is transport.Outcome.SILENCE
    # The bound is the bound. Anything near `worker_seconds` is the defect.
    assert elapsed < worker_seconds / 2

    orphan = int(pid_file.read_text(encoding="utf-8").strip())
    deadline = time.monotonic() + 5.0
    while _alive(orphan) and time.monotonic() < deadline:
        time.sleep(0.05)
    assert not _alive(orphan)
