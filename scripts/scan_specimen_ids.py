#!/usr/bin/env python3
# corpus-command: qc specimen-ids | read accession numbers inked on specimens in figure photos (vision)
"""
Look for accession numbers physically inked/painted/stencilled on specimens in
figure photographs, and corroborate them against the accession series attested
in the corpus.

Specimens are routinely marked with their catalogue number. When the literature
is silent, contradictory, or wrong, a photograph can settle it -- see
docs in .claude/skills/specimen-id-scan/SKILL.md for the full method.

Sources of images, in order of usefulness:
  * pdf_images/{key}/   (run scripts/extract_pdf_images.py first, or use --extract)
  * any loose directory of photos (--images DIR), e.g. museum/collection shots

Usage:
  ./.runtime/venv/bin/python scripts/scan_specimen_ids.py --key KEY --extract
  ./.runtime/venv/bin/python scripts/scan_specimen_ids.py --images photos/
  ./.runtime/venv/bin/python scripts/scan_specimen_ids.py --series 41HIII
"""

from __future__ import annotations

import argparse
import base64
import io
import json
import re
import sys
import time
from pathlib import Path

from corpus.config import ROOT as ARCHIVE, MD_DIR, PDF_IMAGES_DIR, RUNTIME

IMG_DIR = PDF_IMAGES_DIR
CACHE = RUNTIME / "cache" / "specimen_ids"
LOG = RUNTIME / "logs" / "specimen_ids.jsonl"

from corpus.models import SONNET, cache_model_ok
from corpus.keys import key_from_path
from corpus.keys import read_keys_file
from corpus.keys import key_arg
from corpus.io import atomic_write_json
from corpus.io import read_json
from corpus.config import require_env
from corpus.vision.client import make as make_client
DEFAULT_MODEL = SONNET
PROMPT_ID = "specimen-ink-v1"
MAX_EDGE = 1568          # Anthropic vision downscales past this anyway
MIN_BYTES = 15_000       # below this it is a rule, logo or scale bar
JPEG_Q = 85

PROMPT = """You are examining a photograph of a fossil specimen.

Museum specimens are usually marked with their accession number, hand-written
in ink, painted, or stencilled directly onto the bone or onto its matrix/plinth.

Report ONLY what is physically visible in the image. Answer in this exact form:

MARKED: yes | no | unclear
TRANSCRIPTION: <the number exactly as written, or -->
GLYPHS: <character-by-character reading of any Roman numerals, or -->
LOCATION: <where on the specimen, or -->
NOTES: <anything relevant: partial/obscured digits, a second number, a label
        card rather than ink, retouching, or that this is a line drawing>

Rules:
- Roman numerals are often drawn CJK-style (III as three strokes with a bar).
  Read them glyph by glyph and say so in GLYPHS.
- A printed figure label ("Fig. 2", "a", "A") or a scale bar is NOT an
  accession number. Say no.
- A number on a label card next to the specimen is not ink on the bone; report
  it but say so in NOTES.
- If the image is a line drawing, map or chart, answer MARKED: no.
- Do not guess. Prefer "unclear" over a confident misreading."""

SERIES_RE_TMPL = r"{prefix}\s*[-–—]?\s*0*(\d{{1,5}}[a-z]?)\b"


# --------------------------------------------------------------------------- #
# series corroboration
# --------------------------------------------------------------------------- #

def attested_series(prefix: str) -> dict[str, list[str]]:
    """Every sub-number of `prefix` attested in markdown/, -> sorted keys."""
    pat = re.compile(SERIES_RE_TMPL.format(prefix=re.escape(prefix).replace(r"\ ", r"\s*")),
                     re.IGNORECASE)
    found: dict[str, set[str]] = {}
    for p in sorted(MD_DIR.rglob("*.md")):
        try:
            text = p.read_text(errors="ignore", encoding="utf-8")
        except OSError:
            continue
        for m in pat.finditer(text):
            found.setdefault(m.group(1).lstrip("0").zfill(4), set()).add(key_from_path(p))
    return {k: sorted(v) for k, v in sorted(found.items())}


def report_series(prefix: str, candidates: list[str]) -> None:
    att = attested_series(prefix)
    print(f"\n=== accession series {prefix} attested in corpus ({len(att)}) ===")
    for num, keys in att.items():
        print(f"  {prefix}-{num:<6} {', '.join(keys[:4])}"
              + (f" (+{len(keys)-4})" if len(keys) > 4 else ""))
    if not att:
        print("  (none)")
        return
    # letter-suffixed numbers (e.g. 308A) are real but can't anchor a range
    numeric = sorted(int(k) for k in att if k.isdigit())
    if not numeric:
        print("\n  (no purely-numeric entries; gap analysis skipped)")
        return
    lo, hi = numeric[0], numeric[-1]
    print(f"\n  range {lo:04d}-{hi:04d}, {len(numeric)} numeric entries (sparse series)")
    for c in candidates:
        norm = c.lstrip("0").zfill(4)
        if norm in att:
            print(f"  ! {prefix}-{norm}: ALREADY ASSIGNED to {', '.join(att[norm])}"
                  f" -- suspicious, re-read the image")
            continue
        if not norm.isdigit():
            print(f"  ? {prefix}-{norm}: unattested (letter-suffixed; no gap analysis)")
            continue
        n = int(norm)
        below = [x for x in numeric if x < n]
        above = [x for x in numeric if x > n]
        # local density: how much of the +/-10 window around the candidate is attested
        window = [x for x in numeric if abs(x - n) <= 10]
        print(f"  + {prefix}-{norm}: UNATTESTED."
              f" nearest below={below[-1]:04d} above={above[0]:04d}" if below and above
              else f"  + {prefix}-{norm}: UNATTESTED (outside the attested range)")
        if below and above:
            print(f"      {len(window)} other entries within +/-10 -- "
                  + ("densely populated neighbourhood, so a genuine local gap "
                     "is meaningful corroboration" if len(window) >= 3 else
                     "sparse neighbourhood, so treat the gap as weak corroboration"))


# --------------------------------------------------------------------------- #
# vision scan
# --------------------------------------------------------------------------- #

def load_jpeg(path: Path) -> bytes | None:
    from PIL import Image
    try:
        im = Image.open(path)
        im = im.convert("RGB")
    except Exception:
        return None
    w, h = im.size
    if max(w, h) > MAX_EDGE:
        scale = MAX_EDGE / max(w, h)
        im = im.resize((max(1, int(w * scale)), max(1, int(h * scale))))
    buf = io.BytesIO()
    im.save(buf, format="JPEG", quality=JPEG_Q)
    return buf.getvalue()


def parse_reply(text: str) -> dict:
    out = {}
    for field in ("MARKED", "TRANSCRIPTION", "GLYPHS", "LOCATION", "NOTES"):
        m = re.search(rf"^{field}:\s*(.*)$", text, re.MULTILINE)
        out[field.lower()] = (m.group(1).strip() if m else "")
    return out


def scan_image(client, model: str, path: Path, cache_dir: Path, force: bool) -> dict | None:
    cache_dir.mkdir(parents=True, exist_ok=True)
    cpath = cache_dir / (path.stem + ".json")
    if cpath.exists() and not force:
        cached = (read_json(cpath) or {})
        if cached.get("prompt_id") == PROMPT_ID and cache_model_ok(cached.get("model"), model):
            cached["cached_hit"] = True
            return cached

    jpeg = load_jpeg(path)
    if jpeg is None:
        return None
    msg = client.messages.create(
        model=model,
        max_tokens=512,
        system=[{"type": "text", "text": PROMPT,
                 "cache_control": {"type": "ephemeral"}}],
        messages=[{"role": "user", "content": [{
            "type": "image",
            "source": {"type": "base64", "media_type": "image/jpeg",
                       "data": base64.standard_b64encode(jpeg).decode("ascii")},
        }]}],
    )
    raw = "".join(b.text for b in msg.content if b.type == "text").strip()
    rec = {
        "image": str(path.relative_to(ARCHIVE)) if ARCHIVE in path.parents else str(path),
        "model": model, "prompt_id": PROMPT_ID,
        "raw": raw, **parse_reply(raw),
        "input_tokens": msg.usage.input_tokens,
        "output_tokens": msg.usage.output_tokens,
        "cached_hit": False,
    }
    atomic_write_json(cpath, rec, sort_keys=False, ensure_ascii=False)
    return rec


def gather_images(keys: list[str], images_dir: Path | None, extract: bool) -> list[tuple[str, Path]]:
    out: list[tuple[str, Path]] = []
    if images_dir:
        for p in sorted(images_dir.rglob("*")):
            if p.suffix.lower() in (".jpg", ".jpeg", ".png", ".tif", ".tiff", ".webp"):
                out.append((images_dir.name, p))
    for k in keys:
        d = IMG_DIR / k
        if not d.is_dir() or not any(d.iterdir()):
            if extract:
                import subprocess
                subprocess.run([sys.executable, str(ARCHIVE / "scripts" / "extract_pdf_images.py"),
                                "--key", k], check=False)
            else:
                print(f"  ! {k}: no pdf_images/{k}/ -- run with --extract", file=sys.stderr)
                continue
        if d.is_dir():
            out.extend((k, p) for p in sorted(d.iterdir())
                       if p.is_file() and p.stat().st_size >= MIN_BYTES)
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--key", type=key_arg, action="append", default=[],
                    help="citation key; repeatable. Scans pdf_images/{key}/")
    ap.add_argument("--keys-file")
    ap.add_argument("--images", type=Path,
                    help="scan a loose directory of photos instead of/besides pdf_images")
    ap.add_argument("--extract", action="store_true",
                    help="run extract_pdf_images.py for keys with no images yet")
    ap.add_argument("--series", help='accession prefix to corroborate against, e.g. "41HIII"')
    ap.add_argument("--model", default=DEFAULT_MODEL)
    ap.add_argument("--force", action="store_true", help="ignore cached replies")
    ap.add_argument("--all", action="store_true",
                    help="print every image, not just the marked/unclear ones")
    args = ap.parse_args()

    keys = list(args.key)
    if args.keys_file:
        keys += read_keys_file(args.keys_file)

    if args.series and not keys and not args.images:
        report_series(args.series, [])
        return 0

    targets = gather_images(keys, args.images, args.extract)
    if not targets:
        print("No images to scan.", file=sys.stderr)
        return 1

    require_env("ANTHROPIC_API_KEY")
    client = make_client(async_=False)

    LOG.parent.mkdir(parents=True, exist_ok=True)
    print(f"Scanning {len(targets)} image(s) with {args.model} ...\n")
    hits, unclear, n_cached, tok_in, tok_out = [], [], 0, 0, 0
    t0 = time.time()

    for owner, path in targets:
        rec = scan_image(client, args.model, path, CACHE / owner, args.force)
        if rec is None:
            print(f"  ? {path.name}: unreadable image")
            continue
        n_cached += bool(rec.get("cached_hit"))
        tok_in += rec.get("input_tokens", 0)
        tok_out += rec.get("output_tokens", 0)
        marked = (rec.get("marked") or "").lower()
        with LOG.open("a") as fh:
            fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
        if marked.startswith("yes"):
            hits.append((owner, path, rec))
            print(f"  ✓ {owner}/{path.name}: {rec.get('transcription')!r}")
            if rec.get("glyphs") not in ("", "-"):
                print(f"      glyphs: {rec['glyphs']}")
            if rec.get("location") not in ("", "-"):
                print(f"      on: {rec['location']}")
            if rec.get("notes") not in ("", "-"):
                print(f"      note: {rec['notes']}")
        elif marked.startswith("unclear"):
            unclear.append((owner, path, rec))
            print(f"  ~ {owner}/{path.name}: unclear -- {rec.get('notes')}")
        elif args.all:
            print(f"  · {owner}/{path.name}: no marking")

    print(f"\nDone in {(time.time()-t0)/60:.1f} min"
          f"  |  marked={len(hits)} unclear={len(unclear)}"
          f" none={len(targets)-len(hits)-len(unclear)} cached={n_cached}")
    print(f"  tokens: in={tok_in} out={tok_out}   log: {LOG.relative_to(ARCHIVE)}")

    if args.series:
        cands = []
        for _, _, rec in hits:
            for m in re.finditer(r"(\d{1,5}[a-z]?)\s*$", (rec.get("transcription") or "").strip()):
                cands.append(m.group(1))
        report_series(args.series, sorted(set(cands)))

    if unclear:
        print("\nEyeball the 'unclear' images by hand before believing or dismissing them.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
