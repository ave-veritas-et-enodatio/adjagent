"""Op-level and surface-level tests for the claim-graph renderer.

One section per clause of the row's done-condition: where the sheet lands, the
two layers that reach exit 2, the two refusals, the totality of a defective
graph, and the surface's refusal of an abbreviated spelling.

**The index is written directly, not refreshed.** The renderer reads
``.index/`` and nothing else, so a corpus for it is five JSONL files — and the
three defects this row must prove render (a cycle, a ghost id, a disconnected
component) are between them unreachable through a real refresh, which raises on
a cycle before it writes. The record keys are taken from the loader's own
builders; a key it stops reading fails here rather than silently.

No test below asserts a report line's wording — only its status token, the
identity it names, and the exit code. The two exceptions are the loader's own
messages, which are asserted *verbatim* against the exception this same loader
raises, because carrying them unaltered is the contract.
"""

import json
import os
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

from kb_tools import kb_util
from kb_tools.kb_cmd import index as kb_index
from kb_tools.kb_graph import model, ops

# ---------------------------------------------------------------------------
# A corpus on disk
# ---------------------------------------------------------------------------


def _claim(node_id: str, *, path: str, title: str = "A claim") -> dict:
    """One ``claims.jsonl`` claim record, carrying every key the loader reads."""
    return {
        "node_type": "claim",
        "id": node_id,
        "title": title,
        "canonical_path": path,
        "canonical_anchor": node_id,
        "solidity": 0.5,
        "build_band": "supported",
        "rationale": "",
        "depends_on_count": 0,
        "strengthen_by_count": 0,
        "citation_count": 0,
    }


def _edge(source: str, target: str, *, relation: str = "depends") -> dict:
    return {
        "source": source,
        "target": target,
        "target_kind": "claim",
        "target_solidity_recorded": None,
        "context": None,
        "relation": relation,
        "strength": None,
        "fraction": None,
    }


def _write_index(index_dir: Path, *, claims: list[dict], depends_on: list[dict]) -> None:
    """Write the five files ``kb_cmd.index.load`` requires; the unread three empty."""
    index_dir.mkdir(parents=True, exist_ok=True)
    files = {
        "claims.jsonl": claims,
        "depends-on.jsonl": depends_on,
        "strengthen-by.jsonl": [],
        "cites.jsonl": [],
        "subtree-aggregates.jsonl": [],
    }
    for name, records in files.items():
        body = "".join(f"{json.dumps(record)}\n" for record in records)
        (index_dir / name).write_text(body, encoding="utf-8")


#: A corpus carrying all three graph defects at once, plus two domains and a
#: root-hosted node. The cycle is ``aaaaaa`` and ``bbbbbb`` leaning on each
#: other; ``cccccc`` names ``zzzzzz``, which no record carries; ``dddddd`` and
#: ``eeeeee`` are a component with no segment to the rest.
_CLAIMS = [
    _claim("clm-aaaaaa", path="volume-a/one.md"),
    _claim("clm-bbbbbb", path="volume-a/two.md"),
    _claim("clm-cccccc", path="volume-a/three.md"),
    _claim("clm-dddddd", path="volume-b/one.md"),
    _claim("clm-eeeeee", path="volume-b/two.md"),
    _claim("clm-ffffff", path="entry-point.md"),
]
_EDGES = [
    _edge("clm-aaaaaa", "clm-bbbbbb"),
    _edge("clm-bbbbbb", "clm-aaaaaa"),
    _edge("clm-cccccc", "clm-zzzzzz"),
    _edge("clm-dddddd", "clm-eeeeee"),
]


@pytest.fixture
def kb(tmp_path: Path) -> Path:
    """A consuming repo's ``kb-root/``, with the defective corpus indexed under it."""
    repo = tmp_path / "consumer"
    (repo / ".git").mkdir(parents=True)
    kb_root = repo / kb_util.KB_DIRNAME
    (kb_root / "volume-a").mkdir(parents=True)
    _write_index(kb_root / kb_util.INDEX_DIRNAME, claims=_CLAIMS, depends_on=_EDGES)
    return kb_root


def _tree(root: Path) -> dict[Path, bytes]:
    """Every file under ``root``, by path, with its bytes — the whole write surface."""
    return {path: path.read_bytes() for path in sorted(root.rglob("*")) if path.is_file()}


def _detail(result: ops.Result, name: str) -> str:
    """The one reported item named ``name``, asserted unique."""
    matching = [item for item in result.report if item.name == name]
    assert len(matching) == 1, result.report
    return matching[0].detail


def _drawn_ids(document: str) -> set[str]:
    """Every node id the sheet draws, read off its first label line."""
    root = ET.fromstring(document)
    texts = [element.text or "" for element in root.iter("{http://www.w3.org/2000/svg}text")]
    return {text for text in texts if text.startswith("clm-")}


# ---------------------------------------------------------------------------
# Totality: a defective graph is the picture's subject
# ---------------------------------------------------------------------------


def test_a_cycle_a_ghost_id_and_a_disconnected_component_render_and_exit_zero(kb: Path) -> None:
    result = ops.render(kb_root=kb)

    assert result.exit_code == ops.EXIT_RENDERED
    assert result.written is not None and result.written.is_file()
    ET.fromstring(result.written.read_text(encoding="utf-8"))
    defects = _detail(result, "defects")
    assert "ghost-id 1" in defects
    assert "back-edge 2" in defects
    # Four components — the cycle, the ghost pair, volume-b's two, and the
    # root-hosted node alone — so three stand disconnected from the first.
    assert "disconnected-component 3" in defects
    assert "isolated-node 1" in defects


def test_the_census_reports_every_family(kb: Path) -> None:
    """Every ``FACT`` line §8 names is emitted, and the node census counts the drawn set."""
    result = ops.render(kb_root=kb)

    assert {item.name for item in result.report if item.status == kb_util.FACT} == {
        "nodes",
        "edges",
        "layers",
        "crossings",
        "defects",
    }
    # Six records plus the stub the ghost id stands for.
    assert _detail(result, "nodes").startswith("7 drawn")
    assert "claim 6" in _detail(result, "nodes")
    assert _detail(result, "edges").startswith("4 drawn")
    assert "depends 4" in _detail(result, "edges")


# ---------------------------------------------------------------------------
# Where the sheet lands, and what else it touches
# ---------------------------------------------------------------------------


def test_the_sheet_lands_beside_claim_quality_when_no_out_is_given(kb: Path) -> None:
    result = ops.render(kb_root=kb)

    assert result.written == kb / kb_util.CLAIM_GRAPH_FILENAME


def test_out_overrides_the_destination(kb: Path, tmp_path: Path) -> None:
    elsewhere = tmp_path / "sheet.svg"

    result = ops.render(kb_root=kb, out=elsewhere)

    assert result.exit_code == ops.EXIT_RENDERED
    assert result.written == elsewhere
    assert not (kb / kb_util.CLAIM_GRAPH_FILENAME).exists()


def test_the_op_writes_the_sheet_and_nothing_else(kb: Path) -> None:
    before = _tree(kb.parent)

    result = ops.render(kb_root=kb)

    after = _tree(kb.parent)
    assert set(after) - set(before) == {result.written}
    assert {path: body for path, body in after.items() if path in before} == before


def test_a_node_links_to_its_definition_relative_to_the_sheets_own_directory(kb: Path, tmp_path: Path) -> None:
    sheet_dir = tmp_path / "beside"
    sheet_dir.mkdir()

    result = ops.render(kb_root=kb, out=sheet_dir / "sheet.svg")

    document = ET.fromstring(result.written.read_text(encoding="utf-8"))
    hrefs = {element.get("href") for element in document.iter("{http://www.w3.org/2000/svg}a")}
    assert f"../{kb.parent.name}/{kb.name}/volume-a/one.md#clm-aaaaaa" in hrefs


# ---------------------------------------------------------------------------
# Composing without writing, and the graphs that are no precondition
# ---------------------------------------------------------------------------


def test_composing_yields_the_bytes_rendering_writes_and_touches_nothing(kb: Path) -> None:
    """``verify``'s half of the freshness gate: the sheet's bytes, no file.

    The directory composed against is the one the sheet is read from, which is
    what makes the two comparable — a node's hyperlink resolves relative to the
    document holding it.
    """
    before = _tree(kb.parent)

    composed = ops.compose(kb_root=kb, sheet_dir=kb)

    assert _tree(kb.parent) == before
    assert composed.document == ops.render(kb_root=kb).written.read_text(encoding="utf-8")


def test_an_unknown_domain_refuses_composition_by_raising(kb: Path) -> None:
    """``compose`` has no exit code to return, so the refusal is the exception.

    It is not a ``ValueError``: the loader raises those and they mean exit 2,
    which is the other code.
    """
    with pytest.raises(ops.UnknownDomain):
        ops.compose(kb_root=kb, sheet_dir=kb, domain="volume-c")


def test_a_graph_with_no_nodes_and_no_edges_renders_and_exits_zero(tmp_path: Path) -> None:
    """No property of the graph is a precondition — including having one.

    The failure this forecloses is dependency-order: a seeded spine whose sheet
    nobody can draw until something has been attributed to it. A picture of
    nothing is the true picture of a KB with nothing in it.
    """
    kb_root = tmp_path / "consumer" / kb_util.KB_DIRNAME
    _write_index(kb_root / kb_util.INDEX_DIRNAME, claims=[], depends_on=[])

    result = ops.render(kb_root=kb_root)

    assert result.exit_code == ops.EXIT_RENDERED
    ET.fromstring(result.written.read_text(encoding="utf-8"))
    assert _detail(result, "nodes").startswith("0 drawn")


def test_one_node_and_no_edges_renders_and_exits_zero(tmp_path: Path) -> None:
    """The spine's first claim, before anything has been attributed to it."""
    kb_root = tmp_path / "consumer" / kb_util.KB_DIRNAME
    _write_index(kb_root / kb_util.INDEX_DIRNAME, claims=[_claim("clm-aaaaaa", path="volume-a/one.md")], depends_on=[])

    result = ops.render(kb_root=kb_root)

    assert result.exit_code == ops.EXIT_RENDERED
    assert _drawn_ids(result.written.read_text(encoding="utf-8")) == {"clm-aaaaaa"}


# ---------------------------------------------------------------------------
# Exit 2 — the environment, in its two layers
# ---------------------------------------------------------------------------


@pytest.mark.skipif(hasattr(os, "geteuid") and os.geteuid() == 0, reason="root reads an unreadable directory")
def test_an_unreadable_index_exits_two_unread(kb: Path) -> None:
    """The pre-check refuses it, and the proof is which message comes back.

    A load attempted against this directory would fail too — but with the
    loader's own missing-files message, since an unreadable parent makes every
    required file report absent. The pre-check's own message is what says the
    op never got that far.
    """
    index_dir = kb / kb_util.INDEX_DIRNAME
    index_dir.chmod(0o000)
    try:
        result = ops.render(kb_root=kb)
    finally:
        index_dir.chmod(0o755)

    assert result.exit_code == ops.EXIT_ENVIRONMENT_UNFIT
    assert result.written is None
    assert "Index files missing" not in _detail(result, "index")
    assert str(index_dir) in _detail(result, "index")


def test_an_absent_index_file_exits_two_carrying_the_loaders_message(kb: Path) -> None:
    index_dir = kb / kb_util.INDEX_DIRNAME
    (index_dir / "cites.jsonl").unlink()

    result = ops.render(kb_root=kb)

    with pytest.raises(FileNotFoundError) as raised:
        kb_index.load(index_dir)
    assert result.exit_code == ops.EXIT_ENVIRONMENT_UNFIT
    assert _detail(result, "index") == str(raised.value)


def test_a_malformed_record_exits_two_carrying_the_loaders_message(kb: Path) -> None:
    index_dir = kb / kb_util.INDEX_DIRNAME
    (index_dir / "claims.jsonl").write_text('{"id": "clm-aaaaaa"\n', encoding="utf-8")

    result = ops.render(kb_root=kb)

    with pytest.raises(ValueError) as raised:
        kb_index.load(index_dir)
    assert result.exit_code == ops.EXIT_ENVIRONMENT_UNFIT
    assert _detail(result, "index") == str(raised.value)
    assert not (kb / kb_util.CLAIM_GRAPH_FILENAME).exists()


def test_an_out_whose_parent_is_absent_exits_two_having_written_nothing(kb: Path, tmp_path: Path) -> None:
    before = _tree(kb.parent)

    result = ops.render(kb_root=kb, out=tmp_path / "nowhere" / "sheet.svg")

    assert result.exit_code == ops.EXIT_ENVIRONMENT_UNFIT
    assert result.written is None
    assert _tree(kb.parent) == before


# ---------------------------------------------------------------------------
# Exit 7 — the two refusals
# ---------------------------------------------------------------------------


def test_an_out_inside_the_index_directory_is_refused(kb: Path) -> None:
    before = _tree(kb.parent)

    result = ops.render(kb_root=kb, out=kb / kb_util.INDEX_DIRNAME / "sheet.svg")

    assert result.exit_code == ops.EXIT_REFUSED
    assert result.written is None
    assert _tree(kb.parent) == before


def test_an_unknown_domain_is_refused(kb: Path) -> None:
    result = ops.render(kb_root=kb, domain="volume-c")

    assert result.exit_code == ops.EXIT_REFUSED
    assert result.written is None
    assert not (kb / kb_util.CLAIM_GRAPH_FILENAME).exists()


def test_the_root_sentinel_is_a_domain_the_flag_can_name(kb: Path) -> None:
    result = ops.render(kb_root=kb, domain=model.DOMAIN_ROOT)

    assert result.exit_code == ops.EXIT_RENDERED
    assert _drawn_ids(result.written.read_text(encoding="utf-8")) == {"clm-ffffff"}


# ---------------------------------------------------------------------------
# Domain selection, and the ghost id this row had to decide
# ---------------------------------------------------------------------------


def test_a_domain_sheet_carries_its_one_hop_foreign_neighbours_including_a_ghost_id(kb: Path) -> None:
    """``volume-a``'s three claims, plus the ghost id one of them names.

    The stub is in no domain, so the one-edge-away rule reaches it like any
    other outside node — which is the answer this row owed: dropping it would
    drop the edge too, hiding the defect the sheet exists to show.
    """
    result = ops.render(kb_root=kb, domain="volume-a")

    assert result.exit_code == ops.EXIT_RENDERED
    assert _drawn_ids(result.written.read_text(encoding="utf-8")) == {
        "clm-aaaaaa",
        "clm-bbbbbb",
        "clm-cccccc",
        "clm-zzzzzz",
    }


def test_a_domain_sheet_moves_no_node_from_where_the_full_sheet_puts_it(kb: Path, tmp_path: Path) -> None:
    """Layers and columns are the corpus's, so a node never moves between views."""
    full = ET.fromstring(ops.render(kb_root=kb, out=tmp_path / "full.svg").written.read_text(encoding="utf-8"))
    part = ET.fromstring(
        ops.render(kb_root=kb, domain="volume-a", out=tmp_path / "part.svg").written.read_text(encoding="utf-8")
    )

    def boxes(document: ET.Element) -> dict[str, tuple[str | None, str | None]]:
        placed = {}
        for group in document.iter("{http://www.w3.org/2000/svg}g"):
            labels = [text.text or "" for text in group.iter("{http://www.w3.org/2000/svg}text")]
            rects = list(group.iter("{http://www.w3.org/2000/svg}rect"))
            if labels and rects and labels[0].startswith("clm-"):
                placed[labels[0]] = (rects[0].get("x"), rects[0].get("y"))
        return placed

    shared = boxes(full).keys() & boxes(part).keys()
    assert shared
    assert {node_id: boxes(part)[node_id] for node_id in shared} == {
        node_id: boxes(full)[node_id] for node_id in shared
    }


# ---------------------------------------------------------------------------
# The surface
# ---------------------------------------------------------------------------


def _cli(kb: Path, monkeypatch: pytest.MonkeyPatch, *argv: str) -> int:
    """The op through ``kb_util.main``, from inside the consuming repo."""
    monkeypatch.chdir(kb.parent)
    return kb_util.main([kb_util.OP_RENDER_CLAIM_GRAPH, *argv])


def test_the_surface_renders_to_the_default_destination_and_prints_nothing_to_stdout(
    kb: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    code = _cli(kb, monkeypatch)

    captured = capsys.readouterr()
    assert code == ops.EXIT_RENDERED
    assert (kb / kb_util.CLAIM_GRAPH_FILENAME).is_file()
    assert captured.out == ""
    assert f"[{ops.REPORT_TAG}] " in captured.err


def test_the_surface_returns_the_ops_refusal_code(kb: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    assert _cli(kb, monkeypatch, "--domain", "volume-c") == ops.EXIT_REFUSED


def test_the_subcommand_rejects_an_abbreviated_spelling(kb: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(kb.parent)

    with pytest.raises(SystemExit) as raised:
        kb_util.main(["render-claim-grap"])

    assert raised.value.code == 2
    assert not (kb / kb_util.CLAIM_GRAPH_FILENAME).exists()


def test_an_abbreviated_option_is_a_usage_error(kb: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The ``allow_abbrev=False`` partial reaches this subparser like every sibling."""
    monkeypatch.chdir(kb.parent)

    with pytest.raises(SystemExit) as raised:
        kb_util.main([kb_util.OP_RENDER_CLAIM_GRAPH, "--do", "volume-a"])

    assert raised.value.code == 2


def test_the_refusal_code_is_the_houses_own(kb: Path) -> None:
    """7 is the toolchain's refusal rung, spelled in a package that cannot import it."""
    from kb_tools.kb_write import ops as write_ops

    assert ops.EXIT_REFUSED == write_ops.ExitCode.REFUSED
    assert ops.EXIT_ENVIRONMENT_UNFIT == kb_util.EXIT_ENVIRONMENT_UNFIT
