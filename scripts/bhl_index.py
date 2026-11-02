#!/usr/bin/env python3
# corpus-command: bhl index | build the BHL SQLite index
"""
Build a SQLite index from BHL TSV dumps so we can match our citations to
PartIDs locally (no API, no rate limits).

Reads the gzipped TSVs in .runtime/bhl_dumps/, writes .runtime/bhl_dumps/bhl.db with:
  - part           one row per article
  - partpage       PartID → PageID list (also has ItemID, SequenceOrder)
  - partcreator    PartID → CreatorName (denormalized, no creator join needed)
  - doi            EntityType/EntityID → DOI (covers Titles, Items, Parts, Pages)
  - item           ItemID → BarCode + TitleID + ItemPDFURL
  - title          journal/book metadata
  - part_fts       FTS5 over Title + ContainerTitle + author concat for fuzzy match

Schema mirrors the actual BHL TSV column names verified by `gzcat | head -1`.
Header row is read at runtime, so BHL adding new columns won't break us.
"""

from __future__ import annotations

import csv
import gzip
import re
import sqlite3
import sys
import time
from pathlib import Path
import argparse

from corpus.bhl import DB, DUMPS


def _strip_nul(it):
    for line in it:
        yield line.replace("\x00", "")


def open_tsv(path: Path):
    f = gzip.open(path, "rt", encoding="utf-8-sig", errors="replace", newline="")
    return f, csv.DictReader(_strip_nul(f), dialect="excel-tab")


def parse_year(date_str: str | None) -> int | None:
    if not date_str:
        return None
    m = re.search(r"\b(1[6-9]\d{2}|20\d{2})\b", date_str)
    return int(m.group(1)) if m else None


def parse_page_range(rng: str | None) -> tuple[str | None, str | None]:
    if not rng:
        return None, None
    parts = re.split(r"\s*--\s*|\s*-\s*", rng.strip(), maxsplit=1)
    if len(parts) == 2:
        return parts[0] or None, parts[1] or None
    return parts[0] or None, None


def g(row: dict, name: str) -> str | None:
    v = row.get(name)
    return v.strip() if isinstance(v, str) and v.strip() else None


def load_part(c, conn) -> int:
    print("Loading part.txt.gz ...", flush=True)
    f, r = open_tsv(DUMPS / "part.txt.gz")
    rows = []
    for row in r:
        try:
            pid = int(g(row, "PartID") or 0)
            if not pid:
                continue
        except ValueError:
            continue
        iid_raw = g(row, "ItemID")
        iid = int(iid_raw) if iid_raw and iid_raw.isdigit() else None
        date = g(row, "Date")
        start_pg, end_pg = parse_page_range(g(row, "PageRange"))
        start_pgid = g(row, "StartPageID")
        try:
            start_pgid_int = int(start_pgid) if start_pgid and start_pgid.isdigit() else None
        except ValueError:
            start_pgid_int = None
        rows.append((
            pid,
            iid,
            g(row, "Title"),
            g(row, "ContainerTitle"),
            g(row, "Volume"),
            g(row, "Issue"),
            date,
            parse_year(date),
            start_pg,
            end_pg,
            start_pgid_int,
            g(row, "Genre"),
            g(row, "SegmentType"),
            g(row, "LanguageName"),
            g(row, "SegmentUrl"),
            g(row, "DownloadUrl"),
            g(row, "BarCode"),
        ))
    f.close()
    c.executemany(
        "INSERT OR IGNORE INTO part VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", rows
    )
    conn.commit()
    print(f"  {len(rows):,} parts")
    return len(rows)


def load_partpage(c, conn) -> int:
    print("Loading partpage.txt.gz ...", flush=True)
    f, r = open_tsv(DUMPS / "partpage.txt.gz")
    rows = []
    for row in r:
        try:
            rows.append((
                int(g(row, "PartID") or 0),
                int(g(row, "PageID") or 0),
                int(g(row, "ItemID") or 0) if (g(row, "ItemID") or "").isdigit() else None,
                int(g(row, "SequenceOrder") or 0) if (g(row, "SequenceOrder") or "").isdigit() else None,
            ))
        except ValueError:
            continue
    f.close()
    c.executemany("INSERT INTO partpage VALUES (?,?,?,?)", rows)
    conn.commit()
    print(f"  {len(rows):,} partpage rows")
    return len(rows)


def load_doi(c, conn) -> int:
    print("Loading doi.txt.gz ...", flush=True)
    f, r = open_tsv(DUMPS / "doi.txt.gz")
    rows = []
    for row in r:
        et = g(row, "EntityType")
        eid = g(row, "EntityID")
        doi = g(row, "DOI")
        if not (et and eid and doi):
            continue
        try:
            rows.append((et, int(eid), doi, doi.lower()))
        except ValueError:
            continue
    f.close()
    c.executemany("INSERT INTO doi VALUES (?,?,?,?)", rows)
    conn.commit()
    print(f"  {len(rows):,} DOI rows")
    return len(rows)


def load_partidentifier(c, conn) -> int:
    print("Loading partidentifier.txt.gz ...", flush=True)
    f, r = open_tsv(DUMPS / "partidentifier.txt.gz")
    rows = []
    for row in r:
        try:
            pid = int(g(row, "PartID") or 0)
        except ValueError:
            continue
        name = g(row, "IdentifierName")
        value = g(row, "IdentifierValue")
        if pid and name and value:
            rows.append((pid, name, value, value.lower()))
    f.close()
    c.executemany("INSERT INTO partidentifier VALUES (?,?,?,?)", rows)
    conn.commit()
    print(f"  {len(rows):,} part identifiers")
    return len(rows)


def load_partcreator(c, conn) -> int:
    print("Loading partcreator.txt.gz ...", flush=True)
    f, r = open_tsv(DUMPS / "partcreator.txt.gz")
    rows = []
    for row in r:
        try:
            pid = int(g(row, "PartID") or 0)
            cid = int(g(row, "CreatorID") or 0)
        except ValueError:
            continue
        name = g(row, "CreatorName")
        if pid and name:
            rows.append((pid, cid, name))
    f.close()
    c.executemany("INSERT INTO partcreator VALUES (?,?,?)", rows)
    conn.commit()
    print(f"  {len(rows):,} partcreator rows")
    return len(rows)


def load_item(c, conn) -> int:
    print("Loading item.txt.gz ...", flush=True)
    f, r = open_tsv(DUMPS / "item.txt.gz")
    rows = []
    for row in r:
        try:
            iid = int(g(row, "ItemID") or 0)
        except ValueError:
            continue
        if not iid:
            continue
        tid_raw = g(row, "TitleID")
        tid = int(tid_raw) if tid_raw and tid_raw.isdigit() else None
        rows.append((
            iid,
            g(row, "BarCode"),
            tid,
            g(row, "Year"),
            g(row, "VolumeInfo"),
            g(row, "ItemPDFURL"),
        ))
    f.close()
    c.executemany("INSERT OR IGNORE INTO item VALUES (?,?,?,?,?,?)", rows)
    conn.commit()
    print(f"  {len(rows):,} items")
    return len(rows)


def load_title(c, conn) -> int:
    print("Loading title.txt.gz ...", flush=True)
    f, r = open_tsv(DUMPS / "title.txt.gz")
    rows = []
    for row in r:
        try:
            tid = int(g(row, "TitleID") or 0)
        except ValueError:
            continue
        if tid:
            rows.append((
                tid,
                g(row, "FullTitle"),
                g(row, "ShortTitle"),
                g(row, "LanguageCode"),
                g(row, "StartYear"),
                g(row, "EndYear"),
            ))
    f.close()
    c.executemany("INSERT OR IGNORE INTO title VALUES (?,?,?,?,?,?)", rows)
    conn.commit()
    print(f"  {len(rows):,} titles")
    return len(rows)


def build_fts(c, conn) -> None:
    print("Building FTS index over part.Title + ContainerTitle + author concat ...", flush=True)
    c.execute(
        """
        INSERT INTO part_fts(rowid, Title, ContainerTitle, AuthorsConcat)
        SELECT
            p.PartID,
            COALESCE(p.Title, ''),
            COALESCE(p.ContainerTitle, ''),
            COALESCE(
                (SELECT GROUP_CONCAT(pc.CreatorName, '; ')
                 FROM partcreator pc
                 WHERE pc.PartID = p.PartID),
                ''
            )
        FROM part p
        """
    )
    conn.commit()


def main() -> int:
    # No options: --help prints this and exits instead of starting a run.
    argparse.ArgumentParser(description=__doc__,
                            formatter_class=argparse.RawDescriptionHelpFormatter).parse_args()
    if not (DUMPS / "part.txt.gz").exists():
        print(f"ERROR: {DUMPS}/part.txt.gz not found. Run scripts/bhl_setup.py first.", file=sys.stderr)
        return 2
    if DB.exists():
        print(f"Removing existing {DB}")
        DB.unlink()

    conn = sqlite3.connect(DB)
    c = conn.cursor()
    c.executescript("""
        PRAGMA journal_mode = WAL;

        CREATE TABLE part (
            PartID INTEGER PRIMARY KEY,
            ItemID INTEGER,
            Title TEXT,
            ContainerTitle TEXT,
            Volume TEXT,
            Issue TEXT,
            Date TEXT,
            Year INTEGER,
            StartPage TEXT,
            EndPage TEXT,
            StartPageID INTEGER,
            Genre TEXT,
            SegmentType TEXT,
            LanguageName TEXT,
            SegmentUrl TEXT,
            DownloadUrl TEXT,
            BarCode TEXT
        );
        CREATE INDEX ix_part_year ON part(Year);
        CREATE INDEX ix_part_item ON part(ItemID);

        CREATE TABLE partpage (
            PartID INTEGER,
            PageID INTEGER,
            ItemID INTEGER,
            SequenceOrder INTEGER
        );
        CREATE INDEX ix_partpage_part ON partpage(PartID);
        CREATE INDEX ix_partpage_item ON partpage(ItemID);

        CREATE TABLE doi (
            EntityType TEXT,
            EntityID INTEGER,
            DOI TEXT,
            DOILower TEXT
        );
        CREATE INDEX ix_doi_lower ON doi(DOILower);
        CREATE INDEX ix_doi_entity ON doi(EntityType, EntityID);

        CREATE TABLE partidentifier (
            PartID INTEGER,
            IdentifierName TEXT,
            IdentifierValue TEXT,
            IdentifierValueLower TEXT
        );
        CREATE INDEX ix_partid_value ON partidentifier(IdentifierValueLower);

        CREATE TABLE partcreator (
            PartID INTEGER,
            CreatorID INTEGER,
            CreatorName TEXT
        );
        CREATE INDEX ix_partcreator_part ON partcreator(PartID);

        CREATE TABLE item (
            ItemID INTEGER PRIMARY KEY,
            BarCode TEXT,
            TitleID INTEGER,
            Year TEXT,
            VolumeInfo TEXT,
            ItemPDFURL TEXT
        );
        CREATE INDEX ix_item_title ON item(TitleID);

        CREATE TABLE title (
            TitleID INTEGER PRIMARY KEY,
            FullTitle TEXT,
            ShortTitle TEXT,
            LanguageCode TEXT,
            StartYear TEXT,
            EndYear TEXT
        );

        CREATE VIRTUAL TABLE part_fts USING fts5(
            Title, ContainerTitle, AuthorsConcat,
            tokenize='porter unicode61 remove_diacritics 2'
        );
    """)
    conn.commit()

    t0 = time.time()
    load_part(c, conn)
    load_partpage(c, conn)
    load_doi(c, conn)
    load_partidentifier(c, conn)
    load_partcreator(c, conn)
    load_item(c, conn)
    load_title(c, conn)
    build_fts(c, conn)

    c.execute("ANALYZE")
    conn.commit()
    print(f"\nIndex built in {time.time() - t0:.1f}s → {DB} ({DB.stat().st_size/1024/1024:.0f} MB)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
