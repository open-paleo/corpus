#!/usr/bin/env python3
# corpus-command: bhl fetch | page images of accepted BHL matches -> pdfs/{key}.pdf
"""
Fetch BHL papers as page images and stitch them into pdfs/{key}.pdf.

For each manifest entry with an ACCEPTED `bhl_match` (doi_part matches are
accepted by bhl_match.py; a fuzzy or item_unique match is accepted by naming
its key with --key) and no PDF on disk:

  1. The article's pages:
     - Part match (fuzzy, doi_part): its PageIDs from the index's `partpage`.
     - Item match (item_unique, a whole volume): narrowed to the cited print
       pages (citation.pages) by reading the printed page number on each
       page's BHL OCR text. If the range can't be found, nothing is fetched:
       pass --item-seqs FIRST-LAST (the volume's own page sequence) instead.
  2. The JP2 page images from BHL's open S3 bucket
     (s3://bhl-open-data/images/{BarCode}/{BarCode}_{seq}.jp2), cached in
     bhl_images/{key}/. biodiversitylibrary.org itself is not used: its PDF
     endpoints are behind Cloudflare.
  3. A lossless PDF of those images (corpus.bhl.stitch_jp2s), recorded as
     acquisition.source = "bhl" with conversion.vision_ocr_needed set: the
     paper then shows up in /intake (intake_discover: "unconverted") and goes
     to vision OCR like any other scan. BHL's own OCR text is not kept as the
     paper's markdown: in this archive vision OCR replaced it for 45 of 46
     BHL papers.

A PageID's sequence number in its volume (which names the JP2) comes from the
bucket's OCR file names (ocr/item-{ItemID}/item-{ItemID}-{PageID}-{seq}.txt),
or, for a volume with no OCR in the bucket, from BHL's API (GetItemMetadata:
the page list in order; position = SequenceOrder, checked against the full
page table on three volumes, 1,428 pages). The API also gives each page's
printed number, which locates an article in a volume better than reading it
off the OCR. The API needs BHL_API_KEY (free); without it, volumes with no
OCR in the bucket are reported as no_pages.

Usage:
  ./.runtime/venv/bin/python scripts/bhl_fetch.py              # accepted matches; lists the rest
  ./.runtime/venv/bin/python scripts/bhl_fetch.py --key K      # accept K's match and fetch it
  ./.runtime/venv/bin/python scripts/bhl_fetch.py --key K --item-seqs 212-231
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import urllib.parse
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from corpus.bhl import connect, stitch_jp2s
from corpus.bib import page_range
from corpus.config import ROOT as ARCHIVE, PDF_DIR, RUNTIME, cache_dir
from corpus.fetch import http, tier
from corpus.io import atomic_write_bytes, atomic_write_text, jsonl_append
from corpus.keys import add_key_args, resolve_keys
from corpus.manifest import commit as manifest_commit, load as manifest_load

IMG_DIR = ARCHIVE / "bhl_images"
OCR_CACHE = cache_dir("bhl_ocr")      # page OCR, read only to narrow volume matches
LOG = RUNTIME / "logs" / "bhl_fetch.jsonl"

S3 = "https://bhl-open-data.s3.amazonaws.com"
TIMEOUT = 60
OCR_WORKERS = 24       # S3 has no rate limit; latency is the bottleneck
JP2_WORKERS = 12       # JP2s are 1-3 MB; fewer workers to keep bandwidth in check
MAX_PAGES = 400        # refuse to pull more than this many images for one paper


def http_get(url: str, timeout: int = TIMEOUT) -> tuple[int, bytes, str]:
    r = http.get(url, timeout=timeout, max_bytes=50 << 20)
    return r.status, r.body, r.error


def s3_list(prefix: str) -> list[str]:
    """Every key under prefix in the BHL bucket."""
    keys: list[str] = []
    marker = ""
    while True:
        url = f"{S3}/?prefix={urllib.parse.quote(prefix)}&max-keys=1000"
        if marker:
            url += f"&marker={urllib.parse.quote(marker)}"
        status, body, _ = http_get(url, timeout=30)
        if status != 200:
            return keys
        text = body.decode("utf-8", "replace")
        batch = re.findall(r"<Key>([^<]+)</Key>", text)
        keys += batch
        if "<IsTruncated>true</IsTruncated>" in text and batch:
            marker = batch[-1]
        else:
            return keys


def item_pages(item_id: int) -> dict[int, tuple[int, str]]:
    """{PageID: (sequence in the volume, OCR key)} for a BHL Item."""
    out: dict[int, tuple[int, str]] = {}
    for k in s3_list(f"ocr/item-{item_id}/"):
        m = re.match(rf"ocr/item-{item_id}/item-{item_id}-(\d+)-(\d+)\.txt$", k)
        if m:
            out[int(m.group(1))] = (int(m.group(2)), k)
    return out


def item_jp2s(barcode: str) -> dict[int, str]:
    """{sequence: JP2 key} for a BHL Item's barcode."""
    out: dict[int, str] = {}
    for k in s3_list(f"images/{barcode}/"):
        m = re.match(rf"images/{re.escape(barcode)}/{re.escape(barcode)}_(\d+)\.jp2$", k)
        if m:
            out[int(m.group(1))] = k
    return out


def api_item(item_id: int) -> list[dict] | None:
    """BHL API page list of an Item (cached), or None without BHL_API_KEY or
    when the call fails."""
    import os
    from corpus.config import load_env
    path = cache_dir("bhl_api") / f"item-{item_id}.json"
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    load_env()
    key = os.environ.get("BHL_API_KEY")
    if not key:
        return None
    q = urllib.parse.urlencode({"op": "GetItemMetadata", "id": item_id, "pages": "t",
                                "format": "json", "apikey": key})
    r = http.get(f"https://www.biodiversitylibrary.org/api3?{q}", timeout=120, max_bytes=50 << 20)
    try:
        pages = json.loads(r.body)["Result"][0]["Pages"] if r.status == 200 else None
    except (ValueError, KeyError, IndexError, TypeError):
        pages = None
    if pages is not None:
        path.parent.mkdir(parents=True, exist_ok=True)
        atomic_write_text(path, json.dumps(pages))
    return pages


def page_sequences(item_id: int) -> tuple[dict[int, tuple[int, str | None]], list[dict] | None]:
    """({PageID: (seq, OCR key or None)}, API pages or None) for an Item."""
    pages = item_pages(item_id)
    api = api_item(item_id) if not pages else None
    if not pages and api:
        pages = {p["PageID"]: (i, None) for i, p in enumerate(api, 1)}
    return pages, api


def printed_number(api_page: dict) -> int | None:
    for pn in api_page.get("PageNumbers") or []:
        # "459", or "[459]" for a number BHL inferred; a "Plate" prefix marks plates
        if (pn.get("Prefix") or "").strip().lower().startswith("pl"):
            continue
        m = re.search(r"\d{1,5}", str(pn.get("Number") or ""))
        if m:
            return int(m.group(0))
    return None


# A print page number near the start or end of an OCR page.
PRINT_PAGE_RE = re.compile(r"(?:^|\n)\s*\[?(?:Page\s*|p\.?\s*)?(\d{2,5})\b", re.IGNORECASE)


def detect_print_page_number(ocr_text: str) -> int | None:
    """The printed page number in OCR text, from its first/last 300 chars."""
    for chunk in (ocr_text[:300], ocr_text[-300:]):
        for m in PRINT_PAGE_RE.finditer(chunk):
            n = int(m.group(1))
            if 1 <= n < 9999:
                return n
    return None


def _ocr_text(args) -> tuple[int, str]:
    seq, s3_key, item_id = args
    path = OCR_CACHE / str(item_id) / f"{seq:05d}.txt"
    if path.exists():
        return seq, path.read_text(encoding="utf-8", errors="replace")
    status, body, _ = http_get(f"{S3}/{s3_key}", timeout=30)
    text = body.decode("utf-8", "replace") if status == 200 else ""
    if status == 200:
        path.parent.mkdir(parents=True, exist_ok=True)
        atomic_write_text(path, text)
    return seq, text


def narrow_item(item_id: int, pages: dict[int, tuple[int, str | None]], cite: dict,
                api: list[dict] | None = None) -> tuple[list[int], str]:
    """Volume sequences holding the cited print pages, and a note. Empty
    when the range is missing or its pages can't be found.

    Printed page numbers come from BHL's API page metadata when available,
    else from each page's OCR text. Within one article, sequence minus
    printed number is constant, so only hits sharing the commonest offset
    (+-2, for unnumbered plates) are kept: a stray "120" on another page of
    the volume can no longer stretch the range (osborn1903a asked for 609
    pages before this)."""
    start, end = page_range(cite.get("pages"))
    if not start:
        return [], "no page range in the citation: pass --item-seqs"
    end = end or start
    if api is None:
        api = api_item(item_id)
    if api:
        printed = {i: printed_number(p) for i, p in enumerate(api, 1)}
        source = "BHL page metadata"
    else:
        jobs = [(seq, key, item_id) for seq, key in sorted(pages.values()) if key]
        with ThreadPoolExecutor(max_workers=OCR_WORKERS) as ex:
            printed = {seq: detect_print_page_number(t) for seq, t in ex.map(_ocr_text, jobs) if t}
        source = "OCR"
    hits = {s: n for s, n in printed.items() if n is not None and start <= n <= end}
    if not hits:
        return [], f"print pages {start}-{end} not found ({source}): pass --item-seqs"
    offsets = Counter(s - n for s, n in hits.items())
    best = offsets.most_common(1)[0][0]
    kept = sorted(s for s, n in hits.items() if abs((s - n) - best) <= 2)
    if len(kept) < max(1, (end - start + 1) // 3):
        return [], (f"print pages {start}-{end}: only {len(kept)} consistent page(s) "
                    f"found ({source}): pass --item-seqs")
    seqs = list(range(kept[0], kept[-1] + 1))   # include plates and unread numbers between
    return seqs, f"print pages {start}-{end} = volume seqs {seqs[0]}-{seqs[-1]} ({source})"


def _fetch_jp2(args) -> bool:
    s3_key, path = args
    if path.exists() and path.stat().st_size > 0:
        return True
    status, body, _ = http_get(f"{S3}/{s3_key}", timeout=120)
    if status == 200 and body:
        atomic_write_bytes(path, body)
        return True
    return False


def fetch_jp2s(barcode: str, seqs: list[int], dest: Path) -> tuple[int, int]:
    """(saved, missing) after downloading the JP2s for seqs into dest."""
    keys = item_jp2s(barcode)
    dest.mkdir(parents=True, exist_ok=True)
    jobs = [(keys[s], dest / f"page-{s:04d}.jp2") for s in seqs if s in keys]
    with ThreadPoolExecutor(max_workers=JP2_WORKERS) as ex:
        saved = sum(ex.map(_fetch_jp2, jobs))
    return saved, len(seqs) - saved


def accepted(match: dict) -> bool:
    return match.get("method") == "doi_part" or match.get("reviewed") is True


def parse_seqs(spec: str) -> list[int]:
    m = re.fullmatch(r"\s*(\d+)\s*-\s*(\d+)\s*", spec or "")
    if not m or int(m.group(2)) < int(m.group(1)):
        raise SystemExit(f"--item-seqs: expected FIRST-LAST, got {spec!r}")
    return list(range(int(m.group(1)), int(m.group(2)) + 1))


def fetch_one(c, key: str, entry: dict, item_seqs: list[int] | None) -> dict:
    """Download and stitch one accepted match; the log record."""
    match = entry["bhl_match"]
    method = match["method"]
    rec = {"key": key, "method": method, "part_id": match.get("part_id"),
           "item_id": match.get("item_id")}
    item_id = match.get("item_id")
    row = c.execute("SELECT BarCode FROM item WHERE ItemID=?", (item_id,)).fetchone()
    barcode = match.get("barcode") or (row[0] if row else None)
    if not item_id or not barcode:
        return {**rec, "outcome": "no_item"}
    pages, api = page_sequences(item_id)
    if item_seqs:
        seqs, note = item_seqs, "volume seqs given with --item-seqs"
    elif method in ("fuzzy", "doi_part"):
        page_ids = [pid for (pid,) in c.execute(
            "SELECT PageID FROM partpage WHERE PartID=? ORDER BY SequenceOrder",
            (match["part_id"],))]
        seqs = sorted(pages[pid][0] for pid in page_ids if pid in pages)
        note = f"{len(page_ids)} pages in the BHL part"
        if not seqs:
            return {**rec, "outcome": "no_pages", "note": note}
    else:
        seqs, note = narrow_item(item_id, pages, entry.get("citation") or {}, api)
        if not seqs:
            return {**rec, "outcome": "needs_range", "note": note}
    if len(seqs) > MAX_PAGES:
        return {**rec, "outcome": "too_many_pages", "note": f"{len(seqs)} pages"}
    saved, missing = fetch_jp2s(barcode, seqs, IMG_DIR / key)
    rec.update(pages=len(seqs), jp2_saved=saved, jp2_missing=missing, note=note)
    if not saved or missing:
        return {**rec, "outcome": "jp2_incomplete"}
    n_pages, n_bytes = stitch_jp2s(IMG_DIR / key, PDF_DIR / f"{key}.pdf")
    return {**rec, "outcome": "ok", "pdf_pages": n_pages, "pdf_bytes": n_bytes,
            "barcode": barcode, "seqs": [seqs[0], seqs[-1]]}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    add_key_args(ap)
    ap.add_argument("--item-seqs", metavar="FIRST-LAST",
                    help="with one --key: the volume's page sequence range to fetch "
                         "(for an item match whose print pages could not be found)")
    args = ap.parse_args()
    named = resolve_keys(args)
    item_seqs = parse_seqs(args.item_seqs) if args.item_seqs else None
    if item_seqs and len(named) != 1:
        raise SystemExit("--item-seqs needs exactly one --key")
    c = connect().cursor()
    manifest = manifest_load()
    for k in named:
        if not (manifest.get(k) or {}).get("bhl_match"):
            raise SystemExit(f"{k}: no bhl_match (run scripts/bhl_match.py --key {k})")
        manifest[k]["bhl_match"]["reviewed"] = True     # naming a key accepts its match

    todo = [k for k, e in manifest.items()
            if e.get("bhl_match") and (k in named if named else accepted(e["bhl_match"]))
            and not (PDF_DIR / f"{k}.pdf").exists()]
    pending = [k for k, e in manifest.items()
               if e.get("bhl_match") and not accepted(e["bhl_match"])
               and not (PDF_DIR / f"{k}.pdf").exists()]
    print(f"BHL fetch: {len(todo)} accepted match(es) to fetch")

    PDF_DIR.mkdir(parents=True, exist_ok=True)
    counts: dict[str, int] = {}
    for i, key in enumerate(todo, 1):
        entry = manifest[key]
        rec = fetch_one(c, key, entry, item_seqs)
        rec["ts"] = tier.now()
        jsonl_append(LOG, rec)
        counts[rec["outcome"]] = counts.get(rec["outcome"], 0) + 1
        if rec["outcome"] == "ok":
            url = (f"https://www.biodiversitylibrary.org/part/{rec['part_id']}"
                   if rec.get("part_id") else
                   f"https://www.biodiversitylibrary.org/item/{rec['item_id']}")
            tier.record_pdf(entry, "bhl", key, url,
                            pdf_source=(f"stitched from {rec['pdf_pages']} BHL JP2s "
                                        f"({rec['barcode']}, seqs {rec['seqs'][0]}-"
                                        f"{rec['seqs'][1]})"))
            conv = entry.setdefault("conversion", {})
            conv["vision_ocr_needed"] = True
            conv["vision_ocr_reason"] = "BHL scan (page images)"
            print(f"[{i:>3}/{len(todo)}] {key:25s} OK   {rec['pdf_pages']:>3}p  "
                  f"{rec['pdf_bytes'] / 1e6:6.1f} MB  {rec['note']}")
        else:
            print(f"[{i:>3}/{len(todo)}] {key:25s} {rec['outcome']}"
                  + (f"  {rec['note']}" if rec.get("note") else ""))
        manifest_commit(manifest)
    manifest_commit(manifest)

    print("\nBHL fetch summary:")
    for k, v in sorted(counts.items()):
        print(f"  {k:16s} {v}")
    if counts.get("ok"):
        print("\nNext: /intake (the papers show as 'unconverted', flagged for vision OCR).")
    if pending and not named:
        print(f"\n{len(pending)} match(es) awaiting review (bhl_match.py prints the table); "
              "accept and fetch with --key:")
        for k in pending[:30]:
            m = manifest[k]["bhl_match"]
            print(f"  {k:25s} {m['method']:12s} {(m.get('title_in_bhl') or '')[:60]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
