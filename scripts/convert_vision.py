#!/usr/bin/env python3
# corpus-command: convert vision | PDF -> markdown via Claude vision OCR
"""
Step 2 (vision-OCR path): convert backlog PDFs to Markdown via
Claude Sonnet vision (models.SONNET).

Backlog detection (any of):
  1. markdown/{key}.md exists with tool=="marker" and holds no text (page markers only)
     (scans-without-text-layer)
  2. manifest[key].conversion.vision_ocr_needed is True
     (set by qc_scan.py for CJK-heavy / PUA-garbage cases)

Per page: rasterize via pypdfium2, base64-encode JPEG, send to the
Messages API. System prompt is prompt-cached so per-page input cost is
mostly the image (~2.5k tokens). Per-page response cached on disk at
.runtime/cache/vision/{key}/page-NNNN.json so re-runs are free.

Usage:
  ./.runtime/venv/bin/python scripts/convert_vision.py [--key K]... [--limit N]
                                                   [--concurrency N] [--force]
                                                   [--dpi N] [--model NAME]
"""

from __future__ import annotations

import argparse
import asyncio
import sys
import time
from datetime import datetime, timezone

import pypdfium2 as pdfium

from corpus.progress import clear_progress, set_progress
from corpus.io import atomic_write_text
from corpus.pages import is_empty as md_is_empty, join as join_pages
from corpus.config import PDF_DIR, MD_DIR, RUNTIME, VISION_CACHE as CACHE_DIR
from corpus.manifest import commit as manifest_commit, load as manifest_load

LOG = RUNTIME / "logs" / "vision.jsonl"

from corpus.keys import key_arg
from corpus.models import cost as model_cost
from corpus.pdf.render import render_jpeg
from corpus.vision import cache as vision_cache
from corpus.vision.client import make as make_client
from corpus.vision.ocr import ocr_one_page, select_prompt
from corpus.vision.ocr import DEFAULT_CONCURRENCY, DEFAULT_DPI, DEFAULT_MODEL  # noqa: E402
from corpus.pdf.pages import compact as compact_pages, parse as parse_pages
from corpus.io import jsonl_append





def select_backlog(manifest: dict) -> list[str]:
    backlog = []
    for k, v in manifest.items():
        conv = v.get("conversion") or {}
        md = MD_DIR / f"{k}.md"
        is_zero = (conv.get("tool") == "marker"
                   and md.exists() and md_is_empty(md))
        is_flagged = conv.get("vision_ocr_needed") is True
        is_already_vision = conv.get("tool") == "claude-vision"
        if (is_zero or is_flagged) and not is_already_vision:
            if (PDF_DIR / f"{k}.pdf").exists():
                backlog.append(k)
    return sorted(backlog)


async def ocr_one_pdf(client, sem, model: str, dpi: int,
                      manifest: dict, key: str,
                      extra_context: str = "",
                      pages: list[int] | None = None,
                      force: bool = False) -> dict:
    """OCR one PDF (all pages, or just `pages`), write markdown, return summary.

    `pages` is a 1-indexed list restricting which pages are rendered and sent.
    Cache paths stay keyed on the TRUE page index, so a later unscoped run
    cache-hits everything a scoped run already did.

    Rasterization is done serially in the main thread up-front (pypdfium2
    is not safely usable from concurrent threads — concurrent calls can
    leave PDFium global state corrupted, causing all subsequent PDFs to
    fail with 'Data format error'). API calls are then dispatched
    concurrently using the pre-rendered images.
    """
    started = time.time()
    pdf_path = PDF_DIR / f"{key}.pdf"
    cache_dir = CACHE_DIR / key
    cache_dir.mkdir(parents=True, exist_ok=True)
    prompt_text, prompt_id = select_prompt(manifest, key, extra_context)

    # Open once, count pages, render uncached pages serially, close.
    doc = pdfium.PdfDocument(pdf_path)
    try:
        n_pages = len(doc)
        if pages:
            bad = [p for p in pages if not 1 <= p <= n_pages]
            if bad:
                raise ValueError(f"{key}: page(s) {bad} out of range (1-{n_pages})")
            targets = [p - 1 for p in pages]
        else:
            targets = list(range(n_pages))
        page_jpegs: list[bytes | None] = [None] * n_pages
        for i in targets:
            if vision_cache.read(cache_dir, i, model=model, prompt_id=prompt_id,
                                 force=force) is not None:
                continue  # skip rasterization, response already cached
            page_jpegs[i] = render_jpeg(doc, i, dpi=dpi)
    finally:
        doc.close()

    async def do_page(i):
        async with sem:
            try:
                # If page is cached, ocr_one_page short-circuits without
                # touching the (None) jpeg; pass empty bytes as a safe sentinel.
                jpeg = page_jpegs[i] if page_jpegs[i] is not None else b""
                return await ocr_one_page(client, model, prompt_text, prompt_id,
                                          i, jpeg, cache_dir, force=force)
            except Exception as e:
                return {"error": str(e), "page": i + 1}

    results = await asyncio.gather(*[do_page(i) for i in targets])

    # Assemble markdown: each page opened by its page marker, pages separated by ---
    page_markdowns = []
    in_tok = out_tok = c_read = c_write = 0
    n_cached = 0
    n_failed = 0
    for i, r in zip(targets, results):
        if "error" in r:
            n_failed += 1
            page_markdowns.append(f"[VISION OCR FAILED FOR PAGE {i + 1}: {r['error']}]")
            continue
        page_markdowns.append(r["markdown"] or f"[empty page {i + 1}]")
        in_tok += r["input_tokens"]
        out_tok += r["output_tokens"]
        c_read += r["cache_read"]
        c_write += r["cache_write"]
        if r.get("cached_hit"):
            n_cached += 1

    full_md = join_pages([(i + 1, md) for i, md in zip(targets, page_markdowns)])
    md_path = MD_DIR / f"{key}.md"
    atomic_write_text(md_path, full_md)

    elapsed = time.time() - started
    cost = model_cost(model, in_tok, out_tok, c_read, c_write)

    summary = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "key": key, "outcome": "ok" if n_failed == 0 else "partial",
        "n_pages": len(targets), "n_cached": n_cached, "n_failed": n_failed,
        "scoped_pages": compact_pages(pages) if pages else None,
        "input_tokens": in_tok, "output_tokens": out_tok,
        "cache_read": c_read, "cache_write": c_write,
        "cost_usd": round(cost, 4),
        "elapsed_s": round(elapsed, 1),
        "model": model, "prompt_id": prompt_id,
    }
    jsonl_append(LOG, summary)
    return summary


async def main_async(args) -> int:
    client = make_client()

    manifest = manifest_load()
    if args.key:
        candidates = [k for k in args.key
                      if k in manifest and (PDF_DIR / f"{k}.pdf").exists()]
    else:
        candidates = select_backlog(manifest)
    if args.limit:
        candidates = candidates[: args.limit]
    if not candidates:
        print("Nothing to do.")
        return 0

    scoped_pages = parse_pages(args.pages) if args.pages else None
    if scoped_pages and len(candidates) != 1:
        print("ERROR: --pages applies to one PDF; pass exactly one --key",
              file=sys.stderr)
        return 2

    print(f"Vision-OCR candidates: {len(candidates)}")
    print(f"  model:       {args.model}")
    print(f"  concurrency: {args.concurrency}")
    print(f"  dpi:         {args.dpi}")
    if scoped_pages:
        print(f"  pages:       {compact_pages(scoped_pages)} "
              f"({len(scoped_pages)} of the PDF)")
    print()

    LOG.parent.mkdir(parents=True, exist_ok=True)
    sem = asyncio.Semaphore(args.concurrency)
    started = time.time()
    n_ok = n_partial = 0
    total_cost = 0.0
    total_pages = 0
    for i, k in enumerate(candidates, 1):
        set_progress("vision", i, len(candidates), note=k)
        try:
            s = await ocr_one_pdf(client, sem, args.model, args.dpi,
                                  manifest, k, args.extra_context,
                                  pages=scoped_pages, force=args.force)
        except Exception as e:
            print(f"  ! {k}: {e}")
            continue
        # Update manifest atomically per PDF (resume-safe)
        manifest = manifest_load()
        entry_conv = manifest[k].setdefault("conversion", {})
        entry_conv["tool"] = "claude-vision"
        entry_conv["markdown_path"] = f"markdown/{k}.md"
        entry_conv["page_count"] = s["n_pages"]
        entry_conv["converted_at"] = s["ts"]
        entry_conv["notes"] = (
            f"vision-OCR via {s['model']}; prompt={s['prompt_id']}; "
            f"cost=${s['cost_usd']:.3f}; cached_pages={s['n_cached']}"
            + (f"; SCOPE-LIMITED to PDF pages {s['scoped_pages']}"
               if s.get("scoped_pages") else "")
            + (f"; failed_pages={s['n_failed']}" if s["n_failed"] else "")
        )
        entry_conv["vision_input_tokens"] = s["input_tokens"]
        entry_conv["vision_output_tokens"] = s["output_tokens"]
        entry_conv["vision_cost_usd"] = s["cost_usd"]
        # Clear the flag now that we've handled it
        entry_conv.pop("vision_ocr_needed", None)
        entry_conv.pop("vision_ocr_reason", None)
        entry_conv.pop("vision_ocr_flagged_at", None)
        manifest_commit(manifest)

        total_cost += s["cost_usd"]
        total_pages += s["n_pages"]
        if s["outcome"] == "ok":
            n_ok += 1
        else:
            n_partial += 1
        print(f"  [{i:3d}/{len(candidates)}] {k:30s} "
              f"{s['n_pages']:3d}p · ${s['cost_usd']:6.3f} · "
              f"{s['elapsed_s']:5.1f}s"
              + (f" · CACHED {s['n_cached']}p" if s['n_cached'] else "")
              + (f" · FAILED {s['n_failed']}p" if s['n_failed'] else ""))

    clear_progress()
    elapsed = time.time() - started
    print()
    print(f"Done in {elapsed/60:.1f} min")
    print(f"  PDFs: ok={n_ok}, partial={n_partial}")
    print(f"  Pages: {total_pages}")
    print(f"  Total cost: ${total_cost:.2f}")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--key", type=key_arg, action="append", default=[])
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--concurrency", type=int, default=DEFAULT_CONCURRENCY)
    ap.add_argument("--dpi", type=int, default=DEFAULT_DPI)
    ap.add_argument("--model", default=DEFAULT_MODEL)
    ap.add_argument("--force", action="store_true",
                    help="re-OCR the targeted pages (all, or --pages) even if cached; "
                         "the new responses replace the cache entries")
    ap.add_argument("--pages", default=None,
                    help="1-indexed page spec (e.g. '1-24' or '1-3, 7') "
                         "restricting OCR to those PDF pages — the scope-limited "
                         "whole-volume case. Requires exactly one --key. Cache "
                         "keys stay page-indexed, so a later full run cache-hits "
                         "these pages.")
    ap.add_argument("--extra-context", default="",
                    help="Extra context appended to the prompt (e.g. for retrying "
                         "content-filter failures). Does NOT change cache keys, "
                         "so previously-cached pages stay cache-hit.")
    args = ap.parse_args()
    return asyncio.run(main_async(args))


if __name__ == "__main__":
    sys.exit(main())
