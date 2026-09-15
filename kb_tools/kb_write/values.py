"""The values-file grammar and the closed field vocabulary.

This module answers exactly one question: **is this a well-formed value?** It
never touches the KB, never opens a file under ``kb-root/``, and never resolves
an id — "does this id exist?" is ``ops.py``'s question against the store, and
"does this path resolve inside ``kb-root/``?" is ``store.py``'s. What it owns is
the grammar, the closed per-op key vocabulary, the inherited value domains, and
a **located refusal** for every value it will not accept.

**Transport: TOML via :mod:`tomllib`, prose in ``'''`` literal blocks.** Prose
in this corpus routinely carries ``$``, backslashes, backticks and quotes; a
literal block holds all of it with no escape grammar, which is what keeps the
byte-fidelity demand from being relocated one layer out instead of removed. Four
grammar rules are binding and are implemented here:

1. **The op travels on argv and never inside the file.** ``parse_values``
   takes the op as an argument; a top-level ``op`` key in the file is a second
   source for which op is running, and is refused as an unknown top-level key.
2. **One array-of-tables name, ``[[entry]]``, across every op.** The op fixes
   what an entry means. A per-op table name would make this grammar nine
   grammars and these refusal messages nine vocabularies. One file may carry a
   batch of entries; the batch is all-or-nothing, so a refusal anywhere
   refuses the file.
3. **The key vocabulary is closed and total.** An unrecognized key is a located
   refusal, never ignored — a silently dropped key is the same failure class
   this whole program exists to remove, in a new layer.
4. **A refusal names the key and the line**, and for the two failure modes a
   caller cannot see says which convention was expected: a path given
   repo-root-relative rather than kb-root-relative, and a prose value that
   embeds ``'''``, which no TOML literal block can hold because it is the
   block's own delimiter.

**Nothing here is coerced, defaulted, substituted, or repaired.** A missing
required key is a refusal, not a default. The single value transform in this
package — whitespace-run collapse inside a single-paragraph prose field — is
``render.py``'s; this module returns every prose value exactly as supplied. A
value that *cannot* survive the reader's grammar — a rationale carrying a blank
line, whose remainder the fold discards before normalization ever runs
(``kb_index_lib.py:987-991``) — is **refused here**, naming the line, never
collapsed and never truncated.

**Every numeric bound below is inherited**; this program authors none of its
own, and none on prose length, field size, entry count, or file size appears
anywhere:

* ``rigor`` (the one concept behind the on-disk ``confidence:`` and
  ``quality:`` fields) — a number in ``[0, 1]`` or the literal ``*pending*``
  (``verify_kb_metadata.py:185``, ``check_confidence_values``: the range and
  the pending literal are what is mechanically enforced — the rubric's named
  grades are an authoring convention, not a check).
* an on-point ``fraction`` — ``[0, 1]`` or ``*pending*``
  (:func:`kb_index_lib.parse_support_leaf` and
  :class:`kb_index_lib.SupportLeafError`'s docstring).
* a citation ``excerpt`` — at most :data:`EXCERPT_MAX_CHARS` characters
  (``verify_citations.py:92``).
* a ``strengthens`` pair's ``strength`` — ``[0, 1]`` (SPEC.md, Claim-Graph
  Nodes and Edges, the edge-class table's ``strengthens`` row). The verifier
  requires the value to be non-null and inside the same domain, so a strength
  beyond it — which renders a solidity the build-band ladder has no band
  for — is refused at both ends rather than only here.

Beside those domains sits one constraint that is not a magnitude at all but a
**representability** check, inherited from the reader's number grammar
(:data:`_READER_NUMBER_RE`) rather than from any range: the token a number
renders to must be a token that grammar matches. Python's shortest
round-tripping repr — which is what ``render._format_score`` emits, deliberately
(rounding an authored ``0.875`` would be a near-miss coercion) — switches to
exponent notation below ``1e-4`` and above ``1e16``, and spells the two
non-finite floats ``nan`` and ``inf``. None of those tokens is in the reader's
grammar, and each fails in a different silent direction: ``rigor = 1e-05``
renders ``- confidence: 1e-05``, whose *leading* token
``kb_index_lib._NUMBER_RE.search`` (``:174``) reads as **1.0** — a different
number, inside the domain; ``strength = nan`` renders a pair line
``_STRENGTHENS_PAIR_RE`` (``:131``) does not match at all, so the edge is
dropped entire. Every such value is inside its magnitude domain, so no domain
check above can see it. Refusing it here is what puts the report at the value
step naming the field, instead of at the readback step as a mismatch on an
already-minted id — or, for an in-range strength the reader cannot hold, as a
message telling the caller to report a renderer defect for a value they
supplied: a check failing for the wrong reason is a failed check. The check is
derived from the format contract, not from a magnitude this program authored,
so the no-authored-bounds rule is untouched.

**Dependency direction**: this module imports :mod:`kb_tools.kb_schema` and the
standard library, and nothing else. In particular it does **not** import
:mod:`render`, which is why the value carriers below mirror render's dataclasses
rather than reusing them; ``ops.py`` maps one onto the other. :mod:`pathlib` is
admissible here and is not in ``render.py``: the values file is the
*agent-supplied values* boundary, not the KB, and reading it under a pinned
strict UTF-8 decode is this module's job.

**Report lines are not composed here.** A refusal is returned as data —
:class:`Refusal`, carrying the offending field, the located line, and the
entry — because rendering the ``[<tag>] STATUS name detail`` line and its
``restore:`` clause needs the invocation, which is ``ops.py``'s to know.
"""

import re
import tomllib
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

from kb_tools import kb_schema

# ---------------------------------------------------------------------------
# Inherited vocabularies and bounds
#
# Each literal below is a module-local copy of a contract owned elsewhere,
# because this module's imports are pinned to `kb_schema` alone. Each is pinned
# to its owner by a test in test_kb_write_values.py rather than by an import —
# the strongest single-sourcing that dependency direction admits for a contract
# whose owner this module may not import. Where the owner can be moved into
# `kb_schema` instead, it is, and the copy goes away.
# ---------------------------------------------------------------------------

#: The authored "not yet assessed" literal, for a rigor or a fraction. Not a
#: copy: the object ``kb_index_lib`` itself compares against.
PENDING_LITERAL = kb_schema.PENDING_LITERAL

#: The inherited citation-excerpt bound (``verify_citations.EXCERPT_MAX_CHARS``).
#: An excerpt is a minimal quotation, not a pasted section.
EXCERPT_MAX_CHARS = 240

#: The reader's number grammar — the only spelling of a number anything
#: downstream of this API can read back. It is one sub-pattern with three
#: owners, all in ``kb_index_lib``: ``_NUMBER_RE`` (``:174``), which reads a
#: ``- confidence:`` / ``- quality:`` / ``- solidity:`` line; and the second
#: group of ``_STRENGTHENS_PAIR_RE`` (``:131``) and ``_SUPPORTS_PAIR_RE``
#: (``:137``), which read a pair line's score. A module-local copy for the
#: dependency-direction reason, and pinned to all three owners *behaviourally*
#: — by putting rendered
#: tokens through the owners' own matchers — in ``test_kb_write_values.py``,
#: which is stronger than comparing two pattern strings.
_READER_NUMBER_RE = re.compile(r"-?\d+(?:\.\d+)?")

#: The closed ``kind:`` vocabulary of a KB document. Structural position only —
#: it does not encode claim-graph flavor. The reader's two guards are
#: ``kb_index_lib.py:1226`` (leaf kinds) and ``:1506`` (index kinds); a document
#: whose kind is outside this set parses as neither.
DOCUMENT_KINDS: tuple[str, ...] = ("leaf", "index", "entry-point")

#: The closed ``status:`` vocabulary of an experiment block. ``run`` means the
#: result exists and its strengthens edges count; ``pending`` means unrun and
#: contributing nothing (``kb_index_lib.py:1371-1373``).
EXPERIMENT_STATUSES: tuple[str, ...] = ("run", "pending")

# Framework depends-on targets are authored as tokens rather than as node ids:
# `### INVARIANT-XX` headings and `- Axiom N:` bullets. The bullet-head scanner
# matches these two spellings and only these two (`kb_index_lib.py:168-169`), so
# a normalized `axiom-3` written into a bullet would be silently dropped rather
# than resolved — which is why the authored spelling is what this grammar takes.
_INVARIANT_TOKEN_RE = re.compile(r"^INVARIANT-[A-Z]+[0-9]+$")
_AXIOM_TOKEN_RE = re.compile(r"^Axiom [0-9]+$")

# The array-of-tables name every op shares.
ENTRY_KEY = "entry"


# ---------------------------------------------------------------------------
# Refusals and results
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Refusal:
    """One located reason a values file was not accepted.

    ``field`` names the offending key — dotted and indexed for a nested one
    (``depends-on[2].id``), so the identity in the report is exact even where
    the line is approximate. ``line`` is the 1-based line in the values file.
    ``entry`` is the 1-based position of the ``[[entry]]`` table the refusal
    came from, or ``None`` for a file-level refusal (a decode failure, a TOML
    parse error, an unknown top-level key).

    ``detail`` is a sentence, not a report line: prose here is the PE seat's to
    revise and no test asserts its wording.
    """

    field: str
    detail: str
    line: int | None = None
    entry: int | None = None


@dataclass(frozen=True)
class Entry:
    """One accepted ``[[entry]]``: its 1-based position and its checked values.

    ``values`` is keyed by the op's own vocabulary, with each value in the type
    its checker returns — prose as the supplied :class:`str`, a rigor or a
    fraction as :class:`float` or ``None`` for :data:`PENDING_LITERAL`, edges
    and hosted node blocks as the carriers below.
    """

    index: int
    values: Mapping[str, object]


@dataclass(frozen=True)
class ParsedValues:
    """The whole file's verdict: every entry, or every reason there is none.

    The two are exclusive — a batch that refuses any entry writes none,
    so ``entries`` is empty whenever ``refusals`` is not. Refusals are
    *collected* rather than raised at the first one: a caller fixing a batch
    should see every bad entry in one report instead of one per invocation.
    """

    op: str
    entries: tuple[Entry, ...] = ()
    refusals: tuple[Refusal, ...] = ()

    @property
    def refused(self) -> bool:
        return bool(self.refusals)


# ---------------------------------------------------------------------------
# Value carriers
#
# These mirror render.py's DependsOnTarget / ExperimentDecl / SupportDecl
# without importing them, per the dependency direction. They deliberately carry
# no `title`: a depends-on target's title is read off the resolved referent,
# which is ops.py's resolution to make and not a value the caller supplies.
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class DependsOnValue:
    """One ``depends-on`` target: a ``clm-`` id, a framework token or a work id.

    ``applicability`` is a **work** target's alone — how much of the cited work
    bears on the entry being written — and is ``None`` for the authored
    ``*pending*`` literal. Supplying it against any other target kind is refused
    by name rather than ignored: a key with no renderer behind it is the
    silently-dropped-value class this vocabulary exists to close.
    """

    id: str
    context: str | None = None
    applicability: float | None = None


@dataclass(frozen=True)
class StrengthensPair:
    """One ``strengthens:`` pair — the claim an experiment's result bears on."""

    id: str
    strength: float


@dataclass(frozen=True)
class SupportsPair:
    """One ``supports:`` pair — a beneficiary claim and the on-point fraction.

    ``fraction`` is ``None`` for the authored :data:`PENDING_LITERAL`: an
    intended-but-unassessed beneficiary edge, which is a legal authored value
    and not a zero.
    """

    id: str
    fraction: float | None


@dataclass(frozen=True)
class ExperimentValues:
    """One hosted ``exp-id:`` / ``status:`` / ``strengthens:`` block."""

    exp_id: str
    status: str
    strengthens: tuple[StrengthensPair, ...] = ()


@dataclass(frozen=True)
class SupportValues:
    """One hosted ``sup-id:`` / ``supports:`` block."""

    sup_id: str
    supports: tuple[SupportsPair, ...] = ()


# ---------------------------------------------------------------------------
# The internal refusal signal
# ---------------------------------------------------------------------------


class _Refused(Exception):
    """Raised by a checker; converted to a located :class:`Refusal` by the caller.

    A checker knows the field and the reason but not where the entry sits, so
    it carries the *source key path* it should be located by: a tuple of
    ``(key, occurrence)`` steps walked from the entry header down, which is
    what lets a refusal three levels deep — a strengthens pair inside an
    experiment block inside an entry — resolve to one line rather than to the
    nearest container. ``prose_offset`` is the 0-based line offset of an
    offending line *within a prose value*, which is what lets a blank-line
    refusal name the blank line rather than the key above it.
    """

    def __init__(
        self,
        field: str,
        detail: str,
        *,
        path: Sequence[tuple[str, int]] | None = None,
        prose_offset: int | None = None,
    ) -> None:
        super().__init__(detail)
        self.field = field
        self.detail = detail
        self.path: tuple[tuple[str, int], ...] = tuple(path) if path is not None else ((field, 1),)
        self.prose_offset = prose_offset


def _nested(exc: _Refused, *, container: str, item: int, key: str | None = None) -> _Refused:
    """Re-raise a checker's refusal with its position inside a container.

    ``key`` distinguishes the two container shapes. An array of *tables* has a
    key inside each item, so the label and the path both extend
    (``depends-on[2].id``, located by the second ``depends-on`` and then its
    ``id``). An array of *scalars* has no inner key and lives on one source
    line, so the item index goes into the label alone and the path stops at the
    container (``claims[2]``, located at the ``claims`` line).
    """
    if key is None:
        return _Refused(f"{container}[{item}]", exc.detail, path=((container, 1),), prose_offset=exc.prose_offset)
    return _Refused(
        f"{container}[{item}].{exc.field}",
        exc.detail,
        path=((container, item),) + exc.path,
        prose_offset=exc.prose_offset,
    )


# ---------------------------------------------------------------------------
# Locating a refusal in the source text
# ---------------------------------------------------------------------------


_ENTRY_HEADER_RE = re.compile(rf"^\s*\[\[\s*{ENTRY_KEY}\s*\]\]")


def _key_pattern(key: str) -> re.Pattern[str]:
    """Match ``key = …`` in either TOML spelling, bare or inside an inline table,
    and the ``[[entry.key]]`` / ``[entry.a.key]`` table header that opens one."""
    esc = re.escape(key)
    return re.compile(rf"(?:^|[\s{{,\[])({esc})\s*=|^\s*\[\[?[^\]]*?{esc}\s*\]\]?")


class _Source:
    """Best-effort mapping from an entry and a key path back to a source line.

    Best-effort by construction: :mod:`tomllib` reports a position for a *parse*
    error and nothing at all for a value that parsed, so a semantic refusal is
    located by scanning the text the parser already accepted. The scan skips
    comment lines and never fails — an unlocatable key falls back to the
    deepest line it did reach, and finally to the entry's own header — so every
    refusal over a parsed file names a line. The field name in the refusal is
    the exact identity; the line is the aid to finding it.
    """

    def __init__(self, text: str) -> None:
        self._lines = text.splitlines()
        self._entry_starts = [n for n, line in enumerate(self._lines, 1) if _ENTRY_HEADER_RE.match(line)]

    def _span(self, entry: int) -> tuple[int, int]:
        """The 1-based inclusive line span of the ``entry``-th ``[[entry]]`` table."""
        if not self._entry_starts or entry < 1 or entry > len(self._entry_starts):
            return 1, len(self._lines)
        start = self._entry_starts[entry - 1]
        end = self._entry_starts[entry] - 1 if entry < len(self._entry_starts) else len(self._lines)
        return start, end

    def entry_line(self, entry: int) -> int:
        return self._span(entry)[0]

    def _is_comment(self, lineno: int) -> bool:
        return self._lines[lineno - 1].lstrip().startswith("#")

    def key_line(self, entry: int, path: Sequence[tuple[str, int]]) -> int:
        """Walk ``path`` from the entry header down, returning the deepest line found."""
        start, end = self._span(entry)
        found = start
        cursor = start
        for key, occurrence in path:
            pattern = _key_pattern(key)
            hits = 0
            for lineno in range(cursor, end + 1):
                if self._is_comment(lineno) or not pattern.search(self._lines[lineno - 1]):
                    continue
                hits += 1
                if hits == occurrence:
                    found = cursor = lineno
                    break
            else:
                # Not found at this depth: the deepest line reached so far is
                # the honest answer, and a deeper search would only wander.
                return found
        return found

    def prose_line(self, entry: int, path: Sequence[tuple[str, int]], offset: int) -> int:
        """The source line of a prose value's ``offset``-th line.

        A TOML multi-line literal trims a newline immediately following its
        opening ``'''``, so the value's first line sits on the *next* source
        line in that (idiomatic) layout and on the key's own line otherwise. A
        value with no triple-quote delimiter is single-line in the source
        whatever newlines it carries, so its every line is the key's line.
        """
        key_line = self.key_line(entry, path)
        source = self._lines[key_line - 1]
        opener = max(source.rfind("'''"), source.rfind('"""'))
        if opener < 0:
            return key_line
        tail = source[opener + 3 :]
        base = key_line + 1 if not tail.strip() else key_line
        return min(base + offset, len(self._lines))

    def top_level_line(self, key: str) -> int:
        """Locate a key above the first ``[[entry]]`` header."""
        end = (self._entry_starts[0] - 1) if self._entry_starts else len(self._lines)
        pattern = _key_pattern(key)
        for lineno in range(1, end + 1):
            if not self._is_comment(lineno) and pattern.search(self._lines[lineno - 1]):
                return lineno
        return 1


# ---------------------------------------------------------------------------
# Type helpers
# ---------------------------------------------------------------------------


def _typename(value: object) -> str:
    """A TOML-shaped name for what arrived, so a refusal says what was supplied."""
    if isinstance(value, bool):
        return "a boolean"
    if isinstance(value, int):
        return "an integer"
    if isinstance(value, float):
        return "a float"
    if isinstance(value, str):
        return "a string"
    if isinstance(value, dict):
        return "a table"
    if isinstance(value, list):
        return "an array"
    return f"a {type(value).__name__}"


def _require_str(value: object, field: str, *, expected: str = "a string") -> str:
    if not isinstance(value, str):
        raise _Refused(field, f"expected {expected}, got {_typename(value)}")
    return value


def _require_number(value: object, field: str, *, domain: str) -> float:
    """Accept an int or a float the reader can read back; reject a bool.

    A TOML integer is widened to a float. That is a lossless widening of the
    same number, not a coercion of one value into another: refusing ``rigor =
    1`` while accepting ``rigor = 1.0`` would be a byte-fidelity demand of
    exactly the kind this program exists to remove.

    The **representability** gate below is deliberately here rather than in each
    of the three numeric checkers: this is the one funnel every authored number
    in the vocabulary passes through, so a numeric field added later inherits it
    without anyone remembering to. It runs *before* each caller's domain check,
    because a value the reader cannot hold is not out of range — it is
    unwritable, and saying "outside [0, 1]" of ``nan`` would be a second check
    failing for the wrong reason. See the module docstring for what each
    unreadable spelling does downstream.
    """
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise _Refused(field, f"expected a number in {domain}, got {_typename(value)}")
    number = float(value)
    token = f"{number}"
    if _READER_NUMBER_RE.fullmatch(token) is None:
        raise _Refused(
            field,
            f"is written to disk as {token!r}, which is not a number in the grammar every reader of these "
            f"files uses ({_READER_NUMBER_RE.pattern}) — exponent notation, 'nan' and 'inf' are all outside "
            f"it, and a value spelled that way is read back as a different number or dropped entirely. "
            f"Supply it as a plain decimal",
        )
    return number


def _require_array(value: object, field: str, *, of: str) -> list:
    if not isinstance(value, list):
        raise _Refused(field, f"expected an array of {of}, got {_typename(value)}")
    if not value:
        raise _Refused(field, "is an empty array; omit the key rather than supplying it empty")
    return value


# ---------------------------------------------------------------------------
# Scalar checkers
# ---------------------------------------------------------------------------

# A paragraph break: a newline, optional horizontal whitespace, a newline.
_BLANK_LINE_RE = re.compile(r"\n[^\S\n]*\n")


def check_prose(value: object, field: str) -> str:
    """A single-paragraph prose value, returned exactly as supplied.

    **A blank line is refused, never collapsed.** Every prose field
    this API carries is single-paragraph by the reader's grammar: the register
    fold breaks at a paragraph break *before* normalization ever runs and
    discards the remainder, a ``## `` heading and a ``no-claim:`` reason are one
    line by construction, and a citation excerpt is rejected outright if it
    carries a newline. Silently absorbing the break would be a near-miss
    coercion, and truncating it is the silent-loss family this whole
    program exists to close — so the value is refused and the blank line named.

    A *single* newline is fine and is not touched here: ``render.py``'s
    whitespace collapse joins it into the one physical line the reader folds,
    which is what defeats the key-leading-continuation truncation class.
    """
    text = _require_str(value, field, expected="prose")
    if not text.strip():
        raise _Refused(field, "expected prose, got an empty value")
    lead = len(text) - len(text.lstrip())
    body = text.strip()
    match = _BLANK_LINE_RE.search(body)
    if match is not None:
        offset = text.count("\n", 0, lead) + body.count("\n", 0, match.start()) + 1
        raise _Refused(
            field,
            "carries a blank line. This field is single-paragraph by the reader's grammar — the fold "
            "stops at a paragraph break and discards everything after it — so the value is refused "
            "here rather than truncated on disk or silently joined. Rewrite it as one paragraph",
            prose_offset=offset,
        )
    return text


def check_rigor(value: object, field: str) -> float | None:
    """Local rigor — the one concept behind on-disk ``confidence:`` and ``quality:``.

    A number in the inherited ``[0, 1]`` domain, or :data:`PENDING_LITERAL`,
    which returns ``None`` — the same encoding of the same literal that
    ``render``'s score formatter takes, not a substituted value. Which on-disk
    field name it lands in is read off the id's kind by the op, never chosen by
    the caller.
    """
    if isinstance(value, str):
        if value == PENDING_LITERAL:
            return None
        raise _Refused(field, f"expected a number in [0, 1] or the literal {PENDING_LITERAL}, got {value!r}")
    number = _require_number(value, field, domain=f"[0, 1] or the literal {PENDING_LITERAL}")
    if not 0.0 <= number <= 1.0:
        raise _Refused(
            field,
            f"{number} is outside [0, 1] — the domain hand-authored confidence and quality values are "
            f"verifier-enforced to. Use a value in [0, 1] or {PENDING_LITERAL}",
        )
    return number


def check_fraction(value: object, field: str) -> float | None:
    """An on-point fraction: the inherited ``[0, 1]`` domain, or ``*pending*``.

    The value is the share of the supporting work that bears on this claim, so
    both ends are closed. Zero is admissible and says the support bears nothing
    on the claim — a judgement about the edge, distinct both from omitting it
    and from ``*pending*``.
    """
    if isinstance(value, str):
        if value == PENDING_LITERAL:
            return None
        raise _Refused(field, f"expected a number in [0, 1] or the literal {PENDING_LITERAL}, got {value!r}")
    number = _require_number(value, field, domain=f"[0, 1] or the literal {PENDING_LITERAL}")
    if not 0.0 <= number <= 1.0:
        raise _Refused(
            field,
            f"{number} is outside [0, 1] — the value is the share of the supporting work that bears on "
            f"this claim, so 0 (none of it) is the floor and 1 (all of it) the ceiling. Use a value in "
            f"[0, 1] or {PENDING_LITERAL}",
        )
    return number


_CITATION_KEY_RE = re.compile(rf"^{kb_schema.WORK_KEY_RE}$")


def check_citation_key(value: object, field: str) -> str:
    """An external work's citation key — the whole of its node identity.

    Checked against the same grammar the bullet-head scanner reads a work
    target with, because the two have to agree: a key this admits and that scan
    does not would write an entry no edge could ever name.
    """
    token = _require_str(value, field, expected="a citation key")
    if not _CITATION_KEY_RE.match(token):
        raise _Refused(
            field,
            f"{token!r} is not a citation key matching {kb_schema.WORK_KEY_RE} — it must open and close "
            f"alphanumeric, so the token has no trailing punctuation for a depends-on bullet's head to "
            f"argue about",
        )
    return token


def check_strength_value(value: object, field: str) -> float | None:
    """An external work's standing: the inherited ``[0, 1]``, or ``*pending*``.

    Foundational at 1 through spurious at 0. It is hand-authored and nothing
    derives it, so ``*pending*`` is admissible and is what a build writes: a
    number here about a paper nobody in this build has read would be worse than
    a blank, which is why no default and no heuristic stands behind this field.
    """
    if isinstance(value, str):
        if value == PENDING_LITERAL:
            return None
        raise _Refused(field, f"expected a number in [0, 1] or the literal {PENDING_LITERAL}, got {value!r}")
    number = _require_number(value, field, domain=f"[0, 1] or the literal {PENDING_LITERAL}")
    if not 0.0 <= number <= 1.0:
        raise _Refused(
            field,
            f"{number} is outside [0, 1] — the domain a cited work's standing carries, foundational at 1 "
            f"and spurious at 0. Use a value in [0, 1] or {PENDING_LITERAL}",
        )
    return number


def check_applicability(value: object, field: str) -> float | None:
    """A claim→work pairing's applicability: the same domain and the same reading
    as a ``supports`` edge's on-point fraction, over a work rather than a support.

    Zero is admissible and says the cited work is sound and bears nothing on
    this claim — a relevance asserted and found wanting, which is a distinct
    fact from no edge at all and from ``*pending*``.
    """
    return check_fraction(value, field)


def check_strength(value: object, field: str) -> float:
    """A ``strengthens:`` pair's conferred strength — the inherited ``[0, 1]``.

    The domain is SPEC.md's, in the same row of the edge-class table that says
    what a ``strengthens`` edge is; it is not a magnitude this program chose.
    An out-of-range strength writes a solidity outside the band ladder, which
    is why the verifier checks the same domain over the materialized edge —
    this gate is the earlier of the two, not the only one. ``*pending*`` is not
    admissible: the verifier rejects a null strength on every strengthens edge.
    """
    if isinstance(value, str):
        raise _Refused(field, f"expected a number, got {value!r}; a strengthens pair carries no pending form")
    number = _require_number(value, field, domain="[0, 1]")
    if not 0.0 <= number <= 1.0:
        raise _Refused(
            field,
            f"{number} is outside [0, 1], the domain a strengthens edge's strength carries. A strength "
            f"beyond it lifts the claim it strengthens off the build-band ladder entirely",
        )
    return number


def check_excerpt(value: object, field: str) -> str:
    """A citation excerpt: prose, and at most the inherited 240 characters.

    The bound is measured on the supplied text. A value that would fall under
    it only after the renderer's whitespace collapse is refused rather than
    accepted, which is the safe direction: the caller shortens a quotation
    instead of the tool silently reshaping one.
    """
    text = check_prose(value, field)
    if len(text.strip()) > EXCERPT_MAX_CHARS:
        raise _Refused(
            field,
            f"is {len(text.strip())} characters, over the {EXCERPT_MAX_CHARS}-character bound the citation "
            f"checker enforces. Quote the clause, not the section",
        )
    return text


def check_path(value: object, field: str) -> str:
    """A document or register path, **kb-root-relative**.

    Only the shape is decidable here: a non-empty relative string. Whether the
    path resolves inside ``kb-root/`` after symlink resolution is ``store.py``'s
    containment check, and is deliberately not duplicated.

    The convention is stated in the refusal because it is a failure the caller
    cannot see: the briefs render some paths repo-root-relative, so a caller
    composing a values file has both conventions on screen, and a repo-root
    -relative register path resolves *inside* kb-root — failing no boundary and
    naming the wrong file.
    """
    text = _require_str(value, field, expected="a kb-root-relative path")
    if not text.strip():
        raise _Refused(field, "expected a kb-root-relative path, got an empty value")
    if Path(text).is_absolute() or text.startswith("/"):
        raise _Refused(
            field,
            f"{text!r} is absolute. Paths are relative to kb-root/, not to the repository root and not "
            f"to the filesystem — a register at <kb-root>/part3/claim-quality.md is 'part3/claim-quality.md'",
        )
    return text


def check_kind(value: object, field: str) -> str:
    """A document's structural-position ``kind:``, from the closed set."""
    text = _require_str(value, field)
    if text not in DOCUMENT_KINDS:
        raise _Refused(field, f"{text!r} is outside the closed kind vocabulary {DOCUMENT_KINDS}")
    return text


def check_status(value: object, field: str) -> str:
    """An experiment block's ``status:``, from the closed set."""
    text = _require_str(value, field)
    if text not in EXPERIMENT_STATUSES:
        raise _Refused(field, f"{text!r} is outside the closed status vocabulary {EXPERIMENT_STATUSES}")
    return text


def _id_checker(*kinds: str) -> Callable[[object, str], str]:
    """Build a checker for a node id of the given kinds — **shape only**.

    Shape is this module's; existence is ``ops.py``'s against the authored
    inventory. The authored placeholder bodies are refused even though
    they match the hash grammar: ``clm-xxxxxx`` is the literal a template
    carries, never a node, and letting one through as well-formed would put the
    resolution failure a layer further from the caller who typed it.
    """
    pattern = re.compile(rf"^{kb_schema.id_body(*kinds)}$")
    expected = " or ".join(f"{kind}-{kb_schema.HASH_RE}" for kind in (kinds or kb_schema.ID_KINDS))

    def check(value: object, field: str) -> str:
        token = _require_str(value, field, expected=f"an id matching {expected}")
        if token in kb_schema.ID_PLACEHOLDERS:
            raise _Refused(field, f"{token!r} is the authored placeholder id, which is never a real node")
        if not pattern.match(token):
            raise _Refused(field, f"{token!r} is not an id matching {expected}")
        return token

    return check


check_claim_id = _id_checker("clm")
check_experiment_id = _id_checker("exp")
check_support_id = _id_checker("sup")
check_entry_id = _id_checker("clm", "sup")

_WORK_ID_RE = re.compile(rf"^{kb_schema.WORK_ID_RE}$")


def check_work_id(value: object, field: str) -> str:
    """An external work's id: ``work-`` plus the citation key.

    Shape only, like every other id checker; existence is ``ops.py``'s. Nothing
    mints one — the key is the corpus's own and the id is derived from it
    (:func:`kb_schema.work_id`) — so there is no placeholder body to refuse and
    no hash to match, only the key grammar a bibliography spells keys in.
    """
    token = _require_str(value, field, expected=f"an id matching {kb_schema.WORK_ID_RE}")
    if not _WORK_ID_RE.match(token):
        raise _Refused(field, f"{token!r} is not an external-work id matching {kb_schema.WORK_ID_RE}")
    return token


def check_rationale_id(value: object, field: str) -> str:
    """A register entry's id, over all three kinds a register holds.

    Wider than :data:`check_entry_id` by exactly one kind, because a rationale
    is one free-form prose field on a claim, a support and an external work
    alike — and a work's is the only place this corpus records what it has to
    say about a paper it does not contain, the cited authority behind that
    judgement included. Nothing about that text can be composed when the entry
    is minted, so a write-once rationale is a field with no author.

    The two other update ops taking an entry id stay at two kinds and are not
    widened alongside this one: a work's standing is ``set-work-strength``'s
    question and has a field of its own, and a work is terminal, so there is no
    depends-on bullet for ``add-depends-on`` to write onto one.
    """
    token = _require_str(value, field, expected="a claim, support or external-work id")
    if token.startswith(f"{kb_schema.WORK_PREFIX}-"):
        return check_work_id(token, field)
    return check_entry_id(token, field)


def check_depends_target(value: object, field: str) -> str:
    """A ``depends-on`` target: a ``clm-`` id, a framework token, or a work id.

    Framework tokens are matched in their authored spellings — ``INVARIANT-XX``
    and ``Axiom N`` — because those are the two the bullet-head scanner
    recognizes. The derived ``axiom-N`` form the index records would be dropped
    silently if it were written into a bullet, so it is not admitted here.
    """
    token = _require_str(value, field, expected="a claim id, a framework token or a work id")
    if _INVARIANT_TOKEN_RE.match(token) or _AXIOM_TOKEN_RE.match(token):
        return token
    if token.startswith(f"{kb_schema.WORK_PREFIX}-"):
        return check_work_id(token, field)
    return check_claim_id(token, field)


# ---------------------------------------------------------------------------
# Composite checkers
# ---------------------------------------------------------------------------


def _table_items(value: object, field: str, *, of: str) -> list[dict]:
    items = _require_array(value, field, of=of)
    for position, item in enumerate(items, 1):
        if not isinstance(item, dict):
            raise _Refused(f"{field}[{position}]", f"expected {of}, got {_typename(item)}", path=((field, 1),))
    return items


def _check_table(
    item: Mapping[str, object],
    *,
    container: str,
    position: int,
    specs: "tuple[Field, ...]",
) -> dict[str, object]:
    """Check one nested table against a closed sub-vocabulary.

    Unknown and missing keys are refused here exactly as they are at entry
    level: a closed vocabulary that is total only at the top would drop a
    mistyped key inside an edge, which is the same failure in a smaller box.
    """
    known = {spec.name: spec for spec in specs}
    for key in item:
        if key not in known:
            raise _Refused(
                f"{container}[{position}].{key}",
                f"unknown key; a {container} table takes {sorted(known)}",
                path=((container, position), (key, 1)),
            )
    checked: dict[str, object] = {}
    for spec in specs:
        if spec.name not in item:
            if spec.required:
                raise _Refused(
                    f"{container}[{position}].{spec.name}",
                    "required key is missing; nothing is defaulted",
                    path=((container, position),),
                )
            continue
        try:
            checked[spec.name] = spec.check(item[spec.name], spec.name)
        except _Refused as exc:
            raise _nested(exc, container=container, item=position, key=spec.name) from exc
    return checked


def check_depends_on(value: object, field: str) -> tuple[DependsOnValue, ...]:
    """The ``depends-on`` list: ``{ id, context? }`` tables.

    One key of one shape in every op that carries it — the insert ops and
    ``add-depends-on`` alike (A0.1). ``id`` names the referent the entry
    consumes; there is no ``source``/``target`` pair to get the orientation of
    backwards, because the entry being written *is* the source.
    """
    items = _table_items(value, field, of="a { id, context, applicability } table")
    out = []
    for position, item in enumerate(items, 1):
        checked = _check_table(item, container=field, position=position, specs=_DEPENDS_ON_FIELDS)
        target = str(checked["id"])
        if "applicability" in item and not target.startswith(f"{kb_schema.WORK_PREFIX}-"):
            raise _Refused(
                f"{field}[{position}].applicability",
                f"{target!r} is not an external work, and applicability is a work pairing's quantity "
                f"alone — a claim target's paren carries its derived solidity and a framework target's "
                f"carries its context, so there is nowhere for this value to land",
                path=((field, position), ("applicability", 1)),
            )
        out.append(
            DependsOnValue(
                id=target,
                context=checked.get("context"),  # type: ignore[arg-type]
                applicability=checked.get("applicability"),  # type: ignore[arg-type]
            )
        )
    return tuple(out)


def check_references(value: object, field: str) -> tuple[DependsOnValue, ...]:
    """The ``references`` list: ``{ id, context? }`` tables, claim ids only.

    A reference is one claim of this corpus naming another, so the target
    vocabulary is narrower than ``depends-on``'s by two kinds: a framework node
    is bedrock nobody cross-references, and an external work is what the
    ``rests-on`` class already reaches. There is no ``applicability`` either —
    the pairing carries no score of any kind.
    """
    items = _table_items(value, field, of="a { id, context } table")
    return tuple(
        DependsOnValue(id=str(checked["id"]), context=checked.get("context"))  # type: ignore[arg-type]
        for checked in (
            _check_table(item, container=field, position=position, specs=_REFERENCES_FIELDS)
            for position, item in enumerate(items, 1)
        )
    )


def check_strengthens(value: object, field: str) -> tuple[StrengthensPair, ...]:
    """An experiment block's ``strengthens:`` pairs: ``{ id, strength }``."""
    items = _table_items(value, field, of="a { id, strength } table")
    out = []
    for position, item in enumerate(items, 1):
        checked = _check_table(item, container=field, position=position, specs=_STRENGTHENS_FIELDS)
        out.append(StrengthensPair(id=str(checked["id"]), strength=float(checked["strength"])))  # type: ignore[arg-type]
    return tuple(out)


def check_supports(value: object, field: str) -> tuple[SupportsPair, ...]:
    """A support block's ``supports:`` pairs: ``{ id, fraction }``."""
    items = _table_items(value, field, of="a { id, fraction } table")
    out = []
    for position, item in enumerate(items, 1):
        checked = _check_table(item, container=field, position=position, specs=_SUPPORTS_FIELDS)
        fraction = checked["fraction"]
        out.append(SupportsPair(id=str(checked["id"]), fraction=None if fraction is None else float(fraction)))  # type: ignore[arg-type]
    return tuple(out)


def check_experiment_nodes(value: object, field: str) -> tuple[ExperimentValues, ...]:
    """The hosted experiment blocks of one document's frontmatter.

    Named apart from the additive ``experiments`` *reference* list on purpose:
    a container that hosts an experiment and one that merely names other
    people's are different declarations, and two keys one letter apart would be
    a collision inside a closed vocabulary.
    """
    items = _table_items(value, field, of="an { exp-id, status, strengthens } table")
    out = []
    for position, item in enumerate(items, 1):
        checked = _check_table(item, container=field, position=position, specs=_EXPERIMENT_NODE_FIELDS)
        out.append(
            ExperimentValues(
                exp_id=str(checked["exp-id"]),
                status=str(checked["status"]),
                strengthens=tuple(checked.get("strengthens", ())),  # type: ignore[arg-type]
            )
        )
    return tuple(out)


def check_support_nodes(value: object, field: str) -> tuple[SupportValues, ...]:
    """The hosted support blocks of one document's frontmatter."""
    items = _table_items(value, field, of="a { sup-id, supports } table")
    out = []
    for position, item in enumerate(items, 1):
        checked = _check_table(item, container=field, position=position, specs=_SUPPORT_NODE_FIELDS)
        out.append(
            SupportValues(
                sup_id=str(checked["sup-id"]),
                supports=tuple(checked.get("supports", ())),  # type: ignore[arg-type]
            )
        )
    return tuple(out)


def _id_list_checker(check_one: Callable[[object, str], str], *, of: str) -> Callable[[object, str], tuple[str, ...]]:
    def check(value: object, field: str) -> tuple[str, ...]:
        items = _require_array(value, field, of=of)
        out = []
        for position, item in enumerate(items, 1):
            try:
                out.append(check_one(item, field))
            except _Refused as exc:
                raise _nested(exc, container=field, item=position) from exc
        return tuple(out)

    return check


#: A leaf's ``claims:`` membership. Claim ids only — a ``sup-`` id here is a
#: silent drop today (recon 2.2) and is a refusal now.
check_claim_id_list = _id_list_checker(check_claim_id, of="claim ids")
check_experiment_id_list = _id_list_checker(check_experiment_id, of="experiment ids")


def check_prose_list(value: object, field: str) -> tuple[str, ...]:
    """A list of single-paragraph prose items — the ``strengthen-by`` shape."""
    items = _require_array(value, field, of="prose items")
    out = []
    for position, item in enumerate(items, 1):
        try:
            out.append(check_prose(item, field))
        except _Refused as exc:
            raise _nested(exc, container=field, item=position) from exc
    return tuple(out)


# ---------------------------------------------------------------------------
# The closed vocabulary, per op
#
# One table, additive by construction: a new op is a new row, and a new key on
# an existing op is a new Field in that row. Nothing outside this table decides
# what an op takes, so the surface an agent writes against and the surface this
# module checks cannot drift apart.
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Field:
    """One key of one op's vocabulary: its name, its checker, and whether it is required.

    A required key that is absent is a refusal, never a default — the whole
    point of the closed vocabulary is that a value the caller did not supply is
    a value nobody supplied.
    """

    name: str
    check: Callable[[object, str], object]
    required: bool = False


_DEPENDS_ON_FIELDS: tuple[Field, ...] = (
    Field("id", check_depends_target, required=True),
    Field("context", check_prose),
    # A work target's alone; refused by name on any other kind, in
    # `check_depends_on`, rather than accepted and dropped.
    Field("applicability", check_applicability),
)

_REFERENCES_FIELDS: tuple[Field, ...] = (
    Field("id", check_claim_id, required=True),
    Field("context", check_prose),
)

_STRENGTHENS_FIELDS: tuple[Field, ...] = (
    Field("id", check_claim_id, required=True),
    Field("strength", check_strength, required=True),
)

_SUPPORTS_FIELDS: tuple[Field, ...] = (
    Field("id", check_claim_id, required=True),
    Field("fraction", check_fraction, required=True),
)

_EXPERIMENT_NODE_FIELDS: tuple[Field, ...] = (
    Field("exp-id", check_experiment_id, required=True),
    Field("status", check_status, required=True),
    Field("strengthens", check_strengthens),
)

_SUPPORT_NODE_FIELDS: tuple[Field, ...] = (
    Field("sup-id", check_support_id, required=True),
    Field("supports", check_supports),
)

# The insert ops share every key below and differ by exactly one each:
# `strengthen-by` is a claim's open work items and a support entry has no
# strengthen-by section in the register; `supports` is a support's beneficiary
# fan-out and a claim entry stages none. Accepting either key on the other op
# would be a key with no renderer behind it — the silently-dropped-value class,
# arriving through the door built to close it.
_INSERT_COMMON: tuple[Field, ...] = (
    Field("register", check_path, required=True),
    Field("title", check_prose, required=True),
    Field("rigor", check_rigor, required=True),
    Field("rationale", check_prose, required=True),
    Field("depends-on", check_depends_on),
    Field("no-edge", check_prose),
)

OP_FIELDS: Mapping[str, tuple[Field, ...]] = {
    "insert-claim-entry": _INSERT_COMMON + (Field("strengthen-by", check_prose_list),),
    # `supports` is spelled and shaped exactly as it is inside
    # `set-frontmatter`'s `support-node` table (A0.1's one-concept-one-key
    # rule): the pairs are the same pairs, staged in the register entry while
    # the leaf that will host them does not exist yet, and transcribed into
    # that leaf's frontmatter at phase-3. A0.1's rule is what keeps the two
    # spellings from becoming two concepts.
    "insert-support-entry": _INSERT_COMMON + (Field("supports", check_supports),),
    # An experiment has no register entry — its canonical declaration IS the
    # `exp-id:` block in its hosting document — so it takes `document` rather
    # than `register`, and no title, rigor or rationale, which are a register
    # entry's fields. `status` and `strengthens` are spelled exactly as they are
    # inside `set-frontmatter`'s `experiment-node` table, per the
    # one-concept-one-key rule. There is no `exp-id` key: the op mints the id,
    # and a key by which a caller could name one would be the id-without-a-write
    # path that mint fusion makes unconstructible.
    "insert-experiment-entry": (
        Field("document", check_path, required=True),
        Field("status", check_status, required=True),
        Field("strengthens", check_strengthens),
    ),
    # An external work is not minted, so this insert takes the one value its
    # identity is derived from — `key`, the corpus's own citation key — where
    # every other insert takes none. There is still no `id` key: the id is a
    # function of the key (`kb_schema.work_id`), so an entry and its id cannot
    # come apart, and a caller naming both could disagree with itself. There is
    # no `depends-on` either: the node is terminal.
    "insert-work-entry": (
        Field("register", check_path, required=True),
        Field("key", check_citation_key, required=True),
        Field("title", check_prose, required=True),
        Field("strength", check_strength_value, required=True),
        Field("rationale", check_prose, required=True),
    ),
    # Every update op leads with `id`, the entry being edited.
    "set-work-strength": (
        Field("id", check_work_id, required=True),
        Field("strength", check_strength_value, required=True),
    ),
    # Both ends of the pairing, because an applicability is a property of
    # neither alone: `id` is the claim whose entry carries the bullet and `work`
    # is the work it names. Spelled `work` rather than a second bare `id` for
    # `set-on-point-fraction`'s reason — which end is which is the whole content
    # of this op's inputs.
    "set-applicability": (
        Field("id", check_claim_id, required=True),
        Field("work", check_work_id, required=True),
        Field("applicability", check_applicability, required=True),
    ),
    "set-rigor": (
        Field("id", check_entry_id, required=True),
        Field("rigor", check_rigor, required=True),
    ),
    # The one update op whose `id` reaches every register entry kind: a
    # rationale is the same prose field on a claim, a support and a work.
    "set-rationale": (
        Field("id", check_rationale_id, required=True),
        Field("rationale", check_prose, required=True),
    ),
    # Two lists, both optional, because this op adds an entry's outgoing edges
    # and a caller may have only one class to add. Neither supplied is refused
    # in `ops._plan_add_depends_on`, which is where the entry is resolved: a
    # cross-key condition is not something a per-key vocabulary can state.
    "add-depends-on": (
        Field("id", check_entry_id, required=True),
        Field("depends-on", check_depends_on),
        Field("references", check_references),
    ),
    "set-frontmatter": (
        Field("document", check_path, required=True),
        Field("kind", check_kind, required=True),
        # A free-text stable-reference label the reader types generically, the
        # same way it types `no-claim` (`kb_index_lib._frontmatter_value`): a
        # quoted string, unquoted on the way in. No vocabulary, no length and
        # no shape is stated for it anywhere in the contract, so none is
        # enforced here — this program authors no bound of its own, and the only
        # constraint applied is the one the block's grammar already imposes on
        # every field, that the value be a single paragraph.
        Field("path-stable", check_prose),
        Field("claims", check_claim_id_list),
        Field("no-claim", check_prose),
        Field("experiments", check_experiment_id_list),
        Field("experiment-node", check_experiment_nodes),
        Field("support-node", check_support_nodes),
    ),
    "mark-claim-in-leaf": (
        Field("document", check_path, required=True),
        Field("id", check_claim_id, required=True),
        Field("locator", check_prose, required=True),
    ),
    "set-on-point-fraction": (
        Field("id", check_support_id, required=True),
        Field("claim", check_claim_id, required=True),
        Field("fraction", check_fraction, required=True),
    ),
    # The one read-only row: it writes nothing and prints the citation form.
    #
    # **Four keys, not three.** A citation's link target is resolved by every reader of it —
    # `verify_citations.check_citations` and `verify_md_links` alike — relative
    # to the file the link is written IN, never to kb-root. A three-value op
    # knowing only the cited path can therefore emit no spelling that resolves:
    # a kb-root-relative target printed into `part3/index.md` resolves to
    # `part3/part3/…` and fails the gate the op exists to pre-empt. So the
    # citing document is a value, and the op renders the target relative to it.
    # The two paths are spelled `citing-` / `cited-` rather than one of them
    # taking the bare `document` of the write ops: which end is which is the
    # whole content of this op's inputs, and a bare `document` beside a
    # qualified one is exactly the one-word-apart collision a closed vocabulary
    # must not carry.
    "render-citation": (
        Field("excerpt", check_excerpt, required=True),
        Field("cited-document", check_path, required=True),
        Field("anchor", check_prose, required=True),
        Field("citing-document", check_path, required=True),
    ),
}


# ---------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------

# tomllib reports the position inside the message text on 3.11; 3.13 added
# structured attributes. Read the attribute where it exists and fall back to
# the message, so the located line survives either interpreter.
_TOML_POSITION_RE = re.compile(r"at line (\d+)")


def _decode_error_line(data: bytes, offset: int) -> int:
    return data.count(b"\n", 0, offset) + 1


def parse_values_file(path: Path, *, op: str) -> ParsedValues:
    """Read and check a values file. See :func:`parse_values`.

    The bytes are decoded **strict UTF-8** and a decode failure is a located
    refusal, never an ``errors="replace"`` repair: a mojibake rationale
    written into a register is a silent corruption of an author's value, which
    is precisely what this API exists to make unrepresentable. The read is byte
    -level rather than :meth:`Path.read_text` for one reason — a text-mode
    failure reports its offset within whatever chunk the decoder held, so the
    line could not be named.
    """
    data = path.read_bytes()
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError as exc:
        return ParsedValues(
            op=op,
            refusals=(
                Refusal(
                    field=str(path),
                    detail=(
                        f"is not valid UTF-8 ({exc.reason} at byte {exc.start}). A values file is read as "
                        f"strict UTF-8 and is never decoded with replacement characters — re-save it as UTF-8"
                    ),
                    line=_decode_error_line(data, exc.start),
                ),
            ),
        )
    return parse_values(text, op=op)


def parse_values(text: str, *, op: str) -> ParsedValues:
    """Check a values file's text against ``op``'s closed vocabulary.

    Returns every accepted entry, or — if anything at all is refused — no
    entries and every refusal, because the batch is all-or-nothing. An
    ``op`` outside :data:`OP_FIELDS` is a programming error rather than a
    refusal: which op is running comes from argv, where an unknown subcommand
    is the CLI's usage error (exit 2), not the values' (exit 7).
    """
    fields = OP_FIELDS.get(op)
    if fields is None:
        raise ValueError(f"unknown op {op!r}; the write API's ops are {sorted(OP_FIELDS)}")

    source = _Source(text)
    try:
        document = tomllib.loads(text)
    except tomllib.TOMLDecodeError as exc:
        message = str(exc)
        located = getattr(exc, "lineno", None)
        if located is None:
            position = _TOML_POSITION_RE.search(message)
            if position is not None:
                located = int(position.group(1))
            elif "end of document" in message:
                # The parser ran off the end — an unclosed literal block, most
                # often. The end of the file is where it gave up, and is a
                # truer answer than no line at all.
                located = len(text.splitlines()) or 1
        return ParsedValues(
            op=op,
            refusals=(
                Refusal(
                    field=ENTRY_KEY,
                    detail=(
                        f"is not parseable TOML: {message}. If the offending value is a '''literal''' "
                        f"block, check that its text does not embed ''' — no TOML literal block can hold "
                        f"its own delimiter, and the parse error it raises looks like a tool defect"
                    ),
                    line=located,
                ),
            ),
        )

    refusals: list[Refusal] = []
    for key in document:
        if key != ENTRY_KEY:
            refusals.append(
                Refusal(
                    field=key,
                    detail=(
                        f"is not a key of a values file. The only top-level name is [[{ENTRY_KEY}]]; the op "
                        f"travels on the command line and is never named inside the file"
                    ),
                    line=source.top_level_line(key),
                )
            )

    raw_entries = document.get(ENTRY_KEY)
    if raw_entries is None:
        refusals.append(
            Refusal(
                field=ENTRY_KEY,
                detail=f"carries no [[{ENTRY_KEY}]] table; every op reads its values from that array of tables",
                line=1,
            )
        )
        return ParsedValues(op=op, refusals=tuple(refusals))
    if not isinstance(raw_entries, list):
        refusals.append(
            Refusal(
                field=ENTRY_KEY,
                detail=f"expected an array of tables ([[{ENTRY_KEY}]]), got {_typename(raw_entries)}",
                line=source.top_level_line(ENTRY_KEY),
            )
        )
        return ParsedValues(op=op, refusals=tuple(refusals))
    if not raw_entries:
        refusals.append(
            Refusal(
                field=ENTRY_KEY,
                detail="is empty; a values file with no entries names no work",
                line=source.top_level_line(ENTRY_KEY),
            )
        )
        return ParsedValues(op=op, refusals=tuple(refusals))

    entries: list[Entry] = []
    for index, raw in enumerate(raw_entries, 1):
        if not isinstance(raw, dict):
            refusals.append(
                Refusal(
                    field=ENTRY_KEY,
                    detail=f"expected a table, got {_typename(raw)}",
                    line=source.entry_line(index),
                    entry=index,
                )
            )
            continue
        values, entry_refusals = _check_entry(raw, index=index, fields=fields, source=source)
        refusals.extend(entry_refusals)
        if not entry_refusals:
            entries.append(Entry(index=index, values=values))

    if refusals:
        return ParsedValues(op=op, refusals=tuple(refusals))
    return ParsedValues(op=op, entries=tuple(entries))


def _check_entry(
    raw: Mapping[str, object],
    *,
    index: int,
    fields: tuple[Field, ...],
    source: _Source,
) -> tuple[dict[str, object], list[Refusal]]:
    """Check one ``[[entry]]``: unknown keys, missing required keys, then values."""
    known = {spec.name: spec for spec in fields}
    values: dict[str, object] = {}
    refusals: list[Refusal] = []

    for key in raw:
        if key not in known:
            refusals.append(
                Refusal(
                    field=key,
                    detail=(
                        f"is not a key this op takes. The vocabulary is closed and total — an unrecognized "
                        f"key is refused, never ignored. This op takes {sorted(known)}"
                    ),
                    line=source.key_line(index, ((key, 1),)),
                    entry=index,
                )
            )

    for spec in fields:
        if spec.name not in raw:
            if spec.required:
                refusals.append(
                    Refusal(
                        field=spec.name,
                        detail="is required and was not supplied. No value is defaulted or inferred",
                        line=source.entry_line(index),
                        entry=index,
                    )
                )
            continue
        try:
            values[spec.name] = spec.check(raw[spec.name], spec.name)
        except _Refused as exc:
            refusals.append(_locate(exc, index=index, source=source))

    return values, refusals


def _locate(exc: _Refused, *, index: int, source: _Source) -> Refusal:
    if exc.prose_offset is not None:
        line = source.prose_line(index, exc.path, exc.prose_offset)
    else:
        line = source.key_line(index, exc.path)
    return Refusal(field=exc.field, detail=exc.detail, line=line, entry=index)
