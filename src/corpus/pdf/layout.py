"""Page layout from a rendered image: where a two-column page's gutter is.

Used to cut a page into pieces for vision OCR only on whitespace
(convert_recover.py, `corpus convert recover`).
"""
from __future__ import annotations

# A blank strip is a gutter only if each side carries at least this share of
# the ink (a single-column block set off-centre is not two columns).
MIN_COLUMN_INK_SHARE = 0.20


def _gutter_x_in(block, w: int, ink_thresh: int, min_width: int) -> int | None:
    """Core detector: the x of a whitespace gutter in one image block, or None.

    Only the middle 30-70% of the width is considered, so page margins and the
    gap beside a figure don't read as a gutter. A blank strip counts only if
    BOTH sides carry at least MIN_COLUMN_INK_SHARE of the block's ink —
    without that, a single-column page whose text block sits off-centre reads
    as two-column (text offset right leaves a blank strip at a third of the
    width).
    """
    if block.shape[0] < 40:
        return None
    ink = (block < ink_thresh).astype("uint8")
    prof = ink.sum(axis=0)
    thr = max(3, int(0.01 * block.shape[0]))
    lo, hi = int(w * 0.30), int(w * 0.70)

    best, start = None, None
    for x in range(lo, hi):
        if prof[x] < thr:
            if start is None:
                start = x
        else:
            if start is not None and x - start >= min_width:
                if best is None or (x - start) > (best[1] - best[0]):
                    best = (start, x)
            start = None
    if start is not None and hi - start >= min_width:
        if best is None or (hi - start) > (best[1] - best[0]):
            best = (start, hi)
    if best is None:
        return None

    total = int(prof.sum())
    if total <= 0:
        return None
    left = int(prof[:best[0]].sum()) / total
    right = int(prof[best[1]:].sum()) / total
    if min(left, right) < MIN_COLUMN_INK_SHARE:
        return None
    return (best[0] + best[1]) // 2


def center_gutter_x(img, ink_thresh: int = 160,
                    min_width_frac: float = 0.012) -> int | None:
    """Return the x of a sustained whitespace gutter near mid-width, or None.

    Tries the whole body first, then six horizontal slices: a page whose text
    is set in two columns *below* a full-width figure shows no gutter in the
    whole-body profile (the figure puts ink right across the middle) but does
    in the slices that contain only text. Two slices agreeing is enough.
    """
    import numpy as np

    gray = np.asarray(img.convert("L"))
    h, w = gray.shape
    # Ignore the top 12% so a full-width running head doesn't mask the gutter.
    body = gray[int(h * 0.12):, :]
    min_width = max(4, int(w * min_width_frac))

    whole = _gutter_x_in(body, w, ink_thresh, min_width)
    if whole is not None:
        return whole

    slices = 6
    step = body.shape[0] // slices
    found = [g for g in (_gutter_x_in(body[i * step:(i + 1) * step, :], w,
                                      ink_thresh, min_width)
                         for i in range(slices)) if g is not None]
    if len(found) >= 2:
        found.sort()
        if found[-1] - found[0] <= w * 0.05:
            return found[len(found) // 2]
    return None
