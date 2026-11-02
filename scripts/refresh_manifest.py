#!/usr/bin/env python3
# corpus-command: manifest refresh | merge the bib (and genus/clade labels) into the manifest
"""
Merge-safe manifest refresh from the open-paleo bib.

`bootstrap.py` rebuilds manifest.json from scratch — it clobbers
`acquisition`, `conversion`, `closed_access`, and any other state we've
built up. Use this script instead when the bib has changed (DOIs added,
new entries, citation metadata fixes) but you want to preserve all the
acquisition/conversion work.

What it does:
  1. Re-reads the bib (CORPUS_BIB_SOURCE; re-downloaded in remote mode) and
     which genera/clades cite each key (same as bootstrap.py).
  2. For each key:
     - If already in manifest: overlay the bib-derived fields
       (citation, in_bib, genera, clades, doi, year) onto the existing
       entry. Acquisition/conversion/closed_access/index untouched.
     - If new in bib/YAMLs: insert a fresh entry with empty acquisition
       and conversion (same shape as bootstrap.py).
  3. For keys *only* in the old manifest (not in current bib/YAMLs):
     keep the entry but set in_bib=False, genera=[], clades=[]. This
     way a paper we've acquired doesn't disappear if its bib entry was
     pruned upstream.
  4. Atomic write. Reports a diff: DOIs added, new keys, dropped-from-bib.

Usage:
  git -C ../data pull          # sibling mode; remote mode fetches by itself
  ./.runtime/venv/bin/python scripts/refresh_manifest.py
"""
from __future__ import annotations

from datetime import datetime, timezone

from corpus.bib import bib_path, citing, parse_bib
from corpus.config import MANIFEST
from corpus.manifest import commit as manifest_commit, load as manifest_load
from corpus.manifest import entry_skeleton
import argparse

def main() -> int:
    # No options: --help prints this and exits instead of starting a run.
    argparse.ArgumentParser(description=__doc__,
                            formatter_class=argparse.RawDescriptionHelpFormatter).parse_args()
    if not MANIFEST.exists():
        print(f"No {MANIFEST.name} yet: building it from the bib.")

    bib_file = bib_path(refresh=True)
    print(f"Reading {bib_file} ...")
    bib = parse_bib(bib_file)
    print(f"  {len(bib)} bib entries")

    cited, _, where = citing(refresh=True)
    print(f"Genus/clade citations from {where}")
    print(f"  {len(cited)} unique cited keys")

    print(f"Reading existing {MANIFEST} ...")
    manifest = manifest_load()
    print(f"  {len(manifest)} existing entries")

    bib_keys = set(bib.keys())
    cited_keys = set(cited.keys())
    new_universe = bib_keys | cited_keys
    old_universe = set(manifest.keys())

    new_keys = sorted(new_universe - old_universe)
    dropped_from_bib = sorted(old_universe - new_universe)
    common = old_universe & new_universe

    # Track what changed for reporting
    doi_added = []
    doi_changed = []
    citation_changed = 0
    genera_changed = 0
    now = datetime.now(timezone.utc).isoformat()

    # 1) Update common keys: overlay bib-derived fields
    for key in common:
        entry = manifest[key]
        bib_entry = bib.get(key, {})
        year_str = bib_entry.get("year") or ""
        try:
            year = int(year_str)
        except ValueError:
            year = None
        new_doi = bib_entry.get("doi")
        old_doi = entry.get("doi")
        if old_doi != new_doi:
            if not old_doi and new_doi:
                doi_added.append((key, new_doi))
            elif old_doi and new_doi:
                doi_changed.append((key, old_doi, new_doi))
        new_citation = bib_entry or None
        if entry.get("citation") != new_citation:
            citation_changed += 1
        new_genera = sorted(cited[key]["genera"]) if key in cited else []
        new_clades = sorted(cited[key]["clades"]) if key in cited else []
        if entry.get("genera") != new_genera or entry.get("clades") != new_clades:
            genera_changed += 1
        # Overlay
        entry["citation"] = new_citation
        entry["in_bib"] = key in bib_keys
        entry["genera"] = new_genera
        entry["clades"] = new_clades
        entry["doi"] = new_doi
        entry["year"] = year
        entry["bootstrapped_at"] = now
        manifest[key] = entry

    # 2) Add fresh entries for new keys
    for key in new_keys:
        bib_entry = bib.get(key, {})
        year_str = bib_entry.get("year") or ""
        try:
            year = int(year_str)
        except ValueError:
            year = None
        entry = entry_skeleton()
        entry["citation"] = bib_entry or None
        entry["in_bib"] = key in bib_keys
        entry["genera"] = sorted(cited[key]["genera"]) if key in cited else []
        entry["clades"] = sorted(cited[key]["clades"]) if key in cited else []
        entry["doi"] = bib_entry.get("doi")
        entry["year"] = year
        entry["bootstrapped_at"] = now
        manifest[key] = entry

    # 3) For dropped keys: keep entry but mark not-in-bib, clear citations.
    #    Do not delete — they may have on-disk PDFs/markdown we don't want
    #    to lose track of.
    for key in dropped_from_bib:
        entry = manifest[key]
        entry["in_bib"] = False
        entry["genera"] = []
        entry["clades"] = []
        # Citation/doi/year stay as last-seen values for reference.
        entry["bootstrapped_at"] = now
        manifest[key] = entry

    manifest_commit(manifest)

    # ---- diff report ----
    print()
    print("=== Refresh summary ===")
    print(f"Total entries:               {len(manifest)}  (was {len(old_universe)})")
    print(f"New entries (bib added):     {len(new_keys)}")
    print(f"Dropped from bib (kept):     {len(dropped_from_bib)}")
    print(f"DOIs newly added:            {len(doi_added)}")
    print(f"DOIs changed:                {len(doi_changed)}")
    print(f"Citation metadata changed:   {citation_changed}")
    print(f"Genera/clades cites changed: {genera_changed}")

    if new_keys:
        print()
        print(f"New keys (showing first 20 of {len(new_keys)}):")
        for k in new_keys[:20]:
            yr = manifest[k].get("year") or "?"
            doi = manifest[k].get("doi") or "(no DOI)"
            print(f"  + {k}  ({yr})  {doi}")

    if doi_added:
        print()
        print(f"DOIs newly added (showing first 20 of {len(doi_added)}):")
        for k, doi in doi_added[:20]:
            print(f"  + {k}  → {doi}")

    if dropped_from_bib:
        print()
        print("Dropped from bib (kept in manifest, in_bib=False):")
        for k in dropped_from_bib:
            acq = manifest[k].get("acquisition") or {}
            src = acq.get("source") or "(no acquisition)"
            print(f"  - {k}  src={src}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
