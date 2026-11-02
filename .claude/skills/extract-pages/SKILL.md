---
name: extract-pages
description: Extract specific pages from pdfs/{key}.pdf in place, replacing the file with just the requested pages — for scope-limiting whole-volume PDFs before /intake
---

> **This checkout's own steps:** if `.claude/skills/extract-pages/archive.md` exists, read it
> first. It is a private repository's layer on this skill (extra steps, local
> conventions) and is never part of the public repository; where it and this
> file differ, it wins for this checkout.

Replace `pdfs/{key}.pdf` with a subset of its pages. Use when the user has a whole-volume PDF (a multi-hundred-page monograph or journal volume) but only cares about a few pages within it — e.g., a chapter, an appendix, the figure plates, or a single description block.

This is the **destructive** companion to `/stitch`: where stitch builds a PDF up from images, extract-pages cuts an existing PDF down. The original is replaced in place; rely on git LFS history for rollback if the source was previously committed.

## 1. Parse the invocation

Slash form: `/extract-pages KEY PAGE_SPEC`

The `PAGE_SPEC` is 1-indexed and may include single pages, hyphenated ranges, and comma-separated combinations. Whitespace is permitted around commas and hyphens. Examples (all valid):

- `122` → page 122
- `122-123` → pages 122–123
- `97, 122-130` → page 97, then pages 122–130
- `1-3, 7, 11-13` → pages 1, 2, 3, 7, 11, 12, 13

Pages are emitted in spec order (so `130-122` produces a reverse range, which is rarely intentional but supported). Duplicate pages are silently dropped while preserving the first occurrence's position.

When the user invokes the skill, parse `ARGUMENTS` into the citation key (first whitespace-separated token) and the page spec (everything after). Pass both to the script — the script accepts the page spec quoted or unquoted (`nargs="+"`).

## 2. Pre-flight

```bash
# Confirm the PDF exists and report its current shape:
ls -la pdfs/{key}.pdf
./.runtime/venv/bin/python -c "
import pypdfium2 as pdfium
pdf = pdfium.PdfDocument('pdfs/{key}.pdf')
print(f'{len(pdf)} pages')
"

# Sanity-check the page spec is within range and looks reasonable.
```

If the requested pages would resolve to more than ~80% of the source PDF, **ask the user to confirm** — they likely want a different range, or shouldn't be running this skill at all (just commit the whole PDF). Same goes if the spec evaluates to zero pages or out-of-range numbers (the script will error, but flag it earlier in conversation).

## 3. Extract

```bash
./.runtime/venv/bin/python scripts/extract_pdf_pages.py {key} "{spec}"
```

The script:
- Opens `pdfs/{key}.pdf` via pypdfium2.
- Parses the spec, dedupes while preserving order, validates each page is in range.
- Copies the requested pages into a new PDF with pypdfium2's `import_pages` (original page streams, losslessly; each page brings only the objects it references, so a few pages cut from a volume don't carry the rest of it).
- Atomic-writes back to `pdfs/{key}.pdf` (`.tmp` + rename).
- Updates `manifest[key].acquisition`:
  - `pdf_extracted_at`: ISO timestamp.
  - `pdf_extract_pages`: compact form of the page list (consecutive runs collapsed: `[1, 3, 4, 5]` → `"1, 3-5"`).
  - Appends to `acquisition.notes` (or replaces a prior `extracted ...` note).

Output reports source page count + size, the resolved page list, output page count + size. If output exceeds 90 MB it emits a soft-limit warning (matches `/stitch`'s threshold).

### Common variations

```bash
# REQUIRED when the full source is not kept anywhere else (a public clone
# never commits PDFs; an archive may not have committed this one: check
# `git log -- pdfs/{key}.pdf`): keep it, under a descriptive name, outside
# the repository. Without it a whole monograph cut down to one chapter is gone.
./.runtime/venv/bin/python scripts/extract_pdf_pages.py {key} "{spec}" \
    --keep-source "/path/outside/the/repo/{Author} {year} - {short title}.pdf"

# Tune the size-limit warning threshold (no behavior change beyond the warning):
./.runtime/venv/bin/python scripts/extract_pdf_pages.py {key} "{spec}" \
    --soft-limit-mb 120

# Rewrite the PDF but leave manifest.json untouched (a PDF that isn't a
# manifest key). The page range is then recorded nowhere: put it in the
# commit message.
./.runtime/venv/bin/python scripts/extract_pdf_pages.py {key} "{spec}" --no-manifest

# Supplementary data: target supplementary/{parent}-{name}.pdf instead of pdfs/.
# Here the positional key is the full supplementary stem {parent}-{name}.
./.runtime/venv/bin/python scripts/extract_pdf_pages.py {parent}-{name} "{spec}" \
    --supplementary
```

### Supplementary PDFs (`--supplementary`)

With `--supplementary`, the script operates on `supplementary/{parent}-{name}.pdf`
(the supplementary-data folder — see `/intake`'s "Supplementary data" section)
and, instead of annotating `acquisition`, records the extraction on the
**parent's** sub-record: `manifest[parent]["supplementary"][name]` gets
`pdf_extract_pages` / `pdf_extracted_at` / `notes`. The `{parent}` is resolved by
longest-prefix match against manifest keys, so hyphenated parent keys and
hyphenated `{name}`s both resolve. After extracting, hand the supplementary stem
to `/intake` for `marker --supplementary` conversion (→ `markdown/supplementary/`).

## 4. Hand off to /intake

After extraction, the on-disk PDF is different from what was previously converted (if any). The conversion entry in the manifest still points at the *old* `markdown/{key}.md` from the full-volume conversion — and that markdown is now stale.

Two cases:

- **Acquisition was never run / no prior conversion**: the user's typical flow is `/intake` next, listing `{key}` as a fresh intake.
- **Prior conversion existed (e.g., from a full-volume marker run)**: the markdown is now stale. Treat as a *replacement* re-intake: tell the user that `{key}` should be in their next `/intake` re-conversion list. Don't invalidate the conversion entry yourself — `/intake`'s Path D (replacement re-convert with `--force`) handles it. A key already converted by vision OCR needs `convert_with_vision.py --key {key} --force`: without `--key` the script skips it, and without `--force` the page cache, which is keyed by page number, returns the old volume's pages.

## 5. Reporting

Tell the user:
1. The source page count + size.
2. The extracted page list (compact form) + new size.
3. The manifest annotation that was written.
4. Whether they should add the key to a `/intake` batch (always yes, unless they're already planning one).

## Notes / gotchas

- **Destructive in-place replacement**: there's no undo besides git LFS history. If the source PDF was uncommitted (e.g., the user hasn't yet pushed it from a stitched intake), it's gone after extraction. Use `--keep-source PATH` if rollback matters.
- **Cited→PDF page offsets**: when the user says "I want pages 39-40 of the original paper" but the PDF has frontmatter, **ask which physical PDF pages they mean** — same caveat as the scope-limited inline OCR pattern in `docs/notes/conversion.md` ("Scope-limited vision OCR for whole-volume PDFs"). Don't assume cited-page == PDF-page.
- **Lossless extraction**: `import_pages` preserves the original page content streams (text layer, embedded images, fonts). No re-rendering, no quality loss. The output is small because it has fewer pages, not because pages were re-encoded.
- **Page order**: `"130-122"` is a *reverse* range; the script honors it. If the user gives a list that's deliberately out of order (e.g. `"5, 1, 3"`), the output PDF preserves that order.
- **`pdf_images/{key}/`**: stale after extraction, if it exists. Re-extract from the new PDF with `/extract-images {key}` (`corpus pdf images --key {key} --force`); intake does not extract images.
- **Don't run on stitched-from-images PDFs** unless you mean it — those PDFs are already scope-limited via `/stitch`. Running extract-pages on top is a double-narrowing that's rarely useful.

## Decision pointer

```
User says: "I have a 400-page volume; only need pages X, Y-Z"
  ↓
PDF already in pdfs/{key}.pdf  (acquired, possibly committed)
  ↓
/extract-pages {key} "X, Y-Z"        (replaces in place; manifest annotated)
  ↓
Verify pdfs/{key}.pdf size + pages
  ↓
Add {key} to next /intake (re-)conversion list
```
