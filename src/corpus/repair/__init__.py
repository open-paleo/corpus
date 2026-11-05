"""Text-layer repairs: fix markdown/{key}.md using what pdfs/{key}.pdf says.

Each module exposes `repair(doc, md, key, quiet=...) -> (new_md, stats)`: an
open PDF (corpus.pdf.text) and the markdown in, the repaired markdown out,
no file I/O. Its own `main()` is the per-repair command (`corpus repair dash`,
or `corpus repair dash`).

`run_all()` (`corpus repair all`) runs every repair on each paper in ONE pass:
the markdown is read once, the PDF is opened once and only if some repair
applies, the repairs run in REPAIRS order with each taking the previous one's
output, and the file is written once. Run separately, each repair re-extracts
every PDF's characters (~1 s per paper per repair).

control_glyphs is not in the pass: it needs a hand-made code map per paper.
"""
from __future__ import annotations

import argparse
import contextlib
import importlib
import io
import sys
from dataclasses import dataclass
from typing import Callable

from corpus.config import MD_DIR, PDF_DIR
from corpus.keys import add_key_args, key_from_path, resolve_keys
from corpus.manifest import load as manifest_load
from corpus.pages import write_markdown


def _tool(entry: dict) -> str | None:
    return (entry.get("conversion") or {}).get("tool")


@dataclass(frozen=True)
class Repair:
    name: str
    module: str
    # Same scope as the module's own --all: (key, manifest entry, markdown) -> bool
    eligible: Callable[[str, dict, str], bool]
    summary: str

    def load(self):
        return importlib.import_module(f"corpus.repair.{self.module}")


def _accents_eligible(key: str, entry: dict, md: str) -> bool:
    from corpus.repair.accents import eligible
    return eligible(key)


def _diaeresis_eligible(key: str, entry: dict, md: str) -> bool:
    from corpus.repair.diaeresis import eligible
    return eligible(entry, md)


def _ref_links_eligible(key: str, entry: dict, md: str) -> bool:
    from corpus.repair.ref_links import GLUED
    return bool(GLUED.search(md))


# Order matters only where two repairs touch the same characters; the pass
# gives the same result as running the per-repair commands in this order.
REPAIRS: list[Repair] = [
    Repair("dash", "dash", lambda k, e, md: True,
           "Elsevier dash font: en/em dashes extracted as letters"),
    Repair("symbols", "symbols", lambda k, e, md: True,
           "symbol glyphs the text layer dropped as control characters"),
    Repair("accents", "accents", _accents_eligible,
           "accents drawn by a companion font (identified papers only)"),
    Repair("diaeresis", "diaeresis", _diaeresis_eligible,
           "umlauts extracted as the euro sign"),
    Repair("ref_links", "ref_links", _ref_links_eligible,
           "letters marker moved into reference-call links"),
    Repair("fused_calls", "fused_calls", lambda k, e, md: _tool(e) == "marker",
           "superscript reference calls fused onto the preceding word"),
]
BY_NAME = {r.name: r for r in REPAIRS}


def repair_paper(key: str, entry: dict, *, only: list[str] | None = None,
                 apply: bool = False, quiet: bool = False) -> dict[str, dict]:
    """Run the eligible repairs on one paper. Returns {repair name: stats}."""
    from corpus.pdf.text import open_pdf
    md_path, pdf_path = MD_DIR / f"{key}.md", PDF_DIR / f"{key}.pdf"
    if not (md_path.exists() and pdf_path.exists()):
        return {}
    md = md_path.read_text(encoding="utf-8")
    todo = [r for r in REPAIRS if (not only or r.name in only) and r.eligible(key, entry, md)]
    if not todo:
        return {}
    try:
        doc = open_pdf(pdf_path)
    except Exception as e:
        return {"error": {"message": f"{type(e).__name__}: {e}"}}
    stats: dict[str, dict] = {}
    new = md
    try:
        for r in todo:
            out = io.StringIO()
            with contextlib.redirect_stdout(out) if quiet else contextlib.nullcontext():
                new, st = r.load().repair(doc, new, key, quiet=quiet)
            stats[r.name] = st
    finally:
        doc.close()
    if apply and new != md:
        write_markdown(md_path, new)
    stats["_changed"] = {"changed": new != md}
    return stats


def run_all(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        prog="corpus repair all",
        description="Run every text-layer repair on each paper in one pass "
                    "(dry run unless --apply).")
    add_key_args(ap)
    ap.add_argument("--only", action="append", choices=list(BY_NAME),
                    help="run only these repairs (repeatable)")
    ap.add_argument("--apply", action="store_true", help="write the repaired markdown")
    ap.add_argument("--quiet", action="store_true", help="no per-edit lines, totals only")
    args = ap.parse_args(argv)

    manifest = manifest_load(track=False)
    keys = resolve_keys(args)
    if not keys:   # every paper with both a PDF and markdown
        keys = sorted(k for k in (key_from_path(p) for p in PDF_DIR.glob("*.pdf"))
                      if (MD_DIR / f"{k}.md").exists())
    totals = {r.name: 0 for r in REPAIRS}
    changed = errors = 0
    for i, key in enumerate(keys, 1):
        stats = repair_paper(key, manifest.get(key, {}), only=args.only,
                             apply=args.apply, quiet=args.quiet)
        if "error" in stats:
            errors += 1
            print(f"  ! {key}: {stats['error']['message']}", file=sys.stderr)
            continue
        for name, st in stats.items():
            if name in totals:
                totals[name] += st.get("applied", 0)
        changed += bool(stats.get("_changed", {}).get("changed"))
        if len(keys) > 250 and i % 250 == 0:
            print(f"  ...{i}/{len(keys)}", file=sys.stderr, flush=True)
    verb = "repaired" if args.apply else "repairable"
    print(f"\n{len(keys)} paper(s); {changed} {'changed' if args.apply else 'would change'}"
          + (f"; {errors} unreadable" if errors else ""))
    for r in REPAIRS:
        if not args.only or r.name in args.only:
            print(f"  {r.name:12} {totals[r.name]:6d} {verb}   {r.summary}")
    return 0
