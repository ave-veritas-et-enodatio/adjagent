"""Every reference the toolchain's model-facing text makes has to resolve.

**Population.** The two bodies this toolchain composes and puts in front of a
model: the rendered stage cards of every ``kb_pipeline.STAGES`` entry, and the
composed brief of every shipped ``kb_driver/prompt-templates/`` template a step-table row
names. Both are rendered here the way a run renders them — cards through
``card_lines``, briefs through ``prompt_templates.render`` with the row's slots filled by
named placeholders — because what a seat reads is the filled body, and a raw
read of either source would judge text nobody receives. ``kb_pipeline``'s
refusals compose their paths, ops and commands from the same constants the
cards do and then render the stage's card beneath, so they carry no reference
of their own.

**Extraction is per-kind and anchored.** Each check below looks only where the
text marks a reference as that kind — the scratch root in front of a path, the
sanctioned invocation in front of an op, a backticked runner call, the word
``cap`` in front of an integer, the ``kb-`` namespace. A permissive extractor
that fired on prose merely resembling a path would be suppressed, and then it
would protect nothing.

**What it deliberately does not see.** A path carrying neither the scratch root
in front of it nor ``.md`` on the end (``review/`` alone). A runner target named
outside backticks. A seat named in free prose outside the ``kb-`` namespace — no
rule tells ``applied-mathematician`` from ``warrant-mapping`` without a
hand-maintained list, so that direction is covered instead by holding the step
table's own seats against the shipped definitions. Anything inside an angle
bracket, which is a placeholder rather than a reference.

Vocabularies are harvested from the code, never enumerated here: a list of
today's references would be the same hand-maintained text one layer down.
"""

import argparse
import re
from importlib import util as importlib_util
from pathlib import Path, PurePosixPath

import pytest

from kb_tools import kb_index_lib, kb_pipeline, kb_util
from kb_tools.kb_driver import prompt_templates, steps

pytestmark = pytest.mark.skip(
    reason="lints a pipeline shape that has not yet completed a run end to end; re-enable once it has"
)

_AGENTS_SURFACE = Path(__file__).resolve().parent.parent.parent
_AGENT_TEMPLATES = _AGENTS_SURFACE / "templates" / "agents"
_GEN_DEFS = _AGENTS_SURFACE / "gen-defs.py"

# A package resource, so ``__file__`` is the right anchor: the fragment ships
# beside the modules it wraps, wherever kb_tools was installed.
_RUNNER_FRAGMENT = Path(kb_util.__file__).resolve().parent / "runner-snippets" / "kb.just"


# ---------------------------------------------------------------------------
# The population
# ---------------------------------------------------------------------------


def _population() -> dict[str, str]:
    """Every model-facing body the toolchain composes, keyed by where it comes from."""
    texts = {
        f"card:{stage.id}": "\n".join(kb_pipeline.card_lines(stage, _AGENTS_SURFACE)) for stage in kb_pipeline.STAGES
    }
    shipped = {path.name for path in prompt_templates.template_paths()}
    for step in steps.STEPS:
        if step.template is None or step.template not in shipped:
            continue
        texts[f"brief:{step.id}"] = prompt_templates.render(
            step.template,
            slots={slot: f"<{slot}>" for slot in step.slots},
            constants=steps.CONSTANT_SLOTS,
            row=prompt_templates.RowSlots(step_id=step.id, seat=step.seat),
            wave=step.unit is steps.Unit.WAVE,
        )
    return texts


POPULATION = _population()
POPULATION_IDS = sorted(POPULATION)


# ---------------------------------------------------------------------------
# The vocabularies, harvested
# ---------------------------------------------------------------------------

_VOCABULARY_MODULES = (kb_util, kb_pipeline, kb_index_lib, steps)

_KB_WORD = re.compile(r"kb-[a-z0-9]+(?:-[a-z0-9]+)*")


def _published_strings() -> frozenset[str]:
    """Every module-level string the toolchain publishes, string containers flattened.

    The dataclass tables (``STAGES``, ``STEPS``) hold no bare strings, so none of
    their prose arrives here: what a module states as a constant is what
    something else may resolve against.
    """
    found: set[str] = set()
    for module in _VOCABULARY_MODULES:
        for name, value in vars(module).items():
            if name.startswith("__"):
                continue
            if isinstance(value, str):
                found.add(value)
            elif isinstance(value, (tuple, list, set, frozenset)):
                found.update(item for item in value if isinstance(item, str))
    return frozenset(found)


PUBLISHED_STRINGS = _published_strings()


def _declared_seats() -> frozenset[str]:
    """Every seat the step table can dispatch — row seats plus the meta-review roster."""
    return frozenset({step.seat for step in steps.STEPS if step.seat} | {steps.META_REVIEW_SEAT})


def _published_kb_words() -> frozenset[str]:
    """Every ``kb-`` word the toolchain publishes, seats included."""
    return frozenset(word for value in PUBLISHED_STRINGS for word in _KB_WORD.findall(value)) | _declared_seats()


def _published_documents() -> frozenset[str]:
    """The basename of every ``.md`` a published constant names."""
    return frozenset(PurePosixPath(value).name for value in PUBLISHED_STRINGS if value.endswith(".md"))


def _declared_subcommands() -> frozenset[str]:
    """The op vocabulary the CLI actually declares, read off its own parser."""
    parser = kb_util.build_parser()
    groups = [action for action in parser._actions if isinstance(action, argparse._SubParsersAction)]
    assert len(groups) == 1, "kb_util no longer declares exactly one subcommand group"
    return frozenset(groups[0].choices)


def _consumer_targets() -> frozenset[str]:
    """The consumer-side runner targets, read off the shipped justfile fragment."""
    return frozenset(re.findall(r"^([a-z][a-z0-9-]*):", _RUNNER_FRAGMENT.read_text(encoding="utf-8"), re.MULTILINE))


def _published_caps() -> frozenset[int]:
    """Every loop cap the pipeline states, the same constants the cards render from."""
    return frozenset(
        value for name, value in vars(kb_pipeline).items() if name.endswith("_CAP") and isinstance(value, int)
    )


# ---------------------------------------------------------------------------
# The extractors
# ---------------------------------------------------------------------------

_SCRATCH_PATH = re.compile(rf"{re.escape(steps.SCRATCH_ROOT)}/(\S*)")
_TRAILING_PUNCTUATION = re.compile(r"[.,;:)\]}`\"']+$")
_PLACEHOLDER = re.compile(r"<[^<>]*>")
_DOCUMENT = re.compile(r"(?<![\w.<-])([A-Za-z0-9][\w.-]*\.md)(?![\w-])")
_RUNNER_CALL = re.compile(r"`(?:just|make)\s+([^`\s]+)")
_CAP = re.compile(r"\bcap\s+(\d+)\b")
_NAMESPACED = re.compile(r"(?<![\w<-])(kb-[a-z0-9-]*[a-z0-9])")


def _scratch_paths(text: str) -> list[str]:
    """Every token the scratch root introduces, minus the bare root itself."""
    found = (_TRAILING_PUNCTUATION.sub("", match.group(1)) for match in _SCRATCH_PATH.finditer(text))
    return [path for path in found if path]


def _documents(text: str) -> list[str]:
    """Every ``.md`` basename named outside a scratch-rooted path."""
    return _DOCUMENT.findall(_SCRATCH_PATH.sub(" ", text))


def _ops(text: str) -> list[str]:
    """Every token the sanctioned invocation is followed by."""
    return re.findall(rf"{re.escape(kb_util.INVOCATION)}\s+(\S+)", text)


def _layout_pattern(entry: str) -> re.Pattern[str]:
    """One layout entry as a pattern, each ``<slot>`` free to carry a value or stay a slot.

    A path that has bound a slot to a real value still names the layout entry it
    came from, so binding one is not drift; landing in a directory or under a
    filename shape the layout does not hold is.
    """
    return re.compile("[^/]+".join(re.escape(part) for part in _PLACEHOLDER.split(entry)))


LAYOUT_PATTERNS = tuple(_layout_pattern(entry) for entry in steps.SCRATCH_LAYOUT)


def _in_layout(path: str) -> bool:
    return any(pattern.fullmatch(path) for pattern in LAYOUT_PATTERNS)


def _resolves(word: str, known: frozenset[str]) -> bool:
    """``word`` is a published one, or a compound extending one at a hyphen boundary.

    The extension arm keeps ``kb-root-relative`` — an adjective built on a
    published word — out of the findings, without a list of prose exceptions
    that would drift exactly the way the text it guards does.
    """
    return any(word == item or word.startswith(f"{item}-") for item in known)


# ---------------------------------------------------------------------------
# The checks
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("source", POPULATION_IDS)
def test_every_scratch_path_named_resolves_in_the_layout(source: str) -> None:
    """A build artifact goes where the driver looks for it, or the reference is dead."""
    unresolved = [path for path in _scratch_paths(POPULATION[source]) if not _in_layout(path)]

    assert not unresolved, f"{source}: no SCRATCH_LAYOUT entry covers {unresolved}"


@pytest.mark.parametrize("source", POPULATION_IDS)
def test_every_document_named_is_one_a_constant_names(source: str) -> None:
    documents = _published_documents()
    unresolved = [name for name in _documents(POPULATION[source]) if name not in documents]

    assert not unresolved, f"{source}: no published constant names {unresolved}"


@pytest.mark.parametrize("source", POPULATION_IDS)
def test_every_op_named_is_a_declared_subcommand(source: str) -> None:
    """A rename that misses a renderer advertises an op the CLI would refuse."""
    declared = _declared_subcommands()
    unresolved = [op for op in _ops(POPULATION[source]) if op not in declared]

    assert not unresolved, f"{source}: kb_util declares no subcommand {unresolved}"


@pytest.mark.parametrize("source", POPULATION_IDS)
def test_every_runner_target_named_is_one_the_fragment_defines(source: str) -> None:
    targets = _consumer_targets()
    unresolved = [target for target in _RUNNER_CALL.findall(POPULATION[source]) if target not in targets]

    assert not unresolved, f"{source}: the shipped runner fragment defines no target {unresolved}"


@pytest.mark.parametrize("source", POPULATION_IDS)
def test_every_cap_stated_is_a_published_cap(source: str) -> None:
    caps = _published_caps()
    unresolved = [value for value in _CAP.findall(POPULATION[source]) if int(value) not in caps]

    assert not unresolved, f"{source}: no cap constant holds {unresolved}"


@pytest.mark.parametrize("source", POPULATION_IDS)
def test_every_namespaced_word_resolves_to_a_constant_or_a_seat(source: str) -> None:
    """The ``kb-`` namespace is where this toolchain's seats and tokens live.

    A seat rename, a target rename or a typo lands here: the stale word extends
    no published one, so it resolves against nothing.
    """
    known = _published_kb_words()
    unresolved = [word for word in _NAMESPACED.findall(POPULATION[source]) if not _resolves(word, known)]

    assert not unresolved, f"{source}: {unresolved} is neither a published kb_tools word nor a seat the table holds"


def test_every_seat_the_step_table_holds_is_a_shipped_definition() -> None:
    """The seat a brief dispatches comes from the row; this is what makes the row true.

    Templates spell no seat and cards name only the ``kb-`` ones, so a seat that
    stopped existing would otherwise reach a wave as an agent type nothing
    defines. The names come from the generator's own reader, since a template may
    declare several outputs and none of them has to be its stem.
    """
    assert _AGENT_TEMPLATES.is_dir(), f"no agent template tree at {_AGENT_TEMPLATES}"
    spec = importlib_util.spec_from_file_location("gen_defs", _GEN_DEFS)
    assert spec is not None and spec.loader is not None
    gen_defs = importlib_util.module_from_spec(spec)
    spec.loader.exec_module(gen_defs)

    shipped = {name for template in _AGENT_TEMPLATES.rglob("*.md.tmpl") for name in gen_defs.split_outputs(template)[0]}

    missing = sorted(_declared_seats() - shipped)

    assert not missing, f"the step table seats {missing}, which no template renders"


@pytest.mark.parametrize("stage", kb_pipeline.STAGES, ids=[stage.id for stage in kb_pipeline.STAGES])
def test_no_plain_card_obligation_spells_a_cap(stage: kb_pipeline.Stage) -> None:
    """The check above says a stated cap is *a* cap; this says nobody typed one.

    A cap is right on the day it is written, so a hand-spelled one goes stale in
    silence. ``CappedLine`` is what a card obligation naming a cap must be.
    """
    spelled = [item for item in stage.card if isinstance(item, str) and _CAP.search(item)]

    assert not spelled, f"{stage.id}: render the cap from its constant instead of spelling it — {spelled}"


@pytest.mark.parametrize("template", prompt_templates.template_paths(), ids=lambda path: path.name)
def test_no_brief_template_spells_a_cap(template: Path) -> None:
    """The brief's half of the same rule: a row's cap reaches it through a slot."""
    body = template.read_text(encoding="utf-8")

    spelled = _CAP.findall(body)

    assert not spelled, f"{template.name}: take the cap from the row's slot — cap {spelled}"


def test_no_extractor_is_vacuous() -> None:
    """A refactor that changed the text's shape could empty every check silently.

    A floor, not a census: what matters is that each extractor still sees the
    population, not how many references the population happens to hold.
    """
    whole = "\n".join(POPULATION.values())
    seen = {
        "scratch path": _scratch_paths(whole),
        "document": _documents(whole),
        "op": _ops(whole),
        "runner target": _RUNNER_CALL.findall(whole),
        "cap": _CAP.findall(whole),
        "namespaced word": _NAMESPACED.findall(whole),
    }

    blind = [kind for kind, found in seen.items() if not found]

    assert not blind, f"{blind}: the extractor no longer sees the population it guards"
