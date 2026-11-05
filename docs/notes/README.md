# Project notes

Durable lessons for working in this repository: pipeline gotchas, research
method, and answers already reached. They live in the repository rather than in
Claude's per-machine memory so they survive a new checkout. `CLAUDE.md` covers how the
pipeline works; these files cover the traps and the decisions.

| File | Read it when |
|---|---|
| [working-agreements.md](working-agreements.md) | Always. The data-repo boundary and review rules. |
| [conversion.md](conversion.md) | Converting, re-OCRing, translating or repairing markdown. |
| [intake-manifest-keys.md](intake-manifest-keys.md) | Running `/intake` or `/refresh-bib`, editing `manifest.json`, naming or renaming keys; also shell and environment traps. |
| [code.md](code.md) | Changing a script or the `corpus` package: where shared code lives, how to test a script that writes, what to run before committing. |
| [acquisition.md](acquisition.md) | Finding or fetching a paper: free routes (BHL, archive.org, Europe PMC/PMC, public archives), fetch-tier pitfalls, DjVu and viewer recipes. |

A private archive built on this repository keeps its own notes beside these
(findings, acquisition leads, a work queue) and indexes them in
[README-archive.md](README-archive.md). Read that file too when it exists.

Search before re-deriving: `grep -rn -i 'TERM' docs/notes/` is quicker than
re-reading the papers.

## Keeping these current

- One entry per fact, under a `###` heading that states the claim.
- Describe the present state and what to do. A past mistake belongs here only
  as a warning about a pitfall that can still happen.
- Pipeline notes describe the situation ("a key with a diacritic", "two papers
  by the same authors in the same year") rather than naming a paper, because
  another checkout's copy of a paper can differ. Findings about papers keep
  their evidence (citation keys, pages, specimen numbers) so a reader can
  check the claim without re-running the search.
- When a finding is overturned, edit or delete the entry. Don't append a
  contradiction below it.
- Citation keys change at `/refresh-bib` (orphan `…z` keys migrate onto bib
  keys). If a key named here no longer exists, look it up in the manifest by
  DOI or title and update the note.
