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
  (`bib/references.bib`, updated from the data repository after each build that changes it, by the
  `update-bib` workflow's pull requests), `remote` (downloaded from the data
  repository on GitHub), or `auto` (the first of those that exists, else
  remote).
- **Credentials:** `.env` holds `UNPAYWALL_EMAIL`, `ANTHROPIC_API_KEY` and,
  for the BHL tier, the optional `BHL_API_KEY`.
  Mode 0600. Never commit, never log, never print it.
- **Never commit content.** `manifest.json` is gitignored, and the content
  folders each hold a `.gitignore` of `*`. Don't remove those files, don't `git add -f` anything under them, and
  never push papers, page images or converted text to the public repository.
- **What other repositories rely on** is in `CORPUS_INTERFACE.md`. Treat a
  change to anything listed there as breaking.

## Project notes (`docs/notes/`)

Lessons and settled answers about the pipeline. Start with
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
index/papers.db                     FTS5 index (scripts/index.py)
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
| `corpus manifest refresh` | `manifest_refresh.py` | Merge the bib into the manifest; preserves acquisition and conversion |
| `corpus manifest refresh-dois` | `manifest_refresh_dois.py` | DOI-only refresh |
| | `bootstrap.py` | Rebuild the manifest from scratch (**clobbers state**: refuses a non-empty manifest without `--force`) |
| `corpus status` | | Held / converted / indexed / missing counts; `--missing` lists keys |
| `corpus fetch unpaywall` | `fetch_unpaywall.py` | Tier 1: open-access PDFs by DOI via Unpaywall |
| `corpus fetch unpaywall-by-doi` | `fetch_unpaywall_by_doi.py` | Same, for key/DOI lists not yet in the manifest |
| `corpus fetch pmc` | `fetch_pmc.py` | Tier 2: PMC Open Access subset by DOI (Europe PMC lookup, NCBI's public bucket) |
| `corpus fetch oa-direct` | `fetch_oa_direct.py` | Tier 3: DOI landing page → `citation_pdf_url` (`--browser-ua` opt-in) |
| `corpus fetch retry-unpaywall` | `fetch_retry_unpaywall.py` | Retry Unpaywall download failures, saving any HTML the URL serves (`--browser-ua` opt-in) |
| `corpus bhl setup`, `corpus bhl index` | `bhl_setup.py`, `bhl_index.py` | BHL tier, once: 92 MB of dumps from BHL's S3 bucket → `.runtime/bhl_dumps/bhl.db` (0.55 GB) |
| `corpus bhl match` | `bhl_match.py` (+ `bhl_item_match.py`) | Match missing keys to a BHL article, else a unique volume; prints a review table |
| `corpus bhl fetch` | `bhl_fetch.py` (+ `bhl_stitch.py`) | Page images of accepted matches → `pdfs/{key}.pdf`, flagged for vision OCR |
| `corpus intake discover` | `intake_discover.py` | What is pending in `pdfs/`, with a path hint per key |
| `corpus intake acquire` | `intake_acquire.py` | Record dropped PDFs as acquired (`--vision-ocr REASON` to skip marker) |
| `corpus intake qc` | `intake_qc.py` | Bytes-per-page triage of a converted batch |
| `corpus pdf extract-pages` | `pdf_extract_pages.py` | Cut a whole-volume PDF down to the article, in place |
| `corpus pdf append-pages` | `pdf_append_pages.py` | Append pages from a source PDF |
| `corpus pdf compress` | `pdf_compress.py` | Shrink an oversized scan (**drops the text layer**) |
| `corpus pdf stitch` | `pdf_stitch.py` | `source_images/{key}/` → `pdfs/{key}.pdf` |
| `corpus pdf photocopier` | `pdf_photocopier.py` | Same, flat-field-normalising phone photos of a volume |
| `corpus pdf images` | `pdf_images.py` | Embedded figures of `--key K` → `pdf_images/K/` (on demand, gitignored; `/extract-images`) |
| `corpus convert marker` | `convert_marker.py` | PDF → Markdown from the text layer (`--workers 1`) |
| `corpus convert vision` | `convert_vision.py` | PDF → Markdown via Claude vision OCR (per-page cache) |
| `corpus convert recover` | `convert_recover.py` | Re-OCR pages the content filter blocked, cut only on whitespace (candidate first, `--apply` to write) |
| `corpus convert spread` | `convert_spread.py` | Vision OCR of scans whose pages are two-page book spreads, split at the gutter |
| `corpus convert poster` | `convert_poster.py` | Vision OCR of a single-canvas conference poster, in tiles |
| `corpus convert translate` | `convert_translate.py` | Translate non-English markdown (keeps `.original.md`) |
| | `repair_cjk.py` | Correct vision-OCR'd Chinese/Japanese from a born-digital text layer |
| `corpus repair all` | `corpus.repair` | All text-layer repairs in one pass (dry run unless `--apply`) |
| `corpus clean frontmatter` | `clean_frontmatter.py` | Publisher cover boilerplate |
| `corpus clean running-heads` | `clean_running_heads.py` | Repeated page heads and feet (**review the list**) |
| `corpus clean despace` | `clean_despace.py` | Rejoin OCR word splits (removes spaces only) |
| `corpus clean ligature-codes` | `clean_ligature_codes.py` | fi/fl ligatures and en dashes a text layer reads as ®, ¯, ± (Nature Adv* fonts) |
| `corpus clean transposed-accents` | `clean_transposed_accents.py` | Accents carried as a separate mark beside their letter (`Neuque´n`, `Mus´eum`) |
| `corpus clean diacritics` | `clean_diacritics.py` | Spacing accents after their letter (`Osmo´lska`); refuses rough scans |
| `corpus clean glued-words` | `clean_glued_words.py` | Spaces a text layer never emitted (`KaiparowitsFormation`) |
| `corpus clean gesperrt` | `clean_gesperrt.py` | German letterspaced emphasis (`H o l o t y p e`) |
| `corpus clean coordinates` | `clean_coordinates.py` | Verified °/′/″ repairs from `repairs/coordinate_glyphs.json`; `--audit` finds suspects |
| `corpus clean ligatures` | `clean_ligatures.py` | Restore f-/Th-ligatures the text layer dropped (`diferent`, `Te`); word frequencies from the corpus once it has 500 clean papers, from `wordfreq` before that |
| | `clean_gutter_tables.py`, `repair_gutter_numbers.py` | Accepted-manuscript line-number gutters |
| | `clean_padded_tables.py` | Whitespace-padded table cells |
| `corpus qc scan` | `qc_scan.py` | PUA garbage, CJK, image-only stubs; `--apply` flags vision OCR |
| `corpus qc languages` | `qc_languages.py` | Non-English bodies (`TRANSLATION_AUDIT.md`) |
| `corpus qc spillover` | `qc_spillover.py` | Markdown holding a neighbouring article |
| `corpus clean spillover` | `clean_spillover.py` | Cut neighbouring articles out of a Nature-style reprint (dry run unless `--apply`) |
| `corpus qc translations` | `qc_translations.py` | Translations that lost content: short or cut-off chunks, model false starts |
| `corpus qc control-glyphs` | `qc_control_glyphs.py` | What glyph each broken font's control codes draw; builds the map for `repair control_glyphs` |
| `corpus qc symbol-glyphs` | `qc_symbol_glyphs.py` | Coordinate symbols a text layer hands back as the wrong character, with crops |
| `corpus qc garbage` | `qc_garbage.py` | ASCII-looking OCR garbage the quality scan misses (`Ankylo8aurus`; `--near-miss` for `Ornlthlschia`) |
| `corpus qc gutter` | `qc_gutter.py` | Marker conversions of line-numbered manuscripts that lost lines, measured against the text layer |
| `corpus qc quotes` | `qc_quotes.py` | Quotations in a findings write-up checked against the markdown they cite |
| `corpus qc specimen-ids` | `qc_specimen_ids.py` | Accession numbers inked on specimens in figure photos (vision; `/specimen-id-scan`) |
| `corpus qc textlayer` | `qc_textlayer.py` | Vision output against the PDF's own text layer |
| `corpus index` | `index.py` | Build/refresh `index/papers.db` (incremental) |
| `corpus search` | `search.py` | Query the index |
| `corpus web` | `web.py` | Search, read and check papers in a browser: the text beside its PDF, kept on the same page (127.0.0.1:8765; `--open`) |

## "I just added a PDF — now what?"

**Run `/intake`.** It discovers the new PDFs, classifies each (marker for
born-digital PDFs from 1900 on, vision OCR for scans and pre-1900 papers,
scope-limited vision for a whole volume, vision with gutter context for
line-numbered manuscripts), converts, runs QC and the repairs, and indexes.
The skill encodes the heuristics; the steps below are what it runs.

```bash
# 1. Save the file under its exact citation key (from `corpus status --missing`).
cp ~/Downloads/paper.pdf pdfs/KEY.pdf
# 2. Record it (creates a stub entry, in_bib=False, if the key is not in the bib yet).
./.runtime/venv/bin/corpus intake acquire KEY
#    A scan with no usable text layer: skip marker.
./.runtime/venv/bin/corpus intake acquire KEY --vision-ocr "1800s scan; skip marker"
# 3. Convert.
./.runtime/venv/bin/corpus convert marker --workers 1 --key KEY
./.runtime/venv/bin/corpus convert vision --key KEY    # for flagged keys
# 4. QC, repairs, cleanup.
./.runtime/venv/bin/corpus intake qc KEY
./.runtime/venv/bin/corpus qc scan
./.runtime/venv/bin/corpus repair all --key KEY            # read, then --apply
./.runtime/venv/bin/corpus clean ligatures --key KEY       # read, then --apply
./.runtime/venv/bin/corpus clean frontmatter --key KEY     # read, then --apply
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
| `numerou s`, `th e` | scan OCR word splits | `clean_despace.py` |
| `Cr~tac~ Inf~rieur`, no accents at all in a French paper | scan OCR flattened accents | vision re-OCR |
| `Ornlthlschia`, `Ho~otype` | scan OCR letter misreads | vision re-OCR |
| `CampanianePaleocene`, `M€uller`, `Rhaetian6` | font faults in the text layer | `corpus repair all` |
| `diferent`, `frst`, `lef`, `Te fossil` | dropped f-/Th-ligatures (Springer Nature, Elsevier) | `corpus clean ligatures --key K` |
| `AnewprosauropoddinosaurfromLowerJurassic` | fullwidth Latin in a Chinese PDF | vision re-OCR |
| Whole pages as `\| 24 \| text \|` tables | accepted-manuscript line numbers | `clean_gutter_tables.py`, or vision with gutter context |
| Journal line repeating once per page | running head | `clean_running_heads.py` (review the list) |
| Body starts mid-article | marker dropped image-only pages | vision re-OCR |
| `[VISION OCR FAILED FOR PAGE N: ...]` | content filter blocked a page | `corpus convert recover --key K --page N`; read the measurements, never print the page; then `--apply` |

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
./.runtime/venv/bin/corpus search --key KEY --full-body
./.runtime/venv/bin/corpus web --open      # the same search in a browser, each paper beside its PDF
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

## The BHL tier (old, public-domain papers)

The Biodiversity Heritage Library holds scans of most pre-1930 literature.
biodiversitylibrary.org itself is behind Cloudflare, so everything goes
through BHL's open S3 bucket (data dumps, OCR, JP2 page images) and its API.

1. `corpus bhl setup && corpus bhl index`, once (and again to pick up new
   BHL content).
2. `corpus bhl match` matches every bib key without a PDF to a BHL *Part*
   (an article: by DOI, else by title, first author and year), else to the
   only *Item* (volume) of the cited journal and year. **Read the review
   table it prints**: a fuzzy match can be a different paper by the same
   author (a 1990 paper for a 1985 one), and a volume match is the whole
   volume. DOI matches are accepted automatically; nothing else is fetched
   until accepted.
3. `corpus bhl fetch --key K` accepts K's match, downloads its page images
   and stitches `pdfs/K.pdf`. For a volume match the article is located from
   the cited pages using BHL's printed page numbers (needs `BHL_API_KEY`) or
   its OCR; when that fails it says so, and
   `corpus bhl fetch --key K --item-seqs FIRST-LAST` takes the volume's own
   page sequence (read it off the volume at biodiversitylibrary.org).
   Plates bound at the end of a volume, numbered separately, are not
   included: check the plate references against the PDF.
4. `/intake`: the papers show as "unconverted" and are flagged for vision
   OCR. BHL's own OCR is not used as the text (it is too rough).
