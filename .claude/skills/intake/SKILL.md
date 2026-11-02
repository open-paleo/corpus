---
name: intake
description: Discover new/updated PDFs in pdfs/, classify by era + scope, run marker/vision/translation/QC, index, and commit
---

End-to-end intake for new or replaced PDFs sitting in `pdfs/`. Builds on the `Scenario A-bulk` workflow in CLAUDE.md, plus the era-classification heuristics, scope-limit pattern, and stub-triage rules from feedback memory.

## 1. Discover what changed

Run the discovery script. It does NFC-normalized `disk - acquired` plus the `git status pdfs/` cross-check (replacements + already-acquired-uncommitted) in one pass, and prints a Path A / B / C / D classification hint per pending key with year + page-count metadata pulled from the manifest:

```bash
./.runtime/venv/bin/python scripts/intake_discover.py
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

### Caveat: legacy failed-fetch sentinels on keys already processed

Keys whose `acquisition.source` is still a failed-fetch sentinel (`unpaywall_no_oa`, `oa_direct_landing_failed`, ...; the full list is `corpus.manifest.FAILED_SUFFIXES`) but whose PDFs and markdown are already committed to git (processed in earlier sessions; only the source field is stale) don't need re-intake. The discover script's `git status` cross-check naturally excludes them — they're tracked + committed, so they don't show as untracked or modified.

### Supplementary data — a separate `=== N supplementary pending ===` block

`intake_discover.py` also scans `supplementary/` and prints a second table of
supplementary PDFs (`supplementary/{parent}-{name}.pdf`) that have no converted
markdown yet. These are **not** bib keys and **not** manifest entries — they're
auxiliary data (character matrices, appendices, plate sets, data tables) from a
paper already in `pdfs/`. See the dedicated **"Supplementary data"** section
below for how to process them. If a row shows `⚠ NO PARENT KEY`, the
`{parent}-` prefix doesn't match any manifest key — STOP and fix the filename
(the parent paper must exist first).

## 2. Classify each PDF

For each candidate, decide which conversion path it takes. Get the year from the manifest entry's `citation.year` or `year`, and page count via `pypdfium2`. Check whether the cited `pages` field is much smaller than the PDF page count (whole-volume case).

### Path A — Marker first (modern, text-layer expected)

Default for `≥1900` papers. Marker handles them in 30-100s/PDF on Apple Silicon.

### Path B — Vision OCR direct (1800s and very early 1900s scans)

Pre-1900 PDFs are nearly always image-only scans of letterpress prints; marker's `--disable_ocr` mode produces empty stubs and even with OCR it misreads old typography. Skip marker entirely; set `conversion.vision_ocr_needed=True` upfront with `vision_ocr_reason="1800s scan; skip marker"`.

- `<1900` → always Path B.
- `1900–1919` → decide by text-layer quality, not year: BAMNH, RSC Transactions, Comptes Rendus and JSTOR-scanned plate monographs usually need Path B; USGS and Smithsonian products often have clean text layers and take Path A. Probe interior pages, not page 1 (see `docs/notes/conversion.md`, "1900-1919 papers").

### Path C — Vision OCR scope-limited (whole-volume PDF, article ≪ PDF)

See `docs/notes/conversion.md` ("Scope-limited vision OCR for whole-volume PDFs"). When a PDF is a whole-volume scan but the cited article is small (e.g. `marsh1885` 7-page BHL stitch → 1-page article; `hitchcock1865` 104-page Supplement → 2-page appendix at pp. 39-40), running `convert_with_vision.py` over all pages is wasteful and expensive ($0.05/page).

**Always ask the user for the cited→PDF page mapping before running.** The frontmatter offset is rarely zero; e.g. cited p. 39-40 = PDF p. 48-49 for hitchcock1865. Don't assume.

Use the inline one-shot pattern (see §5).

### Path D — Replacement re-convert

User-listed PDFs that already have `acquisition` and prior markdown. Treat like Path A (or B if pre-1900) but with `--force` on marker. Update `acquisition` with `previous_source` preserved.

### Path V — Line-numbered manuscript → vision OCR, never marker

Accepted manuscripts, bioRxiv preprints, AGU/Elsevier review copies and
manuscript-style supplements print a line-number gutter. Marker keeps the
words but deletes whole lines and shifts table cells against those numbers
(`longrich2024c`: 15 of 457 lines gone; `funston2020a-supplemental`: the
reference column of the age table slipped a row for three taxa), and no QC
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

Use the acquire script. It creates stub-orphan manifest entries for keys not yet in the manifest (`in_bib=False`, blank citation), preserves `previous_source` for already-acquired keys (audit trail across replacements + failed-fetch sentinel takeovers), and atomically rewrites `manifest.json`. For Path B/C keys you can flag vision-OCR upfront in the same call — that will skip marker entirely.

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

Marker takes ~30-60s/PDF on Apple Silicon; budget accordingly. **`--workers 1` is mandatory** — workers≥2 hits an MPS contention bug.

**Embedded raster-image extraction is not part of intake**: the figures are rarely needed and cost several MB per paper. If you need the figures from a specific paper, run `/extract-images {key}` (`corpus pdf images --key {key}`) on demand; it rips them into `pdf_images/{key}/`.

### Manifest edits while marker runs

Scripts write the manifest under a lock and apply only the fields they changed, so another script or an inline edit can run while marker works. Make inline edits with `corpus.manifest.update(...)`, never a plain `json` read and write, which bypasses the lock (see `docs/notes/intake-manifest-keys.md`, "Concurrent manifest writes").

## 5. Inline OCR for Path C (scope-limited whole-volume)

For each scope-limited key, render only the target pages and OCR them inline, mirroring `convert_with_vision.py`'s prompt + cache layout so future script runs cache-hit and don't re-OCR the rest of the PDF.

Use the standard `PROMPT_VERBATIM` text (copy from `scripts/convert_with_vision.py`, prompt_id = `verbatim-v1`). Render at DPI 150 with JPEG q=82; if a page exceeds Anthropic's 5 MB image cap (b64-overhead → ~3.7 MB raw) drop to DPI 120. BHL JP2 sources tend to need DPI 120.

Cache responses at `.runtime/cache/vision/{key}/page-NNNN.json` with the same schema the script writes. Join page markdowns with `"\n\n---\n\n"` and write `markdown/{key}.md`. Update `manifest.conversion`: `tool=claude-vision`, `page_count=N (limited)`, with notes documenting the scope.

Real precedents: `marsh1885` (PDF p1, art. p169), `hitchcock1865` (PDF p48-49, art. p39-40).

## 6. Post-marker QC

After marker (Path A/D) finishes:

```bash
./.runtime/venv/bin/python scripts/scan_markdown_quality.py
./.runtime/venv/bin/python scripts/audit_languages.py
./.runtime/venv/bin/python scripts/intake_qc.py --keys-file /tmp/intake_keys.txt
```

`intake_qc.py` does the bytes-per-page sanity check + T&F cover-stub detection in one call (replacing the inline B/page table). It implements the triage rules from `docs/notes/conversion.md` ("Stub markdown: wrong PDF or image-only body?"):

- `0` KB or `<200 B/page` over `>10` pages → `image_only` flag (image-only scan; set `vision_ocr_needed=True`).
- `<1000 B/page` with T&F cover-stub markers in the first 6 KB ("This article was downloaded by", "Informa Ltd", "Mortimer Street", "PLEASE SCROLL DOWN FOR ARTICLE") → `tf_cover_stub` flag.
- `<1000 B/page` but no T&F markers → `likely_stub` flag (eyeball the body before declaring ok). A genuinely short 1900s-era paper (e.g., `lucas1901`, 4 pages, 1024 B/page) reads like real text.
- Otherwise → `ok`.

The script exits non-zero (1) if any flagged rows; useful for scripted gates. For `image_only` / `tf_cover_stub` keys, set `vision_ocr_needed=True` (re-run `intake_acquire.py --vision-ocr` or edit manifest manually) and rerun via §7.

For language-audit hits **on a key whose journal you know is English-published** (e.g. langdetect says 60% German on a paper from a Chinese journal), assume marker CID-garbage and route to vision OCR — translating gibberish produces hallucinations. Real CJK content (e.g. `zhu1994` from Sichuan Cultural Relics) routes to translation instead.

## 7. Vision OCR (Paths B + C-by-script + QC-flagged)

Run `convert_with_vision.py` — it reads `vision_ocr_needed=True` flags and processes those keys:

```bash
./.runtime/venv/bin/python scripts/convert_with_vision.py
```

**Then audit the vision output against the PDF's own text layer** whenever the
PDF has one (born-digital papers, Chinese/Japanese journals, line-numbered
manuscripts):

```bash
./.runtime/venv/bin/python scripts/audit_vision_textlayer.py --key KEY --show
```

Vision OCR writes fluent text and fails silently in two ways this catches:
skipped passages (`pang2024a` lost its dated fission-track sentence; whole
pages can come back empty, `sotonúñez2026a` pp. 29-30) and plausible wrong
characters. In Chinese/Japanese text the substitutions change meaning and
survive translation unseen (肱 humerus → 胫 tibia or 胱 bladder, 锄 vomer → 颧
jugal, 砾 gravel → 碎). On a **born-digital CJK** layer, correct the
substitutions from the layer BEFORE translating
(`scripts/correct_cjk_from_textlayer.py KEY`, review the numbered list, then
`--apply --skip N,...`; see `docs/notes/conversion.md`,
"A born-digital Chinese PDF's own text layer is a full control"); a layer
whose CJK agreement is UNUSABLE has a broken font map and is no control.
On Latin-script layers most "subs" are the layer's own broken accents
(vision is right) and most "drops" are text inside charts and maps; read the
drops, and treat an empty or near-empty page as a real loss.

If output reports `partial=N` for any key, individual pages were content-filter blocked. Run the following (see also `docs/notes/conversion.md`, "Content-filter blocks"):

```bash
./.runtime/venv/bin/python scripts/recover_blocked_pages.py --key {key}
```

Geometric split + fuzzy stitch. After recovery, **eyeball the recovered pages for boundary duplication** — when a paragraph lands fully inside the overlap region, both halves transcribe it and the stitch can leave duplicates. Use the iterative-longest-substring deduplication trick:

```python
# pseudocode — see commit b97f91a for the real implementation
def dedupe(md, min_len=80):
    while (r := find_longest_repeat(md, min_len)):
        a, b, L = r            # a < b
        md = md[:b] + md[b+L:] # remove second copy
    return md
```

Apply to both the cache JSON's `markdown` field (so future script runs cache-hit on clean content) and the rebuilt `markdown/{key}.md` (joined from cleaned cache via `"\n\n---\n\n"` separator).

## 8. Translate non-English bodies

For language-audit hits that are real non-English content (not CID garbage):

```bash
./.runtime/venv/bin/python scripts/translate_markdown.py --key {key}
```

Backs up to `markdown/{key}.original.md` and refuses if result is `<50%` original size. Both the translated and `.original.md` files get committed.

## 8b. Supplementary data (`supplementary/` → `markdown/supplementary/`)

Supplementary files are auxiliary data from a paper already in `pdfs/` —
character matrices, appendices, plate sets, data tables. They live in
`supplementary/{parent}-{name}.pdf`, where `{parent}` is an existing citation
key and `{name}` is a short descriptive label. A hyphenated `{name}` (e.g.
`appendix-3`) is fine — the parent is resolved by **longest-prefix match**
against real manifest keys, so hyphenated parent keys
(`prieto-márquez2010c-matrix`) and hyphenated names both resolve unambiguously.

They are **not** manifest keys. Conversion records onto the PARENT entry:
`manifest[parent]["supplementary"][name] = {pdf_path, markdown_path, tool,
page_count, converted_at, notes}`. This keeps the manifest bib-keyed — no
orphan-list churn at `/refresh-bib`.

**Flow:**

1. **Discover** — `intake_discover.py` lists them under `=== N supplementary
   pending ===` with the parent auto-resolved. Confirm no `⚠ NO PARENT KEY` rows
   (a bad `{parent}-` prefix → fix the filename before converting).
2. **Classify** — same era/text-layer rules as §2. Most supplementary data is
   born-digital (tables/matrices) → **marker**. Image-only scans → vision (4b).
3. **No acquire step.** Supplementary files aren't "acquired" (no fetch
   tier, no `acquisition` block) — they're dropped in `supplementary/`
   by hand. Skip §3.
4a. **Convert (marker)** — pass the `{parent}-{name}` stems + `--supplementary`:
    ```bash
    printf '%s\n' 'carrano2012a-appendix-3' > /tmp/supp_keys.txt
    ./.runtime/venv/bin/python scripts/convert_with_marker.py \
      --workers 1 --force --supplementary --keys-file /tmp/supp_keys.txt
    ```
    Output lands in `markdown/supplementary/`; the parent sub-record is written.
4b. **Convert (vision, image-only scans)** — there is no `--supplementary` flag
    on `convert_with_vision.py`. Use the inline scope-limited pattern from §5,
    but render from `supplementary/{stem}.pdf`, write
    `markdown/supplementary/{stem}.md`, and record the result on
    `manifest[parent]["supplementary"][name]` (`tool="claude-vision"`) rather
    than a top-level conversion block.
5. **QC** — `intake_qc.py`/`scan_markdown_quality.py`/`audit_languages.py`
   iterate manifest keys, so they won't see supplementary stems — **eyeball the
   markdown by hand**. `strip_frontmatter.py --key {stem}` still works on the
   file if a cover slipped in (usually a no-op for supplementary).
6. **Index** — `indexer.py` walks `markdown/supplementary/` automatically and
   indexes each under `{parent}-{name}`, inheriting the parent's
   title/year/genera with a `[suppl: {name}]` title prefix. Supplementary never
   counts as missing (`corpus status`, `MISSING_PAPERS.md`).
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
4 KB of each file — it removes the T&F "This article was downloaded by…" +
Informa/Mortimer registration + terms blocks (old download-stamp) and the
newer T&F "tandfonline masthead" prefix (journal-name heading + subtitle +
"ISSN … Journal homepage: tandfonline.com/loi"), the BioOne "…is a nonprofit…"
cover, the JSTOR cover (including its fake "## REFERENCES" heading and
`[%3B2](…)` URL fragments), and the Elsevier accepted-manuscript cover
(`# Accepted Manuscript`, `PII:`, `Reference:`, `To appear in:`,
Received/Revised/Accepted Date) — while **preserving** the real title,
authors, and citation lines (`Author(s):` / `Source:` / `To cite this
article:` / `Please cite this article as:`). For Elsevier accepted
manuscripts it also strips the recurring "ACCEPTED MANUSCRIPT" diagonal
page watermark that marker glues to the start of every page's first body
line — this one is removed globally (not just the head) since it recurs
throughout the document, list items included. It also removes the BHL
(Biodiversity Heritage Library) attribution page — "# Biodiversity Heritage
Library" / "View This Item Online" / "Holding Institution" / "Copyright &
Reuse" / closing blurb — that BHL exports prepend or append, as a head- or
tail-anchored run gated on a strong marker (so a reference that merely cites
a biodiversitylibrary.org URL is untouched). BHL scans are common intake
sources, so this fires often. It also removes the Royal Society
(royalsocietypublishing.org) cover sheet — scrambled title + authors +
"Proc. R. Soc. B … first published online" + Supplementary data / References /
Subject collections / Email alerting nav — as a head prefix cut through the
"sign up in the box" block. The GSA (gsapubs.org) HighWire cover — "Email
alerting services / Subscribe / Permission request / Notes" — is removed at
the head OR the tail (Geology and Memoirs downloads put it last), keeping the
citation and doi lines before it; GSA "Downloaded from … gsapubs.org on DATE"
page stamps and the AAPG Datapages personalised "prepared for the exclusive
use of NAME" stamp are removed globally. The HAL deposit cover (HAL Id /
"Submitted on" / the English and French archive blurbs) is removed, keeping
the title, authors and "To cite this version" block. The New Mexico Geological Society (nmgs.nmt.edu) guidebook download
cover ("Annual NMGS Fall Field Conference Guidebooks" / "Free Downloads" /
"Copyright Information") is removed, keeping the society link, title, authors,
DOI and "in: <guidebook>" citation lines. (Not yet handled: the sibling HighWire
covers from lyellcollection.org / QJGS.)
It's a no-op on files with no boilerplate, and a safety guard skips any file
that would strip below 30% / 200 chars — inspect those by hand (a
cover-page-only stub belongs in vision-OCR/`tf_cover_stub` territory, §6,
not here). Omit `--key` to sweep the whole corpus (the standalone cleanup
that produced the initial 176-file pass).

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
`--only dash` (repeatable) runs a subset; the single repairs are also
`corpus repair dash|symbols|accents|diaeresis|ref_links|fused_calls`, and
the old `scripts/repair_*.py` paths still work.

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

Indexer is mtime-based (incremental). `corpus status` should show the batch as converted and indexed, with nothing under "PDF, no markdown". If this checkout has `scripts/generate_missing_report.py` (a private archive's missing-papers report), also run it: it rewrites `MISSING_PAPERS.md` and round-trips any `(Closed access)` annotations into the manifest.

## 10. Commit + push

Only where the content folders are tracked. A fresh clone of the public corpus repository keeps `pdfs/`, `markdown/` and the other content folders out of git (each holds a `.gitignore` of `*`), and that is deliberate: papers are never committed there. In such a checkout there is nothing to commit; skip this step. A private archive that commits its content removes those `.gitignore` files once.

One commit covering the batch.

Stage `manifest.json`, `MISSING_PAPERS.md` (if present), optionally `TRANSLATION_AUDIT.md`, plus all changed/new `markdown/*.md`, `pdfs/*.pdf` (LFS). `pdf_images/` and `.runtime/cache/vision/` are gitignored. Don't bundle unrelated working-tree changes — the user often has pending `MISSING_PAPERS.md` annotation edits or `markdown/*.md` cleanups that aren't part of *your* intake; surface them separately and ask before staging.

If the batch included orphan keys (`in_bib=False`), call them out in the commit message body (e.g. "Stub-orphans pending bib propagation: osmolska1996, slowiak2024") and in the final user-facing summary. They're processed and indexed; the only follow-up is upstream bib entries.

Push to `origin/main` of the private archive, never to the public repository.

## Quick decision tree

```
PDF in pdfs/ but not in manifest acquired set, OR modified/untracked in git status
├── Not in manifest at all → STUB-ORPHAN INTAKE
│   ├── Create stub entry: in_bib=False, blank citation
│   ├── Classify by year/page count same as below (Path A/B/C)
│   └── Flag in final summary so user lands bib entry upstream
├── In manifest, no PDF previously → NEW INTAKE
│   ├── year < 1900     → Path B (vision direct)
│   ├── 1900 ≤ year     → Path A (marker)
│   ├── text layer has a line-number gutter → Path V (vision + gutter context; never marker)
│   └── PDF pages ≫ cited pages → Path C (ask user for scope; inline OCR)
└── In manifest, prior conversion exists (or PDF modified in git) → REPLACEMENT
    └── Path D (marker --force; for vision keys
        clear .runtime/cache/vision/{key}/ first and use --key bypass)

After conversion (Path A/D):
├── 0-byte md or <200 B/page over >10 pages → set vision_ocr_needed; rerun via §7
├── Body is cover-page boilerplate (T&F pattern) → set vision_ocr_needed; rerun via §7
├── audit_languages flag on English journal → marker CID-garbage; vision OCR
└── audit_languages flag on actual non-English → translate via §8
```

## Cost reference

- Marker (text-layer): free, ~30-100 s/PDF.
- Vision OCR (Sonnet 5.5, $2/$10 per MTok): ~$0.01-0.02/page. Older session totals were logged at $3/$15 and overstate spend by ~1.5x: 13-paper batch with 3 vision keys + 2 partials = $0.71; 4 old papers (40 pages) = $0.52.
- `recover_blocked_pages.py`: ~$0.05-0.15 per blocked page (geometric split).
- `translate_markdown.py` (Sonnet 5.5): ~$0.10-0.20 per typical paper (furrer2025z, 13 pp German: $0.16).

When the batch includes large vision OCR (>50 pages) or expensive recovery, surface a cost estimate to the user before kicking off.
