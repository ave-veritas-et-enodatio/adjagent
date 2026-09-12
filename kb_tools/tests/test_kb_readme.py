"""The overview document's mechanical half: the facts, and the substitution.

Everything ``<kb-root>/README.md`` says about size and shape — how many claims,
which ``.index/`` artifacts this corpus populated, how deep the tree runs — is
already on disk when ``phase-5`` runs. These tests hold :mod:`kb_tools.kb_readme`
to *reading* it: each fact against the artifact it is read from, so a second
derivation of one would show up as a disagreement rather than as two numbers
that happen to match today.

The other half is the refusal. A template and a fact set written by different
hands can disagree, and the failure that must never be quiet is a knowledge base
shipping a document with a slot's own name in it.
"""

import json
from pathlib import Path, PurePosixPath

import pytest

from kb_tools import kb_index_lib, kb_pipeline, kb_readme, kb_schema, kb_util
from kb_tools.kb_write import render

# ---------------------------------------------------------------------------
# A KB, as a finished build leaves one
# ---------------------------------------------------------------------------

#: One document of each kind the vocabulary admits, nested two deep. `vol/` is
#: the one domain; `entry-point.md` falls under none.
DOCUMENTS = {
    "entry-point.md": "entry-point",
    "vol/index.md": "index",
    "vol/alpha.md": "leaf",
    "vol/deeper/index.md": "leaf-as-index",
    "vol/deeper/beta.md": "leaf",
}

#: Files `kb_index_lib.kb_files` excludes: authored, but not documents of the
#: tree. Present in the fixture so their exclusion is exercised rather than
#: assumed.
NON_DOCUMENTS = ("README.md", "CONVENTIONS.md", "CLAUDE.md", "vol/claim-quality.md")

REGISTER = "vol/claim-quality.md"


def index_filenames() -> tuple[str, ...]:
    """Every ``.index/`` artifact, from the emitter's own record-set vocabulary."""
    empty = kb_index_lib.KbState(claim_entries=(), leaves=(), indexes=(), framework_nodes=(), experiments=())
    return tuple(f"{name}.jsonl" for name in kb_index_lib.build_all_records(empty))


def claim_record(claim_id: str, *, solidity: float | None) -> dict:
    return {
        "node_type": "claim",
        "id": claim_id,
        "title": f"Theorem {claim_id[-1]}",
        "canonical_path": REGISTER,
        "canonical_anchor": claim_id,
        "confidence": solidity,
        "solidity": solidity,
        "build_status": None,
        "build_band": kb_schema.UNKNOWN_BAND_SLUG if solidity is None else "ok-to-build",
        "rationale": "",
        "depends_on_count": 0,
        "strengthen_by_count": 0,
        "citation_count": 0,
    }


def work_record(work_id: str) -> dict:
    return {
        "node_type": "work",
        "id": work_id,
        "title": work_id,
        "canonical_path": "claim-quality.md",
        "canonical_anchor": work_id,
        "strength": None,
    }


def edge_record(source: str, target: str, relation: str) -> dict:
    return {
        "source": source,
        "target": target,
        "relation": relation,
        "target_kind": "claim",
        "target_solidity_recorded": None,
        "strength": None,
        "context": None,
        "fraction": None,
    }


CLAIMS = [claim_record("clm-aaaaa1", solidity=None), claim_record("clm-aaaaa2", solidity=0.9)]
WORKS = [work_record("work-khalil2002")]
EDGES = [
    edge_record("clm-aaaaa2", "clm-aaaaa1", "depends"),
    edge_record("clm-aaaaa2", "work-khalil2002", "rests-on"),
]


def write_document(kb_root: Path, path: str, kind: str) -> None:
    """One document as the build stamps it: a heading and a frontmatter block.

    The block is composed by the write API, which is the only composer of one —
    a hand-written block here would be a second spelling of the format the
    parser under test reads.
    """
    target = kb_root / path
    target.parent.mkdir(parents=True, exist_ok=True)
    block = render.render_frontmatter_block(render.FrontmatterValues(kind=kind))
    target.write_text(f"# {PurePosixPath(path).stem}\n\n{block}\n", encoding="utf-8")


@pytest.fixture
def kb_root(tmp_path: Path) -> Path:
    """A KB at the state ``phase-5`` meets it: a stamped tree over a refreshed index."""
    root = tmp_path / kb_util.KB_DIRNAME
    for path, kind in DOCUMENTS.items():
        write_document(root, path, kind)
    for name in NON_DOCUMENTS:
        (root / name).parent.mkdir(parents=True, exist_ok=True)
        (root / name).write_text(f"# {name}\n", encoding="utf-8")

    index_dir = root / kb_util.INDEX_DIRNAME
    index_dir.mkdir(parents=True)
    populated = {"claims.jsonl": [*CLAIMS, *WORKS], "depends-on.jsonl": EDGES}
    for name in index_filenames():
        records = populated.get(name, [])
        (index_dir / name).write_text(
            "".join(json.dumps(record) + "\n" for record in records),
            encoding="utf-8",
        )
    return root


@pytest.fixture
def facts(kb_root: Path) -> dict[str, str]:
    return kb_readme.facts(kb_root=kb_root, project_name="fixture-kb")


# ---------------------------------------------------------------------------
# The substitution, and the refusal that guards it
# ---------------------------------------------------------------------------


def test_a_slot_nothing_computes_is_refused_by_name() -> None:
    """The one failure that must not be quiet: a slot's own name shipped as content."""
    with pytest.raises(kb_readme.TemplateError) as raised:
        kb_readme.fill("{claim-count} claims, {invented-fact} of them blue", {"claim-count": "3"})

    assert "invented-fact" in str(raised.value)
    assert "claim-count" not in str(raised.value).partition("nothing computes")[2].partition("—")[0]


def test_a_computed_fact_the_template_never_names_is_not_an_error() -> None:
    """The fact set is the toolchain's; which of it to state is the template's."""
    assert kb_readme.fill("{claim-count}", {"claim-count": "3", "leaf-count": "9"}) == "3"


def test_substitution_is_one_pass_so_a_value_is_content_and_never_a_slot() -> None:
    """A seat's prose naming a slot is prose: the pass that placed it does not re-read it."""
    values = {kb_readme.PROSE_SLOT: "read {leaf-count} first", "leaf-count": "9"}
    filled = kb_readme.fill("{" + kb_readme.PROSE_SLOT + "}", values)

    assert filled == "read {leaf-count} first"


def test_the_slots_of_a_template_are_first_appearance_order_without_repeats() -> None:
    assert kb_readme.slots("{b} {a} {b}") == ("b", "a")


def test_the_project_name_slot_is_the_one_the_packaged_readiness_docs_take() -> None:
    """One name for one fact across every template under ``installed/``."""
    assert "{" + kb_readme.PROJECT_NAME_SLOT + "}" == kb_pipeline.PROJECT_NAME_FIELD


def test_every_slot_the_packaged_template_names_is_a_fact_or_the_seats_passage(kb_root: Path) -> None:
    """The shipped pair, held against each other — the interlock the runtime refusal also makes.

    The template and the fact set are written by different hands, so this is the
    one place their agreement is a checked property rather than a convention:
    every slot the shipped document names is served either by a read of the KB
    or by the one answer the stage asks a seat for.
    """
    served = set(kb_readme.facts(kb_root=kb_root, project_name="fixture-kb")) | {kb_readme.PROSE_SLOT}

    assert set(kb_readme.slots(kb_readme.template_text())) <= served


# ---------------------------------------------------------------------------
# The claim-graph facts, against `.index/`
# ---------------------------------------------------------------------------


def test_the_node_census_counts_what_claims_jsonl_holds(facts: dict[str, str]) -> None:
    assert facts["claim-count"] == str(len(CLAIMS))
    assert facts["work-count"] == str(len(WORKS))
    assert facts["experiment-count"] == "0"


def test_the_node_census_spans_the_whole_kind_vocabulary_in_its_own_order(facts: dict[str, str]) -> None:
    """A kind this corpus has none of is reported as zero, not omitted.

    The order is the vocabulary's, so the corpus's own content cannot reach an
    output position — which is what a census over a dict of counts would let it
    do the first time a KB populated its kinds in a different sequence.
    """
    reported = [term.split(" ", 1)[1] for term in facts["node-kind-counts"].split(", ")]

    assert reported == [kb_schema.node_kind_plural(kind) for kind in kb_schema.NODE_KINDS]


def test_the_edge_census_counts_every_relation_class_separately(facts: dict[str, str]) -> None:
    """A class with no instance is a zero on its own line, in the declared census order."""
    assert facts["edge-count"] == str(len(EDGES))
    assert facts["edge-class-counts"].splitlines() == [
        "- depends — 1",
        "- supports — 0",
        "- strengthens — 0",
        "- rests-on — 1",
        "- references — 0",
    ]


def test_the_scoring_state_is_read_off_the_claims_rather_than_counted_twice(facts: dict[str, str]) -> None:
    assert facts["pending-count"] == "1"
    assert facts["scored-count"] == "1"
    assert facts["pending-literal"] == kb_schema.PENDING_LITERAL


# ---------------------------------------------------------------------------
# The derived artifacts, against the directory
# ---------------------------------------------------------------------------


def test_empty_index_artifacts_are_reported_as_empty_rather_than_missing(facts: dict[str, str]) -> None:
    """An empty edge file is a fact about the corpus — no experiments, no supports."""
    assert facts["populated-index-files"] == "claims.jsonl, depends-on.jsonl"
    assert set(facts["empty-index-files"].split(", ")) == set(index_filenames()) - {
        "claims.jsonl",
        "depends-on.jsonl",
    }


# ---------------------------------------------------------------------------
# The tree facts, against the document walk
# ---------------------------------------------------------------------------


def test_the_document_walk_is_the_one_that_excludes_the_authored_non_documents(facts: dict[str, str]) -> None:
    assert facts["document-count"] == str(len(DOCUMENTS))
    for name in NON_DOCUMENTS:
        assert name not in facts["document-tree"]


def test_the_leaf_count_spans_both_leaf_kinds(facts: dict[str, str]) -> None:
    """A `leaf-as-index` is a leaf: it hosts a claim-bearing body like any other."""
    assert facts["leaf-count"] == "3"


def test_the_tree_renders_each_index_above_what_it_indexes(facts: dict[str, str]) -> None:
    """The declared order: a directory's own index, then its documents, then its subdirectories."""
    assert facts["document-tree"] == "\n".join(
        [
            "entry-point.md  (entry-point)",
            "vol/",
            "  index.md  (index)",
            "  alpha.md  (leaf)",
            "  deeper/",
            "    index.md  (leaf-as-index)",
            "    beta.md  (leaf)",
        ]
    )


# ---------------------------------------------------------------------------
# Assembly
# ---------------------------------------------------------------------------


def test_the_seats_answer_and_the_read_facts_are_one_mapping(kb_root: Path) -> None:
    """A template slot is served by either half without the substitution knowing which."""
    values = {
        **kb_readme.facts(kb_root=kb_root, project_name="fixture-kb"),
        kb_readme.PROSE_SLOT: "Start at the introduction.",
    }
    filled = kb_readme.fill(
        "# {project-name}\n\n{overview-passage}\n\n{claim-count} claims.\n",
        values,
    )

    assert filled == "# fixture-kb\n\nStart at the introduction.\n\n2 claims.\n"
