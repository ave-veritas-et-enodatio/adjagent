"""The ordered step table — the only place that says what happens next.

The pipeline sequence as data. No brief template, no worker, and no wave
session ever names a stage id, a record command, or a successor step; the rows
below are the sequencer, and everything else in the driver reads them. The
guard is ``prompt_templates.lint`` run over :data:`TEMPLATE_PROHIBITIONS`, which also
carries the metadata markers — the other thing a brief may never spell.

What this module holds: rows, their call unit and seat, the template and the
per-call slots the run loop must compute, the artifacts the contract check
looks for, the parses each return must survive, the barriers a row can raise,
and the capped loop series. What it must not hold: subprocess calls, file
writes, template text, or a cap literal — caps have exactly one definition, in
``kb_pipeline`` beside ``STAGES``, and are imported here so a run can never
contradict the card it just printed.

**Scope**: the rows below cover every stage of the pipeline, and a run walks all
of them. :data:`TABLE_STAGE_IDS` is sliced from ``kb_pipeline.STAGE_IDS`` so it
cannot disagree with the stage vocabulary about order or membership.

Executing a row is the run loop's job. This module is a table.

Stdlib only.
"""

import re
from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from types import MappingProxyType

from .. import kb_index_lib, kb_pipeline, kb_util
from . import envelope

# --- vocabularies -----------------------------------------------------------


class Unit(StrEnum):
    """A step's call unit. ``WAVE_STAR`` is a WAVE with >1 member and a SINGLE with one."""

    DRIVER_OP = "driver-op"
    REFRESH = "refresh"
    GATE = "gate"
    SINGLE = "SINGLE"
    WAVE = "WAVE"
    WAVE_STAR = "wave*"


class Writer(StrEnum):
    """Who writes a step's artifacts — the three persistence routes."""

    NONE = "—"
    DRIVER = "driver"
    WAVE_SESSION = "wave-session"
    WORKER = "worker"
    TOOL = "tool"


class Parse(StrEnum):
    """The formats a step's return must survive. Existence and parse only, never quality."""

    ENVELOPE = "envelope"
    VERDICT = "verdict"
    SCOPE = "scope"


class LedgerOp(StrEnum):
    """The sanctioned ledger ops. Invoked as a subprocess by ``ledger.py``, never imported.

    The member values are ``kb_util``'s own op-name constants, so these are the
    subcommand tokens rather than a copy of them: one definition, two readers.
    """

    START_BUILD = kb_util.OP_START_BUILD
    ADVANCE_STEP = kb_util.OP_ADVANCE_STEP


# The two revision-series letters a capped loop's findings are named by. `r` is
# every current loop row's own series; `g` is a second series a row can open for
# a revision reached by a different path than its first pass — no current row
# does. The letter rides the filename so that one stage's two series can never
# name one file. It labels and never counts: a round number is the running
# process's own, and the stage's boundary commit is what records the rounds it
# ran.
SERIES_INITIAL = "r"
SERIES_GATE = "g"


# --- the scratch layout this cut touches ------------------------------------
#
# The `.claude-temp/kb-build/` layout is a contract governing build artifacts —
# things later stages consume and postconditions check. These constants are the
# driver's own statement of it, and `@!layout-paths!@` is filled from them, so
# what a brief says and what the driver constructs cannot drift. Later
# increments extend the block as their rows land.

# Every path a stage card also names, or a postcondition checks, is imported
# rather than restated: `kb_pipeline` states it once, its postconditions check
# it there, and its cards render the commands that write it. A card that
# spelled its own path could send an artifact somewhere the tool never looks.
SCRATCH_ROOT = kb_pipeline.SCRATCH_RELROOT

# The charter is not a member of this layout and may not become one: it is an
# input that must already stand when the build opens, and the `start` boundary
# names its path permanently, so it lives at `kb_pipeline.CHARTER_RELPATH` in
# the tracked tree. Staging deletes this one wholesale.

# Every capped loop's evidence lands in `review/` under one filename grammar, so
# that a reader can tell one round's findings from another's and one author's
# from another's. One format string, so a stage that writes three files per round
# and a stage that writes one cannot end up with two readings of the same name.
# Nothing reads a round number back out of a name: a file here is evidence, and
# the round it belongs to is the running process's own count.
_FINDINGS_FMT = "review/{stage}-{series}{round}-{author}.md"
FINDINGS = _FINDINGS_FMT.format(stage="<stage>", series="<series>", round="<N>", author="<author>")


def findings(*, stage: str, series: str, round_number: int, author: str) -> str:
    """One round's findings path for one author, scratch-relative."""
    return _FINDINGS_FMT.format(stage=stage, series=series, round=round_number, author=author)


# The seat's whole half of the meta-documentation stages: one prose answer, which
# the driver persists here and then substitutes into the packaged overview
# template beside the counts it read out of the KB. Deliberately not under
# `review/` — that grammar is the findings loop's, and a file there belongs to
# one of its rounds — and deliberately one path rather than one per round: the
# latest answer is the one the document stands on, and the round bookkeeping is
# the loop's own.
_PROSE_FMT = "{stage}/overview-prose.md"
OVERVIEW_PROSE = _PROSE_FMT.format(stage="<stage>")


def overview_prose(*, stage: str) -> str:
    """Where ``stage``'s seat's prose answer lands, scratch-relative."""
    return _PROSE_FMT.format(stage=stage)


#: The whole scratch layout, as an enumerable structure rather than a comment
#: block: every path the driver constructs or briefs a seat with is a member,
#: so single-sourcing is a question asked of a tuple instead of a region.
SCRATCH_LAYOUT: tuple[str, ...] = (
    FINDINGS,
    OVERVIEW_PROSE,
)


def _layout_paths() -> str:
    return "\n".join(f"{SCRATCH_ROOT}/{path}" for path in SCRATCH_LAYOUT)


# --- the KB documents the last three stages author ---------------------------
#
# These live under `kb-root/`, not under the scratch layout, so they are never
# a row's `outputs` — those are scratch-relative patterns. The run loop names
# the KB path and declares it at the call, the way the mint-bearing rows
# already do for registers.

META_REVIEW_SEAT = "tech-writer-reviewer"


# --- slots filled from driver constants -------------------------------------
#
# The one wiring point where a code constant becomes brief prose. Every entry
# here exists because the same value is also something the toolchain *does*:
# the deviation vocabulary the driver accepts, the paths it constructs, the
# grammars it parses, the subcommands `kb_util` declares. A template restating
# any of them by hand would be a second definition, which this wiring rules out
# by construction rather than by a test comparing prose to code.

# The unscored literal, as the register format spells it and as
# `run._check_minted_grades` tests for it. One definition — `kb_index_lib`'s —
# reaching both the prose a minting seat writes it under and the scan that then
# checks nothing else was written.
PENDING_RULE = (
    f"An unscored value is written as the literal `{kb_index_lib.PENDING_LITERAL}`, and only as that. "
    f"A value left at the literal is unscored, which a mechanical scan reports as an unscored entry "
    f"rather than as a low one."
)

#: One slot per metadata write op: the sanctioned invocation up to and
#: including the op token, so a brief that has to name one writes
#: `@!insert-claim-entry!@ --values <path>` rather than a command line of its own
#: — no brief hand-writes a `kb_util` invocation.
#:
#: Both halves come from `kb_util`: the prefix from `INVOCATION`, the token from
#: `WRITE_OPS`, which is also what `build_parser` names its subparsers from. A
#: brief therefore cannot advertise an op the CLI does not have, and cannot
#: spell the invocation a way the consumer does not run.
#:
#: A slot key is the op token verbatim — the op names the slot that expands to
#: it, with no transliteration between the two spellings. These slots stop at
#: the op: the flag that follows it is :data:`VALUES_FLAG_SLOT` below, and only
#: the values file's own path — which differs per brief — is the template's to
#: write.
WRITE_OP_SLOTS: Mapping[str, str] = MappingProxyType({op: f"{kb_util.INVOCATION} {op}" for op in kb_util.WRITE_OPS})

#: The write ops' one flag, as a slot of its own rather than folded into the
#: nine above. A brief writes
#: `@!insert-claim-entry!@ @!values-flag!@ <path>`, so the whole invocation up to
#: the caller's own path is `kb_util`'s spelling and none of it is hand-typed —
#: which is what `TEMPLATE_PROHIBITIONS` then enforces by refusing a template
#: that spells the flag. Separate from the op slots because the fragment
#: `fragments/write-op-contract.tmpl` names the flag without naming any one op.
VALUES_FLAG_SLOT = kb_util.VALUES_FLAG

CONSTANT_SLOTS: Mapping[str, str] = MappingProxyType(
    {
        **WRITE_OP_SLOTS,
        "values-flag": VALUES_FLAG_SLOT,
        "deviation-kinds": ", ".join(envelope.DEVIATION_KINDS),
        "layout-paths": _layout_paths(),
        "scope-line-contract": envelope.SCOPE_LINE_CONTRACT,
        "pending-rule": PENDING_RULE,
    }
)


# --- what a brief says when it hands a seat a path ----------------------------

#: What a brief says when a slot has nothing to carry. A slot is never left
#: empty: an empty slot reads as a truncated brief, while a named absence reads
#: as an absence. It is the one value a path slot may carry that is not a path.
NOTHING = "(none)"

#: The path slots a brief must carry a real path for. Each names something an
#: earlier stage has already produced, so an absent one is that stage having
#: failed quietly rather than an input this build may not have: ``kb-root`` is
#: the tree the head wrote, ``readme-path`` is what ``ov.docs`` assembles in the
#: stage immediately before the review, and ``conventions-path`` is what
#: ``phase-3a``'s readiness stamp seeds two stages earlier.
REQUIRED_PATH_SLOTS: frozenset[str] = frozenset({"kb-root", "readme-path", "conventions-path"})

#: The path slots whose subject a build may legitimately not have, and which
#: therefore admit :data:`NOTHING`. A first review round has no findings to
#: answer, which is an absence with a name rather than a file that is missing.
OPTIONAL_PATH_SLOTS: frozenset[str] = frozenset({"remediation-source-path"})

#: Every slot of this driver's own vocabulary whose value is a filesystem path,
#: and the whole of it. ``call.Caller`` holds two things of one: the value is an
#: absolute path that exists, or — for an optional slot alone — :data:`NOTHING`.
#:
#: **Absolute**, because a seat resolves a relative path against a working
#: directory this driver sets and no brief states, so a relative path in a brief
#: is a path with no base. That is the ambiguity ``call.CallRequest`` already
#: refuses for the artifacts a step declares, asked here on the side of the call
#: that carries paths *in*. A live review handed ``kb-root/README.md`` searched
#: for a directory of that name instead of resolving it, reviewed a different
#: repository's knowledge base, and returned three critical findings about it.
#:
#: **Existing**, because the alternative to a precondition is an instruction,
#: and a seat handed a path to a file that is not there has to be relied on to
#: report that rather than to find something nearby. A dangling path is never
#: the right thing to state: where a build may not have the subject, the slot
#: is optional and carries the named absence instead.
#:
#: Keyed by slot rather than by row so one entry closes the slot wherever a row
#: fills it — including the slots of a template no row dispatches today.
PATH_SLOTS: frozenset[str] = REQUIRED_PATH_SLOTS | OPTIONAL_PATH_SLOTS


# --- what a template may never say --------------------------------------------

# `start` is a stage id and an ordinary English word. Matched as prose it
# would fail every template that says "start with the charter", so it is
# flagged only in its sequencing spelling. Every other id is unambiguous.
AMBIGUOUS_STAGE_IDS = frozenset({"start"})

# The two ledger-write verbs, which the driver owns outright. Both are banned
# by name: banning only one leaves a brief free to spell the other.
SEQUENCING_TOKENS: tuple[str, ...] = (LedgerOp.ADVANCE_STEP.value, LedgerOp.START_BUILD.value)

# The three metadata marker openers `kb_write.render` alone composes.
# A brief that spells one is a freehand
# instruction — the agent is being told to hand-write metadata the write API
# exists to compose — and freehand sites are not a list a reviewer re-checks by
# hand. They join the sequencing tokens in one map because the lint asks one
# question of a template line: does it say something it may not say.
METADATA_MARKER_TOKENS: tuple[str, ...] = ("<!-- id:", "<!-- kb-frontmatter", "<!-- claim-quality:")

# The write ops' one flag. A template names it through
# `@!values-flag!@`; spelling it by hand is the same freehand act removed from
# the op token, and it is the token a rename would leave stale in every brief at
# once. Banning the literal is what makes "no site hand-types the flag" a
# property of the template set rather than of the sites that happen to exist.
WRITE_FLAG_TOKENS: tuple[str, ...] = (kb_util.VALUES_FLAG,)


def _template_prohibitions() -> dict[str, re.Pattern[str]]:
    # Boundaries exclude `.` and `-` so that one id does not match inside
    # another that extends it — each is flagged under its own name — and so a
    # layout path the driver itself supplies (`review/phase-3a-r1-…`) is not
    # read as prose naming a stage.
    patterns: dict[str, re.Pattern[str]] = {}
    for stage_id in kb_pipeline.STAGE_IDS:
        body = re.escape(stage_id)
        if stage_id in AMBIGUOUS_STAGE_IDS:
            patterns[f"--stage {stage_id}"] = re.compile(rf"--stage\s+{body}(?![\w.-])", re.IGNORECASE)
        else:
            patterns[stage_id] = re.compile(rf"(?<![\w.-]){body}(?![\w.-])", re.IGNORECASE)
    for token in SEQUENCING_TOKENS + METADATA_MARKER_TOKENS + WRITE_FLAG_TOKENS:
        patterns[token] = re.compile(re.escape(token), re.IGNORECASE)
    return patterns


TEMPLATE_PROHIBITIONS: Mapping[str, re.Pattern[str]] = MappingProxyType(_template_prohibitions())


# --- the row -----------------------------------------------------------------


@dataclass(frozen=True)
class LoopSeries:
    """One capped revision series: its filename letter, its cap, its escalation."""

    letter: str
    cap: int
    cap_barrier: str


@dataclass(frozen=True, kw_only=True)
class Step:
    """One row of the step table.

    ``outputs`` are layout patterns, scratch-relative, with ``<...>`` marking a
    segment the run loop expands (a volume slug, a round number). ``slots`` are
    the per-call values the run loop computes; fragment slots and the constants
    of :data:`CONSTANT_SLOTS` are the composer's and are deliberately absent.
    """

    id: str
    stage: str
    unit: Unit
    writer: Writer = Writer.NONE
    seat: str | None = None
    template: str | None = None
    slots: tuple[str, ...] = ()
    outputs: tuple[str, ...] = ()
    parses: tuple[Parse, ...] = ()
    raises: tuple[str, ...] = ()
    series: tuple[LoopSeries, ...] = ()
    ledger_op: LedgerOp | None = None
    #: This row's seat authors register entries. Every graded field the KB
    #: has — a claim's rigor, a support's rigor, a warrant edge's on-point
    #: fraction — lives in one, so this is the property that makes
    #: ``run._check_minted_grades`` necessary, and the run loop reads it off
    #: the row rather than off a list of ids. A row added with this property
    #: is guarded by declaring it, and only by declaring it: the same rows
    #: are the ones that must be briefed with an existing-id inventory, and
    #: ``test_kb_driver_steps`` holds the two facts equal so neither can be
    #: set without the other.
    writes_register: bool = False
    #: This row runs a tool that spawns its **own** model, through a seam no
    #: flag of this driver reaches — ``kb_claimgraph``'s ``ask.SeatAsk``. Every
    #: other call a run makes is the driver's own dispatch, which ``replay.py``
    #: substitutes for, so this property is exactly the set of rows a
    #: ``--dry-run`` cannot stand in for. :attr:`spends_inference` is what reads
    #: it; nothing else does.
    spends_own_inference: bool = False

    @property
    def barrier_pairs(self) -> tuple[str, ...]:
        """Every ``<stage>.<kind>`` this row can raise, including its caps' escalations."""
        return self.raises + tuple(series.cap_barrier for series in self.series)

    @property
    def spends_inference(self) -> bool:
        """Whether running this row costs a model call, by whichever route.

        The two routes are not otherwise comparable and that is why this exists:
        :attr:`spends_own_inference` is a model spawned *inside* a tool the
        driver invokes, and a row naming a ``seat`` is a model the driver
        dispatches through its own transport. Only the second is what
        ``--dry-run`` replays. A run spending no inference does without both, so
        it is this union — never either half — that decides which rows it walks.
        """
        return self.spends_own_inference or self.seat is not None


# --- the table ---------------------------------------------------------------

_START = "start"
_DOCUMENT_GRAPH = "document-graph"
_SPINE_SEED = "spine-seed"
_CLAIMS_DECLARED = "claims-declared"
_CLAIMS_DISCOVERED = "claims-discovered"
_DEPENDS_ATTRIBUTED = "depends-attributed"
_PHASE_3A = "phase-3a"
_OVERVIEW_DRAFTED = "overview-drafted"
_PHASE_5 = "phase-5"


def _through(stage: str) -> tuple[str, ...]:
    """The stage vocabulary up to and including ``stage``. Order is never restated here."""
    return kb_pipeline.STAGE_IDS[: kb_pipeline.STAGE_IDS.index(stage) + 1]


#: Every stage this table holds rows for, and every stage a run walks — the
#: whole pipeline. ``_through(_PHASE_5)`` rather than ``kb_pipeline.STAGE_IDS``
#: directly, so that a stage appended to the vocabulary after ``phase-5``
#: arrives here as a row this table is missing rather than as a stage the walk
#: silently claims to cover.
TABLE_STAGE_IDS: tuple[str, ...] = _through(_PHASE_5)

STEPS: tuple[Step, ...] = (
    # --- pre-stage: before `start` is recorded -------------------------------
    # `pre.lock` holds <repo>/.claude-temp/kb-driver.lock — the REPOSITORY's
    # lock, not the run directory's, so a second `--run-dir` cannot slip past
    # it. A live pid there is exit 16.
    Step(id="pre.lock", stage=_START, unit=Unit.DRIVER_OP, writer=Writer.DRIVER),
    # Preflight's stdout is relayed verbatim; rc != 0 is exit 14. Nothing is
    # read back off it: its `runner-file` FACT is a statement to the operator,
    # and `seed.graph-init` — which needs the same answer to decide whether to
    # raise `spine-seed.runner-choice` — asks the working tree itself. This row
    # is a `start` row, and a resume skips the stage whole, so an answer carried
    # from here would be absent on exactly the invocations that resume.
    Step(id="pre.preflight", stage=_START, unit=Unit.DRIVER_OP),
    # A charter is optional and is never composed here: the row resolves
    # whether one stands at the configured path, so the record and the two
    # briefs that quote it read one answer instead of each asking the
    # filesystem their own question.
    Step(id="pre.charter", stage=_START, unit=Unit.DRIVER_OP),
    Step(id="pre.proceed", stage=_START, unit=Unit.DRIVER_OP, raises=("start.proceed",)),
    # The launch guard, and it is a launch guard because of where it sits: every
    # row of this stage is skipped once `start` is recorded, so this row runs on
    # the invocation that opens a build and on no other. A resume therefore
    # never meets it, which is what lets it refuse the one state that destroys
    # work — a build opened over a `kb-root/` somebody else's build filled — and
    # still let the same populated tree through on every invocation after.
    # Last before `start.record` on purpose: nothing between the reading and the
    # first write can change the answer.
    Step(id="pre.kb-root", stage=_START, unit=Unit.DRIVER_OP),
    # `start-build`, carrying `--charter <path>` only where a charter stands;
    # rc 5 (already started) reads as done.
    Step(
        id="start.record",
        stage=_START,
        unit=Unit.DRIVER_OP,
        writer=Writer.TOOL,
        ledger_op=LedgerOp.START_BUILD,
    ),
    # --- the head: the build's own production ---------------------------------
    #
    # Five tool rows and their records. Every one of them invokes a module and
    # reads an exit code, so none briefs a seat and none raises a barrier of its
    # own — the two exceptions being stated where they sit. The order is forced
    # end to end: the seed refuses a `kb-root/` with no tree in it, the declared
    # pass refuses a `kb-root/` with no spine, discovery reads what the declared
    # pass left awaiting, and stage D authors edges over the claims discovery
    # minted. Each stage's boundary commit is also what leaves the worktree
    # clean for the next stage's tool, which is why the seed is a stage of its
    # own rather than a row of `start`: its preflight refuses the dirty worktree
    # the document graph has just created.
    #
    # No row here is conditional. Each runs on the invocation that walks its
    # stage and never again, because a recorded stage is not re-walked — so
    # what keeps `dg.build` off a tree a finished build stamped is the ledger,
    # and what keeps it off a tree nobody here built is `pre.kb-root`.
    # --- document-graph — LaTeX volumes in, the Markdown tree out -------------
    # `kb_docgraph`: rc 1 (a check failed) → exit 11; rc 2 (a source or the
    # bibliography is not there) → exit 14.
    Step(id="dg.build", stage=_DOCUMENT_GRAPH, unit=Unit.DRIVER_OP, writer=Writer.TOOL),
    Step(
        id="dg.record",
        stage=_DOCUMENT_GRAPH,
        unit=Unit.DRIVER_OP,
        writer=Writer.TOOL,
        ledger_op=LedgerOp.ADVANCE_STEP,
    ),
    # --- spine-seed — the claim-graph metadata seed ----------------------------
    # `kb_util graph-init`: rc 3 (kb-root holds no document tree) → exit 14, the
    # stage above not having produced one; rc 2 → exit 14; rc 1 → 11. The one
    # barrier the head raises, and it is the same question it has always asked:
    # a repository carrying neither runner file cannot be told where the include
    # line goes, and no other row of the build can answer it either. The row
    # reads that condition off the working tree when it runs, so the barrier is
    # reachable on a resume as well as on a launch.
    Step(
        id="seed.graph-init",
        stage=_SPINE_SEED,
        unit=Unit.DRIVER_OP,
        writer=Writer.TOOL,
        raises=("spine-seed.runner-choice",),
    ),
    Step(
        id="seed.record",
        stage=_SPINE_SEED,
        unit=Unit.DRIVER_OP,
        writer=Writer.TOOL,
        ledger_op=LedgerOp.ADVANCE_STEP,
    ),
    # --- claims-declared — the graph the author marked, mechanically ----------
    Step(
        id="declared.build",
        stage=_CLAIMS_DECLARED,
        unit=Unit.DRIVER_OP,
        writer=Writer.TOOL,
    ),
    Step(
        id="declared.record",
        stage=_CLAIMS_DECLARED,
        unit=Unit.DRIVER_OP,
        writer=Writer.TOOL,
        ledger_op=LedgerOp.ADVANCE_STEP,
    ),
    # --- claims-discovered — stage C-inf ---------------------------------------
    # One of the head's two inference-spending rows, and both spend it inside
    # `kb_claimgraph` rather than through this driver's own transport: the seat
    # is that package's `ask.SeatAsk`, which no flag of this driver reaches. So
    # So `--dry-run`, which replaces the models this driver dispatches, cannot
    # replace either of these — which is what `spends_own_inference` marks, and
    # the reason it stays a field of its own beside `seat` rather than being
    # collapsed into `Step.spends_inference`.
    #
    # `--no-inference` excludes this row outright, which is not a bound: the
    # walk continues, `discover.record` still writes the boundary, and the
    # documents this row would have read keep the awaiting reason the declared
    # pass wrote (ARCHITECTURE.md, The Driver).
    Step(
        id="discover.build",
        stage=_CLAIMS_DISCOVERED,
        unit=Unit.DRIVER_OP,
        writer=Writer.TOOL,
        spends_own_inference=True,
    ),
    Step(
        id="discover.record",
        stage=_CLAIMS_DISCOVERED,
        unit=Unit.DRIVER_OP,
        writer=Writer.TOOL,
        ledger_op=LedgerOp.ADVANCE_STEP,
    ),
    # --- depends-attributed — stage D -----------------------------------------
    # **The row runs in every build, and only part of it costs a call.** Stage
    # D's narrowing settles an edge wherever containment decides it — a `\ref`
    # inside a proof body is direction-bearing by construction — and only the
    # pairs it leaves open go to `ask.SeatAsk` through `ask.ModelSelector`. The
    # row therefore declares no inference of its own: dropping it would discard
    # the settled edges along with the questions, and a graph that records no
    # relationship it holds in hand asserts its claims rest on nothing
    # (`kb_tools/AGENTS.md`, the second bullet). `run.py` passes the tool its
    # own `--no-inference` instead, and the open pairs are reported rather than
    # guessed at.
    Step(
        id="depends.attribute",
        stage=_DEPENDS_ATTRIBUTED,
        unit=Unit.DRIVER_OP,
        writer=Writer.TOOL,
    ),
    Step(
        id="depends.record",
        stage=_DEPENDS_ATTRIBUTED,
        unit=Unit.DRIVER_OP,
        writer=Writer.TOOL,
        ledger_op=LedgerOp.ADVANCE_STEP,
    ),
    # --- phase-3a — validation gate -------------------------------------------
    # Gate and record, and nothing between them: `kb-refresh` then `kb-verify`,
    # green or the run stops. The repair wave this stage used to drive is gone
    # with its subject — every gate the three verifiers run compares one
    # mechanically-produced artifact against another, so a red one is a defect
    # in a tool or in what was authored, and neither is a seat's to rewrite in
    # the KB.
    Step(id="p3a.gate", stage=_PHASE_3A, unit=Unit.GATE),
    Step(
        id="p3a.record",
        stage=_PHASE_3A,
        unit=Unit.DRIVER_OP,
        writer=Writer.TOOL,
        ledger_op=LedgerOp.ADVANCE_STEP,
    ),
    # --- overview-drafted — the overview document, written --------------------
    # **The stage assembles the document; the seat is never asked to compose
    # one.** Every count the overview document states already sits in `.index/`
    # or in the tree, so it is read and substituted rather than written out by a
    # model and then audited by a second one. What is left for a seat is the
    # part no read produces — what this corpus is and where a reader starts —
    # and the answer to that is prose and nothing else, which is a return shape
    # with nothing to get wrong. Hence `Writer.DRIVER`: the returned text *is*
    # the artifact, persisted under the scratch layout, and `run._meta_docs`
    # substitutes it.
    #
    # **The draft is a stage of its own because it spends a model call and the
    # review that follows it spends another.** A stage holding both would leave
    # the draft's answer behind a review that can fail, and a resume would buy it
    # a second time; the boundary below is what earns it once
    # (`kb_pipeline.STAGES`, the granularity comment).
    #
    # The docent check runs *first* for the same reason read the other way: it
    # can fail, so it may not stand behind the call. An incomplete install is
    # also cheaper to meet before a seat is dispatched than after. A driver-op
    # over an imported constant: the docent commands are what make a finished KB
    # navigable, and their absence is an incomplete install (exit 14), never a
    # barrier — there is no answer that would install them.
    Step(id="ov.docent-check", stage=_OVERVIEW_DRAFTED, unit=Unit.DRIVER_OP),
    Step(
        id="ov.docs",
        stage=_OVERVIEW_DRAFTED,
        unit=Unit.SINGLE,
        writer=Writer.DRIVER,
        seat="tech-writer",
        template="phase-5-overview-passage.single.tmpl",
        slots=("kb-root", "remediation-source-path"),
        outputs=(OVERVIEW_PROSE,),
    ),
    Step(
        id="ov.record",
        stage=_OVERVIEW_DRAFTED,
        unit=Unit.DRIVER_OP,
        writer=Writer.TOOL,
        ledger_op=LedgerOp.ADVANCE_STEP,
    ),
    # --- phase-5 — the review cycle over what was drafted ---------------------
    # `ov.docs` and `p5.fix` share one template and one slot list: there is no
    # separate fix template, and the two calls differ only in whether a
    # reviewer's findings are the input — which is a slot, filled with a named
    # absence on the first pass. The template's name carries the stage the pair
    # used to share; it is the prompt set's, not this table's, and renaming it is
    # not this table's to do.
    Step(
        id="p5.review",
        stage=_PHASE_5,
        unit=Unit.SINGLE,
        writer=Writer.DRIVER,
        seat=META_REVIEW_SEAT,
        template="phase-5-review.single.tmpl",
        slots=("readme-path", "conventions-path"),
        outputs=(FINDINGS,),
        parses=(Parse.VERDICT,),
    ),
    # phase-5 has no separate loop row, so the row that escalates is the
    # row that drives: `p5.fix` owns the review-fix-re-review cycle.
    Step(
        id="p5.fix",
        stage=_PHASE_5,
        unit=Unit.SINGLE,
        writer=Writer.DRIVER,
        seat="tech-writer",
        template="phase-5-overview-passage.single.tmpl",
        slots=("kb-root", "remediation-source-path"),
        outputs=(OVERVIEW_PROSE,),
        series=(
            LoopSeries(
                letter=SERIES_INITIAL,
                cap=kb_pipeline.PHASE_5_FIX_CAP,
                cap_barrier="phase-5.cap-exhausted",
            ),
        ),
    ),
    Step(
        id="p5.record",
        stage=_PHASE_5,
        unit=Unit.DRIVER_OP,
        writer=Writer.TOOL,
        ledger_op=LedgerOp.ADVANCE_STEP,
    ),
)

STEP_IDS: tuple[str, ...] = tuple(step.id for step in STEPS)
STEPS_BY_ID: Mapping[str, Step] = MappingProxyType({step.id: step for step in STEPS})


def steps_for(stage: str) -> tuple[Step, ...]:
    """Every row of one stage, in table order."""
    return tuple(step for step in STEPS if step.stage == stage)


def applies(step: Step, *, spend_inference: bool = True) -> bool:
    """Whether a row runs in this run — the one condition a run still carries.

    ``spend_inference`` is **row-level rather than a bound**: a run spending none
    drops every row that would cost a model call and walks every stage
    regardless, so the stages around an excluded row still run and the build
    still closes out.

    There is no second condition. A row used to be able to name the build mode
    it belonged to, which is how a build entering against a KB it had not built
    skipped the rows that would overwrite it; the modes are gone, and what keeps
    those rows off such a tree is the ledger (a recorded stage is not re-walked)
    and the launch guard that refuses to open a build over one.

    A stage's ledger row never costs a model call — a record dispatches no seat
    and invokes no tool that spawns one — which is what lets the walk continue
    past a stage whose work it dropped.
    """
    return spend_inference or not step.spends_inference


def inference_rows(stage: str) -> tuple[str, ...]:
    """The ids of ``stage``'s rows a run spending no inference does without.

    Derived from :attr:`Step.spends_inference`, never from a stage id: a stage
    that gains or loses an inference-spending row moves this answer without an
    edit here.
    """
    return tuple(step.id for step in steps_for(stage) if step.spends_inference)
