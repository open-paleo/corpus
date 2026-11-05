#!/usr/bin/env python3
# corpus-command: qc gutter | marker conversions of line-numbered manuscripts that lost lines
"""
Find marker conversions damaged by a line-number gutter.

Accepted manuscripts (Elsevier and friends) carry a left-margin gutter of
sequential line numbers. marker mishandles them three ways: it wraps body
text into two-column "| 94 | text |" tables, it reorders text around
headings, and — the reason this audit exists — it SILENTLY DELETES whole
lines. Deleted lines leave no artifact, so intake_qc, qc_scan
and the language audit all pass. See
docs/notes/conversion.md ("Gutter-numbered accepted manuscripts").

The PDFs are born-digital, so their own text layer is ground truth. This
walks marker-converted keys, detects which PDFs have a gutter, and for
those measures how many gutter lines are missing from the markdown.

  ./.runtime/venv/bin/python scripts/qc_gutter.py

Two numbers are reported. `line%` is the fraction of gutter lines whose
middle-6-word fingerprint is absent from the markdown — calibrated on a
manuscript whose marker output was checked by hand (3.3%, against 0.7% for
the corrected vision output), so treat ~1% as the noise floor. `content%`
is the share of source words in dropped blocks after excluding publisher
boilerplate. Neither is a verdict: a dropped block can mix a page
watermark with the sentence that followed it, so READ THE EXAMPLES before
deciding a paper needs re-OCR.
  ./.runtime/venv/bin/python scripts/qc_gutter.py --key KEY
  ./.runtime/venv/bin/python scripts/qc_gutter.py --json out.json

Exit code 1 if any key exceeds --loss-threshold (default 2%).
"""
from __future__ import annotations

import argparse
import json
import difflib
import re
import sys
from pathlib import Path

import pypdfium2 as pdfium
from corpus.config import PDF_DIR, MD_DIR
from corpus.manifest import load as manifest_load
from corpus.keys import key_arg

# A page's gutter is a run of lines each opening with a sequential integer.
GUTTER_MIN_FRACTION = 0.55   # of a page's non-trivial lines
GUTTER_MIN_LINES = 8


def strip_leading_number(line: str) -> str:
    return re.sub(r"^\s*\d+\s*", "", line)


def page_lines(text: str) -> list[str]:
    """Split a page into lines, dropping the bare page-number line on top."""
    raw = re.split(r"[\r\n]+", text)
    if raw and raw[0].strip().isdigit():
        raw = raw[1:]
    return raw


def has_gutter(pages: list[str]) -> bool:
    """True if the text layer carries sequential left-margin line numbers.

    Requires the numbers to ASCEND across the sampled pages — a page of
    numeric table cells also starts many lines with digits, but those do
    not climb monotonically.
    """
    nums: list[int] = []
    hits = considered = 0
    for text in pages:
        for ln in page_lines(text):
            if len(ln.strip()) < 3:
                continue
            considered += 1
            m = re.match(r"^\s*(\d+)\s", ln)
            if m:
                hits += 1
                nums.append(int(m.group(1)))
    if considered == 0 or hits < GUTTER_MIN_LINES:
        return False
    if hits / considered < GUTTER_MIN_FRACTION:
        return False
    ascending = sum(1 for a, b in zip(nums, nums[1:]) if b == a + 1)
    return ascending >= 0.6 * (len(nums) - 1)


SUPERSCRIPTS = str.maketrans("", "", "¹²³⁴⁵⁶⁷⁸⁹⁰ª\u00ba")


def norm(s: str) -> str:
    s = re.sub(r"<sup>.*?</sup>", "", s)
    s = re.sub(r"[*_#|`]", "", s)          # DELETE, never space-replace:
    s = (s.replace("’", "'").replace("‘", "'")
          .replace("“", '"').replace("”", '"'))
    s = s.translate(SUPERSCRIPTS)          # affiliation markers
    return " ".join(s.split()).lower()


# Boilerplate marker is RIGHT to drop: publisher covers, per-page watermarks,
# pre-proof stamps. Excluded so the reported figure is content loss, not the
# sum of losses and correct removals: a per-page "(c) the author(s) ... Draft"
# header alone can score 12% of lines with almost no real content missing.
BOILER = re.compile(
    r"click here to (access/download|view linked references)"
    r"|\u00a9\s*the author|their institution\(s\)|earth sciences draft"
    r"|^page \d+ of \d+|pii:\s*s\d|to appear in:|accepted manuscript"
    r"|journal pre-proof|this is a final draft|please refer to the final"
    r"|received date|revised date|downloaded from|all rights reserved"
    r"|for peer review", re.I)
# NB deliberately NOT matching bare URLs: real content cites DOIs, and one
# incidental link would otherwise exclude the whole block it sits in (a
# 600-word figure caption with one DOI, for instance).

MIN_BLOCK = 6           # words; shorter deletes are alignment noise
BOILER_MAX_WORDS = 60   # above this a boilerplate hit is incidental, not the
                        # nature of the block — keep it and let a human judge


def dropped_blocks(src_words: list[str], md_words: list[str]) -> list[tuple[int, str]]:
    """Contiguous runs of source words not alignable to the markdown.

    Used only to SHOW where the loss is. It is not the metric: difflib
    finds spurious partial matches inside a dropped sentence (English
    repeats too many short word runs), so a dropped 12-word line shreds
    into 5- and 7-word deletes and no size threshold separates cleanly.
    """
    sm = difflib.SequenceMatcher(None, src_words, md_words, autojunk=False)
    out = []
    for tag, i1, i2, _j1, _j2 in sm.get_opcodes():
        if tag in ("delete", "replace"):
            out.append((i2 - i1, " ".join(src_words[i1:i2])))
    return out


def heading_gaps(md: str) -> list[int]:
    """Missing integers in the top-level numbered-heading sequence."""
    nums = []
    for m in re.finditer(r"^#+\s*\**\s*(\d+)\.\s", md, re.M):
        n = int(m.group(1))
        if not nums or n != nums[-1]:
            nums.append(n)
    seen = sorted(set(nums))
    if len(seen) < 2:
        return []
    return [n for n in range(seen[0], seen[-1] + 1) if n not in seen]


def audit_one(key: str) -> dict | None:
    pdf_path = PDF_DIR / f"{key}.pdf"
    md_path = MD_DIR / f"{key}.md"
    if not pdf_path.exists() or not md_path.exists():
        return None
    try:
        doc = pdfium.PdfDocument(pdf_path)
        pages = [doc[i].get_textpage().get_text_bounded() for i in range(len(doc))]
        doc.close()
    except Exception as e:
        return {"key": key, "error": str(e)}
    if not pages or not has_gutter(pages[: min(6, len(pages))]):
        return None

    md = md_path.read_text(errors="replace", encoding="utf-8")
    M = norm(md)

    # Metric: per-line presence by a middle-of-line 6-word fingerprint.
    # Validated on a manuscript whose marker output was checked by hand:
    # 15 of 457 lines lost (3.3%), 1 of 457 after vision correction (0.2%).
    # Treat anything under ~1% as the noise floor, not as loss.
    total = missing = 0
    for text in pages:
        for ln in page_lines(text):
            m = re.match(r"^\s*(\d+)\s*(.*)$", ln)
            if not m:
                continue
            txt = m.group(2).strip()
            if len(txt) < 25:
                continue
            w = norm(txt).split()
            if len(w) < 6:
                continue
            total += 1
            mid = " ".join(w[len(w) // 2 - 3: len(w) // 2 + 3])
            if mid not in M:
                missing += 1
    if total == 0:
        return None

    src = []
    for text in pages:
        for ln in page_lines(text):
            src.append(strip_leading_number(ln))
    S = norm(" ".join(src)).split()
    blocks = dropped_blocks(S, M.split())
    content = [b for b in blocks
               if b[0] >= MIN_BLOCK
               and not (b[0] <= BOILER_MAX_WORDS and BOILER.search(b[1]))]
    big = sorted(content, key=lambda b: -b[0])[:5]

    return {
        "key": key,
        "pdf_pages": len(pages),
        "gutter_lines": total,
        "missing_lines": missing,
        "loss_pct": round(100 * missing / total, 1),
        "source_words": len(S),
        "content_words_lost": sum(n for n, _ in content),
        "content_loss_pct": round(100 * sum(n for n, _ in content) / len(S), 1),
        "gutter_table_rows": len(re.findall(r"^\| *\d+ *\|", md, re.M)),
        "heading_gaps": heading_gaps(md),
        "examples": [f"[{n}w] {t[:110]}" for n, t in big],
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--key", type=key_arg, action="append", default=[])
    ap.add_argument("--loss-threshold", type=float, default=0.5,
                    help="Report keys above this %% CONTENT loss (noise floor ~0.3%%).")
    ap.add_argument("--json", default=None, help="Write full results here.")
    args = ap.parse_args()

    manifest = manifest_load()
    if args.key:
        keys = [k for k in args.key if k in manifest]
    else:
        keys = sorted(k for k, e in manifest.items()
                      if ((e.get("conversion") or {}).get("tool")) == "marker")

    print(f"Scanning {len(keys)} marker-converted keys for line-number gutters...",
          file=sys.stderr)
    results = []
    for i, k in enumerate(keys, 1):
        if i % 200 == 0:
            print(f"  ...{i}/{len(keys)}", file=sys.stderr)
        r = audit_one(k)
        if r:
            results.append(r)

    gutter = [r for r in results if "error" not in r]
    flagged = sorted((r for r in gutter
                      if r["content_loss_pct"] > args.loss_threshold),
                     key=lambda r: -r["content_loss_pct"])

    print()
    print(f"gutter manuscripts found: {len(gutter)}")
    print(f"over {args.loss_threshold}% content loss: {len(flagged)}")
    print()
    if flagged:
        print(f"{'key':24} {'pp':>4} {'lines':>6} {'line%':>6} "
              f"{'contentw':>9} {'content%':>9} {'tbl':>5}  gaps")
        print("-" * 92)
        for r in flagged:
            print(f"{r['key']:24} {r['pdf_pages']:4d} {r['gutter_lines']:6d} "
                  f"{r['loss_pct']:5.1f}% {r['content_words_lost']:9d} "
                  f"{r['content_loss_pct']:8.1f}% {r['gutter_table_rows']:5d}  "
                  f"{r['heading_gaps'] or ''}")
    if args.json:
        Path(args.json).write_text(json.dumps(results, indent=2), encoding="utf-8")
        print(f"\nfull results -> {args.json}")
    return 1 if flagged else 0


if __name__ == "__main__":
    sys.exit(main())
