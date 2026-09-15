"""Tests for the pipeline vocabulary itself (``kb_tools/kb_pipeline.py``).

``test_kb_util.py`` exercises the cards as the CLI renders them; this file
tests the stage table directly, for obligations whose *absence* is the
contract and which therefore have no rendered line to look for — and for the
generated invocations, whose contract is that they are **generated**: the
last section drives each card's underlying constant and asserts the render
followed.
"""

import re
from dataclasses import replace
from pathlib import Path

import pytest

from kb_tools import kb_index_lib, kb_pipeline, kb_util


def _stage(stage_id: str) -> kb_pipeline.Stage:
    stage = next((s for s in kb_pipeline.STAGES if s.id == stage_id), None)
    assert stage is not None, f"no stage {stage_id} in STAGES"
    return stage


def test_only_phase_3a_has_a_pre_commit_hook() -> None:
    """Pins the ``advance-step`` help text's claim that phase-3a alone writes readiness docs.

    ``kb_util.py``'s ``advance_step_help`` names phase-3a as the one stage that
    writes the KB's readiness docs. That claim is true only because
    :func:`kb_pipeline.stamp_readiness_docs` is wired as phase-3a's
    ``pre_commit`` and no other stage's; if the hook ever moves to a different
    stage (or a second one gains one), this goes red before the help text is
    left describing a stage that no longer writes it.

    The hook sits at the gate rather than at the finish so that every stage
    after it runs against a KB already carrying its orientation docs.
    """
    with_pre_commit = {stage.id for stage in kb_pipeline.STAGES if stage.pre_commit is not None}

    assert with_pre_commit == {"phase-3a"}


def test_a_document_the_readiness_stamp_writes_is_not_also_a_later_stages_contract() -> None:
    """One owner per document, and the earlier one wins whatever a later one declares.

    ``CONVENTIONS.md`` is stamped at ``phase-3a`` from a packaged template whose
    only slot is the project name, so it stands before the meta-documentation
    stages are reached and a boundary check for it there is satisfied by work
    neither stage did. What that costs is not a false green but a lost
    observable: a contract no run can fail cannot tell a stage that did its job
    from one that did nothing.

    The reviewer is still handed both paths — narrowing what a seat is asked to
    read is a different question from what a boundary checks, and this asserts
    only the second.
    """
    assert kb_pipeline.CONVENTIONS_DOC in kb_pipeline.READINESS_DOCS
    assert kb_pipeline.CONVENTIONS_DOC not in kb_pipeline.META_DOCS
    # Not vacuous by emptiness: the set still declares the one document whose
    # opening passage no read of the KB produces.
    assert kb_pipeline.META_DOCS == (kb_pipeline.OVERVIEW_DOC,)


def test_the_meta_documentation_boundary_refuses_on_the_overview_and_on_nothing_else(tmp_path: Path) -> None:
    """The stamped document standing does not buy the stage its boundary.

    The tree here is the one both meta-documentation stages really meet on a run
    whose draft never landed: ``phase-3a`` stamped ``CONVENTIONS.md`` and the
    overview was never written. The refusal names the overview, and the report
    holds no unit the readiness stamp already satisfied — otherwise a stage that
    wrote nothing covers half its contract for free.
    """
    kb = kb_util.kb_root(tmp_path)
    kb.mkdir(parents=True)
    (kb / kb_pipeline.CONVENTIONS_DOC).write_text("# Conventions\n", encoding="utf-8")
    ctx = kb_pipeline.CheckContext(repo_root=tmp_path)

    report = kb_pipeline._check_meta_docs(ctx)

    assert [unit.id for unit in report.units] == [kb_pipeline.OVERVIEW_DOC]
    refusal = kb_pipeline._coverage_refusal(report)
    assert refusal is not None and kb_pipeline.OVERVIEW_DOC in refusal

    # And it is the overview that lifts it: the stamped document was standing
    # the whole time and nothing about the refusal moved until this write.
    (kb / kb_pipeline.OVERVIEW_DOC).write_text("# Overview\n", encoding="utf-8")
    assert kb_pipeline._coverage_refusal(kb_pipeline._check_meta_docs(ctx)) is None


# ---------------------------------------------------------------------------
# Where the build expects the user
# ---------------------------------------------------------------------------


def _gate_named(stage: kb_pipeline.Stage, line: str) -> bool:
    """Whether the sentence names this stage.

    Matched as the whole rendered pair, never as the bare id: one id can be a
    substring of another, so an id test would read the gate as standing where
    it does not.
    """
    return f"{stage.id} ({stage.display})" in line


def test_the_confirmation_sentence_names_exactly_the_stages_the_table_gates() -> None:
    """The flag is the source; the sentence is what is read off it."""
    line = kb_pipeline._user_gate_line()

    named = {stage.id for stage in kb_pipeline.STAGES if _gate_named(stage, line)}
    assert named == {stage.id for stage in kb_pipeline.STAGES if stage.user_gate}
    # Not vacuous: a build with no gate at all would satisfy the equality above.
    assert named


@pytest.mark.parametrize("gated", ["phase-3a", "phase-5"])
def test_moving_the_gate_moves_the_sentence(monkeypatch: pytest.MonkeyPatch, gated: str) -> None:
    """A gate that moves without the flag disappears from the confirmation.

    Driving the table is what says the sentence is derived: a hand-written one
    naming today's gated stage passes the test above and fails this.
    """
    moved = tuple(replace(stage, user_gate=stage.id == gated) for stage in kb_pipeline.STAGES)
    monkeypatch.setattr(kb_pipeline, "STAGES", moved)

    line = kb_pipeline._user_gate_line()

    assert {stage.id for stage in moved if _gate_named(stage, line)} == {gated}


# ---------------------------------------------------------------------------
# The generated invocations
# ---------------------------------------------------------------------------


def _card(stage_id: str, tmp_path: Path) -> str:
    """One stage's card as one blob — the invocations are long, and this reads them whole."""
    return "\n".join(kb_pipeline.card_lines(_stage(stage_id), tmp_path))


# The ops whose invocation needs the render's own context — a stage id, a
# per-consumer runner. Each has a generated item behind it, so none of them
# may appear in a plain string. `OP_SHOW_CONFIRMATION` is not one of these: the
# `start` card's opening line composes it once, from constants alone, at
# STAGES-table-definition time — there is no per-render value it could go
# stale against, unlike a stage id or a repo-relative path.
_GENERATED_OPS = (
    kb_util.OP_START_BUILD,
    kb_util.OP_ADVANCE_STEP,
    kb_util.OP_SHOW_STAGE_STATUS,
)

#: Every op token the CLI declares, taken from ``kb_util``'s own constants
#: rather than listed here: a card may name any of them and nothing else.
_DECLARED_OPS = frozenset(
    value for name, value in vars(kb_util).items() if name.startswith("OP_") and isinstance(value, str)
)

#: The sanctioned prefix, and whatever token follows it. A card line either
#: matches this with a declared op or it hand-wrote a command.
_INVOCATION_RE = re.compile(rf"{re.escape(kb_pipeline._INVOCATION)}\s+(\S+)")


def test_no_card_hand_writes_a_command(tmp_path: Path) -> None:
    """A command a card composed by hand is a command that can go stale unnoticed.

    Two halves, because the cards now carry two kinds of invocation.

    *Render-time ops* name a stage id or the consumer's runner, so each must
    come from a generated item and none may appear in a plain string at all —
    that is what keeps the stage id a card prints and the stage id the ledger
    records one value.

    *The write ops* name none of those: the op token and the prefix are
    the whole of the command, so ``_kb_util_command`` composes them where the
    card is written and there is nothing left for a render to bind. What can
    still go wrong there is a card advertising a subcommand the CLI does not
    have, so every invocation any card carries — prose or generated — is held
    against ``kb_util``'s own op constants.
    """
    prose = [item for stage in kb_pipeline.STAGES for item in stage.card if isinstance(item, str)]
    rendered = [line for stage in kb_pipeline.STAGES for line in kb_pipeline.card_lines(stage, tmp_path)]

    assert [line for line in prose for op in _GENERATED_OPS if op in line] == []
    assert [op for line in rendered for op in _INVOCATION_RE.findall(line) if op not in _DECLARED_OPS] == []


def test_no_card_carries_a_rendered_manifest_view(tmp_path: Path) -> None:
    """No card line embeds a newline.

    A card's obligations are read into a message a line at a time; anything a
    card names has to fit that grammar, so nothing rendered here may itself be
    multi-line text.
    """
    for stage in kb_pipeline.STAGES:
        for line in kb_pipeline.card_lines(stage, tmp_path):
            assert "\n" not in line, stage.id


@pytest.mark.parametrize(
    ("module", "constant", "value", "stage_ids"),
    [
        pytest.param(kb_util, "OP_START_BUILD", "commence-build", ("start",), id="start-build-op"),
        pytest.param(kb_util, "OP_ADVANCE_STEP", "record-stage", ("phase-3a",), id="advance-step-op"),
    ],
)
def test_moving_the_constant_moves_the_card(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    module: object,
    constant: str,
    value: str,
    stage_ids: tuple[str, ...],
) -> None:
    """The row's real claim: these lines are rendered from the constants, not beside them."""
    before = {stage_id: _card(stage_id, tmp_path) for stage_id in stage_ids}
    monkeypatch.setattr(module, constant, value)

    for stage_id in stage_ids:
        card = _card(stage_id, tmp_path)
        assert value in card, stage_id
        assert card != before[stage_id], stage_id


# ---------------------------------------------------------------------------
# The stage-coverage read
#
# The op's own render, tested where the tree is constructed directly.
# `test_kb_util.py` is where the two consumers are held to one another through
# the running CLI; here it is the shape of one report.
# ---------------------------------------------------------------------------


def _status(stage_id: str, root: Path) -> list[str]:
    return kb_pipeline.stage_status(root, _stage(stage_id))


def test_an_argument_derived_unit_reports_the_question_and_not_the_tree(tmp_path: Path) -> None:
    """``start``'s charter, read where no record is being made.

    A read builds its context with no charter argument, so this unit cannot be
    satisfied here for a reason that is not a fact about the KB. Saying what
    the record would have been missing instead — the detail beside it — would
    be a claim about a tree this call never looked at.
    """
    lines = _status("start", tmp_path)

    assert len(lines) == 2
    assert lines[1].startswith(f"{kb_pipeline.STAGE_STATUS_TAG} {kb_pipeline.MISSING} charter ")
    assert kb_pipeline.ARGUMENT_ON_A_READ in lines[1]
    # The record path's own detail is what it must NOT have said.
    assert "the record names no charter" not in lines[1]


def test_no_read_line_parses_as_a_checklist_entry(tmp_path: Path) -> None:
    """The checklist block keeps its one producer, over every stage's report.

    ``^\\[[x* ]\\] `` is the parse agents lift the checklist with; a status
    line carrying a word in its brackets cannot be mistaken for one.
    """
    checklist = re.compile(r"^\[([x* ])\] (\S+)")

    for stage in kb_pipeline.STAGES:
        for line in kb_pipeline.stage_status(tmp_path, stage):
            assert line.startswith(kb_pipeline.STAGE_STATUS_TAG), (stage.id, line)
            assert not checklist.match(line), (stage.id, line)
            assert not line.startswith(kb_pipeline.CARD_PREFIX), (stage.id, line)


# ---------------------------------------------------------------------------
# The two kinds of coverage check
#
# One kind asserts the stage did its work and has nothing to assert of a build
# that excluded the work. The other asserts the state handed across the
# boundary is valid for what comes next, and no path to the boundary excuses
# it. The classification is the unit's, so a check attached to two stages
# carries one answer to both.
# ---------------------------------------------------------------------------


def _unit(**fields: object) -> kb_pipeline.CoverageUnit:
    return kb_pipeline.CoverageUnit(
        **{"id": "u", "source": "s", "satisfied": True, "asserts_own_work": True, **fields}  # type: ignore[arg-type]
    )


def test_a_units_classification_has_no_default() -> None:
    """Total by construction: a new check cannot forget to say which kind it is.

    A default would pick one silently, and the wrong pick in either direction is
    a defect that reads green — a validity gate excused, or a stage that can
    never record a build it was never asked to do work for.
    """
    with pytest.raises(TypeError):
        kb_pipeline.CoverageUnit(id="u", source="s", satisfied=True)  # type: ignore[call-arg]


def test_the_check_two_stages_share_carries_one_classification(tmp_path: Path) -> None:
    """``_check_verify_gates`` is a validity check at ``depends-attributed`` too.

    The case a per-stage classification breaks: ``depends-attributed`` is a
    stage whose work a no-inference build excludes, so a stage-level rule would
    have excused its postcondition — and that postcondition is the head's exit
    gate over the whole KB, not an assertion about attribution having run.
    """
    sharing = [stage for stage in kb_pipeline.STAGES if stage.coverage is kb_pipeline._check_verify_gates]

    assert {stage.id for stage in sharing} == {"depends-attributed", "phase-3a"}
    for stage in sharing:
        report = stage.coverage(kb_pipeline.CheckContext(tmp_path))
        assert [unit.asserts_own_work for unit in report.units] == [False], stage.id


@pytest.mark.parametrize("stage", [stage for stage in kb_pipeline.STAGES if stage.work_is_inference])
def test_an_inference_stages_own_work_units_are_excused_and_its_validity_units_are_not(
    stage: kb_pipeline.Stage,
) -> None:
    """The rule, applied to each stage whose work is a model call."""
    report = kb_pipeline.CoverageReport.declared(
        (
            _unit(id="did-my-work", satisfied=False, asserts_own_work=True),
            _unit(id="state-validity", satisfied=False, asserts_own_work=False),
        ),
        unit_class="two kinds of unit",
    )
    excused = kb_pipeline._excused(report, stage, kb_pipeline.CheckContext(Path("/nowhere"), no_inference=True))
    by_id = {unit.id: unit for unit in excused.units}

    assert by_id["did-my-work"].satisfied and by_id["did-my-work"].vacuous
    assert by_id["did-my-work"].detail == kb_pipeline.WORK_EXCLUDED_DETAIL
    assert not by_id["state-validity"].satisfied and not by_id["state-validity"].vacuous


@pytest.mark.parametrize("stage", [stage for stage in kb_pipeline.STAGES if not stage.work_is_inference])
def test_a_stage_whose_work_is_not_inference_is_excused_nothing(stage: kb_pipeline.Stage) -> None:
    """Both halves are needed, and this is the half a one-condition rule would lose.

    A build that spent no model call still derived its tree and seeded its
    spine for real. Excusing every own-work unit on the strength of the flag
    alone would record a broken tree green.
    """
    report = kb_pipeline.CoverageReport.declared(
        (_unit(id="did-my-work", satisfied=False, asserts_own_work=True),), degenerate=True
    )
    excused = kb_pipeline._excused(report, stage, kb_pipeline.CheckContext(Path("/nowhere"), no_inference=True))

    assert excused == report


@pytest.mark.parametrize("stage", list(kb_pipeline.STAGES))
def test_a_build_that_spent_inference_is_excused_nothing_either(stage: kb_pipeline.Stage) -> None:
    """The flag is the only door, and it is the build's statement rather than a check's."""
    report = kb_pipeline.CoverageReport.declared(
        (_unit(id="did-my-work", satisfied=False, asserts_own_work=True),), degenerate=True
    )

    assert kb_pipeline._excused(report, stage, kb_pipeline.CheckContext(Path("/nowhere"))) == report


# ---------------------------------------------------------------------------
# The import direction the stage table now depends on
# ---------------------------------------------------------------------------


def test_kb_pipeline_imports_nothing_from_the_claim_graph_builder() -> None:
    """The cycle is impossible rather than held apart by where a line sits.

    ``kb_claimgraph`` reads this stage table, so an import back the other way
    would close a cycle. Two of them used to sit inside function bodies, which
    is exactly the tidy-up a later reader performs — nothing failed first and
    nothing said why they were there. What they reached for now lives in
    ``kb_index_lib``: the document walk, and the awaiting reason.
    """
    source = Path(kb_pipeline.__file__).read_text(encoding="utf-8")
    code = [line for line in source.splitlines() if line.strip().startswith(("import ", "from "))]

    assert [line for line in code if "kb_claimgraph" in line] == []
    assert kb_index_lib.UNSCANNED_REASON
    assert "kb_claimgraph" not in str(kb_index_lib.document_texts.__module__)


def test_the_awaiting_reason_is_one_literal_wherever_it_is_read() -> None:
    """The writer names it and the coverage check reads it — one definition, two sides."""
    from kb_tools.kb_claimgraph import assemble, conform

    assert assemble.UNSCANNED_REASON is kb_index_lib.UNSCANNED_REASON
    assert conform.determination({"no-claim": kb_index_lib.UNSCANNED_REASON}) is conform.Determination.AWAITING


def test_the_document_walk_is_one_walk(tmp_path: Path) -> None:
    """The coverage checks and the claim-graph builder see the same document set.

    Two implementations of the exclusion rules is how one of them starts
    checking a set the other does not — and the coverage check that reads the
    awaiting reason is exactly a question about "every document of this KB".
    """
    from kb_tools.kb_claimgraph import tree

    kb_root = kb_util.kb_root(tmp_path)
    for relative in ("entry-point.md", "vol/index.md", "vol/leaf.md", ".index/skipped.md"):
        target = kb_root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(f"# {relative}\n", encoding="utf-8")

    assert set(kb_index_lib.document_texts(kb_root)) == set(tree.read(kb_root).documents)
    assert ".index/skipped.md" not in kb_index_lib.document_texts(kb_root)
