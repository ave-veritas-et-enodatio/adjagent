"""Tests for the shared Markdown link primitives (``kb_tools/kb_links.py``).

These primitives back both ``verify_md_links.py`` and the ``kb_cmd``
reverse-find. The behavior-preservation of the verifier refactor is covered by
``test_verify_md_links.py`` (which exercises the same functions through the
verifier module); this file adds a direct smoke test of the shared module and
confirms the verifier really imports the single shared copy.
"""

from pathlib import Path

import pytest

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


@pytest.mark.parametrize(
    ("label", "text", "survivors"),
    [
        # A ```` block quoting a single ``` line. The inner run is shorter than
        # the opener and must not close the block: closing it would let the
        # outer ```` re-open one, inverting inside-ness for the rest of the
        # file so PROSE would be blanked and CODE would survive.
        ("four-backtick block, asymmetric inner run", "````\n```\n````\nPROSE\n", ["PROSE"]),
        # Same shape with a symmetric inner block: the toggle flips an even
        # number of times and the old scanner landed on the right answer by
        # accident. Kept so a regression here cannot hide behind the case above.
        ("four-backtick block, symmetric inner block", "````\n```\nCODE\n```\n````\nPROSE\n", ["PROSE"]),
        # Fence nested in a list item: opener and closer both indented.
        ("indented open and close", "1. shape:\n\n   ```\n   CODE\n   ```\n\nPROSE\n", ["1. shape:", "PROSE"]),
        # Opener flush-left, closer indented — the desync that emptied a whole
        # claim register when the scanner anchored the fence at column 0.
        ("flush open, indented close", "```\nCODE\n  ```\nPROSE\n", ["PROSE"]),
        # A tilde block is not closed by a backtick run, and vice versa.
        ("tilde block, backtick line inside", "~~~\n```\nCODE\n~~~\nPROSE\n", ["PROSE"]),
        # An info string disqualifies a line as a CLOSER: `` ```py `` inside a
        # block is quoted content, not the end of it.
        ("info string does not close", "```\n```py\nCODE\n```\nPROSE\n", ["PROSE"]),
        # Point 9's fence inside point 12's labelled blockquote. Unrecognised,
        # the LaTeX is scanned as prose and `[T](z)` reads as a broken link.
        ("blockquote-prefixed fence", "> ``` math\n> [T](z)\n> ```\n\nPROSE\n", ["PROSE"]),
        # The same fence inside an emphasised theorem statement: the writer puts
        # the emphasis' own delimiter on the closing line. Refusing it leaves the
        # fence open and blanks every line after it.
        ("emphasis glued to the close", "> ``` math\n> [T](z)\n> ```*\n\nPROSE\n", ["PROSE"]),
        # ...and the tolerance is emphasis alone: an info string still does not
        # close, quoted or not.
        ("info string does not close, quoted", "> ```\n> ```py\n> CODE\n> ```\n\nPROSE\n", ["PROSE"]),
        # A quoted fence nobody closed ends with the blockquote. Fenced content
        # takes no lazy continuation, so the unprefixed line is outside both —
        # and a file blanked to its end is one no gate reads.
        ("unclosed quoted fence stops at the quote", "> ``` math\n> [T](z)\n\nPROSE\n", ["PROSE"]),
    ],
)
def test_blank_fenced_lines_fence_grammar(label: str, text: str, survivors: list[str]) -> None:
    lines = kb_links.blank_fenced_lines(text)
    assert len(lines) == len(text.splitlines()), label
    assert [ln for ln in lines if ln.strip()] == survivors, label


@pytest.mark.parametrize(
    ("label", "text", "targets"),
    [
        # The measured failure. Pandoc hard-wraps a long inline maths span, and
        # inside a labelled blockquote the continuation carries the block's own
        # `> ` marker. Left standing, `\bigl[...\bigr](\xi)` reads as an inline
        # link to a file named `\xi` that every gate reports broken.
        (
            "hard-wrapped inline maths inside a blockquote",
            "> $`a(\\xi)\n> = \\Pi_+\\bigl[b\\bigr](\\xi)`$. Hence\n",
            [],
        ),
        # The same span on one line: already neutralised, a maths span being a
        # code span. Kept so a regression cannot hide behind the case above.
        ("inline maths on one line", "$`\\bigl[b\\bigr](\\xi)`$\n", []),
        # Blanking the maths must not blank the document: a broken link two
        # lines under the span is still the gate's to report, which is the half
        # a scanner that skipped every line carrying a backslash would lose.
        (
            "a link below the span survives it",
            "> $`a(\\xi)\n> = \\bigl[b\\bigr](\\xi)`$.\n>\n> see [x](ghost.md)\n",
            ["ghost.md"],
        ),
        # The `$` at both ends is what pays for the multi-line reach: an odd
        # backtick in prose opens nothing, so nothing blanks a tract of document
        # and takes the links inside it along.
        ("unpaired backtick opens nothing", "a stray ` in prose\n\n[x](ghost.md)\n", ["ghost.md"]),
        # A code span stays line-bounded, so one backtick per line is two opens
        # and neither reaches the link between them.
        ("code spans do not span lines", "one `a\n[x](ghost.md)\ntwo `b\n", ["ghost.md"]),
    ],
)
def test_strip_code_blanks_inline_maths(label: str, text: str, targets: list[str]) -> None:
    stripped = kb_links.strip_code(text)
    assert len(stripped.split("\n")) == len(text.splitlines()), label
    assert [m.group(1) for m in kb_links.LINK_RE.finditer(stripped)] == targets, label


def test_strip_code_is_the_fence_scanner_plus_inline_spans() -> None:
    """``strip_code`` must not carry a second, divergent fence scanner."""
    text = "````\n```\n````\n[link](ghost.md)\n"
    assert kb_links.strip_code(text).splitlines() == kb_links.blank_fenced_lines(text)


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


@pytest.mark.parametrize(
    "line, want",
    [
        ("[x](plain.md)", "plain.md"),
        # Angle-bracket destinations: the only form that can carry a space. A
        # broken target inside one must be matched, or it is invisible to
        # every gate — which reads as a pass.
        ("[x](<plain.md>)", "<plain.md>"),
        ("[x](<a file.md>)", "<a file.md>"),
        ("see [the leaf](../sec4/leaf.md) here", "../sec4/leaf.md"),
    ],
)
def test_link_re_captures_one_group_per_destination_form(line: str, want: str) -> None:
    m = kb_links.LINK_RE.search(line)
    assert m is not None, line
    # Exactly one capture group: callers index group(1), and a second group
    # would silently re-key every one of them.
    assert m.re.groups == 1
    assert m.group(1) == want


@pytest.mark.parametrize(
    "raw, want",
    [
        ("path/file.md#section", "path/file.md"),
        ("path/file.md:42", "path/file.md"),
        ("path/file.md#sec:99", "path/file.md"),
        # Angle brackets are delimiters, not path characters.
        ("<path/file.md>", "path/file.md"),
        ("<a file.md>", "a file.md"),
        # Percent-decoding is the correct reading of an encoded space...
        ("sub/a%20file.md", "sub/a file.md"),
        # ...and happens LAST, so an encoded #23 is not split off as an anchor.
        ("sub/a%23b.md", "sub/a#b.md"),
        # A `<...>` destination carries its own anchor inside the brackets.
        ("<sub/a%20file.md#anchor>", "sub/a file.md"),
    ],
)
def test_strip_target_normalizes_destinations(raw: str, want: str) -> None:
    assert kb_links.strip_target(raw) == want


@pytest.mark.parametrize(
    "text, want",
    [
        ("[ref]: sub/target.md", ["sub/target.md"]),
        ("[ref]: <a file.md>", ["<a file.md>"]),
        ('[ref]: sub/target.md "A title"', ["sub/target.md"]),
        ("   [ref]: sub/target.md", ["sub/target.md"]),
        # A footnote definition is prose, not a link.
        ("[^1]: Confirmed numerically: the escape time follows a power law.", []),
        # CommonMark allows only an optional title after the destination, so a
        # bracketed label in running prose is a paragraph — not a definition.
        ("[architect]: which minimal stage subset builds first.", []),
        # Four leading spaces is an indented code block, not a definition.
        ("    [ref]: sub/target.md", []),
    ],
)
def test_ref_def_re_matches_definitions_and_not_prose(text: str, want: list[str]) -> None:
    assert kb_links.REF_DEF_RE.findall(text) == want
