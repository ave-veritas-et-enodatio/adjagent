"""Stage 3 — write the tree and its assets, then run the gates.

A failure stops. There is no fix loop, because there is no seat to run one: every
check here compares one mechanical product against another, so a red gate is a
defect in this stage or in its input, and neither is repaired by asking.

What the build writes under ``kb-root/`` is a directory tree of Markdown and the
image assets those documents embed, and **nothing else** — no ``.index/``, no
frontmatter block, no ``claims:`` key, no id or claim-quality marker. A document
carries body text, a heading, an up-link, and — for an index — a child list. The
claim graph is a later stage's, and it reads this tree as its sole input.

The pandoc version is recorded in the report rather than in the tree, for the
same reason: a version stamp in a document is a build artifact in a space that
holds none. A tree that differs from the last one is diagnosable from the run
that produced it.
"""

import shutil
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path

from .. import pandoc, verify_md_links
from ..kb_index_lib import ENTRY_POINT_FILENAME
from ..kb_survey import validate
from . import partition
from .convert import Volume, convert
from .judge import judge, recut
from .outline import ASSET_DIRNAME, VolumeTree, build_tree, render
from .partition import FACT, FAIL, PASS, Finding
from .walk import read_outline

#: The one document that is nobody's child. Its title is the tree's, and it is
#: the only place a volume is named to a reader who has not opened one.
ENTRY_POINT_TITLE = "Knowledge Base"


@dataclass
class Report:
    """What one build produced and what the gates said about it."""

    pandoc_version: str
    trees: list[VolumeTree] = field(default_factory=list)
    findings: list[Finding] = field(default_factory=list)

    @property
    def failed(self) -> bool:
        return partition.gating(self.findings)

    def lines(self) -> list[str]:
        # The reader is named off the seam's own constant rather than spelled
        # here: nothing outside that module names the binary, and the report is
        # not an exception to it.
        version = Finding(partition.FACT, f"{pandoc.BINARY}-version", self.pandoc_version)
        return [version.line(), *(finding.line() for finding in self.findings)]


def build(*, volume_roots: Sequence[Path], bibliographies: Sequence[Path], kb_root: Path) -> Report:
    """Run stages 1 through 3 over ``volume_roots`` and return the gates' verdict.

    ``volume_roots`` are top-level LaTeX documents and nothing else. A file
    reached by ``\\input`` from one of them is not a volume: passing it alongside
    its includer converts the same content twice, and no per-volume check can see
    it, because neither volume can see the other's copy.

    ``bibliographies`` is the whole set every volume resolves against, in the
    order the caller fixed: pandoc merges them and citeproc renders only what a
    volume cites, so a file a given volume has no use for costs it nothing.
    """
    report = Report(pandoc_version=pandoc.version())

    for root in volume_roots:
        volume = convert(root, bibliographies=bibliographies)
        outline = read_outline(volume.ast)
        tree = build_tree(stem=volume.stem, markdown=volume.markdown, outline=outline, volume_directory=root.parent)
        verdict = judge(tree)
        if not verdict.accepted:
            tree = recut(tree, verdict)
        report.trees.append(tree)
        report.findings += _bibliography(volume)
        report.findings += _declarations(volume)
        report.findings += partition.check_ast_against_markdown(
            stem=volume.stem, ast=volume.ast, markdown=volume.markdown
        )
        report.findings += partition.check_markdown_against_tree(tree)
        report.findings += partition.check_math(stem=volume.stem, ast=volume.ast, tree=tree)
        report.findings += partition.check_anchors(tree)
        report.findings += partition.check_assets(tree)

    _write(report.trees, kb_root=kb_root, volume_roots=volume_roots)
    report.findings += _validate(report.trees, kb_root=kb_root)
    report.findings += _check_links(kb_root)
    return report


def _bibliography(volume: Volume) -> list[Finding]:
    """What the volume's citations resolved against, where that was not what was asked for.

    A bibliography pandoc could not read costs resolution and not the citations
    (SPEC.md point 10), so this does not gate. It is stated all the same, and
    with the consequence spelled out rather than left to be inferred from a path:
    the degraded tree is indistinguishable from a corpus that shipped no
    bibliography, and a reader of a silent build's output would take these
    citations for resolved ones.

    **Every file offered is named, because pandoc's exit code says one of them
    failed and not which.** The reader's own complaint says which, and it is on
    stderr; this line is what says the whole set went unused.
    """
    if not volume.unreadable_bibliographies:
        return []
    offered = ", ".join(repr(str(path)) for path in volume.unreadable_bibliographies)
    return [
        Finding(
            FACT,
            partition.CHECK_BIBLIOGRAPHY,
            f"{volume.stem}: unread ({offered}) — the reader's complaint on stderr names the file it "
            "refused; the volume was converted as if it had none, so every citation carries its own key, "
            "no citation resolved, and there is no references leaf",
        )
    ]


def _declarations(volume: Volume) -> list[Finding]:
    """The environment declarations stage 1 could not read, and what each one cost.

    A declaration the pre-scan cannot read is left where the author wrote it, so
    pandoc expands that environment as defined: the content reaches the tree and
    neither partition check goes false. What is lost is narrower and invisible —
    the name — so those blocks arrive as ordinary content rather than as the
    labelled blockquotes SPEC.md point 12 describes, and a loss no check can see
    is one the report has to carry.

    **Both forms are stated, where the bibliography line above appears only on
    the failure.** A tree whose blocks lost their names reads exactly like a
    corpus that distinguished no block, so the zero is what tells a reader which
    of the two this volume is.
    """
    if not volume.unreadable_declarations:
        return [
            Finding(
                FACT,
                partition.CHECK_DECLARATION,
                f"{volume.stem}: none — every environment declaration this volume root makes was read, so "
                "each one's blocks reach the tree as labelled blockquotes carrying the author's own name for it",
            )
        ]
    lines = ", ".join(str(declaration.line) for declaration in volume.unreadable_declarations)
    return [
        Finding(
            FACT,
            partition.CHECK_DECLARATION,
            f"{volume.stem}: unread at line(s) {lines} — the pre-scan's warning on stderr carries each "
            "declaration as written; each was left where the author wrote it, so the reader expands that "
            "environment as defined and its content reaches the tree with neither partition check going "
            "false, but its blocks arrive as ordinary content rather than as labelled blockquotes carrying "
            "a name, which nothing else here reports",
        )
    ]


def _write(trees: Sequence[VolumeTree], *, kb_root: Path, volume_roots: Sequence[Path]) -> None:
    """The tree and the copied assets. Idempotent over an existing tree by overwrite."""
    kb_root.mkdir(parents=True, exist_ok=True)
    for tree, root in zip(trees, volume_roots):
        for node in tree.documents:
            target = kb_root / node.path
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(render(node, tree_root=ENTRY_POINT_FILENAME), encoding="utf-8")
        for source, name in sorted(tree.assets.items()):
            destination = kb_root / Path(tree.index.path).parent / ASSET_DIRNAME / name
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(root.parent / source, destination)
    (kb_root / ENTRY_POINT_FILENAME).write_text(_entry_point(trees), encoding="utf-8")


def _entry_point(trees: Sequence[VolumeTree]) -> str:
    listing = "\n".join(f"- [{tree.title}]({tree.index.path})" for tree in trees)
    return f"# {ENTRY_POINT_TITLE}\n\n{listing}\n"


def _validate(trees: Sequence[VolumeTree], *, kb_root: Path) -> list[Finding]:
    """Points 3, 4 and 5, through the checker that already owns them.

    ``validate_build`` walks a built Markdown tree and never opens a ``.tex``. It
    takes a path list, and this stage supplies its own: the documents it just
    wrote, which is what makes the tree-diff check a real comparison here — a
    caller handed only a tree can compare it against nothing but itself.
    """
    paths = [ENTRY_POINT_FILENAME, *(node.path for tree in trees for node in tree.documents)]
    return [
        Finding(finding.status, f"validate-build/{finding.check}", finding.detail)
        for finding in validate.validate_build(paths=paths, kb_root=kb_root)
    ]


def _check_links(kb_root: Path) -> list[Finding]:
    """Point 7's first half, through the repository-wide dead-link gate."""
    broken = verify_md_links.scan(kb_root, check_ids_enabled=False)
    if broken:
        return [
            Finding(
                FAIL,
                "verify-md-links",
                f"{finding.file.relative_to(kb_root)}:{finding.line} {finding.kind} {finding.target!r}",
            )
            for finding in broken
        ]
    return [Finding(PASS, "verify-md-links", f"no dead link under {kb_root.name}/")]
