"""Single source of the KB metadata-schema vocabulary for the KB toolchain.

Both the build-side scripts (``kb_tools/``) and the read-side query package
(``kb_tools/kb_cmd/``) derive the KB's schema vocabulary — the build-band ladder
and the id grammar — from this module rather than re-typing the literals
themselves. It mirrors the human-readable spec in ``METADATA_SCHEMA.md`` and is
anchored beside :mod:`kb_util` in the ``kb_tools`` package (the single source of
path truth), so any consumer imports it directly as ``from kb_tools import
kb_schema``.

Two vocabularies live here, each previously re-encoded across several tools:

* the **build-band ladder** — the ordered solidity → (slug, status-phrase,
  display-label) mapping written into each claim's ``build_band`` field by the
  derived-index pipeline and rendered by the query dashboard, and
* the **id grammar** — the ``[a-z0-9]{6}`` hash alphabet and the ``clm`` / ``exp``
  / ``sup`` kind tokens that make up a canonical node id.

Stdlib only.
"""

import secrets
from collections.abc import Iterable
from dataclasses import dataclass

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
