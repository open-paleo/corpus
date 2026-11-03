#!/usr/bin/env python3
# corpus-command: convert spread | vision OCR of two-page spreads
"""
Step 2 (vision-OCR path, spread variant): OCR a scanned PDF whose pages are
two-page book SPREADS — one scan capturing both facing pages of an open
volume. Common in monograph scans (Palaeontologia Sinica, Palaeontographica).

Feeding a whole spread to the vision model works, but it reads two columns of
body text at once and the page order can interleave. Splitting each spread at
the gutter first gives one book page per image, which is both more accurate and
better resolution per page (the Anthropic long-edge cap of 1568 px applies to
whatever you send).

The gutter is found by luminance, not assumed at 50%: within the central band of
the page, the whitest column is the gutter. Falls back to the midpoint if the
scan is too dark for a clear winner. Portrait pages (or any page below
--landscape-ratio) are passed through unsplit, as are pages named in
--no-split — a landscape page that is one WIDE plate rather than two facing
pages, whose caption runs across the centre and would be cut in half.

Mirrors convert_with_vision.py exactly for prompt text, prompt_id, cache layout
(.runtime/cache/vision/{key}/page-NNNN.json), markdown join ("\n\n---\n\n") and
cost accounting, so re-runs are free and the two scripts are interchangeable
downstream. NNNN enumerates the SPLIT pages, so a key converted here has more
cache entries than the PDF has pages.

Usage:
  ./.runtime/venv/bin/python scripts/ocr_spread_pdf.py --key KEY [--dpi N]
                                    [--concurrency N] [--model NAME]
                                    [--no-manifest] [--dry-run]

--no-manifest skips the manifest write (use while another conversion script is
running in the background — concurrent manifest writers clobber each other).
"""

from __future__ import annotations

import argparse
import asyncio
import io
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import pypdfium2 as pdfium

from corpus.config import MD_DIR, PDF_DIR, VISION_CACHE as CACHE_DIR, log_path
from corpus.io import atomic_write_text
from corpus.pages import join as join_pages
from corpus.models import cost as model_cost
from corpus.vision import cache as vision_cache
from corpus.vision.client import make as make_client
from corpus.vision.ocr import (DEFAULT_CONCURRENCY, DEFAULT_DPI, DEFAULT_MODEL,
                               ocr_one_page, select_prompt)

LOG = log_path("vision.jsonl")
from corpus.manifest import commit as manifest_commit, load as manifest_load
from corpus.keys import key_arg
from corpus.pdf.pages import parse as _parse_pages
from corpus.io import jsonl_append

MAX_EDGE = 1568          # Anthropic downsamples above this; no point sending more
GUTTER_BAND = 0.20       # search the central 20% of the width for the gutter
EDGE_MARGIN = 0.004      # trim a hair past the gutter so no glyph is clipped




def find_gutter(pil) -> int:
    """Column index of the gutter: the whitest column in the central band."""
    gray = pil.convert("L")
    w, h = gray.size
    lo, hi = int(w * (0.5 - GUTTER_BAND / 2)), int(w * (0.5 + GUTTER_BAND / 2))
    px = gray.load()
    step = max(1, h // 200)                      # sample ~200 rows, not all of them
    rows = range(0, h, step)
    best_x, best_lum = (lo + hi) // 2, -1.0
    for x in range(lo, hi):
        lum = sum(px[x, y] for y in rows) / len(rows)
        if lum > best_lum:
            best_lum, best_x = lum, x
    return best_x


def render_images(pdf_path: Path, dpi: int, landscape_ratio: float,
                  no_split: set[int], wanted: set[int] | None
                  ) -> list[tuple[int, int, bytes]]:
    """Returns [(slot_index, source_page_1based, jpeg_bytes)] for the split pages.

    `wanted` is the set of slot indices still needing an API call; slots already
    cached are skipped so a resumed run does no rasterization work.
    """
    doc = pdfium.PdfDocument(pdf_path)
    out: list[tuple[int, int, bytes]] = []
    slot = 0
    for i in range(len(doc)):
        page = doc.get_page(i)
        pw, ph = page.get_size()
        is_spread = (pw / ph) >= landscape_ratio and (i + 1) not in no_split
        n_halves = 2 if is_spread else 1
        slots = list(range(slot, slot + n_halves))
        slot += n_halves
        if wanted is not None and not (set(slots) & wanted):
            continue                              # every half already cached
        pil = page.render(scale=dpi / 72.0).to_pil().convert("RGB")
        w, h = pil.size
        if is_spread:
            g = find_gutter(pil)
            m = int(w * EDGE_MARGIN)
            halves = [pil.crop((0, 0, min(g + m, w), h)),
                      pil.crop((max(g - m, 0), 0, w, h))]
        else:
            halves = [pil]
        for s, half in zip(slots, halves):
            if wanted is not None and s not in wanted:
                continue
            hw, hh = half.size
            long_edge = max(hw, hh)
            if long_edge > MAX_EDGE:
                r = MAX_EDGE / long_edge
                half = half.resize((int(hw * r), int(hh * r)))
            buf = io.BytesIO()
            half.save(buf, format="JPEG", quality=85, optimize=True)
            out.append((s, i + 1, buf.getvalue()))
    doc.close()
    return out


def count_slots(pdf_path: Path, landscape_ratio: float,
                no_split: set[int]) -> list[int]:
    """Source page (1-based) for each split slot."""
    doc = pdfium.PdfDocument(pdf_path)
    src = []
    for i in range(len(doc)):
        pw, ph = doc.get_page(i).get_size()
        is_spread = (pw / ph) >= landscape_ratio and (i + 1) not in no_split
        src.extend([i + 1] * (2 if is_spread else 1))
    doc.close()
    return src


async def main_async(args) -> int:
    from corpus.config import require_env
    require_env("ANTHROPIC_API_KEY")

    key = args.key
    pdf_path = PDF_DIR / f"{key}.pdf"
    if not pdf_path.exists():
        print(f"ERROR: {pdf_path} not found", file=sys.stderr)
        return 2

    manifest = manifest_load()
    if key not in manifest:
        print(f"ERROR: {key} not in manifest — run intake_acquire.py first",
              file=sys.stderr)
        return 2

    prompt_text, prompt_id = select_prompt(manifest, key, args.extra_context)
    cache_dir = CACHE_DIR / key
    cache_dir.mkdir(parents=True, exist_ok=True)

    no_split = set(_parse_pages(args.no_split or ""))
    src_pages = count_slots(pdf_path, args.landscape_ratio, no_split)
    n_slots = len(src_pages)
    n_spreads = sum(1 for i in range(len(src_pages))
                    if src_pages.count(src_pages[i]) == 2) // 2

    wanted = {s for s in range(n_slots)
              if vision_cache.read(cache_dir, s, model=args.model, prompt_id=prompt_id) is None}

    print(f"key:        {key}")
    print(f"pdf pages:  {len(set(src_pages))}  ({n_spreads} spreads split"
          + (f", {len(no_split)} forced whole" if no_split else "") + ")")
    print(f"book pages: {n_slots}   uncached: {len(wanted)}")
    print(f"dpi: {args.dpi}  model: {args.model}  prompt: {prompt_id}")
    if args.dry_run:
        return 0

    started = time.time()
    images = render_images(pdf_path, args.dpi, args.landscape_ratio,
                           no_split, wanted)
    jpegs = {s: b for s, _, b in images}
    print(f"rendered {len(images)} images "
          f"(avg {sum(len(b) for b in jpegs.values()) // max(len(jpegs), 1) // 1024} KB)")

    client = make_client()
    sem = asyncio.Semaphore(args.concurrency)

    async def do_slot(s):
        async with sem:
            try:
                return await ocr_one_page(client, args.model, prompt_text,
                                          prompt_id, s, jpegs.get(s, b""),
                                          cache_dir)
            except Exception as e:
                return {"error": str(e), "page": s + 1}

    results = await asyncio.gather(*[do_slot(s) for s in range(n_slots)])

    parts, in_tok, out_tok, c_read, c_write = [], 0, 0, 0, 0
    n_cached = n_failed = 0
    for s, r in enumerate(results):
        if "error" in r:
            n_failed += 1
            parts.append(f"[VISION OCR FAILED FOR PAGE {s + 1}: {r['error']}]")
            continue
        parts.append(r["markdown"] or f"[empty page {s + 1}]")
        in_tok += r["input_tokens"]; out_tok += r["output_tokens"]
        c_read += r["cache_read"]; c_write += r["cache_write"]
        n_cached += bool(r.get("cached_hit"))

    # Both book pages of a spread carry the marker of the PDF page they came from.
    atomic_write_text(MD_DIR / f"{key}.md", join_pages(list(zip(src_pages, parts))))

    cost = model_cost(args.model, in_tok, out_tok, c_read, c_write)
    summary = {
        "ts": datetime.now(timezone.utc).isoformat(), "key": key,
        "outcome": "ok" if n_failed == 0 else "partial",
        "source": "spread-split", "n_pdf_pages": len(set(src_pages)),
        "n_pages": n_slots, "n_cached": n_cached, "n_failed": n_failed,
        "input_tokens": in_tok, "output_tokens": out_tok,
        "cache_read": c_read, "cache_write": c_write,
        "cost_usd": round(cost, 4), "elapsed_s": round(time.time() - started, 1),
        "model": args.model, "prompt_id": prompt_id,
    }
    jsonl_append(LOG, summary)

    if not args.no_manifest:
        manifest = manifest_load()   # re-read: minimise clobber window
        conv = manifest[key].setdefault("conversion", {})
        conv.update({
            "tool": "claude-vision", "status": "converted",
            "markdown_path": f"markdown/{key}.md",
            "page_count": n_slots,
            "converted_at": summary["ts"],
            "notes": (f"spread-split: {len(set(src_pages))} PDF pages -> "
                      f"{n_slots} book pages via ocr_spread_pdf.py"
                      + (f"; pages {args.no_split} kept whole (wide plates)"
                         if no_split else "")),
        })
        conv.pop("vision_ocr_needed", None)
        conv.pop("vision_ocr_reason", None)
        manifest_commit(manifest)
        print("manifest updated")
    else:
        print("manifest NOT updated (--no-manifest)")

    print(f"\n{summary['outcome']}: {n_slots} pages "
          f"({n_cached} cached, {n_failed} failed) "
          f"${cost:.2f} in {summary['elapsed_s'] / 60:.1f} min")
    return 0 if n_failed == 0 else 1


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--key", type=key_arg, required=True)
    ap.add_argument("--dpi", type=int, default=DEFAULT_DPI * 2,
                    help="render DPI of the FULL spread; each half is then "
                         "downsampled to the 1568 px cap (default 300)")
    ap.add_argument("--concurrency", type=int, default=DEFAULT_CONCURRENCY)
    ap.add_argument("--model", default=DEFAULT_MODEL)
    ap.add_argument("--landscape-ratio", type=float, default=1.15,
                    help="width/height at or above which a page is a spread")
    ap.add_argument("--no-split", default="",
                    help="1-based PDF pages to OCR whole despite being "
                         "landscape, e.g. '41' or '36-38,41' — for wide "
                         "plates whose caption crosses the centre")
    ap.add_argument("--extra-context", default="")
    ap.add_argument("--no-manifest", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    return asyncio.run(main_async(ap.parse_args()))


if __name__ == "__main__":
    raise SystemExit(main())
