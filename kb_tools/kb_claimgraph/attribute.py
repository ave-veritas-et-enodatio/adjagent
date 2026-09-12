"""Stage D — dependency attribution. Partly mechanical; inferential only for direction.

**The candidate set is narrowed mechanically and the model never adds to it.**
Over stage B's cross-reference anchors, each end of a reference is attributed to
a claim where a rule settles it, and left open where none does:

* the **source** end. A reference sitting inside a claim-bearing block belongs
  to that block's claim; a reference inside a *proof* belongs to the claim that
  proof establishes (:func:`_proofs`); a reference in the prose of a document
  hosting exactly one claim belongs to that claim, there being no other.
* the **target** end. A reference whose fragment names a block's own source
  label lands on that block's claim; an ``eqref`` naming an equation that sits
  inside a claim-bearing block lands on that block's claim, because an equation
  labelled inside a theorem body *is* that theorem's assertion; a reference into
  a document hosting exactly one claim lands on that claim, there being no
  other.
* where an end stays open the enumeration offers every claim it could be, and
  the selection below is what attributes. Where **both** ends are settled *and
  the source end was settled by proof containment*, the edge is authored
  mechanically and no question is asked about it.

**Why a mechanically-derived edge is now authored where it once was withheld.**
Three of the narrowings above used to be exclusions — an ``eqref`` contributed
nothing, a reference whose fragment named a section contributed nothing, and a
reference inside a proof was no more directed than one anywhere else — and each
rested on the same cost/benefit: a wrong edge is worse than a missing one, so
material a rule could not resolve with confidence was dropped rather than
guessed at. That calculus inverted, and the reason is what an edge *is* on this
path. A mechanical edge here is a **draft a later inference pass corrects**, not
a final assertion; the alternative to a rule that sometimes misfires is not a
correct rule but no rule and no result, and a claim recorded as depending on
nothing is itself a wrong answer, arrived at silently and prompting nobody to
revisit it. What stays unacceptable is being provably wrong inside a mechanical
constraint — an edge naming an id that does not exist, or one closing a cycle —
and both of those are checked below, before anything is written. The rulings
each narrowing replaced are kept beside it, because the question was reconsidered
rather than overlooked.

**A reference inside a proof is a dependency, and containment is what directs
it.** A proof establishes the claim it belongs to, so a claim-resolving
reference in a proof body says the proved claim rests on the referenced one.
This needs no model. The ruling it overturns is that an author's cross-reference
"carries no direction: a theorem citing a lemma is usually a dependency, a lemma
citing the theorem it motivates is not, and the markup is identical" — true of a
bare reference in prose, and false here, because the containment is part of the
markup too. Measured across 27 maths papers of the staged corpus, 39% of
cross-references in claim-bearing papers sit inside a proof (``math.AP`` 0.65,
``math.DG`` 0.57, ``math.AG`` 0.54, ``math.PR`` 0.28, ``math.NT`` 0.26,
``math.ST`` 0.24), so this is the bulk of the material and not an edge case.

**Which claim a proof proves is stage B's reading, not this stage's.**
:class:`inventory.Proof` binds each proof block to the *blocks* it establishes —
by the opening run's own ``\\ref`` where the author wrote
``\\begin{proof}[Proof of Theorem \\ref{thm:main}]``, and by the block
*immediately* above where they wrote a bare ``\\begin{proof}``. That binding is
about blocks and lines and is knowable before any id exists, which is why it
lives there and why the off-graph endcap directs a ``\\cite`` through the same
reading rather than through a second one. What :func:`_proofs` does here is the
one thing that needs minted ids: map each subject block to the claim it carries.

What that binding misses, and it is the weaker of the two arms: a proof whose
document opens with it binds to nothing, so a section stating its result
elsewhere contributes no edge unless the opening run names it; a proof under a
remark, an example or a second proof binds to nothing, the block above stating
no claim; a result the author stated in ordinary prose rather than in a labelled
block is not a block at all, so the proof beneath it reads as following whatever
came before it and binds to nothing; and an author who wrote *Proof of Theorem 3*
as plain text rather than as a ``\\ref`` has named a subject this reads as naming
none. Where the opening run names a subject that resolves to no claim, the proof
gets no subject and adjacency is *not* tried behind it — the author said what is
being proved and it is not a node. One more is this stage's alone: a subject
block the tree names but no register entry carries — an undeclared claim — maps
to no node here and the proof binds to nothing. In every one of those cases the
references stay open pairs for the ask rather than becoming directed edges;
nothing is lost that was not already the ask's.

**An equation labelled inside a claim body is that claim's assertion.** The
ruling this replaces is that "an ``eqref`` anchor contributes nothing — it
targets an equation, and an equation is not a node", which is true of the
equation and does not follow for the reference: 232 of 791 claim-reaching
references in an earlier survey were of exactly this shape. An equation is still
not a node; the reference resolves *through* it to the block it sits in.
:func:`_equation_claim` is the join, and it reads the label off the anchor's
third attribute because point 9 leaves an equation's ``\\label`` inside the maths
fence rather than as an addressable id — so an ``eqref`` lands on its document
with an empty fragment, and the label is the whole of what says which equation.
An ``eqref`` whose equation sits in no claim-bearing block still contributes
nothing: there is a claim to resolve to only where the equation is inside one.

**A reference into a document hosting exactly one claim lands on that claim.**
This is the mirror of the source-end rule directly above it — "there being no
other" reads the same at either end — and it is what admits the section
references the third narrowing used to drop. The ruling it replaces held that
"a reference to a document is not a reference to what the document establishes",
since the referent is the section and resolving it would *manufacture a claim
out of containment*; that objection stands wherever the target hosts several
claims, and there the reference is still ambiguous and still the ask's. It does
not stand where the document hosts one: nothing is manufactured, because there
is nothing else the reference could bear on. Measured over a seven-volume tree
at the declared graph, 104 of 159 resolving ``ref`` anchors are section
references, and dropping them took stage D from 15 sources / 29 pairs to 10 /
15. The narrowing's third argument — that its *cost* is k×m in exactly the place
discovery multiplies m — is unanswered and is paid: a section reference into a
multi-claim document offers every claim that document hosts.

The refusal these three replaced is unchanged in one place: **shared containment
in a directory contributes no candidate on its own**, filing being a placement
decision and not a dependency relation. Nothing above derives a candidate from
where a document sits; every one of them derives it from something the author
wrote.

**Leaf-prose references contribute candidates**, which is the ruling the corpus
forced: most of the tree's ``ref`` anchors sit in prose rather than inside a
block, and a rule confining candidates to block interiors would leave most
claims with nothing to select from.

**A pair containment cannot direct is recorded as a ``references`` edge, never
discarded.** The author wrote one claim's own identifier inside another claim's
text, in the formal notation LaTeX generated — *a second certificate, distinct
from the ``V`` of Theorem 2*, *not derivable from the bare cap-table dynamics
(Proposition 8(iii))*. What the narrowing cannot settle is the *direction of
dependence*; the fact that the source names the target is not in doubt and is
the whole content of the class. Measured on that same tree at the declared
graph, four of five such references read as contrasts or pointers rather than
dependencies, so a stage that recorded only dependencies discarded four facts to
avoid one wrong one — and left the graph asserting the claims are unrelated,
which is a wrong answer arrived at silently.

Three properties keep it from being a weak ``depends``. It carries **no
acyclicity constraint**: two claims naming each other is the author's argument,
and the measured corpus contains exactly such a 2-cycle. It **enters no
solidity computation**, being carried on ``ClaimEntry.references`` rather than
``depends_on``, which is the tuple every scorer reads. And it **does not answer
the question**: the same pair is still put to the model, because whether a
naming is also a dependency is exactly what the narrowing could not decide. A
pair the model returns is an upgrade rather than a second edge —
:func:`references_beyond` is the difference the caller writes.

**Point 6 is satisfied by the check and not by abstention.** SPEC.md warns that
a consumer taking cross-references as dependency edges would import the author's
own cycles into a graph that must stay acyclic. That is a reason to check, and
the check runs here over the mechanical edges alone before any question is asked
and over the whole set before anything is written.

**A ring among the settled edges costs its own edges and not the build.** The
cycle proves that the edges on it cannot all be dependencies. It does not prove
that the corpus reasons circularly — four results whose proofs cite one another
are far more likely to be a single argument carried across several statements,
or proofs citing each other for symmetry, *the argument is as in Lemma 4*, which
containment reads as direction-bearing and which is not a dependency — and it
proves nothing whatever about the paper's other edges, so stopping the stage
discards a whole graph over one ring and leaves every claim in the paper
recorded as resting on nothing. Each edge on a ring is therefore recorded as a
``references`` edge (:func:`cycle_edges`), which is the class for a relationship
the corpus states and nothing can direct, and the rest of the settled edges
stand. Measured over a 50-paper arXiv sweep with no model reachable, three
papers carried such a ring — two 4-cycles and a 2-cycle — and each took its
entire graph down with it.

**Every edge on the ring is demoted, not a minimum feedback set.** Breaking one
edge per ring would leave the others asserted as dependencies, and a ring is
exactly where no evidence separates its members: each is a reference inside the
proof of the claim it runs from, read by one rule from one kind of markup.
Picking one by a sort key would be a discrimination nothing in the corpus
supports, and on a 2-cycle it is a coin flip reported as a finding. Demoting the
whole ring gives up only the direction claim — the half the cycle disproves —
and costs no relationship at all, the demoted edge being recorded rather than
dropped. This is what separates the ruling from the three the corpus reversed
above: those traded a fact for silence, and this trades a direction for a
weaker, true statement.

**A demoted pair is not reopened as a question.** The narrowing's questions are
the pairs containment left open, and a ring's edges are not among them either
way: containment did read a direction for each, and what the ring refuses is the
*set*, not any one reading. Putting them to a model would enlarge the ask on
exactly the corpora where the mechanical answer is least stable, and would make
the recorded graph depend on a model being reachable — so a run with a model and
a run without demote the same edges and record the same references.

**The model is asked for a selection and never for a verdict.** One bounded
question per source claim, answered by returning the subset of an enumerated
set of ids. Two mechanical checks bound it, both comparisons:

* every returned id is in the candidate set that source was given — set
  membership, no parsing;
* the assembled edge set is acyclic **before anything is written**, through
  :func:`kb_index_lib.compute_solidity` raising
  :class:`kb_index_lib.SolidityCycleError`.

**The pre-write check is the post-write gate, run early over the same graph.**
:func:`kb_index_lib.compute_solidity` walks every claim and every support
whether or not it is scored, so a cycle among the ``*pending*`` claims this
build authors raises on either side of the write. The synthetic entries exist
only because the proposed edge set is not on disk yet; nothing is written from
them.

**An answer that did not parse costs a re-ask of its own.** It arrived, so it is
asked again — carrying the parse refusal's own message — and a second
unparseable answer stops the stage naming the source claim. **That allowance is
not the membership check's**: a source whose first answer did not parse still
has that check ahead of it and reaches it with its re-ask intact. Only a call
that did not *complete* escapes both, there being no answer to ask again about
(:mod:`ask`).

**An empty edge set is a legitimate output.** Claims whose candidate set is
empty carry no edges, and chains terminating on dependency-free foundational
claims is what SPEC's scoping describes rather than a gap to fill.

**The narrowing is not the ask's preamble, and a run that cannot ask still
runs it.** :func:`narrow` reads the tree, the authored graph and the inventory
and asks nothing of anybody, so the edges it settles are the corpus's answer
and not a model's. :func:`attribute_dependencies` takes ``selector=None`` for a
run with no model reachable: the settled edges are checked for cycles and
recorded exactly as they would be otherwise, and the open pairs are left open
and unrecorded rather than guessed at. Discarding the settled half because the
open half cannot be asked about would record every one of those claims as
resting on nothing — a wrong answer arrived at silently, which is the thing this
stage's rulings above exist to refuse.
"""

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Protocol, TypeVar

from .. import kb_index_lib
from .graph import AuthoredGraph, ClaimNode
from .inventory import PROOF_ENVIRONMENT, Anchor, Block, Inventory, MathFence
from .report import AnswerFormatError, ClaimGraphError
from .tree import Tree, unquote

#: The anchor type naming an equation rather than a block. It reaches a claim
#: only through :func:`_equation_claim`, an equation being no node itself.
EQUATION_REFERENCE_TYPE = "eqref"

#: How the three target-end rules are named in the build's own report, so a
#: reader of the log can tell how an edge arose without a marker on the edge.
BY_IDENTIFIER = "identifier"
BY_EQUATION = "equation"
BY_SOLE_CLAIM = "sole-claim"

#: How many times one source is re-asked after an answer that did not parse.
#: Spent under the ask's own refusal and nowhere else.
PARSE_RETRY_BUDGET = 1

#: How many times one source is re-asked after an answer that parsed and failed
#: the membership check. Spent under that check's report and nowhere else, so a
#: malformed first answer cannot consume it. The exit on exhausting either is a
#: stop — not a degraded answer.
CHECK_RETRY_BUDGET = 1

#: The most calls one source claim costs in one :func:`_ask`: the first ask, plus
#: each allowance once. Derived rather than declared, so a seat alternating
#: between the two failure classes cannot walk past the bound.
CALL_BUDGET = 1 + PARSE_RETRY_BUDGET + CHECK_RETRY_BUDGET


class AttributionError(ClaimGraphError):
    """A mechanical check over the returned selection failed twice."""


@dataclass(frozen=True)
class Question:
    """One source claim, the candidates it may depend on, and where they came from.

    ``evidence`` is the reference line each candidate was enumerated from, in
    the source document's own words. It is what the ask shows instead of the
    whole document: the pair is the question, and the line carrying the
    reference is the context that decides it.
    """

    source: ClaimNode
    candidates: tuple[ClaimNode, ...]
    evidence: tuple[str, ...]

    def offered(self) -> frozenset[str]:
        return frozenset(candidate.id for candidate in self.candidates)


class Selector(Protocol):
    """How stage D reaches inference. The seam every test replaces."""

    def select(self, question: Question, *, report: str | None, cycle: str | None = None) -> tuple[str, ...]:
        """The subset of ``question.candidates`` the source depends on.

        ``report`` is ``None`` on the first ask and carries the mechanical
        failure the previous answer produced on the re-ask — never a critique,
        never a previous answer, and never a request to try harder.

        ``cycle`` is the acyclicity re-ask's, and is the cycle as a path. It
        arrives instead of a ``report`` rather than beside one, because what is
        asked under it differs: the selections stand, and one of them has to go.

        Raises :class:`~.report.AnswerFormatError` where an answer arrived and
        did not parse: it is the one failure this seam reports by exception
        rather than in its return, there being no selection to return.
        """


# --- the mechanical narrowing ------------------------------------------------


class _InDocument(Protocol):
    """Every reading stage B produces carries the document it was found in."""

    document: str


_Found = TypeVar("_Found", bound=_InDocument)


def _by_document(items: Sequence[_Found]) -> Mapping[str, tuple[_Found, ...]]:
    """One stage-B reading grouped by document, each group in the scan's own order."""
    grouped: dict[str, list[_Found]] = {}
    for item in items:
        grouped.setdefault(item.document, []).append(item)
    return {path: tuple(found) for path, found in grouped.items()}


def _hosting_block(line: int, blocks: Sequence[Block]) -> Block | None:
    return next((block for block in blocks if block.start <= line < block.end), None)


def _claim_of(block: Block | None, hosted: Sequence[ClaimNode]) -> ClaimNode | None:
    """The claim a block carries, joined the way :func:`graph.read` bound it: by locator."""
    if block is None:
        return None
    return next((node for node in hosted if node.locator == block.display), None)


def _fragment_claim(anchor: Anchor, hosted: Sequence[ClaimNode]) -> ClaimNode | None:
    """The claim the anchor's fragment names, where it names one.

    Point 7 lands a rewritten anchor on the node that held its label, and stage
    B carries that label off the block's ``<span>``. Where the two agree the
    target is not a document but a claim. This is this package's whole reading
    of SPEC.md's cross-reference join (Corpus Invariants): the fragment is asked
    against the identifier the *target* declares, never against the author's own
    spelling, so ``\\label{bifurcation}`` on a theorem is as legal as
    ``\\label{thm:bif}``.
    """
    if not anchor.fragment:
        return None
    return next((node for node in hosted if node.identifier == anchor.fragment), None)


def _equation_claim(
    anchor: Anchor,
    fences: Sequence[MathFence],
    blocks: Sequence[Block],
    hosted: Sequence[ClaimNode],
) -> ClaimNode | None:
    """The claim whose body holds the equation an ``eqref`` names, where one does.

    The label is the anchor's own third attribute rather than its fragment,
    which point 9 leaves empty for an equation (:data:`tree.ANCHOR_RE`). An
    equation labelled outside every claim-bearing block resolves to no claim:
    the reference reaches a result only where the equation *is* one.
    """
    fence = next((found for found in fences if anchor.label in found.labels), None)
    if fence is None:
        return None
    return _claim_of(_hosting_block(fence.start, blocks), hosted)


@dataclass(frozen=True)
class _Proof:
    """One proof block as this stage needs it: the claims it establishes.

    :class:`inventory.Proof` carries the same binding over *blocks*, which is
    what a pass running before the first id is minted can use. This is that
    binding with each subject block replaced by the claim it carries, and it is
    the whole of what this module adds to it.
    """

    subjects: tuple[ClaimNode, ...]
    head: frozenset[tuple[str, str]]

    def names(self, anchor: Anchor) -> bool:
        """Whether ``anchor`` is one of the run's: what is proved, not what it rests on."""
        return (anchor.href, anchor.label) in self.head


def _proofs(graph: AuthoredGraph, inventory: Inventory) -> Mapping[str, Mapping[int, _Proof]]:
    """Stage B's proof binding, by document and by the line each proof's label sits on.

    The subjects are mapped block by block through :func:`_claim_of`, which is
    the join ``graph.read`` itself made — so a subject block no register entry
    carries drops out here and the proof binds to nothing, exactly as one the
    tree never named would.
    """
    found: dict[str, dict[int, _Proof]] = {}
    for proof in inventory.proofs:
        hosted_by_document = {subject.document: graph.hosted_by(subject.document) for subject in proof.subjects}
        subjects = tuple(
            claim
            for subject in proof.subjects
            if (claim := _claim_of(subject, hosted_by_document[subject.document])) is not None
        )
        found.setdefault(proof.document, {})[proof.start] = _Proof(subjects=subjects, head=proof.head)
    return found


def _source_end(
    anchor: Anchor, blocks: Sequence[Block], hosted: Sequence[ClaimNode], proofs: Mapping[int, _Proof]
) -> tuple[tuple[ClaimNode, ...], bool]:
    """The claims the reference may belong to, and whether containment settled it.

    Settled means a *direction* was established, which only a proof does: the
    claim the proof establishes rests on what the proof draws on. A reference
    inside a claim-bearing block, or in the prose of a single-claim document,
    narrows the end to one claim and says nothing about which way the edge runs.
    """
    if (anchor.hosting_environment or "").casefold() == PROOF_ENVIRONMENT:
        block = _hosting_block(anchor.line, blocks)
        proof = proofs.get(block.start) if block is not None else None
        if proof is None or proof.names(anchor):
            return (), False
        return (proof.subjects, True) if proof.subjects else (tuple(hosted), False)
    inside = _claim_of(_hosting_block(anchor.line, [found for found in blocks if found.claim_bearing]), hosted)
    return ((inside,), False) if inside is not None else (tuple(hosted), False)


def _target_end(
    anchor: Anchor, targets: Sequence[ClaimNode], fences: Sequence[MathFence], blocks: Sequence[Block]
) -> tuple[tuple[ClaimNode, ...], str | None]:
    """The claims the reference may name, and the rule that settled it where one did."""
    if anchor.reference_type == EQUATION_REFERENCE_TYPE:
        claim = _equation_claim(anchor, fences, [block for block in blocks if block.claim_bearing], targets)
        return ((claim,), BY_EQUATION) if claim is not None else ((), None)
    named = _fragment_claim(anchor, targets)
    if named is not None:
        return (named,), BY_IDENTIFIER
    if len(targets) == 1:
        return tuple(targets), BY_SOLE_CLAIM
    return tuple(targets), None


def _reference_line(tree: Tree, anchor: Anchor) -> str:
    """The source line the anchor sits on, unquoted and collapsed to one line."""
    lines = unquote(tree.documents[anchor.document].text).splitlines()
    line = lines[anchor.line] if anchor.line < len(lines) else ""
    return re.sub(r"\s+", " ", line).strip()


def cycle_edges(edges: Sequence[tuple[str, str]]) -> tuple[tuple[str, str], ...]:
    """Every edge lying on a cycle, sorted. Removing them all leaves an acyclic set.

    An edge is on a cycle exactly when its target reaches its source again, so
    this is a property of the edge set alone — no tie-break key, no iteration
    order, and two runs over one corpus name the same edges. What remains is the
    condensation of the graph, which is acyclic by construction rather than by a
    check afterward.
    """
    following: dict[str, set[str]] = {}
    for source, target in edges:
        following.setdefault(source, set()).add(target)

    def reaches(start: str) -> set[str]:
        reached: set[str] = set()
        pending = [start]
        while pending:
            for node in following.get(pending.pop(), ()):
                if node not in reached:
                    reached.add(node)
                    pending.append(node)
        return reached

    onward = {target: reaches(target) for _, target in edges}
    return tuple(sorted({(source, target) for source, target in edges if source in onward[target]}))


@dataclass(frozen=True)
class Attribution:
    """What the narrowing produced: the edges it settled and the pairs it did not.

    ``routes`` counts the settled edges by the target-end rule that resolved
    each, which is what the build's own log carries in place of a marker on the
    edge: an edge authored here is an ordinary ``depends`` edge and nothing in
    the KB distinguishes it from one a person wrote. ``demoted`` is out of it,
    those edges no longer being dependencies.

    ``references`` is every open pair, as a ``references`` edge — the same pairs
    ``questions`` enumerates, recorded rather than discarded. The two are not
    alternatives: a pair is offered to the model *and* recorded as a reference,
    because what the corpus states is that the source names the target, and
    whether that naming is also a dependency is the question. A pair the model
    answers yes to is upgraded by the caller (:func:`attribute_dependencies`'s
    return, differenced against this) rather than recorded twice.

    ``demoted`` is the settled edges a ring took the direction off, and they are
    in ``references`` too — carried separately only so the report can name them,
    a demoted edge being indistinguishable from any other reference once it is
    written.
    """

    edges: tuple[tuple[str, str], ...]
    questions: tuple[Question, ...]
    routes: Mapping[str, int]
    references: tuple[tuple[str, str], ...] = ()
    demoted: tuple[tuple[str, str], ...] = ()


def narrow(tree: Tree, graph: AuthoredGraph, inventory: Inventory) -> Attribution:
    """Stage B's anchors, split into the edges containment settles and the pairs left open.

    Deterministic and total: nothing is sampled, nothing is dropped silently,
    and a source that ends with no candidate is absent rather than asked an
    empty question. A pair the narrowing settled is never also asked about,
    a ring's demoted edges included — what the ring refuses is the set, and
    containment's reading of each member stands as the reference it becomes.
    """
    blocks = _by_document(inventory.blocks)
    fences = _by_document(inventory.fences)
    proofs = _proofs(graph, inventory)

    settled: dict[tuple[str, str], str] = {}
    pairs: dict[str, dict[str, str]] = {}

    for anchor in inventory.anchors:
        if anchor.target is None:
            continue
        targets = graph.hosted_by(anchor.target)
        if not targets:
            continue

        to_ends, route = _target_end(anchor, targets, fences.get(anchor.target, ()), blocks.get(anchor.target, ()))
        if not to_ends:
            continue
        from_ends, directed = _source_end(
            anchor,
            blocks.get(anchor.document, ()),
            graph.hosted_by(anchor.document),
            proofs.get(anchor.document, {}),
        )

        for source in from_ends:
            for target in to_ends:
                if source.id == target.id:
                    continue
                if directed and route is not None:
                    settled.setdefault((source.id, target.id), route)
                else:
                    pairs.setdefault(source.id, {}).setdefault(target.id, _reference_line(tree, anchor))

    demoted = cycle_edges(sorted(settled))
    on_ring = set(demoted)

    routes: dict[str, int] = {}
    for pair, name in settled.items():
        if pair not in on_ring:
            routes[name] = routes.get(name, 0) + 1

    asked: list[Question] = []
    referenced: list[tuple[str, str]] = []
    for source_id, enumerated in sorted(pairs.items()):
        # A pair another anchor already settled is decided; asking about it
        # would offer a candidate whose answer changes nothing.
        open_targets = {target: line for target, line in enumerated.items() if (source_id, target) not in settled}
        if not open_targets:
            continue
        # The pair is recorded as a reference whether or not anybody is asked
        # about it. The author wrote one claim's identifier inside another's
        # text, in formal notation; that the narrowing cannot direct it does not
        # make it nothing, and the alternative to recording it is recording that
        # the two claims are unrelated.
        referenced.extend((source_id, target_id) for target_id in sorted(open_targets))
        asked.append(
            Question(
                source=graph.nodes[source_id],
                candidates=tuple(graph.nodes[target_id] for target_id in sorted(open_targets)),
                evidence=tuple(sorted(set(open_targets.values()))),
            )
        )
    return Attribution(
        edges=tuple(sorted(pair for pair in settled if pair not in on_ring)),
        questions=tuple(asked),
        routes=routes,
        references=tuple(referenced) + demoted,
        demoted=demoted,
    )


def references_beyond(narrowed: Attribution, edges: Sequence[tuple[str, str]]) -> tuple[tuple[str, str], ...]:
    """The narrowing's references, less every pair that became a dependency.

    A pair the selection returned is a dependency, and a dependency is already
    the stronger statement that the source names the target — recording both
    would put two records on one drawn stroke and report a relation conflict
    about a graph that holds none. Where no model was asked, nothing is
    subtracted and every open pair stands as a reference.
    """
    directed = set(edges)
    return tuple(pair for pair in narrowed.references if pair not in directed)


# --- the two mechanical checks ----------------------------------------------


def check_membership(returned: Sequence[str], question: Question) -> tuple[str, ...]:
    """The ids that were not offered. Set membership; no parsing."""
    offered = question.offered()
    return tuple(dict.fromkeys(node_id for node_id in returned if node_id not in offered))


def _synthetic_entries(graph: AuthoredGraph, edges: Sequence[tuple[str, str]]) -> list[kb_index_lib.ClaimEntry]:
    """The authored claims as the solidity computation reads them, with the edge set applied."""
    outgoing: dict[str, list[kb_index_lib.DependsOnEdge]] = {}
    for source, target in edges:
        outgoing.setdefault(source, []).append(
            kb_index_lib.DependsOnEdge(
                source=source,
                target=target,
                relation="depends",
                target_kind="claim",
                target_solidity_recorded=None,
                strength=None,
                context=None,
            )
        )
    return [
        kb_index_lib.ClaimEntry(
            id=node.id,
            title=node.title,
            canonical_path=node.document,
            canonical_anchor="",
            confidence=None,
            solidity=None,
            build_status=None,
            rationale="",
            depends_on=tuple(outgoing.get(node.id, ())),
            strengthen_by=(),
        )
        for node in graph.nodes.values()
    ]


def _cycle_path(members: Sequence[str], edges: Sequence[tuple[str, str]]) -> tuple[str, ...]:
    """One concrete cycle through ``members``, as a path the re-ask can be shown.

    The solidity computation reports which claims are *in* a cycle; a report
    naming a set is not something a re-ask can act on, and a path is. Falls back
    to the member set only if no walk closes, which the computation's own
    verdict says cannot happen — so the fallback is a report that degrades
    rather than a claim that a cycle was not found.
    """
    within = set(members)
    following: dict[str, list[str]] = {}
    for source, target in edges:
        if source in within and target in within:
            following.setdefault(source, []).append(target)

    path: list[str] = []
    on_path: set[str] = set()
    visited: set[str] = set()

    def walk(node: str) -> tuple[str, ...]:
        path.append(node)
        on_path.add(node)
        visited.add(node)
        for following_node in sorted(following.get(node, ())):
            if following_node in on_path:
                return tuple(path[path.index(following_node) :]) + (following_node,)
            if following_node not in visited:
                closed = walk(following_node)
                if closed:
                    return closed
        path.pop()
        on_path.discard(node)
        return ()

    for start in sorted(within):
        if start not in visited:
            closed = walk(start)
            if closed:
                return closed
    return tuple(sorted(within))


def check_acyclic(graph: AuthoredGraph, edges: Sequence[tuple[str, str]]) -> tuple[str, ...]:
    """``()`` when the edge set is acyclic, or the cycle as a concrete path.

    The same call ``refresh`` and ``verify`` make afterward, run before anything
    is written.
    """
    try:
        kb_index_lib.compute_solidity(_synthetic_entries(graph, edges))
    except kb_index_lib.SolidityCycleError as cycle:
        return _cycle_path(cycle.cycle_members, edges)
    return ()


# --- the stage ---------------------------------------------------------------


def _ask(selector: Selector, question: Question, *, report: str | None, cycle: str | None = None) -> tuple[str, ...]:
    """One source's selection, with the membership check and one re-ask per failure class.

    An answer that arrived malformed is re-asked against the parse refusal's own
    message and costs :data:`PARSE_RETRY_BUDGET`; an answer that parsed and named
    an id outside the candidate set is re-asked against the offending ids and
    costs :data:`CHECK_RETRY_BUDGET`. Exhausting either stops the stage here
    rather than under the ask, and however the two alternate this ask costs
    :data:`CALL_BUDGET` calls at most.

    ``cycle`` opens the acyclicity re-ask and is spent on its first call alone:
    a malformed answer to it is re-asked under the refusal, which is the ordinary
    ask again, because the constraint the cycle states is not what that answer
    failed.
    """
    parse_retries = PARSE_RETRY_BUDGET
    check_retries = CHECK_RETRY_BUDGET
    for _ in range(CALL_BUDGET):
        try:
            returned = selector.select(question, report=report, cycle=cycle)
        except AnswerFormatError as refusal:
            if not parse_retries:
                raise AttributionError(
                    refusal.check,
                    f"{question.source.id}: what came back did not parse twice: {refusal.detail}. Nothing "
                    f"was written, and no selection is assumed for an answer that could not be read",
                ) from refusal
            parse_retries -= 1
            report, cycle = refusal.detail, None
            continue
        outside = check_membership(returned, question)
        if not outside:
            return tuple(dict.fromkeys(returned))
        if not check_retries:
            raise AttributionError(
                "candidate-membership",
                f"{question.source.id}: {len(outside)} returned id(s) were not in the candidate set this "
                f"claim was given, twice: {list(outside)[:5]}. The set was {sorted(question.offered())}",
            )
        check_retries -= 1
        report, cycle = (
            f"{len(outside)} of the ids you returned were not in the candidate set: {list(outside)}. "
            f"The candidate set is exactly {sorted(question.offered())}. Return a subset of it."
        ), None
    raise AssertionError("unreachable: the last call finds both allowances spent, and returns or raises")


def attribute_dependencies(
    tree: Tree, graph: AuthoredGraph, inventory: Inventory, selector: Selector | None
) -> tuple[tuple[str, str], ...]:
    """The authored dependency edges, as ``(source, target)`` claim-id pairs.

    Every pair is either one containment settled or one the selector was offered
    and returned, and the whole set is acyclic by the time this returns. A cycle
    among the *selected* edges costs one re-ask of the claims on it, and a second
    cycle stops the stage. A cycle among the settled edges costs neither: the
    narrowing has already demoted it to ``references``, so the check over those
    edges alone is the post-condition of that demotion rather than a stop with a
    corpus finding behind it.

    **``selector`` is ``None`` where no model is reachable**, and what that
    changes is the asking and nothing else: :func:`narrow` is a pure function of
    the tree, the authored graph and the inventory, so the edges containment
    settles are the same edges either way and are recorded either way. The open
    pairs stay open and unrecorded — a pair is open exactly because containment
    did *not* decide it, and a build that guessed at the uncertain half would be
    worse than one that dropped the certain half.
    """
    narrowed = narrow(tree, graph, inventory)

    mechanical = check_acyclic(graph, narrowed.edges)
    if mechanical:
        raise AttributionError(
            "mechanical-acyclicity",
            f"the edges containment settles still close a cycle after demotion: {' -> '.join(mechanical)}. "
            f"Every edge on a ring is demoted to a reference before this runs, so a cycle surviving here is "
            f"a defect in that demotion and not a finding about the corpus; nothing was written",
        )
    if selector is None:
        return narrowed.edges

    selected = {question.source.id: _ask(selector, question, report=None) for question in narrowed.questions}

    def edges_of(chosen: Mapping[str, tuple[str, ...]]) -> tuple[tuple[str, str], ...]:
        asked = ((source, target) for source in chosen for target in chosen[source])
        return tuple(sorted(set(narrowed.edges) | set(asked)))

    cycle = check_acyclic(graph, edges_of(selected))
    if not cycle:
        return edges_of(selected)

    path = " -> ".join(cycle)
    on_cycle = set(cycle)
    for question in narrowed.questions:
        if question.source.id not in on_cycle:
            continue
        selected[question.source.id] = _ask(selector, question, report=None, cycle=path)

    again = check_acyclic(graph, edges_of(selected))
    if again:
        raise AttributionError(
            "acyclicity",
            f"the authored edge set still closes a cycle after one re-ask: {' -> '.join(again)}. Nothing was "
            f"written; solidity is undefined for the members of a dependency cycle",
        )
    return edges_of(selected)
