#!/usr/bin/env python3
# corpus-command: clean ligature-codes | fi/fl ligatures and en dashes a text layer reads as ®, ¯ and ±
"""Repair fi/fl ligatures and en dashes a PDF's text layer hands back as ®, ¯ and ±.

A sibling of `corpus repair symbols` (glyphs dropped as control characters)
and `corpus qc symbol-glyphs` (coordinate marks reusing digit glyph names).
Here the body font itself is at fault: Nature's late-1990s/2000s
typesetting embeds Adv* Type1 subsets whose *built-in* encoding puts the fi
ligature at 174, fl at 175 and the en dash at 177. The font carries an empty
`/Encoding /Differences [ ]`, so extraction falls back to a standard encoding
and reads those codes as ®, ¯ and ±:

    signi®cant change       ->  significant change
    impediment of ¯ow      ->  impediment of flow
    north±south             ->  north-south          (en dash)
    Masiakasaurus knop¯eri  ->  knopfleri

Marker is not involved: the counts in `markdown/{key}.md` match the counts in
the PDF's own text layer exactly. Because `/Differences` is empty there is no
glyph name to look up, so each mapping here was read off a rendered page crop
at 9x before being added.

Two look-alikes must NOT be touched, and the gate below excludes both:

  * a genuine ± between spaced numbers ("150.8 ± 4.0 Ma") -- a real plus-minus;
  * files whose ®/¯/± sit inside blocks of decoded binary noise, which is a
    different defect entirely.

The gate is that ® and ¯ must be *word-adjacent* -- immediately followed by a
letter. A registered-trademark sign never is; a fi ligature always is.

MIN_LIGATURES is doing real work, not padding. A spacing macron standing in for
a long vowel is *also* word-adjacent, and expanding it to "fl" corrupts the
word outright:

    Ky¯ory¯ugaku   Japanese Kyoryugaku (Kyōryūgaku)
    sm¯tan         Old Saxon smitan (smītan)
    m¯eros         Greek meros (mēros)

Each of those files has only one or two such glyphs, so requiring at least
three word-adjacent occurrences separates a systematic font fault from an
incidental accented character. Do not lower the threshold without adding a
dictionary gate (compare `despace_ocr.py`, which resolves the same class of
ambiguity by checking that the result is a real word).

    scripts/fix_ligature_substitutions.py --all --report
    scripts/fix_ligature_substitutions.py --key KEY            # dry run
    scripts/fix_ligature_substitutions.py --key KEY --apply
    scripts/fix_ligature_substitutions.py --key KEY --force --apply

`--force` bypasses the gate for a file already confirmed faulty and repaired,
where the few remaining glyphs are abbreviation-form and the repair itself has
removed the evidence the gate keys on. Verify each residual by hand first.
"""
from __future__ import annotations

import argparse
import re
import sys
from corpus.config import MD_DIR
from corpus.keys import read_keys_file
from corpus.keys import key_arg
from corpus.pages import write_markdown

# Read off rendered page crops at 9x magnification.
LIGATURES = {"®": "fi", "¯": "fl"}
EN_DASH = "–"
PLUS_MINUS = "±"

# A ligature glyph is always followed by a letter; a real sign never is.
LIG_RE = {c: re.compile(re.escape(c) + r"(?=[A-Za-z])") for c in LIGATURES}
LIG_ANY = {c: re.compile(re.escape(c)) for c in LIGATURES}

# Figure-caption abbreviation lists spell the abbreviation with the ligature
# itself, so it is followed by the gloss rather than by more letters:
#     "®, fibula"  ->  "fi, fibula"      "r®, right fibula"  ->  "rfi, ..."
#     "¯, facet for lacrimal"  ->  "fl, facet for lacrimal"
# Only applied to a file already classified faulty, where a genuine ® or ¯
# has been ruled out.
LIG_ABBREV_RE = {c: re.compile(re.escape(c) + r"(?=,\s)") for c in LIGATURES}

# A real plus-minus is spaced on both sides. The substituted en dash is not.
REAL_PLUSMINUS = re.compile(r"(?<=\s)" + PLUS_MINUS + r"(?=\s)")

MIN_LIGATURES = 3        # below this the signature is too weak to act on
MIN_WORD_ADJACENT = 0.8  # fraction of ligature glyphs that must be word-adjacent

SENTINEL = "\x00REALPM\x00"


def classify(text: str):
    """Return (verdict, stats) for one markdown body."""
    total = sum(len(LIG_ANY[c].findall(text)) for c in LIGATURES)
    adjacent = sum(len(LIG_RE[c].findall(text)) for c in LIGATURES)
    stats = {
        "lig_total": total,
        "lig_adjacent": adjacent,
        "plusminus": text.count(PLUS_MINUS),
        "real_plusminus": len(REAL_PLUSMINUS.findall(text)),
    }

    if total == 0 and stats["plusminus"] == 0:
        return "clean", stats
    if adjacent < MIN_LIGATURES:
        return "no-signature", stats
    if adjacent / total < MIN_WORD_ADJACENT:
        # Ligature glyphs mostly free-standing => decoded binary noise.
        return "noise", stats
    return "faulty", stats


def repair(text: str):
    counts = {}
    for ch, rep in LIGATURES.items():
        text, n = LIG_RE[ch].subn(rep, text)
        if n:
            counts["%s->%s" % (ch, rep)] = n
        text, n = LIG_ABBREV_RE[ch].subn(rep, text)
        if n:
            counts["%s->%s (abbrev)" % (ch, rep)] = n

    # Protect a genuine plus-minus, remap the rest to an en dash, restore.
    text, kept = REAL_PLUSMINUS.subn(SENTINEL, text)
    text, n = re.subn(re.escape(PLUS_MINUS), EN_DASH, text)
    if n:
        counts["%s->en dash" % PLUS_MINUS] = n
    text = text.replace(SENTINEL, PLUS_MINUS)
    return text, counts, kept


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--key", type=key_arg)
    g.add_argument("--keys-file")
    g.add_argument("--all", action="store_true")
    ap.add_argument("--apply", action="store_true", help="write changes (default: dry run)")
    ap.add_argument("--report", action="store_true", help="classify only, change nothing")
    ap.add_argument("--force", action="store_true",
                    help="bypass the gate on a file already confirmed faulty")
    args = ap.parse_args()

    if args.key:
        paths = [MD_DIR / ("%s.md" % args.key)]
    elif args.keys_file:
        keys = read_keys_file(args.keys_file)
        paths = [MD_DIR / ("%s.md" % k) for k in keys]
    else:
        paths = sorted(
            p for p in MD_DIR.glob("*.md") if not p.name.endswith(".original.md")
        )

    acted = skipped = 0
    for p in paths:
        if not p.exists():
            print("  !! %s: missing" % p.name)
            continue
        text = p.read_text(encoding="utf-8")
        verdict, st = classify(text)
        if args.force and st["lig_total"]:
            verdict = "faulty"

        if verdict != "faulty":
            if st["lig_total"] or st["plusminus"]:
                print(
                    "  -- %s: %s (lig %d/%d word-adjacent, pm %d) -> skipped"
                    % (p.stem, verdict, st["lig_adjacent"], st["lig_total"], st["plusminus"])
                )
                skipped += 1
            continue

        if args.report:
            print(
                "  ** %s: faulty (lig %d/%d, pm %d)"
                % (p.stem, st["lig_adjacent"], st["lig_total"], st["plusminus"])
            )
            acted += 1
            continue

        new, counts, kept_pm = repair(text)
        if new == text:
            continue
        detail = ", ".join("%s x%d" % (k, v) for k, v in counts.items())
        note = ", kept %d real pm" % kept_pm if kept_pm else ""
        print("  %s %s: %s%s" % ("->" if args.apply else "(dry)", p.stem, detail, note))
        if args.apply and not write_markdown(p, new, old=text):
            continue
        acted += 1

    verb = "repaired" if args.apply else ("flagged" if args.report else "would repair")
    print("\n%s: %d    skipped: %d" % (verb, acted, skipped))
    return 0


if __name__ == "__main__":
    sys.exit(main())
