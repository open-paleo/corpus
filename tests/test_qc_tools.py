"""The identification and audit tools that complete public repairs and checks:
qc control-glyphs, qc symbol-glyphs, clean spillover and qc translations.

Scripts that write run in a subprocess with CORPUS_ROOT pointing at a temporary
corpus, since corpus.config fixes its paths at import.
"""
import json
import os
import subprocess
import sys
from pathlib import Path

from corpus.pages import join, numbers

REPO = Path(__file__).resolve().parents[1]


def run(script, root, *args, check=True):
    env = {**os.environ, "CORPUS_ROOT": str(root)}
    return subprocess.run([sys.executable, str(REPO / "scripts" / script), *args], env=env,
                          capture_output=True, text=True, check=check)


def test_control_glyph_readings_expand_to_a_map_per_paper(tmp_path):
    out = tmp_path / ".runtime" / "control_glyphs"
    out.mkdir(parents=True)
    (tmp_path / "manifest.json").write_text("{}")
    # census: (font, decimal code) -> [[key, page, bbox]], as --scan writes it
    (out / "census.json").write_text(json.dumps({
        "AdvP4C4E74\t1": [["a2001a", 0, [0, 0, 1, 1]], ["b2002a", 3, [0, 0, 1, 1]]],
        "AdvP4C4E74\t3": [["a2001a", 1, [0, 0, 1, 1]]],
        "OtherFont\t2": [["c2003a", 0, [0, 0, 1, 1]]],
    }))
    readings = tmp_path / "readings.json"
    readings.write_text(json.dumps({"AdvP4C4E74": {"01": "U+2212", "03": "~"}}))
    run("identify_control_glyphs.py", tmp_path, "--expand", str(readings))
    mapping = json.loads((out / "control_glyph_map.json").read_text(encoding="utf-8"))
    # the shape repair control_glyphs --map-file reads: {key: {hex code: glyph}}
    assert mapping == {"a2001a": {"01": "−", "03": "~"}, "b2002a": {"01": "−"}}


def test_symbol_glyph_candidates_find_coordinate_runs():
    import identify_symbol_substitutions as tool
    for text in ("(79(27#E, locality", "at 25 28' 15\" N", "(N49u04957.50/ W"):
        assert tool.COORD.search(text), text
    assert not tool.COORD.search("in 1998 the team")


def write_paper(root, key, text, title, authors):
    (root / "markdown").mkdir(parents=True, exist_ok=True)
    (root / "markdown" / f"{key}.md").write_text(text, encoding="utf-8")
    (root / "manifest.json").write_text(json.dumps(
        {key: {"citation": {"title": title, "author": authors}}}), encoding="utf-8")


def test_spillover_trim_keeps_page_markers(tmp_path):
    title = "A new troodontid dinosaur from China"
    text = join([
        (1, "the end of the previous paper about chameleons.\n\n"
            "Correspondence and requests for materials should be addressed to J.S."),
        (2, f"# {title}\n\nXu Xing, Mark A. Norell\n\nThe body of the paper."),
        (3, "More of the paper.\n\nCorrespondence and requests for materials should be "
            "addressed to X.X. (e-mail: xu@example.org)."),
        (4, "# Seismic anisotropy of the inner core\n\nA neighbouring article."),
    ])
    write_paper(tmp_path, "xu2002a", text, title, "Xu, X.; Norell, M. A.")
    result = run("trim_reprint_spillover.py", tmp_path, "--key", "xu2002a", "--apply")
    assert "trimmed: 1" in result.stdout, result.stdout + result.stderr
    new = (tmp_path / "markdown" / "xu2002a.md").read_text(encoding="utf-8")
    assert "chameleons" not in new and "Seismic anisotropy" not in new
    assert numbers(new) == [1, 2, 3, 4]
    # the paper's first page keeps its marker right above the title
    assert f"<!-- page 2 -->\n\n# {title}" in new


def test_spillover_trim_refuses_a_correspondence_to_someone_else(tmp_path):
    title = "A new troodontid dinosaur from China"
    text = (f"# {title}\n\nBody.\n\nCorrespondence and requests for materials should be "
            "addressed to Q.Z.\n\n# Another paper\n\nText.\n")
    write_paper(tmp_path, "xu2002a", text, title, "Xu, X.; Norell, M. A.")
    result = run("trim_reprint_spillover.py", tmp_path, "--key", "xu2002a", "--apply")
    assert "refused" in result.stdout and "no author matches" in result.stdout
    assert (tmp_path / "markdown" / "xu2002a.md").read_text(encoding="utf-8") == text


def test_translation_audit_flags_a_short_chunk(tmp_path):
    from corpus.translate import chunk_cache_key, chunk_markdown
    md = tmp_path / "markdown"
    md.mkdir()
    # four paragraphs of about 15,000 characters: one translation chunk each
    paragraphs = [f"Párrafo {i}. " + "texto del artículo " * 790 for i in range(4)]
    original = "\n\n".join(paragraphs) + "\n"
    chunks = chunk_markdown(original)
    assert len(chunks) == 4
    translated = [chunk.replace("texto del artículo", "text of the article") for chunk in chunks]
    translated[2] = translated[2][:len(translated[2]) // 3]      # a chunk that lost two thirds
    (md / "ab2000a.original.md").write_text(original, encoding="utf-8")
    (md / "ab2000a.md").write_text("\n\n".join(translated) + "\n", encoding="utf-8")
    cache = tmp_path / ".runtime" / "cache" / "translation" / "ab2000a"
    cache.mkdir(parents=True)
    for i, (chunk, text) in enumerate(zip(chunks, translated)):
        (cache / f"chunk-{i:04d}.json").write_text(json.dumps(
            {"hash": chunk_cache_key(chunk), "translated": text, "stop_reason": "end_turn"}))
    (tmp_path / "manifest.json").write_text(json.dumps(
        {"ab2000a": {"conversion": {"translated_from": "es"}}}))
    result = run("audit_translation_completeness.py", tmp_path, check=False)
    assert result.returncode == 1
    assert "short-chunk(chunk-0002.json" in result.stdout
    assert "chunk-0000" not in result.stdout and "chunk-0001" not in result.stdout
