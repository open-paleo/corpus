#!/usr/bin/env python3
"""Unwrap marker's line-number "tables" in Elsevier accepted manuscripts.

Why this exists
---------------
Accepted manuscripts carry sequential LINE NUMBERS in the left gutter. Marker
reads that gutter as a table column and renders whole pages as two-column
tables, one row per physical line:

    | 24 | and two lateral ridges in the lateral area of the deltopectoral    |
    | 25 | crest of the humerus. The presence of Early Cretaceous brachiosaur- |

The prose is intact but wrapped in table syntax, one row per line, so it reads
badly and the FTS index stores pipes and gutter digits as content.

The same PDFs carry a rotated "ACCEPTED MANUSCRIPT" watermark down the page
edge. Marker emits it as isolated capitals glued to the start of the adjacent
cell with <br>, spelling the phrase out vertically:

    | 29 | P<br>Keywords                                              |
    | 30 | I<br>Soriatitan golmayensis, Golmayo Formation, …           |

`strip_frontmatter.py` removes the flat watermark line, but not these
in-cell fragments.

Safety
------
These papers ALSO contain genuine data tables (species lists, measurement
tables), so this only ever touches:

  * separator rows with exactly two cells
  * `| N | text |` rows — exactly two columns, no internal pipes. N may be
    several line numbers marker merged into one row (`| 333<br>334<br>335 |`),
    which is how reference lists come back, but it MUST be digits
  * `| text |` rows — single column, no internal pipes
  * `|  | U |` — blank gutter, watermark-only cell; dropped outright

Any row with 3+ columns is left untouched. The script also REFUSES to run on a
file that doesn't look like an accepted manuscript (no "ACCEPTED MANUSCRIPT"
string and no watermark fragments) or that has fewer than --min-rows gutter
rows — that gate is what stops it mangling a normal data-table paper.

Do NOT relax the mandatory digits in the gutter cell. An empty first cell also
describes an ordinary two-column data-table row, and old monographs with real
measurement tables trip the watermark gate by accident (a cell starting `A<br>`
is enough). Accepting those rows inflates the gutter count past --min-rows and
eats the tables. --min-rows is the only thing standing between those files and
damage.

Watermark capitals are dropped from ANYWHERE in a cell, not just the front: the
rotated text runs down the page edge, so marker interleaves single letters
through the prose. Surviving fragments are continuation lines of one physical
sentence and rejoin with a space.

A cell is only written off as vertical-scramble if what remains AFTER dropping
the watermark letters is still short-and-many — otherwise a caption that merely
had capitals threaded through it would be abandoned when it is fully
recoverable. Genuine scrambles are counted and reported; only re-OCR fixes those.

Inline gutter numbers (second pass)
-----------------------------------
Marker does not table every page. On the ones it renders as prose the gutter
number lands INSIDE the sentence, and no amount of table surgery touches it:

    …calcareous in its lower part and cross-150 bedded higher up (Thompson, 1975)

There is no markup to key off, so these are found as an ascending near-
consecutive run of bare 1-3 digit tokens — real prose numbers don't count upward
across paragraphs. A number that broke a hyphenated word closes back up
("cross-150 bedded" -> "cross-bedded").

The hazard is an author-affiliation block, which is also a short ascending run
and must survive. Length separates the clear cases (a page of gutter is dozens
of numbers); for short runs, position decides — a gutter number interrupts a
sentence, an affiliation number opens a line. One paper can have both: a
4-long gutter tail (mid-line, removed) and affiliations 6-9 (line-initial,
kept, along with street numbers such as "2073 Market Street").

Usage
-----
    ./.runtime/venv/bin/python scripts/untable_gutter_manuscript.py --key KEY
    ./.runtime/venv/bin/python scripts/untable_gutter_manuscript.py --key K --apply
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path
from corpus.config import MD_DIR
from corpus.keys import key_from_path
from corpus.keys import key_arg
from corpus.pages import in_marker, write_markdown

SEP2 = re.compile(r"^\s*\|[-: ]+\|[-: ]+\|\s*$")          # two-cell separator
# The gutter cell is usually one line number, but marker merges several physical
# lines into one row on densely-set pages (reference lists especially), giving
# `| 333<br>334<br>335 |`. Digits stay MANDATORY: an optional/empty first cell
# also matches ordinary two-column data-table rows, and on old monographs with
# real measurement tables that quietly inflates the gutter count past --min-rows
# and eats the tables. Don't.
GUTTER = re.compile(
    r"^\s*\|\s*(\d{1,4}(?:\s*<br>\s*\d{1,4})*)\s*\|([^|]*)\|\s*$")
# Long manuscripts pass line 999, so 4 digits are
# allowed — but capped below 1800 in gutter_values() so a two-column table keyed
# by publication year can never be mistaken for a gutter.
MAX_LINE_NO = 1500


def gutter_values(cell: str) -> bool:
    """True if every number in the gutter cell is a plausible line number."""
    return all(int(v) <= MAX_LINE_NO for v in re.findall(r"\\d+", cell))
# Blank gutter + a cell holding nothing but watermark capitals (`|    | U |`).
# Narrow on purpose — the second cell must be watermark-only, so a data-table
# row with a blank first cell and real content is never touched.
WM_ROW = re.compile(r"^\s*\|\s*\|\s*(?:[ACEDPTMNUSRI](?:<br>)?)+\s*\|\s*$")
# Blank gutter cell where the LINE NUMBER got swept into the text cell behind a
# watermark letter (`|  | I<br>1 6 Museum für Naturkunde…`).
# Requiring the watermark fragment keeps this narrow — a data-table row with an
# empty first cell doesn't open with a lone capital + <br>, so the blanket
# empty-gutter rule (which eats data tables) stays rejected.
WM_LEAD_ROW = re.compile(r"^\s*\|\s*\|\s*[ACEDPTMNUSRI]<br>([^|]*)\|\s*$")
SINGLE = re.compile(r"^\s*\|([^|]*)\|\s*$")
AM = re.compile(r"ACCEPTED\s*MANUSCRIPT", re.I)
# letters of "ACCEPTED MANUSCRIPT", emitted one per line down the page edge
WM_PREFIX = re.compile(r"^(?:[ACEDPTMNUSRI]<br>)+")
WM_ONLY = re.compile(r"^(?:[ACEDPTMNUSRI](?:<br>)?)+$")
# Unanchored form for the file-level gate: the fragment sits at the START OF A
# CELL, not the start of the line, so an anchored match against the raw line
# always fails. (Same trap as clean_cell's leading padding.)
WM_IN_CELL = re.compile(r"\|\s*[ACEDPTMNUSRI]<br>")


BARE_NUM = re.compile(r"(?<!\S)(\d{1,4})(?!\S)")
# The gutter number can also land INSIDE a hyphenated word that wrapped across
# the line break — "cross-150 bedded" for "cross-bedded". Anchored on a letter
# before the hyphen so numeric forms like "126/127" or "2002-2003" can't match.
HYPHEN_NUM = re.compile(r"(?<=[A-Za-z])-(\d{1,4})(?=\s)")
# A long ascending run is gutter on length alone. Short runs are ambiguous: the
# affiliation block ("5 National Museums…, 6 2073 Market Street…") is a genuine
# ascending run of 4 and MUST survive, but so is a 4-long tail of real gutter.
# Length alone can't separate them — position can.
# A gutter number interrupts a sentence; an affiliation number opens a line.
MIN_RUN = 8          # accepted on length alone
MIN_RUN_MIDLINE = 3  # accepted if mostly mid-line as well


def _mid_line(text: str, pos: int) -> bool:
    """True if real text precedes this token on its own line."""
    return bool(text[text.rfind("\n", 0, pos) + 1:pos].strip())


def inline_runs(text: str) -> list[list[tuple[int, int, int]]]:
    """Find ascending runs of bare 1-3 digit tokens: [(start, end, value), …].

    On pages marker did NOT table, the gutter number lands inline in the prose
    ("…and cross-150 bedded higher up…"). They can't be matched positionally, so
    they're identified as a monotonic near-consecutive sequence — real prose
    numbers (measurements, counts) don't count upward across paragraphs.
    """
    toks = [(m.start(1), m.end(1), int(m.group(1))) for m in BARE_NUM.finditer(text)]
    toks += [(m.start(1), m.end(1), int(m.group(1))) for m in HYPHEN_NUM.finditer(text)]
    # 4 digits so manuscripts past line 999 are
    # covered, capped below 1800 so citation years can never join a run.
    marked = in_marker(text)
    toks = sorted(t for t in toks if t[2] <= MAX_LINE_NO and not marked(t[0]))

    def qualifies(run):
        if len(run) >= MIN_RUN:
            return True
        if len(run) < MIN_RUN_MIDLINE:
            return False
        mid = sum(1 for t in run if _mid_line(text, t[0]))
        return mid * 2 >= len(run)

    runs, cur = [], []
    for tok in toks:
        if cur and 0 < tok[2] - cur[-1][2] <= 3:
            cur.append(tok)
        else:
            if qualifies(cur):
                runs.append(cur)
            cur = [tok]
    if qualifies(cur):
        runs.append(cur)
    return runs


def strip_inline_gutter(text: str) -> tuple[str, int]:
    """Delete inline gutter numbers found by inline_runs; return (text, n)."""
    cuts = [t for run in inline_runs(text) for t in run]
    if not cuts:
        return text, 0
    indented = bool(re.search(r"(?m)^[ \t]+\S", text))
    out, prev = [], 0
    for start, end, _ in cuts:
        head = text[prev:start]
        # "cross-150 bedded" -> "cross-bedded": the number broke a hyphenated
        # word, so swallow the trailing space and close the word back up.
        if head.endswith("-") and text[end:end + 1] == " ":
            out.append(head)
            prev = end + 1
        else:
            out.append(head)
            prev = end
    out.append(text[prev:])
    joined = re.sub(r" {2,}", " ", "".join(out))
    # A number sitting at the start of a line leaves its separator behind.
    # Only safe to trim when the file has no meaningful indentation to lose.
    if not indented:
        joined = re.sub(r"(?m)^[ \t]+(?=\S)", "", joined)
    return joined, len(cuts)


def is_scrambled(toks: list[str]) -> bool:
    """Vertical-text cell: many fragments, all very short.

    Evaluated on the SUBSTANTIVE tokens (watermark letters already dropped) —
    a cell is only unrecoverable if what's left is still short-and-many. A cell
    that looked scrambled purely because the watermark interleaved single
    capitals through it is recoverable, and must not be written off.
    """
    if len(toks) < 4:
        return False
    return sum(len(t) for t in toks) / len(toks) < 12


def clean_cell(cell: str) -> tuple[str, int]:
    """Drop watermark letters anywhere in the cell; return (text, n_removed).

    The rotated "ACCEPTED MANUSCRIPT" runs down the page edge, so marker
    interleaves its single capitals through the cell, not just at the front:

        | 331 | R<br>indication of the topography…<br>C<br>S<br>U<br>N<br>A |

    Splitting on <br> and dropping the lone AM-alphabet capitals recovers the
    prose. Surviving fragments are continuation lines of one physical sentence,
    so they rejoin with a space. Cells arrive with the table's alignment padding
    attached — strip BEFORE matching, or anchored patterns silently fail.
    """
    cell = cell.strip()
    toks = [t.strip() for t in cell.split("<br>")]
    keep = [t for t in toks if t and not (len(t) == 1 and t in "ACEDPTMNUSRI")]
    removed = len([t for t in toks if t]) - len(keep)
    if is_scrambled(keep):
        return cell, -1                       # signal: leave the row alone
    return re.sub(r"\s{2,}", " ", " ".join(keep)).strip(), removed


def pdf_marker(key: str) -> str | None:
    """Manuscript cover marker in the SOURCE PDF, if there is one.

    The markdown-side gate misses whole classes of line-numbered manuscript:
    Elsevier "Journal Pre-proof" and "made available under the Elsevier user
    license" covers carry no ACCEPTED MANUSCRIPT string, and any cover that was
    already removed by strip_frontmatter.py leaves no trace at all. The PDF
    still has it, so fall back to that before refusing.
    """
    try:
        import pypdfium2 as pdfium
        from strip_pdf_gutter_numbers import MARKERS, PDF_DIR
    except ImportError:
        return None
    pdf = PDF_DIR / f"{key}.pdf"
    if not pdf.exists():
        return None
    doc = pdfium.PdfDocument(str(pdf))
    head = "".join(doc[i].get_textpage().get_text_bounded()
                   for i in range(min(len(doc), 6)))
    return next((m for m in MARKERS if re.search(m, head, re.I)), None)


def process(path: Path, args):
    text = path.read_text(errors="replace", encoding="utf-8")
    lines = text.split("\n")

    gutter_rows = sum(1 for l in lines if (GUTTER.match(l) and gutter_values(GUTTER.match(l).group(1)))
                       or WM_LEAD_ROW.match(l))
    wm_hits = sum(1 for l in lines if WM_IN_CELL.search(l))
    looks_am = bool(AM.search(text)) or wm_hits > 0
    if not looks_am:
        looks_am = bool(pdf_marker(key_from_path(path)))
    if not looks_am:
        return None, "not an accepted manuscript (no cover marker in markdown or PDF)"
    if gutter_rows < args.min_rows:
        return None, f"only {gutter_rows} gutter rows (< --min-rows {args.min_rows})"

    out, stats = [], {"gutter": 0, "single": 0, "sep": 0, "wm": 0,
                      "scrambled": 0, "kept_table": 0, "inline": 0}
    for l in lines:
        if SEP2.match(l):
            stats["sep"] += 1
            continue
        if WM_ROW.match(l):
            stats["wm"] += 1
            continue
        m = GUTTER.match(l)
        if m and not gutter_values(m.group(1)):
            m = None                              # year-keyed table, not gutter
        m = m or WM_LEAD_ROW.match(l)
        if m:
            txt, n = clean_cell(m.group(m.lastindex))
            if n < 0:                             # word order lost; only re-OCR helps
                stats["scrambled"] += 1
                out.append(l)
                continue
            stats["wm"] += n
            stats["gutter"] += 1
            if txt and not WM_ONLY.match(txt):
                out.append(txt)
            continue
        m = SINGLE.match(l)
        if m and not l.lstrip().startswith("|--"):
            txt, n = clean_cell(m.group(1))
            if n < 0:
                stats["scrambled"] += 1
                out.append(l)
                continue
            stats["wm"] += n
            stats["single"] += 1
            if txt and not WM_ONLY.match(txt):
                out.append(txt)
            continue
        if l.lstrip().startswith("|"):
            stats["kept_table"] += 1              # 3+ columns — genuine table
        out.append(l)

    new = re.sub(r"\n{3,}", "\n\n", "\n".join(out))
    new, stats["inline"] = strip_inline_gutter(new)
    if args.apply:
        write_markdown(path, new)
    return (stats, len(text) - len(new)), None


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--key", type=key_arg, action="append", required=True)
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--min-rows", type=int, default=15)
    args = ap.parse_args()

    print(f"=== untable_gutter_manuscript — {'APPLY' if args.apply else 'DRY RUN'} ===")
    for k in args.key:
        p = MD_DIR / f"{k}.md"
        if not p.exists():
            print(f"  [warn] no such file: {p.name}", file=sys.stderr)
            continue
        res, why = process(p, args)
        if res is None:
            print(f"\n  {k}: REFUSED — {why}")
            continue
        s, delta = res
        print(f"\n  {k}  ({delta:,} bytes removed)")
        print(f"      gutter rows unwrapped : {s['gutter']}")
        print(f"      single-cell rows      : {s['single']}")
        print(f"      separator rows dropped: {s['sep']}")
        print(f"      watermark fragments   : {s['wm']}")
        print(f"      genuine table rows kept: {s['kept_table']}")
        print(f"      inline gutter numbers : {s['inline']}")
        if s["scrambled"]:
            print(f"      ⚠ scrambled cells LEFT AS-IS: {s['scrambled']} "
                  f"(vertical text; only re-OCR recovers word order)")
    if not args.apply:
        print("\nRe-run with --apply to write. Then re-index.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
