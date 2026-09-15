"""What a resuming invocation reads, and what it says about an answer it did not use.

Two properties, and they are two halves of one defect. A row that needed a
reading taken by a row of an *earlier stage* got it from an instance attribute,
so on an invocation that resumes past that stage the attribute held its
constructor default: ``spine-seed.runner-choice`` could not be raised on any
resume, and a ``--decide`` answer for it went nowhere. The first half is fixed
by taking the reading at the point of use; the second is fixed by saying so,
because an answer that decided nothing must not be silent.

The ledger is a fake, as it is in ``test_kb_driver_start.py``: the subject is
which rows the walk runs and what they read off the filesystem. What is real is
the reading — the seed row asks ``kb_util.detected_runner`` about a directory
this file lays down, with a runner file in it or without.
"""

import json
import logging
from collections.abc import Iterator, Sequence
from pathlib import Path

import pytest

from kb_tools import kb_pipeline, kb_util
from kb_tools.kb_driver import barriers, baton, config, ledger, run, runlog

RUNNER_CHOICE = barriers.SPINE_SEED_RUNNER_CHOICE
SPINE_SEED = "spine-seed"

# Every stage before `spine-seed` recorded: the state an invocation that resumes
# into the seed finds, and the one on which `pre.preflight` — a `start` row — has
# not run and will not.
RESUMED_TO_SEED: tuple[str, ...] = kb_pipeline.STAGE_IDS[: kb_pipeline.STAGE_IDS.index(SPINE_SEED)]


@pytest.fixture(autouse=True)
def _detach_log_handlers() -> Iterator[None]:
    """One test configures a run log under ``tmp_path``; drop its handlers after."""
    yield
    driver_log = logging.getLogger("kb_driver")
    for handler in list(driver_log.handlers):
        driver_log.removeHandler(handler)
        handler.close()


def _render(recorded: Sequence[str]) -> str:
    """A ``show-status`` render the walk can read its position out of."""
    lines = [f"[kb-build] status: {len(recorded)} recorded"]
    lines += [f"[{'x' if stage in recorded else ' '}] {stage}  {stage} display" for stage in kb_pipeline.STAGE_IDS]
    return "\n".join(lines) + "\n"


class Calls:
    """The faked ledger, recording what the seed row was actually asked for."""

    def __init__(self, recorded: Sequence[str]) -> None:
        self.recorded = list(recorded)
        self.graph_init_runners: list[str | None] = []
        self.preflights = 0

    def _preflight(self) -> ledger.Outcome:
        self.preflights += 1
        return ledger.Outcome(baton.EXIT_OK, stdout="[preflight] PASS: environment ready.\n")

    def _graph_init(self, *, runner: str | None) -> ledger.Outcome:
        self.graph_init_runners.append(runner)
        return ledger.Outcome(baton.EXIT_OK)

    def _advance(self, *, stage: str, note: str = "", no_inference: bool = False) -> ledger.Outcome:
        del note, no_inference
        self.recorded.append(stage)
        return ledger.Outcome(baton.EXIT_OK)

    def ops(self) -> run.LedgerOps:
        return run.LedgerOps(
            preflight=self._preflight,
            graph_init=self._graph_init,
            document_graph=lambda *, sources, bibliographies, kb_root: ledger.Outcome(baton.EXIT_OK),
            claim_graph=lambda *, flags: ledger.Outcome(baton.EXIT_OK),
            start_build=lambda *, charter: ledger.Outcome(baton.EXIT_OK),
            advance_step=self._advance,
            show_status=lambda *, relay: ledger.Outcome(baton.EXIT_OK, stdout=_render(self.recorded)),
            run_target=lambda *, target: ledger.Outcome(baton.EXIT_OK),
        )


def _repo(tmp_path: Path, *, runner_file: str | None) -> Path:
    """A repo root carrying one runner file, or neither, with the run lock held."""
    root = tmp_path / "repo"
    root.mkdir()
    if runner_file is not None:
        (root / runner_file).write_text("default:\n", encoding="utf-8")
    assert (kb_util.detected_runner(root) is not None) == (runner_file is not None)

    lock = runlog.repo_lock_path(root)
    lock.parent.mkdir(parents=True, exist_ok=True)
    lock.write_text(json.dumps({"pid": 1, "run_id": "resume-suite"}), encoding="utf-8")
    return root


def _resume(
    root: Path,
    paths: runlog.RunPaths,
    *,
    calls: Calls,
    decisions: Sequence[str] = (),
) -> run.Result:
    """Walk ``spine-seed`` on an invocation that finds every stage before it recorded."""
    return run.execute(
        config=config.load(None, run_overrides={"sources": ("AcmeWidgets.tex",)}, admissible=barriers.ADMISSIBLE),
        paths=paths,
        decisions=[config.parse_decision(spec, admissible=barriers.ADMISSIBLE) for spec in decisions],
        repo_root=root,
        ops=calls.ops(),
        stages=(*RESUMED_TO_SEED, SPINE_SEED),
    )


def _paths(tmp_path: Path, run_id: str = "20260901T120000-1") -> runlog.RunPaths:
    return runlog.prepare(tmp_path / "runs", run_id)


# ---------------------------------------------------------------------------
# The barrier a resume could not raise
# ---------------------------------------------------------------------------


def test_the_runner_barrier_fires_on_a_resume_into_the_seed(tmp_path: Path) -> None:
    """A runner-less repository, resumed into ``spine-seed``: the barrier is raised.

    ``pre.preflight`` never runs here — it is a ``start`` row and ``start`` is
    recorded, which the assertion on ``preflights`` pins — so the condition the
    barrier turns on has to be read off the working tree by the row that needs
    it. Carried in an attribute it read ``True``, the seed asked ``graph-init``
    for a runner file that is not there, and no answer was ever solicited.
    """
    calls = Calls(recorded=RESUMED_TO_SEED)

    result = _resume(_repo(tmp_path, runner_file=None), _paths(tmp_path), calls=calls)

    assert calls.preflights == 0, "the whole start stage is skipped, so its reading is not available"
    assert result.exit_code == baton.EXIT_BARRIER
    assert result.pair == RUNNER_CHOICE
    assert result.admissible == barriers.spec(RUNNER_CHOICE).answers
    assert calls.graph_init_runners == [], "the seed is not attempted without an answer"
    assert SPINE_SEED not in calls.recorded


def test_a_decide_answer_for_that_barrier_is_consumed_on_the_resume(tmp_path: Path) -> None:
    """The same invocation with the answer supplied: it reaches ``graph-init`` and nothing is left over."""
    calls = Calls(recorded=RESUMED_TO_SEED)

    result = _resume(
        _repo(tmp_path, runner_file=None),
        _paths(tmp_path),
        calls=calls,
        decisions=(f"{RUNNER_CHOICE}={barriers.ANSWER_MAKE}",),
    )

    assert result.exit_code == baton.EXIT_OK, result.detail
    assert calls.graph_init_runners == [barriers.ANSWER_MAKE]
    assert result.unconsumed_decisions == (), "an answer the walk used is not reported as unused"
    assert SPINE_SEED in calls.recorded


def test_a_repository_with_a_runner_file_raises_nothing(tmp_path: Path) -> None:
    """The condition is the repository's, so the barrier stays unraised where one stands."""
    calls = Calls(recorded=RESUMED_TO_SEED)

    result = _resume(_repo(tmp_path, runner_file="justfile"), _paths(tmp_path), calls=calls)

    assert result.exit_code == baton.EXIT_OK, result.detail
    assert calls.graph_init_runners == [None], "graph-init detects the runner itself when one is there"


# ---------------------------------------------------------------------------
# The answer that decided nothing
# ---------------------------------------------------------------------------


def test_an_unconsumed_decision_is_reported_on_stderr_and_in_the_run_log(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """An answer no barrier asked for is named, in the operator's stream and in the evidence.

    The repository carries a runner file, so ``spine-seed.runner-choice`` is
    never raised and the supplied answer decides nothing. Three things are
    asserted about the report: the spec is on **stderr**, where it cannot land
    inside a block a session is about to paste; the whole statement reaches
    ``run.log`` as the record's own message, not only as context a console tee
    drops; and it is not also on stdout, since bytes written to a console
    directly are what the tee suppresses.
    """
    calls = Calls(recorded=RESUMED_TO_SEED)
    spec = f"{RUNNER_CHOICE}={barriers.ANSWER_JUST}"
    paths = _paths(tmp_path)
    runlog.configure(run_log=paths.run_log, level="INFO")

    result = _resume(_repo(tmp_path, runner_file="justfile"), paths, calls=calls, decisions=(spec,))

    assert result.unconsumed_decisions == (spec,)
    captured = capsys.readouterr()
    assert spec in captured.err
    assert spec not in captured.out, "the notice goes to stderr, and the console tee drops its copy"

    logged = [
        json.loads(line)
        for line in paths.run_log.read_text(encoding="utf-8").splitlines()
        if line.strip() and spec in line
    ]
    assert logged, "the report is evidence too, so it is in run.log"
    assert any(record["level"] == "WARNING" and spec in record["message"] for record in logged)
