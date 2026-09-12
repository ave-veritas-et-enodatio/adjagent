"""Op semantics for the claim-graph sheet: discovery, load, selection, report.

The op is :func:`render`. The ``render-claim-graph`` subcommand binds it, and
anything else that wants a sheet on disk calls it directly — it takes no
argparse, reads no ``sys.argv`` and calls no ``sys.exit``. It returns a
:class:`Result` carrying the exit code, the report lines and the path it wrote;
printing and exiting are the surface's.

:func:`compose` is the same work stopping one step short, at the markup: what a
caller that wants the bytes rather than the file asks for. ``verify``'s
freshness gate is that caller — it renders the sheet the index on disk would
produce and byte-compares it against the sheet beside it, the way it diffs a
dry-run rebuild of ``.index/`` against the files there.

**The only precondition is that the index loads.** No property of the graph is
one: zero edges, one node, every node isolated, a graph with nothing in it at
all — each is a true picture of the KB that produced it, and the seeded spine
whose sheet nobody can draw yet is the dependency-order failure this rules out.

**It reads ``.index/`` and emits one file.** Nothing else under ``kb-root/`` is
opened, and nothing under ``.index/`` is ever written — that tree is refresh's,
which regenerates it wholesale and which verify diffs a dry-run rebuild against.

**Where the sheet lands**: ``claim-graph.svg`` at the KB root, beside
``claim-quality.md``, unless ``out`` names another path. The sheet is a pure
derived view of ``.index/``, so it belongs beside the KB it derives from rather
than wherever the caller happened to be standing.

**The exit ladder**, against the house's existing meanings: ``0`` rendered,
``2`` the environment is unfit, ``7`` refused. **No graph content produces a
non-zero exit.** A cycle, an edge naming an id no record carries, an isolated
node and a whole disconnected component are the picture's subject; an instrument
that raises on the defect it exists to show is worse than no instrument.

**Two layers reach exit 2 and they do not overlap by accident.** This module
pre-checks the index directory itself, so the common failure is refused before a
load is attempted; and it maps the loader's own ``FileNotFoundError`` and
``ValueError`` to the same code **carrying that message unaltered** — it already
names the missing files and the refresh command to run, or the ``path:lineno``
of a malformed record, and rewriting it would lose the only detail the caller
can act on.

Beyond the package's own modules this reaches :mod:`kb_tools.kb_cmd.index` for
the load, :mod:`kb_tools.kb_util` for the KB path vocabulary and the report
tokens, and :mod:`kb_tools.kb_schema` for the node-kind census order.
"""

import os
from collections import Counter
from collections.abc import Collection, Mapping
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from kb_tools import kb_schema, kb_util
from kb_tools.kb_cmd import index as kb_index
from kb_tools.kb_graph import layout, model, style, svg

#: The report tag, taken from the op's own name so the two cannot drift.
REPORT_TAG = kb_util.OP_RENDER_CLAIM_GRAPH

#: Rendered. The sheet is at the reported path.
EXIT_RENDERED = 0
#: The environment is unfit: no readable ``.index/``, a record that will not
#: parse, an ``--out`` whose parent does not exist, an unwritable destination.
EXIT_ENVIRONMENT_UNFIT = kb_util.EXIT_ENVIRONMENT_UNFIT
#: Refused, and nothing was written: a domain no node is attributed to, or a
#: destination inside ``.index/``. The house's refusal code, spelled here rather
#: than imported from ``kb_write`` — this package reaches no gate or write
#: module — and pinned equal to it by test.
EXIT_REFUSED = 7

# Short names: check words, not paths.
_ITEM_NAME_WIDTH = 10


class UnknownDomain(Exception):
    """``domain`` names no domain the corpus carries.

    Raised by :func:`compose`, which has no exit code to return; :func:`render`
    maps it to :data:`EXIT_REFUSED` carrying this message. Deliberately not a
    ``ValueError``: the loader raises those and they mean the environment is
    unfit, which is the other code.
    """


@dataclass(frozen=True)
class ReportItem:
    """One reported line, in the toolchain's uniform shape.

    ``status`` is one of ``kb_util``'s three tokens. ``FACT`` never gates: the
    census is state a reviewer wants, not a verdict. ``detail`` prose is the
    prompt engineer's to revise, and no test asserts its wording — only the
    status token, the named identity, and the exit code.
    """

    status: str
    name: str
    detail: str

    def line(self) -> str:
        # ``ljust`` rather than the sibling reports' alignment format spec: the
        # spec's own ``<`` is a string constant in the parsed tree, and this
        # package is held to carrying no markup opener in any literal.
        return f"[{REPORT_TAG}] {self.status} {self.name.ljust(_ITEM_NAME_WIDTH)} {self.detail}"


@dataclass(frozen=True)
class Composition:
    """One sheet composed and not written: its markup, and the census of it.

    ``census`` is the same ``FACT`` block :func:`render` reports, so a caller
    that composes and then writes for itself reports what the op would have.
    """

    document: str
    census: tuple[ReportItem, ...]


@dataclass(frozen=True)
class Result:
    """What the op did: the exit code, the report, and the one path it wrote.

    ``written`` is ``None`` on every non-zero code, and that is exact rather
    than conventional: every refusal above happens before the write, so a run
    that did not exit 0 left the filesystem as it found it.
    """

    exit_code: int
    report: tuple[ReportItem, ...]
    written: Path | None = None

    def lines(self) -> tuple[str, ...]:
        return tuple(item.line() for item in self.report)


def render(*, kb_root: Path, out: Path | None = None, domain: str | None = None) -> Result:
    """Render one sheet from ``kb_root``'s derived index.

    ``out`` overrides the destination; without it the sheet lands at
    ``claim-graph.svg`` inside ``kb_root``. ``domain`` selects a per-domain subgraph,
    matched by exact string equality against the attributed domain — the first
    component of a node's ``canonical_path``, or :data:`model.DOMAIN_ROOT` for a
    root-hosted one — and omitting it draws the whole corpus.

    One invocation renders one sheet. Looping over domains belongs to a
    consumer's runner recipe, not to a mode in here that would have to invent a
    directory layout and a naming scheme.
    """
    index_dir = kb_root / kb_util.INDEX_DIRNAME
    if not index_dir.is_dir() or not os.access(index_dir, os.R_OK | os.X_OK):
        return _unfit(
            "index",
            f"{index_dir} is not a readable directory — run `{kb_util.refresh_cmd(kb_root.parent)}` "
            f"from the repository root to build it.",
        )

    destination = (kb_root / kb_util.CLAIM_GRAPH_FILENAME if out is None else out).resolve()
    if destination.is_relative_to(index_dir.resolve()):
        return _refused(
            "out",
            f"{destination} is inside {kb_util.INDEX_DIRNAME}/, which refresh regenerates wholesale "
            f"and verify diffs against a dry-run rebuild — a sheet written there would be clobbered "
            f"or flagged. Name a destination outside it.",
        )
    if not destination.parent.is_dir():
        return _unfit(
            "out",
            f"{destination.parent} does not exist — create it, or name a destination inside a directory that does.",
        )

    try:
        composed = compose(kb_root=kb_root, sheet_dir=destination.parent, domain=domain)
    except (FileNotFoundError, ValueError) as exc:
        # The loader's own message, verbatim: it names the missing files and the
        # refresh command, or the path:lineno of the record that will not parse.
        return _unfit("index", str(exc))
    except UnknownDomain as exc:
        return _refused("domain", str(exc))

    try:
        destination.write_text(composed.document, encoding="utf-8", newline="\n")
    except OSError as exc:
        return _unfit("out", f"{destination} could not be written: {exc}")

    written = ReportItem(
        kb_util.PASS,
        "sheet",
        f"{destination} ({len(composed.document.encode('utf-8'))} bytes)",
    )
    return Result(EXIT_RENDERED, (*composed.census, written), written=destination)


def compose(*, kb_root: Path, sheet_dir: Path, domain: str | None = None) -> Composition:
    """Compose one sheet's markup from ``kb_root``'s derived index, writing nothing.

    ``sheet_dir`` is the directory the sheet will be *read from*, not
    necessarily one anything is written to: a node's hyperlink is resolved
    relative to the document holding it, so a caller comparing bytes against a
    sheet on disk passes that sheet's own directory and gets the bytes that
    sheet should carry.

    Raises ``FileNotFoundError`` or ``ValueError`` out of the loader — whose
    messages name the missing files and the refresh command, or the
    ``path:lineno`` that will not parse — and :exc:`UnknownDomain` where
    ``domain`` names no domain in the corpus. Nothing about the graph it draws
    raises: see this module's own docstring.
    """
    index = kb_index.load(kb_root / kb_util.INDEX_DIRNAME)
    graph = model.build_graph(nodes=index.all_nodes, edges=index.all_depends_on_edges)
    if domain is not None and domain not in graph.domains:
        raise UnknownDomain(
            f"no node is attributed to {domain!r}. The corpus carries: {', '.join(graph.domains) or '(none)'}."
        )

    include, foreign = _selection(graph, domain)
    sheet = layout.place(graph, include=include)
    document = svg.render(
        sheet,
        # Both sides resolved, so the walk between them is over normalized
        # absolute paths — and its result is relative, so no machine-local
        # prefix can reach the document.
        link_base=svg.relative_link_base(
            kb_root=PurePosixPath(kb_root.resolve().as_posix()),
            sheet_dir=PurePosixPath(sheet_dir.resolve().as_posix()),
        ),
        foreign=foreign,
    )
    return Composition(document, _census(graph, sheet))


def _selection(graph: model.ClaimGraph, domain: str | None) -> tuple[Collection[str] | None, frozenset[str]]:
    """The ids a sheet draws, and which of them are drawn in the foreign style.

    The full corpus draws everything and has no foreign set. A domain sheet
    carries every node in the domain **plus every node one edge away** — no edge
    is ever silently dropped from a sheet, and dropping the cross-domain ones
    would hide precisely the whole-volume-isolation defect a per-domain view is
    most likely to be looked at for.

    **A ghost id adjacent to a selected node is drawn**, which the one-edge-away
    rule decides rather than excepts: a stub is attributed to no domain, so it
    is a neighbour outside the selection like any other, and it keeps its own
    stub outline because the foreign style is fill-only. The alternative drops
    an edge from the sheet to hide the defect the sheet exists to show.
    """
    if domain is None:
        return None, frozenset()

    members = {node.id for node in graph.nodes if node.domain == domain}
    neighbours: set[str] = set()
    for edge in graph.edges:
        if edge.source in members and edge.target not in members:
            neighbours.add(edge.target)
        elif edge.target in members and edge.source not in members:
            neighbours.add(edge.source)
    return members | neighbours, frozenset(neighbours)


def _census(graph: model.ClaimGraph, sheet: layout.Sheet) -> tuple[ReportItem, ...]:
    """The ``FACT`` lines: what was drawn, each family in its declared order.

    Every count but one is the drawn sheet's. The exception is the disconnected
    component, which is reported for the corpus — connectivity, like layering,
    is a property of the graph rather than of the view taken of it, and a whole
    volume left unlinked is a fact about the KB whichever sheet is being read.
    """
    node_types = Counter(placed.node.node_type for placed in sheet.nodes)
    relations = Counter(placed.edge.relation for placed in sheet.edges)
    defects = {
        "ghost-id": sum(1 for placed in sheet.nodes if placed.node.is_stub),
        "back-edge": sum(1 for placed in sheet.edges if placed.back_edge),
        "isolated-node": sum(1 for placed in sheet.nodes if placed.node.isolated),
        "disconnected-component": max(len(graph.components) - 1, 0),
        "relation-conflict": sum(1 for placed in sheet.edges if placed.edge.conflict),
    }
    return (
        _fact("nodes", f"{len(sheet.nodes)} drawn — {_tally(node_types, kb_schema.NODE_KINDS)}"),
        _fact("edges", f"{len(sheet.edges)} drawn — {_tally(relations, style.CENSUS_RELATIONS)}"),
        _fact("layers", str(sheet.layer_count)),
        _fact("crossings", str(len(sheet.hops))),
        _fact("defects", _tally(defects, style.CENSUS_DEFECT_CLASSES)),
    )


def _tally(counts: Mapping[str | None, int], order: tuple[str, ...]) -> str:
    """One census family, in its declared order and never in the corpus's.

    A key outside ``order`` is not reported here — a stub's absent node type is
    counted as the ghost-id defect, which is the name it has. The family's own
    headline total is what keeps that from being silent.
    """
    return ", ".join(f"{name} {counts.get(name, 0)}" for name in order)


def _fact(name: str, detail: str) -> ReportItem:
    return ReportItem(kb_util.FACT, name, detail)


def _unfit(name: str, detail: str) -> Result:
    return Result(EXIT_ENVIRONMENT_UNFIT, (ReportItem(kb_util.FAIL, name, detail),))


def _refused(name: str, detail: str) -> Result:
    return Result(EXIT_REFUSED, (ReportItem(kb_util.FAIL, name, detail),))


__all__ = [
    "EXIT_ENVIRONMENT_UNFIT",
    "EXIT_REFUSED",
    "EXIT_RENDERED",
    "REPORT_TAG",
    "Composition",
    "ReportItem",
    "Result",
    "UnknownDomain",
    "compose",
    "render",
]
