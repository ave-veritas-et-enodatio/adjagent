#!/usr/bin/env python3
"""
Generator and consistency checker for the generated definitions in this
repository — agent definitions and slash-command definitions alike.

Templates live in two sibling trees under templates/, and a template's surface
tree routes its output into the matching deployed surface:

    templates/agents/<name>.md.tmpl    + templates/shared-sections.toml -> agents/<name>.md
    templates/commands/<name>.md.tmpl  + templates/shared-sections.toml -> commands/<name>.md

Discovery is RECURSIVE within each surface tree, and a template's path is
MIRRORED into its surface — the relative subpath of the template is the
relative subpath of its output. The filesystem is the whole declaration;
there is no metadata key for placement:

    templates/agents/mad/participant-contract.md.tmpl -> agents/mad/participant-contract.md

Every other mechanism — chunks, variants, markers, NB anchors, multi-output
fences, banners, write safety, numbered backups, and both checks — applies
unchanged at a nested path. Check 2 walks *.md RECURSIVELY under each checked
surface, so a banner stranded at a nested path is caught exactly as a
top-level one is. Mirrored subdirectories beneath a surface are created as
needed (see the output-directory guard below).

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
single definition named after the template, at the template's mirrored path.
Chunks,
variants, markers, wrapping, multi-output declarations, write safety, backups,
and the render-identity check apply identically to both template types.

Usage — `just generate` and `just check` are the sole sanctioned entry points
(see ARCHITECTURE.md); they wrap:

    python3 gen-defs.py              # check (default)
    python3 gen-defs.py --generate   # render templates into agents/ and commands/
    python3 gen-defs.py --install R  # full-product install into R (see below)
    python3 gen-defs.py --verbose    # list passing files too
    python3 gen-defs.py --no-diff    # report drift without the diff

Render-to-order flags, accepted by both modes:

    --output-dir ROOT       render/check agents/ and commands/ subtrees under
                            ROOT instead of the repo's deployed surfaces. ROOT
                            must already exist (error otherwise); the surface
                            subdirectories are created under it as needed.
    --surfaces WHICH        agents | commands | both (default both): which
                            surface to render or check.
    --model-family SPEC     load one model-family NB file (see below). SPEC is
                            either a path to the file (anything containing a
                            path separator or ending in .toml), or a bare
                            family name resolved against templates/models/ —
                            matching <name>-addenda.toml or <name>.toml. Both
                            matching is an ambiguity error naming the
                            candidates; neither matching is an error listing
                            the available family files.
    --model NAME            activate NAME's per-model overrides within the
                            loaded family file. Without --model-family, the
                            family is implied: every templates/models/*.toml
                            is scanned for [nb.*.models.NAME] tables
                            mentioning the model. Exactly one family
                            mentioning it is used (and reported); several
                            demand an explicit --model-family; none is an
                            error listing each family's known models — a
                            model mentioned only in comments is NOT known;
                            name the family explicitly to use one.

The strictly-inside-this-repository output guard applies only to the implicit
default output location; an explicit --output-dir is exempt from the
inside-repo constraint but is still asserted to be an existing directory. The
full per-target safety table below (banner detection, numbered backups,
refusal of bannerless files) applies identically to out-of-repo targets.

Model tuning — NB anchors and family files:

    @@nb name="<anchor-name>"@@

is a marker usable in template bodies and chunk bodies. With no family file
loaded, or a loaded family file with no entry for that anchor, it expands to
nothing at all — a base render is byte-identical whether or not the anchor
exists. A family file (templates/models/<family>.toml) fills anchors:

    [nb.<anchor-name>]
    text = "..."                          # fills the anchor family-wide

    [nb.<anchor-name>.models.<model-name>]
    text = "..."                          # overrides the family text for
                                          # that model (with --model NAME)

Resolution, not accumulation: AT MOST ONE NB renders per anchor — the model
scope wins over the family scope when --model names a model the entry covers.
The rendered form is `**NB**: <text>`; neither the family nor the model name
appears in rendered output — the family file is the provenance record. The
filled text is itself marker-expanded, so a typo'd chunk reference inside it
fails loudly. A family-file entry naming an anchor that exists in no template
or chunk is a hard error, and generation reports which anchors were filled and
from which scope (family vs model). A family file with zero [nb.<anchor>]
tables (comments only) loads cleanly and fills nothing — a family name may be
reserved before any observed failure motivates an entry. `nb` is a reserved
marker name: no chunk or placeholder may claim it.

Structural invariant: a family file can never replace, suppress, or modify
BASE text — it can only fill anchors that base templates and chunks
deliberately expose. Anchors are authored on demand, when an observed failure
motivates one; they are never pre-sprinkled speculatively. See
templates/models/README.md and README.md ("Variants and platform
compatibility") for the provenance discipline.

Per-target safety — a target is only ever written when it is provably ours,
and an overwrite never destroys content this tool did not itself write:

    target missing                  -> created
    banner, render identical        -> no write at all (counted as unchanged)
    banner, body hash matches the
      banner's claim, render differs-> overwritten in place, NO backup: the
                                       prior content is provably this tool's
                                       own output, reproducible from the
                                       template, so a copy of it is landfill
    banner, body hash absent or
      mismatched, render differs    -> hand-edited (or pre-hash) content:
                                       copied to <name>.md.<NN>.bak, then
                                       written in place
    target exists without a banner  -> REFUSED, never written, reported, nonzero

The hash-verified branch is what keeps routine regeneration — and the flavored
install's render pass over a freshly copied tree — from churning out backups
nobody reads. A backup sits beside the file it backs up —
agents/go-coder.md.00.bak next to agents/go-coder.md — and the repo's root
*.bak gitignore rule keeps it out of git. The backup is a copy (mode and
timestamps preserved) and the definition is truncated in place, so the live
file keeps its inode, owner and mode however it is regenerated and by whom.
Serials are per-target, zero-padded from 00, allocated as the highest existing
serial plus one, and never reused.

As a guard against a wrong output-directory constant — which would otherwise
exit 0 while writing to the wrong place — default-location generation first
asserts that every SURFACE directory receiving output is an existing directory
inside this repository, and the generation report states where the files
landed (--output-dir generation instead asserts ROOT exists, as described
above). Mirrored subdirectories beneath a surface are not part of that guard:
they come from the template tree rather than from a constant, and are created
as needed under either mode.

Full-product install — `--install ROOT`:

    python3 gen-defs.py --install <project>/.claude [--model-family F] [--model M]

An install delivers the whole deployed product, and it is a PLAIN RECURSIVE
COPY of both surfaces into ROOT — hand-maintained definitions, the checked-in
generated definitions, agents/mad/ topic sets, kb_tools/, liaison_tools/, the
guest commands: everything, minus one explicit exclusion list
(INSTALL_EXCLUDED_*) — test suites and their fixtures, python/pytest caches,
the generator's *.bak safety copies, .DS_Store. There is no inclusion list and
no complement computation: a new hand-maintained definition or tool file ships
with no enrollment step. `--install ROOT` implies `--output-dir ROOT` and
cannot be combined with it or with --generate; `--surfaces` still selects which
surface(s) to install.

A checked-in surface already holds each definition's BASE render, so a plain
install needs no render pass at all — the copy is the install. It is followed
by a check of the installed set, reported but not gating (the target may hold
material this tool did not put there). Only a FLAVORED install renders: after
the copy, the ordinary generation pass runs against ROOT under the requested
family/model, rewriting the definitions the flavor changes. Freshly copied
definitions are provably untouched tool output (see the banner's body hash
below), so that pass leaves no numbered backups behind.

The installed tree is an ARTIFACT, not a working copy: this repository is the
source of truth and a re-install overwrites unconditionally. Local edits to
installed files are not protected — edit the templates or the definitions here
and re-install. The manifest written into ROOT (agents-install-manifest.json)
is therefore a provenance stamp, not a ledger: gen-defs version, source repo
commit (`git rev-parse HEAD`, suffixed -dirty for a modified work tree), the
flavor, and a timestamp. Nothing reads it back; it answers "what is this tree,
and where did it come from".

The banner is a four-line YAML-comment block naming the source template
(`# !GENERATED! from templates/agents/<name>.md.tmpl ...`). When rendered with
a family file, the banner line additionally names the family file (and the
model when --model was given) — the banner is a tuned set's claim to its
tuning, just as it is a definition's claim to being generated. The banner
always lives inside YAML frontmatter, never in the body that becomes a prompt:

  - An agent template must open with a frontmatter block; the banner is
    injected at its top.
  - A command template whose body opens with a frontmatter block gets the
    banner injected the same way; a command template with no frontmatter gets
    a minimal frontmatter block emitted above its body, containing only the
    banner comment lines, so the banner never lands in the literal prompt
    text.

Its third line, `# !BODY-SHA256! <hex>`, is the sha256 of everything the file
holds AFTER the banner block, exactly as written. That single line lets any
later reader answer a question the banner alone cannot: is this file still the
bytes the tool wrote, or has someone edited it since? A definition whose body
hashes to its own claim is provably untouched tool output — reproducible at
will, and therefore safe to overwrite without a backup (see the safety table
above). A pre-1.5.0 banner carries no hash line and proves nothing, so it is
treated as possibly hand-edited.

A definition without a banner is presumed hand-maintained and outside this
tool's remit; if it should be generated, delete it and re-run.

Check mode runs two checks, each failing nonzero:

  1. Render-identity: every target is byte-identical to its rendered template
     (REFUSED targets are reported as such instead — they are not compared,
     and never written). This catches hand-edits to generated output and
     definitions left stale by a chunk or template change. The reported DIFF
     is taken over the post-banner bytes, so a body change reads as itself
     rather than as a body change plus a churned hash line.
  2. Banner-claims-vs-templates: every *.md anywhere under the checked output
     surfaces carrying a banner has the template that banner names, and that
     template declares it (ORPHAN / MISLABELED otherwise). Check 1 walks
     templates and so is blind to a definition claiming generation with no
     template behind it; this walks the claims back the other way.

Check accepts the same --output-dir / --surfaces / --model-family / --model
flags, so a tuned out-of-repo set gets the identical render-identity and
banner-claims validation. Tuning claims are part of both checks: check runs
under exactly one tuning configuration (the one it was invoked with), renders
with it, and requires every target's banner to claim exactly that
configuration. A banner naming a family file (or model) that check was not
invoked with — or omitting one it was — is reported MISTUNED, naming both
sides, instead of an opaque byte diff; a target whose tuning claim matches but
whose bytes differ is ordinary DRIFT. Validating a tuned set therefore means
invoking check with that set's --output-dir and its family/model flags;
checking the same directory under a different tuning is expected to fail —
that is the mismatch the banner exists to catch.

Generation is deterministic and idempotent: output depends only on the
template, the shared sections, and the template's path.

Marker syntax — usable in templates and inside chunk bodies:

    @@name@@                     expand chunk "name"
    @@name variant="platform"@@  expand that variant of a multi-variant chunk
    @@name key="value"@@         bind @@key@@ inside the chunk body
    @@name wrap="70"@@           greedy-wrap the expansion to 70 columns
    @@nb name="anchor"@@         model-tuning NB anchor — expands to nothing
                                 unless a loaded family file fills it

"variant" and "wrap" are reserved; any other key binds a placeholder in the
chunk body, defaulting to [chunks.<name>.defaults] when the marker omits it.
Argument values cannot contain a double quote. An unknown marker name is an
error, so a typo fails loudly rather than shipping into a system prompt.
"""

import argparse
import difflib
import hashlib
import json
import re
import shutil
import subprocess
import sys
import tomllib
from datetime import UTC, datetime
from pathlib import Path

# Bumped per semver on mechanism changes (1.0.0 marks the two-surface
# machinery stabilization; 1.1.0 adds render-to-order — --output-dir /
# --surfaces — and NB model-tuning anchors with family files; 1.2.0 accepts
# zero-entry family files, so a family name can be reserved comments-only;
# 1.3.0 adds flavor resolution — bare family names for --model-family, and
# model -> family implication for a bare --model; 1.4.0 makes template
# discovery recursive within each surface tree and mirrors a template's
# relative subpath into its surface, so placement is declared by the
# filesystem alone; 1.5.0 adds --install, the full-product install — a plain
# recursive copy of both surfaces plus a provenance stamp, with a render pass
# only when a flavor is requested — and stamps each banner with the sha256 of
# the body below it, which lets an overwrite of provably untouched output skip
# the numbered backup).
# Deliberately NOT embedded in rendered banners — that would churn every
# generated file on every bump.
__version__ = "1.5.0"

REPO_ROOT = Path(__file__).parent
TEMPLATES_DIR = REPO_ROOT / "templates"
SHARED_SECTIONS = TEMPLATES_DIR / "shared-sections.toml"
MODELS_DIR = TEMPLATES_DIR / "models"
TEMPLATE_SUFFIX = ".md.tmpl"
FAMILY_SUFFIX = ".toml"
MAX_EXPANSION_DEPTH = 10

SURFACE_NAMES = ("agents", "commands")
COMMAND_SURFACE = "commands"
OUTPUTS_FENCE = "+++"
# The NB model-tuning marker's reserved name; no chunk or placeholder may
# claim it. Anchor names share the marker-name charset.
NB_MARKER = "nb"
ANCHOR_NAME = re.compile(r"[a-z0-9-]+")

MARKER = re.compile(r'@@([a-z0-9-]+)((?:\s+[a-z_]+="[^"]*")*)\s*@@')
ARG = re.compile(r'([a-z_]+)="([^"]*)"')
# The banner, read back out of a definition as its claim to being generated.
# Deliberately path-agnostic: any *.md.tmpl claim marks the file as generated
# (gating write safety); whether the claimed template exists and declares the
# file is check 2's job, so a stale claim is ORPHAN/MISLABELED, not REFUSED.
BANNER_CLAIM = re.compile(r"^# !GENERATED! from (\S+\.md\.tmpl)\b", re.MULTILINE)
# The banner's third line and the block's closing "#": the hash of everything
# after it, and the marker for where "everything after it" begins. Matching
# both together means one search locates the claim and the bytes it covers.
BODY_HASH_CLAIM = re.compile(r"^# !BODY-SHA256! ([0-9a-f]{64})\n#\n", re.MULTILINE)
# A tuned banner's second claim: which family file (and model) rendered it.
TUNING_CLAIM = re.compile(
    r"^# !GENERATED! from \S+\.md\.tmpl and \S+"
    r" with model family (\S+)(?:, model (\S+))? — edit",
    re.MULTILINE,
)


class TemplateError(Exception):
    """A template or shared-section definition is malformed."""


def rel(path: Path) -> str:
    """A path as printed and matched everywhere: relative to the repo root
    when it lies inside it, resolved-absolute otherwise (--output-dir and
    --model-family may point anywhere)."""
    try:
        return path.relative_to(REPO_ROOT).as_posix()
    except ValueError:
        try:
            return path.resolve().relative_to(REPO_ROOT.resolve()).as_posix()
        except ValueError:
            return path.resolve().as_posix()


def surface_map(
    templates_root: Path = TEMPLATES_DIR,
    output_root: Path | None = None,
    surfaces: str = "both",
) -> dict[str, tuple[Path, Path]]:
    """Surface name -> (template source dir, output dir).

    A template's parent directory routes its output; a surface's template dir
    may be absent or empty. With `output_root` None the deployed surfaces
    beside this script are the targets (implicit default, guarded by
    assert_output_dirs); an explicit root must already exist and its surface
    subdirectories are created at generation time as needed. `surfaces`
    filters to one surface, or "both".
    """
    if output_root is not None and not output_root.is_dir():
        raise TemplateError(
            f"output root '{output_root}' is not an existing directory "
            f"(--output-dir and --install both require one)"
        )
    root = REPO_ROOT if output_root is None else output_root
    names = SURFACE_NAMES if surfaces == "both" else (surfaces,)
    if not set(names) <= set(SURFACE_NAMES):
        raise TemplateError(f"unknown surface '{surfaces}'")
    return {name: (templates_root / name, root / name) for name in names}


# ─── Rendering ───────────────────────────────────────────────────────────────


def load_chunks() -> dict[str, dict]:
    """Load shared-sections.toml. Chunk bodies are stripped of edge newlines."""
    data = tomllib.loads(SHARED_SECTIONS.read_text(encoding="utf-8"))
    chunks = data.get("chunks", {})
    if not chunks:
        raise TemplateError(f"no [chunks.*] tables in {SHARED_SECTIONS.name}")
    for name, chunk in chunks.items():
        if name == NB_MARKER:
            raise TemplateError(f"'{NB_MARKER}' is a reserved marker name")
        if "text" in chunk:
            chunk["text"] = chunk["text"].strip("\n")
        for variant, text in chunk.get("variants", {}).items():
            chunk["variants"][variant] = text.strip("\n")
        if "text" not in chunk and "variants" not in chunk:
            raise TemplateError(f"chunk '{name}' has neither text nor variants")
    return chunks


# ─── NB anchors and family files ─────────────────────────────────────────────


def anchors_in(text: str) -> set[str]:
    """Every anchor name that @@nb name="..."@@ markers in `text` declare."""
    found = set()
    for match in MARKER.finditer(text):
        if match.group(1) == NB_MARKER:
            name = dict(ARG.findall(match.group(2))).get("name")
            if name:
                found.add(name)
    return found


def collect_anchors(
    chunks: dict[str, dict], template_paths: list[Path]
) -> set[str]:
    """Every NB anchor authored anywhere — template bodies and chunk bodies
    (text, variants, and defaults values) alike."""
    found: set[str] = set()
    for path in template_paths:
        found |= anchors_in(path.read_text(encoding="utf-8"))
    for chunk in chunks.values():
        for value in (
            chunk.get("text", ""),
            *chunk.get("variants", {}).values(),
            *chunk.get("defaults", {}).values(),
        ):
            found |= anchors_in(value)
    return found


def load_family(path: Path) -> dict[str, dict]:
    """Load and validate a model-family NB file.

    Schema: [nb.<anchor>] tables, each with a family-wide `text` and/or
    [nb.<anchor>.models.<model>] per-model override tables carrying `text`.
    A family file only fills anchors — it has no vocabulary for replacing,
    suppressing, or modifying base text.
    """
    if not path.is_file():
        raise TemplateError(f"model-family file '{path}' does not exist")
    try:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
    except tomllib.TOMLDecodeError as exc:
        raise TemplateError(f"{rel(path)}: invalid TOML — {exc}") from exc
    entries = data.get(NB_MARKER, {})
    stray = set(data) - {NB_MARKER}
    if stray:
        raise TemplateError(
            f"{rel(path)}: unknown top-level table(s) {sorted(stray)} — a "
            f"family file holds only [nb.<anchor>] tables"
        )
    # A family file with zero [nb.<anchor>] tables (comments only) loads
    # cleanly and fills nothing — a family name may be reserved before any
    # observed failure motivates an entry (see templates/models/README.md).
    for anchor, entry in entries.items():
        where = f"{rel(path)}: [nb.{anchor}]"
        if not ANCHOR_NAME.fullmatch(anchor):
            raise TemplateError(f"{where}: anchor names match [a-z0-9-]+")
        if not isinstance(entry, dict) or set(entry) - {"text", "models"}:
            raise TemplateError(f"{where}: only 'text' and 'models' allowed")
        if "text" in entry:
            if not isinstance(entry["text"], str):
                raise TemplateError(f"{where}: text must be a string")
            entry["text"] = entry["text"].strip("\n")
        models = entry.get("models", {})
        for model, override in models.items():
            mwhere = f"{where}.models.{model}"
            if (
                not isinstance(override, dict)
                or set(override) != {"text"}
                or not isinstance(override["text"], str)
            ):
                raise TemplateError(f"{mwhere}: exactly one key, text = \"...\"")
            override["text"] = override["text"].strip("\n")
        if "text" not in entry and not models:
            raise TemplateError(f"{where}: fills nothing (no text, no models)")
    return entries


def resolve_family(spec: str, models_dir: Path = MODELS_DIR) -> Path:
    """Resolve a --model-family argument to a family-file path.

    Anything path-shaped — containing a path separator or ending in .toml —
    is taken as a path, exactly as before. A bare family name is resolved
    against `models_dir`, matching <name>-addenda.toml or <name>.toml: both
    existing is an ambiguity error naming the candidates; neither is an error
    listing the available family files.
    """
    if "/" in spec or "\\" in spec or spec.endswith(FAMILY_SUFFIX):
        return Path(spec)
    candidates = [
        path
        for path in (
            models_dir / f"{spec}-addenda{FAMILY_SUFFIX}",
            models_dir / f"{spec}{FAMILY_SUFFIX}",
        )
        if path.is_file()
    ]
    if len(candidates) > 1:
        raise TemplateError(
            f"--model-family '{spec}' is ambiguous — candidates: "
            + ", ".join(rel(path) for path in candidates)
        )
    if not candidates:
        available = ", ".join(
            rel(path) for path in sorted(models_dir.glob(f"*{FAMILY_SUFFIX}"))
        )
        raise TemplateError(
            f"--model-family '{spec}' matches no family file under "
            f"{rel(models_dir)}/ (tried {spec}-addenda{FAMILY_SUFFIX} and "
            f"{spec}{FAMILY_SUFFIX}) — available: {available or '(none)'}"
        )
    return candidates[0]


def family_models(models_dir: Path = MODELS_DIR) -> dict[Path, set[str]]:
    """Family file -> the model names its [nb.*.models.*] tables mention.

    Loaded through load_family, so only real tables count — a model sketched
    in comments (e.g. claude-addenda.toml's schema illustrations) is not
    known to its family.
    """
    return {
        path: {
            model
            for entry in load_family(path).values()
            for model in entry.get("models", {})
        }
        for path in sorted(models_dir.glob(f"*{FAMILY_SUFFIX}"))
    }


def imply_family(model: str, models_dir: Path = MODELS_DIR) -> Path:
    """The family file a bare --model implies: the unique family whose
    [nb.*.models.<model>] tables mention it. None or several is an error —
    the caller must name the family explicitly."""
    known = family_models(models_dir)
    matches = [path for path, models in known.items() if model in models]
    if len(matches) == 1:
        return matches[0]
    if matches:
        raise TemplateError(
            f"--model '{model}' appears in more than one family file ("
            + ", ".join(rel(path) for path in matches)
            + ") — name one explicitly with --model-family"
        )
    listing = (
        "; ".join(
            f"{rel(path)}: "
            + (", ".join(sorted(models)) if models else "(none)")
            for path, models in known.items()
        )
        or "(no family files)"
    )
    raise TemplateError(
        f"--model '{model}' given without --model-family, and no family file "
        f"under {rel(models_dir)}/ mentions that model in a [nb.*.models.*] "
        f"table. Known models per family: {listing}. A model mentioned only "
        f"in comments is not known — to use it, name its family explicitly: "
        f"--model-family <family> --model {model}"
    )


def validate_family_anchors(entries: dict[str, dict], known: set[str]) -> None:
    """A family-file entry naming an anchor no template or chunk authors is a
    hard error — the file would silently fill nothing."""
    unknown = sorted(set(entries) - known)
    if unknown:
        raise TemplateError(
            "family file names anchor(s) that exist in no template or chunk: "
            + ", ".join(unknown)
        )


def resolve_nb(
    entries: dict[str, dict], model: str | None
) -> dict[str, tuple[str, str]]:
    """Resolve a loaded family file to anchor -> (text, scope).

    Resolution, not accumulation: at most one NB per anchor, the model scope
    ("model", via --model) winning over the family scope ("family"). An entry
    with only model overrides, none matching, resolves to nothing.
    """
    resolved = {}
    for anchor, entry in entries.items():
        models = entry.get("models", {})
        if model is not None and model in models:
            resolved[anchor] = (models[model]["text"], "model")
        elif "text" in entry:
            resolved[anchor] = (entry["text"], "family")
    return resolved


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
    text: str,
    chunks: dict[str, dict],
    scope: dict[str, str],
    nb: dict[str, tuple[str, str]] | None = None,
    depth: int = 0,
) -> str:
    """Expand every marker in `text`. `scope` binds placeholder arguments;
    `nb` is the resolved anchor -> (text, scope-label) map from a family file
    (None or missing anchor: the @@nb@@ marker expands to nothing)."""
    if depth > MAX_EXPANSION_DEPTH:
        raise TemplateError("marker expansion exceeded maximum depth (cycle?)")

    def expand(match: re.Match[str]) -> str:
        name = match.group(1)
        args = dict(ARG.findall(match.group(2)))
        if name == NB_MARKER:
            anchor = args.pop("name", None)
            if not anchor or args:
                raise TemplateError(
                    f'@@{NB_MARKER}@@ takes exactly one argument: name="<anchor>"'
                )
            entry = None if nb is None else nb.get(anchor)
            if entry is None:
                return ""
            # The filled text is marker-expanded like a chunk body, so a
            # typo'd reference inside it fails loudly.
            return render(f"**NB**: {entry[0]}", chunks, scope, nb, depth + 1)
        if name in scope:
            if args:
                raise TemplateError(f"placeholder '{name}' takes no arguments")
            return render(scope[name], chunks, scope, nb, depth + 1)
        chunk = chunks.get(name)
        if chunk is None:
            raise TemplateError(f"unknown chunk or placeholder '{name}'")
        width = args.pop("wrap", None)
        body = chunk_body(name, chunk, args)
        inner = {**chunk.get("defaults", {}), **args}
        inner.pop("variant", None)
        expanded = render(body, chunks, inner, nb, depth + 1)
        return wrap(expanded, int(width)) if width else expanded

    return MARKER.sub(expand, text)


# ─── Banner ──────────────────────────────────────────────────────────────────


def sha256_text(text: str) -> str:
    """Content hash as the banner records it, over utf-8 bytes."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def banner(
    template: Path,
    *,
    body_hash: str,
    tuning: tuple[Path, str | None] | None = None,
) -> str:
    """The four-line YAML-comment block stamped into frontmatter. `body_hash`
    is the sha256 of everything that will follow the block — the definition's
    claim to being unmodified since it was written. `tuning` is (family file,
    model or None) when rendering a tuned set; the banner then also claims the
    tuning, so check can hold it to that claim."""
    origin = f"{rel(template)} and {rel(SHARED_SECTIONS)}"
    if tuning is not None:
        family, model = tuning
        origin += f" with model family {rel(family)}"
        if model is not None:
            origin += f", model {model}"
    return (
        "#\n"
        f"# !GENERATED! from {origin}"
        " — edit those. DO NOT HAND EDIT THIS FILE.\n"
        f"# !BODY-SHA256! {body_hash}\n"
        "#"
    )


def banner_body(text: str) -> str | None:
    """Everything after the banner block — the bytes its !BODY-SHA256! line
    covers — or None when the file carries no hash-stamped banner."""
    match = BODY_HASH_CLAIM.search(text)
    return None if match is None else text[match.end() :]


def body_untouched(text: str) -> bool:
    """Is this file provably unmodified tool output, its post-banner bytes
    hashing to exactly what its own banner claims? A pre-1.5.0 banner carries
    no hash line, proves nothing, and is treated as possibly hand-edited."""
    match = BODY_HASH_CLAIM.search(text)
    return match is not None and match.group(1) == sha256_text(text[match.end() :])


def comparable_body(text: str) -> str:
    """What check diffs: the post-banner bytes when the banner stamps them,
    else the whole file (a bannerless or pre-hash file stamps nothing)."""
    body = banner_body(text)
    return text if body is None else body


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


def tuning_claim(text: str) -> tuple[str, str | None] | None:
    """Return the (family file, model) a definition's banner claims it was
    tuned with, or None for an untuned (or bannerless) definition."""
    front = frontmatter_of(text)
    if front is None:
        return None
    match = TUNING_CLAIM.search(front)
    return (match.group(1), match.group(2)) if match else None


def describe_tuning(claim: tuple[str, str | None] | None) -> str:
    """A tuning claim as printed in MISTUNED reports."""
    if claim is None:
        return "no model family"
    family, model = claim
    return f"model family {family}" + (f", model {model}" if model else "")


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


def render_template(
    path: Path,
    chunks: dict[str, dict],
    out_dir: Path,
    *,
    surface: str,
    nb: dict[str, tuple[str, str]] | None = None,
    tuning: tuple[Path, str | None] | None = None,
) -> list[tuple[Path, str]]:
    """Render every definition a template declares, banner stamped into each.

    `out_dir` is the template's path mirrored into its surface (see
    template_targets); `surface` names that surface. The banner always lands in
    frontmatter: agent templates must open with a frontmatter block; command
    templates may open with one (banner injected identically) or with bare
    prompt text (a minimal banner-only frontmatter block is emitted above it).
    The banner is stamped last, since it carries the hash of what follows it.
    """
    outputs, body = split_outputs(path)
    rendered = []
    for name, params in sorted(outputs.items()):
        text = render(body, chunks, params, nb)
        if text.startswith("---\n"):
            # YAML comments: valid frontmatter, dropped by every parser, and
            # outside the body that becomes the prompt.
            rest = text[4:]
        elif surface == COMMAND_SURFACE:
            rest = "---\n" + text
        else:
            raise TemplateError(
                f"{rel(path)}: agent template must open with YAML frontmatter"
            )
        stamp = banner(path, body_hash=sha256_text(rest), tuning=tuning)
        rendered.append((out_dir / f"{name}.md", "---\n" + stamp + "\n" + rest))
    return rendered


def template_targets(
    smap: dict[str, tuple[Path, Path]]
) -> list[tuple[str, Path, Path]]:
    """(surface name, template path, output directory) for every template.

    Discovery recurses through each surface's template tree (an absent tree is
    tolerated), and a template's relative subpath is mirrored into its surface:
    templates/agents/mad/participant-contract.md.tmpl has output directory
    agents/mad/. Placement is declared by the filesystem and nothing else.
    """
    found: list[tuple[str, Path, Path]] = []
    for name, (template_dir, out_dir) in smap.items():
        if not template_dir.is_dir():
            continue
        for template in template_dir.rglob(f"*{TEMPLATE_SUFFIX}"):
            subpath = template.parent.relative_to(template_dir)
            found.append((name, template, out_dir / subpath))
    return sorted(found, key=lambda target: rel(target[1]))


def templates(smap: dict[str, tuple[Path, Path]]) -> list[Path]:
    """Every template in every surface's template tree, discovered recursively."""
    return [template for _, template, _ in template_targets(smap)]


def declared_outputs(smap: dict[str, tuple[Path, Path]]) -> dict[str, set[str]]:
    """Map template path (repo-relative) -> the output paths it declares."""
    outputs = {}
    for _, template, out_dir in template_targets(smap):
        outputs[rel(template)] = {
            rel(out_dir / f"{name}.md") for name in split_outputs(template)[0]
        }
    return outputs


def check_banner_claims(smap: dict[str, tuple[Path, Path]]) -> list[str]:
    """Verify every definition carrying a banner has the template it names.

    Enrollment runs template -> definition, so iterating templates cannot see a
    definition whose banner points at a template that no longer exists — a
    stale output left by a deleted or renamed template, outside every other
    check and drifting silently. This walks the claims the other way, across
    both deployed surfaces (agents/ and commands/) and recursively through
    their subdirectories, since outputs mirror nested template paths.
    """
    errors = []
    outputs = declared_outputs(smap)
    for _, out_dir in smap.values():
        for path in sorted(out_dir.rglob("*.md")):
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


def all_renders(
    chunks: dict[str, dict],
    smap: dict[str, tuple[Path, Path]],
    nb: dict[str, tuple[str, str]] | None = None,
    tuning: tuple[Path, str | None] | None = None,
) -> list[tuple[Path, str]]:
    """Every (target, rendered text) pair across every template, sorted."""
    pairs = []
    for surface, template, out_dir in template_targets(smap):
        pairs.extend(
            render_template(
                template, chunks, out_dir, surface=surface, nb=nb, tuning=tuning
            )
        )
    return sorted(pairs, key=lambda pair: rel(pair[0]))


def assert_output_dirs(out_dirs: set[Path]) -> None:
    """Guard against a wrong output-dir constant, which would otherwise exit 0
    while writing to the wrong place: every surface directory receiving output
    must be an existing directory strictly inside this repository. Mirrored
    subdirectories beneath a surface are not covered — they are declared by the
    template tree, not by a constant, and are created on demand."""
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


def report_nb(
    entries: dict[str, dict],
    nb: dict[str, tuple[str, str]],
    tuning: tuple[Path, str | None],
) -> None:
    """State which anchors the family file filled, and from which scope."""
    family, model = tuning
    print()
    print(f"NB anchors from {rel(family)}" + (f" (model {model})" if model else ""))
    print("=" * 60)
    for anchor in sorted(entries):
        scope = nb[anchor][1] if anchor in nb else "unfilled (no scope matched)"
        print(f"  {anchor:<28} {scope}")


def generate(
    chunks: dict[str, dict],
    smap: dict[str, tuple[Path, Path]],
    *,
    nb: dict[str, tuple[str, str]] | None = None,
    tuning: tuple[Path, str | None] | None = None,
    explicit_root: bool = False,
    verbose: bool = False,
) -> bool:
    clean = True
    unchanged = 0
    print("Generating definitions")
    print("=" * 60)
    found = template_targets(smap)
    if not found:
        print(f"  ERROR      no templates found under {rel(TEMPLATES_DIR)}/")
        return False

    pairs = all_renders(chunks, smap, nb, tuning)
    if not explicit_root:
        # The wrong-constant guard covers the surface directories only; the
        # mirrored subdirectories below them come from the template tree.
        assert_output_dirs({smap[surface][1] for surface, _, _ in found})
    # The --output-dir root itself was asserted to exist when the surface map
    # was built. Everything below a surface — the surface directory under an
    # explicit root, and mirrored subdirectories under either mode — is
    # created as needed.
    for out_dir in sorted({target.parent for target, _ in pairs}):
        out_dir.mkdir(parents=True, exist_ok=True)
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

        if body_untouched(actual):
            # Provably this tool's own output: reproducible from the template,
            # so a backup of it would be landfill.
            target.write_text(rendered, encoding="utf-8")
            print(f"  {'updated':<10} {rel(target)}")
            continue

        backup = back_up(target)
        target.write_text(rendered, encoding="utf-8")
        print(
            f"  {'updated':<10} {rel(target)} (hand-edited or pre-hash content "
            f"backed up beside it as {backup.name})"
        )

    print()
    print(f"  {'unchanged':<10} {unchanged} definition(s)")
    where = ", ".join(f"{d}/ ({n})" for d, n in sorted(landed.items()))
    print(f"{len(pairs)} definition(s) from {len(found)} template(s), in: {where}")
    return clean


def check(
    chunks: dict[str, dict],
    smap: dict[str, tuple[Path, Path]],
    *,
    nb: dict[str, tuple[str, str]] | None = None,
    tuning: tuple[Path, str | None] | None = None,
    verbose: bool = False,
    show_diff: bool = True,
) -> bool:
    clean = True
    print()
    print("Generated definitions vs templates")
    print("=" * 60)

    found = templates(smap)
    if not found:
        print(f"  ERROR    no templates found under {rel(TEMPLATES_DIR)}/")
        clean = False

    # Check runs under exactly one tuning configuration — the one it was
    # invoked with — and holds every target's banner to that claim.
    expected_tuning = None
    if tuning is not None:
        expected_tuning = (rel(tuning[0]), tuning[1])

    for target, rendered in all_renders(chunks, smap, nb, tuning):
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
        claimed = tuning_claim(actual)
        if claimed != expected_tuning:
            # A tuning mismatch would also show as a byte diff (the banner is
            # part of the render); name the mismatch instead of dumping it.
            print(
                f"  {'MISTUNED':<8} {rel(target)} — banner claims "
                f"{describe_tuning(claimed)}, but check ran with "
                f"{describe_tuning(expected_tuning)}"
            )
            continue
        print(f"  {'DRIFT':<8} {rel(target)} — differs from rendered template")
        if show_diff:
            # Over the post-banner bytes: a body change carries its own hash
            # line with it, and diffing that too would only add noise.
            diff = difflib.unified_diff(
                comparable_body(rendered).splitlines(keepends=True),
                comparable_body(actual).splitlines(keepends=True),
                fromfile=f"rendered/{rel(target)}",
                tofile=rel(target),
            )
            for line in diff:
                print(f"      {line.rstrip()}")

    print()
    print("Banner claims vs templates")
    print("=" * 60)
    claim_errors = check_banner_claims(smap)
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


# ─── Full-product install ────────────────────────────────────────────────────

# The only files under agents/ and commands/ an install does NOT deliver.
# Everything else is copied verbatim — there is no inclusion list, so a new
# definition or tool file ships without an enrollment step. Directory names
# match at any depth; the suffix catches the generator's own safety copies.
INSTALL_EXCLUDED_DIRS = frozenset({"tests", "__pycache__", ".pytest_cache"})
INSTALL_EXCLUDED_NAMES = frozenset({".DS_Store"})
INSTALL_EXCLUDED_SUFFIX = ".bak"

MANIFEST_NAME = "agents-install-manifest.json"
MANIFEST_VERSION = 1


def excluded_from_install(relpath: Path) -> bool:
    """Is this surface-relative path on the install exclusion list?"""
    return (
        bool(INSTALL_EXCLUDED_DIRS.intersection(relpath.parts[:-1]))
        or relpath.name in INSTALL_EXCLUDED_NAMES
        or relpath.suffix == INSTALL_EXCLUDED_SUFFIX
    )


def install_pairs(
    smap: dict[str, tuple[Path, Path]], *, source_root: Path = REPO_ROOT
) -> list[tuple[str, Path, Path]]:
    """(surface-relative key, source, target) for every file an install copies:
    the deployed surfaces entire, minus the exclusion list. The generated
    definitions are copied like everything else — a checked-in surface already
    holds each one's base render."""
    found: list[tuple[str, Path, Path]] = []
    for surface, (_, out_dir) in sorted(smap.items()):
        surface_root = source_root / surface
        if not surface_root.is_dir():
            continue
        for source in sorted(surface_root.rglob("*")):
            if not source.is_file():
                continue
            relpath = source.relative_to(surface_root)
            if excluded_from_install(relpath):
                continue
            found.append(
                ((Path(surface) / relpath).as_posix(), source, out_dir / relpath)
            )
    return found


def source_commit(repo: Path = REPO_ROOT) -> str:
    """`git rev-parse HEAD`, suffixed -dirty when the work tree has changes.
    'unknown' when git cannot answer — an install from an unpacked tarball is
    still a legitimate install, it just cannot cite a commit."""

    def git(*args: str) -> str | None:
        try:
            done = subprocess.run(
                ("git", "-C", str(repo), *args),
                capture_output=True,
                text=True,
                check=True,
            )
        except (OSError, subprocess.CalledProcessError):
            return None
        return done.stdout.strip()

    head = git("rev-parse", "HEAD")
    if head is None:
        return "unknown"
    return head + ("-dirty" if git("status", "--porcelain") else "")


def write_manifest(
    path: Path, *, tuning: tuple[Path, str | None] | None
) -> None:
    """Stamp the installed tree with its provenance. Write-only: nothing reads
    it back, because the tree is an artifact — a re-install overwrites it
    unconditionally rather than reasoning about what is already there."""
    payload = {
        "manifest_version": MANIFEST_VERSION,
        "gen_defs_version": __version__,
        "source_commit": source_commit(),
        "flavor": (
            None
            if tuning is None
            else {"family": rel(tuning[0]), "model": tuning[1]}
        ),
        "installed": datetime.now(UTC).isoformat(timespec="seconds"),
    }
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def install(
    chunks: dict[str, dict],
    smap: dict[str, tuple[Path, Path]],
    *,
    root: Path,
    nb: dict[str, tuple[str, str]] | None = None,
    tuning: tuple[Path, str | None] | None = None,
    source_root: Path = REPO_ROOT,
    verbose: bool = False,
) -> bool:
    """Install the full product into `root` (a consuming project's .claude).

    A plain recursive copy of the deployed surfaces under `source_root` (this
    repository, in every real invocation), minus the exclusion list, followed
    by a provenance stamp. The copied definitions are already their own base
    renders, so an untuned install renders nothing and merely checks the
    result; a tuned install runs the ordinary generation pass over the copy,
    which rewrites what the flavor changes without leaving backups behind
    (freshly copied definitions are provably untouched output).
    """
    pairs = install_pairs(smap, source_root=source_root)
    print("Installing deployed surfaces")
    print("=" * 60)
    landed: dict[str, int] = {}
    for key, source, target in pairs:
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)
        landed[key.split("/", 1)[0]] = landed.get(key.split("/", 1)[0], 0) + 1
        if verbose:
            print(f"  {'copied':<10} {key}")
    for surface, count in sorted(landed.items()):
        print(f"  {'copied':<10} {count} file(s) into {rel(root / surface)}/")
    if not pairs:
        print(f"  ERROR      no installable files under {rel(source_root)}/")
        return False

    print()
    if tuning is None:
        # The copy IS the install: what landed is already each definition's
        # base render, so this pass reports on the result without gating it —
        # a target may hold material this tool never put there.
        integrity = check(chunks, smap, verbose=verbose, show_diff=False)
        verdict = "rendered 0, integrity " + ("OK" if integrity else "REPORTED ABOVE")
        ok = True
    else:
        ok = generate(
            chunks, smap, nb=nb, tuning=tuning, explicit_root=True, verbose=verbose
        )
        verdict = f"rendered {len(all_renders(chunks, smap, nb, tuning))}"

    manifest_path = root / MANIFEST_NAME
    write_manifest(manifest_path, tuning=tuning)

    claim = None if tuning is None else (rel(tuning[0]), tuning[1])
    print()
    print(
        f"install: copied {len(pairs)}, {verdict}; "
        f"manifest {rel(manifest_path)}; {describe_tuning(claim)}"
    )
    print(
        "the installed tree is an artifact — re-install to update it; local "
        "edits to installed files are overwritten, not preserved."
    )
    return ok



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
        "--install",
        type=Path,
        metavar="ROOT",
        help="full-product install into ROOT (a project's existing .claude "
        "directory): copy the deployed surfaces entire, minus test suites and "
        "caches, and stamp the tree with its provenance. A flavor "
        "(--model-family/--model) adds a render pass over the copy. Implies "
        "--output-dir ROOT",
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
    parser.add_argument(
        "--output-dir",
        type=Path,
        metavar="ROOT",
        help="render/check the agents/ and commands/ subtrees under ROOT "
        "(an existing directory) instead of the repo's deployed surfaces",
    )
    parser.add_argument(
        "--surfaces",
        choices=("agents", "commands", "both"),
        default="both",
        help="which surface to render or check (default: both)",
    )
    parser.add_argument(
        "--model-family",
        metavar="FILE|NAME",
        help="model-family NB file filling @@nb@@ anchors — a path, or a bare "
        "family name resolved against templates/models/ "
        "(see templates/models/README.md)",
    )
    parser.add_argument(
        "--model",
        metavar="NAME",
        help="activate NAME's per-model overrides in the family file; "
        "without --model-family, the one family whose [nb.*.models.*] "
        "tables mention NAME is implied",
    )
    args = parser.parse_args()
    if args.install is not None and (args.generate or args.output_dir is not None):
        parser.error(
            "--install ROOT already implies --generate --output-dir ROOT — "
            "pass it alone"
        )
    output_root = args.install if args.install is not None else args.output_dir

    try:
        smap = surface_map(output_root=output_root, surfaces=args.surfaces)
        chunks = load_chunks()
        nb = tuning = entries = family = None
        if args.model_family is not None:
            family = resolve_family(args.model_family)
        elif args.model is not None:
            family = imply_family(args.model)
            print(f"--model {args.model} implies model family {rel(family)}")
        if family is not None:
            entries = load_family(family)
            # Anchors are collected across ALL templates and chunks, not just
            # the --surfaces selection, so a filtered render never miscalls a
            # real anchor unknown.
            validate_family_anchors(
                entries, collect_anchors(chunks, templates(surface_map()))
            )
            nb = resolve_nb(entries, args.model)
            tuning = (family, args.model)
        if args.install is not None:
            ok = install(
                chunks,
                smap,
                root=args.install,
                nb=nb,
                tuning=tuning,
                verbose=args.verbose,
            )
            if entries is not None:
                report_nb(entries, nb, tuning)
        elif args.generate:
            ok = generate(
                chunks,
                smap,
                nb=nb,
                tuning=tuning,
                explicit_root=output_root is not None,
                verbose=args.verbose,
            )
            if entries is not None:
                report_nb(entries, nb, tuning)
        else:
            ok = check(
                chunks,
                smap,
                nb=nb,
                tuning=tuning,
                verbose=args.verbose,
                show_diff=not args.no_diff,
            )
    except TemplateError as exc:
        print(f"error: {exc}", file=sys.stderr)
        sys.exit(2)

    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
