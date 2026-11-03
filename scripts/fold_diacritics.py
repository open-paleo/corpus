#!/usr/bin/env python3
# corpus-command: clean diacritics | fold stray spacing accents into their letters (Osmo´lska -> Osmólska)
"""Fold stray spacing accents back into the letters they belong to.

The defect
----------
Many publisher PDFs emit a diacritic as a SEPARATE spacing character sitting
next to its base letter instead of a precomposed glyph, and the text layer
carries it through verbatim:

    Osmo´lska  -> Osmólska        Lu¨          -> Lü
    Rı´o       -> Río             Soto-Acu~na  -> Soto-Acuña
    Sa~o       -> São             Pala¨ontologie -> Paläontologie

It is common, and nothing flags it: the text is valid Unicode and reads almost
correctly, but every affected name is unsearchable under its real
spelling — "Osmólska" does not match "Osmo´lska" in FTS.

Which side does the accent attach to?
------------------------------------
The accent FOLLOWS its base letter. This matters: "Apesteguı´a" is Apesteguía,
where the acute belongs to the dotless ı BEFORE it, not the a after. A
prefix-first rule yields "Apesteguiá" — wrong, and wrong silently.

Prefix is the FALLBACK, used only when the accent cannot compose with the
preceding letter: "Echapor~a" is Echaporã (r+tilde does not exist, tilde+a
does). Dotless ı/ȷ are mapped to i/j before composing.

Guards
------
Three, each of which caught real damage in testing:

1. PRIME. "**A´**)" in a figure callout is A-prime, not Á. Excluded.
2. TOKEN PLAUSIBILITY. The token must be >=70% letters, so OCR wreckage like
   "Ai^GVL\\R;" is never touched.
3. FILE NOISE GATE — the important one. On rough pre-1930 scans "~" and "^" are
   MISREAD LETTERS, not accents, and folding actively corrupts the file:

       collections^  -> collectionŝ      HORIZO~     -> HORIZÕ
       montanen~is   -> montaneñis       tj^pe       -> tĵpe

   Those files look identical to real ones token by token, so the gate is
   file-level: when candidates that CANNOT compose outnumber the ones that can
   past --noise-threshold, the whole file is refused and reported. Such files
   want vision re-OCR, not surgery — `--list-flagged` prints them.

Usage
-----
    ./.runtime/venv/bin/python scripts/fold_diacritics.py --all
    ./.runtime/venv/bin/python scripts/fold_diacritics.py --all --apply
    ./.runtime/venv/bin/python scripts/fold_diacritics.py --list-flagged
"""

from __future__ import annotations

import argparse
import re
import sys
import unicodedata
from corpus.config import MD_DIR
from corpus.keys import key_arg
from corpus.pages import write_markdown

SPACING = {"´": "́", "¨": "̈", "~": "̃", "^": "̂",
           "`": "̀", "ˇ": "̌", "˚": "̊", "¸": "̧"}
DOTLESS = {"ı": "i", "ȷ": "j"}
ACC = "".join(SPACING)

# Which bases each accent legitimately sits on in European bibliographic text.
# Unicode will happily compose S+circumflex into Ŝ (Esperanto), which turned
# Romanian "S^ınpetru" into "Ŝınpetru" instead of letting the circumflex fall
# to the dotless ı and give "Sînpetru". Composability is not permission.
ALLOWED = {
    "´": "aeiouyacelnorsz",      # á é í ó ú ý ć ń ś ź ĺ ŕ
    "¨": "aeiouy",
    "~": "aon",
    "^": "aeiouw",
    "`": "aeiou",
    "ˇ": "cszredtng",
    "¸": "cst",
    "˚": "au",
}


def allowed(base: str, accent: str) -> bool:
    return base.lower() in ALLOWED.get(accent, "")


POST = re.compile(f"([A-Za-zıȷ])([{re.escape(ACC)}])")
PRE = re.compile(f"([{re.escape(ACC)}])([A-Za-zıȷ])")
TOKEN = re.compile(r"\S+")
PRIME = re.compile(r"^\W*[A-Z]´\W*$")


def classify(tok: str):
    """('fold', new) | ('prime',) | ('garbage',) | ('nocompose',)"""
    if PRIME.match(tok):
        return ("prime",)
    # Measure the WORD, not the decoration around it: "2016<br>Cervigo´n," and
    # "''Iolo´gicas''" are ordinary words wrapped in markup and quotes, and a
    # ratio taken over the raw token drags them under the bar. Backslashes and
    # stray symbols still sink OCR wreckage like "Ai^GVL\\R;".
    if "\\" in tok:
        return ("garbage",)
    core = "".join(c for c in tok if c not in ACC).replace("<br>", "")
    core = core.strip("\"'()[]{}<>*_,;:.!?/|#-–—0123456789")
    letters = sum(c.isalpha() for c in core)
    if letters < 2 or letters / max(len(core), 1) < 0.7:
        return ("garbage",)

    def post(m):
        base = DOTLESS.get(m.group(1), m.group(1))
        # A tilde sitting before an "n" is Spanish ñ, and the n wins: "Espa~na"
        # is España, "Cãnadon"/"Chãnares" are what you get by binding it
        # backwards to the a instead. Only a/o/n take a tilde at all, so this
        # only ever fires on the genuinely ambiguous a~n / o~n case — hand it
        # to the prefix rule.
        if m.group(2) == "~" and tok[m.end():m.end() + 1] in ("n", "N"):
            return m.group(0)
        if not allowed(base, m.group(2)):
            return m.group(0)
        c = unicodedata.normalize("NFC", base + SPACING[m.group(2)])
        return c if len(c) == 1 else m.group(0)

    def pre(m):
        base = DOTLESS.get(m.group(2), m.group(2))
        if not allowed(base, m.group(1)):
            return m.group(0)
        c = unicodedata.normalize("NFC", base + SPACING[m.group(1)])
        return c if len(c) == 1 else m.group(0)

    new = PRE.sub(pre, POST.sub(post, tok))
    return ("fold", new) if new != tok else ("nocompose",)


def fold(text: str):
    parts, prev = [], 0
    n = {"fold": 0, "prime": 0, "garbage": 0, "nocompose": 0}
    for m in TOKEN.finditer(text):
        tok = m.group()
        if not POST.search(tok) and not PRE.search(tok):
            continue
        r = classify(tok)
        n[r[0]] += 1
        if r[0] == "fold":
            parts.append(text[prev:m.start()])
            parts.append(r[1])
            prev = m.end()
    parts.append(text[prev:])
    return "".join(parts), n


def noise_ratio(n) -> float:
    noise = n["garbage"] + n["nocompose"]
    return noise / max(n["fold"] + noise, 1)


def targets(args):
    if args.key:
        return [MD_DIR / f"{k}.md" for k in args.key]
    return [f for f in sorted(MD_DIR.glob("*.md"))
            if not f.name.endswith(".original.md")]


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--key", type=key_arg, action="append")
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--list-flagged", action="store_true")
    ap.add_argument("--noise-threshold", type=float, default=0.35)
    ap.add_argument("--min-noise", type=int, default=8,
                    help="flag as bad OCR only above this many bad candidates")
    args = ap.parse_args()
    if not (args.key or args.all or args.list_flagged):
        ap.error("pass --key, --all or --list-flagged")

    done = flagged = folds = primes = 0
    rows, bad = [], []
    for path in targets(args):
        if not path.exists():
            print(f"  [warn] no such file: {path.name}", file=sys.stderr)
            continue
        text = path.read_text(errors="replace", encoding="utf-8")
        new, n = fold(text)
        if not sum(n.values()):
            continue
        ratio = noise_ratio(n)
        noise = n["garbage"] + n["nocompose"]
        if ratio > args.noise_threshold and noise >= args.min_noise:
            flagged += 1
            bad.append((path.stem, n["fold"], noise, ratio))
            continue
        if not n["fold"]:
            continue
        done += 1
        folds += n["fold"]
        primes += n["prime"]
        rows.append((path.stem, n["fold"], n["prime"]))
        if args.apply:
            write_markdown(path, new)

    if args.list_flagged:
        print(f"=== bad-OCR candidates ({len(bad)}) — vision re-OCR, not folding ===")
        print(f"{'key':<26}{'foldable':>9}{'noise':>7}{'noise%':>8}")
        for k, f, no, r in sorted(bad, key=lambda x: -x[2]):
            print(f"  {k:<24}{f:>9}{no:>7}{r * 100:>7.0f}%")
        return 0

    print(f"=== fold_diacritics — {'APPLY' if args.apply else 'DRY RUN'} ===")
    print(f"  files folded      : {done}")
    print(f"  accents folded    : {folds:,}")
    print(f"  primes preserved  : {primes:,}")
    print(f"  files flagged bad : {flagged}   (--list-flagged to see them)")
    for k, f, p in sorted(rows, key=lambda x: -x[1])[:15]:
        print(f"      {k:<26}{f:>6}")
    if not args.apply:
        print("\nRe-run with --apply to write. Then re-index.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
