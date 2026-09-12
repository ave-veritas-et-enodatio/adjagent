"""Single source of the KB metadata-schema vocabulary for the KB toolchain.

Both the build-side scripts (``kb_tools/``) and the read-side query package
(``kb_tools/kb_cmd/``) derive the KB's schema vocabulary — the build-band ladder
and the id grammar — from this module rather than re-typing the literals
themselves. It is the definition of that vocabulary rather than a copy of one,
and is anchored beside :mod:`kb_util` in the ``kb_tools`` package (the single source of
path truth), so any consumer imports it directly as ``from kb_tools import
kb_schema``.

Four vocabularies live here:

* the **build-band ladder** — the ordered solidity → (slug, status-phrase,
  display-label) mapping written into each claim's ``build_band`` field by the
  derived-index pipeline and rendered by the query dashboard,
* the **id grammar** — the ``[a-z0-9]{6}`` hash alphabet and the ``clm`` / ``exp``
  / ``sup`` kind tokens that make up a canonical node id,
* the **node-kind vocabulary** — :data:`NODE_KINDS`, every value a
  ``node_type`` takes, which a census, a breakdown, a union or an ordering
  iterates rather than spelling, and
* the **derived-field placeholders** — the identity value of each field
  ``refresh`` computes and nothing else may.

Stdlib only.
"""

import secrets
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import TypeVar

# ---------------------------------------------------------------------------
# Derived-field placeholders
# ---------------------------------------------------------------------------
#
# Each of these is the identity value of a field ``refresh`` derives and no
# other writer may compute. They live here rather than beside either writer
# because both need them and neither may import the other: ``refresh`` writes
# them when a value is unavailable, and ``kb_write.render`` writes them into a
# freshly inserted entry so that the first refresh over it either fills the slot
# or leaves it byte-identical. Keep no second spelling in either writer: a copy
# pinned to this one by test is equality maintained rather than sameness
# guaranteed.

#: The authored/derived "not yet assessed" literal — the one spelling, so a
#: value written by one tool is recognized by every other.
PENDING_LITERAL = "*pending*"

#: The canonical ``- solidity:`` slot on an entry with no computable solidity,
#: and on every freshly inserted one. Refresh *replaces* this line and never
#: inserts one, so an entry rendered without the slot never receives a value.
SOLIDITY_PENDING_LINE = f"- solidity: {PENDING_LITERAL}"

#: The canonical ``(solidity …)`` annotation on a claim-target depends-on
#: bullet whose target has no computable solidity. Refresh substitutes *into* an
#: existing annotation and never adds one, so this too is required rather than
#: decorative.
SOLIDITY_ANNOTATION_PENDING = f"(solidity {PENDING_LITERAL})"

#: The derived leaf-references footer's line prefix — what the locator both
#: refresh and verify use recognizes a footer by.
LEAF_REFERENCES_PREFIX = "> **Leaf references:**"

#: The canonical leaf-references footer for an entry no leaf cites yet — which
#: is every entry at the moment it is inserted. The defect is spelled out in the
#: file rather than left as a bare prefix.
LEAF_REFERENCES_PENDING_FOOTER = (
    f"{LEAF_REFERENCES_PREFIX} *(none — entry has no citing leaf; bidirectional-coverage check will fail)*"
)

# ---------------------------------------------------------------------------
# Build-band ladder
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class BuildBand:
    """One rung of the solidity → build-band ladder.

    ``threshold`` is the inclusive lower bound on solidity for this band.
    ``status_phrase`` is the full ``build_status`` parenthetical text (without
    the surrounding parens) written back into claim-quality entries by the
    refresh pipeline. ``label`` is the short display label rendered by the
    query dashboard; it usually coincides with ``status_phrase`` but is
    intentionally allowed to diverge (see ``input-only``).
    """

    slug: str
    threshold: float
    status_phrase: str
    label: str


# Descending-threshold order. The ``input-only`` band's display label
# ("use as input only") is deliberately SHORTER than its status phrase
# ("use as input only, don't build deeper"); every other band's label and
# phrase coincide. Both fields are carried so each consumer reproduces its
# current output byte-for-byte.
BUILD_BAND_LADDER: tuple[BuildBand, ...] = (
    BuildBand("ok-to-build", 0.85, "ok to build on", "ok to build on"),
    BuildBand("ok-with-caveats", 0.65, "ok to build on, see caveats", "ok to build on, see caveats"),
    BuildBand("input-only", 0.45, "use as input only, don't build deeper", "use as input only"),
    BuildBand("do-not-build", 0.20, "do not build on, rework needed", "do not build on, rework needed"),
    BuildBand("refuted", 0.00, "refuted, do not use", "refuted, do not use"),
)

# The slug for a claim whose solidity is unassessed (null). Distinct from any
# scored band — it is the pending bucket, not a rung of the ladder.
UNKNOWN_BAND_SLUG = "unknown"


def band_for_solidity(solidity: float | None) -> BuildBand | None:
    """Return the :class:`BuildBand` for ``solidity``, or ``None`` if unscored.

    ``None`` solidity (unassessed) yields ``None`` — the caller maps that to
    :data:`UNKNOWN_BAND_SLUG`. A numeric solidity selects the first band whose
    ``threshold`` is ``<=`` it, walking the ladder in descending order. A
    solidity below the documented ``[0, 1]`` domain (negative) falls through to
    the last band (``refuted``).
    """
    if solidity is None:
        return None
    for band in BUILD_BAND_LADDER:
        if solidity >= band.threshold:
            return band
    return BUILD_BAND_LADDER[-1]


# ---------------------------------------------------------------------------
# ID grammar
# ---------------------------------------------------------------------------

# Canonical node-id kinds, in the order alternations must preserve.
ID_KINDS: tuple[str, ...] = ("clm", "exp", "sup")

# The hash body shared by every node id: six lowercase alphanumerics.
HASH_RE = r"[a-z0-9]{6}"

# Authored placeholder ids (the literal `xxxxxx` body) — never real nodes.
ID_PLACEHOLDERS = frozenset({"clm-xxxxxx", "exp-xxxxxx", "sup-xxxxxx"})


def id_body(*kinds: str) -> str:
    """Return the regex BODY matching a node id of the given ``kinds``.

    No anchors and no capture group — callers wrap it with their own anchors
    (``\\b(...)\\b``), capture, or comment-marker context. ``kinds`` defaults
    to all of :data:`ID_KINDS`; requested kinds must be a subset of it (raises
    :class:`ValueError` otherwise) and the alternation preserves ``ID_KINDS``
    ordering regardless of argument order. One kind yields ``clm-[a-z0-9]{6}``;
    multiple yield ``(?:clm|exp)-[a-z0-9]{6}``.
    """
    selected = kinds or ID_KINDS
    unknown = [k for k in selected if k not in ID_KINDS]
    if unknown:
        raise ValueError(f"unknown id kind(s) {unknown!r}; valid kinds are {ID_KINDS!r}")
    ordered = tuple(k for k in ID_KINDS if k in selected)
    if len(ordered) == 1:
        prefix = ordered[0]
    else:
        prefix = f"(?:{'|'.join(ordered)})"
    return f"{prefix}-{HASH_RE}"


# ---------------------------------------------------------------------------
# The external-work id grammar
# ---------------------------------------------------------------------------
#
# An external work is the one graph node this toolchain does NOT mint: its
# identity is the citation key the corpus already carries, so there is no body
# to draw and no collision to check. `work-` therefore sits outside `ID_KINDS`
# — every consumer of that tuple is asking about minting — and is spelled here
# because the id is still a token three readers have to agree on.

#: The kind token every external-work id opens with.
WORK_PREFIX = "work"

#: A citation key, as a bibliography spells one. Both ends are alphanumeric so
#: the token has no trailing punctuation to argue about: a key ending in `.`
#: would run into the sentence period after it in a depends-on bullet's head.
WORK_KEY_RE = r"[A-Za-z0-9](?:[A-Za-z0-9_.:+/-]*[A-Za-z0-9])?"

#: The whole id: the prefix plus the key, with no separator of its own beyond
#: the hyphen — so `work-goldsmith-pinkham2020` is one id and its key is
#: everything past the first hyphen.
WORK_ID_RE = rf"{WORK_PREFIX}-{WORK_KEY_RE}"


def work_id(key: str) -> str:
    """The node id of the work cited as ``key``.

    Identity is derived, never assigned: two volumes citing one key compute one
    id here, which is what makes a work cited three times one node rather than
    three.
    """
    return f"{WORK_PREFIX}-{key}"


def work_key(node_id: str) -> str | None:
    """The citation key inside ``node_id``, or ``None`` if it is not a work id."""
    prefix, _, key = node_id.partition("-")
    return key if prefix == WORK_PREFIX and key else None


# Character set and length backing the hash body, used to MINT new ids.
# These mirror ``HASH_RE`` (``[a-z0-9]{6}``); keep the two in sync.
HASH_ALPHABET = "abcdefghijklmnopqrstuvwxyz0123456789"
HASH_LEN = 6


def mint_id(prefix: str = "clm", *, existing: Iterable[str] = ()) -> str:
    """Mint one fresh node id of kind ``prefix`` not already in ``existing``.

    ``prefix`` must be one of :data:`ID_KINDS`. The body is a cryptographically
    random string of :data:`HASH_LEN` chars drawn from :data:`HASH_ALPHABET`
    (matching :data:`HASH_RE`), re-rolled until it collides with nothing in
    ``existing``. Ids are authored once and stable thereafter; this randomness
    is only at mint time and never enters the deterministic ``.index`` rebuild.
    """
    if prefix not in ID_KINDS:
        raise ValueError(f"unknown id kind {prefix!r}; valid kinds are {ID_KINDS!r}")
    seen = set(existing)
    while True:
        candidate = f"{prefix}-" + "".join(secrets.choice(HASH_ALPHABET) for _ in range(HASH_LEN))
        if candidate not in seen:
            return candidate


# ---------------------------------------------------------------------------
# The node-kind vocabulary
# ---------------------------------------------------------------------------

#: Every value a node's ``node_type`` takes, in the order a census reports
#: them. This is the vocabulary: a census, a breakdown, a union or an ordering
#: iterates it, and no consumer keeps a second copy — a kind added here reaches
#: every such site by that alone.
#:
#: It answers a different question from :data:`ID_KINDS`, which is what gets
#: *minted*. ``work`` is here and not there because nothing mints one (its id is
#: the citation key), and the framework pair is here under the labels their
#: records carry rather than under an id prefix, there being no prefix to carry.
NODE_KINDS: tuple[str, ...] = ("claim", "support", "experiment", "invariant", "axiom", "work")

#: The two kinds that share the framework shape — solidity-1.0 by definition,
#: carrying no scoring fields. A subset of :data:`NODE_KINDS`, spelled here
#: because "is this node bedrock?" is a question several consumers ask.
FRAMEWORK_KINDS: tuple[str, ...] = ("invariant", "axiom")

_T = TypeVar("_T")


def kind_table(table: Mapping[str, _T], *, what: str) -> dict[str, _T]:
    """Return ``table`` after proving it names every kind in :data:`NODE_KINDS`.

    A site that *branches* per kind keeps naming kinds — that is what makes it a
    branch rather than an iteration — and this is what stops one from naming
    only some of them. A kind the table omits and a key that is no kind both
    raise, naming ``what`` and the offending kinds, at the moment the table is
    defined. The failure a kind added to the vocabulary would otherwise cause is
    a fall-through to whichever branch happens to be last, which is how an
    external work drew as a ghost.

    The returned table is keyed in :data:`NODE_KINDS` order whatever order it
    was written in, so iterating a kind table is iterating the vocabulary and no
    site's own layout reaches an output position.
    """
    missing = [kind for kind in NODE_KINDS if kind not in table]
    unknown = sorted(set(table) - set(NODE_KINDS))
    if missing or unknown:
        detail = "".join(
            (f"; handles no {missing!r}" if missing else "", f"; names non-kind(s) {unknown!r}" if unknown else "")
        )
        raise ValueError(f"{what}: not total over the node-kind vocabulary {NODE_KINDS!r}{detail}")
    return {kind: table[kind] for kind in NODE_KINDS}


def node_kind_plural(kind: str) -> str:
    """The census bucket name for ``kind`` — how a count of them is labelled.

    Every kind in the vocabulary pluralizes with a bare ``s``; the rule is
    stated once here so that a report, a ``stats`` map key and a test agree on
    the label without any of them keeping a table of them.
    """
    if kind not in NODE_KINDS:
        raise ValueError(f"unknown node kind {kind!r}; valid kinds are {NODE_KINDS!r}")
    return f"{kind}s"
