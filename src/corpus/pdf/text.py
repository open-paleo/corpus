"""Character-level PDF text for the repair scripts, on PyMuPDF or pypdfium2.

The font repairs need each character's text together with the font that drew
it, its size and its box. PyMuPDF gives that as `page.get_text("rawdict")`;
pypdfium2 gives it one character at a time. This module returns the same
rawdict shape from either library, so a script is written once:

    from corpus.pdf.text import open_pdf
    doc = open_pdf(path)
    for pno in range(len(doc)):
        for block in doc.rawdict(pno)["blocks"]:
            for line in block["lines"]:
                for span in line["spans"]:
                    span["font"], span["size"], span["flags"]
                    for ch in span["chars"]:
                        ch["c"], ch["bbox"], ch["origin"]

Each line carries `line["dir"]`, the writing direction as PyMuPDF gives it:
(1, 0) for horizontal text, (0, -1) for text running up the page.

`doc.dict(pno)` is the same with each span's text joined into `span["text"]`
and a span `bbox` instead of `chars` (PyMuPDF's `get_text("dict")`).
`doc.render_png(pno, rect, dpi)` renders a region of a page, in the same
top-left coordinates, to PNG bytes.

`font_differences(path)` reads each font's `/Encoding /Differences` table
(pikepdf, independent of the backend).

Backend: `PDF_BACKEND=pdfium` (the default) or `PDF_BACKEND=pymupdf`, kept
for comparison. Running a repair under both and diffing its dry-run output
checks that the backends agree.

What the pdfium backend normalises, so the repairs (written against PyMuPDF's
output) see the same text on both backends:

- Font names drop the subset prefix (`EAGIHG+OpenSans` -> `OpenSans`). The
  PyMuPDF backend does the same, so scripts compare bare names on both.
  pdfium gives names in full, where PyMuPDF cuts them at 24 characters
  (`Universal-GreekwithMathPi`, PyMuPDF `...MathP`). Type 3 fonts have no
  name in pdfium (PyMuPDF uses the resource key, `T1`): font is "".
- pdfium writes a soft line-end hyphen as U+0002 and flags it with
  FPDFText_IsHyphen, and drops the line break that follows it. It comes back
  here as "-" followed by a line end, as PyMuPDF has it. This matters:
  U+0002 is also a real control-character glyph that corpus repair control_glyphs
  looks for, and only flagged hyphens are converted.
- pdfium inserts generated characters (spaces and line breaks it inferred).
  Generated line breaks end a line; generated spaces are kept as spaces, which
  PyMuPDF also emits between words.

Blocks: PyMuPDF groups lines into blocks by layout; pdfium has no blocks. The
pdfium backend returns one block per line. Scripts that only walk the
block/line/span order (all the repairs) are unaffected.

text(): PyMuPDF returns its own block-ordered text with marks where it put
them; pdfium returns line-ordered text after mark re-placement. A script
that slices text() by offset to take context around a match (as
corpus repair symbols does) therefore sees different left/right context on
the two backends; compare by content, not by offset, across backends.

Coordinates are converted to PyMuPDF's convention (origin at the top left of
the page, y downwards).
"""
from __future__ import annotations

import bisect
import ctypes
import os
import re
from pathlib import Path

BACKEND = os.environ.get("PDF_BACKEND", "pdfium").lower()

# Opt-in on-disk cache of each PDF's processed characters (pdfium backend),
# for runs of several repairs over the whole corpus: a cached PDF loads in
# ~40% of the time extraction takes (0.5 s against 1.3 s for a 65-page paper)
# but costs ~4-5 MB of disk per PDF, ~12 GB for the corpus, so it is
# off unless PDF_TEXT_CACHE=1. Entries are keyed on the PDF's size and mtime
# and on EXTRACTION_VERSION, which must be bumped whenever the extraction
# below changes what it returns.
CACHE_ENABLED = os.environ.get("PDF_TEXT_CACHE") == "1"
EXTRACTION_VERSION = 1


_SUBSET = re.compile(r"^[A-Z]{6}\+")


def bare_font(name: str) -> str:
    """Font name without the subset tag (`ABCDEF+Name` -> `Name`).

    Only a leading six-capital tag is removed: some real names contain a "+"
    themselves (`AdvTTe692faf0+02`), and pdfium sometimes returns names that
    already lack the tag.
    """
    return _SUBSET.sub("", name)


class _MuDoc:
    def __init__(self, path):
        import fitz
        self.path = Path(path)
        self._doc = fitz.open(path)

    def __len__(self):
        return len(self._doc)

    def close(self):
        self._doc.close()

    def page_fonts(self, pno: int) -> list[str]:
        return [bare_font(f[3]) for f in self._doc.get_page_fonts(pno)]

    def text(self, pno: int) -> str:
        return self._doc[pno].get_text()

    def rawdict(self, pno: int) -> dict:
        d = self._doc[pno].get_text("rawdict")
        for block in d["blocks"]:
            for line in block.get("lines", []):
                for span in line.get("spans", []):
                    span["font"] = bare_font(span["font"])
        d["blocks"] = [b for b in d["blocks"] if b.get("lines")]
        return d

    def dict(self, pno: int) -> dict:
        d = self._doc[pno].get_text("dict")
        d["blocks"] = [b for b in d["blocks"] if b.get("lines")]
        for block in d["blocks"]:
            for line in block["lines"]:
                for span in line.get("spans", []):
                    span["font"] = bare_font(span["font"])
        return d

    def render_png(self, pno: int, rect, dpi: int) -> bytes:
        import fitz
        return self._doc[pno].get_pixmap(clip=fitz.Rect(*rect), dpi=dpi).tobytes("png")


class _PdfiumDoc:
    def __init__(self, path):
        import pypdfium2 as pdfium
        import pypdfium2.raw as raw
        self._pdfium, self._raw = pdfium, raw
        self.path = Path(path)
        self._doc = pdfium.PdfDocument(str(path))
        self._buf = ctypes.create_string_buffer(512)
        self._memo: dict[int, list[dict]] = {}
        self._path = os.path.abspath(path)
        self._dirty = False
        if CACHE_ENABLED:
            self._memo.update(_cache_load(self._path))

    def __len__(self):
        return len(self._doc)

    def close(self):
        if CACHE_ENABLED and self._dirty:
            _cache_save(self._path, self._memo)
        self._memo.clear()
        self._doc.close()

    def page_fonts(self, pno: int) -> list[str]:
        """Font names used by the page's text, as the PDF names them.

        Read per character with FPDFText_GetFontInfo, which returns the PDF's
        own BaseFont. (FPDFFont_GetFontName, the object-level call in this
        pdfium build, returns the substituted system font for non-embedded
        fonts, e.g. Arial for Helvetica, so it can't be used here.)
        """
        raw = self._raw
        tp = self._doc[pno].get_textpage()
        h, flags, names, last = tp.raw, ctypes.c_int(), set(), None
        for i in range(raw.FPDFText_CountChars(h)):
            n = raw.FPDFText_GetFontInfo(h, i, self._buf, len(self._buf), ctypes.byref(flags))
            if n:
                v = self._buf.value
                if v != last:
                    names.add(bare_font(v.decode("utf-8", "replace")))
                    last = v
        tp.close()
        return sorted(names)

    def _raw_chars(self, pno: int):
        """Yield one dict per character in textpage order, with PyMuPDF coordinates."""
        raw = self._raw
        page = self._doc[pno]
        height = page.get_height()
        tp = page.get_textpage()
        h = tp.raw
        flags = ctypes.c_int()
        rect, mat = raw.FS_RECTF(), raw.FS_MATRIX()
        ox, oy = ctypes.c_double(), ctypes.c_double()
        # Bound once: this loop runs once per character of every page.
        get_unicode, is_hyphen = raw.FPDFText_GetUnicode, raw.FPDFText_IsHyphen
        is_generated, get_font_info = raw.FPDFText_IsGenerated, raw.FPDFText_GetFontInfo
        get_loose_box, get_origin = raw.FPDFText_GetLooseCharBox, raw.FPDFText_GetCharOrigin
        get_matrix, get_size = raw.FPDFText_GetMatrix, raw.FPDFText_GetFontSize
        buf, buflen, flags_ref = self._buf, len(self._buf), ctypes.byref(flags)
        ox_ref, oy_ref = ctypes.byref(ox), ctypes.byref(oy)
        last_raw, last_font = None, ""
        for i in range(raw.FPDFText_CountChars(h)):
            code = get_unicode(h, i)
            hyphen = bool(is_hyphen(h, i))
            if hyphen:
                c = "-"
            else:
                # code 0: no Unicode mapping; PyMuPDF reports U+FFFD there
                c = chr(code) if code else "\ufffd"
            gen = bool(is_generated(h, i))
            n = get_font_info(h, i, buf, buflen, flags_ref)
            if n:
                v = buf.value
                if v != last_raw:      # decode only when the font changes
                    last_raw, last_font = v, bare_font(v.decode("utf-8", "replace"))
                font = last_font
            else:
                font = ""
            # Loose box: the font's full ascent-to-descent height and advance
            # width, which is what PyMuPDF reports (the tight GetCharBox hugs
            # the ink instead). Checked equal to PyMuPDF to 0.1 pt.
            get_loose_box(h, i, rect)
            box = (rect.left, height - rect.top, rect.right, height - rect.bottom)
            if not gen and box[3] - box[1] < 0.5:
                # Type 3 fonts have no ascent/descent, so the loose box is a
                # sliver (as little as 0.2 pt tall); take the ink box instead.
                l, r_, b, t = (ctypes.c_double() for _ in range(4))
                raw.FPDFText_GetCharBox(h, i, ctypes.byref(l), ctypes.byref(r_),
                                        ctypes.byref(b), ctypes.byref(t))
                if t.value > b.value:
                    box = (l.value, height - t.value, r_.value, height - b.value)
            get_origin(h, i, ox_ref, oy_ref)
            # GetFontSize is the size before the text matrix (often 1.0); the
            # size on the page is that times the matrix scale.
            get_matrix(h, i, mat)
            scale = abs(mat.a * mat.d - mat.b * mat.c) ** 0.5 or 1.0
            yield {
                "c": c, "generated": gen, "hyphen": hyphen, "font": font, "flags": flags.value,
                # writing direction, as PyMuPDF's line "dir": (cos, sin) of the
                # baseline angle with y downwards (horizontal text: (1, 0))
                "dir": _unit(mat.a, -mat.b),
                "size": round(get_size(h, i) * scale, 2),
                "bbox": box,
                "origin": (ox.value, height - oy.value),
            }
        tp.close()

    def _chars(self, pno: int) -> list[dict]:
        """Characters in reading order, with spacing accent marks moved back in
        front of the letter they are drawn over (see _place_marks) and
        spurious line breaks inside a word removed (see _join_broken_lines).

        Memoised per page: text() and rawdict() both start here, and scripts
        call both for the same page. Callers must not mutate the result."""
        got = self._memo.get(pno)
        if got is None:
            got = self._memo[pno] = _place_marks(_join_broken_lines(list(self._raw_chars(pno))))
            self._dirty = True
        return got

    def text(self, pno: int) -> str:
        """Plain text in textpage order, line breaks as "\\n", hyphens as "-"."""
        out = []
        for ch in self._chars(pno):
            c = ch["c"]
            if c == "\r":
                continue
            out.append(c)
            if ch["hyphen"]:      # pdfium drops the line break after a soft hyphen
                out.append("\n")
        return "".join(out)

    def rawdict(self, pno: int) -> dict:
        blocks, spans, span = [], [], None
        line_dir = None

        def end_line():
            nonlocal spans, span, line_dir
            if span:
                spans.append(span)
            if spans:
                boxes = [c["bbox"] for sp in spans for c in sp["chars"]]
                blocks.append({"lines": [{"spans": spans, "bbox": _union(boxes),
                                          "dir": line_dir or (1.0, 0.0)}]})
            spans, span, line_dir = [], None, None

        for ch in self._chars(pno):
            c = ch["c"]
            if c in ("\r", "\n"):
                end_line()
                continue
            if ch["generated"] and c == " ":
                font, size, flags = (span["font"], span["size"], span["flags"]) if span else ("", 0, 0)
            else:
                font, size, flags = ch["font"], ch["size"], ch["flags"]
                if line_dir is None:
                    line_dir = ch["dir"]
            if span is None or (span["font"], span["size"]) != (font, size):
                if span:
                    spans.append(span)
                span = {"font": font, "size": size, "flags": flags, "chars": []}
            span["chars"].append({"c": c, "bbox": ch["bbox"], "origin": ch["origin"]})
            if ch["hyphen"]:      # pdfium drops the line break after a soft hyphen
                end_line()
        end_line()
        return {"blocks": blocks}

    def dict(self, pno: int) -> dict:
        """rawdict with each span's characters joined into text and a span box."""
        d = self.rawdict(pno)
        for block in d["blocks"]:
            for line in block["lines"]:
                for span in line["spans"]:
                    chars = span.pop("chars")
                    span["text"] = "".join(c["c"] for c in chars)
                    span["bbox"] = _union([c["bbox"] for c in chars])
                    span["origin"] = chars[0]["origin"] if chars else (0, 0)
        return d

    def render_png(self, pno: int, rect, dpi: int) -> bytes:
        import io
        page = self._doc[pno]
        w, h = page.get_width(), page.get_height()
        x0, y0, x1, y1 = rect
        # crop is (left, bottom, right, top): points trimmed from each edge
        crop = (max(0, x0), max(0, h - y1), max(0, w - x1), max(0, y0))
        img = page.render(scale=dpi / 72, crop=crop).to_pil()
        buf = io.BytesIO()
        img.save(buf, "PNG")
        return buf.getvalue()


def _cache_file(path: str):
    import hashlib
    from corpus.config import cache_dir
    return cache_dir("pdftext") / (hashlib.sha1(path.encode("utf-8")).hexdigest() + ".pkl.z")


def _cache_stamp(path: str):
    st = os.stat(path)
    return (st.st_size, st.st_mtime_ns, EXTRACTION_VERSION)


def _cache_load(path: str) -> dict:
    """Cached pages for this PDF, or {} if absent, stale or unreadable."""
    import pickle
    import zlib
    try:
        stamp, pages = pickle.loads(zlib.decompress(_cache_file(path).read_bytes()))
    except Exception:
        return {}
    return pages if stamp == _cache_stamp(path) else {}


def _cache_save(path: str, pages: dict) -> None:
    import pickle
    import zlib
    f = _cache_file(path)
    f.parent.mkdir(parents=True, exist_ok=True)
    tmp = f.with_suffix(".tmp")
    tmp.write_bytes(zlib.compress(pickle.dumps((_cache_stamp(path), dict(pages)), protocol=5), 6))
    tmp.replace(f)


def _unit(x, y):
    n = (x * x + y * y) ** 0.5
    return (round(x / n, 3), round(y / n, 3)) if n else (1.0, 0.0)


def _union(boxes):
    if not boxes:
        return (0.0, 0.0, 0.0, 0.0)
    return (min(b[0] for b in boxes), min(b[1] for b in boxes),
            max(b[2] for b in boxes), max(b[3] for b in boxes))


def _join_broken_lines(chars: list[dict]) -> list[dict]:
    """Drop generated line breaks between two glyphs on the same visual line.

    pdfium starts a new line where the font changes and the baseline shifts a
    little, e.g. at an Elsevier companion-font accent: "Prieto-M\x01" then
    "arquez". PyMuPDF keeps those on one line. A break
    is dropped only when the glyphs either side overlap vertically by at least
    half the smaller height and the next one starts within a third of a
    height of where the previous one ends (or over it), so a real line wrap,
    which returns to the left, and a jump to the next column are kept.
    """
    out, i, n = [], 0, len(chars)
    while i < n:
        c = chars[i]
        if c["generated"] and c["c"] in "\r\n":
            j = i
            while j < n and chars[j]["generated"] and chars[j]["c"] in "\r\n":
                j += 1
            prev = next((x for x in reversed(out) if not x["generated"]), None)
            nxt = chars[j] if j < n else None
            if prev and nxt and not nxt["generated"] and _same_line(prev["bbox"], nxt["bbox"]):
                i = j
                continue
        out.append(c)
        i += 1
    return out


def _same_line(a, b) -> bool:
    ha, hb = a[3] - a[1], b[3] - b[1]
    h = min(ha, hb)
    if h <= 0:
        return False
    v = min(a[3], b[3]) - max(a[1], b[1])
    return v >= 0.5 * h and a[0] - 0.5 * h <= b[0] <= a[2] + h / 3


# Spacing glyphs that some fonts draw as a separate accent over the next
# letter: the diaeresis that extracts as "€" (Taylor & Francis, Wiley,
# Elsevier), and TeX-style circumflex, acute, grave and tilde marks.
MARKS = set("€^´`¨˜~ˆ˚¸˝˘ˇ¯")


def _place_marks(chars: list[dict]) -> list[dict]:
    """Move each accent mark to just before the letter it overlaps.

    pdfium does not keep these marks next to their letter: it collects them out
    of reading order, sometimes on another line, with generated spaces around
    them ("Pal € aontologie", or München's mark at the end of the next line).
    Their boxes are right, though. A mark is moved only when its box lies over
    a letter (at least half its width overlapping, vertically overlapping or
    just above), so a real "~" or "^" in running text stays where it is. This
    reproduces PyMuPDF's order ("M€unchen", "F^ant^anele"), which the repairs
    were written against.
    """
    def overlap(m, c):
        mx0, my0, mx1, my1 = m["bbox"]
        cx0, cy0, cx1, cy1 = c["bbox"]
        w = mx1 - mx0
        if w <= 0 or cx1 <= cx0:
            return 0.0
        ox = min(mx1, cx1) - max(mx0, cx0)
        h = cy1 - cy0
        if ox <= 0 or not (my1 >= cy0 - 0.6 * h and my0 <= cy1):
            return 0.0
        return ox / w

    marks = [i for i, c in enumerate(chars) if c["c"] in MARKS and not c["generated"]]
    if not marks:
        return chars
    # Candidate letters sorted by left edge. A letter can overlap a mark only
    # if it starts before the mark ends and ends after the mark starts, so with
    # the widest letter's width as a bound, only letters starting inside
    # (mark.x0 - widest, mark.x1) need testing: a few per mark instead of
    # every letter on the page.
    letters = [j for j, c in enumerate(chars)
               if not c["generated"] and c["c"].isalpha() and c["c"] not in MARKS]
    letters.sort(key=lambda j: chars[j]["bbox"][0])
    xs = [chars[j]["bbox"][0] for j in letters]
    widest = max((chars[j]["bbox"][2] - chars[j]["bbox"][0] for j in letters), default=0.0)
    moves = {}
    for i in marks:
        mx0, _, mx1, _ = chars[i]["bbox"]
        lo = bisect.bisect_right(xs, mx0 - widest)
        hi = bisect.bisect_left(xs, mx1)
        best, bi = 0.5, None
        for j in sorted(letters[lo:hi]):          # page order: a tie goes to the earlier letter
            if j == i:
                continue
            o = overlap(chars[i], chars[j])
            if o > best:
                best, bi = o, j
        if bi is not None:
            moves[i] = bi
    if not moves:
        return chars
    drop = set(moves)
    # pdfium puts generated spaces around a stray mark. Once the mark is gone,
    # drop one of them only where two spaces would end up side by side; a
    # single space there is a real word break and stays.
    for i in moves:
        L = i - 1
        while L >= 0 and L in drop:
            L -= 1
        R = i + 1
        while R < len(chars) and R in drop:
            R += 1
        if (0 <= L and R < len(chars) and chars[L]["c"] == " " and chars[R]["c"] == " "
                and chars[R]["generated"]):
            drop.add(R)
        elif 0 <= L and chars[L]["generated"] and chars[L]["c"] == " " and (
                R >= len(chars) or chars[R]["c"] in " \n\r"):
            drop.add(L)
    before = {}
    for i, j in moves.items():
        before.setdefault(j, []).append(chars[i])
    out = []
    for k, c in enumerate(chars):
        if k in before:
            out.extend(before[k])
        if k not in drop:
            out.append(c)
    return out


def _pdf_name(name) -> str:
    """A PDF name without its slash. Names are bytes; most are ASCII, but some
    fonts carry other bytes (one sample font's BaseFont has 0xB7), read here
    as Latin-1."""
    if name is None:
        return ""
    raw = name.unparse()                      # b"/Name#B7", #xx-escaped
    raw = re.sub(rb"#([0-9A-Fa-f]{2})", lambda m: bytes([int(m.group(1), 16)]), raw)
    return raw[1:].decode("utf-8") if raw[1:].isascii() else raw[1:].decode("latin-1")


def font_differences(path) -> dict[str, dict[int, str]]:
    """{BaseFont: {code: glyph name}} from each font's /Encoding /Differences.

    Fonts are visited page by page, page resources first and then form
    XObjects, and the first font seen with a given BaseFont wins: the same
    order as PyMuPDF's get_fonts. BaseFont keeps its
    subset tag. Fonts without a Differences array are left out.
    """
    import pikepdf
    out: dict[str, dict[int, str]] = {}
    seen_res: set = set()

    def walk(res):
        if not isinstance(res, pikepdf.Dictionary):
            return
        # Only indirect objects can be shared (and so recurse); a direct
        # dictionary belongs to one page and is always visited. (id() of the
        # wrapper is no key: pikepdf makes a new one per access and ids repeat.)
        if res.is_indirect:
            if res.objgen in seen_res:
                return
            seen_res.add(res.objgen)
        fonts = res.get("/Font")
        if isinstance(fonts, pikepdf.Dictionary):
            for res_name, font in fonts.items():
                if not isinstance(font, pikepdf.Dictionary):
                    continue
                # Type 3 fonts have no BaseFont; PyMuPDF names them by their
                # resource key (T1, T2), so do the same.
                base = _pdf_name(font.get("/BaseFont")) or res_name.lstrip("/")
                if base in out:
                    continue
                enc = font.get("/Encoding")
                diffs = enc.get("/Differences") if isinstance(enc, pikepdf.Dictionary) else None
                table, code = {}, None
                for tok in (diffs or []):
                    if isinstance(tok, pikepdf.Name):
                        if code is not None:
                            table[code] = _pdf_name(tok)
                            code += 1
                    else:
                        # A malformed array can hold other objects; skip them
                        # and the names after them, until the next code.
                        try:
                            code = int(tok)
                        except (TypeError, ValueError):
                            code = None
                if table:
                    out[base] = table
        xobjs = res.get("/XObject")
        if isinstance(xobjs, pikepdf.Dictionary):
            for _, xo in xobjs.items():
                if isinstance(xo, pikepdf.Stream) and xo.get("/Subtype") == "/Form":
                    walk(xo.get("/Resources"))

    with pikepdf.open(str(path)) as pdf:
        for page in pdf.pages:
            # .resources follows /Resources inherited from the page tree
            walk(page.resources)
    return out


def open_pdf(path, backend: str | None = None):
    """Open a PDF on the chosen backend (default: $PDF_BACKEND, else pdfium)."""
    b = (backend or BACKEND).lower()
    if b == "pdfium":
        return _PdfiumDoc(path)
    if b == "pymupdf":
        return _MuDoc(path)
    raise ValueError(f"unknown PDF_BACKEND {b!r}")
