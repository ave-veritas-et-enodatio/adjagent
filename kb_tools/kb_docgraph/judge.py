"""The heuristic seam: is this decomposition good enough, or does it need help?

The question is real — a volume that is one undivided wall of text, a leaf far
larger than its siblings, a hierarchy one level deep. The judgement is not
designed, so it is not written. What is owed at this point in the pipeline is the
signature, so that the stage that will one day ask has somewhere to ask.

:func:`judge` accepts every input. :func:`recut` exists, is reached from the
reject branch and from nowhere else, and raises if it is ever invoked. There is
no inference call here, no prompt, and no data format for a response; populating
those is later work.

Judging a decomposition undesirable is deferred, and is **never** done by
truncating it: the tree carries the author's hierarchy at whatever depth they
wrote it, and a depth cutoff standing in for a judgement is the failure mode this
seam exists instead of.
"""

from dataclasses import dataclass

from .outline import VolumeTree


@dataclass(frozen=True)
class Verdict:
    """Accept, or reject carrying a reason. A reason without a rejection is not a state."""

    accepted: bool
    reason: str = ""


def judge(tree: VolumeTree) -> Verdict:
    """Accept every volume. The seat that would decide otherwise does not exist yet."""
    return Verdict(accepted=True)


def recut(tree: VolumeTree, verdict: Verdict) -> VolumeTree:
    """The alternate path. Raises: nothing has specified what a re-cut tree is."""
    raise NotImplementedError(
        f"recut is a stub: {tree.stem} was rejected as {verdict.reason!r}, and the re-cut path is not designed. "
        "judge accepts every volume today, so reaching this is a change to judge that landed without its consumer."
    )
