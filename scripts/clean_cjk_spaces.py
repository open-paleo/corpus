#!/usr/bin/env python3
# corpus-command: clean cjk-spaces | remove spaces marker left at line breaks inside Chinese/Japanese text
"""
Remove the spaces marker leaves where a Chinese or Japanese line broke:
"上白垩统分为 左云组", "鲜 被关注", "重 视".

CJK text has no spaces between characters, so marker's line joining turns
each line break inside a sentence into a stray space. A space is removed
only where the PDF's own text layer has the same characters (the six CJK
characters either side, matched exactly once) with nothing or a line break
between them. A space the PDF prints, such as
between two names in a reference list, stays.

    corpus clean cjk-spaces --key KEY            # dry run
    corpus clean cjk-spaces --all --apply        # every file with 20+ such spaces
"""
from __future__ import annotations

import argparse
import re

from corpus.config import MD_DIR, PDF_DIR
from corpus.keys import key_arg
from corpus.pages import write_markdown
from corpus.pdf.text import open_pdf

CJK = r"[぀-ヿ㐀-䶿一-鿿豈-﫿]"
GAP = re.compile(rf"(?<={CJK}) (?={CJK})")
SIDE = 6


def pdf_text(key: str) -> str:
    doc = open_pdf(PDF_DIR / f"{key}.pdf")
    try:
        return "".join(doc.text(p) for p in range(len(doc)))
    finally:
        doc.close()


def strip(s: str) -> str:
    """CJK characters only, in order: what both sides share around a gap."""
    return "".join(re.findall(CJK, s))


def layer_stream(layer: str):
    """The layer's CJK characters in order, and for each the gap before it:
    True where the PDF prints a space there, False for nothing or a line break."""
    chars, spaced = [], []
    gap = ""
    for ch in layer:
        if re.match(CJK, ch):
            chars.append(ch)
            spaced.append(" " in gap.replace("\n", "").replace("\r", "") and "\n" not in gap)
            gap = ""
        elif ch.isspace():
            gap += ch
        else:
            gap = ""
    return "".join(chars), spaced


def repair(md: str, layer: str):
    stream, spaced = layer_stream(layer)
    removed, kept = [], 0
    for m in GAP.finditer(md):
        left = strip(md[max(0, m.start() - 40):m.start()])[-SIDE:]
        right = strip(md[m.end():m.end() + 40])[:SIDE]
        if len(left) < 2 or len(right) < 2 or stream.count(left + right) != 1:
            kept += 1
            continue
        pos = stream.index(left + right) + len(left)
        if spaced[pos]:
            kept += 1
        else:
            removed.append(m.start())
    new = md
    for pos in reversed(removed):
        new = new[:pos] + new[pos + 1:]
    return new, len(removed), kept


def files_for(key: str):
    return [p for p in (MD_DIR / f"{key}.original.md", MD_DIR / f"{key}.md") if p.exists()]


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--key", type=key_arg, action="append")
    ap.add_argument("--all", action="store_true", help="every file with 20 or more CJK-space-CJK gaps")
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args()
    paths = []
    for k in args.key or []:
        paths += files_for(k)
    if args.all:
        paths = [p for p in sorted(MD_DIR.glob("*.md")) if len(GAP.findall(p.read_text(encoding="utf-8"))) >= 20]
    tot_r = tot_k = changed = 0
    for p in paths:
        key = p.name.removesuffix(".md").removesuffix(".original")
        if not (PDF_DIR / f"{key}.pdf").exists():
            continue
        md = p.read_text(encoding="utf-8")
        new, r, k = repair(md, pdf_text(key))
        tot_r += r
        tot_k += k
        if r:
            changed += 1
            print(f"  {p.name:34} removed {r:5}  kept {k:4}")
            if args.apply:
                write_markdown(p, new, md)
    print(f"{len(paths)} file(s); {changed} {'changed' if args.apply else 'would change'}; "
          f"{tot_r} spaces removed, {tot_k} kept")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
