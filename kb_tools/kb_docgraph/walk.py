"""Reading pandoc's JSON AST — the index the Markdown is split against.

The AST is word-granular: every word is a ``Str``, every gap a ``Space``. There
is no phrase in it to match, so everything here walks. A grep for a phrase over
the JSON returns zero and means nothing.

What this module reads off a document, and why each is needed:

* **the ``Header`` sequence** — the levels and the labels the gfm writer throws
  away. A heading renders as ``# Text`` with nowhere to put an identifier, so a
  section's ``\\label`` exists only here.
* **the identifiers a ``Div`` or a ``Figure`` carries**, and the ``\\label``
  inside display-maths source. Together with the headers these are every
  cross-reference target a volume declares.
* **the text runs of every leaf block, and of ``meta``** — what the partition
  check compares against the rendering.
* **the maths**, counted and carried, for point 9's claim.

``meta`` is walked **recursively and by node type**, never enumerated by
container: ``author`` is a ``MetaList`` of ``MetaInlines``, and a walk that
handles ``MetaBlocks`` and ``MetaInlines`` alone skips it without a word.

**A heading inside a blockquote is that blockquote's content and not a heading
of the document.** Point 12's author-distinguished block reaches this walk as a
``BlockQuote`` — the filter has already reshaped it — and an author who wrote
``\\paragraph{Step 1.}`` inside a proof wrote a step of that proof, not a
section of the volume. The rendering side agrees by the same rule: a quoted
heading line opens with ``>`` and :func:`text.headings` leaves it where it
sits, so the two sequences stage 2 zips are the same sequence rather than two
that happen to match. Reading it the other way would split the tree at *Step
1*, hang half a proof off the outline, and break point 12's rendered form for
the block it tore.
"""

import re
from collections.abc import Container, Iterator, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from .text import plain_tokens

#: Blocks that hold inlines directly. Everything else in a block position is a
#: container and is descended into, so a run never spans a list item boundary or
#: a table cell wall — the writer puts a marker or a pipe there and the AST does
#: not.
_LEAF_BLOCKS = frozenset({"Para", "Plain", "Header", "LineBlock", "CodeBlock", "RawBlock"})

#: Inlines that end a text run. Each is rendered as something other than its own
#: words — a maths span, an HTML fragment, an image — so the words on either side
#: of it are not contiguous in the output even though they are adjacent here.
_RUN_BREAKERS = frozenset({"Math", "RawInline", "Image", "Code", "Note"})

#: Nodes that carry an identifier a ``\ref`` can name. ``Table`` is the one whose
#: id does not survive the writer — gfm's pipe tables have no attribute syntax —
#: so a link to a table names the document that holds it and no fragment.
_IDENTIFIED_BLOCKS = frozenset({"Div", "Figure", "Span", "Table"})

#: ``\label{...}`` as it survives verbatim inside display-maths source. Point 7's
#: whole mechanism for equation labels: they never became AST nodes, so the only
#: place the name exists is the maths string.
_MATH_LABEL = re.compile(r"\\label\s*\{([^}]*)\}")

#: The environment an inline-drawn figure came from, read off a raw block once it
#: has already been identified as one. Naming the environment in the output is
#: not the same as keying identification on it.
_ENVIRONMENT = re.compile(r"\\begin\s*\{([^}]*)\}")


class UnhandledRawBlockError(RuntimeError):
    """A ``RawBlock`` whose treatment this stage has no measured answer for.

    Point 11's identification rule is structural — whatever pandoc returns as a
    raw block rather than as content is inline-drawn figure source — but what a
    drawing environment actually *arrives* as is unmeasured: the tracked corpus
    contains none of any kind. Guessing a treatment would write a rule nobody has
    seen fire. Raising names the block and stops.
    """


@dataclass(frozen=True)
class Header:
    """One ``Header``: what the Markdown cannot supply, plus what it can, for the zip."""

    level: int
    identifier: str
    tokens: tuple[str, ...]


@dataclass
class Outline:
    """Everything one AST walk yields, in document order.

    ``label_section`` maps every declared cross-reference target to the index of
    the header whose section holds it, ``-1`` for material before the first
    heading. That index is what binds a label to the document the splitter will
    put its section in.

    The other two sets say what a link to a label may *spell*. A header's anchor
    is derived from its text, because the writer discards the identifier; a Div,
    Span or Figure identifier reaches the output as a real HTML id and is spelled
    as itself; and an equation label reaches it as nothing at all — it survives
    only as text inside the maths — so a link to one names the document and stops
    there.
    """

    headers: list[Header] = field(default_factory=list)
    label_section: dict[str, int] = field(default_factory=dict)
    header_labels: set[str] = field(default_factory=set)
    anchored_labels: set[str] = field(default_factory=set)


def read_outline(document: Mapping[str, Any]) -> Outline:
    """Walk ``blocks`` once, in order, collecting the index stage 2 zips against."""
    outline = Outline()
    _walk_blocks(document["blocks"], outline, quoted=False)
    return outline


def _walk_blocks(node: Any, outline: Outline, *, quoted: bool) -> None:
    if isinstance(node, list):
        for member in node:
            _walk_blocks(member, outline, quoted=quoted)
        return
    if not isinstance(node, dict):
        return
    kind = node.get("t")
    if kind == "Header":
        level, attributes, inlines = node["c"]
        if quoted:
            # The block's content, not the document's outline. Its label still
            # names something the tree can reach — the document the block sits
            # in — so it maps to the enclosing section the way a Div's id does,
            # and joins neither ``headers`` nor ``header_labels``.
            if attributes[0]:
                outline.label_section[attributes[0]] = len(outline.headers) - 1
            _walk_blocks(inlines, outline, quoted=quoted)
            return
        outline.headers.append(Header(level=level, identifier=attributes[0], tokens=tuple(_inline_tokens(inlines))))
        if attributes[0]:
            outline.label_section[attributes[0]] = len(outline.headers) - 1
            outline.header_labels.add(attributes[0])
        _walk_blocks(inlines, outline, quoted=quoted)
        return
    if kind == "BlockQuote":
        # Only a heading reads differently inside one: an identifier a ``\ref``
        # can name still reaches ``anchored_labels`` from here, because point
        # 12's ``<span id="thm:bif">`` sits inside exactly this node.
        _walk_blocks(node["c"], outline, quoted=True)
        return
    if kind == "RawBlock":
        raise UnhandledRawBlockError(
            f"raw {node['c'][0]!r} block, no measured treatment: {node['c'][1][:200]!r}. "
            "Point 11 identifies inline-drawn figure source structurally, but what a drawing "
            "environment arrives as is unmeasured and this corpus holds none — resolve that "
            f"before this block is handled (environment read off it: {_environment_of(node['c'][1])!r})."
        )
    if kind in _IDENTIFIED_BLOCKS:
        identifier = node["c"][0][0]
        if identifier:
            outline.label_section[identifier] = len(outline.headers) - 1
            if kind != "Table":
                outline.anchored_labels.add(identifier)
    if kind == "Math":
        for label in _MATH_LABEL.findall(node["c"][1]):
            outline.label_section[label] = len(outline.headers) - 1
    for value in node.values():
        _walk_blocks(value, outline, quoted=quoted)


def _environment_of(raw: str) -> str | None:
    found = _ENVIRONMENT.search(raw)
    return found.group(1) if found else None


def math_texts(document: Mapping[str, Any], *, content_keys: Container[str]) -> list[str]:
    """Every ``Math`` element's LaTeX, from ``blocks`` and from content ``meta``.

    ``meta`` as well as ``blocks`` because an abstract is metadata and carries
    maths, and point 9's claim is about the volume rather than about its body.

    **``content_keys`` is the caller's, and the omission is point 13's answer
    rather than an exemption bought to make a paper pass.** Point 9 asks that
    every mathematical element reach a document; point 13 says apparatus enters
    no document at all, so a ``^{1}`` affiliation marker on a byline has no
    document to reach and nothing was lost when it did not. Passing the whole
    of ``meta`` reports the elision as a drop. The classification is
    :data:`outline.CONTENT_KEYS` and stays there — a walk that read the split
    would be a second reading of point 13, and the two would drift.
    """
    found: list[str] = []

    def descend(node: Any) -> None:
        if isinstance(node, list):
            for member in node:
                descend(member)
        elif isinstance(node, dict):
            if node.get("t") == "Math":
                found.append(node["c"][1])
                return
            for value in node.values():
                descend(value)

    descend(document["blocks"])
    descend({key: value for key, value in document["meta"].items() if key in content_keys})
    return found


def leaf_blocks(node: Any) -> Iterator[Mapping[str, Any]]:
    """Every block that holds inlines directly, footnote bodies included.

    A ``Note``'s blocks are yielded beside the block that carries the reference:
    the writer moves them to the foot of the document, so they reach the
    rendering, and a check that skipped them would not notice one going missing.
    """
    if isinstance(node, list):
        for member in node:
            yield from leaf_blocks(member)
        return
    if not isinstance(node, dict):
        return
    if node.get("t") in _LEAF_BLOCKS:
        yield node
        yield from leaf_blocks(_notes_within(node))
        return
    for value in node.values():
        yield from leaf_blocks(value)


def _notes_within(node: Any) -> list[Any]:
    found: list[Any] = []

    def descend(current: Any) -> None:
        if isinstance(current, list):
            for member in current:
                descend(member)
        elif isinstance(current, dict):
            if current.get("t") == "Note":
                found.extend(current["c"])
                return
            for value in current.values():
                descend(value)

    descend(node)
    return found


def text_runs(block: Mapping[str, Any]) -> list[list[str]]:
    """The block's maximal contiguous word runs.

    A run ends wherever the rendering puts something that is not these words —
    a maths span, an HTML fragment, a footnote marker. Whole-block text would be
    a run that the output interrupts and the check would report every maths span
    as a drop.
    """
    if block.get("t") == "CodeBlock":
        return [block["c"][1].split()]
    return _inline_runs(block.get("c"))


def _inline_runs(node: Any) -> list[list[str]]:
    runs: list[list[str]] = []
    pending: list[str] = []

    def flush() -> None:
        if pending:
            tokens = plain_tokens("".join(pending))
            if tokens:
                runs.append(tokens)
            pending.clear()

    def descend(current: Any) -> None:
        if isinstance(current, list):
            for member in current:
                descend(member)
            return
        if not isinstance(current, dict):
            return
        kind = current.get("t")
        if kind == "Str":
            pending.append(current["c"])
        elif kind in {"Space", "SoftBreak", "LineBreak"}:
            pending.append(" ")
        elif kind in _RUN_BREAKERS:
            flush()
        elif "c" in current:
            descend(current["c"])

    descend(node)
    flush()
    return runs


def _inline_tokens(inlines: Any) -> list[str]:
    return [token for run in _inline_runs(inlines) for token in run]


@dataclass(frozen=True)
class MetaEntry:
    """One content-bearing thing found under ``meta``, and where in it that was."""

    path: str
    key: str
    runs: tuple[tuple[str, ...], ...]


def meta_entries(meta: Mapping[str, Any]) -> list[MetaEntry]:
    """Every content-bearing entry of ``meta``, found by node type at any depth.

    Dispatch is on the node's own ``t`` and recursion is unconditional, so a
    ``MetaList`` of ``MetaInlines`` — which is what ``author`` is — is reached
    the same way a top-level ``MetaBlocks`` is. Enumerating the containers a
    corpus happens to use is how ``author`` goes unchecked.
    """
    entries: list[MetaEntry] = []

    def descend(node: Any, path: str, key: str) -> None:
        if not isinstance(node, dict):
            return
        kind = node.get("t")
        if kind == "MetaBlocks":
            runs = [run for block in leaf_blocks(node["c"]) for run in text_runs(block)]
            _record(entries, path, key, runs)
        elif kind == "MetaInlines":
            _record(entries, path, key, _inline_runs(node["c"]))
        elif kind == "MetaString":
            _record(entries, path, key, [plain_tokens(node["c"])])
        elif kind == "MetaList":
            for index, member in enumerate(node["c"]):
                descend(member, f"{path}[{index}]", key)
        elif kind == "MetaMap":
            for name, member in sorted(node["c"].items()):
                descend(member, f"{path}.{name}", key)

    for name, value in sorted(meta.items()):
        descend(value, name, name)
    return entries


def _record(entries: list[MetaEntry], path: str, key: str, runs: Sequence[Sequence[str]]) -> None:
    kept = tuple(tuple(run) for run in runs if run)
    if kept:
        entries.append(MetaEntry(path=path, key=key, runs=kept))
