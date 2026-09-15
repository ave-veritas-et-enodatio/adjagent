"""Stage C — claim identification, split by whether the author marked the site.

**C-mech — block-hosted claims. Mechanical, with no inference at all.** For each
claim-bearing block stage B found, everything a register entry needs is already
on the page: the **title** is the display line's optional argument where the
author wrote one and the environment's own printed word and number where they
did not, the **locator** is the span carrying it, and the **host** is the file.
The first two come off the same span, which is why it is worth naming — a
block's title and the anchor that locates it cannot disagree, so the affordance
does not reduce inference here, it eliminates it.

**C-inf — documents the author marked nothing in. Inferential.** One ask per
document, for the results that document states — each as a self-contained block
carrying the **quote** the result begins at, in the document's own words, the
**label** of that sentence in the render (:mod:`label`) the ask shows, and a
**title** the seat authors — or for a reason it states none. The ask is put
through :mod:`ask`'s injected seam; everything below the ask in this module is
comparison against the document itself.

**The model quotes; the tool addresses.** A quote is a lookup key and never
content: it is resolved against the document by
:func:`~kb_tools.kb_write.ops.resolve_excerpt`, and then it is discarded and the
tool cuts the bytes — so no model-typed byte reaches the KB. What the quote buys
over the label it replaced is that the address is now **verified**: a label
named alone is a pointer, and a pointer one sentence out is a well-formed record
aimed at the wrong span that no downstream check can tell from a right one. The
label survives as the **cross-check** that catches exactly that divergence.

**Extent is mechanical, and so is the locator, and they are not the same
thing.** A claim runs from its resolved start sentence to the next resolved
start in the same paragraph, or to the paragraph's end, whichever comes first —
so starts partition a paragraph and extents never nest. The locator handed to
``mark-claim-in-leaf`` must additionally be *unique in the document*, and
:func:`_unique_slice` widening within the paragraph is what buys that; it is
findability alone that it serves, never a range anybody named.

**Its checks are weak by construction, and the weakness is specific.**
*Verbatimness is obviated rather than weakened*: the bytes a record points at
are the tool's own slice of the document, so the author's words are its words by
construction and there is nothing left for a comparison to establish.
**Uniqueness is not obviated.** A tool-cut slice is unique by nothing, and the
seat has no instrument for making its own quotation unique — so where the slice
repeats, the tool widens it one sentence at a time within its paragraph, and the
record fails only when the whole paragraph is still not unique. Widening costs no
ask.

Nothing here bounds fabrication of *claimhood* — there is no mechanical check
that a passage states a result, and inventing one would be inventing a reviewer.
So the ask biases toward recording: a wrong claim is a register entry anchored at
a span a reader can go and compare, and a missed claim is invisible to every
gate, count and roll-up in this build. Nothing bounds how many records one
document may yield.

**A failure costs the claim it belongs to.** A quote that will not settle on one
sentence is re-asked **on its own** (:class:`Unresolved`), carrying the
sentences the tool actually found and an explicit "none of these"; isolating the
ask is what raises the odds on it, and the population is its own measurement
(:class:`Telemetry`). A block that resolves and still cannot be written — a
title that is not one line, two blocks resolving to one sentence, a slice no
widening makes unique — costs that block and is not re-asked, there being no
menu that would answer it. The document keeps every other claim either way.

**The whole-document re-ask is what is left, and it has one allowance**
(:data:`ANSWER_RETRY_BUDGET`): an answer carrying no readable block at all, and
an answer whose *reason* is the whole answer and fails its checks. A second
failure of the first kind stops the stage naming the document; a second failure
of the second kind substitutes :data:`SUBSTITUTED_NO_CLAIM_REASON` and the run
continues, because a missing sentence loses prose nobody had. Only a call that
did not *complete* escapes both, there being no answer to ask again about
(:mod:`ask`).

**A document whose blocks all fail is not a document that states nothing.** It
takes :data:`UNANCHORED_REASON` — the fifth :class:`~.conform.Determination` —
which records that the anchoring failed and never that there was nothing to
anchor. Recording the latter on the strength of a model failing to point into a
document writes a falsehood into the graph, and that ruling is preserved rather
than overturned.
"""

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol, TypeAlias

from .. import kb_index_lib
from ..kb_write import ops, render, store
from . import label
from .inventory import Block, Inventory, MathFence
from .report import AnswerFormatError, ClaimGraphError
from .tree import DECLARING_KINDS, Document, Tree, document_kind, strip_markers

#: How many times one **document** is asked again. Spent on the two failures
#: that are the whole answer's rather than one claim's: an answer carrying no
#: readable block at all, and an answer whose reason is the answer and fails its
#: checks. Nothing else reaches it — a claim that will not anchor has a re-ask of
#: its own, and a claim that anchors and cannot be written has none.
ANSWER_RETRY_BUDGET = 1

#: The most **per-claim** re-asks one document costs. One re-ask per unresolved
#: claim, under this ceiling; when it trips, every still-unresolved claim is
#: recorded unresolved and the two compose with no special case.
#:
#: **A policy number, not a measured one.** A live run returned nine to thirteen
#: claims per document, so this admits a document recovering several bad anchors
#: without letting one document become twenty calls. :class:`Telemetry` is what
#: would move it: if re-asks routinely succeed, the evidence says ask per claim
#: from the start.
REASK_CEILING = 4

#: The most calls one document costs: the first ask, plus each allowance once.
#: Derived rather than declared, so no arrangement of failures walks past it.
CALL_BUDGET = 1 + ANSWER_RETRY_BUDGET + REASK_CEILING

#: What a document takes when the ask returned no record and no reason this
#: stage could carry. It states what happened rather than a finding about the
#: document, and it is deliberately not the reason an awaiting document already
#: carries: a document handed its own standing reason back still reads
#: ``AWAITING``, and every re-run would reopen it forever.
SUBSTITUTED_NO_CLAIM_REASON = (
    "Claim identification read this document's prose and recorded no result; the reason it returned could "
    "not be carried, so this sentence stands in its place."
)

#: What a document takes when identification named results and **none of them
#: could be anchored**. It is the fifth :class:`~.conform.Determination`, and it
#: is not :attr:`~.conform.Determination.AUTHORED_NO_CLAIM`: this records that
#: we could not anchor what the document stated, never that it stated nothing.
#:
#: **Compared by identity, and that literal is load-bearing**, on
#: :data:`assemble.UNSCANNED_REASON`'s terms and for its reasons — reword it
#: without moving :func:`conform.determination` with it and a failed document
#: reads as somebody's finding. It lives here rather than beside that one
#: because :mod:`assemble` imports from this module and the reverse would close
#: a cycle; it has no consumer outside this package, which is the property that
#: kept the other literal in ``kb_index_lib``.
UNANCHORED_REASON = (
    "Claim identification read this document's prose, named results in it, and could not anchor any of them "
    "in the document's own words. This records that the anchoring failed and not that the document states "
    "no result; it is a finding about this run, and nobody has ruled on what this document states."
)

#: The bytes a Tier-2 marker occupies while C4 asks what placing one would do to
#: the excerpts not yet marked. The id is a placeholder: ids are minted by the
#: write itself, a stage after this check, and what the question turns on is
#: where a marker lands and that it is text — never which id it names.
_PLACEHOLDER_MARKER = render.render_tier2_marker("clm-xxxxxx")


def _with_marker(text: str, line: int) -> str:
    """``text`` as ``mark-claim-in-leaf`` leaves it once one marker is placed at ``line``.

    Where the marker lands is the whole of what this models — appended to the
    end of the located line, which is what ``ops._insert_marker`` does — because
    the only question asked over the result is :func:`ops.excerpt_lines`', and
    that matches over whitespace-collapsed text: the op's care to keep the
    line's own trailing spaces behind the marker cannot change what matches.
    """
    return store.splice_lines(
        text, start=line, end=line + 1, lines=[f"{text.splitlines()[line]} {_PLACEHOLDER_MARKER}"]
    )


@dataclass(frozen=True)
class Claim:
    """One identified claim, before an id exists for it.

    ``locator`` is the Tier-2 anchor a multi-claim host will carry — the claim's
    own display line, apt by construction rather than by selection.
    ``identifier`` is the source ``\\label`` where the block carried one; it is
    the corpus's own name for the site and is carried for a later stage's
    candidate resolution, never as a node id.
    """

    document: str
    title: str
    locator: str
    environment: str
    identifier: str | None


class CoverageError(ClaimGraphError):
    """A claim-bearing block was dropped or double-counted."""


def block_claims(inventory: Inventory) -> tuple[Claim, ...]:
    """C-mech: one claim per claim-bearing block, read off the display line.

    A block's title and display line are optional on the block record and
    required here, and the narrowing is safe because a block that yielded
    neither is not claim-bearing and so is not in ``claim_blocks()``: there is no
    title to author and no span to anchor, so it costs itself a claim and this
    pass never meets it. Where the author declared no optional argument the title
    is the environment's printed word and its number, which is what the line
    carries.
    """
    return tuple(
        Claim(
            document=block.document,
            title=block.title,
            locator=block.display,
            environment=block.environment,
            identifier=block.identifier,
        )
        for block in inventory.claim_blocks()
    )


def check_block_coverage(claims: Sequence[Claim], inventory: Inventory) -> None:
    """Every claim-bearing block is named by exactly one claim. A comparison, not a judgement.

    The block set and the claim set are two artifacts, and this compares them —
    a block may not be dropped and may not be double-counted. It is stated over
    ``(document, locator)`` because that pair is what a register entry will
    carry, so the check is over the identity the write actually lands.
    """
    blocks = [(block.document, block.display) for block in inventory.claim_blocks()]
    named = [(claim.document, claim.locator) for claim in claims]
    dropped = sorted(set(blocks) - set(named))
    doubled = sorted({pair for pair in named if named.count(pair) > 1})
    if dropped or doubled:
        raise CoverageError(
            "block-coverage",
            f"{len(dropped)} claim-bearing block(s) named by no claim and {len(doubled)} named twice: "
            f"dropped {dropped[:3]}, doubled {doubled[:3]}",
        )


def unmarked_documents(tree: Tree, inventory: Inventory) -> tuple[str, ...]:
    """Every leaf the author marked nothing in, as ``stage-C-identify`` counts them.

    Mechanical: the complement of stage B's hosting set, narrowed to the one
    kind a claim declaration is asked of. An index is asked for no declaration
    by anything, so a stage reading one for claims would have nowhere to put
    them. This is a report line and not C-inf's ask surface, which is
    :func:`conform.pass_two_gate`.
    """
    hosting = inventory.hosting_documents()
    return tuple(
        path
        for path in sorted(tree.documents)
        if path not in hosting and document_kind(path, has_children=bool(tree.children[path])) in DECLARING_KINDS
    )


# --- C-inf: the ask's subject, its answer, and the checks over it ------------


@dataclass(frozen=True)
class Reading:
    """One awaiting document, as C-inf reads it.

    ``text`` is the document as it sits on disk — the bytes
    ``mark-claim-in-leaf`` will match a locator against — so the pre-check and
    the op it pre-checks see the same haystack. ``body`` is what the ask is
    shown: that same text with the markers an earlier pass appended taken back
    off, since those are not the author's words and are not there to be quoted.
    Both carry the same line count, so a fence extent names the same line in
    either.

    **These are the bytes before this stage's own markers land, and re-checking
    afterwards must strip them.** A marker goes on the end of the *first* line
    of the span its excerpt covers, so where that span crosses a hard wrap the
    marker sits inside it and the excerpt no longer matches the written
    document. :func:`tree.strip_markers` restores the bytes the check was made
    against and leaves the line count intact.
    """

    document: str
    text: str
    body: str
    #: The ``no-claim:`` reason this document carries right now — the sentence
    #: that makes it awaiting. It is read off the document rather than compared
    #: against a constant because the question C4 asks is whether the answer
    #: says anything the document does not already say, and handing this
    #: sentence back would leave the document awaiting forever.
    standing_reason: str = ""
    fences: tuple[MathFence, ...] = ()
    #: The claim-bearing blocks in this document. Empty for every document in
    #: C-inf's scope, and carried so that the exclusion is asserted rather than
    #: assumed.
    blocks: tuple[Block, ...] = ()

    @property
    def render(self) -> label.Render:
        """The labelled render the ask shows and the checks resolve against.

        Derived rather than stored, so there is one statement of what a label
        means: the prompt the seat reads and the mapping the checks resolve
        through are the same computation over the same bytes, and neither can go
        stale against the other.
        """
        return label.render(self.body, fences=self.fences)


@dataclass(frozen=True)
class Record:
    """One result the ask reports: where it begins, which sentence that is, and what it says.

    ``quote`` is the opening of the sentence the result begins at, in the
    document's **own** words — a lookup key and never content, discarded the
    moment it resolves. ``label`` is the cross-check rather than the address:
    resolution runs off the quote, and the label breaks ties between several
    hits and catches a hit that is not the sentence the seat meant. ``title`` is
    the one thing here the seat composes.
    """

    quote: str
    label: str
    title: str


@dataclass(frozen=True)
class Answer:
    """What one ask returned. Exactly one of claims and a reason is an answer — a check, not a grammar.

    ``refusals`` are the blocks the parse could not read. They are carried
    rather than raised because a malformed block costs that block and nothing
    else: the blocks beside it are still an answer.
    """

    claims: tuple[Record, ...] = ()
    no_claim: str = ""
    refusals: tuple[str, ...] = ()


class Trigger(StrEnum):
    """Why one claim block would not anchor, and so what its re-ask must show.

    Each member is a state of the resolution and never a severity. What
    separates them is which fact the seat needs and does not have, and every one
    of those facts is computed *after* the answer arrived — which is what makes
    the re-ask a re-ask rather than the question asked again.
    """

    #: The quote resolves nowhere. Show the window the search came nearest to,
    #: with its label, beside the labelled neighbourhood of the claimed label.
    NOWHERE = "nowhere"
    #: The quote resolves in several places and the label names none of them.
    #: Show the colliding candidates with their labels.
    SEVERAL = "several"
    #: The quote resolves, and the sentence it resolved to is not the labelled
    #: one. Show what stands at the claimed label beside what the quote matched.
    #: **Raised whichever tier matched**: near-miss auto-accept is about the
    #: label and not the tier, and a folded match with a disagreeing label is
    #: the population folding exists to create.
    DISAGREED = "disagreed"


@dataclass(frozen=True)
class Unresolved:
    """One claim block the tool could not anchor, with the evidence its re-ask carries."""

    record: Record
    trigger: Trigger
    #: The sentences the re-ask offers, each as the render shows it —
    #: ``S12: <the sentence>``. The menu is never forced: an answer of "none of
    #: these" ends this claim unresolved rather than spending another call.
    candidates: tuple[str, ...] = ()


@dataclass(frozen=True)
class Telemetry:
    """What one document's identification cost and what it bought.

    The datapoint the per-claim cadence exists to collect: if first-ask
    resolution is routinely poor and per-claim re-asks routinely succeed, that
    is the evidence for asking per claim from the start, and collecting it costs
    nothing.
    """

    returned: int = 0
    #: Anchored off the first ask, before any re-ask was spent.
    resolved: int = 0
    #: Of those, the ones a folded match found. Strict always wins where it is
    #: available, so this counts resolutions strict would have missed outright.
    folded: int = 0
    reasked: int = 0
    resolved_on_reask: int = 0
    unresolved: int = 0

    def line(self) -> str:
        return (
            f"{self.returned} block(s) returned, {self.resolved} anchored on the first ask ({self.folded} of "
            f"them by folded match), {self.reasked} re-asked and {self.resolved_on_reask} anchored, "
            f"{self.unresolved} recorded unresolved"
        )


class Identifier(Protocol):
    """How C-inf reaches inference. The seam every check over this stage replaces."""

    def identify(self, reading: Reading, *, report: str | None) -> Answer:
        """The results ``reading`` states, or the reason it states none.

        ``report`` is ``None`` on the first ask and carries the mechanical
        failure the previous answer produced on the re-ask — never a critique,
        never a request to try harder.

        Raises :class:`~.report.AnswerFormatError` where an answer arrived and
        could not be read at all: a block left unclosed, or a returned text
        carrying no block of either kind. A block that is merely malformed
        arrives in :attr:`Answer.refusals`.
        """

    def re_ask(self, reading: Reading, unresolved: Unresolved) -> Record | None:
        """The one block a re-ask about ``unresolved`` settled on, or ``None`` for "none of these".

        ``None`` is an answer: it ends that claim unresolved without a further
        call. Raises :class:`~.report.AnswerFormatError` on anything that is
        neither.
        """


#: One claim's re-ask, bound to the document it belongs to. What the checks take
#: rather than a whole :class:`Identifier`, so a check run over an answer as it
#: stands supplies ``None`` and spends nothing.
ReAsk: TypeAlias = Callable[[Unresolved], Record | None]


@dataclass(frozen=True)
class ProseClaim:
    """One identified claim that located, before an id exists for it.

    ``excerpt`` is both the evidence and the Tier-2 locator: it is the **tool's
    own slice** of the document, cut at the resolved extent and widened where it
    had to be to appear exactly once, and it is what ``mark-claim-in-leaf``
    anchors the marker at. ``line`` is where it located, carried for the report
    and for the exclusions checked against it, and ``locator`` is the label range
    of the **semantic extent** — the span before any widening, which is what the
    claim actually covers. The two differ exactly where findability demanded
    more bytes than meaning did.
    """

    document: str
    title: str
    excerpt: str
    line: int
    locator: str


@dataclass(frozen=True)
class Identification:
    """One document's outcome: the claims it states, or the reason it states none."""

    document: str
    claims: tuple[ProseClaim, ...] = ()
    no_claim: str | None = None
    telemetry: Telemetry = Telemetry()

    @property
    def anchored_nothing(self) -> bool:
        """This document named results and none of them could be anchored.

        Read off the reserved literal rather than off a flag beside it, on
        :func:`conform.determination`'s terms: one statement of the state, and
        the frontmatter the run writes is that statement.
        """
        return self.no_claim == UNANCHORED_REASON


@dataclass(frozen=True)
class Checked:
    """C4's verdict over one answer, with the failure kinds kept apart by what each costs.

    They are kept apart because the stage spends something different on each,
    and folding them into one list would lose the distinction at the point it
    decides.
    """

    claims: tuple[ProseClaim, ...] = ()
    no_claim: str = ""
    #: Blocks that would not anchor. Each costs one re-ask of its own, under
    #: :data:`REASK_CEILING`, carrying the sentences the tool found.
    unresolved: tuple[Unresolved, ...] = ()
    #: Blocks that cost their claim and are not re-asked: one the parse could
    #: not read, a start lying inside an author-marked block, a title that is
    #: not one line or is a second record's too, two blocks resolving to one
    #: sentence, a slice still repeated across its whole paragraph, a span an
    #: earlier claim's marker would make unfindable, or an answer returning
    #: claim blocks *and* a reason. No menu would answer any of them.
    refusals: tuple[str, ...] = ()
    #: Failed checks over the no-claim sentence alone.
    reason_failures: tuple[str, ...] = ()

    @property
    def failures(self) -> tuple[str, ...]:
        return tuple(f"{missed.record.quote[:60]!r} ({missed.trigger})" for missed in self.unresolved) + (
            self.refusals + self.reason_failures
        )


class IdentificationError(ClaimGraphError):
    """A mechanical check over one document's answer failed twice."""


def reading_of(document: Document, inventory: Inventory) -> Reading:
    """One document's :class:`Reading`, off stage B's inventory."""
    fields = kb_index_lib.parse_frontmatter(document.text) or {}
    reason = fields.get("no-claim")
    return Reading(
        document=document.path,
        text=document.text,
        body=strip_markers(document.text),
        standing_reason=reason if isinstance(reason, str) else "",
        fences=tuple(fence for fence in inventory.fences if fence.document == document.path),
        blocks=tuple(block for block in inventory.claim_blocks() if block.document == document.path),
    )


#: The fewest words a sentence must canonically carry to be a claim
#: **opening**. Measured: over 843 sentences of two built KBs, every folded
#: collision at three words or fewer was a structural label — ``proof``,
#: ``definition``, ``proposition`` — and folding merged no two distinct prose
#: sentences at any length.
MIN_OPENING_WORDS = 4


def _opens_a_claim(sentence: label.Sentence, fences: Sequence[MathFence]) -> bool:
    """Whether a resolved sentence may be the sentence a result begins at.

    Two exclusions, and both are about what a *start* can be rather than what a
    claim can contain.

    The first is counted over
    :func:`~kb_tools.kb_write.ops.canonical_form` — the matcher's own fold,
    called rather than restated, because a second reduction that disagreed with
    it would classify a sentence as unquotable while tier 2 went on resolving
    quotations to it. It covers both of the exclusion's halves at once: a
    structural label comes down to one or two words, and a markup-only line
    comes down to none.

    The second is the equation: **a claim may contain one and may not be one.**
    Display-maths fences open mid-sentence throughout this corpus — the
    converter emits one flush against the prose it interrupts — so an extent
    crossing a fence is the ordinary way a result carrying its own equation is
    stated, and refusing that would refuse most of what the mathematical volumes
    state. What is refused is a result that *begins* inside the equation.

    **Policy, and it lives here.** It is applied over the hits the resolver
    returned and before the count is taken, so the matcher stays a search over a
    string and no rule about what a claim may be enters ``kb_write``.
    """
    if len(ops.canonical_form(sentence.text).split()) < MIN_OPENING_WORDS:
        return False
    return not any(fence.start <= sentence.line < fence.end for fence in fences)


def _unique_slice(span: label.Span, rendered: label.Render, text: str) -> tuple[label.Span, int] | None:
    """``span`` widened within its paragraph until its slice appears exactly once.

    The question asked is :func:`kb_tools.kb_write.ops.excerpt_lines`' — the
    matching ``mark-claim-in-leaf`` will itself run over these same bytes — and
    not a second implementation of it: a pre-check that disagrees with the op it
    pre-checks is worse than no pre-check.
    """
    for candidate in (span, *rendered.widen(span)):
        lines = ops.excerpt_lines(text, candidate.excerpt)
        if len(lines) == 1:
            return candidate, lines[0]
    return None


@dataclass(frozen=True)
class _Anchor:
    """One claim block bound to the sentence its result begins at."""

    record: Record
    start: label.Sentence
    #: Which tier of :func:`~kb_tools.kb_write.ops.resolve_excerpt` found it — 1
    #: strict, 2 folded. Carried for :class:`Telemetry` and for nothing else: a
    #: folded match is accepted or refused on its label, never on its tier.
    tier: int


#: How many sentences either side of a claimed label a trigger-A re-ask shows.
#: Enough to place the label in its paragraph, few enough that the menu is not
#: the document again.
_NEIGHBOURHOOD = 2


def _labelled(sentence: label.Sentence) -> str:
    """One sentence as the render showed it to the seat, which is the only form it can name."""
    return f"{sentence.label}: {sentence.text}"


def _at(rendered: label.Render, named: str) -> label.Sentence | None:
    return next((sentence for sentence in rendered.sentences if sentence.label == named), None)


def _neighbourhood(rendered: label.Render, named: str) -> tuple[str, ...]:
    """The labelled sentences around ``named``, or empty where the render carries no such label."""
    positions = [index for index, sentence in enumerate(rendered.sentences) if sentence.label == named]
    if not positions:
        return ()
    at = positions[0]
    window = rendered.sentences[max(0, at - _NEIGHBOURHOOD) : at + _NEIGHBOURHOOD + 1]
    return tuple(_labelled(sentence) for sentence in window)


def _went_nowhere(record: Record, reading: Reading, rendered: label.Render) -> Unresolved:
    """Trigger A's evidence: the window the search came nearest, and the claimed label's own.

    **Two facts, because the seat has two ways of being wrong here** and the
    menu must not presume which. A quote mistyped or lifted from the wrong
    document is answered by the window the search came closest to; a quote that
    is fine and a label that is not is answered by what actually stands at the
    label. Neither is offered as the answer — the template's "none of these" is
    what keeps a narrowed menu from forcing a confident wrong choice.
    """
    nearest = ops.nearest_excerpt(reading.body, record.quote)
    found = None if nearest is None else _at(rendered, label.labels_at(rendered, nearest.offset))
    offered = [_labelled(found)] if found is not None else []
    offered += _neighbourhood(rendered, record.label)
    # The window and the neighbourhood are two answers to two different
    # mistakes, and they name the same sentence whenever the quote landed near
    # the label after all. Offering it twice would read as two candidates.
    return Unresolved(record, Trigger.NOWHERE, tuple(dict.fromkeys(offered)))


def _resolve(record: Record, reading: Reading, rendered: label.Render) -> _Anchor | Unresolved:
    """One quote bound to its start sentence, or the evidence a re-ask about it carries.

    **One entry point, no caller-selectable mode.** The escalation from strict
    to folded matching is internal to
    :func:`~kb_tools.kb_write.ops.resolve_excerpt` and deterministic for every
    caller — a pre-check that disagrees with the op it pre-checks is worse than
    no pre-check, and a mode parameter is how they come to disagree.

    The exclusion (:func:`_opens_a_claim`) is a **filter over the hits**, taken
    before the count is: it is policy about what a claim opening may be, and it
    lives here rather than in the matcher, which stays a search over a string.
    """
    resolution = ops.resolve_excerpt(reading.body, record.quote)
    starts: list[label.Sentence] = []
    for hit in resolution.hits:
        found = _at(rendered, label.labels_at(rendered, hit.offset))
        if found is not None and found not in starts and _opens_a_claim(found, reading.fences):
            starts.append(found)

    if not starts:
        return _went_nowhere(record, reading, rendered)
    if len(starts) > 1:
        # Settled without a call where the label names exactly one of the hits.
        # The label is what breaks the tie, which is the whole of what it is for.
        named = [sentence for sentence in starts if sentence.label == record.label]
        if len(named) != 1:
            return Unresolved(record, Trigger.SEVERAL, tuple(_labelled(sentence) for sentence in starts))
        return _Anchor(record, named[0], resolution.tier)
    if starts[0].label != record.label:
        claimed = _at(rendered, record.label)
        offered = (claimed, starts[0]) if claimed is not None else (starts[0],)
        return Unresolved(record, Trigger.DISAGREED, tuple(_labelled(sentence) for sentence in offered))
    return _Anchor(record, starts[0], resolution.tier)


def _extent(start: label.Sentence, rendered: label.Render, starts: frozenset[str]) -> label.Span:
    """``start``'s claim, run to the next start in its paragraph or to the paragraph's end.

    Mechanical, and it is what makes the extent a property of the answer as a
    whole rather than a granularity the ask leaves free. Starts partition a
    paragraph; extents never nest, and none crosses a paragraph break because
    :mod:`label` already guarantees no span does.
    """
    family = [sentence for sentence in rendered.sentences if sentence.paragraph == start.paragraph]
    first = family.index(start)
    last = first + 1
    while last < len(family) and family[last].label not in starts:
        last += 1
    return label.Span(sentences=tuple(family[first:last]))


def _place(anchors: Sequence[_Anchor], reading: Reading, rendered: label.Render) -> tuple[list[ProseClaim], list[str]]:
    """The anchored blocks as claims the write path can land, and the ones that cost their claim.

    Runs over the anchors **together**, because three of the four checks here
    are about the set: the extents partition their paragraph, no two claims may
    share a title, and marking one claim changes the document the next claim's
    slice is findable in.
    """
    refusals: list[str] = []
    by_start: dict[str, _Anchor] = {}
    for anchor in anchors:
        if anchor.start.label in by_start:
            refusals.append(
                f"two claim blocks resolve to the same sentence, {anchor.start.label}; their extents are "
                f"therefore the same span, and one span states one result once. Quote the sentence the "
                f"second result begins at"
            )
            continue
        by_start[anchor.start.label] = anchor

    order = {sentence.label: index for index, sentence in enumerate(rendered.sentences)}
    named = frozenset(by_start)
    located: list[ProseClaim] = []
    titles: set[str] = set()

    for anchor in sorted(by_start.values(), key=lambda bound: order[bound.start.label]):
        extent = _extent(anchor.start, rendered, named)
        # The locator the op is handed must additionally be unique in the
        # document, and widening within the paragraph is the lever for that
        # alone — it names no wider result. The question asked is
        # `ops.excerpt_lines`', which is the matching `mark-claim-in-leaf` will
        # itself run, rather than a second implementation of it.
        widened = _unique_slice(extent, rendered, reading.text)
        if widened is None:
            refusals.append(
                f"the result beginning at {anchor.start.label} runs to words that still appear more than "
                f"once when widened to their whole paragraph, so no single line of the document names them"
            )
            continue
        span, line = widened
        # Vacuous over C-inf's scope by construction, and asserted rather than
        # assumed, because it is what keeps this stage and C-mech from
        # double-counting if the scope ever widens.
        block = next((site for site in reading.blocks if site.start <= line < site.end), None)
        if block is not None:
            refusals.append(
                f"the result beginning at {anchor.start.label} lands at line {line + 1}, inside the author's "
                f"own {block.environment} block, whose claim is declared already"
            )
            continue
        title = anchor.record.title.strip()
        if not title or "\n" in title:
            refusals.append(
                f"the title for {anchor.start.label} is empty or runs over more than one line; a title is "
                f"one paragraph on one line"
            )
            continue
        if title in titles:
            refusals.append(
                f"{title!r} is the title of two records in this document, and a register entry is bound "
                f"back to its site by title"
            )
            continue
        # The last check, and it is here rather than in the write path because
        # the op cannot refuse cheaply: a marker goes on the end of the first
        # line of its own slice's span and each slice is matched against the
        # document the previous marker left, so a slice crossing an earlier
        # record's marked line is unfindable by the time its turn comes — and
        # the refusal lands after this document's register entries and
        # frontmatter, leaving it reading as hosting claims it carries no marker
        # for, outside every re-run's scope. Two starts sharing a physical line
        # is the ordinary case now and is not what this refuses: what it refuses
        # is the placement that breaks, which is why it is a match over bytes
        # and not a comparison of line numbers.
        broken = next(
            (
                placed
                for placed in located
                if ops.excerpt_lines(_with_marker(reading.text, placed.line), span.excerpt) != (line,)
            ),
            None,
        )
        if broken is not None:
            refusals.append(
                f"the result beginning at {anchor.start.label} runs across line {broken.line + 1}, where "
                f"marking {broken.locator} puts that claim's own marker, so this span would no longer be "
                f"findable by the time it is marked"
            )
            continue
        titles.add(title)
        located.append(
            ProseClaim(
                document=reading.document,
                title=title,
                excerpt=span.excerpt,
                line=line,
                locator=extent.locator,
            )
        )
    return located, refusals


def _check_reason(answer: Answer, reading: Reading) -> tuple[str, ...]:
    """The checks over the no-claim sentence, which apply only where the reason *is* the answer.

    An empty reason beside claim blocks is the ordinary shape, not a reason that
    failed a check — and a document whose blocks were all unreadable has not
    said it states nothing, so it is not asked to have given a reason.
    """
    reason = answer.no_claim.strip()
    if answer.claims or answer.refusals:
        return ()
    if not reason:
        return ("the answer returns neither a claim block nor a reason for there being none",)
    if reason == reading.standing_reason:
        return (
            "the reason is the sentence this document already carries, which is the sentence that makes it "
            "awaiting; writing it back would leave it awaiting and reopen it on every re-run",
        )
    if "\n" in reason:
        return ("the reason runs over more than one line; it is one paragraph on one line",)
    return ()


def _check(answer: Answer, reading: Reading, re_ask: ReAsk | None) -> tuple[Checked, Telemetry]:
    """C4 over one answer, with the per-claim re-asks spent where a re-asker is supplied.

    Two phases, and the split is what makes the per-claim re-ask expressible:
    **resolution** binds each block to the sentence its result begins at, and
    what fails there is re-askable with evidence; **placement** computes the
    extents, cuts the slices and refuses what the write path could not land, and
    what fails there costs its claim.

    One worker for both callers, because a check that re-derived the stage's own
    resolution would be a second reading of the same answer — and the two would
    then be able to disagree about what this document states.
    """
    rendered = reading.render
    anchors: list[_Anchor] = []
    missed: list[Unresolved] = []
    refusals = list(answer.refusals)
    for record in answer.claims:
        bound = _resolve(record, reading, rendered)
        if isinstance(bound, _Anchor):
            anchors.append(bound)
        else:
            missed.append(bound)
    resolved = len(anchors)
    folded = sum(1 for anchor in anchors if anchor.tier != 1)

    reasked = 0
    still: list[Unresolved] = []
    for unresolved in missed:
        if re_ask is None or reasked >= REASK_CEILING:
            still.append(unresolved)
            continue
        reasked += 1
        try:
            chosen = re_ask(unresolved)
        except AnswerFormatError as refusal:
            refusals.append(f"the re-ask about {unresolved.record.quote[:60]!r} was not answered: {refusal.detail}")
            still.append(unresolved)
            continue
        if chosen is None:
            still.append(unresolved)
            continue
        bound = _resolve(chosen, reading, rendered)
        if isinstance(bound, _Anchor):
            anchors.append(bound)
        else:
            still.append(bound)

    located, placement = _place(anchors, reading, rendered)
    refusals += placement
    if answer.claims and answer.no_claim.strip():
        refusals.append(
            "the answer returns claim blocks and a no-claim block. Exactly one of the two is an answer; both "
            "together says the document does and does not state a result"
        )
    checked = Checked(
        claims=tuple(located),
        no_claim="" if answer.claims else answer.no_claim.strip(),
        unresolved=tuple(still),
        refusals=tuple(refusals),
        reason_failures=_check_reason(answer, reading),
    )
    telemetry = Telemetry(
        returned=len(answer.claims) + len(answer.refusals),
        resolved=resolved,
        folded=folded,
        reasked=reasked,
        resolved_on_reask=len(anchors) - resolved,
        unresolved=len(still) + len(refusals),
    )
    return checked, telemetry


def check_answer(answer: Answer, reading: Reading) -> Checked:
    """C4's verdict over one answer as it stands — every comparison against the document, no call spent."""
    return _check(answer, reading, None)[0]


def _failure_report(failures: Sequence[str]) -> str:
    return "\n".join(
        [
            "Each of the following failed a comparison against the document itself. Answer again for the "
            "whole document.",
            "",
            *(f"- {failure}" for failure in failures),
        ]
    )


def _ask_document(reading: Reading, identifier: Identifier) -> tuple[Answer, tuple[str, ...]]:
    """The first ask and the one allowance the *document* holds, and what the answer's reason cost.

    Two failures reach here and no others: an answer that could not be read at
    all, and an answer whose reason is the whole answer and fails its checks.
    Everything else is one claim's business.
    """
    report: str | None = None
    for attempt in range(1 + ANSWER_RETRY_BUDGET):
        last = attempt == ANSWER_RETRY_BUDGET
        try:
            answer = identifier.identify(reading, report=report)
        except AnswerFormatError as refusal:
            if last:
                raise IdentificationError(
                    refusal.check,
                    f"{reading.document}: what came back could not be read twice: {refusal.detail}. Nothing "
                    f"was written for this document, and it is not recorded as stating nothing — an answer "
                    f"that could not be read is not evidence about what the document says",
                ) from refusal
            report = refusal.detail
            continue
        reason_failures = _check_reason(answer, reading)
        if reason_failures and not last:
            report = _failure_report(reason_failures)
            continue
        return answer, reason_failures
    raise AssertionError("unreachable: the last attempt returns or raises")


def infer_claims(reading: Reading, identifier: Identifier) -> Identification:
    """C3 and C4 over one document: the ask, the checks, and one re-ask per unresolved claim.

    Returns what the document states or the reason it states none — never both,
    and never an empty result standing in for a failure.

    **A failure costs the claim it belongs to.** A quote that will not settle on
    one sentence is re-asked on its own, under :data:`REASK_CEILING`; when that
    trips, every still-unresolved claim is recorded unresolved, and the ceiling
    and the recording compose with no special case. A document landing even one
    claim takes ``HOSTS_CLAIMS`` and leaves ``AWAITING`` as it always did; one
    landing none of the results it named takes :data:`UNANCHORED_REASON`, which
    is the fifth determination and not a finding that the document states
    nothing.
    """
    answer, reason_failures = _ask_document(reading, identifier)
    checked, telemetry = _check(answer, reading, lambda unresolved: identifier.re_ask(reading, unresolved))

    if checked.claims:
        return Identification(document=reading.document, claims=checked.claims, telemetry=telemetry)
    if answer.claims or answer.refusals:
        return Identification(document=reading.document, no_claim=UNANCHORED_REASON, telemetry=telemetry)
    return Identification(
        document=reading.document,
        no_claim=SUBSTITUTED_NO_CLAIM_REASON if reason_failures else checked.no_claim,
        telemetry=telemetry,
    )
