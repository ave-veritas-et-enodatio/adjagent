"""Integration tests for the JSONL index emission in ``refresh_kb_metadata.py``.

Runs the refresh script as a subprocess against the synthetic fixture KB
under ``tests/fixtures/mini-kb/`` (copied to a per-class tempdir so the
committed fixture is never mutated) and verifies the emission contract:

* All five JSONL files are emitted under ``.index/``.
* The script is idempotent — a second run produces byte-identical files.
* Record counts match what ``kb_index_lib`` produces in-process.
* Every emitted line is a well-formed JSON object; files end with one ``\\n``.
* Referential integrity: every claim id referenced in non-claims files
  appears as a record in ``claims.jsonl``.
* Records are sorted by the per-file sort key its ``kb_index_lib`` emitter documents.

Run via the project's test target (pytest).

Tests are fully independent of live KB state. Nothing here reads, writes, or
asserts on ``kb-root/`` proper.
"""

import hashlib
import json
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from kb_tools import kb_index_lib as lib

_THIS_DIR = Path(__file__).resolve().parent
_REPO_ROOT = _THIS_DIR.parent.parent
_FIXTURE_SRC = _THIS_DIR / "fixtures" / "mini-kb"
_REFRESH_MOD = "kb_tools.refresh_kb_metadata"

_INDEX_FILES = (
    "claims.jsonl",
    "depends-on.jsonl",
    "strengthen-by.jsonl",
    "cites.jsonl",
    "subtree-aggregates.jsonl",
)


def _run_refresh(kb_root: Path) -> subprocess.CompletedProcess:
    """Run refresh_kb_metadata against ``kb_root`` and return the result."""
    return subprocess.run(
        [sys.executable, "-m", _REFRESH_MOD, "--kb-root", str(kb_root)],
        cwd=_REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )


def _materialize_fixture(parent: Path) -> Path:
    """Copy the committed fixture into ``parent``. Returns the KB root path."""
    kb = parent / "mini-kb"
    shutil.copytree(_FIXTURE_SRC, kb)
    return kb


def _hash_file(path: Path) -> str:
    return hashlib.sha256(path.read_text(encoding="utf-8").encode("utf-8")).hexdigest()


def _read_jsonl_lines(path: Path) -> list[str]:
    """Return raw (non-empty) lines preserving exact bytes for sort checks."""
    text = path.read_text(encoding="utf-8")
    return [ln for ln in text.split("\n") if ln]


class TestRefreshIndexJsonlEmission(unittest.TestCase):
    """End-to-end tests; share a single refresh run across test methods."""

    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory()
        cls.addClassCleanup(cls._tmp.cleanup)
        cls.kb_root = _materialize_fixture(Path(cls._tmp.name))
        cls.index_dir = cls.kb_root / ".index"
        cls.first_result = _run_refresh(cls.kb_root)
        if cls.first_result.returncode == 0:
            cls.first_hashes = {name: _hash_file(cls.index_dir / name) for name in _INDEX_FILES}
        else:
            cls.first_hashes = {}

    def test_jsonl_files_emitted(self):
        self.assertEqual(
            self.first_result.returncode,
            0,
            f"refresh exited non-zero: stderr={self.first_result.stderr}",
        )
        for name in _INDEX_FILES:
            with self.subTest(name=name):
                self.assertTrue(
                    (self.index_dir / name).exists(),
                    f"missing emitted file: {name}",
                )

    def test_idempotence(self):
        # Run again and compare hashes.
        second_result = _run_refresh(self.kb_root)
        self.assertEqual(second_result.returncode, 0)
        for name in _INDEX_FILES:
            with self.subTest(name=name):
                self.assertEqual(
                    self.first_hashes[name],
                    _hash_file(self.index_dir / name),
                    f"{name} changed between runs (not idempotent)",
                )

    def test_record_counts_match_state(self):
        state = lib.discover_kb(self.kb_root)
        all_records = lib.build_all_records(state)
        expected = {
            "claims.jsonl": len(all_records["claims"]),
            "depends-on.jsonl": len(all_records["depends-on"]),
            "strengthen-by.jsonl": len(all_records["strengthen-by"]),
            "cites.jsonl": len(all_records["cites"]),
            "subtree-aggregates.jsonl": len(all_records["subtree-aggregates"]),
        }
        for name, want in expected.items():
            with self.subTest(name=name):
                got = len(_read_jsonl_lines(self.index_dir / name))
                self.assertEqual(
                    got,
                    want,
                    f"{name}: on-disk count {got} != build_all_records count {want}",
                )

    def test_jsonl_well_formed(self):
        for name in _INDEX_FILES:
            with self.subTest(name=name):
                path = self.index_dir / name
                raw = path.read_text(encoding="utf-8")
                # Empty file is acceptable per write_jsonl semantics, but we
                # expect every file in this fixture to be non-empty.
                self.assertTrue(raw, f"{name} is empty")
                # File ends with exactly one trailing newline.
                self.assertEqual(
                    raw[-1:],
                    "\n",
                    f"{name} does not end with a newline",
                )
                self.assertNotEqual(
                    raw[-2:],
                    "\n\n",
                    f"{name} has multiple trailing newlines",
                )
                text = raw
                for lineno, line in enumerate(text.split("\n"), start=1):
                    if lineno == len(text.split("\n")):
                        # Last element is empty string from terminal "\n".
                        self.assertEqual(line, "", f"{name}: trailing content after final newline")
                        continue
                    obj = json.loads(line)
                    self.assertIsInstance(
                        obj,
                        dict,
                        f"{name}:{lineno}: not a JSON object",
                    )

    def test_claims_jsonl_node_type_distribution(self):
        # Content-independent: every record's node_type is one of the three
        # valid node kinds.
        recs = [json.loads(ln) for ln in _read_jsonl_lines(self.index_dir / "claims.jsonl")]
        self.assertTrue(recs, "claims.jsonl is empty")
        for rec in recs:
            self.assertIn(
                rec.get("node_type", "claim"),
                {"claim", "experiment", "support", "invariant", "axiom"},
                f"unexpected node_type in {rec}",
            )

    def test_depends_on_target_kind_distribution(self):
        # Content-independent: every edge's target_kind is one of the three
        # valid node kinds.
        recs = [json.loads(ln) for ln in _read_jsonl_lines(self.index_dir / "depends-on.jsonl")]
        self.assertTrue(recs, "depends-on.jsonl is empty")
        for rec in recs:
            self.assertIn(
                rec["target_kind"],
                {"claim", "invariant", "axiom"},
                f"unexpected target_kind in {rec}",
            )

    def test_depends_on_target_kind_matches_resolved_node_type(self):
        # Every edge's target_kind must equal the node_type of the record it
        # resolves to in claims.jsonl.
        node_type = {
            json.loads(ln)["id"]: json.loads(ln).get("node_type", "claim")
            for ln in _read_jsonl_lines(self.index_dir / "claims.jsonl")
        }
        for ln in _read_jsonl_lines(self.index_dir / "depends-on.jsonl"):
            rec = json.loads(ln)
            self.assertIn(rec["target"], node_type, f"orphan target: {rec}")
            self.assertEqual(
                rec["target_kind"],
                node_type[rec["target"]],
                f"target_kind mismatch: {rec}",
            )

    def test_referential_integrity(self):
        # Build the set of canonical claim ids from claims.jsonl.
        claims_path = self.index_dir / "claims.jsonl"
        canonical_ids = {json.loads(ln)["id"] for ln in _read_jsonl_lines(claims_path)}

        # depends-on: source and target must each appear in claims.jsonl.
        dep_path = self.index_dir / "depends-on.jsonl"
        for ln in _read_jsonl_lines(dep_path):
            rec = json.loads(ln)
            self.assertIn(rec["source"], canonical_ids, f"depends-on source orphan: {rec}")
            self.assertIn(rec["target"], canonical_ids, f"depends-on target orphan: {rec}")

        # strengthen-by: claim_id must appear in claims.jsonl.
        sb_path = self.index_dir / "strengthen-by.jsonl"
        for ln in _read_jsonl_lines(sb_path):
            rec = json.loads(ln)
            self.assertIn(
                rec["claim_id"],
                canonical_ids,
                f"strengthen-by claim_id orphan: {rec}",
            )

        # cites: claim_id must appear in claims.jsonl.
        cites_path = self.index_dir / "cites.jsonl"
        for ln in _read_jsonl_lines(cites_path):
            rec = json.loads(ln)
            self.assertIn(
                rec["claim_id"],
                canonical_ids,
                f"cites claim_id orphan: {rec}",
            )

        # subtree-aggregates: every id within each subtree_claims must
        # appear in claims.jsonl.
        agg_path = self.index_dir / "subtree-aggregates.jsonl"
        for ln in _read_jsonl_lines(agg_path):
            rec = json.loads(ln)
            for cid in rec["subtree_claims"]:
                self.assertIn(
                    cid,
                    canonical_ids,
                    f"subtree-aggregates orphan id {cid} in {rec['node_path']}",
                )

    def test_sort_order(self):
        # claims.jsonl sorted by (node_type, id)
        claims_keys = [
            (json.loads(ln).get("node_type", "claim"), json.loads(ln)["id"])
            for ln in _read_jsonl_lines(self.index_dir / "claims.jsonl")
        ]
        self.assertEqual(claims_keys, sorted(claims_keys))

        # depends-on.jsonl sorted by (source, target, context)
        dep_keys = [
            (
                json.loads(ln)["source"],
                json.loads(ln)["target"],
                json.loads(ln).get("context") or "",
            )
            for ln in _read_jsonl_lines(self.index_dir / "depends-on.jsonl")
        ]
        self.assertEqual(dep_keys, sorted(dep_keys))

        # strengthen-by.jsonl sorted by (claim_id, item_idx)
        sb_keys = [
            (json.loads(ln)["claim_id"], json.loads(ln)["item_idx"])
            for ln in _read_jsonl_lines(self.index_dir / "strengthen-by.jsonl")
        ]
        self.assertEqual(sb_keys, sorted(sb_keys))

        # cites.jsonl sorted by (claim_id, leaf_path)
        cite_keys = [
            (json.loads(ln)["claim_id"], json.loads(ln)["leaf_path"])
            for ln in _read_jsonl_lines(self.index_dir / "cites.jsonl")
        ]
        self.assertEqual(cite_keys, sorted(cite_keys))

        # subtree-aggregates.jsonl sorted by node_path
        agg_keys = [
            json.loads(ln)["node_path"] for ln in _read_jsonl_lines(self.index_dir / "subtree-aggregates.jsonl")
        ]
        self.assertEqual(agg_keys, sorted(agg_keys))


class TestRefreshSolidityWriteBack(unittest.TestCase):
    """Refresh writes derived solidity into claim-quality.md, idempotently.

    Runs refresh against a per-class tempdir copy of the fixture. The
    committed fixture is never touched.
    """

    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory()
        cls.addClassCleanup(cls._tmp.cleanup)
        cls.kb_root = _materialize_fixture(Path(cls._tmp.name))
        # First run: bring the fixture copy to a fully-refreshed state.
        first = _run_refresh(cls.kb_root)
        assert first.returncode == 0, first.stderr
        # Discover the canonical claim-quality.md files dynamically so a
        # change in fixture shape doesn't require a hand-edited file list.
        cls.cq_files = sorted(p.relative_to(cls.kb_root).as_posix() for p in cls.kb_root.rglob("claim-quality.md"))
        cls.cq_hashes = {rel: _hash_file(cls.kb_root / rel) for rel in cls.cq_files}

    def test_solidity_lines_idempotent(self):
        # A second refresh must leave every claim-quality.md byte-identical.
        second = _run_refresh(self.kb_root)
        self.assertEqual(second.returncode, 0, second.stderr)
        for rel in self.cq_files:
            with self.subTest(file=rel):
                self.assertEqual(
                    self.cq_hashes[rel],
                    _hash_file(self.kb_root / rel),
                    f"{rel} changed on a second refresh (not idempotent)",
                )

    def test_all_claim_entries_carry_derived_solidity(self):
        # After refresh, every claim entry's on-disk parsed solidity equals
        # compute_solidity's output. Content-independent: loops over all
        # entries rather than hard-coding any (claim, value) pair. A claim
        # with no computable solidity carries None on disk and is absent from
        # the compute_solidity result — both sides agree on None.
        state = lib.discover_kb(self.kb_root, diagnostic_stream=None)
        # Pass experiments AND supports — refresh's write-back computes solidity
        # WITH both (max-branch experiment rescues and DERIVATION-branch support
        # lifts), so the freshness oracle here must use the same inputs or it
        # flags experiment-rescued / support-lifted values as stale.
        sol = lib.compute_solidity(state.claim_entries, state.experiments, state.supports, state.works)
        for entry in state.claim_entries:
            with self.subTest(claim=entry.id):
                self.assertEqual(entry.solidity, sol.get(entry.id))

    def test_solidity_line_has_arithmetic_trace_when_deps_present(self):
        # A claim with a computable solidity AND dependencies carries a
        # `[= ...]` arithmetic trace on its solidity line; a claim with a
        # computable solidity and no dependencies carries none. Targets are
        # found dynamically and the expected line is rebuilt via the refresh
        # module's own `_solidity_line`, so the test holds regardless of
        # which claims/values are current.
        refresh = _load_refresh_module()
        state = lib.discover_kb(self.kb_root, diagnostic_stream=None)
        full = lib.compute_solidity_full(state.claim_entries, state.experiments, state.supports, state.works)
        sol = {cid: r.final for cid, r in full.items() if r.final is not None}

        with_deps = next(e for e in state.claim_entries if e.depends_on and sol.get(e.id) is not None)
        no_deps = next(e for e in state.claim_entries if not e.depends_on and sol.get(e.id) is not None)

        for entry, expect_trace in ((with_deps, True), (no_deps, False)):
            trace = lib.render_solidity_trace(full[entry.id])
            line = refresh._solidity_line(sol[entry.id], trace)
            text = (self.kb_root / entry.canonical_path).read_text(encoding="utf-8")
            # The canonical line must appear verbatim on disk (refresh ran).
            self.assertIn(line, text, f"{entry.id}: canonical solidity line not on disk")
            if expect_trace:
                self.assertIn("[= ", line, f"{entry.id} has deps but no trace")
                # The weakest-link min form, not a product.
                self.assertNotIn(" × ", line, f"{entry.id}: stale product trace")
            else:
                self.assertNotIn("[= ", line, f"{entry.id} no deps but a trace")

    def test_every_trace_evaluates_to_the_value_it_annotates(self):
        """The trace is arithmetic, so it must produce the number beside it.

        This is the invariant the old renderer broke on both non-trivial
        branches: a support-lifted claim printed its PRE-lift confidence, and an
        experimentally-rescued claim printed a min() when a max() had set the
        value. Both rendered equations whose own answer was not the value they
        annotated, and nothing checked.
        """
        state = lib.discover_kb(self.kb_root, diagnostic_stream=None)
        full = lib.compute_solidity_full(state.claim_entries, state.experiments, state.supports, state.works)
        # The three forms the renderer emits: the dep-gate min, the max that a
        # rescuing experiment won, and the bare rescue of a claim whose own
        # derivation is pending (nothing to take a max against).
        binary = re.compile(r"\[=\s*(min|max)\(([0-9.]+),\s*([0-9.]+)\)\]")
        unary = re.compile(r"\[=\s*experimental\s+([0-9.]+)\]")

        checked = 0
        for cid, result in full.items():
            trace = lib.render_solidity_trace(result)
            if not trace:
                continue
            match = binary.search(trace)
            if match is None:
                solo = unary.search(trace)
                self.assertIsNotNone(solo, f"{cid}: unparseable trace {trace!r}")
                evaluated = float(solo.group(1))
            else:
                op, a, b = match.group(1), float(match.group(2)), float(match.group(3))
                evaluated = min(a, b) if op == "min" else max(a, b)
            self.assertAlmostEqual(
                evaluated,
                result.final,
                places=2,
                msg=f"{cid}: trace {trace.strip()} does not evaluate to {result.final}",
            )
            checked += 1
        self.assertTrue(checked, "fixture produced no traces to check")

    def test_depends_on_annotations_synced(self):
        # Every claim-target depends-on (solidity X) annotation equals the
        # target's computed solidity after refresh.
        state = lib.discover_kb(self.kb_root, diagnostic_stream=None)
        sol = lib.compute_solidity(state.claim_entries)
        for entry in state.claim_entries:
            for edge in entry.depends_on:
                if edge.target_kind != "claim":
                    continue
                if edge.target_solidity_recorded is None:
                    continue
                with self.subTest(claim=entry.id, target=edge.target):
                    self.assertAlmostEqual(
                        edge.target_solidity_recorded,
                        sol[edge.target],
                        places=2,
                    )


def _load_refresh_module():
    """Import the refresh_kb_metadata module for direct in-process testing."""
    from kb_tools import refresh_kb_metadata

    return refresh_kb_metadata


# A `kind: index` node in a file NOT named index.md, with both derived lists
# empty. `compute_subtree_aggregates` — what the verifier checks drift against —
# enumerates it by kind; a filename-driven emitter walk never sees it.
_ALT_NAMED_INDEX = """\
[↑ Mini-KB Entry Point](../index.md)

<!-- kb-frontmatter
kind: index
subtree-claims: []
subtree-experiments: []
-->

# Alternate Index

A `kind: index` node in a file not named index.md.
"""


class TestRefreshNodeDiscoveryByKind(unittest.TestCase):
    """Refresh enumerates aggregate nodes by ``kind``, never by filename.

    The emitter used to walk for the literal filename ``index.md`` plus a
    sibling ``entry-point.md`` special case, while the checker enumerates
    ``kind: index`` / ``kind: entry-point`` from ``discover_kb``. A node whose
    kind was right and whose filename differed was therefore verified but never
    refreshed — a subtree-claims/subtree-experiments drift FAIL that no run of
    refresh could clear.
    """

    ALT_REL = "common/overview.md"

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.kb_root = _materialize_fixture(Path(self._tmp.name))
        (self.kb_root / self.ALT_REL).write_text(_ALT_NAMED_INDEX, encoding="utf-8")

    def test_alt_named_index_node_is_refreshed(self):
        result = _run_refresh(self.kb_root)
        self.assertEqual(result.returncode, 0, result.stderr)

        state = lib.discover_kb(self.kb_root, diagnostic_stream=None)
        aggregates = lib.compute_subtree_aggregates(state)
        self.assertIn(self.ALT_REL, aggregates, "the checker no longer enumerates the alternately-named node")
        expected_claims, expected_experiments = aggregates[self.ALT_REL]
        # Guard the test's own premise: an empty expectation would pass against
        # an emitter that wrote nothing at all.
        self.assertTrue(expected_claims, "fixture shape changed: no claims under common/")
        self.assertTrue(expected_experiments, "fixture shape changed: no experiments under common/")

        node = next(idx for idx in state.indexes if idx.path == self.ALT_REL)
        self.assertEqual(list(node.declared_subtree_claims), expected_claims)
        self.assertEqual(list(node.declared_subtree_experiments), expected_experiments)

    def test_verify_passes_after_refresh(self):
        self.assertEqual(_run_refresh(self.kb_root).returncode, 0)
        check = subprocess.run(
            [sys.executable, "-m", "kb_tools.verify_kb_metadata", "--kb-root", str(self.kb_root)],
            cwd=_REPO_ROOT,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(check.returncode, 0, f"verify FAILed after refresh:\n{check.stdout}\n{check.stderr}")

    def test_refresh_reaches_a_fixed_point(self):
        self.assertEqual(_run_refresh(self.kb_root).returncode, 0)
        before = _hash_file(self.kb_root / self.ALT_REL)
        second = _run_refresh(self.kb_root)
        self.assertEqual(second.returncode, 0, second.stderr)
        self.assertIn("Updated 0 subtree-claims field(s).", second.stdout)
        self.assertEqual(before, _hash_file(self.kb_root / self.ALT_REL))


# A scored external work and the claim that rests on it, spliced into the
# fixture copy. `clm-cc3333` is confidence 0.80 over one claim dep at 0.90, so
# the work's 0.40 becomes its weakest link: the gate is observable rather than
# a no-op, which no corpus that exists today would make it.
_WORK_KEY = "nobody2026"
_WORK_ENTRY = """
---

## Nobody, A. (2026). A Work This Corpus Cites And Does Not Contain.
<!-- id: work-nobody2026 -->

### Quality
- strength: 0.40
- rationale: synthetic external work, hand-scored so the endcap gate is live.
"""
_RESTS_ON_BULLET = "  - work-nobody2026 — Nobody (2026) (applicability 1.0)\n"


class TestRefreshOverAScoredExternalWork(unittest.TestCase):
    """The endcap gate, end to end through refresh and verify.

    A `rests-on` edge at a positive applicability puts the work's own
    hand-authored `strength` into the citing claim's dep-gate `min`. Refresh
    must write that through, reach a fixed point, leave the work's own entry
    untouched (nothing about a work is derived), and leave verify green.
    """

    ANCHOR = "- confidence: 0.80\n- depends-on:\n"

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.kb_root = _materialize_fixture(Path(self._tmp.name))
        self.cq_path = self.kb_root / "claim-quality.md"
        text = self.cq_path.read_text(encoding="utf-8")
        self.assertIn(self.ANCHOR, text, "fixture shape changed: clm-cc3333 is no longer 0.80 with a depends-on list")
        self.cq_path.write_text(
            text.replace(self.ANCHOR, self.ANCHOR + _RESTS_ON_BULLET, 1) + _WORK_ENTRY,
            encoding="utf-8",
        )

    def test_the_work_strength_becomes_the_citing_claims_solidity(self):
        self.assertEqual(_run_refresh(self.kb_root).returncode, 0)

        state = lib.discover_kb(self.kb_root, diagnostic_stream=None)
        entry = next(e for e in state.claim_entries if e.id == "clm-cc3333")
        self.assertEqual(entry.solidity, 0.40)
        # Both operands of the written trace are numbers a reader finds on disk:
        # the claim's own confidence, and the work's `- strength:` line.
        self.assertEqual(entry.solidity_trace.strip(), "[= min(0.80, 0.40)]")

    def test_refresh_is_idempotent_and_verify_is_green(self):
        self.assertEqual(_run_refresh(self.kb_root).returncode, 0)
        after_first = {
            rel: _hash_file(self.kb_root / rel)
            for rel in sorted(p.relative_to(self.kb_root).as_posix() for p in self.kb_root.rglob("*.md"))
        }

        second = _run_refresh(self.kb_root)
        self.assertEqual(second.returncode, 0, second.stderr)
        for rel, digest in after_first.items():
            with self.subTest(file=rel):
                self.assertEqual(digest, _hash_file(self.kb_root / rel), f"{rel} changed on a second refresh")

        check = subprocess.run(
            [sys.executable, "-m", "kb_tools.verify_kb_metadata", "--kb-root", str(self.kb_root)],
            cwd=_REPO_ROOT,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(check.returncode, 0, f"verify FAILed:\n{check.stdout}\n{check.stderr}")

    def test_refresh_writes_no_byte_of_the_work_entry_or_its_bullet(self):
        # Nothing about a work is derived — not its strength, not the pairing's
        # applicability — so refresh has nothing to write on either.
        self.assertEqual(_run_refresh(self.kb_root).returncode, 0)
        text = self.cq_path.read_text(encoding="utf-8")
        self.assertIn(_WORK_ENTRY.rstrip("\n"), text)
        self.assertIn(_RESTS_ON_BULLET.rstrip("\n"), text)

    def test_a_dangling_work_target_survives_the_computation_and_fails_at_the_index(self):
        """Refresh computes solidity BEFORE the coverage guard, so it must not raise there."""
        self.cq_path.write_text(
            self.cq_path.read_text(encoding="utf-8").replace(_WORK_ENTRY, ""),
            encoding="utf-8",
        )
        result = _run_refresh(self.kb_root)

        self.assertNotEqual(result.returncode, 0)
        # The solidity pass ran to completion and printed its own line; the run
        # died later, at the index emission, naming the key.
        self.assertIn("[refresh] Rewrote solidity in", result.stdout)
        self.assertIn("ExternalWorkParseError", result.stderr)
        self.assertIn(f"work-{_WORK_KEY}", result.stderr)


# A synthetic claim-quality.md exercising the dormant numeric-confidence-
# blocked-by-pending-dependency path. ``clm-pppppp`` has pending confidence;
# ``clm-aaaaaa`` has numeric confidence (0.90) but depends on it — so its
# solidity is *pending* too. Both the solidity line and the (solidity X)
# annotation below carry STALE numeric values that refresh must correct to
# the *pending* form.
_SYNTHETIC_BLOCKED_CQ = """\
## Pending Upstream Claim
<!-- id: clm-pppppp -->

Body.

### Quality
- confidence: *pending*
- solidity: *pending*
- rationale: *pending*
- strengthen-by:
  - Assess this claim.

---

## Numeric Claim Blocked By A Pending Dependency
<!-- id: clm-aaaaaa -->

Body.

### Quality
- confidence: 0.90
- depends-on:
  - clm-pppppp — Pending Upstream Claim (solidity 0.42) [stale numeric annotation]
- solidity: 0.85 (ok to build on) [= 0.90 × 0.95]
- rationale: A numeric-confidence claim whose only dependency is pending.
- strengthen-by:
  - Assess the upstream claim.
"""


class TestRefreshSolidityPendingWriteBack(unittest.TestCase):
    """Refresh renders a numeric-confidence-blocked claim as ``*pending*``.

    Drives the solidity write-back over an inline synthetic fixture that
    exercises the dormant blocked-by-pending-dependency path. Fully
    independent of any KB state — the input is the inline string above.
    """

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.kb_root = Path(self._tmp.name)
        self.cq_path = self.kb_root / "claim-quality.md"
        self.cq_path.write_text(_SYNTHETIC_BLOCKED_CQ, encoding="utf-8")
        self.refresh = _load_refresh_module()

    def tearDown(self):
        self._tmp.cleanup()

    def _rewrite(self):
        entries = lib.parse_claim_quality_file(self.cq_path, self.kb_root)
        full = lib.compute_solidity_full(entries)
        solidity = {cid: r.final for cid, r in full.items() if r.final is not None}
        # The blocked claim must be absent from the computed result.
        self.assertNotIn("clm-aaaaaa", solidity)
        self.assertNotIn("clm-pppppp", solidity)
        self.refresh._rewrite_claim_quality_solidity(self.cq_path, entries, full)
        return self.cq_path.read_text(encoding="utf-8")

    def test_blocked_claim_solidity_line_rewritten_to_pending(self):
        text = self._rewrite()
        # The stale numeric solidity line is gone; the *pending* form is in.
        self.assertNotIn("- solidity: 0.85", text)
        self.assertNotIn("ok to build on", text)
        # Exactly two *pending* solidity lines: the upstream claim and the
        # now-corrected blocked claim.
        pending_lines = [ln for ln in text.splitlines() if ln.strip() == "- solidity: *pending*"]
        self.assertEqual(len(pending_lines), 2)

    def test_blocked_target_annotation_rewritten_to_pending(self):
        text = self._rewrite()
        # The depends-on bullet's stale (solidity 0.42) annotation is synced
        # to the pending form.
        self.assertNotIn("(solidity 0.42)", text)
        self.assertIn("(solidity *pending*)", text)

    def test_rewrite_is_idempotent(self):
        first = self._rewrite()
        # A second pass over the now-corrected content changes nothing.
        entries = lib.parse_claim_quality_file(self.cq_path, self.kb_root)
        self.refresh._rewrite_claim_quality_solidity(self.cq_path, entries, lib.compute_solidity_full(entries))
        self.assertEqual(first, self.cq_path.read_text(encoding="utf-8"))


class TestSubtreeFieldRewriteSpans(unittest.TestCase):
    """A multi-line field is replaced WHOLE, not by its first line.

    The reader now accepts wrapped and YAML-block id-lists. Rewriting only the
    opening line of one would strand its tail as orphaned text for the next
    parse to fold into whatever field followed — accepting a shape on read and
    mangling it on write is the emitter/checker split at its most destructive.
    """

    def setUp(self):
        from kb_tools import refresh_kb_metadata

        self.refresh = refresh_kb_metadata

    def _rewrite(self, body: str) -> dict:
        text = f"<!-- kb-frontmatter\n{body}\n-->\n\n# Title\n"
        out = self.refresh.replace_subtree_claims(text, ["clm-cccccc"])
        fm = lib.parse_frontmatter(out)
        self.assertIsNotNone(fm, out)
        return {"text": out, "fm": fm}

    def test_wrapped_field_replaced_whole(self):
        got = self._rewrite("kind: index\nsubtree-claims: [clm-aaaaaa,\n                 clm-bbbbbb]")
        self.assertEqual(got["fm"]["subtree-claims"], ["clm-cccccc"])
        # No orphaned tail left behind anywhere in the document.
        self.assertNotIn("clm-bbbbbb", got["text"])
        self.assertEqual(got["fm"]["kind"], "index")

    def test_yaml_block_field_replaced_whole(self):
        got = self._rewrite("kind: index\nsubtree-claims:\n  - clm-aaaaaa\n  - clm-bbbbbb")
        self.assertEqual(got["fm"]["subtree-claims"], ["clm-cccccc"])
        self.assertNotIn("clm-bbbbbb", got["text"])
        self.assertEqual(got["fm"]["kind"], "index")

    def test_following_field_survives_the_replacement(self):
        got = self._rewrite("subtree-claims: [clm-aaaaaa,\n                 clm-bbbbbb]\nkind: index")
        self.assertEqual(got["fm"]["kind"], "index")
        self.assertEqual(got["fm"]["subtree-claims"], ["clm-cccccc"])

    def test_single_line_field_unchanged_in_shape(self):
        got = self._rewrite("kind: index\nsubtree-claims: [clm-aaaaaa]")
        self.assertEqual(got["fm"]["subtree-claims"], ["clm-cccccc"])
        self.assertIn("subtree-claims: [clm-cccccc]", got["text"])

    def test_rewrite_is_idempotent_across_shapes(self):
        once = self._rewrite("kind: index\nsubtree-claims: [clm-aaaaaa,\n                 clm-bbbbbb]")["text"]
        twice = self.refresh.replace_subtree_claims(once, ["clm-cccccc"])
        self.assertEqual(once, twice)


if __name__ == "__main__":
    unittest.main()
