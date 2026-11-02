# Open Paleo corpus

Tools for building your own local, searchable archive of the papers that the
[Open Paleo data repository](https://github.com/open-paleo/data) cites:
fetch open-access PDFs, add the ones you obtain yourself, convert them to
Markdown, repair the text, and search it with SQLite full-text search.

This repository holds **no papers**. It ships the pipeline and empty
content folders. Every PDF, every converted paper and the `manifest.json`
recording what you hold stay on your own machine: git ignores them.

## What you need

- macOS or Linux, Python 3.11 or newer, git.
- For text-layer conversion: [marker](https://github.com/datalab-to/marker)
  (installed by the setup; it pulls in PyTorch, several GB).
- For scanned or non-English papers: an [Anthropic API key](https://console.anthropic.com/).
  Vision OCR costs roughly $0.01–0.02 per page with the default model, and the
  scripts print the cost of every run.
- Optional: an email address for the [Unpaywall](https://unpaywall.org/products/api)
  API, which finds legal open-access copies by DOI.

## Setup

Clone this repository beside the data repository. The data repository looks
for the corpus in a sibling folder named `open-paleo-papers` (or wherever
`OPEN_PALEO_PAPERS_DIR` points), so clone it under that name:

```bash
mkdir open-paleo && cd open-paleo
git clone https://github.com/open-paleo/data.git                    # optional, see below
git clone https://github.com/open-paleo/corpus.git open-paleo-papers
cd open-paleo-papers
./scripts/bootstrap.sh --hooks      # venv, .env from .env.example, search index, git hooks
$EDITOR .env                        # UNPAYWALL_EMAIL, ANTHROPIC_API_KEY
./.runtime/venv/bin/corpus manifest refresh    # one manifest entry per bib key
./.runtime/venv/bin/corpus status
```

The bibliography comes from `CORPUS_BIB_SOURCE` in `.env`:

| Value | Reads |
|---|---|
| `auto` (default) | `../data` if it is there, else `bib/references.bib` if present, else the download |
| `sibling` | `../data/dist/references.bib` (or `$OPEN_PALEO_DATA`) |
| `bundled` | `bib/references.bib` in this repository, kept current by the `update-bib` workflow, which the data repository triggers when a build changes its bib |
| `remote` | the data repository's `dist/references.bib` on GitHub, cached in `.runtime/cache/bib/` |

So the data repository clone is optional: without it, the bib comes from
`bib/references.bib` and the genus/clade labels are downloaded.

## Building the corpus

1. **See what is missing.** `corpus status --missing` lists the bib entries
   you don't hold yet; `--genus Iguanodon`, `--clade Ornithopoda` and
   `--has-doi` narrow it.
2. **Fetch what is open access.** `corpus fetch unpaywall` tries every
   missing key with a DOI; `corpus fetch pmc` then tries the PMC Open Access
   subset (many open-access journals whose own sites refuse scripted
   downloads, PeerJ among them, deposit there); `corpus fetch oa-direct`
   tries the publisher's landing page. All are resume-safe, and fetched
   papers show up in `/intake` as "unconverted".
3. **Add your own PDFs.** Save a paper you have obtained as
   `pdfs/{citation key}.pdf` (the key from `corpus status --missing`).
4. **Run intake.** In [Claude Code](https://claude.com/claude-code), `/intake`
   discovers the new PDFs, picks a conversion path for each (marker for
   born-digital PDFs, vision OCR for scans and pre-1900 papers), runs the
   quality checks and repairs, and indexes the result. Without Claude Code,
   run the same steps yourself: `CLAUDE.md` lists them.
5. **Search.** `corpus search 'Hylaeosaurus'`, `corpus search --genera Iguanodon`,
   `corpus search 'NEAR("tail club" ankylosaur, 10)'`.

`corpus` with no arguments lists every command. Each one is a script in
`scripts/` and takes `--help`.

## How the data repository uses it

The data repository's agents read `markdown/{key}.md`, the search index and
`manifest.json` to check citations against the papers themselves.
[CORPUS_INTERFACE.md](CORPUS_INTERFACE.md) lists what they may rely on.

## Contributing

Pull requests that improve the tools are welcome. **Never submit papers,
PDFs, page images, converted text or anything else taken from a
publication**: a required check rejects pull requests that add files under
the content folders, and every commit needs a `Signed-off-by:` line
certifying you have the right to submit it. See
[CONTRIBUTING.md](CONTRIBUTING.md).

## Licence

MIT; see [LICENSE](LICENSE). marker, which conversion runs as a separate
program, is GPL-3.0 and is installed separately by the setup.
