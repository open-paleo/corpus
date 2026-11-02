---
name: extract-images
description: Rip embedded raster images (figures/plates) out of pdfs/{key}.pdf into pdf_images/{key}/ on demand — the manual companion to intake, which does not extract images
---

> **This checkout's own steps:** if `.claude/skills/extract-images/archive.md` exists, read it
> first. It is a private repository's layer on this skill (extra steps, local
> conventions) and is never part of the public repository; where it and this
> file differ, it wins for this checkout.

Extract the embedded raster images (figure plates, photos, line art) from
`pdfs/{key}.pdf` into `pdf_images/{key}/`. Use when you actually need the
figures from a specific paper: to view a plate, feed an image to another tool,
or inspect a figure the markdown refers to.

This is **on demand only**: intake does not extract images, because they are
rarely needed and cost several MB per paper. `pdf_images/` is gitignored:
extracted images stay on local disk and are never committed.

## 1. Parse the invocation

Slash form: `/extract-images KEY [KEY ...]`

Each `KEY` is a citation key whose PDF exists at `pdfs/{key}.pdf` (acquired
via `/intake`, `/stitch`, a fetcher, etc.).

## 2. Pre-flight

```bash
ls -la pdfs/{key}.pdf                       # the source PDF exists
ls pdf_images/{key}/ 2>/dev/null | wc -l    # already extracted?
```

If `pdfs/{key}.pdf` is missing, stop and tell the user: there is nothing to
extract from (they may need to `/intake` or `/stitch` it first).

## 3. Extract

```bash
./.runtime/venv/bin/corpus pdf images --key {key}            # skips a key already extracted
./.runtime/venv/bin/corpus pdf images --key {key} --force    # re-extract (after a PDF changed)
```

Images land in `pdf_images/{key}/p{page}-i{n}.{ext}` (n = the image's order on
that page). JPEG and JPEG 2000 keep their original encoding; Flate, CCITT and
JBIG2 images are decoded to lossless PNG (1-bit and grayscale kept as such).
An image repeated on several pages is written once. Each paper is extracted
into a temporary folder that replaces `pdf_images/{key}/` only when complete,
so `--force` never leaves images from an older PDF, and an interrupted run
leaves the old folder untouched. It is free and fast.

For a batch, pass `--keys-file FILE` (one key per line) or several `--key`.
`--all` extracts every PDF in `pdfs/`, which is large; the script refuses to
run with neither keys nor `--all`.

## 4. Report

Tell the user:
1. How many images were extracted and the total size (the script prints
   each key's count and `Images: N (M MB)`).
2. The output location: `pdf_images/{key}/`.
3. That `pdf_images/` is gitignored: the images stay local.

## Notes / gotchas

- **Vector-only PDFs yield few or zero images.** A born-digital paper whose
  figures are vector art (not embedded rasters) extracts nothing; the script
  says so. That is expected, not a failure. To get such a figure, render the
  page instead (`corpus.pdf.render`).
- **Stale after a PDF replacement or page extraction.** If `pdfs/{key}.pdf`
  changed (a re-intake, `/extract-pages`, `/stitch --force`), re-run with
  `--force`.
- **Disk cost.** Figure-heavy monographs can produce 50-100+ MB of images
  (a 128-page scanned monograph came to about 100 MB). Extract only what you
  need; delete `pdf_images/{key}/` when done if disk is tight.
- **No manifest write.** Image extraction is not tracked conversion state.

## Decision pointer

```
User: "I need the figures / plates from {key}"
  ↓
pdfs/{key}.pdf exists?  ── no ──▶ /intake or /stitch it first
  ↓ yes
/extract-images {key}          (corpus pdf images --key {key} [--force])
  ↓
pdf_images/{key}/  (gitignored, local only)
```
