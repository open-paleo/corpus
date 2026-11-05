#!/usr/bin/env python3
# corpus-command: clean padded-tables | trim whitespace-padded table cells
"""Trim marker's whitespace-padded table cells in markdown/{key}.md.

Marker sometimes pads a table's cells out to thousands of spaces, giving a
table of hundreds of KB that is 99% whitespace.
Nothing is lost or duplicated (compare "Marker can blow one table up" in docs/notes/conversion.md, where
cells ARE duplicated and vision re-OCR is the fix); the file is just bloated
and unreadable. This rewrites every `|` row with its cells stripped, and
separator rows as `|---|`.

Safety: refuses to write unless the file's non-whitespace text (ignoring the
dashes of separator rows) is unchanged. Before running it, check that the
table's page is fully covered by the markdown.

Usage:
  ./.runtime/venv/bin/python scripts/clean_padded_tables.py --key KEY [--key KEY2] [--apply]
"""
import argparse
import re
import sys
from corpus.config import MD_DIR
from corpus.keys import key_arg
from corpus.pages import write_markdown

SEPARATOR_CELL = re.compile(r"\s*:?-+:?\s*")


def compact_line(line: str) -> str:
    if not line.lstrip().startswith("|"):
        return line
    cells = line.strip().strip("|").split("|")
    if any(c.strip() for c in cells) and all(SEPARATOR_CELL.fullmatch(c) for c in cells if c.strip()):
        return "|" + "|".join("---" for _ in cells) + "|"
    return "| " + " | ".join(c.strip() for c in cells) + " |"


def signature(text: str) -> str:
    return re.sub(r"\s", "", text).replace("-", "")


def largest_block(text: str) -> int:
    return max((len(b) for b in text.split("\n\n")), default=0)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--key", type=key_arg, action="append", required=True)
    ap.add_argument("--apply", action="store_true", help="write the files (default: dry run)")
    args = ap.parse_args(argv)
    rc = 0
    for key in args.key:
        path = MD_DIR / f"{key}.md"
        if not path.exists():
            print(f"  {key}: no markdown/{key}.md"); rc = 1; continue
        old = path.read_text(encoding="utf-8")
        new = "\n".join(compact_line(l) for l in old.split("\n"))
        if signature(new) != signature(old):
            print(f"  {key}: REFUSED, non-whitespace text would change"); rc = 1; continue
        print(f"  {key}: {len(old):,} -> {len(new):,} bytes; largest block "
              f"{largest_block(old):,} -> {largest_block(new):,}")
        if args.apply and new != old:
            write_markdown(path, new)
    if not args.apply:
        print("dry run; re-run with --apply to write")
    return rc


if __name__ == "__main__":
    sys.exit(main())
