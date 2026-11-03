"""Markdown to safe HTML for the reader.

The markdown comes from PDF text layers and OCR, so it can hold anything,
raw HTML included (marker writes <sup> and <br>). It is rendered with
markdown2 and then cleaned with nh3 against an allowlist: no scripts, event
handlers, styles, images or non-http links survive.
"""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path

import markdown2
import nh3

from corpus.pages import MARKER

EXTRAS = ["tables", "fenced-code-blocks", "strike"]
TAGS = {"p", "br", "hr", "h1", "h2", "h3", "h4", "h5", "h6", "em", "strong", "b", "i",
        "u", "s", "del", "sup", "sub", "small", "code", "pre", "blockquote", "ul", "ol",
        "li", "table", "thead", "tbody", "tr", "th", "td", "a", "span", "div"}
ATTRIBUTES = {"a": {"href", "title"}, "div": {"data-page"}, "th": {"colspan", "rowspan"},
              "td": {"colspan", "rowspan"}, "ol": {"start"}}
CHUNK = 50_000


def chunks(text: str, size: int = CHUNK) -> list[str]:
    """Split at blank lines outside tables, about every `size` characters.

    markdown2's time grows faster than the text: a 5.8 MB paper takes about
    twice as long in one piece as in 50 KB pieces.
    """
    out, start = [], 0
    while len(text) - start > size:
        i = text.find("\n\n", start + size)
        while i != -1 and (text.startswith("|", i + 2)
                           or text[text.rfind("\n", 0, i) + 1:i].startswith("|")):
            i = text.find("\n\n", i + 2)
        if i == -1:
            break
        out.append(text[start:i])
        start = i + 2
    out.append(text[start:])
    return out


def to_html(text: str) -> str:
    # Page markers become empty <div data-page="N"> anchors, which the reader
    # uses to keep the text and the PDF on the same page.
    text = MARKER.sub(lambda m: f'<div data-page="{m.group(1)}"></div>', text)
    raw = "\n".join(markdown2.markdown(c, extras=EXTRAS) for c in chunks(text))
    return nh3.clean(raw, tags=TAGS, attributes=ATTRIBUTES, url_schemes={"http", "https"},
                     link_rel="noopener noreferrer")


@lru_cache(maxsize=24)
def _cached(path: str, mtime_ns: int, size: int) -> str:
    return to_html(Path(path).read_text(encoding="utf-8", errors="replace"))


def file_html(path: Path) -> str:
    """The rendered file, cached until the file changes."""
    st = path.stat()
    return _cached(str(path), st.st_mtime_ns, st.st_size)
