"""Claim discovery: the labelled render, the checks over one answer, and the run end to end.

**Every check here runs against a fixed inference.** The seam is
:class:`kb_tools.kb_claimgraph.ask.SeatAsk` — a seat and a prompt in, response
text and an ``Outcome`` out — and the fake reads the prompt the stage actually
composed and answers from a table keyed by document path. So the ask, the reply
parse, both transport failure paths, every mechanical exclusion, each of the two
re-asks, the per-document write and the run's own exit condition are all
exercised, and none of it needs a model to be reachable.

**Nothing here asserts agreement between two live runs**, and nothing could: the
answers are fixed, so what these checks establish is that the mechanism holds
answers fixed — never that a model would give the same ones twice.

**The fixture is shaped by measurement, not by convenience.** Over the survey
corpus, 74 display-maths fences carry 6 blank lines before them and 59 open
mid-sentence, so the leaf below writes its fence flush against the prose it
interrupts — which is what makes a span crossing a fence findable, and what a
fixture with a blank line on each side would have hidden. One second fence *is*
blank-separated, because that minority is where the paragraph rule bites.

The consuming repository is stood up the way a consumer's is: the real runner
snippet, imported by the line the installer writes, over the installed package.
That is what makes the last stage's targets the real ones.
"""

import os
import re
import shutil
import tomllib
from collections import Counter
from pathlib import Path

import pytest

from kb_tools import kb_index_lib, kb_schema, kb_util, refresh_kb_metadata, verify_kb_metadata
from kb_tools.inference import Outcome
from kb_tools.kb_claimgraph import ask, conform, discover, graph, identify, inventory, label, tree, write
from kb_tools.kb_claimgraph.assemble import UNSCANNED_REASON
from kb_tools.kb_claimgraph.build import build
from kb_tools.kb_claimgraph.report import AnswerFormatError
from kb_tools.kb_driver import envelope
from kb_tools.kb_write import ops, render

_PACKAGE_ROOT = Path(kb_util.__file__).resolve().parent

pytestmark = pytest.mark.skipif(
    shutil.which("just") is None,
    reason="claim discovery's last stage runs the consuming project's runner targets",
)


# ---------------------------------------------------------------------------
# A four-leaf corpus: one author-marked block, one prose document stating two
# results across a hard wrap and carrying two maths fences, and one stating none.
# ---------------------------------------------------------------------------

_ENTRY_POINT = "# Knowledge Base\n\n- [Vol](vol/index.md)\n"
_VOLUME_INDEX = (
    "[↑ Knowledge Base](../entry-point.md)\n\n# Vol\n\n"
    "- [Alpha](alpha.md)\n- [Epsilon](epsilon.md)\n- [Zeta](zeta.md)\n"
)
_UPLINK = "[↑ Vol](index.md)"

_ALPHA = f"""{_UPLINK}

# Alpha

> <span id="thm:alpha">**theorem**</span>
>
> **Theorem 1** (Alpha result). *Alpha holds for every admissible state.*

Alpha is argued directly.
"""

# The first fence is flush against the sentence it interrupts, which is how this
# corpus writes 59 of its 74; the second is blank-separated, which is how it
# writes the other 6. Both spans below cross a hard wrap, which is the case the
# write API's locator matching exists for.
_EPSILON = f"""{_UPLINK}

# Epsilon

The author marked nothing here. The admissible configuration set is closed
under the operations of the previous section, and every admissible
configuration therefore admits a stationary point. See Fig. 3 for the picture.

The rate parameter satisfies
``` math
\\lambda = \\tfrac{{1}}{{2}} \\mu^2 \\label{{eq:rate}}
```
from which the second result follows: the stationary point is unique whenever
the rate parameter is strictly positive.

An unrelated identity is recorded separately.

``` math
\\mu = \\sigma^2 \\label{{eq:mu}}
```

The section ends there.
"""

_ZETA = f"""{_UPLINK}

# Zeta

This section fixes notation. The symbol used throughout for the rate parameter
is written as it is in the source, and nothing is asserted about it here.
"""

_TREE = {
    "entry-point.md": _ENTRY_POINT,
    "vol/index.md": _VOLUME_INDEX,
    "vol/alpha.md": _ALPHA,
    "vol/epsilon.md": _EPSILON,
    "vol/zeta.md": _ZETA,
}

#: The labels the render gives Epsilon. Read off the render in
#: ``test_the_render_labels_one_sentence_per_line``, and named here so every
#: later check states a label rather than an ordinal nobody can follow. They are
#: the render's coordinates, not the answer's: an answer names a sentence by
#: quoting its opening, and carries the label only as the cross-check.
_CLOSURE_LABEL = "S3"
_UNIQUENESS_LABEL = "S5"

#: What §1.2 makes of each of those starts over the default answer: from the
#: start to the next start in the same paragraph, or to the paragraph's end.
#: ``S3`` runs to ``S4`` because nothing else starts in that paragraph; ``S5``
#: runs to ``S9``, across the fence flush against the prose it interrupts.
_CLOSURE_EXTENT = "S3-S4"
_UNIQUENESS_EXTENT = "S5-S9"

_SEPARATED_FENCE_LOCATOR = "S11-S13"
_HEADING_LOCATOR = "S1"

#: The openings a seat quotes, each lifted from the corpus above. A quote is the
#: opening of the sentence a result begins at and never the whole of it: §2.3's
#: measurement is that quoting the opening more than halves exposure to markup
#: and typography the seat would have to reproduce.
_CLOSURE_QUOTE = "The admissible configuration set is closed"
_UNIQUENESS_QUOTE = "The rate parameter satisfies"
_OPENER_QUOTE = "The author marked nothing here"

_CLOSURE_TITLE = "Closure of the admissible set under the section's operations"
_UNIQUENESS_TITLE = "Uniqueness of the stationary point at a positive rate"

_ZETA_REASON = "The section fixes notation and states no result of its own."

#: One claim block as a seat returns it: what it quoted, the label it says that
#: is, and the title it wrote.
_Block = tuple[str, str, str]

#: What a run over this corpus is told, in the shape the fake answers from:
#: ``path -> (blocks, reason)``.
_ANSWERS: dict[str, tuple[tuple[_Block, ...], str]] = {
    "vol/epsilon.md": (
        (
            (_CLOSURE_QUOTE, _CLOSURE_LABEL, _CLOSURE_TITLE),
            (_UNIQUENESS_QUOTE, _UNIQUENESS_LABEL, _UNIQUENESS_TITLE),
        ),
        "",
    ),
    "vol/zeta.md": ((), _ZETA_REASON),
}


def _records(blocks) -> list[identify.Record]:
    return [identify.Record(quote=quote, label=label, title=title) for quote, label, title in blocks]


# ---------------------------------------------------------------------------
# The fake inference
# ---------------------------------------------------------------------------

#: The line :func:`ask.compose_identify_prompt` names its subject on. Read
#: rather than assumed: a fake that guessed the prompt's layout would keep
#: answering after the ask changed under it. The per-claim re-ask template names
#: its document on the same line, which is what lets one fake answer both.
_DOCUMENT_RE = re.compile(r"^## The document — `(.+)`$", re.MULTILINE)

_RETRY_HEADING = "## A previous answer failed a mechanical check"

#: The line the per-claim re-ask carries the quote it is about on. It is how a
#: fake tells a re-ask from an ask, and which claim the re-ask is about.
_REASK_RE = re.compile(r"^## The quote — `(.+)`$", re.MULTILINE)

#: An answer this parse cannot read at all: a claim block left unclosed, which
#: swallows every block behind it. Returned beside a well-formed answer, so what
#: fails is the read and nothing else.
_UNCLOSED_CLAIM_BLOCK = f"{ask.CLAIM_OPEN}\nquote: a quotation\n"

#: An answer whose block is malformed but closed — which costs that block and
#: leaves the blocks beside it readable.
_MALFORMED_CLAIM_BLOCK = f"{ask.CLAIM_OPEN}\nquote: a quotation\nlabel: S2\n{ask.CLAIM_CLOSE}\n"


class FakeInference:
    """An :class:`ask.SeatAsk` answering from a table keyed by document path.

    ``outcome`` is what the layer below would have said about the call, which is
    how the two transport failure paths are exercised without a subprocess.
    ``trailing`` and ``on_retry_trailing`` are text returned beside the answer
    on the ask and on the re-ask — which is how an answer that arrives and does
    not parse is expressed without hand-composing the block that does.
    ``reask`` answers a per-claim re-ask, keyed by the quote it is about; a
    quote with no entry is declined with "none of these", which is the answer
    that ends a claim unresolved without another call.
    """

    def __init__(
        self,
        answers=None,
        *,
        on_retry=None,
        reask: dict[str, _Block] | None = None,
        outcome: Outcome = Outcome.OK,
        trailing: str = "",
        on_retry_trailing: str = "",
    ):
        self._answers = _ANSWERS if answers is None else answers
        self._on_retry = on_retry
        self._reask = reask or {}
        self._outcome = outcome
        self._trailing = trailing
        self._on_retry_trailing = on_retry_trailing
        self.seats: list[str] = []
        self.prompts: list[str] = []

    def __call__(
        self, *, seat: str, prompt: str, cwd: Path | None = None, capture_path: Path | None = None
    ) -> tuple[str, Outcome]:
        del cwd, capture_path
        self.seats.append(seat)
        self.prompts.append(prompt)
        about = _REASK_RE.search(prompt)
        if about is not None:
            chosen = self._reask.get(about.group(1))
            if chosen is None:
                return f"{ask.NONE_OF_THESE_OPEN}\n{ask.NONE_OF_THESE_CLOSE}\n", self._outcome
            return ask.identify_answer_block(_records([chosen])), self._outcome

        document = _DOCUMENT_RE.search(prompt).group(1)
        retrying = _RETRY_HEADING in prompt
        table = self._on_retry if (retrying and self._on_retry is not None) else self._answers
        blocks, reason = table[document]
        answer = ask.identify_answer_block(_records(blocks), reason)
        return answer + (self._on_retry_trailing if retrying else self._trailing), self._outcome


#: One scripted call: the blocks to answer with, and text returned beside the
#: answer — an unclosed block being how an answer that cannot be read is
#: expressed without hand-composing the format that can.
_Turn = tuple[tuple[_Block, ...], str]


class ScriptedInference:
    """An :class:`ask.SeatAsk` answering one scripted turn per call, keyed by document.

    :class:`FakeInference` answers one thing on the ask and another on every
    re-ask, which cannot express a document whose first two answers fail
    *different* classes — and which class each answer failed is the whole of
    what a sequence is about. A call past the last scripted turn is the call
    bound broken, and fails here rather than looping.
    """

    def __init__(self, script: dict[str, tuple[_Turn, ...]]):
        self._script = script
        self._taken: Counter[str] = Counter()
        self.prompts: list[str] = []

    def __call__(
        self, *, seat: str, prompt: str, cwd: Path | None = None, capture_path: Path | None = None
    ) -> tuple[str, Outcome]:
        del seat, cwd, capture_path
        self.prompts.append(prompt)
        document = _DOCUMENT_RE.search(prompt).group(1)
        turns = self._script[document]
        assert self._taken[document] < len(turns), f"{document}: a call past the last scripted turn"
        blocks, trailing = turns[self._taken[document]]
        self._taken[document] += 1
        return ask.identify_answer_block(_records(blocks)) + trailing, Outcome.OK


# ---------------------------------------------------------------------------
# The consuming repository, and the declared pass over it
# ---------------------------------------------------------------------------


@pytest.fixture
def consumer(tmp_path: Path) -> Path:
    repo = tmp_path / "consumer"
    for relative, text in _TREE.items():
        target = repo / "kb-root" / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")
    (repo / ".git").mkdir()
    (repo / "justfile").write_text("default:\n    @true\n", encoding="utf-8")
    installed = repo / ".claude" / "agents"
    installed.mkdir(parents=True)
    os.symlink(_PACKAGE_ROOT, installed / _PACKAGE_ROOT.name)
    kb_util.install_targets(repo, "just")
    return repo


def _scratch(repo: Path) -> Path:
    return repo / kb_util.SCRATCH_DIRNAME / "claimgraph"


@pytest.fixture
def declared(consumer: Path) -> Path:
    """``consumer`` after the declared pass has run and its gates are green."""
    outcome = build(kb_root=consumer / "kb-root", repo_root=consumer, scratch=_scratch(consumer))
    assert not outcome.failed, outcome.lines()
    return consumer


def _identifier(inference: ask.SeatAsk, *, cwd: Path = Path(".")) -> ask.ModelIdentifier:
    return ask.ModelIdentifier(cwd=cwd, ask=inference)


def _discover(repo: Path, inference: FakeInference):
    return discover.build(
        kb_root=repo / "kb-root",
        repo_root=repo,
        scratch=_scratch(repo),
        identifier=_identifier(inference),
    )


def _texts(kb_root: Path) -> dict[str, str]:
    return {path: document.text for path, document in tree.read(kb_root).documents.items()}


def _reading(repo: Path, path: str) -> identify.Reading:
    documents = tree.read(repo / "kb-root")
    return identify.reading_of(documents.documents[path], inventory.scan(documents))


def _slice(repo: Path, locator: str, *, path: str = "vol/epsilon.md") -> str:
    """The bytes a locator names — the tool's own slice, as C5 will hand it over."""
    return _reading(repo, path).render.resolve(locator).excerpt


def _entries(repo: Path):
    register = repo / "kb-root" / "vol" / "claim-quality.md"
    return kb_index_lib.parse_claim_quality_file(register, repo / "kb-root")


# ---------------------------------------------------------------------------
# 1 — the entry condition and the scope
# ---------------------------------------------------------------------------


def test_the_scope_is_the_awaiting_partition_and_nothing_else(declared: Path):
    state = conform.pass_two_gate(tree.read(declared / "kb-root"))
    assert state.hosting == ("vol/alpha.md",)
    assert state.awaiting == ("vol/epsilon.md", "vol/zeta.md")
    assert state.determined == ()


def test_a_document_carrying_an_authored_reason_is_not_reopened(declared: Path):
    leaf = declared / "kb-root" / "vol" / "zeta.md"
    leaf.write_text(
        leaf.read_text(encoding="utf-8").replace(UNSCANNED_REASON, _ZETA_REASON),
        encoding="utf-8",
    )
    inference = FakeInference()
    assert not _discover(declared, inference).failed
    assert [_DOCUMENT_RE.search(prompt).group(1) for prompt in inference.prompts] == ["vol/epsilon.md"]


def test_a_run_over_a_tree_the_declared_pass_has_not_touched_stops_before_the_ask(consumer: Path):
    inference = FakeInference()
    report = _discover(consumer, inference)
    assert report.failed
    assert any("frontmatter-absent" in line for line in report.lines())
    assert inference.prompts == []


def test_a_document_both_awaiting_and_hosting_a_block_stops_the_stage():
    """Vacuous over a real tree, and asserted rather than assumed."""
    sites = inventory.Inventory(
        blocks=(
            inventory.Block(
                document="vol/alpha.md",
                environment="theorem",
                identifier=None,
                title="A",
                display="**Theorem 1** (A).",
                start=4,
                end=7,
            ),
        )
    )
    with pytest.raises(discover.DiscoveryError) as refusal:
        discover._no_block_in_scope(sites, ("vol/alpha.md",))
    assert refusal.value.check == "block-in-scope"


# ---------------------------------------------------------------------------
# 2 — the labelled render, which is what the seat may name
# ---------------------------------------------------------------------------


def test_the_render_labels_one_sentence_per_line_with_the_wraps_collapsed(declared: Path):
    rendered = _reading(declared, "vol/epsilon.md").render
    labelled = {sentence.label: sentence.text for sentence in rendered.sentences}

    assert labelled["S1"] == "# Epsilon"
    assert labelled["S2"] == "The author marked nothing here."
    assert labelled[_CLOSURE_LABEL].startswith("The admissible configuration set is closed under")
    assert labelled[_CLOSURE_LABEL].endswith("admits a stationary point.")
    assert "\n" not in labelled[_CLOSURE_LABEL]
    assert labelled["S5"] == "The rate parameter satisfies"
    assert labelled["S6"] == "``` math"


def test_an_abbreviation_followed_by_a_numeral_is_not_a_sentence_end(declared: Path):
    """``Fig. 3`` is what a naive split on a full stop and a space gets wrong."""
    labelled = {sentence.label: sentence.text for sentence in _reading(declared, "vol/epsilon.md").render.sentences}
    assert labelled["S4"] == "See Fig. 3 for the picture."


def test_inline_maths_carrying_a_decimal_point_is_not_two_sentences():
    """``$x = 1.5$`` is one sentence's worth of maths, and the split must not enter it."""
    body = "<!-- kb-frontmatter\nkind: leaf\n-->\n\nThe bound is $x = 1.5$ here. A second sentence follows.\n"
    rendered = label.render(body)

    assert [sentence.text for sentence in rendered.sentences] == [
        "The bound is $x = 1.5$ here.",
        "A second sentence follows.",
    ]


def test_navigation_and_the_frontmatter_block_carry_no_label(declared: Path):
    """What the render never offers, a locator cannot name — which is why no check says so."""
    reading = _reading(declared, "vol/epsilon.md")
    rendered = reading.render

    assert _UPLINK in rendered.text
    assert "kb-frontmatter" in rendered.text
    assert all(_UPLINK not in sentence.text for sentence in rendered.sentences)
    assert all("kb-frontmatter" not in sentence.text for sentence in rendered.sentences)
    assert min(sentence.line for sentence in rendered.sentences) > 0


def test_a_fence_flush_against_its_prose_stays_inside_that_paragraph(declared: Path):
    """The measured majority: a span crossing such a fence is findable, and is admitted."""
    reading = _reading(declared, "vol/epsilon.md")
    span = reading.render.resolve(_UNIQUENESS_EXTENT)

    assert len(span.paragraphs) == 1
    assert "``` math" in span.excerpt
    assert ops.excerpt_lines(reading.text, span.excerpt) == (span.line,)


def test_a_blank_separated_fence_is_a_paragraph_of_its_own(declared: Path):
    """The measured minority, and the reason the paragraph rule is stated over blank lines.

    ``ops.excerpt_lines`` joins each body line's collapsed text with single
    spaces, so a blank line puts two into the haystack and no slice can cross
    one. A paragraph here is exactly a run of lines a slice can span, which is
    what makes the paragraph rule a findability rule rather than a matter of
    taste about locators.
    """
    reading = _reading(declared, "vol/epsilon.md")
    separated = reading.render.resolve(_SEPARATED_FENCE_LOCATOR)

    assert len(separated.paragraphs) == 1
    assert ops.excerpt_lines(reading.text, separated.excerpt) == (separated.line,)

    crossing = reading.render.resolve("S10-S13")
    assert len(crossing.paragraphs) > 1
    assert ops.excerpt_lines(reading.text, crossing.excerpt) == ()


def test_a_heading_opens_a_paragraph_of_its_own(declared: Path):
    """So a locator cannot widen out of a stated result and into the section holding it."""
    rendered = _reading(declared, "vol/epsilon.md").render
    assert len(rendered.resolve(f"{_HEADING_LOCATOR}-S2").paragraphs) == 2


def test_a_locator_resolves_to_the_tool_s_own_slice_and_the_seat_supplies_no_bytes(declared: Path):
    rendered = _reading(declared, "vol/epsilon.md").render
    span = rendered.resolve(_CLOSURE_LABEL)

    assert span.excerpt in render.collapse_prose(_EPSILON.replace("\n", " "))
    assert span.locator == _CLOSURE_LABEL


@pytest.mark.parametrize(
    ("locator", "complaint"),
    [
        ("S999", "does not carry"),
        ("S5-S3", "runs backwards"),
        ("paragraph two", "is not a locator"),
        ("12", "is not a locator"),
    ],
)
def test_a_locator_that_names_no_span_is_refused_by_the_render(declared: Path, locator: str, complaint: str):
    with pytest.raises(label.LocatorError, match=complaint):
        _reading(declared, "vol/epsilon.md").render.resolve(locator)


# ---------------------------------------------------------------------------
# 3 — the ask and its parse
# ---------------------------------------------------------------------------


def test_the_prompt_carries_the_labelled_document_its_maths_labels_and_no_other_document(declared: Path):
    reading = _reading(declared, "vol/epsilon.md")
    prompt = ask.compose_identify_prompt(reading, report=None)

    assert _DOCUMENT_RE.search(prompt).group(1) == "vol/epsilon.md"
    assert f"{_CLOSURE_LABEL}: The admissible configuration set" in prompt
    assert "S6, S7, S8" in prompt, "the maths spans are named as labels, which are the seat's only coordinates"
    assert "Alpha holds for every admissible state" not in prompt


def test_each_substitution_lands_inside_its_own_section(declared: Path):
    """The three seams the fragment files sit at, checked as bytes rather than as substrings.

    A fragment fills a slot inside a line, so the template supplies the breaks
    around it and the file itself carries no trailing newline. Nothing about a
    ``.tmpl`` says so, and an editor adding the customary final newline moves
    every prompt by one blank line — here, and nowhere a reader would look.
    """
    fenced = ask.compose_identify_prompt(_reading(declared, "vol/epsilon.md"), report=None)
    bare = ask.compose_identify_prompt(_reading(declared, "vol/zeta.md"), report=None)
    corrected = ask.compose_identify_prompt(_reading(declared, "vol/zeta.md"), report="the locator names no label")

    assert "\n\n- S6, S7, S8, S11, S12, S13\n\n## What to return\n" in fenced
    assert "\n\nThe document carries no display-maths block.\n\n## What to return\n" in bare
    assert corrected == bare.rstrip("\n") + (
        "\n\n## A previous answer failed a mechanical check\n\nthe locator names no label\n"
    )


def test_the_prompt_shows_the_body_with_an_earlier_pass_s_markers_taken_off(declared: Path):
    marked = "vol/alpha.md"
    leaf = declared / "kb-root" / marked
    leaf.write_text(leaf.read_text(encoding="utf-8").rstrip("\n") + " <!-- claim-quality: clm-aaaaaa -->\n", "utf-8")
    reading = _reading(declared, marked)
    assert "clm-aaaaaa" in reading.text and "clm-aaaaaa" not in reading.body
    assert len(reading.text.splitlines()) == len(reading.body.splitlines())
    assert "clm-aaaaaa" not in reading.render.text


#: One well-formed claim block, composed so that the malformations below are
#: each one edit away from a block that reads.
_GOOD_BLOCK = f"{ask.CLAIM_OPEN}\nquote: a quotation\nlabel: S3\ntitle: A title\n{ask.CLAIM_CLOSE}\n"


@pytest.mark.parametrize(
    ("malformed", "complaint"),
    [
        pytest.param(
            f"{ask.CLAIM_OPEN}\nquote: a quotation\nlabel: S3\n{ask.CLAIM_CLOSE}\n",
            "missing required field(s): title",
            id="a-field-left-out",
        ),
        pytest.param(
            f"{ask.CLAIM_OPEN}\nquote: a quotation\nlabel: S3\ntitle: A\nwhy: because\n{ask.CLAIM_CLOSE}\n",
            "unknown field 'why'",
            id="a-field-invented",
        ),
        pytest.param(
            f"{ask.CLAIM_OPEN}\nquote: one\nquote: two\nlabel: S3\ntitle: A\n{ask.CLAIM_CLOSE}\n",
            "two 'quote' lines",
            id="a-field-written-twice",
        ),
        pytest.param(
            f"{ask.CLAIM_OPEN}\nquote: a quotation\nlabel: S3\ntitle:\n{ask.CLAIM_CLOSE}\n",
            "'title' carries no value",
            id="a-field-left-empty",
        ),
        pytest.param(
            f"{ask.CLAIM_OPEN}\nquote: a quotation\nand a stray line\nlabel: S3\ntitle: A\n{ask.CLAIM_CLOSE}\n",
            "is not a field line",
            id="a-line-that-is-not-a-field",
        ),
    ],
)
def test_a_malformed_claim_block_costs_that_block_and_the_blocks_beside_it_still_read(malformed, complaint):
    """The whole of what the third transport class buys: a slip in one record costs that record.

    The JSON envelope this replaced failed the other way — nine well-formed
    claims were discarded whole because a tenth block's two markers carried
    different numbers.
    """
    answer = ask.parse_identify_answer(_GOOD_BLOCK + malformed + _GOOD_BLOCK, document="d.md")

    assert [record.title for record in answer.claims] == ["A title", "A title"]
    assert len(answer.refusals) == 1 and complaint in answer.refusals[0]


@pytest.mark.parametrize(
    ("text", "complaint"),
    [
        pytest.param("there is no block here at all\n", "carries no", id="no-block-of-either-kind"),
        pytest.param(_UNCLOSED_CLAIM_BLOCK, "never closed", id="a-block-never-closed"),
        pytest.param(
            f"{ask.NO_CLAIM_OPEN}\nfirst\n{ask.NO_CLAIM_CLOSE}\n{ask.NO_CLAIM_OPEN}\nsecond\n"
            f"{ask.NO_CLAIM_CLOSE}\n",
            "blocks",
            id="two-reasons",
        ),
    ],
)
def test_an_answer_that_cannot_be_read_at_all_is_refused_whole(text: str, complaint: str):
    """The only three failures left that are the answer's rather than one block's."""
    with pytest.raises(AnswerFormatError, match=re.escape(complaint)):
        ask.parse_identify_answer(text, document="d.md")


def test_no_field_of_an_answer_is_a_number_kept_consistent_with_another_number():
    """Acceptance: the pointer the old shape failed on does not exist in this one.

    Every block is self-contained and unnumbered, so the answer is a flat
    sequence in any order and nothing in it refers to anything else in it.
    """
    composed = ask.identify_answer_block(_records([(_CLOSURE_QUOTE, _CLOSURE_LABEL, _CLOSURE_TITLE)]))
    assert not re.search(r"\d", composed.replace(_CLOSURE_LABEL, ""))

    forward = ask.identify_answer_block(_records([("first quote", "S3", "First"), ("second quote", "S9", "Second")]))
    backward = ask.identify_answer_block(_records([("second quote", "S9", "Second"), ("first quote", "S3", "First")]))
    assert {record.title for record in ask.parse_identify_answer(forward, document="d.md").claims} == (
        {record.title for record in ask.parse_identify_answer(backward, document="d.md").claims}
    )


def test_a_quote_lifted_from_another_document_resolves_nowhere_in_this_one(declared: Path):
    """What replaced the parser's document cross-check, and it is the stronger of the two.

    The answer carries no path to compare, and there is nothing to compare it
    to: what settles whose answer this is, is whether its words are in this
    document.
    """
    answer = identify.Answer(claims=(identify.Record(quote="Alpha holds for every", label="S3", title="A title"),))
    checked = identify.check_answer(answer, _reading(declared, "vol/epsilon.md"))

    assert checked.claims == ()
    assert [missed.trigger for missed in checked.unresolved] == [identify.Trigger.NOWHERE]


@pytest.mark.parametrize(
    "outcome, calls",
    [
        (Outcome.SILENCE, ask.TRANSPORT_ATTEMPTS),
        (Outcome.TIMEOUT, ask.TRANSPORT_ATTEMPTS),
        (Outcome.TRANSPORT_FAILURE, ask.TRANSPORT_ATTEMPTS),
        (Outcome.CLI_REJECTION, 1),
    ],
)
def test_a_call_that_did_not_complete_stops_the_stage_on_the_layer_s_own_verdict(declared, outcome, calls):
    failing = FakeInference(outcome=outcome)
    with pytest.raises(ask.AskError) as refusal:
        identify.infer_claims(_reading(declared, "vol/epsilon.md"), _identifier(failing))
    assert refusal.value.check == "inference-failed"
    assert len(failing.prompts) == calls
    assert len(set(failing.prompts)) == 1, "a re-issue changes nothing about the ask"


def test_the_seat_is_a_constructor_parameter_and_not_a_constant(declared: Path):
    inference = FakeInference()
    identify.infer_claims(_reading(declared, "vol/zeta.md"), _identifier(inference))
    assert inference.seats == [ask.SEAT] == ["applied-mathematician"]

    moved = FakeInference()
    identify.infer_claims(
        _reading(declared, "vol/zeta.md"),
        ask.ModelIdentifier(cwd=Path("."), ask=moved, seat="kb-maintainer"),
    )
    assert moved.seats == ["kb-maintainer"]


# ---------------------------------------------------------------------------
# 4 — no model-facing transport demands an escape grammar
# ---------------------------------------------------------------------------

#: One sequence of each class. ``\sigma`` is not a JSON escape and fails loudly
#: inside JSON; ``\beta`` is one, decodes to a backspace, and fails later and
#: silently as a value that will not locate. What the fixture pins is the two
#: classes, not these two spellings.
_NOT_AN_ESCAPE = "\\sigma"
_IS_AN_ESCAPE = "\\beta"
_MATHEMATICAL_PROSE = f"Supercritical for ${_NOT_AN_ESCAPE} < 1$ and ${_IS_AN_ESCAPE} > 0$"


def test_every_key_of_every_site_is_classified_by_exactly_one_transport():
    """Acceptance: the three transport declarations partition each level's vocabulary.

    Each is an independent statement, and ``check_levels`` is what proves them a
    partition — at import, so a level added without a classification cannot be
    shipped rather than merely being noticed later.
    """
    for level in (*envelope.LEVELS, *ask.LEVELS):
        declared = [*level.json, *level.prose, *level.fields]
        assert set(declared) == set(level.keys), level.label
        assert len(declared) == len(set(declared)), level.label

    for broken in (
        envelope.Level("gap", ("a", "b"), json=("a",)),
        envelope.Level("overlap", ("a",), json=("a",), prose=("a",)),
        envelope.Level("two-raw-transports", ("a",), prose=("a",), fields=("a",)),
        envelope.Level("stray", ("a",), json=("a",), fields=("b",)),
    ):
        with pytest.raises(envelope.ParseError, match="do not partition"):
            envelope.check_levels([broken])


def test_each_site_takes_an_answer_whose_every_composed_value_carries_an_unescaped_backslash():
    """Acceptance: what used to stop a live run at ``Invalid \\escape`` now parses.

    Every value below is composed or quoted by the seat and every one carries a
    sequence no JSON string could have carried raw. Stage D's answer holds no
    composed value at all, and its declaration is checked by handing it a prose
    block nothing names.
    """
    identified = ask.parse_identify_answer(
        ask.identify_answer_block(_records([(f"{_NOT_AN_ESCAPE} opens it", "S3", _MATHEMATICAL_PROSE)])),
        document="d.md",
    )
    assert identified.claims[0].title == _MATHEMATICAL_PROSE
    assert identified.claims[0].quote == f"{_NOT_AN_ESCAPE} opens it"

    stated = ask.parse_identify_answer(ask.identify_answer_block(no_claim=_MATHEMATICAL_PROSE), document="d.md")
    assert stated.no_claim == _MATHEMATICAL_PROSE

    parsed = envelope.parse_envelope(
        envelope.envelope_block(
            envelope.Envelope(
                step="p0.survey",
                members=(envelope.Member(name="A.tex", status=_MATHEMATICAL_PROSE),),
                gaps=(_MATHEMATICAL_PROSE,),
                deviations=(
                    envelope.Deviation(
                        kind="triage", member="A.tex", what=_MATHEMATICAL_PROSE, why=_MATHEMATICAL_PROSE
                    ),
                ),
            )
        )
    )
    assert parsed.members[0].status == parsed.gaps[0] == parsed.deviations[0].what == _MATHEMATICAL_PROSE

    with pytest.raises(AnswerFormatError, match="named by no key"):
        ask.parse_answer(
            ask.answer_block("clm-aaaaaa", ()) + f"<<<{ask.PROSE_NAME} 1\n{_MATHEMATICAL_PROSE}\n{ask.PROSE_NAME} 1\n",
            claim="clm-aaaaaa",
        )


def test_mathematical_prose_reaches_the_register_and_the_marker_line_byte_identical(declared: Path):
    """Acceptance: byte identity from the returned text through to the written tree.

    Parsing is only the first thing that used to fail. What is under test is that
    every value carrying one of the two escape classes is the same bytes at the
    end of the run as at the start of it — the seat's title in the register, the
    seat's reason in the frontmatter, and the tool's own slice on the line the
    marker lands on. The corpus supplies the second class without being asked:
    ``\\tfrac`` opens with ``\\t``.
    """
    answers = {
        "vol/epsilon.md": (
            (
                (_CLOSURE_QUOTE, _CLOSURE_LABEL, "Closure of the admissible set"),
                (_UNIQUENESS_QUOTE, _UNIQUENESS_LABEL, _MATHEMATICAL_PROSE),
            ),
            "",
        ),
        "vol/zeta.md": ((), f"No result is stated; the notation ${_IS_AN_ESCAPE}$ is only introduced."),
    }
    returned = ask.identify_answer_block(_records([(_UNIQUENESS_QUOTE, _UNIQUENESS_LABEL, _MATHEMATICAL_PROSE)]))
    assert f"title: {_MATHEMATICAL_PROSE}\n" in returned, "the returned text carries the words as the seat wrote them"

    assert not _discover(declared, FakeInference(answers)).failed

    documents = tree.read(declared / "kb-root")
    text = documents.documents["vol/epsilon.md"].text
    marked = next(
        node
        for node in graph.read(documents, inventory.scan(documents)).nodes.values()
        if node.title == _MATHEMATICAL_PROSE
    )

    # The seat's own words, on the page, unchanged.
    assert _MATHEMATICAL_PROSE in [entry.title for entry in _entries(declared)]
    assert f"${_IS_AN_ESCAPE}$" in documents.documents["vol/zeta.md"].text

    # The tool's own slice, carrying a sequence of each class out of the corpus
    # itself, handed to the op and located: the marker sits on the span's first
    # line, and that line is what persists as the claim's locator.
    slice_of = _slice(declared, _UNIQUENESS_EXTENT)
    assert "\\lambda" in slice_of and "\\tfrac" in slice_of
    assert slice_of in (_scratch(declared) / "cinf-vol_epsilon.md-4-mark-claim-in-leaf.toml").read_text("utf-8")
    lines = ops.excerpt_lines(tree.strip_markers(text), slice_of)
    assert len(lines) == 1
    assert render.render_tier2_marker(marked.id) in text.splitlines()[lines[0]]
    assert marked.locator == tree.strip_markers(text.splitlines()[lines[0]]).strip()


# ---------------------------------------------------------------------------
# 5 — the mechanical checks, each against the render or the document itself
# ---------------------------------------------------------------------------


def _checked(declared: Path, blocks, *, reason: str = "", path: str = "vol/epsilon.md") -> identify.Checked:
    return identify.check_answer(
        identify.Answer(claims=tuple(_records(blocks)), no_claim=reason), _reading(declared, path)
    )


def test_a_quote_crossing_a_hard_wrap_resolves_and_its_claim_locates_at_one_line(declared: Path):
    checked = _checked(declared, [(_CLOSURE_QUOTE, _CLOSURE_LABEL, "Closure")])
    assert checked.failures == ()
    assert len(checked.claims) == 1
    located = checked.claims[0]
    body = (declared / "kb-root" / "vol" / "epsilon.md").read_text(encoding="utf-8").splitlines()
    assert body[located.line].startswith("The author marked nothing here")


def test_an_extent_runs_to_the_next_start_in_its_paragraph_or_to_the_paragraph_s_end(declared: Path):
    """§1.2, which is the whole of the answer to the granularity the ask left free.

    Asked twice about one document, a seat subdivided three of the same spans
    the second time. The extent is not the seat's any more: it is what the
    resolved starts make of the paragraph they sit in.
    """
    alone = _checked(declared, [(_CLOSURE_QUOTE, _CLOSURE_LABEL, "Closure")])
    assert [claim.locator for claim in alone.claims] == [_CLOSURE_EXTENT]

    both = _checked(
        declared,
        [(_OPENER_QUOTE, "S2", "The opener"), (_CLOSURE_QUOTE, _CLOSURE_LABEL, "Closure")],
    )
    assert both.failures == ()
    # The earlier start's extent now stops where the later one begins, and the
    # later one still runs to the paragraph's end. They partition; they do not
    # nest, and neither crosses the break.
    assert [claim.locator for claim in both.claims] == ["S2", _CLOSURE_EXTENT]


def test_an_extent_may_contain_an_equation(declared: Path):
    """The ordinary way this corpus states a result, and refusing it would refuse most of them."""
    checked = _checked(declared, [(_UNIQUENESS_QUOTE, _UNIQUENESS_LABEL, "Uniqueness")])
    assert checked.failures == ()
    assert checked.claims[0].locator == _UNIQUENESS_EXTENT
    assert "``` math" in checked.claims[0].excerpt


@pytest.mark.parametrize(
    ("quote", "why"),
    [
        pytest.param("no such words appear in this document", "not in the document", id="resolves-nowhere"),
        pytest.param("\\lambda = \\tfrac", "the start is inside a display-maths fence", id="an-equation"),
        pytest.param("# Epsilon", "the start canonicalises to one word", id="too-short-to-open-a-claim"),
    ],
)
def test_a_quote_no_hit_survives_the_filter_for_is_unresolved_rather_than_refused(declared, quote, why):
    """§2.5 composes as a filter over the hits, taken before the count in §2.2 is.

    So a quote whose only hit cannot open a claim is a quote that resolved
    nowhere — one trigger, not a second refusal beside it, and re-askable like
    any other.
    """
    checked = _checked(declared, [(quote, "S3", "A title")])
    assert checked.claims == () and checked.refusals == (), why
    assert [missed.trigger for missed in checked.unresolved] == [identify.Trigger.NOWHERE]


def test_a_quote_whose_label_disagrees_raises_trigger_c_and_shows_both_sentences(declared: Path):
    checked = _checked(declared, [(_CLOSURE_QUOTE, "S9", "A title")])

    assert checked.claims == ()
    missed = checked.unresolved[0]
    assert missed.trigger is identify.Trigger.DISAGREED
    assert any(line.startswith("S9:") for line in missed.candidates), "what stands at the claimed label"
    assert any(line.startswith(f"{_CLOSURE_LABEL}:") for line in missed.candidates), "what the quote matched"


def test_a_quote_resolving_in_several_places_is_settled_by_its_label_without_a_call(declared: Path):
    """The label's whole job: it breaks the tie, and only where there is one to break."""
    body = (
        "<!-- kb-frontmatter\nkind: leaf\n-->\n\n"
        "The operator is monotone on the cone.\n\n"
        "The operator is monotone on the cone, and the bound follows at once.\n"
    )
    reading = identify.Reading(document="vol/leaf.md", text=body, body=body)
    labels = [sentence.label for sentence in reading.render.sentences]
    assert len(labels) == 2

    settled = identify.check_answer(
        identify.Answer(claims=(identify.Record(quote="The operator is monotone", label=labels[1], title="A title"),)),
        reading,
    )
    assert settled.unresolved == ()
    assert [claim.locator for claim in settled.claims] == [labels[1]]

    unsettled = identify.check_answer(
        identify.Answer(claims=(identify.Record(quote="The operator is monotone", label="S99", title="A title"),)),
        reading,
    )
    missed = unsettled.unresolved[0]
    assert missed.trigger is identify.Trigger.SEVERAL
    assert len(missed.candidates) == 2


def test_a_start_inside_a_claim_bearing_block_is_refused(declared: Path):
    """Vacuous over C-inf's scope, and asserted rather than assumed."""
    checked = _checked(declared, [("Alpha holds for every admissible state", "S4", "A title")], path="vol/alpha.md")

    assert checked.claims == ()
    assert "inside the author's own theorem block" in checked.refusals[0]


def test_a_repeated_slice_is_widened_within_its_paragraph_rather_than_re_asked(declared: Path):
    """Uniqueness is not obviated, and the seat has no instrument for fixing it — the tool does.

    And it is the *locator* that widens, never the extent: the claim still runs
    to the paragraph's end, and the extra sentence is bought for findability
    alone.
    """
    body = (
        "<!-- kb-frontmatter\nkind: leaf\n-->\n\n"
        "The bound holds here. The bound holds here. It holds because the operator is monotone.\n"
    )
    reading = identify.Reading(document="vol/leaf.md", text=body, body=body)
    labels = [sentence.label for sentence in reading.render.sentences]
    assert len(labels) == 3

    checked = identify.check_answer(
        identify.Answer(claims=(identify.Record(quote="The bound holds here", label=labels[0], title="A title"),)),
        reading,
    )

    assert checked.failures == ()
    assert checked.claims[0].locator == f"{labels[0]}-{labels[2]}", "the extent runs to the paragraph's end"
    assert ops.excerpt_lines(body, checked.claims[0].excerpt) == (checked.claims[0].line,)


def test_a_slice_still_repeated_across_its_whole_paragraph_costs_that_claim(declared: Path):
    """Widening ends at the paragraph, and the claim fails there rather than widening past it."""
    body = "<!-- kb-frontmatter\nkind: leaf\n-->\n\nThe bound holds here.\n\nThe bound holds here.\n"
    reading = identify.Reading(document="vol/leaf.md", text=body, body=body)

    checked = identify.check_answer(
        identify.Answer(claims=(identify.Record(quote="The bound holds here", label="S1", title="A title"),)), reading
    )

    assert checked.claims == ()
    assert "widened to their whole paragraph" in checked.refusals[0]


@pytest.mark.parametrize("title", ["A title\nand a second line", "   "])
def test_a_title_that_is_not_one_line_of_text_costs_that_claim(declared, title):
    checked = _checked(declared, [(_CLOSURE_QUOTE, _CLOSURE_LABEL, title)])
    assert checked.claims == () and len(checked.refusals) == 1


def test_two_blocks_sharing_a_title_cost_the_second(declared: Path):
    checked = _checked(
        declared,
        [(_CLOSURE_QUOTE, _CLOSURE_LABEL, "One title"), (_UNIQUENESS_QUOTE, _UNIQUENESS_LABEL, "One title")],
    )
    assert len(checked.claims) == 1
    assert "is the title of two records" in checked.refusals[0]


@pytest.mark.parametrize(
    "blocks, reason, kind",
    [
        (((_CLOSURE_QUOTE, _CLOSURE_LABEL, "A title"),), "and also a reason", "record"),
        ((), "", "reason"),
        ((), UNSCANNED_REASON, "reason"),
        ((), "a reason\nover two lines", "reason"),
    ],
)
def test_exactly_one_of_claim_blocks_and_a_reason_is_an_answer(declared, blocks, reason, kind):
    checked = _checked(declared, blocks, reason=reason)
    assert bool(checked.refusals) == (kind == "record")
    assert bool(checked.reason_failures) == (kind == "reason")


def test_two_blocks_resolving_to_one_sentence_cost_the_second(declared: Path):
    """The shape a live run stopped on: one sentence named once per result read off it.

    A markdown table renders as a single labelled line, so a seat reading a row
    of it as a result quotes the same opening however many rows it names. Under
    §1.2 both extents *are* the same span, and one span states one result once.
    The collision is therefore over the start, not over the physical line — two
    starts sharing a wrapped line is the ordinary case now.
    """
    checked = _checked(
        declared,
        [(_CLOSURE_QUOTE, _CLOSURE_LABEL, "First"), (_CLOSURE_QUOTE, _CLOSURE_LABEL, "Second")],
    )

    assert [claim.locator for claim in checked.claims] == [_CLOSURE_EXTENT]
    assert len(checked.refusals) == 1
    assert "resolve to the same sentence" in checked.refusals[0]


def test_two_starts_sharing_a_wrapped_line_are_both_written(declared: Path):
    """What the old line-level collision check would have refused, and §1.2 makes ordinary.

    ``S2`` and ``S3`` open on the same physical line of Epsilon. Their extents
    partition the paragraph, so neither runs past the other's start and neither
    marker can make the other unfindable.
    """
    checked = _checked(
        declared,
        [(_OPENER_QUOTE, "S2", "First"), (_CLOSURE_QUOTE, _CLOSURE_LABEL, "Second")],
    )

    assert checked.failures == ()
    assert [claim.line for claim in checked.claims] == [checked.claims[0].line] * 2


def test_the_uniqueness_check_is_the_write_api_s_own_matching(declared: Path):
    """Not a second implementation of it: the same call, over the same bytes."""
    text = (declared / "kb-root" / "vol" / "epsilon.md").read_text(encoding="utf-8")
    checked = _checked(declared, [(_CLOSURE_QUOTE, _CLOSURE_LABEL, "Closure")])
    assert ops.excerpt_lines(text, checked.claims[0].excerpt) == (checked.claims[0].line,)


# ---------------------------------------------------------------------------
# 6 — the re-asks: one per unresolved claim, and what a failure costs
# ---------------------------------------------------------------------------

#: A document whose first claim will not resolve and whose second will. The
#: unresolvable one carries the label of a real sentence, so it is a quote that
#: failed and not a label that did.
_ONE_FAILS = {
    "vol/epsilon.md": (
        (
            ("no such words appear in this document", "S9", "A fabricated result"),
            (_UNIQUENESS_QUOTE, _UNIQUENESS_LABEL, _UNIQUENESS_TITLE),
        ),
        "",
    ),
    "vol/zeta.md": ((), _ZETA_REASON),
}


def test_an_unresolvable_claim_is_re_asked_on_its_own_and_costs_only_that_claim(declared: Path):
    """Acceptance 6: the document keeps its other claims.

    One call for the whole document, then one call about the one quote that did
    not settle — never the document again.
    """
    inference = FakeInference(_ONE_FAILS)
    found = identify.infer_claims(_reading(declared, "vol/epsilon.md"), _identifier(inference))

    assert [claim.title for claim in found.claims] == [_UNIQUENESS_TITLE]
    assert len(inference.prompts) == 2
    assert _REASK_RE.search(inference.prompts[1]).group(1) == "no such words appear in this document"
    assert _RETRY_HEADING not in inference.prompts[1], "a per-claim re-ask is not the document asked again"


def test_a_re_ask_that_settles_recovers_that_claim(declared: Path):
    inference = FakeInference(
        _ONE_FAILS,
        reask={"no such words appear in this document": (_CLOSURE_QUOTE, _CLOSURE_LABEL, _CLOSURE_TITLE)},
    )
    found = identify.infer_claims(_reading(declared, "vol/epsilon.md"), _identifier(inference))

    assert sorted(claim.title for claim in found.claims) == sorted([_CLOSURE_TITLE, _UNIQUENESS_TITLE])
    assert found.telemetry.resolved_on_reask == 1
    assert found.telemetry.unresolved == 0


def test_none_of_these_ends_that_claim_without_another_call(declared: Path):
    """A forced choice over a narrowed window gets confidently answered even when it is wrong."""
    inference = FakeInference(_ONE_FAILS)
    found = identify.infer_claims(_reading(declared, "vol/epsilon.md"), _identifier(inference))

    assert len(inference.prompts) == 2, "declining ends the claim rather than re-asking about it"
    assert found.telemetry.reasked == 1
    assert found.telemetry.unresolved == 1


def test_a_quote_that_resolved_nowhere_is_shown_the_window_the_search_came_nearest(declared: Path):
    """Trigger A carries both facts, because the seat has two ways of being wrong here.

    A quote mistyped against a real sentence is answered by the window the
    search came closest to; a quote that is fine beside a label that is not is
    answered by what stands at the label. The menu presumes neither, and the
    template's "none of these" is why it does not have to.
    """
    misquoted = "The admissible configuration set is sealed under the operations"
    assert ops.resolve_excerpt(_reading(declared, "vol/epsilon.md").body, misquoted).hits == ()

    missed = _checked(declared, [(misquoted, "S9", "A title")]).unresolved[0]

    assert missed.trigger is identify.Trigger.NOWHERE
    assert any(line.startswith(f"{_CLOSURE_LABEL}:") for line in missed.candidates), "the nearest window, labelled"
    assert any(line.startswith("S9:") for line in missed.candidates), "what stands at the claimed label"
    assert len(missed.candidates) == len(set(missed.candidates)), "the two halves may overlap and are not doubled"


def test_the_candidate_exclusion_is_taken_over_the_matcher_s_own_fold(declared: Path):
    """§2.5 over the canonical form, by calling the fold rather than restating it.

    A second reduction that disagreed would classify a sentence as unquotable
    while tier 2 went on resolving quotations to it.
    """
    assert ops.canonical_form("**Proof.**").split() == ["proof"]
    assert ops.canonical_form('<span class="citation" data-cites="x"></span>').split() == []

    body = "<!-- kb-frontmatter\nkind: leaf\n-->\n\n**Proof.**\n\nThe operator is monotone on the cone.\n"
    reading = identify.Reading(document="vol/leaf.md", text=body, body=body)
    checked = identify.check_answer(
        identify.Answer(claims=(identify.Record(quote="Proof", label="S1", title="A title"),)), reading
    )

    assert checked.claims == () and checked.refusals == ()
    assert [missed.trigger for missed in checked.unresolved] == [identify.Trigger.NOWHERE]


def test_every_re_ask_carries_a_fact_computed_after_the_previous_answer(declared: Path):
    """Acceptance 9, over all three triggers, and the menu always admits "none of these"."""
    reading = _reading(declared, "vol/epsilon.md")
    for record, trigger, expected in (
        (identify.Record("no such words here at all", "S9", "T"), identify.Trigger.NOWHERE, "S9:"),
        (identify.Record(_CLOSURE_QUOTE, "S9", "T"), identify.Trigger.DISAGREED, f"{_CLOSURE_LABEL}:"),
    ):
        missed = identify.check_answer(identify.Answer(claims=(record,)), reading).unresolved[0]
        assert missed.trigger is trigger
        assert any(line.startswith(expected) for line in missed.candidates)

        prompt = ask.compose_claim_reask_prompt(reading, missed)
        assert record.quote in prompt
        assert all(line in prompt for line in missed.candidates)
        assert ask.NONE_OF_THESE_OPEN in prompt


def test_a_document_whose_every_claim_fails_takes_the_fifth_determination(declared: Path):
    """Acceptance 6 and 7: it does not halt C6, it is not ``AUTHORED_NO_CLAIM``, and it is loud."""
    all_fail = {
        "vol/epsilon.md": ((("no such words appear in this document", "S9", "A fabricated result"),), ""),
        "vol/zeta.md": ((), _ZETA_REASON),
    }
    report = _discover(declared, FakeInference(all_fail))

    assert not report.failed, report.lines()
    documents = tree.read(declared / "kb-root")
    fields = kb_index_lib.parse_frontmatter(documents.documents["vol/epsilon.md"].text)
    assert conform.determination(fields) is conform.Determination.UNANCHORED
    assert fields["no-claim"] == identify.UNANCHORED_REASON
    assert fields["no-claim"] not in (UNSCANNED_REASON, identify.SUBSTITUTED_NO_CLAIM_REASON)

    prominent = [line for line in report.lines() if "stage-C-anchored-nothing" in line]
    assert len(prominent) == 1 and "vol/epsilon.md" in prominent[0]
    assert any("0 claims" in line and "vol/epsilon.md" in line for line in report.lines())

    # It leaves AWAITING, so a re-run neither halts nor re-reads it.
    state = conform.pass_two_gate(documents)
    assert "vol/epsilon.md" not in state.awaiting and "vol/epsilon.md" in state.determined
    again = FakeInference(all_fail)
    assert not _discover(declared, again).failed
    assert again.prompts == []


def test_the_re_ask_ceiling_records_the_rest_unresolved_rather_than_spending_more_calls(declared: Path):
    """The ceiling and the recording compose with no special case."""
    fabricated = tuple((f"no such words number {n} appear here", "S9", f"Title {n}") for n in range(8))
    inference = FakeInference({"vol/epsilon.md": (fabricated, "")})
    found = identify.infer_claims(_reading(declared, "vol/epsilon.md"), _identifier(inference))

    assert len(inference.prompts) == 1 + identify.REASK_CEILING
    assert found.telemetry.returned == 8
    assert found.telemetry.reasked == identify.REASK_CEILING
    assert found.telemetry.unresolved == 8
    assert found.anchored_nothing


def test_telemetry_reports_what_the_cadence_bought(declared: Path):
    """§3.2: the datapoint that would move the cadence, collected because it costs nothing."""
    inference = FakeInference(
        _ONE_FAILS,
        reask={"no such words appear in this document": (_CLOSURE_QUOTE, _CLOSURE_LABEL, _CLOSURE_TITLE)},
    )
    telemetry = identify.infer_claims(_reading(declared, "vol/epsilon.md"), _identifier(inference)).telemetry

    assert (telemetry.returned, telemetry.resolved, telemetry.reasked, telemetry.resolved_on_reask) == (2, 1, 1, 1)
    assert "1 re-asked and 1 anchored" in telemetry.line()


def test_a_malformed_block_costs_that_block_and_the_document_keeps_the_others(declared: Path):
    """No call is spent on it: there is no menu that would answer a missing field."""
    inference = FakeInference(
        {"vol/epsilon.md": (((_UNIQUENESS_QUOTE, _UNIQUENESS_LABEL, _UNIQUENESS_TITLE),), "")},
        trailing=_MALFORMED_CLAIM_BLOCK,
    )
    found = identify.infer_claims(_reading(declared, "vol/epsilon.md"), _identifier(inference))

    assert [claim.title for claim in found.claims] == [_UNIQUENESS_TITLE]
    assert len(inference.prompts) == 1


def test_an_answer_that_cannot_be_read_costs_the_document_s_one_allowance(declared: Path):
    """An answer that arrived is an answer the seat can be asked for again.

    The read refusal is already the report: it names the malformation, so
    nothing is added to it.
    """
    inference = FakeInference(trailing=_UNCLOSED_CLAIM_BLOCK)
    found = identify.infer_claims(_reading(declared, "vol/epsilon.md"), _identifier(inference))

    assert [claim.title for claim in found.claims] == [_CLOSURE_TITLE, _UNIQUENESS_TITLE]
    assert len(inference.prompts) == 2
    assert "never closed" in inference.prompts[1].split(_RETRY_HEADING)[1]


def test_a_second_unreadable_answer_stops_the_stage_naming_the_document(declared: Path):
    inference = FakeInference(trailing=_UNCLOSED_CLAIM_BLOCK, on_retry_trailing=_UNCLOSED_CLAIM_BLOCK)
    with pytest.raises(identify.IdentificationError) as refusal:
        identify.infer_claims(_reading(declared, "vol/epsilon.md"), _identifier(inference))

    assert refusal.value.check == "answer-format"
    assert "vol/epsilon.md" in refusal.value.detail
    assert "Nothing was written for this document" in refusal.value.detail
    assert len(inference.prompts) == 2


def test_a_stop_on_the_read_leaves_the_document_awaiting_with_nothing_written(declared: Path):
    inference = FakeInference(trailing=_UNCLOSED_CLAIM_BLOCK, on_retry_trailing=_UNCLOSED_CLAIM_BLOCK)
    report = _discover(declared, inference)

    assert report.failed and any("answer-format" in line for line in report.lines())
    text = (declared / "kb-root" / "vol" / "epsilon.md").read_text(encoding="utf-8")
    assert UNSCANNED_REASON in text and "claims:" not in text
    assert len(_entries(declared)) == 1, "the register carries the declared pass's entry and nothing else"


def test_an_ill_formed_reason_is_replaced_and_the_run_continues(declared: Path):
    """The asymmetry: a missing sentence loses prose nobody had."""
    empty = {"vol/zeta.md": ((), "")}
    inference = FakeInference(empty, on_retry=empty)
    with pytest.raises(AnswerFormatError):
        # An answer with neither a claim block nor a reason block is not an
        # answer at all, so the fake cannot express this failure through the
        # composer; the reason has to be ill-formed rather than absent.
        ask.parse_identify_answer(ask.identify_answer_block(), document="vol/zeta.md")

    inference = FakeInference({"vol/zeta.md": ((), UNSCANNED_REASON)})
    found = identify.infer_claims(_reading(declared, "vol/zeta.md"), _identifier(inference))
    assert found.claims == ()
    assert found.no_claim == identify.SUBSTITUTED_NO_CLAIM_REASON
    assert found.no_claim != UNSCANNED_REASON
    assert len(inference.prompts) == 2, "the reason costs the document's one allowance before it is replaced"


#: Two answers one live document gave, in the order it gave them: one that could
#: not be read, and one that stands. Read off a run's own captures, where the
#: first arrived as call 016 for one document.
_COULD_NOT_BE_READ: _Turn = (((_CLOSURE_QUOTE, _CLOSURE_LABEL, _CLOSURE_TITLE),), _UNCLOSED_CLAIM_BLOCK)
_STANDS: _Turn = (((_CLOSURE_QUOTE, _CLOSURE_LABEL, _CLOSURE_TITLE),), "")


def test_the_document_allowance_and_the_per_claim_ceiling_are_separate_budgets(declared: Path):
    """One shared budget is what cost the live run the answer that stood.

    A first answer that could not be read spent the document's only re-ask, and
    the claim failure behind it met a stage with nothing left. The document
    allowance is spent here and the whole per-claim ceiling is still available
    behind it.
    """
    inference = ScriptedInference({"vol/epsilon.md": (_COULD_NOT_BE_READ, _STANDS)})
    found = identify.infer_claims(_reading(declared, "vol/epsilon.md"), _identifier(inference))

    assert [claim.title for claim in found.claims] == [_CLOSURE_TITLE]
    assert len(inference.prompts) == 2
    assert "never closed" in inference.prompts[1].split(_RETRY_HEADING)[1]
    assert identify.CALL_BUDGET == 1 + identify.ANSWER_RETRY_BUDGET + identify.REASK_CEILING


# ---------------------------------------------------------------------------
# 7 — the run end to end
# ---------------------------------------------------------------------------


@pytest.fixture
def discovered(declared: Path) -> Path:
    inference = FakeInference()
    report = _discover(declared, inference)
    assert not report.failed, report.lines()
    assert len(inference.prompts) == 2
    return declared


def test_the_run_exits_zero_and_the_runner_s_gates_are_green(discovered: Path):
    kb = discovered / "kb-root"
    assert refresh_kb_metadata.main(["--kb-root", str(kb)]) == 0
    assert verify_kb_metadata.main(["--kb-root", str(kb)]) == 0


def test_no_document_of_a_declaring_kind_still_awaits(discovered: Path):
    state = conform.pass_two_gate(tree.read(discovered / "kb-root"))
    assert state.awaiting == ()
    assert sorted(state.hosting) == ["vol/alpha.md", "vol/epsilon.md"]
    assert state.determined == ("vol/zeta.md",)


def test_no_number_is_authored(discovered: Path):
    register = (discovered / "kb-root" / "vol" / "claim-quality.md").read_text(encoding="utf-8")
    assert register.count(f"- confidence: {kb_schema.PENDING_LITERAL}") == 3
    assert all(entry.confidence is None for entry in _entries(discovered))
    # At the authoring boundary rather than after refresh: the only rigor this
    # stage ever hands the write API is the pending literal.
    values = tomllib.loads(
        (_scratch(discovered) / "cinf-vol_epsilon.md-1-insert-claim-entry.toml").read_text(encoding="utf-8")
    )
    assert [entry["rigor"] for entry in values["entry"]] == [kb_schema.PENDING_LITERAL] * 2
    assert all(not isinstance(value, (int, float)) for entry in values["entry"] for value in entry.values())


def test_every_minted_slice_locates_exactly_once_and_outside_every_exclusion(discovered: Path):
    """Acceptance re-run over the written tree, against a fresh inventory scan.

    Over the marker-stripped bytes, which is what the check was made against:
    the run appends a marker to the first line of each span.
    """
    documents = tree.read(discovered / "kb-root")
    sites = inventory.scan(documents)
    authored = graph.read(documents, sites)
    prose = [node for node in authored.nodes.values() if node.document == "vol/epsilon.md"]
    assert len(prose) == 2

    text = tree.strip_markers(documents.documents["vol/epsilon.md"].text)
    fences = [fence for fence in sites.fences if fence.document == "vol/epsilon.md"]
    blocks = [block for block in sites.claim_blocks() if block.document == "vol/epsilon.md"]
    assert fences and not blocks
    for node in prose:
        lines = ops.excerpt_lines(text, node.locator)
        assert len(lines) == 1
        line = lines[0]
        assert line != 0
        assert not any(fence.start <= line < fence.end for fence in fences)
        assert not any(block.start <= line < block.end for block in blocks)


def test_every_minted_claim_carries_a_marker_the_graph_recovers_its_locator_from(discovered: Path):
    documents = tree.read(discovered / "kb-root")
    authored = graph.read(documents, inventory.scan(documents))
    text = documents.documents["vol/epsilon.md"].text
    lines = text.splitlines()
    prose = {node.id: node for node in authored.nodes.values() if node.document == "vol/epsilon.md"}
    assert len(prose) == 2

    # Every id is marked in the body, and `graph.read` filled its locator from
    # that marker's own line — which is the line its span located to.
    def marked_line(node_id: str) -> int:
        carrying = [number for number, line in enumerate(lines) if render.render_tier2_marker(node_id) in line]
        assert len(carrying) == 1
        return carrying[0]

    for node_id, node in prose.items():
        assert node.locator == graph.marker_locator(text, node_id)
        assert node.locator == tree.strip_markers(lines[marked_line(node_id)]).strip()

    # Each marker sits on the line its own span located to, which is what makes
    # the recovered locator the place a later stage can go and look at. Matched
    # over the marker-stripped text: a marker is appended to the FIRST line of
    # the span, so where that span crosses a hard wrap the marker lands inside it
    # and the slice no longer matches the written bytes. Stripping restores the
    # bytes the check was made against and leaves the line count intact.
    unmarked = tree.strip_markers(text)
    cut = [_slice(discovered, locator) for locator in (_CLOSURE_EXTENT, _UNIQUENESS_EXTENT)]
    assert sorted(ops.excerpt_lines(unmarked, excerpt)[0] for excerpt in cut) == sorted(
        marked_line(node_id) for node_id in prose
    )


def test_block_coverage_is_untouched(discovered: Path):
    """No C-inf span sits inside a claim-bearing block, and every block is still named once."""
    documents = tree.read(discovered / "kb-root")
    sites = inventory.scan(documents)
    claims = identify.block_claims(sites)
    identify.check_block_coverage(claims, sites)
    assert len(claims) == 1


def test_body_preservation_holds_over_the_discovery_run(declared: Path):
    """Every line added belongs to a frontmatter block; every line rewritten gained a marker."""
    before = _texts(declared / "kb-root")
    assert not _discover(declared, FakeInference()).failed
    after = _texts(declared / "kb-root")

    # Every line the run added or rewrote outside a frontmatter block, in both
    # directions, so a pair a marker relates is matched whole rather than by
    # position. The frontmatter block is excluded because it is what the run is
    # entitled to write; the body is what it may not move.
    for path, old in before.items():
        new = after[path]
        lost = Counter(kb_index_lib.strip_frontmatter(old).splitlines())
        lost -= Counter(kb_index_lib.strip_frontmatter(new).splitlines())
        gained = Counter(kb_index_lib.strip_frontmatter(new).splitlines())
        gained -= Counter(kb_index_lib.strip_frontmatter(old).splitlines())

        marked = Counter()
        for line, count in gained.items():
            stripped = tree.strip_markers(line)
            assert stripped != line, f"{path}: {line!r} is prose this run added"
            marked[stripped] += count
        assert marked == lost, path


def test_a_re_run_mints_nothing_and_leaves_the_tree_byte_identical(discovered: Path):
    before = _texts(discovered / "kb-root")
    registers = {
        path.name: path.read_text(encoding="utf-8") for path in (discovered / "kb-root").rglob("claim-quality.md")
    }
    inference = FakeInference()
    report = _discover(discovered, inference)

    assert not report.failed, report.lines()
    assert inference.prompts == [], "a determined document is not re-read"
    assert _texts(discovered / "kb-root") == before
    assert {p.name: p.read_text(encoding="utf-8") for p in (discovered / "kb-root").rglob("claim-quality.md")} == (
        registers
    )


def test_the_write_op_composes_every_metadata_byte(discovered: Path):
    """The values files carry titles, slices and ids; no marker or bullet is spelled here."""
    values = sorted(_scratch(discovered).glob("cinf-*.toml"))
    assert [path.name for path in values] == [
        "cinf-vol_epsilon.md-1-insert-claim-entry.toml",
        "cinf-vol_epsilon.md-2-set-frontmatter.toml",
        "cinf-vol_epsilon.md-4-mark-claim-in-leaf.toml",
        "cinf-vol_zeta.md-2-set-frontmatter.toml",
    ]
    marks = values[2].read_text(encoding="utf-8")
    assert _slice(discovered, _CLOSURE_EXTENT) in marks and "<!--" not in marks


def test_an_identification_carrying_neither_or_both_is_refused_before_a_write(discovered: Path):
    for wrong in (
        identify.Identification(document="vol/zeta.md"),
        identify.Identification(
            document="vol/zeta.md",
            claims=(identify.ProseClaim(document="vol/zeta.md", title="T", excerpt="e", line=4, locator="S1"),),
            no_claim="and a reason",
        ),
    ):
        with pytest.raises(write.WriteError) as refusal:
            write.write_claims(wrong, kind="leaf", kb_root=discovered / "kb-root", scratch=_scratch(discovered))
        assert refusal.value.check == "identification"
