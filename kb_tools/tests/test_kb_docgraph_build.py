"""The pipeline end to end, driven as it ships, over sources this file carries.

The subject is the command line, not the functions behind it: a test that
reached into :mod:`kb_tools.kb_docgraph.build` would be a component test whatever
the file is called, and the surface a caller has is the one worth proving.

Every source here is either authored inline or the committed ``fixtures/lamb/``
paper, so this file needs nothing staged and runs in the unit suite. A walk over
a whole multi-volume corpus is the integration suite's
(``kb-testing/tests/test_docgraph_build.py``).
"""

import os
import shutil
import subprocess
import sys
from collections.abc import Sequence
from pathlib import Path

import pytest

from kb_tools.kb_claimgraph import inventory, tree
from kb_tools.kb_docgraph import judge
from kb_tools.kb_docgraph import text as docgraph_text
from kb_tools.kb_docgraph.outline import VolumeTree
from kb_tools.kb_write import ops

_REPO_ROOT = Path(__file__).resolve().parents[2]


def _env() -> dict[str, str]:
    return {"PYTHONPATH": str(_REPO_ROOT), "PATH": os.environ.get("PATH", "")}


def test_the_display_name_survives_forms_no_table_of_handles_would_reach(tmp_path: Path) -> None:
    """The survey's real cases, built through the shipped CLI.

    ``Teo``/``Teorema`` is an author writing in another language, ``lem*`` a
    starred variant, and ``\\protect\\lemmaname`` babel's translation macro —
    three shapes of internal handle that a closed list of handles cannot admit
    and whose display names land on the same thirteen words.
    """
    corpus = tmp_path / "corpus"
    corpus.mkdir()
    (corpus / "volume.tex").write_text(
        "\n".join(
            [
                r"\documentclass{article}",
                r"\newtheorem{Teo}{Teorema}",
                r"\newtheorem{lem*}{\protect\lemmaname}",
                r"\newtheorem{oss}{Osservazione}",
                r"\begin{document}",
                r"\section{Sezione}",
                r"\begin{Teo}[Il risultato]Vale sempre.\end{Teo}",
                r"\begin{lem*}[Un lemma]Vale nell'interno.\end{lem*}",
                r"\begin{oss}Un commento.\end{oss}",
                r"\end{document}",
                "",
            ]
        ),
        encoding="utf-8",
    )
    kb_root = tmp_path / "kb-root"
    subprocess.run(
        [
            sys.executable,
            "-m",
            "kb_tools.kb_docgraph",
            "--source",
            str(corpus / "volume.tex"),
            "--kb-root",
            str(kb_root),
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=True,
        env=_env(),
    )

    sites = inventory.scan(tree.read(kb_root))

    assert {block.environment: block.claim_bearing for block in sites.blocks} == {
        "Teorema": False,
        "Lemma": True,
        "Osservazione": False,
    }
    assert inventory.census(sites).unclassified == {"Osservazione": 1, "Teorema": 1}


def test_a_caption_s_maths_and_a_list_item_s_display_maths_both_reach_the_rendering(tmp_path: Path) -> None:
    """Two spellings the writer reserves for two positions, built through the shipped CLI.

    A caption's maths is point 9's one exception — MathML carrying its own LaTeX
    annotation — and a list item's display maths opens its fence at the item's
    content column rather than at column zero. Both are the reader's problem and
    neither is a drop: check A sees the caption's words and ``check_math`` finds
    every element, or the two checks are right that something looks wrong and
    wrong about why.
    """
    corpus = tmp_path / "corpus"
    corpus.mkdir()
    (corpus / "diagram.pdf").write_bytes(b"%PDF-stub")
    (corpus / "volume.tex").write_text(
        "\n".join(
            [
                r"\documentclass{article}",
                r"\usepackage{graphicx}",
                r"\title{Calibration}",
                r"\begin{document}",
                r"\section{Findings}",
                r"\begin{figure}",
                r"\includegraphics{diagram.pdf}",
                r"\caption{The $8$-qubit extractor (couplings $(3,4)\dots(6,7)$ are elided).}",
                r"\end{figure}",
                r"\begin{enumerate}",
                r"    \item \emph{Non-negativity:}",
                r"    If a subset predicts negative values it cannot be calibrated on that subset:",
                r"    \begin{equation}",
                r"        \label{eq:nonneg}",
                r"        \sum_{i \in I} p_i < 0 \leq \sum_{i \in I} y_i.",
                r"    \end{equation}",
                r"\end{enumerate}",
                r"\end{document}",
                "",
            ]
        ),
        encoding="utf-8",
    )
    kb_root = tmp_path / "kb-root"
    completed = subprocess.run(
        [
            sys.executable,
            "-m",
            "kb_tools.kb_docgraph",
            "--source",
            str(corpus / "volume.tex"),
            "--kb-root",
            str(kb_root),
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        env=_env(),
    )
    report = completed.stdout.splitlines()

    assert [line for line in report if " FAIL " in line] == [], completed.stderr
    assert any("PASS A-ast-to-markdown volume " in line for line in report)
    assert any("PASS math-survives volume 3 maths elements" in line for line in report)
    leaf = (kb_root / "calibration" / "findings.md").read_text(encoding="utf-8")
    # Point 9's verbatim LaTeX, and with it point 7's equation-label recovery:
    # the item indents the fence, so the reader gives the columns back rather
    # than the writer withholding them.
    assert leaf.count("<math ") == 2
    ((info, content),) = docgraph_text.fenced_blocks(leaf)
    assert info == "math"
    assert content.splitlines() == [
        r"\begin{equation}",
        r"        \label{eq:nonneg}",
        r"        \sum_{i \in I} p_i < 0 \leq \sum_{i \in I} y_i.",
        r"\end{equation}",
    ]


def test_a_paper_whose_sections_never_loaded_does_not_build(tmp_path: Path) -> None:
    """The loss neither partition check can see, stopped where the evidence still exists.

    ``\\input{1.introduction}`` names a file pandoc reads as already carrying an
    extension: it never tries ``1.introduction.tex`` beside the source, drops the
    section, warns, and exits 0. A staged arXiv paper of eight such lines built a
    two-document tree of 108 words and passed every gate in this file — the
    content reached no AST, no rendering and no tree, so both checks compared one
    copy of the gap against another.

    The volume is named as well as the files: the source travels to pandoc on
    stdin, so its own complaint has a line number and no filename, and a run
    converting several roots would otherwise report the loss against none of them.
    """
    corpus = tmp_path / "corpus"
    corpus.mkdir()
    (corpus / "1.introduction.tex").write_text("\\section{Introduction}\nThe body.\n", encoding="utf-8")
    (corpus / "volume.tex").write_text(
        "\n".join([r"\documentclass{article}", r"\begin{document}", r"\input{1.introduction}", r"\end{document}", ""]),
        encoding="utf-8",
    )
    kb_root = tmp_path / "kb-root"

    completed = subprocess.run(
        [
            sys.executable,
            "-m",
            "kb_tools.kb_docgraph",
            "--source",
            str(corpus / "volume.tex"),
            "--kb-root",
            str(kb_root),
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        env=_env(),
    )

    assert completed.returncode != 0, completed.stdout
    assert "1.introduction" in completed.stderr and "line 3" in completed.stderr
    assert str(corpus / "volume.tex") in completed.stderr
    assert not kb_root.exists()


def test_a_block_the_author_titled_with_a_citation_reads_as_words(tmp_path: Path) -> None:
    """Point 12's optional argument is rendered LaTeX, so an author's ``\\citet`` titles a block with markup.

    The title is what a person reads — a register heading, an entry in stage D's
    enumeration — so it carries what the span shows. The locator is what the
    write API matches against the document, so it keeps the span itself; the two
    come off one line and only one of them is bytes.
    """
    corpus = tmp_path / "corpus"
    corpus.mkdir()
    (corpus / "volume.tex").write_text(
        "\n".join(
            [
                r"\documentclass{article}",
                r"\newtheorem{theorem}{Theorem}",
                r"\begin{document}",
                r"\section{Order statistics}",
                r"\begin{theorem}[\citet{gibbons2020nonparametric}]The limit is normal.\end{theorem}",
                r"\end{document}",
                "",
            ]
        ),
        encoding="utf-8",
    )
    kb_root = tmp_path / "kb-root"
    subprocess.run(
        [
            sys.executable,
            "-m",
            "kb_tools.kb_docgraph",
            "--source",
            str(corpus / "volume.tex"),
            "--kb-root",
            str(kb_root),
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=True,
        env=_env(),
    )
    documents = tree.read(kb_root)
    (block,) = [site for site in inventory.scan(documents).blocks if site.claim_bearing]

    assert block.title == "(gibbons2020nonparametric)"
    assert block.display is not None and 'data-cites="gibbons2020nonparametric"' in block.display
    assert len(ops.excerpt_lines(documents.documents[block.document].text, block.display)) == 1


def test_judge_accepts_and_recut_raises() -> None:
    """§3's two assertions. ``recut`` has no caller but the reject branch."""
    tree = VolumeTree(
        stem="vol",
        title="V",
        index=None,
        label_paths={},
        header_labels=set(),
        content_tokens=[],
        distinct_levels=1,
        assets={},
        missing_assets=[],
    )

    assert judge.judge(tree) == judge.Verdict(accepted=True)
    with pytest.raises(NotImplementedError):
        judge.recut(tree, judge.Verdict(accepted=False, reason="one wall of text"))


# ---------------------------------------------------------------------------
# A declaration the pre-scan cannot read
#
# Stage 1's quieter degradation: the declaration stays in the source, pandoc
# expands the environment as the author defined it, and the block's content
# reaches the tree with neither partition check going false. The name is what is
# gone, so the build's own line is the only thing that says so.
# ---------------------------------------------------------------------------

#: Legal LaTeX the pre-scan cannot read, authored rather than taken from a paper:
#: the subject is a declaration the scan reports, not any author's. LaTeX's own
#: argument scanner passes over a brace-protected ``]``; this one reads an
#: optional argument by finding the first ``]`` there is, and so stops inside the
#: default value.
_UNREADABLE_DECLARATION = r"\newenvironment{aside}[1][{[source unstated]}]{\begin{quote}\textit{#1}}{\end{quote}}"

#: The one sentence the degraded block still carries into the tree.
_ASIDE_CONTENT = "An aside the author set apart."


def test_a_declaration_the_pre_scan_cannot_read_is_reported_and_costs_only_its_own_name(tmp_path: Path) -> None:
    """The degradation and the line that keeps it from being silent, through the shipped CLI.

    The content arriving is half of it: a build that lost the block outright
    would fail check A, and this row's whole point is that nothing mechanical
    goes false. What the tree loses is the labelled form of point 12 — so the
    absent ``**aside**`` is the cost, asserted here because the report line is
    the only other place it appears.
    """
    corpus = tmp_path / "corpus"
    corpus.mkdir()
    (corpus / "volume.tex").write_text(
        "\n".join(
            [
                r"\documentclass{article}",
                _UNREADABLE_DECLARATION,
                r"\begin{document}",
                r"\section{Head}",
                r"Body words here.",
                rf"\begin{{aside}}{_ASIDE_CONTENT}\end{{aside}}",
                r"\end{document}",
                "",
            ]
        ),
        encoding="utf-8",
    )
    kb_root = tmp_path / "kb-root"
    completed = subprocess.run(
        [
            sys.executable,
            "-m",
            "kb_tools.kb_docgraph",
            "--source",
            str(corpus / "volume.tex"),
            "--kb-root",
            str(kb_root),
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        env=_env(),
    )

    assert completed.returncode == 0, f"stdout:\n{completed.stdout}\nstderr:\n{completed.stderr}"
    assert "FAIL" not in completed.stdout
    reported = [line for line in completed.stdout.splitlines() if " declaration-read " in line]
    assert len(reported) == 1, completed.stdout
    assert reported[0].startswith("[docgraph] FACT declaration-read volume: unread at line(s) 2 — ")
    assert "labelled blockquotes carrying a name" in reported[0]
    # The pre-scan's own warning is where the declaration is quoted as written;
    # the line above carries where to look and not what was there.
    assert _UNREADABLE_DECLARATION in completed.stderr

    written = "\n".join(path.read_text(encoding="utf-8") for path in sorted(kb_root.rglob("*.md")))
    assert _ASIDE_CONTENT in written
    assert "**aside**" not in written


# ---------------------------------------------------------------------------
# One paper, three citation states
#
# `fixtures/lamb/` carries a citing paper and both bibliography forms, so all
# three states a citation can be in are reachable from one source — the third
# needing only a .bib that answers something else, written here. All three are
# read here, because what they exercise is the reader; the resolved one is also
# walked end to end in `test_kb_driver_head.py`.
#
# The staging each needs — which files land beside the source, whether the .bbl
# is \input, and which .bib the build is pointed at — is done here inline. A
# reusable "stage an arbitrary paper as a throwaway consumer" step is wanted and
# does not exist; when it lands, these are its first callers.
# ---------------------------------------------------------------------------

_LAMB = Path(__file__).resolve().parent / "fixtures" / "lamb"
_LAMB_KEY = "nobody2026"


def _stage_lamb(root: Path, *, bbl: bool) -> Path:
    r"""The paper on its own, optionally with its .bbl \input before the end.

    Pandoc does not pick a .bbl up from `\bibliography{...}`, so reaching it at
    all means putting it in the source — which is what an arXiv tarball's own
    build does, and what a staging step would do here.
    """
    root.mkdir(parents=True, exist_ok=True)
    source = (_LAMB / "lamb.tex").read_text(encoding="utf-8")
    if bbl:
        shutil.copy(_LAMB / "lamb.bbl", root / "lamb.bbl")
        source = source.replace("\\end{document}", "\\input{lamb.bbl}\n\\end{document}")
    (root / "lamb.tex").write_text(source, encoding="utf-8")
    return root


#: A bibliography answering a work this paper does not cite. The fixture's own
#: two forms cannot reach the unanswered state — ``lamb.bib`` answers the only
#: key there is — and a .bib carrying something else is the least that can.
_UNANSWERING_BIB = "@misc{someoneelse2020,\n  author = {Other, A.},\n  title = {Something else},\n  year = {2020}\n}\n"


def _stage_bibliographies(corpus: Path) -> None:
    """Both .bib files beside the paper: the one that answers its key and the one that does not."""
    shutil.copy(_LAMB / "lamb.bib", corpus / "lamb.bib")
    (corpus / "unanswering.bib").write_text(_UNANSWERING_BIB, encoding="utf-8")


def _build_lamb(
    corpus: Path, kb_root: Path, *, bibliographies: Sequence[Path] = ()
) -> subprocess.CompletedProcess[str]:
    """The shipped CLI over the staged paper, with however many bibliographies to resolve against."""
    argv = [sys.executable, "-m", "kb_tools.kb_docgraph", "--source", str(corpus / "lamb.tex")]
    for bibliography in bibliographies:
        argv += ["--bibliography", str(bibliography)]
    return subprocess.run(
        [*argv, "--kb-root", str(kb_root)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        env=_env(),
    )


def _lamb_tree_text(kb_root: Path) -> str:
    return "\n".join(path.read_text(encoding="utf-8") for path in kb_root.rglob("*.md"))


@pytest.mark.parametrize("bbl", [False, True], ids=["no-bibliography-at-all", "a-bbl-and-no-bib"])
def test_a_citation_survives_with_no_bibliography_to_resolve_it(tmp_path: Path, bbl: bool) -> None:
    """The two states with no citeproc behind them, and the deletion they used to be.

    Without ``--citeproc`` a ``Cite`` reached the gfm writer as something it had
    nowhere to put, so the citation was simply gone and the build exited clean.
    Now the filter marks it before citeproc would have run, so the key is on the
    page either way — which is the property an arXiv sweep depends on, most
    tarballs shipping a .bbl and no .bib.
    """
    corpus = _stage_lamb(tmp_path / "corpus", bbl=bbl)
    kb_root = tmp_path / "kb-root"
    completed = _build_lamb(corpus, kb_root)

    assert completed.returncode == 0, f"stdout:\n{completed.stdout}\nstderr:\n{completed.stderr}"
    text = _lamb_tree_text(kb_root)
    assert f'data-cites="{_LAMB_KEY}"' in text, "the citation was deleted"
    assert f"({_LAMB_KEY})" in text, "the citation carries no readable key"


@pytest.mark.parametrize(
    "bibliography, state, census_line",
    [
        (
            "lamb.bib",
            inventory.CitationState.RESOLVED,
            {"inline-resolved": 1, "inline-unanswered": 0, "inline-key-only": 0, "reference-list": 1},
        ),
        (
            "unanswering.bib",
            inventory.CitationState.UNANSWERED,
            {"inline-resolved": 0, "inline-unanswered": 1, "inline-key-only": 0, "reference-list": 0},
        ),
        (
            None,
            inventory.CitationState.KEY_ONLY,
            {"inline-resolved": 0, "inline-unanswered": 0, "inline-key-only": 1, "reference-list": 0},
        ),
    ],
    ids=["a-bibliography-that-answers", "a-bibliography-that-does-not", "no-bibliography-at-all"],
)
def test_each_citation_state_is_read_off_what_the_reader_actually_wrote(
    tmp_path: Path, bibliography: str | None, state: inventory.CitationState, census_line: dict[str, int]
) -> None:
    """Point 10's three states, off one paper, against pandoc's own renderings.

    The two that carry no reference list are the pair the discriminator has to
    separate, and the tree gives no help: neither build has a references leaf, so
    "the volume renders no reference list" calls them the same thing. The span
    does separate them, which is why the state is read there.

    ``reference-list`` is the other half of the same reading. One work cited once
    is one citation and one list entry, not two of either.
    """
    corpus = _stage_lamb(tmp_path / "corpus", bbl=False)
    _stage_bibliographies(corpus)
    kb_root = tmp_path / "kb-root"
    completed = _build_lamb(corpus, kb_root, bibliographies=() if bibliography is None else (corpus / bibliography,))

    assert completed.returncode == 0, f"stdout:\n{completed.stdout}\nstderr:\n{completed.stderr}"
    sites = inventory.scan(tree.read(kb_root))

    assert [(citation.key, citation.state) for citation in sites.citations] == [(_LAMB_KEY, state)]
    assert inventory.census(sites).citations == census_line


def test_a_build_handed_every_bibliography_beside_the_paper_resolves_out_of_their_union(tmp_path: Path) -> None:
    """Two ``.bib`` files, one of which answers nothing this paper cites.

    This is the state four of fifty arXiv papers ship and the build used to
    refuse: a template's stray bibliography sitting beside the one the source
    declares. The refusal was reasoning about a choice the reader never required
    — ``--bibliography`` repeats, pandoc merges, and citeproc renders only what
    the source cites — so the extra file is inert rather than a competing
    answer. Both halves are asserted: the citation resolved, and the reference
    list carries the one work rather than everything on offer.
    """
    corpus = _stage_lamb(tmp_path / "corpus", bbl=False)
    _stage_bibliographies(corpus)
    kb_root = tmp_path / "kb-root"

    completed = _build_lamb(corpus, kb_root, bibliographies=(corpus / "lamb.bib", corpus / "unanswering.bib"))

    assert completed.returncode == 0, f"stdout:\n{completed.stdout}\nstderr:\n{completed.stderr}"
    sites = inventory.scan(tree.read(kb_root))
    assert [(citation.key, citation.state) for citation in sites.citations] == [
        (_LAMB_KEY, inventory.CitationState.RESOLVED)
    ]
    assert inventory.census(sites).citations["reference-list"] == 1
    assert "someoneelse2020" not in _lamb_tree_text(kb_root)


def test_an_inlined_bbl_is_read_as_structure_and_not_as_a_claim(tmp_path: Path) -> None:
    """``thebibliography`` reaching pandoc is apparatus, not an author's result block.

    The filter's STRUCTURAL list already carries the name; this is what says so
    against a real one, since a `.bbl` inlined into the source is the first way
    that Div has ever actually arrived.
    """
    corpus = _stage_lamb(tmp_path / "corpus", bbl=True)
    kb_root = tmp_path / "kb-root"
    completed = _build_lamb(corpus, kb_root)

    assert completed.returncode == 0, f"stdout:\n{completed.stdout}\nstderr:\n{completed.stderr}"
    sites = inventory.scan(tree.read(kb_root))

    assert sites.claim_blocks() == (), [block.environment for block in sites.claim_blocks()]
    assert inventory.census(sites).unclassified == {}
