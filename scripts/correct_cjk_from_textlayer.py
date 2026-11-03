#!/usr/bin/env python3
"""Correct vision-OCR'd Chinese/Japanese markdown from the PDF's born-digital text layer.

Vision OCR substitutes plausible characters in CJK text, and in Chinese one
character is a word: 肱 humerus -> 胫 tibia, 锄 vomer -> 颧 jugal, 砾 gravel -> 碎,
髫髻山组 -> 髻髻山组. A born-digital PDF's own text layer is the control.

Proposes, per key, every small difference between the layer's CJK characters
and the markdown's (the .original.md if the key is translated):

  - replace/delete runs of <= 4 characters (the substitutions)
  - a dash printed between two characters (中—晚期) that vision read as 一
    "one" (中一晚期); the CJK-only alignment cannot see these, since the dash
    is not a CJK character

Dry run prints a numbered list. Review it: reject layer typos (广范), variant
forms (緑/绿, 內/内), running heads, broken-font glyphs (鄄, 方方土 for 坤), and
pairs where the two texts are aligned against different sentences or table
cells ([T] marks a markdown table). Then apply with the rejected numbers:

    scripts/correct_cjk_from_textlayer.py KEY
    scripts/correct_cjk_from_textlayer.py KEY --apply --skip 0,3,11

Run scripts/audit_vision_textlayer.py first: only a layer it calls
born-digital with usable CJK agreement is a control. Re-translate afterwards
(copy .original.md over .md, delete .original.md, translate_markdown.py
--src-lang zh-cn). The vision cache keeps the uncorrected text.
"""
from __future__ import annotations

import argparse
import difflib
import re
import sys
from pathlib import Path

import pypdfium2 as pdfium
from corpus.config import ROOT
from corpus.keys import key_arg
from corpus.pages import write_markdown

HAN = re.compile(r"[一-鿿]")
DASH = r"[—–－‒―~～\-]"


def target(key: str) -> Path:
    orig = ROOT / "markdown" / f"{key}.original.md"
    return orig if orig.exists() else ROOT / "markdown" / f"{key}.md"


def propose(key: str) -> tuple[Path, str, list[tuple]]:
    doc = pdfium.PdfDocument(ROOT / "pdfs" / f"{key}.pdf")
    raw = "".join(doc[i].get_textpage().get_text_bounded() for i in range(len(doc)))
    layer = "".join(HAN.findall(raw))
    path = target(key)
    md = path.read_text(encoding="utf-8")
    pos = [i for i, c in enumerate(md) if HAN.match(c)]
    mtext = "".join(md[i] for i in pos)
    table = set()
    off = 0
    for line in md.split("\n"):
        if line.lstrip().startswith("|"):
            table.update(range(off, off + len(line)))
        off += len(line) + 1
    edits = []  # (start, end, new, old, in_table, layer_context)
    sm = difflib.SequenceMatcher(None, layer, mtext, autojunk=False)
    for op, a1, a2, b1, b2 in sm.get_opcodes():
        if op not in ("replace", "delete") or max(a2 - a1, b2 - b1) > 4:
            continue
        if op == "replace":
            s, e = pos[b1], pos[b2 - 1] + 1
        else:
            s = e = pos[b1] if b1 < len(pos) else len(md)
        ctx = f"{layer[max(0, a1 - 6):a1]}[{layer[a1:a2]}]{layer[a2:a2 + 6]}"
        edits.append((s, e, layer[a1:a2], md[s:e], s in table, ctx))
    flat = re.sub(r"\s+", "", raw)
    for m in re.finditer(rf"([一-鿿）)])({DASH}+)([一-鿿]{{2}})", flat):
        a, dash, b = m.groups()
        if a + "一" + b in flat:
            continue
        for mm in re.finditer(re.escape(a) + r"\s*(一)\s*" + re.escape(b), md):
            s = mm.start(1)
            if not any(x[0] == s for x in edits):
                edits.append((s, s + 1, dash[0], "一", s in table, f"{a}[{dash}]{b} (dash)"))
    edits.sort(key=lambda x: x[0])
    return path, md, edits


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("key", type=key_arg)
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--skip", default="", help="comma-separated edit numbers to leave out")
    args = ap.parse_args()
    key = args.key                       # key_arg: NFC and validated
    path, md, edits = propose(key)
    for n, (s, e, new, old, tab, ctx) in enumerate(edits):
        show = f"…{md[max(0, s - 8):s]}[{old}]{md[e:e + 8]}…".replace("\n", " ")
        print(f"{n:3d} {'[T]' if tab else '   '} {old or '∅'} -> {new}   md{show}  layer…{ctx}…")
    print(f"{key}: {path.name}, {len(edits)} edit(s) proposed")
    if args.apply:
        skip = {int(x) for x in args.skip.split(",") if x.strip()}
        keep = [x for n, x in enumerate(edits) if n not in skip]
        for s, e, new, *_ in sorted(keep, reverse=True):
            md = md[:s] + new + md[e:]
        write_markdown(path, md)
        print(f"applied {len(keep)}, skipped {len(edits) - len(keep)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
