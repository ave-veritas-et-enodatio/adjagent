"""The build-out stages: ``phase-3a`` and ``phase-5``.

The KB tree this driver walks over is no longer this driver's own output: the
distillation stages (``phase-0`` through ``phase-3``) that used to derive it
from a LaTeX survey are gone, replaced by a separately-authored pandoc front
end. What survives here is everything downstream of an already-built tree:

* **``phase-3a`` is a gate and a record, and nothing between them.** The three
  verifiers run behind ``kb-refresh``; green records the stage and red stops the
  run. No round is spent and no seat is dispatched, because each verifier
  compares one mechanically-produced artifact against another and a red one is
  a defect in a tool or in what was authored.
* **``phase-5`` still runs a capped cycle**, over documents a seat wrote:
  write, review, fix from the findings, re-review, escalate at the cap.

The fixture's ``repo`` starts at the state the pandoc pipeline hands off: a KB
spine, with every domain's leaves already distilled (:func:`distilled`) by the
case that needs them — this driver writes no document tree of its own, and the
tree is the only thing it is told about the corpus. The ledger and the runner
targets are the two seams
``test_kb_driver_run.py`` also uses. The templates are local stand-ins named
for the real ones, and their prose is not what this suite is about.
"""

import itertools
import json
import re
from collections.abc import Iterator, Mapping, Sequence
from pathlib import Path

import pytest

from kb_tools import kb_index_lib, kb_pipeline, kb_readme, kb_util
from kb_tools.kb_driver import barriers, baton, config, envelope, ledger, prompt_templates, replay, run, runlog, steps

#: A KB with nothing in it, used only to read the emitter's own artifact
#: vocabulary off ``build_all_records``. A list of filenames here would be a
#: second statement of what ``.index/`` holds.
EMPTY_KB = kb_index_lib.KbState(claim_entries=(), leaves=(), indexes=(), framework_nodes=(), experiments=())

#: The volumes this run is given, and the domain directories the document graph
#: put under ``kb-root/`` for them — a domain IS a volume, but the driver learns
#: that from the tree rather than from the source list. In sorted order, which
#: is the order the walk returns them in.
SOURCES = ("Dynamics.tex", "Foundations.tex", "Policy.tex")
DOMAINS = ("dynamics", "foundations", "policy")

#: The recorded-stage set a fixture starts each scenario from — one entry per
#: stage this table still has rows for, since ``phase-3a`` is the first row
#: any case here drives.
RECORDED_THROUGH_3A_PREDECESSOR = ("start",)
RECORDED_THROUGH_3A = (*RECORDED_THROUGH_3A_PREDECESSOR, "phase-3a")


# ---------------------------------------------------------------------------
# The templates a call composes against
# ---------------------------------------------------------------------------

TEMPLATES: Mapping[str, str] = {
    "phase-5-overview-passage.single.tmpl": ("KB: @!dyn.kb-root!@\nFindings: @!dyn.remediation-source-path!@\n"),
    "phase-5-review.single.tmpl": (
        "README: @!dyn.readme-path!@\nCONVENTIONS: @!dyn.conventions-path!@\n\n@!verdict-contract!@\n@!return-contract!@\n"
    ),
}

# Keyed by bare name and placed under the fragments directory by the fixture, so
# the layout is stated once, where the composer states it.
FRAGMENTS: Mapping[str, str] = {
    "return-contract.tmpl": "Return the artifact as the final message body.\n",
    "verdict-contract.tmpl": "End with VERDICT: critical=<n> warning=<n> note=<n>\n",
}


# ---------------------------------------------------------------------------
# What a call reads out of its brief
# ---------------------------------------------------------------------------


def field(brief: str, name: str) -> str:
    """One ``<Name>: <value>`` line of a stand-in template."""
    match = re.search(rf"^{re.escape(name)}: (.+)$", brief, re.MULTILINE)
    assert match is not None, f"the brief carries no {name!r} line"
    return match.group(1).strip()


#: The three verdicts these cases script, named for what each does to the gate.
#: ``CARRIED`` is the live one: a build of arXiv 2609.09855v1 returned exactly
#: this and stopped, which is the stop the severity gate exists to not make.
CLEAN = envelope.Verdict(critical=0, warning=0, note=0)
CARRIED = envelope.Verdict(critical=0, warning=1, note=2)
CRITICAL = envelope.Verdict(critical=1, warning=0, note=0)


#: How many leaves each domain directory holds — the shape the document graph
#: leaves behind, which is what ``phase-3a`` runs its verifiers over.
LEAVES_PER_DOMAIN = 2


def leaves_for(domain: str, count: int = LEAVES_PER_DOMAIN) -> tuple[str, ...]:
    """The kb-root-relative leaves one domain directory holds."""
    return tuple(f"{domain}/leaf-{ordinal}.md" for ordinal in range(1, count + 1))


# ---------------------------------------------------------------------------
# The seams
# ---------------------------------------------------------------------------

PREFLIGHT = (
    "[preflight] FACT runner-file       justfile (runner: just)\n"
    "[preflight] PASS docent-commands   both present under .claude/commands\n"
    "[preflight] PASS: ready.\n"
)
PREFLIGHT_MISSING_DOCENT = (
    "[preflight] FACT runner-file       justfile (runner: just)\n"
    "[preflight] FAIL docent-commands   missing kb-next.md under .claude/commands — incomplete install; "
    "restore: run 'just install .' from the generator repo\n"
)


# A call's label is `<seq>-<step id>`; the sequence number is evidence, not identity.
_SEQ_PREFIX_RE = re.compile(r"^\d+-")

# One run directory per `drive()`, because a test that resumes a stage drives
# twice and `runlog.prepare` refuses to reuse one.
_RUN_SERIAL = itertools.count(1)


class Script:
    """Scripted returns per step id; the last one repeats. Records every call it served."""

    def __init__(self, returns: Mapping[str, object]) -> None:
        self._returns = dict(returns)
        self.calls: list[replay.ReplayContext] = []

    @property
    def order(self) -> list[str]:
        """Every step this run dispatched, in dispatch order, without its sequence prefix."""
        return [_SEQ_PREFIX_RE.sub("", context.step) for context in self.calls]

    def count(self, step_id: str) -> int:
        return sum(1 for context in self.calls if context.step.endswith(step_id))

    def contexts(self, step_id: str) -> list[replay.ReplayContext]:
        return [context for context in self.calls if context.step.endswith(step_id)]

    def brief(self, step_id: str) -> str:
        contexts = self.contexts(step_id)
        assert contexts, f"{step_id} was never called"
        return contexts[-1].brief_text()

    def scenario(self) -> replay.Scenario:
        def scenario(context: replay.ReplayContext) -> replay.Response:
            self.calls.append(context)
            for step_id, scripted in self._returns.items():
                if not context.step.endswith(step_id) and not context.step.endswith(f"{step_id}-reask"):
                    continue
                answers = scripted if isinstance(scripted, (list, tuple)) else [scripted]
                answer = answers[min(self.count(step_id) - 1, len(answers) - 1)]
                return replay.clean(answer(context) if callable(answer) else str(answer))(context)
            raise AssertionError(f"no scripted return for {context.step}")

        return scenario


def render_for(recorded: Sequence[str]) -> str:
    lines = [f"[kb-build] status: in progress ({len(recorded)} recorded)"]
    lines += [f"[{'x' if stage in recorded else ' '}] {stage}  {stage} display" for stage in kb_pipeline.STAGE_IDS]
    lines.append("[card] next action — do the thing")
    return "\n".join(lines) + "\n"


class FakeLedger:
    """The recorded-stage set as a tool would report it, plus what each op was told.

    ``verify`` is a sequence consumed one per ``kb-verify`` run, the last
    repeating, so a case states the gate's verdict without reaching into the
    stage that reads it.
    """

    def __init__(
        self,
        *,
        recorded: Sequence[str] = (),
        verify: Sequence[ledger.Outcome] = (),
        preflight_stdout: str = PREFLIGHT,
    ) -> None:
        self.recorded = list(recorded)
        self.targets: list[str] = []
        self._repo_root: Path | None = None
        self._verify = list(verify)
        self._verifies = 0
        self._preflight_stdout = preflight_stdout

    def _advance(self, *, stage: str, note: str = "", no_inference: bool = False) -> ledger.Outcome:
        self.recorded.append(stage)
        return ledger.Outcome(baton.EXIT_OK)

    def _run_target(self, *, target: str) -> ledger.Outcome:
        self.targets.append(target)
        if target != kb_util.TARGET_VERIFY or not self._verify:
            return ledger.Outcome(baton.EXIT_OK)
        outcome = self._verify[min(self._verifies, len(self._verify) - 1)]
        self._verifies += 1
        return outcome

    def ops(self, repo_root: Path) -> run.LedgerOps:
        """The seam, with the repo root bound — the real adapter's own shape."""
        self._repo_root = repo_root
        return run.LedgerOps(
            preflight=lambda: ledger.Outcome(baton.EXIT_OK, stdout=self._preflight_stdout),
            graph_init=lambda *, runner: ledger.Outcome(baton.EXIT_OK),
            document_graph=lambda *, sources, bibliographies, kb_root: ledger.Outcome(baton.EXIT_OK),
            claim_graph=lambda *, flags: ledger.Outcome(baton.EXIT_OK),
            revision_entry=lambda: ledger.Outcome(baton.EXIT_OK),
            start_build=lambda *, charter: ledger.Outcome(baton.EXIT_OK),
            advance_step=self._advance,
            show_status=lambda *, relay: ledger.Outcome(baton.EXIT_OK, stdout=render_for(self.recorded)),
            run_target=self._run_target,
        )


def red_gate(*paths: str) -> ledger.Outcome:
    """A verify outcome whose report names the files it faulted.

    The detail carries the report lines as well as the op and the rc, which is
    what ``ledger._failure_detail`` puts there: a card whose ASK names only the
    rc leaves the operator re-running the stage by hand to learn what it said.
    """
    faults = [f"[verify] {kb_util.FAIL} {path}: broken link" for path in paths]
    report = "\n".join(["[verify] metadata gate red", *faults])
    return ledger.Outcome(baton.EXIT_GATE_RED, stdout=report + "\n", detail=("kb-verify exited 1", *faults))


GREEN = ledger.Outcome(baton.EXIT_OK, stdout="[verify] green\n")


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _quiet_logs() -> Iterator[None]:
    yield
    import logging

    driver_log = logging.getLogger("kb_driver")
    for handler in list(driver_log.handlers):
        driver_log.removeHandler(handler)
        handler.close()


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    """A consuming repo at the state the pandoc pipeline hands off.

    The spine and nothing else under ``kb-root/`` — a case that wants a domain
    already distilled calls :func:`distilled` itself, which is what every case
    past ``phase-3a`` does. That call is what puts the volume directories the
    domain partition is walked off on disk, so a case reaching a fix wave
    without it has no tree to partition, which is the honest state rather than
    a fixture detail.
    """
    root = tmp_path / "repo"
    charter = root / config.DEFAULT_CHARTER_FILE
    charter.parent.mkdir(parents=True)
    charter.write_text("# Build charter\n\nBoth volumes.\n", encoding="utf-8")

    index_dir = kb_util.index_dir(root)
    index_dir.mkdir(parents=True)
    # The derived artifacts a real `phase-3a` refresh leaves behind. This
    # suite's ledger is a fake and no refresh runs, so the files are laid down
    # here: `phase-5` reads its counts out of them, and an absent one is the
    # different failure of an index that was never rebuilt.
    for name in kb_index_lib.build_all_records(EMPTY_KB):
        (index_dir / f"{name}.jsonl").write_text("", encoding="utf-8")

    commands = root / kb_util.CLAUDE_DIRNAME / kb_util.COMMANDS_DIRNAME
    commands.mkdir(parents=True)
    for name in kb_util.DOCENT_COMMAND_FILENAMES:
        (commands / name).write_text(f"# {name}\n", encoding="utf-8")
    # `phase-3a`'s readiness stamp writes this, and this suite's ledger is a
    # fake so no stamp runs. `phase-5`'s review is handed its path and the call
    # is refused where the file is not there, so an absent one here would be a
    # fixture that under-models the state the stage really meets.
    (kb_util.kb_root(root) / kb_pipeline.CONVENTIONS_DOC).write_text("# Conventions\n", encoding="utf-8")
    return root


@pytest.fixture
def templates(tmp_path: Path) -> Path:
    directory = tmp_path / "templates"
    directory.mkdir()
    fragments = directory / prompt_templates.FRAGMENTS_DIRNAME
    fragments.mkdir()
    for name, text in TEMPLATES.items():
        (directory / name).write_text(text, encoding="utf-8")
    for name, text in FRAGMENTS.items():
        (fragments / name).write_text(text, encoding="utf-8")
    return directory


def drive(
    *,
    repo_root: Path,
    tmp_path: Path,
    templates: Path,
    script: Script,
    fake: FakeLedger,
    stages: Sequence[str],
    decisions: Mapping[str, str] = (),
    build_mode: str | None = None,
    ops: run.LedgerOps | None = None,
) -> run.Result:
    body = ["[run]", f"sources = {json.dumps(list(SOURCES))}", 'permission_mode = "acceptEdits"']
    if build_mode is not None:
        body.append(f'build_mode = "{build_mode}"')
    for pair, answer in dict(decisions).items():
        stage, _, kind = pair.rpartition(".")
        body += ["", f'[barriers."{stage}"."{kind}"]', f'decision = "{answer}"']
    path = repo_root / "driver-run.toml"
    path.write_text("\n".join(body) + "\n", encoding="utf-8")

    paths = runlog.prepare(tmp_path / "runs", f"20260901T120000-{next(_RUN_SERIAL)}")
    lock = runlog.repo_lock_path(repo_root)
    lock.parent.mkdir(parents=True, exist_ok=True)
    lock.write_text(json.dumps({"pid": 1, "run_id": paths.run_id}), encoding="utf-8")
    return run.execute(
        config=config.load(path, admissible=barriers.ADMISSIBLE),
        paths=paths,
        invoker=replay.ReplayInvoker(script.scenario()),
        ops=ops if ops is not None else fake.ops(repo_root),
        repo_root=repo_root,
        stages=stages,
        prompt_templates_dir=templates,
    )


# ---------------------------------------------------------------------------
# The scenarios each call runs
# ---------------------------------------------------------------------------


#: What the seat returns at ``p5.docs`` / ``p5.fix``: the passage, and nothing
#: else. It writes no file, because the stage assembles the document.
PASSAGE = "This corpus is three volumes of synthetic material. Start at the first."


def phase_5_script(*, rounds: Sequence[envelope.Verdict] = (CLEAN,)) -> Script:
    return Script(
        {
            "p5.docs": PASSAGE,
            "p5.fix": PASSAGE,
            # `replay.verdict` composes the line `envelope.parse_verdict` reads,
            # so a scripted round cannot spell a format the driver would refuse.
            "p5.review": [
                replay.verdict(critical=item.critical, warning=item.warning, note=item.note) for item in rounds
            ],
        }
    )


def stamp_leaf(repo_root: Path, path: str) -> Path:
    """One leaf as the pandoc pipeline leaves it: a rendered body under a metadata block."""
    leaf = kb_util.kb_root(repo_root) / path
    leaf.parent.mkdir(parents=True, exist_ok=True)
    body = f"[Up: {path}](../index.md)\n\nA distilled leaf.\n"
    leaf.write_text(replay.stamped_leaf(body, claims=()), encoding="utf-8")
    return leaf


def distilled(repo_root: Path) -> None:
    """Every derived leaf stamped on disk — the state this driver assumes on entry.

    Nothing in this table writes it: distillation is the pandoc front end's
    job, run before this driver ever sees the repo. ``phase-3a`` onward reads
    the tree, and this is the tree it reads.
    """
    for domain in DOMAINS:
        for path in leaves_for(domain):
            stamp_leaf(repo_root, path)


# ---------------------------------------------------------------------------
# phase-3a: the gate, and the two ways it ends
# ---------------------------------------------------------------------------


def test_the_gate_refreshes_before_it_verifies(repo: Path, tmp_path: Path, templates: Path) -> None:
    """A derived-state read needs a refresh in front of it."""
    distilled(repo)
    fake = FakeLedger(recorded=RECORDED_THROUGH_3A_PREDECESSOR)

    drive(
        repo_root=repo,
        tmp_path=tmp_path,
        templates=templates,
        script=Script({}),
        fake=fake,
        stages=("phase-3a",),
    )

    assert fake.targets == [kb_util.TARGET_REFRESH, kb_util.TARGET_VERIFY]
    assert fake.recorded[-1] == "phase-3a"


def test_a_red_gate_stops_the_run_and_records_nothing(repo: Path, tmp_path: Path, templates: Path) -> None:
    """No round, no seat, no cap: a failed verifier ends the walk where it stands.

    Each of the three verifiers compares one mechanically-produced artifact
    against another, so a red one is a defect in a tool or in what was authored
    — not work a dispatched seat could close, and not a barrier anyone can
    answer. The verifier's own report is in the run log; what reaches the caller
    is the target that failed and exit 11.
    """
    distilled(repo)
    faulted = f"{kb_util.KB_DIRNAME}/{DOMAINS[1]}/{DOMAINS[1]}.md"
    script = Script({})
    fake = FakeLedger(recorded=RECORDED_THROUGH_3A_PREDECESSOR, verify=[red_gate(faulted), GREEN])

    result = drive(
        repo_root=repo,
        tmp_path=tmp_path,
        templates=templates,
        script=script,
        fake=fake,
        stages=("phase-3a",),
    )

    assert result.exit_code == baton.EXIT_GATE_RED
    assert result.pair == "", "a red gate is an exit, not a barrier — no answer would repair the KB"
    assert script.calls == [], "no seat is dispatched over a mechanical gate"
    assert "phase-3a" not in fake.recorded
    assert not (repo / steps.SCRATCH_ROOT / "review").exists(), "nothing writes a findings round here"

    # The card the relay actually prints, from the very context the run ended
    # with: the failing gate's own lines are in it, and nothing in it asks a
    # question or offers a `--decide` for a barrier that was never raised.
    card = baton.render(result.exit_code, result.context)
    assert f"[verify] {kb_util.FAIL} {faulted}: broken link" in card
    assert "(missing —" not in card
    assert "--decide" not in card


def test_an_environment_fault_stops_the_run_the_same_way_a_red_gate_does(
    repo: Path, tmp_path: Path, templates: Path
) -> None:
    """Exit 14, and the restore line the ledger composed — never a fix cycle."""
    distilled(repo)
    missing = ledger.Outcome(baton.EXIT_ENVIRONMENT, detail=("no justfile or Makefile — restore: install targets",))

    result = drive(
        repo_root=repo,
        tmp_path=tmp_path,
        templates=templates,
        script=Script({}),
        fake=FakeLedger(recorded=RECORDED_THROUGH_3A_PREDECESSOR, verify=[missing]),
        stages=("phase-3a",),
    )

    assert result.exit_code == baton.EXIT_ENVIRONMENT
    assert any("restore:" in line for line in result.detail)


# ---------------------------------------------------------------------------
# phase-5: meta-docs, one fix cycle, and the docent check
# ---------------------------------------------------------------------------


def test_the_stage_assembles_the_overview_document_reviews_it_and_records(
    repo: Path, tmp_path: Path, templates: Path
) -> None:
    """The stage assembles; the seat answers. One call each, and no document composed by a model."""
    distilled(repo)
    fake = FakeLedger(recorded=RECORDED_THROUGH_3A)
    script = phase_5_script()

    result = drive(
        repo_root=repo, tmp_path=tmp_path, templates=templates, script=script, fake=fake, stages=("phase-5",)
    )

    assert result.exit_code == baton.EXIT_OK
    assert script.order == ["p5.docs", "p5.review"]
    assert fake.recorded[-1] == "phase-5"

    document = (kb_util.kb_root(repo) / kb_pipeline.OVERVIEW_DOC).read_text(encoding="utf-8")
    # The seat's answer reaches the document verbatim, and nothing else it
    # returned does — every other word is the template's or the index's.
    assert PASSAGE in document
    assert not kb_readme.slots(document), "a slot reached the knowledge base unfilled"
    # The counts the seat was never asked for: this corpus has no claims, and
    # the document says so because the index does.
    assert f"{len(DOMAINS) * LEAVES_PER_DOMAIN} documents" in document


def test_a_critical_finding_runs_one_fix_cycle_and_then_escalates_to_phase_5s_own_key(
    repo: Path, tmp_path: Path, templates: Path
) -> None:
    distilled(repo)
    script = phase_5_script(rounds=(CRITICAL,))

    result = drive(
        repo_root=repo,
        tmp_path=tmp_path,
        templates=templates,
        script=script,
        fake=FakeLedger(recorded=RECORDED_THROUGH_3A),
        stages=("phase-5",),
    )

    assert result.exit_code == baton.EXIT_GATE_RED
    assert result.pair == "phase-5.cap-exhausted"
    assert script.count("p5.fix") == kb_pipeline.PHASE_5_FIX_CAP


def test_a_warning_and_a_note_are_carried_past_the_gate_and_the_stage_records(
    repo: Path, tmp_path: Path, templates: Path
) -> None:
    """The gate is the reviewer's own severity, not a count of what it left open.

    A reviewer that raised no critical finding has ruled that nothing here stops
    the build, and the counting gate discarded that ruling — a note observing an
    unmentioned filename spent the stage's one fix round and escalated. The
    documents stand as written, the stage records, and the run walks on.
    """
    distilled(repo)
    fake = FakeLedger(recorded=RECORDED_THROUGH_3A)
    script = phase_5_script(rounds=(CARRIED,))

    result = drive(
        repo_root=repo, tmp_path=tmp_path, templates=templates, script=script, fake=fake, stages=("phase-5",)
    )

    assert result.exit_code == baton.EXIT_OK
    assert result.pair == "", "a warning is reported, and a report is not a barrier"
    assert script.count("p5.fix") == 0, "no fix round is spent on a finding the reviewer did not call critical"
    assert fake.recorded[-1] == "phase-5"


@pytest.mark.parametrize(
    ("returned", "expected"),
    [
        (CARRIED, "3 finding(s) at warning or note are reported here and repaired by nobody"),
        (CLEAN, "the reviewer raised no finding at warning or note"),
    ],
    ids=["non-zero", "zero"],
)
def test_the_round_reports_its_counts_by_severity_and_where_the_findings_are(
    repo: Path,
    tmp_path: Path,
    templates: Path,
    caplog: pytest.LogCaptureFixture,
    returned: envelope.Verdict,
    expected: str,
) -> None:
    """Both forms reach the operator, and the whole statement is in the message.

    The zero form is the load-bearing half: a build that said nothing when the
    reviewer raised no warning would read exactly like one whose warnings went
    unreported, and promoting a warning to a critical on our own schedule
    depends on having seen it. The console tee prints messages alone, so the
    counts and the path are asserted against the message text and not the
    record's context.
    """
    distilled(repo)
    script = phase_5_script(rounds=(returned,))
    findings = steps.findings(
        stage="phase-5", series=steps.SERIES_INITIAL, round_number=1, author=steps.META_REVIEW_SEAT
    )

    with caplog.at_level("INFO", logger="kb_driver.run"):
        drive(
            repo_root=repo,
            tmp_path=tmp_path,
            templates=templates,
            script=script,
            fake=FakeLedger(recorded=RECORDED_THROUGH_3A),
            stages=("phase-5",),
        )

    line = next(
        (message for message in caplog.messages if message.startswith("phase-5 review round 1:")),
        None,
    )
    assert line is not None, "the review round reported no counts at all"
    assert f"critical={returned.critical} warning={returned.warning} note={returned.note}" in line
    assert expected in line
    assert f"findings: {steps.SCRATCH_ROOT}/{findings}" in line


def test_the_fix_call_reads_the_reviewers_findings_and_the_first_pass_does_not(
    repo: Path, tmp_path: Path, templates: Path
) -> None:
    """One template, two call sites: the difference is one slot with a named absence."""
    distilled(repo)
    script = phase_5_script(rounds=(CRITICAL, CLEAN))

    drive(
        repo_root=repo,
        tmp_path=tmp_path,
        templates=templates,
        script=script,
        fake=FakeLedger(recorded=RECORDED_THROUGH_3A),
        stages=("phase-5",),
    )

    assert field(script.brief("p5.docs"), "Findings") == steps.NOTHING
    findings = steps.findings(
        stage="phase-5", series=steps.SERIES_INITIAL, round_number=1, author=steps.META_REVIEW_SEAT
    )
    assert field(script.brief("p5.fix"), "Findings").endswith(Path(findings).name)


def test_every_path_a_brief_hands_a_seat_resolves_without_a_base(
    repo: Path, tmp_path: Path, templates: Path
) -> None:
    """A relative path here is one a seat resolves against a cwd no brief states.

    Measured: a review handed ``kb-root/README.md`` searched for a directory of
    that name, reviewed a different repository's knowledge base, and returned
    three critical findings about it.
    """
    distilled(repo)
    script = phase_5_script(rounds=(CRITICAL, CLEAN))

    drive(
        repo_root=repo,
        tmp_path=tmp_path,
        templates=templates,
        script=script,
        fake=FakeLedger(recorded=RECORDED_THROUGH_3A),
        stages=("phase-5",),
    )

    stated = {
        ("p5.docs", "KB"): repo / "kb-root",
        ("p5.review", "README"): repo / "kb-root" / kb_pipeline.META_DOCS[0],
        ("p5.review", "CONVENTIONS"): repo / "kb-root" / kb_pipeline.META_DOCS[1],
        ("p5.fix", "Findings"): repo
        / steps.SCRATCH_ROOT
        / steps.findings(stage="phase-5", series=steps.SERIES_INITIAL, round_number=1, author=steps.META_REVIEW_SEAT),
    }
    for (step_id, name), expected in stated.items():
        stated_path = field(script.brief(step_id), name)
        assert Path(stated_path).is_absolute(), f"{step_id}'s {name} has no base"
        assert stated_path == str(expected), f"{step_id}'s {name} names the wrong path"


def test_missing_docent_commands_stop_the_build_with_preflights_own_restore_line(
    repo: Path, tmp_path: Path, templates: Path
) -> None:
    """Exit 14 and a relayed ``restore:``, never a barrier — no answer would install them."""
    distilled(repo)
    (repo / kb_util.CLAUDE_DIRNAME / kb_util.COMMANDS_DIRNAME / kb_util.DOCENT_COMMAND_FILENAMES[1]).unlink()
    fake = FakeLedger(recorded=RECORDED_THROUGH_3A, preflight_stdout=PREFLIGHT_MISSING_DOCENT)

    result = drive(
        repo_root=repo,
        tmp_path=tmp_path,
        templates=templates,
        script=phase_5_script(),
        fake=fake,
        stages=("phase-5",),
    )

    assert result.exit_code == baton.EXIT_ENVIRONMENT
    assert result.pair == "", "a missing install is an exit, not a barrier"
    assert any(kb_util.DOCENT_COMMAND_FILENAMES[1] in line for line in result.detail)
    assert any("restore:" in line for line in result.detail)
    assert "phase-5" not in fake.recorded


# ---------------------------------------------------------------------------
# The two-party contract between the loop's slots and the rows' templates
# ---------------------------------------------------------------------------


def test_the_slots_the_loop_supplies_compose_every_build_out_template(templates: Path) -> None:
    """Every row of these stages composes: the table's slots fill the template's, both ways."""
    build_out = ("phase-3a", "phase-5")
    composed = 0
    for step in steps.STEPS:
        if step.template is None or step.stage not in build_out:
            continue
        text = prompt_templates.render(
            step.template,
            slots={slot: f"<{slot}>" for slot in step.slots},
            constants=steps.CONSTANT_SLOTS,
            wave=step.unit is steps.Unit.WAVE,
            directory=templates,
        )
        assert text.strip()
        composed += 1

    assert composed == len([step for step in steps.STEPS if step.template and step.stage in build_out])


def test_no_build_out_template_names_a_stage_id_or_the_record_verb(templates: Path) -> None:
    """The lint over the stand-ins, so a real template that broke it would be caught the same way."""
    assert (
        prompt_templates.lint(prompt_templates.template_paths(templates), prohibited=steps.TEMPLATE_PROHIBITIONS) == []
    )
