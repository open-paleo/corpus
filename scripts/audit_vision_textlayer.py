#!/usr/bin/env python3
# corpus-command: qc textlayer | vision output vs the PDF text layer
"""Audit vision-OCR output against the PDF's own text layer, page by page.

Vision OCR writes fluent text, so a skipped sentence or a plausible wrong
character passes every other QC script. When the PDF also carries a text
layer, that layer is a control. In a born-digital Chinese PDF it can expose a
dropped sentence and dozens of substituted characters (vomer -> jugal,
humerus -> tibia, 庞 -> 渑) that are invisible after translation.

Per page, the cached vision markdown (.runtime/cache/vision/{key}/page-NNNN.json)
is aligned with the same page's text layer: CJK pages character by character,
others word by word. Reported per key:

  layer   born-digital (no image covering most of the page) or scan-ocr. Only a
          born-digital layer is authoritative; a scan's OCR layer is itself
          noisy, so its "substitutions" are mostly the layer's own errors.
  drops   runs of >= 12 CJK characters / 8 words in the layer that appear
          nowhere in the vision text. Reordered tables and running heads are
          discounted by searching the whole document, so what remains is
          usually real omission. Check each one against the page image.
  agree   share of layer tokens the alignment matched; below 0.5 the layer is
          garbage (broken CID map) and the key is marked unusable.
  subs    small replacements (<= 4 tokens either side). On a born-digital CJK
          layer these are vision misreads; on Latin-script layers many are
          hyphenation and ligature noise, so read the list.

Report only; nothing is changed. Fixing is per paper: see docs/notes/conversion.md,
"A born-digital Chinese PDF's own text layer is a full control".

    scripts/audit_vision_textlayer.py                 # every claude-vision key
    scripts/audit_vision_textlayer.py --cjk           # keys whose text layer has CJK text
    scripts/audit_vision_textlayer.py --key KEY --show   # list each drop and sub
"""
from __future__ import annotations

import argparse
import difflib
import json
import re
import sys
import unicodedata
from collections import Counter
from pathlib import Path

import pypdfium2 as pdfium
from corpus.config import ROOT
from corpus.manifest import load as manifest_load
from corpus.keys import key_arg

MANIFEST = ROOT / "manifest.json"
CACHE = ROOT / ".runtime" / "cache" / "vision"
PDFS = ROOT / "pdfs"

CJK = re.compile(r"[぀-ヿ一-鿿]")
WORD = re.compile(r"[^\W_]+", re.UNICODE)
DROP_CJK, DROP_WORDS, SUB_MAX = 12, 8, 4


LINE_NO = re.compile(r"(?m)^\s*\d{1,4}(?=\s)(?!\s*[.)\]])")


def tokens(text: str, cjk: bool) -> list[str]:
    text = unicodedata.normalize("NFKC", text).replace("-\r\n", "").replace("-\n", "")
    if cjk:
        return CJK.findall(text)
    text = CJK.sub(" ", text)
    return [w.lower() for w in WORD.findall(text) if not w.isdigit() or len(w) > 1]


def absent(run: list[str], cjk: bool, whole: str, width: int = 6) -> bool:
    """True when most windows of the run occur nowhere in the vision text.
    Windows (not the exact run) so a reordered table or one misread
    character inside a passage does not count as a drop."""
    w = width * 2 if cjk else width
    sep = "" if cjk else " "
    wins = [sep.join(run[i:i + w]) for i in range(0, max(1, len(run) - w + 1), max(1, w // 2))]
    return sum(x not in whole for x in wins) / len(wins) > 0.5


def page_is_scan(page) -> bool:
    """A page image covering most of the page, or text drawn invisibly (the
    OCR layer of a scan; render mode 3), means the layer is OCR, not source."""
    w, h = page.get_size()
    invisible = visible = 0
    for obj in page.get_objects(max_depth=4):
        if obj.type == pdfium.raw.FPDF_PAGEOBJ_IMAGE:
            l, b, r, t = obj.get_pos()
            if (r - l) * (t - b) > 0.6 * w * h:
                return True
        elif obj.type == pdfium.raw.FPDF_PAGEOBJ_TEXT:
            if pdfium.raw.FPDFTextObj_GetTextRenderMode(obj.raw) == pdfium.raw.FPDF_TEXTRENDERMODE_INVISIBLE:
                invisible += 1
            else:
                visible += 1
    return invisible > visible


def compare(pages, cjk: bool, show: bool):
    sep = "" if cjk else " "
    whole = sep.join(tokens("\n".join(p[2] for p in pages), cjk))
    total = matched = 0
    drops, subs = [], []
    for n, layer, md, _ in pages:
        A, B = tokens(LINE_NO.sub(" ", layer), cjk), tokens(md, cjk)
        total += len(A)
        sm = difflib.SequenceMatcher(None, A, B, autojunk=False)
        matched += sum(b.size for b in sm.get_matching_blocks())
        for op, a1, a2, b1, b2 in sm.get_opcodes():
            if op in ("equal", "insert"):
                continue
            run = A[a1:a2]
            if len(run) >= (DROP_CJK if cjk else DROP_WORDS):
                if absent(run, cjk, whole):
                    drops.append((n, sep.join(run)))
            elif op == "replace" and max(a2 - a1, b2 - b1) <= SUB_MAX:
                subs.append((n, sep.join(run), sep.join(B[b1:b2])))
    # A run that recurs on three or more pages is a stamp or running head
    # (bioRxiv licence line, journal footer) that vision is told to skip.
    seen = Counter(s[:40] for _, s in drops)
    drops = [d for d in drops if seen[d[1][:40]] < 3]
    if show:
        label = "CJK" if cjk else "LAT"
        for n, s in drops:
            print(f"  {label} DROP p{n}: {s[:160]}")
        for n, a, b in subs:
            print(f"  {label} SUB  p{n}: {a} -> {b}")
    size = (lambda s: len(s)) if cjk else (lambda s: len(s.split()))
    return {"tokens": total, "agree": round(matched / max(1, total), 3), "drops": len(drops),
            "drop_tokens": sum(size(s) for _, s in drops), "subs": len(subs)}


def audit(key: str, show: bool = False) -> dict | None:
    pdf = PDFS / f"{key}.pdf"
    cdir = CACHE / key
    if not pdf.exists() or not cdir.is_dir():
        return None
    doc = pdfium.PdfDocument(pdf)
    pages = []
    for i in range(len(doc)):
        cp = cdir / f"page-{i + 1:04d}.json"
        if not cp.exists():
            continue
        md = json.loads(cp.read_text(encoding="utf-8")).get("markdown") or ""
        pages.append((i + 1, doc[i].get_textpage().get_text_bounded(), md, page_is_scan(doc[i])))
    if not pages:
        return None
    scan = sum(p[3] for p in pages) > len(pages) / 2
    row = {"key": key, "pages": len(pages), "source": "scan-ocr" if scan else "born-digital"}
    layer_cjk = sum(len(CJK.findall(p[1])) for p in pages)
    for cjk, name in ((True, "cjk"), (False, "lat")):
        if cjk and layer_cjk < 300:
            continue
        r = compare(pages, cjk, show)
        if r["tokens"] < 200:
            continue
        r["usable"] = r["agree"] >= 0.5
        row[name] = r
    return row if ("cjk" in row or "lat" in row) else None


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--key", type=key_arg, action="append")
    ap.add_argument("--cjk", action="store_true", help="only keys whose text layer is mostly CJK")
    ap.add_argument("--show", action="store_true", help="list every drop and substitution")
    ap.add_argument("--json", help="also write rows to this JSON file")
    args = ap.parse_args()
    manifest = manifest_load()
    keys = list(args.key) if args.key else sorted(
        k for k, e in manifest.items() if (e.get("conversion") or {}).get("tool") == "claude-vision")
    rows = []
    for k in keys:
        try:
            r = audit(k, args.show)
        except Exception as e:  # a broken PDF should not stop the sweep
            print(f"{k}: {e}", file=sys.stderr)
            continue
        if r and (not args.cjk or "cjk" in r):
            rows.append(r)

    def score(r):
        return sum(r[s]["drop_tokens"] * (1 if s == "lat" else 0.5) for s in ("cjk", "lat")
                   if s in r and r[s]["usable"])
    rows.sort(key=lambda r: (r["source"] != "born-digital", -score(r)))
    print(f"{'key':24s} {'source':12s} {'pp':>4s}  {'CJK agree/drops/dropped/subs':30s} {'Latin agree/drops/dropped/subs'}")
    fmt = lambda r: (f"{r['agree']:.2f} {r['drops']:4d} {r['drop_tokens']:6d} {r['subs']:5d}"
                     + ("" if r["usable"] else " UNUSABLE")) if r else "-"
    for r in rows:
        print(f"{r['key']:24s} {r['source']:12s} {r['pages']:4d}  {fmt(r.get('cjk')):30s} {fmt(r.get('lat'))}")
    print(f"\n{len(rows)} key(s) with a usable text layer. Report only; check drops against the page image.")
    if args.json:
        Path(args.json).write_text(json.dumps(rows, indent=1, ensure_ascii=False), encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
