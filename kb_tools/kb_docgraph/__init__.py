"""The document graph: LaTeX volumes in, a navigable Markdown hierarchy out.

This package builds the KB's tree and stops there. No claim graph, no metadata,
no inference — the claim-graph builder takes this tree as its **sole input**, and
that split is the whole point: any stage that produces a conforming tree can
replace this one.

Four stages, each mechanical:

* **1, :mod:`convert`** — one volume root, one source pre-pass, two artifacts:
  pandoc's JSON AST and the whole-volume Markdown, both under the same Lua
  filter so their header sequences agree.
* **2, :mod:`outline`** — zip the AST's ``Header`` sequence against the
  Markdown's headings, split on the latter, and build the tree. The AST supplies
  level and label, the Markdown supplies content, and nothing is re-rendered
  from an AST slice.
* **3, :mod:`build`** — write the tree and its image assets, then run the gates.
  A failure stops.
* **the seam, :mod:`judge`** — where "is this decomposition good enough?" will be
  asked. Stubbed: it accepts everything.

:mod:`walk` and :mod:`text` are the two readings the stages share, and
:mod:`partition` is the pair of checks that make the content claims enforceable
rather than aspirational.

**Everything that reaches the pandoc binary goes through** :mod:`kb_tools.pandoc`.
Nothing here names it, builds an argv for it, or shells out to it.
"""

from .build import Report, build

__all__ = ["Report", "build"]
