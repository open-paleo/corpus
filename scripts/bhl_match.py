#!/usr/bin/env python3
# corpus-command: bhl match | match missing keys to BHL articles or volumes (prints a review table)
"""
Match manifest entries → BHL PartIDs using the local SQLite index.

Strategy per entry (every bib key with no PDF on disk and no match yet):
  1. DOI exact match → Part            (cleanest, but rare for paleo refs)
  2. DOI exact match → Title           (ambiguous; logged for now, not auto-matched)
  3. Fuzzy: FTS5 over Title + Author   (the main path)
       - Score = year_match*3 + author_match*3 + title_overlap_ratio*5
       - Accept best candidate if score >= MIN_SCORE
       - Otherwise log as "ambiguous" with top 3 candidates
  4. No Part match: the unique Item (volume) for the journal + year
     (bhl_item_match.match_item), method "item_unique"

Every match is saved with "reviewed": false except doi_part (true): a fuzzy
or volume match can be the wrong paper or a whole volume, so bhl_fetch.py
downloads it only once accepted by naming its key. The run ends with a
review table: our title against BHL's, year and score.

Writes match results back into manifest under entry["bhl_match"]:
  {
    "method": "doi_part" | "fuzzy",
    "part_id": int,
    "item_id": int,
    "score": float,
    "title_in_bhl": str,
    "year_in_bhl": int
  }

Run: python3 bhl_match.py [--key K ...] [--limit N] [--dry-run]
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter
from corpus.bhl import connect, match_item
from corpus.config import PDF_DIR, RUNTIME
from corpus.keys import add_key_args, resolve_keys
from corpus.manifest import commit as manifest_commit, load as manifest_load

LOG = RUNTIME / "logs" / "bhl_match.jsonl"

MIN_SCORE = 7.0           # accept threshold
YEAR_TOLERANCE = 2        # ± years
TOP_N = 8                 # FTS candidates to score

STOPWORDS = set("a an and the of in on at to for from with by about as is are was were be been being new note notes paper "
                "n nov novo nova spec species sp gen genus n.gen n.sp n.g new genus type new york london journal vol "
                "first second third part i ii iii".split())


def normalize_words(s: str | None) -> list[str]:
    if not s:
        return []
    s = re.sub(r"[^\w\s]", " ", s.lower())
    return [w for w in s.split() if len(w) > 2 and w not in STOPWORDS and not w.isdigit()]


def fts_query(words: list[str], max_terms: int = 6) -> str:
    """Build a forgiving FTS5 query — OR over significant title terms."""
    # Longest first, ties alphabetical: sorting on length alone left equal-
    # length words in set order, which Python randomises per run, so the
    # six-term cut (and the match) could differ between two runs.
    terms = sorted(set(words), key=lambda w: (-len(w), w))[:max_terms]
    return " OR ".join(f'"{t}"' for t in terms) if terms else ""


def first_author_surname(author_str: str | None) -> str | None:
    """Best-effort surname extraction from a bibtex author field."""
    if not author_str:
        return None
    first = author_str.split(";")[0].split(" and ")[0].strip()
    # "Surname, First Middle" form
    if "," in first:
        return first.split(",")[0].strip().lower()
    # "First Middle Surname" form
    parts = first.split()
    return parts[-1].lower() if parts else None


def score_candidate(query_year: int | None, query_surname: str | None,
                    query_words: set[str], cand: dict) -> tuple[float, dict]:
    breakdown = {}
    s = 0.0

    cand_year = cand["Year"]
    if query_year and cand_year:
        diff = abs(int(cand_year) - int(query_year))
        if diff == 0:
            s += 3.0
            breakdown["year"] = "exact"
        elif diff <= YEAR_TOLERANCE:
            s += 1.5
            breakdown["year"] = f"±{diff}"
        else:
            s -= 1.0
            breakdown["year"] = f"off by {diff}"

    if query_surname and cand["AuthorsConcat"]:
        if query_surname in cand["AuthorsConcat"].lower():
            s += 3.0
            breakdown["author"] = "match"

    cand_words = set(normalize_words(cand["Title"]))
    if query_words and cand_words:
        overlap = query_words & cand_words
        ratio = len(overlap) / max(len(query_words), 1)
        s += ratio * 5.0
        breakdown["title_overlap"] = f"{len(overlap)}/{len(query_words)} ({ratio:.0%})"

    return s, breakdown


def lookup_doi_in_bhl(c, doi: str) -> tuple[str, int] | None:
    r = c.execute(
        "SELECT EntityType, EntityID FROM doi WHERE DOILower=?",
        (doi.lower(),),
    ).fetchone()
    return tuple(r) if r else None


def fuzzy_match(c, query_year, query_surname, query_words):
    fts_q = fts_query(query_words + ([query_surname] if query_surname else []))
    if not fts_q:
        return []
    rows = c.execute(
        """
        SELECT p.PartID, p.ItemID, p.Title, p.ContainerTitle, p.Year,
               (SELECT GROUP_CONCAT(pc.CreatorName, '; ')
                FROM partcreator pc WHERE pc.PartID = p.PartID) AS AuthorsConcat
        FROM part_fts fts
        JOIN part p ON p.PartID = fts.rowid
        WHERE part_fts MATCH ?
        ORDER BY rank
        LIMIT ?
        """,
        (fts_q, TOP_N),
    ).fetchall()
    return [
        {
            "PartID": r[0], "ItemID": r[1], "Title": r[2], "ContainerTitle": r[3],
            "Year": r[4], "AuthorsConcat": r[5] or "",
        }
        for r in rows
    ]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--dry-run", action="store_true")
    add_key_args(ap)
    args = ap.parse_args()
    only = set(resolve_keys(args)) or None

    conn = connect()
    manifest = manifest_load()
    c = conn.cursor()

    # Eligible: in the bib, no PDF on disk, not matched before (or named).
    candidates = [k for k, e in manifest.items()
                  if (only is None and e.get("in_bib") and not e.get("bhl_match")
                      or only is not None and k in only)
                  and not (PDF_DIR / f"{k}.pdf").exists()]

    if args.limit:
        candidates = candidates[: args.limit]

    print(f"BHL match candidates (no PDF, not matched yet): {len(candidates)}")
    print(f"Min score for auto-accept: {MIN_SCORE}, year tolerance: ±{YEAR_TOLERANCE}")
    print()

    LOG.parent.mkdir(parents=True, exist_ok=True)
    log_fh = LOG.open("w")
    counts = Counter()

    for i, k in enumerate(candidates, 1):
        e = manifest[k]
        cite = e.get("citation") or {}
        title = cite.get("title") or ""
        author_str = cite.get("author") or ""
        year = e.get("year")
        doi = (e.get("doi") or "").strip()

        rec = {"key": k, "year": year, "title": title[:90], "doi": doi}

        # 1. DOI direct match
        if doi:
            hit = lookup_doi_in_bhl(c, doi)
            if hit:
                etype, eid = hit
                if etype == "Part":
                    item_row = c.execute(
                        "SELECT ItemID, Title FROM part WHERE PartID=?", (eid,)
                    ).fetchone()
                    e["bhl_match"] = {
                        "method": "doi_part",
                        "part_id": eid,
                        "item_id": item_row[0] if item_row else None,
                        "score": 99.0,
                        "title_in_bhl": item_row[1] if item_row else None,
                        "year_in_bhl": year,
                        "reviewed": True,   # the DOI identifies the article
                    }
                    counts["doi_part"] += 1
                    rec["outcome"] = "doi_part"
                    rec["match"] = e["bhl_match"]
                    log_fh.write(json.dumps(rec) + "\n")
                    if i <= 20 or i % 100 == 0:
                        print(f"[{i:>4}/{len(candidates)}] {k:25s} doi_part → PartID={eid}")
                    continue
                else:
                    rec["doi_in_bhl_at"] = f"{etype}:{eid}"  # logged but not auto-matched

        # 2. Fuzzy match
        query_words = set(normalize_words(title))
        query_surname = first_author_surname(author_str)
        if not query_words:
            counts["no_query"] += 1
            rec["outcome"] = "no_query"
            log_fh.write(json.dumps(rec) + "\n")
            continue

        candidates_bhl = fuzzy_match(c, year, query_surname, list(query_words))
        scored = []
        for cand in candidates_bhl:
            s, br = score_candidate(year, query_surname, query_words, cand)
            scored.append((s, cand, br))
        scored.sort(key=lambda x: -x[0])

        if not scored:
            item, outcome = match_item(c, e)
            if item:
                item["reviewed"] = False
                e["bhl_match"] = item
                rec["match"] = item
            counts[outcome if item else "no_candidates"] += 1
            rec["outcome"] = outcome if item else "no_candidates"
            log_fh.write(json.dumps(rec) + "\n")
            continue

        best_score, best, best_br = scored[0]
        rec["top_candidates"] = [
            {"PartID": s[1]["PartID"], "Title": (s[1]["Title"] or "")[:90],
             "Year": s[1]["Year"], "Authors": (s[1]["AuthorsConcat"] or "")[:60],
             "score": round(s[0], 2), "breakdown": s[2]}
            for s in scored[:3]
        ]

        if best_score >= MIN_SCORE:
            e["bhl_match"] = {
                "method": "fuzzy",
                "part_id": best["PartID"],
                "item_id": best["ItemID"],
                "score": round(best_score, 2),
                "title_in_bhl": best["Title"],
                "year_in_bhl": best["Year"],
                "reviewed": False,
            }
            counts["fuzzy"] += 1
            rec["outcome"] = "fuzzy"
            rec["match"] = e["bhl_match"]
            if i <= 20 or i % 100 == 0:
                print(f"[{i:>4}/{len(candidates)}] {k:25s} fuzzy  score={best_score:.1f} → PartID={best['PartID']}")
        else:
            rec["best_score"] = round(best_score, 2)
            item, outcome = match_item(c, e)
            if item:
                item["reviewed"] = False
                e["bhl_match"] = item
                rec["match"] = item
            counts[outcome if item else "below_threshold"] += 1
            rec["outcome"] = outcome if item else "below_threshold"
            if i <= 20 or i % 100 == 0:
                print(f"[{i:>4}/{len(candidates)}] {k:25s} {rec['outcome']}  best={best_score:.1f}")

        log_fh.write(json.dumps(rec) + "\n")

        if i % 100 == 0 and not args.dry_run:
            manifest_commit(manifest)

    if not args.dry_run:
        manifest_commit(manifest)
    log_fh.close()

    print()
    print("BHL match summary:")
    for k, v in counts.most_common():
        print(f"  {k:20s} {v}")

    pending = [k for k in candidates if (manifest[k].get("bhl_match") or {}).get("reviewed") is False]
    if pending:
        print(f"\n{len(pending)} match(es) to review before fetching. Accept one by fetching it:"
              "\n  scripts/bhl_fetch.py --key KEY   (doi_part matches are accepted already)\n")
        for k in pending:
            e, m = manifest[k], manifest[k]["bhl_match"]
            ours = ((e.get("citation") or {}).get("title") or "")[:70]
            print(f"  {k}  [{m['method']}, score {m.get('score')}, {e.get('year')} vs "
                  f"{m.get('year_in_bhl')}]\n      ours: {ours}\n      BHL:  "
                  f"{(m.get('title_in_bhl') or '')[:70]}"
                  + (f"  ({m.get('volume')})" if m.get("volume") else ""))
    return 0


if __name__ == "__main__":
    sys.exit(main())
