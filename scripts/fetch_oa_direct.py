#!/usr/bin/env python3
# corpus-command: fetch oa-direct | publisher landing page -> citation_pdf_url
"""
Tier 4 acquisition: open-access PDFs from the publisher's own landing page,
for papers Unpaywall has no usable copy of.

Strategy: for every still-missing entry with a DOI, follow the DOI redirect
to the publisher landing page, then parse <meta name="citation_pdf_url">
(or a few publisher-specific patterns). PeerJ, PLOS, Frontiers, MDPI, AMNH,
Acta Pal Polonica, Zenodo, etc. all expose this meta tag, so one generic
fetcher catches the long tail of OA misses.

Resume-safe.
"""

from __future__ import annotations

import argparse
import re
import sys
import time
import urllib.parse

from corpus.config import PDF_DIR, log_path
from corpus.fetch import http, tier
from corpus.io import atomic_write_bytes, jsonl_append
from corpus.keys import add_key_args, resolve_keys
from corpus.manifest import commit as manifest_commit, load as manifest_load

LOG = log_path("oa_direct.jsonl")

TIMEOUT = 30
DELAY = 1.0
MIN_PDF_BYTES = 1024
MAX_PDF_BYTES = 200 * 1024 * 1024

META_PDF_RE = re.compile(
    r'<meta[^>]+name=["\']citation_pdf_url["\'][^>]+content=["\']([^"\']+)["\']',
    re.IGNORECASE,
)


# Set from --browser-ua in main(): publisher landing pages often refuse a
# non-browser User-Agent; impersonating a browser is the operator's call.
BROWSER = False


def http_get(url: str, timeout: int, referer: str | None = None) -> tuple[int, dict, bytes, str, str]:
    """Return (status, headers, body, error, final_url). Bodies over
    MAX_PDF_BYTES are abandoned while streaming, not read whole first."""
    r = http.get(url, timeout=timeout, max_bytes=MAX_PDF_BYTES, browser_ua=BROWSER,
                 referer=referer, accept="text/html,application/pdf,*/*;q=0.8")
    return r.status, r.headers, r.body, r.error, r.url or url

def normalize_doi(doi: str) -> str:
    """Strip whitespace inserted by data entry typos (saw 10.1371/journal. pone)."""
    return re.sub(r"\s+", "", doi.strip())


def resolve_pdf_url(landing_url: str, html: str) -> str | None:
    m = META_PDF_RE.search(html)
    if m:
        url = m.group(1).strip()
        if url.startswith("//"):
            url = "https:" + url
        elif url.startswith("/"):
            base = urllib.parse.urlsplit(landing_url)
            url = f"{base.scheme}://{base.netloc}{url}"
        return url
    # Frontiers occasionally uses og:url+/pdf appendix; PeerJ exposes meta
    # already. Skip publisher-specific fallbacks for now.
    return None


def fetch_one(doi: str) -> tuple[str, dict, bytes | None]:
    """
    Try to grab a PDF for `doi` via DOI redirect → landing page → citation_pdf_url.
    Returns (outcome, extras, pdf_bytes_or_none).
    outcome ∈ {pdf, no_meta_tag, landing_failed, pdf_download_failed,
               doi_resolution_failed, malformed}.
    """
    if not doi:
        return "malformed", {}, None
    doi_clean = normalize_doi(doi)
    extras: dict = {"doi": doi, "doi_clean": doi_clean}

    landing_url = f"https://doi.org/{urllib.parse.quote(doi_clean, safe='/:')}"
    status, hdrs, body, err, final_url = http_get(landing_url, TIMEOUT)
    extras.update({"landing_status": status, "landing_url": final_url,
                   "landing_ct": hdrs.get("Content-Type", ""), "landing_bytes": len(body)})

    if status == 0 or err and status >= 400:
        return ("doi_resolution_failed" if status == 0 else "landing_failed",
                {**extras, "error": err}, None)

    # Sometimes DOI redirects directly to a PDF (e.g., AMNH DSpace)
    if body.startswith(b"%PDF") and len(body) >= MIN_PDF_BYTES:
        return "pdf", extras, body

    text = body.decode("utf-8", "replace")
    pdf_url = resolve_pdf_url(final_url, text)
    if not pdf_url:
        return "no_meta_tag", extras, None

    extras["pdf_url"] = pdf_url
    pstatus, phdrs, pbody, perr, _ = http_get(pdf_url, TIMEOUT * 2, referer=final_url)
    extras["pdf_status"] = pstatus
    extras["pdf_ct"] = phdrs.get("Content-Type", "")
    extras["pdf_bytes"] = len(pbody)

    if pbody.startswith(b"%PDF") and MIN_PDF_BYTES <= len(pbody) <= MAX_PDF_BYTES:
        return "pdf", extras, pbody
    return "pdf_download_failed", {**extras, "error": perr or f"non-pdf body ({len(pbody)} bytes)"}, None


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    add_key_args(ap)   # --key/--keys-file: only those keys; else every key with no PDF
    ap.add_argument("--browser-ua", action="store_true",
                    help="send a browser User-Agent (many publisher pages refuse others)")
    args = ap.parse_args()
    global BROWSER
    BROWSER = args.browser_ua
    only = set(resolve_keys(args)) or None
    manifest = manifest_load()

    # Eligible: has DOI + no PDF on disk yet (regardless of source state)
    candidates = tier.candidates(manifest, only)

    print(f"Tier 4 candidates (DOI + still missing PDF): {len(candidates)}")
    print(f"Estimated wall time: ~{len(candidates) * 2.5 / 60:.0f} min")
    print()

    PDF_DIR.mkdir(parents=True, exist_ok=True)

    counts = {"pdf": 0, "no_meta_tag": 0, "landing_failed": 0,
              "pdf_download_failed": 0, "doi_resolution_failed": 0, "malformed": 0}

    for i, key in enumerate(candidates, 1):
        entry = manifest[key]
        doi = entry["doi"]
        outcome, extras, pdf = fetch_one(doi)
        counts[outcome] += 1

        rec = {"ts": tier.now(), "key": key, "outcome": outcome, **extras}

        if outcome == "pdf":
            atomic_write_bytes(PDF_DIR / f"{key}.pdf", pdf)
            tier.record_pdf(entry, "oa_direct", key,
                            extras.get("pdf_url") or extras.get("landing_url"))
            print(f"[{i:>3}/{len(candidates)}] {key:30s} OK   {len(pdf)/1024:.0f} KB  via {urllib.parse.urlsplit(extras.get('pdf_url','')).netloc}")
        else:
            tier.record_failure(entry, f"oa_direct_{outcome}")
            print(f"[{i:>3}/{len(candidates)}] {key:30s} {outcome}")

        jsonl_append(LOG, rec)

        if i % 25 == 0:
            manifest_commit(manifest)

        time.sleep(DELAY)

    manifest_commit(manifest)

    print()
    print("Tier 4 summary:")
    for k, v in counts.items():
        print(f"  {k:30s} {v}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
