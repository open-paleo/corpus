"""Page markers: written by the converters, kept by every rewrite, hidden from search."""
import argparse
import json
import os
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

from corpus import pages
from corpus.pages import join, numbers

REPO = Path(__file__).resolve().parents[1]

BHL_COVER = """# Biodiversity Heritage Library

https://www.biodiversitylibrary.org/

Holding Institution: Harvard University, Museum of Comparative Zoology, Ernst Mayr Library

Generated 1 May 2026 12:48 AM"""
HEAD = "ANNALS OF THE SOCIETY OF NATURAL HISTORY"


def sample(n_body: int = 7) -> str:
    """A vision-style paper: a BHL cover, a blank page, then body pages that
    share a running head and carry page numbers (which look like a gutter
    sequence to the line-number strippers)."""
    body = [f"{HEAD}\n\n— {200 + i} —\n\nThe femur of this specimen is numerou s and "
            f"robust; paragraph {i} continues with ordinary prose about the skeleton."
            for i in range(1, n_body + 1)]
    return join([(1, BHL_COVER), (2, "This page intentionally left blank.")]
                + [(i + 3, b) for i, b in enumerate(body)])


def test_join_strip_and_segments():
    text = join([(1, "one"), (2, "two")])
    assert text == "<!-- page 1 -->\n\none\n\n---\n\n<!-- page 2 -->\n\ntwo\n"
    assert numbers(text) == [1, 2]
    assert pages.strip(text) == "one\n\n---\n\ntwo\n"
    assert pages.segments(text) == [("<!-- page 1 -->", "one\n\n---"), ("<!-- page 2 -->", "two")]
    assert pages.segments("no markers\n") == [("", "no markers")]


def test_from_marker_output():
    raw = "\n\n{0}" + "-" * 48 + "\n\nIntro\n\n{1}" + "-" * 48 + "\n\nMore\n"
    assert pages.from_marker_output(raw) == "<!-- page 1 -->\n\nIntro\n\n<!-- page 2 -->\n\nMore\n"


def test_write_markdown_refuses_lost_markers(tmp_path, capsys):
    p = tmp_path / "k.md"
    p.write_text(join([(1, "a"), (2, "b")]), encoding="utf-8")
    assert not pages.write_markdown(p, "<!-- page 1 -->\n\na\n")
    assert "lost pages [2]" in capsys.readouterr().err
    assert numbers(p.read_text(encoding="utf-8")) == [1, 2]          # unchanged
    assert pages.write_markdown(p, join([(1, "A"), (2, "B")]))


def test_restore_dropped():
    old = join([(1, "a"), (2, "b"), (3, "c")])
    assert numbers(pages.restore_dropped(old, join([(1, "a"), (3, "c")]))) == [1, 2, 3]
    assert numbers(pages.restore_dropped(old, join([(1, "a")]))) == [1, 2, 3]
    swapped = join([(3, "c"), (1, "a")])
    assert pages.restore_dropped(old, swapped) == swapped              # not a subsequence


def test_is_empty_and_text_size(tmp_path):
    only = tmp_path / "scan.md"
    only.write_text("<!-- page 1 -->\n\n<!-- page 2 -->\n", encoding="utf-8")
    text = tmp_path / "text.md"
    text.write_text(join([(1, "Words.")]), encoding="utf-8")
    assert pages.is_empty(only) and pages.is_empty(tmp_path / "missing.md")
    assert not pages.is_empty(text)
    assert pages.text_size(only) == 0 and pages.text_size(text) == len("Words.\n")


def test_in_marker():
    text = "x 12\n<!-- page 13 -->\n14"
    inside = pages.in_marker(text)
    assert not inside(text.index("12")) and inside(text.index("13")) and not inside(text.index("14"))


# Rewriters run on a marked paper: whatever they change, the markers stay.

def test_strip_frontmatter_keeps_markers_of_removed_pages():
    import strip_frontmatter
    text = sample()
    out = strip_frontmatter.strip_file(text)[0]
    assert "Biodiversity Heritage Library" not in out and "intentionally left blank" not in out
    assert numbers(out) == numbers(text)


def test_strip_running_heads_keeps_markers(tmp_path):
    import strip_running_heads
    p = tmp_path / "k.md"
    p.write_text(sample(), encoding="utf-8")
    args = argparse.Namespace(max_len=90, min_words=3, min_repeats=3, heading_regex=[],
                              drop_number_headings=False, exclude=[], apply=True)
    n_cut, _, cut = strip_running_heads.process(p, args)
    assert set(cut) == {HEAD}
    assert numbers(p.read_text(encoding="utf-8")) == numbers(sample())


def test_despace_keeps_markers():
    import despace_ocr
    out = despace_ocr.despace(sample())[0]
    assert "numerous" in out and numbers(out) == numbers(sample())


def test_gutter_number_strippers_ignore_markers():
    import strip_pdf_gutter_numbers
    import untable_gutter_manuscript
    text = sample()
    assert untable_gutter_manuscript.strip_inline_gutter(text)[0] == text
    marked = pages.in_marker(text)
    assert not any(marked(start) for start, _, _ in strip_pdf_gutter_numbers.md_tokens(text))


def test_translation_never_sends_markers():
    import translate_markdown as tm
    text = sample()
    chunks, plan = tm.chunk_plan(text, 120)
    assert not any("<!-- page" in c for c in chunks)
    fake = {i: c.upper() for i, c in enumerate(chunks)}               # a stand-in "translation"
    assert numbers(tm.assemble(plan, fake)) == numbers(text)
    plain = "No markers here.\n\nSecond paragraph."
    assert tm.chunk_plan(plain, 120)[0] == tm.chunk_markdown(plain, 120)


def test_reader_html_has_page_anchors():
    pytest.importorskip("nh3")
    from corpus.web import render
    html = render.to_html(join([(4, "Text on *page* four."), (5, "And five.")]))
    assert '<div data-page="4"></div>' in html and '<div data-page="5"></div>' in html
    assert "<!--" not in html


def test_index_strips_markers_and_skips_marker_only_files(tmp_path):
    md = tmp_path / "markdown"
    md.mkdir()
    (tmp_path / "manifest.json").write_text(json.dumps({"a2000a": {}, "scan2000a": {}}))
    (md / "a2000a.md").write_text(join([(1, "Femur."), (2, "Tibia.")]), encoding="utf-8")
    (md / "scan2000a.md").write_text("<!-- page 1 -->\n\n<!-- page 2 -->\n", encoding="utf-8")
    subprocess.run([sys.executable, str(REPO / "scripts" / "indexer.py")], check=True,
                   capture_output=True, env={**os.environ, "CORPUS_ROOT": str(tmp_path)})
    con = sqlite3.connect(tmp_path / "index" / "papers.db")
    rows = dict(con.execute("SELECT citation_key, body FROM papers"))
    assert set(rows) == {"a2000a"} and "page" not in rows["a2000a"]
    assert con.execute("SELECT count(*) FROM papers WHERE papers MATCH 'page'").fetchone()[0] == 0
