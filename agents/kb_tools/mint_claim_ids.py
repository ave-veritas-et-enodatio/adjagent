#!/usr/bin/env python3
"""Mint N fresh, collision-checked ``clm-`` ids for new claims.

Usage::

    python3 -m kb_tools.mint_claim_ids [N]   # default N=1

Existing ids are collected from every ``claim-quality.md`` register in the KB
(via ``kb_index_lib.collect_known_claim_ids``) so a minted id never collides
with one already authored. The id grammar and minting live in ``kb_schema``.
"""

import sys

from kb_tools import kb_index_lib, kb_schema, kb_util


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    n = int(argv[0]) if argv else 1
    try:
        kb = kb_util.kb_root()
    except kb_util.RepoRootError as exc:
        print(f"FAIL: {exc}", file=sys.stderr)
        return 2
    existing = set(kb_index_lib.collect_known_claim_ids(kb))
    minted: list[str] = []
    for _ in range(n):
        minted.append(kb_schema.mint_id("clm", existing=existing | set(minted)))
    print("\n".join(minted))
    return 0


if __name__ == "__main__":
    sys.exit(main())
