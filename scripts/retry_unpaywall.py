#!/usr/bin/env python3
# corpus-command: fetch retry-unpaywall | retry Unpaywall download failures, keeping what the URL served
"""
Retry Tier 1 download failures, capturing HTML bodies for inspection.

For each manifest entry whose acquisition.source is an unpaywall_* error:
  - unpaywall_error (API 404):   leave as-is (Unpaywall has no record;
                                   nothing to retry against the API).
  - unpaywall_dl_failed:         re-fetch the recorded pdf_url. If a PDF is
                                   returned, save it. If HTML is returned, save
                                   the body to .runtime/logs/dl_failures/{key}.html
                                   so the user can inspect what the URL serves.

Many publisher hosts refuse a non-browser User-Agent. --browser-ua sends a
browser's instead; as with `corpus fetch oa-direct --browser-ua`, impersonating
a browser is the operator's call, so it is off unless asked for.
"""

from __future__ import annotations

import json
import sys
import time
from datetime import datetime, timezone
from corpus.config import PDF_DIR, RUNTIME
from corpus.manifest import commit as manifest_commit, load as manifest_load
from corpus.fetch import http
import argparse

# Set from --browser-ua in main().
BROWSER = False
HTML_DIR = RUNTIME / "logs" / "dl_failures"
LOG = RUNTIME / "logs" / "unpaywall_retry.jsonl"

PDF_TIMEOUT = 60
DELAY = 0.5
MIN_PDF_BYTES = 1024


def fetch(url: str, timeout: int) -> tuple[int, dict, bytes, str]:
    """Return (status, headers, body, error)."""
    r = http.get(url, timeout=timeout, browser_ua=BROWSER,
                 accept="application/pdf,text/html,*/*;q=0.8")
    return r.status, r.headers, r.body, r.error

def classify(body: bytes, headers: dict) -> str:
    if not body:
        return "empty"
    if body.startswith(b"%PDF"):
        return "pdf"
    head = body[:200].lstrip().lower()
    if head.startswith(b"<!doctype") or head.startswith(b"<html"):
        return "html"
    ct = headers.get("Content-Type", "").lower()
    if "html" in ct:
        return "html"
    if "pdf" in ct:
        return "pdf-bad-magic"
    return "other"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--browser-ua", action="store_true",
                    help="send a browser User-Agent (many publisher hosts refuse others)")
    global BROWSER
    BROWSER = ap.parse_args().browser_ua
    manifest = manifest_load()
    HTML_DIR.mkdir(parents=True, exist_ok=True)
    LOG.parent.mkdir(parents=True, exist_ok=True)

    api_404 = [k for k, e in manifest.items() if (e.get("acquisition") or {}).get("source") == "unpaywall_error"]
    dl_failed = [k for k, e in manifest.items() if (e.get("acquisition") or {}).get("source") == "unpaywall_dl_failed"]

    print(f"API 404s (no Unpaywall record, will mark permanent): {len(api_404)}")
    print(f"DL failures (will retry{' with a browser UA' if BROWSER else ''}):           {len(dl_failed)}")
    print()

    # API 404s: mark permanently so Tier 3 can see them
    for k in api_404:
        manifest[k]["acquisition"]["source"] = "tier1_no_unpaywall_record"
    manifest_commit(manifest)

    log_fh = LOG.open("a")
    n_pdf = n_html = n_403 = n_other = n_err = 0

    for i, k in enumerate(dl_failed, 1):
        entry = manifest[k]
        url = entry["acquisition"].get("url")
        if not url:
            print(f"[{i:>3}/{len(dl_failed)}] {k:30s} no url recorded; skipping")
            continue

        now = datetime.now(timezone.utc).isoformat()
        status, hdrs, body, err = fetch(url, PDF_TIMEOUT)
        kind = classify(body, hdrs)

        record = {
            "ts": now,
            "key": k,
            "url": url,
            "status": status,
            "error": err,
            "content_type": hdrs.get("Content-Type"),
            "bytes": len(body),
            "kind": kind,
        }

        if kind == "pdf" and len(body) >= MIN_PDF_BYTES:
            (PDF_DIR / f"{k}.pdf").write_bytes(body)
            entry["acquisition"]["source"] = "unpaywall_retry"
            entry["acquisition"]["pdf_path"] = f"pdfs/{k}.pdf"
            entry["acquisition"]["fetched_at"] = now
            entry["conversion"]["notes"] = ""
            n_pdf += 1
            print(f"[{i:>3}/{len(dl_failed)}] {k:30s} OK   {len(body)/1024:.0f} KB")
        elif kind == "html":
            (HTML_DIR / f"{k}.html").write_bytes(body)
            entry["acquisition"]["source"] = "unpaywall_html_landing"
            entry["conversion"]["notes"] = f"landing page saved to .runtime/logs/dl_failures/{k}.html"
            n_html += 1
            print(f"[{i:>3}/{len(dl_failed)}] {k:30s} HTML {len(body)/1024:.0f} KB → saved")
        elif status == 403:
            entry["acquisition"]["source"] = "tier1_blocked_403"
            entry["conversion"]["notes"] = err
            n_403 += 1
            print(f"[{i:>3}/{len(dl_failed)}] {k:30s} 403" + (" even with a browser UA" if BROWSER else ""))
        elif err:
            entry["acquisition"]["source"] = "tier1_dl_error"
            entry["conversion"]["notes"] = err
            n_err += 1
            print(f"[{i:>3}/{len(dl_failed)}] {k:30s} ERR {err}")
        else:
            entry["acquisition"]["source"] = "tier1_unknown_response"
            entry["conversion"]["notes"] = f"kind={kind} status={status} ct={hdrs.get('Content-Type')}"
            n_other += 1
            print(f"[{i:>3}/{len(dl_failed)}] {k:30s} ?? kind={kind} status={status}")

        log_fh.write(json.dumps(record) + "\n")
        log_fh.flush()

        if i % 25 == 0:
            manifest_commit(manifest)

        time.sleep(DELAY)

    manifest_commit(manifest)
    log_fh.close()

    print()
    print("Retry summary:")
    print(f"  PDF rescued:                      {n_pdf}")
    print(f"  HTML landing page (saved):        {n_html}")
    print(f"  403:                              {n_403}")
    print(f"  other errors:                     {n_err}")
    print(f"  unknown response:                 {n_other}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
