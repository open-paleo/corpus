#!/usr/bin/env python3
# corpus-command: pdf append-pages | append pages from a source PDF
"""
Pre-conversion: append more pages from a source PDF onto an existing
`pdfs/{key}.pdf`, extending a scope-limited extraction.

The companion to `extract_pdf_pages.py`. That script cuts a whole-volume PDF
down to a page range; this one widens the range afterwards, when it turns out
the article or chapter ran further than first thought — without re-cutting from
scratch and losing the existing extraction.

Pages are appended in spec order, losslessly (`insert_pdf` preserves the
original page streams). The manifest's `acquisition.pdf_extract_pages` is
rewritten to the union of the old and new specs so the record stays truthful
about what the on-disk PDF contains.

Usage:
  ./.runtime/venv/bin/python scripts/append_pdf_pages.py paul1988a \\
      --source "/path/to/Predatory dinosaurs of the world.pdf" \\
      --pages "320-323"

After appending, the existing markdown is stale: it covers fewer pages than the
PDF now holds. Re-convert the key (Path D) rather than trying to splice new
text onto the end of the old file.
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime, timezone
from pathlib import Path

import pypdfium2 as pdfium
from corpus.config import MANIFEST, PDF_DIR
from corpus.manifest import commit as manifest_commit, load as manifest_load
from corpus.keys import key_arg
from corpus.pdf.pages import compact, parse as parse_spec
from corpus.io import atomic_path





def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("key", type=key_arg)
    ap.add_argument("--source", required=True,
                    help="PDF to take the extra pages from (often the full book)")
    ap.add_argument("--pages", required=True,
                    help="1-indexed page spec in the SOURCE pdf, e.g. '320-323'")
    ap.add_argument("--create", action="store_true",
                    help="create pdfs/{key}.pdf if absent (book-chapter case: "
                         "no prior extraction to widen)")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    target = PDF_DIR / f"{args.key}.pdf"
    source = Path(args.source).expanduser()
    creating = not target.exists()
    if creating and not args.create:
        print(f"missing {target} (pass --create to build it from --source)",
              file=sys.stderr)
        return 2
    if not source.exists():
        print(f"missing {source}", file=sys.stderr)
        return 2

    pages = parse_spec(args.pages)
    if not pages:
        print("empty page spec", file=sys.stderr)
        return 2

    src = pdfium.PdfDocument(str(source))
    # Creating from scratch is the book-chapter case: no prior extraction to
    # widen. Build in memory rather than from an empty placeholder file.
    # Cheaper than copying the whole volume into pdfs/ just to cut it down
    # with extract_pdf_pages.py.
    dst = pdfium.PdfDocument.new() if creating else pdfium.PdfDocument(str(target))
    before_pages = 0 if creating else len(dst)
    before_bytes = 0 if creating else target.stat().st_size

    bad = [p for p in pages if p < 1 or p > len(src)]
    if bad:
        print(f"out of range for {source.name} ({len(src)}p): {bad}",
              file=sys.stderr)
        return 2

    verb = "create" if creating else "target"
    print(f"  {verb}:  {target}  ({before_pages}p, {before_bytes/1e6:.1f} MB)")
    print(f"  source:  {source}  ({len(src)}p)")
    print(f"  append:  '{args.pages}'  ->  {len(pages)} page(s): {compact(pages)}")

    if args.dry_run:
        print("  (dry run; nothing written)")
        return 0

    dst.import_pages(src, [p - 1 for p in pages])

    with atomic_path(target) as tmp:
        dst.save(str(tmp))
    after_bytes = target.stat().st_size
    print(f"  output:  {target}  ({len(dst)}p, {after_bytes/1e6:.1f} MB)")

    if MANIFEST.exists():
        m = manifest_load()
        entry = m.get(args.key)
        if entry is None:
            print(f"  warn: {args.key} not in manifest; PDF extended but "
                  f"acquisition not annotated")
        else:
            acq = entry.setdefault("acquisition", {})
            prior = parse_spec(acq.get("pdf_extract_pages") or "")
            merged = compact(sorted(set(prior) | set(pages)) if prior else pages)
            acq["pdf_extract_pages"] = merged
            acq["pdf_extended_at"] = datetime.now(timezone.utc).isoformat()
            manifest_commit(m)
            print(f"  manifest: acquisition.pdf_extract_pages='{merged}'")
            print(f"  NOTE: markdown/{args.key}.md now covers fewer pages than "
                  f"the PDF — re-convert (Path D).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
