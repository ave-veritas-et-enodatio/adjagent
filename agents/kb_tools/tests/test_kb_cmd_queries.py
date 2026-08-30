"""Tests for the ``kb_cmd`` query surface: ``find`` and ``referenced-by``.

``find`` is a pure filter over the claims already loaded in the Index.
``referenced-by`` is a LIVE, leaf-granular reverse-find that scans KB leaf
bodies via the shared ``kb_links`` primitives — no persisted artifact.

``kb_cmd`` is a normal package (``from kb_tools.kb_cmd import index``); fixtures
are built per-test in a tmp dir: a minimal ``.index/`` JSONL set plus a small
leaf tree, so nothing depends on live KB state.
"""

import json
from pathlib import Path

from kb_tools.kb_cmd import index as kb_index


def _claim_record(cid: str, title: str, *, solidity: float, build_band: str) -> dict:
    return {
        "node_type": "claim",
        "id": cid,
        "title": title,
        "canonical_path": "main/claim-quality.md",
        "canonical_anchor": title.lower().replace(" ", "-"),
        "confidence": solidity,
        "solidity": solidity,
        "build_status": None,
        "build_band": build_band,
        "rationale": "",
        "depends_on_count": 0,
        "strengthen_by_count": 0,
        "citation_count": 1,
    }


def _cite_record(cid: str, leaf_path: str) -> dict:
    return {"claim_id": cid, "leaf_path": leaf_path, "leaf_kind": "leaf", "tier2_marked": False}


def _write_jsonl(path: Path, records: list[dict]) -> None:
    path.write_text("".join(json.dumps(r) + "\n" for r in records), encoding="utf-8")


def _build_index_dir(index_dir: Path, claims: list[dict], cites: list[dict]) -> None:
    index_dir.mkdir(parents=True, exist_ok=True)
    _write_jsonl(index_dir / "claims.jsonl", claims)
    _write_jsonl(index_dir / "cites.jsonl", cites)
    # The remaining required files may be empty for these queries.
    for name in ("depends-on.jsonl", "strengthen-by.jsonl", "subtree-aggregates.jsonl"):
        (index_dir / name).write_text("", encoding="utf-8")


_LEAF = """\
[↑ Up](../index.md)

<!-- kb-frontmatter
kind: leaf
{front}
-->

# {title}

{body}
"""


def _write_leaf(path: Path, *, front: str, title: str, body: str = "Leaf body.") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(_LEAF.format(front=front, title=title, body=body), encoding="utf-8")


# ---------------------------------------------------------------------------
# find
# ---------------------------------------------------------------------------


def _index_with_named_claims(tmp_path: Path):
    claims = [
        _claim_record(
            "clm-aaaaaa", "Proposition 4.3 — Institutional Misalignment", solidity=0.55, build_band="input-only"
        ),
        _claim_record("clm-bbbbbb", "Theorem 4.5 — Positive Invariance of M", solidity=0.90, build_band="ok-to-build"),
        _claim_record(
            "clm-cccccc", "Proposition 4.4 — KPI-Gating as Discrete Pontryagin", solidity=0.55, build_band="input-only"
        ),
    ]
    index_dir = tmp_path / ".index"
    _build_index_dir(index_dir, claims, cites=[])
    return kb_index.load(index_dir)


def test_find_matches_by_number(tmp_path: Path) -> None:
    idx = _index_with_named_claims(tmp_path)
    hits = idx.find("4.3")
    assert [c.id for c in hits] == ["clm-aaaaaa"]


def test_find_matches_by_name_case_insensitive(tmp_path: Path) -> None:
    idx = _index_with_named_claims(tmp_path)
    hits = idx.find("pontryagin")
    assert [c.id for c in hits] == ["clm-cccccc"]
    assert hits[0].title.startswith("Proposition 4.4")


def test_find_returns_ids_and_titles_for_shared_substring(tmp_path: Path) -> None:
    idx = _index_with_named_claims(tmp_path)
    hits = idx.find("Proposition")
    assert {c.id for c in hits} == {"clm-aaaaaa", "clm-cccccc"}
    assert all(c.title and c.id.startswith("clm-") for c in hits)


def test_find_empty_query_matches_all(tmp_path: Path) -> None:
    idx = _index_with_named_claims(tmp_path)
    assert len(idx.find("")) == 3


def test_find_no_match(tmp_path: Path) -> None:
    idx = _index_with_named_claims(tmp_path)
    assert idx.find("no-such-claim") == []


# ---------------------------------------------------------------------------
# referenced-by
# ---------------------------------------------------------------------------


def test_referenced_by_returns_linking_leaf(tmp_path: Path) -> None:
    """A leaf body that links to a claim's originating leaf is returned."""
    kb = tmp_path / "kb-root"
    # clm-aaaaaa originates in origin.md.
    claims = [_claim_record("clm-aaaaaa", "Origin Claim", solidity=0.5, build_band="input-only")]
    cites = [_cite_record("clm-aaaaaa", "main/origin.md")]
    _build_index_dir(kb / ".index", claims, cites)

    _write_leaf(kb / "main" / "origin.md", front="claims: [clm-aaaaaa]", title="Origin Claim")
    # A sibling leaf links to the origin leaf in its body.
    _write_leaf(
        kb / "main" / "citing.md",
        front='no-claim: "links to origin"',
        title="Citing Leaf",
        body="See [the origin](origin.md) for the derivation.",
    )
    # An index container also links to origin — must NOT count (not a leaf body).
    (kb / "main" / "index.md").parent.mkdir(parents=True, exist_ok=True)
    (kb / "main" / "index.md").write_text(
        "<!-- kb-frontmatter\nkind: index\n-->\n\n# Index\n\n[origin](origin.md)\n",
        encoding="utf-8",
    )

    idx = kb_index.load(kb / ".index")
    assert idx.originating_leaf("clm-aaaaaa") == "main/origin.md"
    result = idx.referenced_by("clm-aaaaaa", kb_root=kb)
    assert result == ["main/citing.md"]


def test_referenced_by_empty_when_no_leaf_links(tmp_path: Path) -> None:
    """The today's-KB case: no inter-leaf body links -> empty, not an error."""
    kb = tmp_path / "kb-root"
    claims = [_claim_record("clm-aaaaaa", "Origin Claim", solidity=0.5, build_band="input-only")]
    cites = [_cite_record("clm-aaaaaa", "main/origin.md")]
    _build_index_dir(kb / ".index", claims, cites)

    _write_leaf(kb / "main" / "origin.md", front="claims: [clm-aaaaaa]", title="Origin Claim")
    _write_leaf(
        kb / "main" / "other.md",
        front='no-claim: "no links"',
        title="Other Leaf",
        body="No links here.",
    )

    idx = kb_index.load(kb / ".index")
    assert idx.referenced_by("clm-aaaaaa", kb_root=kb) == []


def test_referenced_by_unknown_claim_returns_empty(tmp_path: Path) -> None:
    idx = _index_with_named_claims(tmp_path)
    assert idx.referenced_by("clm-zzzzzz", kb_root=tmp_path) == []
