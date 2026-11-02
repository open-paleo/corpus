#!/usr/bin/env python3
"""Command-line entry for corpus.repair.ref_links (see that module for what it
does). `corpus repair ref_links` runs the same code, and `corpus repair
all` runs every repair in one pass.
"""
from corpus.repair.ref_links import *  # noqa: F401,F403  (names other scripts import)
from corpus.repair.ref_links import main

if __name__ == "__main__":
    raise SystemExit(main())
