---
name: stitch
description: Stitch source JPGs/PNGs from source_images/{key}/ into a single pdfs/{key}.pdf — lossless by default, auto re-encode if size approaches the upstream LFS soft limit
---

> **This checkout's own steps:** if `.claude/skills/stitch/archive.md` exists, read it
> first. It is a private repository's layer on this skill (extra steps, local
> conventions) and is never part of the public repository; where it and this
> file differ, it wins for this checkout.

Build a single `pdfs/{key}.pdf` from a directory of page-image files (JPGs, PNGs, TIFFs) the user has dropped under `source_images/{key}/`. The standard handoff before `/intake` for keys whose source comes from page photos, library scans, screenshots, etc. (not BHL — those go through `bhl_stitch.py`).

## 1. Confirm the input

User-provided source images live under `source_images/{key}/` (gitignored, like `bhl_images/` and `pdf_images/`). Filenames must lexically sort to page order — recommend three- or four-digit zero-padded prefixes:

```
source_images/
└── {key}/
    ├── 001.jpg
    ├── 002.jpg
    ├── ...
    └── 014.jpg
```

Supported extensions: `.jpg`, `.jpeg`, `.png`, `.tif`, `.tiff`, `.jp2`, `.heic`, `.heif` (case-insensitive). JP2 (JPEG 2000) is common in BHL JP2 dumps and other library scans — img2pdf embeds it unchanged on the lossless path, laid out at 72 DPI; Pillow handles it for the re-encode fallback (jpeg2000 plugin is installed in the venv). HEIC/HEIF inputs (iPhone photos) are decoded via `pillow_heif`; img2pdf can't embed HEIC, so the lossless path round-trips through a Pillow→JPEG q=95 step (still single-pass, near-lossless for scans).

Verify the source-image set matches the user's expectation before stitching:

```bash
ls source_images/{key}/ | wc -l      # page count
du -sh source_images/{key}/          # total raw size
ls source_images/{key}/ | head -3    # sort order spot-check
ls source_images/{key}/ | tail -3
```

If pages are out of order (e.g. `1.jpg`, `2.jpg`, `10.jpg` will sort wrong because `10` comes before `2` lexically), ask the user to rename or rename them yourself with zero-pad before stitching.

## 2. Verify the manifest entry

The script writes a PDF either way, but only updates `manifest[key].acquisition` if the key is already in the manifest. If the key is missing:
- Run `/refresh-bib` if you expect the bib to have it.
- Otherwise warn the user and confirm before stitching. `/intake` will still process the PDF as an orphan (a stub entry with `in_bib=False`), but the stitching provenance (`pdf_stitched_at`, `pdf_source`) is not recorded.

## 3. Stitch

```bash
./.runtime/venv/bin/python scripts/pdf_stitch.py --key {key}
```

Default behavior: **lossless embed via `corpus.pdf.build`** (img2pdf + pikepdf; the same path as `bhl_stitch.py`'s BHL JP2 stitching). Each input image becomes one page: JPEG and JP2 streams are embedded unchanged, PNG/TIFF losslessly — no quality loss, fast.

After stitching, the script reports the output PDF's size. If lossless output exceeds `--soft-limit-mb` (default **90 MB**), the script automatically re-stitches with a **re-encoded fallback**:

- Each image is loaded with Pillow, downscaled if its longest side exceeds `--max-dim` (default **2400 px**), and re-saved as JPEG at `--jpeg-q` quality (default **82**).
- The 2400 px ceiling preserves OCR quality (vision OCR uses DPI 150 ≈ ~2200 px on letter pages); 82 is a good middle ground that's nearly indistinguishable from the source on scans.
- The fallback output is much smaller and safe under the upstream git-LFS soft limit (~100-150 MB for the PDF, with margin for safety).

Why 90 MB threshold: the upstream git host enforces a soft 100-150 MB cap on LFS objects; staying under 90 MB leaves margin for any post-stitch adjustments and avoids the cliff.

### Photocopier variant (HEIC volume photos with a yellow cast)

When the source is iPhone/HEIC **photos of a physical volume** — warm/yellow
paper, uneven lighting — use `scripts/pdf_photocopier.py` instead. It
applies a **grayscale flat-field illumination normalization** (divide each
page by a large-blur estimate of its own background → cancels the paper cast
+ lighting gradient → contrast-stretch so paper clips to white) before
stitching. It is NOT a 1-bit threshold: figure/plate grayscale (halftone
stipple, pen-and-ink hatching) is preserved. Output is grayscale JPEG q88
capped at 2400 px, so the OCR-clean pages also convert cheaply at intake.

```bash
# whole photographed paper:
./.runtime/venv/bin/python scripts/pdf_photocopier.py --key {key}
# a chapter within a multi-paper volume — inclusive sorted filename range:
./.runtime/venv/bin/python scripts/pdf_photocopier.py --key {key} \
    --select IMG_0612 IMG_0620
# tunables (rarely needed; defaults tuned on 1930s-70s letterpress):
#   --white 0.93 --black 0.55 --gamma 1.15 --max-dim 2400 --jpeg-q 88
# --force re-stitches over an existing PDF.
```

Always preview the effect on one page (and confirm the `--select` range maps
to the right chapter, checking printed page numbers) before running the full
set. Same manifest-annotation behaviour as `pdf_stitch.py`.

### Common variations

```bash
# Force re-encoding from the start (skip lossless attempt — useful when you
# already know inputs are huge or heterogeneous and you want predictability):
./.runtime/venv/bin/python scripts/pdf_stitch.py --key {key} --reencode

# Tune the soft limit (e.g., user knows the upstream is being lenient today):
./.runtime/venv/bin/python scripts/pdf_stitch.py --key {key} --soft-limit-mb 120

# Override re-encode parameters for higher fidelity (larger PDF):
./.runtime/venv/bin/python scripts/pdf_stitch.py --key {key} --reencode --max-dim 3000 --jpeg-q 90

# Disable the auto fallback entirely (commit lossless even if huge — only do
# this if you'll handle the LFS overage manually, e.g. host-side configuration):
./.runtime/venv/bin/python scripts/pdf_stitch.py --key {key} --no-reencode

# Re-stitch over an existing PDF:
./.runtime/venv/bin/python scripts/pdf_stitch.py --key {key} --force

# Stitch all keys with images under source_images/ (omit --key):
./.runtime/venv/bin/python scripts/pdf_stitch.py
```

## 4. Manifest update

If the key was in the manifest, the script writes:

```jsonc
{
  "acquisition": {
    "pdf_path":      "pdfs/{key}.pdf",
    "pdf_stitched_at": "2026-..-..T..:..:..+00:00",
    "pdf_source":    "stitched from N source images (lossless | reencoded | reencoded (auto-fallback))"
  }
}
```

Note `acquisition.source` is **not** set here — that's `/intake`'s job in step 3 of its runbook (it sets `source=manual`, `previous_source` preserved). The `/stitch` script just lays the PDF down and annotates the stitching provenance.

## 5. Hand off to intake

Once the PDF exists, the rest of the workflow is the standard `/intake` flow:

- For 1800s scans, the user typically already knows this is a vision-OCR-direct case. `/intake` will set `vision_ocr_needed=True` and route to vision OCR.
- For modern scans, `/intake` will run marker first and fall back to vision if marker output is a stub.

Ask the user whether to run `/intake` now or hold off (they may want to drop more PDFs first to batch the conversion).

## Notes / gotchas

- **Lossless ≠ small for PNG inputs**: PNG inputs that are large in pixel dimensions still produce big PDFs lossless. Re-encoded JPEG is usually smaller for full-page scans. For diagrams/charts already PNG-compressed at low size, lossless wins. The script picks lossless first because for scans (the common case) it's higher quality — but trust the auto-fallback to handle the size cliff.
- **TIFF support varies**: img2pdf embeds most TIFFs; anything it refuses is decoded by Pillow and embedded as lossless PNG automatically. If a TIFF still errors, use `--reencode`.
- **Color profile / orientation**: re-encode path converts to RGB and applies any rotation flags via Pillow's default behavior; lossless path embeds streams as-is, so an EXIF-rotated JPEG will display rotated in the PDF. If output looks rotated, re-encode to normalize.
- **Don't stitch into a key that already has a real PDF without `--force`**: the script skips by default to avoid clobbering acquired PDFs from a fetcher or a manual download.
- **`source_images/` is gitignored** — raw inputs aren't committed. Once `pdfs/{key}.pdf` is committed, the user can clean up `source_images/{key}/` if they want to free disk space.

## Decision pointer

```
User says: "I have a folder of JPGs to stitch into a PDF for {key}"
  ↓
Drop into source_images/{key}/  (zero-padded sortable filenames)
  ↓
/stitch --key {key}             (lossless first; auto re-encodes if > 90 MB)
  ↓
Verify pdfs/{key}.pdf size + page count
  ↓
/intake                         (next step in the workflow)
```
