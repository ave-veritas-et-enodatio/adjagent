"""`label.labels_at` — the map from a resolved quotation back to a label.

The mapping is many-to-many by line: a hard-wrapped physical line carries
several sentences and a sentence runs across several lines, so a label named by
line names a set. These pin that it is named by offset instead, and that the
offset a resolution reports is the offset the render answers.
"""

from kb_tools.kb_claimgraph import label
from kb_tools.kb_write import ops

_DOCUMENT = (
    "\n".join(
        [
            "[↑ Up](../index.md)",
            "",
            "# Bistability",
            "",
            "The origin attracts from above. It repels from below, which is the whole",
            "of the bifurcation.",
            "",
            "A later paragraph states the origin attracts from above once more.",
        ]
    )
    + "\n"
)


def _labelled(excerpt: str) -> list[tuple[int, str]]:
    """Every place ``excerpt`` resolves to, as its physical line and its label."""
    rendered = label.render(_DOCUMENT)
    return [(hit.line, label.labels_at(rendered, hit.offset)) for hit in ops.resolve_excerpt(_DOCUMENT, excerpt).hits]


def test_two_sentences_sharing_one_wrapped_line_are_told_apart_by_offset():
    first = _labelled("The origin attracts from above")
    second = _labelled("It repels from below")

    assert [line for line, _ in first + second] == [4, 4]
    assert first[0][1] != second[0][1]


def test_a_sentence_running_across_a_wrap_answers_at_either_end():
    rendered = label.render(_DOCUMENT)
    (opening,) = ops.resolve_excerpt(_DOCUMENT, "It repels from below").hits
    (closing,) = ops.resolve_excerpt(_DOCUMENT, "of the bifurcation").hits

    assert opening.line != closing.line
    assert label.labels_at(rendered, opening.offset) == label.labels_at(rendered, closing.offset)


def test_a_quote_resolving_twice_names_a_different_label_each_time():
    # The cross-check the hit count leans on: two hits the seat's own label
    # settles without a call, which it can only do if they carry two labels.
    hits = _labelled("origin attracts from above")

    assert [line for line, _ in hits] == [4, 7]
    assert len({name for _, name in hits}) == 2


def test_every_label_a_render_carries_answers_at_its_own_sentence():
    rendered = label.render(_DOCUMENT)
    assert [label.labels_at(rendered, sentence.start) for sentence in rendered.sentences] == [
        sentence.label for sentence in rendered.sentences
    ]


def test_an_offset_no_sentence_covers_is_the_empty_label():
    # The up-link is navigation: the search reads it and the render never
    # labels it, so a hit there has no label rather than the nearest one.
    rendered = label.render(_DOCUMENT)
    (hit,) = ops.resolve_excerpt(_DOCUMENT, "../index.md").hits

    assert hit.line == 0
    assert label.labels_at(rendered, hit.offset) == ""
    assert label.labels_at(rendered, len(_DOCUMENT)) == ""
