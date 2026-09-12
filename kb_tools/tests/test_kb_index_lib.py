"""Unit tests for ``kb_index_lib`` against a synthetic fixture KB.

Run via the project's test target, or directly::

Or directly::

    python -m pytest kb_tools/tests/test_kb_index_lib.py

Tests are fully independent of live KB state. Every test runs against the
small, stable hand-built fixture under ``tests/fixtures/mini-kb/`` (its claim
graph is known exactly, so expected values are derivable from the fixture) or
against inline synthetic data. The live KB's own structural health is covered
by the verify target; nothing in this module reads or asserts on ``kb-root/``
proper. All tests are read-only and never mutate any file in the fixture or
the live KB.
"""

import json
import re
import shutil
import tempfile
import time
import unittest
from pathlib import Path

from kb_tools import kb_index_lib as lib
from kb_tools import kb_schema

_THIS_DIR = Path(__file__).resolve().parent

# The synthetic fixture KB — the stable graph behavioral tests run against.
# Lives entirely under ``tests/fixtures/mini-kb/``; this module never touches
# the live ``kb-root/`` tree.
_FIXTURE = _THIS_DIR / "fixtures" / "mini-kb"


class TestParseFrontmatter(unittest.TestCase):
    """parse_frontmatter scalar / list / boolean handling."""

    def test_missing_block_returns_none(self):
        self.assertIsNone(lib.parse_frontmatter("# Title\n\nNo frontmatter here."))

    def test_kind_leaf_field(self):
        text = "<!-- kb-frontmatter\nkind: leaf\n-->\n"
        fm = lib.parse_frontmatter(text)
        self.assertEqual(fm, {"kind": "leaf"})

    def test_claims_list(self):
        text = "<!-- kb-frontmatter\nkind: leaf\nclaims: [clm-aaa111, clm-bbb222, clm-ccc333]\n-->\n"
        fm = lib.parse_frontmatter(text)
        self.assertEqual(fm["kind"], "leaf")
        self.assertEqual(fm["claims"], ["clm-aaa111", "clm-bbb222", "clm-ccc333"])

    def test_no_claim_string(self):
        text = "<!-- kb-frontmatter\nkind: leaf\nno-claim: navigation only\n-->\n"
        fm = lib.parse_frontmatter(text)
        self.assertEqual(fm["no-claim"], "navigation only")

    def test_subtree_claims_list(self):
        text = "<!-- kb-frontmatter\nkind: index\nsubtree-claims: [clm-abc123, clm-def456]\n-->\n"
        fm = lib.parse_frontmatter(text)
        self.assertEqual(fm["subtree-claims"], ["clm-abc123", "clm-def456"])

    def test_path_stable_quoted_string(self):
        text = '<!-- kb-frontmatter\nkind: leaf\npath-stable: "ref label"\n-->\n'
        fm = lib.parse_frontmatter(text)
        self.assertEqual(fm["path-stable"], "ref label")

    def test_bootstrap_boolean_true(self):
        text = "<!-- kb-frontmatter\nkind: index\nbootstrap: true\n-->\n"
        fm = lib.parse_frontmatter(text)
        self.assertIs(fm["bootstrap"], True)

    def test_indented_closing_marker_ends_the_block(self):
        """An indented `-->` closes the block instead of swallowing the body.

        With the close anchored at column 0 the non-greedy body ran on to the
        next `-->` anywhere in the file — here the `claim-quality:` marker — so
        document prose parsed as frontmatter keys, and the `.sub("", text)`
        site deleted the body from the text every later check reads.
        """
        text = (
            "<!-- kb-frontmatter\nkind: leaf\nclaims: [clm-aaa111]\n  -->\n\n"
            "# Body\n\nprose\n\n"
            "<!-- claim-quality: clm-bbb222\nnote\n-->\n"
        )
        self.assertEqual(lib.parse_frontmatter(text), {"kind": "leaf", "claims": ["clm-aaa111"]})
        # The body survives frontmatter removal.
        self.assertIn("# Body", lib.FRONTMATTER_RE.sub("", text))

    def test_delimiter_is_single_sourced_by_the_writer_and_verify(self):
        """One compiled block delimiter, not three near-copies of it.

        The writing side is ``kb_write.store``: the emitter reaches the
        delimiter through its frontmatter-field splice rather than through a
        compiled pattern of its own.
        """
        from kb_tools import verify_kb_metadata
        from kb_tools.kb_write import store

        self.assertIs(store.FRONTMATTER_BLOCK, lib.FRONTMATTER_RE)
        self.assertIs(verify_kb_metadata.FRONTMATTER_BLOCK, lib.FRONTMATTER_RE)


class TestRegisterFenceScrubbing(unittest.TestCase):
    """Fence grammar as ``parse_claim_quality_file`` sees it.

    Each case writes a synthetic register whose *real* content is one entry,
    preceded by a documentation example that must not contribute ids.
    """

    ENTRY = (
        "## Alpha Claim\n\n<!-- id: clm-aaa111 -->\n\n" "### Quality\n\n- confidence: 0.7\n- solidity: 0.7 (settled)\n"
    )

    def _parse(self, body):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            path = root / "claim-quality.md"
            path.write_text(body, encoding="utf-8")
            return [e.id for e in lib.parse_claim_quality_file(path, root)]

    def test_tilde_fenced_example_is_scrubbed(self):
        body = (
            "# Register\n\n~~~markdown\n## Example Claim\n\n<!-- id: clm-fake99 -->\n\n"
            "### Quality\n\n- confidence: 0.9\n~~~\n\n" + self.ENTRY
        )
        self.assertEqual(self._parse(body), ["clm-aaa111"])

    def test_indented_closing_fence_does_not_empty_the_register(self):
        """The whole-register-returns-[] failure: a channel gone silently empty."""
        body = "# Register\n\n1. The shape is:\n\n```\n- confidence: 0.9\n  ```\n\n" + self.ENTRY
        self.assertEqual(self._parse(body), ["clm-aaa111"])

    def test_four_backtick_block_does_not_invert(self):
        body = "# Register\n\n````markdown\n```\n## Example Claim\n\n<!-- id: clm-fake99 -->\n" "````\n\n" + self.ENTRY
        self.assertEqual(self._parse(body), ["clm-aaa111"])

    def test_scanner_is_single_sourced(self):
        """refresh, verify and the register parser share one fence scanner."""
        from kb_tools import kb_links, verify_kb_metadata

        text = "````\n```\n````\nPROSE\n"
        self.assertEqual(lib._strip_code_fences(text), "\n".join(kb_links.blank_fenced_lines(text)))
        self.assertEqual(verify_kb_metadata.strip_code_fences(text), lib._strip_code_fences(text))


class TestParseClaimQualityFile(unittest.TestCase):
    """parse_claim_quality_file against the fixture's root claim-quality.md.

    The root register holds five claims: two no-dependency claims
    (clm-aa1111, clm-bb2222), a single-claim-dependency claim (clm-cc3333),
    a multi-dependency claim (clm-dd4444), and a framework-only-dependency
    claim (clm-ee5555).
    """

    @classmethod
    def setUpClass(cls):
        cls.path = _FIXTURE / "claim-quality.md"
        cls.entries = lib.parse_claim_quality_file(cls.path, _FIXTURE)
        cls.by_id = {e.id: e for e in cls.entries}

    def test_entry_count(self):
        # Five real claims; the fenced `<!-- id: -->` example is not counted.
        self.assertEqual(len(self.entries), 5)

    def test_no_dependency_claim_metadata(self):
        e = self.by_id["clm-aa1111"]
        self.assertIn("Foundation Claim A", e.title)
        self.assertEqual(e.confidence, 0.90)
        # The on-disk solidity line is deliberately stale (0.10); the parsed
        # entry carries exactly what the line says — it is not recomputed.
        self.assertEqual(e.solidity, 0.10)
        self.assertIsNotNone(e.build_status)
        # No depends-on bullets → no edges.
        self.assertEqual(e.depends_on, ())

    def test_claim_dependency_edge_carries_recorded_solidity(self):
        e = self.by_id["clm-cc3333"]
        targets = {edge.target: edge for edge in e.depends_on}
        self.assertIn("clm-aa1111", targets)
        edge = targets["clm-aa1111"]
        self.assertEqual(edge.source, "clm-cc3333")
        self.assertEqual(edge.target_kind, "claim")
        self.assertEqual(edge.target_solidity_recorded, 0.90)

    def test_strengthen_by_item_mentions_two_fixture_ids(self):
        # clm-dd4444's first strengthen-by item names both clm-aa1111 and
        # clm-bb2222 in its text.
        e = self.by_id["clm-dd4444"]
        joint = [sb for sb in e.strengthen_by if "clm-aa1111" in sb.mentioned_ids and "clm-bb2222" in sb.mentioned_ids]
        self.assertTrue(
            joint,
            "expected at least one strengthen-by item mentioning both ids",
        )

    def test_framework_dependency_claim_edges(self):
        # clm-ee5555 depends on a framework invariant and a framework axiom;
        # neither carries a recorded solidity.
        e = self.by_id["clm-ee5555"]
        kinds = sorted(edge.target_kind for edge in e.depends_on)
        self.assertEqual(kinds, ["axiom", "invariant"])
        for edge in e.depends_on:
            self.assertIsNone(edge.target_solidity_recorded)


class TestParseFrameworkNodes(unittest.TestCase):
    """parse_framework_nodes against the fixture's CLAUDE.md.

    The fixture declares four invariant headings (INVARIANT-S1, S2, S3, and
    the subsumed-tombstone S6) and four axiom bullets in the INVARIANT-S2
    section.
    """

    @classmethod
    def setUpClass(cls):
        cls.nodes = lib.parse_framework_nodes(_FIXTURE)
        cls.by_id = {n.id: n for n in cls.nodes}

    def test_invariant_count(self):
        invariants = [n for n in self.nodes if n.node_type == "invariant"]
        self.assertEqual(len(invariants), 4)

    def test_axiom_count(self):
        axioms = [n for n in self.nodes if n.node_type == "axiom"]
        self.assertEqual(len(axioms), 4)
        self.assertEqual(
            sorted(a.id for a in axioms),
            ["axiom-1", "axiom-2", "axiom-3", "axiom-4"],
        )

    def test_tombstone_invariant_present(self):
        # The subsumed S6 heading is still a real heading; a reference must
        # resolve, so the node must exist.
        self.assertIn("INVARIANT-S6", self.by_id)
        self.assertEqual(self.by_id["INVARIANT-S6"].node_type, "invariant")

    def test_invariant_anchor_is_own_heading_slug(self):
        s2 = self.by_id["INVARIANT-S2"]
        self.assertEqual(s2.canonical_path, "CLAUDE.md")
        self.assertEqual(s2.canonical_anchor, "invariant-s2-core-axiom-numbering")
        self.assertEqual(s2.title, "Core Axiom numbering")

    def test_axioms_share_the_s2_anchor(self):
        # All four axioms point at the INVARIANT-S2 heading slug.
        s2_anchor = self.by_id["INVARIANT-S2"].canonical_anchor
        for num in (1, 2, 3, 4):
            self.assertEqual(self.by_id[f"axiom-{num}"].canonical_anchor, s2_anchor)
            self.assertEqual(self.by_id[f"axiom-{num}"].canonical_path, "CLAUDE.md")


class TestParseFrameworkNodesTolerance(unittest.TestCase):
    """Axiom generality and absent-section tolerance.

    Axiom numbering is not capped (any ``- Axiom N:`` bullet parses), and a
    ``CLAUDE.md`` with no INVARIANT-S2 section, no axiom bullets, or no
    content at all yields empty results — never an exception.
    """

    def _parse(self, text: str) -> list:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "CLAUDE.md").write_text(text, encoding="utf-8")
            return lib.parse_framework_nodes(root)

    def test_axiom_numbers_above_four_parse(self):
        text = (
            "### INVARIANT-S2: Core Axiom numbering\n\n"
            "- Axiom 1: **First** — one.\n"
            "- Axiom 12: **Twelfth** — twelve.\n"
        )
        nodes = self._parse(text)
        axioms = sorted(n.id for n in nodes if n.node_type == "axiom")
        self.assertEqual(axioms, ["axiom-1", "axiom-12"])

    def test_no_axiom_bullets_yields_no_axiom_nodes(self):
        text = "### INVARIANT-S1: Only rule\n\nProse, no axiom bullets.\n"
        nodes = self._parse(text)
        self.assertEqual([n for n in nodes if n.node_type == "axiom"], [])
        self.assertEqual(len(nodes), 1)

    def test_axioms_without_s2_heading_get_empty_anchor(self):
        text = "- Axiom 3: **Loose** — declared with no INVARIANT-S2 heading.\n"
        nodes = self._parse(text)
        self.assertEqual(len(nodes), 1)
        self.assertEqual(nodes[0].id, "axiom-3")
        self.assertEqual(nodes[0].node_type, "axiom")
        self.assertEqual(nodes[0].canonical_anchor, "")

    def test_empty_claude_md_yields_empty_list(self):
        self.assertEqual(self._parse(""), [])

    def test_absent_claude_md_yields_empty_list(self):
        with tempfile.TemporaryDirectory() as td:
            self.assertEqual(lib.parse_framework_nodes(Path(td)), [])


class TestDependsOnFrameworkEdges(unittest.TestCase):
    """Head-extraction depends-on parser: framework targets and multi-token.

    Edge-count and shape assertions run against the fixture's known graph:
    9 ``relation:"depends"`` edges total — 5 claim-target, 4 framework-target.
    (4 claim-target + 4 framework-target are sourced by claims; the 5th
    claim-target depends edge is the support sup-dep001's OWN dependency on
    clm-aa1111. The fixture also carries ``strengthens`` edges from experiments
    and ``supports`` edges from support nodes; both are scoped out of these
    depends-edge counts.)
    """

    @classmethod
    def setUpClass(cls):
        cls.state = lib.discover_kb(_FIXTURE, diagnostic_stream=None)
        cls.records = lib.build_depends_on_records(cls.state)
        cls.depends = [r for r in cls.records if r["relation"] == "depends"]

    def test_total_edge_count(self):
        self.assertEqual(len(self.depends), 9)

    def test_claim_edge_count(self):
        claim_edges = [r for r in self.depends if r["target_kind"] == "claim"]
        self.assertEqual(len(claim_edges), 5)

    def test_framework_edge_count(self):
        fw = [r for r in self.depends if r["target_kind"] != "claim"]
        self.assertEqual(len(fw), 4)

    def test_target_kind_matches_target_shape(self):
        for r in self.records:
            kind = r["target_kind"]
            target = r["target"]
            if kind == "claim":
                self.assertTrue(target.startswith("clm-"))
            elif kind == "invariant":
                self.assertTrue(target.startswith("INVARIANT-"))
            elif kind == "axiom":
                self.assertTrue(target.startswith("axiom-"))
            else:
                self.fail(f"unexpected target_kind {kind!r}")

    def test_framework_targets_have_null_solidity(self):
        for r in self.records:
            if r["target_kind"] != "claim":
                self.assertIsNone(r["target_solidity_recorded"])

    def test_double_invariant_s2_edges_from_one_owner(self):
        # clm-hh8888 declares INVARIANT-S2 via two separate bullets with
        # different context — both edge records must survive.
        s2_edges = [r for r in self.records if r["target"] == "INVARIANT-S2" and r["source"] == "clm-hh8888"]
        self.assertEqual(len(s2_edges), 2)
        contexts = sorted(r["context"] or "" for r in s2_edges)
        self.assertEqual(len(set(contexts)), 2, "expected two distinct contexts")

    def test_sorted_by_source_target_context(self):
        keys = [(r["source"], r["target"], r["context"] or "") for r in self.records]
        self.assertEqual(keys, sorted(keys))

    def test_non_token_none_line_yields_zero_edges(self):
        # `- none entry-local — Axiom 4 is framework input...` — the head is
        # `none entry-local` (truncated at the em-dash), no recognized token.
        line = "  - none entry-local — Axiom 4 is framework input; the " "case identification is structural"
        edges = lib._parse_depends_on_line(line, "clm-test01")
        self.assertEqual(edges, [])

    def test_multi_token_bullet_yields_one_edge_per_token(self):
        line = "  - INVARIANT-S2 / Axiom 4 (saturation kernel — for the sketch)"
        edges = lib._parse_depends_on_line(line, "clm-test01")
        kinds = sorted(e.target_kind for e in edges)
        self.assertEqual(kinds, ["axiom", "invariant"])
        for e in edges:
            self.assertEqual(e.context, "saturation kernel — for the sketch")

    def test_plain_claim_bullet_yields_one_claim_edge(self):
        line = "  - clm-unk0bd — Some Title (solidity 0.4)"
        edges = lib._parse_depends_on_line(line, "clm-test01")
        self.assertEqual(len(edges), 1)
        self.assertEqual(edges[0].target, "clm-unk0bd")
        self.assertEqual(edges[0].target_kind, "claim")
        self.assertEqual(edges[0].target_solidity_recorded, 0.4)


class TestParseLeaf(unittest.TestCase):
    """parse_leaf against the fixture's leaves."""

    def test_multi_claim_leaf(self):
        path = _FIXTURE / "common" / "leaf-multi.md"
        leaf = lib.parse_leaf(path, _FIXTURE)
        self.assertIsNotNone(leaf)
        self.assertEqual(leaf.kind, "leaf")
        self.assertEqual(
            set(leaf.claims),
            {"clm-aa1111", "clm-bb2222", "clm-cc3333", "clm-dd4444"},
        )
        # A multi-claim leaf carries a Tier-2 marker for every id.
        self.assertEqual(set(leaf.tier2_marked), set(leaf.claims))

    def test_single_claim_leaf_may_have_empty_tier2(self):
        path = _FIXTURE / "common" / "leaf-single.md"
        leaf = lib.parse_leaf(path, _FIXTURE)
        self.assertIsNotNone(leaf)
        self.assertEqual(leaf.claims, ("clm-aa1111",))
        # The fixture's single-claim leaf carries no inline marker — Tier-2
        # is not required for single-claim leaves.
        self.assertEqual(leaf.tier2_marked, frozenset())

    def test_no_claim_leaf(self):
        path = _FIXTURE / "common" / "leaf-noclaim.md"
        leaf = lib.parse_leaf(path, _FIXTURE)
        self.assertIsNotNone(leaf)
        self.assertEqual(leaf.claims, ())
        self.assertIsNotNone(leaf.no_claim_reason)


class TestDiscoverKb(unittest.TestCase):
    """discover_kb against the synthetic fixture."""

    @classmethod
    def setUpClass(cls):
        cls.state = lib.discover_kb(_FIXTURE, diagnostic_stream=None)

    def test_claim_count_matches_canonical_extraction(self):
        # The fixture's root claim-quality.md carries one placeholder
        # `<!-- id: clm-xxxxxx -->` inside a fenced ``markdown`` code block in
        # the Quality Convention example. The library strips code fences
        # before extracting canonical IDs, so that example is NOT counted.
        # Five real entries in the root register + eleven in common/ = 16
        # (common/ adds clm-co1111, the co-hosted claim+experiment leaf, plus
        # the seven support-beneficiary claims clm-sb1111..clm-sb7777). Support
        # entries (sup-*) are NOT claims and are not counted here.
        self.assertEqual(len(self.state.claim_entries), 16)

    def test_support_node_count(self):
        # Six support nodes: sup-free01 (free-standing),
        # sup-dep001 (own deps), sup-pend01 (pending quality), sup-coh001
        # (co-hosted with a claim on one leaf), plus sup-mlt001 + sup-mlt002 —
        # TWO supports hosted on ONE container (multi-sup; no one-per-leaf cap).
        self.assertEqual(len(self.state.supports), 6)

    def test_multi_sup_container_hosts_two_supports(self):
        # A single container (leaf-multi-sup.md) originates BOTH sup-mlt001 and
        # sup-mlt002 — N sup node-bodies on one leaf (the container
        # model). Both share the leaf's canonical home.
        by_id = {s.id: s for s in self.state.supports}
        self.assertIn("sup-mlt001", by_id)
        self.assertIn("sup-mlt002", by_id)
        self.assertEqual(by_id["sup-mlt001"].canonical_path, "common/leaf-multi-sup.md")
        self.assertEqual(by_id["sup-mlt002"].canonical_path, "common/leaf-multi-sup.md")
        # sup-mlt002's second beneficiary edge carries the pending sentinel.
        by_claim = dict(by_id["sup-mlt002"].supports)
        self.assertIs(by_claim["clm-sb6666"], lib.PENDING_FRACTION)
        self.assertEqual(by_claim["clm-sb7777"], 0.50)

    def test_every_leaf_with_claims_present(self):
        # Build the set of leaf paths from a parallel walk and intersect.
        from kb_tools.kb_index_lib import kb_files

        expected: set[str] = set()
        for p in kb_files(_FIXTURE):
            leaf = lib.parse_leaf(p, _FIXTURE)
            if leaf is not None and leaf.claims:
                expected.add(leaf.path)
        actual = {leaf.path for leaf in self.state.leaves if leaf.claims}
        self.assertEqual(actual, expected)

    def test_indexes_count_positive(self):
        self.assertGreater(len(self.state.indexes), 0)
        # And both an entry-point and an index are discovered.
        kinds = {idx.kind for idx in self.state.indexes}
        self.assertIn("entry-point", kinds)
        self.assertIn("index", kinds)


class TestBuildClaimsRecords(unittest.TestCase):
    """build_claims_records: length, ordering, key order, derived fields."""

    @classmethod
    def setUpClass(cls):
        cls.state = lib.discover_kb(_FIXTURE, diagnostic_stream=None)
        cls.records = lib.build_claims_records(cls.state)
        cls.by_id = {r["id"]: r for r in cls.records}

    def test_length_is_union_of_all_node_types(self):
        # claims.jsonl is a type-tagged union: claims + framework nodes +
        # experiment nodes + support nodes.
        self.assertEqual(
            len(self.records),
            len(self.state.claim_entries)
            + len(self.state.framework_nodes)
            + len(self.state.experiments)
            + len(self.state.supports),
        )

    def test_node_type_distribution(self):
        from collections import Counter

        counts = Counter(r["node_type"] for r in self.records)
        # 16 claims + 2 experiments + 6 support + 4 invariants + 4 axioms = 32.
        self.assertEqual(counts["claim"], 16)
        self.assertEqual(counts["experiment"], 2)
        self.assertEqual(counts["support"], 6)
        self.assertEqual(counts["invariant"], 4)
        self.assertEqual(counts["axiom"], 4)
        self.assertEqual(len(self.records), 32)

    def test_support_record_documented_key_order(self):
        expected_keys = [
            "node_type",
            "id",
            "title",
            "canonical_path",
            "canonical_anchor",
            "quality",
            "solidity",
        ]
        sup_recs = [r for r in self.records if r["node_type"] == "support"]
        self.assertEqual(len(sup_recs), 6)
        for rec in sup_recs:
            self.assertEqual(list(rec.keys()), expected_keys)

    def test_support_sorts_last_after_invariant(self):
        # Sort key (node_type, id): support sorts AFTER invariant
        # (ASCII: axiom < claim < experiment < invariant < support).
        order = [r["node_type"] for r in self.records]
        last_invariant = max(i for i, t in enumerate(order) if t == "invariant")
        first_support = min(i for i, t in enumerate(order) if t == "support")
        self.assertLess(last_invariant, first_support)

    def test_sorted_by_node_type_then_id(self):
        keys = [(r["node_type"], r["id"]) for r in self.records]
        self.assertEqual(keys, sorted(keys))

    def test_claim_record_documented_key_order(self):
        expected_keys = [
            "node_type",
            "id",
            "title",
            "canonical_path",
            "canonical_anchor",
            "confidence",
            "derivation_solidity",
            "experimental_solidity",
            "solidity",
            "build_status",
            "build_band",
            "rationale",
            "depends_on_count",
            "strengthen_by_count",
            "citation_count",
        ]
        claim_recs = [r for r in self.records if r["node_type"] == "claim"]
        self.assertEqual(len(claim_recs), 16)
        for rec in claim_recs:
            self.assertEqual(list(rec.keys()), expected_keys)

    def test_framework_record_has_exactly_five_fields(self):
        expected_keys = [
            "node_type",
            "id",
            "title",
            "canonical_path",
            "canonical_anchor",
        ]
        fw_recs = [r for r in self.records if r["node_type"] in ("invariant", "axiom")]
        self.assertEqual(len(fw_recs), 8)
        for rec in fw_recs:
            self.assertEqual(list(rec.keys()), expected_keys)
            self.assertEqual(rec["canonical_path"], "CLAUDE.md")

    def test_build_band_derived(self):
        # clm-aa1111 computed solidity 0.90 -> ok-to-build.
        self.assertEqual(self.by_id["clm-aa1111"]["build_band"], "ok-to-build")
        # clm-bb2222 solidity 0.60 -> input-only.
        self.assertEqual(self.by_id["clm-bb2222"]["build_band"], "input-only")
        # clm-cc3333 solidity 0.80 -> ok-with-caveats.
        self.assertEqual(self.by_id["clm-cc3333"]["build_band"], "ok-with-caveats")

    def test_solidity_field_is_computed_not_parsed(self):
        # claims.jsonl solidity must match compute_solidity, NOT the value
        # parsed off the claim-quality.md solidity line. clm-aa1111's on-disk
        # line is the stale 0.10; its record must carry the computed 0.90.
        sol = lib.compute_solidity(self.state.claim_entries, self.state.experiments)
        for cid in ("clm-aa1111", "clm-cc3333", "clm-dd4444", "clm-ee5555"):
            self.assertEqual(self.by_id[cid]["solidity"], sol[cid])
        self.assertEqual(self.by_id["clm-aa1111"]["solidity"], 0.90)
        # clm-dd4444 is a multi-dep entry: min(0.90,0.90,0.60)=0.60, build-status follows.
        self.assertEqual(self.by_id["clm-dd4444"]["solidity"], 0.60)
        self.assertEqual(
            self.by_id["clm-dd4444"]["build_status"],
            "use as input only, don't build deeper",
        )

    def test_pending_confidence_claim_has_null_derived_fields(self):
        # clm-ff6666 confidence is *pending* → solidity uncomputable → null.
        rec = self.by_id["clm-ff6666"]
        self.assertIsNone(rec["confidence"])
        self.assertIsNone(rec["solidity"])
        self.assertIsNone(rec["build_status"])

    def test_experiment_rescued_claim_has_experimental_solidity(self):
        # clm-gg7777 has a pending derivation (dependency clm-ff6666 pending),
        # but a `run` experiment (exp-bench1) strengthens it at 0.80 → its
        # experimental branch is the only non-null branch, so final solidity
        # is RESCUED to 0.80 (the max-branch).
        rec = self.by_id["clm-gg7777"]
        self.assertEqual(rec["confidence"], 0.95)
        self.assertIsNone(rec["derivation_solidity"])
        self.assertEqual(rec["experimental_solidity"], 0.80)
        self.assertEqual(rec["solidity"], 0.80)
        self.assertEqual(rec["build_status"], "ok to build on, see caveats")

    def test_counts_are_accurate(self):
        # clm-cc3333 depends on clm-aa1111 (1 edge).
        self.assertEqual(self.by_id["clm-cc3333"]["depends_on_count"], 1)
        # clm-dd4444 has 2 strengthen-by items.
        self.assertEqual(self.by_id["clm-dd4444"]["strengthen_by_count"], 2)
        # clm-aa1111 is cited by the multi-claim and single-claim leaves.
        self.assertEqual(self.by_id["clm-aa1111"]["citation_count"], 2)


class TestBuildDependsOnRecords(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.state = lib.discover_kb(_FIXTURE, diagnostic_stream=None)
        cls.records = lib.build_depends_on_records(cls.state)

    def test_claim_edge_records_recorded_solidity(self):
        edges = [r for r in self.records if r["source"] == "clm-cc3333" and r["target"] == "clm-aa1111"]
        self.assertEqual(len(edges), 1)
        self.assertEqual(edges[0]["target_solidity_recorded"], 0.90)

    def test_sorted_by_source_then_target(self):
        keys = [(r["source"], r["target"]) for r in self.records]
        self.assertEqual(keys, sorted(keys))

    def test_no_dependency_claim_produces_zero_edges(self):
        # clm-aa1111 has no depends-on bullets; it must produce no edges.
        from_aa = [r for r in self.records if r["source"] == "clm-aa1111"]
        self.assertEqual(from_aa, [])


class TestBuildStrengthenByRecords(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.state = lib.discover_kb(_FIXTURE, diagnostic_stream=None)
        cls.records = lib.build_strengthen_by_records(cls.state)

    def test_sorted_by_claim_id_then_item_idx(self):
        keys = [(r["claim_id"], r["item_idx"]) for r in self.records]
        self.assertEqual(keys, sorted(keys))

    def test_item_idx_contiguous_per_claim(self):
        by_claim: dict[str, list[int]] = {}
        for r in self.records:
            by_claim.setdefault(r["claim_id"], []).append(r["item_idx"])
        for claim_id, indices in by_claim.items():
            self.assertEqual(
                indices,
                list(range(len(indices))),
                f"{claim_id}: indices {indices} not contiguous starting from 0",
            )


class TestBuildCitesRecords(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.state = lib.discover_kb(_FIXTURE, diagnostic_stream=None)
        cls.records = lib.build_cites_records(cls.state)

    def test_sorted_by_claim_id_then_leaf_path(self):
        keys = [(r["claim_id"], r["leaf_path"]) for r in self.records]
        self.assertEqual(keys, sorted(keys))

    def test_total_edges_matches_leaf_claim_sum(self):
        expected = sum(len(leaf.claims) for leaf in self.state.leaves)
        self.assertEqual(len(self.records), expected)


class TestBuildSubtreeAggregateRecords(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.state = lib.discover_kb(_FIXTURE, diagnostic_stream=None)
        cls.records = lib.build_subtree_aggregate_records(cls.state)

    def test_one_record_per_index_or_entry_point(self):
        self.assertEqual(len(self.records), len(self.state.indexes))

    def test_subtree_claims_sorted_and_unique(self):
        for r in self.records:
            ids = r["subtree_claims"]
            self.assertEqual(ids, sorted(ids))
            self.assertEqual(len(ids), len(set(ids)))


class TestDeterminism(unittest.TestCase):
    def test_build_all_records_byte_identical(self):
        state = lib.discover_kb(_FIXTURE, diagnostic_stream=None)
        first = lib.build_all_records(state)
        second = lib.build_all_records(state)
        self.assertEqual(set(first.keys()), set(second.keys()))
        for key in first:
            rows_a = [json.dumps(r, ensure_ascii=False, sort_keys=False) for r in first[key]]
            rows_b = [json.dumps(r, ensure_ascii=False, sort_keys=False) for r in second[key]]
            self.assertEqual(rows_a, rows_b, f"non-deterministic output for {key}")


class TestJsonlIo(unittest.TestCase):
    def test_round_trip(self):
        records = [
            {"id": "abc123", "value": 1, "list": ["a", "b"]},
            {"id": "def456", "value": None, "list": []},
        ]
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "out.jsonl"
            lib.write_jsonl(p, records)
            roundtripped = lib.read_jsonl(p)
        self.assertEqual(roundtripped, records)

    def test_write_is_byte_identical_across_calls(self):
        records = [
            {"a": 1, "b": "two", "c": [1, 2, 3]},
            {"a": 4, "b": "five", "c": []},
        ]
        with tempfile.TemporaryDirectory() as tmp:
            p1 = Path(tmp) / "first.jsonl"
            p2 = Path(tmp) / "second.jsonl"
            lib.write_jsonl(p1, records)
            lib.write_jsonl(p2, records)
            self.assertEqual(p1.read_text(encoding="utf-8"), p2.read_text(encoding="utf-8"))

    def test_empty_records_writes_empty_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "empty.jsonl"
            lib.write_jsonl(p, [])
            self.assertEqual(p.read_text(encoding="utf-8"), "")
            self.assertEqual(lib.read_jsonl(p), [])

    def test_malformed_jsonl_raises(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "bad.jsonl"
            p.write_text('{"ok": 1}\nnot-json\n', encoding="utf-8")
            with self.assertRaises(ValueError):
                lib.read_jsonl(p)


def _edge(source, target, kind="claim", recorded=None):
    """Build a depends DependsOnEdge for synthetic-graph tests."""
    return lib.DependsOnEdge(
        source=source,
        target=target,
        relation="depends",
        target_kind=kind,
        target_solidity_recorded=recorded,
        strength=None,
        context=None,
    )


def _claim(cid, confidence, depends_on=()):
    """Build a minimal ClaimEntry for synthetic compute_solidity tests."""
    return lib.ClaimEntry(
        id=cid,
        title=cid,
        canonical_path="test/claim-quality.md",
        canonical_anchor=cid,
        confidence=confidence,
        solidity=None,
        build_status=None,
        rationale="",
        depends_on=tuple(depends_on),
        strengthen_by=(),
    )


class TestRoundHalfUp(unittest.TestCase):
    """round_half_up_2dp rounds AWAY from zero at the 0.005 boundary."""

    def test_boundary_rounds_up(self):
        # Banker's rounding (Python round()) would give 0.12 for 0.125.
        self.assertEqual(lib.round_half_up_2dp(0.125), 0.13)
        self.assertEqual(lib.round_half_up_2dp(0.005), 0.01)
        self.assertEqual(lib.round_half_up_2dp(0.015), 0.02)

    def test_below_boundary_rounds_down(self):
        self.assertEqual(lib.round_half_up_2dp(0.1249), 0.12)
        self.assertEqual(lib.round_half_up_2dp(0.324), 0.32)

    def test_lv3uw1_boundary_case(self):
        # 0.65 × 0.50 = 0.325 → round-half-up → 0.33.
        self.assertEqual(lib.round_half_up_2dp(0.65 * 0.50), 0.33)


class TestBuildStatusPhrase(unittest.TestCase):
    """build_status_phrase maps solidity bands to legend phrases."""

    def test_band_boundaries(self):
        self.assertEqual(lib.build_status_phrase(1.00), "ok to build on")
        self.assertEqual(lib.build_status_phrase(0.85), "ok to build on")
        self.assertEqual(lib.build_status_phrase(0.84), "ok to build on, see caveats")
        self.assertEqual(lib.build_status_phrase(0.65), "ok to build on, see caveats")
        self.assertEqual(
            lib.build_status_phrase(0.45),
            "use as input only, don't build deeper",
        )
        self.assertEqual(lib.build_status_phrase(0.20), "do not build on, rework needed")
        self.assertEqual(lib.build_status_phrase(0.19), "refuted, do not use")
        self.assertEqual(lib.build_status_phrase(0.0), "refuted, do not use")

    def test_none_returns_none(self):
        self.assertIsNone(lib.build_status_phrase(None))


class TestComputeSolidity(unittest.TestCase):
    """compute_solidity over synthetic graphs: the core derivation."""

    def test_no_dependencies_equals_confidence(self):
        sol = lib.compute_solidity([_claim("clm-aaaaaa", 0.85)])
        self.assertEqual(sol["clm-aaaaaa"], 0.85)

    def test_clean_two_link_chain_is_weakest_link_not_product(self):
        # THE distinguishing case for the min model: a clean 2-link chain
        # q=0.9 → dep=0.9 yields 0.9 (the weakest link), NOT 0.81 (the product).
        # Under multiplication this clean derivation would decay purely because
        # it has one extra link; min keeps it at its honest floor.
        a = _claim("clm-aaaaaa", 0.90)
        b = _claim("clm-bbbbbb", 0.90, [_edge("clm-bbbbbb", "clm-aaaaaa")])
        sol = lib.compute_solidity([a, b])
        self.assertEqual(sol["clm-bbbbbb"], 0.90)

    def test_solidity_is_refactor_invariant_under_node_splitting(self):
        # Granularity invariance: splitting one derivation step into two
        # same-quality steps must NOT change the endpoint's solidity. A single
        # 0.90 leaf and a 3-node chain of identical 0.90 confidences both end at
        # 0.90 under min; under a product they would diverge (0.90 vs 0.729),
        # which is the bookkeeping artifact this model eliminates.
        single = lib.compute_solidity([_claim("clm-solo01", 0.90)])

        n1 = _claim("clm-chain1", 0.90)
        n2 = _claim("clm-chain2", 0.90, [_edge("clm-chain2", "clm-chain1")])
        n3 = _claim("clm-chain3", 0.90, [_edge("clm-chain3", "clm-chain2")])
        chained = lib.compute_solidity([n1, n2, n3])

        self.assertEqual(single["clm-solo01"], 0.90)
        self.assertEqual(chained["clm-chain3"], single["clm-solo01"])
        # Every node along the clean chain holds the same value — no decay.
        self.assertEqual(chained["clm-chain1"], 0.90)
        self.assertEqual(chained["clm-chain2"], 0.90)

    def test_single_claim_dependency(self):
        a = _claim("clm-aaaaaa", 0.75)
        b = _claim("clm-bbbbbb", 0.55, [_edge("clm-bbbbbb", "clm-aaaaaa")])
        sol = lib.compute_solidity([a, b])
        self.assertEqual(sol["clm-aaaaaa"], 0.75)
        # min(0.55, 0.75) = 0.55 — weakest link is b's own confidence.
        self.assertEqual(sol["clm-bbbbbb"], 0.55)

    def test_framework_dependency_contributes_one(self):
        # An entry depending only on framework nodes → solidity == confidence.
        e = _claim(
            "clm-aaaaaa",
            0.90,
            [
                _edge("clm-aaaaaa", "INVARIANT-S2", kind="invariant"),
                _edge("clm-aaaaaa", "axiom-4", kind="axiom"),
            ],
        )
        sol = lib.compute_solidity([e])
        self.assertEqual(sol["clm-aaaaaa"], 0.90)

    def test_min_over_multiple_dependencies(self):
        a = _claim("clm-aaaaaa", 0.41)  # leaf, solidity 0.41
        b = _claim("clm-bbbbbb", 0.28)  # leaf, solidity 0.28
        c = _claim(
            "clm-cccccc",
            0.85,
            [_edge("clm-cccccc", "clm-aaaaaa"), _edge("clm-cccccc", "clm-bbbbbb")],
        )
        sol = lib.compute_solidity([a, b, c])
        # min(0.85, 0.41, 0.28) = 0.28 — weakest link in the cone.
        self.assertEqual(sol["clm-cccccc"], 0.28)

    def test_framework_and_claim_dependency_mixed(self):
        a = _claim("clm-aaaaaa", 0.50)
        b = _claim(
            "clm-bbbbbb",
            0.80,
            [
                _edge("clm-bbbbbb", "clm-aaaaaa"),
                _edge("clm-bbbbbb", "INVARIANT-S2", kind="invariant"),
            ],
        )
        sol = lib.compute_solidity([a, b])
        # min(0.80 [local], 0.50 [claim dep], 1.0 [framework]) = 0.50
        self.assertEqual(sol["clm-bbbbbb"], 0.50)

    def test_propagation_uses_dependency_final(self):
        # Each claim's final feeds its dependents: the weakest link propagates
        # down the chain. a: 0.40 → leaf. b: min(0.90, 0.40) = 0.40. c depends
        # on b: min(0.95, 0.40) = 0.40 — the chain is gated by the weakest node,
        # not decayed by repeated multiplication.
        a = _claim("clm-aaaaaa", 0.40)
        b = _claim("clm-bbbbbb", 0.90, [_edge("clm-bbbbbb", "clm-aaaaaa")])
        c = _claim("clm-cccccc", 0.95, [_edge("clm-cccccc", "clm-bbbbbb")])
        sol = lib.compute_solidity([a, b, c])
        self.assertEqual(sol["clm-bbbbbb"], 0.40)
        self.assertEqual(sol["clm-cccccc"], 0.40)

    def test_pending_confidence_omitted(self):
        # An entry with confidence None is uncomputable — omitted from output.
        a = _claim("clm-aaaaaa", None)
        b = _claim("clm-bbbbbb", 0.85)
        sol = lib.compute_solidity([a, b])
        self.assertNotIn("clm-aaaaaa", sol)
        self.assertIn("clm-bbbbbb", sol)

    def test_entry_depending_on_pending_claim_omitted(self):
        # A numeric entry whose dependency is pending cannot be scored.
        a = _claim("clm-aaaaaa", None)
        b = _claim("clm-bbbbbb", 0.85, [_edge("clm-bbbbbb", "clm-aaaaaa")])
        sol = lib.compute_solidity([a, b])
        self.assertNotIn("clm-bbbbbb", sol)

    def test_cycle_raises(self):
        a = _claim("clm-aaaaaa", 0.80, [_edge("clm-aaaaaa", "clm-bbbbbb")])
        b = _claim("clm-bbbbbb", 0.80, [_edge("clm-bbbbbb", "clm-aaaaaa")])
        with self.assertRaises(lib.SolidityCycleError) as ctx:
            lib.compute_solidity([a, b])
        self.assertEqual(set(ctx.exception.cycle_members), {"clm-aaaaaa", "clm-bbbbbb"})

    def test_self_loop_raises(self):
        a = _claim("clm-aaaaaa", 0.80, [_edge("clm-aaaaaa", "clm-aaaaaa")])
        with self.assertRaises(lib.SolidityCycleError):
            lib.compute_solidity([a])


class TestSolidityPendingPropagation(unittest.TestCase):
    """The hard rule: ``*pending*`` propagates transitively, like NaN.

    A claim's solidity is ``*pending*`` (omitted from ``compute_solidity``'s
    result) if its confidence is ``*pending*`` OR any dependency's solidity is
    ``*pending*`` — regardless of its own local confidence. Framework-node
    dependencies are never pending.
    """

    def test_direct_dependency_on_pending_claim_is_pending(self):
        # A (numeric) depends directly on P (pending) → A is pending.
        p = _claim("clm-pppppp", None)
        a = _claim("clm-aaaaaa", 0.85, [_edge("clm-aaaaaa", "clm-pppppp")])
        sol = lib.compute_solidity([p, a])
        self.assertNotIn("clm-pppppp", sol)
        self.assertNotIn("clm-aaaaaa", sol)

    def test_transitive_propagation_through_numeric_chain(self):
        # A (numeric) → B (numeric) → P (pending). Both A and B are pending:
        # pending-ness propagates the full length of the dependency chain.
        p = _claim("clm-pppppp", None)
        b = _claim("clm-bbbbbb", 0.90, [_edge("clm-bbbbbb", "clm-pppppp")])
        a = _claim("clm-aaaaaa", 0.90, [_edge("clm-aaaaaa", "clm-bbbbbb")])
        sol = lib.compute_solidity([p, b, a])
        self.assertNotIn("clm-bbbbbb", sol)
        self.assertNotIn("clm-aaaaaa", sol)

    def test_pending_regardless_of_local_confidence(self):
        # confidence 1.0 does NOT rescue a claim that depends on a pending
        # one — "regardless of local confidence" is load-bearing.
        p = _claim("clm-pppppp", None)
        a = _claim("clm-aaaaaa", 1.0, [_edge("clm-aaaaaa", "clm-pppppp")])
        sol = lib.compute_solidity([p, a])
        self.assertNotIn("clm-aaaaaa", sol)

    def test_framework_only_dependency_is_not_pending(self):
        # Framework nodes (invariant / axiom) are solidity-1.0 bedrock, never
        # pending → a claim depending only on them is NOT pending.
        a = _claim(
            "clm-aaaaaa",
            0.77,
            [
                _edge("clm-aaaaaa", "INVARIANT-S2", kind="invariant"),
                _edge("clm-aaaaaa", "axiom-3", kind="axiom"),
            ],
        )
        sol = lib.compute_solidity([a])
        self.assertIn("clm-aaaaaa", sol)
        self.assertEqual(sol["clm-aaaaaa"], 0.77)  # solidity == confidence

    def test_one_pending_dep_poisons_a_numeric_dep_mix(self):
        # A claim with one numeric claim-dep AND one pending claim-dep is
        # pending — the pending dependency poisons the result.
        good = _claim("clm-gggggg", 0.60)
        pend = _claim("clm-pppppp", None)
        a = _claim(
            "clm-aaaaaa",
            0.95,
            [
                _edge("clm-aaaaaa", "clm-gggggg"),
                _edge("clm-aaaaaa", "clm-pppppp"),
            ],
        )
        sol = lib.compute_solidity([good, pend, a])
        self.assertEqual(sol["clm-gggggg"], 0.60)  # the numeric dep is fine
        self.assertNotIn("clm-aaaaaa", sol)  # but A is poisoned

    def test_numeric_sibling_of_blocked_claim_still_scored(self):
        # Pending-ness does not leak sideways: a numeric claim that does NOT
        # depend on the pending one is still scored normally.
        p = _claim("clm-pppppp", None)
        blocked = _claim("clm-bbbbbb", 0.85, [_edge("clm-bbbbbb", "clm-pppppp")])
        sibling = _claim("clm-ssssss", 0.70)
        sol = lib.compute_solidity([p, blocked, sibling])
        self.assertNotIn("clm-bbbbbb", sol)
        self.assertEqual(sol["clm-ssssss"], 0.70)


def _rests_on(source, key, fraction=lib.PENDING_FRACTION):
    """Build a rests-on DependsOnEdge — the off-graph endcap's edge.

    ``fraction`` is passed through verbatim: ``None`` (no ``(applicability …)``
    on the bullet), ``PENDING_FRACTION`` (the ``*pending*`` literal) and ``0.0``
    are three distinct authored states and the helper collapses none of them.
    """
    return lib.DependsOnEdge(
        source=source,
        target=kb_schema.work_id(key),
        relation="rests-on",
        target_kind="work",
        target_solidity_recorded=None,
        strength=None,
        context=None,
        fraction=fraction,
    )


def _work(key, strength):
    """Build an ExternalWork register node — ``strength`` ``None`` when pending."""
    return lib.ExternalWork(
        id=kb_schema.work_id(key),
        key=key,
        title=key,
        canonical_path="claim-quality.md",
        canonical_anchor=kb_schema.work_id(key),
        strength=strength,
    )


class TestAnOffGraphDependencyGatesTheClaimThatRestsOnIt(unittest.TestCase):
    """A ``rests-on`` edge joins the dep-gate ``min``, and this is the table.

    Applicability decides *whether* the pairing gates; the work's own
    ``strength`` decides *how hard*. The product ``strength × fraction`` is the
    shape to expect a reviewer to propose and it runs backwards under a ``min``:
    it makes the gate harsher as relevance falls, so a work judged sound and
    irrelevant (``strength 0.9``, ``fraction 0.0``) would annihilate the claim.
    The ``fraction 0.0`` row below is what refutes it.

    ``fraction`` is read before ``strength`` and ``None`` /
    ``PENDING_FRACTION`` / ``0.0`` are kept apart: a falsiness test collapses
    the three into one wrong answer.
    """

    def test_the_fraction_strength_table(self):
        # One claim at confidence 0.8 with one rests-on edge, over every
        # (fraction, strength) pairing the domain admits.
        cases = [
            # (fraction, strength, expected final, why)
            (0.0, 0.9, 0.8, "zero applicability removes the pairing from the gate"),
            (0.0, 0.0, 0.8, "and does so whatever the work's own standing is"),
            (0.0, None, 0.8, "short-circuits before strength is consulted at all"),
            (lib.PENDING_FRACTION, 0.9, None, "unscored applicability poisons, like a pending dep"),
            (None, 0.9, None, "an absent applicability is the pending case, not a zero"),
            (1.0, None, None, "unjudged work poisons whatever the pairing scores"),
            (1.0, 0.4, 0.4, "a positive pairing puts strength into the min"),
            (0.3, 0.4, 0.4, "applicability grades membership, never the gate's depth"),
            (1.0, 0.0, 0.0, "citing work judged spurious must be able to hurt"),
        ]
        for fraction, strength, expected, why in cases:
            with self.subTest(fraction=fraction, strength=strength, why=why):
                claim = _claim("clm-aaaaaa", 0.8, [_rests_on("clm-aaaaaa", "nobody2026", fraction)])
                full = lib.compute_solidity_full([claim], (), (), [_work("nobody2026", strength)])
                self.assertEqual(full["clm-aaaaaa"].final, expected)

    def test_a_work_with_no_register_entry_is_pending_and_does_not_raise(self):
        """Refresh computes solidity BEFORE the work-coverage guard names the defect."""
        claim = _claim("clm-aaaaaa", 0.8, [_rests_on("clm-aaaaaa", "nobody2026", 1.0)])

        full = lib.compute_solidity_full([claim], (), (), [])

        self.assertIsNone(full["clm-aaaaaa"].final)

    def test_the_weaker_of_a_work_gate_and_a_claim_gate_wins(self):
        """The work term is one more operand of the same ``min``, not an override."""
        weak = _claim("clm-dddddd", 0.3)
        claim = _claim(
            "clm-eeeeee",
            0.9,
            [_edge("clm-eeeeee", "clm-dddddd"), _rests_on("clm-eeeeee", "nobody2026", 1.0)],
        )

        full = lib.compute_solidity_full([weak, claim], (), (), [_work("nobody2026", 0.7)])

        self.assertEqual(full["clm-eeeeee"].final, 0.3)

    def test_every_operand_of_the_rendered_trace_is_readable_off_disk(self):
        """``min(local_quality, strength)`` — both numbers are hand-authored."""
        claim = _claim("clm-aaaaaa", 0.8, [_rests_on("clm-aaaaaa", "nobody2026", 0.3)])

        full = lib.compute_solidity_full([claim], (), (), [_work("nobody2026", 0.4)])

        self.assertEqual(lib.render_solidity_trace(full["clm-aaaaaa"]), " [= min(0.80, 0.40)]")

    def test_a_run_experiment_rescues_a_claim_gone_pending_through_a_work(self):
        """The max branch rescues a work-poisoned claim exactly as a dep-poisoned one."""
        claim = _claim("clm-aaaaaa", 0.8, [_rests_on("clm-aaaaaa", "nobody2026", 1.0)])
        exp = _experiment("exp-aaaaaa", "run", [("clm-aaaaaa", 0.9)])

        full = lib.compute_solidity_full([claim], [exp], (), [_work("nobody2026", None)])

        self.assertIsNone(full["clm-aaaaaa"].derivation)
        self.assertEqual(full["clm-aaaaaa"].final, 0.9)

    def test_an_off_graph_edge_closes_no_cycle_and_orders_nothing(self):
        """A work is terminal, so it is not a node of the order the cycle check walks."""
        a = _claim("clm-ffffff", 0.5, [_rests_on("clm-ffffff", "nobody2026")])
        b = _claim("clm-gggggg", 0.5, [_rests_on("clm-gggggg", "nobody2026")])

        self.assertEqual(sorted(lib._dependency_order({e.id: e for e in (a, b)}, {}, {})), ["clm-ffffff", "clm-gggggg"])

    def test_a_support_resting_on_outside_work_scores_as_if_it_did_not(self):
        """The support branch is inert because a support-sourced ``rests-on`` is illegal.

        A ``rests-on`` edge runs claim → work by definition (SPEC.md,
        Claim-Graph Nodes and Edges); no authoring path produces one out of a
        ``sup-``. ``_sup_solidity_from_deps`` therefore reads ``depends`` alone
        and this pins that the gate stayed in the claim branch where it belongs
        — not that a support may rest on outside work for free.
        """
        plain = _support("sup-aaaaaa", 0.6, supports=[("clm-hhhhhh", 1.0)])
        resting = _support(
            "sup-bbbbbb",
            0.6,
            depends_on=[_rests_on("sup-bbbbbb", "nobody2026", 1.0)],
            supports=[("clm-hhhhhh", 1.0)],
        )

        for support in (plain, resting):
            full = lib.compute_solidity_full([_claim("clm-hhhhhh", None)], (), [support], [_work("nobody2026", 0.1)])
            self.assertEqual(full.sup_solidity[support.id], 0.6)


def _experiment(exp_id, status, strengthens):
    """Build an ExperimentNode for synthetic strengthens-graph tests."""
    return lib.ExperimentNode(
        id=exp_id,
        title=exp_id,
        canonical_path="test/exp.md",
        canonical_anchor=exp_id,
        status=status,
        strengthens=tuple(strengthens),
    )


class TestExperimentalSolidity(unittest.TestCase):
    """The max-branch: run-experiment strengthens edges rescue/lift claims.

    Covers the SCHEMA "Solidity branches (definitive rule)" max-branch and the
    experiment non-transitivity rule, with no real leaves (synthetic
    in-memory KbState fragments).
    """

    def test_run_experiment_rescues_pending_derivation(self):
        # (a) clm-aaaaaa has a PENDING derivation (its claim-dep clm-pppppp is
        # confidence-pending). A run experiment strengthens it at 1.0 →
        # final = 1.0 even though derivation is pending.
        p = _claim("clm-pppppp", None)  # pending confidence
        a = _claim("clm-aaaaaa", 0.90, [_edge("clm-aaaaaa", "clm-pppppp")])
        exp = _experiment("exp-aaaaaa", "run", [("clm-aaaaaa", 1.0)])
        full = lib.compute_solidity_full([p, a], [exp])
        ra = full["clm-aaaaaa"]
        self.assertIsNone(ra.derivation)  # derivation pending (dep pending)
        self.assertEqual(ra.experimental, 1.0)
        self.assertEqual(ra.final, 1.0)  # RESCUED
        # The wrapper surfaces the non-pending final.
        self.assertEqual(lib.compute_solidity([p, a], [exp])["clm-aaaaaa"], 1.0)

    def test_strengthens_is_non_transitive(self):
        # (b) The experiment edge targets only clm-aaaaaa; its pending upstream
        # input clm-pppppp is NOT touched and stays pending.
        p = _claim("clm-pppppp", None)
        a = _claim("clm-aaaaaa", 0.90, [_edge("clm-aaaaaa", "clm-pppppp")])
        exp = _experiment("exp-aaaaaa", "run", [("clm-aaaaaa", 1.0)])
        full = lib.compute_solidity_full([p, a], [exp])
        self.assertEqual(full["clm-pppppp"].final, None)  # upstream still pending
        self.assertNotIn("clm-pppppp", lib.compute_solidity([p, a], [exp]))

    def test_rescued_final_propagates_downstream(self):
        # (c) clm-dddddd depends on the rescued clm-aaaaaa; its min sees the
        # rescued final 1.0, so it scores normally instead of going pending.
        p = _claim("clm-pppppp", None)
        a = _claim("clm-aaaaaa", 0.90, [_edge("clm-aaaaaa", "clm-pppppp")])
        d = _claim("clm-dddddd", 0.80, [_edge("clm-dddddd", "clm-aaaaaa")])
        exp = _experiment("exp-aaaaaa", "run", [("clm-aaaaaa", 1.0)])
        full = lib.compute_solidity_full([p, a, d], [exp])
        self.assertEqual(full["clm-aaaaaa"].final, 1.0)
        # 0.80 × min(1.0) = 0.80 — downstream benefits from the rescue.
        self.assertEqual(full["clm-dddddd"].derivation, 0.80)
        self.assertEqual(full["clm-dddddd"].final, 0.80)

    def test_unrun_experiment_contributes_nothing(self):
        # (d) An UNRUN (pending) experiment strengthens clm-aaaaaa, but its
        # derivation is pending (dep pending) → no rescue → claim stays pending.
        p = _claim("clm-pppppp", None)
        a = _claim("clm-aaaaaa", 0.90, [_edge("clm-aaaaaa", "clm-pppppp")])
        exp = _experiment("exp-aaaaaa", "pending", [("clm-aaaaaa", 1.0)])
        full = lib.compute_solidity_full([p, a], [exp])
        ra = full["clm-aaaaaa"]
        self.assertIsNone(ra.derivation)
        self.assertIsNone(ra.experimental)  # unrun → excluded from the max
        self.assertIsNone(ra.final)  # still pending
        self.assertNotIn("clm-aaaaaa", lib.compute_solidity([p, a], [exp]))

    def test_experimental_is_max_over_run_edges(self):
        # Multiple run experiments → experimental_solidity is the MAX strength.
        a = _claim("clm-aaaaaa", None)  # pending derivation
        e1 = _experiment("exp-aaaaaa", "run", [("clm-aaaaaa", 0.6)])
        e2 = _experiment("exp-bbbbbb", "run", [("clm-aaaaaa", 0.9)])
        full = lib.compute_solidity_full([a], [e1, e2])
        self.assertEqual(full["clm-aaaaaa"].experimental, 0.9)

    def test_final_is_max_of_derivation_and_experimental(self):
        # A claim with a strong derivation and a weaker experiment keeps the
        # higher derivation; the experiment never floors it down.
        a = _claim("clm-aaaaaa", 0.95)  # derivation 0.95, no deps
        exp = _experiment("exp-aaaaaa", "run", [("clm-aaaaaa", 0.40)])
        full = lib.compute_solidity_full([a], [exp])
        self.assertEqual(full["clm-aaaaaa"].derivation, 0.95)
        self.assertEqual(full["clm-aaaaaa"].experimental, 0.40)
        self.assertEqual(full["clm-aaaaaa"].final, 0.95)  # max keeps derivation

    def test_zero_experiments_final_equals_derivation(self):
        # The HARD-GATE invariant: with no experiments, final == derivation and
        # experimental is None for every claim.
        a = _claim("clm-aaaaaa", 0.75)
        b = _claim("clm-bbbbbb", 0.55, [_edge("clm-bbbbbb", "clm-aaaaaa")])
        full = lib.compute_solidity_full([a, b], [])
        for cid in ("clm-aaaaaa", "clm-bbbbbb"):
            self.assertIsNone(full[cid].experimental)
            self.assertEqual(full[cid].final, full[cid].derivation)


def _support(sup_id, quality, supports, depends_on=()):
    """Build a SupportNode for synthetic support-graph tests."""
    return lib.SupportNode(
        id=sup_id,
        title=sup_id,
        canonical_path="test/sup.md",
        canonical_anchor=sup_id,
        quality=quality,
        depends_on=tuple(depends_on),
        supports=tuple(supports),
    )


class TestSupportSolidity(unittest.TestCase):
    """Support nodes lift the DERIVATION branch, dep-gated.

    Synthetic in-memory KbState fragments — no real leaves. Covers sup_solidity
    derivation, the local_quality lift, on-point fractions, pending no-poison,
    multi-beneficiary fan-out, and dep-gating.
    """

    def test_free_standing_support_solidity_equals_quality(self):
        # A free-standing support (no deps) has sup_solidity == quality.
        s = _support("sup-aaaaaa", 0.90, [("clm-aaaaaa", 1.0)])
        a = _claim("clm-aaaaaa", 0.40)
        sup = lib.compute_support_solidity([a], (), [s])
        self.assertEqual(sup["sup-aaaaaa"], 0.90)

    def test_free_standing_support_lifts_beneficiary(self):
        # (a) A free-standing evaluated support lifts a beneficiary's solidity:
        # local_quality = max(0.40, 0.90×1.0) = 0.90, no deps to gate → 0.90.
        s = _support("sup-aaaaaa", 0.90, [("clm-aaaaaa", 1.0)])
        a = _claim("clm-aaaaaa", 0.40)
        full = lib.compute_solidity_full([a], (), [s])
        self.assertEqual(full["clm-aaaaaa"].derivation, 0.90)
        self.assertEqual(full["clm-aaaaaa"].final, 0.90)

    def test_a_dependency_free_lift_is_rounded_like_every_other_branch(self):
        """A permanent hard block: the no-dep branch skipped ``round_half_up_2dp``.

        A lift is ``sup_solidity x fraction`` — a raw float product, not an
        authored 2dp value. 0.20 x 0.98 wrote 0.196 into ``claims.jsonl`` while
        the markdown rendered ``0.20``, putting the record and the document on
        opposite sides of a band threshold. ``verify`` then FAILed
        "refresh-fixable" at 1e-9 and ``refresh`` reported nothing to change.
        """
        s = _support("sup-aaaaaa", 0.20, [("clm-aaaaaa", 0.98)])
        a = _claim("clm-aaaaaa", 0.10)
        full = lib.compute_solidity_full([a], (), [s])
        self.assertEqual(full["clm-aaaaaa"].derivation, 0.20)
        self.assertEqual(full["clm-aaaaaa"].final, 0.20)

    def test_a_dep_gated_lift_and_a_dep_free_lift_round_the_same_way(self):
        """The two branches must not disagree about precision on the same product."""
        s = _support("sup-aaaaaa", 0.20, [("clm-aaaaaa", 0.98), ("clm-bbbbbb", 0.98)])
        dep_free = _claim("clm-aaaaaa", 0.10)
        # A framework dependency contributes 1.0 and so never lowers the min:
        # the only difference between these two claims is which branch computes
        # the value.
        dep_gated = _claim("clm-bbbbbb", 0.10, [_edge("clm-bbbbbb", "axiom-1", kind="axiom")])
        full = lib.compute_solidity_full([dep_free, dep_gated], (), [s])
        self.assertEqual(full["clm-aaaaaa"].derivation, full["clm-bbbbbb"].derivation)

    def test_support_with_own_deps_is_dep_gated(self):
        # (b) A support with its own depends-on is dep-gated below its quality:
        # sup_solidity = round2(min(0.90, dep final 0.50)) = 0.50 (weakest link).
        dep = _claim("clm-dddddd", 0.50)
        s = _support(
            "sup-aaaaaa",
            0.90,
            [("clm-aaaaaa", 1.0)],
            depends_on=[_edge("sup-aaaaaa", "clm-dddddd")],
        )
        a = _claim("clm-aaaaaa", 0.20)
        full = lib.compute_solidity_full([dep, a], (), [s])
        self.assertEqual(full.sup_solidity["sup-aaaaaa"], 0.50)
        # The lift into clm-aaaaaa is the dep-gated 0.50, not the raw 0.90.
        self.assertEqual(full["clm-aaaaaa"].derivation, 0.50)

    def test_pending_support_contributes_nothing_and_no_poison(self):
        # (c) A pending-quality support contributes nothing to the max AND does
        # not poison a beneficiary with otherwise-valid quality.
        s = _support("sup-aaaaaa", None, [("clm-aaaaaa", 1.0)])
        a = _claim("clm-aaaaaa", 0.55)
        full = lib.compute_solidity_full([a], (), [s])
        self.assertIsNone(full.sup_solidity["sup-aaaaaa"])  # pending
        self.assertEqual(full["clm-aaaaaa"].derivation, 0.55)  # not lifted
        self.assertEqual(full["clm-aaaaaa"].final, 0.55)  # not poisoned

    def test_pending_support_does_not_poison_pending_beneficiary_dep(self):
        # Poison flows ONLY from a claim's own load-bearing depends-on, never
        # from an inbound supports edge: a pending support over a claim that
        # ALSO depends on a numeric claim leaves the claim scorable.
        good = _claim("clm-gggggg", 0.80)
        s = _support("sup-aaaaaa", None, [("clm-aaaaaa", 1.0)])
        a = _claim("clm-aaaaaa", 0.50, [_edge("clm-aaaaaa", "clm-gggggg")])
        full = lib.compute_solidity_full([good, a], (), [s])
        # min(0.50, 0.80) = 0.50; the pending support is simply ignored.
        self.assertEqual(full["clm-aaaaaa"].derivation, 0.50)

    def test_pending_fraction_contributes_nothing_and_no_poison(self):
        # An EVALUATED support whose on-point fraction is *pending* contributes
        # nothing to the beneficiary's local_quality max (excluded, exactly like
        # a pending sup_solidity) and never poisons an otherwise-valid claim.
        s = _support("sup-aaaaaa", 0.90, [("clm-aaaaaa", lib.PENDING_FRACTION)])
        a = _claim("clm-aaaaaa", 0.55)
        full = lib.compute_solidity_full([a], (), [s])
        self.assertEqual(full.sup_solidity["sup-aaaaaa"], 0.90)  # support scored
        self.assertEqual(full["clm-aaaaaa"].derivation, 0.55)  # not lifted
        self.assertEqual(full["clm-aaaaaa"].final, 0.55)  # not poisoned

    def test_pending_fraction_excluded_but_sibling_numeric_lift_applies(self):
        # One support fans out to two claims: a *pending* fraction to one, a
        # numeric fraction to the other. The pending edge is excluded; the
        # numeric edge still lifts its beneficiary.
        s = _support(
            "sup-aaaaaa",
            0.90,
            [("clm-aaaaaa", lib.PENDING_FRACTION), ("clm-bbbbbb", 0.50)],
        )
        a = _claim("clm-aaaaaa", 0.20)
        b = _claim("clm-bbbbbb", 0.20)
        full = lib.compute_solidity_full([a, b], (), [s])
        self.assertEqual(full["clm-aaaaaa"].derivation, 0.20)  # pending f, no lift
        self.assertEqual(full["clm-bbbbbb"].derivation, 0.45)  # 0.90×0.50

    def test_pending_fraction_materializes_as_literal_distinct_from_null(self):
        # On disk a pending supports-fraction is the literal "*pending*", DISTINCT
        # from a depends edge's null fraction (the sentinel-distinctness contract).
        dep = _claim("clm-dddddd", 0.50)
        s = _support(
            "sup-aaaaaa",
            0.90,
            [("clm-aaaaaa", lib.PENDING_FRACTION)],
            depends_on=[_edge("sup-aaaaaa", "clm-dddddd")],
        )
        a = _claim("clm-aaaaaa", 0.20)
        state = lib.KbState(
            claim_entries=(dep, a),
            leaves=(),
            indexes=(),
            framework_nodes=(),
            experiments=(),
            supports=(s,),
        )
        edges = lib.build_depends_on_records(state)
        supports = [e for e in edges if e["relation"] == "supports"]
        depends = [e for e in edges if e["relation"] == "depends" and e["source"] == "sup-aaaaaa"]
        self.assertEqual(supports[0]["fraction"], lib.PENDING_LITERAL)
        self.assertIsNone(depends[0]["fraction"])
        # And the serialized line distinguishes them: "*pending*" vs null.
        blob = lib.serialize_records(edges)
        self.assertIn('"fraction": "*pending*"', blob)
        self.assertIn('"fraction": null', blob)

    def test_fraction_below_one_reduces_contribution(self):
        # (d) An on-point fraction < 1.0 reduces the lift vs f=1.0.
        s = _support("sup-aaaaaa", 0.90, [("clm-aaaaaa", 0.50)])
        a = _claim("clm-aaaaaa", 0.20)
        full = lib.compute_solidity_full([a], (), [s])
        # local_quality = max(0.20, 0.90×0.50=0.45) = 0.45.
        self.assertEqual(full["clm-aaaaaa"].derivation, 0.45)

    def test_multi_beneficiary_support(self):
        # (e) One support → two claims at different fractions.
        s = _support("sup-aaaaaa", 0.90, [("clm-aaaaaa", 1.0), ("clm-bbbbbb", 0.50)])
        a = _claim("clm-aaaaaa", 0.20)
        b = _claim("clm-bbbbbb", 0.20)
        full = lib.compute_solidity_full([a, b], (), [s])
        self.assertEqual(full["clm-aaaaaa"].derivation, 0.90)
        self.assertEqual(full["clm-bbbbbb"].derivation, 0.45)

    def test_support_lift_is_dep_gated_on_beneficiary_deps(self):
        # The support lift is throttled by the BENEFICIARY's own deps — it does
        # NOT bypass them the way an experiment's max-branch does.
        dep = _claim("clm-dddddd", 0.50)
        s = _support("sup-aaaaaa", 0.90, [("clm-aaaaaa", 1.0)])
        a = _claim("clm-aaaaaa", 0.20, [_edge("clm-aaaaaa", "clm-dddddd")])
        full = lib.compute_solidity_full([dep, a], (), [s])
        # local_quality 0.90, but min(0.90, dep 0.50) = 0.50 — dep-gated.
        self.assertEqual(full["clm-aaaaaa"].derivation, 0.50)

    def test_support_lift_never_floors_a_higher_confidence(self):
        # A support weaker than the claim's own confidence does not lower it.
        s = _support("sup-aaaaaa", 0.30, [("clm-aaaaaa", 1.0)])
        a = _claim("clm-aaaaaa", 0.80)
        full = lib.compute_solidity_full([a], (), [s])
        self.assertEqual(full["clm-aaaaaa"].derivation, 0.80)  # max keeps 0.80

    def test_support_lift_is_derivation_branch_not_experimental(self):
        # A support feeds derivation, never the experimental/max branch.
        s = _support("sup-aaaaaa", 0.90, [("clm-aaaaaa", 1.0)])
        a = _claim("clm-aaaaaa", 0.40)
        full = lib.compute_solidity_full([a], (), [s])
        self.assertIsNone(full["clm-aaaaaa"].experimental)
        self.assertEqual(full["clm-aaaaaa"].derivation, 0.90)

    def test_support_cycle_raises(self):
        # A support depending on a claim that the support also supports would
        # cycle; the unified topo detects it.
        s = _support(
            "sup-aaaaaa",
            0.90,
            [("clm-aaaaaa", 1.0)],
            depends_on=[_edge("sup-aaaaaa", "clm-aaaaaa")],
        )
        a = _claim("clm-aaaaaa", 0.40)
        with self.assertRaises(lib.SolidityCycleError):
            lib.compute_solidity_full([a], (), [s])


class TestSupportPendingFractionParse(unittest.TestCase):
    """parse_support_leaf accepts a *pending* on-point fraction.

    A ``supports`` pair may carry the literal ``*pending*`` (intended-but-
    unassessed) — stored as the PENDING_FRACTION sentinel, distinct from a
    depends edge's null. Numeric fractions must still validate to [0, 1].
    """

    _LEAF = (
        "[↑ Parent](index.md)\n\n"
        "<!-- kb-frontmatter\n"
        "kind: leaf\n"
        'no-claim: "hosts a support node only"\n'
        "sup-id: sup-aaaaaa\n"
        "supports:\n"
        "  - clm-aaaaaa: *pending*\n"
        "  - clm-bbbbbb: 0.50\n"
        "-->\n\n"
        "## Pending-Fraction Support\n"
    )

    def _parse(self, text):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            leaf = root / "leaf.md"
            leaf.write_text(text, encoding="utf-8")
            return lib.parse_support_leaf(leaf, root)

    def test_pending_fraction_parses_as_sentinel(self):
        sups = self._parse(self._LEAF)
        self.assertEqual(len(sups), 1)
        sup = sups[0]
        self.assertEqual(sup.id, "sup-aaaaaa")
        by_claim = dict(sup.supports)
        self.assertIs(by_claim["clm-aaaaaa"], lib.PENDING_FRACTION)
        self.assertEqual(by_claim["clm-bbbbbb"], 0.50)

    def test_pending_sentinel_is_distinct_from_none(self):
        # The pending sentinel must NOT be None (which a depends edge uses).
        self.assertIsNotNone(lib.PENDING_FRACTION)
        self.assertIsNot(lib.PENDING_FRACTION, None)

    def test_zero_fraction_is_a_value_and_not_the_sentinel(self):
        # The two facts a reader must not conflate: *pending* is unassessed,
        # 0 is assessed at nothing. Both are authored; neither is an absence.
        text = self._LEAF.replace("clm-bbbbbb: 0.50", "clm-bbbbbb: 0")
        by_claim = dict(self._parse(text)[0].supports)
        self.assertEqual(by_claim["clm-bbbbbb"], 0.0)
        self.assertIsNot(by_claim["clm-bbbbbb"], lib.PENDING_FRACTION)
        self.assertIs(by_claim["clm-aaaaaa"], lib.PENDING_FRACTION)

    def test_below_zero_fraction_rejected(self):
        bad = self._LEAF.replace("clm-bbbbbb: 0.50", "clm-bbbbbb: -0.5")
        with self.assertRaises(lib.SupportLeafError):
            self._parse(bad)

    def test_above_one_fraction_still_rejected(self):
        bad = self._LEAF.replace("clm-bbbbbb: 0.50", "clm-bbbbbb: 1.5")
        with self.assertRaises(lib.SupportLeafError):
            self._parse(bad)


class TestPairPatternLinearity(unittest.TestCase):
    """The two pair patterns cost O(n) on a hostile line, and match what they did.

    Both patterns used to lead with ``^\\s*-?\\s*`` — two whitespace runs around
    an optional atom, which on a line of n spaces with no ``-`` in it fails
    n²/2 times before failing once. They are run per line by every support and
    experiment leaf parse and by ``parse_register_staged_supports``, which every
    census and every write-API prove call reaches, so one long whitespace line
    anywhere in the corpus was a multi-second stall on the *write* path with no
    cycle in it and no error at the end of it.

    Two claims, and the second is what makes the first safe to have made:
    the shipped patterns hold a wall-clock bound over the adversarial input, and
    they accept and capture exactly what the retired spelling did.
    """

    #: The greedy spelling the shipped patterns used to use. Kept here, and
    #: only here, so the equivalence claim is checked against the thing it is a
    #: claim about rather than against a paraphrase of it.
    _RETIRED_BULLET = r"^\s*-?\s*"

    #: Generous for CI and 200x the shipped patterns' measured cost at
    #: ``_ADVERSARIAL_WIDTH`` (~1 ms), while the retired spelling needs seconds.
    _BUDGET_SECONDS = 0.25
    _ADVERSARIAL_WIDTH = 40_000
    #: The teeth check runs the retired spelling, which is quadratic — at half
    #: the width it is still ~5x over the budget and a failing run stays cheap.
    _TEETH_WIDTH = 20_000

    def _elapsed(self, pattern, line):
        started = time.perf_counter()
        pattern.match(line)
        return time.perf_counter() - started

    def _retired(self, shipped):
        """The shipped pattern with its bullet prefix put back the greedy way."""
        return re.compile(self._RETIRED_BULLET + shipped.pattern[len(lib._PAIR_BULLET) :])

    def test_a_long_whitespace_line_is_matched_within_the_budget(self):
        line = " " * self._ADVERSARIAL_WIDTH
        for name, pattern in (
            ("_STRENGTHENS_PAIR_RE", lib._STRENGTHENS_PAIR_RE),
            ("_SUPPORTS_PAIR_RE", lib._SUPPORTS_PAIR_RE),
        ):
            with self.subTest(pattern=name):
                self.assertLess(self._elapsed(pattern, line), self._BUDGET_SECONDS)

    def test_the_other_hostile_shapes_are_within_the_budget_too(self):
        # A `-` splits the two runs, which is the position the greedy spelling
        # was worst at; a trailing run after the id exercises the `\s*:\s*` and
        # `\s*$` runs, which are single and were never the defect.
        width = self._ADVERSARIAL_WIDTH // 2
        for name, line in (
            ("spaces then dash", " " * self._ADVERSARIAL_WIDTH + "-"),
            ("dash then spaces", "-" + " " * self._ADVERSARIAL_WIDTH),
            ("spaces around dash", " " * width + "-" + " " * width),
            ("id then spaces", "clm-aaaaaa" + " " * self._ADVERSARIAL_WIDTH),
            ("id colon then spaces", "clm-aaaaaa:" + " " * self._ADVERSARIAL_WIDTH),
            ("tabs", "\t" * self._ADVERSARIAL_WIDTH),
        ):
            for pattern in (lib._STRENGTHENS_PAIR_RE, lib._SUPPORTS_PAIR_RE):
                with self.subTest(line=name, pattern=pattern.pattern):
                    self.assertLess(self._elapsed(pattern, line), self._BUDGET_SECONDS)

    def test_the_budget_has_teeth_against_the_spelling_it_retired(self):
        # Without this the bound above would pass over any pattern at all, and
        # a future edit that reintroduced the greedy prefix would go unseen.
        line = " " * self._TEETH_WIDTH
        elapsed = self._elapsed(self._retired(lib._SUPPORTS_PAIR_RE), line)
        self.assertGreater(elapsed, self._BUDGET_SECONDS)
        self.assertLess(self._elapsed(lib._SUPPORTS_PAIR_RE, line), self._BUDGET_SECONDS)

    def test_the_grammar_is_the_one_the_retired_spelling_matched(self):
        # The whole risk of the fix: a faster pattern that reads a different
        # language would silently drop or admit edges. Every spelling a pair
        # line is authored in, plus the near misses, through both.
        corpus = (
            "  - clm-aaaaaa: 0.5",
            "clm-aaaaaa: 0.5",
            "- clm-aaaaaa: 0.5",
            "-clm-aaaaaa: 0.5",
            "  -   clm-aaaaaa   :   0.5   ",
            "\t-\tclm-aaaaaa\t:\t0.5\t",
            "  - clm-aaaaaa: -0.5",
            "  - clm-aaaaaa: 1",
            "  - clm-aaaaaa: *pending*",
            "  - clm-aaaaaa: pending",
            "  - clm-aaaaaa: 1e-05",
            "  - clm-aaaaaa: nan",
            "  - clm-aaaaaa: .5",
            "  - clm-aaaaaa: 0.5.5",
            "  - clm-aaaaaa: 0.5 x",
            "  -- clm-aaaaaa: 0.5",
            "  - - clm-aaaaaa: 0.5",
            "  x clm-aaaaaa: 0.5",
            "  - clm-aaaaa: 0.5",
            "  - clm-aaaaaaa: 0.5",
            "  - clm-AAAAAA: 0.5",
            "  - clm-aaaaaa:",
            "-",
            "   ",
            "",
        )
        for shipped in (lib._STRENGTHENS_PAIR_RE, lib._SUPPORTS_PAIR_RE):
            retired = self._retired(shipped)
            for line in corpus:
                with self.subTest(pattern=shipped.pattern, line=line):
                    was = retired.match(line)
                    now = shipped.match(line)
                    self.assertEqual(
                        None if was is None else was.groups(),
                        None if now is None else now.groups(),
                    )


class TestSupportFixture(unittest.TestCase):
    """Support nodes against the fixture graph — the committed answer key.

    * sup-free01 (free-standing, q 0.90)  → sup_solidity 0.90
    * sup-dep001 (q 0.90, dep clm-aa1111 0.90) → min(0.90, 0.90) = 0.90 (dep-gated)
    * sup-pend01 (q *pending*)            → *pending* (omitted)
    * sup-coh001 (free-standing, q 0.85)  → 0.85
    Beneficiary lifts: sb1111→0.90, sb2222→0.45, sb3333→0.90, sb4444→0.55
    (not lifted), sb5555→0.85.
    """

    @classmethod
    def setUpClass(cls):
        cls.state = lib.discover_kb(_FIXTURE, diagnostic_stream=None)
        cls.sol = lib.compute_solidity(cls.state.claim_entries, cls.state.experiments, cls.state.supports)
        cls.sup = lib.compute_support_solidity(cls.state.claim_entries, cls.state.experiments, cls.state.supports)

    def test_support_solidities(self):
        self.assertEqual(self.sup["sup-free01"], 0.90)
        self.assertEqual(self.sup["sup-dep001"], 0.90)
        self.assertEqual(self.sup["sup-coh001"], 0.85)
        self.assertNotIn("sup-pend01", self.sup)  # pending → omitted
        # Both supports on the multi-sup container are free-standing.
        self.assertEqual(self.sup["sup-mlt001"], 0.80)
        self.assertEqual(self.sup["sup-mlt002"], 0.70)

    def test_beneficiary_lifts(self):
        self.assertEqual(self.sol["clm-sb1111"], 0.90)
        self.assertEqual(self.sol["clm-sb2222"], 0.45)
        self.assertEqual(self.sol["clm-sb3333"], 0.90)
        self.assertEqual(self.sol["clm-sb4444"], 0.55)  # pending sup, not lifted
        self.assertEqual(self.sol["clm-sb5555"], 0.85)
        # clm-sb6666: lifted by sup-mlt001 (0.80 × f=1.0 = 0.80); sup-mlt002's
        # pending-fraction edge to it contributes nothing.
        self.assertEqual(self.sol["clm-sb6666"], 0.80)
        # clm-sb7777: lifted by sup-mlt002 (0.70 × f=0.50 = 0.35).
        self.assertEqual(self.sol["clm-sb7777"], 0.35)

    def test_pending_support_does_not_poison_fixture_beneficiary(self):
        # clm-sb4444 has valid confidence (0.55) and a pending support; it must
        # remain scorable, never dragged to pending.
        self.assertIn("clm-sb4444", self.sol)

    def test_cohost_leaf_parses_as_both_claim_and_support(self):
        # (f) leaf-sup-cohost.md hosts BOTH a claim (clm-sb5555) and a support
        # (sup-coh001) — orthogonal node-bodies in one container.
        path = _FIXTURE / "common" / "leaf-sup-cohost.md"
        leaf = lib.parse_leaf(path, _FIXTURE)
        self.assertEqual(leaf.claims, ("clm-sb5555",))
        sups = lib.parse_support_leaf(path, _FIXTURE)
        self.assertEqual(len(sups), 1)
        sup = sups[0]
        self.assertEqual(sup.id, "sup-coh001")
        self.assertEqual(sup.supports, (("clm-sb5555", 1.0),))

    def test_support_depends_edge_emitted(self):
        # sup-dep001's OWN dependency is a depends edge sourced at the sup-id.
        edges = lib.build_depends_on_records(self.state)
        own = [e for e in edges if e["source"] == "sup-dep001" and e["relation"] == "depends"]
        self.assertEqual(len(own), 1)
        self.assertEqual(own[0]["target"], "clm-aa1111")

    def test_supports_edges_carry_fraction(self):
        edges = lib.build_depends_on_records(self.state)
        supports = [e for e in edges if e["relation"] == "supports"]
        # 5 from the single-sup leaves + 3 from the multi-sup container
        # (sup-mlt001→sb6666, sup-mlt002→sb6666 *pending*, sup-mlt002→sb7777).
        self.assertEqual(len(supports), 8)
        for e in supports:
            self.assertEqual(e["target_kind"], "claim")
            self.assertIsNone(e["strength"])
            self.assertIsNotNone(e["fraction"])
        # The pending on-point fraction serializes as the literal "*pending*".
        pending = [e for e in supports if e["source"] == "sup-mlt002" and e["target"] == "clm-sb6666"]
        self.assertEqual(len(pending), 1)
        self.assertEqual(pending[0]["fraction"], lib.PENDING_LITERAL)

    def test_supported_by_reverse_view(self):
        rows = lib.build_supported_by_records(self.state)
        self.assertEqual(len(rows), 8)
        by_claim = {r["claim_id"]: r for r in rows}
        self.assertEqual(by_claim["clm-sb2222"]["fraction"], 0.50)
        self.assertEqual(by_claim["clm-sb2222"]["sup_solidity"], 0.90)
        self.assertIsNone(by_claim["clm-sb4444"]["sup_solidity"])  # pending sup


class TestComputeSolidityFixture(unittest.TestCase):
    """compute_solidity and min_dependency_solidity against the fixture graph.

    The fixture's known answer key:

    * clm-aa1111 (conf 0.90, no deps)         → solidity 0.90
    * clm-bb2222 (conf 0.60, no deps)         → solidity 0.60
    * clm-cc3333 (conf 0.80, dep aa1111)      → min(0.80, 0.90) = 0.80
    * clm-dd4444 (conf 0.90, deps aa+bb)      → min(0.90, 0.90, 0.60) = 0.60
    * clm-ee5555 (conf 0.75, framework deps)  → 0.75 (framework contributes 1.0)
    * clm-hh8888 (conf 0.70, double INV-S2)   → 0.70
    """

    @classmethod
    def setUpClass(cls):
        cls.state = lib.discover_kb(_FIXTURE, diagnostic_stream=None)
        cls.sol = lib.compute_solidity(cls.state.claim_entries, cls.state.experiments)
        cls.by_id = {e.id: e for e in cls.state.claim_entries}

    def test_known_entry_solidities(self):
        self.assertEqual(self.sol["clm-aa1111"], 0.90)
        self.assertEqual(self.sol["clm-bb2222"], 0.60)
        self.assertEqual(self.sol["clm-cc3333"], 0.80)
        self.assertEqual(self.sol["clm-dd4444"], 0.60)
        self.assertEqual(self.sol["clm-ee5555"], 0.75)
        self.assertEqual(self.sol["clm-hh8888"], 0.70)
        # clm-gg7777 is rescued by run-experiment exp-bench1 → 0.80.
        self.assertEqual(self.sol["clm-gg7777"], 0.80)

    def test_no_deps_entry_solidity_equals_confidence(self):
        # clm-aa1111 has no entry-level dependencies → solidity == confidence.
        aa = self.by_id["clm-aa1111"]
        self.assertEqual(self.sol["clm-aa1111"], aa.confidence)

    def test_framework_only_entry_solidity_equals_confidence(self):
        # clm-ee5555 depends only on framework nodes → solidity == confidence.
        ee = self.by_id["clm-ee5555"]
        self.assertEqual(self.sol["clm-ee5555"], ee.confidence)

    def test_min_dependency_solidity_no_deps_is_none(self):
        aa = self.by_id["clm-aa1111"]
        self.assertIsNone(lib.min_dependency_solidity(aa, self.sol))

    def test_min_dependency_solidity_picks_minimum(self):
        # clm-dd4444 depends on clm-aa1111 (0.90) and clm-bb2222 (0.60).
        dd = self.by_id["clm-dd4444"]
        self.assertEqual(lib.min_dependency_solidity(dd, self.sol), 0.60)

    def test_min_dependency_solidity_framework_contributes_one(self):
        # clm-ee5555's deps are all framework nodes → min is 1.0.
        ee = self.by_id["clm-ee5555"]
        self.assertEqual(lib.min_dependency_solidity(ee, self.sol), 1.0)


class TestCycleDetectionIsIndependentOfScoring(unittest.TestCase):
    """A cycle is a property of the edge set; a score is a property of a node.

    Every claim a build authors carries ``*pending*`` until a scoring pass runs,
    so a walk admitting only claims with a numeric ``confidence`` cannot raise
    on a freshly built graph at all — ``refresh`` and ``verify`` are then both
    silent on a cycle in it. The first half below is that hole, closed. The
    second half is the price of closing it, which must be nothing: the same
    inputs still produce the same solidities and the same bands, and the claims
    now walked contribute no value to any of them.
    """

    # -- what raises ------------------------------------------------------

    def test_a_cycle_among_pending_claims_raises(self):
        ring = [
            _claim("clm-penda1", None, [_edge("clm-penda1", "clm-pendb2")]),
            _claim("clm-pendb2", None, [_edge("clm-pendb2", "clm-pendc3")]),
            _claim("clm-pendc3", None, [_edge("clm-pendc3", "clm-penda1")]),
        ]
        with self.assertRaises(lib.SolidityCycleError) as ctx:
            lib.compute_solidity(ring)
        self.assertEqual(set(ctx.exception.cycle_members), {"clm-penda1", "clm-pendb2", "clm-pendc3"})

    def test_a_pending_self_loop_raises(self):
        with self.assertRaises(lib.SolidityCycleError):
            lib.compute_solidity([_claim("clm-penda1", None, [_edge("clm-penda1", "clm-penda1")])])

    def test_a_cycle_spanning_a_scored_and_a_pending_claim_raises(self):
        # The mixed case the old walk also missed: one leg of the ring hangs off
        # a claim with no score, so the ring was never assembled.
        pair = [
            _claim("clm-scored1", 0.90, [_edge("clm-scored1", "clm-penda1")]),
            _claim("clm-penda1", None, [_edge("clm-penda1", "clm-scored1")]),
        ]
        with self.assertRaises(lib.SolidityCycleError):
            lib.compute_solidity(pair)

    def test_an_acyclic_pending_graph_still_computes(self):
        # Widening the walk must not turn every pending chain into a refusal.
        chain = [
            _claim("clm-penda1", None),
            _claim("clm-pendb2", None, [_edge("clm-pendb2", "clm-penda1")]),
            _claim("clm-scored1", 0.90),
        ]
        self.assertEqual(lib.compute_solidity(chain), {"clm-scored1": 0.90})

    # -- what does not change ---------------------------------------------

    #: A graph exercising every branch of the rule at once: a chain, a framework
    #: dep, an experimental rescue that propagates, a support lift, and a scored
    #: claim poisoned by a pending dependency.
    #:
    #: * clm-scored1  conf 0.90, no deps                       -> 0.90
    #: * clm-scored2  conf 0.70, dep scored1, exp 0.85         -> max(min(0.70, 0.90), 0.85) = 0.85
    #: * clm-scored3  conf 0.95, deps scored2 + INVARIANT-S2   -> min(0.95, 0.85, 1.0) = 0.85
    #: * clm-scored4  conf 0.20, lifted 0.60 x 0.50 = 0.30     -> 0.30
    #: * clm-scored5  conf 0.80, dep on a pending claim        -> pending, omitted
    #: * sup-suppp1   quality 0.60, dep scored1                -> min(0.60, 0.90) = 0.60
    _SCORED_SOLIDITIES = {"clm-scored1": 0.90, "clm-scored2": 0.85, "clm-scored3": 0.85, "clm-scored4": 0.30}

    def _scored(self):
        return [
            _claim("clm-scored1", 0.90),
            _claim("clm-scored2", 0.70, [_edge("clm-scored2", "clm-scored1")]),
            _claim(
                "clm-scored3",
                0.95,
                [_edge("clm-scored3", "clm-scored2"), _edge("clm-scored3", "INVARIANT-S2", kind="invariant")],
            ),
            _claim("clm-scored4", 0.20),
            _claim("clm-scored5", 0.80, [_edge("clm-scored5", "clm-penda1")]),
        ]

    def _pending(self):
        """Unscored claims, including edges into and out of the scored half."""
        return [
            _claim("clm-penda1", None),
            _claim("clm-pendb2", None, [_edge("clm-pendb2", "clm-penda1")]),
            _claim("clm-pendc3", None, [_edge("clm-pendc3", "clm-scored1")]),
        ]

    def _experiments(self):
        return [_experiment("exp-runnn1", "run", [("clm-scored2", 0.85)])]

    def _supports(self):
        return [_support("sup-suppp1", 0.60, [("clm-scored4", 0.50)], [_edge("sup-suppp1", "clm-scored1")])]

    def test_the_scored_graphs_solidities_are_what_they_were(self):
        full = lib.compute_solidity_full(self._scored() + self._pending(), self._experiments(), self._supports())
        self.assertEqual({cid: r.final for cid, r in full.items() if r.final is not None}, self._SCORED_SOLIDITIES)
        self.assertIsNone(full["clm-scored5"].final)  # poisoned by its pending dep
        self.assertEqual(full.sup_solidity, {"sup-suppp1": 0.60})
        self.assertEqual(full["clm-scored2"].derivation, 0.70)  # the max branch set the final
        self.assertEqual(full["clm-scored2"].experimental, 0.85)

    def test_the_bands_are_what_they_were(self):
        solidity = lib.compute_solidity(self._scored() + self._pending(), self._experiments(), self._supports())
        self.assertEqual(
            {cid: lib.build_status_phrase(value) for cid, value in solidity.items()},
            {cid: lib.build_status_phrase(value) for cid, value in self._SCORED_SOLIDITIES.items()},
        )

    def test_the_claims_the_walk_gained_contribute_nothing(self):
        # The same computation with the unscored half removed: every value the
        # scored half carries is identical, which is what says the widened walk
        # ordered more nodes and scored none of them.
        with_pending = lib.compute_solidity_full(
            self._scored() + self._pending(), self._experiments(), self._supports()
        )
        without = lib.compute_solidity_full(self._scored(), self._experiments(), self._supports())
        self.assertEqual({cid: with_pending[cid] for cid in without}, dict(without))
        self.assertEqual(with_pending.sup_solidity, without.sup_solidity)


class TestFrameworkNodeCoverageGuard(unittest.TestCase):
    """Issue #28 regression: refresh must fail loudly — not silently emit a
    dangling index — when CLAUDE.md framework-node parsing drops nodes that
    the claim graph references.

    Root cause of #28 was a transient mid-merge CLAUDE.md whose axiom bullets
    no longer matched the parser, so ``parse_framework_nodes`` yielded zero
    axioms and the rebuilt index lost ``axiom-1``..``axiom-4`` while
    ``depends-on`` edges still targeted them — surfacing only later as a flood
    of cryptic referential-integrity orphans in ``verify_kb_metadata``.
    """

    # --- unit: the guard predicate itself -------------------------------

    def test_guard_silent_when_referenced_axiom_present(self):
        claims = [
            {"node_type": "axiom", "id": "axiom-1"},
            {"node_type": "claim", "id": "clm-aaaaaa"},
        ]
        edges = [{"source": "clm-aaaaaa", "target": "axiom-1", "target_kind": "axiom"}]
        # No raise.
        lib._assert_framework_node_coverage(claims, edges)

    def test_guard_fires_when_referenced_axiom_missing(self):
        claims = [{"node_type": "claim", "id": "clm-aaaaaa"}]  # axiom-1 dropped
        edges = [{"source": "clm-aaaaaa", "target": "axiom-1", "target_kind": "axiom"}]
        with self.assertRaises(lib.FrameworkNodeParseError) as ctx:
            lib._assert_framework_node_coverage(claims, edges)
        msg = str(ctx.exception)
        self.assertIn("axiom-1", msg)
        self.assertIn("CLAUDE.md", msg)

    # --- integration: real parse → build chain on the fixture -----------

    def test_build_all_records_succeeds_on_valid_fixture(self):
        state = lib.discover_kb(_FIXTURE, diagnostic_stream=None)
        records = lib.build_all_records(state)  # must not raise
        axioms = [r for r in records["claims"] if r["node_type"] == "axiom"]
        self.assertEqual(len(axioms), 4)

    def test_build_all_records_fires_on_malformed_claude_md(self):
        # Reproduce #28: copy the fixture, mangle the axiom bullets so the
        # parser regex (`^- Axiom N: **...**`) no longer matches (here: indent
        # them, as a hand-merge might). The fixture's claim depends on Axiom 4,
        # so the dropped axiom-4 node leaves a dangling edge → guard must fire.
        with tempfile.TemporaryDirectory() as tmp:
            kb = Path(tmp) / "mini-kb"
            shutil.copytree(_FIXTURE, kb)
            claude = kb / "CLAUDE.md"
            text = claude.read_text(encoding="utf-8")
            mangled = text.replace("\n- Axiom ", "\n  - Axiom ")  # indent bullets
            self.assertNotEqual(text, mangled, "fixture must contain axiom bullets")
            claude.write_text(mangled, encoding="utf-8")

            state = lib.discover_kb(kb, diagnostic_stream=None)
            # Sanity: the mangle actually dropped the axioms at parse time.
            self.assertEqual(sum(1 for n in state.framework_nodes if n.node_type == "axiom"), 0)
            with self.assertRaises(lib.FrameworkNodeParseError) as ctx:
                lib.build_all_records(state)
            msg = str(ctx.exception)
            self.assertIn("axiom-4", msg)
            self.assertIn("CLAUDE.md", msg)


class TestFrontmatterListShapes(unittest.TestCase):
    """An id-list means the same thing in all three authored shapes.

    A wrapped list fell through to the string branch, so every consumer that
    iterated it got one "claim id" per CHARACTER; a YAML-block list was skipped
    line by line and read as empty, dropping a leaf's whole claim set with no
    diagnostic anywhere.
    """

    A = "clm-aaaaaa"
    B = "clm-bbbbbb"

    def _claims(self, body: str) -> list:
        fm = lib.parse_frontmatter(f"<!-- kb-frontmatter\n{body}\n-->\n")
        self.assertIsNotNone(fm)
        return list(fm.get("claims", []) or ())

    def test_single_line_list(self):
        self.assertEqual(self._claims(f"kind: leaf\nclaims: [{self.A}, {self.B}]"), [self.A, self.B])

    def test_wrapped_list_yields_ids_not_characters(self):
        self.assertEqual(self._claims(f"kind: leaf\nclaims: [{self.A},\n         {self.B}]"), [self.A, self.B])

    def test_yaml_block_list_yields_ids_not_nothing(self):
        self.assertEqual(self._claims(f"kind: leaf\nclaims:\n  - {self.A}\n  - {self.B}"), [self.A, self.B])

    def test_field_after_a_wrapped_list_still_parses(self):
        """The continuation scan must not swallow the field that follows it."""
        fm = lib.parse_frontmatter(f"<!-- kb-frontmatter\nclaims: [{self.A},\n  {self.B}]\nkind: leaf\n-->\n")
        self.assertEqual(fm["kind"], "leaf")
        self.assertEqual(fm["claims"], [self.A, self.B])

    def test_field_after_a_yaml_block_still_parses(self):
        fm = lib.parse_frontmatter(f"<!-- kb-frontmatter\nclaims:\n  - {self.A}\nkind: leaf\n-->\n")
        self.assertEqual(fm["kind"], "leaf")
        self.assertEqual(fm["claims"], [self.A])

    def test_unterminated_list_stays_a_string(self):
        """A malformed value degrades exactly as it did before — not an id-list."""
        fm = lib.parse_frontmatter(f"<!-- kb-frontmatter\nclaims: [{self.A},\n-->\n")
        self.assertIsInstance(fm["claims"], str)

    def test_field_end_span_is_the_readers_join_rule(self):
        """The writer's span rule and the reader's join rule are ONE rule."""
        wrapped = [f"claims: [{self.A},", f"         {self.B}]", "kind: leaf"]
        self.assertEqual(lib.frontmatter_field_end(wrapped, 0), 2)
        self.assertEqual(lib.frontmatter_field_end(wrapped, 2), 3)

        bullets = ["claims:", f"  - {self.A}", f"  - {self.B}", "kind: leaf"]
        self.assertEqual(lib.frontmatter_field_end(bullets, 0), 3)


class TestSlugifyHeading(unittest.TestCase):
    """Heading anchors must be the ones GitHub actually emits."""

    def test_em_dash_leaves_two_hyphens(self):
        # GitHub drops the em-dash and hyphenates each surviving space, so the
        # collapsing `\s+` form produced an anchor no renderer resolves.
        self.assertEqual(lib._slugify_heading("Solidity — the dep gate"), "solidity--the-dep-gate")

    def test_any_punctuation_between_spaces_has_the_same_shape(self):
        self.assertEqual(lib._slugify_heading("Rule : applied"), "rule--applied")

    def test_ordinary_heading_unchanged(self):
        self.assertEqual(lib._slugify_heading("The dep gate"), "the-dep-gate")


class TestFrontmatterFinderIsLinear(unittest.TestCase):
    """`find_frontmatter` answers `FRONTMATTER_RE.search`, in one pass.

    ``FRONTMATTER_RE``'s body is a lazy ``(.*?)`` under ``DOTALL``: at every
    candidate opener the engine scans to the end of the document looking for a
    closer, so a document carrying many openers and no ``-->`` at line start
    costs O(openers x length) — 100 openers 17 ms, 400 openers 281 ms, 1 600
    openers 4 493 ms. Only a truncated or hostile document has that shape, but
    ``parse_frontmatter`` runs over every file in the KB, so it only has to
    arrive once.

    Two claims, and the second is what makes the first safe to have made: the
    finder holds a wall-clock bound over the adversarial input, and it returns
    what the pattern's own ``search`` returns on every shape that matters.
    """

    #: Generous for CI and far above the finder's measured cost at
    #: ``_ADVERSARIAL_OPENERS`` (~1 ms), while `search` needs seconds there.
    _BUDGET_SECONDS = 0.5
    #: Measured on the machine this was written on: the retired call costs
    #: ~2 500 ms here and the finder ~1.3 ms, so the bound sits between two
    #: numbers three orders of magnitude apart. The teeth check runs at the SAME
    #: width, so the two halves are a claim about one input rather than two.
    _ADVERSARIAL_OPENERS = 4_000

    @staticmethod
    def _unterminated(openers: int) -> str:
        """A document of `openers` block openers and no closer at line start."""
        return "".join("<!-- kb-frontmatter\nkind: leaf\nclaims: [clm-aa1111]\n" for _ in range(openers))

    def _elapsed(self, call):
        started = time.perf_counter()
        call()
        return time.perf_counter() - started

    def test_the_unterminated_document_is_answered_within_the_budget(self):
        text = self._unterminated(self._ADVERSARIAL_OPENERS)
        self.assertIsNone(lib.find_frontmatter(text))
        self.assertLess(self._elapsed(lambda: lib.find_frontmatter(text)), self._BUDGET_SECONDS)

    def test_stripping_the_unterminated_document_is_within_the_budget_too(self):
        text = self._unterminated(self._ADVERSARIAL_OPENERS)
        self.assertEqual(lib.strip_frontmatter(text), text)
        self.assertLess(self._elapsed(lambda: lib.strip_frontmatter(text)), self._BUDGET_SECONDS)

    def test_the_budget_has_teeth_against_the_retired_call(self):
        """The bound is only evidence if the shape it bounds is really hostile."""
        text = self._unterminated(self._ADVERSARIAL_OPENERS)
        self.assertGreater(
            self._elapsed(lambda: lib.FRONTMATTER_RE.search(text)),
            self._BUDGET_SECONDS,
            "the adversarial document is no longer expensive for the retired call; "
            "the timing bound above has stopped being evidence",
        )

    #: Shapes the finder and `FRONTMATTER_RE.search` must agree on, including
    #: the ones the one-pass argument turns on: an opener that is not the
    #: matching one, and openers before and after the real block.
    _SHAPES = {
        "none": "# Title\n\nprose\n",
        "plain": "<!-- kb-frontmatter\nkind: leaf\n-->\n\n# Body\n",
        "indented close": "<!-- kb-frontmatter\nkind: leaf\n  -->\n\n# Body\n",
        "unterminated": "<!-- kb-frontmatter\nkind: leaf\n",
        "unterminated then real": ("<!-- kb-frontmatter\nkind: index\n" "<!-- kb-frontmatter\nkind: leaf\n-->\n"),
        "real then opener": ("<!-- kb-frontmatter\nkind: leaf\n-->\n\n" "<!-- kb-frontmatter\nkind: index\n"),
        "two blocks": ("<!-- kb-frontmatter\nkind: leaf\n-->\n\nbody\n\n" "<!-- kb-frontmatter\nkind: index\n-->\n"),
        "closer before opener": "-->\n<!-- kb-frontmatter\nkind: leaf\n-->\n",
        "empty body": "<!-- kb-frontmatter\n\n-->\n",
        "no trailing newline": "<!-- kb-frontmatter\nkind: leaf\n-->",
        "empty document": "",
    }

    def test_the_finder_returns_what_search_returns(self):
        for name, text in self._SHAPES.items():
            with self.subTest(shape=name):
                found = lib.find_frontmatter(text)
                expected = lib.FRONTMATTER_RE.search(text)
                if expected is None:
                    self.assertIsNone(found)
                    continue
                self.assertIsNotNone(found)
                self.assertEqual((found.span(), found.group(1)), (expected.span(), expected.group(1)))

    def test_stripping_returns_what_sub_returns(self):
        for name, text in self._SHAPES.items():
            with self.subTest(shape=name):
                self.assertEqual(lib.strip_frontmatter(text), lib.FRONTMATTER_RE.sub("", text))


_REFERENCING_REGISTER = """# Register

## Alpha
<!-- id: clm-aaaaaa -->

### Quality
- confidence: 0.8
- depends-on:
  - clm-cccccc — Gamma (solidity 0.5)
- references:
  - clm-bbbbbb — Beta [distinct from the certificate of Beta]
- solidity: *pending*
- rationale: Alpha.

---

## Beta
<!-- id: clm-bbbbbb -->

### Quality
- confidence: 0.9
- references:
  - clm-aaaaaa — Alpha
- solidity: *pending*
- rationale: Beta.
- strengthen-by:
  - a replication.

---

## Gamma
<!-- id: clm-cccccc -->

### Quality
- confidence: 0.5
- solidity: *pending*
- rationale: Gamma.
"""


class TestReferencesEdges(unittest.TestCase):
    """The ``- references:`` field: parsed apart, materialized, and inert."""

    def setUp(self):
        self.root = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.root)
        self.register = self.root / "claim-quality.md"
        self.register.write_text(_REFERENCING_REGISTER, encoding="utf-8")
        self.entries = {
            entry.id: entry for entry in lib.parse_claim_quality_file(self.register, self.root, known_ids=None)
        }

    def test_a_references_bullet_lands_off_depends_on(self):
        alpha = self.entries["clm-aaaaaa"]
        self.assertEqual([e.target for e in alpha.depends_on], ["clm-cccccc"])
        self.assertEqual(
            [(e.target, e.relation, e.target_kind) for e in alpha.references], [("clm-bbbbbb", "references", "claim")]
        )
        self.assertEqual(alpha.references[0].context, "distinct from the certificate of Beta")
        self.assertIsNone(alpha.references[0].strength)
        self.assertIsNone(alpha.references[0].fraction)

    def test_the_field_bounds_the_fields_around_it(self):
        # `depends-on` must stop at `- references:` and `rationale` must not
        # swallow it — the two failures a key missing from the reader's own key
        # list produces, and the reason that list is stated once.
        alpha = self.entries["clm-aaaaaa"]
        self.assertEqual(alpha.rationale, "Alpha.")
        self.assertEqual(alpha.confidence, 0.8)
        beta = self.entries["clm-bbbbbb"]
        self.assertEqual(beta.depends_on, ())
        self.assertEqual([item.text for item in beta.strengthen_by], ["a replication."])

    def test_solidity_is_computed_as_though_the_references_were_not_there(self):
        # Alpha references Beta (0.9) and depends on Gamma (0.5). If the
        # reference reached the gate at all, Alpha's min would notice; it must
        # be Gamma's 0.5 and nothing else. Beta's mutual reference to Alpha is
        # the cycle a `depends` edge would close.
        computed = lib.compute_solidity(list(self.entries.values()))
        self.assertEqual(computed["clm-cccccc"], 0.5)
        self.assertEqual(computed["clm-aaaaaa"], 0.5)
        self.assertEqual(computed["clm-bbbbbb"], 0.9)

    def test_a_mutual_reference_pair_is_not_a_cycle(self):
        # `compute_solidity` raises `SolidityCycleError` on a dependency cycle;
        # two claims naming each other must not be one.
        lib.compute_solidity(list(self.entries.values()))

    def test_every_reference_reaches_the_depends_on_records(self):
        state = lib.KbState(
            claim_entries=tuple(self.entries.values()),
            leaves=(),
            indexes=(),
            framework_nodes=(),
            experiments=(),
        )
        records = lib.build_depends_on_records(state)
        referencing = [r for r in records if r["relation"] == "references"]
        self.assertEqual(
            [(r["source"], r["target"], r["target_kind"], r["strength"], r["fraction"]) for r in referencing],
            [("clm-aaaaaa", "clm-bbbbbb", "claim", None, None), ("clm-bbbbbb", "clm-aaaaaa", "claim", None, None)],
        )


if __name__ == "__main__":
    unittest.main()
