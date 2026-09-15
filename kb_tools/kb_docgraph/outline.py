"""Stage 2 — the AST is the index, the Markdown is the content.

The stage zips two sequences produced by the same source: the AST's ``Header``
elements give an ordered list of levels and labels, and the rendered Markdown,
split on its headings in document order, gives each node its body. Level and
label come from the first; text comes from the second. **No node's content is
re-rendered from an AST slice.**

The reason is not economy. A whole-document render computes theorem numbering,
citation rendering and the bibliography once, *across* the document; rendering N
slices independently invites each to be numbered in its own universe, and the
defect would be a plausible-looking *Theorem 1* in every leaf.

The AST is needed for exactly one thing the Markdown cannot supply: **heading
labels**. A Div's identifier survives into gfm as literal HTML, but a heading
renders as ``# Text`` with nowhere to put one, so a section's ``\\label`` exists
only in the AST.

**One block is lifted out of the content stream before the split, and it is not
an elision.** Citeproc appends the bibliography at the end of the rendering,
where a split at headings would leave it inside whatever section the source
happened to end with — a placement that is incidental and that moves the day an
author adds a section. It is cut out here and becomes ``<volume>/references.md``,
a leaf under the volume index. The words are relocated, not dropped, so the
partition still accounts for every one of them.

**A document that has children carries no source of its own.** The prose a
section owns ahead of its first subsection, and the volume's abstract and
lead-in ahead of its first heading, are source like any other extent — so they
become a leaf of that node rather than staying in a container. An index that
holds source is a document a reader has to open to find out whether it is a
signpost or an argument, and every consumer downstream then has to ask the same
question of every index it walks. What a container keeps is its own heading,
its up-link, its child list, and whatever summary a later stage writes below
them.

**The heading stays; only the prose beneath it moves.** A container's heading is
what names it to a reader who opened it, and nothing downstream puts one back —
so a container that gave its heading away would be a document with no title for
good, and the tree would hold two shapes of index depending on whether a section
happened to write a lead-in. The leaf gets a supplied heading of its own, the
way the reference list does.

That supplied heading is why :func:`_supplied_titles` counts one per lifted
leaf. **The rule there is that the accounting balances**: a title this stage
supplies is listed exactly as many times as a segment carries it, which is what
keeps the lift a relocation rather than a word the split invented.

**The two sequences are asserted aligned before either is used** — equal count,
matching levels, matching titles, in order. They are the same headings from the
same source, so a misalignment means a filter added or dropped one, and it fails
loudly rather than shifting every label silently onto the wrong node.
"""

import posixpath
import re
from dataclasses import dataclass, field
from pathlib import Path

from .. import kb_index_lib
from ..kb_index_lib import INDEX_FILENAME, UPLINK_MARKER
from ..kb_survey.skeleton import slug
from .text import Heading, github_anchor, headings, markdown_tokens
from .walk import Header, Outline

#: Frontmatter entries that state the volume's claims or name it. Retained: an
#: abstract is not apparatus, it states what the paper claims.
CONTENT_KEYS = frozenset({"abstract", "title"})

#: Frontmatter entries elided as presentation apparatus (point 13). A byline, the
#: institutional address that travels with it, and a draft date bear on the
#: logical construction of nothing, and the only attribution a claim graph needs
#: is which work proved a cited result — the bibliography's job, not the volume
#: byline's. ``bibliography`` names the file this stage itself passed on the
#: command line, so what it would carry into a document is an invocation argument
#: rather than a word of the source.
#:
#: **The list enumerates; point 13's criterion is what classifies.** ``amsart``
#: ships seven more byline macros — ``\curraddr``, ``\email``, ``\urladdr``,
#: ``\thanks``, ``\dedicatory``, ``\subjclass``, ``\keywords`` — and pandoc 3.11
#: lifts none of them into ``meta``, so an entry for one here would classify a key
#: no reader emits. They are absent because nothing has produced them, not
#: because the criterion is unclear about them.
APPARATUS_KEYS = frozenset({"address", "author", "date", "bibliography"})

#: A top-level key of the YAML block ``-s`` puts at the head of the rendering.
_FRONTMATTER_KEY = re.compile(r"^([A-Za-z][A-Za-z0-9_-]*):(.*)$")

#: An anchor element's own ``href``. Cross-references reach gfm as ``<a>``
#: elements carrying the label as a fragment; rewriting the attribute in place
#: leaves the reader-visible text exactly as pandoc rendered it.
_HREF = re.compile(r'(<a\b[^<>]*?\bhref=")#([^"]*)(")')

#: The element pandoc emits for an external image inside a figure. Pandoc emits
#: no Markdown image for ``\includegraphics`` — point 11's self-linking form is
#: constructed here, not extracted.
_EMBED = re.compile(r"<(?:embed|img)\b[^<>]*?\bsrc=\"([^\"]+)\"[^<>]*/?>")

#: Where a volume's copied image assets land, under the volume's own directory.
ASSET_DIRNAME = "assets"

#: The reference list's own document, under the volume index. The title has no
#: source heading behind it — citeproc's block carries none — so this stage
#: supplies one, the way it supplies a volume's title when the source declares
#: none. ``References`` is what the marker itself says (``id="refs"``,
#: ``class="references"``) and it slugs to the segment the path already wanted,
#: so the document's name and its location read the same.
REFERENCES_TITLE = "References"

#: The leaf a container's own prose is lifted into. A constant and not the
#: section's own heading text, which would title the leaf as if it were the whole
#: section — the confusion between a container and a leaf is what this lift exists
#: to remove, and repeating the section's name in its child list would reintroduce
#: it under a different path. The section keeps its own heading, so a reader who
#: opened the index is told which section this is the overview of.
#:
#: Like :data:`REFERENCES_TITLE` this stage supplies it: it heads a leaf no source
#: heading stands behind. So it heads that leaf's segment *and* is accounted in
#: :func:`_supplied_titles`, once per leaf emitted — the two go together, and a
#: change to either alone is what makes check B report an invented or a dropped
#: word.
OWN_PROSE_TITLE = "Overview"

#: Citeproc's bibliography, keyed on the marker citeproc itself puts on it: the
#: identifier ``refs`` and the class ``csl-bib-body``. Pandoc names this block
#: and nothing else does — an author's own Div reaches gfm classed with the
#: author's own name — so this is a named-marker test of the same kind as the
#: title-page elision, not a heuristic about what a trailing block looks like.
_BIBLIOGRAPHY = re.compile(r'^<div\b[^<>]*\bid="refs"[^<>]*\bclass="[^"]*\bcsl-bib-body\b[^"]*"[^<>]*>$')

#: A native Div's delimiters as the gfm writer spells them: at column zero,
#: alone on the line. The bibliography nests one Div per entry, so its close is
#: reached by counting rather than by taking the first one found.
_DIV_OPEN = re.compile(r"^<div\b")
_DIV_CLOSE = re.compile(r"^</div>$")

#: An identifier on one of those Divs. Every cross-reference target the
#: bibliography declares is one of these, which is how the labels move to the
#: document their content moved to.
_DIV_ID = re.compile(r'^<div\b[^<>]*\bid="([^"]*)"', re.MULTILINE)

_INDEX_STEM = INDEX_FILENAME.removesuffix(".md")


class AlignmentError(RuntimeError):
    """The AST's header sequence and the Markdown's headings disagree."""


class BibliographyError(RuntimeError):
    """The bibliography Div opens in the rendering and never closes."""


class DepthError(RuntimeError):
    """A volume's realized tree depth is not its count of distinct heading levels."""


class FrontmatterError(RuntimeError):
    """A metadata key with no classification. The classification list is closed."""


@dataclass
class Document:
    """One node of the tree: where it lands, what it holds, and who it hangs from."""

    title: str
    rank: int
    segment: str
    parent: "Document | None" = None
    children: list["Document"] = field(default_factory=list)
    path: str = ""
    body: str = ""
    label: str | None = None

    @property
    def anchor(self) -> str:
        return github_anchor(self.title)

    def walk(self) -> list["Document"]:
        return [self, *(node for child in self.children for node in child.walk())]


@dataclass
class VolumeTree:
    """One volume's documents, plus what the checks and the writer need from it."""

    stem: str
    title: str
    index: Document
    label_paths: dict[str, str]
    header_labels: set[str]
    content_tokens: list[str]
    distinct_levels: int
    assets: dict[str, str]
    missing_assets: list[str]

    @property
    def documents(self) -> list[Document]:
        return self.index.walk()


@dataclass(frozen=True)
class Frontmatter:
    """The YAML block at the head of the rendering, split by point 13's rule."""

    values: dict[str, str]
    body: str

    @property
    def title(self) -> str:
        """The volume's name, on one line.

        ``\\title{A\\\\B}`` renders as two lines; a link's text and a heading are
        each one, so the break is closed up here rather than at the three places
        that would otherwise each have to remember to.
        """
        return " ".join(self.values.get("title", "").split())

    @property
    def abstract(self) -> str:
        return self.values.get("abstract", "")


def parse_frontmatter(markdown: str) -> Frontmatter:
    """Split the ``-s`` metadata block off the rendering and classify its keys.

    The classification is closed and total: a key that is neither content nor
    apparatus stops the build naming itself, because the alternative is a
    metadata channel silently reaching no document at all — which is one of the
    ways pandoc was already found to lose an abstract.

    Every value is unquoted (:func:`_unquote`) before it is stored, because what
    an emitter quotes is its own decision and the quotes are not part of the
    value.
    """
    lines = markdown.splitlines()
    if not lines or lines[0].strip() != "---":
        return Frontmatter(values={}, body=markdown)
    closing = next((number for number in range(1, len(lines)) if lines[number].strip() == "---"), None)
    if closing is None:
        return Frontmatter(values={}, body=markdown)

    values: dict[str, str] = {}
    key: str | None = None
    collected: list[str] = []
    for line in lines[1:closing]:
        found = _FRONTMATTER_KEY.match(line)
        if found:
            if key is not None:
                values[key] = _unquote(_flatten(collected))
            key, collected = found.group(1), [found.group(2)]
        elif key is not None:
            collected.append(line)
    if key is not None:
        values[key] = _unquote(_flatten(collected))

    unknown = sorted(set(values) - CONTENT_KEYS - APPARATUS_KEYS)
    if unknown:
        raise FrontmatterError(
            f"metadata key(s) {unknown} are neither content nor apparatus. Point 13's split is a closed "
            "list and widening it is a plan change, not a code change — classify them there first."
        )
    return Frontmatter(values=values, body="\n".join(lines[closing + 1 :]).lstrip("\n"))


#: The escapes a YAML emitter writes inside a double-quoted scalar. An escape
#: outside this table keeps its backslash rather than being guessed at: dropping
#: it would silently alter a value, which is the class of defect this whole
#: function exists to close.
_YAML_ESCAPES = {
    "\\": "\\",
    '"': '"',
    "/": "/",
    "n": "\n",
    "t": "\t",
    "r": "\r",
    "b": "\b",
    "f": "\f",
    "0": "\0",
    " ": " ",
}

_YAML_ESCAPE_RE = re.compile(r"\\(u[0-9a-fA-F]{4}|x[0-9a-fA-F]{2}|.)", re.DOTALL)


def _unescape_double_quoted(value: str) -> str:
    def replace(match: re.Match[str]) -> str:
        escape = match.group(1)
        if escape[0] in "ux" and len(escape) > 1:
            return chr(int(escape[1:], 16))
        return _YAML_ESCAPES.get(escape, match.group(0))

    return _YAML_ESCAPE_RE.sub(replace, value)


def _unquote(value: str) -> str:
    """One metadata value as its own text, whatever quoting the emitter chose.

    **A YAML emitter quotes a scalar whenever the plain form would not parse
    back**, and a reader that keeps the quotes has a different string from the
    one the source declared. ``\\title{Mary and Her Lamb: An Account}`` is the
    ordinary case: a title containing ``": "`` must be quoted, and the quotes
    then reach the volume's heading, its entry-point link text and its directory
    name. Nothing downstream can undo that — a link whose text is quoted is
    *the citation gate's own excerpt grammar*, so the failure arrives as a
    citation-grammar violation naming nothing resembling a title.

    **The class, not the case.** Any value containing ``#``, leading or trailing
    space, or text that would otherwise parse as a number, a boolean or a date
    gets the same treatment from the emitter and had the same defect.

    Three forms, which is all an emitter produces: double-quoted with backslash
    escapes, single-quoted with ``''`` for a literal quote, and plain. A plain
    scalar cannot begin with either quote character — YAML makes both
    indicators — so the opening character is what decides, and the closing one
    is checked only to leave malformed input alone rather than truncate it.
    Block scalars never reach here quoted; :func:`_flatten` has already dropped
    their ``|`` header and returned their lines as they stand.
    """
    if len(value) < 2 or value[0] != value[-1]:
        return value
    if value[0] == '"':
        return _unescape_double_quoted(value[1:-1])
    if value[0] == "'":
        return value[1:-1].replace("''", "'")
    return value


def _flatten(collected: list[str]) -> str:
    """One entry's value as text: block scalars dedented, list dashes dropped."""
    first = collected[0].strip()
    rest = [line.strip() for line in collected[1:]]
    if first in {"|", ">", "|-", ">-"}:
        first = ""
    parts = [first, *(part.removeprefix("- ").rstrip("\\") for part in rest)]
    return "\n".join(part for part in parts if part)


def split_bibliography(body: str) -> tuple[str, str]:
    """``body`` without citeproc's reference list, and that list on its own.

    Citeproc appends the bibliography as a Div at the end of the content stream,
    so a split at headings leaves it inside whatever section the source happened
    to end with — a placement that is incidental and that moves the day an author
    adds a section. Lifting it here is what lets it become a document of its own.

    The words are **relocated, not elided**: both halves come back, and the
    caller accounts for both. A volume citeproc emitted no bibliography for gets
    an empty second half and no document.
    """
    lines = body.splitlines()
    start = next((number for number, line in enumerate(lines) if _BIBLIOGRAPHY.match(line)), None)
    if start is None:
        return body, ""
    depth = 0
    for end in range(start, len(lines)):
        if _DIV_OPEN.match(lines[end]):
            depth += 1
        elif _DIV_CLOSE.match(lines[end]):
            depth -= 1
            if depth == 0:
                return "\n".join(lines[:start] + lines[end + 1 :]), "\n".join(lines[start : end + 1])
    raise BibliographyError(
        f"the bibliography Div opens at line {start + 1} of the rendering and never closes. Splitting on the "
        "opener alone would put half a reference list in one document and half in another."
    )


def build_tree(*, stem: str, markdown: str, outline: Outline, volume_directory: Path) -> VolumeTree:
    """The whole of stage 2 for one volume: align, rank, lift, place, rewrite."""
    frontmatter = parse_frontmatter(markdown)
    content, bibliography = split_bibliography(frontmatter.body)
    found = headings(content)
    _assert_aligned(stem=stem, headers=outline.headers, found=found)

    title = frontmatter.title or stem
    ranks = {level: rank for rank, level in enumerate(sorted({header.level for header in outline.headers}), start=1)}
    lines = content.splitlines()

    preamble = "\n".join(lines[: found[0].start if found else len(lines)]).strip()
    index = Document(
        title=title,
        rank=0,
        segment="\n\n".join(part for part in (f"# {title}", frontmatter.abstract.strip(), preamble) if part),
    )
    stack = [index]
    ordered: list[Document] = []
    for position, (header, heading) in enumerate(zip(outline.headers, found)):
        stop = found[position + 1].start if position + 1 < len(found) else len(lines)
        rank = ranks[header.level]
        depth = min(rank, len(stack))
        del stack[depth:]
        node = Document(
            title=heading.text,
            rank=rank,
            segment="\n".join([f"# {heading.text}", *lines[heading.end : stop]]),
            parent=stack[-1],
            label=header.identifier or None,
        )
        stack[-1].children.append(node)
        stack.append(node)
        ordered.append(node)

    # Last in the index's child list because last in document order, which is
    # where citeproc appended it. It is not in ``ordered``: that sequence is the
    # zip against the AST's headers, and this document answers to no header.
    references = _references_document(bibliography, parent=index)
    if references is not None:
        index.children.append(references)
    own_prose = _lift_own_prose(index)

    directory = _volume_directory_name(title, stem)
    _assign_paths(index, directory=directory)
    realized = max((_depth_of(node) for node in ordered), default=0)
    if realized != len(ranks):
        raise DepthError(
            f"{stem}: tree is {realized} level(s) deep but the volume uses {len(ranks)} distinct heading "
            f"levels {sorted(ranks)} — point 2's ranking places one tree level per distinct heading level."
        )

    # Point 7 asks an anchor to land on the node that holds the label, and the
    # lift splits a section's labels in two. A heading's own identifier stays
    # with the heading, which stays with the container; everything else a section
    # declares before its first subsection — a Div or Span id, an equation's
    # in-source ``\label`` — sits in the prose, and follows it to the leaf. A
    # label past the first subsection heading maps to that subsection and neither
    # half reaches it.
    holders = {leaf.parent.path: leaf.path for leaf in own_prose if leaf.parent is not None}
    label_paths: dict[str, str] = {}
    for label, section in sorted(outline.label_section.items()):
        declared_in = index.path if section < 0 else ordered[section].path
        label_paths[label] = declared_in if label in outline.header_labels else holders.get(declared_in, declared_in)
    if references is not None:
        # The AST places these labels in the section the bibliography sat in.
        # Their content is in this document now, and point 7 asks an anchor to
        # land on the node that holds the label — so the labels move with it.
        label_paths.update({label: references.path for label in _DIV_ID.findall(bibliography)})
    anchors = {label: label for label in outline.anchored_labels}
    anchors.update({node.label: node.anchor for node in ordered if node.label})
    assets, missing = _collect_assets(index, volume_directory=volume_directory)
    for node in index.walk():
        node.body = _rewrite(node, label_paths=label_paths, anchors=anchors, assets=assets, directory=directory)

    return VolumeTree(
        stem=stem,
        title=title,
        index=index,
        label_paths=label_paths,
        header_labels=set(outline.header_labels),
        # The titles this stage supplies rather than reads off the source — the
        # volume's, which for a volume declaring none is its filename stem; the
        # reference list's, which answers to no heading at all; and one per leaf
        # the prose lift emitted — are accounted on both sides of the partition.
        # ``frontmatter.body`` is the rendering *before* the bibliography was
        # lifted out of it, which is what puts the lift itself under check B
        # rather than beside it.
        content_tokens=[
            token
            for name in _supplied_titles(title, bibliography, own_prose=len(own_prose))
            for token in markdown_tokens(name)
        ]
        + markdown_tokens(frontmatter.abstract)
        + markdown_tokens(frontmatter.body),
        distinct_levels=len(ranks),
        assets=assets,
        missing_assets=missing,
    )


def _references_document(bibliography: str, *, parent: Document) -> Document | None:
    """The reference list as a leaf under the volume index, or ``None``.

    A volume with no bibliography gets no document. An empty one would be a leaf
    a consumer has to open to learn it says nothing, and the tree would then
    assert that every volume cites something.
    """
    if not bibliography:
        return None
    return Document(
        title=REFERENCES_TITLE,
        rank=1,
        segment=f"# {REFERENCES_TITLE}\n\n{bibliography}",
        parent=parent,
    )


def _lift_own_prose(node: Document) -> list[Document]:
    """Depth-first: every node that has children gives the prose below its heading to a leaf.

    Returns the leaves emitted, in no particular order — the caller needs their
    count for :func:`_supplied_titles` and their parents for the label map.

    **The heading stays with the container and the prose moves.** A container
    that gave its heading away would render as an up-link and a child list and
    nothing else, and nothing downstream writes one back. The leaf carries
    :data:`OWN_PROSE_TITLE` as its own heading instead, the way the reference
    list carries one, and the prose beneath it is the container's byte for byte —
    so check B sees the same relocation the bibliography lift already is.

    The leaf goes **first** in the child list because its content came first: the
    prose a section owns runs from its heading to its first subsection, and point
    4 asks an index to list its children in the source's own document order.

    A node with nothing below its heading has nothing to lift and gets no leaf.
    That is the plain emptiness test :func:`_references_document` makes of a
    bibliography, not a rule about headings: what would be written is a leaf with
    a supplied title and no content, which is a document a consumer has to open
    to learn it says nothing.
    """
    lifted: list[Document] = []
    for child in node.children:
        lifted += _lift_own_prose(child)
    if not node.children:
        return lifted
    heading, _, prose = node.segment.partition("\n")
    if not prose.strip():
        return lifted
    leaf = Document(title=OWN_PROSE_TITLE, rank=node.rank + 1, segment=f"# {OWN_PROSE_TITLE}\n{prose}", parent=node)
    node.children.insert(0, leaf)
    node.segment = heading
    return [*lifted, leaf]


def _supplied_titles(title: str, bibliography: str, *, own_prose: int) -> list[str]:
    """Every title this stage supplies rather than reading off a source heading.

    Listed exactly as many times as a segment carries it — the volume's own title
    once, the reference list's once where citeproc rendered one, and
    :data:`OWN_PROSE_TITLE` once per leaf the lift emitted. **That balance is the
    rule**: these are the left side of check B, so a title in a segment and not
    here reads as a word the split invented, and one here and in no segment reads
    as a word it dropped.
    """
    return [title, *([REFERENCES_TITLE] if bibliography else []), *([OWN_PROSE_TITLE] * own_prose)]


def _assert_aligned(*, stem: str, headers: list[Header], found: list[Heading]) -> None:
    if len(headers) != len(found):
        raise AlignmentError(
            f"{stem}: the AST holds {len(headers)} headers and the rendering {len(found)} headings. "
            "Both come from the same source under the same filters, so a difference is a filter that "
            "added or dropped one — the labels would otherwise shift silently onto the wrong nodes."
        )
    for position, (header, heading) in enumerate(zip(headers, found)):
        if header.level != heading.level:
            raise AlignmentError(
                f"{stem}: header {position} is level {header.level} in the AST and {heading.level} in the rendering"
            )
        rendered = tuple(markdown_tokens(heading.text))
        if header.tokens != rendered:
            raise AlignmentError(
                f"{stem}: header {position} reads {list(header.tokens)} in the AST and {list(rendered)} "
                "in the rendering"
            )


def _depth_of(node: Document) -> int:
    depth = 0
    current = node
    while current.parent is not None:
        depth += 1
        current = current.parent
    return depth


def _volume_directory_name(title: str, stem: str) -> str:
    return slug(_plain(title)) or slug(stem)


def _assign_paths(index: Document, *, directory: str) -> None:
    index.path = f"{directory}/{INDEX_FILENAME}"
    _place_children(index, directory=directory)


def _place_children(parent: Document, *, directory: str) -> None:
    taken: set[str] = set()
    for ordinal, child in enumerate(parent.children, start=1):
        segment = _distinct(slug(_plain(child.title)), taken=taken, ordinal=ordinal)
        if child.children:
            child.path = f"{directory}/{segment}/{INDEX_FILENAME}"
            _place_children(child, directory=f"{directory}/{segment}")
        else:
            child.path = f"{directory}/{segment}.md"


def _distinct(base: str, *, taken: set[str], ordinal: int) -> str:
    """The first segment that is neither reserved nor already used in this directory.

    The ordinal disambiguates first because it is a fact about the document — two
    sections sharing a title keep paths that say which came first.
    """
    for candidate in (base, f"{base}-{ordinal}", *(f"{base}-{ordinal}-{n}" for n in range(2, 1000))):
        if not _reserved(candidate) and candidate not in taken:
            taken.add(candidate)
            return candidate
    raise ValueError(f"no distinct path segment for {base!r}")


def _reserved(candidate: str) -> bool:
    """A segment leaf discovery would not see, or one a directory already owns."""
    return (
        candidate == _INDEX_STEM
        or candidate in kb_index_lib.EXCLUDE_DIRS
        or candidate in kb_index_lib.EXCLUDE_NAMES
        or f"{candidate}.md" in kb_index_lib.EXCLUDE_NAMES
    )


def _plain(title: str) -> str:
    return " ".join(markdown_tokens(title))


def _collect_assets(index: Document, *, volume_directory: Path) -> tuple[dict[str, str], list[str]]:
    """Every external image the volume embeds: source spelling → tree-relative name.

    A reference to a file that is not on disk is recorded rather than resolved.
    Constructing a link to an image nobody can copy would put a dead target in
    the tree and report the cause as a broken link three checks later.
    """
    assets: dict[str, str] = {}
    missing: list[str] = []
    taken: set[str] = set()
    for node in index.walk():
        for source in _EMBED.findall(node.segment):
            if source in assets or source in missing:
                continue
            if not (volume_directory / source).is_file():
                missing.append(source)
                continue
            name = Path(source).name
            while name in taken:
                name = f"{Path(source).stem}-{len(taken)}{Path(source).suffix}"
            taken.add(name)
            assets[source] = name
    return assets, missing


def _rewrite(
    node: Document,
    *,
    label_paths: dict[str, str],
    anchors: dict[str, str],
    assets: dict[str, str],
    directory: str,
) -> str:
    """The node's body: its segment with anchors resolved and images constructed.

    Both transforms only ever *add*: an ``href`` lives inside a tag and carries no
    reader-visible word, and the constructed image sits beside the caption pandoc
    already rendered. That is what lets the partition check assert the body still
    holds every word of the segment it came from.

    A label the map does not hold is left exactly as pandoc wrote it, so a ``\\ref``
    to it renders as the raw label a reader can see — the same visibility standard
    an unresolvable citation key is held to, rather than a link that goes nowhere.
    """

    def resolve(match: re.Match[str]) -> str:
        label = match.group(2)
        target = label_paths.get(label)
        if target is None:
            return match.group(0)
        fragment = f"#{anchors[label]}" if label in anchors else ""
        return f"{match.group(1)}{_relative(node.path, target)}{fragment}{match.group(3)}"

    def embed(match: re.Match[str]) -> str:
        name = assets.get(match.group(1))
        if name is None:
            return match.group(0)
        target = _relative(node.path, f"{directory}/{ASSET_DIRNAME}/{name}")
        return f"{match.group(0)}\n\n[![]({target})]({target})"

    return _EMBED.sub(embed, _HREF.sub(resolve, node.segment))


def _relative(source: str, target: str) -> str:
    """``target`` as ``source`` must spell it. A self-reference is the file's own name."""
    return posixpath.relpath(target, posixpath.dirname(source))


def render(node: Document, *, tree_root: str) -> str:
    """The document as it lands on disk: up-link, body, child list.

    Navigation is composed here and nowhere else, which is what makes it
    separable from content: the partition check compares bodies, so a heading
    reaching both its own document and its parent's child list is not counted as
    duplication.
    """
    parts: list[str] = []
    parent_path = node.parent.path if node.parent is not None else tree_root
    parent_title = node.parent.title if node.parent is not None else "Knowledge Base"
    parts.append(f"[{UPLINK_MARKER} {parent_title}]({_relative(node.path, parent_path)})")
    parts.append(node.body.strip())
    if node.children:
        parts.append("\n".join(f"- [{child.title}]({_relative(node.path, child.path)})" for child in node.children))
    return "\n\n".join(part for part in parts if part) + "\n"
