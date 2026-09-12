"""Config validation and the barrier-decision vocabulary.

Every refusal here is exit 13 at load, before anything runs: a typo must not
become a mid-build stop three hours in. The tests are a table of the ways a
config or a ``--decide`` value can be wrong, plus the typed values a good one
yields.
"""

from pathlib import Path

import pytest

from kb_tools import kb_pipeline
from kb_tools.kb_driver import config, steps

MINIMAL = """
[run]
sources = ["AcmeWidgets.tex"]
permission_mode = "acceptEdits"
"""

# The registry the barrier module will supply once it lands; injected here so
# answer-set validation is testable without importing it.
ADMISSIBLE = {
    "phase-1b.design-gate": frozenset({"approve", "revise", "cancel"}),
    "start.build-mode": frozenset({"fresh", "revision"}),
}


def _write(tmp_path: Path, body: str) -> Path:
    path = tmp_path / "driver-run.toml"
    path.write_text(body, encoding="utf-8")
    return path


# ---------------------------------------------------------------------------
# Accepted configs
# ---------------------------------------------------------------------------


def test_minimal_config_applies_every_default(tmp_path: Path) -> None:
    cfg = config.load(_write(tmp_path, MINIMAL))

    assert cfg.run.sources == ("AcmeWidgets.tex",)
    assert cfg.run.permission_mode == "acceptEdits"
    assert cfg.run.build_mode == "fresh"
    assert cfg.run.charter_file == Path(config.DEFAULT_CHARTER_FILE)
    assert cfg.run.runner is None
    assert cfg.claude.command == ("claude",)
    assert cfg.claude.brief_transport == "stdin"
    assert cfg.claude.env == {}
    assert cfg.timeouts.single_seconds == config.DEFAULT_SINGLE_SECONDS
    assert cfg.timeouts.by_step == {}
    assert cfg.retry.backoff_seconds == config.DEFAULT_BACKOFF_SECONDS
    assert cfg.log.level == "INFO"
    assert cfg.log.run_dir == Path(config.DEFAULT_RUN_DIR)
    assert cfg.decisions == {}


def test_full_config_is_typed_through(tmp_path: Path) -> None:
    body = """
[run]
sources = ["a.tex", "b.tex"]
permission_mode = "bypassPermissions"
build_mode = "revision"
charter_file = "scratch/charter.md"
runner = "make"

[claude]
command = ["claude", "--dangerously-skip-update"]
env = { ANTHROPIC_LOG = "debug" }
brief_transport = "file"

[timeouts]
single_seconds = 60
wave_seconds = 120
silence_seconds = 30
[timeouts.by_step]
"p2.5.claims" = 14400

[retry]
transport_attempts = 2
backoff_seconds = [1, 2, 3]

[log]
level = "DEBUG"
run_dir = "/var/tmp/kb-driver"

[barriers.phase-1b.design-gate]
decision = "approve"
note = "Approve."
"""
    cfg = config.load(_write(tmp_path, body), admissible=ADMISSIBLE)

    assert cfg.run.sources == ("a.tex", "b.tex")
    assert cfg.run.build_mode == "revision"
    assert cfg.run.charter_file == Path("scratch/charter.md")
    assert cfg.run.runner == "make"
    assert cfg.claude.command == ("claude", "--dangerously-skip-update")
    assert cfg.claude.env == {"ANTHROPIC_LOG": "debug"}
    assert cfg.claude.brief_transport == "file"
    assert cfg.timeouts.by_step == {"p2.5.claims": 14400}
    assert cfg.retry.transport_attempts == 2
    assert cfg.retry.backoff_seconds == (1, 2, 3)
    assert cfg.log.run_dir == Path("/var/tmp/kb-driver")

    decision = cfg.decisions["phase-1b.design-gate"]
    assert (decision.stage, decision.kind, decision.answer) == ("phase-1b", "design-gate", "approve")
    assert decision.note == "Approve."
    assert decision.source == "config"
    assert decision.spec == "phase-1b.design-gate=approve"


@pytest.mark.parametrize("mode", config.PERMISSION_MODES)
def test_every_probed_permission_mode_is_accepted(tmp_path: Path, mode: str) -> None:
    body = f'[run]\nsources = ["a.tex"]\npermission_mode = "{mode}"\n'
    assert config.load(_write(tmp_path, body)).run.permission_mode == mode


def test_a_file_naming_no_permission_mode_gets_the_default(tmp_path: Path) -> None:
    body = '[run]\nsources = ["a.tex"]\n'
    assert config.load(_write(tmp_path, body)).run.permission_mode == config.DEFAULT_PERMISSION_MODE


# ---------------------------------------------------------------------------
# No file at all, and the two doors together
# ---------------------------------------------------------------------------


def test_flags_alone_specify_a_run_and_every_other_field_defaults() -> None:
    """The launch that composes nothing: one field given, the rest as they ship."""
    cfg = config.load(None, run_overrides={"sources": ("a.tex", "b.tex")})

    assert cfg.path is None
    assert cfg.run.sources == ("a.tex", "b.tex")
    assert cfg.run.permission_mode == config.DEFAULT_PERMISSION_MODE
    assert cfg.run.build_mode == config.DEFAULT_BUILD_MODE
    assert cfg.run.charter_file == Path(config.DEFAULT_CHARTER_FILE)
    assert cfg.run.runner is None
    assert cfg.claude.command == config.DEFAULT_CLAUDE_COMMAND
    assert cfg.log.run_dir == Path(config.DEFAULT_RUN_DIR)
    assert cfg.decisions == {}


def test_a_flag_wins_over_the_file_for_the_field_it_names(tmp_path: Path) -> None:
    """Precedence, and its bound: an override replaces its own field and no other."""
    body = MINIMAL + 'build_mode = "revision"\n[barriers.phase-1b.design-gate]\ndecision = "approve"\n'

    cfg = config.load(
        _write(tmp_path, body),
        run_overrides={"sources": ("flagged.tex",), "permission_mode": "plan"},
        admissible=ADMISSIBLE,
    )

    assert cfg.run.sources == ("flagged.tex",), "a repeated --source replaces the list rather than extending it"
    assert cfg.run.permission_mode == "plan"
    assert cfg.run.build_mode == "revision", "a field no flag names keeps the file's value"
    assert cfg.decisions["phase-1b.design-gate"].answer == "approve"


def test_the_field_with_no_default_is_refused_naming_both_doors() -> None:
    with pytest.raises(config.ConfigError) as excinfo:
        config.load(None, run_overrides={"permission_mode": "acceptEdits"})

    message = str(excinfo.value)
    assert "[run] sources" in message
    assert config.SOURCE_FLAG in message


def test_a_run_that_names_no_permission_mode_gets_the_headless_default() -> None:
    """The mode is an override, not a requirement: a build launches without naming it."""
    cfg = config.load(None, run_overrides={"sources": ("a.tex",)})

    assert cfg.run.permission_mode == "bypassPermissions"
    assert cfg.run.permission_mode == config.DEFAULT_PERMISSION_MODE


def test_a_flag_value_is_refused_in_the_same_words_its_config_key_would_be() -> None:
    """One vocabulary for both doors: the flags carry no validation of their own."""
    with pytest.raises(config.ConfigError, match="permission_mode"):
        config.load(None, run_overrides={"sources": ("a.tex",), "permission_mode": "yolo"})


@pytest.mark.parametrize(
    ("path", "overrides", "expected"),
    [
        (Path("driver-run.toml"), {}, "--config driver-run.toml"),
        (
            None,
            {"sources": ("a.tex", "b c.tex"), "permission_mode": "auto"},
            "--source a.tex --source 'b c.tex' --permission-mode auto",
        ),
        (
            Path("driver-run.toml"),
            {"permission_mode": "auto"},
            "--config driver-run.toml --permission-mode auto",
        ),
        (None, {}, ""),
    ],
)
def test_the_resume_line_reproduces_what_the_run_was_given(
    path: Path | None, overrides: dict[str, object], expected: str
) -> None:
    """The relay card hands back an invocation the operator made, never one they did not.

    Both doors together is the case that matters: a resume naming only the
    config would drop the flag that overrode it and resume a different run.
    """
    assert config.invocation(path, overrides) == expected


# ---------------------------------------------------------------------------
# Refused configs — the validation table
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("case", "body", "expected"),
    [
        (
            "unknown permission mode",
            '[run]\nsources = ["a.tex"]\npermission_mode = "yolo"\n',
            "permission_mode",
        ),
        ("missing sources", '[run]\npermission_mode = "auto"\n', "sources"),
        ("empty sources", '[run]\nsources = []\npermission_mode = "auto"\n', "sources"),
        ("sources not strings", '[run]\nsources = [1]\npermission_mode = "auto"\n', "sources"),
        ("missing [run]", '[log]\nlevel = "INFO"\n', "sources"),
        ("unknown build mode", MINIMAL + 'build_mode = "sideways"\n', "build_mode"),
        ("unknown runner", MINIMAL + 'runner = "cmake"\n', "runner"),
        ("bad brief transport", MINIMAL + '[claude]\nbrief_transport = "argv"\n', "brief_transport"),
        ("command not a list", MINIMAL + '[claude]\ncommand = "claude"\n', "command"),
        ("env value not a string", MINIMAL + "[claude]\nenv = { A = 1 }\n", "env"),
        ("timeout not an integer", MINIMAL + '[timeouts]\nsingle_seconds = "fast"\n', "single_seconds"),
        ("timeout not positive", MINIMAL + "[timeouts]\nwave_seconds = 0\n", "wave_seconds"),
        ("backoff not integers", MINIMAL + '[retry]\nbackoff_seconds = ["5s"]\n', "backoff_seconds"),
        ("unknown log level", MINIMAL + '[log]\nlevel = "CHATTY"\n', "level"),
        ("barrier table has no decision", MINIMAL + '[barriers.phase-1b.design-gate]\nnote = "hi"\n', "decision"),
        ("barrier stage is not a table", MINIMAL + '[barriers]\nphase-1b = "approve"\n', "barriers.phase-1b"),
    ],
)
def test_invalid_config_is_refused_naming_the_key(tmp_path: Path, case: str, body: str, expected: str) -> None:
    with pytest.raises(config.ConfigError) as excinfo:
        config.load(_write(tmp_path, body))
    assert expected in str(excinfo.value), case


@pytest.mark.parametrize(
    ("case", "body", "key"),
    [
        ("per-step table", '[claude.model_by_step]\n"p2.5.claims" = "haiku"\n', "model_by_step"),
        ("bare model", '[claude]\nmodel = "haiku"\n', "model"),
    ],
)
def test_model_key_is_rejected_at_load_naming_the_key(tmp_path: Path, case: str, body: str, key: str) -> None:
    # An explicit --model overrides a seat's frontmatter pin, so the driver
    # never passes it and no model key may be honored. Exit 13's baton reports
    # the named key, so the key must appear in the message.
    with pytest.raises(config.ConfigError) as excinfo:
        config.load(_write(tmp_path, MINIMAL + body))
    message = str(excinfo.value)
    assert key in message, case
    assert "--model" in message, case


def test_missing_config_file_is_a_config_error(tmp_path: Path) -> None:
    with pytest.raises(config.ConfigError, match="not found"):
        config.load(tmp_path / "absent.toml")


def test_malformed_toml_is_a_config_error(tmp_path: Path) -> None:
    with pytest.raises(config.ConfigError, match="not valid TOML"):
        config.load(_write(tmp_path, "[run\nsources = "))


def test_out_of_set_barrier_decision_is_refused_at_load(tmp_path: Path) -> None:
    body = MINIMAL + '[barriers.phase-1b.design-gate]\ndecision = "approve-ish"\n'
    with pytest.raises(config.ConfigError, match="not admissible"):
        config.load(_write(tmp_path, body), admissible=ADMISSIBLE)


def test_unregistered_barrier_pair_is_refused_at_load(tmp_path: Path) -> None:
    body = MINIMAL + '[barriers.phase-9.made-up]\ndecision = "proceed"\n'
    with pytest.raises(config.ConfigError, match="unknown barrier"):
        config.load(_write(tmp_path, body), admissible=ADMISSIBLE)


def test_barrier_answers_are_unchecked_without_a_registry(tmp_path: Path) -> None:
    # Form only: the registry lives above config in the dependency direction,
    # so a caller that has not loaded it gets structural validation alone.
    body = MINIMAL + '[barriers.phase-1b.design-gate]\ndecision = "approve-ish"\n'
    assert config.load(_write(tmp_path, body)).decisions["phase-1b.design-gate"].answer == "approve-ish"


# ---------------------------------------------------------------------------
# --decide
# ---------------------------------------------------------------------------


def test_decide_parses_pair_answer_and_note() -> None:
    decision = config.parse_decision("phase-1b.design-gate=revise:tighten the domain split")
    assert (decision.stage, decision.kind, decision.answer) == ("phase-1b", "design-gate", "revise")
    assert decision.note == "tighten the domain split"
    assert decision.source == "cli"


def test_decide_splits_a_dotted_stage_id_on_its_last_dot() -> None:
    decision = config.parse_decision("phase-2.5.cap-exhausted=stop")
    assert (decision.stage, decision.kind) == ("phase-2.5", "cap-exhausted")


@pytest.mark.parametrize(
    "spec",
    [
        "phase-1b.design-gate",  # no answer
        "design-gate=approve",  # no stage
        "=approve",  # no pair
        "phase-1b.design-gate=",  # empty answer
        "phase-1b.design-gate=Approve",  # answers are lowercase tokens
        "phase-1b.design-gate=approve now",  # free text belongs after the colon
        ".design-gate=approve",  # empty stage
    ],
)
def test_malformed_decide_is_refused(spec: str) -> None:
    with pytest.raises(config.ConfigError, match="malformed"):
        config.parse_decision(spec)


def test_decide_answer_is_checked_against_the_registry() -> None:
    with pytest.raises(config.ConfigError, match="not admissible"):
        config.parse_decision("phase-1b.design-gate=maybe", admissible=ADMISSIBLE)


def test_decide_pair_is_checked_against_the_registry() -> None:
    with pytest.raises(config.ConfigError, match="unknown barrier"):
        config.parse_decision("phase-9.made-up=proceed", admissible=ADMISSIBLE)


# ---------------------------------------------------------------------------
# The two mode flags, and the one bound
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("key", ["no_inference", "dry_run"])
def test_each_mode_flag_defaults_off_and_is_carried_through(tmp_path: Path, key: str) -> None:
    assert getattr(config.load(_write(tmp_path, MINIMAL)).run, key) is False
    assert getattr(config.load(_write(tmp_path, MINIMAL), run_overrides={key: True}).run, key) is True
    assert getattr(config.load(_write(tmp_path, MINIMAL + f"{key} = true\n")).run, key) is True


def test_both_mode_flags_are_rendered_into_the_resume_line_and_the_bound_is_not() -> None:
    """What a resume must keep, and what it must drop.

    A resume dropping either mode flag would change the build half way through
    — spending the calls it was told to do without, or spending real ones a
    smoke test never meant to. A resume keeping the bound would stop in the
    same place forever, which is what resuming is for.
    """
    line = config.invocation(
        None,
        {"sources": ("a.tex",), "no_inference": True, "dry_run": True, "through": "start"},
    )

    assert line == f"--source a.tex {config.NO_INFERENCE_FLAG} {config.DRY_RUN_FLAG}"
    assert config.THROUGH_FLAG not in line


def test_a_run_spending_no_inference_may_still_be_bounded_anywhere(tmp_path: Path) -> None:
    """The refusal that went with the old meaning is gone, and this is why.

    ``no_inference`` bounded the walk once, so a ``through`` past where it
    stopped was unreachable and refused at load. It bounds nothing now — every
    stage is walked — so every stage is a reachable bound and the two compose
    without a rule pairing them.
    """
    for stage in kb_pipeline.STAGE_IDS:
        cfg = config.load(_write(tmp_path, MINIMAL + "no_inference = true\n"), run_overrides={"through": stage})
        assert cfg.run.through == stage and cfg.run.no_inference


def test_the_rows_a_no_inference_run_drops_are_the_step_tables_answer() -> None:
    """Config carries the flag and classifies nothing — the table owns which rows go."""
    dropped = [step.id for step in steps.STEPS if step.spends_inference]

    assert dropped
    assert all(
        not steps.applies(step, build_mode="fresh", spend_inference=False) for step in steps.STEPS if step.id in dropped
    )
