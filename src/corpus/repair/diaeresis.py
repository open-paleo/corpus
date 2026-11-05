#!/usr/bin/env python3
"""Repair umlauts that a PDF font extracts as "€" (the diaeresis glyph).

Some Taylor & Francis, Wiley and Elsevier PDFs set the diaeresis as a separate
glyph that their font maps to "€". Marker passes it through, either before the
vowel ("M€uller", "f€ur") or detached from it ("Lu, J. €", "Pal € €aontologie",
"Follmi, € 2012"). A regex over the markdown cannot tell which vowel a detached
mark belongs to, so this script reads the answer from the PDF:

1. In the PDF each "€" glyph is drawn over its vowel, so the vowel under it
   and the word around it (read by position on the line) give a word map
   entry, e.g. Hubner -> Hübner. A "€" over no vowel is a real euro sign.
2. In the markdown, each "€" is removed only if a word next to it (or the word
   it is inside) is in that map; the word is replaced by its umlauted form.
   Anything else is left in place and listed for review.

Dry run by default; --apply writes. Idempotent: a repaired file has no "€"
left next to a mapped word.

    ./.runtime/venv/bin/corpus repair diaeresis --all
    ./.runtime/venv/bin/corpus repair diaeresis --key KEY --apply
"""
import argparse
import re
import unicodedata
from pathlib import Path

from corpus.pdf.text import open_pdf
from corpus.config import ROOT
from corpus.manifest import load as manifest_load
from corpus.keys import key_arg
from corpus.pages import write_markdown

UML = {"a": "ä", "e": "ë", "i": "ï", "ı": "ï", "o": "ö", "u": "ü", "y": "ÿ",
       "A": "Ä", "E": "Ë", "I": "Ï", "O": "Ö", "U": "Ü"}
WORD = re.compile(r"[A-Za-zÀ-ÖØ-öø-ÿı]+")


def _page_chars(doc, pno):
    """Every character on the page as (char, font, bbox, size), from the rawdict."""
    out = []
    for block in doc.rawdict(pno)["blocks"]:
        for line in block["lines"]:
            for span in line["spans"]:
                for ch in span["chars"]:
                    out.append((ch["c"], span["font"], ch["bbox"], span.get("size") or 0))
    return out


def pdf_word_map(pdf: Path) -> tuple[dict, int, int]:
    """word_map() for the PDF at a path."""
    doc = open_pdf(pdf)
    try:
        return word_map(doc)
    finally:
        doc.close()


def word_map(doc) -> tuple[dict, int, int]:
    """Return ({plain_word: umlauted_word}, n_diaeresis, n_euro) from an open PDF.

    Built from glyph POSITIONS, not text order. Each "€" glyph is drawn over
    the vowel it belongs to, so its box overlaps that vowel's box; the word is
    then read off the same line by x position. Text order can't be used: the
    two PDF libraries place these mark glyphs differently (pdfium collects them
    away from their letter, sometimes on another line), and an order-based
    map breaks on whichever library it wasn't written against.

    "€" glyphs in GlyphLessFont are skipped: that is the invisible text layer
    OCR tools add to scans, and its stray "€" characters overlapping a letter
    are OCR noise, not diaeresis marks. A "€" that
    isn't over a vowel is counted as a real euro sign and not mapped.
    """
    wmap, n_dia, n_euro = {}, 0, 0
    for pno in range(len(doc)):
        if "€" not in doc.text(pno):
            continue
        chars = _page_chars(doc, pno)
        words = {}   # (line key, first index) -> [letters in x order, set of marked indices]
        for c, font, (mx0, my0, mx1, my1), _ in chars:
            if c != "€" or "GlyphLess" in font:
                continue
            # The diaeresis is centred over its vowel: the letter whose box
            # contains the mark's horizontal centre, and which the mark sits on
            # or above (capitals are taller, so allow a full letter height).
            mcx, base = (mx0 + mx1) / 2, None
            for k, (lc, lf, (x0, y0, x1, y1), _) in enumerate(chars):
                if not lc.isalpha() or lc == "€" or not (x0 <= mcx <= x1):
                    continue
                h = y1 - y0
                if my1 >= y0 - h and my0 <= y1:
                    # Nearest letter vertically first: a mark from the line
                    # above can also sit over this line's letters horizontally.
                    score = (abs((my0 + my1) / 2 - (y0 + y1) / 2), abs((x0 + x1) / 2 - mcx))
                    if base is None or score < base[2]:
                        base = (k, lc, score, (x0, y0, x1, y1))
            if base is None or base[1] not in "aeiouyAEIOUı":
                n_euro += 1
                continue
            bk, _, _, (bx0, by0, bx1, by1) = base
            # Line band and word gaps scale with the FONT SIZE, not the box: the
            # two libraries' boxes differ (PyMuPDF's span the font's full height
            # and advance; pdfium's hug the ink), the size is the same in both.
            cy, bh = (by0 + by1) / 2, (chars[bk][3] or (by1 - by0))
            # The base's line, by x: letters only. Both libraries give advance-
            # width boxes, so letters in a word touch and words are a space's
            # width apart; the gap decides. Space characters are ignored: pdfium
            # leaves a generated space where a mark was, inside the word.
            line = sorted((b[0], k, lc) for k, (lc, lf, b, _) in enumerate(chars)
                          if lc != "€" and lc.isalpha()
                          and abs((b[1] + b[3]) / 2 - cy) < 0.35 * bh)
            pos = next(n for n, (_, k, _) in enumerate(line) if k == bk)
            def joined(a, b):
                return chars[line[b][1]][2][0] - chars[line[a][1]][2][2] < 0.15 * bh
            lo = hi = pos
            while lo > 0 and joined(lo - 1, lo):
                lo -= 1
            while hi + 1 < len(line) and joined(hi, hi + 1):
                hi += 1
            key = (round(cy, 1), line[lo][1])
            entry = words.setdefault(key, [[(k, lc) for _, k, lc in line[lo:hi + 1]], set()])
            entry[1].add(bk)
        for seq, marked in words.values():
            plain = "".join(lc for _, lc in seq)
            fixed = "".join(UML[lc] if k in marked else lc for k, lc in seq)
            if len(plain) >= 2 and fixed != plain:
                n_dia += len(marked)
                wmap[plain] = fixed
    return wmap, n_dia, n_euro


def repair_text(md: str, wmap: dict) -> tuple[str, int, list]:
    """Remove '€' marks next to mapped words, umlauting those words."""
    fixed_n, left = 0, []
    # longest keys first so 'Palaontologie' wins over 'Pal'
    keys = sorted(wmap, key=len, reverse=True)
    out = md
    for _ in range(5000):
        i = out.find("€")
        while i != -1:
            # word containing the mark ("M€uller"), with the mark removed
            a = i
            while a > 0 and (out[a - 1].isalpha()):
                a -= 1
            b = i + 1
            while b < len(out) and (out[b].isalpha() or out[b] == "€"):
                b += 1
            inside = out[a:b].replace("€", "")
            if inside in wmap and (a < i or b > i + 1):
                out = out[:a] + wmap[inside] + out[b:]
                fixed_n += 1
                break
            # split word: marker cut the word at the mark ("Eotv € os", "Vor€ os")
            la = i
            if la > 0 and out[la - 1] == " ":
                la -= 1
            ls = la
            while ls > 0 and out[ls - 1].isalpha():
                ls -= 1
            rb = i + 1
            if rb < len(out) and out[rb] == " ":
                rb += 1
            re_ = rb
            while re_ < len(out) and out[re_].isalpha():
                re_ += 1
            joined = out[ls:la] + out[rb:re_]
            if ls < la and rb < re_ and joined in wmap:
                out = out[:ls] + wmap[joined] + out[re_:]
                fixed_n += 1
                break
            # detached: nearest mapped word within a short window either side
            win_lo, win_hi = max(0, i - 40), min(len(out), i + 40)
            best = None
            for mm in WORD.finditer(out, win_lo, win_hi):
                w = mm.group(0)
                if w in wmap:
                    d = min(abs(mm.start() - i), abs(mm.end() - i))
                    if best is None or d < best[0]:
                        best = (d, mm.start(), mm.end(), w)
            # a second detached mark for the same word is handled in the next pass
            if best and best[0] <= 12:
                d, s, e, w = best
                # drop the mark and one adjacent space
                cut_a, cut_b = i, i + 1
                if cut_b < len(out) and out[cut_b] == " ":
                    cut_b += 1
                elif cut_a > 0 and out[cut_a - 1] == " ":
                    cut_a -= 1
                if s > i:
                    out = out[:cut_a] + out[cut_b:s] + wmap[w] + out[e:]
                    site = cut_a
                else:
                    out = out[:s] + wmap[w] + out[e:cut_a] + out[cut_b:]
                    site = s + len(wmap[w]) + (cut_a - e)
                # Only where the mark was: "Lu, J. €," leaves "Lü, J. ," -> "Lü, J.,".
                # Run over everything before the site, this would strip every
                # space before punctuation in the file.
                if 0 < site < len(out) and out[site - 1] == " " and out[site] in ",.;:)]" \
                        and site >= 2 and not out[site - 2].isspace():
                    out = out[:site - 1] + out[site:]
                fixed_n += 1
                break
            i = out.find("€", i + 1)
        else:
            break
    for mm in re.finditer("€", out):
        left.append(out[max(0, mm.start() - 30):mm.end() + 15].replace("\n", " "))
    return out, fixed_n, left


def eligible(entry: dict, md: str) -> bool:
    """Marker output carrying a "€": in vision output "€" is a real euro sign."""
    return "€" in md and (entry.get("conversion") or {}).get("tool") == "marker"


def repair(doc, md: str, key: str, *, quiet: bool = False) -> tuple[str, dict]:
    """The repaired markdown and counts, from an open PDF and its markdown."""
    if "€" not in md:
        return md, {"applied": 0, "left": 0}
    wmap, n_dia, n_euro = word_map(doc)
    if not wmap:
        return md, {"applied": 0, "left": md.count("€"), "euro": n_euro}
    new, n, left = repair_text(md, wmap)
    if not quiet and n:
        print(f"  {key}: {n} repaired, {len(left)} left | PDF: {n_dia} diaeresis, "
              f"{n_euro} euro, {len(wmap)} words")
    return new, {"applied": n, "left": len(left)}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--key", type=key_arg, action="append", default=[])
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--show-left", action="store_true")
    a = ap.parse_args()
    man = manifest_load()
    targets = []
    if a.all:
        for f in sorted((ROOT / "markdown").glob("*.md")):
            if f.name.endswith(".original.md"):
                continue
            k = unicodedata.normalize("NFC", f.stem)
            if "€" in f.read_text(errors="ignore", encoding="utf-8") and (man.get(k, {}).get("conversion") or {}).get("tool") == "marker":
                targets.append(k)
    targets += a.key
    tot_fixed = tot_left = 0
    for k in targets:
        md_p = ROOT / "markdown" / f"{k}.md"
        pdf_p = ROOT / "pdfs" / f"{k}.pdf"
        if not md_p.exists() or not pdf_p.exists():
            print(f"  ? {k}: missing markdown or PDF")
            continue
        wmap, n_dia, n_euro = pdf_word_map(pdf_p)
        if not wmap:
            print(f"  - {k}: no diaeresis marks in the PDF ({n_euro} euro sign(s)); skipped")
            continue
        md = md_p.read_text(encoding="utf-8")
        new, n, left = repair_text(md, wmap)
        tot_fixed += n
        tot_left += len(left)
        print(f"  {k}: {n} repaired, {len(left)} left | PDF: {n_dia} diaeresis, {n_euro} euro, {len(wmap)} words")
        if a.show_left:
            for x in left[:12]:
                print(f"      left: …{x}…")
        if a.apply and new != md:
            write_markdown(md_p, new)
    print(f"{'repaired' if a.apply else 'repairable'}: {tot_fixed} mark(s) over {len(targets)} file(s); {tot_left} left for review")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
