"""Run-directory layout, the run lock, exit.json, and the log tee.

``exit.json`` is what a session reads after watch mode reports the driver
gone, so its field set is a contract: the terminal code, the barrier record
path, and the ``--decide`` values that were never raised.
"""

import json
import logging
import os
import subprocess
import sys
import threading
from collections.abc import Iterator
from pathlib import Path

import pytest

from kb_tools.kb_driver import runlog

EXIT_JSON_FIELDS = {"exit_code", "barrier_record", "unconsumed_decisions"}


@pytest.fixture
def configured(tmp_path: Path) -> Iterator[runlog.RunPaths]:
    """A prepared run directory with logging attached, torn down after the test."""
    paths = runlog.prepare(tmp_path / "kb-driver", "run-1")
    runlog.configure(run_log=paths.run_log, level="INFO")
    yield paths
    driver_log = logging.getLogger("kb_driver")
    for handler in list(driver_log.handlers):
        driver_log.removeHandler(handler)
        handler.close()


def _records(run_log: Path) -> list[dict]:
    return [json.loads(line) for line in run_log.read_text(encoding="utf-8").splitlines()]


# ---------------------------------------------------------------------------
# Layout
# ---------------------------------------------------------------------------


def test_prepare_lays_out_the_run_directory(tmp_path: Path) -> None:
    paths = runlog.prepare(tmp_path / "kb-driver", "20260901T120000-42")

    assert paths.run_dir.is_dir()
    assert paths.briefs.is_dir() and paths.calls.is_dir() and paths.barriers.is_dir()
    # LATEST lives at the parent; everything else inside the run. The lock is
    # not a run-directory path at all — it belongs to the repo.
    assert paths.latest.parent == paths.parent
    assert not hasattr(paths, "lock")
    assert paths.run_log.parent == paths.run_dir
    assert paths.latest.read_text(encoding="utf-8").strip() == str(paths.run_dir)
    assert paths.run_pid.read_text(encoding="utf-8").strip().isdigit()


def test_prepare_refuses_to_reuse_a_run_directory(tmp_path: Path) -> None:
    parent = tmp_path / "kb-driver"
    runlog.prepare(parent, "run-1")
    with pytest.raises(runlog.BoundaryError, match="already exists"):
        runlog.prepare(parent, "run-1")


def test_run_id_is_sortable_and_unique_per_process() -> None:
    run_id = runlog.new_run_id()
    date, _, pid = run_id.partition("-")
    assert len(date) == len("20260901T120000")
    assert pid.isdigit()


# ---------------------------------------------------------------------------
# exit.json
# ---------------------------------------------------------------------------


def test_exit_json_carries_exactly_the_named_fields(tmp_path: Path) -> None:
    paths = runlog.prepare(tmp_path / "kb-driver", "run-1")
    record = paths.barriers / "phase-1b-design-gate.md"

    written = runlog.write_exit_json(
        paths,
        exit_code=10,
        barrier_record=record,
        unconsumed_decisions=["phase-4.cap-exhausted=authorize-one-more"],
    )
    payload = json.loads(written.read_text(encoding="utf-8"))

    assert set(payload) == EXIT_JSON_FIELDS
    assert payload["exit_code"] == 10
    assert payload["barrier_record"] == str(record)
    assert payload["unconsumed_decisions"] == ["phase-4.cap-exhausted=authorize-one-more"]


def test_exit_json_records_no_barrier_for_a_plain_exit(tmp_path: Path) -> None:
    paths = runlog.prepare(tmp_path / "kb-driver", "run-1")
    payload = json.loads(runlog.write_exit_json(paths, exit_code=0).read_text(encoding="utf-8"))

    assert set(payload) == EXIT_JSON_FIELDS
    assert payload["barrier_record"] is None
    assert payload["unconsumed_decisions"] == []


# ---------------------------------------------------------------------------
# cadence.jsonl
# ---------------------------------------------------------------------------

_STAGES = {"p2.6.apply-scores": "phase-2.6", "p3.distill": "phase-3"}


def _capture(paths: runlog.RunPaths, *, seq: int, label: str, attempt: int, lines: list[str]) -> Path:
    """A capture written through the same grammar the caller composes with."""
    path = runlog.call_stream_path(paths, seq=seq, label=label, attempt=attempt)
    path.write_text("".join(f"{line}\n" for line in lines), encoding="utf-8")
    return path


def _result(*, duration_ms: int, cost: float) -> str:
    return json.dumps({"type": "result", "subtype": "success", "duration_ms": duration_ms, "total_cost_usd": cost})


def test_cadence_carries_one_record_per_capture_with_its_stage(tmp_path: Path) -> None:
    paths = runlog.prepare(tmp_path / "kb-driver", "run-1")
    _capture(paths, seq=11, label="p2.6.apply-scores", attempt=1, lines=[_result(duration_ms=1200, cost=0.25)])
    _capture(paths, seq=12, label="p3.distill", attempt=1, lines=[_result(duration_ms=900, cost=0.5)])

    records = runlog.read_cadence(paths, stages=_STAGES)

    assert records == [
        {
            "seq": 11,
            "step": "p2.6.apply-scores",
            "stage": "phase-2.6",
            "attempt": 1,
            "re_ask": False,
            "duration_ms": 1200,
            "cost_usd": 0.25,
        },
        {
            "seq": 12,
            "step": "p3.distill",
            "stage": "phase-3",
            "attempt": 1,
            "re_ask": False,
            "duration_ms": 900,
            "cost_usd": 0.5,
        },
    ]


def test_a_re_ask_and_a_hyphenated_step_id_are_told_apart(tmp_path: Path) -> None:
    """``p2.6.apply-scores`` and ``-reask`` both contain the separator.

    A reader splitting on the first or last hyphen gets one of them wrong, which
    is the reasoning ``prompt_templates.step_of`` already carries for the brief grammar.
    """
    paths = runlog.prepare(tmp_path / "kb-driver", "run-1")
    _capture(paths, seq=11, label="p2.6.apply-scores-reask", attempt=2, lines=[_result(duration_ms=5, cost=0.0)])

    (record,) = runlog.read_cadence(paths, stages=_STAGES)

    assert record["step"] == "p2.6.apply-scores"
    assert record["stage"] == "phase-2.6"
    assert record["re_ask"] is True
    assert record["attempt"] == 2


def test_the_last_result_event_wins(tmp_path: Path) -> None:
    """A headless call can emit a premature result, a second init, then the real one.

    Reading the first would report a fraction of what the call actually cost.
    """
    paths = runlog.prepare(tmp_path / "kb-driver", "run-1")
    _capture(
        paths,
        seq=1,
        label="p3.distill",
        attempt=1,
        lines=[
            _result(duration_ms=10, cost=0.01),
            json.dumps({"type": "system", "subtype": "init"}),
            _result(duration_ms=4000, cost=1.75),
        ],
    )

    (record,) = runlog.read_cadence(paths, stages=_STAGES)

    assert (record["duration_ms"], record["cost_usd"]) == (4000, 1.75)


def test_a_capture_with_no_result_event_contributes_nothing(tmp_path: Path) -> None:
    """The call died in transport: its duration is the watchdog's story, not the model's."""
    paths = runlog.prepare(tmp_path / "kb-driver", "run-1")
    _capture(paths, seq=1, label="p3.distill", attempt=1, lines=[json.dumps({"type": "system", "subtype": "init"})])
    _capture(paths, seq=2, label="p3.distill", attempt=1, lines=["{ not json", _result(duration_ms=7, cost=0.0)])

    records = runlog.read_cadence(paths, stages=_STAGES)

    assert [record["seq"] for record in records] == [2]


def test_an_unparsable_capture_name_is_logged_and_skipped_not_raised(configured: runlog.RunPaths) -> None:
    """An evidence pass on the way out of a finished run must not turn it into exit 15."""
    paths = configured
    (paths.calls / f"not-a-capture{runlog.CALL_STREAM_SUFFIX}").write_text(
        _result(duration_ms=1, cost=0.0) + "\n", encoding="utf-8"
    )

    assert runlog.read_cadence(paths, stages=_STAGES) == []
    assert any("does not parse" in record["message"] for record in _records(paths.run_log))


def test_cadence_is_written_even_when_the_run_made_no_calls(tmp_path: Path) -> None:
    """ "Absent" must never have to be told apart from "the extraction did not run"."""
    paths = runlog.prepare(tmp_path / "kb-driver", "run-1")

    written = runlog.write_cadence(paths, stages=_STAGES)

    assert written == paths.cadence
    assert written.read_text(encoding="utf-8") == ""


def test_write_cadence_emits_one_json_object_per_line(tmp_path: Path) -> None:
    paths = runlog.prepare(tmp_path / "kb-driver", "run-1")
    _capture(paths, seq=1, label="p3.distill", attempt=1, lines=[_result(duration_ms=3, cost=0.0)])

    lines = runlog.write_cadence(paths, stages=_STAGES).read_text(encoding="utf-8").splitlines()

    assert [json.loads(line)["step"] for line in lines] == ["p3.distill"]


# ---------------------------------------------------------------------------
# The lock (pre.lock)
# ---------------------------------------------------------------------------


def test_the_lock_is_anchored_at_the_repo_root_not_the_run_directory(tmp_path: Path) -> None:
    """Two ``--run-dir`` values under one repo must contend for one lock.

    The lock used to be ``<run-dir-parent>/run.lock``, so this pair took two
    different locks and both proceeded against one KB — the cross-process
    guarantee enforced nothing, while exit 16's baton claimed it had.
    """
    repo = tmp_path / "repo"
    repo.mkdir()
    assert runlog.repo_lock_path(repo) == repo / ".claude-temp" / "kb-driver.lock"

    with runlog.run_lock(runlog.repo_lock_path(repo), run_id="run-under-run-dir-a"):
        with pytest.raises(runlog.LockedError):
            with runlog.run_lock(runlog.repo_lock_path(repo), run_id="run-under-run-dir-b"):
                pytest.fail("a second run directory under the same repo must not acquire")


def test_lock_is_held_for_the_run_and_released_after(tmp_path: Path) -> None:
    path = runlog.repo_lock_path(tmp_path)
    with runlog.run_lock(path, run_id="run-1") as lock:
        held = json.loads(lock.read_text(encoding="utf-8"))
        assert held["run_id"] == "run-1"
        assert held["pid"] > 0
    assert not lock.exists()


def test_a_live_holder_refuses_the_lock(tmp_path: Path) -> None:
    path = runlog.repo_lock_path(tmp_path)
    with runlog.run_lock(path, run_id="run-1"):
        with pytest.raises(runlog.LockedError) as excinfo:
            with runlog.run_lock(path, run_id="run-2"):
                pytest.fail("the second run must not acquire a held lock")
    assert excinfo.value.pid is not None


def test_a_stale_lock_is_taken_over(tmp_path: Path) -> None:
    path = runlog.repo_lock_path(tmp_path)
    path.parent.mkdir(parents=True)
    reaped = subprocess.Popen([sys.executable, "-c", "pass"])
    reaped.wait()
    path.write_text(json.dumps({"pid": reaped.pid, "run_id": "old"}), encoding="utf-8")

    with runlog.run_lock(path, run_id="run-2") as lock:
        assert json.loads(lock.read_text(encoding="utf-8"))["run_id"] == "run-2"


def test_an_unreadable_lock_is_treated_as_stale(tmp_path: Path) -> None:
    path = runlog.repo_lock_path(tmp_path)
    path.parent.mkdir(parents=True)
    path.write_text("not json at all", encoding="utf-8")

    with runlog.run_lock(path, run_id="run-2") as lock:
        assert json.loads(lock.read_text(encoding="utf-8"))["run_id"] == "run-2"


def test_the_lock_is_never_visible_without_its_payload(tmp_path: Path) -> None:
    """The create-then-write shape left a zero-byte lock a rival read as stale.

    The acquire is atomic from any other starter's point of view, so there is
    no state in which the file exists and does not yet name its holder. This
    drives it the only way that is observable from outside: the moment the path
    exists, it parses.
    """
    path = runlog.repo_lock_path(tmp_path)
    seen: list[str] = []

    def watch() -> None:
        while len(seen) < 200:
            try:
                seen.append(path.read_text(encoding="utf-8"))
            except OSError:
                pass

    watcher = threading.Thread(target=watch, daemon=True)
    watcher.start()
    for round_number in range(200):
        with runlog.run_lock(path, run_id=f"run-{round_number}"):
            pass
    watcher.join(timeout=5.0)

    assert seen, "the watcher never caught the lock in existence"
    for text in seen:
        assert json.loads(text)["pid"] > 0


def test_release_leaves_a_lock_this_run_no_longer_owns(tmp_path: Path) -> None:
    """Release unlinked by path, so A's exit deleted B's live lock."""
    path = runlog.repo_lock_path(tmp_path)

    with runlog.run_lock(path, run_id="holder-a"):
        # Exactly what run_lock's own stale-recovery path does to a lock it has
        # judged dead: replace it. B is live and holds it now.
        path.unlink()
        path.write_text(json.dumps({"pid": os.getpid(), "run_id": "holder-b", "token": "b"}), encoding="utf-8")

    assert path.is_file(), "A's release deleted B's live lock"
    assert json.loads(path.read_text(encoding="utf-8"))["run_id"] == "holder-b"


def test_a_stale_recovery_that_loses_the_race_refuses_rather_than_double_holding(tmp_path: Path) -> None:
    """Two starters recovering one stale lock must not both end up holding it.

    The window is between judging a lock stale and removing it. Here it is
    driven deterministically: a live holder publishes inside that window, and
    the recovering starter must find its judgement out of date.
    """
    path = runlog.repo_lock_path(tmp_path)
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps({"pid": 999_999, "run_id": "crashed"}), encoding="utf-8")

    real_holder = runlog._holder

    def holder_then_a_live_rival_publishes(candidate: Path) -> int | None:
        verdict = real_holder(candidate)
        if verdict is None:  # judged stale — a live starter takes it in the window
            candidate.write_text(
                json.dumps({"pid": os.getpid(), "run_id": "rival", "token": "rival"}), encoding="utf-8"
            )
        return verdict

    runlog._holder = holder_then_a_live_rival_publishes
    try:
        with pytest.raises(runlog.LockedError):
            with runlog.run_lock(path, run_id="recovering"):
                pytest.fail("must not acquire over a lock that changed under the staleness judgement")
    finally:
        runlog._holder = real_holder

    assert json.loads(path.read_text(encoding="utf-8"))["run_id"] == "rival"


# ---------------------------------------------------------------------------
# Logging and the relay
# ---------------------------------------------------------------------------


def test_run_log_is_jsonl_and_carries_context(configured: runlog.RunPaths) -> None:
    runlog.logger("test").info("run started", extra={"context": {"run_id": "run-1"}})

    record = _records(configured.run_log)[0]
    assert record["message"] == "run started"
    assert record["level"] == "INFO"
    assert record["logger"] == "kb_driver.test"
    assert record["context"] == {"run_id": "run-1"}
    assert record["ts"]


def test_relay_writes_verbatim_bytes_once(configured: runlog.RunPaths, capsys: pytest.CaptureFixture[str]) -> None:
    block = "[relay] ---\n[relay] ASK THE USER:\n[relay]   none"
    runlog.relay(block)

    out = capsys.readouterr().out
    assert out == block + "\n"  # no level prefix, and no second copy from the console tee
    assert _records(configured.run_log)[0]["message"] == block


def test_require_raises_and_logs_on_violation(configured: runlog.RunPaths) -> None:
    runlog.require(True, "never raised")
    with pytest.raises(runlog.BoundaryError, match="spawn while one is live"):
        runlog.require(False, "second spawn while one is live", step="p0.survey")

    record = _records(configured.run_log)[0]
    assert record["level"] == "ERROR"
    assert record["context"] == {"step": "p0.survey"}
