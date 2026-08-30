"""Runtime query interface over the KB derived index.

Consumes the JSONL artifact set under the KB's ``.index/`` directory and
exposes the canonical question shapes from ``METADATA_SCHEMA.md`` §"Query semantics" as
pure in-memory lookups. Path resolution is shared with the build side via the
``kb_util`` module; this module uses stdlib only otherwise.

Construct an ``Index`` via ``load()``; all queries are dict lookups against
pre-built inverse indices.
"""

import json
import re
from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

# Route default index-dir resolution through the kb_util module.
from kb_tools import kb_links, kb_schema, kb_util

# Repo-root-relative hint for where the index lives by default. A static
# string, not a resolved path — importing this module must never trigger
# repo-root discovery (resolution happens lazily in _default_index_dir).
DEFAULT_INDEX_DIR_HINT = f"{kb_util.KB_DIRNAME}/{kb_util.INDEX_DIRNAME}"

_REQUIRED_FILES = (
    "claims.jsonl",
    "depends-on.jsonl",
    "strengthen-by.jsonl",
    "cites.jsonl",
    "subtree-aggregates.jsonl",
)

# Canonical build-band enum, in descending-solidity order. The slugs are the
# values written into each claim's ``build_band`` field by the derived-index
# pipeline (``kb_index_lib.derive_build_band``); the labels mirror the
# build-status legend in the root ``claim-quality.md``. The terminal
# ``"unknown"`` slug is the pending (unscored) bucket — a claim whose solidity
# is null carries ``build_band == "unknown"``.
#
# The scored (slug, label) rungs are sourced from ``kb_schema.BUILD_BAND_LADDER``
# — the single source of the schema vocabulary shared with the build side — so
# the build and query sides can no longer drift apart. Only the query-only
# ``unknown`` pending bucket is appended here (the ladder carries scored bands
# only; its unscored slug is ``kb_schema.UNKNOWN_BAND_SLUG``).
BUILD_BANDS: tuple[tuple[str, str], ...] = (
    *((b.slug, b.label) for b in kb_schema.BUILD_BAND_LADDER),
    (kb_schema.UNKNOWN_BAND_SLUG, "*pending*"),
)


# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Claim:
    """A canonical claim-quality entry — one ``node_type: claim`` node."""

    node_type: str  # always "claim"
    id: str
    title: str
    canonical_path: str
    canonical_anchor: str
    confidence: float | None
    solidity: float | None
    build_status: str | None
    build_band: str
    rationale: str
    depends_on_count: int
    strengthen_by_count: int
    citation_count: int


@dataclass(frozen=True, slots=True)
class FrameworkNode:
    """A structural invariant or axiom — a framework graph node.

    Framework nodes (``node_type: invariant`` / ``axiom``) carry only the
    five identifying fields. They are solidity-1.0 by definition (framework
    bedrock) — a documented rule, not a stored field.
    """

    node_type: str  # "invariant" | "axiom"
    id: str
    title: str
    canonical_path: str
    canonical_anchor: str


@dataclass(frozen=True, slots=True)
class DependsOnEdge:
    """A forward dependency edge from ``source`` to ``target``.

    ``target_kind`` (``claim`` | ``invariant`` | ``axiom``) discriminates the
    target node type.
    """

    source: str
    target: str
    target_kind: str
    target_solidity_recorded: float | None
    context: str | None


@dataclass(frozen=True, slots=True)
class StrengthenByItem:
    """A single strengthen-by bullet from a claim's Quality section."""

    claim_id: str
    item_idx: int
    text: str
    mentioned_ids: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class CitationEdge:
    """A (claim, leaf) citation edge."""

    claim_id: str
    leaf_path: str
    leaf_kind: str
    tier2_marked: bool


@dataclass(frozen=True, slots=True)
class SubtreeAggregate:
    """A precomputed subtree-claims aggregation for one index/entry-point node."""

    node_path: str
    node_kind: str
    subtree_claims: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class WeakPoint:
    """A claim-rework leverage record: a shaky claim with a dependent count.

    Surfaced by :meth:`Index.weak_points` — a claim that is both low-solidity
    and load-bearing (something depends on it). ``dependents`` is the count of
    claims that depend on this claim; the higher it is, the more downstream
    work a strengthening pass would lift.
    """

    claim: Claim
    dependents: int


# ---------------------------------------------------------------------------
# Default index location resolution
# ---------------------------------------------------------------------------


def _default_index_dir() -> Path:
    """Resolve the default .index directory.

    Lazy: ``kb_util.index_dir()`` discovers the consuming repo's root by
    walking up from the cwd (raising ``kb_util.RepoRootError`` — a
    ``FileNotFoundError`` — when no root is findable).
    """
    return kb_util.index_dir()


# ---------------------------------------------------------------------------
# JSONL parsing
# ---------------------------------------------------------------------------


def _read_jsonl(path: Path) -> list[dict]:
    """Parse a JSONL file. Blank lines skipped; malformed lines raise ValueError."""
    out: list[dict] = []
    with path.open("r", encoding="utf-8") as fh:
        for lineno, line in enumerate(fh, start=1):
            stripped = line.strip()
            if not stripped:
                continue
            try:
                obj = json.loads(stripped)
            except json.JSONDecodeError as exc:
                raise ValueError(f"{path}:{lineno}: malformed JSON: {exc.msg}") from exc
            if not isinstance(obj, dict):
                raise ValueError(f"{path}:{lineno}: expected JSON object, got {type(obj).__name__}")
            out.append(obj)
    return out


def _claim_from_record(rec: dict) -> Claim:
    return Claim(
        node_type=rec.get("node_type", "claim"),
        id=rec["id"],
        title=rec["title"],
        canonical_path=rec["canonical_path"],
        canonical_anchor=rec["canonical_anchor"],
        confidence=rec.get("confidence"),
        solidity=rec.get("solidity"),
        build_status=rec.get("build_status"),
        build_band=rec["build_band"],
        rationale=rec["rationale"],
        depends_on_count=rec["depends_on_count"],
        strengthen_by_count=rec["strengthen_by_count"],
        citation_count=rec["citation_count"],
    )


def _framework_from_record(rec: dict) -> FrameworkNode:
    return FrameworkNode(
        node_type=rec["node_type"],
        id=rec["id"],
        title=rec["title"],
        canonical_path=rec["canonical_path"],
        canonical_anchor=rec["canonical_anchor"],
    )


def _node_from_record(rec: dict) -> Claim | FrameworkNode:
    """Dispatch a claims.jsonl record on its ``node_type`` discriminator."""
    if rec.get("node_type", "claim") == "claim":
        return _claim_from_record(rec)
    return _framework_from_record(rec)


def _depends_on_from_record(rec: dict) -> DependsOnEdge:
    return DependsOnEdge(
        source=rec["source"],
        target=rec["target"],
        target_kind=rec.get("target_kind", "claim"),
        target_solidity_recorded=rec.get("target_solidity_recorded"),
        context=rec.get("context"),
    )


def _strengthen_by_from_record(rec: dict) -> StrengthenByItem:
    return StrengthenByItem(
        claim_id=rec["claim_id"],
        item_idx=rec["item_idx"],
        text=rec["text"],
        mentioned_ids=tuple(rec.get("mentioned_ids") or ()),
    )


def _citation_from_record(rec: dict) -> CitationEdge:
    return CitationEdge(
        claim_id=rec["claim_id"],
        leaf_path=rec["leaf_path"],
        leaf_kind=rec["leaf_kind"],
        tier2_marked=bool(rec["tier2_marked"]),
    )


def _subtree_from_record(rec: dict) -> SubtreeAggregate:
    return SubtreeAggregate(
        node_path=rec["node_path"],
        node_kind=rec["node_kind"],
        subtree_claims=tuple(rec.get("subtree_claims") or ()),
    )


# ---------------------------------------------------------------------------
# Index class
# ---------------------------------------------------------------------------


def _unique_sorted(values: Iterable[str]) -> list[str]:
    """Deduplicate and sort a list of strings."""
    return sorted(set(values))


# A leaf's `kind:` line inside the `<!-- kb-frontmatter ... -->` block. The
# reverse-find scans leaf BODIES only (not index/entry-point containers or the
# claim-quality.md register), so a referencing file must be a leaf.
_LEAF_KINDS = {"leaf", "leaf-as-index"}
_KIND_RE = re.compile(r"^\s*kind:\s*(\S+)", re.MULTILINE)


def _is_leaf_file(text: str) -> bool:
    """True if ``text`` declares a leaf ``kind:`` in its frontmatter block.

    Index/entry-point containers and the claim-quality.md register link to a
    leaf for navigation / derived footers; those are not body references and
    are excluded from the reverse-find.
    """
    m = _KIND_RE.search(text)
    return bool(m) and m.group(1) in _LEAF_KINDS


class Index:
    """In-memory query interface over the .index/*.jsonl artifact set.

    Construct via :func:`load`. After construction, all queries are dict lookups
    against pre-built indices — microseconds at current KB scale.
    """

    def __init__(
        self,
        nodes: list[Claim | FrameworkNode],
        depends_on: list[DependsOnEdge],
        strengthen_by: list[StrengthenByItem],
        cites: list[CitationEdge],
        subtree_aggregates: list[SubtreeAggregate],
    ) -> None:
        # claims.jsonl is a type-tagged union; split it into the claim subset
        # (which carries the scoring fields the filter queries operate on)
        # and the framework subset (invariants + axioms).
        self._claims: list[Claim] = sorted((n for n in nodes if isinstance(n, Claim)), key=lambda c: c.id)
        self._framework: list[FrameworkNode] = sorted(
            (n for n in nodes if isinstance(n, FrameworkNode)), key=lambda n: n.id
        )
        self._depends_on: list[DependsOnEdge] = list(depends_on)
        self._strengthen_by: list[StrengthenByItem] = list(strengthen_by)
        self._cites: list[CitationEdge] = list(cites)
        self._subtree_aggregates: list[SubtreeAggregate] = list(subtree_aggregates)

        self._by_id: dict[str, Claim] = {c.id: c for c in self._claims}
        # Every node, keyed by id — used by node() and dependents_of() so
        # framework ids resolve too.
        self._node_by_id: dict[str, Claim | FrameworkNode] = {n.id: n for n in (*self._claims, *self._framework)}

        # Forward / inverse dependency adjacency.
        deps_fwd: dict[str, set[str]] = defaultdict(set)
        deps_rev: dict[str, set[str]] = defaultdict(set)
        deps_fwd_edges: dict[str, list[DependsOnEdge]] = defaultdict(list)
        for edge in self._depends_on:
            deps_fwd[edge.source].add(edge.target)
            deps_rev[edge.target].add(edge.source)
            deps_fwd_edges[edge.source].append(edge)
        self._deps_fwd: dict[str, list[str]] = {k: sorted(v) for k, v in deps_fwd.items()}
        self._deps_rev: dict[str, list[str]] = {k: sorted(v) for k, v in deps_rev.items()}
        self._deps_fwd_edges: dict[str, list[DependsOnEdge]] = {
            k: sorted(v, key=lambda e: e.target) for k, v in deps_fwd_edges.items()
        }

        # Strengthen-by — grouped by claim_id (ordered by item_idx) and inverse
        # mention map (mentioned_id -> claims that mention it).
        sb_by_claim: dict[str, list[StrengthenByItem]] = defaultdict(list)
        sb_mentions: dict[str, set[str]] = defaultdict(set)
        for item in self._strengthen_by:
            sb_by_claim[item.claim_id].append(item)
            for mid in item.mentioned_ids:
                sb_mentions[mid].add(item.claim_id)
        self._strengthen_by_claim: dict[str, list[StrengthenByItem]] = {
            k: sorted(v, key=lambda it: it.item_idx) for k, v in sb_by_claim.items()
        }
        self._strengthen_mentions: dict[str, list[str]] = {k: sorted(v) for k, v in sb_mentions.items()}

        # Citations — by claim and by leaf path.
        cited_by: dict[str, list[CitationEdge]] = defaultdict(list)
        leaf_claims: dict[str, set[str]] = defaultdict(set)
        for cite in self._cites:
            cited_by[cite.claim_id].append(cite)
            leaf_claims[cite.leaf_path].add(cite.claim_id)
        self._cited_by: dict[str, list[CitationEdge]] = {
            k: sorted(v, key=lambda e: e.leaf_path) for k, v in cited_by.items()
        }
        self._leaf_claims: dict[str, list[str]] = {k: sorted(v) for k, v in leaf_claims.items()}

        # Subtree aggregates — keyed by node_path.
        self._subtree_by_path: dict[str, SubtreeAggregate] = {agg.node_path: agg for agg in self._subtree_aggregates}
        # Identify the entry-point node so callers can pass "" / "." for it.
        self._entry_point: SubtreeAggregate | None = next(
            (agg for agg in self._subtree_aggregates if agg.node_kind == "entry-point"),
            None,
        )

    # ---- Forward dependency edges --------------------------------------

    def depends_on(self, node_id: str) -> list[str]:
        """Node ids that ``node_id`` depends on (deduplicated, sorted).

        Targets may be claim or framework (invariant / axiom) ids. Only claim
        nodes are ever a ``source``, so passing a framework id returns ``[]``.
        """
        return list(self._deps_fwd.get(node_id, ()))

    def dependents_of(self, node_id: str) -> list[str]:
        """Claim ids that depend on ``node_id`` (inverse, deduplicated, sorted).

        Works for any node id, including framework ids — answers "which
        claims break if this invariant / axiom changes?".
        """
        return list(self._deps_rev.get(node_id, ()))

    def depends_on_edges(self, claim_id: str) -> list[DependsOnEdge]:
        """Full forward edge records sourced from ``claim_id`` (sorted by target)."""
        return list(self._deps_fwd_edges.get(claim_id, ()))

    # ---- Open work ------------------------------------------------------

    def strengthen_by(self, claim_id: str) -> list[StrengthenByItem]:
        """Strengthen-by items for ``claim_id`` (ordered by ``item_idx``)."""
        return list(self._strengthen_by_claim.get(claim_id, ()))

    def gated_on(self, claim_id: str) -> list[str]:
        """Claim ids whose strengthen-by items mention ``claim_id`` (sorted)."""
        return list(self._strengthen_mentions.get(claim_id, ()))

    # ---- Citations ------------------------------------------------------

    def cited_by(self, claim_id: str) -> list[CitationEdge]:
        """Citation edges naming this claim (sorted by leaf_path)."""
        return list(self._cited_by.get(claim_id, ()))

    def claims_in_leaf(self, leaf_path: str) -> list[str]:
        """Claim ids cited by ``leaf_path`` (sorted)."""
        return list(self._leaf_claims.get(leaf_path, ()))

    def originating_leaf(self, claim_id: str) -> str | None:
        """The leaf path that ORIGINATES ``claim_id``, or None.

        A claim's originating leaf is the leaf whose ``claims:`` frontmatter
        declares it — recorded as a ``cites`` edge (claim → citing leaf). In
        this KB origination is one leaf per claim; if more than one leaf cites
        the claim the lexicographically-first is returned (deterministic).
        Returns None if no leaf cites the claim or the id is unknown.
        """
        edges = self._cited_by.get(claim_id)
        if not edges:
            return None
        # _cited_by lists are pre-sorted by leaf_path; the first is canonical.
        return edges[0].leaf_path

    def referenced_by(self, claim_id: str, kb_root: Path | None = None) -> list[str]:
        """Leaf paths whose body hyperlinks resolve to ``claim_id``'s home leaf.

        Leaf-granular reverse-find computed LIVE at query time — it scans every
        KB leaf body for Markdown links and keeps the leaves whose links
        resolve to the claim's originating leaf. Nothing is persisted: there is
        no ``.index/`` artifact for this and no refresh/verify rule. (If it
        ever gets hot it can be materialized; deliberately kept off the build
        surface for now.)

        ``kb_root`` defaults to the live KB via ``kb_util``. The originating
        leaf itself is excluded from the result. Returns ``[]`` (not an error)
        when the claim has no originating leaf or no other leaf links to it.
        """
        origin_rel = self.originating_leaf(claim_id)
        if origin_rel is None:
            return []
        root = kb_root if kb_root is not None else kb_util.kb_root()
        origin_abs = (root / origin_rel).resolve()

        referencing: set[str] = set()
        for md_file in kb_links.iter_markdown_files(root):
            if md_file.resolve() == origin_abs:
                continue  # never count a leaf as referencing its own claim
            try:
                text = md_file.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                continue
            if not _is_leaf_file(text):
                continue  # leaf bodies only — skip index/entry-point/register
            body = kb_links.strip_code(text)
            for match in kb_links.LINK_RE.finditer(body):
                target = kb_links.strip_target(match.group(1))
                if not target:
                    continue
                if (md_file.parent / target).resolve() == origin_abs:
                    referencing.add(md_file.relative_to(root).as_posix())
                    break
        return sorted(referencing)

    # ---- Subtree aggregation -------------------------------------------

    def subtree_claims(self, node_path: str) -> list[str]:
        """All claim ids under ``node_path``.

        Accepts:
          - ``""`` or ``"."`` -> the entry-point's aggregate (whole tree)
          - a node-file path like ``"vol1/index.md"`` -> exact match
          - a directory path like ``"vol1"`` -> matches ``"vol1/index.md"``
        """
        if node_path in ("", "."):
            if self._entry_point is None:
                return []
            return list(self._entry_point.subtree_claims)
        # Try exact node_path first (e.g., "vol1/index.md").
        agg = self._subtree_by_path.get(node_path)
        if agg is None:
            # Try directory-style: strip trailing slash, append "/index.md".
            candidate = node_path.rstrip("/") + "/index.md"
            agg = self._subtree_by_path.get(candidate)
        if agg is None:
            return []
        return list(agg.subtree_claims)

    # ---- Filters --------------------------------------------------------

    def solidity_below(self, threshold: float) -> list[Claim]:
        """Claims with non-null ``solidity`` below ``threshold``.

        Sort: by solidity ascending, then by id (stable lexicographic).
        Claims with ``solidity is None`` are excluded.
        """
        matching = [c for c in self._claims if c.solidity is not None and c.solidity < threshold]
        matching.sort(key=lambda c: (c.solidity, c.id))  # type: ignore[arg-type]
        return matching

    def in_band(self, band: str) -> list[Claim]:
        """Claims in the given ``build_band`` (sorted by id)."""
        return [c for c in self._claims if c.build_band == band]

    def find(self, query: str) -> list[Claim]:
        """Claims whose title or canonical anchor contains ``query``.

        Case-insensitive substring match — the forward lookup from a human
        name or proposition number (e.g. ``"4.3"`` matches "Proposition 4.3 —
        …") to a ``clm-`` id. There is otherwise no way to recover a claim id
        from its human-facing name; users must never have to guess one.

        Returns matching claims sorted by id (the construction order of
        ``self._claims``). An empty ``query`` matches everything.
        """
        needle = query.casefold()
        return [c for c in self._claims if needle in c.title.casefold() or needle in c.canonical_anchor.casefold()]

    def weak_points(self, max_solidity: float = 0.65, min_dependents: int = 1) -> list[WeakPoint]:
        """Highest-leverage claim-rework targets: shaky *and* load-bearing.

        A claim qualifies when its ``solidity`` is non-null and strictly below
        ``max_solidity`` (genuinely shaky) and at least ``min_dependents``
        claims depend on it (something rests on it). Strengthening such a claim
        lifts the most downstream work.

        Pending claims (``solidity is None``) are unassessed, not weak, and are
        excluded — a different category from a low-solidity claim.

        Sort: by dependent count descending (most load-bearing first), then by
        solidity ascending (shakiest first) as the tiebreaker, then by id.
        """
        out: list[WeakPoint] = []
        for c in self._claims:
            if c.solidity is None or c.solidity >= max_solidity:
                continue
            dependents = len(self._deps_rev.get(c.id, ()))
            if dependents < min_dependents:
                continue
            out.append(WeakPoint(claim=c, dependents=dependents))
        out.sort(key=lambda wp: (-wp.dependents, wp.claim.solidity, wp.claim.id))  # type: ignore[arg-type]
        return out

    @property
    def pending_count(self) -> int:
        """Number of claims with unassessed (null) solidity."""
        return sum(1 for c in self._claims if c.solidity is None)

    # ---- Lookup ---------------------------------------------------------

    def claim(self, node_id: str) -> Claim | None:
        """Return the Claim record, or None.

        Returns ``None`` for a framework id (invariant / axiom) — use
        :meth:`node` to resolve any node type.
        """
        return self._by_id.get(node_id)

    def node(self, node_id: str) -> Claim | FrameworkNode | None:
        """Return the graph node for ``node_id`` regardless of node type.

        Resolves claim, invariant, and axiom ids. Returns ``None`` if no
        node carries that id.
        """
        return self._node_by_id.get(node_id)

    def __len__(self) -> int:
        return len(self._claims)

    # ---- Inspection -----------------------------------------------------

    @property
    def all_claims(self) -> list[Claim]:
        """All claim nodes, sorted by id (framework nodes excluded)."""
        return list(self._claims)

    @property
    def framework_nodes(self) -> list[FrameworkNode]:
        """All framework nodes (invariants + axioms), sorted by id."""
        return list(self._framework)

    @property
    def all_nodes(self) -> list[Claim | FrameworkNode]:
        """Every graph node — claims plus framework nodes, sorted by id."""
        return sorted((*self._claims, *self._framework), key=lambda n: n.id)

    @property
    def stats(self) -> dict[str, int]:
        """Counts useful for ``kb-cli stats``-style introspection."""
        invariants = sum(1 for n in self._framework if n.node_type == "invariant")
        axioms = sum(1 for n in self._framework if n.node_type == "axiom")
        return {
            "claims": len(self._claims),
            "invariants": invariants,
            "axioms": axioms,
            "depends_on_edges": len(self._depends_on),
            "strengthen_by_items": len(self._strengthen_by),
            "citation_edges": len(self._cites),
            "subtree_aggregates": len(self._subtree_aggregates),
        }

    @property
    def band_distribution(self) -> dict[str, int]:
        """Claim count per build band, keyed by slug, descending-solidity order.

        Counts ``node_type == "claim"`` records by their stored ``build_band``.
        Every slug in :data:`BUILD_BANDS` is present (zero when empty), so the
        result is a complete, stably-ordered view. The counts sum to the total
        claim count. ``"unknown"`` is the pending (unscored) bucket.
        """
        counts = {slug: 0 for slug, _ in BUILD_BANDS}
        for c in self._claims:
            if c.build_band in counts:
                counts[c.build_band] += 1
        return counts


# ---------------------------------------------------------------------------
# Loader
# ---------------------------------------------------------------------------


def load(path: Path | str | None = None) -> Index:
    """Load the index from a directory of ``.index/*.jsonl`` files.

    If ``path`` is None, the directory is auto-resolved via
    :func:`_default_index_dir` (walks up from the cwd to find the repo root).

    Raises:
        FileNotFoundError: if any required JSONL file is missing.
        ValueError: if any JSONL line fails to parse.
    """
    base = Path(path) if path is not None else _default_index_dir()
    missing = [name for name in _REQUIRED_FILES if not (base / name).is_file()]
    if missing:
        bullet_list = "\n".join(f"  - {name}" for name in missing)
        raise FileNotFoundError(
            f"Index files missing under {base}:\n{bullet_list}\n"
            f"Run `{kb_util.refresh_cmd()}` from the repository root to regenerate."
        )

    nodes = [_node_from_record(r) for r in _read_jsonl(base / "claims.jsonl")]
    depends_on = [_depends_on_from_record(r) for r in _read_jsonl(base / "depends-on.jsonl")]
    strengthen_by = [_strengthen_by_from_record(r) for r in _read_jsonl(base / "strengthen-by.jsonl")]
    cites = [_citation_from_record(r) for r in _read_jsonl(base / "cites.jsonl")]
    subtree_aggregates = [_subtree_from_record(r) for r in _read_jsonl(base / "subtree-aggregates.jsonl")]

    return Index(
        nodes=nodes,
        depends_on=depends_on,
        strengthen_by=strengthen_by,
        cites=cites,
        subtree_aggregates=subtree_aggregates,
    )


__all__ = [
    "Claim",
    "FrameworkNode",
    "DependsOnEdge",
    "StrengthenByItem",
    "CitationEdge",
    "SubtreeAggregate",
    "WeakPoint",
    "Index",
    "load",
    "DEFAULT_INDEX_DIR_HINT",
]
