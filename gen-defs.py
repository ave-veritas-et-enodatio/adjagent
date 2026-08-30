#!/usr/bin/env python3
"""
Generator and consistency checker for the generated definitions in this
repository — agent definitions and slash-command definitions alike.

Templates live in two sibling directories under templates/, and a template's
parent directory routes its output into the matching deployed surface:

    templates/agents/<name>.md.tmpl    + templates/shared-sections.toml -> agents/<name>.md
    templates/commands/<name>.md.tmpl  + templates/shared-sections.toml -> commands/<name>.md

templates/shared-sections.toml is the single chunk source for both template
types. A template holds everything unique to its definition; every span of
text shared with other definitions is a marker referencing a chunk, so shared
text lives in exactly one place. templates/commands/ may be absent or empty
(git does not track an empty directory); that is not an error so long as at
least one template exists somewhere.

Enrollment is template existence, and nothing else. There is no enrollment
list and no exclusion list: the tool generates every definition its templates
declare and touches no other file. Every path printed and matched below is
derived from this script's own location at the repository root.

One template may render SEVERAL definitions. A template that opens with a
fenced TOML block declares them, along with the parameters that differ:

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

Each declared key becomes an @@placeholder@@ in the body, plus @@name@@ bound
to the output's own name. Bodies are therefore identical by construction
rather than by maintenance discipline. A template with no such block renders a
single definition named after the template, exactly as before. Chunks,
variants, markers, wrapping, multi-output declarations, write safety, backups,
and the render-identity check apply identically to both template types.

Usage — `just generate` and `just check` are the sole sanctioned entry points
(see ARCHITECTURE.md); they wrap:

    python3 gen-defs.py              # check (default)
    python3 gen-defs.py --generate   # render templates into agents/ and commands/
    python3 gen-defs.py --verbose    # list passing files too
    python3 gen-defs.py --no-diff    # report drift without the diff

Per-target safety — a target is only ever written when it is provably ours,
and an overwrite is never destructive:

    target missing                  -> created
    banner, render identical        -> no write at all (counted as unchanged)
    banner, render differs          -> extant file copied to
                                       <name>.md.<NN>.bak, then written in place
    target exists without a banner  -> REFUSED, never written, reported, nonzero

A backup sits beside the file it backs up — agents/go-coder.md.00.bak next to
agents/go-coder.md — and the repo's root *.bak gitignore rule keeps it out of
git. The backup is a copy (mode and timestamps preserved) and the definition
is truncated in place, so the live file keeps its inode, owner and mode
however it is regenerated and by whom. Serials are per-target, zero-padded
from 00, allocated as the highest existing serial plus one, and never reused.

As a guard against a wrong output-directory constant — which would otherwise
exit 0 while writing to the wrong place — generation first asserts that every
resolved output directory is an existing directory inside this repository, and
the generation report states where the files landed.

The banner is a three-line YAML-comment block naming the source template
(`# !GENERATED! from templates/agents/<name>.md.tmpl ...`). It always lives
inside YAML frontmatter, never in the body that becomes a prompt:

  - An agent template must open with a frontmatter block; the banner is
    injected at its top.
  - A command template whose body opens with a frontmatter block gets the
    banner injected the same way; a command template with no frontmatter gets
    a minimal frontmatter block emitted above its body, containing only the
    banner comment lines, so the banner never lands in the literal prompt
    text.

A definition without a banner is presumed hand-maintained and outside this
tool's remit; if it should be generated, delete it and re-run.

Check mode runs two checks, each failing nonzero:

  1. Render-identity: every target is byte-identical to its rendered template
     (REFUSED targets are reported as such instead — they are not compared,
     and never written). This catches hand-edits to generated output and
     definitions left stale by a chunk or template change.
  2. Banner-claims-vs-templates: every agents/*.md and commands/*.md carrying
     a banner has the template that banner names, and that template declares
     it (ORPHAN / MISLABELED otherwise). Check 1 walks templates and so is
     blind to a definition claiming generation with no template behind it;
     this walks the claims back the other way.

Generation is deterministic and idempotent: output depends only on the
template, the shared sections, and the template's path.

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

# Bumped per semver on mechanism changes (1.0.0 marks the two-surface
# machinery stabilization). Deliberately NOT embedded in rendered banners —
# that would churn every generated file on every bump.
__version__ = "1.0.0"

REPO_ROOT = Path(__file__).parent
TEMPLATES_DIR = REPO_ROOT / "templates"
SHARED_SECTIONS = TEMPLATES_DIR / "shared-sections.toml"
TEMPLATE_SUFFIX = ".md.tmpl"
MAX_EXPANSION_DEPTH = 10

# Surface name -> (template source dir, output dir). A template's parent
# directory routes its output; a surface's template dir may be absent or
# empty. Every path this tool prints or matches is derived from the script's
# own location, so renaming a directory needs no edit beyond this table.
SURFACES = {
    name: (TEMPLATES_DIR / name, REPO_ROOT / name) for name in ("agents", "commands")
}
COMMAND_SURFACE = "commands"
OUTPUTS_FENCE = "+++"

MARKER = re.compile(r'@@([a-z0-9-]+)((?:\s+[a-z_]+="[^"]*")*)\s*@@')
ARG = re.compile(r'([a-z_]+)="([^"]*)"')
# The banner, read back out of a definition as its claim to being generated.
# Deliberately path-agnostic: any *.md.tmpl claim marks the file as generated
# (gating write safety); whether the claimed template exists and declares the
# file is check 2's job, so a stale claim is ORPHAN/MISLABELED, not REFUSED.
BANNER_CLAIM = re.compile(r"^# !GENERATED! from (\S+\.md\.tmpl)\b", re.MULTILINE)


class TemplateError(Exception):
    """A template or shared-section definition is malformed."""


def rel(path: Path) -> str:
    """A path as printed and matched everywhere: relative to the repo root."""
    return path.relative_to(REPO_ROOT).as_posix()


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


def banner(template: Path) -> str:
    """The three-line YAML-comment block stamped into frontmatter."""
    return (
        "#\n"
        f"# !GENERATED! from {rel(template)} and {rel(SHARED_SECTIONS)}"
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
    """Return the template path a definition's banner claims, if it has one."""
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
        raise TemplateError(f"{rel(path)}: unterminated {OUTPUTS_FENCE} outputs block")
    declared = tomllib.loads(text[len(OUTPUTS_FENCE) + 1 : closing]).get("outputs", {})
    if not declared:
        raise TemplateError(f"{rel(path)}: outputs block declares no [outputs.*]")

    outputs = {name: {"name": name, **params} for name, params in declared.items()}
    body = text[closing + len(OUTPUTS_FENCE) + 2 :]
    return outputs, body


def render_template(path: Path, chunks: dict[str, dict]) -> list[tuple[Path, str]]:
    """Render every definition a template declares, banner stamped into each.

    The template's parent directory routes its outputs (templates/agents/ ->
    agents/, templates/commands/ -> commands/). The banner always lands in
    frontmatter: agent templates must open with a frontmatter block; command
    templates may open with one (banner injected identically) or with bare
    prompt text (a minimal banner-only frontmatter block is emitted above it).
    """
    surface = path.parent.name
    out_dir = SURFACES[surface][1]
    outputs, body = split_outputs(path)
    stamp = banner(path)
    rendered = []
    for name, params in sorted(outputs.items()):
        text = render(body, chunks, params)
        if text.startswith("---\n"):
            # YAML comments: valid frontmatter, dropped by every parser, and
            # outside the body that becomes the prompt.
            stamped = "---\n" + stamp + "\n" + text[4:]
        elif surface == COMMAND_SURFACE:
            stamped = "---\n" + stamp + "\n---\n" + text
        else:
            raise TemplateError(
                f"{rel(path)}: agent template must open with YAML frontmatter"
            )
        rendered.append((out_dir / f"{name}.md", stamped))
    return rendered


def templates() -> list[Path]:
    """Every template in every surface's template dir (absent dirs tolerated)."""
    found: list[Path] = []
    for template_dir, _ in SURFACES.values():
        if template_dir.is_dir():
            found.extend(template_dir.glob(f"*{TEMPLATE_SUFFIX}"))
    return sorted(found)


def declared_outputs() -> dict[str, set[str]]:
    """Map template path (repo-relative) -> the output paths it declares."""
    outputs = {}
    for template in templates():
        out_dir = SURFACES[template.parent.name][1]
        outputs[rel(template)] = {
            rel(out_dir / f"{name}.md") for name in split_outputs(template)[0]
        }
    return outputs


def check_banner_claims() -> list[str]:
    """Verify every definition carrying a banner has the template it names.

    Enrollment runs template -> definition, so iterating templates cannot see a
    definition whose banner points at a template that no longer exists — a
    stale output left by a deleted or renamed template, outside every other
    check and drifting silently. This walks the claims the other way, across
    both deployed surfaces (agents/ and commands/).
    """
    errors = []
    outputs = declared_outputs()
    for _, out_dir in SURFACES.values():
        for path in sorted(out_dir.glob("*.md")):
            claimed = banner_claim(path.read_text(encoding="utf-8"))
            if claimed is None:
                continue
            if claimed not in outputs:
                errors.append(
                    f"ORPHAN      {rel(path)} is banner-marked as generated from "
                    f"{claimed}, but that template does not exist"
                )
            elif rel(path) not in outputs[claimed]:
                # A multi-render template names several definitions, so the
                # claim is checked against what the template declares, not
                # against its stem.
                owner = next(
                    (t for t, outs in outputs.items() if rel(path) in outs), None
                )
                errors.append(
                    f"MISLABELED  {rel(path)} is banner-marked as generated from "
                    f"{claimed}, which does not declare it"
                    + (f" — its template is {owner}" if owner else "")
                )
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
    return sorted(pairs, key=lambda pair: rel(pair[0]))


def assert_output_dirs(out_dirs: set[Path]) -> None:
    """Guard against a wrong output-dir constant, which would otherwise exit 0
    while writing to the wrong place: every resolved output directory must be
    an existing directory strictly inside this repository."""
    root = REPO_ROOT.resolve()
    for out_dir in sorted(out_dirs):
        resolved = out_dir.resolve()
        if not (
            resolved.is_dir() and resolved != root and resolved.is_relative_to(root)
        ):
            raise TemplateError(
                f"output directory '{out_dir}' is not an existing directory "
                f"inside the repository — refusing to write"
            )


def generate(chunks: dict[str, dict], *, verbose: bool) -> bool:
    clean = True
    unchanged = 0
    print("Generating definitions")
    print("=" * 60)
    found = templates()
    if not found:
        print(f"  ERROR      no templates found under {rel(TEMPLATES_DIR)}/")
        return False

    pairs = all_renders(chunks)
    assert_output_dirs({target.parent for target, _ in pairs})
    landed: dict[str, int] = {}
    for target, rendered in pairs:
        landed[rel(target.parent)] = landed.get(rel(target.parent), 0) + 1
        if not target.exists():
            target.write_text(rendered, encoding="utf-8")
            print(f"  {'created':<10} {rel(target)}")
            continue

        # Compare before writing: an identical render is not a write at all, so
        # an unchanged definition never accumulates a backup.
        actual = target.read_text(encoding="utf-8")
        if banner_claim(actual) is None:
            print(f"  {'REFUSED':<10} {rel(target)} {REFUSAL}")
            clean = False
            continue

        if actual == rendered:
            unchanged += 1
            if verbose:
                print(f"  {'unchanged':<10} {rel(target)}")
            continue

        backup = back_up(target)
        target.write_text(rendered, encoding="utf-8")
        print(
            f"  {'updated':<10} {rel(target)} (backed up beside it as {backup.name})"
        )

    print()
    print(f"  {'unchanged':<10} {unchanged} definition(s)")
    where = ", ".join(f"{d}/ ({n})" for d, n in sorted(landed.items()))
    print(f"{len(pairs)} definition(s) from {len(found)} template(s), in: {where}")
    return clean


def check(chunks: dict[str, dict], *, verbose: bool, show_diff: bool) -> bool:
    clean = True
    print()
    print("Generated definitions vs templates")
    print("=" * 60)

    found = templates()
    if not found:
        print(f"  ERROR    no templates found under {rel(TEMPLATES_DIR)}/")
        clean = False

    for target, rendered in all_renders(chunks):
        if not target.exists():
            print(f"  {'MISSING':<8} {rel(target)} — run --generate")
            clean = False
            continue

        actual = target.read_text(encoding="utf-8")
        if banner_claim(actual) is None:
            print(f"  {'REFUSED':<8} {rel(target)} {REFUSAL}")
            clean = False
            continue

        if actual == rendered:
            if verbose:
                print(f"  {'OK':<8} {rel(target)}")
            continue

        clean = False
        print(f"  {'DRIFT':<8} {rel(target)} — differs from rendered template")
        if show_diff:
            diff = difflib.unified_diff(
                rendered.splitlines(keepends=True),
                actual.splitlines(keepends=True),
                fromfile=f"rendered/{rel(target)}",
                tofile=rel(target),
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
    if clean:
        print("All generated definitions match their templates. No drift detected.")
    else:
        print(
            f"Drift detected. Edit the definition's template under "
            f"{rel(TEMPLATES_DIR)}/ or {rel(SHARED_SECTIONS)},\n"
            f"then run: just generate"
        )
        if claim_errors:
            print(
                "ORPHAN/MISLABELED is not fixed by --generate: restore the named "
                "template, or delete the stale definition."
            )
    return clean


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Generate and check the generated agent/command definitions."
    )
    parser.add_argument(
        "--generate",
        action="store_true",
        help="render templates into the deployed surfaces (agents/, commands/)",
    )
    parser.add_argument(
        "--version", action="version", version=f"%(prog)s {__version__}"
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
