"""Tests run against temporary directories, never the archive's own data.

`scripts/` and `src/` are on sys.path (pyproject [tool.pytest.ini_options]),
so script modules import the way they do when run from the repo root.

corpus.config fixes every path at import, from CORPUS_ROOT. It is pointed at an
empty directory here, before any test module imports the package, so a test
that runs a script's main() in-process can never read or write the real
corpus: a test that forgot to stub a write once replaced manifest.json with its
one-entry fixture. Tests that need a populated corpus build one in tmp_path and
pass it on (CORPUS_ROOT for a subprocess, or monkeypatched corpus.config paths).
"""
import json
import os
import tempfile

import pytest

os.environ["CORPUS_ROOT"] = tempfile.mkdtemp(prefix="corpus-tests-")


@pytest.fixture
def manifest_file(tmp_path):
    """A small manifest.json in a temporary directory."""
    p = tmp_path / "manifest.json"
    p.write_text(json.dumps({
        "a": {"acquisition": {"source": None}, "conversion": {"tool": None}},
        "b": {"x": 1},
        "c": {"y": 2},
    }), encoding="utf-8")
    return p
