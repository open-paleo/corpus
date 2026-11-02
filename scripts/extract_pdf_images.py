#!/usr/bin/env python3
"""
Rip the embedded raster images (figures, plates, photos) of pdfs/{key}.pdf
into pdf_images/{key}/, on demand (`corpus pdf images --key K`).

JPEG and JPEG 2000 images keep their original encoding; other encodings
(Flate, CCITT, JBIG2) are decoded and written as PNG, losslessly (1-bit and
grayscale kept as such). Files are named p{page}-i{n}.{ext}, n counting the
images on that page; an image drawn on several pages is written once, on its
first page. Vector figures are not raster images: a born-digital paper may
yield few or none.

Each PDF is extracted into a temporary folder that replaces pdf_images/{key}/
only when complete, so an interrupted run leaves the old folder (or none),
and --force never mixes old and new images. A key whose folder already has
images is skipped unless --force. pdf_images/ is gitignored: images stay
local. No manifest write.

Usage:
  ./.runtime/venv/bin/python scripts/extract_pdf_images.py --key K [--key K2] [--force]
  ./.runtime/venv/bin/python scripts/extract_pdf_images.py --keys-file F
  ./.runtime/venv/bin/python scripts/extract_pdf_images.py --all     # every PDF: large
"""

from __future__ import annotations

import argparse
import hashlib
import shutil
import sys
import time
from pathlib import Path

import pypdfium2 as pdfium
import pypdfium2.raw as pdfium_raw

from corpus.config import PDF_DIR, PDF_IMAGES_DIR as OUT_DIR, log_path
from corpus.io import jsonl_append
from corpus.keys import add_key_args, keys_in, resolve_keys

LOG = log_path("pdf_images.jsonl")


def _gray_if_source_gray(img, path: Path) -> None:
    """Rewrite a decoded PNG at the PDF image's own depth: 1-bit or grayscale.

    pdfium decodes these (e.g. CCITT fax scans) to RGB. The pixels are the
    same but the file is larger: exon1976a's 235 pages came to 142 MB, against
    94 MB from PyMuPDF (grayscale); written 1-bit they are about half that.
    """
    from PIL import Image
    meta = img.get_metadata()
    if meta.bits_per_pixel == 1:
        mode = "1"
    elif meta.colorspace == pdfium_raw.FPDF_COLORSPACE_DEVICEGRAY:
        mode = "L"
    else:
        return
    im = Image.open(path)
    if im.mode != mode:
        im.convert(mode).save(path, optimize=True)


def extract_one(pdf_path: Path, out_dir: Path) -> tuple[int, int]:
    """Returns (n_images, n_bytes_written)."""
    out_dir.mkdir(parents=True, exist_ok=True)
    doc = pdfium.PdfDocument(str(pdf_path))
    n = 0
    total_bytes = 0
    seen: set[str] = set()
    for page_index in range(len(doc)):
        page = doc[page_index]
        objs = page.get_objects(filter=(pdfium_raw.FPDF_PAGEOBJ_IMAGE,))
        for k, img in enumerate(objs, 1):
            try:
                digest = hashlib.sha1(bytes(img.get_data(decode_simple=False))).hexdigest()
            except Exception:
                continue
            if digest in seen:
                continue
            seen.add(digest)
            stem = out_dir / f"p{page_index + 1:04d}-i{k:03d}"
            try:
                img.extract(str(stem), fb_format="png")
            except Exception:
                continue
            for out_path in out_dir.glob(stem.name + ".*"):
                if out_path.suffix == ".png":
                    _gray_if_source_gray(img, out_path)
                n += 1
                total_bytes += out_path.stat().st_size
    doc.close()
    return n, total_bytes


def extract_to(pdf_path: Path, out_dir: Path) -> tuple[int, int]:
    """extract_one into a sibling temporary folder, then swap it in for
    out_dir: an interrupted run never leaves a partial out_dir, and a
    re-extraction never keeps images the new PDF no longer has. An empty
    result removes out_dir."""
    tmp = out_dir.with_name(out_dir.name + ".tmp")
    shutil.rmtree(tmp, ignore_errors=True)
    try:
        n, size = extract_one(pdf_path, tmp)
        shutil.rmtree(out_dir, ignore_errors=True)
        if n:
            tmp.replace(out_dir)
        return n, size
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    add_key_args(ap, all_=True, all_help="every PDF in pdfs/ (large: several MB per paper)")
    ap.add_argument("--force", action="store_true",
                    help="re-extract even if pdf_images/{key}/ already has images")
    args = ap.parse_args()

    keys = resolve_keys(args)
    if keys:
        candidates = []
        for k in keys:
            if (PDF_DIR / f"{k}.pdf").exists():
                candidates.append(k)
            else:
                print(f"  ! no pdf for {k}; skipping")
    elif args.all:
        candidates = keys_in(PDF_DIR, ".pdf")
    else:
        ap.error("name the papers with --key/--keys-file, or pass --all")

    print(f"Extracting images from {len(candidates)} PDFs into {OUT_DIR}/")
    started = time.time()
    n_pdfs_ok = n_pdfs_skip = n_pdfs_err = 0
    n_images_total = 0
    bytes_total = 0
    for i, k in enumerate(candidates, 1):
        out_dir = OUT_DIR / k
        if not args.force and out_dir.exists() and any(out_dir.iterdir()):
            n_pdfs_skip += 1
            continue
        try:
            n_images, n_bytes = extract_to(PDF_DIR / f"{k}.pdf", out_dir)
        except Exception as e:
            n_pdfs_err += 1
            jsonl_append(LOG, {"key": k, "outcome": "error", "error": str(e)})
            print(f"  ! {k}: {e}")
            continue
        n_pdfs_ok += 1
        n_images_total += n_images
        bytes_total += n_bytes
        jsonl_append(LOG, {"key": k, "outcome": "ok", "n_images": n_images, "bytes": n_bytes})
        if len(candidates) <= 20:
            print(f"  {k}: {n_images} image(s), {n_bytes / 1e6:.1f} MB"
                  + ("  (no raster images: vector figures?)" if not n_images else ""))
        elif i % 50 == 0:
            elapsed = time.time() - started
            print(f"  [{i}/{len(candidates)}] {k}: {n_images} imgs · "
                  f"{elapsed:.0f}s elapsed · {n_images_total} imgs total")

    elapsed = time.time() - started
    print()
    print(f"Done in {elapsed/60:.1f} min")
    print(f"  PDFs:    ok={n_pdfs_ok}  skip={n_pdfs_skip}  err={n_pdfs_err}")
    print(f"  Images:  {n_images_total}  ({bytes_total / 1024 / 1024:.0f} MB)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
