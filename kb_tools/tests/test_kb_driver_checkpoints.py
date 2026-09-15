"""What a resume re-spends, and what the last boundary has already paid for.

Successful expensive work is never discarded, and a stage boundary is what says
it was earned (SPEC.md, The Driver's Contract). Three properties follow, and this
file is all three over the head's own rows:

* a row whose boundary landed is not walked again, so its call is not re-issued;
* a row whose boundary did *not* land is walked again, because an output no
  boundary accounts for is discarded rather than reconciled;
* neither case asks an operator anything. A lost boundary is not a wedge: the
  next invocation reads the recorded set, finds the stage missing, and re-runs it.

**The inference is a stub.** ``claims-discovered`` spends its model inside
``kb_claimgraph``, which this suite replaces with a ledger op that counts how many
times it was invoked. That count is the whole subject — what the walk *asks for* a
second time is decided by the recorded set and by nothing else, and asking it of a
real model would price the same assertion in hours.

The ledger is a fake, as it is in ``test_kb_driver_start.py`` and
``test_kb_driver_resume_state.py``: no git repository, no boundary commits, and a
record that can be made to fail the way a killed process leaves one — the stage's
work done and no entry for it.
"""

import json
import logging
from collections.abc import Iterator, Sequence
from pathlib import Path

import pytest

from kb_tools import kb_pipeline, kb_util
from kb_tools.kb_driver import barriers, baton, config, ledger, run, runlog, steps

#: The three head stages this file drives, and the row of each that does the
#: work. Named off the table rather than transcribed: the rows are what carry
#: ``spends_inference``, and a stage that gained or lost one would move the
#: answer here without an edit.
SPINE_SEED = "spine-seed"
CLAIMS_DECLARED = "claims-declared"
CLAIMS_DISCOVERED = "claims-discovered"


def _up_to(stage: str) -> tuple[str, ...]:
    """Every stage before ``stage``, which is the recorded set a resume into it finds."""
    return kb_pipeline.STAGE_IDS[: kb_pipeline.STAGE_IDS.index(stage)]


@pytest.fixture(autouse=True)
def _detach_log_handlers() -> Iterator[None]:
    yield
    driver_log = logging.getLogger("kb_driver")
    for handler in list(driver_log.handlers):
        driver_log.removeHandler(handler)
        handler.close()


def _render(recorded: Sequence[str]) -> str:
    """A ``show-status`` render the walk reads its position out of."""
    lines = [f"[kb-build] status: in progress ({len(recorded)} recorded)"]
    lines += [f"[{'x' if stage in recorded else ' '}] {stage}  {stage} display" for stage in kb_pipeline.STAGE_IDS]
    return "\n".join(lines) + "\n"


class Ledger:
    """The recorded set, the calls each op served, and a record that can be lost.

    ``loses`` names the stages whose first record fails. That is the state a
    killed process leaves behind — the stage's work ran and no boundary accounts
    for it — and it fails once, because the kill was the process's and not the
    stage's.
    """

    def __init__(self, *, recorded: Sequence[str] = (), loses: Sequence[str] = ()) -> None:
        self.recorded = list(recorded)
        self.claim_graph_flags: list[tuple[str, ...]] = []
        self.graph_inits = 0
        self._loses = list(loses)

    def _advance(self, *, stage: str, note: str = "", no_inference: bool = False) -> ledger.Outcome:
        del note, no_inference
        if stage in self._loses:
            self._loses.remove(stage)
            return ledger.Outcome(baton.EXIT_ENVIRONMENT, detail=(f"the boundary commit for {stage} did not land",))
        self.recorded.append(stage)
        return ledger.Outcome(baton.EXIT_OK)

    def _claim_graph(self, *, flags: Sequence[str]) -> ledger.Outcome:
        self.claim_graph_flags.append(tuple(flags))
        return ledger.Outcome(baton.EXIT_OK)

    def _graph_init(self, *, runner: str | None) -> ledger.Outcome:
        del runner
        self.graph_inits += 1
        return ledger.Outcome(baton.EXIT_OK)

    def invocations(self, stage: str) -> int:
        """How many times the tool was invoked as ``stage``, resolved through the table.

        Through ``kb_pipeline.claimgraph_stage`` rather than by matching flags, so
        this counts the stage the tool would have run rather than a spelling of
        its command line.
        """
        return sum(1 for flags in self.claim_graph_flags if _stage_of(flags) == stage)

    def ops(self) -> run.LedgerOps:
        return run.LedgerOps(
            preflight=lambda: ledger.Outcome(baton.EXIT_OK, stdout="[preflight] PASS: environment ready.\n"),
            graph_init=self._graph_init,
            document_graph=lambda *, sources, bibliographies, kb_root: ledger.Outcome(baton.EXIT_OK),
            claim_graph=self._claim_graph,
            start_build=lambda *, charter: ledger.Outcome(baton.EXIT_OK),
            advance_step=self._advance,
            show_status=lambda *, relay: ledger.Outcome(baton.EXIT_OK, stdout=_render(self.recorded)),
            run_target=lambda *, target: ledger.Outcome(baton.EXIT_OK),
        )


def _stage_of(flags: Sequence[str]) -> str | None:
    """The stage one ``kb_claimgraph`` command line is, read back off the table."""
    values = list(flags)
    which_pass = int(values[values.index("--pass") + 1])
    scope = values[values.index("--scope") + 1] if "--scope" in values else None
    stage = kb_pipeline.claimgraph_stage(which_pass=which_pass, scope=scope)
    return None if stage is None else stage.id


def _repo(tmp_path: Path) -> Path:
    """A repo root carrying a runner file, with the run lock held.

    The runner file is there so ``spine-seed.runner-choice`` is never raised: the
    subject here is what a resume re-spends with no operator in the loop, and a
    barrier would be an operator in the loop.
    """
    root = tmp_path / "repo"
    root.mkdir()
    (root / "justfile").write_text("default:\n", encoding="utf-8")
    assert kb_util.detected_runner(root) is not None

    lock = runlog.repo_lock_path(root)
    lock.parent.mkdir(parents=True, exist_ok=True)
    lock.write_text(json.dumps({"pid": 1, "run_id": "checkpoint-suite"}), encoding="utf-8")
    return root


def _drive(root: Path, tmp_path: Path, *, ledger_ops: Ledger, stages: Sequence[str], serial: int) -> run.Result:
    """One invocation over ``stages``. A second one takes its own run directory."""
    paths = runlog.prepare(tmp_path / "runs", f"20260901T120000-{serial}")
    return run.execute(
        config=config.load(None, run_overrides={"sources": ("AcmeWidgets.tex",)}, admissible=barriers.ADMISSIBLE),
        paths=paths,
        repo_root=root,
        ops=ledger_ops.ops(),
        stages=stages,
    )


# ---------------------------------------------------------------------------
# The row that spends inside a tool
# ---------------------------------------------------------------------------


def test_the_discovery_row_spends_inference_and_its_boundary_is_the_row_behind_it() -> None:
    """The premise the two cases below rest on, asserted rather than assumed.

    ``discover.build`` is the head's own-inference row and ``discover.record`` is
    the row after it. If a row were ever inserted between them, both cases would
    still pass while the guarantee they are about had gone.
    """
    rows = steps.steps_for(CLAIMS_DISCOVERED)

    assert [step.id for step in rows] == ["discover.build", "discover.record"]
    assert rows[0].spends_inference
    assert rows[-1].ledger_op is steps.LedgerOp.ADVANCE_STEP


def test_a_resume_past_a_recorded_boundary_does_not_re_spend_the_row_behind_it(tmp_path: Path) -> None:
    """The boundary landed, so the work is behind the build and the tool is not re-invoked."""
    root = _repo(tmp_path)
    fake = Ledger(recorded=_up_to(CLAIMS_DISCOVERED))

    first = _drive(root, tmp_path, ledger_ops=fake, stages=(*_up_to(CLAIMS_DISCOVERED), CLAIMS_DISCOVERED), serial=1)

    assert first.exit_code == baton.EXIT_OK, first.detail
    assert fake.invocations(CLAIMS_DISCOVERED) == 1
    assert CLAIMS_DISCOVERED in fake.recorded

    second = _drive(root, tmp_path, ledger_ops=fake, stages=(*_up_to(CLAIMS_DISCOVERED), CLAIMS_DISCOVERED), serial=2)

    assert second.exit_code == baton.EXIT_OK, second.detail
    assert fake.invocations(CLAIMS_DISCOVERED) == 1, "a recorded stage is not re-walked, so its spend is not repeated"


def test_a_resume_after_a_lost_boundary_re_spends_the_row_it_cannot_account_for(tmp_path: Path) -> None:
    """No boundary accounts for it, so it is discarded and the resume re-runs it.

    This is the other half of the same rule and the reason the boundary sits
    immediately behind the spend: what a resume can lose is bounded by one row's
    work, and there is nothing for a later invocation to reconcile, diagnose or
    ask a human about.
    """
    root = _repo(tmp_path)
    fake = Ledger(recorded=_up_to(CLAIMS_DISCOVERED), loses=(CLAIMS_DISCOVERED,))
    walk = (*_up_to(CLAIMS_DISCOVERED), CLAIMS_DISCOVERED)

    stopped = _drive(root, tmp_path, ledger_ops=fake, stages=walk, serial=1)

    assert stopped.exit_code == baton.EXIT_ENVIRONMENT
    assert CLAIMS_DISCOVERED not in fake.recorded
    assert fake.invocations(CLAIMS_DISCOVERED) == 1

    resumed = _drive(root, tmp_path, ledger_ops=fake, stages=walk, serial=2)

    assert resumed.exit_code == baton.EXIT_OK, resumed.detail
    assert fake.invocations(CLAIMS_DISCOVERED) == 2, "the stage is unrecorded, so the walk re-enters it"
    assert CLAIMS_DISCOVERED in fake.recorded


# ---------------------------------------------------------------------------
# The two wedges a lost record used to be
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("stage", [SPINE_SEED, CLAIMS_DECLARED])
def test_a_killed_record_row_resumes_with_no_operator_intervention(tmp_path: Path, stage: str) -> None:
    """A build row that succeeded and a record row that failed is not a state to unpick.

    Both of these stages used to be read as a wedge — the work landed in the tree
    and the ledger did not know — and the remedy on offer was a person deciding
    which half to believe. There is no such state: the tree the stage wrote is
    unaccounted for and therefore discarded, so the resume re-runs the stage from
    its first row, raises nothing, and needs no ``--decide``.
    """
    root = _repo(tmp_path)
    fake = Ledger(recorded=_up_to(stage), loses=(stage,))
    walk = (*_up_to(stage), stage)

    stopped = _drive(root, tmp_path, ledger_ops=fake, stages=walk, serial=1)

    assert stopped.exit_code == baton.EXIT_ENVIRONMENT
    assert stopped.pair == "", "a lost boundary asks nobody anything"
    assert stage not in fake.recorded

    resumed = _drive(root, tmp_path, ledger_ops=fake, stages=walk, serial=2)

    assert resumed.exit_code == baton.EXIT_OK, resumed.detail
    assert resumed.pair == ""
    assert resumed.unconsumed_decisions == (), "the resume was given no answer, because it needed none"
    assert fake.recorded[-1] == stage
