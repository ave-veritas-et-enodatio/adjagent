"""Unit tests over the claim-graph builder's mechanical stages.

What is tested here is logic whose right answer is independently verifiable: the
display-line parse, the fence scan's two contract traps, the closed environment
table, the kind derivation, the values transport's round trip, and each
conformance point refusing the tree that violates it. What is *not* tested here
is the pipeline end to end — that is ``just claimgraph-corpus``, which runs the
real builder over the real tree in an installed consumer and exits on the
runner's own gates.
"""

import tomllib

import pytest

from kb_tools import kb_index_lib, kb_schema
from kb_tools.kb_claimgraph import assemble, conform, endcap, identify, inventory, tree, write
from kb_tools.kb_write import ops, render

# ---------------------------------------------------------------------------
# A minimal conforming tree, and the mutations that break one point each
# ---------------------------------------------------------------------------

_UPLINK = "[↑ Vol](index.md)"

_ENTRY_POINT = "# Knowledge Base\n\n- [Vol](vol/index.md)\n"
_VOLUME_INDEX = "[↑ Knowledge Base](../entry-point.md)\n\n# Vol\n\n- [Leaf](leaf.md)\n"
_LEAF = f"{_UPLINK}\n\n# Leaf\n\nSome prose.\n"


def _write(root, files):
    for path, text in files.items():
        target = root / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")
    return root


@pytest.fixture
def conforming(tmp_path):
    return _write(
        tmp_path / "kb-root",
        {"entry-point.md": _ENTRY_POINT, "vol/index.md": _VOLUME_INDEX, "vol/leaf.md": _LEAF},
    )


def test_a_conforming_tree_passes_the_gate(conforming):
    conform.gate(tree.read(conforming))


@pytest.mark.parametrize(
    "point, files",
    [
        (7, {"vol/leaf.md": f"{_UPLINK}\n\n# Leaf\n\n[gone](missing.md)\n"}),
        (
            7,
            {
                "vol/leaf.md": f"{_UPLINK}\n\n# Leaf\n\n"
                f'<a href="nowhere.md#x" data-reference-type="ref" data-reference="x">1</a>\n'
            },
        ),
        (1, {"entry-point.md": "# Knowledge Base\n\nNo volume is listed.\n"}),
        (3, {"vol/leaf.md": "# Leaf\n\nNo up-link on line 1.\n"}),
        (4, {"vol/index.md": "[↑ Knowledge Base](../entry-point.md)\n\n# Vol\n\nNo child is listed.\n"}),
        (
            2,
            {
                "vol/leaf.md": f"{_UPLINK}\n\n# Leaf\n\n- [Sub](sub.md)\n",
                "vol/sub.md": "[↑ Leaf](leaf.md)\n\n# Sub\n",
            },
        ),
        (14, {"vol/leaf.md": f"{_UPLINK}\n\n<!-- kb-frontmatter\nkind: leaf\n-->\n\n# Leaf\n"}),
    ],
)
def test_the_gate_refuses_each_violated_point(conforming, point, files):
    _write(conforming, files)
    with pytest.raises(conform.ConformanceError) as refusal:
        conform.gate(tree.read(conforming))
    assert refusal.value.check == f"point-{point}"


def test_the_cleanliness_check_is_the_double_run_guard(conforming):
    """Every artifact this stage writes trips point 14 on a second run."""
    for artifact in conform.POINT_14_ARTIFACTS:
        _write(conforming, {"vol/leaf.md": f"{_UPLINK}\n\n{artifact} something -->\n\n# Leaf\n"})
        with pytest.raises(conform.ConformanceError) as refusal:
            conform.gate(tree.read(conforming))
        assert refusal.value.check == "point-14"


# ---------------------------------------------------------------------------
# Stage B
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "display, title, locator",
    [
        (
            "**Theorem 2** (The governance bifurcation). *For …*",
            "The governance bifurcation",
            "**Theorem 2** (The governance bifurcation).",
        ),
        ("**Proposition 8** (What it establishes).", "What it establishes", "**Proposition 8** (What it establishes)."),
        (
            "**Theorem 12** (Positive Invariance of $`M`$). *Under …*",
            "Positive Invariance of $`M`$",
            "**Theorem 12** (Positive Invariance of $`M`$).",
        ),
        (
            "**Lemma 1** (A title (with a nested clause)). *…*",
            "A title (with a nested clause)",
            "**Lemma 1** (A title (with a nested clause)).",
        ),
        ("**Remark 2**. *The named footnotes are the discipline.*", None, None),
        ("*Proof.* For $`\\beta < 1`$ …", None, None),
    ],
)
def test_the_optional_argument_is_read_off_the_display_line(display, title, locator):
    parsed = inventory._optional_argument(display)
    assert parsed == (None if title is None else (title, locator))


@pytest.mark.parametrize(
    "display, title, locator",
    [
        (
            "**Theorem 2** (The governance bifurcation). *For …*",
            "The governance bifurcation",
            "**Theorem 2** (The governance bifurcation).",
        ),
        ("**Proposition 1**. *Assume that the function is defined.*", "Proposition 1", "**Proposition 1**."),
        ("**Lemma 3**", "Lemma 3", "**Lemma 3**"),
        ("**Theorem**. *An unnumbered environment states it.*", "Theorem", "**Theorem**."),
        ("**Test example 1**. *A worked case.*", "Test example 1", "**Test example 1**."),
        ("*Proof.* For $`\\beta < 1`$ …", None, None),
        ("", None, None),
    ],
    ids=[
        "titled",
        "numbered-untitled",
        "no-closing-stop",
        "unnumbered-untitled",
        "multi-word-name",
        "no-bold",
        "empty",
    ],
)
def test_an_untitled_display_line_yields_the_printed_name_it_carries(display, title, locator):
    """Point 12 preserves the optional argument; it does not require one.

    ``\\begin{lemma}`` with no title is the norm outside corpora written with
    tooling that prompts for one, and what the line then carries is the
    environment's printed word and its number. That is read, not inferred — and
    the one line this stage still cannot read is the one that does not open with
    that name at all.
    """
    assert inventory._read_display_line(display) == (None if title is None else (title, locator))


def _document(text):
    return tree.Document(path="vol/leaf.md", text=text)


def test_a_maths_fence_is_found_at_column_zero_and_inside_a_blockquote():
    text = "``` math\n\\label{eq:one}\n```\n\ntext\n\n> ``` math\n> \\label{eq:two}\n> ```\n"
    fences = inventory._fences(_document(text))
    assert [fence.labels for fence in fences] == [("eq:one",), ("eq:two",)]


def test_a_closing_fence_carrying_trailing_markup_still_closes():
    """The reader ends an emphasised run on the fence's own line; the fence still closes."""
    text = "> ``` math\n> x = 1\n> ```*\n\nAfter the block.\n"
    assert len(inventory._fences(_document(text))) == 1


def test_an_unclosed_fence_stops_the_stage():
    with pytest.raises(inventory.ClaimGraphError):
        inventory._fences(_document("``` math\nx = 1\n\nnothing closes it\n"))


def test_a_blockquote_with_no_label_line_is_not_a_claim_site():
    assert inventory._blocks(_document("> Just a quoted conclusion.\n>\n> And its second line.\n")) == []


@pytest.mark.parametrize(
    "name, expected",
    [
        ("Theorem", True),
        ("theorem", True),
        ("Result", True),
        ("Lemma", True),
        ("Claim", True),
        ("Proof", False),
        ("Remark", False),
        ("Assumption", False),
        ("Teorema", False),
    ],
)
def test_a_name_is_classified_case_folded(name, expected):
    """The label carries the author's own capitalisation and the table holds one spelling.

    ``Teorema`` is here to say what the table does *not* do: an Italian display
    name is outside it, is not claim-bearing, and reaches the census as a word
    somebody can read — which the handle ``Teo`` never was.
    """
    text = f"> **{name}**\n>\n> **{name} 1** (A title). *A statement.*\n"
    assert [block.claim_bearing for block in inventory._blocks(_document(text))] == [expected]


def test_a_display_name_of_several_words_is_one_name():
    """``\\newtheorem{testexample}{Test example}`` — measured, in the arXiv survey."""
    text = "> **Test example**\n>\n> **Test example 1**. *A worked case.*\n"
    blocks = inventory._blocks(_document(text))

    assert [(block.environment, block.claim_bearing) for block in blocks] == [("Test example", False)]


def test_the_derived_leaf_reference_footer_is_not_read_as_a_label_line():
    """A quoted bold *phrase* now matches where a bold word already did; this one still must not."""
    text = "> **Leaf references:** [operators](../common/operators.md).\n"
    assert inventory._blocks(_document(text)) == []


def test_an_environment_outside_the_closed_table_is_recorded_rather_than_refused(conforming):
    """The name is admitted as not-claim-bearing, counted, and reported.

    The closed table's argument is unchanged and is why this is safe: nothing
    classifies an unknown name as claim-bearing, so no claim enters the graph
    on a guess. What changed is that the omission is *named* instead of
    stopping the run — a halted build reports one bit, and a corpus this table
    was not calibrated on is exactly what the census exists to describe.
    """
    _write(conforming, {"vol/leaf.md": f"{_UPLINK}\n\n> **axiomatique**\n>\n> **Axiomatique 1** (A title).\n"})

    sites = inventory.scan(tree.read(conforming))
    census = inventory.census(sites)

    assert [(block.environment, block.claim_bearing) for block in sites.blocks] == [("axiomatique", False)]
    assert sites.claim_blocks() == ()
    assert census.unclassified == {"axiomatique": 1}
    assert census.environments["axiomatique"] == 1


def test_a_classified_name_is_not_reported_as_unclassified(conforming):
    """The set nobody has looked at, and not merely the set that is not claim-bearing.

    ``proof`` and ``remark`` are judgements somebody made; an unknown name is
    the absence of one. Collapsing the two would make the census's own line
    useless on the corpus it was calibrated for, which is the one that has to
    stay quiet.
    """
    _write(
        conforming,
        {"vol/leaf.md": f"{_UPLINK}\n\n> **remark**\n>\n> **Remark 1**. *Commentary.*\n"},
    )

    census = inventory.census(inventory.scan(tree.read(conforming)))

    assert census.environments == {"remark": 1}
    assert census.unclassified == {}


def test_an_untitled_claim_bearing_block_yields_a_claim():
    """The overwhelming norm in the wild, and it must not stop a build."""
    text = "> **theorem**\n>\n> **Theorem 1**. *A result with no optional argument.*\n"
    blocks = inventory._blocks(_document(text))

    assert [(block.title, block.display, block.claim_bearing) for block in blocks] == [
        ("Theorem 1", "**Theorem 1**.", True)
    ]


def test_a_titled_claim_bearing_block_still_reads_the_author_s_own_title():
    text = "> **theorem**\n>\n> **Theorem 2** (The governance bifurcation). *For every parameter.*\n"
    blocks = inventory._blocks(_document(text))

    assert [(block.title, block.display) for block in blocks] == [
        ("The governance bifurcation", "**Theorem 2** (The governance bifurcation).")
    ]


def test_two_unnumbered_blocks_sharing_a_display_line_are_told_apart_by_their_own_content():
    """``\\newtheorem*`` renders every one of its blocks under the same word.

    A locator matching two blocks binds a claim to the wrong site silently, so
    each span grows through the author's own words until they differ — and the
    title grows with it, a register entry being bound back to its site by title.
    Both grow: growth is decided against the other block's line rather than
    against the spans already handed out, so which block comes first in the
    document decides nothing.
    """
    text = (
        "> **theorem**\n>\n> **Theorem**. *Assume the map is continuous.*\n\n"
        "> **theorem**\n>\n> **Theorem**. *Let the map be open.*\n"
    )
    blocks = inventory._blocks(_document(text))

    assert [(block.title, block.display) for block in blocks] == [
        ("Theorem. Assume", "**Theorem**. *Assume"),
        ("Theorem. Let", "**Theorem**. *Let"),
    ]


def test_a_non_claim_bearing_environment_may_repeat_its_display_line_freely():
    """Two ``**Remark**.`` blocks are no reason to stop a build: nothing locates by them."""
    text = "> **remark**\n>\n> **Remark**. *One.*\n\n> **remark**\n>\n> **Remark**. *Two.*\n"
    blocks = inventory._blocks(_document(text))

    assert [(block.title, block.display) for block in blocks] == [("Remark", "**Remark**."), ("Remark", "**Remark**.")]


def test_two_blocks_alike_to_their_last_word_cost_themselves_and_not_the_build():
    """Neither span names one block rather than the other, so neither block is a claim site.

    Both are dropped rather than one being bound to a locator that matches the
    other: growth is decided against the other lines, so two blocks alike to
    their last word fail together and no claim is anchored at the wrong site.
    """
    text = "> **theorem**\n>\n> **Theorem**. *Alike.*\n\n> **theorem**\n>\n> **Theorem**. *Alike.*\n"
    blocks = inventory._blocks(_document(text))

    assert [(block.title, block.display, block.claim_bearing) for block in blocks] == [
        (None, None, False),
        (None, None, False),
    ]


def test_a_display_line_that_does_not_open_with_the_printed_name_costs_its_own_block(conforming):
    """One authored block is unreadable and the tree around it is not.

    The shape is the arXiv corpus's: an environment declared in a class file
    rather than in the volume root leaves the reader no name and no number to
    print, so the block's content begins with the author's own sentence and point
    12's span is absent from the line. There is no title to author and no span to
    anchor — so the block enters the graph as no claim, is counted, and the run
    goes on to every other document.
    """
    _write(
        conforming,
        {
            "vol/leaf.md": f"{_UPLINK}\n\n# Leaf\n\n"
            "> **theorem**\n"
            ">\n"
            "> Given a tetrahedron with four vertices and linear shape functions, the\n"
            "> gradient satisfies\n"
            "> ``` math\n"
            "> \\begin{equation}\n"
            "> \\nabla N_i = -\\sum_j K_e^{-1} \\xi_{ij}.\n"
            "> \\label{eq:split}\n"
            "> \\end{equation}\n"
            "> ```\n"
        },
    )

    sites = inventory.scan(tree.read(conforming))
    census = inventory.census(sites)

    assert [(block.environment, block.title, block.display, block.claim_bearing) for block in sites.blocks] == [
        ("theorem", None, None, False)
    ]
    assert sites.claim_blocks() == ()
    assert identify.block_claims(sites) == ()
    identify.check_block_coverage(identify.block_claims(sites), sites)
    assert census.unreadable == {"vol/leaf.md": 1}
    assert census.equation_labels == 1


def test_a_readable_block_reports_no_unreadable_display_line(conforming):
    """The count is the block's absence named, so it must read zero where nothing is absent."""
    _write(conforming, {"vol/leaf.md": f"{_UPLINK}\n\n> **theorem**\n>\n> **Theorem 1**. *A result.*\n"})

    census = inventory.census(inventory.scan(tree.read(conforming)))

    assert census.unreadable == {}
    assert census.claim_blocks == 1


def test_a_block_that_is_not_claim_bearing_is_admitted_whatever_its_display_line_says():
    """Nothing locates by one, so an unreadable display line costs it nothing."""
    blocks = inventory._blocks(_document("> **remark**\n>\n> *Commentary with no printed name.*\n"))
    assert [(block.environment, block.title, block.claim_bearing) for block in blocks] == [("remark", None, False)]


@pytest.mark.parametrize("cut", [0, 1])
def test_a_block_a_previous_pass_marked_reads_back_the_same_title(cut):
    """Pass 2 re-scans a tree pass 1 wrote markers into, wherever the wrap fell.

    A marker sits on the end of the line it marks, so it lands *inside* the
    display line whenever pandoc's hard wrap cut that line before the optional
    argument closed — ``cut=1`` is that case. Read as content it would be part
    of the title; stripped, both wraps yield the title the author wrote.
    """
    marker = render.render_tier2_marker("clm-aa1111")
    wrapped = [
        ["> **Theorem 2** (The governance bifurcation). *For every parameter it holds.*"],
        ["> **Theorem 2** (The governance", "> bifurcation). *For every parameter it holds.*"],
    ][cut]
    lines = ["> **theorem**", ">", f"{wrapped[0]} {marker}", *wrapped[1:]]
    blocks = inventory._blocks(_document("\n".join(lines) + "\n"))
    assert [(block.title, block.end) for block in blocks] == [("The governance bifurcation", len(lines))]


# ---------------------------------------------------------------------------
# Stage B — the three citation states
#
# Every rendering below is one pandoc writes, copied off a real build of
# `fixtures/lamb/`; the states are pinned against the live reader in
# `test_kb_docgraph_build.py`. What is here is what one paper cannot reach:
# a citation of several works at once, where the states differ within one span.
# ---------------------------------------------------------------------------

_REFERENCE_LIST = (
    '<div id="refs" class="references csl-bib-body hanging-indent">\n\n'
    '<div id="ref-nobody2026" class="csl-entry">\n\nNobody. 2026. *Nothing*.\n\n</div>\n\n</div>\n'
)


@pytest.mark.parametrize(
    "rendered, expected",
    [
        (
            '<span class="citation" data-cites="nobody2026">(Nobody 2026)</span>.\n',
            [("nobody2026", inventory.CitationState.RESOLVED)],
        ),
        (
            '<span class="citation" data-cites="nobody2026">(**nobody2026?**)</span>.\n',
            [("nobody2026", inventory.CitationState.UNANSWERED)],
        ),
        (
            '<span class="citation" data-cites="nobody2026">(nobody2026)</span>.\n',
            [("nobody2026", inventory.CitationState.KEY_ONLY)],
        ),
        (
            '<span class="citation" data-cites="nobody2026 other2020">(Nobody\n2026; **other2020?**)</span>.\n',
            [("nobody2026", inventory.CitationState.RESOLVED), ("other2020", inventory.CitationState.UNANSWERED)],
        ),
        (
            '<span class="citation"\ndata-cites="nobody2026 other2020">(**nobody2026?**; Other\n2020)</span>.\n',
            [("nobody2026", inventory.CitationState.UNANSWERED), ("other2020", inventory.CitationState.RESOLVED)],
        ),
        (
            '<span class="citation"\ndata-cites="nobody2026 other2020">(nobody2026; other2020)</span>.\n',
            [("nobody2026", inventory.CitationState.KEY_ONLY), ("other2020", inventory.CitationState.KEY_ONLY)],
        ),
        (
            '> Quoted prose citing <span class="citation"\n> data-cites="nobody2026">(nobody2026)</span>.\n',
            [("nobody2026", inventory.CitationState.KEY_ONLY)],
        ),
    ],
    ids=[
        "answered",
        "offered-a-bibliography-that-did-not-carry-it",
        "offered-no-bibliography",
        "one-span-one-key-answered",
        "one-span-the-other-key-answered",
        "one-span-neither-offered-anything",
        "inside-a-blockquote-and-wrapped",
    ],
)
def test_a_citation_reports_the_state_its_own_span_renders(rendered, expected):
    """The discriminator is the span, so a key's state is the key's and not the build's.

    A span whose text is its own keys was offered no bibliography; anything else
    had citeproc behind it, and there a key marked ``**key?**`` is one the
    bibliography did not carry while its neighbours in the same group resolved.
    """
    read = inventory._citations(_document(rendered))
    assert [(citation.key, citation.state) for citation in read] == expected


@pytest.mark.parametrize(
    "marked",
    [
        '> **Theorem 1** (<span class="citation" {marker}\n> data-cites="nobody2026">(nobody2026)</span>).\n',
        '> **Theorem 1** (<span class="citation" data-cites="nobody2026">(nobody2026)</span>). {marker}\n',
        '> Following <span class="citation" data-cites="nobody2026">(nobody2026)</span>, {marker}\n> it holds.\n',
    ],
    ids=["inside-the-opening-tag", "after-the-closing-tag", "after-the-span-on-a-wrapped-line"],
)
def test_a_citation_survives_a_marker_a_previous_pass_appended(marked):
    """A re-scan reads the citations the pass that wrote the markers read.

    The first case is the one that lost them and the only one a reader would not
    predict: a marker goes on the end of the line its excerpt begins on, a claim
    block's excerpt begins on its display line, and a theorem environment's
    optional argument puts the ``\\cite`` on that same line — so the marker lands
    between ``class="citation"`` and ``data-cites`` and the span stops matching.
    Nothing downstream could tell: a citation that leaves the inventory leaves no
    count behind to disagree with.
    """
    text = marked.format(marker=render.render_tier2_marker("clm-aa1111"))
    read = inventory._citations(_document(text))

    assert [(citation.key, citation.state, citation.line) for citation in read] == [
        ("nobody2026", inventory.CitationState.KEY_ONLY, 0)
    ]


def test_a_reference_list_entry_is_a_work_and_not_a_citation_of_one():
    """What made a one-work corpus report two: the list's entries were counted as citations."""
    document = _document(_REFERENCE_LIST)

    assert inventory._citations(document) == []
    assert [(work.key, work.text) for work in inventory._works(document)] == [
        ("nobody2026", "Nobody. 2026. *Nothing*.")
    ]


# ---------------------------------------------------------------------------
# The off-graph endcap
#
# THE FIXTURE, and why it is here rather than under `fixtures/`. The trigger is
# a citation INSIDE a claim block, and `fixtures/lamb/` cannot reach it: its
# header declares as PROPERTY 2 that it carries no claim-bearing environment
# anywhere, deliberately, because it is the only fixture covering a legitimate
# build with an empty claim graph. Altering that would cost the case it exists
# for. So the trigger is composed here instead, over the same synthetic trees
# every other stage test in this file is written against.
# ---------------------------------------------------------------------------


def _cited_block(rendering, keys="nobody2026"):
    """A claim-bearing block whose statement carries one citation span."""
    return (
        f"{_UPLINK}\n\n# Leaf\n\n"
        "> **Theorem**\n"
        ">\n"
        "> **Theorem 1** (A cited result). *It holds, following\n"
        f'> <span class="citation" data-cites="{keys}">{rendering}</span>.*\n'
    )


def _endcap_of(conforming, leaf):
    _write(conforming, {"vol/leaf.md": leaf})
    return endcap.scan(inventory.scan(tree.read(conforming)))


def test_a_citation_inside_a_claim_block_is_the_trigger_and_one_outside_it_is_not(conforming):
    """The join is a comparison of two positions stage B already recorded."""
    inside = _endcap_of(conforming, _cited_block("(Nobody 2026)"))
    outside = _endcap_of(
        conforming,
        f'{_UPLINK}\n\n# Leaf\n\nProse citing <span class="citation" data-cites="nobody2026">(Nobody 2026)</span>.\n'
        "\n> **Theorem**\n>\n> **Theorem 1** (An uncited result). *It holds.*\n",
    )

    assert [work.key for work in inside.works] == ["nobody2026"]
    assert list(inside.pairings.values()) == [("work-nobody2026",)]
    assert outside.works == ()
    assert outside.pairings == {}


def _proved(proof_body, *, statement="*It holds.*", head=""):
    """A claim block followed by the proof establishing it."""
    return (
        f"{_UPLINK}\n\n# Leaf\n\n"
        "> **Theorem**\n"
        ">\n"
        f"> **Theorem 1** (A result). {statement}\n"
        "\n"
        "> **proof**\n"
        ">\n"
        f"> *Proof{head}.* {proof_body}\n"
    )


_CITE = '<span class="citation" data-cites="nobody2026">(nobody2026)</span>'


def test_a_citation_inside_a_proof_rests_the_claim_that_proof_establishes_on_the_work(conforming):
    """A proof establishes the claim it belongs to, so what it leans on, the claim leans on.

    The same containment stage D directs a ``\\ref`` by, reaching outside the
    corpus instead of inside it. This is where the warrants are: over 25 built
    arXiv KBs, 8 citations sit inside claim blocks and 68 inside proofs.
    """
    found = _endcap_of(conforming, _proved(f"By the estimate of {_CITE}, the bound follows."))

    assert [work.key for work in found.works] == ["nobody2026"]
    assert found.pairings == {("vol/leaf.md", "**Theorem 1** (A result)."): ("work-nobody2026",)}


def test_a_claim_citing_one_work_in_its_statement_and_its_proof_rests_on_it_once(conforming):
    """A dependency is a relation, not a tally, whichever span each citation sat in."""
    found = _endcap_of(
        conforming,
        _proved(f"By {_CITE} again, the bound follows.", statement=f"*It holds, following {_CITE}.*"),
    )

    assert found.pairings == {("vol/leaf.md", "**Theorem 1** (A result)."): ("work-nobody2026",)}


def test_a_proof_that_binds_to_no_claim_contributes_no_edge(conforming):
    """Adjacency answers with the block immediately above, and a remark states no result.

    The binding's ordinary and documented outcome, asserted here because it is
    what keeps the endcap from attributing a proof's warrant to a claim nobody
    said it proves.
    """
    found = _endcap_of(
        conforming,
        f"{_UPLINK}\n\n# Leaf\n\n"
        "> **remark**\n>\n> *An aside.*\n\n"
        f"> **proof**\n>\n> *Proof.* By {_CITE}, it follows.\n",
    )

    assert found.works == ()
    assert found.pairings == {}


def test_a_proof_naming_its_subject_in_another_document_rests_that_claim_on_the_work(conforming):
    """``\\begin{proof}[Proof of Theorem \\ref{…}]`` — the arm adjacency cannot reach.

    The proof opens its own document, so nothing sits above it; what binds it is
    the author's own reference, and the claim it names is in another file. The
    pairing is therefore recorded under the *subject's* site while containment
    was asked of the *proof's* lines.
    """
    anchor = '<a href="claim.md#thm:main" data-reference-type="ref" data-reference="thm:main">1</a>'
    _write(
        conforming,
        {
            "vol/claim.md": f'{_UPLINK}\n\n# Claim\n\n> <span id="thm:main">**Theorem**</span>\n>\n'
            "> **Theorem 1** (A named result). *It holds.*\n",
            "vol/proof.md": f"{_UPLINK}\n\n# Proof\n\n> **proof**\n>\n"
            f"> *Proof of Theorem {anchor}.* By {_CITE}, it follows.\n",
        },
    )
    found = endcap.scan(inventory.scan(tree.read(conforming)))

    assert found.pairings == {("vol/claim.md", "**Theorem 1** (A named result)."): ("work-nobody2026",)}


def test_two_volumes_citing_one_key_reach_one_node(conforming):
    """Identity is the citation key, so a work three volumes cite is one node.

    Tied to the bibliography rather than to the per-volume reference lists: the
    two blocks below sit in different volumes and rest on the same node.
    """
    _write(
        conforming,
        {
            "entry-point.md": "# Knowledge Base\n\n- [Vol](vol/index.md)\n- [Two](two/index.md)\n",
            "vol/leaf.md": _cited_block("(Nobody 2026)"),
            "two/index.md": "[↑ Knowledge Base](../entry-point.md)\n\n# Two\n\n- [Leaf](leaf.md)\n",
            "two/leaf.md": _cited_block("(Nobody 2026)").replace("↑ Vol", "↑ Two"),
        },
    )

    found = endcap.scan(inventory.scan(tree.read(conforming)))

    assert [work.id for work in found.works] == ["work-nobody2026"]
    assert sorted(site[0] for site in found.pairings) == ["two/leaf.md", "vol/leaf.md"]
    assert set(found.pairings.values()) == {("work-nobody2026",)}


def test_a_resolved_citation_names_its_work_and_a_key_only_one_names_the_key(conforming):
    """What the node carries is the whole difference the three states make here."""
    resolved = _endcap_of(conforming, _cited_block("(Nobody 2026)") + "\n" + _REFERENCE_LIST)
    key_only = _endcap_of(conforming, _cited_block("(nobody2026)"))

    assert [(w.title, w.named) for w in resolved.works] == [("Nobody. 2026. *Nothing*.", True)]
    assert [(w.title, w.named) for w in key_only.works] == [("nobody2026", False)]
    assert "resolved against the corpus's own bibliography" in endcap.rationale(resolved.works[0])
    assert "no bibliography answered the key" in endcap.rationale(key_only.works[0])


def test_one_block_citing_one_work_twice_rests_on_it_once(conforming):
    """A dependency is a relation, not a tally."""
    found = _endcap_of(
        conforming,
        _cited_block("(Nobody 2026)")[:-1]
        + '> and again <span class="citation" data-cites="nobody2026">(Nobody 2026)</span>.\n',
    )

    assert list(found.pairings.values()) == [("work-nobody2026",)]


def test_the_plan_carries_the_endcap_and_authors_no_number(conforming):
    """Stage E's arrangement, and the two scores it leaves for a person."""
    _write(conforming, {"vol/leaf.md": _cited_block("(Nobody 2026)")})
    documents = tree.read(conforming)
    sites = inventory.scan(documents)
    plan = assemble.assemble(documents, sites, identify.block_claims(sites))

    assert [work.id for work in plan.works] == ["work-nobody2026"]
    assert plan.rests_on == ((0, "work-nobody2026"),)
    # The bullet a build writes for that edge, and the entry it points at:
    # both scores are the pending literal and nothing in the build fills them.
    bullet = render.render_depends_on_bullet(
        render.DependsOnTarget(target="work-nobody2026", title=plan.works[0].title)
    )
    assert bullet.endswith("(applicability *pending*)")
    entry = render.render_work_entry(node_id="work-nobody2026", title=plan.works[0].title, strength=None, rationale="r")
    assert "- strength: *pending*" in entry


def test_a_work_cited_only_outside_a_claim_block_gets_no_node(conforming):
    """A node for it would assert a dependency the corpus does not state."""
    _write(
        conforming,
        {
            "vol/leaf.md": _cited_block("(Nobody 2026)")
            + '\nProse citing <span class="citation" data-cites="other2020">(Other 2020)</span>.\n'
        },
    )
    documents = tree.read(conforming)
    sites = inventory.scan(documents)
    plan = assemble.assemble(documents, sites, identify.block_claims(sites))

    assert [work.id for work in plan.works] == ["work-nobody2026"]


def test_the_census_reports_every_state_whether_or_not_the_corpus_reached_it(conforming):
    """No state goes missing from the line: an unreached one reads zero."""
    _write(conforming, {"vol/leaf.md": f'{_UPLINK}\n\n# Leaf\n\n<span class="citation" data-cites="a">(a)</span>.\n'})

    census = inventory.census(inventory.scan(tree.read(conforming)))

    assert census.citations == {
        "inline-resolved": 0,
        "inline-unanswered": 0,
        "inline-key-only": 1,
        "reference-list": 0,
    }


# ---------------------------------------------------------------------------
# Stage E
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "path, has_children, kind",
    [
        ("entry-point.md", False, "entry-point"),
        ("entry-point.md", True, "entry-point"),
        ("vol/leaf.md", False, "leaf"),
        ("vol/index.md", True, "index"),
    ],
)
def test_the_kind_vocabulary_is_used_as_specified(path, has_children, kind):
    assert tree.document_kind(path, has_children=has_children) == kind


def test_a_register_lands_in_its_claim_s_own_domain():
    assert assemble.register_for("vol/sub/leaf.md") == "vol/claim-quality.md"


# ---------------------------------------------------------------------------
# Stage F's transport
# ---------------------------------------------------------------------------


def test_a_composed_values_file_parses_back_as_the_values_it_carries():
    text = write._compose(
        [
            {
                "register": "vol/claim-quality.md",
                "title": write.Prose("A $`\\beta`$-independent certificate"),
                "rigor": "*pending*",
                "claims": ("clm-aaaaaa", "clm-bbbbbb"),
            }
        ]
    )
    entry = tomllib.loads(text)["entry"][0]
    assert entry["title"].strip() == "A $`\\beta`$-independent certificate"
    assert entry["register"] == "vol/claim-quality.md"
    assert entry["rigor"] == "*pending*"
    assert entry["claims"] == ["clm-aaaaaa", "clm-bbbbbb"]


def test_prose_a_literal_block_cannot_hold_is_refused_by_name():
    with pytest.raises(write.WriteError) as refusal:
        write._compose([{"title": write.Prose("holds the ''' delimiter")}])
    assert refusal.value.check == "values-transport"


#: The title a live build died on. It quotes a word, so it closes on a single
#: quote — a value the transport carries rather than refuses, because the text
#: sits on its own line and never abuts the closing delimiter.
_QUOTE_CLOSING_TITLE = "Terminological flexibility in the application of the word 'probability'"


def test_prose_closing_on_a_quote_reaches_the_register_byte_exact(tmp_path):
    kb_root = _write(tmp_path / "kb-root", {"vol/index.md": _VOLUME_INDEX})
    values = tmp_path / "values.toml"
    values.write_text(
        write._compose(
            [
                {
                    "register": "vol/claim-quality.md",
                    "title": write.Prose(_QUOTE_CLOSING_TITLE),
                    "rigor": kb_schema.PENDING_LITERAL,
                    "rationale": write.Prose("The transport carries the title as supplied."),
                }
            ]
        ),
        encoding="utf-8",
    )
    result = ops.insert_claim_entry(kb_root=kb_root, values_file=values, create=True)
    assert result.ok, result.lines()

    register = kb_root / "vol/claim-quality.md"
    assert f"## {_QUOTE_CLOSING_TITLE}\n" in register.read_text(encoding="utf-8")
    assert [entry.title for entry in kb_index_lib.parse_claim_quality_file(register, kb_root)] == [_QUOTE_CLOSING_TITLE]


def test_a_path_needing_an_escape_grammar_is_not_emitted_as_a_quoted_string():
    with pytest.raises(write.WriteError):
        write._compose([{"document": 'vol/a"b.md'}])
