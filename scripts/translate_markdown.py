#!/usr/bin/env python3
# corpus-command: convert translate | translate non-English markdown
"""
Translate non-English markdown bodies to English in-place.

Reads `markdown/{key}.md`, sends to Claude as TEXT (not vision), writes
the translated English markdown back to `markdown/{key}.md`. The original
is preserved alongside the translation at `markdown/{key}.original.md`
(visible) and at `.runtime/cache/translation/{key}/original.md` (cache
backup), so you can revert or compare.

Default candidates: keys flagged by `audit_languages.py` (translation
report). Override with `--key K`.

Idempotency: per-chunk responses cached at
`.runtime/cache/translation/{key}/chunk-NNNN.json`. If a paper is half-
translated when interrupted, re-running picks up where it left off.

Safety guards (a marker output holding only image references draws a refusal
message from the model, which would otherwise replace the file):
  - Skip if the input has fewer than MIN_ALPHA_CHARS_TO_TRANSLATE alphabetic
    characters (almost always means a failed scan / image-only stub).
  - Detect LLM refusal patterns in each chunk's response and treat them as
    failures rather than valid translations.
  - Don't write the translated body if it shrank below 50% of the original
    size (suggests omission, summarization, or refusal that slipped past
    the pattern check).

Usage:
  ./.runtime/venv/bin/python scripts/translate_markdown.py [--key K]...
                                                            [--model NAME]
                                                            [--limit N]
                                                            [--threshold 0.25]
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import re
import sys
import time
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

from langdetect import detect_langs, DetectorFactory, LangDetectException
from corpus.config import MD_DIR, RUNTIME
from corpus.manifest import commit as manifest_commit, load as manifest_load
DetectorFactory.seed = 0

CACHE_DIR = RUNTIME / "cache" / "translation"
LOG = RUNTIME / "logs" / "translation.jsonl"

from corpus.models import PRICES, SONNET, cache_model_ok, request_extras
from corpus.keys import key_arg
from corpus.io import atomic_write_json
from corpus.io import read_json
from corpus.config import require_env
from corpus.vision.client import make as make_client
from corpus.io import atomic_write_text
from corpus.io import jsonl_append
# Sonnet for every language. Haiku 5.5 left most paragraphs of a German paper
# untranslated while reporting end_turn, and swapped printed taxon names
# (docs/notes/conversion.md, "Model choice").
DEFAULT_MODEL = SONNET
MAX_CHARS_PER_CHUNK = 18000  # ~4.5k input tokens
# CJK sources tokenize densely and expand ~1.3x in characters on the way to
# English, so an 18k-char Korean/Chinese chunk can exceed MAX_OUTPUT_TOKENS
# and get silently truncated. Give them a smaller chunk.
MAX_CHARS_PER_CHUNK_CJK = 9000
MAX_OUTPUT_TOKENS = 16000
MIN_SPLIT_CHARS = 500  # below this, stop halving and accept what we get

SYSTEM = """You are a translator specializing in paleontology literature, including 19th- and 20th-century scientific journals and monographs.

Translate the user's text into clear, accurate English Markdown.

Rules:
- Translate non-English prose into natural English. If a passage is already in English, leave it verbatim.
- Latin taxonomic names stay in Latin (e.g. *Tyrannosaurus rex*, *Hylaeosaurus*) — italicize them.
- Preserve all Markdown structure: headings (# / ##), italics (*...*), tables, page-break separators (---), HTML comments like `<!-- file 12 -->`, figure markers like `[FIGURE: ...]`, `[ILLEGIBLE]`, etc.
- Preserve in-text citations and bibliographic references exactly. Do not translate author names, journal titles, or publication metadata.
- Preserve numerical data, measurements, and specimen catalogue numbers verbatim.
- Do not summarize, paraphrase, or omit content. Translate every paragraph.
- Output the translated Markdown body only — no preamble, no commentary, no language metadata."""

SKIP_LANGS = {"en", "la"}
TRUSTED_LANGS = {"en", "es", "fr", "de", "it", "pt", "ja", "ru", "zh-cn", "zh-tw", "nl", "ca"}

# CJK sources need the stronger model + an explicit anti-transliteration rule.
# Chinese/Japanese/Korean dinosaur names are written in characters (e.g. 晓龙)
# and must be rendered as their ESTABLISHED Latin genus (Xiaosaurus), never
# phonetically ("Xialong"). Haiku gets 晓龙 right with CJK_TAXON_RULE but misses
# rarer names (大地龙 as "*Dilong* (?)" where Sonnet gives *Tatisaurus*). Sonnet
# is the default for every language, so the upgrade below fires only if the
# default changes; an explicit --model is used as given.
CJK_LANGS = {"zh", "zh-cn", "zh-tw", "ja", "ko"}
CJK_MODEL = SONNET
CJK_TAXON_RULE = (
    "\n- IMPORTANT (CJK source): render every taxonomic name as its established "
    "Latin scientific name, NEVER a phonetic transliteration of the characters. "
    "Chinese genus names end in 龙 (-saurus): map to the accepted Latin genus "
    "(e.g. 晓龙 → *Xiaosaurus*, 营山龙 → *Yingshanosaurus*, 微肿头龙 → "
    "*Micropachycephalosaurus*, 蜀龙 → *Shunosaurus*). Do not output forms like "
    "'Xialong' or 'Dashanpulong'. If unsure of the established Latin name, give "
    "your best Latin rendering followed by the original characters in parentheses."
)

# Minimum prose density to attempt translation. A markdown body with fewer than
# this many alphabetic characters is almost always image-references-only or a
# stub from a failed scan; sending it to the LLM produces refusal text that
# would clobber the original.
MIN_ALPHA_CHARS_TO_TRANSLATE = 300

# Patterns indicating the LLM refused/declined to translate rather than
# producing actual translated content. If a translated chunk matches any of
# these, treat it as a failed chunk (do not write).
REFUSAL_PATTERNS = [
    re.compile(r"\[No text content provided\]", re.IGNORECASE),
    re.compile(r"appear to be (picture|image) files?", re.IGNORECASE),
    re.compile(r"contain no translatable text", re.IGNORECASE),
    re.compile(r"^I'?m sorry,? (but )?I (can'?t|cannot)", re.IGNORECASE | re.MULTILINE),
    re.compile(r"please provide .{0,80}\bin text format\b", re.IGNORECASE),
    re.compile(r"no (actual )?text (was )?provided", re.IGNORECASE),
]


def is_refusal(text: str) -> str | None:
    """Return the matched refusal pattern, or None if the text looks like a real translation."""
    for rx in REFUSAL_PATTERNS:
        if rx.search(text):
            return rx.pattern
    return None


def sample_body_languages(body: str, n_samples: int = 5, sample_chars: int = 1500) -> dict[str, float]:
    """Sample n evenly-spaced regions, run langdetect on each, aggregate by
    prob × len(window). Mirrors audit_languages.py so the two scripts agree
    on what's bilingual; a single-sample heuristic mis-detects papers whose
    Chinese front matter alternates with Latin taxonomic names (langdetect reads it as en+vi+no on one window, all-Chinese on
    another)."""
    body = body.strip()
    if len(body) < sample_chars:
        windows = [body]
    else:
        step = (len(body) - sample_chars) // max(n_samples - 1, 1)
        windows = [body[i * step:i * step + sample_chars] for i in range(n_samples)]
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


def detect_source_lang(text: str) -> str | None:
    """Return the dominant non-English trusted language, or None."""
    mix = sample_body_languages(text)
    candidates = [
        (lang, pct) for lang, pct in mix.items()
        if lang not in SKIP_LANGS and lang in TRUSTED_LANGS
    ]
    if not candidates:
        return None
    candidates.sort(key=lambda lp: -lp[1])
    lang, pct = candidates[0]
    return lang if pct >= 0.15 else None


def chunk_markdown(text: str, max_chars: int = MAX_CHARS_PER_CHUNK) -> list[str]:
    """Split markdown into chunks at paragraph boundaries, never exceeding max_chars."""
    if len(text) <= max_chars:
        return [text]
    paras = text.split("\n\n")
    chunks = []
    cur = []
    cur_len = 0
    for p in paras:
        plen = len(p) + 2
        if cur and cur_len + plen > max_chars:
            chunks.append("\n\n".join(cur))
            cur = [p]
            cur_len = plen
        else:
            cur.append(p)
            cur_len += plen
    if cur:
        chunks.append("\n\n".join(cur))
    return chunks


INVENTED_HEADING_RE = re.compile(
    r"#*\s*\[?\**(?:(?:pal(?:a)?eontolog\w*)\s+(?:literature\s+)?translation(?::[^\n]*)?"
    r"|translat(?:ion|ed text)(?: from \w+(?: to \w+)?)?|begins translation"
    r"|translated paleontology text|[\w ]*paleontology text[^\n]*translat[^\n]*)\**\]?:?\s*",
    re.I)


def clean_chunk_opening(src: str, tr: str) -> tuple[str, list[str]]:
    """Undo what the model adds at the top of a chunk that starts mid-page.

    The additions: a `---` page separator the
    source chunk does not start with; French headings from the source echoed
    untranslated ahead of the translation; the chunk's first paragraph
    promoted to a `#` heading (a `- ` list item became `# — `); an invented
    caption above a table the source chunk opens with. Only the opening is
    touched, and only when the SOURCE does not open that way itself.
    Returns (cleaned, notes)."""
    notes: list[str] = []
    s = src.lstrip()
    paras = tr.strip().split("\n\n")
    if not s.startswith(("#", "---")):
        while paras:
            p = paras[0].strip()
            if p == "---":
                notes.append("dropped leading ---")
            elif re.fullmatch(r"#+ [^\n]+", p) and p in src:
                notes.append(f"dropped echoed heading {p[:50]!r}")
            else:
                break
            paras.pop(0)
    # A heading the model invents to announce its output ("# Translation",
    # "# Paleontology Translation", "# [BEGINS TRANSLATION]", "# Translation from
    # German to English").
    while paras and INVENTED_HEADING_RE.fullmatch(paras[0].strip()) and paras[0].strip() not in src:
        notes.append(f"dropped invented heading {paras[0].strip()[:50]!r}")
        paras.pop(0)
    if s.startswith("|"):
        # Only trim a short caption the model put above the table. If the model
        # dropped the table itself, there is nothing to align to, and trimming
        # up to the next table would eat the chunk's prose.
        first_table = next((j for j, p in enumerate(paras) if p.lstrip().startswith("|")), None)
        if first_table is not None and first_table <= 2 \
                and sum(len(p) for p in paras[:first_table]) < 400:
            for p in paras[:first_table]:
                notes.append(f"dropped text before opening table {p[:50]!r}")
            del paras[:first_table]
    elif paras and not s.startswith("#") and re.match(r"#+ ", paras[0]):
        first = paras[0]
        if "\n" not in first.strip() and first.strip() in (p.strip() for p in paras[1:]):
            # A translated running head the model put on top; it recurs below.
            paras.pop(0)
            notes.append(f"dropped added running head {first[:50]!r}")
        else:
            repl = "- " if s.startswith("- ") else ""
            paras[0] = re.sub(r"\A#+ (?:— )?" if repl else r"\A#+ ", repl, first, count=1)
            notes.append(f"un-promoted first line {first[:50]!r}")
    # A page separator closing the source chunk that the model dropped.
    if src.rstrip().endswith("\n---") and paras and paras[-1].strip() != "---":
        paras.append("---")
        notes.append("restored trailing ---")
    return "\n\n".join(paras), notes


# A non-CJK chunk that comes back this much shorter than its source dropped
# content (at 0.62 a chunk had lost a figure and three paragraphs, with
# stop_reason end_turn). The whole-document 50% guard cannot see a loss that
# small in a long document.
MIN_CHUNK_RATIO = 0.8


def chunk_cache_key(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def _is_stale_cache(cached: dict) -> bool:
    """Reject a cached chunk that may be truncated.

    A chunk that stopped on max_tokens lost its tail silently. An entry with
    no stop_reason is judged by its output reaching the 8192-token cap.
    """
    if cached.get("stop_reason") == "max_tokens":
        return True
    if "stop_reason" not in cached and cached.get("output_tokens", 0) >= 8190:
        return True
    return False


def _split_in_half(chunk: str) -> tuple[str, str] | None:
    """Split at the paragraph break nearest the midpoint; None if unsplittable."""
    paras = chunk.split("\n\n")
    if len(paras) < 2:
        return None
    half = len(chunk) // 2
    best_i, best_d, run = 1, None, 0
    for i, para in enumerate(paras[:-1]):
        run += len(para) + 2
        d = abs(run - half)
        if best_d is None or d < best_d:
            best_i, best_d = i + 1, d
    return "\n\n".join(paras[:best_i]), "\n\n".join(paras[best_i:])


async def _translate_once(client, model: str, chunk: str, src_lang: str | None) -> dict:
    user_msg = (f"Translate the following text from {src_lang or 'any non-English language'} into English Markdown.\n\n"
                f"---\n\n{chunk}")
    system_text = SYSTEM + (CJK_TAXON_RULE if (src_lang or "") in CJK_LANGS else "")
    msg = await client.messages.create(
        model=model,
        max_tokens=MAX_OUTPUT_TOKENS,
        system=[{"type": "text", "text": system_text, "cache_control": {"type": "ephemeral"}}],
        messages=[{"role": "user", "content": user_msg}],
        **request_extras(model),
    )
    # Haiku 5.5 and Sonnet 5.5 run safety classifiers. A refusal is not a
    # translation; raising keeps it out of the cache and marks the chunk failed.
    if msg.stop_reason == "refusal":
        raise RuntimeError("declined by the model's safety classifier (stop_reason=refusal)")
    usage = msg.usage
    return {
        "translated": "".join(b.text for b in msg.content if b.type == "text").strip(),
        "stop_reason": msg.stop_reason,
        "input_tokens": usage.input_tokens,
        "output_tokens": usage.output_tokens,
        "cache_read": getattr(usage, "cache_read_input_tokens", 0) or 0,
        "cache_write": getattr(usage, "cache_creation_input_tokens", 0) or 0,
    }


async def translate_chunk(client, model: str, chunk: str, src_lang: str | None,
                          cache_path: Path) -> dict:
    if cache_path.exists():
        cached = (read_json(cache_path) or {})
        if (cache_model_ok(cached.get("model"), model) and cached.get("hash") == chunk_cache_key(chunk)
                and not _is_stale_cache(cached)):
            cached["cached_hit"] = True
            return cached

    r = await _translate_once(client, model, chunk, src_lang)
    splits = 0
    # A max_tokens stop means the tail was cut off. Halve and retry the pieces
    # rather than cache a truncated translation (which reads as complete).
    if r["stop_reason"] == "max_tokens" and len(chunk) > MIN_SPLIT_CHARS:
        halves = _split_in_half(chunk)
        if halves is not None:
            subs = []
            for h in halves:
                sub_path = cache_path.parent / f"{cache_path.stem}-s{len(subs)}.json"
                subs.append(await translate_chunk(client, model, h, src_lang, sub_path))
            r = {
                "translated": "\n\n".join(s["translated"] for s in subs),
                "stop_reason": "end_turn" if all(s.get("stop_reason") != "max_tokens" for s in subs) else "max_tokens",
                "input_tokens": r["input_tokens"] + sum(s["input_tokens"] for s in subs),
                "output_tokens": r["output_tokens"] + sum(s["output_tokens"] for s in subs),
                "cache_read": r["cache_read"] + sum(s["cache_read"] for s in subs),
                "cache_write": r["cache_write"] + sum(s["cache_write"] for s in subs),
            }
            splits = 1 + sum(s.get("splits", 0) for s in subs)

    d = {
        "model": model, "hash": chunk_cache_key(chunk),
        "splits": splits,
        "cached_hit": False,
        **r,
    }
    if d["stop_reason"] == "max_tokens":
        print(f"  ! chunk still truncated after splitting to {len(chunk)} chars — "
              f"output may be incomplete")
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_json(cache_path, d, sort_keys=False)
    return d


async def translate_one(client, sem, model: str, key: str, manifest: dict,
                        src_lang_override: str | None = None) -> dict | None:
    started = time.time()
    md_path = MD_DIR / f"{key}.md"
    if not md_path.exists() or md_path.stat().st_size == 0:
        print(f"  ! {key}: no markdown"); return None

    body = md_path.read_text(errors="replace", encoding="utf-8")

    # Guard 1: skip if the markdown has too little real prose (image-only stubs etc.)
    alpha_chars = sum(1 for c in body if c.isalpha())
    if alpha_chars < MIN_ALPHA_CHARS_TO_TRANSLATE:
        print(f"  - {key}: too sparse ({alpha_chars} alpha chars < {MIN_ALPHA_CHARS_TO_TRANSLATE}), skipping")
        return None

    # A caller-supplied --src-lang bypasses detection entirely. Needed for
    # bilingual papers whose English abstract dominates langdetect even though
    # the body is not English: a paper 31% CJK by character count can detect
    # as "en", so the automatic path skips it. Only use this when you
    # have eyeballed the body and know the language.
    if src_lang_override:
        src_lang = src_lang_override
        print(f"  [{key}] src-lang forced to '{src_lang}' (detection bypassed)")
    else:
        src_lang = detect_source_lang(body)
        if src_lang is None:
            print(f"  - {key}: no non-English language detected, skipping"); return None

    # CJK sources auto-upgrade to the stronger model unless the caller chose one
    # explicitly (model still at the default). Reassigning `model` here flows
    # through to translate_chunk, the cost calc, and the run summary below.
    if src_lang in CJK_LANGS and model == DEFAULT_MODEL:
        model = CJK_MODEL
        print(f"  [{key}] CJK source ({src_lang}) → upgrading model to {model}")

    cache_dir = CACHE_DIR / key
    cache_dir.mkdir(parents=True, exist_ok=True)
    # Preserve original on first translation — both in cache and visibly alongside
    # the markdown so the user can compare without spelunking through .runtime/.
    orig_cache = cache_dir / "original.md"
    if not orig_cache.exists():
        atomic_write_text(orig_cache, body)
    orig_visible = MD_DIR / f"{key}.original.md"
    if not orig_visible.exists():
        atomic_write_text(orig_visible, body)

    max_chars = MAX_CHARS_PER_CHUNK_CJK if src_lang in CJK_LANGS else MAX_CHARS_PER_CHUNK
    chunks = chunk_markdown(body, max_chars)
    print(f"  [{key}] src={src_lang}, {len(body):,} chars in {len(chunks)} chunk(s)")

    async def do_chunk(i, chunk):
        cache_path = cache_dir / f"chunk-{i:04d}.json"
        async with sem:
            try:
                r = await translate_chunk(client, model, chunk, src_lang, cache_path)
                if src_lang not in CJK_LANGS and len(chunk) > 2000 \
                        and len(r["translated"]) / len(chunk) < MIN_CHUNK_RATIO:
                    ratio = len(r["translated"]) / len(chunk)
                    print(f"  ! {key}: chunk {i} came back at {ratio:.0%} of its source; retrying once")
                    cache_path.rename(cache_path.with_suffix(".short.json"))
                    r = await translate_chunk(client, model, chunk, src_lang, cache_path)
                    ratio = len(r["translated"]) / len(chunk)
                    if ratio < MIN_CHUNK_RATIO:
                        return i, {"error": f"translation still short ({ratio:.0%} of source) after retry"}
                return i, r
            except Exception as e:
                return i, {"error": str(e)}

    results = await asyncio.gather(*[do_chunk(i, c) for i, c in enumerate(chunks)])
    results.sort(key=lambda x: x[0])

    in_tok = out_tok = c_read = c_write = 0
    n_failed = n_cached = n_refused = 0
    pieces = []
    for i, r in results:
        if "error" in r:
            n_failed += 1
            pieces.append(f"\n\n[TRANSLATION FAILED FOR CHUNK {i}: {r['error']}]\n\n")
            continue
        # Guard 2: catch LLM refusals masquerading as valid translations.
        ref = is_refusal(r["translated"])
        if ref:
            n_refused += 1
            print(f"  ! {key}: chunk {i} looks like a refusal (matched /{ref}/), discarding")
            pieces.append(f"\n\n[TRANSLATION REFUSED FOR CHUNK {i}: {ref}]\n\n")
        else:
            cleaned, notes = clean_chunk_opening(chunks[i], r["translated"])
            for n in notes:
                print(f"  ~ {key}: chunk {i}: {n}")
            pieces.append(cleaned)
        in_tok += r["input_tokens"]; out_tok += r["output_tokens"]
        c_read += r["cache_read"]; c_write += r["cache_write"]
        if r.get("cached_hit"): n_cached += 1

    new_body = "\n\n".join(pieces).strip() + "\n"
    write_ok = n_failed == 0 and n_refused == 0
    # Guard 3: don't replace original if translation shrank suspiciously (>50% smaller).
    # This catches summarization/omission that slipped past the refusal regex.
    shrink_ratio = len(new_body) / max(len(body), 1)
    if write_ok and shrink_ratio < 0.5:
        write_ok = False
        print(f"  ! {key}: translated body shrank to {shrink_ratio:.0%} of original ({len(new_body)}/{len(body)} chars), refusing to overwrite")
    if write_ok:
        atomic_write_text(md_path, new_body)

    p = PRICES.get(model, PRICES[SONNET])
    cost = (in_tok * p["in"] + c_read * p["cache_r"]
            + c_write * p["cache_w"] + out_tok * p["out"]) / 1_000_000
    elapsed = time.time() - started
    if write_ok:
        outcome = "ok"
    elif n_failed or n_refused:
        outcome = "partial"
    else:
        outcome = "shrink_blocked"
    summary = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "key": key, "outcome": outcome,
        "n_chunks": len(chunks), "n_cached": n_cached,
        "n_failed": n_failed, "n_refused": n_refused,
        "shrink_ratio": round(shrink_ratio, 3),
        "wrote_translation": write_ok,
        "src_lang": src_lang,
        "input_tokens": in_tok, "output_tokens": out_tok,
        "cache_read": c_read, "cache_write": c_write,
        "cost_usd": round(cost, 4),
        "elapsed_s": round(elapsed, 1),
        "model": model,
    }
    jsonl_append(LOG, summary)
    return summary


def select_candidates(manifest: dict, threshold: float = 0.25) -> list[str]:
    """Re-run langdetect to find candidates fresh (so we don't depend on stale audit doc).
    Uses the same multi-window aggregate as detect_source_lang / audit_languages.py."""
    cands = []
    for key in sorted(manifest):
        md = MD_DIR / f"{key}.md"
        if not md.exists() or md.stat().st_size == 0:
            continue
        body = md.read_text(errors="replace", encoding="utf-8")
        mix = sample_body_languages(body)
        non_en = sum(pct for lang, pct in mix.items()
                     if lang not in SKIP_LANGS and lang in TRUSTED_LANGS)
        if non_en >= threshold:
            cands.append(key)
    return cands


async def main_async(args) -> int:
    require_env("ANTHROPIC_API_KEY")

    client = make_client()

    manifest = manifest_load()

    if args.key:
        candidates = list(args.key)
    else:
        candidates = select_candidates(manifest, args.threshold)
    if args.limit:
        candidates = candidates[: args.limit]

    print(f"Translation candidates: {len(candidates)}")
    print(f"  model: {args.model}, concurrency: {args.concurrency}")
    print()

    LOG.parent.mkdir(parents=True, exist_ok=True)
    sem = asyncio.Semaphore(args.concurrency)
    started = time.time()
    n_ok = n_partial = 0
    total_cost = 0.0
    for i, k in enumerate(candidates, 1):
        try:
            s = await translate_one(client, sem, args.model, k, manifest,
                                    src_lang_override=args.src_lang)
        except Exception as e:
            print(f"  ! {k}: {e}"); continue
        if s is None: continue

        # Update manifest — only mark a paper as "translated" if we actually
        # wrote the translation. Failed/refused/shrink-blocked attempts get
        # translation_attempted_at + translation_blocked_reason instead.
        manifest = manifest_load()
        conv = manifest[k].setdefault("conversion", {})
        if s["wrote_translation"]:
            conv["translated_from"] = s["src_lang"]
            conv["translation_model"] = s["model"]
            conv["translation_cost_usd"] = s["cost_usd"]
            conv["translated_at"] = s["ts"]
        else:
            conv["translation_attempted_at"] = s["ts"]
            conv["translation_blocked_reason"] = s["outcome"]
        manifest_commit(manifest)

        total_cost += s["cost_usd"]
        if s["outcome"] == "ok":
            n_ok += 1
        else:
            n_partial += 1
        warn = ""
        if s["n_failed"]:   warn += f" · FAILED {s['n_failed']}c"
        if s["n_refused"]:  warn += f" · REFUSED {s['n_refused']}c"
        if not s["wrote_translation"] and s["outcome"] == "shrink_blocked":
            warn += f" · SHRINK_BLOCKED ({s['shrink_ratio']:.0%})"
        elif not s["wrote_translation"]:
            warn += " · NOT_WRITTEN"
        print(f"  [{i:2d}/{len(candidates)}] {k:30s} {s['src_lang']}→en  "
              f"{s['n_chunks']}c · ${s['cost_usd']:6.3f} · {s['elapsed_s']:5.1f}s"
              + (f" · CACHED {s['n_cached']}c" if s['n_cached'] else "")
              + warn)

    elapsed = time.time() - started
    print()
    print(f"Done in {elapsed/60:.1f} min")
    print(f"  ok={n_ok}, partial={n_partial}")
    print(f"  total cost: ${total_cost:.2f}")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--key", type=key_arg, action="append", default=[])
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--concurrency", type=int, default=3)
    ap.add_argument("--threshold", type=float, default=0.25,
                    help="Min non-English ratio to flag (default 0.25)")
    ap.add_argument("--model", default=DEFAULT_MODEL)
    ap.add_argument("--src-lang", default=None, metavar="LANG",
                    help="Force the source language (e.g. zh-cn, de, ru) instead of "
                         "auto-detecting. For bilingual papers whose English abstract "
                         "makes langdetect return 'en' and skip the file. Applies to "
                         "every --key in the run, so pass one key at a time.")
    args = ap.parse_args()
    return asyncio.run(main_async(args))


if __name__ == "__main__":
    sys.exit(main())
