"""The search index: its columns, incremental updates and rebuilds.

Each test runs scripts/index.py in a subprocess with CORPUS_ROOT pointing at
a temporary corpus, since corpus.config fixes its paths at import.
"""
import json
import os
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
INDEXER = REPO / "scripts" / "index.py"
SEARCH = REPO / "scripts" / "search.py"

OLD_SCHEMA = """CREATE VIRTUAL TABLE papers USING fts5(
    citation_key UNINDEXED, title, authors, year UNINDEXED, journal,
    genera, tool UNINDEXED, body,
    tokenize = 'porter unicode61 remove_diacritics 2')"""


def run(script, root, *args):
    env = {**os.environ, "CORPUS_ROOT": str(root)}
    return subprocess.run([sys.executable, str(script), *args], env=env,
                          capture_output=True, text=True, check=True).stdout


@pytest.fixture
def corpus(tmp_path):
    md = tmp_path / "markdown"
    (md / "supplementary").mkdir(parents=True)
    (tmp_path / "manifest.json").write_text(json.dumps({
        "smith2001a": {"citation": {"title": "A new ankylosaur", "author": "Smith, A.",
                                    "year": "2001", "journal": "J. Paleo."},
                       "genera": ["Ankylosaurus"], "clades": ["Ankylosauria", "Thyreophora"],
                       "conversion": {"tool": "marker"}},
        "li2010a": {"citation": {"title": "一种新的恐龙", "year": "2010"},
                    "genera": [], "clades": ["Sauropoda"],
                    "conversion": {"tool": "claude-vision"}},
    }), encoding="utf-8")
    (md / "smith2001a.md").write_text("The tail club of this ankylosaur.", encoding="utf-8")
    (md / "li2010a.md").write_text("A sauropod femur, translated.", encoding="utf-8")
    (md / "li2010a.original.md").write_text("一根蜥脚类股骨。", encoding="utf-8")
    (md / "supplementary" / "smith2001a-matrix.md").write_text("Character matrix.", encoding="utf-8")
    return tmp_path


def rows(root):
    con = sqlite3.connect(root / "index" / "papers.db")
    cols = [r[1] for r in con.execute("PRAGMA table_info(papers)")]
    data = {r[0]: dict(zip(cols, r)) for r in con.execute("SELECT * FROM papers")}
    con.close()
    return cols, data


def test_columns_and_values(corpus):
    run(INDEXER, corpus)
    cols, data = rows(corpus)
    assert cols == ["citation_key", "title", "authors", "year", "journal", "genera",
                    "tool", "body", "clades", "translated"]
    assert data["smith2001a"]["clades"] == "Ankylosauria, Thyreophora"
    assert data["smith2001a"]["translated"] == ""
    assert data["li2010a"]["translated"] == "1"
    # a supplement inherits its parent's clades, and is never "translated"
    assert data["smith2001a-matrix"]["clades"] == "Ankylosauria, Thyreophora"
    assert data["smith2001a-matrix"]["translated"] == ""
    # the body is still column 7, which readers' snippet() calls rely on
    con = sqlite3.connect(corpus / "index" / "papers.db")
    snip = con.execute("SELECT snippet(papers, 7, '[', ']', '…', 8) FROM papers "
                       "WHERE papers MATCH 'club'").fetchone()[0]
    assert "[club]" in snip


def test_old_index_is_rebuilt_in_place(corpus):
    (corpus / "index").mkdir()
    con = sqlite3.connect(corpus / "index" / "papers.db")
    con.execute(OLD_SCHEMA)
    con.execute("INSERT INTO papers VALUES ('gone2000a','','','','','','','old row')")
    con.commit()
    con.close()
    out = run(INDEXER, corpus)
    assert "older column list" in out
    cols, data = rows(corpus)
    assert cols[-2:] == ["clades", "translated"]
    assert set(data) == {"smith2001a", "li2010a", "smith2001a-matrix"}
    assert not (corpus / "index" / "papers.db.tmp").exists()


def test_translation_added_later_is_picked_up(corpus):
    (corpus / "markdown" / "li2010a.original.md").unlink()
    run(INDEXER, corpus)
    assert rows(corpus)[1]["li2010a"]["translated"] == ""
    (corpus / "markdown" / "li2010a.original.md").write_text("原文", encoding="utf-8")
    out = run(INDEXER, corpus)
    assert "updated:  1" in out
    assert rows(corpus)[1]["li2010a"]["translated"] == "1"


def test_search_filters(corpus):
    run(INDEXER, corpus)
    out = run(SEARCH, corpus, "--clades", "Sauropoda")
    assert "li2010a" in out and "smith2001a" not in out
    out = run(SEARCH, corpus, "--translated")
    assert "li2010a" in out and "smith2001a" not in out
    out = run(SEARCH, corpus, "club")
    assert "clades: Ankylosauria, Thyreophora" in out
