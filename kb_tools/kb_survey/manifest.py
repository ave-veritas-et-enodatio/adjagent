"""The survey manifest: record types, the closed flag vocabulary, one writer and one reader.

This module is the manifest schema's only definition. The survey op writes
through :func:`write_manifest`; every consumer — the validator, the driver, the
tests — reads through :func:`read_manifest` and never re-parses the JSON with code
of its own. Both directions are derived from the dataclasses below rather than
hand-written, so a field added to a record cannot be silently missing from either
half.

It holds no LaTeX knowledge and no filesystem policy beyond the atomic write, and
it never imports the parser library. Where a boundary check is possible it is
here, at the write: an absolute path or an id without the ``mf:`` prefix is refused
rather than persisted, because both are guarantees no downstream reader can restore.

**Determinism.** :func:`to_json` sorts keys, emits no timestamp, and records
every path relative to an anchor the manifest itself names — the source root, or
the manifest's own directory (Two path spaces, below) — never absolute and never
derived from the working directory, so the same source tree and flag vector produce
byte-identical output. List order is document order — meaningful, and never the
product of iterating a set or a dict built by discovery order.

**Nothing rendered is stored.** The rendered section index
(:func:`render_section_index`) is a join produced at the point of use. It is not
written beside the manifest, and ``worklist[]`` holds no copy of the section
fields it joins — it is an index into ``sections[]``, not a second view of it.

**Every line-oriented rendering emits exactly one line per record, and asserts it.**
Text a rendering places on a line is whitespace-normalized as it is
placed (:func:`_one_line`) and the count is checked against the records rendered
(:func:`_joined`); the *record* keeps the raw fact. The rendering is model-facing,
so a line count that can drift is a model-facing artifact that can silently
misalign.

**Identity.** ``sections[].id`` is a dotted-ordinal path plus a title slug,
collision-suffixed, and ``results[].id`` is minted the same way. Both carry the
constant ``mf:`` prefix — ``mf:2.3.1-navier-stokes``, ``mf:thm-4.2``. Manifest ids
are stable within a run and across re-runs of the same source, and deliberately not
stable across source edits: inserting a section renumbers its siblings. The prefix
is constant rather than run-derived precisely so the id text does not vary run to
run, and so misuse is checkable by a literal token grep. Where a durable key
exists it is the source's own ``\\label``, which ``results[].label`` carries
unchanged; ``results[].id`` is the sharper hazard of the two, because it sits in the
same record as that durable label and only the prefix says which one survives.
This module mints no claim-graph id and validates none — that space is
``kb_schema``'s.

**Schema notes** — the sentences the field names cannot carry alone:

* ``composed_span`` is an extent in the composed stream and never a place to read
  from — a locator is ``origin`` or ``source_span``.
* ``resolved_result_id: null`` is the ordinary case for a bibliographic ``cite``
  and never means malformed.
* The ``subdivision`` value restates ``subsection_count == 0`` and carries no
  judgment.
* ``mf:`` ids are per-run and never enter ``kb-root/``.
* ``parent_id`` is null at the tree root.
* ``files[].included_by`` is null for an entry file.
* ``files[]`` records **every inclusion occurrence**, so several records may name
  one ``path`` — each with its own ``included_by`` and ``include_origin``.
  A target is composed once per run: its text sits in the stream at the
  **first** inclusion in document order, so the sections it mints attach to the
  parent in force *there* — parent attachment is first-inclusion-wins. Every later
  occurrence is a record plus one ``include-collapsed`` flag and contributes no
  second copy of the lines, which is what keeps two leaves from transcribing one
  stretch of source. A reader counting distinct source files therefore takes
  ``{record.path for record in files}`` and never ``len(files)``.
* ``sections[].entry_file`` names the entry file whose composed stream produced the
  record. A source file two entries reach is composed once per entry, so it yields
  one section record per entry — same title, different ``id``, different
  ``entry_file`` — and that is the coverage-correct answer rather than a duplicate.
  The field is stamped where the entry and its harvest are paired, because it is not
  recoverable afterwards: ``files[].included_by`` is an inclusion edge and reaching
  an entry through it would mean reproducing composition order.
* ``stripped_chars`` is a character count and not a token estimate — a reader
  comparing it against a token-stated criterion applies a rough conversion at the
  point of reading, and no document is restated to match the unit.
* a ``protected_spans[]`` entry says its region must not be *parsed*; it says
  nothing about whether the region counts. It counts — opacity is about parsing,
  never about accounting.
* ``display_math_count`` counts display math **however spelled** — declared
  environments, ``\\[…\\]`` and ``$$…$$`` alike — because the number exists to
  inform a reading seat, and one that reports ``0`` on a section built entirely
  from ``\\[…\\]`` misinforms it. Inline ``$…$`` is not display math and is
  not counted.
* an edge's ``kind`` is the semantic class (``cite`` or ``ref``) and its ``macro``
  is the spelling that produced it: ``\\citep`` and ``\\citealp`` are one relation
  differently written, so a consumer building the origination→citers map groups on
  ``kind`` and reads ``macro`` only for forensics.

**Two path spaces, split by record type rather than by value.** ``files[]`` is the
include DAG and every path in it — ``path``, ``included_by``,
``include_origin.file`` — is source-root-relative, as is ``run.entry_files``. Every
*locator* — ``origin_runs[].file``, ``results[].origin.file``,
``edges[].from_origin.file`` — names an **expanded stream** instead, at the path
:func:`expanded_stream_relpath` gives it, relative to the manifest's own directory:
the survey resolves the document's ``\\newcommand`` macros before it harvests, so the
bytes an extent stands for are the expanded ones and there is no line in an original
``.tex`` that carries them. ``flags[].source_span.file`` is the one field spanning
both, and unavoidably: a flag raised while composing — a missing ``\\input``, a
cycle, an encoding fallback — concerns a source file at a source line, and the
material it names may never have reached the stream at all, so it has no expanded
position to be given.

``origin_runs[]`` is an ordered list of contiguous line runs in the expanded stream
the record's entry file produced. It is the only coordinate space that may appear in
a coverage token, a brief, or any rendered slice. ``level`` is the sectioning command name without its
backslash (``part``, ``chapter``, ``section``, ``subsection``, ``subsubsection``,
``paragraph``, ``subparagraph``, plus ``document`` on the synthetic root); depth is
derivable from the ``parent_id`` chain, the command name is not.

**The preamble is not accounted.** Stream start through
``\\begin{document}`` is parsed for vocabulary and macro definitions, and its lines
appear in no ``origin_runs`` and in no ``stripped_chars``: ``\\usepackage{amsmath}``
is markup, and accounting it would oblige a distiller to transcribe it into a leaf.
Front matter — ``\\begin{document}`` through the first sectioning command — *is*
accounted, and the synthetic root is what owns it.
"""

import json
import os
import re
import tempfile
import types
import typing
from collections.abc import Iterator
from dataclasses import dataclass, fields, is_dataclass
from enum import StrEnum
from pathlib import Path, PurePosixPath

# Constant, never run-derived.
ID_PREFIX = "mf:"

_SLUG_CAMEL = re.compile(r"(?<=[a-z0-9])(?=[A-Z])")
_SLUG_OTHER = re.compile(r"[^a-z0-9]+")


def volume_slug(source: str) -> str:
    """The name one source volume is known by everywhere downstream.

    Mechanical and stable: the directory is dropped, camel-case boundaries
    become hyphens, and every other non-alphanumeric run collapses to one.
    ``AcmeWidgetsDerivations.tex`` is ``acme-widgets-derivations``, and so is
    ``sources/AcmeWidgetsDerivations.tex`` — which is what lets a manifest's
    source-root-relative entry file and a run config's repo-relative source
    name one volume. The tool names it, so the slug is never a model's choice
    and never has to be recovered from one.

    Here rather than in ``kb_pipeline`` because its argument is a manifest
    field — ``run.entry_files`` and ``sections[].entry_file`` — and its three
    consumers sit on both sides of that module: the pipeline's artifact paths,
    the driver's member names, and this package's own skeleton derivation,
    which may not import a module that imports the CLI.
    """
    stem = PurePosixPath(source).stem
    return _SLUG_OTHER.sub("-", _SLUG_CAMEL.sub("-", stem).lower()).strip("-")


#: The directory the expanded streams are written to, beside the manifest.
EXPANDED_DIRNAME = "expanded"


def expanded_stream_relpath(entry_file: str) -> str:
    """Where one entry file's expanded stream lands, relative to the manifest's directory.

    **Relative to the manifest, not to the source root**, which is what keeps the
    recorded path independent of where a run was told to put its output: two runs
    over one tree write byte-identical manifests however their ``--manifest-out``
    was spelled, and a reader holding the manifest file holds the directory the
    path resolves against.

    **One stream per entry file, never per source file.** A source file two entries
    reach expands under each entry's own macro table, so one artifact per source
    would be two different expansions of one name. The slug is the volume's, so the
    entry a stream belongs to is legible from the path.

    Here rather than in ``kb_pipeline`` for :func:`volume_slug`'s reason: its
    argument is a manifest field and its consumers sit on both sides of that module.
    """
    return f"{EXPANDED_DIRNAME}/{volume_slug(entry_file)}.tex"


class ManifestError(Exception):
    """Base for every refusal this module raises."""


class ManifestContractError(ManifestError):
    """A manifest violates a guarantee the schema makes about its own contents."""


class FlagCode(StrEnum):
    """The closed ``unparsed-structure`` reason vocabulary.

    Closed is the enforcement: the hostile-fixture table asserts an exact flag
    multiset per fixture, and a fixture needing a code not listed here is a
    deliberate amendment rather than an addition made at the point of discovery.
    """

    MACRO_SECTIONING = "macro-sectioning"
    MACRO_ENVIRONMENT = "macro-environment"
    MACRO_INCLUDE_ARG = "macro-include-arg"
    CONDITIONAL_BRANCH = "conditional-branch"
    WRAPPED_TITLE = "wrapped-title"
    MISSING_INCLUDE = "missing-include"
    INCLUDE_CYCLE = "include-cycle"
    INCLUDE_OUTSIDE_ROOT = "include-outside-root"
    # A target already composed in this run
    # is recorded again and flagged here rather than expanded a second time, so the
    # collapse is data a reading seat can see rather than a silent divergence
    # between the manifest and the document.
    INCLUDE_COLLAPSED = "include-collapsed"
    ORPHAN_SUBSECTION = "orphan-subsection"
    ENCODING_FALLBACK = "encoding-fallback"
    PARSER_EXCEPTION = "parser-exception"


class Subdivision(StrEnum):
    """The worklist's closed two-value vocabulary.

    A closed enum rather than a string is the structural half of the rule: a field
    that cannot hold prose cannot hold a rationale.
    """

    TERMINAL = "terminal"
    SUBDIVIDED = "subdivided"


class EdgeKind(StrEnum):
    """The semantic class of a cross-reference, and nothing finer.

    Two values, because there are two relations: a citation into the bibliography
    and a reference into the document. The eleven macros the harvest is closed
    over are presentations of one of these two, and the spelling that produced an
    edge survives on ``Edge.macro``.
    """

    CITE = "cite"
    REF = "ref"


@dataclass(frozen=True)
class Origin:
    """A point in an originating file."""

    file: str
    line: int


@dataclass(frozen=True)
class OriginRun:
    """One contiguous line run in an originating file."""

    file: str
    line_start: int
    line_end: int


@dataclass(frozen=True)
class SourceSpan:
    """A locator: a line range in an originating file."""

    file: str
    line_start: int
    line_end: int


@dataclass(frozen=True)
class ComposedSpan:
    """An extent in the composed stream. Harvest-internal; never a read target."""

    start: int
    end: int


@dataclass(frozen=True)
class Run:
    source_root: str
    entry_files: list[str]
    invocation_flags: list[str]


@dataclass(frozen=True)
class TheoremEnv:
    name: str
    printed_name: str
    numbered_within: str | None
    origin: Origin


@dataclass(frozen=True)
class Vocabulary:
    theorem_envs: list[TheoremEnv]


@dataclass(frozen=True)
class FileRecord:
    path: str
    included_by: str | None
    include_origin: Origin | None


@dataclass(frozen=True)
class Profile:
    stripped_chars: int
    result_count: int
    subsection_count: int
    display_math_count: int


@dataclass(frozen=True)
class Section:
    id: str
    entry_file: str
    level: str
    title: str
    starred: bool
    in_appendix: bool
    parent_id: str | None
    sibling_ordinal: int
    origin_runs: list[OriginRun]
    composed_span: ComposedSpan
    profile: Profile


@dataclass(frozen=True)
class Result:
    id: str
    env: str
    label: str | None
    section_id: str
    origin_runs: list[OriginRun]
    composed_span: ComposedSpan


@dataclass(frozen=True)
class Edge:
    kind: EdgeKind
    macro: str
    from_section_id: str
    from_origin: Origin
    target_token: str
    resolved_result_id: str | None


@dataclass(frozen=True)
class Flag:
    code: FlagCode
    source_span: SourceSpan
    detail: str


@dataclass(frozen=True)
class ProtectedSpan:
    """One verbatim-content region: content that must not be *parsed*.

    Not content that does not exist — the distinction the whole record turns on. Its
    lines *are* inside its section's ``origin_runs`` and its characters *do* contribute
    ``stripped_chars``; what it is not is walkable. Check 1c reads these extents, so a
    slice boundary cannot fall strictly inside one and leave an unbalanced
    ``\\begin{lstlisting}`` in one leaf.

    Excluded regions (``comment`` bodies) are deliberately **not** recorded here: no
    check reads them, their absence from ``origin_runs`` is the entire statement about
    them, and a record nothing consumes is cost without capability.
    """

    env: str
    origin_runs: list[OriginRun]


@dataclass(frozen=True)
class WorklistEntry:
    """An index into ``sections[]``.

    Two fields, both measurements, neither of them advice. The profile is joined by
    :func:`worklist_slices` rather than copied here, because an unchecked second
    copy drifts for the same reason a second file does.
    """

    section_id: str
    subdivision: Subdivision


@dataclass(frozen=True)
class Manifest:
    run: Run
    vocabulary: Vocabulary
    files: list[FileRecord]
    sections: list[Section]
    results: list[Result]
    edges: list[Edge]
    flags: list[Flag]
    protected_spans: list[ProtectedSpan]
    worklist: list[WorklistEntry]


# ---------------------------------------------------------------------------
# Serialization — one definition, both directions
# ---------------------------------------------------------------------------

# Field names whose string values are paths and must therefore be repo-relative.
_PATH_FIELDS = frozenset({"file", "path", "source_root", "entry_file", "entry_files"})


def _walk_strings(value: object, field_name: str = "") -> Iterator[tuple[str, str]]:
    """Yield every ``(field_name, string_value)`` pair in a record tree."""
    if is_dataclass(value):
        for field in fields(value):
            yield from _walk_strings(getattr(value, field.name), field.name)
    elif isinstance(value, list):
        for item in value:
            yield from _walk_strings(item, field_name)
    elif isinstance(value, str):
        yield field_name, value


def _check_contract(manifest: Manifest) -> None:
    """Refuse a manifest that would break a guarantee no reader could restore."""
    for field_name, value in _walk_strings(manifest):
        if field_name in _PATH_FIELDS and Path(value).is_absolute():
            raise ManifestContractError(
                f"absolute path in {field_name!r}: {value!r} — every path must be repo-root-relative"
            )
    for record in (*manifest.sections, *manifest.results):
        if not record.id.startswith(ID_PREFIX):
            raise ManifestContractError(f"manifest id {record.id!r} does not carry the {ID_PREFIX!r} prefix")


def _to_jsonable(value: object) -> object:
    if is_dataclass(value):
        return {field.name: _to_jsonable(getattr(value, field.name)) for field in fields(value)}
    if isinstance(value, list):
        return [_to_jsonable(item) for item in value]
    if isinstance(value, StrEnum):
        return str(value)
    return value


def _from_jsonable(annotation: object, value: object, *, where: str) -> object:
    origin = typing.get_origin(annotation)
    if origin in (typing.Union, types.UnionType):
        if value is None:
            return None
        inner = [arg for arg in typing.get_args(annotation) if arg is not type(None)]
        return _from_jsonable(inner[0], value, where=where)
    if origin is list:
        if not isinstance(value, list):
            raise ManifestContractError(f"{where}: expected a list, got {type(value).__name__}")
        (item_type,) = typing.get_args(annotation)
        return [_from_jsonable(item_type, item, where=f"{where}[{i}]") for i, item in enumerate(value)]
    if isinstance(annotation, type) and issubclass(annotation, StrEnum):
        try:
            return annotation(value)
        except ValueError as exc:
            raise ManifestContractError(f"{where}: {value!r} is not a member of {annotation.__name__}") from exc
    if is_dataclass(annotation):
        return _build(annotation, value, where=where)
    if not isinstance(value, annotation):  # type: ignore[arg-type]
        raise ManifestContractError(f"{where}: expected {annotation.__name__}, got {type(value).__name__}")
    return value


def _build(cls: type, data: object, *, where: str) -> object:
    if not isinstance(data, dict):
        raise ManifestContractError(f"{where}: expected an object, got {type(data).__name__}")
    hints = typing.get_type_hints(cls)
    expected = {field.name for field in fields(cls)}
    unknown = sorted(set(data) - expected)
    missing = sorted(expected - set(data))
    if unknown or missing:
        raise ManifestContractError(
            f"{where}: unknown fields {unknown}, missing fields {missing}. "
            "Re-run the survey op to regenerate it — a manifest is re-creatable by construction."
        )
    return cls(**{name: _from_jsonable(hints[name], data[name], where=f"{where}.{name}") for name in sorted(expected)})


def to_json(manifest: Manifest) -> str:
    """Serialize deterministically: sorted keys, no timestamp, relative paths."""
    _check_contract(manifest)
    return json.dumps(_to_jsonable(manifest), sort_keys=True, indent=2, ensure_ascii=False) + "\n"


def from_json(text: str) -> Manifest:
    """Deserialize, refusing anything the dataclasses do not describe rather than coercing it."""
    manifest = _build(Manifest, json.loads(text), where="manifest")
    assert isinstance(manifest, Manifest)
    return manifest


def write_text_atomic(text: str, path: Path) -> None:
    """Write ``text`` to ``path`` so a reader sees the whole file or the previous one.

    **The temporary name is minted, not derived.** A name derived from the
    destination's is the same name for every writer of that destination, so two runs
    surveying into one ``--manifest-out`` share one temp file: their writes interleave
    inside it, and whichever renames second finds nothing left to rename. That is the
    half-written file this write exists to prevent, arriving through concurrency rather
    than through a kill. :func:`tempfile.mkstemp` gives each writer a name of its own,
    and the rename stays atomic because the temp is created in the destination's own
    directory — ``os.replace`` is atomic within a filesystem, not across them.

    A failed write takes its temporary with it, rather than leaving one per attempt.

    The manifest is one caller and the expanded streams beside it are the other: the
    manifest names those streams as read targets, so a torn one is a locator pointing
    at bytes nobody wrote — the same failure, and it takes the same discipline.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    handle, temporary = tempfile.mkstemp(dir=path.parent, prefix=f"{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as stream:
            stream.write(text)
        os.replace(temporary, path)
    except OSError:
        Path(temporary).unlink(missing_ok=True)
        raise


def write_manifest(manifest: Manifest, path: Path) -> None:
    """Serialize and write atomically: a killed run leaves the old manifest or none."""
    write_text_atomic(to_json(manifest), path)


def read_manifest(path: Path) -> Manifest:
    return from_json(path.read_text(encoding="utf-8"))


# ---------------------------------------------------------------------------
# The join the reader owns — produced at the point of use, never persisted
# beside the manifest.
# ---------------------------------------------------------------------------


def _one_line(text: str) -> str:
    """Whitespace-collapsed text, so one record cannot occupy two rendered lines.

    The record keeps the raw fact — a ``\\section`` title the author wrapped across
    two source lines is transcribed as written — and the display is derived from it
    at the point of rendering.
    """
    return " ".join(text.split())


def _joined(lines: list[str], *, what: str) -> str:
    """Join one-per-record lines, asserting the count rather than documenting it.

    Every author-supplied string below is placed through :func:`_one_line`, so this
    cannot fire on wrapped source; what it catches is a rendering that stopped
    normalizing. Asserted because the failure is otherwise silent: a wrapped
    ``\\paragraph`` title on the frozen corpus made the section index 162 lines for
    161 sections, and the model reading it out of a prompt slot sees a stray
    fragment and a count that does not match.
    """
    text = "\n".join(lines)
    rendered = len(text.splitlines())
    if rendered != len(lines):
        raise ManifestContractError(
            f"{what}: {rendered} lines rendered for {len(lines)} records — a line-oriented "
            "rendering emits exactly one line per record"
        )
    return text


def render_section_index(manifest: Manifest) -> str:
    """One line per section, ``id · title · level · subdivision``, ordered by id.

    It exists so the designer copies from a compact list rather than mining a JSON
    document for ids. **Every** section, unconditionally: there is no display cap.
    The brief that receives it says "an id this list does not carry does not
    exist", and under a cap that sentence is false — the coverage check then
    reports every omitted section uncovered against a designer that was never shown
    its id, and no revision can close the finding. The index is *incoming* brief
    input, where the constraint is the reading seat's context window rather than the
    outgoing-message limit, so a constant chosen ahead of any corpus is not the
    thing to bound it with. No line count belongs here: :func:`_joined` is the only
    assertion this rendering makes about its size, and it is about correspondence,
    not about length.
    """
    subdivisions = {entry.section_id: entry.subdivision for entry in manifest.worklist}
    ordered = sorted(manifest.sections, key=lambda section: section.id)
    lines = [
        f"{section.id} · {_one_line(section.title)} · {section.level} · "
        f"{subdivisions.get(section.id, '(not in worklist)')}"
        for section in ordered
    ]
    return _joined(lines, what="section index")
