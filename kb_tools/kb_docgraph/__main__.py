"""The build's command line: volume roots in, a tree and a report out.

``--source`` is repeatable and every path it names is a **volume root** — a
top-level LaTeX document. Nothing is discovered: no directory walk, no glob, and
no inference about which of a directory's ``.tex`` files are roots. Separating
top-level documents from included ones automatically is a real feature and a
deferred one; until it exists the caller states the answer, because the cost of
getting it wrong is silent. A file reached by ``\\input`` converts twice when it
is also named, and each copy passes its own volume's checks.
"""

import argparse
import logging
import sys
from pathlib import Path

from . import build

EXIT_OK = 0
EXIT_GATE_FAILED = 1
EXIT_USAGE = 2


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="kb_tools.kb_docgraph", description=__doc__)
    parser.add_argument(
        "--source",
        action="append",
        required=True,
        type=Path,
        metavar="ROOT",
        help="a volume root; repeat once per volume, never for an \\input-ed file",
    )
    parser.add_argument(
        "--bibliography",
        action="append",
        type=Path,
        default=None,
        metavar="BIB",
        help="a .bib citations resolve against; repeat once per file. The reader merges them and renders "
        "only entries this corpus cites, so a file carrying uncited works costs nothing. Given none, every "
        "citation still reaches the tree carrying its own key and there is no references leaf to cut. Where "
        "one key is defined twice the first file given wins, so the order is the caller's",
    )
    parser.add_argument("--kb-root", required=True, type=Path, help="the directory the tree is written into")
    parser.add_argument("--verbose", action="store_true", help="log the reader's stderr and the rest at INFO")
    arguments = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO if arguments.verbose else logging.WARNING, format="%(message)s")

    bibliographies = arguments.bibliography or []
    missing = [str(path) for path in (*arguments.source, *bibliographies) if not path.is_file()]
    if missing:
        print(f"no such file: {', '.join(missing)}", file=sys.stderr)
        return EXIT_USAGE

    report = build(
        volume_roots=[path.resolve() for path in arguments.source],
        bibliographies=[path.resolve() for path in bibliographies],
        kb_root=arguments.kb_root.resolve(),
    )
    print("\n".join(report.lines()))
    return EXIT_GATE_FAILED if report.failed else EXIT_OK


if __name__ == "__main__":
    raise SystemExit(main())
