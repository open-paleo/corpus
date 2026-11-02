"""The Unpaywall API and PDF download shared by fetch_unpaywall.py (manifest
keys) and fetch_unpaywall_by_doi.py (key/DOI lists).

lookup(doi, email) -> (status, record or None, error); download_pdf(url, dest)
-> (ok, error, bytes). Neither raises for HTTP or network errors.
"""
from __future__ import annotations

import json
import urllib.parse
from pathlib import Path

from corpus.config import log_path
from corpus.fetch import http
from corpus.io import atomic_write_bytes

LOG = log_path("unpaywall.jsonl")
API_DELAY = 0.2       # seconds between Unpaywall calls (5 req/s)
API_TIMEOUT = 15
PDF_TIMEOUT = 30
MIN_PDF_BYTES = 1024


def lookup(doi: str, email: str) -> tuple[int, dict | None, str]:
    """(http status, the Unpaywall record, error message)."""
    url = (f"https://api.unpaywall.org/v2/{urllib.parse.quote(doi, safe='/:')}?"
           + urllib.parse.urlencode({"email": email}))
    r = http.get(url, timeout=API_TIMEOUT, accept="application/json")
    if r.status != 200 or r.truncated:
        return r.status, None, r.error or f"http {r.status}"
    try:
        return r.status, json.loads(r.body.decode("utf-8")), ""
    except ValueError as e:
        return r.status, None, f"{type(e).__name__}: {e}"


def download_pdf(url: str, dest: Path) -> tuple[bool, str, int]:
    """Download url to dest (atomically) if it is a PDF: (ok, error, bytes)."""
    r = http.get(url, timeout=PDF_TIMEOUT)
    if r.status != 200 or r.truncated:
        return False, r.error or f"http {r.status}", 0
    body = r.body
    if len(body) < MIN_PDF_BYTES:
        return False, f"too small ({len(body)} bytes)", len(body)
    if not body.startswith(b"%PDF"):
        ctype = r.headers.get("Content-Type", "?")
        return False, f"not a pdf (content-type={ctype}, head={body[:8]!r})", len(body)
    atomic_write_bytes(Path(dest), body)
    return True, "", len(body)
