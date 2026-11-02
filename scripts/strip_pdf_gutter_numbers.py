#!/usr/bin/env python3
"""Remove manuscript gutter line numbers that marker left INLINE in the prose.

Why this exists
---------------
`untable_gutter_manuscript.py` handles the pages marker rendered as tables
(`| 214 | text |`). But marker doesn't table every page — on the rest the gutter
number lands inside the sentence, where there is no markup to key off:

    …calcareous in its lower part and cross-150 bedded higher up (Thompson, 1975)

Guessing these from the markdown alone does not work. Two heuristics were tried
and both failed on real files:

  * local ascending runs — indistinguishable from an author-affiliation block
    ("5 National Museums…, 6 2073 Market Street…"), which must survive;
  * longest bounded-step increasing subsequence — on one paper it locks onto
    the numbered REFERENCE LIST (1..102, dense and contiguous) instead of the
    gutter (fragmented by figure pages). Stripping that would destroy the
    citation numbers.

So don't guess. The PDF states which numbers are gutter numbers: they sit at
the start of a line in its text layer. This script reads that sequence and
consumes it against the markdown IN ORDER, so a number is only removed if it
continues the document's own gutter progression. A reference list restarting at
1 late in the paper never lines up with a gutter that has reached ~1200, so it
is left alone.

Gate
----
Refuses unless the PDF really is a line-numbered manuscript:

  * a manuscript cover marker (see MARKERS) in the first pages — Elsevier
    ACCEPTED MANUSCRIPT / Journal Pre-proof / "made available under the
    Elsevier user license", Nature "accepted for publication", NRC "Draft";
    covers are often stripped from the markdown, so this reads the PDF; AND
  * >=30% of pages carrying >=8 leading-number lines; AND
  * >=40% of the PDF's gutter sequence actually matched in the markdown.

That last check is what stops a partial or mis-aligned match from firing. A text
layer that puts superscript citation markers at the line start ("1 47 and a")
does not reliably put the gutter first, so only a handful match and the script
refuses. Those files need vision re-OCR, not surgery.

Usage
-----
    ./.runtime/venv/bin/python scripts/strip_pdf_gutter_numbers.py --key KEY
    ./.runtime/venv/bin/python scripts/strip_pdf_gutter_numbers.py --key K --apply
"""

from __future__ import annotations

import argparse
import re
from pathlib import Path

import pypdfium2 as pdfium
from corpus.config import PDF_DIR, MD_DIR
from corpus.keys import key_arg
from corpus.io import atomic_write_text

LEAD = re.compile(r"^(\d{1,4})(?=\s|$)", re.M)
BARE = re.compile(r"(?<!\S)(\d{1,4})(?!\S)")
# The gutter number can land inside a hyphenated word that wrapped across the
# line break — "cross-150 bedded" for "cross-bedded". Anchored on a letter so
# numeric forms like "126/127" and "2002-2003" can't match.
HYPHEN = re.compile(r"(?<=[A-Za-z])-(\d{1,4})(?=\s)")
# Long manuscripts pass line 999, but cap below 1800 so publication years in
# citations can never be mistaken for a line number.
MAX_LINE = 1500

MARKERS = (
    r"ACCEPTED\s*MANUSCRIPT",
    r"Journal\s+Pre-?proofs?",
    r"made available under the Elsevier user license",
    r"accepted for publication",
    r"(?m)^\s*Draft\s*$",
)


def pdf_evidence(path: Path) -> tuple[list[int], int, int, str | None]:
    """(gutter sequence, pages with a gutter, total pages, cover marker seen)."""
    doc = pdfium.PdfDocument(str(path))
    seq: list[int] = []
    gutter_pages = 0
    head = ""
    for i in range(len(doc)):
        page = doc[i].get_textpage().get_text_bounded()
        if i < 6:
            head += page
        nums = [int(x) for x in LEAD.findall(page) if int(x) <= MAX_LINE]
        if len(nums) >= 8:
            gutter_pages += 1
        seq.extend(nums)
    marker = next((m for m in MARKERS if re.search(m, head, re.I)), None)
    # keep the monotonic backbone: extraction order can interleave stray
    # superscripts, and only an increasing spine is usable for ordered matching
    spine: list[int] = []
    for v in seq:
        if not spine or v > spine[-1]:
            spine.append(v)
    return spine, gutter_pages, len(doc), marker


def md_tokens(text: str) -> list[tuple[int, int, int]]:
    toks = [(m.start(1), m.end(1), int(m.group(1))) for m in BARE.finditer(text)]
    toks += [(m.start(1), m.end(1), int(m.group(1))) for m in HYPHEN.finditer(text)]
    return sorted(t for t in toks if t[2] <= MAX_LINE)


def match_gutter(text: str, seq: list[int], window: int = 60):
    """Consume the PDF's gutter sequence against the markdown, in order.

    The window tolerates numbers marker dropped (figure pages, tabled pages)
    without letting the pointer run away: a token only matches if it turns up
    within `window` places of where the sequence currently stands.
    """
    hits, ptr = [], 0
    for start, end, val in md_tokens(text):
        if ptr >= len(seq):
            break
        stop = min(ptr + window, len(seq))
        j = ptr
        while j < stop and seq[j] < val:
            j += 1
        if j < stop and seq[j] == val:
            hits.append((start, end, val))
            ptr = j + 1
    return hits


# A number standing in front of one of these is being used as a quantity, not a
# gutter mark: "a body size up to 12 meters" and "including 29 sauropod taxa"
# can match the expected gutter value and would otherwise be deleted.
UNIT_AFTER = re.compile(
    r"^\s*(?:m|mm|cm|km|kg|g|mA|Ma|myr|%|°|meters?|metres?|taxa|specimens?|"
    r"characters?|elements?|individuals?|vertebrae|teeth|genera|species)\b", re.I)


def density_filter(hits, max_gap: int = 200, max_step: int = 3):
    """Keep only hits with a neighbouring hit close by in BOTH text and value.

    Real gutter numbers recur every ~80-100 characters in a tight arithmetic
    run. A prose number that merely happens to equal the expected value stands
    alone, so requiring a neighbour removes it without touching the run.
    """
    keep = []
    for i, (pos, end, val) in enumerate(hits):
        for j in (i - 1, i + 1):
            if 0 <= j < len(hits):
                qpos, _, qval = hits[j]
                if abs(pos - qpos) <= max_gap and abs(val - qval) <= max_step:
                    keep.append((pos, end, val))
                    break
    return keep


def veto_quantities(text: str, hits):
    return [(p, e, v) for p, e, v in hits if not UNIT_AFTER.match(text[e:e + 16])]


def cut(text: str, hits) -> str:
    """Excise the matched tokens and nothing else.

    Repairs are strictly LOCAL to each cut site. Normalising whitespace and
    blank lines across the whole file would also reformat tables and spacing,
    changing far more text than the removed digits.
    """
    out, prev = [], 0
    for start, end, _ in hits:
        head = text[prev:start]
        after = text[end:end + 1]
        if head.endswith("-") and after == " ":
            # "cross-150 bedded" -> "cross-bedded": close the broken word up
            out.append(head)
            prev = end + 1
        elif head.endswith((" ", "\t")) and after in (" ", "\t"):
            # word 123 word -> word word: drop the number and one separator
            out.append(head)
            prev = end + 1
        elif head.endswith("\n") and after in (" ", "\t"):
            # line-initial number: take its trailing space too
            out.append(head)
            prev = end + 1
        else:
            out.append(head)
            prev = end
    out.append(text[prev:])
    return "".join(out)


def process(key: str, args):
    md, pdf = MD_DIR / f"{key}.md", PDF_DIR / f"{key}.pdf"
    if not md.exists():
        return None, "no markdown"
    if not pdf.exists():
        return None, "no PDF (gutter sequence is only knowable from the source)"

    seq, gutter_pages, pages, marker = pdf_evidence(pdf)
    if not marker:
        return None, "no manuscript cover marker in the PDF"
    if pages and gutter_pages / pages < 0.30:
        return None, f"only {gutter_pages}/{pages} pages carry a gutter"
    if len(seq) < 50:
        return None, f"gutter sequence too short ({len(seq)})"

    text = md.read_text(errors="replace", encoding="utf-8")
    raw = match_gutter(text, seq, args.window)
    hits = veto_quantities(text, density_filter(raw))
    guarded = len(raw) - len(hits)
    frac = len(hits) / len(seq)
    if frac < 0.40:
        return None, (f"only {len(hits)}/{len(seq)} ({frac:.0%}) of the gutter "
                      f"matched — sequence is unreliable, use vision re-OCR")

    new = cut(text, hits)
    if args.apply:
        atomic_write_text(md, new)
    return {"marker": marker, "pages": pages, "gutter_pages": gutter_pages,
            "seq": len(seq), "hits": len(hits), "frac": frac,
            "guarded": guarded, "bytes": len(text) - len(new)}, None


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--key", type=key_arg, action="append", required=True)
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--window", type=int, default=60)
    args = ap.parse_args()

    print(f"=== strip_pdf_gutter_numbers — {'APPLY' if args.apply else 'DRY RUN'} ===")
    failed = 0
    for key in args.key:
        res, why = process(key, args)
        if res is None:
            failed += 1
            print(f"\n  {key}: REFUSED — {why}")
            continue
        print(f"\n  {key}  ({res['bytes']:,} bytes removed)")
        print(f"      cover marker      : {res['marker']}")
        print(f"      gutter pages      : {res['gutter_pages']}/{res['pages']}")
        print(f"      numbers removed   : {res['hits']} of {res['seq']} "
              f"({res['frac']:.0%} of the PDF sequence)")
        print(f"      held back by guards: {res['guarded']}")
    if not args.apply:
        print("\nRe-run with --apply to write. Then re-index.")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
