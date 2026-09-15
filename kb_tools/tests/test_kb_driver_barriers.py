"""The barrier registry, answer resolution, and the record's shape.

Both directions of the completeness check hold over the **whole** registry,
because the step table holds rows for every stage: every registered pair is
raised by some row, and every raise site names a registered pair. An entry
nothing can raise is an escalation class that silently does not happen, and a
raise site naming an unregistered pair is a stop nobody can answer.
"""

import json

import pytest

from kb_tools import kb_pipeline
from kb_tools.kb_driver import barriers, baton, config, steps

RAISED_BY_THE_TABLE = frozenset(pair for step in steps.STEPS for pair in step.barrier_pairs)

#: Transcribed — pair → admissible answers, in this order. **This is the
#: authority**, and the transcription is deliberate rather than derived: a
#: completeness check computed from ``barriers.REGISTRY`` would agree with the
#: registry by construction and could never report the registry drifting away
#: from the intended set.
DESIGN_5_1: dict[str, tuple[str, ...]] = {
    "start.proceed": ("yes", "no"),
    "spine-seed.runner-choice": ("just", "make"),
    "phase-5.cap-exhausted": ("stop", "authorize-one-more"),
}


def _decision(pair: str, answer: str, *, note: str = "", source: str = "config") -> config.Decision:
    stage, _, kind = pair.rpartition(".")
    return config.Decision(stage=stage, kind=kind, answer=answer, note=note, source=source)


# ---------------------------------------------------------------------------
# The registry, over the whole table
# ---------------------------------------------------------------------------


def test_the_registry_is_exactly_the_pairs_design_5_1_enumerates() -> None:
    """Completeness against ``DESIGN_5_1``, which is the authority.

    Not "the registry has three entries" — that would pass a registry holding
    the wrong three. The pairs and the answers each admits are both checked,
    because an answer set is what an operator is told they may choose from.
    """
    assert len(DESIGN_5_1) == 3
    assert set(barriers.REGISTRY) == set(DESIGN_5_1)
    assert {pair: spec.answers for pair, spec in barriers.REGISTRY.items()} == DESIGN_5_1


def test_the_admissible_answers_config_validates_against_are_the_designs() -> None:
    """An answer outside ``DESIGN_5_1``'s set is exit 13 at load, so the two must agree."""
    assert barriers.ADMISSIBLE == {pair: frozenset(answers) for pair, answers in DESIGN_5_1.items()}


def test_every_pair_the_table_raises_is_registered() -> None:
    """A raise site naming an unregistered pair is a stop nobody can answer."""
    assert RAISED_BY_THE_TABLE
    assert RAISED_BY_THE_TABLE <= set(barriers.REGISTRY)


@pytest.mark.parametrize("pair", sorted(RAISED_BY_THE_TABLE))
def test_a_raised_pair_has_answers_a_stop_and_a_question(pair: str) -> None:
    spec = barriers.spec(pair)

    assert len(spec.answers) >= 2, "a barrier with one answer is an exit wearing a barrier's clothes"
    assert spec.stopping <= set(spec.answers)
    assert spec.question.strip().endswith("?")
    assert spec.pair == pair


def test_no_registered_pair_is_without_a_raise_site() -> None:
    """An entry nothing can raise never happens.

    Both directions hold over the whole registry — no pair is waiting on a
    stage whose rows have yet to land.
    """
    assert set(barriers.REGISTRY) == RAISED_BY_THE_TABLE
    for spec in barriers.REGISTRY.values():
        assert spec.stage in kb_pipeline.STAGE_IDS


def test_a_cap_exhausted_barrier_reports_a_loop_that_hit_its_limit() -> None:
    """A driver loop at its cap is exit 11, not the plain barrier stop."""
    for pair, spec in barriers.REGISTRY.items():
        expected = baton.EXIT_GATE_RED if "cap-exhausted" in pair else baton.EXIT_BARRIER
        assert spec.exit_code == expected, pair


def test_the_admissible_map_is_what_config_validates_against() -> None:
    assert set(barriers.ADMISSIBLE) == set(barriers.REGISTRY)
    assert barriers.ADMISSIBLE[barriers.START_PROCEED] == frozenset({"yes", "no"})


def test_an_unregistered_pair_is_a_driver_defect() -> None:
    with pytest.raises(Exception, match="no such barrier is registered"):
        barriers.spec("phase-9.invented")


# ---------------------------------------------------------------------------
# Resolution: precedence, consumed-once, grants, unconsumed
# ---------------------------------------------------------------------------


def test_decide_takes_precedence_over_config_for_its_pair() -> None:
    resolver = barriers.Resolver(
        config_decisions={barriers.START_PROCEED: _decision(barriers.START_PROCEED, "no")},
        cli_decisions=[_decision(barriers.START_PROCEED, "yes", source="cli")],
    )

    decision = resolver.take(barriers.START_PROCEED)

    assert decision is not None
    assert decision.answer == "yes"
    assert decision.source == "cli"


def test_an_answer_is_consumed_at_most_once_per_run() -> None:
    """The second raise has no answer, even though config still holds one."""
    pair = barriers.START_PROCEED
    resolver = barriers.Resolver(config_decisions={pair: _decision(pair, "yes", note="already confirmed")})

    first = resolver.take(pair)
    second = resolver.take(pair)

    assert first is not None and first.answer == "yes"
    assert first.note == "already confirmed"
    assert second is None


def test_a_pair_that_was_never_raised_reports_its_decision_as_unconsumed() -> None:
    """An operator resuming past an already-recorded gate learns their answer did nothing."""
    resolver = barriers.Resolver(
        config_decisions={},
        cli_decisions=[
            _decision(barriers.SPINE_SEED_RUNNER_CHOICE, "just", source="cli"),
            _decision(barriers.START_PROCEED, "yes", source="cli"),
        ],
    )

    resolver.take(barriers.START_PROCEED)

    assert resolver.unconsumed == ("spine-seed.runner-choice=just",)


def test_a_raised_but_unanswered_pair_is_not_reported_as_unconsumed() -> None:
    resolver = barriers.Resolver(config_decisions={}, cli_decisions=[])

    assert resolver.take(barriers.START_PROCEED) is None
    assert resolver.unconsumed == ()


def test_grants_are_counted_per_pair_and_start_at_zero() -> None:
    """The arithmetic: effective_cap = cap + grants_this_process."""
    pair = "phase-1a.cap-exhausted"
    resolver = barriers.Resolver(config_decisions={})

    assert resolver.grants(pair) == 0
    assert resolver.grant(pair) == 1
    assert resolver.grants(pair) == 1
    assert resolver.grants("phase-5.cap-exhausted") == 0


def test_no_barrier_answers_from_a_config_field_outside_the_barrier_tables() -> None:
    """``Resolver.resolve`` went with the build mode, and the registry keeps only one door.

    Its whole subject was ``[run] build_mode`` as a carrier the barrier table
    overrode. With no such field left, an answer reaches a barrier through
    ``--decide`` or a ``[barriers.*]`` table and through nothing else — asserted
    over the surface rather than described, since a second door that reappeared
    would be a second precedence rule for an operator to learn.
    """
    assert not hasattr(barriers.Resolver, "resolve")


# ---------------------------------------------------------------------------
# The record
# ---------------------------------------------------------------------------

RENDER = "[kb-build] status: in progress (2 of 5 stages recorded)\n[x] start  the boundary\n[kb-build] contract\n"


def test_the_record_leads_with_the_whole_render_and_carries_the_ask_verbatim() -> None:
    spec = barriers.spec(barriers.SPINE_SEED_RUNNER_CHOICE)
    record = barriers.record(
        spec,
        render=RENDER,
        artifacts=["kb-root/entry-point.md"],
        run_dir="/runs/20260901T000000-1",
        unconsumed=["start.proceed=yes"],
    )

    text = baton.render_record(record)

    assert text.startswith(RENDER.rstrip("\n")), "trimming the render re-creates the paraphrase failure"
    assert "# Barrier: spine-seed / runner-choice" in text
    assert f"**Question**: {spec.question}" in text
    assert "**Admissible answers**: just | make" in text
    assert "**Artifact**: kb-root/entry-point.md" in text
    assert "never its content" in text
    # The baton is the caller's to append, so the record body carries none.
    assert baton.PREFIX not in text


def test_the_records_json_object_carries_exactly_the_fields_the_relay_reads() -> None:
    record = barriers.record(
        barriers.spec("phase-5.cap-exhausted"),
        render=RENDER,
        artifacts=["review/phase-5-r3-tech-writer-reviewer.md"],
        run_dir="/runs/r1",
        unconsumed=(),
    )

    payload = json.loads(baton.render_record(record).split("```json\n")[1].split("\n```")[0])

    assert payload == {
        "stage": "phase-5",
        "kind": "cap-exhausted",
        "answers": ["stop", "authorize-one-more"],
        "artifacts": ["review/phase-5-r3-tech-writer-reviewer.md"],
        "run_dir": "/runs/r1",
        "exit_code": baton.EXIT_GATE_RED,
        "unconsumed_decisions": [],
    }


def test_an_answered_stop_is_recorded_as_answered() -> None:
    """``stop`` still persists a record; it just is not asking anything."""
    record = barriers.record(
        barriers.spec("phase-5.cap-exhausted"),
        render=RENDER,
        run_dir="/runs/r1",
        answered="stop",
    )

    assert "**Answered**: stop" in baton.render_record(record)


def test_a_record_without_a_render_is_a_driver_defect() -> None:
    with pytest.raises(Exception, match="leads with the display"):
        barriers.record(barriers.spec(barriers.START_PROCEED), render="   ", run_dir="/runs/r1")
