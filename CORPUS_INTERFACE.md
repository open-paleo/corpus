# Corpus interface

What other repositories (the Open Paleo data repository and its agents) may
rely on in a corpus checkout. Anything not listed here is internal and may
change without notice. A change to anything listed here is a breaking change
and must be announced in the pull request that makes it.

## Finding the corpus

Clone the two repositories side by side, as `data` and `corpus`. The data
repository resolves the corpus as `$OPEN_PALEO_CORPUS` (`$OPEN_PALEO_PAPERS_DIR`
is still accepted), else the sibling folder `../corpus`. The corpus resolves
the data repository as `$OPEN_PALEO_DATA`, else `../data`.

## Files

All paths are relative to the corpus root. Citation keys are the data
repository's bib keys (author surname, year and an optional letter suffix;
they can contain hyphens and non-ASCII letters), in Unicode NFC.

| Path | Contents |
|---|---|
| `markdown/{key}.md` | The paper as Markdown, in English. Absent, or with no text but page markers, when not yet converted. |
| `markdown/{key}.original.md` | The original-language text, when `markdown/{key}.md` is a translation. |
| `markdown/supplementary/{key}-{name}.md` | Converted supplementary material of paper `{key}` (appendix, matrix, plates). `{key}` is the longest prefix of the stem that is a manifest key. |
| `pdfs/{key}.pdf` | The source PDF. |
| `supplementary/{key}-{name}.pdf` | Source of a supplementary file. |
| `manifest.json` | One entry per key; fields below. |
| `index/papers.db` | SQLite FTS5 search index; schema below. |
| `condensed/` | Written by the data repository's agents (paper condensations); the corpus never writes it. |

Each page's text starts with a page marker, an HTML comment on its own line:
`<!-- page N -->`, where N is the page's position in `pdfs/{key}.pdf` (from 1,
not its printed number). A page with no text keeps its marker. A paper may
have no markers at all, or fewer than the PDF has pages. Strip them with
`re.sub(r"^<!-- page \d+ -->\n", "", text, flags=re.M)` when only the text
matters; `index/papers.db` holds the text without them. Pages of
vision-OCR'd papers are also separated by `\n\n---\n\n`, and vision output may
contain `[FIGURE: ...]` and `[ILLEGIBLE]` markers.

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
PDF, a Markdown file with text besides its page markers), as `corpus status`
does.

Writers other than the corpus scripts must not edit `manifest.json`.

## `index/papers.db`

```sql
CREATE VIRTUAL TABLE papers USING fts5(
    citation_key UNINDEXED, title, authors, year UNINDEXED, journal,
    genera, tool UNINDEXED, body, clades, translated UNINDEXED,
    tokenize = 'porter unicode61 remove_diacritics 2'
);
```

One row per `markdown/{key}.md` and per supplementary file (keyed
`{key}-{name}`, title prefixed `[suppl: {name}]`). `genera` and `clades`
are comma-separated lists of the genera and clades whose data cite the
paper; a supplementary row carries its parent's. `translated` is `1` when
the body is an English translation (the original is
`markdown/{key}.original.md`), else empty. Select columns by name: new
columns are only ever added at the end, so the body stays column 7 for
`snippet()` and `highlight()`. Open it read-only; `scripts/index.py`
rebuilds it from `markdown/`, replacing the file only once the new index
is complete.

## Commands

`scripts/search.py` (or `corpus search`) takes an FTS5 query and
`--key`, `--genera`, `--clades`, `--translated`, `--year`, `--full-body`,
`--list-keys`, `--stats`.
`corpus status [--missing]` reports what is held. Both are read-only.
