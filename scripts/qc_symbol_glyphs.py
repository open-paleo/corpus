#!/usr/bin/env python3
# corpus-command: qc symbol-glyphs | find coordinate symbols a text layer hands back as the wrong character
"""Find coordinate glyphs a PDF's text layer hands back as the wrong character.

`corpus repair symbols` restores symbol glyphs a text layer drops as control
characters. This is the other failure: some symbol fonts are subset with the
digit and punctuation glyph *names* reused for °, ′ and ″, so extraction yields
a well-formed printable character and nothing downstream can tell:

    (N49°04′57.5″/    ->  (N49u04957.50/     (an Elsevier AdvP7DA6 font)
    (79°27′E          ->  (79(27#E           (an ESSTIX-Four font)
    25°28′15″N        ->  258 28' 15" N

`/Differences` cannot resolve these (AdvP7DA6 names its prime `/nine`), so the
only authority is the rendered page. What the text layer *does* say reliably is
which font each character came from, and a coordinate's degree mark always
comes from a different font than the digits around it. That is the signal this
script reports: characters inside a coordinate-shaped run whose font differs
from the run's dominant font, grouped by (font family, character code) so one
verified reading covers every occurrence in the corpus.

Read each reported run off its crop (--crops) and correct the confirmed
coordinates in the markdown.

    scripts/qc_symbol_glyphs.py --key KEY
    scripts/qc_symbol_glyphs.py --keys-file keys.txt --crops crops/
"""
from __future__ import annotations

import argparse
import re
from collections import defaultdict
from pathlib import Path

from corpus.config import PDF_DIR
from corpus.keys import read_keys_file
from corpus.keys import key_arg
from corpus.pdf.text import open_pdf
from corpus.repair.symbols import family

# A coordinate run: a hemisphere letter with digits and separators before it, or
# an explicit lat/long word. Deliberately loose — this is a candidate finder.
COORD = re.compile(
    r"(?:[NSEW]\s*)?\d{1,3}\s*\S{0,3}\s*\d{1,2}(?:\s*\S{0,3}\s*\d{1,2}(?:[.,]\d{1,3})?)?"
    r"\s*\S{0,3}\s*[NSEW](?![a-z])"
)
CONTEXT = 46


def line_runs(doc, pno):
    """Yield (text, [font per char], line bbox) for each line on the page."""
    for blk in doc.rawdict(pno).get("blocks", []):
        for line in blk.get("lines", []):
            txt, fonts = "", []
            for span in line.get("spans", []):
                for ch in span["chars"]:
                    txt += ch["c"]
                    fonts.append(family(span["font"]))
            if txt.strip():
                yield txt, fonts, line["bbox"]


def scan(key: str):
    """Every coordinate-shaped run in the PDF, with its minority-font chars."""
    pdf = PDF_DIR / f"{key}.pdf"
    if not pdf.exists():
        return []
    doc = open_pdf(pdf)
    try:
        out = []
        for pno in range(len(doc)):
            for txt, fonts, bbox in line_runs(doc, pno):
                for m in COORD.finditer(txt):
                    lo, hi = m.start(), m.end()
                    # the run's own dominant font, so a whole line in a symbol
                    # face (rare, but it happens in figure captions) is not
                    # reported as 100% suspect
                    # Spaces are left out: they draw nothing, and pdfium's
                    # generated ones take the font of the text before them,
                    # which can tip the vote.
                    tally = defaultdict(int)
                    for i in range(lo, hi):
                        if not txt[i].isspace():
                            tally[fonts[i]] += 1
                    if not tally:
                        continue
                    body = max(tally, key=tally.get)
                    odd = [(i, txt[i], fonts[i]) for i in range(lo, hi)
                           if fonts[i] != body and not txt[i].isspace()]
                    out.append({
                        "page": pno + 1, "bbox": bbox, "body": body,
                        "text": m.group(0), "odd": odd,
                        "context": txt[max(0, lo - CONTEXT):hi + CONTEXT],
                    })
        return out
    finally:
        doc.close()


def crop(key: str, page: int, bbox, out: Path, dpi: int = 400, pad: int = 4):
    doc = open_pdf(PDF_DIR / f"{key}.pdf")
    try:
        rect = (bbox[0] - pad, bbox[1] - pad, bbox[2] + pad, bbox[3] + pad)
        Path(out).write_bytes(doc.render_png(page - 1, rect, dpi))
    finally:
        doc.close()


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--key", type=key_arg, action="append")
    ap.add_argument("--keys-file")
    ap.add_argument("--crops", metavar="DIR",
                    help="also render a crop of each reported line there")
    ap.add_argument("--max-per-key", type=int, default=4)
    args = ap.parse_args()

    keys = list(args.key or [])
    if args.keys_file:
        keys += read_keys_file(args.keys_file)
    if not keys:
        print("nothing to do — pass --key or --keys-file")
        return 2

    cdir = Path(args.crops) if args.crops else None
    if cdir:
        cdir.mkdir(parents=True, exist_ok=True)

    pairs = defaultdict(int)
    for key in keys:
        try:
            hits = scan(key)
        except Exception as e:
            print(f"### {key}: ! {e}")
            continue
        if not hits:
            print(f"### {key}: no coordinate-shaped run found in the text layer")
            continue
        print(f"### {key}")
        # Runs with minority-font characters first, so --max-per-key never
        # hides the one that matters behind runs with none.
        hits.sort(key=lambda h: not h["odd"])
        for i, h in enumerate(hits[:args.max_per_key]):
            marks = ", ".join(f"{c!r}@{f}" for _, c, f in h["odd"]) or "-"
            print(f"  p{h['page']:<4} {h['text']!r}")
            print(f"        body={h['body']}  odd: {marks}")
            for _, c, f in h["odd"]:
                pairs[(f, c)] += 1
            if cdir:
                out = cdir / f"{key}-p{h['page']}-{i}.png"
                crop(key, h["page"], h["bbox"], out)
                print(f"        crop: {out}")

    if pairs:
        print("\n(font family, character) pairs seen inside coordinates — "
              "each needs one reading off a crop:")
        for (fam, c), n in sorted(pairs.items(), key=lambda kv: -kv[1]):
            print(f"  {fam:28} {c!r:6} x{n}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
