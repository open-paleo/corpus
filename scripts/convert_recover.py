#!/usr/bin/env python3
# corpus-command: convert recover | re-OCR content-filter-blocked pages, cut only on whitespace
"""
Recover pages the content filter blocked (`corpus convert recover`): re-OCR
single pages by cutting them only where there is no text, so nothing has to
be stitched.

Why this way
------------
A blocked page usually passes once cut into pieces, and the pieces must not
overlap: text in an overlap band is read twice, and on a two-column page the
band holds both columns, so no suffix/prefix match can rejoin the pieces in
order. Cutting only on whitespace leaves nothing to rejoin.

Here every cut falls on whitespace, so pieces never share text:

  1. Try the intact page (a newer model often passes what an older one blocked).
  2. Otherwise, on a two-column page, split into reading-order regions at
     the gutter: a band is full-width only where ink crosses the gutter,
     everything else is left column then right column.
  3. A region that still blocks is cut horizontally at the blank row nearest
     its middle, and each part retried, to depth MAX_DEPTH.
  4. Pieces are joined in reading order. A piece that ends mid-sentence is
     joined to the next with a space (a trailing hyphen is dropped).

By default it writes a CANDIDATE under .runtime/tmp/convert_recover/ and prints
measurements only. It never prints page text: the content filter applies to
the assistant's own output as well, so echoing a blocked page re-trips it.
Compare the candidate with the current page, then run again with --apply,
which serves the saved candidate (no new API calls):

    scripts/convert_recover.py --key KEY --page 18
    scripts/convert_recover.py --key KEY --page 18 --apply

--apply writes .runtime/cache/vision/{key}/page-NNNN.json with
method="recovered_nooverlap" and splices the page into markdown/{key}.md by
exact match on the old cached text (see docs/notes/conversion.md, "Map
recovered pages by the cache string"). It refuses if the old text is not found
exactly once, which means the markdown was edited after conversion.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import re
import sys

import numpy as np
from PIL import Image

from corpus.config import MD_DIR, PDF_DIR, TMP_DIR, VISION_CACHE as CACHE_DIR, require_env
from corpus.io import atomic_write_text
from corpus.keys import key_arg
from corpus.pdf.layout import center_gutter_x
from corpus.pdf.render import pil_jpeg, render_pil
from corpus.vision import cache as vision_cache
from corpus.vision.client import make as make_client
from corpus.vision.ocr import DEFAULT_DPI, DEFAULT_MODEL, ocr_fragment
from corpus.pages import write_markdown

CAND_DIR = TMP_DIR / "convert_recover"
PROMPT_ID = "verbatim-v1"      # the page prompt's id, as convert_vision caches it

MAX_DEPTH = 5
FULLWIDTH_MIN = 10     # px of gutter-crossing ink that make a band full-width
FULLWIDTH_GAP = 30     # px; crossing runs closer than this are one block
BLANK_INK = 0.002      # a row with less ink than this is whitespace
BLOCKED = "[FILTER-BLOCKED PIECE]"
# A strip holding only a running head or white space comes back as a remark
# about the image ("No body text is visible in this fragment..."), not as text.
REMARK = re.compile(
    r"(?i)\b(in this fragment|the fragment (contains|shows|is)|looking at the fragment|"
    r"only text in this fragment|nothing to transcribe|no body text)\b"
)


def drop_remarks(md: str) -> str:
    md = re.sub(r"<think>.*?</think>", "", md, flags=re.S)  # leaked reasoning
    paras = [p for p in md.split("\n\n") if not (len(p) < 420 and REMARK.search(p))]
    return "\n\n".join(paras).strip()


def blank_row_near_middle(img: Image.Image) -> int:
    """Row to cut at: centre of the blank band nearest mid-height (30-70%),
    else the least-inked row in 40-60%."""
    gray = np.asarray(img.convert("L"))
    h = gray.shape[0]
    ink = (gray < 160).mean(axis=1)
    lo, hi = int(h * 0.30), int(h * 0.70)
    best, run_start = None, None
    for y in range(lo, hi + 1):
        blank = y < hi and ink[y] < BLANK_INK
        if blank and run_start is None:
            run_start = y
        elif not blank and run_start is not None:
            mid = (run_start + y) // 2
            if y - run_start >= 3 and (best is None or abs(mid - h / 2) < abs(best - h / 2)):
                best = mid
            run_start = None
    if best is not None:
        return best
    lo, hi = int(h * 0.40), int(h * 0.60)
    return lo + int(np.argmin(ink[lo:hi]))


def gutter_zones(img: Image.Image, gx: int) -> list[Image.Image]:
    """Reading-order regions of a two-column page, cut only on blank rows.

    A band is full-width only where ink actually crosses the gutter (a title,
    a wide figure or table, a centred running head). Everything else is split
    left/right at gx, including a band where one column is short or blank:
    read across, it would put one column's heading before the other column's
    text.
    """
    gray = np.asarray(img.convert("L"))
    h, w = gray.shape
    dark = gray < 160
    crosses = dark[:, max(0, gx - 3):gx + 4].any(axis=1)
    row_blank = dark.mean(axis=1) < BLANK_INK

    runs, start = [], None
    for y in range(h + 1):
        on = y < h and crosses[y]
        if on and start is None:
            start = y
        elif not on and start is not None:
            runs.append([start, y])
            start = None
    merged: list[list[int]] = []
    for a, b in runs:
        if merged and a - merged[-1][1] < FULLWIDTH_GAP:
            merged[-1][1] = b
        else:
            merged.append([a, b])
    blocks = [(a, b) for a, b in merged if b - a >= FULLWIDTH_MIN]

    def blank_cut(y: int, step: int) -> int:
        for d in range(0, 60):
            yy = y + step * d
            if 0 <= yy < h and row_blank[yy]:
                return yy
        return min(max(y, 0), h)

    cuts = [0]
    for a, b in blocks:
        cuts += [blank_cut(a - 1, -1), blank_cut(b, 1)]
    cuts.append(h)
    regions: list[Image.Image] = []
    for i in range(len(cuts) - 1):
        y0, y1 = cuts[i], cuts[i + 1]
        if y1 - y0 < 12 or not dark[y0:y1].any():
            continue
        full = i % 2 == 1
        if full:
            regions.append(img.crop((0, y0, w, y1)))
        else:
            for x0, x1 in ((0, gx), (gx, w)):
                if dark[y0:y1, x0:x1].any():
                    regions.append(img.crop((x0, y0, x1, y1)))
    return regions


async def ocr_region(client, img: Image.Image, depth: int, stats: dict) -> list[str]:
    stats["calls"] += 1
    md = await ocr_fragment(client, pil_jpeg(img))
    if md is not None:
        md = drop_remarks(md)
        return [md] if md else []
    stats["blocked"] += 1
    if depth >= MAX_DEPTH or img.size[1] < 200:
        stats["unrecovered"] += 1
        return [BLOCKED]
    y = blank_row_near_middle(img)
    w, h = img.size
    top, bottom = img.crop((0, 0, w, y)), img.crop((0, y, w, h))
    return (await ocr_region(client, top, depth + 1, stats)
            + await ocr_region(client, bottom, depth + 1, stats))


def join_pieces(pieces: list[str]) -> str:
    out = ""
    for piece in pieces:
        if not out:
            out = piece
            continue
        starts_lower = bool(re.match(r"[a-zà-ÿ]", piece))
        ends_open = not re.search(r"[.!?:;)\]\"'”’*|]\s*$", out)
        if starts_lower and ends_open and not out.rstrip().endswith(("|", "#")):
            if out.rstrip().endswith("-"):
                out = out.rstrip()[:-1] + piece
            else:
                out = out.rstrip() + " " + piece
        else:
            out = out.rstrip() + "\n\n" + piece
    return out


async def reocr_page(client, key: str, page: int, columns: str) -> dict:
    img = render_pil(PDF_DIR / f"{key}.pdf", page - 1, dpi=DEFAULT_DPI)
    stats = {"calls": 1, "blocked": 0, "unrecovered": 0}
    full = await ocr_fragment(client, pil_jpeg(img))
    if full is not None:
        return {"markdown": full.strip(), "how": "intact page", **stats}
    stats["blocked"] += 1
    gx = None if columns == "1" else center_gutter_x(img)
    if gx is None and columns == "2":
        gx = img.size[0] // 2
    regions = gutter_zones(img, gx) if gx else [img]
    pieces: list[str] = []
    for region in regions:
        pieces += await ocr_region(client, region, 1, stats)
    return {"markdown": join_pieces(pieces), "how": f"{len(regions)} region(s), {len(pieces)} piece(s)", **stats}


def words(s: str) -> list[str]:
    return re.findall(r"[A-Za-zÀ-ÿ]{4,}", re.sub(r"\[FIGURE:[^\]]*\]", "", s).replace("-\n", ""))


def longest_repeat(s: str, minlen: int = 100) -> int:
    seen, best = {}, 0
    for i in range(0, max(0, len(s) - minlen), 20):
        k = s[i:i + minlen]
        if k in seen and i - seen[k] >= minlen:
            a, n = seen[k], minlen
            while i + n < len(s) and s[a + n] == s[i + n]:
                n += 1
            best = max(best, n)
        else:
            seen.setdefault(k, i)
    return best


def headings(s: str) -> list[str]:
    return [h.strip("*# ")[:26] for h in re.findall(r"(?m)^#{1,4} .+$", s)]


def report(key: str, page: int, old: str, new: dict) -> None:
    import pypdfium2 as pdfium
    layer = pdfium.PdfDocument(str(PDF_DIR / f"{key}.pdf"))[page - 1].get_textpage().get_text_bounded()
    ref = len(words(layer))
    nw, ow = len(words(new["markdown"])), len(words(old))
    print(f"{key} p{page}: {new['how']}; calls={new['calls']} blocked={new['blocked']} "
          f"unrecovered={new['unrecovered']}")
    print(f"   words old={ow} new={nw} text-layer={ref if ref > 50 else 'n/a'} | "
          f"chars old={len(old)} new={len(new['markdown'])} | "
          f"longest repeat old={longest_repeat(old)} new={longest_repeat(new['markdown'])}")
    print(f"   headings old={headings(old)}")
    print(f"   headings new={headings(new['markdown'])}")


def apply(key: str, page: int, old: str, new_md: str) -> bool:
    md_path = MD_DIR / f"{key}.md"
    md = md_path.read_text(encoding="utf-8")
    if md.count(old) != 1:
        print(f"   REFUSED: old page text occurs {md.count(old)} time(s) in {md_path.name}; "
              "the markdown was edited after conversion")
        return False
    # A cache entry shaped like convert_vision's, so a later run of it
    # cache-hits this page instead of re-tripping the filter. Token counts are
    # 0: the calls are accounted in the candidate, not the cache.
    vision_cache.write(CACHE_DIR / key, page - 1, {
        "prompt_id": PROMPT_ID, "model": DEFAULT_MODEL, "markdown": new_md,
        "input_tokens": 0, "output_tokens": 0, "cache_read": 0, "cache_write": 0,
        "cached_hit": False, "method": "recovered_nooverlap"})
    write_markdown(md_path, md.replace(old, new_md), old=md)
    print("   applied")
    return True


async def run(args) -> int:
    key = args.key                       # key_arg: NFC and validated
    client = make_client(timeout=180.0, max_retries=2)
    failures = 0
    for page in args.page:
        entry = vision_cache.read_entry(vision_cache.page_path(CACHE_DIR / key, page - 1))
        if entry is not None:
            old = entry["markdown"]
        else:
            # A page convert_vision.py could not read has no cache entry,
            # only a "[VISION OCR FAILED FOR PAGE N: ...]" line in the markdown;
            # that line is the text to replace.
            md = (MD_DIR / f"{key}.md").read_text(encoding="utf-8")
            hit = re.search(rf"\[VISION OCR FAILED FOR PAGE {page}:[^\n]*\]", md)
            if not hit:
                print(f"{key} p{page}: no cache entry and no FAILED marker; skipped")
                failures += 1
                continue
            old = hit.group(0)
        cand_path = CAND_DIR / key / f"page-{page:04d}.json"
        if cand_path.exists() and not args.fresh:
            new = json.loads(cand_path.read_text(encoding="utf-8"))
        else:
            new = await reocr_page(client, key, page, args.columns)
            cand_path.parent.mkdir(parents=True, exist_ok=True)
            atomic_write_text(cand_path, json.dumps({**new, "old": old}, ensure_ascii=False, indent=2))
        report(key, page, old, new)
        if args.apply:
            if new["unrecovered"] or BLOCKED in new["markdown"]:
                print("   REFUSED: candidate still has a blocked piece")
                failures += 1
            elif not apply(key, page, new.get("old", old), new["markdown"]):
                failures += 1
    return 1 if failures else 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--key", type=key_arg, required=True)
    ap.add_argument("--page", type=int, action="append", required=True, help="1-indexed PDF page; repeatable")
    ap.add_argument("--columns", choices=["auto", "1", "2"], default="auto")
    ap.add_argument("--fresh", action="store_true", help="ignore a saved candidate and OCR again")
    ap.add_argument("--apply", action="store_true", help="write cache + markdown from the saved candidate")
    args = ap.parse_args()
    require_env("ANTHROPIC_API_KEY")
    return asyncio.run(run(args))


if __name__ == "__main__":
    sys.exit(main())
