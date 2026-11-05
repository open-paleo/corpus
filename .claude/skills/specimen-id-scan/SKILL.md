---
name: specimen-id-scan
description: Recover a specimen's accession number from photographs when the literature is wrong, silent or self-contradictory — specimens are inked with their number, so vision-scan figure images and corroborate against the accession series
---

> **This checkout's own steps:** if `.claude/skills/specimen-id-scan/archive.md` exists, read it
> first. It is a private repository's layer on this skill (extra steps, local
> conventions) and is never part of the public repository; where it and this
> file differ, it wins for this checkout.

Fossil specimens are routinely marked with their accession number — hand-written
in ink, painted, or stencilled directly onto the bone or its matrix. When the
published literature is **wrong, silent, or contradicts itself** about a
catalogue number, a photograph of the material can settle it.

Use this when you hit one of:

- two taxa published with the **same** number (two papers each giving one
  catalogue number as the holotype of a different genus);
- a paper that **contradicts itself** (its text gives one number, its own
  table another);
- an erecting paper that gives **no number at all** (common in papers before
  about 1930);
- a number that **fits no known series pattern** (five digits in a uniformly
  four-digit series).

This is a **last-resort corroboration tool**, not a first move. Exhaust the
literature first: search by the specimen's earlier taxonomic identities, by its
provenance (locality, collector, expedition), and by every historical form of
its number (old prefixes, field numbers, numbers with and without separators).

## 1. Enumerate the accession series first

Cheap, no API calls, and it frames everything else:

```bash
./.runtime/venv/bin/python scripts/scan_specimen_ids.py --series PREFIX
```

Prints every sub-number of that prefix attested anywhere in `markdown/`, with
the papers using each. Do this **before** scanning images so you know what a
candidate reading would have to fit.

## 2. Gather images

Order of usefulness — note the counter-intuitive first point:

1. **Later papers beat the erecting paper.** Original plates usually *predate*
   the ink. Redescriptions, revisions and comparative works re-photograph
   specimens as currently curated, often with the mark visible.
2. **Off-corpus sources are usually richer**: museum exhibition/collection
   photos, supplementary material (higher-res, less cropped), conference
   posters, press photos from the announcement, ResearchGate figure galleries.
3. **Just ask.** A researcher who has been in the collection can photograph the
   mark in seconds. A mailing list such as the Dinosaur Mailing List reaches
   people who have.

## 3. Scan

```bash
# extract from held PDFs and scan, corroborating against a series
./.runtime/venv/bin/python scripts/scan_specimen_ids.py \
    --key KEY1 --key KEY2 --extract --series PREFIX

# scan a loose directory of photos (museum shots, a colleague's screenshot)
./.runtime/venv/bin/python scripts/scan_specimen_ids.py \
    --images photos/ --series PREFIX
```

`--key` scans `pdf_images/{key}/`; `--extract` first runs
`extract_pdf_images.py` for keys with no images yet.

Flags: `--all` prints unmarked images too; `--force` ignores the per-image
cache at `.runtime/cache/specimen_ids/`; `--keys-file` for batches. Replies are
cached, so re-runs are free. Log at `.runtime/logs/specimen_ids.jsonl`.

Images under 15 KB are skipped (rules, logos, scale bars), and everything is
downscaled to 1568 px — Anthropic's vision cap — so cost is ~10 images for a
few cents.

## 4. Judge the result

The script reports each candidate against the series:

- **ALREADY ASSIGNED** → suspicious. Re-read the image before believing it.
- **UNATTESTED, dense neighbourhood** → meaningful corroboration, e.g. a
  candidate `-0003` sitting between attested `-0002` and `-0006`, with 7
  entries within ±10.
- **UNATTESTED, sparse neighbourhood** → weak. In a thin series almost any
  number is "a gap"; don't oversell it.

Also check the **element matches** the published type description: if the
photo shows a vertebra, the holotype list should include that kind of vertebra.

## Failure modes

- **CJK Roman numerals.** `Ⅲ` is drawn as three strokes with a bar and misreads
  as `111`, `皿`, `Ш`, `ITI`. The model may return a prefix like `4ITIII` and
  flag its own uncertainty: the numeric suffix can be reliable while the prefix
  is not. **Always eyeball a hit yourself** by `Read`-ing the image.
- **Ink postdating publication** may record a *recataloguing*, not the original
  intent. It is evidence about the register's current state.
- **Field numbers ≠ accession numbers.** Many collections use field numbers
  alongside accession numbers, and papers cite either.
- **Absence proves little.** Ink often sits on an underside surface deliberately
  excluded from published views; journals also retouch backgrounds and sometimes
  remove labels. Published papers can show no ink at all on a specimen that is
  clearly marked.
- The scan is **corroboration, never proof**. The authority is the museum
  register. Use a hit to make a specific, checkable question to the collection
  ("is specimen X the holotype of taxon Y?") rather than to close the case.

## Recording the outcome

A photograph does not overturn a published number on its own. Record the state
as disputed with the evidence attached — under the "sensu Author" convention,
e.g. holotype `PREFIX-0002` *sensu* Author et al. YEAR, with a note that
material inked `PREFIX-0003` and the series gap both point elsewhere, pending
the museum's confirmation.
