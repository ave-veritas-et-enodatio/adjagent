"""The validator's checks: the derivation's own audit, shape facts, reachability, the tree diff.

Two modes over the same report vocabulary. :func:`validate_skeleton` runs the
pre-build checks against a manifest and the skeleton derived from it;
:func:`validate_build` runs the post-build checks against that skeleton's path
list and the tree on disk. Neither mode reads the other's inputs.

Three gates and one measurement. Checks 1, 3 and 4 are gates — each is true or
false from the documents themselves, by set or path-component arithmetic over
identity keys, and each reports in both directions rather than comparing counts.
Check 3 is a gate only for a caller holding a path list independent of the tree;
handed ``paths=None`` it reports ``FACT`` and gates nothing, because comparing
the tree against a walk of the tree cannot fail and reporting ``PASS`` for it
would claim a coverage the run does not have.
Check 2 is a **measurement**: it emits ``FACT`` lines and gates nothing. There is no threshold
anywhere in it, no override, and no configuration surface — a number authored before
the tool met the document cannot know the document, and a tight bound would not fail
visibly, it would coerce corpora that mutilate their own segmentation to pass. The only
constant here is a display cap on list length, which is about report length rather than
about the document.

**What used to be here, and why it is not.** Five checks — coverage, containment,
no-bisection, no-stitching and domain ownership — verified that a *hand-authored*
mapping of source sections onto KB paths tiled the source without gaps, overlaps
or cuts through a theorem. There is no such mapping: ``skeleton.derive`` places
every section at exactly one path, taking the document's own segmentation whole,
so none of those five has a way to fail that is not a fault in that function.
What replaced them is check 1 — the derivation compared against its own input,
which is the same question asked where it can still be answered wrongly.

**This module never reads LaTeX and never opens a ``.tex``.** Its inputs are a
manifest, a derived skeleton, a list of KB paths, and — post-build — the Markdown
tree those paths name.

Design-time reachability has no tree on disk to read the KB's navigation convention
from, so it reads it from ``kb_index_lib`` — the node filenames and the up-link
marker, imported rather than restated, so that what this module checks and what the
driver writes cannot drift apart.

**No downgrade path.** :func:`exit_code` is 0 iff no ``FAIL``; it takes no
argument that could change that, and neither entry point takes one that could arm a
measurement into a gate or disarm a gate into a measurement.

**Report order.** Pre-build findings come back in check order — 1, 2, 4 — and
post-build findings in theirs — 3, 4, the ``mf:`` guard. Within a gate check they
are sorted by ``(check, detail)``, where every detail begins with the offending
identity: a section id, a KB path, or a ``file:line-line`` coordinate. Check 2's
``FACT`` lines are ordered by the distribution they report (leaves descending by
size, then depth, then fan-out), because that ordering is the report's content rather
than an incidental arrangement.
"""

import posixpath
import statistics
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from .. import kb_index_lib, kb_links
from ..kb_index_lib import ENTRY_POINT_FILENAME, INDEX_FILENAME, UPLINK_MARKER
from . import skeleton as survey_skeleton
from .manifest import ID_PREFIX, Manifest

TAG = "survey-validate"

# The report vocabulary, restated rather than imported: `kb_util` is a consumer of
# this package, so importing it here would invert the dependency arrow.
PASS = "PASS"
FAIL = "FAIL"
FACT = "FACT"

EXIT_OK = 0
EXIT_VIOLATION = 1

CHECK_PARTITION = "1-partition"
CHECK_LEAF_SIZE = "2-leaf-size"
CHECK_DEPTH = "2-depth"
CHECK_FAN_OUT = "2-fan-out"
CHECK_TREE_DIFF = "3-tree-diff"
CHECK_REACHABILITY = "4-reachability"
CHECK_ID_GUARD = "guard-mf-token"

# How many entries a FACT list prints. A report-length constant and the only number
# in check 2: nothing about the document is judged by it, and nothing gates on it.
SHAPE_LIST_MAX_ENTRIES = 10


@dataclass(frozen=True)
class Finding:
    """One report line. ``FACT`` describes state and never gates."""

    status: str
    check: str
    detail: str

    def line(self) -> str:
        return f"[{TAG}] {self.status} {self.check} {self.detail}"


# --- check 1: the derivation against its own input ---------------------------


def _check_partition(manifest: Manifest, skeleton: survey_skeleton.Skeleton) -> list[Finding]:
    """1: every section placed at exactly one path, and no path naming a section that is not there.

    The whole of the coverage question, asked where it can still be answered
    wrongly. It is cheap for the reason it is trustworthy: both sides come from
    the toolchain, so nothing here corroborates a claim of a model's against a
    manifest — it compares a derivation against the records it derived from.
    """
    defects = survey_skeleton.partition_defects(manifest, skeleton)
    return [Finding(FAIL, CHECK_PARTITION, defect) for defect in defects] or [
        Finding(
            PASS,
            CHECK_PARTITION,
            f"{len(manifest.sections)} sections placed at {len(skeleton.nodes)} documents, each section exactly once",
        )
    ]


# --- check 2: shape profile, a measurement -----------------------------------


def _capped(entries: Sequence[Finding]) -> list[Finding]:
    if len(entries) <= SHAPE_LIST_MAX_ENTRIES:
        return list(entries)
    shown = list(entries[:SHAPE_LIST_MAX_ENTRIES])
    omitted = len(entries) - SHAPE_LIST_MAX_ENTRIES
    shown.append(Finding(FACT, shown[0].check, f"... {omitted} more not shown ({len(entries)} total)"))
    return shown


def _distribution(check: str, name: str, values: Sequence[int]) -> Finding:
    return Finding(
        FACT,
        check,
        f"{name} count={len(values)} min={min(values)} median={statistics.median(values):g} max={max(values)}",
    )


def _shape_facts(manifest: Manifest, skeleton: survey_skeleton.Skeleton) -> list[Finding]:
    """The shape the source's own segmentation produced: leaf sizes, depth, fan-out.

    Reported and never judged. Where the five retired coverage checks measured
    whether an author had tiled the source, this measures what the document did
    to itself — a corpus whose sectioning yields one 90-KB leaf and forty
    200-byte ones is the document diagnosed, and the report is where a reader
    sees it.
    """
    profiles = {section.id: section.profile for section in manifest.sections}
    findings: list[Finding] = []

    sizes = [
        (node.path, profiles[node.section_id].stripped_chars)
        for node in skeleton.leaves
        if node.section_id is not None and node.section_id in profiles
    ]
    if sizes:
        findings.append(_distribution(CHECK_LEAF_SIZE, "leaves", [size for _, size in sizes]))
        findings.extend(
            _capped(
                [
                    Finding(FACT, CHECK_LEAF_SIZE, f"{path} stripped_chars={size}")
                    for path, size in sorted(sizes, key=lambda item: (-item[1], item[0]))
                ]
            )
        )

    depths = [len(PurePosixPath(node.path).parts) for node in skeleton.nodes]
    if depths:
        findings.append(_distribution(CHECK_DEPTH, "paths", depths))
        counts = {depth: depths.count(depth) for depth in sorted(set(depths))}
        findings.extend(
            _capped([Finding(FACT, CHECK_DEPTH, f"depth={depth} paths={count}") for depth, count in counts.items()])
        )

    # Fan-out is counted over the same parent relation check 4 walks, so a node's
    # children are the paths that hang from it rather than the files beside it.
    children: dict[str, int] = {}
    for node in skeleton.nodes:
        parent = parent_index(node.path)
        if parent is not None:
            children[parent] = children.get(parent, 0) + 1
    if children:
        findings.append(_distribution(CHECK_FAN_OUT, "parents", list(children.values())))
        findings.extend(
            _capped(
                [
                    Finding(FACT, CHECK_FAN_OUT, f"{parent} children={count}")
                    for parent, count in sorted(children.items(), key=lambda item: (-item[1], item[0]))
                ]
            )
        )
    return findings


# --- check 4: design-time reachability ---------------------------------------


def parent_index(path: str) -> str | None:
    """The node file ``path`` hangs from, or ``None`` for the tree root.

    Public because the driver walks it in the other direction: this check reads
    a parent index off a path to ask whether it is present, and ``p3.indexes``
    reads the same relation off the derived nodes to decide which index
    documents exist to be written. A second spelling of it there would be a
    driver that built a tree this check could not walk.
    """
    if path == ENTRY_POINT_FILENAME:
        return None
    node = PurePosixPath(path)
    parent_dir = node.parent.parent if node.name == INDEX_FILENAME else node.parent
    return ENTRY_POINT_FILENAME if str(parent_dir) == "." else f"{parent_dir}/{INDEX_FILENAME}"


def _check_reachability(paths: Sequence[str]) -> list[Finding]:
    known = set(paths)
    orphans = sorted(
        (path, parent)
        for path, parent in ((path, parent_index(path)) for path in known)
        if parent is not None and parent not in known
    )
    if orphans:
        return [
            Finding(
                FAIL,
                CHECK_REACHABILITY,
                f"{path} orphan — its parent index {parent} is absent from the path list",
            )
            for path, parent in orphans
        ]
    return [Finding(PASS, CHECK_REACHABILITY, f"{len(known)} paths, every parent index present")]


# --- the built tree: check 3, post-build check 4, the mf: guard ---------------


@dataclass(frozen=True)
class _Link:
    """One declared link: the destination as written, and whether it is an up-link."""

    target: str
    up: bool


def _tree_paths(kb_root: Path) -> dict[str, Path]:
    """Every authored ``.md`` under ``kb_root``: kb-root-relative path → the file.

    The crawl is ``kb_links.iter_markdown_files``, the toolchain's one Markdown walk
    — which skips ``.index/`` (with the rest of the never-crawled directories) at any
    depth — narrowed by ``EXCLUDE_NAMES``, the walk vocabulary ``kb_index_lib`` owns
    and ``verify_kb_metadata`` re-exports rather than re-spells. A local copy of
    either is the second definition that drifts from the tree every other tool sees.

    It is deliberately not a source of ``paths`` for check 3: a list this walk
    produced is the tree restated, and comparing the tree against itself cannot
    fail. A caller holding no independent list passes ``paths=None`` and check 3
    reports itself inapplicable.
    """
    return {
        path.relative_to(kb_root).as_posix(): path
        for path in kb_links.iter_markdown_files(kb_root)
        if path.name not in kb_index_lib.EXCLUDE_NAMES
    }


def _documents(kb_root: Path) -> dict[str, str]:
    """Every authored document's text, by kb-root-relative path.

    One read serves all three post-build checks, so no document is opened twice and
    none is read by one check and skipped by another.
    """
    return {relative: path.read_text(encoding="utf-8") for relative, path in _tree_paths(kb_root).items()}


def _check_tree_diff(paths: Sequence[str] | None, authored: Iterable[str]) -> list[Finding]:
    """3: the skeleton's path list and the built tree are one set, reported both ways.

    ``paths`` is ``None`` where the caller holds no path list independent of the
    tree, and the check then reports itself inapplicable rather than passing.
    Both sides of the comparison would be the same walk, so a ``PASS`` there
    would claim coverage the run does not have — three reported passes reading
    as three things confirmed when only two were asked.
    """
    if paths is None:
        return [
            Finding(
                FACT,
                CHECK_TREE_DIFF,
                "not applicable — this caller supplied no path list independent of the tree, "
                "so there is nothing to compare it against",
            )
        ]
    listed, built = set(paths), set(authored)
    findings = [
        Finding(FAIL, CHECK_TREE_DIFF, f"{path} listed — the skeleton names it, but no such file was built")
        for path in sorted(listed - built)
    ]
    findings += [
        Finding(FAIL, CHECK_TREE_DIFF, f"{path} unlisted — an authored document the skeleton does not name")
        for path in sorted(built - listed)
    ]
    return findings or [
        Finding(PASS, CHECK_TREE_DIFF, f"{len(listed)} listed paths and {len(built)} authored documents are one set")
    ]


def _resolve(source: str, target: str) -> str | None:
    """``target``, as written in ``source``, as a kb-root-relative path.

    ``None`` where the destination is not a path inside the tree — an absolute path,
    an external URL, or a target climbing out of ``kb-root/``. Such a destination is
    not a navigation edge here; whether it is a *broken* link is ``verify_md_links``'
    question, and answering it twice would be a second scanner over the same graph.
    """
    cleaned = kb_links.strip_target(target)
    if not cleaned or cleaned.startswith("/"):
        return None
    resolved = posixpath.normpath(posixpath.join(posixpath.dirname(source), cleaned))
    return None if resolved.startswith("..") else resolved


def _declared_links(text: str) -> list[_Link]:
    """The links ``text`` declares, read through the shared scanning primitives.

    ``strip_code`` blanks fenced blocks and inline spans, ``LINK_RE`` finds inline
    links and ``REF_DEF_RE`` the reference definitions — the same primitives
    ``verify_md_links`` and ``kb_cmd`` reach for, so this check walks the graph those
    tools see rather than one of its own. An up-link is a link whose text carries the
    marker; a reference definition carries none at its use site and can only ever be
    a down-link.
    """
    scrubbed = kb_links.strip_code(text)
    links = [
        _Link(target=match.group(1), up=UPLINK_MARKER in match.group(0))
        for match in kb_links.LINK_RE.finditer(scrubbed)
    ]
    links += [_Link(target=match.group(1), up=False) for match in kb_links.REF_DEF_RE.finditer(scrubbed)]
    return links


def _check_build_reachability(documents: Mapping[str, str]) -> list[Finding]:
    """4 post-build: down from the root by link chain, up from every node by up-link.

    Both directions, because each catches what the other cannot: the down walk finds
    a subtree nothing links into even though its members up-link correctly, and the
    up-link half finds a node whose parent chain is broken even though a sibling
    links to it.

    The up-link half checks *which* document it reaches, not merely that it reaches
    one: a leaf up-linking to its domain index instead of its own directory's index
    lands on a real document a level too high, so existence alone lets a tree that
    does not match its own parent relation through. The relation is
    :func:`parent_index`, the same one design-time reachability walks.

    *Scope, stated honestly*: the down walk follows every non-up link, so a document
    reached only by a cross-reference counts as reached. Nothing in the Markdown
    distinguishes a down-link from a cross-reference — the up-link marker is the only
    machine-checkable half of the convention — and inventing a distinction here would
    gate on a graph no other tool agrees with.
    """
    links = {source: _declared_links(text) for source, text in documents.items()}
    findings: list[Finding] = []

    for source in sorted(documents):
        if source == ENTRY_POINT_FILENAME:
            continue
        ups = [link for link in links[source] if link.up]
        if not ups:
            findings.append(
                Finding(
                    FAIL,
                    CHECK_REACHABILITY,
                    f"{source} no up-link — a non-root document carrying no {UPLINK_MARKER!r} link, "
                    "so nothing walks up from it",
                )
            )
            continue
        expected = parent_index(source)
        for link in ups:
            target = _resolve(source, link.target)
            if target not in documents:
                findings.append(
                    Finding(
                        FAIL,
                        CHECK_REACHABILITY,
                        f"{source} up-link dangles — its {UPLINK_MARKER!r} link to {link.target!r} "
                        "resolves to no authored document",
                    )
                )
            elif target != expected:
                findings.append(
                    Finding(
                        FAIL,
                        CHECK_REACHABILITY,
                        f"{source} up-link misparented — its {UPLINK_MARKER!r} link to {link.target!r} "
                        f"resolves to {target}, but its parent is {expected}",
                    )
                )

    if ENTRY_POINT_FILENAME not in documents:
        findings.append(
            Finding(FAIL, CHECK_REACHABILITY, f"{ENTRY_POINT_FILENAME} absent — the tree has no root to walk down from")
        )
    else:
        reached = {ENTRY_POINT_FILENAME}
        queue = [ENTRY_POINT_FILENAME]
        while queue:
            source = queue.pop()
            for link in links[source]:
                target = _resolve(source, link.target)
                if not link.up and target in documents and target not in reached:
                    reached.add(target)
                    queue.append(target)
        findings += [
            Finding(
                FAIL,
                CHECK_REACHABILITY,
                f"{path} unreachable — no down-link chain from {ENTRY_POINT_FILENAME} reaches it",
            )
            for path in sorted(documents.keys() - reached)
        ]

    return sorted(findings, key=lambda finding: finding.detail) or [
        Finding(
            PASS,
            CHECK_REACHABILITY,
            f"{len(documents)} documents, every one up-linked to its parent index "
            f"and reachable from {ENTRY_POINT_FILENAME}",
        )
    ]


def _check_id_guard(documents: Mapping[str, str]) -> list[Finding]:
    """The ``mf:`` guard: a manifest id is per-run and never enters ``kb-root/``.

    A literal token grep, which is the whole point of a constant prefix: misuse is
    checkable by one token rather than by set membership over hundreds of ids. It
    reads the document as written rather than the scrubbed copy the link scan reads —
    a per-run id fenced in a code block is in the built tree just the same.
    """
    findings: list[Finding] = []
    for path in sorted(documents):
        hits = [number for number, line in enumerate(documents[path].splitlines(), start=1) if ID_PREFIX in line]
        if hits:
            findings.append(
                Finding(
                    FAIL,
                    CHECK_ID_GUARD,
                    f"{path}:{hits[0]} carries the manifest-id token {ID_PREFIX!r} ({len(hits)} lines in this file) — "
                    "manifest ids are per-run and never enter the KB",
                )
            )
    return findings or [Finding(PASS, CHECK_ID_GUARD, f"no {ID_PREFIX!r} token in {len(documents)} authored documents")]


# --- the entry points ---------------------------------------------------------


def validate_skeleton(*, manifest: Manifest, skeleton: survey_skeleton.Skeleton) -> list[Finding]:
    """Run the pre-build checks and return their findings in report order.

    Two parameters, both keyword-only and both required, neither of which arms or
    disarms anything: there is no severity argument, no bound, and no mode. The
    skeleton is the one ``skeleton.derive`` produced from this manifest — a
    caller passing a skeleton derived from a different one is what check 1
    reports.
    """
    return [
        *_check_partition(manifest, skeleton),
        *_shape_facts(manifest, skeleton),
        *_check_reachability(skeleton.paths),
    ]


def validate_build(*, paths: Sequence[str] | None, kb_root: Path) -> list[Finding]:
    """Run the post-build checks over the built tree and return them in report order.

    ``paths`` is the whole path list the caller independently holds — every node,
    index documents included, because an index claims no leaf of its own and a
    list that dropped them would read their children as orphans — or ``None``
    where the caller holds none. ``kb_root`` is the built tree.

    ``None`` disarms nothing: check 3 reports ``FACT`` instead of ``PASS``, which
    is the weaker claim, and every other check runs unchanged. What it refuses to
    do is let a caller comparing the tree against a copy of its own walk book
    that as a passed check.

    Two parameters, both keyword-only, neither of which arms or disarms anything: no
    severity, no mode, no exclusion list a caller could widen until a check stops
    seeing its violations.
    """
    if not kb_root.is_dir():
        raise NotADirectoryError(f"{kb_root} is not a directory; post-build validation reads a built tree")
    documents = _documents(kb_root)
    return [
        *_check_tree_diff(paths, documents.keys()),
        *_check_build_reachability(documents),
        *_check_id_guard(documents),
    ]


def exit_code(findings: Iterable[Finding]) -> int:
    """``EXIT_OK`` iff no ``FAIL``. ``FACT`` never gates, and nothing tiers."""
    return EXIT_VIOLATION if any(finding.status == FAIL for finding in findings) else EXIT_OK
