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

from kb_tools.kb_driver import prompt_templates, run, steps

PREFLIGHT_WITH_RUNNER = (
    "[preflight] PASS git-repo          clean\n"
    "[preflight] FACT kb-root           absent (kb-root)\n"
    "[preflight] FACT runner-file       justfile (runner: just)\n"
    "[preflight] PASS: environment ready.\n"
)
PREFLIGHT_WITHOUT_RUNNER = PREFLIGHT_WITH_RUNNER.replace(
    "justfile (runner: just)", "neither justfile nor Makefile — --runner will be required to seed"
)


def test_resume_skip_needs_every_declared_artifact_present_and_non_empty(tmp_path: Path) -> None:
    """The resume-skip predicate. An empty file is not evidence of finished work."""
    full, empty, absent = tmp_path / "a.md", tmp_path / "b.md", tmp_path / "c.md"
    full.write_text("x\n", encoding="utf-8")
    empty.touch()

    assert run.resume_skip((full,))
    assert not run.resume_skip((full, empty))
    assert not run.resume_skip((full, absent))
    assert not run.resume_skip(()), "a row declaring no artifacts is never skipped on the evidence"


def test_pending_members_drops_only_the_members_whose_artifacts_landed(tmp_path: Path) -> None:
    """Partial-wave reconciliation, at member granularity."""
    done = run.Member(name="a", source="A.tex", artifact=tmp_path / "a.md")
    missing = run.Member(name="b", source="B.tex", artifact=tmp_path / "b.md")
    done.artifact.write_text("survey\n", encoding="utf-8")

    assert run.pending_members((done, missing)) == (missing,)


def test_the_round_is_the_max_round_number_not_the_file_count(tmp_path: Path) -> None:
    """Counting files runs iteration numbers high and trips the cap early."""
    for name in ("phase-4-r1-structure.md", "phase-4-r1-accuracy.md", "phase-4-r1-burn-down.md"):
        (tmp_path / name).write_text("x\n", encoding="utf-8")

    assert run.next_round(tmp_path, stage="phase-4", series="r") == 2


def test_the_two_series_are_counted_from_their_own_filenames(tmp_path: Path) -> None:
    for name in ("phase-4-r1-structure.md", "phase-4-r2-structure.md", "phase-4-g1-structure.md"):
        (tmp_path / name).write_text("x\n", encoding="utf-8")

    assert run.next_round(tmp_path, stage="phase-4", series="r") == 3
    assert run.next_round(tmp_path, stage="phase-4", series="g") == 2
    assert run.next_round(tmp_path, stage="phase-4", series="g") != run.next_round(
        tmp_path, stage="phase-3a", series="g"
    )


def test_an_empty_review_directory_is_round_one(tmp_path: Path) -> None:
    assert run.next_round(tmp_path / "absent", stage="phase-4", series="r") == 1


@pytest.mark.parametrize(
    ("series", "round_number", "expected"),
    [("r", 1, 0), ("r", 3, 2), ("g", 1, 1), ("g", 2, 2)],
)
def test_revisions_spent_differs_only_by_the_series_opening_move(series: str, round_number: int, expected: int) -> None:
    assert run.revisions_spent(series=series, round_number=round_number) == expected


def test_the_runner_fact_line_is_the_detection_source() -> None:
    assert run.runner_file_present(PREFLIGHT_WITH_RUNNER)
    assert not run.runner_file_present(PREFLIGHT_WITHOUT_RUNNER)


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
