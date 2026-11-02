#!/usr/bin/env python3
"""
Step 2 (vision recovery, post-pass): remove boundary duplication left by
`recover_blocked_pages.py`.

Why this exists
---------------
`recover_blocked_pages.py` salvages a content-filter-blocked page by splitting
it geometrically and OCR'ing the pieces, then stitching them with a fuzzy
suffix/prefix dedup. That stitch is imperfect: when a paragraph lands *fully
inside* the overlap region, both halves transcribe it in full and the seam
heuristic misses it, leaving a duplicated block in the middle of the page.

CLAUDE.md and the /intake skill both say "eyeball the recovered pages and clean
up near-boundary duplication by hand". This script is that step, automated:
iteratively find the longest repeated substring on a page and drop the second
copy, until nothing above the threshold remains.

It writes BOTH sides, per the /intake skill:
  * `.runtime/cache/vision/{key}/page-NNNN.json` (`markdown` field) — so future
    `convert_with_vision.py` runs cache-hit on clean content instead of
    re-serving the duplicated text.
  * `markdown/{key}.md` — rebuilt from the cleaned cache, pages joined with
    "\\n\\n---\\n\\n".

Per `docs/notes/conversion.md` ("Map recovered pages by the cache string"), the rebuild maps pages by
**cache filename**, never by splitting the existing markdown on "---" — recovered
text can contain its own horizontal rules, which shifts page indices.

Safety
------
* `--min-len` (default 80) is the shortest run treated as duplication. Prose
  repeats of that length are essentially always stitch artefacts; short repeats
  (headings, "Text-fig. 3", table cells) stay untouched.
* `--dry-run` reports what would be cut without writing.
* A page is left alone if dedup would remove more than `--max-cut-frac` of it
  (default 0.5) — that signals something other than seam duplication.
* Only pages that actually have a cache entry are rewritten; a key whose cache
  is incomplete is refused rather than rebuilt into a truncated markdown.

Usage
-----
    ./.runtime/venv/bin/python scripts/dedupe_recovered_pages.py --key KEY
    ./.runtime/venv/bin/python scripts/dedupe_recovered_pages.py --key KEY --dry-run
    ./.runtime/venv/bin/python scripts/dedupe_recovered_pages.py --key KEY --pages 6,10
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from corpus.config import ROOT
from corpus.keys import key_arg
from corpus.io import atomic_write_text

CACHE_ROOT = ROOT / ".runtime" / "cache" / "vision"
MARKDOWN_DIR = ROOT / "markdown"
PAGE_SEP = "\n\n---\n\n"


def find_longest_repeat(s: str, min_len: int) -> tuple[int, int, int] | None:
    """Return (first_offset, second_offset, length) of the longest repeated run.

    Coarse-to-fine: probe windows of `min_len` on a stride, then extend any hit
    to its full length. Exact longest-repeat via a suffix automaton is overkill
    for single OCR'd pages (a few KB) and this converges to the same cuts.
    """
    n = len(s)
    if n < min_len * 2:
        return None
    best: tuple[int, int, int] | None = None
    stride = max(1, min_len // 4)
    for i in range(0, n - min_len, stride):
        window = s[i : i + min_len]
        j = s.find(window, i + min_len)
        if j == -1:
            continue
        length = min_len
        while i + length < j and j + length < n and s[i + length] == s[j + length]:
            length += 1
        # walk the match backwards too, so a hit found mid-run reports its full span
        start_i, start_j = i, j
        while start_i > 0 and start_j > i + length and s[start_i - 1] == s[start_j - 1]:
            start_i -= 1
            start_j -= 1
            length += 1
        if best is None or length > best[2]:
            best = (start_i, start_j, length)
    return best


def dedupe(text: str, min_len: int) -> tuple[str, list[int]]:
    """Drop the second copy of each repeated run. Returns (text, [cut lengths])."""
    cuts: list[int] = []
    guard = 0
    while guard < 200:
        guard += 1
        hit = find_longest_repeat(text, min_len)
        if hit is None:
            break
        _, second, length = hit
        text = text[:second] + text[second + length :]
        cuts.append(length)
    return text, cuts


def load_cache_pages(key: str) -> dict[int, Path]:
    cache_dir = CACHE_ROOT / key
    if not cache_dir.is_dir():
        sys.exit(f"error: no vision cache at {cache_dir}")
    pages: dict[int, Path] = {}
    for path in cache_dir.glob("page-*.json"):
        stem = path.stem.split("-")[-1]
        if stem.isdigit():
            pages[int(stem)] = path
    if not pages:
        sys.exit(f"error: no page-NNNN.json files under {cache_dir}")
    return pages


def atomic_write(path: Path, data: str) -> None:
    atomic_write_text(path, data)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--key", type=key_arg, required=True, help="citation key")
    ap.add_argument("--pages", help="comma-separated page numbers; default all cached pages")
    ap.add_argument("--min-len", type=int, default=80,
                    help="shortest repeated run treated as duplication (default 80)")
    ap.add_argument("--max-cut-frac", type=float, default=0.5,
                    help="skip a page if dedup would remove more than this fraction (default 0.5)")
    ap.add_argument("--dry-run", action="store_true", help="report only; write nothing")
    args = ap.parse_args()

    key = args.key
    cache_pages = load_cache_pages(key)
    targets = sorted(cache_pages)
    if args.pages:
        want = {int(p) for p in args.pages.split(",") if p.strip()}
        missing = want - set(cache_pages)
        if missing:
            sys.exit(f"error: no cache entry for page(s) {sorted(missing)}")
        targets = sorted(want)

    print(f"=== dedupe_recovered_pages — {key} "
          f"({'DRY RUN' if args.dry_run else 'APPLY'}) ===")
    print(f"cached pages: {len(cache_pages)}  ·  scanning: {len(targets)}  "
          f"·  min-len: {args.min_len}")
    print()

    cleaned: dict[int, str] = {}
    total_cut = 0
    changed_pages = 0

    for pg in targets:
        path = cache_pages[pg]
        blob = json.loads(path.read_text(encoding="utf-8"))
        original = blob.get("markdown", "")
        new_text, cuts = dedupe(original, args.min_len)
        removed = len(original) - len(new_text)

        if not cuts:
            continue

        frac = removed / len(original) if original else 0.0
        if frac > args.max_cut_frac:
            print(f"  page {pg:>3}: SKIPPED — would cut {removed} chars "
                  f"({frac:.0%} > {args.max_cut_frac:.0%}); inspect by hand")
            continue

        changed_pages += 1
        total_cut += removed
        cleaned[pg] = new_text
        runs = ", ".join(str(c) for c in cuts)
        print(f"  page {pg:>3}: {len(original)} → {len(new_text)} chars "
              f"(-{removed}, {frac:.0%})  ·  {len(cuts)} run(s): {runs}")

        if not args.dry_run:
            blob["markdown"] = new_text
            blob["deduped"] = True
            atomic_write(path, json.dumps(blob, ensure_ascii=False, indent=1))

    print()
    if not changed_pages:
        print("No duplication found — nothing to do.")
        return 0

    print(f"pages changed: {changed_pages}  ·  chars removed: {total_cut}")

    if args.dry_run:
        print("\nDry run — no files written. Re-run without --dry-run to apply.")
        return 0

    # Rebuild markdown/{key}.md from the full cleaned cache, mapping by cache
    # filename (never by splitting the old markdown on '---').
    md_path = MARKDOWN_DIR / f"{key}.md"
    parts: list[str] = []
    for pg in sorted(cache_pages):
        if pg in cleaned:
            parts.append(cleaned[pg])
        else:
            blob = json.loads(cache_pages[pg].read_text(encoding="utf-8"))
            parts.append(blob.get("markdown", ""))
    atomic_write(md_path, PAGE_SEP.join(parts).rstrip() + "\n")
    print(f"rebuilt {md_path.relative_to(ROOT)} from {len(cache_pages)} cached pages")
    print("\nNext: re-run scripts/indexer.py")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
