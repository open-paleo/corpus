"""corpus.keys, corpus.io, corpus.manifest, corpus.pdf.pages, corpus.md, corpus.models."""
import argparse
import json
import multiprocessing
import time
import unicodedata

import pytest

from corpus import io, keys, manifest
from corpus.md import compose, normalize, plain
from corpus.models import PRICES, SONNET, cost
from corpus.pdf.pages import compact, parse


# ---- keys ---------------------------------------------------------------

def test_nfc_and_key_from_path(tmp_path):
    nfd = unicodedata.normalize("NFD", "agnolín2010a")
    assert nfd != "agnolín2010a"
    assert keys.nfc(nfd) == "agnolín2010a"
    assert keys.key_from_path(tmp_path / f"{nfd}.pdf") == "agnolín2010a"


@pytest.mark.parametrize("bad", ["", ".", "..", "../x", "a/b", "a\\b", ".hidden", "a\x00b"])
def test_validate_rejects_unsafe(bad):
    with pytest.raises(ValueError):
        keys.validate(bad)


@pytest.mark.parametrize("good", ["o'connor2005a", "prieto-márquez2010c", "lü2004", "x2020a-supplemental"])
def test_validate_accepts_real_keys(good):
    assert keys.validate(good) == unicodedata.normalize("NFC", good)


def test_read_keys_file_comments_and_nfd(tmp_path):
    f = tmp_path / "keys.txt"
    nfd = unicodedata.normalize("NFD", "hüsing2011a")
    f.write_text(f"a2020\n   # indented note\n\n{nfd}  # trailing\n#b2021\n", encoding="utf-8")
    assert keys.read_keys_file(f) == ["a2020", "hüsing2011a"]


def test_key_arg_in_argparse():
    ap = argparse.ArgumentParser()
    keys.add_key_args(ap)
    args = ap.parse_args(["--key", unicodedata.normalize("NFD", "lü2004")])
    assert keys.resolve_keys(args) == ["lü2004"]
    with pytest.raises(SystemExit):
        ap.parse_args(["--key", "../etc"])


# ---- io -----------------------------------------------------------------

def test_atomic_write_json_matches_manifest_format(tmp_path):
    p = tmp_path / "m.json"
    data = {"b": 1, "a": {"é": None}}
    io.atomic_write_json(p, data)
    assert p.read_text(encoding="utf-8") == json.dumps(data, indent=2, sort_keys=True, default=str)
    assert not (tmp_path / "m.json.tmp").exists()


def test_read_json_tolerates_truncation(tmp_path):
    p = tmp_path / "x.json"
    p.write_text('{"a": 1', encoding="utf-8")
    assert io.read_json(p) is None
    assert io.read_json(tmp_path / "missing.json", default={}) == {}


# ---- manifest -----------------------------------------------------------

def test_commit_merges_fields_from_two_writers(manifest_file):
    m1, m2 = manifest.load(manifest_file), manifest.load(manifest_file)
    m1["a"]["conversion"] = {"tool": "marker"}
    m2["a"]["acquisition"] = {"source": "manual"}
    m2["d"] = {"new": True}
    del m2["c"]
    manifest.commit(m1, manifest_file)
    manifest.commit(m2, manifest_file)
    on_disk = json.loads(manifest_file.read_text(encoding="utf-8"))
    assert on_disk["a"] == {"acquisition": {"source": "manual"}, "conversion": {"tool": "marker"}}
    assert "c" not in on_disk and on_disk["d"] == {"new": True}
    # a stale copy that never touched "c" does not bring it back
    m1["b"]["x"] = 5
    manifest.commit(m1, manifest_file)
    on_disk = json.loads(manifest_file.read_text(encoding="utf-8"))
    assert "c" not in on_disk and on_disk["b"]["x"] == 5


def test_commit_noop_does_not_write(manifest_file):
    m = manifest.load(manifest_file)
    before = manifest_file.stat().st_mtime_ns
    assert manifest.commit(m, manifest_file) == 0
    assert manifest_file.stat().st_mtime_ns == before


def test_update_rereads_under_lock(manifest_file):
    stale = manifest.load(manifest_file)
    manifest.update(lambda m: m["b"].update(x=2), manifest_file)
    stale["c"]["y"] = 3
    manifest.commit(stale, manifest_file)
    on_disk = json.loads(manifest_file.read_text(encoding="utf-8"))
    assert on_disk["b"]["x"] == 2 and on_disk["c"]["y"] == 3


def _hold_lock(path, seconds):
    with manifest.locked(path):
        time.sleep(seconds)


def test_lock_blocks_another_process(manifest_file):
    p = multiprocessing.get_context("fork").Process(target=_hold_lock, args=(manifest_file, 0.6))
    p.start()
    time.sleep(0.15)
    t = time.time()
    manifest.update(lambda m: m.update(z={}), manifest_file)
    waited = time.time() - t
    p.join()
    assert waited > 0.3


def test_entry_skeleton_is_fresh_each_time():
    a, b = manifest.entry_skeleton(), manifest.entry_skeleton(in_bib=True)
    a["acquisition"]["source"] = "x"
    assert b["acquisition"]["source"] is None and b["in_bib"] is True
    assert set(a) >= {"citation", "acquisition", "conversion", "index"}


# ---- page specs ---------------------------------------------------------

@pytest.mark.parametrize("spec,expected", [
    ("1-3, 7", [1, 2, 3, 7]), ("5-9", [5, 6, 7, 8, 9]), ("9-7", [9, 8, 7]),
    ("1 3,3 4", [1, 3, 4]), ("", []),
])
def test_parse_pages(spec, expected):
    assert parse(spec) == expected


@pytest.mark.parametrize("spec,kw", [("x", {}), ("0", {}), ("5", {"max_page": 4}),
                                     ("", {"allow_empty": False})])
def test_parse_pages_errors(spec, kw):
    with pytest.raises(ValueError):
        parse(spec, **kw)


def test_compact():
    assert compact([1, 3, 4, 5, 9]) == "1, 3-5, 9"
    assert compact([]) == ""


# ---- markdown views -----------------------------------------------------

def test_plain_maps_back_to_source():
    md = "See [Smith, 2001](#page-1) and *Ichthyosaurus*."
    flat, idx = plain(md)
    i = flat.index("Ichthyosaurus")
    assert md[idx[i]:idx[i] + 13] == "Ichthyosaurus"
    assert "](#page-1)" not in flat


def test_normalize_collapses_whitespace_with_map():
    flat, idx = normalize("a  \n b")
    assert flat == "a b"
    assert idx[-1] == 5


def test_compose():
    assert compose("e", "́") == "é"
    assert compose("q", "̈") is None


# ---- models -------------------------------------------------------------

def test_cost_uses_the_model_price():
    haiku = cost("claude-haiku-5-5", 1_000_000, 1_000_000)
    sonnet = cost(SONNET, 1_000_000, 1_000_000)
    assert sonnet == PRICES[SONNET]["in"] + PRICES[SONNET]["out"]
    assert haiku < sonnet / 10
    assert cost("unknown-model", 1_000_000) == PRICES[SONNET]["in"]


def test_atomic_path_replaces_on_success_and_cleans_up_on_error(tmp_path):
    from corpus.io import atomic_path
    target = tmp_path / "x.pdf"
    target.write_bytes(b"old")
    with atomic_path(target) as tmp:
        tmp.write_bytes(b"new")
    assert target.read_bytes() == b"new" and not tmp.exists()
    try:
        with atomic_path(target) as tmp:
            tmp.write_bytes(b"half")
            raise RuntimeError
    except RuntimeError:
        pass
    assert target.read_bytes() == b"new" and not tmp.exists()
