#!/usr/bin/env python3
"""Reattach accents that Elsevier's companion font separates from their letter.

PROTOTYPE — only the papers listed in ACCENTS. See "Scope" below before
widening it.

Elsevier sets an accented letter as two glyphs: the base letter in the body
font and the accent in a companion subset (`AdvP4C4E59`). The companion ships
no `/Encoding /Differences`, so the accent extracts either as a stray printable
mark or as a C0 control character that marker drops outright:

    Educação, Ciência   ->  Educaç~ao, Ci^encia
    São Luís            ->  S~ao Luís
    Pró-Reitoria        ->  Pr<0x02>o-Reitoria   (marker drops it: "Pro-Reitoria")
    México              ->  M<0x01>exico         (or "M exico")
    Amparo à Pesquisa   ->  Amparo <0x03>a Pesquisa

`corpus repair symbols` keeps this behind `--accents`, off by default,
because **reading order does not identify the base letter.** The accent
can be emitted before or after the letter it belongs to, and the surrounding
run gives no reliable clue — in `Educaç~ao` the tilde sits after `ç` but
belongs to the `a`, while other papers emit it the other way round.

**Geometry settles it.** The accent is drawn over its own letter, so its bounding
box overlaps that letter's box almost exactly and overlaps the neighbours
barely at all. In one paper the tilde in `Educaç~ao` overlaps the
following `a` by 3.32pt of its 3.3pt width, and the preceding `ç` by -0.10.
Picking the maximum-overlap neighbour is unambiguous at every site inspected,
and it does not care which side of the letter the accent was emitted on.

Scope: the glyph tables below are keyed by (key, font, code) because these
subsets reuse codes — `\\x01` is an acute in one paper's companion font but a
degree sign in the *same paper's* other companion font, and `\\x03` is a grave in one
font and an en dash in the other. Every entry here was read off a rendered 9x
crop of the line it comes from. Widening this to the rest of the corpus means
identifying each new paper's codes the same way — the geometry generalises, the
code-to-accent mapping does not.

    corpus repair accents --key KEY
    corpus repair accents --key KEY --apply
"""
from __future__ import annotations

import argparse
import re
import sys
import unicodedata

from corpus.pdf.text import open_pdf
from corpus.config import PDF_DIR, MD_DIR
from corpus.keys import key_arg
from corpus.md import compose
from corpus.pages import write_markdown

ACUTE, GRAVE, CIRCUM, TILDE, DIAER = "́", "̀", "̂", "̃", "̈"

# (key, font family, extracted character) -> combining mark. Each confirmed
# against a rendered crop; see the module docstring on why the key is this wide.
ACCENTS: dict[tuple[str, str, str], str] = {
    ("lindoso2019a", "AdvP4C4E59", "\x02"): ACUTE,
    ("lindoso2019a", "AdvP4C4E59", "\x03"): GRAVE,
    ("lindoso2019a", "AdvP4C4E59", "~"): TILDE,
    ("lindoso2019a", "AdvP4C4E59", "^"): CIRCUM,
    ("ramírez-velasco2022a", "AdvP4C4E59", "\x01"): ACUTE,
    ("ramírez-velasco2022a", "AdvP4C4E59", "\x03"): GRAVE,
    ("ramírez-velasco2022a", "AdvP4C4E59", "~"): TILDE,
    ("ramírez-velasco2022a", "AdvP4C4E59", "^"): CIRCUM,
}

ACCENT_FONTS = {"AdvP4C4E59"}


def sites(doc, key: str):
    """Accent glyphs with the letter their bounding box actually sits over.

    The base is searched for on the whole page, not among the accent's
    neighbours in text order: pdfium does not keep these accents next to
    their letter (PyMuPDF does). Each accent is then placed just before its
    base in the base's line, so the word around it reads in order.
    """
    out = []
    for pno in range(len(doc)):
        lines = []
        for block in doc.rawdict(pno)["blocks"]:
            for line in block.get("lines", []):
                flat = []
                for span in line.get("spans", []):
                    fam = span["font"].split("+")[-1]
                    for ch in span["chars"]:
                        flat.append((ch["c"], fam, ch["bbox"]))
                lines.append(flat)
        accents = [(li, i, ACCENTS[(key, fam, ch)], bb)
                   for li, flat in enumerate(lines)
                   for i, (ch, fam, bb) in enumerate(flat)
                   if (key, fam, ch) in ACCENTS]
        if not accents:
            continue
        # The letter each accent is drawn over: widest horizontal overlap,
        # at least half the accent's width, with the accent on or just above
        # the letter. Other companion-font glyphs are not candidates.
        moves = {}
        for li, i, mark, bb in accents:
            best, best_ov = None, 0.0
            for lj, flat in enumerate(lines):
                for j, (c2, f2, b2) in enumerate(flat):
                    if f2 in ACCENT_FONTS or not c2.strip() or (lj, j) == (li, i):
                        continue
                    h = b2[3] - b2[1]
                    if not (bb[3] >= b2[1] - 0.6 * h and bb[1] <= b2[3]):
                        continue
                    ov = min(bb[2], b2[2]) - max(bb[0], b2[0])
                    if ov > best_ov:
                        best, best_ov = (lj, j), ov
            if best is None or best_ov < 0.5 * (bb[2] - bb[0]):
                continue
            moves[(li, i)] = (best, mark, best_ov)
        # Rebuild each line with its accents placed before their base.
        before = {}
        for (li, i), (base, mark, ov) in moves.items():
            before.setdefault(base, []).append((li, i))
        for lj, flat in enumerate(lines):
            if not any(b[0] == lj for b in before) and not any(a[0] == lj for a in moves):
                continue
            seq = []
            for j, item in enumerate(flat):
                for a in before.get((lj, j), []):
                    seq.append((a, lines[a[0]][a[1]]))
                if (lj, j) in moves:
                    continue
                seq.append(((lj, j), item))
            # pdfium leaves a space where an accent from elsewhere on the page
            # sat in its text order ("Aut  \x01onoma");
            # the letters either side touch. A word space always leaves a gap,
            # so a space between touching letters is dropped.
            new, pos = [], {}
            for k, (ref, item) in enumerate(seq):
                if item[0].isspace() and _between_touching(seq, k):
                    continue
                pos[ref] = len(new)
                new.append(item)
            for a, (base, mark, ov) in moves.items():
                if base[0] != lj:
                    continue
                made = compose(new[pos[base]][0], mark)
                if made is None:
                    continue
                out.append({"page": pno + 1, "line": new,
                            "accent_at": pos[a], "base_at": pos[base],
                            "char": new[pos[a]][0], "base": new[pos[base]][0],
                            "made": made, "overlap": ov})
    return out


def _between_touching(seq, k) -> bool:
    """True if the nearest letters left and right of seq[k] abut on one line."""
    def letter(step):
        i = k + step
        while 0 <= i < len(seq):
            c, fam, bb = seq[i][1]
            if c.strip() and fam not in ACCENT_FONTS:
                return bb
            i += step
        return None
    lb, rb = letter(-1), letter(1)
    if lb is None or rb is None:
        return False
    h = min(lb[3] - lb[1], rb[3] - rb[1])
    if h <= 0 or min(lb[3], rb[3]) - max(lb[1], rb[1]) < 0.5 * h:
        return False
    return -0.1 * h <= rb[0] - lb[2] < 0.1 * h


def token_around(flat, positions):
    """The whitespace-delimited token spanning the given character positions."""
    lo = min(positions)
    hi = max(positions)
    while lo > 0 and flat[lo - 1][0].strip():
        lo -= 1
    while hi + 1 < len(flat) and flat[hi + 1][0].strip():
        hi += 1
    return lo, hi + 1


def eligible(key: str) -> bool:
    """Only the keys whose accent glyphs have been identified (ACCENTS)."""
    return any(k == key for k, _, _ in ACCENTS)


def process(key: str, apply: bool, quiet: bool) -> tuple[int, int]:
    """Repair markdown/{key}.md from pdfs/{key}.pdf (written only with apply)."""
    pdf, md_path = PDF_DIR / f"{key}.pdf", MD_DIR / f"{key}.md"
    if not (pdf.exists() and md_path.exists()):
        print(f"  ! {key}: missing pdf or markdown")
        return 0, 0
    doc = open_pdf(pdf)
    try:
        md = md_path.read_text(encoding="utf-8")
        new, res = repair(doc, md, key, quiet=quiet)
    finally:
        doc.close()
    if apply and new != md:
        write_markdown(md_path, new)
    return res["applied"], res["missed"]


STRAY = "~^`´¨"          # accent marks marker leaves in the markdown


def _base(c: str) -> str:
    """A character without its combining marks ('é' -> 'e')."""
    return unicodedata.normalize("NFD", c)[0] if c else c


def squash(s: str):
    """Letters and digits of `s`, accents stripped, with an index back to `s`.
    Stray marks, control characters, whitespace and markdown punctuation are
    left out, so the PDF's line and the markdown compare on what both hold."""
    out, idx = [], []
    for i, ch in enumerate(s):
        b = _base(ch)
        if b.isalnum():
            out.append(b.lower())
            idx.append(i)
    return "".join(out), idx


def repair(doc, md: str, key: str, *, quiet: bool = False) -> tuple[str, dict]:
    """The repaired markdown and {"applied", "missed"}, from an open PDF and
    its markdown. No file I/O.

    Each accented word is placed by its context: the letters before and after
    it on its PDF line must match the markdown at exactly one place, and only
    that place is edited. (An earlier version replaced every occurrence of the
    word in the file, so one printed 'México' in an address turned 52
    unaccented 'Mexico' into 'México'.)"""
    found = sites(doc, key)
    if not found:
        return md, {"applied": 0, "missed": 0}
    sq_md, idx_md = squash(md)
    edits: dict[int, tuple[int, str, dict]] = {}
    applied = missed = 0

    by_token: dict[tuple[int, int, int], list[dict]] = {}
    for s in found:
        lo, hi = token_around(s["line"], [s["accent_at"], s["base_at"]])
        by_token.setdefault((s["page"], lo, hi), []).append(s)

    for (_, lo, hi), group in by_token.items():
        flat = group[0]["line"]
        marked = {s["accent_at"] for s in group} | {s["base_at"] for s in group}
        while lo < hi and not flat[lo][0].isalnum() and lo not in marked:
            lo += 1
        while hi > lo and not flat[hi - 1][0].isalnum() and hi - 1 not in marked:
            hi -= 1
        chars = [c for c, _, _ in flat[lo:hi]]
        repl = {s["base_at"] - lo: s["made"] for s in group}
        drop = {s["accent_at"] - lo for s in group}
        fixed = "".join(repl.get(i, c) for i, c in enumerate(chars) if i not in drop)
        word, _ = squash(fixed)
        if len(word) < 3:
            missed += 1
            continue
        left, _ = squash("".join(c for c, _, _ in flat[:lo]))
        right, _ = squash("".join(c for c, _, _ in flat[hi:]))
        pos = None
        for wl, wr in ((16, 16), (10, 10), (6, 6), (12, 0), (0, 12)):
            l, r = left[-wl:] if wl else "", right[:wr]
            if len(l) + len(r) < 6:
                continue
            hits = [m.start() + len(l) for m in re.finditer(re.escape(l + word + r), sq_md)]
            if len(hits) == 1:
                pos = hits[0]
                break
        if pos is None:
            missed += 1
            if not quiet:
                print(f"  ? {key}: no unique place in the markdown for {fixed!r}")
            continue
        start, end = idx_md[pos], idx_md[pos + len(word) - 1] + 1
        if md[start:end] == fixed or start in edits:
            continue
        # Only letters, marks and the spaces or hyphen marker left inside a
        # word may lie between its first and last letter; anything else (a
        # link bracket, "f[acies") means the edit would break markup.
        if any(not (_base(c).isalnum() or c.isspace() or c in STRAY + "-" or ord(c) < 32)
               for c in md[start:end]):
            missed += 1
            if not quiet:
                print(f"  ? {key}: {md[start:end]!r} crosses markup; left alone")
            continue
        edits[start] = (end, fixed, group[0])

    new = md
    for start in sorted(edits, reverse=True):
        end, fixed, s = edits[start]
        if not quiet:
            print(f"  {key} p{s['page']}  {new[start:end]!r} -> {fixed!r}")
        new = new[:start] + fixed + new[end:]
        applied += 1
    return new, {"applied": applied, "missed": missed}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--key", type=key_arg, action="append", required=True)
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--quiet", action="store_true")
    args = ap.parse_args()

    known = {k for k, _, _ in ACCENTS}
    total = missed = 0
    for key in args.key:
        if key not in known:
            print(f"  ! {key}: no verified glyph table — identify its codes "
                  "against rendered crops first (see the module docstring)")
            continue
        a, m = process(key, args.apply, args.quiet)
        total += a
        missed += m

    verb = "repaired" if args.apply else "repairable"
    print(f"\n{total} accent(s) {verb}" + (f", {missed} not found in the markdown" if missed else ""))
    return 0


if __name__ == "__main__":
    sys.exit(main())
