# templates/models/ — model-family NB files

Maintainer documentation for the model-tuning mechanism (`gen-defs.py`'s
docstring is the definitive mechanism description; this is the authoring
guide for the files that live here). One TOML file per model family, reached
through the one tuning flag: `--model-family SPEC`.

## Resolving a SPEC

Family and model are one flag because they are one choice — a model is only
ever reachable through the family that declares it. SPEC resolves in three
steps, against this directory:

1. **A path** — anything containing a path separator or ending in `.toml`:
   that file, taken as given. The escape hatch for a family file named
   outside the conventions below.
2. **A bare family name** — `<name>-addenda.toml` or `<name>.toml` here:
   that family, asking for no model scope on the definitions that carry no
   frontmatter pin.
3. **A bare model name** — declared in exactly one family's
   `[nb.*.models.<name>]` tables: that family, with `<name>` as the export
   flavor for those unpinned definitions (see "One NB per anchor" below).

A name matching both a family file and a model is an ambiguity error naming
both readings; two family files matching the same name is the same error over
the two candidates; a model declared in several families demands the family
file path instead; a name matching neither lists the family files and each
one's known models. A model appearing only in comments is not declared — a
family's models are exactly its real tables. Rendered sets are usually
produced out of repo via `--output-dir`.

## Schema

```toml
# <anchor>: <observed behavior this NB corrects, against which model, when>
[nb.<anchor-name>]
text = "family-wide NB text"

# <model>: <observed behavior motivating the override, when>
[nb.<anchor-name>.models.<model-name>]
text = "override text for that model"
```

Each `[nb.<anchor-name>]` table fills the `@@nb name="<anchor-name>"@@`
anchor. `text` at the table's top level fills it family-wide; a
`models.<model-name>` sub-table overrides the family text for that model.
Anchor names match `[a-z0-9-]+` and must exist in some template or chunk —
naming an anchor nothing authors is a hard error, so a renamed or deleted
anchor cannot leave a family file silently filling nothing.

## One NB per anchor — most specific wins

Resolution, not accumulation: at most one NB renders per anchor. A matching
`models.<NAME>` entry wins outright; otherwise the family `text` renders;
otherwise the anchor renders as nothing. Which `<NAME>` applies is the
definition's own business: each output resolves model scope against its own
frontmatter `model:` pin, so a definition pinned to `opus` takes
`models.opus` whatever the SPEC says, and a pin no table matches quietly
takes the family text instead — a model SPEC is the export flavor for the
definitions that carry no pin at all. Family and
model texts are never concatenated. The rendered form is `**NB**: <text>` —
no family or model name appears in rendered output; this file is the
provenance record.

## Never touches base

A family file can only fill anchors. It has no vocabulary for replacing,
suppressing, or modifying base template or chunk text — a base render (no
`--model-family`) is byte-identical whether or not anchors exist. Base text
is tuned by editing templates or `shared-sections.toml`, never from here.

## Provenance discipline

Anchors are authored on demand, when an observed failure motivates one —
never pre-sprinkled speculatively. Per the defensive-clause doctrine in
README.md ("Variants and platform compatibility"): strip first, observe,
patch. Every entry in a family file carries a TOML comment recording the
observed behavior it corrects, against which model it was observed, and when
— not what the text says, but why it exists. Without that record, future
maintainers cannot distinguish "still load-bearing" from "residue from a
model we don't use anymore."

`gemma-4-addenda.toml` is the first motivated family file (Gemma 4 31B-it
silently filling axiom gaps, probe data 2026-04-29; provenance in its entry
comments). `claude-addenda.toml` is a comments-only reservation — a
zero-entry family file loads cleanly and fills nothing — awaiting an
observed failure before it carries any entry. Neither declares a model in a
real `[nb.*.models.*]` table today, so both are reached by family name
(`gemma-4`, `claude`) or by path; the model step of the resolution above
opens only once an observed per-model failure motivates an override.
