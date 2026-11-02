#!/usr/bin/env python3
"""Command-line entry for corpus.repair.dash (see that module for what it
does). `corpus repair dash` runs the same code, and `corpus repair
all` runs every repair in one pass.
"""
from corpus.repair.dash import *  # noqa: F401,F403  (names other scripts import)
from corpus.repair.dash import main

if __name__ == "__main__":
    raise SystemExit(main())
