#!/usr/bin/env python3
"""
Initial build: one manifest.json entry per references.bib key. CLOBBERS any
existing manifest (acquisition and conversion state included); after the first
build use refresh_manifest.py.

Reads:  the bib from CORPUS_BIB_SOURCE (corpus.bib.bib_path: the data repo
        beside this one, a bundled copy, or a download), and which genera and
        clades cite each key from the data repo's YAMLs or dist/open-paleo.json
        (corpus.bib.citing; labels only, never a filter)
Writes: <repo>/manifest.json (atomic via .tmp+rename)
        <repo>/.runtime/logs/bootstrap_summary.txt
"""

import sys
from collections import Counter
from datetime import datetime, timezone

from corpus.bib import (  # noqa: F401  (citation_venue/is_thesis re-exported)
    bib_path, citation_venue, citing, is_thesis, parse_bib,
)
from corpus.manifest import save as manifest_save
from corpus.config import MANIFEST, RUNTIME
from corpus.manifest import entry_skeleton
from corpus.io import atomic_write_text
import argparse
from corpus.manifest import load as manifest_load

SUMMARY = RUNTIME / "logs" / "bootstrap_summary.txt"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--force", action="store_true",
                    help="overwrite a manifest.json that already has entries")
    args = ap.parse_args()
    if MANIFEST.exists() and manifest_load(track=False) and not args.force:
        print(f"{MANIFEST} already has entries; bootstrap.py would overwrite their "
              "acquisition and conversion state. Use refresh_manifest.py, or --force.",
              file=sys.stderr)
        return 2
    bib_file = bib_path(refresh=True)
    print(f"reading {bib_file} ...")
    bib = parse_bib(bib_file)
    print(f"  parsed {len(bib)} bib entries")

    cited, no_refs, where = citing(refresh=True)
    print(f"genus/clade citations from {where}")
    print(f"  total unique cited keys (genera+clades): {len(cited)}")

    cited_keys = set(cited.keys())
    bib_keys = set(bib.keys())
    in_bib_only = sorted(bib_keys - cited_keys)
    in_yaml_only = sorted(cited_keys - bib_keys)

    now = datetime.now(timezone.utc).isoformat()

    manifest = {}
    for key in sorted(bib_keys | cited_keys):
        bib_entry = bib.get(key, {})
        year_str = bib_entry.get("year") or ""
        try:
            year = int(year_str)
        except ValueError:
            year = None

        manifest[key] = entry_skeleton(
            citation=bib_entry or None,
            in_bib=key in bib_keys,
            genera=sorted(cited[key]["genera"]) if key in cited else [],
            clades=sorted(cited[key]["clades"]) if key in cited else [],
            doi=bib_entry.get("doi"),
            year=year,
            bootstrapped_at=now,
        )

    manifest_save(manifest)

    # ---- summary ----
    n_total = len(manifest)
    n_with_doi = sum(1 for e in manifest.values() if e["doi"])
    n_in_bib = sum(1 for e in manifest.values() if e["in_bib"])
    n_cited = sum(1 for e in manifest.values() if e["genera"] or e["clades"])
    n_orphan_in_bib = sum(1 for e in manifest.values() if e["in_bib"] and not (e["genera"] or e["clades"]))
    n_missing_from_bib = len(in_yaml_only)

    years = [e["year"] for e in manifest.values() if e["year"]]
    decades = Counter((y // 10) * 10 for y in years)

    lines = []
    lines.append("Open Paleo paper-archive manifest — bootstrap summary")
    lines.append(f"Generated: {now}")
    lines.append(f"Manifest:  {MANIFEST}")
    lines.append("")
    lines.append(f"Total manifest entries:        {n_total}")
    lines.append(f"  in bib:                      {n_in_bib}")
    lines.append(f"  cited by genera/clades YAML: {n_cited}")
    lines.append(f"  with DOI:                    {n_with_doi}  ({n_with_doi/n_total*100:.0f}%)")
    lines.append(f"  in bib but uncited in YAML:  {n_orphan_in_bib}")
    lines.append(f"  cited in YAML but not in bib:{n_missing_from_bib}")
    lines.append("")
    lines.append(f"Genera and clades with NO references at all: {len(no_refs)}")
    if no_refs:
        lines.append("")
        lines.append("First 30 with no references:")
        for n in no_refs[:30]:
            lines.append(f"  - {n}")
    lines.append("")
    lines.append("Year distribution (by decade):")
    for d in sorted(decades):
        bar = "#" * min(60, decades[d])
        lines.append(f"  {d}s  {decades[d]:4d}  {bar}")
    if in_yaml_only:
        lines.append("")
        lines.append("First 30 keys cited in YAML but missing from bib:")
        for k in in_yaml_only[:30]:
            lines.append(f"  - {k}")

    SUMMARY.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_text(SUMMARY, "\n".join(lines) + "\n")

    print()
    print("\n".join(lines))
    print()
    print(f"summary written to {SUMMARY}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
