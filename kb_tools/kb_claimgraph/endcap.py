"""The off-graph endcap — the works a claim rests on and this corpus does not hold.

A claim that depends on nothing and a claim that depends on unreachable external
work are the same thing in an edge count: zero. They are not the same epistemic
state, and the second is the ordinary one — every ``\\cite`` inside a claim block
is an instance. This stage is what makes the difference visible: a claim resting
on outside work says so, by an edge to a node standing for the work.

**The node is the cited work, and its identity is the citation key.** The key is
what a citation carries, and one key is one work everywhere in the corpus — a
build resolves every citation against one merged collection, whatever number of
``.bib`` files it was assembled from, so a key names one entry however many times
and in however many volumes it is cited. That is what makes the id corpus-unique
without a minting scheme; :func:`kb_schema.work_id` derives it from the key. The
node is therefore tied to the bibliography and **not** to the per-volume
``references.md`` leaves: a work three volumes cite is one node, because its
standing is a property of the work. Those leaves are *citers* of it, which is
what they already are in prose.

**The trigger is mechanical and asks nobody anything.** It is a comparison of
:attr:`inventory.Citation.line` against a run of lines the claim owns — both
already in stage B's inventory, both read off the rendered page. Nothing here is
inferred, and nothing here may be: a *confident* number about a paper no model in
this build has read is worse than a blank, so neither score this stage authors is
given a default, a heuristic or a fallback. The node's standing and the pairing's
applicability both land as the pending literal and stay there until a person
replaces them.

**Two runs of lines are a claim's, and the second is where the warrants are.**
The first is the block itself — and in the surveyed corpus that population is
almost entirely the theorem environment's *optional argument*, an author writing
``\\begin{lemma}[Yamabe stability, Engelstein–Neumayer–Spolaor \\cite{ENS22}]``,
which attributes the whole statement. The second is **the body of the proof that
establishes it**. A proof establishes the claim it belongs to, so what the proof
leans on the claim leans on — the ruling stage D already directs a ``\\ref`` by
(:mod:`attribute`) — and a ``\\cite`` is the same relation reaching outside the
corpus instead of inside it. Measured over 25 built arXiv KBs: 8 citations sit
inside claim blocks and 68 inside proofs, so reading only the first is reading
the attribution and not the warrant. *"By the Sobolev–Morrey embedding theorem
(Adams and Fournier 2003, Theorem 4.12)"* is what the second population looks
like.

**Which claim a proof proves is not decided here.** It is
:attr:`inventory.Inventory.proofs`, the same reading stage D directs a ``\\ref``
by; this module joins citations to spans and never asks what a proof is. A second
derivation of what a proof proves could disagree with that one about the corpus,
and there is no third artifact to break the tie.

**A key survives with no bibliography behind it.** ``data-cites`` carries the key
whatever became of it, so a key-only citation still yields a node: *this claim
rests on ``nobody2026``, and we know nothing else about it* is a fact worth
recording and is what the corpus itself says. Where a bibliography did answer,
the reference list's own rendered text becomes the node's title, so a resolved
work is named by its author, year and title and an unanswered or key-only one by
the key alone. That difference is what the node carries, and its rationale says
which of the two it is.
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field

from .. import kb_schema
from .inventory import Inventory

#: A claim site's identity before an id exists for it: the document hosting it
#: and its own display line. The same pair
#: ``identify.check_block_coverage`` states block coverage over, so a pairing
#: cannot be keyed to a site that check does not recognise.
ClaimSite = tuple[str, str]


@dataclass(frozen=True)
class ClaimSpan:
    """A run of lines belonging to one claim, and the site an edge from it carries.

    **The two are held apart because they differ.** A claim block's lines are its
    own; a proof's lines belong to the claim the proof establishes, and that
    claim may sit in another document — the author wrote
    ``\\begin{proof}[Proof of Theorem \\ref{thm:main}]`` and the reference
    resolved elsewhere. So containment is asked of :attr:`document`,
    :attr:`start` and :attr:`end`, and the pairing is recorded under
    :attr:`site`, which names the claim rather than the lines.

    ``start`` and ``end`` are 0-based and half-open, :attr:`inventory.Block.start`
    and :attr:`inventory.Block.end`'s own vocabulary.
    """

    document: str
    start: int
    end: int
    site: ClaimSite


@dataclass(frozen=True)
class CitedWork:
    """One external work a claim in this corpus rests on.

    ``title`` is the reference list's rendered text where a bibliography
    answered the key, and the key itself where none did. ``named`` says which of
    those two happened — it is the whole of what a build knows about a work, and
    the node's rationale states it rather than leaving a reader to infer it from
    a title that happens to look like a key.
    """

    key: str
    title: str
    named: bool

    @property
    def id(self) -> str:
        return kb_schema.work_id(self.key)


@dataclass(frozen=True)
class Endcap:
    """Every external work a claim of this corpus rests on, and by which claims."""

    works: tuple[CitedWork, ...] = ()
    #: One entry per claim site that cites at least one work; the work ids it
    #: rests on, deduplicated and in key order. A site citing one work twice
    #: rests on it once — a dependency is a relation, not a tally, and a claim
    #: whose block and whose proof both cite a work rests on it once for the
    #: same reason.
    pairings: Mapping[ClaimSite, tuple[str, ...]] = field(default_factory=dict)


def claim_spans(inventory: Inventory) -> tuple[ClaimSpan, ...]:
    """Every run of lines a claim owns: its own block, and the body of its proof.

    A claim-bearing block whose display line yielded no locator is left out, and
    so is a proof binding to no block — stage B refuses the first for a
    claim-bearing block, and the second is its documented and ordinary outcome.
    """
    spans = [
        ClaimSpan(document=block.document, start=block.start, end=block.end, site=(block.document, block.display))
        for block in inventory.claim_blocks()
        if block.display is not None
    ]
    spans += [
        ClaimSpan(document=proof.document, start=proof.start, end=proof.end, site=(subject.document, subject.display))
        for proof in inventory.proofs
        for subject in proof.subjects
        if subject.display is not None
    ]
    return tuple(spans)


def _hosting_spans(spans: Sequence[ClaimSpan], line: int) -> tuple[ClaimSpan, ...]:
    """Every claim span ``line`` falls inside.

    Blocks do not nest — point 12's labelled blockquote is one quoted run — so a
    line lies in at most one block and at most one proof. It can lie in both
    only where a proof is its own subject, which no binding produces; what it
    *can* do is lie in one proof that names several subjects, and then the
    citation is a warrant for each of them.
    """
    return tuple(span for span in spans if span.start <= line < span.end)


def scan(inventory: Inventory) -> Endcap:
    """Join stage B's citations against the spans its claims own. No new reading."""
    spans_by_document: dict[str, list[ClaimSpan]] = {}
    for span in claim_spans(inventory):
        spans_by_document.setdefault(span.document, []).append(span)

    rendered = {work.key: work.text for work in inventory.works}

    cited: dict[str, CitedWork] = {}
    pairings: dict[ClaimSite, set[str]] = {}
    for citation in inventory.citations:
        hosting = _hosting_spans(spans_by_document.get(citation.document, ()), citation.line)
        if not hosting:
            continue
        text = rendered.get(citation.key)
        cited.setdefault(
            citation.key,
            CitedWork(key=citation.key, title=text or citation.key, named=text is not None),
        )
        for span in hosting:
            pairings.setdefault(span.site, set()).add(kb_schema.work_id(citation.key))

    return Endcap(
        works=tuple(cited[key] for key in sorted(cited)),
        pairings={site: tuple(sorted(ids)) for site, ids in sorted(pairings.items())},
    )


def rationale(work: CitedWork) -> str:
    """The node's rationale: what this build knows about the work, and no more.

    It does not say *where* the claim cited it. One node stands for a work
    however many claims rest on it, and those claims may cite it from their own
    statements and from their proofs alike, so a sentence naming one of those
    places would be false of the others as soon as a second claim arrived.
    """
    if work.named:
        return (
            f"Cited by a claim of this corpus as {work.key}, and resolved against the corpus's own "
            f"bibliography to the reference-list entry this entry is titled with. The work is outside "
            f"the corpus, so nothing here derives from it and its standing is unassessed."
        )
    return (
        f"Cited by a claim of this corpus as {work.key}, which is the whole of what the corpus says "
        f"about it: no bibliography answered the key, so this entry is titled with the key itself. The "
        f"work is outside the corpus and its standing is unassessed."
    )
