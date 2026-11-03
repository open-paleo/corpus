#!/usr/bin/env python3
# corpus-command: qc control-glyphs | find what glyph a broken font's control codes draw (for repair control_glyphs)
"""Identify what glyph a broken font's control code actually draws.

`corpus repair control_glyphs` needs a per-paper mapping such as
`--map 01=U+2212` and refuses to guess. This produces that mapping from
evidence instead of inference.

Two observations make it mechanical:

  * The PDF text layer reports the FONT NAME and the exact BOUNDING BOX of every
    character, controls included. So the glyph can be cropped and looked at —
    it either draws a minus sign or it doesn't.
  * The broken fonts are publisher subset fonts (Elsevier's AdvP4C4E74, for
    one) that recur across many papers. Reading each (font, code) once, rather
    than each paper, collapses hundreds of identifications into a handful.

Modes
-----
  --scan   census of distinct (font, control code) pairs across the corpus,
           with a representative occurrence recorded for each. Read-only.
  --sheet  render the representative occurrences into contact-sheet PNGs, one
           labelled crop per pair, for visual identification.
  --expand READINGS
           turn the readings into the per-paper map that `repair
           control_glyphs --map-file` takes, for every paper in the census.

The readings are NOT produced automatically: a wrong substitution is worse
than a missing character, so a person reads the sheets and writes them as JSON,
one entry per font, code in hex and glyph as a character or U+ code point:

    {"AdvP4C4E74": {"01": "U+2212", "03": "~"}}

Then, for each paper the expansion lists:

    corpus repair control_glyphs --key KEY --map-file control_glyph_map.json --apply
"""

from __future__ import annotations

import argparse
import json
from collections import defaultdict
from pathlib import Path

from corpus.pdf.text import open_pdf
from corpus.config import PDF_DIR, RUNTIME
from corpus.keys import key_from_path
from corpus.keys import read_keys_file
from corpus.io import atomic_write_text

OUT = RUNTIME / "control_glyphs"


def scan_pdf(path: Path, max_pages: int = 8):
    """[(font, code, page, bbox)] — one representative per (font, code)."""
    seen = {}
    try:
        doc = open_pdf(path)
    except Exception:
        return []
    for pno in range(min(len(doc), max_pages)):
        try:
            raw = doc.rawdict(pno)
        except Exception:
            continue
        for block in raw.get("blocks", []):
            for line in block.get("lines", []):
                for span in line.get("spans", []):
                    font = span.get("font", "?")
                    for ch in span.get("chars", []):
                        c = ch["c"]
                        if ord(c) >= 32 or c in "\r\n\t":
                            continue
                        key = (font, ord(c))
                        if key not in seen:
                            seen[key] = (pno, tuple(round(v, 1) for v in ch["bbox"]))
    doc.close()
    return [(f, c, p, b) for (f, c), (p, b) in seen.items()]


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    mode = ap.add_mutually_exclusive_group(required=True)
    mode.add_argument("--scan", action="store_true", help="census of (font, control code) pairs")
    mode.add_argument("--sheet", action="store_true", help="contact sheets of the census's glyphs")
    mode.add_argument("--expand", metavar="READINGS",
                      help="JSON of readings per font -> the per-paper map for repair control_glyphs")
    ap.add_argument("--keys-file", help="one key per line; default = all PDFs")
    ap.add_argument("--max-pages", type=int, default=8)
    ap.add_argument("--per-sheet", type=int, default=48)
    args = ap.parse_args()

    OUT.mkdir(parents=True, exist_ok=True)
    census_path = OUT / "census.json"

    if args.scan:
        if args.keys_file:
            keys = read_keys_file(args.keys_file)
            pdfs = [PDF_DIR / f"{k}.pdf" for k in keys]
        else:
            pdfs = sorted(PDF_DIR.glob("*.pdf"))
        pairs = defaultdict(list)      # (font, code) -> [(key, page, bbox)]
        for i, p in enumerate(pdfs):
            if not p.exists():
                continue
            for font, code, page, bbox in scan_pdf(p, args.max_pages):
                pairs[f"{font}\t{code}"].append([key_from_path(p), page, bbox])
        census = {k: v for k, v in sorted(pairs.items(), key=lambda kv: -len(kv[1]))}
        atomic_write_text(census_path, json.dumps(census, indent=1))
        fonts = {k.split("\t")[0] for k in census}
        files = {f for v in census.values() for f, _, _ in v}
        print(f"distinct (font, code) pairs : {len(census)}")
        print(f"distinct broken fonts       : {len(fonts)}")
        print(f"files touched               : {len(files)}")
        print(f"\nwritten: {census_path}")
        print(f"\n{'font':<28}{'code':>6}{'files':>7}  example")
        for k, v in list(census.items())[:30]:
            font, code = k.split("\t")
            print(f"  {font:<26}{hex(int(code)):>6}{len(v):>7}  {v[0][0]}")
        return 0

    if args.sheet:
        census = json.loads(census_path.read_text(encoding="utf-8"))
        items = list(census.items())
        n = 0
        for s in range(0, len(items), args.per_sheet):
            chunk = items[s:s + args.per_sheet]
            crops = []
            for k, v in chunk:
                font, code = k.split("\t")
                key, page, bbox = v[0]
                try:
                    doc = open_pdf(PDF_DIR / f"{key}.pdf")
                    x0, y0, x1, y1 = bbox
                    # tight: 3pt of padding pulls in the neighbouring glyphs
                    # and the target stops being identifiable
                    pad = 0.4
                    png = doc.render_png(page, (x0 - pad, y0 - pad, x1 + pad, y1 + pad), 600)
                    crops.append((f"{hex(int(code))} {font[:18]}", png))
                    doc.close()
                except Exception:
                    continue
            if not crops:
                continue
            n += 1
            _compose(crops, OUT / f"sheet-{n:02d}.png")
            print(f"  wrote {OUT / f'sheet-{n:02d}.png'}  ({len(crops)} glyphs)")
        return 0

    readings = json.loads(Path(args.expand).read_text(encoding="utf-8"))
    census = json.loads(census_path.read_text(encoding="utf-8"))
    mapping: dict[str, dict[str, str]] = defaultdict(dict)
    for pair, occurrences in census.items():
        font, code = pair.split("\t")
        glyph = (readings.get(font) or {}).get(f"{int(code):02x}")
        if glyph is None:
            continue
        if glyph.upper().startswith("U+"):
            glyph = chr(int(glyph[2:], 16))
        for key, _, _ in occurrences:
            mapping[key][f"{int(code):02x}"] = glyph
    out = OUT / "control_glyph_map.json"
    atomic_write_text(out, json.dumps(mapping, ensure_ascii=False, indent=1, sort_keys=True))
    print(f"{len(mapping)} paper(s) mapped; written: {out}")
    for key in sorted(mapping):
        print(f"  {key}: {mapping[key]}")
    return 0


def _compose(crops, path: Path):
    """Grid the crops with a label under each."""
    from PIL import Image, ImageDraw
    import io
    cell_w, cell_h, lab_h, cols = 170, 120, 22, 6
    rows = (len(crops) + cols - 1) // cols
    sheet = Image.new("RGB", (cols * cell_w, rows * (cell_h + lab_h)), "white")
    draw = ImageDraw.Draw(sheet)
    for i, (label, png) in enumerate(crops):
        im = Image.open(io.BytesIO(png))
        im.thumbnail((cell_w - 12, cell_h - 12))
        cx, cy = (i % cols) * cell_w, (i // cols) * (cell_h + lab_h)
        sheet.paste(im, (cx + (cell_w - im.width) // 2, cy + (cell_h - im.height) // 2))
        draw.rectangle([cx, cy, cx + cell_w - 1, cy + cell_h + lab_h - 1], outline="#bbb")
        draw.text((cx + 4, cy + cell_h + 4), label, fill="black")
    sheet.save(path)


if __name__ == "__main__":
    raise SystemExit(main())
