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

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
from kb_tools import kb_util

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


def test_find_repo_root_without_kb_root_raises(tmp_path: Path) -> None:
    repo = _make_repo(tmp_path / "consumer", kb=False)
    with pytest.raises(kb_util.RepoRootError, match="kb-root"):
        kb_util.find_repo_root(repo)


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
            "kb_tools.verify_kb_metadata, kb_tools.verify_md_links, kb_tools.mint_claim_ids, "
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
# Runner-target installer (--install-targets / --uninstall-targets)
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
    result = _run_installer(repo / "kb-root", "--install-targets")
    assert result.returncode == 0, result.stderr
    assert "installed" in result.stdout
    text = (repo / "justfile").read_text(encoding="utf-8")
    assert text == _JUSTFILE_BODY + "\n" + kb_util.INSTALL_LINE_JUST + "\n"


def test_install_appends_include_line_to_existing_makefile(tmp_path: Path) -> None:
    repo = _make_repo(tmp_path / "consumer")
    (repo / "Makefile").write_text(_MAKEFILE_BODY, encoding="utf-8")
    result = _run_installer(repo / "kb-root", "--install-targets")
    assert result.returncode == 0, result.stderr
    text = (repo / "Makefile").read_text(encoding="utf-8")
    assert text == _MAKEFILE_BODY + "\n" + kb_util.INSTALL_LINE_MAKE + "\n"


def test_install_prefers_justfile_when_both_runner_files_exist(tmp_path: Path) -> None:
    repo = _make_repo(tmp_path / "consumer")
    (repo / "justfile").write_text(_JUSTFILE_BODY, encoding="utf-8")
    (repo / "Makefile").write_text(_MAKEFILE_BODY, encoding="utf-8")
    result = _run_installer(repo, "--install-targets")
    assert result.returncode == 0, result.stderr
    assert kb_util.INSTALL_LINE_JUST in (repo / "justfile").read_text(encoding="utf-8")
    # The Makefile is byte-untouched.
    assert (repo / "Makefile").read_text(encoding="utf-8") == _MAKEFILE_BODY


def test_install_runner_flag_overrides_probe_order(tmp_path: Path) -> None:
    repo = _make_repo(tmp_path / "consumer")
    (repo / "justfile").write_text(_JUSTFILE_BODY, encoding="utf-8")
    (repo / "Makefile").write_text(_MAKEFILE_BODY, encoding="utf-8")
    result = _run_installer(repo, "--install-targets", "--runner", "make")
    assert result.returncode == 0, result.stderr
    assert kb_util.INSTALL_LINE_MAKE in (repo / "Makefile").read_text(encoding="utf-8")
    assert (repo / "justfile").read_text(encoding="utf-8") == _JUSTFILE_BODY


def test_install_refuses_when_no_runner_file_and_no_flag(tmp_path: Path) -> None:
    repo = _make_repo(tmp_path / "consumer")
    result = _run_installer(repo, "--install-targets")
    assert result.returncode == 2
    assert "--runner" in result.stderr
    assert list(repo.iterdir()) and not (repo / "justfile").exists() and not (repo / "Makefile").exists()


def test_install_creates_runner_file_with_explicit_flag(tmp_path: Path) -> None:
    for runner, filename, line in (
        ("just", "justfile", kb_util.INSTALL_LINE_JUST),
        ("make", "Makefile", kb_util.INSTALL_LINE_MAKE),
    ):
        repo = _make_repo(tmp_path / runner)
        result = _run_installer(repo, "--install-targets", "--runner", runner)
        assert result.returncode == 0, result.stderr
        assert "created" in result.stdout
        assert line in (repo / filename).read_text(encoding="utf-8").splitlines()


def test_install_is_idempotent(tmp_path: Path) -> None:
    repo = _make_repo(tmp_path / "consumer")
    (repo / "justfile").write_text(_JUSTFILE_BODY, encoding="utf-8")
    assert _run_installer(repo, "--install-targets").returncode == 0
    once = (repo / "justfile").read_bytes()
    again = _run_installer(repo, "--install-targets")
    assert again.returncode == 0, again.stderr
    assert "already installed" in again.stdout
    assert (repo / "justfile").read_bytes() == once


def test_uninstall_removes_installed_line(tmp_path: Path) -> None:
    repo = _make_repo(tmp_path / "consumer")
    (repo / "Makefile").write_text(_MAKEFILE_BODY, encoding="utf-8")
    assert _run_installer(repo, "--install-targets").returncode == 0
    result = _run_installer(repo, "--uninstall-targets")
    assert result.returncode == 0, result.stderr
    assert "uninstalled" in result.stdout
    assert kb_util.INSTALL_LINE_MAKE not in (repo / "Makefile").read_text(encoding="utf-8")


def test_uninstall_reports_not_installed_without_error(tmp_path: Path) -> None:
    # Runner file present, line absent.
    with_runner = _make_repo(tmp_path / "a")
    (with_runner / "justfile").write_text(_JUSTFILE_BODY, encoding="utf-8")
    result = _run_installer(with_runner, "--uninstall-targets")
    assert result.returncode == 0, result.stderr
    assert "not installed" in result.stdout
    assert (with_runner / "justfile").read_text(encoding="utf-8") == _JUSTFILE_BODY
    # No runner file at all.
    bare = _make_repo(tmp_path / "b")
    result = _run_installer(bare, "--uninstall-targets")
    assert result.returncode == 0, result.stderr
    assert "not installed" in result.stdout


def test_install_uninstall_round_trip_is_byte_exact(tmp_path: Path) -> None:
    repo = _make_repo(tmp_path / "consumer")
    (repo / "justfile").write_text(_JUSTFILE_BODY, encoding="utf-8")
    original = (repo / "justfile").read_bytes()
    assert _run_installer(repo, "--install-targets").returncode == 0
    installed = (repo / "justfile").read_bytes()
    # Install touches nothing but the appended blank line + canonical line.
    assert installed == original + b"\n" + kb_util.INSTALL_LINE_JUST.encode() + b"\n"
    assert _run_installer(repo, "--uninstall-targets").returncode == 0
    assert (repo / "justfile").read_bytes() == original
