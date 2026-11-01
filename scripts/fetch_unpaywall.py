#!/usr/bin/env python3
"""
Tier 1 acquisition: Unpaywall.

For every manifest entry with a DOI that has not yet been acquired:
  1. GET api.unpaywall.org/v2/{doi}?email=...
  2. If best_oa_location.url_for_pdf is set, download it. An open-access
     record with no PDF link is recorded as unpaywall_no_pdf_link, for the
     PMC (fetch_pmc_oa.py) and landing-page fetchers to try.
  3. Verify the response is a real PDF (magic bytes, size > 1 KB).
  4. Save to pdfs/{citation_key}.pdf and update manifest atomically.
  5. Append the raw API response and outcome to .runtime/logs/unpaywall.jsonl.

Resume-safe: skips entries whose acquisition.source is already set.
"""

from __future__ import annotations

import json
import sys
import time
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from corpus.config import PDF_DIR, RUNTIME
from corpus.manifest import commit as manifest_commit, load as manifest_load
from corpus.keys import read_keys_file
from corpus.keys import key_arg
from corpus.config import require_env
from corpus.fetch import http

LOG = RUNTIME / "logs" / "unpaywall.jsonl"
API_DELAY = 0.2  # seconds between Unpaywall calls (5 req/s)
PDF_TIMEOUT = 30
API_TIMEOUT = 15
MIN_PDF_BYTES = 1024


def http_get(url: str, timeout: int) -> tuple[int, dict, bytes]:
    r = http.get(url, timeout=timeout)
    if r.status == 0 or r.truncated:
        raise OSError(r.error)
    if r.status >= 400:
        raise urllib.error.HTTPError(url, r.status, r.error, r.headers, None)
    return r.status, r.headers, r.body


def fetch_unpaywall(doi: str, email: str) -> tuple[int, dict | None, str]:
    """Return (http_status, parsed_json, error_msg)."""
    url = (f"https://api.unpaywall.org/v2/{urllib.parse.quote(doi, safe='/:')}?"
           + urllib.parse.urlencode({"email": email}))
    try:
        status, _hdrs, body = http_get(url, API_TIMEOUT)
        if status != 200:
            return status, None, f"http {status}"
        return status, json.loads(body.decode("utf-8")), ""
    except urllib.error.HTTPError as e:
        return e.code, None, f"http {e.code}: {e.reason}"
    except Exception as e:
        return 0, None, f"{type(e).__name__}: {e}"


def download_pdf(url: str, dest: Path) -> tuple[bool, str, int]:
    """Return (ok, error, byte_count)."""
    try:
        status, hdrs, body = http_get(url, PDF_TIMEOUT)
    except Exception as e:
        return False, f"{type(e).__name__}: {e}", 0
    if status != 200:
        return False, f"http {status}", 0
    if len(body) < MIN_PDF_BYTES:
        return False, f"too small ({len(body)} bytes)", len(body)
    if not body.startswith(b"%PDF"):
        ctype = hdrs.get("Content-Type", "?")
        return False, f"not a pdf (content-type={ctype}, head={body[:8]!r})", len(body)
    dest.write_bytes(body)
    return True, "", len(body)


def parse_keys() -> set[str] | None:
    """--key K (repeatable) / --keys-file F restrict the run to those keys and
    run only those keys. With no keys, every bib key with a DOI and no PDF is
    tried (whole-bib scope). Unknown flags are an error, so a
    stray --help no longer starts a full run."""
    import argparse
    import unicodedata
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--key", type=key_arg, action="append", default=[])
    ap.add_argument("--keys-file")
    a = ap.parse_args()
    keys = list(a.key)
    if a.keys_file:
        keys += read_keys_file(a.keys_file)
    return {unicodedata.normalize("NFC", k) for k in keys} or None


def main() -> int:
    only = parse_keys()
    email = require_env("UNPAYWALL_EMAIL")

    manifest = manifest_load()

    candidates = []
    for key, entry in manifest.items():
        if only is not None and key not in only:
            continue
        if not entry.get("doi"):
            continue
        if (entry.get("acquisition") or {}).get("source") is not None:
            continue
        candidates.append(key)

    print(f"Manifest entries:                      {len(manifest)}")
    print(f"Candidates (DOI + genera + unfetched): {len(candidates)}")
    print(f"Estimated wall time:                   ~{len(candidates) * (API_DELAY + 1) / 60:.1f} min")
    print()

    PDF_DIR.mkdir(parents=True, exist_ok=True)
    LOG.parent.mkdir(parents=True, exist_ok=True)

    n_ok = n_no_oa = n_no_link = n_api_err = n_dl_err = 0
    log_fh = LOG.open("a")
    started = time.time()

    for i, key in enumerate(candidates, 1):
        entry = manifest[key]
        if not isinstance(entry.get("acquisition"), dict):   # stub entries may lack it
            entry["acquisition"] = {}
        doi = entry["doi"]
        now = datetime.now(timezone.utc).isoformat()

        status, payload, err = fetch_unpaywall(doi, email)
        log_record = {"ts": now, "key": key, "doi": doi, "status": status,
                      "error": err, "payload": payload}

        if not payload:
            entry["acquisition"]["source"] = "unpaywall_error"
            entry["acquisition"]["fetched_at"] = now
            entry["conversion"]["notes"] = err
            n_api_err += 1
            log_fh.write(json.dumps(log_record) + "\n")
            log_fh.flush()
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
            entry["acquisition"]["source"] = ("unpaywall_no_pdf_link" if open_access
                                              else "unpaywall_no_oa")
            entry["acquisition"]["fetched_at"] = now
            if open_access:
                n_no_link += 1
            else:
                n_no_oa += 1
            log_fh.write(json.dumps(log_record) + "\n")
            log_fh.flush()
            print(f"[{i:>4}/{len(candidates)}] {key:30s} "
                  + ("open access, but no direct PDF link" if open_access else "no OA copy"))
            time.sleep(API_DELAY)
            continue

        dest = PDF_DIR / f"{key}.pdf"
        ok, dl_err, nbytes = download_pdf(pdf_url, dest)
        if ok:
            entry["acquisition"] = {
                "source": "unpaywall",
                "url": pdf_url,
                "pdf_path": f"pdfs/{key}.pdf",
                "fetched_at": now,
            }
            n_ok += 1
            print(f"[{i:>4}/{len(candidates)}] {key:30s} OK   {nbytes/1024:.0f} KB")
        else:
            entry["acquisition"]["source"] = "unpaywall_dl_failed"
            entry["acquisition"]["url"] = pdf_url
            entry["acquisition"]["fetched_at"] = now
            entry["conversion"]["notes"] = dl_err
            n_dl_err += 1
            print(f"[{i:>4}/{len(candidates)}] {key:30s} DL ERROR: {dl_err}")

        log_record["pdf_url"] = pdf_url
        log_record["downloaded"] = ok
        log_record["dl_error"] = dl_err
        log_record["bytes"] = nbytes
        log_fh.write(json.dumps(log_record) + "\n")
        log_fh.flush()

        if i % 25 == 0:
            manifest_commit(manifest)

        time.sleep(API_DELAY)

    manifest_commit(manifest)
    log_fh.close()

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
