"""Tests for the derived ``> **Leaf references:**`` footer.

The footer is the reverse-citation map of a claim-quality entry — which leaves
host the entry's id. It is a derived field: ``refresh_kb_metadata`` regenerates
it from leaf frontmatter and ``verify_kb_metadata`` drift-gates it, exactly like
``subtree-claims`` and the derived ``solidity`` line — recomputed, never
authored.

Three layers exercised here:

* Unit — :func:`kb_index_lib.build_leaf_references` (the reverse map) and
  :func:`kb_index_lib.render_leaf_references` (footer text), verified directly.
* Integration (generation) — run ``refresh_kb_metadata.py`` against a small
  synthetic KB built in a tempdir; assert the footer it writes matches the
  derived one for the single-leaf, multi-leaf, exp-, and sup- cases, and that a
  second run is byte-identical (idempotent).
* Integration (drift gate) — hand-edit a footer in the refreshed KB and assert
  ``verify_kb_metadata.py`` fails with a leaf-references drift report.

Nothing here reads, writes, or asserts on ``kb-root/`` proper — the KB
is constructed file-by-file in a per-test tempdir.

Run via the project's test target (pytest).
"""

import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from kb_tools import kb_index_lib as lib

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
_REFRESH_MOD = "kb_tools.refresh_kb_metadata"
_VERIFY_MOD = "kb_tools.verify_kb_metadata"


def _run(module: str, kb_root: Path) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "-m", module, "--kb-root", str(kb_root)],
        cwd=_REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )


# ---------------------------------------------------------------------------
# A minimal but realistic synthetic KB. One register at vol-root, leaves under
# its directory citing claims, hosting one experiment and one support.
# ---------------------------------------------------------------------------

_CLAUDE_MD = """# Mini Invariants

### INVARIANT-S2: Core Axiom numbering

- Axiom 4: **Universal Saturation Kernel** — S(A).
"""

_ENTRY_POINT = """[↑ root](entry-point.md)

<!-- kb-frontmatter
kind: entry-point
subtree-claims: []
bootstrap: true
-->

# Entry
"""

_VOL_INDEX = """[↑ root](../entry-point.md)

<!-- kb-frontmatter
kind: index
subtree-claims: []
-->

# Vol Index
"""

# A register with NO footers authored — refresh must INSERT them. The single
# leaf cites clm-aa1111; the multi leaf cites clm-aa1111 + clm-bb2222 (so
# clm-aa1111 is cited by two leaves — the multi-leaf case). A support sup-ss1111
# and an experiment exp-ee1111 live in their own leaves.
_REGISTER = """# Vol Register

> **Canonicality preamble.** Synthetic register.

```markdown
## Example
<!-- id: clm-example -->
> **Leaf references:** fenced example must be ignored.
### Quality
```

---

## Anchor Claim
<!-- id: clm-aa1111 -->

Body prose for the anchor claim.

### Quality
- confidence: 0.90
- solidity: 0.90 (ok to build on)
- rationale: synthetic.
- strengthen-by:
  - work.

---

## Second Claim
<!-- id: clm-bb2222 -->

Body prose. This entry has a STALE hand-authored footer that refresh must fix.

> **Leaf references:** `wrong/path/that/does-not-exist.md` §5 (bogus annotation).

### Quality
- confidence: 0.60
- solidity: 0.60 (use as input only, don't build deeper)
- rationale: synthetic.
- strengthen-by:
  - work.

---

## Free-standing Support
<!-- id: sup-ss1111 -->

### Quality
- quality: 0.80
- solidity: 0.80 (ok to build on, see caveats)
- rationale: synthetic support.
- supports:
  - clm-aa1111 (f=1.0)
"""

_LEAF_SINGLE = """[↑ idx](index.md)

<!-- kb-frontmatter
kind: leaf
claims: [clm-aa1111]
-->

# Single
Body.
"""

_LEAF_MULTI = """[↑ idx](index.md)

<!-- kb-frontmatter
kind: leaf
claims: [clm-aa1111, clm-bb2222]
-->

# Multi

<!-- claim-quality: clm-aa1111 -->
A.

<!-- claim-quality: clm-bb2222 -->
B.
"""

_LEAF_EXP = """[↑ idx](index.md)

<!-- kb-frontmatter
kind: leaf
no-claim: "hosts an experiment"
exp-id: exp-ee1111
status: run
strengthens:
  - clm-aa1111: 1.0
-->

# Experiment Leaf
Body.
"""

_LEAF_SUP = """[↑ idx](index.md)

<!-- kb-frontmatter
kind: leaf
no-claim: "hosts a support"
sup-id: sup-ss1111
supports:
  - clm-aa1111: 1.0
-->

# Support Leaf
Body.
"""


def _build_kb(root: Path) -> None:
    """Materialize the synthetic KB under ``root``."""
    (root / "CLAUDE.md").write_text(_CLAUDE_MD, encoding="utf-8")
    (root / "entry-point.md").write_text(_ENTRY_POINT, encoding="utf-8")
    vol = root / "vol"
    vol.mkdir()
    (vol / "index.md").write_text(_VOL_INDEX, encoding="utf-8")
    (vol / "claim-quality.md").write_text(_REGISTER, encoding="utf-8")
    (vol / "leaf-single.md").write_text(_LEAF_SINGLE, encoding="utf-8")
    (vol / "leaf-multi.md").write_text(_LEAF_MULTI, encoding="utf-8")
    (vol / "leaf-exp.md").write_text(_LEAF_EXP, encoding="utf-8")
    (vol / "leaf-sup.md").write_text(_LEAF_SUP, encoding="utf-8")


class TestReverseMapUnit(unittest.TestCase):
    """Direct unit tests of build_leaf_references / render_leaf_references."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.kb = Path(self._tmp.name) / "kb"
        self.kb.mkdir()
        _build_kb(self.kb)
        self.state = lib.discover_kb(self.kb, diagnostic_stream=None)
        self.refs = lib.build_leaf_references(self.state)

    def test_claim_cited_by_multiple_leaves_lists_all_sorted(self):
        # clm-aa1111 is cited by leaf-single AND leaf-multi (and the exp leaf
        # strengthens it but does NOT host its id — strengthens is not a
        # citation, so it must NOT appear). Stable-sorted by path.
        self.assertEqual(
            self.refs["clm-aa1111"],
            ["vol/leaf-multi.md", "vol/leaf-single.md"],
        )

    def test_single_leaf_claim(self):
        self.assertEqual(self.refs["clm-bb2222"], ["vol/leaf-multi.md"])

    def test_experiment_home_is_its_hosting_leaf(self):
        self.assertEqual(self.refs["exp-ee1111"], ["vol/leaf-exp.md"])

    def test_support_home_is_its_hosting_leaf(self):
        self.assertEqual(self.refs["sup-ss1111"], ["vol/leaf-sup.md"])

    def test_render_relative_to_register_dir(self):
        footer = lib.render_leaf_references("vol/claim-quality.md", ["vol/leaf-single.md", "vol/leaf-multi.md"])
        self.assertEqual(
            footer,
            "> **Leaf references:** [leaf-single](./leaf-single.md), " "[leaf-multi](./leaf-multi.md).",
        )

    def test_render_root_register_keeps_full_path(self):
        footer = lib.render_leaf_references("claim-quality.md", ["vol/leaf-single.md"])
        self.assertEqual(
            footer,
            "> **Leaf references:** [leaf-single](./vol/leaf-single.md).",
        )

    def test_render_cross_directory_citation_climbs(self):
        # A per-volume register cited by a leaf OUTSIDE its directory (e.g. a
        # vol1 claim hosted by a common/ leaf) must climb with ../, not emit a
        # ./-prefixed path that resolves under the register dir and 404s.
        footer = lib.render_leaf_references("vol1/claim-quality.md", ["common/operators.md"])
        self.assertEqual(
            footer,
            "> **Leaf references:** [operators](../common/operators.md).",
        )

    def test_render_empty_is_explicit_marker(self):
        footer = lib.render_leaf_references("vol/claim-quality.md", [])
        self.assertTrue(footer.startswith("> **Leaf references:** *(none"))
        self.assertIn("bidirectional-coverage", footer)


class TestRefreshGeneration(unittest.TestCase):
    """Run refresh against the synthetic KB; assert footers + idempotency."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.kb = Path(self._tmp.name) / "kb"
        self.kb.mkdir()
        _build_kb(self.kb)
        self.register = self.kb / "vol" / "claim-quality.md"
        result = _run(_REFRESH_MOD, self.kb)
        self.assertEqual(result.returncode, 0, f"refresh failed: {result.stderr}")
        self.text = self.register.read_text(encoding="utf-8")

    def _footer_for(self, node_id: str) -> str:
        """Return the footer line within the entry whose id is ``node_id``."""
        lines = self.text.splitlines()
        in_entry = False
        for line in lines:
            if line.strip() == f"<!-- id: {node_id} -->":
                in_entry = True
                continue
            if in_entry:
                if line.startswith(lib.LEAF_REFERENCES_PREFIX):
                    return line
                if line.strip() == "### Quality":
                    self.fail(f"no footer found for {node_id}")
        self.fail(f"id {node_id} not located")

    def test_inserted_footer_for_footerless_entry(self):
        # clm-aa1111 had no footer; refresh inserts the two-leaf footer.
        self.assertEqual(
            self._footer_for("clm-aa1111"),
            "> **Leaf references:** [leaf-multi](./leaf-multi.md), " "[leaf-single](./leaf-single.md).",
        )

    def test_stale_footer_overwritten(self):
        # clm-bb2222 had a bogus hand-authored footer with a dead path + prose
        # annotation; refresh replaces it with the accurate single-leaf list.
        footer = self._footer_for("clm-bb2222")
        self.assertEqual(footer, "> **Leaf references:** [leaf-multi](./leaf-multi.md).")
        self.assertNotIn("does-not-exist", footer)
        self.assertNotIn("bogus", footer)

    def test_support_entry_footer(self):
        self.assertEqual(
            self._footer_for("sup-ss1111"),
            "> **Leaf references:** [leaf-sup](./leaf-sup.md).",
        )

    def test_idempotent(self):
        before = self.register.read_text(encoding="utf-8")
        result = _run(_REFRESH_MOD, self.kb)
        self.assertEqual(result.returncode, 0)
        self.assertEqual(
            self.register.read_text(encoding="utf-8"),
            before,
            "leaf-references footer not idempotent across refresh runs",
        )

    def test_quality_block_intact(self):
        # Inserting the footer must not disturb the `### Quality` section.
        self.assertIn("- confidence: 0.90", self.text)
        self.assertIn("### Quality", self.text)


class TestVerifyDriftGate(unittest.TestCase):
    """A hand-edited footer must fail verify as refresh-fixable drift."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.kb = Path(self._tmp.name) / "kb"
        self.kb.mkdir()
        _build_kb(self.kb)
        self.register = self.kb / "vol" / "claim-quality.md"
        self.assertEqual(_run(_REFRESH_MOD, self.kb).returncode, 0)

    def test_clean_after_refresh(self):
        result = _run(_VERIFY_MOD, self.kb)
        self.assertEqual(
            result.returncode,
            0,
            f"verify should pass on a freshly-refreshed KB:\n{result.stdout}",
        )

    def test_hand_edited_footer_fails(self):
        text = self.register.read_text(encoding="utf-8")
        edited = text.replace(
            "> **Leaf references:** [leaf-sup](./leaf-sup.md).",
            "> **Leaf references:** [leaf-sup](./leaf-sup.md) (hand-added prose).",
        )
        self.assertNotEqual(text, edited, "test setup failed to edit a footer")
        self.register.write_text(edited, encoding="utf-8")
        result = _run(_VERIFY_MOD, self.kb)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("leaf-references footer", result.stdout)

    def test_deleted_footer_fails_as_missing(self):
        text = self.register.read_text(encoding="utf-8")
        edited = text.replace(
            "> **Leaf references:** [leaf-sup](./leaf-sup.md).\n",
            "",
        )
        self.assertNotEqual(text, edited, "test setup failed to delete a footer")
        self.register.write_text(edited, encoding="utf-8")
        result = _run(_VERIFY_MOD, self.kb)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("(missing)", result.stdout)


# A fenced example footer sitting INSIDE an entry's own band — between the
# `<!-- id: -->` marker and `### Quality`. This is the shape that split the
# emitter from the checker: refresh searched raw lines and took the fenced line
# for the entry's footer; verify searched scrubbed lines, correctly did not see
# it, and reported the real footer missing on every run thereafter.
_FENCED_BAND = """## Second Claim
<!-- id: clm-bb2222 -->

Body prose. This entry has a STALE hand-authored footer that refresh must fix.

```markdown
> **Leaf references:** [example](./example.md).
```

> **Leaf references:** `wrong/path/that/does-not-exist.md` §5 (bogus annotation).
"""

_PLAIN_BAND = """## Second Claim
<!-- id: clm-bb2222 -->

Body prose. This entry has a STALE hand-authored footer that refresh must fix.

> **Leaf references:** `wrong/path/that/does-not-exist.md` §5 (bogus annotation).
"""

_FENCE_EXAMPLE = "```markdown\n> **Leaf references:** [example](./example.md).\n```"


class TestFencedFooterInEntryBand(unittest.TestCase):
    """A fenced example in an entry's band is not that entry's footer.

    Done-condition for the emitter/checker split: the example is neither
    corrupted by refresh nor reported missing forever by verify.
    """

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.kb = Path(self._tmp.name) / "kb"
        self.kb.mkdir()
        _build_kb(self.kb)
        self.register = self.kb / "vol" / "claim-quality.md"
        text = self.register.read_text(encoding="utf-8")
        self.assertIn(_PLAIN_BAND, text, "fixture shape changed")
        self.register.write_text(text.replace(_PLAIN_BAND, _FENCED_BAND, 1), encoding="utf-8")
        self.first = _run(_REFRESH_MOD, self.kb)
        self.assertEqual(self.first.returncode, 0, self.first.stderr)

    def test_fenced_example_is_not_rewritten(self):
        """refresh must not overwrite a documentation example with real links."""
        self.assertIn(_FENCE_EXAMPLE, self.register.read_text(encoding="utf-8"))

    def test_real_footer_is_written_into_the_band(self):
        text = self.register.read_text(encoding="utf-8")
        band = text.split("<!-- id: clm-bb2222 -->", 1)[1].split("### Quality", 1)[0]
        # The real footer names the leaf that actually cites clm-bb2222.
        self.assertIn("> **Leaf references:** [leaf-multi](./leaf-multi.md).", band)
        # ...and the bogus hand-authored one is gone.
        self.assertNotIn("does-not-exist.md", band)

    def test_verify_passes_after_refresh(self):
        """The permanent unfixable-loop half: verify must be clearable."""
        result = _run(_VERIFY_MOD, self.kb)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_second_refresh_is_a_fixed_point(self):
        before = self.register.read_text(encoding="utf-8")
        second = _run(_REFRESH_MOD, self.kb)
        self.assertEqual(second.returncode, 0, second.stderr)
        self.assertEqual(before, self.register.read_text(encoding="utf-8"))


_SOLIDITY_FENCE = "```markdown\n- solidity: 0.11 (refuted, do not use)\n```"


class TestFencedSolidityLineInQualitySection(unittest.TestCase):
    """The same raw-vs-scrubbed split, on the solidity line.

    The checker reads its on-disk values from ``parse_claim_quality_file``,
    which parses scrubbed text; the emitter matched raw lines. A ``- solidity:``
    line in a fenced example inside a Quality section was therefore rewritten by
    refresh and never read by verify — the identical trap, one field over.
    """

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.kb = Path(self._tmp.name) / "kb"
        self.kb.mkdir()
        _build_kb(self.kb)
        self.register = self.kb / "vol" / "claim-quality.md"
        text = self.register.read_text(encoding="utf-8")
        anchor = "### Quality\n- confidence: 0.90\n"
        self.assertIn(anchor, text, "fixture shape changed")
        self.register.write_text(text.replace(anchor, anchor + _SOLIDITY_FENCE + "\n", 1), encoding="utf-8")
        result = _run(_REFRESH_MOD, self.kb)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_fenced_solidity_example_is_not_rewritten(self):
        self.assertIn(_SOLIDITY_FENCE, self.register.read_text(encoding="utf-8"))

    def test_the_real_solidity_line_is_still_maintained(self):
        """The fence must not shield the entry's actual solidity line.

        clm-aa1111 is strengthened by exp-ee1111 at 1.0, so its final solidity
        is the experimental branch's 1.00 — refresh corrects the fixture's
        authored 0.90 to it, proving the real line was still reached.
        """
        text = self.register.read_text(encoding="utf-8")
        self.assertIn("- solidity: 1.00 (ok to build on)", text)
        # The fenced example keeps its own value — it was never a candidate.
        self.assertIn("- solidity: 0.11 (refuted, do not use)", text)

    def test_verify_passes(self):
        result = _run(_VERIFY_MOD, self.kb)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


class TestEmitterCheckerSingleSource(unittest.TestCase):
    """The emitter and the checker must not merely agree — they must be one.

    Each of these guards a duplicate implementation that once slipped in. A
    test that only compared their OUTPUTS would pass again the moment someone
    re-forked the logic and kept it briefly in sync.
    """

    def test_both_sides_locate_footers_through_the_library(self):
        from kb_tools import refresh_kb_metadata, verify_kb_metadata

        self.assertTrue(hasattr(lib, "locate_leaf_reference_footers"))
        for module in (refresh_kb_metadata, verify_kb_metadata):
            source = Path(module.__file__).read_text(encoding="utf-8")
            self.assertIn("locate_leaf_reference_footers", source, module.__name__)

    def test_refresh_has_no_private_leaf_walk(self):
        """subtree-claims had THREE implementations; refresh's walk was one."""
        from kb_tools import refresh_kb_metadata

        self.assertFalse(hasattr(refresh_kb_metadata, "collect_leaves"))

    def test_subtree_claims_check_consumes_the_shared_aggregate(self):
        from kb_tools import verify_kb_metadata

        source = Path(verify_kb_metadata.__file__).read_text(encoding="utf-8")
        checker = source.split("def check_subtree_consistency", 1)[1].split("\ndef ", 1)[0]
        self.assertIn("compute_subtree_aggregates", checker)

    def test_refresh_serializes_through_the_library(self):
        """The emitter must not hand-roll the format the checker imports."""
        from kb_tools import refresh_kb_metadata

        source = Path(refresh_kb_metadata.__file__).read_text(encoding="utf-8")
        emitter = source.split("def _emit_jsonl_indexes", 1)[1].split("\ndef ", 1)[0]
        self.assertIn("kb_index_lib.serialize_records", emitter)
        self.assertNotIn("json.dumps", emitter)


class TestSolidityTraceIsGated(unittest.TestCase):
    """A green verify must mean refresh is a fixed point.

    The arithmetic trace was written by refresh and read by nobody, so it could
    be mangled, contradicted, or deleted and the gate stayed green — while the
    next refresh would rewrite it. Three tampers, each of which verify must now
    catch.
    """

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.kb = Path(self._tmp.name) / "kb"
        self.kb.mkdir()
        _build_kb(self.kb)
        self.register = self.kb / "vol" / "claim-quality.md"
        # Converge first: the tamper must be the only thing verify can object to.
        self.assertEqual(_run(_REFRESH_MOD, self.kb).returncode, 0)
        self.assertEqual(_run(_VERIFY_MOD, self.kb).returncode, 0)

    def _tamper(self, old: str, new: str) -> None:
        text = self.register.read_text(encoding="utf-8")
        self.assertIn(old, text, "tamper anchor not present after refresh")
        self.register.write_text(text.replace(old, new, 1), encoding="utf-8")

    def _trace_line(self) -> str:
        for line in self.register.read_text(encoding="utf-8").splitlines():
            if line.startswith("- solidity:") and "[=" in line:
                return line
        self.fail("refresh wrote no arithmetic trace to tamper with")

    def test_mangled_trace_is_caught(self):
        line = self._trace_line()
        self._tamper(line, re.sub(r"\[= .*\]", "[= min(0.01, 0.02)]", line))
        result = _run(_VERIFY_MOD, self.kb)
        self.assertNotEqual(result.returncode, 0, "a contradicting trace passed the gate")
        self.assertIn("trace", result.stdout)

    def test_deleted_trace_is_caught(self):
        line = self._trace_line()
        self._tamper(line, re.sub(r" \[= .*\]", "", line))
        self.assertNotEqual(_run(_VERIFY_MOD, self.kb).returncode, 0, "a deleted trace passed the gate")

    def test_refresh_output_passes_its_own_gate(self):
        """The fixed-point property itself: refresh -> verify -> refresh is a no-op."""
        before = self.register.read_text(encoding="utf-8")
        self.assertEqual(_run(_VERIFY_MOD, self.kb).returncode, 0)
        self.assertEqual(_run(_REFRESH_MOD, self.kb).returncode, 0)
        self.assertEqual(before, self.register.read_text(encoding="utf-8"))


class TestSolidityReportFormatting(unittest.TestCase):
    """Drift reports render both sides at 2dp.

    The on-disk value was interpolated bare while the expected one was
    formatted `{:.2f}`, so a report could read "solidity 0.2, expected 0.20" —
    two identical numbers presented as a mismatch.
    """

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.kb = Path(self._tmp.name) / "kb"
        self.kb.mkdir()
        _build_kb(self.kb)
        self.register = self.kb / "vol" / "claim-quality.md"
        self.assertEqual(_run(_REFRESH_MOD, self.kb).returncode, 0)

    def test_stale_value_is_reported_at_two_decimal_places(self):
        text = self.register.read_text(encoding="utf-8")
        stale = re.sub(r"- solidity: 1\.00 ", "- solidity: 0.20 ", text, count=1)
        self.assertNotEqual(text, stale, "fixture shape changed")
        self.register.write_text(stale, encoding="utf-8")

        result = _run(_VERIFY_MOD, self.kb)
        self.assertNotEqual(result.returncode, 0)
        reported = [ln for ln in result.stdout.splitlines() if "solidity line" in ln]
        self.assertTrue(reported, result.stdout)
        for line in reported:
            self.assertIn("0.20", line)
            # No 1-dp rendering of a 2-dp value anywhere in the report line.
            self.assertFalse(re.findall(r"\b0\.\d\b(?!\d)", line), line)


if __name__ == "__main__":
    unittest.main()
