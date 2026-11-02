#!/usr/bin/env python3
"""
Acquisition from the PMC Open Access subset (NCBI's PMC Article Dataset).

For open-access journals whose own sites refuse scripted requests (PeerJ,
behind a Cloudflare challenge since 2026-10) and whose Unpaywall record has
no direct PDF link. PMC's web pages are also behind a browser check, so this
uses the two programmatic routes both services publish:

  1. DOI -> PMCID with the Europe PMC REST search API
     (www.ebi.ac.uk/europepmc/webservices/rest/search, query DOI:"...").
  2. The article's metadata in the PMC Article Dataset, a public S3 bucket
     (pmc-oa-opendata.s3.amazonaws.com/metadata/{PMCID}.{version}.json):
     is_pmc_openaccess, license_code, and pdf_url with an MD5.
  3. Download that PDF over HTTPS and check the MD5.

Only articles in the open-access subset are fetched (CC licences and the
like); nothing is fetched from a page that asks for a browser check.

Candidates: keys with a DOI and no PDF on disk, or --key/--keys-file. Writes
pdfs/{key}.pdf and acquisition.source = "pmc_oa"; a miss is recorded as
pmc_oa_not_found (no PMCID), pmc_oa_no_oa (not in the open-access subset) or
pmc_oa_dl_failed, unless the key already records a real source. Appends one
line per key to .runtime/logs/pmc_oa.jsonl. Resume-safe.

Usage:
  ./.runtime/venv/bin/python scripts/fetch_pmc_oa.py --key baiano2023a
  ./.runtime/venv/bin/python scripts/fetch_pmc_oa.py          # every candidate
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
import time
import urllib.parse

from corpus.config import PDF_DIR, log_path
from corpus.fetch import http, tier
from corpus.io import atomic_write_bytes, jsonl_append
from corpus.keys import add_key_args, resolve_keys
from corpus.manifest import commit as manifest_commit, load as manifest_load

LOG = log_path("pmc_oa.jsonl")
EPMC_SEARCH = "https://www.ebi.ac.uk/europepmc/webservices/rest/search"
BUCKET = "https://pmc-oa-opendata.s3.amazonaws.com"
MAX_VERSIONS = 6          # metadata/{PMCID}.{1..N}.json: the latest wins
DELAY = 0.5
MAX_PDF_BYTES = 300 * 1024 * 1024


def pmcid_for(doi: str) -> tuple[str | None, str]:
    """(PMCID or None, error) for a DOI, from Europe PMC."""
    q = urllib.parse.urlencode({"query": f'DOI:"{doi.strip()}"', "format": "json",
                                "resultType": "lite"})
    r = http.get(f"{EPMC_SEARCH}?{q}", timeout=30, max_bytes=2 << 20,
                 accept="application/json")
    if r.status != 200:
        return None, r.error or f"http {r.status}"
    try:
        results = json.loads(r.body)["resultList"]["result"]
    except (ValueError, KeyError):
        return None, "unreadable Europe PMC response"
    want = doi.strip().lower()
    for res in results:
        if (res.get("doi") or "").lower() == want and res.get("pmcid"):
            return res["pmcid"], ""
    return None, ""


def metadata_for(pmcid: str) -> dict | None:
    """The latest version's metadata from the PMC Article Dataset, or None."""
    latest = None
    for version in range(1, MAX_VERSIONS + 1):
        r = http.get(f"{BUCKET}/metadata/{pmcid}.{version}.json", timeout=30,
                     max_bytes=1 << 20)
        if r.status != 200:
            break
        try:
            latest = json.loads(r.body)
        except ValueError:
            break
    return latest


def https_url(s3_url: str) -> tuple[str, str | None]:
    """(HTTPS URL, md5) for an s3://pmc-oa-opendata/... link."""
    parts = urllib.parse.urlsplit(s3_url)
    md5 = urllib.parse.parse_qs(parts.query).get("md5", [None])[0]
    return f"{BUCKET}{parts.path}", md5


def fetch_one(doi: str) -> tuple[str, dict, bytes | None]:
    """(outcome, extras, pdf) with outcome pdf / not_found / no_oa / dl_failed."""
    pmcid, err = pmcid_for(doi)
    if not pmcid:
        return ("dl_failed" if err else "not_found"), {"error": err}, None
    meta = metadata_for(pmcid)
    extras = {"pmcid": pmcid}
    if not meta or not meta.get("is_pmc_openaccess") or not meta.get("pdf_url"):
        return "no_oa", extras, None
    url, md5 = https_url(meta["pdf_url"])
    extras.update(url=url, license=meta.get("license_code"), version=meta.get("version"))
    r = http.get(url, timeout=300, max_bytes=MAX_PDF_BYTES, accept="application/pdf")
    if r.status != 200 or not r.body.startswith(b"%PDF"):
        return "dl_failed", {**extras, "error": r.error or f"http {r.status}, not a PDF"}, None
    if md5 and hashlib.md5(r.body).hexdigest() != md5:
        return "dl_failed", {**extras, "error": "md5 mismatch"}, None
    return "pdf", extras, r.body


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    add_key_args(ap)
    args = ap.parse_args()
    only = set(resolve_keys(args)) or None

    manifest = manifest_load()
    candidates = tier.candidates(manifest, only)
    print(f"PMC OA candidates (DOI, no PDF on disk): {len(candidates)}")
    PDF_DIR.mkdir(parents=True, exist_ok=True)

    counts = {"pdf": 0, "not_found": 0, "no_oa": 0, "dl_failed": 0}
    for i, key in enumerate(candidates, 1):
        entry = manifest[key]
        outcome, extras, pdf = fetch_one(entry["doi"])
        counts[outcome] += 1
        if outcome == "pdf":
            atomic_write_bytes(PDF_DIR / f"{key}.pdf", pdf)
            tier.record_pdf(entry, "pmc_oa", key, extras["url"], license=extras.get("license"))
            print(f"[{i:>4}/{len(candidates)}] {key:30s} OK   {len(pdf) / 1024:.0f} KB  "
                  f"{extras['pmcid']} ({extras.get('license')})")
        else:
            tier.record_failure(entry, f"pmc_oa_{outcome}")
            print(f"[{i:>4}/{len(candidates)}] {key:30s} {outcome}"
                  + (f"  {extras['error']}" if extras.get("error") else ""))
        jsonl_append(LOG, {"ts": tier.now(), "key": key, "doi": entry["doi"],
                           "outcome": outcome, **extras})
        if i % 25 == 0:
            manifest_commit(manifest)
        time.sleep(DELAY)
    manifest_commit(manifest)

    print("\nPMC OA summary:")
    for k, v in counts.items():
        print(f"  {k:12s} {v}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
