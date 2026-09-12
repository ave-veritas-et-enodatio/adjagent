"""Stage B — the claim-site inventory. One pass over every document.

Five readings, each keyed on a form the Document-Tree Contract states rather
than on a shape observed once in one corpus:

* **labelled blockquotes** (point 12) — the environment's display name off the
  label line, the identifier where the source carried a ``\\label``, the title
  and locator off the display line beneath, and the block's extent;
* **display-maths fences** (point 9) — so an equation is never mistaken for
  prose and never quoted as a claim, and the ``\\label`` tokens inside them;
* **rewritten cross-references** (point 7) — with the document each resolves to,
  its type, the author's own label off its third attribute, and which labelled
  block it sits inside where it sits inside one;
* **rendered citations** (point 10) — see the caveat below;
* **which claim each proof establishes** (:class:`Proof`) — the one reading that
  spans documents, and the one that is a *relation between* blocks rather than a
  reading of one.

**The name being classified is the environment's display name.** A
``\\newtheorem`` declares two strings — an arbitrary internal handle and a
display name saying what the block is — and point 12's label line carries the
second, so ``\\newtheorem{claimbox}{Result}`` is classified as a ``Result``.
That is the difference between a vocabulary and a list of handles: across 35
surveyed arXiv papers, 43 distinct internal handles appear and 36 fall outside
any table, because the tail is aliases (``Cor``, ``Lem``, ``Pro``), starred
variants (``lem*``) and other languages (``Teo``, ``oss``) — while the display
names concentrate onto the thirteen words below. Where no declaration supplied
a display name the label carries the handle instead (``proof``, which amsthm
declares and no author does), and that is the same string it always carried.

**Names are classified against a closed table, case-folded, and a name outside
it is recorded rather than refused.** The block is admitted as
not-claim-bearing for that build — :attr:`Block.claim_bearing` reads
:data:`CLAIM_BEARING` alone, so an unclassified name is outside it by
construction — and the name is counted into the census this stage reports
(:func:`census`, ``Census.unclassified``). Case is folded because the label
carries the author's own capitalisation and ``Theorem`` and ``theorem`` are the
same word; the census reports the name as the page spells it.

**What the closed table guards against is silence, not omission.** The argument
that made this a refusal is right and stands: a default column *is* how a claim
silently leaves the graph, and nothing here classifies an unknown name as
claim-bearing. What the refusal conflated was *do not lose claims quietly* with
*stop*, and only the first is load-bearing. A recorded, named, counted omission
is not silent — it is the census line a reader greps — while a halted build
reports one bit and no distribution at all. That distinction is why
:data:`NOT_CLAIM_BEARING` still exists with no branch reading it: it is what
separates a name somebody classified as not stating a result from a name nobody
has classified, and the census reports only the second.

**This is what makes the stage usable outside the corpus it was calibrated on.**
Even over display names the table is a table: the survey's residue is some
fifteen one-offs — ``Setup``, ``Axiom``, ``Case``, ``Fact``, ``Hypothesis``,
``Exercise``, ``Solution`` — and a name outside the table has to cost a census
line rather than a run.

A ``proof`` is not a claim; it is the derivation establishing one. A
``conjecture`` **is** claim-bearing, and that is a ruling rather than a default:
a conjecture is a result the corpus states and does not prove, which is exactly
the condition local rigor grades and solidity propagates, so it belongs in the
graph carrying a low rigor rather than outside it carrying none. A ``claim`` and
a ``result`` are claim-bearing on that same reading. ``definition`` is
classified not-claim-bearing because SPEC.md rules definition nodes out of
scope; ``problem`` states a question rather than a result, and ``example``,
``assumption``, ``notation`` and ``remark`` state no result at all.

**A blockquote with no label line is not an unclassified block.** It is ordinary
quoted prose — an author quoting a conclusion, a displayed italic passage — and
reporting it would be reporting that the author quoted something.

**The optional argument is what point 12 preserves, not what it requires, and
most authors write none.** ``\\begin{lemma}`` untitled is the overwhelming norm
outside corpora written with tooling that prompts for a title, so a stage that
demanded one would refuse most mathematical sources. What such a block's display
line carries is the environment's printed word and its number — ``**Lemma 3**.``
— and that is what is read off it: the title is that span with its emphasis
markers taken off, and the locator is the span. A bare *Lemma 3* is a poorer
heading than an author's own sentence would be, and it is the honest one; point
12's own words are that a consumer reads the author's title there "rather than
inferring one", and where the author gave none there is nothing to read and
still nothing to infer.

**The title carries the words the page shows; the locator carries the page's own
bytes.** They come off one span, so they cannot name different blocks, but they
are not the same string: an author who titled a block ``[\\citet{gibbons2020}]``
titled it with whatever point 10 wrapped that citation in, and a ``<span
class="citation" data-cites="…">`` reaching a register heading is markup where a
reader expects words. The title is therefore that span with the citation markup
replaced by what it renders — the text the page itself shows — while the locator
stays the document's own bytes, because the write API matches it against them.

**A span that runs inside another block's display line is extended through its
own content until it does not.** ``\\newtheorem*`` declares an unnumbered
environment, so a document may carry several blocks whose display line opens
``**Theorem**`` exactly — and the span is a locator a marker is placed by, so
one matching two blocks binds a claim to the wrong site silently. Extending
stays inside what the page carries: the words after the printed name are the
author's, and the first of them that differs is the smallest thing on the page
that tells the two apart. Each such span is grown against the *other lines*
rather than against the spans already handed out, so both of two alike blocks
grow and neither's span depends on which the document lists first. The title
follows the extended span, because a register entry is bound back to its site by
title (``graph.read``) and two entries sharing one would join to one block.

**A display line yielding neither a title nor a locator costs its own block, not
the build.** Two shapes do. One does not open with the environment's printed name
at all: where the declaration sits in a class file rather than in the volume root
the reader has no name and no number to print, so the block's content begins with
the author's own sentence and point 12's span is absent from the line. The other
runs inside another block's display line to its last word, so no span of it names
one block rather than the other. Neither is two mechanical artifacts disagreeing —
it is one authored block this stage cannot read, and a tree it sits in carries
every other document the run derived. So the block is admitted carrying no title
and no locator, :attr:`Block.claim_bearing` is false for it however its
environment is classified, it yields no claim node, and it is counted into the
census (:func:`census`, ``Census.unreadable``). That is the same trade an
unclassified name takes above and it is taken for the same reason: what must never
happen is a claim leaving the graph in silence, and a named count is not silence.

**The maths fence is not anchored at column zero.** Point 9 states both halves:
the opening delimiter is ``` ``` math ``` with a space, and a fence may sit
inside a blockquote prefixed ``> ``. A scanner that assumed column zero would
misread the quoted ones, and the failure is not local — an unclosed fence
swallows everything that follows it.

**A closing delimiter may carry trailing markup, and the contract does not say
so.** Where a quoted fence sits inside an emphasised run, the reader closes the
emphasis on the fence's own line, so the delimiter renders with the emphasis
marker attached to it. That is not a closing fence under CommonMark, and a
reader matching the delimiter exactly finds the fence unclosed and swallows
every claim site after it. The close is therefore matched on the delimiter this
scan opened with rather than on the whole line; point 9 owes a sentence about
the rendered closing form the way it owes one about the opening spelling.

**Every citation carries its own key, and that gap is closed.** A resolved
inline citation used to carry no marker at all — the reader emitted it as
ordinary author-year prose — so this stage could name only the unresolvable ones
and the reference-list entries. The front end's Lua filter now wraps every
citation in pandoc's own citation markup, ``<span class="citation"
data-cites="key1 key2">``, whatever citeproc then does inside it, so the key is
readable off the page in all three of the states a citation can be in: answered
by a bibliography, offered one that did not carry it, and offered none at all.

``Citation.state`` reports which of the three, and it is read off the span's own
rendering rather than off anything about the build. **The span carries both
halves of that comparison**: ``data-cites`` holds the keys, and the span's text
holds what became of them. Text that is exactly its own keys — ``(key1; key2)``,
the form the front end's filter writes where it is told no citeproc will run
(``kb_docgraph/authored_blocks.lua``) — was offered no bibliography. Otherwise
citeproc ran, and a key it could not answer carries ``**key?**`` inside that text
while the rest of the span resolved around it. So the states are **per key, not
per build**: one ``\\citep{a,b}`` renders ``(Nobody 2026; **b?**)`` where the
bibliography answers one of the two.

**"The tree renders no reference list" is not a test for "no bibliography was
offered".** A bibliography that answers none of the cited keys emits no
reference list either, so that reading merges the two states that most need
telling apart — a work fully known and a work nothing is known about. The
span-local comparison above is what avoids it.

**The citation reading is over marker-free text, and that is a scar.** A Tier-2
marker is appended to the end of the line its excerpt begins on
(:func:`tree.strip_markers`), and a claim-bearing block's excerpt begins on its
display line — which is exactly where a theorem environment's optional argument
puts a ``\\cite``. The marker therefore lands *inside* the citation span's
opening tag, between ``class="citation"`` and ``data-cites``,
:data:`CITATION_SPAN_RE` stops matching, and the citation leaves the inventory
with nothing downstream able to tell: there is no second count for it to
disagree with. Measured over the built arXiv corpus, four citations of 1387
vanish this way on a re-scan and **all four are citations inside a claim
block** — half of that population, because markers and those citations sit on
the same line by construction. So this reading strips markers the way
:func:`_display` already does, and the line numbering survives it: a marker
carries no newline, so :attr:`Citation.line` still addresses the written
document. The tree's other three readings were measured against the same corpus
and none of them moves.
"""

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from enum import Enum

from .report import ClaimGraphError
from .tree import ANCHOR_RE, Document, Tree, resolve, strip_markers, unquote

#: Display names whose blocks state a result, case-folded. Every member is a
#: display name a surveyed corpus declares.
CLAIM_BEARING: frozenset[str] = frozenset(
    {"theorem", "lemma", "proposition", "corollary", "conjecture", "claim", "result"}
)

#: Display names whose blocks do not. ``proof`` establishes a claim rather than
#: stating one; ``remark`` and ``notation`` are commentary; ``example`` and
#: ``assumption`` are the material a result is stated over; ``problem`` states a
#: question; ``definition`` is a node kind SPEC.md places out of scope.
#:
#: **Nothing branches on this set, and it is not obviated.** An unclassified name
#: is already not-claim-bearing by :attr:`Block.claim_bearing` reading
#: :data:`CLAIM_BEARING` alone. What this set carries is the *judgement* — these
#: names were looked at and found not to state results — and the census
#: subtracts it to report the names nobody has looked at, which is the whole
#: datum a sweep over an unfamiliar corpus is collecting.
NOT_CLAIM_BEARING: frozenset[str] = frozenset(
    {"proof", "remark", "definition", "example", "assumption", "notation", "problem"}
)

#: Every name this package has classified, either way. The census reports what
#: falls outside it.
CLASSIFIED: frozenset[str] = CLAIM_BEARING | NOT_CLAIM_BEARING

#: Point 12's label line, in both of its two forms — bare, and wrapped in the
#: addressable identifier the source's ``\label`` became.
#:
#: **The name may be several words** — ``\newtheorem{testexample}{Test
#: example}`` — so the interior spaces are matched, and a bold *phrase* alone on
#: a quoted line now reads as a label line where a bold word already did. What
#: that costs is a census entry, the same as any other name nobody classified;
#: the derived ``> **Leaf references:**`` footer is not one of them, carrying
#: link text after its closing markers.
LABEL_LINE_RE = re.compile(
    r'^>\s*(?:<span id="(?P<identifier>[^"]*)">)?' r"\*\*(?P<environment>[A-Za-z]+(?: [A-Za-z]+)*)\*\*(?:</span>)?\s*$"
)

#: Point 9's fence, over blockquote-stripped text. The info string is ``math``
#: and the spelling carries the space.
FENCE_OPEN = "``` math"
FENCE_CLOSE = "```"

#: An equation label, surviving verbatim inside the fence (point 9).
EQUATION_LABEL_RE = re.compile(r"\\label\{([^}]*)\}")

#: Every citation, by the keys it carries and by what became of them. Pandoc's
#: own citation markup, which the front end's filter puts around a citation
#: before citeproc sees it — so this matches whether or not anything resolved —
#: with the rendering the filter and citeproc left inside it. Keys are
#: space-separated inside one attribute because one ``\citep{a,b}`` is one
#: citation element.
#:
#: Matched over blockquote-stripped text for ``ANCHOR_RE``'s reason: the reader
#: wraps a long tag across lines, and inside a labelled block the continuation
#: carries the quote prefix. ``DOTALL`` covers both the wrapped tag and the
#: wrapped rendering.
CITATION_SPAN_RE = re.compile(
    r'<span\s+class="citation"\s+data-cites="(?P<keys>[^"]*)"\s*>(?P<rendering>.*?)</span>', re.DOTALL
)

#: One work of a rendered reference list, as the reader's citation processor
#: emits it: a ``csl-entry`` Div keyed by the citation key, with the composed
#: author-year-title text inside it. The text is captured because it is what an
#: external-work node is stood up from — the key alone names the work and says
#: nothing about it.
BIBLIOGRAPHY_ENTRY_RE = re.compile(r'<div id="ref-(?P<key>[^"]+)"[^>]*>(?P<text>.*?)</div>', re.DOTALL)


class CitationState(Enum):
    """What became of one citation key by the time it reached the Markdown.

    Point 10's three, named for the key's own fate rather than for what a reader
    of the page can do about it. The values are the names the census reports
    under, so a state cannot reach a report under a second spelling.
    """

    #: A bibliography answered it, and it renders as an author-year reference.
    RESOLVED = "resolved"
    #: A bibliography was offered and did not carry it: ``(**key?**)``.
    UNANSWERED = "unanswered"
    #: No bibliography stood behind it, so the key is the whole of what the page
    #: says about the work.
    KEY_ONLY = "key-only"


@dataclass(frozen=True)
class Block:
    """One author-distinguished block, and everything a register entry needs.

    ``title`` and ``display`` are read off the same line, which is why it is
    worth naming: a block's title and the anchor that locates it cannot
    disagree. ``display`` is the span of that line they are both read from, and
    is the Tier-2 locator — apt by construction rather than by selection. Where
    the author declared an optional argument the span carries it and the title
    is the argument; where they declared none the span is the environment's
    printed word and number, and the title is that.

    **They are the same span read for two purposes, and only one of them is
    bytes.** ``display`` is matched against the document, so it is the document's
    own text and nothing may normalise it. ``title`` is read by a person — a
    register heading, an entry in stage D's enumeration — so it carries the words
    that span shows and not the reader's markup carrying them
    (:func:`_readable`).

    Both are ``None`` together, on a block whose display line yields neither
    (module docstring). That block is a claim site nothing downstream can name or
    anchor, which is what :attr:`claim_bearing` answers for.
    """

    document: str
    #: The name on the label line: the environment's declared display name, or
    #: its internal one where no declaration supplied a display name.
    environment: str
    identifier: str | None
    title: str | None
    display: str | None
    #: 0-based line of the label line, and the line after the block's last.
    start: int
    end: int

    @property
    def claim_bearing(self) -> bool:
        """Whether this block is a claim site the graph can carry: two conditions, not one.

        The environment's name must state a result — :data:`CLAIM_BEARING` alone,
        so an unclassified name is outside it by construction — **and** the
        display line must have yielded the title and the locator a register entry
        and a Tier-2 marker are composed from. One predicate rather than two
        because every consumer wants the conjunction: a block with no locator
        joins to no claim node, and comparing against its ``None`` would match a
        prose claim whose own marker could not be found.
        """
        return self.environment.lower() in CLAIM_BEARING and self.display is not None


@dataclass(frozen=True)
class MathFence:
    """One display-maths block, and the equation labels inside it."""

    document: str
    start: int
    end: int
    labels: tuple[str, ...]


@dataclass(frozen=True)
class Anchor:
    """One rewritten cross-reference: where it points and where it sits."""

    document: str
    line: int
    reference_type: str
    href: str
    #: The document it resolves to, or ``None`` for a bare fragment — point 7's
    #: legitimate unresolved outcome, visible to a reader as its own label.
    target: str | None
    fragment: str
    #: The author's own ``\label``, off the anchor's third attribute. It rides
    #: every anchor whether or not the fragment does, which is what names the
    #: equation an ``eqref`` points at — that anchor's fragment is empty, point 9
    #: leaving an equation's label inside the maths fence rather than as an id.
    label: str
    #: The environment of the labelled block it sits inside, or ``None`` for one
    #: in surrounding prose.
    hosting_environment: str | None


#: Point 12's label line for a proof, case-folded. ``amsthm`` declares the
#: environment and its display name is the handle, so this is the one word the
#: label line carries whatever the author called the environment internally.
PROOF_ENVIRONMENT = "proof"

#: A proof's opening emphasis run — ``*Proof.*``, or ``*Proof of Theorem <a
#: …>1</a>.*`` where the author gave ``\begin{proof}`` an optional argument.
#: Anchored at the block's content, non-greedy to the first closing marker, and
#: reading no word of it: the run is found by its shape because ``\proofname``
#: is translated and the label line above it is not.
_PROOF_HEAD_RE = re.compile(r"\s*\*(?!\*).+?(?<!\*)\*", re.DOTALL)


@dataclass(frozen=True)
class Proof:
    """One proof block, and the claim-bearing blocks it establishes.

    **A proof establishes the claim it belongs to**, so what the proof leans on,
    the claim leans on — and that is a fact about blocks and lines, knowable
    before any id exists. It is read here for that reason rather than for any
    one consumer's: stage D directs a ``\\ref`` inside a proof body by it, and
    the off-graph endcap directs a ``\\cite`` by the same containment through the
    same binding. Two derivations of what a proof proves could disagree about
    the corpus, and there is no third artifact to break the tie.

    ``subjects`` are :class:`Block`\\ s rather than anything minted, which is what
    lets a pass that runs before the first id exists use it. Empty is the
    ordinary outcome and never an error: a proof under a remark, under a second
    proof, or opening a document proves nothing this reading can name.

    ``head`` is every anchor of the block's opening emphasis run, by href and
    label. It is those two rather than a line range because the run closes
    mid-line — ``*Proof.* By Lemma <a …>`` is one line carrying a head and a
    dependency — and a line-granular reading would take the second for the
    first. A consumer asks :meth:`names` to tell what the proof *proves* from
    what it *rests on*.
    """

    document: str
    #: The proof block's own extent, 0-based and half-open: :attr:`Block.start`
    #: and :attr:`Block.end` of the proof, carried so a consumer joining a
    #: position to a proof needs no second lookup.
    start: int
    end: int
    subjects: tuple[Block, ...]
    head: frozenset[tuple[str, str]] = frozenset()

    def names(self, anchor: Anchor) -> bool:
        """Whether ``anchor`` is one of the opening run's: what is proved, not what it rests on."""
        return (anchor.href, anchor.label) in self.head


@dataclass(frozen=True)
class Citation:
    """One inline citation key, and which of point 10's three states it ended in.

    Every inline citation reaches this inventory whatever its state: the front
    end marks each one with its own key before citeproc runs (see the module
    docstring).
    """

    document: str
    key: str
    state: CitationState
    #: 0-based line of the span's opening, in the document's own line numbering
    #: (``unquote`` is line-for-line). It is what joins a citation to the
    #: :class:`Block` it sits inside — the off-graph endcap's whole trigger —
    #: and it is a position rather than a containment flag because a block is
    #: found by comparing against ``Block.start`` / ``Block.end`` here, not by
    #: this scan knowing what a block is.
    line: int


@dataclass(frozen=True)
class Work:
    """One entry of a rendered reference list — a work, not a citation of one.

    The list is the volume's and carries each work once however many documents
    cite it, so counting these among the citations reports a one-work corpus as
    two. It is the second half of point 10's reading and is counted on its own
    line.

    ``text`` is the entry as the reference list renders it — the author, year
    and title a citation processor composed from the bibliography. It is the
    metadata an external-work node is stood up from, and it exists only where a
    bibliography answered the key: a work in either of point 10's other two
    states has no reference-list entry and therefore no record here.
    """

    document: str
    key: str
    text: str


@dataclass(frozen=True)
class Inventory:
    """Every claim site the tree carries, by the reading that found it."""

    blocks: tuple[Block, ...] = ()
    fences: tuple[MathFence, ...] = ()
    anchors: tuple[Anchor, ...] = ()
    citations: tuple[Citation, ...] = ()
    works: tuple[Work, ...] = ()
    #: Which claim each proof establishes. A sixth reading rather than a
    #: consumer's derivation: two passes direct a reference by it — stage D a
    #: ``\ref``, the off-graph endcap a ``\cite`` — and a binding derived twice
    #: could disagree about the corpus with no third artifact to settle it.
    proofs: tuple[Proof, ...] = ()

    def claim_blocks(self) -> tuple[Block, ...]:
        return tuple(block for block in self.blocks if block.claim_bearing)

    def hosting_documents(self) -> frozenset[str]:
        return frozenset(block.document for block in self.claim_blocks())

    def environments(self) -> Mapping[str, int]:
        counts: dict[str, int] = {}
        for block in self.blocks:
            counts[block.environment] = counts.get(block.environment, 0) + 1
        return dict(sorted(counts.items()))

    def unclassified_environments(self) -> Mapping[str, int]:
        """The environment names this package has classified neither way, with counts.

        Derived from the census rather than collected during the scan, so it
        cannot disagree with it: what is unclassified is exactly what appeared
        and is not in :data:`CLASSIFIED`.
        """
        return {name: count for name, count in self.environments().items() if name.lower() not in CLASSIFIED}

    def unreadable_display_lines(self) -> Mapping[str, int]:
        """Per document, the result-stating blocks whose display line yielded neither title nor locator.

        Derived from the blocks rather than collected while they were read, for
        :meth:`unclassified_environments`' reason: the census cannot disagree
        with the inventory it is taken from. The condition is spelled out here
        rather than asked of :attr:`Block.claim_bearing`, which is false for
        exactly these blocks and for every unclassified name as well — and only
        the first of those two is a lost claim.
        """
        counts: dict[str, int] = {}
        for block in self.blocks:
            if block.environment.lower() in CLAIM_BEARING and block.display is None:
                counts[block.document] = counts.get(block.document, 0) + 1
        return dict(sorted(counts.items()))


def _blockquote_extent(lines: Sequence[str], start: int) -> int:
    """The line after the last of the blockquote ``start`` opens.

    Every line of the block is a quoted line, metadata a previous pass wrote
    included: a marker is appended to the end of the line it marks, so it never
    interrupts the quote and never extends it (:func:`tree.strip_markers`).
    """
    end = start
    while end < len(lines) and lines[end].startswith(">"):
        end += 1
    return end


def _display(lines: Sequence[str], start: int, end: int) -> str:
    """The block's content, its markers removed, unquoted and collapsed to one line.

    Pandoc hard-wraps a block's content, so the display line the contract
    describes is a logical line rather than a physical one. Collapsing here is
    what lets the parse below read it as the contract writes it — and it is the
    same collapse the write API applies to a locator before matching it, so the
    span this yields is a span that API can find again. Metadata a previous pass
    appended is removed rather than collapsed into the content: it is not the
    author's words, and a marker landing where a hard wrap cuts the display line
    lands inside the optional argument the parse below reads the title out of.
    """
    body = " ".join(unquote(strip_markers("\n".join(lines[start:end]))).splitlines())
    return re.sub(r"\s+", " ", body).strip()


def _optional_argument(display: str) -> tuple[str, str] | None:
    """The environment's optional-argument title and the display line carrying it.

    The display line opens with the environment's printed word and number in
    bold — the label line's own word where a declaration supplied one, and a
    number that only this line carries — and the optional argument follows it
    parenthesised. The scan is balanced rather than
    non-greedy because a title may carry parentheses of its own, and it stops at
    the sentence period the reader writes after the closing bracket.
    """
    head = re.match(r"\*\*([^*]+)\*\*", display)
    if head is None:
        return None
    cursor = head.end()
    while cursor < len(display) and display[cursor] == " ":
        cursor += 1
    if cursor >= len(display) or display[cursor] != "(":
        return None
    depth = 0
    for position in range(cursor, len(display)):
        if display[position] == "(":
            depth += 1
        elif display[position] == ")":
            depth -= 1
            if depth == 0:
                closing = position
                break
    else:
        return None
    tail = closing + 1
    if tail < len(display) and display[tail] == ".":
        tail += 1
    return display[cursor + 1 : closing], display[:tail]


def _printed_name(display: str) -> str | None:
    """The span the display line opens with: the environment's printed word and its number.

    Point 12 renders the optional argument *after* this, so this is what an
    untitled block's display line carries and the whole of what it carries. The
    sentence period the reader writes after the word is taken with it, the way
    it is taken after a title's closing bracket above.
    """
    head = re.match(r"\*\*[^*]+\*\*\.?", display)
    return head.group() if head is not None else None


def _plain(span: str) -> str:
    """A display span as a register entry's heading: emphasis markers off, no closing stop."""
    return re.sub(r"\*+", "", span).strip().rstrip(".").strip()


def _read_display_line(display: str) -> tuple[str, str] | None:
    """The block's title and the span of the display line carrying it, or ``None``.

    Point 12's optional argument where the author wrote one; the environment's
    own printed word and number where they wrote none. ``None`` is the line that
    does not open with that name at all — the one shape this stage cannot read.
    """
    parsed = _optional_argument(display)
    if parsed is not None:
        return parsed
    printed = _printed_name(display)
    return None if printed is None else (_plain(printed), printed)


def _readable(title: str) -> str:
    """``title`` as the words the page shows: the reader's citation markup off, its rendering kept.

    Point 12's optional argument is *rendered* LaTeX, so an author who titled a
    block with a ``\\citet`` titled it with the span point 10 wraps every citation
    in — attribute, tags and all. What a reader of that line sees is the span's
    contents, and that is what a heading and an enumeration entry carry.

    The locator is untouched by this. It is matched against the document's own
    bytes, and those still carry the span.
    """
    return CITATION_SPAN_RE.sub(r"\g<rendering>", title).strip()


def _extended(display: str, span: str) -> str | None:
    """``span`` — a prefix of the display line ``display`` — grown by the next word of it."""
    tail = display[len(span) :].lstrip(" ")
    if not tail:
        return None
    return display[: len(display) - len(tail)] + tail.partition(" ")[0]


def _distinct(read: tuple[str, str], display: str, others: Sequence[str]) -> tuple[str, str] | None:
    """``read`` grown along ``display`` until its span appears in no line of ``others``.

    ``None`` where the whole line still does. Growth is decided against the other
    lines rather than against the spans already handed out, so which block a
    document lists first decides nothing: two alike blocks both grow, and each
    one's span names its own block on any re-scan.

    The title follows the span rather than staying the author's optional
    argument, because two register entries sharing a title join to one block
    (``graph.read``) exactly as two sharing a locator mark one line.
    """
    title, span = read
    while any(span in other for other in others):
        wider = _extended(display, span)
        if wider is None:
            return None
        title, span = _plain(wider), wider
    return title, span


def _blocks(document: Document) -> list[Block]:
    """Every labelled blockquote in ``document``, whatever its environment is called.

    A name outside :data:`CLASSIFIED` is admitted like any other and is
    not-claim-bearing by construction, so it needs no branch here: what it costs
    is a line in the census and nothing else.

    Only a result-stating block's span is held apart from the others', because
    only those are located by later: two ``**Remark**.`` blocks under one
    unnumbered environment are no reason to stop a build. The whole document's
    label lines are read before any span is settled, so a span is decided
    against every line it could be confused with rather than against the ones
    that happen to sit above it.

    A span neither reading yields leaves the block with no title and no locator,
    which costs the block and nothing else (module docstring).
    """
    lines = document.lines
    labelled: list[tuple[int, re.Match[str], int]] = []
    displays: list[str] = []
    for number, line in enumerate(lines):
        match = LABEL_LINE_RE.match(line)
        if match is None:
            continue
        end = _blockquote_extent(lines, number)
        labelled.append((number, match, end))
        # The label and the content are separated by one blank quoted line
        # (point 12), so the display line opens two lines below the label.
        displays.append(_display(lines, number + 2, end))

    found: list[Block] = []
    for position, (number, match, end) in enumerate(labelled):
        environment = match.group("environment")
        rendered = displays[position]
        read = _read_display_line(rendered)
        if environment.lower() in CLAIM_BEARING and read is not None:
            read = _distinct(read, rendered, displays[:position] + displays[position + 1 :])
        title, display = read if read is not None else (None, None)
        found.append(
            Block(
                document=document.path,
                environment=environment,
                identifier=match.group("identifier"),
                title=None if title is None else _readable(title),
                display=display,
                start=number,
                end=end,
            )
        )
    return found


def _fences(document: Document) -> list[MathFence]:
    """Every display-maths fence, quoted or not, with the labels inside it."""
    lines = unquote(document.text).splitlines()
    found: list[MathFence] = []
    opened: int | None = None
    for number, line in enumerate(lines):
        stripped = line.strip()
        if opened is None:
            if stripped == FENCE_OPEN:
                opened = number
        elif stripped.startswith(FENCE_CLOSE):
            body = "\n".join(lines[opened + 1 : number])
            found.append(
                MathFence(
                    document=document.path,
                    start=opened,
                    end=number + 1,
                    labels=tuple(EQUATION_LABEL_RE.findall(body)),
                )
            )
            opened = None
    if opened is not None:
        raise ClaimGraphError(
            "math-fence",
            f"{document.path}:{opened + 1}: a display-maths fence opens and never closes. Point 9 makes the "
            f"fence a guarantee, and an unclosed one swallows every claim site after it",
        )
    return found


def _anchors(document: Document, blocks: Sequence[Block], tree: Tree) -> list[Anchor]:
    text = unquote(document.text)
    found: list[Anchor] = []
    for match in ANCHOR_RE.finditer(text):
        number = text.count("\n", 0, match.start())
        path, _, fragment = match.group(1).partition("#")
        landed = resolve(document.path, path) if path else None
        hosting = next((block.environment for block in blocks if block.start <= number < block.end), None)
        found.append(
            Anchor(
                document=document.path,
                line=number,
                reference_type=match.group(2),
                href=match.group(1),
                target=landed if landed in tree.documents else None,
                fragment=fragment,
                label=match.group(3),
                hosting_environment=hosting,
            )
        )
    return found


def _head_anchors(lines: Sequence[str], block: Block) -> frozenset[tuple[str, str]]:
    """Every anchor of the emphasis run opening ``block``'s content, by href and label.

    Point 12 puts that content two lines below the label line, and the reader
    renders ``\\begin{proof}``'s optional argument as the italic run it opens
    with. A block whose content opens with no such run yields nothing, which is
    what sends :func:`_adjacent_subject` looking.
    """
    match = _PROOF_HEAD_RE.match("\n".join(lines[block.start + 2 : block.end]))
    if match is None:
        return frozenset()
    return frozenset((found.group(1), found.group(3)) for found in ANCHOR_RE.finditer(match.group()))


def _adjacent_subject(proof: Block, blocks: Sequence[Block]) -> tuple[Block, ...]:
    """The block immediately above ``proof``, where that block states a result.

    **Immediately** — the block above, not the nearest claim-bearing one. A
    second proof in the same document is a block too, and reaching past it to
    the theorem the first proof already proved is how adjacency inverts an edge:
    measured on ``math.DG`` 2609.10523v1, a result the author stated in ordinary
    prose sits between the first proof and the second, and reaching past both
    bound the second proof to the lemma above them and closed a cycle with the
    first. Where the block above states no result the proof binds to nothing and
    what it draws on stays undirected.
    """
    above = [block for block in blocks if block.end <= proof.start]
    return (above[-1],) if above and above[-1].claim_bearing else ()


def _fragment_block(anchor: Anchor, blocks: Sequence[Block]) -> Block | None:
    """The claim-bearing block the anchor's fragment names, where it names one.

    Point 7 lands a rewritten anchor on the node that held its label, and stage
    B carries that label off the block's ``<span>``. Where the two agree the
    target is not a document but a stated result. The fragment is asked against
    the identifier the *target* declares, never against the author's own
    spelling, so ``\\label{bifurcation}`` on a theorem is as legal as
    ``\\label{thm:bif}``.
    """
    if not anchor.fragment:
        return None
    return next((block for block in blocks if block.claim_bearing and block.identifier == anchor.fragment), None)


def _proofs(tree: Tree, blocks: Mapping[str, Sequence[Block]], anchors: Mapping[str, Sequence[Anchor]]) -> list[Proof]:
    """Every proof block of the tree, bound to the blocks it establishes.

    Two arms, in this order. Where the author wrote ``\\begin{proof}[Proof of
    Theorem \\ref{thm:main}]`` the reader renders the optional argument as the
    block's opening emphasis run, with the reference rewritten to an ordinary
    anchor — so the proof *names* its subject, and may name one in another
    document. Where they wrote a bare ``\\begin{proof}`` the run carries no
    anchor and adjacency answers instead.

    **A head that names nothing resolvable binds nothing**, and that is not the
    same as having no head: the author pointed somewhere and this reading could
    not follow them, so falling back to the block above would answer a question
    they already answered differently.
    """
    found: list[Proof] = []
    for path, document_blocks in sorted(blocks.items()):
        lines = unquote(tree.documents[path].text).splitlines()
        for block in document_blocks:
            if block.environment.casefold() != PROOF_ENVIRONMENT:
                continue
            head = _head_anchors(lines, block)
            named: list[Block] = []
            for anchor in anchors.get(path, ()):
                if not (block.start <= anchor.line < block.end) or (anchor.href, anchor.label) not in head:
                    continue
                subject = _fragment_block(anchor, blocks.get(anchor.target, ())) if anchor.target else None
                if subject is not None:
                    named.append(subject)
            found.append(
                Proof(
                    document=path,
                    start=block.start,
                    end=block.end,
                    subjects=tuple(named) if head else _adjacent_subject(block, document_blocks),
                    head=head,
                )
            )
    return found


def _keys_only_rendering(keys: Sequence[str]) -> str:
    """A span's text where nothing was there to resolve it: the keys themselves.

    The form is the filter's, written where the caller told it no citeproc would
    run (``kb_docgraph/authored_blocks.lua``), and it is the whole discriminator
    for that state — a span whose text is anything else had citeproc behind it.
    """
    return "(" + "; ".join(keys) + ")"


def _unanswered_marker(key: str) -> str:
    """How citeproc marks a key its bibliography did not carry (point 10).

    Read inside the span rather than anchored to the parentheses around the
    whole citation: one span may hold several keys, and citeproc marks only the
    ones it could not answer, leaving them among renderings that resolved.
    """
    return f"**{key}?**"


def _citations(document: Document) -> list[Citation]:
    """Every citation this document renders, by the key it carries and what became of it.

    One span is one citation element and may carry several keys, so a state is
    read per key off that span's own rendering — never off a document-wide or
    build-wide condition, which is what merged two of the three states.

    Markers come off first: one lands inside a citation span's opening tag often
    enough to hide half the citations that matter, and the module docstring
    states which half and why.
    """
    text = unquote(strip_markers(document.text))
    found: list[Citation] = []
    for span in CITATION_SPAN_RE.finditer(text):
        keys = span.group("keys").split()
        line = text.count("\n", 0, span.start())
        # The reader hard-wraps, so the rendering is a logical line rather than a
        # physical one — the collapse ``_display`` runs, for the same reason.
        rendering = re.sub(r"\s+", " ", span.group("rendering")).strip()
        if rendering == _keys_only_rendering(keys):
            found += [Citation(document.path, key, CitationState.KEY_ONLY, line) for key in keys]
            continue
        found += [
            Citation(
                document.path,
                key,
                CitationState.UNANSWERED if _unanswered_marker(key) in rendering else CitationState.RESOLVED,
                line,
            )
            for key in keys
        ]
    return found


def _works(document: Document) -> list[Work]:
    """Every reference-list entry this document renders, one per work.

    A document carrying the volume's reference list is the only one that yields
    any. The entry's own markup is stripped and its wrapping collapsed, so the
    text is the sentence a reader sees rather than the div that carries it.
    """
    return [
        Work(document.path, match.group("key"), _collapse_entry(match.group("text")))
        for match in BIBLIOGRAPHY_ENTRY_RE.finditer(unquote(document.text))
    ]


def _collapse_entry(text: str) -> str:
    """A reference-list entry's rendered text as one line, its inner tags removed."""
    return re.sub(r"\s+", " ", re.sub(r"<[^>]*>", "", text)).strip()


def _grouped(items: Sequence[Block] | Sequence[Anchor]) -> dict[str, list]:
    """One reading grouped by document, each group in the scan's own order."""
    grouped: dict[str, list] = {}
    for item in items:
        grouped.setdefault(item.document, []).append(item)
    return grouped


def scan(tree: Tree) -> Inventory:
    """One pass over every document, then the one reading that spans them.

    Nothing here stops a build. The proof binding runs after the per-document
    loop rather than inside it because a proof may name its subject in another
    document, so it needs every document's blocks before any proof can be bound.
    """
    blocks: list[Block] = []
    fences: list[MathFence] = []
    anchors: list[Anchor] = []
    citations: list[Citation] = []
    works: list[Work] = []

    for path in sorted(tree.documents):
        document = tree.documents[path]
        found = _blocks(document)
        blocks += found
        fences += _fences(document)
        anchors += _anchors(document, found, tree)
        citations += _citations(document)
        works += _works(document)

    return Inventory(
        blocks=tuple(blocks),
        fences=tuple(fences),
        anchors=tuple(anchors),
        citations=tuple(citations),
        works=tuple(works),
        proofs=tuple(_proofs(tree, _grouped(blocks), _grouped(anchors))),
    )


#: Kept out of :class:`Inventory` because it is a report shape rather than a
#: reading: the census the run prints so a discrepancy against a measured corpus
#: is visible rather than reconciled silently.
@dataclass(frozen=True)
class Census:
    blocks: int
    claim_blocks: int
    environments: Mapping[str, int]
    hosting_documents: int
    fences: int
    equation_labels: int
    anchors: int
    resolved_anchors: int
    eqref_anchors: int
    #: One key per state an inline citation can end in — every state present, so
    #: a state with no instances reads zero rather than going missing — plus the
    #: works the volume's own reference list carries, which are not citations and
    #: are not counted as any.
    citations: Mapping[str, int] = field(default_factory=dict)
    #: The environment names this package classifies neither way — the datum a
    #: sweep over an unfamiliar corpus is collecting, and the reason an
    #: unclassified name no longer stops the build.
    unclassified: Mapping[str, int] = field(default_factory=dict)
    #: Per document, the result-stating blocks whose display line yielded neither
    #: a title nor a locator. Each is a claim the graph does not carry, so this
    #: line is what keeps the block's absence from being a silent one — the
    #: reason such a block no longer stops the build.
    unreadable: Mapping[str, int] = field(default_factory=dict)


def census(inventory: Inventory) -> Census:
    return Census(
        blocks=len(inventory.blocks),
        claim_blocks=len(inventory.claim_blocks()),
        environments=inventory.environments(),
        hosting_documents=len(inventory.hosting_documents()),
        fences=len(inventory.fences),
        equation_labels=sum(len(fence.labels) for fence in inventory.fences),
        anchors=len(inventory.anchors),
        resolved_anchors=sum(1 for anchor in inventory.anchors if anchor.target is not None),
        eqref_anchors=sum(1 for anchor in inventory.anchors if anchor.reference_type == "eqref"),
        citations={
            **{
                f"inline-{state.value}": sum(1 for citation in inventory.citations if citation.state is state)
                for state in CitationState
            },
            "reference-list": len(inventory.works),
        },
        unclassified=inventory.unclassified_environments(),
        unreadable=inventory.unreadable_display_lines(),
    )
