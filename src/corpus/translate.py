"""Chunking for translation: how a paper is cut into pieces for the model, and put back.

Shared by scripts/convert_translate.py, which translates, and
scripts/qc_translations.py, which re-chunks a source exactly as
the translator did to measure each cached translation against its own text.
Nothing here needs the translation dependencies.
"""
from __future__ import annotations

import hashlib

from corpus.config import RUNTIME
from corpus.pages import segments

CACHE_DIR = RUNTIME / "cache" / "translation"
MAX_CHARS_PER_CHUNK = 18000  # ~4.5k input tokens
# CJK sources tokenize densely and expand ~1.3x in characters on the way to
# English, so an 18k-char Korean/Chinese chunk can exceed the output cap and
# be cut short. They get a smaller chunk.
MAX_CHARS_PER_CHUNK_CJK = 9000
CJK_LANGS = {"zh", "zh-cn", "zh-tw", "ja", "ko"}


def chunk_markdown(text: str, max_chars: int = MAX_CHARS_PER_CHUNK) -> list[str]:
    """Split markdown into chunks at paragraph boundaries, never exceeding max_chars."""
    if len(text) <= max_chars:
        return [text]
    paras = text.split("\n\n")
    chunks = []
    cur = []
    cur_len = 0
    for p in paras:
        plen = len(p) + 2
        if cur and cur_len + plen > max_chars:
            chunks.append("\n\n".join(cur))
            cur = [p]
            cur_len = plen
        else:
            cur.append(p)
            cur_len += plen
    if cur:
        chunks.append("\n\n".join(cur))
    return chunks


def chunk_plan(body: str, max_chars: int) -> tuple[list[str], list[tuple[str, str | int]]]:
    """The chunks to translate, and the order to reassemble them with the page markers.

    Page markers never go to the model, which could drop or alter them: the
    body is cut at its markers, each piece chunked, and the plan lists
    ("marker", line) and ("chunk", index) in document order. Without markers
    the chunks are exactly chunk_markdown's, so existing chunk caches stay valid.
    """
    if segments(body) == [("", body.strip("\n"))]:
        chunks = chunk_markdown(body, max_chars)
        return chunks, [("chunk", i) for i in range(len(chunks))]
    chunks, plan = [], []
    for opener, seg in segments(body):
        if opener:
            plan.append(("marker", opener))
        for c in (chunk_markdown(seg, max_chars) if seg.strip() else []):
            plan.append(("chunk", len(chunks)))
            chunks.append(c)
    return chunks, plan


def assemble(plan: list[tuple[str, str | int]], pieces: dict[int, str]) -> str:
    return "\n\n".join(v if kind == "marker" else pieces[v] for kind, v in plan).strip() + "\n"


def chunk_cache_key(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]
