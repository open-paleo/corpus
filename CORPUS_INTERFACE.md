# Corpus interface

What other repositories (the Open Paleo data repository and its agents) may
rely on in a corpus checkout. Anything not listed here is internal and may
change without notice. A change to anything listed here is a breaking change
and must be announced in the pull request that makes it.

## Finding the corpus

The data repository resolves the corpus as `$OPEN_PALEO_PAPERS_DIR`, else a
sibling folder `../open-paleo-papers` next to the data repository. The corpus
resolves the data repository as `$OPEN_PALEO_DATA`, else `../data`.

## Files

All paths are relative to the corpus root. Citation keys are the data
repository's bib keys (`mantell1825a`, `prieto-márquez2010c`), in Unicode NFC.

| Path | Contents |
|---|---|
| `markdown/{key}.md` | The paper as Markdown, in English. Absent or empty when not yet converted. |
| `markdown/{key}.original.md` | The original-language text, when `markdown/{key}.md` is a translation. |
| `markdown/supplementary/{key}-{name}.md` | Converted supplementary material of paper `{key}` (appendix, matrix, plates). `{key}` is the longest prefix of the stem that is a manifest key. |
| `pdfs/{key}.pdf` | The source PDF. |
| `supplementary/{key}-{name}.pdf` | Source of a supplementary file. |
| `manifest.json` | One entry per key; fields below. |
| `index/papers.db` | SQLite FTS5 search index; schema below. |
| `condensed/` | Written by the data repository's agents (paper condensations); the corpus never writes it. |

Pages of vision-OCR'd papers are separated by `\n\n---\n\n`. Vision output
may contain `[FIGURE: ...]` and `[ILLEGIBLE]` markers.

## `manifest.json`

A JSON object keyed by citation key. Readers may rely on these fields of an
entry; any of them may be `null` or absent on a key that has never been
acquired:

| Field | Meaning |
|---|---|
| `citation` | The bib entry: `entry_type`, `author`, `title`, `year`, `journal` / `booktitle` / `school` / `publisher` / `howpublished`, `volume`, `pages`, `doi`, ... |
| `in_bib` | `false` for a key the bib no longer has (kept because files exist). |
| `doi`, `year` | From the bib. |
| `genera`, `clades` | Genera and clades that cite the key directly. Labels only. |
| `acquisition.source` | Where the PDF came from (`unpaywall`, `pmc_oa`, `oa_direct`, `manual`, ...). Values with the suffixes in `corpus.manifest.FAILED_SUFFIXES` (`unpaywall_no_oa`, `oa_direct_landing_failed`, ...) record a failed fetch, not a PDF. |
| `conversion.tool` | `marker`, `claude-vision`, or another converter; `null` when not converted. |
| `conversion.skipped` | `true` for a held paper deliberately left unconverted (citation-only). |
| `supplementary.{name}` | `pdf_path`, `markdown_path`, `tool` for each supplementary file. |

Whether a paper is held or converted is best read from the files above (the
PDF, a non-empty Markdown file), as `corpus status` does.

Writers other than the corpus scripts must not edit `manifest.json`.

## `index/papers.db`

```sql
CREATE VIRTUAL TABLE papers USING fts5(
    citation_key UNINDEXED, title, authors, year UNINDEXED, journal,
    genera, tool UNINDEXED, body,
    tokenize = 'porter unicode61 remove_diacritics 2'
);
```

One row per `markdown/{key}.md` and per supplementary file (keyed
`{key}-{name}`, title prefixed `[suppl: {name}]`). `genera` is a
comma-separated list. Open it read-only; `scripts/indexer.py` rebuilds it
from `markdown/`.

## Commands

`scripts/search.py` (or `corpus search`) takes an FTS5 query and
`--key`, `--genera`, `--year`, `--full-body`, `--list-keys`, `--stats`.
`corpus status [--missing]` reports what is held. Both are read-only.
