#!/usr/bin/env python3
# corpus-command: pdf extract-pages | cut a PDF down to a page range, in place
"""
Extract specific pages from pdfs/{key}.pdf, replacing the file in place
with the extracted subset. Use to scope-limit a whole-volume PDF down
to just the pages of interest before /intake processes them.

Page spec syntax (1-indexed, comma-separated, ranges with `-`):

  "122"             →  page 122
  "122-130"         →  pages 122 through 130 inclusive
  "97, 122-130"     →  page 97, then pages 122-130
  "1-3, 7, 11-13"   →  pages 1, 2, 3, 7, 11, 12, 13

Pages are emitted in the order given (so "130-122" is reversed). Dupes
are silently dropped while preserving the first occurrence's position.

⚠ This *replaces* pdfs/{key}.pdf in place. The original PDF is gone
unless previously committed (git LFS history covers that case). Use
--keep-source PATH to write a copy of the source PDF before extracting.

Updates manifest[key].acquisition.pdf_extract_pages /
pdf_extracted_at + appends to acquisition.notes.

Usage:
  scripts/extract_pdf_pages.py KEY PAGE_SPEC [--keep-source PATH]
  scripts/extract_pdf_pages.py KEY "122-123"
  scripts/extract_pdf_pages.py KEY "97, 122-130"
  scripts/extract_pdf_pages.py KEY 97, 122-130              # unquoted OK too
"""
from __future__ import annotations

import argparse
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path

import pypdfium2 as pdfium

from corpus.supplementary import SUPP_PDF_DIR, resolve_parent, supp_record
from corpus.config import MANIFEST, PDF_DIR
from corpus.manifest import commit as manifest_commit, load as manifest_load
from corpus.keys import key_arg
from corpus.pdf.pages import compact as fmt_pages, parse as _parse_pages
from corpus.io import atomic_path





def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("key", type=key_arg, help="citation key (pdfs/{key}.pdf), or supplementary "
                                 "stem {parent}-{name} with --supplementary")
    ap.add_argument("pages", nargs="+",
                    help='page spec, e.g. "97" / "122-130" / "97, 122-130"')
    ap.add_argument("--supplementary", action="store_true",
                    help="Target supplementary/{key}.pdf instead of pdfs/{key}.pdf; "
                         "annotate the parent's supplementary sub-record.")
    ap.add_argument("--keep-source", metavar="PATH",
                    help="Write a copy of the source PDF to PATH before extracting.")
    ap.add_argument("--no-manifest", action="store_true",
                    help="Rewrite the PDF but leave manifest.json untouched. Use when "
                         "another process is mid-run against the manifest (its "
                         "read-modify-write would clobber this annotation, or vice "
                         "versa), or when slicing a PDF that isn't a tracked key. "
                         "NOTE: the page range is then recorded nowhere — put it in "
                         "the commit message, which is the fallback scope record.")
    ap.add_argument("--soft-limit-mb", type=int, default=90,
                    help="Warn if output exceeds this size in MB (default 90).")
    args = ap.parse_args()

    src_dir = SUPP_PDF_DIR if args.supplementary else PDF_DIR
    pdf_path = src_dir / f"{args.key}.pdf"
    if not pdf_path.exists():
        print(f"error: {pdf_path} does not exist", file=sys.stderr)
        return 1

    spec = " ".join(args.pages)

    src = pdfium.PdfDocument(str(pdf_path))
    src_n = len(src)
    src_size = pdf_path.stat().st_size

    try:
        pages = _parse_pages(spec, src_n, allow_empty=False)
    except ValueError as e:
        print(f"error: {e}", file=sys.stderr)
        src.close()
        return 1

    print(f"  source:  {pdf_path}  ({src_n}p, {src_size/1024/1024:.1f} MB)")
    print(f"  spec:    {spec!r}  →  {len(pages)} page(s): {fmt_pages(pages)}")

    if args.keep_source:
        backup = Path(args.keep_source).expanduser()
        backup.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(pdf_path, backup)
        print(f"  source kept at {backup}")

    # import_pages copies each page with only the objects it references, so a
    # few pages cut from a whole volume do not carry the rest of the volume.
    out = pdfium.PdfDocument.new()
    out.import_pages(src, [p - 1 for p in pages])

    with atomic_path(pdf_path) as tmp:
        out.save(str(tmp))
    out.close()
    src.close()
    new_size = pdf_path.stat().st_size

    print(f"  output:  {pdf_path}  ({len(pages)}p, {new_size/1024/1024:.1f} MB)")
    if new_size / 1024 / 1024 > args.soft_limit_mb:
        print(f"  ⚠ output {new_size/1024/1024:.1f} MB > soft limit {args.soft_limit_mb} MB; "
              "consider stitching with /stitch --reencode if pushing to LFS.")

    if args.no_manifest:
        print(f"  manifest: SKIPPED (--no-manifest) — record "
              f"'{fmt_pages(pages)}' in the commit message")
    elif MANIFEST.exists():
        m = manifest_load()
        now = datetime.now(timezone.utc).isoformat()
        note = f"extracted {len(pages)} page(s) ({fmt_pages(pages)}) from {src_n}-page source"

        if args.supplementary:
            # Annotate the parent's supplementary sub-record, not acquisition.
            parsed = resolve_parent(args.key, set(m))
            if parsed is None:
                print(f"  warn: no parent manifest key for supplementary stem "
                      f"{args.key!r}; PDF rewritten but not annotated")
            else:
                parent, name = parsed
                rec = supp_record(m, parent, name)
                rec["pdf_path"] = f"supplementary/{args.key}.pdf"
                rec["pdf_extracted_at"] = now
                rec["pdf_extract_pages"] = fmt_pages(pages)
                existing = rec.get("notes") or ""
                rec["notes"] = (note if (not existing or "extracted" in existing)
                                else f"{existing}; {note}")
                manifest_commit(m)
                print(f"  manifest: {parent}.supplementary.{name}."
                      f"pdf_extract_pages={fmt_pages(pages)!r}")
        elif args.key in m:
            acq = m[args.key].setdefault("acquisition", {})
            acq["pdf_extracted_at"] = now
            acq["pdf_extract_pages"] = fmt_pages(pages)
            existing = acq.get("notes") or ""
            if not existing:
                acq["notes"] = note
            elif "extracted" in existing:
                acq["notes"] = note  # replace prior extraction note
            else:
                acq["notes"] = f"{existing}; {note}"
            manifest_commit(m)
            print(f"  manifest: acquisition.pdf_extract_pages={fmt_pages(pages)!r}")
        else:
            print(f"  warn: {args.key} not in manifest; PDF rewritten but acquisition not annotated")

    return 0


if __name__ == "__main__":
    sys.exit(main())
