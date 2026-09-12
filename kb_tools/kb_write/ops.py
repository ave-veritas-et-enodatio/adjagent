"""Op semantics — the validation ladder, mint fusion, report lines, refusal codes.

**Surface-independent by construction.** There is no ``argparse`` here, no
``sys.argv``, and no ``sys.exit``. Every op's inputs, validation ladder, refusal
reasons, report lines and exit code are defined and tested in this module; the
CLI is a thin adapter that binds a surface shape to them. Whether these ops are
exposed as flags or as subcommands is therefore a change to the CLI and nothing
else.

**The ladder, in order.** Steps 1–4 refuse before anything is composed;
5–7 compose and prove; only step 8 touches a live file.

1. **Well-formedness** — delegated whole to :mod:`values`. The grammar, the
   closed per-op key vocabulary and the inherited value domains are that
   module's, and a refusal it returns names the field and the located line.
2. **Path containment** — every target resolves inside ``kb-root/`` after
   symlink resolution, through :func:`store.resolve_target`, before any id is
   resolved and before anything is rendered.
3. **Reference closure and uniqueness** — two questions, through the two
   functions that answer them, **uniqueness asked first**. That order is the
   driver's existing rule (``run.py:1845-1849``) — every later reading is taken
   over a map keyed by id,
   so asking about duplicates afterwards would be asking a question of a damaged
   answer. :func:`kb_index_lib.scan_duplicate_register_ids` reports a collision
   that :func:`kb_index_lib.scan_authored_ids` **structurally cannot see**: the
   inventory is keyed by id and keeps the first keyer, so a duplicate register
   entry looks exactly like a clean inventory. Both scans read the whole store
   before step 2 has looked at the target, so both are wrapped
   (:func:`_scanned`): a file under ``kb-root/`` that will not decode is the
   located exit 2 of :class:`_Unreadable`, never a traceback.
4. **Census**, 5. **render**, 6. **temp**, 7. **prove**, 8. **replace** — all
   :func:`store.apply_edits`'s. This module composes the ``Edit`` batch and the
   expected records; it never opens a live KB file for writing.

**One file, one edit.** A values file may carry a batch, and a scoring
wave's batch is ordinarily bound for a single register. Two edits naming one
path both splice from the same baseline, so ``store`` refuses the pair rather
than letting the second silently drop the first; this module therefore folds
every entry bound for one file into one edit (:func:`_batch`), summing their
deltas and proving all of their records against the one finished candidate.

**Mint fusion**, over ``clm-``, ``sup-`` and ``exp-`` alike. An insert op
mints the id itself and writes its canonical entry in the same act. There is no
reachable path that produces an id without its entry, and no reachable path that
writes an entry for an id the tool did not mint — **structurally**, not by
discipline: an insert op's inputs are exactly its own parameters plus the closed
values vocabulary of :data:`values.OP_FIELDS`, and neither channel admits a node
id for the entry being created. The draw is
:func:`kb_schema.mint_id`, collision-checked against both scans.
``test_kb_write_ops.py`` asserts the partition over the whole of :data:`OPS`, so
a future op that accepted an id for insert fails there rather than shipping.

**Three outcomes, three codes.** :class:`ExitCode` and
:data:`EXIT_FOR_STATUS` are the stable mapping the CLI binds and ``ledger.py``'s
per-op ``exits`` vocabulary enrolls. **7 — refused**: the values are
wrong; fix them and call again. **8 — retry unchanged**: the values were right
and the write did not happen for a reason nothing about them can fix.
**2 — environment unfit**: an unresolvable root, an unreadable tree, a usage
error. Re-asking a model for values that were already correct is how a duplicate
id gets written, which is why 7 and 8 are not one code.

**Report lines, not a logging framework.** Output is the existing uniform
``[<tag>] STATUS name detail`` convention (``kb_util.PreflightItem``), with
``PASS``/``FAIL`` gating, ``FACT`` never gating, and every failure detail
carrying an inline ``restore:`` clause naming the corrective call. Nothing here
prints: :class:`Result` carries the lines and the exit code, and the surface
decides where they go. A ``restore:`` clause names the *op* and the corrective
action rather than a literal command line, because no site outside the CLI's
own invocation constant may hand-write one.

**Dependency direction**: ``ops`` → :mod:`values`, :mod:`store`,
:mod:`render`, :mod:`kb_index_lib`'s two scan functions and production parsers,
:mod:`kb_schema`'s mint, and ``kb_util``'s report-status tokens. Plus
:mod:`kb_links`, for the one thing that module owns: the link primitive every
gate reads a markdown link with. It dispatches no inference, composes no brief,
names no stage id and names no record verb — and it still imports no
``verify_*`` module, which is why the read-only op reaches the citation gate's
checks through objects the gate shares rather than through the gate.

**Two registries, one vocabulary.** :data:`OPS` is the write surface — the nine
ops that touch a file — and :data:`READ_OPS` is the one op that reads to
verify and prints. They are disjoint, and their union is exactly
:data:`values.OP_FIELDS`' key set.

**Six in-package reaches, all deliberate, all for one reason**: the alternative
to each is a second copy of a rule that already has exactly one owner, which is
where drift starts. :class:`store._Refused` is caught around
:func:`store.resolve_target` so step 2 runs in its stated place without a second
implementation of the containment rule; store's two fold-break patterns bound
the depends-on bullet insert rather than a re-typed key list;
``render._format_score`` renders a rigor value's text;
``kb_index_lib._LEAF_DECL_RES`` is what decides which frontmatter keys declare a
node; ``kb_index_lib._SUPPORTS_PAIR_RE`` is what decides where a staged
fan-out block ends; and ``kb_index_lib._parse_depends_on_line`` is what says
which edge a rendered depends-on bullet *is*, which is the question
:func:`_reader_target` has to answer to tell a new edge from one already there.
Each is the same reach ``store`` itself makes into ``kb_index_lib``'s compiled
patterns, for the same reason.

**The leaf-writing ops carry their own readback.** ``store``'s proof
surface is register-shaped — :class:`store.ExpectedEntry` carries a register
entry's fields and its census counts register records — so a leaf write proves
nothing there. Those ops therefore prove their candidate through the production
*leaf* parsers, inside the splice callable, on a temp beside the target: the
same act, the same pair of writer and reader, and still before any live file is
touched. A mismatch raises out of :func:`store.apply_edits`, whose ``finally``
removes every temp on any path out, including "an exception nobody anticipated".
**What each of those readbacks covers is declared** in
:data:`LEAF_PROOF_COVERAGE` and asserted against each op's values vocabulary, so
a field that reaches a document with nothing reading it back is a failing test
rather than a silently uncovered case.
"""

import bisect
import difflib
import posixpath
import re
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from enum import IntEnum
from pathlib import Path
from typing import TypeVar

from kb_tools import kb_index_lib, kb_links, kb_schema, kb_util
from kb_tools.kb_write import render, store, values

#: One store scan's return, so :func:`_scanned` wraps either of the two scan
#: functions without collapsing what it hands back to ``object``.
_Scanned = TypeVar("_Scanned")

# ---------------------------------------------------------------------------
# Exit codes — the stable mapping the CLI binds
# ---------------------------------------------------------------------------


class ExitCode(IntEnum):
    """The process exit codes these ops resolve to.

    Nothing in this module calls :func:`sys.exit`; a :class:`Result` carries one
    of these and the surface returns it. ``1`` (the existing gate meaning),
    ``3`` and ``4``/``5``/``6`` (``kb_pipeline``'s) are untouched by the write
    API and have no member here — a code this module never produces has no
    business being nameable from it.
    """

    #: Written. Every edit in the batch landed.
    WRITTEN = 0
    #: Printed. The read-only op composed its output and touched no file. An
    #: **alias** of :attr:`WRITTEN` rather than a fourth code: "the op did what
    #: it was asked" is one outcome whether the doing was a write or a print,
    #: and a caller branching on success must not have two zeroes to know about.
    PRINTED = 0
    #: Environment unfit: unresolvable root, unreadable tree, usage error.
    ENVIRONMENT = 2
    #: Refused: the values are wrong, nothing was written. Fix and call again.
    REFUSED = 7
    #: Retry unchanged: a concurrent writer moved the file. Nothing was
    #: written and nothing about the values can fix it — re-run them as they
    #: are, never re-author them.
    RETRY = 8


#: ``store``'s three outcomes to the three codes. The one place a store status
#: becomes an exit code (``store`` knows no exit codes).
EXIT_FOR_STATUS: Mapping[store.Status, ExitCode] = {
    store.Status.WRITTEN: ExitCode.WRITTEN,
    store.Status.REFUSED: ExitCode.REFUSED,
    store.Status.RETRY: ExitCode.RETRY,
}


# ---------------------------------------------------------------------------
# Report lines
# ---------------------------------------------------------------------------

#: The report tag, beside ``[preflight]`` and ``[kb-build]``.
REPORT_TAG = "kb-write"

# Wider than preflight's 17 because these names are kb-root-relative paths and
# dotted value keys rather than one-word check names.
_ITEM_NAME_WIDTH = 28


@dataclass(frozen=True)
class ReportItem:
    """One reported line, in the toolchain's uniform shape.

    ``status`` is one of ``kb_util``'s three tokens — imported rather than
    re-spelled, so ``PASS``/``FAIL``/``FACT`` mean the same thing here as in
    every other report the toolchain emits. ``FACT`` never gates; a minted id is
    reported as one because it is state the caller needs and not a verdict.

    ``detail`` prose is the prompt engineer's to revise, and no test asserts its
    wording — only the status token, the named identity, and the exit code.
    """

    status: str
    name: str
    detail: str

    def line(self) -> str:
        return f"[{REPORT_TAG}] {self.status} {self.name:<{_ITEM_NAME_WIDTH}} {self.detail}"


@dataclass(frozen=True)
class Result:
    """What an op did: the exit code, the report, and the two facts a caller acts on.

    ``minted`` is the ids the op brought into being, in the order it minted them
    — the insert ops' return value, which must be printed. ``written`` is
    the kb-root-relative path of every file replaced.

    It is empty on every non-zero code but one: a batch the environment
    interrupts partway (:class:`store.BatchInterrupted`) reports exit 2 and the
    targets that landed before the failure. All-or-nothing is over
    *verdicts* — no batch is half-refused — and cannot be had over an I/O
    failure across several directories without a journal this toolchain does not
    have. Reporting
    nothing written on a run that wrote is the one answer that is neither
    guarantee.

    ``printed`` is the read-only op's whole output: the composed lines, in
    entry order, that the surface puts on stdout and the caller transcribes.
    It is separate from ``report`` because the two have different audiences —
    a report line is read by whoever is diagnosing the call, and a printed line
    is copied verbatim into a document, so a surface that interleaved them
    would be inviting the wrong bytes into the corpus.
    """

    op: str
    exit_code: ExitCode
    report: tuple[ReportItem, ...] = ()
    minted: tuple[str, ...] = ()
    written: tuple[str, ...] = ()
    printed: tuple[str, ...] = ()

    @property
    def ok(self) -> bool:
        return self.exit_code is ExitCode.WRITTEN

    def lines(self) -> tuple[str, ...]:
        return tuple(item.line() for item in self.report)


# ---------------------------------------------------------------------------
# Internal refusal signals
# ---------------------------------------------------------------------------


class _Refused(Exception):
    """A refusal raised anywhere in steps 2–5, carried to :func:`_execute`.

    ``name`` is the offending identity the report line must carry — a value key,
    an id, or a path. ``restore`` is the corrective clause, appended to the
    detail; it names the op and the action, never a command line.
    """

    def __init__(self, name: str, detail: str, *, restore: str) -> None:
        super().__init__(detail)
        self.name = name
        self.detail = detail
        self.restore = restore


class _Unreadable(Exception):
    """A file under ``kb-root/`` this package could not read, named.

    Raised from two places, and both are exit 2: step 3's store walk meeting a
    file that will not decode (:func:`_scanned`), and ``render-citation``
    failing to *open* a cited document that exists (:func:`_compose_citation`).
    A caller's values cannot fix either — the first names no file the caller
    chose, and the second names one the caller chose correctly.

    **Exit 2, not 7, and the reasoning is the point.** A refusal (7) tells the
    caller their values are wrong: fix them and call again. Nothing in a values
    file can fix a latin-1 byte in a register three directories away, and
    re-asking a model for values that were already right is the duplicate-mint
    path the three-code ladder exists to keep closed. Exit 2 is "environment
    unfit: unresolvable root, **unreadable tree**, usage error" — and an
    unreadable tree is exactly what a KB file that will not decode is.

    The decode stays strict and a failure stays *refused* rather than
    ``errors="replace"``d. What is never licensed is the traceback: an uncaught
    :class:`UnicodeDecodeError` leaves the process at rc 1, which is outside the
    ladder and unenrolled in ``ledger._WRITE_OP_EXITS`` — so a driver-invoked op
    reports **15, broken tool** for a KB the tool read perfectly correctly.
    """

    def __init__(self, name: str, detail: str, *, restore: str) -> None:
        super().__init__(detail)
        self.name = name
        self.detail = detail
        self.restore = restore


class _ReadbackFailed(Exception):
    """A readback failure for a leaf write, raised from *inside* the splice callable.

    It is not a :class:`store.SpliceError`: that reason token means "a splice
    could not locate what it was asked to edit", and a check reported under the
    wrong identity is a failed check. Raising a type ``store`` does not catch
    lets it leave :func:`store.apply_edits` through the documented path — the
    ``finally`` that removes every temp on any exit, including an unanticipated
    exception — with no live file touched.
    """

    def __init__(self, subject: str, mismatched: Sequence[str]) -> None:
        super().__init__(subject)
        self.subject = subject
        self.mismatched = tuple(mismatched)


# ---------------------------------------------------------------------------
# Step 3's context: the store, read with the function that answers the question
# ---------------------------------------------------------------------------


@dataclass
class _Context:
    """One op invocation's view of the authored store, plus what it has minted.

    There is no shadow index behind this and no minted-id ledger file: the
    inventory is the authored Markdown, re-read on every invocation.
    """

    kb_root: Path
    inventory: Mapping[str, kb_index_lib.IdRecord]
    duplicates: Mapping[str, tuple[str, ...]]
    minted: list[str] = field(default_factory=list)
    #: State the caller needs that is not a verdict, reported as ``FACT`` beside
    #: the minted ids — the second thing an op discovers while composing and
    #: cannot return any other way. Filled during the splice, so it describes
    #: the baseline that was actually written into.
    noted: list[ReportItem] = field(default_factory=list)
    _titles: dict[str, str] = field(default_factory=dict)
    _read_registers: set[str] = field(default_factory=set)

    def title_of(self, node_id: str) -> str | None:
        """The register title of an already-resolved node, for a bullet's text.

        Read from the referent's own register rather than supplied by the
        caller: the title on a depends-on bullet is a restatement of the
        referent's heading, and asking an agent to retype it is one more byte
        -fidelity demand for no gain.
        """
        record = self.inventory.get(node_id)
        if record is None or record.register_path is None:
            return None
        if record.register_path not in self._read_registers:
            self._read_registers.add(record.register_path)
            path = self.kb_root / record.register_path
            for entry in kb_index_lib.parse_claim_quality_file(path, self.kb_root):
                self._titles.setdefault(entry.id, entry.title)
            for sup_id, raw in kb_index_lib.parse_support_quality_entries(path, self.kb_root).items():
                self._titles.setdefault(sup_id, raw["title"])
            for work in kb_index_lib.parse_work_entries(path, self.kb_root):
                self._titles.setdefault(work.id, work.title)
        return self._titles.get(node_id)


def _scanned(scan: Callable[[Path], _Scanned], kb_root: Path) -> _Scanned:
    """Run one of the two store scans, an undecodable KB file located.

    Both scans walk the store whole — every register, and for the inventory
    every leaf — before step 2 has looked at the target at all. So one file
    that will not decode is not a fact about the write being attempted: it
    decides the outcome of a write into a clean file three directories away,
    and unwrapped it does so as an rc-1 traceback out of a reader in another
    package. The strict decode is the reader's and stays the reader's;
    what belongs here is the refusal, inside the ladder and located.
    """
    try:
        return scan(kb_root)
    except UnicodeError as exc:
        raise _undecodable(kb_root, exc) from exc


def _undecodable(kb_root: Path, exc: UnicodeError) -> _Unreadable:
    """Name the KB file behind a decode failure, by re-walking to find it.

    A :class:`UnicodeDecodeError` carries the offending bytes and the offset
    but **not the path** — the reader that raised it held the path and dropped
    it — and a report that cannot say which file is a report the caller cannot
    act on. So the file is found rather than guessed: every ``*.md`` under the
    root, which is a superset of both scans' walks and therefore certain to
    contain whichever file raised. Where more than one will not decode it names
    the first; repairing that one re-runs into the next, which is the honest
    sequence for a corpus with two damaged files in it.
    """
    for path in sorted(kb_root.rglob("*.md")):
        try:
            path.read_text(encoding="utf-8")
        except UnicodeDecodeError as found:
            rel = path.relative_to(kb_root).as_posix()
            return _Unreadable(
                rel,
                f"is not valid UTF-8 (byte {found.start}: {found.reason}), so the store itself cannot be "
                f"read. Every write op reads the whole store before it resolves its target, so this file "
                f"stops writes into files that are perfectly well-formed",
                restore=f"restore: re-encode {rel} as UTF-8, or remove it from the KB, then re-run — the "
                f"values were never read and must not be re-authored",
            )
        except OSError:
            continue
    return _Unreadable(
        str(kb_root),
        f"could not be read as UTF-8 text ({exc}), and no single file under it accounts for the failure",
        restore="restore: repair the KB tree's encoding, then re-run — the values were never read and "
        "must not be re-authored",
    )


def _open_store(kb_root: Path) -> _Context:
    """Step 3's read of the store — **uniqueness asked first**.

    A KB already keying one id from two canonical register entries is refused
    before the inventory is even taken, because the inventory would be a
    collapsed and therefore damaged answer: it keys by id and keeps the first
    keyer, so it reports the collision as a clean single entry. Writing into
    that state is how a duplicate is compounded rather than caught.
    """
    duplicates = _scanned(kb_index_lib.scan_duplicate_register_ids, kb_root)
    if duplicates:
        node_id, registers = sorted(duplicates.items())[0]
        raise _Refused(
            node_id,
            f"is keyed by {len(registers)} canonical register entries ({', '.join(registers)}). "
            f"The authored-id inventory cannot see this — it is keyed by id and keeps the first "
            f"keyer, so a duplicate reads as a clean entry — and every later reading would be "
            f"taken over that collapsed answer",
            restore=f"restore: delete the duplicate '<!-- id: {node_id} -->' entry from all but "
            f"one register, then re-run this op unchanged",
        )
    return _Context(kb_root=kb_root, inventory=_scanned(kb_index_lib.scan_authored_ids, kb_root), duplicates=duplicates)


def _mint(ctx: _Context, kind: str) -> str:
    """Draw one fresh id of ``kind``, collision-checked against both scans.

    The draw is ``kb_schema``'s — the collision-checked draw stays a function of
    that module — and the exclusion set is stated over **both** scans plus the
    ids this invocation has already minted. The duplicate map is empty by the
    time control reaches here — :func:`_open_store` refuses on a non-empty one —
    so its contribution is vacuous today. It is named anyway because the union is
    what the draw must be checked against, and a future change to that refusal
    policy must not silently narrow it.
    """
    node_id = kb_schema.mint_id(kind, existing=set(ctx.inventory) | set(ctx.duplicates) | set(ctx.minted))
    ctx.minted.append(node_id)
    return node_id


# ---------------------------------------------------------------------------
# Step 2 and step 3's per-value checks
# ---------------------------------------------------------------------------


def _contained(kb_root: Path, rel_path: str, *, key: str) -> Path:
    """Step 2, in its stated place: resolve ``rel_path`` inside ``kb-root/``.

    ``store.resolve_target`` is the containment rule's one implementation and it
    signals with store's internal refusal type; catching it here is the reach
    the module docstring names. Re-running the check inside
    :func:`store.apply_edits` is not redundancy to be optimized away — it is the
    boundary check standing at the boundary, which is where it belongs.

    Takes the root rather than a :class:`_Context`, because containment is a
    question about a path and not about the store: the read-only op resolves no
    id, and handing it a context whose inventory it never reads would be
    asserting an emptiness that is not true of the KB.
    """
    try:
        return store.resolve_target(kb_root, rel_path)
    except store._Refused as exc:
        raise _Refused(
            key,
            f"{rel_path!r} {exc.detail}. Paths are relative to kb-root/, not to the repository root",
            restore=f"restore: correct {key} to a kb-root-relative path inside the KB and re-run",
        ) from exc


def _resolve(ctx: _Context, node_id: str, *, key: str, needs_register: bool) -> kb_index_lib.IdRecord:
    """Step 3's reference closure for one id-valued field.

    ``needs_register`` distinguishes the two homes a node id is canonically
    declared in. A ``clm-`` or ``sup-`` id is keyed by a register entry, so an
    id present only as a leaf-frontmatter citation is *not* resolved — that is
    the "claims member with no register entry" defect, which today is a silent
    drop. An ``exp-`` id has no register entry by construction: its canonical
    declaration is the ``exp-id:`` block in its hosting leaf, so existence in
    the inventory is the whole question.
    """
    record = ctx.inventory.get(node_id)
    if record is None:
        raise _Refused(
            f"{key}={node_id}",
            "does not resolve against the authored-id inventory. Every id-valued field must name a "
            "node that already exists at write time",
            restore=f"restore: create {node_id}'s node with the insert op for its kind, or correct "
            f"{key}, then re-run",
        )
    if needs_register and record.register_path is None:
        raise _Refused(
            f"{key}={node_id}",
            "is authored in leaf frontmatter but has no canonical register entry, so it is not a "
            "node any consumer can read a title, a rigor or a rationale from",
            restore=f"restore: insert {node_id}'s register entry, or correct {key}, then re-run",
        )
    return record


def _depends_targets(ctx: _Context, supplied: Iterable[values.DependsOnValue]) -> tuple[render.DependsOnTarget, ...]:
    """Resolve a ``depends-on`` list into rendered-bullet values.

    A framework target — ``INVARIANT-XX`` or ``Axiom N`` — is passed through
    unresolved: it is not a node id, it is absent from the authored-id inventory
    by construction, and its declaration site is the KB's framework source
    rather than a register. Resolving it would need a second inventory and a
    second failure mode; one function answers existence, and this is not a node
    whose existence it answers.
    """
    out = []
    for value in supplied:
        if render.is_claim_id(value.id) or render.is_work_id(value.id):
            # A work id resolves like a claim id and for the same reason: its
            # node is a register entry, so an edge into one that does not exist
            # is the dangling-target defect rather than a token to pass through.
            _resolve(ctx, value.id, key="depends-on.id", needs_register=True)
            out.append(
                render.DependsOnTarget(
                    target=value.id,
                    title=ctx.title_of(value.id),
                    context=value.context,
                    applicability=value.applicability,
                )
            )
        else:
            out.append(render.DependsOnTarget(target=value.id, title=None, context=value.context))
    return tuple(out)


# ---------------------------------------------------------------------------
# The current record, for an update op's expectation
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class _RegisterRead:
    """Every canonical record in one register, as the production parsers return it.

    Taken over **one text** — the baseline :func:`store.apply_edits` hands the
    splice — through one :func:`store.proof_temp`, because the parsers derive a
    record's identity from a file's own ``relative_to(kb_root)`` and so read a
    path rather than a string.

    One read rather than one per entry, which is also what it costs: a
    391-entry scoring wave re-parsed its register 391 times to state 391
    expectations about it.
    """

    register: str
    claims: Mapping[str, kb_index_lib.ClaimEntry]
    supports: Mapping[str, Mapping[str, object]]
    staged: Mapping[str, tuple[tuple[str, float | None], ...]]
    works: Mapping[str, kb_index_lib.ExternalWork]


def _read_register(document: str, *, target: Path, kb_root: Path) -> _RegisterRead:
    """Parse ``document`` as the register at ``target``, through the proof seam.

    ``PENDING_FRACTION`` is normalized to ``None`` here, the spelling every
    value carrier in this package uses for the authored ``*pending*`` literal,
    so the staging grammar is read once and translated once.
    """
    with store.proof_temp(target, document) as temp:
        return _RegisterRead(
            register=target.name,
            claims={record.id: record for record in kb_index_lib.parse_claim_quality_file(temp, kb_root)},
            supports=kb_index_lib.parse_support_quality_entries(temp, kb_root),
            staged={
                sup_id: tuple(
                    (claim_id, None if fraction is kb_index_lib.PENDING_FRACTION else fraction)
                    for claim_id, fraction in pairs
                )
                for sup_id, pairs in kb_index_lib.parse_register_staged_supports(temp).items()
            },
            works={work.id: work for work in kb_index_lib.parse_work_entries(temp, kb_root)},
        )


def _current_entry(read: _RegisterRead, node_id: str) -> store.ExpectedEntry:
    """The record as it stands in the baseline, as an expectation.

    An update op's intended record is the current one with exactly the named
    field changed, so the readback proves both halves at once: that the new
    value landed, and that **nothing else moved**.

    **Which bytes "current" means is load-bearing.** Read at plan time from the
    live file, while ``store`` reads the baseline it splices strictly later, a
    writer landing in that gap leaves the baseline post-writer (the freshness
    check passes) and the expectation pre-writer (the readback mismatches). The
    op then reports **7 — the values are wrong, correct them** for a call whose
    values were perfect, and 7's ``restore:`` clause is a re-ask: re-asking a
    model for values that were already right is how a duplicate id gets written.
    Measured with real processes: rc 7 at a 30% and a 50% interleave, rc 8 only
    at 70%.

    So the expectation is derived from the same bytes the splice runs on, and
    the two reads are one read. A writer that lands *before* that read is
    absorbed — the candidate is its content plus this op's one field, which is
    the correct write and rc 0 — and a writer that lands *after* it moves the
    file under the lock, where step 8's check answers **8, retry unchanged**.
    There is no window left in which a moved file can be reported as a bad
    value.

    A ``sup-`` entry's staged fan-out is part of "nothing else moved" and is
    read in for that reason: without it, a ``set-rigor`` on a support that
    stages pairs would state an expectation of *no* pairs against a candidate
    that still has them, and refuse a write that was entirely correct.
    """
    if node_id.startswith(f"{kb_schema.WORK_PREFIX}-"):
        work = read.works.get(node_id)
        if work is None:
            raise _Refused(
                f"id={node_id}",
                f"has no canonical entry in {read.register} that the production parser returns",
                restore=f"restore: correct id, or insert {node_id}'s work entry, then re-run",
            )
        return store.ExpectedEntry(
            node_id=node_id,
            title=work.title,
            rigor=work.strength,
            rationale=work.rationale,
        )
    if node_id.startswith("sup-"):
        raw = read.supports.get(node_id)
        if raw is None:
            raise _Refused(
                f"id={node_id}",
                f"has no canonical entry in {read.register} that the production parser returns",
                restore=f"restore: correct id, or repair {node_id}'s entry, then re-run",
            )
        return store.ExpectedEntry(
            node_id=node_id,
            title=raw["title"],  # type: ignore[arg-type]
            rigor=raw["quality"],  # type: ignore[arg-type]
            rationale=raw["rationale"],  # type: ignore[arg-type]
            depends_on=tuple(store.edge_of(e) for e in raw["depends_on"]),  # type: ignore[attr-defined]
            supports=read.staged.get(node_id, ()),
        )
    record = read.claims.get(node_id)
    if record is None:
        raise _Refused(
            f"id={node_id}",
            f"has no canonical entry in {read.register} that the production parser returns",
            restore=f"restore: correct id, or repair {node_id}'s entry, then re-run",
        )
    return store.ExpectedEntry(
        node_id=node_id,
        title=record.title,
        rigor=record.confidence,
        rationale=record.rationale,
        depends_on=tuple(store.edge_of(e) for e in record.depends_on),
        references=tuple(store.edge_of(e) for e in record.references),
        strengthen_by=tuple(item.text for item in record.strengthen_by),
    )


def _rigor_field(node_id: str) -> str:
    """The on-disk field name for a node's local rigor, from the id's kind.

    ``rigor`` is one concept with two spellings on disk, and **the op picks** —
    the scoring seat is never asked to choose between ``confidence:`` and
    ``quality:`` itself. A caller that never names the field
    can never name the wrong one.
    """
    return "quality" if node_id.startswith("sup-") else "confidence"


def _work_strength_field(node_id: str) -> str:
    """The on-disk field name for an external work's standing.

    A separate function rather than a third branch of :func:`_rigor_field`,
    because it is not the same concept: local rigor grades how well a
    derivation in **this** corpus establishes its own result, and a work's
    standing is a judgement about a paper nobody here wrote. Two questions, two
    field names, and one op each — so a caller cannot ask one and answer the
    other.
    """
    _ = node_id
    return "strength"


# ---------------------------------------------------------------------------
# The leaf readback, inside the splice
# ---------------------------------------------------------------------------

#: The proof one leaf-writing op supplies: parse the candidate at ``path`` and
#: return the names of the fields that came back other than intended.
Prover = Callable[[Path, Path], Sequence[str]]


def _proven(
    splice: Callable[[str], str], *, target: Path, kb_root: Path, provers: Sequence[tuple[Prover, str]]
) -> Callable[[str], str]:
    """Wrap a splice so its candidate is re-parsed before it can be written.

    The candidate goes to :func:`store.proof_temp` — the package's one proof
    seam — which writes it beside the target with the bytes a publish would
    write, hands back the path, and removes it on every path out. The wrap runs
    inside :func:`store.apply_edits`, over the baseline ``store`` itself read,
    which is what keeps the proof over the same bytes that would be replaced.

    The temp written here, with this module's own ``mkstemp``, must carry
    ``store.PROOF_MARK`` as well as ``TEMP_SUFFIX``: without the mark no sweep
    can tell one left by a killed writer from one a living writer is mid-proof
    on, and the litter is permanent.

    **Every prover runs against the finished candidate**, not against the
    intermediate one its own splice produced. A batch touching one document
    twice would otherwise prove each half against a document the other half had
    not landed in yet — which is the shape of proving something true of a file
    that never exists.
    """

    def wrapped(text: str) -> str:
        candidate = splice(text)
        with store.proof_temp(target, candidate) as temp:
            failures = [(subject, prove(temp, kb_root)) for prove, subject in provers]
        for subject, mismatched in failures:
            if mismatched:
                raise _ReadbackFailed(subject, mismatched)
        return candidate

    return wrapped


#: Every field of a rendered frontmatter record, and the values key a mismatch
#: in it is reported under. :func:`_frontmatter_prover` **loops over this map**
#: rather than comparing a hand-listed set of fields, so a field this map does
#: not name is a field nothing compares. ``test_kb_write_ops.py`` asserts the
#: map covers :class:`render.FrontmatterValues` whole — the same anti-rot move
#: ``store.COMPARED_FIELDS``' partition test makes on the register side, and for
#: the same reason: the uncompared set must not be able to grow in silence.
_FRONTMATTER_PROVEN_FIELDS: Mapping[str, str] = {
    "kind": "kind",
    "path_stable": "path-stable",
    "claims": "claims",
    "no_claim": "no-claim",
    "experiments": "experiments",
    "experiment_nodes": "experiment-node",
    "support_nodes": "support-node",
}

#: The reason a path value is not a compared field: it chose the file the
#: readback runs against rather than appearing in it, so the read itself is what
#: proves it and a comparison would be asking the document to restate its own
#: name.
_SELECTS_THE_FILE = "names the file the readback is run against, so the read proves it rather than a comparison"


@dataclass(frozen=True)
class ProofCoverage:
    """What one leaf-writing op's readback compares, over its values vocabulary.

    ``compared`` names the keys of that op's row in :data:`values.OP_FIELDS`
    whose value the op's prover reads back out of the candidate and matches.
    ``excluded`` names the rest, each against the reason its value is not a
    thing the production readers return.

    **The partition is the point, not the listing.** ``test_kb_write_ops.py``
    asserts the two halves cover the op's vocabulary exactly and overlap
    nowhere, so a key added to an op tomorrow lands in neither half and fails
    there — it cannot join the uncompared set without someone writing down why.
    A minted id is compared by every prover in the table (it is the lookup each
    one performs) and appears in neither half, because it is not a values key:
    mint fusion makes it unspellable in one.
    """

    compared: frozenset[str]
    excluded: Mapping[str, str]


#: The ops whose writes ``store``'s register-shaped proof cannot see, and what
#: each one's readback covers. Membership is asserted against behaviour rather
#: than trusted: the test plans every member of :data:`OPS` and requires the ops
#: that attach a :data:`Prover` to be exactly the ops named here.
LEAF_PROOF_COVERAGE: Mapping[str, ProofCoverage] = {
    "insert-experiment-entry": ProofCoverage(
        compared=frozenset({"status", "strengthens"}),
        excluded={"document": _SELECTS_THE_FILE},
    ),
    "set-frontmatter": ProofCoverage(
        # Derived from the map the prover itself walks, so this row cannot
        # describe a comparison the prover does not make.
        compared=frozenset(_FRONTMATTER_PROVEN_FIELDS.values()),
        excluded={"document": _SELECTS_THE_FILE},
    ),
    "mark-claim-in-leaf": ProofCoverage(
        compared=frozenset({"id"}),
        excluded={
            "document": _SELECTS_THE_FILE,
            "locator": "decides where the marker goes, and the reader harvests a marker's id wherever it "
            "sits — so the landing place is not a fact any production parser returns to compare against",
        },
    ),
    "set-on-point-fraction": ProofCoverage(
        compared=frozenset({"id", "claim", "fraction"}),
        excluded={},
    ),
}


# ---------------------------------------------------------------------------
# One entry's intent, and the fold into one Edit per file
# ---------------------------------------------------------------------------


#: An UPDATE op's expectation, as a function of the register the splice is about
#: to run on. Deferred rather than computed at plan time so that the bytes an
#: expectation describes and the bytes it will be proven against are one read
#: (see :func:`_current_entry`).
Expecter = Callable[[_RegisterRead], Sequence[store.ExpectedEntry]]


@dataclass(frozen=True)
class _Intent:
    """One values-file entry's worth of change to one file.

    Several intents may name one file — a scoring wave applying nine rigor
    values to one register is the ordinary case — and folding them into a single
    :class:`store.Edit` is what keeps that legal. Two edits naming one path both
    splice from the same baseline, so ``store`` refuses the pair rather than
    letting the second silently drop the first; the fold is how a batch stays a
    batch instead of becoming that refusal.

    **The two expectation fields are two different kinds of claim**, and keeping
    them apart is what decides whether the baseline gets parsed at all.
    ``expect`` is stated outright: an insert's record does not exist in the
    baseline, so there is nothing there to read it out of and nothing a
    concurrent writer could have changed about it. ``expect_current`` is derived
    from the baseline, because an update's claim is *the record as it stands,
    with one field changed*, and which bytes "as it stands" means is
    load-bearing. Collapsing the two would make every insert pay for a read of the
    register it is appending to.
    """

    path: str
    target: Path
    splice: Callable[[str], str]
    expect: tuple[store.ExpectedEntry, ...] = ()
    expect_current: Expecter | None = None
    prove: Prover | None = None
    subject: str = ""
    claim_delta: int = 0
    support_delta: int = 0
    work_delta: int = 0
    create: bool = False


def _chain(splices: Sequence[Callable[[str], str]]) -> Callable[[str], str]:
    """Apply each splice to the document the one before it returned."""

    def splice(document: str) -> str:
        for step in splices:
            document = step(document)
        return document

    return splice


def _expecting(
    splice: Callable[[str], str],
    *,
    into: list[store.ExpectedEntry],
    expecters: Sequence[Expecter],
    target: Path,
    kb_root: Path,
) -> Callable[[str], str]:
    """Wrap a splice so the batch's expectation is derived from what it receives.

    The document arriving here is ``store``'s baseline — the bytes the candidate
    will be spliced out of and the bytes the freshness check will compare the
    live file against — so parsing it here is what makes "the record as it
    stands" and "the record the proof runs on" one read. ``into`` is the list
    :class:`store.Edit` was handed: it is filled during the splice and read by
    ``store``'s proof afterwards, which is the whole reason an ``Edit``'s
    expectation is a sequence rather than a tuple fixed at construction.
    """

    def wrapped(document: str) -> str:
        read = _read_register(document, target=target, kb_root=kb_root)
        into.extend(expected for expect in expecters for expected in expect(read))
        return splice(document)

    return wrapped


def _batch(intents: Sequence[_Intent], *, kb_root: Path) -> list[store.Edit]:
    """Fold every intent into one edit per file, preserving entry order.

    Grouped by the **resolved** target rather than by the path string the values
    file spelled, because ``store`` claims a target by its resolved subject:
    ``part3/claim-quality.md`` and ``./part3/claim-quality.md`` in one values
    file are one file, and grouping by the spelling turned them into two edits
    and a ``duplicate-target`` refusal. The ``Edit`` keeps
    the first spelling — it is what the caller wrote, so it is what a report
    line about their values should say.
    """
    grouped: dict[Path, list[_Intent]] = {}
    for intent in intents:
        grouped.setdefault(intent.target, []).append(intent)

    edits: list[store.Edit] = []
    for target, group in grouped.items():
        splice = _chain([intent.splice for intent in group])
        # The stated half is known now; the derived half is appended while the
        # splice runs. `store`'s proof compares each expectation independently,
        # so the two halves need no interleaving — and no op mixes them anyway,
        # an op's entries being all inserts or all updates.
        expected: list[store.ExpectedEntry] = [item for intent in group for item in intent.expect]
        expecters = [intent.expect_current for intent in group if intent.expect_current is not None]
        if expecters:
            splice = _expecting(splice, into=expected, expecters=expecters, target=target, kb_root=kb_root)
        provers = [(intent.prove, intent.subject) for intent in group if intent.prove is not None]
        if provers:
            splice = _proven(splice, target=target, kb_root=kb_root, provers=provers)
        edits.append(
            store.Edit(
                path=group[0].path,
                splice=splice,
                expect=expected,
                claim_delta=sum(intent.claim_delta for intent in group),
                support_delta=sum(intent.support_delta for intent in group),
                work_delta=sum(intent.work_delta for intent in group),
                create=any(intent.create for intent in group),
            )
        )
    return edits


# ---------------------------------------------------------------------------
# Splices — composed here from store's locators and render's bytes
#
# `Edit.splice` is caller-supplied by design (store.py, `Edit`): where an entry
# or a bullet goes is a property of the document, not of the value. These
# compose that callable out of `store`'s location primitives and `render`'s
# bytes; no metadata format is spelled in this module.
#
# Every one of them CUTS with `store.splice_lines` rather than rebuilding the
# document as `"\n".join(document.splitlines())`. That join reads as a no-op and
# is not: `splitlines` breaks on \r, \x0c, U+2028 and U+0085 among others and the
# join emits \n for each, so a metadata edit rewrites the author's body prose —
# which is outside this API entirely.
# `splice_lines` takes the reader's own line indices and leaves every line the
# call does not name byte-identical, terminator included.
# ---------------------------------------------------------------------------


def _insert_all(entries: Sequence[str]) -> Callable[[str], str]:
    """Append every rendered entry to a register, in order."""

    def splice(document: str) -> str:
        for entry in entries:
            document = store.insert_entry(document, entry)
        return document

    return splice


def _replace_line(node_id: str, field_name: str, line: str) -> Callable[[str], str]:
    def splice(document: str) -> str:
        return store.replace_field_line(document, node_id=node_id, field_name=field_name, line=line)

    return splice


#: The ``### Quality`` fields ``add-depends-on`` appends bullets to, each mapped
#: to the renderer composing one of its bullets. Both sections anchor above
#: ``- solidity:`` when absent, which is the canonical field order
#: (``render.render_entry``); ``references`` leads ``depends-on`` in the search
#: for that anchor so a section added second still lands below one added first.
_BULLET_SECTIONS: Mapping[str, Callable[[render.DependsOnTarget], str]] = {
    "depends-on": render.render_depends_on_bullet,
    "references": render.render_references_bullet,
}

#: The lines a missing section is anchored above, in the order they are looked
#: for: the first one present wins. ``references`` sits between ``depends-on``
#: and ``solidity``, so a new ``depends-on`` section goes above an existing
#: ``references`` one and the reader's canonical order survives either order of
#: writing.
_SECTION_ANCHORS: Mapping[str, tuple[str, ...]] = {
    "depends-on": ("- references:", "- solidity:"),
    "references": ("- solidity:",),
}


def _add_bullets(addition: "_EdgeAddition", *, section: str) -> Callable[[str], str]:
    """Insert an entry's new bullets into one ``### Quality`` list, never rewriting it.

    The bullets are appended below the last existing one, so no line above the
    insertion point moves and every ``(solidity …)`` annotation ``refresh`` has
    already computed stays byte-identical. Rewriting the block whole would reset
    those derived values to their placeholder, which ``refresh`` then reports as
    drift and ``verify`` fails on.

    An entry with no such section gains one above the first line of
    :data:`_SECTION_ANCHORS` it carries — and the placement is not cosmetic:
    each folding field runs until the next line matching the reader's key list,
    so what follows the section is what bounds it.

    ``addition`` decides which of the requested edges are written; where that
    leaves none, the document comes back untouched rather than gaining an empty
    section.
    """
    node_id = addition.node_id
    break_re = store._fold_break_for(node_id)
    header = f"- {section}:"

    def splice(document: str) -> str:
        bullets = [_BULLET_SECTIONS[section](target) for target in addition.new]
        if not bullets:
            return document
        entry = next((e for e in store.locate_entries(document) if e.node_id == node_id), None)
        if entry is None:
            raise store.SpliceError(f"no canonical entry for {node_id} in this register")
        if entry.quality_start is None or entry.quality_end is None:
            raise store.SpliceError(f"{node_id} has no ### Quality section")
        lines = document.splitlines()
        limit = min(entry.quality_end, len(lines))

        def first_line_starting(prefix: str) -> int | None:
            return next((i for i in range(entry.quality_start, limit) if lines[i].strip().startswith(prefix)), None)

        head = first_line_starting(header)
        if head is None:
            anchor = next(
                (found for found in map(first_line_starting, _SECTION_ANCHORS[section]) if found is not None), None
            )
            if anchor is None:
                wanted = " or ".join(_SECTION_ANCHORS[section])
                raise store.SpliceError(f"{node_id} has no {wanted} line to anchor a {section} section above")
            insert_at, new = anchor, [header, *bullets]
        else:
            i = head + 1
            while i < limit and lines[i].strip() and not break_re.match(lines[i].strip()):
                i += 1
            insert_at, new = i, list(bullets)
        return store.splice_lines(document, start=insert_at, end=insert_at, lines=new)

    return splice


def _chained(*splices: Callable[[str], str]) -> Callable[[str], str]:
    """One splice running several in order, each over the last one's output.

    An ``_Intent`` carries one callable and ``add-depends-on`` may write two
    sections of one entry; running them in sequence keeps the intent one edit
    of one file, which is what the all-or-nothing batch guarantee rests on.
    """

    def splice(document: str) -> str:
        for one in splices:
            document = one(document)
        return document

    return splice


_KNOWN_FRONTMATTER_KEYS = frozenset(
    {
        "kind",
        "path-stable",
        "claims",
        "no-claim",
        "experiments",
        "exp-id",
        "status",
        "strengthens",
        "sup-id",
        "supports",
    }
)

#: Derived frontmatter fields — ``refresh``'s roll-ups. A block-replacing op
#: carries their current lines over verbatim rather than dropping them: deleting
#: a derived value is resetting one, which is forbidden as squarely as computing
#: one. The anchors are refresh's own documented insertion points.
_DERIVED_FRONTMATTER_FIELDS: tuple[tuple[str, str], ...] = (
    ("subtree-claims", "kind:"),
    ("subtree-experiments", "subtree-claims:"),
)


def _replace_block(block: str, *, rel: str, redeclared: Sequence[str]) -> Callable[[str], str]:
    """Replace a document's whole kb-frontmatter block, preserving derived fields.

    The current block is read **here**, out of the document the splice receives,
    rather than at plan time out of the live file: two reads describe different
    generations of the file whenever a writer lands between them, and
    what this one decides — which derived roll-ups to carry over, and whether
    the replace would drop a key or a hosted declaration — is a question about
    exactly the bytes being replaced.
    """

    def splice(document: str) -> str:
        existing = _existing_frontmatter(document, rel, redeclared=redeclared)
        if store.FRONTMATTER_BLOCK.search(document) is None:
            candidate = _insert_block(document, block)
        else:
            candidate = store.FRONTMATTER_BLOCK.sub(lambda _m: block, document, count=1)
        for name, anchor in _DERIVED_FRONTMATTER_FIELDS:
            carried = (existing or {}).get(name)
            # Presence, not truth: an empty roll-up and an absent key are
            # different facts — `subtree-claims: []` is a walk that found
            # nothing, no key at all is a walk that never ran — and the
            # verifier cannot tell them apart, so a falsy test drops the
            # empty one silently.
            if carried is not None:
                candidate = store.replace_or_insert_frontmatter_field(
                    candidate, field=name, ids=list(carried), anchor_prefix=anchor
                )
        return candidate

    return splice


def _insert_block(document: str, block: str) -> str:
    """Place a first frontmatter block: below the up-link line, else at the top.

    Every non-root KB document opens with an up-link to its parent, and the
    block sits under it — so the insertion point is read off the document rather
    than fixed, and a document with no up-link (an entry point) takes the block
    first.

    **A separating blank line is added only where there is not one already.**
    The op emitted one on each side unconditionally, so the ordinary leaf shape
    — an up-link, then a blank line — gained two blanks after the block (review
    finding m3). It is a body edit either way, by an op that owns only the
    metadata, so the rule is to disturb the prose by the minimum that keeps the
    block a block.
    """
    lines = document.splitlines()
    at = 0
    for i, line in enumerate(lines):
        if not line.strip():
            continue
        at = i + 1 if line.lstrip().startswith("[") else i
        break
    new = block.splitlines()
    if at and lines[at - 1].strip():
        new.insert(0, "")
    if at < len(lines) and lines[at].strip():
        new.append("")
    return store.splice_lines(document, start=at, end=at, lines=new)


def _append_to_block(added: Sequence[str]) -> Callable[[str], str]:
    """Append lines inside the existing kb-frontmatter block, disturbing nothing.

    Repeated ``exp-id:`` / ``sup-id:`` keys each open their own fan-out block —
    a container hosts any number of any combination of node bodies — so a new
    declaration appends rather than replacing anything. The delimiters come back
    from :func:`render.wrap_frontmatter`, the one composer of the opener.
    """

    def splice(document: str) -> str:
        if store.FRONTMATTER_BLOCK.search(document) is None:
            raise store.SpliceError("the document has no kb-frontmatter block to declare a node in")
        return store.FRONTMATTER_BLOCK.sub(
            lambda m: render.wrap_frontmatter("\n".join([*m.group(1).splitlines(), *added])), document, count=1
        )

    return splice


def _insert_marker(marker: str, locator: str, *, node_id: str, rel: str) -> Callable[[str], str]:
    """Append a claim's in-body marker to the end of the located line.

    **Inline, because a marker on a line of its own is a block-level element
    and splits whatever block it lands in.** Anchored mid-paragraph it turned
    one ``Para`` into ``Para, RawBlock, Para`` — the author's sentence broken in
    two in the document a person reads — and between a labelled block's label
    line and its statement it ended the blockquote and opened another. On the
    end of the located line it is a ``RawInline`` instead, and the block
    structure is untouched at any nesting depth: a marker appended to a
    ``>``-prefixed line is already inside that quote and needs no prefix of its
    own. The position is as precise either way — the exact located line, never a
    snapped block boundary.

    The line's own trailing whitespace stays where it is and the marker goes in
    front of it: two trailing spaces are Markdown's hard line break, and this API
    owns the metadata in the file, never how the author's prose renders.

    The locator is resolved **at splice time**, against the document the write
    path actually read, rather than at plan time against a line number. A line
    number is a fact about the generation of the file it was read from, and a
    marker bound to the wrong paragraph by a stale one is silent — which is this
    API's whole subject.

    The membership refusal is asked here for the same reason: a marker is
    dropped by the reader unless its id is in the document's own ``claims:``,
    and that list is a fact about the document being edited — read at plan time
    it is a fact about a generation of it that a concurrent ``set-frontmatter``
    may already have replaced.
    """

    def splice(document: str) -> str:
        fields = kb_index_lib.parse_frontmatter(document) or {}
        if node_id not in tuple(fields.get("claims", ()) or ()):
            raise _Refused(
                f"id={node_id}",
                f"is not in {rel}'s own claims: list, so the reader would harvest the marker and drop "
                f"it — markers are intersected with the document's claim membership",
                restore=f"restore: add {node_id} to {rel}'s claims: with set-frontmatter, then re-run",
            )
        at_line = _locate_excerpt(document, locator)
        line = document.splitlines()[at_line]
        content = line.rstrip(" \t")
        trailing = line[len(content) :]
        return store.splice_lines(document, start=at_line, end=at_line + 1, lines=[f"{content} {marker}{trailing}"])

    return splice


def _replace_supports_pair(sup_id: str, claim_id: str, line: str) -> Callable[[str], str]:
    """Rewrite one ``supports:`` pair line inside its own ``sup-id:`` block.

    The pair belongs to the block its ``sup-id:`` opened and to no other, so the
    scan is bounded by the next ``sup-id:`` — a container hosting two supports
    that both name one beneficiary is legal, and rewriting the wrong one would
    be a silent edit of a value nobody asked about.
    """
    pair_re = re.compile(rf"^(\s*-?\s*){re.escape(claim_id)}\s*:")

    def splice(document: str) -> str:
        match = store.FRONTMATTER_BLOCK.search(document)
        if match is None:
            raise store.SpliceError("the document has no kb-frontmatter block")
        block = match.group(1).splitlines()
        start = next((i for i, ln in enumerate(block) if ln.strip() == f"sup-id: {sup_id}"), None)
        if start is None:
            raise store.SpliceError(f"{sup_id} is not declared in this document's frontmatter")
        end = next((i for i in range(start + 1, len(block)) if block[i].strip().startswith("sup-id:")), len(block))
        target = next((i for i in range(start + 1, end) if pair_re.match(block[i])), None)
        if target is None:
            raise store.SpliceError(f"{sup_id} declares no supports pair for {claim_id}")
        # The rendered line carries the canonical indent, so it replaces the
        # physical line whole rather than being grafted onto its leader.
        block[target] = line
        return store.FRONTMATTER_BLOCK.sub(lambda _m: render.wrap_frontmatter("\n".join(block)), document, count=1)

    return splice


def _replace_staged_pair(sup_id: str, claim_id: str, line: str) -> Callable[[str], str]:
    """Rewrite one STAGED ``supports:`` pair line, inside a register entry.

    The fan-out's pre-leaf home: a ``sup-`` id is minted a stage before the
    document that will host it exists, so the pairs are staged in the register
    entry until then and ``kb_index_lib`` reads them there. The scan is bounded
    by that entry's own ``### Quality`` span, so a second entry staging the same
    beneficiary is untouched, and it starts at the entry's ``- supports:``
    bullet, so a pair-shaped line elsewhere in the block is not what gets
    rewritten.

    **Where the block ends is decided by the reader's own pair pattern**, not by
    a second one spelled here — the fifth in-package reach, and the same one
    ``store`` makes for the census markers. The reader matches a pair before it
    tests whether a bullet closes the block; a locator that tested the bullet
    first would stop at the fan-out's *second* beneficiary and report the first
    as the only pair there is.
    """

    def splice(document: str) -> str:
        entry = next((e for e in store.locate_entries(document) if e.node_id == sup_id), None)
        if entry is None:
            raise store.SpliceError(f"no canonical entry for {sup_id} in this register")
        if entry.quality_start is None or entry.quality_end is None:
            raise store.SpliceError(f"{sup_id} has no ### Quality section")
        lines = document.splitlines()
        limit = min(entry.quality_end, len(lines))

        head = next((i for i in range(entry.quality_start, limit) if lines[i].strip() == "- supports:"), None)
        if head is None:
            raise store.SpliceError(f"{sup_id} stages no supports block in this register")
        for i in range(head + 1, limit):
            pair = kb_index_lib._SUPPORTS_PAIR_RE.match(lines[i])
            if pair is not None:
                if pair.group(1) == claim_id:
                    # The rendered line carries the canonical indent, so it
                    # replaces the physical line whole.
                    return store.splice_lines(document, start=i, end=i + 1, lines=[line])
                continue
            if lines[i].strip().startswith("- "):
                break
        raise store.SpliceError(f"{sup_id} stages no supports pair for {claim_id}")

    return splice


# ---------------------------------------------------------------------------
# Locating a body excerpt (mark-claim-in-leaf)
# ---------------------------------------------------------------------------


#: A line's leading blockquote markers — one or more of up-to-3-space indent,
#: ``>``, and an optional following space, repeated for each nesting level
#: (``> > `` and the space-free ``>>`` both match). Read search-side only, by
#: :func:`resolve_excerpt`, so a quoted excerpt matches on the same terms as a
#: plain one; nothing here rewrites a document with it.
_BLOCKQUOTE_PREFIX_RE = re.compile(r"^(?:[ \t]{0,3}>[ \t]?)+")

#: Tier 1: the strict search — blockquote-stripped, whitespace-collapsed, and
#: nothing else. What resolves here resolves on the author's own bytes.
STRICT_TIER = 1

#: Tier 2: the folded search, run **only** where tier 1 returned nothing. Exact
#: always wins when it is available, so folding can add resolutions strict would
#: have missed and can take none away — which is what makes any ambiguity a
#: folded search introduces attributable to the folding.
FOLDED_TIER = 2

#: Markup that wraps the author's words rather than being them: an HTML comment,
#: and any HTML tag. A citation reaches a KB document inside
#: ``<span class="citation" data-cites="…">`` and a cross-reference inside an
#: ``<a href=… data-reference=…>``, and a seat quoting such a sentence writes
#: neither. The tag form requires a name character immediately after the ``<``,
#: so an inequality written ``$a < b$`` is not read as an unclosed tag.
_MARKUP_RE = re.compile(r"<!--.*?-->|</?[A-Za-z][^<>]*>")

#: A Markdown link: the author's words sit in the brackets and the target does
#: not, so the target is dropped rather than folded into words a quotation could
#: never contain.
_LINK_RE = re.compile(r"\[([^\[\]]*)\]\([^()]*\)")

#: Two or more spaces. In a joined body this occurs only where a blank line
#: contributed an empty part, so it is how a paragraph break is recognised after
#: the body has become one string.
_SPACE_RUN_RE = re.compile(r"  +")


@dataclass(frozen=True)
class Hit:
    """One place an excerpt resolves to.

    ``line`` is a 0-based index into ``document.splitlines()`` — a real physical
    line, the one a marker would be appended to. ``offset`` is a character
    offset into ``document`` itself, at the first character of the match.

    **``offset`` is in the document's own coordinates, and that is what makes it
    usable.** Its one consumer is the label cross-check, which asks which
    sentence of :func:`kb_tools.kb_claimgraph.label.render`'s render covers the
    match — and that render carries each sentence's extent as offsets into the
    text it was rendered from. A caller taking both answers must therefore hand
    this function and the render **the same string**; a resolution run over a
    document whose markers a render never saw reports offsets into a different
    document.
    """

    line: int
    offset: int


@dataclass(frozen=True)
class Resolution:
    """What an excerpt resolved to, and which tier resolved it.

    ``tier`` is the tier the search ended at: :data:`STRICT_TIER` where the
    strict search returned anything at all, :data:`FOLDED_TIER` otherwise —
    including where the folded search also returned nothing, there being no
    third thing to try.
    """

    hits: tuple[Hit, ...]
    tier: int


@dataclass(frozen=True)
class NearMiss:
    """The window of a document's body a failed resolution came closest to.

    ``text`` is that window as the strict search read it — collapsed, unquoted —
    and ``line`` and ``offset`` place it, so a caller can say *the document
    reads X, at S12* rather than only *not found*.
    """

    text: str
    line: int
    offset: int


@dataclass(frozen=True)
class _Body:
    """The document body as one searchable string, with every line's place kept.

    ``text`` is what both tiers search: each body line's blockquote markers
    stripped and its whitespace collapsed, the lines joined with single spaces.
    Stripping and collapsing are search-side only — ``lines`` holds the body's
    physical lines verbatim and ``sources`` their offsets in the document, so
    every answer this class returns names a place in the document as it stands.
    """

    text: str
    #: Where each body line begins in :attr:`text`. Strictly ascending, because
    #: an empty part still costs its joining space.
    starts: tuple[int, ...]
    lines: tuple[str, ...]
    #: Each body line's character offset in the document.
    sources: tuple[int, ...]
    #: The document line index of ``lines[0]`` — the first line beneath the
    #: frontmatter block.
    first: int

    def hit_at(self, offset: int) -> Hit:
        """The :class:`Hit` for a character offset into :attr:`text`."""
        index = bisect.bisect_right(self.starts, offset) - 1
        columns = _columns(self.lines[index])
        within = offset - self.starts[index]
        column = columns[within] if within < len(columns) else len(self.lines[index])
        return Hit(line=self.first + index, offset=self.sources[index] + column)


def _columns(line: str) -> tuple[int, ...]:
    """Each character of ``line``'s searchable form, as a column in ``line``.

    The searchable form is ``collapse_prose`` over the unquoted line, which is
    exactly its ``\\S+`` runs rejoined with single spaces — so walking those runs
    reproduces that string and says where each of its characters came from. A
    joining space is attributed to the run it precedes, the only offset in the
    pair that names a character the author wrote.
    """
    prefix = _BLOCKQUOTE_PREFIX_RE.match(line)
    cut = prefix.end() if prefix else 0
    columns: list[int] = []
    for match in re.finditer(r"\S+", line[cut:]):
        if columns:
            columns.append(cut + match.start())
        columns.extend(range(cut + match.start(), cut + match.end()))
    return tuple(columns)


def _body(document: str) -> _Body:
    """``document``'s body, prepared for both tiers.

    The search runs over the body **only**, since a marker belongs beside the
    text it anchors and never inside the frontmatter block.
    """
    block = store.FRONTMATTER_BLOCK.search(document)
    first = document.count("\n", 0, block.end()) + 1 if block else 0
    lines = document.splitlines()

    parts: list[str] = []
    starts: list[int] = []
    sources: list[int] = []
    cursor = 0
    source = sum(len(line) + 1 for line in lines[:first])
    for line in lines[first:]:
        collapsed = render.collapse_prose(_BLOCKQUOTE_PREFIX_RE.sub("", line))
        starts.append(cursor)
        sources.append(source)
        parts.append(collapsed)
        cursor += len(collapsed) + 1
        source += len(line) + 1
    return _Body(
        text=" ".join(parts),
        starts=tuple(starts),
        lines=tuple(lines[first:]),
        sources=tuple(sources),
        first=first,
    )


def _flagged(pattern: re.Pattern[str], text: str) -> list[bool]:
    """One flag per character of ``text``, set across every match of ``pattern``."""
    flags = [False] * len(text)
    for match in pattern.finditer(text):
        flags[match.start() : match.end()] = [True] * (match.end() - match.start())
    return flags


def _dropped(text: str) -> list[bool]:
    """Which characters of ``text`` are markup rather than words the author wrote.

    Dropping is **transparent**: a tag wrapping a word leaves that word joined to
    its neighbours exactly as it was written, because the markup occupies no
    space in the sentence a reader sees and none in the sentence a seat quotes.
    """
    flags = _flagged(_MARKUP_RE, text)
    for match in _LINK_RE.finditer(text):
        flags[match.start()] = True
        flags[match.end(1) : match.end()] = [True] * (match.end() - match.end(1))
    return flags


def _fold(text: str) -> tuple[str, tuple[int, ...]]:
    """``text`` in tier 2's form, and each folded character's index in ``text``.

    Markup goes first and structurally; what remains is lowercased where it is
    alphanumeric — digits kept, an equation's ``2`` being as much of the
    sentence as its words — and becomes a space where it is not, with runs
    collapsed. Case, typography and punctuation stop separating a quotation from
    the sentence it was taken from, which is what tier 2 is for.

    **A paragraph break survives the fold, and it has to.** ``label.py``
    guarantees that no span crosses one by relying on this body's own shape: a
    blank line contributes an empty part and so puts two spaces into the joined
    text, where a needle collapsed to single spaces cannot reach across. Folding
    a two-space run down to one would retire that guarantee silently, so a run
    of two or more spaces folds to two and every other run folds to one.
    """
    dropped = _dropped(text)
    barrier = _flagged(_SPACE_RUN_RE, text)
    out: list[str] = []
    index: list[int] = []
    pending = 0
    for at, char in enumerate(text):
        if dropped[at]:
            continue
        if not char.isalnum():
            pending = max(pending, 2 if barrier[at] else 1)
            continue
        if pending and out:
            out.extend(" " * pending)
            index.extend([at] * pending)
        pending = 0
        lowered = char.lower()
        out.extend(lowered)
        index.extend([at] * len(lowered))
    return "".join(out), tuple(index)


def canonical_form(text: str) -> str:
    """``text`` reduced to the form tier 2 searches — the same fold, not a copy of it.

    :func:`_fold` is the implementation and this is its text half; the index half
    is what carries a folded hit back into the document's coordinates and is of
    no use to a caller that is canonicalising rather than searching. So a caller
    asking *what does this sentence come down to* gets the answer the matcher
    would give, by running the matcher's own code — a second reduction that
    disagreed with this one would classify a sentence as unquotable while tier 2
    went on resolving quotations to it.

    What comes back is markup-free, lowercased, and whitespace-separated: its
    whitespace-separated runs are the words the sentence canonically has, and an
    empty return says the line is markup, punctuation or maths and has none. A
    caller counting them writes ``len(canonical_form(text).split())``.

    **Search-side only, as everything in this section is.** No byte of this
    reaches a document, a register or a record; it exists to decide questions
    about text that stays exactly as its author wrote it. And a run of two or
    more spaces survives it — see :func:`_fold`, where that is the paragraph
    break's guarantee — so ``split`` rather than a count of separators is what
    reads the result.
    """
    folded, _ = _fold(text)
    return folded


def _found(haystack: str, needle: str) -> list[int]:
    """Every offset ``needle`` begins at in ``haystack``, first to last."""
    hits: list[int] = []
    at = haystack.find(needle)
    while at != -1:
        hits.append(at)
        at = haystack.find(needle, at + 1)
    return hits


def resolve_excerpt(document: str, excerpt: str) -> Resolution:
    """Every place ``excerpt`` resolves to in ``document``'s body, and how.

    **One entry point, no caller-selectable mode.** Escalation is internal and
    deterministic for every caller: tier 1 is the strict search — blockquote
    markers stripped, whitespace collapsed — and tier 2, the folded search, runs
    only where tier 1 returned nothing. A caller offered the choice would be
    offered the failure :func:`excerpt_lines` exists to close, a pre-check that
    disagrees with the op it pre-checks.

    Stripping and folding are search-side only. The line count driving
    :attr:`Hit.line` is taken from :func:`str.splitlines` before any of it, so
    every index still names a real physical line, and :attr:`Hit.offset` still
    names a character of the document as it stands.

    The consequence of tier 2 is intended and is stated here rather than
    discovered: ``mark-claim-in-leaf`` now binds an excerpt that differs from
    the document only by markup, typography, case or punctuation. It asks
    whether a marker is findable and unique, and folded-findable is findable.
    """
    body = _body(document)
    if not body.starts:
        # Nothing beneath the frontmatter block: no line for a hit to name, and
        # no second tier that could find one.
        return Resolution(hits=(), tier=FOLDED_TIER)

    needle = render.collapse_prose(excerpt)
    strict = _found(body.text, needle)
    if strict:
        return Resolution(hits=tuple(body.hit_at(at) for at in strict), tier=STRICT_TIER)

    folded, index = _fold(body.text)
    wanted, _ = _fold(needle)
    if not wanted:
        # An excerpt of nothing but markup and punctuation names every gap in
        # the body; it names no sentence.
        return Resolution(hits=(), tier=FOLDED_TIER)
    # Back into the strict body's coordinates before anything is reported: a
    # folded offset is an index into a string no consumer of this module has.
    hits = dict.fromkeys(body.hit_at(index[at]) for at in _found(folded, wanted))
    return Resolution(hits=tuple(hits), tier=FOLDED_TIER)


def excerpt_lines(document: str, excerpt: str) -> tuple[int, ...]:
    """Every 0-based body line ``excerpt`` begins on, in first-to-last order.

    **The matching computation this API locates a marker by, exported so that a
    caller may run it before spending a write.** A caller composing a locator
    has the same two questions the op has — does this text appear, and does it
    appear once — and answering them with a second implementation is the
    "checker that re-derives what the emitter derived" failure
    ``verify_kb_metadata.check_subtree_consistency`` names in its own docstring:
    a pre-check that disagrees with the op it pre-checks is worse than no
    pre-check. So the pre-check *is* this function, and
    :func:`_locate_excerpt` — the op's own resolver — is the refusal policy laid
    over it and nothing more.

    :func:`resolve_excerpt` is that computation; this is its hits with the tier
    dropped, **tier-2 hits included**. Which tier answered is not a fact about
    where a marker goes, and a caller that needs it asks the resolver.
    """
    return tuple(hit.line for hit in resolve_excerpt(document, excerpt).hits)


def nearest_excerpt(document: str, excerpt: str) -> NearMiss | None:
    """The window of ``document``'s body a failed resolution came closest to.

    The diagnostic half of :func:`resolve_excerpt`, carrying its position: a
    re-ask that can show the seat the sentence the search came nearest to, and
    say where it sits, is a re-ask carrying a fact the first ask did not have.
    Returns ``None`` where no window is close enough to be a quotation of
    anything, which is a truthful "nothing comparable is there" rather than a
    misleading near miss.
    """
    body = _body(document)
    if not body.starts:
        return None
    window = _nearest_window(render.collapse_prose(excerpt), body.text)
    if window is None:
        return None
    text, at = window
    hit = body.hit_at(at)
    return NearMiss(text=text, line=hit.line, offset=hit.offset)


def _locate_excerpt(document: str, locator: str) -> int:
    """The one line ``locator`` names, or the refusal saying which way it failed.

    Absent and ambiguous are two different refusals: a marker placed at the
    wrong one of two matches is a silent mis-binding, which is the class this
    whole API exists to close.

    Called from inside a splice, so its :class:`_Refused` leaves
    :func:`store.apply_edits` before any temp is proven and any file replaced —
    the same route the leaf readback's failure takes.
    """
    hits = excerpt_lines(document, locator)
    if not hits:
        raise _Refused(
            "locator",
            f"{locator!r} does not appear in the document body. The locator is matched against the "
            f"body's whitespace-collapsed text, and where that finds nothing against a folded form of "
            f"it — so wrapping, markup, case and punctuation do not matter, but the words themselves "
            f"do, and so does the paragraph they sit in",
            restore="restore: quote the locator text verbatim from one paragraph of the document body " "and re-run",
        )
    if len(hits) > 1:
        raise _Refused(
            "locator",
            f"{locator!r} matches {len(hits)} places in the document body, so the marker's position "
            f"would be chosen arbitrarily",
            restore="restore: extend the locator until it names exactly one place, then re-run",
        )
    return hits[0]


# ---------------------------------------------------------------------------
# The ops
#
# `render-citation` is not among them: it is read-only, so it is not a member
# of this write path's ladder and not a member of `OPS`. It lives further down,
# in its own section, keyed by the sibling `READ_OPS`.
# ---------------------------------------------------------------------------


def insert_claim_entry(*, kb_root: Path, values_file: Path, create: bool = False) -> Result:
    """Mint a ``clm-`` id and write its canonical register entry in one act.

    Takes values — register, title, rigor, rationale, dependencies, open work
    items, an optional foreign-domain exemption — and returns the id it minted.
    It accepts no id, so there is no path by which it could write an entry for
    one the tool did not draw.
    """
    return _execute(
        "insert-claim-entry",
        kb_root=kb_root,
        values_file=values_file,
        plan=lambda ctx, entries: _plan_inserts(ctx, entries, kind="clm", create=create),
    )


def insert_support_entry(*, kb_root: Path, values_file: Path, create: bool = False) -> Result:
    """Mint a ``sup-`` id and write its canonical register entry.

    Identical to the claim insert but for the on-disk rigor field, the absence
    of ``strengthen-by`` (a claim's open-work list), and the optional
    ``supports`` value.

    ``supports`` stages the beneficiary fan-out **in the register entry**. The
    canonical home is the hosting document's ``sup-id:`` block, but a ``sup-``
    id is minted a full stage before that document exists, and the
    warrant-attribution postcondition — which reads both homes through
    ``scan_authored_support_edges`` — is checked at the end of that earlier
    stage. Without this value the surface could mint a support it had no way to
    attach to anything, and the stage that mints it could not pass.
    """
    return _execute(
        "insert-support-entry",
        kb_root=kb_root,
        values_file=values_file,
        plan=lambda ctx, entries: _plan_inserts(ctx, entries, kind="sup", create=create),
    )


def insert_experiment_entry(*, kb_root: Path, values_file: Path) -> Result:
    """Mint an ``exp-`` id and write its canonical declaration.

    An experiment has no register entry: its canonical declaration **is** the
    ``exp-id:`` / ``status:`` / ``strengthens:`` block in its hosting leaf's
    frontmatter, so that block is what this op writes. Creation is never
    implicit here in a second sense too — the hosting document must already
    exist and already carry a frontmatter block, because its body is authored
    prose this API does not compose.

    This op is why ``set-frontmatter`` may keep refusing an unresolvable
    ``exp-id``: a legitimately fresh experiment arrives through here first, so
    that refusal is correct behaviour rather than a gap in the surface.
    """
    return _execute("insert-experiment-entry", kb_root=kb_root, values_file=values_file, plan=_plan_experiments)


def insert_work_entry(*, kb_root: Path, values_file: Path, create: bool = False) -> Result:
    """Write an external work's register entry — the off-graph endcap's node.

    **It mints nothing, and that is the difference from every other insert.** A
    work's identity is the citation key the corpus already carries, so the id is
    *derived* from the supplied ``key`` (:func:`kb_schema.work_id`) rather than
    drawn. Mint fusion's guarantee — no id without its entry, no entry for an id
    the tool did not draw — is unnecessary here rather than weakened: the id is a
    function of a value, so the two cannot come apart, and a key already keyed by
    an entry is refused as a re-insert rather than silently doubled. That refusal
    is what makes a work cited by three volumes one node: the second and third
    calls name the same key and land on the same entry.

    ``strength`` is the work's standing and is normally the pending literal — a
    build writes no number here, and the value is a person's on a later pass
    through ``set-work-strength``.
    """
    return _execute(
        "insert-work-entry",
        kb_root=kb_root,
        values_file=values_file,
        plan=lambda ctx, entries: _plan_work_inserts(ctx, entries, create=create),
    )


def set_work_strength(*, kb_root: Path, values_file: Path) -> Result:
    """Rewrite one external work's ``- strength:`` line, and nothing else.

    The counterpart of ``set-rigor`` for the one node kind that has no local
    rigor: a work's standing is a judgement about a paper this corpus does not
    contain, so it is a different question with a field and an op of its own.
    Nothing derives the value and nothing may be pointed at filling it.
    """
    return _execute("set-work-strength", kb_root=kb_root, values_file=values_file, plan=_plan_set_work_strength)


def set_applicability(*, kb_root: Path, values_file: Path) -> Result:
    """Rewrite one claim→work pairing's applicability, on the bullet carrying it.

    The endcap's edge-level score, and the counterpart of
    ``set-on-point-fraction`` for a ``rests-on`` edge. It has one authored home —
    the depends-on bullet in the claim's own register entry — so exactly one
    annotation moves and no second end has to agree with it. A pairing that has
    no bullet is refused: this op re-scores an edge, it does not create one.
    """
    return _execute("set-applicability", kb_root=kb_root, values_file=values_file, plan=_plan_set_applicability)


def set_rigor(*, kb_root: Path, values_file: Path) -> Result:
    """Rewrite one entry's authored local rigor, and no derived line.

    The on-disk field name — ``confidence:`` for a claim, ``quality:`` for a
    support — is read off the id's kind by the op. Only that one physical line
    is rewritten, so a solidity ``refresh`` has computed stays byte-identical.
    """
    return _execute("set-rigor", kb_root=kb_root, values_file=values_file, plan=_plan_set_rigor)


def set_rationale(*, kb_root: Path, values_file: Path) -> Result:
    """Rewrite one entry's ``- rationale:`` block, whole and collapsed.

    The span replaced is the reader's own: the fold runs until a blank line or a
    line beginning with another key, so a hand-wrapped rationale is removed
    entire rather than leaving continuation lines for the next parse to absorb
    into the new value. A value carrying a blank line never reaches here — it is
    refused at step 1, because the field is single-paragraph by grammar.

    **It serves all three register entry kinds**, an external work included: a
    rationale is one field with one grammar wherever it is written, the fold
    break list is read off the id's kind, and the readback proves the whole
    record either way, so a work's rationale is editable on the same terms its
    standing already is.
    """
    return _execute("set-rationale", kb_root=kb_root, values_file=values_file, plan=_plan_set_rationale)


def add_depends_on(*, kb_root: Path, values_file: Path) -> Result:
    """Add outgoing-edge bullets to one entry, leaving each list otherwise untouched.

    **Two lists, one op**, because what a caller is adding is the entry's
    outgoing edges and the class is a property of the edge rather than of the
    call: ``depends-on`` names what the entry rests on and ``references`` what
    its own text names without resting on it. Either may stand alone; neither
    supplied is refused, as is a references list on an entry that is not a claim.

    An unresolvable target is refused rather than written and silently
    dropped by the reader — and with it every bullet after it. The separator is
    a real em-dash: a hyphen there does not cut the bullet head, so the head
    runs on over the title and every id-shaped token in it becomes a phantom
    edge. Neither byte is the caller's to get right any more.

    **Adding an edge the entry already holds is a no-op, not a second edge and
    not a refusal** (:class:`_EdgeAddition`). A dependency pass over a corpus
    runs for hours, so an interrupted run re-run over the same claims is the
    ordinary case rather than the exceptional one, and refusing it is what would
    make such a run unresumable. The count of edges already present comes back
    as a ``FACT`` line per entry: re-proposing an edge is fine, and a caller
    that believed it wrote N and wrote fewer can see so.
    """
    return _execute("add-depends-on", kb_root=kb_root, values_file=values_file, plan=_plan_add_depends_on)


def set_frontmatter(*, kb_root: Path, values_file: Path) -> Result:
    """Replace or insert a document's whole kb-frontmatter block.

    The tool stamps the metadata block into a file the agent wrote: the body
    prose stays authored, the block is composed here. Derived roll-ups present
    in the current block are carried over verbatim, and a block carrying a key
    outside the closed vocabulary is refused rather than rewritten without it —
    dropping a key nobody can name is the silent-loss class arriving through the
    door built to close it.
    """
    return _execute("set-frontmatter", kb_root=kb_root, values_file=values_file, plan=_plan_set_frontmatter)


def mark_claim_in_leaf(*, kb_root: Path, values_file: Path) -> Result:
    """Place a claim's in-body marker at a located excerpt.

    Placement arrives as a *value* — the locator — rather than as typed bytes,
    which is what keeps the marker's position out of inference's hands. An id
    absent from the document's own ``claims:`` is refused: today the reader
    intersects markers with that list and drops the rest without a word.
    """
    return _execute("mark-claim-in-leaf", kb_root=kb_root, values_file=values_file, plan=_plan_mark_claim)


def set_on_point_fraction(*, kb_root: Path, values_file: Path) -> Result:
    """Rewrite one support→claim on-point fraction, in the home it is authored in.

    The canonical home is the hosting document's frontmatter and it wins wherever
    it exists; a support whose document is not written yet has its pairs staged
    in its register entry, and that copy is what is rewritten instead. Exactly
    one pair line moves either way. A pair authored in neither home is refused:
    this op re-scores an edge, it does not create one.
    """
    return _execute("set-on-point-fraction", kb_root=kb_root, values_file=values_file, plan=_plan_set_on_point_fraction)


# ---------------------------------------------------------------------------
# Planners — one per op, composing the Edit batch and the expected records
# ---------------------------------------------------------------------------


def _plan_inserts(ctx: _Context, entries: Sequence[values.Entry], *, kind: str, create: bool) -> list[_Intent]:
    """Mint, render and expect one register entry per values-file entry."""
    intents: list[_Intent] = []
    for entry in entries:
        supplied = entry.values
        rel = str(supplied["register"])
        target = _contained(ctx.kb_root, rel, key="register")
        depends = _depends_targets(ctx, supplied.get("depends-on", ()))  # type: ignore[arg-type]
        strengthen_by = tuple(supplied.get("strengthen-by", ()))  # type: ignore[arg-type]
        # A staged beneficiary is an id-valued field like any other, so it
        # resolves against the authored inventory before anything is composed
        # The beneficiary is a claim and needs its register entry: a
        # fan-out into an id with no entry lifts a node no consumer can read.
        supports = tuple(supplied.get("supports", ()))  # type: ignore[arg-type]
        for pair in supports:
            _resolve(ctx, pair.id, key="supports.id", needs_register=True)
        staged = tuple((pair.id, pair.fraction) for pair in supports)
        node_id = _mint(ctx, kind)
        common = {
            "node_id": node_id,
            "title": str(supplied["title"]),
            "rationale": str(supplied["rationale"]),
            "depends_on": depends,
            "no_edge": supplied.get("no-edge"),  # type: ignore[arg-type]
        }
        if kind == "clm":
            rendered = render.render_claim_entry(
                confidence=supplied["rigor"], strengthen_by=strengthen_by, **common  # type: ignore[arg-type]
            )
        else:
            rendered = render.render_support_entry(
                quality=supplied["rigor"], supports=staged, **common  # type: ignore[arg-type]
            )
        intents.append(
            _Intent(
                path=rel,
                target=target,
                splice=_insert_all([rendered]),
                expect=(
                    store.ExpectedEntry(
                        node_id=node_id,
                        title=str(supplied["title"]),
                        rigor=supplied["rigor"],  # type: ignore[arg-type]
                        rationale=str(supplied["rationale"]),
                        depends_on=tuple(store.ExpectedEdge(t.target, t.context, t.applicability) for t in depends),
                        strengthen_by=strengthen_by if kind == "clm" else (),
                        supports=staged,
                    ),
                ),
                claim_delta=1 if kind == "clm" else 0,
                support_delta=1 if kind == "sup" else 0,
                create=create,
            )
        )
    return intents


def _plan_experiments(ctx: _Context, entries: Sequence[values.Entry]) -> list[_Intent]:
    intents: list[_Intent] = []
    for entry in entries:
        supplied = entry.values
        rel = str(supplied["document"])
        target = _contained(ctx.kb_root, rel, key="document")
        pairs = tuple(supplied.get("strengthens", ()))  # type: ignore[arg-type]
        for pair in pairs:
            _resolve(ctx, pair.id, key="strengthens.id", needs_register=True)
        exp_id = _mint(ctx, "exp")
        decl = render.ExperimentDecl(
            exp_id=exp_id,
            status=str(supplied["status"]),
            strengthens=tuple((pair.id, pair.strength) for pair in pairs),
        )
        # The declaration's lines are lifted out of a whole rendered block
        # rather than composed here: `render` is the only module that spells a
        # frontmatter field, and it has no renderer for a bare declaration.
        block = render.render_frontmatter_block(render.FrontmatterValues(kind="leaf", experiment_nodes=(decl,)))
        added = [line for line in block.splitlines()[1:-1] if not line.startswith("kind:")]
        intents.append(
            _Intent(
                path=rel,
                target=target,
                splice=_append_to_block(added),
                prove=_experiment_prover(decl),
                subject=f"{rel}:{exp_id}",
            )
        )
    return intents


def _experiment_prover(decl: render.ExperimentDecl) -> Prover:
    def prove(path: Path, kb_root: Path) -> list[str]:
        node = next((n for n in kb_index_lib.parse_experiment_leaf(path, kb_root) if n.id == decl.exp_id), None)
        if node is None:
            return ["exp-id"]
        wrong = [] if node.status == decl.status else ["status"]
        if tuple(node.strengthens) != decl.strengthens:
            wrong.append("strengthens")
        return wrong

    return prove


def _plan_work_inserts(ctx: _Context, entries: Sequence[values.Entry], *, create: bool) -> list[_Intent]:
    """Derive each work's id from its key, render its entry, and expect it.

    **One entry per key, corpus-wide.** A key already carried by a register
    entry is refused rather than inserted a second time, and so is a key
    repeated inside one batch: the id is a function of the key, so a second
    entry would be a second node for one work, which is precisely what tying the
    node to the bibliography rather than to a per-volume references leaf exists
    to prevent.
    """
    intents: list[_Intent] = []
    created: set[str] = set()
    for entry in entries:
        supplied = entry.values
        rel = str(supplied["register"])
        target = _contained(ctx.kb_root, rel, key="register")
        key = str(supplied["key"])
        node_id = kb_schema.work_id(key)
        held = ctx.inventory.get(node_id)
        if held is not None or node_id in created:
            where = "this batch" if node_id in created else f"{held.register_path}" if held else "the KB"
            raise _Refused(
                f"key={key}",
                f"already has an external-work entry ({node_id} in {where}). A work's id is derived from "
                f"its citation key, so a second entry would be a second node for one work — the split "
                f"this node kind exists to prevent",
                restore=f"restore: drop this entry, or edit {node_id}'s standing with set-work-strength "
                f"and its rationale with set-rationale, then re-run",
            )
        created.add(node_id)
        intents.append(
            _Intent(
                path=rel,
                target=target,
                splice=_insert_all(
                    [
                        render.render_work_entry(
                            node_id=node_id,
                            title=str(supplied["title"]),
                            strength=supplied["strength"],  # type: ignore[arg-type]
                            rationale=str(supplied["rationale"]),
                        )
                    ]
                ),
                expect=(
                    store.ExpectedEntry(
                        node_id=node_id,
                        title=str(supplied["title"]),
                        rigor=supplied["strength"],  # type: ignore[arg-type]
                        rationale=str(supplied["rationale"]),
                    ),
                ),
                work_delta=1,
                create=create,
            )
        )
    return intents


def _plan_set_work_strength(ctx: _Context, entries: Sequence[values.Entry]) -> list[_Intent]:
    return _plan_field_update(ctx, entries, key="strength", field_of=_work_strength_field, apply=_apply_rigor)


def _plan_set_applicability(ctx: _Context, entries: Sequence[values.Entry]) -> list[_Intent]:
    """Rewrite one ``(applicability …)`` annotation on one claim's work bullet."""
    intents: list[_Intent] = []
    for entry in entries:
        node_id = str(entry.values["id"])
        work_id = str(entry.values["work"])
        fraction = entry.values["applicability"]
        record = _resolve(ctx, node_id, key="id", needs_register=True)
        _resolve(ctx, work_id, key="work", needs_register=True)
        rel = str(record.register_path)
        register = _contained(ctx.kb_root, rel, key="id")
        intents.append(
            _Intent(
                path=rel,
                target=register,
                splice=_replace_applicability(node_id, work_id, fraction),  # type: ignore[arg-type]
                expect_current=lambda read, node_id=node_id, work_id=work_id, fraction=fraction: (
                    _applied_applicability(_current_entry(read, node_id), work_id, fraction, node_id=node_id),
                ),
            )
        )
    return intents


def _applied_applicability(
    expected: store.ExpectedEntry, work_id: str, fraction: float | None, *, node_id: str
) -> store.ExpectedEntry:
    """The current record with one work edge's applicability changed.

    Refusing here rather than in the splice is what makes "this pairing has no
    bullet" a located value refusal instead of a splice failure: the expectation
    is derived from the same baseline the splice runs on, so the two cannot
    disagree about whether the edge is there.
    """
    edges = list(expected.depends_on)
    matched = [i for i, edge in enumerate(edges) if edge.target == work_id]
    if not matched:
        raise _Refused(
            f"work={work_id}",
            f"is not a dependency of {node_id}, so there is no bullet carrying an applicability to "
            f"rewrite. This op re-scores a pairing; it does not create one",
            restore="restore: add the edge with add-depends-on, or correct work, then re-run",
        )
    for position in matched:
        edges[position] = replace(edges[position], applicability=fraction)
    return replace(expected, depends_on=tuple(edges))


def _replace_applicability(node_id: str, work_id: str, fraction: float | None) -> Callable[[str], str]:
    """Substitute the annotation on every bullet of ``node_id`` naming ``work_id``."""
    annotation = render.render_applicability_annotation(render._format_score(fraction))

    def splice(document: str) -> str:
        entry = next((e for e in store.locate_entries(document) if e.node_id == node_id), None)
        if entry is None or entry.quality_start is None or entry.quality_end is None:
            raise store.SpliceError(f"no located ### Quality section for {node_id} in this register")
        lines = document.splitlines()
        for i in range(entry.quality_start, min(entry.quality_end, len(lines))):
            head = kb_index_lib._depends_on_bullet_head(re.sub(r"^\s*-\s*", "", lines[i].strip()))
            if head != work_id:
                continue
            if kb_index_lib._APPLICABILITY_IN_PAREN_RE.search(lines[i]) is None:
                raise store.SpliceError(f"{node_id}'s bullet for {work_id} carries no (applicability …) annotation")
            # Presence is the test, never "the substitution changed something":
            # re-scoring a pairing to the value it already carries is a legal
            # call and must land as the no-op it is.
            replaced = kb_index_lib._APPLICABILITY_IN_PAREN_RE.sub(annotation[1:-1], lines[i], count=1)
            document = store.splice_lines(document, start=i, end=i + 1, lines=[replaced])
            lines = document.splitlines()
        return document

    return splice


def _plan_set_rigor(ctx: _Context, entries: Sequence[values.Entry]) -> list[_Intent]:
    return _plan_field_update(ctx, entries, key="rigor", field_of=_rigor_field, apply=_apply_rigor)


def _plan_set_rationale(ctx: _Context, entries: Sequence[values.Entry]) -> list[_Intent]:
    return _plan_field_update(ctx, entries, key="rationale", field_of=lambda _id: "rationale", apply=_apply_rationale)


# An update's expectation is the current record with ONE field changed, so it
# is built by replacement rather than by re-listing every field. Re-listing is
# how a field added to `ExpectedEntry` silently stops being carried: the copy
# still compiles, still reads correctly, and quietly expects the new field's
# default of whatever was actually on disk — refusing a write that was right.
# The staged fan-out is the field that has done it.


def _apply_rigor(expected: store.ExpectedEntry, value: object) -> store.ExpectedEntry:
    return replace(expected, rigor=value)  # type: ignore[arg-type]


def _apply_rationale(expected: store.ExpectedEntry, value: object) -> store.ExpectedEntry:
    return replace(expected, rationale=str(value))


def _plan_field_update(
    ctx: _Context,
    entries: Sequence[values.Entry],
    *,
    key: str,
    field_of: Callable[[str], str],
    apply: Callable[[store.ExpectedEntry, object], store.ExpectedEntry],
) -> list[_Intent]:
    """One authored field, rewritten in place, with the whole record as the proof."""
    intents: list[_Intent] = []
    for entry in entries:
        node_id = str(entry.values["id"])
        record = _resolve(ctx, node_id, key="id", needs_register=True)
        rel = str(record.register_path)
        register = _contained(ctx.kb_root, rel, key="id")
        value = entry.values[key]
        field_name = field_of(node_id)
        # `render._format_score` is the one spelling of a rigor value's text —
        # a number through the shortest round-tripping repr, `None` as the
        # pending literal. Reaching for it is the third in-package reach the
        # module docstring names; re-spelling it here would be the second
        # implementation of a format.
        rendered = (
            render.collapse_prose(str(value)) if key == "rationale" else render._format_score(value)  # type: ignore[arg-type]
        )
        intents.append(
            _Intent(
                path=rel,
                target=register,
                splice=_replace_line(node_id, field_name, f"- {field_name}: {rendered}"),
                # Bound over the loop variables, so each entry's expectation is
                # this entry's — a bare closure over `node_id` would give every
                # intent in the batch the last one's id (late binding).
                expect_current=lambda read, node_id=node_id, value=value: (
                    apply(_current_entry(read, node_id), value),
                ),
            )
        )
    return intents


@dataclass
class _EdgeAddition:
    """One entry's requested edges of one class, and the subset it does not hold.

    One of these per ``### Quality`` list the op writes, so the two classes
    dedupe against their own sections: a pair recorded as a reference and later
    raised to a dependency gains the depends bullet, its reference bullet being
    a different list's.

    **A dependency graph is a set of edges**, so an edge the entry already
    carries is added as a no-op rather than as a second bullet: a dependency
    pass interrupted partway and re-run over the same claims converges on one
    edge per pair instead of doubling every edge it wrote the first time.

    The subset is decided **once**, from the baseline ``store`` hands the
    splice, by the expecter that runs over it first (:func:`_expecting`) — so
    the bullets written and the record expected are two readings of one decision
    rather than two implementations of one dedupe. Until that read ``new`` is
    every requested edge, which is this op's behaviour without the narrowing: a
    decision that never ran adds what it was asked to and never silently drops.
    """

    node_id: str
    requested: tuple[render.DependsOnTarget, ...]
    new: tuple[render.DependsOnTarget, ...]

    def narrow(self, present: frozenset[str]) -> int:
        """Keep the requested edges ``present`` lacks; return how many it held.

        Repetition inside one values entry counts the same as repetition across
        two calls — the entry is a set of edges either way — so a target named
        twice in one list contributes one bullet and one duplicate.
        """
        held = set(present)
        keep: list[render.DependsOnTarget] = []
        for target in self.requested:
            edge = _reader_target(self.node_id, target)
            if edge in held:
                continue
            held.add(edge)
            keep.append(target)
        self.new = tuple(keep)
        return len(self.requested) - len(self.new)


def _reader_target(node_id: str, target: render.DependsOnTarget) -> str:
    """The id the production reader will key this edge under, once it is a bullet.

    A values file spells a framework target the way the bullet spells it —
    ``Axiom 1`` — and the reader keys that edge under ``axiom-1``, so comparing
    supplied token against parsed target would find no match and write the
    bullet a second time. The rendered bullet is put through the reader that
    will read it: the sixth in-package reach the module docstring names, and the
    only answer that cannot drift from the one the register will give. A bullet
    the reader takes no target off keeps its supplied spelling, which compares
    it against a set it cannot be in — the pre-dedupe behaviour, for a value
    that has no edge in it.

    The depends-on bullet is what it renders for both lists, and it is the right
    one for both: normalisation only ever moves a *framework* token, which a
    references list does not admit, so a claim id comes back as itself.
    """
    edges = kb_index_lib._parse_depends_on_line(render.render_depends_on_bullet(target), node_id)
    return edges[0].target if edges else target.target


def _plan_add_depends_on(ctx: _Context, entries: Sequence[values.Entry]) -> list[_Intent]:
    intents: list[_Intent] = []
    for entry in entries:
        node_id = str(entry.values["id"])
        record = _resolve(ctx, node_id, key="id", needs_register=True)
        rel = str(record.register_path)
        register = _contained(ctx.kb_root, rel, key="id")

        depends = _depends_targets(ctx, entry.values.get("depends-on", ()))  # type: ignore[arg-type]
        referenced = _depends_targets(ctx, entry.values.get("references", ()))  # type: ignore[arg-type]
        if not depends and not referenced:
            raise _Refused(
                node_id,
                "this op adds an entry's outgoing edges and neither list names one",
                restore="restore: supply a depends-on list, a references list, or both, then re-run",
            )
        if referenced and not render.is_claim_id(node_id):
            raise _Refused(
                node_id,
                "carries a references list, and a reference is one claim of this corpus naming another",
                restore="restore: drop the references list, or name a claim entry as the id, then re-run",
            )
        additions = {
            section: _EdgeAddition(node_id=node_id, requested=targets, new=targets)
            for section, targets in (("depends-on", depends), ("references", referenced))
            if targets
        }

        # Bound over the loop variable, so each entry narrows its own additions —
        # a bare closure would give every intent in the batch the last one's
        # (late binding).
        def expect(read: _RegisterRead, node_id: str = node_id, additions: Mapping[str, _EdgeAddition] = additions):
            current = _current_entry(read, node_id)
            held = {"depends-on": current.depends_on, "references": current.references}
            added: dict[str, tuple[store.ExpectedEdge, ...]] = {}
            for section, addition in additions.items():
                already = addition.narrow(frozenset(edge.target for edge in held[section]))
                if already:
                    ctx.noted.append(
                        ReportItem(
                            kb_util.FACT,
                            addition.node_id,
                            f"{already} of the {len(addition.requested)} {section} edge(s) asked for name an "
                            f"edge this entry already has, and were not written a second time",
                        )
                    )
                added[section] = tuple(store.ExpectedEdge(t.target, t.context, t.applicability) for t in addition.new)
            return (
                replace(
                    current,
                    depends_on=current.depends_on + added.get("depends-on", ()),
                    references=current.references + added.get("references", ()),
                ),
            )

        intents.append(
            _Intent(
                path=rel,
                target=register,
                splice=_chained(*(_add_bullets(addition, section=section) for section, addition in additions.items())),
                expect_current=expect,
            )
        )
    return intents


def _plan_set_frontmatter(ctx: _Context, entries: Sequence[values.Entry]) -> list[_Intent]:
    intents: list[_Intent] = []
    for entry in entries:
        supplied = entry.values
        rel = str(supplied["document"])
        target = _contained(ctx.kb_root, rel, key="document")

        claims = tuple(supplied.get("claims", ()))  # type: ignore[arg-type]
        experiments = tuple(supplied.get("experiments", ()))  # type: ignore[arg-type]
        experiment_nodes = tuple(supplied.get("experiment-node", ()))  # type: ignore[arg-type]
        support_nodes = tuple(supplied.get("support-node", ()))  # type: ignore[arg-type]
        for claim_id in claims:
            _resolve(ctx, claim_id, key="claims", needs_register=True)
        for exp_id in experiments:
            _resolve(ctx, exp_id, key="experiments", needs_register=False)
        for node in experiment_nodes:
            _resolve(ctx, node.exp_id, key="experiment-node.exp-id", needs_register=False)
            for pair in node.strengthens:
                _resolve(ctx, pair.id, key="experiment-node.strengthens.id", needs_register=True)
        for node in support_nodes:
            _resolve(ctx, node.sup_id, key="support-node.sup-id", needs_register=True)
            for pair in node.supports:
                _resolve(ctx, pair.id, key="support-node.supports.id", needs_register=True)

        redeclared = [node.exp_id for node in experiment_nodes] + [node.sup_id for node in support_nodes]
        # One record, rendered and proved. The readback's subject is the object
        # the renderer was handed rather than a second listing of the same
        # values: a field carried into one and forgotten in the other is how a
        # comparison comes to pass over the thing it was written to check.
        intended = render.FrontmatterValues(
            kind=str(supplied["kind"]),
            path_stable=supplied.get("path-stable"),  # type: ignore[arg-type]
            claims=claims,
            no_claim=supplied.get("no-claim"),  # type: ignore[arg-type]
            experiments=experiments,
            experiment_nodes=tuple(
                render.ExperimentDecl(
                    exp_id=node.exp_id,
                    status=node.status,
                    strengthens=tuple((pair.id, pair.strength) for pair in node.strengthens),
                )
                for node in experiment_nodes
            ),
            support_nodes=tuple(
                render.SupportDecl(
                    sup_id=node.sup_id,
                    supports=tuple((pair.id, pair.fraction) for pair in node.supports),
                )
                for node in support_nodes
            ),
        )
        intents.append(
            _Intent(
                path=rel,
                target=target,
                splice=_replace_block(render.render_frontmatter_block(intended), rel=rel, redeclared=redeclared),
                prove=_frontmatter_prover(intended),
                subject=rel,
            )
        )
    return intents


def _existing_frontmatter(text: str, rel: str, *, redeclared: Iterable[str]) -> Mapping[str, object] | None:
    """The document's current frontmatter fields, refusing what a replace would lose.

    Two losses are possible when a whole block is replaced, and both are refused
    rather than absorbed:

    * **A key this API's closed vocabulary cannot render**, which the rewrite
      would simply not carry — the same answer the values grammar gives an
      unrecognized key, at the other end of the same write.
    * **A hosted node declaration the values file does not restate.** An
      ``exp-id:`` / ``sup-id:`` block *originates* a node; dropping one destroys
      it and orphans every reference to it. The values file is a total
      declaration of the block's authored content, so the caller either
      restates the declaration or is told which one they are about to remove.
      Membership fields (``claims:``) are not in this class: dropping a member
      changes what a document cites and destroys nothing. Nor are the free-text
      attributes (``no-claim:``, ``path-stable:``): omitting one removes it, and
      the op that removed it is the op that puts it back.

    The declaration keys are read through ``kb_index_lib``'s own patterns, which
    are what decides on the reading side what a declaration is.

    Takes the document's text rather than its path: it is called from inside the
    splice, over the baseline ``store`` read, so that what it refuses and what
    the replace would actually lose are read off the same bytes.
    """
    fields = kb_index_lib.parse_frontmatter(text)
    if fields is None:
        return None
    carried = {name for name, _ in _DERIVED_FRONTMATTER_FIELDS}
    unknown = sorted(set(fields) - _KNOWN_FRONTMATTER_KEYS - carried)
    if unknown:
        raise _Refused(
            rel,
            f"carries frontmatter key(s) {unknown} that this API cannot render, so replacing the "
            f"block would drop them",
            restore=f"restore: remove {unknown[0]!r} from the block by hand, or leave this document's "
            f"frontmatter alone, then re-run",
        )
    block = store.FRONTMATTER_BLOCK.search(text)
    declared = {node_id for pattern in kb_index_lib._LEAF_DECL_RES for node_id in pattern.findall(block.group(1))}
    dropped = sorted(declared - set(redeclared))
    if dropped:
        raise _Refused(
            rel,
            f"declares hosted node(s) {dropped} that this values file does not restate, and replacing "
            f"the block would destroy the declaration rather than edit it",
            restore=f"restore: restate {dropped[0]}'s block in the values file, or leave this "
            f"document's frontmatter alone, then re-run",
        )
    return fields


def _observed_frontmatter(path: Path, kb_root: Path) -> render.FrontmatterValues | None:
    """The candidate's frontmatter block, as the production readers return it.

    Three readers, because the block carries three grammars and no one function
    owns them all. :func:`kb_index_lib.parse_frontmatter` types the flat fields;
    :func:`kb_index_lib.parse_experiment_leaf` and
    :func:`kb_index_lib.parse_support_leaf` are what confer node-hood on an
    ``exp-id:`` / ``sup-id:`` block, and they are the same functions the claim
    graph is built from — so a declaration they do not return is a node that
    does not exist, whatever the bytes on the line look like.
    """
    fields = kb_index_lib.parse_frontmatter(path.read_text(encoding="utf-8"))
    if fields is None:
        return None
    return render.FrontmatterValues(
        kind=str(fields.get("kind") or ""),
        path_stable=fields.get("path-stable"),
        claims=tuple(fields.get("claims") or ()),
        no_claim=fields.get("no-claim"),
        experiments=tuple(fields.get("experiments") or ()),
        experiment_nodes=tuple(
            render.ExperimentDecl(exp_id=node.id, status=node.status, strengthens=tuple(node.strengthens))
            for node in kb_index_lib.parse_experiment_leaf(path, kb_root)
        ),
        support_nodes=tuple(
            render.SupportDecl(
                sup_id=node.id,
                supports=tuple(
                    (claim_id, None if fraction is kb_index_lib.PENDING_FRACTION else fraction)
                    for claim_id, fraction in node.supports
                ),
            )
            for node in kb_index_lib.parse_support_leaf(path, kb_root)
        ),
    )


def _frontmatter_prover(intended: render.FrontmatterValues) -> Prover:
    """Prove the whole intended record back, field by field.

    **Both hosted-declaration classes are compared**, and they are the fields
    that most need it: an ``exp-id:`` or ``sup-id:`` block *originates* a node,
    so a declaration a renderer dropped or a splice mislaid is not a missing
    attribute but a destroyed node — total loss for an ``exp-`` id, whose only
    other birth path is this op's sibling, and worse than total for a ``sup-``
    id, whose register staging copy leaves the graph looking plausible while the
    canonical home the claim graph reads is empty. The op already refuses that
    loss when it can see it in its *input* (:func:`_existing_frontmatter`);
    this is the same loss seen in its own *output*.

    A hosted declaration written into a document whose ``kind`` is not a leaf
    kind reads back as absent, because leaf-kind-ness is what confers node-hood
    on the reading side — so it refuses. That is correct rather than
    over-strict: a declaration no reader can see is exactly the silent loss the
    readback exists to make unrepresentable.
    """

    def prove(path: Path, kb_root: Path) -> list[str]:
        observed = _observed_frontmatter(path, kb_root)
        if observed is None:
            return ["kb-frontmatter"]
        # Both free-text fields are emitted collapsed and double-quoted and come
        # back unquoted, so the comparison is against the collapsed supplied
        # value — and against `None` where the value was not supplied, which is
        # what catches a field the splice left behind rather than replaced.
        want = replace(
            intended,
            path_stable=None if intended.path_stable is None else render.collapse_prose(intended.path_stable),
            no_claim=None if intended.no_claim is None else render.collapse_prose(intended.no_claim),
        )
        return [
            key for name, key in _FRONTMATTER_PROVEN_FIELDS.items() if getattr(observed, name) != getattr(want, name)
        ]

    return prove


def _plan_mark_claim(ctx: _Context, entries: Sequence[values.Entry]) -> list[_Intent]:
    intents: list[_Intent] = []
    for entry in entries:
        supplied = entry.values
        rel = str(supplied["document"])
        node_id = str(supplied["id"])
        target = _contained(ctx.kb_root, rel, key="document")
        _resolve(ctx, node_id, key="id", needs_register=True)
        if not target.is_file():
            raise _Refused(
                rel,
                "does not exist. A marker is placed beside the prose it anchors, and this API never "
                "composes a document body",
                restore="restore: correct document, or author the document first, then re-run",
            )
        intents.append(
            _Intent(
                path=rel,
                target=target,
                splice=_insert_marker(
                    render.render_tier2_marker(node_id), str(supplied["locator"]), node_id=node_id, rel=rel
                ),
                prove=_marker_prover(node_id),
                subject=f"{rel}:{node_id}",
            )
        )
    return intents


def _marker_prover(node_id: str) -> Prover:
    def prove(path: Path, kb_root: Path) -> list[str]:
        record = kb_index_lib.parse_leaf(path, kb_root)
        if record is None or node_id not in record.tier2_marked:
            return ["claim-quality marker"]
        return []

    return prove


def _plan_set_on_point_fraction(ctx: _Context, entries: Sequence[values.Entry]) -> list[_Intent]:
    """Rewrite one on-point fraction, in whichever home the pair is authored in.

    A fan-out has two legitimate homes and which one a given pair lives in is a
    fact about the build's position, not a choice the caller makes: the hosting
    document's ``sup-id:`` block is canonical, and the ``sup-`` register entry
    stages the pairs while that document does not exist yet. The scoring wave
    that sets these fractions runs at phase-2.6, a full stage before the
    documents are written, so the staged home is the ordinary one at the moment
    this op is called.

    **The hosted home wins where it exists.** A support whose document has been
    written carries the canonical pairs there, and that is what the claim graph
    reads; rewriting the staging copy instead would move a number the graph
    never sees. A pair in NEITHER home is still a refusal — there is nothing to
    rewrite, and inventing the pair would be authoring an edge from a values
    file that only asked to re-score one.
    """
    intents: list[_Intent] = []
    for entry in entries:
        supplied = entry.values
        sup_id = str(supplied["id"])
        claim_id = str(supplied["claim"])
        fraction = supplied["fraction"]
        record = _resolve(ctx, sup_id, key="id", needs_register=True)
        _resolve(ctx, claim_id, key="claim", needs_register=True)
        line = render.render_supports_pair_line(claim_id, fraction)  # type: ignore[arg-type]

        if record.hosting_leaf is not None:
            rel = record.hosting_leaf
            intents.append(
                _Intent(
                    path=rel,
                    target=_contained(ctx.kb_root, rel, key="id"),
                    splice=_replace_supports_pair(sup_id, claim_id, line),
                    prove=_fraction_prover(sup_id, claim_id, fraction),  # type: ignore[arg-type]
                    subject=f"{rel}:{sup_id}",
                )
            )
            continue

        # `_resolve(needs_register=True)` above has already established that the
        # register entry exists, so this is the staged home or nowhere.
        rel = str(record.register_path)
        register = _contained(ctx.kb_root, rel, key="id")

        # Both the refusal and the expectation are questions about the register's
        # content, so both are asked of the baseline the splice runs on —
        # and asking them together keeps them asked of one generation of the
        # file. The splice's own `SpliceError` covers the same absence from the
        # other side; this is the located refusal that names the two homes.
        def expect(read: _RegisterRead, sup_id: str = sup_id, claim_id: str = claim_id, fraction=fraction):
            current = _current_entry(read, sup_id)
            at = next((i for i, (beneficiary, _) in enumerate(current.supports) if beneficiary == claim_id), None)
            if at is None:
                raise _Refused(
                    f"id={sup_id}",
                    f"authors no supports pair for {claim_id} in either home — it is declared in no "
                    f"document's frontmatter, and its register entry stages no such pair. This op "
                    f"re-scores an edge that exists; it does not create one",
                    restore=f"restore: author the pair — with insert-support-entry's supports value if "
                    f"{sup_id} is being created, or with set-frontmatter once its hosting document "
                    f"exists — then re-run",
                )
            updated = list(current.supports)
            updated[at] = (claim_id, fraction)
            return (replace(current, supports=tuple(updated)),)

        intents.append(
            _Intent(
                path=rel,
                target=register,
                splice=_replace_staged_pair(sup_id, claim_id, line),
                expect_current=expect,
            )
        )
    return intents


def _fraction_prover(sup_id: str, claim_id: str, fraction: float | None) -> Prover:
    def prove(path: Path, kb_root: Path) -> list[str]:
        node = next((n for n in kb_index_lib.parse_support_leaf(path, kb_root) if n.id == sup_id), None)
        if node is None:
            return ["sup-id"]
        got = dict(node.supports).get(claim_id, "absent")
        want = kb_index_lib.PENDING_FRACTION if fraction is None else fraction
        return [] if got == want else ["fraction"]

    return prove


# ---------------------------------------------------------------------------
# The ladder driver
# ---------------------------------------------------------------------------


def _execute(op: str, *, kb_root: Path, values_file: Path, plan) -> Result:
    """Run the ladder for one op over one values file.

    Every exit from this function is one of the three codes, and nothing is
    written on 7 or 8.
    """
    root = Path(kb_root)
    if not root.is_dir():
        return _environment(op, str(kb_root), "is not a directory, so no KB can be resolved under it")
    root = root.resolve()

    try:
        parsed = values.parse_values_file(Path(values_file), op=op)
    except OSError as exc:
        return _environment(op, str(values_file), f"could not be read: {exc}")
    if parsed.refused:
        return _refused_values(op, parsed)

    try:
        ctx = _open_store(root)
        outcome = store.apply_edits(kb_root=root, edits=_batch(plan(ctx, parsed.entries), kb_root=root))
    except _Refused as exc:
        return Result(op=op, exit_code=ExitCode.REFUSED, report=(_fail(exc.name, exc.detail, exc.restore),))
    except _Unreadable as exc:
        return _environment(op, exc.name, exc.detail, restore=exc.restore)
    except _ReadbackFailed as exc:
        return Result(
            op=op,
            exit_code=ExitCode.REFUSED,
            report=(
                _fail(
                    exc.subject,
                    f"read back from the composed candidate with a different "
                    f"{', '.join(exc.mismatched)} than the values supplied, so the live file was "
                    f"never written",
                    "restore: this is a renderer defect, not a value defect — report it; nothing on " "disk changed",
                ),
            ),
        )
    except (kb_index_lib.ExperimentLeafError, kb_index_lib.SupportLeafError) as exc:
        return Result(
            op=op,
            exit_code=ExitCode.REFUSED,
            report=(
                _fail("kb-frontmatter", str(exc), "restore: repair the named document's frontmatter, then re-run"),
            ),
        )
    except store.BatchInterrupted as exc:
        # Caught ahead of `OSError` — it is one, by inheritance, so the exit code
        # stays the environment's — because the generic handler below names the
        # ROOT and reports `written=()`. On a batch that half landed, both of
        # those are false: the fault is one named target's, and the caller is
        # the only one who can reconcile what did land. The minted ids are
        # still reported:
        # an id this op drew and wrote into a committed file must not be lost
        # because a LATER target failed, and `written` is what says which of
        # them reached disk.
        report = [ReportItem(kb_util.PASS, path, f"written by {op}") for path in exc.written]
        report.append(
            _fail(
                exc.failed,
                f"could not be replaced, and this batch is interrupted rather than refused: every entry "
                f"was proven before the first replace, so the targets listed above are written and this "
                f"one is not ({exc.__cause__})",
                f"restore: repair the named file's environment, then re-run {op} with a values file "
                f"carrying only the entries for the targets that did not land — re-running the whole "
                f"batch would mint a second id for every insert already committed",
            )
        )
        report += [ReportItem(kb_util.FACT, "minted", f"{node_id} ({op})") for node_id in ctx.minted]
        report += ctx.noted
        return Result(
            op=op,
            exit_code=ExitCode.ENVIRONMENT,
            report=tuple(report),
            minted=tuple(ctx.minted),
            written=exc.written,
        )
    except OSError as exc:
        return _environment(op, str(root), f"could not be written under: {exc}")

    if outcome.status is store.Status.WRITTEN:
        report = [ReportItem(kb_util.PASS, path, f"written by {op}") for path in outcome.written]
        report += [ReportItem(kb_util.FACT, "minted", f"{node_id} ({op})") for node_id in ctx.minted]
        report += ctx.noted
        return Result(
            op=op,
            exit_code=ExitCode.WRITTEN,
            report=tuple(report),
            minted=tuple(ctx.minted),
            written=outcome.written,
        )
    if outcome.status is store.Status.RETRY:
        return Result(
            op=op,
            exit_code=ExitCode.RETRY,
            report=(
                _fail(
                    str(outcome.subject),
                    f"{outcome.detail} ({outcome.reason}). The values were correct and nothing was " f"written",
                    f"restore: re-run {op} with this values file unchanged — never re-author values "
                    f"that were already right",
                ),
            ),
        )
    return Result(
        op=op,
        exit_code=ExitCode.REFUSED,
        report=(
            _fail(
                str(outcome.subject),
                f"{outcome.detail} ({outcome.reason})",
                f"restore: correct the values file or repair the named file, then re-run {op}",
            ),
        ),
    )


def _fail(name: str, detail: str, restore: str) -> ReportItem:
    return ReportItem(kb_util.FAIL, name, f"{detail}. {restore}")


def _environment(op: str, name: str, detail: str, *, restore: str = "") -> Result:
    """An exit-2 result: the environment is unfit and no value can fix it.

    ``restore`` overrides the generic clause where the fault is located
    precisely enough to name the corrective act — a KB file that will not
    decode names itself, and telling that caller to "correct the environment"
    would withhold what the op already knows.
    """
    return Result(
        op=op,
        exit_code=ExitCode.ENVIRONMENT,
        report=(_fail(name, detail, restore or "restore: correct the invocation's environment, then re-run"),),
    )


def _refused_values(op: str, parsed: values.ParsedValues) -> Result:
    """Step 1's refusals, one report line each, every one located."""
    report = []
    for refusal in parsed.refusals:
        name = f"entry[{refusal.entry}].{refusal.field}" if refusal.entry else refusal.field
        located = f" (line {refusal.line})" if refusal.line else ""
        report.append(_fail(name, f"{refusal.detail}{located}", f"restore: correct {refusal.field} and re-run {op}"))
    return Result(op=op, exit_code=ExitCode.REFUSED, report=tuple(report))


# ---------------------------------------------------------------------------
# The read-only op
#
# It writes nothing, so the ladder stops for it at step 2: no census, no
# render into a candidate, no temp, no readback, no replace — there is no
# document being composed. What it keeps is the first two rungs, the closed
# values vocabulary and path containment, followed by the checks
# `verify_citations` will itself apply to the string it prints.
#
# **Those checks are not restated here.** The section finder is
# `kb_index_lib.anchor_section`, the same object the gate calls, and the
# excerpt comparison is
# the gate's own whitespace-normalized containment, spelled with
# `render.collapse_prose`, whose semantics `test_kb_write_ops.py` pins to the
# gate's normalizer over a table of inputs. A composer with its own reading of
# "the excerpt appears at the target" would print citations the gate rejects,
# which is the failure this op exists to remove rather than relocate.
#
# **Deliberately NOT mirrored: an ambiguity refusal.** The gate asks whether
# the excerpt appears in the cited section and asks nothing else, so a clause
# occurring twice there is a citation it passes; refusing it here would be this
# program authoring a rule of its own. `mark-claim-in-leaf`'s ambiguity
# refusal answers a different question — where to PUT a marker, which has one
# right answer or none — and is not a precedent for this one.
# ---------------------------------------------------------------------------


def _nearest_window(needle: str, haystack: str) -> tuple[str, int] | None:
    """The window of ``haystack`` a failed match came closest to, and where it starts.

    A refusal that can say "the section reads X where you quoted Y" turns a
    re-quote into one edit; one that can only say "not found" sends the caller
    back to read the whole section. The window is anchored on the longest run
    the two share, and a run too short to be a quotation of anything is
    reported as no near miss at all rather than as a misleading one.

    It returns the offset as well as the text because a window with no position
    cannot name the sentence it sits in, and naming that sentence is what
    :func:`nearest_excerpt` is for. ``_compose_citation`` reads only the text:
    a citation refusal names a section, which it already knows.
    """
    match = difflib.SequenceMatcher(None, needle, haystack, autojunk=False).find_longest_match(
        0, len(needle), 0, len(haystack)
    )
    if match.size < max(12, len(needle) // 3):
        return None
    start = max(0, match.b - match.a)
    return haystack[start : start + len(needle)], start


def _compose_citation(kb_root: Path, supplied: Mapping[str, object]) -> tuple[str, str]:
    """One entry's citation, and the ``path#anchor`` it was verified against.

    Every refusal below is a check the citation gate would make on the printed
    string, asked here instead — at the moment the values are supplied, by the
    caller who can still fix them, rather than three phases later against a
    corpus nobody remembers authoring.
    """
    citing_rel = str(supplied["citing-document"])
    cited_rel = str(supplied["cited-document"])
    anchor = render.collapse_prose(str(supplied["anchor"]))
    excerpt = str(supplied["excerpt"])

    citing = _contained(kb_root, citing_rel, key="citing-document")
    cited = _contained(kb_root, cited_rel, key="cited-document")

    # The citing document need not exist — the ordinary call composes a
    # citation for prose being written now — but its DIRECTORY must, because
    # the link is rendered relative to it: a typo'd directory would otherwise
    # silently produce a well-formed link to the wrong place, which is the
    # silent-loss class arriving through the door built to close it.
    if not citing.parent.is_dir():
        raise _Refused(
            "citing-document",
            f"{citing_rel!r} names a directory that does not exist under kb-root/. The link is "
            f"rendered relative to the citing document, so its directory decides the target's "
            f"spelling — the file itself may still be unwritten",
            restore="restore: correct citing-document to the path this citation will be written " "into, then re-run",
        )
    if not cited.is_file():
        raise _Refused(
            "cited-document",
            f"{cited_rel!r} does not resolve to a file. A citation names a durable KB path that "
            f"exists at the moment it is written",
            restore="restore: correct cited-document, or cite a document that has been written, " "then re-run",
        )

    # **Exit 7, where the store walk's undecodable file is exit 2**, and the
    # difference is who chose the file. `_Unreadable` covers a file the walk
    # reached on its own — nothing in a values file can fix a latin-1 byte in a
    # register three directories away, so re-asking for values is the
    # duplicate-mint path the ladder keeps closed. This file was NAMED by the
    # caller,
    # in `cited-document`. Citing a document that will not decode is a bad
    # value, and correcting it is exactly what a 7 asks for.
    try:
        cited_text = cited.read_text(encoding="utf-8")
    except OSError as exc:
        # Named, rather than left to the op's boundary handler, which reports
        # the whole KB root as unreadable for one file the caller pointed at.
        # Exit 2 and not 7: the file exists (checked just above) and the values
        # name it correctly — what failed is the environment's ability to hand
        # it over, which no correction to a values file can address.
        raise _Unreadable(
            "cited-document",
            f"{cited_rel!r} exists but could not be read ({exc}), so the excerpt could not be checked " f"against it",
            restore=f"restore: make {cited_rel} readable, then re-run — the values named an existing "
            f"document and must not be re-authored",
        ) from exc
    except UnicodeError as exc:
        raise _Refused(
            "cited-document",
            f"{cited_rel!r} is not valid UTF-8 ({exc}), so the excerpt cannot be checked against it. A "
            f"citation is verified by reading the cited section, and a file that will not decode has no "
            f"sections to read",
            restore=f"restore: cite a document that decodes as UTF-8, or re-encode {cited_rel}, then re-run",
        ) from exc

    body = kb_index_lib.anchor_section(cited_text, anchor)
    if body is None:
        raise _Refused(
            "anchor",
            f"{anchor!r} names no section of {cited_rel}. An anchor is a heading's slug, and a "
            f"heading that exists only inside a fenced example is not a section",
            restore=f"restore: correct anchor to the slug of a heading in {cited_rel}, then re-run",
        )

    needle = render.collapse_prose(excerpt)
    haystack = render.collapse_prose(body)
    if needle not in haystack:
        window = _nearest_window(needle, haystack)
        found = f"the nearest text there reads {window[0]!r}" if window else "no comparable text appears there"
        raise _Refused(
            "excerpt",
            f"does not appear at {cited_rel}#{anchor} — {found}. An excerpt is quoted verbatim "
            f"from the section it cites, compared with whitespace collapsed and fenced examples "
            f"removed",
            restore=f"restore: quote the clause verbatim from {cited_rel}'s {anchor!r} section, " f"then re-run",
        )

    # The link target is relative to the CITING document, because that is how
    # every reader of it resolves one (`verify_citations.check_citations`,
    # `verify_md_links`). Computed on the two kb-root-relative POSIX strings
    # rather than on the filesystem paths, so the spelling is the same on every
    # platform and does not depend on either file existing.
    link_target = posixpath.relpath(cited_rel, posixpath.dirname(citing_rel) or ".")
    citation = render.render_citation(excerpt=excerpt, kb_path=link_target, anchor=anchor)

    # Last: the composed bytes must be a link to the intended target *as the
    # toolchain's shared link primitive reads them*. An unbalanced bracket in an
    # excerpt, or whitespace in an anchor, makes the string invisible to the
    # gate's regex — and a citation no gate can see is the one failure worse
    # than a rejected one, because it reads as verified authority while nothing
    # ever checked it.
    if kb_links.LINK_RE.findall(citation) != [f"{link_target}#{anchor}"]:
        raise _Refused(
            "excerpt",
            f"composes a citation the toolchain's link reader does not see as one link to "
            f"{link_target}#{anchor} — an unbalanced '[' or ']' in the excerpt, or whitespace or "
            f"')' in the anchor, hides the whole citation from every gate that reads links",
            restore="restore: re-quote the excerpt without an unbalanced bracket, correct the " "anchor, then re-run",
        )
    return citation, f"{cited_rel}#{anchor}"


def render_citation(*, kb_root: Path, values_file: Path) -> Result:
    """Print the sanctioned authority-citation form, verified against the KB.

    The one read-only op of the surface: it reads the cited document to
    check the quotation, writes nothing anywhere, and returns the exact
    ``["<excerpt>"](<path>#<anchor>)`` string for the caller to place. What it
    converts is a phase-3 gate failure into a refusal at authoring time — and
    what it does not convert is the placement, which stays the agent's and
    stays covered by ``verify_citations``.

    All-or-nothing over a batch, as every op is: a file carrying three
    citations prints three or prints none, because a caller who transcribed two
    of three and then had to fix the third would be reconciling a partial
    result by hand.
    """
    op = "render-citation"
    root = Path(kb_root)
    if not root.is_dir():
        return _environment(op, str(kb_root), "is not a directory, so no KB can be resolved under it")
    root = root.resolve()

    try:
        parsed = values.parse_values_file(Path(values_file), op=op)
    except OSError as exc:
        return _environment(op, str(values_file), f"could not be read: {exc}")
    if parsed.refused:
        return _refused_values(op, parsed)

    try:
        composed = [_compose_citation(root, entry.values) for entry in parsed.entries]
    except _Refused as exc:
        return Result(op=op, exit_code=ExitCode.REFUSED, report=(_fail(exc.name, exc.detail, exc.restore),))
    except _Unreadable as exc:
        return _environment(op, exc.name, exc.detail, restore=exc.restore)
    except OSError as exc:
        return _environment(op, str(root), f"could not be read under: {exc}")

    return Result(
        op=op,
        exit_code=ExitCode.PRINTED,
        report=tuple(ReportItem(kb_util.FACT, subject, f"excerpt verified ({op})") for _, subject in composed),
        printed=tuple(citation for citation, _ in composed),
    )


# ---------------------------------------------------------------------------
# The op registry — what the surface binds
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Op:
    """One op, and the two facts a surface needs to declare it.

    ``mints`` is the id kind the op brings into being, or ``None`` for an op
    that only edits nodes that already exist. It is the declared half of the
    mint partition and the test asserts the code matches it. ``creates_register``
    says whether ``--create`` is admissible on this op and on no other:
    creation is never implicit, and a companion belonging to one op must not be
    spellable on another.
    """

    name: str
    run: Callable[..., Result]
    mints: str | None = None
    creates_register: bool = False


#: The write surface: the nine ops that touch a file. Membership here is
#: what makes an op a write op everywhere else — ``kb_util.WRITE_OPS`` is
#: asserted equal to these keys, the driver's ledger refuses to spawn anything
#: outside them, and ``steps.WRITE_OP_SLOTS`` renders one brief slot per member.
OPS: Mapping[str, Op] = {
    op.name: op
    for op in (
        Op("insert-claim-entry", insert_claim_entry, mints="clm", creates_register=True),
        Op("insert-support-entry", insert_support_entry, mints="sup", creates_register=True),
        Op("insert-experiment-entry", insert_experiment_entry, mints="exp"),
        # `mints` is None and that is exact rather than an omission: an external
        # work's id is derived from its citation key, so there is no draw to
        # declare and no collision to check (see `insert_work_entry`).
        Op("insert-work-entry", insert_work_entry, creates_register=True),
        Op("set-work-strength", set_work_strength),
        Op("set-applicability", set_applicability),
        Op("set-rigor", set_rigor),
        Op("set-rationale", set_rationale),
        Op("add-depends-on", add_depends_on),
        Op("set-frontmatter", set_frontmatter),
        Op("mark-claim-in-leaf", mark_claim_in_leaf),
        Op("set-on-point-fraction", set_on_point_fraction),
    )
}

#: The read surface — a **sibling** registry, deliberately not a tenth row of
#: :data:`OPS`. Everything keyed off the write registry is a statement
#: about writing: ``ledger._WRITE_OP_EXITS`` says what a driver does with a 7
#: (a driver defect, because the driver composed the values) and with an 8 (a
#: contended file, re-run unchanged) — and a read op has no 8 to earn, having
#: nothing to contend over. ``steps.WRITE_OP_SLOTS`` renders a brief slot per
#: write op, and no brief invokes this one. The mint partition walks
#: ``OPS`` asking which ops mint; a read op mints nothing and would answer
#: vacuously in every clause.
#:
#: The two registries are disjoint by test, and their union is exactly the
#: closed vocabulary of :data:`values.OP_FIELDS`: a values row with no op, or
#: an op with no vocabulary, is drift between the surface an agent writes
#: against and the surface this package checks.
READ_OPS: Mapping[str, Op] = {op.name: op for op in (Op("render-citation", render_citation),)}
