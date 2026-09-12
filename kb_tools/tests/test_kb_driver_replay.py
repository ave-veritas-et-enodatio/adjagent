"""The replay scenarios: the shapes they compose, and the rows they answer.

Every content helper is asserted by **round-tripping through the real parser**
in ``envelope.py`` rather than against a literal of its own. That is the whole
point of composing from that module's markers: a replayed run must be green against
the contract the driver actually enforces, and a helper that quietly drifted
from it would make the first rung of the ladder measure nothing.

The assignment-table reader (:func:`replay.assignment_rows`) is a two-party
contract between what the driver writes into a wave brief and what the
session reads back out of it; the producer side (``run._domain_table``) has
its own suite, and what is asserted here is the read and the write-to-disk
route's containment check.
"""

import json
from pathlib import Path

import pytest

from kb_tools.kb_driver import call, envelope, replay, runlog, steps


def context_for(tmp_path: Path, *, step: str, brief: str = "brief\n", call_index: int = 0) -> replay.ReplayContext:
    """A call as ``call.py`` composes it: a brief on disk, named ``<seq>-<step>``."""
    briefs = tmp_path / "briefs"
    briefs.mkdir(exist_ok=True)
    brief_path = briefs / f"001-{step}.md"
    brief_path.write_text(brief, encoding="utf-8")
    return replay.ReplayContext(argv=("claude", "-p"), cwd=tmp_path, brief_path=brief_path, call_index=call_index)


def returned_text(response: replay.Response) -> str:
    """The result text the driver would read: the **last** ``result`` event's."""
    events = [json.loads(line) for line in response.lines]
    results = [event for event in events if event.get("type") == "result"]
    assert results, "the response carried no result event"
    return str(results[-1]["result"])


# ---------------------------------------------------------------------------
# The shapes, against the parsers that read them
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(("critical", "warning", "note"), [(0, 0, 0), (2, 1, 4), (0, 7, 0)])
def test_a_scripted_verdict_parses_as_the_counts_it_was_asked_for(critical: int, warning: int, note: int) -> None:
    parsed = envelope.parse_verdict(replay.verdict(critical=critical, warning=warning, note=note))

    assert (parsed.critical, parsed.warning, parsed.note) == (critical, warning, note)


def test_a_scripted_envelope_parses_and_answers_to_the_call_that_asked_for_it() -> None:
    deviation = {"kind": "re-brief", "member": "one", "what": "re-briefed it", "why": "the return was empty"}

    parsed = envelope.parse_envelope(
        replay.envelope(
            step="p4.review", members=("one", "two"), gaps=("a volume that would not open",), deviations=(deviation,)
        ),
        step="p4.review",
    )

    assert [member.name for member in parsed.members] == ["one", "two"]
    assert parsed.gaps == ("a volume that would not open",)
    assert parsed.deviations[0].kind == "re-brief"


def test_a_scripted_envelope_declaring_another_step_is_refused() -> None:
    """The step assertion has teeth, so the round-trip above is not vacuous."""
    with pytest.raises(envelope.ParseError):
        envelope.parse_envelope(replay.envelope(step="p4.review", members=()), step="p4.fix")


# ---------------------------------------------------------------------------
# The assignment table, against the write-to-disk route that reads it
# ---------------------------------------------------------------------------


def test_a_brief_with_no_assignment_table_yields_no_rows() -> None:
    assert replay.assignment_rows("# Review\n\nNothing tabular here.\n") == ()


def test_write_assigned_refuses_a_path_that_leaves_the_repo(tmp_path: Path) -> None:
    """The containment check both remaining write routes (review, meta-docs) share."""
    repo = tmp_path / "repo"
    repo.mkdir()
    context = context_for(repo, step="p4.review")

    with pytest.raises(runlog.BoundaryError):
        replay.write_assigned(context, "../outside.md", "text")

    assert not (tmp_path / "outside.md").exists()


# ---------------------------------------------------------------------------
# Dispatch by step
# ---------------------------------------------------------------------------


def test_by_step_answers_the_row_the_brief_names(tmp_path: Path) -> None:
    scenario = replay.by_step({"p4.synthesize": replay.clean("burn-down"), "p5.review": replay.clean("findings")})

    assert returned_text(scenario(context_for(tmp_path, step="p4.synthesize"))) == "burn-down"
    assert returned_text(scenario(context_for(tmp_path, step="p5.review"))) == "findings"


def test_the_re_ask_of_a_step_is_answered_as_that_step(tmp_path: Path) -> None:
    """The one re-ask re-briefs the same row under a marked name; it is the same row."""
    scenario = replay.by_step({"p4.synthesize": replay.clean("burn-down")})

    assert returned_text(scenario(context_for(tmp_path, step=f"p4.synthesize{call.REASK_SUFFIX}"))) == "burn-down"


def test_an_unscripted_step_stops_rather_than_answering_green(tmp_path: Path) -> None:
    """A default answer is how a replayed run reports a step it never exercised as green."""
    scenario = replay.by_step({"p4.synthesize": replay.clean("burn-down")})

    with pytest.raises(runlog.BoundaryError):
        scenario(context_for(tmp_path, step="unscripted-step"))


# ---------------------------------------------------------------------------
# What the scenario map covers
# ---------------------------------------------------------------------------


def test_the_scenario_map_answers_every_calling_row_and_invents_none() -> None:
    """Set equality against the table: every calling row scripted, nothing extra.

    The same claim ``test_kb_driver_steps.py`` makes from the table's side. It
    is asserted from both because the two files fail for different reasons — a
    new row here, a stale scenario there — and the message you get matters.
    """
    calling = {step.id for step in steps.STEPS if step.unit in call.CALL_UNITS}

    assert calling == set(replay.SCENARIOS)


@pytest.mark.parametrize(
    ("stem", "expected"),
    [
        ("001-p2.6.apply", "p2.6.apply"),
        ("002-p2.6.apply-scores", "p2.6.apply-scores"),
        (f"003-p2.6.apply{call.REASK_SUFFIX}", "p2.6.apply"),
        (f"004-p2.6.apply-scores{call.REASK_SUFFIX}", "p2.6.apply-scores"),
    ],
)
def test_a_step_id_that_prefixes_another_is_not_confused_with_it(tmp_path: Path, stem: str, expected: str) -> None:
    """``p2.6.apply`` is a prefix of ``p2.6.apply-scores``, and both are mint-bearing.

    A substring match answers the apply row's shape to the apply-scores call,
    which writes the wrong thing into a register at a mint boundary — the
    quietest way this module could be wrong.
    """
    context = context_for(tmp_path, step=stem.partition("-")[2])

    assert context.step_id == expected


def test_the_dry_run_invoker_is_the_green_run(tmp_path: Path) -> None:
    """``--dry-run`` selects :func:`replay.green_run`, answered per row off the brief's name.

    The review row is the one asked here because its answer is a shape and not
    a document: the write-to-disk rows write to the path their brief names, so
    a scenario handed a stand-in brief would have nowhere to write — which is
    the write-to-disk route working, not a gap in it.
    """
    invoker = replay.dry_run_invoker()
    brief = tmp_path / "001-p5.review.md"
    brief.write_text("brief\n", encoding="utf-8")

    invocation = invoker.run(argv=("claude", "-p"), cwd=tmp_path, env={}, brief_path=brief)
    text = returned_text(replay.Response(lines=tuple(line.rstrip("\n") for line in invocation.lines())))

    assert invoker.calls == 1
    assert envelope.parse_verdict(text).critical == 0
