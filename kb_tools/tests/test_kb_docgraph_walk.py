"""The AST walk: what it finds, and the two things it refuses to guess at.

The metadata cases carry the weight. ``author`` is a ``MetaList`` of
``MetaInlines``, and the walk that skipped it — enumerating ``MetaBlocks`` and
``MetaInlines`` by name — passed every other check silently. The nested case
below is what says the recursion is real rather than a pair of branches that
happen to cover this corpus.
"""

import pytest

from kb_tools.kb_docgraph import outline, walk


def _str(word: str) -> dict:
    return {"t": "Str", "c": word}


_SPACE = {"t": "Space"}


def _para(*inlines: dict) -> dict:
    return {"t": "Para", "c": list(inlines)}


def _inlines(sentence: str) -> list[dict]:
    words = sentence.split()
    return [node for word in words for node in (_str(word), _SPACE)][:-1]


def test_meta_is_walked_recursively_through_a_metalist() -> None:
    """The proof case: a content-bearing entry nested inside a ``MetaList``."""
    meta = {
        "author": {"t": "MetaList", "c": [{"t": "MetaInlines", "c": _inlines("Keith Mertens")}]},
        "title": {"t": "MetaInlines", "c": _inlines("A Volume")},
    }

    entries = walk.meta_entries(meta)

    assert [(entry.path, entry.runs) for entry in entries] == [
        ("author[0]", (("Keith", "Mertens"),)),
        ("title", (("A", "Volume"),)),
    ]


def test_meta_recursion_reaches_a_map_inside_a_list() -> None:
    """Depth is not two: nothing about the walk knows how deeply a corpus nests."""
    meta = {"k": {"t": "MetaList", "c": [{"t": "MetaMap", "c": {"inner": {"t": "MetaString", "c": "buried word"}}}]}}

    assert [entry.path for entry in walk.meta_entries(meta)] == ["k[0].inner"]


def test_a_run_ends_where_the_rendering_puts_something_else() -> None:
    """Maths interrupts a run, because it interrupts the words in the output."""
    block = _para(_str("before"), _SPACE, {"t": "Math", "c": [{"t": "InlineMath"}, "x"]}, _SPACE, _str("after"))

    assert walk.text_runs(block) == [["before"], ["after"]]


def test_a_run_does_not_span_a_list_item() -> None:
    """The writer puts a marker between items and the AST does not."""
    document = {
        "t": "OrderedList",
        "c": [[1, {}, {}], [[_para(*_inlines("first item"))], [_para(*_inlines("second item"))]]],
    }

    assert [walk.text_runs(block) for block in walk.leaf_blocks(document)] == [
        [["first", "item"]],
        [["second", "item"]],
    ]


def test_a_footnote_body_is_a_block_of_its_own() -> None:
    """It reaches the foot of the rendering, so a check that skipped it would miss a drop."""
    note = {"t": "Note", "c": [_para(*_inlines("the footnote text"))]}
    blocks = list(walk.leaf_blocks([_para(_str("body"), note)]))

    assert [walk.text_runs(block) for block in blocks] == [[["body"]], [["the", "footnote", "text"]]]


def test_an_equation_label_is_parsed_out_of_the_maths_source() -> None:
    """Point 7's equation case: the label never became a node, so the string is read."""
    document = {
        "blocks": [
            {"t": "Header", "c": [1, ["sec:one", [], []], _inlines("Opening")]},
            _para({"t": "Math", "c": [{"t": "DisplayMath"}, "\\begin{equation}x\\label{eq:fast}\\end{equation}"]}),
        ]
    }

    outline = walk.read_outline(document)

    assert outline.label_section == {"sec:one": 0, "eq:fast": 0}
    assert outline.header_labels == {"sec:one"}
    assert outline.anchored_labels == set()


def test_a_label_before_the_first_heading_belongs_to_the_volume_index() -> None:
    document = {"blocks": [{"t": "Div", "c": [["thm:x", ["theorem"], []], []]}]}

    assert walk.read_outline(document).label_section == {"thm:x": -1}


def test_a_table_identifier_carries_no_anchor() -> None:
    """gfm's pipe tables have no attribute syntax, so a link to one names the document."""
    document = {"blocks": [{"t": "Table", "c": [["tab:x", [], []], [None, []], [], [], [], []]}]}

    outline = walk.read_outline(document)

    assert outline.label_section == {"tab:x": -1}
    assert outline.anchored_labels == set()


def test_an_unhandled_raw_block_raises_rather_than_guessing() -> None:
    """§5 leaves what a drawing environment arrives as unmeasured; this is the refusal."""
    document = {"blocks": [{"t": "RawBlock", "c": ["latex", "\\begin{tikzpicture}\\draw;\\end{tikzpicture}"]}]}

    with pytest.raises(walk.UnhandledRawBlockError) as raised:
        walk.read_outline(document)

    assert "tikzpicture" in str(raised.value)


def test_maths_is_counted_from_content_meta_as_well_as_blocks() -> None:
    """An abstract is metadata and carries maths; point 9's claim is about the volume."""
    document = {
        "blocks": [_para({"t": "Math", "c": [{"t": "InlineMath"}, "a"]})],
        "meta": {"abstract": {"t": "MetaBlocks", "c": [_para({"t": "Math", "c": [{"t": "InlineMath"}, "b"]})]}},
    }

    assert sorted(walk.math_texts(document, content_keys=outline.CONTENT_KEYS)) == ["a", "b"]


def test_maths_in_apparatus_metadata_is_not_counted() -> None:
    """Point 13 elides the byline, so its ``^{1}`` markers have no document to reach."""
    document = {
        "blocks": [_para({"t": "Math", "c": [{"t": "InlineMath"}, "a"]})],
        "meta": {
            "author": {
                "t": "MetaList",
                "c": [{"t": "MetaInlines", "c": [{"t": "Math", "c": [{"t": "InlineMath"}, "^{1}"]}]}],
            }
        },
    }

    assert walk.math_texts(document, content_keys=outline.CONTENT_KEYS) == ["a"]


def test_a_heading_inside_a_blockquote_is_the_blocks_content_not_a_header() -> None:
    """Point 12: a ``\\paragraph`` inside a proof is a step of that proof, not a section.

    The rendering leaves such a heading quoted where it sits, so counting it here
    is what made the two sequences stage 2 zips disagree. The label still names
    the document the block landed in.
    """
    document = {
        "blocks": [
            {"t": "Header", "c": [1, ["sec:one", [], []], _inlines("Opening")]},
            {
                "t": "BlockQuote",
                "c": [
                    {"t": "Plain", "c": [{"t": "Span", "c": [["thm:x", [], []], _inlines("Proof")]}]},
                    {"t": "Header", "c": [4, ["step-1", [], []], _inlines("Step 1")]},
                ],
            },
        ]
    }

    outline_read = walk.read_outline(document)

    assert [header.tokens for header in outline_read.headers] == [("Opening",)]
    assert outline_read.header_labels == {"sec:one"}
    assert outline_read.label_section == {"sec:one": 0, "thm:x": 0, "step-1": 0}
    assert outline_read.anchored_labels == {"thm:x"}
