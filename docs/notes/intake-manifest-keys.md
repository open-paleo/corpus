# Intake, manifest and citation-key notes

Gotchas for `/intake`, `/refresh-bib`, manifest edits and key handling, plus shell and environment traps on this machine setup.

## Intake

### Check page 1 of every new PDF against its key before converting
`intake_discover.py` never compares content to the key's citation, so a misfiled PDF looks like a normal intake and converting it overwrites the slot while the real paper stays missing.
- In-bib keys: compare the first-page title/authors (`get_text_bounded()[:250]`) with `manifest[key].citation`. `barrett2010.pdf` was Yates & Barrett 2010 (BZN Massospondylus neotype petition), not Barrett's "Dinosaurs of Dorset II"; refiled as `yates2010a` (ICZN/BZN cases are keyed by lead author: paulcarpenter2010, upchurch2009).
- Same author + same year is a red flag, not a confirmation: Barrett, Kear & Benson 2010 (Alcheringa 34) dropped as `barrett2010a` was the requested paper but the wrong key (both cited by *Kakuru*); refiled to `barrett2010z` (now `barrett2010b`). The backstop signal is an unexpected key disappearing from `corpus status --missing` at the end of intake.
- Orphans have no citation to contradict; check domain and siblings. `zheng2021b` was a JSTOR wrong-download (a Portuguese literature essay) next to sibling zheng2021a on *Caudipteryx* cartilage.
On mismatch, stop and ask; don't pick a destination key yourself.

### A split-key collision has so far always been a duplicate of the `a` sibling
When `scripts/intake_discover.py` prints `⚠ SPLIT-KEY COLLISION` (bare `foo2015` next to `foo2015a/b`), DOI-check the bare PDF against the siblings' `citation.doi` before accepting the suggested `…c` rename. Every case so far was a duplicate: riguetti2022 = riguetti2022a, barrett2024 = barrett2024a, müller2018 = müller2018a, wang2017 = wang2017a. The right action was to delete the never-acquired bare PDF (confirm with the user, citing the matching DOI). Rename to the next letter only if it is a genuinely new paper.

### An orphan can duplicate a held sibling or the real bib key; the higher-resolution scan wins
`intake_discover.py` does not flag a distinct-letter orphan in an existing split family. `huene1927a` matched the already-held `huene1927b` (Mem. Queensland Mus. pp. 121–126) and had a worse text layer; discarded. A `z` orphan can duplicate the bib key too: `harrison1975z` was already held as `harrison1975a`. Search the bib by title/DOI, not key. To choose between copies compare embedded image dimensions (`fitz.open(p)[0].get_images(full=True)`), not file size: harrison1975z was 864×1173 (~110 DPI) against the incumbent's 2296×3312 (~290 DPI). Also check the incumbent markdown for plates, references and addresses before assuming a re-drop improves it.

### Don't judge an orphan's identity from the head of its markdown
Nature/Science single-article reprints start mid-page, so the markdown often opens with the tail and references of the previous article. `dalsasso1996` was wrongly declared a misfile (head was B. E. Lyon's brood-parasitism paper); the Scipionyx paper started ~44 lines down. Grep the whole body for the expected title before calling a misfile. A same-author orphan with a wrong year (even 2 years off) is a candidate for a newly-surfaced missing bib key; rename-migrate it. After re-keying, cut everything above the `##` title.

### Citation-only keys: keep the PDF, skip conversion
When the user says a paper is citation-only, (1) `intake_acquire.py {key}` (sets `source=manual`), (2) set `conversion.skipped=true`, `skipped_reason`, `skipped_at`, `tool=null` atomically, (3) commit the PDF. Committing matters: `intake_discover.py` flags `(disk - acquired) | untracked | modified`, so a tracked, acquired PDF never resurfaces. Precedent: `bruns2015a` (308-page thesis, CID-garbage text layer, cited only for Denversaurus validity).

The same works for a supplementary file that isn't worth converting (map sheets, fold-out plates): on `manifest[parent]["supplementary"][name]` set `skipped: true`, `tool`, `markdown_path` and `converted_at` to null, and say why in `notes` ("Stored without OCR by request"). Commit the PDF and no markdown; `intake_discover.py` then stops listing it. Precedents: `evenchick2001a-map`, the `badgley1952a` fold-outs, and `leanza1997a-map` and `nullo2006a-map` (2026-10-07; their empty placeholder markdown was removed).

### Check the key is free before writing an extracted PDF
Writing `pdfs/{key}.pdf` silently overwrites an existing file, and `intake_acquire.py` then rewrites the entry. `sun1992a` (the whole 278-page book, citation-only) was overwritten by a 5-page section and restored from git. Before any extraction, check `ls pdfs/{key}.pdf` and the manifest. A section of a book already held goes in as `supplementary/{key}-{name}.pdf`.

### Plate inserts change the cited→PDF page offset mid-chapter
In bound volumes (USGS Professional Papers, Palaeontographica, society memoirs) unnumbered plate leaves are bound between text pages, so an offset computed at the chapter start truncates the chapter cleanly. Before `/extract-pages`, print each candidate page's char count and first 110 chars and read the printed folios; plate pages have `len` ~150–500 and a head like `PROFESSIONAL PAPER 210 PLATE 5`, blank versos `len=0`. Find the next chapter's title page for the end. `gilmore1946b` (USGS PP 210-C, cited pp. 29–53): offset 17 at the start, 21 after Plates 3–4; the correct range was PDF 44–84, not 46–70.

### A direct manifest edit setting `vision_ocr_needed` must also set `acquisition.source="manual"`
`intake_acquire.py --vision-ocr` sets both. Editing only `conversion` (done to preserve a hand-written `acquisition.notes` that `intake_acquire` would clobber) leaves `source=null`, and `intake_discover.py` re-surfaces the key as new on every run (precedent `abel1919a`).

### Record the DOI on a stub-orphan at intake
Stub-orphans (`{key}z`, blank citation) are matched to candidate rows by fuzzy title because `cite_match.holds()` has no DOI to try. Companion papers then collide: `choi2019z` was ticked HELD for the Lee et al. 2010 row (`lee2010b`). When the PDF prints a DOI, write it to `manifest[key]["doi"]` (and `acquisition.acquired_for` when it answers a candidate row), then check that each newly HELD row in the annotate diff names the key just intaken.

### A stub-orphan under an `a` key can be claimed by the bib for a different paper
`morrison2026a` was taken in as a stub-orphan (`in_bib=False`) for the dental microwear paper (Anat. Rec., `bab34ca`, July 2026). The bib then assigned `morrison2026a` to the megaraptoran paper (rsos.260937). The October intake checked the held file, saw the microwear paper, and filed the megaraptoran under `morrison2026z` with the note "not morrison2026a (dental microwear)" (`dbbc725`). The manifest's `citation.title` and `doi` for `morrison2026a` already named the megaraptoran. Before choosing a `z` key, compare the new paper's DOI and title with the bib citation of each same-stem key, not with the held file's contents. A held file that disagrees with its own key's bib citation is a mis-filed stub, so move it to `z`. Swapped 2026-10-28.

### Never `git add -A pdfs/` or `markdown/`
`pdfs/` is the user's drop zone and may hold unkeyed PDFs for a later intake. `git add -A pdfs markdown` once staged and pushed three raw-named downloads (257a495, undone in 642903f). Stage explicit paths (`git mv` already stages renames) and check `git status --short` for unintended `A ` entries before committing.

## Citation keys

### Data-repo key/year policy lives in its CONTRIBUTING.md
Section "Reference Keys and Years" (upstream 7f4920b6, 2026-09):
- Use the year the work first became readable; online release counts. Online 2013 / print 2014 → 2013; published May 2025 in an issue labelled 2024 → 2025.
- Tiebreak: follow what other papers' reference lists print, not running-text mentions. If no year dominates, keep the key and fix the prose.
- Key form: surname lowercased + year + letter, keeping diacritics and hyphens (`maryańska1975a`, `prieto-márquez2022b`), removing spaces (`geoffroysaint-hilaire1825a`).
- Letters follow the order entries were added, so `a` is not necessarily the earliest paper.
- Keys are never aliased. A year change arrives here at `/refresh-bib` as a dropped-key/new-key pair, never an in-place edit.

### Key years: first-online year, read off Crossref, confirmed by DOI
For a paper that reaches the corpus before the bib (stub-orphans), key it by the online-publication year even when the print issue differs, matching the data repo's policy (Cerroni et al., *Skorpiovenator* neuroanatomy, J. Anat. 240(4), online 2020-06-22, print 2022 → `cerroni2020b`, since `cerroni2020a` was taken). Query `https://api.crossref.org/works/<DOI>` for `published-online`, `published-print`, `issued` (`10.1002/ar.25045` online 2022-08 → `scott2022a`; but `10.1002/spp2.1004`, online 2014-11, was keyed `poropat2015a` by the bib, so an orphan's online-year key can still be overridden). The literature-count tiebreak applies only when no clean online date exists (Norman 2014/2015, Rauhut 1998/2000). Read the DOI off the PDF; never guess one (a plausible `10.1002/ar.24875` resolved to an unrelated cardiology paper). Tell the user when corpus citations mostly use the print year.
The bib may still key the print year, so `/refresh-bib` can show a drop + new-key pair differing by one year (`penkalski2017` → `penkalski2016`, DOI 10.1016/j.cretres.2016.10.004); treat it as a year-correction migration and confirm by DOI. A rename can cascade through a batch, so check what wants the vacated key. The year printed inside the markdown may be a third value (penkalski2013a prints 2014). To verify a renamed stub's body, read `index/papers.db` directly: `search.py --key` shows blank title/year for stub-orphans.

### Orphan keys: `{surname}{year}z` with diacritics, descending letters on collision
Papers not in the upstream bib get `{surname}{year}z` using the diacritic surname (`müller2018z`), keeping a/b/c free for real bib entries. A second orphan of the same author-year takes `y`, then `x`; never composites like `2005az` (`vanitterbeeck2005z` taken → `vanitterbeeck2005y`; both have since been re-keyed by the bib). Rename only the newcomer. Before `mv` into an orphan key, check `ls pdfs/{surname}{year}*` and the manifest: a tracked, clean file is invisible to `git status`, and a `mv` onto it silently clobbers a committed PDF (recover with `git checkout -- pdfs/{key}.pdf`). When the bib later adds the paper it arrives under its own key and is not auto-linked to the `z` orphan.
Exception: an orphan the user drops under an `a` suffix is deliberate; it means the bib entry is coming soon. Don't flag or rename it unless it collides; still check the bib by DOI/title (`colbert1969a` was really `colbertrussell1969a`).

### Resolving a citation key to a corpus file: exact match or a↔z only
Registry citations use the `a`/bib form while the corpus may hold the work under `…z`. Always check both (`for k in (key, key[:-1]+'z')`, in the manifest and `markdown/`) before reporting a key unheld: `merewether1979a` was reported never-attempted while `merewether1979z` had been held for three days. On 2026-09-20, 6 of 163 noteless registry ids were held only under `z`: dixey1928z, he2004z, huang2005z, matsumoto1979z, sohn1979z, tumpeesuwan2010z (all since migrated to their bib keys).
Never match a↔b↔c (different works by construction) and never diacritic-fold: a first resolver mapped `galton1977b → galton1977a` and both `wang2001b` and `wang2001c` → `wang2001a`; `suárez-gonzález2015a` and `suarez-gonzalez2015a` are different papers. Folding and stem matching are fine for finding candidates, not for concluding identity; confirm from the document's first line.
For bulk reconciliation, gate on "store key ends in `a` AND corpus holds exactly `<stem>z` AND no other suffix": 55 of 72 same-stem candidates passed on 2026-09-18. Don't rename orphans pre-emptively while the data repo's ymls are uncommitted (diacritic or ordering changes can still shift the suffix); let `/refresh-bib` do it. Also: the data repo's `scripts/build.ts` writes the whole reference store to `dist/references.bib` unfiltered; the genus/clade scope filter is ours, in `scripts/refresh_manifest.py`.

### Normalising a key set: assert the fold is injective first
Before applying any key normalisation (accent folding, suffix stripping), apply it to every key and assert the output set has the same size. If it shrinks, print the colliding groups and stop. Cases: an orphan resolver mapped `bonaparte1984c`, `galton1977b`, `lucas1993b` to their `a` keys and both `wang2001b` and `wang2001c` to `wang2001a`; an accent fold merged `suárez-gonzález2015a` and `suarez-gonzalez2015a`, which are different papers. Only `a`<->`z` on the same stem is a legitimate orphan mapping.

### Character classes like `[a-zà-ÿ]` silently drop real keys
`[a-z]` drops every diacritic key (`agnolín2010a`, `gradziński1977a`); `[a-zà-ÿ]` looks accent-safe but stops at U+00FF and drops `ősi2005a`, `ősi2010a/b`, `ősi2012a` (ő is U+0151), so a count came back 299 instead of 297. Accent folding merges distinct papers (`suárez-gonzález2015a` vs `suarez-gonzalez2015a`). Walk parsed YAML/JSON instead of regexing serialized text; if you must match, use `\w` (Unicode by default in Python 3). Always check the denominator: count keys the class failed to match.

### The bib's key normalisation is unpredictable, even within one author
Observed: `kräusel1922` -> `krausel1922`, `słowiak2021` -> `slowiak2021`, but `müller2018a` keeps the ü; `santa-luca1980` -> `santaluca1980`; `o'connor2005` -> `oconnor2005`; Russian soft-sign apostrophes are dropped (Belenky, Nessov, Rozhdestvensky); given-name keys get re-keyed to the surname (`lida2009` -> `xing2009`). Prieto-Márquez has both `prieto-márquez2005/2010/...` and `prietomarquez2011`, and the 2011 key oscillated across refreshes (`prieto-márquez2011` -> `prietomarquez2011` -> `prieto-marquez2011`).
An accent can be the disambiguator: `prieto-márquez2013` and `prieto-marquez2013` are different papers (DOIs ...0069835 vs ...0082268). Match by DOI, never by key string.
ł/l and ä/a are distinct codepoints, so `intake_discover.py`'s NFC normalisation does not match them. Check the actual bib/manifest key before naming a dropped PDF; if a "missing" PDF has a diacritic-named twin on disk, rename it rather than re-acquiring. Expect to re-migrate the same artifact on a later refresh.

### Diacritic key corrections can shift the a/b suffix
When a refresh corrects ASCII keys to diacritic ones (agnolin -> agnolín, maryanska -> maryańska, osmolska -> osmólska), the same-letter diacritic key may be a different paper. `agnolin2021a` (alvarezsaurid osteology, 10.1080/08912963.2021.1929203) belongs on `agnolín2021b`; `agnolín2021a` is a different paper (10.1080/02724634.2021.2002348).
Match every ASCII orphan with artifacts by DOI first, title second, reading the markdown (a dropped orphan's manifest citation is blank). Don't rely on the newly-dropped list alone; compute all `in_bib=False` orphans whose de-accented name or DOI collides with an `in_bib` key and flag where the two targets differ. Coincidental collisions exist: orphan `muller2018a` (Chilesaurus comment, rsbl.2017.0581) is not `müller2018a`.

## Bib refresh

### refresh_manifest.py: run once, diff against HEAD
The script applies everything on the first run; a second run prints all zeros. Capture full output once (the skill uses `tee`). If the first output was lost: `git checkout HEAD -- manifest.json`, then run exactly once. For the net effect, compare `git show HEAD:manifest.json` with the working copy (added/removed keys, `in_bib` flips). The raw diff is huge because every entry's `bootstrapped_at` is re-stamped; filter it out.

### Mass bib refresh with near-total renaming: match by identity, finish with a duplicate audit
Upstream data #1946 moved to no bare keys (`name2000` -> `name2000a`): ~1413 new and ~1525 dropped keys in one refresh. The transform is not a uniform `+a`.
1. Match each dropped orphan with artifacts to a new blank key by DOI, else by (first author, title slug, year). New keys carry a placeholder acquisition dict with all-None fields, so test `.get("source")`, not truthiness.
2. Move artifacts with `os.rename` then `git add` (git detects identical-content renames); move pdf, md, `.original.md`, `.runtime/cache/vision/{key}`. Then `indexer.py --rebuild`.
3. Migrate state fields (`acquisition, conversion, index, annotations, availability, paper_watch`, any `bhl*`) onto the bib-derived entry; fix `pdf_path`/`markdown_path`; delete the old entry.
4. `-b`/`-c` orphans (`butler2008b`, `naish2004b`, `sues2009b`) are distinct papers; never migrate them onto `base+a`. Suffix gaps (`benson2009d` with no `a`) are normal.
Then audit duplicates by git blob OID across suffix families using `git -c core.quotepath=false ls-files -s`; without `quotepath=false`, non-ASCII paths are octal-quoted and a `startswith("pdfs/")` guard skips every diacritic key (this hid `fernández2022a` == `regaladofernández2022a`). When the data agent supplies a rekey mapping, still reconcile by DOI: the string target can collide with an already acquired key (`brusatte2013a` == `brusatte2012a`, delete the duplicate rather than rename onto it).

### Year renames and caught-up z-orphans at /refresh-bib
The data repo never aliases keys, so a year/surname correction (`norman2014a`→`norman2015a`, `rauhut1998a`→`rauhut2000a`, `penkalski2016a`→`penkalski2017a`, `nesov1992a`→`nessov1992a`) and a `z`-orphan the bib finally caught up with (`tang2001z`→`tang2001b`, `currie1993z`→`currie1993d`) both appear as a dropped/new pair. Tell for a correction: the orphan's `year` field already disagrees with its key. Verify each pairing by DOI, else by markdown body (many z-orphans have `title='?'`). Check the destination path is free (a tracked file there is invisible to `git status` and `mv` clobbers it). Move `.runtime/cache/vision/{old}` too. Prove completeness against HEAD: added vs removed keys map 1:1 to your migration list, and no key flips `in_bib` True→False. The "Dropped from bib" list is a standing total (235 in Sept 2026), not the delta. Then prune stale FTS rows and `indexer.py --rebuild`.

### Migrating z-orphans onto their new bib keys
`refresh_manifest.py` does not migrate orphans: it adds the new bib keys as unacquired entries and leaves the `z` orphans in place (commit d488637 moved 76). Match by whether the bib title appears as a contiguous run in the markdown BODY, not by the first heading (that catches journal banners and translated titles) and not by author+year. Move per pair: PDF, markdown, `.original.md` backups, vision caches, supplementary PDF+markdown (rewriting inner paths), and manifest `acquisition`, `conversion`, `supplementary`, `annotations`, `bhl_match*`, `closed_access`. Targets are not always `a` (`averianov2021z`→`e`, `bell2019z`→`b`, `eberth1993z`→`b`, `xu2015z`→`b`). Finish with `indexer.py --rebuild`.

### Rewriting manifest paths: `json.dumps` escapes diacritics
A migration that rewrote `pdf_path`/`markdown_path` by string-replacing `/{old}.` in `json.dumps(entry)` silently skipped `marjanović2021a`: `json.dumps` defaults to `ensure_ascii=True`, so the path read `marjanovi\u01072021z` and the old key never matched. The entry kept its stale paths and showed as missing. Pass `ensure_ascii=False` (or set the two path fields directly), and after any migration assert that each new key's `pdf_path` and `markdown_path` exist on disk and contain the new key.

### zsh does not word-split `$VAR`: a pair loop over a string runs once
The shell is zsh. `for p in $PAIRS` with `PAIRS="a:b c:d ..."` iterates ONCE over the whole string, so `${p%%:*}` is the first old key and `${p##*:}` the LAST new key. On 2026-10-29 an 11-pair migration moved `maleev1954z`'s PDF and markdown onto `zhao2011a`, moved nothing else, and indexed the Maleev text under `zhao2011a`. The Python manifest step parsed the list correctly, so manifest and files disagreed. Drive the file moves from Python (`subprocess.run(['git','mv',a,b])` with an exists/not-exists assert per move), or use a zsh array (`pairs=(a:b c:d); for p in $pairs`). Afterwards check each new key's markdown against its bib title, and delete the index rows for both old and new keys before reindexing.

### Renaming migrated keys in notes: skip old -> new tables
After a refresh migrates orphans, a blanket replace of old keys across `docs/` also rewrites the tables that record the mapping itself: `docs/notes/handover-2185/*/MAPPING.md` rows like `` `carr2022z` | `carr2023a` `` became `carr2023a | carr2023a` (caught and reverted in dd59a2e). Exclude `handover-*/` and review the diff for any line where the old key sits next to its new key. Keys used as examples of a spelling issue (`suárez-gonzález2015a` in this file) also keep their old form.

### Dropped-from-bib orphans are often missed splits
A key under "Dropped from bib (kept, in_bib=False)" may be a split done in an earlier refresh whose artifact migration was never done (the split keys are not in this run's "New keys"). Shipped once with `wang2017` (Xingxiulong) and `bakker1988` (Nanotyrannus) holding the PDFs while `wang2017a`/`bakker1988b` showed as missing. For each such orphan: `grep -oiE "\{${k}[a-z]," ../data/dist/references.bib`. A sibling existing is necessary, not sufficient: `seeley1869` has a `seeley1869b` sibling that is a different paper, so it stays an orphan. Match by markdown body.

### Three reasons a key is in_bib=False, and how to report each
All look identical here (`in_bib=False`, blank citation), but the summary line differs. (1) Placement-reference-only: the data repo's cladogram pass (from July 2026) pulls in papers used only during phylogenetic placement, which never enter `references.bib`; these stay orphans, so a rising "Dropped from bib (kept)" count at `/refresh-bib` is not an anomaly (in one refresh only `zanno2010` flipped while ~14 recent orphans stayed False). (2) Bib propagation genuinely pending. (3) Queued behind the user's sign-off checklist on the data side: the data-repo agent does not touch genus YAML or mint store entries until the user annotates the batch checklist (confirmed for `medeiros2015a`, 2026-08-27). Report (3) as "queued behind sign-off"; don't chase or re-flag it. In every case, still process, convert and index the orphan.

### The incremental indexer re-indexes on citation or genera[] changes
The FTS `title`, `authors`, `journal` and `genera` columns come from the manifest, and `scripts/indexer.py` used to skip files by markdown mtime+size alone. A bib refresh that changed only metadata left the index stale (after one refresh, `search.py --genera Gryposaurus` found `lambe1914b` but not `gates2007b`). Since 2026-10-02 the indexer also compares each key's stored metadata row with the manifest and re-indexes on any difference, so plain `indexer.py` after `/refresh-bib` is enough. `--rebuild` is still the fallback if a key looks wrong.

### Bib entry types since data schema 1.10.0: theses, books, @misc
From data commit 96078d295 (2026-10-02) `references.bib` types each entry: `@phdthesis`/`@mastersthesis` with `school` (habilitation adds `type = {Habilitation thesis}`), `@book` with `publisher`, `@misc` for a work with no publisher, and a dissertation abstract (avtoreferat, e.g. `khand1987a`) as `@misc` with `howpublished = {Dissertation abstract, <school>}`. Before that, standalone books were journal-less `@article`, publisher was never exported, and two theses wrote the degree into `booktitle` (`hernán2018a`, `stewart1978a`). `parse_bib()` keeps every field, so nothing breaks at refresh, but anything that reads `journal or booktitle` comes up blank for theses and books. Use `corpus.bib.citation_venue()` for a display/search venue and `corpus.bib.is_thesis()` to detect a thesis. The first refresh after the change reported ~239 citation-metadata changes, mostly `entry_type` and `publisher`; that is expected, not a wrong-paper signal.

### After a bulk rename, run `indexer.py --rebuild` and verify
`git mv`/`os.rename` preserve mtime and size, so the incremental indexer can skip a renamed file after its old row was pruned: 36 renames gave `added: 31`, five keys missing. For bulk renames use `--rebuild`; for one key, `touch markdown/{newkey}.md` and run the incremental indexer. Either way, query the index for every new key and confirm no old-key rows remain.

## Manifest and held state

### Inline manifest edits: go through corpus.manifest
Edit with `from corpus import manifest; manifest.update(lambda m: m[key]["conversion"].update(...))`. It takes the write lock, re-reads the file and writes in the scripts' format (`json.dumps(data, indent=2, sort_keys=True, default=str)`, `ensure_ascii` left True, no trailing newline). A hand-rolled `json.dumps` with `ensure_ascii=False` un-escapes every `\uXXXX` and turned a 3-line edit into 1,241 insertions / 1,235 deletions. After any inline edit run `git diff --stat manifest.json` and confirm the size matches the intended change. Reverting with `git checkout manifest.json` also discards legitimate script writes from earlier in the session; re-run those scripts afterwards. If an inline patch recurs, make it a script.

### Concurrent manifest writes (locked since 2026-10-31)
Until 2026-10-31 every script read the manifest at start and wrote the whole file at the end, so the last writer silently reverted everything written in between (molina-pérez2019/2020: stamped `tool=claude-vision` at 12:30, reverted by marker's final write at 12:44). Scripts now write through `corpus.manifest.commit()`: under an flock on `manifest.json.lock` it re-reads the file and applies only the fields of the entries that script changed, so marker, a vision run, a fetcher and an inline `manifest.update()` can overlap. Two writers changing the SAME field of the SAME entry still end with the later one's value, which is the intended result. An edit that bypasses `corpus.manifest` (plain `json` read and write) is not protected: it can be lost to a script's commit, and it can undo one.

### bool(entry["acquisition"]) is not a "held" test
Cladogram-pass shells carry an acquisition dict whose fields are all null, which is truthy (the four spare `sereno1986*` keys were reported HELD this way). Test `acquisition.pdf_path`, or the existence of `pdfs/{key}.pdf` and `markdown/{key}.md`.

### Duplicate keys: audit by content hash, not DOI
Stub-orphans have `doi=None`, which is exactly what a duplicate looks like, so a DOI audit misses them. md5 every `pdfs/*.pdf` and `markdown/*.md` (skip `.original.md`) and group. Four removed 2026-09-09: `gianechini2020a`, `penkalski2017a`, `han2017a`, `xing2010a`.
Causes: online-first vs print year (each file's citation line prints the other key's year), and a Chinese given name read as the surname (`xing2010a` duplicated `xu2010b`; Zootaxa prints "XING XU").
Keep the key that is `in_bib=True`, has `genera[]`, and appears in a genus/clade YAML (`grep -rl {key} data/genera data/clades`). Removal touches: `git rm` PDF and markdown, the manifest entry, `DELETE FROM papers` in `index/papers.db` (the indexer does not remove rows for deleted files), and any `.runtime/cache/*/{key}`, `pdf_images/{key}`, `.original.md`. Then grep docs/notes for the deleted key.

### Two keys on one volume with contiguous text may both be right
An appendix or note is often registered separately with its own DOI. `seeley1881a` and `suess1881a` (QJGS 37, 1881) look like one paper split across two keys, but the Lyell Collection DOIs differ (`...037.01-04.49` vs `...50`). Before proposing a merge, read both store entries in the data repo (`references/{x}/{key}.yml`); a differing DOI suffix on the same volume means a separate component. Verified page ranges: `seeley1881a` 620-707, `suess1881a` 702-703.
A truncated PDF can be masked by a second key that holds the missing pages (`seeley1881a`'s PDF stopped mid-sentence inside Suess's appendix; fixed in commit 981c210).

### `genera[]` includes supporting citations
A manifest `genera[]` entry means a genus YAML cites the paper, not that the paper describes the genus; genera cite validity, comparative and synonymy papers too (`sotoacuna2024`, an *Antarctopelta* reappraisal, is cited by Denversaurus on purpose). A subject/genera mismatch is not a wrong cite. Citations are owned by the data repo: raise it as a question at most.

## Environment and shell

### Where the data repo and the bib are found
`corpus.config.data_repo()` (OPEN_PALEO_DATA, else `../data` beside the archive) and `corpus.bib.bib_path()` (CORPUS_BIB_SOURCE: sibling, bundled or remote) replaced the hard-coded home-directory paths to the data repo, which worked only because macOS volumes are case-insensitive. For "is this key held?", call the owning script's predicate rather than writing a new one (path recorded AND file on disk, or non-empty markdown, or a live `bhl_match`).

### Environment: no `timeout` command; pillow-heif must stay below 1.0
macOS here has no `timeout`/`gtimeout`; `timeout N cmd` fails as command-not-found and the wrapped command never runs. Use the Bash tool's `timeout` parameter.
`requirements.txt` pins `pillow==10.4.0` and `pillow-heif==0.22.0`. An unpinned `pip install pillow-heif` pulls 1.x, which upgrades Pillow to 12.x and breaks surya (marker requires `pillow>=10.2.0,<11.0.0`). Restore with `pip install 'pillow>=10.2.0,<11.0.0' 'pillow-heif<1.0'`.

### zsh does not word-split $VAR
The shell is zsh. `KEYS="--key a --key b"; script $KEYS` passes ONE argument; argparse lists every flag as unrecognized, which looks like a parsing bug. Use `${=KEYS}` or inline `$(...)`. `for p in $pairs` iterates once over the whole string (it renamed `allain2002z` to `sues1997a` in a rename loop). For pair lists, use a heredoc into `while IFS=: read -r old new`, echo one line per iteration, and test long flag lists with a cheap run before a paid background job.

### Corpus-wide greps double-count translated papers
`markdown/{key}.original.md` is the pre-translation backup (139 of 2470 files on 2026-08-28). Exclude it: `grep -roh -i TERM markdown/*.md --exclude='*.original.md'`, or dedupe by key. The inflation concentrates on single translated sources (`Yuliangze` 79 raw vs 70, mostly `godefroit2000a`). Report the query with the count.

### Fetchers run over every missing key unless given `--key`/`--keys-file`
On 2026-09-29, before the fetchers had argparse, a `--help` probe started a full run that wrote failed-fetch sentinels into 50 entries (recovery: kill it, `git checkout -- manifest.json`). They take `--help`, `--key` and `--keys-file` now, but a bare run still processes every missing key and writes sentinels. Don't chain unfamiliar pipeline scripts with `;`: killing one starts the next.

### Background jobs: an empty output buffer means still running
Confirm a background task has exited (completion notification or `Done in`/exit text) before drawing conclusions. Never start a second run on a key while one is in flight: two concurrent `translate_markdown.py` runs on `zhao1991a` both paid, and the slower one overwrote the faster ($1.05 for one translation, plus a false bug report). Check `ls -l` on the target or the process list first.

### Project skills need the directory layout
Skills must be `.claude/skills/{name}/SKILL.md`. A flat `.claude/skills/{name}.md` silently fails to load (`/reload-plugins` reports fewer skills; invoking gives "Unknown command"). Read the skill count in `/reload-plugins` output after adding one. Agents (`.claude/agents/{name}.md`) are single-file.

### Promote repeated inline scripts into `scripts/`
The user prefers real scripts over heredocs re-run every iteration (how `intake_discover.py`, `intake_acquire.py`, `intake_qc.py` came about). Promote code that repeats, not one-offs. Conventions: argparse, positional `KEY ...` plus `--keys-file` with `#` comments, atomic `tmp + rename` writes for manifest changes, summary output at the end; update the SKILL.md to call the script.

### Never stage `pdfs/` or `markdown/` wholesale in a re-key or refresh commit
On 2026-10-16 the #2282 re-key commit (fab6f58) used `git add -A ... markdown/ pdfs/` and so also committed three PDFs the user had just dropped for intake, under their download names (Bonaparte_86a.pdf, the long Ezcurra filename, dinosaursofnorth00mars.pdf). The next intake then showed them as tracked "new" files and their renames as deletions that had to be staged separately (fixed in the following commit). Stage the exact moved paths, or check `git status --short pdfs/` for untracked downloads first.
