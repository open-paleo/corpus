#!/usr/bin/env python3
# corpus-command: qc scan | PUA garbage, CJK, image-only stubs
"""
Pre-OCR validation: scan markdown/{key}.md for failure modes that need
vision-OCR even though marker produced non-empty output.

Detection modes:
  - PUA-CID garbage (private-use codepoints + U+FFFD replacement chars):
    >threshold ⇒ marker extracted broken font CID streams as garbage.
  - Substantial CJK content: >threshold ⇒ Chinese/Japanese/Korean body
    that should be vision-OCR'd for translation.
  - Image-only stub: tiny markdown body that's mostly `![](image.jpeg)`
    references with little real prose. Indicates the source PDF is a pure
    scan with no text layer; marker had nothing to extract.

With --apply, sets `manifest[key].conversion.vision_ocr_needed = True`
(plus a reason string) for any flagged paper, so the next
`convert_with_vision.py` run picks them up automatically.

Usage:
  ./.runtime/venv/bin/python scripts/scan_markdown_quality.py
      [--pua-threshold 0.05] [--cjk-threshold 0.10]
      [--stub-min-alpha 100] [--stub-image-ratio 0.5]
      [--show-sample] [--apply]
"""

from __future__ import annotations
import argparse
import re
import sys
from datetime import datetime, timezone
from corpus.config import MD_DIR
from corpus.manifest import commit as manifest_commit, load as manifest_load
from corpus.keys import key_from_path

IMAGE_REF_RE = re.compile(r"!\[[^\]]*\]\([^)]+\)")


def is_pua(c: str) -> bool:
    cp = ord(c)
    return 0xE000 <= cp <= 0xF8FF or cp == 0xFFFD


def is_cjk(c: str) -> bool:
    cp = ord(c)
    # CJK Unified Ideographs (main), Extension A, Hiragana, Katakana, Hangul
    return (
        0x4E00 <= cp <= 0x9FFF
        or 0x3400 <= cp <= 0x4DBF
        or 0x3040 <= cp <= 0x309F
        or 0x30A0 <= cp <= 0x30FF
        or 0xAC00 <= cp <= 0xD7AF
    )


# One regex pass per class instead of a Python loop over every character
# (the corpus is ~345 MB of markdown; three loops took ~70 s).
PUA_RE = re.compile("[\ue000-\uf8ff\ufffd]")
CJK_RE = re.compile("[\u4e00-\u9fff\u3400-\u4dbf\u3040-\u309f\u30a0-\u30ff\uac00-\ud7af]")
# Letters, digits-excluded: every str.isalpha() character matches, plus a few
# non-decimal numerals (², ½), so the count is an upper bound on isalpha().
LETTERISH_RE = re.compile(r"[^\W\d_]")


def count_alpha(text: str, exact_below: int) -> int:
    """str.isalpha() count. The regex upper bound is exact enough above
    `exact_below` (only the comparison with the stub threshold uses it);
    below that the exact loop runs, on a file that is mostly not letters."""
    approx = len(LETTERISH_RE.findall(text))
    if approx >= exact_below:
        return approx
    return sum(1 for c in text if c.isalpha())


def detect_image_stub(text: str, n_alpha: int, min_alpha: int, image_ratio_min: float
                      ) -> tuple[bool, str]:
    """Detect markdown that's mostly image references with little real prose.
    Returns (is_stub, reason_str)."""
    if n_alpha >= min_alpha:
        return False, ""
    # Compute what fraction of the body is taken up by image-reference markup.
    image_chars = sum(len(m.group(0)) for m in IMAGE_REF_RE.finditer(text))
    non_ws = len(re.sub(r"\s+", "", text)) or 1
    image_ratio = image_chars / non_ws if image_chars else 0.0
    if image_ratio >= image_ratio_min:
        return True, (f"image-only stub: {n_alpha} alpha chars (<{min_alpha}), "
                      f"{image_ratio:.0%} of non-whitespace is image markup")
    # Even without image refs, very low alpha-char count on a non-trivial file
    # is suspicious — probably equation glyphs or table chars. Flag it too.
    if n_alpha < min_alpha and len(text) >= 50:
        return True, (f"sparse stub: {n_alpha} alpha chars (<{min_alpha}) "
                      f"in {len(text)}-byte body")
    return False, ""


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--pua-threshold", type=float, default=0.05)
    ap.add_argument("--cjk-threshold", type=float, default=0.10)
    ap.add_argument("--stub-min-alpha", type=int, default=300,
                    help="Markdown with fewer alpha chars is a stub candidate. "
                    "Default matches translate_markdown.py's skip threshold so "
                    "anything translate would refuse, scan will flag for re-OCR.")
    ap.add_argument("--stub-image-ratio", type=float, default=0.5,
                    help="If image-markup chars / non-ws chars exceeds this in a stub, flag.")
    ap.add_argument("--show-sample", action="store_true",
                    help="Print a 200-char sample from each flagged file.")
    ap.add_argument("--apply", action="store_true",
                    help="Set vision_ocr_needed=True in manifest for flagged papers.")
    args = ap.parse_args()

    pua_hits, cjk_hits, stub_hits = [], [], []
    n_scanned = 0
    for md in sorted(MD_DIR.glob("*.md")):
        # Skip our visible-original backups so we don't double-flag them
        if md.stem.endswith(".original"):
            continue
        text = md.read_text(errors="replace", encoding="utf-8")
        n = len(text)
        if n == 0:
            continue  # zero-byte already in backlog
        n_scanned += 1
        n_pua = len(PUA_RE.findall(text))
        n_cjk = len(CJK_RE.findall(text))
        n_alpha = count_alpha(text, exact_below=2 * args.stub_min_alpha)
        pua_ratio = n_pua / n
        cjk_ratio = n_cjk / n
        if pua_ratio >= args.pua_threshold:
            pua_hits.append((pua_ratio, key_from_path(md), n, n_pua, text))
        if cjk_ratio >= args.cjk_threshold:
            cjk_hits.append((cjk_ratio, key_from_path(md), n, n_cjk, text))
        is_stub, reason = detect_image_stub(text, n_alpha, args.stub_min_alpha,
                                             args.stub_image_ratio)
        if is_stub:
            stub_hits.append((n_alpha, key_from_path(md), n, reason, text))

    print(f"Scanned {n_scanned} non-empty markdown files")
    print()
    print(f"=== PUA-CID garbage (≥{args.pua_threshold:.0%} PUA + U+FFFD chars) ===")
    print(f"  hits: {len(pua_hits)}")
    for ratio, k, n, n_pua, text in sorted(pua_hits, reverse=True):
        print(f"    {k:30s}  {ratio:6.1%}  ({n_pua}/{n} chars)")
        if args.show_sample:
            sample = text[:200].replace("\n", " ")
            print(f"      sample: {sample!r}")

    print()
    print(f"=== Substantial CJK content (≥{args.cjk_threshold:.0%} CJK chars) ===")
    print(f"  hits: {len(cjk_hits)}")
    for ratio, k, n, n_cjk, text in sorted(cjk_hits, reverse=True):
        print(f"    {k:30s}  {ratio:6.1%}  ({n_cjk}/{n} chars)")
        if args.show_sample:
            idx = next((i for i, c in enumerate(text) if is_cjk(c)), 0)
            start = max(0, idx - 50)
            sample = text[start:start + 200].replace("\n", " ")
            print(f"      sample: {sample!r}")

    print()
    print(f"=== Image-only / sparse stubs (<{args.stub_min_alpha} alpha chars) ===")
    print(f"  hits: {len(stub_hits)}")
    for n_alpha, k, n, reason, text in sorted(stub_hits):
        print(f"    {k:30s}  {n_alpha:>4} alpha / {n:>5} B  — {reason}")
        if args.show_sample:
            sample = text[:200].replace("\n", " ")
            print(f"      sample: {sample!r}")

    if args.apply:
        all_flagged = (
            {k for _, k, *_ in pua_hits}
            | {k for _, k, *_ in cjk_hits}
            | {k for _, k, *_ in stub_hits}
        )
        if not all_flagged:
            print("\n--apply: nothing to flag.")
            return 0
        manifest = manifest_load()
        now = datetime.now(timezone.utc).isoformat()
        n_set = 0
        for k in sorted(all_flagged):
            if k not in manifest:
                continue
            conv = manifest[k].setdefault("conversion", {})
            if conv.get("vision_ocr_needed"):
                continue
            reasons = []
            if k in {x for _, x, *_ in pua_hits}: reasons.append("PUA-CID garbage")
            if k in {x for _, x, *_ in cjk_hits}: reasons.append("substantial CJK content")
            if k in {x for _, x, *_ in stub_hits}: reasons.append("image-only/sparse stub")
            conv["vision_ocr_needed"] = True
            conv["vision_ocr_reason"] = "; ".join(reasons) + " (scan_markdown_quality.py --apply)"
            conv["vision_ocr_flagged_at"] = now
            n_set += 1
        manifest_commit(manifest)
        print(f"\n--apply: set vision_ocr_needed=True on {n_set} keys.")

    return 0


if __name__ == "__main__":
    sys.exit(main())
