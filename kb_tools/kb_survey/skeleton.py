"""The KB skeleton, derived from the survey manifest — mechanically, down to the leaves.

The document's own segmentation decides the tree. A manifest section is a node;
a section holding no subsections is a **leaf**, a section holding subsections is
an **index** that still carries its own material, and the hierarchy between them
is ``parent_id``'s. Nothing here judges what it finds and nothing here is
authored: the same manifest yields the same tree, byte for byte, so a skeleton is
re-derived rather than persisted and read back.

**Every section is placed exactly once, and every placed node names the source it
came from.** That is a property of the walk below rather than a rule someone
follows, and :func:`partition_defects` is the audit — a derivation compared
against its own input, which is why it costs one pass over ``sections[]`` and no
document has to be opened. A defect here is a fault in this module, never a
finding about a corpus.

**The shape of the tree.**

* ``entry-point.md`` is the root, and belongs to no volume.
* Each entry file owns one **domain**: a directory named by
  :func:`kb_tools.kb_survey.manifest.volume_slug`, with its own ``index.md``. A source
  file two entry files reach is composed once per entry and so yields one
  section record per entry (``manifest.py``, ``sections[].entry_file``); each
  copy is placed under its own volume, which is the coverage-correct answer
  rather than a duplicate.
* A volume's synthetic document root — the record owning front matter, ``level``
  ``document`` — **is** that volume's ``index.md``. Where a volume has none, the
  volume index is a node bound to no section: a directory needs a node whether or
  not the source put material before its first sectioning command.
* Every other section is a document under its parent's directory: a terminal
  section at ``<parent>/<slug>.md``, a subdivided one at
  ``<parent>/<slug>/index.md``.

**An index node carries a section too.** A subdivided section's own extent is the
material between its heading and its first subsection — the lead-in a reader
meets first — and it belongs in that section's index document rather than
nowhere. This is what lets the partition be a bijection from ``sections[]`` onto
the nodes that name one, instead of quietly dropping every interior section's
prose.

**Path segments are slugged from titles, not from ids.** A manifest id is
deliberately unstable across source edits (``manifest.py``, Identity) — inserting
a section renumbers its siblings — and a KB path is durable. A title survives
what an ordinal does not. Collisions inside one directory, empty slugs and names
the indexer excludes are resolved by :func:`_distinct`, so no *section* can land
at a path leaf discovery would not see. A volume directory is deliberately not
disambiguated the same way — the name is the operator's, and `p1.exclude-fit`
surfaces an excluded one instead of this renaming it behind them.

Stdlib only. This module reads no LaTeX, opens no file, and imports no driver.
"""

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum

from .. import kb_index_lib
from ..kb_index_lib import ENTRY_POINT_FILENAME, INDEX_FILENAME
from .manifest import Manifest, OriginRun, Section, volume_slug

#: The synthetic document root's ``level``, as ``harvest`` mints it. A volume's
#: front-matter record is the one section that becomes its volume index rather
#: than a document under it, and this is how it is recognized.
DOCUMENT_LEVEL = "document"

#: How long one path segment may run. A title is prose and a path segment is
#: not: an unbounded slug turns a wrapped ``\paragraph`` heading into a
#: filesystem path nobody can read or type. Truncation is at a hyphen so a
#: segment never ends mid-word, and :func:`_distinct` resolves whatever collides.
SLUG_MAX_CHARS = 48

_NON_SLUG = re.compile(r"[^a-z0-9]+")

#: What a section whose title slugs to nothing is called — one carrying no
#: alphanumeric at all, ``\section{$+$}``. It has a real extent and a real place
#: in the tree; only its name is unusable.
UNTITLED_SLUG = "untitled"

#: The stem an ``index.md`` occupies. A section slugging to it would put a leaf
#: at the path its own parent's index already holds, so it is reserved here
#: rather than discovered as a collision later.
_INDEX_STEM = INDEX_FILENAME.removesuffix(".md")


class NodeKind(StrEnum):
    """A node's structural position. Not a claim-graph flavour — SPEC.md, What a KB Is."""

    ENTRY_POINT = "entry-point"
    INDEX = "index"
    LEAF = "leaf"


@dataclass(frozen=True)
class Node:
    """One derived document: where it lands, and the section it was derived from.

    ``section_id`` is ``None`` for exactly two shapes, both of them structural:
    the entry point, and a volume index for a source that put no material before
    its first sectioning command. Every other node names its section, and
    ``origin_runs`` is that section's own extent — so a seat writing this
    document is told where to read, whether it is writing a leaf or an index.
    """

    path: str
    kind: NodeKind
    title: str
    parent_path: str | None
    domain: str
    section_id: str | None = None
    entry_file: str | None = None
    origin_runs: tuple[OriginRun, ...] = ()


@dataclass(frozen=True)
class Skeleton:
    """The derived tree, in document order beneath a root that sorts first."""

    nodes: tuple[Node, ...]

    @property
    def paths(self) -> tuple[str, ...]:
        return tuple(node.path for node in self.nodes)

    @property
    def leaves(self) -> tuple[Node, ...]:
        return tuple(node for node in self.nodes if node.kind is NodeKind.LEAF)

    @property
    def indexes(self) -> tuple[Node, ...]:
        """Every index document, deepest first — the order they must be written in.

        An index summarises the documents directly beneath it, so everything
        below one exists before it is briefed. ``entry-point.md`` is not here:
        it is its own row.
        """
        return tuple(
            sorted(
                (node for node in self.nodes if node.kind is NodeKind.INDEX),
                key=lambda node: (-node.path.count("/"), node.path),
            )
        )

    @property
    def domains(self) -> Mapping[str, tuple[str, ...]]:
        """Domain → the kb-root-relative prefixes it owns, in first-appearance order.

        One domain per source volume, owning one prefix: the partition the
        build-out waves batch on. Derived rather than declared, so a leaf under
        no domain is not a state this can produce.
        """
        found: dict[str, tuple[str, ...]] = {}
        for node in self.nodes:
            if node.domain:
                found.setdefault(node.domain, (node.domain,))
        return found

    def by_section(self) -> Mapping[str, Node]:
        """Section id → the one node derived from it."""
        return {node.section_id: node for node in self.nodes if node.section_id is not None}


# --- slugging ----------------------------------------------------------------


def slug(title: str) -> str:
    """One path segment from one title. Deterministic, and never empty.

    Truncation trims back to the last hyphen so a segment never ends mid-word —
    but only where truncation actually happened, or every short title would lose
    its final word to a rule about long ones.
    """
    flattened = _NON_SLUG.sub("-", title.strip().lower()).strip("-")
    if len(flattened) <= SLUG_MAX_CHARS:
        return flattened or UNTITLED_SLUG
    cut = flattened[:SLUG_MAX_CHARS]
    return (cut.rsplit("-", 1)[0] if "-" in cut else cut).strip("-") or UNTITLED_SLUG


def _reserved(candidate: str) -> bool:
    """Is this segment one leaf discovery would not see, or one a directory owns?

    ``EXCLUDE_NAMES`` and ``EXCLUDE_DIRS`` are the indexer's own vocabulary. A
    node placed at one of them is invisible to every walk the toolchain makes
    over a KB — a document that passes every mechanical gate by not existing as
    far as they are concerned.
    """
    return (
        candidate == _INDEX_STEM
        or candidate in kb_index_lib.EXCLUDE_DIRS
        or f"{candidate}.md" in kb_index_lib.EXCLUDE_NAMES
        or candidate in kb_index_lib.EXCLUDE_NAMES
    )


def _distinct(base: str, *, directory: str, taken: set[str], ordinal: int) -> str:
    """``base``, or the first suffixed form of it that is neither reserved nor taken.

    ``ordinal`` disambiguates first because it is a fact about the document —
    two sections sharing a title keep paths that say which came first — and the
    counter after it exists only for the case where even that collides.
    """
    for candidate in (base, f"{base}-{ordinal}", *(f"{base}-{ordinal}-{n}" for n in range(2, 1000))):
        if not _reserved(candidate) and f"{directory}/{candidate}" not in taken:
            taken.add(f"{directory}/{candidate}")
            return candidate
    raise ValueError(f"no distinct path segment for {base!r} under {directory!r}")


def _place(section: Section, *, directory: str, taken: set[str]) -> str:
    """The segment this section takes inside ``directory``, distinct from its siblings."""
    return _distinct(slug(section.title), directory=directory, taken=taken, ordinal=section.sibling_ordinal)


# --- the derivation ----------------------------------------------------------


def derive(manifest: Manifest) -> Skeleton:
    """The KB skeleton this manifest's own segmentation implies.

    One walk in document order, so the returned tuple is the order the sections
    appear in — which is what makes the rendering below readable and the
    assignment tables stable across runs.
    """
    children: dict[str | None, list[Section]] = {}
    for section in manifest.sections:
        children.setdefault(section.parent_id, []).append(section)

    nodes: list[Node] = [
        Node(
            path=ENTRY_POINT_FILENAME,
            kind=NodeKind.ENTRY_POINT,
            title="Entry point",
            parent_path=None,
            domain="",
        )
    ]
    taken: set[str] = set()

    for entry_file in _entry_files(manifest):
        # **A volume's directory is not disambiguated, and a section's is.** A
        # section title is one of hundreds and a mangled slug is invisible; a
        # volume name is the operator's, and it becomes the domain every path
        # under it carries. A source called `Session.tex` therefore lands a
        # whole domain under a name `EXCLUDE_DIRS` hides, and `p1.exclude-fit`
        # raises it for a person to answer rather than this renaming it behind
        # them.
        domain = volume_slug(entry_file)
        volume_index = f"{domain}/{INDEX_FILENAME}"
        roots = [section for section in children.get(None, []) if section.entry_file == entry_file]
        front_matter = next((section for section in roots if section.level == DOCUMENT_LEVEL), None)
        nodes.append(
            Node(
                path=volume_index,
                kind=NodeKind.INDEX,
                # The volume's own name, whether or not a front-matter record
                # stands behind the node: that record's title is `harvest`'s
                # synthetic placeholder and names nothing a reader would know.
                title=entry_file,
                parent_path=ENTRY_POINT_FILENAME,
                domain=domain,
                section_id=None if front_matter is None else front_matter.id,
                entry_file=entry_file,
                origin_runs=() if front_matter is None else tuple(front_matter.origin_runs),
            )
        )
        for section in roots:
            if section is front_matter:
                continue
            _walk(section, children, nodes=nodes, taken=taken, directory=domain, parent=volume_index, domain=domain)
        if front_matter is not None:
            # The front-matter record's own children are orphan subsections
            # `harvest` attached to it. They hang from the volume index, which is
            # the document that record became.
            for section in children.get(front_matter.id, []):
                _walk(section, children, nodes=nodes, taken=taken, directory=domain, parent=volume_index, domain=domain)

    return Skeleton(nodes=tuple(nodes))


def _entry_files(manifest: Manifest) -> tuple[str, ...]:
    """The volumes to build domains for, in the order the run declared them.

    ``run.entry_files`` rather than the distinct ``sections[].entry_file`` values:
    a source with no sectioning at all still declares a volume, and a domain that
    exists only where the parser found a heading would leave that source's front
    matter with nowhere to go.
    """
    return tuple(dict.fromkeys(manifest.run.entry_files))


def _walk(
    section: Section,
    children: Mapping[str | None, Sequence[Section]],
    *,
    nodes: list[Node],
    taken: set[str],
    directory: str,
    parent: str,
    domain: str,
) -> None:
    """Place ``section``, then everything beneath it. Depth-first, document order."""
    own = children.get(section.id, ())
    segment = _place(section, directory=directory, taken=taken)
    if own:
        path = f"{directory}/{segment}/{INDEX_FILENAME}"
        kind = NodeKind.INDEX
    else:
        path = f"{directory}/{segment}.md"
        kind = NodeKind.LEAF
    nodes.append(
        Node(
            path=path,
            kind=kind,
            title=section.title,
            parent_path=parent,
            domain=domain,
            section_id=section.id,
            entry_file=section.entry_file,
            origin_runs=tuple(section.origin_runs),
        )
    )
    for child in own:
        _walk(
            child,
            children,
            nodes=nodes,
            taken=taken,
            directory=f"{directory}/{segment}",
            parent=path,
            domain=domain,
        )


# --- the audit ---------------------------------------------------------------


def partition_defects(manifest: Manifest, skeleton: Skeleton) -> tuple[str, ...]:
    """Where the derivation and its own input disagree. Empty means the partition holds.

    Three questions, and they are the whole of it: is every section placed, is
    any section placed twice, and does any node name a section the manifest does
    not hold. Cheap because it compares a derivation against the records it was
    derived from — there is no document to open and no claim of a model's to
    corroborate.

    A non-empty result is a defect in :func:`derive`, not a finding about the
    corpus, so its consumer stops rather than routing it to a review.
    """
    declared = [section.id for section in manifest.sections]
    placed: dict[str, list[str]] = {}
    for node in skeleton.nodes:
        if node.section_id is not None:
            placed.setdefault(node.section_id, []).append(node.path)

    known = set(declared)
    defects = [
        f"{section_id} is placed at {len(paths)} paths ({', '.join(sorted(paths))}); a section becomes one document"
        for section_id, paths in sorted(placed.items())
        if len(paths) > 1
    ]
    defects += [
        f"{section_id} is placed at no path, so its source reaches no document"
        for section_id in declared
        if section_id not in placed
    ]
    defects += [
        f"{node.path} names section {node.section_id}, which the manifest does not hold"
        for node in skeleton.nodes
        if node.section_id is not None and node.section_id not in known
    ]
    return tuple(defects)


# --- the judgment seam -------------------------------------------------------


def sections_needing_recut(skeleton: Skeleton) -> tuple[str, ...]:
    """The sections whose derived placement wants a judgement call, in path order.

    **Today: none, always.** The document's own segmentation is taken as it
    stands, and the skeleton goes forward unaltered.

    This is the heuristic half of the stage that stands between the derivation
    and everything downstream. What it returns is a worklist: the sections whose
    placement a model would be asked to rule on, after which the ruled-on
    sections and the accepted ones recombine and the combined skeleton goes
    forward. Nothing goes backwards — a re-cut is work done on this pass, not a
    return to the derivation.

    The brief, the return format, the retry policy and the merge are not
    designed, so they are not written: the driver's row refuses a non-empty
    result by name rather than carrying a plausible-looking path through a
    mechanism nobody has specified. A heuristic added here therefore arrives at
    one known point, with one known consumer to build behind it.
    """
    del skeleton
    return ()


# --- the rendering -----------------------------------------------------------


def _render_runs(runs: Sequence[OriginRun]) -> str:
    return ", ".join(f"{run.file}:{run.line_start}-{run.line_end}" for run in runs)


def render(skeleton: Skeleton) -> str:
    """One line per node — ``path · kind · title · source`` — as a brief slot carries it.

    The tree as text, produced at the point of use and never persisted beside
    the manifest, for the reason the manifest's own joins are not: a second copy
    of a derivation drifts from it. Every line names where the document's
    material comes from, so the list is also the trace.
    """
    return "\n".join(
        f"{node.path} · {node.kind} · {' '.join(node.title.split())} · "
        f"{_render_runs(node.origin_runs) if node.origin_runs else '(no source of its own)'}"
        for node in skeleton.nodes
    )
