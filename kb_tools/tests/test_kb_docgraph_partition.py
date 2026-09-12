"""Check A over a converted volume: what it must not report, and what it must.

Each volume here is **authored**, not cut out of any staged corpus, and each
carries one construct: a ``verbatim`` environment, which the gfm writer spells as
an indented code block rather than as a fence; and a ``\\texttt``-backed macro
written against the word after it, which reaches the AST as an inline ``Code``
and the rendering as a backticked span glued to its neighbour.

Both halves of each pair matter and only together — a check that stopped
reporting the false drop by no longer looking at this content would pass the
first test of a pair and fail the second.
"""

from pathlib import Path

import pytest

from kb_tools.kb_docgraph import convert, partition

#: One paragraph, one ``verbatim`` block of Lean identifiers, one paragraph. The
#: identifiers carry ``_`` because that is the character the rendering's undo
#: pass deletes as an emphasis mark.
_SOURCE = r"""\documentclass{article}
\begin{document}
\section{Lean project and build}

Lean checks terminal exhaustion and certificate generation:
\begin{verbatim}
interleavedDA_MSRR_terminalExhaustion
interleavedDA_MSRR_generatesCertificate
\end{verbatim}
The project files contain no placeholders on the exported proof path.
\end{document}
"""

_DELETED = "interleavedDA_MSRR_generatesCertificate"

#: One paragraph whose macro expands to ``\texttt`` and is written against the
#: hyphenated word after it. ``walk`` breaks the AST's text run at the ``Code``
#: and puts its content in no run, so the run the check carries begins
#: ``directed`` — and the rendering's own first token there is
#: ``` `A7`-directed ```, which is that run's first word glued to a span the AST
#: never spelled into it.
_GLUED_SOURCE = r"""\documentclass{article}
\newcommand{\chordname}[1]{\texttt{#1}}
\begin{document}
\section{Reading the annotations}

The \chordname{A7}-directed approach is annotated once per bar, and the
\chordname{BAR 3: 00:09.69} header names the bar it opens.
\end{document}
"""


def _converted(tmp_path_factory: pytest.TempPathFactory, name: str, source: str) -> convert.Volume:
    root = Path(tmp_path_factory.mktemp(name)) / "main.tex"
    root.write_text(source, encoding="utf-8")
    return convert.convert(root, bibliographies=[])


@pytest.fixture(scope="module")
def volume(tmp_path_factory: pytest.TempPathFactory) -> convert.Volume:
    return _converted(tmp_path_factory, "verbatim", _SOURCE)


@pytest.fixture(scope="module")
def glued(tmp_path_factory: pytest.TempPathFactory) -> convert.Volume:
    return _converted(tmp_path_factory, "code-span", _GLUED_SOURCE)


def _check(volume: convert.Volume, markdown: str) -> list[partition.Finding]:
    return partition.check_ast_against_markdown(stem=volume.stem, ast=volume.ast, markdown=markdown)


def test_an_indented_code_block_reaches_both_sides_of_check_a(volume: convert.Volume) -> None:
    """The writer indents an attribute-less code block, and both sides read that verbatim."""
    assert f"    {_DELETED}" in volume.markdown

    assert [finding.status for finding in _check(volume, volume.markdown)] == [partition.PASS]


def test_a_line_deleted_from_a_code_block_still_fails_check_a(volume: convert.Volume) -> None:
    """The check still bites on a real loss — the verbatim reading widened nothing."""
    damaged = "\n".join(line for line in volume.markdown.splitlines() if _DELETED not in line)

    findings = _check(volume, damaged)

    assert [finding.status for finding in findings] == [partition.FAIL]
    assert findings[0].check == partition.CHECK_AST
    assert _DELETED in findings[0].detail


def test_a_code_span_against_the_next_word_reaches_both_sides_of_check_a(glued: convert.Volume) -> None:
    """The span is a boundary on both sides, so the run after it has a contiguous match."""
    assert "`A7`-directed" in glued.markdown

    assert [finding.status for finding in _check(glued, glued.markdown)] == [partition.PASS]


def test_a_word_deleted_beside_a_code_span_still_fails_check_a(glued: convert.Volume) -> None:
    """The check still bites at the boundary itself — the padding widened nothing.

    The word taken out is the one the span was written against, which is the
    position the false report came from: a fix that made the check stop looking
    there would pass the test above and fail this one.
    """
    findings = _check(glued, glued.markdown.replace("`-directed", "`"))

    assert [finding.status for finding in findings] == [partition.FAIL]
    assert findings[0].check == partition.CHECK_AST
    assert findings[0].detail.endswith("lost: 'directed approach is annotated once per bar and the' (9 words)")
