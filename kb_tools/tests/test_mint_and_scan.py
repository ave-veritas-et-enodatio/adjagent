"""Tests for the authored-id inventory and the id minter.

``kb_index_lib.scan_authored_ids`` is the inventory a mint-bearing re-entry
checks against: it must see every authored ``clm-`` / ``exp-`` / ``sup-`` id
using the authored Markdown alone, and must be unaffected by the state of the
derived ``.index/``. The fixture below reproduces the state that motivated it —
registers populated with dozens of authored ids while every ``.index/*.jsonl``
sits at zero bytes — where an inventory read from ``.index/`` would conclude
"no existing ids" and mint a second, colliding set.

``kb_index_lib.scan_duplicate_register_ids`` is the mint-boundary question the
inventory cannot answer, for a structural reason: the inventory is keyed by id,
so a second register entry for an id already keyed is dropped rather than
reported. Both scans walk the same registers under the same exclusion rules.

``kb_schema.mint_id`` is exercised in process, against the inventory above:
that pairing is the whole of the mint boundary now that ids are born inside the
insert op and no standalone minting command survives.
"""

import re
from pathlib import Path

import pytest

from kb_tools import kb_index_lib, kb_schema

# The full inventory the fixture below must yield: id -> (kind, register, leaf).
# Every asymmetry in it is deliberate and named in `_write_run3_shaped_kb`.
EXPECTED_INVENTORY = {
    "clm-aa1111": ("clm", "claim-quality.md", "domain/aa-first.md"),
    "clm-bb2222": ("clm", "domain/claim-quality.md", "domain/bb-second.md"),
    "clm-dd4444": ("clm", "claim-quality.md", None),
    "exp-ee1111": ("exp", "domain/claim-quality.md", "domain/bench.md"),
    "exp-ff2222": ("exp", None, "domain/bench.md"),
    "sup-ss1111": ("sup", "claim-quality.md", "domain/warrant.md"),
}

_INDEX_FILES = (
    "claims.jsonl",
    "depends-on.jsonl",
    "strengthen-by.jsonl",
    "cites.jsonl",
    "supported-by.jsonl",
    "subtree-aggregates.jsonl",
)


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _write_run3_shaped_kb(kb_root: Path) -> None:
    """Registers populated, `.index/` present and empty — the motivating state.

    The inventory it declares exercises, in one tree:

    * all three id kinds, across two registers at different depths;
    * an ``exp-`` id keyed by a register entry (``exp-ee1111``) AND one declared
      only in leaf frontmatter (``exp-ff2222``, the ordinary case) — the two
      halves of "widened to all three prefixes";
    * a registered id no leaf hosts (``clm-dd4444``) — ``hosting_leaf`` None;
    * an id cited by two leaves (``clm-aa1111``) — lowest-sorted path wins;
    * an id whose declaring leaf sorts AFTER a citing leaf (``exp-ee1111``,
      declared in ``bench.md``, cited in ``aa-cites-bench.md``) — declaration
      wins over sort order;
    * a fenced example marker in a register, and a derived ``subtree-claims:``
      naming a stale id — neither may enter the inventory.
    """
    index_dir = kb_root / ".index"
    index_dir.mkdir(parents=True, exist_ok=True)
    for name in _INDEX_FILES:
        (index_dir / name).write_text("", encoding="utf-8")

    _write(
        kb_root / "claim-quality.md",
        "# Root register\n"
        "\n"
        "The entry shape, as documentation — the marker below is inside a fence\n"
        "and is not a real entry:\n"
        "\n"
        "```markdown\n"
        "## Example\n"
        "<!-- id: clm-xxxxxx -->\n"
        "```\n"
        "\n"
        "## Foundation A\n"
        "<!-- id: clm-aa1111 -->\n"
        "\n"
        "### Quality\n"
        "- confidence: 0.90\n"
        "\n"
        "## Registered But Unhosted\n"
        "<!-- id: clm-dd4444 -->\n"
        "\n"
        "### Quality\n"
        "- confidence: *pending*\n"
        "\n"
        "## Warrant One\n"
        "<!-- id: sup-ss1111 -->\n"
        "\n"
        "### Quality\n"
        "- quality: 0.70\n",
    )

    _write(
        kb_root / "domain" / "claim-quality.md",
        "# Domain register\n"
        "\n"
        "## Derived B\n"
        "<!-- id: clm-bb2222 -->\n"
        "\n"
        "### Quality\n"
        "- confidence: 0.60\n"
        "\n"
        "## Bench Experiment\n"
        "<!-- id: exp-ee1111 -->\n"
        "\n"
        "### Quality\n"
        "- confidence: 0.50\n",
    )

    _write(
        kb_root / "index.md",
        "[↑ Entry](entry-point.md)\n"
        "\n"
        "<!-- kb-frontmatter\n"
        "kind: index\n"
        "subtree-claims: [clm-aa1111, clm-zz9999]\n"
        "-->\n"
        "\n"
        "## Domain Index\n",
    )

    _write(
        kb_root / "domain" / "aa-cites-bench.md",
        "[↑ Domain](index.md)\n"
        "\n"
        "<!-- kb-frontmatter\n"
        "kind: leaf\n"
        "no-claim: narrative bridge\n"
        "experiments: [exp-ee1111]\n"
        "-->\n"
        "\n"
        "## Reader Pointing At The Bench\n",
    )

    _write(
        kb_root / "domain" / "aa-first.md",
        "[↑ Domain](index.md)\n"
        "\n"
        "<!-- kb-frontmatter\n"
        "kind: leaf\n"
        "claims: [clm-aa1111]\n"
        "-->\n"
        "\n"
        "## Foundation A\n",
    )

    _write(
        kb_root / "domain" / "bb-second.md",
        "[↑ Domain](index.md)\n"
        "\n"
        "<!-- kb-frontmatter\n"
        "kind: leaf\n"
        "claims: [clm-aa1111, clm-bb2222]\n"
        "-->\n"
        "\n"
        "## Derived B, Restating A\n",
    )

    _write(
        kb_root / "domain" / "bench.md",
        "[↑ Domain](index.md)\n"
        "\n"
        "<!-- kb-frontmatter\n"
        "kind: leaf\n"
        "exp-id: exp-ee1111\n"
        "status: run\n"
        "strengthens:\n"
        "  - clm-bb2222: 0.90\n"
        "exp-id: exp-ff2222\n"
        "status: pending\n"
        "strengthens:\n"
        "  - clm-aa1111: 0.40\n"
        "-->\n"
        "\n"
        "## Two Bench Experiments\n",
    )

    _write(
        kb_root / "domain" / "warrant.md",
        "[↑ Domain](index.md)\n"
        "\n"
        "<!-- kb-frontmatter\n"
        "kind: leaf\n"
        "sup-id: sup-ss1111\n"
        "supports:\n"
        "  - clm-aa1111: 0.50\n"
        "-->\n"
        "\n"
        "## Analytical Warrant\n",
    )


@pytest.fixture
def run3_shaped_repo(tmp_path: Path) -> Path:
    """A consuming repo (fake ``.git`` + ``kb-root/``) in the motivating state."""
    repo = tmp_path / "consumer"
    (repo / ".git").mkdir(parents=True)
    _write_run3_shaped_kb(repo / "kb-root")
    return repo


# ---------------------------------------------------------------------------
# scan_authored_ids
# ---------------------------------------------------------------------------


def test_index_is_empty_in_the_fixture(run3_shaped_repo: Path) -> None:
    """Guards the premise: every derived index file is present and zero-byte."""
    index_dir = run3_shaped_repo / "kb-root" / ".index"
    assert sorted(p.name for p in index_dir.iterdir()) == sorted(_INDEX_FILES)
    assert all(p.stat().st_size == 0 for p in index_dir.iterdir())


def test_scan_returns_the_full_inventory_with_an_empty_index(run3_shaped_repo: Path) -> None:
    inventory = kb_index_lib.scan_authored_ids(run3_shaped_repo / "kb-root")
    actual = {node_id: (r.kind, r.register_path, r.hosting_leaf) for node_id, r in inventory.items()}
    assert actual == EXPECTED_INVENTORY


def test_scan_finds_all_three_prefixes(run3_shaped_repo: Path) -> None:
    inventory = kb_index_lib.scan_authored_ids(run3_shaped_repo / "kb-root")
    assert {record.kind for record in inventory.values()} == set(kb_schema.ID_KINDS)


def test_scan_is_keyed_and_ordered_by_id(run3_shaped_repo: Path) -> None:
    inventory = kb_index_lib.scan_authored_ids(run3_shaped_repo / "kb-root")
    assert list(inventory) == sorted(EXPECTED_INVENTORY)


@pytest.mark.parametrize("excluded_id", ["clm-xxxxxx", "clm-zz9999"])
def test_scan_ignores_fenced_examples_and_derived_aggregates(run3_shaped_repo: Path, excluded_id: str) -> None:
    # clm-xxxxxx sits inside a register code fence; clm-zz9999 appears only in a
    # derived `subtree-claims:` roll-up. Neither is an authored node.
    assert excluded_id not in kb_index_lib.scan_authored_ids(run3_shaped_repo / "kb-root")


def test_scan_of_a_bare_kb_is_empty(tmp_path: Path) -> None:
    bare = tmp_path / "kb-root"
    (bare / ".index").mkdir(parents=True)
    assert kb_index_lib.scan_authored_ids(bare) == {}


def test_caller_partitions_unscored_ids_by_owning_register(run3_shaped_repo: Path) -> None:
    """The consumer shape phase-2.5's batching step is written against."""
    inventory = kb_index_lib.scan_authored_ids(run3_shaped_repo / "kb-root")
    by_register: dict[str | None, list[str]] = {}
    for node_id, record in inventory.items():
        if record.kind == "clm":
            by_register.setdefault(record.register_path, []).append(node_id)
    assert by_register == {
        "claim-quality.md": ["clm-aa1111", "clm-dd4444"],
        "domain/claim-quality.md": ["clm-bb2222"],
    }


# ---------------------------------------------------------------------------
# scan_duplicate_register_ids
# ---------------------------------------------------------------------------


def _append_entry(register: Path, node_id: str) -> None:
    register.write_text(
        register.read_text(encoding="utf-8") + f"\n## Restated\n<!-- id: {node_id} -->\n",
        encoding="utf-8",
    )


def test_a_clean_kb_has_no_duplicate_register_ids(run3_shaped_repo: Path) -> None:
    """Mentions are not keys: ``clm-aa1111`` is keyed once and named by four other files."""
    assert kb_index_lib.scan_duplicate_register_ids(run3_shaped_repo / "kb-root") == {}


def test_one_id_keyed_by_two_registers_is_reported_with_both(run3_shaped_repo: Path) -> None:
    """The question ``scan_authored_ids`` structurally cannot answer.

    Its return is keyed by id and holds one register per key, so a second entry
    for an id already keyed is dropped rather than reported — a collision reads
    as a clean inventory. That is the whole reason for a second scan.
    """
    kb_root = run3_shaped_repo / "kb-root"
    _append_entry(kb_root / "domain" / "claim-quality.md", "clm-aa1111")

    assert kb_index_lib.scan_duplicate_register_ids(kb_root) == {
        "clm-aa1111": ("claim-quality.md", "domain/claim-quality.md")
    }
    inventory = kb_index_lib.scan_authored_ids(kb_root)
    actual = {node_id: (r.kind, r.register_path, r.hosting_leaf) for node_id, r in inventory.items()}
    assert actual == EXPECTED_INVENTORY, "the inventory is unchanged, and silent about the collision"


def test_one_register_keying_an_id_twice_is_a_duplicate_too(run3_shaped_repo: Path) -> None:
    kb_root = run3_shaped_repo / "kb-root"
    _append_entry(kb_root / "claim-quality.md", "clm-aa1111")

    assert kb_index_lib.scan_duplicate_register_ids(kb_root) == {"clm-aa1111": ("claim-quality.md", "claim-quality.md")}


def test_a_fenced_example_is_not_a_duplicate_of_a_real_entry(run3_shaped_repo: Path) -> None:
    """The same exclusions the inventory uses: a fenced marker is documentation, not an entry."""
    kb_root = run3_shaped_repo / "kb-root"
    register = kb_root / "domain" / "claim-quality.md"
    register.write_text(
        register.read_text(encoding="utf-8") + "\n```markdown\n## Example\n<!-- id: clm-aa1111 -->\n```\n",
        encoding="utf-8",
    )

    assert kb_index_lib.scan_duplicate_register_ids(kb_root) == {}


def test_a_bare_kb_has_no_duplicate_register_ids(tmp_path: Path) -> None:
    bare = tmp_path / "kb-root"
    (bare / ".index").mkdir(parents=True)
    assert kb_index_lib.scan_duplicate_register_ids(bare) == {}


# ---------------------------------------------------------------------------
# The draw, against the inventory
# ---------------------------------------------------------------------------
#
# `mint_claim_ids` retired as an agent-facing command: an id is born inside
# the insert op that writes its entry, so a standalone CLI could only ever
# hand out ids no entry claims. What survives is the pairing this file has
# always been about — the draw in `kb_schema`, checked against the
# authored-Markdown inventory — exercised here as its callers now hold it, and
# covered end to end through the ops in `test_kb_write_ops.py`.

_DRAWS = 25


@pytest.mark.parametrize("kind", kb_schema.ID_KINDS)
def test_a_draw_avoids_every_authored_id_of_every_kind(run3_shaped_repo: Path, kind: str) -> None:
    authored = set(kb_index_lib.scan_authored_ids(run3_shaped_repo / "kb-root"))
    assert authored == set(EXPECTED_INVENTORY)
    grammar = re.compile(kb_schema.id_body(kind))

    minted: list[str] = []
    for _ in range(_DRAWS):
        minted.append(kb_schema.mint_id(kind, existing=authored | set(minted)))

    assert len(set(minted)) == _DRAWS
    assert all(grammar.fullmatch(node_id) for node_id in minted), minted
    assert authored.isdisjoint(minted)


def test_mint_rerolls_a_colliding_body(monkeypatch: pytest.MonkeyPatch) -> None:
    """Collision refusal, pinned: the first draw duplicates an authored id."""
    # Two candidate bodies, six characters each, drawn one character at a time.
    draws = iter("aa1111zz9999")
    monkeypatch.setattr(kb_schema.secrets, "choice", lambda _alphabet: next(draws))
    assert kb_schema.mint_id("clm", existing={"clm-aa1111"}) == "clm-zz9999"


def test_mint_rejects_an_unknown_kind() -> None:
    with pytest.raises(ValueError, match="unknown id kind"):
        kb_schema.mint_id("inv")
