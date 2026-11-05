---
name: refresh-bib
description: Pull the latest open-paleo bib + refresh manifest; handle key renames/splits, check the counts, commit + push
---

> **This checkout's own steps:** if `.claude/skills/refresh-bib/archive.md` exists, read it
> first. It is a private repository's layer on this skill (extra steps, local
> conventions) and is never part of the public repository; where it and this
> file differ, it wins for this checkout.

Refresh the local manifest from the upstream `open-paleo/data` bib. Resolves new keys, dropped keys, citation-metadata changes, and the recurring "split key" pattern (one citation key → two new chronologically-suffixed keys).

## 1. Pull latest bib (if remote has new commits)

Only when the bib comes from a data-repo checkout beside this one (`CORPUS_BIB_SOURCE` sibling, or auto with `../data` present; see `corpus.bib`). In remote mode `manifest_refresh.py` downloads the bib itself, and in bundled mode it reads `bib/references.bib`: skip to §2.

```bash
cd ../data      # OPEN_PALEO_DATA, if set
git fetch origin
behind=$(git rev-list --count HEAD..origin/main)
echo "behind: $behind"
```

If `behind > 0`, attempt a fast-forward pull. The repo often has unstaged local edits in `.claude/settings.local.json` and `reports/*.md` — those are user work and **must not be touched**. If `git pull --ff-only` errors due to unstaged changes:
- Confirm the bib (`dist/references.bib`) itself isn't in the modified list.
- If only `reports/` and settings are dirty, the bib is identical to upstream → skip the pull (we already have origin's bib content because the build commits are upstream and propagate the bib unchanged).
- Tell the user before stashing or otherwise touching their work.

Show the recent commits to give context for what changed:
```bash
git log --oneline -8
```

Look for telltale messages like `data: split X references into Xa/Xb`, `data: add original-description references for Y`, `fix: correct Z describing paper to ...`, `Resolve issue #NNNN wrong-described_in citations`. These hint at renames/splits/additions you'll need to handle below.

## 2. Run the manifest refresh

**⛔ NEVER pipe this script through `tail`, `head`, or any truncating filter,
and NEVER run it a second time to "re-see" the summary.** `manifest_refresh.py`
applies its changes on the FIRST run; a second run is a no-op that reports
`New entries: 0 / changed: 0`, destroying the record of what actually happened.
`tail` compounds this by hiding the summary header (New/Dropped/Changed counts)
that lives at the TOP of the output — you only see the orphan tail, panic, and
re-run. Run it EXACTLY ONCE, capture the FULL output to a file, and read the
whole thing:

```bash
./.runtime/venv/bin/python scripts/manifest_refresh.py 2>&1 | tee /tmp/refresh_out.txt
```

Then read `/tmp/refresh_out.txt` in full (the `=== Refresh summary ===` header
with New/Dropped/DOI/Citation/Genera counts is near the top; the orphan list is
at the bottom). **The authoritative record of what changed is the git diff of
the manifest, not the script's stdout** — always confirm with:

```bash
git diff --stat manifest.json
```

The script is **merge-safe**:
- Overlays bib-derived fields (`citation`, `in_bib`, `genera`, `clades`, `doi`, `year`) onto existing entries.
- Preserves `acquisition`, `conversion`, `closed_access`, `bhl_match*` blocks.
- Adds new entries (blank `acquisition`/`conversion`).
- Marks orphan keys (in manifest but not in bib) as `in_bib=False` — does **not** delete them.

Read the summary carefully. The fields to scan:
- **New entries** — for each, classify by year:
  - `<1900` → "1800s vision-OCR-direct" candidate when acquired (see the intake skill's Path B)
  - `≥1900` → standard marker-first path
- **Dropped from bib** — if the key has on-disk artifacts (PDF/markdown), default to keeping as `in_bib=False`; only drop if it's a renamed-into-other-keys split (see §3) or genuinely wrong (user evict request).
- **DOIs changed / Citation metadata changed** — usually benign metadata polish (an `entry_type` change such as `article`→`book`/`phdthesis`/`misc` is upstream typing, not a different paper; see `docs/notes/intake-manifest-keys.md`, "Bib entry types: theses, books, @misc"); investigate if a key you previously acquired suddenly has a different DOI (could indicate a wrong-paper acquisition).
- **Genera/clades cites changed** — record but no manifest action.

## 3. Handle key splits and renames

The recurring pattern: bib upstream renames `oldkey` → `oldkeya` + adds `oldkeyb`, because two different papers were colliding under one key. After §2:
- `oldkey` is now in `manifest`, `in_bib=False` (orphan), still holding the `acquisition`/`conversion` from the artifact you have on disk.
- `oldkeya` and/or `oldkeyb` are new blank entries.

For each split, **identify which new key your existing artifact maps to** by reading the markdown body and matching against the new keys' DOIs/titles in the manifest. **Don't trust the manifest's cached citation fields on the orphan** — a previous refresh can have overwritten them with the metadata of the other paper that shared the key. The on-disk markdown is ground truth.

The outcomes vary:
- The artifact maps to the `b` key (for example a published correction), and the `a` key (the original article) stays missing.
- The old key had no artifacts: nothing moves, and both new keys start blank.
- The artifact maps to the `a` key even though the orphan's cached citation describes the other paper.

### Migration steps for each split where artifacts exist

```bash
# Rename artifacts: git mv where the files are tracked (an archive that
# commits its content), plain mv otherwise (a public clone ignores pdfs/ and markdown/).
git mv pdfs/{oldkey}.pdf pdfs/{newkey}.pdf          # or mv
git mv markdown/{oldkey}.md markdown/{newkey}.md    # or mv
[ -d pdf_images/{oldkey} ] && mv pdf_images/{oldkey} pdf_images/{newkey}   # gitignored
[ -d .runtime/cache/vision/{oldkey} ] && \
  mv .runtime/cache/vision/{oldkey} .runtime/cache/vision/{newkey}    # gitignored, only if exists
```

Then move the manifest state, through the manifest's lock (`corpus.manifest.update`, never a hand-written `json.dumps`, which also escapes diacritics):

```bash
./.runtime/venv/bin/python - <<'EOF'
from corpus.manifest import update

def migrate(m):
    src = m.pop("{oldkey}")                       # truly gone: the bib renamed it
    dst = m["{newkey}"]
    dst["acquisition"] = {**src["acquisition"], "pdf_path": "pdfs/{newkey}.pdf"}
    dst["conversion"] = {**src["conversion"], "markdown_path": "markdown/{newkey}.md"}

update(migrate)
EOF
```

Re-index: the indexer adds the new key and drops the old key's row itself.
```bash
./.runtime/venv/bin/python scripts/index.py
```

### When the split has no artifact on disk

(e.g., the old key was missing-paper-only) — just delete the orphan from the manifest after the refresh (`update(lambda m: m.pop("{oldkey}"))`); new entries already exist with proper bib metadata. No artifacts to move.

### Concurrent manifest writes

Scripts write the manifest under a lock and apply only the fields they changed, so a marker batch and a refresh can overlap. Any inline edit must go through `corpus.manifest.update(...)`, not a plain `json` read and write (see `docs/notes/intake-manifest-keys.md`, "Concurrent manifest writes").

## 4. Check the counts

```bash
./.runtime/venv/bin/corpus status
```

`corpus status` reports bib entries, held and missing; the missing count should move by the delta you expected (new keys minus newly acquired). A surprise count change is a smell — investigate before committing.

## 5. Commit + push

Only in an archive that tracks its manifest and content: a public clone gitignores `manifest.json` and the content folders, so there a refresh leaves nothing to commit. Use a single descriptive commit, staging by explicit path; don't commit unrelated working-tree changes.

Push to the archive's own remote. Don't force-push.

## Notes / gotchas

- **macOS NFC/NFD**: Citation keys with non-ASCII chars (ü, è, ...) need NFC-normalization when comparing `Path.glob().stem` (which on macOS returns NFD) against manifest keys (NFC). APFS is normalization-insensitive at the syscall level so file ops work either way; only the *string comparison* in Python breaks.
- **Curly vs straight apostrophes**: a key with an apostrophe can switch between U+2019 (’) and U+0027 (') in the bib. If a "rename" looks suspicious (same year + author but a different apostrophe), it's a Unicode rename and needs the same artifact-migration treatment.
- **Citations with offset between cited and PDF pages**: if a new key surfaces a "whole-volume" PDF, defer scope-limiting to user input; don't assume cited-page == PDF-page (see `docs/notes/conversion.md`, "Scope-limited vision OCR for whole-volume PDFs").
- **Pre-emptive renames**: the user may ask you to act on a split before the bib has propagated (e.g., "rename `{key}` → `{key}a` now even though the bib still has plain `{key}`"). In that case set `in_bib=False` on the new key and warn the user that the *next* refresh will recreate a stub `{key}` until the YAMLs/bib catch up.
