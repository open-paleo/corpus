#!/usr/bin/env python3
# corpus-command: qc spillover | markdown holding neighbouring articles
"""Flag markdown that holds more than one work (neighbouring-article spillover).

A PDF cut out of a larger volume carries whatever else was printed on its
pages: the tail of the previous article, the head of the next, encyclopedia
cross-reference stubs. Marker and vision both convert all of it, the result is
fluent English, and every other QC script passes it. The damage is to search
and to citation: a quotation can be taken from the wrong paper.

This script only REPORTS; trim by hand after reading the headings. By default
it covers Nature and Science, plus orphan `z` keys with no citation title;
--chapters adds book chapters (encyclopedia entries among them) and
proceedings.

Signals, per file:
  titles   title-like `#`/`##` headings that are not the paper's own title
  lead     characters before the paper's own title heading
  recv     "Received D Month; accepted D Month" lines (one per Nature letter)
  refs     reference-list headings
  opener   heading followed by an ALL-CAPS author line (encyclopedia entries)
  stubs    "*see* OTHER ENTRY" cross-reference stubs

No single signal is proof. Read the listed headings: a foreign title is the
tell. Harmless single-signal hits include Nature's "Received ..." line rendered
as a heading, the Nature Portfolio reporting summary, "Supporting Online
Material", and monographs with a reference list per chapter.

    scripts/qc_spillover.py                 # Nature/Science keys + orphan stubs
    scripts/qc_spillover.py --chapters      # add book chapters / proceedings (noisy:
                                              # section headings and translated titles
                                              # score, so read the list, do not count it)
    scripts/qc_spillover.py --all           # every key with markdown (noisier still)
    scripts/qc_spillover.py --key KEY
    scripts/qc_spillover.py --min-score 1
"""
from __future__ import annotations

import argparse
import difflib
import re
import unicodedata
from corpus.config import ROOT
from corpus.manifest import load as manifest_load
from corpus.keys import key_arg

MANIFEST = ROOT / "manifest.json"
MARKDOWN = ROOT / "markdown"

MIN_TITLE = 0.75
LEAD_CHARS = 800
SECTION = re.compile(
    r"\**(methods?|references|acknowledg|supplementary|supporting|abstract|results|discussion|"
    r"introduction|systematic|author|competing|additional|data|online|reporting|received)",
    re.I,
)
RUNNING_HEAD = re.compile(r"^\W*(letters to nature|r ?eports?|nature|science|article|research)\W*$", re.I)
REFS = re.compile(
    r"(?im)^#{0,6}\s*\**\s*(references and notes|references cited|references|literature cited)\s*\**:?\s*$"
)
RECEIVED = re.compile(r"(?i)\breceived \d{1,2} \w+[;,]? (?:\d{4};? )?accepted \d{1,2} \w+")
OPENER = re.compile(r"(?m)^#{1,4} \*?[^\n*]+\*?\n\n[A-Z][A-Z .\-]{5,40} \*")
STUB = re.compile(r"(?m)^\*see\* [A-Z ]{6,}")
MAGAZINE = re.compile(r"^(nature|science)$", re.I)
CHAPTER_TYPES = {"incollection", "inbook", "inproceedings"}


def norm(s: str) -> str:
    s = unicodedata.normalize("NFKD", s or "").encode("ascii", "ignore").decode().lower()
    return re.sub(r"[^a-z0-9]+", " ", s).strip()


def scan(key: str, entry: dict) -> dict | None:
    path = MARKDOWN / f"{key}.md"
    if not path.exists():
        return None
    text = path.read_text(encoding="utf-8")
    citation = entry.get("citation") or {}
    title = norm(re.sub(r"\[.*?\]", "", citation.get("title") or ""))

    headings = [(m.start(), m.group(1)) for m in re.finditer(r"(?m)^#{1,3} (.+)$", text)]
    lead = None
    own = set()
    if title:
        scored = [(difflib.SequenceMatcher(None, title, norm(h)).ratio(), o, h) for o, h in headings]
        own = {h for r, _, h in scored if r >= MIN_TITLE}
        best = max(scored, default=(0, None, None))
        lead = best[1] if best[0] >= MIN_TITLE else -1

    foreign = [
        h for o, h in headings
        if h not in own and len(h) > 25
        and not SECTION.match(h.strip("* ")) and not RUNNING_HEAD.match(h)
        and not (title and difflib.SequenceMatcher(None, title, norm(h)).ratio() >= 0.5)
    ]
    counts = {
        "titles": len(foreign),
        "lead": lead,
        "recv": len(RECEIVED.findall(text)),
        "refs": len(REFS.findall(text)),
        "opener": len(OPENER.findall(text)),
        "stubs": len(STUB.findall(text)),
    }
    score = (
        (counts["titles"] > 0) + (lead is not None and (lead > LEAD_CHARS or lead == -1))
        + (counts["recv"] > 1) + (counts["refs"] > 1) + (counts["opener"] > 1) + (counts["stubs"] > 0)
    )
    return {"key": key, "score": score, "counts": counts, "foreign": foreign[:4],
            "venue": citation.get("journal") or citation.get("booktitle") or "(no citation)"}


def in_default_scope(entry: dict, chapters: bool) -> bool:
    citation = entry.get("citation") or {}
    if MAGAZINE.match((citation.get("journal") or "").strip()):
        return True
    if chapters and citation.get("entry_type") in CHAPTER_TYPES:
        return True
    return not citation.get("title")  # orphan stubs: nothing else checks them


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--key", type=key_arg, action="append")
    ap.add_argument("--chapters", action="store_true", help="also scan book chapters and proceedings")
    ap.add_argument("--all", action="store_true", help="scan every key with markdown")
    ap.add_argument("--min-score", type=int, default=2)
    args = ap.parse_args()

    manifest = manifest_load()
    if args.key:
        keys = list(args.key)            # key_arg: NFC and validated
    else:
        keys = [k for k, e in manifest.items() if args.all or in_default_scope(e, args.chapters)]

    rows = [r for k in sorted(keys) if (r := scan(k, manifest.get(k, {})))]
    hits = [r for r in rows if r["score"] >= (0 if args.key else args.min_score)]
    for r in sorted(hits, key=lambda r: (-r["score"], r["key"])):
        c = r["counts"]
        print(f"{r['score']}  {r['key']:24s} {r['venue'][:28]:28s} "
              f"titles={c['titles']} lead={c['lead']} recv={c['recv']} refs={c['refs']} "
              f"opener={c['opener']} stubs={c['stubs']}")
        for h in r["foreign"]:
            print(f"       ? {h[:90]}")
    print(f"\n{len(rows)} file(s) scanned, {len(hits)} at score >= {args.min_score}. "
          "Report only: read the headings before trimming.")


if __name__ == "__main__":
    main()
