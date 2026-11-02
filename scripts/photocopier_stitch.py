#!/usr/bin/env python3
# corpus-command: pdf photocopier | stitch phone photos of a volume
"""Stitch source_images/{key}/ page photos into pdfs/{key}.pdf with a
"photocopier" effect.

This is the /stitch companion for the recurring case of iPhone/HEIC photos of
a physical volume where the pages have a warm/yellow cast and uneven lighting.
The effect is a **grayscale flat-field illumination normalization**: each page
is grayscaled, divided by a large-Gaussian-blur estimate of its own background
(which cancels the paper cast + lighting gradient so the background goes
uniformly bright), then contrast-stretched so the paper clips to white. It is
deliberately NOT a 1-bit threshold — figure/plate grayscale (halftone stipple,
pen-and-ink hatching) is preserved, only the background is whitened.

For volume photos where only a page subset belongs to {key} (e.g. one chapter
of a multi-paper memoir), pass --select START END to stitch an inclusive,
lexically-sorted filename range only.

Output is grayscale JPEG (q88) capped at --max-dim px long edge, wrapped into
a PDF via corpus.pdf.build (img2pdf). Updates manifest[key].acquisition (pdf_stitched_at,
pdf_source) if the key exists — like scripts/stitch_source_images.py, it does
NOT set acquisition.source (that's /intake's job).

Usage:
  ./.runtime/venv/bin/python scripts/photocopier_stitch.py --key KEY
  ./.runtime/venv/bin/python scripts/photocopier_stitch.py --key KEY \
      --select IMG_0612 IMG_0620     # inclusive filename range (a chapter)
  # tune the effect (rarely needed; defaults tuned on 1930s-70s letterpress):
  ...  --white 0.93 --black 0.55 --gamma 1.15
"""
from __future__ import annotations

import argparse
import glob
import io
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pillow_heif
from PIL import Image, ImageOps, ImageFilter
from corpus.config import ROOT as ARCHIVE

pillow_heif.register_heif_opener()
from corpus.pdf.build import images_to_pdf
from corpus.keys import key_arg
from corpus.manifest import update as manifest_update

EXTS = (".heic", ".heif", ".jpg", ".jpeg", ".png", ".tif", ".tiff", ".jp2")


def photocopier(im: Image.Image, white: float, black: float, gamma: float) -> Image.Image:
    """Grayscale flat-field photocopier: whiten background, keep figures."""
    g = ImageOps.grayscale(im)
    a = np.asarray(g, np.float32)
    bg = np.asarray(g.filter(ImageFilter.GaussianBlur(max(g.size) // 14)), np.float32)
    norm = a / np.maximum(bg, 1.0)          # ~1.0 on paper, <1 on ink
    out = np.clip((norm - black) / (white - black), 0, 1) ** gamma
    return Image.fromarray((out * 255).astype(np.uint8))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--key", type=key_arg, required=True)
    ap.add_argument("--select", nargs=2, metavar=("START", "END"), default=None,
                    help="inclusive lexical filename-stem range to stitch")
    ap.add_argument("--max-dim", type=int, default=2400)
    ap.add_argument("--jpeg-q", type=int, default=88)
    ap.add_argument("--white", type=float, default=0.93)
    ap.add_argument("--black", type=float, default=0.55)
    ap.add_argument("--gamma", type=float, default=1.15)
    ap.add_argument("--force", action="store_true")
    args = ap.parse_args()

    key = args.key
    src_dir = ARCHIVE / "source_images" / key
    if not src_dir.is_dir():
        sys.exit(f"no source_images/{key}/ directory")
    files = sorted(f for f in glob.glob(str(src_dir / "*"))
                   if os.path.splitext(f)[1].lower() in EXTS)
    if args.select:
        lo, hi = args.select
        files = [f for f in files if lo <= Path(f).stem <= hi]
    if not files:
        sys.exit("no matching source images")

    dst = ARCHIVE / "pdfs" / f"{key}.pdf"
    if dst.exists() and not args.force:
        sys.exit(f"{dst} exists; use --force to overwrite")

    print(f"{key}: photocopier-stitching {len(files)} page(s) "
          f"({Path(files[0]).name} .. {Path(files[-1]).name})")
    pages = []
    for f in files:
        im = Image.open(f)
        p = photocopier(im, args.white, args.black, args.gamma)
        if max(p.size) > args.max_dim:
            r = args.max_dim / max(p.size)
            p = p.resize((int(p.width * r), int(p.height * r)))
        buf = io.BytesIO()
        p.save(buf, format="JPEG", quality=args.jpeg_q, optimize=True)
        pages.append(buf.getvalue())
    images_to_pdf(pages, dst)
    mb = dst.stat().st_size / 1048576
    print(f"  wrote {dst}: {len(files)} pages, {mb:.1f} MB")

    # manifest acquisition annotation (only if key exists)
    # Through the manifest lock: a direct read-modify-write here could
    # undo a concurrent script's changes (corpus.manifest).
    sel = f" (selected {args.select[0]}..{args.select[1]})" if args.select else ""

    def annotate(m: dict) -> bool:
        if key not in m:
            return False
        acq = m[key].setdefault("acquisition", {})
        acq["pdf_path"] = f"pdfs/{key}.pdf"
        acq["pdf_stitched_at"] = datetime.now(timezone.utc).isoformat()
        acq["pdf_source"] = (f"photocopier-stitched from {len(files)} source "
                             f"photos{sel} (grayscale flat-field, background whitened)")
        return True

    found = {}
    manifest_update(lambda m: found.setdefault("ok", annotate(m)))
    if found["ok"]:
        print("  manifest acquisition annotated")
    else:
        print(f"  ! {key} not in manifest — PDF written, acquisition not annotated")
    return 0


if __name__ == "__main__":
    sys.exit(main())
