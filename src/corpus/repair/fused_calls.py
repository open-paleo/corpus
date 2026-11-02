#!/usr/bin/env python3
"""Separate reference-call digits that marker fused onto the preceding word.

Where `repair_marker_ref_links.py` handles the calls marker turned into links,
this handles the ones it flattened instead: the superscript loses its markup and
lands hard against the word, so the word stops being a word.

    Rhaetian6 ,          should be  Rhaetian   + call 6
    at Langenberg quarry7 ,         quarry     + call 7
    Suteethorn4                     Suteethorn + call 4

The tell is `quarry7` reads as a token in its own right: `search.py 'quarry'`
misses the sentence, and nothing in the QC chain notices because the prose
around it is intact.

The PDF is the authority and the signal is unambiguous. A reference call is set
smaller and raised: in one paper the "6" in "Rhaetian6" is its own span at
size 5.6 on a baseline 4.7pt above the 9.3pt body text around it. So rather than
guessing which trailing digits are a citation and which belong to the word — and
"Iguanodon2" and "IVPP V12" are not distinguishable by shape — each candidate is
read off the PDF's own typography, and only a word the PDF actually set with a
raised digit after it is touched.

Output matches what marker already emits for superscripts elsewhere in these
same files, `word<sup>N</sup>`, so the reference number is preserved rather than
discarded.

    scripts/repair_fused_superscript_refs.py --audit
    scripts/repair_fused_superscript_refs.py --key KEY
    scripts/repair_fused_superscript_refs.py --key KEY --apply
    scripts/repair_fused_superscript_refs.py --all --apply
"""
from __future__ import annotations

import argparse
import re
import sys

from corpus.pdf.text import MARKS, open_pdf
from corpus.config import PDF_DIR, MD_DIR
from corpus.manifest import load as manifest_load
from corpus.keys import key_from_path
from corpus.keys import key_arg
from corpus.io import atomic_write_text

# A call is set clearly smaller than the body text and clearly higher. Both
# tests are required: a small-caps run is small but not raised, and a
# superscripted unit ("cm3") is raised but belongs to its word.
SIZE_RATIO = 0.85
RAISE_MIN = 1.0

CALL = re.compile(r"^[0-9]{1,3}(?:[,–-][0-9]{1,3})*$")

# A unit exponent is typeset exactly like a reference call — smaller and raised —
# but the digit belongs to the unit. "km2" is km², not km + reference 2, and
# separating it turns an area into a citation.
UNITS = {"m", "km", "cm", "mm", "µm", "um", "nm", "ft", "in", "yd", "mi", "s"}


# Atomic numbers, for telling an isotope's mass number from a call.
ELEMENTS = {s: z for z, s in enumerate(
    "H He Li Be B C N O F Ne Na Mg Al Si P S Cl Ar K Ca Sc Ti V Cr Mn Fe Co Ni Cu "
    "Zn Ga Ge As Se Br Kr Rb Sr Y Zr Nb Mo Tc Ru Rh Pd Ag Cd In Sn Sb Te I Xe Cs "
    "Ba La Ce Pr Nd Pm Sm Eu Gd Tb Dy Ho Er Tm Yb Lu Hf Ta W Re Os Ir Pt Au Hg Tl "
    "Pb Bi Po At Rn Fr Ra Ac Th Pa U".split(), 1)}


def is_isotope(word: str, call: str) -> bool:
    """An element symbol with a number in its mass range: the raised mass
    number of "40Ar/39Ar" (or "Ar40/ Ar39"), "87Sr/86Sr",
    "143Nd/144Nd". Author calls such as "Li1,2" or "Mo5" are far too small."""
    z = ELEMENTS.get(word)
    if z is None or z < 3 or not call.isdigit():
        return False
    return 1.8 * z <= int(call) <= 2.7 * z


def is_unit_exponent(word: str, call: str) -> bool:
    return call in {"2", "3"} and word.lower() in UNITS


def candidates(doc):
    """(word, call) pairs the PDF sets as word-then-raised-digits."""
    out = []
    for pno in range(len(doc)):
        for block in doc.rawdict(pno)["blocks"]:
            for line in block.get("lines", []):
                spans = []
                for sp in line.get("spans", []):
                    chars = [c for c in sp["chars"] if c["c"].strip()]
                    if chars:
                        # baselines of the span's first and last glyphs
                        spans.append({"text": "".join(c["c"] for c in sp["chars"]),
                                      "size": sp["size"],
                                      "bbox": (min(c["bbox"][0] for c in chars), 0,
                                               max(c["bbox"][2] for c in chars), 0),
                                      "first": chars[0]["origin"], "last": chars[-1]["origin"]})
                if len(spans) < 2:
                    continue
                # Horizontal text only. In rotated text (a sideways table
                # header, (0, -1)) "raised" and "after" are
                # measured along the wrong axes and subscripts pass.
                if abs(line.get("dir", (1.0, 0.0))[0]) < 0.99:
                    continue
                body = max(s["size"] for s in spans)
                # A call split over several spans ("1" then ",2,6", as PyMuPDF
                # can give it) is one call: merge each small span into the
                # one before it when that is small too, on the same baseline,
                # and the two touch.
                merged = []
                for sp in spans:
                    q = merged[-1] if merged else None
                    if (q and q["size"] <= body * SIZE_RATIO and sp["size"] <= body * SIZE_RATIO
                            and abs(q["last"][1] - sp["first"][1]) < 0.5
                            and abs(sp["bbox"][0] - q["bbox"][2]) < 0.5 * body
                            and re.fullmatch(r"[\s0-9,–\-*†‡]+", q["text"] + sp["text"])):
                        merged[-1] = dict(q, text=q["text"] + sp["text"], last=sp["last"],
                                          bbox=(q["bbox"][0], 0, sp["bbox"][2], 0))
                    else:
                        merged.append(sp)
                spans = merged
                for i, s in enumerate(spans[1:], 1):
                    # Trailing punctuation and author marks set in the call's
                    # own span are not part of it ("shales1 ;", "Mocho1–4,*").
                    txt = re.sub(r"[\s;:.,)*†‡]+$", "", s["text"].strip())
                    # "1, 2" in one span (pdfium; PyMuPDF splits it): the
                    # spaces are typesetting, the call is "1,2".
                    txt = re.sub(r"\s*([,–-])\s*", r"\1", txt)
                    # A call-shaped start followed by no letters counts:
                    # pdfium keeps "5,6,8±10" in one span, the ± a dash in the
                    # wrong font.
                    lead = re.match(r"[0-9]{1,3}(?:[,–-][0-9]{1,3})*", txt)
                    # ...unless what follows is a letter, or a charge sign:
                    # "2+" is an ion ("Fe2+"), not call 2.
                    if not lead or re.search(r"[A-Za-zÀ-ÿ+−]", txt[lead.end():]):
                        continue
                    # No reference number starts with 0: "mass0.75" is an
                    # exponent.
                    if txt.startswith("0"):
                        continue
                    txt = lead.group(0)
                    if s["size"] > body * SIZE_RATIO:
                        continue
                    # Raised against the word it is fused to: a call's baseline
                    # sits above that word's, a subscript's below ("SiO2",
                    # "CO2"). Measured against the line as a whole (its lowest
                    # box bottom, or its lowest baseline) both pass whenever
                    # the line also holds a glyph set lower, a symbol-font "="
                    # or a neighbouring table row.
                    # The word's LAST glyph: a span can open on a raised
                    # character of its own ("Z. FOREMAN2").
                    if spans[i - 1]["last"][1] - s["first"][1] < RAISE_MIN:
                        continue
                    # ...and it follows the word: past the word's middle, not
                    # its box end, since an author name ending in a kerned hair
                    # space can overlap its call by 1.6 pt ("Hattori1,2").
                    pb = spans[i - 1]["bbox"]
                    if s["first"][0] <= (pb[0] + pb[2]) / 2:
                        continue
                    # An ion's charge, not a call: "Ca2+" sets the digit and
                    # the sign raised. The sign is small too; a body-size dash
                    # after a call is just a dash ("ovideposition16,17 – instead").
                    nxt = spans[i + 1] if i + 1 < len(spans) else None
                    if (nxt and nxt["text"].lstrip()[:1] in ("+", "-", "−", "–")
                            and nxt["size"] <= body * SIZE_RATIO):
                        continue
                    # All the line's text before the call, not just the previous
                    # span: a stray accent can be a span of its own inside the
                    # word ("dinos`aurs1,2").
                    # Spans are joined with a space where they are apart: some
                    # lines carry no space characters at all (PyMuPDF can give
                    # "groundup7,8").
                    tail = ""
                    for j, sp in enumerate(spans[:i]):
                        if j and sp["bbox"][0] - spans[j - 1]["bbox"][2] > 0.15 * sp["size"] \
                                and not tail.endswith(" ") and not sp["text"].startswith(" "):
                            tail += " "
                        tail += sp["text"]
                    tail = tail.rstrip()[-40:]
                    forms = word_forms(tail)
                    if forms and not is_unit_exponent(forms[0], txt) and not is_isotope(forms[0], txt):
                        out.append((tail, txt))
    return out


def word_forms(tail: str) -> list[str]:
    """Spellings of the word before a call to look for in the markdown, best first.

    pdfium's text differs from the markdown's around the word in three ways,
    each handled here:
      - it splits a kerned word with spaces ("BELLARD IN I1,2,5,6"): the last
        two and three letter runs, joined, are tried;
      - it puts a spacing accent inside the word ("Ro´gers8,9") where marker
        has none: every form is also tried with marks removed;
      - a word space is missing ("andFernandoEscaso1", "NingLi1,2") or a
        mark sits on the other side of its letter ("Arga˜naraz5" against
        "Argan˜araz5"): ever shorter endings are tried, down to three letters,
        or two where a capitalised word starts.
    Each is matched as a whole token, so an ending never matches inside a
    longer word. Forms under two letters are never tried ("A2" is as often a
    specimen label as a call).
    """
    forms = []
    for t in (tail, "".join(c for c in tail if c not in MARKS)):
        for k in (1, 2, 3):
            m = re.search(r"[A-Za-zÀ-ÿ]+(?: [A-Za-zÀ-ÿ]+){%d}$" % (k - 1), t)
            if m:
                forms.append(m.group(0).replace(" ", ""))
    endings = [w[k:] for w in forms for k in range(1, len(w) - 2)]
    # Two-letter endings only where a capitalised word starts: names run
    # together in the PDF itself ("NingLi1,2") end in "Li", "Ma".
    endings += [w[-2:] for w in forms
                if len(w) > 2 and w[-2].isupper() and w[-3].islower()]
    return [w for w in dict.fromkeys(forms + endings) if len(w) >= 2]


def call_forms(call: str) -> list[str]:
    """The call, then with the spaces the markdown keeps ("wollastoni10, 11"),
    then its first group ("mongoliensis2-4, 7, 15"), then its first number:
    marker can link the rest away ("Europe3[,18](#page-8-…)")."""
    first_group = call.split(",")[0]
    first = re.match(r"[0-9]+", call).group(0)
    return list(dict.fromkeys([call, call.replace(",", ", "), first_group, first]))


def process(key: str, apply: bool, quiet: bool) -> tuple[int, int]:
    """Repair markdown/{key}.md from pdfs/{key}.pdf (written only with apply)."""
    pdf, md_path = PDF_DIR / f"{key}.pdf", MD_DIR / f"{key}.md"
    if not (pdf.exists() and md_path.exists()):
        return 0, 0
    try:
        doc = open_pdf(pdf)
    except Exception:
        return 0, 0
    try:
        md = md_path.read_text(encoding="utf-8")
        new, res = repair(doc, md, key, quiet=quiet)
    finally:
        doc.close()
    if apply and new != md:
        atomic_write_text(md_path, new)
    return res["applied"], res["missed"]


def repair(doc, md: str, key: str, *, quiet: bool = False) -> tuple[str, dict]:
    """The repaired markdown and {"applied", "missed"}, from an open PDF and
    its markdown. No file I/O."""
    pairs = candidates(doc)
    if not pairs:
        return md, {"applied": 0, "missed": 0}
    new, applied, missed = md, 0, 0
    for tail, full_call in dict.fromkeys(pairs):
        hits = []
        for call in call_forms(full_call):
            for word in word_forms(tail):
                fused = f"{word}{call}"
                fixed = f"{word}<sup>{call}</sup>"
                # Already repaired, as a whole token: a bare substring test
                # would take "Stegosaurs<sup>1</sup>" for "aurs<sup>1</sup>" and
                # skip "dinosaurs1".
                if re.search(r"(?<![0-9A-Za-zÀ-ÿ])" + re.escape(fixed), new):
                    hits = None
                    break
                # Bounded both sides: the fused form must be a whole token, or
                # "rare4" would also match inside "rare45"; the call must end
                # there, or the first-number fallback "6" would cut
                # "deinonychosaurs6,11" to "<sup>6</sup>,11"; and no
                # charge sign may follow ("Fe2+").
                # Nor is a call next to a slash, before a decimal point, or
                # hyphened to a word: those are isotope ratios ("Ar40/Ar39",
                # "Ar40-Ar39", "Sr87/Sr86"), specimen numbers
                # ("BENC32/2–0001"), land-survey fractions ("SE1/4") and
                # exponents ("BM0.75").
                pat = re.compile(r"(?<![0-9A-Za-zÀ-ÿ/])" + re.escape(fused)
                                 + r"(?![0-9A-Za-zÀ-ÿ+−/])(?![,–-][0-9])(?![-–][A-Za-z])(?!\.[0-9])")
                hits = list(pat.finditer(new))
                if hits:
                    break
            if hits is None or hits:
                break
        if hits is None:
            continue
        if not hits:
            missed += 1
            continue
        new = pat.sub(fixed, new)
        applied += len(hits)
        if not quiet:
            print(f"  {key}  x{len(hits)}   {fused}  ->  {word} + call {call}")

    return new, {"applied": applied, "missed": missed}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--key", type=key_arg, action="append")
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--audit", action="store_true")
    ap.add_argument("--quiet", action="store_true")
    args = ap.parse_args()

    if args.all or args.audit:
        man = manifest_load()
        keys = sorted(key_from_path(p) for p in PDF_DIR.glob("*.pdf")
                      if (MD_DIR / f"{key_from_path(p)}.md").exists()
                      and man.get(key_from_path(p), {}).get("conversion", {}).get("tool") == "marker")
    elif args.key:
        keys = args.key
    else:
        ap.error("need --key, --all or --audit")

    total = missed = files = 0
    for key in keys:
        n, m = process(key, args.apply and not args.audit, args.quiet or args.audit)
        total += n
        missed += m
        if n:
            files += 1
            if args.audit:
                print(f"  {n:4d}  {key}")

    verb = "repaired" if (args.apply and not args.audit) else "repairable"
    print(f"\n{total} fused call(s) {verb} over {files} file(s)"
          + (f", {missed} not found in the markdown" if missed else ""))
    return 0


if __name__ == "__main__":
    sys.exit(main())
