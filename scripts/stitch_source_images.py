#!/usr/bin/env python3
# corpus-command: pdf stitch | source_images/{key}/ -> pdfs/{key}.pdf
"""
Stitch user-provided source images (JPG/JPEG/PNG/TIFF/JP2/HEIC/HEIF) for a
citation key into a single PDF at pdfs/{key}.pdf.

Inputs:  source_images/{key}/<sortable-filenames>.{jpg,jpeg,png,tif,tiff,jp2,heic,heif}
Output:  pdfs/{key}.pdf  (atomic write via .tmp + rename)

Pages are taken in lexically-sorted filename order. JPEG and JP2 data are
embedded unchanged via corpus.pdf.build (img2pdf, as in stitch_bhl_pdfs.py);
PNG/TIFF losslessly. HEIC/HEIF inputs (iPhone photos) require pillow_heif
and are decoded then re-encoded as q=95 JPEG before embedding (img2pdf
cannot embed HEIC).

Size guard: the upstream git host has a soft limit around 100-150 MB for
LFS objects, so if the lossless stitch produces a PDF larger than
--soft-limit-mb (default 90), the script automatically re-stitches with
re-encoding (downscale to --max-dim and JPEG quality --jpeg-q). Use
--reencode to skip the lossless attempt; --no-reencode to disable the
auto-fallback (use with caution).

Updates manifest[key].acquisition with pdf_path / pdf_stitched_at /
pdf_source. Does NOT set acquisition.source — the user/operator decides
that (typically "manual"); the manifest entry should already exist.

Usage:
  ./.runtime/venv/bin/python scripts/stitch_source_images.py [--key K]...
  ./.runtime/venv/bin/python scripts/stitch_source_images.py --reencode --key K
  ./.runtime/venv/bin/python scripts/stitch_source_images.py --soft-limit-mb 120
"""
from __future__ import annotations

import argparse
import io
import sys
from datetime import datetime, timezone
from pathlib import Path

from corpus.pdf.build import images_to_pdf
from PIL import Image
from corpus.config import ROOT as ARCHIVE, MANIFEST, PDF_DIR
from corpus.manifest import commit as manifest_commit, load as manifest_load
from corpus.keys import key_arg

try:
    import pillow_heif
    pillow_heif.register_heif_opener()
except ImportError:
    pass  # HEIC/HEIF inputs will fall back to error; install pillow-heif for support

SRC_DIR = ARCHIVE / "source_images"
EXTS = {".jpg", ".jpeg", ".png", ".tif", ".tiff", ".jp2", ".heic", ".heif"}
# Embedded by img2pdf as they are (JPEG/JP2 unchanged, PNG/TIFF losslessly).
NATIVE = {".jpg", ".jpeg", ".png", ".tif", ".tiff", ".jp2"}


def list_pages(src_dir: Path) -> list[Path]:
    return sorted(p for p in src_dir.iterdir()
                  if p.is_file() and p.suffix.lower() in EXTS)


def stitch_one(pages: list[Path], pdf_path: Path,
               reencode: bool, max_dim: int, jpeg_q: int) -> int:
    """Build pdf_path from pages. Returns final size in bytes."""
    items = []
    for img_path in pages:
        if reencode:
            with Image.open(img_path) as pil:
                if pil.mode not in ("RGB", "L"):
                    pil = pil.convert("RGB")
                if max(pil.size) > max_dim:
                    pil.thumbnail((max_dim, max_dim), Image.LANCZOS)
                buf = io.BytesIO()
                pil.save(buf, format="JPEG", quality=jpeg_q, optimize=True)
                items.append(buf.getvalue())
        elif img_path.suffix.lower() in NATIVE:
            items.append(img_path)
        else:
            # img2pdf can't embed this format (e.g. HEIC) — round-trip through
            # Pillow as a high-quality JPEG, preserving the lossless intent
            # as best we can. Inputs at original pixel dimensions, q=95.
            with Image.open(img_path) as pil:
                if pil.mode not in ("RGB", "L"):
                    pil = pil.convert("RGB")
                buf = io.BytesIO()
                pil.save(buf, format="JPEG", quality=95, optimize=True)
                items.append(buf.getvalue())
    return images_to_pdf(items, pdf_path)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--key", type=key_arg, action="append", default=[],
                    help="Stitch this key only. Repeatable. Default: all.")
    ap.add_argument("--force", action="store_true",
                    help="Re-stitch even if pdfs/{key}.pdf already exists.")
    ap.add_argument("--reencode", action="store_true",
                    help="Skip lossless attempt; go straight to re-encoded JPEG output.")
    ap.add_argument("--no-reencode", action="store_true",
                    help="Disable the auto re-encode fallback when lossless > soft-limit.")
    ap.add_argument("--soft-limit-mb", type=int, default=90,
                    help="If lossless PDF > this size in MB, auto re-encode (default 90).")
    ap.add_argument("--max-dim", type=int, default=2400,
                    help="Re-encode: max pixel dimension on longest side (default 2400).")
    ap.add_argument("--jpeg-q", type=int, default=82,
                    help="Re-encode: JPEG quality (default 82).")
    args = ap.parse_args()

    if not SRC_DIR.exists():
        print(f"No source_images/ directory at {SRC_DIR}; nothing to stitch.")
        return 0

    if args.key:
        keys = list(dict.fromkeys(args.key))  # preserve order, dedupe
    else:
        keys = sorted(p.name for p in SRC_DIR.iterdir() if p.is_dir())
    print(f"Candidates: {len(keys)} key(s) under source_images/")

    manifest = manifest_load() if MANIFEST.exists() else {}
    PDF_DIR.mkdir(exist_ok=True)
    n_ok = n_skip = n_err = n_reencoded = 0
    now = datetime.now(timezone.utc).isoformat()

    for k in keys:
        src = SRC_DIR / k
        if not src.is_dir():
            n_err += 1
            print(f"  [err]  {k}: source_images/{k}/ missing")
            continue
        pages = list_pages(src)
        if not pages:
            n_err += 1
            print(f"  [err]  {k}: no images in source_images/{k}/")
            continue
        pdf_path = PDF_DIR / f"{k}.pdf"
        if pdf_path.exists() and not args.force:
            n_skip += 1
            print(f"  [skip] {k} (pdfs/{k}.pdf exists; --force to overwrite)")
            continue

        try:
            size = stitch_one(pages, pdf_path,
                              reencode=args.reencode,
                              max_dim=args.max_dim, jpeg_q=args.jpeg_q)
        except Exception as e:
            n_err += 1
            print(f"  [err]  {k}: {e}")
            continue

        size_mb = size / 1024 / 1024
        method = "reencoded" if args.reencode else "lossless"

        # Auto re-encode fallback if lossless output exceeds soft limit
        if (not args.reencode and not args.no_reencode
                and size_mb > args.soft_limit_mb):
            print(f"  [warn] {k}: lossless {size_mb:.1f} MB > {args.soft_limit_mb} MB; re-encoding...")
            try:
                size = stitch_one(pages, pdf_path,
                                  reencode=True,
                                  max_dim=args.max_dim, jpeg_q=args.jpeg_q)
                size_mb = size / 1024 / 1024
                method = "reencoded (auto-fallback)"
                n_reencoded += 1
            except Exception as e:
                n_err += 1
                print(f"  [err]  {k}: re-encode failed: {e}")
                continue

        # Update manifest acquisition (in-place; user may further annotate)
        if k in manifest:
            acq = manifest[k].setdefault("acquisition", {})
            acq["pdf_path"] = f"pdfs/{k}.pdf"
            acq["pdf_stitched_at"] = now
            acq["pdf_source"] = f"stitched from {len(pages)} source images ({method})"
        else:
            print(f"  [warn] {k} not in manifest; PDF written but acquisition not updated")

        n_ok += 1
        print(f"  [ok]   {k:<25s} {len(pages):>4d}p  {size_mb:>6.1f} MB  ({method})")

    if n_ok and manifest:
        manifest_commit(manifest)

    print()
    print(f"Done: ok={n_ok}, skip={n_skip}, err={n_err}, auto-reencoded={n_reencoded}")
    return 0 if n_err == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
