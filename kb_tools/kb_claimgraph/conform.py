"""Stage A — the conformance gate over SPEC.md's Document-Tree Contract.

**A non-conforming tree does not produce a missing graph, it produces a wrong
one.** So the run opens here, writes nothing, and stops on the first failed
assertion naming the point it belongs to. The check is cheap because the
contract is mechanical, and it is not a defensive parser: it *asserts* the
guarantees rather than tolerating their absence. A tolerant reader would be
building for a contract that does not exist while the one that does went
unenforced.

**The cleanliness check is the declared pass's double-run guard.** That pass
mints ids; a second run over its own output would mint a second set and double
the graph. Point 14 guarantees a fresh tree carries none of the artifacts it
writes, so the presence of any one of them means the input is not a fresh tree,
and :func:`gate` refuses.

**The discovered pass's guard is a different one, and it is per document.** That
pass extends the tree additively — SPEC declares nodes additively in leaf
frontmatter — so a whole-tree refusal on "any frontmatter at all" would forbid
it entirely. What it must not do is re-mint, so the question moves from the tree
to the document: :func:`determination` says whether one document has already had
its claim declaration settled, and :func:`pass_two_gate` partitions the tree by
that answer.

**The forbidden artifacts are read off the module that composes them**
(:data:`tree.METADATA_OPENERS`, itself read off :mod:`kb_tools.kb_write.render`)
rather than re-typed here. A cleanliness check carrying its own copy of the
spellings would pass a tree holding an artifact whose spelling had since moved,
which is the silent-clean failure this gate exists to prevent.

**One clause of point 14 is not checked, and that is a ruling rather than an
omission.** Point 14 also forbids a ``.index/`` directory, and what it describes
is what the *front end* leaves behind. Between that front end and this stage
runs ``graph-init``, whose landed behaviour is to create ``.index/`` and install
the runner include line over an already-populated tree (ARCHITECTURE.md,
Initialising the Claim-Graph Spine). Refusing on its presence here would refuse
every run of this stage as designed. Derived space is out of this gate's scope
in both directions: nothing under it is authored, and nothing this stage writes
lands there.
"""

from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

from .. import kb_index_lib, kb_util, verify_md_links
from .assemble import UNSCANNED_REASON
from .identify import UNANCHORED_REASON
from .report import ClaimGraphError
from .tree import ANCHOR_RE, DECLARING_KINDS, METADATA_OPENERS, Tree, resolve, unquote

#: The authored artifacts point 14 forbids: exactly the line-shaped metadata the
#: write API inserts, which :data:`tree.METADATA_OPENERS` already spells off the
#: module that composes them. Point 14's fourth item — a ``claims:`` key — is a
#: *field of* the frontmatter block and cannot exist outside one, so the block's
#: absence is what checks it. A bare substring test would instead read an
#: author's own sentence about a framework's core claims as a metadata key,
#: which is a gate refusing a conforming tree.
POINT_14_ARTIFACTS: tuple[str, ...] = METADATA_OPENERS


class ConformanceError(ClaimGraphError):
    """The input tree fails the contract. ``check`` names the point it fails."""


def _refuse(point: int, detail: str) -> ConformanceError:
    return ConformanceError(f"point-{point}", detail)


def _entry_point(tree: Tree) -> None:
    """Point 1 — the root lists every volume, and nothing else."""
    root = kb_index_lib.ENTRY_POINT_FILENAME
    if root not in tree.documents:
        raise _refuse(1, f"no {root} at the KB root")
    volumes = {
        path for path in tree.documents if path.count("/") == 1 and path.endswith(f"/{kb_index_lib.INDEX_FILENAME}")
    }
    listed = set(tree.children[root])
    if listed != volumes:
        raise _refuse(
            1,
            f"{root}'s link set is not the depth-1 volume-index set: unlisted {sorted(volumes - listed)}, "
            f"listed but not a volume index {sorted(listed - volumes)}",
        )


def _uplinks(tree: Tree) -> None:
    """Points 3 and 4 — line 1 is an up-link, and the parent names it back."""
    for path in sorted(tree.documents):
        if path == kb_index_lib.ENTRY_POINT_FILENAME:
            continue
        parent = tree.parents.get(path)
        if parent is None:
            raise _refuse(3, f"{path} does not open with an up-link carrying {kb_index_lib.UPLINK_MARKER!r}")
        if parent not in tree.documents:
            raise _refuse(3, f"{path}'s up-link names {parent}, which is not a document of this tree")
        if path not in tree.children[parent]:
            raise _refuse(4, f"{path} up-links to {parent}, whose child list does not name it")


def _path_shape(tree: Tree) -> None:
    """Point 2 — descendants decide the filename, in both directions."""
    index = kb_index_lib.INDEX_FILENAME
    for path, children in sorted(tree.children.items()):
        if path == kb_index_lib.ENTRY_POINT_FILENAME:
            continue
        sits_at_index = path.rsplit("/", 1)[-1] == index
        if children and not sits_at_index:
            raise _refuse(2, f"{path} lists {len(children)} children but does not sit at <dir>/{index}")
        if sits_at_index and not children:
            raise _refuse(2, f"{path} sits at an index path but lists no children")


def _spine(tree: Tree) -> None:
    """Point 5 — total, acyclic, and inverting the up-link relation exactly."""
    root = kb_index_lib.ENTRY_POINT_FILENAME
    seen: set[str] = set()
    stack = [(root, (root,))]
    while stack:
        path, trail = stack.pop()
        if path in seen:
            raise _refuse(5, f"the down-link spine is not acyclic: {' -> '.join(trail)}")
        seen.add(path)
        stack.extend((child, trail + (child,)) for child in tree.children[path])
    unreached = sorted(set(tree.documents) - seen)
    if unreached:
        raise _refuse(5, f"{len(unreached)} document(s) unreachable from {root} by down-links: {unreached[:5]}")
    for path, children in sorted(tree.children.items()):
        for child in children:
            if tree.parents.get(child) != path:
                raise _refuse(5, f"{path} lists {child} as a child, but {child}'s up-link does not name {path}")


def _markdown_links(tree: Tree) -> None:
    """Point 7, for the link form the repo-wide dead-link gate can see."""
    broken = verify_md_links.scan(tree.root, check_ids_enabled=False)
    if broken:
        first = broken[0]
        raise _refuse(
            7,
            f"{len(broken)} dead markdown link(s), first at "
            f"{first.file.relative_to(tree.root)}:{first.line} {first.kind} {first.target!r}",
        )


def _anchors(tree: Tree) -> None:
    """Point 7, for the link form it cannot.

    ``kb_links.LINK_RE`` matches ``[text](target)`` and nothing else, so every
    rewritten cross-reference in the tree is invisible to the gate above. This
    stage resolves them itself, against the same document set. A bare fragment
    is not a failure — point 7 admits a label that cannot be resolved rendering
    as its own text, and a fragment with no path in front of it is what that
    looks like.
    """
    for path, document in sorted(tree.documents.items()):
        for match in ANCHOR_RE.finditer(unquote(document.text)):
            target, _, _ = match.group(1).partition("#")
            if target and resolve(path, target) not in tree.documents:
                raise _refuse(7, f"{path}: cross-reference anchor {match.group(1)!r} lands on no document")


def _cleanliness(tree: Tree) -> None:
    """Point 14 — and the double-run guard, over every file the tree holds."""
    for path in sorted(tree.root.rglob("*.md")):
        relative = path.relative_to(tree.root)
        if set(relative.parts[:-1]) & kb_index_lib.EXCLUDE_DIRS:
            continue
        text = path.read_text(encoding="utf-8")
        for artifact in POINT_14_ARTIFACTS:
            if artifact in text:
                raise _refuse(
                    14,
                    f"{relative.as_posix()} already carries {artifact!r}. This is not a tree the front end "
                    f"just wrote: this stage mints ids, so a second run over its own output would mint a "
                    f"second set and double the graph. Rebuild the tree from the corpus and run once",
                )


#: The structural half of the gate, ordered by what each check's own subject
#: depends on rather than by the contract's numbering. Point 7 comes first
#: because every relation below it is *derived from links*: a dead link is a
#: phantom child, and a tree checked for its shape before its links resolve
#: reports the phantom's consequence — a leaf with a child — instead of the dead
#: link that invented it. Both passes rely on every one of these, and neither
#: adds to them: the shape of the tree does not change when metadata lands on
#: it.
_STRUCTURE = (_markdown_links, _anchors, _entry_point, _uplinks, _path_shape, _spine)

#: Point 14 comes last because a fresh tree passes every structural check
#: anyway, so a double run is refused by naming the artifact rather than a
#: symptom of one.
_CHECKS = (*_STRUCTURE, _cleanliness)


def gate(tree: Tree) -> None:
    """Assert every guarantee the later stages rely on, or raise :class:`ConformanceError`."""
    for check in _CHECKS:
        check(tree)


# --- the discovered pass's entry condition ----------------------------------


class Determination(StrEnum):
    """Whether one document's claim declaration has already been settled.

    ``AWAITING`` is the only value that admits a document to a minting stage.
    The other four each close the document to one, and they close it for
    different reasons: three say the question is answered — by a declared claim,
    by somebody's authored reason, or by the document not being asked — and
    ``UNANCHORED`` says it is not, but that this build has already tried and
    could not. That last one is not a finding about the document, and it is the
    distinction that keeps a failure to point into a document from being written
    down as the document stating nothing.
    """

    #: Carries the unscanned reason: settled by nothing, and awaiting this pass.
    AWAITING = "awaiting"
    #: Declares ids. Its claims exist; re-minting would double them.
    HOSTS_CLAIMS = "hosts-claims"
    #: Carries a reason somebody wrote. A hand or a stage decided this document
    #: states no claim, and that decision is not this pass's to overturn.
    AUTHORED_NO_CLAIM = "authored-no-claim"
    #: Carries the unanchored reason: claim identification ran over this
    #: document's prose, named results in it, and anchored none of them. It
    #: leaves ``AWAITING`` so a run finishes rather than halting on it, and it
    #: reopens for nobody — a re-run would buy the same failure at the same
    #: price, and what the state is for is being reported loudly enough that a
    #: person looks.
    UNANCHORED = "unanchored"
    #: Declares neither, and is a kind that must. The tier-1 coverage check
    #: refuses this too; it is named here so the refusal arrives before a write.
    UNDECLARED = "undeclared"


#: The reason literals this module compares by identity, and what each says.
#: **Both literals are load-bearing.** :data:`assemble.UNSCANNED_REASON` was
#: written as an honest description of what a block-hosted build did not look at,
#: and it is *also* the resumption marker: it is the one ``no-claim:`` reason
#: that asserts nothing about the document, so it is the one a later pass may
#: replace. :data:`identify.UNANCHORED_REASON` is the same pattern for the run
#: that tried and failed. Every other reason is somebody's finding. Reword either
#: without moving this table with it and the documents carrying it read as
#: finished ones — the pass that would have read them reads nothing, reports
#: success, and the claims stay unfound.
_RESERVED_REASONS: dict[str, Determination] = {
    UNSCANNED_REASON: Determination.AWAITING,
    UNANCHORED_REASON: Determination.UNANCHORED,
}


def determination(fields: dict) -> Determination:
    """What ``fields`` — one document's parsed frontmatter — says about its claims."""
    if fields.get("claims"):
        return Determination.HOSTS_CLAIMS
    reason = fields.get("no-claim")
    if not isinstance(reason, str) or not reason:
        return Determination.UNDECLARED
    return _RESERVED_REASONS.get(reason, Determination.AUTHORED_NO_CLAIM)


@dataclass(frozen=True)
class PassTwoState:
    """The tree partitioned by :func:`determination`, over the kinds that declare.

    ``awaiting`` is what a minting stage may read. ``hosting`` is what the
    dependency stage reads — a claim has to exist before an edge can name it.
    ``determined`` is refused to both, and is reported rather than dropped so a
    run states how much of the tree it declined to touch; a document a previous
    run could anchor nothing in is in there, because a re-run would buy the same
    failure at the same price.
    """

    awaiting: tuple[str, ...] = ()
    hosting: tuple[str, ...] = ()
    determined: tuple[str, ...] = ()


def pass_two_gate(tree: Tree) -> PassTwoState:
    """The discovered pass's entry condition: the structural checks, then the partition.

    Refuses a tree the declared pass has not run over — no frontmatter is not an
    additive starting point, it is a tree with no claims to attach anything to —
    and refuses a document of a declaring kind that carries neither declaration,
    which is a half-written pass 1 rather than an input.
    """
    for check in _STRUCTURE:
        check(tree)

    awaiting: list[str] = []
    hosting: list[str] = []
    determined: list[str] = []
    for path in sorted(tree.documents):
        fields = kb_index_lib.parse_frontmatter(tree.documents[path].text)
        if not fields:
            raise ConformanceError(
                "frontmatter-absent",
                f"{path} carries no frontmatter block, so the declared pass has not run over this tree. "
                f"The discovered pass extends an authored graph additively and has nothing to extend here; "
                f"run the block-hosted build first",
            )
        if fields.get("kind") not in DECLARING_KINDS:
            continue
        verdict = determination(fields)
        if verdict is Determination.UNDECLARED:
            raise ConformanceError(
                "determination",
                f"{path} is a {fields.get('kind')} declaring neither claims nor a no-claim reason. The "
                f"declared pass writes exactly one of the two onto every document of that kind, so this "
                f"tree is not its complete output",
            )
        {
            Determination.AWAITING: awaiting,
            Determination.HOSTS_CLAIMS: hosting,
            Determination.AUTHORED_NO_CLAIM: determined,
            Determination.UNANCHORED: determined,
        }[verdict].append(path)

    return PassTwoState(awaiting=tuple(awaiting), hosting=tuple(hosting), determined=tuple(determined))


def spine_seeded(kb_root: Path, *, targets_installed: bool) -> str | None:
    """Why the claim-graph spine is absent, or ``None`` when it is there.

    ``graph-init`` is what installs it, and this stage assumes it has run rather
    than seeding anything itself: the two writes it makes — the derived-index
    directory and the runner include line — belong to the verb that owns them,
    and stage G is the include line's only consumer.
    """
    if not (kb_root / kb_util.INDEX_DIRNAME).is_dir():
        return f"{kb_root.name}/{kb_util.INDEX_DIRNAME}/ does not exist"
    if not targets_installed:
        return "the runner file carries no KB include line, so there are no refresh and verify targets to run"
    return None
