#!/usr/bin/env python3
"""
Step 0 of BHL Tier 2 — pull the 8 small TSV dumps from BHL's open S3 bucket.

Total ~92 MB gzipped. Resume-safe: skips files already on disk at expected size.
"""

from __future__ import annotations

import sys
import urllib.request
from pathlib import Path
from corpus.config import RUNTIME
from corpus.config import user_agent
import argparse

DUMPS = RUNTIME / "bhl_dumps"
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

UA = user_agent()


def expected_size(url: str) -> int | None:
    try:
        req = urllib.request.Request(url, method="HEAD", headers={"User-Agent": UA})
        with urllib.request.urlopen(req, timeout=30) as r:
            cl = r.headers.get("Content-Length")
            return int(cl) if cl else None
    except Exception:
        return None


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

    print(f"  → {dest.name} ({expected/1024/1024:.1f} MB)", flush=True)
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    tmp = dest.with_suffix(dest.suffix + ".part")
    with urllib.request.urlopen(req, timeout=120) as r, open(tmp, "wb") as f:
        while True:
            chunk = r.read(1024 * 1024)
            if not chunk:
                break
            f.write(chunk)
    tmp.replace(dest)
    print(f"    saved {dest.stat().st_size/1024/1024:.1f} MB")


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
