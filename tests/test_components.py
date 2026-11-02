"""Vision cache, HTTP cap, bib parsing, supplementary keys, pdf_text helpers,
script helpers, and web UI path/escaping regressions."""
import http.server
import json
import sqlite3
import threading

import pytest

from corpus import bib
from corpus.supplementary import resolve_parent
from corpus.vision import cache as vcache


# ---- vision cache -------------------------------------------------------

def test_vision_cache_roundtrip_and_model_rules(tmp_path):
    vcache.write(tmp_path, 0, {"prompt_id": "verbatim-v1", "model": "claude-sonnet-5", "markdown": "x"})
    hit = vcache.read(tmp_path, 0, model="claude-sonnet-5-5", prompt_id="verbatim-v1")
    assert hit and hit["cached_hit"] and hit["markdown"] == "x"       # compatible model
    assert vcache.read(tmp_path, 0, model="claude-haiku-5-5", prompt_id="verbatim-v1") is None
    assert vcache.read(tmp_path, 0, model="claude-sonnet-5", prompt_id="translate-v1") is None
    assert vcache.read(tmp_path, 0, model="claude-sonnet-5", prompt_id="verbatim-v1", force=True) is None
    assert (tmp_path / "page-0001.json").exists()


def test_vision_cache_truncated_entry_is_a_miss_and_removed(tmp_path):
    p = vcache.page_path(tmp_path, 2)
    p.write_text('{"prompt_id": "verbatim-v1", "mark', encoding="utf-8")
    assert vcache.read(tmp_path, 2, model="claude-sonnet-5-5", prompt_id="verbatim-v1") is None
    assert not p.exists()


# ---- HTTP ---------------------------------------------------------------

@pytest.fixture
def server():
    class H(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            n = int(self.path.strip("/") or 0)
            self.send_response(404 if n < 0 else 200)
            self.end_headers()
            self.wfile.write(b"x" * max(n, 0))
            ua.append(self.headers.get("User-Agent"))

        def log_message(self, *a):
            pass

    ua = []
    srv = http.server.HTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_port}", ua
    srv.shutdown()


def test_http_get_caps_body(server, monkeypatch):
    from corpus.fetch import http as fhttp
    base, ua = server
    monkeypatch.setenv("CORPUS_CONTACT", "someone@example.org")
    r = fhttp.get(f"{base}/1000", max_bytes=10_000)
    assert r.status == 200 and len(r.body) == 1000 and not r.truncated
    r = fhttp.get(f"{base}/5000000", max_bytes=100_000)
    assert r.truncated and r.error and len(r.body) <= 100_000 + fhttp.CHUNK
    assert "someone@example.org" in ua[0]
    fhttp.get(f"{base}/1", browser_ua=True)
    assert ua[-1] == fhttp.BROWSER_UA


def test_http_get_never_raises():
    from corpus.fetch import http as fhttp
    r = fhttp.get("http://127.0.0.1:9/", timeout=2)
    assert r.status == 0 and r.error


# ---- bib, supplementary -------------------------------------------------

def test_parse_bib_and_venue(tmp_path):
    f = tmp_path / "r.bib"
    f.write_text("@article{smith2001a,\n  title = {A  {Nested} title},\n  journal = {J. Paleo.},\n"
                 "  year = {2001}\n}\n@phdthesis{jones2019a,\n  title = {T},\n  school = {Uni},\n"
                 "  year = {2019}\n}\n", encoding="utf-8")
    b = bib.parse_bib(f)
    assert b["smith2001a"]["entry_type"] == "article"
    assert bib.citation_venue(b["smith2001a"]) == "J. Paleo."
    assert bib.citation_venue(b["jones2019a"]) == "Uni" and bib.is_thesis(b["jones2019a"])


@pytest.mark.parametrize("stem,expected", [
    ("prieto-márquez2010c-matrix", ("prieto-márquez2010c", "matrix")),
    ("carrano2012a-appendix-3", ("carrano2012a", "appendix-3")),
    ("carrano2012a", None),
    ("nobody2000a-x", None),
])
def test_resolve_parent_longest_prefix(stem, expected):
    known = {"prieto-márquez2010c", "prieto", "carrano2012a"}
    assert resolve_parent(stem, known) == expected


# ---- pdf_text helpers ---------------------------------------------------

def _ch(c, x0, y0=0.0, w=5.0, h=10.0, gen=False):
    return {"c": c, "generated": gen, "hyphen": False, "font": "F", "flags": 0, "dir": (1.0, 0.0),
            "size": 10.0, "bbox": (x0, y0, x0 + w, y0 + h), "origin": (x0, y0 + h)}


def test_join_broken_lines_rejoins_touching_glyphs():
    from corpus.pdf.text import _join_broken_lines
    chars = [_ch("M", 0), _ch("\r", 5, gen=True), _ch("\n", 5, gen=True), _ch("a", 5)]
    assert [c["c"] for c in _join_broken_lines(chars)] == ["M", "a"]
    wrapped = [_ch("M", 100), _ch("\r", 105, gen=True), _ch("\n", 105, gen=True), _ch("a", 0, y0=12)]
    assert [c["c"] for c in _join_broken_lines(wrapped)] == ["M", "\r", "\n", "a"]


def test_place_marks_moves_mark_before_its_letter():
    from corpus.pdf.text import _place_marks
    # "M u n" with a diaeresis drawn over "u" but emitted after "n"
    chars = [_ch("M", 0), _ch("u", 5), _ch("n", 10), _ch("€", 5.5, y0=-2, w=4)]
    assert "".join(c["c"] for c in _place_marks(chars)) == "M€un"
    # a "~" that overlaps no letter stays where it is
    lone = [_ch("a", 0), _ch(" ", 5, gen=True), _ch("~", 40)]
    assert "".join(c["c"] for c in _place_marks(lone)) == "a ~"


def test_bare_font_strips_subset_tag_only():
    from corpus.pdf.text import bare_font
    assert bare_font("EAGIHG+OpenSans") == "OpenSans"
    assert bare_font("AdvTTe692faf0+02") == "AdvTTe692faf0+02"


# ---- script helpers -----------------------------------------------------

def test_fused_call_word_and_call_forms():
    import repair_fused_superscript_refs as R
    assert R.word_forms("BELLARD IN I")[:2] == ["INI", "BELLARDINI"]
    assert "Rogers" in R.word_forms("Rubilar-Ro´gers")
    assert "Li" in R.word_forms("NingLi") and "Escaso" in R.word_forms("andFernandoEscaso")
    assert all(len(w) >= 2 for w in R.word_forms("x A"))
    assert R.call_forms("2-4,7,15") == ["2-4,7,15", "2-4, 7, 15", "2-4", "2"]
    assert R.is_isotope("Ar", "40") and R.is_isotope("Sr", "87")
    assert not R.is_isotope("Li", "1,2") and not R.is_isotope("Mo", "5")
    assert R.is_unit_exponent("km", "2")


@pytest.mark.parametrize("line,expected", [
    ("- (Closed access) (Buy) smith2001a: Smith; ...", ("smith2001a", ["Closed access", "Buy"])),
    ("- d'emic2013a: ...", ("d'emic2013a", [])),
    ("not a bullet", None),
    ("- (unclosed smith2001a: x", None),
])
def test_report_entry_line(line, expected):
    G = pytest.importorskip("generate_missing_report")   # not in every checkout
    assert G.parse_entry_line(line) == expected


def test_intake_discover_helpers():
    import intake_discover as D
    assert D.parse_cited_pages("501-505") == 5
    assert D.parse_cited_pages("12") == 1
    assert D.parse_cited_pages("xii") is None
    assert D.split_stem_suffix("foo2015a") == ("foo2015", "a")
    assert D.detect_split_collision("foo2015", {"foo2015a", "foo2015b"})


# ---- web UI -------------------------------------------------------------

@pytest.fixture
def ui(tmp_path, monkeypatch):
    pytest.importorskip("fastapi")
    pytest.importorskip("httpx")
    from fastapi.testclient import TestClient
    webui = pytest.importorskip("webui")   # not in every checkout
    md = tmp_path / "markdown"
    md.mkdir()
    (md / "o'connor2005a.md").write_text("# Body <img src=x onerror=alert(1)>", encoding="utf-8")
    (tmp_path / "secret.md").write_text("SECRET", encoding="utf-8")
    # assembled at runtime so the pre-commit secret check does not flag the test
    (tmp_path / ".env").write_text("ANTHROPIC_" + "API_KEY=" + "fake-value-for-test", encoding="utf-8")
    man = tmp_path / "manifest.json"
    man.write_text(json.dumps({"o'connor2005a": {"citation": {"title": "T"}}}), encoding="utf-8")
    db = tmp_path / "papers.db"
    con = sqlite3.connect(db)
    con.execute("CREATE VIRTUAL TABLE papers USING fts5(citation_key UNINDEXED, title, authors, "
                "year UNINDEXED, journal, genera, tool UNINDEXED, body)")
    con.execute("INSERT INTO papers VALUES (?,?,?,?,?,?,?,?)",
                ("o'connor2005a", "T", "A", "2005", "J", "", "marker",
                 "air sacs <img src=x onerror=alert(1)> in theropods"))
    con.commit()
    con.close()
    for name, val in (("MD_DIR", md), ("PDF_DIR", tmp_path / "pdfs"), ("IMG_DIR", tmp_path / "img"),
                      ("MANIFEST", man), ("DB", db)):
        monkeypatch.setattr(webui, name, val)
    webui._manifest_cache["mtime"] = None
    return TestClient(webui.app)


@pytest.mark.parametrize("key", ["../secret", "../.env", "..", ".env", "a/b"])
def test_webui_rejects_traversal(ui, key):
    r = ui.get("/api/paper", params={"key": key})
    assert r.status_code in (400, 404) and "SECRET" not in r.text and "fake-value" not in r.text


def test_webui_serves_apostrophe_key_and_escapes_snippets(ui):
    r = ui.get("/api/paper", params={"key": "o'connor2005a"})
    assert r.status_code == 200 and r.json()["title"] == "T"
    hits = ui.get("/api/search", params={"q": "theropods"}).json()["hits"]
    assert hits and "<img" not in hits[0]["snippet"] and "&lt;img" in hits[0]["snippet"]
    assert "<mark>theropods</mark>" in hits[0]["snippet"]


# ---- repair registry and the corpus command ----------------------------

def test_repair_registry_modules_expose_repair():
    from corpus.repair import REPAIRS
    names = [r.name for r in REPAIRS]
    assert names == ["dash", "symbols", "accents", "diaeresis", "ref_links", "fused_calls"]
    for r in REPAIRS:
        assert callable(r.load().repair) and callable(r.load().main)


def test_repair_eligibility():
    from corpus.repair import BY_NAME
    marker = {"conversion": {"tool": "marker"}}
    vision = {"conversion": {"tool": "claude-vision"}}
    assert BY_NAME["diaeresis"].eligible("k", marker, "M€uller")
    assert not BY_NAME["diaeresis"].eligible("k", vision, "M€uller")
    assert not BY_NAME["diaeresis"].eligible("k", marker, "Muller")
    assert BY_NAME["fused_calls"].eligible("k", marker, "") and not BY_NAME["fused_calls"].eligible("k", vision, "")
    assert BY_NAME["accents"].eligible("lindoso2019a", {}, "") and not BY_NAME["accents"].eligible("x2000a", {}, "")


def test_cli_lists_and_dispatches(capsys):
    from pathlib import Path
    from corpus import cli
    assert cli.main([]) == 0 and "repair all" in capsys.readouterr().out
    assert cli.main(["nonsense"]) == 2
    assert cli.main(["repair", "nope"]) == 2
    scripts = Path(__file__).resolve().parents[1] / "scripts"
    # Every checkout has the core commands; others may be left out.
    for words in [("intake", "acquire"), ("convert", "marker"), ("index",), ("search",)]:
        assert (scripts / f"{cli.COMMANDS[words][0]}.py").is_file(), words
    listed = cli.usage()
    for words, (script, _) in cli.COMMANDS.items():
        assert (" ".join(words) in listed) == (scripts / f"{script}.py").is_file() \
            or len(words) == 1, words


# ---- bib source and corpus status ---------------------------------------

def test_bib_source_auto_order(tmp_path, monkeypatch):
    from corpus import bib
    data, bundled = tmp_path / "data", tmp_path / "bib" / "references.bib"
    monkeypatch.setattr(bib, "data_repo", lambda: data)
    monkeypatch.setattr(bib, "BUNDLED_BIB", bundled)
    monkeypatch.delenv("CORPUS_BIB_SOURCE", raising=False)
    assert bib.bib_source() == "remote"
    bundled.parent.mkdir()
    bundled.write_text("")
    assert bib.bib_source() == "bundled"
    (data / "dist").mkdir(parents=True)
    (data / "dist" / "references.bib").write_text("")
    assert bib.bib_source() == "sibling"
    monkeypatch.setenv("CORPUS_BIB_SOURCE", "remote")
    assert bib.bib_source() == "remote"
    monkeypatch.setenv("CORPUS_BIB_SOURCE", "nonsense")
    with pytest.raises(SystemExit):
        bib.bib_source()


def test_citing_reads_json_export_like_yamls(tmp_path, monkeypatch):
    import json as _json
    from corpus import bib
    export = {"genera": {"Iguanodon": {"genus": "Iguanodon",
                                       "references": [{"id": "mantell1825a"}],
                                       "species": [{"described_in": ["owen1842a"]}]}},
              "clades": {"Ornithopoda": {"references": [{"id": "mantell1825a"}]},
                         "Empty": {}}}
    (tmp_path / "data" / "dist").mkdir(parents=True)
    (tmp_path / "data" / "dist" / "open-paleo.json").write_text(_json.dumps(export))
    monkeypatch.setattr(bib, "data_repo", lambda: tmp_path / "data")
    refs, no_refs, where = bib.citing()
    assert refs["mantell1825a"] == {"genera": {"Iguanodon"}, "clades": {"Ornithopoda"}}
    assert refs["owen1842a"]["genera"] == {"Iguanodon"}
    assert no_refs == ["Empty (Empty)"] and where.endswith("open-paleo.json")


def test_status_counts_from_disk(tmp_path, monkeypatch, capsys):
    import json as _json
    from corpus import status
    (tmp_path / "pdfs").mkdir()
    (tmp_path / "markdown").mkdir()
    for k in ("held", "conv", "skip"):
        (tmp_path / "pdfs" / f"{k}.pdf").write_bytes(b"%PDF")
    (tmp_path / "markdown" / "conv.md").write_text("body")
    m = {"held": {"in_bib": True, "acquisition": {"source": "manual"}},
         "conv": {"in_bib": True, "conversion": {"tool": "marker"}, "genera": ["Iguanodon"]},
         "skip": {"in_bib": True, "conversion": {"skipped": True}},
         "gone": {"in_bib": True, "doi": "10.1/x", "acquisition": {"source": "unpaywall_no_oa"}},
         "orphan": {"in_bib": False}}
    (tmp_path / "manifest.json").write_text(_json.dumps(m))
    monkeypatch.setattr(status, "PDF_DIR", tmp_path / "pdfs")
    monkeypatch.setattr(status, "MD_DIR", tmp_path / "markdown")
    monkeypatch.setattr(status, "DB", tmp_path / "none.db")
    monkeypatch.setattr(status, "manifest_load",
                        lambda track=False: _json.loads((tmp_path / "manifest.json").read_text()))
    assert status.main(["--missing"]) == 0
    out = capsys.readouterr().out
    assert "bib entries             4" in out and "held (PDF)              3" in out
    assert "converted               1" in out and "held, not converted     1" in out
    assert "PDF, no markdown        1" in out and "missing                 1   1 with a DOI" in out
    assert "\n  gone " in out
    status.main(["--genus", "iguanodon"])
    assert "bib entries             1" in capsys.readouterr().out


def test_failed_source_sentinels():
    from corpus.manifest import failed_source
    for s in ("unpaywall_no_oa", "oa_direct_landing_failed", "x_no_match", "unpaywall_error",
              "oa_direct_no_meta_tag", "unpaywall_html_landing"):
        assert failed_source(s)
    for s in (None, "", "manual", "unpaywall", "bhl", "oa_direct_pdf", "unpaywall_retry"):
        assert not failed_source(s)


# ---- dropped-ligature repair: wordfreq mode -------------------------------

@pytest.fixture
def lig():
    pytest.importorskip("wordfreq")
    import fix_dropped_ligatures as L
    return L, L.WordFreqLexicon(domain={"theropoda", "midshaft"})


def test_ligatures_wordfreq_repairs_breaks(lig):
    L, lex = lig
    out, n = L.fix("The frst and diferent results were identifed in the lef femur.", lex)
    assert out == "The first and different results were identified in the left femur."
    assert L.fix("The midshaf of the bone.", lex)[0] == "The midshaft of the bone."


def test_ligatures_wordfreq_leaves_real_words(lig):
    L, lex = lig
    text = ("The feet were found by Fuentes near the Eifel. "
            "As noted by Lefèvre et al. and in los mamíferos fosilíferos.")
    assert L.fix(text, lex) == (text, 0)


def test_ligatures_wordfreq_judges_accented_words_whole(lig):
    L, lex = lig
    assert L.fix("una síntesis estratigráfca y científca", lex)[0] == \
        "una síntesis estratigráfica y científica"


def test_ligatures_wordfreq_th_in_a_broken_paper(lig):
    L, lex = lig
    text = ("Tis is it. Tey found it. Terefore we agree. Te bone is long. "
            "the end of the bone, this base and they say. Te end.")
    out, _ = L.fix(text, lex)
    assert out.startswith("This is it. They found it. Therefore we agree. The bone")


def test_ligatures_names_protect(lig):
    L, lex = lig
    lex.names = {"fona"}
    assert L.fix("including Fona herzogae and others", lex)[1] == 0


# ---- discovery and fetchers ----------------------------------------------

def test_discover_lists_fetched_but_unconverted(tmp_path, monkeypatch):
    import intake_discover as D
    (tmp_path / "pdfs").mkdir()
    (tmp_path / "markdown").mkdir()
    for k in ("fetched", "done", "cited"):
        (tmp_path / "pdfs" / f"{k}.pdf").write_bytes(b"%PDF-1.4\n")
    (tmp_path / "markdown" / "done.md").write_text("body")
    monkeypatch.setattr(D, "PDF_DIR", tmp_path / "pdfs")
    monkeypatch.setattr(D, "MD_DIR", tmp_path / "markdown")
    monkeypatch.setattr(D, "git_status_pdfs", lambda: (set(), set()))
    m = {"fetched": {"acquisition": {"source": "unpaywall"}},
         "done": {"acquisition": {"source": "unpaywall"}},
         "cited": {"acquisition": {"source": "manual"}, "conversion": {"skipped": True}}}
    pending = D.gather_pending(m)
    assert [(p.key, p.git_state) for p in pending] == [("fetched", "unconverted")]


def test_pmc_https_url():
    import fetch_pmc_oa as F
    url, md5 = F.https_url("s3://pmc-oa-opendata/PMC1.1/PMC1.1.pdf?md5=abc")
    assert url == "https://pmc-oa-opendata.s3.amazonaws.com/PMC1.1/PMC1.1.pdf" and md5 == "abc"


# ---- BHL fetch -------------------------------------------------------------

def test_bhl_narrow_item_keeps_the_consistent_offset():
    import bhl_fetch as F
    # A volume: seq = printed + 20 for the article, plus a stray "460" far away.
    api = [{"PageNumbers": [{"Number": str(i - 20)}]} for i in range(1, 600)]
    api[50] = {"PageNumbers": [{"Number": "460"}]}         # seq 51 misread as p. 460
    api[481] = {"PageNumbers": []}                           # an unnumbered plate, seq 482
    seqs, note = F.narrow_item(1, {}, {"pages": "459-464"}, api)
    assert seqs == list(range(479, 485)) and "BHL page metadata" in note
    assert F.narrow_item(1, {}, {"pages": None}, api)[0] == []
    assert F.narrow_item(1, {}, {"pages": "9000-9001"}, api)[0] == []


def test_bhl_helpers():
    import bhl_fetch as F
    assert F.printed_number({"PageNumbers": [{"Prefix": "Page", "Number": "%[459]"}]}) == 459
    assert F.printed_number({"PageNumbers": []}) is None
    assert F.parse_seqs("212-231") == list(range(212, 232))
    with pytest.raises(SystemExit):
        F.parse_seqs("231-212")
    assert F.accepted({"method": "doi_part"}) and not F.accepted({"method": "fuzzy"})
    assert F.accepted({"method": "item_unique", "reviewed": True})
    assert F.parse_page_range("pp. 187–201") == (187, 201)


def test_every_script_parses_its_arguments():
    """A script with no argument parser treats --help as a real run
    (bhl_index.py rebuilt this archive's index that way, 2026-11-02)."""
    from pathlib import Path
    scripts = Path(__file__).resolve().parents[1] / "scripts"
    bare = []
    for p in sorted(scripts.glob("*.py")):
        text = p.read_text(encoding="utf-8")
        if "__main__" not in text:
            continue
        if not ("argparse" in text or "from corpus.repair." in text):
            bare.append(p.name)
    assert not bare, f"scripts that would run on --help: {bare}"
