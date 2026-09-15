"""Stage 2's own rules: the classification, the zip, the ranking, the placement.

Almost none of these needs pandoc. What they need is the two sequences a
conversion produces, and the failures worth pinning are the ones that would
otherwise be silent — a metadata key nobody classified, a header sequence off by
one, a volume whose interior heading gap turns into empty tree levels. The one
exception converts for real, because what it pins is that the two sequences
*agree* on a construct the writer spells in a way no reader of the result can
parse — and a hand-written pair would only say that the pair was written to
match.
"""

import pytest

from kb_tools.kb_docgraph import outline
from kb_tools.kb_docgraph.convert import convert
from kb_tools.kb_docgraph.text import fenced_blocks
from kb_tools.kb_docgraph.walk import Header, Outline, read_outline

_RENDERED = "---\nabstract: |\n  The claim.\nauthor:\n- A. Author\ntitle: Volume One\n---\n\nlead-in\n\n# Alpha\n\nalpha body\n\n# Beta\n\nbeta body\n"

#: Citeproc's bibliography, spelled as pandoc's gfm writer emits it. The opening
#: line is the marker the lift is keyed on, so it is reproduced byte for byte
#: rather than paraphrased — a test written against an approximation of it would
#: pass while the build silently matched nothing.
_BIBLIOGRAPHY = (
    '<div id="refs" class="references csl-bib-body hanging-indent">\n'
    "\n"
    '<div id="ref-keynes1936" class="csl-entry">\n'
    "\n"
    "Keynes, John Maynard. 1936. *The General Theory*. Macmillan.\n"
    "\n"
    "</div>\n"
    "\n"
    "</div>"
)


def _outline(*headers: tuple[int, str, str]) -> Outline:
    """What ``read_outline`` returns for a document whose only labels are on headings."""
    return Outline(
        headers=[
            Header(level=level, identifier=identifier, tokens=tuple(title.split()))
            for level, identifier, title in headers
        ],
        label_section={identifier: position for position, (_, identifier, _) in enumerate(headers) if identifier},
        header_labels={identifier for _, identifier, _ in headers if identifier},
    )


def test_frontmatter_splits_by_point_13_s_rule() -> None:
    parsed = outline.parse_frontmatter(_RENDERED)

    assert parsed.title == "Volume One"
    assert parsed.abstract == "The claim."
    assert parsed.values["author"] == "A. Author"
    assert parsed.body.startswith("lead-in")


def test_a_multi_line_title_becomes_one_line() -> None:
    """A link's text and a heading are each one line; ``\\title{A\\\\B}`` renders as two."""
    parsed = outline.parse_frontmatter("---\ntitle: |\n  Part One\\\n  A Subtitle\n---\n\nbody\n")

    assert parsed.title == "Part One A Subtitle"


def test_an_unclassified_metadata_key_stops_the_build() -> None:
    """The split is a closed list: widening it is a plan change, not a code change."""
    with pytest.raises(outline.FrontmatterError) as raised:
        outline.parse_frontmatter("---\nkeywords: one, two\n---\n\nbody\n")

    assert "keywords" in str(raised.value)


def test_an_authors_address_is_apparatus_and_does_not_stop_the_build() -> None:
    """``amsart``'s ``\\address`` — the one byline macro pandoc lifts into ``meta``.

    It is an institutional affiliation, which bears on the logical construction
    of no argument, so point 13's criterion puts it exactly where the byline it
    travels with already sits. Six papers of a 33-paper sweep died on it, which
    is what a closed list costs and what it is for: the stop is the build
    refusing to drop a metadata channel it has not been told about.
    """
    parsed = outline.parse_frontmatter("---\naddress: Dept of Things, Some University\ntitle: A Probe\n---\n\nbody\n")

    assert parsed.values["address"] == "Dept of Things, Some University"
    assert "address" in outline.APPARATUS_KEYS and "address" not in outline.CONTENT_KEYS


def test_the_tree_is_built_from_the_zip(tmp_path) -> None:
    tree = outline.build_tree(
        stem="vol", markdown=_RENDERED, outline=_outline((1, "", "Alpha"), (1, "", "Beta")), volume_directory=tmp_path
    )

    assert tree.title == "Volume One"
    assert [node.path for node in tree.documents] == [
        "volume-one/index.md",
        "volume-one/overview.md",
        "volume-one/alpha.md",
        "volume-one/beta.md",
    ]
    # The volume's abstract and lead-in are source, so they land in a leaf and
    # not in the index that lists it. The index keeps its own heading.
    assert tree.index.segment == "# Volume One"
    overview = tree.index.children[0]
    assert overview.segment == "# Overview\n\nThe claim.\n\nlead-in"


def test_ranking_closes_an_interior_gap(tmp_path) -> None:
    """Levels ``{1, 4}`` are two tree levels, not four with two empty ones.

    Pandoc assigns levels from a fixed macro hierarchy, so a volume using
    ``\\section`` and ``\\paragraph`` yields 1 and 4 with nothing between. Under
    subtraction that is a tree with two empty interior levels; under ranking it
    is two deep, which is what the author wrote.
    """
    rendered = "---\ntitle: V\n---\n\n# Alpha\n\na\n\n#### Beta\n\nb\n"

    tree = outline.build_tree(
        stem="vol", markdown=rendered, outline=_outline((1, "", "Alpha"), (4, "", "Beta")), volume_directory=tmp_path
    )

    assert tree.distinct_levels == 2
    assert [node.path for node in tree.documents] == [
        "v/index.md",
        "v/alpha/index.md",
        "v/alpha/overview.md",
        "v/alpha/beta.md",
    ]


def test_a_heading_inside_an_authored_block_leaves_the_tree_undivided(tmp_path) -> None:
    """Point 12: the two sides read one rule, so the block reaches one document whole.

    The AST here is the real one — ``read_outline`` rather than the helper — so
    what is pinned is that the walk and :func:`text.headings` agree about a
    quoted heading rather than that a fixture was written to make them.
    """
    rendered = "---\ntitle: V\n---\n\n# Alpha\n\n> **Proof**\n>\n> #### Step 1. The bound.\n>\n> qed\n\n# Beta\n\nb\n"
    ast = {
        "blocks": [
            {"t": "Header", "c": [1, ["", [], []], [{"t": "Str", "c": "Alpha"}]]},
            {
                "t": "BlockQuote",
                "c": [
                    {"t": "Plain", "c": [{"t": "Strong", "c": [{"t": "Str", "c": "Proof"}]}]},
                    {"t": "Header", "c": [4, ["step-1", [], []], [{"t": "Str", "c": "Step"}]]},
                    {"t": "Para", "c": [{"t": "Str", "c": "qed"}]},
                ],
            },
            {"t": "Header", "c": [1, ["", [], []], [{"t": "Str", "c": "Beta"}]]},
        ]
    }

    tree = outline.build_tree(stem="vol", markdown=rendered, outline=read_outline(ast), volume_directory=tmp_path)

    assert [node.path for node in tree.documents] == ["v/index.md", "v/alpha.md", "v/beta.md"]
    alpha = next(node for node in tree.documents if node.path == "v/alpha.md")
    assert "> #### Step 1. The bound." in alpha.body
    # The label names the document the block landed in, with no fragment: a
    # heading's identifier reaches the rendering as nothing a reader can target.
    assert tree.label_paths["step-1"] == "v/alpha.md"


#: A theorem statement whose first content is a display equation. Reduced to the
#: three things that matter: the statement is italic (every ``\newtheorem``
#: environment is), the equation opens it rather than following a word, and a
#: heading comes after the block. Drop any one and the writer spells a fence its
#: own reader can open.
_EMPHASISED_DISPLAY_MATHS = "\n".join(
    [
        r"\documentclass{article}",
        r"\newtheorem{lemma}{Lemma}",
        r"\begin{document}",
        r"\section{Alpha}",
        r"\begin{lemma}",
        r"\begin{enumerate}",
        r"\item",
        r"\begin{equation}",
        "a = 1",
        r"\end{equation}",
        "and",
        r"\begin{equation}",
        "b = 2",
        r"\end{equation}",
        r"\end{enumerate}",
        r"\end{lemma}",
        r"\section{Beta}",
        "Beta body.",
        r"\end{document}",
        "",
    ]
)


def test_display_maths_opening_an_emphasised_statement_still_opens_a_fence(tmp_path) -> None:
    """The zip over a real conversion, on the construct that split the two sides.

    A theorem statement is italic, so the reader hands back the whole statement —
    display equations included — as one ``Emph``, and where an equation opens it
    the writer puts that ``Emph``'s delimiter against the fence's own backticks.
    Nothing opens a fence then: the maths is read as prose, the closing delimiter
    opens one instead, and every heading after it is read as sitting inside it.
    In the one volume of the arXiv survey that writes a lemma this way, four
    headings went that way and stage 2 reported 26 headers against 22 headings.

    Both halves are pinned, because the count agreeing is not the point on its
    own: point 9 says the maths reaches a document as a fenced block holding the
    LaTeX verbatim, and that is what makes the count agree here.
    """
    source = tmp_path / "v.tex"
    source.write_text(_EMPHASISED_DISPLAY_MATHS, encoding="utf-8")
    volume = convert(source, bibliographies=())

    tree = outline.build_tree(
        stem=volume.stem, markdown=volume.markdown, outline=read_outline(volume.ast), volume_directory=tmp_path
    )

    assert [node.path for node in tree.documents] == ["v/index.md", "v/alpha.md", "v/beta.md"]
    alpha = next(node for node in tree.documents if node.path == "v/alpha.md")
    assert ("math", "\\begin{equation}\na = 1\n\\end{equation}") in fenced_blocks(alpha.body)


@pytest.mark.parametrize(
    ("headers", "complaint"),
    [
        (((1, "", "Alpha"),), "1 headers and the rendering 2"),
        (((1, "", "Alpha"), (2, "", "Beta")), "level 2 in the AST and 1 in the rendering"),
        (((1, "", "Alpha"), (1, "", "Gamma")), "reads ['Gamma'] in the AST"),
    ],
    ids=["count", "level", "title"],
)
def test_a_misaligned_zip_fails_loudly(headers, complaint: str, tmp_path) -> None:
    """The alternative is every label landing one node off, and nothing saying so."""
    with pytest.raises(outline.AlignmentError) as raised:
        outline.build_tree(stem="vol", markdown=_RENDERED, outline=_outline(*headers), volume_directory=tmp_path)

    assert complaint in str(raised.value)


def test_a_volume_with_no_title_is_named_by_its_stem(tmp_path) -> None:
    tree = outline.build_tree(
        stem="AcmeWidgets",
        markdown="\n# Alpha\n\nbody\n",
        outline=_outline((1, "", "Alpha")),
        volume_directory=tmp_path,
    )

    assert tree.title == "AcmeWidgets"
    assert tree.index.path == "acmewidgets/index.md"


def test_a_cross_reference_is_rewritten_to_the_node_that_held_its_label(tmp_path) -> None:
    rendered = (
        '---\ntitle: V\n---\n\n# Alpha\n\nsee <a href="#sec:beta" data-reference="sec:beta">2</a>\n\n'
        "# Beta\n\nbody\n"
    )

    tree = outline.build_tree(
        stem="vol",
        markdown=rendered,
        outline=_outline((1, "", "Alpha"), (1, "sec:beta", "Beta")),
        volume_directory=tmp_path,
    )
    alpha = next(node for node in tree.documents if node.path.endswith("alpha.md"))

    assert 'href="beta.md#beta"' in alpha.body


def test_an_unmapped_label_is_left_as_the_raw_label_a_reader_can_see(tmp_path) -> None:
    rendered = '---\ntitle: V\n---\n\n# Alpha\n\nsee <a href="#eq:gone" data-reference="eq:gone">[eq:gone]</a>\n'

    tree = outline.build_tree(
        stem="vol", markdown=rendered, outline=_outline((1, "", "Alpha")), volume_directory=tmp_path
    )

    assert 'href="#eq:gone"' in tree.documents[1].body


def test_the_bibliography_is_lifted_out_of_the_content_stream() -> None:
    """The marker is citeproc's own, and the nested entry Divs do not end the block.

    A footnote definition follows the bibliography in the corpus's own renderings,
    so the lift is an excision from the middle rather than a truncation.
    """
    body = f"# Alpha\n\nalpha body\n\n{_BIBLIOGRAPHY}\n\n[^1]: a footnote\n"

    content, bibliography = outline.split_bibliography(body)

    assert content == "# Alpha\n\nalpha body\n\n\n[^1]: a footnote"
    assert bibliography == _BIBLIOGRAPHY


def test_a_volume_with_no_bibliography_is_untouched() -> None:
    body = "# Alpha\n\nalpha body\n"

    assert outline.split_bibliography(body) == (body, "")


def test_an_unclosed_bibliography_stops_the_build() -> None:
    """Half a reference list in one document and half in another is the alternative."""
    with pytest.raises(outline.BibliographyError):
        outline.split_bibliography('body\n\n<div id="refs" class="references csl-bib-body hanging-indent">\n\nx\n')


def test_the_reference_list_gets_its_own_leaf_last_under_the_volume_index(tmp_path) -> None:
    """Point 10's placement: addressable from the tree's shape, not searched for."""
    rendered = f"---\ntitle: V\n---\n\n# Alpha\n\nalpha body\n\n{_BIBLIOGRAPHY}\n"

    tree = outline.build_tree(
        stem="vol", markdown=rendered, outline=_outline((1, "", "Alpha")), volume_directory=tmp_path
    )

    assert [node.path for node in tree.documents] == ["v/index.md", "v/alpha.md", "v/references.md"]
    assert tree.index.children[-1].title == "References"
    assert "Keynes" in tree.documents[2].body and "Keynes" not in tree.documents[1].body
    # The words are relocated, not elided: the check-B reference still holds them,
    # and the title this stage supplied is accounted alongside the volume's own.
    assert "Keynes" in tree.content_tokens and "References" in tree.content_tokens


def test_a_bibliography_label_moves_to_the_document_its_content_moved_to(tmp_path) -> None:
    """Point 7: an anchor lands on the node that holds the label, wherever it went."""
    rendered = f"---\ntitle: V\n---\n\n# Alpha\n\nalpha body\n\n{_BIBLIOGRAPHY}\n"

    tree = outline.build_tree(
        stem="vol", markdown=rendered, outline=_outline((1, "", "Alpha")), volume_directory=tmp_path
    )

    assert tree.label_paths["ref-keynes1936"] == "v/references.md"


# ---------------------------------------------------------------------------
# A container's own prose
#
# The second relocation, on the bibliography's terms: a node that has children
# hands its segment to a leaf rather than holding source a consumer has to open
# an index to find. Both sites are one rule — the volume root's abstract and
# lead-in, and a section's prose ahead of its first subsection.
# ---------------------------------------------------------------------------


_A_SECTION_WITH_BOTH_PROSE_AND_SUBSECTIONS = "---\ntitle: V\n---\n\n# Alpha\n\nalpha lead\n\n## Gamma\n\ngamma body\n"


def test_a_section_holding_both_prose_and_subsections_keeps_its_heading_and_nothing_else(tmp_path) -> None:
    """The leaf is first, because a section's own prose runs ahead of its first subsection."""
    tree = outline.build_tree(
        stem="vol",
        markdown=_A_SECTION_WITH_BOTH_PROSE_AND_SUBSECTIONS,
        outline=_outline((1, "", "Alpha"), (2, "", "Gamma")),
        volume_directory=tmp_path,
    )
    alpha = next(node for node in tree.documents if node.path == "v/alpha/index.md")

    assert alpha.segment == "# Alpha"
    assert [child.title for child in alpha.children] == ["Overview", "Gamma"]
    assert [node.path for node in tree.documents] == [
        "v/index.md",
        "v/alpha/index.md",
        "v/alpha/overview.md",
        "v/alpha/gamma.md",
    ]
    # The prose is carried byte for byte under a supplied heading, and that
    # heading is accounted on check B's other side, once per leaf emitted. One
    # here: this volume writes nothing ahead of its first section, so the volume
    # index has no prose of its own to lift.
    assert alpha.children[0].segment == "# Overview\n\nalpha lead\n"
    assert tree.index.segment == "# V"
    assert tree.content_tokens.count("Overview") == 1


def test_every_container_renders_its_own_heading_whether_or_not_it_had_prose(tmp_path) -> None:
    """One shape of index, not two: nothing downstream writes a container's heading back."""
    both = outline.build_tree(
        stem="vol",
        markdown=_A_SECTION_WITH_BOTH_PROSE_AND_SUBSECTIONS,
        outline=_outline((1, "", "Alpha"), (2, "", "Gamma")),
        volume_directory=tmp_path,
    )
    subsections_only = outline.build_tree(
        stem="vol",
        markdown="---\ntitle: V\n---\n\n# Alpha\n\n## Gamma\n\ngamma body\n",
        outline=_outline((1, "", "Alpha"), (2, "", "Gamma")),
        volume_directory=tmp_path,
    )

    for tree in (both, subsections_only):
        alpha = next(node for node in tree.documents if node.path == "v/alpha/index.md")
        assert outline.render(alpha, tree_root="entry-point.md").splitlines()[2] == "# Alpha"


def test_a_section_whose_prose_all_sits_in_its_subsections_gets_no_leaf(tmp_path) -> None:
    """Nothing below the heading is nothing to lift — the empty bibliography's own answer."""
    rendered = "---\ntitle: V\n---\n\n# Alpha\n\n## Gamma\n\ngamma body\n"

    tree = outline.build_tree(
        stem="vol", markdown=rendered, outline=_outline((1, "", "Alpha"), (2, "", "Gamma")), volume_directory=tmp_path
    )

    assert [node.path for node in tree.documents] == ["v/index.md", "v/alpha/index.md", "v/alpha/gamma.md"]
    assert "Overview" not in tree.content_tokens


def test_the_lift_splits_a_section_s_labels_between_the_heading_and_the_prose(tmp_path) -> None:
    """Point 7 on both halves: an anchor lands on the node that holds the label.

    The section's own identifier is on its heading, which stays with the
    container; a ``\\label`` its lead-in declares is in the prose, which does
    not. A label past the first subsection heading belongs to that subsection and
    is reached by neither.
    """
    rendered = (
        '---\ntitle: V\n---\n\n# Alpha\n\nsee <a href="#sec:beta" data-reference="sec:beta">2</a> and '
        '<a href="#thm:bound" data-reference="thm:bound">1</a>\n\n'
        '# Beta\n\n<span id="thm:bound">beta lead</span>\n\n## Gamma\n\ngamma body\n'
    )
    walked = Outline(
        headers=[
            Header(level=1, identifier="", tokens=("Alpha",)),
            Header(level=1, identifier="sec:beta", tokens=("Beta",)),
            Header(level=2, identifier="", tokens=("Gamma",)),
        ],
        label_section={"sec:beta": 1, "thm:bound": 1},
        header_labels={"sec:beta"},
        anchored_labels={"thm:bound"},
    )

    tree = outline.build_tree(stem="vol", markdown=rendered, outline=walked, volume_directory=tmp_path)
    alpha = next(node for node in tree.documents if node.path == "v/alpha.md")

    assert tree.label_paths["sec:beta"] == "v/beta/index.md"
    assert tree.label_paths["thm:bound"] == "v/beta/overview.md"
    assert 'href="beta/index.md#beta"' in alpha.body
    assert 'href="beta/overview.md#thm:bound"' in alpha.body


def test_navigation_is_composed_at_render_and_is_not_part_of_the_body(tmp_path) -> None:
    """Which is what makes a heading in a parent's child list not a duplication."""
    tree = outline.build_tree(
        stem="vol", markdown=_RENDERED, outline=_outline((1, "", "Alpha"), (1, "", "Beta")), volume_directory=tmp_path
    )
    written = outline.render(tree.index, tree_root="entry-point.md")

    assert written.splitlines()[0] == "[↑ Knowledge Base](../entry-point.md)"
    assert "- [Alpha](alpha.md)" in written
    assert "Alpha" not in tree.index.body


# ---------------------------------------------------------------------------
# YAML scalar quoting
#
# An emitter quotes whenever the plain form would not parse back, so this is a
# class of values and not a title bug. Each case below is a reason an emitter
# reaches for quotes.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("emitted", "expected", "why_quoted"),
    [
        ('"Mary and Her Lamb: An Account"', "Mary and Her Lamb: An Account", "a colon-space would start a mapping"),
        ('"#Hash Lead"', "#Hash Lead", "a leading # would start a comment"),
        ('"  padded  "', "  padded  ", "leading and trailing space would be stripped"),
        ('"2026-09-09"', "2026-09-09", "the plain form would parse as a date"),
        ('"true"', "true", "the plain form would parse as a boolean"),
        ('"He said \\"hi\\""', 'He said "hi"', "an embedded double quote is escaped"),
        ('"Back\\\\slash"', "Back\\slash", "a literal backslash is doubled"),
        ('"Tab\\there"', "Tab\there", "a control character is escaped"),
        ('"Caf\\u00e9"', "Café", "a non-ASCII character may be escaped by codepoint"),
        ("'It''s Here'", "It's Here", "single-quoted doubles an embedded quote"),
        ("Plain Title", "Plain Title", "nothing to quote, so nothing is quoted"),
        ('He said "hi" mid-line', 'He said "hi" mid-line', "a quote inside a plain scalar is not a wrapper"),
        ('"', '"', "one character cannot be a quoted scalar"),
        ('"unterminated', '"unterminated', "malformed input is left alone rather than truncated"),
    ],
)
def test_a_quoted_scalar_is_read_as_its_own_text(emitted: str, expected: str, why_quoted: str) -> None:
    """Asked of the stored value, not of ``Frontmatter.title``.

    That property additionally collapses whitespace — a two-line ``\\title``
    has to become one line for a heading and a link — so reading the class
    through it would hide what unquoting did behind what normalising did.
    """
    parsed = outline.parse_frontmatter(f"---\ntitle: {emitted}\n---\n\nbody\n")

    assert parsed.values["title"] == expected, why_quoted


def test_an_unquoted_title_reaches_the_slug_and_the_link_text_it_feeds() -> None:
    """The two consumers a surviving quote would have silently corrupted.

    The citation gate caught the link text; nothing would ever have caught the
    directory name, which is why the fix belongs at the parse and not at either.
    """
    frontmatter = outline.parse_frontmatter('---\ntitle: "Mary and Her Lamb: An Account"\n---\n\nbody\n')

    assert frontmatter.title == "Mary and Her Lamb: An Account"
    # The slug drops the colon on its own, through `markdown_tokens`; what it
    # must not carry is a quote, which would survive slugging as a dropped
    # character in the middle of a directory name nobody would question.
    assert outline._volume_directory_name(frontmatter.title, "lamb") == "mary-and-her-lamb-an-account"
    assert outline._plain(frontmatter.title) == "Mary and Her Lamb An Account"
    assert outline._supplied_titles(frontmatter.title, "", own_prose=0) == ["Mary and Her Lamb: An Account"]
