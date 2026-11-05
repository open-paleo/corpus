#!/usr/bin/env python3
# corpus-command: clean despace | rejoin OCR word splits
"""Mechanical de-spacing of marker OCR word-splits (dictionary-gated).

Scanned pre-2000 reprints run through marker keep the scan's embedded OCR text
layer, which sprays spurious mid-word spaces ("numerou s", "th e", "orname nta
tion", "correspond s in length"). This pass rejoins them WITHOUT re-OCR, using
the system word list (/usr/share/dict/words) as an oracle. It is deliberately
conservative: every merge produces a dictionary word or a valid inflection, and
a battery of guards suppresses the false-merge
classes. Pre-existing letter-misreads ("nolllen"->nomen, "skulIs"->skulls) and
genuinely-ambiguous both-real-word splits ("for amen"->foramen) are left alone —
this only removes spaces, it never rewrites letters.

Merge rule for adjacent word tokens `a b` (single space between):
  - skip if a and b are BOTH real standalone words ("of ten", "man kind")
  - else merge if a+b is a dict word (>=5 chars, or a vetted common short word)
  - suffix-left: attach a bare inflection (s/es/ed/ing/ly) to a real >=4-char
    stem even if the inflected form isn't in the dict (web2 lacks most)
Guards: single-letter-then-'.' abbreviation ("see p."), digit+unit ("5 m in"),
uppercase initials ("M us."), split proper-noun start ("the N odosauridae"),
right-attachment for 3-token splits ("for th e"->"for the", "to ot her"->"to
other"), and a deny-list of odd web2 short words that only ever arise as wrong
cross-line-break orphan joins.

Usage:
    python scripts/clean_despace.py --key KEY          # dry-run
    python scripts/clean_despace.py --key KEY --apply  # writes .bak + file
    python scripts/clean_despace.py --path some/file.md --top     # ad-hoc, show merges

Not part of intake. Run on demand for a specific rough-marker scan, then
re-index. --apply saves markdown/{key}.md.despace.bak first.
"""
import argparse
import re
import sys
from pathlib import Path
from corpus.keys import key_arg
from corpus.words import WORDS as _WORDS, wordlist_path
from corpus.pages import write_markdown

DICT_PATH = wordlist_path()
WORDS = _WORDS   # read on first use

# genuine short English words; every OTHER len<=2 dict entry (th, e, em, en,
# ye, ...) is treated as a fragment so "th e"->"the", "th is"->"this" merge
# while "of ten", "he art", "a way" (both parts real) stay untouched.
SHORT_OK = {"a","i","am","an","as","at","be","by","do","go","he","if","in",
            "is","it","me","my","no","of","oh","on","or","ox","so","to","up",
            "us","we"}

# odd/archaic web2 entries that only ever arise as wrong cross-line-break orphan
# joins ("er"+"th"->erth, "am"+"en"->amen, "th"+"of"->thof, "IV"+"in"->ivin).
DENY = {"erth","amen","lis","din","dan","ivin","thof","mens","lute","gil",
        "baru","via","win","thed","notd","ands"}

# false merges overwhelmingly produce SHORT words. Only accept a merge whose
# result is >=5 chars (long joins are essentially never spurious) OR is one of
# these hand-vetted common short words. Unlisted short joins are left as spaces.
SAFE_SHORT = {
    "the","and","that","than","this","they","them","their","there","these",
    "those","then","when","from","with","been","more","some","such","were",
    "what","will","your","area","also","into","over","here","have","form",
    "part","bone","bony","jaw","jaws","roof","just","only","must","does","ends",
    "type","wing","wall","keel","head","well","hind","note","nose","acta",
    "main","made","runs","run","due","day","late","near","much","long","land",
    "take","base","post","body","open","text","used","ulna","pes","ribs",
    "toes","upon","each","both","most","many",
}

SUFFIX = {"s", "es", "ed", "ing", "ly"}
TOK = re.compile(r"[A-Za-z]+|[^A-Za-z]+")


# web2 omits most inflected forms, so isword("has") / isword("sources") are
# FALSE and the realword(a) and realword(b) guard never fires for them. That let
# "It has an annual" collapse to "It hasan" (Hasan is a dict proper noun) and
# "Park and re flects" to "Park andre flects" — merging the wrong pair, since
# the real break was re+flects. Treat these as words regardless of the dict.
STOPWORDS = {
    "has", "had", "have", "was", "were", "is", "are", "been", "being", "does",
    "did", "done", "and", "the", "for", "but", "not", "its", "his", "her",
    "our", "their", "this", "that", "these", "those", "with", "from", "they",
    "them", "than", "then", "when", "which", "who", "what", "where", "some",
    "such", "each", "both", "into", "over", "also", "more", "most", "other",
    "only", "very", "can", "will", "would", "may", "might", "must", "should",
}


def isword(t):
    return t.lower() in WORDS or t.lower() in STOPWORDS


def realword(t):
    tl = t.lower()
    if tl in STOPWORDS:
        return True
    if tl not in WORDS:
        return False
    return len(tl) >= 3 or tl in SHORT_OK


def despace(text, log=None):
    toks = TOK.findall(text)
    is_w = [bool(re.fullmatch(r"[A-Za-z]+", t)) for t in toks]
    i = 0
    n = len(toks)
    out_merges = 0
    while i < n:
        if not is_w[i]:
            i += 1
            continue
        j = i + 1
        if j >= n:
            break
        # separator between this word and the next must be exactly one space
        if is_w[j] or toks[j] != " ":
            i += 1
            while i < n and not is_w[i]:
                i += 1
            continue
        k = j + 1
        if k >= n or not is_w[k]:
            break
        a, b = toks[i], toks[k]
        joined = a + b
        merge = True

        # suffix-left: attach a bare inflection to a real stem (web2 lacks most
        # inflected forms; also stops the orphan suffix merging right, e.g.
        # "correspond s in" -> "corresponds in" not "correspond sin"). Bare "d"
        # excluded (nearly always the split start of the next word).
        if (len(a) >= 4 and realword(a) and b.lower() in SUFFIX
                and not b.isupper() and not realword(b)
                and joined.lower() not in DENY):
            if log is not None:
                log[(a, b, joined)] = log.get((a, b, joined), 0) + 1
            toks[i] = joined
            del toks[j:k+1]
            del is_w[j:k+1]
            is_w[i] = True
            n = len(toks)
            out_merges += 1
            continue

        if realword(a) and realword(b):
            merge = False
        # A complete function word is never the left half of a hyphenation
        # break, so if the right fragment is itself a dictionary word the pair
        # is two real words that merely wrapped. Blocks "and"+"re"->andre while
        # leaving "in"+"fluence"->influence alone ("fluence" is not a word).
        elif a.lower() in STOPWORDS and isword(b):
            merge = False
        elif not isword(joined):
            merge = False
        elif joined.lower() in DENY:
            merge = False
        elif len(joined) < 5 and joined.lower() not in SAFE_SHORT:
            merge = False
        else:
            sep_before = toks[i-1] if i >= 1 else ""
            after = toks[k+1] if k + 1 < n else ""
            # single-letter b then '.' -> abbreviation ("see p." = see page)
            if len(b) == 1 and after.startswith("."):
                merge = False
            # single-letter a after a digit -> unit ("5 m in" = 5 metres in)
            if merge and len(a) == 1 and re.search(r"\d\s*$", sep_before):
                merge = False
            # lone uppercase a -> author initial / specimen label ("M us.")
            if merge and len(a) == 1 and a.isupper():
                merge = False
            # lone uppercase b -> split proper-noun start ("the N odosauridae")
            if merge and len(b) == 1 and b.isupper():
                merge = False
            # right-attachment: b belongs to the word on its right, not a
            # ("for th e" -> "for the"; "to ot her" -> "to other")
            if merge and realword(a):
                if (k + 2 < n and toks[k+1] == " " and is_w[k+2]
                        and isword(b + toks[k+2])):
                    merge = False

        if merge:
            if log is not None:
                log[(a, b, joined)] = log.get((a, b, joined), 0) + 1
            toks[i] = joined
            del toks[j:k+1]
            is_w[i] = True
            del is_w[j:k+1]
            n = len(toks)
            out_merges += 1
            continue
        i = k
    return "".join(toks), out_merges


def main():
    ap = argparse.ArgumentParser()
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--key", type=key_arg, help="citation key -> markdown/{key}.md")
    g.add_argument("--path", help="explicit markdown path")
    ap.add_argument("--apply", action="store_true", help="write changes (.bak saved)")
    ap.add_argument("--top", action="store_true", help="print the top merges")
    args = ap.parse_args()

    path = Path(args.path) if args.path else Path("markdown") / f"{args.key}.md"
    if not path.exists():
        sys.exit(f"not found: {path}")
    src = path.read_text(encoding="utf-8")
    log = {}
    out, total = despace(src, log)
    print(f"{path.name}: merges={total}  distinct={len(log)}  "
          f"chars {len(src)}->{len(out)} (removed {len(src)-len(out)} spaces)")
    # safety: every produced token must be a dict word or a valid inflection
    bad = [f"{a!r}+{b!r}->{j!r}" for (a, b, j) in log
           if not isword(j) and not any(j.lower().endswith(s) for s in SUFFIX)]
    if bad:
        print(f"  WARNING: {len(bad)} non-dict/non-inflection joins: {bad[:10]}")
    if args.top:
        for (a, b, j), c in sorted(log.items(), key=lambda x: -x[1])[:60]:
            print(f"  {c:4d}  {a!r}+{b!r} -> {j!r}")
    if args.apply:
        bak = path.with_suffix(path.suffix + ".despace.bak")
        bak.write_text(src, encoding="utf-8")
        write_markdown(path, out)
        print(f"  APPLIED (backup: {bak.name})")


if __name__ == "__main__":
    main()
