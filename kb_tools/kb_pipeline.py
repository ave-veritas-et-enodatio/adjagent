"""The KB build pipeline's state machine: stage table, ledger, checklist.

**The stage table lives here and only here.** The ordered stage vocabulary is
the in-code constant :data:`STAGES`; agent definitions read it out of
``kb_util show-status`` rather than enumerating stages of their own, so there
is exactly one source and no definition can drift from it.

**The ledger is the git commit trail.** There is no pipeline metadata file:
the KB and the repository's history are the only durable state. A stage
boundary is recorded by making a commit whose subject carries the stage id,
and status is read back with ``git log --grep``.

Subject format — stable, greppable, and machine-readable::

    kb-build: <stage-id> | <display name>

``<stage-id>`` is the first whitespace-free token after the ``kb-build: ``
prefix and is always one of :data:`STAGE_IDS`. An optional body paragraph
follows the subject: the charter path on ``start``, the ``--note`` text
``advance-step`` carries on any stage.

**Stage-addressed and declarative.** Recording is idempotent per stage — a
re-record reports and exits 0 — and out-of-order recording is refused with
the checklist, so a caller with a wrong world-model is corrected rather than
obeyed.

Stdlib only.
"""

import contextlib
import re
from collections.abc import Callable, Sequence
from dataclasses import dataclass, replace
from pathlib import Path

from kb_tools import kb_index_lib, kb_util

# Exit codes. 0/2 keep kb_util's meanings (success; environment unfit, which
# includes an unresolvable root and a failed git invocation) and 3 stays
# reserved for kb_util's already-populated KB, so a caller can read one ladder
# across the whole CLI.
EXIT_OK = 0
EXIT_GIT_FAILURE = 2
EXIT_OUT_OF_ORDER = 4
EXIT_ALREADY_STARTED = 5
EXIT_POSTCONDITION_FAILED = 6

# The one greppable marker. `git log --grep` finds candidate commits; the
# subject regex is what actually decides, so a note quoting the prefix in a
# commit body cannot forge a ledger entry.
LEDGER_PREFIX = "kb-build:"
_SUBJECT_RE = re.compile(rf"^{re.escape(LEDGER_PREFIX)} ([^\s|]+) \| ")

# Line prefixes. The checklist is the only block matching `^\[[x* ]\] `;
# everything else carries a word in the brackets so the two cannot be
# confused by a parser or by a reader.
_TAG = f"[{LEDGER_PREFIX[:-1]}]"
CARD_PREFIX = "[card]"

# Stated on every render, unconditionally. The display obligation belongs at
# read-time because that is where it was being missed.
CONTRACT_LINE = "Any message to the user opens with the checklist above, verbatim, in the message body."


# The consumer-side invocation every generated card command is built from.
_INVOCATION = "PYTHONPATH=.claude/agents python3 -m kb_tools.kb_util"

# The build's scratch layout, repo-root-relative. One definition per path, read
# by the cards that name it, by the coverage checks that read it, and by
# `kb_driver.steps`, which imports these rather than restating them: a card that
# spelled its own path could send an artifact somewhere the tool never looks.
SCRATCH_RELROOT = f"{kb_util.SCRATCH_DIRNAME}/{kb_util.SCRATCH_BUILD_DIRNAME}"

# Where the build charter lands, repo-root-relative and tracked. Not under the
# scratch tree with the rest of the build's working artifacts: the start
# commit's body names this path permanently, and scratch is wiped between
# sessions — a ledger entry pointing into a wiped tree names nothing. Not under
# kb-root/ either, which holds the distillation and is walked as authored KB
# content by refresh and both verifiers.
CHARTER_RELPATH = "kb-build-charter.md"


class PipelineError(RuntimeError):
    """A git invocation the pipeline depends on failed."""


# --- generated card obligations -------------------------------------------
#
# A card obligation is a plain string unless it names something that would go
# stale as one: a stage id in a command, a runner that differs per consumer, or
# a loop cap the driver enforces. Those are built at render time from the
# stage, the repo, and the cap constants, so a card can never advertise the
# wrong stage id, the wrong runner, or a cap the run does not use.


def _kb_util_command(op: str, *arguments: str, values: str | None = None) -> str:
    """One sanctioned ``kb_util`` invocation, as a card renders it.

    Every generated command below is built here, so the consumer-side prefix has
    one spelling and a card can never name a second front end. ``op`` is always
    a ``kb_util`` op constant, never a literal, so a card cannot advertise a
    subcommand the CLI does not have.

    ``values`` is the values file a metadata op is to be called on, given as the
    placeholder alone: the flag in front of it comes from ``kb_util``, so the
    card carries the whole of the op's required argument without any card site
    spelling the flag. Every other argument is passed positionally, because
    every other flag belongs to one op rather than to a whole class of them.
    """
    tail = () if values is None else (kb_util.VALUES_FLAG, values)
    return " ".join((_INVOCATION, op, *arguments, *tail))


def _front_end_command(module: str, flags: Sequence[str]) -> str:
    """One build front end's invocation, as a card renders it.

    ``kb_docgraph`` and ``kb_claimgraph`` are module CLIs rather than
    ``kb_util`` ops, so they are built from ``kb_util``'s own module-invocation
    and flag helpers for the reason :func:`_kb_util_command` is built from its
    op constants: a card cannot name a front end that does not exist, and cannot
    spell one a way the consumer does not run.
    """
    return " ".join((kb_util.module_invocation(module), *flags))


@dataclass(frozen=True)
class RecordStep:
    """The card's record obligation, expanded to the full sanctioned command.

    ``note`` is the spec's guidance for what to record, rendered as an
    angle-bracketed placeholder inside ``--note``; ``aside`` is a trailing
    parenthetical. The stage id comes from the stage being rendered, never
    from a hand-written string.

    The first stage's record is ``open-build``, which performs it: the seed,
    the charter and the ``start`` boundary are one act, and the boundary is not
    separately callable on the path an agent walks. What the card names is the
    call that records the stage it fronts, so a stage whose record moved into
    another op renders that op.
    """

    note: str | None = None
    aside: str | None = None

    def render(self, stage: "Stage", repo_root: Path) -> str:
        if stage.id == FIRST_STAGE_ID:
            command = _kb_util_command(kb_util.OP_OPEN_BUILD, f"{kb_util.CHARTER_VALUES_FLAG} <values-file>")
        else:
            command = _kb_util_command(kb_util.OP_ADVANCE_STEP, f"--stage {stage.id}")
        if self.note:
            command += f' --note "<{self.note}>"'
        return f"record: {command}" + (f"  ({self.aside})" if self.aside else "")


@dataclass(frozen=True)
class StageStatusStep:
    """The stage-coverage read, expanded to the full sanctioned command.

    The stage id comes from the stage being rendered, exactly as
    :class:`RecordStep`'s does, so a card asks about the stage it fronts and
    never about another.
    """

    def render(self, stage: "Stage", repo_root: Path) -> str:
        command = _kb_util_command(kb_util.OP_SHOW_STAGE_STATUS, f"--stage {stage.id}")
        return (
            f"ask what this stage still has to cover rather than reconstructing it: {command}"
            "  (each unit comes back with where it is satisfied from — dispatch against those and "
            "compose no path of your own; however the work is divided, that set is what has to come back)"
        )


def _refresh_line(repo_root: Path) -> str:
    """The refresh obligation, named in the consumer's own runner.

    A justfile consumer must be told ``just kb-refresh``, a Makefile consumer
    ``make kb-refresh``; kb_util already detects which, so the card asks it
    rather than hardcoding one. Both steps below open with this, so the
    refresh-only obligation is literally the gate's first half.
    """
    return f"run `{kb_util.refresh_cmd(repo_root)}`"


@dataclass(frozen=True)
class GateStep:
    """The refresh-then-verify obligation, named in the consumer's own runner."""

    def render(self, stage: "Stage", repo_root: Path) -> str:
        return f"{_refresh_line(repo_root)} then `{kb_util.verify_cmd(repo_root)}`"


# The loop caps. Each has exactly one definition here, the cards render from
# it, and the driver's step table imports it — so a run can never contradict
# the card it just printed, because there is nothing to keep in sync.
PHASE_5_FIX_CAP = 1


def _cap_values() -> dict[str, int]:
    """The cap slots a :class:`CappedLine` may name, read at render time.

    Built per render rather than once at import, so the constants above stay
    the only definition: nothing holds a copy of a cap taken before it moved.
    """
    return {
        "phase_5_fix_cap": PHASE_5_FIX_CAP,
    }


@dataclass(frozen=True)
class CappedLine:
    """A card obligation naming a loop cap, filled from the constants above.

    Only lines declared this way are formatted. A blanket format pass over
    every card string would be a trap: any card may carry a literal brace.
    """

    template: str

    def render(self, stage: "Stage", repo_root: Path) -> str:
        return self.template.format_map(_cap_values())


CardItem = str | RecordStep | StageStatusStep | GateStep | CappedLine


# --- coverage ---------------------------------------------------------------
#
# A stage's coverage is the units it must cover, each carrying where it is
# satisfied from and whether it is; a stage whose coverage is incomplete cannot
# be recorded.
#
# Coverage checks EXISTENCE, per unit, and existence is not quality.
# `_check_verify_gates` is the one exception: it runs the three verifiers, and
# judging the work is theirs alone. Every path checked is one the established layout contract
# already names, and where a stage's units are enumerated from an artifact the
# toolchain wrote, that artifact also names them — nothing here invents a
# location or a count.


@dataclass(frozen=True)
class CheckContext:
    """What a coverage check may look at: the repo, and the record's own arguments."""

    repo_root: Path
    note: str | None = None
    charter: str | None = None
    #: This build spent no model call. A property of the *build*, stated by the
    #: record, and the only thing a caller says here: which coverage units it
    #: makes vacuous is decided below and is not a caller's to name. A record
    #: cannot waive a check; it can only say what the build was.
    no_inference: bool = False


@dataclass(frozen=True, kw_only=True)
class CoverageUnit:
    """One unit a stage must cover.

    ``source`` is where the unit is satisfied *from* — an artifact path, a path
    prefix, or the command whose outcome decides it. ``detail`` states the
    condition an unsatisfied unit fails, and is what a refusal carries beside
    the id.

    ``vacuous`` separates the two ways a unit is satisfied: an artifact was
    found, or there was nothing to look for. Both are ``satisfied``, so without
    the flag nothing distinguishes them, and a build that covered nothing reads
    exactly like one that covered everything. It is reported and never gates.

    ``from_argument`` marks a unit satisfied from the record's own arguments
    rather than from the tree. A read has no record arguments, so such a unit
    is unsatisfiable there for a reason that is not a fact about the KB, and
    :func:`stage_status` says which rather than reporting a tree it never
    looked at.

    **``asserts_own_work`` is the two kinds of coverage check, and it has no
    default.** A unit either asserts *this stage did its work* — pointless to
    demand of a build that excluded the work, so it goes vacuous there
    (:func:`_excused`) — or it asserts *the state handed to the next stage is
    valid for it*, which no path to the boundary excuses, however the state
    got there. Declared on the unit rather than on the stage because a check
    can be attached to more than one stage: ``_check_verify_gates`` is
    ``depends-attributed``'s and ``phase-3a``'s alike, and a per-stage
    classification would let the two disagree about one check — which is
    exactly how a validity gate goes missing from the stage that needed it.
    """

    id: str
    source: str
    satisfied: bool
    asserts_own_work: bool
    detail: str = ""
    vacuous: bool = False
    from_argument: bool = False

    def __post_init__(self) -> None:
        if self.vacuous and not self.satisfied:
            raise ValueError("a unit with nothing to check is satisfied; there is nothing left to fail")


@dataclass(frozen=True)
class CoverageReport:
    """A stage's declared units, or the reason it declares none.

    Two constructors and no third: :meth:`declared` refuses an empty unit
    tuple, :meth:`undeclared` carries a reason and holds no units, and a report
    holding neither cannot be built at all — which is what keeps
    ``all(unit.satisfied for unit in ())`` out of reach. Stated as the
    biconditional it is: ``reason`` is non-``None`` exactly when there are no
    units.

    Units are ordered by id, so two asks of an unchanged tree render alike.

    ``unit_class`` is what the units are, said once and in the negative — what
    a report with nothing satisfied is missing, stated as a class rather than
    as its instances. A decomposed report carries it and a degenerate one does
    not, its single unit being its own class.
    """

    units: tuple[CoverageUnit, ...]
    reason: str | None
    degenerate: bool = False
    unit_class: str = ""

    def __post_init__(self) -> None:
        if not self.units and self.reason is None:
            raise ValueError("a coverage report declaring no units must carry the reason it declares none")
        if self.units and self.reason is not None:
            raise ValueError("a coverage report carrying units declares them, so it holds no reason")
        if self.degenerate and len(self.units) != 1:
            raise ValueError("a degenerate report is the one unit standing for a stage with no decomposition")
        if self.units and bool(self.unit_class) == self.degenerate:
            raise ValueError(
                "a decomposed report states what its units are as a class; "
                "a degenerate report's one unit is that class already"
            )
        object.__setattr__(self, "units", tuple(sorted(self.units, key=lambda unit: unit.id)))

    @classmethod
    def declared(
        cls, units: Sequence[CoverageUnit], *, degenerate: bool = False, unit_class: str = ""
    ) -> "CoverageReport":
        """The units a stage declares, and what they are as a class. Refuses an empty tuple."""
        return cls(units=tuple(units), reason=None, degenerate=degenerate, unit_class=unit_class)

    @classmethod
    def undeclared(cls, reason: str) -> "CoverageReport":
        """No units, and why.

        Three shapes reach here, and none of them is coverage of any amount: a
        declaring artifact that could not be read, one that declares nothing to
        cover, and one whose declarations cannot be told apart on disk.
        """
        return cls(units=(), reason=reason)


def _file_unit(
    *, unit_id: str, path: Path, detail: str, asserts_own_work: bool, from_argument: bool = False
) -> CoverageUnit:
    return CoverageUnit(
        id=unit_id,
        source=str(path),
        satisfied=path.is_file(),
        asserts_own_work=asserts_own_work,
        detail=detail,
        from_argument=from_argument,
    )


def _check_charter_written(ctx: CheckContext) -> CoverageReport:
    """The charter the record names is on disk, where the record names one.

    Argument-derived, over three conditions the unit tells apart. ``None`` is a
    *read*, which holds no record argument to check and so cannot be satisfied
    from the tree. The empty string is a record that named no charter, which is
    a build carrying none: there is nothing to look for, so the unit is vacuous
    rather than failed — a charter is what a build was told, and a build told
    nothing but its sources is an ordinary build. A path is checked on disk.
    """
    if ctx.charter is None:
        unit = CoverageUnit(
            id="charter",
            source="the record's charter argument",
            satisfied=False,
            asserts_own_work=True,
            detail="the record names no charter",
            from_argument=True,
        )
    elif not ctx.charter:
        unit = CoverageUnit(
            id="charter",
            source="the record's charter argument",
            satisfied=True,
            asserts_own_work=True,
            detail="this build carries no charter, so there is none to find",
            vacuous=True,
            from_argument=True,
        )
    else:
        unit = _file_unit(
            unit_id="charter",
            path=ctx.repo_root / ctx.charter,
            detail="no charter stands where the record names one",
            asserts_own_work=True,
            from_argument=True,
        )
    return CoverageReport.declared((unit,), degenerate=True)


def _check_document_tree(ctx: CheckContext) -> CoverageReport:
    """The tree the document graph writes: an entry point with a volume beside it.

    ``kb_util.document_tree_present`` is the same precondition ``graph-init``
    and ``open-build`` already refuse on, asked here as this stage's coverage —
    one predicate, three callers, and no second reading of what "a tree is
    there" means.
    """
    return CoverageReport.declared(
        (
            CoverageUnit(
                id="document-tree",
                source=str(kb_util.kb_root(ctx.repo_root) / kb_index_lib.ENTRY_POINT_FILENAME),
                satisfied=kb_util.document_tree_present(ctx.repo_root),
                asserts_own_work=True,
                detail=f"{kb_util.KB_DIRNAME}/ holds no entry point with a volume directory beside it",
            ),
        ),
        degenerate=True,
    )


def _check_spine_seeded(ctx: CheckContext) -> CoverageReport:
    """``graph-init``'s two writes: the derived-index directory, and the runner include line.

    Two units and not one, because they fail for different reasons and are
    restored by the same call for different halves of it — a seed that created
    the directory and could not find a runner file to install into is a
    different state from one that never ran.
    """
    kb = kb_util.kb_root(ctx.repo_root)
    return CoverageReport.declared(
        (
            CoverageUnit(
                id="derived-index",
                source=str(kb / kb_util.INDEX_DIRNAME),
                satisfied=(kb / kb_util.INDEX_DIRNAME).is_dir(),
                asserts_own_work=True,
                detail="the derived-index directory was never created",
            ),
            CoverageUnit(
                id="runner-targets",
                source=kb_util.verify_cmd(ctx.repo_root),
                satisfied=kb_util.targets_installed(ctx.repo_root),
                asserts_own_work=True,
                detail="this repository's runner file carries no KB include line",
            ),
        ),
        unit_class="the claim-graph spine was never seeded",
    )


def _tree_documents(repo_root: Path) -> dict[str, str]:
    """Every document of the tree by kb-root-relative path, with its text.

    ``kb_index_lib``'s walk, which is the one the verifiers and the claim-graph
    stages are both checked against. **Read from there and never from
    ``kb_claimgraph``**: that package reads this stage table, so an import in
    this direction would close a cycle — and this module holding no import of
    it at all is what makes the cycle impossible rather than a thing held apart
    by where a line sits.
    """
    return kb_index_lib.document_texts(kb_util.kb_root(repo_root))


def _check_claims_declared(ctx: CheckContext) -> CoverageReport:
    """The declared pass's universal product: a metadata block on every document.

    Which claims a document declares is the corpus's answer and may be none;
    that it carries the block saying so is this stage's, for every document
    without exception. An empty tree fails rather than passing vacuously — a
    walk that found nothing to stamp has not stamped everything.
    """
    from kb_tools.kb_write.render import FRONTMATTER_OPENER

    documents = _tree_documents(ctx.repo_root)
    missing = sorted(path for path, text in documents.items() if FRONTMATTER_OPENER not in text)
    detail = (
        f"{kb_util.KB_DIRNAME}/ holds no document to stamp"
        if not documents
        else f"{len(missing)} of {len(documents)} document(s) carry no metadata block: {', '.join(missing[:5])}"
    )
    return CoverageReport.declared(
        (
            CoverageUnit(
                id="frontmatter",
                source=str(kb_util.kb_root(ctx.repo_root)),
                satisfied=bool(documents) and not missing,
                asserts_own_work=True,
                detail=detail,
            ),
        ),
        degenerate=True,
    )


def _check_claims_discovered(ctx: CheckContext) -> CoverageReport:
    """Claim discovery's exit condition: no document is still awaiting a reading.

    The declared pass writes one reason — ``kb_index_lib.UNSCANNED_REASON``, its
    own statement that no claim has been looked for in this document's prose —
    and discovery's whole job is to replace every instance of it. A document
    still carrying it is a document this stage did not reach.
    """
    documents = _tree_documents(ctx.repo_root)
    awaiting = sorted(path for path, text in documents.items() if kb_index_lib.UNSCANNED_REASON in text)
    detail = (
        f"{kb_util.KB_DIRNAME}/ holds no document to read"
        if not documents
        else f"{len(awaiting)} document(s) still await a reading: {', '.join(awaiting[:5])}"
    )
    return CoverageReport.declared(
        (
            CoverageUnit(
                id="determinations",
                source=str(kb_util.kb_root(ctx.repo_root)),
                satisfied=bool(documents) and not awaiting,
                asserts_own_work=True,
                detail=detail,
            ),
        ),
        degenerate=True,
    )


def _check_verify_gates(ctx: CheckContext) -> CoverageReport:
    """The three verifiers, green. Two stages share it, for two different reasons.

    ``phase-3a`` is the tail's entry gate. ``depends-attributed`` is the head's
    exit, and it is this rather than an artifact check because dependency
    attribution is the one stage whose product cannot be found by looking: it
    writes ``- depends-on:`` bullets, how many is the corpus's answer, and an
    author who cross-referenced nothing leaves a tree indistinguishable from a
    pass that never ran. What can be asked of it is what the whole head has
    just built — every edge resolving, the graph acyclic, the derived index
    matching what is authored — which is the same question its own tool exits
    on, asked here by the ledger rather than taken on the tool's word.
    """
    # Local import: these modules import kb_util, which imports this one only
    # from its CLI dispatch. Kept local for the same reason kb_util does.
    from kb_tools import verify_citations, verify_kb_metadata, verify_md_links

    kb = str(kb_util.kb_root(ctx.repo_root))
    links_rc = verify_md_links.main(["--root", str(ctx.repo_root)])
    metadata_rc = verify_kb_metadata.main(["--kb-root", kb])
    citations_rc = verify_citations.main(["--kb-root", kb])
    # One unit and not three: a partial verify is not partial progress.
    return CoverageReport.declared(
        (
            CoverageUnit(
                id="verify-gates",
                source=kb_util.verify_cmd(ctx.repo_root),
                satisfied=not (links_rc or metadata_rc or citations_rc),
                asserts_own_work=False,
                detail=f"links rc={links_rc}, metadata rc={metadata_rc}, citations rc={citations_rc}",
            ),
        ),
        degenerate=True,
    )


#: The KB's overview document — what the tree holds, how it is organized, how a
#: reader navigates it. The stage assembles it from
#: :mod:`kb_tools.kb_readme`'s packaged template; the name is declared here
#: because this stage's coverage unit and postcondition look for that file.
OVERVIEW_DOC = "README.md"

#: The KB's operating contract, seeded from its packaged template at the
#: readiness stamp below.
CONVENTIONS_DOC = "CONVENTIONS.md"

#: phase-5's two documents, declared here so the stage's units and anything
#: else naming them read one definition.
META_DOCS = (OVERVIEW_DOC, CONVENTIONS_DOC)


def _check_meta_docs(ctx: CheckContext) -> CoverageReport:
    kb = kb_util.kb_root(ctx.repo_root)
    return CoverageReport.declared(
        tuple(
            _file_unit(unit_id=name, path=kb / name, detail="this document was never written", asserts_own_work=True)
            for name in META_DOCS
        ),
        unit_class="no meta-documentation document was written",
    )


# --- pre-commit stamps ------------------------------------------------------
#
# A stamp WRITES, where a coverage check only reads. It runs on the success
# path after coverage passes and before the boundary commit, so what
# it writes is swept into that commit rather than left dangling for the next
# stage to pick up.

#: The KB's orientation document, and the home of its scope pin — charter prose
#: the build run writes there as soon as the build is open (`kb_tools/SPEC.md`,
#: Project Scoping). Named rather than spelled twice: it is also the name
#: `stamp_readiness_docs` seeds through :data:`READINESS_DOCS`, and only where
#: no file already stands there.
SCOPE_PIN_DOC = "CLAUDE.md"

READINESS_DOCS = (SCOPE_PIN_DOC, CONVENTIONS_DOC)
PROJECT_NAME_FIELD = "{project-name}"

# kb_tools/installed/ holds the artifacts this toolchain writes into a
# consuming KB rather than anything rendered here. The directory name is the
# statement: nothing in it is a source for a file of the same name beside it.
INSTALLED_DIR = "installed"


def installed_template(name: str) -> Path:
    """The packaged source for the KB document ``name``, under ``kb_tools/installed/``.

    ``__file__`` is the right anchor here and only here: these are package
    resources, so they live wherever kb_tools was installed. Repo and KB paths
    stay cwd-anchored.
    """
    return Path(__file__).resolve().parent / INSTALLED_DIR / f"{name}.tmpl"


def stamp_readiness_docs(ctx: CheckContext) -> list[str]:
    """Write the KB's readiness docs from their packaged templates.

    Only-if-absent: a project that has authored its own CLAUDE.md or CONVENTIONS.md
    keeps it. ``{project-name}`` is substituted from the repo directory name —
    the only per-project fact these canned documents carry.

    Neither file is the corpus-invariant channel: those live in
    ``invariants.md``, which is what the toolchain parses for framework nodes.
    """
    kb = kb_util.kb_root(ctx.repo_root)
    reports = []
    for name in READINESS_DOCS:
        target = kb / name
        if target.exists():
            reports.append(f"{name}: present, left as authored")
            continue
        source = installed_template(name)
        if not source.is_file():
            raise PipelineError(
                f"the packaged readiness template {source} is missing; this "
                f"kb_tools install is incomplete — re-install the agent definitions."
            )
        text = source.read_text(encoding="utf-8").replace(PROJECT_NAME_FIELD, ctx.repo_root.name)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")
        reports.append(f"{name}: written from {source.name}")
    return reports


@dataclass(frozen=True)
class ClaimgraphInvocation:
    """Which ``kb_claimgraph`` invocation a stage is, as its own command line spells it.

    **The table that used to exist nowhere.** ``--pass 1 --scope block-hosted``,
    ``--pass 1 --scope full`` and ``--pass 2`` are three stages of this
    pipeline, and until this declaration the pairing lived in two places that
    could not see each other: the driver composed the flags from pass numbers
    of its own, and the tool parsed them back into a branch of its own. Both
    read this now, in opposite directions — :attr:`flags` composes and
    :func:`claimgraph_stage` resolves — so a build cannot invoke a pass the
    tool would run as a different stage.

    The numbering is the tool's shipped surface and is not touched here; what
    this fixes is that nothing said what the numbers meant.
    """

    which_pass: int
    scope: str | None = None

    @property
    def flags(self) -> tuple[str, ...]:
        return ("--pass", str(self.which_pass), *(() if self.scope is None else ("--scope", self.scope)))


#: The scope vocabulary ``kb_claimgraph``'s command line declares. Named here
#: because the stages below are what the two values distinguish.
CLAIMGRAPH_SCOPE_BLOCK_HOSTED = "block-hosted"
CLAIMGRAPH_SCOPE_FULL = "full"

# The three claim-graph invocations, named before the table so each stage's own
# card renders from the same value the stage declares.
_DECLARED_INVOCATION = ClaimgraphInvocation(which_pass=1, scope=CLAIMGRAPH_SCOPE_BLOCK_HOSTED)
_DISCOVERED_INVOCATION = ClaimgraphInvocation(which_pass=1, scope=CLAIMGRAPH_SCOPE_FULL)
_ATTRIBUTED_INVOCATION = ClaimgraphInvocation(which_pass=2)


@dataclass(frozen=True)
class Stage:
    """One pipeline stage: contract id, human label, action card, coverage.

    ``card`` is the stage's procedural obligations — what executing it requires
    — single-sourced here exactly as the stage list is, and rendered at the
    moment of action rather than carried in an agent definition. It is the
    completeness counterpart to the ordering interlocks: the interlocks say
    *when*, the card says *what*.

    ``coverage`` reports the units the stage must cover and which of them are
    satisfied. Every stage has one: a stage with no enumerable decomposition
    returns a degenerate report of one unit rather than no report at all, which
    is how a stage that gates on nothing says so instead of being silent.

    ``user_gate`` marks a stage the build stops at for an answer only the user
    can give. The confirmation renders where the user is expected by reading
    this flag off the table, so a gate that moves takes that sentence with it
    where a literal would go on naming the stage the gate used to be at.

    ``work_is_inference`` marks a stage whose work is a model call — the stages
    a build spending none does without. It is the *other* half of what excuses
    a coverage unit, and both halves are needed: a build that spent no
    inference still derived its tree and seeded its spine for real, so those
    stages' own-work units stand. Only where this stage's work was the
    inference and the unit asserts that work is there nothing left to assert
    (:func:`_excused`).

    ``claimgraph_invocation`` is set on the three stages ``kb_claimgraph`` runs
    and on no other — the mapping between this vocabulary and that tool's
    command line, read from both ends.
    """

    id: str
    display: str
    card: tuple[CardItem, ...]
    coverage: Callable[["CheckContext"], CoverageReport]
    # Runs after coverage passes and before the boundary commit, so whatever it
    # writes is swept into that commit. Returns report lines.
    pre_commit: Callable[["CheckContext"], list[str]] | None = None
    user_gate: bool = False
    work_is_inference: bool = False
    claimgraph_invocation: ClaimgraphInvocation | None = None


# The frozen vocabulary. Ids are a cross-team contract — templates elsewhere
# are written against these exact strings — so an id is never renamed in
# place; a change means a new id and a migration. Card text is the same kind
# of contract: agent definitions are trimmed against it, not the reverse.
STAGES: tuple[Stage, ...] = (
    Stage(
        "start",
        "build started",
        card=(
            "put this in front of the user exactly as it prints, adding nothing and leaving nothing out: "
            + _kb_util_command(
                kb_util.OP_SHOW_CONFIRMATION,
                "--source <path> [--source ...]",
                f"[{kb_util.CHARTER_VALUES_FLAG} <values-file>]",
            ),
            f"whatever the user then says that is not an answer to an unsettled fact is charter text: put it, "
            f"and any charter the invocation carried, into a values file under {SCRATCH_RELROOT}/ in their own "
            f"words ({kb_util.CHARTER_KEY} = '''...''' in one [[{kb_util.ENTRY_TABLE}]] table)",
            StageStatusStep(),
            RecordStep(),
            f"dispatch the first coordinator carrying root + {CHARTER_RELPATH}",
        ),
        coverage=_check_charter_written,
        user_gate=True,
    ),
    Stage(
        "document-graph",
        "document tree derived",
        card=(
            "derive the document tree from the build's sources, one --source per volume root and never for a "
            "file reached by \\input: "
            + _front_end_command(
                kb_util.DOCGRAPH_MODULE,
                kb_util.docgraph_flags(
                    sources=("<volume-root> [--source ...]",),
                    bibliographies=("<path.bib> [--bibliography ...]",),
                    kb_root_path=kb_util.KB_DIRNAME,
                ),
            ),
            "a red report is a defect in the front end or in the corpus; there is no fix cycle for it and no "
            "seat to run one",
            StageStatusStep(),
            RecordStep(),
        ),
        coverage=_check_document_tree,
    ),
    Stage(
        "spine-seed",
        "claim-graph spine seeded",
        card=(
            "seed the claim-graph spine over the tree: "
            + _kb_util_command(kb_util.OP_GRAPH_INIT, "[--runner just|make]"),
            "the tree is committed before this runs — the seed's own preflight refuses a dirty worktree",
            StageStatusStep(),
            RecordStep(),
        ),
        coverage=_check_spine_seeded,
    ),
    Stage(
        "claims-declared",
        "declared claim graph",
        card=(
            "author the claims the corpus's own author marked, mechanically: "
            + _front_end_command(kb_util.CLAIMGRAPH_MODULE, _DECLARED_INVOCATION.flags),
            "it refuses a tree that already carries claim-graph artifacts; that refusal is the double-run "
            "guard, not a fault to work around",
            StageStatusStep(),
            RecordStep(),
        ),
        coverage=_check_claims_declared,
        claimgraph_invocation=_DECLARED_INVOCATION,
    ),
    Stage(
        "claims-discovered",
        "claim discovery",
        card=(
            "read every document the declared pass left awaiting and mint a claim per result it states: "
            + _front_end_command(kb_util.CLAIMGRAPH_MODULE, _DISCOVERED_INVOCATION.flags),
            "one inference per awaiting document, and the build's only expensive stage — it writes per "
            "document, so a stopped run keeps what it already read",
            StageStatusStep(),
            RecordStep(),
        ),
        coverage=_check_claims_discovered,
        work_is_inference=True,
        claimgraph_invocation=_DISCOVERED_INVOCATION,
    ),
    Stage(
        "depends-attributed",
        "dependency attribution",
        card=(
            "attribute each claim's dependencies over the graph that now exists: "
            + _front_end_command(kb_util.CLAIMGRAPH_MODULE, _ATTRIBUTED_INVOCATION.flags),
            "every value it writes is unscored; grading is the maintenance path's and enters later",
            StageStatusStep(),
            RecordStep(aside="the head's exit: the tool refuses to record this stage over a red verify"),
        ),
        coverage=_check_verify_gates,
        claimgraph_invocation=_ATTRIBUTED_INVOCATION,
    ),
    Stage(
        "phase-3a",
        "validation gate",
        card=(
            GateStep(),
            StageStatusStep(),
            RecordStep(aside="the tool refuses to record this stage red"),
        ),
        coverage=_check_verify_gates,
        # Seeded at the gate rather than at the finish: these are the KB's
        # orientation docs, and every stage after this one runs against a KB
        # that should already carry them. Only-if-absent, so a later stage
        # authoring its own keeps it.
        pre_commit=stamp_readiness_docs,
    ),
    Stage(
        "phase-5",
        "meta-documentation",
        card=(
            CappedLine(
                f"the stage assembles {OVERVIEW_DOC} from the index and one passage tech-writer answers with"
                "; tech-writer-reviewer, fix-cycle cap {phase_5_fix_cap}; findings persisting -> escalate"
            ),
            "confirm docent commands present; absence -> escalate",
            StageStatusStep(),
            RecordStep(),
            "return: build finished, all stages [x]",
        ),
        coverage=_check_meta_docs,
        work_is_inference=True,
    ),
)

STAGE_IDS: tuple[str, ...] = tuple(stage.id for stage in STAGES)
_STAGE_BY_ID = {stage.id: stage for stage in STAGES}
_ID_WIDTH = max(len(stage_id) for stage_id in STAGE_IDS)
FIRST_STAGE_ID = STAGES[0].id


def resolve_stage(name: str) -> Stage | None:
    """The stage ``name`` names — by id or by display name — or ``None``.

    Both spellings are admitted because the ids are a machine contract and half
    of them are not readable as anything else: an operator bounding a run after
    the validation gate should not have to know that it is ``phase-3a``.
    Whitespace is normalized and case is ignored, so ``"Validation Gate"``
    and ``phase-3a`` name one stage.
    """
    wanted = " ".join(name.split()).casefold()
    return next((stage for stage in STAGES if wanted in (stage.id.casefold(), stage.display.casefold())), None)


def stage_by_id(stage_id: str) -> Stage:
    """The stage one id names. Raises ``KeyError`` for an id outside the vocabulary."""
    return _STAGE_BY_ID[stage_id]


def claimgraph_stage(*, which_pass: int, scope: str | None) -> Stage | None:
    """The stage one ``kb_claimgraph`` invocation is, or ``None`` for no such pass.

    The resolving direction of :class:`ClaimgraphInvocation`. The tool parses
    its own flags and asks this what stage it is running, so what a pass number
    means is answered in the table both sides read rather than in a branch on
    either side of the subprocess.
    """
    wanted = ClaimgraphInvocation(which_pass=which_pass, scope=scope)
    return next((stage for stage in STAGES if stage.claimgraph_invocation == wanted), None)


def precondition_of(stage: Stage) -> Stage | None:
    """The stage whose own work must already stand for ``stage``'s to run.

    The pipeline is a line, so a stage's precondition is the stage before it and
    there is nothing further to declare: what "already stands" means is that
    stage's own-work coverage units (``CoverageUnit.asserts_own_work``), which
    are declared once beside the check that produces them. The first stage has
    none.

    Read by the claim-graph tool as well as by this module, which is the point:
    each of its three invocations refuses a tree the pass before it has not run
    over, and until this the two statements of that order — the tool's entry
    conditions and this table — could not see each other.
    """
    index = STAGE_IDS.index(stage.id)
    return None if index == 0 else STAGES[index - 1]


def stage_vocabulary() -> str:
    """Every name :func:`resolve_stage` admits, in walk order — a refusal's whole guidance."""
    return ", ".join(f"{stage.id} ({stage.display})" for stage in STAGES)


def recorded_stages(repo_root: Path) -> set[str]:
    """The stage ids the commit trail records, read from subjects alone."""
    result = kb_util.run_git(repo_root, "log", f"--grep=^{LEDGER_PREFIX}", "--format=%s")
    # A repo whose branch is unborn (no commits yet) makes `git log` exit
    # nonzero. That is an empty ledger, not a fault.
    if result is None or result.returncode != 0:
        return set()
    found = (_SUBJECT_RE.match(subject) for subject in result.stdout.splitlines())
    return {match.group(1) for match in found if match is not None and match.group(1) in _STAGE_BY_ID}


def current_stage(recorded: set[str]) -> Stage | None:
    """The stage to act on — the first unrecorded one — or None when complete.

    One definition serves both the checklist's ``[*]`` marker and the action
    card, so the card can never advertise a different stage than the checklist
    points at.
    """
    return next((stage for stage in STAGES if stage.id not in recorded), None)


def checklist_lines(recorded: set[str]) -> list[str]:
    """The checklist: ``[x]`` recorded, ``[*]`` in progress, ``[ ]`` undone.

    ``[*]`` marks the first unrecorded stage, and only once the build has
    started — before that nothing is in progress.
    """
    started = FIRST_STAGE_ID in recorded
    in_progress = current_stage(recorded)
    lines = []
    for stage in STAGES:
        if stage.id in recorded:
            marker = "x"
        elif started and in_progress is not None and stage.id == in_progress.id:
            marker = "*"
        else:
            marker = " "
        lines.append(f"[{marker}] {stage.id:<{_ID_WIDTH}}  {stage.display}")
    return lines


def card_lines(stage: Stage, repo_root: Path) -> list[str]:
    """The stage's action card, prefixed so it cannot be read as checklist.

    Generated obligations (:class:`RecordStep`, :class:`GateStep`) resolve here
    against ``stage`` and ``repo_root``; plain strings pass through.
    """
    return [f"{CARD_PREFIX} next action — {stage.id} ({stage.display}):"] + [
        f"{CARD_PREFIX} · {item if isinstance(item, str) else item.render(stage, repo_root)}" for item in stage.card
    ]


def status_line(recorded: set[str]) -> str:
    """The one-line verdict naming which of the three world-states holds."""
    count = len(recorded)
    if FIRST_STAGE_ID not in recorded:
        state = "not started"
    elif count == len(STAGES):
        state = "complete"
    else:
        state = "in progress"
    return f"{_TAG} status: {state} ({count} of {len(STAGES)} stages recorded)"


def _print_report(
    repo_root: Path,
    recorded: set[str],
    *,
    advisory: str | None = None,
    baton: str | None = None,
    stage_status: Sequence[str] = (),
) -> None:
    """The full render: status, checklist, coverage, action card, baton, contract line.

    The checklist block stays contiguous and is the only thing matching
    ``^\\[[x* ]\\] ``; every other line carries a word-prefix instead, so a
    parser can lift the checklist without knowing about the rest.

    ``stage_status`` is a refusal's unsatisfied units, rendered between the
    checklist and the card that corrects them.
    """
    print(status_line(recorded))
    if advisory is not None:
        print(advisory)
    for line in checklist_lines(recorded):
        print(line)
    for line in stage_status:
        print(line)
    stage = current_stage(recorded)
    if stage is not None:
        for line in card_lines(stage, repo_root):
            print(line)
    if baton is not None:
        print(baton)
    # Stated at read-time, every time: the observed failure was an agent
    # referencing collapsed tool output instead of embedding this render.
    print(f"{_TAG} {CONTRACT_LINE}")


def _record(repo_root: Path, stage: Stage, body: str | None = None) -> None:
    """Sweep the worktree into a boundary commit for ``stage``.

    ``git add -A`` is the sweep: gitignore rules keep scratch out, and on the
    first stage it deliberately picks up the uncommitted spine seed, which
    belongs to the build's first commit. A stage that changed no tracked file
    still records, via ``--allow-empty`` — the boundary is the point, not the
    diff.
    """
    added = kb_util.run_git(repo_root, "add", "-A")
    if added is None or added.returncode != 0:
        raise PipelineError(f"git add -A failed at {repo_root}: {'' if added is None else added.stderr.strip()}")
    status = kb_util.run_git(repo_root, "status", "--porcelain")
    if status is None or status.returncode != 0:
        raise PipelineError(f"git status failed at {repo_root}")
    args = ["commit", "-m", f"{LEDGER_PREFIX} {stage.id} | {stage.display}"]
    if body:
        args += ["-m", body]
    if not status.stdout.strip():
        args.append("--allow-empty")
    committed = kb_util.run_git(repo_root, *args)
    if committed is None or committed.returncode != 0:
        detail = "git is not runnable" if committed is None else committed.stderr.strip()
        raise PipelineError(f"git commit failed at {repo_root}: {detail}")


def _unseeded_advisory(repo_root: Path) -> str | None:
    """The note a checklist carries while ``kb-root/`` does not exist yet, if it does not.

    One statement, read by every render that can meet an unseeded repo — the
    status render and the confirmation's checklist block alike.
    """
    if kb_util.kb_root(repo_root).is_dir():
        return None
    return f"{_TAG} note: {kb_util.KB_DIRNAME}/ is not seeded yet — the seed runs before the first stage is recorded."


def show_status(repo_root: Path) -> int:
    """Render the checklist for ``repo_root``. Read-only; always exit 0.

    This is also the resume detector: a present-but-incomplete ledger is the
    mechanically detectable third state beside fresh and revision.

    ``repo_root`` is a git root, not necessarily a seeded one: the ledger
    lives in the commit trail, so a KB that does not exist yet is *status*
    — the confirmation step of a fresh build reads this before the spine
    is seeded — and it is named in the render rather than left implied.
    """
    _print_report(repo_root, recorded_stages(repo_root), advisory=_unseeded_advisory(repo_root))
    return EXIT_OK


def _unit_phrase(unit: CoverageUnit) -> str:
    return f"{unit.id} ({unit.source})" + (f" — {unit.detail}" if unit.detail else "")


def _named_missing(report: CoverageReport) -> tuple[CoverageUnit, ...]:
    """The unsatisfied units a refusal names one by one.

    Empty in the two shapes where instances say nothing a reader can act on: an
    undeclared report, whose units were never enumerated at all, and a
    decomposed report with nothing satisfied — the stage did not happen, and
    naming a hundred instances of that obscures the one fact.
    ``show-stage-status`` is where the paths are obtained in that state.

    Anything else names every unsatisfied unit and never a subset: those are
    exactly the gap between what happened and what should have, and a caller
    told only how many remain cannot dispatch against them. A stage declaring
    one unit names it either way, there being no instances for a class
    statement to stand above.
    """
    if report.reason is not None:
        return ()
    missing = tuple(unit for unit in report.units if not unit.satisfied)
    if len(missing) == len(report.units) and len(report.units) > 1:
        return ()
    return missing


def _coverage_refusal(report: CoverageReport) -> str | None:
    """The report's refusal reason, or None when the stage may be recorded.

    One decision, and the whole of it: no refusal iff the report is declared
    and every unit in it is satisfied. An undeclared report is a refusal
    because a stage whose unit source could not be read cannot be shown
    complete.

    The verdict is binary; the message says which shape of failure produced it,
    over the units :func:`_named_missing` decides are worth naming.

    Takes a report rather than a stage because the record path reads one
    production of it twice — for this verdict and for
    :func:`_report_vacuous_units` — and building a report can run the verify
    gates.
    """
    if report.reason is not None:
        return report.reason
    if all(unit.satisfied for unit in report.units):
        return None
    named = _named_missing(report)
    if not named:
        return f"not one of {len(report.units)} coverage units is satisfied: {report.unit_class}"
    phrases = "; ".join(_unit_phrase(unit) for unit in named)
    return f"{len(named)} of {len(report.units)} coverage unit(s) unsatisfied: {phrases}"


#: What a unit's detail becomes once this build's exclusion has excused it.
#: Written where the excusing happens rather than in each check, because the
#: reason is the same reason every time and none of the checks knows it.
WORK_EXCLUDED_DETAIL = "this build spent no model call, so this stage's work did not run and there is none to find"


def _excused(report: CoverageReport, stage: Stage, ctx: CheckContext) -> CoverageReport:
    """The report with the units this build's exclusion excuses turned vacuous.

    **Two conditions, and both are the tool's own.** The build says one thing —
    that it spent no inference — and everything else is decided here: whether
    this stage's work was the inference (:attr:`Stage.work_is_inference`), and
    which of its units assert that work (:attr:`CoverageUnit.asserts_own_work`).
    A record cannot name a unit and cannot waive a check; the classification is
    not reachable from a command line.

    **A validity unit is never excused, whatever the build did.** Whatever path
    reached this boundary, the state handed across it must satisfy the next
    stage's contract — which is why ``_check_verify_gates`` runs at
    ``depends-attributed`` under this flag exactly as it does without it, over a
    KB that stage left unchanged. Refresh is idempotent absent claim-value
    changes and verify is cheap, so the cost of asking twice is nothing beside
    a validity gate silently skipped.
    """
    if report.reason is not None or not (ctx.no_inference and stage.work_is_inference):
        return report
    return replace(
        report,
        units=tuple(
            replace(unit, satisfied=True, vacuous=True, detail=WORK_EXCLUDED_DETAIL) if unit.asserts_own_work else unit
            for unit in report.units
        ),
    )


def _report_vacuous_units(report: CoverageReport) -> None:
    """Name every unit that was satisfied because there was nothing to check.

    Never a gate — the stage records either way. Zero support nodes has two
    causes the tool cannot tell apart, a corpus that derives nothing and a wave
    that noticed nothing, which is why it passes; this line is what leaves a
    reader able to tell, and it claims nothing about which cause holds.
    """
    for unit in report.units:
        if unit.vacuous:
            print(f"{_TAG} note: nothing to check for {unit.id} — {unit.detail}")


# --- the stage-coverage read ------------------------------------------------
#
# One stage's coverage, rendered for a caller who asked rather than for one who
# was refused. Three statuses and no verdict: reading a stage decides nothing,
# so nothing here gates and nothing here writes.
#
# The MISSING line is built in one place and both consumers call it — the
# refusal below renders the units it names as exactly these lines — so the two
# outputs are one computation rather than two renderings that agree today.

STAGE_STATUS_TAG = "[stage-status]"
COVERED = "COVERED"
MISSING = "MISSING"
FACT = "FACT"

#: Not a fourth status: the word a ``FACT`` line opens its detail with when the
#: stage's units could not be enumerated at all.
UNDECLARED = "UNDECLARED"

#: What an argument-derived unit reports on a read. ``start``'s charter rides
#: the record, so a read holds no value to check — which is a fact about the
#: question asked, never about the tree.
ARGUMENT_ON_A_READ = "this argument rides the record and is not available on a read"


def _missing_line(unit: CoverageUnit) -> str:
    return f"{STAGE_STATUS_TAG} {MISSING} {_unit_phrase(unit)}"


def _covered_line(unit: CoverageUnit) -> str:
    # A satisfied unit's `detail` states the condition it did not fail, which
    # beside COVERED reads as a defect; a vacuous unit's says why there was
    # nothing to look for, which is the whole of what it has to report.
    return f"{STAGE_STATUS_TAG} {COVERED} {unit.id} ({unit.source})" + (f" — {unit.detail}" if unit.vacuous else "")


def _stage_fact(stage: Stage, detail: str) -> str:
    return f"{STAGE_STATUS_TAG} {FACT} {stage.id} ({stage.display}) — {detail}"


def stage_status(repo_root: Path, stage: Stage) -> list[str]:
    """One stage's coverage, read with no record arguments to hand.

    The context is built here, and empty of them: a read is not a record, so
    an argument-derived unit reports :data:`ARGUMENT_ON_A_READ` rather than a
    claim about the tree.

    An undeclared stage is one ``FACT`` line and no units — its report carries
    the reason, which names the declaring artifact and the call that restores
    it — because a stage whose units were never enumerated has none to list.
    """
    report = stage.coverage(CheckContext(repo_root))
    if report.reason is not None:
        return [_stage_fact(stage, f"{UNDECLARED}: {report.reason}")]
    lines = [_stage_fact(stage, f"{len(report.units)} coverage unit(s) declared")]
    for unit in report.units:
        if unit.satisfied:
            lines.append(_covered_line(unit))
        else:
            lines.append(_missing_line(replace(unit, detail=ARGUMENT_ON_A_READ) if unit.from_argument else unit))
    return lines


def show_stage_status(repo_root: Path, stage_id: str | None) -> int:
    """Print one stage's coverage. Read-only; always exit 0.

    Zero-argument resolves through :func:`current_stage` — the same decision
    the checklist's ``[*]`` marker and the action card share — so asking what
    remains where the build actually stands takes no stage id. A complete build
    has no such stage and says so rather than falling back to the last one.

    ``stage_id`` names any stage, recorded or unreached: a recorded stage's
    report is how a resumed build reads what actually landed, and an unreached
    stage's ``UNDECLARED`` is a true answer to a read.

    Neither a checklist nor a card is rendered. This is not ``show-status``,
    whose render is quoted verbatim into every user message and would then
    carry a unit list of a corpus's own length inside a contract line's payload.
    """
    if stage_id is not None:
        stage = _STAGE_BY_ID[stage_id]
    else:
        in_flight = current_stage(recorded_stages(repo_root))
        if in_flight is None:
            print(
                f"{STAGE_STATUS_TAG} {FACT} complete — all {len(STAGES)} stages are recorded; "
                f"--stage names one to inspect"
            )
            return EXIT_OK
        stage = in_flight
    for line in stage_status(repo_root, stage):
        print(line)
    return EXIT_OK


def _refuse(repo_root: Path, recorded: set[str], stage: Stage, report: CoverageReport, refusal: str) -> int:
    """Report incomplete coverage and render the card — the card IS the fix.

    The units the refusal names are rendered as the same ``MISSING`` lines
    ``show-stage-status`` prints, so a caller who was refused and a reader who
    asked are looking at one computation.
    """
    kb_util.to_stderr(f"{_TAG} cannot record '{stage.id}' — {refusal}. Nothing committed.")
    _print_report(repo_root, recorded, stage_status=[_missing_line(unit) for unit in _named_missing(report)])
    return EXIT_POSTCONDITION_FAILED


def start_build(repo_root: Path, charter: str) -> int:
    """Record the ``start`` boundary, sweeping the seed into it.

    Refuses without committing when ``start`` is already recorded: this
    commit is by definition the build's first, so a second one would be a
    contradiction rather than a repetition.

    ``charter`` empty is a build carrying none: the boundary is recorded with
    no body, since the body's whole content here is the charter it names.
    """
    recorded = recorded_stages(repo_root)
    if FIRST_STAGE_ID in recorded:
        kb_util.to_stderr(
            f"{_TAG} this build is already started — nothing committed. "
            f"Use '{kb_util.OP_ADVANCE_STEP}' to record the next stage."
        )
        _print_report(repo_root, recorded)
        return EXIT_ALREADY_STARTED
    stage = _STAGE_BY_ID[FIRST_STAGE_ID]
    report = stage.coverage(CheckContext(repo_root, charter=charter))
    refusal = _coverage_refusal(report)
    if refusal is not None:
        return _refuse(repo_root, recorded, stage, report, refusal)
    _report_vacuous_units(report)
    _record(repo_root, stage, body=f"charter: {charter}" if charter else "")
    _print_report(repo_root, recorded_stages(repo_root), baton=f"{_TAG} next: dispatch the coordinator")
    return EXIT_OK


def advance_step(repo_root: Path, stage_id: str, note: str | None = None, no_inference: bool = False) -> int:
    """Record the ``stage_id`` boundary commit.

    Stage-addressed and declarative: an already-recorded stage reports and
    exits 0, and a stage whose predecessors are unrecorded is refused with the
    checklist so the caller's world-model is corrected rather than obeyed.

    ``no_inference`` states one fact about the build — that it spent no model
    call. It names no check and excuses none by itself; :func:`_excused` is
    where that fact meets this table's own classification of what each coverage
    unit asserts.
    """
    stage = _STAGE_BY_ID[stage_id]
    recorded = recorded_stages(repo_root)

    if stage.id in recorded:
        banner = (
            "process already complete" if len(recorded) == len(STAGES) else f"stage '{stage.id}' is already recorded"
        )
        print(f"{_TAG} {banner} — nothing committed.")
        _print_report(repo_root, recorded)
        return EXIT_OK

    unrecorded = [s.id for s in STAGES[: STAGE_IDS.index(stage.id)] if s.id not in recorded]
    if unrecorded:
        kb_util.to_stderr(
            f"{_TAG} cannot record '{stage.id}' — these predecessors are "
            f"unrecorded: {', '.join(unrecorded)}. Record them in order, or re-read the "
            f"checklist below for where this build actually stands."
        )
        _print_report(repo_root, recorded)
        return EXIT_OUT_OF_ORDER

    ctx = CheckContext(repo_root, note=note, no_inference=no_inference)
    report = _excused(stage.coverage(ctx), stage, ctx)
    refusal = _coverage_refusal(report)
    if refusal is not None:
        return _refuse(repo_root, recorded, stage, report, refusal)
    _report_vacuous_units(report)

    if stage.pre_commit is not None:
        for line in stage.pre_commit(ctx):
            print(f"{_TAG} {line}")

    _record(repo_root, stage, body=note)
    _print_report(repo_root, recorded_stages(repo_root))
    return EXIT_OK


# --- the build's opening gate ----------------------------------------------
#
# One read before the gate and one write after it, and no sequence between them
# for a caller to execute out of order. The read renders the whole confirmation
# — every fact the answer turns on — as a message to be relayed unchanged; the
# write performs everything the answer releases, in the one order that works,
# and leaves nothing behind if it cannot finish.
#
# The render is read by a person and by the agent relaying it at once, so it is
# plain declaratives throughout: a line here is either a fact about this
# repository or a question with the answer that holds if it is not asked.

CONFIRMATION_TAG = "[confirmation]"
CHARTER_TAG = "[charter]"
OPEN_BUILD_TAG = "[open-build]"

_CONFIRMATION_FIELD_WIDTH = 14

#: The parts ``open-build`` performs, in order. A failure names the one it
#: stopped at, because "the build did not open" leaves a caller re-running the
#: whole act to find out how far it got.
SEED_PART = "the spine seed"
CHARTER_PART = "the charter"
RECORD_PART = "the start record"


def _confirmation(text: str) -> str:
    return f"{CONFIRMATION_TAG} {text}"


def _confirmation_field(name: str, detail: str) -> str:
    return _confirmation(f"{name:<{_CONFIRMATION_FIELD_WIDTH}} {detail}")


def _source_lines(sources: Sequence[Path]) -> tuple[list[str], int]:
    """One line per source, resolved, and how many of them are not there.

    A source the build cannot read is the confirmation's business and not a
    later stage's: the sources are the one thing a caller supplies from outside
    the repository, and a typo in one is invisible until a specialist is
    dispatched against it.
    """
    lines = []
    missing = 0
    for source in sources:
        resolved = source.resolve()
        if resolved.exists():
            lines.append(_confirmation_field("source", str(resolved)))
        else:
            missing += 1
            lines.append(_confirmation_field("source", f"{resolved} — this path does not exist"))
    return lines, missing


def _determination_line(repo_root: Path) -> str:
    """The kb-root tri-state, stated as a fact rather than read as fresh-or-revision.

    Before kb_docgraph existed, the spine was seeded first, so a populated
    kb-root/ could only mean a prior build's content and fresh-or-revision
    followed straight from the tri-state. Under the current order kb_docgraph
    writes the document tree first, so a populated kb-root/ is what every fresh
    build's confirmation now sees before any claim-graph metadata exists — the
    same inverted precondition :func:`graph_init_kb` (kb_util) already had to
    correct in its own guard. Nothing here can tell "a tree with no claim-graph
    metadata yet" apart from "a tree a finished build already stamped" by
    reading kb-root/ content alone, so that reading is asked of the user
    instead (:func:`_unsettled_lines`) rather than asserted from tree content.
    """
    state = kb_util.kb_root_state(repo_root)
    if state == "absent":
        detail = f"{kb_util.KB_DIRNAME}/ does not exist yet"
    elif state == "spine-only":
        detail = f"{kb_util.KB_DIRNAME}/ exists but holds nothing outside {kb_util.INDEX_DIRNAME}/"
    else:
        detail = f"{kb_util.KB_DIRNAME}/ holds a document tree"
    return _confirmation_field("kb-root", detail)


def _charter_lines(charter: str | None) -> list[str]:
    """The charter quoted back for the user to check, one tagged line each.

    The tag is added per line and the file gets the text without it: a charter
    is prose, and one line of prose reading ``[x] done`` would otherwise be a
    stage as far as anything reading the checklist block is concerned.
    """
    if charter is None:
        return [_confirmation_field("charter", "none supplied — what you say now is the whole of it")]
    return [
        _confirmation("The charter this build will carry, quoted back a line at a time:"),
        *(f"{CHARTER_TAG} {line}" for line in charter.splitlines()),
    ]


def _user_gate_line() -> str:
    """Where this build expects the user, read off the stage table.

    Derived rather than stated: the table says which stages stop for an answer,
    so a gate that is added or moved is named here without anyone remembering
    to come and say so.
    """
    gates = [f"{stage.id} ({stage.display})" for stage in STAGES if stage.user_gate]
    return _confirmation(
        f"This build stops for you at {len(gates)} of its {len(STAGES)} stages: {', '.join(gates)}. "
        f"Everywhere else it runs unattended, and stops early only to escalate."
    )


def _unsettled_lines(repo_root: Path) -> list[str]:
    """The per-project facts the confirmation settles, each with what holds if it is not answered.

    A default nobody states is a decision made silently, which is what these
    lines exist to prevent. The runner entry appears only where there is
    something to settle: a repository already carrying a justfile or a Makefile
    has its answer.
    """
    lines = [_confirmation("Unsettled. Each is followed by what this build does if you say nothing:")]
    if kb_util.detected_runner(repo_root) is None:
        created = kb_util.runner_filename(kb_util.DEFAULT_RUNNER)
        lines.append(
            _confirmation(
                f"  Task runner — this repository has neither a justfile nor a Makefile, and the KB's "
                f"maintenance commands arrive as one include line in one of them. Default: a {created} "
                f"is created carrying that line."
            )
        )
    lines.append(
        _confirmation(
            "  Canonical direction — which side wins when the sources and the knowledge base "
            "disagree. Default: the sources are canonical and the KB is derived from them."
        )
    )
    if kb_util.kb_root_state(repo_root) == "populated":
        lines.append(
            _confirmation(
                "  Fresh or revision — whether the document tree above carries no claim-graph "
                "metadata yet, or a finished build already stamped it. kb-root/ content alone "
                "cannot tell the two apart once the tree is built before the spine is. Default: "
                "fresh — the seed and the start record both no-op or refuse rather than duplicate "
                "work over a spine already initialised, so treating this as fresh is safe even "
                "where it is not."
            )
        )
    lines.append(_confirmation("Anything else you say is charter text, and reaches the build in your own words."))
    return lines


def show_confirmation(repo_root: Path, *, sources: Sequence[Path], charter: str | None) -> int:
    """Print the build's opening confirmation whole. Reads everything, decides nothing.

    What comes back is the message itself, not material for one: the parse, the
    charter, the run map, where the build expects the user, what is still
    unsettled, and the environment report, in the order a person reads them. A caller that summarized, re-ordered or
    selected from it would be making the judgements this call exists to remove.

    Exit codes: ``0`` there is something to confirm; ``1`` a blocking item
    stands in the way — a preflight ``FAIL`` or a source that is not there — so
    the answer cannot start a build yet. The only write anywhere below is the
    scratch directory ``preflight`` creates.
    """
    source_lines, missing = _source_lines(sources)
    print(_confirmation("Confirm this before the build starts. Nothing has been written yet."))
    print(_confirmation_field("repository", str(repo_root)))
    for line in source_lines:
        print(line)
    print(_confirmation_field("knowledge base", str(kb_util.kb_root(repo_root))))
    print(_determination_line(repo_root))
    for line in _charter_lines(charter):
        print(line)

    print(_confirmation("The stages this build runs, and where it stands now:"))
    recorded = recorded_stages(repo_root)
    advisory = _unseeded_advisory(repo_root)
    if advisory is not None:
        print(advisory)
    for line in checklist_lines(recorded):
        print(line)
    print(_user_gate_line())

    for line in _unsettled_lines(repo_root):
        print(line)

    print(_confirmation("The environment checks this build just ran:"))
    preflight_rc = kb_util.run_preflight(repo_root)

    if preflight_rc != 0 or missing:
        kb_util.to_stderr(
            _confirmation(
                "The build cannot start: the items marked above have to be cleared first. "
                "Relay this message unchanged; there is nothing to confirm until they are."
            )
        )
        return 1
    print(
        _confirmation(
            "Relay this message to the user unchanged, wait for the answer, and write nothing until it arrives."
        )
    )
    # The baton, generated for the same reason a card's record step is: the op
    # and its one flag come from the CLI's own constants, so this cannot name a
    # call that does not exist or spell one the consumer does not run.
    print(
        _confirmation(
            f"next: the answer and the charter go to "
            f"{_kb_util_command(kb_util.OP_OPEN_BUILD, f'{kb_util.CHARTER_VALUES_FLAG} <values-file>')}"
        )
    )
    return EXIT_OK


def _unstage(repo_root: Path) -> None:
    """Drop whatever the record's sweep staged, leaving the index at HEAD.

    Undoing the files a failed act wrote is only half of putting a repository
    back: ``_record`` sweeps with ``git add -A`` before it commits, so a commit
    that fails leaves every one of those paths staged, and a worktree with a
    staged deletion is not the clean one the retry's preflight requires. Safe to
    do wholesale precisely because that preflight already passed — the index
    matched HEAD when this call began, so resetting to HEAD restores it rather
    than discarding work someone else had staged.
    """
    kb_util.run_git(repo_root, "reset", "-q")


def _open_build_failed(part: str, detail: str, code: int) -> int:
    """Report which part stopped the act, and that nothing of it is left."""
    kb_util.to_stderr(
        f"{OPEN_BUILD_TAG} stopped at {part} — {detail} "
        f"Nothing this call wrote is left behind: the repository stands as it did before it ran."
    )
    return code


def open_build(repo_root: Path, *, charter: str, runner: str | None) -> int:
    """Seed the spine, write the charter, record the start — one act or none.

    The order is the only one that works and so is not a caller's to get right:
    the seed runs first, because it refuses a ``kb-root/`` holding no document
    tree and a missing prerequisite is better reported before a charter is
    written than after; the charter is written second, because the record's
    postcondition is that it exists; and the record runs last, sweeping both into
    the build's first commit.

    Every write is undone if a later part cannot complete, so a failed call
    leaves the repository as it found it and the retry is the identical call.
    What is not undone is what was already there: a KB seeded by an earlier run,
    a runner file this call did not create.

    Exit codes: ``0`` open; ``1`` the seed's refresh or verify failed;
    :data:`EXIT_GIT_FAILURE` preflight blocked the seed, or git would not
    record; :data:`kb_util.EXIT_NO_DOCUMENT_TREE` ``kb-root/`` holds no document
    tree, so there is nothing to open a claim-graph build over;
    :data:`EXIT_ALREADY_STARTED` the build is already started.
    """
    recorded = recorded_stages(repo_root)
    if FIRST_STAGE_ID in recorded:
        kb_util.to_stderr(
            f"{OPEN_BUILD_TAG} this build is already started — nothing written. "
            f"Use '{kb_util.OP_ADVANCE_STEP}' to record the next stage."
        )
        _print_report(repo_root, recorded)
        return EXIT_ALREADY_STARTED

    charter_path = repo_root / CHARTER_RELPATH
    with contextlib.ExitStack() as undo:
        # Registered first, so it runs last: the files come out of the worktree,
        # then the index is put back. A no-op on every failure before the record
        # is attempted, nothing having been staged yet.
        undo.callback(_unstage, repo_root)
        seed_rc = kb_util.graph_init_kb(repo_root, runner, undo=undo)
        if seed_rc == kb_util.EXIT_NO_DOCUMENT_TREE:
            return _open_build_failed(
                SEED_PART,
                f"{kb_util.KB_DIRNAME}/ holds no document tree, so there is nothing to build a "
                f"claim graph over; run the document-graph front end over the sources first, "
                f"then re-issue this call.",
                seed_rc,
            )
        if seed_rc != EXIT_OK:
            return _open_build_failed(SEED_PART, "the report above says what failed.", seed_rc)

        try:
            charter_path.write_text(charter, encoding="utf-8")
        except OSError as exc:
            return _open_build_failed(CHARTER_PART, f"{charter_path} could not be written: {exc}", EXIT_GIT_FAILURE)
        undo.callback(charter_path.unlink)
        print(f"{OPEN_BUILD_TAG} charter: {len(charter.encode('utf-8'))} bytes written to {CHARTER_RELPATH}")

        try:
            record_rc = start_build(repo_root, CHARTER_RELPATH)
        except PipelineError as exc:
            return _open_build_failed(
                RECORD_PART, f"the boundary could not be committed ({str(exc).rstrip()}).", EXIT_GIT_FAILURE
            )
        if record_rc != EXIT_OK:
            return _open_build_failed(RECORD_PART, "the report above says what was refused.", record_rc)
        undo.pop_all()

    print(f"{OPEN_BUILD_TAG} the build is open: spine seeded, charter written, start recorded.")
    return EXIT_OK


def run_op(
    repo_root: Path,
    *,
    op: str,
    stage: str | None,
    charter: str | None,
    note: str | None,
    no_inference: bool = False,
) -> int:
    """Dispatch one ledger op.

    Each op's subparser declares only the arguments that op takes, so this
    trusts the combination it is handed.
    """
    try:
        if op == kb_util.OP_SHOW_STATUS:
            return show_status(repo_root)
        if op == kb_util.OP_SHOW_STAGE_STATUS:
            # The one op whose --stage is optional: without it the read
            # resolves the stage in flight.
            return show_stage_status(repo_root, stage)
        if op == kb_util.OP_START_BUILD:
            return start_build(repo_root, charter=charter or "")
        return advance_step(repo_root, stage_id=stage or "", note=note, no_inference=no_inference)
    except PipelineError as exc:
        kb_util.to_stderr(f"error: {exc}")
        return EXIT_GIT_FAILURE
