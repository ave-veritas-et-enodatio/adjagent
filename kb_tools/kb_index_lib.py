"""Foundation library for the Knowledge Base derived-index pipeline.

Pure-function parsing and record building for the ``<kb-root>/.index/*.jsonl``
files: the record shapes and field orders are the emitters below, which are
their own definition rather than a second view of one. This module is the
canonical parser for
KB frontmatter, claim-quality entries, and leaf metadata; downstream tools
(``refresh_kb_metadata``, ``verify_kb_metadata``) will be unified onto it in
later phases. The library is side-effect-free with respect to KB content; the
only file I/O it performs is reading canonical sources via pathlib and writing
JSONL through ``write_jsonl`` for callers that own a destination path.

Stdlib only. No timestamps, no environment-dependent paths in emitted records.
Same canonical input -> byte-identical output.
"""

import json
import posixpath
import re
import sys
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path, PurePosixPath
from typing import TextIO

# Route all KB path construction through the kb_util module (the single source
# of path truth). Root discovery is lazy — nothing here resolves a repo root
# at import time.
from kb_tools import kb_links, kb_schema, kb_util

# The on-disk literal that marks a value as unassessed throughout the KB
# (quality / solidity / support on-point fraction). Used both as the authored
# token in frontmatter and as the materialized JSONL value for a pending
# support fraction. Defined in kb_schema, which is where the write API reads it
# from too — one object rather than two spellings of the same intent.
PENDING_LITERAL = kb_schema.PENDING_LITERAL


class _PendingFraction:
    """Singleton sentinel for an UNASSESSED support on-point fraction (S10).

    A ``supports:`` pair may carry the literal ``*pending*`` instead of a number,
    meaning the beneficiary is an intended target but the on-point fraction is
    not yet scored. This sentinel is DISTINCT from the ``None`` that a ``depends``
    edge uses for its (absent) fraction: ``None`` means "no fraction applies to
    this edge class", ``PENDING_FRACTION`` means "a fraction applies but is
    unassessed". The two are distinguishable in the data model AND on disk — a
    depends-edge serializes ``"fraction": null`` while a pending supports-edge
    serializes ``"fraction": "*pending*"``.

    A pending fraction contributes nothing to its beneficiary's ``local_quality``
    max (excluded, exactly like a pending ``sup_solidity``); it never poisons the
    beneficiary to pending.
    """

    _instance: "_PendingFraction | None" = None

    def __new__(cls) -> "_PendingFraction":
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def __repr__(self) -> str:
        return "PENDING_FRACTION"


PENDING_FRACTION = _PendingFraction()

# Directory / file names excluded from every KB walk. Single-sourced here;
# refresh_kb_metadata and verify_kb_metadata import these rather than keeping
# their own copies. `claim-quality-closure-roadmap.md` is one known consumer's
# planning-doc convention — harmless to KBs that don't use the name.
# The corpus-invariant source: the authored home of `### INVARIANT-*`
# headings and `- Axiom N:` bullets, and therefore of every framework
# node. `CLAUDE.md` is canned generic orientation and is not an invariant
# channel; parse_framework_nodes still falls back to it for KBs built
# before the split.
INVARIANTS_FILENAME = "invariants.md"
LEGACY_INVARIANTS_FILENAME = "CLAUDE.md"

# The external works' register: the KB root's own `claim-quality.md`, one file
# for the whole corpus. Its home is stated here rather than derived per volume
# because an external work's standing is a property of the work: a paper three
# volumes cite is one node, and a per-volume register would key three.
WORKS_REGISTER = "claim-quality.md"

# The KB's navigation convention, published beside the names above because this
# module is the lowest common import of everything that reads or writes it: a
# directory's node is its `index.md`, the tree's root is `entry-point.md`, and a
# non-root document's link back to its parent carries the up-arrow marker.
# Spelling these independently at their four consumers — the survey validator's
# design-time reachability, the driver's step and replay constants, and this
# package's verifier — lets a convention change miss one and leave design-time
# and post-build reachability disagreeing about the same tree.
ENTRY_POINT_FILENAME = "entry-point.md"
INDEX_FILENAME = "index.md"
UPLINK_MARKER = "↑"

EXCLUDE_DIRS = {"session", ".index", "tools"}
EXCLUDE_NAMES = {
    "claim-quality.md",
    "claim-quality-closure-roadmap.md",
    "CLAUDE.md",
    "CONVENTIONS.md",
    "CONVENTIONS.md",
    "README.md",
    INVARIANTS_FILENAME,
}

# Node-id patterns. The id grammar (the hash body and the
# clm/exp/sup kind tokens) is single-sourced in kb_schema.id_body; the wrappers
# below add each callsite's own anchors / capture / comment-marker context. The
# kind prefix makes every pattern exact — it cannot match incidental prose.
# Claim-ID: the `clm-` prefix plus the shared hash body.
_CLAIM_ID_RE = re.compile(rf"\b({kb_schema.id_body('clm')})\b")
# Either an exp- or clm- id — used to extract id-list frontmatter values that
# may hold either prefix (a `claims:` list holds clm- ids, an `experiments:`
# list holds exp- ids).
_ANY_ID_RE = re.compile(rf"\b({kb_schema.id_body('clm', 'exp')})\b")
# A canonical-id marker keys either a claim entry (`clm-`) or a support entry
# (`sup-`) in a claim-quality.md register; both share the same `### Quality`
# entry shape. The narrow pattern is for a consumer that wants claims alone, the
# wide one for a consumer that takes the register's two entry kinds together.
_CANONICAL_ID_RE = re.compile(rf"<!--\s*id:\s*({kb_schema.id_body('clm')})\s*-->")
_CANONICAL_ANY_ID_RE = re.compile(rf"<!--\s*id:\s*({kb_schema.id_body('clm', 'sup')})\s*-->")
# A canonical-id marker of ANY node kind. The two patterns above cover the kinds
# each consumer keys entries by; this one is the id-inventory scan's (widened to
# `exp-` as well, so an experiment entry a project chooses to register cannot be
# missed by the collision check that guards minting, and to `work-`, whose
# entries are register entries like any other even though nothing mints one).
_CANONICAL_NODE_ID_RE = re.compile(rf"<!--\s*id:\s*({kb_schema.id_body()}|{kb_schema.WORK_ID_RE})\s*-->")
# Experiment-ID pattern: `exp-` prefix plus the hash body.
_EXP_ID_RE = re.compile(rf"\b({kb_schema.id_body('exp')})\b")
# Support-ID pattern: `sup-` prefix plus the hash body.
_SUP_ID_RE = re.compile(rf"\b({kb_schema.id_body('sup')})\b")
# The optional bullet marker both pair patterns below lead with, and the reason
# it is spelled `(?:-\s*)?` rather than `-?\s*`.
#
# `^\s*-?\s*` is two whitespace runs separated by an optional atom: on a line of
# n leading spaces with no `-` in it, the first run can end at any of n
# positions and the second can then consume any of the rest, so the match fails
# n²/2 times before it fails once. Both patterns are run per line by
# `parse_support_leaf`, `parse_experiment_leaf` and
# `parse_register_staged_supports` — the last of which every census and every
# write-API `_prove` call reaches — so one long whitespace line in one KB file
# was a hang with no cycle in it: 40 000 spaces cost 5.3 s in a single
# `.match()`, and a `set-on-point-fraction` over an entry carrying that one line
# took 10.3 s and exited 0.
#
# Binding `\s*` inside the optional group makes it reachable only after a
# literal `-`, which leaves exactly one whitespace run per position and one
# backtrack per position: the same language, matched linearly. Equality of the
# two languages is not an eyeball claim — `test_kb_index_lib.py` puts a corpus
# of pair-line spellings through both spellings of the pattern, and a timing
# bound stands over the adversarial input.
_PAIR_BULLET = r"^\s*(?:-\s*)?"
# A `strengthens:` block pair line: `clm-<id>: <strength>` (strength a float
# in [0,1]). Indented under the `strengthens:` frontmatter key.
_STRENGTHENS_PAIR_RE = re.compile(_PAIR_BULLET + rf"({kb_schema.id_body('clm')})\s*:\s*(-?\d+(?:\.\d+)?)\s*$")
# A `supports:` block pair line: `clm-<id>: <fraction>`, where <fraction> is
# either an on-point fraction float in [0,1] OR the literal `*pending*` (the
# fraction is an intended-but-unassessed edge — see PENDING_FRACTION). Indented
# under the `supports:` frontmatter key. Group 2 captures the raw fraction
# token; the loop in parse_support_leaf converts and validates it.
_SUPPORTS_PAIR_RE = re.compile(_PAIR_BULLET + rf"({kb_schema.id_body('clm')})\s*:\s*(-?\d+(?:\.\d+)?|\*pending\*)\s*$")
# Leaf-frontmatter id DECLARATIONS — the keys that ORIGINATE a node body in the
# container that carries them. Each key may repeat (a container hosts any number
# of experiment / support bodies), so these are scanned over the whole
# frontmatter block rather than read out of the flat parse_frontmatter dict,
# whose later duplicate keys overwrite earlier ones.
_LEAF_DECL_RES = (
    re.compile(rf"^\s*-?\s*exp-id:\s*({kb_schema.id_body('exp')})\s*$", re.MULTILINE),
    re.compile(rf"^\s*-?\s*sup-id:\s*({kb_schema.id_body('sup')})\s*$", re.MULTILINE),
)
# Leaf-frontmatter id CITATIONS — the list-valued keys naming nodes the leaf
# hosts (`claims:`; for a clm- id the citing leaf IS the hosting leaf, per
# build_leaf_references) or merely references (`experiments:`).
_LEAF_CITE_KEYS = ("claims", "experiments")
# The kb-frontmatter block. The closing `-->` may carry leading whitespace:
# without that, an authored block indented under a list item fails the column-0
# close, so the non-greedy body runs on to the NEXT `-->` in the file —
# swallowing the document body into the frontmatter, and deleting it outright at
# the `.sub("")` site below. Public because `refresh_kb_metadata` and
# `verify_kb_metadata` bind it rather than re-compiling their own copies; three
# copies of a block delimiter is three chances for the emitter and the checker
# to disagree about where a document starts.
FRONTMATTER_RE = re.compile(r"<!--\s*kb-frontmatter\s*\n(.*?)\n[ \t]*-->", re.DOTALL)
# The opener alone. Used to find where a block COULD start without paying for
# the lazy body scan at each candidate — see :func:`find_frontmatter`, which is
# how every reader in this module reaches the pattern above.
_FRONTMATTER_OPEN_RE = re.compile(r"<!--\s*kb-frontmatter\s*\n")
_TIER2_INLINE_RE = re.compile(r"<!--\s*claim-quality:\s*(.*?)\s*-->", re.DOTALL)

# Framework-node parsing (from the KB's CLAUDE.md).
# Invariant headings: `### INVARIANT-XX: <title>`.
_INVARIANT_HEADING_RE = re.compile(r"^### (INVARIANT-[A-Z]+[0-9]+):\s*(.+)$")
# Axiom bullets in the INVARIANT-S2 section: `- Axiom N: **<title>** — ...`.
# N is any positive integer; how many axioms a KB declares is its own affair.
_AXIOM_BULLET_RE = re.compile(r"^- Axiom (\d+): \*\*(.+?)\*\*")
# In-bullet target tokens for depends-on head extraction.
_INVARIANT_TOKEN_RE = re.compile(r"\b(INVARIANT-[A-Z]+[0-9]+)\b")
_AXIOM_TOKEN_RE = re.compile(r"\bAxiom (\d+)\b")
# An external-work target. `\b` is no use on either side: a citation key may end
# in a digit and may carry `:` or `/`, so the boundaries are stated as "not
# preceded by a word character or a hyphen" and, on the right, by the key
# pattern's own alphanumeric close.
_WORK_TOKEN_RE = re.compile(rf"(?<![\w-])({kb_schema.WORK_ID_RE})")

# Quality-field parsing.
# `confidence: 0.X` and `solidity: 0.X (build-status phrase) [optional arithmetic]`
_NUMBER_RE = re.compile(r"-?\d+(?:\.\d+)?")
# Captures a parenthetical group that does not start with `=` (which marks the
# arithmetic annotation). Build-status is the first parenthetical after the
# numeric value.
_FIRST_PAREN_RE = re.compile(r"\(([^()]*)\)")
# A depends-on entry line: `- <id> — ... (solidity <num>) [optional context]`.
# The placeholder is detected separately and produces no edge.
_DEPENDS_ON_PLACEHOLDER_RE = re.compile(r"^\s*-\s*\*\(")
_DEPENDS_ON_BRACKET_RE = re.compile(r"\[([^\[\]]*)\]\s*$")
_DEPENDS_ON_PAREN_RE = re.compile(r"\(([^()]*)\)")
_SOLIDITY_IN_PAREN_RE = re.compile(r"solidity\s+(-?\d+(?:\.\d+)?)")
# A work-target depends-on bullet's applicability annotation — the on-point
# fraction of the pairing, in the place a claim-target bullet carries its
# derived `(solidity …)`. Both spellings are read: a number, or the pending
# literal, which is what a build writes and what nothing but a person replaces.
_APPLICABILITY_IN_PAREN_RE = re.compile(rf"applicability\s+(-?\d+(?:\.\d+)?|{re.escape(kb_schema.PENDING_LITERAL)})")

# Every `- <key>:` line a claim entry's `### Quality` section may carry. A set,
# not an order — the canonical field order is `kb_write.render`'s. Each folding
# field runs until the next line matching this list, so a key missing from it is
# a field the one above it silently swallows: the list is stated once, every
# field's own bound is derived from it, and `kb_write.store` builds its
# fold-break regex from it rather than retyping the alternation.
QUALITY_FIELD_KEYS: tuple[str, ...] = (
    "confidence",
    "solidity",
    "rationale",
    "depends-on",
    "references",
    "strengthen-by",
)


def _field_break(excluding: str) -> re.Pattern[str]:
    """The regex bounding one folding field: every quality key but its own."""
    return re.compile(r"^- (" + "|".join(key for key in QUALITY_FIELD_KEYS if key != excluding) + "):")


_BREAK_AFTER_RATIONALE = _field_break("rationale")
_BREAK_AFTER_DEPENDS_ON = _field_break("depends-on")
_BREAK_AFTER_REFERENCES = _field_break("references")
_BREAK_AFTER_STRENGTHEN_BY = _field_break("strengthen-by")


# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class DependsOnEdge:
    """A forward edge in the claim graph — a ``depends`` or ``strengthens`` edge.

    ``relation`` discriminates the edge class:

    * ``"depends"`` (gating, min-branch): ``source`` is a claim; ``target`` is
      a claim / invariant / axiom. ``strength`` is ``None``.
    * ``"strengthens"`` (max-branch): ``source`` is an experiment; ``target``
      is a claim; ``strength`` is the conferred experimental solidity in
      ``[0, 1]``; ``target_kind`` is ``"claim"`` and ``target_solidity_recorded``
      is ``None``.
    * ``"rests-on"`` (gating, min-branch): ``source`` is a claim; ``target`` is
      an external work; ``strength`` is ``None`` (a work's standing is a
      property of the node, carried on :class:`ExternalWork`) and ``fraction``
      is the pairing's applicability. It is the off-graph endcap — the claim
      rests on work this corpus does not contain — and it gates: a positive
      applicability puts the target work's own ``strength`` into the source's
      ``min``, a zero one takes the pairing out of it, and either value pending
      leaves the source's derivation pending
      (:func:`compute_solidity_full`).
    * ``"references"`` (non-gating, no branch): ``source`` and ``target`` are
      both claims, and the edge records that the source's own text names the
      target — a contrast, a pointer, a *distinct from Theorem 2*. ``strength``
      and ``fraction`` are ``None``, it enters no solidity computation, and it
      is under no acyclicity constraint: two claims naming each other is the
      author's argument, not a dependency cycle. It is carried on
      :attr:`ClaimEntry.references` rather than on ``depends_on`` so that
      :func:`compute_solidity_full`, which reads only the latter, cannot see
      one.

    ``target_kind`` discriminates the target node type: ``"claim"`` for an
    edge to another claim, ``"invariant"`` / ``"axiom"`` for an edge to a
    framework node, ``"work"`` for an external work. For those three
    ``target_solidity_recorded`` is always ``None`` (neither a framework node
    nor an external work carries a solidity).
    """

    source: str
    target: str
    relation: str  # "depends" | "strengthens" | "supports" | "rests-on" | "references"
    target_kind: str
    target_solidity_recorded: float | None
    strength: float | None
    context: str | None
    # on-point fraction for a "supports" edge: a float f ∈ [0,1], or
    # PENDING_FRACTION when the fraction is authored but unassessed. None for
    # non-supports edge classes (a depends edge carries no fraction at all).
    fraction: float | _PendingFraction | None = None


@dataclass(frozen=True)
class ExperimentNode:
    """A physical experiment — a first-class, terminal graph node.

    Experiments are strength-sources: they have NO ``depends`` edges and never
    gate; they only emit ``strengthens`` edges to the claims their result bears
    on. ``status`` is ``"run"`` (its strengthens edges count toward
    experimental solidity) or ``"pending"`` (unrun — its edges contribute
    nothing). ``strengthens`` is the tuple of ``(claim_id, strength)`` pairs
    parsed from the leaf's ``strengthens:`` frontmatter block.
    """

    id: str
    title: str
    canonical_path: str
    canonical_anchor: str
    status: str  # "run" | "pending"
    strengthens: tuple[tuple[str, float], ...]


@dataclass(frozen=True)
class SupportNode:
    """A non-physical analytical support node.

    A ``sup-`` is claim-like inside (carries a local-rigor ``quality`` and may
    consume its own ``depends_on`` claims), experiment-like in fan-out (one
    support may help many claims), and contributes to the DERIVATION branch of
    each beneficiary (never the experimental/max branch).

    Its own solidity is computed exactly like a claim:
    ``round2(min(quality, *dependency final solidities))``; framework deps
    contribute 1.0; pending propagates (pending ``quality`` OR a pending dep =>
    pending ``sup_solidity``). A free-standing support (no deps) has
    ``sup_solidity == quality``.

    ``supports`` is the tuple of ``(claim_id, fraction)`` beneficiary pairs
    parsed from the hosting leaf's ``supports:`` frontmatter block; each
    ``fraction`` is the on-point fraction f ∈ [0, 1] for that claim, or
    ``PENDING_FRACTION`` when authored as ``*pending*`` (an intended-but-
    unassessed edge — contributes nothing to the beneficiary's local_quality and
    never poisons it). ``quality`` / ``depends_on`` / ``rationale`` come from the
    support's claim-quality entry (keyed by ``<!-- id: sup-xxxxxx -->``),
    parallel to a claim entry.
    """

    id: str
    title: str
    canonical_path: str
    canonical_anchor: str
    quality: float | None
    depends_on: tuple[DependsOnEdge, ...]
    supports: tuple[tuple[str, float | _PendingFraction], ...]
    rationale: str = ""
    # The on-disk ``- solidity:`` value parsed from the support's claim-quality
    # entry (NOT recomputed). Used only by the freshness verifier to detect a
    # stale write-back; the authoritative value is the computed sup_solidity.
    solidity: float | None = None
    # The on-disk arithmetic trace (``[= min(...)]``), verbatim. Derived like
    # the value above, and gated the same way.
    solidity_trace: str = ""


@dataclass(frozen=True)
class FrameworkNode:
    """A structural invariant or axiom — a first-class framework graph node.

    Framework nodes are parsed from the KB's ``CLAUDE.md``. They are
    solidity-1.0 by definition (framework bedrock) — a documented rule, not a
    stored field. The record carries only the five identifying fields.
    """

    node_type: str  # "invariant" | "axiom"
    id: str
    title: str
    canonical_path: str
    canonical_anchor: str


@dataclass(frozen=True)
class ExternalWork:
    """A work the corpus cites and does not contain — the off-graph endcap.

    **Terminal, and one node per work.** It emits no edge of its own: whatever
    the cited paper rests on is outside this corpus too, and inventing a cone
    below it would be inventing the corpus. It is the target of ``rests-on``
    edges from every claim that cites it, however many volumes those claims are
    spread across, because a work's standing is a property of the work.

    ``strength`` is that standing — hand-authored, ``None`` for the
    ``*pending*`` literal a build writes and nothing computes. It is not a
    solidity and sits on no build band, but it does enter one: it is the gate
    term every claim whose pairing with this work is scored non-zero takes its
    dep-gate ``min`` over, and while it is ``None`` every such claim's
    derivation is pending (:func:`compute_solidity_full`).

    ``key`` is the citation key, which is the whole of the node's identity;
    ``title`` is the work's rendered reference-list text where a bibliography
    answered the key, and the key itself where none did.
    """

    id: str
    key: str
    title: str
    canonical_path: str
    canonical_anchor: str
    strength: float | None = None
    rationale: str = ""


@dataclass(frozen=True)
class StrengthenByItem:
    """A single strengthen-by bullet from a claim's Quality section."""

    claim_id: str
    item_idx: int
    text: str
    mentioned_ids: tuple[str, ...]


@dataclass(frozen=True)
class ClaimEntry:
    """A canonical claim-quality entry, parsed from a claim-quality.md file."""

    id: str
    title: str
    canonical_path: str
    canonical_anchor: str
    confidence: float | None
    solidity: float | None
    build_status: str | None
    rationale: str
    depends_on: tuple[DependsOnEdge, ...]
    strengthen_by: tuple[StrengthenByItem, ...]
    # The ``- references:`` bullets: claim-to-claim cross-references the corpus
    # states and nothing gates on. They are a field of their own rather than
    # `relation == "references"` members of `depends_on` because every solidity
    # consumer reads that tuple — keeping them out of it is what makes "a
    # reference gates nothing" structural rather than a branch each consumer
    # remembers to write.
    references: tuple[DependsOnEdge, ...] = ()
    # The on-disk arithmetic trace (``[= min(...)]`` / ``[= max(...)]``),
    # verbatim and including its leading space. A derived field like the value
    # and the phrase beside it, and gated with them.
    solidity_trace: str = ""


@dataclass(frozen=True)
class LeafRecord:
    """A leaf file's parsed metadata.

    ``experiments_ref`` holds the exp-ids a leaf REFERENCES via its optional
    ``experiments:`` frontmatter field (the exact analog of ``claims:`` for
    claims — a leaf-level citation, the inverse of an experiment's
    Leaf-references). It is additive: a referencing leaf still declares
    ``claims:`` or ``no-claim:`` as its primary field. References do NOT roll
    up into ``subtree-experiments`` (that aggregate is owned-only — see
    :func:`build_subtree_aggregate_records`).
    """

    path: str
    kind: str
    claims: tuple[str, ...]
    tier2_marked: frozenset[str]
    no_claim_reason: str | None
    experiments_ref: tuple[str, ...] = ()


@dataclass(frozen=True)
class IndexRecord:
    """An index or entry-point file's parsed metadata.

    ``declared_subtree_experiments`` is the derived ``subtree-experiments:``
    field — the union of exp-ids OWNED (declared via ``exp-id:``) by
    experiment leaves under this node's directory. Owned-only, parallel to
    how ``declared_subtree_claims`` aggregates owned leaf claims.
    """

    path: str
    kind: str
    declared_subtree_claims: tuple[str, ...]
    declared_subtree_experiments: tuple[str, ...] = ()


@dataclass(frozen=True)
class KbState:
    """The full discovered state of the KB after a one-shot load."""

    claim_entries: tuple[ClaimEntry, ...]
    leaves: tuple[LeafRecord, ...]
    indexes: tuple[IndexRecord, ...]
    framework_nodes: tuple[FrameworkNode, ...]
    experiments: tuple[ExperimentNode, ...]
    supports: tuple[SupportNode, ...] = ()
    works: tuple[ExternalWork, ...] = ()


# ---------------------------------------------------------------------------
# Frontmatter parsing
# ---------------------------------------------------------------------------


# A YAML-block list item inside a frontmatter body: `  - clm-aaaaaa`.
_FM_BULLET_RE = re.compile(r"^\s*-\s+(.*)$")


def _frontmatter_value(value: str):
    """Type one already-joined frontmatter value."""
    if value.startswith("[") and value.endswith("]"):
        # Id-list values hold clm- ids (e.g. claims:, subtree-claims:) or
        # exp- ids (experiments:, subtree-experiments:); accept both.
        return _ANY_ID_RE.findall(value)
    if value.startswith('"') and value.endswith('"'):
        return value[1:-1]
    if value in ("true", "false"):
        return value == "true"
    return value


def _frontmatter_fields(body: str) -> dict:
    """Parse a kb-frontmatter block body into typed fields.

    THE frontmatter reader for the toolchain: ``parse_frontmatter`` here, the
    hosting-leaf citation scan, ``refresh_kb_metadata`` and
    ``verify_kb_metadata`` all reach this function, so a field means the same
    thing to each of them. Four copies of a field parser is four chances for
    the emitter and the checker to disagree about what a leaf declares.

    A field's value may span more than one physical line in two authored
    shapes, both of which a single-line parser mangles silently:

    * **wrapped** — ``claims: [clm-aaaaaa,`` / ``          clm-bbbbbb]``. The
      opening line does not end in ``]``, so the value falls through to the
      string branch, and every consumer that iterates it (``tuple(fm["claims"])``
      at the leaf-index builder, the subtree union, the orphan-ref check) gets
      one "claim id" per CHARACTER.
    * **YAML-block** — ``claims:`` / ``  - clm-aaaaaa`` / ``  - clm-bbbbbb``.
      The value is empty and the bullet lines carry no colon, so they are
      skipped entirely and the field reads as the empty list — a leaf's whole
      claim set dropped with no diagnostic anywhere.

    Continuation lines are joined into the logical value before it is typed, so
    all three shapes yield the same ``list[str]``.
    """
    fields: dict = {}
    lines = body.splitlines()
    i = 0
    while i < len(lines):
        line = lines[i].rstrip()
        if not line or ":" not in line:
            i += 1
            continue
        end = frontmatter_field_end(lines, i)
        key, _, value = line.partition(":")
        fields[key.strip()] = _frontmatter_value(_join_frontmatter_value(lines, i, end))
        i = end
    return fields


def _join_frontmatter_value(lines: list[str], start: int, end: int) -> str:
    """Join the physical lines ``[start, end)`` into one logical field value."""
    _, _, value = lines[start].rstrip().partition(":")
    value = value.strip()
    tail = lines[start + 1 : end]
    if not tail:
        return value
    if value.startswith("["):
        return " ".join([value, *(line.strip() for line in tail)])
    # YAML-block list: the bullets ARE the value.
    items = [_FM_BULLET_RE.match(line).group(1).strip() for line in tail]  # type: ignore[union-attr]
    return "[" + ", ".join(items) + "]"


def frontmatter_field_end(lines: list[str], start: int) -> int:
    """Index one past the last physical line of the field beginning at ``start``.

    ONE rule for how many physical lines a logical frontmatter field occupies,
    shared by the reader (:func:`_frontmatter_fields`) and the writer
    (``refresh_kb_metadata._replace_or_insert_field``). A value the reader joins
    is therefore exactly the span the writer replaces: a wrapped list rewritten
    by its first line alone would strand its tail as orphaned text for the next
    parse to fold into the following field.
    """
    _, _, value = lines[start].rstrip().partition(":")
    value = value.strip()
    i = start + 1
    if value.startswith("[") and not value.endswith("]"):
        # Wrapped list: runs to the line carrying the closing bracket. An
        # unterminated list runs out at the end of the block and stays a
        # string, exactly as any malformed value does.
        while i < len(lines) and not lines[i].strip().endswith("]"):
            i += 1
        return min(i + 1, len(lines))
    if not value:
        # YAML-block list: runs to the last `- item` bullet.
        while i < len(lines) and _FM_BULLET_RE.match(lines[i]):
            i += 1
    return i


def find_frontmatter(text: str, pos: int = 0) -> "re.Match[str] | None":
    """The first ``kb-frontmatter`` block at or after ``pos``, in one pass.

    Returns exactly what ``FRONTMATTER_RE.search(text, pos)`` returns — and
    produces it with that very pattern, so there is one grammar and one compiled
    object, not a second reader of the block delimiter. What differs is the cost
    of the case where the answer is "none".

    ``FRONTMATTER_RE``'s body is a lazy ``(.*?)`` under ``DOTALL``, so at every
    candidate opener the engine scans forward to the end of the document looking
    for a closer. A document carrying many openers and no ``-->`` at line start
    therefore costs O(openers x length): measured at 100 openers 17 ms, 400
    openers 281 ms, 1 600 openers **4 493 ms**. Only a
    truncated or hostile document has that shape — one *about* the format shows
    its closer — but ``parse_frontmatter`` runs over every file in the KB, so
    the shape only has to arrive once.

    The one-pass form rests on a property of the pattern rather than on a
    rewrite of it: **if the FIRST opener has no closer after it, no later opener
    has one either.** Closer positions only increase, and the lazy body spans
    anything, so any closer that a later opener could reach is also reachable
    from the first. The leftmost match, when one exists, therefore begins at the
    first opener — one opener search and one anchored match attempt, each linear
    in the document.
    """
    opener = _FRONTMATTER_OPEN_RE.search(text, pos)
    if opener is None:
        return None
    return FRONTMATTER_RE.match(text, opener.start())


def strip_frontmatter(text: str) -> str:
    """``text`` with every ``kb-frontmatter`` block removed.

    What ``FRONTMATTER_RE.sub("", text)`` returns, found through
    :func:`find_frontmatter` so a hostile document costs one pass rather than
    one per opener. Each round advances past the block it removed, so the whole
    walk is linear in the document.
    """
    out: list[str] = []
    pos = 0
    while (match := find_frontmatter(text, pos)) is not None:
        out.append(text[pos : match.start()])
        pos = match.end()
    out.append(text[pos:])
    return "".join(out)


def parse_frontmatter(text: str) -> dict | None:
    """Return parsed kb-frontmatter fields, or None if no block found.

    ID-lists return as ``list[str]`` — in any of the three authored shapes,
    see :func:`_frontmatter_fields` — quoted strings are unquoted, booleans
    become Python bool, everything else stays a string.
    """
    m = find_frontmatter(text)
    if not m:
        return None
    return _frontmatter_fields(m.group(1))


class FrameworkNodeParseError(ValueError):
    """Edges reference framework nodes (axiom-N / INVARIANT-*) that did not
    parse out of the KB's ``CLAUDE.md``.

    Raised by :func:`build_all_records` when the assembled depends-on edges
    target framework nodes that are absent from the rebuilt ``claims.jsonl``
    node set. The usual cause is a transient ``CLAUDE.md`` state where the
    INVARIANT-S2 axiom bullets or ``### INVARIANT-*`` headings don't match the
    parser (e.g. indented, reflowed, or carrying merge-conflict markers mid
    hand-merge): :func:`parse_framework_nodes` then silently yields fewer
    framework nodes, and a naive write would emit an index whose edges dangle.
    Failing loudly here prevents the silent drop (the downstream symptom is a
    flood of cryptic referential-integrity orphans in the verify target).
    """


# ---------------------------------------------------------------------------
# Framework-node parsing (CLAUDE.md)
# ---------------------------------------------------------------------------


def framework_source(kb_root: Path) -> Path | None:
    """The file framework nodes are parsed from, or None when there is none.

    ``invariants.md`` is the authored home of corpus invariants. ``CLAUDE.md``
    is the legacy home, kept as a fallback so a KB built before the split keeps
    minting its framework nodes; :func:`framework_source_is_legacy` is what
    lets a report say so out loud rather than migrating silently.
    """
    for name in (INVARIANTS_FILENAME, LEGACY_INVARIANTS_FILENAME):
        candidate = kb_root / name
        if candidate.is_file():
            return candidate
    return None


def framework_source_is_legacy(kb_root: Path) -> bool:
    """True when framework nodes still come from the deprecated CLAUDE.md."""
    source = framework_source(kb_root)
    return source is not None and source.name == LEGACY_INVARIANTS_FILENAME


def parse_framework_nodes(kb_root: Path | None = None) -> list[FrameworkNode]:
    """Parse invariant and axiom nodes from the KB's ``CLAUDE.md``.

    Invariants come from ``### INVARIANT-XX: <title>`` headings; each node's
    ``canonical_anchor`` is the GitHub-style slug of its own heading.

    Axioms come from the ``- Axiom N: **<title>** — ...`` bullets (any N) in
    the INVARIANT-S2 section; every axiom points at the INVARIANT-S2 heading's
    slug (the KB's axiom-numbering authority, empty when the KB declares no
    INVARIANT-S2 heading). Node ids are ``axiom-<N>``.

    The source is ``invariants.md`` when present, else ``CLAUDE.md`` — see
    :func:`framework_source`. Framework nodes are optional: no source file, or
    a source carrying no invariant headings and no axiom bullets, yields an
    empty list — never an error. ``canonical_path`` is the source file's name,
    so a node's citation target names whichever source it came from.

    ``kb_root`` defaults to lazy discovery via ``kb_util.kb_root()``.
    """
    if kb_root is None:
        kb_root = kb_util.kb_root()
    source = framework_source(kb_root)
    if source is None:
        return []
    canonical_path = source.name
    lines = source.read_text(encoding="utf-8").splitlines()

    nodes: list[FrameworkNode] = []
    s2_anchor: str | None = None
    for line in lines:
        m = _INVARIANT_HEADING_RE.match(line)
        if m:
            label, title = m.group(1), m.group(2).strip()
            anchor = _slugify_heading(line[4:].strip())
            nodes.append(
                FrameworkNode(
                    node_type="invariant",
                    id=label,
                    title=title,
                    canonical_path=canonical_path,
                    canonical_anchor=anchor,
                )
            )
            if label == "INVARIANT-S2":
                s2_anchor = anchor

    for line in lines:
        m = _AXIOM_BULLET_RE.match(line)
        if m:
            num, title = m.group(1), m.group(2).strip()
            nodes.append(
                FrameworkNode(
                    node_type="axiom",
                    id=f"axiom-{num}",
                    title=title,
                    canonical_path=canonical_path,
                    canonical_anchor=s2_anchor or "",
                )
            )
    return nodes


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _strip_code_fences(text: str) -> str:
    """Blank out lines inside fenced code blocks.

    Used to scrub claim-quality.md content before regex extraction so the
    example snippet in the Quality Convention preamble does not contribute
    false ID matches. Delegates to :func:`kb_links.blank_fenced_lines` — the
    toolchain's one fence scanner — so a ``~~~`` example is scrubbed like a
    backtick one, and an indented closing fence cannot desync the register
    parse into returning no entries at all.
    """
    return "\n".join(kb_links.blank_fenced_lines(text))


def _slugify_heading(text: str) -> str:
    """GitHub-style heading anchor.

    Lowercase, drop characters outside [a-z0-9-_], then replace whitespace
    with '-' ONE CHARACTER AT A TIME. The per-character substitution is the
    whole subtlety: GitHub deletes the punctuation and hyphenates each
    surviving space independently, so ``Solidity — the dep gate`` anchors at
    ``solidity--the-dep-gate`` — the em-dash leaves a space on either side and
    both become hyphens. Collapsing the run with ``\\s+`` produces
    ``solidity-the-dep-gate``: a single hyphen, an anchor GitHub never emits,
    and a heading link that resolves in no renderer. Any punctuation
    between two spaces has this shape — an em-dash, a colon, a slash.
    """
    s = text.strip().lower()
    s = re.sub(r"[^\w\s-]", "", s, flags=re.UNICODE)
    s = re.sub(r"\s", "-", s)
    return s.strip("-")


_ANY_HEADING_RE = re.compile(r"^(#{1,6})\s")


def anchor_section(text: str, anchor: str) -> str | None:
    """The code-stripped body of the section whose heading slugifies to ``anchor``.

    The section runs from its heading to the next heading at the same depth or
    shallower, and ``None`` means no heading in the document anchors there.

    **Code is stripped before the scan, and that is the load-bearing half.** A
    ``## `` heading inside a fenced example is documentation *about* a section,
    not a section, and a clause inside a fenced example is documentation about a
    rule rather than the rule — so a citation resting on either would be resting
    on nothing. Stripping here rather than at
    each caller is what keeps the citation gate and the citation *composer*
    reading one document: the tool prints only what the gate will accept,
    because both ask this function.
    """
    lines = kb_links.strip_code(text).split("\n")
    for i, line in enumerate(lines):
        heading = _ANY_HEADING_RE.match(line)
        if heading is None:
            continue
        title = line[len(heading.group(1)) :].strip()
        if _slugify_heading(title) != anchor:
            continue
        depth = len(heading.group(1))
        body = []
        for following in lines[i + 1 :]:
            nxt = _ANY_HEADING_RE.match(following)
            if nxt is not None and len(nxt.group(1)) <= depth:
                break
            body.append(following)
        return "\n".join(body)
    return None


def _posix_relative(path: Path, kb_root: Path) -> str:
    """Return POSIX-style path relative to kb_root."""
    return path.relative_to(kb_root).as_posix()


def kb_files(kb_root: Path):
    """Iterate non-excluded .md files under kb_root — **the** KB document walk.

    Public because it is a contract rather than a detail: the verifiers, the
    claim-graph builder and the build pipeline's coverage checks must all mean
    the same thing by "a document of this KB", and a second implementation of
    the exclusion rules is how one of them starts checking a set another does
    not.
    """
    for p in sorted(kb_root.rglob("*.md")):
        if any(part in EXCLUDE_DIRS for part in p.relative_to(kb_root).parts[:-1]):
            continue
        if p.name in EXCLUDE_NAMES:
            continue
        yield p


def document_texts(kb_root: Path) -> dict[str, str]:
    """Every document of the tree by kb-root-relative POSIX path, with its text.

    The reading a caller wants when it has a question about the whole tree and
    no need for the link relations :mod:`kb_claimgraph.tree` derives. It lives
    here rather than there because the build pipeline asks it too, and
    ``kb_pipeline`` must not import ``kb_claimgraph``: that package reads the
    stage table, so an import the other way would close a cycle.
    """
    return {_posix_relative(path, kb_root): path.read_text(encoding="utf-8") for path in kb_files(kb_root)}


#: What a document carries while no claim has been looked for in its prose —
#: the declared pass's own statement of its scope, not a finding about the
#: document. **Compared by identity** wherever a determination is read
#: (``kb_claimgraph.conform.determination``), and the admission ticket claim
#: discovery reads a document in on.
#:
#: It lives here, beside the frontmatter parser, rather than in the module that
#: writes it: the build pipeline's coverage check for claim discovery reads it
#: too, and ``kb_pipeline`` may not import ``kb_claimgraph``.
UNSCANNED_REASON = (
    "No author-marked claim block; this build identified block-hosted claims only, so no claim has been "
    "looked for in this document's prose."
)


_SOLIDITY_TRACE_RE = re.compile(r"\[[^\]]*\]\s*$")


def _parse_solidity_line(line: str) -> tuple[float | None, str | None, str]:
    """Parse `- solidity: 0.X (build-status phrase) [optional arithmetic]`.

    Returns ``(solidity, build_status, trace)``. The first parenthetical is the
    build-status phrase; the trailing ``[...]`` is the arithmetic trace,
    returned verbatim (with its leading space) or ``""`` when absent.

    The trace is returned rather than discarded because a field this parser
    drops is a field nothing can check: ``check_solidity_fresh`` cannot compare
    what it never reads, and a trace contradicting its own value — or
    hand-mangled outright — would pass the gate forever.
    """
    value = line.split(":", 1)[1].strip() if ":" in line else line.strip()
    num_match = _NUMBER_RE.search(value)
    solidity = float(num_match.group(0)) if num_match else None
    paren = _FIRST_PAREN_RE.search(value)
    status = paren.group(1).strip() if paren else None
    trace_match = _SOLIDITY_TRACE_RE.search(value)
    trace = f" {trace_match.group(0).strip()}" if trace_match else ""
    return solidity, status, trace


def format_solidity(value: float | None) -> str:
    """Format a solidity / confidence scalar as the KB's 2-dp decimal string.

    THE formatter for these values: every register line is written with two
    decimal places (``0.90``, ``0.20``), so a report that renders the on-disk
    value with bare ``str()`` prints ``solidity 0.2, expected 0.20`` and reads
    as a mismatch between two identical numbers.
    """
    if value is None:
        return PENDING_LITERAL
    return f"{value:.2f}"


def _parse_confidence_line(line: str) -> float | None:
    """Parse `- confidence: 0.X`."""
    value = line.split(":", 1)[1].strip() if ":" in line else ""
    num_match = _NUMBER_RE.search(value)
    return float(num_match.group(0)) if num_match else None


def _parse_scalar_number_line(line: str) -> float | None:
    """Parse a `- key: 0.X` line into its leading float (or None if absent).

    Used for a support entry's ``- quality:`` line — the support analog of a
    claim's ``- confidence:`` line, scored by the same rubric. ``*pending*``
    (no numeric token) yields ``None``.
    """
    value = line.split(":", 1)[1].strip() if ":" in line else ""
    num_match = _NUMBER_RE.search(value)
    return float(num_match.group(0)) if num_match else None


def _normalize_text(s: str) -> str:
    """Collapse internal whitespace runs and line breaks to single spaces."""
    return re.sub(r"\s+", " ", s).strip()


def _depends_on_bullet_head(stripped: str) -> str:
    """Extract the head of a depends-on bullet.

    The head is the bullet text (already stripped of the leading ``- ``)
    truncated at the EARLIER of the first ` — ` (em-dash title separator) or
    the first ` (` (paren). The dependency target token(s) live in the head;
    the title/context after the separator is not scanned for targets.
    """
    cut = len(stripped)
    dash = stripped.find(" — ")
    if dash != -1:
        cut = min(cut, dash)
    paren = stripped.find(" (")
    if paren != -1:
        cut = min(cut, paren)
    return stripped[:cut]


def _known_claim_targets(
    head: str,
    source_id: str,
    *,
    bullet: str,
    known_ids: set[str] | None,
    diagnostic_stream: TextIO | None,
    canonical_path: str | None,
) -> list[str]:
    """Every registered ``clm-`` token in a bullet head, in the order written.

    The one place a ``clm-``-shaped token that is not a registered id is
    dropped, so the depends-on and references bullets cannot disagree about what
    a typo or a stale reference costs.
    """
    kept: list[str] = []
    for cid in _CLAIM_ID_RE.findall(head):
        if known_ids is not None and cid not in known_ids:
            if diagnostic_stream is not None:
                location = f"{canonical_path}:{source_id}" if canonical_path else source_id
                diagnostic_stream.write(
                    f"[kb_index_lib] dropped non-claim depends-on target in "
                    f'{location}: "{cid}" (bullet: "{_normalize_text(bullet)}")\n'
                )
            continue
        kept.append(cid)
    return kept


def _parse_references_line(
    line: str,
    source_id: str,
    known_ids: set[str] | None = None,
    diagnostic_stream: TextIO | None = None,
    canonical_path: str | None = None,
) -> list[DependsOnEdge]:
    """Parse a ``- references:`` sub-bullet into zero or more ``references`` edges.

    The head is cut the way a depends-on bullet's is and scanned for ``clm-``
    tokens alone: a reference is one claim of this corpus naming another, so a
    framework token or a ``work-`` id in this list names nothing this class can
    reach and contributes no edge. ``context`` comes from a trailing ``[...]``,
    as on a claim depends-on bullet; the bullet carries no annotation in its
    parens, nothing being derived for a relation that gates nothing.
    """
    if _DEPENDS_ON_PLACEHOLDER_RE.match(line):
        return []
    stripped = re.sub(r"^\s*-\s*", "", line).strip()
    bracket_match = _DEPENDS_ON_BRACKET_RE.search(stripped)
    context: str | None = None
    if bracket_match:
        raw = bracket_match.group(1).strip()
        if not raw.startswith("="):
            context = raw
    return [
        DependsOnEdge(
            source=source_id,
            target=cid,
            relation="references",
            target_kind="claim",
            target_solidity_recorded=None,
            strength=None,
            context=context,
        )
        for cid in _known_claim_targets(
            _depends_on_bullet_head(stripped),
            source_id,
            bullet=stripped,
            known_ids=known_ids,
            diagnostic_stream=diagnostic_stream,
            canonical_path=canonical_path,
        )
    ]


def _parse_depends_on_line(
    line: str,
    source_id: str,
    known_ids: set[str] | None = None,
    diagnostic_stream: TextIO | None = None,
    canonical_path: str | None = None,
) -> list[DependsOnEdge]:
    """Parse a depends-on bullet into zero or more edges (head-extraction).

    A bullet's dependency target(s) live in its *head* — the text before the
    first ` — ` or ` (`. The head is scanned for every recognized target
    token; one edge is emitted per token:

    * ``clm-xxxxxx`` -> a ``claim`` edge; ``target_solidity_recorded`` parsed
      from a ``(solidity <num>)`` group; ``context`` from a trailing ``[...]``.
    * ``INVARIANT-XX`` -> an ``invariant`` edge; ``target_solidity_recorded``
      is ``None``; ``context`` from the bullet's first ``(...)`` paren content.
    * ``Axiom N`` -> an ``axiom`` edge with ``target`` normalized to
      ``axiom-N``; ``target_solidity_recorded`` is ``None``; ``context`` from
      the first ``(...)`` paren content.
    * ``work-<citation key>`` -> a ``rests-on`` edge to an external work;
      ``fraction`` from an ``(applicability <f>)`` group, which is the pairing's
      own applicability and the reason this bullet's paren is NOT read as
      context the way a framework bullet's is; ``context`` from a trailing
      ``[...]``, as on a claim bullet.

    Placeholder bullets (``- *(none entry-local — ...)*``) and bullets whose
    head contains no recognized token produce zero edges.

    When ``known_ids`` is provided, a ``clm-``-shaped target outside that set
    is dropped (with a diagnostic on ``diagnostic_stream`` if non-None) —
    catching a typo or stale reference. ``known_ids`` does not gate framework
    targets; their resolution is checked by the verifier's referential
    integrity check.
    """
    if _DEPENDS_ON_PLACEHOLDER_RE.match(line):
        return []
    stripped = re.sub(r"^\s*-\s*", "", line).strip()
    head = _depends_on_bullet_head(stripped)

    # Context shared by framework edges: the first `(...)` paren content.
    paren_match = _DEPENDS_ON_PAREN_RE.search(stripped)
    paren_context = paren_match.group(1).strip() if paren_match else None

    # Context for claim edges: a trailing `[...]` group (skip `[= ...]`
    # arithmetic annotations).
    bracket_match = _DEPENDS_ON_BRACKET_RE.search(stripped)
    bracket_context: str | None = None
    if bracket_match:
        raw = bracket_match.group(1).strip()
        if not raw.startswith("="):
            bracket_context = raw

    sol_match = _SOLIDITY_IN_PAREN_RE.search(stripped)
    target_sol = float(sol_match.group(1)) if sol_match else None

    edges: list[DependsOnEdge] = []
    for cid in _known_claim_targets(
        head,
        source_id,
        bullet=stripped,
        known_ids=known_ids,
        diagnostic_stream=diagnostic_stream,
        canonical_path=canonical_path,
    ):
        edges.append(
            DependsOnEdge(
                source=source_id,
                target=cid,
                relation="depends",
                target_kind="claim",
                target_solidity_recorded=target_sol,
                strength=None,
                context=bracket_context,
            )
        )
    for label in _INVARIANT_TOKEN_RE.findall(head):
        edges.append(
            DependsOnEdge(
                source=source_id,
                target=label,
                relation="depends",
                target_kind="invariant",
                target_solidity_recorded=None,
                strength=None,
                context=paren_context,
            )
        )
    for num in _AXIOM_TOKEN_RE.findall(head):
        edges.append(
            DependsOnEdge(
                source=source_id,
                target=f"axiom-{num}",
                relation="depends",
                target_kind="axiom",
                target_solidity_recorded=None,
                strength=None,
                context=paren_context,
            )
        )
    applicability = _APPLICABILITY_IN_PAREN_RE.search(stripped)
    for work in _WORK_TOKEN_RE.findall(head):
        edges.append(
            DependsOnEdge(
                source=source_id,
                target=work,
                relation="rests-on",
                target_kind="work",
                target_solidity_recorded=None,
                strength=None,
                context=bracket_context,
                fraction=(
                    None
                    if applicability is None
                    else (
                        PENDING_FRACTION if applicability.group(1) == PENDING_LITERAL else float(applicability.group(1))
                    )
                ),
            )
        )
    return edges


def _parse_strengthen_by_lines(
    lines: list[str],
    source_id: str,
    known_ids: set[str] | None = None,
    diagnostic_stream: TextIO | None = None,
) -> tuple[StrengthenByItem, ...]:
    """Each top-level `- ` bullet becomes one item; continuation lines fold in.

    When ``known_ids`` is provided, mentioned IDs are filtered against that
    set; dropped candidates produce a diagnostic line on ``diagnostic_stream``
    if non-None.
    """
    items: list[tuple[list[str]]] = []
    current: list[str] | None = None
    for line in lines:
        # Top-level bullet detection: exactly two leading spaces is the typical
        # convention for the strengthen-by sub-bullets (under `- strengthen-by:`).
        # We accept any indentation depth that begins with `-` after at least
        # two leading spaces, treating deeper indents as continuations.
        m = re.match(r"^(\s+)-\s+(.*)$", line)
        if m and len(m.group(1)) <= 4:
            if current is not None:
                items.append((current,))
            current = [m.group(2)]
        else:
            if current is not None:
                current.append(line.strip())
    if current is not None:
        items.append((current,))

    out: list[StrengthenByItem] = []
    for idx, (chunks,) in enumerate(items):
        text = _normalize_text(" ".join(chunks))
        if not text:
            continue
        # Reject placeholders mirrored from depends-on: "*(none entry-local — ...)*"
        # is itself a strengthen-by item in some entries (legitimately - it
        # documents "no entry-local work would help"), so we keep it; but
        # mentioned_ids will simply be empty for it.
        candidates = sorted(set(_CLAIM_ID_RE.findall(text)))
        if known_ids is None:
            mentioned = candidates
        else:
            mentioned = []
            for cand in candidates:
                if cand in known_ids:
                    mentioned.append(cand)
                elif diagnostic_stream is not None:
                    diagnostic_stream.write(
                        f"[kb_index_lib] dropped non-claim mention in "
                        f'strengthen-by for {source_id} item #{idx}: "{cand}"\n'
                    )
        out.append(
            StrengthenByItem(
                claim_id=source_id,
                item_idx=idx,
                text=text,
                mentioned_ids=tuple(mentioned),
            )
        )
    return tuple(out)


# ---------------------------------------------------------------------------
# Claim-quality file parsing
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class RegisterEntry:
    """One canonical ``<!-- id: -->`` marker and the heading it binds to.

    Line indices are into the register's fence-scrubbed ``splitlines()``.
    Scrubbing blanks lines without removing them, so an index computed on the
    scrubbed lines addresses the same physical line in the raw ones.

    Two independent defects live in these fields, and the second is the
    harder-to-see one:

    * ``heading_line is None`` — the marker has NO preceding ``## `` heading, so
      it produces no record at all. Its id is registered and nothing reads it.
      The census is what reports this one, because it is the only thing that
      makes markers and records differ.
    * ``quality_line is None`` on a marker that IS bound — the marker reached a
      sibling entry's title before its own ``### Quality``, which is what a
      marker written ABOVE its heading looks like from the reader's side. The
      record exists, so the counts still balance, and every title from there on
      is off by one entry.
    """

    node_id: str
    kind: str  # "clm" | "exp" | "sup" | "work" — the id's prefix token
    marker_line: int
    #: The ``## `` heading the READER binds this marker to, or ``None`` when no
    #: heading precedes it.
    heading_line: int | None
    heading_title: str
    #: The entry's ``### Quality`` heading, searched forward from the marker and
    #: stopped where the reader stops it — at the next ``## ``. ``None`` when
    #: that search reached a sibling title first.
    quality_line: int | None
    #: The marker is the first non-blank line under the heading it bound to.
    #: Always ``False`` for an unbound marker, which has no heading to be under.
    #: Carried as data rather than used as the verdict: an authored blank line
    #: or a line of prose between heading and marker changes neither what the
    #: reader binds nor what it reads.
    adjacent: bool

    @property
    def bound(self) -> bool:
        """A heading precedes this marker, so the parsers will emit a record."""
        return self.heading_line is not None


def locate_register_entries(text: str) -> tuple[RegisterEntry, ...]:
    """Every canonical marker in one register, with the heading it binds to.

    THE marker-to-heading binding for this module: a marker binds to the
    **preceding** ``## `` heading. Both register parsers below take their entry
    positions from here rather than each walking the lines themselves, so a
    change to what "an entry" means reaches the claim half and the support half
    together — and so the gate that asks whether a register is losing entries
    asks it of the same walk that would lose them (:func:`walk_registers`).

    That binding is where entry loss originates, which is why this reproduces it
    exactly rather than a tidier rule: its job is to answer what the reader will
    see, not what the author meant. Every marker is returned,
    INCLUDING one with no preceding heading — the parsers drop those (a marker
    bound to nothing yields no record) and the census counts them, which is the
    only way the two numbers can ever differ.

    Entries are in document order, every kind included; a caller keys on
    :attr:`RegisterEntry.kind` for the one it parses and on
    :attr:`RegisterEntry.bound` for whether it is an entry at all.
    """
    lines = _strip_code_fences(text).splitlines()
    stripped = [line.strip() for line in lines]

    found: list[RegisterEntry] = []
    heading_line: int | None = None
    heading_title = ""
    for i, line in enumerate(lines):
        if line.startswith("## "):
            heading_line = i
            heading_title = line[3:].strip()
            continue
        marker = _CANONICAL_NODE_ID_RE.match(stripped[i])
        if marker is None:
            continue
        adjacent = False
        if heading_line is not None:
            first = heading_line + 1
            while first < len(stripped) and not stripped[first]:
                first += 1
            adjacent = first == i
        # The entry's own `### Quality`, with the reader's stop: the next `## `
        # is a sibling entry's title, and an H3 `### Quality` never starts with
        # `## `. Reaching the sibling first means this marker's fields are not
        # below it.
        quality_line: int | None = None
        for j in range(i + 1, len(lines)):
            if stripped[j] == "### Quality":
                quality_line = j
                break
            if lines[j].startswith("## "):
                break
        found.append(
            RegisterEntry(
                node_id=marker.group(1),
                kind=marker.group(1).split("-", 1)[0],
                marker_line=i,
                heading_line=heading_line,
                heading_title=heading_title if heading_line is not None else "",
                quality_line=quality_line,
                adjacent=adjacent,
            )
        )
    return tuple(found)


def mis_bound_entries(entries: Sequence[RegisterEntry]) -> tuple[RegisterEntry, ...]:
    """Of ONE register's located entries, those bound under a heading not their own.

    Two shapes, both of which leave the census counts equal and every record
    present:

    * the marker's ``### Quality`` is beyond the next ``## ``, so the record it
      produces carries the PREVIOUS entry's title;
    * two markers share one heading, so two records come back with one title and
      one of the two ids is titled with something never written for it.

    An UNBOUND marker is not mis-bound: it produced no record at all, and the
    census is what reports it. Counting it here as well would put one defect
    under two headings.

    Stated over one register's entries because that is the granularity both
    callers have: :attr:`RegisterWalk.mis_bound` groups its tree-wide walk back
    into registers to ask this, and ``kb_write.store.take_census`` asks it of the
    single file a write is about to touch. The read gate and the write gate
    therefore refuse the same registers by construction, rather than by a parity
    test holding two implementations equal.
    """
    per_heading = Counter(entry.heading_line for entry in entries if entry.bound)
    return tuple(
        entry
        for entry in entries
        if entry.bound and (entry.quality_line is None or per_heading[entry.heading_line] > 1)
    )


def parse_claim_quality_file(
    path: Path,
    kb_root: Path,
    known_ids: set[str] | None = None,
    diagnostic_stream: TextIO | None = None,
) -> list[ClaimEntry]:
    """Parse every canonical entry in a single claim-quality.md file.

    For each `<!-- id: xxxxxx -->` marker, locates the preceding `##` heading
    and the following `### Quality` section. Confidence / solidity /
    build_status / rationale / depends-on / strengthen-by are extracted from
    the Quality section.

    When ``known_ids`` is provided, depends-on edges with a target outside
    that set are dropped, and strengthen-by ``mentioned_ids`` are filtered to
    members of that set. The ID regex is exact, so this filter only catches a
    `clm-`-shaped token that isn't a registered ID (a typo or stale reference)
    — incidental English words are never matched. Drops emit one diagnostic
    line each on ``diagnostic_stream`` (default ``None`` = silent). When
    ``known_ids`` is ``None``, no filtering occurs.
    """
    raw = path.read_text(encoding="utf-8")
    scrubbed = _strip_code_fences(raw)
    lines = scrubbed.splitlines()
    canonical_rel = _posix_relative(path, kb_root)

    # Locate every (id_line_idx, claim_id, heading_line_idx, heading_text) —
    # through the shared locator, so this parser and the census that asks
    # whether it lost an entry read one binding rule.
    entries_meta: list[tuple[int, str, int, str]] = [
        (e.marker_line, e.node_id, e.heading_line, e.heading_title)
        for e in locate_register_entries(raw)
        if e.kind == "clm" and e.bound
    ]

    # For each entry, find its Quality section: the next `### Quality` heading
    # after the id-marker line (the Quality heading is an H3 nested under the
    # claim's `## <Title>` H2). Section ends at the next `## ` heading or EOF.
    quality_starts: list[int | None] = []
    quality_ends: list[int | None] = []
    for idx, (id_line, _claim_id, _hd_idx, _hd_text) in enumerate(entries_meta):
        qstart: int | None = None
        for j in range(id_line + 1, len(lines)):
            if lines[j].strip() == "### Quality":
                qstart = j
                break
            # Stop searching if we hit the next entry's `## ` title heading;
            # the Quality block is typically very close to the id-marker line.
            # An H3 `### Quality` heading does not start with `## `, so it is
            # never mistaken for a sibling-entry title.
            if lines[j].startswith("## "):
                break
        qend: int | None = None
        if qstart is not None:
            for j in range(qstart + 1, len(lines)):
                if lines[j].startswith("## "):
                    qend = j
                    break
            if qend is None:
                qend = len(lines)
        quality_starts.append(qstart)
        quality_ends.append(qend)

    out: list[ClaimEntry] = []
    for (id_line, claim_id, _hd_idx, hd_text), qstart, qend in zip(entries_meta, quality_starts, quality_ends):
        confidence: float | None = None
        solidity: float | None = None
        build_status: str | None = None
        solidity_trace = ""
        rationale = ""
        depends_on: list[DependsOnEdge] = []
        references: list[DependsOnEdge] = []
        strengthen_items: tuple[StrengthenByItem, ...] = ()

        if qstart is not None and qend is not None:
            qlines = lines[qstart + 1 : qend]
            i = 0
            while i < len(qlines):
                ln = qlines[i]
                stripped = ln.strip()
                if stripped.startswith("- confidence:"):
                    confidence = _parse_confidence_line(stripped)
                    i += 1
                elif stripped.startswith("- solidity:"):
                    solidity, build_status, solidity_trace = _parse_solidity_line(stripped)
                    i += 1
                elif stripped.startswith("- rationale:"):
                    rationale_chunks = [stripped.split(":", 1)[1].strip()]
                    i += 1
                    # Fold continuation lines until the next top-level `- key:`
                    # or list-bullet for depends-on/strengthen-by.
                    while i < len(qlines):
                        nxt = qlines[i]
                        nxt_strip = nxt.strip()
                        if _BREAK_AFTER_RATIONALE.match(nxt_strip):
                            break
                        if not nxt_strip:
                            break
                        rationale_chunks.append(nxt_strip)
                        i += 1
                    rationale = _normalize_text(" ".join(rationale_chunks))
                elif stripped.startswith("- depends-on:"):
                    i += 1
                    dep_lines: list[str] = []
                    while i < len(qlines):
                        nxt = qlines[i]
                        nxt_strip = nxt.strip()
                        if _BREAK_AFTER_DEPENDS_ON.match(nxt_strip):
                            break
                        # A sub-bullet starts with `- ` and at least one leading space.
                        if re.match(r"^\s+-\s+", nxt):
                            dep_lines.append(nxt)
                        elif not nxt_strip:
                            pass
                        else:
                            # Continuation of the previous sub-bullet; tack on.
                            if dep_lines:
                                dep_lines[-1] = dep_lines[-1] + " " + nxt_strip
                        i += 1
                    for dep_line in dep_lines:
                        depends_on.extend(
                            _parse_depends_on_line(
                                dep_line,
                                claim_id,
                                known_ids=known_ids,
                                diagnostic_stream=diagnostic_stream,
                                canonical_path=canonical_rel,
                            )
                        )
                elif stripped.startswith("- references:"):
                    i += 1
                    ref_lines: list[str] = []
                    while i < len(qlines):
                        nxt = qlines[i]
                        nxt_strip = nxt.strip()
                        if _BREAK_AFTER_REFERENCES.match(nxt_strip):
                            break
                        if nxt_strip == "---":
                            break
                        if re.match(r"^\s+-\s+", nxt):
                            ref_lines.append(nxt)
                        elif not nxt_strip:
                            pass
                        elif ref_lines:
                            ref_lines[-1] = ref_lines[-1] + " " + nxt_strip
                        i += 1
                    for ref_line in ref_lines:
                        references.extend(
                            _parse_references_line(
                                ref_line,
                                claim_id,
                                known_ids=known_ids,
                                diagnostic_stream=diagnostic_stream,
                                canonical_path=canonical_rel,
                            )
                        )
                elif stripped.startswith("- strengthen-by:"):
                    i += 1
                    sb_lines: list[str] = []
                    while i < len(qlines):
                        nxt = qlines[i]
                        nxt_strip = nxt.strip()
                        if _BREAK_AFTER_STRENGTHEN_BY.match(nxt_strip):
                            break
                        # The Quality section is bounded by the next `## `
                        # heading, so qlines includes the entry-separating
                        # `---` rule. strengthen-by is the last field, so its
                        # loop must stop there or it swallows `---` into the
                        # final bullet's text.
                        if nxt_strip == "---":
                            break
                        sb_lines.append(nxt)
                        i += 1
                    strengthen_items = _parse_strengthen_by_lines(
                        sb_lines,
                        claim_id,
                        known_ids=known_ids,
                        diagnostic_stream=diagnostic_stream,
                    )
                else:
                    i += 1

        out.append(
            ClaimEntry(
                id=claim_id,
                title=hd_text,
                canonical_path=canonical_rel,
                canonical_anchor=_slugify_heading(hd_text),
                confidence=confidence,
                solidity=solidity,
                build_status=build_status,
                solidity_trace=solidity_trace,
                rationale=rationale,
                depends_on=tuple(depends_on),
                strengthen_by=strengthen_items,
                references=tuple(references),
            )
        )
    return out


def parse_support_quality_entries(
    path: Path,
    kb_root: Path,
    known_ids: set[str] | None = None,
    diagnostic_stream: TextIO | None = None,
) -> dict[str, dict]:
    """Parse every ``<!-- id: sup-xxxxxx -->`` entry in a claim-quality.md file.

    A support entry uses the SAME ``### Quality`` shape as a claim entry, with
    ``quality:`` in place of ``confidence:``. This returns the
    claim-quality-resident half of a support node — its local rigor and its own
    dependencies — keyed by sup-id::

        {sup_id: {"quality": float|None, "depends_on": tuple[DependsOnEdge],
                  "rationale": str, "title": str, "canonical_path": str,
                  "canonical_anchor": str, "solidity": float|None}}

    The beneficiary fan-out (``supports:``) is NOT here — it is authored in the
    hosting leaf's frontmatter (parallel to an experiment's ``strengthens:``)
    and parsed by :func:`parse_support_leaf`. The two halves are joined in
    :func:`discover_kb`.

    ``known_ids`` (the set of canonical claim ids) filters a support's
    depends-on targets exactly as for a claim entry; a ``clm-``-shaped target
    outside the set is dropped with a diagnostic.
    """
    raw = path.read_text(encoding="utf-8")
    scrubbed = _strip_code_fences(raw)
    lines = scrubbed.splitlines()
    canonical_rel = _posix_relative(path, kb_root)

    # The support half of the same walk (see :func:`locate_register_entries`).
    entries_meta: list[tuple[int, str, int, str]] = [
        (e.marker_line, e.node_id, e.heading_line, e.heading_title)
        for e in locate_register_entries(raw)
        if e.kind == "sup" and e.bound
    ]

    out: dict[str, dict] = {}
    for id_line, sup_id, _hd_idx, hd_text in entries_meta:
        qstart: int | None = None
        for j in range(id_line + 1, len(lines)):
            if lines[j].strip() == "### Quality":
                qstart = j
                break
            if lines[j].startswith("## "):
                break
        qend = len(lines)
        if qstart is not None:
            for j in range(qstart + 1, len(lines)):
                if lines[j].startswith("## "):
                    qend = j
                    break

        quality: float | None = None
        solidity: float | None = None
        solidity_trace = ""
        rationale = ""
        depends_on: list[DependsOnEdge] = []

        if qstart is not None:
            qlines = lines[qstart + 1 : qend]
            i = 0
            while i < len(qlines):
                stripped = qlines[i].strip()
                if stripped.startswith("- quality:"):
                    quality = _parse_scalar_number_line(stripped)
                    i += 1
                elif stripped.startswith("- solidity:"):
                    solidity, _, solidity_trace = _parse_solidity_line(stripped)
                    i += 1
                elif stripped.startswith("- rationale:"):
                    chunks = [stripped.split(":", 1)[1].strip()]
                    i += 1
                    while i < len(qlines):
                        nxt_strip = qlines[i].strip()
                        if re.match(
                            r"^- (quality|solidity|rationale|depends-on|supports):",
                            nxt_strip,
                        ):
                            break
                        if not nxt_strip:
                            break
                        chunks.append(nxt_strip)
                        i += 1
                    rationale = _normalize_text(" ".join(chunks))
                elif stripped.startswith("- depends-on:"):
                    i += 1
                    dep_lines: list[str] = []
                    while i < len(qlines):
                        nxt = qlines[i]
                        nxt_strip = nxt.strip()
                        if re.match(r"^- (quality|solidity|rationale|supports):", nxt_strip):
                            break
                        if nxt_strip == "---":
                            break
                        if re.match(r"^\s+-\s+", nxt):
                            dep_lines.append(nxt)
                        elif not nxt_strip:
                            pass
                        elif dep_lines:
                            dep_lines[-1] = dep_lines[-1] + " " + nxt_strip
                        i += 1
                    for dep_line in dep_lines:
                        depends_on.extend(
                            _parse_depends_on_line(
                                dep_line,
                                sup_id,
                                known_ids=known_ids,
                                diagnostic_stream=diagnostic_stream,
                                canonical_path=canonical_rel,
                            )
                        )
                else:
                    i += 1

        out[sup_id] = {
            "quality": quality,
            "solidity": solidity,
            "solidity_trace": solidity_trace,
            "rationale": rationale,
            "title": hd_text,
            "canonical_path": canonical_rel,
            "canonical_anchor": _slugify_heading(hd_text),
            "depends_on": tuple(depends_on),
        }
    return out


def parse_work_entries(path: Path, kb_root: Path) -> list[ExternalWork]:
    """Parse every ``<!-- id: work-<key> -->`` entry in one register.

    A work entry carries the same ``## heading`` / marker / ``### Quality``
    skeleton every register entry carries — which is why the census, the
    entry locator and every splice in the write API serve it unchanged — and
    exactly two fields inside it: ``- strength:``, the work's own standing, and
    ``- rationale:``. It carries **no** ``- solidity:`` line and **no**
    ``- depends-on:`` list, because it terminates: there is no cone below a work
    this corpus does not contain, and nothing derives a value for it.
    """
    raw = path.read_text(encoding="utf-8")
    lines = _strip_code_fences(raw).splitlines()
    canonical_rel = _posix_relative(path, kb_root)

    out: list[ExternalWork] = []
    for located in locate_register_entries(raw):
        if located.kind != kb_schema.WORK_PREFIX or not located.bound:
            continue
        qstart, qend = located.quality_line, len(lines)
        if qstart is None:
            continue
        for j in range(qstart + 1, len(lines)):
            if lines[j].startswith("## "):
                qend = j
                break

        strength: float | None = None
        rationale = ""
        qlines = lines[qstart + 1 : qend]
        i = 0
        while i < len(qlines):
            stripped = qlines[i].strip()
            if stripped.startswith("- strength:"):
                strength = _parse_scalar_number_line(stripped)
                i += 1
            elif stripped.startswith("- rationale:"):
                chunks = [stripped.split(":", 1)[1].strip()]
                i += 1
                while i < len(qlines):
                    nxt = qlines[i].strip()
                    if not nxt or re.match(r"^- (strength|rationale):", nxt):
                        break
                    chunks.append(nxt)
                    i += 1
                rationale = _normalize_text(" ".join(chunks))
            else:
                i += 1

        out.append(
            ExternalWork(
                id=located.node_id,
                key=kb_schema.work_key(located.node_id) or "",
                title=located.heading_title,
                canonical_path=canonical_rel,
                canonical_anchor=_slugify_heading(located.heading_title),
                strength=strength,
                rationale=rationale,
            )
        )
    return out


# ---------------------------------------------------------------------------
# Leaf / index discovery
# ---------------------------------------------------------------------------


def parse_leaf(path: Path, kb_root: Path) -> LeafRecord | None:
    """Parse a leaf file's frontmatter and Tier 2 markers.

    Returns None if the file has no frontmatter or its kind is not ``leaf``.
    """
    text = path.read_text(encoding="utf-8")
    fm = parse_frontmatter(text)
    if not fm:
        return None
    kind = fm.get("kind", "")
    if kind != "leaf":
        return None
    claims = tuple(fm.get("claims", []) or ())
    no_claim_value = fm.get("no-claim")
    no_claim_reason = no_claim_value if isinstance(no_claim_value, str) and no_claim_value else None
    # Optional `experiments:` references — exp-ids this leaf cites but does
    # NOT own. Additive to claims:/no-claim:; never a primary field.
    experiments_ref = tuple(i for i in (fm.get("experiments", []) or ()) if i.startswith("exp-"))
    # Tier 2 markers: scan body (minus the frontmatter block) for
    # `<!-- claim-quality: <id> ... -->` markers and intersect with claims.
    scrubbed = strip_frontmatter(text)
    marker_bodies = _TIER2_INLINE_RE.findall(scrubbed)
    marked: set[str] = set()
    for body in marker_bodies:
        for cid in _CLAIM_ID_RE.findall(body):
            if cid in claims:
                marked.add(cid)
    return LeafRecord(
        path=_posix_relative(path, kb_root),
        kind=kind,
        claims=claims,
        tier2_marked=frozenset(marked),
        no_claim_reason=no_claim_reason,
        experiments_ref=experiments_ref,
    )


class ExperimentLeafError(ValueError):
    """Raised when a leaf hosting an ``exp-id`` experiment node is malformed.

    The leaf carries an ``exp-id`` that does not match the
    format :func:`kb_schema.id_body` defines, a ``status`` outside ``{run, pending}``,
    a ``strengthens:`` pair whose strength is outside ``[0, 1]``, or an
    ``experiments:`` reference field (an owning experiment-hosting leaf
    must not also reference other experiments). Co-hosting ``claims:`` is
    allowed — ``exp-id`` and ``claims:`` are orthogonal node-bodies.
    """


def _experiment_heading(text: str) -> str:
    """Return the first Markdown heading text at any level (``#`` … ``######``), or ''.

    The experiment node's ``title``/``canonical_anchor`` come from the leaf's
    title heading. KB leaves use ``##`` for their title heading (a few use
    ``#``); match any level so the title is captured regardless.
    """
    for line in text.splitlines():
        m = re.match(r"^(#{1,6})\s+(.*)$", line)
        if m:
            return m.group(2).strip()
    return ""


def parse_experiment_leaf(path: Path, kb_root: Path) -> list[ExperimentNode]:
    """Parse an experiment-hosting leaf into its ExperimentNode(s).

    A KB leaf is a **container**: it may host ANY number of ``exp`` node-bodies
    (no one-per-leaf cap). Experiment-ness is conferred by a leaf HOSTING an
    ``exp-id``, not by a ``kind``: any ``kind: leaf`` container carrying one or
    more well-formed ``exp-id:`` keys originates that many experiment nodes,
    regardless of whether it ALSO carries ``claims:`` /
    ``sup-id:`` (orthogonal node-bodies in one container). Returns ``[]`` if the
    file has no frontmatter, is not a leaf-kind container, or declares no
    ``exp-id``.

    **Multi-exp syntax (scalar-or-list via repetition).** Each ``exp-id:`` key
    opens a new experiment block; its following ``status:`` and ``strengthens:``
    sub-block belong to that block until the next ``exp-id:`` (or end of
    frontmatter). A single ``exp-id:`` parses as a one-element list — so the
    a scalar single-experiment leaf parses unchanged.

    Each block's ``strengthens`` pairs are returned in source order; a target
    may include a claim originated by this same leaf (a node→node edge between
    two distinct co-located node-bodies — not a self-loop).

    Raises :class:`ExperimentLeafError` when the leaf carries an
    ``experiments:`` reference field (an owning experiment-hosting leaf must not
    also reference other experiments), any ``exp-id`` is malformed, any
    block's ``status`` is outside ``{run, pending}``, or any strengthens pair's
    strength is outside ``[0, 1]``.
    """
    text = path.read_text(encoding="utf-8")
    m = find_frontmatter(text)
    if not m:
        return []
    lines = m.group(1).splitlines()

    # Top-level scan. Each `exp-id:` opens a new block; `status:` / `strengthens:`
    # attach to the currently-open block. `claims:` / `no-claim:` / `sup-id:` are
    # NOT inspected here — they are separate, orthogonal node-bodies.
    kind = ""
    has_experiments_ref = False
    in_strengthens = False
    # Per-block accumulators, parallel-indexed: exp_ids[i] owns statuses[i] and
    # strengthens_pairs[i]. A new exp-id appends a fresh slot.
    exp_ids: list[str] = []
    statuses: list[str | None] = []
    strengthens_pairs: list[list[tuple[str, float]]] = []
    for line in lines:
        stripped = line.strip()
        if not stripped:
            continue
        pair = _STRENGTHENS_PAIR_RE.match(line)
        if in_strengthens and pair and strengthens_pairs:
            strengthens_pairs[-1].append((pair.group(1), float(pair.group(2))))
            continue
        if ":" in stripped:
            key = stripped.split(":", 1)[0].strip().lstrip("- ").strip()
            value = stripped.split(":", 1)[1].strip()
            if key == "strengthens":
                in_strengthens = True
                continue
            in_strengthens = False
            if key == "kind":
                kind = value
            elif key == "exp-id":
                exp_ids.append(value)
                statuses.append(None)
                strengthens_pairs.append([])
            elif key == "status":
                if statuses:
                    statuses[-1] = value
            elif key == "experiments":
                if value:
                    has_experiments_ref = True

    if kind != "leaf":
        return []
    if not exp_ids:
        return []

    rel = _posix_relative(path, kb_root)
    if has_experiments_ref:
        raise ExperimentLeafError(
            f"{rel}: experiment-hosting leaf carries experiments: — an owning "
            f"experiment leaf must not also reference other experiments."
        )

    heading = _experiment_heading(text)
    anchor = _slugify_heading(heading)
    nodes: list[ExperimentNode] = []
    for exp_id, status, pairs in zip(exp_ids, statuses, strengthens_pairs):
        if not _EXP_ID_RE.fullmatch(exp_id):
            raise ExperimentLeafError(
                f"{rel}: experiment-hosting leaf has malformed exp-id "
                f"{exp_id!r} (expected \\b{kb_schema.id_body('exp')}\\b)."
            )
        if status not in ("run", "pending"):
            raise ExperimentLeafError(
                f"{rel}: experiment {exp_id} has invalid status {status!r} " f"(expected 'run' or 'pending')."
            )
        for claim_id, strength in pairs:
            # The parallel of the on-point fraction's range check below: a
            # strength outside [0, 1] is a claim's experimental solidity off
            # the build-band ladder.
            if not (0.0 <= strength <= 1.0):
                raise ExperimentLeafError(
                    f"{rel}: experiment {exp_id} strength for {claim_id} " f"is {strength} — must be in [0, 1]."
                )
        nodes.append(
            ExperimentNode(
                id=exp_id,
                title=heading,
                canonical_path=rel,
                canonical_anchor=anchor,
                status=status,
                strengthens=tuple(pairs),
            )
        )
    return nodes


class SupportLeafError(ValueError):
    """Raised when a leaf hosting a ``sup-id`` support node is malformed.

    The leaf carries a ``sup-id`` outside the format :func:`kb_schema.id_body` defines,
    or a ``supports:`` block with a malformed claim id or an on-point
    ``fraction`` that is neither ``*pending*`` nor in ``[0, 1]`` (0 says the
    support bears nothing on the claim and is a value; > 1 is out of range).
    Co-hosting ``claims:`` /
    ``exp-id:`` / ``no-claim:`` is allowed — they are orthogonal node-bodies.
    """


def parse_support_leaf(path: Path, kb_root: Path) -> list[SupportNode]:
    """Parse a support-hosting leaf into its partial SupportNode(s).

    A KB leaf is a **container**: it may host ANY number of ``sup`` node-bodies
    (no one-per-leaf cap). Support-ness is conferred by a leaf HOSTING a
    ``sup-id`` (parallel to how an ``exp-id`` confers experiment-ness): any
    ``kind: leaf`` container carrying one or more well-formed ``sup-id:`` keys
    originates that many support nodes, regardless of whether it
    ALSO carries ``claims:`` / ``exp-id:`` / ``no-claim:`` (orthogonal
    node-bodies). Returns ``[]`` if the file has no frontmatter, is not a
    leaf-kind container, or declares no ``sup-id``.

    **Multi-sup syntax (scalar-or-list via repetition).** Each ``sup-id:`` key
    opens a new support block; its following ``supports:`` sub-block of
    ``clm-<id>: <fraction>`` beneficiary pairs (one per line, indented) belongs
    to that block until the next ``sup-id:`` (or end of frontmatter). A single
    ``sup-id:`` parses as a one-element list. ``<fraction>`` is the on-point
    fraction f ∈ [0, 1] or the literal ``*pending*`` (an intended-but-unassessed
    edge, stored as PENDING_FRACTION). Each returned node carries the
    canonical_path/anchor/title from the leaf and its own ``supports`` fan-out;
    its ``quality`` / ``depends_on`` / ``rationale`` are filled in from the
    support's claim-quality entry by :func:`discover_kb`.

    Raises :class:`SupportLeafError` for a malformed ``sup-id``, a malformed
    ``supports:`` claim id, or an on-point fraction outside ``[0, 1]``.
    """
    text = path.read_text(encoding="utf-8")
    m = find_frontmatter(text)
    if not m:
        return []
    lines = m.group(1).splitlines()

    kind = ""
    in_supports = False
    # Per-block accumulators, parallel-indexed: sup_ids[i] owns supports_pairs[i].
    sup_ids: list[str] = []
    supports_pairs: list[list[tuple[str, float | _PendingFraction]]] = []
    for line in lines:
        stripped = line.strip()
        if not stripped:
            continue
        pair = _SUPPORTS_PAIR_RE.match(line)
        if in_supports and pair and supports_pairs:
            raw = pair.group(2)
            fraction = PENDING_FRACTION if raw == PENDING_LITERAL else float(raw)
            supports_pairs[-1].append((pair.group(1), fraction))
            continue
        if ":" in stripped:
            key = stripped.split(":", 1)[0].strip().lstrip("- ").strip()
            value = stripped.split(":", 1)[1].strip()
            if key == "supports":
                in_supports = True
                continue
            in_supports = False
            if key == "kind":
                kind = value
            elif key == "sup-id":
                sup_ids.append(value)
                supports_pairs.append([])

    if kind != "leaf":
        return []
    if not sup_ids:
        return []

    rel = _posix_relative(path, kb_root)
    heading = _experiment_heading(text)
    anchor = _slugify_heading(heading)
    nodes: list[SupportNode] = []
    for sup_id, pairs in zip(sup_ids, supports_pairs):
        if not _SUP_ID_RE.fullmatch(sup_id):
            raise SupportLeafError(
                f"{rel}: support-hosting leaf has malformed sup-id {sup_id!r} "
                f"(expected \\b{kb_schema.id_body('sup')}\\b)."
            )
        for claim_id, fraction in pairs:
            # A pending fraction is a valid authored value; a numeric fraction
            # must lie in [0, 1] (0 is a value — the support bears nothing on
            # this claim — and > 1 is invalid).
            if fraction is PENDING_FRACTION:
                continue
            if not (0.0 <= fraction <= 1.0):
                raise SupportLeafError(
                    f"{rel}: support {sup_id} on-point fraction for {claim_id} "
                    f"is {fraction} — must be in [0, 1] or *pending*."
                )
        nodes.append(
            SupportNode(
                id=sup_id,
                title=heading,
                canonical_path=rel,
                canonical_anchor=anchor,
                quality=None,  # filled from the claim-quality entry in discover_kb
                depends_on=(),  # filled from the claim-quality entry in discover_kb
                supports=tuple(pairs),
            )
        )
    return nodes


def _parse_index(path: Path, kb_root: Path) -> IndexRecord | None:
    """Parse an ``index`` or ``entry-point`` kind file."""
    text = path.read_text(encoding="utf-8")
    fm = parse_frontmatter(text)
    if not fm:
        return None
    kind = fm.get("kind", "")
    if kind not in ("index", "entry-point"):
        return None
    declared = tuple(fm.get("subtree-claims", []) or ())
    declared_exp = tuple(i for i in (fm.get("subtree-experiments", []) or ()) if i.startswith("exp-"))
    return IndexRecord(
        path=_posix_relative(path, kb_root),
        kind=kind,
        declared_subtree_claims=declared,
        declared_subtree_experiments=declared_exp,
    )


def collect_known_claim_ids(kb_root: Path | None = None) -> set[str]:
    """First-pass scan of every ``claim-quality.md`` for canonical IDs.

    Returns the set of IDs marked by ``<!-- id: clm-xxxxxx -->`` in any
    non-excluded ``claim-quality.md`` register, after stripping fenced code
    blocks (so example placeholders inside ```` ``` ```` blocks do not count).

    ``kb_root`` defaults to lazy discovery via ``kb_util.kb_root()``.
    """
    if kb_root is None:
        kb_root = kb_util.kb_root()
    known: set[str] = set()
    for cq in sorted(kb_root.rglob("claim-quality.md")):
        if any(part in EXCLUDE_DIRS for part in cq.relative_to(kb_root).parts[:-1]):
            continue
        scrubbed = _strip_code_fences(cq.read_text(encoding="utf-8"))
        for line in scrubbed.splitlines():
            m = _CANONICAL_ID_RE.match(line.strip())
            if m:
                known.add(m.group(1))
    return known


@dataclass(frozen=True)
class IdRecord:
    """Where one authored node id lives, per the authored Markdown alone.

    ``kind`` is the id's prefix token (``clm`` / ``exp`` / ``sup`` / ``work``).
    ``register_path`` is the kb-root-relative POSIX path of the
    ``claim-quality.md`` holding its canonical ``<!-- id: -->`` entry, or
    ``None`` when no register keys it (the normal case for ``exp-``, which is
    declared in leaf frontmatter; a defect for a ``clm-`` / ``sup-``).
    ``hosting_leaf`` is the kb-root-relative POSIX path of the leaf hosting the
    node body, or ``None`` when no leaf does (also a defect). Where several
    leaves carry the id, a declaring leaf (``exp-id:`` / ``sup-id:``) wins over
    a citing one and the lowest-sorted path wins among equals — one
    representative home, not the full reverse map (that is
    :func:`build_leaf_references`).
    """

    kind: str
    register_path: str | None
    hosting_leaf: str | None


def _register_id_occurrences(kb_root: Path) -> dict[str, list[str]]:
    """Every canonical ``<!-- id: -->`` marker in every register: id → the registers keying it.

    One walk of the registers, two readers. :func:`scan_authored_ids` takes the
    first register for each id, because its return is keyed by id and can hold
    only one; :func:`scan_duplicate_register_ids` asks how many there were. Both
    therefore see the same files under the same exclusion rules.

    Occurrences are kept in path order and repeats are preserved, so one file
    keying an id twice is as visible as two files keying it once.
    """
    occurrences: dict[str, list[str]] = {}
    for cq in sorted(kb_root.rglob("claim-quality.md")):
        if any(part in EXCLUDE_DIRS for part in cq.relative_to(kb_root).parts[:-1]):
            continue
        register_rel = _posix_relative(cq, kb_root)
        for line in _strip_code_fences(cq.read_text(encoding="utf-8")).splitlines():
            m = _CANONICAL_NODE_ID_RE.match(line.strip())
            if m:
                occurrences.setdefault(m.group(1), []).append(register_rel)
    return occurrences


def scan_duplicate_register_ids(kb_root: Path | None = None) -> dict[str, tuple[str, ...]]:
    """Node ids keyed by more than one canonical register entry: id → those registers.

    The mint-boundary question :func:`scan_authored_ids` structurally cannot
    answer. Its return is keyed by id, so a second entry for an id already keyed
    is silently dropped rather than reported — a collision looks exactly like a
    clean inventory. This walks the same registers and reports the collisions.

    A single register keying one id twice counts, and its path appears twice in
    that id's tuple. An empty return means every keyed id is keyed once.

    ``kb_root`` defaults to lazy discovery via ``kb_util.kb_root()``.
    """
    if kb_root is None:
        kb_root = kb_util.kb_root()
    return {
        node_id: tuple(registers)
        for node_id, registers in sorted(_register_id_occurrences(kb_root).items())
        if len(registers) > 1
    }


def scan_authored_ids(kb_root: Path | None = None) -> dict[str, IdRecord]:
    """Inventory every authored node id in the KB, keyed by id, sorted by id.

    The inventory a mint-bearing re-entry checks against, and the map by which
    unscored ids are partitioned across their owning registers. It is sourced
    from **authored Markdown only** — the ``claim-quality.md`` registers plus
    leaf frontmatter — and never from ``.index/``, so it is correct whether or
    not ``refresh`` has run: an empty (or zero-byte) derived index yields the
    same inventory as a freshly rebuilt one. Reading ``.index/`` instead would
    report "no existing ids" for a KB whose registers hold dozens, and mint a
    second colliding set.

    Both sources are needed to cover the three kinds, because the two halves of
    a node are authored in different places: a ``clm-`` / ``sup-`` id is keyed
    canonically by a register entry, while ``exp-`` ids and every id's hosting
    leaf exist only in leaf frontmatter. Scanning registers alone would miss
    every experiment and could fill no ``hosting_leaf``.

    ``kb_root`` defaults to lazy discovery via ``kb_util.kb_root()``.
    """
    if kb_root is None:
        kb_root = kb_util.kb_root()

    # First keyer wins; :func:`scan_duplicate_register_ids` is what reports the
    # ones this collapse hides.
    registers = {node_id: found[0] for node_id, found in _register_id_occurrences(kb_root).items()}

    # `kb_files` yields sorted, non-excluded leaves (registers among the
    # excluded names), so `setdefault` below resolves a multi-leaf id to its
    # lowest-sorted path.
    declared: dict[str, str] = {}
    cited: dict[str, str] = {}
    for path in kb_files(kb_root):
        fm = find_frontmatter(path.read_text(encoding="utf-8"))
        if fm is None:
            continue
        leaf_rel = _posix_relative(path, kb_root)
        block = fm.group(1)
        for pattern in _LEAF_DECL_RES:
            for node_id in pattern.findall(block):
                declared.setdefault(node_id, leaf_rel)
        fields = _frontmatter_fields(block)
        for key in _LEAF_CITE_KEYS:
            for node_id in fields.get(key, []) or ():
                if _ANY_ID_RE.fullmatch(node_id):
                    cited.setdefault(node_id, leaf_rel)

    return {
        node_id: IdRecord(
            kind=node_id.split("-", 1)[0],
            register_path=registers.get(node_id),
            hosting_leaf=declared.get(node_id) or cited.get(node_id),
        )
        for node_id in sorted(registers.keys() | declared.keys() | cited.keys())
    }


def parse_register_staged_supports(path: Path) -> dict[str, list[tuple[str, float | _PendingFraction]]]:
    """The ``supports:`` pairs staged inside ONE register's ``sup-`` entries.

    THE staging grammar, at the granularity of a single file. Two readers stand
    on it and neither re-spells it: :func:`_register_staged_support_edges` takes
    this read over the whole tree, and the write API's readback
    (``kb_write.store``) takes it over the candidate it has just composed,
    before that candidate may replace a live file. Without a per-file entry
    point the writing side would need a second implementation of this grammar,
    which is the emitter/checker split in its most destructive form.

    Lexical by necessity — a register entry is Markdown, not frontmatter — so
    it reads the ``### Quality`` block's ``- supports:`` bullet and the indented
    pairs under it, and stops at the next sibling bullet. Pairs are matched
    before that stop is tested, so a pair line is never taken for the sibling
    that would close the block.

    Every ``sup-`` entry in the file is keyed, with an empty list for one that
    stages no pairs — an isolated support is a fact a caller acts on, not an
    absence. A ``clm-`` entry hosts no fan-out and opens no block.
    """
    staged: dict[str, list[tuple[str, float | _PendingFraction]]] = {}
    sup_id: str | None = None
    in_supports = False
    for line in _strip_code_fences(path.read_text(encoding="utf-8")).splitlines():
        stripped = line.strip()
        marker = _CANONICAL_NODE_ID_RE.match(stripped)
        if marker:
            # A new entry of any kind closes the previous one; only a
            # `sup-` entry can carry a fan-out.
            node_id = marker.group(1)
            sup_id = node_id if node_id.startswith("sup-") else None
            in_supports = False
            if sup_id is not None:
                staged.setdefault(sup_id, [])
            continue
        if sup_id is None:
            continue
        pair = _SUPPORTS_PAIR_RE.match(line)
        if in_supports and pair:
            raw = pair.group(2)
            staged[sup_id].append((pair.group(1), PENDING_FRACTION if raw == PENDING_LITERAL else float(raw)))
            continue
        # Pairs are matched first, so any remaining bullet is a sibling key
        # in the entry's `### Quality` block: `- supports:` opens the block
        # and every other one closes it.
        if stripped.startswith("- "):
            in_supports = stripped == "- supports:"
    return staged


def _register_staged_support_edges(kb_root: Path) -> dict[str, list[tuple[str, float | _PendingFraction]]]:
    """Every ``supports:`` pair staged in a ``sup-`` register entry, KB-wide.

    The pre-leaf home of a fan-out (see :func:`scan_authored_support_edges`),
    read file by file through :func:`parse_register_staged_supports`. An id
    keyed by two registers accumulates both files' pairs, exactly as it did
    when this walk carried the grammar itself.
    """
    staged: dict[str, list[tuple[str, float | _PendingFraction]]] = {}
    for cq in sorted(kb_root.rglob("claim-quality.md")):
        if any(part in EXCLUDE_DIRS for part in cq.relative_to(kb_root).parts[:-1]):
            continue
        for sup_id, pairs in parse_register_staged_supports(cq).items():
            staged.setdefault(sup_id, []).extend(pairs)
    return staged


def scan_authored_support_edges(
    kb_root: Path | None = None,
) -> dict[str, tuple[tuple[str, float | _PendingFraction], ...]]:
    """Every authored ``sup-`` node and the beneficiary pairs written for it.

    Sourced from **authored Markdown only** — never from ``.index/`` — so it is
    answerable at any point in a build, including before a refresh has run or
    before a hosting leaf exists. That is the whole point: a support's derived
    record needs a hosting leaf, so asking the derived index about a fan-out
    authored one stage earlier can only ever return nothing.

    A fan-out has two legitimate homes and this reads both:

    * the HOSTING LEAF's frontmatter (:func:`parse_support_leaf`) — the schema's
      canonical home, and the only one the claim graph reads; and
    * the ``sup-`` register entry's ``### Quality`` block, where the pairs are
      staged while the leaf that will carry them does not exist yet (a support
      id is minted a full stage before its leaf is written), for the distiller
      to transcribe.

    Returns ``{sup_id: ((claim_id, fraction), ...)}``, sorted by id, with an
    empty tuple for a node whose fan-out was never authored anywhere. Fractions
    are floats or :data:`PENDING_FRACTION`, as :class:`SupportNode` carries them.

    NOT a graph source: pairs from the staging home are not edges and must not
    be built into claim-graph records. Use this to ask what has been authored.

    ``kb_root`` defaults to lazy discovery via ``kb_util.kb_root()``.
    Raises :class:`SupportLeafError` from a malformed support-hosting leaf.
    """
    if kb_root is None:
        kb_root = kb_util.kb_root()

    edges = _register_staged_support_edges(kb_root)
    for path in kb_files(kb_root):
        for node in parse_support_leaf(path, kb_root):
            edges.setdefault(node.id, []).extend(node.supports)
    return {sup_id: tuple(pairs) for sup_id, pairs in sorted(edges.items())}


# ---------------------------------------------------------------------------
# The register walk: census, binding, and the two-end fan-out reconciliation
# ---------------------------------------------------------------------------
#
# Three questions of one set of bytes, answered in one pass and returned as
# DATA. No printing, no exit code, no policy about which answers are failures —
# that belongs to each consumer — `verify_kb_metadata` formats the defects as
# gate failures. A consumer that re-walked the registers to get its own answer
# would be a second reader of a grammar this module defines, which is exactly
# the drift the single-source discipline exists to close.


@dataclass(frozen=True)
class RegisterCensus:
    """Markers against records for ONE node kind in ONE register.

    The no-lost-entries promise, as a measurement. Taken per kind because a register
    may host both and the two kinds have two parsers: counting *every* marker
    against ``parse_claim_quality_file`` would report a mismatch on every
    register that holds a support entry.
    """

    register_path: str
    kind: str  # "clm" | "sup"
    markers: tuple[str, ...]
    records: tuple[str, ...]

    @property
    def consistent(self) -> bool:
        """True when every canonical marker of this kind produced a record."""
        return len(self.markers) == len(self.records)

    @property
    def lost(self) -> tuple[str, ...]:
        """The marked ids no record came back for, sorted.

        Empty on a register whose drift is a REPEATED id rather than a dropped
        one — the counts still disagree, and :attr:`consistent` is what says so.
        """
        return tuple(sorted(set(self.markers) - set(self.records)))


@dataclass(frozen=True)
class FanOutRecord:
    """One ``supports`` beneficiary edge, as recorded at each authored end.

    ``supports`` is the one relationship the schema records TWICE by design:
    staged in the ``sup-`` entry's ``### Quality`` block, and declared in the
    hosting leaf's ``supports:`` frontmatter. The leaf is canonical — it is the
    only end :func:`discover_kb` builds the claim graph from — and the staged
    end exists because a support id is minted a full stage before the leaf that
    will carry it (see :func:`scan_authored_support_edges`).

    ``staged`` / ``declared`` hold that end's on-point fraction, or ``None``
    when the end does not record this beneficiary at all. ``None`` here is
    "absent from this end" and is distinct from :data:`PENDING_FRACTION`, which
    is a value the end *did* record.

    A single record is enough to draw the edge and to say what is wrong with
    it; nothing about which of those states is a *failure* is decided here.
    """

    sup_id: str
    claim_id: str
    register_path: str
    #: The hosting leaf's path, or ``None`` when no leaf originates this
    #: support yet (the documented pre-leaf staging stage).
    leaf_path: str | None
    staged: "float | _PendingFraction | None"
    declared: "float | _PendingFraction | None"
    #: Whether the register's entry for this support stages ANY pairs. A
    #: register that stages none is not participating in the double entry —
    #: which is the ordinary case, not a missing end.
    register_stages: bool

    @property
    def ends(self) -> tuple[str, ...]:
        """Which authored ends record this edge, in schema order."""
        return tuple(name for name, value in (("register", self.staged), ("leaf", self.declared)) if value is not None)

    @property
    def double_entered(self) -> bool:
        """Both ends are live, so the two are meant to agree.

        False for a fan-out the register never staged (staging is optional) and
        for one whose hosting leaf does not exist yet.
        """
        return self.register_stages and self.leaf_path is not None

    @property
    def agrees(self) -> bool:
        """Both ends record this edge at the same on-point fraction."""
        if self.staged is None or self.declared is None:
            return False
        staged_pending = self.staged is PENDING_FRACTION
        declared_pending = self.declared is PENDING_FRACTION
        if staged_pending or declared_pending:
            return staged_pending and declared_pending
        return self.staged == self.declared


@dataclass(frozen=True)
class RegisterWalk:
    """Everything one pass over the registers found."""

    census: tuple[RegisterCensus, ...]
    entries: tuple[tuple[str, RegisterEntry], ...]
    fan_out: tuple[FanOutRecord, ...]

    @property
    def mis_bound(self) -> tuple[tuple[str, RegisterEntry], ...]:
        """``(register_path, entry)`` for every BOUND marker under a heading not its own.

        The predicate is :func:`mis_bound_entries`, which ``kb_write.store``'s
        write-time census also asks — one implementation, so the read gate and
        the write gate cannot come to disagree about what a healthy register is.

        Grouped back into registers before asking, because the predicate counts
        markers per heading and a heading is a position in ONE file: two entries
        at line 6 of two registers do not share a heading.
        """
        per_register: dict[str, list[RegisterEntry]] = {}
        for path, entry in self.entries:
            per_register.setdefault(path, []).append(entry)
        return tuple((path, entry) for path, entries in per_register.items() for entry in mis_bound_entries(entries))

    @property
    def unreconciled(self) -> tuple[FanOutRecord, ...]:
        """Double-entered fan-out edges whose two ends do not agree."""
        return tuple(record for record in self.fan_out if record.double_entered and not record.agrees)


def _registers(kb_root: Path):
    """Every non-excluded ``claim-quality.md`` under ``kb_root``, in path order."""
    for path in sorted(kb_root.rglob("claim-quality.md")):
        if any(part in EXCLUDE_DIRS for part in path.relative_to(kb_root).parts[:-1]):
            continue
        yield path


def walk_registers(state: KbState, kb_root: Path | None = None) -> RegisterWalk:
    """Census, marker binding and fan-out reconciliation — one pass, as data.

    Each register is read once and asked three things:

    * **the census** — per kind, the canonical markers against the records the
      production parser returns for them. Enforced only on the hot path of a
      write, this rule leaves a KB nobody is writing into unmeasured, and its
      markers can outnumber its records with every gate green.
    * **the binding** — whether each marker is the first line under the heading
      it bound to. Counting sees a marker bound to NOTHING; only this sees one
      bound to the WRONG heading, which leaves the counts equal and every title
      in the file off by one entry.
    * **the fan-out** — one :class:`FanOutRecord` per ``supports`` beneficiary
      named at either authored end, so a consumer can compare the two.

    ``state`` supplies the leaf-declared end (``state.supports``); ``kb_root``
    defaults to lazy discovery via ``kb_util.kb_root()``. Read-only, and no
    judgement about which findings are failures — see the section note above.
    """
    if kb_root is None:
        kb_root = kb_util.kb_root()

    hosting = {sup.id: sup for sup in state.supports}
    census: list[RegisterCensus] = []
    entries: list[tuple[str, RegisterEntry]] = []
    fan_out: list[FanOutRecord] = []

    for path in _registers(kb_root):
        rel = _posix_relative(path, kb_root)
        located = locate_register_entries(path.read_text(encoding="utf-8"))
        entries.extend((rel, entry) for entry in located)

        for kind, record_ids in (
            ("clm", tuple(entry.id for entry in parse_claim_quality_file(path, kb_root))),
            ("sup", tuple(parse_support_quality_entries(path, kb_root))),
        ):
            census.append(
                RegisterCensus(
                    register_path=rel,
                    kind=kind,
                    markers=tuple(entry.node_id for entry in located if entry.kind == kind),
                    records=record_ids,
                )
            )

        for sup_id, pairs in sorted(parse_register_staged_supports(path).items()):
            sup = hosting.get(sup_id)
            staged = dict(pairs)
            declared = dict(sup.supports) if sup is not None else {}
            for claim_id in sorted(staged.keys() | declared.keys()):
                fan_out.append(
                    FanOutRecord(
                        sup_id=sup_id,
                        claim_id=claim_id,
                        register_path=rel,
                        leaf_path=sup.canonical_path if sup is not None else None,
                        staged=staged.get(claim_id),
                        declared=declared.get(claim_id),
                        register_stages=bool(pairs),
                    )
                )

    return RegisterWalk(census=tuple(census), entries=tuple(entries), fan_out=tuple(fan_out))


def reconcile_support_fan_out(state: KbState, kb_root: Path | None = None) -> tuple[FanOutRecord, ...]:
    """The two-end map of every ``supports`` beneficiary edge, as data.

    The named entry point for a consumer that wants the relationship map and
    not the census beside it — the claim-graph renderer being the one in view,
    which draws an edge missing an end differently from one whose two ends
    disagree.

    **The discrimination surface is** :attr:`FanOutRecord.staged`,
    :attr:`~FanOutRecord.declared` **and** :attr:`~FanOutRecord.leaf_path`, not
    a count. ``len(record.ends) == 1`` says only *recorded at one end*, and the
    one-ended cases are two different situations a consumer renders differently:
    ``staged`` set with ``declared`` unset and ``leaf_path`` present is an edge
    the hosting leaf never declared, while ``leaf_path`` absent is an edge whose
    leaf does not exist yet — the ordinary mid-build state, and not a fault at
    all. The disagreement case is a third: both ends present and
    :attr:`~FanOutRecord.agrees` false, which no count sees, since both ends are
    populated. So branch on which end is populated and on whether the leaf
    exists; :attr:`~FanOutRecord.double_entered` is the ready-made answer to
    "are both ends meant to agree here".

    It is :func:`walk_registers`' fan-out half and costs the same one pass; the
    point of routing through it is that neither consumer re-implements the
    staging grammar, which only this module defines.
    """
    return walk_registers(state, kb_root).fan_out


def discover_kb(
    kb_root: Path | None = None,
    diagnostic_stream: TextIO | None = sys.stderr,
) -> KbState:
    """One-shot load of the KB. Reads every non-excluded .md file under
    kb_root plus every claim-quality.md register and ``CLAUDE.md`` (for the
    framework nodes — invariants and axioms).

    Two passes over claim-quality registers: the first collects the canonical
    set of claim IDs; the second parses entries with that set in hand so a
    `clm-`-shaped token that isn't a registered ID (a typo or stale reference)
    is rejected as a depends-on target or strengthen-by mention. Diagnostics
    for rejected candidates are written to ``diagnostic_stream`` (default
    ``sys.stderr``; pass ``None`` to silence).

    ``kb_root`` defaults to lazy discovery via ``kb_util.kb_root()``.
    """
    if kb_root is None:
        kb_root = kb_util.kb_root()
    known_ids = collect_known_claim_ids(kb_root)

    claim_entries: list[ClaimEntry] = []
    support_quality: dict[str, dict] = {}
    works: list[ExternalWork] = []
    for cq in sorted(kb_root.rglob("claim-quality.md")):
        # Exclude session-tree claim-quality files if any.
        if any(part in EXCLUDE_DIRS for part in cq.relative_to(kb_root).parts[:-1]):
            continue
        # External works are register entries too, and every register is read
        # for them rather than only the one this toolchain writes them into: a
        # KB whose works landed elsewhere is a KB with those nodes, and a reader
        # scoped to the canonical path would report them missing instead.
        works.extend(parse_work_entries(cq, kb_root))
        claim_entries.extend(
            parse_claim_quality_file(
                cq,
                kb_root,
                known_ids=known_ids,
                diagnostic_stream=diagnostic_stream,
            )
        )
        # Support entries (`<!-- id: sup-xxxxxx -->`) share the register and
        # the `### Quality` shape; collect their claim-quality-resident half
        # (quality + own depends-on + rationale) keyed by sup-id.
        support_quality.update(
            parse_support_quality_entries(
                cq,
                kb_root,
                known_ids=known_ids,
                diagnostic_stream=diagnostic_stream,
            )
        )

    leaves: list[LeafRecord] = []
    indexes: list[IndexRecord] = []
    experiments: list[ExperimentNode] = []
    support_leaves: list[SupportNode] = []
    for p in kb_files(kb_root):
        # A single container may host SEVERAL orthogonal node-bodies — a claim
        # list (LeafRecord), an experiment (ExperimentNode), and/or a support
        # (SupportNode): `kind` is the container's topography role, while
        # `claims:` / `exp-id:` / `sup-id:` are independent claim-graph
        # node-bodies it originates. Run every parser; do not short-circuit:
        # hosting an id is what confers the node, so a container may
        # originate any combination of them.
        leaf = parse_leaf(p, kb_root)
        if leaf is not None:
            leaves.append(leaf)
        exps = parse_experiment_leaf(p, kb_root)
        experiments.extend(exps)
        sups = parse_support_leaf(p, kb_root)
        support_leaves.extend(sups)
        if leaf is None and not exps and not sups:
            idx = _parse_index(p, kb_root)
            if idx is not None:
                indexes.append(idx)

    # Join each support leaf's fan-out (supports edges, canonical home) with its
    # claim-quality-resident internals (quality, own deps, rationale). A support
    # leaf with no matching claim-quality entry stays pending-quality with no
    # own deps — the verifier flags the missing entry as an orphan.
    supports: list[SupportNode] = []
    for sup in support_leaves:
        cq_half = support_quality.get(sup.id, {})
        supports.append(
            SupportNode(
                id=sup.id,
                title=sup.title,
                canonical_path=sup.canonical_path,
                canonical_anchor=sup.canonical_anchor,
                quality=cq_half.get("quality"),
                depends_on=cq_half.get("depends_on", ()),
                supports=sup.supports,
                rationale=cq_half.get("rationale", ""),
                solidity=cq_half.get("solidity"),
                solidity_trace=cq_half.get("solidity_trace", ""),
            )
        )

    framework_nodes = parse_framework_nodes(kb_root)

    return KbState(
        claim_entries=tuple(claim_entries),
        leaves=tuple(leaves),
        indexes=tuple(indexes),
        framework_nodes=tuple(framework_nodes),
        experiments=tuple(experiments),
        supports=tuple(supports),
        works=tuple(sorted(works, key=lambda w: w.id)),
    )


# ---------------------------------------------------------------------------
# Build-band derivation
# ---------------------------------------------------------------------------


def derive_build_band(solidity: float | None) -> str:
    """Map solidity in [0, 1] to a stable build_band enum.

    The slug/threshold ladder is single-sourced in
    :data:`kb_schema.BUILD_BAND_LADDER`; a ``None`` solidity is the unscored
    bucket (:data:`kb_schema.UNKNOWN_BAND_SLUG`).
    """
    band = kb_schema.band_for_solidity(solidity)
    return kb_schema.UNKNOWN_BAND_SLUG if band is None else band.slug


# ---------------------------------------------------------------------------
# Solidity computation (derived field)
# ---------------------------------------------------------------------------
#
# ``solidity`` is a *derived* quality field: it is computed mechanically from
# the hand-authored ``confidence`` values and the claim depends-on graph, not
# hand-maintained. The build-status phrase and the depends-on ``(solidity X)``
# annotations are likewise derived. ``refresh_kb_metadata`` owns writing all
# three back; ``verify_kb_metadata`` verifies the on-disk values match.

# Build-status phrase bands (mapped from solidity), mirroring the
# "Build-status legend" table in the root claim-quality.md preamble. The
# phrases here are the parenthetical text WITHOUT the surrounding parens. The
# (threshold, phrase) pairs are sourced from the single-sourced ladder in
# kb_schema so they cannot drift from the build_band slugs.
_BUILD_STATUS_BANDS: tuple[tuple[float, str], ...] = tuple(
    (b.threshold, b.status_phrase) for b in kb_schema.BUILD_BAND_LADDER
)


def build_status_phrase(solidity: float | None) -> str | None:
    """Map a solidity value to its build-status phrase.

    Returns the band phrase (without surrounding parens) for a numeric
    solidity, or ``None`` when ``solidity`` is ``None`` (an entry whose
    confidence is unset — its solidity is undefined and not written).
    The bands mirror the legend table in the root ``claim-quality.md``.
    """
    if solidity is None:
        return None
    for threshold, phrase in _BUILD_STATUS_BANDS:
        if solidity >= threshold:
            return phrase
    # solidity < 0.0 is out of the documented [0, 1] domain; treat as refuted.
    return _BUILD_STATUS_BANDS[-1][1]


def round_half_up_2dp(value: float) -> float:
    """Round ``value`` to 2 decimal places using round-half-up.

    The KB convention rounds solidity at the 0.005 boundary AWAY from zero
    (round-half-up), NOT with Python's built-in banker's rounding. Using
    ``Decimal`` keeps the boundary deterministic and matches what a human
    auditing the arithmetic by hand would write.
    """
    return float(Decimal(str(value)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))


class SolidityCycleError(ValueError):
    """Raised when the claim depends-on graph contains a cycle.

    Solidity is undefined for the members of a dependency cycle (the
    bottom-up recurrence has no base case). The offending claim ids are
    available on ``cycle_members`` — every node the walk could not reach,
    which is the cycle plus whatever it blocks, scored or ``*pending*``.
    """

    def __init__(self, cycle_members: list[str]) -> None:
        self.cycle_members = cycle_members
        joined = ", ".join(cycle_members)
        super().__init__(f"claim depends-on graph has a cycle among {len(cycle_members)} " f"claim(s): {joined}")


@dataclass(frozen=True)
class SolidityResult:
    """The three derived solidity branches for one claim (SCHEMA definitive rule).

    * ``derivation`` — gating branch: ``round2(min(confidence, *dep final
      solidities))`` — the weakest link in the dependency cone; ``None``
      (pending) if confidence is pending or any claim dependency's *final*
      solidity is pending. Framework deps contribute 1.0 (never lower the min).
    * ``experimental`` — max-branch: ``max`` of ``strength`` over every
      ``run``-experiment ``strengthens`` edge into this claim; ``None`` if no
      run experiment strengthens it.
    * ``final`` — ``max`` over the non-None of ``{derivation, experimental}``;
      ``None`` (pending) iff BOTH are None.
    """

    derivation: float | None
    experimental: float | None
    final: float | None
    # The two INPUTS the derivation branch actually consumed, carried out of the
    # computation so the rendered trace can state what happened instead of
    # re-deriving a plausible-looking version of it:
    #
    # * ``local_quality`` — confidence AFTER any support lift. A trace printing
    #   the raw ``confidence`` renders a claim lifted 0.40 -> 0.90 as
    #   ``0.90 ... [= min(0.40, 0.90)]``: an equation whose own answer is 0.40.
    # * ``min_dep`` — the dep-gate minimum applied, or ``None`` when the entry
    #   has no dependency edges (no trace is emitted in that case).
    local_quality: float | None = None
    min_dep: float | None = None


def render_solidity_trace(result: SolidityResult) -> str:
    """Render the arithmetic trace for the branch the computation actually took.

    Returns the ``" [= ...]"`` suffix (leading space included) or ``""``.

    ``final`` is ``max(derivation, experimental)``, and the renderer must say
    which of those two produced it. Writing every trace as
    ``[= min(confidence, min_dep)]`` regardless renders an experimentally-rescued
    claim as ``0.80 ... [= min(0.50, 0.90)]`` — the max() branch that actually
    set the value left invisible, and the min() shown contradicting it.

    Supports have no experimental branch and no lift, so their trace is the
    plain weakest-link form; see :func:`render_min_trace`.
    """
    if result.final is None:
        return ""
    derivation, experimental = result.derivation, result.experimental
    if experimental is not None and (derivation is None or experimental > derivation):
        # The max branch set the value. Name both operands when there is a
        # derivation to lose to, and the rescue alone when there is not.
        if derivation is None:
            return f" [= experimental {format_solidity(experimental)}]"
        return f" [= max({format_solidity(derivation)}, {format_solidity(experimental)})]"
    return render_min_trace(result.local_quality, result.min_dep)


def render_min_trace(base: float | None, min_dep: float | None) -> str:
    """Render the weakest-link trace ``[= min(base, min_dep)]``, or ``""``.

    ``base`` is the value the min was actually taken over — a claim's
    post-lift ``local_quality``, a support's ``quality``. No dependency edges
    means no trace: solidity trivially equals the base.
    """
    if base is None or min_dep is None:
        return ""
    return f" [= min({format_solidity(base)}, {format_solidity(min_dep)})]"


class _SolidityResults(dict):
    """A ``{claim_id: SolidityResult}`` dict that also carries support solidities.

    Subclasses ``dict`` so every existing caller (which indexes by claim id and
    iterates ``.items()``) is unaffected, while exposing the computed
    ``sup_solidity`` map as a sidecar attribute. The single computation in
    :func:`compute_solidity_full` populates both, so the support record builder,
    the claim-quality write-back, and the verifier all read the SAME values —
    never re-deriving sup_solidity at a second call site (the dual-compute trap).
    """

    sup_solidity: dict[str, float | None]

    def __init__(self, results: dict | None = None) -> None:
        super().__init__(results or {})
        self.sup_solidity = {}


def _sup_solidity_from_deps(
    quality: float | None,
    depends_on,
    final_of,
) -> float | None:
    """Claim-style derivation of a support node's own solidity.

    ``round2(min(quality, *dependency final solidities))`` — the weakest link
    in the support's own dependency cone, NOT the product down the chain.
    Framework deps contribute 1.0 (never lower the min); a free-standing support
    (no deps) has ``sup_solidity == quality``. Returns ``None`` (pending) if
    ``quality`` is pending OR any claim dependency's final is pending — exactly
    the claim derivation rule, applied with the support's ``quality`` in place of
    a claim's ``confidence``.
    """
    if quality is None:
        return None
    dep_finals: list[float] = []
    for edge in depends_on:
        if edge.relation != "depends":
            continue
        if edge.target_kind == "claim":
            dep_final = final_of(edge.target)
            if dep_final is None:
                return None  # pending dep poisons the support's own solidity
            dep_finals.append(dep_final)
        else:
            dep_finals.append(1.0)
    if not dep_finals:
        return quality
    return round_half_up_2dp(min(quality, *dep_finals))


def _dependency_order(entries, sups, supporters) -> list[str]:
    """Kahn order over the combined claim + support graph, cycle-checked whole.

    **Every claim and every support is a node here, scored or not.** A cycle is
    a property of the edge set, and an unscored claim's edges are as real as a
    scored one's — so a walk that admitted only claims carrying a numeric
    ``confidence`` would leave :class:`SolidityCycleError` unraisable on a
    freshly built graph, every claim a build authors carrying ``*pending*``
    until a scoring pass runs. Scoring reads this order and computes a value for
    the nodes it has one for; which nodes those are decides nothing about which
    graphs are cyclic.

    Edges (X before Y): claim/support → each claim it depends on; claim → each
    support that supports it. Experiments are terminal and introduce none. An
    edge to an id no node in the graph keys is not an edge — a dangling target
    orders nothing and cannot close a cycle.
    """
    nodes = set(entries) | set(sups)
    indegree: dict[str, int] = {n: 0 for n in nodes}
    dependents: dict[str, list[str]] = {n: [] for n in nodes}

    def add_edge(before: str, after: str) -> None:
        # `after` cannot be processed until `before` is done.
        if before in nodes and after in nodes:
            indegree[after] += 1
            dependents[before].append(after)

    for cid, entry in entries.items():
        for edge in entry.depends_on:
            if edge.relation == "depends" and edge.target_kind == "claim":
                add_edge(edge.target, cid)
        for sup_id, _fraction in supporters.get(cid, ()):
            add_edge(sup_id, cid)
    for sid, sup in sups.items():
        for edge in sup.depends_on:
            if edge.relation == "depends" and edge.target_kind == "claim":
                add_edge(edge.target, sid)

    queue = sorted(n for n in nodes if indegree[n] == 0)
    order: list[str] = []
    while queue:
        node = queue.pop(0)
        order.append(node)
        for dep in sorted(dependents[node]):
            indegree[dep] -= 1
            if indegree[dep] == 0:
                queue.append(dep)
                queue.sort()

    if len(order) != len(nodes):
        raise SolidityCycleError(sorted(n for n in nodes if indegree[n] > 0))
    return order


def compute_solidity_full(claim_entries, experiments=(), supports=(), works=()) -> dict[str, SolidityResult]:
    """Compute derivation / experimental / final solidity for every claim.

    THE definitive solidity rule — this docstring and the code below it are the
    statement of it, with no prose second view anywhere. Includes the
    DERIVATION-branch lift from support nodes. One result per
    claim with a numeric ``confidence`` OR a non-pending lift source (a run
    experiment, or a support edge with a non-pending ``sup_solidity``). A claim
    with none is fully pending and omitted (treat absence as ``*pending*``).

    Algorithm:

    * **experimental_solidity[C]** = ``max`` of ``edge.strength`` over all
      ``relation:"strengthens"`` run-experiment edges into ``C``; ``None`` if
      none. Unrun experiments contribute nothing. The MAX/experimental branch.
    * **sup_solidity[S]** (computed in unified topo order — see below) =
      ``round2(min(quality, *S's dep finals))``; pending propagates. Each
      support feeds the DERIVATION branch of its beneficiaries, never the
      experimental/max branch.
    * **local_quality[C]** = ``max(confidence[C], max over supporting sups S of
      sup_solidity[S] × f)``, where a pending ``sup_solidity`` OR a pending
      on-point fraction ``f`` (``PENDING_FRACTION``) is EXCLUDED from the max (no
      NaN, no poison). A support lift never drags a beneficiary with
      otherwise-valid quality to pending — pending flows ONLY from a claim's own
      load-bearing ``depends-on``. NOTE: the support on-point fraction ``f`` is a
      single edge-weight relevance discount (``sup_solidity × f``), NOT a chain,
      so it stays multiplicative — it is not subject to the granularity argument
      that motivates the min dep-gating below.
    * **derivation_solidity[C]** = ``round2(min(local_quality[C], *C's claim-dep
      finals, *C's work-gate strengths))`` — the weakest link in C's dependency
      cone, framework deps 1.0;
      ``None`` if ``local_quality`` is pending (confidence pending AND no support
      lift) OR any claim-dep's final is pending. So a support lift is still
      dep-gated — it does NOT bypass C's own deps the way an experiment's
      max-branch does. The dep-gate is a pure ``min`` (weakest link), not a
      product: solidity is refactor-invariant — splitting one derivation step
      into two same-quality steps must not lower it, and a deep clean chain must
      not decay toward 0 as a bookkeeping artifact (ordinal grades are not
      independent probabilities to be multiplied).
    * **final[C]** = ``max`` over the non-None of ``{derivation, experimental}``;
      ``None`` iff both are None.

    The traversal is :func:`_dependency_order`'s, over **every** claim and
    support: a claim/support is processed after every claim it depends on (so
    dep finals are known) and a claim after every support that supports it (so
    the lift is known), and a cycle anywhere in that graph raises
    :class:`SolidityCycleError` before a single value is computed. Which claims
    carry a value is decided here, afterwards, and decides nothing about which
    graphs raise. Experiments are terminal and introduce no cycles.

    **A ``rests-on`` edge joins the same ``min``, with the pairing's
    applicability deciding membership and the work's ``strength`` deciding the
    gate.** For each ``relation == "rests-on"`` edge out of claim ``C``:

    * ``fraction == 0.0`` — the work bears nothing on this claim, so the pairing
      gates nothing and is skipped before ``strength`` is even read.
    * ``fraction`` numeric and ``> 0`` — the target work's hand-authored
      ``strength`` enters ``C``'s dep-gate ``min`` exactly as a claim dep's final
      does. ``strength 0.0`` therefore gates ``C`` to ``0.0``: citing work judged
      spurious must be able to hurt.
    * ``fraction`` pending (``PENDING_FRACTION``) or absent (``None``), or the
      work's ``strength`` pending / the work absent from ``works`` — ``C``'s
      derivation is pending, poisoning exactly as a pending ``depends`` target
      does. Pending is the safe direction: a claim resting on unjudged outside
      work genuinely has unknown solidity.

    ``fraction`` is read BEFORE ``strength`` and compared explicitly: ``None``,
    ``PENDING_FRACTION`` and ``0.0`` are three distinct states that a falsiness
    test would collapse into one wrong answer. The gate is *membership times
    threshold*, never ``strength × fraction`` — that product runs the wrong way
    under a ``min`` (it makes the gate harsher as applicability falls, so a work
    judged sound-but-irrelevant would annihilate the claim), and any arithmetic
    that discounts toward 1.0 instead both treats ordinal bands as probabilities
    and produces a ``min`` operand written on no disk anywhere. The consequence
    accepted: applicability ``0.3`` and ``1.0`` gate identically.

    A work absent from ``works`` and a work present with ``strength is None``
    differ in intent and not in outcome — both are pending. This computation
    does NOT raise on the dangling target: ``refresh`` runs it before
    :func:`_assert_work_node_coverage`, which is the check that names that
    defect.

    With ``supports=()`` this reduces exactly to the prior behavior:
    ``local_quality[C] == confidence[C]`` and the support machinery is inert.
    With ``works=()`` every ``rests-on`` edge is pending-by-absent-strength,
    which is what a caller holding no register — ``attribute.check_acyclic``,
    walking synthetic claim-to-claim entries — needs and gets.
    """
    entries = {e.id: e for e in claim_entries}
    sups = {s.id: s for s in supports}
    strength_of = {w.id: w.strength for w in works}

    # --- experimental_solidity[C]: max strength over run-experiment edges ---
    experimental: dict[str, float] = {}
    for exp in experiments:
        if exp.status != "run":
            continue
        for claim_id, strength in exp.strengthens:
            prev = experimental.get(claim_id)
            if prev is None or strength > prev:
                experimental[claim_id] = strength

    # --- supporters[C]: list of (sup_id, fraction) supporting claim C ---
    supporters: dict[str, list[tuple[str, float]]] = {}
    for sup in supports:
        for claim_id, fraction in sup.supports:
            supporters.setdefault(claim_id, []).append((sup.id, fraction))

    # The whole graph is walked, and the cycle check is that walk's; a claim
    # carries a computed result only where there is something to compute one
    # from — a numeric confidence, or a support lifting it (its derivation may
    # be authored from a support with confidence still pending). Every other
    # claim is traversed and skipped below.
    order = _dependency_order(entries, sups, supporters)
    scored_claims = {eid for eid, e in entries.items() if e.confidence is not None} | (set(supporters) & set(entries))

    results: dict[str, SolidityResult] = {}
    final: dict[str, float | None] = {}
    sup_solidity: dict[str, float | None] = {}

    # An unscored claim (pending confidence, no support lift) may still be
    # experimentally rescued; seed its final from the max-branch so a numeric
    # claim depending on a rescued pending-confidence claim sees the rescued
    # final. (Its derivation is always pending — no order needed.)
    for eid in entries:
        if eid in scored_claims:
            continue
        exp_sol = experimental.get(eid)
        results[eid] = SolidityResult(None, exp_sol, exp_sol)
        final[eid] = exp_sol

    def _final_of(claim_id: str) -> float | None:
        return final.get(claim_id)

    for node in order:
        if node in sups:
            sup = sups[node]
            sup_solidity[node] = _sup_solidity_from_deps(sup.quality, sup.depends_on, _final_of)
            continue
        if node not in scored_claims:
            continue  # walked for the cycle check; its result was seeded above

        entry = entries[node]
        # local_quality: max(confidence, max over supporting sups of
        # sup_solidity × f), pending sup excluded (no poison). Confidence
        # pending contributes nothing to the max.
        local_quality: float | None = entry.confidence
        for sup_id, fraction in supporters.get(node, ()):
            if fraction is PENDING_FRACTION:
                continue  # pending fraction — excluded from the max (no poison)
            s_sol = sup_solidity.get(sup_id)
            if s_sol is None:
                continue  # pending support — excluded from the max
            lift = s_sol * fraction
            if local_quality is None or lift > local_quality:
                local_quality = lift

        # derivation: min(local_quality, claim-dep finals, work-gate strengths) —
        # the weakest link in the dependency cone, framework deps 1.0 (never
        # lower the min).
        dep_finals: list[float] = []
        derivation: float | None
        pending = local_quality is None
        if not pending:
            for edge in entry.depends_on:
                if edge.relation == "rests-on":
                    # `fraction` decides WHETHER the pairing gates, `strength`
                    # how hard. Read in that order, and compared identity-first:
                    # None / PENDING_FRACTION / 0.0 are three states.
                    if edge.fraction == 0.0:
                        continue  # bears nothing on this claim — gates nothing
                    if edge.fraction is None or edge.fraction is PENDING_FRACTION:
                        pending = True
                        break
                    strength = strength_of.get(edge.target)
                    if strength is None:
                        pending = True  # unjudged work, or none on the register
                        break
                    dep_finals.append(strength)
                    continue
                if edge.relation != "depends":
                    continue
                if edge.target_kind == "claim":
                    dep_final = _final_of(edge.target)
                    if dep_final is None:
                        pending = True
                        break
                    dep_finals.append(dep_final)
                else:
                    dep_finals.append(1.0)
        # The dep-gate minimum this claim's derivation was actually taken over,
        # kept so the trace renders the computation's own inputs rather than a
        # second derivation at write time (the dual-compute trap).
        min_dep = min(dep_finals) if (not pending and dep_finals) else None
        if pending:
            derivation = None
        elif dep_finals:
            derivation = round_half_up_2dp(min(local_quality, *dep_finals))
        else:
            # Rounded on this branch too. `local_quality` is not always an
            # authored 2dp value — a support lift is `sup_solidity * fraction`,
            # a raw float product — so without it a dependency-FREE lifted claim
            # carries an unrounded solidity while the same claim with any
            # dependency carries a rounded one. 0.2 x 0.98 writes 0.196 into
            # claims.jsonl and renders "0.20" in the markdown, which puts the
            # two on opposite sides of a band threshold and makes `verify` FAIL
            # "refresh-fixable" at 1e-9 while `refresh` reports 0 changes: a
            # permanent hard block on a legal KB shape.
            derivation = round_half_up_2dp(local_quality)
        exp_sol = experimental.get(node)
        fin = _max_nonnull(derivation, exp_sol)
        final[node] = fin
        results[node] = SolidityResult(derivation, exp_sol, fin, local_quality, min_dep)

    # Expose support solidities via a private sidecar attribute on the result
    # dict so callers that need them (record builder, write-back, verifier) read
    # the SAME computed values — no second derivation (the dual-compute trap).
    results_sidecar = _SolidityResults(results)
    results_sidecar.sup_solidity = dict(sup_solidity)
    return results_sidecar


def _max_nonnull(a: float | None, b: float | None) -> float | None:
    """Return the max of the non-None values; None iff both are None."""
    if a is None:
        return b
    if b is None:
        return a
    return max(a, b)


def compute_solidity(claim_entries, experiments=(), supports=(), works=()) -> dict[str, float]:
    """Compute the derived FINAL ``solidity`` for every scorable claim.

    Backward-compatible thin wrapper over :func:`compute_solidity_full`:
    returns ``{claim_id: final_solidity}`` for every claim whose final solidity
    is non-pending (numeric). A claim with a pending final (``*pending*``) is
    OMITTED from the mapping — every consumer treats "absent" identically to
    "pending". With zero experiments, ``final == derivation`` for every claim,
    so this returns exactly the historical min-branch result.

    ``solidity = round_half_up_2dp(min(confidence, *dependency solidities))``,
    computed bottom-up over the claim depends-on DAG (Kahn topological sort):

    * A claim's dependencies are its ``depends-on`` edges. A ``claim``-target
      edge contributes that dependency's already-computed (and already-rounded)
      solidity; an ``invariant`` / ``axiom`` edge contributes ``1.0``
      (framework bedrock — solidity-1.0 by definition, never lowers the min).
    * A claim with no depends-on edges has ``solidity = confidence``.
    * The dep-gate is the *weakest link* (``min``) over the claim's own
      local quality and its dependency finals — NOT the product down the chain.
      This makes solidity refactor-invariant: splitting one derivation step
      into two same-quality steps leaves it unchanged, and a deep clean chain
      does not decay toward 0 as a granularity artifact (ordinal confidence
      grades are not independent probabilities to be multiplied).
    * Propagation uses each dependency's *rounded* solidity, so a human
      auditing ``min(0.85, 0.41, 0.28)`` against the written values gets the
      same answer the tool does.

    HARD RULE — ``*pending*`` propagates transitively, exactly like NaN
    through arithmetic. A claim's solidity is ``*pending*`` (undefined) if its
    ``confidence`` is ``*pending*`` (parsed as ``None`` — not yet quality
    assessed) OR any of its dependencies' solidity is ``*pending*``,
    REGARDLESS of the claim's own local ``confidence``. A claim with
    ``confidence: 1.0`` that depends on one pending claim still has a pending
    solidity. Framework-node dependencies (invariant / axiom targets) are
    never pending — they are solidity-1.0 bedrock by definition, so a claim
    that depends only on framework nodes is NOT pending (its solidity equals
    its confidence).

    A claim with a pending solidity is OMITTED from the returned mapping: the
    dict contains an entry only for claims with a fully-computable numeric
    solidity. Every consumer must treat "absent from this result" identically
    to "pending" — render/record it as ``*pending*`` / ``null``. The current
    KB is a closed subgraph (no numeric-confidence claim depends on a pending
    claim), so the blocked-by-pending-dependency path is dormant; it activates
    the first time a volume is assessed while a volume it depends on is still
    pending.

    Returns ``{claim_id: solidity}`` for every claim with a computable
    solidity. Raises :class:`SolidityCycleError` if the depends-on graph
    contains a cycle — over every claim, including the ones this mapping omits
    as pending, because a cycle is a property of the edges and not of the
    scores (:func:`_dependency_order`).
    """
    full = compute_solidity_full(claim_entries, experiments, supports, works)
    return {cid: r.final for cid, r in full.items() if r.final is not None}


def compute_support_solidity(claim_entries, experiments=(), supports=(), works=()) -> dict[str, float]:
    """Compute the FINAL ``sup_solidity`` for every scorable support node.

    Reads the SAME single computation as :func:`compute_solidity` /
    :func:`compute_solidity_full` (no second derivation): returns
    ``{sup_id: sup_solidity}`` for every support whose solidity is non-pending.
    A support with a pending solidity (pending ``quality`` OR a pending claim
    dependency) is OMITTED — every consumer treats "absent" identically to
    "pending", exactly as for a pending claim.
    """
    full = compute_solidity_full(claim_entries, experiments, supports, works)
    return {sid: sol for sid, sol in full.sup_solidity.items() if sol is not None}


def min_dependency_solidity(entry: ClaimEntry, solidity: dict[str, float]) -> float | None:
    """Return the minimum dependency solidity feeding ``entry``.

    A ``claim``-target edge contributes the dependency's computed solidity
    (from ``solidity``); an ``invariant`` / ``axiom`` edge contributes ``1.0``.
    Returns ``None`` when ``entry`` has no depends-on edges (``solidity``
    trivially equals ``confidence`` — no arithmetic trace) or when any claim
    dependency is itself uncomputable (so the minimum is undefined).

    This is the binding dep term ``refresh_kb_metadata`` writes into the
    ``[= min(<confidence>, <min-dep-solidity>)]`` trace on the solidity line.
    """
    dep_solidities: list[float] = []
    for edge in entry.depends_on:
        if edge.relation != "depends":
            continue
        if edge.target_kind == "claim":
            if edge.target not in solidity:
                return None
            dep_solidities.append(solidity[edge.target])
        else:
            dep_solidities.append(1.0)
    if not dep_solidities:
        return None
    return min(dep_solidities)


# ---------------------------------------------------------------------------
# Record builders
# ---------------------------------------------------------------------------


def build_claims_records(state: KbState) -> list[dict]:
    """One record per graph node, sorted by ``(node_type, id)``.

    ``claims.jsonl`` holds a type-tagged union over
    :data:`kb_schema.NODE_KINDS`, discriminated by ``node_type``. Each kind's
    record shape differs, which is why each is emitted by its own branch below:

    * ``claim`` records carry the full 15-field shape (``node_type`` first,
      then the 14 claim fields, including ``derivation_solidity`` and
      ``experimental_solidity`` before ``solidity``).
    * ``support`` records carry the five identifying fields plus ``quality``
      and the ``solidity`` the shared computation derived for them.
    * ``experiment`` records are minimal — six fields (``node_type``, ``id``,
      ``title``, ``canonical_path``, ``canonical_anchor``, ``status``).
    * ``invariant`` / ``axiom`` records are minimal — exactly the five
      identifying fields. Framework nodes are solidity-1.0 by definition, so
      they carry no scoring fields.
    * ``work`` records carry the five identifying fields plus ``strength``.

    The sort key ``(node_type, id)`` groups the kinds in ASCII order of the
    discriminator, which is the file's own order and not the vocabulary's.

    Claim counts (depends_on_count, strengthen_by_count, citation_count) are
    derived from the same state so they're internally consistent with the
    other record files this module emits. ``depends_on_count`` counts only
    ``relation:"depends"`` edges.

    ``derivation_solidity`` / ``experimental_solidity`` / ``solidity`` /
    ``build_status`` / ``build_band`` are **derived** — computed by
    :func:`compute_solidity_full` from the hand-authored ``confidence`` values,
    the depends-on DAG, and run-experiment strengthens edges, NOT re-parsed
    from the claim-quality.md ``solidity`` line. ``build_status`` / ``build_band``
    derive from the FINAL solidity. A claim whose final solidity is pending
    carries ``null`` for ``solidity`` / ``build_status``.
    """
    # Citation counts derived from leaves once.
    cite_counts: dict[str, int] = {}
    for leaf in state.leaves:
        for cid in leaf.claims:
            cite_counts[cid] = cite_counts.get(cid, 0) + 1

    # Solidity is the single derived computation — shared with the
    # claim-quality.md write-back; never computed twice. It carries both the
    # per-claim results AND the per-support sup_solidity sidecar.
    full = compute_solidity_full(state.claim_entries, state.experiments, state.supports, state.works)

    out: list[dict] = []
    for entry in state.claim_entries:
        result = full.get(entry.id)
        derivation = result.derivation if result else None
        experimental = result.experimental if result else None
        final = result.final if result else None
        depends_count = sum(1 for e in entry.depends_on if e.relation == "depends")
        out.append(
            {
                "node_type": "claim",
                "id": entry.id,
                "title": entry.title,
                "canonical_path": entry.canonical_path,
                "canonical_anchor": entry.canonical_anchor,
                "confidence": entry.confidence,
                "derivation_solidity": derivation,
                "experimental_solidity": experimental,
                "solidity": final,
                "build_status": build_status_phrase(final),
                "build_band": derive_build_band(final),
                "rationale": entry.rationale,
                "depends_on_count": depends_count,
                "strengthen_by_count": len(entry.strengthen_by),
                "citation_count": cite_counts.get(entry.id, 0),
            }
        )
    for exp in state.experiments:
        out.append(
            {
                "node_type": "experiment",
                "id": exp.id,
                "title": exp.title,
                "canonical_path": exp.canonical_path,
                "canonical_anchor": exp.canonical_anchor,
                "status": exp.status,
            }
        )
    for sup in state.supports:
        # A support node's own computed solidity comes from the SAME shared
        # computation (sup_solidity sidecar) — never re-derived here.
        out.append(
            {
                "node_type": "support",
                "id": sup.id,
                "title": sup.title,
                "canonical_path": sup.canonical_path,
                "canonical_anchor": sup.canonical_anchor,
                "quality": sup.quality,
                "solidity": full.sup_solidity.get(sup.id),
            }
        )
    for node in state.framework_nodes:
        out.append(
            {
                "node_type": node.node_type,
                "id": node.id,
                "title": node.title,
                "canonical_path": node.canonical_path,
                "canonical_anchor": node.canonical_anchor,
            }
        )
    for work in state.works:
        # Six fields: the five identifying ones plus `strength`, the work's own
        # standing. There is no solidity field and no band: an external work is
        # not scored on this corpus's ladder, and a null here would read as an
        # unassessed one rather than as an inapplicable one.
        out.append(
            {
                "node_type": "work",
                "id": work.id,
                "title": work.title,
                "canonical_path": work.canonical_path,
                "canonical_anchor": work.canonical_anchor,
                "strength": work.strength,
            }
        )
    out.sort(key=lambda r: (r["node_type"], r["id"]))
    return out


def build_depends_on_records(state: KbState) -> list[dict]:
    """One record per forward graph edge — every class, ``references`` included.

    Field order per SCHEMA: ``source``, ``target``, ``relation``,
    ``target_kind``, ``target_solidity_recorded``, ``strength``, ``context``,
    ``fraction``. Every record carries all eight keys (schema closure); only the
    relevant ones are non-null per edge class.

    * ``depends`` edges come from claim (and now SUPPORT) Quality sections —
      ``strength:null``, ``fraction:null``. A support's own deps are ``depends``
      edges with ``source`` the sup-id.
    * ``strengthens`` edges come from each experiment leaf's ``strengthens:``
      block (``source: exp-id``, ``target: clm-id``, ``strength:<value>``,
      ``fraction:null``).
    * ``supports`` edges come from each support leaf's ``supports:`` block
      (``source: sup-id``, ``target: clm-id``, ``relation:"supports"``,
      ``target_kind:"claim"``, ``strength:null``, ``fraction:<f>`` where
      f ∈ [0, 1], OR ``fraction:"*pending*"`` for an unassessed edge). The
      pending-fraction literal is distinct on disk from a depends edge's
      ``fraction:null``. The DERIVATION-branch analog of a ``strengthens`` edge.
    * ``rests-on`` edges come from a claim entry's own depends-on bullets, the
      ones whose target is a ``work-`` id (``source: clm-id``,
      ``target: work-<key>``, ``target_kind:"work"``, ``strength:null``,
      ``fraction`` the pairing's applicability or the pending literal). They
      gate: an applicability above zero puts the work's ``strength`` into the
      source's dependency ``min``, a zero one takes the pairing out of it, and
      either left unsupplied keeps the source pending.
    * ``references`` edges come from a claim entry's ``- references:`` bullets
      (``source`` and ``target`` both ``clm-`` ids, ``target_kind:"claim"``,
      ``strength:null``, ``fraction:null``). They record a cross-reference the
      corpus states and gate nothing: no solidity computation reads them, and
      the acyclicity gate is over ``depends`` alone, two claims naming each
      other being the author's argument rather than circular reasoning.

    Sorted by ``(source, target, context)`` — a null context sorts as the empty
    string — so two edges from one source to one target with different context
    notes stay deterministically ordered.
    """
    edges: list[dict] = []

    def _emit(
        source,
        target,
        relation,
        target_kind,
        target_solidity_recorded=None,
        strength=None,
        context=None,
        fraction=None,
    ):
        edges.append(
            {
                "source": source,
                "target": target,
                "relation": relation,
                "target_kind": target_kind,
                "target_solidity_recorded": target_solidity_recorded,
                "strength": strength,
                "context": context,
                # The pending sentinel is an in-memory object; on disk it is the
                # literal, so an unassessed fraction stays distinguishable from
                # a class that carries none. Converted here rather than at each
                # call site, so no edge class can reach the file holding it.
                "fraction": PENDING_LITERAL if fraction is PENDING_FRACTION else fraction,
            }
        )

    for entry in state.claim_entries:
        for edge in (*entry.depends_on, *entry.references):
            _emit(
                edge.source,
                edge.target,
                edge.relation,
                edge.target_kind,
                edge.target_solidity_recorded,
                edge.strength,
                edge.context,
                edge.fraction,
            )
    for sup in state.supports:
        # A support's OWN dependencies — depends edges sourced at the sup-id.
        for edge in sup.depends_on:
            _emit(
                edge.source,
                edge.target,
                edge.relation,
                edge.target_kind,
                edge.target_solidity_recorded,
                edge.strength,
                edge.context,
                edge.fraction,
            )
        # A support's beneficiary fan-out — supports edges sourced at the sup-id.
        # A pending on-point fraction materializes as the literal "*pending*",
        # distinct on disk from a depends edge's null fraction.
        for claim_id, fraction in sup.supports:
            _emit(
                sup.id,
                claim_id,
                "supports",
                "claim",
                fraction=fraction,
            )
    for exp in state.experiments:
        for claim_id, strength in exp.strengthens:
            _emit(
                exp.id,
                claim_id,
                "strengthens",
                "claim",
                strength=strength,
            )
    edges.sort(key=lambda r: (r["source"], r["target"], r["context"] or ""))
    return edges


def build_strengthen_by_records(state: KbState) -> list[dict]:
    """One record per strengthen-by item, sorted by (claim_id, item_idx).

    item_idx is 0-indexed within each claim. Records are emitted in the
    original bullet order so item_idx is contiguous within each claim.
    """
    items: list[dict] = []
    for entry in state.claim_entries:
        for sb in entry.strengthen_by:
            items.append(
                {
                    "claim_id": sb.claim_id,
                    "item_idx": sb.item_idx,
                    "text": sb.text,
                    "mentioned_ids": list(sb.mentioned_ids),
                }
            )
    items.sort(key=lambda r: (r["claim_id"], r["item_idx"]))
    return items


def build_supported_by_records(state: KbState) -> list[dict]:
    """One record per (claim, supporting support) edge — the reverse view.

    The ``supported-by`` view answers "which support nodes lift claim X, and by
    how much?" — analogous to ``strengthen-by`` as untraversed bookkeeping.
    It is NOT consulted for solidity (that flows forward
    through the ``supports`` edges in ``depends-on.jsonl``); it is a convenience
    reverse index. Each record carries the on-point ``fraction`` and the
    support's computed ``sup_solidity`` (the SAME shared computation; pending =>
    ``null``) so a reviewer sees the realized lift contribution at a glance.

    Sorted by ``(claim_id, sup_id)``.
    """
    sup_solidity = compute_solidity_full(
        state.claim_entries, state.experiments, state.supports, state.works
    ).sup_solidity
    rows: list[dict] = []
    for sup in state.supports:
        for claim_id, fraction in sup.supports:
            emitted_fraction = PENDING_LITERAL if fraction is PENDING_FRACTION else fraction
            rows.append(
                {
                    "claim_id": claim_id,
                    "sup_id": sup.id,
                    "fraction": emitted_fraction,
                    "sup_solidity": sup_solidity.get(sup.id),
                }
            )
    rows.sort(key=lambda r: (r["claim_id"], r["sup_id"]))
    return rows


def build_cites_records(state: KbState) -> list[dict]:
    """One record per (claim, leaf) edge, sorted by (claim_id, leaf_path)."""
    rows: list[dict] = []
    for leaf in state.leaves:
        for cid in leaf.claims:
            rows.append(
                {
                    "claim_id": cid,
                    "leaf_path": leaf.path,
                    "leaf_kind": leaf.kind,
                    "tier2_marked": cid in leaf.tier2_marked,
                }
            )
    rows.sort(key=lambda r: (r["claim_id"], r["leaf_path"]))
    return rows


# ---------------------------------------------------------------------------
# Leaf-references footer (derived field on claim-quality.md entries)
# ---------------------------------------------------------------------------
#
# Each canonical entry in a claim-quality.md register carries a
# ``> **Leaf references:**`` blockquote footer — the reverse-citation map of
# which leaves host the entry's id. It is a *derived* field: regenerated by
# ``refresh_kb_metadata`` and drift-gated by ``verify_kb_metadata``, exactly
# like ``subtree-claims`` and the derived ``solidity`` line. The reverse map
# below is THE single computation both consume (no dual-compute drift).

# The literal that opens the footer blockquote. The full footer is a single
# blockquote line beginning with this prefix; refresh finds-and-replaces it as
# one stable region (or inserts it before the entry's `### Quality` heading).
# Single-sourced in kb_schema beside the empty-case footer it opens, so the
# write API's inserted footer and this one are the same bytes by construction
# rather than by two literals kept equal.
LEAF_REFERENCES_PREFIX = kb_schema.LEAF_REFERENCES_PREFIX


def build_leaf_references(state: KbState) -> dict[str, list[str]]:
    """Reverse-citation map ``{node_id: [citing leaf paths]}`` for every entry.

    The footer of a ``clm-`` / ``exp-`` / ``sup-`` claim-quality entry lists the
    leaves whose frontmatter hosts that id — fully derivable from leaf metadata:

    * ``clm-`` — every leaf whose ``claims:`` frontmatter lists the id (the same
      leaf→claim edges materialized in ``cites.jsonl``).
    * ``exp-`` — the experiment's canonical home (the leaf hosting the
      ``exp-id:``) plus every leaf that REFERENCES it via ``experiments:``.
    * ``sup-`` — the support's canonical home (the leaf hosting the ``sup-id:``).

    Each id's list is de-duplicated and stable-sorted by POSIX leaf path. An id
    cited by exactly one leaf yields a one-element list (the common case); a
    multi-leaf id lists all. The bidirectional-coverage check guarantees every
    canonical entry is cited by ≥ 1 leaf, so a normal entry never has an empty
    list — but an id absent from this map (a defect) simply yields no entry, and
    the caller decides how to render that (see :func:`render_leaf_references`).
    """
    refs: dict[str, set[str]] = {}
    for leaf in state.leaves:
        for cid in leaf.claims:
            refs.setdefault(cid, set()).add(leaf.path)
        for eid in leaf.experiments_ref:
            refs.setdefault(eid, set()).add(leaf.path)
    for exp in state.experiments:
        refs.setdefault(exp.id, set()).add(exp.canonical_path)
    for sup in state.supports:
        refs.setdefault(sup.id, set()).add(sup.canonical_path)
    return {node_id: sorted(paths) for node_id, paths in refs.items()}


def render_leaf_references(register_path: str, leaf_paths: list[str]) -> str:
    """Render the canonical ``> **Leaf references:**`` footer line.

    ``register_path`` is the POSIX path (relative to kb-root) of the
    ``claim-quality.md`` register the footer lives in; ``leaf_paths`` are the
    kb-root-relative citing leaf paths. Each leaf is rendered as a real
    Markdown link ``[<name>](./<rel>)`` where ``<rel>`` is the leaf path
    relative to the register's own directory and ``<name>`` is the leaf
    filename stem (no backticks) — so the footer links are link-checkable by
    ``verify_md_links`` (a dead path now gates instead of rotting silently).
    The list is comma-joined in the given (stable-sorted) order, ending with a
    period. All free-text editorial annotations are dropped (the rot the
    derived footer eliminates).

    An empty ``leaf_paths`` renders
    :data:`kb_schema.LEAF_REFERENCES_PENDING_FOOTER` so the defect is visible in
    the file rather than silently producing a bare prefix. That constant is
    returned rather than re-composed here because the write API renders the same
    footer into every entry it inserts: returning the object makes the two the
    same bytes by construction, not by a literal kept equal.
    """
    if not leaf_paths:
        return kb_schema.LEAF_REFERENCES_PENDING_FOOTER
    register_dir = PurePosixPath(register_path).parent
    links: list[str] = []
    for leaf_path in leaf_paths:
        rel = _posix_path_relative_to(leaf_path, register_dir)
        name = PurePosixPath(rel).stem
        target = rel if rel.startswith(("../", "/")) else f"./{rel}"
        links.append(f"[{name}]({target})")
    return f"{LEAF_REFERENCES_PREFIX} {', '.join(links)}."


def _posix_path_relative_to(leaf_path: str, register_dir: PurePosixPath) -> str:
    """Return ``leaf_path`` expressed as a true relative path from ``register_dir`` (POSIX).

    Leaves under the register's directory render cleanly (``dynamics/x.md``);
    cross-directory citations — a per-volume register cited by a ``common/``
    leaf, or any cross-volume citation — climb with ``../``
    (``../common/operators.md``). The KB root register (``claim-quality.md``,
    dir ``.``) yields the full kb-root-relative path. The result is always a
    valid link target relative to the register file's own location.
    """
    base = str(register_dir)
    if base in ("", "."):
        return PurePosixPath(leaf_path).as_posix()
    return posixpath.relpath(leaf_path, base)


@dataclass(frozen=True)
class EntryFooterBand:
    """Where one register entry's leaf-references footer lives — or would.

    Line indices are into the register's raw ``text.split("\\n")``. Fence
    scrubbing preserves line count, so an index computed on scrubbed lines
    addresses the same physical line in the raw ones.
    """

    body_start: int  # the line AFTER the `<!-- id: ... -->` marker
    quality_start: int  # the `### Quality` heading line
    footer_line: int | None  # an existing footer's line, or None if absent


def locate_leaf_reference_footers(text: str) -> dict[str, EntryFooterBand]:
    """Locate every entry's leaf-references footer band in one register.

    THE locator for the derived ``> **Leaf references:**`` footer: the emitter
    (``refresh_kb_metadata``) and the checker (``verify_kb_metadata``) both call
    this, so they cannot reach different conclusions about where a footer is or
    whether one exists.

    The trap this closes: refresh searching the RAW lines for the footer prefix
    while verify searches the FENCE-SCRUBBED ones makes a
    ``> **Leaf references:**`` line inside a fenced example in the footer band
    the entry's footer as far as refresh is concerned — which it overwrites with
    real generated links, corrupting the documentation example — while verify,
    correctly not seeing it, reports the real footer ``(missing)``. refresh then
    reports 0 changes on every subsequent run: a verify FAIL no run of refresh
    could ever clear.

    Everything here is decided on scrubbed lines, so a fenced example is never
    a footer to either side. An entry with no ``### Quality`` section is
    omitted (the quality-block-integrity check reports that separately).
    """
    scrubbed = _strip_code_fences(text).splitlines()
    bands: dict[str, EntryFooterBand] = {}
    for i, line in enumerate(scrubbed):
        marker = _CANONICAL_ANY_ID_RE.match(line.strip())
        if marker is None:
            continue
        node_id = marker.group(1)
        quality_start: int | None = None
        for j in range(i + 1, len(scrubbed)):
            if scrubbed[j].strip() == "### Quality":
                quality_start = j
                break
            # The next `## ` H2 is a sibling entry's title; an H3 `### Quality`
            # does not start with `## `.
            if scrubbed[j].startswith("## "):
                break
        if quality_start is None:
            continue
        body_start = i + 1
        footer_line = None
        for idx in range(body_start, quality_start):
            if scrubbed[idx].startswith(LEAF_REFERENCES_PREFIX):
                footer_line = idx
                break
        bands[node_id] = EntryFooterBand(body_start, quality_start, footer_line)
    return bands


def _is_under(leaf_path: Path, idx_dir: Path) -> bool:
    """True if ``leaf_path`` lies within ``idx_dir`` (the index's directory)."""
    try:
        leaf_path.relative_to(idx_dir)
        return True
    except ValueError:
        return False


def compute_subtree_aggregates(
    state: KbState,
) -> dict[str, tuple[list[str], list[str]]]:
    """THE single computation of every index/entry-point subtree aggregate.

    Returns ``{node_path: (subtree_claims, subtree_experiments)}`` with both
    lists sorted. This is the one place either aggregate is derived; refresh
    (emitter) and verify (checker) both consume this same function from the
    same :class:`KbState`, so the two cannot drift (the dual-compute trap).

    * ``subtree_claims`` — union of OWNED leaf ``claims`` under the node's
      directory (a leaf's foreign depends-on references do not roll up).
    * ``subtree_experiments`` — union of exp-ids OWNED (declared via
      ``exp-id:``) by experiment leaves under the node's directory. OWNED-ONLY:
      a leaf's ``experiments:`` REFERENCES never propagate here, exactly as a
      leaf's foreign claim references never enter ``subtree_claims``.

    A ``kind: entry-point`` node aggregates the whole KB; a ``kind: index``
    node aggregates everything under its own directory.
    """
    leaf_claims = [(Path(leaf.path), leaf.claims) for leaf in state.leaves]
    exp_paths = [(Path(exp.canonical_path), exp.id) for exp in state.experiments]

    out: dict[str, tuple[list[str], list[str]]] = {}
    for idx in state.indexes:
        idx_dir = Path(idx.path).parent
        is_ep = idx.kind == "entry-point"
        claims: set[str] = set()
        experiments: set[str] = set()
        for leaf_path, ids in leaf_claims:
            if is_ep or _is_under(leaf_path, idx_dir):
                claims.update(ids)
        for exp_path, exp_id in exp_paths:
            if is_ep or _is_under(exp_path, idx_dir):
                experiments.add(exp_id)
        out[idx.path] = (sorted(claims), sorted(experiments))
    return out


def build_subtree_aggregate_records(state: KbState) -> list[dict]:
    """One record per index/entry-point node, sorted by node_path.

    Both ``subtree_claims`` and ``subtree_experiments`` come from the single
    shared :func:`compute_subtree_aggregates` so the materialized JSONL cannot
    diverge from what the frontmatter refresh and the verify check derive.
    ``subtree_experiments`` is owned-only (see that function).
    """
    aggregates = compute_subtree_aggregates(state)
    rows: list[dict] = []
    for idx in state.indexes:
        subtree_claims, subtree_experiments = aggregates[idx.path]
        rows.append(
            {
                "node_path": idx.path,
                "node_kind": idx.kind,
                "subtree_claims": subtree_claims,
                "subtree_experiments": subtree_experiments,
            }
        )
    rows.sort(key=lambda r: r["node_path"])
    return rows


def _assert_framework_node_coverage(claims_records: list[dict], depends_on_records: list[dict]) -> None:
    """Fail loudly if any depends-on edge targets a framework node that is not
    present in the rebuilt claims records.

    Guards the silent-drop failure mode: if ``parse_framework_nodes`` yields
    fewer axiom/invariant nodes than the claim graph references (a malformed
    ``CLAUDE.md`` state), the index would be written with dangling edges. We
    catch it at build time with an actionable message instead.
    """
    present = {r["id"] for r in claims_records if r["node_type"] in kb_schema.FRAMEWORK_KINDS}
    referenced = {e["target"] for e in depends_on_records if e.get("target_kind") in kb_schema.FRAMEWORK_KINDS}
    missing = sorted(referenced - present)
    if not missing:
        return
    yielded = " + ".join(
        f"{sum(1 for r in claims_records if r['node_type'] == kind)} {kind}" for kind in kb_schema.FRAMEWORK_KINDS
    )
    raise FrameworkNodeParseError(
        f"{len(missing)} depends-on edge target(s) reference framework nodes "
        f"absent from the rebuilt index: {', '.join(missing)}.\n"
        f"parse_framework_nodes() yielded {yielded} node(s) from the KB's "
        f"CLAUDE.md (kb-root/CLAUDE.md).\n"
        f"This is the silent framework-node drop (issue #28): the CLAUDE.md "
        f"INVARIANT-S2 axiom bullets and/or '### INVARIANT-*' headings did not "
        f"parse. Axiom bullets must match '- Axiom N: **Title** — ...' at "
        f"line start (no leading indent, no merge-conflict markers); invariants "
        f"need '### INVARIANT-XNN: <title>' headings. Fix CLAUDE.md and re-run "
        f"(refresh aborted before writing a dangling index)."
    )


class ExternalWorkParseError(ValueError):
    """An edge names an external work no register entry stands up.

    The same silent-drop failure the framework guard above catches, in the one
    other place a node's declaration and the edges into it are authored apart:
    a ``rests-on`` bullet naming a key whose ``work-`` entry was deleted, or
    never landed, would otherwise write a dangling edge.
    """


def _assert_work_node_coverage(claims_records: list[dict], depends_on_records: list[dict]) -> None:
    """Fail loudly if a ``rests-on`` edge targets a work with no register entry."""
    present = {r["id"] for r in claims_records if r["node_type"] == "work"}
    referenced = {e["target"] for e in depends_on_records if e.get("target_kind") == "work"}
    missing = sorted(referenced - present)
    if not missing:
        return
    raise ExternalWorkParseError(
        f"{len(missing)} rests-on edge target(s) name external works with no register entry: "
        f"{', '.join(missing)}.\nAn external work's node is its `<!-- id: work-<key> -->` entry in a "
        f"claim-quality.md register (canonically kb-root/{WORKS_REGISTER}); the edge is a depends-on "
        f"bullet naming it. Insert the missing entry, or remove the bullet, and re-run (refresh aborted "
        f"before writing a dangling index)."
    )


def build_all_records(state: KbState) -> dict[str, list[dict]]:
    """Return every JSONL file's records keyed by short file name.

    Raises :class:`FrameworkNodeParseError` if the assembled edges reference
    framework nodes that did not parse from ``CLAUDE.md`` (issue #28 guard), and
    :class:`ExternalWorkParseError` if they reference an external work with no
    register entry.
    """
    claims = build_claims_records(state)
    depends_on = build_depends_on_records(state)
    _assert_framework_node_coverage(claims, depends_on)
    _assert_work_node_coverage(claims, depends_on)
    return {
        "claims": claims,
        "depends-on": depends_on,
        "strengthen-by": build_strengthen_by_records(state),
        "supported-by": build_supported_by_records(state),
        "cites": build_cites_records(state),
        "subtree-aggregates": build_subtree_aggregate_records(state),
    }


# ---------------------------------------------------------------------------
# JSONL I/O
# ---------------------------------------------------------------------------


def serialize_records(records: list[dict]) -> str:
    """Serialize records to canonical JSONL text.

    Each line is ``json.dumps(rec, ensure_ascii=False, separators=(', ', ': '))``.
    Keys appear in the dict's insertion order (Python 3.7+), so callers must
    construct records with keys in the documented order. The result has one
    trailing ``\\n`` for non-empty inputs and is the empty string for ``[]``.
    """
    lines = [json.dumps(rec, ensure_ascii=False, separators=(", ", ": ")) for rec in records]
    body = "\n".join(lines)
    if body:
        body += "\n"
    return body


def write_jsonl(path: Path, records: list[dict]) -> None:
    """Write records as JSONL, one object per line, single trailing newline.

    Thin wrapper around :func:`serialize_records` that writes the canonical
    text to ``path`` as UTF-8 with LF line endings.
    """
    path.write_text(serialize_records(records), encoding="utf-8", newline="\n")


def read_jsonl(path: Path) -> list[dict]:
    """Parse JSONL file. Blank lines skipped; malformed lines raise ValueError."""
    out: list[dict] = []
    text = path.read_text(encoding="utf-8")
    for lineno, line in enumerate(text.splitlines(), start=1):
        if not line.strip():
            continue
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError as exc:
            raise ValueError(f"{path}:{lineno}: malformed JSON: {exc.msg}") from exc
    return out


__all__ = [
    "EXCLUDE_DIRS",
    "EXCLUDE_NAMES",
    "PENDING_LITERAL",
    "PENDING_FRACTION",
    "ClaimEntry",
    "DependsOnEdge",
    "FrameworkNode",
    "ExperimentNode",
    "ExperimentLeafError",
    "SupportNode",
    "SupportLeafError",
    "StrengthenByItem",
    "LeafRecord",
    "IndexRecord",
    "KbState",
    "SolidityResult",
    "parse_frontmatter",
    "framework_source",
    "framework_source_is_legacy",
    "parse_framework_nodes",
    "parse_leaf",
    "parse_experiment_leaf",
    "parse_support_leaf",
    "parse_claim_quality_file",
    "parse_support_quality_entries",
    "RegisterEntry",
    "locate_register_entries",
    "RegisterCensus",
    "FanOutRecord",
    "RegisterWalk",
    "walk_registers",
    "reconcile_support_fan_out",
    "collect_known_claim_ids",
    "IdRecord",
    "scan_authored_ids",
    "scan_duplicate_register_ids",
    "scan_authored_support_edges",
    "discover_kb",
    "derive_build_band",
    "build_status_phrase",
    "round_half_up_2dp",
    "compute_solidity",
    "compute_solidity_full",
    "compute_support_solidity",
    "min_dependency_solidity",
    "SolidityCycleError",
    "FrameworkNodeParseError",
    "format_solidity",
    "render_solidity_trace",
    "render_min_trace",
    "build_claims_records",
    "build_depends_on_records",
    "build_strengthen_by_records",
    "build_supported_by_records",
    "build_cites_records",
    "LEAF_REFERENCES_PREFIX",
    "EntryFooterBand",
    "locate_leaf_reference_footers",
    "build_leaf_references",
    "render_leaf_references",
    "frontmatter_field_end",
    "compute_subtree_aggregates",
    "build_subtree_aggregate_records",
    "build_all_records",
    "serialize_records",
    "write_jsonl",
    "read_jsonl",
]
