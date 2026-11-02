"""The Biodiversity Heritage Library tier's shared pieces.

Where its data lives (DUMPS, DB), the volume (Item) matcher used by
bhl_match.py and bhl_item_match.py, and the JP2 stitch used by bhl_fetch.py
and stitch_bhl_pdfs.py. Scripts: bhl_setup, bhl_index, bhl_match,
bhl_item_match, bhl_fetch, stitch_bhl_pdfs.
"""
from __future__ import annotations

import re
import sqlite3
from pathlib import Path

from corpus.config import RUNTIME
from corpus.pdf.build import images_to_pdf

DUMPS = RUNTIME / "bhl_dumps"
DB = DUMPS / "bhl.db"


def connect(readonly: bool = False) -> sqlite3.Connection:
    """The local BHL index, or exit naming the two setup scripts."""
    if not DB.exists():
        raise SystemExit(f"{DB} not found: run scripts/bhl_setup.py, then scripts/bhl_index.py")
    if readonly:
        return sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    return sqlite3.connect(DB)


def stitch_jp2s(jp2_dir: Path, pdf_path: Path) -> tuple[int, int]:
    """Stitch jp2_dir/page-*.jp2 (lossless) into pdf_path: (pages, bytes)."""
    jp2_files = sorted(Path(jp2_dir).glob("page-*.jp2"))
    if not jp2_files:
        return 0, 0
    return len(jp2_files), images_to_pdf(jp2_files, pdf_path)


# ---- the volume (Item) matcher ---------------------------------------------

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
