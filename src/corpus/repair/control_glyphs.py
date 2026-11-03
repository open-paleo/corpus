#!/usr/bin/env python3
"""Restore glyphs a broken ToUnicode map turned into C0 control characters.

The defect
----------
Some publisher PDFs carry a font whose ToUnicode CMap maps real glyphs to
unprintable C0 controls. One Anat. Rec. paper maps hyphen to \\x02, minus
to \\x01, "~" to \\x03, degree to \\x04 and multiplication to \\x05. Nothing
warns you: marker drops the controls silently, and so does the clipboard, so
the text reads plausibly while the characters are simply gone —

    semi-fossorial          -> semifossorial     (words fused)
    99.674 + 0.439/-0.197   -> 99.674 + 0.439/0.197
    Silhouettes are 2x      -> Silhouettes are 2
    essentially ~0deg       -> essentially 0.

The asymmetric-uncertainty case is the dangerous one: a +/- pair silently
becomes two positive numbers.

The fix
-------
Marker drops the controls; the PDF's text layer, read through corpus.pdf.text,
keeps them as control characters. So re-extract the text layer, apply a
per-file mapping from control code to glyph, and splice the recovered
characters back into the existing markdown.

Repairs are derived, never guessed: for each whitespace token in the PDF text
containing a target glyph, the "stripped" form (that glyph deleted) is what
marker would have written. Where the markdown holds the stripped form, it is
replaced with the real one. A token whose stripped form never appears in the
markdown is reported, not forced.

The mapping is PER FILE and MUST be verified against the rendered page before
applying — the same control code means different glyphs in different fonts, and
a wrong substitution is worse than a missing character. Pass it explicitly:

    --map 01=U+2212 --map 03=~ --map 04=U+00B0 --map 05=U+00D7

Scope
-----
Only for files where a FEW codes are mis-mapped. When a PDF shows the whole
\\x01-\\x1f spread the entire font is unmapped and no
substitution table can recover it — those need vision re-OCR.

Usage
-----
    ./.runtime/venv/bin/python scripts/repair_control_glyphs.py --key KEY \\
        --map 01=U+2212 --map 03=~ --map 04=U+00B0 --map 05=U+00D7
    ... --apply
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

from corpus.pdf.text import open_pdf
from corpus.config import PDF_DIR, MD_DIR
from corpus.keys import key_arg
from corpus.pages import write_markdown

# Base set worth restoring. Whatever --map produces is ADDED to this at run
# time: papers lose accented letters (á é í ó — Spanish author names) far
# more often than symbols, and a fixed symbol-only set would silently ignore
# every one of them.
BASE_TARGETS = "-−~°×–—"
TARGETS = BASE_TARGETS


def parse_map(pairs: list[str]) -> dict[str, str]:
    out = {}
    for p in pairs or []:
        code, _, val = p.partition("=")
        if not val:
            sys.exit(f"bad --map {p!r}, expected NN=CHAR or NN=U+XXXX")
        ch = chr(int(val[2:], 16)) if val.upper().startswith("U+") else val
        out[chr(int(code, 16))] = ch
    return out


# Combining marks a control code may stand for. These fonts encode an accent as
# a PREFIX glyph drawn over the NEXT letter (TeX style):
#     Sebasti\x01an -> Sebastián      Evoluci\x01on -> Evolución
# so one code yields á, é, í, ó and ú depending on what follows. Treating it as
# a fixed character substitution is wrong — it has to consume the next letter.
COMBINING = {"\u0301", "\u0300", "\u0302", "\u0303", "\u0308", "\u0327"}
# dotless i/j are used as the base under an accent
DOTLESS = {"\u0131": "i", "\u0237": "j"}


def apply_combining(text: str, ctl: str, mark: str) -> tuple[str, int, int]:
    """ctl + base -> precomposed accented letter. Returns (text, applied, skipped)."""
    import unicodedata
    out, i, applied, skipped = [], 0, 0, 0
    while i < len(text):
        if text[i] == ctl and i + 1 < len(text):
            base = DOTLESS.get(text[i + 1], text[i + 1])
            if base.isalpha():
                out.append(unicodedata.normalize("NFC", base + mark))
                i += 2
                applied += 1
                continue
            skipped += 1          # not over a letter — leave the control alone
        out.append(text[i])
        i += 1
    return "".join(out), applied, skipped


def pdf_text(key: str, mapping: dict[str, str]) -> tuple[str, dict[str, int]]:
    doc = open_pdf(PDF_DIR / f"{key}.pdf")
    text = "".join(doc.text(i) for i in range(len(doc)))
    doc.close()
    for ctl, ch in mapping.items():
        if ch in COMBINING:
            text, _, _ = apply_combining(text, ctl, ch)
        else:
            text = text.replace(ctl, ch)
    left = {}
    for ch in text:
        if ord(ch) < 32 and ch not in "\r\n\t":
            left[hex(ord(ch))] = left.get(hex(ord(ch)), 0) + 1
    return text, left


NOISE = set("*_#`>|")


def normalise(s: str, drop_targets: bool = False):
    """Collapse to a comparable form; return (text, index map back to source)."""
    out, idx, prev_space = [], [], False
    for i, ch in enumerate(s):
        if ch in NOISE or (drop_targets and ch in TARGETS):
            continue
        if ch.isspace():
            if prev_space:
                continue
            out.append(" ")
            idx.append(i)
            prev_space = True
        else:
            out.append(ch)
            idx.append(i)
            prev_space = False
    return "".join(out), idx


def repairs(text: str, md: str, ctx: int = 28):
    """Locate each lost glyph by its surrounding context, one occurrence at a time.

    Token-global replacement cannot work here. A PDF hyphen at end of line is
    line-break hyphenation, not a real hyphen, so its token ("some-") strips to
    an ordinary word and rewriting every "some" would wreck the file. Likewise
    "~100" strips to "100", which occurs throughout. So anchor on context: take
    the text either side of the glyph, find that pair in the markdown, and
    insert only where it resolves to exactly ONE site.
    """
    # BOTH sides must drop the target glyphs, or any context containing a
    # second hyphen can never match, and most of them would report "not found".
    nmd, nmap = normalise(md, True)
    found, ambiguous, missing = [], 0, 0
    for m in re.finditer("[" + re.escape(TARGETS) + "]", text):
        # end-of-line hyphen = hyphenation artefact, not a glyph to restore
        if text[m.start()] in "-\u2013\u2014" and text[m.end():m.end() + 1] == "\n":
            continue
        left, _ = normalise(text[max(0, m.start() - ctx):m.start()], True)
        right, _ = normalise(text[m.end():m.end() + ctx], True)
        left, right = left.lstrip(), right.rstrip()
        if len(left) < 6 or len(right) < 6:
            continue
        probe = left + right
        start = 0; hits = []
        while len(hits) < 3:
            i = nmd.find(probe, start)
            if i < 0:
                break
            hits.append(i); start = i + 1
        if len(hits) == 1:
            pos = nmap[hits[0] + len(left)]
            if md[pos - 1:pos] == text[m.start()] or md[pos:pos + 1] == text[m.start()]:
                continue                       # already present; don't double it
            # marker sometimes renders the unmapped glyph as a bare hyphen, so
            # the site already holds a stray "-". Inserting beside it yields
            # "130-°" / "(->1,000"; consume it instead when the PDF has no
            # hyphen there.
            eat = 0
            if md[pos - 1:pos] == "-" and text[m.start() - 1:m.start()] != "-":
                eat = 1
            found.append((pos, text[m.start()], probe, eat))
        elif len(hits) > 1:
            ambiguous += 1
        else:
            missing += 1
    uniq = {}
    for pos, ch, probe, eat in found:
        uniq.setdefault(pos, (ch, probe, eat))
    return (sorted(((p, c, pr, e) for p, (c, pr, e) in uniq.items()), reverse=True),
            ambiguous, missing)


def fused_words(text: str, md: str):
    """Words the PDF hyphenates whose FUSED form is what the markdown holds.

    Only INTERNAL hyphens count. A hyphen at a line break is hyphenation, and
    marker is right to close those up — mistaking them for lost glyphs is how
    a naive pass proposes rewriting every "some" as "some-".
    """
    out = []
    for tok in {t.strip("()[]{},;:.") for t in re.split(r"\s+", text)}:
        if not re.fullmatch(r"[A-Za-z]{2,}-[A-Za-z]{2,}", tok):
            continue
        joined = tok.replace("-", "")
        n = len(re.findall(rf"(?<![\w-]){re.escape(joined)}(?![\w-])", md))
        if n:
            out.append((n, joined, tok))
    return sorted(out, reverse=True)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--key", type=key_arg, required=True)
    ap.add_argument("--map", action="append", metavar="NN=CHAR",
                    help="control code -> glyph, e.g. 01=U+2212 (repeatable)")
    ap.add_argument("--map-file", help="JSON {key: {code: glyph}} for batches")
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args()

    md_path = MD_DIR / f"{args.key}.md"
    if not md_path.exists():
        sys.exit(f"no markdown for {args.key}")
    md = md_path.read_text(errors="replace", encoding="utf-8")
    mapping = parse_map(args.map)
    if args.map_file:
        import json as _json
        entry = _json.loads(Path(args.map_file).read_text(encoding="utf-8")).get(args.key, {})
        mapping.update({chr(int(c, 16)): g for c, g in entry.items()})
    if not mapping:
        sys.exit(f"no mapping for {args.key}: identify each control code's glyph on "
                 f"the rendered page first, then pass --map")
    global TARGETS
    TARGETS = BASE_TARGETS + "".join(sorted(set(mapping.values()) - set(BASE_TARGETS)))
    text, left = pdf_text(args.key, mapping)

    print(f"=== repair_control_glyphs — {'APPLY' if args.apply else 'DRY RUN'} ===")
    print(f"  {args.key}")
    if left:
        print(f"      ⚠ controls still unmapped: {left}")
        print("        identify them against the rendered page, then pass --map")

    plan, ambiguous, missing = repairs(text, md)
    from collections import Counter
    by_glyph = Counter(ch for _, ch, _, _ in plan)
    print(f"      glyphs restored  : {len(plan)}  {dict(by_glyph)}")
    print(f"      context ambiguous: {ambiguous}   (skipped — more than one site)")
    print(f"      context not found: {missing}   (skipped — markdown differs)")
    for pos, ch, probe, _ in plan[:12]:
        print(f"        {ch!r} into ...{probe[:56]}...")
    if len(plan) > 12:
        print(f"        … {len(plan) - 12} more")

    fused = fused_words(text, md)
    print(f"      fused words      : {len(fused)} distinct, "
          f"{sum(n for n, _, _ in fused)} occurrences")
    for n, joined, real in fused[:12]:
        print(f"        {n:>4}x  {joined!r} -> {real!r}")

    if args.apply and (plan or fused):
        for pos, ch, _, eat in plan:     # descending offsets: earlier stay valid
            md = md[:pos - eat] + ch + md[pos:]
        for _, joined, real in fused:
            md = re.sub(rf"(?<![\w-]){re.escape(joined)}(?![\w-])", real, md)
        write_markdown(md_path, md)
        print("      APPLIED")
    elif not args.apply:
        print("\nRe-run with --apply to write. Then re-index.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
