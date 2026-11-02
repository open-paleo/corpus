#!/usr/bin/env python3
# corpus-command: clean running-heads | repeated per-page heads and feet
"""Remove per-page running heads/feet that survive vision OCR.

Why this exists
---------------
`convert_with_vision.py`'s prompt says "Skip page headers, page footers, and
page numbers", but compliance is imperfect on scanned journal pages — the
running head is visually part of the text block, so it often comes through.
Worse, marker and vision both sometimes promote it to a `##` heading, which
corrupts the document outline (a fake section per page).

Rather than hand-writing a regex per paper (done three times now: mader1989a,
hulke1879a, ...), this detects page furniture **structurally**: a running head
is a SHORT line whose text, once page numbers are stripped, RECURS many times
across the document. Real prose lines don't.

Detection rules (all must hold):
  - line is short (< --max-len chars, default 95)
  - line is not markup furniture — no `![](…)` image placeholder, no HTML tag,
    no markdown link. Marker emits these in bulk (`![](_page_0_Picture_1.jpeg)`,
    `<span id="page-3-0"></span>`) and they trivially clear a naive
    repeat threshold while being structurally meaningful.
  - line is not a markdown table row (`| … |`) — table rows are data.
  - line is not a figure/table/plate caption. Captions are content, but a run
    of them collapses to one key once digits are stripped ("Fig. 3.1 (Legend
    see p. 78)" and "Fig. 5 (Legend see p. 81)" both normalise to
    "fig legend see p"), so they would otherwise clear the threshold too.
  - after normalising away markdown hashes/emphasis, digits, and punctuation,
    the residue has >= --min-words words (default 3) — this protects bare
    "Fig. 1." / "Table 2." markers, whose residue is 1 word
  - the residue is >= 60% alphabetic (filters numeric table rows)
  - that normalised residue occurs >= --min-repeats times (default 3)

Tuning note: the default --min-words 3 will MISS short journal running heads
whose residue is only two words once digits are stripped — e.g.
"188 PALAEONTOLOGY, VOLUME 38" normalises to "palaeontology volume". When the
dry run leaves such heads behind, re-run with --min-words 2 and read the list;
that is safe per-key but noticeably looser for a --all sweep.

ALWAYS REVIEW THE PRINTED LIST BEFORE --apply. The repeat heuristic is strong
but not infallible: a short line of real content that genuinely recurs (a table
axis label, a refrain like "1 km wide body") will also trip it. This is why the
corpus-wide sweep is gated behind an explicit --all: the intended use is
per-key, right after a vision intake, eyeballing the handful of lines it names.

Usage
-----
    ./.runtime/venv/bin/python scripts/strip_running_heads.py --key hulke1879a
    ./.runtime/venv/bin/python scripts/strip_running_heads.py --key K --apply
    ./.runtime/venv/bin/python scripts/strip_running_heads.py --all   # review only
"""

from __future__ import annotations

import argparse
import re
import sys
from collections import Counter
from pathlib import Path
from corpus.config import MD_DIR
from corpus.keys import key_arg
from corpus.io import atomic_write_text

# Strip leading heading hashes, emphasis, digits and punctuation so that
# "## J. W. HULKE ON ORNITHOPSIS. 753" and "756 J. W. HULKE ON ORNITHOPSIS."
# collapse to the same key.
_NORM_STRIP = re.compile(r"[#*_`~]|[0-9]|[^\w\s]", re.UNICODE)

# Markup furniture marker emits in bulk — never page furniture in the
# running-head sense, and structurally meaningful (anchors are link targets).
_MARKUP = re.compile(r"^\s*(?:!\[|<[a-zA-Z/])|\]\(")

# Figure/table/plate captions are CONTENT, never page furniture — but a run of
# them collapses to one normalised key once digits are stripped
# ("Fig. 3.1 (Legend see p. 78)" / "Fig. 5 (Legend see p. 81)" both become
# "fig legend see p") and so trivially clears the repeat threshold. Caught on
# galton1980a, where three real cross-references were proposed for deletion.
_CAPTION = re.compile(
    r"^\s*[#*_\s]*(?:fig(?:ure)?s?|tab(?:le)?s?|plates?|pl)\b\.?\s*[\dIVXivx]",
    re.IGNORECASE)

# Markdown table rows are DATA, never page furniture. Whole blocks of them
# share a normalised key once digits are stripped — "| Total length | 363 mm |"
# and "| Total length | 185 mm |" both become "total length mm" — so a
# measurement or specimen table trivially clears the repeat threshold. Caught
# on martinelli2004a (MACN-PV specimen rows) and sereno1996a (phalanx
# measurements).
_TABLE_ROW = re.compile(r"^\s*\|")


def normalise(line: str) -> str:
    return " ".join(_NORM_STRIP.sub(" ", line).lower().split())


def eligible(s: str, max_len: int, min_words: int) -> str | None:
    """Return the normalised key if `s` could be a running head, else None."""
    if (not s or len(s) > max_len or _MARKUP.search(s)
            or _CAPTION.match(s) or _TABLE_ROW.match(s)):
        return None
    norm = normalise(s)
    if len(norm.split()) < min_words:
        return None
    letters = sum(c.isalpha() for c in norm)
    if not norm or letters / max(1, len(norm.replace(" ", ""))) < 0.60:
        return None
    return norm


def find_running_heads(lines: list[str], max_len: int, min_words: int,
                       min_repeats: int) -> set[str]:
    counts: Counter[str] = Counter()
    for ln in lines:
        norm = eligible(ln.strip(), max_len, min_words)
        if norm:
            counts[norm] += 1
    return {k for k, n in counts.items() if n >= min_repeats}


_HEADING = re.compile(r"^#{1,6}\s+(.*?)\s*$")
# a heading that is only a page number, once emphasis and rule dashes are gone:
# "# 250", "# — 545 —", "## 1652."
_NUM_HEADING = re.compile(r"^[\s—–\-*_]*\d{1,4}[\s—–\-*_.]*$")


def heading_text(line: str) -> str | None:
    m = _HEADING.match(line)
    return m.group(1) if m else None


def find_heading_furniture(lines: list[str], patterns: list[re.Pattern],
                           drop_numbers: bool) -> list[int]:
    """Indices of HEADING lines that are page furniture by explicit pattern.

    Complements the repeat heuristic, which needs --min-repeats occurrences and
    so misses running heads in short papers (a 2-page article has each head
    once or twice). Matching is on the heading text with markdown emphasis
    stripped, so `# 88 *O. C. Marsh*—*Notice…*` matches `O\\. C\\. Marsh`.
    """
    out = []
    for i, ln in enumerate(lines):
        txt = heading_text(ln.strip())
        if txt is None:
            continue
        bare = re.sub(r"[*_]", "", txt).strip()
        if drop_numbers and _NUM_HEADING.match(bare):
            out.append(i)
        elif any(p.search(bare) for p in patterns):
            out.append(i)
    return out


def process(path: Path, args) -> tuple[int, int, list[str]]:
    text = path.read_text(errors="replace", encoding="utf-8")
    lines = text.split("\n")
    heads = find_running_heads(lines, args.max_len, args.min_words,
                               args.min_repeats)
    hpats = [re.compile(p) for p in args.heading_regex]
    furniture = set(find_heading_furniture(lines, hpats, args.drop_number_headings))
    if not heads and not furniture:
        return 0, 0, []
    keeps = [re.compile(p) for p in args.exclude]
    keep, cut = [], []
    for i, ln in enumerate(lines):
        s = ln.strip()
        if i in furniture and not any(k.search(s) for k in keeps):
            cut.append(s)
            continue
        norm = eligible(s, args.max_len, args.min_words)
        if norm and norm in heads and not any(k.search(s) for k in keeps):
            cut.append(s)
            continue
        keep.append(ln)
    out = re.sub(r"\n{3,}", "\n\n", "\n".join(keep))
    removed = len(text) - len(out)
    if args.apply and cut:
        atomic_write_text(path, out)
    return len(cut), removed, cut


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--key", type=key_arg, action="append", default=[],
                    help="citation key (repeatable) — the intended mode")
    ap.add_argument("--all", action="store_true",
                    help="sweep every markdown file (review-only; needs --apply "
                         "too, and you should read every line it names first)")
    ap.add_argument("--apply", action="store_true",
                    help="write changes (default is a dry run)")
    ap.add_argument("--exclude", action="append", default=[], metavar="REGEX",
                    help="never remove lines matching REGEX (repeatable). Use "
                         "this to act on the dry-run review when real repeated "
                         "content is caught — e.g. plate captions that repeat "
                         "the taxon and specimen number on every plate page")
    ap.add_argument("--heading-regex", action="append", default=[], metavar="REGEX",
                    help="drop any HEADING line whose text matches REGEX "
                         "(repeatable), regardless of how often it repeats. For "
                         "running heads the frequency rule misses because the "
                         "paper is too short — e.g. --heading-regex 'O\\. C\\. "
                         "Marsh' on an 8-page AJS notice. Emphasis is stripped "
                         "before matching")
    ap.add_argument("--drop-number-headings", action="store_true",
                    help="drop headings that are only a page number ('# 250', "
                         "'# — 545 —'), which vision emits when the running "
                         "head is just the folio")
    ap.add_argument("--max-len", type=int, default=95)
    ap.add_argument("--min-words", type=int, default=3)
    ap.add_argument("--min-repeats", type=int, default=3)
    args = ap.parse_args()

    if not args.key and not args.all:
        ap.error("pass --key KEY (repeatable), or --all to sweep the corpus")
    paths = ([MD_DIR / f"{k}.md" for k in args.key] if args.key
             else sorted(MD_DIR.glob("*.md")))
    missing = [p for p in paths if not p.exists()]
    for p in missing:
        print(f"  [warn] no such file: {p.name}", file=sys.stderr)
    paths = [p for p in paths if p.exists()]

    mode = "APPLY" if args.apply else "DRY RUN (no files written)"
    print(f"=== strip_running_heads — {mode} ===")
    tot_files = tot_lines = tot_bytes = 0
    for p in paths:
        n, b, cut = process(p, args)
        if not n:
            continue
        tot_files += 1
        tot_lines += n
        tot_bytes += b
        print(f"\n  {p.name}  (-{b} bytes, {n} lines)")
        for s in dict.fromkeys(cut):          # unique forms, order preserved
            print(f"      ✂ {s[:88]}")
    print(f"\nfiles scanned:   {len(paths)}")
    print(f"files changed:   {tot_files}")
    print(f"lines removed:   {tot_lines}")
    print(f"bytes removed:   {tot_bytes:,}")
    if not args.apply and tot_files:
        print("\nRe-run with --apply to write. Then re-index:")
        print("  ./.runtime/venv/bin/python scripts/indexer.py")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
