#!/usr/bin/env python3
"""
Generator and consistency checker for the agent definitions in this repository.

Each definition is generated from a template:

    templates/<name>.md.tmpl  +  templates/shared-sections.toml  ->  <name>.md

The template holds everything unique to that agent. Every span of text shared
with other definitions is a marker referencing a chunk in shared-sections.toml,
so shared text lives in exactly one place.

Enrollment is template existence, and nothing else. There is no enrollment list
and no exclusion list: the tool generates every definition its templates declare
and touches no other file. Paths printed and matched below are derived from this
script's own directory, so renaming that directory needs no edit here.

One template may render SEVERAL definitions. A template that opens with a fenced
TOML block declares them, along with the parameters that differ:

    +++
    [outputs.mad-participant-opus]
    model = "opus"
    color = "#6D28D9"

    [outputs.mad-participant-haiku]
    model = "haiku"
    color = "#5B21B6"
    +++
    ---
    name: @@name@@
    model: @@model@@
    ---
    <one body, rendered identically into every output>

Each declared key becomes an @@placeholder@@ in the body, plus @@name@@ bound to
the output's own name. Bodies are therefore identical by construction rather
than by maintenance discipline. A template with no such block renders a single
definition named after the template, exactly as before.

Usage
    python3 templates/gen-agents.py              # check (default)
    python3 templates/gen-agents.py --generate   # render templates to ../
    python3 templates/gen-agents.py --verbose    # list passing files too
    python3 templates/gen-agents.py --no-diff    # report drift without the diff

Per-target safety — a target is only ever written when it is provably ours,
and an overwrite is never destructive:

    target missing                  -> created
    banner, render identical        -> no write at all (counted as unchanged)
    banner, render differs          -> extant file copied to
                                       <name>.md.<NN>.bak, then written in place
    target exists without a banner  -> REFUSED, never written, reported, nonzero

A backup sits beside the file it backs up — ../go-coder.md.00.bak next to
../go-coder.md — and the repo's root *.bak gitignore rule keeps it out of git.
The backup is a copy (mode and timestamps preserved) and the definition is
truncated in place, so the live file keeps its inode, owner and mode however it
is regenerated and by whom. Serials are per-target, zero-padded from 00,
allocated as the highest existing serial plus one, and never reused.

The banner is a three-line YAML-comment block at the top of the frontmatter. A
definition without it is presumed hand-maintained and outside this tool's remit;
if it should be generated, delete it and re-run.

Check mode runs three checks, each failing nonzero:

  1. Every target is byte-identical to its rendered template (REFUSED targets
     are reported as such instead — they are not compared, and never written).
     This subsumes the old canonical-substring drift detection — canonical text
     now has exactly one source — and catches hand-edits to generated output.
  2. Every ../*.md carrying a banner has the template that banner names
     (ORPHAN / MISLABELED otherwise). Check 1 walks templates and so is blind
     to a definition claiming generation with no template behind it; this walks
     the claims back the other way.
  3. The non-coder coordinator specialist list.

Generation is deterministic and idempotent: output depends only on the
template, the shared sections, and the template's filename.

Marker syntax — usable in templates and inside chunk bodies:

    @@name@@                     expand chunk "name"
    @@name variant="platform"@@  expand that variant of a multi-variant chunk
    @@name key="value"@@         bind @@key@@ inside the chunk body
    @@name wrap="70"@@           greedy-wrap the expansion to 70 columns

"variant" and "wrap" are reserved; any other key binds a placeholder in the
chunk body, defaulting to [chunks.<name>.defaults] when the marker omits it.
Argument values cannot contain a double quote. An unknown marker name is an
error, so a typo fails loudly rather than shipping into a system prompt.
"""

import argparse
import difflib
import re
import shutil
import sys
import tomllib
from pathlib import Path

SCRIPT_DIR = Path(__file__).parent
AGENTS_DIR = SCRIPT_DIR.parent
SHARED_SECTIONS = SCRIPT_DIR / "shared-sections.toml"
TEMPLATE_SUFFIX = ".md.tmpl"
MAX_EXPANSION_DEPTH = 10

# Every path this tool prints or matches is derived from where the script lives,
# so renaming the template directory needs no edit here.
HOME = SCRIPT_DIR.name
OUTPUTS_FENCE = "+++"

MARKER = re.compile(r'@@([a-z0-9-]+)((?:\s+[a-z_]+="[^"]*")*)\s*@@')
ARG = re.compile(r'([a-z_]+)="([^"]*)"')
# The banner, read back out of a definition as its claim to being generated.
BANNER_CLAIM = re.compile(
    rf"^# !GENERATED! from {re.escape(HOME)}/(\S+\.md\.tmpl)\b", re.MULTILINE
)


class TemplateError(Exception):
    """A template or shared-section definition is malformed."""


# ─── Rendering ───────────────────────────────────────────────────────────────


def load_chunks() -> dict[str, dict]:
    """Load shared-sections.toml. Chunk bodies are stripped of edge newlines."""
    data = tomllib.loads(SHARED_SECTIONS.read_text(encoding="utf-8"))
    chunks = data.get("chunks", {})
    if not chunks:
        raise TemplateError(f"no [chunks.*] tables in {SHARED_SECTIONS.name}")
    for name, chunk in chunks.items():
        if "text" in chunk:
            chunk["text"] = chunk["text"].strip("\n")
        for variant, text in chunk.get("variants", {}).items():
            chunk["variants"][variant] = text.strip("\n")
        if "text" not in chunk and "variants" not in chunk:
            raise TemplateError(f"chunk '{name}' has neither text nor variants")
    return chunks


def wrap(text: str, width: int) -> str:
    """Greedy-wrap each paragraph of `text` to `width` columns on whitespace."""
    wrapped = []
    for paragraph in text.split("\n\n"):
        lines: list[str] = []
        current = ""
        for word in paragraph.split():
            if not current:
                current = word
            elif len(current) + 1 + len(word) <= width:
                current = f"{current} {word}"
            else:
                lines.append(current)
                current = word
        if current:
            lines.append(current)
        wrapped.append("\n".join(lines))
    return "\n\n".join(wrapped)


def chunk_body(name: str, chunk: dict, args: dict[str, str]) -> str:
    variants = chunk.get("variants")
    if variants is None:
        if "variant" in args:
            raise TemplateError(f"chunk '{name}' takes no variant")
        return chunk["text"]
    variant = args.get("variant")
    if variant is None:
        raise TemplateError(
            f"chunk '{name}' requires variant= (one of {sorted(variants)})"
        )
    if variant not in variants:
        raise TemplateError(
            f"chunk '{name}' has no variant '{variant}' (one of {sorted(variants)})"
        )
    return variants[variant]


def render(
    text: str, chunks: dict[str, dict], scope: dict[str, str], depth: int = 0
) -> str:
    """Expand every marker in `text`. `scope` binds placeholder arguments."""
    if depth > MAX_EXPANSION_DEPTH:
        raise TemplateError("marker expansion exceeded maximum depth (cycle?)")

    def expand(match: re.Match[str]) -> str:
        name = match.group(1)
        args = dict(ARG.findall(match.group(2)))
        if name in scope:
            if args:
                raise TemplateError(f"placeholder '{name}' takes no arguments")
            return render(scope[name], chunks, scope, depth + 1)
        chunk = chunks.get(name)
        if chunk is None:
            raise TemplateError(f"unknown chunk or placeholder '{name}'")
        width = args.pop("wrap", None)
        body = chunk_body(name, chunk, args)
        inner = {**chunk.get("defaults", {}), **args}
        inner.pop("variant", None)
        expanded = render(body, chunks, inner, depth + 1)
        return wrap(expanded, int(width)) if width else expanded

    return MARKER.sub(expand, text)


# ─── Banner ──────────────────────────────────────────────────────────────────


def banner(template_name: str) -> str:
    """The three-line YAML-comment block stamped at the top of frontmatter."""
    return (
        "#\n"
        f"# !GENERATED! from {HOME}/{template_name} and {HOME}/shared-sections.toml"
        " — edit those. DO NOT HAND EDIT THIS FILE.\n"
        "#"
    )


def frontmatter_of(text: str) -> str | None:
    """Return the YAML frontmatter block, or None if the file has none."""
    lines = text.split("\n")
    if not lines or lines[0].strip() != "---":
        return None
    for i, line in enumerate(lines[1:], start=1):
        if line.strip() == "---":
            return "\n".join(lines[1:i])
    return None


def banner_claim(text: str) -> str | None:
    """Return the template name a definition's banner claims, if it has one."""
    front = frontmatter_of(text)
    if front is None:
        return None
    match = BANNER_CLAIM.search(front)
    return match.group(1) if match else None


def split_outputs(path: Path) -> tuple[dict[str, dict[str, str]], str]:
    """Split a template into its output declarations and its body.

    A template may open with a fenced TOML block declaring the definitions it
    renders and the parameters that differ between them:

        +++
        [outputs.mad-participant-opus]
        model = "opus"
        +++
        ---
        name: @@name@@
        model: @@model@@
        ...

    Each key becomes an @@placeholder@@ in the body, plus @@name@@ bound to the
    output's own name. A template with no block renders one definition named
    after the template.
    """
    text = path.read_text(encoding="utf-8")
    stem = path.name[: -len(TEMPLATE_SUFFIX)]
    if not text.startswith(OUTPUTS_FENCE + "\n"):
        return {stem: {"name": stem}}, text

    closing = text.find(f"\n{OUTPUTS_FENCE}\n", len(OUTPUTS_FENCE))
    if closing == -1:
        raise TemplateError(f"{path.name}: unterminated {OUTPUTS_FENCE} outputs block")
    declared = tomllib.loads(text[len(OUTPUTS_FENCE) + 1 : closing]).get("outputs", {})
    if not declared:
        raise TemplateError(f"{path.name}: outputs block declares no [outputs.*]")

    outputs = {name: {"name": name, **params} for name, params in declared.items()}
    body = text[closing + len(OUTPUTS_FENCE) + 2 :]
    return outputs, body


def render_template(path: Path, chunks: dict[str, dict]) -> list[tuple[Path, str]]:
    """Render every definition a template declares, banner stamped into each."""
    outputs, body = split_outputs(path)
    rendered = []
    for name, params in sorted(outputs.items()):
        text = render(body, chunks, params)
        if not text.startswith("---\n"):
            raise TemplateError(
                f"{path.name}: template must open with YAML frontmatter"
            )
        # YAML comments: valid frontmatter, dropped by every parser, and outside
        # the body that becomes the agent's system prompt.
        stamped = "---\n" + banner(path.name) + "\n" + text[4:]
        rendered.append((AGENTS_DIR / f"{name}.md", stamped))
    return rendered


def templates() -> list[Path]:
    return sorted(SCRIPT_DIR.glob(f"*{TEMPLATE_SUFFIX}"))


def declared_outputs() -> dict[str, set[str]]:
    """Map template filename -> the definition filenames it declares."""
    return {
        t.name: {f"{name}.md" for name in split_outputs(t)[0]} for t in templates()
    }


def check_banner_claims() -> list[str]:
    """Verify every definition carrying a banner has the template it names.

    Enrollment runs template -> definition, so iterating templates cannot see a
    definition whose banner points at a template that no longer exists — a
    stale output left by a deleted or renamed template, outside every other
    check and drifting silently. This walks the claims the other way.
    """
    errors = []
    outputs = declared_outputs()
    for path in sorted(AGENTS_DIR.glob("*.md")):
        claimed = banner_claim(path.read_text(encoding="utf-8"))
        if claimed is None:
            continue
        if claimed not in outputs:
            errors.append(
                f"ORPHAN      {path.name} is banner-marked as generated from "
                f"{HOME}/{claimed}, but that template does not exist"
            )
        elif path.name not in outputs[claimed]:
            # A multi-render template names several definitions, so the claim is
            # checked against what the template declares, not against its stem.
            owner = next(
                (t for t, outs in outputs.items() if path.name in outs), None
            )
            errors.append(
                f"MISLABELED  {path.name} is banner-marked as generated from "
                f"{HOME}/{claimed}, which does not declare it"
                + (f" — its template is {HOME}/{owner}" if owner else "")
            )
    return errors


# ─── Non-coder checks ────────────────────────────────────────────────────────

# Not an enrollment or exclusion list for generation — this belongs to the
# coordinator specialist check below, and names the two files in agents/ that
# are not dispatchable specialists (the coordinator itself, and the README).
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
        errors.append(f"listed in coordinator.md but no agent file exists: {extra}")
    return errors


# ─── Modes ───────────────────────────────────────────────────────────────────

REFUSAL = (
    "exists without the generated banner — refusing to overwrite. If it should "
    "be generated, delete it and re-run; otherwise remove its template."
)

# A backup sits beside the file it backs up. The repo's root *.bak gitignore
# rule keeps them out of git. Serials are per-target and never reused.
SERIAL = re.compile(r"\.(\d+)\.bak$")


def next_backup(target: Path) -> Path:
    """Allocate <target>.<NN>.bak beside the target, NN = highest existing + 1."""
    used = []
    for path in target.parent.glob(f"{target.name}.*.bak"):
        match = SERIAL.search(path.name)
        if match:
            used.append(int(match.group(1)))
    return target.with_name(f"{target.name}.{max(used) + 1 if used else 0:02d}.bak")


def back_up(target: Path) -> Path:
    """Copy the extant definition aside before it is overwritten in place.

    Copy, not rename: the subsequent write truncates the original inode, so the
    live definition keeps its identity, owner and mode no matter who runs the
    tool. Renaming would hand the original inode to the disposable backup and
    leave the tracked file owned by whoever regenerated it.
    """
    backup = next_backup(target)
    shutil.copy2(target, backup)
    return backup


def all_renders(chunks: dict[str, dict]) -> list[tuple[Path, str]]:
    """Every (target, rendered text) pair across every template, sorted."""
    pairs = []
    for template in templates():
        pairs.extend(render_template(template, chunks))
    return sorted(pairs, key=lambda pair: pair[0].name)


def generate(chunks: dict[str, dict], *, verbose: bool) -> bool:
    clean = True
    unchanged = 0
    print("Generating agent definitions")
    print("=" * 60)
    found = templates()
    if not found:
        print(f"  ERROR      no templates found in {SCRIPT_DIR}")
        return False

    pairs = all_renders(chunks)
    for target, rendered in pairs:
        if not target.exists():
            target.write_text(rendered, encoding="utf-8")
            print(f"  {'created':<10} {target.name}")
            continue

        # Compare before writing: an identical render is not a write at all, so
        # an unchanged definition never accumulates a backup.
        actual = target.read_text(encoding="utf-8")
        if banner_claim(actual) is None:
            print(f"  {'REFUSED':<10} {target.name} {REFUSAL}")
            clean = False
            continue

        if actual == rendered:
            unchanged += 1
            if verbose:
                print(f"  {'unchanged':<10} {target.name}")
            continue

        backup = back_up(target)
        target.write_text(rendered, encoding="utf-8")
        print(f"  {'updated':<10} {target.name} (backed up beside it as {backup.name})")

    print()
    print(f"  {'unchanged':<10} {unchanged} definition(s)")
    print(f"{len(pairs)} definition(s) from {len(found)} template(s).")
    return clean


def check(chunks: dict[str, dict], *, verbose: bool, show_diff: bool) -> bool:
    clean = True
    print()
    print("Generated definitions vs templates")
    print("=" * 60)

    found = templates()
    if not found:
        print(f"  ERROR    no templates found in {SCRIPT_DIR}")
        clean = False

    for target, rendered in all_renders(chunks):
        if not target.exists():
            print(f"  {'MISSING':<8} {target.name} — run --generate")
            clean = False
            continue

        actual = target.read_text(encoding="utf-8")
        if banner_claim(actual) is None:
            print(f"  {'REFUSED':<8} {target.name} {REFUSAL}")
            clean = False
            continue

        if actual == rendered:
            if verbose:
                print(f"  {'OK':<8} {target.name}")
            continue

        clean = False
        print(f"  {'DRIFT':<8} {target.name} — differs from rendered template")
        if show_diff:
            diff = difflib.unified_diff(
                rendered.splitlines(keepends=True),
                actual.splitlines(keepends=True),
                fromfile=f"rendered/{target.name}",
                tofile=f"agents/{target.name}",
            )
            for line in diff:
                print(f"      {line.rstrip()}")

    print()
    print("Banner claims vs templates")
    print("=" * 60)
    claim_errors = check_banner_claims()
    if claim_errors:
        clean = False
        for err in claim_errors:
            print(f"  {err}")
    elif verbose:
        print("  OK")

    print()
    print("Coordinator specialist list check")
    print("=" * 60)
    coord_errors = check_coordinator_specialists()
    if coord_errors:
        clean = False
        for err in coord_errors:
            print(f"  DRIFT  {err}")
    elif verbose:
        print("  OK")

    print()
    if clean:
        print("All generated definitions match their templates. No drift detected.")
    else:
        print(
            f"Drift detected. Edit {HOME}/<name>.md.tmpl or "
            f"{HOME}/shared-sections.toml,\n"
            f"then run: python3 {HOME}/gen-agents.py --generate"
        )
        if claim_errors:
            print(
                "ORPHAN/MISLABELED is not fixed by --generate: restore the named "
                "template, or delete the stale definition."
            )
    return clean


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Generate and check the coder agent definitions."
    )
    parser.add_argument(
        "--generate",
        action="store_true",
        help="render templates over the definitions in the repository root",
    )
    parser.add_argument("--verbose", "-v", action="store_true", help="list every file")
    parser.add_argument(
        "--no-diff",
        action="store_true",
        help="in check mode, report drift without printing the diff",
    )
    args = parser.parse_args()

    try:
        chunks = load_chunks()
        if args.generate:
            ok = generate(chunks, verbose=args.verbose)
        else:
            ok = check(chunks, verbose=args.verbose, show_diff=not args.no_diff)
    except TemplateError as exc:
        print(f"error: {exc}", file=sys.stderr)
        sys.exit(2)

    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
