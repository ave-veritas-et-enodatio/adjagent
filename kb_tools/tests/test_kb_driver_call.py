"""Call policy: retry, the one re-ask, and the three persistence routes.

Every case drives the real ``call.Caller`` through the same
``inference.invoke`` a live call goes through, against ``replay``'s synthetic
stream-json — save one, which substitutes the real ``SubprocessInvoker``
because the fault it is about is ``Popen``'s own and no synthetic invoker
raises it where it happens.
Two things are asserted about writes throughout, not only in the route tests:
that each route wrote exactly where its step row declares, and that nothing the
driver process wrote landed under ``kb-root/``.

The step rows are the real ones from ``steps.py`` — what the run loop will pass
— while the templates are local stand-ins named for the real ones, because the
shipped templates are the prompt engineer's and their prose is not what this
suite is about. **Two rows are local**, and for one reason: no shipped row is a
wave any more. The call machinery's wave route outlives the stages that used it
— the envelope parse, the one re-ask, the premature-dispatch check, the
one-member collapse, the two writer routes a wave takes — so the rows those
cases run through are built in this file rather than borrowed from a table that
no longer holds one.
"""

import json
from collections.abc import Callable, Mapping
from dataclasses import dataclass, replace
from pathlib import Path

import pytest

from kb_tools import inference, kb_pipeline
from kb_tools.kb_driver import baton, call, config, envelope, prompt_templates, replay, runlog, steps

# --- the templates a call composes against ----------------------------------

TEMPLATES: Mapping[str, str] = {
    "phase-5-overview-passage.single.tmpl": ("KB: @!dyn.kb-root!@\nFindings: @!dyn.remediation-source-path!@\n"),
    "docs.single.tmpl": ("README: @!dyn.readme-path!@\n"),
    "phase-5-review.single.tmpl": (
        "README: @!dyn.readme-path!@\nCONVENTIONS: @!dyn.conventions-path!@\n\n@!verdict-contract!@\n@!return-contract!@\n"
    ),
    "fix.wave.tmpl": (
        "Kind: @!dyn.remediation-kind!@\nAttempt @!dyn.attempt!@.\n"
        "Echo @!dyn.step-id!@.\n\n@!dyn.members!@\n\n@!dispatch-discipline!@\n@!envelope-contract!@\n@!deviation-contract!@\n"
    ),
    "review.wave.tmpl": (
        "Members:\n@!dyn.members!@\n\n@!persist-members!@\n"
        "@!dispatch-discipline!@\n@!envelope-contract!@\n@!deviation-contract!@\n"
    ),
}

# Keyed by bare name and placed under the fragments directory by the fixture, so
# the layout is stated once, where the composer states it.
FRAGMENTS: Mapping[str, str] = {
    "dispatch-discipline.tmpl": "Every member call sets run_in_background to false, and the turn is held.\n",
    "envelope-contract.tmpl": "Close with exactly one envelope block for @!dyn.step-id!@ and nothing after it.\n",
    "deviation-contract.tmpl": "Report deviations, one of: @!deviation-kinds!@\n",
    "persist-members.tmpl": "Write each member's return to that member's named path, verbatim.\n",
    "return-contract.tmpl": "Return the artifact as the final message body.\n",
    "verdict-contract.tmpl": "End with VERDICT: critical=<n> warning=<n> note=<n>\n",
}

# --- the returns a scenario can make ----------------------------------------

VERDICT_TEXT = "Findings: the tree is navigable.\n\nVERDICT: critical=0 warning=2 note=1"

DOCS_TEXT = "# README\n"

# A path slot's value is checked before the call is made — absolute, and there
# on disk — so these are functions of the repo under test rather than literals.
# The `repo` fixture stands both documents a review brief names up because a
# real `phase-5` meets them already written: `phase-3a`'s readiness stamp seeds
# CONVENTIONS.md and `ov.docs` assembles README.md in the stage before the
# review. Which of them the stage's own boundary then checks is a narrower set
# (`kb_pipeline.META_DOCS`) and not this fixture's question.


def docs_slots(repo: Path) -> dict[str, str]:
    return {"kb-root": str(repo / "kb-root"), "remediation-source-path": steps.NOTHING}


PASSAGE_TEXT = "This corpus argues one thing, and the place to start is its introduction.\n"

# The write-to-disk route at SINGLE, which the step table no longer holds a row
# for: `phase-5` assembles its document from a template and the index now, and
# the seat it calls returns prose the driver persists. The route itself is live
# machinery with a live wave* producer (`FIX_STEP` below), so the SINGLE half
# keeps a row here rather than losing its coverage with the row that used it.
DOCS_STEP = steps.Step(
    id="single.docs",
    stage="phase-5",
    unit=steps.Unit.SINGLE,
    writer=steps.Writer.WORKER,
    seat="tech-writer",
    template="docs.single.tmpl",
    slots=("readme-path",),
)


def docs_step_slots(repo: Path) -> dict[str, str]:
    return {"readme-path": str(repo / "kb-root" / kb_pipeline.OVERVIEW_DOC)}


#: The pair a review brief states, each named for itself. A zip over a document
#: set would drop `conventions-path` the moment that set stopped holding two
#: names, and a dropped required slot is refused by `steps.REQUIRED_PATH_SLOTS`
#: rather than noticed here.
REVIEW_DOCS: dict[str, str] = {
    "readme-path": kb_pipeline.OVERVIEW_DOC,
    "conventions-path": kb_pipeline.CONVENTIONS_DOC,
}


def review_slots(repo: Path) -> dict[str, str]:
    return {slot: str(repo / "kb-root" / name) for slot, name in REVIEW_DOCS.items()}


# The two local rows, and the values one call of the first carries. `wave.fix`
# is the worker-writes route at `wave*`: members write their own artifacts under
# `kb-root/` and the driver validates them, which is what makes the "the driver
# process writes nothing under kb-root" assertions worth making. It names a real
# seat, because `--agent` carries the name on the one-member collapse and a seat
# nothing defines would be a call no consumer could make.
FIX_STEP = steps.Step(
    id="wave.fix",
    stage="phase-5",
    unit=steps.Unit.WAVE_STAR,
    writer=steps.Writer.WORKER,
    seat="tech-writer",
    template="fix.wave.tmpl",
    slots=("remediation-kind", "members", "attempt", "step-id"),
    parses=(steps.Parse.ENVELOPE,),
)

FIX_SLOTS = {
    "remediation-kind": "verify-gate failure",
    "members": "- foundations: red paths under foundations/",
    "attempt": "1",
    "step-id": FIX_STEP.id,
}

FIX_MEMBERS = ("foundations", "dynamics")

# The wave-session route: never-writer members return text and the seatless
# session writes each return to the driver-named path.
WAVE_SESSION_STEP = steps.Step(
    id="wave.review",
    stage="phase-5",
    unit=steps.Unit.WAVE,
    writer=steps.Writer.WAVE_SESSION,
    template="review.wave.tmpl",
    slots=("members", "step-id"),
    parses=(steps.Parse.ENVELOPE,),
)


def _envelope_text(step_id: str, *, members: tuple[str, ...], invented: Mapping[str, object] | None = None) -> str:
    """The envelope a wave returns; ``invented`` adds keys the contract does not name."""
    payload: dict[str, object] = {
        "step": step_id,
        "members": [{"name": name, "status": "ok"} for name in members],
        "gaps": [],
        "deviations": [],
        **(invented or {}),
    }
    return "\n".join(
        ["The wave is complete.", "", envelope.ENVELOPE_OPEN, json.dumps(payload), envelope.ENVELOPE_CLOSE]
    )


# --- harness ----------------------------------------------------------------


@dataclass(frozen=True)
class Harness:
    """One caller, plus the evidence a test needs to look at afterwards."""

    caller: call.Caller
    invoker: replay.ReplayInvoker
    sleeps: list[float]
    seen: list[replay.ReplayContext]
    paths: runlog.RunPaths
    root: Path

    def brief(self, seq: int, label: str) -> Path:
        return self.paths.briefs / f"{seq:03d}-{label}.md"

    def stream(self, seq: int, label: str, attempt: int) -> Path:
        return self.paths.calls / f"{seq:03d}-{label}-a{attempt}.stream.jsonl"


def _config(*, attempts: int = 3, silence: int = 30) -> config.DriverConfig:
    return config.DriverConfig(
        path=Path("driver-run.toml"),
        invocation="--config driver-run.toml",
        run=config.RunSection(
            sources=("AcmeWidgets.tex",),
            permission_mode="acceptEdits",
            charter_file=Path(kb_pipeline.CHARTER_RELPATH),
            runner=None,
        ),
        claude=config.ClaudeSection(command=("claude",), env={}, brief_transport="stdin"),
        timeouts=config.TimeoutSection(single_seconds=60, wave_seconds=120, silence_seconds=silence, by_step={}),
        retry=config.RetrySection(transport_attempts=attempts, backoff_seconds=(5, 30)),
        log=config.LogSection(level="INFO", run_dir=Path(".claude-temp/kb-driver")),
        decisions={},
    )


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    """A repo root with the scratch layout root and a kb-root the driver may never write.

    Both documents a review brief names stand because ``phase-5`` meets them
    written — the readiness stamp seeds CONVENTIONS.md at ``phase-3a`` and
    ``ov.docs`` assembles README.md in the stage before the review — and a path
    slot is checked against disk before its call is made.
    """
    root = tmp_path / "repo"
    (root / steps.SCRATCH_ROOT).mkdir(parents=True)
    (root / "kb-root").mkdir()
    for name in REVIEW_DOCS.values():
        (root / "kb-root" / name).write_text(f"# {name}\n", encoding="utf-8")
    return root


@pytest.fixture
def build(tmp_path: Path, repo: Path) -> Callable[..., Harness]:
    """A caller over the local template set and a laid-out run directory."""
    templates = tmp_path / "templates"
    templates.mkdir()
    fragments = templates / prompt_templates.FRAGMENTS_DIRNAME
    fragments.mkdir()
    for name, text in TEMPLATES.items():
        (templates / name).write_text(text, encoding="utf-8")
    for name, text in FRAGMENTS.items():
        (fragments / name).write_text(text, encoding="utf-8")
    paths = runlog.prepare(tmp_path / "kb-driver", "run-0001")

    def _build(scenario: replay.Scenario, **overrides: int) -> Harness:
        sleeps: list[float] = []
        seen: list[replay.ReplayContext] = []

        def watched(context: replay.ReplayContext) -> replay.Response:
            seen.append(context)
            return scenario(context)

        invoker = replay.ReplayInvoker(watched)
        caller = call.Caller(
            invoker=invoker,
            config=_config(**overrides),
            repo_root=repo,
            paths=paths,
            prompt_templates_dir=templates,
            sleep=sleeps.append,
        )
        return Harness(caller=caller, invoker=invoker, sleeps=sleeps, seen=seen, paths=paths, root=tmp_path)

    return _build


def _writes(artifacts: Mapping[Path, str], scenario: replay.Scenario) -> replay.Scenario:
    """A scenario whose call writes its own artifacts — the worker and wave-session routes."""

    def wrapped(context: replay.ReplayContext) -> replay.Response:
        for path, text in artifacts.items():
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text, encoding="utf-8")
        return scenario(context)

    return wrapped


def _snapshot(root: Path) -> dict[Path, str]:
    return {path: path.read_text(encoding="utf-8") for path in root.rglob("*") if path.is_file()}


def _changed(before: Mapping[Path, str], after: Mapping[Path, str]) -> set[Path]:
    return {path for path, text in after.items() if before.get(path) != text}


def _fix_outputs(repo: Path, *domains: str) -> tuple[Path, ...]:
    return tuple(repo / "kb-root" / domain / f"{domain}.md" for domain in domains)


def _passage(repo: Path) -> Path:
    """``ov.docs``' one declared artifact: the prose answer, under the scratch layout."""
    return repo / steps.SCRATCH_ROOT / steps.overview_prose(stage=steps.STEPS_BY_ID["ov.docs"].stage)


# ---------------------------------------------------------------------------
# The three persistence routes, and the guarantee nothing lands under kb-root/
# ---------------------------------------------------------------------------


def test_the_driver_persists_a_never_writers_return_and_writes_nowhere_else(
    repo: Path, build: Callable[..., Harness]
) -> None:
    harness = build(replay.clean(VERDICT_TEXT))
    findings = (
        repo
        / steps.SCRATCH_ROOT
        / steps.findings(stage="phase-5", series=steps.SERIES_INITIAL, round_number=1, author="tech-writer-reviewer")
    )
    before = _snapshot(harness.root)

    outcome = harness.caller.execute(
        call.CallRequest(step=steps.STEPS_BY_ID["p5.review"], seq=3, slots=review_slots(repo), outputs=(findings,))
    )

    assert outcome.ok
    assert outcome.written == (findings,)
    assert findings.read_text(encoding="utf-8") == VERDICT_TEXT + "\n"
    # Parsed, never judged: the reviewer judges and the driver counts.
    assert outcome.verdict == envelope.Verdict(critical=0, warning=2, note=1)
    # The artifact, the composed brief, the capture — and nothing else, anywhere.
    assert _changed(before, _snapshot(harness.root)) == {
        findings,
        harness.brief(3, "p5.review"),
        harness.stream(3, "p5.review", 1),
    }


def test_the_write_to_disk_route_validates_the_document_and_writes_none_of_it(
    repo: Path, build: Callable[..., Harness]
) -> None:
    """The write-to-disk route: the seat writes the document, the driver only validates it."""
    readme = repo / "kb-root" / "README.md"
    harness = build(_writes({readme: DOCS_TEXT}, replay.clean("Wrote README.md.")))

    outcome = harness.caller.execute(
        call.CallRequest(step=DOCS_STEP, seq=3, slots=docs_step_slots(repo), outputs=(readme,))
    )

    assert outcome.ok
    assert outcome.written == (), "the seat wrote the document; the driver validated it"


def test_the_worker_writes_route_validates_the_artifacts_and_writes_none_of_them(
    repo: Path, build: Callable[..., Harness]
) -> None:
    outputs = _fix_outputs(repo, *FIX_MEMBERS)
    harness = build(
        _writes(
            {path: f"# {path.stem}\n" for path in outputs},
            replay.clean(_envelope_text(FIX_STEP.id, members=FIX_MEMBERS)),
        )
    )
    before = _snapshot(harness.root)

    outcome = harness.caller.execute(
        call.CallRequest(step=FIX_STEP, seq=1, slots=FIX_SLOTS, outputs=outputs, members=2)
    )

    assert outcome.ok
    assert outcome.written == ()  # the members wrote their own artifacts; the driver validated them
    assert outcome.envelope is not None
    assert [member.name for member in outcome.envelope.members] == list(FIX_MEMBERS)
    assert _changed(before, _snapshot(harness.root)) == {
        *outputs,
        harness.brief(1, FIX_STEP.id),
        harness.stream(1, FIX_STEP.id, 1),
    }
    # A WAVE_STAR of more than one member is seatless: a named seat would be a
    # re-created coordinator.
    assert harness.seen[0].agent is None


def test_the_wave_session_persists_route_leaves_every_write_to_the_session(
    repo: Path, build: Callable[..., Harness]
) -> None:
    reviewers = ("first-lens", "second-lens")
    outputs = tuple(repo / steps.SCRATCH_ROOT / "review" / f"phase-5-r1-{reviewer}.md" for reviewer in reviewers)
    harness = build(
        _writes(
            {path: f"# {path.stem}\n\nVERDICT: critical=0 warning=0 note=0\n" for path in outputs},
            replay.clean(_envelope_text(WAVE_SESSION_STEP.id, members=reviewers)),
        )
    )
    before = _snapshot(harness.root)

    outcome = harness.caller.execute(
        call.CallRequest(
            step=WAVE_SESSION_STEP,
            seq=12,
            slots={"members": "- first-lens\n- second-lens", "step-id": WAVE_SESSION_STEP.id},
            outputs=outputs,
            members=2,
        )
    )

    assert outcome.ok
    assert outcome.written == ()
    assert _changed(before, _snapshot(harness.root)) == {
        *outputs,
        harness.brief(12, WAVE_SESSION_STEP.id),
        harness.stream(12, WAVE_SESSION_STEP.id, 1),
    }


def test_the_driver_process_writes_nothing_under_kb_root(repo: Path, build: Callable[..., Harness]) -> None:
    # The artifact is under kb-root and the worker writes it: this module
    # validates existence and never touches the path. What assembles a document
    # under `kb-root/` is the run loop, from a template and the index, and it
    # reaches no persistence route here.
    step = DOCS_STEP
    target = repo / "kb-root" / "README.md"
    harness = build(_writes({target: DOCS_TEXT}, replay.clean("Wrote README.md.")))
    before = _snapshot(harness.root)

    outcome = harness.caller.execute(call.CallRequest(step=step, seq=2, slots=docs_step_slots(repo), outputs=(target,)))

    assert outcome.ok
    assert outcome.written == ()
    driver_wrote = _changed(before, _snapshot(harness.root)) - {target}
    assert driver_wrote == {harness.brief(2, step.id), harness.stream(2, step.id, 1)}
    assert not [path for path in driver_wrote if repo / "kb-root" in path.parents]


# ---------------------------------------------------------------------------
# The driver-persists route's write is atomic
# ---------------------------------------------------------------------------
#
# **The dying write is injected as data, not by patching.** A text carrying a
# lone surrogate cannot be encoded, so the write fails after the file has been
# opened and before all of its bytes are there — which is the shape a killed
# process leaves, reachable through whichever write call this module makes rather
# than through the one a patch happened to name. Every reader of these paths asks
# presence and non-emptiness and nothing else, so a zero-byte or truncated file
# under the final name reads as work that finished.

UNWRITABLE_TEXT = "A passage that does not survive encoding: " + "\ud800" + "\n"


def _persist_request(repo: Path, target: Path) -> call.CallRequest:
    return call.CallRequest(step=steps.STEPS_BY_ID["ov.docs"], seq=1, slots=docs_slots(repo), outputs=(target,))


def test_a_dying_persist_leaves_no_partial_artifact_under_the_final_name(
    repo: Path, build: Callable[..., Harness]
) -> None:
    harness = build(replay.clean(PASSAGE_TEXT))
    target = _passage(repo)

    with pytest.raises(UnicodeEncodeError):
        harness.caller._persist(_persist_request(repo, target), text=UNWRITABLE_TEXT)

    assert not target.exists(), "a write that died left a file under the name a resume reads as finished work"


def test_a_dying_persist_leaves_the_artifact_already_there_untouched(repo: Path, build: Callable[..., Harness]) -> None:
    """The target is the previous file or the whole new one, and there is no third state."""
    harness = build(replay.clean(PASSAGE_TEXT))
    target = _passage(repo)
    harness.caller._persist(_persist_request(repo, target), text=PASSAGE_TEXT)

    with pytest.raises(UnicodeEncodeError):
        harness.caller._persist(_persist_request(repo, target), text=UNWRITABLE_TEXT)

    assert target.read_text(encoding="utf-8") == PASSAGE_TEXT


def test_a_driver_persist_target_outside_the_scratch_root_is_a_boundary_error(
    repo: Path, build: Callable[..., Harness]
) -> None:
    harness = build(replay.clean(VERDICT_TEXT))
    stray = repo / "kb-root" / "phase-5-r1-review.md"

    with pytest.raises(runlog.BoundaryError, match="scratch layout root"):
        harness.caller.execute(
            call.CallRequest(step=steps.STEPS_BY_ID["p5.review"], seq=3, slots=review_slots(repo), outputs=(stray,))
        )
    assert not stray.exists()


def test_a_path_a_seat_could_not_act_on_is_refused_before_the_call(repo: Path, build: Callable[..., Harness]) -> None:
    """Relative, absent, or a named absence where the slot admits none.

    The first is measured: a review handed ``kb-root/README.md`` searched for a
    directory of that name, reviewed a different repository's knowledge base,
    and returned three critical findings about it. All three fail the same way
    at the seat — it goes looking — so all three are refused before it is asked.
    """
    harness = build(replay.clean(VERDICT_TEXT))
    findings = repo / steps.SCRATCH_ROOT / "review" / "phase-5-r1-tech-writer-reviewer.md"
    sound = review_slots(repo)
    broken = {
        "is relative": {**sound, "readme-path": "kb-root/README.md"},
        "is not there": {**sound, "readme-path": str(repo / "kb-root" / "absent.md")},
        "admits none": {**sound, "conventions-path": steps.NOTHING},
    }

    for complaint, slots in broken.items():
        with pytest.raises(runlog.BoundaryError, match=complaint):
            harness.caller.execute(
                call.CallRequest(step=steps.STEPS_BY_ID["p5.review"], seq=3, slots=slots, outputs=(findings,))
            )
    assert not harness.seen, "a brief no seat could act on was dispatched anyway"


def test_an_optional_path_slot_may_carry_the_named_absence(repo: Path, build: Callable[..., Harness]) -> None:
    """A first round has no findings to answer, which is an absence with a name."""
    passage = _passage(repo)
    harness = build(replay.clean(PASSAGE_TEXT))

    outcome = harness.caller.execute(
        call.CallRequest(step=steps.STEPS_BY_ID["ov.docs"], seq=3, slots=docs_slots(repo), outputs=(passage,))
    )

    assert outcome.ok
    assert steps.NOTHING in harness.brief(3, "ov.docs").read_text(encoding="utf-8")


def test_an_empty_return_never_becomes_an_artifact(repo: Path, build: Callable[..., Harness]) -> None:
    harness = build(replay.clean("   \n"))
    target = repo / steps.SCRATCH_ROOT / "review" / "phase-5-r1-tech-writer-reviewer.md"

    outcome = harness.caller.execute(
        call.CallRequest(step=steps.STEPS_BY_ID["p5.review"], seq=20, slots=review_slots(repo), outputs=(target,))
    )

    assert outcome.exit_code == baton.EXIT_CONTRACT
    assert not target.exists()


# ---------------------------------------------------------------------------
# Composition: the call unit, the seat, and the driver constants
# ---------------------------------------------------------------------------


def test_a_one_member_wave_star_is_a_single_that_names_its_seat(repo: Path, build: Callable[..., Harness]) -> None:
    # A step's call unit is a property of its member count, which is what makes
    # the one-domain mini-run exercise both paths without a second one.
    (output,) = _fix_outputs(repo, "foundations")
    harness = build(_writes({output: "# fixed\n"}, replay.clean(_envelope_text(FIX_STEP.id, members=("foundations",)))))

    outcome = harness.caller.execute(
        call.CallRequest(step=FIX_STEP, seq=1, slots=FIX_SLOTS, outputs=(output,), members=1)
    )

    assert outcome.ok
    assert harness.seen[0].agent == FIX_STEP.seat
    # The vocabulary the driver accepts and the prose the wave sees are one
    # definition, wired through steps.CONSTANT_SLOTS; the dispatch discipline is
    # injected by the composer, not restated per template.
    brief = harness.brief(1, FIX_STEP.id).read_text(encoding="utf-8")
    assert steps.CONSTANT_SLOTS["deviation-kinds"] in brief
    assert "run_in_background" in brief


def test_a_step_that_makes_no_call_is_a_boundary_error(build: Callable[..., Harness]) -> None:
    harness = build(replay.clean("ok"))

    with pytest.raises(runlog.BoundaryError, match="makes no call"):
        harness.caller.execute(call.CallRequest(step=steps.STEPS_BY_ID["p3a.record"], seq=4))


@pytest.mark.parametrize("smuggled", [["--model", "haiku"], ["--model=haiku"]])
def test_a_command_prefix_carrying_model_is_refused_in_either_spelling(
    repo: Path, build: Callable[..., Harness], smuggled: list[str]
) -> None:
    """Both spellings override every seat's frontmatter pin, so both are refused alike.

    Exact list membership reads the separated form and misses the joined one,
    which argparse accepts identically — so the pin was overridable with no log
    line, no exit-code change, and nothing on the card to read.
    """
    harness = build(replay.clean(PASSAGE_TEXT))
    caller = replace(
        harness.caller,
        config=replace(
            harness.caller.config,
            claude=replace(harness.caller.config.claude, command=("claude", *smuggled)),
        ),
    )

    with pytest.raises(runlog.BoundaryError, match="--model"):
        caller.execute(
            call.CallRequest(
                step=steps.STEPS_BY_ID["ov.docs"], seq=3, slots=docs_slots(repo), outputs=(_passage(repo),)
            )
        )

    assert harness.invoker.calls == 0


def test_an_empty_composed_brief_is_a_boundary_error_rather_than_a_call(
    repo: Path, build: Callable[..., Harness], monkeypatch: pytest.MonkeyPatch
) -> None:
    """The spawn boundary: nothing is worth spawning on a brief with nothing in it."""
    harness = build(replay.clean(PASSAGE_TEXT))

    def persist_nothing(briefs_dir: Path, *, seq: int, step_id: str, text: str) -> Path:
        del text
        path = briefs_dir / f"{seq:03d}-{step_id}.md"
        path.write_text("", encoding="utf-8")
        return path

    monkeypatch.setattr(prompt_templates, "persist", persist_nothing)

    with pytest.raises(runlog.BoundaryError, match="brief is empty"):
        harness.caller.execute(
            call.CallRequest(
                step=steps.STEPS_BY_ID["ov.docs"], seq=3, slots=docs_slots(repo), outputs=(_passage(repo),)
            )
        )

    assert harness.invoker.calls == 0


def test_a_call_spawned_while_another_is_live_is_a_boundary_error(repo: Path, build: Callable[..., Harness]) -> None:
    """Exactly one ``claude`` subprocess at a time — a policy of this driver's, held here.

    The re-entry is made from inside a replayed call, which is where a second
    spawn would happen for real: a scenario stands in for the process that is
    still live while the next one is started.
    """
    nested: list[runlog.BoundaryError] = []
    request = call.CallRequest(
        step=steps.STEPS_BY_ID["ov.docs"], seq=3, slots=docs_slots(repo), outputs=(_passage(repo),)
    )

    def reentrant(context: replay.ReplayContext) -> replay.Response:
        try:
            harness.caller.execute(replace(request, seq=4))
        except runlog.BoundaryError as exc:
            nested.append(exc)
        return replay.clean(PASSAGE_TEXT)(context)

    harness = build(reentrant)

    assert harness.caller.execute(request).ok
    assert [str(exc) for exc in nested] == ["a call would be spawned while another is still live"]
    # …and the guard released, so the next call is not poisoned by the last.
    assert harness.caller.execute(replace(request, seq=5)).ok


# ---------------------------------------------------------------------------
# Transport retry
# ---------------------------------------------------------------------------


def test_a_transport_death_is_retried_until_a_call_stands(repo: Path, build: Callable[..., Harness]) -> None:
    passage = _passage(repo)
    stands = replay.clean(PASSAGE_TEXT)
    harness = build(replay.sequence(replay.transport_die(), replay.transport_die(), stands))

    outcome = harness.caller.execute(
        call.CallRequest(step=steps.STEPS_BY_ID["ov.docs"], seq=3, slots=docs_slots(repo), outputs=(passage,))
    )

    assert outcome.ok
    assert outcome.attempts == 3
    assert harness.invoker.calls == 3
    assert harness.sleeps == [5.0, 30.0]  # the coordinator's own backoff: driver and briefs state one policy
    # Every attempt's capture is its own evidence file.
    assert all(harness.stream(3, "ov.docs", attempt).is_file() for attempt in (1, 2, 3))


def test_exhausted_transport_retries_exit_12(repo: Path, build: Callable[..., Harness]) -> None:
    harness = build(replay.transport_die(stderr="connection reset\n"))
    passage = _passage(repo)

    outcome = harness.caller.execute(
        call.CallRequest(step=steps.STEPS_BY_ID["ov.docs"], seq=3, slots=docs_slots(repo), outputs=(passage,))
    )

    assert outcome.exit_code == baton.EXIT_TRANSPORT
    assert harness.invoker.calls == 3
    assert outcome.detail[0] == "ov.docs: transport-failure after 3 attempt(s)"
    assert "connection reset" in outcome.detail
    assert not passage.exists()
    # A transport death is not a contract failure: the step is never re-asked.
    assert not harness.brief(3, f"ov.docs{call.REASK_SUFFIX}").exists()


def test_a_cli_rejection_is_never_retried_and_exits_13(repo: Path, build: Callable[..., Harness]) -> None:
    stderr = "error: unknown option '--frobnicate-widget'\n"
    harness = build(replay.cli_rejection(stderr))

    outcome = harness.caller.execute(
        call.CallRequest(step=steps.STEPS_BY_ID["ov.docs"], seq=3, slots=docs_slots(repo), outputs=(_passage(repo),))
    )

    assert outcome.exit_code == baton.EXIT_CONFIG
    assert harness.invoker.calls == 1  # retrying it is three identical failures and a misleading exit 12
    assert harness.sleeps == []
    assert stderr.strip() in outcome.detail


def test_a_command_that_cannot_be_spawned_exits_14_and_carries_a_restore_line(
    tmp_path: Path, repo: Path, build: Callable[..., Harness]
) -> None:
    """A missing or mistyped ``[claude] command`` is an environment fault, not a driver defect.

    The real ``SubprocessInvoker``, because the fault is ``Popen``'s own
    ``FileNotFoundError`` and a substituted invoker cannot raise it in the place
    that matters. Exit 14 is where ``ledger._run`` already puts a tool it could
    not spawn, and its card is the one that fits: relay the ``restore:`` line
    and re-run after it. Unclassified, this leaves as exit 15 — the code whose
    card names the run directory as a bug report against the driver.
    """
    absent = tmp_path / "no-such-claude"
    harness = build(replay.clean(PASSAGE_TEXT))
    caller = replace(
        harness.caller,
        invoker=inference.SubprocessInvoker(),
        config=replace(
            harness.caller.config,
            claude=replace(harness.caller.config.claude, command=(str(absent),)),
        ),
    )
    passage = _passage(repo)

    outcome = caller.execute(
        call.CallRequest(step=steps.STEPS_BY_ID["ov.docs"], seq=3, slots=docs_slots(repo), outputs=(passage,))
    )

    assert outcome.exit_code == baton.EXIT_ENVIRONMENT
    assert outcome.attempts == 1, "a command that is not on the path is not on it three times either"
    assert harness.sleeps == []
    assert str(absent) in outcome.detail[0]
    assert "restore:" in outcome.detail[0]
    assert not passage.exists()


def test_a_silence_wedge_is_a_transport_failure_and_exhausts_to_12(repo: Path, build: Callable[..., Harness]) -> None:
    harness = build(replay.stall(), attempts=1, silence=1)

    outcome = harness.caller.execute(
        call.CallRequest(step=steps.STEPS_BY_ID["ov.docs"], seq=3, slots=docs_slots(repo), outputs=(_passage(repo),))
    )

    assert outcome.exit_code == baton.EXIT_TRANSPORT
    assert outcome.detail[0].startswith(f"ov.docs: {inference.Outcome.SILENCE.value}")


# ---------------------------------------------------------------------------
# Contract validation and the one re-ask (exit 17)
# ---------------------------------------------------------------------------


def test_a_contract_failure_re_asks_the_same_step_once_with_the_complaint(
    repo: Path, build: Callable[..., Harness]
) -> None:
    harness = build(replay.sequence(replay.clean("no verdict anywhere in this text"), replay.clean(VERDICT_TEXT)))
    findings = (
        repo
        / steps.SCRATCH_ROOT
        / steps.findings(stage="phase-5", series=steps.SERIES_INITIAL, round_number=1, author="tech-writer-reviewer")
    )

    outcome = harness.caller.execute(
        call.CallRequest(step=steps.STEPS_BY_ID["p5.review"], seq=5, slots=review_slots(repo), outputs=(findings,))
    )

    assert outcome.ok
    assert outcome.verdict == envelope.Verdict(critical=0, warning=2, note=1)
    assert harness.invoker.calls == 2
    # Only the answer that stood became the artifact.
    assert findings.read_text(encoding="utf-8") == VERDICT_TEXT + "\n"

    first = harness.brief(5, "p5.review").read_text(encoding="utf-8")
    re_ask = harness.brief(5, f"p5.review{call.REASK_SUFFIX}").read_text(encoding="utf-8")
    assert re_ask.startswith(first.rstrip("\n"))  # the same brief …
    assert "VERDICT" in re_ask.removeprefix(first.rstrip("\n"))  # … with the validator's complaint attached


def test_an_envelope_carrying_an_invented_key_is_re_asked_and_the_step_recovers(
    repo: Path, build: Callable[..., Harness]
) -> None:
    """A strict envelope parse costs one re-ask, not the run.

    The refusal is the ``ParseError`` every contract failure raises, so it takes
    the same path: the wave that drops its invention on the second ask has its
    artifacts accepted and the build goes on. Only a second failure ends the
    invocation.
    """
    outputs = _fix_outputs(repo, *FIX_MEMBERS)
    harness = build(
        _writes(
            {path: f"# {path.stem}\n" for path in outputs},
            replay.sequence(
                replay.clean(_envelope_text(FIX_STEP.id, members=FIX_MEMBERS, invented={"elapsed_s": 41})),
                replay.clean(_envelope_text(FIX_STEP.id, members=FIX_MEMBERS)),
            ),
        )
    )

    outcome = harness.caller.execute(
        call.CallRequest(step=FIX_STEP, seq=1, slots=FIX_SLOTS, outputs=outputs, members=2)
    )

    assert outcome.ok
    assert harness.invoker.calls == 2
    re_ask = harness.brief(1, f"{FIX_STEP.id}{call.REASK_SUFFIX}").read_text(encoding="utf-8")
    assert "elapsed_s" in re_ask  # the complaint names the offending key


def test_a_second_contract_failure_exits_17_naming_the_step_and_the_complaint(
    repo: Path, build: Callable[..., Harness]
) -> None:
    harness = build(replay.clean("VERDICT: critical=none"))
    findings = (
        repo
        / steps.SCRATCH_ROOT
        / steps.findings(stage="phase-5", series=steps.SERIES_INITIAL, round_number=1, author="tech-writer-reviewer")
    )

    outcome = harness.caller.execute(
        call.CallRequest(step=steps.STEPS_BY_ID["p5.review"], seq=5, slots=review_slots(repo), outputs=(findings,))
    )

    assert outcome.exit_code == baton.EXIT_CONTRACT
    assert harness.invoker.calls == 2  # one ask, one re-ask, and no third
    assert outcome.detail[0].startswith("p5.review:")
    assert any("VERDICT" in line for line in outcome.detail[1:])
    assert not findings.exists()


def test_a_premature_dispatch_fails_the_contract_check_rather_than_passing_as_a_wave(
    repo: Path, build: Callable[..., Harness]
) -> None:
    # The async-dispatch shape: a first `result` announcing success before any
    # member ran. Accepting it records a completed wave with zero member
    # artifacts — the false green this architecture exists to eliminate.
    outputs = _fix_outputs(repo, *FIX_MEMBERS)
    body = _envelope_text(FIX_STEP.id, members=FIX_MEMBERS)
    harness = build(
        _writes(
            {path: f"# {path.stem}\n" for path in outputs},
            replay.sequence(replay.premature_dispatch(body), replay.clean(body)),
        )
    )

    outcome = harness.caller.execute(
        call.CallRequest(step=FIX_STEP, seq=1, slots=FIX_SLOTS, outputs=outputs, members=2)
    )

    assert outcome.ok  # the re-ask stood
    assert harness.invoker.calls == 2
    re_ask = harness.brief(1, f"{FIX_STEP.id}{call.REASK_SUFFIX}").read_text(encoding="utf-8")
    assert "init events in one call" in re_ask
    assert "run_in_background" in re_ask


def test_a_missing_declared_artifact_is_a_contract_failure_that_names_it(
    repo: Path, build: Callable[..., Harness]
) -> None:
    outputs = _fix_outputs(repo, *FIX_MEMBERS)
    harness = build(
        _writes(
            {outputs[0]: "# only the first member wrote\n"},
            replay.clean(_envelope_text(FIX_STEP.id, members=FIX_MEMBERS)),
        )
    )

    outcome = harness.caller.execute(
        call.CallRequest(step=FIX_STEP, seq=1, slots=FIX_SLOTS, outputs=outputs, members=2)
    )

    assert outcome.exit_code == baton.EXIT_CONTRACT
    complaint = " ".join(outcome.detail)
    assert str(outputs[1]) in complaint
    assert str(outputs[0]) not in complaint


def test_an_envelope_declaring_another_step_is_a_contract_failure(repo: Path, build: Callable[..., Harness]) -> None:
    (output,) = _fix_outputs(repo, "foundations")
    harness = build(_writes({output: "# fixed\n"}, replay.clean(_envelope_text("p4.fix", members=("foundations",)))))

    outcome = harness.caller.execute(
        call.CallRequest(step=FIX_STEP, seq=1, slots=FIX_SLOTS, outputs=(output,), members=1)
    )

    assert outcome.exit_code == baton.EXIT_CONTRACT
    assert any("p4.fix" in line for line in outcome.detail)
