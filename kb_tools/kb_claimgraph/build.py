"""The declared pass: the tree in, the claims the author marked authored into it.

Six stages, run in order. Every one of them terminates on a comparison between
two artifacts or on a return code; none exits on an opinion, and none asks
whether the work is good enough.

**Mechanical end to end.** This pass identifies the claims the author marked and
spends no inference. The two stages that do — claim discovery over the unmarked
documents (:mod:`discover`) and dependency attribution (:mod:`depends`) — are
separate entry points over this pass's output, because a failed or improved
inference must not cost the mechanical work that preceded it and must be
re-runnable without rebuilding the tree.

**This pass's output is a lower bound and says so in its report.** The claims it
authors are the ones the corpus marked; the ones it did not look for are named
by count, and the documents carrying no author-marked block carry a reason that
states the run's scope rather than a finding about the document — which is also
the sentence claim discovery reads as its admission ticket.
"""

from pathlib import Path

from . import assemble, conform, gate, identify, inventory, tree, write
from .report import FACT, PASS, ClaimGraphError, Finding, Report


def _census_findings(census: inventory.Census) -> list[Finding]:
    environments = ", ".join(f"{name}={count}" for name, count in census.environments.items())
    return [
        Finding(
            FACT,
            "stage-B-blocks",
            f"{census.claim_blocks} claim-bearing of {census.blocks} labelled blocks, in "
            f"{census.hosting_documents} documents; environments {environments}",
        ),
        Finding(
            FACT,
            "stage-B-unclassified",
            (
                ", ".join(f"{name}={count}" for name, count in census.unclassified.items())
                + " — environment name(s) this build classifies neither way, admitted as "
                "not-claim-bearing and counted here"
                if census.unclassified
                else "none — every environment name this corpus declares is classified"
            ),
        ),
        Finding(
            FACT,
            "stage-B-unreadable",
            (
                f"{sum(census.unreadable.values())} in "
                + ", ".join(f"{path}={count}" for path, count in census.unreadable.items())
                + " — block(s) stating a result whose display line yields neither a title nor a "
                "locator; each costs itself, entering the graph as no claim, and costs the build nothing"
                if census.unreadable
                else "none — every block stating a result yields a title and a locator off its display line"
            ),
        ),
        Finding(
            FACT,
            "stage-B-maths",
            f"{census.fences} display-maths fences carrying {census.equation_labels} equation labels",
        ),
        Finding(
            FACT,
            "stage-B-anchors",
            f"{census.anchors} cross-reference anchors, {census.resolved_anchors} resolving to a document, "
            f"{census.eqref_anchors} of type eqref",
        ),
        Finding(
            FACT,
            "stage-B-citations",
            ", ".join(f"{name} {count}" for name, count in census.citations.items())
            + " (every inline citation carries its own key, in whichever state it ended in; reference-list "
            "counts the works the volume's own list carries and not citations of them)",
        ),
    ]


def build(*, kb_root: Path, repo_root: Path, scratch: Path) -> Report:
    """Run stages A through G over ``kb_root``. The tree is the sole input."""
    report = Report()

    try:
        documents = tree.read(kb_root)
        conform.gate(documents)
        report.findings.append(Finding(PASS, "stage-A-conformance", f"{len(documents.documents)} documents conform"))

        sites = inventory.scan(documents)
        report.findings += _census_findings(inventory.census(sites))

        claims = identify.block_claims(sites)
        identify.check_block_coverage(claims, sites)
        unmarked = identify.unmarked_documents(documents, sites)
        report.findings.append(
            Finding(
                PASS,
                "stage-C-identify",
                f"{len(claims)} block-hosted claims; {len(unmarked)} leaf-kind documents carry no "
                f"author-marked block and were not read for one",
            )
        )

        plan = assemble.assemble(documents, sites, claims)
        report.findings.append(
            Finding(
                PASS,
                "stage-E-assemble",
                f"{len(plan.entries)} register entries across {len(plan.registers())} registers, "
                f"{len(plan.documents)} frontmatter records, {len(plan.markers)} markers",
            )
        )
        report.findings.append(
            Finding(
                FACT,
                "stage-E-endcap",
                f"{len(plan.works)} external works cited from inside a claim's own block or the proof "
                f"establishing it, carrying {len(plan.rests_on)} off-graph edges from "
                f"{len({position for position, _ in plan.rests_on})} claims — recorded, and gating no "
                f"solidity",
            )
        )

        written, _ = write.write(plan, kb_root=kb_root, scratch=scratch)
        report.findings += written
    except ClaimGraphError as error:
        report.findings.append(error.finding())
        return report

    report.findings += gate.run(repo_root)
    return report
