"""
Shared helpers for supplementary/ data PDFs.

Supplementary data (from papers already in pdfs/) is named
`{parent_key}-{name}.pdf` and lives in `supplementary/`. Its converted
markdown lives in `markdown/supplementary/{parent_key}-{name}.md`.

Supplementary files are NOT top-level manifest keys (that would clutter the
bib-keyed manifest and show up as in_bib=False orphans on every /refresh-bib).
Instead they are recorded as a sub-field on the PARENT paper's manifest entry:

    manifest[parent]["supplementary"][name] = {
        "pdf_path":      "supplementary/{parent}-{name}.pdf",
        "markdown_path": "markdown/supplementary/{parent}-{name}.md",
        "tool":          "marker" | "claude-vision",
        "page_count":    int,
        "converted_at":  ISO timestamp,
        "notes":         str,
    }

Because parent citation keys themselves can contain hyphens (a hyphenated
surname, e.g. `smith-jones2010a`), a supplementary stem cannot be split on the
first (or last) hyphen. `resolve_parent` matches the LONGEST parent-key prefix
that actually exists, so `smith-jones2010a-matrix` → (`smith-jones2010a`,
`matrix`).
"""
from __future__ import annotations

from corpus.config import SUPP_MD_DIR, SUPP_PDF_DIR  # noqa: F401  (re-exported)


def resolve_parent(stem: str, known_parents) -> tuple[str, str] | None:
    """Given a supplementary stem `{parent}-{name}`, return (parent, name)
    by LONGEST-prefix match against `known_parents` (a set/iterable of valid
    parent keys — e.g. manifest keys, or pdfs/*.pdf stems).

    Returns None if no known parent is a prefix of the stem. Longest-prefix
    resolution is what makes hyphenated parent keys unambiguous."""
    best: str | None = None
    for k in known_parents:
        if stem == k:
            continue
        if stem.startswith(k + "-") and (best is None or len(k) > len(best)):
            best = k
    if best is None:
        return None
    return best, stem[len(best) + 1:]


def supp_record(manifest: dict, parent: str, name: str) -> dict:
    """Return (creating if needed) the supplementary sub-record dict for
    manifest[parent]["supplementary"][name]. Caller mutates + writes."""
    entry = manifest[parent]
    supp = entry.setdefault("supplementary", {})
    return supp.setdefault(name, {})
