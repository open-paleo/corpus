#!/usr/bin/env python3
# corpus-command: qc garbage | score markdown for ASCII-looking OCR garbage (Ankylo8aurus, Ornlthlschia)
"""Heuristic OCR-garbage scoring for converted markdown.

Answers "did this conversion silently produce character-level garbage?" — the
failure mode `qc_scan.py` cannot see, because bad scan OCR emits
plausible *ASCII* (`agili8`, `Oeratop8idm`, `Ankylo8aurus`), not PUA or U+FFFD.

Two independent signals, both per-1000-tokens so length doesn't skew them:

  mixed_k  digits fused into words  — `agili8`, `MAR8H`, `Acerotheri1lm`
  sig_k    19th-c. scan signatures  — `'1'he` for The, æ-ligature flattened to
           `re` (`Moropodidre`), stray `£~` diacritic junk, mid-word case flips

Calibrated on a 19th-century scan before and after a vision re-OCR (mixed
6.13 -> 1.03, sig 4.77 -> 0.00). Flag threshold: mixed_k >= 3 or sig_k >= 1.5.

The score only ever *nominates*. Short letter+digit tokens are ambiguous —
`Cv1`/`Mt3` are legitimate anatomical labels, `Ri93` is a corrupted NHMUK
R193 — so the offending tokens are always printed. Read them before acting.

Blind spots, by construction: fabricated text, and flattened accents. For the
last one pass --lang to add a diacritic-density check.

--near-miss adds a third signal for letter-for-letter misreads with no digit
(`Ornlthlschia`, `Lambeosavrus`, `Palreontological`):

  near_k   words found in at most 2 corpus files that are within one deletion
           of a word found in >= 8 files (`Lambeosavrus` -> lambeosaurus)

It is scored on English paragraphs only (stopword ratio), because ordinary
Czech, Dutch or Portuguese words are rare in a palaeontology corpus and would
all count. Calibrated on seven papers before and after vision re-OCR: before
3.0-53, after 0.0-2.3 (one paper full of Polish names stays at 5.0). Flag: near_k >= 8;
5-8 is mostly proper names and needs reading.

Usage:
    scripts/qc_garbage.py --all
    scripts/qc_garbage.py --key KEY --key KEY2
    scripts/qc_garbage.py --all --max-year 1980 --non-vision
    scripts/qc_garbage.py --all --non-vision --near-miss --flagged-only
    scripts/qc_garbage.py --key KEY --original --lang de
"""
from __future__ import annotations

import argparse
import collections
import re
import sys
import unicodedata
from corpus.manifest import load as manifest_load
from corpus.config import ROOT
from corpus.keys import key_arg
from corpus.words import wordlist_path

MANIFEST = ROOT / "manifest.json"
MD_DIR = ROOT / "markdown"
DICT_PATH = wordlist_path()

MIXED_THRESHOLD = 3.0
SIG_THRESHOLD = 1.5
MIN_TOKENS = 200

# units that legitimately fuse to a number ("43mm", "1st"), so not corruption
UNIT_RE = re.compile(r"^\d+(?:[.·]\d+)?(?:mm|cm|km|kg|mg|nm|ml|m|ft|in|st|nd|rd|th|s)?$", re.I)

TOKEN_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9'·£~]*")
MIXED_RE = re.compile(r"[A-Za-z]{2,}[0-9]|[0-9][A-Za-z]{2,}")

# NB: the ligature rule is deliberately narrow. A bare `\w+rre\b` matches
# ordinary French (Angleterre, terre, pierre) and produced two false Tier-3
# nominations; only Latin/anatomical -ae endings are scored.
SIG_RULES = (
    ("apostrophe-T", re.compile(r"'[l1]'?[Hh]?e\b")),
    ("ae-ligature", re.compile(r"\w*(?:idre|ebrre|acere)\b")),
    ("diacritic-junk", re.compile(r"\w+[£~]\w+")),
    ("case-flip", re.compile(r"\b[a-z]{2,}[A-Z][a-z]\w*")),
)

ACCENT_RE = re.compile(r"[àâäçéèêëîïôöùûüñáíóúãõßæœÀÂÄÇÉÈÊËÎÏÔÖÙÛÜÑ]")
# languages whose prose should carry visible diacritics; ~<3 per 1k letters
# in one of these means the scan OCR flattened them (-> vision re-OCR).
ACCENT_LANGS = {"de", "fr", "es", "pt", "it", "pl", "cs", "hu", "tr", "sv"}


def load_dict() -> set[str]:
    if not DICT_PATH.exists():
        return set()
    return {w.strip().lower() for w in DICT_PATH.open(encoding="latin-1") if w.strip()}


# --- near-miss misreads ------------------------------------------------------
NEAR_THRESHOLD = 8.0
NEAR_COMMON_DF = 8   # a "real" word appears in at least this many files
NEAR_RARE_DF = 2     # a misread appears in at most this many
NEAR_WORD_RE = re.compile(r"[^\W\d_]{5,}")
SUPERSCRIPT_RE = re.compile(r"[¹²³⁰-₟]")
FOLD = str.maketrans({"ł": "l", "Ł": "L", "ø": "o", "Ø": "O", "æ": "ae", "Æ": "AE",
                      "œ": "oe", "đ": "d", "ß": "ss"})
EN_STOP = {"the", "of", "and", "is", "in", "to", "with", "from", "are", "was", "which",
           "that", "this", "by", "be", "for", "as", "on", "were", "or", "at", "it", "an"}


def fold(w: str) -> str:
    """Lowercase and strip accents, so Ziembińska and ziembinska are one word."""
    w = unicodedata.normalize("NFKD", w.translate(FOLD))
    return "".join(c for c in w if not unicodedata.combining(c)).lower()


def _clean(text: str) -> str:
    # footnote superscripts glue onto words (Matley³) and would read as misreads
    return SUPERSCRIPT_RE.sub(" ", re.sub(r"https?://\S+", " ", text))


def _deletes(w: str) -> set[str]:
    return {w[:i] + w[i + 1:] for i in range(len(w))}


class NearMiss:
    """Document-frequency lexicon over every markdown file, originals included."""

    def __init__(self) -> None:
        df: collections.Counter[str] = collections.Counter()
        for p in MD_DIR.glob("*.md"):
            df.update({fold(w) for w in NEAR_WORD_RE.findall(_clean(p.read_text(errors="replace", encoding="utf-8")))})
        self.df = df
        self.index: dict[str, set[str]] = collections.defaultdict(set)
        for w, n in df.items():
            if n >= NEAR_COMMON_DF:
                self.index[w].add(w)
                for d in _deletes(w):
                    self.index[d].add(w)
        self.words = {fold(w) for w in load_dict()}

    @staticmethod
    def english(text: str) -> tuple[str, float]:
        """English paragraphs only; short ones count if the file is mostly English."""
        paras = [p for p in re.split(r"\n\s*\n", text) if p.strip()]
        verdict: dict[str, bool] = {}
        for p in paras:
            toks = re.findall(r"[^\W\d_]+", p.lower())
            if len(toks) >= 12:
                verdict[p] = sum(t in EN_STOP for t in toks) / len(toks) >= 0.08
        eng = sum(verdict.values()) / len(verdict) if verdict else 0.0
        keep = [p for p in paras if verdict.get(p, eng >= 0.7)]
        return "\n\n".join(keep), eng

    def score(self, text: str) -> tuple[float, float, list[str]]:
        """(near_k, English fraction, examples as misread>likely)."""
        etext, eng = self.english(_clean(text))
        if len(etext) < 2000:
            return 0.0, eng, []
        n = len(re.findall(r"[^\W_]+", etext)) or 1
        hits = []
        for w in NEAR_WORD_RE.findall(etext):
            lw = fold(w)
            if self.df.get(lw, 0) > NEAR_RARE_DF or lw in self.words:
                continue
            cands = set(self.index.get(lw, ()))
            for d in _deletes(lw):
                cands |= self.index.get(d, set())
            cands.discard(lw)
            if cands:
                hits.append(f"{w}>{max(cands, key=lambda c: self.df[c])}")
        return round(1000 * len(hits) / n, 2), round(eng, 2), hits


def score(text: str, lang: str | None = None, words: set[str] | None = None) -> dict | None:
    """Return garbage metrics for `text`, or None if it is too short to judge."""
    t = re.sub(r"https?://\S+", " ", text)
    toks = TOKEN_RE.findall(t)
    if len(toks) < MIN_TOKENS:
        return None
    n = len(toks)

    mixed = [w for w in toks if not UNIT_RE.match(w) and MIXED_RE.search(w)]
    sig_hits: list[str] = []
    for _name, rx in SIG_RULES:
        sig_hits += rx.findall(t)

    out = {
        "tokens": n,
        "mixed_k": round(1000 * len(mixed) / n, 2),
        "sig_k": round(1000 * len(sig_hits) / n, 2),
        "mixed_tokens": _top(mixed),
        "sig_tokens": _top(sig_hits),
    }
    if words:
        alpha = [w for w in toks if w.isalpha() and len(w) >= 4]
        if alpha:
            out["ood"] = round(sum(1 for w in alpha if w.lower() not in words) / len(alpha), 3)
    if lang in ACCENT_LANGS:
        letters = len(re.findall(r"[A-Za-z]", t)) or 1
        out["acc_k"] = round(1000 * len(ACCENT_RE.findall(t)) / letters, 1)
    return out


def _top(items: list[str], limit: int = 12) -> list[str]:
    seen, out = set(), []
    for w in items:
        k = w.lower()
        if k not in seen:
            seen.add(k)
            out.append(w)
        if len(out) >= limit:
            break
    return out


def flagged(sc: dict) -> bool:
    if sc["mixed_k"] >= MIXED_THRESHOLD or sc["sig_k"] >= SIG_THRESHOLD:
        return True
    if sc.get("near_k", 0) >= NEAR_THRESHOLD:
        return True
    return "acc_k" in sc and sc["acc_k"] < 3.0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--key", type=key_arg, action="append", default=[])
    ap.add_argument("--all", action="store_true", help="score every converted key")
    ap.add_argument("--original", action="store_true",
                    help="score markdown/{key}.original.md (pre-translation OCR)")
    ap.add_argument("--lang", help="source language; adds a diacritic-density check")
    ap.add_argument("--max-year", type=int, help="only keys published at or before YEAR")
    ap.add_argument("--non-vision", action="store_true",
                    help="skip keys already converted by claude-vision")
    ap.add_argument("--flagged-only", action="store_true")
    ap.add_argument("--dict", action="store_true",
                    help="also compute out-of-dictionary rate (weak signal; slow)")
    ap.add_argument("--near-miss", action="store_true",
                    help="also score letter misreads with no digit (builds a corpus "
                         "lexicon first, ~1 min); reads {key}.original.md when present")
    args = ap.parse_args()

    if not args.key and not args.all:
        ap.error("pass --key KEY (repeatable) or --all")

    manifest = manifest_load()
    words = load_dict() if args.dict else None
    near = NearMiss() if args.near_miss else None

    if args.key:
        keys = [k for k in args.key if k in manifest]
        missing = set(args.key) - set(keys)
        for k in sorted(missing):
            print(f"  ! {k}: not in manifest", file=sys.stderr)
    else:
        keys = sorted(manifest)

    rows = []
    n_scored = 0
    for k in keys:
        entry = manifest[k]
        conv = entry.get("conversion") or {}
        tool = conv.get("tool")
        if args.all:
            if not tool or conv.get("skipped"):
                continue
            if args.non_vision and "vision" in tool:
                continue
            if args.max_year is not None:
                year = entry.get("year") or (entry.get("citation") or {}).get("year")
                try:
                    if int(str(year)[:4]) > args.max_year:
                        continue
                except (TypeError, ValueError):
                    continue
        suffix = ".original.md" if args.original else ".md"
        path = MD_DIR / f"{k}{suffix}"
        if not path.exists():
            continue
        # The diacritic check only means anything against SOURCE-language text.
        # For a translated key, markdown/{key}.md is English and legitimately
        # has no accents — scoring it that way flags every translation. Infer
        # the language only when we are looking at the untranslated side.
        translated = bool(conv.get("translated_from"))
        lang = args.lang if args.lang else (
            conv.get("translated_from") if (args.original or not translated) else None)
        sc = score(path.read_text(errors="replace", encoding="utf-8"), lang=lang, words=words)
        if sc is None:
            rows.append((k, tool, None))
            continue
        if near:
            # the OCR'd text, not the translation the model may have repaired
            orig = MD_DIR / f"{k}.original.md"
            src = orig if orig.exists() else path
            sc["near_k"], sc["eng"], hits = near.score(src.read_text(errors="replace", encoding="utf-8"))
            sc["near_tokens"] = _top(hits)
        n_scored += 1
        if args.flagged_only and not flagged(sc):
            continue
        rows.append((k, tool, sc))

    rows.sort(key=lambda r: -(r[2]["mixed_k"] + r[2]["sig_k"] + r[2].get("near_k", 0))
              if r[2] else 0)

    n_flagged = 0
    for k, tool, sc in rows:
        if sc is None:
            print(f"{k:<24} (too short to score)")
            continue
        mark = "FLAG" if flagged(sc) else "ok  "
        n_flagged += mark == "FLAG"
        extra = f" ood={sc['ood']}" if "ood" in sc else ""
        extra += f" acc/k={sc['acc_k']}" if "acc_k" in sc else ""
        extra += f" near={sc['near_k']} eng={sc['eng']}" if "near_k" in sc else ""
        print(f"{mark} {k:<24} mixed={sc['mixed_k']:<7} sig={sc['sig_k']:<7}"
              f" tokens={sc['tokens']:<7}{extra}  [{tool}]")
        if mark == "FLAG":
            if sc["mixed_tokens"]:
                print(f"       mixed: {' · '.join(sc['mixed_tokens'])}")
            if sc["sig_tokens"]:
                print(f"       sig:   {' · '.join(sc['sig_tokens'])}")
            if sc.get("near_tokens") and sc["near_k"] >= NEAR_THRESHOLD:
                print(f"       near:  {' · '.join(sc['near_tokens'])}")

    print(f"\n{n_scored} scored, {n_flagged} flagged "
          f"(mixed>={MIXED_THRESHOLD} or sig>={SIG_THRESHOLD}"
          f"{', or acc/k<3 for accented languages' if args.lang else ''}"
          f"{f', or near>={NEAR_THRESHOLD}' if near else ''})")
    print("Short letter+digit tokens can be legitimate labels (Cv1, Mt3) — "
          "read the token list before re-OCRing.")
    return 1 if n_flagged else 0


if __name__ == "__main__":
    sys.exit(main())
