"""Loader-level tests for ``kb_cmd.index``: the node union, the edge classes,
and the ``stats`` census.

``claims.jsonl`` is a type-tagged union over ``kb_schema.NODE_KINDS`` and
``depends-on.jsonl`` of four edge classes. These tests hold the query side to
both: every record type loads as its own dataclass carrying its own fields, a
``supports`` edge is distinguishable from a ``depends`` edge, and ``stats``
counts every record in ``claims.jsonl`` rather than a subset of it.

Two layers: synthetic ``.index/`` fixtures written per-test (fast, and able to
plant cases the fixture KB has no instance of), plus one pass over the
committed ``mini-kb`` fixture refreshed through the real emitter, so the
loader is proven against the bytes the build side actually writes.
"""

import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from kb_tools import kb_index_lib, kb_schema
from kb_tools.kb_cmd import cli as kb_cli
from kb_tools.kb_cmd import index as kb_index

_THIS_DIR = Path(__file__).resolve().parent
_REPO_ROOT = _THIS_DIR.parent.parent
_FIXTURE_SRC = _THIS_DIR / "fixtures" / "mini-kb"


# ---------------------------------------------------------------------------
# Synthetic index fixtures
# ---------------------------------------------------------------------------


def _claim_record(cid: str) -> dict:
    return {
        "node_type": "claim",
        "id": cid,
        "title": f"Claim {cid}",
        "canonical_path": "main/claim-quality.md",
        "canonical_anchor": cid,
        "confidence": 0.8,
        "solidity": 0.8,
        "build_status": None,
        "build_band": "ok-to-build",
        "rationale": "",
        "depends_on_count": 0,
        "strengthen_by_count": 0,
        "citation_count": 0,
    }


def _framework_record(node_type: str, nid: str) -> dict:
    return {
        "node_type": node_type,
        "id": nid,
        "title": f"Framework {nid}",
        "canonical_path": "invariants.md",
        "canonical_anchor": nid.lower(),
    }


def _support_record(sid: str, *, quality: float | None, solidity: float | None) -> dict:
    """A support record as ``build_claims_records`` emits it — note the absence
    of ``build_band``, which is why the query side derives one."""
    return {
        "node_type": "support",
        "id": sid,
        "title": f"Support {sid}",
        "canonical_path": "common/sup.md",
        "canonical_anchor": sid,
        "quality": quality,
        "solidity": solidity,
    }


def _experiment_record(eid: str, *, status: str) -> dict:
    return {
        "node_type": "experiment",
        "id": eid,
        "title": f"Experiment {eid}",
        "canonical_path": "common/exp.md",
        "canonical_anchor": eid,
        "status": status,
    }


def _work_record(wid: str, *, strength: float | None) -> dict:
    """An external work as ``build_claims_records`` emits it — the five
    identifying fields plus ``strength``, and no solidity or band."""
    return {
        "node_type": "work",
        "id": wid,
        "title": f"Work {wid}",
        "canonical_path": "claim-quality.md",
        "canonical_anchor": wid,
        "strength": strength,
    }


def _edge_record(source: str, target: str, relation: str, **overrides) -> dict:
    rec = {
        "source": source,
        "target": target,
        "relation": relation,
        "target_kind": "claim",
        "target_solidity_recorded": None,
        "strength": None,
        "context": None,
        "fraction": None,
    }
    rec.update(overrides)
    return rec


def _write_index(index_dir: Path, *, claims: list[dict], depends_on: list[dict] | None = None) -> Path:
    index_dir.mkdir(parents=True, exist_ok=True)
    (index_dir / "claims.jsonl").write_text(
        "".join(json.dumps(r) + "\n" for r in claims),
        encoding="utf-8",
    )
    (index_dir / "depends-on.jsonl").write_text(
        "".join(json.dumps(r) + "\n" for r in (depends_on or ())),
        encoding="utf-8",
    )
    for name in ("strengthen-by.jsonl", "cites.jsonl", "subtree-aggregates.jsonl"):
        (index_dir / name).write_text("", encoding="utf-8")
    return index_dir


# One record of every kind in the vocabulary. A list standing up five of the
# six is what let `all_nodes` omit works unremarked.
_ALL_TYPES = [
    _claim_record("clm-aaaaaa"),
    _framework_record("invariant", "INVARIANT-S1"),
    _framework_record("axiom", "axiom-1"),
    _support_record("sup-aaaaaa", quality=0.9, solidity=0.9),
    _support_record("sup-bbbbbb", quality=None, solidity=None),
    _experiment_record("exp-aaaaaa", status="run"),
    _work_record("work-khalil2002", strength=None),
]


def test_the_synthetic_fixture_stands_up_every_node_kind() -> None:
    assert {rec["node_type"] for rec in _ALL_TYPES} == set(kb_schema.NODE_KINDS)


# ---------------------------------------------------------------------------
# Node union
# ---------------------------------------------------------------------------


def test_each_node_type_loads_as_its_own_dataclass(tmp_path: Path) -> None:
    idx = kb_index.load(_write_index(tmp_path / ".index", claims=_ALL_TYPES))
    assert isinstance(idx.node("clm-aaaaaa"), kb_index.Claim)
    assert isinstance(idx.node("INVARIANT-S1"), kb_index.FrameworkNode)
    assert isinstance(idx.node("axiom-1"), kb_index.FrameworkNode)
    assert isinstance(idx.node("sup-aaaaaa"), kb_index.SupportNode)
    assert isinstance(idx.node("exp-aaaaaa"), kb_index.ExperimentNode)
    assert isinstance(idx.node("work-khalil2002"), kb_index.ExternalWorkNode)
    # The framework bucket is invariants and axioms alone — a support or an
    # experiment landing there is what dropped their fields before.
    assert [n.id for n in idx.framework_nodes] == ["INVARIANT-S1", "axiom-1"]


def test_support_scoring_fields_survive_the_load(tmp_path: Path) -> None:
    idx = kb_index.load(_write_index(tmp_path / ".index", claims=_ALL_TYPES))
    sup = idx.node("sup-aaaaaa")
    assert (sup.quality, sup.solidity) == (0.9, 0.9)
    pending = idx.node("sup-bbbbbb")
    assert (pending.quality, pending.solidity) == (None, None)


def test_experiment_status_survives_the_load(tmp_path: Path) -> None:
    idx = kb_index.load(_write_index(tmp_path / ".index", claims=_ALL_TYPES))
    assert idx.node("exp-aaaaaa").status == "run"


@pytest.mark.parametrize("solidity", [None, 0.0, 0.19, 0.35, 0.6, 0.84, 0.85, 1.0])
def test_support_build_band_is_the_build_sides_mapping(tmp_path: Path, solidity: float | None) -> None:
    """The band is derived, and derived through the build side's own function —
    the record carries no ``build_band`` for the query side to read."""
    index_dir = _write_index(
        tmp_path / ".index",
        claims=[_support_record("sup-aaaaaa", quality=solidity, solidity=solidity)],
    )
    assert "build_band" not in json.loads((index_dir / "claims.jsonl").read_text(encoding="utf-8"))
    band = kb_index.load(index_dir).node("sup-aaaaaa").build_band
    assert band == kb_index_lib.derive_build_band(solidity)


def test_pending_support_bands_unknown_and_a_scored_one_does_not(tmp_path: Path) -> None:
    idx = kb_index.load(_write_index(tmp_path / ".index", claims=_ALL_TYPES))
    assert idx.node("sup-bbbbbb").build_band == kb_schema.UNKNOWN_BAND_SLUG
    assert idx.node("sup-aaaaaa").build_band == kb_schema.BUILD_BAND_LADDER[0].slug


def test_unknown_node_type_is_refused_rather_than_typed_as_framework(tmp_path: Path) -> None:
    """A discriminator outside the emitter's closed union must not be given
    framework bedrock semantics by default."""
    rogue = {**_framework_record("invariant", "zzz-1"), "node_type": "conjecture"}
    with pytest.raises(ValueError, match="unknown node_type 'conjecture'"):
        kb_index.load(_write_index(tmp_path / ".index", claims=[rogue]))


# ---------------------------------------------------------------------------
# Edge classes
# ---------------------------------------------------------------------------


def test_edge_classes_are_distinguishable_through_the_loader(tmp_path: Path) -> None:
    edges = [
        _edge_record("clm-aaaaaa", "clm-zzzzzz", "depends", target_solidity_recorded=0.9, context="note"),
        _edge_record("exp-aaaaaa", "clm-aaaaaa", "strengthens", strength=0.8),
        _edge_record("sup-aaaaaa", "clm-aaaaaa", "supports", fraction=0.5),
        _edge_record("sup-bbbbbb", "clm-aaaaaa", "supports", fraction=kb_schema.PENDING_LITERAL),
    ]
    idx = kb_index.load(_write_index(tmp_path / ".index", claims=_ALL_TYPES, depends_on=edges))

    depends = idx.depends_on_edges("clm-aaaaaa")[0]
    assert (depends.relation, depends.strength, depends.fraction) == ("depends", None, None)
    assert (depends.target_solidity_recorded, depends.context) == (0.9, "note")

    strengthens = idx.depends_on_edges("exp-aaaaaa")[0]
    assert (strengthens.relation, strengthens.strength, strengthens.fraction) == ("strengthens", 0.8, None)

    supports = idx.depends_on_edges("sup-aaaaaa")[0]
    assert (supports.relation, supports.strength, supports.fraction) == ("supports", None, 0.5)

    # A pending fraction is the schema's literal on disk and stays that literal
    # in memory — distinct from a depends edge's None.
    pending = idx.depends_on_edges("sup-bbbbbb")[0]
    assert pending.fraction == kb_schema.PENDING_LITERAL


# ---------------------------------------------------------------------------
# The deps output
# ---------------------------------------------------------------------------


def _deps_index(tmp_path: Path) -> Path:
    """A claim with one claim dep and two pairings with one external work."""
    edges = [
        _edge_record("clm-aaaaaa", "clm-zzzzzz", "depends", target_solidity_recorded=0.9),
        _edge_record("clm-aaaaaa", "work-khalil2002", "rests-on", target_kind="work", fraction=0.4),
        _edge_record(
            "clm-aaaaaa",
            "work-khalil2002",
            "rests-on",
            target_kind="work",
            fraction=kb_schema.PENDING_LITERAL,
            context="second pairing",
        ),
    ]
    return _write_index(tmp_path / ".index", claims=_ALL_TYPES, depends_on=edges)


def test_deps_text_carries_the_relation_and_the_applicability(tmp_path: Path, capsys) -> None:
    """The applicability is reachable through no other query, so the one place
    a reader can compare it against the rest of the chain is here."""
    assert kb_cli.main(["deps", "clm-aaaaaa", "--index-dir", str(_deps_index(tmp_path))]) == 0
    lines = capsys.readouterr().out.splitlines()
    assert lines[0] == "depends\tclm-zzzzzz"
    assert lines[1] == "rests-on\twork-khalil2002\tapplicability 0.4"
    # Two pairings with one work are two rows: they differ only in the number
    # the reader came for, so neither may be collapsed into the other.
    assert lines[2] == f"rests-on\twork-khalil2002\tapplicability {kb_schema.PENDING_LITERAL}"


def test_deps_json_carries_the_same_fields(tmp_path: Path, capsys) -> None:
    assert kb_cli.main(["deps", "clm-aaaaaa", "--json", "--index-dir", str(_deps_index(tmp_path))]) == 0
    records = json.loads(capsys.readouterr().out)
    assert records[0] == {"relation": "depends", "target": "clm-zzzzzz", "applicability": None}
    assert records[1] == {"relation": "rests-on", "target": "work-khalil2002", "applicability": 0.4}
    assert records[2]["applicability"] == kb_schema.PENDING_LITERAL


def test_deps_inverse_still_answers_with_ids(tmp_path: Path, capsys) -> None:
    """The inverse direction answers "who rests on this?" and has no one edge
    class to report — it stays a list of source ids."""
    args = ["deps", "work-khalil2002", "-i", "--index-dir", str(_deps_index(tmp_path))]
    assert kb_cli.main(args) == 0
    assert capsys.readouterr().out.splitlines() == ["clm-aaaaaa"]


# ---------------------------------------------------------------------------
# The stats census
# ---------------------------------------------------------------------------


def _census(stats: dict[str, int]) -> int:
    """The node total, summed over the whole vocabulary rather than a list of
    buckets — the helper naming five of five is why nothing caught the sixth."""
    return sum(stats[kb_schema.node_kind_plural(kind)] for kind in kb_schema.NODE_KINDS)


def test_stats_counts_every_record_in_claims_jsonl(tmp_path: Path) -> None:
    index_dir = _write_index(tmp_path / ".index", claims=_ALL_TYPES)
    stats = kb_index.load(index_dir).stats
    assert _census(stats) == len(_ALL_TYPES)
    assert (stats["supports"], stats["experiments"]) == (2, 1)


def test_stats_text_output_prints_every_stats_key(tmp_path: Path, capsys) -> None:
    """The text renderer emits the census's own keys, so a count added to
    ``stats`` — a node kind added to the vocabulary included — is shown by that
    alone. A hand-kept list here would count one and never show it."""
    index_dir = _write_index(tmp_path / ".index", claims=_ALL_TYPES)
    assert kb_cli.main(["stats", "--index-dir", str(index_dir)]) == 0
    printed = capsys.readouterr().out
    for key, value in kb_index.load(index_dir).stats.items():
        assert f"{key}: {value}" in printed


# ---------------------------------------------------------------------------
# Against the real emitter
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def refreshed_mini_kb(tmp_path_factory) -> Path:
    """The committed fixture copied out and refreshed through the real
    pipeline; the returned path is its ``.index/`` directory."""
    kb = tmp_path_factory.mktemp("mini-kb-loader") / "kb-root"
    shutil.copytree(_FIXTURE_SRC, kb)
    result = subprocess.run(
        [sys.executable, "-m", "kb_tools.refresh_kb_metadata", "--kb-root", str(kb)],
        cwd=_REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, f"refresh failed: {result.stdout}\n{result.stderr}"
    return kb / ".index"


def test_census_matches_the_emitted_record_count(refreshed_mini_kb: Path) -> None:
    records = [
        json.loads(line)
        for line in (refreshed_mini_kb / "claims.jsonl").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    stats = kb_index.load(refreshed_mini_kb).stats
    assert _census(stats) == len(records)
    # And the census is not vacuous. Every kind has a bucket whether or not a
    # KB populates it; this fixture stands up all of them but the external work,
    # which is the one kind no leaf can name.
    unpopulated = {kind for kind in kb_schema.NODE_KINDS if stats[kb_schema.node_kind_plural(kind)] == 0}
    assert unpopulated == {"work"}


def test_emitted_support_and_experiment_records_load_whole(refreshed_mini_kb: Path) -> None:
    idx = kb_index.load(refreshed_mini_kb)
    supports = idx.support_nodes
    assert supports and all(s.node_type == "support" for s in supports)
    # The fixture carries both a scored support and a pending one.
    assert any(s.solidity is not None and s.quality is not None for s in supports)
    assert any(s.solidity is None and s.build_band == kb_schema.UNKNOWN_BAND_SLUG for s in supports)
    for sup in supports:
        assert sup.build_band == kb_index_lib.derive_build_band(sup.solidity)

    experiments = idx.experiment_nodes
    assert experiments and all(e.node_type == "experiment" and e.status for e in experiments)


def test_emitted_supports_edges_carry_their_fraction(refreshed_mini_kb: Path) -> None:
    idx = kb_index.load(refreshed_mini_kb)
    by_relation: dict[str, list[kb_index.DependsOnEdge]] = {}
    for sup in idx.support_nodes:
        for edge in idx.depends_on_edges(sup.id):
            by_relation.setdefault(edge.relation, []).append(edge)
    supports_edges = by_relation.get("supports", [])
    assert supports_edges, "the fixture's supports edges must reach the loader"
    assert any(isinstance(e.fraction, float) for e in supports_edges)
    assert any(e.fraction == kb_schema.PENDING_LITERAL for e in supports_edges)
    assert all(e.strength is None for e in supports_edges)
