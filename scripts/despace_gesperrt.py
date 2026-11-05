#!/usr/bin/env python3
# corpus-command: clean gesperrt | rejoin letterspaced emphasis
"""Rejoin letterspaced (German *gesperrt*) emphasis in OCR'd markdown.

German-language palaeo journals — Neues Jahrbuch, Palaeontographica,
Zeitschrift der Deutschen Geologischen Gesellschaft — set emphasis by
LETTERSPACING rather than italics or bold:

    H o l o t y p e :  BMNH R1993, a femur.
    T y p e  l o c a l i t y  a n d  h o r i z o n :  Lower Oxford Clay …

OCR transcribes that faithfully, character by character, which is *correct* but
leaves the text unsearchable: an FTS query for "Holotype" or "Diagnosis" never
matches "H o l o t y p e". In these papers the letterspaced strings are almost
always the systematic-section labels — precisely the terms the corpus is
queried on.

This is NOT the same problem as scripts/despace_ocr.py. That one rejoins
occasional mid-word splits ("numerou s", "th e") by merging adjacent *word*
tokens into a dictionary word. Here every token is a single letter, so nothing
merges and despace_ocr.py is a no-op. This script instead collapses a whole
letterspaced run and re-segments it against the dictionary.

Method
------
1. Find runs of >= 4 single letters separated by one or two spaces.
2. Collapse the spaces ("T y p e l o c a l i t y" -> "Typelocality").
3. Greedy longest-first segmentation against the word list (CORPUS_WORDLIST,
   else /usr/share/dict/words) plus a small allowlist of palaeo/German label
   terms such lists lack.
4. Replace ONLY if the whole run segments into known words. A run that does not
   fully segment is left untouched — no partial rewrites, no guessing.

Because step 4 is all-or-nothing and only ever deletes spaces, it cannot
invent text. Word boundaries are recovered from the dictionary, so
"Typelocalityandhorizon" comes back as "Type locality and horizon".

Usage
-----
    ./.runtime/venv/bin/python scripts/despace_gesperrt.py --key KEY
    ./.runtime/venv/bin/python scripts/despace_gesperrt.py --key KEY --apply

Run on demand after vision OCR of a German-set scan, then re-index. Not part of
intake. Pairs with scripts/despace_ocr.py (word-split rejoin, different case).
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path
from corpus.config import MD_DIR
from corpus.keys import key_arg
from corpus.pages import write_markdown
from corpus.words import wordlist_path

DICT_PATH = wordlist_path()

# Terms web2 lacks, plus the German label vocabulary that recurs letterspaced
# in Neues Jahrbuch / Palaeontographica front and systematic matter.
EXTRA = {
    "holotype", "paratype", "paratypes", "lectotype", "neotype", "syntype",
    "syntypes", "keywords", "horizon", "specimens", "topotype",
    "anschrift", "des", "verfassers", "verfasser", "zusammenfassung",
    "abstract", "schlusselworter",
}

RUN = re.compile(r"(?<![A-Za-z])(?:[A-Za-z] {1,2}){3,}[A-Za-z](?![A-Za-z])")


def load_vocab() -> set[str]:
    try:
        words = {w.strip().lower()
                 for w in open(DICT_PATH, encoding="latin-1") if w.strip()}
    except OSError:
        sys.exit(f"no system word list at {DICT_PATH}")
    return words | EXTRA


def segment(s: str, vocab: set[str], min_piece: int = 2) -> list[str] | None:
    """Greedy longest-first split of a spaceless string; None if any residue."""
    out, i, low = [], 0, s.lower()
    while i < len(s):
        for j in range(len(s), i, -1):
            if j - i >= min_piece and low[i:j] in vocab:
                out.append(s[i:j])
                i = j
                break
        else:
            return None
    return out


def process(path: Path, vocab: set[str], apply: bool):
    text = path.read_text(errors="replace", encoding="utf-8")
    done, skipped = [], []

    def repl(m: re.Match) -> str:
        raw = m.group(0)
        seg = segment(raw.replace(" ", ""), vocab)
        if not seg:
            skipped.append(raw)
            return raw
        new = " ".join(seg)
        done.append((raw, new))
        return new

    out = RUN.sub(repl, text)
    if apply and done:
        write_markdown(path, out, old=text)
    return done, skipped, len(text) - len(out)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--key", type=key_arg, action="append", required=True,
                    help="citation key (repeatable)")
    ap.add_argument("--apply", action="store_true",
                    help="write changes (default is a dry run)")
    args = ap.parse_args()

    vocab = load_vocab()
    mode = "APPLY" if args.apply else "DRY RUN (no files written)"
    print(f"=== despace_gesperrt — {mode} ===")
    tot_done = tot_skip = 0
    for key in args.key:
        p = MD_DIR / f"{key}.md"
        if not p.exists():
            print(f"  [warn] no such file: {p.name}", file=sys.stderr)
            continue
        done, skipped, delta = process(p, vocab, args.apply)
        tot_done += len(done)
        tot_skip += len(skipped)
        if not done and not skipped:
            print(f"\n  {p.name}: no letterspaced runs")
            continue
        print(f"\n  {p.name}: {len(done)} rejoined, {len(skipped)} left alone "
              f"({delta:+d} bytes)")
        for raw, new in dict.fromkeys(done):
            print(f"      {raw[:60]!r}\n        -> {new!r}")
        for raw in dict.fromkeys(skipped):
            print(f"      [SKIP — no clean segmentation] {raw[:60]!r}")
    print(f"\nrejoined: {tot_done}   left alone: {tot_skip}")
    if not args.apply and tot_done:
        print("\nRe-run with --apply to write. Then re-index:")
        print("  ./.runtime/venv/bin/python scripts/indexer.py")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
