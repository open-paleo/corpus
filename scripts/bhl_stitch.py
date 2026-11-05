#!/usr/bin/env python3
# corpus-command: bhl stitch | stitch BHL page images into PDFs
"""
Stitch the article-level JP2s for each BHL key into a single PDF at
pdfs/{key}.pdf. corpus.pdf.build (img2pdf) embeds the JP2 stream unchanged, one
page per image, at 72 DPI.

After stitching, updates manifest[key].acquisition.pdf_path.

Usage:
  ./.runtime/venv/bin/python scripts/bhl_stitch.py [--key K]... [--force]
"""
from __future__ import annotations

import argparse
import sys
from datetime import datetime, timezone

from corpus.bhl import stitch_jp2s
from corpus.config import ROOT as ARCHIVE, PDF_DIR
from corpus.manifest import commit as manifest_commit, load as manifest_load
from corpus.keys import key_arg

JP2_DIR = ARCHIVE / "bhl_images"
def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--key", type=key_arg, action="append", default=[])
    ap.add_argument("--force", action="store_true",
                    help="Re-stitch even if pdfs/{key}.pdf exists.")
    args = ap.parse_args()

    if args.key:
        keys = args.key
    else:
        keys = sorted(p.name for p in JP2_DIR.iterdir()
                      if p.is_dir() and list(p.glob("page-*.jp2")))
    print(f"Candidates: {len(keys)} BHL keys with JP2s")

    manifest = manifest_load()
    PDF_DIR.mkdir(exist_ok=True)
    n_ok = n_skip = n_err = 0
    total_pp = 0
    total_bytes = 0
    now = datetime.now(timezone.utc).isoformat()

    for k in keys:
        pdf_path = PDF_DIR / f"{k}.pdf"
        if pdf_path.exists() and not args.force:
            n_skip += 1
            print(f"  [skip] {k} (already stitched)")
            continue
        try:
            n_pp, n_bytes = stitch_jp2s(JP2_DIR / k, pdf_path)
        except Exception as e:
            n_err += 1
            print(f"  [err]  {k}: {e}")
            continue
        if n_pp == 0:
            n_err += 1
            print(f"  [err]  {k}: no JP2s found")
            continue
        # Update manifest
        if k in manifest:
            acq = manifest[k].setdefault("acquisition", {})
            acq["pdf_path"] = f"pdfs/{k}.pdf"
            acq["pdf_stitched_at"] = now
            acq["pdf_source"] = f"stitched from {n_pp} BHL JP2s"
        n_ok += 1
        total_pp += n_pp
        total_bytes += n_bytes
        print(f"  [ok]   {k:<25s} {n_pp:>4d}p  {n_bytes/1024/1024:>6.1f} MB")

    if n_ok:
        manifest_commit(manifest)
    print()
    print(f"Done: ok={n_ok}, skip={n_skip}, err={n_err}")
    print(f"  total: {total_pp} pages, {total_bytes/1024/1024:.0f} MB across {n_ok} PDFs")
    return 0 if n_err == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
