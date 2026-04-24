#!/usr/bin/env python3
"""
Drift checker for shared coder agent sections.

Verifies that canonical text blocks appear verbatim in all covered agent
definition files. All canonical strings live here — there is no separate
template file.

Usage: python3 check-drift.py [--verbose]
Run from the repository root or any subdirectory.

─── Scope ────────────────────────────────────────────────────────────────────
Covered files (9):
  generalist-coder.md, go-coder.md, python-coder.md,
  android-app-expert.md, ios-app-expert.md, linux-app-expert.md,
  macos-app-expert.md, web-app-expert.md, windows-app-expert.md

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

─── Updating canonical strings ───────────────────────────────────────────────
When a shared section is intentionally changed, update the relevant string in
CHECKS below, then run this script to confirm all files are in sync.
"""

import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).parent
AGENTS_DIR = SCRIPT_DIR.parent

COVERED_FILES = [
    "generalist-coder.md",
    "go-coder.md",
    "python-coder.md",
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
        "**KEY GUIDELINE**: Code is cost, capability is value.",
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
        "No paid or commercial packages unless explicitly approved by the coordinator/user",
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
        "This thin abstraction is an explicit exception to the no-premature-abstraction principle.",
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
}


def check_file(filepath: Path) -> list[str]:
    content = filepath.read_text(encoding="utf-8")
    filename = filepath.name
    skipped = EXCEPTIONS.get(filename, [])
    failures = []

    for label, files, substring in CHECKS:
        if filename not in files:
            continue
        if label in skipped:
            continue
        if substring not in content:
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
    if all_clean:
        print("All sections match. No drift detected.")
        sys.exit(0)
    else:
        print("Drift detected. Update diverging files to match the canonical strings in this script.")
        sys.exit(1)


if __name__ == "__main__":
    main()
