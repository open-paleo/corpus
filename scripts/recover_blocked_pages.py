#!/usr/bin/env python3
"""
Recover individual pages where convert_with_vision.py hit a content-filter
block. See docs/notes/conversion.md ("Content-filter blocks"): the trigger is
localized — splitting the page geometrically and OCRing the halves
separately almost always succeeds.

Strategy per failed page:
  1. Render the page as JPEG at the same DPI used by convert_with_vision.
  2. Split horizontally (top / bottom, 20% overlap).
  3. If a half still 400-blocks, sub-split it — but the direction depends on
     the page layout, detected from the ink-density profile:
       - two-column page (a sustained whitespace gutter near mid-width):
         split VERTICALLY at the detected gutter, since columns don't span it.
       - single-column page: split into full-width horizontal bands.
     Never cut a single-column page vertically: the cut runs down the middle
     of every line of running text, so each half holds line fragments rather
     than lines, and no suffix/prefix stitch can rejoin them (they interleave
     line by line instead of overlapping). That silently produced shredded
     markdown on cope1868a/leidy1868b and 13 other keys before this check
     existed; scripts/reocr_hbands.py is the repair tool for pages already
     damaged that way.
  4. If a piece still blocks, leave a `[FILTER-BLOCKED PIECE]` marker
     so the user can spot it.
  5. Stitch the recovered pieces with fuzzy-suffix-prefix dedup.
  6. Save a synthetic cache entry at .runtime/cache/vision/{key}/page-NNNN.json
     keyed to prompt_id="verbatim-v1" / model=models.SONNET (older Sonnet entries still hit), so
     future convert_with_vision runs hit cache instead of re-blocking.
  7. Splice the recovered text into markdown/{key}.md, replacing the
     `[VISION OCR FAILED FOR PAGE N: ...]` line.

Run after a vision OCR run that reported `partial`:
  ./.runtime/venv/bin/python scripts/recover_blocked_pages.py --key sereno2000
  ./.runtime/venv/bin/python scripts/recover_blocked_pages.py --key christiansen2003
"""
from __future__ import annotations

import argparse
import asyncio
import base64
import io
import json
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from PIL import Image
from anthropic import APIStatusError, AsyncAnthropic
from corpus.config import PDF_DIR, MD_DIR, RUNTIME, VISION_CACHE as CACHE_DIR

LOG = RUNTIME / "logs" / "vision.jsonl"

# Minimum share of a page's ink each side of a candidate gutter must carry
# before it counts as a real column boundary rather than a wide margin.
MIN_COLUMN_INK_SHARE = 0.20

from corpus.models import SONNET
from corpus.keys import key_arg
from corpus.pdf.render import render_pil
from corpus.io import atomic_write_json
from corpus.config import require_env
from corpus.vision.client import make as make_client
from corpus.io import atomic_write_text
MODEL = SONNET
PROMPT_ID = "verbatim-v1"
DPI = 150
JPEG_QUALITY = 85

# Same prompt as convert_with_vision.py PROMPT_VERBATIM, but applied to
# fragments. Telling the model the input is a partial page reduces
# hallucinated continuations at the cut line.
PROMPT_FRAGMENT = """You are an OCR engine. Extract the text on this page fragment into clean Markdown, verbatim.

This is a *fragment* of a larger page (the page was split geometrically to work around output blocks). Transcribe only what you see; do not invent text past the edge.

Rules:
- Preserve italic formatting for genus and species names (e.g. *Tyrannosaurus rex*).
- Preserve heading structure (use # / ## as appropriate).
- Preserve table structure where present.
- Preserve in-text citations and bibliographic references exactly.
- Skip page headers, page footers, and page numbers.
- For figures: emit `[FIGURE: <one-line description>]` followed by the caption verbatim.
- Do NOT invent text. If part of the fragment is illegible or cut off, stop there — do not guess what comes next.
- Output the markdown body only — no preamble, no explanation."""

FAILED_LINE_RE = re.compile(
    r"^\[VISION OCR FAILED FOR PAGE (\d+):.*?\]$", re.MULTILINE
)


def render_page_jpeg(pdf_path: Path, page_index: int, dpi: int) -> Image.Image:
    """Render one PDF page to a PIL Image (RGB)."""
    return render_pil(pdf_path, page_index, dpi=dpi)

def img_to_jpeg_bytes(img: Image.Image) -> bytes:
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=JPEG_QUALITY)
    return buf.getvalue()


def horizontal_halves(img: Image.Image, overlap_frac: float = 0.20) -> tuple[Image.Image, Image.Image]:
    w, h = img.size
    cut = h // 2
    over = int(h * overlap_frac / 2)
    top = img.crop((0, 0, w, cut + over))
    bottom = img.crop((0, cut - over, w, h))
    return top, bottom


def vertical_halves(img: Image.Image, overlap_frac: float = 0.10,
                    cut: int | None = None) -> tuple[Image.Image, Image.Image]:
    w, h = img.size
    if cut is None:
        cut = w // 2
    over = int(w * overlap_frac / 2)
    left = img.crop((0, 0, min(w, cut + over), h))
    right = img.crop((max(0, cut - over), 0, w, h))
    return left, right


def horizontal_bands(img: Image.Image, bands: int = 2,
                     overlap_frac: float = 0.10) -> list[Image.Image]:
    """Cut into `bands` full-width strips, each overlapping the next."""
    w, h = img.size
    step = h / bands
    pad = step * overlap_frac
    out = []
    for i in range(bands):
        top = max(0, int(i * step - pad))
        bot = min(h, int((i + 1) * step + pad))
        out.append(img.crop((0, top, w, bot)))
    return out


def _gutter_x_in(block, w: int, ink_thresh: int, min_width: int) -> int | None:
    """Core detector: the x of a whitespace gutter in one image block, or None.

    Only the middle 30-70% of the width is considered, so page margins and the
    gap beside a figure don't read as a gutter. A blank strip counts only if
    BOTH sides carry at least MIN_COLUMN_INK_SHARE of the block's ink —
    without that, a single-column page whose text block sits off-centre reads
    as two-column (jin2012a: text offset right, blank strip at 33% width).
    """
    if block.shape[0] < 40:
        return None
    ink = (block < ink_thresh).astype("uint8")
    prof = ink.sum(axis=0)
    thr = max(3, int(0.01 * block.shape[0]))
    lo, hi = int(w * 0.30), int(w * 0.70)

    best, start = None, None
    for x in range(lo, hi):
        if prof[x] < thr:
            if start is None:
                start = x
        else:
            if start is not None and x - start >= min_width:
                if best is None or (x - start) > (best[1] - best[0]):
                    best = (start, x)
            start = None
    if start is not None and hi - start >= min_width:
        if best is None or (hi - start) > (best[1] - best[0]):
            best = (start, hi)
    if best is None:
        return None

    total = int(prof.sum())
    if total <= 0:
        return None
    left = int(prof[:best[0]].sum()) / total
    right = int(prof[best[1]:].sum()) / total
    if min(left, right) < MIN_COLUMN_INK_SHARE:
        return None
    return (best[0] + best[1]) // 2


def center_gutter_x(img: Image.Image, ink_thresh: int = 160,
                    min_width_frac: float = 0.012) -> int | None:
    """Return the x of a sustained whitespace gutter near mid-width, or None.

    Tries the whole body first, then six horizontal slices: a page whose text
    is set in two columns *below* a full-width figure shows no gutter in the
    whole-body profile (the figure puts ink right across the middle) but does
    in the slices that contain only text. Two slices agreeing is enough.
    """
    import numpy as np

    gray = np.asarray(img.convert("L"))
    h, w = gray.shape
    # Ignore the top 12% so a full-width running head doesn't mask the gutter.
    body = gray[int(h * 0.12):, :]
    min_width = max(4, int(w * min_width_frac))

    whole = _gutter_x_in(body, w, ink_thresh, min_width)
    if whole is not None:
        return whole

    slices = 6
    step = body.shape[0] // slices
    found = [g for g in (_gutter_x_in(body[i * step:(i + 1) * step, :], w,
                                      ink_thresh, min_width)
                         for i in range(slices)) if g is not None]
    if len(found) >= 2:
        found.sort()
        if found[-1] - found[0] <= w * 0.05:
            return found[len(found) // 2]
    return None


def column_regions(img: Image.Image, slices: int = 24,
                   min_run_frac: float = 0.20,
                   min_run_slices: int = 3) -> list[Image.Image]:
    """Split a page into sub-images in READING ORDER.

    Handles the mixed layouts that a single gutter x cannot describe: a
    full-width title/abstract or figure on top with two columns beneath
    (sander1992a p1, chatterjee1991a p28). Scans horizontal slices for a
    gutter, finds the longest contiguous run of slices that agree on one, and
    returns:

        [full-width above the run, left column, right column, full-width below]

    omitting any empty region. A page with no sustained gutter returns
    ``[img]`` unchanged, so callers can always just iterate the result.
    """
    import numpy as np

    gray = np.asarray(img.convert("L"))
    h, w = gray.shape
    step = max(1, h // slices)
    min_width = max(4, int(w * 0.012))
    xs: list[int | None] = []
    for i in range(slices):
        top, bot = i * step, min(h, (i + 1) * step)
        xs.append(_gutter_x_in(gray[top:bot, :], w, 160, min_width))

    # Longest contiguous run of slices agreeing on a gutter x (within 5% w).
    best = None
    i = 0
    while i < len(xs):
        if xs[i] is None:
            i += 1
            continue
        j = i
        vals = []
        while j < len(xs) and xs[j] is not None and \
                abs(xs[j] - xs[i]) <= w * 0.05:
            vals.append(xs[j])
            j += 1
        if best is None or (j - i) > (best[1] - best[0]):
            best = (i, j, int(np.median(vals)))
        i = j

    run_slices = 0 if best is None else best[1] - best[0]
    if best is None or run_slices < min_run_slices or \
            run_slices * step < h * min_run_frac:
        return [img]

    y0, y1, gutter = best[0] * step, min(h, best[1] * step), best[2]
    over = int(w * 0.02)
    regions: list[Image.Image] = []
    if y0 > h * 0.03:
        regions.append(img.crop((0, 0, w, y0)))
    regions.append(img.crop((0, y0, min(w, gutter + over), y1)))
    regions.append(img.crop((max(0, gutter - over), y0, w, y1)))
    if y1 < h * 0.97:
        regions.append(img.crop((0, y1, w, h)))
    return regions


def normalize_for_match(s: str) -> str:
    return re.sub(r"[^a-z0-9 ]+", "", s.lower())


def fuzzy_suffix_prefix_trim(a: str, b: str,
                              min_overlap: int = 40,
                              max_overlap: int = 300) -> str:
    """Find the longest suffix of A (between min/max chars, normalized) that
    appears as a prefix-region of B. Trim B from that match. Returns the
    trimmed B. If no overlap found, returns B unchanged."""
    if not a or not b:
        return b
    a_norm = normalize_for_match(a[-max_overlap * 3:])  # search in tail
    # Build B's normalized prefix region with index map back to original
    b_search_window = b[: max_overlap * 3]
    b_norm_chars = []
    b_idx_map = []
    for i, ch in enumerate(b_search_window):
        n = ch.lower()
        if n.isalnum() or n == " ":
            b_norm_chars.append(n)
            b_idx_map.append(i)
    b_norm = "".join(b_norm_chars)

    best_match_end_in_b = -1
    for L in range(min(max_overlap, len(a_norm)), min_overlap - 1, -1):
        suffix = a_norm[-L:]
        idx = b_norm.find(suffix)
        if idx != -1 and idx < L:  # match must be near the start of B
            best_match_end_in_b = b_idx_map[idx + L - 1] + 1
            break
    if best_match_end_in_b == -1:
        return b
    return b[best_match_end_in_b:]


async def call_vision(client: AsyncAnthropic, jpeg: bytes,
                      prompt_text: str = PROMPT_FRAGMENT) -> str | None:
    """Returns markdown text, or None if blocked."""
    b64 = base64.standard_b64encode(jpeg).decode("ascii")
    try:
        msg = await client.messages.create(
            model=MODEL,
            max_tokens=4096,
            system=[{
                "type": "text",
                "text": prompt_text,
                "cache_control": {"type": "ephemeral"},
            }],
            messages=[{
                "role": "user",
                "content": [{
                    "type": "image",
                    "source": {
                        "type": "base64",
                        "media_type": "image/jpeg",
                        "data": b64,
                    },
                }],
            }],
        )
    except APIStatusError as e:
        body = (e.response.text or "") if e.response else ""
        if e.status_code == 400 and "content filtering" in body:
            return None
        raise
    parts = [b.text for b in msg.content if b.type == "text"]
    return "".join(parts).strip()


async def recover_page(client: AsyncAnthropic, pdf_path: Path,
                       page_index: int) -> str:
    """Recover one blocked page by progressive geometric splitting.
    Returns the stitched markdown."""
    page_img = render_page_jpeg(pdf_path, page_index, DPI)

    # Layer 1: try full page once more (sometimes flakes resolve)
    full = await call_vision(client, img_to_jpeg_bytes(page_img))
    if full is not None:
        return full

    # Layer 2: horizontal halves
    top, bottom = horizontal_halves(page_img)
    top_md = await call_vision(client, img_to_jpeg_bytes(top))
    bottom_md = await call_vision(client, img_to_jpeg_bytes(bottom))

    if top_md is not None and bottom_md is not None:
        bottom_trimmed = fuzzy_suffix_prefix_trim(top_md, bottom_md)
        return top_md + "\n\n" + bottom_trimmed

    # Layer 3: any half that still blocks gets sub-split — direction chosen
    # from the layout of the FULL page (a half of a two-column page still has
    # the gutter, but measuring on the whole page is more reliable).
    async def split_stubborn(half: Image.Image) -> str:
        # column_regions returns sub-images in reading order: a single-column
        # half comes back as [half] and is banded horizontally (lines stay
        # whole); a two-column half comes back as full-width/left/right/full-
        # width and each region is banded within itself, so the columns never
        # interleave.
        regions = column_regions(half)
        pieces: list[Image.Image] = []
        for region in regions:
            pieces.extend(horizontal_bands(region, bands=2)
                          if len(regions) == 1 else [region])
        acc = ""
        for piece in pieces:
            md = await call_vision(client, img_to_jpeg_bytes(piece))
            if md is None:
                md = "[FILTER-BLOCKED PIECE]"
            acc = md if not acc else acc + "\n\n" + fuzzy_suffix_prefix_trim(acc, md)
        return acc

    if top_md is None:
        top_md = await split_stubborn(top)
    if bottom_md is None:
        bottom_md = await split_stubborn(bottom)

    bottom_trimmed = fuzzy_suffix_prefix_trim(top_md, bottom_md)
    return top_md + "\n\n" + bottom_trimmed


def write_synthetic_cache(cache_dir: Path, page_number: int, markdown: str) -> Path:
    """Save a cache entry shaped like convert_with_vision.py's. Token usage
    is set to 0 since the recovery happened via this script and the
    accounting goes into vision.jsonl with method="recovered_split"."""
    cache_dir.mkdir(parents=True, exist_ok=True)
    cache_path = cache_dir / f"page-{page_number:04d}.json"
    payload = {
        "prompt_id": PROMPT_ID,
        "model": MODEL,
        "markdown": markdown,
        "input_tokens": 0,
        "output_tokens": 0,
        "cache_read": 0,
        "cache_write": 0,
        "cached_hit": False,
        "method": "recovered_split",
    }
    atomic_write_json(cache_path, payload, sort_keys=False)
    return cache_path


def splice_into_markdown(md_path: Path, page_number: int, recovered: str) -> bool:
    """Replace the `[VISION OCR FAILED FOR PAGE N: ...]` line in the markdown
    with the recovered text. Returns True if a line was replaced."""
    text = md_path.read_text(encoding="utf-8")
    pattern = re.compile(
        r"^\[VISION OCR FAILED FOR PAGE " + str(page_number) + r":.*?\]$",
        re.MULTILINE,
    )
    new_text, n = pattern.subn(recovered, text, count=1)
    if n == 0:
        return False
    atomic_write_text(md_path, new_text)
    return True


async def main_async(args) -> int:
    require_env("ANTHROPIC_API_KEY")

    client = make_client()
    md_path = MD_DIR / f"{args.key}.md"
    pdf_path = PDF_DIR / f"{args.key}.pdf"
    if not md_path.exists():
        print(f"ERROR: {md_path} not found", file=sys.stderr); return 2
    if not pdf_path.exists():
        print(f"ERROR: {pdf_path} not found", file=sys.stderr); return 2

    text = md_path.read_text(encoding="utf-8")
    failed_pages = sorted({int(m) for m in FAILED_LINE_RE.findall(text)})
    if not failed_pages:
        print(f"No FAILED markers found in {md_path}.")
        return 0

    print(f"Recovering {len(failed_pages)} blocked page(s) for {args.key}: {failed_pages}")
    cache_dir = CACHE_DIR / args.key
    LOG.parent.mkdir(parents=True, exist_ok=True)
    log_fh = LOG.open("a")

    for page_number in failed_pages:
        page_index = page_number - 1
        started = time.time()
        try:
            recovered = await recover_page(client, pdf_path, page_index)
        except Exception as e:
            print(f"  [{page_number}] recovery raised: {e}")
            log_fh.write(json.dumps({
                "ts": datetime.now(timezone.utc).isoformat(),
                "key": args.key, "page": page_number,
                "method": "recovered_split", "outcome": "error",
                "error": str(e),
            }) + "\n")
            continue
        cache_path = write_synthetic_cache(cache_dir, page_number, recovered)
        spliced = splice_into_markdown(md_path, page_number, recovered)
        elapsed = time.time() - started
        n_blocked_quarters = recovered.count("[FILTER-BLOCKED QUARTER]")
        marker = "✓" if n_blocked_quarters == 0 else f"!{n_blocked_quarters}q"
        print(f"  [{page_number}] {marker} {len(recovered):,} chars · {elapsed:.1f}s · cache={cache_path.name} · spliced={spliced}")
        log_fh.write(json.dumps({
            "ts": datetime.now(timezone.utc).isoformat(),
            "key": args.key, "page": page_number,
            "method": "recovered_split",
            "outcome": "ok" if n_blocked_quarters == 0 else "partial",
            "blocked_quarters": n_blocked_quarters,
            "chars": len(recovered),
            "elapsed_s": elapsed,
        }) + "\n")
    log_fh.close()
    print("Done.")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--key", type=key_arg, required=True)
    args = ap.parse_args()
    return asyncio.run(main_async(args))


if __name__ == "__main__":
    raise SystemExit(main())
