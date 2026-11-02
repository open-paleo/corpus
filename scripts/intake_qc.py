#!/usr/bin/env python3
# corpus-command: intake qc | bytes-per-page triage of a converted batch
"""
Bytes-per-page sanity check on a batch of just-converted keys, plus
flagging of likely image-only stubs and T&F cover-stub patterns. Run
after marker (or vision OCR) has produced markdown to triage which keys
need an OCR escalation.

Triage rules (from docs/notes/conversion.md, "Stub markdown"):

  - 0 KB or <200 B/page over >10 pages → image-only scan
    (set conversion.vision_ocr_needed=True and run convert_with_vision.py)
  - <1000 B/page but page count plausibly real for a short paper →
    eyeball the markdown body. Could be a genuinely-short 1900s paper,
    a T&F cover-boilerplate stub, or marker CID-garbage. Searches for
    T&F-stub markers ("PLEASE SCROLL DOWN FOR ARTICLE", "Informa Ltd",
    "Mortimer Street") and reports any hits.
  - >=1000 B/page → looks healthy, no flag.

Examples:

  scripts/intake_qc.py KEY [KEY ...]
  scripts/intake_qc.py --keys-file /tmp/intake_keys.txt
  scripts/intake_qc.py --json    # machine-readable
"""
from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass, asdict
from corpus.config import MD_DIR
from corpus.manifest import load as manifest_load
from corpus.keys import read_keys_file
from corpus.keys import key_arg

TF_STUB_MARKERS = [
    "PLEASE SCROLL DOWN FOR ARTICLE",
    "Informa Ltd",
    "Mortimer Street",
    "This article was downloaded by",
]


@dataclass
class QcRow:
    key: str
    pages: int
    md_kb: float
    bpp: float
    tool: str | None
    flag: str           # "ok" | "image_only" | "likely_stub" | "tf_cover_stub"
    note: str


def load_keys(args) -> list[str]:
    keys: list[str] = list(args.keys or [])
    if args.keys_file:
        keys.extend(read_keys_file(args.keys_file))
    if not keys:
        print("error: no keys given (positional or --keys-file)", file=sys.stderr)
        sys.exit(2)
    seen: set[str] = set()
    out: list[str] = []
    for k in keys:
        if k not in seen:
            seen.add(k)
            out.append(k)
    return out


def qc_one(key: str, manifest: dict) -> QcRow:
    entry = manifest.get(key, {}) or {}
    conv = entry.get("conversion") or {}
    tool = conv.get("tool")
    pages = conv.get("page_count") or 0
    md = MD_DIR / f"{key}.md"
    sz = md.stat().st_size if md.exists() else 0
    bpp = (sz / pages) if pages else 0.0

    flag = "ok"
    notes: list[str] = []

    if not md.exists():
        flag = "image_only"
        notes.append("no markdown")
    elif sz == 0:
        flag = "image_only"
        notes.append("0-byte markdown — image-only scan likely")
    elif pages > 10 and bpp < 200:
        flag = "image_only"
        notes.append(f"{bpp:.0f} B/page over {pages} pages — image-only scan likely")
    elif bpp < 1000 and pages > 1:
        # Inspect the body for T&F cover-stub markers
        try:
            sample = md.read_text(errors="replace", encoding="utf-8")[:6000]
        except OSError:
            sample = ""
        hits = [m for m in TF_STUB_MARKERS if m in sample]
        if hits:
            flag = "tf_cover_stub"
            notes.append(f"T&F cover-stub markers present: {', '.join(hits)}")
        else:
            flag = "likely_stub"
            notes.append(f"low B/page ({bpp:.0f}) — eyeball the body before declaring ok")

    return QcRow(
        key=key, pages=pages, md_kb=sz / 1024.0, bpp=bpp,
        tool=tool, flag=flag, note="; ".join(notes),
    )


def print_table(rows: list[QcRow]) -> int:
    print(f"{'key':28s} {'tool':14s} {'pages':>5s} {'KB':>7s} {'B/p':>5s}  {'flag':14s}  {'note'}")
    print("-" * 90)
    nonok = 0
    for r in rows:
        if r.flag != "ok":
            nonok += 1
        print(f"{r.key:28s} {str(r.tool or '-'):14s} {r.pages:5d} {r.md_kb:7.1f} {r.bpp:5.0f}  {r.flag:14s}  {r.note}")
    if nonok:
        print()
        print(f"=== {nonok} key(s) flagged for follow-up ===")
        print("Recommended next step:")
        print("  for image_only / tf_cover_stub: rerun via /intake §7")
        print("  (set conversion.vision_ocr_needed=True; run convert_with_vision.py)")
        print("  or pass `--vision-ocr REASON` to scripts/intake_acquire.py upfront")
    return nonok


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("keys", type=key_arg, nargs="*", help="citation keys to QC")
    ap.add_argument("--keys-file", help="file with one key per line (#-comments OK)")
    ap.add_argument("--json", action="store_true",
                    help="emit JSON to stdout instead of a table")
    args = ap.parse_args()
    keys = load_keys(args)
    manifest = manifest_load()

    rows = [qc_one(k, manifest) for k in keys]

    if args.json:
        json.dump([asdict(r) for r in rows], sys.stdout, indent=2)
        sys.stdout.write("\n")
        return 1 if any(r.flag != "ok" for r in rows) else 0
    return 1 if print_table(rows) else 0


if __name__ == "__main__":
    sys.exit(main())
