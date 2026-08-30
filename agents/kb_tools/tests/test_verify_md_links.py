"""Self-test for kb_tools/verify_md_links.py.

Runs the checker against the fixture pair under tools/tests/fixtures/ and
asserts the exact finding set: a good link resolves, a broken intra link, a
broken inter link, a code-fence/inline example link is ignored, and an
unknown-id citation is flagged while the literal placeholder is not.

Run via the project's test target (pytest).
"""

import tempfile
from pathlib import Path

_FIXTURES = Path(__file__).resolve().parent / "fixtures"


def _load_module():
    from kb_tools import verify_md_links

    return verify_md_links


def test_fixture_findings() -> None:
    vml = _load_module()
    sample = _FIXTURES / "sample.md"
    # Treat the fixtures dir as the repo root so the inter-repo link escapes it.
    repo_root = _FIXTURES.resolve()
    body = vml.strip_code(sample.read_text(encoding="utf-8"))

    link_findings = vml.check_links(sample, body, repo_root)
    kinds = sorted((f.kind, f.target) for f in link_findings)

    # Good links (neighbor.md, with anchor, with :linenum) must NOT appear.
    assert ("broken intra", "neighbor.md") not in kinds
    # Broken intra link is flagged.
    assert ("broken intra", "does-not-exist.md") in kinds
    # Broken inter link (escapes fixtures root) is flagged as inter.
    assert ("broken inter", "../../../../Sibling-Repo/nope.md") in kinds
    # External link is skipped entirely.
    assert all("example.com" not in t for _, t in kinds)
    # Fenced and inline-code example links are ignored.
    assert all("not-real" not in t for _, t in kinds)
    # Exactly one broken intra + one broken inter from prose.
    assert sum(1 for k, _ in kinds if k == "broken intra") == 1
    assert sum(1 for k, _ in kinds if k == "broken inter") == 1

    # Id-validity: known set deliberately omits clm-zzzzzz.
    known_ids = {"clm-abc123"}
    id_findings = vml.check_ids(sample, body, known_ids)
    id_targets = sorted(f.target for f in id_findings)
    assert id_targets == ["clm-zzzzzz"], id_targets  # placeholder + fence excluded


def test_tex_and_home_targets_skipped() -> None:
    vml = _load_module()
    sample = _FIXTURES / "sample.md"
    repo_root = _FIXTURES.resolve()
    body = vml.strip_code(sample.read_text(encoding="utf-8"))

    targets = {f.target for f in vml.check_links(sample, body, repo_root)}
    # .tex targets (with and without a :linenum suffix) are never classified.
    assert not any(t.endswith(".tex") or ".tex:" in t for t in targets)
    assert "manuscript/vol_1/main.tex" not in targets
    assert "nope.tex:42" not in targets
    # Home-dir (~) targets are never classified.
    assert not any(t.startswith("~") for t in targets)


def test_skip_trees_excluded_from_crawl() -> None:
    vml = _load_module()
    repo_root = _FIXTURES.resolve()
    crawled = {p.resolve() for p in vml.iter_markdown_files(repo_root)}

    # _archive/ trees are skipped entirely; non-skipped siblings under the same
    # parent are still crawled.
    assert (repo_root / "skiptrees" / "_archive" / "arch.md").resolve() not in crawled
    assert (repo_root / "skiptrees" / "live.md").resolve() in crawled
    assert (repo_root / "skiptrees" / "scratch.md").resolve() in crawled

    # The .agents/ skip can't use a committed fixture (.agents/ is gitignored
    # repo-wide), so build the tree at runtime to exercise the crawl skip.
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        (root / ".agents").mkdir()
        (root / ".agents" / "note.md").write_text("x", encoding="utf-8")
        (root / "keep.md").write_text("y", encoding="utf-8")
        tmp_crawled = {p.resolve() for p in vml.iter_markdown_files(root)}
        assert (root / ".agents" / "note.md").resolve() not in tmp_crawled
        assert (root / "keep.md").resolve() in tmp_crawled


def test_all_files_are_error_sources() -> None:
    """No warn-only tier: every crawled .md file gates, regardless of location."""
    vml = _load_module()
    repo_root = Path("/repo")

    def src(rel: str) -> Path:
        return repo_root / rel

    assert vml.is_error_source(src("kb-root/common/foo.md"), repo_root)
    assert vml.is_error_source(src("kb-root/session/note.md"), repo_root)
    assert vml.is_error_source(src("README.md"), repo_root)
    assert vml.is_error_source(src("kb_tools/notes.md"), repo_root)
    assert vml.is_error_source(src("docs/README.md"), repo_root)


def test_gating_exit_code() -> None:
    """Every broken-intra gates (exit 1); broken-inter is handled by --inter-repo."""
    vml = _load_module()
    repo_root = Path("/repo")
    Finding = vml.Finding

    kb = Finding(repo_root / "kb-root/common/foo.md", 1, "broken intra", "missing.md")
    other = Finding(repo_root / "kb_tools/r.md", 1, "broken intra", "missing.md")

    assert vml.is_gating(kb, repo_root)
    assert vml.is_gating(other, repo_root)
    # broken inter is never gating here (handled by --inter-repo).
    inter = Finding(repo_root / "kb-root/common/foo.md", 1, "broken inter", "../x.md")
    assert not vml.is_gating(inter, repo_root)


def test_ignored_paths_carveout() -> None:
    """IGNORED_PATHS defaults empty; when populated, links into a listed dir never gate."""
    vml = _load_module()
    repo_root = Path("/repo").resolve()

    # Default is empty — no target is exempt out of the box.
    assert vml.IGNORED_PATHS == ()
    assert not vml._under_ignored_path((repo_root / "assets/generated/x.png").resolve(), repo_root)

    # With a project-side entry, the mechanism carves the tree out.
    original = vml.IGNORED_PATHS
    vml.IGNORED_PATHS = (Path("assets/generated"),)
    try:
        # A missing target under the carved-out dir is exempt.
        under = (repo_root / "kb-root/main/common").joinpath("../../../assets/generated/figures/missing.png").resolve()
        assert vml._under_ignored_path(under, repo_root)
        # The carved-out dir itself, referenced directly, is also exempt.
        assert vml._under_ignored_path((repo_root / "assets/generated").resolve(), repo_root)
        # A sibling asset path NOT in the carveout is not exempt.
        assert not vml._under_ignored_path((repo_root / "assets/figures/x.png").resolve(), repo_root)
        # A path outside the repo is not exempt.
        assert not vml._under_ignored_path(Path("/elsewhere/assets/generated/x.png"), repo_root)
    finally:
        vml.IGNORED_PATHS = original


def test_strip_code_preserves_line_numbers() -> None:
    vml = _load_module()
    text = "a\n```\nb\nc\n```\nd `e` f\n"
    stripped = vml.strip_code(text)
    assert len(stripped.splitlines()) == len(text.splitlines())
    assert "b" not in stripped and "c" not in stripped  # fence body blanked
    assert "`e`" not in stripped  # inline span blanked
    assert stripped.splitlines()[0] == "a"


if __name__ == "__main__":
    test_fixture_findings()
    test_tex_and_home_targets_skipped()
    test_skip_trees_excluded_from_crawl()
    test_all_files_are_error_sources()
    test_gating_exit_code()
    test_ignored_paths_carveout()
    test_strip_code_preserves_line_numbers()
    print("OK: all self-tests passed")
