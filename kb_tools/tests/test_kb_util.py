"""Tests for ``kb_util`` root discovery and runner-hint detection.

Root discovery is cwd-anchored: walk up to the first directory containing a
``.git`` entry (dir, or the file a linked worktree carries), then require the
``kb-root/`` content tree beside it. Nothing may derive the root from
``__file__`` — the toolchain is an installed copy under ``.claude/agents/``,
so ``__file__`` describes the install location, not the consuming repo.

Runner hints are detected from the discovered root's runner file (justfile
wins over Makefile; raw ``python3 -m kb_tools....`` when neither exists).

The end-to-end cases build a synthetic consumer repo in a tmp tree (fake
``.git`` + a copy of the ``mini-kb`` fixture as ``kb-root/``) and run the
entry points via subprocess with the cwd inside that repo — no ``--kb-root``
override — proving the walk-up discovery works through the module boundary.
"""

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

import pytest

from kb_tools import kb_pipeline, kb_util
from kb_tools.kb_driver import baton, runlog
from kb_tools.kb_survey import manifest as survey_manifest
from kb_tools.kb_survey import skeleton as survey_skeleton
from kb_tools.tests._manifests import surveyed_manifest

_THIS_DIR = Path(__file__).resolve().parent
# The directory containing the ``kb_tools`` package — used only to point the
# subprocess PYTHONPATH at the package, never to derive a consumer repo root.
_PKG_PARENT = _THIS_DIR.parent.parent
_FIXTURE_SRC = _THIS_DIR / "fixtures" / "mini-kb"


def _make_repo(root: Path, *, git: str = "dir", kb: bool = True) -> Path:
    """Lay out a minimal consuming-repo skeleton under ``root``."""
    root.mkdir(parents=True, exist_ok=True)
    if git == "dir":
        (root / ".git").mkdir()
    elif git == "file":
        # A linked git worktree carries a .git *file*, not a directory.
        (root / ".git").write_text("gitdir: ../elsewhere/.git/worktrees/x\n", encoding="utf-8")
    if kb:
        (root / "kb-root").mkdir()
    return root


def _subprocess_env() -> dict[str, str]:
    return {**os.environ, "PYTHONPATH": str(_PKG_PARENT), "PYTHONDONTWRITEBYTECODE": "1"}


# ---------------------------------------------------------------------------
# find_repo_root / is_repo_root
# ---------------------------------------------------------------------------


def test_find_repo_root_walks_up_from_nested_subdir(tmp_path: Path) -> None:
    repo = _make_repo(tmp_path / "consumer")
    deep = repo / "kb-root" / "a" / "b"
    deep.mkdir(parents=True)
    assert kb_util.find_repo_root(deep) == repo


def test_find_repo_root_accepts_worktree_git_file(tmp_path: Path) -> None:
    repo = _make_repo(tmp_path / "worktree", git="file")
    assert kb_util.find_repo_root(repo / "kb-root") == repo


def test_find_repo_root_defaults_to_cwd(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    repo = _make_repo(tmp_path / "consumer")
    sub = repo / "docs"
    sub.mkdir()
    monkeypatch.chdir(sub)
    assert kb_util.find_repo_root() == repo


def test_find_repo_root_without_git_raises(tmp_path: Path) -> None:
    start = tmp_path / "nowhere" / "deep"
    start.mkdir(parents=True)
    with pytest.raises(kb_util.RepoRootError, match=r"no \.git entry found"):
        kb_util.find_repo_root(start)


def test_root_errors_never_advertise_a_kb_root_flag(tmp_path: Path) -> None:
    """No parser here implements --kb-root; the message must not offer it.

    The root derives from the working directory, so both failure messages say
    that and name running from inside the target repo as the remedy.
    """
    no_git = tmp_path / "nowhere"
    no_git.mkdir()
    with pytest.raises(kb_util.RepoRootError) as no_git_exc:
        kb_util.find_repo_root(no_git)
    no_kb = _make_repo(tmp_path / "consumer", kb=False)
    with pytest.raises(kb_util.RepoRootError) as no_kb_exc:
        kb_util.find_repo_root(no_kb)
    for exc in (no_git_exc, no_kb_exc):
        message = str(exc.value)
        assert "--kb-root" not in message
        assert "working directory" in message
        assert "no override flag" in message


def test_find_git_root_does_not_require_kb_root(tmp_path: Path) -> None:
    """The init op must anchor before kb-root/ exists — that is the tree it creates."""
    repo = _make_repo(tmp_path / "consumer", kb=False)
    nested = repo / "docs" / "deep"
    nested.mkdir(parents=True)
    assert kb_util.find_git_root(nested) == repo
    with pytest.raises(kb_util.RepoRootError, match=r"no \.git entry found"):
        kb_util.find_git_root(tmp_path / "elsewhere")


def test_repo_root_error_is_a_file_not_found_error(tmp_path: Path) -> None:
    # Callers that catch FileNotFoundError (e.g. the CLI) handle discovery
    # failure without special-casing.
    with pytest.raises(FileNotFoundError):
        kb_util.find_repo_root(tmp_path)


def test_is_repo_root_needs_git_and_kb_root_but_no_makefile(tmp_path: Path) -> None:
    # .git + kb-root, no runner file at all: still a root.
    with_git = _make_repo(tmp_path / "a")
    assert kb_util.is_repo_root(with_git)
    # Makefile + kb-root but no .git: not a root (the old Makefile rule is gone).
    no_git = tmp_path / "b"
    (no_git / "kb-root").mkdir(parents=True)
    (no_git / "Makefile").write_text("verify:\n", encoding="utf-8")
    assert not kb_util.is_repo_root(no_git)
    # .git without kb-root: not a root.
    assert not kb_util.is_repo_root(_make_repo(tmp_path / "c", kb=False))


def test_path_helpers_accept_explicit_root(tmp_path: Path) -> None:
    repo = _make_repo(tmp_path / "consumer")
    assert kb_util.kb_root(repo) == repo / "kb-root"
    assert kb_util.index_dir(repo) == repo / "kb-root" / ".index"
    assert kb_util.claims_jsonl(repo) == repo / "kb-root" / ".index" / "claims.jsonl"


# ---------------------------------------------------------------------------
# Runner-hint detection
# ---------------------------------------------------------------------------


def test_hint_prefers_just_when_only_justfile_exists(tmp_path: Path) -> None:
    repo = _make_repo(tmp_path / "consumer")
    (repo / "justfile").write_text("refresh:\n", encoding="utf-8")
    assert kb_util.refresh_cmd(repo) == "just kb-refresh"
    assert kb_util.verify_cmd(repo) == "just kb-verify"


def test_hint_uses_make_when_only_makefile_exists(tmp_path: Path) -> None:
    repo = _make_repo(tmp_path / "consumer")
    (repo / "Makefile").write_text("refresh:\n", encoding="utf-8")
    assert kb_util.refresh_cmd(repo) == "make kb-refresh"
    assert kb_util.verify_cmd(repo) == "make kb-verify"


def test_hint_justfile_wins_over_makefile(tmp_path: Path) -> None:
    repo = _make_repo(tmp_path / "consumer")
    (repo / "justfile").write_text("refresh:\n", encoding="utf-8")
    (repo / "Makefile").write_text("refresh:\n", encoding="utf-8")
    assert kb_util.refresh_cmd(repo) == "just kb-refresh"
    assert kb_util.verify_cmd(repo) == "just kb-verify"


def test_hint_falls_back_to_raw_invocation_with_no_runner_file(tmp_path: Path) -> None:
    repo = _make_repo(tmp_path / "consumer")
    assert kb_util.refresh_cmd(repo) == ("PYTHONPATH=.claude/agents python3 -m kb_tools.refresh_kb_metadata")
    assert kb_util.verify_cmd(repo) == ("PYTHONPATH=.claude/agents python3 -m kb_tools.verify_kb_metadata")


def test_hint_recognizes_runner_file_name_variants(tmp_path: Path) -> None:
    dot_just = _make_repo(tmp_path / "dotjust")
    (dot_just / ".justfile").write_text("refresh:\n", encoding="utf-8")
    assert kb_util.refresh_cmd(dot_just) == "just kb-refresh"

    gnu = _make_repo(tmp_path / "gnu")
    (gnu / "GNUmakefile").write_text("refresh:\n", encoding="utf-8")
    assert kb_util.refresh_cmd(gnu) == "make kb-refresh"


def test_hint_never_raises_without_a_discoverable_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # No repo root anywhere above the cwd: the hint degrades to the raw
    # invocation instead of raising (a remediation hint must never fail).
    bare = tmp_path / "bare"
    bare.mkdir()
    monkeypatch.chdir(bare)
    assert kb_util.refresh_cmd() == ("PYTHONPATH=.claude/agents python3 -m kb_tools.refresh_kb_metadata")


# ---------------------------------------------------------------------------
# Lazy binding and end-to-end cwd discovery
# ---------------------------------------------------------------------------


def test_importing_tools_never_triggers_root_discovery(tmp_path: Path) -> None:
    """Every module imports cleanly with a cwd that has no repo root at all."""
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "import kb_tools.kb_util, kb_tools.kb_index_lib, kb_tools.refresh_kb_metadata, "
            "kb_tools.verify_kb_metadata, kb_tools.verify_md_links, "
            "kb_tools.kb_cmd.index, kb_tools.kb_cmd.cli",
        ],
        cwd=tmp_path,
        env=_subprocess_env(),
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr


def test_refresh_and_verify_discover_root_from_nested_cwd(tmp_path: Path) -> None:
    """No --kb-root: the tools self-anchor by walking up from a nested cwd."""
    repo = _make_repo(tmp_path / "consumer", kb=False)
    shutil.copytree(_FIXTURE_SRC, repo / "kb-root")
    cwd = repo / "kb-root" / "common"

    refresh = subprocess.run(
        [sys.executable, "-m", "kb_tools.refresh_kb_metadata"],
        cwd=cwd,
        env=_subprocess_env(),
        capture_output=True,
        text=True,
        check=False,
    )
    assert refresh.returncode == 0, f"stdout={refresh.stdout}\nstderr={refresh.stderr}"
    assert (repo / "kb-root" / ".index" / "claims.jsonl").is_file()

    verify = subprocess.run(
        [sys.executable, "-m", "kb_tools.verify_kb_metadata"],
        cwd=cwd,
        env=_subprocess_env(),
        capture_output=True,
        text=True,
        check=False,
    )
    assert verify.returncode == 0, f"stdout={verify.stdout}\nstderr={verify.stderr}"


def test_refresh_fails_actionably_when_kb_root_missing(tmp_path: Path) -> None:
    repo = _make_repo(tmp_path / "consumer", kb=False)
    result = subprocess.run(
        [sys.executable, "-m", "kb_tools.refresh_kb_metadata"],
        cwd=repo,
        env=_subprocess_env(),
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 2
    assert "kb-root" in result.stderr


# ---------------------------------------------------------------------------
# Runner-target installer (install-targets / uninstall-targets)
# ---------------------------------------------------------------------------

_JUSTFILE_BODY = "# project recipes\n\nhello:\n    echo hi\n"
_MAKEFILE_BODY = "# project rules\n\nhello:\n\techo hi\n"


def _run_installer(cwd: Path, *args: str) -> subprocess.CompletedProcess:
    """Invoke the installer CLI as a subprocess with the cwd inside a fixture tree."""
    return subprocess.run(
        [sys.executable, "-m", "kb_tools.kb_util", *args],
        cwd=cwd,
        env=_subprocess_env(),
        capture_output=True,
        text=True,
        check=False,
    )


def test_install_appends_include_line_to_existing_justfile(tmp_path: Path) -> None:
    repo = _make_repo(tmp_path / "consumer")
    (repo / "justfile").write_text(_JUSTFILE_BODY, encoding="utf-8")
    # cwd nested inside the tree: the installer discovers the root by walk-up.
    result = _run_installer(repo / "kb-root", "install-targets")
    assert result.returncode == 0, result.stderr
    assert "installed" in result.stdout
    text = (repo / "justfile").read_text(encoding="utf-8")
    assert text == _JUSTFILE_BODY + "\n" + kb_util.INSTALL_LINE_JUST + "\n"


def test_install_appends_include_line_to_existing_makefile(tmp_path: Path) -> None:
    repo = _make_repo(tmp_path / "consumer")
    (repo / "Makefile").write_text(_MAKEFILE_BODY, encoding="utf-8")
    result = _run_installer(repo / "kb-root", "install-targets")
    assert result.returncode == 0, result.stderr
    text = (repo / "Makefile").read_text(encoding="utf-8")
    assert text == _MAKEFILE_BODY + "\n" + kb_util.INSTALL_LINE_MAKE + "\n"


def test_install_prefers_justfile_when_both_runner_files_exist(tmp_path: Path) -> None:
    repo = _make_repo(tmp_path / "consumer")
    (repo / "justfile").write_text(_JUSTFILE_BODY, encoding="utf-8")
    (repo / "Makefile").write_text(_MAKEFILE_BODY, encoding="utf-8")
    result = _run_installer(repo, "install-targets")
    assert result.returncode == 0, result.stderr
    assert kb_util.INSTALL_LINE_JUST in (repo / "justfile").read_text(encoding="utf-8")
    # The Makefile is byte-untouched.
    assert (repo / "Makefile").read_text(encoding="utf-8") == _MAKEFILE_BODY


def test_install_runner_flag_overrides_probe_order(tmp_path: Path) -> None:
    repo = _make_repo(tmp_path / "consumer")
    (repo / "justfile").write_text(_JUSTFILE_BODY, encoding="utf-8")
    (repo / "Makefile").write_text(_MAKEFILE_BODY, encoding="utf-8")
    result = _run_installer(repo, "install-targets", "--runner", "make")
    assert result.returncode == 0, result.stderr
    assert kb_util.INSTALL_LINE_MAKE in (repo / "Makefile").read_text(encoding="utf-8")
    assert (repo / "justfile").read_text(encoding="utf-8") == _JUSTFILE_BODY


def test_install_refuses_when_no_runner_file_and_no_flag(tmp_path: Path) -> None:
    repo = _make_repo(tmp_path / "consumer")
    result = _run_installer(repo, "install-targets")
    assert result.returncode == 2
    assert "--runner" in result.stderr
    assert list(repo.iterdir()) and not (repo / "justfile").exists() and not (repo / "Makefile").exists()


def test_install_creates_runner_file_with_explicit_flag(tmp_path: Path) -> None:
    for runner, filename, line in (
        ("just", "justfile", kb_util.INSTALL_LINE_JUST),
        ("make", "Makefile", kb_util.INSTALL_LINE_MAKE),
    ):
        repo = _make_repo(tmp_path / runner)
        result = _run_installer(repo, "install-targets", "--runner", runner)
        assert result.returncode == 0, result.stderr
        assert "created" in result.stdout
        assert line in (repo / filename).read_text(encoding="utf-8").splitlines()


def test_install_is_idempotent(tmp_path: Path) -> None:
    repo = _make_repo(tmp_path / "consumer")
    (repo / "justfile").write_text(_JUSTFILE_BODY, encoding="utf-8")
    assert _run_installer(repo, "install-targets").returncode == 0
    once = (repo / "justfile").read_bytes()
    again = _run_installer(repo, "install-targets")
    assert again.returncode == 0, again.stderr
    assert "already installed" in again.stdout
    assert (repo / "justfile").read_bytes() == once


def test_uninstall_removes_installed_line(tmp_path: Path) -> None:
    repo = _make_repo(tmp_path / "consumer")
    (repo / "Makefile").write_text(_MAKEFILE_BODY, encoding="utf-8")
    assert _run_installer(repo, "install-targets").returncode == 0
    result = _run_installer(repo, "uninstall-targets")
    assert result.returncode == 0, result.stderr
    assert "uninstalled" in result.stdout
    assert kb_util.INSTALL_LINE_MAKE not in (repo / "Makefile").read_text(encoding="utf-8")


def test_uninstall_reports_not_installed_without_error(tmp_path: Path) -> None:
    # Runner file present, line absent.
    with_runner = _make_repo(tmp_path / "a")
    (with_runner / "justfile").write_text(_JUSTFILE_BODY, encoding="utf-8")
    result = _run_installer(with_runner, "uninstall-targets")
    assert result.returncode == 0, result.stderr
    assert "not installed" in result.stdout
    assert (with_runner / "justfile").read_text(encoding="utf-8") == _JUSTFILE_BODY
    # No runner file at all.
    bare = _make_repo(tmp_path / "b")
    result = _run_installer(bare, "uninstall-targets")
    assert result.returncode == 0, result.stderr
    assert "not installed" in result.stdout


def test_install_uninstall_round_trip_is_byte_exact(tmp_path: Path) -> None:
    repo = _make_repo(tmp_path / "consumer")
    (repo / "justfile").write_text(_JUSTFILE_BODY, encoding="utf-8")
    original = (repo / "justfile").read_bytes()
    assert _run_installer(repo, "install-targets").returncode == 0
    installed = (repo / "justfile").read_bytes()
    # Install touches nothing but the appended blank line + canonical line.
    assert installed == original + b"\n" + kb_util.INSTALL_LINE_JUST.encode() + b"\n"
    assert _run_installer(repo, "uninstall-targets").returncode == 0
    assert (repo / "justfile").read_bytes() == original


# ---------------------------------------------------------------------------
# Real-git fixtures — shared by the seeder and the environment report, both of
# which shell out to git, so the fake .git of _make_repo will not serve.
# ---------------------------------------------------------------------------


def _git_commit_all(root: Path) -> None:
    """Commit everything in ``root`` (identity comes from the repo's own config)."""
    subprocess.run(["git", "add", "-A"], cwd=root, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-qm", "seed"], cwd=root, check=True, capture_output=True)


def _git_repo(
    root: Path,
    *,
    gitignore: str = ".claude-temp/\n",
    docent: bool = True,
    files: dict[str, str] | None = None,
) -> Path:
    """A committed, preflight-clean consumer repo: docent commands + ignored scratch.

    Identity and signing are pinned in the repo's *local* config rather than
    passed per invocation, because the pipeline makes its own commits and must
    do so exactly as it would in a consumer's repo.
    """
    root.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", "-q"], cwd=root, check=True, capture_output=True)
    for key, value in (
        ("user.email", "fixture@example.invalid"),
        ("user.name", "fixture"),
        ("commit.gpgsign", "false"),
    ):
        subprocess.run(["git", "config", key, value], cwd=root, check=True, capture_output=True)
    if docent:
        commands = root / ".claude" / "commands"
        commands.mkdir(parents=True)
        for name in kb_util.DOCENT_COMMAND_FILENAMES:
            (commands / name).write_text(f"# {name}\n", encoding="utf-8")
    (root / ".gitignore").write_text(gitignore, encoding="utf-8")
    for relpath, content in (files or {}).items():
        target = root / relpath
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
    _git_commit_all(root)
    return root


#: A minimal conforming document tree, each document carrying the frontmatter
#: block the metadata gates require: ``entry-point.md``, one volume index, one
#: leaf. ``graph-init`` initialises claim-graph metadata *over* documents, so a
#: fixture with no tree in it exercises only the refusal — this is what the
#: green paths need in ``kb-root/`` before the verb will do anything.
_DOCUMENT_TREE: dict[str, str] = {
    "kb-root/entry-point.md": (
        "<!-- kb-frontmatter\nkind: entry-point\n-->\n\n# Entry Point\n\n- [Volume](vol/index.md)\n"
    ),
    "kb-root/vol/index.md": (
        "[↑ Entry Point](../entry-point.md)\n\n<!-- kb-frontmatter\nkind: index\n-->\n\n"
        "# Volume\n\n- [Leaf](leaf.md)\n"
    ),
    "kb-root/vol/leaf.md": (
        '[↑ Volume](index.md)\n\n<!-- kb-frontmatter\nkind: leaf\nno-claim: "fixture leaf; it states '
        'no result"\n-->\n\n# Leaf\n\nBody text.\n'
    ),
}


def _with_tree(**files: str) -> dict[str, str]:
    """``files`` beside the document tree ``graph-init`` requires as its precondition."""
    return {**_DOCUMENT_TREE, **files}


def _tree_snapshot(root: Path) -> dict[str, bytes]:
    """Every file under ``root`` outside ``.git``, by repo-relative path."""
    return {
        str(path.relative_to(root)): path.read_bytes()
        for path in sorted(root.rglob("*"))
        if path.is_file() and ".git" not in path.relative_to(root).parts
    }


# ---------------------------------------------------------------------------
# Claim-graph metadata seeder (graph-init)
#
# The document tree is the verb's PRECONDITION: it initialises claim-graph
# metadata over documents that already exist, so every green case below hands it
# a tree and the refusal case is the one that hands it none.
# ---------------------------------------------------------------------------


def _seeded_paths(repo: Path) -> tuple[Path, Path]:
    """(index dir, claims register) under an initialised ``repo``."""
    index = repo / "kb-root" / ".index"
    return index, index / "claims.jsonl"


def test_graph_init_initialises_over_a_document_tree(tmp_path: Path) -> None:
    """A tree and a runner file: one command produces a green claim-graph spine.

    And the seed writes no format contract into the KB: `SCHEMA.md`
    was the one artifact this verb copied rather than derived, and a KB that
    still carried it would give a seat something to hand-narrow.
    """
    repo = _git_repo(tmp_path / "consumer", files=_with_tree(justfile=_JUSTFILE_BODY))

    result = _run_installer(repo, "graph-init")

    assert result.returncode == 0, f"stdout={result.stdout}\nstderr={result.stderr}"
    index, claims = _seeded_paths(repo)
    assert index.is_dir()
    assert claims.is_file()
    assert not (index / "SCHEMA.md").exists(), "the seed wrote a local schema copy"
    assert kb_util.INSTALL_LINE_JUST in (repo / "justfile").read_text(encoding="utf-8").splitlines()


def test_graph_init_is_idempotent(tmp_path: Path) -> None:
    repo = _git_repo(tmp_path / "consumer", files=_with_tree(justfile=_JUSTFILE_BODY))
    assert _run_installer(repo, "graph-init").returncode == 0
    # The seed's own output must be committed before a re-run: graph-init gates
    # on preflight, and preflight gates on a clean worktree.
    _git_commit_all(repo)
    index, claims = _seeded_paths(repo)
    before = (claims.read_bytes(), (repo / "justfile").read_bytes())

    again = _run_installer(repo, "graph-init")

    assert again.returncode == 0, again.stderr
    assert "already fully initialised" in again.stdout
    assert "present" in again.stdout
    assert "already installed" in again.stdout
    assert (claims.read_bytes(), (repo / "justfile").read_bytes()) == before
    assert not (index / "SCHEMA.md").exists()


def test_graph_init_completes_a_partly_initialised_tree(tmp_path: Path) -> None:
    """An index directory that is already there is completed, not refused."""
    repo = _git_repo(
        tmp_path / "consumer",
        files=_with_tree(**{"justfile": _JUSTFILE_BODY, "kb-root/.index/.keep": ""}),
    )

    result = _run_installer(repo, "graph-init")

    assert result.returncode == 0, f"stdout={result.stdout}\nstderr={result.stderr}"
    _, claims = _seeded_paths(repo)
    assert claims.is_file()
    assert kb_util.INSTALL_LINE_JUST in (repo / "justfile").read_text(encoding="utf-8").splitlines()


@pytest.mark.parametrize(
    ("runner", "filename", "line"),
    [
        ("just", "justfile", kb_util.INSTALL_LINE_JUST),
        ("make", "Makefile", kb_util.INSTALL_LINE_MAKE),
    ],
)
def test_graph_init_creates_the_named_runner_file_when_the_repo_has_neither(
    tmp_path: Path, runner: str, filename: str, line: str
) -> None:
    """A repo with a tree but no justfile and no Makefile initialises under --runner."""
    repo = _git_repo(tmp_path / runner, files=_with_tree())

    result = _run_installer(repo, "graph-init", "--runner", runner)

    assert result.returncode == 0, f"stdout={result.stdout}\nstderr={result.stderr}"
    assert line in (repo / filename).read_text(encoding="utf-8").splitlines()
    _, claims = _seeded_paths(repo)
    assert claims.is_file()


def test_graph_init_with_no_runner_file_takes_the_default_runner(tmp_path: Path) -> None:
    """No runner file and no --runner initialises, creating the default runner's file.

    This is the reversal of a former refusal: a repo with neither runner file
    used to exit 2 naming both choices and create nothing. ``--runner`` is not a
    question a fresh build should have to carry through its confirmation, so the
    answer that fits a LaTeX project — which needs real dependency management —
    is the one taken when nobody names another. Naming the other still works,
    which is ``test_graph_init_creates_the_named_runner_file_when_the_repo_has_neither``
    above.
    """
    repo = _git_repo(tmp_path / "consumer", files=_with_tree())

    result = _run_installer(repo, "graph-init")

    assert result.returncode == 0, f"stdout={result.stdout}\nstderr={result.stderr}"
    created = repo / kb_util.runner_filename(kb_util.DEFAULT_RUNNER)
    assert kb_util.INSTALL_LINE_MAKE in created.read_text(encoding="utf-8").splitlines()
    # One runner file, not two: the default is a choice made where there was
    # nothing to detect, never a second file beside a runner already there.
    assert not (repo / "justfile").exists()
    _, claims = _seeded_paths(repo)
    assert claims.is_file()


def test_graph_init_does_not_create_the_default_beside_an_existing_runner(tmp_path: Path) -> None:
    """The default is consulted only where the probe found nothing.

    Applied as a plain argparse default it would name ``make`` on every call,
    and naming a runner restricts the probe to that runner's file names — so a
    repo carrying a justfile would gain a Makefile beside it and an include line
    the runner it actually uses never reads.
    """
    repo = _git_repo(tmp_path / "consumer", files=_with_tree(justfile=_JUSTFILE_BODY))

    result = _run_installer(repo, "graph-init")

    assert result.returncode == 0, f"stdout={result.stdout}\nstderr={result.stderr}"
    assert not (repo / "Makefile").exists()
    assert kb_util.INSTALL_LINE_JUST in (repo / "justfile").read_text(encoding="utf-8").splitlines()


def test_graph_init_outside_a_git_repo_reports_cwd_derivation(tmp_path: Path) -> None:
    bare = tmp_path / "bare"
    bare.mkdir()
    result = _run_installer(bare, "graph-init")
    assert result.returncode == 2
    assert "--kb-root" not in result.stderr
    assert "working directory" in result.stderr


def test_graph_init_writes_nothing_when_preflight_fails(tmp_path: Path) -> None:
    """The fused guard: a blocking item stops the seed before any write."""
    repo = _git_repo(tmp_path / "consumer", docent=False, files=_with_tree(justfile=_JUSTFILE_BODY))
    before = _tree_snapshot(repo)

    result = _run_installer(repo, "graph-init")

    assert result.returncode == 2
    assert "FAIL" in result.stdout and "docent-commands" in result.stdout
    assert "nothing seeded" in result.stderr
    assert not (repo / "kb-root" / ".index").exists()
    assert _tree_snapshot(repo) == before


@pytest.mark.parametrize(
    ("files", "case"),
    [
        ({}, "no kb-root at all"),
        ({"kb-root/.index/.keep": ""}, "an index directory and no documents"),
        ({"kb-root/stray.md": "# not a tree\n"}, "content but no entry point"),
    ],
    ids=["absent", "index-only", "no-entry-point"],
)
def test_graph_init_refuses_a_kb_root_holding_no_document_tree(
    tmp_path: Path, files: dict[str, str], case: str
) -> None:
    """The precondition, in the three shapes of its absence — nothing written in any.

    Claim-graph metadata attaches to documents. A ``kb-root/`` with no tree in it
    has nothing to attach to, and that is the error condition rather than the
    environment being unfit: the refusal carries its own code so a caller routes
    to the document-graph front end instead of repairing a sound repository.
    """
    repo = _git_repo(tmp_path / "consumer", files={"justfile": _JUSTFILE_BODY, **files})
    # Pre-created so preflight's own mkdir is not the difference under test.
    (repo / kb_util.SCRATCH_DIRNAME).mkdir()
    before = _tree_snapshot(repo)

    result = _run_installer(repo, "graph-init")

    assert result.returncode == kb_util.EXIT_NO_DOCUMENT_TREE, case
    assert "nothing written" in result.stderr
    assert "no document tree" in result.stderr
    assert "kb_docgraph" in result.stderr
    assert _tree_snapshot(repo) == before
    # Specifically: no include line, and no derived index the seed would have
    # built. The pre-existing `.index/` of the second case is the caller's, and
    # the snapshot above is what says nothing was added to it.
    assert (repo / "justfile").read_text(encoding="utf-8") == _JUSTFILE_BODY
    assert not (repo / "kb-root" / ".index" / "claims.jsonl").exists()


# ---------------------------------------------------------------------------
# Build-environment report (preflight)
# ---------------------------------------------------------------------------


def _item(stdout: str, name: str) -> str:
    """The single report line for item ``name``."""
    matches = [line for line in stdout.splitlines() if f" {name} " in line]
    assert len(matches) == 1, f"expected exactly one {name!r} line in:\n{stdout}"
    return matches[0]


def test_preflight_all_green(tmp_path: Path) -> None:
    """Every gating item passes on a sound repo — worktree-clean included.

    The scratch mkdir happens after the status read, so it cannot self-fail:
    preflight reports green on a repo whose scratch directory it is about to
    create, which is why this case starts without one.
    """
    repo = _git_repo(tmp_path / "consumer")
    assert not (repo / kb_util.SCRATCH_DIRNAME).exists()

    result = _run_installer(repo, "preflight")

    assert result.returncode == 0, f"stdout={result.stdout}\nstderr={result.stderr}"
    assert "FAIL" not in result.stdout
    assert str(repo) in _item(result.stdout, "git-root")
    assert "PASS" in _item(result.stdout, "docent-commands")
    assert "PASS" in _item(result.stdout, "scratch-ignored")
    assert "PASS" in _item(result.stdout, "worktree-clean")


def test_preflight_flags_missing_docent_commands(tmp_path: Path) -> None:
    repo = _git_repo(tmp_path / "consumer", docent=False)

    result = _run_installer(repo, "preflight")

    assert result.returncode == 1
    line = _item(result.stdout, "docent-commands")
    assert "FAIL" in line
    assert "kb-start.md" in line and "kb-next.md" in line
    assert "install" in line


def test_preflight_creates_the_scratch_dir_then_reports_it_present(tmp_path: Path) -> None:
    repo = _git_repo(tmp_path / "consumer")
    scratch = repo / kb_util.SCRATCH_DIRNAME
    assert not scratch.exists()

    first = _run_installer(repo, "preflight")
    assert first.returncode == 0, first.stderr
    assert "created" in _item(first.stdout, "scratch-dir")
    assert scratch.is_dir()

    second = _run_installer(repo, "preflight")
    assert second.returncode == 0, second.stderr
    assert "present" in _item(second.stdout, "scratch-dir")


def test_preflight_flags_uncovered_scratch_without_writing_gitignore(tmp_path: Path) -> None:
    repo = _git_repo(tmp_path / "consumer", gitignore="# nothing ignored here\n")
    before = (repo / ".gitignore").read_bytes()

    result = _run_installer(repo, "preflight")

    assert result.returncode == 1
    line = _item(result.stdout, "scratch-ignored")
    assert "FAIL" in line
    assert "gitignore" in line and "commit" in line
    # The remedy is named, never performed.
    assert (repo / ".gitignore").read_bytes() == before


def test_preflight_flags_a_dirty_worktree_with_a_count(tmp_path: Path) -> None:
    repo = _git_repo(tmp_path / "consumer")
    (repo / "unstaged.md").write_text("scratch\n", encoding="utf-8")
    (repo / "second.md").write_text("scratch\n", encoding="utf-8")

    result = _run_installer(repo, "preflight")

    assert result.returncode == 1
    line = _item(result.stdout, "worktree-clean")
    assert "FAIL" in line
    assert "2 uncommitted entries" in line
    assert "stash" in line


@pytest.mark.parametrize(
    ("populate", "expected"),
    [(None, "absent"), ("spine", "spine-only"), ("content", "populated")],
)
def test_preflight_reports_the_kb_root_tri_state(tmp_path: Path, populate: str | None, expected: str) -> None:
    repo = _git_repo(tmp_path / (populate or "absent"))
    if populate is not None:
        (repo / "kb-root" / ".index").mkdir(parents=True)
    if populate == "content":
        (repo / "kb-root" / "entry-point.md").write_text("# KB\n", encoding="utf-8")
        # Committed so the tri-state is read against a clean worktree — this
        # test is about the fact line, not about dirtiness.
        _git_commit_all(repo)

    result = _run_installer(repo, "preflight")

    line = _item(result.stdout, "kb-root")
    assert "FACT" in line
    assert expected in line
    # A tri-state fact never gates here: acting on it is the driver's launch guard's.
    assert result.returncode == 0, result.stdout


@pytest.mark.parametrize(
    ("filename", "expected"),
    [("justfile", "just"), ("Makefile", "make"), (None, "neither")],
)
def test_preflight_reports_runner_detection(tmp_path: Path, filename: str | None, expected: str) -> None:
    repo = _git_repo(tmp_path / (filename or "bare"))
    if filename is not None:
        (repo / filename).write_text("hello:\n", encoding="utf-8")

    result = _run_installer(repo, "preflight")

    line = _item(result.stdout, "runner-file")
    assert "FACT" in line
    assert expected in line
    if filename is None:
        assert "--runner" in line


def test_preflight_reports_kb_build_scratch_existence_without_gating(tmp_path: Path) -> None:
    repo = _git_repo(tmp_path / "consumer")
    absent = _run_installer(repo, "preflight")
    assert absent.returncode == 0, absent.stderr
    assert "absent" in _item(absent.stdout, "scratch-kb-build")

    (repo / kb_util.SCRATCH_DIRNAME / kb_util.SCRATCH_BUILD_DIRNAME).mkdir(parents=True)
    present = _run_installer(repo, "preflight")
    assert present.returncode == 0, present.stderr
    assert "exists" in _item(present.stdout, "scratch-kb-build")


def test_preflight_ignores_unrelated_scratch_contents(tmp_path: Path) -> None:
    """A .claude-temp/ full of unrelated scratch is not evidence of a prior build.

    A consumer may have used this agent set for ages without ever running a KB
    build, so the report must read identically either way.
    """
    repo = _git_repo(tmp_path / "consumer")
    # First run creates the scratch dir; baseline is the steady state after it,
    # so the only difference under test is the scratch tree's contents.
    assert _run_installer(repo, "preflight").returncode == 0
    baseline = _run_installer(repo, "preflight")
    assert baseline.returncode == 0, baseline.stderr

    scratch = repo / kb_util.SCRATCH_DIRNAME
    (scratch / "probe-harness").mkdir()
    (scratch / "probe-harness" / "out.log").write_text("noise\n", encoding="utf-8")
    (scratch / "old-notes.txt").write_text("unrelated\n", encoding="utf-8")

    after = _run_installer(repo, "preflight")

    assert after.returncode == 0, after.stderr
    assert after.stdout == baseline.stdout


def test_preflight_outside_a_git_repo_reports_cwd_derivation(tmp_path: Path) -> None:
    bare = tmp_path / "bare"
    bare.mkdir()

    result = _run_installer(bare, "preflight")

    assert result.returncode == 2
    assert "working directory" in result.stderr
    # No side effect when the root cannot be resolved.
    assert not (bare / kb_util.SCRATCH_DIRNAME).exists()


# ---------------------------------------------------------------------------
# Pipeline ledger (show-status / start-build / advance-step)
# ---------------------------------------------------------------------------

_CHECKLIST_RE = re.compile(r"^\[([x* ])\] (\S+)")


def _pipeline_repo(root: Path) -> Path:
    """A consumer repo with a seeded spine, so the walk starts where a build's head leaves it.

    The runner file carries the KB include line and ``.index/`` stands, because
    both are what ``spine-seed`` records against: this fixture stands in for the
    head's product exactly as it stands in for the tree.
    """
    justfile = f"{_JUSTFILE_BODY}\n{kb_util.INSTALL_LINE_JUST}\n"
    return _git_repo(root, files=_with_tree(**{"justfile": justfile, "kb-root/.index/.keep": ""}))


def _op(cwd: Path, op: str, *args: str) -> subprocess.CompletedProcess:
    return _run_installer(cwd, op, *args)


def _checklist(stdout: str) -> list[tuple[str, str]]:
    """(marker, stage-id) pairs parsed out of a rendered checklist."""
    found = (_CHECKLIST_RE.match(line) for line in stdout.splitlines())
    return [(m.group(1), m.group(2)) for m in found if m is not None]


def _subjects(repo: Path) -> list[str]:
    """Ledger commit subjects, oldest first."""
    result = subprocess.run(
        ["git", "log", "--reverse", "--format=%s"], cwd=repo, capture_output=True, text=True, check=True
    )
    return [line for line in result.stdout.splitlines() if line.startswith(kb_pipeline.LEDGER_PREFIX)]


def _commit_body(repo: Path) -> str:
    """The newest commit's body paragraph, stripped — the ledger's per-stage detail."""
    return subprocess.run(
        ["git", "log", "-1", "--format=%b"], cwd=repo, capture_output=True, text=True, check=True
    ).stdout.strip()


def _commit_count(repo: Path) -> int:
    result = subprocess.run(
        ["git", "rev-list", "--count", "HEAD"], cwd=repo, capture_output=True, text=True, check=True
    )
    return int(result.stdout.strip())


_NOTHING_TO_CHECK_RE = re.compile(rf"^{re.escape(kb_pipeline._TAG)} note: nothing to check for (\S+) — ")


def _nothing_to_check(stdout: str) -> list[str]:
    """The unit ids a record named as having had nothing to check."""
    found = (_NOTHING_TO_CHECK_RE.match(line) for line in stdout.splitlines())
    return [m.group(1) for m in found if m is not None]


# The manifest a small derived tree is built from, purely as a mechanical way
# to obtain a legal skeleton: two sections, an entry point and one index above
# them.
_MANIFEST = surveyed_manifest()

#: The frontmatter each kind of derived document carries. Claimless throughout:
#: the entry point declares an empty ``subtree-claims``, so a document carrying
#: an id would put the freshness gate phase-3a actually runs at odds with it.
#: Bare Markdown under kb-root fails those gates, so every one of them has a
#: block — the entry point's is what makes the tree bootstrap.
_FRONTMATTER = {
    survey_skeleton.NodeKind.ENTRY_POINT: (
        "kind: entry-point\nsubtree-claims: []\nsubtree-experiments: []\nbootstrap: true"
    ),
    survey_skeleton.NodeKind.INDEX: "kind: index\nsubtree-claims: []\nsubtree-experiments: []",
    survey_skeleton.NodeKind.LEAF: "kind: leaf\nno-claim: fixture leaf, carrying no result of its own",
}


def _declared_documents(manifest: survey_manifest.Manifest) -> dict[str, str]:
    """A small tree the derived skeleton names, kb-root-relative, with a body that verifies.

    Keyed off the production derivation rather than off typed-out paths, so a
    fixture cannot satisfy a gate the running tool would refuse. Built only to
    give ``phase-3a``'s real ``kb-verify`` gate a tree it can pass — no stage
    checks this list against the derivation any more.
    """
    return {
        node.path: f"<!-- kb-frontmatter\n{_FRONTMATTER[node.kind]}\n-->\n\n# {node.title}\n\nContent.\n"
        for node in survey_skeleton.derive(manifest).nodes
    }


# The artifacts the stage postconditions require, at the paths the layout
# contract names. Written only when absent, so a refresh that rewrites a KB
# file is never clobbered by a later call.
_ARTIFACTS = {
    "docs/charter.md": "# Charter\n",
    **{f"kb-root/{relpath}": body for relpath, body in _declared_documents(_MANIFEST).items()},
    "kb-root/README.md": "# README\n",
    "kb-root/CONVENTIONS.md": "# Conventions\n",
}


def _lay_down_artifacts(repo: Path, *, only: set[str] | None = None) -> None:
    """Create the build artifacts the postconditions check for."""
    for relpath, content in _ARTIFACTS.items():
        if only is not None and relpath not in only:
            continue
        target = repo / relpath
        if target.exists():
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")


def _record(repo: Path, stage_id: str) -> subprocess.CompletedProcess:
    """Record one stage through the op its own card names."""
    if stage_id == kb_pipeline.FIRST_STAGE_ID:
        return _op(repo, "start-build", "--charter", "docs/charter.md")
    return _op(repo, "advance-step", "--stage", stage_id)


def _advance_through(repo: Path, last_stage: str) -> None:
    """Record every stage up to and including ``last_stage``.

    Lays the artifacts down first: every stage past ``start`` now refuses to
    record without them, which is the point of the postconditions. Refreshes
    once after that: ``phase-3a``'s coverage check runs the real verify gates
    against ``.index/`` as it stands on disk, and nothing in ``advance-step``
    itself refreshes it first — that is ``kb-refresh``'s job, run here as the
    walk's own driver would run it before the gate.
    """
    _lay_down_artifacts(repo)
    subprocess.run(
        [sys.executable, "-m", "kb_tools.refresh_kb_metadata"],
        cwd=repo,
        env=_subprocess_env(),
        capture_output=True,
        check=True,
    )
    for stage_id in kb_pipeline.STAGE_IDS[: kb_pipeline.STAGE_IDS.index(last_stage) + 1]:
        result = _record(repo, stage_id)
        assert result.returncode == 0, f"{stage_id}: {result.stdout}\n{result.stderr}"


# --- the stage table is the single source ---------------------------------


def test_show_status_exposes_the_frozen_stage_vocabulary_in_order(tmp_path: Path) -> None:
    """Agents extract the stage list from here; nothing else may enumerate it."""
    repo = _pipeline_repo(tmp_path / "consumer")

    result = _op(repo, "show-status")

    assert result.returncode == 0, result.stderr
    assert [stage_id for _, stage_id in _checklist(result.stdout)] == [
        "start",
        "document-graph",
        "spine-seed",
        "claims-declared",
        "claims-discovered",
        "depends-attributed",
        "phase-3a",
        "overview-drafted",
        "phase-5",
    ]


# --- the three world-states -----------------------------------------------


def test_show_status_reports_not_started(tmp_path: Path) -> None:
    repo = _pipeline_repo(tmp_path / "consumer")

    result = _op(repo, "show-status")

    assert result.returncode == 0, result.stderr
    assert "not started" in result.stdout
    # Nothing is in progress before the build begins.
    assert {marker for marker, _ in _checklist(result.stdout)} == {" "}


def test_show_status_reports_in_progress_with_a_resume_marker(tmp_path: Path) -> None:
    repo = _pipeline_repo(tmp_path / "consumer")
    _advance_through(repo, "start")

    result = _op(repo, "show-status")

    assert result.returncode == 0, result.stderr
    assert "in progress" in result.stdout
    assert f"1 of {len(kb_pipeline.STAGES)}" in result.stdout
    markers = dict((stage_id, marker) for marker, stage_id in _checklist(result.stdout))
    assert markers["start"] == "x"
    # The first unrecorded stage is where a resuming agent picks up.
    assert markers[kb_pipeline.STAGE_IDS[1]] == "*"
    assert markers["phase-3a"] == " " and markers["phase-5"] == " "


# The third world-state, complete, needs a spine green enough to clear
# phase-3a's postcondition: see
# test_the_walk_records_every_stage_and_ends_complete.


def test_show_status_is_read_only(tmp_path: Path) -> None:
    repo = _pipeline_repo(tmp_path / "consumer")
    before = (_tree_snapshot(repo), _commit_count(repo))

    assert _op(repo, "show-status").returncode == 0

    assert (_tree_snapshot(repo), _commit_count(repo)) == before


# --- start-build ----------------------------------------------------------


def test_start_build_sweeps_the_uncommitted_seed_into_the_first_commit(tmp_path: Path) -> None:
    repo = _pipeline_repo(tmp_path / "consumer")
    # Stand in for what init leaves uncommitted: the seed belongs to the
    # build's first commit, so `git add -A` must pick it up.
    (repo / "kb-root" / ".index" / "claims.jsonl").write_text("", encoding="utf-8")
    (repo / "docs").mkdir()
    (repo / "docs" / "charter.md").write_text("# Charter\n", encoding="utf-8")

    result = _op(repo, "start-build", "--charter", "docs/charter.md")

    assert result.returncode == 0, f"{result.stdout}\n{result.stderr}"
    assert _subjects(repo) == ["kb-build: start | build started"]
    body = subprocess.run(
        ["git", "log", "-1", "--format=%b"], cwd=repo, capture_output=True, text=True, check=True
    ).stdout
    assert "charter: docs/charter.md" in body
    # The sweep left the worktree clean.
    status = subprocess.run(["git", "status", "--porcelain"], cwd=repo, capture_output=True, text=True, check=True)
    assert status.stdout.strip() == ""


def test_the_start_boundary_says_which_of_the_two_charter_states_held(tmp_path: Path) -> None:
    """A build with no charter is recorded as having none, not recorded silently.

    A charter is optional (SPEC.md, The Driver's Contract), so opening without
    one is a legal build rather than something to refuse. What is not legal is a
    boundary that says nothing: an empty body is equally what a caller that
    dropped the argument leaves behind, and the start commit is the only durable
    place the two can be told apart — so a user who wrote a charter this build
    never looked at finds that out by reading the ledger.
    """
    named = _pipeline_repo(tmp_path / "with-charter")
    _lay_down_artifacts(named, only={"docs/charter.md"})
    absent = _pipeline_repo(tmp_path / "without-charter")

    assert _op(named, "start-build", "--charter", "docs/charter.md").returncode == 0
    assert _op(absent, "start-build").returncode == 0

    named_body, absent_body = _commit_body(named), _commit_body(absent)
    assert "docs/charter.md" in named_body
    assert absent_body == kb_pipeline.NO_CHARTER_BODY
    # Neither boundary is silent, and the two do not read alike.
    assert named_body and absent_body and named_body != absent_body


def test_start_build_refuses_a_second_start(tmp_path: Path) -> None:
    repo = _pipeline_repo(tmp_path / "consumer")
    _lay_down_artifacts(repo, only={"docs/charter.md"})
    assert _op(repo, "start-build", "--charter", "docs/charter.md").returncode == 0
    before = _commit_count(repo)

    result = _op(repo, "start-build", "--charter", "docs/charter.md")

    assert result.returncode == kb_pipeline.EXIT_ALREADY_STARTED
    assert "already started" in result.stderr
    assert _commit_count(repo) == before
    # The checklist still comes back, so the caller learns where it actually is.
    assert _checklist(result.stdout)


# --- advance-step ---------------------------------------------------------


def test_advance_step_records_a_boundary_commit(tmp_path: Path) -> None:
    repo = _pipeline_repo(tmp_path / "consumer")
    _advance_through(repo, _predecessor_of("phase-3a"))

    result = _op(repo, "advance-step", "--stage", "phase-3a")

    assert result.returncode == 0, result.stderr
    assert _subjects(repo)[-1] == "kb-build: phase-3a | validation gate"


def test_advance_step_commits_even_when_nothing_changed(tmp_path: Path) -> None:
    """The boundary is the point, not the diff: --allow-empty fallback."""
    repo = _pipeline_repo(tmp_path / "consumer")
    _advance_through(repo, _predecessor_of("phase-3a"))
    before = _commit_count(repo)

    result = _op(repo, "advance-step", "--stage", "phase-3a")

    assert result.returncode == 0, result.stderr
    assert _commit_count(repo) == before + 1


def test_advance_step_sweeps_worktree_changes_into_the_boundary(tmp_path: Path) -> None:
    repo = _pipeline_repo(tmp_path / "consumer")
    _advance_through(repo, "phase-3a")
    (repo / "kb-root" / "extra-note.md").write_text(
        f"<!-- kb-frontmatter\n{_FRONTMATTER[survey_skeleton.NodeKind.LEAF]}\n-->\n\n# Extra\n\nContent.\n",
        encoding="utf-8",
    )
    # Scratch is gitignored and must stay out of the commit.
    (repo / kb_util.SCRATCH_DIRNAME).mkdir(exist_ok=True)
    (repo / kb_util.SCRATCH_DIRNAME / "noise.txt").write_text("junk\n", encoding="utf-8")

    assert _op(repo, "advance-step", "--stage", "overview-drafted").returncode == 0

    tracked = subprocess.run(
        ["git", "ls-files"], cwd=repo, capture_output=True, text=True, check=True
    ).stdout.splitlines()
    assert "kb-root/extra-note.md" in tracked
    assert not any(name.startswith(kb_util.SCRATCH_DIRNAME) for name in tracked)


def test_advance_step_records_a_note_in_the_commit_body(tmp_path: Path) -> None:
    repo = _pipeline_repo(tmp_path / "consumer")
    _advance_through(repo, _predecessor_of("phase-3a"))

    result = _op(repo, "advance-step", "--stage", "phase-3a", "--note", "gate green on the first pass")

    assert result.returncode == 0, result.stderr
    body = subprocess.run(
        ["git", "log", "-1", "--format=%b"], cwd=repo, capture_output=True, text=True, check=True
    ).stdout
    assert "gate green on the first pass" in body


def test_advance_step_is_idempotent_per_stage(tmp_path: Path) -> None:
    repo = _pipeline_repo(tmp_path / "consumer")
    _advance_through(repo, "phase-3a")
    before = _commit_count(repo)

    result = _op(repo, "advance-step", "--stage", "phase-3a")

    assert result.returncode == 0, result.stderr
    assert "already recorded" in result.stdout
    assert _commit_count(repo) == before
    assert _checklist(result.stdout)


def test_advance_step_refuses_an_out_of_order_stage(tmp_path: Path) -> None:
    repo = _pipeline_repo(tmp_path / "consumer")
    _advance_through(repo, "start")
    before = _commit_count(repo)

    result = _op(repo, "advance-step", "--stage", "phase-5")

    assert result.returncode == kb_pipeline.EXIT_OUT_OF_ORDER
    assert "cannot record 'phase-5'" in result.stderr
    # The refusal names exactly what is missing, and corrects the world-model.
    for missing in ("phase-3a",):
        assert missing in result.stderr
    assert _commit_count(repo) == before
    assert _checklist(result.stdout)


# --- the scope pin: phase-3a's readiness stamp -----------------------------
#
# `kb-root/CLAUDE.md` asserts that this KB's scope is pinned in it, so the pin
# has to actually be there. It is charter prose, and the stamp that writes the
# document is what fills it.

_PIN_CHARTER = "This KB distills the Ave corpus: the governance-bifurcation papers and nothing else.\n"


def _charter_build_repo(root: Path, charter: str | None = _PIN_CHARTER) -> Path:
    """A pipeline repo advanced to just before ``phase-3a``, carrying ``charter``.

    ``charter`` of ``None`` opens the build with no ``--charter`` at all, which
    is the legitimate charterless build rather than an error.
    """
    repo = _pipeline_repo(root)
    _lay_down_artifacts(repo)
    if charter is not None:
        (repo / "docs" / "charter.md").write_text(charter, encoding="utf-8")
    subprocess.run(
        [sys.executable, "-m", "kb_tools.refresh_kb_metadata"],
        cwd=repo,
        env=_subprocess_env(),
        capture_output=True,
        check=True,
    )
    opened = _op(repo, "start-build", *(("--charter", "docs/charter.md") if charter is not None else ()))
    assert opened.returncode == 0, opened.stderr
    for stage_id in kb_pipeline.STAGE_IDS[1 : kb_pipeline.STAGE_IDS.index("phase-3a")]:
        result = _op(repo, "advance-step", "--stage", stage_id)
        assert result.returncode == 0, f"{stage_id}: {result.stdout}\n{result.stderr}"
    # The stamp is only-if-absent, and this document is the one it must write.
    (repo / "kb-root" / kb_pipeline.SCOPE_PIN_DOC).unlink(missing_ok=True)
    return repo


def test_scope_pin_reaches_the_document_that_claims_to_carry_it(tmp_path: Path) -> None:
    repo = _charter_build_repo(tmp_path / "consumer")

    result = _op(repo, "advance-step", "--stage", "phase-3a")

    assert result.returncode == 0, result.stderr
    stamped = (repo / "kb-root" / kb_pipeline.SCOPE_PIN_DOC).read_text(encoding="utf-8")
    assert _PIN_CHARTER.strip() in stamped
    # And no slot survives into the consumer's own file.
    assert kb_pipeline.SCOPE_PIN_FIELD not in stamped
    assert kb_pipeline.PROJECT_NAME_FIELD not in stamped


def test_charterless_build_states_the_absence_as_the_pin(tmp_path: Path) -> None:
    """A charter is optional, so the document says there is none rather than standing blank."""
    repo = _charter_build_repo(tmp_path / "consumer", charter=None)

    result = _op(repo, "advance-step", "--stage", "phase-3a")

    assert result.returncode == 0, result.stderr
    stamped = (repo / "kb-root" / kb_pipeline.SCOPE_PIN_DOC).read_text(encoding="utf-8")
    assert kb_pipeline.NO_CHARTER_PIN in stamped
    assert kb_pipeline.SCOPE_PIN_FIELD not in stamped


def test_readiness_stamp_does_not_change_the_kb_root_state(tmp_path: Path) -> None:
    """The constraint the pin's placement answers to.

    ``pre.kb-root`` refuses a fresh build over a populated ``kb-root/`` by
    reading this tri-state, so a build-open write that flipped it would make the
    guard refuse the build that performed the write. Stamping at the validation
    gate cannot: the tree is already populated by then.
    """
    repo = _charter_build_repo(tmp_path / "consumer")
    before = kb_util.kb_root_state(repo)

    result = _op(repo, "advance-step", "--stage", "phase-3a")

    assert result.returncode == 0, result.stderr
    assert (repo / "kb-root" / kb_pipeline.SCOPE_PIN_DOC).is_file()
    assert kb_util.kb_root_state(repo) == before == kb_util.KB_ROOT_POPULATED


def test_a_build_open_pin_write_would_have_changed_the_kb_root_state(tmp_path: Path) -> None:
    """Why the pin is not written when the build opens — the demonstration, not an assertion.

    At build-open ``kb-root/`` holds nothing outside ``.index/``. Any file
    written into it there — the pin's document included — turns the reading
    ``pre.kb-root`` refuses on.
    """
    repo = _pipeline_repo(tmp_path / "consumer")
    shutil.rmtree(repo / "kb-root")
    (repo / "kb-root" / kb_util.INDEX_DIRNAME).mkdir(parents=True)
    assert kb_util.kb_root_state(repo) == kb_util.KB_ROOT_SPINE_ONLY

    (repo / "kb-root" / kb_pipeline.SCOPE_PIN_DOC).write_text("# pinned\n", encoding="utf-8")

    assert kb_util.kb_root_state(repo) == kb_util.KB_ROOT_POPULATED


def test_recorded_charter_reads_the_start_boundary(tmp_path: Path) -> None:
    """The ledger is where the charter's path durably stands, and an absence is stated there too."""
    with_charter = _charter_build_repo(tmp_path / "with-charter")
    without = _charter_build_repo(tmp_path / "without-charter", charter=None)

    assert kb_pipeline.recorded_charter(with_charter) == "docs/charter.md"
    assert kb_pipeline.recorded_charter(without) is None
    # An unopened build has no boundary to read, and names no charter either.
    assert kb_pipeline.recorded_charter(_pipeline_repo(tmp_path / "unopened")) is None


def test_stamp_refuses_a_recorded_charter_that_is_no_longer_on_disk(tmp_path: Path) -> None:
    """Writing the stated absence over a charter the ledger names would put a lie in the pin."""
    repo = _charter_build_repo(tmp_path / "consumer")
    (repo / "docs" / "charter.md").unlink()

    result = _op(repo, "advance-step", "--stage", "phase-3a")

    assert result.returncode != 0
    assert "docs/charter.md" in result.stdout + result.stderr
    assert not (repo / "kb-root" / kb_pipeline.SCOPE_PIN_DOC).exists()


# --- the seeded-spine cases: postcondition and completion ------------------


def _seeded_build_repo(root: Path) -> Path:
    """A consumer repo with a real, green, committed spine and its artifacts.

    Order matters and mirrors the real flow: the document tree is there first,
    ``graph-init`` initialises the claim-graph spine over it (it refuses a
    kb-root holding no tree), then the rest of the build's artifacts land, then
    a refresh makes the derived index match — which is what phase-3a verifies.
    """
    repo = _git_repo(root, files=_with_tree(justfile=_JUSTFILE_BODY))
    assert _run_installer(repo, "graph-init").returncode == 0
    _lay_down_artifacts(repo)
    subprocess.run(
        [sys.executable, "-m", "kb_tools.refresh_kb_metadata"],
        cwd=repo,
        env=_subprocess_env(),
        capture_output=True,
        check=True,
    )
    _git_commit_all(repo)
    return repo


@dataclass(frozen=True)
class Rung:
    """One boundary of the ladder walk: how it recorded, and the world it left."""

    stage_id: str
    record: subprocess.CompletedProcess
    status: subprocess.CompletedProcess
    tree: Path


@pytest.fixture(scope="session")
def ladder(tmp_path_factory: pytest.TempPathFactory) -> dict[str, Rung]:
    """One seeded repo walked through every stage, snapshotted at each boundary.

    Two costs are paid once here. Seeding the spine is a git init, an ``init``
    subprocess, a real refresh and two commits; reaching stage N on top of that
    is N more subprocesses and N more commits, so a case that starts late used
    to pay for every stage before it. A case now copies the snapshot it wants,
    and branching at the last stage costs one ``advance-step`` rather than one
    per stage before it.

    A snapshot is a whole working repository mid-build: a ``git init`` tree
    records no absolute path of its own, and the build artifacts under the
    gitignored scratch directory are ordinary files the copy carries along.

    Nothing is asserted here. A walk that went wrong is diagnosed by
    ``test_the_walk_records_every_stage_and_ends_complete``, which reads these
    outcomes back and names every checkpoint that broke in a single run.
    The snapshots are the session's to read and never to write — ``branch_at``
    is how a test obtains one it may drive further.
    """
    root = tmp_path_factory.mktemp("ladder")
    repo = _seeded_build_repo(root / "walk")
    _lay_down_artifacts(repo)
    rungs: dict[str, Rung] = {}
    for stage_id in kb_pipeline.STAGE_IDS:
        record = _record(repo, stage_id)
        tree = root / "after" / stage_id
        shutil.copytree(repo, tree)
        rungs[stage_id] = Rung(stage_id=stage_id, record=record, status=_op(repo, "show-status"), tree=tree)
    return rungs


@pytest.fixture
def branch_at(ladder: dict[str, Rung], tmp_path: Path) -> Callable[[str], Path]:
    """This test's own copy of the walk as it stood after ``stage_id``.

    Copied, never reset: the copy only ever writes into the directory pytest
    just created for this test, so a wrong path here cannot destroy anything a
    reset or a clean could.
    """

    def branch(stage_id: str) -> Path:
        target = tmp_path / f"after-{stage_id}"
        shutil.copytree(ladder[stage_id].tree, target)
        return target

    return branch


def test_validation_gate_refuses_to_record_on_red(branch_at: Callable[[str], Path]) -> None:
    """The one mechanical coverage check: 3a is proved, not claimed.

    All three return codes are named, not just the one that broke: a refusal
    reporting only the failing gate would leave a reader unable to see that the
    other two were even run.
    """
    repo = branch_at(_predecessor_of("phase-3a"))
    (repo / "kb-root" / "broken.md").write_text("# Broken\n\n[gone](nowhere.md)\n", encoding="utf-8")
    before = _commit_count(repo)

    result = _op(repo, "advance-step", "--stage", "phase-3a")

    assert result.returncode == kb_pipeline.EXIT_POSTCONDITION_FAILED
    assert "cannot record 'phase-3a'" in result.stderr
    assert "verify-gates" in result.stderr
    assert "links rc=1" in result.stderr
    assert "metadata rc=" in result.stderr
    assert "citations rc=" in result.stderr
    assert _commit_count(repo) == before


def test_the_walk_records_every_stage_and_ends_complete(
    ladder: dict[str, Rung], branch_at: Callable[[str], Path]
) -> None:
    """Every checkpoint of one clean walk, reported together.

    These properties are independent of one another, so they are collected and
    compared in a single mapping rather than asserted one at a time: a run that
    breaks four of them names all four, instead of surfacing the first and
    hiding the rest behind it.

    The artifact-gated stages are deliberately one entry and not several. They
    are nested prefixes of this same walk, so a break in one is a break in all
    of them, and several messages would be one fact repeated.
    """
    last_stage = kb_pipeline.STAGE_IDS[-1]
    final = ladder[last_stage]
    recorded = {
        subject.removeprefix(f"{kb_pipeline.LEDGER_PREFIX} ").split(" | ", 1)[0] for subject in _subjects(final.tree)
    }

    # The one observation here that writes, and so the only one that cannot be
    # made against the shared walk: a complete process asked to record its final
    # stage a second time.
    settled = branch_at(last_stage)
    before = _commit_count(settled)
    rerecorded = _op(settled, "advance-step", "--stage", last_stage)

    observed = {
        "stages whose record was not clean": [
            f"{rung.stage_id} exited {rung.record.returncode}: {rung.record.stdout}\n{rung.record.stderr}"
            for rung in ladder.values()
            if rung.record.returncode != 0
        ],
        "the ledger's boundary subjects, in order": _subjects(final.tree),
        "artifact-gated stages missing from the ledger": sorted({s for s, _ in _CHECKED_STAGES} - recorded),
        "commits the phase-5 boundary added": _commit_count(ladder["phase-5"].tree)
        - _commit_count(ladder[_predecessor_of("phase-5")].tree),
        "the world-state named at completion": "complete" in final.status.stdout,
        "checklist markers at completion": sorted({marker for marker, _ in _checklist(final.status.stdout)}),
        "cards rendered by a complete process": _cards(final.status.stdout),
        "the line closing the card-less render": final.status.stdout.splitlines()[-1],
        "re-recording the final stage exits": rerecorded.returncode,
        "re-recording says the process is already complete": "process already complete" in rerecorded.stdout,
        "commits re-recording added": _commit_count(settled) - before,
    }

    assert observed == {
        "stages whose record was not clean": [],
        "the ledger's boundary subjects, in order": [
            f"{kb_pipeline.LEDGER_PREFIX} {stage.id} | {stage.display}" for stage in kb_pipeline.STAGES
        ],
        "artifact-gated stages missing from the ledger": [],
        "commits the phase-5 boundary added": 1,
        "the world-state named at completion": True,
        "checklist markers at completion": ["x"],
        "cards rendered by a complete process": [],
        "the line closing the card-less render": f"[kb-build] {kb_pipeline.CONTRACT_LINE}",
        "re-recording the final stage exits": 0,
        "re-recording says the process is already complete": True,
        "commits re-recording added": 0,
    }


# --- argument pairing ------------------------------------------------------


@pytest.mark.parametrize(
    "args",
    [
        ("advance-step",),
        ("show-status", "--stage", "phase-3a"),
        ("show-status", "--charter", "c.md"),
        # --stage is the read's own option; the record's two arguments are not.
        ("show-stage-status", "--charter", "c.md"),
        ("show-stage-status", "--note", "x"),
        ("start-build", "--charter", "c.md", "--stage", "phase-3a"),
        ("start-build", "--charter", "c.md", "--note", "x"),
        ("init", "--stage", "phase-3a"),
        # --runner is declared on init / install-targets / uninstall-targets
        # alone, so the flat surface's silent acceptance of it on every op is
        # now a usage error.
        ("preflight", "--runner", "just"),
        ("show-status", "--runner", "make"),
        # The write ops. `--values` is required by each of them and
        # declared on no build op; `--create` is declared on the two register
        # inserts and on no other write op, so an invalid pairing here is a
        # usage error before any KB file is opened.
        ("insert-claim-entry",),
        ("set-rigor",),
        ("set-rigor", "--values", "v.toml", "--create"),
        ("insert-experiment-entry", "--values", "v.toml", "--create"),
        ("mark-claim-in-leaf", "--values", "v.toml", "--runner", "just"),
        ("preflight", "--values", "v.toml"),
        ("insert-claim-entry", "--values", "v.toml", "--stage", "phase-3a"),
        # No op at all: the subparsers action is required.
        (),
    ],
)
def test_op_argument_pairing_is_enforced(tmp_path: Path, args: tuple[str, ...]) -> None:
    """An option belonging to another op is unrepresentable rather than policed."""
    repo = _pipeline_repo(tmp_path / "consumer")
    result = _run_installer(repo, *args)
    assert result.returncode == 2
    assert "usage:" in result.stderr


@pytest.mark.parametrize("op", ["advance-step", "show-stage-status"])
def test_unknown_stage_id_is_a_usage_error(tmp_path: Path, op: str) -> None:
    """Both ops that take a stage take it from the one vocabulary."""
    repo = _pipeline_repo(tmp_path / "consumer")
    result = _op(repo, op, "--stage", "phase-9")
    assert result.returncode == 2
    assert "invalid choice" in result.stderr


def test_the_build_opens_in_a_repository_that_has_no_kb_yet(tmp_path: Path) -> None:
    """``start-build`` anchors on the git root, so a build opens before a KB exists.

    This is the state a fresh build actually starts from: the tree is the
    ``document-graph`` stage's own product, and the stage that opens the build
    runs before it. An op that required ``kb-root/`` here would make the first
    two stages of every build unrecordable.
    """
    repo = _git_repo(tmp_path / "consumer", files={"docs/charter.md": "# Charter\n"})

    result = _op(repo, "start-build", "--charter", "docs/charter.md")

    assert result.returncode == 0, result.stderr
    assert _subjects(repo) == [f"{kb_pipeline.LEDGER_PREFIX} start | build started"]


def test_a_stage_recorded_out_of_order_is_refused_before_the_kb_exists_too(tmp_path: Path) -> None:
    """The refusal a caller meets there is the ordering one, not a missing-root one."""
    repo = _git_repo(tmp_path / "consumer")

    result = _op(repo, "advance-step", "--stage", kb_pipeline.STAGE_IDS[1])

    assert result.returncode == kb_pipeline.EXIT_OUT_OF_ORDER
    assert "predecessors are unrecorded" in result.stderr


def test_show_status_works_before_the_spine_is_seeded(tmp_path: Path) -> None:
    """A fresh build renders its checklist at confirmation time — before init.

    The ledger lives in the commit trail, not the KB, so an absent kb-root/ is
    status information rather than a fault.
    """
    repo = _git_repo(tmp_path / "consumer")
    assert not (repo / "kb-root").exists()

    result = _op(repo, "show-status")

    assert result.returncode == 0, result.stderr
    assert "not started" in result.stdout
    assert {marker for marker, _ in _checklist(result.stdout)} == {" "}
    assert [stage_id for _, stage_id in _checklist(result.stdout)] == list(kb_pipeline.STAGE_IDS)
    # The unseeded state is named, not left for the reader to infer.
    assert "not seeded yet" in result.stdout


def test_show_status_omits_the_unseeded_note_once_the_spine_exists(tmp_path: Path) -> None:
    repo = _pipeline_repo(tmp_path / "consumer")

    result = _op(repo, "show-status")

    assert result.returncode == 0, result.stderr
    assert "not seeded yet" not in result.stdout


# ---------------------------------------------------------------------------
# Action cards, the contract line, and baton lines
# ---------------------------------------------------------------------------


def _cards(stdout: str) -> list[str]:
    return [line for line in stdout.splitlines() if line.startswith(kb_pipeline.CARD_PREFIX)]


def test_every_stage_carries_a_card_with_exactly_one_record_step_and_stage_status_step(tmp_path: Path) -> None:
    """The card is single-sourced beside the stage id; none may be missing.

    Each must carry exactly one generated ``RecordStep``: the record verb
    appears nowhere else an agent reads, so a card without one is a dead end
    and a card with two is ambiguous. Each must likewise carry exactly one
    generated ``StageStatusStep``, for the same reason: it is the one place a
    card hands back what a stage still has to cover.
    """
    for stage in kb_pipeline.STAGES:
        assert stage.card, f"{stage.id} has no action card"
        assert all(not isinstance(item, str) or item.strip() for item in stage.card), stage.id
        assert sum(isinstance(item, kb_pipeline.RecordStep) for item in stage.card) == 1, stage.id
        assert sum(isinstance(item, kb_pipeline.StageStatusStep) for item in stage.card) == 1, stage.id


def test_pre_start_renders_the_start_card(tmp_path: Path) -> None:
    repo = _pipeline_repo(tmp_path / "consumer")

    result = _op(repo, "show-status")

    cards = _cards(result.stdout)
    assert cards[0] == f"{kb_pipeline.CARD_PREFIX} next action — start (build started):"
    assert any(kb_pipeline.CHARTER_RELPATH in line for line in cards)
    # What follows the opening boundary is the driver's own next stage. The card
    # used to direct a dispatch here, and the build has no such route: a card is
    # an operator's next action, so one naming a seat that does not exist is a
    # defect rather than a stale phrasing.
    assert any("nothing is dispatched here" in line for line in cards)
    assert not any("coordinator" in line for line in cards)


def test_advance_step_card_is_its_baton(tmp_path: Path) -> None:
    """Recording a stage renders the next stage's card, not the recorded one's."""
    repo = _pipeline_repo(tmp_path / "consumer")
    _advance_through(repo, _predecessor_of("phase-3a"))

    result = _op(repo, "advance-step", "--stage", "phase-3a")

    assert _cards(result.stdout)[0].startswith(f"{kb_pipeline.CARD_PREFIX} next action — overview-drafted ")


def test_refusals_still_render_the_current_card(tmp_path: Path) -> None:
    repo = _pipeline_repo(tmp_path / "consumer")
    _advance_through(repo, _predecessor_of("phase-3a"))

    result = _op(repo, "advance-step", "--stage", "phase-5")

    assert result.returncode == kb_pipeline.EXIT_OUT_OF_ORDER
    # Refused the wrong stage, still told where the build actually is.
    assert _cards(result.stdout)[0].startswith(f"{kb_pipeline.CARD_PREFIX} next action — phase-3a ")


@pytest.mark.parametrize(
    "make_call",
    [
        pytest.param(lambda repo: _op(repo, "show-status"), id="show-status"),
        pytest.param(lambda repo: _op(repo, "start-build", "--charter", "c.md"), id="start-build"),
        pytest.param(lambda repo: _op(repo, "advance-step", "--stage", "start"), id="advance-step"),
    ],
)
def test_every_pipeline_render_ends_with_the_contract_line(tmp_path: Path, make_call) -> None:
    repo = _pipeline_repo(tmp_path / "consumer")

    result = make_call(repo)

    assert result.stdout.splitlines()[-1] == f"[kb-build] {kb_pipeline.CONTRACT_LINE}"


def test_contract_line_closes_a_refusal_render_too(tmp_path: Path) -> None:
    repo = _pipeline_repo(tmp_path / "consumer")
    _advance_through(repo, "start")

    result = _op(repo, "advance-step", "--stage", "phase-5")

    assert result.returncode == kb_pipeline.EXIT_OUT_OF_ORDER
    assert result.stdout.splitlines()[-1] == f"[kb-build] {kb_pipeline.CONTRACT_LINE}"


def test_checklist_block_stays_contiguous_and_uniquely_parseable(tmp_path: Path) -> None:
    """Card, note, status and contract lines must not leak into a checklist parse."""
    repo = _pipeline_repo(tmp_path / "consumer")
    _advance_through(repo, "start")

    lines = _op(repo, "show-status").stdout.splitlines()

    matched = [i for i, line in enumerate(lines) if _CHECKLIST_RE.match(line)]
    assert len(matched) == len(kb_pipeline.STAGES)
    # One unbroken run: no card or prose line falls inside the block.
    assert matched == list(range(matched[0], matched[0] + len(kb_pipeline.STAGES)))
    # And nothing outside it parses as a checklist entry.
    assert not _CHECKLIST_RE.match(f"[kb-build] {kb_pipeline.CONTRACT_LINE}")
    assert not _CHECKLIST_RE.match(f"{kb_pipeline.CARD_PREFIX} · record")


def test_start_build_directs_no_coordinator(tmp_path: Path) -> None:
    """R-D: the build is driver-controlled end to end, so no card names a coordinator seat."""
    repo = _pipeline_repo(tmp_path / "consumer")
    _lay_down_artifacts(repo, only={"docs/charter.md"})

    result = _op(repo, "start-build", "--charter", "docs/charter.md")

    assert result.returncode == 0, result.stderr
    assert "coordinator" not in result.stdout
    # The render still hands the reader its next action: the card for the
    # stage the checklist now points at.
    assert kb_pipeline.CARD_PREFIX in result.stdout


def test_preflight_baton_on_success_only(tmp_path: Path) -> None:
    repo = _git_repo(tmp_path / "consumer")

    green = _run_installer(repo, "preflight")
    assert green.returncode == 0
    assert "[preflight] next: confirm with the user, then run graph-init" in green.stdout

    red = _run_installer(_git_repo(tmp_path / "broken", docent=False), "preflight")
    assert red.returncode == 1
    assert "next:" not in red.stdout


def test_graph_init_baton_on_success(tmp_path: Path) -> None:
    repo = _git_repo(tmp_path / "consumer", files=_with_tree(justfile=_JUSTFILE_BODY))

    result = _run_installer(repo, "graph-init")

    assert result.returncode == 0, result.stderr
    assert f"{kb_util.GRAPH_INIT_TAG} next: start-build [--charter <path>]" in result.stdout
    # graph-init runs the preflight suite; its standalone baton must not appear.
    assert "then run graph-init" not in result.stdout


# --- generated card obligations -------------------------------------------

_OP_PREFIX = "PYTHONPATH=.claude/agents python3 -m kb_tools.kb_util "


@pytest.mark.parametrize("stage", kb_pipeline.STAGES, ids=[s.id for s in kb_pipeline.STAGES])
def test_record_line_carries_the_full_invocation_for_its_own_stage(tmp_path: Path, stage: kb_pipeline.Stage) -> None:
    """The record command is generated from the stage, never hand-written.

    A hand-maintained stage id inside a card string is exactly the drift this
    guards: every stage's record line must name its own id and no other.
    """
    repo = _pipeline_repo(tmp_path / "consumer")

    lines = kb_pipeline.card_lines(stage, repo)
    record = [line for line in lines if " · record: " in line]
    assert len(record) == 1, lines

    if stage.id == kb_pipeline.FIRST_STAGE_ID:
        assert f"{_OP_PREFIX}start-build [--charter {kb_pipeline.CHARTER_RELPATH}]" in record[0]
        assert "advance-step" not in record[0]
    else:
        assert f"{_OP_PREFIX}advance-step --stage {stage.id}" in record[0]
        assert "start-build" not in record[0]
        # No other stage's id may appear in this stage's record line.
        others = [other for other in kb_pipeline.STAGE_IDS if other != stage.id]
        tail = record[0].split(f"--stage {stage.id}", 1)[1]
        assert not any(f"--stage {other}" in tail for other in others)


def test_record_line_renders_note_and_aside_where_the_spec_asks(tmp_path: Path) -> None:
    repo = _pipeline_repo(tmp_path / "consumer")
    by_id = {stage.id: stage for stage in kb_pipeline.STAGES}

    phase_3a = " ".join(kb_pipeline.card_lines(by_id["phase-3a"], repo))
    assert "--stage phase-3a" in phase_3a
    assert "(the tool refuses to record this stage red)" in phase_3a

    # A stage whose spec record line says nothing extra gets a bare command.
    phase_5 = [line for line in kb_pipeline.card_lines(by_id["phase-5"], repo) if " · record: " in line][0]
    assert "--note" not in phase_5


_RUNNERS = [("justfile", "just"), ("Makefile", "make")]


def _repo_with_runner(tmp_path: Path, runner_file: str) -> Path:
    """A pipeline repo carrying exactly one runner file, so detection has one answer."""
    repo = _pipeline_repo(tmp_path / runner_file)
    for name in ("justfile", "Makefile"):
        (repo / name).unlink(missing_ok=True)
    (repo / runner_file).write_text("hello:\n", encoding="utf-8")
    return repo


@pytest.mark.parametrize(("runner_file", "expected"), _RUNNERS)
def test_validation_gate_card_names_the_repos_own_runner(tmp_path: Path, runner_file: str, expected: str) -> None:
    """A justfile consumer must not be told to run make."""
    repo = _repo_with_runner(tmp_path, runner_file)
    gate = {stage.id: stage for stage in kb_pipeline.STAGES}["phase-3a"]

    line = [line for line in kb_pipeline.card_lines(gate, repo) if " · run " in line][0]

    assert line.endswith(f"run `{expected} kb-refresh` then `{expected} kb-verify`")


def test_the_full_verify_gate_belongs_to_phase_3a_alone(tmp_path: Path) -> None:
    """One pre-completion verify gate, at the first stage where it can pass.

    Stated over the table rather than one card, so a later stage cannot
    quietly acquire a second gate.
    """
    gated = {
        stage.id for stage in kb_pipeline.STAGES if any(isinstance(item, kb_pipeline.GateStep) for item in stage.card)
    }
    assert gated == {"phase-3a"}


# --- loop caps, rendered from their one definition ------------------------

# (stage whose card carries the line, the cap constant it renders, the line as
# an agent must read it).
_CAPPED_CARD_LINES = [
    (
        "phase-5",
        "PHASE_5_FIX_CAP",
        "tech-writer-reviewer reviews the documents the stage before this one wrote, tech-writer "
        "answers each round's findings; fix-cycle cap {cap}; findings persisting -> escalate",
    ),
]
_CAPPED_IDS = [stage_id for stage_id, _, _ in _CAPPED_CARD_LINES]

# A cap number written into a plain card string, in any of the shapes the
# cards used before the constants existed.
_HAND_WRITTEN_CAP_RE = re.compile(r"\bcap \d|\bmax \d|\b\d+ (?:cycles?|iterations?)\b")


def _card_line(template: str, cap: int) -> str:
    return f"{kb_pipeline.CARD_PREFIX} · " + template.format(cap=cap)


@pytest.mark.parametrize(("stage_id", "constant", "template"), _CAPPED_CARD_LINES, ids=_CAPPED_IDS)
def test_card_renders_its_cap_from_the_constant(tmp_path: Path, stage_id: str, constant: str, template: str) -> None:
    """The card an agent reads carries the number the driver will enforce."""
    repo = _pipeline_repo(tmp_path / "consumer")
    stage = {s.id: s for s in kb_pipeline.STAGES}[stage_id]

    lines = kb_pipeline.card_lines(stage, repo)

    assert _card_line(template, getattr(kb_pipeline, constant)) in lines


@pytest.mark.parametrize(("stage_id", "constant", "template"), _CAPPED_CARD_LINES, ids=_CAPPED_IDS)
def test_retuning_a_cap_retunes_the_card_that_names_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, stage_id: str, constant: str, template: str
) -> None:
    """One definition: the constant moves and the card follows, with nothing to sync."""
    repo = _pipeline_repo(tmp_path / "consumer")
    stage = {s.id: s for s in kb_pipeline.STAGES}[stage_id]
    before = _card_line(template, getattr(kb_pipeline, constant))

    monkeypatch.setattr(kb_pipeline, constant, 9)
    lines = kb_pipeline.card_lines(stage, repo)

    assert _card_line(template, 9) in lines
    assert before not in lines


@pytest.mark.parametrize(
    "removed",
    [
        "Criticals -> architect revises (max 2 cycles, one final review)",
        "failures -> distiller fixes, cap 2, then escalate",
        "max 3 iterations then escalate",
    ],
)
def test_the_hand_written_cap_guard_catches_the_shapes_it_replaced(removed: str) -> None:
    """The guard below is only worth running if it matches what it is guarding against."""
    assert _HAND_WRITTEN_CAP_RE.search(removed)


def test_no_card_hand_writes_a_cap_number() -> None:
    """A cap living in a plain string is exactly the drift the constants remove."""
    offenders = [
        (stage.id, item)
        for stage in kb_pipeline.STAGES
        for item in stage.card
        if isinstance(item, str) and _HAND_WRITTEN_CAP_RE.search(item)
    ]

    assert offenders == []


# --- the cards a walking agent actually reads ------------------------------
#
# Everything above is asserted against the stage table; these four are asserted
# against what `show-status` actually puts on an agent's screen, at the
# boundaries whose rendered card says something distinct. Each reads the render
# the ladder walk already captured, so all four are free and stay separate —
# a card naming the wrong runner and a card hand-writing a cap number are
# different diagnoses and deserve different reports.


def test_in_progress_renders_the_starred_stages_card(ladder: dict[str, Rung]) -> None:
    """The card and the [*] marker are one decision, so they cannot disagree."""
    rendered = ladder[_predecessor_of("phase-3a")].status.stdout

    starred = [stage_id for marker, stage_id in _checklist(rendered) if marker == "*"]
    assert starred == ["phase-3a"]
    cards = _cards(rendered)
    assert cards[0].startswith(f"{kb_pipeline.CARD_PREFIX} next action — phase-3a ")
    assert any("run `just kb-refresh` then `just kb-verify`" in line for line in cards)


def test_cap_lines_are_rendered_through_the_running_tool(ladder: dict[str, Rung]) -> None:
    """End-to-end: the number an agent actually reads comes from the constant."""
    cards = _cards(ladder[_predecessor_of("phase-5")].status.stdout)

    assert any(f"fix-cycle cap {kb_pipeline.PHASE_5_FIX_CAP};" in line for line in cards)
    # The pre-constant phrasing, which said the same number twice.
    assert not any("max 1 iteration" in line for line in cards)


def test_validation_gate_card_is_rendered_through_the_running_tool(ladder: dict[str, Rung]) -> None:
    """End-to-end: the card an agent actually reads names the detected runner."""
    cards = _cards(ladder[_predecessor_of("phase-3a")].status.stdout)

    assert any("run `just kb-refresh` then `just kb-verify`" in line for line in cards)
    assert not any("make kb-refresh" in line for line in cards)


# --- artifact-existence postconditions ------------------------------------

# (stage id, the artifact path whose absence must block that stage). Empty
# today: every remaining stage's coverage is either the verify gates
# (``phase-3a``, ``depends-attributed``) or a unit the walk itself produces,
# and the two review stages whose findings files this list held are gone. The
# parametrization below runs over nothing and is kept because the shape it
# asserts — remove the artifact, the boundary will not record — is what any
# stage that gains an artifact unit must satisfy.
_CHECKED_STAGES: list[tuple[str, str]] = []


def _predecessor_of(stage_id: str) -> str:
    return kb_pipeline.STAGE_IDS[kb_pipeline.STAGE_IDS.index(stage_id) - 1]


# The passing half — every one of these stages recording once its artifact is
# there — is one entry in
# test_the_walk_records_every_stage_and_ends_complete, whose walk passes
# through both. The refusing half needs a distinct end-state per stage, and
# two broken postconditions is a different diagnosis from one, so it stays
# parametrized and branches from the walk instead of replaying it.


@pytest.mark.parametrize(("stage_id", "artifact"), _CHECKED_STAGES, ids=[s for s, _ in _CHECKED_STAGES])
def test_stage_refuses_when_its_artifact_is_missing(
    branch_at: Callable[[str], Path], stage_id: str, artifact: str
) -> None:
    """Existence is the gate: remove the artifact, the boundary will not record."""
    repo = branch_at(_predecessor_of(stage_id))
    (repo / artifact).unlink()
    before = _commit_count(repo)

    result = _op(repo, "advance-step", "--stage", stage_id)

    assert result.returncode == kb_pipeline.EXIT_POSTCONDITION_FAILED
    assert f"cannot record '{stage_id}'" in result.stderr
    assert _commit_count(repo) == before
    # The corrective feedback is the card itself.
    assert _cards(result.stdout)[0].startswith(f"{kb_pipeline.CARD_PREFIX} next action — {stage_id} ")


def test_start_refuses_when_the_charter_was_never_written(tmp_path: Path) -> None:
    repo = _pipeline_repo(tmp_path / "consumer")
    before = _commit_count(repo)

    result = _op(repo, "start-build", "--charter", "docs/charter.md")

    assert result.returncode == kb_pipeline.EXIT_POSTCONDITION_FAILED
    # The unit, and the path it was looked for at — a refusal naming neither
    # leaves the caller guessing which of the two arguments was wrong.
    assert "charter" in result.stderr
    assert "docs/charter.md" in result.stderr
    assert _commit_count(repo) == before


def test_meta_documentation_requires_the_overview_and_not_the_stamped_document(
    branch_at: Callable[[str], Path],
) -> None:
    """One check, two boundaries, and it asks for the document these stages produce.

    ``CONVENTIONS.md`` comes from ``phase-3a``'s readiness stamp, so a boundary
    here that asked for it would be satisfied by an earlier stage's work — a
    unit that cannot fail and therefore tells a caller nothing. Asserted both
    ways from one tree: its absence does not refuse the boundary, and the
    overview's does.
    """
    repo = branch_at("phase-3a")
    (repo / "kb-root" / kb_pipeline.CONVENTIONS_DOC).unlink()
    before = _commit_count(repo)

    stamped_gone = _op(repo, "advance-step", "--stage", "overview-drafted")

    assert stamped_gone.returncode == 0, stamped_gone.stderr
    assert _commit_count(repo) == before + 1

    (repo / "kb-root" / kb_pipeline.OVERVIEW_DOC).unlink()
    overview_gone = _op(repo, "advance-step", "--stage", "phase-5")

    assert overview_gone.returncode == kb_pipeline.EXIT_POSTCONDITION_FAILED
    assert kb_pipeline.OVERVIEW_DOC in overview_gone.stderr
    # Only what this stage owes: the document an earlier stage stamped is gone
    # from the tree and the refusal has nothing to say about it.
    assert kb_pipeline.CONVENTIONS_DOC not in overview_gone.stderr
    assert _commit_count(repo) == before + 1


def test_only_one_unit_is_satisfied_with_nothing_to_check(branch_at: Callable[[str], Path]) -> None:
    """Every stage carries a coverage report, so "has no check" is now a unit property.

    Asked with an empty charter — the one argument-derived unit any surviving
    stage can leave vacuous — exactly one unit is satisfied by construction
    rather than by something found. A second stage appearing here is one that
    silently stopped checking.
    """
    repo = branch_at(kb_pipeline.STAGE_IDS[-1])
    ctx = kb_pipeline.CheckContext(repo, note="", charter="")

    vacuous = {
        f"{stage.id}:{unit.id}" for stage in kb_pipeline.STAGES for unit in stage.coverage(ctx).units if unit.vacuous
    }

    assert vacuous == {"start:charter"}


def test_a_recording_stage_names_what_it_had_nothing_to_check(tmp_path: Path) -> None:
    """A build with no charter must not read like one that named it.

    The line is what leaves a reader able to tell, and it is checked in both
    directions: absent it, an assertion that a normal build stays quiet would
    pass against code that can never speak.
    """
    repo = _pipeline_repo(tmp_path / "consumer")

    result = _op(repo, "start-build")

    assert result.returncode == 0, result.stderr
    assert _nothing_to_check(result.stdout) == ["charter"]
    # The line carries the reason, not just the name.
    assert "this build carries no charter" in result.stdout


def test_a_stage_that_found_its_artifacts_says_nothing(tmp_path: Path, branch_at: Callable[[str], Path]) -> None:
    """The other direction, over both kinds of unit that can carry the flag.

    A build given a charter is the same argument-derived unit as above with a
    value to find; ``overview-drafted`` is an ordinary existence unit satisfied by
    the document that is there. Neither may emit the line, or it degrades
    into noise a reader stops reading.
    """
    repo = _pipeline_repo(tmp_path / "consumer")
    _lay_down_artifacts(repo, only={"docs/charter.md"})
    started = _op(repo, "start-build", "--charter", "docs/charter.md")

    assert started.returncode == 0, started.stderr
    assert _nothing_to_check(started.stdout) == []

    documented = _op(branch_at("phase-3a"), "advance-step", "--stage", "overview-drafted")

    assert documented.returncode == 0, documented.stderr
    assert _nothing_to_check(documented.stdout) == []


# ---------------------------------------------------------------------------
# show-stage-status: the read, and its agreement with the refusal
# ---------------------------------------------------------------------------

_UNIT_RE = re.compile(
    rf"^{re.escape(kb_pipeline.STAGE_STATUS_TAG)} ({kb_pipeline.COVERED}|{kb_pipeline.MISSING}) (\S+) "
)


def _unit_ids(stdout: str, status: str) -> list[str]:
    """The unit ids a render reports under ``status``, lifted from its lines.

    From the lines and never from a sentence: what the two consumers must agree
    on is the set of units, and a test reading one of them out of prose would
    be asserting over a wording rather than over the computation behind it.
    """
    found = (_UNIT_RE.match(line) for line in stdout.splitlines())
    return sorted(match.group(2) for match in found if match is not None and match.group(1) == status)


def _missing_ids(stdout: str) -> list[str]:
    return _unit_ids(stdout, kb_pipeline.MISSING)


def _stage_status_lines(stdout: str) -> list[str]:
    return [line for line in stdout.splitlines() if line.startswith(kb_pipeline.STAGE_STATUS_TAG)]


def _remove(*relpaths: str) -> Callable[[Path], None]:
    def mutate(repo: Path) -> None:
        for relpath in relpaths:
            (repo / relpath).unlink()

    return mutate


def _break_a_link(repo: Path) -> None:
    (repo / "kb-root" / "broken.md").write_text("# Broken\n\n[gone](nowhere.md)\n", encoding="utf-8")


def _uninstall_the_include_line(repo: Path) -> None:
    """The half of the seed that installs the runner targets, undone."""
    justfile = repo / "justfile"
    kept = [line for line in justfile.read_text(encoding="utf-8").splitlines() if line != kb_util.INSTALL_LINE_JUST]
    justfile.write_text("\n".join(kept) + "\n", encoding="utf-8")


def _a_document_without_its_metadata_block(repo: Path) -> None:
    """One document as the document graph leaves it: no metadata block on it at all."""
    (repo / "kb-root" / "unstamped.md").write_text("# Unstamped\n\nContent.\n", encoding="utf-8")


def _a_document_still_awaiting_a_reading(repo: Path) -> None:
    """One document carrying the declared pass's own awaiting reason, verbatim."""
    from kb_tools.kb_claimgraph.assemble import UNSCANNED_REASON

    (repo / "kb-root" / "awaiting.md").write_text(
        f"<!-- kb-frontmatter\nkind: leaf\nno-claim: {UNSCANNED_REASON}\n-->\n\n# Awaiting\n\nContent.\n",
        encoding="utf-8",
    )


#: One constructed repo state per stage, built on that stage's own predecessor
#: snapshot so everything before it is real. ``start`` has no predecessor and
#: is constructed in the test itself.
_PARTIAL_COVERAGE: dict[str, Callable[[Path], None]] = {
    **{stage_id: _remove(artifact) for stage_id, artifact in _CHECKED_STAGES},
    "document-graph": _remove("kb-root/entry-point.md"),
    "spine-seed": _uninstall_the_include_line,
    "claims-declared": _a_document_without_its_metadata_block,
    "claims-discovered": _a_document_still_awaiting_a_reading,
    # The head's exit is the same gate the tail enters on, so it fails the same way.
    "depends-attributed": _break_a_link,
    "phase-3a": _break_a_link,
    # Both meta-documentation boundaries read one check, and the overview is
    # what it asks for. Removing CONVENTIONS.md would refuse neither: the
    # readiness stamp owns that document and no boundary after phase-3a asks
    # whether it stands.
    "overview-drafted": _remove("kb-root/README.md"),
    "phase-5": _remove("kb-root/README.md"),
}


def _refuse_stage(repo: Path, stage_id: str) -> subprocess.CompletedProcess:
    """Ask the ledger to record ``stage_id`` over a state that cannot satisfy it."""
    if stage_id == kb_pipeline.FIRST_STAGE_ID:
        return _op(repo, "start-build", "--charter", "docs/charter.md")
    return _op(repo, "advance-step", "--stage", stage_id)


@pytest.mark.parametrize("stage_id", kb_pipeline.STAGE_IDS)
def test_the_read_and_the_refusal_name_one_set_of_missing_units(
    branch_at: Callable[[str], Path], tmp_path: Path, stage_id: str
) -> None:
    """The two consumers of one stage's coverage agree, unit for unit.

    This is the row's load-bearing test. Both renders come out of the running
    tool over the same tree, and the ids are lifted from the ``MISSING`` lines
    of each — so a handler that composed its own report would still print
    something plausible, and would still fail here.

    Compared as id sets and not as text: the details legitimately differ, and
    ``start`` and ``phase-1b`` are why. Their units are argument-derived, so a
    read says the argument is not available to it while a refusal says what the
    record was missing. Which unit is unsatisfied is the same answer either way.
    """
    if stage_id == kb_pipeline.FIRST_STAGE_ID:
        repo = _pipeline_repo(tmp_path / "unstarted")
    else:
        repo = branch_at(_predecessor_of(stage_id))
        _PARTIAL_COVERAGE[stage_id](repo)

    read = _op(repo, "show-stage-status", "--stage", stage_id)
    refused = _refuse_stage(repo, stage_id)

    assert read.returncode == 0, read.stderr
    assert refused.returncode == kb_pipeline.EXIT_POSTCONDITION_FAILED, refused.stdout
    assert _missing_ids(read.stdout) == _missing_ids(refused.stdout)
    # Not vacuous: a pair of renders naming nothing would agree trivially.
    assert _missing_ids(read.stdout)


def test_the_read_reports_phase_5s_one_unit_covered_and_then_missing(branch_at: Callable[[str], Path]) -> None:
    """What landed and what did not, with the path either way.

    The paths are the point. A coordinator resuming here dispatches against the
    MISSING line's path, which no other verb renders — and reads the COVERED
    line's to know it need not. One unit, so the two renders are taken from the
    same tree either side of one deletion rather than from two units at once.
    """
    repo = branch_at("phase-3a")
    # The tool resolves its root from the working directory, so the paths it
    # prints are that resolution's, not the fixture path's spelling of it.
    kb = repo.resolve() / "kb-root"
    fact = (
        f"{kb_pipeline.STAGE_STATUS_TAG} {kb_pipeline.FACT} phase-5 (meta-documentation) — 1 coverage unit(s) declared"
    )

    covered = _op(repo, "show-stage-status", "--stage", "phase-5")

    assert covered.returncode == 0, covered.stderr
    assert _stage_status_lines(covered.stdout) == [
        fact,
        f"{kb_pipeline.STAGE_STATUS_TAG} {kb_pipeline.COVERED} README.md ({kb / 'README.md'})",
    ]

    (kb / "README.md").unlink()
    missing = _op(repo, "show-stage-status", "--stage", "phase-5")

    assert missing.returncode == 0, missing.stderr
    assert _stage_status_lines(missing.stdout) == [
        fact,
        f"{kb_pipeline.STAGE_STATUS_TAG} {kb_pipeline.MISSING} README.md "
        f"({kb / 'README.md'}) — this document was never written",
    ]


def test_the_read_renders_no_checklist_and_no_card(branch_at: Callable[[str], Path]) -> None:
    """Not ``show-status``: this render is a list as long as the corpus.

    ``show-status``' render is quoted verbatim into every user message, which is
    what the contract line demands of it; a unit list inside that payload has no
    bound. So the coverage read carries neither the checklist nor the card, and
    the checklist block keeps its one producer.
    """
    repo = branch_at("phase-3a")

    for stage_id in (None, "start", "phase-3a", "phase-5"):
        result = _op(repo, "show-stage-status", *(() if stage_id is None else ("--stage", stage_id)))

        assert result.returncode == 0, result.stderr
        assert _checklist(result.stdout) == [], stage_id
        assert _cards(result.stdout) == [], stage_id
        assert kb_pipeline.CONTRACT_LINE not in result.stdout, stage_id


def test_the_zero_argument_read_is_the_stage_the_checklist_stars(branch_at: Callable[[str], Path]) -> None:
    """One ``current_stage`` decision behind the marker, the card and this read."""
    repo = branch_at(_predecessor_of("phase-5"))
    starred = [stage_id for marker, stage_id in _checklist(_op(repo, "show-status").stdout) if marker == "*"]

    asked = _op(repo, "show-stage-status")

    assert asked.returncode == 0, asked.stderr
    assert starred == ["phase-5"]
    assert _stage_status_lines(asked.stdout) == _stage_status_lines(
        _op(repo, "show-stage-status", "--stage", "phase-5").stdout
    )


def test_a_complete_build_names_no_stage_and_does_not_fall_back_to_the_last(branch_at: Callable[[str], Path]) -> None:
    """Every stage recorded is an answer, not a stage to report on.

    Falling back to the final stage would answer a question nobody asked and
    read as though phase-5 were still in flight.
    """
    repo = branch_at(kb_pipeline.STAGE_IDS[-1])

    asked = _op(repo, "show-stage-status")

    assert asked.returncode == 0, asked.stderr
    lines = _stage_status_lines(asked.stdout)
    assert len(lines) == 1
    assert lines[0].startswith(f"{kb_pipeline.STAGE_STATUS_TAG} {kb_pipeline.FACT} ")
    assert f"all {len(kb_pipeline.STAGES)} stages are recorded" in lines[0]
    assert "--stage" in lines[0]
    # And it is not the last stage's report wearing a different opening line.
    assert lines != _stage_status_lines(_op(repo, "show-stage-status", "--stage", kb_pipeline.STAGE_IDS[-1]).stdout)


def test_a_recorded_stage_and_an_unreached_stage_both_report(branch_at: Callable[[str], Path]) -> None:
    """Neither position refuses the question: a resume reads back, a lookahead reads forward.

    Both report every unit they declare, and here both find them: the walk lays
    its artifacts down before it starts, so the document phase-5 declares is on
    disk long before phase-5 is reached. That is the tree, and the read
    reports the tree rather than the ledger's opinion of it.
    """
    repo = branch_at("phase-3a")

    recorded = _op(repo, "show-stage-status", "--stage", "phase-3a")
    unreached = _op(repo, "show-stage-status", "--stage", "phase-5")

    assert recorded.returncode == 0, recorded.stderr
    assert _unit_ids(recorded.stdout, kb_pipeline.COVERED) == ["verify-gates"]
    assert _missing_ids(recorded.stdout) == []
    assert unreached.returncode == 0, unreached.stderr
    assert _unit_ids(unreached.stdout, kb_pipeline.COVERED) == sorted(kb_pipeline.META_DOCS)


def test_the_read_writes_nothing_and_records_nothing(branch_at: Callable[[str], Path]) -> None:
    """The op's whole product is its stdout, in every stage state it can be asked about."""
    repo = branch_at("phase-3a")
    before = (_tree_snapshot(repo), _commit_count(repo))

    for args in ((), ("--stage", kb_pipeline.FIRST_STAGE_ID), ("--stage", "phase-3a"), ("--stage", "phase-5")):
        assert _op(repo, "show-stage-status", *args).returncode == 0

    assert (_tree_snapshot(repo), _commit_count(repo)) == before


def test_the_read_outside_a_git_repo_exits_two(tmp_path: Path) -> None:
    """The one nonzero code it has: an unresolvable root is the absence of an answer."""
    nowhere = tmp_path / "nowhere"
    nowhere.mkdir()

    result = _op(nowhere, "show-stage-status")

    assert result.returncode == 2
    assert "working directory" in result.stderr


# ---------------------------------------------------------------------------
# The run lock, read from outside the driver (show-run-lock)
#
# The question is `kb_driver.runlog`'s and so is the answer; what is asserted
# here is that a caller that is not the driver can ask it — the three states told
# apart in the output rather than in the exit status, the lock file untouched by
# the asking, and the liveness judgement reaching the output from `runlog` rather
# than from a second test on this side.
# ---------------------------------------------------------------------------


def _lock_repo(root: Path, *, payload: str | None) -> Path:
    """A repo carrying a ``.git`` entry and, where ``payload`` is given, a run lock holding it.

    Deliberately no ``kb-root/``: the op anchors on the git root alone, which is
    what lets the caller that is about to remove the KB tree — or that has just
    removed it — still ask whether a build is running.
    """
    repo = _make_repo(root, kb=False)
    if payload is not None:
        lock = runlog.repo_lock_path(repo)
        lock.parent.mkdir(parents=True, exist_ok=True)
        lock.write_text(payload, encoding="utf-8")
    return repo


def _reported(stdout: str) -> dict[str, str]:
    """The op's output as a shell reads it: one ``key=value`` per line, split at the first ``=``."""
    return dict(line.split("=", 1) for line in stdout.splitlines())


def test_show_run_lock_reports_a_live_holder_and_names_it(tmp_path: Path) -> None:
    """A lock naming a pid that is alive — this test process — reads live, holder and all.

    The holder fields travel with the verdict because a caller refusing to wipe a
    workspace has to say whose run it refused for, and the pid and the run id are
    what it says it with.
    """
    started = "2026-09-12T00:00:00+00:00"
    payload = json.dumps({"pid": os.getpid(), "run_id": "run-alive", "token": "t", "started": started})
    repo = _lock_repo(tmp_path / "live", payload=payload)

    result = _op(repo, "show-run-lock")

    assert result.returncode == 0, result.stderr
    reported = _reported(result.stdout)
    assert list(reported) == list(kb_util.RUN_LOCK_KEYS)
    assert reported["state"] == runlog.LOCK_LIVE
    assert reported["pid"] == str(os.getpid())
    assert reported["run_id"] == "run-alive"
    assert reported["started"] == started
    assert reported["lock"] == str(runlog.repo_lock_path(repo.resolve()))


@pytest.mark.parametrize("flavour", ["reaped-holder", "unparseable"])
def test_show_run_lock_reports_a_stale_lock_and_clears_nothing(tmp_path: Path, flavour: str) -> None:
    """Stale is an answer, not an act: the lock is still there afterwards, byte for byte.

    Two flavours, because ``runlog`` judges both alike: a lock naming a pid that
    has been reaped, and one whose payload will not parse — which is a lock no
    holder can be read out of rather than a lock with none, so it names nobody
    and is stale all the same.
    """
    if flavour == "reaped-holder":
        reaped = subprocess.Popen([sys.executable, "-c", "pass"])
        reaped.wait()
        payload = json.dumps({"pid": reaped.pid, "run_id": "run-gone", "token": "t"})
        expected_pid = str(reaped.pid)
    else:
        payload = "not json at all"
        expected_pid = ""
    repo = _lock_repo(tmp_path / flavour, payload=payload)

    result = _op(repo, "show-run-lock")

    assert result.returncode == 0, result.stderr
    reported = _reported(result.stdout)
    assert reported["state"] == runlog.LOCK_STALE
    assert reported["pid"] == expected_pid
    assert runlog.repo_lock_path(repo).read_text(encoding="utf-8") == payload


def test_show_run_lock_reports_an_absent_lock_in_the_same_shape(tmp_path: Path) -> None:
    """No lock file is the third answer, and it arrives shaped like the other two.

    The key set is closed and total, so a shell branches on the value of
    ``state`` and never on which keys turned up.
    """
    repo = _lock_repo(tmp_path / "absent", payload=None)

    result = _op(repo, "show-run-lock")

    assert result.returncode == 0, result.stderr
    reported = _reported(result.stdout)
    assert list(reported) == list(kb_util.RUN_LOCK_KEYS)
    assert reported["state"] == runlog.LOCK_ABSENT
    assert [reported[key] for key in ("pid", "run_id", "started")] == ["", "", ""]
    assert reported["lock"] == str(runlog.repo_lock_path(repo.resolve()))
    assert not runlog.repo_lock_path(repo).exists()


def test_show_run_lock_takes_its_liveness_from_runlog_rather_than_re_deriving_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """The op reports ``runlog``'s judgement, so moving that judgement moves the answer.

    Driven in-process because that is what lets the judgement be moved under it:
    with ``runlog``'s pid probe answering *gone*, a lock naming this very
    process — alive by construction — must read stale. An op carrying an
    ``os.kill`` test of its own would go on saying live, which is the duplication
    this asserts the absence of.
    """
    repo = _lock_repo(tmp_path / "routed", payload=json.dumps({"pid": os.getpid(), "run_id": "run-x"}))
    monkeypatch.chdir(repo)
    monkeypatch.setattr(runlog, "_pid_alive", lambda pid: False)

    assert kb_util.main(["show-run-lock"]) == 0

    assert _reported(capsys.readouterr().out)["state"] == runlog.LOCK_STALE


# ---------------------------------------------------------------------------
# The nine write ops on the CLI
#
# What is asserted here is the *binding* and nothing else: that every op in the
# registry has a subparser, that each declares the arguments its own semantics
# take and no others, and that the three exit codes reach a process exit status
# unchanged. The ladder itself — every refusal reason, every report line, the
# readback, the census — is `test_kb_write_ops.py`'s, tested with no argparse in
# the picture, and duplicating any of it here would be a second answer to a
# question that already has one owner.
#
# No test below asserts a report line's wording, only its status token, the
# identity it names, and the exit code.
# ---------------------------------------------------------------------------


def _write_handlers() -> dict[str, object]:
    """Each subcommand's bound handler, read off the shipped parser."""
    action = next(a for a in kb_util.build_parser()._actions if isinstance(a, argparse._SubParsersAction))
    return {name: sub.get_default("handler") for name, sub in action.choices.items()}


def _options_of(op: str) -> set[str]:
    """The long options one subcommand declares, minus argparse's own help."""
    action = next(a for a in kb_util.build_parser()._actions if isinstance(a, argparse._SubParsersAction))
    return {
        flag
        for parser_action in action.choices[op]._actions
        for flag in parser_action.option_strings
        if flag.startswith("--") and flag != "--help"
    }


def test_the_write_op_constants_key_the_op_registry() -> None:
    """The CLI's op constants and the write registry's semantics name the same ops.

    ``kb_write.ops`` imports ``kb_util``, so the import that would let this
    module read the registry does not exist in this direction — the constants
    are declared upstream and this equality is what stands in for it. A rename
    on either side, or an op declared on one and not the other, fails here
    rather than shipping a subcommand with no semantics behind it (or semantics
    no surface reaches).
    """
    from kb_tools.kb_write import ops as write_ops

    assert set(kb_util.WRITE_OPS) == set(write_ops.OPS)
    assert len(kb_util.WRITE_OPS) == len(set(kb_util.WRITE_OPS)) == len(write_ops.OPS)


def test_the_read_op_constant_keys_the_sibling_registry_and_joins_no_write_set() -> None:
    """The read-only op is a sibling of the write surface, not a tenth member.

    The consequence is what this pins. ``WRITE_OPS`` is what the driver's
    ledger admits as a spawnable write, what carries the write ops' four-code
    exit vocabulary, and what ``steps.WRITE_OP_SLOTS`` renders a brief slot
    for; an op that writes nothing and that no brief invokes belongs to none of
    those. The two registries are disjoint and together are exactly the closed
    values vocabulary.
    """
    from kb_tools.kb_write import ops as write_ops
    from kb_tools.kb_write import values as write_values

    assert set(kb_util.READ_OPS) == set(write_ops.READ_OPS) == {kb_util.OP_RENDER_CITATION}
    assert set(kb_util.WRITE_OPS).isdisjoint(kb_util.READ_OPS)
    assert set(write_ops.OPS) | set(write_ops.READ_OPS) == set(write_values.OP_FIELDS)


def test_the_read_op_binds_to_its_own_adapter_and_takes_no_create() -> None:
    """It prints rather than writes, so it is not on the write adapter's path."""
    handlers = _write_handlers()

    assert handlers[kb_util.OP_RENDER_CITATION] is kb_util._handle_render_citation
    assert kb_util._handle_render_citation not in {handlers[op] for op in handlers if op != kb_util.OP_RENDER_CITATION}
    assert "--create" not in _options_of(kb_util.OP_RENDER_CITATION)


def test_every_write_op_is_a_subcommand_bound_to_the_one_write_adapter() -> None:
    """One adapter, nine subcommands: the surface cannot bind an op to nothing."""
    handlers = _write_handlers()

    assert set(kb_util.WRITE_OPS) <= set(handlers)
    assert {handlers[op] for op in kb_util.WRITE_OPS} == {kb_util._handle_write_op}
    # And the build ops are not on it: a root-discovery mistake on one op must
    # not be reachable from another.
    assert kb_util._handle_write_op not in {handlers[op] for op in handlers if op not in kb_util.WRITE_OPS}


def test_values_is_required_on_every_metadata_op_and_declared_on_no_other() -> None:
    """One transport, and only where it belongs.

    Every op — the scalar-only ones included — reads its values from a file,
    because that is the signature ``kb_write.ops`` publishes and because one
    transport across the nine is what lets a batch arrive as one all-or-nothing
    call. The read-only op is on the same transport for the same reason:
    an excerpt is prose, and prose travels in a file.
    """
    handlers = _write_handlers()
    metadata_ops = set(kb_util.WRITE_OPS) | set(kb_util.READ_OPS)

    for op in metadata_ops:
        assert kb_util.VALUES_FLAG in _options_of(op), op
    for op in set(handlers) - metadata_ops:
        assert kb_util.VALUES_FLAG not in _options_of(op), op


def test_create_is_declared_exactly_where_a_register_may_be_created() -> None:
    """Creation is acknowledged, and the acknowledgement is unspellable elsewhere.

    The admissible set is read off ``Op.creates_register`` rather than listed
    here, so the surface and the semantics cannot come to disagree about which
    ops may bring a register into being.
    """
    from kb_tools.kb_write import ops as write_ops

    handlers = _write_handlers()
    creators = {name for name, op in write_ops.OPS.items() if op.creates_register}
    assert creators == {
        kb_util.OP_INSERT_CLAIM_ENTRY,
        kb_util.OP_INSERT_SUPPORT_ENTRY,
        kb_util.OP_INSERT_WORK_ENTRY,
    }

    for op in handlers:
        assert ("--create" in _options_of(op)) is (op in creators), op


def test_every_metadata_op_carries_both_a_help_line_and_a_description() -> None:
    """A subcommand is described in the op list and on its own page.

    The two are no longer one string: ``description`` opens with the help line
    and then names the op's value vocabulary, which the op list has no room for.
    """
    action = next(a for a in kb_util.build_parser()._actions if isinstance(a, argparse._SubParsersAction))
    listed = {choice.dest: choice.help for choice in action._choices_actions}

    for op in (*kb_util.WRITE_OPS, *kb_util.READ_OPS):
        assert listed[op], op
        assert action.choices[op].description.startswith(listed[op]), op


def test_every_metadata_ops_description_names_its_own_value_vocabulary() -> None:
    """``--help`` names the op's value keys, and names them FROM ``OP_FIELDS``.

    The keys are not compared against a list written here — that would be a
    third copy of a closed set that already has one owner, and it would drift in
    exactly the way a hand-typed help string does. The key set is read back out
    of the rendered description and required to equal the op's own row, with the
    required half marked. A key added to an op tomorrow reaches ``--help`` with
    no edit anywhere; one that does not fails here.
    """
    from kb_tools.kb_write import values as write_values

    action = next(a for a in kb_util.build_parser()._actions if isinstance(a, argparse._SubParsersAction))

    for op in (*kb_util.WRITE_OPS, *kb_util.READ_OPS):
        description = action.choices[op].description
        rendered = description.split("Value keys: ", 1)[1].split(" (* required)", 1)[0]
        spelled = [key.strip() for key in rendered.split(",")]
        fields = write_values.OP_FIELDS[op]
        assert spelled == [f"{f.name}*" if f.required else f.name for f in fields], op
        # ...and the marking is the vocabulary's own rather than a guess: every
        # key the row calls required carries the star, and no other key does.
        assert {k.rstrip("*") for k in spelled if k.endswith("*")} == {f.name for f in fields if f.required}, op


def test_a_values_flag_abbreviation_is_a_usage_error(tmp_path: Path) -> None:
    """``allow_abbrev=False`` reaches the subparsers added last, not only the first ones.

    The partial in ``build_parser`` is what makes that structural rather than
    remembered, and a nine-op addition is exactly the occasion on which a
    per-call kwarg would have been forgotten once.
    """
    repo = _pipeline_repo(tmp_path / "consumer")
    values = tmp_path / "values.toml"
    values.write_text("[[entry]]\nid = 'clm-aa1111'\nrigor = 0.5\n", encoding="utf-8")

    result = _run_installer(repo, "set-rigor", "--value", str(values))

    assert result.returncode == 2
    assert "usage:" in result.stderr
    assert "--values" in result.stderr


# --- the three exit codes, through a real process --------------------------

_CLAIM_VALUES = """\
[[entry]]
register = "claim-quality.md"
title = "{title}"
rigor = 0.5
rationale = "{title} was written by the fixture."
"""


def _values_file(tmp_path: Path, name: str, text: str) -> Path:
    """A values file, written **outside** the KB: it is an input to the tool, not content."""
    path = tmp_path / name
    path.write_text(text, encoding="utf-8")
    return path


def test_a_written_insert_exits_zero_and_prints_the_id_it_minted(tmp_path: Path) -> None:
    """The control for the two refusal cases below, proving the write path prints the minted id.

    Without it, an exit 7 downstream would be evidence only that the harness
    cannot reach the write path at all.
    """
    repo = _pipeline_repo(tmp_path / "consumer")
    values = _values_file(tmp_path, "values.toml", _CLAIM_VALUES.format(title="A Fixture Claim"))

    result = _run_installer(repo, "insert-claim-entry", "--create", "--values", str(values))

    assert result.returncode == 0, result.stderr
    minted = [line for line in result.stdout.splitlines() if f" {kb_util.FACT} " in line and "minted" in line]
    assert len(minted) == 1, result.stdout
    node_id = minted[0].split()[-2]
    register = repo / "kb-root" / "claim-quality.md"
    assert f"<!-- id: {node_id} -->" in register.read_text(encoding="utf-8")


def test_a_refused_write_exits_seven_and_reports_the_offending_field(tmp_path: Path) -> None:
    """Exit 7 out of the shipped entry point, with the live tree untouched."""
    repo = _pipeline_repo(tmp_path / "consumer")
    values = _values_file(tmp_path, "values.toml", "[[entry]]\nid = 'clm-aa1111'\nrigor = 47\n")
    before = _tree_snapshot(repo)

    result = _run_installer(repo, "set-rigor", "--values", str(values))

    assert result.returncode == 7, result.stderr
    assert [line for line in result.stdout.splitlines() if f" {kb_util.FAIL} " in line and "rigor" in line]
    assert _tree_snapshot(repo) == before


# The contended write, driven in a child process. The seam — a second
# writer completing between this op's read and its replace — has no expression
# in argv, so the interleaving is forced the way `test_kb_write_ops.py` forces
# it, from inside the first writer's splice. Everything from `main()` outward is
# the shipped path: the argv, the parse, the adapter, the op, and the code
# leaving the process. What this proves that the in-process test cannot is that
# an 8 survives `main()` and becomes an exit status, rather than being clamped
# to 1 or swallowed by the FileNotFoundError wrapper.
_CONTENTION_CHILD = '''\
"""Force one interleaving and let the resulting exit code leave the process."""

import sys
from unittest import mock

from kb_tools import kb_util
from kb_tools.kb_write import ops

first, second = sys.argv[1], sys.argv[2]
real = ops._insert_all
inner = []


def hooked(entries):
    splice = real(entries)

    def wrapped(text):
        candidate = splice(text)
        if not inner:
            with mock.patch.object(ops, "_insert_all", real):
                inner.append(kb_util.main(["insert-claim-entry", "--values", second]))
        return candidate

    return wrapped


with mock.patch.object(ops, "_insert_all", hooked):
    code = kb_util.main(["insert-claim-entry", "--values", first])

print(f"inner={inner}", file=sys.stderr)
raise SystemExit(code)
'''


def test_a_contended_write_exits_eight_out_of_a_real_process(tmp_path: Path) -> None:
    """Exit 8, and the one code a caller must not answer by re-authoring values."""
    repo = _pipeline_repo(tmp_path / "consumer")
    seed = _values_file(tmp_path, "seed.toml", _CLAIM_VALUES.format(title="The Seed Claim"))
    assert _run_installer(repo, "insert-claim-entry", "--create", "--values", str(seed)).returncode == 0

    first = _values_file(tmp_path, "first.toml", _CLAIM_VALUES.format(title="Writer A"))
    second = _values_file(tmp_path, "second.toml", _CLAIM_VALUES.format(title="Writer B"))
    child = _values_file(tmp_path, "contend.py", _CONTENTION_CHILD)

    result = subprocess.run(
        [sys.executable, str(child), str(first), str(second)],
        cwd=repo,
        env=_subprocess_env(),
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 8, f"stdout={result.stdout}\nstderr={result.stderr}"
    assert "inner=[0]" in result.stderr, result.stderr
    # Nothing of writer A's landed, and the register carries exactly the two
    # entries that were actually written.
    register = (repo / "kb-root" / "claim-quality.md").read_text(encoding="utf-8")
    assert register.count("<!-- id: ") == 2
    assert "Writer A" not in register
    assert "Writer B" in register


# --- the read-only op, end to end against the gate it must satisfy ---------
#
# "A non-matching excerpt refuses; a matching one prints a string that passes
# `verify_citations` unmodified." Both halves are driven through the shipped
# entry points — the CLI subprocess for the op, the gate's own CLI for the
# verdict — because a printed string that satisfies an in-process
# reimplementation of the gate satisfies nothing.

_CITED = """\
<!-- kb-frontmatter
kind: index
-->

# Part 3 Claim Quality

## Second Claim

The load-bearing clause lives here, stated plainly.
"""

_CITING = """\
<!-- kb-frontmatter
kind: index
-->

# Part 3

Established elsewhere: CITATION.
"""


def _citation_kb(tmp_path: Path) -> Path:
    """A repo whose KB holds a cited section and a citing index beside it.

    Both documents are ``kind: index``: a leaf's BODY is out of the citation
    gate's scope, so a citation spliced into one would be checked by nothing
    and the whole proof would be vacuous.
    """
    repo = _pipeline_repo(tmp_path / "consumer")
    part3 = repo / "kb-root" / "part3"
    part3.mkdir(parents=True)
    (part3 / "claim-quality.md").write_text(_CITED, encoding="utf-8")
    (part3 / "index.md").write_text(_CITING, encoding="utf-8")
    return repo


def _verify_citations(repo: Path) -> subprocess.CompletedProcess:
    """The gate, through its own shipped CLI."""
    return subprocess.run(
        [sys.executable, "-m", "kb_tools.verify_citations", "--kb-root", str(repo / "kb-root")],
        cwd=repo,
        env=_subprocess_env(),
        capture_output=True,
        text=True,
        check=False,
    )


_CITATION_VALUES = """\
[[entry]]
excerpt = "The load-bearing clause lives here"
cited-document = "part3/claim-quality.md"
anchor = "second-claim"
citing-document = "part3/index.md"
"""


def test_a_rendered_citation_passes_the_gate_unmodified(tmp_path: Path) -> None:
    """The done-condition's matching half, and the reason the citing document is a value.

    The printed target is ``claim-quality.md`` — relative to the document the
    citation is written into — because that is how ``verify_citations`` and
    ``verify_md_links`` both resolve a link. The kb-root-relative spelling of
    the same target is shown failing the gate at the end, which is what makes
    the relative spelling a requirement rather than a preference.
    """
    repo = _citation_kb(tmp_path)
    values = _values_file(tmp_path, "citation.toml", _CITATION_VALUES)
    citing = repo / "kb-root" / "part3" / "index.md"
    before = _tree_snapshot(repo)

    result = _run_installer(repo, "render-citation", "--values", str(values))

    assert result.returncode == 0, result.stderr
    printed = result.stdout.splitlines()
    assert printed == ['["The load-bearing clause lives here"](claim-quality.md#second-claim)'], result.stdout
    # Read-only: not one byte under the repo moved, the KB included.
    assert _tree_snapshot(repo) == before

    citing.write_text(_CITING.replace("CITATION", printed[0]), encoding="utf-8")
    gate = _verify_citations(repo)
    assert gate.returncode == 0, gate.stdout + gate.stderr

    # Teeth, both ways. A kb-root-relative target — the spelling a three-value
    # op could have printed — resolves to part3/part3/… and is a broken
    # referent; and a mutated excerpt is a failed quotation. If either of these
    # passed, the green above would be evidence of nothing.
    citing.write_text(_CITING.replace("CITATION", printed[0].replace("](", "](part3/")), encoding="utf-8")
    assert _verify_citations(repo).returncode == 1
    citing.write_text(_CITING.replace("CITATION", printed[0].replace("load-bearing", "load bearing")), encoding="utf-8")
    assert _verify_citations(repo).returncode == 1


def test_a_non_matching_excerpt_refuses_with_nothing_on_stdout(tmp_path: Path) -> None:
    """The done-condition's refusing half: exit 7, and no citation to transcribe.

    Nothing on stdout is part of the contract, not an accident of this case: a
    caller pipes this op's stdout into a document, so a refusal that printed a
    diagnostic there would put the diagnostic in the corpus.
    """
    repo = _citation_kb(tmp_path)
    values = _values_file(
        tmp_path, "citation.toml", _CITATION_VALUES.replace("clause lives here", "clause was never written")
    )
    before = _tree_snapshot(repo)

    result = _run_installer(repo, "render-citation", "--values", str(values))

    assert result.returncode == 7
    assert result.stdout == ""
    assert [line for line in result.stderr.splitlines() if f" {kb_util.FAIL} " in line and "excerpt" in line]
    assert _tree_snapshot(repo) == before


def test_the_published_invocation_constants_are_what_the_cards_render(tmp_path: Path) -> None:
    """Both published invocations, pinned against the renderers that consume them.

    ``kb_pipeline`` builds every card command and ``kb_driver.steps`` fills every
    brief slot from ``INVOCATION``; ``kb_driver.baton`` builds every ``THEN RUN:``
    line and ``kb_driver.watch`` its restore hint from ``DRIVER_INVOCATION``.
    Pinning each against the text an agent actually reads is what keeps one
    invocation from becoming several spellings of itself — and the literal here
    is what keeps a spelling that drops the ``PYTHONPATH`` prefix, and so does
    not resolve where the relaying session stands, from passing.
    """
    repo = _pipeline_repo(tmp_path / "consumer")
    stage = {s.id: s for s in kb_pipeline.STAGES}["phase-3a"]

    record = [line for line in kb_pipeline.card_lines(stage, repo) if " · record: " in line][0]

    assert f"record: {kb_util.INVOCATION} " in record
    assert _OP_PREFIX == f"{kb_util.INVOCATION} "

    assert kb_util.DRIVER_INVOCATION == "PYTHONPATH=.claude/agents python3 -m kb_tools.kb_driver"
    resume = baton.render(baton.EXIT_TRANSPORT, baton.BatonContext(invocation="--config cfg.toml"))
    assert f"{kb_util.DRIVER_INVOCATION} run --config cfg.toml" in resume


# ---------------------------------------------------------------------------
# The build's opening gate (show-confirmation)
#
# One read in front of the gate; what the answer releases is `start-build`,
# tested above. What is asserted below is what the read leaves behind — the
# render's content, and that it writes nothing outside the scratch directory
# preflight creates — never the wording of a line.
# ---------------------------------------------------------------------------


def _bare_repo(root: Path, *, sources: bool = True) -> Path:
    """A committed consumer repo with no runner file and no KB.

    What ``show-confirmation`` reads its tri-state off: the confirmation renders
    before anything has been built, and ``kb-root/`` not existing yet is one of
    the things it reports.
    """
    files = {"sources/book.tex": "\\documentclass{article}\n"} if sources else {}
    return _git_repo(root, files=files)


def _charter_values(repo: Path, charter: str, name: str = "charter.toml") -> Path:
    """A charter values file in the repo's scratch tree, where an input belongs.

    Written under ``.claude-temp/`` deliberately: it is the call's input rather
    than a build artifact, and an uncommitted file anywhere else would fail the
    clean-worktree check the seed that follows this read runs.
    """
    path = repo / kb_util.SCRATCH_DIRNAME / name
    path.parent.mkdir(parents=True, exist_ok=True)
    body = charter.replace("'''", "")
    path.write_text(f"[[entry]]\ncharter = '''\n{body}'''\n", encoding="utf-8")
    return path


# --- show-confirmation: the render is the message --------------------------


def test_confirmation_carries_the_parse_the_checklist_and_the_environment(tmp_path: Path) -> None:
    """One call renders everything the confirmation turns on.

    The sequence this replaced ran two ops and composed the rest, which is where
    the ordering defect lived. Every fact the answer depends on comes back from
    one call, so there is nothing left to assemble and nothing to assemble it in
    the wrong order.
    """
    repo = _bare_repo(tmp_path / "consumer")
    values = _charter_values(repo, "Build the KB from sources/book.tex.\n")

    result = _run_installer(repo, "show-confirmation", "--source", "sources/book.tex", "--charter-values", str(values))

    assert result.returncode == 0, f"stdout={result.stdout}\nstderr={result.stderr}"
    assert str(repo.resolve()) in result.stdout
    assert str((repo / "sources" / "book.tex").resolve()) in result.stdout
    assert str(kb_util.kb_root(repo)) in result.stdout
    # The determination, the run map, the unsettled facts, and the environment.
    assert "does not exist yet" in result.stdout
    assert [stage_id for _, stage_id in _checklist(result.stdout)] == list(kb_pipeline.STAGE_IDS)
    assert "Default" in result.stdout
    assert "[preflight] PASS" in result.stdout
    # The charter is quoted back for the user to check what will be carried.
    assert "[charter] Build the KB from sources/book.tex." in result.stdout


def test_confirmation_names_every_stage_the_table_gates_on_the_user(tmp_path: Path) -> None:
    """Where the user is expected reaches the render, and comes from the table.

    Compared as the pair rather than the bare id, so an id that happens to be a
    substring of another stage's cannot read a gate as standing where it does
    not.
    """
    repo = _bare_repo(tmp_path / "consumer")

    result = _run_installer(repo, "show-confirmation", "--source", "sources")

    assert result.returncode == 0, result.stderr
    named = {stage.id for stage in kb_pipeline.STAGES if f"{stage.id} ({stage.display})" in result.stdout}
    assert named == {stage.id for stage in kb_pipeline.STAGES if stage.user_gate}


def test_confirmation_writes_nothing_but_the_scratch_directory(tmp_path: Path) -> None:
    """A read before a gate: no KB, no charter, no ledger, no runner file."""
    repo = _bare_repo(tmp_path / "consumer")
    (repo / kb_util.SCRATCH_DIRNAME).mkdir()
    before = _tree_snapshot(repo)

    result = _run_installer(repo, "show-confirmation", "--source", "sources")

    assert result.returncode == 0, result.stderr
    assert _tree_snapshot(repo) == before
    assert not (repo / "kb-root").exists()
    assert not (repo / kb_pipeline.CHARTER_RELPATH).exists()
    assert not _subjects(repo)


def test_confirmation_names_the_runner_default_only_where_it_is_unsettled(tmp_path: Path) -> None:
    """A fact the repository already answers is not a question to put to the user.

    The runner is the only entry the section carries, so a repository that
    answers it leaves nothing unsettled: the heading goes with the entries
    rather than standing over an empty section. What the rest of the answer
    becomes is not conditional on that, and is still said.
    """
    bare = _run_installer(_bare_repo(tmp_path / "bare"), "show-confirmation", "--source", "sources")
    with_runner = _run_installer(
        _git_repo(tmp_path / "runner", files={"justfile": _JUSTFILE_BODY, "sources/b.tex": "x\n"}),
        "show-confirmation",
        "--source",
        "sources",
    )

    assert bare.returncode == 0 and with_runner.returncode == 0
    # The repo with no runner file is asked, and told what it gets by default.
    assert "Task runner" in _unsettled_block(bare.stdout)
    assert kb_util.runner_filename(kb_util.DEFAULT_RUNNER) in _unsettled_block(bare.stdout)
    # The repo carrying a justfile is not asked at all, and the section it
    # would have been the only entry of does not print.
    assert "Task runner" not in with_runner.stdout
    assert "Unsettled" not in with_runner.stdout
    assert "you say is charter text" in with_runner.stdout


def _unsettled_block(stdout: str) -> str:
    """The confirmation's unsettled-facts lines, as one blob."""
    lines = stdout.splitlines()
    opening = [i for i, line in enumerate(lines) if line.startswith(f"{kb_pipeline.CONFIRMATION_TAG} Unsettled")]
    assert len(opening) == 1, stdout
    tail = lines[opening[0] :]
    return "\n".join(
        line for line in tail if line.startswith(kb_pipeline.CONFIRMATION_TAG) and "environment checks" not in line
    )


def test_confirmation_reports_kb_root_state_with_what_it_means_for_opening_a_build(tmp_path: Path) -> None:
    """The tri-state, and the one consequence a reader can still act on.

    A populated kb-root/ is what the driver's launch guard refuses over, so the
    confirmation says so where there is still a choice to make — and says
    nothing of the kind where the tree is absent, there being no refusal to
    warn about. It states and never gates: whether this invocation is opening a
    build or continuing one is the ledger's answer, and the checklist printed
    below carries it.
    """
    empty = _run_installer(_bare_repo(tmp_path / "empty"), "show-confirmation", "--source", "sources")
    populated = _git_repo(tmp_path / "populated", files={"kb-root/entry-point.md": "# KB\n", "sources/b.tex": "x\n"})

    with_tree = _run_installer(populated, "show-confirmation", "--source", "sources")

    assert "does not exist yet" in empty.stdout
    assert "refused" not in empty.stdout
    assert "holds a document tree" in with_tree.stdout
    assert "a build opened over it is refused" in with_tree.stdout
    assert with_tree.returncode == 0, with_tree.stderr


def test_confirmation_blocks_on_a_source_that_is_not_there(tmp_path: Path) -> None:
    """The one input from outside the repository, checked where it can still be fixed."""
    repo = _bare_repo(tmp_path / "consumer")

    result = _run_installer(repo, "show-confirmation", "--source", "sources/book.tex", "--source", "sources/gone.tex")

    assert result.returncode == 1
    assert "gone.tex" in result.stdout
    assert "does not exist" in result.stdout


def test_confirmation_blocks_on_a_preflight_failure(tmp_path: Path) -> None:
    repo = _git_repo(tmp_path / "consumer", docent=False, files={"sources/b.tex": "x\n"})

    result = _run_installer(repo, "show-confirmation", "--source", "sources")

    assert result.returncode == 1
    assert "FAIL" in result.stdout and "docent-commands" in result.stdout


@pytest.mark.parametrize(
    ("body", "reason"),
    [
        ("op = 'show-confirmation'\n[[entry]]\ncharter = '''x'''\n", "op"),
        ("[[entry]]\ncharter = '''a'''\n[[entry]]\ncharter = '''b'''\n", "one build has one charter"),
        ("[[entry]]\ncharter_text = '''x'''\n", "charter_text"),
        ("[[entry]]\ncharter = '''   '''\n", "charter"),
        ("[[entry\n", "readable"),
    ],
)
def test_a_refused_charter_values_file_writes_nothing_and_names_the_key(tmp_path: Path, body: str, reason: str) -> None:
    """The values grammar refuses before the op reaches the repository."""
    repo = _bare_repo(tmp_path / "consumer")
    values = repo / kb_util.SCRATCH_DIRNAME / "bad.toml"
    values.parent.mkdir(parents=True, exist_ok=True)
    values.write_text(body, encoding="utf-8")
    before = _tree_snapshot(repo)

    result = _run_installer(repo, "show-confirmation", "--source", "sources", "--charter-values", str(values))

    assert result.returncode == 2
    assert reason in result.stderr
    assert _tree_snapshot(repo) == before
    assert not _subjects(repo)


def test_the_charter_lands_outside_the_scratch_tree_and_outside_the_kb(tmp_path: Path) -> None:
    """Where the charter lives is the tool's answer, and it is a durable one.

    A ledger entry names the charter path permanently, so scratch cannot hold
    it; refresh and both verifiers walk ``kb-root/`` as authored content, so the
    KB cannot either.
    """
    relpath = PurePosixPath(kb_pipeline.CHARTER_RELPATH)

    assert not relpath.is_absolute()
    assert kb_util.SCRATCH_DIRNAME not in relpath.parts
    assert kb_util.KB_DIRNAME not in relpath.parts
