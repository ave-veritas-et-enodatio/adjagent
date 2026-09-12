"""Tests for gen-defs.py's pure logic — overlay anchor collection and resolution,
family-file loading and validation (including the required [tiers] table and
the member-reachability rule), family selection, scope resolution (model wins
over family), tier resolution (each output's model scope comes from the tier
its own pin site declares, through the effective tier map), the two map flags'
parse-and-merge semantics, surface-map construction, definition-selection globs
(matching, validation, accounting, and the filtered generate/check paths),
the banner's !TUNING! line, the two body-hash banners
(!GENERATED! and !INSTALLED!) and the backup branches they gate, the
render-to-order generate/check round trip, and full-product install (copy set,
per-filetype banner placement, per-target write safety, the content-only write
path — in-place update, inode and mode preserved, exec bit on creation, backups
as plain content copies — and end-to-end installs of this repository — default,
tuned, and re-installed — whose report names problems only: a clean install
is one summary line).

The script lives at the repo root under a hyphenated name, so it is loaded
here via importlib rather than imported. Filesystem-shaped cases build
scratch template/output trees with tempfile and drive the refactored
functions (surface_map / all_renders / generate / check) against them — the
real templates/ and deployed surfaces are never touched or written.
"""

import contextlib
import io
import os
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import unittest
from importlib import util
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
_spec = util.spec_from_file_location("gen_defs", _REPO_ROOT / "gen-defs.py")
gen_defs = util.module_from_spec(_spec)
_spec.loader.exec_module(gen_defs)


def _quiet(func, *args, **kwargs):
    """Run a printing mode function, discarding its report."""
    with contextlib.redirect_stdout(io.StringIO()):
        return func(*args, **kwargs)


def _tiers_toml(mapping=None):
    """The [tiers] table every family file must carry, as TOML text."""
    mapping = gen_defs.DEFAULT_PIN_MAP if mapping is None else mapping
    return "[tiers]\n" + "".join(f'{tier} = "{mapping[tier]}"\n' for tier in gen_defs.TIERS)


def _tuning(family, *, tier_map=None, pin_map=None, entries=None, is_default=False):
    """A triple for a scratch render. Tier and pin maps default to the same
    claude-shaped map, which is the shipped default family's own state."""
    tier_map = dict(gen_defs.DEFAULT_PIN_MAP if tier_map is None else tier_map)
    return gen_defs.Tuning(
        family=Path(family),
        tier_map=tier_map,
        pin_map=dict(gen_defs.DEFAULT_PIN_MAP if pin_map is None else pin_map),
        stock=gen_defs.stock_tiers(entries or {}, tier_map),
        is_default=is_default,
    )


def _binding(chunks, pin_map=None):
    return gen_defs.tier_binding(chunks, pin_map=dict(gen_defs.DEFAULT_PIN_MAP if pin_map is None else pin_map))


def _shipped_renders(family_name, *, tier_spec=None, pin_spec=None):
    """Every real definition rendered under one tuning triple, in memory.

    The route generate, check and install all take, minus the write: nothing is
    created under the root named here. Returns the triple, the resolved overlay map
    per tier (the None key included — the resolution an output with no pin site
    runs under), and {relative path: rendered text}.
    """
    path = gen_defs.FAMILY_DIR / f"{family_name}{gen_defs.FAMILY_SUFFIX}"
    family = gen_defs.load_family(path)
    tuning = gen_defs.effective_tuning(path, family, tier_spec=tier_spec, pin_spec=pin_spec)
    resolve = gen_defs.tier_resolver(family.entries, tuning.tier_map)
    renders = gen_defs.all_renders(
        _binding(gen_defs.load_chunks(), tuning.pin_map),
        gen_defs.surface_map(gen_defs.REPO_ROOT),
        resolve,
        tuning=tuning,
    )
    return (
        tuning,
        {tier: resolve(tier) for tier in (None, *gen_defs.TIERS)},
        {gen_defs.rel(target): text for target, text in renders},
    )


class TestAnchorCollection(unittest.TestCase):
    def test_anchors_in_finds_overlay_markers(self):
        text = "a @!fam.one!@ b @!chunk!@ c @!fam.two-x!@"
        self.assertEqual(gen_defs.anchors_in(text, where="probe"), {"fam.one", "fam.two-x"})

    def test_anchors_in_ignores_other_markers_and_plain_text(self):
        self.assertEqual(gen_defs.anchors_in('@!x!@ @!y variant="z"!@ overlay', where="probe"), set())

    def test_collect_anchors_spans_templates_and_chunk_bodies(self):
        chunks = {
            "a": {"text": "x @!fam.from-text!@"},
            "b": {"variants": {"v": "@!fam.from-variant!@"}},
            "c": {"text": "t", "defaults": {"k": "@!fam.from-default!@"}},
        }
        with tempfile.TemporaryDirectory() as tmp:
            tmpl = Path(tmp) / "t.md.tmpl"
            tmpl.write_text("---\n---\n@!fam.from-template!@\n")
            found = gen_defs.collect_anchors(chunks, [tmpl])
        self.assertEqual(found, {"fam.from-text", "fam.from-variant", "fam.from-default", "fam.from-template"})


class TestIdentifierClass(unittest.TestCase):
    """IDENTIFIER is the one kebab-case class shared by marker names, namespace
    prefixes, family anchor keys, and chunk-argument keys, built once and
    embedded in every regex site rather than re-spelled at each. The embedding
    test is the one that actually guards against divergence: a test that only
    exercised accept/reject behavior on each regex separately would not catch
    someone re-inlining a slightly different pattern by hand at one of the
    sites."""

    def test_identifier_is_embedded_in_every_site(self):
        self.assertIn(gen_defs.IDENTIFIER, gen_defs.MARKER.pattern)
        self.assertIn(gen_defs.IDENTIFIER, gen_defs.ARG.pattern)
        self.assertIn(gen_defs.IDENTIFIER, gen_defs.ANCHOR_KEY.pattern)

    def test_the_namespace_separator_is_outside_the_class(self):
        # The whole of what keeps a namespace from ever being read as a name:
        # widening IDENTIFIER to admit "." would make `fam.gap` a legal chunk
        # name and the routing unrecoverable.
        self.assertIsNone(re.fullmatch(gen_defs.IDENTIFIER, "fam.gap"))

    def test_every_registered_namespace_is_itself_an_identifier(self):
        for namespace in gen_defs.NAMESPACES:
            with self.subTest(namespace=namespace):
                self.assertIsNotNone(re.fullmatch(gen_defs.IDENTIFIER, namespace))

    def test_refuses_leading_digit_leading_or_trailing_or_doubled_hyphen_and_underscore(self):
        for candidate in ("1abc", "-abc", "abc-", "abc--def", "my_key"):
            with self.subTest(candidate=candidate):
                self.assertIsNone(re.fullmatch(gen_defs.IDENTIFIER, candidate))

    def test_accepts_interior_digits_and_multi_segment_kebab_names(self):
        for candidate in ("gap2", "a1b2", "ask-vs-stipulate", "abc-2-def"):
            with self.subTest(candidate=candidate):
                self.assertIsNotNone(re.fullmatch(gen_defs.IDENTIFIER, candidate))


class TestMarkerNamespace(unittest.TestCase):
    """A marker's namespace names the source its value comes from, and it is
    validated wherever a marker is read. It has to be: an overlay anchor no
    loaded source fills renders as NOTHING by design, so a misspelled namespace
    would otherwise be indistinguishable from a legitimately unfilled one."""

    def test_registered_namespaces_are_the_whole_vocabulary(self):
        self.assertEqual(gen_defs.NAMESPACES, ("arg", "dyn", "fam"))
        self.assertEqual(gen_defs.OVERLAY_NAMESPACES, ("fam",))

    def test_unknown_namespace_is_an_error_naming_the_registered_ones(self):
        for marker in ("@!famly.gap!@", "@!hrn.key!@"):
            with self.subTest(marker=marker):
                with self.assertRaisesRegex(gen_defs.TemplateError, "names no source.*arg, dyn, fam"):
                    gen_defs.anchors_in(marker, where="probe.md.tmpl")
                with self.assertRaisesRegex(gen_defs.TemplateError, "names no source"):
                    gen_defs.render(marker, {}, {}, None)

    def test_a_malformed_namespaced_marker_is_not_a_marker_at_all(self):
        # Neither half may leave the IDENTIFIER class, and neither may the
        # separator repeat. render() consumes only what MARKER matches, so
        # these pass through as literal text — which the residual-marker guard
        # refuses at generation (TestResidualMarkerGuard).
        for marker in ("@!fam.!@", "@!.gap!@", "@!fam.a.b!@", "@!fam.Gap!@", "@!1x.gap!@", "@!fam.gap--x!@"):
            with self.subTest(marker=marker):
                self.assertEqual(gen_defs.render(marker, {}, {}, {}), marker)

    def test_a_known_namespace_with_an_unfilled_key_stays_silent(self):
        # The deliberate half: silence is the legal outcome for a key no loaded
        # source defines, which is why the namespace itself must be policed.
        self.assertEqual(gen_defs.render("a@!fam.nobody-fills-me!@b", {}, {}, {}), "ab")

    def test_the_namespace_routes_and_the_name_never_does(self):
        # One name, three sources, no precedence: what a marker resolves to is
        # decided by its prefix alone, so a collision has nothing to arbitrate.
        chunks = {"gap": {"text": "from the chunk"}}
        scope = {"gap": "from the argument"}
        dynamic = {"gap": "from the invocation"}
        overlays = {"fam.gap": ("from the family", "family")}
        text = "@!gap!@ / @!arg.gap!@ / @!dyn.gap!@ / @!fam.gap!@"
        self.assertEqual(
            gen_defs.render(text, chunks, scope, overlays, dynamic),
            "from the chunk / from the argument / from the invocation / from the family",
        )

    def test_a_bare_marker_never_falls_back_to_an_argument(self):
        # The fallback chain this replaced: a chunk-argument key matching a
        # chunk name silently won, and the chunk never rendered.
        self.assertEqual(gen_defs.render("@!gap!@", {"gap": {"text": "chunk"}}, {"gap": "arg"}), "chunk")
        with self.assertRaisesRegex(gen_defs.TemplateError, "unknown chunk 'gap'"):
            gen_defs.render("@!gap!@", {}, {"gap": "arg"})

    def test_an_unbound_argument_names_itself(self):
        with self.assertRaisesRegex(gen_defs.TemplateError, "no argument 'basis' is bound here"):
            gen_defs.render("@!arg.basis!@", {}, {})

    def test_an_unknown_invocation_parameter_names_what_there_is(self):
        with self.assertRaisesRegex(gen_defs.TemplateError, "unknown invocation parameter 'tier-mid'.*tier-low"):
            gen_defs.render("@!dyn.tier-mid!@", {}, {}, None, {"tier-low": "haiku"})

    def test_a_namespaced_marker_takes_no_arguments(self):
        for marker in ('@!fam.gap wrap="70"!@', '@!arg.x variant="v"!@', '@!dyn.tier-low wrap="70"!@'):
            with self.subTest(marker=marker):
                with self.assertRaisesRegex(gen_defs.TemplateError, "takes no arguments"):
                    gen_defs.render(marker, {}, {"x": "v"}, {}, {"tier-low": "haiku"})


class TestFamilyLoading(unittest.TestCase):
    def _load(self, toml_text, tiers=None):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "family.toml"
            path.write_text((_tiers_toml() if tiers is None else tiers) + toml_text, encoding="utf-8")
            return gen_defs.load_family(path)

    def _entries(self, toml_text):
        return self._load(toml_text).entries

    def test_family_and_model_entries_load(self):
        entries = self._entries('[family.gap]\ntext = "fam"\n[family.gap.models.haiku]\ntext = "mod"\n')
        self.assertEqual(entries["fam.gap"]["text"], "fam")
        self.assertEqual(entries["fam.gap"]["models"]["haiku"]["text"], "mod")

    def test_model_only_entry_loads(self):
        entries = self._entries('[family.gap.models.haiku]\ntext = "mod"\n')
        self.assertNotIn("text", entries["fam.gap"])

    def test_text_edge_newlines_stripped(self):
        entries = self._entries('[family.gap]\ntext = """\nfam\n"""\n')
        self.assertEqual(entries["fam.gap"]["text"], "fam")

    def test_missing_file_is_error(self):
        with self.assertRaisesRegex(gen_defs.TemplateError, "does not exist"):
            gen_defs.load_family(Path("no/such/family.toml"))

    def test_invalid_toml_is_error(self):
        with self.assertRaisesRegex(gen_defs.TemplateError, "invalid TOML"):
            self._load("[family.gap\n")

    def test_zero_entry_file_loads_cleanly(self):
        # A family name stays reservable before any observed failure motivates
        # an entry — but the [tiers] table is the price of the reservation now.
        for toml_text in ("", "# comments only\n", "[family]\n"):
            self.assertEqual(self._load(toml_text).entries, {})

    def test_tiers_table_is_returned_in_canonical_order(self):
        self.assertEqual(list(self._load("").tiers), list(gen_defs.TIERS))
        self.assertEqual(self._load("").tiers, gen_defs.DEFAULT_PIN_MAP)

    def test_missing_tiers_table_is_a_load_error_naming_the_five(self):
        with self.assertRaisesRegex(gen_defs.TemplateError, r"no \[tiers\] table.*highest, high, medium, low, lowest"):
            self._load('[family.gap]\ntext = "t"\n', tiers="")

    def test_incomplete_tiers_table_is_a_load_error(self):
        with self.assertRaisesRegex(gen_defs.TemplateError, "keys must be exactly"):
            self._load("", tiers='[tiers]\nhighest = "fable"\nhigh = "opus"\n')

    def test_unknown_tier_key_is_a_load_error(self):
        with self.assertRaisesRegex(gen_defs.TemplateError, "keys must be exactly"):
            self._load("", tiers=_tiers_toml() + 'middling = "sonnet"\n')

    def test_empty_or_whitespace_member_is_a_load_error(self):
        for bad in ('""', '"two words"'):
            with self.subTest(member=bad):
                broken = _tiers_toml().replace('"sonnet"', bad)
                with self.assertRaisesRegex(gen_defs.TemplateError, "non-empty member name"):
                    self._load("", tiers=broken)

    def test_duplicate_members_across_tiers_are_legal(self):
        # A family's ladder may staff two tiers with one member — the shipped
        # claude family does exactly that at low and lowest.
        self.assertEqual(self._load("").tiers["low"], self._load("").tiers["lowest"])

    def test_stray_top_level_table_is_error(self):
        with self.assertRaisesRegex(gen_defs.TemplateError, "unknown top-level"):
            self._load('[family.gap]\ntext = "t"\n[other]\nx = "y"\n')

    def test_bad_anchor_key_is_error(self):
        with self.assertRaisesRegex(gen_defs.TemplateError, "a-z0-9"):
            self._load('[family.BadName]\ntext = "t"\n')

    def test_entry_keys_load_under_their_full_anchor_name(self):
        # The table path IS the anchor: [family.gap] fills family.gap, so the
        # loaded map compares directly against what templates author.
        self.assertEqual(list(self._entries('[family.gap]\ntext = "t"\n')), ["fam.gap"])

    def test_unknown_entry_key_is_error(self):
        with self.assertRaisesRegex(gen_defs.TemplateError, "only 'text'"):
            self._load('[family.gap]\ntext = "t"\nextra = "x"\n')

    def test_malformed_model_override_is_error(self):
        with self.assertRaisesRegex(gen_defs.TemplateError, "exactly one key"):
            self._load('[family.gap.models.haiku]\ntext = "t"\nextra = "x"\n')

    def test_entry_filling_nothing_is_error(self):
        with self.assertRaisesRegex(gen_defs.TemplateError, "fills nothing"):
            self._load("[family.gap]\n")


class TestFamilyValidation(unittest.TestCase):
    def test_known_anchors_pass(self):
        gen_defs.validate_family_anchors({"fam.gap": {}}, {"fam.gap", "fam.other"})

    def test_unknown_anchor_is_hard_error(self):
        with self.assertRaisesRegex(gen_defs.TemplateError, "ghost"):
            gen_defs.validate_family_anchors({"fam.ghost": {}, "fam.gap": {}}, {"fam.gap"})


class TestMemberReachability(unittest.TestCase):
    """A [family.*.models.<member>] table no tier reaches can never render, and
    is a hard error rather than a tier that silently reports Stock."""

    PATH = Path("templates/family/probe.toml")

    def _family(self, member, tiers=None):
        return gen_defs.Family(
            entries={"fam.gap": {"models": {member: {"text": "t"}}}},
            tiers=dict(gen_defs.DEFAULT_PIN_MAP if tiers is None else tiers),
        )

    def test_member_the_family_maps_passes(self):
        gen_defs.validate_family_members(self._family("sonnet"), gen_defs.DEFAULT_PIN_MAP, self.PATH)

    def test_member_only_the_effective_tier_map_reaches_passes(self):
        # The union, not just [tiers]: a table authored for a member reachable
        # through --model-tier-map must not fail a default run.
        tier_map = {**gen_defs.DEFAULT_PIN_MAP, "medium": "probe-member"}
        gen_defs.validate_family_members(self._family("sonnet"), tier_map, self.PATH)
        gen_defs.validate_family_members(self._family("probe-member"), tier_map, self.PATH)

    def test_unreachable_member_names_itself_and_what_is_reachable(self):
        with self.assertRaises(gen_defs.TemplateError) as caught:
            gen_defs.validate_family_members(self._family("sonnett"), gen_defs.DEFAULT_PIN_MAP, self.PATH)
        message = str(caught.exception)
        self.assertIn("no tier reaches: sonnett", message)
        self.assertIn("sonnet", message)


class TestFamilyResolution(unittest.TestCase):
    """resolve_family: --family NAME is a path or a family file, and nothing
    else — a bare model name has no reading left."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.family_dir = Path(self._tmp.name)

    def _family(self, filename):
        path = self.family_dir / filename
        path.write_text(_tiers_toml(), encoding="utf-8")
        return path

    def _resolve(self, name):
        return gen_defs.resolve_family(name, self.family_dir)

    def test_path_with_separator_passes_through(self):
        # Path-shaped names are never name-resolved, even when nonexistent —
        # load_family owns the existence error.
        self.assertEqual(self._resolve("no/such/family"), Path("no/such/family"))

    def test_toml_suffix_passes_through(self):
        self.assertEqual(self._resolve("fam.toml"), Path("fam.toml"))

    def test_bare_name_matches_the_family_file(self):
        planted = self._family("gem.toml")
        self.assertEqual(self._resolve("gem"), planted)

    def test_a_model_name_is_refused_and_the_families_are_listed(self):
        self._family("gem.toml")
        self._family("claude.toml")
        with self.assertRaisesRegex(
            gen_defs.TemplateError,
            r"--family 'opus' names no family file.*Bare model names are not " r"family names.*available: claude, gem",
        ):
            self._resolve("opus")

    def test_no_family_files_at_all_says_so(self):
        with self.assertRaisesRegex(gen_defs.TemplateError, r"available: \(none\)"):
            self._resolve("ghost")


class TestMapMerge(unittest.TestCase):
    """effective_map: what a partial map flag does, and what it refuses."""

    FLAG = "--model-pin-map"

    def _merge(self, spec, defaults=None):
        return gen_defs.effective_map(gen_defs.DEFAULT_PIN_MAP if defaults is None else defaults, spec, flag=self.FLAG)

    def test_no_spec_is_the_defaults(self):
        self.assertEqual(self._merge(None), gen_defs.DEFAULT_PIN_MAP)

    def test_named_tier_masks_only_itself(self):
        # The whole-map-replacement bug looks identical in the happy path, so
        # this is the case that separates them.
        merged = self._merge("medium=fable")
        self.assertEqual(merged["medium"], "fable")
        self.assertEqual(
            {tier: merged[tier] for tier in gen_defs.TIERS if tier != "medium"},
            {tier: value for tier, value in gen_defs.DEFAULT_PIN_MAP.items() if tier != "medium"},
        )

    def test_all_covers_every_tier(self):
        self.assertEqual(self._merge("all=haiku"), {tier: "haiku" for tier in gen_defs.TIERS})

    def test_all_is_positional_independent(self):
        # Whichever side of the named tier `all` sits, the named tier wins.
        first = self._merge("all=haiku,high=opus")
        second = self._merge("high=opus,all=haiku")
        self.assertEqual(first, second)
        self.assertEqual(first["high"], "opus")
        self.assertEqual(first["lowest"], "haiku")

    def test_merged_map_is_always_total(self):
        for spec in (None, "low=x", "all=y", "all=y,highest=z"):
            with self.subTest(spec=spec):
                self.assertEqual(set(self._merge(spec)), set(gen_defs.TIERS))

    def test_surrounding_whitespace_is_tolerated(self):
        self.assertEqual(self._merge(" high = a , low = b ")["high"], "a")

    def test_unknown_tier_names_the_five_and_all(self):
        with self.assertRaisesRegex(
            gen_defs.TemplateError,
            r"--model-pin-map: 'mid=sonnet' names no tier — tiers are " r"highest, high, medium, low, lowest, or all\.",
        ):
            self._merge("mid=sonnet")

    def test_duplicate_key_is_an_error(self):
        with self.assertRaisesRegex(gen_defs.TemplateError, r"duplicate key 'low' — a map is 1:1\."):
            self._merge("low=a,low=b")

    def test_duplicate_all_is_an_error_too(self):
        with self.assertRaisesRegex(gen_defs.TemplateError, "duplicate key 'all'"):
            self._merge("all=a,all=b")

    def test_malformed_pairs_are_errors(self):
        for spec in ("high", "high=", "=haiku", "", "high=a=b", "two words=a", "high=a b"):
            with self.subTest(spec=spec), self.assertRaises(gen_defs.TemplateError):
                self._merge(spec)

    def test_the_flag_name_leads_every_error(self):
        with self.assertRaisesRegex(gen_defs.TemplateError, "^--model-tier-map: "):
            gen_defs.effective_map(gen_defs.DEFAULT_PIN_MAP, "nope=x", flag="--model-tier-map")

    def test_pin_values_are_not_validated(self):
        # Deliberate accepted risk: nothing in-repo owns the set of legal
        # claude aliases, so the echo and the banner are the safety story.
        self.assertEqual(self._merge("high=not-a-real-model")["high"], "not-a-real-model")

    def test_map_spec_serializes_in_tier_order_never_sorted(self):
        self.assertEqual(
            gen_defs.map_spec(gen_defs.DEFAULT_PIN_MAP),
            "highest=fable,high=opus,medium=sonnet,low=haiku,lowest=haiku",
        )


class TestTierStates(unittest.TestCase):
    """The two render states §2.3 distinguishes, read off stock_tiers: Tuned
    (the member has tables) and Stock (the member has none at all)."""

    ENTRIES = {
        "fam.gap": {"text": "fam", "models": {"sonnet": {"text": "for sonnet"}}},
        "fam.other": {"text": "fam2"},
    }

    def test_a_mapped_member_with_tables_is_not_stock(self):
        self.assertNotIn("medium", gen_defs.stock_tiers(self.ENTRIES, gen_defs.DEFAULT_PIN_MAP))

    def test_every_other_tier_is_stock(self):
        self.assertEqual(
            gen_defs.stock_tiers(self.ENTRIES, gen_defs.DEFAULT_PIN_MAP),
            ("highest", "high", "low", "lowest"),
        )

    def test_a_member_missing_only_some_anchors_is_still_not_stock(self):
        # Per-anchor fallback to family-wide text is not the stock state:
        # sonnet has no `other` table and is still a tuned member.
        self.assertNotIn("sonnet", [self.ENTRIES["fam.other"].get("models", {})])
        self.assertNotIn("medium", gen_defs.stock_tiers(self.ENTRIES, gen_defs.DEFAULT_PIN_MAP))

    def test_a_family_with_no_member_tables_is_stock_at_every_tier(self):
        self.assertEqual(gen_defs.stock_tiers({"fam.gap": {"text": "f"}}, gen_defs.DEFAULT_PIN_MAP), gen_defs.TIERS)

    def test_retargeting_a_tier_moves_it_between_the_states(self):
        tuned_low = {**gen_defs.DEFAULT_PIN_MAP, "low": "sonnet"}
        self.assertNotIn("low", gen_defs.stock_tiers(self.ENTRIES, tuned_low))


class TestOverlayResolution(unittest.TestCase):
    ENTRIES = {
        "fam.both": {"text": "fam", "models": {"m1": {"text": "mod"}}},
        "fam.family-only": {"text": "fam-only"},
        "fam.model-only": {"models": {"m1": {"text": "mod-only"}}},
    }

    def test_no_member_resolves_family_scope_only(self):
        resolved = gen_defs.resolve_overlays(self.ENTRIES, None)
        self.assertEqual(
            resolved,
            {"fam.both": ("fam", "family"), "fam.family-only": ("fam-only", "family")},
        )

    def test_model_scope_wins_over_family(self):
        resolved = gen_defs.resolve_overlays(self.ENTRIES, "m1")
        self.assertEqual(resolved["fam.both"], ("mod", "model"))
        self.assertEqual(resolved["fam.model-only"], ("mod-only", "model"))
        # A model with no override still gets the family text.
        self.assertEqual(resolved["fam.family-only"], ("fam-only", "family"))

    def test_unmatched_model_falls_back_to_family_or_nothing(self):
        resolved = gen_defs.resolve_overlays(self.ENTRIES, "other-model")
        self.assertEqual(resolved["fam.both"], ("fam", "family"))
        self.assertNotIn("fam.model-only", resolved)


class TestFrontmatterPin(unittest.TestCase):
    """frontmatter_pin: an output's own model pin, read out of its RENDERED
    frontmatter — however the pin got there."""

    def test_literal_frontmatter_pin(self):
        self.assertEqual(gen_defs.frontmatter_pin("---\nname: x\nmodel: opus\n---\nbody\n"), "opus")

    def test_quoted_pin(self):
        self.assertEqual(gen_defs.frontmatter_pin('---\nmodel: "haiku"\n---\nbody\n'), "haiku")

    def test_frontmatter_without_a_model_key_is_unpinned(self):
        self.assertIsNone(gen_defs.frontmatter_pin("---\nname: x\n---\nbody\n"))
        self.assertIsNone(gen_defs.frontmatter_pin("---\n---\n\nbody\n"))

    def test_file_without_frontmatter_is_unpinned(self):
        self.assertIsNone(gen_defs.frontmatter_pin("Do the thing.\n\n## Behavior\n"))

    def test_a_model_line_in_the_body_is_not_a_pin(self):
        self.assertIsNone(gen_defs.frontmatter_pin("---\nname: x\n---\nmodel: sonnet\n"))

    def test_real_set_pins_come_from_params_and_from_literals(self):
        # Both routes, in the deployed set: mad-participant's four pins are
        # outputs-table parameters (@!arg.model!@), while the participant contract
        # and the generated commands declare no pin at all.
        pins = {
            gen_defs.rel(target): gen_defs.frontmatter_pin(text)
            # all_renders writes nothing; the root only names where the
            # targets would land, and REPO_ROOT is what makes those names
            # read as the surface-relative keys asserted below.
            for target, text in gen_defs.all_renders(
                _binding(gen_defs.load_chunks()),
                gen_defs.surface_map(gen_defs.REPO_ROOT),
                tuning=_tuning(gen_defs.FAMILY_DIR / "claude.toml"),
            )
        }
        self.assertEqual(pins["agents/mad-participant-haiku.md"], "haiku")
        self.assertEqual(pins["agents/mad-participant-opus.md"], "opus")
        self.assertIsNone(pins["agents/mad/participant-contract.md"])
        self.assertIsNone(pins["commands/mad-review.md"])


class TestTierDiscovery(unittest.TestCase):
    """output_tier: what a discovery render's frontmatter pin means."""

    def _probe(self, pin):
        return f"---\nname: x\nmodel: {pin}\n---\nbody\n"

    def test_sentinel_yields_the_tier(self):
        for tier in gen_defs.TIERS:
            self.assertEqual(gen_defs.output_tier(self._probe(f"{gen_defs.TIER_SENTINEL}{tier}")), tier)

    def test_a_literal_pin_declares_no_tier(self):
        # The migration-window third state: a pin site not yet tokenized comes
        # back as itself and takes family-wide overlay text only.
        self.assertIsNone(gen_defs.output_tier(self._probe("opus")))

    def test_no_pin_site_declares_no_tier(self):
        self.assertIsNone(gen_defs.output_tier("---\nname: x\n---\nbody\n"))
        self.assertIsNone(gen_defs.output_tier("bare body\n"))

    def test_the_tier_tokens_expand_from_the_pin_map_alone(self):
        # The one thing a family-member name must never reach: a model: line.
        binding = _binding({}, pin_map={**gen_defs.DEFAULT_PIN_MAP, "medium": "sonnet-probe"})
        self.assertEqual(gen_defs.render("@!dyn.tier-medium!@", {}, {}, None, binding.real), "sonnet-probe")
        self.assertEqual(gen_defs.render("@!dyn.tier-medium!@", {}, {}, None, binding.probe), "tier:medium")

    def test_a_tier_token_expands_inside_a_chunk_body(self):
        # The dynamic table is threaded through every recursive expansion, so a
        # token reaches a chunk body where an argument — bound only at the call
        # site — would not.
        binding = _binding({"c": {"text": "pin @!dyn.tier-low!@"}})
        self.assertEqual(gen_defs.render("@!c!@", binding.chunks, {}, None, binding.real), "pin haiku")

    def test_the_tier_tokens_are_not_in_the_chunk_table_at_all(self):
        # The re-homing: they are the invocation's parameters, so a chunk named
        # tier-low is an ordinary chunk rather than a collision to reserve
        # against, and a BARE tier-low never resolves to a pin.
        binding = _binding({"tier-low": {"text": "an ordinary chunk"}})
        self.assertEqual(binding.chunks["tier-low"], {"text": "an ordinary chunk"})
        self.assertEqual(set(binding.real), {f"tier-{tier}" for tier in gen_defs.TIERS})
        self.assertEqual(gen_defs.render("@!tier-low!@", binding.chunks, {}, None, binding.real), "an ordinary chunk")
        self.assertEqual(gen_defs.render("@!dyn.tier-low!@", binding.chunks, {}, None, binding.real), "haiku")


class TestTierResolver(unittest.TestCase):
    """tier_resolver: model scope belongs to the output's own TIER, through the
    effective tier map; family scope stays render-wide."""

    FAMILY = (
        '[family.gap]\ntext = "family-wide"\n'
        '[family.gap.models.opus]\ntext = "for opus"\n'
        '[family.other]\ntext = "other family-wide"\n'
    )

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.family_dir = Path(self._tmp.name)
        self.family = self.family_dir / "fam.toml"
        self.family.write_text(_tiers_toml() + self.FAMILY, encoding="utf-8")
        self.entries = gen_defs.load_family(self.family).entries

    def _resolver(self, tier_map=None):
        return gen_defs.tier_resolver(self.entries, dict(tier_map or gen_defs.DEFAULT_PIN_MAP))

    def test_member_scope_wins_over_family_scope_at_the_same_anchor(self):
        resolve = self._resolver()
        self.assertEqual(resolve("high")["fam.gap"], ("for opus", "model"))
        # Anchors the member does not override still take the family text.
        self.assertEqual(resolve("high")["fam.other"], ("other family-wide", "family"))

    def test_tier_whose_member_has_no_entry_takes_family_scope(self):
        self.assertEqual(self._resolver()("low")["fam.gap"], ("family-wide", "family"))

    def test_no_tier_takes_family_scope_only(self):
        # An output with no pin site is outside the pinning system, not inside
        # it holding an empty pin — and still gets every family-wide anchor.
        resolved = self._resolver()(None)
        self.assertEqual(resolved["fam.gap"], ("family-wide", "family"))
        self.assertEqual(resolved["fam.other"], ("other family-wide", "family"))

    def test_the_tier_map_decides_which_member_a_tier_resolves(self):
        # The same tier, retargeted: nothing about the output changed.
        retargeted = self._resolver({**gen_defs.DEFAULT_PIN_MAP, "lowest": "opus"})
        self.assertEqual(retargeted("lowest")["fam.gap"], ("for opus", "model"))
        self.assertEqual(self._resolver()("lowest")["fam.gap"], ("family-wide", "family"))

    def test_a_family_with_no_entries_resolves_to_nothing(self):
        resolve = gen_defs.tier_resolver({}, dict(gen_defs.DEFAULT_PIN_MAP))
        self.assertEqual(resolve(None), {})
        self.assertEqual(resolve("high"), {})

    def test_plain_map_stays_render_wide(self):
        # as_resolver is what keeps a caller that has already resolved one map
        # working: it reaches every output, tiered or not.
        resolve = gen_defs.as_resolver({"fam.gap": ("wide", "family")})
        self.assertEqual(resolve(None), resolve("high"))
        self.assertIsNone(gen_defs.as_resolver(None)("high"))


class TestRenderOverlay(unittest.TestCase):
    def test_unfilled_anchor_expands_to_nothing(self):
        for overlays in (None, {}):
            self.assertEqual(gen_defs.render("a @!fam.gap!@b", {}, {}, overlays), "a b")

    def test_filled_anchor_renders_the_family_text_verbatim(self):
        # Exact equality is the whole assertion: the anchor expands to the
        # family's own bytes and to nothing else — no lead-in, no wrapper.
        out = gen_defs.render("@!fam.gap!@", {}, {}, {"fam.gap": ("watch it", "family")})
        self.assertEqual(out, "watch it")

    def test_base_text_never_modified_around_anchor(self):
        # The never-touches-base invariant at render level: filling an anchor
        # adds the family's text and changes nothing else.
        template = "base line\n@!fam.gap!@\nmore base"
        bare = gen_defs.render(template, {}, {}, None)
        filled = gen_defs.render(template, {}, {}, {"fam.gap": ("t", "model")})
        self.assertEqual(bare, "base line\n\nmore base")
        self.assertEqual(filled, "base line\nt\nmore base")

    def test_overlay_text_is_marker_expanded(self):
        chunks = {"c": {"text": "chunked"}}
        out = gen_defs.render("@!fam.gap!@", chunks, {}, {"fam.gap": ("see @!c!@", "family")})
        self.assertEqual(out, "see chunked")

    def test_typo_in_overlay_text_fails_loudly(self):
        with self.assertRaisesRegex(gen_defs.TemplateError, "unknown chunk"):
            gen_defs.render("@!fam.gap!@", {}, {}, {"fam.gap": ("see @!nope!@", "family")})

    def test_overlay_is_no_longer_a_reserved_marker_name(self):
        # The anchor is spelled by its namespace now, so the marker name
        # `overlay` and its quoted name= argument are both gone and a chunk may
        # claim the word like any other.
        chunks = {"overlay": {"text": "an ordinary chunk"}}
        self.assertEqual(gen_defs.render("@!overlay!@", chunks, {}, {}), "an ordinary chunk")

    def test_render_output_resolves_against_its_own_declared_tier(self):
        # render_output reads the tier out of its own discovery pass, so a tier
        # token bound as an outputs-table parameter resolves exactly as a
        # literal one does.
        body = "---\nname: @!arg.name!@\nmodel: @!arg.model!@\n---\n@!fam.gap!@\n"
        params = {"name": "x", "model": "@!dyn.tier-high!@"}
        text, tier = gen_defs.render_output(body, _binding({}), params, lambda t: {"fam.gap": (str(t), "model")})
        self.assertEqual(tier, "high")
        self.assertIn("\nhigh\n", text)
        # The shipped pin, never the tier and never a family member.
        self.assertEqual(gen_defs.frontmatter_pin(text), "opus")

    def test_render_output_of_a_body_with_no_pin_site_takes_the_no_tier_map(self):
        body = "---\nname: @!arg.name!@\n---\n@!fam.gap!@\n"
        text, tier = gen_defs.render_output(body, _binding({}), {"name": "x"}, lambda t: {"fam.gap": (str(t), "model")})
        self.assertIsNone(tier)
        self.assertIn("\nNone\n", text)

    def test_pass_b_is_unconditional_so_no_sentinel_ever_ships(self):
        # The overlay set is identical for every tier here, which is exactly when
        # the retired "re-render only if the overlay set changed" optimization would
        # have returned the discovery render — sentinel text and all.
        body = "---\nname: @!arg.name!@\nmodel: @!dyn.tier-low!@\n---\nrun on @!dyn.tier-low!@\n"
        text, tier = gen_defs.render_output(body, _binding({}), {"name": "x"}, lambda t: {})
        self.assertEqual(tier, "low")
        self.assertNotIn(gen_defs.TIER_SENTINEL, text)
        self.assertIn("run on haiku", text)

    def test_anchor_inside_chunk_body_resolves(self):
        chunks = {"c": {"text": "chunk @!fam.gap!@tail"}}
        self.assertEqual(gen_defs.render("@!c!@", chunks, {}, None), "chunk tail")
        self.assertEqual(
            gen_defs.render("@!c!@", chunks, {}, {"fam.gap": ("filled", "family")}),
            "chunk filledtail",
        )


class TestSurfaceMap(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)

    def test_an_output_root_maps_both_surfaces_beneath_it(self):
        smap = gen_defs.surface_map(self.root)
        self.assertEqual(set(smap), {"agents", "commands"})
        self.assertEqual(smap["agents"][1], self.root / "agents")

    def test_surfaces_filters_to_one(self):
        self.assertEqual(set(gen_defs.surface_map(self.root, surfaces="agents")), {"agents"})
        self.assertEqual(set(gen_defs.surface_map(self.root, surfaces="commands")), {"commands"})

    def test_unknown_surface_is_error(self):
        with self.assertRaises(gen_defs.TemplateError):
            gen_defs.surface_map(self.root, surfaces="nope")

    def test_output_root_must_exist(self):
        with self.assertRaisesRegex(gen_defs.TemplateError, "existing directory"):
            gen_defs.surface_map(output_root=Path("no/such/root"))

    def test_explicit_roots_route_both_trees(self):
        with tempfile.TemporaryDirectory() as tmp:
            smap = gen_defs.surface_map(templates_root=Path(tmp) / "tsrc", output_root=Path(tmp))
            self.assertEqual(smap["agents"][0], Path(tmp) / "tsrc" / "agents")
            self.assertEqual(smap["commands"][1], Path(tmp) / "commands")


class TestSelectionMatching(unittest.TestCase):
    """What a selection glob covers: surface-relative keys without the .md
    suffix, fnmatch semantics (`*` crosses `/`), `|`-alternation as a union,
    and an unglobbed surface passing whole."""

    def test_output_key_is_surface_relative_and_suffixless(self):
        root = Path("/out/agents")
        self.assertEqual(gen_defs.output_key(root / "go-coder.md", root), "go-coder")
        self.assertEqual(
            gen_defs.output_key(root / "mad" / "participant-contract.md", root),
            "mad/participant-contract",
        )

    def test_split_globs_splits_on_the_separator(self):
        self.assertEqual(gen_defs.split_globs("*-coder*"), ["*-coder*"])
        self.assertEqual(gen_defs.split_globs("*app-expert*|*-coder*"), ["*app-expert*", "*-coder*"])

    def test_single_pattern_selects_its_matches_only(self):
        globs = {"agents": ["*-coder*"]}
        self.assertTrue(gen_defs.selected("agents", "go-coder", globs))
        self.assertFalse(gen_defs.selected("agents", "architect", globs))

    def test_alternation_selects_the_union(self):
        globs = {"agents": gen_defs.split_globs("*app-expert*|*-coder*")}
        for key in ("ios-app-expert", "rust-coder"):
            self.assertTrue(gen_defs.selected("agents", key, globs), key)
        self.assertFalse(gen_defs.selected("agents", "architect", globs))

    def test_star_crosses_the_path_separator(self):
        # A nested output is addressable by its full key and by any pattern
        # spanning the separator — that is what makes mad/participant-contract
        # reachable at all.
        for pattern in ("mad/participant-contract", "mad/*", "*participant-contract", "*contract*"):
            self.assertTrue(
                gen_defs.selected("agents", "mad/participant-contract", {"agents": [pattern]}),
                pattern,
            )
        # The subdirectory is still part of the key: a top-level definition
        # with a similar name is not swept in by mad/*.
        self.assertFalse(gen_defs.selected("agents", "mad-participant-opus", {"agents": ["mad/*"]}))

    def test_unglobbed_surface_and_empty_selection_pass_whole(self):
        self.assertTrue(gen_defs.selected("commands", "kb-start", {"agents": ["*-coder*"]}))
        for nothing in (None, {}):
            self.assertTrue(gen_defs.selected("agents", "architect", nothing))

    def test_matching_does_not_depend_on_host_case_rules(self):
        self.assertFalse(gen_defs.selected("agents", "go-coder", {"agents": ["GO-*"]}))


class TestSelectionValidationAndAccounting(unittest.TestCase):
    """A pattern matching nothing is a hard error, per `|`-segment; a run that
    selects states what it selected, per surface."""

    KEYS = {
        "agents": ["architect", "go-coder", "mad/participant-contract"],
        "commands": ["kb-start"],
    }

    def test_matching_patterns_validate(self):
        gen_defs.validate_selection(self.KEYS, {"agents": ["*-coder", "mad/*"]})

    def test_zero_match_names_the_pattern_and_lists_the_surface(self):
        with self.assertRaisesRegex(
            gen_defs.TemplateError,
            r"--agent-glob pattern '\*-codr\*' matches none of the 3 agents "
            r"output\(s\): architect, go-coder, mad/participant-contract",
        ):
            gen_defs.validate_selection(self.KEYS, {"agents": ["*-codr*"]})

    def test_every_alternation_segment_is_held_to_the_rule(self):
        # A typo cannot hide behind a sibling pattern that does match.
        with self.assertRaisesRegex(gen_defs.TemplateError, "'ghost'"):
            gen_defs.validate_selection(self.KEYS, {"agents": ["*-coder", "ghost"]})

    def test_each_surface_error_names_its_own_flag(self):
        with self.assertRaisesRegex(gen_defs.TemplateError, r"--command-glob pattern 'nope'"):
            gen_defs.validate_selection(self.KEYS, {"commands": ["nope"]})

    def test_accounting_states_selected_of_declared_per_surface(self):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            gen_defs.report_selection(self.KEYS, {"agents": ["*-coder", "mad/*"], "commands": ["kb-*"]})
        self.assertIn("agents: 2 of 3 outputs selected by --agent-glob", buf.getvalue())
        self.assertIn("commands: 1 of 1 outputs selected by --command-glob", buf.getvalue())


class TestSelectedGeneration(unittest.TestCase):
    """Selection over a scratch tree: per-output filtering of a multi-output
    template, nested-path selection, unselected outputs left untouched, a
    narrowed check, and composition with tier tuning."""

    CHUNKS = {"shared": {"text": "shared text"}}
    PINNED = "---\nname: @!arg.name!@\nmodel: @!dyn.tier-high!@\n---\nbody @!shared!@\n@!fam.probe!@\n"
    UNPINNED = "---\nname: @!arg.name!@\n---\nbody @!shared!@\n" "@!fam.probe!@\n"
    MULTI = (
        "+++\n"
        "[outputs.multi-one]\n"
        'model = "@!dyn.tier-high!@"\n'
        "[outputs.multi-two]\n"
        'model = "@!dyn.tier-low!@"\n'
        "+++\n"
        "---\nname: @!arg.name!@\nmodel: @!arg.model!@\n---\nbody @!shared!@\n"
    )
    FAMILY = (
        '[family.probe]\ntext = "family"\n'
        '[family.probe.models.opus]\ntext = "opus text"\n'
        '[family.probe.models.haiku]\ntext = "haiku text"\n'
    )

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        root = Path(self._tmp.name)
        self.tsrc = root / "templates"
        agents = self.tsrc / "agents"
        (agents / "mad").mkdir(parents=True)
        (agents / "go-coder.md.tmpl").write_text(self.PINNED, encoding="utf-8")
        (agents / "architect.md.tmpl").write_text(self.UNPINNED, encoding="utf-8")
        (agents / "multi.md.tmpl").write_text(self.MULTI, encoding="utf-8")
        self.nested_template = agents / "mad" / "participant-contract.md.tmpl"
        self.nested_template.write_text(self.UNPINNED, encoding="utf-8")
        self.out = root / "out"
        self.out.mkdir()
        self.smap = gen_defs.surface_map(templates_root=self.tsrc, output_root=self.out)
        self.family_dir = root / "family"
        self.family_dir.mkdir()
        self.family = self.family_dir / "fam.toml"
        self.family.write_text(_tiers_toml() + self.FAMILY, encoding="utf-8")

    def _generate(self, globs=None, chunks=None, **kwargs):
        kwargs.setdefault("tuning", _tuning(self.family))
        return _quiet(
            gen_defs.generate,
            _binding(self.CHUNKS if chunks is None else chunks),
            self.smap,
            globs=globs,
            **kwargs,
        )

    def _rendered(self):
        return sorted(path.relative_to(self.out).as_posix() for path in self.out.rglob("*.md"))

    def test_output_keys_span_both_surfaces_and_nested_paths(self):
        self.assertEqual(
            gen_defs.output_keys(self.smap)["agents"],
            ["architect", "go-coder", "mad/participant-contract", "multi-one", "multi-two"],
        )

    def test_glob_renders_only_its_matches(self):
        self.assertTrue(self._generate({"agents": ["*-coder"]}))
        self.assertEqual(self._rendered(), ["agents/go-coder.md"])

    def test_multi_output_template_filters_per_output(self):
        self.assertTrue(self._generate({"agents": ["multi-one"]}))
        self.assertEqual(self._rendered(), ["agents/multi-one.md"])

    def test_nested_output_is_addressable_by_its_key(self):
        self.assertTrue(self._generate({"agents": ["mad/*"]}))
        self.assertEqual(self._rendered(), ["agents/mad/participant-contract.md"])

    def test_unselected_outputs_are_left_untouched_on_disk(self):
        self._generate()
        before = {name: (self.out / name).read_bytes() for name in self._rendered()}
        stamps = {name: (self.out / name).stat().st_mtime_ns for name in before}
        # A chunk change every output would pick up, applied under a selection
        # that covers exactly one of them.
        self.assertTrue(self._generate({"agents": ["multi-one"]}, chunks={"shared": {"text": "changed text"}}))
        self.assertIn("changed text", (self.out / "agents" / "multi-one.md").read_text(encoding="utf-8"))
        for name, content in before.items():
            if name == "agents/multi-one.md":
                continue
            self.assertEqual((self.out / name).read_bytes(), content, name)
            self.assertEqual((self.out / name).stat().st_mtime_ns, stamps[name], name)

    def test_check_under_a_selection_ignores_everything_else(self):
        tuning = _tuning(self.family)
        self.assertTrue(self._generate({"agents": ["*-coder"]}))
        self.assertTrue(
            _quiet(
                gen_defs.check,
                _binding(self.CHUNKS),
                self.smap,
                tuning=tuning,
                globs={"agents": ["*-coder"]},
            )
        )
        # The same tree unselected: the outputs never rendered are MISSING.
        self.assertFalse(_quiet(gen_defs.check, _binding(self.CHUNKS), self.smap, tuning=tuning))

    def test_check_two_walk_is_narrowed_by_the_selection(self):
        self._generate()
        self.nested_template.unlink()
        orphans = gen_defs.check_banner_claims(self.smap)
        self.assertEqual(len(orphans), 1)
        self.assertIn("ORPHAN", orphans[0])
        self.assertEqual(gen_defs.check_banner_claims(self.smap, {"agents": ["*-coder"]}), [])

    def test_selection_composes_with_tier_tuning(self):
        entries = gen_defs.load_family(self.family).entries
        resolve = gen_defs.tier_resolver(entries, dict(gen_defs.DEFAULT_PIN_MAP))
        tuning = _tuning(self.family, entries=entries)
        globs = {"agents": ["go-coder", "architect"]}
        self.assertTrue(self._generate(globs, overlays=resolve, tuning=tuning))
        self.assertEqual(self._rendered(), ["agents/architect.md", "agents/go-coder.md"])
        tiered = (self.out / "agents" / "go-coder.md").read_text(encoding="utf-8")
        untiered = (self.out / "agents" / "architect.md").read_text(encoding="utf-8")
        # Selection changes which outputs are rendered and nothing about how:
        # the tier still owns its model scope, an output with no pin site still
        # takes family-wide text, and the banner still records both.
        # Anchored against the base line above the anchor, which is what makes
        # these say "verbatim, in place" rather than merely "present".
        self.assertIn("body shared text\nopus text\n", tiered)
        self.assertIn("body shared text\nfamily\n", untiered)
        self.assertEqual(gen_defs.tuning_claim(tiered).seat, "high")
        self.assertEqual(gen_defs.tuning_claim(tiered).member, "opus")
        self.assertEqual(gen_defs.tuning_claim(untiered).seat, "none")
        self.assertEqual(gen_defs.tuning_claim(untiered).member, "none")
        self.assertTrue(gen_defs.body_untouched(tiered))
        self.assertTrue(
            _quiet(gen_defs.check, _binding(self.CHUNKS), self.smap, overlays=resolve, tuning=tuning, globs=globs)
        )

    def test_a_seat_mismatch_is_mistuned_and_not_drift(self):
        # The banner's seat/member are part of the tuning claim, so a tree
        # rendered under one tier map and checked under another is named
        # rather than dumped as a byte diff.
        entries = gen_defs.load_family(self.family).entries
        globs = {"agents": ["go-coder"]}
        self._generate(globs, overlays=gen_defs.tier_resolver(entries, dict(gen_defs.DEFAULT_PIN_MAP)))
        retargeted = {**gen_defs.DEFAULT_PIN_MAP, "high": "haiku"}
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            ok = gen_defs.check(
                _binding(self.CHUNKS),
                self.smap,
                overlays=gen_defs.tier_resolver(entries, retargeted),
                tuning=_tuning(self.family, tier_map=retargeted, entries=entries),
                globs=globs,
            )
        report = buf.getvalue()
        self.assertFalse(ok)
        self.assertIn("MISTUNED", report)
        self.assertNotIn("DRIFT", report)
        self.assertIn("member opus", report)
        self.assertIn("member haiku", report)


class TestSelectionCLI(unittest.TestCase):
    """The verbs and their flags as shipped, driven through gen-defs.py itself:
    the required verb and root, surface implication, both-glob runs, the one
    surviving conflict error, the per-verb flags a verb does not declare, and
    the loud no-match."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.out = Path(self._tmp.name)

    def _run(self, *args):
        return subprocess.run(
            [sys.executable, str(_REPO_ROOT / "gen-defs.py"), *args],
            capture_output=True,
            text=True,
            check=False,
            cwd=_REPO_ROOT,
            env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
        )

    def _rendered(self):
        return sorted(path.relative_to(self.out).as_posix() for path in self.out.rglob("*.md"))

    def test_the_verb_is_required_and_every_verb_names_its_root(self):
        # No bare invocation and no implicit default mode; and there is no
        # checked-in tree for a root to default to. Exit 2 — an unusable
        # invocation, not a verdict about a tree — which is the distinction a
        # caller keys on.
        bare = self._run()
        self.assertEqual(bare.returncode, 2, bare.stderr)
        self.assertIn("the following arguments are required", bare.stderr)
        for verb in ("generate", "check", "install"):
            done = self._run(verb)
            self.assertEqual(done.returncode, 2, f"{verb}: {done.stderr}")
            self.assertIn("the following arguments are required: ROOT", done.stderr, verb)

    def test_the_flat_flag_surface_is_gone_rather_than_aliased(self):
        # The mode flags the verbs replaced are unknown flags, not shims: an
        # invocation written against the old surface fails loudly instead of
        # being reinterpreted.
        for args in (("--generate", "--output-dir", str(self.out)), ("--install", str(self.out))):
            with self.subTest(args=args):
                done = self._run(*args)
                self.assertEqual(done.returncode, 2, done.stderr)
                self.assertEqual(self._rendered(), [])

    def test_a_missing_output_root_is_refused_rather_than_created(self):
        absent = self.out / "not-there"
        done = self._run("generate", str(absent))
        self.assertEqual(done.returncode, 2, done.stderr)
        self.assertFalse(absent.exists())

    def test_agent_glob_alone_excludes_the_commands_surface(self):
        done = self._run("generate", str(self.out), "--agent-glob", "*-coder*")
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertEqual(
            self._rendered(),
            [
                "agents/generalist-coder.md",
                "agents/go-coder.md",
                "agents/python-coder.md",
                "agents/rust-coder.md",
                "agents/shell-dsl-coder.md",
            ],
        )
        self.assertFalse((self.out / "commands").exists())

    def test_both_globs_filter_each_surface_and_report_the_accounting(self):
        done = self._run(
            "generate",
            str(self.out),
            "--agent-glob",
            "mad/participant-*",
            "--command-glob",
            "kb-*",
        )
        self.assertEqual(done.returncode, 0, done.stderr)
        rendered = self._rendered()
        agents = [name for name in rendered if name.startswith("agents/")]
        commands = [name for name in rendered if name.startswith("commands/")]
        # Each surface filtered by its own patterns, and both present.
        self.assertEqual(agents, ["agents/mad/participant-contract.md"])
        self.assertTrue(commands)
        for name in commands:
            self.assertTrue(Path(name).name.startswith("kb-"), name)
        self.assertRegex(done.stdout, r"agents: 1 of \d+ outputs selected by --agent-glob")
        self.assertRegex(done.stdout, r"commands: \d+ of \d+ outputs selected by --command-glob")

    def test_surfaces_is_subsumed_and_refused(self):
        # The one exclusion that survives as logic: both flags live on both
        # selecting verbs, so nothing structural separates them.
        for verb in ("generate", "check"):
            with self.subTest(verb=verb):
                done = self._run(verb, str(self.out), "--agent-glob", "*", "--surfaces", "agents")
                self.assertEqual(done.returncode, 2)
                self.assertIn("drop --surfaces", done.stderr)

    def test_install_cannot_be_narrowed_by_a_glob(self):
        # Not refused by a check inside the install path: `install` declares no
        # selection glob at all, so a partial install is unrepresentable.
        done = self._run("install", str(self.out), "--agent-glob", "*")
        self.assertEqual(done.returncode, 2)
        self.assertIn("unrecognized arguments: --agent-glob", done.stderr)
        self.assertEqual(self._rendered(), [])

    def test_install_cannot_be_narrowed_by_surfaces(self):
        done = self._run("install", str(self.out), "--surfaces", "agents")
        self.assertEqual(done.returncode, 2)
        self.assertIn("unrecognized arguments: --surfaces", done.stderr)
        self.assertEqual(self._rendered(), [])

    def test_surfaces_still_narrows_generate_and_check(self):
        generated = self._run("generate", str(self.out), "--surfaces", "commands")
        self.assertEqual(generated.returncode, 0, generated.stderr)
        self.assertTrue(self._rendered())
        self.assertFalse((self.out / "agents").exists())
        checked = self._run("check", str(self.out), "--surfaces", "commands")
        self.assertEqual(checked.returncode, 0, checked.stderr)

    def test_no_diff_is_a_check_flag_alone(self):
        # Nothing to suppress in a render: generate prints no diff.
        done = self._run("generate", str(self.out), "--no-diff")
        self.assertEqual(done.returncode, 2)
        self.assertIn("unrecognized arguments: --no-diff", done.stderr)
        self.assertEqual(self._rendered(), [])

    def test_zero_match_is_a_hard_error_listing_the_surface(self):
        done = self._run("generate", str(self.out), "--agent-glob", "*-codr*")
        self.assertEqual(done.returncode, 2)
        self.assertIn("--agent-glob pattern '*-codr*' matches none of", done.stderr)
        self.assertIn("go-coder", done.stderr)
        self.assertEqual(self._rendered(), [])


class TestTuningCLI(unittest.TestCase):
    """The three tuning flags as shipped, driven through gen-defs.py itself.

    Against an isolated COPY of the repo's templates, not the real tree: the
    member-scope route needs a family declaring member tables, neither shipped
    family does, and a fixture planted in the real templates/family/ would be
    reachable as real input by unrelated runs.
    """

    PROBE = (
        _tiers_toml({**gen_defs.DEFAULT_PIN_MAP, "lowest": "probe-member"})
        + '[family.ask-vs-stipulate]\ntext = "probe family text"\n'
        '[family.ask-vs-stipulate.models.probe-member]\ntext = "probe member text"\n'
        '[family.ask-vs-stipulate.models.opus]\ntext = "probe opus text"\n'
    )
    # applied-mathematician authors ask-vs-stipulate and carries a pin site;
    # gap-aversion is authored in kb-claim-scorer.md.tmpl.
    SELECT = ("--agent-glob", "applied-mathematician|mad/participant-contract")

    @classmethod
    def setUpClass(cls):
        cls._class_tmp = tempfile.TemporaryDirectory()
        cls.repo = Path(cls._class_tmp.name) / "repo"
        cls.repo.mkdir()
        shutil.copytree(
            gen_defs.TEMPLATES_DIR,
            cls.repo / "templates",
            ignore=shutil.ignore_patterns("tests", "__pycache__"),
        )
        shutil.copy(_REPO_ROOT / "gen-defs.py", cls.repo / "gen-defs.py")
        cls.family_dir = cls.repo / "templates" / "family"
        (cls.family_dir / "probe.toml").write_text(cls.PROBE, encoding="utf-8")

    @classmethod
    def tearDownClass(cls):
        cls._class_tmp.cleanup()

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.out = Path(self._tmp.name)

    def _run(self, *args):
        return subprocess.run(
            [sys.executable, str(self.repo / "gen-defs.py"), *args],
            capture_output=True,
            text=True,
            check=False,
            cwd=self.repo,
            env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
        )

    def _generate(self, *tuning_args):
        return self._run("generate", str(self.out), *self.SELECT, *tuning_args)

    def _bodies(self):
        return {
            path.relative_to(self.out).as_posix(): path.read_text(encoding="utf-8") for path in self.out.rglob("*.md")
        }

    def _claim(self, name="agents/applied-mathematician.md"):
        return gen_defs.tuning_claim(self._bodies()[name])

    def test_the_default_run_needs_no_flag_at_all(self):
        done = self._generate()
        self.assertEqual(done.returncode, 0, done.stderr)
        claim = self._claim()
        self.assertEqual(claim.family, "templates/family/claude.toml")
        self.assertEqual(claim.tier, gen_defs.map_spec(gen_defs.DEFAULT_PIN_MAP))
        self.assertEqual(claim.pin, gen_defs.map_spec(gen_defs.DEFAULT_PIN_MAP))

    def test_family_name_loads_that_family(self):
        done = self._generate("--family", "probe")
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertEqual(self._claim().family, "templates/family/probe.toml")
        self.assertIn("probe opus text", self._bodies()["agents/applied-mathematician.md"])

    def test_path_shaped_family_loads_the_file(self):
        done = self._generate("--family", "templates/family/probe.toml")
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertEqual(self._claim().family, "templates/family/probe.toml")

    def test_a_model_name_as_a_family_is_refused(self):
        done = self._generate("--family", "opus")
        self.assertEqual(done.returncode, 2)
        self.assertIn("Bare model names are not family names", done.stderr)
        self.assertIn("available: claude, gemma-4, probe", done.stderr)
        self.assertEqual(self._bodies(), {})

    def test_an_unknown_family_is_refused(self):
        done = self._generate("--family", "ghost")
        self.assertEqual(done.returncode, 2)
        self.assertIn("--family 'ghost' names no family file", done.stderr)
        self.assertEqual(self._bodies(), {})

    def test_the_tier_map_retargets_a_tier_without_touching_its_pin(self):
        # The whole point of two maps: what a definition is TUNED for and what
        # it DISPATCHES on move independently.
        done = self._generate("--family", "probe", "--model-tier-map", "highest=opus")
        self.assertEqual(done.returncode, 0, done.stderr)
        claim = self._claim()
        self.assertIn("highest=opus", claim.tier)
        self.assertIn("highest=fable", claim.pin)

    def test_the_pin_map_changes_rendered_pins_and_says_so(self):
        done = self._generate("--model-pin-map", "all=haiku")
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertEqual(self._claim().pin, ",".join(f"{tier}=haiku" for tier in gen_defs.TIERS))

    def test_a_bad_map_key_is_a_hard_error_before_anything_is_written(self):
        for flag in ("--model-tier-map", "--model-pin-map"):
            with self.subTest(flag=flag):
                done = self._generate(flag, "mid=sonnet")
                self.assertEqual(done.returncode, 2)
                self.assertIn(f"{flag}: 'mid=sonnet' names no tier", done.stderr)
                self.assertEqual(self._bodies(), {})

    def test_the_retired_flags_are_unknown_flags(self):
        # Not deprecation shims, and not abbreviations of the spellings that
        # replaced them: argparse prefix matching is off, so both fail loudly.
        for args in (("--model-family", "probe"), ("--model-map", "opus=haiku"), ("--model", "opus")):
            with self.subTest(args=args):
                done = self._run("generate", str(self.out), *args)
                self.assertEqual(done.returncode, 2, args)
                self.assertIn("unrecognized arguments: " + args[0], done.stderr)
        self.assertEqual(self._bodies(), {})

    def test_a_family_missing_its_tiers_table_refuses_to_render(self):
        broken = self.family_dir / "broken.toml"
        broken.write_text('[family.gap-aversion]\ntext = "x"\n', encoding="utf-8")
        self.addCleanup(broken.unlink)
        done = self._generate("--family", "broken")
        self.assertEqual(done.returncode, 2)
        self.assertIn("no [tiers] table", done.stderr)
        self.assertEqual(self._bodies(), {})

    def test_an_unreachable_member_table_refuses_to_render(self):
        # Ripple 1: a mis-spelled member would otherwise render as an ordinary
        # Stock tier, silently.
        broken = self.family_dir / "typo.toml"
        broken.write_text(
            _tiers_toml() + '[family.gap-aversion.models.oppus]\ntext = "x"\n',
            encoding="utf-8",
        )
        self.addCleanup(broken.unlink)
        done = self._generate("--family", "typo")
        self.assertEqual(done.returncode, 2)
        self.assertIn("no tier reaches: oppus", done.stderr)
        # …and the tier map is the union, so mapping a tier to it fixes the run.
        fixed = self._generate("--family", "typo", "--model-tier-map", "lowest=oppus")
        self.assertEqual(fixed.returncode, 0, fixed.stderr)

    def test_check_holds_a_tuned_set_to_the_triple_that_rendered_it(self):
        self.assertEqual(self._generate("--family", "probe").returncode, 0)
        same = self._run("check", str(self.out), *self.SELECT, "--family", "probe")
        self.assertEqual(same.returncode, 0, same.stdout)
        # The same directory checked under the default family: MISTUNED, named
        # rather than dumped as a byte diff.
        other = self._run("check", str(self.out), *self.SELECT, "--no-diff")
        self.assertEqual(other.returncode, 1)
        self.assertIn("MISTUNED", other.stdout)
        self.assertIn("templates/family/probe.toml", other.stdout)

    def test_the_run_echoes_its_triple_and_neither_notice_fires(self):
        stock = self._generate()
        self.assertIn(
            "tuning: family=templates/family/claude.toml "
            f"tier[{gen_defs.map_spec(gen_defs.DEFAULT_PIN_MAP)}] "
            f"pin[{gen_defs.map_spec(gen_defs.DEFAULT_PIN_MAP)}]",
            stock.stdout,
        )
        # Every tier of the shipped claude family is stock and the two maps
        # agree, so the triple is the whole of what a default run says. The
        # stock state is what the banner's stock= field is for; the run's
        # report does not mention it at all.
        self.assertEqual([line for line in stock.stdout.splitlines() if line.startswith("notice:")], [])
        self.assertNotIn("stock", stock.stdout)

    def test_the_divergence_notice_is_a_notice_and_gates_nothing(self):
        done = self._generate("--model-tier-map", "medium=haiku")
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertIn(
            "notice: tier map and pin map diverge at medium (tier=haiku, pin=sonnet) — "
            "tuning/capacity isolation, not an error",
            done.stdout,
        )
        # It rendered: a notice never refuses.
        self.assertTrue(self._bodies())

    def test_the_floor_rung_prints_no_notice_at_all(self):
        # `just check-floor`'s flags. Two maps collapsed onto one value cannot
        # diverge, and haiku is a member the claude family declares nothing
        # for, so the rung renders stock at every tier and stays silent.
        done = self._generate("--model-tier-map", "all=haiku", "--model-pin-map", "all=haiku")
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertEqual([line for line in done.stdout.splitlines() if line.startswith("notice:")], [])
        self.assertEqual(gen_defs.frontmatter_pin(self._bodies()["agents/applied-mathematician.md"]), "haiku")

    def test_the_divergence_notice_is_confined_to_the_claude_family(self):
        # For any other family the two maps diverge at every tier by
        # construction — members against claude aliases — so a notice there
        # would fire five times a run carrying no information.
        done = self._generate("--family", "gemma-4")
        self.assertEqual(done.returncode, 0, done.stderr)
        # The triple pins that the reporting block ran at all, so the absent
        # notice is a decision and not an unreached print.
        self.assertIn("tuning: family=templates/family/gemma-4.toml", done.stdout)
        self.assertEqual([line for line in done.stdout.splitlines() if line.startswith("notice:")], [])

    def test_the_tuned_notice_names_the_override_bearing_tiers_and_no_others(self):
        # probe.toml declares member tables for opus and probe-member alone,
        # and its [tiers] table staffs high and lowest with those two; the
        # other three tiers map to members it says nothing about. Whole-line
        # equality is the "and no others" half — a fourth tier or a stray
        # member reaching the line fails here.
        done = self._generate("--family", "probe")
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertEqual(
            [line for line in done.stdout.splitlines() if line.startswith("notice:")],
            [
                "notice: member-scoped tuning is in force at high (opus), lowest (probe-member) — "
                "templates/family/probe.toml fills their anchors"
            ],
        )


class TestTunedBanner(unittest.TestCase):
    TMPL = gen_defs.TEMPLATES_DIR / "agents" / "x.md.tmpl"
    FAMILY = gen_defs.TEMPLATES_DIR / "family" / "fam.toml"

    def _stamped(self, *, seat=None, tuning=None):
        body = "name: x\n---\nbody\n"
        tuning = _tuning(self.FAMILY) if tuning is None else tuning
        stamp = gen_defs.banner(self.TMPL, body_hash=gen_defs.sha256_text(body), tuning=tuning, seat=seat)
        return "---\n" + stamp + "\n" + body

    def test_the_generated_line_carries_only_the_template_and_the_chunks(self):
        # The tuning moved onto its own line: the origin clause is one claim.
        stamp = self._stamped()
        self.assertIn(
            "# !GENERATED! from templates/agents/x.md.tmpl and templates/shared-chunks.toml — edit those.",
            stamp,
        )
        self.assertNotIn("with model family", stamp)

    def test_the_block_is_five_lines_with_tuning_above_the_hash(self):
        # BODY_HASH_CLAIM matches the hash line together with the `#` closing
        # the block, so anything added has to go above it.
        lines = self._stamped().split("\n")
        self.assertEqual(lines[0], "---")
        self.assertEqual(lines[1], "#")
        self.assertTrue(lines[2].startswith("# !GENERATED! "))
        self.assertTrue(lines[3].startswith("# !TUNING! "))
        self.assertTrue(lines[4].startswith("# !BODY-SHA256! "))
        self.assertEqual(lines[5], "#")

    def test_claims_round_trip(self):
        text = self._stamped(seat="high")
        self.assertEqual(gen_defs.banner_claim(text), "templates/agents/x.md.tmpl")
        self.assertEqual(
            gen_defs.tuning_claim(text),
            gen_defs.TuningClaim(
                family="templates/family/fam.toml",
                seat="high",
                member="opus",
                tier=gen_defs.map_spec(gen_defs.DEFAULT_PIN_MAP),
                pin=gen_defs.map_spec(gen_defs.DEFAULT_PIN_MAP),
                stock=",".join(gen_defs.TIERS),
            ),
        )
        self.assertTrue(gen_defs.body_untouched(text))

    def test_an_output_with_no_pin_site_records_none_for_both(self):
        claim = gen_defs.tuning_claim(self._stamped())
        self.assertEqual((claim.seat, claim.member), ("none", "none"))

    def test_the_member_is_recorded_so_the_tier_never_has_to_be_inverted(self):
        # DEFAULT_PIN_MAP is not injective — low and lowest both ship haiku —
        # and an all= pin map collapses every tier onto one value, so seat is
        # unrecoverable from the pin. Recorded, not derived.
        floor = _tuning(self.FAMILY, pin_map={tier: "haiku" for tier in gen_defs.TIERS})
        for seat in ("low", "lowest"):
            claim = gen_defs.tuning_claim(self._stamped(seat=seat, tuning=floor))
            self.assertEqual(claim.seat, seat)
            self.assertEqual(claim.member, gen_defs.DEFAULT_PIN_MAP[seat])

    def test_stock_none_when_every_tier_is_tuned(self):
        entries = {"fam.gap": {"models": {member: {"text": "t"} for member in gen_defs.DEFAULT_PIN_MAP.values()}}}
        claim = gen_defs.tuning_claim(self._stamped(tuning=_tuning(self.FAMILY, entries=entries)))
        self.assertEqual(claim.stock, "none")

    def test_maps_serialize_in_tier_order_never_sorted(self):
        claim = gen_defs.tuning_claim(self._stamped())
        self.assertTrue(claim.tier.startswith("highest="))
        self.assertTrue(claim.tier.endswith("lowest=haiku"))

    def test_describe_tuning(self):
        self.assertEqual(gen_defs.describe_tuning(None), "no tuning claim")
        described = gen_defs.describe_tuning(gen_defs.tuning_claim(self._stamped(seat="medium")))
        self.assertIn("family templates/family/fam.toml", described)
        self.assertIn("seat medium, member sonnet", described)


class TestGenerateCheckRoundTrip(unittest.TestCase):
    """End-to-end over a scratch template tree: a family with no entry fills
    nothing, a family with one fills the anchor, and check enforces the
    tuning claim."""

    CHUNKS = {"shared": {"text": "shared text"}}
    BODY = "---\nname: @!arg.name!@\n---\n" "body @!shared!@\n@!fam.probe!@\ntail\n"
    ENTRIES = {"fam.probe": {"text": "family fill", "models": {"sonnet": {"text": "model fill"}}}}

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        root = Path(self._tmp.name)
        self.tsrc = root / "templates"
        (self.tsrc / "agents").mkdir(parents=True)
        (self.tsrc / "agents" / "probe.md.tmpl").write_text(self.BODY, encoding="utf-8")
        self.out = root / "out"
        self.out.mkdir()
        self.smap = gen_defs.surface_map(templates_root=self.tsrc, output_root=self.out)
        self.family = root / "family.toml"
        self.family.write_text(
            _tiers_toml() + '[family.probe]\ntext = "family fill"\n[family.probe.models.sonnet]\ntext = "model fill"\n',
            encoding="utf-8",
        )
        self.other = root / "other.toml"
        self.other.write_text(_tiers_toml(), encoding="utf-8")

    def _target(self):
        return self.out / "agents" / "probe.md"

    def _generate(self, **kwargs):
        kwargs.setdefault("tuning", _tuning(self.family))
        return _quiet(gen_defs.generate, _binding(self.CHUNKS), self.smap, **kwargs)

    def test_scratch_template_anchor_is_collectable(self):
        found = gen_defs.collect_anchors(self.CHUNKS, gen_defs.templates(self.smap))
        self.assertEqual(found, {"fam.probe"})

    def test_a_family_filling_nothing_leaves_the_base_render(self):
        self.assertTrue(self._generate())
        text = self._target().read_text(encoding="utf-8")
        self.assertIn("body shared text\n\ntail", text)
        self.assertNotIn("family fill", text)

    def test_tuned_generate_fills_one_nb_and_claims_the_triple(self):
        # An output with no pin site takes family-wide text; retarget nothing.
        overlays = gen_defs.tier_resolver(self.ENTRIES, dict(gen_defs.DEFAULT_PIN_MAP))
        self.assertTrue(self._generate(overlays=overlays, tuning=_tuning(self.family, entries=self.ENTRIES)))
        text = self._target().read_text(encoding="utf-8")
        self.assertIn("body shared text\nfamily fill\ntail", text)
        self.assertNotIn("model fill", text)  # one text per anchor, and no tier here
        claim = gen_defs.tuning_claim(text)
        self.assertEqual(claim.family, gen_defs.rel(self.family))
        self.assertEqual(claim.stock, "highest,high,low,lowest")

    def test_check_passes_only_under_the_triple_that_rendered_it(self):
        overlays = gen_defs.tier_resolver(self.ENTRIES, dict(gen_defs.DEFAULT_PIN_MAP))
        tuning = _tuning(self.family, entries=self.ENTRIES)
        self._generate(overlays=overlays, tuning=tuning)
        self.assertTrue(_quiet(gen_defs.check, _binding(self.CHUNKS), self.smap, overlays=overlays, tuning=tuning))
        # The same directory checked under another family: MISTUNED, nonzero.
        self.assertFalse(_quiet(gen_defs.check, _binding(self.CHUNKS), self.smap, tuning=_tuning(self.other)))

    def test_mistuned_is_named_not_dumped(self):
        self._generate()
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            ok = gen_defs.check(
                _binding(self.CHUNKS),
                self.smap,
                overlays=gen_defs.tier_resolver(self.ENTRIES, dict(gen_defs.DEFAULT_PIN_MAP)),
                tuning=_tuning(self.other, entries=self.ENTRIES),
            )
        self.assertFalse(ok)
        self.assertIn("MISTUNED", buf.getvalue())
        self.assertIn(gen_defs.rel(self.other), buf.getvalue())

    def test_bannerless_target_refused_out_of_repo_too(self):
        target = self._target()
        target.parent.mkdir(parents=True)
        target.write_text("hand-maintained\n", encoding="utf-8")
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            ok = gen_defs.generate(_binding(self.CHUNKS), self.smap, tuning=_tuning(self.family))
        self.assertFalse(ok)
        self.assertIn("REFUSED", buf.getvalue())
        self.assertEqual(target.read_text(encoding="utf-8"), "hand-maintained\n")

    def test_hand_edited_target_backs_up_out_of_repo_too(self):
        # The whole write-safety table applies identically out of repo: an
        # untouched render is replaced outright, a hand-edited one is copied
        # aside first.
        self._generate()
        overlays = gen_defs.tier_resolver(self.ENTRIES, dict(gen_defs.DEFAULT_PIN_MAP))
        self._generate(overlays=overlays, tuning=_tuning(self.family, entries=self.ENTRIES))
        self.assertFalse(self._target().with_name("probe.md.00.bak").exists())

        target = self._target()
        target.write_text(target.read_text(encoding="utf-8") + "hand-added\n", encoding="utf-8")
        self._generate()
        self.assertTrue(target.with_name("probe.md.00.bak").exists())


class TestCheckReportsRefactorBlastRadius(unittest.TestCase):
    """The canonical use `check` exists for: extracting text duplicated across
    several definitions into a shared chunk, then reading back exactly what
    the extraction changed. `check` renders nothing of its own — it diffs a
    PRESERVED baseline (a prior `generate`) against what the templates render
    now, which is what makes the diff show the refactor's real blast radius
    rather than an always-empty report. Passing (exit 0) is not the point
    here; a refactor meant to touch two definitions that quietly reflows a
    third is the failure, so the assertions are about WHICH definitions the
    report names and WHAT it says changed, not merely that it went nonzero.
    """

    OLD_PHRASE = "Recieve the input and process it."
    NEW_PHRASE = "Receive the input and process it."
    ALPHA = f"---\nname: @!arg.name!@\n---\nAlpha body. {OLD_PHRASE}\n"
    BETA = f"---\nname: @!arg.name!@\n---\nBeta body. {OLD_PHRASE}\n"
    GAMMA = "---\nname: @!arg.name!@\n---\nGamma body, unrelated text entirely.\n"

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        root = Path(self._tmp.name)
        self.tsrc = root / "templates"
        (self.tsrc / "agents").mkdir(parents=True)
        for name, body in (("alpha", self.ALPHA), ("beta", self.BETA), ("gamma", self.GAMMA)):
            (self.tsrc / "agents" / f"{name}.md.tmpl").write_text(body, encoding="utf-8")
        self.out = root / "out"
        self.out.mkdir()
        self.smap = gen_defs.surface_map(templates_root=self.tsrc, output_root=self.out)
        self.family = root / "fam.toml"
        self.family.write_text(_tiers_toml(), encoding="utf-8")
        self.tuning = _tuning(self.family)
        # The baseline: alpha and beta still carry the duplicated (typo'd)
        # phrase inline — the pre-refactor state `just generate` would have
        # produced, and that `check` must diff against rather than overwrite.
        self.assertTrue(_quiet(gen_defs.generate, _binding({}), self.smap, tuning=self.tuning))

    def _refactor(self):
        """Lift the duplicated phrase into a chunk, correcting its typo once,
        and reference it from the two templates that carried it. gamma, which
        never had the phrase, is left untouched — the intended shape of a
        scoped refactor."""
        (self.tsrc / "agents" / "alpha.md.tmpl").write_text(
            "---\nname: @!arg.name!@\n---\nAlpha body. @!shared-receive!@\n", encoding="utf-8"
        )
        (self.tsrc / "agents" / "beta.md.tmpl").write_text(
            "---\nname: @!arg.name!@\n---\nBeta body. @!shared-receive!@\n", encoding="utf-8"
        )
        return _binding({"shared-receive": {"text": self.NEW_PHRASE}})

    def _check_report(self, binding):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            ok = gen_defs.check(binding, self.smap, tuning=self.tuning)
        return ok, buf.getvalue()

    def test_the_report_names_exactly_the_two_refactored_targets(self):
        ok, report = self._check_report(self._refactor())
        self.assertFalse(ok)
        alpha, beta, gamma = (gen_defs.rel(self.out / "agents" / f"{n}.md") for n in ("alpha", "beta", "gamma"))
        drift_lines = [line for line in report.splitlines() if "DRIFT" in line]
        self.assertEqual(len(drift_lines), 2)
        self.assertTrue(any(alpha in line for line in drift_lines))
        self.assertTrue(any(beta in line for line in drift_lines))
        # gamma never had the phrase and its template was never touched — its
        # path must not appear anywhere in the report, not even in a diff.
        self.assertNotIn(gamma, report)

    def test_the_diff_shows_the_intended_change_at_each_target_and_nothing_else(self):
        _, report = self._check_report(self._refactor())
        # check diffs the fresh (post-refactor) render against the preserved
        # baseline, so the corrected wording is the '-' side and the baseline
        # typo is the '+' side, at both — and only both — targets.
        self.assertIn(f"-Alpha body. {self.NEW_PHRASE}", report)
        self.assertIn(f"+Alpha body. {self.OLD_PHRASE}", report)
        self.assertIn(f"-Beta body. {self.NEW_PHRASE}", report)
        self.assertIn(f"+Beta body. {self.OLD_PHRASE}", report)

    def test_a_correctly_scoped_refactor_with_no_baseline_left_behind_is_clean(self):
        # The negative control: re-generating the baseline after the same
        # refactor leaves nothing for check to report — the tool the fix
        # protects, not the mutation it is supposed to catch.
        binding = self._refactor()
        self.assertTrue(_quiet(gen_defs.generate, binding, self.smap, tuning=self.tuning))
        ok, report = self._check_report(binding)
        self.assertTrue(ok)
        self.assertNotIn("DRIFT", report)


class TestMixedTierRender(unittest.TestCase):
    """A mixed render over a scratch tree — one tiered agent, one command with
    no pin site, one family file — resolving each output against its own tier."""

    CHUNKS = {}
    TIERED = "---\nname: @!arg.name!@\nmodel: @!dyn.tier-high!@\n---\n@!fam.probe!@\n"
    # A frontmatter-less command: the surface that carries no pin site today.
    NO_PIN_SITE = "@!fam.probe!@\n"
    FAMILY = (
        '[family.probe]\ntext = "family"\n'
        '[family.probe.models.opus]\ntext = "opus text"\n'
        '[family.probe.models.haiku]\ntext = "haiku text"\n'
    )

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        root = Path(self._tmp.name)
        self.tsrc = root / "templates"
        (self.tsrc / "agents").mkdir(parents=True)
        (self.tsrc / "commands").mkdir(parents=True)
        (self.tsrc / "agents" / "tiered.md.tmpl").write_text(self.TIERED, encoding="utf-8")
        (self.tsrc / "commands" / "plain.md.tmpl").write_text(self.NO_PIN_SITE, encoding="utf-8")
        self.out = root / "out"
        self.out.mkdir()
        self.smap = gen_defs.surface_map(templates_root=self.tsrc, output_root=self.out)
        self.family = root / "fam.toml"
        self.family.write_text(_tiers_toml() + self.FAMILY, encoding="utf-8")
        self.entries = gen_defs.load_family(self.family).entries

    def _generate(self, *, tier_map=None):
        tier_map = dict(tier_map or gen_defs.DEFAULT_PIN_MAP)
        resolve = gen_defs.tier_resolver(self.entries, tier_map)
        tuning = _tuning(self.family, tier_map=tier_map, entries=self.entries)
        ok = _quiet(gen_defs.generate, _binding(self.CHUNKS), self.smap, overlays=resolve, tuning=tuning)
        return ok, resolve, tuning

    def _bodies(self):
        return (
            gen_defs.banner_body((self.out / "agents" / "tiered.md").read_text(encoding="utf-8")),
            gen_defs.banner_body((self.out / "commands" / "plain.md").read_text(encoding="utf-8")),
        )

    def test_each_output_resolves_against_its_own_tier(self):
        ok, _, _ = self._generate()
        self.assertTrue(ok)
        tiered, plain = self._bodies()
        self.assertIn("\nopus text\n", tiered)
        self.assertNotIn("haiku text", tiered)
        # An output with no pin site still takes the family-wide text — only
        # MEMBER-scoped entries require a tier.
        self.assertIn("\nfamily\n", plain)

    def test_the_rendered_pin_is_the_pin_map_value_not_the_member(self):
        self._generate(tier_map={**gen_defs.DEFAULT_PIN_MAP, "high": "haiku"})
        text = (self.out / "agents" / "tiered.md").read_text(encoding="utf-8")
        self.assertEqual(gen_defs.frontmatter_pin(text), "opus")
        self.assertIn("\nhaiku text\n", text)

    def test_mixed_render_round_trips_through_check(self):
        _, resolve, tuning = self._generate()
        self.assertTrue(_quiet(gen_defs.check, _binding(self.CHUNKS), self.smap, overlays=resolve, tuning=tuning))

    def test_no_rendered_output_carries_the_sentinel(self):
        self._generate()
        for body in self._bodies():
            self.assertNotIn(gen_defs.TIER_SENTINEL, body)

    def test_a_family_with_no_entries_renders_byte_identically_to_anchor_free(self):
        # SPEC's additive-only guarantee at render level: an unfilled anchor
        # leaves not a trace, so the bare render must equal — byte for byte —
        # the render of the same templates with the anchors deleted outright.
        bare = self.family.with_name("bare.toml")
        bare.write_text(_tiers_toml(), encoding="utf-8")
        self.assertTrue(
            _quiet(
                gen_defs.generate,
                _binding(self.CHUNKS),
                self.smap,
                overlays=gen_defs.tier_resolver({}, dict(gen_defs.DEFAULT_PIN_MAP)),
                tuning=_tuning(bare),
            )
        )
        bare_bodies = self._bodies()

        marker = "@!fam.probe!@"
        (self.tsrc / "agents" / "tiered.md.tmpl").write_text(self.TIERED.replace(marker, ""), encoding="utf-8")
        (self.tsrc / "commands" / "plain.md.tmpl").write_text(self.NO_PIN_SITE.replace(marker, ""), encoding="utf-8")
        _quiet(gen_defs.generate, _binding(self.CHUNKS), self.smap, tuning=_tuning(bare))
        self.assertEqual(bare_bodies, self._bodies())


class TestNestedTemplateMirroring(unittest.TestCase):
    """Recursive discovery and path mirroring: a template's relative subpath
    within its surface tree is its output's relative subpath within the
    surface, and every other mechanism applies unchanged at that nested path.
    """

    CHUNKS = {"shared": {"text": "shared text"}}
    BODY = "---\nname: @!arg.name!@\n---\nbody @!shared!@\n"

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        root = Path(self._tmp.name)
        self.tsrc = root / "templates"
        self.agent_templates = self.tsrc / "agents"
        self.nested_template = self.agent_templates / "mad" / "participant-contract.md.tmpl"
        self.nested_template.parent.mkdir(parents=True)
        for template in (
            self.agent_templates / "flat.md.tmpl",
            self.nested_template,
        ):
            template.write_text(self.BODY, encoding="utf-8")
        self.out = root / "out"
        self.out.mkdir()
        self.smap = gen_defs.surface_map(templates_root=self.tsrc, output_root=self.out)

    TUNING = _tuning(gen_defs.FAMILY_DIR / "claude.toml")

    def _generate(self):
        return _quiet(gen_defs.generate, _binding(self.CHUNKS), self.smap, tuning=self.TUNING)

    def test_template_targets_mirror_subpaths(self):
        targets = {template.name: out_dir for _, template, out_dir in gen_defs.template_targets(self.smap)}
        self.assertEqual(targets["flat.md.tmpl"], self.out / "agents")
        self.assertEqual(targets["participant-contract.md.tmpl"], self.out / "agents" / "mad")

    def test_nested_template_renders_to_mirrored_path(self):
        # The mirrored subdirectory does not exist beforehand — generation
        # creates it, since placement is declared by the template tree.
        self.assertFalse((self.out / "agents" / "mad").exists())
        self.assertTrue(self._generate())
        nested = self.out / "agents" / "mad" / "participant-contract.md"
        text = nested.read_text(encoding="utf-8")
        self.assertIn("name: participant-contract\n", text)
        self.assertIn("body shared text\n", text)
        self.assertTrue(gen_defs.banner_claim(text).endswith("agents/mad/participant-contract.md.tmpl"))

    def test_top_level_template_unaffected(self):
        self.assertTrue(self._generate())
        flat = self.out / "agents" / "flat.md"
        self.assertIn("name: flat\n", flat.read_text(encoding="utf-8"))
        self.assertFalse((self.out / "agents" / "mad" / "flat.md").exists())

    def test_generate_check_round_trip_over_nested_tree(self):
        self._generate()
        self.assertTrue(_quiet(gen_defs.check, _binding(self.CHUNKS), self.smap, tuning=self.TUNING))

    def test_check_two_reports_orphan_for_nested_banner(self):
        self._generate()
        self.nested_template.unlink()
        errors = gen_defs.check_banner_claims(self.smap)
        self.assertEqual(len(errors), 1)
        self.assertIn("ORPHAN", errors[0])
        self.assertIn("mad/participant-contract.md", errors[0])

    def test_bannerless_nested_md_is_ignored_by_check_two(self):
        # The recursive walk crosses supporting material (methodology topics,
        # tool docs). Anything without a banner is not this tool's business.
        self._generate()
        (self.out / "agents" / "topics").mkdir()
        (self.out / "agents" / "topics" / "note.md").write_text("# a topic, not a definition\n", encoding="utf-8")
        self.assertEqual(gen_defs.check_banner_claims(self.smap), [])


class TestBodyHashBanner(unittest.TestCase):
    """The banner's !BODY-SHA256! line: what it covers, and what it proves."""

    CHUNKS = {}
    TMPL = gen_defs.TEMPLATES_DIR / "agents" / "x.md.tmpl"

    TUNING = _tuning(gen_defs.FAMILY_DIR / "claude.toml")

    def _stamped(self, body):
        stamp = gen_defs.banner(self.TMPL, body_hash=gen_defs.sha256_text(body), tuning=self.TUNING)
        return "---\n" + stamp + "\n" + body

    def test_hash_covers_everything_after_the_banner(self):
        body = "name: x\n---\nthe prompt body\n"
        self.assertEqual(gen_defs.banner_body(self._stamped(body)), body)

    def test_untouched_output_is_provable(self):
        self.assertTrue(gen_defs.body_untouched(self._stamped("name: x\n---\nb\n")))

    def test_edited_body_breaks_the_proof(self):
        text = self._stamped("name: x\n---\nb\n") + "appended by hand\n"
        self.assertFalse(gen_defs.body_untouched(text))

    def test_pre_hash_banner_proves_nothing(self):
        old = (
            "---\n#\n# !GENERATED! from templates/agents/x.md.tmpl and "
            "templates/shared-chunks.toml — edit those. DO NOT HAND EDIT "
            "THIS FILE.\n#\nname: x\n---\nb\n"
        )
        self.assertIsNotNone(gen_defs.banner_claim(old))  # still a banner
        self.assertIsNone(gen_defs.banner_body(old))
        self.assertFalse(gen_defs.body_untouched(old))
        # Nothing to strip: check falls back to the whole file.
        self.assertEqual(gen_defs.comparable_body(old), old)

    def test_rendered_definitions_carry_a_true_hash(self):
        # In memory only — nothing is written under the root named here.
        smap = gen_defs.surface_map(gen_defs.REPO_ROOT)
        for target, rendered in gen_defs.all_renders(_binding(gen_defs.load_chunks()), smap, tuning=self.TUNING):
            self.assertTrue(gen_defs.body_untouched(rendered), gen_defs.rel(target))

    def test_no_shipped_render_carries_the_tier_sentinel(self):
        # The discovery pass's binding must never reach a written file. Checked
        # over the whole real set, in memory, under the shipped default triple.
        smap = gen_defs.surface_map(gen_defs.REPO_ROOT)
        for target, rendered in gen_defs.all_renders(_binding(gen_defs.load_chunks()), smap, tuning=self.TUNING):
            self.assertNotIn(gen_defs.TIER_SENTINEL, rendered, gen_defs.rel(target))

    def test_line_endings_are_translated_so_there_is_one_reading(self):
        # Generation reads its targets through read_text, whose universal
        # newlines hand a CRLF target here already translated; install reads
        # raw bytes and hands the CRLF through. Two answers to one question is
        # what made a tree nobody edited unprovable to one caller and provable
        # to the other.
        text = self._stamped("name: x\n---\nb\n")
        self.assertTrue(gen_defs.body_untouched(text.replace("\n", "\r\n")))
        self.assertTrue(gen_defs.body_untouched(text.replace("\n", "\r")))
        # Translating is not forgiving: an edit inside the widened file is
        # still an edit.
        self.assertFalse(gen_defs.body_untouched(text.replace("\n", "\r\n") + "by hand\r\n"))


class TestWriteSafetyBackupBranches(unittest.TestCase):
    """Regenerating over provably-untouched output writes no backup;
    regenerating over hand-edited bannered content still does."""

    CHUNKS = {"shared": {"text": "shared text"}}
    BODY = "---\nname: @!arg.name!@\n---\nbody @!shared!@\n"

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        root = Path(self._tmp.name)
        self.tsrc = root / "templates"
        (self.tsrc / "agents").mkdir(parents=True)
        self.template = self.tsrc / "agents" / "probe.md.tmpl"
        self.template.write_text(self.BODY, encoding="utf-8")
        self.out = root / "out"
        self.out.mkdir()
        self.smap = gen_defs.surface_map(templates_root=self.tsrc, output_root=self.out)
        self.target = self.out / "agents" / "probe.md"

    def _generate(self, chunks=None):
        return _quiet(
            gen_defs.generate,
            _binding(self.CHUNKS if chunks is None else chunks),
            self.smap,
            tuning=_tuning(gen_defs.FAMILY_DIR / "claude.toml"),
        )

    def _backups(self):
        return sorted(p.name for p in (self.out / "agents").glob("*.bak"))

    def test_untouched_target_is_overwritten_without_a_backup(self):
        self._generate()
        self.assertTrue(gen_defs.body_untouched(self.target.read_text("utf-8")))
        self._generate({"shared": {"text": "changed text"}})
        self.assertIn("changed text", self.target.read_text(encoding="utf-8"))
        self.assertEqual(self._backups(), [])

    def test_hand_edited_target_is_backed_up_before_overwrite(self):
        self._generate()
        edited = self.target.read_text(encoding="utf-8") + "hand-added line\n"
        self.target.write_text(edited, encoding="utf-8")
        self._generate({"shared": {"text": "changed text"}})
        self.assertEqual(self._backups(), ["probe.md.00.bak"])
        backup = (self.out / "agents" / "probe.md.00.bak").read_text(encoding="utf-8")
        self.assertEqual(backup, edited)

    def test_identical_render_still_writes_nothing(self):
        self._generate()
        stamp = self.target.stat().st_mtime_ns
        self._generate()
        self.assertEqual(self.target.stat().st_mtime_ns, stamp)
        self.assertEqual(self._backups(), [])


class TestInstallCopySet(unittest.TestCase):
    """An install is a plain recursive copy of both surfaces, plus the shipped
    packages at the destinations SHIPPED_PACKAGES names, minus the exclusion
    list — no complement computation, generated definitions included.

    The scratch source tree is laid out the way this repository is: the
    packages at the top level, reaching their `agents/…` destinations by their
    rows rather than by placement, and `agents/mad/` holding what the surface
    itself owns — the participant contract and the two topic sets."""

    SOURCE_FILES = (
        "agents/gen.md",  # a generated definition — copied like any other file
        "agents/hand.md",
        "agents/mad/participant-contract.md",  # the surface's own, not a package's
        "agents/mad/review-topics/topic.md",  # likewise: rendered into the surface
        "kb_tools/kb_util.py",
        "kb_tools/runner-snippets/kb.just",
        "kb_tools/tests/test_kb_util.py",  # excluded: tests/
        "kb_tools/tests/fixtures/mini-kb/index.md",  # excluded: tests/
        "kb_tools/__pycache__/kb_util.cpython-311.pyc",  # excluded
        "agents/.pytest_cache/CACHEDIR.TAG",  # excluded
        "agents/hand.md.00.bak",  # excluded: generator safety copy
        "agents/.DS_Store",  # excluded
        "commands/guest.md",
    )
    EXPECTED = {
        "agents/gen.md",
        "agents/hand.md",
        "agents/mad/participant-contract.md",
        "agents/mad/review-topics/topic.md",
        "agents/kb_tools/kb_util.py",
        "agents/kb_tools/runner-snippets/kb.just",
        "commands/guest.md",
    }

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        self.templates = self.root / "templates"
        (self.templates / "agents").mkdir(parents=True)
        self.src = self.root / "src"
        for name in self.SOURCE_FILES:
            path = self.src / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(f"content of {name}\n", encoding="utf-8")
        self.out = self.root / "out"
        self.out.mkdir()
        self.smap = gen_defs.surface_map(templates_root=self.templates, output_root=self.out)

    def _keys(self, smap=None):
        return {key for key, _, _ in gen_defs.install_pairs(self.smap if smap is None else smap, source_root=self.src)}

    def test_copy_set_is_the_surfaces_minus_the_exclusions(self):
        self.assertEqual(self._keys(), self.EXPECTED)

    def test_targets_mirror_source_subpaths(self):
        targets = {key: target for key, _, target in gen_defs.install_pairs(self.smap, source_root=self.src)}
        # A surface's own files mirror; a package's land where its row says
        # (TestShippedPackageMapping owns that half).
        self.assertEqual(
            targets["agents/mad/participant-contract.md"],
            self.out / "agents" / "mad" / "participant-contract.md",
        )
        self.assertEqual(targets["commands/guest.md"], self.out / "commands" / "guest.md")

    def test_surfaces_filter_narrows_the_copy_set(self):
        agents_only = gen_defs.surface_map(templates_root=self.templates, output_root=self.out, surfaces="agents")
        self.assertNotIn("commands/guest.md", self._keys(agents_only))

    def test_exclusion_predicate_is_depth_independent(self):
        for excluded in (
            "kb_tools/tests/fixtures/deep/note.md",
            "a/b/__pycache__/x.pyc",
            "x.md.07.bak",
            "sub/.DS_Store",
            "kb_tools/CONVENTIONS.md",
            "liaison_tools/docs/SPEC.md",
        ):
            self.assertTrue(gen_defs.excluded_from_install(Path(excluded)), excluded)
        # README is the one project-doc name a package writes for its consumer,
        # and a `.tmpl` is payload rather than documentation.
        for kept in (
            "hand.md",
            "agents/mad/review-topics/t.md",
            "kb_tools/kb_util.py",
            "kb_tools/README.md",
            "kb_tools/installed/CONVENTIONS.md.tmpl",
        ):
            self.assertFalse(gen_defs.excluded_from_install(Path(kept)), kept)


class TestShippedPackageMapping(unittest.TestCase):
    """A shipped package lands at the destination SHIPPED_PACKAGES names, not
    at the one its source placement implies.

    The destination `.claude/agents/kb_tools/…` is a consumer contract: runner
    snippets already installed in consuming projects import against it. The
    source side is this repository's to arrange. These cases pin the two apart
    by driving the SAME assertions over both source layouts — the package
    inside the surface, and the package at the repository root — and requiring
    identical keys and identical targets from each. What that buys is the
    guarantee that relocating a package is an edit to the table and to nothing
    else; if the two layouts ever disagree here, a consuming project's tree
    moves under it on the next install."""

    # Two layouts, one product. Each maps source path -> the SHIPPED_PACKAGES
    # rows that address it; the expected install is identical for both.
    NESTED = ("agents/kb_tools", "agents/liaison_tools")
    TOP_LEVEL = ("kb_tools", "liaison_tools")

    COMMON_FILES = ("agents/hand.md", "agents/mad/review-topics/topic.md", "commands/guest.md")
    PACKAGE_FILES = (
        "kb_tools/kb_util.py",
        "kb_tools/runner-snippets/kb.just",
        "kb_tools/tests/test_kb_util.py",  # excluded: tests/, at any source depth
        "liaison_tools/post-openai.py",
    )
    EXPECTED_KEYS = {
        "agents/hand.md",
        "agents/mad/review-topics/topic.md",
        "agents/kb_tools/kb_util.py",
        "agents/kb_tools/runner-snippets/kb.just",
        "agents/liaison_tools/post-openai.py",
        "commands/guest.md",
    }

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        self.templates = self.root / "templates"
        (self.templates / "agents").mkdir(parents=True)
        self.out = self.root / "out"
        self.out.mkdir()

    def _layout(self, prefix: str) -> Path:
        """A source tree with the packages rooted at `prefix`, and
        SHIPPED_PACKAGES pointed at them for the duration of the test."""
        source = self.root / f"src-{prefix or 'top'}"
        for name in self.COMMON_FILES:
            path = source / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(f"content of {name}\n", encoding="utf-8")
        for name in self.PACKAGE_FILES:
            path = source / prefix / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(f"content of {name}\n", encoding="utf-8")
        rows = self.NESTED if prefix else self.TOP_LEVEL
        original = gen_defs.SHIPPED_PACKAGES
        gen_defs.SHIPPED_PACKAGES = tuple(zip(rows, self.NESTED))
        self.addCleanup(setattr, gen_defs, "SHIPPED_PACKAGES", original)
        return source

    def _pairs(self, source: Path, surfaces: str = "both"):
        smap = gen_defs.surface_map(templates_root=self.templates, output_root=self.out, surfaces=surfaces)
        return gen_defs.install_pairs(smap, source_root=source)

    def test_both_source_layouts_install_the_same_keys(self):
        for prefix in ("agents", ""):
            with self.subTest(layout=prefix or "top-level"):
                keys = [key for key, _, _ in self._pairs(self._layout(prefix))]
                self.assertEqual(set(keys), self.EXPECTED_KEYS)
                # Emitted exactly once each: a package inside a surface is
                # carved out of that surface's walk rather than seen twice.
                self.assertEqual(len(keys), len(set(keys)))

    def test_both_source_layouts_install_to_the_same_targets(self):
        for prefix in ("agents", ""):
            with self.subTest(layout=prefix or "top-level"):
                targets = {key: target for key, _, target in self._pairs(self._layout(prefix))}
                self.assertEqual(
                    targets["agents/kb_tools/runner-snippets/kb.just"],
                    self.out / "agents" / "kb_tools" / "runner-snippets" / "kb.just",
                )
                self.assertEqual(
                    targets["agents/liaison_tools/post-openai.py"],
                    self.out / "agents" / "liaison_tools" / "post-openai.py",
                )

    def test_a_relocated_package_still_reads_its_source_from_the_new_place(self):
        # The destination is frozen; the SOURCE the bytes come from is not.
        pairs = {key: source for key, source, _ in self._pairs(self._layout(""))}
        self.assertEqual(pairs["agents/kb_tools/kb_util.py"], self.root / "src-top" / "kb_tools" / "kb_util.py")

    def test_pairs_are_ordered_by_destination_not_by_source_placement(self):
        # Keyed by destination, so the report and the per-surface counts do
        # not shift when a package's source moves.
        for prefix in ("agents", ""):
            with self.subTest(layout=prefix or "top-level"):
                keys = [key for key, _, _ in self._pairs(self._layout(prefix))]
                self.assertEqual(keys, sorted(keys))

    def test_surface_filter_drops_the_agent_side_packages(self):
        keys = {key for key, _, _ in self._pairs(self._layout(""), surfaces="commands")}
        self.assertEqual(keys, {"commands/guest.md"})

    def test_install_root_guard_covers_a_relocated_package_source(self):
        source = self._layout("")
        with self.assertRaises(gen_defs.TemplateError) as caught:
            gen_defs.assert_install_root(source / "kb_tools", source_root=source)
        self.assertIn("kb_tools/ package source", str(caught.exception))


class TestInstallSourceGuard(unittest.TestCase):
    """`install ROOT` refuses a ROOT overlapping a shipped package's source.

    Installing over a package source rewrites the files the copy is reading
    from — and the second run, whose content now hashes to its own banner,
    nests another one with no backup. The justfile's `<target>/.claude` shape
    is the case that must keep working."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        self.src = self.root / "src"
        for name in (
            "agents/hand.md",
            "kb_tools/kb_util.py",
            "kb_tools/kb_driver/run.py",
            "nested/pack/tool.sh",
            "commands/guest.md",
        ):
            path = self.src / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(f"content of {name}\n", encoding="utf-8")

    def _refuses(self, root: Path):
        with self.assertRaises(gen_defs.TemplateError) as caught:
            gen_defs.assert_install_root(root, source_root=self.src)
        return str(caught.exception)

    def test_root_that_contains_the_package_sources_is_refused(self):
        # `install .` from the repo root: every package source sits under it,
        # so the rows refuse it without a surface name being involved.
        message = self._refuses(self.src)
        self.assertIn("kb_tools/ package source", message)
        self.assertIn("copies from", message)

    def test_root_that_is_a_package_source_is_refused(self):
        self.assertIn("kb_tools/ package source", self._refuses(self.src / "kb_tools"))

    def test_root_inside_a_package_source_is_refused(self):
        self.assertIn("kb_tools/ package source", self._refuses(self.src / "kb_tools" / "kb_driver"))

    def test_a_root_above_a_nested_package_source_is_refused(self):
        # The guard reads the row rather than a top-level name, so a row whose
        # source sits below the repository root is guarded at its own depth: a
        # ROOT that merely CONTAINS one is refused. No row is nested today,
        # which is why this one is supplied.
        original = gen_defs.SHIPPED_PACKAGES
        gen_defs.SHIPPED_PACKAGES = original + (("nested/pack", "agents/pack"),)
        self.addCleanup(setattr, gen_defs, "SHIPPED_PACKAGES", original)
        self.assertIn("nested/pack/ package source", self._refuses(self.src / "nested"))

    def test_a_root_named_like_a_deployed_surface_is_an_ordinary_consumer(self):
        # `agents/` and `commands/` are not directories in this repository, so
        # there is nothing there to protect: a consumer whose path happens to
        # end in one is installed into like any other.
        gen_defs.assert_install_root(self.src / "agents", source_root=self.src)  # no raise
        gen_defs.assert_install_root(self.src / "commands", source_root=self.src)

    def test_a_root_reaching_the_source_through_a_symlink_is_refused(self):
        # Resolution happens before comparison, so neither a link nor a `..`
        # segment routes around the containment check.
        link = self.root / "link-to-src"
        link.symlink_to(self.src, target_is_directory=True)
        self.assertIn("package source", self._refuses(link))
        self.assertIn("package source", self._refuses(self.src / "agents" / ".." / "kb_tools"))

    def test_the_justfile_install_shape_still_passes(self):
        target = self.root / "consumer" / ".claude"
        target.mkdir(parents=True)
        gen_defs.assert_install_root(target, source_root=self.src)  # no raise
        # A sibling of the source root, and the source root's own parent's
        # sibling, are both ordinary consumers.
        gen_defs.assert_install_root(self.root / "consumer", source_root=self.src)

    def test_install_into_the_repo_root_refuses_before_writing_anything(self):
        # The real thing, end to end and against the real repository: no file
        # is touched, because the guard runs before the copy set is built.
        out = self.root / "out"
        out.mkdir()
        smap = gen_defs.surface_map(output_root=out)
        with self.assertRaises(gen_defs.TemplateError):
            _quiet(
                gen_defs.install,
                _binding(gen_defs.load_chunks()),
                smap,
                root=gen_defs.REPO_ROOT,
                tuning=_tuning(gen_defs.FAMILY_DIR / "claude.toml"),
            )
        self.assertEqual(sorted(out.rglob("*")), [])


class TestInstalledBanner(unittest.TestCase):
    """The !INSTALLED! banner a copied file carries: where each filetype puts
    it, and the hash mechanics it shares with the !GENERATED! banner."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.src = Path(self._tmp.name)

    def _source(self, name, text):
        path = self.src / name
        path.write_text(text, encoding="utf-8")
        return path

    def _lines(self, name, text, surface="agents"):
        content = gen_defs.install_content(self._source(name, text), surface=surface)
        return content.split("\n")

    def test_frontmatter_banner_sits_inside_the_block(self):
        lines = self._lines("hand.md", "---\nname: hand\n---\nprompt body\n")
        self.assertEqual(lines[0], "---")
        self.assertIn(gen_defs.INSTALLED_NOTICE, lines[2])
        self.assertTrue(lines[3].startswith("# !BODY-SHA256! "))
        self.assertEqual(lines[5], "name: hand")

    def test_bare_command_gets_a_banner_only_frontmatter_block(self):
        # A frontmatter-less command's first BODY line is the description
        # Claude Code lists the slash command by: the banner has to go above
        # it in frontmatter, not in front of it.
        lines = self._lines("guest-end.md", "End the session.\n\n## Behavior\n", surface="commands")
        self.assertEqual(lines[0], "---")
        self.assertIn(gen_defs.INSTALLED_NOTICE, lines[2])
        self.assertEqual(lines[5], "---")
        self.assertEqual(lines[6], "End the session.")

    def test_hash_comment_banner_tops_the_file(self):
        for name, first in (
            ("tool.py", '"""docstring."""'),
            ("kb.just", "# KB recipes"),
            ("kb.mk", "# KB targets"),
            ("conf.toml", "[table]"),
        ):
            lines = self._lines(name, first + "\n")
            self.assertIn(gen_defs.INSTALLED_NOTICE, lines[1], name)
            self.assertEqual(lines[4], first, name)

    def test_hash_comment_banner_sits_below_a_shebang(self):
        lines = self._lines("tool.sh", "#!/usr/bin/env bash\nset -eu\n")
        self.assertEqual(lines[0], "#!/usr/bin/env bash")
        self.assertIn(gen_defs.INSTALLED_NOTICE, lines[2])
        self.assertEqual(lines[5], "set -eu")

    def test_html_banner_wraps_agents_material_without_frontmatter(self):
        # Supporting material must not gain frontmatter: SPEC's
        # Guest-Extraction Contract requires extraction over a topic to fail
        # rather than yield a body, and frontmatter would make it succeed.
        lines = self._lines("topic.md", "# TOPIC: Review\n")
        self.assertEqual(lines[0], "<!--")
        self.assertIn(gen_defs.INSTALLED_NOTICE, lines[1])
        self.assertEqual(lines[3], "-->")
        self.assertEqual(lines[4], "# TOPIC: Review")

    def test_every_style_hashes_the_content_below_it(self):
        for surface, name, text in (
            ("agents", "hand.md", "---\nname: hand\n---\nprompt\n"),
            ("agents", "topic.md", "# TOPIC\n\ncontent\n"),
            ("commands", "bare.md", "Do the thing.\n"),
            ("agents", "tool.py", '"""d."""\ncode()\n'),
            ("agents", "tool.sh", "#!/bin/sh\nexec true\n"),
        ):
            stamped = gen_defs.install_content(self._source(name, text), surface=surface)
            # The shared mechanics read either banner kind: the body is what
            # follows the block, and it hashes to the block's own claim.
            self.assertTrue(gen_defs.body_untouched(stamped), name)
            self.assertTrue(stamped.endswith(gen_defs.banner_body(stamped)), name)
            self.assertFalse(gen_defs.body_untouched(stamped + "edit\n"), name)

    def test_generated_definition_is_exempt(self):
        template = gen_defs.TEMPLATES_DIR / "agents" / "x.md.tmpl"
        body = "name: x\n---\nbody\n"
        stamp = gen_defs.banner(
            template, body_hash=gen_defs.sha256_text(body), tuning=_tuning(gen_defs.FAMILY_DIR / "claude.toml")
        )
        generated = "---\n" + stamp + "\n" + body
        self.assertIsNone(gen_defs.install_content(self._source("gen.md", generated), surface="agents"))

    def test_vendor_carve_out_matches_a_directory_at_any_depth(self):
        for key in ("agents/kb_tools/_vendor/x.py", "agents/kb_tools/_vendor/pkg/deep/x.py"):
            self.assertTrue(gen_defs.vendored(Path(key)), key)
        # A file NAMED _vendor is not a vendored tree, and the carve-out reaches
        # no further than one.
        for key in ("agents/kb_tools/kb_util.py", "agents/kb_tools/_vendor", "agents/_vendored/x.py"):
            self.assertFalse(gen_defs.vendored(Path(key)), key)

    def test_comment_less_suffix_takes_no_banner(self):
        self.assertFalse(gen_defs.bannerable(Path("settings.json")))
        self.assertIsNone(gen_defs.install_content(self._source("s.json", "{}\n"), surface="agents"))
        for kept in ("a.md", "a.py", "a.sh", "a.toml", "a.mk", "a.just"):
            self.assertTrue(gen_defs.bannerable(Path(kept)), kept)


class TestInstallWriteSafety(unittest.TestCase):
    """Re-install safety for copied files: the banner's body hash separates a
    silent overwrite from a numbered backup, exactly as in generation. The
    overwrite itself is never in question — the tree is an artifact."""

    SOURCE = "---\nname: hand\n---\nprompt body\n"

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        root = Path(self._tmp.name)
        self.source = root / "hand.md"
        self.source.write_text(self.SOURCE, encoding="utf-8")
        self.target = root / "out" / "hand.md"

    def _install(self):
        return gen_defs.install_file(
            self.source,
            self.target,
            gen_defs.install_content(self.source, surface="agents"),
        )

    def _backups(self):
        return sorted(path.name for path in self.target.parent.glob("*.bak"))

    def test_absent_target_is_created_with_its_banner(self):
        self.assertEqual(self._install(), "written")
        text = self.target.read_text(encoding="utf-8")
        self.assertIn(gen_defs.INSTALLED_NOTICE, text)
        self.assertTrue(gen_defs.body_untouched(text))
        self.assertEqual(self._backups(), [])

    def test_matching_hash_is_overwritten_without_a_backup(self):
        self._install()
        self.source.write_text("---\nname: hand\n---\nrevised body\n", encoding="utf-8")
        self.assertEqual(self._install(), "written")
        self.assertIn("revised body", self.target.read_text(encoding="utf-8"))
        self.assertEqual(self._backups(), [])

    def test_mismatched_hash_is_backed_up_first(self):
        self._install()
        edited = self.target.read_text(encoding="utf-8") + "hand-added\n"
        self.target.write_text(edited, encoding="utf-8")
        self.assertEqual(self._install(), "backed up")
        self.assertEqual(self._backups(), ["hand.md.00.bak"])
        self.assertEqual(
            (self.target.parent / "hand.md.00.bak").read_text(encoding="utf-8"),
            edited,
        )
        # Replaced all the same: the backup protects the work, not the file.
        self.assertTrue(gen_defs.body_untouched(self.target.read_text("utf-8")))

    def test_unbannered_target_is_backed_up_first(self):
        # The 1.5.0-installed tree: copies landed with no banner at all, so
        # the first 1.6.0 install cannot prove any of them untouched.
        self.target.parent.mkdir(parents=True)
        self.target.write_text(self.SOURCE, encoding="utf-8")
        self.assertEqual(self._install(), "backed up")
        self.assertEqual(self._backups(), ["hand.md.00.bak"])

    def test_unchanged_target_is_not_rewritten(self):
        self._install()
        stamp = self.target.stat().st_mtime_ns
        self.assertEqual(self._install(), "unchanged")
        self.assertEqual(self.target.stat().st_mtime_ns, stamp)
        self.assertEqual(self._backups(), [])

    def test_crlf_normalized_target_is_replaced_without_a_backup(self):
        # A consuming checkout (autocrlf, or an editor that normalizes) widened
        # every newline in the installed tree. Nobody edited anything, so the
        # re-install must not accuse them of it — and must not leave a .bak per
        # file per run for the rest of the tree's life.
        self._install()
        widened = self.target.read_bytes().replace(b"\n", b"\r\n")
        self.target.write_bytes(widened)
        self.assertEqual(self._install(), "written")
        self.assertEqual(self._backups(), [])
        # Still not forgiving: an edit inside the widened file is an edit.
        self.target.write_bytes(widened + b"hand-added\r\n")
        self.assertEqual(self._install(), "backed up")
        self.assertEqual(self._backups(), ["hand.md.00.bak"])

    def test_verbatim_file_copies_and_still_backs_up_a_difference(self):
        # A filetype with no comment syntax gets no banner, so it can never be
        # proven ours — but an identical copy is still not a write.
        source = self.source.with_name("settings.json")
        source.write_text('{"a": 1}\n', encoding="utf-8")
        target = self.target.with_name("settings.json")

        def install():
            return gen_defs.install_file(
                source,
                target,
                gen_defs.install_content(source, surface="agents"),
            )

        self.assertEqual(install(), "written")
        self.assertEqual(target.read_text(encoding="utf-8"), '{"a": 1}\n')
        self.assertEqual(install(), "unchanged")
        target.write_text('{"a": 2}\n', encoding="utf-8")
        self.assertEqual(install(), "backed up")


class TestInstallWritePath(unittest.TestCase):
    """An install writes CONTENT and nothing else. An update goes through the
    target's own inode — no utime/chmod, which require ownership of a file
    another user may have installed — and only a file this run creates is
    chmodded, to carry the source's executable bit."""

    SOURCE = "---\nname: hand\n---\nprompt body\n"

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        self.source = self.root / "hand.md"
        self.source.write_text(self.SOURCE, encoding="utf-8")
        self.target = self.root / "out" / "hand.md"

    def _install(self, source=None, target=None):
        source = self.source if source is None else source
        target = self.target if target is None else target
        return gen_defs.install_file(source, target, gen_defs.install_content(source, surface="agents"))

    def _revise(self, body):
        self.source.write_text(f"---\nname: hand\n---\n{body}\n", encoding="utf-8")

    def _mode(self, path):
        return stat.S_IMODE(path.stat().st_mode)

    def test_update_keeps_the_target_inode(self):
        self._install()
        before = self.target.stat().st_ino
        self._revise("revised body")
        self.assertEqual(self._install(), "written")
        self.assertIn("revised body", self.target.read_text(encoding="utf-8"))
        self.assertEqual(self.target.stat().st_ino, before)

    def test_update_preserves_an_odd_target_mode(self):
        self._install()
        self.target.chmod(0o646)
        self._revise("revised body")
        self.assertEqual(self._install(), "written")
        self.assertEqual(self._mode(self.target), 0o646)

    def test_backup_branch_keeps_the_target_inode_too(self):
        self._install()
        self.target.write_text(
            self.target.read_text(encoding="utf-8") + "hand-added\n",
            encoding="utf-8",
        )
        before = self.target.stat().st_ino
        self.assertEqual(self._install(), "backed up")
        self.assertEqual(self.target.stat().st_ino, before)

    def test_backup_is_a_content_copy_with_its_own_identity(self):
        self._install()
        self.target.chmod(0o646)
        self.target.write_text(
            self.target.read_text(encoding="utf-8") + "hand-added\n",
            encoding="utf-8",
        )
        pre_overwrite = self.target.read_bytes()
        self._revise("revised body")
        self.assertEqual(self._install(), "backed up")
        backup = self.target.parent / "hand.md.00.bak"
        self.assertEqual(backup.read_bytes(), pre_overwrite)
        # A recovery artifact, not a mirror: its own inode, and the mode this
        # process gives a file it creates — nothing cloned off the target,
        # which cloning would have required ownership of.
        self.assertNotEqual(backup.stat().st_ino, self.target.stat().st_ino)
        probe = self.target.parent / "probe"
        probe.write_bytes(b"")
        self.assertEqual(self._mode(backup), self._mode(probe))

    def test_creation_carries_the_sources_exec_bit(self):
        # An installed tool has to land runnable, and stamping rewrites the
        # content rather than copying it. The source is a shell script because
        # no shipped package holds one any more: `.sh` is in the set the
        # generator stamps, and this fixture is the only thing exercising it.
        source = self.root / "tool.sh"
        source.write_text("#!/usr/bin/env bash\nexec true\n", encoding="utf-8")
        source.chmod(0o755)
        target = self.target.with_name("tool.sh")
        self.assertEqual(self._install(source, target), "written")
        self.assertTrue(self._mode(target) & 0o111)
        self.assertIn(gen_defs.INSTALLED_NOTICE, target.read_text(encoding="utf-8"))

        # And survives an update, which preserves the mode by not touching it.
        source.write_text("#!/usr/bin/env bash\nexec false\n", encoding="utf-8")
        self.assertEqual(self._install(source, target), "written")
        self.assertTrue(self._mode(target) & 0o111)

    def test_creation_leaves_a_non_executable_source_non_executable(self):
        self.assertFalse(self._mode(self.source) & 0o111)
        self._install()
        self.assertFalse(self._mode(self.target) & 0o111)


class TestQuietPass(unittest.TestCase):
    """The install passes' output buffer: held back when there is nothing to
    say, released when there is — including when the pass raises."""

    def _drive(self, run, *, verbose=False):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            with contextlib.suppress(gen_defs.TemplateError):
                ok = gen_defs.quiet_pass(run, verbose=verbose)
                self.assertIsInstance(ok, bool)
        return buf.getvalue()

    def test_a_clean_pass_says_nothing(self):
        self.assertEqual(self._drive(lambda: (print("every file OK"), True)[1]), "")

    def test_a_clean_pass_still_says_everything_under_verbose(self):
        self.assertIn("every file OK", self._drive(lambda: (print("every file OK"), True)[1], verbose=True))

    def test_an_unclean_pass_releases_its_report(self):
        self.assertIn("DRIFT", self._drive(lambda: (print("DRIFT here"), False)[1]))

    def test_a_raising_pass_releases_its_report_too(self):
        # The buffer is the only copy of how far the pass got, and the run that
        # raises is the one where that matters most. Without the finally, the
        # operator gets an error naming a template and no indication of what
        # the pass had already covered.
        def run():
            print("  OK      agents/first.md")
            raise gen_defs.TemplateError("unknown chunk or placeholder 'no-such-chunk'")

        self.assertIn("agents/first.md", self._drive(run))

    def test_the_exception_still_propagates(self):
        def run():
            raise gen_defs.TemplateError("boom")

        with contextlib.redirect_stdout(io.StringIO()):
            with self.assertRaises(gen_defs.TemplateError):
                gen_defs.quiet_pass(run, verbose=False)


class TestInstallPassContract(unittest.TestCase):
    """What install()'s integrity pass renders under, and what the operator is
    told when it raises.

    A scratch repository stands in for this one: one template whose output
    declares a tier the family gives overlay overrides for, and a source tree
    holding that output's render — the state `just generate` leaves behind.
    """

    CHUNKS: dict[str, dict] = {}
    BODY = "---\nname: probe\nmodel: @!dyn.tier-medium!@\n---\nbody\n@!fam.probe!@\n"

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        root = Path(self._tmp.name)
        self.src = root / "src"
        self.templates = self.src / "templates"
        (self.templates / "agents").mkdir(parents=True)
        (self.templates / "commands").mkdir()
        self.template = self.templates / "agents" / "probe.md.tmpl"
        self.template.write_text(self.BODY, encoding="utf-8")
        family_dir = self.templates / "family"
        family_dir.mkdir()
        self.family = family_dir / "fam.toml"
        self.family.write_text(
            _tiers_toml() + '[family.probe.models.sonnet]\ntext = "watch the tier"\n', encoding="utf-8"
        )

        entries = gen_defs.load_family(self.family).entries
        self.overlays = gen_defs.tier_resolver(entries, dict(gen_defs.DEFAULT_PIN_MAP))
        self.tuning = _tuning(self.family, entries=entries)
        self.binding = _binding(self.CHUNKS)
        self.src.mkdir(exist_ok=True)
        surfaces = gen_defs.surface_map(templates_root=self.templates, output_root=self.src)
        _quiet(gen_defs.generate, self.binding, surfaces, overlays=self.overlays, tuning=self.tuning)
        self.source_def = self.src / "agents" / "probe.md"
        self.assertIn("watch the tier", self.source_def.read_text(encoding="utf-8"))

        self.root = root / "consumer"
        self.root.mkdir()
        self.smap = gen_defs.surface_map(templates_root=self.templates, output_root=self.root)

    def _install(self, **kwargs):
        kwargs.setdefault("tuning", self.tuning)
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            ok = gen_defs.install(self.binding, self.smap, root=self.root, source_root=self.src, **kwargs)
        return ok, buf.getvalue()

    def test_byte_perfect_install_of_a_tiered_output_is_clean(self):
        ok, report = self._install(overlays=self.overlays)
        self.assertTrue(ok)
        installed = self.root / "agents" / "probe.md"
        self.assertEqual(installed.read_bytes(), self.source_def.read_bytes())
        self.assertNotIn("DRIFT", report)
        self.assertNotIn("NOT CLEAN", report)
        self.assertEqual(len(report.splitlines()), 1, report)

    def test_the_same_install_without_the_resolver_installs_the_wrong_bytes(self):
        # The teeth. `overlays` unpassed is the pre-fix call site: since the render IS
        # the install, the omission is silent and consequential — the consumer
        # receives a definition missing the overrides its own tier resolves. So
        # the assertion sits on the delivered file, where the damage lands.
        installed = self.root / "agents" / "probe.md"
        self._install()
        self.assertNotIn("watch the tier", installed.read_text(encoding="utf-8"))
        self._install(overlays=self.overlays)
        self.assertIn("watch the tier", installed.read_text(encoding="utf-8"))
        self.assertEqual(installed.read_bytes(), self.source_def.read_bytes())

    def test_the_same_install_without_the_triple_mislabels_every_banner(self):
        # The other half of the C3 guard: a pass handed no triple would stamp a
        # banner claiming something the run did not run under, so a correct
        # install reports its own tree as MISTUNED on the next check.
        ok, _ = self._install(overlays=self.overlays)
        self.assertTrue(ok)
        claim = gen_defs.tuning_claim((self.root / "agents" / "probe.md").read_text(encoding="utf-8"))
        self.assertEqual(claim.family, gen_defs.rel(self.family))
        self.assertEqual((claim.seat, claim.member), ("medium", "sonnet"))

    def test_a_raising_pass_still_names_what_landed(self):
        # The copy completes before the render runs, so a template that cannot
        # render still leaves the copied half live under ROOT. An operator
        # shown only "error: unknown chunk" reads that as "nothing happened".
        self.template.write_text(self.BODY.replace("body\n", "body @!no-such-chunk!@\n"), encoding="utf-8")
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            with self.assertRaises(gen_defs.TemplateError):
                gen_defs.install(
                    self.binding,
                    self.smap,
                    root=self.root,
                    source_root=self.src,
                    overlays=self.overlays,
                    tuning=self.tuning,
                )
        report = buf.getvalue()
        self.assertIn(f"installed: 1 under agents/ → {gen_defs.rel(self.root)}", report)
        self.assertIn("the packages are in place", report)
        # Said because it is true: the tree really is live.
        self.assertEqual(
            (self.root / "agents" / "probe.md").read_bytes(),
            self.source_def.read_bytes(),
        )


class TestInstallEndToEnd(unittest.TestCase):
    """Real installs of this repository into scratch targets: default-triple,
    tuned, and re-installed."""

    # A hand-maintained agent definition: frontmatter of its own, no
    # !GENERATED! banner. That is the only shape the FRONTMATTER-style
    # !INSTALLED! banner is stamped into, and no definition in either deployed
    # surface has that shape any more, so the fixture is built here — the
    # invariant belongs to the file class, not to some file staying
    # hand-maintained.
    HAND_FRONTMATTER = "---\nname: probe-guest\ndescription: scratch stand-in for a hand-maintained agent.\n---\n"
    HAND_BODY = "\nThe prompt a guest model is relayed.\n\n## Behavior\n\nA last line, which must survive.\n"

    # Third-party source as a shipped package would vendor it. The vendored
    # file and its non-vendored sibling are byte-identical on purpose: what
    # decides whether a banner is stamped is where the file lands, and nothing
    # else about it.
    VENDOR_SOURCE = '"""Upstream module: ours to ship, never ours to claim."""\n\n\ndef parse(text):\n    return text\n'

    @classmethod
    def setUpClass(cls):
        cls.chunks = gen_defs.load_chunks()
        # The triple `just install <target>` runs under, built the way main()
        # builds it, so an end-to-end install here is the shipped default one.
        family_path = gen_defs.FAMILY_DIR / "claude.toml"
        cls.family = gen_defs.load_family(family_path)
        cls.tuning = gen_defs.effective_tuning(family_path, cls.family)
        # staticmethod: a plain function on a class would bind as a method and
        # arrive at render_output with `self` prepended.
        cls.overlays = staticmethod(gen_defs.tier_resolver(cls.family.entries, cls.tuning.tier_map))
        cls.binding = gen_defs.tier_binding(cls.chunks, pin_map=cls.tuning.pin_map)

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name) / ".claude"
        self.root.mkdir()
        self.smap = gen_defs.surface_map(output_root=self.root)

    def _defaults(self, kwargs):
        kwargs.setdefault("overlays", self.overlays)
        kwargs.setdefault("tuning", self.tuning)
        kwargs.setdefault("binding", self.binding)
        return kwargs.pop("binding"), kwargs

    def _install(self, **kwargs):
        binding, kwargs = self._defaults(kwargs)
        return _quiet(gen_defs.install, binding, self.smap, root=self.root, **kwargs)

    def _install_report(self, **kwargs) -> str:
        binding, kwargs = self._defaults(kwargs)
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            gen_defs.install(binding, self.smap, root=self.root, **kwargs)
        return buf.getvalue()

    def _installed(self):
        return {path.relative_to(self.root).as_posix() for path in self.root.rglob("*") if path.is_file()}

    def test_plain_install_delivers_the_whole_product(self):
        self.assertTrue(self._install())
        installed = self._installed()
        # The failing case this mode exists for: /kb-start references
        # @.claude/agents/kb-docent.md, which no rendered set contains.
        for expected in (
            "agents/kb-docent.md",
            "agents/security-reviewer.md",
            "agents/guest-liaison.md",
            "agents/python-coder.md",
            "agents/mad/participant-contract.md",
            "agents/kb_tools/kb_util.py",
            "agents/kb_tools/installed/CONVENTIONS.md.tmpl",
            "agents/kb_tools/runner-snippets/kb.just",
            "agents/liaison_tools/post-openai.py",
            "commands/guest.md",
        ):
            self.assertIn(expected, installed)
        self.assertTrue(any(name.startswith("agents/mad/design-topics/") for name in installed))
        # A consuming project installs the code, not the project it came from:
        # no file documenting THIS repository to its own developers reaches a
        # consumer tree. Swept over the whole manifest rather than over one
        # package's directory, because the rule is a filename vocabulary and
        # not a placement — a doc reappearing one level down would pass a
        # directory-scoped pin. The vocabulary is spelled out here rather than
        # read off gen_defs, so a name quietly leaving the exclusion set fails
        # against a second statement of it instead of agreeing with itself.
        project_docs = {"SPEC.md", "ARCHITECTURE.md", "CONVENTIONS.md", "ROADMAP.md", "AGENTS.md", "CLAUDE.md"}
        self.assertEqual(sorted(name for name in installed if Path(name).name in project_docs), [])
        # The suffix is what keeps the sweep off the payload: this template is
        # a file a build writes into a consuming project's own KB, and it must
        # keep shipping.
        self.assertIn("agents/kb_tools/installed/CLAUDE.md.tmpl", installed)

    def test_plain_install_carries_no_test_suites_or_caches(self):
        self._install()
        stray = sorted(
            name
            for name in self._installed()
            # Drop the surface component: the exclusion rule is written
            # against surface-relative paths.
            if gen_defs.excluded_from_install(Path(*Path(name).parts[1:]))
        )
        self.assertEqual(stray, [])
        self.assertFalse((self.root / "agents" / "kb_tools" / "tests").exists())
        self.assertFalse((self.root / "agents" / "liaison_tools" / "tests").exists())

    def test_plain_install_renders_the_definitions_and_leaves_no_backups(self):
        report = self._install_report()
        # A default install renders like any other — the definitions are not
        # copied from anywhere — but the summary says nothing about it: a
        # default triple is the non-event, and only a triple differing from it
        # earns a clause.
        self.assertNotIn("rendered", report)
        self.assertIn("!GENERATED!", (self.root / "agents" / "python-coder.md").read_text(encoding="utf-8"))
        # Every target was fresh, so nothing was set aside.
        self.assertEqual(sorted(self.root.rglob("*.bak")), [])

    def test_default_install_into_a_fresh_root_reports_integrity_ok(self):
        # The C3 guard: once every banner claims a triple, an integrity pass
        # handed no triple and no resolver would report the whole tree
        # MISTUNED on a correct install — non-gating, and therefore worse,
        # because it trains the operator to ignore the one real signal.
        # quiet_pass prints nothing on a clean pass, so the verdict is the
        # assertion, not the absence of output.
        ok = self._install()
        self.assertTrue(ok)
        report = self._install_report()
        self.assertNotIn("NOT CLEAN", report)
        self.assertNotIn("MISTUNED", report)
        self.assertEqual(len(report.splitlines()), 1, report)

    def test_every_installed_banner_claims_the_triple_the_install_ran_under(self):
        self._install()
        claims = {
            path: gen_defs.tuning_claim(path.read_text(encoding="utf-8"))
            for path in sorted((self.root / "agents").glob("*.md"))
            if gen_defs.banner_claim(path.read_text(encoding="utf-8")) is not None
        }
        self.assertTrue(claims)
        for path, claim in claims.items():
            self.assertEqual(claim.family, gen_defs.rel(self.tuning.family), gen_defs.rel(path))
            self.assertEqual(claim.pin, gen_defs.map_spec(self.tuning.pin_map), gen_defs.rel(path))

    def test_no_installed_file_carries_the_tier_sentinel(self):
        self._install()
        for path in sorted(self.root.rglob("*.md")):
            self.assertNotIn(gen_defs.TIER_SENTINEL, path.read_text(encoding="utf-8"), gen_defs.rel(path))

    def test_clean_install_is_one_summary_line(self):
        # Report by exception: a fresh install is a non-event. The whole report
        # is what landed where — no per-file listing, no integrity chrome, and
        # no restatement of the artifact contract that --help already carries.
        lines = self._install_report().splitlines()
        self.assertEqual(len(lines), 1, lines)
        self.assertRegex(lines[0], r"^installed: \d+ under agents/, \d+ under commands/ → ")
        self.assertTrue(lines[0].endswith(gen_defs.rel(self.root)), lines[0])

    def test_clean_reinstall_stays_one_line_and_names_no_unbannered_file(self):
        # A clean overwrite is as much a non-event as a fresh install, and the
        # unbannered set is a property of the copy set's filetypes — identical
        # every run, so it is documentation rather than something to report.
        self._install()
        report = self._install_report()
        self.assertEqual(len(report.splitlines()), 1, report)
        self.assertNotIn("unbannered", report)
        self.assertNotIn(".tmpl", report)

    def test_replaced_local_edit_is_the_only_thing_reported(self):
        self._install()
        edited = self.root / "agents" / "kb_tools" / "kb_util.py"
        edited.write_text(edited.read_text(encoding="utf-8") + "\n# local edit\n", encoding="utf-8")
        report = self._install_report()
        # The one target whose extant content this tool did not write is named,
        # and its prior bytes are beside it.
        self.assertIn("agents/kb_tools/kb_util.py", report)
        self.assertEqual([path.name for path in self.root.rglob("*.bak")], ["kb_util.py.00.bak"])
        # Named specifically: the other ~130 clean overwrites stay silent.
        self.assertEqual(
            [line for line in report.splitlines() if line.startswith("  ")],
            ["  agents/kb_tools/kb_util.py"],
        )

    def test_verbose_lists_every_file_and_names_the_unbannered_ones(self):
        report = self._install_report(verbose=True)
        # Both halves report: a copied package file by its ROOT-relative key,
        # a rendered definition by the generation pass's own created/updated
        # line, which names an absolute target.
        self.assertIn("  written    agents/kb_tools/kb_util.py", report)
        self.assertIn(f"  created    {gen_defs.rel(self.root / 'agents' / 'python-coder.md')}", report)
        unbannered = [line for line in report.splitlines() if line.startswith("unbannered")]
        self.assertEqual(len(unbannered), 1, report)
        expected = [key for key, source, _ in gen_defs.install_pairs(self.smap) if not gen_defs.bannerable(source)]
        self.assertIn(f"{len(expected)} file(s)", unbannered[0])
        self.assertIn(expected[0], unbannered[0])

    def _vendoring_source_root(self) -> Path:
        """A scratch shipped-package source carrying a `_vendor/` tree beside an
        ordinary module, travelling the real SHIPPED_PACKAGES row for kb_tools.
        Built here rather than read from the repository: the carve-out belongs
        to the path class, not to any package currently vendoring anything."""
        src = Path(self._tmp.name) / "vendor-src"
        vendor = src / "kb_tools" / "_vendor"
        (vendor / "upstream").mkdir(parents=True)
        (vendor / "upstream" / "walker.py").write_text(self.VENDOR_SOURCE, encoding="utf-8")
        (vendor / "LICENSE.txt").write_text("MIT, upstream's.\n", encoding="utf-8")
        (src / "kb_tools" / "sibling.py").write_text(self.VENDOR_SOURCE, encoding="utf-8")
        return src

    def test_vendored_source_ships_verbatim_and_its_sibling_is_still_stamped(self):
        src = self._vendoring_source_root()
        self._install(source_root=src)
        vendored = self.root / "agents" / "kb_tools" / "_vendor" / "upstream" / "walker.py"
        # It ships: a stamping carve-out, never an exclusion.
        self.assertTrue(vendored.is_file())
        self.assertEqual(vendored.read_bytes(), self.VENDOR_SOURCE.encode("utf-8"))
        self.assertNotIn(gen_defs.INSTALLED_NOTICE, vendored.read_text(encoding="utf-8"))
        self.assertEqual(
            (self.root / "agents" / "kb_tools" / "_vendor" / "LICENSE.txt").read_text(encoding="utf-8"),
            "MIT, upstream's.\n",
        )
        # Narrow: identical bytes one directory up are stamped exactly as ever.
        sibling = (self.root / "agents" / "kb_tools" / "sibling.py").read_text(encoding="utf-8")
        self.assertIn(gen_defs.INSTALLED_NOTICE, sibling)
        self.assertTrue(gen_defs.body_untouched(sibling))
        self.assertEqual(gen_defs.banner_body(sibling), self.VENDOR_SOURCE)
        # Re-vendoring the same bytes is a no-op: an unstamped file is never
        # provably ours, so only a CHANGED one can cost a numbered backup.
        self._install(source_root=src)
        self.assertEqual(sorted(self.root.rglob("*.bak")), [])

    def test_verbose_report_names_the_vendored_file_as_unstamped(self):
        report = self._install_report(source_root=self._vendoring_source_root(), verbose=True)
        unstamped = [line for line in report.splitlines() if line.startswith("unstamped")]
        unbannered = [line for line in report.splitlines() if line.startswith("unbannered")]
        self.assertEqual(len(unstamped), 1, report)
        self.assertEqual(len(unbannered), 1, report)
        self.assertIn("agents/kb_tools/_vendor/upstream/walker.py", unstamped[0])
        # One bucket per file, one reason each: the vendored .txt is verbatim
        # because its type admits no comment, which is the older reason and
        # still the right one for it.
        self.assertNotIn("LICENSE", unstamped[0])
        self.assertIn("agents/kb_tools/_vendor/LICENSE.txt", unbannered[0])
        self.assertNotIn("walker.py", unbannered[0])

    def test_reinstall_is_idempotent(self):
        self._install()
        first = {
            name: ((self.root / name).read_bytes(), (self.root / name).stat().st_mtime_ns) for name in self._installed()
        }
        self._install()
        second = {
            name: ((self.root / name).read_bytes(), (self.root / name).stat().st_mtime_ns) for name in self._installed()
        }
        # Byte-stable and not even rewritten: the banner text is deterministic
        # and an identical target is skipped outright.
        self.assertEqual(first, second)
        self.assertEqual(sorted(self.root.rglob("*.bak")), [])

    def test_installed_copies_are_bannered_and_sources_are_not(self):
        # Every source an install COPIES — which is now the shipped packages
        # and nothing else, the definitions being rendered rather than read
        # from anywhere. Each package source sits at the repository top level
        # and is stamped on the way out, so each has a never-stamped-here
        # claim to keep. Listing the eliminated surfaces here would make the
        # walk silently empty and the assertion vacuous.
        surfaces = [gen_defs.REPO_ROOT / source for source, _ in gen_defs.SHIPPED_PACKAGES]
        before = {
            path: gen_defs.sha256_text(path.read_text(encoding="utf-8"))
            for surface in surfaces
            for path in sorted(surface.rglob("*.md"))
        }
        self._install()
        after = {
            path: gen_defs.sha256_text(path.read_text(encoding="utf-8"))
            for surface in surfaces
            for path in sorted(surface.rglob("*.md"))
        }
        # The banners exist only in the installed copies.
        self.assertEqual(before, after)
        for source in surfaces:
            for path in source.rglob("*.md"):
                self.assertNotIn(
                    gen_defs.INSTALLED_NOTICE,
                    path.read_text(encoding="utf-8"),
                    gen_defs.rel(path),
                )
        # One row per banner style the real product can exercise: `#` comments
        # in a shipped .py, and the exempt case, a generated definition
        # arriving with its own !GENERATED! banner. Neither the frontmatter nor
        # the HTML style has a row, for the same reason in both cases — no file
        # the product delivers takes either. Every *.md with frontmatter under
        # the surfaces is generated and so exempt, and the frontmatter-less
        # markdown the HTML style existed for was a package's own
        # documentation, which no longer installs. Both are covered on scratch
        # fixtures: test_hand_maintained_definition_takes_a_frontmatter_banner
        # and test_html_banner_wraps_agents_material_without_frontmatter.
        for name, bannered in (
            ("agents/kb_tools/kb_util.py", True),
            ("agents/python-coder.md", False),
        ):
            text = (self.root / name).read_text(encoding="utf-8")
            self.assertEqual(gen_defs.INSTALLED_NOTICE in text, bannered, name)
            self.assertTrue(gen_defs.body_untouched(text), name)

    def test_bare_command_keeps_its_first_body_line(self):
        # The fixture is built here rather than read from commands/: the
        # invariant belongs to the file class — any frontmatter-less command —
        # and not to some definition staying hand-maintained, which no command
        # in the surface is any more. A scratch source root overlaid onto the
        # installed tree still travels the real install walk, which is what
        # reads a bare *.md's surface and picks its banner style from it.
        self._install()
        src = Path(self._tmp.name) / "src"
        (src / "commands").mkdir(parents=True)
        source = "End the active guest-model session pointer.\n\n## Behavior\n"
        (src / "commands" / "bare.md").write_text(source, encoding="utf-8")
        self._install(source_root=src)
        installed = (self.root / "commands" / "bare.md").read_text(encoding="utf-8")
        self.assertIn(gen_defs.INSTALLED_NOTICE, installed)
        # The banner lands in a frontmatter block of its own, above content
        # that is otherwise byte-identical — the first body line, which Claude
        # Code lists a frontmatter-less command by, is still the source's.
        self.assertEqual(gen_defs.banner_body(installed), "---\n" + source)

    def _extract(self, path):
        """The extraction the liaison definitions run, verbatim: drop every line
        through the one that closes the frontmatter block. Run as a subprocess
        rather than reimplemented in python, because the command line in those
        definitions is the thing under test."""
        return subprocess.run(
            ["sed", "1,/^---$/d", str(path)],
            capture_output=True,
            text=True,
            check=False,
        )

    def _install_hand_maintained_agent(self) -> Path:
        """Install one hand-maintained definition (HAND_FRONTMATTER +
        HAND_BODY) from a scratch source root, overlaid onto the real installed
        tree so it travels the real install walk — which is what reads a *.md's
        own frontmatter and picks the banner style from it. Returns its path
        under ROOT."""
        self._install()
        src = Path(self._tmp.name) / "hand-src"
        (src / "agents").mkdir(parents=True)
        (src / "agents" / "probe-guest.md").write_text(self.HAND_FRONTMATTER + self.HAND_BODY, encoding="utf-8")
        self._install(source_root=src)
        return self.root / "agents" / "probe-guest.md"

    def test_hand_maintained_definition_takes_a_frontmatter_banner(self):
        # A *.md carrying frontmatter of its own gets the banner as comment
        # lines INSIDE that block, where every frontmatter reader drops it: not
        # a second block above the definition's own, and not an HTML comment,
        # either of which would put banner text where a prompt body is read.
        installed = self._install_hand_maintained_agent().read_text(encoding="utf-8")
        self.assertIn(gen_defs.INSTALLED_NOTICE, installed)
        self.assertTrue(gen_defs.body_untouched(installed))
        # Still exactly one frontmatter block, and every byte the source had
        # below its opening delimiter is still there, unshifted.
        self.assertEqual(installed.count("---\n"), 2, installed)
        self.assertEqual(
            gen_defs.banner_body(installed),
            (self.HAND_FRONTMATTER + self.HAND_BODY)[len("---\n") :],
        )

    @unittest.skipUnless(shutil.which("sed"), "extraction needs sed")
    def test_installed_definition_still_extracts_frontmatter_free(self):
        # The frontmatter-style !INSTALLED! banner has to survive contact with
        # the real consumer of an installed definition's body: the guest-relay
        # extraction, which must drop the whole block and lose no body content.
        done = self._extract(self._install_hand_maintained_agent())
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertNotIn(gen_defs.INSTALLED_NOTICE, done.stdout)
        self.assertNotIn("!BODY-SHA256!", done.stdout)
        # Byte-identical to the body that went in: the banner cost the prompt
        # nothing at either end.
        self.assertEqual(done.stdout, self.HAND_BODY)

    @unittest.skipUnless(shutil.which("sed"), "extraction needs sed")
    def test_every_generated_definition_extracts_frontmatter_free(self):
        # SPEC's Guest-Extraction Contract, enforced where a definition is
        # BUILT rather than where it is read: one that would reach a guest
        # model truncated fails here. Every rendered *.md under agents/, not a
        # sample — the property is a claim about the surface, and a sampled
        # check would pass on the day one definition broke it.
        self._install()
        rendered = [
            path
            for path in sorted((self.root / "agents").rglob("*.md"))
            if gen_defs.banner_claim(path.read_text(encoding="utf-8")) is not None
        ]
        self.assertGreater(len(rendered), 20, "the walk found no definitions to check")
        for path in rendered:
            with self.subTest(definition=path.relative_to(self.root).as_posix()):
                done = self._extract(path)
                self.assertEqual(done.returncode, 0, done.stderr)
                self.assertNotIn("!GENERATED!", done.stdout)
                self.assertNotIn("!BODY-SHA256!", done.stdout)
                # No body content lost: the frontmatter block and what came
                # out reconstitute the file byte for byte.
                text = path.read_text(encoding="utf-8")
                front = gen_defs.frontmatter_of(text)
                self.assertEqual(text, "---\n" + front + "\n---\n" + done.stdout)

    def test_a_non_default_triple_renders_and_earns_its_summary_clause(self):
        family_path = gen_defs.FAMILY_DIR / "gemma-4.toml"
        family = gen_defs.load_family(family_path)
        tuning = gen_defs.effective_tuning(family_path, family)
        self.assertFalse(tuning.is_default)
        binding = gen_defs.tier_binding(self.chunks, pin_map=tuning.pin_map)
        overlays = gen_defs.tier_resolver(family.entries, tuning.tier_map)

        # The default-triple render to compare against, produced the same way:
        # a comparison between two delivered artifacts, not a reconstruction.
        base_root = Path(self._tmp.name) / "base"
        base_root.mkdir()
        _quiet(
            gen_defs.install,
            self.binding,
            gen_defs.surface_map(base_root),
            root=base_root,
            overlays=self.overlays,
            tuning=self.tuning,
        )
        plain_body = gen_defs.banner_body((base_root / "agents" / "go-coder.md").read_text(encoding="utf-8"))

        report = self._install_report(binding=binding, overlays=overlays, tuning=tuning)
        # A non-default render IS an event: the one summary line says how many
        # and under what — and the pass's own file-by-file report stays back.
        self.assertEqual(len(report.splitlines()), 1, report)
        self.assertRegex(report, r"; \d+ rendered under family templates/family/gemma-4\.toml, tier\[")
        # No backups anywhere: every rendered target was a fresh copy, and so
        # provably this tool's own output.
        self.assertEqual(sorted(self.root.rglob("*.bak")), [])
        # Each anchor lands in the one definition that authors it. The text
        # renders verbatim, so the family's own first line is what to look
        # for — read out of the family file rather than transcribed.
        scorer_tuned = (self.root / "agents" / "kb-claim-scorer.md").read_text(encoding="utf-8")
        self.assertIn(family.entries["fam.gap-aversion"]["text"].splitlines()[0], scorer_tuned)
        tuned = (self.root / "agents" / "applied-mathematician.md").read_text(encoding="utf-8")
        self.assertIn(family.entries["fam.ask-vs-stipulate"]["text"].splitlines()[0], tuned)
        claim = gen_defs.tuning_claim(tuned)
        self.assertEqual(claim.family, "templates/family/gemma-4.toml")
        self.assertIn("gemma-4-31B-it", claim.tier)
        # A family member never reaches a pin: the tokens come from the pin map
        # alone, so the rendered pins are claude-legal under any family.
        self.assertEqual(claim.pin, gen_defs.map_spec(gen_defs.DEFAULT_PIN_MAP))
        # A definition the family does not touch keeps a byte-identical body;
        # only its banner records the triple it was rendered under.
        untouched = (self.root / "agents" / "go-coder.md").read_text(encoding="utf-8")
        self.assertEqual(gen_defs.banner_body(untouched), plain_body)


class TestShippedFamilyFiles(unittest.TestCase):
    """The two families this repository ships, held to the schema every family
    file now has to satisfy."""

    def test_both_shipped_families_declare_a_total_tier_map(self):
        for name in ("claude.toml", "gemma-4.toml"):
            with self.subTest(family=name):
                family = gen_defs.load_family(gen_defs.FAMILY_DIR / name)
                self.assertEqual(set(family.tiers), set(gen_defs.TIERS))

    def test_the_default_family_is_the_default_triple(self):
        path = gen_defs.FAMILY_DIR / f"{gen_defs.DEFAULT_FAMILY}{gen_defs.FAMILY_SUFFIX}"
        tuning = gen_defs.effective_tuning(path, gen_defs.load_family(path))
        self.assertTrue(tuning.is_default)
        # The two maps coincide inside claude, which is what makes a divergence
        # there a deliberate act worth naming.
        self.assertEqual(tuning.tier_map, tuning.pin_map)

    def test_an_explicit_no_op_override_is_still_the_default_triple(self):
        # `is_default` is a comparison by VALUE, not "was a flag passed": an
        # override that changes nothing must not turn an install into an event.
        path = gen_defs.FAMILY_DIR / f"{gen_defs.DEFAULT_FAMILY}{gen_defs.FAMILY_SUFFIX}"
        tuning = gen_defs.effective_tuning(path, gen_defs.load_family(path), pin_spec="high=opus")
        self.assertTrue(tuning.is_default)

    def test_both_shipped_families_are_stock_at_every_tier(self):
        # Ruling 6's planned rung as it stands today: real members, and no
        # member-scoped overrides authored against any of them yet.
        for name in ("claude.toml", "gemma-4.toml"):
            with self.subTest(family=name):
                path = gen_defs.FAMILY_DIR / name
                tuning = gen_defs.effective_tuning(path, gen_defs.load_family(path))
                self.assertEqual(tuning.stock, gen_defs.TIERS)

    def test_gemma_members_carry_the_it_spellings(self):
        self.assertEqual(
            gen_defs.load_family(gen_defs.FAMILY_DIR / "gemma-4.toml").tiers,
            {
                "highest": "gemma-4-31B-it",
                "high": "gemma-4-31B-it",
                "medium": "gemma-4-26B-A4B-it",
                "low": "gemma-4-12B-it",
                "lowest": "gemma-4-E4B-it",
            },
        )


class TestFloorRung(unittest.TestCase):
    """`just check-floor`, over the real definition set: both maps collapsed
    onto one member with `all=`.

    What the rung proves is the `all=` merge reaching every tier a real
    definition sits at. Nothing is asserted about `lowest`: no pin site carries
    that token, so the assertion would pass vacuously whatever the merge did.
    """

    FLOOR = "haiku"

    @classmethod
    def setUpClass(cls):
        _, _, cls.renders = _shipped_renders(
            "claude",
            tier_spec=f"all={cls.FLOOR}",
            pin_spec=f"all={cls.FLOOR}",
        )
        cls.pinned = {key: text for key, text in cls.renders.items() if gen_defs.frontmatter_pin(text)}
        cls.unpinned = {key: text for key, text in cls.renders.items() if key not in cls.pinned}

    def test_every_pin_site_pins_the_floor_and_tunes_against_it(self):
        for key, text in self.pinned.items():
            with self.subTest(output=key):
                self.assertEqual(gen_defs.frontmatter_pin(text), self.FLOOR)
                claim = gen_defs.tuning_claim(text)
                self.assertEqual(claim.member, self.FLOOR)
                self.assertIn(claim.seat, gen_defs.TIERS)

    def test_the_merge_reached_every_tier_a_definition_sits_at(self):
        seats = {gen_defs.tuning_claim(text).seat for text in self.pinned.values()}
        self.assertEqual(seats, {"highest", "high", "medium", "low"})

    def test_an_output_with_no_pin_site_gains_none(self):
        # A map cannot pin an output that has no pin site: no command carries
        # model frontmatter, and neither does anything under agents/mad/ —
        # the participant contract is forbidden one, and a methodology topic
        # is prose a referee reads, not a seat something dispatches.
        self.assertEqual([key for key in self.pinned if key.startswith("commands/")], [])
        self.assertEqual(
            sorted(key for key in self.unpinned if not key.startswith("commands/")),
            sorted(
                ["agents/mad/participant-contract.md"]
                + [key for key in self.renders if key.startswith("agents/mad/") and "-topics/" in key]
            ),
        )
        for key, text in self.unpinned.items():
            with self.subTest(output=key):
                claim = gen_defs.tuning_claim(text)
                self.assertEqual((claim.seat, claim.member), ("none", "none"))

    def test_the_census_is_thirty_six_pin_sites_and_twelve_without(self):
        # A census, and it moves: adding a template moves one of these numbers,
        # and that is a deliberate edit here. Losing a pin site moves them too.
        self.assertEqual((len(self.pinned), len(self.unpinned)), (36, 12))


class TestStockRung(unittest.TestCase):
    """`just check-stock`, over the real definition set: the gemma-4 family,
    whose five tiers name real members the family declares no overrides for —
    the stock state, which every banner records and the run's notice, having
    nothing member-scoped to name, says nothing about."""

    TIER_MAP = (
        "highest=gemma-4-31B-it,high=gemma-4-31B-it,medium=gemma-4-26B-A4B-it,"
        "low=gemma-4-12B-it,lowest=gemma-4-E4B-it"
    )
    ANCHORS = ("fam.ask-vs-stipulate", "fam.gap-aversion")

    @classmethod
    def setUpClass(cls):
        _, cls.overlays, cls.renders = _shipped_renders("gemma-4")
        cls.members = set(gen_defs.load_family(gen_defs.FAMILY_DIR / "gemma-4.toml").tiers.values())

    def test_every_banner_records_the_family_the_tier_map_and_a_stock_render(self):
        for key, text in self.renders.items():
            with self.subTest(output=key):
                claim = gen_defs.tuning_claim(text)
                self.assertEqual(claim.family, "templates/family/gemma-4.toml")
                self.assertEqual(claim.tier, self.TIER_MAP)
                self.assertEqual(claim.stock, ",".join(gen_defs.TIERS))

    def test_both_family_wide_anchors_fill_at_every_tier_and_at_none(self):
        # The no-tier resolution is what the outputs with no pin site run
        # under, and family scope fills both anchors there exactly as it does
        # for a tiered output — only MODEL-scoped entries require a tier.
        for tier, filled in self.overlays.items():
            with self.subTest(tier=tier):
                self.assertEqual(
                    {anchor: scope for anchor, (_, scope) in filled.items()}, dict.fromkeys(self.ANCHORS, "family")
                )

    def test_the_family_wide_text_reaches_the_rendered_tree(self):
        for anchor in self.ANCHORS:
            lead = self.overlays[None][anchor][0].splitlines()[0]
            with self.subTest(anchor=anchor):
                # An empty lead would make the search below match everything.
                self.assertTrue(lead)
                self.assertTrue([key for key, text in self.renders.items() if lead in text])

    def test_no_rendered_body_carries_a_member_scoped_word(self):
        # Stock is a claim about the MEMBER, so this is its proof: the family
        # declares no [family.*.models.*] table for any of the five, and a member
        # name never reaches rendered text in any case — the pin map is the
        # sole source of pin text. Over the body, since the banner's own tier=
        # field names all five by design.
        for key, text in self.renders.items():
            body = gen_defs.banner_body(text)
            for member in self.members:
                with self.subTest(output=key, member=member):
                    self.assertNotIn(member, body)


class TestResidualMarkerGuard(unittest.TestCase):
    """A rendered output must carry no leftover marker delimiter ('@!', '!@') — the
    guard against a marker mistyped past MARKER's own grammar (wrong case, an
    underscore) shipping verbatim into what becomes an agent's system prompt,
    with no chunk, no overlay, and no error to say so otherwise."""

    CLEAN = "---\nname: @!arg.name!@\n---\nbody\n"
    # Capitalized and underscored: valid-looking, but outside MARKER's
    # IDENTIFIER name grammar, so render() never touches it at all.
    RESIDUAL = "---\nname: @!arg.name!@\n---\nbody mentioning @!Guest_Extraction_Contract!@ verbatim.\n"

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        root = Path(self._tmp.name)
        self.tsrc = root / "templates"
        (self.tsrc / "agents").mkdir(parents=True)
        self.out = root / "out"
        self.out.mkdir()
        self.smap = gen_defs.surface_map(templates_root=self.tsrc, output_root=self.out)
        self.family = root / "fam.toml"
        self.family.write_text(_tiers_toml(), encoding="utf-8")

    def _write(self, body):
        (self.tsrc / "agents" / "probe.md.tmpl").write_text(body, encoding="utf-8")

    def _generate(self):
        return _quiet(gen_defs.generate, _binding({}), self.smap, tuning=_tuning(self.family))

    def _check(self):
        return _quiet(gen_defs.check, _binding({}), self.smap, tuning=_tuning(self.family))

    def test_a_clean_render_is_unaffected(self):
        self._write(self.CLEAN)
        self.assertTrue(self._generate())

    def test_a_malformed_marker_is_refused_naming_the_file_and_line(self):
        self._write(self.RESIDUAL)
        with self.assertRaises(gen_defs.TemplateError) as caught:
            self._generate()
        message = str(caught.exception)
        self.assertIn("templates/agents/probe.md.tmpl", message)
        self.assertIn("line 4", message)
        self.assertIn("Guest_Extraction_Contract", message)
        # Nothing lands: the guard fires before generate writes its target.
        self.assertFalse((self.out / "agents" / "probe.md").exists())

    def test_check_is_refused_too_not_only_generate(self):
        # check re-renders through the identical render_template path, so a
        # template that regresses to a malformed marker after a clean
        # baseline fails check exactly as it fails generate — never silently.
        self._write(self.CLEAN)
        self.assertTrue(self._generate())
        self._write(self.RESIDUAL)
        with self.assertRaises(gen_defs.TemplateError):
            self._check()


class TestArgumentKeyIdentifierClass(unittest.TestCase):
    """The behavior change tightening ARG's key class actually causes. Before,
    ARG's key grammar ([a-z_]+) was looser than MARKER's own name grammar, so
    `@!some-chunk my_key="x"!@` parsed fine and bound "my_key" into the
    chunk's scope — and since a chunk body is never required to reference
    every bound key, an unused off-convention key like this rendered clean
    with no error at all. Now that ARG draws from the same IDENTIFIER class as
    MARKER, the whole marker no longer matches MARKER's grammar (the argument
    segment does not parse), so it ships as literal '@!...!@' text and the
    residual-marker guard (TestResidualMarkerGuard) refuses it instead of
    letting it through unused."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        root = Path(self._tmp.name)
        self.tsrc = root / "templates"
        (self.tsrc / "agents").mkdir(parents=True)
        self.out = root / "out"
        self.out.mkdir()
        self.smap = gen_defs.surface_map(templates_root=self.tsrc, output_root=self.out)
        self.family = root / "fam.toml"
        self.family.write_text(_tiers_toml(), encoding="utf-8")
        (self.tsrc / "agents" / "probe.md.tmpl").write_text(
            '---\nname: @!arg.name!@\n---\nbody @!some-chunk my_key="x"!@ tail\n', encoding="utf-8"
        )

    def test_underscored_argument_key_no_longer_binds_and_is_refused(self):
        # "some-chunk"'s own body never references my_key, so the only way
        # this ever surfaces is through the marker's own grammar, not through
        # an unknown-placeholder error inside the chunk body.
        chunks = {"some-chunk": {"text": "expanded"}}
        with self.assertRaises(gen_defs.TemplateError) as caught:
            _quiet(gen_defs.generate, _binding(chunks), self.smap, tuning=_tuning(self.family))
        message = str(caught.exception)
        self.assertIn("templates/agents/probe.md.tmpl", message)
        self.assertIn("my_key", message)
        # Nothing lands: the guard fires before generate writes its target.
        self.assertFalse((self.out / "agents" / "probe.md").exists())


class TestLiteralPinGuard(unittest.TestCase):
    """A pin site must resolve a tier — the standing guard against a template
    landing with a literal pin, which renders the bytes its token would have
    and costs the definition its model scope in silence."""

    TOKENIZED = "---\nname: @!arg.name!@\nmodel: @!dyn.tier-high!@\n---\nbody\n"
    LITERAL = "---\nname: @!arg.name!@\nmodel: opus\n---\nbody\n"
    NO_PIN_SITE = "---\nname: @!arg.name!@\n---\nbody\n"
    LITERAL_PARAM = (
        "+++\n[outputs.probe]\n" 'model = "opus"\n' "+++\n" "---\nname: @!arg.name!@\nmodel: @!arg.model!@\n---\nbody\n"
    )

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        root = Path(self._tmp.name)
        self.tsrc = root / "templates"
        (self.tsrc / "agents").mkdir(parents=True)
        self.out = root / "out"
        self.out.mkdir()
        self.smap = gen_defs.surface_map(templates_root=self.tsrc, output_root=self.out)
        self.family = root / "fam.toml"
        self.family.write_text(_tiers_toml(), encoding="utf-8")

    def _generate(self, body):
        (self.tsrc / "agents" / "probe.md.tmpl").write_text(body, encoding="utf-8")
        return _quiet(gen_defs.generate, _binding({}), self.smap, tuning=_tuning(self.family))

    def _rendered(self):
        return (self.out / "agents" / "probe.md").read_text(encoding="utf-8")

    def _refused(self, body):
        with self.assertRaises(gen_defs.TemplateError) as caught:
            self._generate(body)
        # Nothing lands: the render raises before generate writes its first
        # target, so a refused template leaves no half-tuned tree behind.
        self.assertFalse((self.out / "agents" / "probe.md").exists())
        return str(caught.exception)

    def test_a_literal_pin_is_refused_naming_the_output_and_the_pin(self):
        message = self._refused(self.LITERAL)
        self.assertIn("templates/agents/probe.md.tmpl", message)
        self.assertIn("output 'probe'", message)
        self.assertIn("'model: opus'", message)
        self.assertIn("@!dyn.tier-high!@", message)

    def test_a_literal_pin_bound_as_an_outputs_parameter_is_refused_too(self):
        # The pin is read out of the RENDER, so the two spellings of a pin site
        # are one thing here exactly as they are to the discovery pass.
        self.assertIn("'model: opus'", self._refused(self.LITERAL_PARAM))

    def test_a_tokenized_pin_site_renders(self):
        self.assertTrue(self._generate(self.TOKENIZED))
        self.assertEqual(gen_defs.tuning_claim(self._rendered()).seat, "high")

    def test_an_output_with_no_pin_site_renders(self):
        # The guard is about a pin site that resolves nothing, never about the
        # absence of one: no `model:` key is a property of the output's type.
        self.assertTrue(self._generate(self.NO_PIN_SITE))
        self.assertEqual(gen_defs.tuning_claim(self._rendered()).seat, "none")

    def test_every_shipped_pin_site_resolves_a_tier(self):
        # The guard's positive half, over the real set: the whole tree renders,
        # which under the rule above is itself the assertion that no shipped
        # definition holds a literal pin.
        _, _, renders = _shipped_renders("claude")
        for key, text in renders.items():
            with self.subTest(output=key):
                seat = gen_defs.tuning_claim(text).seat
                if gen_defs.frontmatter_pin(text) is None:
                    self.assertEqual(seat, "none")
                else:
                    self.assertIn(seat, gen_defs.TIERS)


if __name__ == "__main__":
    unittest.main()
