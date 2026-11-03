#!/usr/bin/env python3
# corpus-command: index | build/refresh the FTS5 search index
"""
Step 3: build (or refresh) the FTS5 search index over markdown/{key}.md.

One row per converted markdown file. Metadata fields come from the
manifest's bibtex citation. Tokenizer uses porter stemming +
unicode61 + remove_diacritics 2 so "México" matches "Mexico" and
"Tyrannosaurus" matches inflected forms equally on both query and corpus.

A rebuild (--rebuild, or an index written with an older column list) is
built in a new file that replaces the old one only when complete, so
anything reading the index meanwhile sees the old one, never a partial one.

Usage:
  ./.runtime/venv/bin/python scripts/indexer.py [--rebuild]
"""
from __future__ import annotations

import argparse
import os
import sqlite3
import sys
import time

from corpus.bib import citation_venue
from corpus.supplementary import SUPP_MD_DIR, resolve_parent
from corpus.config import DB, MD_DIR, INDEX_DIR
from corpus.manifest import load as manifest_load
from corpus.keys import key_from_path
from corpus.pages import is_empty as md_is_empty, strip as strip_markers

# New columns go at the end: readers that number columns by position
# (snippet(papers, 7, ...) for the body) keep working.
COLUMNS = ("citation_key", "title", "authors", "year", "journal", "genera",
           "tool", "body", "clades", "translated")
META = COLUMNS[1:7] + COLUMNS[8:]          # everything but the key and body


def current_columns(path) -> tuple[str, ...] | None:
    """The index's column names, or None when there is no index yet."""
    if not path.exists():
        return None
    with sqlite3.connect(path) as con:
        cols = tuple(r[1] for r in con.execute("PRAGMA table_info(papers)"))
    return cols or None


def ensure_schema(con: sqlite3.Connection) -> None:
    con.executescript("""
        CREATE VIRTUAL TABLE IF NOT EXISTS papers USING fts5(
            citation_key UNINDEXED,
            title,
            authors,
            year UNINDEXED,
            journal,
            genera,
            tool UNINDEXED,
            body,
            clades,
            translated UNINDEXED,
            tokenize = 'porter unicode61 remove_diacritics 2'
        );

        -- Sidecar: track per-key body mtime + size so re-runs are incremental.
        CREATE TABLE IF NOT EXISTS index_state (
            citation_key TEXT PRIMARY KEY,
            md_mtime REAL NOT NULL,
            md_size INTEGER NOT NULL,
            indexed_at TEXT NOT NULL
        );
    """)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--rebuild", action="store_true",
                    help="Drop and rebuild the FTS5 table from scratch.")
    args = ap.parse_args()

    INDEX_DIR.mkdir(exist_ok=True)
    cols = current_columns(DB)
    rebuild = args.rebuild or (cols is not None and cols != COLUMNS)
    if rebuild and not args.rebuild:
        print("Index has an older column list: rebuilding.")
    target = DB.with_name(DB.name + ".tmp") if rebuild else DB
    if rebuild:
        target.unlink(missing_ok=True)
    con = sqlite3.connect(target)
    ensure_schema(con)

    manifest = manifest_load()

    state = {row[0]: row for row in con.execute("SELECT citation_key, md_mtime, md_size FROM index_state")}
    # Indexed metadata per key, so a bib refresh that changes only the
    # citation (a title fix, a thesis gaining `school`) is re-indexed even
    # though the markdown's mtime and size are unchanged.
    indexed_meta = {
        row[0]: tuple(row[1:])
        for row in con.execute(f"SELECT citation_key, {', '.join(META)} FROM papers")
    }
    insert = (f"INSERT INTO papers ({', '.join(COLUMNS)}) "
              f"VALUES ({', '.join('?' * len(COLUMNS))})")
    started = time.time()
    n_added = n_updated = n_skipped_unchanged = n_skipped_no_md = 0
    seen: set[str] = set()   # every key whose markdown exists now

    from datetime import datetime, timezone
    now = datetime.now(timezone.utc).isoformat()

    for key, entry in sorted(manifest.items()):
        md = MD_DIR / f"{key}.md"
        if md_is_empty(md):
            n_skipped_no_md += 1
            continue

        seen.add(key)
        cit = entry.get("citation") or {}
        title = (cit.get("title") or "").strip()
        authors = (cit.get("author") or "").strip()
        year = str(cit.get("year") or "").strip()
        journal = citation_venue(cit)
        genera = ", ".join(entry.get("genera") or [])
        tool = (entry.get("conversion") or {}).get("tool") or ""
        clades = ", ".join(entry.get("clades") or [])
        translated = "1" if (MD_DIR / f"{key}.original.md").exists() else ""
        meta = (title, authors, year, journal, genera, tool, clades, translated)

        st = md.stat()
        prev = state.get(key)
        if prev:
            prev_mtime, prev_size = prev[1], prev[2]
            if (abs(prev_mtime - st.st_mtime) < 0.001 and prev_size == st.st_size
                    and indexed_meta.get(key) == meta):
                n_skipped_unchanged += 1
                continue
            # Changed → delete old row before re-insert
            con.execute("DELETE FROM papers WHERE citation_key = ?", (key,))
            n_updated += 1
        else:
            n_added += 1

        body = strip_markers(md.read_text(encoding="utf-8", errors="replace"))

        con.execute(insert, (key, title, authors, year, journal, genera, tool, body,
                             clades, translated))
        con.execute(
            "INSERT OR REPLACE INTO index_state (citation_key, md_mtime, md_size, indexed_at) "
            "VALUES (?,?,?,?)",
            (key, st.st_mtime, st.st_size, now)
        )

    # --- supplementary markdown: markdown/supplementary/{parent}-{name}.md ---
    # Indexed under the {parent}-{name} key, inheriting the parent paper's
    # citation metadata + genera, with a "[suppl: name]" title prefix so search
    # hits are identifiable as supplementary and trace back to the source paper.
    known_parents = set(manifest)
    if SUPP_MD_DIR.exists():
        for md in sorted(SUPP_MD_DIR.glob("*.md")):
            stem = key_from_path(md)
            if md_is_empty(md):
                n_skipped_no_md += 1
                continue
            parsed = resolve_parent(stem, known_parents)
            if parsed is None:
                n_skipped_no_md += 1   # parent gone; skip orphaned supplementary
                continue
            parent, name = parsed
            seen.add(stem)

            pe = manifest[parent]
            cit = pe.get("citation") or {}
            title = f"[suppl: {name}] " + (cit.get("title") or "").strip()
            authors = (cit.get("author") or "").strip()
            year = str(cit.get("year") or "").strip()
            journal = citation_venue(cit)
            genera = ", ".join(pe.get("genera") or [])
            tool = ((pe.get("supplementary") or {}).get(name) or {}).get("tool") or ""
            clades = ", ".join(pe.get("clades") or [])
            meta = (title, authors, year, journal, genera, tool, clades, "")

            st = md.stat()
            prev = state.get(stem)
            if prev:
                if (abs(prev[1] - st.st_mtime) < 0.001 and prev[2] == st.st_size
                        and indexed_meta.get(stem) == meta):
                    n_skipped_unchanged += 1
                    continue
                con.execute("DELETE FROM papers WHERE citation_key = ?", (stem,))
                n_updated += 1
            else:
                n_added += 1

            body = strip_markers(md.read_text(encoding="utf-8", errors="replace"))

            con.execute(insert, (stem, title, authors, year, journal, genera, tool, body,
                                 clades, ""))
            con.execute(
                "INSERT OR REPLACE INTO index_state (citation_key, md_mtime, md_size, indexed_at) "
                "VALUES (?,?,?,?)",
                (stem, st.st_mtime, st.st_size, now)
            )

    # Rows for markdown that is gone (deleted, or a key renamed by a bib
    # split) are removed.
    stale = [k for (k,) in con.execute("SELECT citation_key FROM index_state") if k not in seen]
    stale += [k for (k,) in con.execute("SELECT citation_key FROM papers")
              if k not in seen and k not in stale]
    for k in stale:
        con.execute("DELETE FROM papers WHERE citation_key = ?", (k,))
        con.execute("DELETE FROM index_state WHERE citation_key = ?", (k,))
    con.commit()
    n_total = con.execute("SELECT COUNT(*) FROM papers").fetchone()[0]
    con.close()
    if rebuild:
        os.replace(target, DB)
    db_size_mb = DB.stat().st_size / 1024 / 1024
    elapsed = time.time() - started
    print(f"Done in {elapsed:.1f}s")
    print(f"  added:    {n_added}")
    print(f"  updated:  {n_updated}")
    print(f"  unchanged (skipped): {n_skipped_unchanged}")
    print(f"  no markdown (skipped): {n_skipped_no_md}")
    print(f"  stale rows removed: {len(stale)}")
    print(f"  total rows in index: {n_total}")
    print(f"  index file: {DB} ({db_size_mb:.1f} MB)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
