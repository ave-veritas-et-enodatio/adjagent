"""The discovered pass: its entry condition, and dependency attribution end to end.

**Every test here runs against a fake inference.** The seam is
:class:`kb_tools.kb_claimgraph.ask.SeatAsk` — a seat and a prompt in, response
text and an `Outcome` out — and the fake reads the prompt the stage actually
composed and answers from a fixed table. So the ask, the reply parse, both
transport failure paths, the candidate-membership check, the acyclicity check
and the write are all exercised, and none of it needs a model to be reachable: a
check that cannot run without one is a check that does not run.

The consuming repository is stood up the way a consumer's is: the real runner
snippet, imported by the line the installer writes, over the installed package.
That is what makes the last stage's targets the real ones rather than a
justfile this file invented.
"""

import os
import re
import shutil
from collections import Counter
from pathlib import Path

import pytest

from kb_tools import kb_index_lib, kb_util, refresh_kb_metadata, verify_kb_metadata
from kb_tools.inference import Outcome
from kb_tools.kb_claimgraph import __main__ as cli
from kb_tools.kb_claimgraph import ask, attribute, conform, depends, graph, inventory, report, tree, write
from kb_tools.kb_claimgraph.assemble import UNSCANNED_REASON
from kb_tools.kb_claimgraph.build import build
from kb_tools.kb_claimgraph.report import AnswerFormatError

_PACKAGE_ROOT = Path(kb_util.__file__).resolve().parent

# The last stage reaches the KB through the consuming project's runner targets,
# which is the mechanism and not a detail this file may route around.
pytestmark = pytest.mark.skipif(
    shutil.which("just") is None,
    reason="the discovered pass's last stage runs the consuming project's runner targets",
)


# ---------------------------------------------------------------------------
# A five-leaf corpus: one claim each in three leaves, two in a fourth, none in
# the fifth, and cross-references arranged so every rule this stage applies is
# exercised — a proof bound to its claim by adjacency and another by the result
# its opening run names, an eqref into an equation inside a claim body, a
# section reference into a single-claim document and another into a two-claim
# one, and a reference inside a proof that resolves to no claim at all.
#
# Every anchor carries all three of the attributes SPEC.md's cross-reference
# join states, in that order, because that is what a rewritten reference is.
# ---------------------------------------------------------------------------

_ENTRY_POINT = "# Knowledge Base\n\n- [Vol](vol/index.md)\n"
_VOLUME_INDEX = (
    "[↑ Knowledge Base](../entry-point.md)\n\n# Vol\n\n"
    "- [Alpha](alpha.md)\n- [Beta](beta.md)\n- [Gamma](gamma.md)\n- [Delta](delta.md)\n"
    "- [Epsilon](epsilon.md)\n"
)
_UPLINK = "[↑ Vol](index.md)"

_ALPHA = f"""{_UPLINK}

# Alpha

> <span id="thm:alpha">**theorem**</span>
>
> **Theorem 1** (Alpha result). *Alpha holds for every admissible state.*
>
> ``` math
> \\begin{{equation}}
> \\label{{eq:one}}
>  a = b
> \\end{{equation}}
> ```

Alpha is argued from
<a href="beta.md#thm:beta" data-reference-type="ref" data-reference="thm:beta">Lemma 2</a>.
"""

_BETA = f"""{_UPLINK}

# Beta

> <span id="thm:beta">**lemma**</span>
>
> **Lemma 2** (Beta lemma). *Beta holds on the interior.*

> **proof**
>
> *Proof.* Beta rests on
> <a href="gamma.md#thm:g2" data-reference-type="ref" data-reference="thm:g2">Lemma 1</a> and on
> <a href="epsilon.md#epsilon" data-reference-type="ref" data-reference="sec:epsilon">Section 5</a>,
> which states no result of its own. ◻

Beta is read beside
<a href="gamma.md#thm:g1" data-reference-type="ref" data-reference="thm:g1">Theorem 1</a>.
"""

_GAMMA = f"""{_UPLINK}

# Gamma

> <span id="thm:g1">**theorem**</span>
>
> **Theorem 1** (Gamma one). *The first gamma result.*

> <span id="thm:g2">**lemma**</span>
>
> **Lemma 1** (Gamma two). *The second gamma result.*

> **proof**
>
> *Proof of
> <a href="gamma.md#thm:g1" data-reference-type="ref" data-reference="thm:g1">Theorem 1</a>.*
> It follows from equation
> <a href="alpha.md" data-reference-type="eqref" data-reference="eq:one">1</a>. ◻

Both are read beside
<a href="alpha.md#thm:alpha" data-reference-type="ref" data-reference="thm:alpha">Theorem 1</a>.
"""

_DELTA = f"""{_UPLINK}

# Delta

> <span id="thm:delta">**proposition**</span>
>
> **Proposition 1** (Delta result). *Delta stands on its own.*

Delta names no result of anyone else's. It points at two whole sections —
<a href="alpha.md#alpha" data-reference-type="ref" data-reference="sec:alpha">Section 1</a>,
which hosts one claim and can therefore mean no other, and
<a href="gamma.md#gamma" data-reference-type="ref" data-reference="sec:gamma">Section 3</a>,
which hosts two and stays ambiguous.
"""

_EPSILON = f"""{_UPLINK}

# Epsilon

The author marked no result here, so the declared pass writes the unscanned
reason and the discovered pass is what would read it.
"""

_TREE = {
    "entry-point.md": _ENTRY_POINT,
    "vol/index.md": _VOLUME_INDEX,
    "vol/alpha.md": _ALPHA,
    "vol/beta.md": _BETA,
    "vol/gamma.md": _GAMMA,
    "vol/delta.md": _DELTA,
    "vol/epsilon.md": _EPSILON,
}


# ---------------------------------------------------------------------------
# The fake inference
# ---------------------------------------------------------------------------

#: The shape :func:`ask.compose_prompt` writes one claim on. Read rather than
#: assumed: a fake that guessed the prompt's layout would keep answering after
#: the ask changed under it.
_CLAIM_LINE_RE = re.compile(r"^- `(clm-[a-z0-9]+)` — (.+) \(stated in `", re.MULTILINE)

_RETRY_HEADING = "## A previous answer failed a mechanical check"

#: The malformation a live run stopped on, over on C-inf's side: one prose block
#: whose two markers carry different numbers. Stage D composes no prose at all,
#: so beside its answer this is both a block nothing names and a block that does
#: not close — and either way, what fails is the parse.
_MISMATCHED_PROSE_BLOCK = f"<<<{ask.PROSE_NAME} 2\nAn afterthought\n{ask.PROSE_NAME} 3\n"


def _read_prompt(prompt: str) -> tuple[tuple[str, str], list[tuple[str, str]]]:
    """The prompt's source claim and its candidates, each as ``(id, title)``."""
    claims = _CLAIM_LINE_RE.findall(prompt)
    return claims[0], claims[1:]


class FakeInference:
    """An :class:`ask.SeatAsk` answering from a fixed table keyed by claim title.

    A table value is a title — resolved against the candidates the prompt
    offered — or a literal id, which is how an answer outside the candidate set
    is expressed without the fake needing to know what the check does with it.

    ``outcome`` is what the layer below would have said about the call, which is
    how the two transport failure paths are exercised without a subprocess.
    ``trailing`` and ``on_retry_trailing`` are text returned beside the answer on
    the ask and on the re-ask — which is how an answer that arrives and does not
    parse is expressed without hand-composing the block that does.
    """

    def __init__(
        self,
        answers: dict[str, tuple[str, ...]],
        *,
        on_retry: dict[str, tuple[str, ...]] | None = None,
        outcome: Outcome = Outcome.OK,
        trailing: str = "",
        on_retry_trailing: str = "",
    ):
        self._answers = answers
        self._on_retry = on_retry
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
        (source_id, source_title), candidates = _read_prompt(prompt)
        retrying = _RETRY_HEADING in prompt
        table = self._on_retry if (retrying and self._on_retry is not None) else self._answers
        by_title = {title: node_id for node_id, title in candidates}
        chosen = [by_title.get(wanted, wanted) for wanted in table.get(source_title, ())]
        trailing = self._on_retry_trailing if retrying else self._trailing
        return ask.answer_block(source_id, chosen) + trailing, self._outcome


#: One scripted call: what to select — a candidate title or a literal id, on the
#: same terms as the table above — and text returned beside the answer, a
#: mismatched prose block being how an answer that does not parse is expressed.
_Turn = tuple[tuple[str, ...], str]


class ScriptedInference:
    """An :class:`ask.SeatAsk` answering one scripted turn per call, keyed by source claim.

    :class:`FakeInference` answers one thing on the ask and another on every
    re-ask, which cannot express a source whose first two answers fail
    *different* classes — and which class each answer failed is the whole of
    what a sequence is about. A source with no script selects nothing, which is
    a complete answer; a call past the last scripted turn is the call bound
    broken, and fails here rather than looping.
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
        (source_id, source_title), candidates = _read_prompt(prompt)
        turns = self._script.get(source_title, ())
        if not turns:
            return ask.answer_block(source_id, ()), Outcome.OK
        assert self._taken[source_title] < len(turns), f"{source_title}: a call past the last scripted turn"
        wanted, trailing = turns[self._taken[source_title]]
        self._taken[source_title] += 1
        by_title = {title: node_id for node_id, title in candidates}
        return ask.answer_block(source_id, [by_title.get(name, name) for name in wanted]) + trailing, Outcome.OK


# ---------------------------------------------------------------------------
# The consuming repository, and the declared pass over it
# ---------------------------------------------------------------------------


@pytest.fixture
def consumer(tmp_path: Path) -> Path:
    """A repo carrying the tree, the installed package and the real runner targets."""
    repo = tmp_path / "consumer"
    for relative, text in _TREE.items():
        target = repo / "kb-root" / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")
    # The runner targets resolve the repository root the way every tool in this
    # toolchain does — a .git beside a kb-root/ — so the marker is what makes
    # this a repository to them, not a convenience of the fixture.
    (repo / ".git").mkdir()
    (repo / "justfile").write_text("default:\n    @true\n", encoding="utf-8")
    installed = repo / ".claude" / "agents"
    installed.mkdir(parents=True)
    os.symlink(_PACKAGE_ROOT, installed / _PACKAGE_ROOT.name)
    kb_util.install_targets(repo, "just")
    return repo


def _scratch(repo: Path) -> Path:
    return repo / kb_util.SCRATCH_DIRNAME / "claimgraph"


def test_a_build_completes_over_a_document_carrying_an_unknown_environment(consumer: Path) -> None:
    """The property a sweep over an unfamiliar corpus depends on: a build, not a stop.

    Driven through the whole declared pass rather than through stage B alone,
    because what an unclassified name used to cost was the *build*: every stage
    after the inventory has to accept a block nobody classified, and the gates
    at the end have to stay green over the KB it produced.

    ``Setup`` is a name of that shape and a measured one — one paper of the
    35-paper arXiv survey declares ``\newtheorem{setup}{Setup}``, and it sits in
    the survey's residue of one-offs that no table admits.
    """
    unfamiliar = consumer / "kb-root" / "vol" / "epsilon.md"
    unfamiliar.write_text(
        unfamiliar.read_text(encoding="utf-8")
        + "\n> **Setup**\n>\n> **Setup 1** (Bounded arrivals). *Arrivals are bounded.*\n",
        encoding="utf-8",
    )

    report = build(kb_root=consumer / "kb-root", repo_root=consumer, scratch=_scratch(consumer))

    assert not report.failed, "\n".join(report.lines())
    census = [line for line in report.lines() if "stage-B-unclassified" in line]
    assert census and "Setup=1" in census[0], report.lines()
    # Not claim-bearing, so it mints nothing and leaves the document awaiting —
    # the omission the census line is reporting, and the state a later pass with
    # the name classified would find.
    assert "clm-" not in unfamiliar.read_text(encoding="utf-8")


def test_the_calibrated_corpus_reports_no_unclassified_name(consumer: Path) -> None:
    """The line stays quiet where every name is classified, so it means something when it is not."""
    report = build(kb_root=consumer / "kb-root", repo_root=consumer, scratch=_scratch(consumer))

    census = [line for line in report.lines() if "stage-B-unclassified" in line]
    assert census and "none" in census[0], report.lines()


@pytest.fixture
def declared(consumer: Path) -> Path:
    """``consumer`` after the declared pass has run and its gates are green."""
    outcome = build(kb_root=consumer / "kb-root", repo_root=consumer, scratch=_scratch(consumer))
    assert not outcome.failed, outcome.lines()
    return consumer


def _selector(inference: ask.SeatAsk, *, cwd: Path = Path(".")) -> ask.ModelSelector:
    return ask.ModelSelector(cwd=cwd, ask=inference)


def _read(repo: Path):
    documents = tree.read(repo / "kb-root")
    sites = inventory.scan(documents)
    return documents, sites, graph.read(documents, sites)


def _titles(authored: graph.AuthoredGraph) -> dict[str, str]:
    return {node.title: node.id for node in authored.nodes.values()}


def _register_edges(repo: Path) -> set[tuple[str, str]]:
    """The edges on disk, read back through the production parser."""
    entries = kb_index_lib.parse_claim_quality_file(repo / "kb-root" / "vol" / "claim-quality.md", repo / "kb-root")
    return {(entry.id, edge.target) for entry in entries for edge in entry.depends_on}


# ---------------------------------------------------------------------------
# 1 — the entry condition
# ---------------------------------------------------------------------------


def test_the_entry_condition_accepts_the_declared_pass_s_own_output(declared: Path):
    """The tree pass 1 wrote is exactly what pass 2 must admit — frontmatter and all."""
    state = conform.pass_two_gate(tree.read(declared / "kb-root"))
    assert sorted(state.hosting) == ["vol/alpha.md", "vol/beta.md", "vol/delta.md", "vol/gamma.md"]
    assert state.awaiting == ("vol/epsilon.md",)
    assert state.determined == ()


def test_the_declared_pass_s_own_gate_refuses_that_same_tree(declared: Path):
    """The two guards are different questions, and only the first one is about the tree."""
    with pytest.raises(conform.ConformanceError) as refusal:
        conform.gate(tree.read(declared / "kb-root"))
    assert refusal.value.check == "point-14"


def test_a_document_carrying_an_authored_determination_is_refused(declared: Path):
    """A reason somebody wrote is a finding; the unscanned reason is not, and only it resumes."""
    leaf = declared / "kb-root" / "vol" / "epsilon.md"
    text = leaf.read_text(encoding="utf-8")
    assert UNSCANNED_REASON in text
    leaf.write_text(text.replace(UNSCANNED_REASON, "This section restates a result proved in Alpha."), encoding="utf-8")

    state = conform.pass_two_gate(tree.read(declared / "kb-root"))
    assert state.determined == ("vol/epsilon.md",)
    assert state.awaiting == ()


@pytest.mark.parametrize(
    "fields, verdict",
    [
        ({"claims": ["clm-aaaaaa"]}, conform.Determination.HOSTS_CLAIMS),
        ({"no-claim": UNSCANNED_REASON}, conform.Determination.AWAITING),
        ({"no-claim": "The section is a bibliography."}, conform.Determination.AUTHORED_NO_CLAIM),
        ({}, conform.Determination.UNDECLARED),
        ({"no-claim": ""}, conform.Determination.UNDECLARED),
    ],
)
def test_the_determination_predicate_is_the_unscanned_reason_by_identity(fields, verdict):
    assert conform.determination(fields) is verdict


def test_the_entry_condition_refuses_a_tree_the_declared_pass_has_not_run_over(consumer: Path):
    with pytest.raises(conform.ConformanceError) as refusal:
        conform.pass_two_gate(tree.read(consumer / "kb-root"))
    assert refusal.value.check == "frontmatter-absent"


# ---------------------------------------------------------------------------
# 2 — the mechanical narrowing
# ---------------------------------------------------------------------------


def _narrowed(declared: Path):
    documents, sites, authored = _read(declared)
    return _titles(authored), attribute.narrow(documents, authored, sites)


def _pairs(narrowed: attribute.Attribution) -> set[tuple[str, str]]:
    return {(question.source.id, candidate.id) for question in narrowed.questions for candidate in question.candidates}


def _settled(ids: dict[str, str]) -> set[tuple[str, str]]:
    """The two edges the fixture's proofs settle with no model asked, per the tests below."""
    return {(ids["Beta lemma"], ids["Gamma two"]), (ids["Gamma one"], ids["Alpha result"])}


def test_the_candidate_set_is_narrowed_the_way_the_plan_narrows_it(declared: Path):
    ids, narrowed = _narrowed(declared)

    assert _pairs(narrowed) == {
        # a single-claim document's prose reference, resolved at both ends —
        # neither of which says which way an edge between them runs
        (ids["Alpha result"], ids["Beta lemma"]),
        (ids["Beta lemma"], ids["Gamma one"]),
        # a two-claim document leaves the SOURCE open; Gamma one's own pair with
        # Alpha is settled below and is therefore not also asked about
        (ids["Gamma two"], ids["Alpha result"]),
        # a section reference into a single-claim document names that claim, and
        # one into a two-claim document offers both
        (ids["Delta result"], ids["Alpha result"]),
        (ids["Delta result"], ids["Gamma one"]),
        (ids["Delta result"], ids["Gamma two"]),
    }


def test_containment_settles_the_edges_a_proof_directs_and_no_model_is_asked(declared: Path):
    """Both arms of the proof-to-claim binding, and the route each target resolved by.

    Beta's proof carries no opening argument, so it binds to the block directly
    above it; Gamma's names its own theorem in its opening run, which is what
    lets a proof bind across a block it does not follow.
    """
    ids, narrowed = _narrowed(declared)

    assert set(narrowed.edges) == {
        (ids["Beta lemma"], ids["Gamma two"]),
        (ids["Gamma one"], ids["Alpha result"]),
    }
    assert narrowed.routes == {attribute.BY_IDENTIFIER: 1, attribute.BY_EQUATION: 1}


def test_an_eqref_into_an_equation_inside_a_claim_body_names_that_claim(declared: Path):
    """The equation is no node; the block it is labelled inside states the claim it asserts.

    Asserted through the anchor as well, so a fixture that stopped carrying the
    ``eqref`` — whose fragment is empty, the label riding the third attribute —
    fails here rather than passing vacuously.
    """
    documents, sites, authored = _read(declared)
    ids = _titles(authored)
    equation = [anchor for anchor in sites.anchors if anchor.reference_type == attribute.EQUATION_REFERENCE_TYPE]
    assert [(anchor.fragment, anchor.label, anchor.target) for anchor in equation] == [("", "eq:one", "vol/alpha.md")]

    narrowed = attribute.narrow(documents, authored, sites)
    assert (ids["Gamma one"], ids["Alpha result"]) in narrowed.edges


def test_a_reference_inside_a_proof_that_resolves_to_no_claim_contributes_nothing(declared: Path):
    """Beta's proof names two documents; only the one hosting a claim yields an edge.

    A reference is a dependency only where there is something to depend on, and
    a document the author marked no result in is that case rather than a defect.
    """
    documents, sites, authored = _read(declared)
    ids = _titles(authored)
    dangling = [
        (anchor.document, anchor.hosting_environment) for anchor in sites.anchors if anchor.target == "vol/epsilon.md"
    ]
    assert dangling == [("vol/beta.md", "proof")], "the fixture stopped carrying the reference this is about"

    narrowed = attribute.narrow(documents, authored, sites)
    assert {target for source, target in narrowed.edges if source == ids["Beta lemma"]} == {ids["Gamma two"]}


def test_the_anchor_naming_what_a_proof_proves_is_not_read_as_a_dependency(declared: Path):
    """Gamma's proof opens by naming Theorem 1; that reference is its subject, not its warrant."""
    ids, narrowed = _narrowed(declared)
    reached = set(narrowed.edges) | _pairs(narrowed)
    assert (ids["Gamma one"], ids["Gamma one"]) not in reached
    assert (ids["Gamma two"], ids["Gamma one"]) not in reached


def test_a_fragment_naming_a_block_resolves_the_target_to_that_block_s_claim(declared: Path):
    """gamma.md hosts two claims; beta's prose reference names one of them by its source label."""
    ids, narrowed = _narrowed(declared)
    asked = {question.source.id: question for question in narrowed.questions}
    assert asked[ids["Beta lemma"]].offered() == {ids["Gamma one"]}


def test_a_section_reference_resolves_only_where_the_target_hosts_one_claim(declared: Path):
    """Delta's two section references: one document with a single claim, one with two."""
    ids, narrowed = _narrowed(declared)
    asked = {question.source.id: question for question in narrowed.questions}
    assert asked[ids["Delta result"]].offered() == {
        ids["Alpha result"],
        ids["Gamma one"],
        ids["Gamma two"],
    }


def test_edges_containment_settles_are_never_also_asked_about(declared: Path):
    """A pair another anchor already decided is not offered as a candidate for it."""
    ids, narrowed = _narrowed(declared)
    assert (ids["Gamma one"], ids["Alpha result"]) in narrowed.edges
    assert (ids["Gamma one"], ids["Alpha result"]) not in _pairs(narrowed)
    assert ids["Gamma one"] not in {question.source.id for question in narrowed.questions}


# ---------------------------------------------------------------------------
# 3 — the two mechanical checks
# ---------------------------------------------------------------------------


def test_the_acyclicity_check_rejects_a_planted_cycle(declared: Path):
    _, _, authored = _read(declared)
    ids = _titles(authored)
    ring = [
        (ids["Alpha result"], ids["Beta lemma"]),
        (ids["Beta lemma"], ids["Gamma one"]),
        (ids["Gamma one"], ids["Alpha result"]),
    ]
    cycle = attribute.check_acyclic(authored, ring)
    assert cycle and cycle[0] == cycle[-1]
    assert set(cycle) == {ids["Alpha result"], ids["Beta lemma"], ids["Gamma one"]}
    assert attribute.check_acyclic(authored, ring[:2]) == ()


def test_a_selection_closing_a_cycle_costs_one_re_ask_and_then_stops(declared: Path):
    """The check runs over the whole set, so a selection closing on a settled edge is caught.

    Gamma one's dependency on Alpha is the one Gamma's proof settled; the two
    selections below close a ring through it, which is a cycle no re-ask of
    Gamma one could break — it was never asked. What is re-asked is the claims
    on the ring that *were*.
    """
    documents, sites, authored = _read(declared)
    closing = {"Alpha result": ("Beta lemma",), "Beta lemma": ("Gamma one",)}
    unrepentant = FakeInference(closing, on_retry=closing)
    with pytest.raises(attribute.AttributionError) as refusal:
        attribute.attribute_dependencies(documents, authored, sites, _selector(unrepentant))
    assert refusal.value.check == "acyclicity"

    relenting = FakeInference(closing, on_retry={**closing, "Alpha result": ()})
    edges = attribute.attribute_dependencies(documents, authored, sites, _selector(relenting))
    ids = _titles(authored)
    assert set(edges) == _settled(ids) | {(ids["Beta lemma"], ids["Gamma one"])}
    assert _RETRY_HEADING in relenting.prompts[-1]


def _plant_a_mutual_proof_cycle(declared: Path) -> None:
    """Alpha's proof rests on Beta and Beta's on Alpha: a ring of settled edges alone.

    The tree is edited rather than fixtured because a corpus this shape is the
    pathology, not the norm.
    """
    kb = declared / "kb-root" / "vol"
    proof_of = (
        '\n> **proof**\n>\n> *Proof of <a href="{proved}" data-reference-type="ref" '
        'data-reference="{proved_label}">1</a>.* It rests on '
        '<a href="{rests_on}" data-reference-type="ref" data-reference="{rests_label}">2</a>. ◻\n'
    )
    for leaf, proved, rests_on in (("alpha.md", "thm:alpha", "thm:beta"), ("beta.md", "thm:beta", "thm:alpha")):
        other = "beta.md" if leaf == "alpha.md" else "alpha.md"
        (kb / leaf).write_text(
            (kb / leaf).read_text(encoding="utf-8")
            + proof_of.format(
                proved=f"{leaf}#{proved}", proved_label=proved, rests_on=f"{other}#{rests_on}", rests_label=rests_on
            ),
            encoding="utf-8",
        )


@pytest.mark.parametrize(
    "edges, demoted",
    [
        # A chain settles nothing; nothing is on a ring.
        ((("a", "b"), ("b", "c")), ()),
        # The smallest ring: both members go, because nothing separates them.
        ((("a", "b"), ("b", "a")), (("a", "b"), ("b", "a"))),
        # A longer ring goes whole, and the edge entering it from outside stays:
        # it lies on no cycle and the ring says nothing about it.
        ((("x", "a"), ("a", "b"), ("b", "c"), ("c", "a")), (("a", "b"), ("b", "c"), ("c", "a"))),
        # A chord is on a ring of its own and goes with them.
        ((("a", "b"), ("b", "c"), ("c", "a"), ("a", "c")), (("a", "b"), ("a", "c"), ("b", "c"), ("c", "a"))),
        # Two rings sharing no edge; each goes, and the bridge between them
        # stays, being on neither.
        (
            (("a", "b"), ("b", "a"), ("b", "c"), ("c", "d"), ("d", "c")),
            (("a", "b"), ("b", "a"), ("c", "d"), ("d", "c")),
        ),
    ],
)
def test_every_edge_on_a_ring_is_demoted_and_no_other_edge_is(edges, demoted):
    """The rule is *on a cycle*, not a feedback set: a ring goes whole, its surroundings stand."""
    assert attribute.cycle_edges(edges) == demoted
    kept = [edge for edge in edges if edge not in demoted]
    assert attribute.cycle_edges(kept) == (), "what remains carries no cycle"


def test_the_demoted_set_is_a_property_of_the_edges_and_not_of_their_order():
    """Two runs over one corpus demote the same edges — no tie-break key to agree on."""
    edges = [("a", "b"), ("b", "c"), ("c", "a"), ("x", "a"), ("b", "y"), ("y", "b")]
    forward = attribute.cycle_edges(edges)

    assert forward == attribute.cycle_edges(list(reversed(edges)))
    assert forward == attribute.cycle_edges(sorted(edges, key=lambda edge: edge[1]))


def test_a_ring_among_the_settled_edges_is_demoted_rather_than_stopping_the_stage(declared: Path):
    """Two proofs each proving what the other rests on: the ring cannot all be dependencies.

    That is not proof the corpus reasons circularly, and it says nothing about
    the paper's other edges — so the ring's edges become references, the rest
    stand, and nobody is asked about any of it.
    """
    _plant_a_mutual_proof_cycle(declared)

    documents, sites, authored = _read(declared)
    ids = _titles(authored)
    ring = {(ids["Alpha result"], ids["Beta lemma"]), (ids["Beta lemma"], ids["Alpha result"])}
    narrowed = attribute.narrow(documents, authored, sites)

    assert set(narrowed.demoted) == ring
    assert set(narrowed.edges) == _settled(ids), "the edges off the ring are untouched"
    assert ring <= set(narrowed.references)
    assert attribute.check_acyclic(authored, narrowed.edges) == ()

    # The paper's open pairs are still put to the model; the ring's edges are
    # not among them. What the ring refuses is the set, not containment's
    # reading of any one member, so there is nothing to ask about them.
    assert ring.isdisjoint(_pairs(narrowed))
    edges = attribute.attribute_dependencies(documents, authored, sites, _selector(FakeInference({})))
    assert set(edges) == _settled(ids)


def test_the_correction_lands_beneath_the_ask_and_the_cycle_re_ask_is_its_own_template(declared: Path):
    """The two seams stage D's templates carry, checked as bytes rather than as substrings.

    The correction is a fragment spliced into a slot inside a line, so the file
    itself carries no trailing newline and the template supplies the breaks
    around it. Nothing about a ``.tmpl`` says so, and an editor adding the
    customary final newline moves every prompt by one blank line.
    """
    documents, sites, authored = _read(declared)
    question = attribute.narrow(documents, authored, sites).questions[0]
    first = ask.compose_prompt(question, report=None)

    assert first.endswith("key is admitted.\n")
    assert ask.compose_prompt(question, report="two ids were not offered") == first.rstrip("\n") + (
        "\n\n## A previous answer failed a mechanical check\n\ntwo ids were not offered\n"
    )
    # A whole ask of its own — the claim and its candidates are in it, not just
    # the constraint — and it is not held to the ask above beyond that: the
    # template exists so that what it asks may differ.
    cycle = ask.compose_cycle_prompt(question, cycle="clm-a -> clm-b -> clm-a")
    assert f"- `{question.candidates[0].id}` — " in cycle
    assert cycle.endswith(
        f"\n\n{_RETRY_HEADING}\n\nThe selections returned so far close a dependency cycle: "
        f"clm-a -> clm-b -> clm-a. A claim graph must be acyclic. Re-answer for {question.source.id} "
        f"without the selection that closes it.\n"
    )


def test_an_id_outside_the_candidate_set_costs_one_re_ask_and_then_stops(declared: Path):
    documents, sites, authored = _read(declared)
    inventing = FakeInference({"Alpha result": ("clm-zzzzzz",)})
    with pytest.raises(attribute.AttributionError) as refusal:
        attribute.attribute_dependencies(documents, authored, sites, _selector(inventing))
    assert refusal.value.check == "candidate-membership"
    asked_twice = [prompt for prompt in inventing.prompts if _read_prompt(prompt)[0][1] == "Alpha result"]
    assert len(asked_twice) == 2 and _RETRY_HEADING in asked_twice[1]


def test_an_answer_for_another_claim_is_refused_by_the_parser():
    with pytest.raises(AnswerFormatError) as refusal:
        ask.parse_answer(ask.answer_block("clm-bbbbbb", []), claim="clm-aaaaaa")
    assert refusal.value.check == "answer-format"


def test_a_malformed_answer_costs_the_re_ask_and_the_selection_still_lands(declared: Path):
    """An answer that arrived is one the seat can be asked for again."""
    documents, sites, authored = _read(declared)
    inference = FakeInference({"Alpha result": ("Beta lemma",)}, trailing=_MISMATCHED_PROSE_BLOCK)

    edges = attribute.attribute_dependencies(documents, authored, sites, _selector(inference))

    ids = _titles(authored)
    assert set(edges) == _settled(ids) | {(ids["Alpha result"], ids["Beta lemma"])}
    alpha = [prompt for prompt in inference.prompts if _read_prompt(prompt)[0][1] == "Alpha result"]
    assert len(alpha) == 2 and _RETRY_HEADING in alpha[1]


def test_a_second_malformed_answer_stops_the_stage_naming_the_source_claim(declared: Path):
    documents, sites, authored = _read(declared)
    inference = FakeInference({}, trailing=_MISMATCHED_PROSE_BLOCK, on_retry_trailing=_MISMATCHED_PROSE_BLOCK)
    with pytest.raises(attribute.AttributionError) as refusal:
        attribute.attribute_dependencies(documents, authored, sites, _selector(inference))

    assert refusal.value.check == "answer-format"
    assert refusal.value.detail.startswith(_read_prompt(inference.prompts[0])[0][0])
    assert "Nothing was written" in refusal.value.detail
    assert len(inference.prompts) == 2


#: The three answers one source can give, in the classes they fail: one that did
#: not parse, one that parsed and named an id nobody offered, and one that stands.
_DID_NOT_PARSE: _Turn = ((), _MISMATCHED_PROSE_BLOCK)
_OUTSIDE_THE_SET: _Turn = (("clm-zzzzzz",), "")
_STANDS: _Turn = (("Beta lemma",), "")


def test_a_parse_failure_and_a_membership_failure_each_spend_their_own_allowance(declared: Path):
    """One shared budget spent on the parse left the check that never ran with none."""
    documents, sites, authored = _read(declared)
    inference = ScriptedInference({"Alpha result": (_DID_NOT_PARSE, _OUTSIDE_THE_SET, _STANDS)})

    edges = attribute.attribute_dependencies(documents, authored, sites, _selector(inference))

    ids = _titles(authored)
    assert set(edges) == _settled(ids) | {(ids["Alpha result"], ids["Beta lemma"])}
    alpha = [prompt for prompt in inference.prompts if _read_prompt(prompt)[0][1] == "Alpha result"]
    assert len(alpha) == attribute.CALL_BUDGET == 3
    assert "two markers carry one number" in alpha[1].split(_RETRY_HEADING)[1]
    assert "not in the candidate set" in alpha[2].split(_RETRY_HEADING)[1]


@pytest.mark.parametrize(
    "turns, check",
    [
        ((_DID_NOT_PARSE, _OUTSIDE_THE_SET, _DID_NOT_PARSE), "answer-format"),
        ((_OUTSIDE_THE_SET, _DID_NOT_PARSE, _OUTSIDE_THE_SET), "candidate-membership"),
    ],
)
def test_failures_alternating_between_the_two_classes_stop_at_the_call_bound(declared: Path, turns, check: str):
    """Two allowances of one are two calls, not a seat alternating its way past them."""
    documents, sites, authored = _read(declared)
    inference = ScriptedInference({"Alpha result": turns})

    with pytest.raises(attribute.AttributionError) as refusal:
        attribute.attribute_dependencies(documents, authored, sites, _selector(inference))

    assert refusal.value.check == check
    alpha = [prompt for prompt in inference.prompts if _read_prompt(prompt)[0][1] == "Alpha result"]
    assert len(alpha) == attribute.CALL_BUDGET == 3


@pytest.mark.parametrize(
    "block",
    [
        '<<<KB-CLAIMGRAPH-DEPENDS\n{"claim": "clm-aaaaaa"}\nKB-CLAIMGRAPH-DEPENDS\n',
        '<<<KB-CLAIMGRAPH-DEPENDS\n{"claim": "clm-aaaaaa", "depends-on": [], "why": "…"}\nKB-CLAIMGRAPH-DEPENDS\n',
        "there is no block here at all\n",
    ],
)
def test_the_answer_vocabulary_is_closed_and_total(block):
    """A key left out is refused, and so is a key added — including a verdict nobody asked for."""
    with pytest.raises(AnswerFormatError):
        ask.parse_answer(block, claim="clm-aaaaaa")


@pytest.mark.parametrize(
    "outcome, calls",
    [
        # The layer below retries nothing, so the bound is here: an outcome it
        # calls retryable is re-issued identically and then stops; one it calls
        # unretryable stops on the first return, because three identical
        # rejections of the same invocation are one fault reported three times.
        (Outcome.SILENCE, ask.TRANSPORT_ATTEMPTS),
        (Outcome.TIMEOUT, ask.TRANSPORT_ATTEMPTS),
        (Outcome.TRANSPORT_FAILURE, ask.TRANSPORT_ATTEMPTS),
        # The two the driver's own transport classified and this path did not:
        # a result the CLI marked errored is not an answer to validate, and a
        # command that is not runnable is an environment fault reported once.
        (Outcome.RESULT_ERROR, ask.TRANSPORT_ATTEMPTS),
        (Outcome.CLI_REJECTION, 1),
        (Outcome.SPAWN_FAILURE, 1),
    ],
)
def test_a_call_that_did_not_complete_stops_the_stage_on_the_layer_s_own_verdict(declared, outcome, calls):
    documents, sites, authored = _read(declared)
    failing = FakeInference({}, outcome=outcome)
    with pytest.raises(ask.AskError) as refusal:
        attribute.attribute_dependencies(documents, authored, sites, _selector(failing))
    assert refusal.value.check == "inference-failed"
    assert len(failing.prompts) == calls
    assert len(set(failing.prompts)) == 1, "a re-issue changes nothing about the ask"


def test_the_ask_names_one_seat_and_asks_it_everything(declared: Path):
    documents, sites, authored = _read(declared)
    inference = FakeInference({})
    attribute.attribute_dependencies(documents, authored, sites, _selector(inference))
    assert set(inference.seats) == {ask.SEAT}


# ---------------------------------------------------------------------------
# 4 — end to end
# ---------------------------------------------------------------------------


def test_the_discovered_pass_runs_end_to_end_against_a_fake_inference(declared: Path):
    """Pass 1's output in, edges authored through the write API, the runner's gates green."""
    inference = FakeInference(
        {
            "Alpha result": (),
            "Beta lemma": ("Gamma one",),
            "Gamma two": ("Alpha result",),
            "Delta result": (),
        }
    )
    report = depends.build(
        kb_root=declared / "kb-root",
        repo_root=declared,
        scratch=_scratch(declared),
        selector=_selector(inference),
    )
    assert not report.failed, report.lines()
    assert len(inference.prompts) == 4

    _, _, authored = _read(declared)
    ids = _titles(authored)
    # What the seat selected, and what containment settled without asking it —
    # both reaching the register as ordinary `depends` bullets, nothing in the
    # KB telling one from the other.
    assert _register_edges(declared) == _settled(ids) | {
        (ids["Beta lemma"], ids["Gamma one"]),
        (ids["Gamma two"], ids["Alpha result"]),
    }

    kb = declared / "kb-root"
    assert refresh_kb_metadata.main(["--kb-root", str(kb)]) == 0
    assert verify_kb_metadata.main(["--kb-root", str(kb)]) == 0


def test_a_run_stopping_at_the_entry_condition_writes_no_edge(consumer: Path):
    """The sequencer reports the stop and never reaches the ask or the write."""
    inference = FakeInference({})
    report = depends.build(
        kb_root=consumer / "kb-root",
        repo_root=consumer,
        scratch=_scratch(consumer),
        selector=_selector(inference),
    )
    assert report.failed
    assert any("frontmatter-absent" in line for line in report.lines())
    assert inference.prompts == []
    assert not (_scratch(consumer) / "3-add-depends-on.toml").exists()


def test_the_write_op_is_the_only_thing_that_composes_an_edge(declared: Path):
    """The values file carries ids; the bullet's bytes are the write API's."""
    _, _, authored = _read(declared)
    ids = _titles(authored)
    findings = write.write_edges(
        [(ids["Alpha result"], ids["Beta lemma"])], kb_root=declared / "kb-root", scratch=_scratch(declared)
    )
    assert [finding.status for finding in findings] == [report.PASS]
    values = (_scratch(declared) / "3-add-depends-on.toml").read_text(encoding="utf-8")
    assert "—" not in values and "depends-on" in values


# ---------------------------------------------------------------------------
# 5 — a run with no model reachable
# ---------------------------------------------------------------------------


def test_a_run_with_no_model_records_the_edges_containment_settled(declared: Path, monkeypatch, capsys):
    """Through the shipped command line, which is what a seat is told to run.

    The narrowing asks nobody, so its edges are the corpus's answer whether or
    not a model is reachable — and dropping them would record every one of these
    claims as resting on nothing.
    """
    ids, narrowed = _narrowed(declared)
    monkeypatch.chdir(declared)

    assert cli.main(["--pass", "2", kb_util.NO_INFERENCE_FLAG]) == cli.EXIT_OK
    lines = capsys.readouterr().out

    assert _register_edges(declared) == _settled(ids) == set(narrowed.edges)
    assert "no model asked" in lines
    kb = declared / "kb-root"
    assert refresh_kb_metadata.main(["--kb-root", str(kb)]) == 0
    assert verify_kb_metadata.main(["--kb-root", str(kb)]) == 0


def test_the_pairs_containment_left_open_are_neither_asserted_nor_denied(declared: Path):
    """A build that invented the uncertain half would be worse than one dropping the certain half."""
    ids, narrowed = _narrowed(declared)

    outcome = depends.build(kb_root=declared / "kb-root", repo_root=declared, scratch=_scratch(declared), selector=None)

    assert not outcome.failed, outcome.lines()
    recorded = _register_edges(declared)
    assert recorded == _settled(ids)
    assert recorded.isdisjoint(_pairs(narrowed)), "an open pair is one containment did not decide"


def _entries(kb: Path) -> list[kb_index_lib.ClaimEntry]:
    """Every claim entry on disk, through the production parser."""
    return kb_index_lib.parse_claim_quality_file(kb / "vol" / "claim-quality.md", kb)


def _register_references(repo: Path) -> set[tuple[str, str]]:
    """The references on disk, read back through the production parser."""
    return {(entry.id, edge.target) for entry in _entries(repo / "kb-root") for edge in entry.references}


def test_every_open_pair_is_recorded_as_a_reference_and_is_still_asked_about(declared: Path):
    """The two are not alternatives: the note is not the answer to the question.

    What the narrowing could not settle is the direction of dependence. That the
    source's own text names the target is not in doubt, so it is recorded — and
    the pair still goes to the model, because suppressing the ask would trade
    the answer for the note.
    """
    _ids, narrowed = _narrowed(declared)

    assert set(narrowed.references) == _pairs(narrowed)
    assert set(narrowed.references).isdisjoint(narrowed.edges)


def test_a_run_with_no_model_records_every_open_pair_as_a_reference(declared: Path):
    ids, narrowed = _narrowed(declared)

    outcome = depends.build(kb_root=declared / "kb-root", repo_root=declared, scratch=_scratch(declared), selector=None)

    assert not outcome.failed, outcome.lines()
    # The dependency set is exactly what containment settled, unchanged.
    assert _register_edges(declared) == _settled(ids)
    # And the pairs it could not direct are recorded rather than discarded.
    assert _register_references(declared) == _pairs(narrowed)

    kb = declared / "kb-root"
    assert refresh_kb_metadata.main(["--kb-root", str(kb)]) == 0
    assert verify_kb_metadata.main(["--kb-root", str(kb)]) == 0


def test_a_pair_the_selection_raises_to_a_dependency_is_not_also_a_reference(declared: Path):
    """One fact, one record: the upgrade replaces the note rather than doubling it."""
    _ids, narrowed = _narrowed(declared)
    raised = narrowed.references[0]

    remaining = attribute.references_beyond(narrowed, [*narrowed.edges, raised])

    assert raised not in remaining
    assert set(remaining) == set(narrowed.references) - {raised}
    # With nobody asked, nothing is subtracted.
    assert attribute.references_beyond(narrowed, narrowed.edges) == narrowed.references


def test_a_reference_does_not_reach_the_solidity_of_any_claim(declared: Path):
    """A reference gates nothing — the same computation over the same corpus, twice."""
    kb = declared / "kb-root"
    before = kb_index_lib.compute_solidity(_entries(kb))

    outcome = depends.build(kb_root=kb, repo_root=declared, scratch=_scratch(declared), selector=None)
    assert not outcome.failed, outcome.lines()
    assert _register_references(declared), "the fixture must actually author references for this to bind"

    assert kb_index_lib.compute_solidity(_entries(kb)) == before


def test_a_mutual_reference_pair_does_not_stop_the_stage(declared: Path):
    """Two claims naming each other is the author's argument, not a dependency cycle.

    The acyclicity gate runs over ``depends`` alone, so a corpus whose claims
    name each other mutually builds and its registers verify.
    """
    # Alpha's prose already names Beta; this is Beta's prose naming Alpha back.
    # In prose rather than in a proof, so containment directs neither and both
    # stay open pairs.
    beta = declared / "kb-root" / "vol" / "beta.md"
    beta.write_text(
        beta.read_text(encoding="utf-8")
        + '\nBeta is contrasted with\n<a href="alpha.md#thm:alpha" data-reference-type="ref" '
        'data-reference="thm:alpha">Theorem 1</a>, which it does not rest on.\n',
        encoding="utf-8",
    )
    ids, narrowed = _narrowed(declared)
    ring = ((ids["Alpha result"], ids["Beta lemma"]), (ids["Beta lemma"], ids["Alpha result"]))
    assert set(ring) <= set(narrowed.references)

    # The counterfactual the class exists to avoid: the same two pairs read as
    # dependencies close a cycle. The gate never sees them, so it still passes.
    authored = _read(declared)[2]
    assert attribute.check_acyclic(authored, list(ring)) != ()
    assert attribute.check_acyclic(authored, narrowed.edges) == ()

    outcome = depends.build(kb_root=declared / "kb-root", repo_root=declared, scratch=_scratch(declared), selector=None)

    assert not outcome.failed, outcome.lines()
    assert set(ring) <= _register_references(declared)
    kb = declared / "kb-root"
    assert refresh_kb_metadata.main(["--kb-root", str(kb)]) == 0
    assert verify_kb_metadata.main(["--kb-root", str(kb)]) == 0


def test_a_ring_among_the_settled_edges_costs_its_own_edges_and_not_the_paper(declared: Path):
    """End to end, with nobody to ask: the paper still gets a graph, and it says which edges went.

    This is the case a 50-paper sweep met three times, each of which produced no
    graph at all for the paper — the ring's edges are the only ones it may cost.
    """
    _plant_a_mutual_proof_cycle(declared)
    ids, narrowed = _narrowed(declared)
    ring = {(ids["Alpha result"], ids["Beta lemma"]), (ids["Beta lemma"], ids["Alpha result"])}

    outcome = depends.build(kb_root=declared / "kb-root", repo_root=declared, scratch=_scratch(declared), selector=None)

    assert not outcome.failed, outcome.lines()
    assert _register_edges(declared) == _settled(ids)
    assert ring <= _register_references(declared)
    verdict = next(line for line in outcome.lines() if "stage-D-attribute" in line)
    assert "2 settled edges lay on a cycle" in verdict
    assert all(f"{source} -> {target}" in verdict for source, target in narrowed.demoted)

    kb = declared / "kb-root"
    assert refresh_kb_metadata.main(["--kb-root", str(kb)]) == 0
    assert verify_kb_metadata.main(["--kb-root", str(kb)]) == 0


@pytest.mark.parametrize(
    "demoted, expected",
    [
        ((), "no settled edge lay on a cycle, so none was demoted"),
        ((("clm-aaaaaa", "clm-bbbbbb"),), "1 settled edges lay on a cycle"),
        ((("clm-aaaaaa", "clm-bbbbbb"), ("clm-bbbbbb", "clm-aaaaaa")), "clm-aaaaaa -> clm-bbbbbb"),
    ],
)
def test_the_verdict_line_names_what_a_ring_cost_and_says_so_when_nothing_did(demoted, expected):
    """One line, both forms, and the demoted edges named — nothing else records which they were."""
    finding = depends._attribute_finding(edges=7, settled=7, offered=3, demoted=demoted, asked=False)

    assert finding.status == report.PASS
    assert finding.check == "stage-D-attribute"
    assert "no model asked" in finding.detail
    assert expected in finding.detail


@pytest.mark.parametrize(
    "offered, sources, asked, expected",
    [
        (0, 0, True, "none — the narrowing left no candidate pair open"),
        (0, 0, False, "none — the narrowing left no candidate pair open"),
        (6, 4, True, "none — every one of 6 candidate pairs over 4 claims was put to a model"),
        (6, 4, False, "6 candidate pairs over 4 claims were put to no model — this build asked none"),
    ],
)
def test_the_report_tells_a_corpus_with_no_open_pairs_from_a_build_that_could_not_ask(
    offered: int, sources: int, asked: bool, expected: str
):
    """Three states, one line, and the zero form said in as many words."""
    finding = depends._unasked_finding(offered=offered, sources=sources, asked=asked)

    assert finding.status == report.FACT
    assert finding.check == "stage-D-unasked"
    assert expected in finding.detail


def test_claim_discovery_has_no_run_that_asks_nobody(declared: Path, monkeypatch, capsys):
    """Refused at the command line rather than run empty into its own exit condition."""
    monkeypatch.chdir(declared)

    assert cli.main(["--pass", "1", "--scope", "full", kb_util.NO_INFERENCE_FLAG]) == cli.EXIT_USAGE

    assert kb_util.NO_INFERENCE_FLAG in capsys.readouterr().err
