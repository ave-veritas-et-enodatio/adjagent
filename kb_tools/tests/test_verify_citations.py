"""Tests for the citation-grammar gate.

Every case builds its own tiny KB in a tmp tree: the committed ``mini-kb``
fixture is shaped for the index/graph tests and is deliberately NOT a
citation-grammar conformant corpus, so it is never used here.
"""

import json
from pathlib import Path

import pytest

from kb_tools import kb_links
from kb_tools import verify_citations as vc

_FRONTMATTER = "<!-- kb-frontmatter\nkind: {kind}\n-->\n\n"


def _kb(tmp_path: Path, files: dict[str, str]) -> Path:
    root = tmp_path / "kb-root"
    for relpath, content in files.items():
        target = root / relpath
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
    root.mkdir(parents=True, exist_ok=True)
    return root


def _scan(root: Path) -> list[vc.Finding]:
    return vc.scan(root, root / ".index")


def _checks(findings: list[vc.Finding]) -> set[str]:
    return {f.check for f in findings}


# ---------------------------------------------------------------------------
# Check 1 — channel exclusivity
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("prose", "expected"),
    [
        ("This follows per the build's Invariant 11 as designed.", True),
        ("This follows per design-doc Invariant 3.", True),
        ("See INVARIANT-S2 for the numbering.", True),
        ("The claim clm-aa1111 supports this.", True),
        ("Nothing citation-shaped here at all.", False),
    ],
)
def test_citation_shaped_prose_is_flagged(tmp_path: Path, prose: str, expected: bool) -> None:
    root = _kb(tmp_path, {"index.md": _FRONTMATTER.format(kind="index") + prose + "\n"})
    assert (_checks(_scan(root)) == {"channel"}) is expected


def test_fenced_examples_are_documentation_not_citations(tmp_path: Path) -> None:
    """Fence-aware from birth: an example must not trip the gate."""
    body = "Here is how to cite:\n\n```\nSee INVARIANT-S2 and clm-aa1111, per Invariant 11.\n```\n"
    root = _kb(tmp_path, {"index.md": _FRONTMATTER.format(kind="index") + body})
    assert _scan(root) == []


def test_sanctioned_channels_do_not_trip_the_gate(tmp_path: Path) -> None:
    register = (
        "## A Claim\n"
        "<!-- id: clm-aa1111 -->\n\n"
        "- confidence: 0.75\n"
        "- depends-on:\n"
        "  - INVARIANT-S2 (axiom scaffold)\n"
        "  - clm-bb2222 (a real dependency)\n"
        "- solidity: 0.75 (ok to build on, see caveats)\n"
    )
    leaf = _FRONTMATTER.format(kind="leaf") + "<!-- claim-quality: clm-aa1111 -->\n\nProse.\n"
    root = _kb(tmp_path, {"claim-quality.md": register, "leaf.md": leaf})
    assert _scan(root) == []


def test_leaf_bodies_are_exempt_but_frontmatter_is_not(tmp_path: Path) -> None:
    """A leaf is verbatim source; its obligations live in frontmatter/markers."""
    leaf = _FRONTMATTER.format(kind="leaf") + "The source says INVARIANT-S2 and clm-aa1111, per Invariant 4.\n"
    index = _FRONTMATTER.format(kind="index") + "The index says clm-aa1111.\n"
    root = _kb(tmp_path, {"leaf.md": leaf, "index.md": index})
    findings = _scan(root)
    assert {f.path for f in findings} == {"index.md"}


def test_invariant_section_may_name_its_siblings(tmp_path: Path) -> None:
    """Approved exemption, scoped to the framework source's declaration sections."""
    invariants = (
        "# Invariants\n\n"
        "### INVARIANT-S1: First rule\n\n"
        "Holds jointly with INVARIANT-S2, which numbers the axioms.\n\n"
        "### INVARIANT-S2: Axiom numbering\n\n"
        "- Axiom 1: **One** — a bullet.\n"
    )
    root = _kb(tmp_path, {"invariants.md": invariants})
    assert _scan(root) == []


def test_prose_outside_a_declaration_section_is_still_flagged(tmp_path: Path) -> None:
    """The exemption is the declaration sections, not the whole file."""
    invariants = (
        "# Invariants\n\n"
        "## Preamble\n\n"
        "This corpus relies on INVARIANT-S2 throughout.\n\n"
        "### INVARIANT-S2: Axiom numbering\n\nBody.\n"
    )
    root = _kb(tmp_path, {"invariants.md": invariants})
    findings = _scan(root)
    assert [f.line for f in findings] == [5]


def test_the_exemption_does_not_extend_to_other_files(tmp_path: Path) -> None:
    other = _FRONTMATTER.format(kind="index") + "### INVARIANT-S2: Not the framework source\n\nSee INVARIANT-S2.\n"
    root = _kb(tmp_path, {"invariants.md": "# Invariants\n", "domain/index.md": other})
    assert _checks(_scan(root)) == {"channel"}


# ---------------------------------------------------------------------------
# Checks 2, 3, 4 — referent, excerpt, durable target
# ---------------------------------------------------------------------------


def _cited_target() -> str:
    return "# Target\n\n## The Section\n\nThe load-bearing clause lives here, stated plainly.\n"


def test_well_formed_authority_citation_passes(tmp_path: Path) -> None:
    citing = (
        _FRONTMATTER.format(kind="index")
        + 'As established in ["The load-bearing clause lives here"](target.md#the-section).\n'
    )
    root = _kb(tmp_path, {"index.md": citing, "target.md": _cited_target()})
    assert _scan(root) == []


def test_unresolvable_target_is_flagged(tmp_path: Path) -> None:
    citing = _FRONTMATTER.format(kind="index") + 'See ["the clause"](nowhere.md#x).\n'
    root = _kb(tmp_path, {"index.md": citing})
    assert _checks(_scan(root)) == {"referent"}


def test_missing_anchor_is_flagged(tmp_path: Path) -> None:
    citing = _FRONTMATTER.format(kind="index") + 'See ["The load-bearing clause lives here"](target.md#no-such).\n'
    root = _kb(tmp_path, {"index.md": citing, "target.md": _cited_target()})
    assert _checks(_scan(root)) == {"referent"}


def test_excerpt_absent_from_the_section_is_flagged(tmp_path: Path) -> None:
    citing = _FRONTMATTER.format(kind="index") + 'See ["a clause never written there"](target.md#the-section).\n'
    root = _kb(tmp_path, {"index.md": citing, "target.md": _cited_target()})
    findings = _scan(root)
    assert _checks(findings) == {"excerpt"}
    assert "quoted verbatim" in findings[0].message


def test_excerpt_match_is_whitespace_normalized(tmp_path: Path) -> None:
    citing = (
        _FRONTMATTER.format(kind="index") + 'See ["The load-bearing   clause    lives here"](target.md#the-section).\n'
    )
    root = _kb(tmp_path, {"index.md": citing, "target.md": _cited_target()})
    assert _scan(root) == []


def test_quoted_excerpt_without_an_anchor_is_flagged(tmp_path: Path) -> None:
    citing = _FRONTMATTER.format(kind="index") + 'See ["The load-bearing clause lives here"](target.md).\n'
    root = _kb(tmp_path, {"index.md": citing, "target.md": _cited_target()})
    findings = _scan(root)
    assert _checks(findings) == {"excerpt"}
    assert "no #anchor" in findings[0].message


def test_bloated_excerpt_is_flagged_by_the_length_bound(tmp_path: Path) -> None:
    """Nothing else stops a citation pasting a whole section as link text."""
    long_clause = "x" * (vc.EXCERPT_MAX_CHARS + 1)
    target = f"# Target\n\n## The Section\n\n{long_clause}\n"
    citing = _FRONTMATTER.format(kind="index") + f'See ["{long_clause}"](target.md#the-section).\n'
    root = _kb(tmp_path, {"index.md": citing, "target.md": target})
    findings = _scan(root)
    assert _checks(findings) == {"excerpt"}
    assert "minimal quotation" in findings[0].message


# ---------------------------------------------------------------------------
# The false-pass set — four ways a citation verified against nothing
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("blank", ['""', '" "', '"\t"'])
def test_a_blank_excerpt_does_not_verify_against_everything(tmp_path: Path, blank: str) -> None:
    """``_normalize("") == ""``, and "" is a substring of every section.

    So a blank quotation passed against any target at all, while rendering as a
    checked authority citation.
    """
    citing = _FRONTMATTER.format(kind="index") + f"See [{blank}](target.md#the-section) for the rule.\n"
    root = _kb(tmp_path, {"index.md": citing, "target.md": _cited_target()})
    findings = _scan(root)
    assert _checks(findings) == {"excerpt"}
    assert "empty quoted excerpt" in findings[0].message


def test_an_excerpt_that_exists_only_inside_a_fence_at_the_target_is_not_authority(tmp_path: Path) -> None:
    """ "Every check is code-fence aware" held only of the citing file.

    The target was read raw, so a citation could rest on a fenced example —
    documentation *about* a rule quoted as though it were the rule.
    """
    target = "# Target\n\n## The Section\n\nThe real clause.\n\n```\nthe moon is made of cheese\n```\n"
    citing = _FRONTMATTER.format(kind="index") + 'Authority: ["the moon is made of cheese"](target.md#the-section).\n'
    root = _kb(tmp_path, {"index.md": citing, "target.md": target})
    assert _checks(_scan(root)) == {"excerpt"}


def test_an_anchor_that_exists_only_as_a_fenced_heading_does_not_resolve(tmp_path: Path) -> None:
    """The same blindness one level up: a heading inside a fence is not a section."""
    target = "# Target\n\n```markdown\n## Fake Section\n\ninvented authority text\n```\n"
    citing = _FRONTMATTER.format(kind="index") + 'Authority: ["invented authority text"](target.md#fake-section).\n'
    root = _kb(tmp_path, {"index.md": citing, "target.md": target})
    findings = _scan(root)
    assert _checks(findings) == {"referent"}
    assert "anchor" in findings[0].message


@pytest.mark.parametrize(("opening", "closing"), [("“", "”"), ("‘", "’")])
def test_typographic_quotes_do_not_skip_the_verbatim_check(tmp_path: Path, opening: str, closing: str) -> None:
    """Curled quotes made EXCERPT_RE miss, so the citation read as a plain reference link.

    An editor — or a model — that curls the quotes silently downgraded an
    authority citation to something no check verifies.
    """
    citing = (
        _FRONTMATTER.format(kind="index")
        + f"Authority: [{opening}a clause that is not in the target{closing}](target.md#the-section).\n"
    )
    root = _kb(tmp_path, {"index.md": citing, "target.md": _cited_target()})
    assert _checks(_scan(root)) == {"excerpt"}


def test_a_bracket_in_the_link_text_does_not_hide_the_citation_from_either_gate(tmp_path: Path) -> None:
    """``[^\\]]*`` cannot match ``["the rule [see note] applies"](...)``.

    A link the regex cannot see is a link no gate checks — so a ``]`` anywhere
    in an excerpt was enough to smuggle a broken target past both this gate and
    ``verify_md_links``.
    """
    link = '["the rule [see note] applies"](does-not-exist.md#nope)'
    citing = _FRONTMATTER.format(kind="index") + f"Authority: {link}.\n"
    root = _kb(tmp_path, {"index.md": citing})
    assert _checks(_scan(root)) == {"referent"}

    # The shared primitive is what makes "either gate" true.
    assert kb_links.LINK_RE.findall(link) == ["does-not-exist.md#nope"]


def test_excerpt_at_the_bound_passes(tmp_path: Path) -> None:
    clause = "y" * vc.EXCERPT_MAX_CHARS
    target = f"# Target\n\n## The Section\n\n{clause}\n"
    citing = _FRONTMATTER.format(kind="index") + f'See ["{clause}"](target.md#the-section).\n'
    root = _kb(tmp_path, {"index.md": citing, "target.md": target})
    assert _scan(root) == []


# Three levels up from kb-root/domain/sub/ is the repo root: two would land
# back inside kb-root and be an ordinary missing-referent failure.
@pytest.mark.parametrize("target", ["../../../.claude-temp/kb-build/design-doc.md#x", "../../../elsewhere/notes.md#x"])
def test_non_durable_targets_are_flagged(tmp_path: Path, target: str) -> None:
    citing = _FRONTMATTER.format(kind="index") + f'See ["the clause"]({target}).\n'
    root = _kb(tmp_path, {"domain/sub/index.md": citing})
    findings = _scan(root)
    assert _checks(findings) == {"durable"}
    assert "durable KB path" in findings[0].message


def test_external_links_are_not_citations(tmp_path: Path) -> None:
    citing = _FRONTMATTER.format(kind="index") + "See [the spec](https://example.invalid/spec).\n"
    root = _kb(tmp_path, {"index.md": citing})
    assert _scan(root) == []


# ---------------------------------------------------------------------------
# Check 5 — edge-backed foreign-domain references
# ---------------------------------------------------------------------------


def _indexed(root: Path, nodes: dict[str, str], edges: list[tuple[str, str]]) -> None:
    index = root / ".index"
    index.mkdir(parents=True, exist_ok=True)
    (index / "claims.jsonl").write_text(
        "".join(json.dumps({"id": i, "canonical_path": p}) + "\n" for i, p in nodes.items()), encoding="utf-8"
    )
    (index / "depends-on.jsonl").write_text(
        "".join(json.dumps({"source": s, "target": t}) + "\n" for s, t in edges), encoding="utf-8"
    )


_REGISTER = "## Entry\n" "<!-- id: clm-aa1111 -->\n\n" "Rationale prose naming clm-bb2222 from the other domain.\n"


def test_foreign_domain_reference_without_an_edge_is_flagged(tmp_path: Path) -> None:
    root = _kb(tmp_path, {"alpha/claim-quality.md": _REGISTER})
    _indexed(root, {"clm-aa1111": "alpha/one.md", "clm-bb2222": "beta/two.md"}, [])
    findings = [f for f in _scan(root) if f.check == "edge"]
    assert len(findings) == 1
    assert "no depends-on edge" in findings[0].message
    assert "no-edge:" in findings[0].message


def test_foreign_domain_reference_with_an_edge_passes(tmp_path: Path) -> None:
    root = _kb(tmp_path, {"alpha/claim-quality.md": _REGISTER})
    _indexed(root, {"clm-aa1111": "alpha/one.md", "clm-bb2222": "beta/two.md"}, [("clm-aa1111", "clm-bb2222")])
    assert [f for f in _scan(root) if f.check == "edge"] == []


def test_same_domain_reference_needs_no_edge(tmp_path: Path) -> None:
    root = _kb(tmp_path, {"alpha/claim-quality.md": _REGISTER})
    _indexed(root, {"clm-aa1111": "alpha/one.md", "clm-bb2222": "alpha/two.md"}, [])
    assert [f for f in _scan(root) if f.check == "edge"] == []


def test_no_edge_exemption_silences_the_entry(tmp_path: Path) -> None:
    register = (
        "## Entry\n"
        "<!-- id: clm-aa1111 -->\n\n"
        "- no-edge: cited as contrast, not as a dependency\n\n"
        "Rationale prose naming clm-bb2222 from the other domain.\n"
    )
    root = _kb(tmp_path, {"alpha/claim-quality.md": register})
    _indexed(root, {"clm-aa1111": "alpha/one.md", "clm-bb2222": "beta/two.md"}, [])
    assert [f for f in _scan(root) if f.check == "edge"] == []


# ---------------------------------------------------------------------------
# Scope boundary
# ---------------------------------------------------------------------------


def test_index_directory_is_out_of_scope(tmp_path: Path) -> None:
    """.index/ is contract and derived space, not build-authored content.

    Its SCHEMA.md carries a Build-invariants numbered list that is a near-miss
    for the plain-numbering pattern.
    """
    schema = "# Schema\n\n## Build invariants\n\n1. Invariant 1: the index is derived.\n"
    root = _kb(tmp_path, {".index/SCHEMA.md": schema})
    assert _scan(root) == []


def test_report_states_the_scope_boundary(tmp_path: Path, capsys: pytest.CaptureFixture) -> None:
    root = _kb(tmp_path, {"index.md": _FRONTMATTER.format(kind="index") + "Clean.\n"})
    assert vc.main(["--kb-root", str(root)]) == 0
    out = capsys.readouterr().out
    assert "leaf bodies exempt" in out
    assert ".index/ out of scope" in out
