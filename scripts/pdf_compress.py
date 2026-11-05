#!/usr/bin/env python3
# corpus-command: pdf compress | re-render an oversized scan to JPEG (drops text)
"""Shrink an oversized scanned PDF under a target size by re-rendering each
page to a downsampled JPEG and rebuilding the document.

Companion to `pdf_stitch.py`'s re-encode fallback, but for an
*existing* PDF (typically a large image-only book scan that lands over the
repo's git-LFS soft size cap). Pages are rasterized via pypdfium2 at a DPI
chosen to cap the long edge at `--max-dim` px, re-encoded as JPEG at
`--jpeg-q`, and reassembled. If the result is still over `--target-mb`, the
script steps `max-dim` down and retries (auto-fallback), mirroring the
stitch convention.

Lossy by design: only run on scans whose text layer is absent (image-only)
or expendable. Vision OCR renders at ~1568 px long edge regardless, so a
2400 px / q82 re-encode is OCR-lossless in practice. The original page
streams are NOT preserved — keep a copy with `--keep-source` if you need
rollback and the source is uncommitted.

Usage:
  ./.runtime/venv/bin/python scripts/pdf_compress.py KEY
  ./.runtime/venv/bin/python scripts/pdf_compress.py KEY --target-mb 90 \
      --max-dim 2400 --jpeg-q 82 --keep-source ../KEY-full.pdf
"""
import argparse
import io
import sys
from datetime import datetime, timezone
from pathlib import Path

import pypdfium2 as pdfium

from corpus.pdf.build import images_to_pdf_bytes
from corpus.config import MANIFEST, PDF_DIR
from corpus.manifest import commit as manifest_commit, load as manifest_load
from corpus.keys import key_arg
from corpus.io import atomic_write_bytes

def render_reencoded(src_path: Path, max_dim: int, jpeg_q: int,
                     dpi_cap: int = 200) -> bytes:
    """Render each page to a downsampled JPEG and rebuild the PDF.

    DPI per page is chosen so the long edge is ~max_dim px, but never above
    dpi_cap (so small pages aren't upscaled into bloat).
    """
    src = pdfium.PdfDocument(str(src_path))
    pages = []
    for page in src:
        w_pt, h_pt = page.get_size()
        # dpi that makes long edge == max_dim; cap so we never upscale hard
        dpi = min(dpi_cap, max_dim * 72.0 / (max(w_pt, h_pt) or 1.0))
        scale = int(dpi) / 72.0
        pil = page.render(scale=scale).to_pil().convert("RGB")
        # The page keeps its point size as displayed. pdfium's get_size and
        # render both already apply /Rotate.
        size = (w_pt, h_pt)
        long_edge = max(pil.size)
        if long_edge > max_dim:
            ratio = max_dim / long_edge
            pil = pil.resize((int(pil.width * ratio), int(pil.height * ratio)))
        buf = io.BytesIO()
        pil.save(buf, format="JPEG", quality=jpeg_q, optimize=True)
        # the JPEG fills a page at the original point size
        pages.append((buf.getvalue(), size))
    src.close()
    data = images_to_pdf_bytes(pages)
    return data


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("key", type=key_arg)
    ap.add_argument("--target-mb", type=float, default=90.0,
                    help="Re-encode until under this size (default 90).")
    ap.add_argument("--max-dim", type=int, default=2400,
                    help="Initial long-edge cap in px (default 2400).")
    ap.add_argument("--jpeg-q", type=int, default=82,
                    help="JPEG quality (default 82).")
    ap.add_argument("--keep-source", type=Path, default=None,
                    help="Copy the original PDF here before clobbering.")
    ap.add_argument("--force", action="store_true",
                    help="Re-compress even if already under target.")
    args = ap.parse_args()

    key = args.key                       # key_arg: NFC and validated
    pdf_path = PDF_DIR / f"{key}.pdf"
    if not pdf_path.exists():
        print(f"ERROR: {pdf_path} not found", file=sys.stderr)
        return 2

    orig_bytes = pdf_path.stat().st_size
    orig_mb = orig_bytes / 1048576
    target_bytes = args.target_mb * 1048576
    print(f"{key}: source {orig_mb:.1f} MB, target <{args.target_mb:.0f} MB")
    if orig_bytes <= target_bytes and not args.force:
        print("  already under target; nothing to do (use --force to override).")
        return 0

    if args.keep_source:
        args.keep_source.write_bytes(pdf_path.read_bytes())
        print(f"  kept source copy at {args.keep_source}")

    max_dim, jpeg_q = args.max_dim, args.jpeg_q
    data = None
    for attempt in range(1, 11):
        data = render_reencoded(pdf_path, max_dim, jpeg_q)
        mb = len(data) / 1048576
        print(f"  attempt {attempt}: max_dim={max_dim} q={jpeg_q} -> {mb:.1f} MB")
        if len(data) <= target_bytes:
            break
        # step down in stages: shrink dimension to ~OCR-floor first, then
        # quality down to a readable floor, then a final mild dim reduction.
        # 1568 px is vision's optimal; staying >=~1400 keeps OCR lossless.
        if max_dim > 1700:
            max_dim = int(max_dim * 0.85)
        elif jpeg_q > 58:
            jpeg_q = max(58, jpeg_q - 8)
        elif max_dim > 1300:
            max_dim = max(1300, int(max_dim * 0.92))
        else:
            break  # exhausted all knobs
    final_mb = len(data) / 1048576
    if len(data) > target_bytes:
        print(f"  WARNING: still {final_mb:.1f} MB after step-down "
              f"(min max_dim={max_dim}, q={jpeg_q}); writing anyway.")

    atomic_write_bytes(pdf_path, data)
    print(f"  wrote {pdf_path.name}: {orig_mb:.1f} -> {final_mb:.1f} MB "
          f"({100 * (1 - final_mb / orig_mb):.0f}% smaller)")

    # Annotate the manifest if the key already exists (orphans get annotated
    # later by intake_acquire.py; only touch an existing entry here).
    if MANIFEST.exists():
        m = manifest_load()
        if key in m:
            acq = m[key].setdefault("acquisition", {})
            acq["pdf_compressed_at"] = datetime.now(timezone.utc).isoformat()
            acq["pdf_compression"] = (
                f"re-encoded {orig_mb:.1f}->{final_mb:.1f} MB "
                f"(max_dim={max_dim}, jpeg_q={jpeg_q})")
            manifest_commit(m)
            print("  annotated manifest acquisition block.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
