# templates/family/ — model-family tuning files

One TOML file per model family, named for the family (`claude.toml`,
`gemma-4.toml`) and selected by `--family NAME`: a family name resolved against
this directory, or a path to a family file. There is no third resolution step —
a bare model name is not a family name.

This is the authoring guide for the files that live here. `gen-defs.py`'s module
docstring is the definitive description of the mechanism they feed.

## Schema

```toml
[tiers]
highest = "<member>"      # required, and exactly these five keys
high    = "<member>"
medium  = "<member>"
low     = "<member>"
lowest  = "<member>"

# <key>: <observed behavior this overlay corrects, against which model, when>
[family.<key>]
text = "family-wide overlay text"

# <member>: <observed behavior motivating the override, when>
[family.<key>.models.<member-name>]
text = "override text for that member"
```

**`[tiers]` is required and total.** It names the family member staffing each of
the five tiers. A file that omits the table, drops a tier, or carries a key
outside the five does not load — never a fallback to a partial map, to another
family's members, or to the pin map. A family name is still reservable before
any observed failure motivates tuning: zero `[family.*]` tables is legal, but the
five-line `[tiers]` table is what makes the file load at all.

Two tiers staffed by one member is legal — a statement about that family's
ladder, not a defect.

**Anchors are consumed through a namespace.** A template or chunk spells one
`@!fam.<key>!@`, where `fam` is the namespace naming this file as the source
that fills it — the only overlay namespace registered today
(`OVERLAY_NAMESPACES` in `gen-defs.py`). A table here supplies the key:
`[family.gap-aversion]` fills `@!fam.gap-aversion!@`. The table name stays
`family`, because this file is where the text is *authored*; `fam` is how a
template *reads* it. The key matches the same kebab-case identifier class every
marker name does. An anchor must exist in some template or chunk; naming an
anchor nothing authors is a hard error, so a renamed or deleted anchor cannot
leave a family file silently filling nothing.

**Member names** under `[family.<key>.models.<member>]` must be reachable — spelled
as some tier reaches them, in this file's `[tiers]` or in an effective
`--model-tier-map`. An unreachable member is a hard error rather than a table
that quietly never fires, which makes `[tiers]` the single source of truth for
member spelling.

## Two maps, and only one of them is yours

| Map | Source | What it decides |
|---|---|---|
| tier → member | this file's `[tiers]`, masked per tier by `--model-tier-map` | which member's `[family.*.models.*]` overrides a definition is tuned against |
| tier → pin | `DEFAULT_PIN_MAP` in `gen-defs.py`, masked per tier by `--model-pin-map` | the `model:` text a definition renders, always a claude-legal name |

The two are independent on purpose: what a definition is tuned for and what it
dispatches on are separately chosen. No member name ever reaches rendered text —
nothing in this file sources a `model:` value. On a non-claude family the two
maps therefore differ at every tier by construction, which is that render's
normal state rather than a problem to reconcile.

## Tuned, Stock, and the load error

Per tier, what this file holds decides what that tier's definitions get:

| In the file | What renders | What the run says |
|---|---|---|
| the tier's member has `[family.<key>.models.<member>]` tables | member text wins over family-wide text, per anchor | the tier drops out of the banner's `stock=` list |
| the tier's member has **zero** such tables anywhere in the file | family-wide `text` only | one stock notice per run; the tier is named in `stock=` |
| `[tiers]` absent, incomplete, or holding an unknown key | nothing — the file does not load | a hard error naming the five tiers |

Stock is a legal reported state, not a warning: it is where a family sits until
probe data motivates an override, and the notice says so. A member holding a
table for one anchor and not another is **not** stock — that anchor simply falls
back to family-wide text.

## One overlay per anchor — most specific wins

Resolution, not accumulation: at most one overlay renders per anchor. A table
matching the member the definition's tier maps to wins outright; otherwise the
family-wide `text` renders; otherwise the anchor renders as nothing. Family and
member texts are never concatenated. The text renders verbatim in place, with no
lead-in and no wrapper — any lead-in belongs in the text itself — and no family
or member name appears in rendered output, so this file is the provenance
record.

## Never touches base

A family file can only fill anchors. It has no vocabulary for replacing,
suppressing, or modifying base template or chunk text, and an anchor no loaded
family fills expands to nothing at all. Base text is tuned by editing templates
or `templates/shared-chunks.toml`, never from here.

## Provenance discipline

Anchors are authored on demand, when an observed failure motivates one — never
pre-sprinkled speculatively. Per the defensive-clause doctrine in README.md
("Variants and platform compatibility"): strip first, observe, patch. Every
entry in a family file carries a TOML comment recording the observed behavior it
corrects, against which model it was observed, and when — not what the text
says, but why it exists. Without that record, future maintainers cannot
distinguish "still load-bearing" from "residue from a model we don't use
anymore."

`gemma-4.toml` is the first motivated family file (Gemma 4 31B-it silently
filling axiom gaps, probe data 2026-04-29; provenance in its entry comments).
`claude.toml` carries its `[tiers]` table and no entries — the family name
reserved, and the shape a future entry takes sketched in comments, awaiting an
observed failure before it carries any tuning.
