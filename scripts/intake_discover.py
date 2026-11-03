#!/usr/bin/env python3
# corpus-command: intake discover | what is pending in pdfs/, with path hints
"""
Discover what's pending intake by reconciling pdfs/ on disk against the
manifest and `git status pdfs/`. Prints a classification table that the
/intake skill (and the human running it) can act on directly.

The discovery has two distinct sources of "pending work" — both must be
checked, since neither alone is sufficient:

  1. `disk - acquired`  — PDFs in pdfs/ whose manifest entry has no
     acquisition.source set. Catches genuinely-new PDFs.

  2. `git status pdfs/` — untracked or modified PDFs. Catches:
       - Replacements (modified): user dropped a new PDF over an
         existing tracked one. The disk-vs-acquired diff misses these
         because the manifest already shows them as acquired.
       - Already-acquired-but-uncommitted (untracked): manifest shows
         acquired (often with a failed-fetch sentinel such as
         `unpaywall_no_oa`) but the PDF was never committed. User dropped
         a real PDF later.

For each pending key the script also reports year, page count, file
size, and a Path A / B / C / D / V classification hint based on the year,
the cited-vs-PDF page mismatch, and whether the text layer carries
manuscript line numbers (V: marker deletes lines and shifts table cells on
those, so they go straight to vision OCR with --extra-context).

Examples:

  scripts/intake_discover.py
  scripts/intake_discover.py --json     # machine-readable
"""
from __future__ import annotations

import argparse
import json
import re
import string
import subprocess
import sys
from dataclasses import dataclass, asdict
from pathlib import Path

import pypdfium2 as pdfium

from corpus.supplementary import SUPP_PDF_DIR, SUPP_MD_DIR, resolve_parent
from corpus.config import ROOT as ARCHIVE, MD_DIR, PDF_DIR
from corpus.manifest import load as manifest_load
from corpus.manifest import failed_source
from corpus.bib import page_range
from corpus.keys import nfc
from corpus.pages import is_empty as md_is_empty

# Citation-key shape: {authorlastname}{4-digit-year} with an optional single
# trailing lowercase letter that marks a same-author-same-year split
# (smith1945a / smith1945b). Anchored on the year so multi-token stems
# like "vonsmith1926" still parse.
_KEY_RE = re.compile(r"^(?P<stem>.+?\d{4})(?P<suf>[a-z])?$")


def split_stem_suffix(key: str) -> tuple[str | None, str | None]:
    """Split a citation key into (stem-through-year, split-letter|None).

    Returns (None, None) for keys that don't end in a 4-digit year (we can't
    reason about split collisions for those)."""
    m = _KEY_RE.match(key)
    if not m:
        return None, None
    return m.group("stem"), m.group("suf")


def detect_split_collision(key: str, universe: set[str]) -> str | None:
    """If `key` is a *bare* key (no split letter) but sibling split keys
    already exist in `universe` (manifest ∪ disk), return a warning string
    suggesting the next free letter; otherwise None.

    This catches the case the file-exists prompt misses: dropping "foo2015"
    when "foo2015a"/"foo2015b" already exist. The OS only warns on an exact
    name clash, so a bare-vs-split collision slips through to intake and
    forces a rename later."""
    stem, suf = split_stem_suffix(key)
    if stem is None or suf is not None:
        return None  # unparseable, or already a split form — nothing to flag
    siblings = sorted(
        c for c in string.ascii_lowercase
        if f"{stem}{c}" in universe and f"{stem}{c}" != key
    )
    if not siblings:
        return None
    # First free letter scanning a-z (after a,b → c).
    suggestion = next(
        (c for c in string.ascii_lowercase if f"{stem}{c}" not in universe),
        None,
    )
    sib_list = ", ".join(f"{stem}{c}" for c in siblings)
    tail = f" → rename PDF to '{stem}{suggestion}'" if suggestion else ""
    return f"bare key next to split(s) {sib_list}{tail}"


def git_status_pdfs() -> tuple[set[str], set[str]]:
    """Return (untracked_keys, modified_keys) for pdfs/*.pdf via git status."""
    try:
        # core.quotePath=false: keep non-ASCII filenames as raw UTF-8 in the
        # output. Default (true) octal-escapes them ('caf\303\2512026.pdf'),
        # which then doesn't match the NFC-normalized disk key.
        out = subprocess.check_output(
            ["git", "-c", "core.quotePath=false",
             "status", "--porcelain", "--", "pdfs/"],
            cwd=ARCHIVE, text=True,
        )
    except subprocess.CalledProcessError:
        return set(), set()
    untracked: set[str] = set()
    modified: set[str] = set()
    for line in out.splitlines():
        # Porcelain v1: "XY path" (X = index status, Y = worktree status)
        # "?? pdfs/foo.pdf"  → untracked
        # " M pdfs/foo.pdf"  → modified
        if not line or len(line) < 4:
            continue
        code, path = line[:2], line[3:]
        if not path.startswith("pdfs/") or not path.endswith(".pdf"):
            continue
        key = nfc(Path(path).stem)
        if code == "??":
            untracked.add(key)
        elif "M" in code:
            modified.add(key)
    return untracked, modified


@dataclass
class Pending:
    key: str
    in_manifest: bool
    in_bib: bool | None
    prior_source: str | None
    git_state: str          # "new" | "untracked" | "modified" | "unconverted" | "tracked"
    year: int | None
    cited_pages: str | None  # raw bib pages field, like "501-505"
    pdf_pages: int | None
    pdf_size_kb: float | None
    path_hint: str          # "A" | "B" | "C" | "D" | "V" | "?"
    note: str               # extra free-form classification context
    split_warn: str | None  # bare-key-next-to-split-key collision warning


def parse_cited_pages(pages_field: str | None) -> int | None:
    """The cited article's length in pages ("501-505" -> 5), or None
    (corpus.bib.page_range: a book's "250 pp." is not a range)."""
    first, last = page_range(pages_field)
    return last - first + 1 if first and last else None


LEADING_INT = re.compile(r"^\s*(\d{1,4})(?=\s|$)(?!\s*[.)\]])")


def has_line_numbers(pdf, sample: int = 6) -> bool:
    """True when the text layer shows a manuscript line-number gutter: on at
    least two sampled pages, ten or more lines start with an integer that is
    one more than the previous such integer. Numbered lists ("1.", "1)") and
    reference lists ("[1]") do not match. On such pages marker loses table
    cells and whole lines.

    `pdf` is a path or an already open pypdfium2 document (so a caller that
    has opened it for the page count does not open it twice)."""
    if isinstance(pdf, pdfium.PdfDocument):
        doc = pdf
    else:
        try:
            doc = pdfium.PdfDocument(str(pdf))
        except Exception:
            return False
    n = len(doc)
    idx = sorted({min(n - 1, round(i * (n - 1) / max(1, sample - 1))) for i in range(sample)}) if n else []
    hits = 0
    for i in idx:
        nums = []
        for line in doc[i].get_textpage().get_text_bounded().splitlines():
            m = LEADING_INT.match(line)
            if m:
                nums.append(int(m.group(1)))
        if sum(1 for a, b in zip(nums, nums[1:]) if b == a + 1) >= 10:
            hits += 1
    return hits >= 2


def classify(year: int | None, pdf_pages: int | None, cited_pages: int | None,
             prior_source: str | None) -> tuple[str, str]:
    """Return (path_hint, note) using era + scope heuristics."""
    notes: list[str] = []
    # Replacement / re-convert (Path D) trumps year — caller maps git_state
    # to this; we just provide era hints here and Path C scope hint when
    # cited << pdf.
    hint = "?"
    if year is None:
        hint = "?"
        notes.append("no year metadata; inspect manifest")
    elif year < 1900:
        hint = "B"
        notes.append("pre-1900 → vision OCR direct")
    elif year < 1920:
        hint = "A"
        notes.append("1900-1919 → marker first; expect higher vision-fallback rate")
    else:
        hint = "A"
    # Whole-volume hint: if we have a cited pages estimate and the PDF is
    # much larger, Path C (scope-limited inline OCR).
    if pdf_pages and cited_pages and pdf_pages >= 4 * cited_pages and pdf_pages >= 30:
        hint = "C"
        notes.append(f"cited≈{cited_pages}p in {pdf_pages}p PDF → ask user for cited→PDF page mapping")
    if failed_source(prior_source):
        notes.append(f"prior source={prior_source} (failed-fetch sentinel; uncommitted PDF likely)")
    return hint, "; ".join(notes)


def gather_pending(manifest: dict) -> list[Pending]:
    disk_keys = {nfc(p.stem) for p in PDF_DIR.glob("*.pdf")}
    acquired = {
        k for k, e in manifest.items()
        if (e.get("acquisition") or {}).get("source")
    }
    untracked, modified = git_status_pdfs()

    # Universe of known keys for split-collision detection: every manifest
    # entry plus every PDF on disk. A bare pending key whose split siblings
    # live here should be renamed before intake.
    universe = set(manifest) | disk_keys

    # Acquired PDFs that were never converted: what the fetchers downloaded.
    # Where pdfs/ is gitignored (a public checkout) git status never reports
    # them, so without this set they would be invisible to intake.
    def converted(k: str) -> bool:
        md = MD_DIR / f"{k}.md"
        return not md_is_empty(md)
    unconverted = {k for k in disk_keys & acquired
                   if not converted(k)
                   and not (manifest[k].get("conversion") or {}).get("skipped")}

    # Union of: (disk - acquired), untracked, modified, unconverted.
    pending_keys = (disk_keys - acquired) | untracked | modified | unconverted
    out: list[Pending] = []
    for k in sorted(pending_keys):
        pdf_path = PDF_DIR / f"{k}.pdf"
        in_m = k in manifest
        entry = manifest.get(k, {}) or {}
        in_bib = entry.get("in_bib") if in_m else None
        acq = entry.get("acquisition") or {}
        prior_source = acq.get("source")

        if k in modified:
            git_state = "modified"
        elif k in untracked:
            git_state = "untracked"
        elif not in_m or not prior_source:
            git_state = "new"
        elif k in unconverted:
            git_state = "unconverted"
        else:
            git_state = "tracked"

        year = entry.get("year")
        if isinstance(year, str):
            try:
                year = int(year)
            except ValueError:
                year = None
        cited_pages_raw = (entry.get("citation") or {}).get("pages")
        cited_pages = parse_cited_pages(cited_pages_raw)

        pdf_pages: int | None = None
        size_kb: float | None = None
        pdf = None
        if pdf_path.exists():
            try:
                pdf = pdfium.PdfDocument(str(pdf_path))
                pdf_pages = len(pdf)
            except Exception:
                pdf_pages = None
            size_kb = pdf_path.stat().st_size / 1024.0

        path_hint, note = classify(year, pdf_pages, cited_pages, prior_source)
        if path_hint in ("A", "?") and pdf is not None and has_line_numbers(pdf):
            path_hint = "V"
            note = (note + ("; " if note else "")
                    + "line-numbered manuscript → vision OCR with --extra-context naming the gutter, skip marker")
        # Path D overrides if PDF was modified (replacement re-convert)
        if git_state == "modified":
            path_hint = "D"
            note = (note + ("; " if note else "")
                    + "modified in git → replacement re-convert; for vision keys"
                    + " clear .runtime/cache/vision/{key}/ first")
        elif git_state == "untracked" and prior_source:
            note = (note + ("; " if note else "")
                    + f"already-acquired-uncommitted (prior source={prior_source}); "
                    + "treat as new acquisition with previous_source preserved")

        out.append(Pending(
            key=k,
            in_manifest=in_m,
            in_bib=in_bib,
            prior_source=prior_source,
            git_state=git_state,
            year=year,
            cited_pages=cited_pages_raw,
            pdf_pages=pdf_pages,
            pdf_size_kb=size_kb,
            path_hint=path_hint,
            note=note,
            split_warn=detect_split_collision(k, universe),
        ))
    return out


def print_table(pending: list[Pending]) -> None:
    if not pending:
        print("Nothing pending intake. ✓")
        return
    hdr = f"{'key':28s} {'state':11s} {'in_m':5s} {'in_bib':6s} {'year':4s} {'pp':>5s} {'KB':>7s} {'P':1s}  {'note'}"
    print(hdr)
    print("-" * len(hdr))
    for p in pending:
        in_m = "Y" if p.in_manifest else "ORPH"
        in_bib = "Y" if p.in_bib else ("N" if p.in_manifest else "-")
        year = str(p.year) if p.year else "?"
        pp = str(p.pdf_pages) if p.pdf_pages is not None else "?"
        size = f"{p.pdf_size_kb:.0f}" if p.pdf_size_kb is not None else "?"
        flag = "  ⚠SPLIT" if p.split_warn else ""
        print(f"{p.key:28s} {p.git_state:11s} {in_m:5s} {in_bib:6s} {year:4s} {pp:>5s} {size:>7s} {p.path_hint}  {p.note}{flag}")
    print()
    paths = {}
    for p in pending:
        paths[p.path_hint] = paths.get(p.path_hint, 0) + 1
    legend = "  ".join(f"{h}={n}" for h, n in sorted(paths.items()))
    print(f"=== {len(pending)} pending  ({legend}) ===")

    collisions = [p for p in pending if p.split_warn]
    if collisions:
        print()
        print("⚠  SPLIT-KEY COLLISION — rename before intake:")
        for p in collisions:
            print(f"     {p.key}: {p.split_warn}")



def gather_supplementary_pending(manifest: dict) -> list[dict]:
    """Supplementary PDFs (supplementary/{parent}-{name}.pdf) that have no
    converted markdown yet (markdown/supplementary/{stem}.md missing/empty).
    Flags any whose {parent} prefix doesn't match a manifest key."""
    if not SUPP_PDF_DIR.exists():
        return []
    known_parents = set(manifest)
    out: list[dict] = []
    for pdf in sorted(SUPP_PDF_DIR.glob("*.pdf")):
        stem = nfc(pdf.stem)
        md = SUPP_MD_DIR / f"{stem}.md"
        if not md_is_empty(md):
            continue  # already converted
        parsed = resolve_parent(stem, known_parents)
        if parsed and ((manifest[parsed[0]].get("supplementary") or {})
                       .get(parsed[1]) or {}).get("skipped"):
            continue  # stored on purpose without markdown (e.g. a map sheet)
        try:
            doc = pdfium.PdfDocument(str(pdf))
            pp = len(doc)
        except Exception:
            doc, pp = None, None
        lined = has_line_numbers(doc) if doc is not None else False
        out.append({
            "line_numbers": lined,
            "stem": stem,
            "parent": parsed[0] if parsed else None,
            "name": parsed[1] if parsed else None,
            "pdf_pages": pp,
            "pdf_size_kb": pdf.stat().st_size / 1024.0,
            "parent_ok": parsed is not None,
        })
    return out


def print_supplementary(pending: list[dict]) -> None:
    if not pending:
        return
    print()
    hdr = f"{'supplementary stem':40s} {'parent':22s} {'pp':>4s} {'KB':>7s}  note"
    print(hdr)
    print("-" * len(hdr))
    for p in pending:
        parent = p["parent"] or "??"
        pp = str(p["pdf_pages"]) if p["pdf_pages"] is not None else "?"
        size = f"{p['pdf_size_kb']:.0f}"
        if not p["parent_ok"]:
            note = "⚠ NO PARENT KEY — check {parent}-{name} naming"
        elif p.get("line_numbers"):
            note = "line-numbered → vision OCR with --extra-context (not marker)"
        else:
            note = "marker --supplementary"
        print(f"{p['stem']:40s} {parent:22s} {pp:>4s} {size:>7s}  {note}")
    print(f"\n=== {len(pending)} supplementary pending ===")


def print_odd_extensions() -> None:
    """Every script expects pdfs/{key}.pdf; a ".PDF" or ".Pdf" drop is invisible
    to the "*.pdf" glob in gather_pending. Runs even when nothing else is
    pending."""
    odd_ext = sorted(p.name for p in PDF_DIR.iterdir()
                     if p.is_file() and p.suffix.lower() == ".pdf" and p.suffix != ".pdf")
    if odd_ext:
        print()
        print("⚠  NOT LISTED ABOVE — extension is not lowercase .pdf; rename to {key}.pdf:")
        for name in odd_ext:
            print(f"     {name}")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--json", action="store_true",
                    help="emit JSON to stdout instead of a table")
    args = ap.parse_args()

    manifest = manifest_load()
    pending = gather_pending(manifest)
    supp = gather_supplementary_pending(manifest)
    if args.json:
        json.dump({"pending": [asdict(p) for p in pending],
                   "supplementary": supp}, sys.stdout, indent=2)
        sys.stdout.write("\n")
    else:
        print_table(pending)
        print_supplementary(supp)
        print_odd_extensions()
    return 0


if __name__ == "__main__":
    sys.exit(main())
