"""Per-page vision responses cached at .runtime/cache/vision/{key}/page-NNNN.json.

A cache entry records the prompt id and model that wrote it; it serves a
request only for the same prompt and a cache-compatible model
(corpus.models.cache_model_ok). Writes are atomic. An entry that cannot be
parsed (a write cut off part-way) is a miss and is removed, so it does not
fail its paper on every run.
"""
from __future__ import annotations

import json
from pathlib import Path

from corpus.config import VISION_CACHE
from corpus.io import atomic_write_json
from corpus.models import cache_model_ok


def key_dir(key: str) -> Path:
    return VISION_CACHE / key


def page_path(cache_dir: Path, page_index: int) -> Path:
    """page-NNNN.json for a 0-based page index (file names are 1-based)."""
    return Path(cache_dir) / f"page-{page_index + 1:04d}.json"


def read_entry(path: Path) -> dict | None:
    """The cached dict at path, or None if missing or unreadable (then removed)."""
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None
    except (json.JSONDecodeError, UnicodeDecodeError):
        Path(path).unlink(missing_ok=True)
        return None


def read(cache_dir: Path, page_index: int, *, model: str, prompt_id: str | None,
         force: bool = False) -> dict | None:
    """A cached response usable for (model, prompt_id), marked cached_hit; else None.
    prompt_id=None accepts any prompt. force=True always misses."""
    if force:
        return None
    entry = read_entry(page_path(cache_dir, page_index))
    if entry is None:
        return None
    if prompt_id is not None and entry.get("prompt_id") != prompt_id:
        return None
    if not cache_model_ok(entry.get("model"), model):
        return None
    entry["cached_hit"] = True
    return entry


def write(cache_dir: Path, page_index: int, result: dict) -> Path:
    path = page_path(cache_dir, page_index)
    path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_json(path, result, sort_keys=False)
    return path
