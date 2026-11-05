#!/usr/bin/env python3
"""Restore glyphs that a PDF's text layer drops as control characters.

Publisher PDFs routinely set °, ′, ″, ±, ×, ≤, ≥, ~, ©, †, the en dash, and the
accents of author names in subsetted symbol fonts (Elsevier's `AdvP4C4E*`,
Adobe's `Universal-*Pi`, `MathematicalPi-One`, `Symbol`) whose glyph names are
not in the Adobe Glyph List. Extraction yields C0 control characters and marker
drops them, leaving text that is clean, well formed, and wrong:

    (79°32′, 19°15′)   ->  (7932, 1915)        a type locality
    18°51′N, 79°58′E   ->  1851N, 7958E
    150.8 ± 4.0 Ma     ->  150.8 4.0 Ma
    ~76.5–75 Ma        ->  76.5–75 Ma          an estimate read as exact
    Agnolín            ->  Agnolına            an author's name
    ratio ≥1.5         ->  ratio 1.5

Nothing else in the QC chain sees this: the prose reads normally and the
numbers are well formed — only the characters between them are gone.

Identification is not guesswork. Each font's `/Encoding /Differences` array
names every code it uses, so a dropped character resolves to a glyph *name*
(`H11034`, `C14`). Names are then looked up in GLYPHS below, keyed by (font
family, name) — `C##` names are a subset's internal index and mean different
things in different families, so the family cannot be dropped from the key.
Every entry in GLYPHS was read off a rendered page crop before being added.
Unknown pairs are reported and left alone.

    corpus repair symbols --key KEY                   # dry run
    corpus repair symbols --key KEY --apply
    corpus repair symbols --all --apply               # whole corpus
"""
from __future__ import annotations

import argparse
import re
import re as _RE
from collections import defaultdict

from corpus.pdf.text import font_differences, open_pdf
from corpus.config import PDF_DIR, MD_DIR
from corpus.keys import key_from_path
from corpus.keys import read_keys_file
from corpus.keys import key_arg
from corpus.md import compose as compose_mark, normalize
from corpus.pages import write_markdown

CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f]")
CONTEXT = 28

# kind: "sym" inserts a character, "accent" composes with the character that
# follows it, "skip" is a known non-text glyph (a publisher logo) that should
# stay dropped.
SYM, ACCENT, SKIP = "sym", "accent", "skip"

# (font family, glyph name) -> (kind, value). Adobe's H-numbers are stable
# across families; the C-numbers are per-family and were each confirmed against
# a rendered page crop.
GLYPHS: dict[tuple[str, str], tuple[str, str]] = {}


def _add(families, name, kind, value):
    for fam in families:
        GLYPHS[(fam, name)] = (kind, value)


_UNIVERSAL = ["Universal-GreekwithMathP", "Universal-GreekwithMathPi",
              "Universal-NewswithCommPi", "MathematicalPi-One", "Symbol",
              "AdvMathSymb", "AdvPSSym", "AdvPSMP1"]
for _n, _v in [("H11001", "+"), ("H11002", "−"), ("H11003", "×"), ("H11005", "="),
               ("H11011", "~"), ("H11021", "<"), ("H11022", ">"), ("H11032", "′"),
               ("H11033", "″"), ("H11034", "°"), ("H11349", "≤"), ("H11350", "≥"),
               ("H11505", "="), ("H17015", "©"), ("H20855", "⟨"), ("H20856", "⟩")]:
    _add(_UNIVERSAL, _n, SYM, _v)

# Elsevier "Adv" families. Each entry was read off a rendered page crop in the
# family named — the C-numbers are a subset's internal glyph index, so the same
# name means different things in different families. AdvP4C4E59 is the accent
# companion font and AdvP4C4E74 the symbol font: C21 is a breve in the first
# (Rusca Montană) and ≥ in the second (ratio ≥1.5). Mapping one from the other
# corrupts Romanian and Polish names into mathematics.
_add(["AdvP4C4E74"], "C14", SYM, "°")
_add(["AdvP4C4E74"], "C0", SYM, "–")
_add(["AdvP4C4E74"], "C2", SYM, "×")
_add(["AdvP4C4E74"], "C24", SYM, "~")
_add(["AdvP4C4E74"], "C20", SYM, "≤")
_add(["AdvP4C4E74"], "C21", SYM, "≥")
_add(["AdvP4C4E74"], "C6", SYM, "±")
_add(["AdvP4C4E74"], "C1", SYM, "·")
_add(["AdvP4C4E74"], "C15", SYM, "•")
_add(["AdvP4C4E74"], "C3", SKIP, "orcid-logo")
_add(["AdvPSSym"], "C211", SYM, "©")
_add(["AdvT377"], "C223", SYM, "©")
_add(["AdvPSSym"], "C176", SYM, "°")
_add(["Symbol"], "c176", SYM, "°")
_add(["AdvPSMP1"], "C160", SYM, "†")
_add(["AdvPSMP1"], "C224", SYM, "‡")
_add(["AdvP697C"], "C211", SYM, "©")
_add(["PiOne"], "p30", SYM, "©")
_add(["WPMathA"], "c45", SYM, "~")
_add(["MathematicalPi-Three"], "H20841", SYM, "|")
_add(["AdvP4C4E74"], "C25", SYM, "≈")
# An Elsevier book's maths font: C0 is the en dash, dropped hundreds of times a
# chapter ("U–Pb", "Pliensbachian–Toarcian"); C14 is the degree sign
# ("7°C–9°C"). Both read off rendered page crops.
_add(["AdvMacMthSyN"], "C0", SYM, "–")
_add(["AdvMacMthSyN"], "C14", SYM, "°")
# Left unmapped on purpose, each checked and still ambiguous on the page:
#   MathematicalPi-Three H20862 — punctuation inside a URL, could be the solidus
#   MathematicalPi-One   H9262  — sits beside an already-rendered en dash
#   AdvSPSASORT          C223   — a cedilla on Ç, not the © it is in AdvT377
# Accents: the glyph precedes or follows its base letter depending on where the
# typesetter set its origin, so these are gated behind --accents (see process()).
_add(["AdvP4C4E59", "AdvP4C4E5E"], "C19", ACCENT, "acute")
_add(["AdvP4C4E59", "AdvP4C4E5E"], "C18", ACCENT, "grave")
_add(["AdvP4C4E59"], "C21", ACCENT, "breve")
_add(["AdvP4C4E59"], "C20", ACCENT, "cedilla")

SPACING = {
    "°": ("", ""), "′": ("", ""), "″": ("", ""), "⟩": ("", ""), "·": (" ", " "),
    "=": (" ", " "), "+": (" ", " "), "×": (" ", " "), "−": (" ", " "),
    "±": (" ", " "), "≤": (" ", ""), "≥": (" ", ""), "<": (" ", ""), ">": (" ", ""),
    "~": (" ", ""), "⟨": (" ", ""), "©": (" ", " "), "–": ("", ""),
    "†": (" ", ""), "‡": (" ", ""), "•": (" ", " "),
}

COMBINING = {"acute": "́", "grave": "̀", "circumflex": "̂",
             "tilde": "̃", "dieresis": "̈", "cedilla": "̧",
             "caron": "̌", "ring": "̊", "breve": "̆"}
# These fonts set í and ì on a dotless i, so the base character in the text is ı.
DOTLESS = {"ı": "i", "ȷ": "j"}


def compose(accent: str, base: str) -> str | None:
    """acute + 'ı' -> 'í'. Returns None when there is no precomposed form."""
    mark = COMBINING.get(accent)
    if not mark or not base:
        return None
    plain = DOTLESS.get(base, base)
    if not plain.isalpha():
        return None
    return compose_mark(plain, mark)


def family(font: str) -> str:
    f = re.sub(r"^[A-Z]{6}\+", "", font or "")
    f = re.sub(r"-OV-[A-Z]{6}$", "", f)
    return re.sub(r"^B\d+\+", "", f)


def occurrences(doc, pdf):
    """Every dropped glyph with its resolved name and surrounding text."""
    maps = font_differences(pdf)
    out = []
    for pno in range(len(doc)):
        text = doc.text(pno)
        spans = []
        raw = doc.rawdict(pno)
        for block in raw["blocks"]:
            for line in block.get("lines", []):
                for span in line["spans"]:
                    for ch in span["chars"]:
                        if CONTROL.fullmatch(ch["c"]):
                            spans.append((span["font"], ord(ch["c"])))
        hits = list(CONTROL.finditer(text))
        if len(spans) != len(hits):
            spans = [(None, ord(h.group(0))) for h in hits]
        for h, (font, code) in zip(hits, spans):
            name = None
            for bf, mp in maps.items():
                if code in mp and (font is None or font in bf or bf.endswith(font)):
                    name = mp[code]
                    break
            out.append({
                "page": pno + 1, "font": font, "family": family(font), "code": code,
                "name": name,
                "left": re.sub(r"\s+", " ", text[max(0, h.start() - CONTEXT * 2):h.start()]),
                "right": re.sub(r"\s+", " ", text[h.end():h.end() + CONTEXT * 2]),
            })
    return out


def new_result(key: str) -> dict:
    return {"key": key, "applied": 0, "skipped": 0, "unmatched": 0, "already": 0,
            "unknown": defaultdict(int), "unknown_ctx": {}}


def process(key: str, apply: bool, verbose: bool, accents: bool = False) -> dict:
    """Repair markdown/{key}.md from pdfs/{key}.pdf (written only with apply)."""
    pdf, md_path = PDF_DIR / f"{key}.pdf", MD_DIR / f"{key}.md"
    if not pdf.exists() or not md_path.exists():
        return new_result(key)
    doc = open_pdf(pdf)
    try:
        md = md_path.read_text(encoding="utf-8")
        new, res = repair(doc, md, key, quiet=not verbose, accents=accents)
    finally:
        doc.close()
    if apply and new != md:
        write_markdown(md_path, new)
    return res


def repair(doc, md: str, key: str, *, quiet: bool = True,
           accents: bool = False) -> tuple[str, dict]:
    """The repaired markdown and counts, from an open PDF and its markdown.
    Per-edit lines print unless quiet (the script's --verbose)."""
    verbose = not quiet
    res = new_result(key)
    occ = occurrences(doc, doc.path)
    if not occ:
        return md, res
    norm_md, idx_md = normalize(md)
    edits = []
    for o in occ:
        entry = GLYPHS.get((o["family"], o["name"])) if o["name"] else None
        if entry is None:
            res["unknown"][(o["family"], o["name"])] += 1
            res["unknown_ctx"].setdefault(
                (o["family"], o["name"]),
                f"{key} p{o['page']}: …{o['left'][-42:]} <?> {o['right'][:42]}…")
            continue
        kind, value = entry
        if kind == SKIP:
            res["skipped"] += 1
            continue
        if kind == ACCENT and not accents:
            # An accent's position in reading order does not reliably say which
            # letter it belongs to: its x-origin sits over the base glyph, so
            # "Krasiejów" extracts as "Krasiejo<acute>w" while "Agnolín"
            # extracts as "Agnol<acute>ın". Picking the neighbour by reading
            # order gets one of the two wrong. It needs bbox geometry
            # (corpus.repair.accents), so this is off by default.
            res["skipped"] += 1
            continue
        left_full, _ = normalize(o["left"])
        right_full, _ = normalize(o["right"])
        if len(left_full) < 6 or len(right_full) < 4:
            res["unmatched"] += 1
            continue
        left, right = left_full[-CONTEXT:].strip(), right_full[:CONTEXT].strip()
        # Try progressively shorter anchors until one matches exactly once.
        # A long anchor is unambiguous but brittle — marker rejoins hyphenated
        # line breaks, rebuilds columns and turns some blocks into tables, so a
        # 28-char window often spans an edit the markdown made. A short one can
        # match in several places, so uniqueness is required at every width.
        # The already-restored test runs at each width too: skipping it there
        # would let a shorter anchor re-apply a glyph an earlier run inserted.
        pos_norm, already = None, False
        for width in (CONTEXT, 20, 14, 10):
            lft, rgt = left_full[-width:].strip(), right_full[:width].strip()
            if len(lft) < 5 or len(rgt) < 3:
                continue
            variants = [(lft, rgt)]
            if "- " in lft:
                variants.append((lft.replace("- ", ""), rgt))
            if "- " in rgt:
                variants.append((lft, rgt.replace("- ", "")))
            for pre, suf in variants:
                if kind == ACCENT:
                    c = compose(value, suf[0]) if suf else None
                    done = _RE.escape(pre) + _RE.escape(c + suf[1:]) if c else None
                else:
                    done = _RE.escape(pre) + r"\s*" + _RE.escape(value) + r"\s*" + _RE.escape(suf)
                if done and _RE.search(done, norm_md):
                    already = True
                    break
                for needle, off in ((pre + suf, len(pre)), (pre + " " + suf, len(pre))):
                    hits = [m.start() for m in _RE.finditer(_RE.escape(needle), norm_md)]
                    if len(hits) == 1:
                        pos_norm = hits[0] + off
                        break
                if pos_norm is not None or already:
                    break
            if pos_norm is not None or already:
                break
        if already:
            res["already"] += 1
            continue
        if pos_norm is None:
            res["unmatched"] += 1
            continue
        edits.append((idx_md[pos_norm], kind, value, o))

    edits.sort(key=lambda t: -t[0])
    new = md
    for pos, kind, value, o in edits:
        if kind == ACCENT:
            if pos >= len(new):
                res["unmatched"] += 1
                continue
            composed = compose(value, new[pos])
            if composed is None:
                res["unmatched"] += 1
                continue
            if verbose:
                print(f"    {key} p{o['page']} {value}+{new[pos]!r} -> {composed!r}")
            new = new[:pos] + composed + new[pos + 1:]
        else:
            lead, trail = SPACING.get(value, (" ", " "))
            start, end = pos, pos
            while start > 0 and new[start - 1] == " ":
                start -= 1
            if trail:
                while end < len(new) and new[end] == " ":
                    end += 1
            if verbose:
                ctx = re.sub(r"\s+", " ", new[max(0, start - 32):start])
                print(f"    {key} p{o['page']} -> {value!r}: …{ctx}<{value}>…")
            new = new[:start] + lead + value + trail + new[end:]
        res["applied"] += 1

    return new, res


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--key", type=key_arg, action="append")
    ap.add_argument("--keys-file")
    ap.add_argument("--all", action="store_true", help="every key with a pdf and markdown")
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--verbose", action="store_true")
    ap.add_argument("--accents", action="store_true",
                    help="also compose dropped accents (see the note in process())")
    args = ap.parse_args()

    keys = list(args.key or [])
    if args.keys_file:
        keys += read_keys_file(args.keys_file)
    if args.all:
        keys = sorted(key_from_path(p) for p in PDF_DIR.glob("*.pdf") if (MD_DIR / f"{key_from_path(p)}.md").exists())
    if not keys:
        print("nothing to do — pass --key, --keys-file or --all")
        return 2

    unknown = defaultdict(int)
    unknown_ctx, changed = {}, []
    tot_applied = tot_skipped = tot_unmatched = tot_already = 0
    for i, key in enumerate(keys):
        try:
            r = process(key, args.apply, args.verbose, args.accents)
        except Exception as e:
            print(f"  ! {key}: {e}")
            continue
        tot_applied += r["applied"]
        tot_skipped += r["skipped"]
        tot_unmatched += r["unmatched"]
        tot_already += r["already"]
        for k, v in r["unknown"].items():
            unknown[k] += v
        unknown_ctx.update(r["unknown_ctx"])
        if r["applied"]:
            changed.append((key, r["applied"], r["unmatched"]))
        if args.all and i % 250 == 0:
            print(f"  ...{i}/{len(keys)}", flush=True)

    verb = "restored" if args.apply else "restorable"
    print(f"\n{len(changed)} key(s) with {verb} glyphs: "
          f"{tot_applied} applied, {tot_already} already restored, "
          f"{tot_unmatched} unmatched, {tot_skipped} skipped-by-design")
    for key, n, un in sorted(changed, key=lambda t: -t[1])[:40]:
        print(f"  {key:22} {n:5} restored" + (f", {un} unmatched" if un else ""))
    if unknown:
        print(f"\nunidentified (font family, glyph name) pairs — left untouched: {len(unknown)}")
        for (fam, name), n in sorted(unknown.items(), key=lambda kv: -kv[1])[:30]:
            print(f"  {str(fam)[:30]:32} {str(name):10} x{n}")
            print(f"      {unknown_ctx[(fam, name)][:130]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
