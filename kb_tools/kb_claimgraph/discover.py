"""Claim discovery — stage C-inf, over the documents the author marked nothing in.

**The missing middle.** The declared pass authors the claims the author marked;
stage D attributes dependencies over an authored graph. Between them sits the
question neither asks: what does a document nobody marked actually state. This
pipeline asks it, once per document, and mints a claim node per record.

Six stages, in order, and every one of them terminates on a comparison between
two artifacts or on a return code:

* **C1, :mod:`conform`** — the same structural checks and the same per-document
  partition ``--pass 2`` runs. This stage's scope is
  :attr:`~conform.PassTwoState.awaiting` and nothing else; a document carrying a
  ``claims:`` list or an authored reason is somebody's finding and is not this
  pass's to reopen.
* **C2, :mod:`inventory`** — the same scan. Three of its readings are used: that
  the documents in scope carry no claim-bearing block (asserted rather than
  assumed, because it is what keeps this stage and C-mech from double-counting
  if the scope ever widens), the maths-fence extents, and — through the
  inventory the ask is composed from — nothing else.
* **C3 and C4, :mod:`identify`** — the ask and the checks over what comes back.
  This is the whole of C-inf's inference.
* **C5, :func:`write.write_claims`** — three of the write path's four passes,
  scoped to one document. Per document, so that a stopped run resumes.
* **C6, :mod:`gate`** — the runner's refresh and verify. Preceded by this
  stage's own exit condition: **no document in the run's scope still reads
  awaiting**, which is a comparison over the tree the run just wrote and is what
  says the stage finished rather than stopped quietly. A document identification
  could anchor nothing in leaves awaiting by carrying a reason of its own, so it
  satisfies this condition rather than halting on it — and is reported by name,
  because a state that exits awaiting quietly is the silent zero this whole
  pipeline exists to remove.

**No number is authored and no edge is.** Every entry's rigor is the pending
literal, and dependency attribution runs afterward from :mod:`depends`, over a
graph that by then includes what this stage minted.

**A run is resumable and a re-run is a partial no-op.** Discovery costs an ask
per document, so an interrupted run is the ordinary case rather than the
exceptional one. There is no transcript replay: a re-run costs inference again
for every document still awaiting, and nothing for one that is not.
"""

from pathlib import Path

from .. import kb_index_lib
from . import conform, gate, identify, inventory, tree, write
from .report import FACT, PASS, ClaimGraphError, Finding, Report


class DiscoveryError(ClaimGraphError):
    """A stage of the discovery run stopped on a comparison it makes."""


def _no_block_in_scope(sites: inventory.Inventory, scope: tuple[str, ...]) -> None:
    """C2 — a document in scope hosts no author-marked block. Vacuous here, and asserted."""
    marked = sorted(set(scope) & sites.hosting_documents())
    if marked:
        raise DiscoveryError(
            "block-in-scope",
            f"{len(marked)} document(s) both await claim identification and host an author-marked claim "
            f"block: {marked[:5]}. The declared pass writes claims onto every such document, so this stage "
            f"and it would each mint a claim for the same site",
        )


def _still_awaiting(kb_root: Path, scope: tuple[str, ...]) -> tuple[str, ...]:
    """C6's own exit condition, over the tree the run just wrote."""
    written = tree.read(kb_root)
    return tuple(
        path
        for path in scope
        if path in written.documents
        and conform.determination(kb_index_lib.parse_frontmatter(written.documents[path].text) or {})
        is conform.Determination.AWAITING
    )


def build(*, kb_root: Path, repo_root: Path, scratch: Path, identifier: identify.Identifier) -> Report:
    """Run C1 through C6 over ``kb_root``. The tree is the sole input."""
    report = Report()

    try:
        documents = tree.read(kb_root)
        state = conform.pass_two_gate(documents)
        report.findings.append(
            Finding(PASS, "stage-C1-entry", f"{len(documents.documents)} documents conform and admit this pass")
        )

        sites = inventory.scan(documents)
        _no_block_in_scope(sites, state.awaiting)
        report.findings.append(
            Finding(
                FACT,
                "stage-C2-scope",
                f"{len(state.awaiting)} documents await claim identification, none of them hosting an "
                f"author-marked block; {len(state.hosting)} host claims and {len(state.determined)} carry an "
                f"authored reason, and neither is this pass's to reopen",
            )
        )

        minted = 0
        anchored_nothing: list[str] = []
        for path in state.awaiting:
            reading = identify.reading_of(documents.documents[path], sites)
            found = identify.infer_claims(reading, identifier)
            fields = kb_index_lib.parse_frontmatter(reading.text) or {}
            report.findings += write.write_claims(found, kind=str(fields.get("kind")), kb_root=kb_root, scratch=scratch)
            report.findings.append(Finding(FACT, "stage-C-inference-cost", f"{path}: {found.telemetry.line()}"))
            minted += len(found.claims)
            if found.anchored_nothing:
                anchored_nothing.append(path)

        if anchored_nothing:
            report.findings.append(
                Finding(
                    FACT,
                    "stage-C-anchored-nothing",
                    f"{len(anchored_nothing)} of {len(state.awaiting)} document(s) end this run with zero "
                    f"claims after identification named results in them: {anchored_nothing}. Each carries a "
                    f"reason saying the anchoring failed, not that the document states nothing, and none of "
                    f"them is re-read by a re-run. Read the captures before treating this tree as complete",
                )
            )

        awaiting = _still_awaiting(kb_root, state.awaiting)
        if awaiting:
            raise DiscoveryError(
                "exit-condition",
                f"{len(awaiting)} document(s) in this run's scope still await claim identification after the "
                f"write: {list(awaiting)[:5]}. The run stopped rather than finished",
            )
        report.findings.append(
            Finding(
                PASS,
                "stage-C-identify",
                f"{minted} claims minted across {len(state.awaiting)} documents; none of them still awaits",
            )
        )
    except ClaimGraphError as error:
        report.findings.append(error.finding())
        return report

    report.findings += gate.run(repo_root)
    return report
