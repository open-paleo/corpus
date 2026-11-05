"""The text repairs for defects a PDF's text layer or OCR carries into the
markdown: each one fixes its defect and leaves the page markers as they were."""
import json
import os
import subprocess
import sys
from pathlib import Path

from corpus.pages import join, numbers

REPO = Path(__file__).resolve().parents[1]


def run(script, root, *args):
    env = {**os.environ, "CORPUS_ROOT": str(root)}
    return subprocess.run([sys.executable, str(REPO / "scripts" / script), *args], env=env,
                          capture_output=True, text=True, check=False)


def test_ligature_codes():
    import clean_ligature_codes as tool
    text = join([(1, "A signi®cant change in the ¯ow of the river."),
                 (2, "The ®rst ®eld season; north±south; dated 150.8 ± 4.0 Ma.")])
    assert tool.classify(text)[0] == "faulty"
    new, counts, kept = tool.repair(text)
    assert "significant" in new and "flow" in new and "first field" in new
    assert "150.8 ± 4.0 Ma" in new and kept == 1             # a real plus-minus stays
    assert numbers(new) == [1, 2]


def test_ligature_codes_leave_a_macron_alone():
    import clean_ligature_codes as tool
    # one spacing macron for a long vowel is not a font fault
    assert tool.classify("The Japanese term Ky¯ory¯ugaku.")[0] == "no-signature"


def test_diacritics_fold_after_their_letter():
    import clean_diacritics as tool
    text = join([(1, "Osmo´lska and Apesteguı´a described it."), (2, "Rı´o Negro, Sa~o Paulo.")])
    new, _ = tool.fold(text)
    assert "Osmólska" in new and "Apesteguía" in new and "Río" in new and "São" in new
    assert numbers(new) == [1, 2]


def test_gesperrt_rejoins_letterspaced_labels(tmp_path):
    import clean_gesperrt as tool
    path = tmp_path / "k.md"
    path.write_text(join([(1, "H o l o t y p e : a femur."), (2, "T y p e  l o c a l i t y : Kent.")]),
                    encoding="utf-8")
    vocab = {"holotype", "type", "locality"}
    done, skipped, _ = tool.process(path, vocab, apply=True)
    new = path.read_text(encoding="utf-8")
    assert "Holotype : a femur" in new and "Type locality" in new
    assert numbers(new) == [1, 2]


def test_glued_words_split_on_attested_halves():
    import clean_glued_words as tool
    df = {"kaiparowits": 40, "formation": 900, "the": 3000, "aguja": 30}
    text = join([(1, "The KaiparowitsFormation and theAguja.")]) + "TheTheTheThe " * 3
    new, n = tool.fix(text, df, {}, force=True)
    assert "Kaiparowits Formation" in new and "the Aguja" in new
    assert numbers(new) == [1]


def test_transposed_accents_take_the_letter_that_can_carry_them():
    import clean_transposed_accents as tool
    text = join([(1, "Neuque´n, the Mus´eum, and Man˜ero.")])
    new, n = tool.fix(text, {"neuquén": 9, "muséum": 9, "mañero": 9})
    assert "Neuquén" in new and "Muséum" in new and "Mañero" in new
    assert numbers(new) == [1]


def test_coordinate_repairs_come_from_the_repairs_file(tmp_path):
    md = tmp_path / "markdown"
    md.mkdir()
    (tmp_path / "manifest.json").write_text("{}")
    (md / "a2001a.md").write_text(join([(1, "At (79(27#E), near the river.")]), encoding="utf-8")
    (md / "b2002a.md").write_text("Two sites: 12(30#N and 12(30#N.\n", encoding="utf-8")
    (tmp_path / "repairs").mkdir()
    (tmp_path / "repairs" / "coordinate_glyphs.json").write_text(json.dumps({
        "a2001a": [["(79(27#E)", "(79°27′E)"]],
        "b2002a": [["12(30#N", "12°30′N"]],                    # matches twice: skipped
    }, ensure_ascii=False), encoding="utf-8")
    result = run("clean_coordinates.py", tmp_path, "--apply")
    a = (md / "a2001a.md").read_text(encoding="utf-8")
    assert "(79°27′E)" in a and numbers(a) == [1]
    assert "matches 2 times, skipped" in result.stdout
    assert "12(30#N" in (md / "b2002a.md").read_text(encoding="utf-8")


def test_coordinate_repairs_without_a_file(tmp_path):
    (tmp_path / "markdown").mkdir()
    (tmp_path / "manifest.json").write_text("{}")
    result = run("clean_coordinates.py", tmp_path)
    assert result.returncode == 0 and "no recorded repairs" in result.stdout
