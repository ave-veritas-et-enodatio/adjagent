"""Every presentation constant the claim-graph sheet is drawn with.

The pitches, the box padding, the character advance and the pinned font, the
label cap, the band palette, the dash patterns, the hop radius, and the declared
order tuples. This module imports :mod:`kb_tools.kb_schema` for the band ladder
and nothing else.

**Every magnitude here is an ``int``.** That is what makes every coordinate
:mod:`kb_tools.kb_graph.layout` computes exact, what makes the crossing test
divide nothing, and what keeps a golden diff from widening under floating-point
noise. A float in this module would undo all three.

**Geometry is metric-free.** Box width is a function of a character count and
the font pinned here; nothing measures text, and the emitted document names the
same font it was sized against, so the boxes fit in a renderer nobody ran.

**Nothing here gates.** No refusal, no exit code and no check derives from any
constant in this module. They decide how the sheet looks and nothing else.

Stdlib only.
"""

from kb_tools import kb_schema

# ---------------------------------------------------------------------------
# Type metrics — the pinned font, and the advance the boxes are sized by
# ---------------------------------------------------------------------------

#: The font the sheet is sized against and the font it asks for. A family list
#: rather than one name because no single monospace family is present on every
#: host; every member of it — and the generic ``monospace`` fallback — advances
#: at or below :data:`CHAR_ADVANCE` for :data:`FONT_SIZE`, so a box sized here
#: fits its label in any of them.
FONT_FAMILY = "DejaVu Sans Mono, Menlo, Consolas, monospace"

FONT_SIZE = 12

#: Horizontal advance of one character at :data:`FONT_SIZE`. Monospace faces
#: advance at roughly 0.6 em (7.2 px here); this is rounded **up** to an integer
#: so a label can only ever be narrower than the box it was sized for.
CHAR_ADVANCE = 8

#: Baseline-to-baseline distance of the node label's two lines.
LINE_HEIGHT = 16

#: The cap on **every** label line — the node's id as much as its title. A
#: longer line is truncated to this many characters with
#: :data:`TRUNCATION_ELLIPSIS`, and the whole of both goes in the node's
#: ``title`` child, where it costs no layout at all. One cap and no exception is
#: what makes this the sheet's single width lever: nothing drawn inside a box
#: can be wider than the box, so :data:`BOX_WIDTH` is a width the labels fit
#: rather than a width that usually holds.
#:
#: **Fourteen is the knee of the real title distribution, not a round number.**
#: Over 679 claims across 50 built arXiv KBs, a cap of 14 holds 70% of titles
#: whole against 83% at 28 — the 14 characters after it buy 14 points and the
#: sheet's other half. What sits under the knee is the family SPEC.md point 12
#: calls the norm: a block the author gave no optional argument renders its
#: environment's printed word and its number, and ``Proposition 12`` is exactly
#: 14 characters. Cutting below it starts truncating those, which is the one
#: on-box title worth drawing — an author-titled claim is a sentence that no cap
#: holds, and the tooltip and the hyperlink are what a reader reaches for there.
#:
#: A minted id is ten characters by :mod:`kb_tools.kb_schema`'s grammar and a
#: framework id is twelve as authored, so both stand whole under this cap; a
#: ``work-`` id is a citation key and is bounded by nothing, which is why the
#: line truncates rather than being assumed to fit.
LABEL_CHARS = 14

#: The single character a truncated title ends with.
TRUNCATION_ELLIPSIS = "…"

# ---------------------------------------------------------------------------
# Box and grid geometry
# ---------------------------------------------------------------------------

BOX_PADDING_X = 8
BOX_PADDING_Y = 6

#: Box width, a **constant** — a function of the label cap and the character
#: advance, never of the label actually drawn, so a long title never moves a
#: neighbour. Since :data:`LABEL_CHARS` caps every line, this is also a width
#: no label exceeds.
BOX_WIDTH = 2 * BOX_PADDING_X + LABEL_CHARS * CHAR_ADVANCE

#: Box height: the two label lines plus padding. Also a constant.
BOX_HEIGHT = 2 * LINE_HEIGHT + 2 * BOX_PADDING_Y

#: Blank space between one column's box and the next. ``BOX_WIDTH`` is
#: ``COLUMN_PITCH - COLUMN_GUTTER`` by construction, which is the grid rule read
#: from the other end: the pitch is a constant, never a cumulative sum of box
#: widths, so inserting a node moves nothing to its right.
#:
#: Two padding widths — the blank between two boxes reads as one box's own
#: horizontal padding on each side, which is the narrowest gap that still
#: separates them. It is also the blank half of the ``COLUMN_PITCH`` separation
#: the coordinate phase holds two occupants apart by at its tightest, so every
#: pixel here is paid once per column of the widest layer.
COLUMN_GUTTER = 16
COLUMN_PITCH = BOX_WIDTH + COLUMN_GUTTER

#: Blank space between one layer's boxes and the next layer's: one box height of
#: air, where it was most of two. It buys no width — the sheet is bound by its
#: widest layer — so what it is spent on is how much of the hierarchy stands on
#: a screen at a zoom that reads the labels.
LAYER_GAP = 44
LAYER_PITCH = BOX_HEIGHT + LAYER_GAP

#: How many orphans stand in one row of the grid block beneath the hierarchy.
#: Four, so the block comes out tall and narrow rather than wide and short: a
#: long vertical scroll is cheaper to read than a long horizontal one, and the
#: block's height then reads at a glance as how much of the KB is unattached.
#: The block takes the layer pitch and the column pitch above it, so it needs no
#: magnitude of its own.
ORPHAN_ROW = 4

#: Added around the union of the box extents to form the ``viewBox``.
#: Comfortably larger than :data:`HOP_RADIUS`, so a glyph on a boundary edge
#: cannot leave the sheet.
MARGIN = 24

#: The radius of the semicircular hop arc, centred on the crossing point.
#: Two glyphs closer than twice this abut or overlap, which is accepted rather
#: than merged: merging is order-dependent, and a congested region reading as
#: congested is information.
HOP_RADIUS = 8

# ---------------------------------------------------------------------------
# Stroke weights
# ---------------------------------------------------------------------------

NODE_STROKE_WIDTH = 1
EDGE_STROKE_WIDTH = 2
HOP_STROKE_WIDTH = 2

#: A ``references`` stroke: half the weight of every other class. Weight is the
#: channel because the other three are already spoken for — colour says how
#: strong, dash says whether the bookkeeping is sound, shape says what kind of
#: node — and because a reference has no strength to show, so its colour
#: (:data:`REFERENCE_EDGE_COLOUR`) is off the ramp and would otherwise be the
#: neutral a *pending* dependency already takes. Weight is also the half that
#: survives greyscale and a screenshot in a review, which the colour alone does
#: not.
REFERENCE_STROKE_WIDTH = 1

# ---------------------------------------------------------------------------
# The band ramp
# ---------------------------------------------------------------------------
#
# Discrete rungs of the project's own build-band ladder, not a continuous
# gradient: a re-scored claim either moves a band or changes no byte, where a
# gradient rewrites a hex triple on every rescore and drowns the cross-run diff.
#
# Two properties bind, and both are constraints rather than taste: no
# distinction is carried by a red/green pair alone, and the ramp is monotone in
# lightness so it survives greyscale and a screenshot in a review. The sequence
# is Okabe-Ito-derived — a sky-blue tint pair, orange, vermillion, and a dark
# vermillion shade — with relative luminance strictly descending along it.

_BAND_RAMP: tuple[str, ...] = (
    "#bfe3f5",  # ok-to-build
    "#7fc4eb",  # ok-with-caveats
    "#e69f00",  # input-only
    "#d55e00",  # do-not-build
    "#7a2e00",  # refuted
)

#: The off-ladder neutral: ``solidity: null`` / ``fraction: "*pending*"``.
#: A colour, never a dash — dashes are reserved for defects, so colour
#: says how strong, dash says whether the bookkeeping is sound.
PENDING_FILL = "#9e9e9e"

#: One colour per rung of :data:`kb_schema.BUILD_BAND_LADDER`, plus the pending
#: bucket under :data:`kb_schema.UNKNOWN_BAND_SLUG`. Keyed by the ladder's own
#: slugs rather than by a retyped list of them, and zipped ``strict`` so a rung
#: added to the ladder fails loudly here instead of rendering uncoloured.
BAND_PALETTE: dict[str, str] = {
    **{band.slug: colour for band, colour in zip(kb_schema.BUILD_BAND_LADDER, _BAND_RAMP, strict=True)},
    kb_schema.UNKNOWN_BAND_SLUG: PENDING_FILL,
}

#: A ``references`` edge — one claim naming another without resting on it.
#: Deliberately **off the ramp**, for :data:`FRAMEWORK_FILL`'s reason read at
#: the other end: the ramp says how strong a thing is, and this class asserts
#: nothing about strength, so painting it at a rung would report a score that
#: does not exist. The hue is the framework lavender's, darkened to stroke
#: weight — one family for "off the ladder by nature", distinct from the grey
#: :data:`PENDING_FILL` means "on the ladder, unscored".
REFERENCE_EDGE_COLOUR = "#8a6fa8"

#: A ``depends`` edge onto an invariant or an axiom. Framework
#: dependencies contribute 1.0 to the solidity min and never pend, so the edge
#: takes the ladder's **top rung** — read from the ladder rather than named as a
#: hex here, because the rung is that rule's answer and not this module's.
FRAMEWORK_EDGE_COLOUR = BAND_PALETTE[kb_schema.BUILD_BAND_LADDER[0].slug]


def band_colour(value: float | str | None) -> str:
    """The ramp colour for one strength value: a ``supports`` edge's on-point
    ``fraction`` or a ``strengthens`` edge's ``strength``.

    The band comes from :func:`kb_schema.band_for_solidity` — the project's own
    ladder, which both the build and the query sides already read — so the ramp
    here is a palette over that vocabulary and never a second one. Anything
    non-numeric is the pending case: ``None``, or the literal
    :data:`kb_schema.PENDING_LITERAL` a fraction may carry. It is recognised by
    *not being a number* rather than by comparing against a typed-out string,
    and it renders in the off-ladder neutral — a colour, never a dash, because
    dashes are reserved for defects.
    """
    numeric = value if isinstance(value, (int, float)) and not isinstance(value, bool) else None
    band = kb_schema.band_for_solidity(numeric)
    return BAND_PALETTE[kb_schema.UNKNOWN_BAND_SLUG if band is None else band.slug]


# ---------------------------------------------------------------------------
# Node and edge presentation
# ---------------------------------------------------------------------------

#: An invariant or axiom: a distinct framework fill, deliberately **not** a rung
#: of the ramp. Bedrock is a convention of the solidity rule rather than a
#: stored value, and painting it at the top rung would assert a score that does
#: not exist.
FRAMEWORK_FILL = "#e3d3ec"

#: An experiment carries no solidity of any kind; its ``status`` goes on its own
#: label line rather than being asserted as a score.
EXPERIMENT_FILL = PENDING_FILL

NODE_STROKE = "#333333"
LABEL_FILL = "#111111"
SHEET_BACKGROUND = "#ffffff"

#: A ghost id: a stub with a distinct outline, its id shown and no title.
STUB_FILL = "none"
STUB_STROKE = "#b00020"

#: A one-hop foreign neighbour on a domain sheet: outline only, no fill.
FOREIGN_FILL = "none"

#: Dash patterns, the one channel reserved for defects. Values are SVG
#: ``stroke-dasharray`` strings rather than magnitudes: nothing computes with
#: them, and no coordinate is derived from one.
DASH_STUB = "3 3"
DASH_BACK_EDGE = "6 4"

#: The mark a stroke carrying two relations for one fact takes.
CONFLICT_STROKE = "#b00020"

# ---------------------------------------------------------------------------
# The declared order tuples
# ---------------------------------------------------------------------------
#
# The ``FACT`` census lines are emitted in these orders and no other. A census
# that iterated a dict of counts would order its lines by whatever the corpus
# happened to contain first, and no incidental order may reach an output
# position.
#
# There is no node-type order here: that one is
# :data:`kb_schema.NODE_KINDS`' own declared order, which is the vocabulary a
# node-type census iterates. A tuple here would be a second spelling of it, and
# the kind it omitted would be the kind the census never reported.

#: Relation census order.
#:
#: Equal in value to :data:`kb_tools.kb_graph.model.RELATION_PRECEDENCE` and
#: deliberately not imported from it: this module's only dependency is
#: ``kb_schema``, and importing ``model`` would break that. The two constants
#: also answer different questions — that one is the dedupe *semantics* (which
#: relation survives a group disagreeing on relation), this one is the *report
#: order*. Their coincidence is pinned by a test rather than asserted here, so a
#: future divergence surfaces as a decision instead of a silent drift.
CENSUS_RELATIONS: tuple[str, ...] = ("depends", "supports", "strengthens", "rests-on", "references")

#: Defect-class census order.
CENSUS_DEFECT_CLASSES: tuple[str, ...] = (
    "ghost-id",
    "back-edge",
    "isolated-node",
    "disconnected-component",
    "relation-conflict",
)
