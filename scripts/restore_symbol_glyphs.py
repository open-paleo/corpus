#!/usr/bin/env python3
"""Command-line entry for corpus.repair.symbols (see that module for what it
does). `corpus repair symbols` runs the same code, and `corpus repair
all` runs every repair in one pass.
"""
from corpus.repair.symbols import *  # noqa: F401,F403  (names other scripts import)
from corpus.repair.symbols import main

if __name__ == "__main__":
    raise SystemExit(main())
