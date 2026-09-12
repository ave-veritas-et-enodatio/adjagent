"""Placed geometry for the claim-graph sheet: model in, coordinates out.

Layering, in-layer ordering and coordinates, edge routing through dummy nodes,
and hop detection with hop-side selection. Pure — it opens no file, and it knows
no colour, no font, no tag name and no URL. It reads
:mod:`kb_tools.kb_graph.style` for magnitudes and nothing else from it; if this
module ever imports ``xml``, the boundary has been crossed.

**An edge spanning more than one layer is a chain, not a segment.** Every layer
it crosses holds a **dummy** — a placeholder that takes a real column in that
layer, ordered among the real nodes by the sweep below — and the edge draws as
one polyline through them. Two consequences, and the second is why the dummies
exist: a real node is no longer drawn under an edge path, and every segment is
layer-adjacent, so an ordering pass has something in the middle to reorder. A
dummy is never drawn and carries no label; it is mechanical, with no heuristic
and nothing to tune.

**A node with no edges at all leaves the hierarchy.** Layering answers "what
does this rest on", and a node that rests on nothing and carries nothing has no
answer to give: put through the longest-path rule it lands on layer 0 beside the
corpus's real bedrock, where it says nothing and makes that layer as wide as the
count of unattached nodes. So an **orphan** — degree zero over the whole corpus
graph, which is :attr:`kb_tools.kb_graph.model.Node.isolated` — is taken out of
layer assignment entirely and drawn in a grid block below the baseline,
:data:`style.ORPHAN_ROW` to a row, in the same stable key order every other
occupant starts from. Degree is the corpus's and never the drawn sheet's, for
the reason layers are (:func:`place`): otherwise a node would move between the
grid and the hierarchy depending on which domain was being rendered. Nothing an
orphan does can reach a crossing or a hop, there being no stroke to cross.

**In-layer order is computed, not looked up.** Sugiyama's crossing-reduction
phase runs here. Each layer's occupants — real nodes and dummies alike — start
in the stable key order :func:`_occupants` states, and an iterated
**barycentre** sweep reorders them from there: one round is a pass up the
layers, each reordered against the one beneath it, then a pass back down, each
reordered against the one above, an occupant's rank being the mean position of
the occupants it is joined to across that band. Crossings that survive are
still denoted by the hop glyph; the sweep only makes fewer of them.

The parameters, measured rather than assumed — over ``mini-kb`` plus 25
synthetic shapes, flat and wide two-layer graphs and multi-layer DAGs carrying
dummies:

* **Barycentre, not median.** The barycentre sweep left 3251 crossings across
  that sample against the median sweep's 3706, and was best-or-equal on 18 of
  the 26 shapes against the median's 10. The median carries the better
  published approximation bound for one layer at a time; on these shapes it
  did not cash out.
* **At most :data:`SWEEP_ROUNDS` rounds, stopping on the first that does not
  improve**, and the best-scoring order any round reached is what is returned
  — so the sweep never hands back a picture worse than the order it started
  from. Two rounds leaves about 15% of the crossings eight remove; thirty-two
  buys under 1% over eight.
* **Ties hold their ground.** Occupants with equal barycentres, and an
  occupant with no neighbour across the band at all, keep the position they
  already had — which at the first pass is the start key's.

That start key and that tie rule are the whole of what keeps the result
independent of the order the records were loaded in. **Locality is not a
constraint**: the sweep may relocate any node it likes, and one added edge may
rearrange the sheet. Determinism is the constraint, and
``test_shuffling_the_loaded_records_changes_no_geometry`` is what holds it.

**A column index is not an x coordinate.** Sugiyama's coordinate-assignment
phase runs here too, by the **priority method** (Sugiyama, Tagawa & Toda 1981).
The ordering above says who stands left of whom; this says where each of them
stands. Every occupant starts at its index times the column pitch — the grid the
ordering alone used to draw — and is then pulled toward the barycentre of the
occupants it joins across one band, layer by layer, in alternating passes up and
down. What it minimises is the **total horizontal edge length**, which is the
same thing as saying a chain through placeholders comes out near-vertical
instead of zigzagging through its own columns.

Two rules bound every move, and between them they are why the phase cannot undo
the phase above it: **an occupant never passes its neighbours in the order the
sweep chose**, and no two occupants of one layer ever come closer than
:data:`style.COLUMN_PITCH` — one box width plus the gutter, so the boxes keep
the blank space between them the grid always gave them.

The parameters, measured rather than assumed, over 25 synthetic layered DAGs —
three to six layers, two to seven nodes a layer, edges skipping up to three — in
which the phase cuts total horizontal edge length by 36%, from 359520 to 231380:

* **Priority is ``(is a dummy, degree across the band)``, descending, ties
  taking the occupants in their drawn order.** A dummy outranks every real node,
  which is what makes a long edge's chain straighten and the real nodes give way
  to it rather than the other way round; among real nodes the one with more
  neighbours to answer to moves first. An occupant moves only lower-priority
  occupants out of its way — it stops dead against an equal or higher one — so
  the vertex that most needs its position gets it. **That rank is a trade and it
  is paid on purpose**: against a degree-only rule giving a dummy no standing of
  its own, the chains come out 14% straighter (36898 against 42923 over the
  chained edges) and the sheet's total length 5% longer. A chain that runs
  straight reads as one edge; the length it costs is spread over edges that
  read as themselves either way.
* **An occupant with no neighbour across the band does not move**, exactly as in
  the ordering sweep: it has no barycentre, and inventing one for it would drag
  an isolated node around the sheet to no one's benefit. It can still be pushed.
* **At most :data:`COORDINATE_ROUNDS` rounds, stopping on the first that moves
  nothing, and the best-scoring round is what is returned.** The stop is a fixed
  point rather than the ordering sweep's stop-on-no-improvement, because total
  length is *flat* wherever a vertex sits anywhere between two neighbours: a
  round that centres such a vertex scores the same and draws better. So the
  score carries the sheet's width behind its length as a tie-break, ties go to
  the swept round, and only the starting grid is kept against a round scoring
  worse — which is what makes "no longer than the sheet it was given" hold.
  Four rounds already reach the thirty-two round total on every shape measured,
  and one round leaves about 10% of the length eight rounds remove; the cap is
  headroom rather than a tuned number.

The whole sheet is then shifted once so its leftmost occupant sits where column
zero always sat. A global shift, never a per-layer one — a per-layer shift is
alignment thrown away.

**Nothing raises on a defective graph.** A cycle, a ghost id, an isolated node
and a disconnected component are all placed, and the defects geometry decides —
cycle membership and its back edges — come out as fields for the drawing and the
report lines to read.

**The arithmetic is exact integer arithmetic and there is no epsilon anywhere.**
Every coordinate is an ``int`` because every magnitude in :mod:`style` is; the
crossing test's denominator is therefore exact, ``den == 0`` is the whole
parallel/colinear test, and nothing divides. The two non-integral quantities are
a crossing point and an ordering barycentre, and both are exact
:class:`fractions.Fraction`\\ s: the point so that its rounding happens once, at
emission, in the serializer's single number formatter rather than at an
intermediate step, and the rank so that a tie between two occupants is a tie
rather than a float comparison that rounded its way into one. The coordinate
phase's barycentre is the one quantity rounded here rather than at emission,
because what it produces *is* a coordinate: it rounds to the nearer integer by
exact integer arithmetic, so nothing about where a box lands depends on a
binary fraction.

Stdlib only.
"""

from collections import defaultdict, deque
from collections.abc import Collection, Iterable
from dataclasses import dataclass
from fractions import Fraction
from itertools import combinations

from kb_tools.kb_graph import style
from kb_tools.kb_graph.model import ClaimGraph, Edge, Node

#: One layer occupant's identity and its start-order key: a real node's id
#: alone, or the ``(source, target)`` pair of the edge a dummy stands in for.
_Key = tuple[str, ...]

#: The crossing-reduction sweep's round cap. A round is a pass up the layers
#: and a pass back down; the sweep stops earlier on the first round that fails
#: to improve. Measured, not guessed — the module docstring carries the numbers.
SWEEP_ROUNDS = 8

#: The coordinate phase's round cap. A round is a pass up the layers and a pass
#: back down; the phase stops earlier at a fixed point — the first round that
#: moves nothing — and the best-scoring round is what is returned. Measured, not
#: guessed — the module docstring carries the numbers.
COORDINATE_ROUNDS = 8

#: The one relation whose record direction is the reverse of the premise
#: direction. A ``depends`` record ``(source=A, target=B)`` says A leans on
#: B, so B is the premise and A the dependent; a ``supports`` or ``strengthens``
#: record ``(source=S, target=C)`` says S lifts C, so the source is the premise
#: and the record already reads premise-first. Any relation this module does not
#: know is read the same way as those two — the record's own direction — rather
#: than being dropped from the layering.
PREMISE_INVERTING_RELATION = "depends"

#: The relations that are no premise relation at all, and so constrain the
#: layering not at all. A ``references`` record says one claim's text names
#: another and asserts nothing about which stands on which — so reading it as a
#: premise pair would order two layers on a fact that does not order them, and a
#: mutual pair would fall into the residual set and draw as a cycle with two back
#: edges. Two claims naming each other is the author's argument, not the defect a
#: dependency cycle is, so the class enters neither the layering nor the cycle
#: membership derived from it. It is still drawn, and both its endpoints still
#: count as attached: only the ordering is silent.
NON_PREMISE_RELATIONS: frozenset[str] = frozenset({"references"})


@dataclass(frozen=True, slots=True)
class Point:
    """An exact integer point in the bottom-anchored frame."""

    x: int
    y: int


@dataclass(frozen=True, slots=True)
class PlacedNode:
    """One node's box, placed on the column grid.

    ``x``/``y`` are the box's left and **top** edges. Layer 0 sits on the
    baseline and the sheet grows upward into negative ``y``, so a new deepest
    layer changes the ``viewBox`` and moves nothing already placed.

    ``column`` is the box's place in its layer's left-to-right order and ``x``
    is where it stands, and the two are no longer one quantity twice: the
    coordinate phase pulls a box toward its neighbours and away from its index's
    own multiple of the pitch. What still holds between them is the order — ``x``
    ascends with ``column`` within a layer, by at least one column pitch.

    **A negative ``layer`` is a row of the orphan block**, not a layer: an
    orphan is outside the hierarchy (this module's docstring), and row ``r`` of
    the block takes ``layer = -(r + 1)`` so that the one rule ``y = -layer ×
    LAYER_PITCH`` still places it — one pitch below the baseline for the first
    row, and downward from there. Its ``column`` is its place in its own row,
    and ``x`` is that column's own multiple of the pitch: the block is a grid,
    with no coordinate phase over it and nothing to pull it toward.

    ``cycle_member`` is the residual set: a node the Kahn order never resolved.
    Cycle membership is defined as exactly that, which also sweeps in the nodes
    standing downstream of a cycle — their premises are never all resolved
    either. Both populations are unlayerable for the same reason and the picture
    says so in the same way.
    """

    node: Node
    layer: int
    column: int
    x: int
    y: int
    width: int
    height: int
    cycle_member: bool

    @property
    def id(self) -> str:
        return self.node.id

    @property
    def top_centre(self) -> Point:
        """The single anchor every edge leaving this box uses."""
        return Point(self.x + self.width // 2, self.y)

    @property
    def bottom_centre(self) -> Point:
        """The single anchor every edge arriving at this box uses."""
        return Point(self.x + self.width // 2, self.y + self.height)


@dataclass(frozen=True, slots=True)
class Segment:
    """One straight run of a chain, from its lower-layer end to its upper one."""

    start: Point
    end: Point


@dataclass(frozen=True, slots=True)
class PlacedEdge:
    """One stroke: the polyline chaining an edge's two box anchors.

    ``points`` runs from the lower node's top-centre, through one dummy per
    layer the edge crosses in ascending layer order, to the upper node's
    bottom-centre — "lower" being the smaller ``(layer, column)``. That reading
    coincides with premise-to-dependent whenever the premise really is lower,
    and stays deterministic when it is not — an intra-cycle edge whose endpoints
    the layering could not order, which is drawn and marked rather than hidden.

    An edge between adjacent layers, or within one, crosses nothing and carries
    the two anchors alone; an edge spanning N layers carries N−1 dummies and
    draws as N segments.
    """

    edge: Edge
    points: tuple[Point, ...]
    back_edge: bool

    @property
    def key(self) -> tuple[str, str]:
        """The ordered pair that is this edge's identity and its sort key."""
        return (self.edge.source, self.edge.target)

    @property
    def start(self) -> Point:
        """The lower node's anchor, where the chain begins."""
        return self.points[0]

    @property
    def end(self) -> Point:
        """The upper node's anchor, where the chain ends."""
        return self.points[-1]

    @property
    def segments(self) -> tuple[Segment, ...]:
        """The chain's runs, each between consecutive points."""
        return tuple(Segment(first, second) for first, second in zip(self.points, self.points[1:]))


@dataclass(frozen=True, slots=True)
class Hop:
    """One bridge glyph: where one edge's segment hops over another's.

    ``hopping`` is the edge the arc is drawn on and ``crossed`` the one it goes
    over — of the two, the pair sorting **greater** hops, a stable key rather
    than an iteration order. ``hopping_segment`` and ``crossed_segment`` index
    each edge's own :attr:`PlacedEdge.segments`, which is what keeps the
    emission key total now that two chains can cross more than once.

    ``x``/``y`` are the exact crossing point and ``direction`` is the hopping
    **segment's** own vector, from its lower end to its upper one — an
    unnormalized integer vector naming the line the arc's endpoints sit on. It
    is the line and not the bulge: which side the arc bulges toward is the
    emitter's, which orders the two endpoints by screen x before rotating
    (:func:`kb_tools.kb_graph.svg.draw_hop_over`), and stating it twice is how
    the two would come to disagree.
    """

    hopping: tuple[str, str]
    hopping_segment: int
    crossed: tuple[str, str]
    crossed_segment: int
    x: Fraction
    y: Fraction
    direction: tuple[int, int]


@dataclass(frozen=True, slots=True)
class ViewBox:
    """The sheet's extent: the union of every drawn box and every drawn point, plus a margin.

    The strokes enter it because a chain bends at a dummy's column, which can
    sit outside the widest box in its own layer — a `viewBox` taken from the
    boxes alone would clip the very routing the dummies exist to draw.

    The only global quantity in the document.
    """

    min_x: int
    min_y: int
    width: int
    height: int


@dataclass(frozen=True, slots=True)
class Sheet:
    """Everything drawn, in the declared emission orders.

    ``nodes`` ascends by ``(layer, in-layer index)``, ``edges`` by
    ``(source, target)`` — one entry per edge however many segments it draws as
    — and ``hops`` by ``(hopping pair, hopping segment, crossed pair, crossed
    segment)``. No set or dict iteration order reaches any of them.

    Ascending layer runs from the bottom of the sheet upward, and the orphan
    block's rows carry negative layers (:class:`PlacedNode`), so the block's
    lowest row is emitted first and the deepest layer last — one reading of the
    key, not two.
    """

    nodes: tuple[PlacedNode, ...]
    edges: tuple[PlacedEdge, ...]
    hops: tuple[Hop, ...]
    view_box: ViewBox

    @property
    def layer_count(self) -> int:
        """How many layers the drawn hierarchy occupies — a ``FACT`` census line.

        The orphan block's rows are not layers and are not counted: a sheet of
        nothing but orphans occupies no layer, which is what its zero says.
        """
        layers = [n.layer for n in self.nodes if n.layer >= 0]
        return 0 if not layers else max(layers) + 1


def place(graph: ClaimGraph, *, include: Collection[str] | None = None) -> Sheet:
    """Place ``graph`` on the column grid and find its crossings.

    ``include``, when given, is the set of node ids this sheet draws — the domain
    selection, which is the caller's policy and not this module's.
    **Layering, orphan-ness, in-layer indices, coordinates and the dummy chains
    are computed over the whole of ``graph`` either way**, so a node occupies the
    same coordinates on a domain sheet as on the full one and never moves between
    views — and a chain bends where it bends on the full sheet, because an edge
    the domain sheet does not draw still took its columns and still pulled on
    the coordinates. Orphan-ness is in that list for the same reason and one
    more: a node whose only edge leaves the selected domain would otherwise be
    an orphan on that domain's sheet and a layered node on the full one, which
    is the same node drawn as two different facts. An edge is drawn when both its
    endpoints are; crossings are found over the edges actually drawn, since a
    glyph marks what a reader can see.

    The result is a function of ``graph`` alone: shuffling the records it was
    assembled from cannot change a byte of it.
    """
    hierarchy, orphans = _partition(graph)
    layers, cycle_members = _assign_layers(hierarchy, graph.edges)
    bands = _bands(graph.edges, layers)
    order = _sweep(_occupants(hierarchy, graph.edges, layers), bands)
    columns = {layer: _positions(keys) for layer, keys in order.items()}
    centres = _centres(order, bands)

    placed: dict[str, PlacedNode] = {}
    for node in hierarchy:
        if include is not None and node.id not in include:
            continue
        layer = layers[node.id]
        placed[node.id] = PlacedNode(
            node=node,
            layer=layer,
            column=columns[layer][(node.id,)],
            x=centres[layer][(node.id,)] - style.BOX_WIDTH // 2,
            y=-layer * style.LAYER_PITCH,
            width=style.BOX_WIDTH,
            height=style.BOX_HEIGHT,
            cycle_member=node.id in cycle_members,
        )
    for index, node in enumerate(orphans):
        if include is not None and node.id not in include:
            continue
        placed[node.id] = _place_orphan(node, index)

    nodes = tuple(sorted(placed.values(), key=lambda p: (p.layer, p.column)))
    edges = tuple(
        _place_edge(edge, placed[edge.source], placed[edge.target], centres=centres, cycle_members=cycle_members)
        for edge in graph.edges
        if edge.source in placed and edge.target in placed
    )
    return Sheet(nodes=nodes, edges=edges, hops=_hops(edges), view_box=_view_box(nodes, edges))


# ---------------------------------------------------------------------------
# The hierarchy and the orphan block
# ---------------------------------------------------------------------------


def _partition(graph: ClaimGraph) -> tuple[tuple[Node, ...], tuple[Node, ...]]:
    """The nodes the hierarchy places, and the orphans it does not.

    An orphan is a node of degree zero over the whole corpus graph, which is
    what ``Node.isolated`` already says — read from there rather than recounted
    here, so the sheet and the defect census cannot come to disagree about which
    nodes they are. Both halves stay ascending by id, ``graph.nodes`` being so.
    """
    return (
        tuple(node for node in graph.nodes if not node.isolated),
        tuple(node for node in graph.nodes if node.isolated),
    )


def _place_orphan(node: Node, index: int) -> PlacedNode:
    """One orphan's box in the grid block, at its own place in the corpus's orphan order.

    ``index`` is that order — the same ascending-id key every other occupant
    starts from — so the block is a function of the graph and of nothing a
    particular sheet selected. The row is below the baseline and the column is
    the grid's, with no coordinate phase over either: an orphan has no
    neighbours to be pulled toward.
    """
    row, column = divmod(index, style.ORPHAN_ROW)
    layer = -(row + 1)
    return PlacedNode(
        node=node,
        layer=layer,
        column=column,
        x=column * style.COLUMN_PITCH,
        y=-layer * style.LAYER_PITCH,
        width=style.BOX_WIDTH,
        height=style.BOX_HEIGHT,
        cycle_member=False,
    )


# ---------------------------------------------------------------------------
# Layering
# ---------------------------------------------------------------------------


def _premise_pairs(edges: Iterable[Edge]) -> set[tuple[str, str]]:
    """The premise relation over the deduped edge set, as ``(premise, dependent)``.

    Deduplicated: two distinct strokes can express the same premise pair — a
    ``depends`` edge ``(A, B)`` and a ``supports`` edge ``(B, A)`` both say B is
    an input to A — and counting that premise twice would leave A's in-degree
    permanently above zero and strand it in the residual set.

    A stroke of a :data:`NON_PREMISE_RELATIONS` class contributes no pair: it
    states no premise, so it orders no layer and joins no cycle.
    """
    return {_premise_pair(edge) for edge in edges if edge.relation not in NON_PREMISE_RELATIONS}


def _premise_pair(edge: Edge) -> tuple[str, str]:
    """``(premise, dependent)`` for one stroke, per :data:`PREMISE_INVERTING_RELATION`."""
    if edge.relation == PREMISE_INVERTING_RELATION:
        return (edge.target, edge.source)
    return (edge.source, edge.target)


def _assign_layers(nodes: tuple[Node, ...], edges: Iterable[Edge]) -> tuple[dict[str, int], frozenset[str]]:
    """Longest path from the premise-less nodes, in Kahn order, plus the residual pass.

    Iterative throughout: a recursive descent is a stack overflow on the first
    cyclic input, and this renderer's whole value on a defective graph is that it
    draws one.

    ``nodes`` is the hierarchy alone: every endpoint of every edge is in it by
    construction, an orphan being a node no edge names.

    Returns the layer of every node it was given and the residual set — the
    nodes the Kahn order never resolved, which are the cycle members.
    """
    ids = [node.id for node in nodes]  # model orders these ascending
    premises: dict[str, list[str]] = {node_id: [] for node_id in ids}
    dependents: dict[str, list[str]] = {node_id: [] for node_id in ids}
    for premise, dependent in sorted(_premise_pairs(edges)):
        premises[dependent].append(premise)
        dependents[premise].append(dependent)

    unresolved = {node_id: len(premises[node_id]) for node_id in ids}
    layers: dict[str, int] = {node_id: 0 for node_id in ids if not premises[node_id]}
    longest: dict[str, int] = {}
    queue = deque(sorted(layers))
    while queue:
        current = queue.popleft()
        for dependent in dependents[current]:
            longest[dependent] = max(longest.get(dependent, 0), layers[current] + 1)
            unresolved[dependent] -= 1
            if unresolved[dependent] == 0:
                layers[dependent] = longest[dependent]
                queue.append(dependent)

    # The residual pass reads this snapshot, so a layer assigned during the pass
    # never feeds a later member's max() and the residual set is
    # order-independent — a coder who walks it as a set gets the same picture.
    snapshot = dict(layers)
    cycle_members = frozenset(node_id for node_id in ids if node_id not in snapshot)
    for node_id in sorted(cycle_members):
        resolved = [snapshot[premise] for premise in premises[node_id] if premise in snapshot]
        # Never layer 0: bedrock is where the reader looks for the corpus's most
        # basic premises, and a broken cycle drawn there misreads the instrument
        # at exactly the moment the instrument matters.
        layers[node_id] = max(1, 1 + max(resolved, default=-1))
    return layers, cycle_members


def _intervening_layers(first: int, second: int) -> range:
    """The layers strictly between two endpoint layers — one dummy apiece."""
    lower, upper = sorted((first, second))
    return range(lower + 1, upper)


# ---------------------------------------------------------------------------
# In-layer ordering — the crossing-reduction sweep
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class _Band:
    """The segments between one layer and the next one up, as the sweep reads them.

    ``pairs`` is what the crossing count runs over; ``below`` and ``above`` are
    the same segments indexed from each end, which is what a pass needs and
    what would otherwise be rebuilt on every round.
    """

    pairs: tuple[tuple[_Key, _Key], ...]
    below: dict[_Key, list[_Key]]
    above: dict[_Key, list[_Key]]


def _occupants(nodes: tuple[Node, ...], edges: Iterable[Edge], layers: dict[str, int]) -> dict[int, list[_Key]]:
    """Every layer's occupants in the stable start order: key ascending.

    **A real node's key is its id alone and a dummy's is its edge's
    ``(source, target)`` pair**, and the two are ordered against each other as
    the tuples they are — so the order is total because ids are unique among
    nodes and pairs among edges, and it is a function of the graph rather than
    of the order its records arrived in. Nothing else enters either key: not
    domain, not degree, not component membership.

    This is a *starting* order, not the drawn one — and it is what the sweep's
    determinism rests on, a sweep started from a dict built in record order
    being reproducible only by accident.

    **Dummies are placed over the whole corpus graph**, never over the edge set
    a particular sheet draws, or a node would take one column on the full sheet
    and another on a domain sheet.

    ``nodes`` is the hierarchy alone: an orphan occupies no layer, and leaving
    it in one would put the whole unattached half of a mechanically-built corpus
    into layer 0 — which is the width this block exists to take out of it.
    """
    by_layer: dict[int, list[_Key]] = defaultdict(list)
    for node in nodes:
        by_layer[layers[node.id]].append((node.id,))
    for edge in edges:
        for layer in _intervening_layers(layers[edge.source], layers[edge.target]):
            by_layer[layer].append((edge.source, edge.target))
    return {layer: sorted(keys) for layer, keys in by_layer.items()}


def _chain(edge: Edge, layers: dict[str, int]) -> tuple[int, list[_Key]] | None:
    """The occupant key this edge holds in each layer, lower end upward.

    Its own two ends, and between them the dummy key :func:`_occupants` placed
    in each layer it crosses — the same keys, read as a chain.

    ``None`` for an edge within one layer, which crosses no band and so has no
    say in any layer's order — an intra-cycle edge, drawn and marked but not
    swept on.
    """
    lower_layer, upper_layer = sorted((layers[edge.source], layers[edge.target]))
    if lower_layer == upper_layer:
        return None
    lower_end = (edge.source,) if layers[edge.source] == lower_layer else (edge.target,)
    upper_end = (edge.target,) if layers[edge.target] == upper_layer else (edge.source,)
    dummies = [(edge.source, edge.target)] * (upper_layer - lower_layer - 1)
    return lower_layer, [lower_end, *dummies, upper_end]


def _bands(edges: Iterable[Edge], layers: dict[str, int]) -> dict[int, _Band]:
    """The segments running between each layer and the next one up, keyed by the lower layer."""
    pairs: dict[int, list[tuple[_Key, _Key]]] = defaultdict(list)
    for edge in edges:  # ascending by (source, target) — no incidental order enters
        chained = _chain(edge, layers)
        if chained is None:
            continue
        lower_layer, keys = chained
        for offset, pair in enumerate(zip(keys, keys[1:])):
            pairs[lower_layer + offset].append(pair)

    bands: dict[int, _Band] = {}
    for layer, group in sorted(pairs.items()):
        below: dict[_Key, list[_Key]] = defaultdict(list)
        above: dict[_Key, list[_Key]] = defaultdict(list)
        for lower, upper in group:
            below[upper].append(lower)
            above[lower].append(upper)
        bands[layer] = _Band(pairs=tuple(group), below=dict(below), above=dict(above))
    return bands


def _positions(keys: list[_Key]) -> dict[_Key, int]:
    """Each occupant's index in its layer."""
    return {key: index for index, key in enumerate(keys)}


def _band_crossings(band: _Band, below: dict[_Key, int], above: dict[_Key, int]) -> int:
    """How many pairs of this band's segments cross.

    Two segments cross when one starts left of the other and ends right of it.
    Segments meeting at an occupant never cross — they share that position, so
    neither comparison is strict — which is the same exclusion the hop scan
    makes on node identity.
    """
    placed = [(below[lower], above[upper]) for lower, upper in band.pairs]
    return sum(
        1
        for (first_low, first_high), (second_low, second_high) in combinations(placed, 2)
        if (first_low - second_low) * (first_high - second_high) < 0
    )


def _crossings(order: dict[int, list[_Key]], bands: dict[int, _Band]) -> int:
    """The whole sheet's crossing count under one ordering — the sweep's score."""
    positions = {layer: _positions(keys) for layer, keys in order.items()}
    return sum(_band_crossings(band, positions[layer], positions[layer + 1]) for layer, band in bands.items())


def _reordered(keys: list[_Key], neighbours: dict[_Key, list[_Key]], positions: dict[_Key, int]) -> list[_Key]:
    """One layer re-sorted on each occupant's barycentre across one band.

    An occupant with no neighbour in that band scores its own current index, so
    it neither drifts nor blocks: it holds its place among the occupants that
    did move. Ties sort by current index, which keeps the pass stable and is
    what carries the start order through a layer nothing constrains.
    """
    scored = []
    for index, key in enumerate(keys):
        adjacent = neighbours.get(key, ())
        rank = Fraction(sum(positions[other] for other in adjacent), len(adjacent)) if adjacent else Fraction(index)
        scored.append((rank, index))
    return [keys[index] for _, index in sorted(scored)]


def _pass(order: dict[int, list[_Key]], bands: dict[int, _Band], *, upward: bool) -> None:
    """One sweep in one direction, in place: each layer reordered against its already-placed neighbour."""
    for layer in sorted(bands, reverse=not upward):
        band = bands[layer]
        moving, fixed = (layer + 1, layer) if upward else (layer, layer + 1)
        neighbours = band.below if upward else band.above
        order[moving] = _reordered(order[moving], neighbours, _positions(order[fixed]))


def _snapshot(order: dict[int, list[_Key]]) -> dict[int, list[_Key]]:
    """A copy the passes cannot reach into — each layer's list is rewritten in place."""
    return {layer: list(keys) for layer, keys in order.items()}


def _sweep(occupants: dict[int, list[_Key]], bands: dict[int, _Band]) -> dict[int, list[_Key]]:
    """The iterated barycentre sweep, returning the best-scoring order it reached.

    Best-scoring rather than last-reached: a round can trade one band's
    crossings for another's, and the start order is in the running, so the sweep
    cannot return a worse picture than the one it was handed.
    """
    order = _snapshot(occupants)
    best, best_score = _snapshot(order), _crossings(order, bands)
    for _ in range(SWEEP_ROUNDS):
        _pass(order, bands, upward=True)
        _pass(order, bands, upward=False)
        score = _crossings(order, bands)
        if score >= best_score:
            break
        best, best_score = _snapshot(order), score
    return best


# ---------------------------------------------------------------------------
# Coordinates — the priority method
# ---------------------------------------------------------------------------


def _priority(key: _Key, neighbours: dict[_Key, list[_Key]]) -> tuple[int, int]:
    """One occupant's claim on its own position: a dummy first, then degree.

    Read as a tuple so the two rank against each other without a sentinel
    number: **a dummy outranks every real node**, which is what straightens a
    long edge's chain and makes the real nodes give way to it, and among real
    nodes the one with more neighbours to answer to moves first.
    """
    return (1 if len(key) > 1 else 0, len(neighbours.get(key, ())))


def _mean(values: Iterable[int]) -> int:
    """The barycentre of some positions, rounded to the nearer integer, exactly.

    Integer arithmetic throughout — this quantity *is* a coordinate, so it
    rounds here rather than at emission, and nothing about where a box lands may
    depend on a binary fraction.
    """
    positions = list(values)
    total, count = sum(positions), len(positions)
    return (2 * total + count) // (2 * count)


def _blocker(keys: list[_Key], priorities: dict[_Key, tuple[int, int]], *, index: int, step: int) -> int:
    """The first occupant in ``step``'s direction that will not give way.

    Everything between here and it has strictly lower priority and can be
    pushed. The returned position may be one past the layer's own end, which is
    the unbounded case and needs no second spelling.
    """
    position = index + step
    while 0 <= position < len(keys) and priorities[keys[position]] < priorities[keys[index]]:
        position += step
    return position


def _shift(
    keys: list[_Key],
    xs: dict[_Key, int],
    priorities: dict[_Key, tuple[int, int]],
    *,
    index: int,
    target: int,
) -> None:
    """Move one occupant toward ``target``, pushing only occupants it outranks.

    The two bounds are the whole of what keeps this phase from undoing the
    ordering phase: the mover stops dead against the first occupant of equal or
    higher priority, and every occupant it does push keeps at least
    :data:`style.COLUMN_PITCH` of separation — so no occupant ever passes
    another and the boxes keep the gutter between them.
    """
    key = keys[index]
    step = 1 if target > xs[key] else -1
    blocker = _blocker(keys, priorities, index=index, step=step)
    if 0 <= blocker < len(keys):
        # Everything between here and the blocker must still fit at that
        # separation, so the blocker's own position is what caps this move.
        limit = xs[keys[blocker]] - (blocker - index) * style.COLUMN_PITCH
        if step * (target - limit) > 0:
            target = limit
    if step * (target - xs[key]) <= 0:
        return

    xs[key] = target
    for position in range(index + step, blocker, step):
        # The pushed follow only as far as the separation demands, never the
        # whole distance the mover travelled.
        pushed = xs[keys[position - step]] + step * style.COLUMN_PITCH
        if step * (pushed - xs[keys[position]]) > 0:
            xs[keys[position]] = pushed


def _align(
    keys: list[_Key],
    xs: dict[_Key, int],
    neighbours: dict[_Key, list[_Key]],
    fixed: dict[_Key, int],
) -> None:
    """One layer pulled toward the layer across one band, in priority order.

    ``reverse=True`` leaves equal priorities in the layer's own drawn order,
    which is the stated tie rule and the reason nothing here reads an index
    twice.
    """
    priorities = {key: _priority(key, neighbours) for key in keys}
    for index, key in sorted(enumerate(keys), key=lambda item: priorities[item[1]], reverse=True):
        adjacent = neighbours.get(key, ())
        if not adjacent:
            # No barycentre to be pulled toward. It holds its place and is
            # pushed like any other occupant.
            continue
        _shift(keys, xs, priorities, index=index, target=_mean(fixed[other] for other in adjacent))


def _coordinate_pass(
    order: dict[int, list[_Key]],
    xs: dict[int, dict[_Key, int]],
    bands: dict[int, _Band],
    *,
    upward: bool,
) -> None:
    """One coordinate sweep in one direction, in place — the ordering pass's shape, over positions."""
    for layer in sorted(bands, reverse=not upward):
        band = bands[layer]
        moving, fixed = (layer + 1, layer) if upward else (layer, layer + 1)
        _align(order[moving], xs[moving], band.below if upward else band.above, xs[fixed])


def _edge_length(xs: dict[int, dict[_Key, int]], bands: dict[int, _Band]) -> int:
    """The total horizontal edge length — what this phase minimises, and its score.

    One term per drawn segment, because every segment is layer-adjacent: a chain
    that runs straight up through its placeholders contributes nothing, and one
    that zigzags through them pays for every bend.
    """
    return sum(
        abs(xs[layer + 1][upper] - xs[layer][lower]) for layer, band in bands.items() for lower, upper in band.pairs
    )


def _score(xs: dict[int, dict[_Key, int]], bands: dict[int, _Band]) -> tuple[int, int]:
    """What one round is judged on: total edge length first, then the sheet's width.

    The width is a tie-break and never an objective. Total length is *flat*
    wherever a vertex sits anywhere between two neighbours, so on its own it
    cannot tell a round that centred such a vertex from one that also prised the
    sheet open around it; between two pictures whose edges are equally long, the
    narrower one is the one a reader can take in.
    """
    positions = [position for row in xs.values() for position in row.values()]
    return (_edge_length(xs, bands), max(positions, default=0) - min(positions, default=0))


def _anchored(xs: dict[int, dict[_Key, int]]) -> dict[int, dict[_Key, int]]:
    """Shift the whole sheet so its leftmost occupant stands where column zero stood.

    One global shift and never a per-layer one: a per-layer shift is the
    alignment this phase just bought, thrown away. Applied after every round and
    not only at the end, because a configuration nothing pins can translate
    sideways for ever without changing — and a round that moved nothing but the
    frame must read as the fixed point it is.
    """
    offset = style.BOX_WIDTH // 2 - min((position for row in xs.values() for position in row.values()), default=0)
    return {layer: {key: position + offset for key, position in row.items()} for layer, row in xs.items()}


def _centres(order: dict[int, list[_Key]], bands: dict[int, _Band]) -> dict[int, dict[_Key, int]]:
    """Each occupant's centre x, by the priority method — the module docstring's phase 5.

    Starts from the column grid the ordering alone used to draw and returns the
    best-scoring round it reached: that grid is in the running, so the phase
    cannot hand back a sheet whose edges are longer than the one it was given.
    """
    grid = {layer: {key: index * style.COLUMN_PITCH for index, key in enumerate(keys)} for layer, keys in order.items()}
    xs = _anchored(grid)
    best, best_score = xs, _score(xs, bands)
    for _ in range(COORDINATE_ROUNDS):
        previous = xs
        xs = {layer: dict(row) for layer, row in xs.items()}
        _coordinate_pass(order, xs, bands, upward=True)
        _coordinate_pass(order, xs, bands, upward=False)
        xs = _anchored(xs)
        score = _score(xs, bands)
        if score <= best_score:
            # Ties go to the swept round rather than to the grid: where both
            # halves of the score are flat it is the barycentre that decides,
            # and putting a vertex on it is what this phase is.
            best, best_score = xs, score
        if xs == previous:
            break
    return best


# ---------------------------------------------------------------------------
# Edges and the sheet extent
# ---------------------------------------------------------------------------


def _dummy_point(layer: int, centre: int) -> Point:
    """Where a chain bends in one layer: its dummy's own centre, at the layer's middle.

    The same x a real box placed there would put its anchors on, and the layer's
    own vertical middle — so the bend sits between the anchors it joins and a
    chain reads as one line rather than as a stack of hinges.
    """
    return Point(centre, -layer * style.LAYER_PITCH + style.BOX_HEIGHT // 2)


def _place_edge(
    edge: Edge,
    source: PlacedNode,
    target: PlacedNode,
    *,
    centres: dict[int, dict[_Key, int]],
    cycle_members: frozenset[str],
) -> PlacedEdge:
    """The edge's chain: the lower anchor, a dummy per layer crossed, the upper anchor."""
    lower, upper = sorted((source, target), key=lambda p: (p.layer, p.column))
    pair = (edge.source, edge.target)
    bends = tuple(_dummy_point(layer, centres[layer][pair]) for layer in _intervening_layers(lower.layer, upper.layer))
    return PlacedEdge(
        edge=edge,
        points=(lower.top_centre, *bends, upper.bottom_centre),
        # Every intra-cycle edge is a back-edge defect.
        back_edge=edge.source in cycle_members and edge.target in cycle_members,
    )


def _view_box(nodes: tuple[PlacedNode, ...], edges: tuple[PlacedEdge, ...]) -> ViewBox:
    """The union of every drawn box and every drawn point, plus :data:`style.MARGIN`.

    The stroke points are in it because a chain bends at a dummy's column, which
    may sit right of the widest box on the sheet.
    """
    if not nodes:
        return ViewBox(-style.MARGIN, -style.MARGIN, 2 * style.MARGIN, 2 * style.MARGIN)
    points = [point for edge in edges for point in edge.points]
    xs = [node.x for node in nodes] + [node.x + node.width for node in nodes] + [point.x for point in points]
    ys = [node.y for node in nodes] + [node.y + node.height for node in nodes] + [point.y for point in points]
    min_x, min_y = min(xs) - style.MARGIN, min(ys) - style.MARGIN
    max_x, max_y = max(xs) + style.MARGIN, max(ys) + style.MARGIN
    return ViewBox(min_x, min_y, max_x - min_x, max_y - min_y)


# ---------------------------------------------------------------------------
# Hop detection and hop side
# ---------------------------------------------------------------------------


def _hops(edges: tuple[PlacedEdge, ...]) -> tuple[Hop, ...]:
    """Every crossing in the drawn edge set, one glyph each.

    The test is per **segment**: an edge is a chain, so two edges may cross more
    than once and each crossing is its own glyph on its own run. O(E^2) in the
    edges and quadratic in their segments, and deliberately the naive
    implementation: at corpus scale it is free, and determinism by stable keys
    is worth more than asymptotics. An accelerated replacement would have to
    produce the identical glyph set, proven by an equivalence test — an
    acceleration that changes the picture has changed the contract.

    Multiple hops on one edge are independent: each crossing yields its own
    glyph at its own point, and two glyphs closer than twice the hop radius abut
    or overlap rather than being merged. Merging is order-dependent, and a
    congested region reading as congested is information.
    """
    # Each chain's runs, built once: the scan visits every pair of edges, and
    # rebuilding a tuple of segments inside that loop is the whole cost of it.
    segments = {edge.key: edge.segments for edge in edges}
    out: list[Hop] = []
    for position, first in enumerate(edges):
        for second in edges[position + 1 :]:
            if _share_a_node(first.edge, second.edge):
                # Every fan-in and fan-out shares an endpoint; a naive test
                # flags all of them and the sheet fills with glyphs at every
                # junction. The test is on node identity, never on coincident
                # coordinates.
                continue
            out.extend(_segment_hops(*_hop_side(first, second), segments=segments))
    return tuple(sorted(out, key=lambda hop: (hop.hopping, hop.hopping_segment, hop.crossed, hop.crossed_segment)))


def _segment_hops(
    hopping: PlacedEdge,
    crossed: PlacedEdge,
    *,
    segments: dict[tuple[str, str], tuple[Segment, ...]],
) -> Iterable[Hop]:
    """Every crossing between two chains, one per crossing pair of their runs."""
    for hopping_index, hopping_segment in enumerate(segments[hopping.key]):
        for crossed_index, crossed_segment in enumerate(segments[crossed.key]):
            crossing = _crossing(hopping_segment, crossed_segment)
            if crossing is None:
                continue
            yield Hop(
                hopping=hopping.key,
                hopping_segment=hopping_index,
                crossed=crossed.key,
                crossed_segment=crossed_index,
                x=crossing[0],
                y=crossing[1],
                direction=_delta(hopping_segment),
            )


def _share_a_node(first: Edge, second: Edge) -> bool:
    """True when the two strokes meet at a node, by id and never by coordinate."""
    return bool({first.source, first.target} & {second.source, second.target})


def _hop_side(first: PlacedEdge, second: PlacedEdge) -> tuple[PlacedEdge, PlacedEdge]:
    """Which of the two hops: the one whose ``(source, target)`` sorts greater.

    A stable key, stated as one so that it cannot quietly become the order the
    scan happened to visit the pair in. The pair is the whole key — ``relation``
    is redundant after the dedupe and is undefined for a two-relation conflict
    edge.
    """
    if first.key > second.key:
        return (first, second)
    return (second, first)


def _crossing(first: Segment, second: Segment) -> tuple[Fraction, Fraction] | None:
    """The point strictly interior to both segments, or ``None``.

    Exact integer arithmetic with no epsilon. With integer endpoints the
    cross-product denominator is exact, so ``den == 0`` is the whole
    parallel/colinear test — a colinear overlap draws as one line and carries no
    glyph — and each parameter is compared as a numerator against that
    denominator, sign-aware, rather than divided out. Only the returned point
    divides, exactly, into a :class:`~fractions.Fraction`.
    """
    origin, run = first.start, _delta(first)
    other_origin, other_run = second.start, _delta(second)
    den = run[0] * other_run[1] - run[1] * other_run[0]
    if den == 0:
        return None

    offset = (other_origin.x - origin.x, other_origin.y - origin.y)
    first_num = offset[0] * other_run[1] - offset[1] * other_run[0]
    second_num = offset[0] * run[1] - offset[1] * run[0]
    if den < 0:
        den, first_num, second_num = -den, -first_num, -second_num
    if not (0 < first_num < den and 0 < second_num < den):
        return None

    along = Fraction(first_num, den)
    return (origin.x + along * run[0], origin.y + along * run[1])


def _delta(segment: Segment) -> tuple[int, int]:
    """The segment's vector, lower end to upper end."""
    return (segment.end.x - segment.start.x, segment.end.y - segment.start.y)


__all__ = [
    "NON_PREMISE_RELATIONS",
    "PREMISE_INVERTING_RELATION",
    "Hop",
    "PlacedEdge",
    "PlacedNode",
    "Point",
    "Segment",
    "Sheet",
    "ViewBox",
    "place",
]
