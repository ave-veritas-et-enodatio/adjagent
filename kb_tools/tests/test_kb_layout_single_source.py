"""The layout's single-sourcing instrument: one home per path.

``kb_pipeline`` is the home of every layout path a card renders, a
postcondition checks, or a coverage check depends on; ``kb_driver.steps``
imports those and states only what is the driver's alone. The subject here is
the enumerable structure — :data:`steps.SCRATCH_LAYOUT` — rather than a region
of a file, so a path added to the layout has to be declared on one side or the
other before this passes.

Membership is tested by **value, never by identity**: short interned literals
satisfy ``is`` by accident, so an ``is`` test would go green on a restatement.
"""

import pytest

from kb_tools import kb_pipeline
from kb_tools.kb_driver import steps

# The `kb_pipeline` constants the scratch layout is entitled to hold, by name:
# reading them off the module is what makes this a pin rather than a second
# copy of their values. Empty today — the survey manifest was the last layout
# path a card rendered, and it went with the manifest requirement — so the
# whole layout is currently declared driver-only below. The tuple stays as the
# side a shared path is declared on.
PIPELINE_LAYOUT_CONSTANTS: tuple[str, ...] = ()

# The driver-only allow-list. Each member is the driver's alone because no card
# renders it and no postcondition checks it, so it has no second reader to
# drift from: the charter path scratch carries during the run; the findings
# grammar, whose round numbers the driver's own loops reconstruct from these
# filenames; and the overview passage, which the driver persists and then
# substitutes — what a postcondition checks is the document assembled from it,
# under `kb-root/`, which is no part of this layout.
DRIVER_ONLY_LAYOUT = frozenset(
    {
        steps.CHARTER,
        steps.FINDINGS,
        steps.OVERVIEW_PROSE,
    }
)


def _pipeline_layout() -> frozenset[str]:
    return frozenset(getattr(kb_pipeline, name) for name in PIPELINE_LAYOUT_CONSTANTS)


@pytest.mark.parametrize("path", steps.SCRATCH_LAYOUT)
def test_every_layout_path_is_a_pipeline_constant_or_declared_driver_only(path: str) -> None:
    hint = f"{path!r} is stated in the driver alone: single-source it in kb_pipeline, or declare it driver-only here"

    assert path in _pipeline_layout() | DRIVER_ONLY_LAYOUT, hint


def test_both_declared_sets_are_spent_on_the_layout() -> None:
    """A stale entry on either side is a declaration nothing holds up any more."""
    layout = set(steps.SCRATCH_LAYOUT)

    assert DRIVER_ONLY_LAYOUT <= layout
    assert _pipeline_layout() <= layout


def test_the_layout_is_the_slot_the_briefs_receive() -> None:
    """The tuple is the region: what a brief says comes from it and nothing else."""
    slot = steps.CONSTANT_SLOTS["layout-paths"]

    assert slot.splitlines() == [f"{steps.SCRATCH_ROOT}/{path}" for path in steps.SCRATCH_LAYOUT]
