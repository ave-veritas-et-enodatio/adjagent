"""The read-modify-write path over authored Markdown.

``render.py`` composes the bytes; this module is what puts them on disk without
ever leaving a file holding bytes nobody proved. The ladder it runs:

2. **Path containment** — every target resolves inside ``kb-root/`` after
   symlink resolution *and names a file in it*, or is refused **unread and
   unwritten**. A value that resolves to the root itself, or that no
   filesystem can hold (a NUL byte), is refused here rather than at the first
   ``open`` — see :func:`resolve_target`.
4. **Census, before** — for each register the op will touch, the count of
   canonical ``<!-- id: … -->`` markers equals the count of records the
   production parser returns, **and every marker binds to a heading of its
   own**. A pre-existing mismatch is a refusal naming the defect and the file:
   the op will not write into a register that is already losing entries.
   Counting alone cannot see the same defect one heading earlier — a marker
   bound to the *wrong* heading leaves the counts equal — so the census asks the
   binding question too (``kb_index_lib.mis_bound_entries``). A census is
   believed only if the file held still while it was taken: it is several opens
   of one name, and a concurrent publish between two of them describes no
   version of the file (:func:`_compose`).
6. **Write the temp** — beside the target, so ``os.replace`` is a rename within
   one directory and therefore atomic. Nothing else on disk has changed. Steps
   6, 7 and 8 run with the target's directory held (:func:`_exclusive`); the
   first act under that lock is to unlink any publish temp a killed writer left
   beside the target (:func:`_sweep_orphan_temps`). ``tempfile.mkstemp`` makes
   its file ``0600`` by design, and ``os.replace`` carries a file's permission
   bits with it rather than the destination name's — the replace is a rename,
   not a copy, so what lands under the target's name is the temp's own inode
   with the temp's own mode. Left alone that would narrow every file this API
   touches to owner-only on every write, silently, which is not this API's
   change to make: :func:`_target_mode` sets the temp's mode before it is ever
   proven, to a pre-existing target's own mode or, for a creation, to what an
   ordinary create would get under the process umask (see
   :func:`_default_file_mode`) — never a hard-coded ``0644``.
7. **Prove the temp** — re-parse it with the *production* parsers, match
   every intended record field by field against the values supplied, re-run the
   census on it, and assert the record count is step 4's plus the intended
   delta. Any failure discards the temp and refuses; the live file was never
   written, so there is nothing to restore and no window to be killed in. A
   ``sup-`` entry is read by two of those parsers — its own fields by
   ``parse_support_quality_entries`` and its staged ``supports:`` fan-out by
   ``parse_register_staged_supports`` — because the entry has two grammars and
   proving one of them is proving half a write.
8. **``os.replace``, under the freshness check** — the live file's content must still
   equal what step 4 read. If it does not, a concurrent writer intervened: the
   temp is discarded and the outcome is **retry**, never a merge and never a
   silent overwrite. The check and the replace run inside one critical section
   held against every other writer of the same file (:func:`_exclusive`), and
   the compared read is taken **inside** it: a check whose answer is computed
   outside the exclusion answers a question about a moment that has already
   passed. See below.

**Why step 8 is one critical section and not two steps.** With the check loop
and the replace loop adjacent but unguarded, every writer in a wave reads the
same baseline, proves its own temp, sees the baseline still live, and replaces
unconditionally: the last replace wins and overwrites the entries the others
have just published. Nothing reports it — each writer's own record survives its
own temp, so the readback passes for all of them; each file is internally
complete, so the census passes too; and all of them exit 0. Measured at three
concurrent inserts into one register, that shape lost an entry in **22 of 25
rounds and reported 34 ids minted with no entry anywhere in the KB** — precisely
the ghost-id state mint fusion exists to make unconstructible. The window is
small and the loss rate 88%, which is the ordinary shape of a race: rare per
instruction, near-certain per wave. The exclusion below closes it, and
``test_writeapi_concurrency.py`` is the probe that measured it, kept as a test.

**Where the exclusion starts, and why it is not the check.** The section opens
before the first temp is written rather than at the check, because the same lock
carries the orphaned-temp sweep and a temp is only provably an orphan while
nobody else can be making one. What stays *outside* it is the read, the census
and the splice — the expensive half, and the half a second writer must be able
to interleave with, since a writer that composed against a baseline someone has
since replaced is exactly the writer the freshness check must answer *retry* to.

**Three outcomes, not two.** :class:`Outcome` carries ``written`` /
``refused`` / ``retry``, and the distinction is not cosmetic: *refused* means
the values are wrong and must be fixed, *retry* means the values were right and
the write did not happen for a reason nothing about them can fix. Re-asking a
model for values that were already correct is how a duplicate id gets written.
Process exit codes (7 and 8) are ``ops.py``'s mapping of these; nothing here
knows an exit code, and nothing here composes a report line.

**All-or-nothing per invocation.** Every edit in a batch is prepared and
proven before any of them is replaced, and the freshness check runs over every
target before the first replace. A batch that refuses any entry writes none. The
critical section covers steps 6 through 8 for *every* target at once, so a
batch's targets are checked and replaced with all of them held: no target may
move between the first check and the last replace.

**Where all-or-nothing stops, and what is reported there.** Every *verdict* on
the values is reached before the first replace,
so no batch is ever half-refused. The one thing that can still fail partway is
the replace loop itself — ENOSPC, EIO, a quota, a permission change on one
directory of several — and across several directories that is not fixable
without a journal, which this toolchain does not have. So it is *reported*
instead of pretended away: :class:`BatchInterrupted` names the target that could
not be replaced and lists the ones already committed, and the caller sees the
environment's exit code (never *refused* or *retry* — the values were right).
What this module must never do is raise a bare ``OSError`` after committing part
of a batch, leaving the caller with ``written=()`` on a run that wrote.

**Register creation is explicit.** A target that does not exist is a
refusal naming the path unless the caller opted in with ``Edit(create=True)`` —
a typo'd register path can no longer succeed into a fresh, empty file.

**The temp's placement.**
``ClaimEntry.canonical_path`` and ``canonical_anchor`` are derived by the parser
from the parsed file's own ``relative_to(kb_root)``, so a temp that is not the
target makes those two fields differ from the target's for a reason the op never
supplied. Of the two possible answers this module takes the second: **the temp is
a sibling of the target and the two path-derived fields are excluded from the
readback comparison by name** — :data:`PATH_DERIVED_FIELDS`, with
``test_kb_write_store.py`` asserting that every field of ``ClaimEntry`` is either
compared or named in one of the two exclusion tuples, so the exclusion cannot
silently grow. The compared half of that partition is :data:`COMPARED_FIELD_READS`,
which is the map :func:`_mismatched_fields` iterates — one object, so a field
declared compared *is* compared. A guard certifying one tuple while a separate
literal did the comparing would make "declared compared" and "compared" two
independent facts with a green test between them.

The other answer — a temp whose ``relative_to(kb_root)`` equals the target's —
fails on two counts, both fatal rather than aesthetic. It requires a
mirror directory tree, and that tree has to sit on the target's own filesystem
for ``os.replace`` to stay atomic, which puts a second file named
``claim-quality.md`` **inside ``kb-root/``**: ``scan_authored_ids`` walks
``kb_root.rglob("claim-quality.md")``, so a concurrent reader would see every id
in the temp as a duplicate register entry — this API's own uniqueness question
answered wrong, by this API's own scratch file. The sibling temp is invisible to
both KB walks by construction (see :data:`TEMP_SUFFIX`), and nothing is lost by
the exclusion: ``canonical_anchor`` is a pure function of the heading title,
which *is* compared, and ``canonical_path`` is a pure function of the path the
op itself named.

**Derived fields are not compared and not supplied.** ``solidity``,
``build_status`` and ``solidity_trace`` are ``refresh``'s;
:class:`ExpectedEntry` has no field for any of them, so there is nothing for the
readback to match and no path by which this module could write one.
:data:`DERIVED_FIELDS` names them so the structural test above stays total.

**Dependency direction**: ``store`` → ``kb_index_lib`` (the production
parsers, for readback and location) and ``render`` (the prose collapse the
comparison is well-posed in), plus the standard library. It computes no derived
value, imports no solidity machinery, does not know what a build band is, and
writes nothing under ``.index/``. ``fcntl`` is stdlib but POSIX-only, which
matches the rest of the toolchain's assumptions (``os.replace`` over a sibling
temp, a git-backed KB) and keeps the stdlib-only rule whole: closing the race
needs no third-party dependency.
"""

import contextlib
import fcntl
import os
import re
import stat
import tempfile
import time
from collections.abc import Callable, Iterator, Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

from kb_tools import kb_index_lib, kb_schema
from kb_tools.kb_write import render

# ---------------------------------------------------------------------------
# The temp file's name
# ---------------------------------------------------------------------------

#: The suffix every temp this module writes carries. Load-bearing, not
#: decorative: a temp beside the target is inside ``kb-root/``, where two walks
#: are looking. ``kb_index_lib.kb_files`` globs ``*.md`` and
#: ``_register_id_occurrences`` globs the exact name ``claim-quality.md``; a
#: name ending in this suffix matches neither, so a concurrent reader can never
#: see a half-written candidate as authored content. The leading dot the temp
#: name also carries keeps it out of a shell glob and an editor sidebar.
TEMP_SUFFIX = ".kbwrite-tmp"

#: The extra mark **this module's own** candidate temps carry, between the
#: target's name and the random part: ``.claim-quality.md.publish.<rand>``. It
#: exists so :func:`_sweep_orphan_temps` can name a class of file it is allowed
#: to delete. Every publish temp is created, proven and consumed inside its
#: writer's exclusion (see :func:`apply_edits`), so a publish temp found *under*
#: that exclusion cannot belong to a living writer — which is the whole of the
#: sweep's safety argument, and is not true of ``TEMP_SUFFIX`` in general:
#: ``ops._proven`` writes proof temps of the same suffix from inside a splice,
#: which runs before the lock is taken. The mark is what keeps the sweep off
#: them.
PUBLISH_MARK = "publish"

#: The mark a **proof** temp carries — the candidate a caller's splice writes
#: out to run the production parsers over before it returns it (``ops._proven``).
#: A proof temp is written at compose time, outside the publish exclusion, so
#: without a distinguishing mark there is no moment at which one can be told
#: from a living writer's, and a killed writer's proof temp is permanent
#: litter. Taking the target's directory around the proof
#: makes the same argument the publish mark rests on available to it: under that
#: lock, every proof temp of this target is an orphan.
PROOF_MARK = "proof"


# ---------------------------------------------------------------------------
# Outcomes
# ---------------------------------------------------------------------------


class Status(StrEnum):
    """The three answers a caller acts on differently.

    ``ops.py`` maps these to process exit codes — ``REFUSED`` to 7 and ``RETRY``
    to 8 — and that mapping is the only place an exit code appears. The three
    are kept distinct here because a caller that collapses *retry* into *refused*
    will re-ask a model for values that were already right.
    """

    WRITTEN = "written"
    REFUSED = "refused"
    RETRY = "retry"


class Reason(StrEnum):
    """The closed vocabulary of why a write did not happen.

    A refusal names its own identity: a check failing for the wrong reason is a
    failed check, so each member below is raised at exactly one point in the
    ladder. Report *wording* is ``ops.py``'s and the prompt engineer's; these
    tokens are the machine-readable half and are what tests assert against.
    """

    #: Step 2 — the value does not name a file inside ``kb-root/``:
    #: it escapes the root, it *is* the root, or it is not a path a filesystem
    #: can hold. Refused unread. One token rather than three because the
    #: caller's next act is the same for all three — correct the path value —
    #: and the detail sentence carries which of them it was.
    PATH_OUTSIDE_ROOT = "path-outside-root"
    #: There is no register file at this path (it does not exist, or the
    #: path names a directory) and creation cannot make one.
    REGISTER_ABSENT = "register-absent"
    #: Two edits in one batch name the same file; the second would clobber the
    #: first, since both splice from the same baseline.
    DUPLICATE_TARGET = "duplicate-target"
    #: The file did not decode as UTF-8 under a strict decode.
    NOT_UTF8 = "not-utf8"
    #: Step 4 — the register is already losing entries. Refused before
    #: anything is composed.
    CENSUS_MISMATCH = "census-mismatch"
    #: A splice primitive could not locate what it was asked to edit.
    SPLICE_FAILED = "splice-failed"
    #: Step 7 — the composed candidate does not read back as the intended
    #: record. This is the marker-above-heading class, caught on the temp.
    READBACK_MISMATCH = "readback-mismatch"
    #: Step 7 — the temp's own census fails, or its record count is not the
    #: before-count plus the intended delta.
    RECORD_COUNT = "record-count"
    #: Step 8 — the live file moved under us. The one *retry* reason.
    CONTENDED = "contended"


@dataclass(frozen=True)
class Outcome:
    """What a call to :func:`apply_edits` did, as data rather than as a report.

    ``subject`` is the offending field or the contended file — the identity a
    report line must name. ``detail`` is one sentence of machine-composed
    context with no status token, no tag and no ``restore:`` clause: those are
    ``ops.py``'s to add, because they need the invocation, which this
    module does not know.
    """

    status: Status
    written: tuple[str, ...] = ()
    reason: Reason | None = None
    subject: str | None = None
    detail: str = ""

    @property
    def ok(self) -> bool:
        return self.status is Status.WRITTEN


class SpliceError(Exception):
    """A splice primitive could not locate what it was asked to edit.

    Raised by :func:`replace_field_line`, and caught by :func:`apply_edits`,
    which turns it into a :data:`Reason.SPLICE_FAILED` refusal. It is an
    exception rather than a return value because a splice is a caller-supplied
    callable: there is no return channel through it that :func:`apply_edits`
    could read.
    """


class BatchInterrupted(OSError):
    """The replace loop failed partway: some targets are committed, some are not.

    An ``OSError`` by inheritance, deliberately, so a caller's existing
    environment handler catches it and the exit code stays the environment's
    (the three-code ladder is closed and this is not a verdict on the values —
    they were proven before the first replace). What it adds is the accounting
    that ``OSError`` cannot carry: :attr:`written` is exactly the set of targets
    that landed, and :attr:`failed` is the one that did not.

    The alternative — full multi-file atomicity — needs a journal this
    toolchain does not have, and cannot be had across directories anyway. So the
    all-or-nothing guarantee is
    per file and per *verdict*: no batch is half-refused, and a batch the
    environment interrupts says so, naming both halves. Reporting ``written=()``
    on a run that wrote is the one answer that is neither.
    """

    def __init__(self, *, written: tuple[str, ...], failed: str, cause: OSError) -> None:
        landed = ", ".join(written) if written else "nothing"
        super().__init__(
            f"{failed} could not be replaced ({cause}); this batch is interrupted, not refused — "
            f"already committed: {landed}"
        )
        self.written = written
        self.failed = failed


class _Refused(Exception):
    """Internal: a refusal raised anywhere in the ladder, carried to the top."""

    def __init__(self, reason: Reason, subject: str, detail: str) -> None:
        super().__init__(detail)
        self.reason = reason
        self.subject = subject
        self.detail = detail


class _Contended(_Refused):
    """Internal: the freshness check's violation. A separate type because it is
    not a refusal — the values were correct and nothing about them can fix it."""


# ---------------------------------------------------------------------------
# The readback comparison surface
# ---------------------------------------------------------------------------

#: Matched by the lookup rather than by a comparison: a record the parser does
#: not key under the intended id is a refusal before any other field is read.
MATCHED_BY_LOOKUP = ("id",)

#: Every ``ClaimEntry`` field the readback compares, mapped to the :class:`_Read`
#: attribute that carries it. **This is the map the comparison iterates**
#: (:func:`_mismatched_fields`), not a parallel declaration of it. A totality
#: guard certifying a tuple while a separate literal did the comparing would let
#: a field be added to ``ClaimEntry``, named compared, and never looked at —
#: with a green test asserting it was.
COMPARED_FIELD_READS: Mapping[str, str] = {
    "title": "title",
    "confidence": "rigor",
    "rationale": "rationale",
    "depends_on": "depends_on",
    "references": "references",
    "strengthen_by": "strengthen_by",
}

#: The one compared field that is not a ``ClaimEntry`` field at all: a ``sup-``
#: entry's staged ``supports:`` fan-out, read by a second production parser over
#: the same file (see :class:`ExpectedEntry`). Named here so the two totality
#: tests — over ``ClaimEntry`` and over :class:`_Read` — can both be exact.
COMPARED_NON_ENTRY_READS = ("supports",)

#: The ``ClaimEntry`` fields the readback accounts for, which is what the
#: partition test in ``test_kb_write_store.py`` reads.
COMPARED_FIELDS = MATCHED_BY_LOOKUP + tuple(COMPARED_FIELD_READS)

#: Every :class:`kb_index_lib.ExternalWork` field the readback compares, mapped
#: to the :class:`_Read` attribute that carries it — and, like the claim map
#: above, **the map the comparison is built through** (:func:`_work_read`).
#: Stated apart rather than folded into that one because a work is the simpler
#: record: three authored fields, no list among them, and ``strength`` is not a
#: local rigor but the standing of a paper nobody here wrote. Widening the
#: claim's map to admit it would describe one record with a vocabulary drawn
#: from another.
COMPARED_WORK_FIELD_READS: Mapping[str, str] = {
    "title": "title",
    "strength": "rigor",
    "rationale": "rationale",
}

#: The one ``ExternalWork`` field the readback accounts for without comparing
#: and without excluding: the citation key is a pure function of the id
#: :func:`_prove` looked the record up by, so a record whose key disagreed with
#: its id is not a state the parser can return.
WORK_ID_DERIVED_FIELDS = ("key",)

#: The ``ExternalWork`` fields the readback accounts for. The partition test
#: reads this the way it reads :data:`COMPARED_FIELDS`, so a field added to
#: that record lands in no group and fails rather than going unproven.
COMPARED_WORK_FIELDS = MATCHED_BY_LOOKUP + tuple(COMPARED_WORK_FIELD_READS)

#: Excluded from the comparison **by name**, because the temp is a sibling of
#: the target and the parser derives both from the parsed file's own location.
#: See the module docstring for why the alternative does not work.
PATH_DERIVED_FIELDS = ("canonical_path", "canonical_anchor")

#: Never supplied by this API and never compared. ``refresh`` owns them, and
#: :class:`ExpectedEntry` carries no field an op could supply one through.
DERIVED_FIELDS = ("solidity", "build_status", "solidity_trace")


@dataclass(frozen=True)
class ExpectedEdge:
    """One intended ``depends-on`` edge, as the op supplied it.

    ``applicability`` is a work-target edge's on-point fraction, ``None`` for
    the authored ``*pending*`` literal and for every other target kind, which
    carry no such quantity. It is compared like every other field: an op that
    rewrote an annotation and did not move it would otherwise read back clean.
    """

    target: str
    context: str | None = None
    applicability: float | None = None


def edge_of(edge) -> ExpectedEdge:
    """One parsed :class:`kb_index_lib.DependsOnEdge` as an expectation.

    The one translation from the reader's edge to this module's, so a caller
    restating a record it just parsed cannot drop the fraction off a work edge
    and expect a bullet it never intended.
    """
    return ExpectedEdge(
        target=edge.target,
        context=edge.context,
        applicability=None if edge.fraction is kb_index_lib.PENDING_FRACTION else edge.fraction,
    )


@dataclass(frozen=True)
class ExpectedEntry:
    """One record the op intends the written file to hold, as supplied values.

    ``rigor`` is the one concept behind the on-disk ``confidence:`` (claim) and
    ``quality:`` (support) fields — the comparison routes on the id's kind, so
    the caller never picks the field name and never gets it wrong. ``None`` is
    the authored ``*pending*`` literal.

    Prose fields are supplied here **uncollapsed**; the comparison collapses
    them with ``render.collapse_prose`` before matching, because that is what
    the renderer stored and what the parser returns.

    ``supports`` is a ``sup-`` entry's staged beneficiary fan-out, and it is the
    one compared field that does not come off a ``ClaimEntry``: the staging
    block is read by ``kb_index_lib.parse_register_staged_supports``, a second
    production reader over the same file. It is compared for the same reason
    every other field is — a block the renderer emitted and the reader could not
    find is precisely the silent structural loss the readback exists to make
    unrepresentable — and it is compared on **every** entry, so a claim
    expectation carrying pairs, or a support whose block went missing, both fail
    here. A fraction is ``None`` for the authored ``*pending*`` literal, the
    spelling used everywhere in this package.
    """

    node_id: str
    title: str
    rigor: float | None
    rationale: str
    depends_on: tuple[ExpectedEdge, ...] = ()
    references: tuple[ExpectedEdge, ...] = ()
    strengthen_by: tuple[str, ...] = ()
    supports: tuple[tuple[str, float | None], ...] = ()


@dataclass(frozen=True)
class Edit:
    """One file's worth of intent, prepared and proven before anything is written.

    ``splice`` takes the file's current text and returns the full candidate
    document. It is a callable rather than a rendered fragment plus a position
    because *where* an entry goes is a property of the document, not of the
    entry — and because that keeps this module's proof honest: the readback
    re-parses whatever the splice produced, with no knowledge of how it was
    produced.

    ``expect`` is a **sequence rather than a tuple**, and read only after the
    splice has run. That is what lets a caller derive its expectation from the
    baseline this module hands the splice instead of from a read of its own
    taken earlier: an expectation built from an earlier read describes a
    different generation of the file than the one being spliced, and a writer
    landing between the two would make a correct call report a *bad value*
    (see ``ops._expecting``). Nothing here fills it — the
    contract is only that this module does not look until the splice returns.

    ``claim_delta`` / ``support_delta`` / ``work_delta`` are the intended change
    in record count for each of the register's three node kinds. They are
    separate fields rather than one number because each kind has its own parser,
    and an insert of a support must not be allowed to satisfy a claim's count.
    """

    path: str
    splice: Callable[[str], str]
    expect: Sequence[ExpectedEntry] = ()
    claim_delta: int = 0
    support_delta: int = 0
    work_delta: int = 0
    create: bool = False


# ---------------------------------------------------------------------------
# The census
# ---------------------------------------------------------------------------

# The marker grammar is read out of the parser's own compiled patterns rather
# than re-spelled here — one reader per grammar. A census whose regex could
# disagree with the parser's would report a mismatch that does not exist, or
# miss the one that does — which is the entire defect this check exists to
# catch. Both the patterns and the walk that applies them are now
# `kb_index_lib.locate_register_entries`', which returns each marker's own kind;
# this module counts by that rather than by a pattern of its own.


@dataclass(frozen=True)
class Census:
    """One register's structural health: counts per node kind, and bindings.

    The count check is stated over ``parse_claim_quality_file``. It is taken
    per kind here because a register may host both, and the two kinds have
    two parsers: ``parse_claim_quality_file`` returns ``clm-`` records only, so
    counting *every* marker against it would fail every register that holds a
    support entry — a false refusal in place of a true check. Each kind is
    counted against its own parser and both halves must hold.

    ``misbound`` is the second half, and it is a different question from the
    counts: a count sees a marker that binds to *nothing*, and cannot see one
    that binds to the *wrong heading* — the same defect one heading earlier,
    with the counts left equal. A register whose first marker has no ``## ``
    heading above it at all is caught by the counts; one stray H2 anywhere above
    it and they balance again.
    """

    claim_markers: int = 0
    claim_records: int = 0
    support_markers: int = 0
    support_records: int = 0
    work_markers: int = 0
    work_records: int = 0
    misbound: tuple[str, ...] = ()

    @property
    def consistent(self) -> bool:
        """True when every canonical marker produced a record of its own."""
        return (
            self.claim_markers == self.claim_records
            and self.support_markers == self.support_records
            and self.work_markers == self.work_records
            and not self.misbound
        )

    def describe(self) -> str:
        counts = (
            f"claim markers {self.claim_markers} vs records {self.claim_records}; "
            f"support markers {self.support_markers} vs records {self.support_records}; "
            f"work markers {self.work_markers} vs records {self.work_records}"
        )
        if not self.misbound:
            return counts
        return f"{counts}; bound to a heading that is not their own: {', '.join(self.misbound)}"


def _scrubbed_lines(document: str) -> list[str]:
    """The document as the parsers see it: fenced lines blanked, kept in place.

    Blanking rather than removing is what makes an index computed here an index
    into the original document (see :class:`EntryLocation`), and
    ``str.splitlines`` is the parsers' own line grammar
    (``kb_index_lib.parse_claim_quality_file``), not a choice made here.
    """
    return kb_index_lib._strip_code_fences(document).splitlines()


def take_census(path: Path, kb_root: Path) -> Census:
    """Count canonical markers and parsed records in one register, and bind them.

    Both halves are asked of ``kb_index_lib.locate_register_entries`` — the
    reader's own walk, and now the only implementation of the marker-to-heading
    binding. Markers are therefore counted with the reader's patterns, over the reader's
    fence-scrubbed lines, at the reader's anchoring, and a marker inside a
    documentation fence is counted by neither side. The binding half is
    ``kb_index_lib.mis_bound_entries``, which the read-side gate asks of every
    register in the tree: a register ``verify`` calls mis-bound is one this
    module refuses a write into, because it is the same question asked once.
    """
    located = kb_index_lib.locate_register_entries(_read_text(path, subject=path.name))
    return Census(
        claim_markers=sum(1 for entry in located if entry.kind == "clm"),
        claim_records=len(kb_index_lib.parse_claim_quality_file(path, kb_root)),
        support_markers=sum(1 for entry in located if entry.kind == "sup"),
        support_records=len(kb_index_lib.parse_support_quality_entries(path, kb_root)),
        work_markers=sum(1 for entry in located if entry.kind == kb_schema.WORK_PREFIX),
        work_records=len(kb_index_lib.parse_work_entries(path, kb_root)),
        misbound=tuple(entry.node_id for entry in kb_index_lib.mis_bound_entries(located)),
    )


# ---------------------------------------------------------------------------
# Locate and splice
# ---------------------------------------------------------------------------

# Both entry kinds carry a `### Quality` section, so both are locatable by the
# same walk, which is why `locate_entries` serves refresh's solidity write-back
# as well as this module's splices.

# The frontmatter block delimiter, single-sourced in `kb_index_lib` — the same
# compiled pattern `verify_kb_metadata` reads with. The emitter and the checker
# must agree on where a document's frontmatter ends or this splice rewrites a
# span verify never read.
FRONTMATTER_BLOCK = kb_index_lib.FRONTMATTER_RE

# The reader's fold-break key lists, one per entry kind
# (`kb_index_lib.py:997` for a claim, `:1158` for a support). A field's span is
# the reader's to define: a replacement that deleted a shorter span than the
# reader folds would strand a continuation line for the next parse to absorb
# into the new value, which is the emitter/checker split in its most
# destructive form.
# The claim list is the reader's own, imported rather than retyped: a field
# added there and missed here is a bullet block the replacement stops short of.
_CLAIM_FOLD_BREAK_RE = re.compile(r"^- (" + "|".join(kb_index_lib.QUALITY_FIELD_KEYS) + "):")
_SUPPORT_FOLD_BREAK_RE = re.compile(r"^- (quality|solidity|rationale|depends-on|supports):")
# A work entry's two fields, and no more (`kb_index_lib.parse_work_entries`).
_WORK_FOLD_BREAK_RE = re.compile(r"^- (strength|rationale):")


def _fold_break_for(node_id: str) -> "re.Pattern[str]":
    """The reader's fold-break key list for the entry kind ``node_id`` names.

    One dispatch, two callers — this module's field replacement and ``ops``'
    depends-on bullet insert — so a kind added here reaches both.
    """
    if node_id.startswith(f"{kb_schema.WORK_PREFIX}-"):
        return _WORK_FOLD_BREAK_RE
    if node_id.startswith("sup-"):
        return _SUPPORT_FOLD_BREAK_RE
    return _CLAIM_FOLD_BREAK_RE


# The fields this primitive will replace: a scalar, and the one single-paragraph
# prose field. A list-valued field (`depends-on`, `strengthen-by`, `supports`)
# is refused by name — see `replace_field_line`.
_SINGLE_LINE_FIELDS = frozenset({"confidence", "quality", "strength"})
_FOLDED_FIELDS = frozenset({"rationale"})
_LIST_FIELDS = frozenset({"depends-on", "references", "strengthen-by", "supports"})


@dataclass(frozen=True)
class EntryLocation:
    """Where one register entry sits, in raw line indices over the document.

    ``quality_start`` is the line *after* the ``### Quality`` heading and
    ``quality_end`` is exclusive. Both are ``None`` for an entry with no
    ``### Quality`` section.

    Indices are raw: fence scrubbing blanks lines without removing them, so an
    index computed on scrubbed text is an index into the original.
    """

    node_id: str
    heading_line: int
    marker_line: int
    quality_start: int | None = None
    quality_end: int | None = None


def locate_entries(document: str) -> tuple[EntryLocation, ...]:
    """Locate every canonical entry in a register document, with its edit span.

    The binding is ``kb_index_lib.locate_register_entries``' — the reader's own
    walk and the only implementation of it. A second copy held equal to it by a
    parity test is a grammar with two implementations and a test between them,
    which is drift one green suite away from happening.

    What is added here, and belongs here, is the **span**: ``quality_end``, the
    exclusive end of the entry's ``### Quality`` section. A reader has no use
    for it — it stops at what it parses — and a splice cannot work without it,
    because it is the bound on where a field may be inserted or replaced.

    Only BOUND markers are returned. An unbound one yields no record from either
    production parser, so there is nothing for a splice to edit; the census is
    what reports its existence (see :func:`take_census`).

    Entries are returned in document order.
    """
    lines = _scrubbed_lines(document)
    located = []
    for entry in kb_index_lib.locate_register_entries(document):
        if entry.heading_line is None:
            continue
        located.append(
            EntryLocation(
                node_id=entry.node_id,
                heading_line=entry.heading_line,
                marker_line=entry.marker_line,
                quality_start=None if entry.quality_line is None else entry.quality_line + 1,
                quality_end=None if entry.quality_line is None else _quality_end(lines, entry.quality_line + 1),
            )
        )
    return tuple(located)


def _quality_end(lines: Sequence[str], start: int) -> int:
    """Where an entry's ``### Quality`` section stops: the next ``## ``, or EOF.

    The next H2 is a sibling entry's title. An H3 ``### Quality`` heading does
    not start with ``## ``, so a nested heading inside the section does not end
    it.
    """
    for j in range(start, len(lines)):
        if lines[j].startswith("## "):
            return j
    return len(lines)


def _line_text(line: str) -> str:
    """A ``keepends`` line without its terminator.

    Derived from ``str.splitlines`` rather than from a list of break characters
    this module would then own a copy of: whatever the reader's grammar breaks
    on, this strips — and only that.
    """
    body = line.splitlines()
    return body[0] if body else ""


def _terminator(line: str) -> str:
    """The line-break bytes a ``keepends`` line carries, or ``""`` at EOF."""
    return line[len(_line_text(line)) :]


def _prevailing_terminator(parts: Sequence[str], start: int) -> str:
    """The terminator a line spliced in at ``start`` should carry.

    The displaced line's own, so an edit inside a CRLF document stays CRLF;
    failing that (an append at the end) the nearest terminated line above it;
    failing that (an empty document, or one unterminated line) ``"\\n"``.
    """
    if start < len(parts) and (term := _terminator(parts[start])):
        return term
    for line in reversed(parts[:start]):
        if term := _terminator(line):
            return term
    return "\n"


def splice_lines(document: str, *, start: int, end: int, lines: Sequence[str]) -> str:
    """Replace the raw line range ``[start, end)`` with ``lines``, byte-exactly elsewhere.

    **The line-editing primitive of this package**, and public so that every
    splice — this module's and the ones ``ops`` composes — edits a document the
    same way. A splice that rebuilds its document as
    ``"\\n".join(document.splitlines())`` rewrites *every* line terminator in the
    file, including the author's body prose, which is outside this API entirely
    — this API owns the metadata in the file, never the prose. Measured without
    this primitive: one ``set-rigor`` over a CRLF register deleted 123 CR bytes,
    and one ``set-frontmatter`` over a CRLF leaf turned its form feed, its
    U+2028 and its U+0085 into newlines — a body of 12 physical lines came back
    as 21. Neither the readback nor the freshness check can see it: the parsers
    normalize whitespace, so the readback compares equal, and the baseline was
    read before the splice ran.

    Indices are the **reader's**: they come from :func:`locate_entries`, which
    counts lines the way the production parsers do (``str.splitlines``, which
    breaks on ``\\r\\n``, ``\\r``, ``\\x0b``, ``\\x0c``, ``\\x1c``–``\\x1e``,
    ``\\x85``, U+2028 and U+2029). The cut is made with
    ``splitlines(keepends=True)``, whose split points are the same ones and
    whose parts concatenate back to the exact original — so an index means the
    same line to the reader and to this splice, and every line the call does not
    name keeps its own bytes.

    Each replacement line is supplied **without** a terminator and is emitted
    with the one it displaces. A document that does not end in a newline does
    not gain one, and a line appended past the end of one gets the break its
    predecessor was missing.
    """
    parts = document.splitlines(keepends=True)
    if not 0 <= start <= end <= len(parts):
        raise ValueError(f"line range [{start}, {end}) is not inside a document of {len(parts)} lines")

    eol = _prevailing_terminator(parts, start)
    head = list(parts[:start])
    tail = parts[end:]
    emitted = [line + eol for line in lines]
    # An append past an unterminated last line: it needs the break it never had,
    # or the first emitted line would be glued onto it.
    if head and not _terminator(head[-1]):
        head[-1] += eol
    # ...and if the replaced range ran to an unterminated end of file, the line
    # that now ends it must not carry one either.
    if not tail and emitted and parts and not _terminator(parts[-1]):
        emitted[-1] = emitted[-1][: -len(eol)]
    return "".join([*head, *emitted, *tail])


def insert_entry(document: str, entry: str) -> str:
    """Append a rendered entry to a register, below every entry already there.

    Position is the whole question a splice answers, and appending is the only
    position that cannot disturb an existing entry: no line above the insertion
    point moves, so no derived value refresh has already computed is touched,
    and no other entry's ``### Quality`` span changes shape.

    The ``---`` rule between entries is the corpus's convention and is emitted
    unless the document already ends with one, in which case that rule becomes
    this entry's separator rather than a second one being added. A document with
    no content yet — the creation case — receives the entry alone.

    Everything above the insertion point keeps its bytes, and the entry is
    emitted in the register's own line terminator (see :func:`splice_lines`):
    appending to a CRLF register with ``"\\n"`` would leave one document in two
    conventions. The trailing blank lines the append lands on are the one thing
    it rewrites, and it always ends the file with a newline.
    """
    parts = document.splitlines(keepends=True)
    while parts and not parts[-1].strip():
        parts.pop()
    if not parts:
        return entry + "\n"

    eol = _prevailing_terminator(parts, len(parts) - 1)
    last = _line_text(parts[-1])
    kept = "".join(parts[:-1]) + last + eol
    rule = "" if last.strip() == "---" else f"{eol}---{eol}"
    return f"{kept}{rule}{eol}{eol.join(entry.splitlines())}{eol}"


def replace_or_insert_frontmatter_field(
    document: str,
    *,
    field: str,
    ids: Sequence[str],
    anchor_prefix: str,
) -> str:
    """Replace ``field: [...]`` in the frontmatter block, or insert it.

    The frontmatter splice has one implementation, and this is it. The block's own
    delimiter lines are **not re-emitted at all** — the edit is made over the
    span the block pattern captures, so the opener and closer keep whatever
    spelling and terminators the document had, and no composer of them is needed
    here. Re-emitting them canonicalizes an opener written
    ``<!--kb-frontmatter`` and puts LF inside a CRLF document's block.

    When the field is absent it is inserted immediately after the line whose
    stripped form starts with ``anchor_prefix`` (``subtree-claims:`` for
    ``subtree-experiments``, ``kind:`` for ``subtree-claims``), preserving the
    documented field order. It falls back to the top of the block when no anchor
    line is present — the shape a block gains its first derived field in.

    An existing value spanning several physical lines — the wrapped ``[a,``/``
    b]`` and YAML-block ``- a``/``- b`` shapes the reader accepts — is replaced
    WHOLE, over the span :func:`kb_index_lib.frontmatter_field_end` defines.
    Rewriting only the first line would strand the continuations as orphaned
    text that the next parse folds into whatever field followed: accepting a
    shape on read and mangling it on write is the emitter/checker split in its
    most destructive form.

    A document with no frontmatter block is returned unchanged — the regex finds
    nothing to substitute. That is the caller's condition to check, not a
    refusal: this primitive is driven over a node set that was enumerated *by*
    its parsed frontmatter.
    """
    match = FRONTMATTER_BLOCK.search(document)
    if match is None:
        return document

    new_line = render.render_id_list_field(field, tuple(ids))
    # Two views of the captured block: the text of each line, for the decisions,
    # and the line with its own terminator, for everything kept.
    lines = match.group(1).splitlines()
    parts = match.group(1).splitlines(keepends=True)
    eol = _prevailing_terminator(parts, 0)

    out: list[str] = []
    replaced = False
    i = 0
    while i < len(lines):
        if lines[i].strip().startswith(f"{field}:"):
            indent = lines[i][: len(lines[i]) - len(lines[i].lstrip())]
            out.append(f"{indent}{new_line}{eol}")
            replaced = True
            # Drop the field's continuation lines, if any — the span comes from
            # the library rule the reader uses, so writer and reader cannot
            # disagree about where this field ends.
            i = kb_index_lib.frontmatter_field_end(lines, i)
            continue
        out.append(parts[i])
        i += 1

    if not replaced:
        out = []
        inserted = False
        for text, part in zip(lines, parts):
            out.append(part)
            if not inserted and text.strip().startswith(anchor_prefix):
                out.append(f"{new_line}{eol}")
                inserted = True
        if not inserted:
            out.insert(0, f"{new_line}{eol}")

    # The captured block's *last* line carries no terminator — the pattern
    # anchors on the closer's line feed — so a line that was last and no longer
    # is has to be given one, or it and its new neighbour become one line. (That
    # is the whole of this fix: inserting `subtree-experiments:` after a
    # `subtree-claims:` that ended the block produced a single glued line, which
    # every reader then parsed as one field holding the other's value.)
    for index in range(len(out) - 1):
        if not _terminator(out[index]):
            out[index] += eol
    # ...and the line that ends it now must not carry one. Only the `\n` is
    # dropped: in a CRLF document the pattern leaves the `\r` inside the
    # capture, so stripping the terminator whole would convert that one line to
    # LF — the rewrite this function was changed to stop.
    if out and out[-1].endswith("\n"):
        out[-1] = out[-1][:-1]
    return document[: match.start(1)] + "".join(out) + document[match.end(1) :]


def replace_field_line(document: str, *, node_id: str, field_name: str, line: str) -> str:
    """Replace one authored field line inside one entry's ``### Quality`` section.

    The rewritten span is the **reader's**: a scalar field is one physical line,
    and ``- rationale:`` runs until the reader's fold breaks — a blank line or a
    line beginning with another key from that entry kind's list. Nothing outside
    that span moves, which is what leaves every derived line in the entry
    byte-untouched: rewriting a whole ``### Quality`` block would reset
    refresh-computed solidity values to their placeholder, which refresh then
    reports as drift and verify fails on. *Byte*-untouched is meant literally —
    the edit is made through :func:`splice_lines`, so the rest of the document,
    body prose included, is not even re-terminated.

    A list-valued field (``depends-on``, ``strengthen-by``, ``supports``) is
    **refused by name**. Replacing such a block whole would rewrite the
    ``(solidity …)`` annotation on every bullet in it — derived values this API
    never computes — so adding one member is an insert of a single bullet, a
    different primitive with a different proof obligation, and one built over
    ``refresh``'s own line builders.

    Raises :class:`SpliceError` when the entry, its Quality section, or the field
    is absent: a field this API is asked to update was written by this API, so
    its absence is a defect in the file rather than a case to insert into.
    """
    if field_name in _LIST_FIELDS:
        raise SpliceError(
            f"{field_name!r} is a list-valued field; replacing it whole would rewrite the derived "
            f"(solidity …) annotation on every bullet in it"
        )
    if field_name not in _SINGLE_LINE_FIELDS | _FOLDED_FIELDS:
        raise SpliceError(f"{field_name!r} is outside this primitive's field vocabulary")

    entry = next((e for e in locate_entries(document) if e.node_id == node_id), None)
    if entry is None:
        raise SpliceError(f"no canonical entry for {node_id} in this register")
    if entry.quality_start is None or entry.quality_end is None:
        raise SpliceError(f"{node_id} has no ### Quality section")

    lines = document.splitlines()
    key = f"- {field_name}:"
    for i in range(entry.quality_start, min(entry.quality_end, len(lines))):
        if not lines[i].strip().startswith(key):
            continue
        indent = lines[i][: len(lines[i]) - len(lines[i].lstrip())]
        end = _field_span_end(lines, i, entry.quality_end, node_id=node_id, field_name=field_name)
        return splice_lines(document, start=i, end=end, lines=[indent + line])
    raise SpliceError(f"{node_id} has no - {field_name}: line to replace")


def _field_span_end(lines: list[str], start: int, limit: int, *, node_id: str, field_name: str) -> int:
    """One past the last physical line the reader folds into this field."""
    if field_name in _SINGLE_LINE_FIELDS:
        return start + 1
    break_re = _fold_break_for(node_id)
    i = start + 1
    while i < limit and i < len(lines):
        stripped = lines[i].strip()
        if not stripped or break_re.match(stripped):
            break
        i += 1
    return i


# ---------------------------------------------------------------------------
# Path containment and text I/O
# ---------------------------------------------------------------------------


def resolve_target(kb_root: Path, rel_path: str) -> Path:
    """Resolve a kb-root-relative path, or refuse it unread and unwritten.

    Symlinks are resolved **before** the containment test, so a link inside
    ``kb-root/`` pointing out of it is refused rather than followed. The check
    is on the hot path of every write and costs one ``resolve()``: cheap enough
    to always run is the property that matters.

    Three refusals, and the order is the point: the two degenerate cases are
    answered **before** ``resolve()`` and before ``is_relative_to``, because
    neither of those two answers them.

    * A **NUL** anywhere in the value: ``Path.resolve`` raises ``ValueError``
      on one, which leaves the op with an unhandled traceback and rc 1 —
      outside the exit-code ladder entirely — for a value the caller supplied.
    * The **root itself** (``"."``, ``"common/.."``, ``""``): ``is_relative_to``
      is reflexive, so the root passed containment, and the temp then went to
      ``target.parent`` — the KB's *parent directory*, outside ``kb-root/``.
      The target must resolve inside the root or be refused unread and
      unwritten; a file written outside it breaks that even though the run then
      dies before publishing anything.
    """
    if "\x00" in rel_path:
        # Shown escaped: a report line is text, and a NUL in one is a byte the
        # terminal, the log and the ledger each answer differently.
        raise _Refused(
            Reason.PATH_OUTSIDE_ROOT,
            rel_path.replace("\x00", "\\x00"),
            "contains a NUL byte, which no path can hold",
        )

    root = Path(kb_root).resolve()
    target = (root / rel_path).resolve()
    if target == root:
        raise _Refused(
            Reason.PATH_OUTSIDE_ROOT,
            str(rel_path),
            f"resolves to the kb root {root} itself, and a target must be a file inside it",
        )
    if not target.is_relative_to(root):
        raise _Refused(
            Reason.PATH_OUTSIDE_ROOT,
            str(rel_path),
            f"resolves to {target} which is outside {root}",
        )
    return target


def _read_text(path: Path, *, subject: str) -> str:
    """Read a text file as UTF-8, strictly.

    ``newline=""`` disables newline translation, so the string this returns is
    the file's exact content: on a strict decode, string equality is byte
    equality (UTF-8 decoding is injective over the sequences ``strict``
    accepts), which is what lets the freshness check be stated over text. A
    decode failure is refused rather than ``errors="replace"``d — replacing an
    undecodable byte would put a substitution character into a file this API
    then claims to have proven.
    """
    try:
        with path.open("r", encoding="utf-8", newline="") as handle:
            return handle.read()
    except UnicodeDecodeError as exc:
        raise _Refused(Reason.NOT_UTF8, subject, f"is not valid UTF-8: {exc}") from exc


def _still_matches(path: Path, baseline: str | None) -> bool:
    """The freshness question: is the live file still exactly what step 4 read?

    Called only from inside :func:`_exclusive`'s critical section, and it reads
    the file itself rather than being handed a value read earlier: the answer is
    about the live bytes *now*, and it is load-bearing only because no other
    writer can change them until the replace has happened.

    ``baseline`` is ``None`` for the creation case, where "unchanged" means the
    file still does not exist. A file that has stopped decoding as UTF-8 since
    step 4 has by definition changed, so it answers false rather than raising:
    at step 8 the answer to any change is the same one.
    """
    try:
        with path.open("r", encoding="utf-8", newline="") as handle:
            return baseline is not None and handle.read() == baseline
    except FileNotFoundError:
        return baseline is None
    except UnicodeDecodeError:
        return False


def _temp_prefix(target: Path, mark: str = PUBLISH_MARK) -> str:
    """The name every temp of ``target`` carrying ``mark`` starts with."""
    return f".{target.name}.{mark}."


def _sweep_orphan_temps(target: Path) -> None:
    """Unlink marked temps left beside ``target`` by a writer that was killed.

    Called with ``target``'s directory held, and only then. Under that
    exclusion every marked temp of this target is an orphan by construction:
    the writer that made one holds the lock from before it exists until after it
    is consumed, so a living writer's temp is never visible to this scan. That
    is true of a :data:`PUBLISH_MARK` temp because :func:`apply_edits` makes it
    under the lock, and of a :data:`PROOF_MARK` temp because :func:`proof_temp`
    does — and it is *not* true of a temp carrying neither mark, which is why
    the sweep is pinned to the two it names rather than to
    :data:`TEMP_SUFFIX` at large.

    **Correctness never depends on cleanup running at death** — all-or-nothing
    is positional, and a kill leaves each file holding either its prior content or
    a proven candidate whatever becomes of the temps. This is litter control,
    and it survives ``SIGKILL`` because the *next* writer does it rather than
    the dying one: no signal handler, no reaper daemon, nothing to be killed
    before it runs.

    The match is pinned at both ends — one of this target's marked prefixes and
    :data:`TEMP_SUFFIX` — so it is a class of file this module names and makes,
    never a neighbouring document that happens to share a directory. It does not
    reach past the locked target: another register's writer may be live and is
    not excluded by this lock.
    """
    prefixes = (_temp_prefix(target, PUBLISH_MARK), _temp_prefix(target, PROOF_MARK))
    for path in target.parent.iterdir():
        if path.name.startswith(prefixes) and path.name.endswith(TEMP_SUFFIX):
            path.unlink(missing_ok=True)


def _default_file_mode() -> int:
    """The mode an ordinary file create would get under the process umask.

    ``os.umask`` has no read-only form — the only way to read it is to set it,
    which is why this sets ``0`` and restores the prior value immediately
    rather than leaving any window open. ``0o666`` is the mode ``open()``
    itself requests for a new file before the umask narrows it, so masking it
    here reproduces exactly what a normal create in this process would have
    produced: ``022`` yields ``0o644``, ``002`` yields ``0o664``.
    """
    current = os.umask(0)
    os.umask(current)
    return 0o666 & ~current


def _target_mode(target: Path) -> int:
    """The mode this write's temp must carry before it can be proven.

    A pre-existing target's write replaces its *contents*; the mode is not
    this API's to change, so the temp inherits it — ``os.replace`` then
    carries it onto the target unchanged, which is the point. A target that
    does not exist yet is this write's creation, and gets what an ordinary
    create honours: the process umask, via :func:`_default_file_mode`, never a
    hard-coded literal.

    Read here, immediately before the temp is written under the target's
    directory lock, rather than during the earlier unlocked compose: like the
    target's content, its mode could move between the two phases, and this is
    the read closest to the replace that publishes it. A mode that changes
    after this read and before the replace is the same residual race the
    freshness check already accepts for content on the creation path — the
    check's answer is content-only — and is no wider than it.
    """
    try:
        return stat.S_IMODE(target.stat().st_mode)
    except FileNotFoundError:
        return _default_file_mode()


def _write_temp(target: Path, text: str, *, mark: str = PUBLISH_MARK, mode: int | None = None) -> Path:
    """Write ``text`` to a fresh temp beside ``target`` and return its path.

    Beside, because ``os.replace`` is only atomic within one filesystem, and a
    sibling is the one placement guaranteed to be on the target's. The content
    is flushed and fsynced before the handle closes, so the bytes the replace
    publishes are the bytes that were proven even if the machine stops between
    the two.

    ``mark`` says which kind of temp this is — a candidate about to be published
    or a candidate about to be parsed — and is what :func:`_sweep_orphan_temps`
    reads. ``newline=""`` for the same reason :func:`_read_text` uses it: with
    translation on, the bytes proven and the bytes published are not the same
    bytes on any document that carries a ``\\r``.

    ``mode``, when given, is applied with ``chmod`` before this returns:
    ``tempfile.mkstemp`` always makes its file ``0600`` regardless of it, and
    ``os.replace`` would otherwise carry that ``0600`` onto whatever this temp
    publishes over (:func:`_target_mode`). Left ``None`` for a proof temp,
    which is unlinked after it is parsed and never replaces anything, so its
    mode has no reader to matter to.
    """
    handle_fd, name = tempfile.mkstemp(dir=target.parent, prefix=_temp_prefix(target, mark), suffix=TEMP_SUFFIX)
    with os.fdopen(handle_fd, "w", encoding="utf-8", newline="") as handle:
        handle.write(text)
        handle.flush()
        os.fsync(handle.fileno())
    if mode is not None:
        os.chmod(name, mode)
    return Path(name)


# ---------------------------------------------------------------------------
# The readback
# ---------------------------------------------------------------------------

# `kb_index_lib._parse_depends_on_line` normalizes a framework axiom token to
# `axiom-N` on the way into an edge, so an expectation stated in the authored
# spelling has to be normalized the same way or every axiom dependency reads
# back as a mismatch. Mirrored rather than imported: the parser applies it
# inline against its own scan.
_AXIOM_TOKEN_RE = re.compile(r"^Axiom (\d+)$")


def _normalize_target(token: str) -> str:
    match = _AXIOM_TOKEN_RE.match(token.strip())
    return f"axiom-{match.group(1)}" if match else token.strip()


@dataclass(frozen=True)
class _Read:
    """One parsed record, reduced to the fields the readback compares.

    A claim record and a support record are two shapes on disk — a
    :class:`kb_index_lib.ClaimEntry` and a dict — and one question here, so both
    are normalized into this before the comparison rather than the comparison
    being written twice.
    """

    title: str
    rigor: float | None
    rationale: str
    depends_on: tuple[tuple[str, str | None, float | None], ...]
    references: tuple[tuple[str, str | None, float | None], ...]
    strengthen_by: tuple[str, ...]
    supports: tuple[tuple[str, float | None], ...]


def _edges(edges) -> tuple[tuple[str, str | None, float | None], ...]:
    return tuple((edge.target, edge.context, edge_of(edge).applicability) for edge in edges)


def _staged(pairs) -> tuple[tuple[str, float | None], ...]:
    """A staging read's pairs in the spelling an expectation is stated in.

    The reader returns ``kb_index_lib.PENDING_FRACTION`` for the authored
    ``*pending*`` literal; every value carrier in this package spells that
    ``None``. Normalizing here rather than at each caller keeps one direction of
    translation and one place it could be got wrong.
    """
    return tuple(
        (claim_id, None if fraction is kb_index_lib.PENDING_FRACTION else fraction) for claim_id, fraction in pairs
    )


def _work_read(work: kb_index_lib.ExternalWork) -> _Read:
    """One parsed external work as a comparable record.

    Built through :data:`COMPARED_WORK_FIELD_READS` rather than field by field,
    so "declared compared" and "compared" are one fact for this record as they
    are for a claim's. The three list-valued reads are empty because a work
    carries no such field — the node is terminal and no leaf hosts it — which
    makes an expectation stating edges, open-work items or a staged fan-out
    against one a mismatch rather than an ignored value.
    """
    return _Read(
        **{read: getattr(work, field) for field, read in COMPARED_WORK_FIELD_READS.items()},
        depends_on=(),
        references=(),
        strengthen_by=(),
        supports=(),
    )


def _expected_edges(edges: tuple[ExpectedEdge, ...]) -> tuple[tuple[str, str | None, float | None], ...]:
    return tuple(
        (
            _normalize_target(edge.target),
            render.collapse_prose(edge.context) if edge.context else None,
            edge.applicability,
        )
        for edge in edges
    )


def _expected_read(expected: ExpectedEntry) -> _Read:
    return _Read(
        title=render.collapse_prose(expected.title),
        rigor=expected.rigor,
        rationale=render.collapse_prose(expected.rationale),
        depends_on=_expected_edges(expected.depends_on),
        references=_expected_edges(expected.references),
        strengthen_by=tuple(render.collapse_prose(item) for item in expected.strengthen_by),
        supports=tuple(expected.supports),
    )


def _mismatched_fields(expected: ExpectedEntry, actual: _Read) -> list[str]:
    """The names of the fields on which the record read back differs.

    Iterates :data:`COMPARED_FIELD_READS` — the same object the totality test
    partitions ``ClaimEntry`` against — so "declared compared" and "compared"
    are one fact.
    """
    want = _expected_read(expected)
    return [
        name
        for name in (*COMPARED_FIELD_READS.values(), *COMPARED_NON_ENTRY_READS)
        if getattr(want, name) != getattr(actual, name)
    ]


def _prove(temp: Path, kb_root: Path, edit: Edit, before: Census, *, subject: str) -> None:
    """Step 7 — prove the temp, or refuse. The live file is untouched here."""
    after = take_census(temp, kb_root)
    if not after.consistent:
        raise _Refused(
            Reason.RECORD_COUNT,
            subject,
            f"the composed candidate loses a record, or binds one to the wrong heading: {after.describe()}",
        )
    for kind, want, got in (
        ("claim", before.claim_records + edit.claim_delta, after.claim_records),
        ("support", before.support_records + edit.support_delta, after.support_records),
        ("work", before.work_records + edit.work_delta, after.work_records),
    ):
        if want != got:
            raise _Refused(
                Reason.RECORD_COUNT,
                subject,
                f"{kind} record count is {got}, expected {want} (before plus the intended delta)",
            )

    claims = {record.id: record for record in kb_index_lib.parse_claim_quality_file(temp, kb_root)}
    supports = kb_index_lib.parse_support_quality_entries(temp, kb_root)
    # A support entry's two halves are parsed by two production readers — the
    # entry's own fields by the one above, its staged fan-out by this one — so
    # both are taken over the same candidate before either is compared.
    staged = kb_index_lib.parse_register_staged_supports(temp)
    works = {work.id: work for work in kb_index_lib.parse_work_entries(temp, kb_root)}

    for expected in edit.expect:
        if expected.node_id.startswith(f"{kb_schema.WORK_PREFIX}-"):
            work = works.get(expected.node_id)
            actual = None if work is None else _work_read(work)
        elif expected.node_id.startswith("sup-"):
            raw = supports.get(expected.node_id)
            actual = (
                None
                if raw is None
                else _Read(
                    title=raw["title"],
                    rigor=raw["quality"],
                    rationale=raw["rationale"],
                    depends_on=_edges(raw["depends_on"]),
                    # A support entry has no `- references:` field: a reference
                    # is one claim of this corpus naming another. An expectation
                    # carrying one on a support is a mismatch, not a drop.
                    references=(),
                    strengthen_by=(),
                    supports=_staged(staged.get(expected.node_id, ())),
                )
            )
        else:
            record = claims.get(expected.node_id)
            actual = (
                None
                if record is None
                else _Read(
                    title=record.title,
                    rigor=record.confidence,
                    rationale=record.rationale,
                    depends_on=_edges(record.depends_on),
                    references=_edges(record.references),
                    strengthen_by=tuple(item.text for item in record.strengthen_by),
                    # A claim entry has no staging home; an expectation carrying
                    # pairs on one is a mismatch rather than an ignored value.
                    supports=(),
                )
            )
        if actual is None:
            raise _Refused(
                Reason.READBACK_MISMATCH,
                f"{subject}:{expected.node_id}",
                "the production parser returns no record for this id in the composed candidate",
            )
        wrong = _mismatched_fields(expected, actual)
        if wrong:
            raise _Refused(
                Reason.READBACK_MISMATCH,
                f"{subject}:{expected.node_id}",
                f"read back with a different {', '.join(wrong)} than the values supplied",
            )


# ---------------------------------------------------------------------------
# The write lock
# ---------------------------------------------------------------------------

#: How long a writer waits for another writer's critical section before giving
#: up and answering *retry*. It bounds a wait on one temp write, one
#: proof and one rename — milliseconds of held time, and bounded by the size of
#: one register — so this is not a timeout anyone should reach by working: it is
#: the bound that keeps a wedged or stopped peer from turning a write into a
#: hang. Exhausting it is CONTENDED, the same answer
#: a moved file gets, because it is the same answer to the caller: the values
#: were right, nothing was written, re-run unchanged. **No new outcome code**
#: — the ladder is closed.
LOCK_TIMEOUT_SECONDS = 5.0

#: The retry interval while waiting. ``flock`` is taken non-blocking and polled
#: rather than taken blocking, because a blocking ``flock`` cannot be given a
#: deadline without a signal, and an alarm in a library is a side effect on a
#: process this module does not own.
_LOCK_POLL_SECONDS = 0.005


@contextlib.contextmanager
def _exclusive(directory: Path, *, subject: str) -> Iterator[None]:
    """Hold an exclusive ``flock`` on ``directory``'s own descriptor.

    **The lock is on the directory, not on the target and not on a sidecar
    file.** The target is the wrong object twice over: ``os.replace`` swaps a
    new inode into the name, so a writer holding a lock on the old inode
    excludes nobody the moment it publishes, and in the creation case there
    is no target yet to open. A lock file beside the target has the same defect
    in its cleanup — unlinking it lets the next writer create and lock a
    *different* inode under the same name while the current holder still holds
    the old one — and not unlinking it leaves a stray artifact inside a
    git-tracked KB. The directory entry is the one object that already exists
    before a creation, survives every replace, and needs nothing cleaned up.
    Its cost is granularity: two writers to two different files in one directory
    serialize. The section they serialize on is a re-read and a rename, so that
    cost is paid in microseconds and buys a lock with no lifecycle.

    ``O_RDONLY`` is enough — ``flock`` asks for a descriptor, not for write
    access — and the descriptor is closed on every path out, which releases the
    lock even if the explicit unlock is skipped.

    Raises :class:`_Contended` when the wait exhausts
    :data:`LOCK_TIMEOUT_SECONDS`. Any other ``OSError`` propagates: a filesystem
    that cannot ``flock`` is the environment being unfit, which is a different
    exit code and ``ops.py``'s to map — never a silent write without the
    exclusion.
    """
    fd = os.open(directory, os.O_RDONLY)
    try:
        deadline = time.monotonic() + LOCK_TIMEOUT_SECONDS
        while True:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if time.monotonic() >= deadline:
                    raise _Contended(
                        Reason.CONTENDED,
                        subject,
                        f"is held by another writer that did not finish within {LOCK_TIMEOUT_SECONDS:g}s",
                    ) from None
                time.sleep(_LOCK_POLL_SECONDS)
        try:
            yield
        finally:
            fcntl.flock(fd, fcntl.LOCK_UN)
    finally:
        os.close(fd)


@contextlib.contextmanager
def proof_temp(target: Path, text: str) -> Iterator[Path]:
    """A candidate on disk beside ``target``, for a caller to run parsers over.

    The seam for a splice that must **parse** what it composed before returning
    it — ``ops._proven``'s leaf readback, which needs a real path because the
    production parsers derive a node's identity from the file's own
    ``relative_to(kb_root)``. Written here rather than by each caller for two
    reasons, and the second is the whole of it:

    * one temp writer, so a proof runs over the bytes a publish would write —
      ``newline=""``, strict UTF-8, fsynced (review note N3: a proof temp
      written with newline translation on is a proof about different bytes);
    * the target's directory is **held** for the life of the temp, which is what
      makes a proof temp sweepable. Under that lock every marked temp beside the
      target belongs to a process that died holding it, so the next writer takes
      it out (:func:`_sweep_orphan_temps`). Without the lock there is no moment
      at which a proof temp can be told from a living writer's, and a killed
      writer's is permanent litter — up to 30 files of 80 kB in one measured
      round.

    **Call this from outside the exclusion**, which is where a splice runs:
    ``apply_edits`` composes every edit before it takes any lock, deliberately
    (see its docstring). A caller that took this while already holding the
    target's directory would block on its own ``flock`` — it belongs to the open
    file description, not to the process — and answer *retry* after the timeout.

    Contention here is :class:`_Contended`, which :func:`apply_edits` turns into
    the same *retry* a moved file gets: a proof that could not start because
    somebody else is publishing is a write to re-run unchanged, never a verdict
    on the values.
    """
    with _exclusive(target.parent, subject=target.name):
        _sweep_orphan_temps(target)
        temp = _write_temp(target, text, mark=PROOF_MARK)
        try:
            yield temp
        finally:
            temp.unlink(missing_ok=True)


# ---------------------------------------------------------------------------
# The write path
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class _Composed:
    """One edit, contained, censused and spliced — nothing written yet.

    The product of the unlocked phase. Everything here is a read of the live
    file or a pure computation over what was read, which is why it needs no
    exclusion: it touches no file any other writer can see.
    """

    edit: Edit
    target: Path
    subject: str
    baseline: str | None
    before: Census
    candidate: str


@dataclass(frozen=True)
class _Prepared:
    """One composed edit, proven on a temp and waiting for the freshness check."""

    target: Path
    temp: Path
    baseline: str | None
    subject: str


def _lock_sites(composed: Sequence[_Composed]) -> list[tuple[Path, str]]:
    """The directories the locked phase must hold, deduplicated and in a fixed order.

    **Deduplicated**, because a ``flock`` belongs to an open file description
    rather than to a process: a second ``os.open`` of a directory this process
    already holds conflicts with itself, and a batch touching two files in one
    directory would deadlock against its own first lock.

    **Sorted**, because two batches whose target sets overlap must take the
    shared directories in the same order or each can hold what the other waits
    for. A total order over the paths is the whole of the deadlock argument, and
    it costs one ``sorted``.

    The subject carried alongside each directory is the first edit that named
    it — the identity a report line gets if the wait for that directory runs
    out.
    """
    sites: dict[Path, str] = {}
    for item in composed:
        sites.setdefault(item.target.parent, item.subject)
    return sorted(sites.items())


def _compose(edit: Edit, *, root: Path, claimed: set[str]) -> _Composed:
    """The unlocked phase for one edit: steps 2, 4 and 5.

    Reads the live file, censuses it, and runs the caller's splice over what it
    read. Refuses — before any exclusion is taken and before anything is
    written — on an uncontained path, a duplicate target, a bad decode, an
    inconsistent census, an absent register, or a splice that could not find its
    site. A refusal here is *unread and unwritten*, which is also
    why nothing is swept on this path: an op that refuses before it locks
    anything has no business deleting anything.
    """
    target = resolve_target(root, edit.path)
    subject = target.relative_to(root).as_posix()
    if subject in claimed:
        raise _Refused(
            Reason.DUPLICATE_TARGET,
            subject,
            "is named twice in one batch; both edits would splice from the same baseline",
        )
    claimed.add(subject)

    # Step 4's read is the freshness check's anchor as well as the census's
    # input. It is taken here, outside the exclusion, and it is deliberately
    # *not* the read that check compares against: this one dates the candidate, and the
    # locked phase's own read is what proves the file has not moved since.
    if target.is_file():
        baseline: str | None = _read_text(target, subject=subject)
        before = take_census(target, root)
        # The baseline and the census are four opens of one name, and a
        # concurrent `os.replace` between any two of them puts one generation's
        # markers against the next generation's records. Measured under a wave:
        # "claim markers 5 vs records 6" on a register that was well-formed in
        # both generations, reported as CENSUS_MISMATCH — a *refusal*, telling a
        # seat whose values were perfect to repair a file with nothing wrong
        # with it. (`test_writeapi_concurrency`'s rc-7 failures under
        # full-suite load are this shape.) Whether the file moved is asked
        # *before* the census is believed, because a census taken across two
        # files is not evidence about either of them.
        if _read_text(target, subject=subject) != baseline:
            raise _Contended(
                Reason.CONTENDED,
                subject,
                "changed while it was being read, so its census describes no single version of it",
            )
        if not before.consistent:
            raise _Refused(
                Reason.CENSUS_MISMATCH,
                subject,
                f"is already losing entries and will not be written into: {before.describe()}",
            )
    elif target.exists():
        # Exists, but `is_file()` said no: a directory (`register = "common"`),
        # or a device. Named here rather than left to the replace, which reports
        # it as the environment failing ("could not be written under: Is a
        # directory") — an environment report for a value defect, and one that
        # does not name the value.
        raise _Refused(
            Reason.REGISTER_ABSENT,
            subject,
            "exists but is not a file, so there is no register here to write into",
        )
    elif edit.create:
        # Creation makes a file, never a directory tree: a register's parent is
        # a KB domain directory that already exists, and conjuring one from an
        # agent-supplied path is the typo explicit creation exists to stop,
        # arriving through the acknowledgment instead of around it.
        if not target.parent.is_dir():
            raise _Refused(
                Reason.REGISTER_ABSENT,
                subject,
                "cannot be created: its parent directory does not exist",
            )
        baseline = None
        before = Census()
    else:
        raise _Refused(
            Reason.REGISTER_ABSENT,
            subject,
            "does not exist, and creation was not asked for (creation is never implicit)",
        )

    try:
        candidate = edit.splice(baseline or "")
    except SpliceError as exc:
        raise _Refused(Reason.SPLICE_FAILED, subject, str(exc)) from exc

    return _Composed(
        edit=edit,
        target=target,
        subject=subject,
        baseline=baseline,
        before=before,
        candidate=candidate,
    )


def apply_edits(*, kb_root: Path, edits: Sequence[Edit]) -> Outcome:
    """Run the write path over a batch of edits, all-or-nothing.

    Two phases, and the boundary between them is the whole of this module's
    concurrency argument.

    **Unlocked** (:func:`_compose`, steps 2, 4 and 5): contain the path, read
    the live file, census it, splice the candidate. Every one of these is a read
    or a pure computation over what was read, none of them touches a file
    another writer can see, and the expensive half of a write is here rather
    than inside an exclusion. A splice that must parse what it composed takes
    the target's directory for itself, briefly, through :func:`proof_temp` —
    which is why nothing here may already hold it.

    **Locked** (steps 6, 7 and 8, with every target's directory held): sweep
    orphaned publish temps, write this write's temps, prove them, check that
    every target still holds the bytes its baseline was read from, and
    replace. It is one section from the first temp to the last replace, because a
    publish temp is only provably an orphan while nobody else can be making one,
    and because a check whose answer is computed outside the exclusion answers a
    question about a moment that has already passed.

    So: a batch that refuses any entry writes none; a concurrent writer's entry
    cannot be overwritten by one of these replaces; and a kill at any point
    leaves each file holding either its prior content or the proven candidate —
    there is no third state and therefore no rollback path to get wrong.

    The *retry* consequence is intended, not conceded: a writer whose baseline moved
    while it was composing finds it moved when it takes the lock, and answers
    *retry* rather than merging.

    Raises ``ValueError`` on an empty batch, and :class:`BatchInterrupted` — an
    ``OSError`` — when a replace fails: both are the environment being unfit
    rather than a verdict on the values, which is a different exit code and
    ``ops.py``'s to map. The exception carries the targets that landed before
    the failure, because a nine-entry wave that half-landed and a wave that did
    nothing are different situations and the caller has to tell them apart.
    """
    if not edits:
        raise ValueError("apply_edits requires at least one edit")

    root = Path(kb_root).resolve()
    temps: list[Path] = []
    try:
        claimed: set[str] = set()
        composed = [_compose(edit, root=root, claimed=claimed) for edit in edits]

        with contextlib.ExitStack() as held:
            for directory, subject in _lock_sites(composed):
                held.enter_context(_exclusive(directory, subject=subject))

            # Litter control, first thing under the lock and before this write
            # makes any temp of its own: whatever matches here was left by a
            # process that died holding it. Unconditional on this call's
            # outcome, since it runs ahead of the proof and the freshness check.
            for item in composed:
                _sweep_orphan_temps(item.target)

            # Steps 6 and 7 — compose on disk and prove, still touching no
            # live file.
            prepared: list[_Prepared] = []
            for item in composed:
                temp = _write_temp(item.target, item.candidate, mode=_target_mode(item.target))
                temps.append(temp)
                _prove(temp, root, item.edit, item.before, subject=item.subject)
                prepared.append(_Prepared(target=item.target, temp=temp, baseline=item.baseline, subject=item.subject))

            # Step 8, the freshness check: a *fresh* read of each live file,
            # not the composing read carried forward. With this check and the
            # replaces merely adjacent rather than excluded, a concurrent
            # replace landing between them is lost with no diagnostic, at 88%
            # per wave.
            for item in prepared:
                if not _still_matches(item.target, item.baseline):
                    raise _Contended(
                        Reason.CONTENDED,
                        item.subject,
                        "changed between the read and the replace; a concurrent writer intervened",
                    )

            # The only lines that touch a live file. Every verdict is already
            # in; what can still fail here is the filesystem, and when it does
            # the caller is told which targets went in before it did (see the
            # module docstring and :class:`BatchInterrupted`).
            committed: list[str] = []
            for item in prepared:
                try:
                    os.replace(item.temp, item.target)
                except OSError as exc:
                    raise BatchInterrupted(written=tuple(committed), failed=item.subject, cause=exc) from exc
                committed.append(item.subject)
        return Outcome(status=Status.WRITTEN, written=tuple(item.subject for item in composed))
    except _Contended as exc:
        return Outcome(status=Status.RETRY, reason=exc.reason, subject=exc.subject, detail=exc.detail)
    except _Refused as exc:
        return Outcome(status=Status.REFUSED, reason=exc.reason, subject=exc.subject, detail=exc.detail)
    finally:
        # No temp survives any path out of this function — refusal, retry,
        # success, or an exception nobody anticipated. A replaced temp is
        # already gone, which is what `missing_ok` is for.
        for temp in temps:
            temp.unlink(missing_ok=True)
