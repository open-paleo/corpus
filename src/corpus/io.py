"""File writes that survive Ctrl-C: write a sibling .tmp, then rename over.

The JSON format is manifest.json's (indent 2, sorted keys, ASCII-escaped,
no trailing newline), so rewriting unchanged data reproduces the file byte
for byte.
"""
from __future__ import annotations

import json
from contextlib import contextmanager
from pathlib import Path


def atomic_write_text(path: Path, text: str) -> None:
    path = Path(path)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    tmp.replace(path)


@contextmanager
def atomic_path(path: Path):
    """For a library that writes to a path itself (pikepdf `save`): yields a
    sibling .tmp path, renamed over `path` if the block completes and
    removed if it raises.

        with atomic_path(pdf_path) as tmp:
            pdf.save(str(tmp))
    """
    path = Path(path)
    tmp = path.with_suffix(path.suffix + ".tmp")
    try:
        yield tmp
        tmp.replace(path)
    finally:
        tmp.unlink(missing_ok=True)


def atomic_write_bytes(path: Path, data: bytes) -> None:
    path = Path(path)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_bytes(data)
    tmp.replace(path)


def atomic_write_json(path: Path, data, *, indent: int | None = 2, sort_keys: bool = True,
                      ensure_ascii: bool = True) -> None:
    atomic_write_text(path, json.dumps(data, indent=indent, sort_keys=sort_keys,
                                       ensure_ascii=ensure_ascii, default=str))


def read_json(path: Path, default=None):
    """Parsed JSON, or `default` if the file is missing or not valid JSON
    (a cache entry truncated by an interrupted write counts as missing)."""
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError, UnicodeDecodeError):
        return default


def jsonl_append(path: Path, record: dict) -> None:
    """Append one JSON line (a log record), creating the folder if needed."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False, default=str) + "\n")
