#!/usr/bin/env python3
# corpus-command: fetch unpaywall-by-doi | Unpaywall for a key/DOI list
"""
Tier 1 acquisition by explicit DOI, for keys not in the manifest yet.

`fetch_unpaywall.py` walks `manifest.json`, so it cannot see citations minted
in the data repo's reference store before they reach the bib. This driver takes
key -> DOI lists (a `to-fetch.md`, or plain `key<TAB>doi` lines) and reuses
`fetch_unpaywall`'s API and download helpers.

Government and society publishers (USGS `10.3133/*`, NMGS `10.56577/*`, state
surveys) are largely open access, so they are worth trying here even when the
DOI looks obscure.

Unlike `fetch_unpaywall.py` this tries EVERY `oa_locations` entry, not only
`best_oa_location` — older records often have a null `url_for_pdf` on the best
location while a repository copy further down the list has a usable one. It
also falls back to a location's `url` when `url_for_pdf` is absent.

Writes `pdfs/{key}.pdf` and appends to `.runtime/logs/unpaywall.jsonl`. Does
NOT touch the manifest; run `intake_acquire.py` (or `/intake`) afterwards.

Resume-safe: a key whose PDF is already on disk is skipped.
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

from corpus.config import PDF_DIR, require_env
from corpus.fetch.doi_list import parse_list
from corpus.fetch.tier import now
from corpus.fetch.unpaywall import API_DELAY, LOG, download_pdf, lookup
from corpus.io import jsonl_append
from corpus.keys import keys_in, nfc


def pdf_candidates(payload: dict) -> list[tuple[str, str]]:
    """Every plausible PDF URL, best location first. Returns [(url, host_type)]."""
    seen: set[str] = set()
    out: list[tuple[str, str]] = []
    locs = []
    best = payload.get("best_oa_location")
    if best:
        locs.append(best)
    locs.extend(payload.get("oa_locations") or [])
    for loc in locs:
        if not isinstance(loc, dict):
            continue
        for field in ("url_for_pdf", "url"):
            u = loc.get(field)
            if u and u not in seen:
                seen.add(u)
                out.append((u, loc.get("host_type") or "?"))
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--list", required=True, type=Path, action="append",
                    help="to-fetch.md or 'key<TAB>doi' file; repeatable")
    ap.add_argument("--keys", default="", help="comma-separated keys to restrict to")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    pairs: list[tuple[str, str]] = []
    seen_keys: set[str] = set()
    for path in args.list:
        for k, d in parse_list(path)[0]:
            if k not in seen_keys:
                seen_keys.add(k)
                pairs.append((k, d))

    want = {k.strip() for k in args.keys.split(",") if k.strip()}
    if want:
        pairs = [(k, d) for k, d in pairs if k in want]

    PDF_DIR.mkdir(parents=True, exist_ok=True)
    on_disk = set(keys_in(PDF_DIR, ".pdf"))
    todo = [(k, d) for k, d in pairs if nfc(k) not in on_disk]

    email = require_env("UNPAYWALL_EMAIL")

    print(f"key/DOI pairs:   {len(pairs)}")
    print(f"already on disk: {len(pairs) - len(todo)}")
    print(f"to try:          {len(todo)}")
    print()
    if args.dry_run:
        for k, d in todo:
            print(f"  {k:28s} {d}")
        return 0

    got: list[tuple[str, int, str]] = []
    closed: list[str] = []
    failed: list[tuple[str, str]] = []

    for i, (key, doi) in enumerate(todo, 1):
        ts = now()
        status, payload, err = lookup(doi, email)
        if not payload:
            failed.append((key, err or f"http {status}"))
            print(f"[{i:>3}/{len(todo)}] {key:28s} --   api: {err or status}")
            jsonl_append(LOG, {"ts": ts, "key": key, "doi": doi, "via": "by_doi",
                               "outcome": "api_error", "error": err})
            time.sleep(API_DELAY)
            continue

        cands = pdf_candidates(payload)
        if not payload.get("is_oa") and not cands:
            closed.append(key)
            print(f"[{i:>3}/{len(todo)}] {key:28s} --   closed access")
            jsonl_append(LOG, {"ts": ts, "key": key, "doi": doi, "via": "by_doi",
                               "outcome": "closed"})
            time.sleep(API_DELAY)
            continue

        dest = PDF_DIR / f"{nfc(key)}.pdf"
        ok, why, nbytes, used = False, "no candidate urls", 0, ""
        for url, host in cands:
            ok, why, nbytes = download_pdf(url, dest)    # writes dest only for a PDF
            if ok:
                used = url
                break
        if ok:
            got.append((key, nbytes, used))
            print(f"[{i:>3}/{len(todo)}] {key:28s} OK   {nbytes/1024:>6.0f} KB")
        else:
            failed.append((key, why))
            print(f"[{i:>3}/{len(todo)}] {key:28s} --   {why[:60]}")

        jsonl_append(LOG, {"ts": ts, "key": key, "doi": doi, "via": "by_doi",
                           "outcome": "pdf" if ok else "download_failed",
                           "url": used, "bytes": nbytes,
                           "candidates": [u for u, _ in cands],
                           "error": "" if ok else why})
        time.sleep(API_DELAY)

    print()
    print(f"acquired: {len(got)}   closed access: {len(closed)}   failed: {len(failed)}")
    if got:
        print()
        print("Acquired:")
        for key, nbytes, url in got:
            print(f"  {key:28s} {nbytes/1024:>6.0f} KB  {url[:70]}")
    if closed:
        print()
        print("Closed access (no OA copy): " + ", ".join(closed))
    if failed:
        print()
        print("Failed:")
        for key, why in failed:
            print(f"  {key:28s} {why[:80]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
