"""The run loop's own vocabulary: resume-skip, round arithmetic, and the driver-wide contracts.

End-to-end walks of the surviving stages (``phase-3a`` through ``phase-5`') —
barriers, the two capped series, exit selection, fix-wave partitioning, the
wave-session-persists route — are ``test_kb_driver_buildout.py``'s: that file
owns the only stages this table still has rows for, and duplicating its
scenario scaffold here would be a second answer to a question it already
has one for.

What is left here is the vocabulary those scenarios are built from and two
contracts that hold over the whole step table rather than over any one stage:
every row has a handler, and every row's slots compose the template it is
shipped with.
"""

from pathlib import Path

import pytest

from kb_tools import kb_pipeline
from kb_tools.kb_driver import barriers, call, config, prompt_templates, replay, run, runlog, steps


def _runner(tmp_path: Path) -> run.Runner:
    """A ``Runner`` over real collaborators and a repo it never walks.

    What this is for is the attribute registry, which is a property of the
    object rather than of any walk, so nothing here needs a ledger behind it.
    """
    root = tmp_path / "repo"
    root.mkdir()
    paths = runlog.prepare(tmp_path / "runs", "20260901T120000-1")
    cfg = config.load(None, run_overrides={"sources": ("AcmeWidgets.tex",)}, admissible=barriers.ADMISSIBLE)
    return run.Runner(
        config=cfg,
        paths=paths,
        repo_root=root,
        caller=call.Caller(invoker=replay.dry_run_invoker(), config=cfg, repo_root=root, paths=paths),
        answers=barriers.Resolver(config_decisions={}),
        ops=run.ledger_ops_for(root),
    )


def test_present_is_existence_and_non_emptiness_and_nothing_else(tmp_path: Path) -> None:
    """The only artifact question the driver asks. An empty file is not evidence of work."""
    full, empty, absent = tmp_path / "a.md", tmp_path / "b.md", tmp_path / "c.md"
    full.write_text("x\n", encoding="utf-8")
    empty.touch()

    assert run.present(full)
    assert not run.present(empty)
    assert not run.present(absent)


def test_pending_members_drops_only_the_members_whose_artifacts_landed(tmp_path: Path) -> None:
    """Partial-wave reconciliation, at member granularity."""
    done = run.Member(name="a", source="A.tex", artifact=tmp_path / "a.md")
    missing = run.Member(name="b", source="B.tex", artifact=tmp_path / "b.md")
    done.artifact.write_text("survey\n", encoding="utf-8")

    assert run.pending_members((done, missing)) == (missing,)


@pytest.mark.parametrize(
    ("series", "round_number", "expected"),
    [("r", 1, 0), ("r", 3, 2), ("g", 1, 1), ("g", 2, 2)],
)
def test_revisions_spent_differs_only_by_the_series_opening_move(series: str, round_number: int, expected: int) -> None:
    assert run.revisions_spent(series=series, round_number=round_number) == expected


# ---------------------------------------------------------------------------
# What a Runner may hold
# ---------------------------------------------------------------------------


def test_a_runner_holds_exactly_the_attributes_the_registry_declares(tmp_path: Path) -> None:
    """Closure in both directions, so the registry cannot describe a different object.

    An attribute missing from the registry is the defect the guard exists for; a
    registry entry nothing binds is a name a reader would go looking for the
    justification of and find none.
    """
    assert set(vars(_runner(tmp_path))) == run.RUNNER_ATTRIBUTES


def test_an_attribute_the_registry_does_not_declare_stops_the_next_stage(tmp_path: Path) -> None:
    """The guard against a new unguarded attribute, at the granularity of its criterion.

    ``_runner_file`` is the one this row removed, re-introduced here as exactly
    the shape the criterion forbids: written by a ``start`` row and read by a
    ``spine-seed`` row, so a resume — which skips ``start`` whole — would read
    its constructor default. It is caught at a stage transition, because a stage
    boundary is where a resume re-enters and therefore what the criterion is
    about.
    """
    runner = _runner(tmp_path)
    runner._runner_file = True

    with pytest.raises(runlog.BoundaryError) as raised:
        runner._run_stage(kb_pipeline.FIRST_STAGE_ID)

    assert "_runner_file" in str(raised.value), "exit 15's card must name the attribute to fix"


def test_the_constructor_runs_the_same_guard(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The guard's second wiring point: a binding added to ``__init__`` never reaches a walk.

    Narrowing the registry is how that is exercised without editing the class.
    The guard's question is the difference between the two sets, and which side
    of it moved does not change the answer.
    """
    monkeypatch.setattr(run, "RUNNER_ATTRIBUTES", run.RUNNER_ATTRIBUTES - {"_rounds"})

    with pytest.raises(runlog.BoundaryError) as raised:
        _runner(tmp_path)

    assert "_rounds" in str(raised.value)


def test_no_row_hands_a_seat_the_charter() -> None:
    """``run._pre_charter``'s docstring rests on this, so the negative is checked.

    The charter's one consumer is ``start.record``, which puts it in the ``start``
    boundary; no brief carries it. A prose negative about another module's table
    is what rots first, so it is asserted rather than merely written down — a row
    that starts declaring a charter slot fails here and takes that docstring with
    it.
    """
    carrying = [step.id for step in steps.STEPS if any("charter" in slot for slot in step.slots)]

    assert not carrying, carrying


def test_every_row_of_the_table_has_a_handler_or_a_driver() -> None:
    """A row nobody executes is a stage that silently does not happen."""
    covered = set(run._HANDLERS) | run.LOOP_DRIVEN_STEPS

    assert covered == set(steps.STEP_IDS)


def test_the_slots_the_loop_supplies_compose_every_shipped_template() -> None:
    """The two-party contract: what the loop computes must fill what the PE declared."""
    shipped = {path.name for path in prompt_templates.template_paths()}
    for step in steps.STEPS:
        if step.template is None or step.template not in shipped:
            continue
        text = prompt_templates.render(
            step.template,
            slots={slot: f"<{slot}>" for slot in step.slots},
            constants=steps.CONSTANT_SLOTS,
            row=prompt_templates.RowSlots(step_id=step.id, seat=step.seat),
            wave=step.unit is steps.Unit.WAVE,
        )
        assert text.strip()
