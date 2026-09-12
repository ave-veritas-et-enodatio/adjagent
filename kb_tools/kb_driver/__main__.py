"""Module entry point so ``python3 -m kb_tools.kb_driver <args>`` works."""

import sys

from .cli import main

if __name__ == "__main__":
    sys.exit(main())
