#!/usr/bin/env python3
"""
Drift checker for shared coder agent sections.

Verifies that canonical text blocks appear verbatim in all covered agent
definition files. All canonical strings live here — there is no separate
template file.

Matching is whitespace-normalized: runs of whitespace (including newlines)
collapse to a single space on both sides of the comparison. Canonical text
therefore matches regardless of how a file hard-wraps it — shell-dsl-coder.md
wraps prose at ~70 columns while the rest use one line per paragraph.

Usage: python3 check-drift.py [--verbose]
Run from the repository root or any subdirectory.

─── Scope ────────────────────────────────────────────────────────────────────
Covered files (11):
  generalist-coder.md, go-coder.md, python-coder.md, rust-coder.md,
  shell-dsl-coder.md, android-app-expert.md, ios-app-expert.md,
  linux-app-expert.md, macos-app-expert.md, web-app-expert.md,
  windows-app-expert.md

Not covered: architect.md, security-reviewer.md — distinct structures,
  not members of the coder/platform expert family.

─── Expected variation (not drift) ──────────────────────────────────────────
These sections intentionally differ per file and are not checked:

  Data formats (opening)      generalist/go/python use "TOML is the preferred
                              format for project-owned..."; platform experts use
                              "TOML for project-owned..." — both correct. Check
                              uses the common substring.

  Logging abstraction         python-coder uses logging.getLogger(__name__)
  exception                   directly — no thin wrapper, so the exception note
                              does not apply. See EXCEPTIONS dict below.

  Logging directive           Platform logging API differs per file (OSLog,
                              android.util.Log, slog, ILogger, etc.)

  Build system                Platform build tool invocation differs per file
                              (./gradlew, xcodebuild, msbuild, etc.)

  Dependencies (trailing)     Platform package repo differs per file
                              (Maven Central, major distro repos, etc.)

  Platform-adjacent files     Platform-specific file types in Declare Scope

  Mocking threshold           Tier (2 platform / 5 general) and rationale text

  Core Expertise / Gotchas    Platform-specific — entirely unique per file

  Post-mortem bullets         Platform-specific wording in Over-constraint bullet

  Output Format               generalist-coder has its own expanded variant;
                              go-coder and python-coder use the stopping-early
                              format only (no separate "When done:" block)

  Dependencies wording        rust-coder says "crates" where the others say
                              "packages". Check uses the common substring
                              (the coordinator/user approval clause).

  Logging exception phrasing  rust-coder embeds the exception mid-sentence
                              ("that thin abstraction...") rather than opening
                              with it. Check uses the common substring.

  KEY GUIDELINE continuation  shell-dsl-coder continues the sentence with an
                              em dash instead of ending it. Check omits the
                              trailing period.

  Boundary-check boundaries   rust-coder lists Rust-idiomatic boundaries (FFI,
                              file/socket I/O, channel/thread) instead of the
                              general list. Exempted — see EXCEPTIONS.

  shell-dsl-coder structure   Terse, ~70-column file with no three-layer
                              testing block, no shared Core Principles block,
                              and its own Output Format. It carries the
                              KEY GUIDELINE core sentence and the integration-
                              test clause; everything else is exempted
                              individually in EXCEPTIONS rather than
                              bulk-skipped, so adopting any one of them later
                              is a one-line deletion.

─── Updating canonical strings ───────────────────────────────────────────────
When a shared section is intentionally changed, update the relevant string in
CHECKS below, then run this script to confirm all files are in sync.
"""

import re
import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).parent
AGENTS_DIR = SCRIPT_DIR.parent

COVERED_FILES = [
    "generalist-coder.md",
    "go-coder.md",
    "python-coder.md",
    "rust-coder.md",
    "shell-dsl-coder.md",
    "android-app-expert.md",
    "ios-app-expert.md",
    "linux-app-expert.md",
    "macos-app-expert.md",
    "web-app-expert.md",
    "windows-app-expert.md",
]

PLATFORM_EXPERTS = [
    "android-app-expert.md",
    "ios-app-expert.md",
    "linux-app-expert.md",
    "macos-app-expert.md",
    "web-app-expert.md",
    "windows-app-expert.md",
]

# go-coder, python-coder, and platform experts (not generalist)
NON_GENERALIST = [f for f in COVERED_FILES if f != "generalist-coder.md"]


# Each check: (label, files_to_check, canonical_substring)
# canonical_substring must appear verbatim somewhere in the file content.
CHECKS = [
    (
        "KEY GUIDELINE — core sentence",
        COVERED_FILES,
        # no trailing period — shell-dsl-coder continues the sentence
        "**KEY GUIDELINE**: Code is cost, capability is value",
    ),
    (
        "KEY GUIDELINE — default to omission",
        COVERED_FILES,
        "When uncertain whether to add something, default to omission.",
    ),
    (
        "KEY GUIDELINE — performance exception",
        COVERED_FILES,
        "Exception: when performance is the requirement, complexity that demonstrably satisfies it is justified",
    ),
    (
        "Data formats — TOML project-owned",
        COVERED_FILES,
        # generalist/go/python: "TOML is the preferred format for project-owned..."
        # platform experts:     "TOML for project-owned..."
        # common substring covers both:
        "project-owned configuration and structured data files",
    ),
    (
        "Dependencies — commercial packages escape clause",
        COVERED_FILES,
        # rust-coder says "crates", the rest say "packages" — common substring:
        "unless explicitly approved by the coordinator/user",
    ),
    (
        "Boundary checks — concrete definition",
        COVERED_FILES,
        "external API calls, user input parsing, database writes, IPC, and queue boundaries",
    ),
    (
        "Boundary checks — scope rule",
        COVERED_FILES,
        "Apply these only when the change directly touches or creates such a boundary",
    ),
    (
        "Dissent protocol",
        COVERED_FILES,
        "If you believe a directive would produce technically incorrect output, state the concern and your recommended alternative before proceeding — do not silently comply.",
    ),
    (
        "Logging abstraction exception",
        COVERED_FILES,
        # python-coder exempted in EXCEPTIONS — see module docstring
        # leading demonstrative omitted: rust-coder embeds this as "that thin..."
        "thin abstraction is an explicit exception to the no-premature-abstraction principle.",
    ),
    (
        "Memory directive explanation",
        COVERED_FILES,
        "`memory: user` in the frontmatter is a harness-level directive; the path below is for project-local notes this agent writes",
    ),
    (
        "Stopping-early format — Discovered bullet",
        NON_GENERALIST,
        # generalist-coder exempted in EXCEPTIONS
        "- **Discovered**: what was found — the conflict, the expansion, the design gap",
    ),
    (
        "Stopping-early format — Recommendation bullet (clean form)",
        NON_GENERALIST,
        # generalist-coder exempted in EXCEPTIONS
        "- **Recommendation**: your assessment of how to proceed",
    ),
    (
        "Done output format — Changed bullet",
        PLATFORM_EXPERTS,
        "- **Changed**: list files modified and a one-line summary of each change",
    ),
    (
        "Done output format — Blockers bullet",
        PLATFORM_EXPERTS,
        "- **Blockers**: any issues that prevent completing the task or that require human/coordinator decision",
    ),
    (
        "Mocking threshold — rationale present",
        COVERED_FILES,
        "native platform APIs have non-mockable runtime behavior",
    ),
    (
        "Platform-adjacent files exception",
        PLATFORM_EXPERTS,
        "Platform-required adjacent files",
    ),
    (
        "Integration tests — public surface of the delivered artifact",
        COVERED_FILES,
        "**Integration tests exercise the delivered artifact** through its public "
        "surface (the binary/API as shipped), never in-process calls to internals",
    ),
    (
        "Integration tests — no dev-only entry points",
        COVERED_FILES,
        "Never create dev-only entry points or test-only verbs to make testing easier",
    ),
    (
        "Integration tests — dev-only switches behind config, not env",
        COVERED_FILES,
        "live behind a config-file setting, never an environment variable",
    ),
]

# Known acceptable divergences — skip these checks for these files.
# Each entry documents why the exemption exists.
EXCEPTIONS: dict[str, list[str]] = {
    # generalist-coder has its own expanded Output Format section with a
    # different stopping-early variant
    "generalist-coder.md": [
        "Stopping-early format — Discovered bullet",
        "Stopping-early format — Recommendation bullet (clean form)",
    ],
    # python-coder uses logging.getLogger(__name__) directly with no thin
    # wrapper — the logging abstraction exception note is not applicable
    "python-coder.md": [
        "Logging abstraction exception",
    ],
    # rust-coder enumerates Rust-idiomatic boundaries (FFI calls, file/socket
    # I/O, channel/thread) rather than the general list — same rule, right
    # nouns for the language
    "rust-coder.md": [
        "Boundary checks — concrete definition",
    ],
    # shell-dsl-coder is a terse, structurally distinct file: no three-layer
    # testing block, no shared Core Principles block, its own Output Format.
    # It carries the KEY GUIDELINE core sentence and the integration-test
    # clause; the rest have no home in it yet. Listed individually so adopting
    # any one of them later is a one-line deletion.
    "shell-dsl-coder.md": [
        "KEY GUIDELINE — default to omission",
        "KEY GUIDELINE — performance exception",
        "Data formats — TOML project-owned",
        "Dependencies — commercial packages escape clause",
        "Boundary checks — concrete definition",
        "Boundary checks — scope rule",
        "Dissent protocol",
        "Logging abstraction exception",
        "Memory directive explanation",
        "Mocking threshold — rationale present",
        "Stopping-early format — Discovered bullet",
        "Stopping-early format — Recommendation bullet (clean form)",
    ],
}


# Files in agents/ that are NOT expected to appear in coordinator.md's
# "Available specialists" list. The coordinator dispatches coder, platform-expert,
# reviewer, and specialist agents; it does not dispatch the MAD or KB orchestrators
# (those have their own referees/coordinators) or itself.
COORDINATOR_LIST_EXCLUSIONS = {
    "coordinator.md",
    "README.md",
}


def extract_coordinator_specialists() -> tuple[set[str], list[str]]:
    """Return (agent names listed in coordinator.md, error messages)."""
    coord = AGENTS_DIR / "coordinator.md"
    if not coord.exists():
        return set(), ["coordinator.md not found"]
    text = coord.read_text(encoding="utf-8")
    # Section starts at "Available specialists" and ends at the next "## " heading.
    match = re.search(
        r"Available specialists.*?(?=^##\s)", text, re.DOTALL | re.MULTILINE
    )
    if not match:
        return set(), ["could not find 'Available specialists' section"]
    section = match.group(0)
    # Agent names appear as `agent-name` (backtick-wrapped, lowercase, hyphens).
    names = set(re.findall(r"`([a-z][a-z0-9-]+)`", section))
    return names, []


def check_coordinator_specialists() -> list[str]:
    """Verify coordinator.md's specialist list matches the agent directory."""
    listed, errors = extract_coordinator_specialists()

    expected: set[str] = set()
    for path in AGENTS_DIR.glob("*.md"):
        if path.name in COORDINATOR_LIST_EXCLUSIONS:
            continue
        name = path.stem
        if name.startswith("mad-") or name.startswith("kb-"):
            continue
        expected.add(name)

    missing = sorted(expected - listed)
    extra = sorted(listed - expected)

    if missing:
        errors.append(
            f"agent file(s) exist but not listed in coordinator.md: {missing}"
        )
    if extra:
        errors.append(
            f"listed in coordinator.md but no agent file exists: {extra}"
        )
    return errors


def normalize(text: str) -> str:
    """Collapse whitespace runs so hard-wrapped prose matches one-line prose."""
    return re.sub(r"\s+", " ", text)


def check_file(filepath: Path) -> list[str]:
    content = normalize(filepath.read_text(encoding="utf-8"))
    filename = filepath.name
    skipped = EXCEPTIONS.get(filename, [])
    failures = []

    for label, files, substring in CHECKS:
        if filename not in files:
            continue
        if label in skipped:
            continue
        if normalize(substring) not in content:
            failures.append(label)

    return failures


def main() -> None:
    verbose = "--verbose" in sys.argv or "-v" in sys.argv
    all_clean = True
    results: dict[str, list[str]] = {}

    for filename in COVERED_FILES:
        filepath = AGENTS_DIR / filename
        if not filepath.exists():
            print(f"  MISSING  {filename}")
            all_clean = False
            continue
        results[filename] = check_file(filepath)

    print()
    print("Coder agent drift check")
    print("=" * 60)

    for filename in COVERED_FILES:
        if filename not in results:
            continue
        failures = results[filename]
        if failures:
            all_clean = False
            status = f"DRIFT  ({len(failures)} section{'s' if len(failures) != 1 else ''})"
            print(f"  {status:<30} {filename}")
            for f in failures:
                print(f"      missing: {f}")
        elif verbose:
            print(f"  {'OK':<30} {filename}")

    print()
    print("Coordinator specialist list check")
    print("=" * 60)
    coord_errors = check_coordinator_specialists()
    if coord_errors:
        all_clean = False
        for err in coord_errors:
            print(f"  DRIFT  {err}")
    elif verbose:
        print("  OK")

    print()
    if all_clean:
        print("All sections match. No drift detected.")
        sys.exit(0)
    else:
        print("Drift detected. Update diverging files to match the canonical strings in this script.")
        sys.exit(1)


if __name__ == "__main__":
    main()
