"""Render a PDF page to JPEG bytes for vision OCR."""
from __future__ import annotations

import io

# Claude downsamples images whose long edge exceeds this, so larger renders
# only cost time and upload size.
MAX_EDGE = 1568


def render_jpeg(doc, page_index: int, *, dpi: float = 150, max_edge: int = MAX_EDGE,
                quality: int = 85) -> bytes:
    """JPEG of one page of a pypdfium2 PdfDocument.

    Renders directly at min(dpi, the scale that makes the long edge max_edge)
    instead of rendering at dpi and resampling down, and closes the page."""
    page = doc[page_index]
    try:
        w_pt, h_pt = page.get_size()
        scale = dpi / 72.0
        if max_edge:
            scale = min(scale, max_edge / max(w_pt, h_pt, 1.0))
        pil = page.render(scale=scale).to_pil()
        if max_edge and max(pil.size) > max_edge:      # rounding can overshoot by a pixel
            r = max_edge / max(pil.size)
            pil = pil.resize((int(pil.width * r), int(pil.height * r)))
        buf = io.BytesIO()
        pil.convert("RGB").save(buf, format="JPEG", quality=quality, optimize=True)
        return buf.getvalue()
    finally:
        page.close()


def render_pil(pdf_path, page_index: int, *, dpi: float = 150):
    """One page (0-based) of the PDF at pdf_path as an RGB PIL image, at dpi
    (no size cap: callers that split the page into bands or tiles need the
    full resolution). Opens and closes the document."""
    import pypdfium2 as pdfium
    doc = pdfium.PdfDocument(str(pdf_path))
    try:
        page = doc[page_index]
        try:
            return page.render(scale=dpi / 72.0).to_pil().convert("RGB")
        finally:
            page.close()
    finally:
        doc.close()


def pil_jpeg(img, *, quality: int = 85) -> bytes:
    """JPEG bytes of a PIL image (a page, or a piece cut from one)."""
    buf = io.BytesIO()
    img.convert("RGB").save(buf, format="JPEG", quality=quality)
    return buf.getvalue()


def render_jpeg_file(pdf_path, page_index: int, **kw) -> bytes | None:
    """render_jpeg for one page (0-based) of the PDF at pdf_path; None if the
    page is out of range."""
    import pypdfium2 as pdfium
    doc = pdfium.PdfDocument(str(pdf_path))
    try:
        if not 0 <= page_index < len(doc):
            return None
        return render_jpeg(doc, page_index, **kw)
    finally:
        doc.close()
