"""Tests for the ledger adapter, against a temp git repo and the real ``kb_util``.

Nothing here is mocked: every case builds a consuming repo with ``git init`` in
``tmp_path`` and drives the shipped ``python3 -m kb_tools.kb_util`` surface as a
subprocess, which is what the adapter does in a real run. The two properties
under test are the rc→exit mapping and the display relay: the tool's complete
stdout reaches the driver's stdout byte-for-byte, never trimmed and never
re-rendered.
"""

import json
import logging
import os
import shutil
import subprocess
import sys
from collections.abc import Iterator
from pathlib import Path

import pytest

from kb_tools import kb_pipeline, kb_util
from kb_tools.kb_driver import baton, ledger, runlog, transport
from kb_tools.kb_write import ops as write_ops

_THIS_DIR = Path(__file__).resolve().parent
_PKG_PARENT = _THIS_DIR.parent.parent

_CHARTER = "docs/charter.md"

# A Makefile carrying the canonical include line — which is what
# ``targets_installed`` looks for — plus locally-defined targets, so the runner
# path is exercised without needing the toolchain installed under
# ``.claude/agents/`` in the fixture. The include is non-fatal by design.
_MAKEFILE_GREEN = f"{kb_util.INSTALL_LINE_MAKE}\n\nkb-verify:\n\t@echo verifying\n"
_MAKEFILE_RED = f"{kb_util.INSTALL_LINE_MAKE}\n\nkb-verify:\n\t@echo 'dead link'; exit 1\n"
# The same red gate, printing one byte that is not valid UTF-8 (0xE9, latin-1
# 'é'). A consuming repo's recipe prints whatever its verifiers print, and the
# driver does not get to assume that is decodable.
_MAKEFILE_UNDECODABLE = f"{kb_util.INSTALL_LINE_MAKE}\n\nkb-verify:\n\t@printf 'dead link in caf\\351.md\\n'; exit 1\n"


def _git(root: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=root, check=True, capture_output=True)


def _repo(root: Path, *, docent: bool = True, files: dict[str, str] | None = None) -> Path:
    """A committed, preflight-clean consuming repo.

    Identity and signing are pinned in the repo's local config: the pipeline
    makes its own commits and must do so exactly as it would in a consumer's
    repo.
    """
    root.mkdir(parents=True, exist_ok=True)
    _git(root, "init", "-q")
    for key, value in (
        ("user.email", "fixture@example.invalid"),
        ("user.name", "fixture"),
        ("commit.gpgsign", "false"),
    ):
        _git(root, "config", key, value)
    if docent:
        commands = root / ".claude" / "commands"
        commands.mkdir(parents=True)
        for name in kb_util.DOCENT_COMMAND_FILENAMES:
            (commands / name).write_text(f"# {name}\n", encoding="utf-8")
    (root / ".gitignore").write_text(".claude-temp/\n", encoding="utf-8")
    for relpath, content in (files or {}).items():
        target = root / relpath
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
    _git(root, "add", "-A")
    _git(root, "commit", "-qm", "seed")
    return root


#: A minimal conforming document tree, frontmatter and all — ``graph-init``'s
#: precondition. The verb initialises claim-graph metadata over documents, so a
#: fixture with none exercises only its refusal.
_DOCUMENT_TREE: dict[str, str] = {
    "kb-root/entry-point.md": (
        "<!-- kb-frontmatter\nkind: entry-point\n-->\n\n# Entry Point\n\n- [Volume](vol/index.md)\n"
    ),
    "kb-root/vol/index.md": (
        "[\u2191 Entry Point](../entry-point.md)\n\n<!-- kb-frontmatter\nkind: index\n-->\n\n"
        "# Volume\n\n- [Leaf](leaf.md)\n"
    ),
    "kb-root/vol/leaf.md": (
        '[\u2191 Volume](index.md)\n\n<!-- kb-frontmatter\nkind: leaf\nno-claim: "fixture leaf; it '
        'states no result"\n-->\n\n# Leaf\n\nBody text.\n'
    ),
}


def _tree_repo(root: Path, *, docent: bool = True, files: dict[str, str] | None = None) -> Path:
    """``_repo`` carrying the document tree ``graph-init`` requires."""
    return _repo(root, docent=docent, files={**_DOCUMENT_TREE, **(files or {})})


def _seeded_repo(root: Path, **files: str) -> Path:
    """A repo with the head's product on disk: the tree, a spine, and a charter.

    The ledger records against what a stage produced, so a repo standing in for
    a build in flight carries the document tree as well as the spine.
    """
    return _repo(root, files={**_DOCUMENT_TREE, "kb-root/.index/.keep": "", _CHARTER: "# Charter\n", **files})


def _kb_util_directly(repo: Path, *args: str) -> subprocess.CompletedProcess[str]:
    """The same invocation the adapter makes, run here — the byte-identity reference."""
    return subprocess.run(
        [sys.executable, "-m", "kb_tools.kb_util", *args],
        cwd=repo,
        env={**os.environ, "PYTHONPATH": str(_PKG_PARENT), "PYTHONDONTWRITEBYTECODE": "1"},
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
    )


# ---------------------------------------------------------------------------
# pre.preflight
# ---------------------------------------------------------------------------


def test_preflight_passes_on_a_clean_repo(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    repo = _repo(tmp_path / "consumer")

    outcome = ledger.preflight(repo)

    assert outcome.ok
    assert outcome.exit_code == baton.EXIT_OK
    assert "[preflight] PASS: environment ready." in capsys.readouterr().out


def test_preflight_failure_is_exit_14_carrying_its_restore_lines(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    # No docent commands: a FAIL item that names its own restoring action.
    repo = _repo(tmp_path / "consumer", docent=False)

    outcome = ledger.preflight(repo)

    assert outcome.exit_code == baton.EXIT_ENVIRONMENT
    out = capsys.readouterr().out
    assert "[preflight] FAIL docent-commands" in out
    assert "restore: run 'just install" in out
    assert outcome.detail[0] == f"kb_util {kb_util.OP_PREFLIGHT} exited 1"


# ---------------------------------------------------------------------------
# seed.graph-init
# ---------------------------------------------------------------------------


def test_graph_init_initialises_over_a_document_tree(tmp_path: Path) -> None:
    repo = _tree_repo(tmp_path / "consumer", files={"justfile": "default:\n    @true\n"})

    outcome = ledger.graph_init(repo)

    assert outcome.ok, outcome.detail
    assert (repo / "kb-root" / ".index" / "claims.jsonl").is_file()
    assert not (repo / "kb-root" / ".index" / "SCHEMA.md").exists()


def test_graph_init_with_no_document_tree_is_exit_14(tmp_path: Path) -> None:
    """rc 3: kb-root holds nothing to initialise over, and no barrier asks about it.

    The one corrective act is to run the document-graph front end over the
    sources — upstream work, not a question this run could put to anybody — so
    it halts on the environment rung rather than stopping for an answer.
    """
    repo = _repo(tmp_path / "consumer", files={"justfile": "default:\n    @true\n"})

    outcome = ledger.graph_init(repo)

    assert outcome.exit_code == baton.EXIT_ENVIRONMENT
    assert not outcome.barrier
    assert not outcome.ok


def test_graph_init_blocked_by_preflight_is_exit_14(tmp_path: Path) -> None:
    """rc 2: nothing seeded, and the fault is in the environment."""
    repo = _tree_repo(tmp_path / "consumer", docent=False, files={"justfile": "default:\n    @true\n"})

    outcome = ledger.graph_init(repo)

    assert outcome.exit_code == baton.EXIT_ENVIRONMENT
    assert not (repo / "kb-root" / ".index").exists()


def test_graph_init_with_a_red_verify_is_exit_11(tmp_path: Path) -> None:
    """rc 1: seeding ran and the gates came back red — a gate failure, not an environment one."""
    # A repo-wide dead link fails verify_md_links, which graph-init runs before
    # it reports success.
    repo = _tree_repo(
        tmp_path / "consumer",
        files={"justfile": "default:\n    @true\n", "broken.md": "[missing](nowhere.md)\n"},
    )

    outcome = ledger.graph_init(repo)

    assert outcome.exit_code == baton.EXIT_GATE_RED
    assert outcome.detail[0] == f"kb_util {kb_util.OP_GRAPH_INIT} exited 1"


def test_graph_init_without_a_runner_file_passes_the_configured_runner_through(tmp_path: Path) -> None:
    repo = _tree_repo(tmp_path / "consumer")

    outcome = ledger.graph_init(repo, runner="just")

    assert outcome.ok, outcome.detail
    assert kb_util.INSTALL_LINE_JUST in (repo / "justfile").read_text(encoding="utf-8").splitlines()


# ---------------------------------------------------------------------------
# start.record and the stage records
# ---------------------------------------------------------------------------


def test_start_build_records_the_boundary(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    repo = _seeded_repo(tmp_path / "consumer")

    outcome = ledger.record_start(repo, charter=_CHARTER)

    assert outcome.ok, outcome.detail
    assert "[x] start" in capsys.readouterr().out


def test_start_build_on_a_started_build_reads_as_done(tmp_path: Path) -> None:
    """rc 5: the boundary exists, which is what the row wanted (redo-safe)."""
    repo = _seeded_repo(tmp_path / "consumer")
    assert ledger.record_start(repo, charter=_CHARTER).ok

    again = ledger.record_start(repo, charter=_CHARTER)

    assert again.ok
    assert again.exit_code == baton.EXIT_OK


def test_out_of_order_record_is_exit_14(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """rc 4: the ledger refuses; the render that comes back carries the real position."""
    repo = _seeded_repo(tmp_path / "consumer")

    outcome = ledger.record_stage(repo, stage="phase-5")

    assert outcome.exit_code == baton.EXIT_ENVIRONMENT
    assert "[ ] phase-5" in capsys.readouterr().out
    assert any("predecessors are unrecorded" in line for line in outcome.detail)


def test_recording_a_stage_is_inert_the_second_time(tmp_path: Path) -> None:
    repo = _seeded_repo(tmp_path / "consumer")
    subprocess.run(
        [sys.executable, "-m", "kb_tools.refresh_kb_metadata"],
        cwd=repo,
        env={**os.environ, "PYTHONPATH": str(_PKG_PARENT), "PYTHONDONTWRITEBYTECODE": "1"},
        capture_output=True,
        check=True,
    )
    # The stage after `start`, read off the vocabulary: what is under test is
    # that a second record of one stage is inert, not which stage it was.
    after_start = kb_pipeline.STAGE_IDS[1]
    assert ledger.record_start(repo, charter=_CHARTER).ok
    assert ledger.record_stage(repo, stage=after_start).ok

    again = ledger.record_stage(repo, stage=after_start)

    assert again.ok
    assert "already recorded" in again.stdout


def test_an_unknown_stage_id_is_refused_as_an_environment_fault(tmp_path: Path) -> None:
    """A usage error from the tool (rc 2) is mapped, not left to the fallback baton."""
    repo = _seeded_repo(tmp_path / "consumer")

    outcome = ledger.record_stage(repo, stage="phase-99")

    assert outcome.exit_code == baton.EXIT_ENVIRONMENT


def test_record_start_without_a_charter_records_the_boundary_and_names_none(tmp_path: Path) -> None:
    """A charter is optional, so the boundary lands and its body names no charter."""
    repo = _seeded_repo(tmp_path / "consumer")

    outcome = ledger.record_start(repo, charter="")

    assert outcome.ok, outcome.detail
    subject_and_body = subprocess.run(
        ["git", "log", "-1", "--format=%B"], cwd=repo, capture_output=True, text=True, check=True
    ).stdout
    assert "start" in subject_and_body
    assert "charter" not in subject_and_body


# ---------------------------------------------------------------------------
# The display relay
# ---------------------------------------------------------------------------


def test_relayed_stdout_is_byte_identical_to_the_tools_own(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """The complete verbatim stdout of show-status, across the process boundary."""
    repo = _seeded_repo(tmp_path / "consumer")
    assert ledger.record_start(repo, charter=_CHARTER).ok
    capsys.readouterr()
    reference = _kb_util_directly(repo, kb_util.OP_SHOW_STATUS)
    assert reference.returncode == 0, reference.stderr

    outcome = ledger.show_status(repo)

    relayed = capsys.readouterr().out
    assert outcome.ok
    assert relayed == reference.stdout
    assert outcome.stdout == reference.stdout


def test_a_failing_op_still_relays_its_whole_render(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """The refusal render is display too: it carries the card, which is the fix."""
    repo = _seeded_repo(tmp_path / "consumer")

    outcome = ledger.record_stage(repo, stage="phase-5")

    assert capsys.readouterr().out == outcome.stdout
    assert outcome.stdout.endswith("\n")
    assert "[card] next action" in outcome.stdout


def test_relay_can_be_suppressed_for_a_read_that_is_not_a_transition(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    repo = _seeded_repo(tmp_path / "consumer")

    outcome = ledger.show_status(repo, relay=False)

    assert capsys.readouterr().out == ""
    assert "[kb-build] status:" in outcome.stdout


# ---------------------------------------------------------------------------
# pre.revision-entry
# ---------------------------------------------------------------------------


def test_revision_entry_without_installed_targets_is_exit_14(tmp_path: Path) -> None:
    repo = _seeded_repo(tmp_path / "consumer", **{"justfile": "default:\n    @true\n"})

    outcome = ledger.revision_entry(repo)

    assert outcome.exit_code == baton.EXIT_ENVIRONMENT
    assert any(kb_util.OP_INSTALL_TARGETS in line for line in outcome.detail)


@pytest.mark.skipif(shutil.which("make") is None, reason="the runner target needs make on PATH")
def test_revision_entry_proceeds_over_a_kb_with_no_local_schema(tmp_path: Path) -> None:
    """Entry no longer gates on `.index/SCHEMA.md`.

    This exact repo — targets installed, verify green, no local schema — halted
    at exit 14 before the seed evaporated, on a file `init` no longer writes.
    Entry now reaches `kb-verify`, which is what actually answers whether the
    spine is sound.
    """
    repo = _seeded_repo(tmp_path / "consumer", **{"Makefile": _MAKEFILE_GREEN})
    assert not (repo / "kb-root" / ".index" / "SCHEMA.md").exists()

    outcome = ledger.revision_entry(repo)

    assert outcome.ok, outcome.detail
    assert outcome.exit_code != baton.EXIT_ENVIRONMENT
    assert "verifying" in outcome.stdout, "entry halted before reaching kb-verify"


@pytest.mark.skipif(shutil.which("make") is None, reason="the runner target needs make on PATH")
def test_revision_entry_green_runs_the_runners_verify_target(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    repo = _seeded_repo(tmp_path / "consumer", **{"Makefile": _MAKEFILE_GREEN})

    outcome = ledger.revision_entry(repo)

    assert outcome.ok, outcome.detail
    # The target ran — its report is on the Outcome — but a gate is not a
    # display transition, so nothing of it reached the relay.
    assert "verifying" in outcome.stdout
    assert capsys.readouterr().out == ""


@pytest.mark.skipif(shutil.which("make") is None, reason="the runner target needs make on PATH")
def test_revision_entry_with_a_red_verify_is_exit_11(tmp_path: Path) -> None:
    repo = _seeded_repo(tmp_path / "consumer", **{"Makefile": _MAKEFILE_RED})

    outcome = ledger.revision_entry(repo)

    assert outcome.exit_code == baton.EXIT_GATE_RED


@pytest.mark.skipif(shutil.which("make") is None, reason="the runner target needs make on PATH")
@pytest.mark.parametrize(
    ("makefile", "expected"),
    [(_MAKEFILE_GREEN, baton.EXIT_OK), (_MAKEFILE_RED, baton.EXIT_GATE_RED)],
    ids=["green", "red"],
)
def test_a_gate_report_is_kept_out_of_the_relay_without_changing_the_rc_mapping(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], makefile: str, expected: int
) -> None:
    """The paste instruction is 'everything above this block'; a gate dump must not be in it.

    ``run.py`` writes a red gate's report to the round's findings file and the
    baton names that path, so suppressing the relay bounds the message body
    without losing the report.
    """
    repo = _seeded_repo(tmp_path / "consumer", **{"Makefile": makefile})

    outcome = ledger.run_target(repo, target=kb_util.TARGET_VERIFY)

    assert outcome.exit_code == expected
    assert outcome.stdout.strip() != ""
    assert capsys.readouterr().out == ""


def test_a_repo_with_no_runner_file_has_no_target_to_run(tmp_path: Path) -> None:
    """The raw fallback hint names one module, not the whole gate — it is not a substitute."""
    repo = _seeded_repo(tmp_path / "consumer")

    outcome = ledger.run_target(repo, target=kb_util.TARGET_VERIFY)

    assert outcome.exit_code == baton.EXIT_ENVIRONMENT
    assert any("no justfile or Makefile" in line for line in outcome.detail)


def test_a_runner_file_without_the_kb_include_line_has_no_target_to_run_either(tmp_path: Path) -> None:
    """An uninstalled target is an environment fault, not a red gate.

    The runner answers an unknown recipe the same way a verifier answers a
    broken KB — nonzero — and exit 11 says the KB failed a check it was really
    put through. Spawning first would stop the build as though the knowledge
    base were broken when nothing had been checked at all (the stage half of
    that is covered in the buildout suite by
    ``test_an_environment_fault_stops_the_run_the_same_way_a_red_gate_does``).

    No ``make`` on PATH is needed here, and that is the point: the guard
    answers before anything is spawned.
    """
    repo = _seeded_repo(tmp_path / "consumer", **{"Makefile": "build:\n\t@echo building\n"})
    assert not kb_util.targets_installed(repo)

    outcome = ledger.run_target(repo, target=kb_util.TARGET_VERIFY)

    assert outcome.exit_code == baton.EXIT_ENVIRONMENT
    assert any(kb_util.OP_INSTALL_TARGETS in line for line in outcome.detail)


@pytest.mark.skipif(shutil.which("make") is None, reason="the runner target needs make on PATH")
def test_an_undecodable_byte_in_a_targets_output_maps_its_rc_instead_of_raising(tmp_path: Path) -> None:
    """A UnicodeDecodeError here escapes the rc mapping entirely: no exit, no baton.

    The byte survives as U+FFFD and the row keeps the verdict its rc earned.
    """
    repo = _seeded_repo(tmp_path / "consumer", **{"Makefile": _MAKEFILE_UNDECODABLE})

    outcome = ledger.run_target(repo, target=kb_util.TARGET_VERIFY)

    assert outcome.exit_code == baton.EXIT_GATE_RED
    assert "dead link in caf�.md" in outcome.stdout


# ---------------------------------------------------------------------------
# The failing op's own diagnostic
# ---------------------------------------------------------------------------
#
# A stage says what is wrong on whichever stream it chose: `kb_claimgraph`
# prints its FAIL findings on stdout and exits 1, an unhandled exception in
# `kb_docgraph` arrives as a traceback on stderr. A detail built from stderr
# alone relayed `exited 1` and nothing else for every stage of the first kind,
# and each of those failures had to be diagnosed by re-running the stage by
# hand outside the driver.

_STDOUT_FAULT = f"[verify] {kb_util.FAIL} point-2 index.md sits at an index path but lists no children"
_STDERR_FAULT = "ValueError: the document tree does not conform"


def _failing_makefile(*, out: str = "", err: str = "") -> str:
    """A red ``kb-verify`` that says what is wrong on the streams named."""
    recipe = []
    if out:
        recipe.append(f"@echo '{out}'")
    if err:
        recipe.append(f"@echo '{err}' >&2")
    recipe.append("@exit 1")
    return f"{kb_util.INSTALL_LINE_MAKE}\n\nkb-verify:\n" + "".join(f"\t{line}\n" for line in recipe)


@pytest.mark.skipif(shutil.which("make") is None, reason="the runner target needs make on PATH")
@pytest.mark.parametrize(
    ("out", "err", "expected"),
    [
        (_STDOUT_FAULT, "", (_STDOUT_FAULT,)),
        ("", _STDERR_FAULT, (_STDERR_FAULT,)),
        (_STDOUT_FAULT, _STDERR_FAULT, (_STDOUT_FAULT, _STDERR_FAULT)),
    ],
    ids=["stdout", "stderr", "both"],
)
def test_a_failing_ops_report_reaches_the_detail_whichever_stream_carried_it(
    tmp_path: Path, out: str, err: str, expected: tuple[str, ...]
) -> None:
    repo = _seeded_repo(tmp_path / "consumer", **{"Makefile": _failing_makefile(out=out, err=err)})

    outcome = ledger.run_target(repo, target=kb_util.TARGET_VERIFY)

    assert outcome.exit_code == baton.EXIT_GATE_RED
    assert outcome.detail[0].startswith(f"make {kb_util.TARGET_VERIFY} exited")
    for line in expected:
        assert line in outcome.detail, outcome.detail


def _completed(*, stdout: str = "", stderr: str = "") -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess(args=["kb-verify"], returncode=1, stdout=stdout, stderr=stderr)


def test_a_long_traceback_is_bounded_and_keeps_the_line_that_names_the_failure() -> None:
    """The tail is what survives a traceback: the exception's own message is its last line."""
    frames = [f'  File "kb_docgraph/build.py", line {n}, in build' for n in range(500)]
    detail = ledger._failure_detail("kb_docgraph", _completed(stderr="\n".join(["Traceback:", *frames, _STDERR_FAULT])))

    assert detail[0] == "kb_docgraph exited 1"
    assert detail[-1] == _STDERR_FAULT
    assert len(detail) <= ledger.FAILURE_DETAIL_LINES + 3, detail
    assert any("line(s) of the report omitted" in line for line in detail), "a truncation nobody announced"


def test_a_long_report_keeps_its_fail_lines_wherever_they_sit_in_it() -> None:
    """The bound may cost context; it may not cost the finding.

    A `FAIL` near the head of a long report is exactly what a tail-only bound
    would drop, and it is the one line the operator has to see.
    """
    report = [f"[verify] {kb_util.FACT} scanned {n}" for n in range(300)]
    report[2] = _STDOUT_FAULT

    detail = ledger._failure_detail("kb-verify", _completed(stdout="\n".join(report)))

    assert _STDOUT_FAULT in detail
    assert detail[-1] == report[-1]
    assert len(detail) <= ledger.FAILURE_DETAIL_LINES + 3, detail


def test_a_short_report_is_carried_whole_and_says_nothing_about_truncation() -> None:
    detail = ledger._failure_detail("kb_claimgraph --pass 1", _completed(stdout=f"first\n{_STDOUT_FAULT}"))

    assert detail == ("kb_claimgraph --pass 1 exited 1", "first", _STDOUT_FAULT)


def test_run_target_refuses_a_name_that_is_not_a_maintenance_target(tmp_path: Path) -> None:
    repo = _seeded_repo(tmp_path / "consumer")

    with pytest.raises(runlog.BoundaryError):
        ledger.run_target(repo, target="kb-publish")


# ---------------------------------------------------------------------------
# The write ops: exit 8 enrolled, and retried unchanged
# ---------------------------------------------------------------------------
#
# The rc is injected rather than raced for: a real exit 8 needs a second writer
# moving the file inside the op's own contention window, which is
# `test_kb_write_*`'s forced interleaving and proves nothing about the
# adapter. What is under test here is the adapter's answer to the rc — that
# the identical argv is re-run, a bounded number of times, with no dispatch
# anywhere in the loop.

# A stand-in for `python3 -m kb_tools.kb_util <op>`: it appends its own argv to
# a log and exits the next code in a scripted sequence, so a case states the rc
# series it means to test and reads back exactly what was invoked. Written as a
# joined list because the formatter collapses an implicitly concatenated
# multi-line literal onto one line.
_SCRIPTED_OP = "\n".join(
    [
        "import sys",
        "from pathlib import Path",
        "here = Path(__file__).resolve().parent",
        "log = here / 'invocations.log'",
        "codes = (here / 'codes.txt').read_text(encoding='utf-8').split()",
        "seen = len(log.read_text(encoding='utf-8').splitlines()) if log.exists() else 0",
        "with log.open('a', encoding='utf-8') as handle:",
        "    handle.write(' '.join(sys.argv[1:]) + '\\n')",
        "sys.exit(int(codes[seen if seen < len(codes) else -1]))",
    ]
)

# Every write op takes one `--values FILE` and nothing else — the values are
# TOML — so an op name and a values path is the whole shape of a
# driver-invoked call.
# The file is never opened here: the scripted op reads its rc, not its input.
_WRITE_ARGS = ("--values", ".claude-temp/kb-build/set-rigor.toml")


def _no_dispatch(*args: object, **kwargs: object) -> None:
    raise AssertionError("the write-op path dispatched inference")


def _scripted_write_op(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, *codes: int) -> Path:
    """Point the adapter's ``kb_util`` invocation at :data:`_SCRIPTED_OP`; return its log.

    ``_KB_UTIL`` is the seam the adapter itself uses to name the tool, so
    replacing it exercises the real ``_outcome`` path — the real subprocess,
    the real rc mapping, the real retry loop.
    """
    home = tmp_path / "scripted-op"
    home.mkdir()
    (home / "codes.txt").write_text(" ".join(str(code) for code in codes), encoding="utf-8")
    script = home / "op.py"
    script.write_text(_SCRIPTED_OP, encoding="utf-8")
    monkeypatch.setattr(ledger, "_KB_UTIL", (sys.executable, str(script)))
    return home / "invocations.log"


def _invocations(log: Path) -> list[str]:
    return log.read_text(encoding="utf-8").splitlines()


@pytest.fixture
def run_log(tmp_path: Path) -> Iterator[Path]:
    """The driver's own evidence channel, attached for one test and detached after.

    A retry is *reported* where the driver reports everything — the run log's
    JSONL — so the count is read back from there rather than through
    ``caplog``, whose handler lands on the driver logger as well as the root
    when a suite has already turned that logger's propagation off, and then
    sees every record twice.
    """
    paths = runlog.prepare(tmp_path / "kb-driver", "run-1")
    runlog.configure(run_log=paths.run_log, level="INFO")
    yield paths.run_log
    driver_log = logging.getLogger("kb_driver")
    for handler in list(driver_log.handlers):
        driver_log.removeHandler(handler)
        handler.close()


def _reported_retries(run_log: Path) -> list[dict]:
    records = [json.loads(line) for line in run_log.read_text(encoding="utf-8").splitlines()]
    return [record for record in records if "re-ran the identical invocation" in record["message"]]


def test_a_contended_write_op_is_re_run_unchanged_until_it_lands(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, run_log: Path
) -> None:
    """rc 8 twice then 0: three invocations of one argv, one reported retry, exit 0."""
    repo = _seeded_repo(tmp_path / "consumer")
    log = _scripted_write_op(tmp_path, monkeypatch, 8, 8, 0)
    # Nothing in this path may reach the model. The dispatch entry point is
    # `transport.invoke`; if the adapter ever routed a retry through inference,
    # this is where it would show.
    monkeypatch.setattr(transport, "invoke", _no_dispatch)

    outcome = ledger.write_op(repo, op=kb_util.OP_SET_RIGOR, args=_WRITE_ARGS)

    assert outcome.ok
    invocations = _invocations(log)
    assert len(invocations) == 3
    assert set(invocations) == {" ".join((kb_util.OP_SET_RIGOR, *_WRITE_ARGS))}
    retries = _reported_retries(run_log)
    assert len(retries) == 1
    # The run log's context values are strings by the formatter's contract.
    assert retries[0]["context"] == {
        "argv": " ".join((sys.executable, str(tmp_path / "scripted-op" / "op.py"), kb_util.OP_SET_RIGOR, *_WRITE_ARGS)),
        "invocations": "3",
        "returncode": "0",
    }


def test_a_write_op_that_stays_contended_is_bounded_and_names_its_restore(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """rc 8 forever: the original call plus the bound, then an environment fault."""
    repo = _seeded_repo(tmp_path / "consumer")
    log = _scripted_write_op(tmp_path, monkeypatch, 8)
    monkeypatch.setattr(transport, "invoke", _no_dispatch)

    outcome = ledger.write_op(repo, op=kb_util.OP_SET_RIGOR, args=_WRITE_ARGS)

    assert outcome.exit_code == baton.EXIT_ENVIRONMENT
    assert len(_invocations(log)) == ledger.WRITE_OP_RETRY_LIMIT + 1
    assert any("restore:" in line for line in outcome.detail)


def test_a_write_op_that_lands_first_time_is_neither_retried_nor_reported(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, run_log: Path
) -> None:
    repo = _seeded_repo(tmp_path / "consumer")
    log = _scripted_write_op(tmp_path, monkeypatch, 0)

    outcome = ledger.write_op(repo, op=kb_util.OP_SET_RIGOR, args=_WRITE_ARGS)

    assert outcome.ok
    assert len(_invocations(log)) == 1
    assert _reported_retries(run_log) == []


@pytest.mark.parametrize(
    ("code", "expected"),
    [
        (write_ops.ExitCode.ENVIRONMENT, baton.EXIT_ENVIRONMENT),
        (write_ops.ExitCode.REFUSED, baton.EXIT_INTERNAL),
    ],
    ids=["environment", "refused"],
)
def test_the_other_write_op_codes_map_without_a_retry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, code: int, expected: int
) -> None:
    repo = _seeded_repo(tmp_path / "consumer")
    log = _scripted_write_op(tmp_path, monkeypatch, code)

    outcome = ledger.write_op(repo, op=kb_util.OP_SET_RIGOR, args=_WRITE_ARGS)

    assert outcome.exit_code == expected
    assert len(_invocations(log)) == 1


def test_an_rc_outside_the_write_vocabulary_is_still_a_contract_violation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The reason 8 had to be enrolled: an unmapped rc reads as a broken tool (exit 15)."""
    repo = _seeded_repo(tmp_path / "consumer")
    _scripted_write_op(tmp_path, monkeypatch, 9)

    with pytest.raises(runlog.BoundaryError):
        ledger.write_op(repo, op=kb_util.OP_SET_RIGOR, args=_WRITE_ARGS)


def test_write_op_refuses_a_name_that_is_not_a_write_op(tmp_path: Path) -> None:
    repo = _seeded_repo(tmp_path / "consumer")

    with pytest.raises(runlog.BoundaryError):
        ledger.write_op(repo, op=kb_util.OP_SHOW_STATUS)
