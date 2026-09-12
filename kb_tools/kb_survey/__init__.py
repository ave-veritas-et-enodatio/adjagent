"""kb_survey — the manifest schema and the checks over a derived KB tree.

Module map:

* :mod:`manifest` — the schema: record types, ``FlagCode``, writer, reader,
  the atomic write, and the two joins the reader owns.
* :mod:`skeleton` — the derivation: manifest in, KB tree out.
* :mod:`validate` — the checks over (manifest, skeleton, tree).

This package's ``__init__`` deliberately imports nothing from its own
modules; a consumer imports the module it needs directly.
"""
