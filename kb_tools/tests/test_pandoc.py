"""The pandoc seam, exercised against the real binary.

There is no mock here and there should not be: every property this module is
responsible for is a property of an external program's behaviour — what ``-s``
buys, what a missing ``--citeproc`` costs, where ``\\input`` resolves from — and
a double would assert our belief about pandoc rather than pandoc. The binary is
assumed present (``kb_tools/SPEC.md``, Corpus Invariants); where it is not, these
fail loudly, which is the honest report for a toolchain that cannot read a
source without it.

The two tests that do not touch pandoc are the ones about a pandoc that is not
there: both point :data:`kb_tools.pandoc.BINARY` at something else on purpose.
"""

import json
import logging
import re
import sys
from pathlib import Path

import pytest

from kb_tools import kb_docgraph, pandoc

_VOLUME = "\n".join(
    [
        r"\documentclass{article}",
        r"\title{Zombie Dynamics}",
        r"\author{A. Author}",
        r"\begin{document}",
        r"\maketitle",
        r"\begin{abstract}",
        r"The abstract states the claim.",
        r"\end{abstract}",
        r"\section{Opening}",
        r"A sentence citing \cite{cohen2001} and more.",
        r"\end{document}",
        "",
    ]
)

_BIBLIOGRAPHY = "\n".join(
    [
        "@article{cohen2001,",
        "  author = {Cohen, Jane and Others, Many},",
        "  title = {On Things},",
        "  journal = {J. Things},",
        "  year = {2001}",
        "}",
        "",
    ]
)


@pytest.fixture
def bibliography(tmp_path: Path) -> Path:
    path = tmp_path / "refs.bib"
    path.write_text(_BIBLIOGRAPHY, encoding="utf-8")
    return path


def _filter(path: Path, body: str) -> Path:
    path.write_text(body, encoding="utf-8")
    return path


def test_the_ast_is_pandocs_own_json(bibliography: Path) -> None:
    """Word-granular, and shaped the way every AST walk downstream expects."""
    document = pandoc.to_ast(_VOLUME, bibliographies=[bibliography])

    assert set(document) >= {"pandoc-api-version", "meta", "blocks"}
    assert [block["c"][0] for block in document["blocks"] if block["t"] == "Header"] == [1]

    words = json.dumps(document["blocks"])
    assert '{"t": "Str", "c": "sentence"}' in words and '{"t": "Space"}' in words


def test_the_metadata_channel_reaches_the_markdown(bibliography: Path) -> None:
    """What ``-s`` buys, stated as the content it is the only route for.

    Title and abstract are content the build places (``kb_tools/SPEC.md``, Document-Tree
    Contract point 13); without ``-s`` they leave no trace on any channel, so this failing
    is what a silently metadata-less build would look like.
    """
    markdown = pandoc.to_markdown(_VOLUME, bibliographies=[bibliography])

    assert "title: Zombie Dynamics" in markdown
    assert "The abstract states the claim." in markdown
    assert "# Opening" in markdown


@pytest.mark.parametrize("convert", [pandoc.to_ast, pandoc.to_markdown], ids=["ast", "markdown"])
def test_a_filter_reaches_both_conversion_directions(convert, tmp_path: Path, bibliography: Path) -> None:
    """Filters are the pipeline's only handle on the parsed document, so both routes must honour them."""
    shout = _filter(tmp_path / "shout.lua", "function Str(el) return pandoc.Str(el.text:upper()) end\n")
    urgent = _filter(tmp_path / "urgent.lua", 'function Str(el) return pandoc.Str(el.text .. "!") end\n')

    produced = convert(_VOLUME, bibliographies=[bibliography], filters=[shout, urgent])

    assert "SENTENCE!" in json.dumps(produced)


def test_a_citation_resolves_only_when_a_bibliography_is_passed(bibliography: Path) -> None:
    """The silent drop the ``bibliographies`` parameter has no default for.

    Both calls exit 0. One renders the work; the other renders a sentence with a
    hole in it and says nothing about it anywhere.
    """
    with_bibliography = pandoc.to_markdown(_VOLUME, bibliographies=[bibliography])
    without = pandoc.to_markdown(_VOLUME, bibliographies=())

    assert "Cohen" in with_bibliography
    assert "Cohen" not in without and "cohen2001" not in without


def test_several_bibliographies_merge_and_only_cited_entries_render(tmp_path: Path, bibliography: Path) -> None:
    """The fact the single-file model was built against: the flag repeats and pandoc merges.

    Two files, one of them carrying nothing this source cites. The citation
    resolves out of the union, and the uncited entry reaches neither the text
    nor the reference list — which is what makes passing every ``.bib`` beside a
    corpus cost nothing, rather than a guess between them.
    """
    unrelated = tmp_path / "unrelated.bib"
    unrelated.write_text(
        "@article{nobodycites2019,\n  author = {Unread, U.},\n  title = {Nothing Cites This},\n"
        "  journal = {J. Nothing},\n  year = {2019}\n}\n",
        encoding="utf-8",
    )

    merged = pandoc.to_markdown(_VOLUME, bibliographies=[unrelated, bibliography])

    assert "Cohen" in merged
    assert "Unread" not in merged and "nobodycites2019" not in merged


def test_an_unresolvable_key_is_visible_on_both_channels(tmp_path: Path, bibliography: Path, caplog) -> None:
    """A key with no entry renders as itself and warns — the plan's standard for an unresolvable citation.

    It is also what holds the unloadable-include scan narrow: this run lost
    nothing and wrote to stderr, so a scan reading the channel rather than
    pandoc's own sentence would stop the build here.
    """
    caplog.set_level(logging.WARNING)
    source = _VOLUME.replace("cohen2001", "nosuchkey")

    markdown = pandoc.to_markdown(source, bibliographies=[bibliography])

    assert "nosuchkey" in markdown
    assert any("nosuchkey" in record.getMessage() for record in caplog.records), caplog.text


def test_an_included_file_arrives_with_the_working_directory(tmp_path: Path) -> None:
    """``\\input`` resolves against the process's directory, so a volume root needs its own.

    The failure mode is the reason this is a test rather than a docstring: with
    the wrong directory pandoc exits 0 and omits the part. It is a stop now, and
    the two halves are asserted together — the same document read from the right
    place carries the part, and read from anywhere else raises rather than
    quietly shrinking.
    """
    (tmp_path / "part.tex").write_text("\\section{Appendix}\nIncluded body text.\n", encoding="utf-8")
    root = "\\documentclass{article}\n\\begin{document}\nRoot text.\n\\input{part}\n\\end{document}\n"

    included = pandoc.to_markdown(root, bibliographies=(), working_directory=tmp_path)

    assert "Included body text." in included
    with pytest.raises(pandoc.PandocIncludeError):
        pandoc.to_markdown(root, bibliographies=())


@pytest.mark.parametrize("convert", [pandoc.to_ast, pandoc.to_markdown], ids=["ast", "markdown"])
def test_an_include_that_did_not_load_is_a_stop_and_names_what_was_lost(convert, tmp_path: Path) -> None:
    """Exit 0, a warning, and a section that is in no artifact to compare against another.

    ``\\input{1.introduction}`` is the measured shape: pandoc reads a target
    carrying a dot as one that already has an extension, so it never tries
    ``1.introduction.tex`` sitting beside the source. Eight such lines built a
    two-document tree of 108 words out of an eight-section paper, and every
    check downstream passed — the content entered no AST, no rendering and no
    tree, so each comparison had the same hole on both sides.

    Both directions, because stage 2 zips them: an AST that stopped and a
    rendering that did not would be two readings of two different documents.
    """
    (tmp_path / "1.introduction.tex").write_text("\\section{Introduction}\nThe body.\n", encoding="utf-8")
    root = "\n".join(
        [
            r"\documentclass{article}",
            r"\begin{document}",
            r"\input{1.introduction}",
            r"\input{2.method}",
            r"\end{document}",
            "",
        ]
    )

    with pytest.raises(pandoc.PandocIncludeError) as raised:
        convert(root, bibliographies=(), working_directory=tmp_path)

    message = str(raised.value)
    assert "1.introduction" in message and "2.method" in message
    assert "line 3" in message and "line 4" in message


@pytest.mark.parametrize(
    "flag", [pandoc.CITATION_KEYS_ONLY_FLAG, pandoc.THEOREM_NAMES_FLAG], ids=["citation-keys-only", "theorem-names"]
)
def test_every_metadata_key_this_module_sets_is_the_one_the_filter_reads(flag: str) -> None:
    """One fact crossing a process boundary, and the two sides cannot import each other.

    A divergence here is silent in both directions: pandoc accepts a metadata key
    nobody reads, and the filter reads a key nobody sets and finds ``nil``. The
    build stays green and the effect the flag exists for simply does not happen —
    for the theorem names that is every label line quietly back to its internal
    handle, which is the defect the mapping was added to end.
    """
    filter_source = (Path(kb_docgraph.__file__).resolve().parent / "authored_blocks.lua").read_text(encoding="utf-8")

    assert f'"{flag}"' in filter_source


def test_a_declared_display_name_replaces_the_environments_own(tmp_path: Path) -> None:
    """The mapping's whole job, across the transport that carries it.

    JSON rather than a delimited list because these are the real shapes: an
    internal handle carrying a ``*``, and a display name carrying a space.
    """
    source = "\n".join(
        [
            r"\documentclass{article}",
            r"\begin{document}",
            r"\begin{claimbox}Stated.\end{claimbox}",
            r"\begin{lem*}Also stated.\end{lem*}",
            r"\begin{undeclared}Nobody named this twice.\end{undeclared}",
            r"\end{document}",
            "",
        ]
    )
    filter_path = Path(kb_docgraph.__file__).resolve().parent / "authored_blocks.lua"

    markdown = pandoc.to_markdown(
        source,
        bibliographies=(),
        filters=[filter_path],
        theorem_names={"claimbox": "Result", "lem*": "Test example"},
    )

    assert "> **Result**" in markdown
    assert "> **Test example**" in markdown
    assert "> **undeclared**" in markdown
    assert pandoc.THEOREM_NAMES_FLAG not in markdown


def test_the_round_trip_renders_the_document_it_parsed(bibliography: Path) -> None:
    """``from_ast``'s direction, proven on the one document shape ``-f json`` accepts."""
    rendered = pandoc.from_ast(pandoc.to_ast(_VOLUME, bibliographies=[bibliography]))

    assert "# Opening" in rendered and "Cohen" in rendered


def test_the_version_probe_reports_the_binarys_own_number() -> None:
    """Recorded in build output, so it has to be the number and not the banner line."""
    assert re.fullmatch(r"\d+(\.\d+)+", pandoc.version()), pandoc.version()


def test_a_version_line_that_is_not_pandocs_is_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    """The probe's teeth: a program that answers ``--version`` with something else is not silently believed."""
    monkeypatch.setattr(pandoc, "BINARY", sys.executable)

    with pytest.raises(pandoc.PandocError, match="unrecognized"):
        pandoc.version()


def test_an_absent_binary_names_itself_and_the_remedy(monkeypatch: pytest.MonkeyPatch) -> None:
    """Not the bare ``FileNotFoundError`` subprocess raises, which names a path and no action."""
    monkeypatch.setattr(pandoc, "BINARY", "pandoc-that-is-not-installed")

    with pytest.raises(pandoc.PandocMissingError) as raised:
        pandoc.to_ast(_VOLUME, bibliographies=())

    assert "pandoc-that-is-not-installed" in str(raised.value)
    assert "install" in str(raised.value)


def test_a_bibliography_pandoc_cannot_parse_gets_its_own_type(tmp_path: Path) -> None:
    """The one pandoc failure a caller may continue past, told apart by exit code.

    ``@String(...)`` — parenthesised where citeproc's reader accepts only
    braces — is the real defect: three papers of a 33-paper arXiv sweep ship a
    ``.bib`` opening exactly this way, and every one of them stopped the build.
    """
    unreadable = tmp_path / "broken.bib"
    unreadable.write_text("@String(PAMI = {IEEE Trans.})\n", encoding="utf-8")

    with pytest.raises(pandoc.PandocBibliographyError) as raised:
        pandoc.to_ast(_VOLUME, bibliographies=[unreadable])

    assert "broken.bib" in str(raised.value)


def test_an_unparseable_source_is_not_the_bibliography_failure(bibliography: Path) -> None:
    """The distinction the recovery rests on, so that widening it needs a test to fail.

    A caller that separated the two on message text would swallow this one the
    day pandoc reworded the other, and an unparseable source is a real failure:
    there is no AST to degrade, only a build that should stop.
    """
    with pytest.raises(pandoc.PandocError) as raised:
        pandoc.to_ast("\\documentclass{article}\n\\begin{document}\n\\end{oops}\n", bibliographies=[bibliography])

    assert not isinstance(raised.value, pandoc.PandocBibliographyError)


def test_a_non_zero_exit_carries_the_argv_and_the_complaint() -> None:
    """What a caller has to debug with, since there is no fix loop above this."""
    with pytest.raises(pandoc.PandocError) as raised:
        pandoc.from_ast({"not": "a document"})

    message = str(raised.value)
    assert "pandoc -f json -t gfm" in message
    assert "pandoc-api-version" in message
    assert not isinstance(raised.value, pandoc.PandocMissingError)
