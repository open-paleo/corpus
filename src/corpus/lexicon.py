"""The corpus's own vocabulary, for repairs that judge a candidate word by use.

Several text repairs choose between readings ("diferent" or "different", "the
dinosaur" or "thedinosaur") by asking which word the corpus uses. The answer is
a document frequency: in how many files a word occurs. It is counted only over
files that do not show the damage being repaired, so a defect common in broken
files cannot vouch for itself. Each repair supplies its own test for damage and
its own idea of a word, and gets its own cache, rebuilt when the set of
markdown files or their total size changes.
"""
from __future__ import annotations

import json
import re
import sys
from collections import Counter
from collections.abc import Callable
from pathlib import Path

from corpus.config import CACHE_ROOT
from corpus.io import atomic_write_text

WORD = re.compile(r"[^\W\d_]{2,}", re.UNICODE)


def corpus_signature(md_dir: Path) -> str:
    """Changes when a markdown file is added, removed or changes size."""
    files = sorted(md_dir.glob("*.md"))
    return f"{len(files)}:{sum(p.stat().st_size for p in files)}"


def document_frequencies(md_dir: Path, name: str, damaged: Callable[[str], object],
                         word: re.Pattern = WORD, rebuild: bool = False) -> dict[str, int]:
    """{lower-cased word: number of undamaged files it occurs in}, cached as `name`.

    `damaged(text)` is true for a file that shows the defect; those files are
    left out of the count.
    """
    cache = CACHE_ROOT / f"{name}.json"
    signature = corpus_signature(md_dir)
    if not rebuild and cache.exists():
        try:
            blob = json.loads(cache.read_text(encoding="utf-8"))
            if blob.get("signature") == signature:
                return blob["df"]
        except (ValueError, KeyError):
            pass
    df: Counter[str] = Counter()
    clean = skipped = 0
    for path in sorted(md_dir.glob("*.md")):
        text = path.read_text(encoding="utf-8", errors="replace")
        if damaged(text):
            skipped += 1
            continue
        clean += 1
        df.update({w.lower() for w in word.findall(text)})
    print(f"  lexicon: {clean} clean files, {skipped} damaged, {len(df)} distinct tokens",
          file=sys.stderr)
    cache.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_text(cache, json.dumps({"signature": signature, "df": dict(df)}))
    return dict(df)
