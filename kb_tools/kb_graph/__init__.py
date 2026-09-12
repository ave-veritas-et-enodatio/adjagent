"""kb_graph — the claim-graph SVG renderer.

Module map:

* :mod:`model` — graph assembly from the loaded index: the node table, the
  deduped edge set, the defects decidable without geometry, and domain
  attribution. No file I/O, no geometry, no markup.
* :mod:`layout` — pure: model to placed geometry. Layering and the cycle
  residual pass, in-layer ordering, coordinates, and hop detection with
  hop-side selection. No markup, no colour, no id-to-URL knowledge.
* :mod:`style` — every presentation constant: pitches, box padding, the
  character advance and the pinned font, the label cap, the band palette, the
  dash patterns, the hop radius, and the declared order tuples. Imports
  :mod:`kb_tools.kb_schema` for the band ladder and nothing else.
* :mod:`svg` — pure: placed geometry plus style to an element tree. The only
  module that composes markup, and the home of the single number formatter.
  Computes no coordinate.
* :mod:`ops` — op semantics: index discovery, load, domain selection, report
  lines, and outcome to exit code. One function the surface binds, with no
  argparse, no ``sys.argv`` and no ``sys.exit`` in it.

This package's ``__init__`` deliberately imports nothing from its own modules,
following :mod:`kb_tools.kb_survey` and :mod:`kb_tools.kb_write`. :mod:`model`
depends on :mod:`kb_tools.kb_cmd.index` alone; the modules that join it later
reach for ``xml.etree`` and for ``kb_util``'s path helpers, and an ``__init__``
that re-exported them would drag the serializer and repo-root discovery in
behind every ``model`` import.

Stdlib only.
"""
