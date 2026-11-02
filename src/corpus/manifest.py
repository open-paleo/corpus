"""manifest.json: load it, change it safely, and the fields an entry has.

The manifest is one JSON object keyed by citation key. Several scripts may
run at once (marker in the background while a fetcher or an intake step
writes). A script that read the file at start and wrote the whole dict at the
end would silently undo changes another script made in between.
`update()` prevents that: it takes an exclusive lock on manifest.json.lock,
re-reads the file under it, applies the caller's change, and writes
atomically. Scripts keep their start-of-run copy for reading and call
`update()` for each change.

    from corpus import manifest
    m = manifest.load()
    ...
    manifest.update(lambda m: m[key]["conversion"].update(tool="marker"))
"""
from __future__ import annotations

import copy
import fcntl
import json
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from corpus.config import MANIFEST
from corpus.io import atomic_write_json


# What each loaded manifest looked like when it was read (or last committed),
# by id() of the dict load() returned: commit() writes only the difference.
_snapshots: dict[int, dict] = {}


def load(path: Path = MANIFEST, *, track: bool = True) -> dict:
    """The manifest as a dict ({} if the file does not exist yet).

    track=True (default) remembers what was read so commit() can write only
    the changes; read-only callers pass track=False and skip that copy."""
    try:
        text = Path(path).read_text(encoding="utf-8")
    except FileNotFoundError:
        text = "{}"
    m = json.loads(text)
    if track:
        _snapshots[id(m)] = json.loads(text)    # cheaper than a deepcopy of m
    return m


_MISSING = object()


def _changes(snap: dict, cur: dict) -> tuple[dict, dict, list]:
    """(field changes per kept key, whole new entries, removed keys)."""
    fields, added, removed = {}, {}, [k for k in snap if k not in cur]
    for k, entry in cur.items():
        old = snap.get(k, _MISSING)
        if old is _MISSING:
            added[k] = entry
        elif old != entry:
            if not isinstance(entry, dict) or not isinstance(old, dict):
                added[k] = entry
                continue
            fields[k] = {f: entry.get(f, _MISSING) for f in set(old) | set(entry)
                         if old.get(f, _MISSING) != entry.get(f, _MISSING)}
    return fields, added, removed


def commit(m: dict, path: Path = MANIFEST) -> int:
    """Write the changes made to `m` since it was loaded (or last committed).

    Under the lock, re-reads the file and applies only what this script
    changed, field by field within each entry (`acquisition`, `conversion`,
    `annotations`, ...). Another script's changes to other entries, or to
    other fields of the same entry, survive. Returns the number of entries
    touched. A dict that did not come from load() is written whole, with a
    warning: it can undo other scripts' changes."""
    snap = _snapshots.get(id(m))
    if snap is None:
        import sys
        print("manifest.commit: dict not from manifest.load(); writing it whole",
              file=sys.stderr)
        save(m, path)
        return len(m)
    fields, added, removed = _changes(snap, m)
    if not (fields or added or removed):
        return 0
    with locked(path):
        fresh = json.loads(Path(path).read_text(encoding="utf-8")) if Path(path).exists() else {}
        for k, entry in added.items():
            fresh[k] = copy.deepcopy(entry)
        for k in removed:
            fresh.pop(k, None)
        for k, changed in fields.items():
            target = fresh.setdefault(k, {})
            for f, v in changed.items():
                if v is _MISSING:
                    target.pop(f, None)
                else:
                    target[f] = copy.deepcopy(v)
        atomic_write_json(path, fresh)
    _snapshots[id(m)] = copy.deepcopy(m)
    return len(fields) + len(added) + len(removed)


@contextmanager
def locked(path: Path = MANIFEST):
    """Hold the manifest's write lock (an flock on manifest.json.lock)."""
    lock = Path(path).with_suffix(Path(path).suffix + ".lock")
    with open(lock, "w") as fh:
        fcntl.flock(fh, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(fh, fcntl.LOCK_UN)


def update(mutate: Callable[[dict], object], path: Path = MANIFEST) -> dict:
    """Apply `mutate(manifest)` to a fresh read under the lock, then write.

    Returns the manifest as written. `mutate` should touch only the entries
    the calling script owns; everything else is whatever is on disk now."""
    with locked(path):
        m = load(path)
        mutate(m)
        atomic_write_json(path, m)
        return m


def save(m: dict, path: Path = MANIFEST) -> None:
    """Write a whole manifest under the lock. Only for scripts that rebuild
    the file wholesale (bootstrap, refresh); everything else uses update()."""
    with locked(path):
        atomic_write_json(path, m)


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


_SKELETON = {
    "citation": None,
    "in_bib": False,
    "genera": [],
    "clades": [],
    "doi": None,
    "year": None,
    "acquisition": {"source": None, "url": None, "pdf_path": None, "fetched_at": None},
    "conversion": {"tool": None, "markdown_path": None, "page_count": None,
                   "converted_at": None, "notes": ""},
    "index": {"indexed_at": None, "word_count": None},
}


def entry_skeleton(**fields) -> dict:
    """A new manifest entry with every block present; `fields` override."""
    e = copy.deepcopy(_SKELETON)
    e.update(fields)
    return e


def acquisition(entry: dict) -> dict:
    """entry["acquisition"], never None (stub entries may hold {} or nothing)."""
    return entry.get("acquisition") or {}


# Outcome suffixes the fetchers write into acquisition.source on failure
# (unpaywall_no_oa, unpaywall_error, oa_direct_no_meta_tag,
# oa_direct_landing_failed, ...), so that a re-run skips the key.
FAILED_SUFFIXES = ("_no_match", "_no_oa", "_failed", "_error", "_not_found",
                   "_html_landing", "_no_meta_tag", "_malformed", "_no_pdf_link")


def failed_source(source: str | None) -> bool:
    """True when acquisition.source records a failed fetch rather than a PDF."""
    return bool(source) and source.endswith(FAILED_SUFFIXES)


def conversion(entry: dict) -> dict:
    """entry["conversion"], never None."""
    return entry.get("conversion") or {}
