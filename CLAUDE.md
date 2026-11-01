# Open Paleo corpus: Claude operating notes

This checkout is a **local archive** of the papers cited by the Open Paleo
data repository (https://github.com/open-paleo/data): PDFs in `pdfs/`, their
Markdown in `markdown/`, and an SQLite full-text index over the Markdown.
The repository itself ships no papers; whatever is in the content folders
was put there by this machine's user and stays here.

## Orientation

- **Source of truth for what's done:** `manifest.json`, one entry per bib
  key. Each entry tracks `acquisition` (where the PDF came from) and
  `conversion` (Markdown status). `corpus status` summarises it from the
  files on disk; `corpus status --missing` lists what is not held yet.
- **Scope:** every key in the bib. Which genera and clades cite a key
  (`genera[]`, `clades[]`) is a label for searching and filtering, not a filter.
- **The bib** comes from `CORPUS_BIB_SOURCE` (`corpus.bib`): `sibling`
  (`../data/dist/references.bib`, or `$OPEN_PALEO_DATA`), `bundled`
  (`bib/references.bib`), `remote` (downloaded from the data repository on
  GitHub), or `auto` (the first of those that exists, else remote).
- **Credentials:** `.env` holds `UNPAYWALL_EMAIL` and `ANTHROPIC_API_KEY`.
  Mode 0600. Never commit, never log, never print it.
- **Never commit content.** `manifest.json` is gitignored, and the content
  folders each hold a `.gitignore` of `*`. Don't remove those files, don't `git add -f` anything under them, and
  never push papers, page images or converted text to the public repository.
- **What other repositories rely on** is in `CORPUS_INTERFACE.md`. Treat a
  change to anything listed there as breaking.

## Project notes (`docs/notes/`)

Lessons and settled answers from past sessions. Start with
`docs/notes/README.md`, which says which file to read for which task. Always
read `working-agreements.md`. Before converting or repairing a paper, read
`conversion.md`. Before changing code, read `code.md`. When a session learns
something durable (a gotcha, a decision), add it to the matching file.

## Setup

```bash
./scripts/bootstrap.sh --hooks      # venv in .runtime/venv, .env from .env.example, index, hooks
./.runtime/venv/bin/corpus manifest refresh    # fill manifest.json from the bib (safe to re-run)
```

The venv lives at `.runtime/venv/`; run scripts as
`./.runtime/venv/bin/python scripts/...` from the repository root.
`./.runtime/venv/bin/corpus` is the single entry point: `corpus` alone lists
the commands, and arguments pass through to the script unchanged.

The shared code is the `corpus` package (`src/corpus/`: config, io, keys,
manifest, bib, status, pdf, vision, fetch, repair, md, words); `scripts/` are
the command-line entry points.

## Storage layout

```
pdfs/{key}.pdf                      PDFs (fetched, or dropped in by the user)
supplementary/{key}-{name}.pdf      supplementary files of a paper in pdfs/
markdown/{key}.md                   Markdown (marker / claude-vision)
markdown/{key}.original.md          original language, when {key}.md is a translation
markdown/supplementary/{key}-{name}.md
source_images/{key}/                page photos or scans to stitch into a PDF
index/papers.db                     FTS5 index (scripts/indexer.py)
condensed/                          written by the data repository's agents
manifest.json                       citation_key -> metadata (locked, atomic writes)
.runtime/                           venv, caches (vision OCR per page), logs
```

Supplementary files are not manifest keys: they are recorded on the parent's
entry as `manifest[key]["supplementary"][name]`, the parent found by
longest-prefix match of the stem against manifest keys.

`manifest.json` writes are atomic and locked (`corpus.manifest.commit` /
`update` re-read the file under an flock and apply only the fields the
caller changed), so scripts can run at the same time. Inline edits go
through `corpus.manifest.update()`. Scripts are resume-safe.

## Pipeline

| Command | Script | Step |
|---|---|---|
| `corpus manifest refresh` | `refresh_manifest.py` | Merge the bib into the manifest; preserves acquisition and conversion |
| `corpus manifest refresh-dois` | `refresh_dois.py` | DOI-only refresh |
| | `bootstrap.py` | Rebuild the manifest from scratch (**clobbers state**; fresh checkout only) |
| `corpus status` | | Held / converted / indexed / missing counts; `--missing` lists keys |
| `corpus fetch unpaywall` | `fetch_unpaywall.py` | Tier 1: open-access PDFs by DOI via Unpaywall |
| `corpus fetch unpaywall-by-doi` | `fetch_unpaywall_by_doi.py` | Same, for key/DOI lists not yet in the manifest |
| `corpus fetch pmc` | `fetch_pmc_oa.py` | Tier 2: PMC Open Access subset by DOI (Europe PMC lookup, NCBI's public bucket) |
| `corpus fetch oa-direct` | `fetch_oa_direct.py` | Tier 3: DOI landing page → `citation_pdf_url` (`--browser-ua` opt-in) |
| `corpus intake discover` | `intake_discover.py` | What is pending in `pdfs/`, with a path hint per key |
| `corpus intake acquire` | `intake_acquire.py` | Record dropped PDFs as acquired (`--vision-ocr REASON` to skip marker) |
| `corpus intake qc` | `intake_qc.py` | Bytes-per-page triage of a converted batch |
| `corpus pdf extract-pages` | `extract_pdf_pages.py` | Cut a whole-volume PDF down to the article, in place |
| `corpus pdf append-pages` | `append_pdf_pages.py` | Append pages from a source PDF |
| `corpus pdf compress` | `compress_pdf.py` | Shrink an oversized scan (**drops the text layer**) |
| `corpus pdf stitch` | `stitch_source_images.py` | `source_images/{key}/` → `pdfs/{key}.pdf` |
| `corpus pdf photocopier` | `photocopier_stitch.py` | Same, flat-field-normalising phone photos of a volume |
| `corpus convert marker` | `convert_with_marker.py` | PDF → Markdown from the text layer (`--workers 1`) |
| `corpus convert vision` | `convert_with_vision.py` | PDF → Markdown via Claude vision OCR (per-page cache) |
| `corpus convert recover` | `recover_blocked_pages.py` | Re-OCR pages the content filter blocked, by halves |
| | `dedupe_recovered_pages.py` | Remove boundary duplication left by recovery |
| `corpus convert translate` | `translate_markdown.py` | Translate non-English markdown (keeps `.original.md`) |
| | `correct_cjk_from_textlayer.py` | Correct vision-OCR'd Chinese/Japanese from a born-digital text layer |
| `corpus repair all` | `corpus.repair` | All text-layer repairs in one pass (dry run unless `--apply`) |
| `corpus clean frontmatter` | `strip_frontmatter.py` | Publisher cover boilerplate |
| `corpus clean running-heads` | `strip_running_heads.py` | Repeated page heads and feet (**review the list**) |
| `corpus clean despace` | `despace_ocr.py` | Rejoin OCR word splits (removes spaces only) |
| `corpus clean ligatures` | `fix_dropped_ligatures.py` | Restore f-/Th-ligatures the text layer dropped (`diferent`, `Te`); word frequencies from the corpus once it has 500 clean papers, from `wordfreq` before that |
| | `untable_gutter_manuscript.py`, `strip_pdf_gutter_numbers.py` | Accepted-manuscript line-number gutters |
| | `compact_padded_tables.py` | Whitespace-padded table cells |
| `corpus qc scan` | `scan_markdown_quality.py` | PUA garbage, CJK, image-only stubs; `--apply` flags vision OCR |
| `corpus qc languages` | `audit_languages.py` | Non-English bodies (`TRANSLATION_AUDIT.md`) |
| `corpus qc spillover` | `scan_spillover.py` | Markdown holding a neighbouring article |
| `corpus qc textlayer` | `audit_vision_textlayer.py` | Vision output against the PDF's own text layer |
| `corpus index` | `indexer.py` | Build/refresh `index/papers.db` (incremental) |
| `corpus search` | `search.py` | Query the index |

## "I just added a PDF — now what?"

**Run `/intake`.** It discovers the new PDFs, classifies each (marker for
born-digital PDFs from 1900 on, vision OCR for scans and pre-1900 papers,
scope-limited vision for a whole volume, vision with gutter context for
line-numbered manuscripts), converts, runs QC and the repairs, and indexes.
The skill encodes the heuristics; the steps below are what it runs.

```bash
# 1. Save the file under its exact citation key (from `corpus status --missing`).
cp ~/Downloads/paper.pdf pdfs/cope1872a.pdf
# 2. Record it (creates a stub entry, in_bib=False, if the key is not in the bib yet).
./.runtime/venv/bin/corpus intake acquire cope1872a
#    A scan with no usable text layer: skip marker.
./.runtime/venv/bin/corpus intake acquire cope1872a --vision-ocr "1800s scan; skip marker"
# 3. Convert.
./.runtime/venv/bin/corpus convert marker --workers 1 --key cope1872a
./.runtime/venv/bin/corpus convert vision --key cope1872a    # for flagged keys
# 4. QC, repairs, cleanup.
./.runtime/venv/bin/corpus intake qc cope1872a
./.runtime/venv/bin/corpus qc scan
./.runtime/venv/bin/corpus repair all --key cope1872a            # read, then --apply
./.runtime/venv/bin/corpus clean ligatures --key cope1872a       # read, then --apply
./.runtime/venv/bin/corpus clean frontmatter --key cope1872a     # read, then --apply
# 5. Index and check.
./.runtime/venv/bin/corpus index
./.runtime/venv/bin/corpus status
```

For a batch, put the keys in a file and pass `--keys-file` (marker then
loads its models once). `--workers 1` is required for marker on Apple
silicon: two workers contend for the GPU and corrupt each other's output.

Vision OCR costs money (about $0.01–0.02 a page). **Quote the cost before
any run over about $5** and wait for the user. Responses are cached per page
in `.runtime/cache/vision/`, so a re-run is free.

## Conversion failure modes → which tool

Marker passes through whatever is in the PDF's **own text layer**, so check
the layer before blaming marker:

```bash
./.runtime/venv/bin/python -c "
import pypdfium2 as p; d=p.PdfDocument('pdfs/KEY.pdf')
print(''.join(d[i].get_textpage().get_text_bounded() for i in range(len(d)))[:600])"
```

| Symptom in the markdown | Cause | Fix |
|---|---|---|
| `numerou s`, `th e` | scan OCR word splits | `despace_ocr.py` |
| `Cr~tac~ Inf~rieur`, no accents at all in a French paper | scan OCR flattened accents | vision re-OCR |
| `Ornlthlschia`, `Ho~otype` | scan OCR letter misreads | vision re-OCR |
| `CampanianePaleocene`, `M€uller`, `Rhaetian6` | font faults in the text layer | `corpus repair all` |
| `diferent`, `frst`, `lef`, `Te fossil` | dropped f-/Th-ligatures (Springer Nature, Elsevier) | `corpus clean ligatures --key K` |
| `AnewprosauropoddinosaurfromLowerJurassic` | fullwidth Latin in a Chinese PDF | vision re-OCR |
| Whole pages as `\| 24 \| text \|` tables | accepted-manuscript line numbers | `untable_gutter_manuscript.py`, or vision with gutter context |
| Journal line repeating once per page | running head | `strip_running_heads.py` (review the list) |
| Body starts mid-article | marker dropped image-only pages | vision re-OCR |
| `[VISION OCR FAILED FOR PAGE N: ...]` | content filter blocked a page | `recover_blocked_pages.py`, then check for duplicated text |

**Always verify vision output against the rendered page** for taxon names,
specimen numbers and systematic headings. On degraded scans the model can
write plausible text rather than `[ILLEGIBLE]`, and that passes every
automated check.

## Search

```bash
./.runtime/venv/bin/corpus search 'Hylaeosaurus'
./.runtime/venv/bin/corpus search 'title:hadrosaur'
./.runtime/venv/bin/corpus search 'NEAR("tail club" ankylosaur, 10)'   # function syntax
./.runtime/venv/bin/corpus search --genera Pteropelyx
./.runtime/venv/bin/corpus search 'titanosaur' --year '202*'
./.runtime/venv/bin/corpus search --key cope1889a --full-body
```

The tokenizer is `porter unicode61 remove_diacritics 2` ("México" matches
"Mexico"). `year` is unindexed: use `--year`. A zero-hit search is often an
OCR miss, not an absence; try a stem or a neighbouring phrase before
reporting that no paper says something.

## Things to remember

- **Atomic writes**: `corpus.io.atomic_write_text/json`; manifest edits
  through `corpus.manifest.update()`.
- **The data repository is read-only from here.** Report proposed changes
  to its maintainers; never edit its files (`working-agreements.md`).
- **Fetchers run over every missing key** unless given `--key` or
  `--keys-file`, and record failed attempts in `acquisition.source`.
- **Respect access controls.** Fetch only open-access copies; never work
  around logins, paywalls, CAPTCHAs or bot checks.
- **Rate limits**: Unpaywall allows about 5 requests a second; the fetchers
  pace themselves.
