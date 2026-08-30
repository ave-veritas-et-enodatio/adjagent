"""Tests for the shared Markdown link primitives (``kb_tools/kb_links.py``).

These primitives back both ``verify_md_links.py`` and the ``kb_cmd``
reverse-find. The behavior-preservation of the verifier refactor is covered by
``test_verify_md_links.py`` (which exercises the same functions through the
verifier module); this file adds a direct smoke test of the shared module and
confirms the verifier really imports the single shared copy.
"""

from pathlib import Path

from kb_tools import kb_links


def _load_verifier():
    from kb_tools import verify_md_links

    return verify_md_links


def test_strip_code_blanks_fences_and_inline_preserving_lines() -> None:
    text = "a\n```\nb\nc\n```\nd `e` f\n"
    stripped = kb_links.strip_code(text)
    assert len(stripped.splitlines()) == len(text.splitlines())
    assert "b" not in stripped and "c" not in stripped  # fence body blanked
    assert "`e`" not in stripped  # inline span blanked
    assert stripped.splitlines()[0] == "a"


def test_link_re_captures_target_only() -> None:
    m = kb_links.LINK_RE.search("see [the leaf](../sec4/leaf.md) here")
    assert m is not None
    assert m.group(1) == "../sec4/leaf.md"


def test_strip_target_drops_anchor_and_linenum() -> None:
    assert kb_links.strip_target("path/file.md#section") == "path/file.md"
    assert kb_links.strip_target("path/file.md:42") == "path/file.md"
    assert kb_links.strip_target("path/file.md#sec:99") == "path/file.md"


def test_iter_markdown_files_skips_skip_dirs(tmp_path: Path) -> None:
    (tmp_path / "keep.md").write_text("x", encoding="utf-8")
    (tmp_path / ".index").mkdir()
    (tmp_path / ".index" / "gen.md").write_text("y", encoding="utf-8")
    (tmp_path / "tests" / "fixtures").mkdir(parents=True)
    (tmp_path / "tests" / "fixtures" / "fix.md").write_text("z", encoding="utf-8")

    found = {p.relative_to(tmp_path).as_posix() for p in kb_links.iter_markdown_files(tmp_path)}
    assert found == {"keep.md"}


def test_verifier_imports_shared_primitives() -> None:
    """The verifier uses the single shared copy, not a private duplicate."""
    vml = _load_verifier()
    # Same function objects — proves the verifier re-exports kb_links, not a fork.
    assert vml.strip_code is kb_links.strip_code
    assert vml.strip_target is kb_links.strip_target
    assert vml.iter_markdown_files is kb_links.iter_markdown_files
    # The verifier scans through kb_links.LINK_RE (no private _LINK_RE remains).
    assert not hasattr(vml, "_LINK_RE")
