"""Prompt composition, persistence, and the template lint.

The fixtures below stand in for the shipped bodies, so the machinery is tested
against templates whose defects are deliberate; the lint also runs over the
shipped ``prompt-templates/`` directory, which is the same guard aimed at the
prose a run actually dispatches.

Composition is strict in both directions on purpose: a template and the step
table that fills it are written by different hands, and every disagreement
between them is a driver defect (exit 15), never a pipeline outcome.

A slot is ``@!slot!@``, so a brace is only a brace: the fixtures carry raw JSON
and assert it survives composition, which under the ``str.format`` mechanism
this replaced was a render-time failure keyed on the body's own content.

The namespace is what routes a slot to its source, so the fixtures spell both
halves: ``@!dyn.charter!@`` for what the caller computes, and a bare name for
everything the composer fills. Each direction's refusal is checked for what it
*says* and not only that it fired — naming the miswiring is the whole of what
routing buys a caller over a flat namespace.
"""

from pathlib import Path

import pytest

from kb_tools.kb_driver import prompt_templates, runlog, steps

# --- the fixture template set -----------------------------------------------
#
# Deliberately says nothing about sequencing: these are what a clean template
# set looks like, and the lint test below asserts exactly that.

FRAGMENT_FIXTURES = {
    "dispatch-discipline.tmpl": (
        "Dispatch every member call with run_in_background: false. Hold the turn\n" "until all members return.\n"
    ),
    "envelope-contract.tmpl": (
        "Close with one envelope block:\n\n"
        "<<<KB-DRIVER-ENVELOPE\n"
        '{"step": "...", "members": [], "gaps": [], "deviations": []}\n'
        "KB-DRIVER-ENVELOPE\n"
    ),
    "deviation-contract.tmpl": "A deviation is one of: @!deviation-kinds!@. Reporting one is not a fault.\n",
    "verdict-contract.tmpl": "End with: VERDICT: critical=<n> warning=<n> note=<n>\n",
    "return-contract.tmpl": "Return the artifact as your final message body and nothing else.\n",
    "scratch-layout.tmpl": "Scratch paths:\n\n@!layout-paths!@\n",
    "persist-members.tmpl": "Write each member's return to the path named for it, verbatim.\n",
    # The caller-selected alternatives, stood in for under their registered
    # names: which file an alternative slot resolves to is the registry's, so a
    # fixture that renamed them would be exercising a route nothing takes.
    "ask-correction.tmpl": "\n\n## A previous answer failed\n\n@!dyn.report!@",
    "identify-display-maths.tmpl": "These labels are maths:\n\n- @!dyn.fenced-labels!@",
    "identify-no-display-maths.tmpl": "The document carries no display-maths block.",
}

WAVE_FIXTURE = "fixture-survey.wave.tmpl"
SINGLE_FIXTURE = "fixture-design.single.tmpl"
ALTERNATIVE_FIXTURE = "fixture-ask.single.tmpl"

STEP_FIXTURES = {
    ALTERNATIVE_FIXTURE: "Read @!dyn.document!@.\n\n@!display-maths!@\n\nAnswer.@!correction!@",
    WAVE_FIXTURE: (
        "Survey each source named below.\n\n"
        "Charter: @!dyn.charter!@\n\nMembers:\n@!dyn.members!@\n\n"
        "@!dispatch-discipline!@\n@!envelope-contract!@\n@!deviation-contract!@\n@!scratch-layout!@\n"
    ),
    SINGLE_FIXTURE: (
        "Design the taxonomy from the charter at @!dyn.charter!@.\n\n"
        "@!scope-line-contract!@\n\n@!pending-rule!@\n\n@!return-contract!@\n"
    ),
}


def _templates(
    tmp_path: Path, extra: dict[str, str] | None = None, fragments_extra: dict[str, str] | None = None
) -> Path:
    """A template set on the shipped layout: dispatchable bodies on top, fragments beneath.

    ``fragments_extra`` replaces a fragment body under its own registered name,
    which is the only way to put a defect inside one: which file a slot resolves
    to is the registry's, never a fixture's.
    """
    directory = tmp_path / "prompt-templates"
    fragments = directory / prompt_templates.FRAGMENTS_DIRNAME
    fragments.mkdir(parents=True)
    for name, body in (FRAGMENT_FIXTURES | (fragments_extra or {})).items():
        (fragments / name).write_text(body, encoding="utf-8")
    for name, body in (STEP_FIXTURES | (extra or {})).items():
        (directory / name).write_text(body, encoding="utf-8")
    return directory


# ---------------------------------------------------------------------------
# Composition
# ---------------------------------------------------------------------------


def test_render_fills_step_slots_fragments_and_constants(tmp_path: Path) -> None:
    directory = _templates(tmp_path)

    brief = prompt_templates.render(
        WAVE_FIXTURE,
        slots={"charter": "scratch/build-charter.md", "members": "- AcmeWidgets.tex"},
        constants=steps.CONSTANT_SLOTS,
        wave=True,
        directory=directory,
    )

    assert "scratch/build-charter.md" in brief
    assert "- AcmeWidgets.tex" in brief
    # The fragment arrived, and its own slot was filled from the driver's
    # single definition rather than restated in the template.
    assert "run_in_background: false" in brief
    assert "adaptive-retry" in brief
    assert steps.SCRATCH_ROOT in brief
    assert "@!dyn.charter!@" not in brief


def test_a_fragment_example_survives_as_a_literal(tmp_path: Path) -> None:
    directory = _templates(tmp_path)

    brief = prompt_templates.render(
        WAVE_FIXTURE,
        slots={"charter": "c.md", "members": "- one"},
        constants=steps.CONSTANT_SLOTS,
        wave=True,
        directory=directory,
    )

    assert '{"step": "...", "members": [], "gaps": [], "deviations": []}' in brief


def test_constants_are_drawn_on_only_where_a_slot_names_them(tmp_path: Path) -> None:
    directory = _templates(tmp_path)

    brief = prompt_templates.render(
        SINGLE_FIXTURE,
        slots={"charter": "c.md"},
        constants=steps.CONSTANT_SLOTS,
        directory=directory,
    )

    assert prompt_templates.RESERVED_WAVE_SLOTS[0] not in brief
    assert "SCOPE: within-charter" in brief


def test_a_wave_template_missing_a_reserved_slot_fails_composition(tmp_path: Path) -> None:
    body = "Survey @!dyn.members!@.\n@!dispatch-discipline!@\n@!envelope-contract!@\n"
    directory = _templates(tmp_path, extra={"gap.wave.tmpl": body})

    with pytest.raises(runlog.BoundaryError, match="reserved slot"):
        prompt_templates.render("gap.wave.tmpl", slots={"members": "- one"}, wave=True, directory=directory)


def test_an_unfilled_composer_slot_fails_composition(tmp_path: Path) -> None:
    """A bare slot no pool answers for. The caller is not asked about it and cannot fix it."""
    directory = _templates(tmp_path)

    with pytest.raises(runlog.BoundaryError, match="unfilled slot\\(s\\): pending-rule, scope-line-contract"):
        prompt_templates.render(SINGLE_FIXTURE, slots={"charter": "c.md"}, directory=directory)


def test_an_unsupplied_dynamic_slot_names_the_caller_s_omission(tmp_path: Path) -> None:
    """The failure a miswired caller gets, saying which of the two mistakes it made."""
    directory = _templates(tmp_path)

    with pytest.raises(runlog.BoundaryError, match=r"supplied no value for @!dyn\.…!@ slot\(s\): charter"):
        prompt_templates.render(SINGLE_FIXTURE, slots={}, constants=steps.CONSTANT_SLOTS, directory=directory)


def test_a_caller_supplying_a_composer_slot_is_told_which_half_it_belongs_to(tmp_path: Path) -> None:
    """The other half of the miswiring, and the one routing exists to catch.

    Under a single flat namespace this arrived as an unfilled slot and an unused
    value in two separate messages, and the reader inferred the rest.
    """
    directory = _templates(tmp_path)

    with pytest.raises(runlog.BoundaryError, match="the composer fills: scope-line-contract"):
        prompt_templates.render(
            SINGLE_FIXTURE,
            slots={"charter": "c.md", "scope-line-contract": "whatever I like"},
            constants=steps.CONSTANT_SLOTS,
            directory=directory,
        )


def test_a_caller_handing_prose_to_an_alternative_slot_reads_as_the_same_mistake(tmp_path: Path) -> None:
    """The shape the claim-graph asks had before routing: the chosen body's prose, in a slot dict."""
    directory = _templates(tmp_path)

    with pytest.raises(runlog.BoundaryError, match="the composer fills: correction"):
        prompt_templates.render(
            ALTERNATIVE_FIXTURE,
            slots={"document": "vol/one.md", "correction": "\n\n## A previous answer failed\n\nno such label"},
            alternatives={"display-maths": "identify-no-display-maths", "correction": None},
            directory=directory,
        )


def test_an_unused_supplied_slot_fails_composition(tmp_path: Path) -> None:
    directory = _templates(tmp_path)

    with pytest.raises(runlog.BoundaryError, match="never uses"):
        prompt_templates.render(
            SINGLE_FIXTURE,
            slots={"charter": "c.md", "volume-list": "- one"},
            constants=steps.CONSTANT_SLOTS,
            directory=directory,
        )


# --- one expansion level, in every direction ---------------------------------
#
# The composer expands twice: the template, then each body it splices in. A
# spliced body naming another expandable slot would need a third pass, and the
# refusal has to cover the union of both vocabularies in both directions —
# nothing shipped nests today, which is exactly why an untested guard here would
# stop holding the first time someone added a slot to a fragment.


@pytest.mark.parametrize("inner", ["dispatch-discipline", "correction"])
def test_a_fragment_whose_body_names_an_expandable_slot_is_refused(tmp_path: Path, inner: str) -> None:
    directory = _templates(tmp_path, fragments_extra={"scratch-layout.tmpl": f"Scratch paths:\n\n@!{inner}!@\n"})

    with pytest.raises(runlog.BoundaryError, match=rf"@!scratch-layout!@ resolves to .*whose body names @!{inner}!@"):
        prompt_templates.render(
            WAVE_FIXTURE,
            slots={"charter": "c.md", "members": "- one"},
            constants=steps.CONSTANT_SLOTS,
            wave=True,
            directory=directory,
        )


@pytest.mark.parametrize("inner", ["persist-members", "display-maths"])
def test_an_alternative_whose_body_names_an_expandable_slot_is_refused(tmp_path: Path, inner: str) -> None:
    directory = _templates(tmp_path, fragments_extra={"ask-correction.tmpl": f"\n\nCorrection:\n\n@!{inner}!@"})

    with pytest.raises(runlog.BoundaryError, match=rf"@!correction!@ resolves to .*whose body names @!{inner}!@"):
        prompt_templates.render(
            ALTERNATIVE_FIXTURE,
            slots={"document": "vol/one.md"},
            alternatives={"display-maths": "identify-no-display-maths", "correction": "ask-correction"},
            directory=directory,
        )


# --- the caller-selected alternatives ---------------------------------------


def test_a_caller_names_an_alternative_and_the_composer_resolves_it(tmp_path: Path) -> None:
    """The choice travels, never the prose: the caller names one and supplies its slot's value."""
    directory = _templates(tmp_path)

    brief = prompt_templates.render(
        ALTERNATIVE_FIXTURE,
        slots={"document": "vol/one.md", "fenced-labels": "S6, S7"},
        alternatives={"display-maths": "identify-display-maths", "correction": None},
        directory=directory,
    )

    assert brief == "Read vol/one.md.\n\nThese labels are maths:\n\n- S6, S7\n\nAnswer."


def test_the_choice_that_is_no_choice_fills_the_slot_with_nothing(tmp_path: Path) -> None:
    """``None`` is a registered answer where the slot's absence is itself one."""
    directory = _templates(tmp_path)

    chosen, absent = (
        prompt_templates.render(
            ALTERNATIVE_FIXTURE,
            slots={"document": "vol/one.md", **extra},
            alternatives={"display-maths": "identify-no-display-maths", "correction": correction},
            directory=directory,
        )
        for correction, extra in (("ask-correction", {"report": "the locator names no label"}), (None, {}))
    )

    assert chosen == absent + "\n\n## A previous answer failed\n\nthe locator names no label"


@pytest.mark.parametrize(
    ("alternatives", "complaint"),
    [
        ({"display-maths": "identify-display-maths"}, "no alternative chosen for slot"),
        (
            {"display-maths": "identify-display-maths", "correction": None, "verdict": "ask-correction"},
            "does not declare",
        ),
        ({"display-maths": "ask-correction", "correction": None}, "takes one of"),
        ({"display-maths": "identify-no-display-maths", "correction": "no-such-fragment"}, "takes one of"),
    ],
)
def test_a_choice_the_slot_does_not_register_is_refused(
    tmp_path: Path, alternatives: dict[str, str | None], complaint: str
) -> None:
    """Strict in both directions, like every other part of composition.

    An unanswered choice would fill a section of a dispatched prompt with
    nothing and say so nowhere, which is the one failure a composed prompt
    cannot report on its own.
    """
    directory = _templates(tmp_path)

    with pytest.raises(runlog.BoundaryError, match=complaint):
        prompt_templates.render(
            ALTERNATIVE_FIXTURE, slots={"document": "vol/one.md"}, alternatives=alternatives, directory=directory
        )


def test_a_named_but_absent_template_fails_composition(tmp_path: Path) -> None:
    directory = _templates(tmp_path)

    with pytest.raises(runlog.BoundaryError, match="not found"):
        prompt_templates.render("no-such.single.tmpl", slots={}, directory=directory)


def test_a_body_that_is_all_punctuation_composes_untouched(tmp_path: Path) -> None:
    """Nothing but a marker is syntax, so nothing but a marker needs escaping.

    The three shapes that used to raise at render time, keyed on the body's own
    content rather than on anything the author was writing as syntax: a JSON
    example, a shell expansion, and a LaTeX macro taking braced arguments.
    """
    body = 'Return {"step": "x"}, run ${VAR}, and typeset \\frac{a}{b}.\n'
    directory = _templates(tmp_path, extra={"raw.single.tmpl": body})

    assert prompt_templates.render("raw.single.tmpl", slots={}, directory=directory) == body


def test_an_unclosed_marker_is_refused_by_line(tmp_path: Path) -> None:
    """The property a polar delimiter buys: an opener with no closer names its own line."""
    directory = _templates(tmp_path, extra={"stray.single.tmpl": "Fill @!charter and stop.\n"})

    with pytest.raises(runlog.BoundaryError, match="stray slot delimiter on line\\(s\\) 1"):
        prompt_templates.render("stray.single.tmpl", slots={}, directory=directory)


@pytest.mark.parametrize("body", ["Fill @!Charter Path!@.\n", "Fill @!charter_path!@.\n", "Fill @!2nd-charter!@.\n"])
def test_a_marker_whose_name_is_not_a_slot_name_is_refused(tmp_path: Path, body: str) -> None:
    """A well-formed-looking pair the grammar does not admit is a defect, not a literal.

    The name class is kebab — the generator's own (``SLOT_NAME``) — so an
    underscore and a leading digit are as much a defect as a space is.
    """
    directory = _templates(tmp_path, extra={"odd.single.tmpl": body})

    with pytest.raises(runlog.BoundaryError, match="stray slot delimiter"):
        prompt_templates.render("odd.single.tmpl", slots={}, directory=directory)


# ---------------------------------------------------------------------------
# Persistence
# ---------------------------------------------------------------------------


def test_the_composed_brief_is_on_disk_before_anything_is_spawned(tmp_path: Path) -> None:
    briefs_dir = tmp_path / "briefs"
    briefs_dir.mkdir()

    path = prompt_templates.persist(briefs_dir, seq=7, step_id="p1a.review", text="brief body — ünicode")

    assert path == briefs_dir / "007-p1a.review.md"
    assert path.read_text(encoding="utf-8") == "brief body — ünicode\n"


def test_persisting_into_a_missing_directory_is_a_boundary_failure(tmp_path: Path) -> None:
    with pytest.raises(runlog.BoundaryError, match="brief directory"):
        prompt_templates.persist(tmp_path / "absent", seq=1, step_id="p0.survey", text="body")


# ---------------------------------------------------------------------------
# The template lint
# ---------------------------------------------------------------------------


def test_the_fixture_set_lints_clean(tmp_path: Path) -> None:
    directory = _templates(tmp_path)

    assert (
        prompt_templates.lint(prompt_templates.template_paths(directory), prohibited=steps.TEMPLATE_PROHIBITIONS) == []
    )


def test_the_shipped_template_directory_lints_clean() -> None:
    """The same guard aimed at ``prompt-templates/`` — the bodies a run dispatches."""
    assert prompt_templates.lint(prompt_templates.template_paths(), prohibited=steps.TEMPLATE_PROHIBITIONS) == []


def test_every_shipped_fragment_is_registered_exactly_once_and_every_registration_ships() -> None:
    """``fragments/`` and the two registries are one set, checked in both directions.

    A fragment's filename is derived from its name, so a registration and its
    file cannot disagree about spelling — but a fragment listed and not shipped,
    or shipped and listed nowhere, still can, and most fragments are named by no
    template today, so nothing else would meet the discrepancy. *Exactly once* is
    the half that keeps the two kinds apart: a composer-resolved slot and a
    caller-selected alternative reach a template by different routes, and a file
    claimed by both is a file whose route nobody can name.
    """
    directory = prompt_templates.PROMPT_TEMPLATES_DIR / prompt_templates.FRAGMENTS_DIRNAME
    shipped = {
        f"{prompt_templates.FRAGMENTS_DIRNAME}/{path.name}" for path in prompt_templates.template_paths(directory)
    }
    resolved = set(prompt_templates.FRAGMENTS.values())
    selected = set(prompt_templates.ALTERNATIVES.values())

    assert resolved | selected == shipped
    assert resolved & selected == set()


def test_no_dispatchable_template_sits_in_the_fragments_directory_or_the_other_way_round() -> None:
    """The layout is the declaration, so the step table's own names have to honour it."""
    named = {step.template for step in steps.STEPS if step.template}
    fragments = set(prompt_templates.FRAGMENTS.values()) | set(prompt_templates.ALTERNATIVES.values())

    assert named & fragments == set()
    assert all("/" not in name for name in named), "a row dispatches a template from the top level"


# --- the agent-side write-op contract ---------------------------------------


def test_the_write_op_contract_fragment_states_the_exit_eight_rule() -> None:
    """Teeth for the guard below: the one body it looks for has to say the thing.

    Exit 8 is the outcome a *correct* set of values gets when another writer
    moved the file, and the whole point of separating it from 7 is that the
    caller must re-run rather than re-author — re-asking a model for values that
    were already right is how a duplicate id gets written. A guard that only
    checked the fragment was present would pass over a fragment that had
    quietly lost the rule.
    """
    body = prompt_templates.load(prompt_templates.FRAGMENTS["write-op-contract"])

    assert "**8**" in body
    assert "Re-run the identical invocation" in body
    assert "Never re-author the values" in body


#: No shipped template names a write op today: the mint-bearing rows that used
#: to call one from inside a wave session are gone, and the fix and review
#: waves this table still has rows for are generic content work, never a
#: metadata write. ``test_the_write_op_contract_fragment_states_the_exit_eight_rule``
#: keeps the fragment itself honest; there is currently no composed brief to
#: hold to it.


@pytest.mark.parametrize(
    ("body", "named"),
    [
        ("Record phase-3a once the review is clean.\n", "phase-3a"),
        ("Then depends-attributed writes the edges.\n", "depends-attributed"),
        ("Run advance-step when you are done.\n", "advance-step"),
        ("Then start-build opens the ledger.\n", "start-build"),
        ("Record PHASE-5 before moving on.\n", "phase-5"),
        # The flag is `@!values-flag!@`'s to render. A template that
        # spells it has hand-written the third token of an invocation whose
        # first two it was given, and a rename would leave it stale. The
        # fixture is built from the constant for that same reason — a literal
        # here would be one more site a rename leaves behind.
        (f"Call the op with {steps.VALUES_FLAG_SLOT} <your file>.\n", steps.VALUES_FLAG_SLOT),
    ],
)
def test_a_template_naming_the_drivers_own_business_is_flagged(tmp_path: Path, body: str, named: str) -> None:
    directory = _templates(tmp_path, extra={"leak.single.tmpl": body})

    findings = prompt_templates.lint(prompt_templates.template_paths(directory), prohibited=steps.TEMPLATE_PROHIBITIONS)

    assert [finding for finding in findings if named in finding and finding.startswith("leak.single.tmpl:1:")]


def test_start_reads_as_prose_but_not_as_a_stage_argument(tmp_path: Path) -> None:
    prose = _templates(tmp_path / "a", extra={"ok.single.tmpl": "Start with the charter, then start the survey.\n"})
    naming = _templates(tmp_path / "b", extra={"bad.single.tmpl": "Then --stage start is recorded.\n"})

    assert prompt_templates.lint(prompt_templates.template_paths(prose), prohibited=steps.TEMPLATE_PROHIBITIONS) == []
    assert prompt_templates.lint(prompt_templates.template_paths(naming), prohibited=steps.TEMPLATE_PROHIBITIONS)


def test_a_driver_supplied_layout_path_is_not_read_as_prose(tmp_path: Path) -> None:
    """The layout paths a brief carries are filled from the driver's constants, not authored."""
    findings = prompt_templates.lint(
        prompt_templates.template_paths(_templates(tmp_path)),
        prohibited=steps.TEMPLATE_PROHIBITIONS,
    )

    assert findings == []
    assert steps.SCRATCH_ROOT in steps.CONSTANT_SLOTS["layout-paths"]


def test_the_lint_flags_a_wave_template_missing_its_reserved_slots(tmp_path: Path) -> None:
    directory = _templates(tmp_path, extra={"gap.wave.tmpl": "Survey @!dyn.members!@.\n@!dispatch-discipline!@\n"})

    findings = prompt_templates.lint(prompt_templates.template_paths(directory), prohibited=steps.TEMPLATE_PROHIBITIONS)

    flagged = {
        slot
        for slot in prompt_templates.RESERVED_WAVE_SLOTS
        for finding in findings
        if finding.startswith("gap.wave.tmpl") and slot in finding
    }
    assert flagged == {"envelope-contract", "deviation-contract"}


def test_the_lint_reports_a_stray_delimiter_rather_than_raising(tmp_path: Path) -> None:
    """A template defect the lint has to survive: it reports every file, not the first bad one."""
    directory = _templates(tmp_path, extra={"stray.wave.tmpl": "Survey @!members and stop.\n"})

    findings = prompt_templates.lint(prompt_templates.template_paths(directory), prohibited=steps.TEMPLATE_PROHIBITIONS)

    assert any("stray slot delimiter" in finding for finding in findings)


def test_template_paths_tolerates_an_absent_directory(tmp_path: Path) -> None:
    assert prompt_templates.template_paths(tmp_path / "absent") == ()


# ---------------------------------------------------------------------------
# The seat slot
# ---------------------------------------------------------------------------

#: A wave template naming ``@!seat!@`` directly. No shipped template names it —
#: the one-member-collapse fragment that did went with the last ``wave*`` row —
#: so both halves of the mechanism are exercised against a fixture: the seat a
#: row states reaches the brief, and a row with none is refused rather than
#: rendered.
SEAT_FIXTURES = {
    "seated.wave.tmpl": (
        "Members:\n@!dyn.members!@\n\nDispatch each member as `subagent_type: @!seat!@`.\n\n"
        "@!dispatch-discipline!@\n@!envelope-contract!@\n@!deviation-contract!@\n"
    ),
}


def test_the_seat_slot_is_filled_from_the_row(tmp_path: Path) -> None:
    directory = _templates(tmp_path, extra=SEAT_FIXTURES)

    brief = prompt_templates.render(
        "seated.wave.tmpl",
        slots={"members": "- one"},
        constants=steps.CONSTANT_SLOTS,
        row=prompt_templates.RowSlots(step_id="p5.docs", seat="tech-writer"),
        wave=True,
        directory=directory,
    )

    assert "`subagent_type: tech-writer`" in brief


def test_a_seatless_row_reaching_the_seat_slot_is_refused_naming_the_step(tmp_path: Path) -> None:
    """The refusal is the composer's, not the dispatched prompt's.

    A seatless row is legitimate — a WAVE fans out to the seats its member
    table names rather than through ``--agent`` — so the defect is not the row
    but its meeting this slot, and the message has to say which row it was.
    """
    directory = _templates(tmp_path, extra=SEAT_FIXTURES)

    with pytest.raises(runlog.BoundaryError, match="p5.review carries no seat"):
        prompt_templates.render(
            "seated.wave.tmpl",
            slots={"members": "- one"},
            constants=steps.CONSTANT_SLOTS,
            row=prompt_templates.RowSlots(step_id="p5.review", seat=None),
            wave=True,
            directory=directory,
        )


def test_no_shipped_template_spells_a_seat_the_step_table_holds() -> None:
    """A seat rename reaches the briefs through the row, or it does not reach them at all.

    The clause used to name its seat in eight places, so renaming a seat went red
    at none of them and left a brief dispatching an agent type that no longer
    exists. Nothing a template says may be a seat name now.
    """
    seats = {step.seat for step in steps.STEPS if step.seat}

    spelled = {
        (path.name, seat)
        for path in prompt_templates.template_paths()
        for seat in seats
        if seat in path.read_text(encoding="utf-8")
    }

    assert spelled == set()
