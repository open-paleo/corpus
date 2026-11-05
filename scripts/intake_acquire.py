#!/usr/bin/env python3
# corpus-command: intake acquire | record dropped PDFs as acquired
"""
Atomically write/update manifest acquisition entries for one or more
PDFs that the user has dropped into pdfs/.

For each key:
  - If not in manifest, create a stub entry with in_bib=False and blank
    citation/year/doi/genera/clades. (manifest_refresh.py will overlay
    bib metadata once the upstream catches up.)
  - Set acquisition = {source: manual, url: None, pdf_path, fetched_at,
    previous_source: <prior source if any>}. The previous_source field
    preserves the audit trail across replacements and failed-fetch
    sentinel takeovers (corpus.manifest.failed_source). PDF-provenance annotations written by sibling
    skills (pdf_extract_pages, pdf_extracted_at, pdf_stitched_at,
    pdf_source, pdf_compressed_at, pdf_compression, jp2_dir, note) are
    carried forward — they describe the on-disk PDF, not the fetch.
  - Optionally set conversion.vision_ocr_needed=True with a reason
    (Path B for pre-1900, or any case where marker should be skipped
    and we go straight to vision OCR).

Skips any key whose PDF doesn't exist on disk (warns).

Examples:

  scripts/intake_acquire.py KEY [KEY ...]
  scripts/intake_acquire.py --keys-file /tmp/intake_keys.txt
  scripts/intake_acquire.py KEY --vision-ocr "1800s scan; skip marker"
  scripts/intake_acquire.py KEY --vision-ocr "book chapter scan; no text layer"
"""
from __future__ import annotations

import argparse
import sys
from datetime import datetime, timezone
from corpus.config import PDF_DIR
from corpus.manifest import commit as manifest_commit, load as manifest_load
from corpus.keys import read_keys_file
from corpus.keys import key_arg
from corpus.manifest import entry_skeleton

def load_keys(args) -> list[str]:
    keys: list[str] = list(args.keys or [])
    if args.keys_file:
        keys.extend(read_keys_file(args.keys_file))
    if not keys:
        print("error: no keys given (positional or --keys-file)", file=sys.stderr)
        sys.exit(2)
    # de-dupe while preserving order
    seen: set[str] = set()
    out: list[str] = []
    for k in keys:
        if k not in seen:
            seen.add(k)
            out.append(k)
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("keys", type=key_arg, nargs="*", help="citation keys to mark acquired")
    ap.add_argument("--keys-file", help="file with one key per line (#-comments OK)")
    ap.add_argument("--vision-ocr", metavar="REASON",
                    help="set conversion.vision_ocr_needed=True with this "
                         "reason. Use for Path B (pre-1900 scans) and for "
                         "PDFs you know don't have a usable text layer.")
    ap.add_argument("--source", default="manual",
                    help='acquisition.source value (default "manual")')
    ap.add_argument("--url", default=None,
                    help="acquisition.url, e.g. the manual download landing page")
    args = ap.parse_args()
    keys = load_keys(args)

    manifest = manifest_load()
    now = datetime.now(timezone.utc).isoformat()
    created: list[str] = []
    updated: list[str] = []
    skipped: list[tuple[str, str]] = []

    for k in keys:
        pdf_path = PDF_DIR / f"{k}.pdf"
        if not pdf_path.exists():
            skipped.append((k, "no pdf on disk"))
            continue
        if k not in manifest:
            # Stub orphan: in_bib False, blank citation until manifest_refresh
            # overlays the bib entry.
            entry = entry_skeleton(in_bib=False, citation={})
            manifest[k] = entry
            created.append(k)
        else:
            updated.append(k)
        prior = manifest[k].get("acquisition") or {}
        acq = {
            "source": args.source,
            "url": args.url,
            "pdf_path": f"pdfs/{k}.pdf",
            "fetched_at": now,
            "previous_source": prior.get("source"),
        }
        # Preserve PDF-provenance annotations written by sibling skills
        # (/extract-pages, /stitch, /compress). They describe how the
        # on-disk PDF was produced and must survive a re-acquire — without
        # this, intake_acquire silently drops e.g. pdf_extract_pages.
        for fld in (
            "pdf_extract_pages", "pdf_extracted_at",
            "pdf_extended_at",
            "pdf_stitched_at", "pdf_source",
            "pdf_compressed_at", "pdf_compression",
            "jp2_dir", "note",
        ):
            if prior.get(fld) is not None:
                acq[fld] = prior[fld]
        manifest[k]["acquisition"] = acq
        if args.vision_ocr:
            conv = manifest[k].setdefault("conversion", {})
            prev_tool = conv.get("tool")
            conv["vision_ocr_needed"] = True
            conv["vision_ocr_reason"] = args.vision_ocr
            if prev_tool:
                conv["previous_tool"] = prev_tool

    manifest_commit(manifest)

    print(f"Acquisitions written: {len(created) + len(updated)}")
    if created:
        print(f"  stub-orphan (in_bib=False) created: {len(created)}")
        for k in created: print(f"    + {k}")
    if updated:
        print(f"  existing entry updated: {len(updated)}")
        for k in updated: print(f"    ~ {k}")
    if skipped:
        print(f"  skipped: {len(skipped)}")
        for k, why in skipped: print(f"    ! {k}  ({why})")
    if args.vision_ocr:
        print(f'  conversion.vision_ocr_needed=True for all written keys '
              f'(reason="{args.vision_ocr}")')

    orphans = [k for k in created if not manifest[k].get("in_bib")]
    if orphans:
        print()
        print(f"Stub-orphans pending bib propagation ({len(orphans)}):")
        for k in orphans: print(f"  - {k}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
