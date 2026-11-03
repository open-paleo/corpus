#!/usr/bin/env python3
# corpus-command: qc translations | translations that lost content (short or cut-off chunks, model artefacts)
"""Audit translated markdown for silently lost content.

`translate_markdown.py` cuts a paper into chunks, translates each and joins the
results. A chunk that stops on the output cap, or comes back with a passage
missing, still joins into a file that reads as a finished translation. The
translator guards the whole file (it refuses one under half the original's
length) and retries a non-CJK chunk of over 2,000 characters that comes back
under 80% of its source; CJK chunks, short chunks and smaller losses pass.

This script checks three things per key:

  1. **Cut-off chunks**: cache entries with `stop_reason == "max_tokens"`, and
     entries without that field whose `output_tokens` reached the 8,192 cap.
  2. **Per-chunk length**: the source is re-chunked exactly as the translator
     chunked it (corpus.translate), so each cached translation is measured
     against the text it came from. The comparison is against an *expected*
     length: Latin text (reference lists, captions) passes through at about
     1.0, while a CJK character becomes several English ones, and that
     expansion is fitted from the file's own chunks rather than assumed (it
     runs about 2.2 for Korean and 3.3 for Chinese; a fixed constant makes the
     reference-heavy chunks of a Chinese paper look cut short when they are
     not). A chunk well below its expectation lost content even if it never
     reported a cut-off. One case the ratio cannot see through: where the
     source is itself bilingual (captions printed as `中文［English］`), a
     correct translation drops one half and comes in short, so read a flagged
     chunk's head and tail before re-running anything.
  3. **Model artefacts**: false starts ("Wait, I need to...", "Let me redo"),
     refusal and failure markers, and lines of untranslated source script.

Usage:
    scripts/audit_translation_completeness.py              # every translated key
    scripts/audit_translation_completeness.py --key KEY
    scripts/audit_translation_completeness.py --verbose    # per-chunk table

Exits non-zero if any key is flagged.
"""
from __future__ import annotations

import argparse
import glob
import json
import re
import statistics
from pathlib import Path

from corpus.config import MD_DIR
from corpus.keys import key_arg
from corpus.manifest import load as manifest_load
from corpus.pages import strip
from corpus.translate import (CACHE_DIR, CJK_LANGS, MAX_CHARS_PER_CHUNK, MAX_CHARS_PER_CHUNK_CJK,
                              chunk_cache_key, chunk_plan)

OUTPUT_CAP = 8190  # entries without stop_reason: max_tokens=8192, minus slack
RATIO_FLOOR = 0.75  # chunk ratio as a fraction of the file's median
CJK_EXPANSION_FALLBACK = 2.6  # used only when a file has too little CJK to fit
SOURCE_SCRIPTS = re.compile(r"[一-鿿぀-ヿ가-힯Ѐ-ӿ]")
CJK_CHARS = re.compile(r"[一-鿿぀-ヿ가-힯]")
ARTIFACTS = [
    re.compile(r"Wait, I need to", re.I),
    re.compile(r"Let me (redo|start over|try again)", re.I),
    re.compile(r"^I (cannot|can't|will not) translate", re.I | re.M),
    re.compile(r"\[TRANSLATION (FAILED|REFUSED)"),
]


def cache_entries(key: str) -> list[dict]:
    out = []
    for f in sorted(glob.glob(str(CACHE_DIR / key / "chunk-*.json"))):
        # chunk-NNNN.short.json is a rejected attempt the translator kept after
        # retrying the chunk; only chunk-NNNN.json is what the file holds
        if not Path(f).name.count(".") == 1:
            continue
        try:
            d = json.loads(Path(f).read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        d["_file"] = Path(f).name
        out.append(d)
    return out


def is_truncated(entry: dict) -> bool:
    if entry.get("stop_reason") == "max_tokens":
        return True
    # Entries without stop_reason: reaching the cap is the tell.
    return "stop_reason" not in entry and entry.get("output_tokens", 0) >= OUTPUT_CAP


def audit_key(key: str, src_lang: str | None, verbose: bool) -> dict:
    orig = MD_DIR / f"{key}.original.md"
    cur = MD_DIR / f"{key}.md"
    res = {"key": key, "flags": [], "chunks": 0, "worst": None}
    if not orig.exists() or not cur.exists():
        res["flags"].append("no-original")
        return res

    body = orig.read_text(encoding="utf-8")
    translated = cur.read_text(encoding="utf-8")

    for rx in ARTIFACTS:
        if rx.search(translated):
            res["flags"].append("artifact")
            break

    # Count source-script characters only on lines that carry no real English.
    # Retained bilingual matter — a masthead, a funding note, a figure caption
    # printed in both languages — is normal and would otherwise dominate this
    # count; a run of source-script lines with no English beside them is not.
    src_chars = sum(
        len(SOURCE_SCRIPTS.findall(line))
        for line in translated.splitlines()
        if len(re.findall(r"[A-Za-z]", line)) < 20
    )
    if src_chars > 400:
        res["flags"].append(f"source-script({src_chars})")

    entries = cache_entries(key)
    res["chunks"] = len(entries)
    if not entries:
        res["flags"].append("no-cache")
        return res

    if any(is_truncated(e) for e in entries):
        res["flags"].append("truncated")

    # Re-chunk the source the way the translator would, so each cached
    # translation can be measured against the text it was produced from.
    max_chars = MAX_CHARS_PER_CHUNK_CJK if (src_lang or "") in CJK_LANGS else MAX_CHARS_PER_CHUNK
    # A translation made before the original had page markers was chunked from
    # the text without them, which strip() gives back exactly.
    chunks = chunk_plan(body, max_chars)[0] + chunk_plan(strip(body), max_chars)[0]
    by_hash = {chunk_cache_key(c): c for c in chunks}
    matched = []
    for e in entries:
        src = by_hash.get(e.get("hash"))
        if src is None:
            continue
        cjk = len(CJK_CHARS.findall(src))
        matched.append((e["_file"], len(src) - cjk, cjk, len(e.get("translated", ""))))

    # Fit the CJK expansion factor from this file: translated ≈ latin + cjk * K.
    # Fitting beats a constant because zh expands far more than ko, and a wrong
    # constant shifts every chunk's ratio in the same direction, which turns
    # the median-relative test into noise.
    tot_cjk = sum(c for _, _, c, _ in matched)
    tot_gain = sum(t - n for _, n, _, t in matched)
    k = tot_gain / tot_cjk if tot_cjk > 200 and tot_gain > 0 else CJK_EXPANSION_FALLBACK
    pairs = [(name, n + c * k, t) for name, n, c, t in matched]

    if not pairs:
        # Chunk boundaries moved (different max_chars, or the source was
        # re-OCR'd since) — ratios are not comparable, say so rather than
        # invent a number.
        res["flags"].append("cache-stale")
        return res

    ratios = [t / e for _, e, t in pairs if e]
    med = statistics.median(ratios)
    for (name, e, t), r in zip(pairs, ratios):
        if med and r < med * RATIO_FLOOR:
            res["flags"].append(f"short-chunk({name} {r:.2f} vs med {med:.2f})")
            res["worst"] = min(res["worst"] or r, r)
    if verbose:
        print(f"    {key}: median act/expected {med:.2f} (fitted CJK expansion {k:.2f})")
        for (name, e, t), r in zip(pairs, ratios):
            mark = "  <-- short" if med and r < med * RATIO_FLOOR else ""
            print(f"      {name}  expected {e:8.0f} -> actual {t:6}  {r:5.2f}{mark}")
    return res


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--key", type=key_arg, action="append", help="audit only these keys")
    ap.add_argument("--verbose", action="store_true", help="print per-chunk ratios")
    args = ap.parse_args()

    manifest = manifest_load()
    if args.key:
        keys = args.key
    else:
        keys = sorted(p.name[: -len(".original.md")] for p in MD_DIR.glob("*.original.md"))

    flagged = []
    for k in keys:
        src_lang = (manifest.get(k, {}).get("conversion") or {}).get("translated_from")
        r = audit_key(k, src_lang, args.verbose)
        if r["flags"]:
            flagged.append(r)

    print(f"\nAudited {len(keys)} translated key(s); {len(flagged)} flagged.")
    for r in flagged:
        print(f"  {r['key']:16} {', '.join(r['flags'])}")
    if flagged:
        print("\nRepair: restore markdown/{key}.md from {key}.original.md, then re-run")
        print("translate_markdown.py --key {key} --src-lang {lang} (one key per run).")
    return 1 if flagged else 0


if __name__ == "__main__":
    raise SystemExit(main())
