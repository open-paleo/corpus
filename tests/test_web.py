"""The `corpus web` app, against a small corpus in a temporary directory."""
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("nh3")
from fastapi.testclient import TestClient  # noqa: E402

from corpus import config  # noqa: E402
from corpus.web import query  # noqa: E402
from corpus.web.app import create_app  # noqa: E402

REPO = Path(__file__).resolve().parents[1]

HOSTILE = """# A paper

<script>alert(1)</script>
<img src=x onerror="alert(2)">
[click](javascript:alert(3)) and [doi](https://doi.org/10.1/x)
Area 12 cm<sup>2</sup>, *Tyrannosaurus rex* from the Late-Cretaceous.

| Specimen | Length |
| --- | --- |
| MPC-D 100/1 | 12 |
"""


@pytest.fixture
def corpus(tmp_path, monkeypatch):
    md = tmp_path / "markdown"
    (md / "supplementary").mkdir(parents=True)
    (tmp_path / "pdfs").mkdir()
    (tmp_path / "supplementary").mkdir()
    papers = {
        "smith2001a": ("A new ankylosaur", "2001", ["Ankylosaurus"], ["Ankylosauria"], HOSTILE),
        "o'connor2010a": ("Femora of sauropods", "2010", ["Brachiosaurus"], [],
                          "The femur of O'Connor's sauropod."),
        "li2015a": ("一种新的恐龙", "2015", [], ["Sauropoda"], "A translated femur."),
    }
    manifest = {}
    for key, (title, year, genera, clades, body) in papers.items():
        manifest[key] = {"citation": {"title": title, "year": year, "author": "Author, A."},
                         "genera": genera, "clades": clades,
                         "conversion": {"tool": "marker"}}
        (md / f"{key}.md").write_text(body, encoding="utf-8")
    manifest["smith2001a"]["supplementary"] = {"matrix": {"tool": "marker"}}
    (md / "supplementary" / "smith2001a-matrix.md").write_text("Character matrix femur.",
                                                                encoding="utf-8")
    (md / "li2015a.original.md").write_text("一根股骨。", encoding="utf-8")
    (tmp_path / "pdfs" / "smith2001a.pdf").write_bytes(b"%PDF-1.4 test")
    (tmp_path / "supplementary" / "smith2001a-matrix.pdf").write_bytes(b"%PDF-1.4 supp")
    (tmp_path / ".env").write_text("SECRET=1", encoding="utf-8")
    (tmp_path / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    subprocess.run([sys.executable, str(REPO / "scripts" / "indexer.py")], check=True,
                   capture_output=True, env={**os.environ, "CORPUS_ROOT": str(tmp_path)})
    for name, rel in [("MANIFEST", "manifest.json"), ("MD_DIR", "markdown"),
                      ("SUPP_MD_DIR", "markdown/supplementary"), ("PDF_DIR", "pdfs"),
                      ("SUPP_PDF_DIR", "supplementary"), ("DB", "index/papers.db")]:
        monkeypatch.setattr(config, name, tmp_path / rel)
    return tmp_path


@pytest.fixture
def client(corpus):
    return TestClient(create_app(allowed_hosts={"testserver"}))


def keys(r):
    assert r.status_code == 200, r.text
    return {h["key"] for h in r.json()["hits"]}


@pytest.mark.parametrize("q", ["T. rex", "Late-Cretaceous", "MPC-D 100/1", "O'Connor",
                               "ilium:", '"unbalanced', "Tyranno*"])
def test_plain_queries_never_fail(client, q):
    assert client.get("/api/search", params={"q": q}).status_code == 200


def test_plain_queries_find_their_words(client):
    assert keys(client.get("/api/search", params={"q": "MPC-D 100/1"})) == {"smith2001a"}
    assert keys(client.get("/api/search", params={"q": "O'Connor"})) == {"o'connor2010a"}
    assert keys(client.get("/api/search", params={"q": "Tyranno*"})) == {"smith2001a"}


def test_query_errors_are_readable(client):
    r = client.get("/api/search", params={"q": "- / ;"})
    assert r.status_code == 400 and "no words" in r.json()["detail"]
    r = client.get("/api/search", params={"q": "ilium:", "syntax": 1})
    assert r.status_code == 400 and r.json()["detail"].startswith("Search syntax")


def test_filters(client):
    get = lambda **p: keys(client.get("/api/search", params=p))  # noqa: E731
    assert get(genus="Ankylosaurus") == {"smith2001a", "smith2001a-matrix"}
    assert get(genus="Ankylo") == set()                     # whole names only
    assert get(clade="Sauropoda") == {"li2015a"}
    assert get(q="femur", translated=1) == {"li2015a"}
    assert get(q="femur", supplements=1) == set()           # the parent has no "femur"
    assert get(q="femur", **{"from": 2005, "to": 2012}) == {"o'connor2010a"}


def test_paging_and_total(client, monkeypatch):
    monkeypatch.setattr(query, "PER_PAGE", 2)
    first = client.get("/api/search", params={"q": "femur", "sort": "year"}).json()
    second = client.get("/api/search", params={"q": "femur", "sort": "year", "page": 2}).json()
    assert first["total"] == second["total"] == 3
    assert len(first["hits"]) == 2 and len(second["hits"]) == 1
    assert not {h["key"] for h in first["hits"]} & {h["key"] for h in second["hits"]}


def test_snippets_are_escaped_and_marked():
    raw = f"<b>x</b> *{query.MARK_OPEN}Tyrannosaurus{query.MARK_CLOSE}* rex"
    assert query.snippet_html(raw) == "&lt;b&gt;x&lt;/b&gt; <mark>Tyrannosaurus</mark> rex"


def test_paper_html_is_sanitised(client):
    html = client.get("/api/paper/smith2001a").json()["html"]
    assert "<script" not in html and "onerror" not in html and "javascript:" not in html
    assert "<img" not in html
    assert "<sup>2</sup>" in html and "<table>" in html and "<em>Tyrannosaurus rex</em>" in html
    assert 'href="https://doi.org/10.1/x"' in html and 'rel="noopener noreferrer"' in html


def test_paper_record_supplements_and_original(client):
    d = client.get("/api/paper/smith2001a").json()
    assert d["supplements"] == [{"key": "smith2001a-matrix", "name": "matrix"}]
    assert d["has_pdf"] and not d["has_original"] and d["clades"] == ["Ankylosauria"]
    s = client.get("/api/paper/smith2001a-matrix").json()
    assert (s["parent"], s["name"], s["has_pdf"]) == ("smith2001a", "matrix", True)
    o = client.get("/api/paper/li2015a", params={"version": "original"}).json()
    assert o["has_original"] and "一根股骨" in o["html"]
    assert client.get("/api/paper/smith2001a", params={"version": "original"}).status_code == 404


def test_files(client):
    assert client.get("/pdf/smith2001a").content == b"%PDF-1.4 test"
    assert client.get("/pdf/smith2001a-matrix").content == b"%PDF-1.4 supp"
    assert client.get("/pdf/li2015a").status_code == 404
    assert client.get("/md/o'connor2010a").text.startswith("The femur")


# "..%2Fmanifest.json" reaches the app encoded; a bare ".." is resolved by the client.
@pytest.mark.parametrize("key", [".env", "..%2Fmanifest.json", "a\\b", "nosuch2000a"])
def test_bad_keys(client, key):
    assert client.get(f"/api/paper/{key}").status_code in (400, 404)
    assert client.get(f"/md/{key}").status_code in (400, 404)


def test_host_check_and_headers(corpus):
    app = TestClient(create_app(allowed_hosts={"127.0.0.1:8765"}))
    assert app.get("/api/facets").status_code == 421               # Host: testserver
    r = app.get("/api/facets", headers={"host": "127.0.0.1:8765"})
    assert r.status_code == 200 and r.json()["papers"] == 3
    assert "script-src 'self'" in r.headers["content-security-policy"]
    assert r.headers["x-content-type-options"] == "nosniff"


def test_static_types(client):
    assert client.get("/").headers["content-type"].startswith("text/html")
    assert client.get("/static/pdfjs/pdf.min.mjs").headers["content-type"].startswith("text/javascript")
    assert client.get("/static/pdfjs/wasm/openjpeg.wasm").headers["content-type"] == "application/wasm"
