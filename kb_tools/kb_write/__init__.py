"""kb_write — the metadata write API.

Module map:

* :mod:`render` — the ONLY place metadata bytes are composed: register entries
  (claim and support), the kb-frontmatter block, id and Tier-2 markers,
  structured bullets, prose normalization, derived-field placeholders, and the
  citation form. Values in, text out — total, pure, and path-free.
* :mod:`values` — the values-file grammar (TOML) and the closed per-op field
  vocabulary: parse, domain-check against inherited bounds, and refuse with a
  located message. Answers "is this a well-formed value?" and nothing else.
* :mod:`store` — read-modify-write over the authored Markdown: path
  containment, the census, locate/splice, the temp write, the readback proof on
  the temp, the optimistic-concurrency check, and the atomic replace. Returns
  written / refused / retry as data; exit codes are ``ops``'s.
* :mod:`ops` — op semantics: the validation ladder in order, mint fusion, the
  report lines, and the outcome-to-exit-code mapping. One function per op, an
  ``OPS`` registry for a surface to bind, and no ``argparse``, ``sys.argv`` or
  ``sys.exit`` anywhere in it.

This package's ``__init__`` deliberately imports nothing from its own modules,
following :mod:`kb_tools.kb_survey`'s precedent. :mod:`render` depends on
:mod:`kb_tools.kb_schema` alone; the modules that join it later
(``store``/``ops``) reach for :mod:`kb_tools.kb_index_lib`, and an ``__init__``
that re-exported them would drag the parser tree in behind every ``render``
import — and, because ``store`` imports ``kb_index_lib`` while nothing in
``kb_index_lib`` may import back, would put a partially-initialized package on
the only path where a cycle could form.
"""
