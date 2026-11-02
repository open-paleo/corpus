#!/usr/bin/env python3
"""
Step 0 of BHL Tier 2 — pull the 8 small TSV dumps from BHL's open S3 bucket.

Total ~92 MB gzipped. Resume-safe: skips files already on disk at expected size.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from corpus.bhl import DUMPS
from corpus.fetch import http
S3 = "https://bhl-open-data.s3.amazonaws.com/data"

FILES = [
    "title.txt.gz",
    "item.txt.gz",
    "part.txt.gz",
    "partpage.txt.gz",
    "doi.txt.gz",
    "partidentifier.txt.gz",
    "creator.txt.gz",
    "partcreator.txt.gz",
]

def expected_size(url: str) -> int | None:
    cl = http.head(url).headers.get("Content-Length")
    return int(cl) if cl and cl.isdigit() else None


def download(url: str, dest: Path) -> None:
    expected = expected_size(url)
    if dest.exists():
        if expected is not None and dest.stat().st_size == expected:
            print(f"  ✓ {dest.name} ({dest.stat().st_size/1024/1024:.1f} MB) already complete")
            return
        if expected is None and dest.stat().st_size > 0:
            print(f"  ? {dest.name} exists, server didn't report size; assuming complete")
            return
        print(f"  ! {dest.name} exists but wrong size ({dest.stat().st_size} != {expected}); redownloading")

    print(f"  → {dest.name} ({(expected or 0)/1024/1024:.1f} MB)", flush=True)
    n, err = http.download(url, dest)
    if err:
        raise SystemExit(f"    download failed: {err}")
    print(f"    saved {n/1024/1024:.1f} MB")


def main() -> int:
    # No options: --help prints this and exits instead of starting a run.
    argparse.ArgumentParser(description=__doc__,
                            formatter_class=argparse.RawDescriptionHelpFormatter).parse_args()
    DUMPS.mkdir(parents=True, exist_ok=True)
    print(f"Downloading BHL TSV dumps to {DUMPS}/")
    for fname in FILES:
        download(f"{S3}/{fname}", DUMPS / fname)
    total = sum(p.stat().st_size for p in DUMPS.glob("*.txt.gz"))
    print(f"\nDone. Total on disk: {total/1024/1024:.1f} MB")
    return 0


if __name__ == "__main__":
    sys.exit(main())
