#!/usr/bin/env python3
# corpus-command: fetch unpaywall | open-access PDFs by DOI via Unpaywall
"""
Tier 1 acquisition: Unpaywall.

For every manifest entry with a DOI that has not yet been acquired:
  1. GET api.unpaywall.org/v2/{doi}?email=...
  2. If best_oa_location.url_for_pdf is set, download it. An open-access
     record with no PDF link is recorded as unpaywall_no_pdf_link, for the
     PMC (fetch_pmc.py) and landing-page fetchers to try.
  3. Verify the response is a real PDF (magic bytes, size > 1 KB).
  4. Save to pdfs/{citation_key}.pdf and update manifest atomically.
  5. Append the raw API response and outcome to .runtime/logs/unpaywall.jsonl.

Resume-safe: skips entries whose acquisition.source is already set.
"""

from __future__ import annotations

import argparse
import sys
import time

from corpus.config import PDF_DIR, require_env
from corpus.fetch import tier
from corpus.fetch.unpaywall import API_DELAY, LOG, download_pdf, lookup
from corpus.io import jsonl_append
from corpus.keys import add_key_args, resolve_keys
from corpus.manifest import commit as manifest_commit, load as manifest_load


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    add_key_args(ap)   # --key/--keys-file: only those keys; else every untried key
    only = set(resolve_keys(ap.parse_args())) or None
    email = require_env("UNPAYWALL_EMAIL")

    manifest = manifest_load()
    candidates = tier.candidates(manifest, only, untried_only=True)

    print(f"Manifest entries:                      {len(manifest)}")
    print(f"Candidates (DOI, no PDF, untried):     {len(candidates)}")
    print(f"Estimated wall time:                   ~{len(candidates) * (API_DELAY + 1) / 60:.1f} min")
    print()

    PDF_DIR.mkdir(parents=True, exist_ok=True)
    n_ok = n_no_oa = n_no_link = n_api_err = n_dl_err = 0
    started = time.time()

    for i, key in enumerate(candidates, 1):
        entry = manifest[key]
        doi = entry["doi"]
        status, payload, err = lookup(doi, email)
        log_record = {"ts": tier.now(), "key": key, "doi": doi, "status": status,
                      "error": err, "payload": payload}

        if not payload:
            tier.record_failure(entry, "unpaywall_error")
            entry.setdefault("conversion", {})["notes"] = err
            n_api_err += 1
            jsonl_append(LOG, log_record)
            print(f"[{i:>4}/{len(candidates)}] {key:30s} API ERROR: {err}")
            time.sleep(API_DELAY)
            continue

        loc = payload.get("best_oa_location") or {}
        pdf_url = loc.get("url_for_pdf")

        if not pdf_url:
            # is_oa with no url_for_pdf: an open-access paper Unpaywall knows
            # only by its landing page (PeerJ, many PMC-deposited papers). Not
            # "no OA copy": the PMC and landing-page fetchers can try it.
            open_access = bool(payload.get("is_oa"))
            tier.record_failure(entry, "unpaywall_no_pdf_link" if open_access
                                else "unpaywall_no_oa")
            if open_access:
                n_no_link += 1
            else:
                n_no_oa += 1
            jsonl_append(LOG, log_record)
            print(f"[{i:>4}/{len(candidates)}] {key:30s} "
                  + ("open access, but no direct PDF link" if open_access else "no OA copy"))
            time.sleep(API_DELAY)
            continue

        ok, dl_err, nbytes = download_pdf(pdf_url, PDF_DIR / f"{key}.pdf")
        if ok:
            tier.record_pdf(entry, "unpaywall", key, pdf_url)
            n_ok += 1
            print(f"[{i:>4}/{len(candidates)}] {key:30s} OK   {nbytes/1024:.0f} KB")
        else:
            tier.record_failure(entry, "unpaywall_dl_failed", url=pdf_url)
            entry.setdefault("conversion", {})["notes"] = dl_err
            n_dl_err += 1
            print(f"[{i:>4}/{len(candidates)}] {key:30s} DL ERROR: {dl_err}")

        jsonl_append(LOG, {**log_record, "pdf_url": pdf_url, "downloaded": ok,
                           "dl_error": dl_err, "bytes": nbytes})
        if i % 25 == 0:
            manifest_commit(manifest)
        time.sleep(API_DELAY)

    manifest_commit(manifest)

    elapsed = time.time() - started
    print()
    print(f"Done in {elapsed/60:.1f} min")
    print(f"  OK (PDF saved):       {n_ok}")
    print(f"  OA, no PDF link:      {n_no_link}"
          + ("   (try: corpus fetch pmc, then corpus fetch oa-direct)" if n_no_link else ""))
    print(f"  no OA copy:           {n_no_oa}")
    print(f"  API error:            {n_api_err}")
    print(f"  download error:       {n_dl_err}")
    print(f"  total processed:      {n_ok + n_no_oa + n_no_link + n_api_err + n_dl_err}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
