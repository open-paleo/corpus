---
name: intake
description: Discover new/updated PDFs in pdfs/, classify by era + scope, run marker/vision/translation/QC, index, and commit
---

> **This checkout's own steps:** if `.claude/skills/intake/archive.md` exists, read it
> first. It is a private repository's layer on this skill (extra steps, local
> conventions) and is never part of the public repository; where it and this
> file differ, it wins for this checkout.

End-to-end intake for new or replaced PDFs sitting in `pdfs/`: the batch workflow in CLAUDE.md, plus the era classification, scope limiting and stub triage described in `docs/notes/conversion.md`.

## 1. Discover what changed

Run the discovery script. It does NFC-normalized `disk - acquired` plus the `git status pdfs/` cross-check (replacements + already-acquired-uncommitted) in one pass, and prints a Path A / B / C / D / V classification hint per pending key with year + page-count metadata pulled from the manifest:

```bash
./.runtime/venv/bin/python scripts/intake_discover.py          # --json for machine-readable output
```

Output columns: `key | state (new/untracked/modified/tracked) | in_m | in_bib | year | pp | KB | path-hint | note`. The `state` column maps to:

- **new** — disk-but-not-acquired, fresh intake.
- **untracked** — PDF on disk but not in git tracking AND already-acquired in manifest. This is the already-acquired-uncommitted case (often a failed-fetch sentinel such as `unpaywall_no_oa` + a real PDF dropped later). The script flags it with `prior source=...`. Treat as a new acquisition with `previous_source` preserved (the acquire script does this automatically).
- **modified** — PDF tracked in git but content changed. Replacement re-convert (Path D). Surface to user.
- **unconverted** — PDF on disk, acquisition already recorded (typically by a fetcher: `corpus fetch unpaywall` / `pmc` / `oa-direct`), but no markdown and not marked citation-only. Convert it by the normal path for its year; **skip §3** for it, since `intake_acquire.py` would overwrite the fetcher's `source` with `manual`. In a public checkout, where `pdfs/` is gitignored, this is the only way fetched papers reach intake. Papers from `corpus bhl fetch` arrive this way already flagged `vision_ocr_needed` (Path B, §7): check a volume match's page range and any end-of-volume plates against the citation before OCR.

The `path-hint` column suggests:
- `B` for pre-1900 papers (vision OCR direct).
- `A` for ≥1900 papers (marker first).
- `C` if the PDF is much larger than the cited page range (whole-volume scope-limit candidate; ask user).
- `D` is set automatically when `state=modified`.
- `V` when the text layer carries manuscript line numbers (vision OCR, never marker).
- `?` when no year metadata is available (inspect manifest).

### Split-key collisions — STOP and rename before proceeding

The script also detects **bare-key-next-to-split-key collisions** and prints a
`⚠  SPLIT-KEY COLLISION` block (and a `⚠SPLIT` marker on the row). This catches
the case the OS file-exists prompt misses: dropping `foo2015` when `foo2015a`
and `foo2015b` already exist. An exact-name clash is caught at copy time by the
filesystem, but a bare-vs-split clash is not — it slips through to intake and
forces a rename/move later.

If any collision is reported, **STOP before acquiring/converting** and surface
it to the user with the script's suggested rename (the next free letter, e.g.
`foo2015c`). Once the user confirms the correct key, rename the PDF in `pdfs/`
and re-run `intake_discover.py` to confirm the warning clears, then continue.
Don't acquire a bare key that the script flagged without resolving it first.

### Replacements named by the user

Plus any keys the user explicitly named for re-conversion (e.g. "X.pdf has been updated"). These have `acquired` already but need re-running as Path D.

### Orphans — process inline; flag at the end

Orphans (PDFs whose key isn't in the manifest at all, shown as `in_m=ORPH`) are **not skipped**. The corpus may include reference-only papers that aren't in the upstream `open-paleo/data` bib, and PDFs are routinely dropped *ahead* of bib propagation (the open-paleo workflow pulls genus information from corpus papers before committing the bib entry). Process them through the normal classification + conversion path. The acquire script (§3) creates a stub manifest entry with `in_bib=False` and otherwise-blank citation metadata; `refresh_manifest.py` will overlay bib metadata and flip `in_bib=True` when the bib catches up.

Flag the not-in-bib set in your final summary so the user knows which keys still need bib follow-up. If you suspect the bib already has new entries that should overlay before processing (e.g., the user mentions a recent push), run `/refresh-bib` first — but in the absence of that signal, just process.

### Failed-fetch sentinels on keys already processed

A key whose `acquisition.source` is a failed-fetch sentinel (`unpaywall_no_oa`, `oa_direct_landing_failed`, ...; the full list is `corpus.manifest.FAILED_SUFFIXES`) but whose PDF and markdown are already committed to git needs no re-intake: only the source field is stale. The discover script's `git status` cross-check excludes these, since they are tracked and committed.

### Supplementary data — a separate `=== N supplementary pending ===` block

`intake_discover.py` also scans `supplementary/` and prints a second table of
supplementary PDFs (`supplementary/{parent}-{name}.pdf`) that have no converted
markdown yet. These are **not** bib keys and **not** manifest entries — they're
auxiliary data (character matrices, appendices, plate sets, data tables) from a
paper already in `pdfs/`. See §8b for how to process them. If a row shows
`⚠ NO PARENT KEY`, the `{parent}-` prefix doesn't match any manifest key — STOP
and fix the filename (the parent paper must exist first).

## 2. Classify each PDF

For each candidate, decide which conversion path it takes. Get the year from the manifest entry's `citation.year` or `year`, and page count via `pypdfium2`. Check whether the cited `pages` field is much smaller than the PDF page count (whole-volume case).

### Path A — Marker first (modern, text-layer expected)

Default for `≥1900` papers. Marker handles them in 30-100s/PDF on Apple Silicon.

### Path B — Vision OCR direct (1800s and very early 1900s scans)

Pre-1900 PDFs are nearly always image-only scans of letterpress prints; marker's `--disable_ocr` mode produces empty stubs and even with OCR it misreads old typography. Skip marker entirely; set `conversion.vision_ocr_needed=True` upfront with `vision_ocr_reason="1800s scan; skip marker"`.

- `<1900` → always Path B.
- `1900–1919` → decide by text-layer quality, not year: BAMNH, RSC Transactions, Comptes Rendus and JSTOR-scanned plate monographs usually need Path B; USGS and Smithsonian products often have clean text layers and take Path A. Probe interior pages, not page 1 (see `docs/notes/conversion.md`, "1900-1919 papers").

### Path C — Vision OCR scope-limited (whole-volume PDF, article ≪ PDF)

The PDF is a whole-volume scan but the cited article is a few pages: a 7-page scan holding a 1-page article, or a 104-page supplement holding a 2-page appendix. OCR only the article's pages (§5), not the whole file.

**Always ask the user for the cited→PDF page mapping before running.** The frontmatter offset is rarely zero (cited pp. 39-40 can be PDF pp. 48-49). Don't assume.

### Path D — Replacement re-convert

User-listed PDFs that already have `acquisition` and prior markdown. Treat like Path A (or B if pre-1900) but with `--force` on marker. Update `acquisition` with `previous_source` preserved.

### Path V — Line-numbered manuscript → vision OCR, never marker

Accepted manuscripts, bioRxiv preprints, AGU/Elsevier review copies and
manuscript-style supplements print a line-number gutter. Marker keeps the
words but deletes whole lines (15 of 457 in one manuscript) and shifts table
cells against those numbers (a reference column slipping a row), and no QC
script notices. `intake_discover.py` detects the gutter from the text layer
and prints `V`; supplements show "line-numbered → vision OCR". Do not run
marker on these, not even "to see". Flag vision upfront and pass the gutter
in the prompt:

```bash
./.runtime/venv/bin/python scripts/intake_acquire.py KEY --vision-ocr "line-numbered manuscript"
./.runtime/venv/bin/python scripts/convert_with_vision.py --key KEY --extra-context \
  "Manuscript with a LINE-NUMBER column in the margin. Never transcribe the line numbers or treat them as a table column. Body text is prose; only genuine data tables are tables, one printed row per table row, every cell exactly as printed."
```

Then check tables row by row against the text layer (it is born-digital and
reliable here) and run `scripts/audit_vision_textlayer.py --key KEY`.
For a supplement, see §8b (vision path).

## 3. Set acquisitions atomically

Use the acquire script. It creates stub-orphan manifest entries for keys not yet in the manifest (`in_bib=False`, blank citation), preserves `previous_source` for already-acquired keys (audit trail across replacements + failed-fetch sentinel takeovers), and atomically rewrites `manifest.json`. For Path B/C/V keys you can flag vision-OCR upfront in the same call — that will skip marker entirely.

```bash
# Path A (marker first; default):
./.runtime/venv/bin/python scripts/intake_acquire.py KEY1 KEY2 ...
./.runtime/venv/bin/python scripts/intake_acquire.py --keys-file /tmp/intake_keys.txt

# Path B (pre-1900 scan; skip marker):
./.runtime/venv/bin/python scripts/intake_acquire.py KEY \
    --vision-ocr "1800s scan; skip marker"

# Replacement / book-chapter scan with no text layer:
./.runtime/venv/bin/python scripts/intake_acquire.py KEY \
    --vision-ocr "book chapter scan; no text layer (per user)"
```

`--source` (default `manual`) and `--url` set `acquisition.source` and `acquisition.url` when the PDF came from somewhere worth recording.

The script prints a "stub-orphans pending bib propagation" list at the end — those carry through to step 10 (list them in the commit message + final summary).

## 4. Run marker (Paths A and D)

Write Path-A/D keys into `/tmp/intake_keys.txt` and run marker:

```bash
cat > /tmp/intake_keys.txt <<EOF
key1
key2
...
EOF

./.runtime/venv/bin/python scripts/convert_with_marker.py \
  --workers 1 --force --keys-file /tmp/intake_keys.txt
```

Marker takes ~30-60s/PDF on Apple Silicon; budget accordingly. **`--workers 1` is mandatory** — workers≥2 hits an MPS contention bug. For long or image-heavy PDFs on a 16 GB machine add `--low-memory`.

**Embedded raster-image extraction is not part of intake**: the figures are rarely needed and cost several MB per paper. If you need the figures from a specific paper, run `/extract-images {key}` (`corpus pdf images --key {key}`) on demand; it rips them into `pdf_images/{key}/`.

### Manifest edits while marker runs

Scripts write the manifest under a lock and apply only the fields they changed, so another script or an inline edit can run while marker works. Make inline edits with `corpus.manifest.update(...)`, never a plain `json` read and write, which bypasses the lock (see `docs/notes/intake-manifest-keys.md`, "Concurrent manifest writes").

## 5. Scope-limited vision OCR (Path C)

With the user's PDF page range, OCR only those pages:

```bash
./.runtime/venv/bin/python scripts/convert_with_vision.py --key KEY --pages "48-49"
```

`--pages` takes a 1-indexed spec (`'1-24'`, `'1-3, 7'`) and needs exactly one `--key`. The markdown holds only those pages, joined with `"\n\n---\n\n"`; the manifest gets `tool=claude-vision`, the page count of the scope, and `SCOPE-LIMITED to PDF pages ...` in `conversion.notes`. The PDF stays whole. Responses are cached per PDF page (`.runtime/cache/vision/{key}/page-NNNN.json`), so a later full run cache-hits these pages.

`--pages` overwrites `markdown/{key}.md` with the selected pages only. Never use it to add pages to an existing conversion: `--pages 85-89` on an 84-page conversion leaves 5 pages.

To cut the PDF itself down to the article instead, use `/extract-pages` before converting.

## 6. Post-marker QC

After marker (Path A/D) finishes:

```bash
./.runtime/venv/bin/python scripts/scan_markdown_quality.py
./.runtime/venv/bin/python scripts/audit_languages.py
./.runtime/venv/bin/python scripts/intake_qc.py --keys-file /tmp/intake_keys.txt
```

`intake_qc.py` does the bytes-per-page sanity check + T&F cover-stub detection in one call. It implements the triage rules from `docs/notes/conversion.md` ("Stub markdown: wrong PDF or image-only body?"):

- `0` KB or `<200 B/page` over `>10` pages → `image_only` flag (image-only scan; set `vision_ocr_needed=True`).
- `<1000 B/page` with T&F cover-stub markers in the first 6 KB ("This article was downloaded by", "Informa Ltd", "Mortimer Street", "PLEASE SCROLL DOWN FOR ARTICLE") → `tf_cover_stub` flag.
- `<1000 B/page` but no T&F markers → `likely_stub` flag (eyeball the body before declaring ok). A genuinely short early-1900s paper (4 pages at about 1,000 B/page) reads like real text.
- Otherwise → `ok`.

The script exits non-zero (1) if any flagged rows; useful for scripted gates. For `image_only` / `tf_cover_stub` keys, set `vision_ocr_needed=True` (re-run `intake_acquire.py --vision-ocr`, or `scan_markdown_quality.py --apply` for the keys it flags) and rerun via §7.

For language-audit hits **on a key whose journal you know is English-published** (e.g. langdetect says 60% German on a paper from a Chinese journal), assume marker CID-garbage and route to vision OCR — translating gibberish produces hallucinations. Real CJK content routes to translation instead.

## 7. Vision OCR (Paths B, V + QC-flagged)

Run `convert_with_vision.py` — it reads `vision_ocr_needed=True` flags and processes those keys:

```bash
./.runtime/venv/bin/python scripts/convert_with_vision.py
```

Without `--key` it skips keys whose `conversion.tool` is already `claude-vision`, even when flagged. For a replacement re-OCR (a new PDF, or after `/extract-pages`) pass `--key {key} --force`: the cache key does not include page content, and `--force` re-OCRs the pages and replaces their cache entries. Check the "Vision-OCR candidates: N" count against the keys you expected.

**Then audit the vision output against the PDF's own text layer** whenever the
PDF has one (born-digital papers, Chinese/Japanese journals, line-numbered
manuscripts):

```bash
./.runtime/venv/bin/python scripts/audit_vision_textlayer.py --key KEY --show
```

Vision OCR writes fluent text and fails silently in two ways this catches:
skipped passages (a dated sentence dropped mid-page; whole pages can come back
empty) and plausible wrong characters. In Chinese/Japanese text the
substitutions change meaning and survive translation unseen (肱 humerus → 胫
tibia or 胱 bladder, 锄 vomer → 颧 jugal, 砾 gravel → 碎). On a **born-digital
CJK** layer, correct the substitutions from the layer BEFORE translating
(`scripts/correct_cjk_from_textlayer.py KEY`, review the numbered list, then
`--apply --skip N,...`; see `docs/notes/conversion.md`,
"A born-digital Chinese PDF's own text layer is a full control"); a layer
whose CJK agreement is UNUSABLE has a broken font map and is no control.
On Latin-script layers most "subs" are the layer's own broken accents
(vision is right) and most "drops" are text inside charts and maps; read the
drops, and treat an empty or near-empty page as a real loss.

If output reports `partial=N` for any key, individual pages were content-filter blocked: they appear in the markdown as `[VISION OCR FAILED FOR PAGE N: ...]` lines. Recover each page with `corpus convert recover` (`scripts/reocr_nooverlap.py`; see `docs/notes/conversion.md`, "Content-filter blocks"):

```bash
./.runtime/venv/bin/corpus convert recover --key {key} --page N [--page M ...]          # candidate + measurements
./.runtime/venv/bin/corpus convert recover --key {key} --page N [--page M ...] --apply  # write it (no new API calls)
```

It tries the intact page first, then cuts it only where there is no text (at a two-column page's gutter, and at blank rows), so no pieces overlap and nothing is stitched or duplicated. The first run saves a candidate and prints measurements only: word counts against the PDF's text layer, the longest repeated passage, and the headings in order. **Never print the recovered page text into the conversation**: the content filter applies to your own output too, and echoing a blocked page re-trips it. Read the measurements; a word count well off the text layer, a long repeat or headings out of order mean a bad cut. For a single-column page pass `--columns 1`; for a two-column page whose gutter is not detected, `--columns 2`; `--fresh` discards a saved candidate and OCRs again. `--apply` writes the page into the markdown and a cache entry, so a later `convert vision` run reuses it instead of re-blocking. Known limit: ink that crosses the gutter partway down a page (a figure edge, a stray mark) can still start a full-width band and swap a few lines between the columns; the headings check catches a large swap, not a small one.

## 8. Translate non-English bodies

For language-audit hits that are real non-English content (not CID garbage):

```bash
./.runtime/venv/bin/python scripts/translate_markdown.py --key {key}
```

Backs up to `markdown/{key}.original.md` and refuses if result is `<50%` original size. Both the translated and `.original.md` files get committed. For a bilingual paper whose English abstract makes language detection return English and skip the file, force the source language with `--src-lang` (e.g. `zh-cn`, `de`), one key per run.

## 8b. Supplementary data (`supplementary/` → `markdown/supplementary/`)

Supplementary files are auxiliary data from a paper already in `pdfs/` —
character matrices, appendices, plate sets, data tables. They live in
`supplementary/{parent}-{name}.pdf`, where `{parent}` is an existing citation
key and `{name}` is a short descriptive label. A hyphenated `{name}` (e.g.
`appendix-3`) is fine — the parent is resolved by **longest-prefix match**
against real manifest keys, so hyphenated parent keys and hyphenated names
both resolve unambiguously.

They are **not** manifest keys. Conversion records onto the PARENT entry:
`manifest[parent]["supplementary"][name] = {pdf_path, markdown_path, tool,
page_count, converted_at, notes}`. This keeps the manifest bib-keyed — no
orphan-list churn at `/refresh-bib`.

**Flow:**

1. **Discover** — `intake_discover.py` lists them under `=== N supplementary
   pending ===` with the parent auto-resolved. Confirm no `⚠ NO PARENT KEY` rows
   (a bad `{parent}-` prefix → fix the filename before converting).
2. **Classify** — same era/text-layer rules as §2. Most supplementary data is
   born-digital (tables/matrices) → **marker**. Image-only scans and
   line-numbered supplements → vision (4b).
3. **No acquire step.** Supplementary files aren't "acquired" (no fetch
   tier, no `acquisition` block) — they're dropped in `supplementary/`
   by hand. Skip §3.
4a. **Convert (marker)** — pass the `{parent}-{name}` stems + `--supplementary`:
    ```bash
    printf '%s\n' '{parent}-{name}' > /tmp/supp_keys.txt
    ./.runtime/venv/bin/python scripts/convert_with_marker.py \
      --workers 1 --force --supplementary --keys-file /tmp/supp_keys.txt
    ```
    Output lands in `markdown/supplementary/`; the parent sub-record is written.
4b. **Convert (vision)** — `convert_with_vision.py` has no `--supplementary`
    flag, so run a short inline script: render each page of
    `supplementary/{stem}.pdf` with `corpus.pdf.render.render_jpeg` (DPI 150),
    OCR it with `corpus.vision.ocr.ocr_one_page` using the prompt from
    `select_prompt` (`PROMPT_VERBATIM`, prompt_id `verbatim-v1`; add the gutter
    context from Path V for a line-numbered supplement) and a cache directory
    such as `.runtime/cache/vision/{stem}/`, join the pages with
    `"\n\n---\n\n"`, write `markdown/supplementary/{stem}.md`, and record the
    result on `manifest[parent]["supplementary"][name]` (`tool="claude-vision"`)
    through `corpus.manifest.update`, not a top-level conversion block.
5. **QC** — `intake_qc.py`/`scan_markdown_quality.py`/`audit_languages.py`
   iterate manifest keys, so they won't see supplementary stems — **eyeball the
   markdown by hand**. `strip_frontmatter.py --key {stem}` still works on the
   file if a cover slipped in (usually a no-op for supplementary).
6. **Index** — `indexer.py` walks `markdown/supplementary/` automatically and
   indexes each under `{parent}-{name}`, inheriting the parent's
   title/year/genera with a `[suppl: {name}]` title prefix. Supplementary never
   counts as missing (`corpus status`).
7. **Extract pages** — to scope-limit a large supplementary PDF first, use
   `/extract-pages {parent}-{name} "SPEC" --supplementary` (see that skill).
8. **Commit** — stage `supplementary/{stem}.pdf` (LFS),
   `markdown/supplementary/{stem}.md`, and `manifest.json`.
9. **Not worth converting** (map sheets, fold-out plates): mark the record
   `skipped: true` instead of converting; discovery then stops listing it. See
   `docs/notes/intake-manifest-keys.md`, "Citation-only keys".

## 9. Strip front-matter, then index + missing report

First strip journal/aggregator cover boilerplate from the batch's markdown.
Both marker **and** vision output can carry it — a full BioOne/JSTOR cover
page OCRs as body text, not a header — so run this after all conversion
paths (§4/§5/§7) and any translation (§8) have finished, on the batch keys:

```bash
# dry-run on just this batch's keys (prints the blocks it would cut)
./.runtime/venv/bin/python scripts/strip_frontmatter.py --key key1 --key key2
# apply
./.runtime/venv/bin/python scripts/strip_frontmatter.py --apply --key key1 --key key2
```

`strip_frontmatter.py` is a paragraph-level denylist bounded to the first
4,000 characters of each file. It removes:

- the T&F download stamp ("This article was downloaded by…", the
  Informa/Mortimer registration and terms blocks) and the T&F masthead prefix
  (journal-name heading + subtitle + "ISSN … Journal homepage: tandfonline.com/loi");
- the BioOne "…is a nonprofit…" cover;
- the JSTOR cover, including its fake "## REFERENCES" heading and
  `[%3B2](…)` URL fragments;
- the Elsevier accepted-manuscript cover (`# Accepted Manuscript`, `PII:`,
  `Reference:`, `To appear in:`, Received/Revised/Accepted Date), and the
  "ACCEPTED MANUSCRIPT" diagonal page watermark that marker glues to the start
  of every page's first body line, removed throughout the document, list items
  included;
- the BHL (Biodiversity Heritage Library) attribution page ("# Biodiversity
  Heritage Library" / "View This Item Online" / "Holding Institution" /
  "Copyright & Reuse" / closing blurb) that BHL exports prepend or append, as a
  head- or tail-anchored run gated on a strong marker, so a reference that
  merely cites a biodiversitylibrary.org URL is untouched;
- the Royal Society (royalsocietypublishing.org) cover sheet (scrambled title +
  authors + "Proc. R. Soc. B … first published online" + Supplementary data /
  References / Subject collections / Email alerting nav), as a head prefix cut
  through the "sign up in the box" block;
- the GSA (gsapubs.org) HighWire cover ("Email alerting services / Subscribe /
  Permission request / Notes") at the head or the tail (Geology and Memoirs
  downloads put it last), keeping the citation and doi lines before it; GSA
  "Downloaded from … gsapubs.org on DATE" page stamps and the AAPG Datapages
  "prepared for the exclusive use of NAME" stamp, throughout;
- the HAL deposit cover (HAL Id / "Submitted on" / the English and French
  archive blurbs), keeping the title, authors and "To cite this version" block;
- the New Mexico Geological Society (nmgs.nmt.edu) guidebook download cover
  ("Annual NMGS Fall Field Conference Guidebooks" / "Free Downloads" /
  "Copyright Information"), keeping the society link, title, authors, DOI and
  "in: <guidebook>" citation lines.

It **preserves** the real title, authors and citation lines (`Author(s):` /
`Source:` / `To cite this article:` / `Please cite this article as:`). It does
not handle the sibling HighWire covers from lyellcollection.org / QJGS. It's a
no-op on files with no boilerplate, and a safety guard skips any file that
would strip below 30% / 200 chars — inspect those by hand (a cover-page-only
stub belongs in vision-OCR/`tf_cover_stub` territory, §6, not here). Omit
`--key` to sweep the whole corpus.

### 9a. Text-layer repairs (one pass over the batch)

Marker passes through whatever the PDF's own text layer holds, and several
faults recur in fresh batches that no QC script flags: Elsevier's dash font
extracts en/em dashes as "e"/"d" (`CampanianePaleocene`, `1813e1836`), umlauts
arrive as "€" (`M€uller`), marker moves letters into reference-call links
(`flora[s14](...)`), superscript calls fuse onto words (`Rhaetian6`), and some
fonts drop symbol glyphs. `corpus repair all` runs every repair on each paper
in one pass (PDF opened once, markdown written once). Each repair keys on the
PDF, never on a regex over the markdown, and is idempotent:

```bash
# dry run: per-edit lines, then totals per repair; read them
./.runtime/venv/bin/corpus repair all --keys-file /tmp/intake_keys.txt
# apply
./.runtime/venv/bin/corpus repair all --keys-file /tmp/intake_keys.txt --apply
```

Read the dry run before applying. For the dash repair, ignore the
"bibliography page range" and "too short to match safely" counts and check
the "running prose (WORTH CHASING)" lines by eye; for fused calls, look at any
edit whose "call" is not a plausible reference number. Details: the
conversion notes, "Elsevier font AdvPS44A44B" and "PDF libraries".
`--only dash` (repeatable) runs a subset, `--quiet` prints totals only, and
each repair also runs on its own as
`corpus repair dash|symbols|accents|diaeresis|ref_links|fused_calls`.

Then the dropped-ligature repair, for born-digital papers whose markdown shows
the tell (`frst`, `diferent`, `identifed`, `lef`, sentence-initial `Te`):

```bash
grep -lE '\b(frst|signifcant|identifed|diferent|defned|Tis|Tey)\b' markdown/{key}.md   # per batch key
./.runtime/venv/bin/corpus clean ligatures --key {key}            # dry run: read the list
./.runtime/venv/bin/corpus clean ligatures --key {key} --apply    # keeps {key}.md.ligature.bak
rm markdown/{key}.md.ligature.bak                                 # once the result reads right
```

It takes its word frequencies from the corpus once it has 500 clean papers and
from the `wordfreq` package before that (`--lexicon` picks one). Read the
dry-run list: a name or a foreign word "repaired" into English is the failure
to look for.

Then index, and check the counts:

```bash
./.runtime/venv/bin/python scripts/indexer.py
./.runtime/venv/bin/corpus status        # held / converted / indexed / missing
```

Indexer is mtime-based (incremental). `corpus status` should show the batch as converted and indexed, with nothing under "PDF, no markdown".

## 10. Commit + push

Only where the content folders are tracked. A fresh clone of the public corpus repository keeps `pdfs/`, `markdown/` and the other content folders out of git (each holds a `.gitignore` of `*`), and that is deliberate: papers are never committed there. In such a checkout there is nothing to commit; skip this step. A private archive that commits its content removes those `.gitignore` files once.

One commit covering the batch.

Stage `manifest.json` plus all changed/new `markdown/*.md` and `pdfs/*.pdf` (LFS) of the batch, by explicit path. `pdf_images/` and `.runtime/` are gitignored. Don't bundle unrelated working-tree changes: surface them separately and ask before staging.

If the batch included orphan keys (`in_bib=False`), call them out in the commit message body (e.g. "Stub-orphans pending bib propagation: {key1}, {key2}") and in the final user-facing summary. They're processed and indexed; the only follow-up is upstream bib entries.

Push to the archive's own remote, never to the public repository.

## Quick decision tree

```
PDF in pdfs/ but not in manifest acquired set, OR modified/untracked in git status
├── Not in manifest at all → STUB-ORPHAN INTAKE
│   ├── Create stub entry: in_bib=False, blank citation
│   ├── Classify by year/page count same as below (Path A/B/C/V)
│   └── Flag in final summary so user lands bib entry upstream
├── In manifest, no PDF previously → NEW INTAKE
│   ├── year < 1900     → Path B (vision direct)
│   ├── 1900 ≤ year     → Path A (marker)
│   ├── text layer has a line-number gutter → Path V (vision + gutter context; never marker)
│   └── PDF pages ≫ cited pages → Path C (ask user for scope; convert_with_vision.py --pages)
└── In manifest, prior conversion exists (or PDF modified in git) → REPLACEMENT
    └── Path D (marker --force; for vision keys
        convert_with_vision.py --key {key} --force)

After conversion (Path A/D):
├── 0-byte md or <200 B/page over >10 pages → set vision_ocr_needed; rerun via §7
├── Body is cover-page boilerplate (T&F pattern) → set vision_ocr_needed; rerun via §7
├── audit_languages flag on English journal → marker CID-garbage; vision OCR
└── audit_languages flag on actual non-English → translate via §8
```

## Cost reference

- Marker (text-layer): free, ~30-100 s/PDF.
- Vision OCR (Sonnet 5.5, $2/$10 per MTok): ~$0.01-0.02/page. The script prints each run's cost.
- `corpus convert recover` (reocr_nooverlap.py): ~$0.05-0.15 per blocked page, from the number of pieces it takes.
- `translate_markdown.py` (Sonnet 5.5): ~$0.10-0.20 per typical paper (a 13-page German paper is about $0.16).

When the batch includes large vision OCR (>50 pages) or expensive recovery, surface a cost estimate to the user before kicking off.
