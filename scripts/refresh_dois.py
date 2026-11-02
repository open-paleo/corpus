#!/usr/bin/env python3
"""
Surgical DOI refresh: pull new DOIs from references.bib into manifest.json
without disturbing acquisition/conversion state.

Use case: the open-paleo repo just got a DOI backfill, and we want our
acquisition tiers to be able to re-attempt the entries that newly have
DOIs. We do NOT want bootstrap.py — that rebuilds the manifest from
scratch and would wipe all acquisition history.

For each citation key in the bib that already exists in our manifest:
  - If bib has a DOI and manifest does not, set entry["doi"]
  - Also refresh entry["citation"] fields in case authors/title got cleaned
  - Leave entry["acquisition"] and entry["conversion"] untouched
  - Leave entry["genera"] / entry["clades"] untouched (computed elsewhere)

Reports: total updated, broken down by what changed.
"""

from __future__ import annotations

import sys
from corpus.manifest import commit as manifest_commit, load as manifest_load
from corpus.bib import bib_path, parse_bib
import argparse

def main() -> int:
    # No options: --help prints this and exits instead of starting a run.
    argparse.ArgumentParser(description=__doc__,
                            formatter_class=argparse.RawDescriptionHelpFormatter).parse_args()
    bib = parse_bib(bib_path(refresh=True))
    manifest = manifest_load()

    n_doi_added = 0
    n_doi_changed = 0
    n_citation_refreshed = 0
    n_unchanged = 0
    new_doi_keys = []

    for key, entry in manifest.items():
        bib_entry = bib.get(key)
        if not bib_entry:
            continue

        # DOI handling
        new_doi = bib_entry.get("doi")
        old_doi = entry.get("doi")
        if new_doi and not old_doi:
            entry["doi"] = new_doi
            n_doi_added += 1
            new_doi_keys.append(key)
        elif new_doi and old_doi and new_doi != old_doi:
            entry["doi"] = new_doi
            n_doi_changed += 1
            new_doi_keys.append(key)

        # Refresh year (in case it changed)
        year_str = bib_entry.get("year") or ""
        try:
            entry["year"] = int(year_str) if year_str.isdigit() else entry.get("year")
        except (TypeError, ValueError):
            pass

        # Replace citation block wholesale (it's the bib row; safe to overwrite)
        # but only if it actually differs, to avoid noisy diffs
        if entry.get("citation") != bib_entry:
            entry["citation"] = bib_entry
            n_citation_refreshed += 1
        else:
            n_unchanged += 1

    manifest_commit(manifest)

    print("DOI refresh complete:")
    print(f"  DOIs newly added (was null in manifest):       {n_doi_added}")
    print(f"  DOIs changed (different value than manifest):  {n_doi_changed}")
    print(f"  Citation blocks refreshed:                     {n_citation_refreshed}")
    print(f"  Untouched:                                     {n_unchanged}")
    print()
    if new_doi_keys:
        print("Keys with new/changed DOIs (next acquisition runs will pick these up):")
        for k in new_doi_keys[:20]:
            print(f"  {k:25s} {manifest[k]['doi']}")
        if len(new_doi_keys) > 20:
            print(f"  ... and {len(new_doi_keys) - 20} more")
    return 0


if __name__ == "__main__":
    sys.exit(main())
