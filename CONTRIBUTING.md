# Contributing

Thank you for improving the corpus tools. Bug reports, fixes to the
conversion and repair scripts, new publisher patterns for
`clean_frontmatter.py` and better documentation are all welcome.

## Never submit content from publications

This repository holds tools only. Do not add, in any pull request or commit:

- PDFs, scans, page photographs or figures from a paper;
- converted Markdown of a paper, or long quotations from one;
- tables, character matrices or supplementary data copied from a publication;
- anything else you do not hold the copyright to or a licence to publish.

Short snippets in a test or an issue, enough to show a conversion fault (a
few words around a broken dash, say), are fine. Write them out yourself
rather than pasting a page.

The content folders (`pdfs/`, `markdown/`, `supplementary/`, `source_images/`,
`pdf_images/`, `condensed/`, `index/`) each contain a `.gitignore` of `*`, so
git ignores what you put there. Don't remove those files in a fork you
publish. A required check fails any pull request that adds files under those
folders, or any PDF, image, Word or Excel file anywhere.

## Developer Certificate of Origin

Every commit must be signed off, certifying the
[Developer Certificate of Origin 1.1](https://developercertificate.org/):
that you wrote the change or otherwise have the right to submit it under
this repository's MIT licence. For this repository the certificate also
covers the rule above: by signing off you confirm the commit contains no
content from publications.

Sign off with `git commit -s`, which adds a line like

    Signed-off-by: Your Name <you@example.com>

A required check fails pull requests with unsigned commits. To sign off
commits you have already made: `git rebase --signoff main`, then force-push
your branch.

## Making a change

1. Fork the repository and work on a branch in your fork.
2. `./scripts/bootstrap.sh --hooks` sets up the environment and the hooks
   (the pre-commit hook refuses `.env` and anything that looks like an API key).
3. Before committing, run:

   ```bash
   ./.runtime/venv/bin/ruff check scripts src tests
   ./.runtime/venv/bin/python -m pytest -q
   ```

   Tests run on temporary directories, never on your corpus. A change to a
   repair or cleanup script should also be checked on real papers: run it as
   a dry run on a sample before and after, and say in the pull request what
   changed. `docs/notes/code.md` describes how the code is laid out.
4. Open a pull request against `main`.
