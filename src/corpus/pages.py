"""Page markers: where each PDF page starts in a paper's markdown.

A converter writes `<!-- page N -->` on its own line at the start of the text
it read from page N of the PDF (1-based: the page's position in the file, not
its printed number). Vision output still separates pages with a `---` line;
the marker follows the separator. Rendered markdown hides the comment, and the
indexer strips it, so search never sees it.

A marker is only useful while it stays where the converter put it, so every
script that rewrites converted markdown saves through write_markdown(), which
refuses a result whose markers differ from the file's current ones: a repair
that deleted or reordered a marker would otherwise go unnoticed until the
reader showed the wrong PDF page.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

from corpus.io import atomic_write_text

MARKER = re.compile(r"^<!-- page (\d+) -->$", re.M)
SEPARATOR = "\n\n---\n\n"
# marker's --paginate_output separator: "{N}" (0-based) and 48 dashes.
MARKER_PDF_SEPARATOR = re.compile(r"^\{(\d+)\}-{48}$", re.M)


def marker(n: int) -> str:
    return f"<!-- page {n} -->"


def numbers(text: str) -> list[int]:
    """The page numbers of the text's markers, in order."""
    return [int(n) for n in MARKER.findall(text)]


def strip(text: str) -> str:
    """The text without its markers (and the blank line each one opens)."""
    return re.sub(r"^<!-- page \d+ -->\n(?:[ \t]*\n)?", "", text, flags=re.M)


def join(pages: list[tuple[int, str]]) -> str:
    """Vision-style markdown: each page opened by its marker, pages separated by `---`."""
    return SEPARATOR.join(f"{marker(n)}\n\n{md}" for n, md in pages) + "\n"


def from_marker_output(text: str) -> str:
    """marker's paginated markdown with its separators turned into page markers."""
    text = MARKER_PDF_SEPARATOR.sub(lambda m: marker(int(m.group(1)) + 1), text)
    return text.lstrip("\n")


def segments(text: str) -> list[tuple[str, str]]:
    """The text cut at its markers: [(marker line or "", the text after it)].

    The first segment is whatever precedes the first marker (often ""). Joining
    marker + "\\n\\n" + text for each segment restores the original up to the
    blank lines around markers.
    """
    out, at, opener = [], 0, ""
    for m in MARKER.finditer(text):
        out.append((opener, text[at:m.start()].strip("\n")))
        opener, at = m.group(0), m.end()
    out.append((opener, text[at:].strip("\n")))
    return [(o, t) for o, t in out if o or t]


def in_marker(text: str):
    """A test for "this offset lies inside a page-marker line", for scripts that
    scan the text for numbers: a marker's page number must never be taken for
    a line number or a footnote call."""
    spans = [(m.start(), m.end()) for m in MARKER.finditer(text)]
    return lambda pos: any(a <= pos < b for a, b in spans)


def restore_dropped(old: str, new: str) -> str:
    """`new` with the markers of pages it deleted outright put back.

    For edits that remove whole pages of junk (a cover sheet, a blank page):
    the PDF still has the page, so its marker stays, next to the following
    page's marker. Returns `new` unchanged if its markers are not a
    subsequence of `old`'s (the guard in write_markdown then refuses it).
    """
    before, after = numbers(old), numbers(new)
    it = iter(before)
    if before == after or not all(n in it for n in after):
        return new
    kept = set(after)
    missing_before: dict[int | None, list[int]] = {}
    pending: list[int] = []
    for n in before:
        if n in kept:
            if pending:
                missing_before[n] = pending
                pending = []
        else:
            pending.append(n)
    out = MARKER.sub(lambda m: "\n\n".join(
        [marker(p) for p in missing_before.get(int(m.group(1)), [])] + [m.group(0)]), new)
    if pending:
        out = out.rstrip("\n") + "\n\n" + "\n\n".join(marker(p) for p in pending) + "\n"
    return out


def unsegment(parts: list[tuple[str, str]]) -> str:
    return "\n\n".join("\n\n".join(p for p in (o, t) if p) for o, t in parts).strip("\n") + "\n"


class MarkersChanged(ValueError):
    pass


def check(old: str, new: str, name: str = "") -> None:
    """Raise MarkersChanged unless `new` keeps exactly the markers of `old`."""
    before, after = numbers(old), numbers(new)
    if before != after:
        lost = sorted(set(before) - set(after))
        detail = f"lost pages {lost[:8]}" if lost else "markers reordered or duplicated"
        raise MarkersChanged(f"{name}: the rewrite changes the page markers ({detail})")


def write_markdown(path: Path, new: str, old: str | None = None) -> bool:
    """Write converted markdown that an edit produced; refuse if its markers changed.

    `old` is the text the edit started from (default: the file as it is now).
    Returns False, with a message on stderr, when the write is refused.
    """
    if old is None:
        old = path.read_text(encoding="utf-8", errors="replace") if path.exists() else ""
    try:
        check(old, new, path.name)
    except MarkersChanged as e:
        print(f"  ! not written: {e}", file=sys.stderr)
        return False
    atomic_write_text(path, new)
    return True


# Markers alone are about 18 bytes a page, so a file this large has text.
_SURELY_TEXT = 64 * 1024


def is_empty(path: Path) -> bool:
    """True when a markdown file is missing or holds nothing but page markers.

    For a scan without a text layer marker writes the page markers and
    nothing else, so the file size alone does not show whether there is text.
    """
    if not path.exists():
        return True
    size = path.stat().st_size
    if size == 0:
        return True
    if size > _SURELY_TEXT:
        return False
    return not strip(path.read_text(encoding="utf-8", errors="replace")).strip()


def text_size(path: Path) -> int:
    """Bytes of text in a markdown file, not counting its page markers."""
    if not path.exists():
        return 0
    return len(strip(path.read_text(encoding="utf-8", errors="replace")).encode("utf-8"))
