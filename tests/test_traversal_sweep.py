"""The traversal-obligation sweep over the rendered KB agent set.

Graph traversal sits off the inference side: coverage, tiling, containment, the
tree diff and reachability are the validator's, proved and logged.
That no definition still *asks a seat* to perform one of those walks is an
exhaustiveness claim, and an exhaustiveness claim with no mechanical check is a
wish. This file is that check.

**How it reads.** Every line of every rendered ``kb-*`` definition is matched
against :data:`PATTERNS` — the four traversal-obligation shapes. Each hit
must match exactly one entry of :data:`JUDGMENT_SITES`, whose value says why the
site's object is a *judgment* (does this index distinguish its children?) rather
than a *resolution* (does this link resolve?). A new theater site fails by not
being in the map; a site that leaves fails by leaving its entry stale.

**Two stated bounds.**

*Scope*: the KB agent set, rendered — the definitions a KB build dispatches, and
the only ones that can carry an obligation over the KB's link graph. The same
verbs elsewhere in ``agents/`` name other objects entirely (a derivation chain, a
doc's reading path, ownership flow), and sweeping them would grow an allowlist
that says nothing about this claim.

*Rendering*: definitions are rendered in memory from ``templates/``, not read out
of ``rendered/``, which is a gitignored build product that may be absent or stale.
The templates are the source the render is a function of, so the sweep sees what
the next ``just generate`` would write.

The sweep's first run found one resolution-object site — the taxonomy
architect's Review Mode navigability item — and held it in an ``OPEN_FINDINGS``
map asserted as *still present*, so that fixing it turned this file red. It is
fixed and the map is gone with it. A finding held that way again is
restored from history complete with its still-present assertion: an exemption
map that nothing asserts against would let a site out of :data:`JUDGMENT_SITES`
silently, which is the failure this sweep exists to prevent.
"""

import re
from collections.abc import Iterator
from functools import cache
from importlib import util
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parent.parent
_spec = util.spec_from_file_location("gen_defs", _REPO_ROOT / "gen-defs.py")
gen_defs = util.module_from_spec(_spec)
_spec.loader.exec_module(gen_defs)


#: The four shapes. ``follow`` is matched only with ``links``
#: in reach — bare "follow" is ordinary English ("follow the canonical
#: direction") and matching it would drown the map in prose.
PATTERNS = {
    "trace": re.compile(r"\btrac(?:e|es|ed|ing)\b", re.IGNORECASE),
    "walk": re.compile(r"\bwalk(?:s|ed|ing)?\b", re.IGNORECASE),
    "follow-links": re.compile(r"\bfollow(?:s|ed|ing)?\b[^.!?]{0,80}\blinks?\b", re.IGNORECASE),
    "starting-from-any": re.compile(r"starting from any", re.IGNORECASE),
}

#: One specimen per shape, so that retiring the last shipped site of a shape
#: retires neither its pattern nor the proof that the pattern fires. Two shapes
#: have no site in the shipped set today: ``follow-links`` never had one, and
#: ``starting-from-any`` had exactly one — the taxonomy architect's navigability
#: item, since fixed and then retired with that definition.
PATTERN_SPECIMENS = {
    "trace": "Trace random paths, don't just inspect link lists.",
    "walk": "Walk every up-link in the tree and confirm each one resolves.",
    "follow-links": "Follow the down-links from each index to its children.",
    "starting-from-any": "Starting from any leaf, can you reach entry-point?",
}

#: ``(definition, phrase) -> why the object is judgment``. The phrase is matched
#: case-insensitively against the hit line and must identify it alone.
JUDGMENT_SITES: dict[tuple[str, str], str] = {
    (
        "kb-claim-scorer.md",
        "must trace from stated axioms",
    ): "a derivation chain inside one cited result, not a route through the KB's link graph — "
    "whether the stated steps carry the conclusion is the judgment this seat exists to make",
    (
        "kb-docent.md",
        "trace the solidity of the chain",
    ): "the chain is resolved by the query CLI (`deps <clm-id>`); the seat reads the returned "
    "weakest link and judges what it means for the user's derivation",
    (
        "kb-maintainer.md",
        "walk-back annotations",
    ): "a noun — an author-adjudication marker to preserve verbatim. No traversal is asked for",
}


@cache
def _rendered_kb_definitions() -> tuple[tuple[str, str], ...]:
    """Every ``kb-*`` agent definition, rendered in memory under the shipped triple."""
    tier_map = dict(gen_defs.DEFAULT_PIN_MAP)
    tuning = gen_defs.Tuning(
        family=gen_defs.FAMILY_DIR / "claude.toml",
        tier_map=tier_map,
        pin_map=dict(gen_defs.DEFAULT_PIN_MAP),
        stock=gen_defs.stock_tiers({}, tier_map),
        is_default=True,
    )
    binding = gen_defs.tier_binding(gen_defs.load_chunks(), pin_map=dict(gen_defs.DEFAULT_PIN_MAP))
    renders = gen_defs.all_renders(binding, gen_defs.surface_map(gen_defs.REPO_ROOT), tuning=tuning)
    return tuple(sorted((target.name, text) for target, text in renders if target.name.startswith("kb-")))


def _hits(definitions: tuple[tuple[str, str], ...]) -> Iterator[tuple[str, int, str, tuple[str, ...]]]:
    """``(definition, line number, line, the patterns it matched)`` for every hit.

    Takes the definitions rather than reading them, so the same sweep runs over
    the shipped set and over a planted one — which is what makes its teeth
    testable instead of assumed.
    """
    for name, text in definitions:
        for number, line in enumerate(text.splitlines(), start=1):
            matched = tuple(sorted(key for key, pattern in PATTERNS.items() if pattern.search(line)))
            if matched:
                yield name, number, line, matched


def _entries_matching(name: str, line: str) -> list[tuple[str, str]]:
    lowered = line.lower()
    return [key for key in JUDGMENT_SITES if key[0] == name and key[1].lower() in lowered]


def _unclassified(definitions: tuple[tuple[str, str], ...]) -> list[str]:
    """Every traversal site with no entry behind it, as report lines."""
    return [
        f"{name}:{number} [{','.join(keys)}] {line.strip()}"
        for name, number, line, keys in _hits(definitions)
        if not _entries_matching(name, line)
    ]


def test_the_sweep_can_see() -> None:
    """A sweep over nothing passes vacuously; this is what says it did not.

    The definition count is a floor rather than a fixture: the KB agent set is
    three definitions today and a fourth must not require editing this line. The
    shapes asserted here are the ones the shipped set still carries; every shape
    is proved to fire by :func:`test_every_shape_still_fires`, so a shipped site
    going away is a template change and not a hole in the sweep.
    """
    definitions = _rendered_kb_definitions()
    matched = {key for _, _, _, keys in _hits(definitions) for key in keys}

    assert len(definitions) >= 3, [name for name, _ in definitions]
    assert all(text.strip() for _, text in definitions)
    assert {"trace", "walk"} <= matched


def test_every_shape_still_fires() -> None:
    """Each pattern against a specimen of the shape it exists to catch.

    A shape with no shipped site is matched by nothing in a green run, which
    makes a pattern that stopped compiling to what it means indistinguishable
    from a surface that is simply clean.
    """
    assert PATTERN_SPECIMENS.keys() == PATTERNS.keys()
    assert [key for key, specimen in PATTERN_SPECIMENS.items() if not PATTERNS[key].search(specimen)] == []


def test_a_planted_theater_site_fails_the_sweep() -> None:
    """The sweep's teeth, over a definition that does not exist.

    Without this, a green run above is equally consistent with a classifier that
    matches everything. The planted line is the retired shape: an exhaustive
    walk of the link graph, asked of a seat.
    """
    planted = (("kb-planted.md", "Walk every up-link in the tree and confirm each one resolves.\n"),)

    assert _unclassified(planted) == [
        "kb-planted.md:1 [walk] Walk every up-link in the tree and confirm each one resolves."
    ]
    # And the judgment map is not a blanket pass: an entry belongs to its own
    # definition, so the same phrase in another file is still unclassified.
    assert _unclassified((("kb-planted.md", "a literal sentence walk\n"),))


def test_every_traversal_site_is_judgment_or_a_named_finding() -> None:
    """The exhaustiveness claim itself: no unclassified traversal obligation ships."""
    unclassified = _unclassified(_rendered_kb_definitions())

    assert unclassified == [], (
        "a traversal verb reached a rendered definition with no entry in this file. Read the "
        "site: if its object is a judgment, add it to JUDGMENT_SITES with the reason; if its "
        "object is resolution, it is theater the toolchain already proves — report it and fix "
        "the template.\n" + "\n".join(unclassified)
    )


def test_no_traversal_site_matches_two_entries() -> None:
    """One hit, one reason. An ambiguous phrase makes both entries unfalsifiable."""
    ambiguous = [
        f"{name}:{number} matches {[key[1] for key in _entries_matching(name, line)]}"
        for name, number, line, _ in _hits(_rendered_kb_definitions())
        if len(_entries_matching(name, line)) > 1
    ]

    assert ambiguous == []


@pytest.mark.parametrize("key", sorted(JUDGMENT_SITES))
def test_no_entry_is_stale(key: tuple[str, str]) -> None:
    """An entry whose site is gone must go with it, or the map stops describing the surface."""
    name, phrase = key
    assert [hit for hit in _hits(_rendered_kb_definitions()) if hit[0] == name and phrase.lower() in hit[2].lower()], (
        f"no rendered line in {name} matches {phrase!r}. If the text moved, re-judge the site and "
        f"update the entry; if the site is gone, delete the entry."
    )
