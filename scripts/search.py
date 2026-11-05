#!/usr/bin/env python3
# corpus-command: search | query the search index
"""
Query CLI over the FTS5 index built by index.py.

FTS5 query syntax:
  word                       — match (all words required by default)
  "phrase"                   — exact phrase
  NEAR(word1 word2, 10)      — proximity (within 10 tokens)
  word*                      — prefix
  field:term                 — restrict to field (title, authors, body, journal, genera, clades)
  word1 OR word2             — boolean
  word1 AND NOT word2        — negation

Examples:
  search.py 'NEAR("tail club" ankylosaur, 10)'
  search.py 'title:hadrosaur'
  search.py 'Hylaeosaurus' --year 1833
  search.py --genera Pteropelyx
  search.py ankylosaur --clades Ankylosauria --translated
  search.py --list-keys
  search.py --stats

Usage:
  ./.runtime/venv/bin/python scripts/search.py <FTS5 query> [--limit N]
                                                            [--year YEAR]
                                                            [--key K]
                                                            [--genera GENUS]
                                                            [--clades CLADE]
                                                            [--translated]
                                                            [--full-body]
                                                            [--list-keys]
"""
from __future__ import annotations

import argparse
import sqlite3
import sys
from corpus.config import DB
from corpus.keys import key_arg

def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("query", nargs="?", help="FTS5 query string")
    ap.add_argument("--limit", type=int, default=20)
    ap.add_argument("--year", help="Restrict to this year (or year prefix, e.g. '187*')")
    ap.add_argument("--key", type=key_arg, help="Restrict to this citation_key")
    ap.add_argument("--genera", help="Restrict to a specific genus (substring match)")
    ap.add_argument("--clades", help="Restrict to a specific clade (substring match)")
    ap.add_argument("--translated", action="store_true",
                    help="Only papers translated into English (the original is markdown/{key}.original.md)")
    ap.add_argument("--full-body", action="store_true",
                    help="Print the full body of the first match instead of snippets.")
    ap.add_argument("--list-keys", action="store_true",
                    help="List all indexed citation keys and exit.")
    ap.add_argument("--stats", action="store_true",
                    help="Print index stats and exit.")
    args = ap.parse_args()

    if not DB.exists():
        print(f"ERROR: index not found at {DB}. Run scripts/index.py first.", file=sys.stderr)
        return 2

    con = sqlite3.connect(DB)

    if args.stats:
        n = con.execute("SELECT COUNT(*) FROM papers").fetchone()[0]
        years = con.execute(
            "SELECT MIN(CAST(year AS INTEGER)), MAX(CAST(year AS INTEGER)) FROM papers WHERE year != ''"
        ).fetchone()
        sz = DB.stat().st_size / 1024 / 1024
        n_by_tool = con.execute(
            "SELECT tool, COUNT(*) FROM papers GROUP BY tool ORDER BY COUNT(*) DESC"
        ).fetchall()
        print(f"Index: {DB} ({sz:.1f} MB)")
        print(f"  papers: {n}")
        print(f"  year range: {years[0]} – {years[1]}")
        print("  by conversion tool:")
        for tool, cnt in n_by_tool:
            print(f"    {tool!r}: {cnt}")
        return 0

    if args.list_keys:
        rows = con.execute("SELECT citation_key, year, title FROM papers ORDER BY citation_key").fetchall()
        for k, y, t in rows:
            print(f"{k:30s}  {y:5s}  {t[:80]}")
        return 0

    # Build SQL
    where_clauses = []
    params: list = []
    if args.query:
        where_clauses.append("papers MATCH ?")
        params.append(args.query)
    if args.year:
        if "*" in args.year:
            where_clauses.append("year LIKE ?")
            params.append(args.year.replace("*", "%"))
        else:
            where_clauses.append("year = ?")
            params.append(args.year)
    if args.key:
        where_clauses.append("citation_key = ?")
        params.append(args.key)
    if args.genera:
        where_clauses.append("genera LIKE ?")
        params.append(f"%{args.genera}%")
    if args.clades:
        where_clauses.append("clades LIKE ?")
        params.append(f"%{args.clades}%")
    if args.translated:
        where_clauses.append("translated = '1'")

    if not where_clauses:
        print("ERROR: provide a query, --year, --key, --genera, --clades or --translated "
              "(or --list-keys / --stats)", file=sys.stderr)
        return 2

    select_cols = "citation_key, year, title, authors, journal, genera, tool, clades"
    if args.query:
        # snippet of body field (column index 7 in the FTS5 schema)
        select_cols += ", snippet(papers, 7, '«', '»', '…', 24) AS snip"
        order = "ORDER BY rank"
    else:
        select_cols += ", '' AS snip"
        order = "ORDER BY year, citation_key"

    sql = f"SELECT {select_cols} FROM papers WHERE " + " AND ".join(where_clauses) + f" {order} LIMIT ?"
    params.append(args.limit)

    rows = con.execute(sql, params).fetchall()
    if not rows:
        print("(no matches)")
        return 0

    if args.full_body and rows:
        # Just print the body of the first match
        row = rows[0]
        body = con.execute("SELECT body FROM papers WHERE citation_key = ?", (row[0],)).fetchone()[0]
        print(f"# {row[0]} ({row[1]}) — {row[3]}")
        print(f"# {row[2]}")
        if row[5]:
            print(f"# genera: {row[5]}")
        if row[7]:
            print(f"# clades: {row[7]}")
        print()
        print(body)
        return 0

    print(f"{len(rows)} hit(s):\n")
    for k, y, title, authors, journal, genera, tool, clades, snip in rows:
        first_author = authors.split(";")[0].split(",")[0].strip() if authors else "?"
        print(f"  {k:30s}  {y:5s}  {first_author[:25]:25s}  [{tool or '—'}]")
        if title:
            print(f"    {title[:110]}")
        if genera:
            print(f"    genera: {genera}")
        if clades:
            print(f"    clades: {clades}")
        if snip:
            print(f"    …{snip}…")
        print()

    return 0


if __name__ == "__main__":
    sys.exit(main())
