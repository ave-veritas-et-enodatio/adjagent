"""The ``start`` stage's rows, and the one thing that tells a launch from a resume.

There is no build mode. An invocation that finds ``start`` unrecorded is
opening a build and walks this stage; one that finds it recorded is continuing
a build and skips every row of it. That single fact carries the launch guard:
``pre.kb-root`` refuses to open a build over a ``kb-root/`` holding documents
this build did not write, and never fires on a resume, because a resume never
reaches it.

The ledger is a fake here, as it is in ``test_kb_driver_buildout.py``: the
subject is which rows the walk runs and what they read off the filesystem, and
a real commit trail behind every case would be a second suite's answer to a
question this one asks of the loop. What is real is the reading — the guard
calls ``kb_util.kb_root_state`` against a directory this file lays down.
"""

import json
from collections.abc import Sequence
from pathlib import Path

import pytest

from kb_tools import kb_pipeline, kb_util
from kb_tools.kb_driver import barriers, baton, config, ledger, run, runlog, steps

PREFLIGHT = (
    "[preflight] PASS git-repo          clean\n"
    "[preflight] FACT runner-file       justfile (runner: just)\n"
    "[preflight] PASS: environment ready.\n"
)


def _render(recorded: Sequence[str]) -> str:
    """A ``show-status`` render the walk can read its position out of."""
    lines = [f"[kb-build] status: {len(recorded)} recorded"]
    lines += [f"[{'x' if stage in recorded else ' '}] {stage}  {stage} display" for stage in kb_pipeline.STAGE_IDS]
    return "\n".join(lines) + "\n"


class Calls:
    """What each faked ledger op was asked to do, so a skipped row is observable."""

    def __init__(self, recorded: Sequence[str] = ()) -> None:
        self.recorded = list(recorded)
        self.started = 0
        self.document_graphs = 0

    def _start(self, *, charter: str) -> ledger.Outcome:
        del charter
        self.started += 1
        self.recorded.append(kb_pipeline.FIRST_STAGE_ID)
        return ledger.Outcome(baton.EXIT_OK)

    def _document_graph(self, *, sources: Sequence[str], bibliographies: Sequence[str], kb_root: str) -> ledger.Outcome:
        del sources, bibliographies, kb_root
        self.document_graphs += 1
        return ledger.Outcome(baton.EXIT_OK)

    def _advance(self, *, stage: str, note: str = "", no_inference: bool = False) -> ledger.Outcome:
        del note, no_inference
        self.recorded.append(stage)
        return ledger.Outcome(baton.EXIT_OK)

    def ops(self) -> run.LedgerOps:
        return run.LedgerOps(
            preflight=lambda: ledger.Outcome(baton.EXIT_OK, stdout=PREFLIGHT),
            graph_init=lambda *, runner: ledger.Outcome(baton.EXIT_OK),
            document_graph=self._document_graph,
            claim_graph=lambda *, flags: ledger.Outcome(baton.EXIT_OK),
            start_build=self._start,
            advance_step=self._advance,
            show_status=lambda *, relay: ledger.Outcome(baton.EXIT_OK, stdout=_render(self.recorded)),
            run_target=lambda *, target: ledger.Outcome(baton.EXIT_OK),
        )


def _repo(tmp_path: Path, *, state: str) -> Path:
    """A repo root whose ``kb-root/`` is in one of the three states, and the lock held.

    Keyed on the tri-state rather than on a shape, so the three cases below are
    the three values and cannot drift into two spellings of one of them.
    """
    root = tmp_path / "repo"
    root.mkdir()
    if state == kb_util.KB_ROOT_SPINE_ONLY:
        kb_util.index_dir(root).mkdir(parents=True)
    elif state == kb_util.KB_ROOT_POPULATED:
        kb_util.kb_root(root).mkdir(parents=True)
        (kb_util.kb_root(root) / "entry-point.md").write_text("# KB\n", encoding="utf-8")
    assert kb_util.kb_root_state(root) == state

    lock = runlog.repo_lock_path(root)
    lock.parent.mkdir(parents=True, exist_ok=True)
    lock.write_text(json.dumps({"pid": 1, "run_id": "start-suite"}), encoding="utf-8")
    return root


def _drive(
    root: Path,
    tmp_path: Path,
    *,
    calls: Calls,
    decisions: Sequence[str] = ("start.proceed=yes",),
    stages: Sequence[str] = ("start",),
) -> run.Result:
    return run.execute(
        config=config.load(None, run_overrides={"sources": ("AcmeWidgets.tex",)}, admissible=barriers.ADMISSIBLE),
        paths=runlog.prepare(tmp_path / "runs", "20260901T120000-1"),
        decisions=[config.parse_decision(spec, admissible=barriers.ADMISSIBLE) for spec in decisions],
        repo_root=root,
        ops=calls.ops(),
        stages=stages,
    )


# ---------------------------------------------------------------------------
# The launch guard, one case per kb_root_state value
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("state", [kb_util.KB_ROOT_ABSENT, kb_util.KB_ROOT_SPINE_ONLY])
def test_a_build_opens_over_a_kb_root_with_nothing_authored_in_it(state: str, tmp_path: Path) -> None:
    """The two states that proceed, and why ``spine-only`` is one of them.

    ``absent`` is the ordinary launch — the document graph creates the tree.
    ``spine-only`` means the directory holds nothing outside ``.index/``, which
    is derived space rebuilt unconditionally from the authored Markdown: there
    is no authored byte for the tree derivation to overwrite, so a seeded but
    uncontented KB is opened over exactly as an absent one is.
    """
    calls = Calls()

    result = _drive(_repo(tmp_path, state=state), tmp_path, calls=calls)

    assert result.exit_code == baton.EXIT_OK, result.detail
    assert calls.started == 1
    assert kb_pipeline.FIRST_STAGE_ID in calls.recorded


def test_a_build_refuses_to_open_over_a_populated_kb_root(tmp_path: Path) -> None:
    """The state that destroys work, refused before the first write.

    Exit 14 with a ``restore:`` clause, which is the card that relays those
    lines as printed — the refusal is a fact about the repository and there is
    no answer an operator could supply to a barrier instead. The state it found
    is named, because ``populated`` and ``spine-only`` are one directory apart
    and a message saying only "kb-root/ is in the way" leaves the reader to
    guess which reading refused them.
    """
    calls = Calls()

    result = _drive(_repo(tmp_path, state=kb_util.KB_ROOT_POPULATED), tmp_path, calls=calls)

    assert result.exit_code == baton.EXIT_ENVIRONMENT
    detail = " ".join(result.detail)
    assert kb_util.KB_ROOT_POPULATED in detail
    assert kb_util.KB_DIRNAME in detail
    assert "restore:" in detail
    assert calls.started == 0, "nothing was recorded, so the refusal costs the ledger nothing"


def test_every_kb_root_state_has_a_case_here() -> None:
    """The tri-state is closed, and this file answers for all of it.

    A fourth value would arrive as a state nothing above decides, which is the
    silent case: the guard's ``!=`` would let it through with no test saying so.
    """
    assert set(kb_util.KB_ROOT_STATES) == {
        kb_util.KB_ROOT_ABSENT,
        kb_util.KB_ROOT_SPINE_ONLY,
        kb_util.KB_ROOT_POPULATED,
    }


# ---------------------------------------------------------------------------
# Resume
# ---------------------------------------------------------------------------


def test_a_resume_continues_from_the_recorded_stage_with_no_mode_on_the_command_line(tmp_path: Path) -> None:
    """The other half of the guard: a recorded ``start`` is what says "resume".

    The repository is the one the launch guard just refused — a populated
    ``kb-root/`` — and this invocation walks straight past it into the stage the
    ledger left unrecorded, carrying no flag, no ``--decide`` and no config key
    to say so. That is the whole mechanism R-A leaves standing: the recorded set
    is re-read every invocation and is the only thing that distinguishes the two.
    """
    calls = Calls(recorded=[kb_pipeline.FIRST_STAGE_ID])

    result = _drive(
        _repo(tmp_path, state=kb_util.KB_ROOT_POPULATED),
        tmp_path,
        calls=calls,
        decisions=(),
        stages=("start", "document-graph"),
    )

    assert result.exit_code == baton.EXIT_OK, result.detail
    assert calls.started == 0, "the whole start stage is skipped, the guard and the proceed barrier with it"
    assert calls.document_graphs == 1, "the walk continued into the first unrecorded stage"
    assert "document-graph" in calls.recorded


def test_the_launch_guard_is_the_last_row_before_the_first_write(tmp_path: Path) -> None:
    """Placement is the mechanism, so it is asserted rather than described.

    Two properties, and both would be lost by moving the row. It is in ``start``
    — which is what makes it a *launch* guard, since a resume skips the stage —
    and it is the last row before ``start.record``, so nothing between the
    reading and the first write can change the answer.
    """
    del tmp_path
    rows = [step.id for step in steps.steps_for(kb_pipeline.FIRST_STAGE_ID)]

    assert rows.index("pre.kb-root") == rows.index("start.record") - 1
