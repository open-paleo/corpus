#!/usr/bin/env python3
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

import json
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from corpus.config import PDF_DIR, RUNTIME
from corpus.manifest import commit as manifest_commit, load as manifest_load
from corpus.keys import read_keys_file
from corpus.keys import key_arg
from corpus.fetch import http

LOG = RUNTIME / "logs" / "oa_direct.jsonl"

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
    ap.add_argument("--browser-ua", action="store_true",
                    help="send a browser User-Agent (many publisher pages refuse others)")
    a = ap.parse_args()
    global BROWSER
    BROWSER = a.browser_ua
    keys = list(a.key)
    if a.keys_file:
        keys += read_keys_file(a.keys_file)
    return {unicodedata.normalize("NFC", k) for k in keys} or None


def main() -> int:
    only = parse_keys()
    manifest = manifest_load()

    # Eligible: has DOI + no PDF on disk yet (regardless of source state)
    candidates = []
    for k, e in manifest.items():
        if only is not None and k not in only:
            continue
        if not e.get("doi"):
            continue
        if e["acquisition"].get("pdf_path") and (PDF_DIR / f"{k}.pdf").exists():
            continue
        candidates.append(k)

    print(f"Tier 4 candidates (DOI + still missing PDF): {len(candidates)}")
    print(f"Estimated wall time: ~{len(candidates) * 2.5 / 60:.0f} min")
    print()

    PDF_DIR.mkdir(parents=True, exist_ok=True)
    LOG.parent.mkdir(parents=True, exist_ok=True)
    log_fh = LOG.open("a")

    counts = {"pdf": 0, "no_meta_tag": 0, "landing_failed": 0,
              "pdf_download_failed": 0, "doi_resolution_failed": 0, "malformed": 0}

    for i, key in enumerate(candidates, 1):
        entry = manifest[key]
        if not isinstance(entry.get("acquisition"), dict):   # stub entries may lack it
            entry["acquisition"] = {}
        doi = entry["doi"]
        now = datetime.now(timezone.utc).isoformat()
        outcome, extras, pdf = fetch_one(doi)
        counts[outcome] += 1

        rec = {"ts": now, "key": key, "outcome": outcome, **extras}

        if outcome == "pdf":
            (PDF_DIR / f"{key}.pdf").write_bytes(pdf)
            entry["acquisition"] = {
                "source": "oa_direct",
                "url": extras.get("pdf_url") or extras.get("landing_url"),
                "pdf_path": f"pdfs/{key}.pdf",
                "fetched_at": now,
            }
            print(f"[{i:>3}/{len(candidates)}] {key:30s} OK   {len(pdf)/1024:.0f} KB  via {urllib.parse.urlsplit(extras.get('pdf_url','')).netloc}")
        else:
            entry["acquisition"]["source"] = f"oa_direct_{outcome}"
            entry["acquisition"]["fetched_at"] = now
            print(f"[{i:>3}/{len(candidates)}] {key:30s} {outcome}")

        log_fh.write(json.dumps(rec) + "\n")
        log_fh.flush()

        if i % 25 == 0:
            manifest_commit(manifest)

        time.sleep(DELAY)

    manifest_commit(manifest)
    log_fh.close()

    print()
    print("Tier 4 summary:")
    for k, v in counts.items():
        print(f"  {k:30s} {v}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
