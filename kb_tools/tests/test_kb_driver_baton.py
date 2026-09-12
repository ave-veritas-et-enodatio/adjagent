"""Baton completeness.

Every enumerated exit code renders a baton, and an unlisted code renders the
fallback: an unrecognized exit state is the one place a relaying session will
otherwise improvise.
"""

import pytest

from kb_tools import kb_util
from kb_tools.kb_driver import baton

# A table transcribed from the design rather than from the code under test —
# the point of the guard is that the two agree.
DESIGN_CODES = (0, 10, 11, 12, 13, 14, 15, 16, 17, 18, 21, 22)


def _asks(block: str) -> str:
    """The ASK section of a rendered baton."""
    lines = block.splitlines()
    start = lines.index(f"{baton.PREFIX} ASK THE USER:")
    end = next(i for i, line in enumerate(lines) if line.startswith(f"{baton.PREFIX} THEN RUN"))
    return "\n".join(lines[start + 1 : end])


def test_every_design_code_has_a_baton() -> None:
    assert baton.CODES == DESIGN_CODES


@pytest.mark.parametrize("code", DESIGN_CODES)
def test_every_code_renders_a_complete_card(code: int) -> None:
    # A pair is supplied so the two answer-substituting rows render their own
    # card rather than the no-barrier one — this guard is over the enumeration.
    block = baton.render(
        code,
        baton.BatonContext(invocation="--config cfg.toml", pair="start.proceed", question="q?", run_dir="/runs/1"),
    )
    lines = block.splitlines()
    assert all(line.startswith(baton.PREFIX) for line in lines)
    assert f"{baton.PREFIX} PLACE IN YOUR MESSAGE BODY, VERBATIM:" in lines
    assert f"{baton.PREFIX} ASK THE USER:" in lines
    assert any(line.startswith(f"{baton.PREFIX} THEN RUN") for line in lines)
    assert _asks(block).strip()
    # Teeth: an enumerated code that lost its row would silently render the
    # fallback, which is a passing card for the wrong reason.
    assert "do not interpret it" not in block


@pytest.mark.parametrize("code", [1, 9, 19, 23, 99, -1])
def test_unlisted_code_renders_the_fallback(code: int) -> None:
    block = baton.render(code)
    assert "report this output verbatim and stop; do not interpret it" in _asks(block)
    assert f"{baton.PREFIX}   nothing" in block.splitlines()


def test_barrier_baton_carries_question_answers_and_resume_command() -> None:
    block = baton.render(
        baton.EXIT_BARRIER,
        baton.BatonContext(
            invocation="--config .claude-temp/kb-build/driver-run.toml",
            pair="phase-1b.design-gate",
            question="phase-1b design gate — approve, revise (with direction), or cancel?",
            admissible=("approve", "revise", "cancel"),
        ),
    )
    assert "phase-1b design gate — approve, revise (with direction), or cancel?" in block
    assert f"{baton.PREFIX} ADMISSIBLE ANSWERS:" in block
    assert "approve | revise | cancel" in block
    assert "THEN RUN, WITH THE ANSWER SUBSTITUTED:" in block
    assert f"{kb_util.DRIVER_INVOCATION} run --config .claude-temp/kb-build/driver-run.toml" in block
    assert "--decide phase-1b.design-gate=<answer>" in block


def test_unconsumed_decisions_are_named_in_the_baton() -> None:
    block = baton.render(
        baton.EXIT_OK,
        baton.BatonContext(unconsumed_decisions=("phase-1b.design-gate=approve",)),
    )
    assert "UNCONSUMED --decide (never raised in this run):" in block
    assert "phase-1b.design-gate=approve" in block


def test_missing_question_on_a_raised_barrier_is_visible_rather_than_blank() -> None:
    """The alarm, still armed: a raised barrier with no question looks like the defect it is."""
    block = baton.render(
        baton.EXIT_BARRIER,
        baton.BatonContext(invocation="--config cfg.toml", pair="start.proceed"),
    )
    assert "(missing — read the barrier record and report it verbatim)" in _asks(block)


@pytest.mark.parametrize("code", [baton.EXIT_BARRIER, baton.EXIT_GATE_RED])
def test_a_stage_that_failed_mechanically_is_not_rendered_as_an_ask(code: int) -> None:
    """No pair means no barrier: no blank question, and nothing to substitute an answer into.

    Both codes are reachable without a barrier — a red front end and a red gate
    are exits, not questions — and the barrier card asked those operators a
    blank question and told them to resume with `--decide <stage>.<kind>=`,
    which answers nothing that was raised.
    """
    block = baton.render(
        code,
        baton.BatonContext(
            invocation="--source main.tex",
            detail=("kb_claimgraph --pass 1 --scope block-hosted exited 1",),
        ),
    )

    assert "(missing —" not in block
    assert "<stage>.<kind>" not in block
    assert "--decide" not in block
    assert "THEN RUN, WITH THE ANSWER SUBSTITUTED:" not in block
    assert "kb_claimgraph --pass 1 --scope block-hosted exited 1" in _asks(block)


def test_detail_lines_ride_the_ask() -> None:
    block = baton.render(
        baton.EXIT_CONFIG,
        baton.BatonContext(detail=("[run] sources is required and has no default",)),
    )
    assert "[run] sources is required and has no default" in _asks(block)
