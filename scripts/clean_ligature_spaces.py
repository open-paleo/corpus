#!/usr/bin/env python3
# corpus-command: clean ligature-spaces | rejoin words a text layer splits after an fi/fl ligature ("defi ned")
"""
Rejoin words split by a space after an f-ligature: "defi ned", "signifi es",
"estratigráfi cos", "fi rst", "refl ect".

Some publishers' fonts (GSA, Revista Mexicana de Ciencias Geológicas, ...)
extract the fi/fl/ff/ffi/ffl ligature followed by a space, so the PDF's own
text layer reads "defi ned" and marker passes it through. The fix only
removes that space, and only where the joined word is one the corpus uses
(in at least MIN_DF files that do not show the damage) and the part before
the space is not itself a word the corpus uses more. Only spaces are
removed; letters are never rewritten.

    corpus clean ligature-spaces --key KEY            # dry run, prints each join
    corpus clean ligature-spaces --all --apply
"""
from __future__ import annotations

import argparse
import re
from collections import Counter

from corpus.config import MD_DIR
from corpus.keys import key_arg
from corpus.lexicon import document_frequencies
from corpus.pages import write_markdown

SPLIT = re.compile(r"(?<![^\W\d_])([^\W\d_]*?(?:ffi|ffl|ff|fi|fl))[  ]([^\W\d_]+)")
MIN_DF = 3


def damaged(text: str) -> bool:
    return len(re.findall(r"[a-zà-ÿ](?:fi|fl) [a-zà-ÿ]{2}", text)) >= 5


def repair(text: str, df: dict[str, int]):
    joins = Counter()

    def sub(m):
        left, right = m.group(1), m.group(2)
        joined = left + right
        j, l, r = joined.lower(), left.lower(), right.lower()
        if df.get(j, 0) < MIN_DF:
            return m.group(0)
        # "fi" + "no" -> "fino" is wanted; "self" + "reported" is two words
        if df.get(l, 0) >= df.get(j, 0) and df.get(r, 0) >= df.get(j, 0):
            return m.group(0)
        joins[f"{left} {right} -> {joined}"] += 1
        return joined

    return SPLIT.sub(sub, text), joins


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--key", type=key_arg, action="append")
    ap.add_argument("--all", action="store_true", help="every markdown file showing the damage")
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--rebuild-lexicon", action="store_true")
    args = ap.parse_args()
    df = document_frequencies(MD_DIR, "lexicon_ligature_spaces", damaged, rebuild=args.rebuild_lexicon)
    if args.all:
        paths = sorted(p for p in MD_DIR.glob("*.md") if damaged(p.read_text(encoding="utf-8")))
    else:
        paths = []
        for k in args.key or []:
            paths += [p for p in (MD_DIR / f"{k}.md", MD_DIR / f"{k}.original.md") if p.exists()]
    total, changed = Counter(), 0
    for p in paths:
        text = p.read_text(encoding="utf-8")
        new, joins = repair(text, df)
        if not joins:
            continue
        changed += 1
        total.update(joins)
        print(f"  {p.name:34} {sum(joins.values()):5} joins  e.g. {', '.join(list(joins)[:3])}")
        if args.apply:
            write_markdown(p, new, text)
    print(f"{len(paths)} file(s) checked; {changed} {'changed' if args.apply else 'would change'}; "
          f"{sum(total.values())} joins, {len(total)} distinct")
    if not args.apply and len(args.key or []) and total:
        for j, n in total.most_common(40):
            print(f"    {n:4}  {j}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
