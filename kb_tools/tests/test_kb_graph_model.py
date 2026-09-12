"""Assembly-level tests for ``kb_graph.model``.

One clause of the row's done-condition per section: edge dedupe and the
relation-precedence conflict, ``supported-by.jsonl`` contributing nothing, an
edge naming an absent id becoming a stub rather than an exception, and
root-hosted domain attribution. Plus the two properties those clauses rest
on — connectivity classification, and the shuffle invariance demanded of
every ordering.

Most tests hand the model its dataclasses directly; only the
``supported-by.jsonl`` clause needs bytes on disk, because the fact it asserts
is about what the loader does and does not read.
"""

import json
import random
from dataclasses import asdict
from pathlib import Path

import pytest

from kb_tools import kb_schema
from kb_tools.kb_cmd import index as kb_index
from kb_tools.kb_graph import model

# ---------------------------------------------------------------------------
# Record builders
# ---------------------------------------------------------------------------


def _claim(cid: str, *, canonical_path: str = "vol1/topic/leaf.md") -> kb_index.Claim:
    return kb_index.Claim(
        node_type="claim",
        id=cid,
        title=f"Claim {cid}",
        canonical_path=canonical_path,
        canonical_anchor=cid,
        confidence=0.8,
        solidity=0.8,
        build_status=None,
        build_band="ok-to-build",
        rationale="",
        depends_on_count=0,
        strengthen_by_count=0,
        citation_count=0,
    )


def _support(sid: str, *, canonical_path: str = "vol1/topic/sup.md") -> kb_index.SupportNode:
    return kb_index.SupportNode(
        node_type="support",
        id=sid,
        title=f"Support {sid}",
        canonical_path=canonical_path,
        canonical_anchor=sid,
        quality=0.9,
        solidity=0.9,
        build_band="ok-to-build",
    )


def _edge(source: str, target: str, relation: str = "depends", **overrides) -> kb_index.DependsOnEdge:
    fields = {
        "source": source,
        "target": target,
        "target_kind": "claim",
        "target_solidity_recorded": None,
        "context": None,
        "relation": relation,
        "strength": None,
        "fraction": None,
    }
    fields.update(overrides)
    return kb_index.DependsOnEdge(**fields)


# ---------------------------------------------------------------------------
# Dedupe — one edge per relationship, one stroke per edge
# ---------------------------------------------------------------------------


def test_two_records_for_one_pair_yield_one_edge() -> None:
    """The file's own sort key includes ``context`` because duplicates occur."""
    graph = model.build_graph(
        nodes=[_claim("clm-aaaaaa"), _claim("clm-bbbbbb")],
        edges=[
            _edge("clm-aaaaaa", "clm-bbbbbb", context="second note"),
            _edge("clm-aaaaaa", "clm-bbbbbb", context="first note"),
        ],
    )
    assert [(e.source, e.target) for e in graph.edges] == [("clm-aaaaaa", "clm-bbbbbb")]
    assert graph.edges[0].contexts == ("first note", "second note")
    assert not graph.edges[0].conflict


def test_repeated_and_empty_contexts_collapse() -> None:
    graph = model.build_graph(
        nodes=[_claim("clm-aaaaaa"), _claim("clm-bbbbbb")],
        edges=[
            _edge("clm-aaaaaa", "clm-bbbbbb", context="note"),
            _edge("clm-aaaaaa", "clm-bbbbbb", context="note"),
            _edge("clm-aaaaaa", "clm-bbbbbb", context=None),
        ],
    )
    assert len(graph.edges) == 1
    assert graph.edges[0].contexts == ("note",)


def test_two_relation_group_is_one_stroke_carrying_depends_and_the_conflict_mark() -> None:
    graph = model.build_graph(
        nodes=[_claim("clm-aaaaaa"), _support("sup-aaaaaa")],
        edges=[
            _edge("sup-aaaaaa", "clm-aaaaaa", "supports", fraction=0.5),
            _edge("sup-aaaaaa", "clm-aaaaaa", "depends"),
        ],
    )
    assert len(graph.edges) == 1
    edge = graph.edges[0]
    assert edge.relation == "depends"
    assert edge.conflict
    # The stroke names every relation found, so its title can too.
    assert edge.relations == ("depends", "supports")


@pytest.mark.parametrize(
    ("present", "expected"),
    [
        (("supports", "strengthens"), "supports"),
        (("strengthens", "depends"), "depends"),
        (("strengthens",), "strengthens"),
        (("supports", "conjectures"), "supports"),
        (("conjectures", "zzz"), "conjectures"),
        # `references` asserts nothing about strength, so any other class on the
        # same pair is the one worth drawing — it sorts last of the declared set
        # and still ahead of an undeclared one.
        (("references", "rests-on"), "rests-on"),
        (("references", "conjectures"), "references"),
    ],
)
def test_relation_precedence_is_the_declared_constant(present: tuple[str, ...], expected: str) -> None:
    """Precedence is ``RELATION_PRECEDENCE``, not the order records arrived in;
    a relation outside it sorts after all of them, by name."""
    graph = model.build_graph(
        nodes=[_claim("clm-aaaaaa"), _claim("clm-bbbbbb")],
        edges=[_edge("clm-aaaaaa", "clm-bbbbbb", relation) for relation in reversed(present)],
    )
    assert graph.edges[0].relation == expected
    assert model.RELATION_PRECEDENCE == ("depends", "supports", "strengthens", "rests-on", "references")


def test_surviving_scalars_come_from_the_surviving_relation() -> None:
    """A conflict's ``fraction`` must not leak in from the losing record."""
    graph = model.build_graph(
        nodes=[_claim("clm-aaaaaa"), _support("sup-aaaaaa")],
        edges=[
            _edge("sup-aaaaaa", "clm-aaaaaa", "supports", fraction=kb_schema.PENDING_LITERAL),
            _edge("sup-aaaaaa", "clm-aaaaaa", "depends", target_kind="invariant"),
        ],
    )
    assert (graph.edges[0].fraction, graph.edges[0].target_kind) == (None, "invariant")


def test_a_supports_edge_keeps_its_fraction_when_it_is_the_only_relation() -> None:
    graph = model.build_graph(
        nodes=[_claim("clm-aaaaaa"), _support("sup-aaaaaa")],
        edges=[_edge("sup-aaaaaa", "clm-aaaaaa", "supports", fraction=0.3)],
    )
    assert (graph.edges[0].relation, graph.edges[0].fraction) == ("supports", 0.3)


def test_opposite_directions_are_two_edges() -> None:
    """Edge identity is the ORDERED pair; a reciprocal pair is two facts."""
    graph = model.build_graph(
        nodes=[_claim("clm-aaaaaa"), _claim("clm-bbbbbb")],
        edges=[_edge("clm-bbbbbb", "clm-aaaaaa"), _edge("clm-aaaaaa", "clm-bbbbbb")],
    )
    assert [(e.source, e.target) for e in graph.edges] == [
        ("clm-aaaaaa", "clm-bbbbbb"),
        ("clm-bbbbbb", "clm-aaaaaa"),
    ]


# ---------------------------------------------------------------------------
# The reference reduction — a reference a path already implies is not drawn
# ---------------------------------------------------------------------------

_A, _B, _C, _D = "clm-aaaaaa", "clm-bbbbbb", "clm-cccccc", "clm-dddddd"


def _drawn(edges: list[kb_index.DependsOnEdge], *, ids: tuple[str, ...] = (_A, _B, _C, _D)) -> list[tuple[str, str]]:
    graph = model.build_graph(nodes=[_claim(cid) for cid in ids], edges=edges)
    return [(e.source, e.target) for e in graph.edges]


def _walkable(drawn: list[tuple[str, str]], source: str, target: str) -> bool:
    """Whether one or more drawn edges lead from ``source`` to ``target``."""
    frontier, seen = [t for s, t in drawn if s == source], set()
    while frontier:
        current = frontier.pop()
        if current == target:
            return True
        if current not in seen:
            seen.add(current)
            frontier.extend(t for s, t in drawn if s == current)
    return False


def test_a_reference_a_dependency_path_implies_is_not_drawn() -> None:
    """The reduction is over the union of the classes: a reader walking the two
    ``depends`` edges reaches C from A, so the reference over the top adds no
    relationship to the picture."""
    drawn = _drawn(
        [
            _edge(_A, _B, "depends"),
            _edge(_B, _C, "depends"),
            _edge(_A, _C, model.REFERENCE_RELATION),
        ],
        ids=(_A, _B, _C),
    )
    assert drawn == [(_A, _B), (_B, _C)]


def test_a_reference_a_reference_path_implies_is_not_drawn() -> None:
    """One class or two makes no difference — the union is what is walked."""
    drawn = _drawn(
        [_edge(*pair, model.REFERENCE_RELATION) for pair in ((_A, _B), (_B, _C), (_C, _D), (_A, _D))],
    )
    assert drawn == [(_A, _B), (_B, _C), (_C, _D)]


def test_a_reference_with_no_other_path_is_drawn_whatever_its_length() -> None:
    """No alternate route, no suppression: the reference is the only thing
    saying these two claims are related at all."""
    drawn = _drawn(
        [
            _edge(_B, _A, "depends"),
            _edge(_C, _B, "depends"),
            _edge(_A, _D, model.REFERENCE_RELATION),
        ]
    )
    assert drawn == [(_A, _D), (_B, _A), (_C, _B)]


@pytest.mark.parametrize("relation", ["depends", "rests-on", "supports", "strengthens"])
def test_no_class_but_references_is_ever_suppressed(relation: str) -> None:
    """Only the class that computes nothing may go: every other one states a
    premise, a lift or a standing, and a path implying it does not make it
    redundant."""
    drawn = _drawn(
        [_edge(*pair, relation) for pair in ((_A, _B), (_B, _C), (_A, _C))],
        ids=(_A, _B, _C),
    )
    assert drawn == [(_A, _B), (_A, _C), (_B, _C)]


def test_a_pair_recorded_as_both_a_dependency_and_a_reference_is_drawn() -> None:
    """Dedupe leaves the pair carrying ``depends``, and a ``depends`` stroke is
    never a candidate — the reference riding with it cannot take it off the
    sheet."""
    drawn = _drawn(
        [
            _edge(_A, _B, "depends"),
            _edge(_B, _C, "depends"),
            _edge(_A, _C, model.REFERENCE_RELATION),
            _edge(_A, _C, "depends"),
        ],
        ids=(_A, _B, _C),
    )
    assert drawn == [(_A, _B), (_A, _C), (_B, _C)]


def test_every_suppressed_reference_is_still_walkable_through_the_drawn_edges() -> None:
    """The property the ruling rests on: dropping a reference hides no
    relationship, because its source still reaches its target on the sheet."""
    pairs = [(a, b) for a in (_A, _B, _C, _D) for b in (_A, _B, _C, _D) if a != b]
    edges = [_edge(*pair, model.REFERENCE_RELATION) for pair in pairs] + [_edge(_D, _A, "depends")]
    drawn = _drawn(edges)

    assert len(drawn) < len(pairs), "a fully connected graph has references to spare"
    for source, target in pairs:
        assert _walkable(drawn, source, target), f"{source} no longer reaches {target}"


def test_a_reference_cycle_keeps_every_member_attached() -> None:
    """Every edge of a mutual pair has an alternate path in the *unreduced*
    graph, so a snapshot-based reduction drops both and strands two claims. The
    reduction reads the edges still standing, so the last one holding the pair
    together finds no path and stays."""
    graph = model.build_graph(
        nodes=[_claim(_A), _claim(_B)],
        edges=[_edge(_A, _B, model.REFERENCE_RELATION), _edge(_B, _A, model.REFERENCE_RELATION)],
    )
    assert [(e.source, e.target) for e in graph.edges] == [(_A, _B), (_B, _A)]
    assert [node.id for node in graph.nodes if node.isolated] == []


def test_a_suppression_never_makes_an_orphan_or_a_component() -> None:
    """Degree is measured over the drawn set, and that is safe rather than
    lucky: a suppressed edge's endpoints each keep an incident stroke, so no
    claim drops into the orphan block and no component splits."""
    pairs = [(_A, _B), (_B, _C), (_A, _C), (_C, _D), (_B, _D), (_A, _D)]
    graph = model.build_graph(
        nodes=[_claim(cid) for cid in (_A, _B, _C, _D)],
        edges=[_edge(*pair, model.REFERENCE_RELATION) for pair in pairs],
    )
    assert len(graph.edges) < len(pairs)
    assert [node.id for node in graph.nodes if node.isolated] == []
    assert graph.components == ((_A, _B, _C, _D),)


def test_a_self_reference_survives_where_nothing_returns_to_the_claim() -> None:
    """Reaching a claim from itself takes an edge; a reflexive reading would
    drop every self-reference, and one whose claim has no other stroke would
    take the claim out of the hierarchy with it."""
    drawn = _drawn([_edge(_A, _A, model.REFERENCE_RELATION)], ids=(_A,))
    assert drawn == [(_A, _A)]


# ---------------------------------------------------------------------------
# ``supported-by.jsonl`` contributes nothing
# ---------------------------------------------------------------------------


def _write_index(index_dir: Path, *, claims: list[dict], depends_on: list[dict], extra: dict[str, list[dict]]) -> Path:
    index_dir.mkdir(parents=True, exist_ok=True)
    files: dict[str, list[dict]] = {
        "claims.jsonl": claims,
        "depends-on.jsonl": depends_on,
        "strengthen-by.jsonl": [],
        "cites.jsonl": [],
        "subtree-aggregates.jsonl": [],
        **extra,
    }
    for name, records in files.items():
        (index_dir / name).write_text("".join(json.dumps(r) + "\n" for r in records), encoding="utf-8")
    return index_dir


def _loaded_edges(idx: kb_index.Index) -> list[kb_index.DependsOnEdge]:
    """Every edge the index loaded.

    ``Index`` exposes no accessor for the whole edge list, only the per-source
    ``depends_on_edges``; this walk over known node ids is exact for a fixture
    with no ghost source, which is the case here.
    """
    return [edge for node in idx.all_nodes for edge in idx.depends_on_edges(node.id)]


def test_supported_by_on_disk_contributes_nothing(tmp_path: Path) -> None:
    """``supported-by.jsonl`` is a derived reverse view of the same ``supports``
    records; drawing from it would double-stroke a fact recorded once."""
    claims = [asdict(_claim("clm-aaaaaa")), asdict(_support("sup-aaaaaa"))]
    depends_on = [asdict(_edge("sup-aaaaaa", "clm-aaaaaa", "supports", fraction=0.5))]
    # The reverse view of that same fact, plus a second pair no depends-on line
    # backs — if either were read, the graph would carry an extra stroke.
    supported_by = [
        {"claim_id": "clm-aaaaaa", "support_id": "sup-aaaaaa", "fraction": 0.5},
        {"claim_id": "clm-aaaaaa", "support_id": "sup-bbbbbb", "fraction": 1.0},
    ]
    index_dir = _write_index(
        tmp_path / ".index",
        claims=claims,
        depends_on=depends_on,
        extra={"supported-by.jsonl": supported_by},
    )
    assert (index_dir / "supported-by.jsonl").is_file()

    idx = kb_index.load(index_dir)
    assert idx.stats["depends_on_edges"] == 1
    graph = model.build_graph(nodes=idx.all_nodes, edges=_loaded_edges(idx))
    assert [(e.source, e.target) for e in graph.edges] == [("sup-aaaaaa", "clm-aaaaaa")]
    assert graph.node("sup-bbbbbb") is None


# ---------------------------------------------------------------------------
# Defects decidable without geometry
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("ghost_end", ["source", "target"])
def test_an_edge_naming_an_absent_id_yields_a_stub_node(ghost_end: str) -> None:
    """A defective graph is the picture's subject, never an error."""
    known, ghost = "clm-aaaaaa", "clm-ghostx"
    edge = _edge(ghost, known) if ghost_end == "source" else _edge(known, ghost)
    graph = model.build_graph(nodes=[_claim(known)], edges=[edge])

    stub = graph.node(ghost)
    assert stub is not None and stub.is_stub
    assert (stub.title, stub.node_type, stub.domain) == ("", None, None)
    assert not stub.isolated  # it exists because an edge named it
    assert graph.node(known).is_stub is False
    assert len(graph.edges) == 1


def test_a_rests_on_target_drawn_off_the_loader_is_a_node_and_not_a_ghost(tmp_path: Path) -> None:
    """The whole path a renderer takes: ``.index/`` bytes, ``all_nodes``, the
    assembly. A work node reached that way must draw as itself — the loader's
    node list omitting it is what turned every ``rests-on`` target into a stub
    while ``claims.jsonl`` carried the record all along."""
    work = "work-khalil2002"
    claims = [
        asdict(_claim("clm-aaaaaa")),
        {
            "node_type": "work",
            "id": work,
            "title": "Khalil, Hassan K. 2002. Nonlinear Systems.",
            "canonical_path": "claim-quality.md",
            "canonical_anchor": "khalil-2002",
            "strength": None,
        },
    ]
    depends_on = [asdict(_edge("clm-aaaaaa", work, "rests-on", target_kind="work", fraction=kb_schema.PENDING_LITERAL))]
    idx = kb_index.load(_write_index(tmp_path / ".index", claims=claims, depends_on=depends_on, extra={}))

    graph = model.build_graph(nodes=idx.all_nodes, edges=_loaded_edges(idx))

    node = graph.node(work)
    assert node is not None and not node.is_stub
    assert (node.node_type, node.title) == ("work", "Khalil, Hassan K. 2002. Nonlinear Systems.")
    assert [(e.source, e.target, e.relation) for e in graph.edges] == [("clm-aaaaaa", work, "rests-on")]


def test_isolated_node_carries_no_incident_edge() -> None:
    graph = model.build_graph(
        nodes=[_claim("clm-aaaaaa"), _claim("clm-bbbbbb"), _claim("clm-cccccc")],
        edges=[_edge("clm-aaaaaa", "clm-bbbbbb")],
    )
    assert [n.id for n in graph.nodes if n.isolated] == ["clm-cccccc"]


def test_disconnected_components_are_partitioned_deterministically() -> None:
    graph = model.build_graph(
        nodes=[_claim(cid) for cid in ("clm-aaaaaa", "clm-bbbbbb", "clm-cccccc", "clm-dddddd", "clm-eeeeee")],
        edges=[
            _edge("clm-dddddd", "clm-cccccc"),
            _edge("clm-bbbbbb", "clm-aaaaaa"),
        ],
    )
    assert graph.components == (
        ("clm-aaaaaa", "clm-bbbbbb"),
        ("clm-cccccc", "clm-dddddd"),
        ("clm-eeeeee",),
    )


def test_a_whole_graph_is_one_component() -> None:
    graph = model.build_graph(
        nodes=[_claim("clm-aaaaaa"), _claim("clm-bbbbbb"), _claim("clm-cccccc")],
        edges=[_edge("clm-aaaaaa", "clm-bbbbbb"), _edge("clm-bbbbbb", "clm-cccccc")],
    )
    assert graph.components == (("clm-aaaaaa", "clm-bbbbbb", "clm-cccccc"),)


def test_a_cycle_assembles_without_raising() -> None:
    """Cycle members and their back edges are the layering pass's marks;
    assembly must simply not choke on the shape."""
    graph = model.build_graph(
        nodes=[_claim("clm-aaaaaa"), _claim("clm-bbbbbb")],
        edges=[_edge("clm-aaaaaa", "clm-bbbbbb"), _edge("clm-bbbbbb", "clm-aaaaaa")],
    )
    assert len(graph.edges) == 2
    assert graph.components == (("clm-aaaaaa", "clm-bbbbbb"),)


def test_an_empty_index_assembles_to_an_empty_graph() -> None:
    graph = model.build_graph(nodes=[], edges=[])
    assert (graph.nodes, graph.edges, graph.components, graph.domains) == ((), (), (), ())


# ---------------------------------------------------------------------------
# Domain attribution
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("canonical_path", "expected"),
    [
        ("claim-quality.md", model.DOMAIN_ROOT),
        ("invariants.md", model.DOMAIN_ROOT),
        ("./entry-point.md", model.DOMAIN_ROOT),
        ("", model.DOMAIN_ROOT),
        ("vol1/index.md", "vol1"),
        ("vol1/topic/leaf.md", "vol1"),
    ],
)
def test_domain_is_the_first_path_component_with_a_root_sentinel(canonical_path: str, expected: str) -> None:
    graph = model.build_graph(nodes=[_claim("clm-aaaaaa", canonical_path=canonical_path)], edges=[])
    assert graph.node("clm-aaaaaa").domain == expected


def test_domains_are_the_ascending_attributed_set() -> None:
    graph = model.build_graph(
        nodes=[
            _claim("clm-aaaaaa", canonical_path="vol2/leaf.md"),
            _claim("clm-bbbbbb", canonical_path="vol1/leaf.md"),
            _claim("clm-cccccc", canonical_path="vol1/other/leaf.md"),
            _claim("clm-dddddd", canonical_path="claim-quality.md"),
        ],
        edges=[_edge("clm-aaaaaa", "clm-ghostx")],
    )
    assert graph.domains == (model.DOMAIN_ROOT, "vol1", "vol2")


# ---------------------------------------------------------------------------
# The assembly is a function of the record set, not of its order
# ---------------------------------------------------------------------------


def test_shuffling_the_records_changes_nothing() -> None:
    nodes = [
        _claim("clm-aaaaaa", canonical_path="vol1/leaf.md"),
        _claim("clm-bbbbbb", canonical_path="vol2/leaf.md"),
        _claim("clm-cccccc", canonical_path="claim-quality.md"),
        _support("sup-aaaaaa"),
    ]
    edges = [
        _edge("clm-aaaaaa", "clm-bbbbbb", context="beta"),
        _edge("clm-aaaaaa", "clm-bbbbbb", context="alpha"),
        _edge("sup-aaaaaa", "clm-aaaaaa", "supports", fraction=0.5),
        _edge("sup-aaaaaa", "clm-aaaaaa", "strengthens", strength=0.2),
        _edge("clm-cccccc", "clm-ghostx"),
        # Reducible references: which of them survives is decided by the
        # declared candidate order, so a reduction reading the record order
        # would come back with a different edge set on a shuffled list.
        _edge("clm-bbbbbb", "clm-cccccc", model.REFERENCE_RELATION),
        _edge("clm-cccccc", "clm-dddddd", model.REFERENCE_RELATION),
        _edge("clm-bbbbbb", "clm-dddddd", model.REFERENCE_RELATION),
    ]
    reference = model.build_graph(nodes=nodes, edges=edges)

    rng = random.Random(20260904)
    for _ in range(20):
        rng.shuffle(nodes)
        rng.shuffle(edges)
        assert model.build_graph(nodes=nodes, edges=edges) == reference
