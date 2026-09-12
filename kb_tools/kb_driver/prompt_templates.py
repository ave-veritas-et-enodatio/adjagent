"""Prompt composition: template load, strict slot fill, persistence, lint.

Templates ship beside the code at ``kb_driver/prompt-templates/*.tmpl`` and are
anchored by ``__file__`` — the package-resource exception
``kb_pipeline.installed_template`` already makes; repo and KB paths stay
cwd-anchored. Bodies are the prompt engineer's; this module is the machinery
that loads, composes, persists, and lints them, and it never chooses which
template to use — its callers do: the step table for the seat briefs, and
:mod:`kb_tools.kb_claimgraph.ask` for the two claim-graph inference asks, whose
composed prompts land in that build's own workspace rather than through
:func:`persist`.

**A slot is spelled** ``@!slot-name!@``, the same marker grammar ``gen-defs.py``
renders the agent-definition templates with — delimiters and name class alike
(:data:`SLOT_NAME`) — so a model reading either surface reads one syntax. The
delimiter is polar rather than symmetric because an
unclosed opener is then unambiguous: :func:`slots_of` refuses any ``@!`` or
``!@`` that does not parse as a marker and names the line, which a symmetric
delimiter cannot do — its unclosed opener pairs silently with the next marker's
opening. A brace is only a brace here: a JSON example, a shell ``${VAR}`` or a
``\\frac{a}{b}`` reaches the model as written.

**A namespace routes the slot; the name never does.** ``@!dyn.<name>!@`` is
filled from the caller's per-call data and from nowhere else, and ``@!<name>!@``
from the composer's own sources — constants, fragments, alternatives, the
calling row — and from nowhere else. :data:`DYNAMIC_PREFIX` sits outside the name
class, so a namespace can never be mistaken for a name and a name can never claim
one. What that buys is a hole that declares what it is in the artifact: these
templates are read and edited by agents holding no repository context, and the
plausible helpful edit to a marker indistinguishable from prose is to inline the
literal it stands for — which is exactly the divergence a slot exists to prevent,
the parser matching against the same constant. It buys the composer the same
thing: neither source can shadow the other, so the collision is not checked but
absent, and a miswiring is reported as the mistake it is rather than as an
unfilled slot here and an unused value there.

**Composition is strict in both directions.** Every slot a template (or a
fragment it pulls in) declares must have a value, and every value the caller
supplies must be used. A violation is a boundary check failure —
:class:`runlog.BoundaryError`, exit 15 — because only driver code writes the
step table and only the PE writes the templates: a disagreement between them
is a defect, never a pipeline outcome.

**What a bare slot is filled from**, and the distinction is what keeps prose and
code single-sourced:

* *constants* — the pool a template draws on only where a slot names it, so one
  pool serves every template: driver constants (``@!deviation-kinds!@``,
  ``@!layout-paths!@``, the DOMAINS/SCOPE contracts) wired once in
  ``steps.CONSTANT_SLOTS``, and, for the claim-graph asks,
  ``kb_claimgraph.ask.MARKER_SLOTS`` — the marker literals whose parse matches
  against the same constants;
* *fragments* — the shared fragments, resolved here from :data:`FRAGMENTS` by
  loading the file under ``fragments/`` and rendering it first, so wave
  discipline is one chunk injected by the composer rather than a restatement per
  template;
* *alternatives* — the same directory's caller-selected pieces, registered per
  slot in :data:`ALTERNATIVE_SLOTS`. The caller passes a **choice** — one
  registered name, or ``None`` where the slot admits an empty fill — and the
  composer resolves it exactly as it resolves a fragment. A caller holding the
  chosen body's prose would be prose reaching a model from application code, so
  the name is what travels;
* *the row* — what the calling row rather than the driver decides, passed in
  ``row``. Today that is one value, :data:`SEAT_SLOT`, and it is here rather
  than in the constants because it differs per step.

A fragment and an alternative each resolve **one level deep**: the slots the
resolved body declares join the required set and are filled from these same
sources, and a resolved body naming a fragment or an alternative slot of its own
is refused.

The reserved-slot rule is enforced twice on purpose: :func:`render` refuses a WAVE call whose
template omits a reserved slot (the composition that is about to happen), and
:func:`lint` refuses any ``*.wave.tmpl`` that omits one (every template that
exists, whether or not this run reaches it).

Stdlib only.
"""

import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType

from . import runlog

_log = runlog.logger("prompt_templates")

# Package resource, not a cwd path: the templates ship with the code.
PROMPT_TEMPLATES_DIR = Path(__file__).parent / "prompt-templates"

TEMPLATE_SUFFIX = ".tmpl"

#: Where the pieces spliced into a template live, beneath it. **Membership is
#: the whole declaration**: the top level holds exactly what something
#: dispatches, this directory holds exactly what something splices, and a
#: reader tells the two apart by where a file sits rather than by decoding its
#: name. Every file here is registered in exactly one of the two vocabularies
#: below — one resolved by the composer, one chosen by a caller — and the
#: correspondence is checked in both directions rather than asserted.
FRAGMENTS_DIRNAME = "fragments"

#: A slot name: letter-led lower-case segments joined by single hyphens, the
#: identifier class ``gen-defs.py`` admits for a marker name. Stated
#: independently rather than imported — the generator is stdlib-only, does not
#: ship, and neither package depends on the other — so the two spellings are
#: kept identical by hand and by a reader comparing them, not by an import.
SLOT_NAME = r"[a-z][a-z0-9]*(?:-[a-z0-9]+)*"

#: The one namespace, and the whole of the routing grammar: a slot carrying it is
#: the caller's to fill, a slot without it the composer's. The ``.`` is outside
#: :data:`SLOT_NAME`, which is what keeps the two unmistakable for one another —
#: the class is not widened to admit it.
DYNAMIC_PREFIX = "dyn."

#: One slot, whole, namespace included: the group is the slot's spelling, and the
#: spelling is what every mapping below is keyed by. Anything else between the
#: delimiters is not a slot and falls to the stray-marker check.
SLOT = re.compile(rf"@!((?:{re.escape(DYNAMIC_PREFIX)})?{SLOT_NAME})!@")

#: Either delimiter, matched on a line the well-formed slots have been removed
#: from — what is left is an opener with no closer, a closer with no opener, or
#: a marker whose name is not a slot name.
_DELIMITER = re.compile(r"@!|!@")

# The naming convention: `<name>.wave.tmpl` / `<name>.single.tmpl`. It is a
# convention the lint reads, never a resolution rule — the step table names
# every template it uses, so there are no magic filenames.
WAVE_INFIX = ".wave."

# Every WAVE template carries all three, and composition fails without them:
# the most-observed failure class is structurally impossible to omit, not
# merely available to include.
RESERVED_WAVE_SLOTS: tuple[str, ...] = ("dispatch-discipline", "envelope-contract", "deviation-contract")

# The shared fragments, by slot name. A template names the slot; the composer
# loads the file and supplies the rendered body.
#
# `write-op-contract` is the agent-side half of the write-op contract: the
# values-file discipline and the three exit codes a distiller reads for itself.
# Exit 8 lands in the calling agent's own Bash result and never reaches the
# driver, so the retry rule has to be in the brief body — and in exactly one of
# them, injected here rather than restated by every template that names a write
# op.
#
# It is not the only statement of that contract. The `kb-metadata-write` chunk
# of `templates/shared-chunks.toml` states it for the two seats that write
# metadata without a brief — the duplication is deliberate: different readers,
# separately worded. What is jointly asserted of the two bodies — the
# values-file transport, exit 7 and 8, the re-run-identical rule and its bound
# of three — is `tests/test_write_op_contract_sources.py`, which is the only
# thing naming both. Editing that fragment out of one of those facts fails
# there.
FRAGMENT_SLOTS: tuple[str, ...] = (
    "dispatch-discipline",
    "envelope-contract",
    "deviation-contract",
    "verdict-contract",
    "return-contract",
    "scratch-layout",
    "persist-members",
    "write-op-contract",
)

#: The caller-selected alternatives, by the slot each is a choice for. A template
#: carries an ordinary bare slot that knows nothing about the choice, and the
#: caller names one of that slot's registered alternatives — which is what keeps
#: a conditional out of a template body and the chosen prose out of the code that
#: chooses. ``None`` is a registered choice where the slot's absence is itself an
#: answer: a first ask carries no correction, and the slot fills with nothing.
#: Today all three slots are :mod:`kb_tools.kb_claimgraph.ask`'s.
#:
#: ``correction`` is **shared** by both of that module's asks, which is why
#: C-inf's per-claim re-ask registers a slot of its own rather than tailoring
#: that fragment: an edit there would change the other stage's re-ask.
ALTERNATIVE_SLOTS: Mapping[str, tuple[str | None, ...]] = MappingProxyType(
    {
        "correction": (None, "ask-correction"),
        "display-maths": ("identify-display-maths", "identify-no-display-maths"),
        "claim-evidence": (
            "identify-reask-nowhere",
            "identify-reask-several",
            "identify-reask-disagreed",
        ),
    }
)

#: Every alternative that ships, flattened out of the registry above so the two
#: cannot disagree about what exists.
ALTERNATIVE_NAMES: tuple[str, ...] = tuple(
    dict.fromkeys(name for choices in ALTERNATIVE_SLOTS.values() for name in choices if name is not None)
)


def _fragment_file(name: str) -> str:
    """One fragment's path, relative to the templates directory.

    Derived rather than listed, so a file and its registration cannot drift
    apart and a renamed file is a renamed slot by construction.
    """
    return f"{FRAGMENTS_DIRNAME}/{name}{TEMPLATE_SUFFIX}"


#: Slot name → the file the composer fills it from.
FRAGMENTS: Mapping[str, str] = MappingProxyType({slot: _fragment_file(slot) for slot in FRAGMENT_SLOTS})

#: Alternative name → the file the composer resolves it to. A caller names the
#: alternative and never the path: the directory is this module's knowledge, and
#: a caller that spelled one would be a second place a move has to reach.
ALTERNATIVES: Mapping[str, str] = MappingProxyType({name: _fragment_file(name) for name in ALTERNATIVE_NAMES})

#: The one slot a template fills from the calling row rather than from the
#: driver's constants. Named here because the composer is what refuses a row
#: that cannot fill it; :class:`RowSlots` carries the value. No shipped
#: template names it today — the one-member-collapse fragment that did went
#: with the last ``wave*`` row — and it stays because the seat a row dispatches
#: is the row's to state and a template's never to spell.
SEAT_SLOT = "seat"

# The re-ask's brief and captures carry this suffix, so the two asks are
# distinguishable on disk and neither overwrites the other's evidence. It lives
# here rather than beside the re-ask policy because it is part of the brief
# *filename* grammar, which `persist` writes and `step_of` reads back — one
# module owning both directions of one format.
REASK_SUFFIX = "-reask"

_NO_VALUES: Mapping[str, str] = MappingProxyType({})
_NO_CHOICES: Mapping[str, str | None] = MappingProxyType({})


@dataclass(frozen=True, kw_only=True)
class RowSlots:
    """What the calling row contributes to the composer's pool, and the id a refusal names.

    ``seat`` is the seat that row dispatches. A row that dispatches none carries
    ``None`` — the review waves fan out to the seats their member table names —
    and a template naming ``@!seat!@`` is then refused rather than rendered,
    because ``None`` in a dispatched prompt reads as a seat name.
    """

    step_id: str = ""
    seat: str | None = None


_NO_ROW = RowSlots()


# --- loading and slot discovery ---------------------------------------------


def template_paths(directory: Path = PROMPT_TEMPLATES_DIR) -> tuple[Path, ...]:
    """Every ``*.tmpl`` under ``directory``, fragments included, sorted. Empty is a legal answer.

    The fragments are in because every sweep over the shipped bodies — the lint
    below, and the checks over what a model is shown — asks a question about
    prose, and a fragment's prose reaches a model exactly as a template's does.
    Nothing is made dispatchable by being listed here: a row dispatches the
    template its own ``template`` field names, and the step table names no
    fragment.
    """
    if not directory.is_dir():
        return ()
    return tuple(sorted(directory.rglob(f"*{TEMPLATE_SUFFIX}")))


def load(name: str, *, directory: Path = PROMPT_TEMPLATES_DIR) -> str:
    """Read one template. A named-but-absent template is a driver defect (exit 15)."""
    path = directory / name
    runlog.require(path.is_file(), f"prompt template not found: {name}", template=str(path))
    return path.read_text(encoding="utf-8")


def slots_of(text: str, *, source: str) -> tuple[str, ...]:
    """The ordered, deduplicated slot spellings in ``text``, namespace included.

    Spellings rather than names, because the spelling is what routes: a
    ``dyn.``-prefixed entry and a bare one are two different slots even where the
    name after the prefix is the same, and every mapping the composer builds is
    keyed the way the body spells it.

    A delimiter left over once the well-formed markers are taken out is a
    template defect and is refused by line: an opener with no closer, a closer
    with no opener, or a name the slot grammar does not admit. Nothing else in
    a body is syntax, so a brace, a dollar sign or a backslash needs no
    escaping and none is offered.
    """
    stray = [number for number, line in enumerate(text.splitlines(), start=1) if _DELIMITER.search(SLOT.sub("", line))]
    runlog.require(
        not stray,
        f"{source}: stray slot delimiter on line(s) {', '.join(str(number) for number in stray)}; "
        f"every @! opens an @!slot!@ and every !@ closes one",
        template=source,
    )
    names: list[str] = []
    for match in SLOT.finditer(text):
        if match.group(1) not in names:
            names.append(match.group(1))
    return tuple(names)


def _fill(text: str, values: Mapping[str, str]) -> str:
    """Substitute every marker from ``values``, once. A filled value is never rescanned.

    The replacement is a function rather than a template string so that no
    backslash in a value is read as a group reference — a corpus of mathematics
    makes ``\\frac`` the ordinary case, not the edge.
    """
    return SLOT.sub(lambda match: values[match.group(1)], text)


# --- composition ------------------------------------------------------------


def _chosen(name: str, *, fields: Iterable[str], alternatives: Mapping[str, str | None]) -> dict[str, str | None]:
    """The file each alternative slot takes: one per declared slot, and no more.

    Strict in both directions like every other part of composition, and for the
    same reason — an unanswered choice would otherwise fill a section of a
    dispatched prompt with nothing, silently.
    """
    declared = [field for field in fields if field in ALTERNATIVE_SLOTS]
    unanswered = sorted(set(declared) - alternatives.keys())
    runlog.require(
        not unanswered,
        f"{name}: no alternative chosen for slot(s): {', '.join(unanswered)}",
        template=name,
    )
    stray = sorted(alternatives.keys() - set(declared))
    runlog.require(
        not stray,
        f"{name}: alternative(s) chosen for slot(s) the template does not declare: {', '.join(stray)}",
        template=name,
    )
    files: dict[str, str | None] = {}
    for field in declared:
        pick = alternatives[field]
        runlog.require(
            pick in ALTERNATIVE_SLOTS[field],
            f"{name}: @!{field}!@ takes one of "
            f"{', '.join('nothing' if choice is None else repr(choice) for choice in ALTERNATIVE_SLOTS[field])}, "
            f"not {pick!r}",
            template=name,
        )
        files[field] = None if pick is None else ALTERNATIVES[pick]
    return files


def render(
    name: str,
    *,
    slots: Mapping[str, str],
    constants: Mapping[str, str] = _NO_VALUES,
    alternatives: Mapping[str, str | None] = _NO_CHOICES,
    row: RowSlots = _NO_ROW,
    wave: bool = False,
    directory: Path = PROMPT_TEMPLATES_DIR,
) -> str:
    """Compose one brief. Every declared slot filled, every supplied value used.

    ``slots`` fills the ``@!dyn.<name>!@`` slots and nothing else, keyed by the
    bare name: the namespace is the composer's to spell, never a caller's.
    ``alternatives`` names one registered choice per alternative slot the
    template declares.

    ``wave`` says the call unit is a WAVE, which is the step table's knowledge,
    not the filename's: a one-member ``wave*`` step collapses to a SINGLE,
    and a SINGLE composed from a wave template still needs its discipline.
    """
    text = load(name, directory=directory)
    fields = slots_of(text, source=name)

    if wave:
        missing_reserved = [slot for slot in RESERVED_WAVE_SLOTS if slot not in fields]
        runlog.require(
            not missing_reserved,
            f"{name}: WAVE template omits reserved slot(s): {', '.join(missing_reserved)}",
            template=name,
        )

    # Resolve the spliced bodies first: their own slots join the required set. A
    # fragment's file is fixed by the slot's name, an alternative's is the
    # caller's choice among the ones that slot registers, and past this point
    # the two are one thing.
    chosen = _chosen(name, fields=fields, alternatives=alternatives)
    spliced_fields: dict[str, tuple[str, ...]] = {}
    spliced_text: dict[str, str] = {}
    required: set[str] = set()
    for field in fields:
        if field in FRAGMENTS:
            source: str | None = FRAGMENTS[field]
        elif field in chosen:
            source = chosen[field]
        else:
            required.add(field)
            continue
        if source is None:
            spliced_fields[field] = ()
            spliced_text[field] = ""
            continue
        body = load(source, directory=directory)
        inner = slots_of(body, source=source)
        # The refusal is against the union of both expandable vocabularies, in
        # every direction: a fragment or an alternative naming either kind needs
        # a third expansion pass, and there are two.
        nested = [slot for slot in inner if slot in FRAGMENTS or slot in ALTERNATIVE_SLOTS]
        runlog.require(
            not nested,
            f"{name}: @!{field}!@ resolves to {source}, whose body names "
            f"{', '.join(f'@!{slot}!@' for slot in nested)} — a slot the composer would have to expand in turn. "
            f"Composition expands one level: fill the inner slot from the caller (@!{DYNAMIC_PREFIX}<name>!@) "
            f"or name it in the template beside this one instead of inside this body",
            template=source,
        )
        spliced_fields[field] = inner
        spliced_text[field] = body
        required.update(inner)

    dynamic = {field.removeprefix(DYNAMIC_PREFIX) for field in required if field.startswith(DYNAMIC_PREFIX)}
    static = {field for field in required if not field.startswith(DYNAMIC_PREFIX)}

    pool: dict[str, str] = {key: constants[key] for key in static if key in constants}
    if SEAT_SLOT in static:
        seat = row.seat
        runlog.require(
            bool(seat),
            f"{name}: step {row.step_id} carries no seat, so @!{SEAT_SLOT}!@ has nothing to name",
            template=name,
            step=row.step_id,
        )
        pool[SEAT_SLOT] = seat

    unsupplied = sorted(dynamic - slots.keys())
    runlog.require(
        not unsupplied,
        f"{name}: the caller supplied no value for @!{DYNAMIC_PREFIX}…!@ slot(s): {', '.join(unsupplied)}",
        template=name,
    )
    # Every bare slot this template declares, the spliced ones included: a
    # caller handing prose to a slot the composer splices a body into is the
    # same miswiring as one shadowing a constant, and reads as the same mistake.
    misrouted = sorted(slots.keys() & (static | spliced_text.keys()))
    runlog.require(
        not misrouted,
        f"{name}: the caller supplied slot(s) the composer fills: {', '.join(misrouted)}; "
        f"a caller fills @!{DYNAMIC_PREFIX}…!@ slots and no others",
        template=name,
    )
    unused = sorted(slots.keys() - dynamic)
    runlog.require(not unused, f"{name}: supplied slot(s) the template never uses: {', '.join(unused)}", template=name)
    unfilled = sorted(static - pool.keys())
    runlog.require(not unfilled, f"{name}: unfilled slot(s): {', '.join(unfilled)}", template=name)

    values: dict[str, str] = {f"{DYNAMIC_PREFIX}{key}": value for key, value in slots.items()}
    values.update(pool)

    filled = {field: values[field] for field in fields if field not in spliced_text}
    for slot, body in spliced_text.items():
        filled[slot] = _fill(body, {key: values[key] for key in spliced_fields[slot]})
    return _fill(text, filled)


def persist(briefs_dir: Path, *, seq: int, step_id: str, text: str) -> Path:
    """Write the composed brief to ``<run-dir>/briefs/<seq>-<step>.md``.

    Every brief is on disk before anything is spawned: evidence,
    reproducibility, and the file-reference transport fallback in one act. A
    brief never travels as an argv value — a wave-scale brief carrying a full
    member table fails as a mystery ``E2BIG`` otherwise.
    """
    runlog.require(briefs_dir.is_dir(), "brief directory does not exist", directory=str(briefs_dir))
    path = briefs_dir / f"{seq:03d}-{step_id}.md"
    path.write_text(text if text.endswith("\n") else text + "\n", encoding="utf-8")
    _log.debug("brief written", extra={"context": {"step": step_id, "brief": str(path)}})
    return path


def step_of(brief_path: Path) -> str:
    """The step id a persisted brief's filename names — :func:`persist`'s grammar, read back.

    The inverse, and the reason it is a function rather than a caller's split:
    a step id contains hyphens (``pre.revision-entry``) and so does the re-ask
    marker, so the only reading that cannot confuse the two is one that knows
    both halves of the grammar. A caller matching a step id as a substring of
    the stem would match one row's id inside another's.
    """
    _, separator, body = brief_path.stem.partition("-")
    runlog.require(bool(separator), "not a persisted brief filename", brief=brief_path.name)
    return body[: -len(REASK_SUFFIX)] if body.endswith(REASK_SUFFIX) else body


# --- the template lint -------------------------------------------------------


def lint(paths: Iterable[Path], *, prohibited: Mapping[str, re.Pattern[str]]) -> list[str]:
    """Static check over template files. An empty list is a pass.

    Three findings, in file order: a template spelling something ``prohibited``
    names — the driver's own business (a stage id, the record verb, the
    ledger's op flag) or a metadata marker the write API alone composes;
    a template carrying a delimiter that does not parse as a slot; and a
    ``*.wave.tmpl`` missing one of the reserved wave slots.

    ``prohibited`` is injected rather than imported, and the finding reports
    the label rather than a reason: both vocabularies are the caller's, and
    this module holds neither stage nor metadata knowledge.
    """
    findings: list[str] = []
    for path in paths:
        text = path.read_text(encoding="utf-8")
        for number, line in enumerate(text.splitlines(), start=1):
            findings += [
                f"{path.name}:{number}: names {label!r} — prohibited in a prompt template"
                for label, pattern in prohibited.items()
                if pattern.search(line)
            ]
        try:
            fields = slots_of(text, source=path.name)
        except runlog.BoundaryError as exc:
            findings.append(f"{path.name}: {exc}")
            continue
        if WAVE_INFIX in path.name:
            findings += [
                f"{path.name}: WAVE template omits reserved slot {slot!r}"
                for slot in RESERVED_WAVE_SLOTS
                if slot not in fields
            ]
    return findings
