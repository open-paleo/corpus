#!/usr/bin/env python3
"""
Item-level matcher: for entries we couldn't pin to a BHL Part, find a unique
BHL Item (whole volume) by journal name + year, and save it to the manifest.

Only saves entries where exactly ONE BHL Item matches the citation's
journal + year (±1). Ambiguous (multiple matches) and missing-journal cases
are reported but not auto-saved.

Page-range narrowing happens later in bhl_fetch.py using citation.pages.
bhl_match.py calls corpus.bhl.match_item() for keys with no Part match; run this script
on its own only to retry volume matching. Matches are saved unreviewed:
bhl_fetch.py downloads them only once accepted (--key).
"""

from __future__ import annotations

import argparse
import sys
from collections import Counter

from corpus.bhl import connect, match_item
from corpus.config import PDF_DIR
from corpus.manifest import commit as manifest_commit, load as manifest_load


def main() -> int:
    """Volume matching on its own; bhl_match.py runs it as its fallback."""
    # No options: --help prints this and exits instead of starting a run.
    argparse.ArgumentParser(description=__doc__,
                            formatter_class=argparse.RawDescriptionHelpFormatter).parse_args()
    manifest = manifest_load()
    c = connect().cursor()

    candidates = [k for k, e in manifest.items()
                  if e.get("in_bib") and not (PDF_DIR / f"{k}.pdf").exists()
                  and not e.get("bhl_match")]
    print(f"Candidates for Item-level match: {len(candidates)}")
    print()

    counts = Counter()
    saved = 0
    for k in candidates:
        e = manifest[k]
        match, outcome = match_item(c, e)
        counts[outcome] += 1
        if match:
            match["reviewed"] = False
            e["bhl_match"] = match
            saved += 1
            pages = (e.get("citation") or {}).get("pages") or "(no pages)"
            print(f"  {k:25s} → ItemID={match['item_id']} ({match['barcode']}) pages={pages}")

    manifest_commit(manifest)
    print()
    print("Item-level match summary:")
    for k, v in counts.most_common():
        print(f"  {k:25s} {v}")
    print(f"\nSaved {saved} new Item-level matches to manifest.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
