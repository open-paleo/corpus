#!/usr/bin/env python3
# corpus-command: qc languages | non-English bodies (TRANSLATION_AUDIT.md)
"""
Scan markdown/{key}.md for non-English content. Flags papers that may
benefit from translate-on-OCR (or post-hoc translation cleanup).

Languages detected: en, es, fr, de, it, pt, zh-cn, zh-tw, ja, ru, plus
fallbacks. Uses `langdetect` (Google's CLD3 port).

Heuristic for flagging:
  - Sample multiple regions of the body (start/middle/end) to dampen
    bilingual abstract effects (e.g., a Chinese paper with English
    abstract).
  - If the dominant non-English language has >25% confidence weight, flag.
  - Existing `vision_ocr_needed` flagged keys are excluded (already queued
    for translate-on-OCR).

Output:
  - Markdown report at TRANSLATION_AUDIT.md
  - Optional: with --apply, sets `manifest[k].conversion.vision_ocr_needed`
    + reason for flagged keys.

Usage:
  ./.runtime/venv/bin/python scripts/qc_languages.py [--threshold 0.25]
                                                        [--apply]
                                                        [--key K]...
"""
from __future__ import annotations

import argparse
import sys
from collections import Counter
from datetime import datetime, timezone

from langdetect import detect_langs, DetectorFactory, LangDetectException
from corpus.config import ROOT as ARCHIVE, MD_DIR
from corpus.manifest import commit as manifest_commit, load as manifest_load
from corpus.keys import key_arg
from corpus.io import atomic_write_text
DetectorFactory.seed = 0  # deterministic

REPORT = ARCHIVE / "TRANSLATION_AUDIT.md"

# Languages we don't want to flag (English is target; Latin diagnoses are common)
SKIP_LANGS = {"en", "la"}

# Languages that legitimately appear in paleontology papers; anything outside
# this set on a short snippet is almost always a langdetect false positive
# (Welsh / Hungarian / Lithuanian / Indonesian etc. on Latin-rich text).
TRUSTED_LANGS = {
    "en", "es", "fr", "de", "it", "pt", "ja", "ru",
    "zh-cn", "zh-tw", "nl", "ca",
}

# Friendly names
LANG_NAMES = {
    "en": "English", "es": "Spanish", "fr": "French", "de": "German",
    "it": "Italian", "pt": "Portuguese", "ja": "Japanese", "ru": "Russian",
    "zh-cn": "Chinese (simplified)", "zh-tw": "Chinese (traditional)",
    "nl": "Dutch", "pl": "Polish", "sv": "Swedish", "no": "Norwegian",
    "da": "Danish", "ca": "Catalan", "ro": "Romanian",
}


def sample_body_languages(body: str, n_samples: int = 5, sample_chars: int = 1500) -> dict[str, float]:
    """Sample n regions, detect language of each, weight by sample length."""
    body = body.strip()
    if len(body) < sample_chars:
        windows = [body]
    else:
        windows = []
        # evenly-spaced samples
        step = (len(body) - sample_chars) // max(n_samples - 1, 1)
        for i in range(n_samples):
            start = i * step
            windows.append(body[start:start + sample_chars])
    weights: Counter[str] = Counter()
    for w in windows:
        if len(w.strip()) < 100:
            continue
        try:
            results = detect_langs(w)
        except LangDetectException:
            continue
        for r in results:
            weights[r.lang] += r.prob * len(w)
    total = sum(weights.values())
    if total == 0:
        return {}
    return {lang: pct / total for lang, pct in weights.items()}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--threshold", type=float, default=0.25,
                    help="Min combined non-English ratio to flag a paper.")
    ap.add_argument("--apply", action="store_true",
                    help="Set vision_ocr_needed in manifest for flagged keys.")
    ap.add_argument("--key", type=key_arg, action="append", default=[])
    args = ap.parse_args()

    manifest = manifest_load()

    if args.key:
        candidates = args.key
    else:
        candidates = sorted(manifest.keys())

    flagged: list[tuple[str, dict[str, float], str]] = []
    n_scanned = 0
    n_already_flagged = 0
    n_unreadable = 0

    for key in candidates:
        md = MD_DIR / f"{key}.md"
        if not md.exists() or md.stat().st_size == 0:
            continue
        n_scanned += 1
        # Skip if already on the vision-ocr backlog
        if (manifest.get(key, {}).get("conversion") or {}).get("vision_ocr_needed"):
            n_already_flagged += 1
            continue
        text = md.read_text(errors="replace", encoding="utf-8")
        try:
            lang_dist = sample_body_languages(text)
        except Exception:
            n_unreadable += 1
            continue
        if not lang_dist:
            continue
        # Only count non-English ratios from TRUSTED_LANGS; anything else is noise.
        non_en_trusted = sum(p for L, p in lang_dist.items()
                             if L not in SKIP_LANGS and L in TRUSTED_LANGS)
        if non_en_trusted >= args.threshold:
            top_lang = max(((L, p) for L, p in lang_dist.items()
                            if L not in SKIP_LANGS and L in TRUSTED_LANGS),
                           key=lambda x: x[1])
            flagged.append((key, lang_dist, top_lang[0]))

    # Sort by dominant non-English ratio desc
    flagged.sort(key=lambda x: -sum(p for L, p in x[1].items() if L not in SKIP_LANGS))

    # Write report
    lines = [
        "# Translation audit",
        "",
        f"Scanned {n_scanned} markdown files (excluded {n_already_flagged} already flagged for vision-OCR translation).",
        f"Threshold: ≥{args.threshold:.0%} of body text in non-English / non-Latin language.",
        "",
        f"**{len(flagged)} papers flagged.**",
        "",
    ]
    if flagged:
        # Group by dominant language
        by_lang: dict[str, list] = {}
        for key, dist, top in flagged:
            by_lang.setdefault(top, []).append((key, dist))
        for top in sorted(by_lang, key=lambda L: -len(by_lang[L])):
            lang_name = LANG_NAMES.get(top, top)
            entries = by_lang[top]
            lines.append(f"## {lang_name} ({top}) — {len(entries)} papers")
            lines.append("")
            for key, dist in entries:
                cit = manifest[key].get("citation", {})
                title = (cit.get("title") or "")[:80]
                # Top 3 langs
                top3 = sorted(dist.items(), key=lambda x: -x[1])[:3]
                top3_str = ", ".join(f"{L}={p:.0%}" for L, p in top3)
                tool = (manifest[key].get("conversion") or {}).get("tool") or "?"
                lines.append(f"- **`{key}`** — {cit.get('author','?')} ({cit.get('year','?')}). *{title}*")
                lines.append(f"  - tool: {tool}; lang mix: {top3_str}")
            lines.append("")

    atomic_write_text(REPORT, "\n".join(lines))
    print(f"Scanned {n_scanned}, already-flagged {n_already_flagged}, unreadable {n_unreadable}")
    print(f"Flagged {len(flagged)} papers (≥{args.threshold:.0%} non-English).")
    print(f"Report: {REPORT}")

    if args.apply and flagged:
        now = datetime.now(timezone.utc).isoformat()
        for key, dist, top in flagged:
            conv = manifest[key].setdefault("conversion", {})
            conv["vision_ocr_needed"] = True
            conv["vision_ocr_reason"] = (
                f"non-English content detected by langdetect "
                f"({LANG_NAMES.get(top, top)}, "
                f"{sum(p for L, p in dist.items() if L not in SKIP_LANGS):.0%} non-English)"
            )
            conv["vision_ocr_flagged_at"] = now
        manifest_commit(manifest)
        print(f"Applied: set vision_ocr_needed for {len(flagged)} keys.")

    return 0


if __name__ == "__main__":
    sys.exit(main())
