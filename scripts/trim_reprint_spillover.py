#!/usr/bin/env python3
# corpus-command: clean spillover | cut neighbouring articles out of a Nature/Science reprint's markdown
"""Trim neighbouring articles out of a Nature/Science reprint PDF's markdown.

A single-article reprint ordered from Nature carries whatever else shared those
sheets: the tail of the preceding paper before the target's title, and the head
of the following one after its Correspondence line. Marker converts all of it,
so `markdown/{key}.md` silently holds two or three unrelated papers.

Nothing in the QC chain notices -- the file is well formed, the right length for
its page count, and its own paper is complete and correct. The damage is to
search: a query for a neighbour's subject (a chameleon, seismic anisotropy)
returns the dinosaur paper that happened to share its sheets. `corpus qc
spillover` finds these files; this script trims the Nature-style ones.

A Nature letter has a fixed structure that makes the boundaries exact:

    # <title>                       <- target starts here
    <authors, affiliations, abstract, body, figures, references>
    Acknowledgements
    Competing interests statement
    Correspondence and requests ... addressed to <initials>    <- target ends
    # <next article's title>

So the target runs from the heading matching the manifest citation title to the
first Correspondence line after it. Two gates keep that honest, and the script
refuses the file rather than guessing if either fails:

  * the heading must match the manifest title (SequenceMatcher >= MIN_TITLE);
  * the Correspondence line must be addressed to one of the authors, which is
    what proves the line closes *this* paper and not a neighbour.

Each side is then trimmed only on its own evidence of a neighbour, so a modern
open-access paper that merely ends the same way keeps its back matter.

The following article's heading is sometimes `##` rather than `#`, so heading
level is not used as a signal. Page markers stay: the page where the paper
starts keeps its marker above the title, and a page holding only a neighbour
keeps its marker with no text, since the PDF still has the page.

    scripts/trim_reprint_spillover.py --all --report
    scripts/trim_reprint_spillover.py --key KEY           # dry run
    scripts/trim_reprint_spillover.py --key KEY --apply
"""
from __future__ import annotations

import argparse
import difflib
import re
import sys

from corpus.config import MD_DIR
from corpus.manifest import load as manifest_load
from corpus.keys import key_from_path
from corpus.keys import read_keys_file
from corpus.keys import key_arg
from corpus.pages import MARKER, restore_dropped, write_markdown

CORRESPONDENCE = "Correspondence and requests"
MIN_TITLE = 0.90


def norm(s: str) -> str:
    return re.sub(r"[^a-z0-9 ]", "", s.lower()).strip()


def addressee_tokens(author_field: str):
    """Every form the Correspondence line might use to name one of the authors.

    The corresponding author is often not the first (a letter addressed to the
    second or third author's initials), so every author contributes a token. Modern papers spell the name out in full
    ("addressed to Lucio M. Ibiricu"), hence the surname as well as the
    initials. Surnames shorter than four letters are dropped -- a two-letter
    surname matches almost any run of capitals.
    """
    tokens = set()
    for part in author_field.split(";"):
        part = part.strip()
        if "," not in part:
            continue
        surname, given = (x.strip() for x in part.split(",", 1))
        ini = re.sub(r"[^A-Za-z]", "", given).upper()
        sur = re.sub(r"[^A-Za-z]", "", surname).upper()
        if ini and sur:
            tokens.add(ini + sur[:1])
        if len(sur) >= 4:
            tokens.add(sur)
    return tokens


def find_bounds(lines, title, tokens):
    """Return (start, end, note) as 0-based inclusive indices, or (None, None, why)."""
    best, ratio = None, 0.0
    ntitle = norm(title)
    for i, l in enumerate(lines):
        if l.startswith("#"):
            r = difflib.SequenceMatcher(None, ntitle, norm(l.lstrip("# "))).ratio()
            if r > ratio:
                best, ratio = i, r
    if best is None or ratio < MIN_TITLE:
        return None, None, "no heading matches the manifest title (best %.2f)" % ratio

    end = None
    for i in range(best + 1, len(lines)):
        if lines[i].startswith(CORRESPONDENCE):
            end = i
            break
    if end is None:
        return None, None, "no Correspondence line after the title"

    # The addressee proves this line closes the target paper.
    addressed = re.sub(r"[^A-Za-z]", "", lines[end].split("addressed to")[-1][:40]).upper()
    if tokens and not any(t in addressed for t in tokens):
        return None, None, "Correspondence addressed to %s, no author matches" % addressed[:16]

    # Each side is trimmed only on its own evidence of a neighbouring article.
    # A modern open-access paper can also end with a Correspondence
    # line, but what follows is its own back matter -- peer review information,
    # the licence, the copyright line -- and deleting that would be a straight
    # loss. A real neighbour opens with a heading. Likewise the material before
    # the target is another paper's tail only if it closes with a Correspondence
    # line of its own; a bare journal or DOI header is the target's own.
    trim_after = any(l.startswith("#") for l in lines[end + 1:])
    trim_before = any(l.startswith(CORRESPONDENCE) for l in lines[:best])
    if not trim_before:
        best = 0
    if not trim_after:
        end = len(lines) - 1
    return best, end, "ok (title %.2f, before=%s after=%s)" % (
        ratio, "yes" if trim_before else "no", "yes" if trim_after else "no")


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--key", type=key_arg)
    g.add_argument("--keys-file")
    g.add_argument("--all", action="store_true")
    ap.add_argument("--apply", action="store_true", help="write changes (default: dry run)")
    ap.add_argument("--report", action="store_true", help="show bounds, change nothing")
    args = ap.parse_args()

    manifest = manifest_load()

    if args.key:
        keys = [args.key]
    elif args.keys_file:
        keys = read_keys_file(args.keys_file)
    else:
        keys = sorted(
            key_from_path(p)
            for p in MD_DIR.glob("*.md")
            if not p.name.endswith(".original.md")
            and p.read_text(encoding="utf-8").count(CORRESPONDENCE) > 1
        )

    acted = refused = 0
    for k in keys:
        p = MD_DIR / ("%s.md" % k)
        if not p.exists():
            print("  !! %s: no markdown" % k)
            continue
        entry = manifest.get(k, {})
        title = entry.get("citation", {}).get("title", "")
        if not title:
            print("  !! %s: no citation title in manifest" % k)
            refused += 1
            continue

        text = p.read_text(encoding="utf-8")
        lines = text.splitlines()
        tokens = addressee_tokens(entry.get("citation", {}).get("author", ""))
        start, end, why = find_bounds(lines, title, tokens)
        if start is None:
            print("  !! %s: refused -- %s" % (k, why))
            refused += 1
            continue

        before, after = start, len(lines) - (end + 1)
        if not before and not after:
            continue
        print(
            "  %s %s: keep %d-%d of %d  (drop %d before, %d after)  %s"
            % ("->" if args.apply else "(dry)", k, start + 1, end + 1,
               len(lines), before, after, why)
        )
        if args.report:
            for i in (start, end):
                print("       L%-4d %s" % (i + 1, lines[i][:88]))
            acted += 1
            continue

        kept = lines[start:end + 1]
        # The page the paper starts on keeps its marker: the last one above the title.
        opener = next((line for line in reversed(lines[:start]) if MARKER.fullmatch(line)), None)
        if opener and not (kept and MARKER.fullmatch(kept[0])):
            kept = [opener, ""] + kept
        new = restore_dropped(text, "\n".join(kept) + "\n")
        if args.apply and not write_markdown(p, new, old=text):
            refused += 1
            continue
        acted += 1

    verb = "trimmed" if args.apply else "would trim"
    print("\n%s: %d    refused: %d" % (verb, acted, refused))
    return 0


if __name__ == "__main__":
    sys.exit(main())
