#!/usr/bin/env python3
# corpus-command: qc quotes | check quotations in a findings write-up against the markdown they cite
"""Check quoted claims in an audit write-up against the markdown they cite.

Every audit findings file quotes the corpus. Those quotes have to be checked
mechanically, not by eye -- a plausible-looking paraphrase reads exactly like a
real quotation. This script does the check.

Two modes:

  # scan a findings file: pull out "..."-quoted spans and try each against the
  # keys named nearby (bare citation keys on the same line or the ones above it)
  ./.runtime/venv/bin/python scripts/verify_audit_quotes.py FINDINGS.md

  # explicit pairs, one per line, "key<TAB>quote" (or key|quote)
  ./.runtime/venv/bin/python scripts/verify_audit_quotes.py --pairs pairs.tsv

Matching strips ALL punctuation, diacritics and case before comparing, because
marker/vision OCR routinely mangles accents, dashes and quote glyphs.  A MISS is
therefore a real problem: either the quote is wrong, or it is from a paper we do
not hold under that key.

Exits non-zero if anything missed.
"""

from __future__ import annotations

import argparse
import pathlib
import re
import sys
import unicodedata
from corpus.keys import key_from_path

MARKDOWN_DIR = pathlib.Path("markdown")

# citation keys look like: surname + 4-digit year + optional letter.
# allow accented surnames (núñez2013a) and two-letter ones (li2014a, an2015z).
KEY_RE = re.compile(r"\b([a-zà-ÿ][a-zà-ÿ'\-]+\d{4}[a-z]?)\b")

# quoted spans: straight or curly doubles, non-greedy, at least a few words.
QUOTE_RE = re.compile(r"[\"“]([^\"“”]{25,600})[\"”]")

MIN_WORDS = 5

# A real quotation never crosses a blank line, a table row or a heading. Prose
# in a findings file has plenty of stray/unpaired quote marks (inline code,
# citation titles), and without these guards the scanner pairs an opener with
# some *later* span's closer and "quotes" half a paragraph.
JUNK_RE = re.compile(r"\n\s*\n|\||^#|\n#|---|\*\*")


# Marker splices its cross-reference machinery *inside* sentences:
#   "(Dong, [1992,](#page-15-0) [1993](#page-15-0))"  and  '<span id="page-7-0"></span>'
# Dropping punctuation alone leaves "page 15 0" tokens wedged mid-quotation, so a
# verbatim quote from such a paragraph never matches. Strip the link targets and
# HTML anchors before folding; they are never part of the text being quoted.
LINK_TARGET_RE = re.compile(r"\]\((?:#|https?://|mailto:)[^)]*\)")
HTML_TAG_RE = re.compile(r"<[^>\n]{0,200}>")


def norm(s: str) -> str:
    """Fold to comparable form: NFKD, drop everything but alphanumerics."""
    s = unicodedata.normalize("NFKD", s)
    s = LINK_TARGET_RE.sub("]", s)
    s = HTML_TAG_RE.sub(" ", s)
    s = re.sub(r"[^0-9A-Za-z]+", " ", s)
    return " ".join(s.split()).lower()


ELLIPSIS_RE = re.compile(r"\s*(?:…|\.\.\.)\s*")


def contains(body: str, quote: str) -> bool:
    """Is `quote` present in normalised `body`?

    An elided quote ("A ... B") is satisfied when every fragment appears, in
    order -- that is what the ellipsis asserts and nothing more. Fragments
    shorter than three words are dropped: they carry no evidential weight and
    would match almost anything.
    """
    frags = [
        norm(f) for f in ELLIPSIS_RE.split(quote) if len(norm(f).split()) >= 3
    ]
    if not frags:
        return False
    pos = 0
    for frag in frags:
        pos = body.find(frag, pos)
        if pos < 0:
            return False
        pos += len(frag)
    return True


class Corpus:
    def __init__(self, root: pathlib.Path) -> None:
        self.root = root
        self._cache: dict[str, str | None] = {}

    def get(self, key: str) -> str | None:
        if key not in self._cache:
            path = self.root / f"{key}.md"
            self._cache[key] = (
                norm(path.read_text(errors="ignore", encoding="utf-8")) if path.exists() else None
            )
        return self._cache[key]

    def find_anywhere(self, quote: str, limit: int = 3) -> list[str]:
        """Which key(s) in the whole corpus contain this quote?

        Only worth running on a miss. A hit here means the quote is real but
        attributed to the wrong paper -- a very different problem from a
        fabricated quotation, and the caller should say which it is.
        """
        hits: list[str] = []
        for path in sorted(self.root.glob("*.md")):
            key = key_from_path(path)
            body = self._cache.get(key)
            if body is None and key not in self._cache:
                body = norm(path.read_text(errors="ignore", encoding="utf-8"))
                self._cache[key] = body
            if body and contains(body, quote):
                hits.append(key)
                if len(hits) >= limit:
                    break
        return hits


def scan_findings(path: pathlib.Path) -> list[tuple[list[str], str, int]]:
    """Return (candidate_keys, quote, line_no) for each quoted span.

    Scanning is document-wide, not line-by-line: prose wraps, so a quotation
    routinely straddles a newline. Candidate keys are those appearing in the
    ~2500 characters up to and including the quote, nearest first -- audit prose
    attributes a quote either inline or in the sentence/table row just above it.
    """
    text = path.read_text(encoding="utf-8")
    out: list[tuple[list[str], str, int]] = []
    for m in QUOTE_RE.finditer(text):
        raw = m.group(1)
        if JUNK_RE.search(raw):
            continue
        quote = " ".join(raw.split())
        if len(quote.split()) < MIN_WORDS or not quote[0].isalpha() and quote[0] != "…":
            continue
        window = text[max(0, m.start() - 2500) : m.end()]
        keys: list[str] = []
        for k in reversed(KEY_RE.findall(window)):
            if k not in keys:
                keys.append(k)
        out.append((keys, quote, text.count("\n", 0, m.start()) + 1))
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("findings", nargs="?", help="audit write-up to scan")
    ap.add_argument("--pairs", help="TSV/pipe file of key<TAB>quote pairs")
    ap.add_argument(
        "--markdown-dir", default=str(MARKDOWN_DIR), help="corpus markdown dir"
    )
    ap.add_argument("-v", "--verbose", action="store_true", help="print hits too")
    ap.add_argument(
        "--no-rescue",
        action="store_true",
        help="skip the corpus-wide search that locates a missed quote elsewhere",
    )
    args = ap.parse_args()

    corpus = Corpus(pathlib.Path(args.markdown_dir))
    checks: list[tuple[list[str], str, int]] = []

    if args.pairs:
        for n, line in enumerate(pathlib.Path(args.pairs).read_text(encoding="utf-8").splitlines(), 1):
            if not line.strip() or line.lstrip().startswith("#"):
                continue
            key, _, quote = line.partition("\t" if "\t" in line else "|")
            checks.append(([key.strip()], quote.strip(), n))
    elif args.findings:
        checks = scan_findings(pathlib.Path(args.findings))
    else:
        ap.error("give a findings file or --pairs")

    misses = unheld = misattributed = 0
    for keys, quote, line_no in checks:
        hit = next(
            (k for k in keys if (b := corpus.get(k)) and contains(b, quote)), None
        )
        if hit:
            if args.verbose:
                print(f"  ok   L{line_no} {hit}: {quote[:70]}")
            continue
        if keys and all(corpus.get(k) is None for k in keys):
            unheld += 1
            print(f"UNHELD L{line_no} {','.join(keys[:3])}: {quote[:80]}")
        else:
            elsewhere = [] if args.no_rescue else corpus.find_anywhere(quote)
            if elsewhere:
                misattributed += 1
                print(f"ELSEWHERE L{line_no} found in {','.join(elsewhere)}")
            else:
                misses += 1
                print(f"MISS   L{line_no} tried {','.join(keys[:4]) or '(no key)'}")
            print(f"       {quote[:160]}")

    matched = len(checks) - misses - unheld - misattributed
    print(
        f"\nchecked {len(checks)} quote(s): {matched} matched, "
        f"{misattributed} found under a key the prose did not name nearby, "
        f"{misses} not found anywhere in the corpus, "
        f"{unheld} cited to keys not in the corpus"
    )
    return 1 if (misses or unheld) else 0


if __name__ == "__main__":
    sys.exit(main())
