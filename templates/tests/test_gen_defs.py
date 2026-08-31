"""Tests for gen-defs.py's pure logic — NB anchor collection and resolution,
family-file loading and validation, flavor resolution (bare family names and
model -> family implication), scope resolution (model wins over family),
per-pin resolution (each output's model scope comes from its own frontmatter
model: pin, leaving --model as the export flavor for unpinned outputs),
surface-map construction, tuned banners, the two body-hash banners
(!GENERATED! and !INSTALLED!) and the backup branches they gate, the
render-to-order generate/check round trip, and full-product install (copy set,
per-filetype banner placement, per-target write safety, and end-to-end
installs of this repository — plain, flavored, and re-installed).

The script lives at the repo root under a hyphenated name, so it is loaded
here via importlib rather than imported. Filesystem-shaped cases build
scratch template/output trees with tempfile and drive the refactored
functions (surface_map / all_renders / generate / check) against them — the
real templates/ and deployed surfaces are never touched or written.
"""

import contextlib
import io
import shutil
import subprocess
import tempfile
import unittest
from importlib import util
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
_spec = util.spec_from_file_location("gen_defs", _REPO_ROOT / "gen-defs.py")
gen_defs = util.module_from_spec(_spec)
_spec.loader.exec_module(gen_defs)


def _quiet(func, *args, **kwargs):
    """Run a printing mode function, discarding its report."""
    with contextlib.redirect_stdout(io.StringIO()):
        return func(*args, **kwargs)


class TestAnchorCollection(unittest.TestCase):
    def test_anchors_in_finds_nb_markers(self):
        text = 'a @@nb name="one"@@ b @@chunk@@ c @@nb name="two-x"@@'
        self.assertEqual(gen_defs.anchors_in(text), {"one", "two-x"})

    def test_anchors_in_ignores_other_markers_and_plain_text(self):
        self.assertEqual(gen_defs.anchors_in('@@x@@ @@y variant="z"@@ nb'), set())

    def test_collect_anchors_spans_templates_and_chunk_bodies(self):
        chunks = {
            "a": {"text": 'x @@nb name="from-text"@@'},
            "b": {"variants": {"v": '@@nb name="from-variant"@@'}},
            "c": {"text": "t", "defaults": {"k": '@@nb name="from-default"@@'}},
        }
        with tempfile.TemporaryDirectory() as tmp:
            tmpl = Path(tmp) / "t.md.tmpl"
            tmpl.write_text('---\n---\n@@nb name="from-template"@@\n')
            found = gen_defs.collect_anchors(chunks, [tmpl])
        self.assertEqual(found, {"from-text", "from-variant", "from-default", "from-template"})


class TestFamilyLoading(unittest.TestCase):
    def _load(self, toml_text):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "family.toml"
            path.write_text(toml_text)
            return gen_defs.load_family(path)

    def test_family_and_model_entries_load(self):
        entries = self._load('[nb.gap]\ntext = "fam"\n[nb.gap.models.m1]\ntext = "mod"\n')
        self.assertEqual(entries["gap"]["text"], "fam")
        self.assertEqual(entries["gap"]["models"]["m1"]["text"], "mod")

    def test_model_only_entry_loads(self):
        entries = self._load('[nb.gap.models.m1]\ntext = "mod"\n')
        self.assertNotIn("text", entries["gap"])

    def test_text_edge_newlines_stripped(self):
        entries = self._load('[nb.gap]\ntext = """\nfam\n"""\n')
        self.assertEqual(entries["gap"]["text"], "fam")

    def test_missing_file_is_error(self):
        with self.assertRaisesRegex(gen_defs.TemplateError, "does not exist"):
            gen_defs.load_family(Path("no/such/family.toml"))

    def test_invalid_toml_is_error(self):
        with self.assertRaisesRegex(gen_defs.TemplateError, "invalid TOML"):
            self._load("[nb.gap\n")

    def test_zero_entry_file_loads_cleanly(self):
        # Comments-only reserved family files (e.g. claude-addenda.toml)
        # load cleanly and fill nothing.
        for toml_text in ("", "# comments only\n", "[nb]\n"):
            self.assertEqual(self._load(toml_text), {})

    def test_stray_top_level_table_is_error(self):
        with self.assertRaisesRegex(gen_defs.TemplateError, "unknown top-level"):
            self._load('[nb.gap]\ntext = "t"\n[other]\nx = "y"\n')

    def test_bad_anchor_name_is_error(self):
        with self.assertRaisesRegex(gen_defs.TemplateError, "a-z0-9"):
            self._load('[nb.BadName]\ntext = "t"\n')

    def test_unknown_entry_key_is_error(self):
        with self.assertRaisesRegex(gen_defs.TemplateError, "only 'text'"):
            self._load('[nb.gap]\ntext = "t"\nextra = "x"\n')

    def test_malformed_model_override_is_error(self):
        with self.assertRaisesRegex(gen_defs.TemplateError, "exactly one key"):
            self._load('[nb.gap.models.m1]\ntext = "t"\nextra = "x"\n')

    def test_entry_filling_nothing_is_error(self):
        with self.assertRaisesRegex(gen_defs.TemplateError, "fills nothing"):
            self._load("[nb.gap]\n")


class TestFamilyValidation(unittest.TestCase):
    def test_known_anchors_pass(self):
        gen_defs.validate_family_anchors({"gap": {}}, {"gap", "other"})

    def test_unknown_anchor_is_hard_error(self):
        with self.assertRaisesRegex(gen_defs.TemplateError, "ghost"):
            gen_defs.validate_family_anchors({"ghost": {}, "gap": {}}, {"gap"})


class TestFamilyNameResolution(unittest.TestCase):
    """resolve_family: path-shaped specs pass through; bare names resolve
    against the models dir, <name>-addenda.toml / <name>.toml."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.models = Path(self._tmp.name)

    def _family(self, filename, toml_text='[nb.gap.models.m1]\ntext = "t"\n'):
        path = self.models / filename
        path.write_text(toml_text)
        return path

    def test_path_with_separator_passes_through(self):
        # Path-shaped specs are never name-resolved, even when nonexistent —
        # load_family owns the existence error, exactly as before.
        spec = "no/such/family"
        self.assertEqual(gen_defs.resolve_family(spec, self.models), Path(spec))

    def test_toml_suffix_passes_through(self):
        self.assertEqual(gen_defs.resolve_family("fam.toml", self.models), Path("fam.toml"))

    def test_bare_name_matches_addenda_file(self):
        addenda = self._family("gem-addenda.toml")
        self.assertEqual(gen_defs.resolve_family("gem", self.models), addenda)

    def test_bare_name_matches_plain_file(self):
        plain = self._family("gem.toml")
        self.assertEqual(gen_defs.resolve_family("gem", self.models), plain)

    def test_both_candidates_is_ambiguity_error_naming_them(self):
        self._family("gem-addenda.toml")
        self._family("gem.toml")
        with self.assertRaisesRegex(gen_defs.TemplateError, r"ambiguous.*gem-addenda\.toml.*gem\.toml"):
            gen_defs.resolve_family("gem", self.models)

    def test_no_match_error_lists_available_family_files(self):
        self._family("other.toml")
        with self.assertRaisesRegex(gen_defs.TemplateError, r"matches no family file.*other\.toml"):
            gen_defs.resolve_family("gem", self.models)


class TestModelImpliesFamily(unittest.TestCase):
    """imply_family: a bare --model implies the unique family whose
    [nb.*.models.*] tables mention it."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.models = Path(self._tmp.name)

    def _family(self, filename, toml_text):
        (self.models / filename).write_text(toml_text)

    def test_unique_family_is_implied(self):
        self._family("one.toml", '[nb.gap.models.m1]\ntext = "t"\n')
        self._family("two.toml", '[nb.gap.models.m2]\ntext = "t"\n')
        self.assertEqual(gen_defs.imply_family("m1", self.models), self.models / "one.toml")

    def test_family_models_ignores_comment_only_mentions(self):
        # A model sketched in comments (claude-addenda.toml's pattern) is
        # not known to its family.
        self._family("sketch.toml", '# [nb.gap.models.ghost]\n# text = "t"\n')
        known = gen_defs.family_models(self.models)
        self.assertEqual(known[self.models / "sketch.toml"], set())

    def test_unknown_model_error_lists_models_and_the_fix(self):
        self._family("one.toml", '[nb.gap.models.m1]\ntext = "t"\n')
        self._family("sketch.toml", "# comments only\n")
        with self.assertRaisesRegex(
            gen_defs.TemplateError,
            r"no family file.*mentions that model.*one\.toml: m1"
            r".*sketch\.toml: \(none\)"
            r".*comments is not known.*--model-family <family> --model ghost",
        ):
            gen_defs.imply_family("ghost", self.models)

    def test_model_in_several_families_demands_explicit_family(self):
        self._family("one.toml", '[nb.gap.models.m1]\ntext = "t"\n')
        self._family("two.toml", '[nb.gap.models.m1]\ntext = "t"\n')
        with self.assertRaisesRegex(
            gen_defs.TemplateError,
            r"more than one family file.*one\.toml.*two\.toml.*--model-family",
        ):
            gen_defs.imply_family("m1", self.models)


class TestNBResolution(unittest.TestCase):
    ENTRIES = {
        "both": {"text": "fam", "models": {"m1": {"text": "mod"}}},
        "family-only": {"text": "fam-only"},
        "model-only": {"models": {"m1": {"text": "mod-only"}}},
    }

    def test_no_model_resolves_family_scope_only(self):
        resolved = gen_defs.resolve_nb(self.ENTRIES, None)
        self.assertEqual(
            resolved,
            {"both": ("fam", "family"), "family-only": ("fam-only", "family")},
        )

    def test_model_scope_wins_over_family(self):
        resolved = gen_defs.resolve_nb(self.ENTRIES, "m1")
        self.assertEqual(resolved["both"], ("mod", "model"))
        self.assertEqual(resolved["model-only"], ("mod-only", "model"))
        # A model with no override still gets the family text.
        self.assertEqual(resolved["family-only"], ("fam-only", "family"))

    def test_unmatched_model_falls_back_to_family_or_nothing(self):
        resolved = gen_defs.resolve_nb(self.ENTRIES, "other-model")
        self.assertEqual(resolved["both"], ("fam", "family"))
        self.assertNotIn("model-only", resolved)


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
        # outputs-table parameters (@@model@@), while the participant contract
        # and the generated commands declare no pin at all.
        pins = {
            gen_defs.rel(target): gen_defs.frontmatter_pin(text)
            for target, text in gen_defs.all_renders(gen_defs.load_chunks(), gen_defs.surface_map())
        }
        self.assertEqual(pins["agents/mad-participant-haiku.md"], "haiku")
        self.assertEqual(pins["agents/mad-participant-opus.md"], "opus")
        self.assertIsNone(pins["agents/mad/participant-contract.md"])
        self.assertIsNone(pins["commands/mad-review.md"])


class TestPerPinResolver(unittest.TestCase):
    """per_pin_resolver: model scope belongs to the output's own pin, family
    scope stays render-wide, and CLI --model reaches unpinned outputs only."""

    FAMILY = (
        '[nb.gap]\ntext = "family-wide"\n'
        '[nb.gap.models.opus]\ntext = "for opus"\n'
        '[nb.other]\ntext = "other family-wide"\n'
    )

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.models = Path(self._tmp.name)
        self.family = self.models / "fam-addenda.toml"
        self.family.write_text(self.FAMILY, encoding="utf-8")
        self.entries = gen_defs.load_family(self.family)

    def _resolver(self, entries, *, cli_model=None, family_given=False):
        return gen_defs.per_pin_resolver(
            entries,
            cli_model=cli_model,
            family_given=family_given,
            models_dir=self.models,
        )

    def test_pin_model_scope_wins_over_family_scope_at_the_same_anchor(self):
        resolve = self._resolver(self.entries, family_given=True)
        self.assertEqual(resolve("opus")["gap"], ("for opus", "model"))
        # Anchors the pin does not override still take the family text.
        self.assertEqual(resolve("opus")["other"], ("other family-wide", "family"))

    def test_pin_matching_no_entry_resolves_silently_to_family_scope(self):
        resolve = self._resolver(self.entries, family_given=True)
        self.assertEqual(resolve("haiku")["gap"], ("family-wide", "family"))

    def test_unpinned_output_follows_the_cli_model(self):
        resolve = self._resolver(self.entries, cli_model="opus", family_given=True)
        self.assertEqual(resolve(None)["gap"], ("for opus", "model"))

    def test_cli_model_never_reaches_a_pinned_output(self):
        # --model is the export flavor: an output pinned elsewhere resolves its
        # own scope (here: none), not the flavor's.
        resolve = self._resolver(self.entries, cli_model="opus", family_given=True)
        self.assertEqual(resolve("sonnet")["gap"], ("family-wide", "family"))

    def test_named_family_confines_pin_resolution_to_it(self):
        (self.models / "second.toml").write_text('[nb.gap.models.haiku]\ntext = "elsewhere"\n', encoding="utf-8")
        resolve = self._resolver(self.entries, family_given=True)
        self.assertEqual(resolve("haiku")["gap"], ("family-wide", "family"))

    def test_pin_implies_its_own_family_when_none_was_named(self):
        resolve = self._resolver(None)
        self.assertEqual(resolve("opus"), {"gap": ("for opus", "model")})
        # Only the pin's own override travels: an implied family's family-wide
        # text belongs to the family an invocation actually loads.
        self.assertNotIn("other", resolve("opus"))

    def test_pin_matching_no_family_at_all_is_silent(self):
        self.assertEqual(self._resolver(None)("sonnet"), {})

    def test_ambiguous_pin_is_silent_where_the_cli_ask_is_an_error(self):
        # The asymmetry: a pin is routine metadata, so ambiguity costs it its
        # model scope and nothing else; --model is a request, and a request
        # that resolves to nothing still fails loudly.
        (self.models / "second.toml").write_text('[nb.gap.models.opus]\ntext = "elsewhere"\n', encoding="utf-8")
        self.assertEqual(self._resolver(None)("opus"), {})
        with self.assertRaisesRegex(gen_defs.TemplateError, "more than one family file"):
            gen_defs.imply_family("opus", self.models)

    def test_no_family_files_leaves_every_output_bare(self):
        self.family.unlink()
        resolve = self._resolver(None)
        self.assertEqual(resolve(None), {})
        self.assertEqual(resolve("opus"), {})

    def test_plain_map_stays_render_wide(self):
        # as_resolver is what keeps a caller that has already resolved one map
        # working: it reaches every output, pinned or not.
        resolve = gen_defs.as_resolver({"gap": ("wide", "family")})
        self.assertEqual(resolve(None), resolve("opus"))
        self.assertIsNone(gen_defs.as_resolver(None)("opus"))


class TestRenderNB(unittest.TestCase):
    def test_unfilled_anchor_expands_to_nothing(self):
        for nb in (None, {}):
            self.assertEqual(gen_defs.render('a @@nb name="gap"@@b', {}, {}, nb), "a b")

    def test_filled_anchor_renders_single_nb(self):
        out = gen_defs.render('@@nb name="gap"@@', {}, {}, {"gap": ("watch it", "family")})
        self.assertEqual(out, "**NB**: watch it")

    def test_base_text_never_modified_around_anchor(self):
        # The never-touches-base invariant at render level: filling an anchor
        # adds the NB and changes nothing else.
        template = 'base line\n@@nb name="gap"@@\nmore base'
        bare = gen_defs.render(template, {}, {}, None)
        filled = gen_defs.render(template, {}, {}, {"gap": ("t", "model")})
        self.assertEqual(bare, "base line\n\nmore base")
        self.assertEqual(filled, "base line\n**NB**: t\nmore base")

    def test_nb_text_is_marker_expanded(self):
        chunks = {"c": {"text": "chunked"}}
        out = gen_defs.render('@@nb name="gap"@@', chunks, {}, {"gap": ("see @@c@@", "family")})
        self.assertEqual(out, "**NB**: see chunked")

    def test_typo_in_nb_text_fails_loudly(self):
        with self.assertRaisesRegex(gen_defs.TemplateError, "unknown chunk"):
            gen_defs.render('@@nb name="gap"@@', {}, {}, {"gap": ("see @@nope@@", "family")})

    def test_nb_marker_requires_exactly_name(self):
        for marker in ("@@nb@@", '@@nb name="x" wrap="70"@@', '@@nb variant="v"@@'):
            with self.assertRaisesRegex(gen_defs.TemplateError, "exactly one"):
                gen_defs.render(marker, {}, {}, {})

    def test_render_output_resolves_against_the_rendered_pin(self):
        # render_output reads the pin out of its own first pass, so a pin
        # bound as an outputs-table parameter resolves exactly as a literal
        # one does.
        body = '---\nname: @@name@@\nmodel: @@model@@\n---\n@@nb name="gap"@@\n'
        out = gen_defs.render_output(body, {}, {"name": "x", "model": "opus"}, lambda pin: {"gap": (str(pin), "model")})
        self.assertIn("**NB**: opus", out)

    def test_render_output_of_an_unpinned_body_takes_the_unpinned_map(self):
        body = '---\nname: @@name@@\n---\n@@nb name="gap"@@\n'
        out = gen_defs.render_output(body, {}, {"name": "x"}, lambda pin: {"gap": (str(pin), "model")})
        self.assertIn("**NB**: None", out)

    def test_anchor_inside_chunk_body_resolves(self):
        chunks = {"c": {"text": 'chunk @@nb name="gap"@@tail'}}
        self.assertEqual(gen_defs.render("@@c@@", chunks, {}, None), "chunk tail")
        self.assertEqual(
            gen_defs.render("@@c@@", chunks, {}, {"gap": ("nb", "family")}),
            "chunk **NB**: nbtail",
        )


class TestSurfaceMap(unittest.TestCase):
    def test_default_maps_both_deployed_surfaces(self):
        smap = gen_defs.surface_map()
        self.assertEqual(set(smap), {"agents", "commands"})
        self.assertEqual(smap["agents"][1], gen_defs.REPO_ROOT / "agents")

    def test_surfaces_filters_to_one(self):
        self.assertEqual(set(gen_defs.surface_map(surfaces="agents")), {"agents"})
        self.assertEqual(set(gen_defs.surface_map(surfaces="commands")), {"commands"})

    def test_unknown_surface_is_error(self):
        with self.assertRaises(gen_defs.TemplateError):
            gen_defs.surface_map(surfaces="nope")

    def test_explicit_output_root_must_exist(self):
        with self.assertRaisesRegex(gen_defs.TemplateError, "existing directory"):
            gen_defs.surface_map(output_root=Path("no/such/root"))

    def test_explicit_roots_route_both_trees(self):
        with tempfile.TemporaryDirectory() as tmp:
            smap = gen_defs.surface_map(templates_root=Path(tmp) / "tsrc", output_root=Path(tmp))
            self.assertEqual(smap["agents"][0], Path(tmp) / "tsrc" / "agents")
            self.assertEqual(smap["commands"][1], Path(tmp) / "commands")


class TestTunedBanner(unittest.TestCase):
    TMPL = gen_defs.TEMPLATES_DIR / "agents" / "x.md.tmpl"

    def test_untuned_banner_unchanged(self):
        stamp = gen_defs.banner(self.TMPL, body_hash="0" * 64)
        self.assertIn(
            "# !GENERATED! from templates/agents/x.md.tmpl and " "templates/shared-sections.toml — edit those.",
            stamp,
        )
        self.assertNotIn("model family", stamp)

    def _claim(self, tuning):
        body = "name: x\n---\nbody\n"
        stamp = gen_defs.banner(self.TMPL, body_hash=gen_defs.sha256_text(body), tuning=tuning)
        text = "---\n" + stamp + "\n" + body
        return gen_defs.banner_claim(text), gen_defs.tuning_claim(text)

    def test_untuned_claims_round_trip(self):
        template_claim, tuned_claim = self._claim(None)
        self.assertEqual(template_claim, "templates/agents/x.md.tmpl")
        self.assertIsNone(tuned_claim)

    def test_family_claim_round_trips(self):
        family = gen_defs.TEMPLATES_DIR / "models" / "fam.toml"
        template_claim, tuned_claim = self._claim((family, None))
        self.assertEqual(template_claim, "templates/agents/x.md.tmpl")
        self.assertEqual(tuned_claim, ("templates/models/fam.toml", None))

    def test_family_and_model_claim_round_trips(self):
        family = gen_defs.TEMPLATES_DIR / "models" / "fam.toml"
        _, tuned_claim = self._claim((family, "m-1"))
        self.assertEqual(tuned_claim, ("templates/models/fam.toml", "m-1"))

    def test_describe_tuning(self):
        self.assertEqual(gen_defs.describe_tuning(None), "no model family")
        self.assertEqual(gen_defs.describe_tuning(("f", None)), "model family f")
        self.assertEqual(gen_defs.describe_tuning(("f", "m")), "model family f, model m")


class TestGenerateCheckRoundTrip(unittest.TestCase):
    """End-to-end over a scratch template tree: base render fills nothing,
    tuned render fills the anchor, and check enforces the tuning claim."""

    CHUNKS = {"shared": {"text": "shared text"}}
    BODY = "---\nname: @@name@@\n---\n" 'body @@shared@@\n@@nb name="probe"@@\ntail\n'
    ENTRIES = {"probe": {"text": "family fill", "models": {"m1": {"text": "model fill"}}}}

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        root = Path(self._tmp.name)
        self.tsrc = root / "templates"
        (self.tsrc / "agents").mkdir(parents=True)
        (self.tsrc / "agents" / "probe.md.tmpl").write_text(self.BODY)
        self.out = root / "out"
        self.out.mkdir()
        self.smap = gen_defs.surface_map(templates_root=self.tsrc, output_root=self.out)
        self.family = root / "family.toml"
        self.family.write_text('[nb.probe]\ntext = "family fill"\n' '[nb.probe.models.m1]\ntext = "model fill"\n')

    def _target(self):
        return self.out / "agents" / "probe.md"

    def test_scratch_template_anchor_is_collectable(self):
        found = gen_defs.collect_anchors(self.CHUNKS, gen_defs.templates(self.smap))
        self.assertEqual(found, {"probe"})

    def test_base_generate_fills_nothing(self):
        ok = _quiet(gen_defs.generate, self.CHUNKS, self.smap, explicit_root=True)
        self.assertTrue(ok)
        text = self._target().read_text()
        self.assertIn("body shared text\n\ntail", text)
        self.assertNotIn("NB", text)
        self.assertNotIn("model family", text)

    def test_tuned_generate_fills_one_nb_and_claims_tuning(self):
        nb = gen_defs.resolve_nb(self.ENTRIES, "m1")
        ok = _quiet(
            gen_defs.generate,
            self.CHUNKS,
            self.smap,
            nb=nb,
            tuning=(self.family, "m1"),
            explicit_root=True,
        )
        self.assertTrue(ok)
        text = self._target().read_text()
        self.assertIn("**NB**: model fill", text)
        self.assertNotIn("family fill", text)  # one NB per anchor
        self.assertEqual(gen_defs.tuning_claim(text), (gen_defs.rel(self.family), "m1"))

    def test_check_passes_only_under_matching_tuning(self):
        nb = gen_defs.resolve_nb(self.ENTRIES, None)
        _quiet(
            gen_defs.generate,
            self.CHUNKS,
            self.smap,
            nb=nb,
            tuning=(self.family, None),
            explicit_root=True,
        )
        self.assertTrue(
            _quiet(
                gen_defs.check,
                self.CHUNKS,
                self.smap,
                nb=nb,
                tuning=(self.family, None),
            )
        )
        # Same directory checked untuned: MISTUNED, nonzero.
        self.assertFalse(_quiet(gen_defs.check, self.CHUNKS, self.smap))

    def test_mistuned_is_named_not_dumped(self):
        _quiet(gen_defs.generate, self.CHUNKS, self.smap, explicit_root=True)
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            ok = gen_defs.check(
                self.CHUNKS,
                self.smap,
                nb=gen_defs.resolve_nb(self.ENTRIES, None),
                tuning=(self.family, None),
            )
        self.assertFalse(ok)
        self.assertIn("MISTUNED", buf.getvalue())
        self.assertIn("no model family", buf.getvalue())

    def test_bannerless_target_refused_out_of_repo_too(self):
        target = self._target()
        target.parent.mkdir(parents=True)
        target.write_text("hand-maintained\n")
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            ok = gen_defs.generate(self.CHUNKS, self.smap, explicit_root=True)
        self.assertFalse(ok)
        self.assertIn("REFUSED", buf.getvalue())
        self.assertEqual(target.read_text(), "hand-maintained\n")

    def test_hand_edited_target_backs_up_out_of_repo_too(self):
        # The whole write-safety table applies identically out of repo: an
        # untouched render is replaced outright, a hand-edited one is copied
        # aside first.
        _quiet(gen_defs.generate, self.CHUNKS, self.smap, explicit_root=True)
        nb = gen_defs.resolve_nb(self.ENTRIES, None)
        retune = dict(nb=nb, tuning=(self.family, None), explicit_root=True)
        _quiet(gen_defs.generate, self.CHUNKS, self.smap, **retune)
        self.assertFalse(self._target().with_name("probe.md.00.bak").exists())

        target = self._target()
        target.write_text(target.read_text(encoding="utf-8") + "hand-added\n", encoding="utf-8")
        _quiet(gen_defs.generate, self.CHUNKS, self.smap, explicit_root=True)
        self.assertTrue(target.with_name("probe.md.00.bak").exists())


class TestMixedPinRender(unittest.TestCase):
    """A mixed render over a scratch tree — one pinned agent, one unpinned
    command, one family file — resolving each output against its own pin."""

    CHUNKS = {}
    PINNED = '---\nname: @@name@@\nmodel: opus\n---\n@@nb name="probe"@@\n'
    # A frontmatter-less command: the surface that carries no pin today.
    UNPINNED = '@@nb name="probe"@@\n'
    FAMILY = (
        '[nb.probe]\ntext = "family"\n'
        '[nb.probe.models.opus]\ntext = "opus text"\n'
        '[nb.probe.models.haiku]\ntext = "haiku text"\n'
    )

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        root = Path(self._tmp.name)
        self.tsrc = root / "templates"
        (self.tsrc / "agents").mkdir(parents=True)
        (self.tsrc / "commands").mkdir(parents=True)
        (self.tsrc / "agents" / "pinned.md.tmpl").write_text(self.PINNED, encoding="utf-8")
        (self.tsrc / "commands" / "plain.md.tmpl").write_text(self.UNPINNED, encoding="utf-8")
        self.out = root / "out"
        self.out.mkdir()
        self.smap = gen_defs.surface_map(templates_root=self.tsrc, output_root=self.out)
        self.models = root / "models"
        self.models.mkdir()
        self.family = self.models / "fam-addenda.toml"
        self.family.write_text(self.FAMILY, encoding="utf-8")
        self.entries = gen_defs.load_family(self.family)

    def _resolver(self, entries, *, cli_model=None, family_given=False):
        return gen_defs.per_pin_resolver(
            entries,
            cli_model=cli_model,
            family_given=family_given,
            models_dir=self.models,
        )

    def _generate(self, resolve, tuning=None):
        return _quiet(
            gen_defs.generate,
            self.CHUNKS,
            self.smap,
            nb=resolve,
            tuning=tuning,
            explicit_root=True,
        )

    def _bodies(self):
        return (
            gen_defs.banner_body((self.out / "agents" / "pinned.md").read_text(encoding="utf-8")),
            gen_defs.banner_body((self.out / "commands" / "plain.md").read_text(encoding="utf-8")),
        )

    def test_each_output_resolves_against_its_own_pin(self):
        resolve = self._resolver(self.entries, cli_model="haiku", family_given=True)
        self.assertTrue(self._generate(resolve, tuning=(self.family, "haiku")))
        pinned, plain = self._bodies()
        self.assertIn("**NB**: opus text", pinned)
        self.assertNotIn("haiku text", pinned)
        # The flavor lands where nothing was pinned, and only there.
        self.assertIn("**NB**: haiku text", plain)

    def test_mixed_render_round_trips_through_check(self):
        resolve = self._resolver(self.entries, cli_model="haiku", family_given=True)
        self._generate(resolve, tuning=(self.family, "haiku"))
        self.assertTrue(_quiet(gen_defs.check, self.CHUNKS, self.smap, nb=resolve, tuning=(self.family, "haiku")))

    def test_untuned_render_still_resolves_the_pin_from_its_family(self):
        # Universal: a pin owns its tuning whether or not the invocation asked
        # for a flavor. No family is loaded, so no family-wide text reaches
        # anything — only the pin's own override.
        self.assertTrue(self._generate(self._resolver(None)))
        pinned, plain = self._bodies()
        self.assertIn("**NB**: opus text", pinned)
        self.assertNotIn("NB", plain)

    def test_render_is_bare_when_no_family_file_mentions_the_pin(self):
        self.family.unlink()
        self.assertTrue(self._generate(self._resolver(None)))
        for body in self._bodies():
            self.assertNotIn("NB", body)


class TestModelFlavorAccounting(unittest.TestCase):
    """--model's reach is reported out loud: a flavored render that reached
    nothing must not read like one that worked."""

    PINNED = "---\nname: a\nmodel: opus\n---\nbody\n"
    UNPINNED = "---\nname: b\n---\nbody\n"

    def _report(self, texts):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            gen_defs.report_model_flavor("haiku", [(Path("agents/x.md"), text) for text in texts])
        return buf.getvalue()

    def test_both_counts_are_named(self):
        out = self._report([self.PINNED] * 3 + [self.UNPINNED] * 2)
        self.assertIn(
            "--model haiku: applied to 2 unpinned output(s); skipped 3 pinned output(s) (pins own their tuning)",
            out,
        )

    def test_reaching_nothing_says_exactly_that(self):
        out = self._report([self.PINNED] * 4)
        self.assertIn(
            "--model haiku: applied to 0 unpinned output(s) — all 4 output(s) carry a frontmatter pin",
            out,
        )


class TestNestedTemplateMirroring(unittest.TestCase):
    """Recursive discovery and path mirroring: a template's relative subpath
    within its surface tree is its output's relative subpath within the
    surface, and every other mechanism applies unchanged at that nested path.
    """

    CHUNKS = {"shared": {"text": "shared text"}}
    BODY = "---\nname: @@name@@\n---\nbody @@shared@@\n"

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

    def _generate(self):
        return _quiet(gen_defs.generate, self.CHUNKS, self.smap, explicit_root=True)

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
        self.assertTrue(_quiet(gen_defs.check, self.CHUNKS, self.smap))

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

    def _stamped(self, body):
        return "---\n" + gen_defs.banner(self.TMPL, body_hash=gen_defs.sha256_text(body)) + "\n" + body

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
            "templates/shared-sections.toml — edit those. DO NOT HAND EDIT "
            "THIS FILE.\n#\nname: x\n---\nb\n"
        )
        self.assertIsNotNone(gen_defs.banner_claim(old))  # still a banner
        self.assertIsNone(gen_defs.banner_body(old))
        self.assertFalse(gen_defs.body_untouched(old))
        # Nothing to strip: check falls back to the whole file.
        self.assertEqual(gen_defs.comparable_body(old), old)

    def test_rendered_definitions_carry_a_true_hash(self):
        smap = gen_defs.surface_map()
        for target, rendered in gen_defs.all_renders(gen_defs.load_chunks(), smap):
            self.assertTrue(gen_defs.body_untouched(rendered), gen_defs.rel(target))


class TestWriteSafetyBackupBranches(unittest.TestCase):
    """Regenerating over provably-untouched output writes no backup;
    regenerating over hand-edited bannered content still does."""

    CHUNKS = {"shared": {"text": "shared text"}}
    BODY = "---\nname: @@name@@\n---\nbody @@shared@@\n"

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
            self.CHUNKS if chunks is None else chunks,
            self.smap,
            explicit_root=True,
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
    """An install is a plain recursive copy of both surfaces minus the
    exclusion list — no complement computation, generated definitions
    included. Built over a scratch source tree."""

    SOURCE_FILES = (
        "agents/gen.md",  # a generated definition — copied like any other file
        "agents/hand.md",
        "agents/mad/review-topics/topic.md",
        "agents/kb_tools/kb_util.py",
        "agents/kb_tools/runner-snippets/kb.just",
        "agents/kb_tools/tests/test_kb_util.py",  # excluded: tests/
        "agents/kb_tools/tests/fixtures/mini-kb/index.md",  # excluded: tests/
        "agents/kb_tools/__pycache__/kb_util.cpython-311.pyc",  # excluded
        "agents/.pytest_cache/CACHEDIR.TAG",  # excluded
        "agents/hand.md.00.bak",  # excluded: generator safety copy
        "agents/.DS_Store",  # excluded
        "commands/guest.md",
    )
    EXPECTED = {
        "agents/gen.md",
        "agents/hand.md",
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
        self.assertEqual(
            targets["agents/mad/review-topics/topic.md"],
            self.out / "agents" / "mad" / "review-topics" / "topic.md",
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
        ):
            self.assertTrue(gen_defs.excluded_from_install(Path(excluded)), excluded)
        for kept in ("hand.md", "mad/review-topics/t.md", "kb_tools/kb_util.py"):
            self.assertFalse(gen_defs.excluded_from_install(Path(kept)), kept)


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
        generated = "---\n" + gen_defs.banner(template, body_hash=gen_defs.sha256_text(body)) + "\n" + body
        self.assertIsNone(gen_defs.install_content(self._source("gen.md", generated), surface="agents"))

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


class TestInstallEndToEnd(unittest.TestCase):
    """Real installs of this repository into scratch targets: plain, flavored,
    and re-installed."""

    @classmethod
    def setUpClass(cls):
        cls.chunks = gen_defs.load_chunks()

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name) / ".claude"
        self.root.mkdir()
        self.smap = gen_defs.surface_map(output_root=self.root)

    def _install(self, **kwargs):
        return _quiet(gen_defs.install, self.chunks, self.smap, root=self.root, **kwargs)

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
            "agents/kb_tools/METADATA_SCHEMA.md",
            "agents/kb_tools/AGENTS.md",
            "agents/kb_tools/runner-snippets/kb.just",
            "agents/liaison_tools/post-openai.sh",
            "commands/guest.md",
        ):
            self.assertIn(expected, installed)
        self.assertTrue(any(name.startswith("agents/mad/review-topics/") for name in installed))

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

    def test_plain_install_renders_nothing_and_reports_integrity(self):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            gen_defs.install(self.chunks, self.smap, root=self.root)
        self.assertIn("rendered 0, integrity OK", buf.getvalue())
        self.assertEqual(sorted(self.root.rglob("*.bak")), [])

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
        surfaces = [gen_defs.REPO_ROOT / name for name in gen_defs.SURFACE_NAMES]
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
        for name, bannered in (
            ("agents/guest-liaison.md", True),  # hand-maintained: !INSTALLED!
            ("agents/kb_tools/kb_util.py", True),
            ("agents/mad/review-topics/general-code.md", True),
            ("agents/python-coder.md", False),  # generated: !GENERATED! already
        ):
            text = (self.root / name).read_text(encoding="utf-8")
            self.assertEqual(gen_defs.INSTALLED_NOTICE in text, bannered, name)
            self.assertTrue(gen_defs.body_untouched(text), name)

    def test_bare_command_keeps_its_first_body_line(self):
        self._install()
        name = "commands/guest-end.md"
        source = (gen_defs.REPO_ROOT / name).read_text(encoding="utf-8")
        installed = (self.root / name).read_text(encoding="utf-8")
        self.assertIn(gen_defs.INSTALLED_NOTICE, installed)
        # The banner lands in a frontmatter block of its own, above content
        # that is otherwise byte-identical — the first body line, which Claude
        # Code lists a frontmatter-less command by, is still the source's.
        self.assertEqual(gen_defs.banner_body(installed), "---\n" + source)

    @unittest.skipUnless(shutil.which("bash"), "extraction tool needs bash")
    def test_installed_supporting_material_stays_unextractable(self):
        # SPEC's Guest-Extraction Contract: extraction over supporting
        # material must fail rather than yield a silently truncated prompt.
        # The banner must not change that verdict, which is why a topic gets
        # an HTML comment and not a frontmatter block.
        self._install()
        for name in (
            "agents/mad/review-topics/general-code.md",
            "agents/kb_tools/AGENTS.md",
        ):
            done = self._extract(self.root / name)
            self.assertEqual(done.returncode, 2, name)

    def _extract(self, path):
        extractor = gen_defs.REPO_ROOT / "agents" / "liaison_tools" / "extract-agent-body.sh"
        return subprocess.run(
            ["bash", str(extractor), str(path)],
            capture_output=True,
            text=True,
            check=False,
        )

    @unittest.skipUnless(shutil.which("bash"), "extraction tool needs bash")
    def test_installed_definition_still_extracts_frontmatter_free(self):
        # The frontmatter-style banner has to survive contact with the real
        # consumer of an installed definition's body: the guest-relay
        # extractor, which must drop the whole block and lose no body content.
        self._install()
        name = "agents/guest-liaison.md"
        done = self._extract(self.root / name)
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertNotIn(gen_defs.INSTALLED_NOTICE, done.stdout)
        self.assertNotIn("!BODY-SHA256!", done.stdout)
        self.assertEqual(done.stdout, self._extract(gen_defs.REPO_ROOT / name).stdout)

    def test_flavored_install_renders_the_flavor_without_backups(self):
        family = gen_defs.MODELS_DIR / "gemma-4-addenda.toml"
        entries = gen_defs.load_family(family)
        plain_body = gen_defs.banner_body((Path(gen_defs.REPO_ROOT) / "agents" / "go-coder.md").read_text("utf-8"))
        self.assertTrue(self._install(nb=gen_defs.resolve_nb(entries, None), tuning=(family, None)))
        # No backups anywhere: every rendered target was a fresh copy, and so
        # provably this tool's own output.
        self.assertEqual(sorted(self.root.rglob("*.bak")), [])
        # The flavor's anchors land in the one definition that authors them.
        tuned = (self.root / "agents" / "applied-mathematician.md").read_text("utf-8")
        self.assertIn("**NB**:", tuned)
        self.assertEqual(
            gen_defs.tuning_claim(tuned),
            ("templates/models/gemma-4-addenda.toml", None),
        )
        # A definition the flavor does not touch keeps a byte-identical body;
        # only its banner records the tuning it was rendered under.
        untouched = (self.root / "agents" / "go-coder.md").read_text("utf-8")
        self.assertEqual(gen_defs.banner_body(untouched), plain_body)
        self.assertIsNotNone(gen_defs.tuning_claim(untouched))


if __name__ == "__main__":
    unittest.main()
