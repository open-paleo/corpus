# Intake, manifest and citation-key notes

Gotchas for `/intake`, `/refresh-bib`, manifest edits and key handling, plus shell and environment traps.

## Intake

### Check page 1 of every new PDF against its key before converting
`intake_discover.py` never compares content to the key's citation, so a misfiled PDF looks like a normal intake and converting it overwrites the slot while the real paper stays missing.
- In-bib keys: compare the first-page title/authors (`get_text_bounded()[:250]`) with `manifest[key].citation`. A file dropped under one author's key can be a paper that author co-wrote under a different lead author (ICZN/BZN cases are keyed by lead author).
- Same author + same year is a red flag, not a confirmation: with two papers by the same author in the same year, the right paper can arrive under the sibling's key. The backstop signal is an unexpected key disappearing from `corpus status --missing` at the end of intake.
- Orphans have no citation to contradict; check the subject against the domain and against same-stem siblings (a wrong download from an aggregator can be an unrelated essay).
On mismatch, stop and ask; don't pick a destination key yourself.

### A split-key collision is usually a duplicate of the `a` sibling
When `scripts/intake_discover.py` prints `⚠ SPLIT-KEY COLLISION` (bare `foo2015` next to `foo2015a/b`), DOI-check the bare PDF against the siblings' `citation.doi` before accepting the suggested `…c` rename. If it duplicates a sibling, delete the never-acquired bare PDF (confirm with the user, citing the matching DOI). Rename to the next letter only if it is a genuinely new paper.

### An orphan can duplicate a held sibling or the real bib key; the higher-resolution scan wins
`intake_discover.py` does not flag a distinct-letter orphan in an existing split family, and a `z` orphan can duplicate a held bib key. Search the bib by title/DOI, not key. To choose between copies compare embedded image dimensions (`fitz.open(p)[0].get_images(full=True)`), not file size: a ~110 DPI scan loses to a ~290 DPI one whatever their sizes. Also check the incumbent markdown for plates, references and addresses before assuming a re-drop improves it.

### Don't judge an orphan's identity from the head of its markdown
Nature/Science single-article reprints start mid-page, so the markdown often opens with the tail and references of the previous article. Grep the whole body for the expected title before calling a misfile. A same-author orphan with a wrong year (even two years off) is a candidate for a newly surfaced missing bib key; rename-migrate it. After re-keying, cut everything above the `##` title.

### Citation-only keys: keep the PDF, skip conversion
When the user says a paper is citation-only, (1) `intake_acquire.py {key}` (sets `source=manual`), (2) set `conversion.skipped=true`, `skipped_reason`, `skipped_at`, `tool=null` atomically, (3) commit the PDF. Committing matters: `intake_discover.py` flags `(disk - acquired) | untracked | modified`, so a tracked, acquired PDF never resurfaces.

The same works for a supplementary file that isn't worth converting (map sheets, fold-out plates): on `manifest[parent]["supplementary"][name]` set `skipped: true`, `tool`, `markdown_path` and `converted_at` to null, and say why in `notes` ("Stored without OCR by request"). Commit the PDF and no markdown (remove any empty placeholder markdown); `intake_discover.py` then stops listing it.

### Check the key is free before writing an extracted PDF
Writing `pdfs/{key}.pdf` silently overwrites an existing file, and `intake_acquire.py` then rewrites the entry; a whole held book can be replaced by a five-page section this way (restore from git). Before any extraction, check `ls pdfs/{key}.pdf` and the manifest. A section of a book already held goes in as `supplementary/{key}-{name}.pdf`.

### Plate inserts change the cited→PDF page offset mid-chapter
In bound volumes (USGS Professional Papers, Palaeontographica, society memoirs) unnumbered plate leaves are bound between text pages, so an offset computed at the chapter start truncates the chapter. Before `/extract-pages`, print each candidate page's char count and first 110 chars and read the printed folios; plate pages have `len` ~150–500 and a head like `PROFESSIONAL PAPER 210 PLATE 5`, blank versos `len=0`. Find the next chapter's title page for the end.

### A direct manifest edit setting `vision_ocr_needed` must also set `acquisition.source="manual"`
`intake_acquire.py --vision-ocr` sets both. Editing only `conversion` (for instance to preserve a hand-written `acquisition.notes` that `intake_acquire` would overwrite) leaves `source=null`, and `intake_discover.py` re-surfaces the key as new on every run.

### A stub-orphan under an `a` key can be claimed by the bib for a different paper
A stub-orphan (`in_bib=False`) taken in under an `a` key can later find the bib assigning that key to another paper by the same author and year. Before choosing a `z` key for a new paper, compare its DOI and title with the bib citation (`manifest[key].citation`) of each same-stem key, not with the held file's contents. A held file that disagrees with its own key's bib citation is a mis-filed stub: move it to `z`, and put the new paper on the key the bib gave it.

### Never stage `pdfs/` or `markdown/` wholesale
`pdfs/` is the user's drop zone and may hold unkeyed PDFs for a later intake. `git add -A pdfs markdown` (in an intake, re-key or refresh commit) stages them under their download names, and the next intake shows them as tracked "new" files with renames as deletions. Stage explicit paths (`git mv` already stages renames) and check `git status --short pdfs/` for untracked downloads and unintended `A ` entries before committing.

## Citation keys

### Data-repo key/year policy lives in its CONTRIBUTING.md
Section "Reference Keys and Years":
- Use the year the work first became readable; online release counts. Online 2013 / print 2014 → 2013; published May 2025 in an issue labelled 2024 → 2025.
- Tiebreak: follow what other papers' reference lists print, not running-text mentions. If no year dominates, keep the key and fix the prose.
- Key form: surname lowercased + year + letter, keeping diacritics and hyphens, removing spaces.
- Letters follow the order entries were added, so `a` is not necessarily the earliest paper.
- Keys are never aliased. A year change arrives here at `/refresh-bib` as a dropped-key/new-key pair, never an in-place edit.

### Key years: first-online year, read off Crossref, confirmed by DOI
For a paper that reaches the corpus before the bib (stub-orphans), key it by the online-publication year even when the print issue differs, matching the data repo's policy. Query `https://api.crossref.org/works/<DOI>` for `published-online`, `published-print`, `issued`. The bib can still key such a paper differently, so an orphan's online-year key may be overridden at refresh. The literature-count tiebreak applies only when no clean online date exists. Read the DOI off the PDF; never guess one (a plausible-looking DOI can resolve to an unrelated paper). Tell the user when corpus citations mostly use the print year. The year printed inside the markdown may be a third value. To verify a renamed stub's body, read `index/papers.db` directly: `search.py --key` shows blank title/year for stub-orphans.

### Orphan keys: `{surname}{year}z` with diacritics, descending letters on collision
Papers not in the upstream bib get `{surname}{year}z` using the diacritic surname, keeping a/b/c free for real bib entries. A second orphan of the same author-year takes `y`, then `x`; never composites like `2005az`. Rename only the newcomer. Before `mv` into an orphan key, check `ls pdfs/{surname}{year}*` and the manifest: a tracked, clean file is invisible to `git status`, and a `mv` onto it silently overwrites a committed PDF (recover with `git checkout -- pdfs/{key}.pdf`). When the bib later adds the paper it arrives under its own key and is not auto-linked to the `z` orphan.
Exception: an orphan the user drops under an `a` suffix is deliberate; it means the bib entry is coming soon. Don't flag or rename it unless it collides; still check the bib by DOI/title (the bib key may use a compound surname).

### Resolving a citation key to a corpus file: exact match or a↔z only
Registry citations use the `a`/bib form while the corpus may hold the work under `…z`. Always check both (`for k in (key, key[:-1]+'z')`, in the manifest and `markdown/`) before reporting a key unheld.
Never match a↔b↔c (different works by construction) and never diacritic-fold: two keys differing only by an accent can be different papers. Folding and stem matching are fine for finding candidates, not for concluding identity; confirm from the document's first line.
For bulk reconciliation, gate on "store key ends in `a` AND corpus holds exactly `<stem>z` AND no other suffix". Don't rename orphans pre-emptively while the data repo's YAMLs are uncommitted (diacritic or ordering changes can still shift the suffix); let `/refresh-bib` do it. The data repo's build writes its whole reference store to `dist/references.bib`, and the manifest takes every bib key: there is no scope filter, and which genera and clades cite a key is a label (`corpus.bib.citing`), not a condition.

### Normalising a key set: assert the fold is injective first
Before applying any key normalisation (accent folding, suffix stripping), apply it to every key and assert the output set has the same size. If it shrinks, print the colliding groups and stop. Only `a`↔`z` on the same stem is a legitimate orphan mapping.

### Character classes like `[a-zà-ÿ]` silently drop real keys
`[a-z]` drops every diacritic key; `[a-zà-ÿ]` looks accent-safe but stops at U+00FF and drops keys with letters such as ő (U+0151). Walk parsed YAML/JSON instead of regexing serialized text; if you must match, use `\w` (Unicode by default in Python 3). Always check the denominator: count keys the class failed to match.

### The bib's key normalisation is unpredictable, even within one author
Some keys lose their diacritics and others keep them; hyphens and apostrophes in surnames are usually dropped; Russian soft-sign apostrophes are dropped; a key built on a given name can be re-keyed to the surname. One author can have keys in two spellings, and a key's spelling can change between refreshes.
An accent can be the disambiguator: two keys that differ only by an accent can be different papers. Match by DOI, never by key string.
ł/l and ä/a are distinct codepoints, so `intake_discover.py`'s NFC normalisation does not match them. Check the actual bib/manifest key before naming a dropped PDF; if a "missing" PDF has a diacritic-named twin on disk, rename it rather than re-acquiring. Expect to re-migrate the same artifact on a later refresh.

### Diacritic key corrections can shift the a/b suffix
When a refresh corrects ASCII keys to diacritic ones, the same-letter diacritic key may be a different paper (the ASCII `a` key's paper can belong on the diacritic `b` key).
Match every ASCII orphan with artifacts by DOI first, title second, reading the markdown (a dropped orphan's manifest citation is blank). Don't rely on the newly-dropped list alone; compute all `in_bib=False` orphans whose de-accented name or DOI collides with an `in_bib` key and flag where the two targets differ. Coincidental collisions exist: an ASCII orphan and a diacritic bib key with the same letters can be unrelated papers.

## Bib refresh

### manifest_refresh.py: run once, diff against HEAD
The script applies everything on the first run; a second run prints all zeros. Capture full output once (the skill uses `tee`). If the first output was lost: `git checkout HEAD -- manifest.json`, then run exactly once. For the net effect, compare `git show HEAD:manifest.json` with the working copy (added/removed keys, `in_bib` flips). The raw diff is huge because every entry's `bootstrapped_at` is re-stamped; filter it out.

### Renames, year corrections and caught-up z-orphans: migrate each dropped/new pair
The data repo never aliases keys, and `manifest_refresh.py` does not migrate artifacts: it adds new bib keys as unacquired entries and keeps the old ones as `in_bib=False`. So a year or surname correction and a `z` orphan the bib finally caught up with both appear as a dropped/new pair. Tell for a correction: the orphan's `year` field already disagrees with its key. A rename can cascade through a batch, so check what wants a vacated key.
- Pair by DOI; else by whether the bib title appears as a contiguous run in the markdown BODY (not the first heading, which catches journal banners and translated titles, and not author+year; many z-orphans have `title='?'`). Targets are not always `a`.
- Check the destination path is free (a tracked file there is invisible to `git status` and `mv` overwrites it).
- Move per pair: PDF, markdown, `.original.md` backups, `.runtime/cache/vision/{key}`, supplementary PDF+markdown (rewriting inner paths), and the manifest's `acquisition`, `conversion`, `supplementary`, `annotations`, `availability`, `paper_watch`, `bhl*`, `closed_access`; fix `pdf_path`/`markdown_path`; delete the old entry. New keys carry a placeholder acquisition dict with all-None fields, so test `.get("source")`, not truthiness.
- Prove completeness against HEAD: added vs removed keys map 1:1 to your migration list, and no key flips `in_bib` True→False. The "Dropped from bib" list is a standing total, not the delta. Then prune stale FTS rows and `index.py --rebuild`.

### A refresh that renames many keys at once: match by identity, finish with a duplicate audit
When a refresh drops and adds keys by the hundred, the transform is not uniform (not simply `+a`). Match each dropped orphan with artifacts to a new blank key by DOI, else by (first author, title slug, year), and migrate as above with `os.rename` then `git add` (git detects identical-content renames). `-b`/`-c` orphans are distinct papers; never migrate them onto `base+a`. Suffix gaps (a `d` key with no `a`) are normal.
Then audit duplicates by git blob OID across suffix families using `git -c core.quotepath=false ls-files -s`; without `quotepath=false`, non-ASCII paths are octal-quoted and a `startswith("pdfs/")` guard skips every diacritic key. When the data agent supplies a rekey mapping, still reconcile by DOI: the string target can collide with an already acquired key holding the same paper (delete the duplicate rather than rename onto it).

### Rewriting manifest paths: `json.dumps` escapes diacritics
A migration that rewrites `pdf_path`/`markdown_path` by string-replacing `/{old}.` in `json.dumps(entry)` silently skips diacritic keys: `json.dumps` defaults to `ensure_ascii=True`, so the path reads `\uXXXX` and the old key never matches. The entry keeps stale paths and shows as missing. Pass `ensure_ascii=False` (or set the two path fields directly), and after any migration assert that each new key's `pdf_path` and `markdown_path` exist on disk and contain the new key.

### Dropped-from-bib orphans are often missed splits
A key under "Dropped from bib (kept, in_bib=False)" may be a split done in an earlier refresh whose artifact migration was never done (the split keys are not in this run's "New keys"), so the bare key holds the PDF while the split key shows as missing. For each such orphan: `grep -oiE "\{${k}[a-z]," ../data/dist/references.bib`. A sibling existing is necessary, not sufficient: it can be a different paper, and then the bare key stays an orphan. Match by markdown body.

### Three reasons a key is in_bib=False, and how to report each
All look identical here (`in_bib=False`, blank citation), but the summary line differs. (1) Placement-reference-only: the data repo's cladogram pass pulls in papers used only during phylogenetic placement, which never enter `references.bib`; these stay orphans, so a rising "Dropped from bib (kept)" count at `/refresh-bib` is not an anomaly. (2) Bib propagation genuinely pending. (3) Queued behind the user's sign-off checklist on the data side: the data-repo agent does not touch genus YAML or mint store entries until the user annotates the batch checklist. Report (3) as "queued behind sign-off"; don't chase or re-flag it. In every case, still process, convert and index the orphan.

### Bib entry types: theses, books, @misc
`references.bib` types each entry: `@phdthesis`/`@mastersthesis` with `school` (habilitation adds `type = {Habilitation thesis}`), `@book` with `publisher`, `@misc` for a work with no publisher, and a dissertation abstract (avtoreferat) as `@misc` with `howpublished = {Dissertation abstract, <school>}`. Anything that reads `journal or booktitle` comes up blank for theses and books. Use `corpus.bib.citation_venue()` for a display/search venue and `corpus.bib.is_thesis()` to detect a thesis.

### The incremental indexer re-indexes on citation or genera[] changes
The FTS `title`, `authors`, `journal` and `genera` columns come from the manifest. The indexer compares each key's stored metadata row with the manifest as well as the markdown's mtime+size, so plain `index.py` after `/refresh-bib` is enough. `--rebuild` is the fallback if a key looks wrong.

### After a bulk rename, run `index.py --rebuild` and verify
`git mv`/`os.rename` preserve mtime and size, so the incremental indexer can skip a renamed file after its old row was pruned, and some new keys end up missing from the index. For bulk renames use `--rebuild`; for one key, `touch markdown/{newkey}.md` and run the incremental indexer. Either way, query the index for every new key and confirm no old-key rows remain.

## Manifest and held state

### Edit the manifest only through corpus.manifest
Scripts write through `corpus.manifest.commit()`: under an flock on `manifest.json.lock` it re-reads the file and applies only the fields of the entries that script changed, so marker, a vision run, a fetcher and an inline edit can overlap. Two writers changing the same field of the same entry end with the later one's value. Make inline edits the same way: `from corpus import manifest; manifest.update(lambda m: m[key]["conversion"].update(...))`, which takes the lock and writes in the scripts' format (`json.dumps(data, indent=2, sort_keys=True, default=str)`, `ensure_ascii` left True, no trailing newline). A plain `json` read and write is not protected: it can be lost to a script's commit or undo one, and with `ensure_ascii=False` it un-escapes every `\uXXXX`, turning a three-line edit into a diff of over a thousand lines. After any inline edit run `git diff --stat manifest.json` and confirm the size matches the intended change. Reverting with `git checkout manifest.json` also discards legitimate script writes from earlier in the session; re-run those scripts afterwards. If an inline patch recurs, make it a script.

### bool(entry["acquisition"]) is not a "held" test
Cladogram-pass shells carry an acquisition dict whose fields are all null, which is truthy. Test `acquisition.pdf_path`, or the existence of `pdfs/{key}.pdf` and `markdown/{key}.md`.

### Duplicate keys: audit by content hash, not DOI
Stub-orphans have `doi=None`, which is exactly what a duplicate looks like, so a DOI audit misses them. md5 every `pdfs/*.pdf` and `markdown/*.md` (skip `.original.md`) and group.
Causes: online-first vs print year (each file's citation line prints the other key's year), and a Chinese given name read as the surname (journals that print the surname first in capitals, "XING XU").
Keep the key that is `in_bib=True`, has `genera[]`, and appears in a genus/clade YAML (`grep -rl {key} data/genera data/clades`). Removal touches: `git rm` PDF and markdown, the manifest entry, `DELETE FROM papers` in `index/papers.db` (the indexer does not remove rows for deleted files), and any `.runtime/cache/*/{key}`, `pdf_images/{key}`, `.original.md`. Then grep docs/notes for the deleted key.

### Two keys on one volume with contiguous text may both be right
An appendix or note is often registered separately with its own DOI, so two keys in the same volume with overlapping page ranges can look like one paper split in two. Before proposing a merge, read both store entries in the data repo (`references/{x}/{key}.yml`); a differing DOI suffix on the same volume means a separate component.
A truncated PDF can be masked by a second key that holds the missing pages: check that each PDF ends where its citation's page range ends.

### `genera[]` includes supporting citations
A manifest `genera[]` entry means a genus YAML cites the paper, not that the paper describes the genus; genera cite validity, comparative and synonymy papers too. A subject/genera mismatch is not a wrong cite. Citations are owned by the data repo: raise it as a question at most.

## Environment and shell

### Where the data repo and the bib are found
`corpus.config.data_repo()` (OPEN_PALEO_DATA, else `../data` beside the archive) and `corpus.bib.bib_path()` (CORPUS_BIB_SOURCE: sibling, bundled or remote). Don't hard-code a path to the data repo. For "is this key held?", call the owning script's predicate rather than writing a new one (path recorded AND file on disk, or non-empty markdown, or a live `bhl_match`).

### Environment: no `timeout` command; pillow-heif must stay below 1.0
macOS has no `timeout`/`gtimeout` by default; `timeout N cmd` fails as command-not-found and the wrapped command never runs. Use the Bash tool's `timeout` parameter.
`requirements.txt` pins `pillow==10.4.0` and `pillow-heif==0.22.0`. An unpinned `pip install pillow-heif` pulls 1.x, which upgrades Pillow to 12.x and breaks surya (marker requires `pillow>=10.2.0,<11.0.0`). Restore with `pip install 'pillow>=10.2.0,<11.0.0' 'pillow-heif<1.0'`.

### zsh does not word-split $VAR
The shell is zsh. `KEYS="--key a --key b"; script $KEYS` passes ONE argument; argparse lists every flag as unrecognized, which looks like a parsing bug. Use `${=KEYS}` or inline `$(...)`. `for p in $PAIRS` with `PAIRS="a:b c:d ..."` iterates once over the whole string, so `${p%%:*}` is the first old key and `${p##*:}` the last new key: a rename loop moves the first file onto the last target and nothing else, while a Python step parsing the same list does it correctly, leaving manifest and files in disagreement. Drive file moves from Python (`subprocess.run(['git','mv',a,b])` with an exists/not-exists assert per move), use a zsh array (`pairs=(a:b c:d); for p in $pairs`), or a heredoc into `while IFS=: read -r old new`. Afterwards check each new key's markdown against its bib title, and delete the index rows for both old and new keys before reindexing. Test long flag lists with a cheap run before a paid background job.

### Corpus-wide greps double-count translated papers
`markdown/{key}.original.md` is the pre-translation backup. Exclude it: `grep -roh -i TERM markdown/*.md --exclude='*.original.md'`, or dedupe by key. The inflation concentrates on single translated sources. Report the query with the count.

### Fetchers run over every missing key unless given `--key`/`--keys-file`
A bare fetcher run processes every missing key and writes failed-fetch markers into their entries (recovery: kill it, `git checkout -- manifest.json`). Pass `--key` or `--keys-file`; `--help` is safe. Don't chain unfamiliar pipeline scripts with `;`: killing one starts the next.

### Background jobs: an empty output buffer means still running
Confirm a background task has exited (completion notification or `Done in`/exit text) before drawing conclusions. Never start a second run on a key while one is in flight: two concurrent paid runs (translation, vision) both bill, and the slower one overwrites the faster. Check `ls -l` on the target or the process list first.

### Project skills need the directory layout
Skills must be `.claude/skills/{name}/SKILL.md`. A flat `.claude/skills/{name}.md` silently fails to load (`/reload-plugins` reports fewer skills; invoking gives "Unknown command"). Read the skill count in `/reload-plugins` output after adding one. Agents (`.claude/agents/{name}.md`) are single-file.

### Promote repeated inline scripts into `scripts/`
Prefer real scripts over heredocs re-run every iteration. Promote code that repeats, not one-offs. Conventions: argparse, positional `KEY ...` plus `--keys-file` with `#` comments, atomic writes through `corpus.io` and `corpus.manifest` for manifest changes, summary output at the end; update the SKILL.md to call the script.
