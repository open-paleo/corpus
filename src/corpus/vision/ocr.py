"""Vision OCR of one page image: the prompts, the request, the cached result.

Shared by convert_with_vision.py, ocr_spread_pdf.py, tile_ocr_poster.py and
reocr_nooverlap.py (ocr_fragment: a piece of a page, for content-filter
recovery).
"""
from __future__ import annotations

import base64
from pathlib import Path

from corpus.models import request_extras
from corpus.models import SONNET
from corpus.vision import cache

DEFAULT_MODEL = SONNET
DEFAULT_DPI = 150
DEFAULT_CONCURRENCY = 3

PROMPT_VERBATIM = """You are an OCR engine. Extract the text on this page into clean Markdown, verbatim.

Rules:
- Preserve italic formatting for genus and species names (e.g. *Tyrannosaurus rex*).
- Preserve heading structure (use # / ## as appropriate).
- Preserve table structure where present (Markdown tables; if too complex, use HTML).
- Preserve in-text citations and bibliographic references exactly.
- Skip page headers, page footers, and page numbers.
- For figures: emit `[FIGURE: <one-line description of what the figure shows>]` followed by the figure caption verbatim.
- Do NOT invent text. If part of the page is illegible, write `[ILLEGIBLE]`.
- If the page is blank or contains only a figure with no text, output a single line with the figure marker only.
- Output the markdown body only — no preamble, no explanation."""


PROMPT_TRANSLATE = """You are an OCR-and-translate engine. Extract the text on this page into clean English Markdown.

Rules:
- For text in Chinese, Japanese, or Korean: TRANSLATE into accurate, natural English. Preserve paleontological terminology.
- For text in English (e.g., abstracts, captions, references): keep verbatim.
- Latin taxon names stay in Latin (e.g. *Tyrannosaurus rex*) — italicize them.
- Preserve heading structure (use # / ## as appropriate).
- Preserve table structure where present (Markdown tables; if too complex, use HTML).
- Preserve in-text citations and bibliographic references exactly (do not translate citation keys, author names, or journal titles).
- Skip page headers, page footers, and page numbers.
- For figures: emit `[FIGURE: <one-line description>]` followed by the caption (translated if non-English).
- Do NOT invent text. If part of the page is illegible, write `[ILLEGIBLE]`.
- Output the markdown body only — no preamble, no explanation."""


def select_prompt(manifest: dict, key: str, extra_context: str = "") -> tuple[str, str]:
    """Returns (prompt_text, prompt_id). prompt_id goes into the cache key.
    extra_context is appended to the prompt text but does NOT change the
    prompt_id, so previously-cached successful pages stay cache-hit while
    pages without cache (e.g. content-filter failures) get the augmented
    prompt on the next API call."""
    conv = manifest[key].get("conversion") or {}
    if conv.get("vision_ocr_needed") and "CJK" in (conv.get("vision_ocr_reason") or ""):
        text, pid = PROMPT_TRANSLATE, "translate-v1"
    else:
        text, pid = PROMPT_VERBATIM, "verbatim-v1"
    if extra_context:
        text = text + "\n\nAdditional context: " + extra_context
    return text, pid


# Output cap per page. A dense reference page can exceed 4096 tokens; the
# stop_reason check in ocr_one_page() turns any overrun into a visible failure.
MAX_OUTPUT_TOKENS = 16000


async def call_image(client, model: str, system_prompt: str, jpeg: bytes, *,
                     max_tokens: int = MAX_OUTPUT_TOKENS, user_text: str | None = None,
                     cache_system: bool = True):
    """One messages.create() with a JPEG and an optional instruction; returns the
    raw message. The system prompt is prompt-cached so a multi-page run pays
    for it once."""
    system = {"type": "text", "text": system_prompt}
    if cache_system:
        system["cache_control"] = {"type": "ephemeral"}
    content = [{"type": "image", "source": {"type": "base64", "media_type": "image/jpeg",
                                             "data": base64.standard_b64encode(jpeg).decode("ascii")}}]
    if user_text:
        content.append({"type": "text", "text": user_text})
    return await client.messages.create(model=model, max_tokens=max_tokens, system=[system],
                                        messages=[{"role": "user", "content": content}],
                                        **request_extras(model))


async def ocr_one_page(client, model: str, prompt_text: str, prompt_id: str,
                       page_index: int, jpeg: bytes,
                       cache_dir: Path, *, force: bool = False) -> dict:
    """Returns {markdown, input_tokens, output_tokens, cache_read, cache_write,
              cached_hit (bool)}. Caches the response on disk."""
    cached = cache.read(cache_dir, page_index, model=model, prompt_id=prompt_id, force=force)
    if cached:
        return cached

    msg = await call_image(client, model, prompt_text, jpeg, max_tokens=MAX_OUTPUT_TOKENS)

    text_parts = [b.text for b in msg.content if b.type == "text"]
    markdown = "".join(text_parts).strip()
    usage = msg.usage
    # A page that hits the output cap is TRUNCATED, and nothing downstream
    # notices: the run reports ok, bytes-per-page looks normal, and the end of
    # the page is simply missing. Refuse to cache it, so the page surfaces as
    # a FAILED marker instead of shipping short.
    if msg.stop_reason == "max_tokens":
        raise RuntimeError(
            f"output truncated at max_tokens={MAX_OUTPUT_TOKENS} "
            f"({usage.output_tokens} tokens); page not cached")
    result = {
        "prompt_id": prompt_id,
        "model": model,
        "markdown": markdown,
        "input_tokens": usage.input_tokens,
        "output_tokens": usage.output_tokens,
        "cache_read": getattr(usage, "cache_read_input_tokens", 0) or 0,
        "cache_write": getattr(usage, "cache_creation_input_tokens", 0) or 0,
        "cached_hit": False,
    }
    cache.write(cache_dir, page_index, result)
    return result


# Same rules as PROMPT_VERBATIM, applied to a fragment: telling the model the
# input is a partial page reduces hallucinated continuations at the cut line.
PROMPT_FRAGMENT = """You are an OCR engine. Extract the text on this page fragment into clean Markdown, verbatim.

This is a *fragment* of a larger page (the page was split geometrically to work around output blocks). Transcribe only what you see; do not invent text past the edge.

Rules:
- Preserve italic formatting for genus and species names (e.g. *Tyrannosaurus rex*).
- Preserve heading structure (use # / ## as appropriate).
- Preserve table structure where present.
- Preserve in-text citations and bibliographic references exactly.
- Skip page headers, page footers, and page numbers.
- For figures: emit `[FIGURE: <one-line description>]` followed by the caption verbatim.
- Do NOT invent text. If part of the fragment is illegible or cut off, stop there — do not guess what comes next.
- Output the markdown body only — no preamble, no explanation."""


async def ocr_fragment(client, jpeg: bytes, *, model: str = DEFAULT_MODEL,
                       prompt: str = PROMPT_FRAGMENT) -> str | None:
    """The markdown of one image (a page or a piece of one), or None when the
    content filter blocks it. Not cached (the caller decides what to keep).
    Output cut at the token cap raises, as in ocr_one_page()."""
    from anthropic import APIStatusError
    try:
        msg = await call_image(client, model, prompt, jpeg)
    except APIStatusError as e:
        body = (e.response.text or "") if e.response is not None else ""
        if e.status_code == 400 and "content filtering" in body:
            return None
        raise
    if msg.stop_reason == "max_tokens":
        raise RuntimeError(f"output truncated at max_tokens={MAX_OUTPUT_TOKENS}")
    return "".join(b.text for b in msg.content if b.type == "text").strip()
