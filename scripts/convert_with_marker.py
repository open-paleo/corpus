#!/usr/bin/env python3
"""
Step 2a: bulk convert PDFs to Markdown via marker (datalab-to/marker).

Uses marker's CLI in folder mode with `--workers N` for true multi-process
parallelism (each worker is a separate Python process with its own model
copy). Keep --workers at 1 on Apple silicon: two or more MPS contexts
contend and marker reads garbage sequence lengths. TORCH_DEVICE defaults to
mps on Apple silicon (PyTorch falls back to CPU per-op for what MPS lacks)
and is left to torch elsewhere.

Default flow:
  - filter manifest for PDFs not yet marker-converted (by `conversion.tool`)
  - symlink them into a temp staging dir
  - run `marker stage_dir --output_dir raw_outputs --workers N --disable_ocr`
  - move per-key outputs into markdown/{key}.md (image extraction is off)
  - update manifest.conversion (locked commit)

Keys flagged for vision OCR (conversion.vision_ocr_needed) are skipped
unless --force: marker would replace their conversion block and the flag.

Run from project root with the venv:
  ./.runtime/venv/bin/python scripts/convert_with_marker.py [--workers 1] [--ocr]
                                                   [--limit N] [--force]
                                                   [--key K1 --key K2 ...]
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from corpus.progress import clear_progress, set_progress
from corpus.supplementary import SUPP_PDF_DIR, SUPP_MD_DIR, resolve_parent, supp_record
from corpus.config import PDF_DIR, MD_DIR, RUNTIME
from corpus.manifest import commit as manifest_commit, load as manifest_load
from corpus.keys import key_from_path
from corpus.keys import read_keys_file
from corpus.keys import key_arg
from corpus.io import jsonl_append

# marker prints a tqdm bar like "Processing PDFs:  88%|███| 7/8 [..]" — parse
# the N/M so the status line can show live progress.
_MARKER_PROG_RE = re.compile(rb"Processing PDFs:\s*\d+%\|[^|]*\|\s*(\d+)/(\d+)")


def _run_marker_streaming(cmd, env, job_note=""):
    """Run marker, tee its output through to our stderr (so the native tqdm
    bar still streams to the terminal / background-task log) while parsing
    the N/M PDF count to drive the progress sentinel. Returns returncode."""
    proc = subprocess.Popen(cmd, env=env, stdout=subprocess.PIPE,
                            stderr=subprocess.STDOUT)
    buf = b""
    last = -1
    try:
        for chunk in iter(lambda: proc.stdout.read(256), b""):
            sys.stderr.buffer.write(chunk)
            sys.stderr.buffer.flush()
            buf = (buf + chunk)[-4096:]  # bounded rolling window
            m = None
            for m in _MARKER_PROG_RE.finditer(buf):
                pass  # keep only the last match in the window
            if m:
                cur, tot = int(m.group(1)), int(m.group(2))
                if cur != last:
                    last = cur
                    set_progress("marker", cur, tot, note=job_note)
    finally:
        proc.stdout.close()
    return proc.wait()
TMP_DIR = RUNTIME / "tmp"
LOG = RUNTIME / "logs" / "marker.jsonl"


def marker_bin() -> str:
    """The marker CLI installed beside the running Python, else on PATH."""
    here = Path(sys.executable).with_name("marker")
    if here.exists():
        return str(here)
    found = shutil.which("marker")
    if not found:
        raise SystemExit("marker CLI not found: install the 'marker' extra "
                         "(pip install -e '.[marker]') into this environment")
    return found


def default_torch_device() -> str | None:
    """mps on Apple silicon; elsewhere None (torch picks cuda or cpu)."""
    import platform
    if sys.platform == "darwin" and platform.machine() == "arm64":
        return "mps"
    return None


def vision_flagged(entry: dict) -> bool:
    return bool((entry.get("conversion") or {}).get("vision_ocr_needed"))


def already_marker_converted(entry: dict, key: str) -> bool:
    conv = entry.get("conversion") or {}
    if conv.get("tool") != "marker":
        return False
    md_path = MD_DIR / f"{key}.md"
    return md_path.exists() and md_path.stat().st_size > 0


def already_marker_converted_supp(manifest: dict, stem: str) -> bool:
    """True if this supplementary stem already has a marker record on its
    parent + a non-empty markdown/supplementary/{stem}.md."""
    parsed = resolve_parent(stem, set(manifest))
    if parsed is None:
        return False
    parent, name = parsed
    rec = ((manifest[parent].get("supplementary") or {}).get(name)) or {}
    if rec.get("tool") != "marker":
        return False
    md_path = SUPP_MD_DIR / f"{stem}.md"
    return md_path.exists() and md_path.stat().st_size > 0


def post_process_outputs(candidates: list[str], output_dir: Path,
                         manifest: dict, ocr_enabled: bool,
                         supplementary: bool = False) -> tuple[int, int]:
    """Move marker outputs into our standard layout. Returns (ok, err).

    In supplementary mode, markdown is written to markdown/supplementary/ and
    the manifest is updated on the PARENT key's `supplementary[name]` sub-record
    rather than the (non-existent) top-level `{stem}` entry."""
    md_out_dir = SUPP_MD_DIR if supplementary else MD_DIR
    md_out_dir.mkdir(exist_ok=True, parents=True)
    known = set(manifest)
    n_ok = n_err = 0
    now = datetime.now(timezone.utc).isoformat()

    for key in candidates:
        sub = output_dir / key
        md_src = sub / f"{key}.md"
        if not md_src.exists():
            n_err += 1
            jsonl_append(LOG, {"ts": now, "key": key, "outcome": "no_output"})
            print(f"  ! {key:30s} no marker output produced")
            continue

        # Move markdown
        md_dst = md_out_dir / f"{key}.md"
        shutil.move(str(md_src), str(md_dst))

        # Read meta for page count
        meta_src = sub / f"{key}_meta.json"
        page_count = None
        if meta_src.exists():
            try:
                meta = json.loads(meta_src.read_text(encoding="utf-8"))
                page_count = (meta.get("page_count")
                              or (len(meta.get("page_stats", []))
                                  if isinstance(meta.get("page_stats"), list) else None))
            except Exception:
                pass

        if supplementary:
            parsed = resolve_parent(key, known)
            if parsed is None:
                n_err += 1
                print(f"  ! {key:30s} no parent manifest key; markdown written "
                      f"but not recorded")
                continue
            parent, name = parsed
            rec = supp_record(manifest, parent, name)
            rec.update({
                "tool": "marker",
                "pdf_path": f"supplementary/{key}.pdf",
                "markdown_path": f"markdown/supplementary/{key}.md",
                "page_count": page_count,
                "converted_at": now,
                "notes": f"supplementary of {parent}; ocr={'on' if ocr_enabled else 'off'}",
            })
        else:
            manifest[key]["conversion"] = {
                "tool": "marker",
                "markdown_path": f"markdown/{key}.md",
                "page_count": page_count,
                "converted_at": now,
                "notes": f"ocr={'on' if ocr_enabled else 'off'}",
            }
        n_ok += 1
        jsonl_append(LOG, {
            "ts": now, "key": key, "outcome": "ok",
            "page_count": page_count,
            "md_size": md_dst.stat().st_size, "ocr": ocr_enabled,
        })
    return n_ok, n_err


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--workers", type=int, default=1,
                    help="Parallel marker worker processes (each loads its own models). "
                         "Keep 1 on Apple silicon: 2+ hits an MPS contention bug.")
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--force", action="store_true",
                    help="Re-convert even if marker output already exists, and convert "
                         "keys flagged for vision OCR.")
    ap.add_argument("--ocr", action="store_true",
                    help="Enable surya OCR. Default: disabled (text-layer fast path).")
    ap.add_argument("--key", type=key_arg, action="append",
                    help="Only process these specific citation keys. Repeatable.")
    ap.add_argument("--keys-file",
                    help="Path to a file with one citation key per line (alternative to --key).")
    ap.add_argument("--keep-stage", action="store_true",
                    help="Don't delete the temp staging/output dirs after run.")
    ap.add_argument("--low-memory", action="store_true",
                    help="Small model batch sizes and a lower high-res render DPI, for "
                         "long or image-heavy PDFs on a 16 GB machine (default settings "
                         "reached 20 GB RSS and thrashed swap on a 512-page thesis).")
    ap.add_argument("--supplementary", action="store_true",
                    help="Convert supplementary/{parent}-{name}.pdf into "
                         "markdown/supplementary/; record on the parent's "
                         "supplementary sub-field. Keys are {parent}-{name} stems.")
    args = ap.parse_args()

    manifest = manifest_load()
    src_dir = SUPP_PDF_DIR if args.supplementary else PDF_DIR

    keys = list(args.key or [])
    if args.keys_file:
        keys.extend(read_keys_file(args.keys_file))

    # Build candidate list
    known = set(manifest)
    if keys:
        candidates = []
        for k in keys:
            if not (src_dir / f"{k}.pdf").exists():
                print(f"  ! no pdf for {k}; skipping")
            elif args.supplementary and resolve_parent(k, known) is None:
                print(f"  ! {k}: no parent manifest key; skipping")
            elif not args.supplementary and k not in manifest:
                print(f"  ! {k} not in manifest; skipping")
            elif not args.supplementary and not args.force and vision_flagged(manifest[k]):
                print(f"  ! {k} is flagged for vision OCR; skipping (--force to convert anyway)")
            else:
                candidates.append(k)
    else:
        candidates = []
        for pdf in sorted(src_dir.glob("*.pdf")):
            key = key_from_path(pdf)
            if args.supplementary:
                if resolve_parent(key, known) is None:
                    continue
                if not args.force and already_marker_converted_supp(manifest, key):
                    continue
            else:
                if key not in manifest:
                    continue
                if not args.force and already_marker_converted(manifest[key], key):
                    continue
                if not args.force and vision_flagged(manifest[key]):
                    continue
            candidates.append(key)

    if args.limit:
        candidates = candidates[: args.limit]

    print(f"Marker conversion candidates: {len(candidates)}")
    print(f"  workers: {args.workers}")
    print(f"  OCR:     {'ENABLED (slow path)' if args.ocr else 'disabled (text-layer fast path)'}")
    print(f"  device:  TORCH_DEVICE={os.environ.get('TORCH_DEVICE') or default_torch_device() or 'auto'}")
    print()
    if not candidates:
        print("Nothing to do.")
        return 0

    # Stage: symlink only the candidates into a temp dir so marker doesn't re-walk pdfs/
    TMP_DIR.mkdir(parents=True, exist_ok=True)
    run_id = int(time.time())
    stage_dir = TMP_DIR / f"marker_stage_{run_id}"
    output_dir = TMP_DIR / f"marker_outputs_{run_id}"
    stage_dir.mkdir(parents=True, exist_ok=True)
    output_dir.mkdir(parents=True, exist_ok=True)

    for k in candidates:
        try:
            (stage_dir / f"{k}.pdf").symlink_to((src_dir / f"{k}.pdf").resolve())
        except FileExistsError:
            pass

    cmd = [
        marker_bin(), str(stage_dir),
        "--output_dir", str(output_dir),
        "--workers", str(args.workers),
        "--output_format", "markdown",
        "--disable_image_extraction",
        "--disable_ocr_math",
        "--lowres_image_dpi", "72",
        "--max_tasks_per_worker", "25",
    ]
    if not args.ocr:
        cmd.append("--disable_ocr")
    if args.low_memory:
        cmd += [
            "--layout_batch_size", "2",
            "--detection_batch_size", "2",
            "--recognition_batch_size", "8",
            "--table_rec_batch_size", "2",
            "--ocr_error_batch_size", "2",
            "--highres_image_dpi", "120",
        ]

    env = {**os.environ}
    if default_torch_device() and "TORCH_DEVICE" not in env:
        env["TORCH_DEVICE"] = default_torch_device()

    print(f"Command: {' '.join(cmd)}")
    print(f"Stage:   {stage_dir}")
    print(f"Output:  {output_dir}")
    print()
    started = time.time()
    set_progress("marker", 0, len(candidates), note=f"{len(candidates)} pdfs")
    try:
        returncode = _run_marker_streaming(cmd, env, job_note=f"{len(candidates)} pdfs")
    finally:
        clear_progress()
    elapsed = time.time() - started

    if returncode != 0:
        print(f"\nWARNING: marker exited with code {returncode} — partial outputs may exist")

    # Post-process all keys, even if marker partial-failed (some may have completed)
    n_ok, n_err = post_process_outputs(candidates, output_dir, manifest, args.ocr,
                                       supplementary=args.supplementary)
    manifest_commit(manifest)

    print()
    print(f"Done in {elapsed/60:.1f} min  →  ok={n_ok}  err={n_err}")
    if n_ok:
        print(f"  avg per-doc: {elapsed/n_ok:.1f} s  (with {args.workers} workers)")

    if not args.keep_stage:
        shutil.rmtree(stage_dir, ignore_errors=True)
        shutil.rmtree(output_dir, ignore_errors=True)

    return 0 if returncode == 0 else returncode


if __name__ == "__main__":
    sys.exit(main())
