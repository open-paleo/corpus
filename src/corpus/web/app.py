"""The `corpus web` application: search the index, read a paper, check it against its PDF.

Read-only and for one person on this machine. There is no authentication, so
the server binds to 127.0.0.1, refuses requests whose Host header names any
other host (a DNS-rebinding page would otherwise read files through the
browser), validates every key and file name as a single path segment inside
its folder, and serves only sanitised HTML under a strict Content-Security-Policy.
"""
from __future__ import annotations

import mimetypes
import sqlite3
import unicodedata
from contextlib import closing
from pathlib import Path

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles

from corpus import config
from corpus.bib import citation_venue
from corpus.manifest import load as manifest_load
from corpus.supplementary import resolve_parent
from corpus.web import render
from corpus.web.query import Filters, QueryError, search

STATIC = Path(__file__).parent / "static"
LOOPBACK = ("127.0.0.1", "localhost", "[::1]")

# The ES modules and decoders PDF.js loads; some platforms' mimetypes lack them,
# and a browser refuses a module script served as application/octet-stream.
mimetypes.add_type("text/javascript", ".mjs")
mimetypes.add_type("application/wasm", ".wasm")

CSP = ("default-src 'self'; script-src 'self' 'wasm-unsafe-eval'; worker-src 'self'; "
       "style-src 'self'; img-src 'self' data: blob:; font-src 'self' data:; "
       "connect-src 'self'; object-src 'none'; base-uri 'none'; form-action 'self'; "
       "frame-ancestors 'none'")


def segment(name: str) -> str:
    """A request-supplied key or file name, accepted only as one path segment."""
    name = unicodedata.normalize("NFC", name)
    if not name or name.startswith(".") or any(c in name for c in "/\\\0"):
        raise HTTPException(400, "bad name")
    return name


def inside(base: Path, path: Path) -> Path:
    """path, resolved, if it lies inside base; else 404."""
    resolved = path.resolve()
    if not resolved.is_relative_to(base.resolve()):
        raise HTTPException(404)
    return resolved


class Corpus:
    """The files the app reads, with the manifest and index facts cached until they change."""

    def __init__(self):
        self._manifest = (None, {})
        self._facets = (None, {})

    def manifest(self) -> dict:
        mtime = config.MANIFEST.stat().st_mtime_ns if config.MANIFEST.exists() else None
        if self._manifest[0] != mtime:
            self._manifest = (mtime, manifest_load(config.MANIFEST, track=False) if mtime else {})
        return self._manifest[1]

    def connect(self):
        if not config.DB.exists():
            raise HTTPException(503, "No search index yet: run `corpus index`.")
        return closing(sqlite3.connect(f"file:{config.DB}?mode=ro", uri=True))

    def facets(self) -> dict:
        mtime = config.DB.stat().st_mtime_ns if config.DB.exists() else None
        if self._facets[0] != mtime:
            out = {"papers": 0, "genera": [], "clades": [], "tools": [], "years": [None, None]}
            if mtime:
                with self.connect() as con:
                    genera, clades = set(), set()
                    for g, c in con.execute("SELECT genera, clades FROM papers"):
                        genera.update(n for n in (g or "").split(", ") if n)
                        clades.update(n for n in (c or "").split(", ") if n)
                    out["papers"] = con.execute(
                        "SELECT count(*) FROM papers WHERE title NOT LIKE '[suppl:%'").fetchone()[0]
                    out["genera"], out["clades"] = sorted(genera), sorted(clades)
                    out["tools"] = [t for (t,) in con.execute(
                        "SELECT DISTINCT tool FROM papers WHERE tool != '' ORDER BY tool")]
                    out["years"] = list(con.execute(
                        "SELECT min(CAST(year AS INTEGER)), max(CAST(year AS INTEGER)) "
                        "FROM papers WHERE year != ''").fetchone())
            self._facets = (mtime, out)
        return self._facets[1]

    def locate(self, key: str) -> dict:
        """Where a paper's or a supplement's files are; 404 if it has no markdown."""
        key = segment(key)
        manifest = self.manifest()
        if key in manifest:
            md = inside(config.MD_DIR, config.MD_DIR / f"{key}.md")
            loc = {"key": key, "parent": None, "name": None, "md": md,
                   "original": config.MD_DIR / f"{key}.original.md",
                   "pdf": config.PDF_DIR / f"{key}.pdf"}
        else:
            parsed = resolve_parent(key, set(manifest))
            if parsed is None:
                raise HTTPException(404, "no such paper")
            md = inside(config.SUPP_MD_DIR, config.SUPP_MD_DIR / f"{key}.md")
            loc = {"key": key, "parent": parsed[0], "name": parsed[1], "md": md,
                   "original": None, "pdf": config.SUPP_PDF_DIR / f"{key}.pdf"}
        if not md.exists():
            raise HTTPException(404, "no markdown for that key")
        return loc


def create_app(allowed_hosts: set[str] | None = None) -> FastAPI:
    """The app; `allowed_hosts` are the Host header values it answers (host:port)."""
    app = FastAPI(title="corpus", docs_url=None, redoc_url=None, openapi_url=None)
    corpus = Corpus()

    @app.middleware("http")
    async def guard(request: Request, call_next):
        if allowed_hosts is not None and request.headers.get("host") not in allowed_hosts:
            return PlainTextResponse("unknown host", status_code=421)
        response = await call_next(request)
        response.headers["Content-Security-Policy"] = CSP
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        return response

    app.mount("/static", StaticFiles(directory=STATIC), name="static")

    @app.get("/")
    def index():
        return FileResponse(STATIC / "index.html", media_type="text/html; charset=utf-8")

    @app.get("/api/facets")
    def facets():
        return corpus.facets()

    @app.get("/api/search")
    def api_search(q: str = "", syntax: bool = False, genus: str = "", clade: str = "",
                   year_from: int | None = Query(None, alias="from"),
                   year_to: int | None = Query(None, alias="to"),
                   tool: str = "", translated: bool = False, supplements: bool = False,
                   sort: str = "relevance", page: int = Query(1, ge=1)):
        with_supp = None
        if supplements:
            with_supp = frozenset(k for k, e in corpus.manifest().items() if e.get("supplementary"))
        filters = Filters(genus=genus, clade=clade, year_from=year_from, year_to=year_to,
                          tool=tool, translated=translated, with_supplements=with_supp)
        with corpus.connect() as con:
            try:
                return search(con, q, filters, syntax=syntax, sort=sort, page=page)
            except QueryError as e:
                return JSONResponse({"detail": str(e)}, status_code=400)

    @app.get("/api/paper/{key}")
    def api_paper(key: str, version: str = Query("text", pattern="^(text|original)$")):
        loc = corpus.locate(key)
        manifest = corpus.manifest()
        entry = manifest.get(loc["parent"] or loc["key"], {})
        cit = entry.get("citation") or {}
        if version == "original":
            if not (loc["original"] and loc["original"].exists()):
                raise HTTPException(404, "no original for that key")
            body = render.file_html(loc["original"])
        else:
            body = render.file_html(loc["md"])
        supplements = []
        if not loc["parent"]:
            for name, rec in sorted((entry.get("supplementary") or {}).items()):
                stem = f"{loc['key']}-{name}"
                if (config.SUPP_MD_DIR / f"{stem}.md").exists():
                    supplements.append({"key": stem, "name": name})
        conversion = (entry.get("supplementary") or {}).get(loc["name"], {}) \
            if loc["parent"] else entry.get("conversion") or {}
        return {
            "key": loc["key"], "parent": loc["parent"], "name": loc["name"],
            "title": cit.get("title") or "", "authors": cit.get("author") or "",
            "year": cit.get("year") or "", "venue": citation_venue(cit),
            "doi": cit.get("doi") or entry.get("doi") or "",
            "genera": entry.get("genera") or [], "clades": entry.get("clades") or [],
            "conversion": conversion, "supplements": supplements,
            "has_pdf": loc["pdf"].exists(),
            "has_original": bool(loc["original"] and loc["original"].exists()),
            "version": version, "html": body,
        }

    @app.get("/pdf/{key}")
    def serve_pdf(key: str):
        loc = corpus.locate(key)
        base = config.SUPP_PDF_DIR if loc["parent"] else config.PDF_DIR
        pdf = inside(base, loc["pdf"])
        if not pdf.exists():
            raise HTTPException(404, "no PDF for that key")
        return FileResponse(pdf, media_type="application/pdf")

    @app.get("/md/{key}")
    def serve_md(key: str, version: str = Query("text", pattern="^(text|original)$")):
        loc = corpus.locate(key)
        path = loc["md"] if version == "text" else loc["original"]
        if not (path and path.exists()):
            raise HTTPException(404)
        return PlainTextResponse(path.read_text(encoding="utf-8", errors="replace"),
                                 media_type="text/markdown; charset=utf-8")

    return app


def loopback_hosts(host: str, port: int) -> set[str]:
    return {f"{h}:{port}" for h in {*LOOPBACK, host}}
