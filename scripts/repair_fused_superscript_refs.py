#!/usr/bin/env python3
"""Command-line entry for corpus.repair.fused_calls (see that module for what it
does). `corpus repair fused_calls` runs the same code, and `corpus repair
all` runs every repair in one pass.
"""
from corpus.repair.fused_calls import *  # noqa: F401,F403  (names other scripts import)
from corpus.repair.fused_calls import main

if __name__ == "__main__":
    raise SystemExit(main())
