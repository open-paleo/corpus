#!/usr/bin/env python3
"""Give back the letters marker's reference links took off the preceding word.

Marker turns a superscript reference call into a markdown link, but it puts the
opening bracket one or more characters too early, so the end of the word lands
inside the link text:

    Zhiluo Formatio[n50,](#page-13-20)      should be  Formation  + ref 50
    Guangdong Provinc[e18.](#page-13-8)     should be  Province   + ref 18
    Jeb Bugo[s4](#page-0-4)                 should be  Bugos      + ref 4
    Carballido et al[.2](#page-10-0)        should be  et al.     + ref 2

The PDF is not at fault — its own text layer reads `Guangdong Province18.` with
the word spelled correctly — and neither is the reference number. Only the
bracket boundary moved. But a reader (or anything mining these files) cannot
tell `Provinc` + link `e18.` from a word that genuinely ends in "Provinc", and
the word is then unsearchable: `search.py 'Province'` misses the sentence that
names it.

Identification is exact rather than heuristic, on two independent grounds:

  1. A reference call is digits. Link text that *starts* with letters and then
     runs into a digit is marker's boundary error every time — there is no
     reference named "s4".
  2. Every repair is checked against the PDF's own text layer before it is
     written. The text layer keeps the word and the call fused (`Province18.`),
     so the reconstructed string has to appear there. A proposal that does not
     verify is reported and skipped, never guessed at.

That second check is what makes a corpus-wide sweep safe: nothing is rewritten
on the strength of the pattern alone.

    corpus repair ref_links --audit            # what's damaged
    corpus repair ref_links --key KEY          # dry run
    corpus repair ref_links --key KEY --apply
    corpus repair ref_links --all --apply      # whole corpus

Two related marker defects are deliberately NOT touched here, because unlike
the above they are not recoverable by convention and they corrupt body text
rather than markup — they need a human to read the page:

  * the brackets interleaving through a word, `arya](#page-72-0)n[ska, 1977]`
  * a run of prose swallowed whole, `the follo[wing combination o](#page-6-0)`

`--audit` counts both so they stay visible.
"""
from __future__ import annotations

import argparse
import re
import sys

from corpus.pdf.text import open_pdf
from corpus.config import PDF_DIR, MD_DIR
from corpus.keys import key_from_path
from corpus.keys import key_arg
from corpus.pages import write_markdown

# A link whose text is glued to the end of the preceding word. The word is
# captured so the reconstruction can be anchored against the PDF text layer.
GLUED = re.compile(r"(?P<word>[A-Za-z]{1,40})\[(?P<text>[^\]\n]{1,24})\]\(#page-[0-9-]+\)")

# Link text that begins with letters (or the period of "et al.") and then runs
# into the digits of the reference call. The leading part belongs to the word.
STOLEN = re.compile(r"^(?P<stolen>[A-Za-z]{1,12}|\.)(?P<ref>[0-9][^\]]*)$")

# Author affiliation markers: link text that is only letters. "Tan[a]" is
# correct (a single marker); "Oter[oa]" stole the "o" off Otero. Ambiguous
# against a genuine two-affiliation call like "Smith[ab]", so it is gated.
AFFIL = re.compile(r"^[A-Za-z]{2,3}$")

# The two defects this script refuses to touch, counted for --audit only. Both
# are glued to the preceding word the same way, so a loose pattern would sweep
# in every legitimate multi-word citation link — hence the leading [A-Za-z].
INTERLEAVED = re.compile(r"\]\(#page-[0-9-]+\)[A-Za-z]\[[^\]\n]*\]\(#page-[0-9-]+\)")
PROSE_IN_LINK = re.compile(r"[A-Za-z]\[[a-z]+ [a-z]+(?: [a-z]+)*\]\(#page-[0-9-]+\)")


def layer(key: str) -> str | None:
    """The PDF's own text with all whitespace removed, for anchor matching."""
    pdf = PDF_DIR / f"{key}.pdf"
    if not pdf.exists():
        return None
    try:
        doc = open_pdf(pdf)
    except Exception:
        return None
    try:
        return layer_text(doc)
    finally:
        doc.close()


def layer_text(doc) -> str:
    """layer() for an open PDF."""
    return re.sub(r"\s+", "", "".join(doc.text(i) for i in range(len(doc))))


def proposals(text: str, affiliations: bool):
    """Yield (match, stolen, ref) for every link that took letters off a word."""
    for m in GLUED.finditer(text):
        body = m.group("text")
        hit = STOLEN.match(body)
        if hit:
            yield m, hit.group("stolen"), hit.group("ref")
        elif affiliations and AFFIL.match(body):
            # The last letter is the marker; everything before it is the word's.
            yield m, body[:-1], body[-1]


def process(key: str, apply: bool, affiliations: bool, quiet: bool) -> tuple[int, int]:
    """Repair markdown/{key}.md, verified against pdfs/{key}.pdf (written only
    with apply). The PDF is opened only if the markdown has a candidate."""
    path = MD_DIR / f"{key}.md"
    if not path.exists():
        return 0, 0
    text = path.read_text(encoding="utf-8")
    if not GLUED.search(text) or not any(True for _ in proposals(text, affiliations)):
        return 0, 0
    pdf = PDF_DIR / f"{key}.pdf"
    try:
        doc = open_pdf(pdf) if pdf.exists() else None
    except Exception:
        doc = None
    if doc is None:
        if not quiet:
            print(f"  ! {key}: no readable PDF — cannot verify, skipped")
        return 0, len(list(proposals(text, affiliations)))
    try:
        new, res = repair(doc, text, key, quiet=quiet, affiliations=affiliations)
    finally:
        doc.close()
    if apply and new != text:
        write_markdown(path, new)
    return res["applied"], res["unverified"]


def repair(doc, text: str, key: str, *, quiet: bool = False,
           affiliations: bool = False) -> tuple[str, dict]:
    """The repaired markdown and {"applied", "unverified"}, from an open PDF
    and its markdown. No file I/O."""
    if not GLUED.search(text):
        return text, {"applied": 0, "unverified": 0}
    found = list(proposals(text, affiliations))
    if not found:
        return text, {"applied": 0, "unverified": 0}
    pdf_text = layer_text(doc)

    edits, unverified = [], 0
    for m, stolen, ref in found:
        # The word and the reference call sit fused in the text layer. Anchor on
        # the leading digits of the call only: markdown escapes the closing
        # paren of "n26\)" and the trailing punctuation of a call often differs
        # from the PDF ("s35," is "35,36" there), but the word-plus-number join
        # is exactly the thing being asserted, so it is enough to check.
        digits = re.match(r"[0-9]*", ref).group(0)
        anchor = re.sub(r"\s+", "", m.group("word") + stolen + digits)
        if anchor not in pdf_text:
            unverified += 1
            if not quiet:
                print(f"  ? {key}: unverified, skipped — {m.group(0)[:46]!r}")
            continue
        edits.append((m, stolen, ref))

    if not edits:
        return text, {"applied": 0, "unverified": unverified}

    out, last = [], 0
    for m, stolen, ref in edits:
        out.append(text[last:m.start()])
        out.append(f"{m.group('word')}{stolen}[{ref}]({m.group(0).split('](')[1]}")
        last = m.end()
        if not quiet:
            print(f"  {key}")
            print(f"      -  {m.group('word')}[{m.group('text')}]")
            print(f"      +  {m.group('word')}{stolen}[{ref}]")
    out.append(text[last:])
    new = "".join(out)
    return new, {"applied": len(edits), "unverified": unverified}


def audit(keys: list[str], affiliations: bool) -> None:
    rows, inter, prose = [], 0, 0
    for key in keys:
        path = MD_DIR / f"{key}.md"
        text = path.read_text(encoding="utf-8")
        n = len(list(proposals(text, affiliations)))
        i, p = len(INTERLEAVED.findall(text)), len(PROSE_IN_LINK.findall(text))
        inter += i
        prose += p
        if n:
            rows.append((n, key))
    rows.sort(reverse=True)
    for n, key in rows[:30]:
        print(f"  {n:5d}  {key}")
    print(f"\n{sum(n for n, _ in rows)} stolen-letter link(s) over {len(rows)} file(s)")
    print(f"{inter} interleaved-bracket and {prose} prose-in-link site(s) — "
          "not repairable by convention, left alone")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--key", type=key_arg, action="append")
    ap.add_argument("--all", action="store_true", help="every key with a markdown")
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--audit", action="store_true", help="count the damage, change nothing")
    ap.add_argument("--affiliations", action="store_true",
                    help="also repair author affiliation markers (Oter[oa] -> Otero[a]); "
                         "ambiguous against a genuine two-affiliation call, off by default")
    ap.add_argument("--quiet", action="store_true", help="counts only, no per-edit lines")
    args = ap.parse_args()

    if args.all or args.audit:
        keys = sorted(key_from_path(p) for p in MD_DIR.glob("*.md") if not p.name.endswith(".original.md"))
        if args.key:
            keys = [k for k in keys if k in set(args.key)]
    elif args.key:
        keys = args.key
    else:
        ap.error("need --key, --all or --audit")

    if args.audit:
        audit(keys, args.affiliations)
        return 0

    total = unverified = files = 0
    for key in keys:
        n, u = process(key, args.apply, args.affiliations, args.quiet)
        total += n
        unverified += u
        files += bool(n)

    verb = "repaired" if args.apply else "repairable"
    print(f"\n{total} link(s) {verb} over {files} file(s)"
          + (f", {unverified} unverified and skipped" if unverified else ""))
    return 0


if __name__ == "__main__":
    sys.exit(main())
