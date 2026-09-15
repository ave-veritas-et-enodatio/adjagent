"""The KB's overview document, assembled from the build's own derived facts.

The build used to ask a seat to compose ``<kb-root>/README.md`` whole, counts
included, and a second seat to check those counts by recounting. Every one of
them already sits in ``.index/`` or in the tree, so the composition is
mechanical and the check was two model calls spent moving integers out of a
JSONL file. This module is the mechanical half: :func:`facts` reads them,
:func:`fill` substitutes them into the packaged template, and the one thing
neither can produce — the passage saying what this corpus is and where a reader
starts — arrives as one more entry in the same mapping, written by the seat that
read the corpus.

**Every fact is read, never recomputed.** The claim-graph half comes through
``kb_cmd.index``, which is the ``.index/`` query interface, and the tree half
through ``kb_index_lib.kb_files``, which is the KB document walk. A second
derivation of a number the index already carries would be a second answer to one
question, and the pair would disagree the first time either side moved.

**A slot the caller has no value for is a refusal, never a blank.** The template
and the fact set are written by different hands; the one failure that must not
be silent is a KB shipping a README with ``{claim-count}`` in it, so
:func:`fill` names every unfilled slot and writes nothing.

Stdlib only.
"""

import re
from collections.abc import Iterable, Iterator, Mapping
from pathlib import Path

from . import kb_index_lib, kb_pipeline, kb_schema, kb_util
from .kb_cmd import index as index_query
from .kb_graph.style import CENSUS_RELATIONS
from .kb_write.values import DOCUMENT_KINDS

#: The packaged template this module fills, under ``kb_tools/installed/``. Named
#: through ``kb_pipeline`` so the document's name has one spelling and the
#: stage's own postcondition looks for the file this writes.
TEMPLATE_DOC = kb_pipeline.OVERVIEW_DOC

#: The per-project name slot, spelled once for every packaged template: the
#: readiness docs already take this field, and a second spelling of it in
#: ``installed/`` would be a second name for one fact.
PROJECT_NAME_SLOT = kb_pipeline.PROJECT_NAME_FIELD.strip("{}")

#: The one slot no read of the KB fills — what this corpus argues, what it leaves
#: out, and which document a reader opens first. It is the whole of what the
#: stage asks a seat for, and the whole of what the seat returns.
PROSE_SLOT = "overview-passage"

# A slot is a lowercase hyphenated name in braces — the shape
# `kb_pipeline.PROJECT_NAME_FIELD` already writes. Prose in the template is
# Markdown and carries no braces of its own; one that did would be reported as
# an unfilled slot, which is loud rather than silent.
_SLOT_RE = re.compile(r"\{([a-z][a-z0-9-]*)\}")

# What a slot carries where the KB has nothing to put in it. A slot is never
# filled with the empty string: a document that silently loses a sentence's
# subject reads as a truncated document rather than as an honest zero.
_ABSENT = "(none)"


class TemplateError(ValueError):
    """The template and the fact set disagree about which slots exist."""


# --- the substitution --------------------------------------------------------


def slots(template: str) -> tuple[str, ...]:
    """Every slot the template names, in first-appearance order, without repeats."""
    return tuple(dict.fromkeys(_SLOT_RE.findall(template)))


def fill(template: str, values: Mapping[str, str]) -> str:
    """Substitute ``values`` into ``template``, refusing a slot nothing computes.

    One pass, so a value that happens to contain brace text is content rather
    than a slot of its own. A value the template never names is not an error:
    the fact set is the toolchain's and the template picks from it.
    """
    missing = [name for name in slots(template) if name not in values]
    if missing:
        raise TemplateError(
            f"{TEMPLATE_DOC}: the template names slot(s) nothing computes: {', '.join(missing)} — "
            f"computed: {', '.join(sorted(values))}"
        )
    return _SLOT_RE.sub(lambda match: values[match.group(1)], template)


def template_text() -> str:
    """The packaged template's text.

    Its absence is an incomplete install of this toolchain, reported the way
    ``kb_pipeline.stamp_readiness_docs`` reports the same fault for the
    readiness templates.
    """
    source = kb_pipeline.installed_template(TEMPLATE_DOC)
    if not source.is_file():
        raise kb_pipeline.PipelineError(
            f"the packaged template {source} is missing; this kb_tools install is "
            f"incomplete — re-install the agent definitions."
        )
    return source.read_text(encoding="utf-8")


def assemble(*, kb_root: Path, project_name: str, prose: Mapping[str, str]) -> str:
    """The overview document: the packaged template, the derived facts, the seat's prose.

    ``prose`` is what no read of the KB produces — the seat's answer — keyed by
    the slot the template holds it in. It is merged over the derived facts so the
    two sets are one mapping, which is what lets a slot the template names be
    served by either without this function knowing which.
    """
    return fill(template_text(), {**facts(kb_root=kb_root, project_name=project_name), **prose})


# --- the facts ---------------------------------------------------------------


def facts(*, kb_root: Path, project_name: str) -> dict[str, str]:
    """Every derived fact the overview document can state, as template values.

    Read from the two places a finished build leaves them: ``.index/`` for the
    claim graph, and the document tree for the topography. Nothing here reads the
    corpus, and nothing here judges.
    """
    index_dir = kb_root / kb_util.INDEX_DIRNAME
    return {
        PROJECT_NAME_SLOT: project_name,
        **_graph_facts(index_query.load(index_dir)),
        **_index_file_facts(index_dir),
        **_tree_facts(kb_root),
    }


def _graph_facts(index: index_query.Index) -> dict[str, str]:
    """What ``.index/`` says about the claim graph: the nodes, the edges, the scoring.

    Both censuses run over a **declared vocabulary** rather than over the counts
    they are counting. A census that iterated a dict would order its terms by
    whatever the corpus happened to hold first, and would omit the kinds this
    corpus has none of — and a kind omitted is a kind a reader cannot tell from
    one this KB does not populate.
    """
    stats = index.stats
    node_counts = {kind: stats[kb_schema.node_kind_plural(kind)] for kind in kb_schema.NODE_KINDS}

    relations: dict[str, int] = {}
    for edge in index.all_depends_on_edges:
        relations[edge.relation] = relations.get(edge.relation, 0) + 1
    # A relation outside the declared census order still reaches the line, after
    # the declared ones and in name order — the rule `RELATION_PRECEDENCE`
    # already states for a relation it does not name.
    relation_order = CENSUS_RELATIONS + tuple(sorted(set(relations) - set(CENSUS_RELATIONS)))

    pending = index.pending_count

    return {
        **{f"{kind}-count": str(count) for kind, count in node_counts.items()},
        "node-kind-counts": _names(
            f"{node_counts[kind]} {kb_schema.node_kind_plural(kind)}" for kind in kb_schema.NODE_KINDS
        ),
        "edge-count": str(len(index.all_depends_on_edges)),
        # A block rather than a line: an edge class is a list item in the
        # document. Which artifact holds them is not here, because it is one
        # artifact for all four classes (SPEC.md, Claim-Graph Nodes and Edges)
        # — invariant prose, which is the template's, not a fact that varies.
        "edge-class-counts": _lines(f"- {term} — {relations.get(term, 0)}" for term in relation_order),
        "scored-count": str(len(index.all_claims) - pending),
        "pending-count": str(pending),
        "pending-literal": kb_schema.PENDING_LITERAL,
    }


def _index_file_facts(index_dir: Path) -> dict[str, str]:
    """Which derived artifacts this build populated, and which it left empty.

    Read off the directory rather than off a list of filenames, so an artifact
    the emitter gains is reported by that alone; an empty one is a fact about the
    corpus (no experiments, no supports) and not a defect.
    """
    counts = {path.name: len(kb_index_lib.read_jsonl(path)) for path in sorted(index_dir.glob("*.jsonl"))}
    return {
        "populated-index-files": _names(name for name, count in counts.items() if count),
        "empty-index-files": _names(name for name, count in counts.items() if not count),
    }


def _tree_facts(kb_root: Path) -> dict[str, str]:
    """What the document tree is: its documents, their kinds, and its shape."""
    kinds = {
        path.relative_to(kb_root).as_posix(): _document_kind(path.read_text(encoding="utf-8"))
        for path in kb_index_lib.kb_files(kb_root)
    }
    return {
        "document-count": str(len(kinds)),
        "leaf-count": str(sum(1 for kind in kinds.values() if kind == "leaf")),
        "document-tree": _render_tree(kinds),
    }


def _document_kind(text: str) -> str:
    """A document's structural-position label, off the frontmatter the build stamped."""
    frontmatter = kb_index_lib.parse_frontmatter(text) or {}
    kind = frontmatter.get("kind", "")
    return kind if kind in DOCUMENT_KINDS else ""


def _render_tree(kinds: Mapping[str, str]) -> str:
    """The tree as an indented listing, each document under the directory holding it.

    **The order is declared here and is not the source's own section order.** A
    directory's own ``index.md`` comes first, then its other documents by name,
    then its subdirectories by name — the shape a reader walks down, and the one
    place an index can sit above what it indexes. Where a document falls among
    its siblings *in the source* is carried by its parent index's child-link list
    (SPEC.md, the Document-Tree Contract, point 4) and by nothing ``.index/``
    holds, so it is not this listing's to reproduce.
    """
    tree: dict = {}
    for path, kind in kinds.items():
        node = tree
        *directories, name = path.split("/")
        for directory in directories:
            node = node.setdefault(directory, {})
        node[name] = kind
    return _lines(_tree_lines(tree, depth=0))


def _tree_lines(node: Mapping[str, object], *, depth: int) -> Iterator[str]:
    indent = "  " * depth
    documents = (name for name, value in node.items() if isinstance(value, str))
    for name in sorted(documents, key=lambda name: (name != kb_index_lib.INDEX_FILENAME, name)):
        kind = node[name]
        yield f"{indent}{name}  ({kind})" if kind else f"{indent}{name}"
    for name in sorted(name for name, value in node.items() if not isinstance(value, str)):
        yield f"{indent}{name}/"
        yield from _tree_lines(node[name], depth=depth + 1)  # type: ignore[arg-type]


def _lines(items: Iterable[str]) -> str:
    """A block of lines, or a named absence — a slot is never filled with nothing."""
    return "\n".join(items) or _ABSENT


def _names(items: Iterable[str]) -> str:
    """A comma-joined list, or a named absence."""
    return ", ".join(items) or _ABSENT
