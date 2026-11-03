#!/usr/bin/env python3
# corpus-command: convert poster | vision OCR of a single-canvas poster
"""Tiled vision OCR for conference posters and other single-canvas layouts.

Why this exists
---------------
`convert_with_vision.py` sends one image per PDF *page*. That works for
journal pages but fails on a poster: a poster is one enormous landscape
canvas (e.g. 6192x2880 px), and Anthropic's vision input downscales to
~1568 px on the long edge. At that scale a poster's body text lands at 2-3 px
tall — illegible. Sonnet does not reliably say `[ILLEGIBLE]` on unreadable
input; it fabricates plausible text, which then passes every automated QC
check we have. So a single-shot call on a poster is worse than useless.

This script instead cuts the canvas into tiles that each stay UNDER the
1568 px cap, so every tile is OCR'd at native resolution with no downscaling
at all.

Tiles follow the poster's real column structure rather than a blind grid.
Blind grids bisect text columns, which scrambles reading order (the model
reads across two half-columns as if they were one). We find the actual
vertical gutters by ink-density profiling, split there, then split tall
panels into row-tiles. Output is assembled column-major (panel 1 top, panel 1
bottom, panel 2 top, ...) which is how a poster is meant to be read.

The title banner usually spans the full width above the panels, which would
put ink in every column and hide all gutters — so gutter detection ignores
the top `--top-frac` of the canvas, and that band is emitted as its own tile.

Cache layout mirrors convert_with_vision.py (`.runtime/cache/vision/{key}/`)
so re-runs are free, but uses `tile-NNNN.json` names so poster tiles never
collide with page caches.

Usage
-----
    ./.runtime/venv/bin/python scripts/tile_ocr_poster.py --key KEY
    ./.runtime/venv/bin/python scripts/tile_ocr_poster.py --key K --dry-run
    ./.runtime/venv/bin/python scripts/tile_ocr_poster.py --key K --panels 5 --rows 2
"""

from __future__ import annotations

import argparse
import asyncio
import io
import sys
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
from PIL import Image
from corpus.io import atomic_write_json
from corpus.config import MANIFEST, PDF_DIR, MD_DIR, RUNTIME, SOURCE_IMAGES_DIR, VISION_CACHE as CACHE_DIR
from corpus.manifest import commit as manifest_commit, load as manifest_load

SRC_DIR = SOURCE_IMAGES_DIR
LOG = RUNTIME / "logs" / "vision.jsonl"

from corpus.models import SONNET, cache_model_ok
from corpus.keys import key_arg
from corpus.config import require_env
from corpus.vision.client import make as make_client
from corpus.io import atomic_write_text
from corpus.pages import marker as page_marker
from corpus.vision import cache as vision_cache
from corpus.vision.ocr import call_image
from corpus.io import jsonl_append
DEFAULT_MODEL = SONNET
MAX_EDGE = 1568          # Anthropic vision: long edge beyond this is downscaled
JPEG_Q = 92
DEFAULT_CONCURRENCY = 3

PROMPT_TILE = """You are an OCR engine. This image is ONE TILE cropped out of a larger \
conference poster. Extract the text visible in this tile into clean Markdown, verbatim.

Rules:
- Preserve italic formatting for genus and species names (e.g. *Tyrannosaurus rex*).
- Preserve heading structure (use # / ## as appropriate).
- Preserve table structure where present (Markdown tables; if too complex, use HTML).
- Preserve in-text citations and bibliographic references exactly.
- Read strictly top-to-bottom. This tile is a single column of the poster; do NOT
  jump between text blocks.
- Text may be cut off at the tile edges. Transcribe partial lines as they appear —
  do NOT guess at or complete the missing part.
- For figures: emit `[FIGURE: <one-line description of what the figure shows>]`
  followed by the figure caption verbatim.
- Do NOT invent text. If part of the tile is illegible, write `[ILLEGIBLE]`.
- If the tile is blank or contains only a figure with no text, output the figure
  marker only.
- Output the markdown body only — no preamble, no explanation."""
PROMPT_ID = "poster-tile-v1"


def load_canvas(key: str) -> Image.Image:
    """Prefer the original source image (native pixels, no resample). Fall back
    to rendering page 1 of the stitched PDF."""
    src = SRC_DIR / key
    if src.is_dir():
        imgs = sorted(
            p for p in src.iterdir()
            if p.suffix.lower() in {".png", ".jpg", ".jpeg", ".tif", ".tiff"}
        )
        if len(imgs) == 1:
            return Image.open(imgs[0]).convert("RGB")
        if len(imgs) > 1:
            sys.exit(f"{key}: {len(imgs)} source images; this script expects a "
                     f"single-canvas poster. Use convert_with_vision.py instead.")
    pdf = PDF_DIR / f"{key}.pdf"
    if not pdf.exists():
        sys.exit(f"{key}: no source image and no {pdf}")
    import pypdfium2 as pdfium
    doc = pdfium.PdfDocument(str(pdf))
    if len(doc) != 1:
        sys.exit(f"{key}: PDF has {len(doc)} pages; poster tiling expects 1.")
    return doc[0].render(scale=4).to_pil().convert("RGB")


def find_gutters(gray: np.ndarray, top_frac: float,
                 min_width: int = 15, ink_thresh: int = 160) -> list[int]:
    """Return x-coordinates to split the canvas at (midpoints of internal
    whitespace gutters). Ignores the top `top_frac` band so a full-width title
    banner doesn't mask every gutter."""
    h, w = gray.shape
    body = gray[int(h * top_frac):, :]
    ink = (body < ink_thresh).astype(np.uint8)
    prof = ink.sum(axis=0)
    thr = max(3, int(0.004 * body.shape[0]))
    runs, start = [], None
    for x, is_gutter in enumerate(prof < thr):
        if is_gutter and start is None:
            start = x
        elif not is_gutter and start is not None:
            if x - start >= min_width:
                runs.append((start, x))
            start = None
    if start is not None and w - start >= min_width:
        runs.append((start, w))
    # Drop the outer margins; keep internal gutters only.
    internal = [r for r in runs if r[0] > w * 0.02 and r[1] < w * 0.98]
    return [(a + b) // 2 for a, b in internal]


def build_tiles(canvas: Image.Image, top_frac: float, rows: int,
                overlap: float, panels_override: int | None,
                title_cols: int = 3) -> tuple[list[dict], int]:
    """Return ([{label, box}], n_panels) in poster reading order (title, then
    column-major)."""
    w, h = canvas.size
    gray = np.asarray(canvas.convert("L"))
    title_y = int(h * top_frac)

    if panels_override:
        step = w // panels_override
        cuts = [step * i for i in range(1, panels_override)]
    else:
        cuts = find_gutters(gray, top_frac)
    bounds = [0] + cuts + [w]
    n_panels = len(bounds) - 1

    tiles: list[dict] = []
    if title_y > 40:
        # The title band is full-width and short; as one tile its long edge is
        # the whole canvas width, so it would downscale hard and lose the small
        # affiliation line. Cut it into title_cols pieces (with overlap, since
        # a cut can land mid-word) to keep the glyphs legible.
        tw = w // title_cols
        tpad = int(tw * 0.04)
        for i in range(title_cols):
            x0 = max(0, i * tw - (tpad if i else 0))
            x1 = min(w, (i + 1) * tw + (tpad if i < title_cols - 1 else 0))
            label = "title" if title_cols == 1 else f"title{i + 1}"
            tiles.append({"label": label, "box": (x0, 0, x1, title_y)})

    for ci in range(len(bounds) - 1):
        x0, x1 = bounds[ci], bounds[ci + 1]
        col_h = h - title_y
        row_h = col_h // rows
        pad = int(row_h * overlap)
        for ri in range(rows):
            y0 = title_y + ri * row_h
            y1 = title_y + (ri + 1) * row_h if ri < rows - 1 else h
            y0 = max(title_y, y0 - (pad if ri else 0))
            y1 = min(h, y1 + (pad if ri < rows - 1 else 0))
            tiles.append({"label": f"p{ci + 1}r{ri + 1}", "box": (x0, y0, x1, y1)})
    return tiles, n_panels


def tile_to_jpeg(canvas: Image.Image, box: tuple) -> tuple[bytes, tuple, bool]:
    """Crop, downscale only if the long edge exceeds MAX_EDGE, encode JPEG.
    Returns (bytes, final_size, was_downscaled)."""
    crop = canvas.crop(box)
    downscaled = False
    if max(crop.size) > MAX_EDGE:
        scale = MAX_EDGE / max(crop.size)
        crop = crop.resize((max(1, int(crop.width * scale)),
                            max(1, int(crop.height * scale))), Image.LANCZOS)
        downscaled = True
    buf = io.BytesIO()
    crop.save(buf, format="JPEG", quality=JPEG_Q)
    return buf.getvalue(), crop.size, downscaled


def cache_path_for(key: str, idx: int) -> Path:
    return CACHE_DIR / key / f"tile-{idx:04d}.json"


def cached_tile(key: str, idx: int, model: str) -> dict | None:
    d = vision_cache.read_entry(cache_path_for(key, idx))   # tolerant of a half-written file
    if d is None:
        return None
    if d.get("prompt_id") == PROMPT_ID and cache_model_ok(d.get("model"), model):
        d["cached_hit"] = True
        return d
    return None


async def ocr_tile(client, sem, model: str, key: str, idx: int,
                   label: str, jpeg: bytes) -> dict:
    hit = cached_tile(key, idx, model)
    if hit:
        print(f"  [cache] tile {idx:02d} {label}")
        return hit
    async with sem:
        msg = await call_image(client, model, PROMPT_TILE, jpeg)
    if msg.stop_reason == "max_tokens":
        # A tile cut off at the cap would ship short and pass every check.
        raise RuntimeError(f"tile {idx} {label}: output truncated at the token cap; not cached")
    md = "".join(b.text for b in msg.content if b.type == "text").strip()
    res = {
        "prompt_id": PROMPT_ID, "model": model, "label": label,
        "markdown": md,
        "input_tokens": msg.usage.input_tokens,
        "output_tokens": msg.usage.output_tokens,
        "cached_hit": False,
    }
    p = cache_path_for(key, idx)
    p.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_json(p, res, sort_keys=False)
    print(f"  [ocr]   tile {idx:02d} {label}  in={res['input_tokens']} "
          f"out={res['output_tokens']}  {len(md)} chars")
    return res


def dedupe_overlap(prev: str, cur: str, min_len: int = 60) -> str:
    """Drop the leading chunk of `cur` that repeats the tail of `prev` (the
    row-overlap band gets transcribed twice)."""
    tail = prev[-4000:]
    limit = min(len(tail), len(cur))
    for n in range(limit, min_len - 1, -1):
        if cur[:n] and tail.endswith(cur[:n]):
            return cur[n:].lstrip()
    return cur


async def main_async(args) -> int:
    key = args.key
    canvas = load_canvas(key)
    tiles, n_panels = build_tiles(canvas, args.top_frac, args.rows,
                                  args.overlap, args.panels, args.title_cols)

    print(f"{key}: canvas {canvas.width}x{canvas.height}, {len(tiles)} tiles")
    payloads = []
    n_down = 0
    for t in tiles:
        jpeg, size, down = tile_to_jpeg(canvas, t["box"])
        n_down += down
        payloads.append(jpeg)
        flag = "  DOWNSCALED" if down else ""
        print(f"  {t['label']:>8}  box={t['box']}  -> {size[0]}x{size[1]}  "
              f"{len(jpeg)/1024:.0f} KB{flag}")
    if n_down:
        print(f"  note: {n_down} tile(s) exceeded {MAX_EDGE}px and were downscaled")
    if args.dry_run:
        print("dry-run: no API calls made.")
        return 0

    require_env("ANTHROPIC_API_KEY")
    client = make_client()
    sem = asyncio.Semaphore(args.concurrency)

    results = await asyncio.gather(*[
        ocr_tile(client, sem, args.model, key, i, t["label"], payloads[i])
        for i, t in enumerate(tiles)
    ])

    parts: list[str] = []
    for r in results:
        md = r["markdown"] or ""
        if parts:
            md = dedupe_overlap(parts[-1], md)
        if md.strip():
            parts.append(md)
    body = page_marker(1) + "\n\n" + "\n\n".join(parts) + "\n"   # a poster is one PDF page

    MD_DIR.mkdir(parents=True, exist_ok=True)
    atomic_write_text((MD_DIR / f"{key}.md"), body)

    tok_in = sum(r["input_tokens"] for r in results if not r.get("cached_hit"))
    tok_out = sum(r["output_tokens"] for r in results if not r.get("cached_hit"))
    hits = sum(1 for r in results if r.get("cached_hit"))
    print(f"\nwrote markdown/{key}.md  ({len(body):,} chars)")
    print(f"  tiles={len(tiles)} cache_hits={hits} in={tok_in:,} out={tok_out:,}")

    if MANIFEST.exists():
        m = manifest_load()
        if key in m:
            conv = m[key].setdefault("conversion", {})
            conv.update({
                "tool": "claude-vision",
                "markdown_path": f"markdown/{key}.md",
                "page_count": 1,
                "converted_at": datetime.now(timezone.utc).isoformat(),
                "notes": (f"poster tiled OCR: {len(tiles)} tiles "
                          f"({n_panels} panels x {args.rows} rows "
                          f"+ {args.title_cols} title), prompt={PROMPT_ID}"),
            })
            conv.pop("vision_ocr_needed", None)
            conv.pop("vision_ocr_reason", None)
            manifest_commit(m)
            print(f"  manifest: {key}.conversion updated")
        else:
            print(f"  [warn] {key} not in manifest; markdown written, "
                  f"conversion not recorded")

    jsonl_append(LOG, {
        "key": key, "mode": "poster-tile", "tiles": len(tiles),
        "model": args.model, "prompt_id": PROMPT_ID,
        "input_tokens": tok_in, "output_tokens": tok_out,
        "chars": len(body),
        "at": datetime.now(timezone.utc).isoformat(),
    })
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--key", type=key_arg, required=True)
    ap.add_argument("--rows", type=int, default=2,
                    help="row-tiles per panel (default 2)")
    ap.add_argument("--panels", type=int, default=None,
                    help="force N equal panels instead of gutter detection")
    ap.add_argument("--top-frac", type=float, default=0.08,
                    help="fraction of height treated as the title banner")
    ap.add_argument("--title-cols", type=int, default=3,
                    help="horizontal splits of the title banner (default 3)")
    ap.add_argument("--overlap", type=float, default=0.06,
                    help="vertical overlap between row-tiles")
    ap.add_argument("--model", default=DEFAULT_MODEL)
    ap.add_argument("--concurrency", type=int, default=DEFAULT_CONCURRENCY)
    ap.add_argument("--dry-run", action="store_true",
                    help="show the tiling plan without calling the API")
    return asyncio.run(main_async(ap.parse_args()))


if __name__ == "__main__":
    raise SystemExit(main())
