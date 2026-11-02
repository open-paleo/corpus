#!/usr/bin/env python3
"""
Item-level matcher: for entries we couldn't pin to a BHL Part, find a unique
BHL Item (whole volume) by journal name + year, and save it to the manifest.

Only saves entries where exactly ONE BHL Item matches the citation's
journal + year (±1). Ambiguous (multiple matches) and missing-journal cases
are reported but not auto-saved.

Page-range narrowing happens later in bhl_fetch.py using citation.pages.
bhl_match.py calls match_item() for keys with no Part match; run this script
on its own only to retry volume matching. Matches are saved unreviewed:
bhl_fetch.py downloads them only once accepted (--key).
"""

from __future__ import annotations

import re
import sqlite3
import sys
from collections import Counter
from corpus.config import PDF_DIR, RUNTIME
from corpus.manifest import commit as manifest_commit, load as manifest_load
import argparse

DB = RUNTIME / "bhl_dumps" / "bhl.db"

STOPWORDS = set("a an and the of in on at to for from with by about as is are "
                "journal proceedings bulletin transactions annals memoirs annal".split())

YEAR_TOLERANCE = 1


def normalize(s: str | None) -> list[str]:
    if not s:
        return []
    s = re.sub(r"[^\w\s]", " ", s.lower())
    return [w for w in s.split() if len(w) > 2 and w not in STOPWORDS and not w.isdigit()]


def find_titles_for_journal(c, journal: str, max_results: int = 20,
                            min_jaccard: float = 0.5):
    """
    Find BHL titles whose words substantially overlap with our journal name.
    Uses LIKE for the SQL filter, then scores by Jaccard token similarity to
    reject monographs that just happen to contain the same key words.
    """
    qwords = set(normalize(journal))
    if not qwords:
        return []
    # Pick the longest 4 words as LIKE filters (most distinctive)
    like_words = sorted(qwords, key=len, reverse=True)[:4]
    where = " AND ".join(["LOWER(FullTitle) LIKE ?"] * len(like_words))
    params = tuple(f"%{w}%" for w in like_words)
    rows = c.execute(
        f"SELECT TitleID, FullTitle FROM title WHERE {where} LIMIT 200",
        params,
    ).fetchall()

    scored = []
    for tid, full_title in rows:
        twords = set(normalize(full_title))
        if not twords:
            continue
        inter = qwords & twords
        union = qwords | twords
        jacc = len(inter) / len(union) if union else 0
        # Also require: at least 70% of OUR words appear in the title
        coverage = len(inter) / len(qwords)
        if jacc >= min_jaccard and coverage >= 0.7:
            scored.append((jacc, tid, full_title))
    scored.sort(reverse=True)
    return [(tid, ft) for _j, tid, ft in scored[:max_results]]


def items_for_title_in_year(c, title_id: int, year: int, volume: str | None = None):
    """
    Return items linked to title_id whose Year matches (±tolerance). If
    `volume` is given, also require it to appear in the item's VolumeInfo
    (e.g., '24' matches 'v.24 (1908)').
    """
    rows = c.execute(
        "SELECT ItemID, Year, VolumeInfo, BarCode FROM item WHERE TitleID=?", (title_id,)
    ).fetchall()
    out = []
    for iid, y, vol_info, bc in rows:
        if not y:
            continue
        m = re.search(r"\b(1[6-9]\d{2}|20\d{2})\b", y)
        if not m:
            continue
        item_year = int(m.group(1))
        if abs(item_year - year) > YEAR_TOLERANCE:
            continue
        if volume and vol_info:
            # Look for "v.24" or "vol. 24" or just "24" as a delimited token
            if not re.search(rf"\bv?o?l?\.?\s*{re.escape(volume)}\b", vol_info, re.IGNORECASE):
                continue
        out.append({"item_id": iid, "year": item_year, "volume": vol_info, "barcode": bc})
    return out


def match_item(c, entry: dict) -> tuple[dict | None, str]:
    """(bhl_match dict or None, outcome) for one manifest entry: the unique BHL
    Item (volume) for its journal + year (+ volume when cited)."""
    cite = entry.get("citation") or {}
    journal = cite.get("journal") or cite.get("booktitle") or ""
    year = entry.get("year")
    if not journal or not year:
        return None, "no_journal_or_year"
    titles = find_titles_for_journal(c, journal)
    if not titles:
        return None, "title_not_in_bhl"
    cite_volume = (cite.get("volume") or "").strip() or None
    items = []
    for tid, full_title in titles:
        for it in items_for_title_in_year(c, tid, year, volume=cite_volume):
            it["title_id"] = tid
            it["full_title"] = full_title
            items.append(it)
    if not items:
        return None, "title_no_year_match"
    if len(items) > 1:
        return None, "item_ambiguous"
    it = items[0]
    return {
        "method": "item_unique",
        "item_id": it["item_id"],
        "title_id": it["title_id"],
        "barcode": it["barcode"],
        "volume": it["volume"],
        "year_in_bhl": it["year"],
        "title_in_bhl": it["full_title"],
        "score": 5.0,  # less confident than fuzzy part match
    }, "item_unique"


def main() -> int:
    # No options: --help prints this and exits instead of starting a run.
    argparse.ArgumentParser(description=__doc__,
                            formatter_class=argparse.RawDescriptionHelpFormatter).parse_args()
    """Volume matching on its own; bhl_match.py runs it as its fallback."""
    manifest = manifest_load()
    conn = sqlite3.connect(DB)
    c = conn.cursor()

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
