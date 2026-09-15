"""Tests for `dupe_sweep.py` — the one-idea-two-places candidate sweep.

The load-bearing one is :func:`test_the_known_seven_way_duplicate_is_found`. Seven
copies of one sentence lived in seven agent templates until a shared chunk's
**Blockers** field made them redundant and they were deleted; six were exact and
the seventh carried an extra parenthetical. That tree is :data:`KNOWN_ANSWER_REV`,
and a pass that cannot surface a duplicate somebody already found by hand is not
working whatever else it reports. The other tests are what that one cannot see:
the containment metric, the guards that keep clusters from chaining into ropes,
and the extractors' bounds.

Corpus-shaped cases pass a `{path: source}` mapping straight to the extractors
rather than building a tree — the sweep reads its corpus from git, and a
filesystem is not on the path between a source file and a candidate.
"""

import io
import subprocess

import pytest

import dupe_sweep

#: The tree the prose pass has a known answer against. Skipped rather than failed
#: where history does not reach it: a shallow clone is not a defect in the sweep.
KNOWN_ANSWER_REV = "6b1aaa9"
KNOWN_ANSWER_SENTENCE = (
    "If you cannot complete the task as scoped, report immediately rather than proceeding with assumptions."
)
KNOWN_ANSWER_SITES = 7


def has_rev(rev: str) -> bool:
    done = subprocess.run(["git", "cat-file", "-e", f"{rev}^{{commit}}"], cwd=dupe_sweep.REPO_ROOT, capture_output=True)
    return done.returncode == 0


def unit(text: str, *, path: str = "a.md", line: int = 1) -> dupe_sweep.Unit:
    return dupe_sweep.Unit(path, line, "", text, dupe_sweep.prose_tokens(text))


# ── the known answer ─────────────────────────────────────────────────────────


@pytest.mark.skipif(not has_rev(KNOWN_ANSWER_REV), reason=f"{KNOWN_ANSWER_REV} is not in this clone's history")
def test_the_known_seven_way_duplicate_is_found():
    found = dupe_sweep.clusters(dupe_sweep.prose_units(rev=KNOWN_ANSWER_REV), **dupe_sweep.PROSE_DEFAULTS)
    wanted = dupe_sweep.prose_tokens(KNOWN_ANSWER_SENTENCE)
    matching = [cluster for cluster in found if any(member.tokens == wanted for member, _ in cluster.members)]
    assert len(matching) == 1, "the seven copies must be one candidate, not several"
    sites = [member.site for member, _ in matching[0].members]
    assert len(sites) == KNOWN_ANSWER_SITES, sites
    # Six exact copies and one carrying an extra parenthetical — the near copy is
    # the reason containment and not ratio decides.
    assert sum(1 for member, _ in matching[0].members if member.tokens == wanted) == KNOWN_ANSWER_SITES - 1


# ── the metric ───────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "left, right, floor, ceiling",
    [
        ("the same sentence exactly", "the same sentence exactly", 1.0, 1.0),
        # The known near-copy: an inserted parenthetical and one added word.
        (
            KNOWN_ANSWER_SENTENCE,
            "If you cannot complete the task as scoped (missing context, ambiguous requirements, file conflict"
            " risk), report this immediately rather than proceeding with assumptions.",
            0.90,
            1.0,
        ),
        ("a sentence about parsing markdown links", "an unrelated remark concerning venv layout", 0.0, 0.4),
    ],
)
def test_containment_scores_a_restatement_high_and_a_stranger_low(left, right, floor, ceiling):
    score = dupe_sweep.containment(dupe_sweep.prose_tokens(left), dupe_sweep.prose_tokens(right))
    assert floor <= score <= ceiling


def test_containment_is_symmetric():
    long_side = dupe_sweep.prose_tokens("the rule holds at every seat and in every definition without exception")
    short_side = dupe_sweep.prose_tokens("the rule holds at every seat")
    assert dupe_sweep.containment(long_side, short_side) == dupe_sweep.containment(short_side, long_side)


# ── clustering ───────────────────────────────────────────────────────────────


def test_seven_copies_report_as_one_candidate_with_seven_sites():
    copies = [unit(KNOWN_ANSWER_SENTENCE, path=f"t{index}.md") for index in range(7)]
    found = dupe_sweep.clusters(copies, **dupe_sweep.PROSE_DEFAULTS)
    assert len(found) == 1
    assert len(found[0].members) == 7


def test_the_representative_is_the_shortest_statement_of_the_shared_idea():
    short = unit("Declare the files you will modify before you start, and stay inside that set.", path="a.md")
    long = unit(
        "Declare the files you will modify before you start, and stay inside that set, because another"
        " agent may be editing one.",
        path="b.md",
    )
    found = dupe_sweep.clusters([long, short], **dupe_sweep.PROSE_DEFAULTS)
    assert len(found) == 1
    assert found[0].representative.path == "a.md"


def test_a_short_span_inside_a_long_one_does_not_chain_them():
    """`length_ratio`'s job. Containment alone links a fragment to everything that
    quotes it, and the cluster becomes a rope rather than a finding."""
    fragment = unit("never invoke the compiler directly when a target covers it", path="a.md")
    quoting = unit(
        "Use the project's runner targets for every build, test, lint and integration operation, and"
        " never invoke the compiler directly when a target covers it, whichever runner the project has"
        " chosen for itself and however tempting the naked command line looks.",
        path="b.md",
    )
    assert dupe_sweep.containment(fragment.tokens, quoting.tokens) == 1.0
    assert dupe_sweep.clusters([fragment, quoting], **dupe_sweep.PROSE_DEFAULTS) == []


def test_a_span_shorter_than_min_tokens_is_not_compared():
    pair = [unit("Stdlib-first, always.", path=f"t{index}.md") for index in range(2)]
    assert dupe_sweep.clusters(pair, **dupe_sweep.PROSE_DEFAULTS) == []


def test_the_same_units_in_any_order_render_the_same_report():
    units = [unit(KNOWN_ANSWER_SENTENCE, path=f"t{index}.md") for index in range(4)] + [
        unit(
            "A dependency is a permanent maintenance obligation, so justify it before you add one.", path=f"d{index}.md"
        )
        for index in range(3)
    ]

    def report(ordered):
        out = io.StringIO()
        dupe_sweep.render_clusters("t", dupe_sweep.clusters(ordered, **dupe_sweep.PROSE_DEFAULTS), out=out)
        return out.getvalue()

    assert report(units) == report(list(reversed(units)))


def test_the_file_pair_tally_counts_a_pair_once_per_candidate():
    units = [
        unit(KNOWN_ANSWER_SENTENCE, path="a.md"),
        unit(KNOWN_ANSWER_SENTENCE, path="b.md"),
        unit("A dependency is a permanent maintenance obligation, so justify it before you add one.", path="a.md"),
        unit("A dependency is a permanent maintenance obligation, so justify it before adding one.", path="b.md"),
    ]
    found = dupe_sweep.clusters(units, **dupe_sweep.PROSE_DEFAULTS)
    assert dupe_sweep.file_pairs(found) == [(2, "a.md", "b.md")]


# ── prose extraction ─────────────────────────────────────────────────────────


def test_a_chunk_marker_is_not_prose_and_a_code_span_is():
    assert dupe_sweep.prose_tokens('@!parallel-execution variant="platform"!@ Use `NSFileCoordinator` here.') == (
        "use",
        "nsfilecoordinator",
        "here",
    )


def test_a_template_body_starts_after_the_fence_and_the_frontmatter():
    text = "\n".join(
        [
            "+++",
            "[outputs.one]",
            "model = 'high'",
            "+++",
            "---",
            "name: one",
            "description: a description that is prose but not a prompt body",
            "---",
            "",
            "The body proper.",
        ]
    )
    start, body = dupe_sweep.template_body(text)
    assert body.strip() == "The body proper."
    assert text.splitlines()[start - 1 + 1] == "The body proper."


def test_a_template_with_neither_fence_nor_frontmatter_is_body_entire():
    assert dupe_sweep.template_body("Just prose.\n") == (1, "Just prose.")


def test_an_unclosed_fence_leaves_the_file_readable_as_body(caplog):
    start, body = dupe_sweep.template_body("+++\n[outputs.one]\nstill open\n")
    assert start == 1 and "still open" in body
    assert "unclosed" in caplog.text


def test_fenced_code_is_not_prose():
    text = "A sentence.\n```sh\njust install target\n```\nAnother sentence."
    assert [sentence for _, sentence in dupe_sweep.line_sentences(text)] == ["A sentence.", "Another sentence."]


@pytest.mark.parametrize(
    "line, expected",
    [
        (
            "- **Blockers**: what stopped you. And what you need.",
            ["**Blockers**: what stopped you.", "And what you need."],
        ),
        ("### A heading", ["A heading"]),
        ("> a quoted claim", ["a quoted claim"]),
        ("3. an ordered item", ["an ordered item"]),
    ],
)
def test_list_and_heading_markup_is_not_part_of_the_sentence(line, expected):
    assert [sentence for _, sentence in dupe_sweep.line_sentences(line)] == expected


def test_hard_wrapped_prose_is_rejoined_before_it_is_split():
    numbered = [(10, "A rule that spans"), (11, "two physical lines."), (12, ""), (13, "A separate block.")]
    assert list(dupe_sweep.block_sentences(numbered)) == [
        (10, "A rule that spans two physical lines."),
        (13, "A separate block."),
    ]


def test_chunk_bodies_carry_every_variant_and_the_header_line():
    text = "\n".join(
        [
            "# a maintainer note",
            "[chunks.dissent]",
            "text = '''State the concern.'''",
            "",
            "[chunks.parallel-execution.variants]",
            "platform = '''Declare your files.'''",
            "shell = '''Declare your recipes.'''",
        ]
    )
    assert dupe_sweep.chunk_bodies(text) == [
        ("chunks.dissent", 2, "State the concern."),
        ("chunks.parallel-execution.platform", 5, "Declare your files."),
        ("chunks.parallel-execution.shell", 5, "Declare your recipes."),
    ]


# ── python extraction ────────────────────────────────────────────────────────


TWO_SPELLINGS_OF_ONE_LOOP = {
    "one.py": (
        "def totals(rows):\n"
        "    '''A docstring the shape does not see.'''\n"
        "    out = {}\n"
        "    for row in rows:\n"
        "        if row.kind not in out:\n"
        "            out[row.kind] = 0\n"
        "        out[row.kind] += row.weight\n"
        "    return out\n"
    ),
    "two.py": (
        "def tally(entries):\n"
        "    counts = {}\n"
        "    for entry in entries:\n"
        "        if entry.sort not in counts:\n"
        "            counts[entry.sort] = 0\n"
        "        counts[entry.sort] += entry.size\n"
        "    return counts\n"
    ),
}


def test_two_spellings_of_one_loop_are_one_candidate():
    found = dupe_sweep.clusters(
        dupe_sweep.shape_units(TWO_SPELLINGS_OF_ONE_LOOP), **{**dupe_sweep.CODE_DEFAULTS, "min_tokens": 10}
    )
    assert [member.site for member, _ in found[0].members] == ["one.py:1 totals()", "two.py:1 tally()"]


def test_unlike_functions_are_not_a_candidate():
    files = {
        "one.py": TWO_SPELLINGS_OF_ONE_LOOP["one.py"],
        "two.py": "def shout(text):\n    return text.upper()\n",
    }
    assert dupe_sweep.clusters(dupe_sweep.shape_units(files), **{**dupe_sweep.CODE_DEFAULTS, "min_tokens": 10}) == []


def test_a_method_is_a_unit_and_a_nested_function_is_not():
    files = {
        "one.py": (
            "class Store:\n"
            "    def put(self, key):\n"
            "        def inner(value):\n"
            "            return value\n"
            "        return inner(key)\n"
        )
    }
    assert [unit.label for unit in dupe_sweep.shape_units(files)] == ["Store.put()"]


def test_a_docstring_and_a_comment_both_state_rules():
    files = {"one.py": '"""A module rule.\n\nA second sentence.\n"""\n\n# A commented rule.\nx = 1\n'}
    units = dupe_sweep.doc_units(files)
    assert [(unit.label, unit.text) for unit in units] == [
        ("module docstring", "A module rule."),
        ("module docstring", "A second sentence."),
        ("comment", "A commented rule."),
    ]


def test_a_module_that_does_not_parse_is_reported_and_skipped(caplog):
    assert dupe_sweep.shape_units({"broken.py": "def (:\n"}) == []
    assert "does not parse" in caplog.text


@pytest.mark.parametrize(
    "source, expected",
    [
        ("WIDTH = 72\n", [("WIDTH", "72")]),
        ("TAG: str = 'survey'\n", [("TAG", "survey".join("''"))]),
        ("KINDS = ('clm', 'exp')\n", [("KINDS", "('clm', 'exp')")]),
        ("ENABLED = True\nEMPTY = ''\nZERO = 0\n", []),  # trivia
        ("VALUE = compute()\n", []),  # not a literal
        ("def f():\n    INNER = 4096\n", []),  # not module level
    ],
)
def test_module_level_literals_are_collected_and_trivia_is_not(source, expected):
    found = dupe_sweep.constants({"one.py": source})
    assert [(constant.name, constant.value) for constant in found] == expected


def test_one_name_in_two_modules_is_a_candidate():
    found = dupe_sweep.constants({"one.py": "EXCERPT_MAX_CHARS = 240\n", "two.py": "EXCERPT_MAX_CHARS = 240\n"})
    (why, sites), *rest = dupe_sweep.constant_candidates(found)
    assert why == "one name bound in 2 modules: EXCERPT_MAX_CHARS"
    assert [site.path for site in sites] == ["one.py", "two.py"]
    assert rest == [], "the same sites must not be reported again as a repeated value"


def test_two_unrelated_uses_of_a_small_number_are_not_a_candidate():
    found = dupe_sweep.constants({"one.py": "EXIT_USAGE = 2\n", "two.py": "EDGE_STROKE_WIDTH = 2\n"})
    assert dupe_sweep.constant_candidates(found) == []


def test_one_distinctive_value_under_similar_names_is_a_candidate():
    found = dupe_sweep.constants(
        {"one.py": "DEFAULT_SILENCE_SECONDS = 600\n", "two.py": "DEFAULT_SILENCE_SECONDS_LIMIT = 600\n"}
    )
    assert [why for why, _ in dupe_sweep.constant_candidates(found)] == [
        "one value in 2 modules under similar names: 600"
    ]


# ── the command line ─────────────────────────────────────────────────────────


def test_a_pass_that_finds_candidates_still_exits_zero():
    """Candidates are not verdicts: a nonzero exit would be the judgment the sweep
    refuses to make, and would put it in the way of every checkpoint it runs at."""
    out = io.StringIO()
    assert dupe_sweep.main(["prose"], out=out) == 0
    assert "candidates, not verdicts" in out.getvalue()


def test_an_unreadable_revision_exits_two_rather_than_reporting_a_clean_tree():
    out = io.StringIO()
    assert dupe_sweep.main(["prose", "--rev", "no-such-revision"], out=out) == 2
    assert out.getvalue() == ""


def test_an_unknown_pass_is_refused():
    with pytest.raises(SystemExit):
        dupe_sweep.main(["structure"])
