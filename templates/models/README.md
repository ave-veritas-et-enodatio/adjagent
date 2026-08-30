# templates/models/ — model-family NB files

Maintainer documentation for the model-tuning mechanism (`gen-defs.py`'s
docstring is the definitive mechanism description; this is the authoring
guide for the files that live here). One TOML file per model family; a file
is loaded with `--model-family FILE`, and `--model NAME` activates that
model's overrides within it. `--model-family` also takes a bare family name,
resolved here as `<name>-addenda.toml` or `<name>.toml` (both matching or
neither is an error), and a bare `--model` without `--model-family` implies
the unique family whose `[nb.*.models.*]` tables mention that model — a
model appearing only in comments is not known. Rendered sets are usually
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

Resolution, not accumulation: at most one NB renders per anchor. With
`--model NAME`, a matching `models.<NAME>` entry wins outright; otherwise the
family `text` renders; otherwise the anchor renders as nothing. Family and
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
observed failure before it carries any entry.
