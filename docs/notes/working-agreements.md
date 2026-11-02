# Working agreements

Boundaries and review rules for working in a corpus checkout. A private
archive can add its owner's own preferences in `working-agreements-archive.md`;
read that too when it exists.

## Data repo boundaries

### Never edit the data repo from this repo, and don't offer to
Reading the data repository (`../data`, or `OPEN_PALEO_DATA`) is fine; writing to it is not (genera YAMLs, `formations.yaml`, `references/`, `stratigraphy/`, anything under `data/`). The data repo is curated through its own agent, which owns schema conventions, the sign-off gate and citation bookkeeping. Don't close answers with an offer to apply changes there. End corpus research with a written handoff the user can paste to the data agent: the verdict, the field-level change, and `{key}:{line}` citations.

### Draft note wording in the data repo's prose style
The data repo's CONTRIBUTING sets these rules for its notes; draft handoff wording to match, and check CONTRIBUTING if in doubt:
- **Authors:** three or more are "and colleagues", e.g. "Smith and colleagues (1999)" or "(Smith and colleagues, 1999)". Never write "et al." or "and others (Year)" outside a quotation. Quotations keep their source's wording.
- **Dashes:** a spaced em dash in prose; an unspaced en dash for ranges (Aptian–Albian).
- **No self-reference:** not "recorded here", "adopted here", "the entry" or "the registry". State the facts and their sources, not what the record does or doesn't hold.
- **Pronouns:** none gendered for people unless the source states the gender.
- **Absence claims:** scope them to "the works cited for this taxon".
- **Only held works by name:** a note names only papers the data repo cites. A relayed source we don't hold is described ("earlier palynological work, reported through the held paper"), not named by author and year.

### A record's period and stages describe the type horizon only
A species record is dated by its holotype's horizon, not by the species' known range. Referred material from other horizons belongs in the notes. For example, *Heterodontosaurus* stays [Sinemurian, Pliensbachian] (holotype in the lower Clarens); the referred upper-Elliot skeleton doesn't add the Hettangian. Exception: a reworked type takes the age of its source beds, and the depositional age of the bed it was found in goes in the note (*Craterosaurus*: Neocomian, from the Aptian–Albian Potton Nodule Bed).

### Never redirect output while the data repo is the working directory
An unbalanced quote in an ad-hoc heredoc can write junk files into the data repo (for example into `references/`, with a trailing `"` in the filename), and the data agent then looks for a bug in its own scripts. Read data-repo files by absolute path without `cd`-ing there, send all redirects to the scratchpad, and prefer `python3 - <<'PY'` with explicit `open(..., 'w')` over shell `>`. No script in this repo writes to the data repo. A file in `references/` not matching `^[a-z][a-z0-9-]*\d{4}[a-z]?\.yml$` is invisible to the data repo's validator, so such junk goes unnoticed there.

## Review rules

### Vision fabrication: heightened review, not a prompt change
Do not change `PROMPT_VERBATIM` (a new prompt_id invalidates the whole vision cache). Instead eyeball the taxon-bearing pages of vision output for sources prone to poor scans (early and mid-20th-century reprints, Smithsonian/BHL bound volumes), and check any page the user flags as suspect.

### Ask for the PDF page range on whole-volume scans
Do not assume cited page = PDF page; ask the user which physical pages hold the article before scope-limited OCR.

### QC before translating rough scans
On rough foreign-language scans, stop for a cleanup/QC checkpoint before translation. Delegated cleanup reports metrics, not pasted text.
