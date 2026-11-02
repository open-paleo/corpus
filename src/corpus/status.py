"""`corpus status`: how much of the bib this corpus holds, and what is missing.

    corpus status                      counts: bib, held, converted, indexed
    corpus status --missing            plus one line per missing key
    corpus status --missing --has-doi  only what the fetchers can try
    corpus status --genus Iguanodon    restrict everything to keys a genus cites

"Held" and "converted" are read from disk (pdfs/{key}.pdf, a non-empty
markdown/{key}.md), not from manifest.acquisition, whose source field also
records failed attempts. A key is missing when it is in the bib and has
neither. --genus/--clade match the genera and clades that cite a key
directly (manifest genera[]/clades[]), not the taxa nested under a clade.
"""
from __future__ import annotations

import argparse
import sqlite3
import sys
from collections import Counter

from corpus.config import DB, MD_DIR, PDF_DIR
from corpus.keys import keys_in
from corpus.manifest import acquisition, conversion, load as manifest_load


def _on_disk(folder, suffix: str, nonempty: bool = False) -> set[str]:
    """corpus.keys.keys_in, without translation backups and, if nonempty,
    without empty files (a converted paper has text)."""
    keys = {k for k in keys_in(folder, suffix) if not k.endswith(".original")}
    if nonempty:
        keys = {k for k in keys if (folder / f"{k}{suffix}").stat().st_size}
    return keys


def _indexed() -> set[str] | None:
    if not DB.exists():
        return None
    try:
        with sqlite3.connect(f"file:{DB}?mode=ro", uri=True) as conn:
            return {r[0] for r in conn.execute("SELECT citation_key FROM papers")}
    except sqlite3.Error:
        return None


def _label(entry: dict, width: int = 70) -> str:
    cit = entry.get("citation") or {}
    author = (cit.get("author") or "").split(" and ")[0].split(",")[0]
    title = cit.get("title") or ""
    text = f"{author}: {title}" if author else title
    return text if len(text) <= width else text[:width - 1] + "…"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="corpus status", description=__doc__.split("\n\n")[0])
    ap.add_argument("--missing", action="store_true", help="list the missing keys")
    ap.add_argument("--genus", action="append", default=[],
                    help="only keys this genus cites (repeatable)")
    ap.add_argument("--clade", action="append", default=[],
                    help="only keys this clade cites directly (repeatable)")
    doi = ap.add_mutually_exclusive_group()
    doi.add_argument("--has-doi", action="store_true", help="only keys with a DOI")
    doi.add_argument("--no-doi", action="store_true", help="only keys without a DOI")
    ap.add_argument("--limit", type=int, default=0, help="list at most N missing keys")
    args = ap.parse_args(argv)

    manifest = manifest_load(track=False)
    if not manifest:
        print("manifest.json is empty or missing: run `corpus manifest refresh` first", file=sys.stderr)
        return 2
    genera = {g.lower() for g in args.genus}
    clades = {c.lower() for c in args.clade}

    def wanted(entry: dict) -> bool:
        if genera and not genera & {g.lower() for g in entry.get("genera") or []}:
            return False
        if clades and not clades & {c.lower() for c in entry.get("clades") or []}:
            return False
        if args.has_doi and not entry.get("doi"):
            return False
        if args.no_doi and entry.get("doi"):
            return False
        return True

    keys = {k for k, e in manifest.items() if wanted(e)}
    pdfs, mds = _on_disk(PDF_DIR, ".pdf") & keys, _on_disk(MD_DIR, ".md", nonempty=True) & keys
    indexed = _indexed()
    in_bib = {k for k in keys if manifest[k].get("in_bib")}
    missing = sorted(k for k in in_bib if k not in pdfs and k not in mds)
    no_md = [k for k in keys if k in pdfs and k not in mds]
    skipped = sorted(k for k in no_md if conversion(manifest[k]).get("skipped"))
    pending = sorted(k for k in no_md if not conversion(manifest[k]).get("skipped"))
    vision = sorted(k for k in keys if conversion(manifest[k]).get("vision_ocr_needed"))

    sources = Counter(acquisition(manifest[k]).get("source") or "unrecorded" for k in pdfs)
    tools = Counter(conversion(manifest[k]).get("tool") or "unrecorded" for k in mds)
    n_doi = sum(1 for k in missing if manifest[k].get("doi"))

    scope = [f"genus {g}" for g in args.genus] + [f"clade {c}" for c in args.clade]
    scope += ["with a DOI"] if args.has_doi else ["without a DOI"] if args.no_doi else []
    print(f"Corpus status{' (' + ', '.join(scope) + ')' if scope else ''}")
    print(f"  bib entries        {len(in_bib):6d}"
          + (f"   (+{len(keys) - len(in_bib)} not in the bib)" if len(keys) > len(in_bib) else ""))
    print(f"  held (PDF)         {len(pdfs):6d}   "
          + ", ".join(f"{s} {n}" for s, n in sources.most_common(6)))
    print(f"  converted          {len(mds):6d}   "
          + ", ".join(f"{t} {n}" for t, n in tools.most_common(4)))
    if skipped:
        print(f"  held, not converted{len(skipped):6d}   conversion.skipped (citation-only)")
    if pending:
        print(f"  PDF, no markdown   {len(pending):6d}   run /intake (corpus intake discover): "
              + ", ".join(pending[:5]) + (" ..." if len(pending) > 5 else ""))
    if vision:
        print(f"  vision OCR flagged {len(vision):6d}   corpus convert vision")
    if indexed is not None:
        stale = len(mds - indexed)
        print(f"  indexed            {len(indexed & keys):6d}"
              + (f"   ({stale} converted but not indexed: corpus index)" if stale else ""))
    else:
        print("  indexed               -   no index yet: corpus index")
    print(f"  missing            {len(missing):6d}   {n_doi} with a DOI "
          "(corpus fetch unpaywall / oa-direct can try these)")

    if args.missing:
        shown = missing[:args.limit] if args.limit else missing
        print()
        for k in shown:
            e = manifest[k]
            tags = "".join(f"({a}) " for a in e.get("annotations") or [])
            print(f"  {k:<28} {str(e.get('year') or ''):>4}  {tags}{_label(e)}"
                  + (f"  doi:{e['doi']}" if e.get("doi") else ""))
        if len(shown) < len(missing):
            print(f"  ... {len(missing) - len(shown)} more (--limit 0 for all)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
