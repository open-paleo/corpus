#!/usr/bin/env python3
"""Restore symbols that a font's text layer extracts as an ordinary character.

Publisher "Pi" (symbol) fonts often claim WinAnsiEncoding and name each glyph
after the key it sits on, not the symbol it draws. The page shows the right
symbol; the text layer, and so the markdown, gets a plausible wrong character:

    a protracted >40 Ma period    ->  a protracted N40 Ma period   (AdvTT454a7a89)
    ~1300 m of nonmarine          ->  \\*1300 m of nonmarine       (AdvPi1)
    strike N75°E                  ->  strike N758E                 (AdvPi1)
    MSWD = 0.2                    ->  MSWD ¼ 0.2                   (AdvP4C4E74)
    168.07 ± 0.07 Ma              ->  168.07 – 0.07 Ma             (AdvPS697C)

`corpus repair symbols` cannot see these: nothing is dropped, and the fonts have
no /Differences array (and their embedded glyph names are the wrong letters too).
The only evidence is the drawn shape, so TABLE below maps (font family,
extracted character) to the printed symbol. Every entry was found by comparing
each symbol font's glyphs with reference symbols (docs/notes/conversion.md,
"Symbols that extract as letters") and then read off a rendered crop in context.
Pairs not in TABLE are left alone; a glyph that only looks like a symbol at
thumbnail size stays out until a crop settles it.

The character is replaced only where the markdown holds it between the same
neighbours as in the PDF, matched uniquely, so a real "N", "8" or "+" elsewhere
in the paper is untouched.

    corpus repair symbol_letters --key KEY            # dry run
    corpus repair symbol_letters --key KEY --apply
    corpus repair symbol_letters --all --apply
"""
from __future__ import annotations

import argparse
import re

from corpus.config import MD_DIR, PDF_DIR
from corpus.keys import key_arg, key_from_path, read_keys_file
from corpus.pages import write_markdown
from corpus.pdf.text import open_pdf

# (font family, character in the text layer) -> printed symbol.
# Read off rendered crops on 2026-11-25 (one crop per pair, in context).
TABLE: dict[tuple[str, str], str] = {
    # Elsevier symbol font (the "¼ for =" family; C-named glyphs are in symbols.py)
    ("AdvP4C4E74", "¼"): "=", ("AdvP4C4E74", "þ"): "+", ("AdvP4C4E74", "0"): "′",
    ("AdvP4C4E74", "y"): "†", ("AdvP4C4E74", "z"): "‡", ("AdvP4C4E74", "f"): "{",
    ("AdvMathSymb", "0"): "′", ("AdvMathSymb", "þ"): "+",
    ("AdvPSMP1", "Æ"): "·",
    ("AdvP44E6F4", "u"): "°",
    ("AdvPi1", "8"): "°", ("AdvPi1", "+"): "±", ("AdvPi1", "*"): "~", ("AdvPi1", "m"): "µ",
    ("AdvPi1", "6"): "×",
    ("AdvPi2", "m"): "µ", ("AdvPi15", "6"): "©",
    ("AdvP7DA6", "5"): "=", ("AdvPS7DA6", "8"): "°",
    ("AdvP7DB7", ","): "~", ("AdvPS7DB7", ","): "~",
    ("AdvP4C9543", "D"): "=", ("AdvP4C9543", "C"): "+", ("AdvP4C9543", "»"): "~",
    ("AdvP4C9543", "0"): "′", ("AdvP4C9543", "£"): "×",
    ("AdvEls-ent7", "B"): "~", ("AdvEls-ent5", "1"): "°", ("AdvEls-ent2", "y"): "§",
    ("AdvPS3FDD77", "w"): "~", ("AdvPS3FDD77", "("): "°",
    ("AdvP3F4C13", "m"): "µ", ("AdvPS3F4C13", "m"): "µ", ("AdvPSMP13", "l"): "µ",
    ("AdvP3EAA99", "m"): "µ", ("AdvP3F8D2B", "m"): "µ", ("AdvPS462D00", "m"): "µ",
    ("AdvP43A417", "m"): "µ", ("AdvPS4BD310", "m"): "µ", ("AdvP697C", "m"): "µ",
    ("AdvTTcd7bcd39", "m"): "µ", ("AdvTTab7e17fd", "m"): "µ", ("AdvTT5f4b71f8", "m"): "µ",
    ("AdvOT24fff15b", "m"): "µ",
    ("AdvP0005", "&"): "•", ("AdvP7CA8", "N"): "•", ("AdvTT1143c7e7", "!"): "•",
    ("AdvTir_symb", "?"): "+", ("AdvTir_symb", "9"): "×",
    ("AdvTA42", "&"): "©", ("AdvTA42", "’"): "©",
    ("AdvMPi-One", "5"): "=", ("AdvMPi-One", "8"): "°",
    ("AdvPS480274", "B"): None,          # an open circle, not ©: left alone
    ("AdvPSSym", "ª"): "©", ("AdvPSSym", "·"): "×", ("AdvPSSym", "Ó"): "©",
    ("AdvPS586D", "q"): "©", ("AdvPS586D", "5"): "±",
    ("AdvPS586B", "8"): "°", ("AdvPS586B", "3"): "×", ("AdvPS586B", ";"): "~",
    ("AdvPS697C", "–"): "±", ("AdvPS697C", "´"): "×",
    ("AdvP7CAC", "E"): "©",
    ("AdvOTf592e37c", "#"): "×",
    ("AdvP7DED", "5"): "=", ("AdvP7DED", "3"): "×", ("AdvP7DED", "2"): "−",
    ("AdvPS7DED", "5"): "=",
    ("AdvP3D3256", "3"): "×",
    ("AdvP414BFB", "5"): "=", ("AdvP414BFB", "3"): "×",
    ("AdvP0004", "0"): "=", ("AdvP0003", "È"): "~",
    ("AdvP80675", "5"): "=",
    ("AdvPS44A44B", "!"): "×", ("AdvPS44A44B", "C"): "+", ("AdvPS44A44B", "G"): "±",
    ("AdvPS44A44B", "D"): "+",
    ("AdvTT3f84ef53", "´"): "×",
    ("AdvOT410c0b94", "5"): "=", ("AdvOT410c0b94", "3"): "×",
    ("AdvOT0efb2265", "p"): "=",
    ("AdvMacMthSyN", "¼"): "=",
    ("AdvPS44447F", "0"): "′",
    ("AdvPSX0201", "8"): "°", ("AdvPSX0201", "3"): "×", ("AdvPSX0206", "s"): "©",
    ("AdvPS7CFD", "£"): "×",
    ("AdvTT454a7a89", "N"): ">",
    # Adobe / other Pi and maths fonts (Universal-GreekwithMathP is truncated in the PDFs)
    ("Universal-GreekwithMathP", "5"): "=", ("Universal-GreekwithMathP", ";"): "~",
    ("Universal-GreekwithMathP", "8"): "°", ("Universal-GreekwithMathP", "3"): "×",
    ("Universal-NewswithCommPi", "q"): "©",
    ("MathematicalPi-One", "5"): "=", ("MathematicalPi-One", "3"): "×",
    ("MathematicalPi-Three", ","): "~",
    ("MathematicalPi-Four", "5"): "=", ("MathematicalPi-Four", "2"): "−",
    ("MathTechnicalP03", "p"): "=", ("MathTechnicalP04", "#"): "×",
    ("MathPackThirteen", "l"): "µ", ("MathPackTwelve", "l"): "µ",
    ("TeX_CM_Maths_Symbols", "þ"): "+", ("TeX_CM_Maths_Symbols", "0"): "′",
    ("Texmatha", "¼"): "=",
    ("ESSTIX-Four", "w"): "~", ("ESSTIX-Four", "("): "°",
    ("SpecialSortsFont", "\\"): "°", ("SpecialSortsFont", "—"): "×",
    ("SymbolBS", "¥"): "×",
    ("Symbol", "m"): "µ",
    ("TIMES_NR_MonoText_Smartfont", "ß"): "©",
}
TABLE = {k: v for k, v in TABLE.items() if v}

CONTEXT = 24
SQUASHED = set("*_`|\\")   # left out of the comparison (see squash())


def family(font: str) -> str:
    """Subset tag (ABCDEF+), a mixed-case six-letter prefix glued on by some
    producers (GdgypkAdvP44E6F4) and the +20 variant suffix are not part of
    the family."""
    f = re.sub(r"^[A-Z]{6}\+", "", font or "")
    f = re.sub(r"^[A-Z][a-z]{5}(?=(Adv|Math|Universal))", "", f)
    return re.sub(r"\+\d+$", "", f)


WORD = re.compile(r"[A-Za-z]{3}")


def occurrences(doc):
    """Every TABLE character in the PDF with its line context.

    A font that sets a word (three ASCII letters in a row) anywhere in the PDF
    is a text font in this paper, whatever its name, and is left alone: every
    TABLE entry was confirmed in fonts that set no words."""
    pages = [doc.rawdict(pno) for pno in range(len(doc))]
    texty = set()
    for raw in pages:
        for block in raw["blocks"]:
            for line in block.get("lines", []):
                for span in line["spans"]:
                    if WORD.search("".join(ch["c"] for ch in span["chars"])):
                        texty.add(span["font"])
    out = []
    for pno, raw in enumerate(pages):
        chars = []
        for block in raw["blocks"]:
            for line in block.get("lines", []):
                for span in line["spans"]:
                    fam = "" if span["font"] in texty else family(span["font"])
                    for ch in span["chars"]:
                        chars.append((ch["c"], fam))
                chars.append((" ", ""))
        text = "".join(c for c, _ in chars)
        for i, (c, fam) in enumerate(chars):
            to = TABLE.get((fam, c))
            if to:
                out.append({"page": pno + 1, "char": c, "to": to, "family": fam,
                            "left": text[max(0, i - CONTEXT * 2):i], "right": text[i + 1:i + 1 + CONTEXT * 2]})
    return out


def new_result(key: str) -> dict:
    return {"key": key, "applied": 0, "already": 0, "unmatched": 0}


def squash(s: str):
    """`s` without whitespace, table pipes, emphasis, escapes and control
    characters, with an index back to `s`. Marker rejoins lines, puts numbers
    in table cells and escapes symbols, so the PDF's context and the
    markdown's are compared on what is left."""
    out, idx = [], []
    for i, ch in enumerate(s):
        if ch.isspace() or ch in SQUASHED or ord(ch) < 32:
            continue
        out.append(ch)
        idx.append(i)
    return "".join(out), idx


def repair(doc, md: str, key: str, *, quiet: bool = True) -> tuple[str, dict]:
    res = new_result(key)
    occ = occurrences(doc)
    if not occ:
        return md, res
    sq_md, idx_md = squash(md)
    # A context character that is itself a TABLE character may already have
    # been restored (an earlier run), so it matches either form.
    alt: dict[str, set] = {}
    for o in occ:
        alt.setdefault(o["char"], set()).add(o["to"])

    def pat(ctx: str) -> str:
        return "".join("[" + re.escape(c) + "".join(re.escape(t) for t in sorted(alt[c])) + "]"
                       if c in alt else re.escape(c) for c in ctx)

    edits = {}
    for o in occ:
        left, _ = squash(o["left"])
        right, _ = squash(o["right"])
        gap = o["char"] in SQUASHED or o["char"].isspace()
        found, already = None, False
        # Symmetric windows first; the lopsided ones catch a symbol at the
        # start or end of a line, whose context on that side is another line.
        for wl, wr in ((20, 20), (12, 12), (8, 8), (5, 5), (3, 10), (10, 3)):
            pre, suf = left[-wl:], right[:wr]
            if len(pre) + len(suf) < 7:
                continue
            for p, s in ((pre, suf), (pre.replace("-", ""), suf), (pre, suf.replace("-", ""))):
                if re.search(pat(p) + re.escape(o["to"]) + pat(s), sq_md):
                    already = True
                    break
                mid = "" if gap else re.escape(o["char"])
                hits = list(re.finditer(pat(p) + "(" + mid + ")" + pat(s), sq_md))
                if len(hits) == 1:
                    found = hits[0].start(1)
                    break
            if found is not None or already:
                break
        if already:
            res["already"] += 1
            continue
        if found is None:
            res["unmatched"] += 1
            continue
        if gap:
            # The character was squashed out: find it between its neighbours.
            lo = idx_md[found - 1] + 1 if found else 0
            hi = idx_md[found] if found < len(idx_md) else len(md)
            j = md.rfind(o["char"], lo, hi)
            if j < 0:
                res["unmatched"] += 1
                continue
            start = j - 1 if j > lo and md[j - 1] == "\\" else j
            end = j + 1
        else:
            start = idx_md[found]
            end = start + 1
            if start and md[start - 1] == "\\":
                start -= 1
        if start in edits:
            continue
        edits[start] = [end, o["to"], o]
    # Two primes in a row are a double prime (45°57′29.8″).
    for s in sorted(edits):
        e = edits.get(s)
        if e and e[1] == "′" and e[0] in edits and edits[e[0]][1] == "′":
            e[0], e[1] = edits.pop(e[0])[0], "″"
    new = md
    for start in sorted(edits, reverse=True):
        end, to, o = edits[start]
        if not quiet:
            ctx = re.sub(r"\s+", " ", new[max(0, start - 30):start])
            print(f"    {key} p{o['page']} {o['family']} {new[start:end]!r} -> {to!r}: …{ctx}<{to}>{new[end:end + 15]!r}")
        new = new[:start] + to + new[end:]
        res["applied"] += 1
    return new, res


def process(key: str, apply: bool, verbose: bool) -> dict:
    pdf, md_path = PDF_DIR / f"{key}.pdf", MD_DIR / f"{key}.md"
    if not pdf.exists() or not md_path.exists():
        return new_result(key)
    doc = open_pdf(pdf)
    try:
        md = md_path.read_text(encoding="utf-8")
        new, res = repair(doc, md, key, quiet=not verbose)
    finally:
        doc.close()
    if apply and new != md:
        write_markdown(md_path, new)
    return res


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--key", type=key_arg, action="append")
    ap.add_argument("--keys-file")
    ap.add_argument("--all", action="store_true", help="every key with a pdf and markdown")
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--verbose", action="store_true")
    args = ap.parse_args()
    keys = list(args.key or [])
    if args.keys_file:
        keys += read_keys_file(args.keys_file)
    if args.all:
        keys = sorted(key_from_path(p) for p in PDF_DIR.glob("*.pdf") if (MD_DIR / f"{key_from_path(p)}.md").exists())
    tot = {"applied": 0, "already": 0, "unmatched": 0}; changed = 0
    for k in keys:
        r = process(k, args.apply, args.verbose)
        for f in tot: tot[f] += r[f]
        changed += bool(r["applied"])
        if r["applied"] or r["unmatched"]:
            print(f"  {k:28} applied {r['applied']:4}  already {r['already']:4}  unmatched {r['unmatched']:4}")
    print(f"{len(keys)} paper(s); {changed} {'changed' if args.apply else 'would change'}; "
          f"applied {tot['applied']}, already {tot['already']}, unmatched {tot['unmatched']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
