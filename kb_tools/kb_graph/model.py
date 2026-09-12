"""Claim-graph assembly from the loaded derived index.

This module turns the records :mod:`kb_tools.kb_cmd.index` loads into the graph
the renderer draws — the node table, the deduped edge set, the defects decidable
without geometry, and each node's domain attribution.

Pure and path-free: it opens no file and reads no path, and its only import
outside the standard library is the loader's dataclasses — this package defines
no second reader of ``.index/``. ``depends-on.jsonl`` is the only edge source;
``supported-by.jsonl`` is a derived reverse view of the same ``supports``
records and ``strengthen-by.jsonl`` is text bookkeeping, and neither contributes
an edge. Both are simply never read: this module is handed edges, and the only
edges the loader builds are ``depends-on.jsonl``'s.

**A ``references`` edge a path already implies is not drawn.** The drawn edge
set is the deduped one minus the references that a transitive reduction over
the union of all classes finds redundant — ``A depends B``, ``B depends C``,
``A references C`` draws the first two, because a reader can walk from A to C
without the third. The suppression is a rendering decision and reaches nothing
else: the index keeps every record, no ``depends`` or ``rests-on`` edge is ever
a candidate, and a reference whose endpoints have no other path stays whatever
its length. What it buys is width — a spanning edge takes a full column in
every layer it crosses, so a sheet's width is O(edges), and a mechanically
built corpus carries references by the thousand.

Nothing here refuses. A defective graph is the picture's subject, never an
error, so an edge naming an unknown id yields a stub node, a group of records
disagreeing on ``relation`` yields one stroke carrying a conflict, and both are
returned as data for the report lines and the drawing to read.

Nothing here decides geometry, colour or markup. Two defect classes are
deliberately absent: cycle membership and its back edges are the layering pass's,
which is the first place a cycle is detectable at all.
"""

from collections import defaultdict
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from pathlib import PurePosixPath

from kb_tools.kb_cmd.index import DependsOnEdge, GraphNode

# A node whose ``canonical_path`` has no directory component is hosted at the KB
# root and has no directory to name its domain. It takes this sentinel, which
# ``--domain`` may name like any other domain. The parentheses keep it out of the
# namespace a real directory could occupy.
DOMAIN_ROOT = "(root)"

# The one class a drawn sheet may suppress (:func:`_reduce_references`) and the
# one asserting nothing about premise or standing: a reader who can walk from
# its source to its target loses nothing when it is not drawn.
REFERENCE_RELATION = "references"

# When one ``(source, target)`` group's records disagree on ``relation``,
# the surviving stroke takes the first match here: ``depends`` wins because it is
# the gating relation solidity flows through and the one the acyclicity gate
# covers, so the layering premise direction is that relation's. A declared
# constant, never an encounter order. A relation outside this tuple sorts after
# all of them, by name.
#: ``references`` sits last, for the reason above it: any other relation on the
#: same pair is the one worth drawing.
RELATION_PRECEDENCE: tuple[str, ...] = ("depends", "supports", "strengthens", "rests-on", REFERENCE_RELATION)


@dataclass(frozen=True, slots=True)
class Node:
    """One drawn node: an index record, or a stub standing in for a ghost id.

    ``record`` is ``None`` exactly when no ``claims.jsonl`` record carries this
    id and an edge named it anyway — a ghost id, which draws as a stub with its
    id shown and no title. A stub has no ``canonical_path``, so it has no domain
    either and ``domain`` is ``None``.

    ``isolated`` means no deduped edge is incident to the node. A stub is never
    isolated — it exists only because an edge named it.
    """

    id: str
    record: GraphNode | None
    domain: str | None
    isolated: bool

    @property
    def is_stub(self) -> bool:
        """True when no index record carries this id — a ghost id."""
        return self.record is None

    @property
    def node_type(self) -> str | None:
        """The record's discriminator, or ``None`` for a stub."""
        return None if self.record is None else self.record.node_type

    @property
    def title(self) -> str:
        """The record's title; the empty string for a stub, which shows none."""
        return "" if self.record is None else self.record.title


@dataclass(frozen=True, slots=True)
class Edge:
    """One drawn stroke: every ``depends-on.jsonl`` record for one ordered pair.

    Edge identity is the ordered pair ``(source, target)``. Multiple records
    for one pair are legal — the file's own sort key includes ``context``
    precisely because they occur — and they draw one stroke whose ``contexts``
    are theirs, merged.

    ``relation`` is the survivor of :data:`RELATION_PRECEDENCE`; ``relations``
    holds every distinct relation the group carried, in that same precedence
    order, so a conflicting stroke can name them all. ``target_kind``,
    ``strength`` and ``fraction`` are the surviving relation's, taken from the
    group's first such record under :func:`_tiebreak`.
    """

    source: str
    target: str
    relation: str
    relations: tuple[str, ...]
    target_kind: str
    strength: float | None
    fraction: float | str | None
    contexts: tuple[str, ...]

    @property
    def conflict(self) -> bool:
        """True when the group carried more than one relation.

        One fact, one stroke, plus a mark — never two strokes in two styles.
        """
        return len(self.relations) > 1


@dataclass(frozen=True, slots=True)
class ClaimGraph:
    """The assembled graph: every node, every deduped edge, and connectivity.

    ``nodes`` is ascending by id and ``edges`` ascending by ``(source, target)``
    — declared orderings, so nothing downstream depends on the order the records
    were loaded in. ``edges`` is what the sheet draws: a ``references`` edge a
    remaining path already implies is not in it (:func:`_reduce_references`).

    ``components`` is the undirected connectivity partition, each component's ids
    ascending and the components themselves ordered by their smallest id. One
    component means the graph is whole; more than one is a disconnected graph,
    and an isolated node is a component of size one.
    """

    nodes: tuple[Node, ...]
    edges: tuple[Edge, ...]
    components: tuple[tuple[str, ...], ...]
    _by_id: dict[str, Node] = field(init=False, compare=False, repr=False)

    def __post_init__(self) -> None:
        object.__setattr__(self, "_by_id", {n.id: n for n in self.nodes})

    def node(self, node_id: str) -> Node | None:
        """The node carrying ``node_id``, stub or not, or ``None``."""
        return self._by_id.get(node_id)

    @property
    def domains(self) -> tuple[str, ...]:
        """Every attributed domain, ascending; stubs contribute none."""
        return tuple(sorted({n.domain for n in self.nodes if n.domain is not None}))


def build_graph(*, nodes: Iterable[GraphNode], edges: Iterable[DependsOnEdge]) -> ClaimGraph:
    """Assemble the drawn graph from a loaded index's nodes and edges.

    ``nodes`` is the index's ``claims.jsonl`` records and ``edges`` its
    ``depends-on.jsonl`` records — the only edge source. Ids are assumed unique
    across ``nodes``; ``verify_kb_metadata`` gates that, and a duplicate would be
    a defect of the index rather than of the graph.

    The edges returned are the **drawn** set: deduped, then stripped of the
    ``references`` a path already implies (:func:`_reduce_references`).
    ``isolated`` is measured over that set, and measuring it over the union set
    would give the same answer — a suppressed edge's source still reaches its
    target through the drawn edges, so both its endpoints keep an incident
    stroke and no node can be moved into the orphan block by a suppression.

    The result is a function of the record *set* alone: every ordering in it is
    an explicit sort on a declared key, so shuffling either argument cannot
    change a byte of it.
    """
    records: dict[str, GraphNode] = {n.id: n for n in nodes}
    deduped = _reduce_references(_dedupe(edges))

    incident = {end for e in deduped for end in (e.source, e.target)}
    ids = sorted(set(records) | incident)
    node_table = tuple(
        Node(
            id=node_id,
            record=records.get(node_id),
            domain=_domain_of(records.get(node_id)),
            isolated=node_id not in incident,
        )
        for node_id in ids
    )
    return ClaimGraph(nodes=node_table, edges=deduped, components=_components(ids, deduped))


def _dedupe(edges: Iterable[DependsOnEdge]) -> tuple[Edge, ...]:
    """One stroke per ``(source, target)`` group, in ascending pair order."""
    groups: dict[tuple[str, str], list[DependsOnEdge]] = defaultdict(list)
    for edge in edges:
        groups[(edge.source, edge.target)].append(edge)

    out: list[Edge] = []
    for pair in sorted(groups):
        group = groups[pair]
        relations = tuple(sorted({e.relation for e in group}, key=_relation_rank))
        survivor = min((e for e in group if e.relation == relations[0]), key=_tiebreak)
        source, target = pair
        out.append(
            Edge(
                source=source,
                target=target,
                relation=relations[0],
                relations=relations,
                target_kind=survivor.target_kind,
                strength=survivor.strength,
                fraction=survivor.fraction,
                contexts=tuple(sorted({e.context for e in group if e.context})),
            )
        )
    return tuple(out)


def _reduce_references(edges: tuple[Edge, ...]) -> tuple[Edge, ...]:
    """Drop every ``references`` edge the remaining edges already imply.

    A greedy transitive reduction restricted to the one suppressible class, over
    the union of all of them: candidates are taken in ascending ``(source,
    target)`` — the declared order, so the result is a function of the graph and
    not of the order the records loaded in — and a candidate is dropped when its
    source still reaches its target without it. Each drop preserves reachability
    by construction, so the drawn set reaches everything the full set did and
    "the reader can walk it instead" is a property rather than an argument.

    **Against the current graph, never against a snapshot.** ``references`` is
    under no acyclicity constraint, so the union graph may hold a reference
    cycle, and a snapshot-based reduction drops every edge of one — each member
    has an alternate path in the snapshot — leaving its claims disconnected.
    Testing against the edges still standing means the last edge holding a cycle
    together finds no alternate path and stays.

    Reachability is walked per candidate rather than transitively closed: the
    closure is dense on a reference-heavy corpus, and this stays a few million
    set lookups at the scale the corpus reaches.
    """
    successors: dict[str, set[str]] = defaultdict(set)
    for edge in edges:
        successors[edge.source].add(edge.target)

    suppressed: set[tuple[str, str]] = set()
    for edge in edges:
        if edge.relation != REFERENCE_RELATION:
            continue
        successors[edge.source].discard(edge.target)
        if _reaches(successors, edge.source, edge.target):
            suppressed.add((edge.source, edge.target))
        else:
            successors[edge.source].add(edge.target)
    return tuple(edge for edge in edges if (edge.source, edge.target) not in suppressed)


def _reaches(successors: Mapping[str, set[str]], source: str, target: str) -> bool:
    """Whether a walk of one or more edges runs from ``source`` to ``target``.

    One edge at least, so a self-reference is redundant only where a real cycle
    returns to its own claim — under a reflexive reading every self-reference
    would be dropped and its claim could lose its only stroke.
    """
    seen: set[str] = set()
    stack = list(successors.get(source, ()))
    while stack:
        current = stack.pop()
        if current == target:
            return True
        if current in seen:
            continue
        seen.add(current)
        stack.extend(successors.get(current, ()))
    return False


def _relation_rank(relation: str) -> tuple[int, str]:
    """Sort key placing a relation at its declared precedence, unknowns last by name."""
    try:
        return (RELATION_PRECEDENCE.index(relation), relation)
    except ValueError:
        return (len(RELATION_PRECEDENCE), relation)


def _tiebreak(edge: DependsOnEdge) -> tuple[str, str, str, str]:
    """A total order over one group's records, so the survivor is never the input order's.

    Records sharing a pair normally differ by ``context`` alone and agree on
    everything the stroke shows; the remaining fields are here to make the key
    total rather than because they are expected to decide it. Nothing in this key
    is emitted.
    """
    return (edge.context or "", edge.target_kind, _scalar_key(edge.strength), _scalar_key(edge.fraction))


def _scalar_key(value: float | str | None) -> str:
    """Order-stable rendering of an optional scalar, for :func:`_tiebreak` only."""
    return "" if value is None else repr(value)


def _domain_of(record: GraphNode | None) -> str | None:
    """The first path component of a node's ``canonical_path``.

    A path with no directory component is root-hosted and takes
    :data:`DOMAIN_ROOT`; a stub has no path and takes ``None``.
    """
    if record is None:
        return None
    parts = PurePosixPath(record.canonical_path).parts
    return parts[0] if len(parts) > 1 else DOMAIN_ROOT


def _components(ids: list[str], edges: tuple[Edge, ...]) -> tuple[tuple[str, ...], ...]:
    """Undirected connectivity over the deduped edge set.

    Iterative, because a corpus-scale chain would overflow a recursive walk, and
    keyed throughout on ascending id so the partition is the same whatever order
    the edges arrived in.
    """
    adjacency: dict[str, set[str]] = {node_id: set() for node_id in ids}
    for edge in edges:
        adjacency[edge.source].add(edge.target)
        adjacency[edge.target].add(edge.source)

    seen: set[str] = set()
    out: list[tuple[str, ...]] = []
    for start in ids:
        if start in seen:
            continue
        seen.add(start)
        stack = [start]
        members: list[str] = []
        while stack:
            current = stack.pop()
            members.append(current)
            for neighbour in adjacency[current]:
                if neighbour not in seen:
                    seen.add(neighbour)
                    stack.append(neighbour)
        out.append(tuple(sorted(members)))
    return tuple(out)


__all__ = [
    "DOMAIN_ROOT",
    "REFERENCE_RELATION",
    "RELATION_PRECEDENCE",
    "ClaimGraph",
    "Edge",
    "Node",
    "build_graph",
]
