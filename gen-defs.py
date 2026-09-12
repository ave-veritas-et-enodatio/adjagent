#!/usr/bin/env python3
"""
Generator and consistency checker for the generated definitions in this
repository — agent definitions and slash-command definitions alike.

Templates live in two sibling trees under templates/, and a template's surface
tree routes its output into the matching deployed surface beneath the output
root the invocation names (ROOT below; every verb takes it as its positional
argument, and none of them defaults — this repository keeps no rendered tree
of its own for a render to default into):

    templates/agents/<name>.md.tmpl    + templates/shared-chunks.toml -> ROOT/agents/<name>.md
    templates/commands/<name>.md.tmpl  + templates/shared-chunks.toml -> ROOT/commands/<name>.md

Discovery is RECURSIVE within each surface tree, and a template's path is
MIRRORED into its surface — the relative subpath of the template is the
relative subpath of its output. The filesystem is the whole declaration;
there is no metadata key for placement:

    templates/agents/mad/participant-contract.md.tmpl -> ROOT/agents/mad/participant-contract.md

Every other mechanism — chunks, variants, markers, overlay anchors,
multi-output fences, banners, write safety, numbered backups, and both checks
— applies unchanged at a nested path. Check 2 walks *.md RECURSIVELY under each
checked surface, so a banner stranded at a nested path is caught exactly as a
top-level one is. The surface directories under ROOT, and the mirrored
subdirectories beneath them, are created as needed.

templates/shared-chunks.toml is the single chunk source for both template
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
    name: @!arg.name!@
    model: @!arg.model!@
    ---
    <one body, rendered identically into every output>

Each declared key is readable in the body as @!arg.<key>!@, plus @!arg.name!@
bound to the output's own name. The fence is where those values are BOUND, so
its keys are bare; the prefix goes where one is CONSUMED (Marker syntax,
below). Bodies are therefore identical by construction rather than by
maintenance discipline. A template with no such block renders a single
definition named after the template, at the template's mirrored path. Chunks,
variants, markers, wrapping, multi-output declarations, write safety, backups,
and the render-identity check apply identically to both template types.

Usage — four verbs, one required, no default mode. `just generate`,
`just check`, `just install` and `just install-claude-md` are the sole
sanctioned entry points (see ARCHITECTURE.md); they wrap:

    python3 gen-defs.py check R              # check the tree under R
    python3 gen-defs.py generate R           # render templates into R
    python3 gen-defs.py install R            # full-product install into R
    python3 gen-defs.py install-claude-md D  # merge the operator baseline into D

The first three name their output root positionally, and none of them
defaults, because there is no in-repository tree left to default to; the
fourth names the operator's own CLAUDE.md and is described under
"Operator-config integration" below. Each verb declares only the flags it can
act on: what a verb cannot do is unrepresentable on its command line rather
than refused after the fact.

Flags common to generate, check and install:

    --family NAME           the model family whose tuning applies: a bare
                            family name resolved against templates/family/
                            (default: claude), or a path to a family file.
                            Bare MODEL names are not valid values.
    --model-tier-map SPEC   override the tier -> family member map the family
                            declares: comma-separated tier=member pairs, or
                            all=member. Named tiers mask only themselves.
    --model-pin-map SPEC    override the tier -> rendered pin map: same syntax,
                            over DEFAULT_PIN_MAP below. Pin text is always in
                            the claude model namespace.
    --verbose, -v           list every file, not just the exceptions.

`generate` and `check` additionally take:

    --surfaces WHICH        agents | commands | both (default both): which
                            surface to render or check. Cannot be combined
                            with the selection globs below, which imply it.
    --agent-glob PATTERNS   restrict the run to the agents outputs matching
                            PATTERNS: one or more fnmatch patterns joined by
                            "|" ("*app-expert*|*-coder*"). See below.
    --command-glob PATTERNS the same, over the commands surface.

and `check` alone takes:

    --no-diff               report drift without printing the diff.

`install` takes none of those four: it delivers the product entire, and a
partial install is a future feature.

ROOT is asserted to be an existing directory and is otherwise unconstrained —
inside this repository or anywhere else; the surface subdirectories are created
under it as needed. The full per-target safety table below (banner detection,
numbered backups, refusal of bannerless files) applies identically wherever it
lands.

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
with a glob and combining them is an argparse error. Narrowing an install is
not an error but an impossibility: `install` declares neither the globs nor
--surfaces, so an install always delivers the whole product.

Selection filters PER OUTPUT, not per template: a glob matching one output of
a multi-output template renders or checks exactly that one, and the template's
other outputs are left untouched on disk and reported as nothing at all.

A pattern matching zero outputs — any single "|"-segment, and therefore the
set — is a hard error naming the pattern and listing what the surface does
declare. A selection that silently selected nothing would report exactly like
a clean run. What was selected is stated in the run report, per surface:

    agents: 12 of 24 outputs selected by --agent-glob

Selection composes orthogonally with everything else: overlay anchors and tier
resolution, banners and tuning claims, the write-safety table, and both checks
all apply to the selected outputs exactly as they do to a full run.

Tier tokens and the two maps:

A definition declares its capability TIER at its pin site, as one of five
markers in the `dyn.` namespace — the invocation's own parameters:

    model: @!dyn.tier-highest!@ | @!dyn.tier-high!@ | @!dyn.tier-medium!@ |
           @!dyn.tier-low!@     | @!dyn.tier-lowest!@

They are bound at load time to `pin_map[<tier>]`, in a dynamic table threaded
through every expansion, so they expand wherever a marker does — template
bodies, chunk bodies, variants, defaults, and overlay text alike. Nothing is
reserved against the chunk table for them: a [chunks.tier-low] table is an
ordinary chunk, reachable as the bare @!tier-low!@, because the namespace is
what separates the two.

Two independent maps, each TOTAL over the five tiers on every render:

    tier map   tier -> FAMILY MEMBER. Declared by the family file's required
               [tiers] table; --model-tier-map masks the tiers it names. It
               selects whose [family.*.models.<member>] overrides a definition
               is tuned against, and never reaches rendered text.
    pin map    tier -> RENDERED PIN TEXT, always a claude-legal model name.
               Defaults to DEFAULT_PIN_MAP below; --model-pin-map masks the
               tiers it names. It is the SOLE source of the @!dyn.tier-*!@
               tokens.

The two are independent on purpose: what a definition is TUNED for and what it
DISPATCHES on are separately chosen, so their divergence is disclosed rather
than prevented. A tier-map value is NOT validated against the family's overlay
tables — naming a member the family declares no override for is the legal
STOCK state below, never an error. A pin-map value is not validated at all:
nothing in this repository owns the set of legal claude aliases, so the report
echo and the banner are the whole safety story there. That is an accepted risk,
stated so it does not get "fixed" into a gate.

Merge semantics, identical for both flags: start from the defaults; `all=V`
overwrites all five; then each named tier is applied. So `all=haiku,high=opus`
is haiku everywhere but high, whichever order the two appear in, and a named
tier masks ONLY itself — unnamed tiers keep their default. A key that is
neither one of the five tiers nor `all`, a duplicate key, and a malformed pair
are each hard errors. Both merged maps are total by construction: a tier
holding no member, or no pin, is unrepresentable rather than an error branch.

Per-output tier discovery renders a template's body TWICE. The first pass binds
the tier tokens to a marker-free sentinel (`tier:high`), so reading the pin out
of that render's frontmatter yields the output's own tier; the second renders
for real, under the overlay set that tier resolves. The second pass is
UNCONDITIONAL — a tier token in a body or a chunk would otherwise ship sentinel
text into a system prompt. An output with no pin site at all (the participant
contract, the generated commands) declares no tier and takes family-wide
overlay text only.

A pin site that resolves NO tier — a literal `model: opus` where a token
belongs — is a hard error naming the output and its pin. It is the one failure
the render path cannot afford to tolerate quietly: a literal pin renders the
same bytes as the token that replaced it and silently loses the definition's
model scope, so a template landing with one would be tuned against nothing and
nothing would say so.

Model tuning — overlay anchors and family files:

    @!fam.<key>!@

is a marker usable in template bodies and chunk bodies: text layered over base
text, present or absent depending on which source file is loaded. `fam` is an
OVERLAY NAMESPACE — a prefix naming the source that fills the anchor, filled by
a family file's [family.<key>] tables, and the only one registered
(OVERLAY_NAMESPACES). Registering a second — a harness file, say — is a name in
that tuple plus the loader that reads it; resolution keys on the marker's full
spelling, so nothing downstream learns the new name. A marker whose namespace
is not registered is a hard error wherever it is read: an anchor no loaded
source fills renders as NOTHING by design, so a typo'd namespace has no other
way to announce itself.

With a loaded family file carrying no entry for an anchor, it expands to
nothing at all — a base render is byte-identical whether or not the anchor
exists. A family file (templates/family/<family>.toml) declares its tier map
and fills `fam.*` anchors:

    [tiers]
    highest = "fable"                     # all five tiers, or the file does
    high    = "opus"                      # not load — see below
    medium  = "sonnet"
    low     = "haiku"
    lowest  = "haiku"

    [family.<key>]
    text = "..."                          # fills fam.<key> family-wide

    [family.<key>.models.<member>]
    text = "..."                          # overrides the family text for that
                                          # family member

The table's key IS the anchor key: [family.gap-aversion] fills
@!fam.gap-aversion!@. The table name stays `family` — it is where the text is
AUTHORED, and SPEC.md names it — while `fam` is the namespace a template
CONSUMES it through.

Resolution, not accumulation: AT MOST ONE overlay renders per anchor — the
model scope wins over the family scope.

Family selection — what one --family NAME means:

    1. path-shaped   — contains a path separator or ends in .toml: taken as a
                       path to a family file, unresolved and unvalidated here
                       (load_family owns the existence error).
    2. family name   — <NAME>.toml under templates/family/.

Anything else is an error listing the available families. A bare MODEL name is
not a family name, and there is no third resolution step behind it.

The [tiers] table is REQUIRED and TOTAL. A family file that omits it, drops a
tier, or names a key outside the five fails to LOAD rather than falling back to
a partial map, to another family's members, or to the pin map: there is no
fallback anywhere in this resolution, and the default FAMILY is a selection,
not a fallback value. A family file may still carry zero [family.*] tables — a
family name is reservable before an observed failure motivates an entry — but
it needs its five-line [tiers] table to do so.

Two render states, plus one config error:

    Tuned    the tier's member has [family.<key>.models.<member>] tables: model
             scope wins over family-wide scope, per anchor. Named once per run,
             in the run's one notice about member scope.
    Stock    the tier's member has ZERO such tables anywhere in the family.
             Legal and unreported — silence is the stock state — and recorded
             in the banner's `stock=` field. A member with a table for one
             anchor and not another is NOT stock — that anchor simply falls
             back to family-wide text.
    (error)  [tiers] absent, incomplete, or carrying an unknown key.

A member named by a [family.*.models.<member>] table that no tier reaches — in
neither the family's own [tiers] nor the effective tier map — is a hard error:
the table could never render, which is exactly the class
validate_family_anchors already rejects for anchor names. Checking the UNION is
what lets a table be authored for a member reachable only via
--model-tier-map without failing a default run.

Family scope is unchanged by any of that: a loaded family file's family-wide
text fills its anchors render-wide, for outputs with a tier and outputs with no
pin site alike. Only MODEL-scoped entries require a tier. The banner records
the invocation's whole triple, and tier resolution is a deterministic function
of that triple and the templates, so check reproduces it exactly.

A filled anchor renders its family text VERBATIM in place — no lead-in, no
wrapper, no marker of its own around it, so a family file can restore a passage
of contract prose as readily as it can add a corrective note; an author wanting
a lead-in writes one into their text. Neither the family nor the member name
appears in rendered output — the family file is the provenance record. The
filled text is itself marker-expanded, so a typo'd chunk reference inside it
fails loudly. A family-file entry naming an anchor that exists in no template
or chunk is a hard error, and a family declaring anchors reports which of them
were filled and from which scope (family vs model). NO name is reserved
anywhere in this: the namespace is what separates an anchor from a chunk, so
`overlay` and `tier-low` are ordinary chunk names now.

Structural invariant: a family file can never replace, suppress, or modify
BASE text — it can only fill anchors that base templates and chunks
deliberately expose. Anchors are authored on demand, when an observed failure
motivates one; they are never pre-sprinkled speculatively. See
templates/family/README.md and README.md ("Variants and platform
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

The hash-verified branch is what keeps routine regeneration — and an install's
render pass over a freshly copied tree — from churning out backups
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

There is no wrong-output-directory-constant guard, because there is no
constant: every run names its ROOT, ROOT is asserted to exist, and the report
states where the files landed. The surface directories under ROOT and the
mirrored subdirectories beneath them come from the template tree rather than
from a constant, and are created as needed.

Full-product install — `install ROOT`:

    python3 gen-defs.py install <project>/.claude [--family NAME]
        [--model-tier-map SPEC] [--model-pin-map SPEC]

An install delivers the whole deployed product in two halves. The shipped
packages (kb_tools/, liaison_tools/) are a RECURSIVE COPY
into the destinations the table below names, minus one explicit exclusion list
(INSTALL_EXCLUDED_*) — test suites and their fixtures, python/pytest caches,
*.bak safety copies, .DS_Store, and this repository's own project
documentation, which is code's provenance rather than part of it. There is no
inclusion list and no complement computation: a new tool file ships with no
enrollment step. The definitions are RENDERED into ROOT's two surfaces by the
ordinary generation pass — there is no checked-in render to copy, so the copy
half never had them to deliver, and the (family, tier-map, pin-map) triple an
install was given is just the triple that render runs under. `install` declares
neither `--surfaces` nor the selection globs, which would narrow the product to
a partial install.

Where a copied file LANDS is not, however, read off where its source sits. A
file walked from a source-root surface mirrors its surface-relative subpath,
but the shipped code packages travel by an explicit source -> destination table
(SHIPPED_PACKAGES): each row pairs a source directory in this repository with
the ROOT-relative destination it must arrive at. The destination is a CONSUMER
contract — runner snippets already installed in consuming projects name
.claude/agents/kb_tools/..., and every definition body writes its paths
against .claude/agents/... — so the source may be relocated within this
repository and the destination may not follow it. Stating the pairing as data
is what keeps those two facts independent; deriving the destination from
directory placement is what made them the same fact. A row whose source lies
inside a deployed surface is carved out of that surface's own walk and
delivered by its row instead, so every file is emitted exactly once whichever
side of a relocation its source currently sits on.

EVERY install renders, whatever triple it was given: a default-triple install
is one triple among the possible ones, not a special case with a copy behind
it, which is what makes a default install and a tuned one the same code path
and the same guarantee. The render pass receives the effective triple and the
tier resolver the invocation built, so an install can never deliver
definitions tuned differently from what its own banners claim. Every target
that pass meets is freshly copied or provably untouched tool output (see the
banner's body hash below), so it leaves no numbered backups behind. Only a
NON-DEFAULT triple earns a clause on the summary line — a default render is
the non-event report-by-exception exists for.

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
                             dropped by every frontmatter reader, the body
                             extraction the liaisons run included
    *.md without, commands/  a minimal frontmatter block holding only the
                             banner, as render_template already does for a
                             frontmatter-less command template: a command's
                             first BODY line is the description Claude Code
                             lists it by, and nothing may displace it
    *.md without, agents/    an HTML comment block above the content — a
                             shipped package's own documentation must not gain
                             frontmatter, which would present it as a
                             definition
    .py .sh .toml .mk .just  `#` comment lines at the top, below a shebang
    any other suffix         no comment syntax to carry a banner: the file is
                             copied verbatim, and --verbose names it (which
                             files those are follows from their suffix alone,
                             so a clean install does not report it)

Third-party source vendored into a shipped package under a `_vendor/`
directory is exempt whatever its suffix: the banner would assert adjagent
provenance over code we did not write, and tell its reader to edit a source
repository that is not the file's. It is copied verbatim and --verbose names
it as unstamped. A STAMPING carve-out only — vendored code ships like any
other package file, and the exclusion list above is untouched.

A generated definition is exempt: it arrives carrying its own !GENERATED!
banner, which already forbids in-place edits and names the real edit path.
The two banners are one marking scheme — provenance plus a body hash — and
the hash buys the same thing on re-install that it buys on regeneration: a
target whose body still hashes to its own banner is provably ours and is
overwritten silently, while a mismatched or unbannered target is backed up
first. Re-installing an untouched tree is therefore byte-stable: no content
changes and no backups.

The banner is a five-line YAML-comment block naming the source template
(`# !GENERATED! from templates/agents/<name>.md.tmpl ...`) above a `!TUNING!`
line and the body hash:

    #
    # !GENERATED! from <tmpl> and <chunks> — edit those. DO NOT HAND EDIT ...
    # !TUNING! family=<file> seat=<tier|none> member=<member|none>
              tier=<tier map> pin=<pin map> stock=<tiers|none>
    # !BODY-SHA256! <hex>
    #

(one physical line for `!TUNING!`; wrapped here only to fit). `family=` and
the two maps are the render's whole triple, both maps EFFECTIVE — post-merge —
and serialized in TIERS order rather than sorted, so the claim is deterministic
and reads highest-to-lowest. `seat=` and `member=` are this definition's OWN
resolution: the tier its pin site declared and the family member its overlays
resolved against, `none` for an output with no pin site. They are carried
because the pin map is not injective (`low` and `lowest` both default to
haiku), so a definition's tier cannot be recovered by inverting it — recording
the answer is what keeps a definition's tuning readable from the definition
alone. `member=` is redundant with tier[seat] in a well-formed banner, and that
is the point: it is the cheap cross-check catching a banner written under one
tier map and read under another. `stock=` lists the tiers whose mapped member
declares zero [family.*.models.<member>] tables in the loaded family, or `none`.
The banner always lives inside YAML frontmatter, never in the body that becomes
a prompt:

  - An agent template must open with a frontmatter block; the banner is
    injected at its top.
  - A command template whose body opens with a frontmatter block gets the
    banner injected the same way; a command template with no frontmatter gets
    a minimal frontmatter block emitted above its body, containing only the
    banner comment lines, so the banner never lands in the literal prompt
    text.

Its `# !BODY-SHA256! <hex>` line is the sha256 of everything the file
holds AFTER the banner block, exactly as written — the one line both banner
kinds share. It lets any later reader answer a question the banner alone
cannot: is this file still the bytes the tool wrote, or has someone edited it
since? A file whose body hashes to its own claim is provably untouched tool
output — reproducible at will, and therefore safe to overwrite without a
backup (see the safety table above). A pre-1.5.0 banner carries no hash line
and proves nothing, so it is treated as possibly hand-edited.

A definition without a banner is presumed hand-maintained and outside this
tool's remit; if it should be generated, delete it and re-run.

Operator-config integration — `install-claude-md DEST`:

    python3 gen-defs.py install-claude-md ~/.claude/CLAUDE.md
        [--source user-config/INSTALLED_CLAUDE.md]

DEST is a file its operator owns and edits, so this verb MERGES rather than
overwrites, and it never marks the file it writes: CLAUDE.md has no comment
syntax a reading model would not also read, so a region marker would ship into
every session that loads it. Nothing is stored beside the live file either —
the merge base is recovered from git.

The base is the published revision the operator most likely last integrated:
`git log --follow` over the published baseline (the path has been renamed, and
a revision from before the rename is exactly the era a live file may date
from), each revision diffed against the live file, and the candidates ordered
by how many lines differ. The MERGE VALIDATES THE PICK — a base that produces a
clean whole-block merge corroborates itself, and one that conflicts sends the
next candidate forward — so a near-miss in the ordering costs a retry rather
than a wrong merge. A revision equal to the published text is not a candidate:
one side of that merge has no changes at all, so it succeeds against anything
and yields the live file unchanged — an update deciding by itself that it had
nothing to deliver.

A MERGE NEVER INSERTS WHAT IS ALREADY THERE: an insertion whose paragraphs
already sit where it would land says the same thing twice, so it is dropped and
the copy in place — the operator's own bytes — is the one that survives. That is
what makes a second run over a file the first one merged a no-op. The base still
on offer then predates the update the live file now carries, and against it the
operator's edit and the applied insertion beside it read as one changed span, so
the insertion is no longer recognisable as already present by position alone.

Four cases, classified and REPORTED before anything is written:

    no shared        the live file was hand-written and never integrated from
    ancestry         ours: there is nothing to align, so everything below our
                     H1 is APPENDED to theirs and the report says to hand-edit
                     out the duplication and whatever contradicts.
    exact match      the live file IS some published revision, so it carries no
                     local edits and the update applies whole. (A live file
                     matching the CURRENT baseline is the no-op.)
    a clear base     merged. Both sides' changes are taken at PARAGRAPH
    plus local       granularity — a paragraph being a run of non-blank lines —
    edits            so every hunk is bounded by blank lines and adds, drops or
                     replaces whole blocks rather than editing inside one. Two
                     sides changing one paragraph differently is a conflict,
                     and a conflict is the next case.
    anything else    BAIL. The baseline is written beside the live file as
                     incoming.CLAUDE.md — extension LAST, so an editor still
                     reads it as Markdown — and nothing else is touched: no
                     partial merge, no backup, no write to the live file. The
                     exit is nonzero, because the integration did not happen.

WRITE AUTHORITY ENDS AT THE SEAM, and that is what the paragraph unit carries
bytes for. Content this verb did not merge into comes back byte-identical —
their paragraphs, their separators, their whitespace-only lines, their doubled
blanks — because a block is emitted from the document it came from rather than
reconstituted from its text, and an untouched region is copied out of the
operator's file rather than out of the base. Normalizing is right in exactly
two places: the published baseline, which this repository authors, and the
joint where our content meets theirs, where a missing blank line is topped up
and nothing already written is trimmed. A tool that tidies a config it was
asked only to update is a tool people stop trusting.

The report is SECTION-granular rather than a diff: which `##` sections the
update adds, which exist in both and differ, which are the operator's own, and
which already match, each as a list of names, under the base revision and its
date. A line diff is the classification's own evidence, not its output —
nobody hand-integrates from a unified diff.

One rolling backup, not the old recipe's numbered `.bak` files. A write that
changes the live file's bytes — the whole-update, unrelated-append, and merge
cases alike — copies what the file held before the write to a single
`backup.CLAUDE.md` beside it: extension last, the same shape as
`incoming.CLAUDE.md`, so an editor still reads it as Markdown. It is
overwritten on the next such write and left alone by one that changes nothing
— the exact-match no-op keeps none because there is none to keep, and the bail
path never touches the live file at all. The numbered `.bak` files the old
recipe left behind accumulated in a directory the operator owns and bought
nothing; a single rolling file that only appears when there is something to
recover is the fix, not a return to it.

`check` runs two checks, each failing nonzero:

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

Check takes the same ROOT and the same --surfaces / --family /
--model-tier-map / --model-pin-map flags as generate, so a tuned out-of-repo
set gets the identical render-identity and banner-claims validation. The
tuning claim is part of both
checks: check runs under exactly one triple (the one it was invoked with),
renders with it, and requires every target's !TUNING! line to match the one its
own render produces — `seat=` and `member=` included. A target whose recorded
family, either map, seat or member differs is reported MISTUNED naming both
sides, instead of an opaque byte diff; a target whose tuning claim matches but
whose bytes differ is ordinary DRIFT. Validating a tuned set therefore means
invoking check with that set's ROOT and its tuning flags; checking the same
directory under a different triple is expected to fail — that is the mismatch
the banner exists to catch.

Generation is deterministic and idempotent: output depends only on the
template, the shared chunks, and the template's path.

Marker syntax — usable in templates and inside chunk bodies.

A MARKER'S NAMESPACE NAMES THE SOURCE ITS VALUE COMES FROM, and a marker
without one is a chunk. Four sources, four spellings, no precedence between
them: render dispatches on the prefix alone, so a chunk and an argument
sharing a name are two different markers rather than a collision some rule has
to arbitrate.

    @!name!@                     expand chunk "name" — ALWAYS a chunk, and
                                 nothing else
    @!name variant="platform"!@  expand that variant of a multi-variant chunk
    @!name key="value"!@         bind @!arg.key!@ inside the chunk body
    @!name wrap="70"!@           greedy-wrap the expansion to 70 columns
    @!arg.key!@                  the value the call site bound for `key`, or
                                 [chunks.<name>.defaults] where it omitted one
    @!dyn.tier-high!@ …          an invocation parameter — the five tier
                                 tokens, from the PIN map alone (Tier tokens,
                                 above)
    @!fam.gap-aversion!@         model-tuning overlay anchor — expands to
                                 nothing unless the loaded family fills it
                                 (Model tuning, above)

THE PREFIX GOES WHERE A VALUE IS CONSUMED, NEVER WHERE IT IS BOUND. A chunk
marker's `key="value"` argument, a [chunks.<name>.defaults] key, and an
[outputs.<name>] fence key all stay bare: position already says what they are.
It is the marker READING one, inside a body, that carries `arg.`, because
nothing there would otherwise say where the value comes from.

"variant" and "wrap" are reserved argument keys; any other key binds a value
the chunk body reads as @!arg.<key>!@, defaulting to [chunks.<name>.defaults]
when the marker omits it. Argument values cannot contain a double quote. Only
a bare marker takes arguments at all. An unknown chunk name, an unbound
argument, an unknown invocation parameter, and an unregistered namespace are
each errors, so a typo fails loudly rather than shipping into a system prompt
— while an anchor whose namespace IS registered and whose key no loaded source
fills is silence by design, which is exactly why the namespace itself is
policed.

A marker name, a namespace, a family anchor key, and an argument key are one
identifier class — a kebab-case identifier, IDENTIFIER below — built once and
shared by every regex site so they cannot spell it differently and drift apart.
The `.` joining a namespace to a name is deliberately outside that class: it is
the whole of what keeps a namespace from ever being read as a name. A malformed
name fails loudly: `@!write-op-!@` raises "unknown chunk 'write-op-'", naming
the exact string. A malformed argument key, or a malformed namespaced marker
(`@!fam.Gap!@`, `@!fam.a.b!@`), does not parse as a marker at all and ships as
literal text, which the residual-marker guard below refuses at generation
rather than letting it work by accident.
"""

import argparse
import contextlib
import difflib
import fnmatch
import hashlib
import io
import re
import stat
import subprocess
import sys
import tomllib
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import NamedTuple

# Semver, bumped on mechanism changes; history: git log. Not embedded in
# rendered banners — that would churn every generated file on every bump.
__version__ = "5.0.0"

REPO_ROOT = Path(__file__).parent
TEMPLATES_DIR = REPO_ROOT / "templates"
SHARED_CHUNKS = TEMPLATES_DIR / "shared-chunks.toml"
FAMILY_DIR = TEMPLATES_DIR / "family"
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
# The namespace vocabulary: a marker's prefix names the SOURCE its value comes
# from, and a bare marker is a chunk and nothing else. Registering a further
# overlay source — a harness file, say — is a name here plus the loader that
# reads it; render dispatches on the prefix and keys overlays by the marker's
# full spelling, so nothing else has to learn the new name.
ARG_NAMESPACE = "arg"
DYNAMIC_NAMESPACE = "dyn"
FAMILY_NAMESPACE = "fam"
OVERLAY_NAMESPACES = (FAMILY_NAMESPACE,)
NAMESPACES = (ARG_NAMESPACE, DYNAMIC_NAMESPACE, *OVERLAY_NAMESPACES)
# The one identifier class shared by marker names, namespace prefixes, family
# anchor keys, and chunk-argument keys: strict kebab-case — a letter-led
# segment, optionally followed by more letter/digit segments joined by single
# hyphens. No leading digit or hyphen, no trailing or doubled hyphen, no
# underscore. Tight rather than merely typo-proof because these templates are
# increasingly authored by models: a trailing or doubled hyphen is exactly what
# a generator emits without noticing. The `.` a namespace is joined on is
# deliberately NOT in the class — that is what keeps a namespace and a name
# unmistakable for one another.
IDENTIFIER = r"[a-z][a-z0-9]*(?:-[a-z0-9]+)*"
# A family file's anchor key, held to the same class as the marker half that
# consumes it: [family.<key>] fills @!fam.<key>!@.
ANCHOR_KEY = re.compile(IDENTIFIER)

# The five capability tiers, in canonical order — the order every serialization
# of either map uses, so a banner claim is deterministic and reads
# highest-to-lowest rather than alphabetically.
TIERS = ("highest", "high", "medium", "low", "lowest")
DEFAULT_FAMILY = "claude"
# The family file's two top-level tables — its overlay entries and its tier
# table — and the literal a map flag spells "every tier". [family.<key>] is the
# authoring spelling; fam.<key> is the marker that consumes it.
FAMILY_TABLE = "family"
TIERS_TABLE = "tiers"
MAP_ALL = "all"
# The five dynamic names a tier token is spelled with: @!dyn.tier-<tier>!@.
# A chunk named tier-anything is unaffected — the namespace is what separates
# them, so nothing here is reserved against the chunk table.
TIER_TOKEN_PREFIX = "tier-"
# What the discovery pass binds the tier tokens to. Marker-free and
# whitespace-free, so FRONTMATTER_PIN reads `model: tier:high` unchanged.
TIER_SENTINEL = "tier:"
# Tier -> rendered pin text. This default lives here and is changed only by
# --model-pin-map; no family owns it, and it is always claude-legal names.
# Deliberately NOT injective — low and lowest both ship haiku — which is why a
# definition's own tier is recorded in its banner rather than derived.
DEFAULT_PIN_MAP = {"highest": "fable", "high": "opus", "medium": "sonnet", "low": "haiku", "lowest": "haiku"}
# An output's own model pin, read out of its RENDERED frontmatter: the value
# of a `model:` key, however it got there — an outputs-table parameter or a
# literal line. Quotes are optional in YAML and stripped here.
FRONTMATTER_PIN = re.compile(r"""^model:[ \t]*["']?([^"'\s#]+)["']?[ \t]*$""", re.MULTILINE)

# Surface -> the fnmatch patterns selecting that surface's outputs. A surface
# absent from the map is unfiltered, so None and {} both mean "everything".
GlobMap = dict[str, list[str]]
# Marker spelling (fam.<key>) -> (overlay text, the scope it resolved from:
# "family"/"model").
OverlayMap = dict[str, tuple[str, str]]
# What the render path accepts for `overlays`: a per-tier resolver (tier ->
# map), or a plain map applied render-wide, or nothing at all.
OverlaySource = OverlayMap | Callable[[str | None], OverlayMap | None] | None
# Tier -> family member, or tier -> rendered pin text. Total over TIERS in
# every instance the render path ever sees (see effective_map).
TierMap = dict[str, str]
# Invocation-parameter name -> its value: what @!dyn.<name>!@ resolves from.
# Keyed by parameter name (tier-high), not by tier — one table for everything
# the invocation supplies, of which the five tier tokens are today's whole set.
DynamicMap = dict[str, str]

# One marker, whole: an optional namespace prefix, the name, and the quoted
# arguments a bare (chunk) marker may carry. The prefix is a separate group
# because it ROUTES — render dispatches on it and never on the name — and it is
# matched as an IDENTIFIER rather than as an alternation of the registered
# names so that an unregistered one reaches that dispatch and is refused by
# name, instead of failing to parse as a marker at all.
MARKER = re.compile(rf'@!(?:({IDENTIFIER})\.)?({IDENTIFIER})((?:\s+{IDENTIFIER}="[^"]*")*)\s*!@')
ARG = re.compile(rf'({IDENTIFIER})="([^"]*)"')
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
# The banner's tuning claim: the whole triple this definition was rendered
# under, plus its own seat and member. A MACHINE claim — check parses it back
# and compares it field for field, so it is bracket-free, one token per field,
# and must stay stable across versions or every rendered definition reports
# MISTUNED. The run-report echo (report_tuning) is the display form and is
# deliberately a separate serialization.
TUNING_CLAIM = re.compile(
    r"^# !TUNING! family=(\S+) seat=(\S+) member=(\S+) tier=(\S+) pin=(\S+) stock=(\S+)$",
    re.MULTILINE,
)


class TemplateError(Exception):
    """A template or shared-chunks definition is malformed."""


def rel(path: Path) -> str:
    """A path as printed and matched everywhere: relative to the repo root
    when it lies inside it, resolved-absolute otherwise (ROOT and --family may
    point anywhere)."""
    try:
        return path.relative_to(REPO_ROOT).as_posix()
    except ValueError:
        try:
            return path.resolve().relative_to(REPO_ROOT.resolve()).as_posix()
        except ValueError:
            return path.resolve().as_posix()


def surface_map(
    output_root: Path,
    *,
    templates_root: Path = TEMPLATES_DIR,
    surfaces: str = "both",
) -> dict[str, tuple[Path, Path]]:
    """Surface name -> (template source dir, output dir).

    A template's parent directory routes its output; a surface's template dir
    may be absent or empty. `output_root` is required and must already exist —
    this repository holds no rendered tree for it to default to — and its
    surface subdirectories are created at generation time as needed.
    `surfaces` filters to one surface, or "both".
    """
    if not output_root.is_dir():
        raise TemplateError(f"output root '{output_root}' is not an existing directory — every verb requires one")
    root = output_root
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
    """Load shared-chunks.toml. Chunk bodies are stripped of edge newlines.

    No name is reserved here. A bare marker resolves from this table and from
    nothing else, so no other source has a name to collide with: the overlay
    marker and the five tier tokens carry namespaces of their own, and a chunk
    called `overlay` or `tier-high` is now an ordinary chunk.
    """
    data = tomllib.loads(SHARED_CHUNKS.read_text(encoding="utf-8"))
    chunks = data.get("chunks", {})
    if not chunks:
        raise TemplateError(f"no [chunks.*] tables in {SHARED_CHUNKS.name}")
    for name, chunk in chunks.items():
        if "text" in chunk:
            chunk["text"] = chunk["text"].strip("\n")
        for variant, text in chunk.get("variants", {}).items():
            chunk["variants"][variant] = text.strip("\n")
        if "text" not in chunk and "variants" not in chunk:
            raise TemplateError(f"chunk '{name}' has neither text nor variants")
    return chunks


class TierBinding(NamedTuple):
    """The three tables one render needs, built from ONE load_chunks().

    `chunks` is what a bare marker resolves from. `real` and `probe` are the
    two dynamic tables the @!dyn.…!@ namespace resolves from, differing in
    exactly the five tier entries: `real` binds them to the effective pin map's
    text — what ships — and `probe` to a marker-free sentinel the discovery
    pass reads an output's own tier back out of. Threaded as one parameter so
    no call site can pair a stale table with a fresh one.
    """

    chunks: dict[str, dict]
    real: DynamicMap
    probe: DynamicMap


def tier_binding(chunks: dict[str, dict], *, pin_map: TierMap) -> TierBinding:
    """Bind the five tier tokens as dynamic parameters, twice over.

    A dynamic table rather than the chunk table or `scope`: the tokens are the
    invocation's parameters, not shared text and not arguments anything bound
    at a call site, and the table is threaded through every recursive expansion
    unchanged — so a token expands in template bodies, chunk bodies, variants,
    defaults, and overlay text alike, exactly as it did as a chunk.
    """
    return TierBinding(
        chunks=chunks,
        real={f"{TIER_TOKEN_PREFIX}{tier}": pin_map[tier] for tier in TIERS},
        probe={f"{TIER_TOKEN_PREFIX}{tier}": f"{TIER_SENTINEL}{tier}" for tier in TIERS},
    )


# ─── Overlay anchors and family files ────────────────────────────────────────


def assert_namespace(namespace: str, name: str, *, where: str) -> None:
    """Refuse a marker whose namespace names no source.

    The prefix is what routes, so an unregistered one has no resolution at all
    — and for an overlay namespace it could not announce itself any other way,
    silence being the LEGAL outcome for an anchor no loaded source fills.
    `where` labels the text the marker was read from.
    """
    if namespace not in NAMESPACES:
        raise TemplateError(
            f"{where}: @!{namespace}.{name}!@ names no source — namespaces are "
            f"{', '.join(NAMESPACES)}, and a marker with none is a chunk"
        )


def anchors_in(text: str, *, where: str) -> set[str]:
    """Every overlay anchor `text` declares — a marker's full spelling,
    `fam.<key>` — with every namespaced marker's namespace validated as it is
    read, overlay or not.

    The sweep is what catches a typo'd namespace in a chunk body this render
    never expands: render's own dispatch only sees what it reaches.
    """
    found = set()
    for match in MARKER.finditer(text):
        namespace, name = match.group(1), match.group(2)
        if namespace is None:
            continue
        assert_namespace(namespace, name, where=where)
        if namespace in OVERLAY_NAMESPACES:
            found.add(f"{namespace}.{name}")
    return found


def collect_anchors(chunks: dict[str, dict], template_paths: list[Path]) -> set[str]:
    """Every overlay anchor authored anywhere — template bodies and chunk
    bodies (text, variants, and defaults values) alike."""
    found: set[str] = set()
    for path in template_paths:
        found |= anchors_in(path.read_text(encoding="utf-8"), where=rel(path))
    for name, chunk in chunks.items():
        for value in (
            chunk.get("text", ""),
            *chunk.get("variants", {}).values(),
            *chunk.get("defaults", {}).values(),
        ):
            found |= anchors_in(value, where=f"chunk '{name}'")
    return found


class Family(NamedTuple):
    """A loaded family file: its overlay entries, and the tier map it declares.

    `entries` is keyed by the MARKER SPELLING its table fills — fam.<key>, for
    a [family.<key>] table — so it compares directly against the anchors
    templates and chunks author, and a second overlay source's entries key
    into the same map without collision.
    """

    entries: dict[str, dict]
    tiers: TierMap


def load_family(path: Path) -> Family:
    """Load and validate a model-family file.

    Schema: a required [tiers] table naming the member that staffs each of the
    five tiers, plus [family.<key>] tables, each with a family-wide `text`
    and/or [family.<key>.models.<member>] per-member override tables carrying
    `text`. The table's key is the anchor it fills: [family.<key>] fills
    @!fam.<key>!@. A family file only fills anchors — it has no vocabulary for
    replacing, suppressing, or modifying base text.

    [tiers] is required and TOTAL. A missing table, a missing tier, or a key
    outside the five is a hard error rather than a fallback to a partial map,
    to another family's members, or to the pin map: there is no fallback
    anywhere in this resolution. A family may still declare zero [family.*]
    tables — a name is reservable before an observed failure motivates an entry
    — but it needs its five-line [tiers] table to do so.
    """
    if not path.is_file():
        raise TemplateError(f"model-family file '{path}' does not exist")
    try:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
    except tomllib.TOMLDecodeError as exc:
        raise TemplateError(f"{rel(path)}: invalid TOML — {exc}") from exc
    stray = set(data) - {FAMILY_TABLE, TIERS_TABLE}
    if stray:
        raise TemplateError(
            f"{rel(path)}: unknown top-level table(s) {sorted(stray)} — a "
            f"family file holds only [{TIERS_TABLE}] and [{FAMILY_TABLE}.<key>] tables"
        )
    tiers = load_tiers(data, path)
    entries: dict[str, dict] = {}
    for key, entry in data.get(FAMILY_TABLE, {}).items():
        anchor = f"{FAMILY_NAMESPACE}.{key}"
        where = f"{rel(path)}: [{FAMILY_TABLE}.{key}]"
        if ANCHOR_KEY.fullmatch(key) is None:
            raise TemplateError(f"{where}: '{key}' is not an anchor key — it must match {IDENTIFIER}")
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
        entries[anchor] = entry
    return Family(entries=entries, tiers=tiers)


def load_tiers(data: dict, path: Path) -> TierMap:
    """Validate and return a family file's required [tiers] table."""
    where = f"{rel(path)}: [{TIERS_TABLE}]"
    listed = ", ".join(TIERS)
    if TIERS_TABLE not in data:
        raise TemplateError(
            f"{rel(path)}: no [{TIERS_TABLE}] table — every family file names "
            f"the member staffing each of {listed}. There is no fallback: an "
            f"incomplete family file does not load."
        )
    tiers = data[TIERS_TABLE]
    if not isinstance(tiers, dict) or set(tiers) != set(TIERS):
        raise TemplateError(
            f"{where}: keys must be exactly {listed} — found "
            + (", ".join(sorted(tiers)) if isinstance(tiers, dict) and tiers else "(none)")
        )
    for tier, member in tiers.items():
        if not isinstance(member, str) or member.split() != [member]:
            raise TemplateError(f"{where}: {tier} must be a non-empty member name with no whitespace")
    # Duplicate members across tiers are legal: two tiers staffed by one member
    # is a statement about that family's ladder, not a defect.
    return {tier: tiers[tier] for tier in TIERS}


def resolve_family(name: str, family_dir: Path = FAMILY_DIR) -> Path:
    """Resolve --family NAME to a family file.

    Two steps, and no third: path-shaped (a separator, or a .toml suffix) is
    taken as given, and load_family owns the existence error; otherwise
    <NAME>.toml under `family_dir`. A bare MODEL name is not a family name —
    the tier map names members now, so there is nothing for a model SPEC to
    have meant.
    """
    if "/" in name or "\\" in name or name.endswith(FAMILY_SUFFIX):
        return Path(name)
    family = family_dir / f"{name}{FAMILY_SUFFIX}"
    if family.is_file():
        return family
    available = ", ".join(sorted(path.stem for path in family_dir.glob(f"*{FAMILY_SUFFIX}"))) or "(none)"
    raise TemplateError(
        f"--family '{name}' names no family file: no {rel(family)}. Bare "
        f"model names are not family names — pass a family (available: "
        f"{available}) or a family-file path."
    )


def validate_family_anchors(entries: dict[str, dict], known: set[str]) -> None:
    """A family-file entry naming an anchor no template or chunk authors is a
    hard error — the file would silently fill nothing."""
    unknown = sorted(set(entries) - known)
    if unknown:
        raise TemplateError("family file names anchor(s) that exist in no template or chunk: " + ", ".join(unknown))


def validate_family_members(family: Family, tier_map: TierMap, path: Path) -> None:
    """A member no tier reaches is a hard error — the mirror of
    validate_family_anchors, for the other half of an overlay table's address.

    With [tiers] values and [family.*.models.<member>] keys as two independent
    strings, a mis-spelled member never fires and its tier reports as an
    ordinary Stock one: an entry that can never render, silently. Reachability
    is judged against the UNION of the family's own [tiers] and the EFFECTIVE
    tier map, so a table authored for a member reachable only through
    --model-tier-map does not fail a default run.
    """
    reachable = set(family.tiers.values()) | set(tier_map.values())
    declared = {member for entry in family.entries.values() for member in entry.get("models", {})}
    unreachable = sorted(declared - reachable)
    if unreachable:
        raise TemplateError(
            f"{rel(path)}: [{FAMILY_TABLE}.*.models.*] names member(s) no tier reaches: "
            + ", ".join(unreachable)
            + " — reachable members are "
            + ", ".join(sorted(reachable))
            + ". Such a table can never render; fix the spelling, or map a tier to it."
        )


def model_scope(entries: dict[str, dict], member: str) -> OverlayMap:
    """Only the model-scope overlays `member` matches in `entries`.

    The per-tier half of resolution, on its own: a tier pulls the family file's
    override for its own member and nothing else — never the file's family-wide
    text, which family_scope owns.
    """
    return {
        anchor: (entry["models"][member]["text"], "model")
        for anchor, entry in entries.items()
        if member in entry.get("models", {})
    }


def family_scope(entries: dict[str, dict]) -> OverlayMap:
    """Only the family-wide overlays in `entries` — what a loaded family fills
    render-wide, for outputs with a tier and outputs with no pin site alike."""
    return {anchor: (entry["text"], "family") for anchor, entry in entries.items() if "text" in entry}


def stock_tiers(entries: dict[str, dict], tier_map: TierMap) -> tuple[str, ...]:
    """The tiers whose mapped member declares ZERO [family.*.models.<member>]
    tables anywhere in the family — the legal Stock state.

    Zero, not "some anchors missing": a member with a table for one anchor and
    not another is per-anchor fallback to family-wide text, which
    resolve_overlays already handles, and is not stock.
    """
    declared = {member for entry in entries.values() for member in entry.get("models", {})}
    return tuple(tier for tier in TIERS if tier_map[tier] not in declared)


def resolve_overlays(entries: dict[str, dict], member: str | None) -> OverlayMap:
    """Resolve a loaded family file to anchor -> (text, scope).

    Resolution, not accumulation: at most one overlay per anchor, the model
    scope ("model") winning over the family scope ("family"). An entry with
    only member overrides, none matching, resolves to nothing.
    """
    return {**family_scope(entries), **(model_scope(entries, member) if member is not None else {})}


def tier_resolver(entries: dict[str, dict], tier_map: TierMap) -> Callable[[str | None], OverlayMap]:
    """Build the per-output resolver: the output's own TIER -> its overlays.

    The tier map is total, so there is no missing-member branch to write. A
    tier of None means the output has no pin site — a property of its type, not
    a failed resolution — and takes family-wide text only: only MODEL-scoped
    entries require a tier.
    """

    def resolve(tier: str | None) -> OverlayMap:
        return resolve_overlays(entries, None if tier is None else tier_map[tier])

    return resolve


def as_resolver(overlays: OverlaySource) -> Callable[[str | None], OverlayMap | None]:
    """Normalize a render call's `overlays` argument to a per-tier resolver.

    A resolver passes through; a plain map (or None) is render-wide — every
    output resolves to it, tiered or not.
    """
    return overlays if callable(overlays) else lambda tier: overlays


# ─── The tuning triple ───────────────────────────────────────────────────────


@dataclass(frozen=True)
class Tuning:
    """The effective (family, tier map, pin map) triple one render runs under.

    Both maps are post-merge and total. `stock` is derived from the family and
    the tier map together (stock_tiers) and `is_default` says the whole triple
    is the default one — claude with neither map changed BY VALUE, so
    `--family claude` and a no-op override are the default triple too. Only
    `is_default` is provenance rather than substance, and it exists for one
    caller: an install's summary line, where a default render is the non-event
    report-by-exception is built around.
    """

    family: Path
    tier_map: TierMap
    pin_map: TierMap
    stock: tuple[str, ...] = ()
    is_default: bool = False


def effective_map(defaults: TierMap, spec: str | None, *, flag: str) -> TierMap:
    """Merge one map flag's `tier=value` pairs over `defaults`.

    Positional-independent: `all=V` overwrites all five wherever it sits in the
    string, and each named tier is applied after it — so `all=haiku,high=opus`
    and `high=opus,all=haiku` both mean haiku everywhere but high. A named tier
    masks ONLY itself; unnamed tiers keep their default, which is what makes a
    partial override partial. Defaults are total and a merge only overwrites,
    so the result is total too.

    Values are not validated here, by design: a tier-map value naming a member
    the family declares no override for is the legal Stock state, and nothing
    in this repository owns the set of legal claude aliases a pin-map value is
    drawn from (see the module docstring — accepted risk, not an oversight).
    """
    if spec is None:
        return dict(defaults)
    named: TierMap = {}
    for pair in spec.split(","):
        entry = pair.strip()
        key, sep, value = entry.partition("=")
        key, value = key.strip(), value.strip()
        if not sep or not key or not value or "=" in value:
            raise TemplateError(f"{flag}: '{entry}' is not a tier=value pair")
        if any(part.split() != [part] for part in (key, value)):
            raise TemplateError(f"{flag}: '{entry}' contains whitespace inside a tier or a value")
        if key != MAP_ALL and key not in TIERS:
            raise TemplateError(f"{flag}: '{entry}' names no tier — tiers are {', '.join(TIERS)}, or all.")
        if key in named:
            raise TemplateError(f"{flag}: duplicate key '{key}' — a map is 1:1.")
        named[key] = value
    merged = {tier: named[MAP_ALL] for tier in TIERS} if MAP_ALL in named else dict(defaults)
    merged.update({tier: value for tier, value in named.items() if tier != MAP_ALL})
    return merged


def effective_tuning(
    path: Path,
    family: Family,
    *,
    tier_spec: str | None = None,
    pin_spec: str | None = None,
    family_dir: Path = FAMILY_DIR,
) -> Tuning:
    """Build the run's triple from the loaded family and the two map flags."""
    tier_map = effective_map(family.tiers, tier_spec, flag="--model-tier-map")
    pin_map = effective_map(DEFAULT_PIN_MAP, pin_spec, flag="--model-pin-map")
    default_family = family_dir / f"{DEFAULT_FAMILY}{FAMILY_SUFFIX}"
    return Tuning(
        family=path,
        tier_map=tier_map,
        pin_map=pin_map,
        stock=stock_tiers(family.entries, tier_map),
        is_default=(
            path.resolve() == default_family.resolve() and tier_map == family.tiers and pin_map == DEFAULT_PIN_MAP
        ),
    )


def map_spec(mapping: TierMap) -> str:
    """A map as the banner records it: TIERS order, never sorted — deterministic
    by construction, and readable highest-to-lowest."""
    return ",".join(f"{tier}={mapping[tier]}" for tier in TIERS)


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
    overlays: OverlayMap | None = None,
    dynamic: DynamicMap | None = None,
    depth: int = 0,
) -> str:
    """Expand every marker in `text`, each from the source its namespace names.

    Four sources, one dispatch, no precedence: a bare marker is a chunk;
    `arg.<name>` is `scope`, the arguments bound at this body's call site;
    `dyn.<name>` is `dynamic`, the invocation's own parameters; `fam.<key>` is
    `overlays`, one output's resolved anchor -> (text, scope-label) map (None,
    or a key no source filled: the marker expands to nothing). Per-output
    overlay resolution happens above this, in render_output.

    Because the namespace decides, a chunk and an argument that share a name
    are two different markers rather than a collision to arbitrate — there is
    no rule about which wins, and nothing for one to shadow.
    """
    if depth > MAX_EXPANSION_DEPTH:
        raise TemplateError("marker expansion exceeded maximum depth (cycle?)")

    def expand(match: re.Match[str]) -> str:
        namespace, name, arg_text = match.groups()
        args = dict(ARG.findall(arg_text))
        if namespace is None:
            chunk = chunks.get(name)
            if chunk is None:
                raise TemplateError(f"unknown chunk '{name}' — a bare marker names a chunk and nothing else")
            width = args.pop("wrap", None)
            body = chunk_body(name, chunk, args)
            inner = {**chunk.get("defaults", {}), **args}
            inner.pop("variant", None)
            expanded = render(body, chunks, inner, overlays, dynamic, depth + 1)
            return wrap(expanded, int(width)) if width else expanded
        assert_namespace(namespace, name, where=f"@!{namespace}.{name}!@")
        if args:
            raise TemplateError(f"@!{namespace}.{name}!@ takes no arguments — only a chunk marker does")
        if namespace == ARG_NAMESPACE:
            if name not in scope:
                raise TemplateError(
                    f"no argument '{name}' is bound here — @!{ARG_NAMESPACE}.{name}!@ reads a value the "
                    f"call site passes or [chunks.<name>.defaults] supplies"
                )
            return render(scope[name], chunks, scope, overlays, dynamic, depth + 1)
        if namespace == DYNAMIC_NAMESPACE:
            if dynamic is None or name not in dynamic:
                known = ", ".join(sorted(dynamic or ())) or "(none)"
                raise TemplateError(f"unknown invocation parameter '{name}' — @!{DYNAMIC_NAMESPACE}.…!@ names {known}")
            # A parameter is a value, not a body: it renders as itself. Marker
            # syntax reaching one from a map flag ships as literal text, which
            # assert_no_residual_markers refuses.
            return dynamic[name]
        entry = None if overlays is None else overlays.get(f"{namespace}.{name}")
        if entry is None:
            return ""
        # Verbatim in place, unwrapped: the anchor is a hole in the base text
        # and the source's text is what fills it, so any lead-in an author
        # wants is theirs to write. Marker-expanded like a chunk body, so a
        # typo'd reference inside it fails loudly.
        return render(entry[0], chunks, scope, overlays, dynamic, depth + 1)

    return MARKER.sub(expand, text)


# ─── Banner ──────────────────────────────────────────────────────────────────


def sha256_text(text: str) -> str:
    """Content hash as the banner records it, over utf-8 bytes."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def tuning_line(tuning: Tuning, *, seat: str | None) -> str:
    """The banner's !TUNING! line: the run's whole triple, plus this
    definition's own seat and the member it resolved against.

    `seat`/`member` are deliberately not spelled `tier` — that key is already
    spent on the tier MAP, and two different things named tier in one line is
    how a parser and a human come to disagree. An output with no pin site
    records `seat=none member=none`.
    """
    member = "none" if seat is None else tuning.tier_map[seat]
    return (
        f"# !TUNING! family={rel(tuning.family)}"
        f" seat={seat or 'none'} member={member}"
        f" tier={map_spec(tuning.tier_map)} pin={map_spec(tuning.pin_map)}"
        f" stock={','.join(tuning.stock) or 'none'}"
    )


def banner(template: Path, *, body_hash: str, tuning: Tuning, seat: str | None = None) -> str:
    """The five-line YAML-comment block stamped into frontmatter.

    `body_hash` is the sha256 of everything that will follow the block — the
    definition's claim to being unmodified since it was written. The !TUNING!
    line sits ABOVE it, because BODY_HASH_CLAIM matches the hash line together
    with the `#` closing the block around it. `tuning` is always present: with
    --family defaulting to claude there is no untuned render left to represent.
    """
    return (
        "#\n"
        f"# !GENERATED! from {rel(template)} and {rel(SHARED_CHUNKS)}"
        " — edit those. DO NOT HAND EDIT THIS FILE.\n"
        f"{tuning_line(tuning, seat=seat)}\n"
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
    no hash line, proves nothing, and is treated as possibly hand-edited.

    Line endings are translated first, and that is what makes this the single
    write-safety reading ARCHITECTURE.md promises rather than two. Generation
    reads its targets through read_text, whose universal newlines hand this
    function a CRLF target already translated; install reads raw bytes and hands
    it the CRLF through. Without the translation here the same file is
    unmodified output to one caller and hand-written content to the other, and a
    consumer tree some tool normalized to CRLF backs every file up, on every
    re-install, under a line accusing the operator of edits nobody made.
    """
    text = text.replace("\r\n", "\n").replace("\r", "\n")
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


def output_tier(probe_text: str) -> str | None:
    """The output's own tier, read out of a DISCOVERY render's frontmatter pin.

    Three legal readings, and only the first is a tier: the sentinel the probe
    binding put there; a literal pin, which comes back as itself and declares
    no tier; and no pin site at all. None is not a failed resolution — it is a
    property of the output's type (or of a pin site not yet tokenized).
    """
    pin = frontmatter_pin(probe_text)
    if pin is None or not pin.startswith(TIER_SENTINEL):
        return None
    tier = pin[len(TIER_SENTINEL) :]
    return tier if tier in TIERS else None


def banner_claim(text: str) -> str | None:
    """Return the template path a definition's banner claims, if it has one."""
    front = frontmatter_of(text)
    if front is None:
        return None
    match = BANNER_CLAIM.search(front)
    return match.group(1) if match else None


class TuningClaim(NamedTuple):
    """A banner's !TUNING! line, parsed back. Strings throughout: it is a claim
    read off a file, compared against another claim, never re-resolved."""

    family: str
    seat: str
    member: str
    tier: str
    pin: str
    stock: str


def tuning_claim(text: str) -> TuningClaim | None:
    """Return the tuning a definition's banner claims, or None when it carries
    no !TUNING! line (a bannerless file, or one written before 3.0.0)."""
    front = frontmatter_of(text)
    if front is None:
        return None
    match = TUNING_CLAIM.search(front)
    return TuningClaim(*match.groups()) if match else None


def describe_tuning(claim: TuningClaim | None) -> str:
    """A tuning claim as MISTUNED prints it."""
    if claim is None:
        return "no tuning claim"
    return f"family {claim.family}, seat {claim.seat}, member {claim.member}, " f"tier[{claim.tier}] pin[{claim.pin}]"


def split_outputs(path: Path) -> tuple[dict[str, dict[str, str]], str]:
    """Split a template into its output declarations and its body.

    A template may open with a fenced TOML block declaring the definitions it
    renders and the parameters that differ between them:

        +++
        [outputs.mad-participant-opus]
        model = "opus"
        +++
        ---
        name: @!arg.name!@
        model: @!arg.model!@
        ...

    The fence BINDS, so its keys are bare; the body READS them as
    @!arg.<key>!@, plus @!arg.name!@ bound to the output's own name. A template
    with no block renders one definition named
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
    binding: TierBinding,
    params: dict[str, str],
    resolve: Callable[[str | None], OverlayMap | None],
) -> tuple[str, str | None]:
    """Render one declared output; return (text, the tier it declared).

    Two passes, both over the raw body. Pass A renders under `binding.probe`,
    whose tier tokens carry a marker-free sentinel, so reading the pin out of
    that render's frontmatter yields the output's own tier — an outputs-table
    `model` parameter and a literal frontmatter line are the same thing here,
    exactly as they are to frontmatter_pin. Pass B renders under `binding.real`
    and the overlay set the tier resolves.

    Pass B is UNCONDITIONAL. Skipping it when the overlay set is unchanged — the
    optimization this replaced — would ship pass A's sentinel text into a
    system prompt wherever a tier token sits in a body or chunk. Pass A's
    output is never written, never hashed, and never returned.
    """
    tier = output_tier(render(body, binding.chunks, params, resolve(None), binding.probe))
    return render(body, binding.chunks, params, resolve(tier), binding.real), tier


def assert_tiered(text: str, tier: str | None, *, path: Path, name: str) -> None:
    """Refuse a rendered output whose pin site resolved no tier.

    A pin site declares its tier with one of the five tier tokens, and every
    pin site does: the alternative — a literal pin — renders exactly the bytes
    the token would have, so it costs the definition its model scope and
    nothing else, which is a loss no diff and no check can show. Refusing here
    is what keeps that from being how a new template arrives.

    An output with NO pin site is untouched by this. It carries no `model:` key
    at all, which is a property of its type, not a failed resolution.
    """
    pin = frontmatter_pin(text)
    if tier is not None or pin is None:
        return
    tokens = ", ".join(f"@!{DYNAMIC_NAMESPACE}.{TIER_TOKEN_PREFIX}{seat}!@" for seat in TIERS)
    raise TemplateError(
        f"{rel(path)}: output '{name}' pins 'model: {pin}' literally, which "
        f"resolves no tier — declare the tier with one of {tokens}, whose "
        f"rendered text the pin map supplies"
    )


RESIDUAL_MARKER = re.compile(r"@!|!@")


def assert_no_residual_markers(text: str, *, path: Path, name: str) -> None:
    """Refuse a rendered output that still carries a literal marker delimiter
    — '@!' or '!@' — anywhere in it.

    render() only ever consumes a span matching MARKER — a name and every
    argument key drawn from IDENTIFIER, plus its quoted arguments. A marker
    mistyped past that syntax (wrong case, an underscore, a stray character)
    is therefore never a marker to render() at all: it is ordinary text that
    happens to spell "@!...!@", passes through untouched, and ships verbatim
    into what becomes an agent's system prompt — with no chunk, no overlay,
    and no error to say so. A name inside valid syntax but unknown to
    render() is already caught there, before this ever runs; what reaches
    here is the harder case, a marker no regex recognized as an attempt in
    the first place. Naming the line spares a maintainer a grep for it.
    """
    for lineno, line in enumerate(text.splitlines(), start=1):
        if RESIDUAL_MARKER.search(line):
            raise TemplateError(
                f"{rel(path)}: output '{name}' renders a literal marker delimiter at line {lineno}: {line.strip()!r} "
                f"— a marker mistyped past MARKER's syntax, or literal text that needs escaping; "
                f"rendered output must hold no marker syntax at all"
            )


def render_template(
    path: Path,
    binding: TierBinding,
    out_dir: Path,
    *,
    surface: str,
    overlays: OverlaySource = None,
    tuning: Tuning,
) -> list[tuple[Path, str]]:
    """Render every definition a template declares, banner stamped into each.

    `out_dir` is the template's path mirrored into its surface (see
    template_targets); `surface` names that surface. The banner always lands in
    frontmatter: agent templates must open with a frontmatter block; command
    templates may open with one (banner injected identically) or with bare
    prompt text (a minimal banner-only frontmatter block is emitted above it).
    The banner is stamped last, since it carries the hash of what follows it.

    This is where an output's pin site is held to declaring a tier
    (assert_tiered) and where a rendered body is held to carrying no leftover
    marker syntax (assert_no_residual_markers), because this is the first
    point that knows both the render and the name of the output it came from.
    """
    outputs, body = split_outputs(path)
    resolve = as_resolver(overlays)
    rendered = []
    for name, params in sorted(outputs.items()):
        text, tier = render_output(body, binding, params, resolve)
        assert_tiered(text, tier, path=path, name=name)
        assert_no_residual_markers(text, path=path, name=name)
        if text.startswith("---\n"):
            # YAML comments: valid frontmatter, dropped by every parser, and
            # outside the body that becomes the prompt.
            rest = text[4:]
        elif surface == COMMAND_SURFACE:
            rest = "---\n" + text
        else:
            raise TemplateError(f"{rel(path)}: agent template must open with YAML frontmatter")
        stamp = banner(path, body_hash=sha256_text(rest), tuning=tuning, seat=tier)
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
    "be generated, delete it and re-run; otherwise rename the colliding file."
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
    binding: TierBinding,
    smap: dict[str, tuple[Path, Path]],
    overlays: OverlaySource = None,
    globs: GlobMap | None = None,
    *,
    tuning: Tuning,
) -> list[tuple[Path, str]]:
    """Every (target, rendered text) pair across every template, sorted — the
    ones `globs` selects, when a selection is in force.

    `tuning` is required rather than defaulted: every render runs under a
    triple, so a None one has no reading — it would reach banner() as a
    provenance claim with nothing to claim.
    """
    pairs = []
    for surface, template, out_dir in template_targets(smap, globs):
        surface_root = smap[surface][1]
        pairs.extend(
            pair
            for pair in render_template(template, binding, out_dir, surface=surface, overlays=overlays, tuning=tuning)
            if selected(surface, output_key(pair[0], surface_root), globs)
        )
    return sorted(pairs, key=lambda pair: rel(pair[0]))


def report_overlays(entries: dict[str, dict], overlays: OverlayMap, tuning: Tuning) -> None:
    """State which anchors the family file filled, and from which scope, for an
    output with no tier — the family-wide resolution every output starts from.
    Prints nothing for a family declaring no anchors, which is every default
    run today."""
    if not entries:
        return
    print()
    print(f"overlay anchors from {rel(tuning.family)}")
    print("=" * 60)
    for anchor in sorted(entries):
        scope = overlays[anchor][1] if anchor in overlays else "unfilled (no scope matched)"
        print(f"  {anchor:<28} {scope}")


def report_tuning(tuning: Tuning) -> None:
    """The run's triple, echoed once in generate, check and install alike.

    A DISPLAY serialization, deliberately not the banner's: the banner is a
    machine claim check parses back and must stay stable across versions, while
    this brackets each map for a human scanning a terminal and is free to
    change. One shared serializer would couple a display choice to a parsed
    contract — a tree-wide MISTUNED sweep caused by adding a space.
    """
    print(f"tuning: family={rel(tuning.family)} " f"tier[{map_spec(tuning.tier_map)}] pin[{map_spec(tuning.pin_map)}]")


def report_divergence(tuning: Tuning, *, family_dir: Path = FAMILY_DIR) -> None:
    """Notice — never a gate, never an exit status — when the two maps disagree
    within the claude family.

    Gated on the effective family file BEING claude.toml, however it was
    reached. For any other family the two maps diverge at every tier by
    construction — the tier map holds family members and the pin map holds
    claude aliases — so the notice would fire five times a run carrying no
    information. Within claude the namespaces coincide, so a divergence is a
    deliberate act worth naming: tuning against one member's overrides while
    shipping another's capacity is the isolation this feature exists to allow.
    """
    if tuning.family.resolve() != (family_dir / f"{DEFAULT_FAMILY}{FAMILY_SUFFIX}").resolve():
        return
    diverging = [tier for tier in TIERS if tuning.tier_map[tier] != tuning.pin_map[tier]]
    if not diverging:
        return
    where = ", ".join(f"{tier} (tier={tuning.tier_map[tier]}, pin={tuning.pin_map[tier]})" for tier in diverging)
    print(f"notice: tier map and pin map diverge at {where} — tuning/capacity isolation, not an error")


def report_tuned(tuning: Tuning) -> None:
    """Notice — one summary line, never per-tier, never an error — naming the
    tiers whose mapped member the family declares overrides for, and the member
    each one resolved to.

    Silence is the stock render: no mapped member carries a
    [family.*.models.<member>] table, which is every run of both shipped families
    today. The set is the complement of `tuning.stock` rather than a second
    walk of the family entries, so this line and the banner's `stock=` field
    answer out of one computation and cannot drift apart.
    """
    tuned = [tier for tier in TIERS if tier not in tuning.stock]
    if not tuned:
        return
    where = ", ".join(f"{tier} ({tuning.tier_map[tier]})" for tier in tuned)
    print(f"notice: member-scoped tuning is in force at {where} — {rel(tuning.family)} fills their anchors")


def generate(
    binding: TierBinding,
    smap: dict[str, tuple[Path, Path]],
    *,
    overlays: OverlaySource = None,
    globs: GlobMap | None = None,
    verbose: bool = False,
    tuning: Tuning,
) -> bool:
    clean = True
    unchanged = 0
    print("Generating definitions")
    print("=" * 60)
    found = template_targets(smap, globs)
    if not found:
        print(f"  ERROR      no templates found under {rel(TEMPLATES_DIR)}/")
        return False

    pairs = all_renders(binding, smap, overlays, globs, tuning=tuning)
    # The output root itself was asserted to exist when the surface map was
    # built. Everything below it — each surface directory, and the mirrored
    # subdirectories under those — is created as needed.
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
    binding: TierBinding,
    smap: dict[str, tuple[Path, Path]],
    *,
    overlays: OverlaySource = None,
    globs: GlobMap | None = None,
    verbose: bool = False,
    show_diff: bool = True,
    tuning: Tuning,
) -> bool:
    clean = True
    print()
    print("Generated definitions vs templates")
    print("=" * 60)

    found = templates(smap)
    if not found:
        print(f"  ERROR    no templates found under {rel(TEMPLATES_DIR)}/")
        clean = False

    for target, rendered in all_renders(binding, smap, overlays, globs, tuning=tuning):
        if not target.exists():
            print(f"  {'MISSING':<8} {rel(target)} — run: just generate")
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
        # The expected claim is this output's OWN render, not the run's triple:
        # `seat` and `member` are per-output, so only the render knows them.
        claimed = tuning_claim(actual)
        expected = tuning_claim(rendered)
        if claimed != expected:
            # A tuning mismatch would also show as a byte diff (the banner is
            # part of the render); name the mismatch instead of dumping it.
            print(
                f"  {'MISTUNED':<8} {rel(target)} — banner claims "
                f"{describe_tuning(claimed)}, but check ran with "
                f"{describe_tuning(expected)}"
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
            f"{rel(TEMPLATES_DIR)}/ or {rel(SHARED_CHUNKS)},\n"
            f"then run: just generate"
        )
        if claim_errors:
            print(
                "ORPHAN/MISLABELED is not fixed by regenerating: restore the named "
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

# Project documentation: the closed vocabulary of filenames that document a
# project TO ITS OWN DEVELOPERS. A consuming project installs the code, not the
# project the code came from — a shipped package's SPEC and ARCHITECTURE state
# its contract for someone changing it here, its CONVENTIONS carries house rules
# for working inside this repository (kb_tools' names the tooling repo's own
# justfile targets), and ROADMAP, AGENTS and CLAUDE are development apparatus by
# definition. A rule and not a list: it is the same vocabulary the contract-doc
# precedence chain is spelled in, so it is closed, and adding a package or a
# docs/ directory below one needs no edit here.
#
# Matched by NAME at any depth, never by placement or suffix. `README.md` is
# deliberately absent: it is the one name in that vocabulary a package writes
# for its consumer rather than its maintainer, so a package that grows one is
# shipping it on purpose. `.tmpl` files are payload rather than documentation —
# kb_tools/installed/CONVENTIONS.md.tmpl is written into a consuming project's
# own KB by a build — and match nothing here.
INSTALL_EXCLUDED_DOCS = frozenset(
    {"SPEC.md", "ARCHITECTURE.md", "CONVENTIONS.md", "ROADMAP.md", "AGENTS.md", "CLAUDE.md"}
)

# The shipped code packages: (source directory in this repository, destination
# relative to the install ROOT). Both paths are POSIX-relative strings.
#
# The destination is a CONSUMER CONTRACT and is frozen. Runner snippets already
# installed in consuming projects name `.claude/agents/kb_tools/…`, and every
# definition body writes its paths against `.claude/agents/…` (SPEC.md,
# Deployed Surfaces), so a consuming project cannot be asked to notice that
# this repository rearranged itself. Stating the pairing as data is what lets
# the source side move while the destination side does not — the two are one
# fact only for as long as the destination is derived from directory
# placement.
#
# Rows are read by install_pairs (which carves a row's files out of the
# surface walk when its source lies inside a surface, so nothing is emitted
# twice) and by assert_install_root (which must guard a package source
# wherever it sits). Relocating a package is therefore an edit to this table
# and to nothing else.
SHIPPED_PACKAGES = (
    ("kb_tools", "agents/kb_tools"),
    ("liaison_tools", "agents/liaison_tools"),
)

# The !INSTALLED! banner: what a copied file says about itself. Deterministic
# text — nothing dated, nothing per-run — so an unchanged source re-installs to
# the identical bytes.
INSTALLED_NOTICE = "!INSTALLED! from the adjagent repo — do not edit in place; " "edit the source repo and re-install."
# Filetypes whose comment syntax can carry the banner as `#` lines. A suffix
# absent here and not .md admits no comment we can rely on (JSON is the case
# in point), so its file is copied verbatim and the install reports it.
HASH_COMMENT_SUFFIXES = frozenset({".py", ".sh", ".toml", ".mk", ".just"})
MARKDOWN_SUFFIX = ".md"
# Third-party source vendored into a shipped package lives under a directory of
# this name. Its files ship — this is a STAMPING carve-out, never an exclusion —
# but they are copied verbatim: the banner would claim adjagent provenance over
# code we did not write and send its reader to the wrong source repository.
VENDOR_DIR = "_vendor"
# The only mode bits an install ever sets, and only on a file it just created:
# an executable source (the liaison shell tools) must land runnable. An update
# writes in place and inherits whatever mode the target already carries.
EXEC_BITS = stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH


def bannerable(source: Path) -> bool:
    """Does this file's type admit a comment the !INSTALLED! banner can live
    in? (Whether it *needs* one is install_content's question.)"""
    return source.suffix == MARKDOWN_SUFFIX or source.suffix in HASH_COMMENT_SUFFIXES


def vendored(key: Path) -> bool:
    """Is this install key inside a shipped package's `_vendor/` tree?

    Decided on the DESTINATION key, as every other piece of install accounting
    is (install_pairs), and never on the source path: a source root that itself
    sits somewhere under a `_vendor/` directory would otherwise carve out the
    whole product. Directory names match at any depth; a file named `_vendor`
    is not one."""
    return VENDOR_DIR in key.parts[:-1]


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
    Claude Code lists it by, so nothing may displace it), while frontmatter-less
    markdown under agents/ takes an HTML comment instead — frontmatter there
    would present supporting material as a dispatchable definition, which it is
    not.
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
        or relpath.name in INSTALL_EXCLUDED_DOCS
        or relpath.suffix == INSTALL_EXCLUDED_SUFFIX
    )


def assert_install_root(root: Path, *, source_root: Path = REPO_ROOT) -> None:
    """Guard against installing this repository into itself.

    An install stamps an !INSTALLED! banner into every copied file and renders
    definitions into ROOT's two surfaces, so a ROOT overlapping a package
    source rewrites the source it is copying from; a second run — the content
    now hashing to its own banner — nests a second banner inside the first
    and, being unrecognizable as this tool's output, does so with no backup.

    Both paths are resolved before comparison, so a symlink or a `..` segment
    cannot route around the check, and containment is tested in both directions:
    a ROOT *inside* a package source is as fatal as a ROOT *containing* one.

    The guarded set is the shipped code packages, read from SHIPPED_PACKAGES
    rather than assumed to sit under a deployed surface: a package source
    outside every surface is still a source this install reads and must never
    be written back into. Every row's source sits at the repository top level
    today, so the row is the only thing guarding it — and `install .` from the
    repo root is refused by those rows, since the repo root contains every one
    of them.
    """
    target = root.resolve()
    for source, _ in SHIPPED_PACKAGES:
        source_dir = (source_root / source).resolve()
        if target.is_relative_to(source_dir) or source_dir.is_relative_to(target):
            raise TemplateError(
                f"install ROOT '{rel(root)}' is, or contains, this repository's own "
                f"{source}/ package source ({rel(source_dir)}) — installing there would "
                f"stamp banners into the very files it copies from. Install into a consuming "
                f"project's .claude directory instead"
            )


def package_sources(surface: str) -> tuple[Path, ...]:
    """The surface-relative paths within `surface` that a SHIPPED_PACKAGES row
    owns, and which that surface's own walk must therefore skip.

    Empty for every surface now that the package sources sit at the repository
    top level — which is what made that relocation a change to
    SHIPPED_PACKAGES alone. It stays because the carve-out is a property of the
    table, not of today's arrangement: a package moved back under a surface
    must still be emitted once."""
    return tuple(
        Path(source).relative_to(surface) for source, _ in SHIPPED_PACKAGES if Path(source).is_relative_to(surface)
    )


def install_pairs(smap: dict[str, tuple[Path, Path]], *, source_root: Path = REPO_ROOT) -> list[tuple[str, Path, Path]]:
    """(ROOT-relative key, source, target) for every file an install COPIES,
    sorted by key: whatever the surfaces hold under `source_root`, plus the
    shipped code packages at their frozen destinations, minus the exclusion
    list. The definitions are not here — they are rendered, not copied, and
    this repository has no surface directories for the walk to find. The walk
    stays because `source_root` is a parameter: it is what makes the
    source-to-destination mapping below testable against a source layout that
    does have them.

    The key is the file's destination, not its source. That is what keeps an
    install's accounting — the per-surface counts, the report, a consuming
    project's tree — invariant under a change to where a package's source
    sits, which is the whole point of SHIPPED_PACKAGES.

    A package row is delivered only when the surface owning its destination is
    in `smap`, so `--surfaces commands` installs no agent-side package.
    """
    found: list[tuple[str, Path, Path]] = []

    def collect(walk_root: Path, key_root: Path, out_dir: Path, *, carved: tuple[Path, ...] = ()) -> None:
        if not walk_root.is_dir():
            return
        for source in sorted(walk_root.rglob("*")):
            if not source.is_file():
                continue
            relpath = source.relative_to(walk_root)
            if excluded_from_install(relpath) or any(relpath.is_relative_to(owned) for owned in carved):
                continue
            found.append(((key_root / relpath).as_posix(), source, out_dir / relpath))

    for surface, (_, out_dir) in smap.items():
        collect(source_root / surface, Path(surface), out_dir, carved=package_sources(surface))

    for source_rel, dest_rel in SHIPPED_PACKAGES:
        destination = Path(dest_rel)
        surface, *below = destination.parts
        if surface not in smap:
            continue
        collect(source_root / source_rel, destination, smap[surface][1].joinpath(*below))

    return sorted(found, key=lambda pair: pair[0])


def quiet_pass(run: Callable[[], bool], *, verbose: bool) -> bool:
    """Run an install's integrity or render pass, holding its file-by-file
    report back unless it has something to say.

    An install reports by exception: a fresh install and a clean overwrite are
    both non-events, so the pass prints only when it came back unclean — or when
    --verbose asked for everything. The pass's own verdict is returned either
    way, so what the install does with it never depends on what was printed.

    A pass that raises has already printed the most useful part of its report —
    how far it got before the template it could not render — and the buffer is
    the only copy of it. Releasing the buffer in a finally is what keeps "held
    back" from meaning "discarded on exactly the run that needed it".
    """
    buffer = io.StringIO()
    ok = False
    try:
        with contextlib.redirect_stdout(buffer):
            ok = run()
    finally:
        if verbose or not ok:
            print(buffer.getvalue(), end="")
    return ok


def install(
    binding: TierBinding,
    smap: dict[str, tuple[Path, Path]],
    *,
    root: Path,
    overlays: OverlaySource = None,
    source_root: Path = REPO_ROOT,
    verbose: bool = False,
    tuning: Tuning,
) -> bool:
    """Install the full product into `root` (a consuming project's .claude).

    Two halves, in order. First a recursive copy of everything install_pairs
    names under `source_root` — for this repository, the shipped packages at
    their frozen destinations — minus the exclusion list, with an !INSTALLED!
    banner stamped into every copied file whose type admits one and whose
    provenance is this repository's (so never under `_vendor/`). Then the
    ordinary generation pass, which renders the definitions into the surfaces
    under `root`.

    The definitions are rendered, never copied: this repository keeps no
    checked-in render for a copy to read. The (family, tier-map, pin-map)
    triple the install was given is simply the triple that render runs under,
    which is why a default install and a tuned one are one code path rather
    than a copy and a special case. Every target the pass meets is absent or
    provably untouched output, so it leaves no backups behind.

    Reports by exception. A clean install — every target either fresh or
    overwritten with output provably this tool's own — is one summary line and
    nothing else: what landed where. The lines beyond it are problems only, and
    each names its files: a pass that came back unclean, and a target whose
    extant content no install of this repo wrote, whose prior bytes this run put
    aside as a .bak. --verbose still lists every file and names the two verbatim
    populations apart: the unbannered ones, whose filetype (never their state)
    is the whole of what makes them so, and the vendored ones under `_vendor/`,
    which a banner would misattribute to this repository. A re-vendor to a new
    pin therefore leaves one numbered .bak per changed file in a consumer's
    tree — an unstamped file can never be proved this tool's own — which is the
    per-upgrade cost of not lying about provenance.

    The copy finishes before the render begins, so a render that raises leaves
    the packages complete and the definitions partial under ROOT. What landed
    is printed on the way out too — an operator shown only the error would read
    it as "nothing happened."
    """
    assert_install_root(root, source_root=source_root)
    pairs = install_pairs(smap, source_root=source_root)
    if not pairs:
        print(f"ERROR: no installable files under {rel(source_root)}/")
        return False

    landed: dict[str, int] = {}
    unbannered: list[str] = []
    unstamped: list[str] = []
    clobbered: list[str] = []
    for key, source, target in pairs:
        surface = key.split("/", 1)[0]
        # Two ways a copied file goes out verbatim, reported apart because the
        # reasons are: a filetype with no comment syntax, and foreign source
        # under _vendor/ whose provenance is not ours to claim.
        is_vendored = vendored(Path(key))
        content = None if is_vendored else install_content(source, surface=surface)
        if is_vendored and bannerable(source):
            unstamped.append(key)
        elif content is None and not bannerable(source):
            unbannered.append(key)
        outcome = install_file(source, target, content)
        landed[surface] = landed.get(surface, 0) + 1
        if outcome == "backed up":
            clobbered.append(key)
        if verbose:
            print(f"  {outcome:<10} {key}")

    def summary(counts: dict[str, int]) -> str:
        return ", ".join(f"{n} under {surface}/" for surface, n in sorted(counts.items()))

    copied = summary(landed)
    try:
        # One pass, one triple, whatever the triple is. Both the effective
        # triple and the tier resolver reach it — arguments this function was
        # itself handed, and which a call site that dropped them would turn
        # into a tree of definitions tuned differently from what their own
        # banners claim, or a whole-tree MISTUNED verdict on a correct install.
        integrity = quiet_pass(
            lambda: generate(binding, smap, overlays=overlays, tuning=tuning, verbose=verbose),
            verbose=verbose,
        )
        renders = all_renders(binding, smap, overlays, tuning=tuning)
        for target, _ in renders:
            for surface, (_, out_dir) in smap.items():
                if target.is_relative_to(out_dir):
                    landed[surface] = landed.get(surface, 0) + 1
                    break
        rendered = len(renders)
        ok = integrity
    except TemplateError:
        # The copy finishes before the render starts, so ROOT already holds the
        # shipped packages entire, and however far the render got, neither of
        # which the error on its way out says anything about. An operator told
        # only "error: unknown chunk" reads it as "the install did not happen";
        # this is the last point at which anything can tell them otherwise.
        print(f"installed: {copied} → {rel(root)} — the packages are in place; the render after them failed")
        raise

    counts = summary(landed)

    tuned = ""
    if not tuning.is_default:
        # Report by exception, and every install renders now: a DEFAULT-triple
        # render is the non-event, so only a triple that differs from the
        # default one earns a clause. Keying this on `rendered` instead would
        # append a count and a restatement of the defaults to every ordinary
        # install — nothing an operator needs, and a description of nothing.
        tuned = (
            f"; {rendered} rendered under family {rel(tuning.family)}, "
            f"tier[{map_spec(tuning.tier_map)}] pin[{map_spec(tuning.pin_map)}]"
        )
    print(f"installed: {counts} → {rel(root)}{tuned}")
    if verbose and unbannered:
        print(f"unbannered (type admits no comment): {len(unbannered)} file(s) — " + ", ".join(unbannered))
    if verbose and unstamped:
        print(f"unstamped (vendored third-party source): {len(unstamped)} file(s) — " + ", ".join(unstamped))
    if not integrity:
        print("integrity: NOT CLEAN — see the report above")
    if clobbered:
        print(
            f"replaced: {len(clobbered)} installed file(s) held content no install of this repo "
            "wrote; each one's prior content is beside it as a numbered *.bak, yours to delete:"
        )
        for key in clobbered:
            print(f"  {key}")
    return ok


# ─── Operator-config integration ─────────────────────────────────────────────

# The published operator baseline, and the file the bail path writes beside a
# live one. The extension lands LAST — incoming.CLAUDE.md — so an editor still
# opens it as Markdown.
PUBLISHED_BASELINE = REPO_ROOT / "user-config" / "INSTALLED_CLAUDE.md"
INCOMING_PREFIX = "incoming."
# The single rolling backup this verb ever writes: the live file's own bytes as
# they stood immediately before a write that changes them. Overwritten on the
# next such write, left untouched by one that changes nothing, and never
# written at all on the bail path (which never touches the live file) or the
# exact-match no-op (which has nothing to keep). Named like INCOMING_PREFIX —
# prefix first, .md last — for the same reason: an editor still reads it as
# Markdown.
BACKUP_PREFIX = "backup."
# Below this difflib ratio against the nearest recovered revision — twice the
# matched non-blank lines over the two files' total — a live file is taken to
# share no ancestry with the baseline. Two documents on the same subject written
# independently share whole lines only by accident, so a quarter is a low bar
# deliberately: the consequence of the call, append rather than align, is
# reported before it is taken and undoable by hand either way.
MIN_ANCESTRY_SIMILARITY = 0.25
# The report's unit: a `## ` heading, and the text above the first one.
SECTION_HEADING = re.compile(r"^## +(.+?)\s*$")
PREAMBLE_SECTION = "(preamble)"
# git log's per-commit line, marked so a filename can never be read as one. A
# unit separator rather than a NUL: this travels in argv, which cannot carry
# one, and git renders a control byte in a path as an escape rather than
# verbatim, so no filename can open with it.
LOG_MARK = "\x1f"
# case -> (what the report leads with, what it tells the operator to do). Every
# branch of plan_integration lands on exactly one key.
CASE_REPORT = {
    "fresh": (
        "no live file — the published baseline is installed whole",
        "installed; nothing of yours was there to keep",
    ),
    "current": (
        "the live file already matches the published baseline",
        "nothing to do",
    ),
    "published": (
        "the live file is an unmodified published revision — the update applies whole",
        "updated; it carried no local edits to keep",
    ),
    "unrelated": (
        "no shared ancestry — the baseline is appended to your file, not merged",
        "appended below your own content: hand-edit out the duplication, and anything of "
        "ours that contradicts what you wrote",
    ),
    "merged": (
        "local edits kept, published changes merged in around them",
        "merged; your own sections and edits are in the result",
    ),
    "bail": (
        "no recovered base merges as whole blocks — the live file is left alone",
        "integrate by hand from the incoming copy, then delete it",
    ),
}


@dataclass(frozen=True)
class Revision:
    """One published revision of the baseline, as git holds it."""

    commit: str
    date: str
    text: str

    @property
    def label(self) -> str:
        return f"{self.commit[:8]} ({self.date})"


@dataclass(frozen=True)
class Integration:
    """What an integration decided, before anything is written.

    `case` keys CASE_REPORT; `base` is the published revision the live file was
    matched against, absent where no base was needed or none was found;
    `live_text` is what the operator's file becomes, None where it is not
    touched at all; `incoming_text` lands beside it as incoming.CLAUDE.md on the
    bail path and nowhere else.
    """

    case: str
    base: Revision | None = None
    live_text: str | None = None
    incoming_text: str | None = None


def git_output(cwd: Path, *args: str) -> str | None:
    """A git command's stdout, or None where git could not answer — no binary,
    no repository, no such revision.

    None is a classification input rather than a failure: a caller with no
    history recovers no base, and no base is the bail path."""
    try:
        done = subprocess.run(
            ["git", "-C", str(cwd), *args],
            capture_output=True,
            text=True,
            encoding="utf-8",
            check=False,
        )
    except OSError:
        return None
    return done.stdout if done.returncode == 0 else None


def baseline_revisions(source: Path) -> list[Revision]:
    """Every published revision of `source`, newest first.

    `--follow`, because the published baseline has been renamed
    (user-config/CLAUDE.md -> INSTALLED_CLAUDE.md) and a revision from before
    the rename is exactly the era an operator's file may date from. The path a
    revision names is read back out of the log rather than assumed: `git show
    <commit>:<path>` wants the path as it was at that commit, which a rename
    changes.
    """
    log = git_output(
        source.parent,
        "log",
        "--follow",
        f"--format={LOG_MARK}%H %cs",
        "--name-only",
        "--",
        source.name,
    )
    if log is None:
        return []
    revisions: list[Revision] = []
    commit = date = ""
    for line in log.splitlines():
        if line.startswith(LOG_MARK):
            commit, _, date = line[len(LOG_MARK) :].partition(" ")
        elif line.strip() and commit:
            text = git_output(source.parent, "show", f"{commit}:{line}")
            if text is not None:
                revisions.append(Revision(commit=commit, date=date, text=text))
            commit = ""
    return revisions


def line_matcher(left: str, right: str) -> difflib.SequenceMatcher:
    """The line-level comparison both candidate measures read.

    Blank lines are dropped before comparing: they are a third of a Markdown
    document's lines and every one of them matches every other, which pulls the
    similarity of two unrelated documents toward the similarity of two related
    ones. autojunk is off for the mirror-image reason — it would discard a line
    recurring across a long document, and here that is the structure."""
    return difflib.SequenceMatcher(
        None,
        [line for line in left.splitlines() if line.strip()],
        [line for line in right.splitlines() if line.strip()],
        autojunk=False,
    )


def differing_lines(left: str, right: str) -> int:
    """How many lines the two texts do not have in common — the distance
    candidate bases are ordered by, nearest first."""
    return sum(
        (i2 - i1) + (j2 - j1) for tag, i1, i2, j1, j2 in line_matcher(left, right).get_opcodes() if tag != "equal"
    )


@dataclass(frozen=True)
class Block:
    """One paragraph of a document, and the exact bytes it occupies there.

    `text` — the paragraph's lines with the blank lines around them stripped
    away — is what a merge aligns and compares on. `raw` is what a merge emits:
    the same lines exactly as their document holds them, followed by the blank
    lines that separated them from what came next. The two are kept apart
    because only the first is a statement about content: two blocks saying the
    same thing with different spacing are the same paragraph and different
    bytes, and an integration has to honour both readings at once.
    """

    text: str
    raw: str


def blocks(text: str) -> list[Block]:
    """`text` as blocks, one per paragraph, partitioning it: every byte lands in
    exactly one block, so `stitch(blocks(text))` is `text` again.

    The merge unit, and the whole of the safety discriminator: a paragraph is
    bounded by blank lines on both sides by construction, so a merge over these
    units adds, drops or replaces whole blocks and can never edit inside one.
    Each block carries its own trailing blank lines — and the first carries the
    document's leading ones — because those bytes are the operator's too, and a
    block copied through a merge has to bring its spacing with it. A document
    with no content line at all is the one exception, holding no blocks and so
    stitching back as empty: there is nothing there for a merge to carry.
    """
    found: list[Block] = []
    lines: list[str] = []
    content: list[str] = []
    ended = False
    for line in text.splitlines(keepends=True):
        if line.strip():
            if ended:
                found.append(Block(text="\n".join(content), raw="".join(lines)))
                lines, content, ended = [], [], False
            content.append(line.rstrip("\r\n"))
        else:
            ended = bool(content)
        lines.append(line)
    if content:
        found.append(Block(text="\n".join(content), raw="".join(lines)))
    return found


def stitch(units: list[Block]) -> str:
    """Blocks back into a document, each contributing its own bytes verbatim.

    Write authority ends at the seam. A block brings the separator it had where
    it came from, so a run of blocks out of one document reproduces that
    document exactly — spacing, whitespace-only lines and all. The only bytes
    this adds are at a joint the merge itself created, where a block that ended
    its own document meets one that followed it nowhere: there it TOPS UP to the
    blank line that keeps two paragraphs two, adding just the shortfall and
    never removing what is already there. Trailing blank lines someone wrote are
    content, not slack to be trimmed on the way past.
    """
    out: list[str] = []
    for unit in units:
        if out and out[-1].splitlines()[-1].strip():
            out.append("\n" if out[-1].endswith("\n") else "\n\n")
        out.append(unit.raw)
    return "".join(out)


#: An edit as both sides can compare it: where it applies in the base, and the
#: paragraphs it puts there. Spacing is deliberately absent — two sides writing
#: the same paragraphs at the same place agree, however each spaced them.
Edit = tuple[int, int, tuple[str, ...]]


def block_edits(base: list[Block], other: list[Block]) -> dict[Edit, tuple[Block, ...]]:
    """`other` as edits against `base`: what each edit says, mapped to the
    blocks that say it — so the comparison is over paragraphs and the emission
    is over the bytes of whichever side supplied them."""
    matcher = difflib.SequenceMatcher(
        None,
        [unit.text for unit in base],
        [unit.text for unit in other],
        autojunk=False,
    )
    return {
        (i1, i2, tuple(unit.text for unit in other[j1:j2])): tuple(other[j1:j2])
        for tag, i1, i2, j1, j2 in matcher.get_opcodes()
        if tag != "equal"
    }


def unchanged_blocks(base: list[Block], live: list[Block]) -> dict[int, Block]:
    """Base positions the live file left alone, as the LIVE document's blocks.

    A region neither side edited is copied out of the operator's file rather
    than out of the base: the two hold the same paragraphs by definition here,
    and the operator's are the bytes that must survive the round trip."""
    matcher = difflib.SequenceMatcher(
        None,
        [unit.text for unit in base],
        [unit.text for unit in live],
        autojunk=False,
    )
    kept: dict[int, Block] = {}
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == "equal":
            for offset in range(i2 - i1):
                kept[i1 + offset] = live[j1 + offset]
    return kept


def repeats_a_neighbour(sequence: list[Block], *, start: int, count: int) -> bool:
    """Whether the `count` blocks at `start` say what the run of blocks
    immediately before them, or immediately after them, already says."""

    def said(first: int, last: int) -> list[str]:
        return [unit.text for unit in sequence[max(first, 0) : max(last, 0)]]

    here = said(start, start + count)
    return here == said(start - count, start) or here == said(start + count, start + 2 * count)


def merge_blocks(base: list[Block], live: list[Block], published: list[Block]) -> list[Block] | None:
    """`live` and `published` reconciled against their common `base`, or None
    where the two sides touched one span differently.

    Both sides' edits are spans of whole paragraphs, so applying them is a
    block-granular three-way merge. Two edits that overlap — including two
    insertions at the same point, which no interval test catches — are a
    conflict unless they say the same thing, in which case the live file's
    blocks are the ones applied, since between two spellings of one paragraph
    the operator's is the one already on disk. A conflict is not resolved here:
    the caller's answer is a different base, or the bail path.

    An insertion whose paragraphs already sit where it would land is dropped:
    inserting them again would say the same thing twice, whatever base put the
    merge in that position. It is the shape a second run takes when the live
    file already carries the update — against the older base still on offer, an
    operator edit next to the applied insertion is one changed span with it, so
    the insertion is no longer recognisable as already present. The copy that
    survives is the one already in place, which is the operator's own bytes.
    """
    ours, theirs = block_edits(base, live), block_edits(base, published)
    for one in ours:
        for other in theirs:
            (o1, o2, _), (t1, t2, _) = one, other
            overlap = max(o1, t1) < min(o2, t2) or o1 == o2 == t1 == t2
            if overlap and one != other:
                return None
    edits = theirs | ours
    kept = unchanged_blocks(base, live)
    merged: list[Block] = []
    inserted: list[tuple[int, int]] = []
    at = 0
    for edit in sorted(edits):
        start, stop, _ = edit
        merged.extend(kept[index] for index in range(at, start))
        if start == stop:
            inserted.append((len(merged), len(edits[edit])))
        merged.extend(edits[edit])
        at = max(at, stop)
    merged.extend(kept[index] for index in range(at, len(base)))
    redundant = {
        index
        for begin, count in inserted
        if repeats_a_neighbour(merged, start=begin, count=count)
        for index in range(begin, begin + count)
    }
    return [unit for index, unit in enumerate(merged) if index not in redundant]


def below_h1(text: str) -> str:
    """`text` from just after its first level-1 heading — all of it when there
    is none. What a concatenation appends: our title is not a second title for
    the operator's file."""
    lines = text.splitlines(keepends=True)
    for index, line in enumerate(lines):
        if line.startswith("# "):
            return "".join(lines[index + 1 :])
    return text


def sections(text: str) -> dict[str, str]:
    """Level-2 section name -> its body, in file order, with everything above
    the first `## ` heading under PREAMBLE_SECTION (dropped when blank).

    The granularity the integration reports at: a `##` section is the unit an
    operator reads and integrates, and a line diff is not."""
    found: dict[str, str] = {}
    name = PREAMBLE_SECTION
    body: list[str] = []
    for line in text.splitlines(keepends=True):
        heading = SECTION_HEADING.match(line)
        if heading:
            found[name] = "".join(body)
            name, body = heading.group(1), []
        else:
            body.append(line)
    found[name] = "".join(body)
    if not found[PREAMBLE_SECTION].strip():
        del found[PREAMBLE_SECTION]
    return found


def section_report(published: str, live: str) -> list[str]:
    """The published baseline against the live file, named section by section:
    what the update adds, what both hold and differ on, what is the operator's
    own, and what already matches."""
    ours, theirs = sections(published), sections(live)
    categories = (
        ("new in the published baseline", [name for name in ours if name not in theirs]),
        ("in both, differing", [name for name in ours if name in theirs and ours[name] != theirs[name]]),
        ("yours alone", [name for name in theirs if name not in ours]),
        ("identical", [name for name in ours if name in theirs and ours[name] == theirs[name]]),
    )
    return [f"  sections {label}: {', '.join(names)}" for label, names in categories if names]


def plan_integration(*, published: str, live: str | None, revisions: list[Revision]) -> Integration:
    """Classify the live file against the published baseline and its history,
    deciding everything before anything is written.

    Base selection is by distance and confirmed by the merge: candidates are
    ordered by how many lines separate them from the live file, and the first
    that merges cleanly is the base. A base that conflicts everywhere was
    probably the wrong pick, so the next candidate is tried rather than the
    nearest one trusted outright.

    A revision equal to the published text is not a candidate. It would merge
    against anything — one side having no changes at all — and the merge it
    produces is the live file unchanged, which is this update silently deciding
    it had nothing to deliver. Excluding it is also what leaves the conflict
    case reachable at all, since the newest revision is normally the published
    one.
    """
    if live is None:
        return Integration("fresh", live_text=published)
    if live == published:
        return Integration("current")
    exact = next((revision for revision in revisions if revision.text == live), None)
    if exact is not None:
        return Integration("published", base=exact, live_text=published)

    candidates = [revision for revision in revisions if revision.text != published]
    ranked = sorted(candidates, key=lambda revision: differing_lines(revision.text, live))
    if not ranked:
        # No history to select from — no git, no repository, an unpublished
        # source, or a baseline with no revision behind its current text. There
        # is no base to merge against, and inventing one is exactly what the
        # bail path exists to refuse.
        return Integration("bail", incoming_text=published)
    if line_matcher(ranked[0].text, live).ratio() < MIN_ANCESTRY_SIMILARITY:
        # Their file entire, then the seam, then ours. Trimming what their file
        # ends with, or imposing a separator on top of one they already wrote,
        # would both be writes on their side of a joint that is only ours from
        # their last byte onward — `stitch` tops up the shortfall and no more.
        # The lstrip is on OUR text, whose leading blank lines we author.
        below = blocks(below_h1(published).lstrip("\n"))
        return Integration("unrelated", live_text=stitch(blocks(live) + below))
    for candidate in ranked:
        merged = merge_blocks(blocks(candidate.text), blocks(live), blocks(published))
        if merged is not None:
            return Integration("merged", base=candidate, live_text=stitch(merged))
    return Integration("bail", base=ranked[0], incoming_text=published)


def integrate_claude_md(*, source: Path, dest: Path) -> bool:
    """Integrate the published operator baseline at `source` into `dest`, a
    CLAUDE.md the operator owns and edits.

    A merge, never an overwrite, and never a marked file: CLAUDE.md has no
    comment syntax a reading model would not also read, so nothing may be
    written into it to delimit what came from here. The merge base is recovered
    from this repository's git history instead, rather than stored beside the
    live file; the one thing this verb does store beside it is a single rolling
    backup of the live file's pre-write bytes, written whenever a write changes
    them and left alone otherwise (see BACKUP_PREFIX).

    Classification is printed before the first byte is written, so an operator
    sees which case they are in and which revision they were matched against —
    a misclassification is visible rather than silent. On the bail path nothing
    is written but incoming.CLAUDE.md, and the return is False: the integration
    the operator asked for did not happen.
    """
    if not source.is_file():
        raise TemplateError(f"published baseline '{rel(source)}' does not exist")
    if dest.exists() and not dest.is_file():
        raise TemplateError(f"DEST '{dest}' is not a file — name the CLAUDE.md itself, not its directory")
    published = source.read_text(encoding="utf-8")
    live = dest.read_text(encoding="utf-8") if dest.is_file() else None
    plan = plan_integration(published=published, live=live, revisions=baseline_revisions(source))

    headline, advice = CASE_REPORT[plan.case]
    print(f"CLAUDE.md integration — {headline}")
    print(f"  live:      {dest}")
    print(f"  published: {rel(source)}")
    if plan.base is not None and live is not None:
        print(f"  base:      {plan.base.label}, {differing_lines(plan.base.text, live)} lines from yours")
    elif live is not None:
        print("  base:      none — no earlier published revision could be read from git history")
    for line in section_report(published, live) if live is not None else []:
        print(line)

    if plan.live_text is not None:
        dest.parent.mkdir(parents=True, exist_ok=True)
        backup = dest.with_name(BACKUP_PREFIX + dest.name)
        if live is not None and plan.live_text != live:
            backup.write_text(live, encoding="utf-8")
            note = f", prior content backed up to {backup} — yours to delete"
        else:
            note = ""
        dest.write_text(plan.live_text, encoding="utf-8")
        print(f"wrote {dest} — {advice}{note}")
    elif plan.incoming_text is not None:
        incoming = dest.with_name(INCOMING_PREFIX + dest.name)
        incoming.write_text(plan.incoming_text, encoding="utf-8")
        print(f"wrote {incoming}, and nothing else — {dest} is untouched. {advice}")
    else:
        print(f"{dest} — {advice}")
    print(
        "reminder: publishing live improvements back into the repo is a manual diff-and-adopt per "
        "user-config/README.md — this verb only installs, it never reads live changes back."
    )
    return plan.case != "bail"


def build_parser() -> argparse.ArgumentParser:
    """The four verbs, each declaring only the flags it can act on.

    A flag a verb does not declare is unrepresentable there rather than
    refused by hand: `install` takes neither `--surfaces` nor a selection glob,
    so a partial install cannot be asked for in the first place.

    Prefix abbreviation is off on every parser built here, main and subcommand
    alike: it would silently keep a renamed flag alive as a prefix of the
    spelling that replaced it, whose value means something else entirely, so a
    rename would land as a wrong-argument bug instead of an unknown-flag error.
    """
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument(
        "root",
        type=Path,
        metavar="ROOT",
        help="the output root: an existing directory, whose agents/ and "
        "commands/ subtrees this run writes or reads. There is no "
        "in-repository default — this repository keeps no rendered tree",
    )
    common.add_argument(
        "--family",
        metavar="NAME",
        default=DEFAULT_FAMILY,
        help=f"the model family whose tuning applies (default: {DEFAULT_FAMILY}): "
        "a bare family name resolved against templates/family/, or a path to a "
        "family file. Bare MODEL names are not valid values — the family's "
        "required [tiers] table names its members. See templates/family/README.md",
    )
    common.add_argument(
        "--model-tier-map",
        metavar="SPEC",
        help="override the tier -> family member map the family declares: "
        "comma-separated tier=member pairs, or all=member "
        "(--model-tier-map medium=haiku). Named tiers mask only themselves; "
        "the rest keep the family's own [tiers] value. Drives which member's "
        "overlay overrides render, never the rendered pin text",
    )
    common.add_argument(
        "--model-pin-map",
        metavar="SPEC",
        help="override the tier -> rendered pin map: the same syntax, over "
        "this tool's DEFAULT_PIN_MAP (--model-pin-map all=haiku). Pin text is "
        "always in the claude model namespace, and is the sole source of the "
        "five @!dyn.tier-*!@ tokens' text",
    )
    common.add_argument("--verbose", "-v", action="store_true", help="list every file")

    selection = argparse.ArgumentParser(add_help=False)
    selection.add_argument(
        "--surfaces",
        choices=("agents", "commands", "both"),
        # No default: the selection globs imply their surfaces, so combining
        # the two flags has to be distinguishable from not passing this one.
        help="which surface to render or check (default: both). Not combinable "
        "with --agent-glob/--command-glob, which imply their surfaces",
    )
    selection.add_argument(
        "--agent-glob",
        metavar="PATTERNS",
        help="restrict the run to the agents outputs matching PATTERNS — one "
        'or more fnmatch patterns joined by "|", matched against the '
        "surface-relative output path without its .md suffix "
        '(--agent-glob "*app-expert*|*-coder*"). Implies --surfaces agents '
        "unless --command-glob is given too; a pattern matching nothing is an "
        "error",
    )
    selection.add_argument(
        "--command-glob",
        metavar="PATTERNS",
        help="the same, over the commands surface",
    )

    parser = argparse.ArgumentParser(
        description="Generate, check and install the generated agent/command definitions, and "
        "integrate the published operator baseline into an operator's own CLAUDE.md.",
        allow_abbrev=False,
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    verbs = parser.add_subparsers(dest="verb", required=True)

    verbs.add_parser(
        "generate",
        parents=[common, selection],
        allow_abbrev=False,
        help="render the definitions into ROOT's two surfaces",
        description="Render every declared definition into ROOT's agents/ and commands/ "
        "surfaces, under the tuning triple this invocation names. The surface "
        "directories, and the mirrored subdirectories beneath them, are created "
        "as needed; the per-target write-safety table decides what may be "
        "overwritten.",
    )

    check = verbs.add_parser(
        "check",
        parents=[common, selection],
        allow_abbrev=False,
        help="check the definitions under ROOT against what the templates render",
        description="Check the tree under ROOT: every target byte-identical to what its "
        "template currently renders under this invocation's tuning triple, and "
        "every banner naming a template that exists and declares it. A target "
        "whose banner claims a different triple is reported MISTUNED, so "
        "validating a tuned set means checking it with that set's own flags.",
    )
    check.add_argument(
        "--no-diff",
        action="store_true",
        help="report drift without printing the diff",
    )

    verbs.add_parser(
        "install",
        parents=[common],
        allow_abbrev=False,
        help="full-product install into ROOT (a project's existing .claude directory)",
        description="Install the whole product into ROOT: render both deployed surfaces and "
        "copy the shipped packages, minus test suites and caches, stamping each "
        "copied file with an !INSTALLED! banner. The render runs under whatever "
        "tuning triple the install was given. An installed tree is an artifact: "
        "re-install to update it, and local edits to installed files are "
        "replaced — not preserved, though the prior content of any file this "
        "tool did not write is kept beside it as a numbered *.bak, yours to "
        "delete. Reports by exception: a clean install is one summary line, and "
        "-v lists every file.",
    )

    claude_md = verbs.add_parser(
        "install-claude-md",
        allow_abbrev=False,
        help="integrate the published operator baseline into an operator's own CLAUDE.md",
        description="Merge user-config/INSTALLED_CLAUDE.md into the CLAUDE.md at DEST, which "
        "an operator owns and edits. The merge base is recovered from this "
        "repository's git history — nothing is stored beside the live file and "
        "nothing is ever written into it to mark a region. The case is "
        "classified and reported, section by section, before anything is "
        "written; a live file no recovered base merges cleanly against is left "
        "untouched, with the baseline written beside it as incoming.CLAUDE.md "
        "and a nonzero exit.",
    )
    claude_md.add_argument(
        "dest",
        type=Path,
        metavar="DEST",
        help="the CLAUDE.md to integrate into (~/.claude/CLAUDE.md, or a project's own). "
        "Its parent directory is created if absent; a missing file is installed whole",
    )
    claude_md.add_argument(
        "--source",
        type=Path,
        default=PUBLISHED_BASELINE,
        metavar="PATH",
        help=f"the published baseline to integrate (default: {rel(PUBLISHED_BASELINE)}). "
        "Its own git history is where the merge bases come from",
    )
    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()

    try:
        if args.verb == "install-claude-md":
            # A verb of its own entirely: no templates, no surfaces, no tuning
            # triple. It is the one write in this tool that lands in a file its
            # operator maintains, which is why it merges rather than renders.
            sys.exit(0 if integrate_claude_md(source=args.source, dest=args.dest) else 1)

        # An install delivers the product entire, so it declares neither flag
        # and covers both surfaces unconditionally.
        globs: GlobMap = {}
        surfaces = "both"
        if args.verb != "install":
            globs = {
                surface: split_globs(spec)
                for surface, spec in (("agents", args.agent_glob), ("commands", args.command_glob))
                if spec is not None
            }
            if globs and args.surfaces is not None:
                parser.error(
                    "--agent-glob/--command-glob already select their surface(s) — drop --surfaces, which they subsume"
                )
            # The globs imply the surfaces they cover; without them, --surfaces
            # (or its default) does.
            surfaces = ("both" if len(globs) > 1 else next(iter(globs))) if globs else (args.surfaces or "both")

        smap = surface_map(args.root, surfaces=surfaces)
        if globs:
            validate_selection(output_keys(smap), globs)
        chunks = load_chunks()
        # A family is always loaded — --family defaults rather than being
        # absent — so there is no untuned path left to branch on.
        family_path = resolve_family(args.family)
        family = load_family(family_path)
        tuning = effective_tuning(
            family_path,
            family,
            tier_spec=args.model_tier_map,
            pin_spec=args.model_pin_map,
        )
        # Anchors are collected across ALL templates and chunks, not just the
        # --surfaces selection, so a filtered render never miscalls a real
        # anchor unknown. The second surface map is built for its template dirs
        # alone, so reusing this run's root keeps it an existing one.
        validate_family_anchors(family.entries, collect_anchors(chunks, templates(surface_map(args.root))))
        validate_family_members(family, tuning.tier_map, family_path)
        binding = tier_binding(chunks, pin_map=tuning.pin_map)
        overlays = tier_resolver(family.entries, tuning.tier_map)

        report_tuning(tuning)
        report_divergence(tuning)
        report_tuned(tuning)

        if args.verb == "install":
            ok = install(binding, smap, root=args.root, overlays=overlays, tuning=tuning, verbose=args.verbose)
            report_overlays(family.entries, overlays(None), tuning)
        elif args.verb == "generate":
            ok = generate(binding, smap, overlays=overlays, tuning=tuning, globs=globs, verbose=args.verbose)
            report_overlays(family.entries, overlays(None), tuning)
        else:
            ok = check(
                binding,
                smap,
                overlays=overlays,
                tuning=tuning,
                globs=globs,
                verbose=args.verbose,
                show_diff=not args.no_diff,
            )
        if globs:
            report_selection(output_keys(smap), globs)
    except TemplateError as exc:
        print(f"error: {exc}", file=sys.stderr)
        sys.exit(2)

    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
