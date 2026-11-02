"""HTTP GET for the fetchers: one User-Agent policy, timeouts, a size cap.

The User-Agent names the archive and a contact address (corpus.config
.user_agent: CORPUS_CONTACT, else UNPAYWALL_EMAIL). A browser User-Agent is
sent only when a caller asks for it (`browser_ua=True`): some publisher
landing pages refuse anything else, and whether to impersonate a browser is
the operator's decision, made per run with a flag.

The body is read in chunks and abandoned past `max_bytes`, so a server that
sends far more than expected cannot make the process hold all of it.
"""
from __future__ import annotations

import urllib.error
import urllib.request
from dataclasses import dataclass, field

from corpus.config import user_agent

BROWSER_UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
              "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36")
DEFAULT_MAX_BYTES = 200 * 1024 * 1024
CHUNK = 1 << 20


@dataclass
class Response:
    status: int
    headers: dict = field(default_factory=dict)
    body: bytes = b""
    error: str = ""
    url: str = ""          # final URL after redirects
    truncated: bool = False


def _read_capped(resp, max_bytes: int | None) -> tuple[bytes, bool]:
    if max_bytes is None:
        return resp.read(), False
    parts, n = [], 0
    while True:
        chunk = resp.read(CHUNK)
        if not chunk:
            return b"".join(parts), False
        n += len(chunk)
        if n > max_bytes:
            return b"".join(parts), True
        parts.append(chunk)


def get(url: str, *, timeout: float = 30, max_bytes: int | None = DEFAULT_MAX_BYTES,
        accept: str = "*/*", browser_ua: bool = False, referer: str | None = None,
        headers: dict | None = None) -> Response:
    """GET url. Never raises for HTTP or network errors: they come back as
    Response.status (0 for no response) and Response.error. A body over
    max_bytes is cut off with truncated=True and an error."""
    h = {"User-Agent": BROWSER_UA if browser_ua else user_agent(), "Accept": accept}
    if browser_ua:
        h["Accept-Language"] = "en-US,en;q=0.9"
    if referer:
        h["Referer"] = referer
    h.update(headers or {})
    req = urllib.request.Request(url, headers=h)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body, cut = _read_capped(resp, max_bytes)
            err = f"body over {max_bytes} bytes; abandoned" if cut else ""
            return Response(resp.status, dict(resp.headers), body, err, resp.geturl(), cut)
    except urllib.error.HTTPError as e:
        try:
            body, cut = _read_capped(e, min(max_bytes or 1 << 20, 1 << 20))
        except Exception:
            body, cut = b"", False
        return Response(e.code, dict(e.headers or {}), body, f"http {e.code}: {e.reason}", url, cut)
    except Exception as e:
        return Response(0, {}, b"", f"{type(e).__name__}: {e}", url)


def head(url: str, *, timeout: float = 30) -> Response:
    """HEAD url (headers only, e.g. Content-Length). Never raises."""
    req = urllib.request.Request(url, method="HEAD", headers={"User-Agent": user_agent()})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return Response(resp.status, dict(resp.headers), b"", "", resp.geturl())
    except urllib.error.HTTPError as e:
        return Response(e.code, dict(e.headers or {}), b"", f"http {e.code}: {e.reason}", url)
    except Exception as e:
        return Response(0, {}, b"", f"{type(e).__name__}: {e}", url)


def download(url: str, dest, *, timeout: float = 120) -> tuple[int, str]:
    """Stream url to dest (via dest.part, renamed on success) without holding
    it in memory: (bytes written, error). For large files such as data dumps;
    get() is for responses that fit in memory."""
    from pathlib import Path
    dest = Path(dest)
    part = dest.with_suffix(dest.suffix + ".part")
    req = urllib.request.Request(url, headers={"User-Agent": user_agent()})
    try:
        n = 0
        with urllib.request.urlopen(req, timeout=timeout) as resp, open(part, "wb") as f:
            while chunk := resp.read(CHUNK):
                f.write(chunk)
                n += len(chunk)
        part.replace(dest)
        return n, ""
    except urllib.error.HTTPError as e:
        return 0, f"http {e.code}: {e.reason}"
    except Exception as e:
        return 0, f"{type(e).__name__}: {e}"
    finally:
        part.unlink(missing_ok=True)
