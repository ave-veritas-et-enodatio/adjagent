"""Runtime query interface over the KB derived index (Phase 3 consumer).

The query side reads the on-disk JSONL artifacts under the KB's ``.index/``
directory and exposes question-shaped lookups via :class:`Index`. Path
resolution is shared with the build side (``kb_tools/``) through the
``kb_util`` module — the single source of KB path truth.

``kb_cmd`` is a normal importable package. The CLI entry point is ``python -m
kb_tools.kb_cmd``; for programmatic access import the loader directly::

    from kb_tools.kb_cmd import load
    idx = load()
    idx.depends_on("0ktpcn")
"""

from .index import CitationEdge, Claim, DependsOnEdge, Index, StrengthenByItem, SubtreeAggregate, load

__all__ = [
    "Claim",
    "CitationEdge",
    "DependsOnEdge",
    "Index",
    "StrengthenByItem",
    "SubtreeAggregate",
    "load",
]
