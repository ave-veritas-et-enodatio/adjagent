"""Dependency attribution: the edges between the claims the graph already holds.

**The passes are separate because inference is the part that needs repeating.**
A failed or improved inferential pass must not cost the mechanical work, and
must be re-runnable against a different model or a better ask without rebuilding
the tree. That is an operational property, and it is why this is a second
entry point over the first's output rather than a branch inside it.

**It runs after claim discovery** (:mod:`discover`), over a graph that by then
includes the claims nobody marked. Nothing here mints a node, and nothing there
authors an edge.

Stages, in order:

* **A′, :mod:`conform`** — the same structural checks the declared pass makes,
  and in place of its cleanliness check the per-document entry condition:
  :func:`conform.pass_two_gate`.
* **B, :mod:`inventory`** — the same scan. The tree gained metadata and no
  prose, so every claim site it found before is where it was.
* **:mod:`graph`** — the authored claim graph, read back off the tree's own
  declarations and registers.
* **D, :mod:`attribute`** — the narrowing, which settles an edge outright where
  containment directs it, demotes to a ``references`` edge any settled edge
  lying on a cycle, and offers a candidate pair where containment directs
  nothing; then one bounded selection per source claim over what is left, and
  the two mechanical checks that bound it. Inference reaches it through
  :mod:`ask`'s injected seam, which is why this pipeline takes a selector rather
  than building one.
* **F′, :func:`write.write_edges`** — pass 3 of the write path, one batch.
* **G, :mod:`gate`** — the runner's refresh and verify targets. Exits on the
  return code.

**Nothing here exits on a model's opinion.** Every stage ends on a comparison
between two artifacts or on a return code, and the one inference is asked for a
selection from a set it did not choose.

**Only the asking is conditional.** ``selector=None`` is a run with no model
reachable, and every stage above still runs: the entry condition, the inventory,
the authored graph, the narrowing, the acyclicity check over what it settled,
the write and the gates. What such a run does without is the selection over the
pairs containment left open — those pairs carry no ``depends`` edge — so its
dependency set is a subset of what a full run authors, never a different one.
The report says which run it was on its own line, in both directions, because a
corpus that left no pair open and a build that could not ask about the ones it
did are otherwise the same silence.

**What such a run does not do without is the cross-references themselves.**
Every open pair is recorded as a ``references`` edge either way
(:mod:`attribute`): the corpus states that one claim's text names another, and
that statement is not conditional on a model being reachable. A full run
differs only in that pairs the selection raised to dependencies are recorded as
dependencies instead.
"""

from collections.abc import Sequence
from pathlib import Path

from . import attribute, conform, gate, graph, inventory, tree, write
from .report import FACT, PASS, ClaimGraphError, Finding, Report


def _census_findings(
    state: conform.PassTwoState, authored: graph.AuthoredGraph, sites: inventory.Inventory
) -> list[Finding]:
    return [
        Finding(
            FACT,
            "stage-A-determinations",
            f"{len(state.hosting)} documents host claims, {len(state.awaiting)} await claim identification, "
            f"{len(state.determined)} carry an authored no-claim reason and are not this pass's to reopen",
        ),
        Finding(
            FACT,
            "authored-graph",
            f"{len(authored.nodes)} claims across {len(authored.documents())} hosting documents",
        ),
        Finding(
            FACT,
            "stage-D-anchors",
            f"{len(sites.anchors)} cross-reference anchors, "
            f"{sum(1 for a in sites.anchors if a.target is not None)} resolving to a document, "
            f"{sum(1 for a in sites.anchors if (a.hosting_environment or '').casefold() == attribute.PROOF_ENVIRONMENT)}"
            f" sitting inside a proof",
        ),
    ]


def _unasked_finding(*, offered: int, sources: int, asked: bool) -> Finding:
    """What the narrowing left open, and whether anybody was asked about it.

    Total over both dimensions and stated in the zero form as well, because the
    two absences a reader has to tell apart — a corpus whose references
    containment settled outright, and a build with no model to put the rest to —
    produce the same dependency count and the same silence everywhere else.

    What is *not* at stake is whether the pair was recorded: every open pair is
    a ``references`` edge either way (``stage-D-references``). This line is
    about the dependency question alone.
    """
    if not offered:
        detail = "none — the narrowing left no candidate pair open, so there was nothing to ask about"
    elif asked:
        detail = f"none — every one of {offered} candidate pairs over {sources} claims was put to a model"
    else:
        detail = (
            f"{offered} candidate pairs over {sources} claims were put to no model — this build asked none, "
            f"so among them no dependency is asserted and none is denied; each is recorded as a reference"
        )
    return Finding(FACT, "stage-D-unasked", detail)


def _attribute_finding(
    *, edges: int, settled: int, offered: int, demoted: Sequence[tuple[str, str]], asked: bool
) -> Finding:
    """The stage's verdict: what was authored, under which mode, and what a ring cost it.

    The demotion is stated in the zero form as well, because a corpus whose
    settled edges are acyclic and one that lost a ring to the constraint pass
    identically otherwise — and the second is the one a reader has to be able to
    go and look at. Each demoted edge is named, a demoted edge being an ordinary
    ``references`` edge once written and findable by nothing else.
    """
    mode = (
        f"{edges} edges: {settled} settled by containment and the rest of {offered} candidate pairs "
        f"selected as dependencies, acyclic before the write"
        if asked
        else f"{edges} edges, every one settled by containment with no model asked, acyclic before the write"
    )
    cost = (
        "no settled edge lay on a cycle, so none was demoted"
        if not demoted
        else f"{len(demoted)} settled edges lay on a cycle and are recorded as references instead, the ring "
        f"disproving for the set the direction containment read for each: "
        + ", ".join(f"{source} -> {target}" for source, target in demoted)
    )
    return Finding(PASS, "stage-D-attribute", f"{mode}; {cost}")


def build(*, kb_root: Path, repo_root: Path, scratch: Path, selector: attribute.Selector | None) -> Report:
    """Run the discovered pass's dependency attribution over ``kb_root``.

    ``selector`` is ``None`` for a run with no model reachable: the mechanical
    half runs and its edges are recorded; the pairs it left open are reported
    and left open.
    """
    report = Report()

    try:
        documents = tree.read(kb_root)
        state = conform.pass_two_gate(documents)
        report.findings.append(
            Finding(PASS, "stage-A-conformance", f"{len(documents.documents)} documents conform and admit this pass")
        )

        sites = inventory.scan(documents)
        authored = graph.read(documents, sites)
        report.findings += _census_findings(state, authored, sites)

        narrowed = attribute.narrow(documents, authored, sites)
        offered = sum(len(question.candidates) for question in narrowed.questions)
        routes = ", ".join(f"{route} {count}" for route, count in sorted(narrowed.routes.items())) or "none"
        report.findings.append(
            Finding(
                FACT,
                "stage-D-settled",
                f"{len(narrowed.edges)} edges settled by proof containment, no model asked; by the rule that "
                f"resolved the referenced claim: {routes}",
            )
        )
        report.findings.append(
            Finding(
                FACT,
                "stage-D-candidates",
                f"{offered} candidate pairs over {len(narrowed.questions)} claims; "
                f"{len(authored.nodes) - len(narrowed.questions)} claims have an empty candidate set and can "
                f"carry no edge beyond what containment already settled",
            )
        )
        report.findings.append(
            _unasked_finding(offered=offered, sources=len(narrowed.questions), asked=selector is not None)
        )

        edges = attribute.attribute_dependencies(documents, authored, sites, selector)
        report.findings.append(
            _attribute_finding(
                edges=len(edges),
                settled=len(narrowed.edges),
                offered=offered,
                demoted=narrowed.demoted,
                asked=selector is not None,
            )
        )

        references = attribute.references_beyond(narrowed, edges)
        report.findings.append(
            Finding(
                FACT,
                "stage-D-references",
                f"{len(references)} cross-references recorded that carry no direction and "
                f"{len(narrowed.references) - len(references)} that the selection raised to dependencies; "
                f"a reference gates nothing, enters no solidity and is under no acyclicity constraint",
            )
        )

        report.findings += write.write_edges(edges, kb_root=kb_root, scratch=scratch, references=references)
    except ClaimGraphError as error:
        report.findings.append(error.finding())
        return report

    report.findings += gate.run(repo_root)
    return report
