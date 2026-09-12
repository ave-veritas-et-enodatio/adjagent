"""The token normalizer and the heading scan, against the shapes pandoc's writer emits.

Every case here is one the gfm writer was observed producing. The normalizer is
the instrument both partition checks read through, so a case it gets wrong is a
check that reports a drop that did not happen — or, worse, misses one that did.
"""

import pytest

from kb_tools.kb_docgraph import text


@pytest.mark.parametrize(
    ("rendered", "expected"),
    [
        ("plain words here", ["plain", "words", "here"]),
        ("*rate*-limited growth", ["rate-limited", "growth"]),
        ('<span class="nocase">T</span>he Anatomy', ["The", "Anatomy"]),
        ('Section <a href="#sec:x"\ndata-reference="sec:x">4</a>)', ["Section", "4"]),
        ("value $`\\sigma_2 < 1`$ holds", ["value", "holds"]),
        ("Mertens[^1] wrote", ["Mertens", "wrote"]),
        ("see <https://doi.org/10.1/x>", ["see", "https://doi.org/10.1/x"]),
        ("[https://doi.org/10.1/(a)b](https://doi.org/10.1/(a)b)", ["https://doi.org/10.1/(a)b"]),
        ("doi 10.1/18:1+\\<3.3 and more", ["doi", "10.1/18:1+<3.3", "and", "more"]),
        ("(``zombie''/calcified)", ["zombie/calcified"]),
        ("> **proof**\n>\n> Proof of Theorem 2", ["proof", "Proof", "of", "Theorem", "2"]),
        (
            "<figcaption>The\n"
            '<math display="inline" xmlns="http://www.w3.org/1998/Math/MathML"><semantics><mn>8</mn>'
            '<annotation encoding="application/x-tex">8</annotation></semantics></math>-qubit\n'
            "IQP extractor</figcaption>",
            ["The", "qubit", "IQP", "extractor"],
        ),
        (
            "<figcaption>WIP Upload &amp; Analysis, H&#39;&#39; and 5 &lt; 6</figcaption>",
            ["WIP", "Upload", "Analysis", "H", "and", "5", "6"],
        ),
        ("R&D and Q&A", ["R&D", "and", "Q&A"]),
        ("an `A7`-directed close", ["an", "A7", "directed", "close"]),
        ("the `BAR 3: 00:09.69` header", ["the", "BAR", "3", "00:09.69", "header"]),
    ],
    ids=[
        "plain",
        "emphasis-inside-word",
        "nocase-span",
        "wrapped-anchor",
        "math-span",
        "footnote-ref",
        "autolink",
        "inline-link",
        "escaped-angle",
        "quotation",
        "blockquote",
        "mathml-in-a-caption",
        "character-references",
        "a-bare-ampersand",
        "code-span-glued-to-the-next-word",
        "code-span-holding-spaces",
    ],
)
def test_markdown_tokens(rendered: str, expected: list[str]) -> None:
    assert text.markdown_tokens(rendered) == expected


def test_fenced_content_is_verbatim_and_survives_the_tag_pass() -> None:
    """A ``<`` in maths is not an open tag, and a fence is not prose.

    The failure this pins: ``\\det J > 0`` inside a fence, read as prose, is an
    unterminated tag that swallows every word after it until the next ``>``.
    """
    rendered = "``` math\n\\det J_+ > 0, \\qquad v<0\n```\nbecause it holds"

    assert text.markdown_tokens(rendered) == ["\\det", "J_+", ">", "0,", "\\qquad", "v<0", "because", "it", "holds"]


def test_a_blockquoted_fence_is_still_a_fence() -> None:
    """Point 12's labelled blockquotes carry display maths, and the marker prefixes it."""
    rendered = "> ``` math\n> a > b\n> ```\n> and then prose"

    assert text.markdown_tokens(rendered) == ["a", ">", "b", "and", "then", "prose"]


def test_a_list_item_s_fence_is_still_a_fence() -> None:
    """Point 9's display maths inside an ``enumerate`` opens at the item's content column.

    That is four spaces in for a single ``1.  `` marker and deeper for a nested
    item — past the three CommonMark allows a *top-level* fence, so a scan that
    stops at three reads the LaTeX as prose and ``p_i < 0 \\leq`` opens a tag
    that eats the words after it.
    """
    rendered = "1.  Non-negativity:\n    ``` math\n    p_i < 0 \\leq y_i\n    ```\n\n2.  Normalisation\n"

    words = ["1", "Non-negativity", "p_i", "<", "0", "\\leq", "y_i", "2", "Normalisation"]

    assert text.markdown_tokens(rendered) == words


def test_a_list_item_s_fence_gives_back_the_latex_without_the_item_s_indent() -> None:
    """The columns the list item put on are the item's, and point 9's verbatim LaTeX is not theirs."""
    rendered = (
        "1.  Non-negativity:\n"
        "    ``` math\n"
        "    \\begin{equation}\n"
        "            \\sum p_i < 0\n"
        "    \\end{equation}\n"
        "    ```\n"
    )

    assert text.fenced_blocks(rendered) == [("math", "\\begin{equation}\n        \\sum p_i < 0\n\\end{equation}")]


def test_a_tilde_run_inside_a_backtick_fence_is_content_and_not_a_close() -> None:
    """``~~~\\mbox{and }~~~`` is an author's spacing inside a display equation.

    A fence closes on its own marker and on nothing else. Closed there, the rest
    of the equation is read as prose and every heading after it is read as
    sitting inside a fence — which is how a volume loses seven of its eight.
    """
    rendered = "``` math\na = b\n    ~~~\\mbox{and }~~~\nc = d\n```\n\n# Real\n"

    assert text.fenced_blocks(rendered) == [("math", "a = b\n    ~~~\\mbox{and }~~~\nc = d")]
    assert text.headings(rendered) == [text.Heading(level=1, text="Real", start=6, end=7)]


def test_an_indented_code_block_is_verbatim_too() -> None:
    """The writer's second spelling of verbatim, and the one a ``verbatim`` environment gets.

    A fence hangs a ``CodeBlock``'s attributes off its info string, so a block
    carrying none is written indented instead. Handed to the undo pass, its
    ``_`` is deleted as an emphasis mark and a Lean identifier arrives spelled
    two ways across check A's two sides.
    """
    rendered = "Prose before.\n\n    interleavedDA_MSRR\n    lake build\n\nProse after.\n"

    tokens = text.markdown_tokens(rendered)

    assert tokens == ["Prose", "before", "interleavedDA_MSRR", "lake", "build", "Prose", "after"]


def test_a_list_item_s_second_paragraph_is_not_an_indented_code_block() -> None:
    """Four columns in is the item's content, not code: pandoc writes ``1.  ``.

    Read as code, the paragraph keeps the raw ``<a>`` of every cross-reference in
    it, the run the AST holds across that anchor has no contiguous match, and the
    check reports the paragraph lost.
    """
    rendered = (
        "1.  Non-negativity holds.\n"
        "\n"
        '    as Section <a href="#sec:x" data-reference="sec:x">4</a> shows.\n'
        "\n"
        "2.  Normalisation\n"
    )

    tokens = text.markdown_tokens(rendered)

    assert tokens == ["1", "Non-negativity", "holds", "as", "Section", "4", "shows", "2", "Normalisation"]


def test_an_indented_code_block_inside_a_list_item_is_still_verbatim() -> None:
    """The item's own four columns are its container's; the code block's are four more."""
    rendered = "1.  Run it:\n\n        audit_query_complexity.py\n\n2.  Read the log\n"

    assert text.markdown_tokens(rendered) == ["1", "Run", "it", "audit_query_complexity.py", "2", "Read", "the", "log"]


def test_headings_fold_the_writer_s_wrap_back_in() -> None:
    """The gfm writer wraps at 72 columns and does not exempt a heading from it."""
    rendered = '# Details (Section <a href="#sec:x"\ndata-reference="sec:x">4</a>)\n\nbody\n'

    found = text.headings(rendered)

    assert len(found) == 1
    assert found[0].level == 1
    assert found[0].text.endswith("4</a>)")
    assert found[0].end == 2


def test_a_hash_inside_a_fence_is_not_a_heading() -> None:
    assert text.headings("``` math\n# not a heading\n```\n\n# real\n") == [
        text.Heading(level=1, text="real", start=4, end=5)
    ]


@pytest.mark.parametrize(
    ("info", "content"),
    [("math", "a + b"), ("tikz", "\\begin{tikzpicture}")],
)
def test_fenced_blocks_carry_their_info_string(info: str, content: str) -> None:
    assert text.fenced_blocks(f"``` {info}\n{content}\n```\n") == [(info, content)]


def test_a_fence_closed_by_an_emphasis_delimiter_still_closes() -> None:
    """A display equation inside an emphasised theorem statement closes on ```` ```* ````."""
    assert text.fenced_blocks("> ``` math\n> N \\le 1\n> ```*\n") == [("math", "N \\le 1")]


@pytest.mark.parametrize(
    ("heading", "anchor"),
    [
        ("The Phase Transition Argument", "the-phase-transition-argument"),
        ("What Is Known About $`\\psi`$", "what-is-known-about"),
        ('Details (Section <a href="#s">4</a>)', "details-section4"),
    ],
    ids=["plain", "maths", "cross-reference"],
)
def test_github_anchor(heading: str, anchor: str) -> None:
    assert text.github_anchor(heading) == anchor
