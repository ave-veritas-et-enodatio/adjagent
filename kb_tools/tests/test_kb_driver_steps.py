"""The step table: stage coverage, row shape, and single-sourcing.

The table holds rows for every stage of the pipeline and a run walks all of
them, so the coverage assertions are against the whole stage vocabulary. The
table/walk split is retired with rung 1; what replaces it is the assertion
below, which is stronger — not "the walk lags the table" but "the two are the
same set, and it is the stage vocabulary's".

Nothing here tests what a row *does*; that is the run loop's suites. These
check that the table cannot silently disagree with the stage vocabulary, the
cap constants, or the barrier registry's naming.
"""

import argparse
import inspect

import pytest

from kb_tools import kb_pipeline, kb_util
from kb_tools.kb_driver import call, envelope, prompt_templates, replay, run, steps

# ---------------------------------------------------------------------------
# Stage coverage
# ---------------------------------------------------------------------------


def test_the_table_covers_the_whole_stage_vocabulary_and_the_walk_covers_the_table() -> None:
    """Rung 1's structural claim: rows for every stage, and a walk over every row.

    A stage appended to ``kb_pipeline.STAGE_IDS`` fails this rather than being
    silently walked with no rows, and a walk narrowed back below the table
    fails it rather than quietly buying a green by not going there.
    """
    assert steps.TABLE_STAGE_IDS == kb_pipeline.STAGE_IDS
    assert len(steps.TABLE_STAGE_IDS) == 8


def test_every_row_of_the_table_is_executed_by_a_handler_or_driven_by_a_loop() -> None:
    """The other half: a row nothing runs is a row the sequencer has silently dropped."""
    unexecuted = set(steps.STEP_IDS) - set(run._HANDLERS) - run.LOOP_DRIVEN_STEPS

    assert not unexecuted


def test_no_handler_and_no_loop_row_names_a_row_the_table_does_not_hold() -> None:
    assert set(run._HANDLERS) <= set(steps.STEP_IDS)
    assert run.LOOP_DRIVEN_STEPS <= set(steps.STEP_IDS)


def test_every_calling_row_of_every_stage_is_scripted_by_the_replay() -> None:
    """The replay criterion, as a set equality rather than a spot check.

    A calling row with no scenario is a replayed run that stops at ``by_step``'s
    boundary check; a scenario for a row the table does not hold is a shape
    nothing asks for. Asserted here as well as in the replay suite because the
    two fail for different reasons — a new row here, a stale scenario there —
    and the message a reader gets matters.
    """
    calling = {step.id for step in steps.STEPS if step.unit in call.CALL_UNITS}

    assert calling == set(replay.SCENARIOS)


def test_each_stage_of_the_table_appears_once_in_stage_id_order() -> None:
    """One contiguous run of rows per stage, in the order ``STAGE_IDS`` fixes."""
    appearances = tuple(dict.fromkeys(step.stage for step in steps.STEPS))

    assert appearances == steps.TABLE_STAGE_IDS


@pytest.mark.parametrize("stage", steps.TABLE_STAGE_IDS)
def test_every_stage_of_the_table_ends_in_exactly_one_ledger_row(stage: str) -> None:
    """A stage is recorded once, by its last row: the ledger is written only by the sanctioned ops."""
    rows = steps.steps_for(stage)
    recording = [row for row in rows if row.ledger_op is not None]

    assert rows
    assert len(recording) == 1
    assert recording[0] is rows[-1]


def test_only_the_first_stage_starts_the_build() -> None:
    starting = [step.id for step in steps.STEPS if step.ledger_op is steps.LedgerOp.START_BUILD]

    assert starting == ["start.record"]
    assert steps.STEPS_BY_ID["start.record"].stage == kb_pipeline.FIRST_STAGE_ID


# ---------------------------------------------------------------------------
# Row shape
# ---------------------------------------------------------------------------


def test_step_ids_are_unique_and_indexed() -> None:
    assert len(steps.STEP_IDS) == len(set(steps.STEP_IDS))
    assert set(steps.STEPS_BY_ID) == set(steps.STEP_IDS)


CALL_UNITS = (steps.Unit.SINGLE, steps.Unit.WAVE, steps.Unit.WAVE_STAR)
CALL_ROWS = [step for step in steps.STEPS if step.unit in CALL_UNITS]
CALL_IDS = [step.id for step in CALL_ROWS]


@pytest.mark.parametrize("step", steps.STEPS, ids=steps.STEP_IDS)
def test_only_a_row_that_calls_inference_carries_a_brief(step: steps.Step) -> None:
    if step.unit in CALL_UNITS:
        assert step.template and step.template.endswith(".tmpl")
    else:
        assert step.template is None
        assert step.slots == ()


@pytest.mark.parametrize("step", CALL_ROWS, ids=CALL_IDS)
def test_a_templates_name_matches_its_call_unit(step: steps.Step) -> None:
    """The naming convention the lint reads to enforce: a template's name reflects its call unit."""
    infix = ".single." if step.unit is steps.Unit.SINGLE else ".wave."

    assert step.template is not None and infix in step.template


SEATED_ROWS = [step for step in CALL_ROWS if step.unit is not steps.Unit.WAVE]


@pytest.mark.parametrize("step", SEATED_ROWS, ids=[step.id for step in SEATED_ROWS])
def test_every_single_and_wave_star_row_names_the_seat_it_dispatches(step: steps.Step) -> None:
    """The seat on a ``wave*`` row is its members'; a one-member collapse carries it on ``--agent``."""
    assert step.seat, "a SINGLE or wave* row names the seat --agent will carry"


@pytest.mark.parametrize(
    "step",
    [step for step in CALL_ROWS if step.unit is steps.Unit.WAVE],
    ids=[step.id for step in CALL_ROWS if step.unit is steps.Unit.WAVE],
)
def test_a_true_wave_row_names_no_seat(step: steps.Step) -> None:
    """A WAVE session is seatless, so its members' seats travel in the member table.

    A named seat would be a re-created coordinator whose domain knowledge had to
    be maintained in a generated definition *and* in the templates.
    """
    assert step.seat is None
    assert step.writer is steps.Writer.WAVE_SESSION


@pytest.mark.parametrize("step", steps.STEPS, ids=steps.STEP_IDS)
def test_only_a_call_row_declares_parses(step: steps.Step) -> None:
    if step.parses:
        assert step.unit in CALL_UNITS
        assert step.template


def test_the_envelope_is_asked_of_every_wave_row() -> None:
    envelope_rows = {step.id for step in steps.STEPS if steps.Parse.ENVELOPE in step.parses}
    verdict_rows = {step.id for step in steps.STEPS if steps.Parse.VERDICT in step.parses}
    scope_rows = {step.id for step in steps.STEPS if steps.Parse.SCOPE in step.parses}

    # A wave's envelope is its only structured return, and every current
    # envelope-parsing row is a wave: the write-to-disk SINGLEs that once
    # answered for a document they wrote (`p1.design`'s shape) are gone with
    # the stages that dispatched them.
    waves = {step.id for step in steps.STEPS if step.unit in (steps.Unit.WAVE, steps.Unit.WAVE_STAR)}
    assert envelope_rows == waves
    # No current row is both a worker-writer and a document-parser: the
    # mechanism (`call.DOCUMENT_PARSES`) stays generic infrastructure with no
    # producer today, rather than a property this table happens to need.
    assert {
        step.id
        for step in steps.STEPS
        if step.writer is steps.Writer.WORKER and call.DOCUMENT_PARSES & set(step.parses)
    } == set()
    # The one review row, and its verdict rides the *returned text*: a SINGLE
    # never-writer whose return `call.py` persists and parses.
    assert verdict_rows == {"p5.review"}
    # No current row parses SCOPE: the document that once carried it (the
    # taxonomy design) is produced upstream of this driver now, if at all.
    assert scope_rows == set()


def test_no_row_writes_register_entries_without_the_id_inventory_it_would_need() -> None:
    """The two facts a minting row would carry, held equal so neither is set alone.

    ``writes_register`` is what the self-grading guard rides, and the
    existing-id inventory is what would keep a re-entry from minting a second
    id for material already covered. No current row mints anything the build
    itself grades, so both are empty — a row declaring one without the other
    is either a mint nothing guards or a guard over a row that mints nothing.
    """
    writing = {step.id for step in steps.STEPS if step.writes_register}
    inventoried = {step.id for step in steps.STEPS if "existing_id_inventory" in step.slots}

    assert writing == inventoried == set()


def test_declared_outputs_are_scratch_relative_layout_patterns() -> None:
    for step in steps.STEPS:
        for output in step.outputs:
            assert not output.startswith("/")
            assert not output.startswith(steps.SCRATCH_ROOT), "outputs are relative to the scratch root"


# ---------------------------------------------------------------------------
# Barriers and caps
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("step", steps.STEPS, ids=steps.STEP_IDS)
def test_every_barrier_a_row_raises_names_a_stage_and_a_kind(step: steps.Step) -> None:
    for pair in step.barrier_pairs:
        stage, dot, kind = pair.rpartition(".")
        assert dot and kind
        assert stage in kb_pipeline.STAGE_IDS, f"{pair} names no stage in the vocabulary"


def test_the_tables_barrier_pairs_are_each_raised_once() -> None:
    raised = [pair for step in steps.STEPS for pair in step.barrier_pairs]

    assert len(raised) == len(set(raised))
    assert set(raised) == {
        "start.build-mode",
        "start.proceed",
        "spine-seed.runner-choice",
        "phase-5.cap-exhausted",
    }


@pytest.mark.parametrize(
    ("series", "round_number", "expected"),
    [
        (steps.SERIES_INITIAL, 1, "review/phase-3a-r1-gate.md"),
        (steps.SERIES_GATE, 2, "review/phase-3a-g2-gate.md"),
    ],
)
def test_the_findings_path_carries_its_series_letter(series: str, round_number: int, expected: str) -> None:
    """The letter rides the filename, so both counters survive resume.

    Both letters, even though no current row's own series carries ``g``: the
    function is a pure format over whatever letter it is given, and the two
    counters staying distinct is a property of it rather than of today's table.
    """
    assert steps.findings(stage="phase-3a", series=series, round_number=round_number, author="gate") == expected


# ---------------------------------------------------------------------------
# Single-sourced slot values and the lint vocabulary
# ---------------------------------------------------------------------------


def _declared_options(op: str) -> set[str]:
    """The long options ``kb_util.build_parser`` declares on one subcommand.

    Read off the built parser rather than off the module's constants: what a
    slot has to agree with is the surface a consumer's command line meets.
    """
    subparsers = next(
        action for action in kb_util.build_parser()._actions if isinstance(action, argparse._SubParsersAction)
    )
    return {
        flag
        for action in subparsers.choices[op]._actions
        for flag in action.option_strings
        if flag.startswith("--") and flag != "--help"
    }


def test_the_deviation_kinds_slot_carries_every_kind_the_parser_accepts() -> None:
    slot = steps.CONSTANT_SLOTS["deviation-kinds"]

    assert all(kind in slot for kind in envelope.DEVIATION_KINDS)


def test_the_layout_slot_carries_the_paths_the_rows_declare() -> None:
    slot = steps.CONSTANT_SLOTS["layout-paths"]
    declared = {output for step in steps.STEPS for output in step.outputs}

    assert declared
    assert all(f"{steps.SCRATCH_ROOT}/{output}" in slot for output in declared)


def test_every_path_slot_is_a_slot_some_shipped_template_declares() -> None:
    """A misspelled entry checks nothing, and reads exactly like one that does.

    Against the shipped templates rather than against the rows, because the
    vocabulary deliberately reaches the slots of a template no row dispatches
    yet — which is what stops a first caller of one from having to know.
    """
    declared = {
        slot.removeprefix(prompt_templates.DYNAMIC_PREFIX)
        for path in prompt_templates.template_paths()
        for slot in prompt_templates.slots_of(path.read_text(encoding="utf-8"), source=path.name)
        if slot.startswith(prompt_templates.DYNAMIC_PREFIX)
    }

    assert steps.PATH_SLOTS <= declared


def test_the_scope_slot_comes_from_the_parsers_own_grammar() -> None:
    assert steps.CONSTANT_SLOTS["scope-line-contract"] is envelope.SCOPE_LINE_CONTRACT


def test_every_write_op_has_an_invocation_slot_built_from_the_cli_constants() -> None:
    """A brief needing a write-op invocation fills a slot, never types a command.

    Both halves are ``kb_util``'s — the prefix it publishes and the op token
    ``build_parser`` names its own subparser from — so a brief cannot advertise
    an op the CLI does not have, nor spell the invocation a way the consumer
    does not run.
    """
    assert set(steps.WRITE_OP_SLOTS) <= set(steps.CONSTANT_SLOTS)
    assert len(steps.WRITE_OP_SLOTS) == len(kb_util.WRITE_OPS)

    for op in kb_util.WRITE_OPS:
        # The op token *is* the slot name: `@!insert-claim-entry!@` expands to an
        # invocation ending in the same string it is spelled with.
        slot = steps.CONSTANT_SLOTS[op]

        assert slot == f"{kb_util.INVOCATION} {op}"
        # The op slot stops at the op: the flag that follows it is
        # `@!values-flag!@`'s (the test below), and the values file's own path is
        # the brief's — so no op slot carries either.
        assert "--" not in slot.removeprefix(kb_util.INVOCATION)


def test_the_values_flag_slot_is_the_option_the_parser_actually_declares() -> None:
    """The last token of the sanctioned invocation, pinned to argparse.

    ``--values`` used to be typed by hand at every card, brief and doc that
    named a write op, so renaming the flag would have gone red at none of them
    and left every one of them advertising a command line the CLI rejects. The
    flag is a published constant now, and this is the pin that gives that
    constant its meaning: the string the briefs render is the string
    ``build_parser`` declares, for every op that takes it.
    """
    flag = steps.CONSTANT_SLOTS["values-flag"]

    assert flag == kb_util.VALUES_FLAG
    for op in kb_util.WRITE_OPS:
        assert flag in _declared_options(op), op


def test_no_write_op_slot_name_collides_with_a_slot_a_row_supplies() -> None:
    """``prompt_templates.render`` refuses a per-call slot shadowing a constant; none may exist.

    The nine landed on a namespace the step table was already using, so this is
    the check that the landing was collision-free rather than the assumption
    that it was.
    """
    supplied = {slot for step in steps.STEPS for slot in step.slots}

    assert supplied.isdisjoint(steps.WRITE_OP_SLOTS)


def test_the_lint_vocabulary_covers_every_stage_id_and_both_ledger_write_verbs() -> None:
    """Both write verbs are banned by name, which is the widening the ops brought.

    While the retired op flag was the second token a brief could say
    ``start-build`` freely and only that flag spelling was caught. Both verbs
    are subcommands now, and a brief may name neither.
    """
    labels = set(steps.TEMPLATE_PROHIBITIONS)

    for stage_id in kb_pipeline.STAGE_IDS:
        assert stage_id in labels or f"--stage {stage_id}" in labels
    assert {kb_util.OP_ADVANCE_STEP, kb_util.OP_START_BUILD} <= labels


@pytest.mark.parametrize("stage_id", [s for s in kb_pipeline.STAGE_IDS if s not in steps.AMBIGUOUS_STAGE_IDS])
def test_each_unambiguous_stage_id_is_flagged_under_its_own_name(stage_id: str) -> None:
    """``phase-1`` must not answer for ``phase-1a``: each id is reported as itself."""
    matched = {label for label, pattern in steps.TEMPLATE_PROHIBITIONS.items() if pattern.search(f"record {stage_id}")}

    assert matched == {stage_id}


# ---------------------------------------------------------------------------
# Spending no inference
# ---------------------------------------------------------------------------


def test_a_row_spends_inference_by_either_route_and_the_two_stay_distinguishable() -> None:
    """The union is what a no-inference run drops; the halves are not interchangeable.

    ``--dry-run`` replaces only the calls this driver dispatches, so the seat
    half and the ``spends_own_inference`` half have to stay tellable apart —
    which is why the derived property sits beside both rather than replacing
    either.
    """
    dispatched = {step.id for step in steps.STEPS if step.seat is not None}
    inside_a_tool = {step.id for step in steps.STEPS if step.spends_own_inference}
    spending = {step.id for step in steps.STEPS if step.spends_inference}

    assert dispatched and inside_a_tool
    assert dispatched.isdisjoint(inside_a_tool)
    assert spending == dispatched | inside_a_tool


def test_no_ledger_row_spends_inference() -> None:
    """What lets the walk continue past a stage whose work was dropped.

    A stage nothing recorded is a stage no later stage can be recorded after,
    so a build spending no inference finishes only if recording never costs
    one. Asserted rather than assumed: a record row that grew a seat would make
    the whole mode unreachable, and quietly.
    """
    assert [step.id for step in steps.STEPS if step.ledger_op is not None and step.spends_inference] == []


@pytest.mark.parametrize("build_mode", ["fresh", "revision"])
def test_a_run_spending_no_inference_drops_exactly_the_rows_that_cost_one(build_mode: str) -> None:
    """``applies`` is row-level, and the two conditions compose rather than override."""
    dropped = {
        step.id
        for step in steps.STEPS
        if steps.applies(step, build_mode=build_mode)
        and not steps.applies(step, build_mode=build_mode, spend_inference=False)
    }

    assert dropped == {
        step.id for step in steps.STEPS if step.spends_inference and steps.applies(step, build_mode=build_mode)
    }


def test_the_rows_a_stage_loses_are_named_by_the_table_and_not_by_a_stage_id() -> None:
    """``inference_rows`` is the note's source, and it is a per-run answer.

    A revision build's head rows do not apply at all, so it loses none of them
    — the same ``applies`` deciding both, which is what keeps the note from
    claiming a row did not run when it was never going to.
    """
    fresh = {stage: steps.inference_rows(stage, build_mode="fresh") for stage in steps.TABLE_STAGE_IDS}
    revision = {stage: steps.inference_rows(stage, build_mode="revision") for stage in steps.TABLE_STAGE_IDS}

    # `depends-attributed` is deliberately absent: its row spends no inference
    # of its own, because the narrowing settles every edge containment decides
    # whether or not a model is reachable and only the pairs left open need
    # one. The tool is told with its own `--no-inference` and reports what went
    # unasked; no row is dropped, so the stage loses nothing to name.
    assert {stage for stage, rows in fresh.items() if rows} == {
        "claims-discovered",
        "phase-5",
    }
    assert {stage for stage, rows in revision.items() if rows} == {"phase-5"}
    for stage, rows in fresh.items():
        assert set(rows) <= {step.id for step in steps.steps_for(stage)}, stage


# ---------------------------------------------------------------------------
# The stage table and the step table, held equal
#
# `stages_without_own_inference` used to guarantee, at runtime, that an
# inference-spending row inserted anywhere could not escape the bound. The
# bound is gone; the failure it guarded against is not. What replaces it is a
# single declaration both consumers read (`kb_pipeline.Stage.work_is_inference`)
# and the derivation performed HERE, over `steps.STEPS`, compared in both
# directions. A test that only asked whether the declaration is self-consistent
# would pass on a table that had quietly stopped describing the rows.
# ---------------------------------------------------------------------------


def test_the_stage_declaration_and_the_rows_agree_about_which_stages_spend_inference() -> None:
    """Derived here, declared there, compared both ways.

    A row gaining a seat or ``spends_own_inference`` in a stage the table calls
    mechanical fails this; so does a stage declaring itself inferential with no
    row that costs a call. Neither can reach a shipped run, which is the
    guarantee the deleted runtime derivation used to carry.
    """
    derived = {
        stage for stage in steps.TABLE_STAGE_IDS if any(step.spends_inference for step in steps.steps_for(stage))
    }
    declared = {stage.id for stage in kb_pipeline.STAGES if stage.work_is_inference}

    assert derived == declared, f"only in the rows: {derived - declared}; only in the table: {declared - derived}"


def test_the_declaration_is_the_one_runtime_source_and_nothing_re_derives_it() -> None:
    """Both consumers read the declaration; neither computes a second answer.

    The trap this closes is the driver deriving at runtime while the tool reads
    a declaration — they would then disagree only in a shipped run, where no
    test is watching. ``_excused`` is the sole reader of the stage-level fact,
    and it reads the field.
    """
    source = inspect.getsource(kb_pipeline._excused)

    assert "stage.work_is_inference" in source
    assert "spends_inference" not in source and "steps" not in source


@pytest.mark.parametrize("stage", [stage for stage in kb_pipeline.STAGES if stage.claimgraph_invocation])
def test_each_claim_graph_stage_composes_and_resolves_to_itself(stage: kb_pipeline.Stage) -> None:
    """The invocation table, read in both directions.

    The driver composes flags from the stage; the tool parses those flags and
    asks what stage they are. A round trip that did not land back on the same
    stage would mean a build running a pass as one stage and recording it as
    another.
    """
    assert stage.claimgraph_invocation is not None  # the parametrization's own filter
    invocation = stage.claimgraph_invocation

    assert kb_pipeline.claimgraph_stage(which_pass=invocation.which_pass, scope=invocation.scope) is stage
    assert invocation.flags[:2] == ("--pass", str(invocation.which_pass))


def test_every_claim_graph_row_belongs_to_a_stage_that_declares_an_invocation() -> None:
    """The driver has no pass number of its own left to get wrong."""
    invoking = {stage.id for stage in kb_pipeline.STAGES if stage.claimgraph_invocation}
    rows = {steps.STEPS_BY_ID[step_id].stage for step_id in ("declared.build", "discover.build", "depends.attribute")}

    assert rows == invoking


def test_each_stages_precondition_is_the_stage_before_it() -> None:
    """One statement of the order, read by the driver's table and by the tool alike."""
    assert kb_pipeline.precondition_of(kb_pipeline.STAGES[0]) is None
    for earlier, later in zip(kb_pipeline.STAGES, kb_pipeline.STAGES[1:]):
        assert kb_pipeline.precondition_of(later) is earlier
