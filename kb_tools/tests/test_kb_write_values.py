"""The values module's contract, as tests.

The contract has four clauses, and each has a class below.

**"Every malformed-values fixture refused with the offending field and line
named."** :class:`TestGrammar`, :class:`TestClosedVocabulary`,
:class:`TestHostileShapes` and :class:`TestEncoding` carry the malformed-values
classes, **each exactly once and each asserting its own identity in the
refusal** — a check that fires for the wrong reason is a failed check, so every
fixture asserts the refused ``field`` as well as the fact of refusal.

**"A rationale value containing a blank line is refused here, naming the
line — never collapsed, never truncated, and never reaching the
renderer."** :class:`TestBlankLineRefusal`, which is this module's load-bearing
class. It asserts the named line under both ``'''`` layouts, asserts that no
entry survives the refusal (nothing reaches the renderer), and asserts the two
shapes that are *not* this class: a single newline, which the renderer's
collapse defeats, and the trailing newline every idiomatic literal block
carries.

**"No value is ever coerced or defaulted."** :class:`TestNoCoercion`. A missing
optional key is absent rather than defaulted; a near-miss id is refused rather
than repaired; prose comes back byte-identical, because the one permitted
transform is the renderer's and happens two steps later.

**"No numeric bound appears that isn't inherited."**
:class:`TestInheritedDomains` pins the inherited bounds to their owners — the
excerpt bound to ``verify_citations``, the fraction domain *behaviourally* to
the parser that raises on it, the strengthens ``strength`` to SPEC.md's
edge-class table — and :class:`TestNoAuthoredBound` shows the bounds a coder
would otherwise add are absent: prose length, entry count, file size.

:class:`TestDependencyDirection` reads the module's import set out of its AST.
The import set is pinned to ``values`` importing ``kb_schema`` alone, and a
negative constraint with no instrument is a wish.
"""

import ast
import tempfile
import unittest
from pathlib import Path

from kb_tools import kb_index_lib, kb_schema, verify_citations
from kb_tools.kb_write import render, values

_MODULE = Path(values.__file__)


# ---------------------------------------------------------------------------
# Fixture helpers
# ---------------------------------------------------------------------------


def _insert_file(**overrides: str) -> str:
    """A minimal well-formed ``insert-claim-entry`` file, one key per line.

    Values are spliced as raw TOML so a fixture can put any shape on the right
    of the ``=``. Every fixture below starts here and perturbs exactly one key,
    which is what makes "refused for its own reason" checkable.
    """
    body = {
        "register": '"part3/claim-quality.md"',
        "title": '"Price-Unclosable Attention Wedge"',
        "rigor": "0.7",
        "rationale": '"firm equates perceived MP to wage"',
    }
    body.update(overrides)
    lines = ["[[entry]]"] + [f"{key} = {value}" for key, value in body.items()]
    return "\n".join(lines) + "\n"


def _only(parsed: values.ParsedValues) -> values.Refusal:
    """The single refusal a one-defect fixture must produce.

    Asserting there is exactly one is part of the check: a fixture that trips
    two ladders proves neither of them fired for the reason it was written for.
    """
    if len(parsed.refusals) != 1:
        raise AssertionError(f"expected exactly one refusal, got {parsed.refusals}")
    return parsed.refusals[0]


def _support_leaf_text(fraction: float | None) -> str:
    """A support-hosting leaf whose one beneficiary edge carries ``fraction``.

    Composed through ``render.py`` rather than hand-typed: the production
    composer is the one that knows the block's layout, and a hand-typed fixture
    here would be the same freehand-metadata defect in a test file.
    """
    block = render.render_frontmatter_block(
        render.FrontmatterValues(
            kind="leaf",
            support_nodes=(render.SupportDecl(sup_id="sup-hh8888", supports=(("clm-aa1111", fraction),)),),
        )
    )
    return f"{block}\n\n# A Support Container\n"


def _frontmatter_file(strength: str) -> str:
    """A ``set-frontmatter`` file whose one strengthens pair carries ``strength``."""
    return (
        '[[entry]]\ndocument = "part3/leaf.md"\nkind = "leaf"\n\n'
        '[[entry.experiment-node]]\nexp-id = "exp-gg7777"\nstatus = "run"\n'
        f'strengthens = [{{ id = "clm-aa1111", strength = {strength} }}]\n'
    )


# ---------------------------------------------------------------------------
# The file grammar (four rules)
# ---------------------------------------------------------------------------


class TestGrammar(unittest.TestCase):
    """The values-file shape: TOML, one ``[[entry]]`` array, op on argv."""

    def test_a_well_formed_file_parses_to_its_values(self):
        parsed = values.parse_values(_insert_file(), op="insert-claim-entry")
        self.assertEqual(parsed.refusals, ())
        self.assertEqual(len(parsed.entries), 1)
        self.assertEqual(parsed.entries[0].index, 1)
        self.assertEqual(parsed.entries[0].values["rigor"], 0.7)

    def test_one_file_may_batch_entries(self):
        text = _insert_file() + "\n" + _insert_file(title='"A Second Entry"')
        parsed = values.parse_values(text, op="insert-claim-entry")
        self.assertEqual(parsed.refusals, ())
        self.assertEqual([entry.index for entry in parsed.entries], [1, 2])
        self.assertEqual(parsed.entries[1].values["title"], "A Second Entry")

    def test_the_op_may_not_be_named_inside_the_file(self):
        # An op key in the file is a second source for which op is
        # running, and a mismatch class with no reader.
        text = 'op = "insert-claim-entry"\n\n' + _insert_file()
        refusal = _only(values.parse_values(text, op="insert-claim-entry"))
        self.assertEqual(refusal.field, "op")
        self.assertEqual(refusal.line, 1)
        self.assertIsNone(refusal.entry)

    def test_a_single_entry_table_is_not_the_array_of_tables(self):
        text = '[entry]\nregister = "part3/claim-quality.md"\n'
        refusal = _only(values.parse_values(text, op="insert-claim-entry"))
        self.assertEqual(refusal.field, "entry")
        self.assertIn("array of tables", refusal.detail)

    def test_a_file_with_no_entries_is_refused(self):
        for text in ("entry = []\n", "# nothing here\n"):
            with self.subTest(text=text):
                refusal = _only(values.parse_values(text, op="insert-claim-entry"))
                self.assertEqual(refusal.field, "entry")

    def test_an_entry_that_is_not_a_table_is_refused(self):
        refusal = _only(values.parse_values('entry = ["a string"]\n', op="insert-claim-entry"))
        self.assertEqual(refusal.field, "entry")
        self.assertEqual(refusal.entry, 1)

    def test_unparseable_toml_is_a_located_refusal(self):
        # An unterminated literal string is unparseable TOML for a reason that
        # has nothing to do with a trailing quote; the hint should still point
        # at the one sequence a literal block truly cannot hold — its own
        # delimiter — rather than at a harmless trailing quote.
        text = "[[entry]]\ntitle = 'unterminated\nrigor = 0.7\n"
        refusal = _only(values.parse_values(text, op="insert-claim-entry"))
        self.assertEqual(refusal.field, "entry")
        self.assertIsNotNone(refusal.line)
        self.assertIn("'''", refusal.detail)

    def test_an_unknown_op_is_a_usage_error_not_a_refusal(self):
        # Which op is running comes from argv, where an unknown subcommand is
        # the CLI's usage error (exit 2) rather than the values' (exit 7).
        with self.assertRaises(ValueError):
            values.parse_values(_insert_file(), op="set-score")

    def test_every_refusal_in_a_batch_is_reported(self):
        text = _insert_file(rigor="47") + "\n" + _insert_file(rigor="-1.0")
        parsed = values.parse_values(text, op="insert-claim-entry")
        self.assertEqual([refusal.entry for refusal in parsed.refusals], [1, 2])
        # A batch that refuses any entry writes none, so the good half of a
        # partly-bad file is never handed on.
        self.assertEqual(parsed.entries, ())


# ---------------------------------------------------------------------------
# The closed vocabulary
# ---------------------------------------------------------------------------


class TestClosedVocabulary(unittest.TestCase):
    """Unrecognized keys are refused, never ignored; required keys are never defaulted."""

    def test_an_unknown_key_is_refused_naming_itself_and_its_line(self):
        refusal = _only(values.parse_values(_insert_file(confidence="0.7"), op="insert-claim-entry"))
        self.assertEqual(refusal.field, "confidence")
        self.assertEqual(refusal.entry, 1)
        self.assertEqual(refusal.line, 6)

    def test_a_missing_required_key_is_refused_naming_itself(self):
        text = '[[entry]]\nregister = "part3/claim-quality.md"\ntitle = "T"\nrigor = 0.7\n'
        refusal = _only(values.parse_values(text, op="insert-claim-entry"))
        self.assertEqual(refusal.field, "rationale")
        self.assertEqual(refusal.line, 1)

    def test_an_unknown_key_inside_a_nested_table_is_refused(self):
        text = _insert_file() + '\n[[entry.depends-on]]\nid = "clm-aa1111"\nreason = "why"\n'
        refusal = _only(values.parse_values(text, op="insert-claim-entry"))
        self.assertEqual(refusal.field, "depends-on[1].reason")
        self.assertEqual(refusal.line, 9)

    def test_a_missing_required_key_inside_a_nested_table_is_refused(self):
        text = _insert_file() + '\n[[entry.depends-on]]\ncontext = "entry-local"\n'
        refusal = _only(values.parse_values(text, op="insert-claim-entry"))
        self.assertEqual(refusal.field, "depends-on[1].id")

    def test_depends_on_and_rigor_are_spelled_identically_across_ops(self):
        # The identity requirement: one concept, one key, one shape, in
        # every op that carries it. Spelled twice, these are two grammars.
        for op in ("insert-claim-entry", "insert-support-entry", "add-depends-on"):
            with self.subTest(op=op):
                names = {spec.name: spec.check for spec in values.OP_FIELDS[op]}
                self.assertIn("depends-on", names)
                self.assertIs(names["depends-on"], values.check_depends_on)
        for op in ("insert-claim-entry", "insert-support-entry", "set-rigor"):
            with self.subTest(op=op):
                names = {spec.name: spec.check for spec in values.OP_FIELDS[op]}
                self.assertIs(names["rigor"], values.check_rigor)

    def test_supports_is_spelled_identically_wherever_a_fan_out_is_authored(self):
        # The staged pairs and the hosted pairs are the same pairs in
        # two homes, so they are one key of one shape. Two
        # spellings would make them two concepts and let a transcription from
        # one home into the other lose a field.
        insert = {spec.name: spec.check for spec in values.OP_FIELDS["insert-support-entry"]}
        hosted = {spec.name: spec.check for spec in values._SUPPORT_NODE_FIELDS}
        self.assertIs(insert["supports"], values.check_supports)
        self.assertIs(hosted["supports"], values.check_supports)

    def test_supports_is_refused_on_the_claim_insert(self):
        # A claim entry stages no fan-out and the claim renderer takes no such
        # value, so the key would have no renderer behind it there.
        text = _insert_file() + '\n[[entry.supports]]\nid = "clm-aa1111"\nfraction = 0.5\n'
        refusal = _only(values.parse_values(text, op="insert-claim-entry"))
        self.assertEqual(refusal.field, "supports")

    def test_a_staged_supports_pair_takes_the_inherited_fraction_domain(self):
        # The domain is `kb_index_lib.parse_support_leaf`'s, inherited through
        # the same checker the hosted pairs use — outside [0, 1] out of range,
        # `*pending*` a legal authored value.
        def parse(fraction: str) -> values.ParsedValues:
            text = _insert_file() + f'\n[[entry.supports]]\nid = "clm-aa1111"\nfraction = {fraction}\n'
            return values.parse_values(text, op="insert-support-entry")

        for bad in ("1.5", "-0.5"):
            with self.subTest(fraction=bad):
                self.assertEqual(_only(parse(bad)).field, "supports[1].fraction")
        for good in ("0", "0.0", "0.5", "1.0", f'"{values.PENDING_LITERAL}"'):
            with self.subTest(fraction=good):
                self.assertEqual(parse(good).refusals, ())

    def test_path_stable_is_a_set_frontmatter_key_and_nothing_else(self):
        # A real key of the block, typed generically by the
        # frontmatter reader; with hand-editing retired this op is the only way
        # to author one.
        names = {spec.name: spec for spec in values.OP_FIELDS["set-frontmatter"]}
        self.assertIn("path-stable", names)
        self.assertFalse(names["path-stable"].required)
        self.assertIs(names["path-stable"].check, values.check_prose)
        for op in ("insert-claim-entry", "insert-support-entry", "mark-claim-in-leaf"):
            with self.subTest(op=op):
                self.assertNotIn("path-stable", {spec.name for spec in values.OP_FIELDS[op]})

    def test_path_stable_accepts_any_single_paragraph_label(self):
        # No vocabulary and no length is stated for this field anywhere in the
        # contract, so none is enforced. What IS enforced is the one
        # constraint the block's grammar already imposes on every field.
        def parse(value: str) -> values.ParsedValues:
            return values.parse_values(
                f'[[entry]]\ndocument = "part3/leaf.md"\nkind = "leaf"\npath-stable = {value}\n',
                op="set-frontmatter",
            )

        self.assertEqual(parse('"synthetic fixture register — root scope"').refusals, ())
        self.assertEqual(parse(f'"{"x" * 500}"').refusals, ())
        self.assertEqual(_only(parse("'''one\n\ntwo'''")).field, "path-stable")

    def test_id_leads_every_update_op(self):
        # The entry being edited is the first value of every update op, and it
        # is spelled `id` — never `source id` or `sup id`, whose orientation a
        # caller would have to infer.
        for op in ("set-rigor", "set-rationale", "add-depends-on", "set-on-point-fraction"):
            with self.subTest(op=op):
                first = values.OP_FIELDS[op][0]
                self.assertEqual(first.name, "id")
                self.assertTrue(first.required)

    def test_only_set_rationale_takes_a_work_id_as_the_entry_it_edits(self):
        # A rationale is one prose field on all three register entry kinds, so
        # set-rationale reaches a work; the other two update ops that take an
        # entry id do not, and the asymmetry is the test. A work's standing has
        # a field and an op of its own, and a terminal node carries no
        # depends-on list for a bullet to be added to.
        edits = {
            "set-rationale": '[[entry]]\nid = "{id}"\nrationale = "Restated."\n',
            "set-rigor": '[[entry]]\nid = "{id}"\nrigor = 0.5\n',
            "add-depends-on": '[[entry]]\nid = "{id}"\n  [[entry.depends-on]]\n  id = "clm-aa1111"\n',
        }
        for op, template in edits.items():
            with self.subTest(op=op):
                claim = values.parse_values(template.format(id="clm-aa1111"), op=op)
                self.assertEqual(claim.refusals, (), f"{op} refused a claim id")
                work = values.parse_values(template.format(id="work-nobody2026"), op=op)
                if op == "set-rationale":
                    self.assertEqual(work.refusals, ())
                    self.assertEqual(work.entries[0].values["id"], "work-nobody2026")
                else:
                    self.assertEqual(_only(work).field, "id")

    def test_a_work_id_set_rationale_takes_is_the_one_the_key_derives(self):
        # Shape only, and the same shape check_work_id applies everywhere else:
        # a token that is not `work-` plus a citation key is refused by the op
        # that now admits the kind, not admitted for being prefixed right.
        for token in ("work-nobody2026", "work-vanDerWaals1873a"):
            with self.subTest(token=token):
                self.assertEqual(values.check_rationale_id(token, "id"), token)
        for token in ("work-", "work-not a key", "work-trailing-", "exp-aa1111"):
            with self.subTest(token=token), self.assertRaises(Exception):
                values.check_rationale_id(token, "id")

    def test_the_retired_spellings_are_not_in_the_vocabulary(self):
        # Seven retired renames. A surviving pre-rename token would be a
        # second live spelling of a retired concept.
        self.assertNotIn("set-score", values.OP_FIELDS)
        self.assertNotIn("mark-tier2", values.OP_FIELDS)
        self.assertNotIn("set-leaf-frontmatter", values.OP_FIELDS)
        self.assertNotIn("set-support-fraction", values.OP_FIELDS)
        every_key = {spec.name for fields in values.OP_FIELDS.values() for spec in fields}
        self.assertNotIn("value", every_key)
        self.assertNotIn("score", every_key)
        self.assertNotIn("anchor excerpt", every_key)
        self.assertIn("locator", every_key)

    def test_strengthen_by_is_a_claim_key_only(self):
        # A support entry has no strengthen-by section in the register
        # (`parse_support_quality_entries` reads quality/depends-on/rationale
        # and nothing else), so accepting the key there would be a key with no
        # renderer behind it.
        claim_keys = {spec.name for spec in values.OP_FIELDS["insert-claim-entry"]}
        support_keys = {spec.name for spec in values.OP_FIELDS["insert-support-entry"]}
        self.assertIn("strengthen-by", claim_keys)
        self.assertNotIn("strengthen-by", support_keys)

    def test_a_sup_id_in_a_claims_list_is_refused(self):
        text = '[[entry]]\ndocument = "part3/leaf.md"\nkind = "leaf"\nclaims = ["clm-aa1111", "sup-hh8888"]\n'
        refusal = _only(values.parse_values(text, op="set-frontmatter"))
        self.assertEqual(refusal.field, "claims[2]")
        self.assertEqual(refusal.line, 4)

    def test_a_kind_outside_the_closed_set_is_refused(self):
        text = '[[entry]]\ndocument = "part3/leaf.md"\nkind = "chapter"\n'
        refusal = _only(values.parse_values(text, op="set-frontmatter"))
        self.assertEqual(refusal.field, "kind")

    def test_an_experiment_status_outside_the_closed_set_is_refused(self):
        text = (
            '[[entry]]\ndocument = "part3/leaf.md"\nkind = "leaf"\n\n'
            '[[entry.experiment-node]]\nexp-id = "exp-gg7777"\nstatus = "planned"\n'
        )
        refusal = _only(values.parse_values(text, op="set-frontmatter"))
        self.assertEqual(refusal.field, "experiment-node[1].status")

    def test_a_frontmatter_entry_with_hosted_nodes_parses(self):
        text = (
            '[[entry]]\ndocument = "part3/leaf.md"\nkind = "leaf"\nclaims = ["clm-aa1111"]\n\n'
            '[[entry.experiment-node]]\nexp-id = "exp-gg7777"\nstatus = "run"\n'
            'strengthens = [{ id = "clm-aa1111", strength = 0.8 }]\n\n'
            '[[entry.support-node]]\nsup-id = "sup-hh8888"\n'
            'supports = [{ id = "clm-aa1111", fraction = "*pending*" }]\n'
        )
        parsed = values.parse_values(text, op="set-frontmatter")
        self.assertEqual(parsed.refusals, ())
        entry = parsed.entries[0].values
        self.assertEqual(
            entry["experiment-node"],
            (values.ExperimentValues("exp-gg7777", "run", (values.StrengthensPair("clm-aa1111", 0.8),)),),
        )
        self.assertEqual(
            entry["support-node"], (values.SupportValues("sup-hh8888", (values.SupportsPair("clm-aa1111", None),)),)
        )


# ---------------------------------------------------------------------------
# Inherited domains
# ---------------------------------------------------------------------------


class TestInheritedDomains(unittest.TestCase):
    """Every bound the ladder enforces, and its owner."""

    def test_rigor_47_is_refused(self):
        # TOML parses 47 as 47.0 without complaint; only this check catches
        # the value being out of domain.
        refusal = _only(values.parse_values(_insert_file(rigor="47"), op="insert-claim-entry"))
        self.assertEqual(refusal.field, "rigor")
        self.assertEqual(refusal.line, 4)

    def test_rigor_accepts_the_closed_interval_and_the_pending_literal(self):
        for literal, expected in (("0.0", 0.0), ("1.0", 1.0), ("0.875", 0.875), ('"*pending*"', None)):
            with self.subTest(literal=literal):
                parsed = values.parse_values(_insert_file(rigor=literal), op="insert-claim-entry")
                self.assertEqual(parsed.refusals, ())
                self.assertEqual(parsed.entries[0].values["rigor"], expected)

    def test_rigor_below_zero_is_refused(self):
        refusal = _only(values.parse_values(_insert_file(rigor="-0.1"), op="insert-claim-entry"))
        self.assertEqual(refusal.field, "rigor")

    def test_a_string_that_is_not_the_pending_literal_is_refused(self):
        refusal = _only(values.parse_values(_insert_file(rigor='"pending"'), op="insert-claim-entry"))
        self.assertEqual(refusal.field, "rigor")

    def test_a_strength_outside_zero_one_is_refused(self):
        # SPEC.md's edge-class table gives a strengthens edge's strength as
        # [0, 1]: 5.0 otherwise writes a claim `solidity: 5.00 (ok to build
        # on)`, off the band ladder. This is the earlier of the two gates on
        # it; the verifier holds the materialized edge to the same domain.
        for literal in ("5.0", "-3.0", "1.1", "-0.1"):
            with self.subTest(literal=literal):
                refusal = _only(values.parse_values(_frontmatter_file(literal), op="set-frontmatter"))
                self.assertEqual(refusal.field, "experiment-node[1].strengthens[1].strength")

    def test_a_strength_accepts_the_closed_interval(self):
        for literal in ("0.0", "0.8", "1.0"):
            with self.subTest(literal=literal):
                parsed = values.parse_values(_frontmatter_file(literal), op="set-frontmatter")
                self.assertEqual(parsed.refusals, ())

    def test_a_fraction_outside_the_closed_interval_is_refused(self):
        for literal in ("1.1", "-0.1"):
            with self.subTest(literal=literal):
                text = f'[[entry]]\nid = "sup-hh8888"\nclaim = "clm-aa1111"\nfraction = {literal}\n'
                refusal = _only(values.parse_values(text, op="set-on-point-fraction"))
                self.assertEqual(refusal.field, "fraction")
                self.assertEqual(refusal.line, 4)

    def test_a_fraction_of_zero_is_a_value(self):
        # Zero says the support is sound and bears nothing on this claim — a
        # fact distinct from no edge and from the pending literal, so it is
        # authored rather than refused.
        for literal, expected in (("0", 0.0), ("0.0", 0.0)):
            with self.subTest(literal=literal):
                text = f'[[entry]]\nid = "sup-hh8888"\nclaim = "clm-aa1111"\nfraction = {literal}\n'
                parsed = values.parse_values(text, op="set-on-point-fraction")
                self.assertEqual(parsed.refusals, ())
                self.assertEqual(parsed.entries[0].values["fraction"], expected)

    def test_the_fraction_domain_is_the_parsers_own(self):
        # The pin that matters: this module refuses exactly where the reader
        # raises. Not a retyped bound — the same four points put through
        # `parse_support_leaf`, which owns [0, 1].
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for fraction, admissible in ((0.0, True), (-0.1, False), (1.1, False), (0.5, True), (1.0, True)):
                with self.subTest(fraction=fraction):
                    path = root / "leaf.md"
                    path.write_text(_support_leaf_text(fraction), encoding="utf-8")
                    if admissible:
                        self.assertTrue(kb_index_lib.parse_support_leaf(path, root))
                    else:
                        with self.assertRaises(kb_index_lib.SupportLeafError):
                            kb_index_lib.parse_support_leaf(path, root)
                    refused = False
                    try:
                        values.check_fraction(fraction, "fraction")
                    except Exception:  # the module-internal refusal signal
                        refused = True
                    self.assertEqual(refused, not admissible)

    def test_the_excerpt_bound_is_the_citation_checkers_own(self):
        self.assertEqual(values.EXCERPT_MAX_CHARS, verify_citations.EXCERPT_MAX_CHARS)

    def test_an_over_long_excerpt_is_refused_and_an_at_bound_one_is_not(self):
        for length, refused in ((values.EXCERPT_MAX_CHARS, False), (values.EXCERPT_MAX_CHARS + 1, True)):
            with self.subTest(length=length):
                text = (
                    f'[[entry]]\nexcerpt = "{"x" * length}"\n'
                    'cited-document = "part3/leaf.md"\n'
                    'anchor = "a-heading"\n'
                    'citing-document = "part3/index.md"\n'
                )
                parsed = values.parse_values(text, op="render-citation")
                if refused:
                    self.assertEqual(_only(parsed).field, "excerpt")
                else:
                    self.assertEqual(parsed.refusals, ())

    def test_the_pending_literal_is_the_readers_own(self):
        self.assertEqual(values.PENDING_LITERAL, kb_index_lib.PENDING_LITERAL)

    def test_the_id_grammar_is_kb_schemas_own(self):
        # Shape only — existence is ops.py's against the store.
        for token in ("clm-aa1111", "clm-k970j7"):
            self.assertEqual(values.check_claim_id(token, "id"), token)
        for token in ("clm-AA1111", "clm-12345", "clm-1234567", "sup-hh8888", "not-an-id"):
            with self.subTest(token=token), self.assertRaises(Exception):
                values.check_claim_id(token, "id")
        for placeholder in sorted(kb_schema.ID_PLACEHOLDERS):
            with self.subTest(placeholder=placeholder), self.assertRaises(Exception):
                values.check_entry_id(placeholder, "id")

    def test_the_document_kind_vocabulary_covers_the_readers_leaf_kinds(self):
        self.assertTrue(set(verify_citations.LEAF_KINDS) <= set(values.DOCUMENT_KINDS))
        self.assertEqual(set(values.DOCUMENT_KINDS) - set(verify_citations.LEAF_KINDS), {"index", "entry-point"})

    def test_framework_target_tokens_are_the_bullet_scanners_own(self):
        # A depends-on target's framework token is matched in the authored
        # spelling the head scanner recognizes; the derived `axiom-3` form
        # would be dropped silently if written into a bullet.
        for token in ("INVARIANT-S2", "INVARIANT-AB12", "invariant-s2", "INVARIANT-2"):
            with self.subTest(token=token):
                mine = bool(values._INVARIANT_TOKEN_RE.match(token))
                theirs = bool(kb_index_lib._INVARIANT_TOKEN_RE.fullmatch(token))
                self.assertEqual(mine, theirs)
        for token in ("Axiom 3", "Axiom 12", "axiom-3", "Axiom"):
            with self.subTest(token=token):
                mine = bool(values._AXIOM_TOKEN_RE.match(token))
                theirs = bool(kb_index_lib._AXIOM_TOKEN_RE.fullmatch(token))
                self.assertEqual(mine, theirs)

    def test_a_framework_target_is_an_admissible_dependency(self):
        text = _insert_file() + '\ndepends-on = [{ id = "INVARIANT-S2" }, { id = "Axiom 3" }]\n'
        parsed = values.parse_values(text, op="insert-claim-entry")
        self.assertEqual(parsed.refusals, ())
        self.assertEqual(
            parsed.entries[0].values["depends-on"],
            (values.DependsOnValue("INVARIANT-S2"), values.DependsOnValue("Axiom 3")),
        )

    def test_a_normalized_axiom_id_is_refused_as_a_target(self):
        text = _insert_file() + '\ndepends-on = [{ id = "axiom-3" }]\n'
        refusal = _only(values.parse_values(text, op="insert-claim-entry"))
        self.assertEqual(refusal.field, "depends-on[1].id")


class TestNoAuthoredBound(unittest.TestCase):
    """The bounds a coder would add, shown absent.

    Prose length, entry count and file size are the three places a magnitude
    would otherwise appear. No contract states any of them, so none is here.
    """

    def test_no_prose_length_bound(self):
        parsed = values.parse_values(_insert_file(rationale=f'"{"word " * 4000}"'), op="insert-claim-entry")
        self.assertEqual(parsed.refusals, ())
        self.assertEqual(len(parsed.entries[0].values["rationale"]), 20000)

    def test_no_entry_count_or_file_size_bound(self):
        text = "\n".join(_insert_file(title=f'"Entry {n}"') for n in range(200))
        parsed = values.parse_values(text, op="insert-claim-entry")
        self.assertEqual(parsed.refusals, ())
        self.assertEqual(len(parsed.entries), 200)

    def test_a_pending_strength_is_refused_because_the_verifier_rejects_a_null(self):
        refusal = _only(values.parse_values(_frontmatter_file('"*pending*"'), op="set-frontmatter"))
        self.assertEqual(refusal.field, "experiment-node[1].strengthens[1].strength")


# ---------------------------------------------------------------------------
# The reader's number grammar — inherited, and not a magnitude
# ---------------------------------------------------------------------------


class TestReaderNumberGrammar(unittest.TestCase):
    """A number the reader's grammar cannot hold is refused here, naming the field.

    The domains above are magnitudes; this is not one. Python's shortest
    round-tripping repr — ``render._format_score``'s output, and deliberately
    so — leaves the reader's ``-?\\d+(?:\\.\\d+)?`` below ``1e-4``, above
    ``1e16``, and at ``nan`` / ``inf``. Every such value sits *inside* its
    magnitude domain, so no domain check can see it, and each fails silently in
    a different direction downstream: a confidence line is read back as a
    different number, a pair line is not matched at all and the edge is dropped.

    A check failing for the wrong reason is a failed check.
    """

    #: Rendered spellings the reader's grammar does not hold, with what a caller
    #: would plausibly type to reach each.
    _UNREADABLE = ("1e-05", "0.00001", "2.5e-07")

    def test_a_rigor_below_the_reprs_exponent_threshold_is_refused_at_step_one(self):
        for literal in self._UNREADABLE:
            with self.subTest(literal=literal):
                refusal = _only(values.parse_values(_insert_file(rigor=literal), op="insert-claim-entry"))
                self.assertEqual(refusal.field, "rigor")
                self.assertEqual(refusal.line, 4)

    def test_the_representable_neighbour_is_accepted(self):
        # The teeth: 1e-4 is the threshold, so `0.0001` reprs as itself and is
        # a perfectly writable rigor. A check that refused it would be an
        # authored magnitude bound, which this program does not add.
        parsed = values.parse_values(_insert_file(rigor="0.0001"), op="insert-claim-entry")
        self.assertEqual(parsed.refusals, ())
        self.assertEqual(parsed.entries[0].values["rigor"], 0.0001)

    def test_a_fraction_the_reader_cannot_hold_is_refused(self):
        text = '[[entry]]\nid = "sup-hh8888"\nclaim = "clm-aa1111"\nfraction = 1e-05\n'
        refusal = _only(values.parse_values(text, op="set-on-point-fraction"))
        self.assertEqual(refusal.field, "fraction")
        self.assertEqual(refusal.line, 4)

    def test_a_strength_inside_its_domain_can_still_be_unwritable(self):
        # The two gates are independent: `1e-05` and `2.5e-07` are both inside
        # [0, 1] and neither can be written, because the token the renderer
        # emits for them is one the pair pattern does not match.
        for literal in ("1e-05", "2.5e-07"):
            with self.subTest(literal=literal):
                refusal = _only(values.parse_values(_frontmatter_file(literal), op="set-frontmatter"))
                self.assertEqual(refusal.field, "experiment-node[1].strengthens[1].strength")

    def test_the_representable_neighbour_is_a_writable_strength(self):
        # The teeth on the other side: `1e-4` is the repr threshold, so
        # `0.0001` reprs as itself and is a perfectly writable strength.
        parsed = values.parse_values(_frontmatter_file("0.0001"), op="set-frontmatter")
        self.assertEqual(parsed.refusals, ())

    def test_the_checked_token_is_the_one_the_renderer_writes(self):
        # This module may not import `render`, so it re-spells the repr.
        # The pin against that duplication: the two spellings agree.
        for number in (0.0, 1.0, 0.875, 0.0001, 1e-05, 4.0, 1e15, 1e300, float("nan"), float("inf")):
            with self.subTest(number=number):
                self.assertEqual(f"{number}", render._format_score(number))

    def test_this_module_refuses_exactly_what_the_readers_own_matchers_drop(self):
        # The pin that matters, and the reason `_READER_NUMBER_RE` is a copy
        # rather than a retyped bound: every value below is put through the
        # production renderer and then through the reader's OWN pair patterns,
        # and this module's verdict is compared against theirs. The verdict
        # asked for is `_require_number`'s — the representability funnel every
        # numeric field passes through — and not a field checker's, so that a
        # value's magnitude domain cannot decide the answer.
        corpus = (0.0001, 0.5, 1.0, 4.0, 1e15, 1e-05, 2.5e-07, 1e300, float("nan"), float("inf"))
        for number in corpus:
            with self.subTest(number=number):
                strengthens = render.render_strengthens_pair_line("clm-aa1111", number)
                supports = render.render_supports_pair_line("clm-aa1111", number)
                readable = bool(kb_index_lib._STRENGTHENS_PAIR_RE.match(strengthens))
                self.assertEqual(readable, bool(kb_index_lib._SUPPORTS_PAIR_RE.match(supports)))
                refused = False
                try:
                    values._require_number(number, "strength", domain="[0, 1]")
                except Exception:  # the module-internal refusal signal
                    refused = True
                self.assertEqual(refused, not readable)

    def test_an_unreadable_confidence_would_be_read_back_as_a_different_number(self):
        # The first silent direction, demonstrated through the real parser
        # rather than asserted: `_NUMBER_RE.search` takes the LEADING token of
        # `1e-05`, so a rigor inside [0, 1] comes back as 1.0.
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            register = root / "claim-quality.md"
            register.write_text(
                render.render_claim_entry(
                    node_id="clm-aa1111",
                    title="An Entry",
                    confidence=1e-05,
                    rationale="why",
                )
                + "\n",
                encoding="utf-8",
            )
            entries = kb_index_lib.parse_claim_quality_file(register, root)
        self.assertEqual([entry.confidence for entry in entries], [1.0])

    def test_an_unreadable_fraction_would_drop_its_edge_entirely(self):
        # The second silent direction: the pair line does not match, so the
        # beneficiary edge is not read at all — no error, no edge.
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            leaf = root / "leaf.md"
            leaf.write_text(_support_leaf_text(1e-05), encoding="utf-8")
            nodes = kb_index_lib.parse_support_leaf(leaf, root)
        self.assertEqual([node.supports for node in nodes], [()])


# ---------------------------------------------------------------------------
# The blank-line refusal — this module's load-bearing class
# ---------------------------------------------------------------------------


class TestBlankLineRefusal(unittest.TestCase):
    """A paragraph break in a prose value is refused here, and the line is named.

    The reader's fold breaks on a blank line *before* normalization runs and
    discards the remainder, so no rendering can save the value: collapsing it
    would silently edit an author's prose and writing it would silently truncate
    it. The refusal is `values.py`'s, and its message names the
    paragraph break rather than the TOML syntax — which is valid.
    """

    def test_a_blank_line_names_its_own_line_in_the_idiomatic_layout(self):
        text = (
            "[[entry]]\n"
            'register = "part3/claim-quality.md"\n'
            'title = "T"\n'
            "rigor = 0.7\n"
            "rationale = '''\nfirst paragraph\n\nsecond paragraph\n'''\n"
        )
        parsed = values.parse_values(text, op="insert-claim-entry")
        refusal = _only(parsed)
        self.assertEqual(refusal.field, "rationale")
        self.assertEqual(refusal.line, 7)
        self.assertIn("blank line", refusal.detail)

    def test_a_blank_line_names_its_own_line_when_the_value_opens_on_the_key_line(self):
        text = (
            "[[entry]]\n"
            'register = "part3/claim-quality.md"\n'
            'title = "T"\n'
            "rigor = 0.7\n"
            "rationale = '''first paragraph\n\nsecond paragraph'''\n"
        )
        refusal = _only(values.parse_values(text, op="insert-claim-entry"))
        self.assertEqual(refusal.line, 6)

    def test_a_whitespace_only_line_is_a_blank_line(self):
        text = (
            "[[entry]]\n"
            'register = "r.md"\n'
            'title = "T"\n'
            "rigor = 0.7\n"
            "rationale = '''\nfirst\n   \nsecond\n'''\n"
        )
        refusal = _only(values.parse_values(text, op="insert-claim-entry"))
        self.assertEqual(refusal.field, "rationale")
        self.assertEqual(refusal.line, 7)

    def test_nothing_reaches_the_renderer(self):
        # The value is refused, so no entry is produced — not a collapsed one,
        # not a truncated one, and not the good sibling in the same batch.
        text = (
            _insert_file() + "\n" + "[[entry]]\n"
            'register = "r.md"\n'
            'title = "T"\n'
            "rigor = 0.7\n"
            "rationale = '''\na\n\nb\n'''\n"
        )
        parsed = values.parse_values(text, op="insert-claim-entry")
        self.assertEqual(parsed.entries, ())
        self.assertEqual(len(parsed.refusals), 1)
        self.assertEqual(parsed.refusals[0].entry, 2)

    def test_a_single_newline_is_not_this_class(self):
        # The key-leading-continuation class is representable and is the
        # renderer's collapse to defeat, not a refusal here.
        text = (
            "[[entry]]\n"
            'register = "r.md"\n'
            'title = "T"\n'
            "rigor = 0.7\n"
            "rationale = '''\ntext\n- confidence: 0.9\nmore text\n'''\n"
        )
        parsed = values.parse_values(text, op="insert-claim-entry")
        self.assertEqual(parsed.refusals, ())
        stored = parsed.entries[0].values["rationale"]
        self.assertEqual(render.collapse_prose(stored), "text - confidence: 0.9 more text")

    def test_the_trailing_newline_of_a_literal_block_is_not_a_blank_line(self):
        # Every idiomatic ''' block ends with one. Refusing it would refuse the
        # grammar's own recommended spelling.
        text = (
            "[[entry]]\n"
            'register = "r.md"\n'
            'title = "T"\n'
            "rigor = 0.7\n"
            "rationale = '''\none paragraph only\n'''\n"
        )
        parsed = values.parse_values(text, op="insert-claim-entry")
        self.assertEqual(parsed.refusals, ())
        self.assertEqual(parsed.entries[0].values["rationale"], "one paragraph only\n")

    def test_every_prose_field_refuses_a_paragraph_break(self):
        # The rule is the grammar's, not one field's: each of these is
        # single-paragraph where the reader meets it.
        broken = "'''\nfirst\n\nsecond\n'''"
        cases = (
            ("insert-claim-entry", _insert_file(title=broken), "title"),
            ("insert-claim-entry", _insert_file(**{"no-edge": broken}), "no-edge"),
            ("insert-claim-entry", _insert_file(**{"strengthen-by": f"[{broken}]"}), "strengthen-by[1]"),
            (
                "insert-claim-entry",
                _insert_file(**{"depends-on": f'[{{ id = "clm-aa1111", context = {broken} }}]'}),
                "depends-on[1].context",
            ),
            ("set-rationale", f'[[entry]]\nid = "clm-aa1111"\nrationale = {broken}\n', "rationale"),
            ("mark-claim-in-leaf", f'[[entry]]\ndocument = "l.md"\nid = "clm-aa1111"\nlocator = {broken}\n', "locator"),
        )
        for op, text, field in cases:
            with self.subTest(field=field):
                self.assertEqual(_only(values.parse_values(text, op=op)).field, field)


# ---------------------------------------------------------------------------
# No coercion, no defaulting, no repair
# ---------------------------------------------------------------------------


class TestNoCoercion(unittest.TestCase):
    """Malformed and unresolvable values are refused; nothing is substituted."""

    def test_an_absent_optional_key_is_absent_rather_than_defaulted(self):
        parsed = values.parse_values(_insert_file(), op="insert-claim-entry")
        self.assertNotIn("depends-on", parsed.entries[0].values)
        self.assertNotIn("no-edge", parsed.entries[0].values)
        self.assertNotIn("strengthen-by", parsed.entries[0].values)

    def test_prose_is_returned_exactly_as_supplied(self):
        # The whitespace collapse is render.py's. Applying it
        # here would put the transform in two places and leave the readback
        # comparing against a value nobody supplied.
        supplied = "  ragged   spacing\nand a newline  "
        parsed = values.parse_values(_insert_file(rationale=f"'''{supplied}'''"), op="insert-claim-entry")
        self.assertEqual(parsed.refusals, ())
        self.assertEqual(parsed.entries[0].values["rationale"], supplied)

    def test_a_boolean_is_not_read_as_a_number(self):
        # `isinstance(True, int)` is True in Python; a rigor of `true` silently
        # becoming 1.0 is exactly the near-miss this ladder must not perform.
        refusal = _only(values.parse_values(_insert_file(rigor="true"), op="insert-claim-entry"))
        self.assertEqual(refusal.field, "rigor")
        self.assertIn("boolean", refusal.detail)

    def test_an_integer_rigor_is_widened_losslessly(self):
        # Not a coercion: refusing `rigor = 1` while accepting `rigor = 1.0`
        # would be a byte-fidelity demand of the kind this program removes.
        parsed = values.parse_values(_insert_file(rigor="1"), op="insert-claim-entry")
        self.assertEqual(parsed.refusals, ())
        self.assertEqual(parsed.entries[0].values["rigor"], 1.0)

    def test_a_near_miss_id_is_refused_rather_than_repaired(self):
        for token in ("clm_aa1111", " clm-aa1111", "CLM-aa1111", "clm-aa111"):
            with self.subTest(token=token):
                text = f'[[entry]]\nid = "{token}"\nrigor = 0.7\n'
                self.assertEqual(_only(values.parse_values(text, op="set-rigor")).field, "id")

    def test_an_empty_prose_value_is_refused_rather_than_treated_as_absent(self):
        refusal = _only(values.parse_values(_insert_file(rationale='"   "'), op="insert-claim-entry"))
        self.assertEqual(refusal.field, "rationale")

    def test_an_empty_array_is_refused_rather_than_read_as_omission(self):
        refusal = _only(values.parse_values(_insert_file(**{"depends-on": "[]"}), op="insert-claim-entry"))
        self.assertEqual(refusal.field, "depends-on")


# ---------------------------------------------------------------------------
# Hostile shapes: what is malformed, and what is merely ugly
# ---------------------------------------------------------------------------


class TestHostileShapes(unittest.TestCase):
    """Refuse by grammar, and only by grammar.

    A preformatted metadata fragment offered *as prose* is not a refusal —
    prose may quote a marker, and a rationale discussing ``<!-- id: -->``
    placement is a legitimate value. What is refused is a shape the grammar
    cannot carry: a table where a scalar belongs, an array where prose belongs.
    """

    def test_a_table_where_a_scalar_belongs_is_refused(self):
        for key, literal in (("rigor", "{ value = 0.7 }"), ("title", '{ text = "T" }'), ("register", "{ path = 1 }")):
            with self.subTest(key=key):
                refusal = _only(values.parse_values(_insert_file(**{key: literal}), op="insert-claim-entry"))
                self.assertEqual(refusal.field, key)
                self.assertIn("table", refusal.detail)

    def test_an_array_where_prose_belongs_is_refused(self):
        refusal = _only(values.parse_values(_insert_file(title='["T"]'), op="insert-claim-entry"))
        self.assertEqual(refusal.field, "title")
        self.assertIn("array", refusal.detail)

    def test_a_scalar_where_a_table_array_belongs_is_refused(self):
        refusal = _only(values.parse_values(_insert_file(**{"depends-on": '"clm-aa1111"'}), op="insert-claim-entry"))
        self.assertEqual(refusal.field, "depends-on")

    def test_a_depends_on_item_that_is_not_a_table_is_refused(self):
        refusal = _only(values.parse_values(_insert_file(**{"depends-on": '["clm-aa1111"]'}), op="insert-claim-entry"))
        self.assertEqual(refusal.field, "depends-on[1]")

    def test_prose_may_quote_a_metadata_marker(self):
        # Merely ugly, not malformed. The marker is a string inside a value the
        # renderer will quote; refusing it would refuse an author discussing
        # the format. The rule is about who composes the bytes, not what
        # prose says.
        rationale = "the 2026-09-02 loss put <!-- id: clm-3hh05h --> above its ## heading"
        title = "Why <!-- kb-frontmatter --> Placement Matters"
        parsed = values.parse_values(
            _insert_file(rationale=f'"{rationale}"', title=f'"{title}"'), op="insert-claim-entry"
        )
        self.assertEqual(parsed.refusals, ())
        self.assertEqual(parsed.entries[0].values["rationale"], rationale)
        self.assertEqual(parsed.entries[0].values["title"], title)

    def test_latex_dense_prose_survives_the_literal_block_verbatim(self):
        # The reason for a file rather than argv: `$`, backslashes, backticks
        # and quotes all arrive unescaped and unchanged.
        text = (
            "[[entry]]\n"
            'register = "r.md"\n'
            'title = "T"\n'
            "rigor = 0.7\n"
            "rationale = '''firm equates perceived MP to wage; true MP exceeds it by "
            "$(1+\\varphi_e)^{1-\\rho_2}$ with `\\alpha` \"quoted\"'''\n"
        )
        parsed = values.parse_values(text, op="insert-claim-entry")
        self.assertEqual(parsed.refusals, ())
        self.assertIn("$(1+\\varphi_e)^{1-\\rho_2}$", parsed.entries[0].values["rationale"])

    def test_an_absolute_path_is_refused_and_the_convention_is_stated(self):
        # The failure a caller cannot see: the briefs render some
        # paths repo-root-relative, so both conventions are on screen.
        refusal = _only(values.parse_values(_insert_file(register='"/etc/passwd"'), op="insert-claim-entry"))
        self.assertEqual(refusal.field, "register")
        self.assertIn("kb-root", refusal.detail)

    def test_containment_is_not_this_modules_question(self):
        # A `..` path is well-formed and is refused one step later, by store.py
        # after symlink resolution. Duplicating that check
        # here would put the boundary in two places.
        parsed = values.parse_values(_insert_file(register='"../outside.md"'), op="insert-claim-entry")
        self.assertEqual(parsed.refusals, ())


class TestCommentDelimitersInProse(unittest.TestCase):
    """``-->`` inside a prose value is carried, because the reader carries it.

    The question raised is whether a value containing the frontmatter
    block's own closer must be refused. The rule is the same one the blank-line
    refusal follows and the answer is the opposite one: a value the reader
    cannot hold is refused *here*; a value it can hold is written, and never
    escaped or reshaped to make it look safer than it is — an escape would be
    the silent edit of an author's prose this module forbids, and it would put
    a transform in a module whose contract is that it performs none.

    ``FRONTMATTER_RE``'s closer is anchored to a line start, and every prose
    value is collapsed to a single physical line before it is written, so a
    ``-->`` inside a value can never reach column zero. The class below shows
    that end to end — through the production renderer and the production
    parser — and then shows the anchor is load-bearing, so the demonstration is
    not vacuous.

    What remains true and is *not* this module's to fix: a Markdown renderer
    that is not this toolchain ends an HTML comment at the first ``-->``
    wherever it sits. That is a rendering-surface defect.
    """

    _VALUE = "the fold reads `a --> b` as one hop, not two"

    def _document(self, no_claim: str) -> str:
        block = render.render_frontmatter_block(render.FrontmatterValues(kind="leaf", no_claim=no_claim))
        return f"[↑ Parent](index.md)\n\n{block}\n\n# A Leaf\n\nBody prose survives.\n"

    def test_the_value_is_accepted_and_returned_byte_identical(self):
        text = f'[[entry]]\ndocument = "l.md"\nkind = "leaf"\nno-claim = "{self._VALUE}"\n'
        parsed = values.parse_values(text, op="set-frontmatter")
        self.assertEqual(parsed.refusals, ())
        self.assertEqual(parsed.entries[0].values["no-claim"], self._VALUE)

    def test_the_written_block_round_trips_through_the_production_parser(self):
        document = self._document(self._VALUE)
        self.assertEqual(kb_index_lib.parse_frontmatter(document)["no-claim"], self._VALUE)

    def test_the_block_still_ends_where_the_writer_closed_it(self):
        # The other direction: the closer inside the value must not shorten the
        # block, and must not swallow the body when the block is scrubbed.
        document = self._document(self._VALUE)
        scrubbed = kb_index_lib.FRONTMATTER_RE.sub("", document)
        self.assertIn("# A Leaf", scrubbed)
        self.assertIn("Body prose survives.", scrubbed)
        self.assertNotIn("no-claim", scrubbed)

    def test_a_closer_at_a_line_start_is_the_terminator(self):
        # The teeth. Without the line-start anchor the three tests above would
        # pass over a parser that ends the block anywhere, so this shows the
        # anchor is what carries them — and that the renderer's collapse to one
        # physical line is what keeps a value from ever reaching column zero.
        hostile = self._document(self._VALUE).replace(f'no-claim: "{self._VALUE}"', "-->\nno-claim: swallowed")
        self.assertNotIn("no-claim", kb_index_lib.parse_frontmatter(hostile))
        self.assertEqual(render.collapse_prose(f"a\n{self._VALUE}").count("\n"), 0)


# ---------------------------------------------------------------------------
# Encoding
# ---------------------------------------------------------------------------


class TestEncoding(unittest.TestCase):
    """UTF-8 strict, and a decode failure is a located refusal."""

    def test_a_non_utf8_file_is_refused_with_its_line(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "values.toml"
            path.write_bytes(b'[[entry]]\nregister = "r.md"\nrigor = 0.7\ntitle = "caf\xe9"\nrationale = "why"\n')
            parsed = values.parse_values_file(path, op="insert-claim-entry")
        refusal = _only(parsed)
        self.assertEqual(refusal.line, 4)
        self.assertIn("UTF-8", refusal.detail)
        self.assertEqual(parsed.entries, ())

    def test_utf8_prose_round_trips(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "values.toml"
            path.write_text(_insert_file(rationale='"café — naïve résumé"'), encoding="utf-8")
            parsed = values.parse_values_file(path, op="insert-claim-entry")
        self.assertEqual(parsed.refusals, ())
        self.assertEqual(parsed.entries[0].values["rationale"], "café — naïve résumé")


# ---------------------------------------------------------------------------
# The dependency direction
# ---------------------------------------------------------------------------


class TestDependencyDirection(unittest.TestCase):
    """``values`` imports ``kb_schema`` and the standard library, and nothing else.

    Read out of the AST rather than asserted in prose. The two names that must
    stay out are ``render`` — whose dataclasses this module deliberately
    mirrors rather than reuses — and ``kb_index_lib``, whose parsers are
    ``store.py``'s to call.
    """

    _ALLOWED = frozenset(
        {
            "re",
            "tomllib",
            "collections.abc.Callable",
            "collections.abc.Mapping",
            "collections.abc.Sequence",
            "dataclasses.dataclass",
            "pathlib.Path",
            "kb_tools.kb_schema",
        }
    )

    def _imported(self) -> set[str]:
        tree = ast.parse(_MODULE.read_text(encoding="utf-8"))
        names: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                names.update(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom):
                module = node.module or ""
                names.update(f"{module}.{alias.name}" for alias in node.names)
        return names

    def test_the_import_set_is_the_declared_one(self):
        self.assertEqual(self._imported() - self._ALLOWED, set())

    def test_neither_the_renderer_nor_the_parsers_are_imported(self):
        imported = self._imported()
        for forbidden in ("kb_tools.kb_write.render", "render", "kb_tools.kb_index_lib", "kb_tools.kb_util"):
            self.assertNotIn(forbidden, imported)


if __name__ == "__main__":
    unittest.main()
