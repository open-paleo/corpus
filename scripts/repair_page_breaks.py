#!/usr/bin/env python3
# corpus-command: repair page-breaks | split paragraphs marker filed wholly under the page they end on
"""
Put the text of a paragraph that crosses a page break under the right page.

marker files a paragraph that starts at the foot of page N-1 and runs onto
page N wholly under page N's marker, so its opening lines cite one page too
late (kacemi2026a: the El Bayadh paragraph, printed on p. 18, sat under 19).
A census on 2026-11-25 found about 7,200 such paragraphs in 1,600 marker
papers. Vision OCR reads one page at a time, so its markdown already splits
a paragraph at the page break; this makes marker's output do the same.

For each page marker N, the paragraphs right after it are checked against
the PDF's own text layer (letters and digits only, NFKC). A paragraph whose
opening is found on page N-1 and nowhere else is split after the longest
run of whole words that page N-1 holds, provided the remainder's opening is
on page N; the marker moves to the split. A paragraph wholly on page N-1
moves above the marker and the next one is checked. Anything else (a
heading, a table row, an opening found on several pages, a remainder not
found on page N) is left alone and counted as unclear.

A translated paper is split in `.original.md` (the text the PDF holds) and
the translation follows by sentence count: when the paragraph at the same
place in the page block has the same number of sentences in both files,
the translation is split after the same sentence (a sentence
that crosses the break stays with the page it starts on). Otherwise the
translation is left as it was and the report says so.

Every change is appended to --report (JSON lines: key, file, page, the text
moved to the page before) so citations by page can be audited.

    corpus repair page-breaks --key KEY                    # dry run, per-change lines
    corpus repair page-breaks --all --apply --report R.jsonl
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import unicodedata

from corpus.config import MD_DIR, PDF_DIR
from corpus.keys import key_arg, read_keys_file
from corpus.manifest import load as manifest_load
from corpus.pages import MARKER, write_markdown
from corpus.pdf.text import open_pdf

SKIP_START = ("#", "|", "<!--", "![", "```", "---")
SENT = re.compile(r"(?<=[.!?])[\"'”’)\]*]*\s+(?=[\"'“‘(\[*]*[A-ZÀ-ÖØ-ÞА-ЯΑ-Ω0-9])")


def sq(s: str) -> str:
    """Letters and digits only: what a paragraph and a page's text layer share."""
    s = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", s)        # links keep their text
    s = re.sub(r"<[^>]+>", "", s)                          # spans, sup, br
    return re.sub(r"[\W_]+", "", unicodedata.normalize("NFKC", s)).lower()


def sentences(p: str) -> list[str]:
    return [x for x in SENT.split(p) if x.strip()]


def blocks(text: str):
    """(page, start line, end line) for each page block, lines 0-based, end exclusive."""
    lines = text.split("\n")
    idx = [(i, int(m.group(1))) for i, l in enumerate(lines) if (m := MARKER.match(l))]
    out = []
    for j, (i, n) in enumerate(idx):
        end = idx[j + 1][0] if j + 1 < len(idx) else len(lines)
        out.append((n, i, end))
    return lines, out


def plan(lines, start, end, prev_pg: str, page_pg: str, pages: list[str], prev_no: int):
    """What to move from the head of one page block to the page before.

    Returns (moves, status): moves is a list of (line index, split word count
    or None for the whole line); status is 'ok', 'none' or 'unclear'."""
    moves = []
    i = start + 1
    while i < end and len(moves) < 4:
        line = lines[i]
        if not line.strip():
            i += 1
            continue
        if line.lstrip().startswith(SKIP_START):
            return moves, ("ok" if moves else "none")
        s = sq(line)
        if len(s) < 40:
            return moves, ("ok" if moves else "none")
        probe = s[:30]
        hits = [k for k, pg in enumerate(pages) if probe in pg]
        if hits != [prev_no - 1]:
            # not (only) on the page before: stays where it is
            return moves, ("ok" if moves else ("none" if (prev_no) in [h + 1 for h in hits] or not hits or prev_no - 1 not in hits else "unclear"))
        words = line.split(" ")
        best = 0
        for w in range(1, len(words) + 1):
            if sq(" ".join(words[:w])) in prev_pg:
                best = w
            else:
                break
        if best == len(words):
            moves.append((i, None))
            i += 1
            continue
        rest = sq(" ".join(words[best:]))
        if best == 0 or not rest or rest[:25] not in page_pg:
            return moves, "unclear"
        moves.append((i, best))
        return moves, "ok"
    return moves, ("ok" if moves else "none")


def apply_moves(lines, marker_line, moves):
    """Lines with the marker moved past the moved text; returns (lines, moved texts)."""
    out = list(lines)
    marker = out[marker_line]
    texts = []
    last, split = moves[-1]
    for i, w in moves:
        words = out[i].split(" ")
        texts.append(" ".join(words[:w]) if w else out[i])
    if split:
        words = out[last].split(" ")
        out[last] = " ".join(words[:split]) + "\n\n" + marker + "\n\n" + " ".join(words[split:])
    else:
        out[last] = out[last] + "\n\n" + marker
    out[marker_line] = None
    return out, texts


def finish(lines) -> str:
    text = "\n".join(l for l in lines if l is not None)
    return re.sub(r"\n{3,}", "\n\n", text)


def translate_moves(t_lines, t_start, t_end, o_lines, o_start, o_end, moves):
    """The same moves in a translation, by paragraph and sentence count, or None."""
    o_par = [i for i in range(o_start + 1, o_end) if o_lines[i].strip()]
    t_par = [i for i in range(t_start + 1, t_end) if t_lines[i].strip()]
    out = []
    for i, w in moves:
        k = o_par.index(i)
        if k >= len(t_par):
            return None
        ti = t_par[k]
        o_s, t_s = sentences(o_lines[i]), sentences(t_lines[ti])
        # The paragraphs correspond only if they have the same sentences;
        # a block's later paragraphs may still differ (the translation can
        # split or merge them), which does not matter here.
        if len(o_s) != len(t_s):
            return None
        if w is None:
            out.append((ti, None))
            continue
        if len(o_s) < 2:
            return None
        head = " ".join(o_lines[i].split(" ")[:w])
        # sentences started in the head: a sentence crossing the break stays with its start
        n = 0
        pos = 0
        for s in o_s:
            if pos < len(head):
                n += 1
            pos += len(s) + 1
        if n >= len(t_s):
            out.append((ti, None))
            continue
        out.append((ti, len(" ".join(t_s[:n]).split(" "))))
    return out


def process(key: str, apply: bool, verbose: bool, report) -> dict:
    res = {"key": key, "moved": 0, "unclear": 0, "translation_skipped": 0}
    pdf = PDF_DIR / f"{key}.pdf"
    orig = MD_DIR / f"{key}.original.md"
    main_md = MD_DIR / f"{key}.md"
    primary = orig if orig.exists() else main_md
    trans = main_md if orig.exists() else None
    if not pdf.exists() or not primary.exists():
        return res
    doc = open_pdf(pdf)
    try:
        pages = [sq(doc.text(p)) for p in range(len(doc))]
    finally:
        doc.close()
    o_text = primary.read_text(encoding="utf-8")
    o_lines, o_blocks = blocks(o_text)
    t_text = trans.read_text(encoding="utf-8") if trans else None
    t_lines, t_blocks = blocks(t_text) if trans else (None, [])
    t_by_page = {n: (s, e) for n, s, e in t_blocks}
    entries = []
    o_new, t_new = list(o_lines), list(t_lines) if trans else None
    for n, s, e in o_blocks:
        if n < 2 or n - 1 > len(pages) or n > len(pages):
            continue
        moves, status = plan(o_lines, s, e, pages[n - 2], pages[n - 1], pages, n - 1)
        if status == "unclear":
            res["unclear"] += 1
        if not moves:
            continue
        o_new, texts = apply_moves(o_new, s, moves)
        entry = {"key": key, "file": primary.name, "page": n, "moved_to_page": n - 1,
                 "kind": "split" if moves[-1][1] else "whole", "text": "\n\n".join(texts)}
        if trans:
            tm = None
            if n in t_by_page:
                ts, te = t_by_page[n]
                tm = translate_moves(t_lines, ts, te, o_lines, s, e, moves)
            if tm:
                t_new, t_texts = apply_moves(t_new, t_by_page[n][0], tm)
                entry["translation_text"] = "\n\n".join(t_texts)
                if moves[-1][1] and not re.search(r"[.!?][\"'”’)\]*]*$", texts[-1]):
                    entry["translation_note"] = ("the page break falls mid-sentence; the translation "
                                                 "keeps that sentence with the page it starts on")
            else:
                res["translation_skipped"] += 1
                entry["translation"] = "not adjusted: the translation's paragraphs or sentences do not line up"
        entries.append(entry)
        res["moved"] += 1
        if verbose:
            t = entry["text"]
            print(f"    {key} p{n}->{n - 1} {entry['kind']}: …{t[-70:]!r}"
                  + ("" if not trans else f"  [translation {'split' if 'translation_text' in entry else 'NOT adjusted'}]"))
    if apply and entries:
        ok = write_markdown(primary, finish(o_new), o_text)
        if ok and trans:
            ok = write_markdown(trans, finish(t_new), t_text)
        if not ok:
            res["moved"] = 0
            return res
    if report and entries and apply:
        for en in entries:
            report.write(json.dumps(en, ensure_ascii=False) + "\n")
    return res


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[1])
    ap.add_argument("--key", type=key_arg, action="append")
    ap.add_argument("--keys-file")
    ap.add_argument("--all", action="store_true", help="every marker conversion with a PDF")
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--verbose", action="store_true")
    ap.add_argument("--report", help="append each change as a JSON line (with --apply)")
    args = ap.parse_args()
    keys = list(args.key or [])
    if args.keys_file:
        keys += read_keys_file(args.keys_file)
    if args.all:
        m = manifest_load()
        keys = sorted(k for k, e in m.items() if (e.get("conversion") or {}).get("tool") == "marker")
    report = open(args.report, "a", encoding="utf-8") if args.report else None
    tot = {"moved": 0, "unclear": 0, "translation_skipped": 0}
    changed = 0
    for k in keys:
        try:
            r = process(k, args.apply, args.verbose or not args.all, report)
        except Exception as e:     # one unreadable PDF must not stop a corpus run
            print(f"  ! {k}: {e}", file=sys.stderr)
            continue
        for f in tot:
            tot[f] += r[f]
        changed += bool(r["moved"])
    if report:
        report.close()
    print(f"{len(keys)} paper(s); {changed} {'changed' if args.apply else 'would change'}; "
          f"page breaks fixed {tot['moved']}, unclear {tot['unclear']}, "
          f"translations not adjusted {tot['translation_skipped']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
