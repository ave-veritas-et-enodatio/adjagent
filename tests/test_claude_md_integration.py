"""`gen-defs.py install-claude-md`: the four cases, and what each one writes.

The subject is a write into space its operator owns, so every case asserts BOTH
halves — what landed and what was left alone. The bail case is the sharpest of
them: an integration that cannot merge must leave the live file byte-identical
and put its copy beside it under a name an editor still reads as Markdown.

Nothing here reads or writes `~/.claude`. Each case builds its own git
repository under `tmp_path`, publishes a short history of a baseline into it,
and points the verb at a live file in another directory — which is what
`--source` is for. The functions under test take paths and nothing else, so a
case can be driven either through the module or through the CLI; the four
end-to-end cases go through the CLI, because that is the surface `just
install-claude-md` invokes.

The script lives at the repo root under a hyphenated name, so it is loaded here
by path rather than imported.
"""

import subprocess
import sys
from importlib import util
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parent.parent
_spec = util.spec_from_file_location("gen_defs", _REPO_ROOT / "gen-defs.py")
gen_defs = util.module_from_spec(_spec)
_spec.loader.exec_module(gen_defs)


# --- the published history each case is measured against --------------------

#: Three published revisions of a baseline, oldest first. They differ the way
#: the real one does between releases: a section gains a paragraph, a section
#: is added, wording inside a block is rewritten.
V1 = """# Cross-project working preferences

## Scratch space

All scratch goes under the project root.

## Planning

Read the primary sources before presenting a plan.
"""

V2 = """# Cross-project working preferences

## Scratch space

All scratch goes under the project root. If it does not exist, stop and say so.

## Planning

Read the primary sources before presenting a plan.
"""

V3 = """# Cross-project working preferences

## Scratch space

All scratch goes under the project root. If it does not exist, stop and say so.

## Planning

Read the primary sources before presenting a plan.

## Communication style

Minimal flattery. Focused and concise.
"""

#: A file about the same subject, written by someone who never had ours.
UNRELATED = """# My own notes

## Editor

Two spaces, no tabs, and never reformat a file I did not touch.

## Reviews

Ask before rewriting a test.
"""


def _git(repo: Path, *args: str) -> None:
    subprocess.run(
        ["git", "-C", str(repo), "-c", "user.name=t", "-c", "user.email=t@invalid", *args],
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )


@pytest.fixture
def published(tmp_path: Path) -> Path:
    """The published baseline, with V1, V2 and V3 committed in that order —
    and renamed mid-history, as the real one was, so `--follow` is exercised
    rather than assumed."""
    repo = tmp_path / "repo"
    (repo / "user-config").mkdir(parents=True)
    _git(repo, "init", "-q")

    old = repo / "user-config" / "CLAUDE.md"
    old.write_text(V1, encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "v1")

    source = repo / "user-config" / "INSTALLED_CLAUDE.md"
    old.rename(source)
    source.write_text(V2, encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "v2 under the new name")

    source.write_text(V3, encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "v3")
    return source


@pytest.fixture
def live(tmp_path: Path) -> Path:
    """Where an operator's own CLAUDE.md sits — never under the published
    repository, so a stray write into one is visible in the other."""
    home = tmp_path / "home" / ".claude"
    home.mkdir(parents=True)
    return home / "CLAUDE.md"


def run_verb(published: Path, live: Path) -> subprocess.CompletedProcess:
    """The verb as `just install-claude-md` invokes it."""
    return subprocess.run(
        [
            sys.executable,
            str(_REPO_ROOT / "gen-defs.py"),
            "install-claude-md",
            str(live),
            "--source",
            str(published),
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
    )


def beside(live: Path) -> list[str]:
    """Every file the live file's directory holds, so a case can assert what
    was NOT written — a backup, a partial merge, a marker file."""
    return sorted(path.name for path in live.parent.iterdir())


# --- case 1: no shared ancestry ---------------------------------------------


def test_an_unrelated_file_is_appended_to_rather_than_merged(published: Path, live: Path) -> None:
    """Hand-written and never integrated from ours: there is nothing to align,
    so their file keeps every line and ours arrives below it."""
    live.write_text(UNRELATED, encoding="utf-8")

    done = run_verb(published, live)

    assert done.returncode == 0, done.stderr
    assert "no shared ancestry" in done.stdout
    merged = live.read_text(encoding="utf-8")
    # Theirs entire, ours below it, and our H1 left behind: one title, theirs.
    assert merged.startswith(UNRELATED.rstrip("\n"))
    assert "## Communication style" in merged
    assert merged.count("# My own notes") == 1
    assert "# Cross-project working preferences" not in merged
    assert beside(live) == ["CLAUDE.md", "backup.CLAUDE.md"]
    # The concat case rewrites the live file, so it backs up too — the
    # pre-merge bytes, exactly.
    assert (live.parent / "backup.CLAUDE.md").read_text(encoding="utf-8") == UNRELATED


# --- case 2: an exact match to a published revision -------------------------


def test_an_unmodified_published_revision_takes_the_update_whole(published: Path, live: Path) -> None:
    live.write_text(V1, encoding="utf-8")

    done = run_verb(published, live)

    assert done.returncode == 0, done.stderr
    assert "unmodified published revision" in done.stdout
    assert live.read_text(encoding="utf-8") == V3
    assert beside(live) == ["CLAUDE.md", "backup.CLAUDE.md"]
    # The whole-update case rewrites the live file too, so it backs up: the
    # pre-merge bytes, exactly.
    assert (live.parent / "backup.CLAUDE.md").read_text(encoding="utf-8") == V1


def test_a_live_file_matching_the_baseline_is_a_no_op(published: Path, live: Path) -> None:
    live.write_text(V3, encoding="utf-8")

    done = run_verb(published, live)

    assert done.returncode == 0, done.stderr
    assert "already matches" in done.stdout
    assert live.read_text(encoding="utf-8") == V3
    assert beside(live) == ["CLAUDE.md"]


def test_a_missing_live_file_is_installed_whole(published: Path, live: Path) -> None:
    done = run_verb(published, live)

    assert done.returncode == 0, done.stderr
    assert live.read_text(encoding="utf-8") == V3


# --- case 3: a clear base plus local edits ----------------------------------

#: V2 plus two local edits: a section of the operator's own, and a whole block
#: replaced inside a section the published baseline does not touch.
LOCAL = """# Cross-project working preferences

## Scratch space

All scratch goes under the project root. If it does not exist, stop and say so.

## Sandbox discipline

Agents run unprivileged. This is machine-local and is never published.

## Planning

Read the primary sources — actual current files, not guesses — before presenting a plan.
"""


def test_local_edits_survive_the_update_that_lands_around_them(published: Path, live: Path) -> None:
    """The merge case: our new section arrives, their own section and their
    rewritten block both stay — nothing of the operator's is dropped, though
    the live file's bytes still change, and a bytes change is what earns a
    backup."""
    live.write_text(LOCAL, encoding="utf-8")

    done = run_verb(published, live)

    assert done.returncode == 0, done.stderr
    assert "local edits kept" in done.stdout
    merged = live.read_text(encoding="utf-8")
    assert "## Sandbox discipline" in merged
    assert "actual current files, not guesses" in merged
    assert "## Communication style" in merged
    assert "Minimal flattery. Focused and concise." in merged
    assert beside(live) == ["CLAUDE.md", "backup.CLAUDE.md"]
    assert (live.parent / "backup.CLAUDE.md").read_text(encoding="utf-8") == LOCAL


def test_a_second_run_leaves_the_backup_from_the_first_run_untouched(published: Path, live: Path) -> None:
    """A run that changes nothing must not disturb a backup an earlier run
    wrote — checked by content and mtime, not merely by the file existing."""
    live.write_text(LOCAL, encoding="utf-8")
    backup = live.parent / "backup.CLAUDE.md"

    run_verb(published, live)
    written, mtime = backup.read_text(encoding="utf-8"), backup.stat().st_mtime_ns

    run_verb(published, live)

    assert backup.read_text(encoding="utf-8") == written == LOCAL
    assert backup.stat().st_mtime_ns == mtime


def test_the_merge_reports_which_sections_it_found_where(published: Path, live: Path) -> None:
    """Section granularity is the report: names an operator reads, not a diff
    they would have to integrate from."""
    live.write_text(LOCAL, encoding="utf-8")

    report = run_verb(published, live).stdout

    assert "sections new in the published baseline: Communication style" in report
    assert "sections in both, differing: Planning" in report
    assert "sections yours alone: Sandbox discipline" in report
    assert "sections identical: (preamble), Scratch space" in report
    # The base is named, with the era it comes from.
    assert "base:" in report and "lines from yours" in report


def test_the_merged_file_gains_no_line_neither_side_wrote(published: Path, live: Path) -> None:
    """No marker, no delimiter, no conflict furniture: CLAUDE.md is read by a
    model, so the merge may introduce nothing at all."""
    live.write_text(LOCAL, encoding="utf-8")

    run_verb(published, live)

    known = set(LOCAL.splitlines()) | set(V3.splitlines())
    assert [line for line in live.read_text(encoding="utf-8").splitlines() if line not in known] == []


# --- case 4: anything else --------------------------------------------------

#: V2 plus a section of the operator's own that lands exactly where the update
#: adds one of the same name, with different text. Every candidate base has the
#: two sides writing a different block at one position, which is the conflict no
#: choice of base resolves.
CONFLICTED = V2 + """
## Communication style

Answer in one paragraph. Never open with a compliment.
"""


def test_a_file_no_base_merges_against_is_left_alone(published: Path, live: Path) -> None:
    """The bail path. Nothing is written but the incoming copy — no partial
    merge, no backup, no touch of the live file — and the exit says so."""
    live.write_text(CONFLICTED, encoding="utf-8")

    done = run_verb(published, live)

    assert done.returncode == 1, done.stdout
    assert "no recovered base merges" in done.stdout
    assert live.read_text(encoding="utf-8") == CONFLICTED
    assert beside(live) == ["CLAUDE.md", "incoming.CLAUDE.md"]
    # Extension last, so an editor still opens it as Markdown, and it is our
    # published text verbatim.
    assert (live.parent / "incoming.CLAUDE.md").read_text(encoding="utf-8") == V3


def test_no_history_bails_rather_than_merging_against_a_guess(tmp_path: Path, live: Path) -> None:
    """A source outside any repository recovers no base at all. There is
    nothing to merge against, and inventing one is what this path refuses."""
    loose = tmp_path / "loose" / "INSTALLED_CLAUDE.md"
    loose.parent.mkdir()
    loose.write_text(V3, encoding="utf-8")
    live.write_text(LOCAL, encoding="utf-8")

    done = run_verb(loose, live)

    assert done.returncode == 1, done.stdout
    assert "no earlier published revision could be read from git history" in done.stdout
    assert live.read_text(encoding="utf-8") == LOCAL
    assert beside(live) == ["CLAUDE.md", "incoming.CLAUDE.md"]


# --- their bytes, and the seam ----------------------------------------------

#: LOCAL, with the operator's own spacing inside the section that is theirs
#: alone: a whitespace-only line between two of its paragraphs, and a doubled
#: blank line closing it. Nothing published touches that section, so every byte
#: of it has to come back.
SPACED = """# Cross-project working preferences

## Scratch space

All scratch goes under the project root. If it does not exist, stop and say so.

## Sandbox discipline

Agents run unprivileged.
<spaces>
This is machine-local and is never published.


## Planning

Read the primary sources — actual current files, not guesses — before presenting a plan.
""".replace(
    # Spelled rather than typed: a line of nothing but spaces survives neither
    # an editor nor a reader's eye when it is written as one.
    "<spaces>",
    "   ",
)

#: V3 below its own H1: what the concat case appends. Ours to spell — the bytes
#: before it in the result are theirs.
OURS_BELOW_H1 = V3.partition("\n")[2].lstrip("\n")


def test_the_operators_own_spacing_survives_the_merge(published: Path, live: Path) -> None:
    """Write authority ends at the seam. A region the merge did not write into
    comes back byte-identical — whitespace-only line, doubled blank and all —
    and the published section arrives after their file rather than in place of
    its spacing."""
    live.write_text(SPACED, encoding="utf-8")

    run_verb(published, live)

    merged = live.read_text(encoding="utf-8")
    theirs = SPACED[SPACED.index("## Sandbox discipline") : SPACED.index("## Planning")]
    assert theirs in merged
    assert merged.startswith(SPACED)
    # And past their last byte, the seam: one blank line, then ours.
    assert merged == SPACED + "\n## Communication style\n\nMinimal flattery. Focused and concise.\n"


@pytest.mark.parametrize(
    "tail, separator",
    [("", "\n\n"), ("\n", "\n\n"), ("\n\n", "\n\n"), ("\n\n\n", "\n\n\n")],
    ids=["none", "one", "two", "three"],
)
def test_the_seam_tops_up_what_is_missing_and_takes_nothing_away(
    published: Path, live: Path, tail: str, separator: str
) -> None:
    """The joint is ours only from their last byte onward. A file already
    separating its content from ours is added to not at all; one ending short
    gets the shortfall and no more; and trailing newlines beyond the minimum
    are theirs and stay."""
    body = UNRELATED.rstrip("\n")
    live.write_text(body + tail, encoding="utf-8")

    run_verb(published, live)

    assert live.read_text(encoding="utf-8") == body + separator + OURS_BELOW_H1


# --- every case converges: a second run finds nothing left to do ------------

#: The file that motivated the two-run property: a couple of lines somebody
#: wrote by hand, sharing nothing with ours, spaced their own way.
HAND_WRITTEN = "# Notes\n   \n\nNever reformat a file I did not touch.\n\n\nAsk before rewriting a test.\n"


@pytest.mark.parametrize(
    "headline, initial",
    [
        ("no live file", None),
        ("no shared ancestry", UNRELATED),
        ("no shared ancestry", HAND_WRITTEN),
        ("unmodified published revision", V1),
        ("local edits kept", LOCAL),
        ("local edits kept", SPACED),
        ("no recovered base merges", CONFLICTED),
    ],
    ids=["fresh", "unrelated", "unrelated-hand-written", "published", "merged", "merged-spaced", "bail"],
)
def test_a_second_run_leaves_the_live_file_byte_identical(
    published: Path, live: Path, headline: str, initial: str | None
) -> None:
    """Running the verb twice is running it once — whichever case the first run
    took, and whichever the second reclassifies into.

    The live file is what the property is over: it is what a session loads.
    `incoming.CLAUDE.md` has none of that weight, so the bail case rewriting it
    identically is not a second run doing something. The first run's case is
    asserted too, because idempotence over a case nothing reaches would hold
    vacuously.
    """
    if initial is not None:
        live.write_text(initial, encoding="utf-8")

    first = run_verb(published, live)
    settled, alongside = live.read_text(encoding="utf-8"), beside(live)
    second = run_verb(published, live)

    assert headline in first.stdout
    assert second.returncode == first.returncode, second.stdout
    assert live.read_text(encoding="utf-8") == settled
    assert beside(live) == alongside


# --- the pieces the cases rest on -------------------------------------------


@pytest.mark.parametrize(
    "text",
    [
        "",
        "one line, no newline",
        "a\n\nb\n",
        "\n\n# leading blanks\n\ntail\n\n\n",
        "spaced\n   \ntheir way\n\n\n",
        V3,
        LOCAL,
        SPACED,
    ],
)
def test_a_documents_own_blocks_stitch_back_into_the_document(text: str) -> None:
    """The split is a partition, which is what makes byte-identity structural
    rather than something to check for: every byte of the document lands in
    exactly one block, so blocks carried through a merge unchanged put the
    document back exactly — leading blanks, whitespace-only lines, trailing runs
    and all. What a merge compares is the content lines, and those survive in
    order too."""
    units = gen_defs.blocks(text)

    assert gen_defs.stitch(units) == text
    assert [line for unit in units for line in unit.text.splitlines()] == [
        line for line in text.splitlines() if line.strip()
    ]


def test_a_base_that_conflicts_is_rejected_so_the_next_candidate_is_tried() -> None:
    """Base selection is validated by the merge rather than trusted: a base
    both sides changed differently returns nothing to write."""
    base = gen_defs.blocks("a\n\nb\n\nc\n")

    assert (
        gen_defs.merge_blocks(base, gen_defs.blocks("a\n\nB ours\n\nc\n"), gen_defs.blocks("a\n\nB theirs\n\nc\n"))
        is None
    )
    # The same edit from both sides is agreement, not a conflict — and between
    # two spellings of one paragraph, the live file's is the one that lands.
    agreed = gen_defs.merge_blocks(base, gen_defs.blocks("a\n\nB\n\n\nc\n"), gen_defs.blocks("a\n\nB\n\nc\n"))
    assert gen_defs.stitch(agreed) == "a\n\nB\n\n\nc\n"
    # Edits to different paragraphs are independent and both land.
    both = gen_defs.merge_blocks(base, gen_defs.blocks("a\n\nb\n\nC ours\n"), gen_defs.blocks("A theirs\n\nb\n\nc\n"))
    assert gen_defs.stitch(both) == "A theirs\n\nb\n\nC ours\n"


def test_an_insertion_already_present_where_it_would_land_is_not_made_twice() -> None:
    """The live file already carrying what the published side inserts is the
    second run of a merge that landed: against the older base still on offer,
    the operator's edit and the applied insertion are one changed span, so the
    insertion is no longer recognisable as already there. It is dropped either
    way round — the copy the live file holds may precede the insertion point or
    follow it — and what survives is the operator's own blocks."""
    base = gen_defs.blocks("a\n\nb\n")

    # Published appends; the live file holds the append fused with its own edit
    # of the block before it.
    tail = gen_defs.merge_blocks(base, gen_defs.blocks("a\n\nB ours\n\nc\n"), gen_defs.blocks("a\n\nb\n\nc\n"))
    assert gen_defs.stitch(tail) == "a\n\nB ours\n\nc\n"

    # The mirror image: published prepends, and the copy already on disk follows
    # the insertion point rather than preceding it.
    head = gen_defs.merge_blocks(base, gen_defs.blocks("c\n\nA ours\n\nb\n"), gen_defs.blocks("c\n\na\n\nb\n"))
    assert gen_defs.stitch(head) == "c\n\nA ours\n\nb\n"


def test_the_nearest_revision_wins_and_a_conflicting_one_yields_to_the_next(published: Path) -> None:
    """Candidates are ordered by distance from the live file, and the pick is
    the nearest one that actually merges."""
    revisions = gen_defs.baseline_revisions(published)
    assert [revision.text for revision in revisions] == [V3, V2, V1]

    plan = gen_defs.plan_integration(published=V3, live=LOCAL, revisions=revisions)

    assert plan.case == "merged"
    # V3 is nearer to LOCAL by line count than V1, but it is V2 that LOCAL was
    # built from, and V2 is the one that merges.
    assert plan.base.text == V2
