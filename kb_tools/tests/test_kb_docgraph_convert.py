"""The source readings stage 1 runs before pandoc sees anything.

Both exist for the same reason: pandoc consumes the preamble, so a declaration
is gone by the time any filter runs. :func:`theorem_display_names` is the
``\\newtheorem`` internal-handle → display-name mapping that lets a later stage
classify a block on what the author says it *is* rather than on whatever handle
the source's own markup uses; :func:`strip_environment_declarations` is the
``\\newenvironment`` removal that stops pandoc expanding an environment before
it can class a Div with the author's name for it.

Every declaration form below is authored to a shape established in a real
source: the arXiv sweep this reader was measured against.
"""

import pytest

from kb_tools.kb_docgraph.convert import strip_environment_declarations, theorem_display_names


@pytest.mark.parametrize(
    "declaration, expected",
    [
        (r"\newtheorem{claimbox}{Result}", {"claimbox": "Result"}),
        # A shared counter, in a leading optional argument.
        (r"\newtheorem{corollary}[theorem]{Corollary}", {"corollary": "Corollary"}),
        # A subordinate counter, in a trailing one.
        (r"\newtheorem{theorem}{Theorem}[section]", {"theorem": "Theorem"}),
        # Unnumbered.
        (r"\newtheorem*{remark}{Remark}", {"remark": "Remark"}),
        # A starred handle is a handle like any other.
        (r"\newtheorem{lem*}{Lemma}", {"lem*": "Lemma"}),
        # A handle in another language, and an alias.
        (r"\newtheorem{Teo}{Teorema}", {"Teo": "Teorema"}),
        (r"\newtheorem{Pro}{Proposition}", {"Pro": "Proposition"}),
        # Several words.
        (r"\newtheorem{testexample}{Test example}", {"testexample": "Test example"}),
    ],
)
def test_a_declaration_yields_its_two_names(declaration: str, expected: dict[str, str]) -> None:
    assert theorem_display_names(declaration) == expected


@pytest.mark.parametrize(
    "macro, expected",
    [
        (r"\protect\theoremname", "Theorem"),
        (r"\protect\lemmaname", "Lemma"),
        (r"\protect\propositionname", "Proposition"),
        (r"\protect\corollaryname", "Corollary"),
        (r"\protect\definitionname", "Definition"),
        (r"\protect\examplename", "Example"),
        (r"\protect\remarkname", "Remark"),
        (r"\protect\assumptionname", "Assumption"),
        (r"\protect\claimname", "Claim"),
        # The same macro without babel's `\protect` in front of it.
        (r"\theoremname", "Theorem"),
    ],
)
def test_a_translation_macro_resolves_to_the_word_it_stands_for(macro: str, expected: str) -> None:
    """babel's standard vocabulary — nine of these across the surveyed papers.

    Pandoc does not expand them: a block declared this way renders its display
    line as a bare number, so the declaration is the only place the word exists.
    """
    assert theorem_display_names(rf"\newtheorem{{thm}}{{{macro}}}") == {"thm": expected}


@pytest.mark.parametrize("declared", [r"\textbf{Theorem}", r"\somecommand", ""])
def test_a_display_name_this_cannot_resolve_is_left_out(declared: str) -> None:
    """No name is better than markup on a label line, and the handle is then what the block carries."""
    assert theorem_display_names(rf"\newtheorem{{thm}}{{{declared}}}") == {}


def test_a_source_declaring_nothing_maps_nothing() -> None:
    assert theorem_display_names(r"\documentclass{article}\begin{document}Text.\end{document}") == {}


def test_every_declaration_in_a_preamble_is_read() -> None:
    """The shape a surveyed paper's preamble actually has: amsthm, then three declarations."""
    preamble = "\n".join(
        [
            r"\usepackage{amsthm}",
            r"\newtheorem{theorem}{Theorem}[section]",
            r"\newtheorem{proposition}[theorem]{Proposition}",
            r"\newtheorem{claimbox}{Result}",
        ]
    )

    assert theorem_display_names(preamble) == {
        "theorem": "Theorem",
        "proposition": "Proposition",
        "claimbox": "Result",
    }


#: The form a LyX-exported preamble writes: an argument count, a default value
#: for the first argument, and a comment ending the line so the body groups may
#: sit on the ones beneath without contributing a space to the definition. The
#: comment is what a whitespace-only scan stops at.
_COMMENTED_ARGUMENT_LIST = "\n".join(
    [
        r"\newenvironment{elabeling}[2][]%",
        r"    {\begin{description}[leftmargin=#2,#1]}",
        r"    {\end{description}}",
    ]
)


@pytest.mark.parametrize(
    "declaration",
    [
        # The plain form.
        r"\newenvironment{aside}{\begin{quote}}{\end{quote}}",
        # Redefinition, and the starred variant.
        r"\renewenvironment{aside}{\begin{quote}}{\end{quote}}",
        r"\newenvironment*{aside}{\begin{quote}}{\end{quote}}",
        # An argument count, and a default for the first argument.
        r"\newenvironment{aside}[1]{\begin{quote}#1}{\end{quote}}",
        r"\newenvironment{aside}[2][none]{\begin{quote}#1}{\end{quote}}",
        # A comment ending the argument list, with the body groups beneath.
        _COMMENTED_ARGUMENT_LIST,
        # A comment inside a body group, commenting out a brace.
        "\n".join([r"\newenvironment{aside}", r"  {\begin{quote}% a stray { here", r"  }", r"  {\end{quote}}"]),
    ],
)
def test_a_declaration_is_removed_whole(declaration: str) -> None:
    """What is left is the document around it — the declaration and its bodies are gone."""
    source = f"\\usepackage{{amsthm}}\n{declaration}\n\\begin{{document}}Text.\\end{{document}}\n"

    stripped, unreadable = strip_environment_declarations(source)

    assert unreadable == ()
    assert stripped == "\\usepackage{amsthm}\n\n\\begin{document}Text.\\end{document}\n"


def test_every_declaration_in_a_preamble_is_removed() -> None:
    source = "\n".join(
        [
            r"\newenvironment{aside}{\begin{quote}}{\end{quote}}",
            _COMMENTED_ARGUMENT_LIST,
            r"\renewenvironment{abstract}{\begin{center}}{\end{center}}",
        ]
    )

    stripped, unreadable = strip_environment_declarations(source)

    assert unreadable == ()
    assert stripped.strip() == ""


@pytest.mark.parametrize(
    "declaration",
    [
        # No body group at all.
        r"\newenvironment{aside}",
        # One body group where two are wanted.
        r"\newenvironment{aside}{\begin{quote}}",
        # A body group that never closes.
        r"\newenvironment{aside}{\begin{quote}}{\end{quote}",
    ],
)
def test_a_declaration_this_cannot_read_is_left_where_it_was_written(declaration: str) -> None:
    """The unit that cannot be read costs itself: it is named, and the scan carries on.

    Leaving it costs that environment's blocks their author-given name — pandoc
    expands the environment instead of classing a Div with it — and costs the
    build nothing.
    """
    source = f"\\usepackage{{amsthm}}\n{declaration}\n"

    stripped, unreadable = strip_environment_declarations(source)

    assert stripped == source
    assert [(item.line, item.text) for item in unreadable] == [(2, declaration)]


def test_an_unreadable_declaration_does_not_cost_the_one_after_it() -> None:
    """The scan resumes past the keyword it could not read, not past the file."""
    source = "\n".join(
        [
            r"\newenvironment{unreadable}{\begin{quote}}",
            r"\newenvironment{aside}{\begin{center}}{\end{center}}",
            r"\begin{document}Text.\end{document}",
        ]
    )

    stripped, unreadable = strip_environment_declarations(source)

    assert [item.line for item in unreadable] == [1]
    assert stripped == "\\newenvironment{unreadable}{\\begin{quote}}\n\n\\begin{document}Text.\\end{document}"


def test_a_source_declaring_nothing_is_returned_unchanged() -> None:
    source = r"\documentclass{article}\begin{document}Text.\end{document}"

    assert strip_environment_declarations(source) == (source, ())
