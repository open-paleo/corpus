"""Build a PDF with one page per image, for the stitch scripts.

Each image becomes a full page through img2pdf, which embeds JPEG and JPEG 2000
data unchanged (DCTDecode / JPXDecode) and PNG/TIFF losslessly (FlateDecode),
so "lossless" stitching is lossless. Pages are joined with pikepdf.

Page size: the DPI an image declares, else 96 DPI, except JPEG 2000 (BHL page
scans), laid out at 72 DPI, and an image declaring a DPI below 10 (a bogus
value such as 0.381), also laid out at 72. These are the sizes of the stitched
PDFs already in archives, so marker and vision see the same pixel count when
they render a page at a fixed DPI.

    from corpus.pdf.build import images_to_pdf
    images_to_pdf([path_or_jpeg_bytes, ...], out_path)
    images_to_pdf([(jpeg_bytes, (w_pt, h_pt)), ...], out_path)   # fixed page size

`out_path` is written via a `.tmp` file and renamed, like every other write here.
"""
from __future__ import annotations

import io
from pathlib import Path

import img2pdf
import pikepdf
from PIL import Image

_JP2_LAYOUT = img2pdf.get_fixed_dpi_layout_fun((72, 72))


def _is_jp2(data: bytes) -> bool:
    return data[:12] == b"\x00\x00\x00\x0cjP  \r\n\x87\n" or data[:4] == b"\xff\x4f\xff\x51"


def _bogus_dpi(data: bytes) -> bool:
    try:
        with Image.open(io.BytesIO(data)) as im:
            dpi = im.info.get("dpi")
    except Exception:
        return False
    return bool(dpi) and 0 < min(dpi) < 10


def _lossless_png(data: bytes) -> bytes:
    """Pillow re-encode for what img2pdf refuses (alpha, odd modes): PNG, alpha
    flattened onto white, DPI kept."""
    with Image.open(io.BytesIO(data)) as im:
        dpi = im.info.get("dpi")
        if im.mode in ("RGBA", "LA", "PA") or (im.mode == "P" and "transparency" in im.info):
            rgba = im.convert("RGBA")
            flat = Image.new("RGB", rgba.size, "white")
            flat.paste(rgba, mask=rgba.split()[-1])
            im = flat
        elif im.mode not in ("1", "L", "RGB", "CMYK"):
            im = im.convert("RGB")
        buf = io.BytesIO()
        im.save(buf, format="PNG", **({"dpi": dpi} if dpi else {}))
        return buf.getvalue()


def image_page(image, pagesize=None) -> bytes:
    """One-page PDF bytes for an image given as a path or as encoded bytes.

    With pagesize (points), the image fills a page of exactly that size."""
    data = Path(image).read_bytes() if isinstance(image, (str, Path)) else bytes(image)
    if pagesize:
        kw = {"layout_fun": img2pdf.get_layout_fun(pagesize=tuple(pagesize),
                                                   fit=img2pdf.FitMode.exact)}
    elif _is_jp2(data) or _bogus_dpi(data):
        kw = {"layout_fun": _JP2_LAYOUT}
    else:
        kw = {}
    try:
        return img2pdf.convert(data, **kw)
    except Exception:
        return img2pdf.convert(_lossless_png(data), **kw)


def images_to_pdf_bytes(images) -> bytes:
    """The PDF, one page per image, as bytes.

    Each item is an image (path or bytes) or an (image, (w_pt, h_pt)) pair."""
    out = pikepdf.Pdf.new()
    parts = []
    for image in images:
        img, size = image if isinstance(image, tuple) else (image, None)
        part = pikepdf.Pdf.open(io.BytesIO(image_page(img, size)))
        parts.append(part)
        out.pages.extend(part.pages)
    buf = io.BytesIO()
    out.save(buf, compress_streams=True,
             object_stream_mode=pikepdf.ObjectStreamMode.generate)
    out.close()
    for part in parts:
        part.close()
    return buf.getvalue()


def images_to_pdf(images, out_path: Path) -> int:
    """Write out_path with one page per image (items as for images_to_pdf_bytes).
    Returns the file size in bytes."""
    out_path = Path(out_path)
    tmp = out_path.with_suffix(out_path.suffix + ".tmp")
    tmp.write_bytes(images_to_pdf_bytes(images))
    tmp.replace(out_path)
    return out_path.stat().st_size
