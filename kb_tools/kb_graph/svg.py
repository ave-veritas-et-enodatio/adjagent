"""Markup for the claim-graph sheet: placed geometry in, one document out.

This is the **only** module in the package that composes markup, and it computes
no coordinate and makes no layout decision. It compares no two node positions —
every position it emits is one :mod:`kb_tools.kb_graph.layout` already decided,
and every offset it adds to one is a label-local constant from
:mod:`kb_tools.kb_graph.style` (the text baselines inside a box, the arc
endpoints along a hop's own direction vector).

**The document is built as an element tree and serialized by it.** No f-string
composes a tag or an attribute here, the pinned XML declaration included — that
declaration is emitted from a processing-instruction node rather than written as
a literal, so the rule has no exception to remember. The reason
is not tidiness: node titles in this corpus carry ``&``, ``$\\beta$``, quotes and
em-dashes, and a hand-rolled escaper is an exhaustiveness obligation with no
mechanical check, where the standard library's escaping is total by
construction. *The trap*: the tree is plain and non-namespaced with the SVG
namespace set as an ordinary attribute on the root — ``ET.QName`` and
``register_namespace`` prefix every tag with ``ns0:`` instead.

**One number formatter, and rounding happens once.** Every coordinate
:mod:`layout` hands over is an ``int`` and emits with no decimal point. The hop
arc is the single non-integral geometry, and its path values carry exactly three
decimals applied at emission, after all the arithmetic — never at an
intermediate step.

The caller writes the returned text with ``encoding="utf-8", newline="\\n"``;
nothing here opens a file.

Stdlib only.
"""

import math
import xml.etree.ElementTree as ET
from collections.abc import Collection, Mapping
from dataclasses import dataclass
from fractions import Fraction
from pathlib import PurePosixPath

from kb_tools import kb_schema
from kb_tools.kb_graph import layout, style

#: Set as a literal attribute on a plain, non-namespaced root. Node
#: links are emitted as ``href`` alone; there is no ``xlink`` namespace.
SVG_NAMESPACE = "http://www.w3.org/2000/svg"

#: The XML declaration, composed by the serializer rather than written
#: out: this package composes no markup by hand, and the declaration is markup.
#: A processing-instruction node serializes to exactly the pinned bytes.
_DECLARATION = ET.tostring(ET.ProcessingInstruction("xml", 'version="1.0" encoding="utf-8"'), encoding="unicode")

#: The serializer call, pinned: two spaces of indentation, then a text
#: serialization, and exactly one trailing newline on the document.
_INDENT = "  "
_NEWLINE = "\n"

#: The kind whose tooltip carries a second value beside its id.
_EXPERIMENT = "experiment"

#: The two edge classes carrying a strength of their own. A ``depends``
#: edge carries neither and is drawn in the colour of the premise it leans on.
_SUPPORTS = "supports"
_STRENGTHENS = "strengthens"

#: The class carrying no strength at all: one claim naming another. It is the
#: one stroke drawn off the ramp and at half weight.
_REFERENCES = "references"

#: An experiment's tooltip carries its ``status`` beside its id; a deduped
#: stroke's tooltip carries its merged contexts.
_ID_LINE_SEPARATOR = " · "
_CONTEXT_SEPARATOR = " · "
_TITLE_ARROW = " → "
_TITLE_DASH = " — "
_RELATIONS_LABEL = "relations: "
_RELATION_SEPARATOR = ", "

#: The one admitted fragment separator. An empty ``canonical_anchor`` yields
#: a path with no fragment rather than a trailing one.
_FRAGMENT = "#"

#: An edge's ``points``: ``x,y`` per vertex, vertices separated by a space.
_COORDINATE_SEPARATOR = ","
_POINT_SEPARATOR = " "

#: The hop arc, as SVG path data. Its bulge is ``n = (d.y, -d.x)`` for the
#: hopping edge's direction ``d`` **read left to right across the sheet**, which
#: is a fixed rotation of ``d`` — so the arc's handedness never varies and the
#: sweep flag is a constant, not a case analysis.
_ARC_SWEEP = "1"


# ---------------------------------------------------------------------------
# The single number formatter
# ---------------------------------------------------------------------------


def format_number(value: int | float | Fraction) -> str:
    """Format one emitted number: integers bare, everything else to three decimals.

    Every coordinate in the frame is an ``int`` because every magnitude in
    :mod:`style` is, so it emits with no decimal point and a golden diff cannot
    widen under floating-point noise. The hop arc is the one
    non-integral geometry, and this is where its rounding happens — once, at
    emission.

    A value that rounds to zero from below emits as ``0.000``: the signed zero
    is the same point and reads as a defect.
    """
    if isinstance(value, int) and not isinstance(value, bool):
        return str(value)
    text = format(float(value), ".3f")
    return text[1:] if text.startswith("-") and float(text) == 0 else text


# ---------------------------------------------------------------------------
# Hyperlinks
# ---------------------------------------------------------------------------


def relative_link_base(*, kb_root: PurePosixPath | str, sheet_dir: PurePosixPath | str) -> PurePosixPath:
    """Where the KB root sits, relative to the directory the sheet is written into.

    ``PurePosixPath`` throughout and never ``os.path``, whose separators are
    platform-dependent and would make the emitted document host-dependent. The
    result is what :func:`node_href` joins a node's kb-root-relative
    ``canonical_path`` onto, so a link resolves from the sheet's own directory.

    Raises:
        ValueError: when one path is absolute and the other is not — there is no
            relative answer between them, and returning a plausible-looking one
            would emit links that resolve nowhere.
    """
    root, here = PurePosixPath(kb_root), PurePosixPath(sheet_dir)
    if root.is_absolute() != here.is_absolute():
        raise ValueError(f"kb_root {str(root)!r} and sheet_dir {str(here)!r} must both be absolute or both relative")

    shared = 0
    for mine, theirs in zip(root.parts, here.parts):
        if mine != theirs:
            break
        shared += 1
    return PurePosixPath(*([".."] * (len(here.parts) - shared)), *root.parts[shared:])


def node_href(placed: layout.PlacedNode, *, link_base: PurePosixPath | str) -> str | None:
    """The link target for one node, or ``None`` when it has no definition to name.

    The index's ``canonical_path`` (kb-root-relative POSIX) under ``link_base``,
    plus the ``canonical_anchor`` as a fragment. An empty anchor — which the
    index emits in one branch — yields a path with **no** fragment rather than a
    trailing one. A stub stands for a ghost id and has no record, so it carries
    no link at all.
    """
    record = placed.node.record
    if record is None:
        return None
    target = str(PurePosixPath(link_base) / record.canonical_path)
    anchor = record.canonical_anchor
    return target + _FRAGMENT + anchor if anchor else target


# ---------------------------------------------------------------------------
# Colour — one entry per node kind
# ---------------------------------------------------------------------------


def _band_fill(placed: layout.PlacedNode) -> str:
    """The palette colour of a node's own stored band, or the pending neutral.

    A claim carries ``build_band`` and a support has one derived on the load
    side; an external work and a stub carry none, and are unscored rather than
    weak, so they take the off-ladder neutral. The absent-band reading is a
    ``getattr`` default because the field's presence is the record type's, and
    the record types are :mod:`kb_tools.kb_cmd.index`'s to define.
    """
    return style.BAND_PALETTE.get(getattr(placed.node.record, "build_band", ""), style.PENDING_FILL)


#: A fixed fill per node kind, or ``None`` where the kind's fill is the node's
#: own band. Total over :data:`kb_schema.NODE_KINDS` by construction: a kind
#: added to the vocabulary and not to this table fails here rather than
#: inheriting whichever branch came last. A framework node takes a fill that is
#: deliberately no rung of the ramp; an experiment and an external work carry no
#: band, and :func:`_band_fill` answers the neutral for them.
_FILL_BY_KIND: dict[str, str | None] = kb_schema.kind_table(
    {
        "claim": None,
        "support": None,
        "work": None,
        "experiment": style.EXPERIMENT_FILL,
        "invariant": style.FRAMEWORK_FILL,
        "axiom": style.FRAMEWORK_FILL,
    },
    what="kb_graph.svg node fills",
)


def _node_fill(placed: layout.PlacedNode, *, foreign: bool) -> str:
    """Fill for one box, across every node kind plus the stub and the foreign one."""
    if foreign:
        return style.FOREIGN_FILL
    node_type = placed.node.node_type
    if node_type is None:
        return style.STUB_FILL
    if node_type not in _FILL_BY_KIND:
        raise ValueError(f"node {placed.id!r} carries node_type {node_type!r}, which is no kind the sheet draws")
    fixed = _FILL_BY_KIND[node_type]
    return _band_fill(placed) if fixed is None else fixed


def _premise_colour(target: layout.PlacedNode) -> str:
    """The strength a ``depends`` edge inherits from the premise it leans on.

    A framework premise takes the ladder's top rung: framework dependencies
    contribute 1.0 to the solidity min and never pend, so the rung is read from
    that rule rather than invented. Every other premise shows its own band — so
    a weak premise's whole fan-out reads weak at every dependent, which is the
    instrument's second role exactly.
    """
    if target.node.node_type in kb_schema.FRAMEWORK_KINDS:
        return style.FRAMEWORK_EDGE_COLOUR
    return _band_fill(target)


@dataclass(frozen=True, slots=True)
class _Stroke:
    """How one edge is drawn: its colour, its dash, and its weight."""

    colour: str
    dash: str | None
    width: int


def _edge_presentation(placed: layout.PlacedEdge, nodes: Mapping[str, layout.PlacedNode]) -> _Stroke:
    """One stroke's presentation, with the precedence between defects stated once.

    A ghost endpoint wins outright — such an edge takes the defect stub style and
    no band, because there is no node whose strength it could be showing. A
    conflict mark then wins over the band, since the fact the colour would report
    is the one in dispute. Dashes are the defect channel and colour is the
    strength channel, so a back edge keeps its band and takes the dash.

    **Weight is the fourth channel and only one class uses it.** A ``references``
    edge asserts nothing about strength, so it takes no rung of the ramp and no
    neutral — the neutral already means *on the ladder, unscored*, which a
    pending dependency is and this is not. It takes
    :data:`style.REFERENCE_EDGE_COLOUR` and half the stroke weight, and the
    weight is what carries the distinction in greyscale.
    """
    edge = placed.edge
    if nodes[edge.source].node.is_stub or nodes[edge.target].node.is_stub:
        return _Stroke(style.STUB_STROKE, style.DASH_STUB, style.EDGE_STROKE_WIDTH)

    dash = style.DASH_BACK_EDGE if placed.back_edge else None
    if edge.conflict:
        return _Stroke(style.CONFLICT_STROKE, dash, style.EDGE_STROKE_WIDTH)
    if edge.relation == _REFERENCES:
        return _Stroke(style.REFERENCE_EDGE_COLOUR, dash, style.REFERENCE_STROKE_WIDTH)
    if edge.relation == _SUPPORTS:
        return _Stroke(style.band_colour(edge.fraction), dash, style.EDGE_STROKE_WIDTH)
    if edge.relation == _STRENGTHENS:
        return _Stroke(style.band_colour(edge.strength), dash, style.EDGE_STROKE_WIDTH)
    return _Stroke(_premise_colour(nodes[edge.target]), dash, style.EDGE_STROKE_WIDTH)


# ---------------------------------------------------------------------------
# Labels
# ---------------------------------------------------------------------------


def _fitted(text: str) -> str:
    """One label line cut to the width of the box it is drawn in.

    :data:`style.LABEL_CHARS` with a single ellipsis character, applied to
    **every** line — the id as much as the title. One rule and no exception is
    what makes the box a width labels fit rather than one they usually fit: a
    ``work-`` id is a citation key, bounded by nothing, and the corpus carries
    them up to 37 characters, so a line left uncut would run across its
    neighbour.
    """
    return text if len(text) <= style.LABEL_CHARS else text[: style.LABEL_CHARS - 1] + style.TRUNCATION_ELLIPSIS


def _label_lines(placed: layout.PlacedNode) -> tuple[str, str]:
    """The box's two lines: the node's id, and its title.

    Both cut to the box (:func:`_fitted`), and neither is where the whole of
    either is read — :func:`_node_title` carries both in full, at no layout
    cost, as a tooltip in every browser. The id line is what a reader carries
    off the sheet to a register, a query or a grep, so it is drawn ahead of the
    title rather than sized around it.
    """
    return _fitted(placed.node.id), _fitted(placed.node.title)


def _node_title(placed: layout.PlacedNode) -> str:
    """The box's tooltip: the whole identity and the whole title, uncut.

    An experiment's ``status`` rides here rather than on the id line. It is
    still shown as its own word and never asserted as a score — which is the
    property the fill already carries — but the id line is the one thing on the
    box a reader acts on, and a status sharing it made the box wide enough to
    hold both on every sheet in the corpus, experiments or none.
    """
    node = placed.node
    identity = node.id
    if node.node_type == _EXPERIMENT:
        identity = identity + _ID_LINE_SEPARATOR + getattr(node.record, "status", "")
    return identity + _TITLE_DASH + node.title


def _edge_title(placed: layout.PlacedEdge) -> str:
    """The stroke's tooltip: the pair, its merged contexts, and any relation conflict.

    Contexts arrive already ascending and collapsed; the head stands alone when
    the group carried none, rather than trailing the separator that would
    introduce them. A stroke carrying two relations for one fact names every
    relation it found.
    """
    edge = placed.edge
    parts = [edge.source + _TITLE_ARROW + edge.target]
    if edge.contexts:
        parts.append(_CONTEXT_SEPARATOR.join(edge.contexts))
    if edge.conflict:
        parts.append(_RELATIONS_LABEL + _RELATION_SEPARATOR.join(edge.relations))
    return _TITLE_DASH.join(parts)


# ---------------------------------------------------------------------------
# Element emitters
# ---------------------------------------------------------------------------


def _root(view_box: layout.ViewBox) -> ET.Element:
    """The sheet root: the namespace as a plain attribute, and the pinned font.

    **The root carries the ``viewBox`` and no ``width`` or ``height``**, so the
    sheet scales to whatever it is placed in. Those two are the intrinsic size a
    viewer lays the document out at, and a real corpus's sheet is some fifteen
    thousand pixels wide: pinned, it opens at that size and can only be zoomed
    *in* from there. Absent, the SVG root takes its initial ``100%`` in both
    axes, so a standalone file fills the window and the default
    ``preserveAspectRatio`` fits the ``viewBox`` inside it at any window size.

    ``width="100%" height="100%"`` reaches the same standalone view and is not
    the same document: a percentage is no intrinsic size, so an ``img`` element
    or a CSS background with no sizing of its own falls back to the replaced
    element's 300×150 default, where the ``viewBox`` alone gives it the sheet's
    own aspect ratio to size by.

    The font sits here rather than on every text element: it is inherited, the
    document names the family and size it was sized against, and a per-node byte
    identity stays as small as it can be.
    """
    extent = (view_box.min_x, view_box.min_y, view_box.width, view_box.height)
    return ET.Element(
        "svg",
        {
            "xmlns": SVG_NAMESPACE,
            "viewBox": " ".join(format_number(value) for value in extent),
            "font-family": style.FONT_FAMILY,
            "font-size": format_number(style.FONT_SIZE),
        },
    )


def _background(view_box: layout.ViewBox) -> ET.Element:
    return ET.Element(
        "rect",
        {
            "class": "sheet",
            "x": format_number(view_box.min_x),
            "y": format_number(view_box.min_y),
            "width": format_number(view_box.width),
            "height": format_number(view_box.height),
            "fill": style.SHEET_BACKGROUND,
        },
    )


def _edge_element(placed: layout.PlacedEdge, *, stroke: "_Stroke") -> ET.Element:
    """One stroke: the whole chain as a single polyline, however many segments.

    ``fill`` is explicitly none because a polyline's default fill closes the
    path — a three-point chain would paint the triangle under itself.
    """
    attrib = {
        "class": "edge",
        "points": _POINT_SEPARATOR.join(
            format_number(point.x) + _COORDINATE_SEPARATOR + format_number(point.y) for point in placed.points
        ),
        "fill": "none",
        "stroke": stroke.colour,
        "stroke-width": format_number(stroke.width),
    }
    if stroke.dash is not None:
        attrib["stroke-dasharray"] = stroke.dash
    element = ET.Element("polyline", attrib)
    ET.SubElement(element, "title").text = _edge_title(placed)
    return element


def _rightward(direction: tuple[int, int]) -> tuple[int, int]:
    """The hopping segment's own direction, read left to right across the sheet.

    The arc's two endpoints ordered by screen x, tie-broken by y where the
    segment is vertical — which is the whole of the rule. ``d.x >= 0``
    afterwards, always.
    """
    x, y = direction
    return direction if (x, y) > (0, 0) else (-x, -y)


def draw_hop_over(hop: layout.Hop, *, colour: str) -> ET.Element:
    """The bridge glyph: a fixed-radius semicircular arc centred on the crossing.

    The endpoints sit at :data:`style.HOP_RADIUS` either side of the crossing
    point along the hopping edge's own direction, **ordered by screen x** —
    and the arc bulges toward ``n = (d.y, -d.x)`` from there, which is screen
    up for every edge that is not vertical and screen right for one that is.
    One rule, no cases: ``n`` is a fixed rotation of ``d``, so the arc's
    handedness is constant and the sweep flag is a constant with it. The
    ordering is what makes that constant handedness a *screen* fact rather than
    an edge-local one — an edge whose graph direction runs right to left is
    drawn right to left, and a rotation of its own vector would dip the bridge
    downward on exactly those edges.

    The straight stroke stays drawn beneath the arc. Erasing the chord under it
    would mean painting over the crossed edge at the very point it passes
    through, which inverts what the glyph says; the bump is the denotation, and
    this renderer denotes a crossing rather than avoiding it.
    """
    direction = _rightward(hop.direction)
    length = math.hypot(*direction)
    along_x = style.HOP_RADIUS * direction[0] / length
    along_y = style.HOP_RADIUS * direction[1] / length
    radius = float(style.HOP_RADIUS)
    data = (
        "M",
        format_number(hop.x - along_x),
        format_number(hop.y - along_y),
        "A",
        format_number(radius),
        format_number(radius),
        "0",
        "0",
        _ARC_SWEEP,
        format_number(hop.x + along_x),
        format_number(hop.y + along_y),
    )
    return ET.Element(
        "path",
        {
            "class": "hop",
            "d": " ".join(data),
            "fill": "none",
            "stroke": colour,
            "stroke-width": format_number(style.HOP_STROKE_WIDTH),
        },
    )


def _box(placed: layout.PlacedNode, *, foreign: bool) -> ET.Element:
    stub = placed.node.is_stub
    attrib = {
        "x": format_number(placed.x),
        "y": format_number(placed.y),
        "width": format_number(placed.width),
        "height": format_number(placed.height),
        "fill": _node_fill(placed, foreign=foreign),
        "stroke": style.STUB_STROKE if stub else style.NODE_STROKE,
        "stroke-width": format_number(style.NODE_STROKE_WIDTH),
    }
    # Dashes are the defect channel: a ghost id's stub and a cycle member's box,
    # the two defects a box itself can carry.
    dash = style.DASH_STUB if stub else style.DASH_BACK_EDGE if placed.cycle_member else None
    if dash is not None:
        attrib["stroke-dasharray"] = dash
    return ET.Element("rect", attrib)


def _label(placed: layout.PlacedNode, *, line: int, text: str) -> ET.Element:
    """One label line, offset inside its own box by :mod:`style` constants alone.

    The baseline of line ``n`` is the box top plus the vertical padding, one
    font size for the first baseline, and one line height per line after it. No
    other box enters the calculation.
    """
    element = ET.Element(
        "text",
        {
            "x": format_number(placed.x + style.BOX_PADDING_X),
            "y": format_number(placed.y + style.BOX_PADDING_Y + style.FONT_SIZE + line * style.LINE_HEIGHT),
            "fill": style.LABEL_FILL,
        },
    )
    element.text = text
    return element


def _node_element(placed: layout.PlacedNode, *, link_base: PurePosixPath | str, foreign: bool) -> ET.Element:
    """One node's group, wrapped in a link when the index names a definition for it."""
    group = ET.Element("g", {"class": "node"})
    if not placed.node.is_stub:
        # First child of the group, which is the condition SVG puts on a
        # `title` being its parent's: the group is the innermost element under
        # the link, so this is the title a viewer resolves from anywhere inside
        # the box. A stub gets none — there is no record to take one from, and
        # an empty tooltip would assert the node has no name.
        ET.SubElement(group, "title").text = _node_title(placed)
    group.append(_box(placed, foreign=foreign))
    for line, text in enumerate(_label_lines(placed)):
        group.append(_label(placed, line=line, text=text))

    href = node_href(placed, link_base=link_base)
    if href is None:
        return group
    anchor = ET.Element("a", {"href": href})
    anchor.append(group)
    return anchor


# ---------------------------------------------------------------------------
# The document
# ---------------------------------------------------------------------------


def build(
    sheet: layout.Sheet,
    *,
    link_base: PurePosixPath | str,
    foreign: Collection[str] = (),
) -> ET.Element:
    """The sheet as an element tree, in the declared draw and emission orders.

    Edges first, then the hop glyphs, then the node boxes with an opaque fill.
    An edge spanning several layers is routed through a dummy column in each
    (:mod:`kb_tools.kb_graph.layout`) rather than sliced across the boxes
    between its ends, so the draw order is what settles the remaining overlaps
    — a stroke meeting a box still passes under it, which is the schematic
    reading and costs nothing.

    ``link_base`` is the KB root relative to the sheet's own directory
    (:func:`relative_link_base`). ``foreign`` is the one-hop neighbour set on a
    domain sheet — the ids drawn in the outline-only foreign style — which is the
    caller's selection policy and not this module's.

    Every order here is the one :class:`layout.Sheet` already fixed, so no set
    or dict iteration reaches an output position.
    """
    root = _root(sheet.view_box)
    root.append(_background(sheet.view_box))

    placed_nodes = {placed.id: placed for placed in sheet.nodes}
    edges = ET.SubElement(root, "g", {"class": "edges"})
    colours: dict[tuple[str, str], str] = {}
    for edge in sheet.edges:
        stroke = _edge_presentation(edge, placed_nodes)
        colours[edge.key] = stroke.colour
        edges.append(_edge_element(edge, stroke=stroke))

    hops = ET.SubElement(root, "g", {"class": "hops"})
    for hop in sheet.hops:
        hops.append(draw_hop_over(hop, colour=colours[hop.hopping]))

    nodes = ET.SubElement(root, "g", {"class": "nodes"})
    for placed in sheet.nodes:
        nodes.append(_node_element(placed, link_base=link_base, foreign=placed.id in foreign))
    return root


def serialize(root: ET.Element) -> str:
    """The pinned serializer call, plus the declaration and one trailing newline.

    Attribute order is insertion order, which the toolchain's 3.11+ floor
    guarantees. The caller writes the result with ``encoding="utf-8",
    newline="\\n"``.
    """
    ET.indent(root, space=_INDENT)
    return _DECLARATION + _NEWLINE + ET.tostring(root, encoding="unicode") + _NEWLINE


def render(
    sheet: layout.Sheet,
    *,
    link_base: PurePosixPath | str,
    foreign: Collection[str] = (),
) -> str:
    """The whole document as text: :func:`build` then :func:`serialize`."""
    return serialize(build(sheet, link_base=link_base, foreign=foreign))


__all__ = [
    "SVG_NAMESPACE",
    "build",
    "draw_hop_over",
    "format_number",
    "node_href",
    "relative_link_base",
    "render",
    "serialize",
]
