"""The open-paleo bib and the genus/clade records that cite it.

`parse_bib` reads references.bib into {key: {entry_type, field: value}};
`collect_yaml_keys` walks genera/ or clades/ and records which entity cites
which key; `citation_venue` and `is_thesis` read a parsed citation.

Where the bib comes from is CORPUS_BIB_SOURCE (`bib_path()`):
  sibling   {data}/dist/references.bib, {data} = OPEN_PALEO_DATA or ../data
  bundled   ROOT/bib/references.bib, a copy kept in this repository
  remote    CORPUS_BIB_URL (default: the data repo's main branch on GitHub),
            downloaded to .runtime/cache/bib/ and re-fetched on refresh
  auto      (default) the first of sibling, bundled that exists, else remote

Which genera and clades cite a key (`citing()`) is an annotation, not a
filter: every bib key is in the manifest. It is read from the data repo's
YAMLs when they are there, else from its dist/open-paleo.json (local, or
downloaded in remote/bundled mode); with neither, keys carry no labels.
"""
from __future__ import annotations

import json
import os
import re
import sys
from collections import defaultdict
from pathlib import Path

import yaml

from corpus.config import ROOT, cache_dir, data_repo

DATA_RAW = "https://raw.githubusercontent.com/open-paleo/data/main/dist/"
SOURCES = ("auto", "sibling", "bundled", "remote")
BUNDLED_BIB = ROOT / "bib" / "references.bib"


ENTRY_RE = re.compile(r"@(\w+)\{([^,]+),(.*?)\n\}", re.DOTALL)
FIELD_RE = re.compile(r"\s*(\w+)\s*=\s*\{(.*?)\}\s*,?\s*$", re.MULTILINE | re.DOTALL)


def parse_bib(path: Path) -> dict:
    """Return {citation_key: {entry_type, **fields}}."""
    text = path.read_text(encoding="utf-8")
    out = {}
    for m in ENTRY_RE.finditer(text):
        entry_type, key, body = m.group(1), m.group(2).strip(), m.group(3)
        fields = {}
        for fm in FIELD_RE.finditer(body):
            fname = fm.group(1).lower()
            fval = fm.group(2).strip()
            fval = re.sub(r"\s+", " ", fval)
            fields[fname] = fval
        fields["entry_type"] = entry_type
        out[key] = fields
    return out


_RANGE_RE = re.compile(r"(\d+)\s*(?:-{1,2}|–|—)\s*(\d+)\b")


def page_range(pages: str | None) -> tuple[int | None, int | None]:
    """(first, last) printed page of a citation's `pages` field: "187-201",
    "pp. 187–201", "501-505, pl. 3" -> (187, 201) ...; "169" or "p. 169" ->
    (169, 169); an abbreviated "501-5" -> (501, None). Anything else, a
    book's "250 pp." included, -> (None, None): it is not a page range."""
    if not pages or not isinstance(pages, str):
        return None, None
    s = re.sub(r"^p{1,2}\.?\s*", "", pages.strip(), flags=re.I)
    m = _RANGE_RE.match(s)
    if m:
        a, b = int(m.group(1)), int(m.group(2))
        return (a, b) if b >= a else (a, None)
    m = re.fullmatch(r"(\d+)\.?", s)
    if m:
        return int(m.group(1)), int(m.group(1))
    return None, None


THESIS_TYPES = {"phdthesis", "mastersthesis"}


def is_thesis(cit: dict) -> bool:
    """True for a thesis citation. The data repo exports doctoral, master's
    and habilitation theses as @phdthesis/@mastersthesis (habilitation adds
    `type`), and a dissertation abstract (Soviet avtoreferat) as @misc whose
    `howpublished` starts "Dissertation abstract"."""
    if (cit.get("entry_type") or "").lower() in THESIS_TYPES:
        return True
    return (cit.get("howpublished") or "").startswith("Dissertation abstract")


def citation_venue(cit: dict) -> str:
    """Where a work appeared, for display and search: `journal` for an
    article, `booktitle` for a chapter, `school` for a thesis, `howpublished`
    for a @misc, `publisher` for a standalone book. Since data-repo schema
    1.10.0 (2026-10-02) theses and books no longer carry a journal-ish field,
    so reading journal/booktitle alone leaves them blank."""
    for field in ("journal", "booktitle", "school", "howpublished", "publisher"):
        value = (cit.get(field) or "").strip()
        if value:
            return value
    return ""


def collect_yaml_keys(root: Path, kind: str):
    """
    Walk root/**/*.yml. For each citation key referenced, accumulate the
    entity (genus or clade name) that referenced it.

    Returns (refs, entities_without_refs):
      refs = {citation_key: {"genera": set[str], "clades": set[str]}}
      entities_without_refs = sorted list of entity names with zero citation keys
    """
    def records():
        for ypath in sorted(root.rglob("*.yml")):
            try:
                doc = yaml.safe_load(ypath.read_text(encoding="utf-8"))
            except Exception as e:
                print(f"  ! failed to parse {ypath}: {e}", file=sys.stderr)
                continue
            if isinstance(doc, dict):
                yield doc.get("genus") or doc.get("clade") or ypath.stem, doc, \
                    str(ypath.relative_to(root))
    return _collect(records(), kind)


def collect_json_keys(export: dict, kind: str):
    """collect_yaml_keys() over the data repo's dist/open-paleo.json, whose
    "genera"/"clades" maps hold the same records (checked equal 2026-10-31)."""
    return _collect(((doc.get("genus") or doc.get("clade") or name, doc, name)
                     for name, doc in (export.get(kind) or {}).items()
                     if isinstance(doc, dict)), kind)


def _collect(records, kind: str):
    refs = defaultdict(lambda: {"genera": set(), "clades": set()})
    entities_without_refs = []
    bucket = "genera" if kind == "genera" else "clades"
    for name, doc, where in records:
        keys_for_this_entity = set()

        for r in doc.get("references", []) or []:
            if isinstance(r, dict) and r.get("id"):
                refs[r["id"]][bucket].add(name)
                keys_for_this_entity.add(r["id"])

        for sp in doc.get("species", []) or []:
            if isinstance(sp, dict):
                di = sp.get("described_in")
                if isinstance(di, str):
                    refs[di][bucket].add(name)
                    keys_for_this_entity.add(di)
                elif isinstance(di, list):
                    for k in di:
                        refs[k][bucket].add(name)
                        keys_for_this_entity.add(k)

        if not keys_for_this_entity:
            entities_without_refs.append(f"{name} ({where})")
    return refs, entities_without_refs


def merge(a: dict, b: dict) -> dict:
    out = defaultdict(lambda: {"genera": set(), "clades": set()})
    for src in (a, b):
        for k, v in src.items():
            out[k]["genera"] |= v["genera"]
            out[k]["clades"] |= v["clades"]
    return out


# ---- where the bib and the citing records come from --------------------

def bib_source() -> str:
    """CORPUS_BIB_SOURCE, resolved: sibling, bundled or remote."""
    from corpus.config import load_env
    load_env()
    src = (os.environ.get("CORPUS_BIB_SOURCE") or "auto").strip().lower()
    if src not in SOURCES:
        raise SystemExit(f"CORPUS_BIB_SOURCE={src!r}: use one of {', '.join(SOURCES)}")
    if src != "auto":
        return src
    if (data_repo() / "dist" / "references.bib").exists():
        return "sibling"
    if BUNDLED_BIB.exists():
        return "bundled"
    return "remote"


def _download(url: str, dest: Path, refresh: bool, what: str) -> Path:
    """dest, (re)fetched from url when missing or refresh is set. A failed
    fetch falls back to the cached copy with a warning, or exits."""
    if dest.exists() and not refresh:
        return dest
    from corpus.fetch.http import get
    from corpus.io import atomic_write_bytes
    print(f"fetching {what} from {url} ...", file=sys.stderr)
    r = get(url, timeout=120)
    if r.status == 200 and r.body and not r.truncated:
        dest.parent.mkdir(parents=True, exist_ok=True)
        atomic_write_bytes(dest, r.body)
        return dest
    if dest.exists():
        print(f"  ! fetch failed ({r.error or r.status}); using the cached {dest}",
              file=sys.stderr)
        return dest
    raise SystemExit(f"could not fetch {what} from {url}: {r.error or r.status}")


def bib_path(*, refresh: bool = False) -> Path:
    """The references.bib to read, per CORPUS_BIB_SOURCE. In remote mode,
    refresh=True re-downloads it (the refresh scripts pass it)."""
    src = bib_source()
    if src == "sibling":
        path = data_repo() / "dist" / "references.bib"
        if not path.exists():
            raise SystemExit(f"CORPUS_BIB_SOURCE=sibling but {path} does not exist "
                             "(set OPEN_PALEO_DATA, or use bundled/remote)")
        return path
    if src == "bundled":
        if not BUNDLED_BIB.exists():
            raise SystemExit(f"CORPUS_BIB_SOURCE=bundled but {BUNDLED_BIB} does not exist")
        return BUNDLED_BIB
    url = os.environ.get("CORPUS_BIB_URL") or DATA_RAW + "references.bib"
    return _download(url, cache_dir("bib") / "references.bib", refresh, "the bib")


def citing(*, refresh: bool = False) -> tuple[dict, list[str], str]:
    """(refs, entities_without_refs, where they came from) for genera and
    clades together; refs is {key: {"genera": set, "clades": set}}."""
    repo = data_repo()
    if (repo / "genera").is_dir():
        g, gn = collect_yaml_keys(repo / "genera", "genera")
        c, cn = collect_yaml_keys(repo / "clades", "clades")
        return merge(g, c), gn + cn, f"YAMLs in {repo}"
    export = repo / "dist" / "open-paleo.json"
    if not export.exists():
        if bib_source() == "sibling":
            return {}, [], "nothing (no data repo YAMLs or dist/open-paleo.json)"
        url = os.environ.get("CORPUS_DATA_JSON_URL") or DATA_RAW + "open-paleo.json"
        try:
            export = _download(url, cache_dir("bib") / "open-paleo.json", refresh,
                               "genus and clade records")
        except SystemExit as e:
            print(f"  ! {e}; keys get no genus/clade labels", file=sys.stderr)
            return {}, [], "nothing (download failed)"
    data = json.loads(export.read_text(encoding="utf-8"))
    g, gn = collect_json_keys(data, "genera")
    c, cn = collect_json_keys(data, "clades")
    return merge(g, c), gn + cn, str(export)
