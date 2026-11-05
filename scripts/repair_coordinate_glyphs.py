#!/usr/bin/env python3
# corpus-command: clean coordinates | apply verified °/′/″ repairs to locality coordinates; --audit finds suspects
"""Repair the degree, minute and second marks in locality coordinates.

Three different failures land in the same place — a coordinate that reads as a
run of digits — and none of them trips any other QC in this repo, because what
survives is well-formed prose around a well-formed number that means something
else:

  1. *Substitution.* A subsetted symbol font reuses digit and punctuation glyph
     names for °, ′ and ″, so extraction returns a printable character and
     `/Differences` corroborates the lie (AdvP7DA6 names its prime `/nine`).
     `corpus repair symbols` cannot see these: it only handles characters
     the text layer drops as control characters.
         25°28′15″N   ->  258 28<sup>0</sup> 15<sup>00</sup> N
         N49°04′57.5″ ->  N49u04957.50
  2. *Dropped primes.* marker keeps the degree sign but silently drops ′ and ″
     when they come from a maths font (MTSY), and renders ° as ◦.
         44°40′2.95″N ->  44◦ 40 2.95 N
  3. *Scan OCR.* On pre-1980 scans the degree sign is misread as 0, ', ", ?, •
     or the word "go".
         19°58′S      ->  19058' S

Only the rendered page can say which, so nothing here is inferred from the
shape of a coordinate. `--audit` lists coordinates that look damaged; `corpus qc
symbol-glyphs --crops DIR` renders the lines to read them off. Each confirmed
reading goes into repairs/coordinate_glyphs.json in the corpus root (or the
file given with --repairs) as a literal before/after pair under its paper:

    {"smith2010a": [["(79(27#E", "(79°27′E"]]

A pair must match its file exactly once; one that matches zero times or more
than once is reported and skipped rather than guessed at.

    scripts/repair_coordinate_glyphs.py --audit          # find suspects
    scripts/repair_coordinate_glyphs.py                  # dry run of the recorded repairs
    scripts/repair_coordinate_glyphs.py --key KEY --apply
"""
from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

from corpus.config import MD_DIR, ROOT
from corpus.keys import key_arg
from corpus.pages import write_markdown


DEG = "°"

# The repairs: {key: [[before, after], ...]}, each pair read off a rendered crop
# of the page, one per printed coordinate. Where the page itself prints ASCII
# ' and " they stay as they are: only the character that is *wrong* is touched.
REPAIRS_FILE = ROOT / "repairs" / "coordinate_glyphs.json"


def load_repairs(path: Path = REPAIRS_FILE) -> dict[str, list[tuple[str, str]]]:
    if not path.exists():
        return {}
    data = json.loads(path.read_text(encoding="utf-8"))
    return {key: [tuple(pair) for pair in pairs] for key, pairs in data.items()}


# --- audit ---------------------------------------------------------------
# Finding the damage again is harder than fixing it: a broken coordinate is
# just digits, and digits are everywhere. The signal that survives every
# failure above is the *pair* — a latitude token and a longitude token side by
# side, one carrying N or S and the other E or W. Figure callouts ("figs. 16.1C,
# 16.4E"), specimen numbers ("CCMGE 628/12457 (Sues") and azimuth classes
# ("N020-N030") supply one hemisphere letter but never a complementary pair.
TOK = (r"\d{1,3}\s?[^\w\s]{0,3}\s?\d{1,2}"
       r"(?:\s?[^\w\s]{0,3}\s?\d{1,2}(?:[.,]\d{1,3})?)?\s?[^\w\s]{0,3}")


def _side(i: int) -> str:
    return f"(?:(?P<h{i}>[NSEW])\\s?{TOK}|{TOK}\\s?(?P<t{i}>[NSEW]))"


PAIR = re.compile(_side(1) + r"[\s,;/·]{1,6}" + _side(2))
LOOKALIKE = "◦º⁰˚∘"
# UTM eastings/northings are six and seven digits with no degree sign by
# definition, and are correct as they stand.
UTM = re.compile(r"(?i)\bUTM\b|\b1[0-9][A-Z]\b|NAD ?[0-9]{2}|WGS ?84")
NOISE = re.compile(r"(?i)fig\.|figs|table|plate|\bpl\.|http|doi|%")


def audit(paths) -> int:
    n = 0
    for p in sorted(paths):
        if p.name.endswith(".original.md"):
            continue
        text = p.read_text(encoding="utf-8", errors="replace")
        for m in PAIR.finditer(text):
            s = m.group(0)
            a = m.group("h1") or m.group("t1")
            b = m.group("h2") or m.group("t2")
            if not (a and b):
                continue
            if not ({a, b} & set("NS") and {a, b} & set("EW")):
                continue
            if len(re.findall(r"\d", s)) < 6:
                continue
            ctx = re.sub(r"\s+", " ", text[max(0, m.start() - 75):m.end() + 25])
            if NOISE.search(ctx) or UTM.search(ctx):
                continue
            why = ("degree look-alike" if any(c in s for c in LOOKALIKE)
                   else "no degree sign" if DEG not in s else None)
            if not why:
                continue
            n += 1
            print(f"  {p.stem:24} {why:18} {s!r}")
            print(f"      …{ctx}…")
    print(f"\n{n} suspect coordinate(s)")
    return n


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--key", type=key_arg, action="append")
    ap.add_argument("--repairs", type=Path, default=REPAIRS_FILE,
                    help="JSON of verified repairs (default: repairs/coordinate_glyphs.json)")
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--audit", action="store_true",
                    help="scan every markdown for coordinates that look damaged")
    args = ap.parse_args()

    if args.audit:
        paths = ([MD_DIR / f"{k}.md" for k in args.key] if args.key
                 else sorted(MD_DIR.glob("*.md")))
        return 0 if audit([p for p in paths if p.exists()]) >= 0 else 1

    repairs = load_repairs(args.repairs)
    if not repairs and not args.key:
        print(f"no recorded repairs ({args.repairs}); --audit lists suspect coordinates")
        return 0
    keys = args.key or sorted(repairs)
    applied = missing = ambiguous = already = 0
    for key in keys:
        pairs = repairs.get(key)
        if not pairs:
            print(f"  ! {key}: no repair recorded")
            continue
        path = MD_DIR / f"{key}.md"
        if not path.exists():
            print(f"  ! {key}: no markdown")
            continue
        text = new = path.read_text(encoding="utf-8")
        for before, after in pairs:
            n = new.count(before)
            if n > 1:
                ambiguous += 1
                print(f"  ! {key}: matches {n} times, skipped — {before!r}")
                continue
            if n == 0:
                if after in new:
                    already += 1
                    print(f"  = {key}: already repaired")
                else:
                    missing += 1
                    print(f"  ! {key}: not found — {before!r}")
                continue
            new = new.replace(before, after)
            applied += n
            print(f"  {key}")
            print(f"      -  {before}")
            print(f"      +  {after}")
        if args.apply and new != text:
            write_markdown(path, new, old=text)

    verb = "repaired" if args.apply else "repairable"
    print(f"\n{applied} coordinate(s) {verb}"
          + (f", {already} already done" if already else "")
          + (f", {missing} not found" if missing else "")
          + (f", {ambiguous} ambiguous" if ambiguous else ""))
    return 1 if (missing or ambiguous) else 0


if __name__ == "__main__":
    raise SystemExit(main())
