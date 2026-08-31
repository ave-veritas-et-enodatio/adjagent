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
                            surface to render or check. Cannot be combined
                            with the selection globs below, which imply it.
    --agent-glob PATTERNS   restrict the run to the agents outputs matching
                            PATTERNS: one or more fnmatch patterns joined by
                            "|" ("*app-expert*|*-coder*"). See below.
    --command-glob PATTERNS the same, over the commands surface.
    --model-family SPEC     the one model-tuning flag. SPEC names a family
                            file OR a model inside one, and resolution decides
                            which (Flavor resolution, below). A family SPEC
                            loads that family and asks for no model scope; a
                            model SPEC loads the family declaring that model
                            and makes it the EXPORT FLAVOR for the outputs
                            carrying no frontmatter `model:` pin. A pinned
                            output resolves its own model scope either way
                            (see per-pin resolution below).

The strictly-inside-this-repository output guard applies only to the implicit
default output location; an explicit --output-dir is exempt from the
inside-repo constraint but is still asserted to be an existing directory. The
full per-target safety table below (banner detection, numbered backups,
refusal of bannerless files) applies identically to out-of-repo targets.

Definition selection — --agent-glob and --command-glob:

A pattern is matched against an output's SURFACE-RELATIVE path with the .md
suffix dropped — go-coder, kb-start, mad/participant-contract — under fnmatch
semantics, in which `*` crosses `/`. A nested output is therefore addressable
both by its full key (mad/participant-contract) and by any pattern spanning
the separator (*participant-contract*), and a bare *-coder* selects the
top-level coders without reaching into a subdirectory only because none of
those outputs live in one.

Presence of either glob IMPLIES the surface(s) the run covers: --agent-glob
alone renders/checks the agents surface only, filtered; --command-glob alone
the commands surface only; both, both surfaces, each filtered by its own
patterns; neither, every output, as always. --surfaces is therefore redundant
with a glob and combining them is an argparse error. So is combining a glob
with --install: an install delivers the whole product, and a partial install
is a future feature, not this flag.

Selection filters PER OUTPUT, not per template: a glob matching one output of
a multi-output template renders or checks exactly that one, and the template's
other outputs are left untouched on disk and reported as nothing at all.

A pattern matching zero outputs — any single "|"-segment, and therefore the
set — is a hard error naming the pattern and listing what the surface does
declare. A selection that silently selected nothing would report exactly like
a clean run. What was selected is stated in the run report, per surface:

    agents: 12 of 24 outputs selected by --agent-glob

Selection composes orthogonally with everything else: NB anchors and per-pin
model resolution, banners and tuning claims, the write-safety table, and both
checks all apply to the selected outputs exactly as they do to a full run.

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
                                          # that model

Resolution, not accumulation: AT MOST ONE NB renders per anchor — the model
scope wins over the family scope.

Flavor resolution — what one --model-family SPEC means:

There is exactly ONE tuning flag, because family and model are not independent
choices: a model is only ever reachable through the family that declares it,
and naming both invited combinations that contradict each other. SPEC is
resolved in this order, against templates/models/:

    1. path-shaped   — contains a path separator or ends in .toml: taken as a
                       path to a family file, unresolved and unvalidated here
                       (load_family owns the existence error).
    2. family name   — matches <SPEC>-addenda.toml or <SPEC>.toml: that family
                       is loaded, and NO model scope is asked for. Both files
                       existing is an ambiguity error naming the candidates.
    3. model name    — found in exactly one family's [nb.*.models.<SPEC>]
                       tables: that family is loaded AND <SPEC> becomes the
                       export flavor for unpinned outputs. A model mentioned
                       only in comments is NOT known.

A bare name matching BOTH a family file and a model is an ambiguity error
naming both readings; matching a model in SEVERAL families is an error naming
them and demanding the family file path instead; matching NEITHER is an error
listing the available family files and each one's known models.

Model scope resolves PER OUTPUT, against that output's own frontmatter
`model:` pin — the value in its RENDERED frontmatter, whether that comes from
an outputs-table parameter (@@model@@, as the mad-participant template
declares) or a literal frontmatter line. An output pinned to X resolves X's
overrides: within the family file a family SPEC named, or, when the SPEC named
a model (or nothing at all), within the one family whose [nb.*.models.X]
tables mention X. A pin matching no family — or matching several — resolves to
NO model scope at all, SILENTLY: a pin is routine metadata saying which model
the definition dispatches on, not a request for tuning. A model SPEC keeps the
opposite semantics, because it IS a request: one that matches nothing is still
an error.

Family scope is unchanged by any of that: a loaded family file's family-wide
text fills its anchors render-wide, for pinned and unpinned outputs alike. A
family a PIN implies contributes only its model-scope entry for that pin,
never its family-wide text — that text belongs to the family the invocation
actually loaded. The banner records the invocation's tuning; per-pin
resolution is a deterministic function of that invocation, the templates, and
templates/models/, so check reproduces it exactly.

A model SPEC is therefore the EXPORT FLAVOR: it tunes the outputs carrying no
pin (today the generated commands and the participant contract) and is ignored
by the pinned ones. That is accounted for out loud rather than silently, in the
generate/check/install report:

    --model-family haiku (model in family templates/models/claude-addenda.toml):
    applied to 5 unpinned output(s); skipped 23 pinned output(s) (pins own
    their tuning)

A family SPEC prints no such line: nothing model-scoped was asked for the
unpinned outputs, so there is no reach to account for.

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
*.bak gitignore rule keeps it out of git. Serials are per-target, zero-padded
from 00, allocated as the highest existing serial plus one, and never reused.

Every write in this tool is a CONTENT write and nothing else. An extant target
is written in place — its inode, owner and mode survive untouched — and a
backup is a plain content copy into a new file the tool itself owns, never a
metadata clone of the file it copies. That is a portability property, not a
detail: writing bytes into an existing file needs only write permission, while
cloning mtime or mode (utime, chmod — what shutil.copy2/copystat/copymode do)
requires OWNERSHIP of the target, which fails outright in a shared group-writable
tree whose files another user installed. Timestamps carry no meaning anywhere in
this system — integrity is decided by the banner's content hash — so cloned
metadata bought nothing and cost a mid-run EACCES that could split an install
into a partial apply. The one metadata write left is on the creation path
alone: a file the tool has just created takes the source's executable bit (a
chmod on a file it owns by construction, always legal), so an installed shell
tool stays runnable.

As a guard against a wrong output-directory constant — which would otherwise
exit 0 while writing to the wrong place — default-location generation first
asserts that every SURFACE directory receiving output is an existing directory
inside this repository, and the generation report states where the files
landed (--output-dir generation instead asserts ROOT exists, as described
above). Mirrored subdirectories beneath a surface are not part of that guard:
they come from the template tree rather than from a constant, and are created
as needed under either mode.

Full-product install — `--install ROOT`:

    python3 gen-defs.py --install <project>/.claude [--model-family SPEC]

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
source of truth and a re-install always overwrites. Local edits to installed
files are never preserved — edit the templates or the definitions here and
re-install — but they are not destroyed either: a target that is not provably
this tool's own output is copied aside as a numbered .bak first, exactly as
generation does it. The overwrite always happens; the backup only keeps
divergent human work from being destroyed by it, and *.bak files under an
installed tree are deletable at will.

An install writes exactly as generation does (see the content-write paragraph
above): an extant installed file is rewritten through its own inode, so a tree
installed by one user updates cleanly under another so long as the group can
write. Ownership and mode are whatever the first install left; only a file this
run creates is chmodded, and only to carry the source's executable bit.

Every COPIED file is stamped with an !INSTALLED! banner carrying the same
!BODY-SHA256! line the generated banner carries, in whatever comment syntax
its filetype admits:

    *.md with frontmatter    comment lines inside the frontmatter block —
                             dropped by every frontmatter reader, including
                             liaison_tools/extract-agent-body.sh
    *.md without, commands/  a minimal frontmatter block holding only the
                             banner, as render_template already does for a
                             frontmatter-less command template: a command's
                             first BODY line is the description Claude Code
                             lists it by, and nothing may displace it
    *.md without, agents/    an HTML comment block above the content —
                             supporting material must not gain frontmatter,
                             which would make a methodology topic look
                             extractable as a definition body
    .py .sh .toml .mk .just  `#` comment lines at the top, below a shebang
    any other suffix         no comment syntax to carry a banner: the file is
                             copied verbatim and the install reports it

A generated definition is exempt: it arrives carrying its own !GENERATED!
banner, which already forbids in-place edits and names the real edit path.
The two banners are one marking scheme — provenance plus a body hash — and
the hash buys the same thing on re-install that it buys on regeneration: a
target whose body still hashes to its own banner is provably ours and is
overwritten silently, while a mismatched or unbannered target is backed up
first. Re-installing an untouched tree is therefore byte-stable: no content
changes and no backups.

The banner is a four-line YAML-comment block naming the source template
(`# !GENERATED! from templates/agents/<name>.md.tmpl ...`). When rendered with
a family file, the banner line additionally names the family file, and the
model too when the SPEC named a model — the minimal claim that separates two
renders: family and model SPECs resolving to the same file still render
unpinned outputs differently, so the model half is recorded exactly when it
was asked for. The banner is a tuned set's claim to its tuning, just as it is
a definition's claim to being generated. The banner
always lives inside YAML frontmatter, never in the body that becomes a prompt:

  - An agent template must open with a frontmatter block; the banner is
    injected at its top.
  - A command template whose body opens with a frontmatter block gets the
    banner injected the same way; a command template with no frontmatter gets
    a minimal frontmatter block emitted above its body, containing only the
    banner comment lines, so the banner never lands in the literal prompt
    text.

Its third line, `# !BODY-SHA256! <hex>`, is the sha256 of everything the file
holds AFTER the banner block, exactly as written — the one line both banner
kinds share. It lets any later reader answer a question the banner alone
cannot: is this file still the bytes the tool wrote, or has someone edited it
since? A file whose body hashes to its own claim is provably untouched tool
output — reproducible at will, and therefore safe to overwrite without a
backup (see the safety table above). A pre-1.5.0 banner carries no hash line
and proves nothing, so it is treated as possibly hand-edited.

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

Check accepts the same --output-dir / --surfaces / --model-family flags, so a
tuned out-of-repo set gets the identical render-identity and
banner-claims validation. Tuning claims are part of both checks: check runs
under exactly one tuning configuration (the one it was invoked with), renders
with it, and requires every target's banner to claim exactly that
configuration. A banner naming a family file (or model) that check was not
invoked with — or omitting one it was — is reported MISTUNED, naming both
sides, instead of an opaque byte diff; a target whose tuning claim matches but
whose bytes differ is ordinary DRIFT. Validating a tuned set therefore means
invoking check with that set's --output-dir and its --model-family SPEC;
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
import fnmatch
import hashlib
import re
import stat
import sys
import tomllib
from collections.abc import Callable
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
# the numbered backup; 1.6.0 drops the write-only install manifest and instead
# stamps every COPIED file with an !INSTALLED! banner in its filetype's comment
# syntax, extending the hash-gated write-safety table to the install; 1.7.0
# resolves model-scope NBs per output, against that output's own frontmatter
# model: pin, which makes a CLI model ask the export flavor for unpinned
# outputs alone — loudly accounted for, silent for a pin matching nothing; 1.8.0
# adds definition-selection globs — --agent-glob / --command-glob, matched
# per output against the surface-relative output path, implying the surfaces
# they cover and hard-erroring on a pattern that matches nothing; 1.8.1 makes
# every write a pure content write — in-place into the extant inode, backups
# as plain content copies, and no utime/chmod on a file the runner may not
# own, which is what made an install over another user's tree fail; 2.0.0
# collapses --model-family and --model into ONE flag — --model-family SPEC,
# whose bare name resolves to a family file OR to a model inside one — because
# the pair let an operator name a family and a model that contradicted each
# other; --model is REMOVED rather than deprecated, this tool having one
# operator, so the old flag is an unknown-flag error).
# Deliberately NOT embedded in rendered banners — that would churn every
# generated file on every bump.
__version__ = "2.0.0"

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
# Definition selection: the flag each surface is globbed with, and the
# separator joining several patterns into one flag value.
SURFACE_GLOB_FLAG = {"agents": "--agent-glob", "commands": "--command-glob"}
GLOB_SEPARATOR = "|"
# The NB model-tuning marker's reserved name; no chunk or placeholder may
# claim it. Anchor names share the marker-name charset.
NB_MARKER = "nb"
ANCHOR_NAME = re.compile(r"[a-z0-9-]+")
# An output's own model pin, read out of its RENDERED frontmatter: the value
# of a `model:` key, however it got there — an outputs-table parameter or a
# literal line. Quotes are optional in YAML and stripped here.
FRONTMATTER_PIN = re.compile(r"""^model:[ \t]*["']?([^"'\s#]+)["']?[ \t]*$""", re.MULTILINE)

# Surface -> the fnmatch patterns selecting that surface's outputs. A surface
# absent from the map is unfiltered, so None and {} both mean "everything".
GlobMap = dict[str, list[str]]
# anchor name -> (NB text, the scope it resolved from: "family" or "model").
NBMap = dict[str, tuple[str, str]]
# What the render path accepts for `nb`: a per-pin resolver (pin -> map), or a
# plain map applied render-wide, or nothing at all.
NBSource = NBMap | Callable[[str | None], NBMap | None] | None

MARKER = re.compile(r'@@([a-z0-9-]+)((?:\s+[a-z_]+="[^"]*")*)\s*@@')
ARG = re.compile(r'([a-z_]+)="([^"]*)"')
# The banner, read back out of a definition as its claim to being generated.
# Deliberately path-agnostic: any *.md.tmpl claim marks the file as generated
# (gating write safety); whether the claimed template exists and declares the
# file is check 2's job, so a stale claim is ORPHAN/MISLABELED, not REFUSED.
BANNER_CLAIM = re.compile(r"^# !GENERATED! from (\S+\.md\.tmpl)\b", re.MULTILINE)
# The banner's hash line and the line that closes the block around it: the
# hash of everything after it, and the marker for where "everything after it"
# begins. Matching both together means one search locates the claim and the
# bytes it covers — for either banner kind, since an !INSTALLED! banner in an
# HTML comment closes with "-->" where the others close with "#".
BODY_HASH_CLAIM = re.compile(r"^# !BODY-SHA256! ([0-9a-f]{64})\n(?:#|-->)\n", re.MULTILINE)
# A tuned banner's second claim: which family file (and model) rendered it.
TUNING_CLAIM = re.compile(
    r"^# !GENERATED! from \S+\.md\.tmpl and \S+" r" with model family (\S+)(?:, model (\S+))? — edit",
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


# ─── Definition selection ────────────────────────────────────────────────────


def split_globs(spec: str) -> list[str]:
    """One --agent-glob/--command-glob value as its individual patterns."""
    return spec.split(GLOB_SEPARATOR)


def output_key(target: Path, surface_root: Path) -> str:
    """What a selection pattern matches: the output's path relative to its
    surface, without the .md suffix — go-coder, mad/participant-contract."""
    return target.relative_to(surface_root).with_suffix("").as_posix()


def selected(surface: str, key: str, globs: GlobMap | None) -> bool:
    """Does the selection cover this output? A surface with no patterns is
    covered entire. fnmatchcase, not fnmatch: matching must not depend on the
    host filesystem's case rules."""
    patterns = (globs or {}).get(surface)
    return patterns is None or any(fnmatch.fnmatchcase(key, pattern) for pattern in patterns)


def output_keys(smap: dict[str, tuple[Path, Path]]) -> dict[str, list[str]]:
    """Surface -> every output key its templates declare, sorted. The set a
    selection is validated and accounted against, without rendering anything."""
    keys: dict[str, list[str]] = {surface: [] for surface in smap}
    for surface, template, out_dir in template_targets(smap):
        surface_root = smap[surface][1]
        keys[surface].extend(output_key(out_dir / f"{name}.md", surface_root) for name in split_outputs(template)[0])
    return {surface: sorted(found) for surface, found in keys.items()}


def validate_selection(keys: dict[str, list[str]], globs: GlobMap) -> None:
    """A pattern matching no output is a hard error naming it and listing what
    its surface declares. Every "|"-segment is held to this individually, so a
    typo inside an alternation cannot hide behind a sibling that matches — and
    a selection that selected nothing can never report like a clean run."""
    for surface, patterns in globs.items():
        available = keys.get(surface, [])
        for pattern in patterns:
            if not any(fnmatch.fnmatchcase(key, pattern) for key in available):
                raise TemplateError(
                    f"{SURFACE_GLOB_FLAG[surface]} pattern '{pattern}' matches none of "
                    f"the {len(available)} {surface} output(s): " + (", ".join(available) or "(none)")
                )


def report_selection(keys: dict[str, list[str]], globs: GlobMap) -> None:
    """State what each globbed surface selected, out of what it declares."""
    print()
    for surface, patterns in sorted(globs.items()):
        available = keys.get(surface, [])
        chosen = sum(1 for key in available if selected(surface, key, globs))
        print(f"{surface}: {chosen} of {len(available)} outputs selected by {SURFACE_GLOB_FLAG[surface]}")


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


def collect_anchors(chunks: dict[str, dict], template_paths: list[Path]) -> set[str]:
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
            if not isinstance(override, dict) or set(override) != {"text"} or not isinstance(override["text"], str):
                raise TemplateError(f'{mwhere}: exactly one key, text = "..."')
            override["text"] = override["text"].strip("\n")
        if "text" not in entry and not models:
            raise TemplateError(f"{where}: fills nothing (no text, no models)")
    return entries


def family_models(models_dir: Path = MODELS_DIR) -> dict[Path, set[str]]:
    """Family file -> the model names its [nb.*.models.*] tables mention.

    Loaded through load_family, so only real tables count — a model sketched
    in comments (e.g. claude-addenda.toml's schema illustrations) is not
    known to its family.
    """
    return {
        path: {model for entry in load_family(path).values() for model in entry.get("models", {})}
        for path in sorted(models_dir.glob(f"*{FAMILY_SUFFIX}"))
    }


def resolve_flavor(spec: str, models_dir: Path = MODELS_DIR) -> tuple[Path, str | None]:
    """Resolve the single --model-family SPEC to (family file, model or None).

    Family and model are one flag because they are one choice: a model is only
    reachable through the family declaring it. SPEC resolves in three steps —

      1. path-shaped (a path separator, or a .toml suffix): a family file path,
         taken as given. load_family owns the existence error.
      2. a bare family name: <spec>-addenda.toml or <spec>.toml under
         `models_dir`. That family, and no model scope.
      3. a bare model name: found in exactly one family's [nb.*.models.<spec>]
         tables. That family, with <spec> as the export flavor.

    Matching a family AND a model is an ambiguity error naming both readings;
    matching two family files is the same error over the two candidates;
    matching a model in several families demands the family file path instead;
    matching nothing lists the family files and each one's known models.

    Every family file is loaded to decide steps 2 and 3 apart, so a malformed
    sibling family file fails a bare SPEC that does not name it. That is
    inherent to resolving one flag against both namespaces, and loud.
    """
    if "/" in spec or "\\" in spec or spec.endswith(FAMILY_SUFFIX):
        return Path(spec), None
    families = [
        path
        for path in (
            models_dir / f"{spec}-addenda{FAMILY_SUFFIX}",
            models_dir / f"{spec}{FAMILY_SUFFIX}",
        )
        if path.is_file()
    ]
    known = family_models(models_dir)
    declaring = [path for path, models in known.items() if spec in models]

    if families and declaring:
        raise TemplateError(
            f"--model-family '{spec}' is ambiguous: it names the family file(s) "
            + ", ".join(rel(path) for path in families)
            + " and a model declared in "
            + ", ".join(rel(path) for path in declaring)
            + " — pass the family file path to mean the family, or rename one of the two"
        )
    if len(families) > 1:
        raise TemplateError(
            f"--model-family '{spec}' is ambiguous — candidates: " + ", ".join(rel(path) for path in families)
        )
    if families:
        return families[0], None
    if len(declaring) == 1:
        return declaring[0], spec
    if declaring:
        raise TemplateError(
            f"--model-family '{spec}' is a model declared in more than one family file ("
            + ", ".join(rel(path) for path in declaring)
            + ") — pass the family file path instead"
        )
    listing = (
        "; ".join(
            f"{rel(path)}: " + (", ".join(sorted(models)) if models else "(no models)")
            for path, models in known.items()
        )
        or "(no family files)"
    )
    raise TemplateError(
        f"--model-family '{spec}' names neither a family file nor a known "
        f"model: no {spec}-addenda{FAMILY_SUFFIX} or {spec}{FAMILY_SUFFIX} "
        f"under {rel(models_dir)}/, and no family's [nb.*.models.*] tables "
        f"declare it. Available families and their known models: {listing}. A "
        f"model mentioned only in comments is not declared; a family file "
        f"named otherwise is reachable by its path."
    )


def validate_family_anchors(entries: dict[str, dict], known: set[str]) -> None:
    """A family-file entry naming an anchor no template or chunk authors is a
    hard error — the file would silently fill nothing."""
    unknown = sorted(set(entries) - known)
    if unknown:
        raise TemplateError("family file names anchor(s) that exist in no template or chunk: " + ", ".join(unknown))


def model_scope(entries: dict[str, dict], model: str) -> NBMap:
    """Only the model-scope NBs `model` matches in `entries`.

    The per-pin half of resolution, on its own: a pin pulls a family file's
    override for its own model and nothing else — never that file's
    family-wide text, which belongs to the family the invocation loaded.
    """
    return {
        anchor: (entry["models"][model]["text"], "model")
        for anchor, entry in entries.items()
        if model in entry.get("models", {})
    }


def family_scope(entries: dict[str, dict]) -> NBMap:
    """Only the family-wide NBs in `entries` — what a loaded family file fills
    render-wide, for pinned and unpinned outputs alike."""
    return {anchor: (entry["text"], "family") for anchor, entry in entries.items() if "text" in entry}


def resolve_nb(entries: dict[str, dict], model: str | None) -> NBMap:
    """Resolve a loaded family file to anchor -> (text, scope).

    Resolution, not accumulation: at most one NB per anchor, the model scope
    ("model") winning over the family scope ("family"). An entry with only
    model overrides, none matching, resolves to nothing.
    """
    return {**family_scope(entries), **(model_scope(entries, model) if model is not None else {})}


def per_pin_resolver(
    entries: dict[str, dict] | None,
    *,
    cli_model: str | None,
    family_given: bool,
    models_dir: Path = MODELS_DIR,
) -> Callable[[str | None], NBMap]:
    """Build the per-output NB resolver: output's model pin -> resolved NBs.

    Model scope belongs to the output, not to the render. An output pinned to
    X resolves X's overrides — within the family file a family SPEC named, or
    else within the one family whose [nb.*.models.X] tables mention X. A pin
    matching no family, or several, resolves to no model scope at all and says
    nothing about it: a pin is metadata about which model the definition
    dispatches on, not a request for tuning (a CLI model SPEC, which IS a
    request, still errors when it matches nothing — see resolve_flavor).

    An output with no pin takes `cli_model` instead — the export flavor, set
    only when the SPEC named a model. `family_given` says the SPEC named the
    family directly, which confines pin resolution to it; a model SPEC implies
    its family rather than naming it, and leaves pins free to imply their own.
    The loaded family's family-wide text applies to every output either way.
    """
    wide = family_scope(entries or {})
    if family_given:
        sources = [entries or {}]
    else:
        # A pin can imply its own family exactly as a model SPEC does, so
        # every family file is a candidate. Loading them validates their
        # schema as a side effect, which is why a malformed family file is an
        # error even for a render that ends up using none of them.
        sources = [load_family(path) for path in sorted(models_dir.glob(f"*{FAMILY_SUFFIX}"))]
    owner: dict[str, dict[str, dict] | None] = {}
    for source in sources:
        for model in {model for entry in source.values() for model in entry.get("models", {})}:
            # Mentioned by two families: ambiguous, and therefore nothing.
            owner[model] = source if model not in owner else None

    def resolve(pin: str | None) -> NBMap:
        if pin is None:
            return resolve_nb(entries or {}, cli_model)
        source = owner.get(pin)
        return dict(wide) if source is None else {**wide, **model_scope(source, pin)}

    return resolve


def as_resolver(nb: NBSource) -> Callable[[str | None], NBMap | None]:
    """Normalize a render call's `nb` argument to a per-pin resolver.

    A resolver passes through; a plain map (or None) is render-wide — every
    output resolves to it, pinned or not.
    """
    return nb if callable(nb) else lambda pin: nb


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
        raise TemplateError(f"chunk '{name}' requires variant= (one of {sorted(variants)})")
    if variant not in variants:
        raise TemplateError(f"chunk '{name}' has no variant '{variant}' (one of {sorted(variants)})")
    return variants[variant]


def render(
    text: str,
    chunks: dict[str, dict],
    scope: dict[str, str],
    nb: NBMap | None = None,
    depth: int = 0,
) -> str:
    """Expand every marker in `text`. `scope` binds placeholder arguments;
    `nb` is one output's resolved anchor -> (text, scope-label) map (None or
    missing anchor: the @@nb@@ marker expands to nothing). Per-output
    resolution happens above this, in render_output."""
    if depth > MAX_EXPANSION_DEPTH:
        raise TemplateError("marker expansion exceeded maximum depth (cycle?)")

    def expand(match: re.Match[str]) -> str:
        name = match.group(1)
        args = dict(ARG.findall(match.group(2)))
        if name == NB_MARKER:
            anchor = args.pop("name", None)
            if not anchor or args:
                raise TemplateError(f'@@{NB_MARKER}@@ takes exactly one argument: name="<anchor>"')
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


def frontmatter_pin(text: str) -> str | None:
    """The output's own model pin: the `model:` value in its rendered
    frontmatter, or None when it declares none (the generated commands and the
    participant contract, today). Read from the rendered text because a pin may
    arrive as an outputs-table parameter rather than a literal line."""
    front = frontmatter_of(text)
    if front is None:
        return None
    match = FRONTMATTER_PIN.search(front)
    return match.group(1) if match else None


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


def render_output(
    body: str,
    chunks: dict[str, dict],
    params: dict[str, str],
    resolve: Callable[[str | None], NBMap | None],
) -> str:
    """Render one declared output, its NBs resolved against its own model pin.

    The pin lives in the output's rendered frontmatter, so it can only be read
    from a render: the body is rendered once under the unpinned resolution to
    read it, and re-rendered only when the pin resolves to a different NB set.
    NB anchors are authored in prompt bodies, never in frontmatter, so the pin
    the first pass reads is the pin the second pass renders under.
    """
    unpinned = resolve(None)
    text = render(body, chunks, params, unpinned)
    pin = frontmatter_pin(text)
    if pin is None:
        return text
    nb = resolve(pin)
    return text if nb == unpinned else render(body, chunks, params, nb)


def render_template(
    path: Path,
    chunks: dict[str, dict],
    out_dir: Path,
    *,
    surface: str,
    nb: NBSource = None,
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
    resolve = as_resolver(nb)
    rendered = []
    for name, params in sorted(outputs.items()):
        text = render_output(body, chunks, params, resolve)
        if text.startswith("---\n"):
            # YAML comments: valid frontmatter, dropped by every parser, and
            # outside the body that becomes the prompt.
            rest = text[4:]
        elif surface == COMMAND_SURFACE:
            rest = "---\n" + text
        else:
            raise TemplateError(f"{rel(path)}: agent template must open with YAML frontmatter")
        stamp = banner(path, body_hash=sha256_text(rest), tuning=tuning)
        rendered.append((out_dir / f"{name}.md", "---\n" + stamp + "\n" + rest))
    return rendered


def template_targets(
    smap: dict[str, tuple[Path, Path]],
    globs: GlobMap | None = None,
) -> list[tuple[str, Path, Path]]:
    """(surface name, template path, output directory) for every template.

    Discovery recurses through each surface's template tree (an absent tree is
    tolerated), and a template's relative subpath is mirrored into its surface:
    templates/agents/mad/participant-contract.md.tmpl has output directory
    agents/mad/. Placement is declared by the filesystem and nothing else.

    `globs` drops a template none of whose declared outputs are selected; a
    partially selected one stays, and its unselected outputs are filtered out
    per output where they are rendered.
    """
    found: list[tuple[str, Path, Path]] = []
    for name, (template_dir, out_dir) in smap.items():
        if not template_dir.is_dir():
            continue
        for template in template_dir.rglob(f"*{TEMPLATE_SUFFIX}"):
            subpath = template.parent.relative_to(template_dir)
            mirrored = out_dir / subpath
            if globs and not any(
                selected(name, output_key(mirrored / f"{output}.md", out_dir), globs)
                for output in split_outputs(template)[0]
            ):
                continue
            found.append((name, template, mirrored))
    return sorted(found, key=lambda target: rel(target[1]))


def templates(smap: dict[str, tuple[Path, Path]]) -> list[Path]:
    """Every template in every surface's template tree, discovered recursively."""
    return [template for _, template, _ in template_targets(smap)]


def declared_outputs(smap: dict[str, tuple[Path, Path]]) -> dict[str, set[str]]:
    """Map template path (repo-relative) -> the output paths it declares."""
    outputs = {}
    for _, template, out_dir in template_targets(smap):
        outputs[rel(template)] = {rel(out_dir / f"{name}.md") for name in split_outputs(template)[0]}
    return outputs


def check_banner_claims(smap: dict[str, tuple[Path, Path]], globs: GlobMap | None = None) -> list[str]:
    """Verify every definition carrying a banner has the template it names.

    Enrollment runs template -> definition, so iterating templates cannot see a
    definition whose banner points at a template that no longer exists — a
    stale output left by a deleted or renamed template, outside every other
    check and drifting silently. This walks the claims the other way, across
    both deployed surfaces (agents/ and commands/) and recursively through
    their subdirectories, since outputs mirror nested template paths.

    `globs` narrows the WALK, never the claim map: a selected file's banner is
    still checked against everything every template declares.
    """
    errors = []
    outputs = declared_outputs(smap)
    for surface, (_, out_dir) in smap.items():
        for path in sorted(out_dir.rglob("*.md")):
            if not selected(surface, output_key(path, out_dir), globs):
                continue
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
                owner = next((t for t, outs in outputs.items() if rel(path) in outs), None)
                errors.append(
                    f"MISLABELED  {rel(path)} is banner-marked as generated from "
                    f"{claimed}, which does not declare it" + (f" — its template is {owner}" if owner else "")
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
    """Copy the extant definition's CONTENT aside before it is overwritten.

    Copy, not rename: the subsequent write goes through the original inode, so
    the live definition keeps its identity, owner and mode no matter who runs
    the tool. Renaming would hand the original inode to the disposable backup
    and leave the tracked file owned by whoever regenerated it.

    Content only — the .bak is a recovery artifact, not a mirror. It is a new
    file this run owns, with this run's default mode and its own timestamps;
    cloning the original's metadata would need ownership of a file the runner
    may not own, and would buy nothing, since nothing in this system reads a
    timestamp (integrity is the banner's content hash).
    """
    backup = next_backup(target)
    backup.write_bytes(target.read_bytes())
    return backup


def all_renders(
    chunks: dict[str, dict],
    smap: dict[str, tuple[Path, Path]],
    nb: NBSource = None,
    tuning: tuple[Path, str | None] | None = None,
    globs: GlobMap | None = None,
) -> list[tuple[Path, str]]:
    """Every (target, rendered text) pair across every template, sorted — the
    ones `globs` selects, when a selection is in force."""
    pairs = []
    for surface, template, out_dir in template_targets(smap, globs):
        surface_root = smap[surface][1]
        pairs.extend(
            pair
            for pair in render_template(template, chunks, out_dir, surface=surface, nb=nb, tuning=tuning)
            if selected(surface, output_key(pair[0], surface_root), globs)
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
        if not (resolved.is_dir() and resolved != root and resolved.is_relative_to(root)):
            raise TemplateError(
                f"output directory '{out_dir}' is not an existing directory "
                f"inside the repository — refusing to write"
            )


def report_nb(
    entries: dict[str, dict],
    nb: NBMap,
    tuning: tuple[Path, str | None],
) -> None:
    """State which anchors the family file filled, and from which scope. Under
    a model SPEC, that is the resolution the flavor gives an UNPINNED output; a
    pinned one resolves its own model scope (report_model_flavor accounts for
    which outputs are which)."""
    family, model = tuning
    print()
    print(f"NB anchors from {rel(family)}" + (f" (model {model}, as unpinned outputs resolve it)" if model else ""))
    print("=" * 60)
    for anchor in sorted(entries):
        scope = nb[anchor][1] if anchor in nb else "unfilled (no scope matched)"
        print(f"  {anchor:<28} {scope}")


def report_model_flavor(model: str, family: Path, pairs: list[tuple[Path, str]]) -> None:
    """Account for where a model SPEC landed. It is the export flavor: it tunes
    the outputs carrying no frontmatter pin and is ignored by the rest. Said out
    loud whenever the SPEC named a model — a flavored render that reached
    nothing must not read like one that worked. A family SPEC asked for no model
    scope on unpinned outputs, so it has no reach to account for and prints
    nothing here."""
    pinned = sum(1 for _, text in pairs if frontmatter_pin(text) is not None)
    unpinned = len(pairs) - pinned
    asked = f"--model-family {model} (model in family {rel(family)})"
    print()
    if unpinned:
        print(
            f"{asked}: applied to {unpinned} unpinned output(s); "
            f"skipped {pinned} pinned output(s) (pins own their tuning)"
        )
    else:
        print(
            f"{asked}: applied to 0 unpinned output(s) — all "
            f"{pinned} output(s) carry a frontmatter pin (pins own their tuning)"
        )


def generate(
    chunks: dict[str, dict],
    smap: dict[str, tuple[Path, Path]],
    *,
    nb: NBSource = None,
    tuning: tuple[Path, str | None] | None = None,
    globs: GlobMap | None = None,
    explicit_root: bool = False,
    verbose: bool = False,
) -> bool:
    clean = True
    unchanged = 0
    print("Generating definitions")
    print("=" * 60)
    found = template_targets(smap, globs)
    if not found:
        print(f"  ERROR      no templates found under {rel(TEMPLATES_DIR)}/")
        return False

    pairs = all_renders(chunks, smap, nb, tuning, globs)
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
    nb: NBSource = None,
    tuning: tuple[Path, str | None] | None = None,
    globs: GlobMap | None = None,
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

    for target, rendered in all_renders(chunks, smap, nb, tuning, globs):
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
    claim_errors = check_banner_claims(smap, globs)
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

# The !INSTALLED! banner: what a copied file says about itself. Deterministic
# text — nothing dated, nothing per-run — so an unchanged source re-installs to
# the identical bytes.
INSTALLED_NOTICE = "!INSTALLED! from the agents repo — do not edit in place; " "edit the source repo and re-install."
# Filetypes whose comment syntax can carry the banner as `#` lines. A suffix
# absent here and not .md admits no comment we can rely on (JSON is the case
# in point), so its file is copied verbatim and the install reports it.
HASH_COMMENT_SUFFIXES = frozenset({".py", ".sh", ".toml", ".mk", ".just"})
MARKDOWN_SUFFIX = ".md"
# The only mode bits an install ever sets, and only on a file it just created:
# an executable source (the liaison shell tools) must land runnable. An update
# writes in place and inherits whatever mode the target already carries.
EXEC_BITS = stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH


def bannerable(source: Path) -> bool:
    """Does this file's type admit a comment the !INSTALLED! banner can live
    in? (Whether it *needs* one is install_content's question.)"""
    return source.suffix == MARKDOWN_SUFFIX or source.suffix in HASH_COMMENT_SUFFIXES


def stamp_installed(text: str, *, style: str) -> str:
    """`text` with an !INSTALLED! banner stamped in, in `style`'s comment
    syntax, above the bytes its !BODY-SHA256! line covers.

    Four styles, one block: "frontmatter" opens the file's own YAML block and
    injects at its top (a frontmatter reader drops the comment lines, so the
    banner never reaches the prompt); "bare-frontmatter" emits a minimal block
    holding only the banner above a file that has none, exactly as
    render_template does for a frontmatter-less command template; "html" wraps
    the block in an HTML comment above content that must not gain frontmatter;
    "hash" puts it at the top of the file, below a shebang when there is one.
    The hashed body is everything after the block in every case, so
    banner_body / body_untouched read either banner kind without knowing which
    it met.
    """
    prefix = ""
    if style in ("frontmatter", "bare-frontmatter"):
        prefix = "---\n"
        body = text[len("---\n") :] if style == "frontmatter" else "---\n" + text
        opening, closing = "#\n", "#\n"
    elif style == "html":
        body = text
        opening, closing = "<!--\n", "-->\n"
    else:
        body = text
        if text.startswith("#!"):
            shebang, _, body = text.partition("\n")
            prefix = shebang + "\n"
        opening, closing = "#\n", "#\n"
    return f"{prefix}{opening}# {INSTALLED_NOTICE}\n" f"# !BODY-SHA256! {sha256_text(body)}\n{closing}{body}"


def install_content(source: Path, *, surface: str) -> str | None:
    """The text to install for a copied file — its source, banner stamped in —
    or None when the file travels verbatim: a generated definition (its own
    !GENERATED! banner already forbids in-place edits and names the real edit
    path), or a filetype with no comment syntax to carry a banner.

    `surface` decides where a markdown file with no frontmatter of its own puts
    the banner, on the same split render_template already makes: a command is
    given a minimal frontmatter block (its first BODY line is the description
    Claude Code lists it by, so nothing may displace it), while supporting
    material under agents/ takes an HTML comment instead — frontmatter there
    would make a methodology topic look extractable as a definition body, which
    SPEC.md's Guest-Extraction Contract requires it not to be.
    """
    if not bannerable(source):
        return None
    text = source.read_text(encoding="utf-8")
    if source.suffix != MARKDOWN_SUFFIX:
        return stamp_installed(text, style="hash")
    if banner_claim(text) is not None:
        return None
    if text.startswith("---\n"):
        style = "frontmatter"
    else:
        style = "bare-frontmatter" if surface == COMMAND_SURFACE else "html"
    return stamp_installed(text, style=style)


def provably_ours(extant: bytes) -> bool:
    """Do these extant bytes still hash to their own banner's claim? Bytes that
    are not utf-8 text carry no banner and prove nothing."""
    try:
        return body_untouched(extant.decode("utf-8"))
    except UnicodeDecodeError:
        return False


def install_file(source: Path, target: Path, content: str | None) -> str:
    """Write one copied file, under the same per-target safety generation uses.

    Returns the outcome — "unchanged", "written", or "backed up" — where
    "backed up" means the extant target was not provably this tool's output
    (its body did not hash to its own banner's claim, or it carried no banner
    at all) and was copied aside before being replaced. The replacement itself
    is never in question: the tree is an artifact.

    The extant target is read ONCE and every verdict is reached over those bytes
    in memory; the write that follows goes straight through the target's own
    inode, so an update never touches ownership, mode, or timestamps — see the
    content-write paragraph in the module docstring for why that matters in a
    tree whose files another user installed. Only a file created by this call is
    chmodded, and only to carry the source's executable bit.

    Bytes throughout: the copy set includes filetypes this tool never decodes
    (a stamped file's content is already utf-8 text, encoded here), and both the
    comparison and the write have to be byte-exact.
    """
    desired = source.read_bytes() if content is None else content.encode("utf-8")
    if not target.exists():
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(desired)
        if source.stat().st_mode & EXEC_BITS:
            target.chmod(target.stat().st_mode | EXEC_BITS)
        return "written"

    extant = target.read_bytes()
    if extant == desired:
        return "unchanged"
    outcome = "written"
    if not provably_ours(extant):
        back_up(target)
        outcome = "backed up"
    # In place: write_bytes opens the extant inode "wb" and truncates it.
    target.write_bytes(desired)
    return outcome


def excluded_from_install(relpath: Path) -> bool:
    """Is this surface-relative path on the install exclusion list?"""
    return (
        bool(INSTALL_EXCLUDED_DIRS.intersection(relpath.parts[:-1]))
        or relpath.name in INSTALL_EXCLUDED_NAMES
        or relpath.suffix == INSTALL_EXCLUDED_SUFFIX
    )


def install_pairs(smap: dict[str, tuple[Path, Path]], *, source_root: Path = REPO_ROOT) -> list[tuple[str, Path, Path]]:
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
            found.append(((Path(surface) / relpath).as_posix(), source, out_dir / relpath))
    return found


def install(
    chunks: dict[str, dict],
    smap: dict[str, tuple[Path, Path]],
    *,
    root: Path,
    nb: NBSource = None,
    tuning: tuple[Path, str | None] | None = None,
    source_root: Path = REPO_ROOT,
    verbose: bool = False,
) -> bool:
    """Install the full product into `root` (a consuming project's .claude).

    A recursive copy of the deployed surfaces under `source_root` (this
    repository, in every real invocation), minus the exclusion list, with an
    !INSTALLED! banner stamped into every copied file whose type admits one.
    The copied definitions are already their own base renders, so an untuned
    install renders nothing and merely checks the result; a tuned install runs
    the ordinary generation pass over the copy, which rewrites what the flavor
    changes without leaving backups behind (freshly copied definitions are
    provably untouched output).
    """
    pairs = install_pairs(smap, source_root=source_root)
    print("Installing deployed surfaces")
    print("=" * 60)
    landed: dict[str, int] = {}
    outcomes: dict[str, int] = {}
    skipped: list[str] = []
    for key, source, target in pairs:
        surface = key.split("/", 1)[0]
        content = install_content(source, surface=surface)
        if content is None and not bannerable(source):
            skipped.append(key)
        outcome = install_file(source, target, content)
        outcomes[outcome] = outcomes.get(outcome, 0) + 1
        landed[surface] = landed.get(surface, 0) + 1
        if verbose:
            print(f"  {outcome:<10} {key}")
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
        ok = generate(chunks, smap, nb=nb, tuning=tuning, explicit_root=True, verbose=verbose)
        verdict = f"rendered {len(all_renders(chunks, smap, nb, tuning))}"

    claim = None if tuning is None else (rel(tuning[0]), tuning[1])
    backups = outcomes.get("backed up", 0)
    print()
    print(
        f"install: copied {len(pairs)} "
        f"({outcomes.get('unchanged', 0)} already current), "
        f"{verdict}; {describe_tuning(claim)}"
    )
    print(
        f"backups: {backups} extant file(s) copied aside as *.bak before being "
        f"overwritten — an installed tree is an artifact, so its *.bak files "
        f"are yours to delete."
    )
    if skipped:
        print(f"unbannered: {len(skipped)} file(s) whose type admits no comment — " + ", ".join(skipped))
    print(
        "the installed tree is an artifact — re-install to update it; local "
        "edits to installed files are replaced, not preserved."
    )
    return ok


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Generate and check the generated agent/command definitions.",
        # Exact flag names only. Prefix abbreviation would silently keep the
        # retired --model alive as an alias for --model-family, whose SPEC
        # means something else entirely — the one collapse 2.0.0 exists to make
        # would land as a wrong-argument bug instead of an unknown-flag error.
        allow_abbrev=False,
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
        "caches, stamping each copied file with an !INSTALLED! banner. A "
        "flavor (--model-family SPEC) adds a render pass over the copy. "
        "Implies --output-dir ROOT",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
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
        # No default: the selection globs imply their surfaces, so combining
        # the two flags has to be distinguishable from not passing this one.
        help="which surface to render or check (default: both). Not combinable "
        "with --agent-glob/--command-glob, which imply their surfaces",
    )
    parser.add_argument(
        "--agent-glob",
        metavar="PATTERNS",
        help="restrict the run to the agents outputs matching PATTERNS — one "
        'or more fnmatch patterns joined by "|", matched against the '
        "surface-relative output path without its .md suffix "
        '(--agent-glob "*app-expert*|*-coder*"). Implies --surfaces agents '
        "unless --command-glob is given too; a pattern matching nothing is an "
        "error",
    )
    parser.add_argument(
        "--command-glob",
        metavar="PATTERNS",
        help="the same, over the commands surface",
    )
    parser.add_argument(
        "--model-family",
        metavar="SPEC",
        help="the model-tuning flag: SPEC is a family-file path, a bare family "
        "name resolved against templates/models/, or a bare model name found in "
        "exactly one family's [nb.*.models.*] tables — which additionally makes "
        "that model the export flavor for the outputs carrying no frontmatter "
        "model: pin (a pinned output resolves its own and ignores it). See "
        "templates/models/README.md",
    )
    args = parser.parse_args()
    if args.install is not None and (args.generate or args.output_dir is not None):
        parser.error("--install ROOT already implies --generate --output-dir ROOT — " "pass it alone")
    output_root = args.install if args.install is not None else args.output_dir

    globs: GlobMap = {
        surface: split_globs(spec)
        for surface, spec in (("agents", args.agent_glob), ("commands", args.command_glob))
        if spec is not None
    }
    if globs:
        if args.surfaces is not None:
            parser.error(
                "--agent-glob/--command-glob already select their surface(s) — " "drop --surfaces, which they subsume"
            )
        if args.install is not None:
            parser.error(
                "--install does not accept selection globs: an install delivers "
                "the whole product, and a partial install is a future feature. "
                "Use the globs with --generate or check instead"
            )
    # The globs imply the surfaces they cover; without them, --surfaces (or its
    # default) does.
    surfaces = ("both" if len(globs) > 1 else next(iter(globs))) if globs else (args.surfaces or "both")

    try:
        smap = surface_map(output_root=output_root, surfaces=surfaces)
        if globs:
            validate_selection(output_keys(smap), globs)
        chunks = load_chunks()
        nb = tuning = entries = family = model = None
        if args.model_family is not None:
            family, model = resolve_flavor(args.model_family)
            if model is not None:
                print(f"--model-family {model} resolves to model {model} in family {rel(family)}")
            entries = load_family(family)
            # Anchors are collected across ALL templates and chunks, not just
            # the --surfaces selection, so a filtered render never miscalls a
            # real anchor unknown.
            validate_family_anchors(entries, collect_anchors(chunks, templates(surface_map())))
            tuning = (family, model)
        # Always per-pin: an output's own frontmatter model: pin owns its model
        # scope, whether or not this invocation asked for a flavor. A model SPEC
        # implies its family rather than naming it, so pins stay free to imply
        # their own — exactly as they did under the retired bare --model.
        nb = per_pin_resolver(
            entries,
            cli_model=model,
            family_given=args.model_family is not None and model is None,
        )
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
                report_nb(entries, nb(None), tuning)
        elif args.generate:
            ok = generate(
                chunks,
                smap,
                nb=nb,
                tuning=tuning,
                globs=globs,
                explicit_root=output_root is not None,
                verbose=args.verbose,
            )
            if entries is not None:
                report_nb(entries, nb(None), tuning)
        else:
            ok = check(
                chunks,
                smap,
                nb=nb,
                tuning=tuning,
                globs=globs,
                verbose=args.verbose,
                show_diff=not args.no_diff,
            )
        if globs:
            report_selection(output_keys(smap), globs)
        if model is not None:
            # A model SPEC's reach is accounted for in every mode: it is the one
            # ask whose effect depends on what each output declares. A family
            # SPEC asked for none, and prints nothing.
            report_model_flavor(model, family, all_renders(chunks, smap, nb, tuning, globs))
    except TemplateError as exc:
        print(f"error: {exc}", file=sys.stderr)
        sys.exit(2)

    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
