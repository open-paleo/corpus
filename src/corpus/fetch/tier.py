"""What every acquisition tier shares: which keys to try, and how a result
is recorded in manifest[key]["acquisition"].

    from corpus.fetch import tier
    for key in tier.candidates(manifest, only):
        ...
        tier.record_pdf(entry, "pmc_oa", key, url, license="CC BY")
        tier.record_failure(entry, "pmc_oa_not_found")

A failure is written as "{tier}_{outcome}" (corpus.manifest.FAILED_SUFFIXES)
so a later run of the same tier skips the key, but never over a real source:
a key recorded as "manual" whose PDF went missing keeps that record.
"""
from __future__ import annotations

from datetime import datetime, timezone

from corpus.config import PDF_DIR
from corpus.manifest import failed_source


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def acquisition(entry: dict) -> dict:
    """entry["acquisition"], created if a stub entry lacks it."""
    if not isinstance(entry.get("acquisition"), dict):
        entry["acquisition"] = {}
    return entry["acquisition"]


def candidates(manifest: dict, only: set[str] | None = None, *,
               untried_only: bool = False) -> list[str]:
    """Keys with a DOI and no PDF on disk, in manifest order. `only` (from
    --key/--keys-file) restricts them; untried_only also skips any key whose
    acquisition.source is already set (a tier that should not retry its own
    or another tier's misses)."""
    out = []
    for key, entry in manifest.items():
        if only is not None and key not in only:
            continue
        if not entry.get("doi") or (PDF_DIR / f"{key}.pdf").exists():
            continue
        if untried_only and (entry.get("acquisition") or {}).get("source") is not None:
            continue
        out.append(key)
    return out


def record_pdf(entry: dict, source: str, key: str, url: str | None, **extra) -> None:
    """A PDF now at pdfs/{key}.pdf, fetched by `source` from `url`. A real
    earlier source is kept as previous_source."""
    prior = acquisition(entry).get("source")
    entry["acquisition"] = {
        "source": source, "url": url, "pdf_path": f"pdfs/{key}.pdf", "fetched_at": now(),
        **extra,
        **({"previous_source": prior} if prior and not failed_source(prior) else {}),
    }


def record_failure(entry: dict, source: str, **extra) -> bool:
    """Record a failed attempt as `source` ("{tier}_{outcome}") unless the
    key already has a real source. Returns whether it was written."""
    acq = acquisition(entry)
    if acq.get("source") and not failed_source(acq["source"]):
        return False
    acq.update(source=source, fetched_at=now(), **extra)
    return True
