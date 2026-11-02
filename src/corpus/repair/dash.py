#!/usr/bin/env python3
"""Restore the en and em dashes that Elsevier's dash subset decodes as letters.

Elsevier's `AdvPS44A44B` is a font that carries dashes and almost nothing else.
It ships no `/Encoding /Differences`, so extraction falls back to a standard
encoding and the two dashes come back as ordinary lowercase letters:

    Barremian–Early Aptian      ->  BarremianeEarly Aptian
    Cenomanian–Early Turonian   ->  CenomanianeEarly Turonian
    middle–upper                ->  middleeupper
    Chelonia—Podocnemidoidea    ->  CheloniadPodocnemidoidea

The PDF's own text layer already reads this way, so marker is not at fault and
re-running it changes nothing. Re-OCR is the wrong tool too: the page renders
correctly, and vision on a clean born-digital paper costs money to reproduce
text that is already there.

The `d`/em-dash half is the dangerous one. An en dash usually joins two
capitalised words, so `AptianeAlbian` at least looks wrong; an em dash between
two taxon names leaves a single plausible lowercase-joined word with nothing to
catch the eye — `adTitanosauridaed`, `adDryolestoidead`, `adChelidaed` are all
faunal-list entries in one paper.

Identification is font-keyed, not textual. A regex over the markdown cannot do
this job: it would rewrite `ScienceDirect` and every other legitimate camel
case, and it would never see the `d` variant at all. So each occurrence is
located in the PDF by the font it was drawn with, and its surrounding text is
used as an anchor into the markdown.

**That font is not purely dashes.** A small tail of occurrences sits at other
codes, and what they mean differs from paper to paper — `!` is a multiplication
sign, `C` and `D` are both plus signs, `G` is a plus-minus, `$` a multiplication
dot. Those live in KEY_GLYPHS below, keyed by (key, character) and each read off
a rendered crop. Anything not in either table is reported, never guessed at.

Guessing here is not safe. From context alone `!` in
"high!30-32 cm wide" reads as an approximation mark; it is a multiplication sign,
and "45 cm high × 30–32 cm wide" is a different measurement from "approximately
30-32 cm wide". `G` in "(99.5 G 1.5 Ma)" is a plus-minus on a radiometric age.
Every mapping in both tables was read off a rendered crop, not inferred.

    scripts/repair_dash_font_substitutions.py --audit        # scope, no writes
    scripts/repair_dash_font_substitutions.py --key KEY
    scripts/repair_dash_font_substitutions.py --key KEY --apply
    scripts/repair_dash_font_substitutions.py --all --apply
"""
from __future__ import annotations

import argparse
import re
import sys
from collections import Counter

from corpus.pdf.text import open_pdf
from corpus.io import atomic_write_text
from corpus.config import PDF_DIR, MD_DIR
from corpus.keys import key_from_path
from corpus.keys import key_arg
from corpus.md import plain

# The Elsevier dash-only subset. Keyed by font name because the decoded
# character is an ordinary letter and carries no signal of its own.
DASH_FONT = "AdvPS44A44B"

# Confirmed against a rendered crop. Anything this font emits that is not
# listed here is reported, never guessed at.
GLYPHS = {"e": "–", "d": "—"}

# The font is not purely dashes. A handful of papers use other codes in it, and
# the code means different things in different subsets — so these are keyed by
# (key, character) and every one was read off a rendered 8x crop of the line it
# appears in, never inferred from its surroundings. Guessing here writes real
# errors into otherwise clean papers: `!` looks like an approximation mark in
# "high!30-32 cm wide" and is in fact a multiplication sign, which is a
# different measurement.
KEY_GLYPHS = {
    ("varricchio2007a", "!"): "×",     # "45 cm high × 30–32 cm wide"
    ("hao2024a", "$"): "·",            # "1.75 g/cm3 · 59.00 cm3 = 103.25 g"
    ("vanitterbeeck2005z", "C"): "+",  # "K and ERH/+ (Fig. 3)"
    ("gonzálezriga2009a", "D"): "+",   # table header "width (+)  height (+)"
    ("makovicky2010a", "C"): "+",      # "suffixed with a '+' are for taxa known"
    ("mateus2009a", "C"): "+",         # "Stegosaurus + Dacentrurinae"
    ("vanitterbeeck2004z", "G"): "±",  # "Formation (99.5 ± 1.5 Ma), Xinjiang"
    # One more paper's "C" is deliberately absent: its glyph renders as nothing
    # at all on the page (an artefact inside a table), so there is no character to
    # restore. Left reported.
}

CONTEXT = 24


# Tokens the markdown does not contain, split by how much they matter to a
# reader. A bibliography page range nobody looks at twice and a figure callout
# that reads as a typo are both "residue", but only one of them is worth
# chasing, so a single combined number stops meaning anything as it shrinks.
PAGE_RANGE = re.compile(r"[^A-Za-z]*\d[\d,.:;()\[\]]*[ed][\d,.:;()\[\]]*\d[^A-Za-z]*")
FIG_CALLOUT = re.compile(r"[^A-Za-z]*\d*[A-Z][ed][A-Z][^a-z]*")

DESCRIPTIONS = {
    "range": "bibliography page range (invisible to a reader)",
    "figure": "figure-part callout, e.g. '3AeE)' (READS AS A TYPO)",
    "prose": "running prose or a proper noun (WORTH CHASING)",
}


def classify(tokens: list[str]) -> dict[str, list[str]]:
    out: dict[str, list[str]] = {}
    for t in tokens:
        if PAGE_RANGE.fullmatch(t):
            label = "range"
        elif FIG_CALLOUT.fullmatch(t):
            label = "figure"
        else:
            label = "prose"
        out.setdefault(label, []).append(t)
    return out


def occurrences(doc):
    """Every whitespace-delimited token the dash font drew a character inside.

    Repairing whole tokens rather than single characters is what makes this
    tractable. The substitutions repeat verbatim — a running head carries the
    same `61e87` on every page, and a faunal list fuses a whole chain into one
    word (`DinosauriadOrnithischiadEurypodadStegosauria`, three em dashes in a
    single token) — so no per-character anchor can ever be unique, and patching
    the characters one at a time would invalidate the next one's context. A
    token carries all of its own dashes and is repaired in one substitution.

    The page text is rebuilt from rawdict rather than taken from doc.text() so
    each character can be tagged with the font that drew it; the block/line/span
    walk is the same order doc.text() uses.
    """
    # rawdict is expensive, and few papers embed this font, so
    # check the (much cheaper) font resource list before walking any glyphs.
    if not any(f == DASH_FONT
               for pno in range(len(doc)) for f in doc.page_fonts(pno)):
        return []

    out = []
    for pno in range(len(doc)):
        chars, marks = [], []
        for block in doc.rawdict(pno)["blocks"]:
            for line in block.get("lines", []):
                for span in line.get("spans", []):
                    dash = span["font"] == DASH_FONT
                    for ch in span["chars"]:
                        chars.append(ch["c"])
                        marks.append(dash)
                chars.append("\n")
                marks.append(False)
        toks, i, n = [], 0, len(chars)
        while i < n:
            if chars[i].isspace():
                i += 1
                continue
            j = i
            while j < n and not chars[j].isspace():
                j += 1
            toks.append((chars[i:j], marks[i:j]))
            i = j
        # A token broken across a line carries a trailing hyphen the markdown
        # does not have — marker rejoins it — so keep the follower available.
        text = "".join(chars)
        cursor = 0
        for k, (tc, tm) in enumerate(toks):
            tok = "".join(tc)
            start = text.find(tok, cursor)
            cursor = start + len(tok) if start >= 0 else cursor
            if any(tm):
                nxt = toks[k + 1] if k + 1 < len(toks) else None
                out.append({"page": pno + 1, "chars": tc, "marks": tm,
                            "token": tok, "next": nxt,
                            # Surrounding text, for the symbols that stand alone
                            # as their own token ("99.5 G 1.5 Ma") and so have
                            # nothing distinctive to match on by themselves.
                            "left": text[max(0, start - 40):start] if start >= 0 else "",
                            "right": text[start + len(tok):start + len(tok) + 40] if start >= 0 else ""})
    return out


def new_result(key: str) -> dict:
    return {"key": key, "applied": 0, "already": 0, "unmatched": 0, "short": 0, "drift": 0,
            "unknown": Counter(), "unknown_ctx": {}}


def process(key: str, apply: bool, quiet: bool) -> dict:
    """Repair markdown/{key}.md from pdfs/{key}.pdf (written only with apply)."""
    pdf, md_path = PDF_DIR / f"{key}.pdf", MD_DIR / f"{key}.md"
    if not (pdf.exists() and md_path.exists()):
        return new_result(key)
    try:
        doc = open_pdf(pdf)
    except Exception:
        return new_result(key)
    try:
        md = md_path.read_text(encoding="utf-8")
        new, res = repair(doc, md, key, quiet=quiet)
    finally:
        doc.close()
    if apply and new != md:
        atomic_write_text(md_path, new)
    return res


def repair(doc, md: str, key: str, *, quiet: bool = False) -> tuple[str, dict]:
    """The repaired markdown and the counts, from an open PDF and its markdown.
    No file I/O: process() and the corpus-wide `repair all` driver call this."""
    res = new_result(key)
    hits = occurrences(doc)
    if not hits:
        return md, res

    # Collapse to distinct token repairs. The same running head or faunal list
    # recurs verbatim across pages, and every occurrence needs the same edit.
    def render(chars, marks):
        """Apply the glyph table across a character run; count the dashes."""
        out, dashes = [], 0
        for ch, is_dash in zip(chars, marks):
            if is_dash and ch.strip():
                sub = GLYPHS.get(ch) or KEY_GLYPHS.get((key, ch))
                if sub:
                    out.append(sub)
                    dashes += 1
                    continue
                res["unknown"][ch] += 1
                res["unknown_ctx"].setdefault(ch, "".join(chars)[:60])
            out.append(ch)
        return "".join(out), dashes

    # Marker straightens the PDF's curly quotes, so a token carrying one will
    # not match verbatim.
    QUOTES = str.maketrans({"‘": "'", "’": "'", "“": '"', "”": '"'})

    fixed_of: dict[str, str] = {}
    short_ctx: list[tuple[dict, str, str, str]] = []
    for o in hits:
        fixed, dashes = render(o["chars"], o["marks"])
        token = o["token"]
        if not dashes or token == fixed:
            continue
        # A token is a fused string the PDF has confirmed contains a dash, so
        # every identical token in the same paper is the same construct. Guard
        # only against something too short to be distinctive.
        if len(token) < 5:
            # A symbol that stands alone as its own token ("99.5 ± 1.5 Ma",
            # "width (+)") is too small to match on, but the surrounding words
            # are not. Anchor on those instead of giving up.
            # Only the characters this font actually drew are candidates. A
            # token like "6BeC" carries a real letter C next to a font-drawn
            # dash; selecting by character value would rewrite the C.
            drawn = [c for c, m in zip(token, o["marks"]) if m and (key, c) in KEY_GLYPHS]
            if drawn:
                short_ctx.append((o, token, fixed, drawn[0]))
            else:
                res["short"] += 1
            continue
        cands = [(token, fixed), (token.translate(QUOTES), fixed.translate(QUOTES))]
        # Marker rewrites what sits beside a word — brackets, quotes, a trailing
        # comma — so "(AptianeAlbian)" fails on its parentheses while the word
        # inside is present and intact. The core is stable; try it too. Safe
        # because GLYPHS maps one character to one, so `fixed` stays aligned.
        a, b = 0, len(token)
        while a < b and not token[a].isalnum():
            a += 1
        while b > a and not token[b - 1].isalnum():
            b -= 1
        if (a or b < len(token)) and b - a >= 5:
            cands.append((token[a:b], fixed[a:b]))
        if token.endswith("-") and o["next"]:
            # Rejoin the line break the way marker does, dropping the hyphen.
            nc, nm = o["next"]
            ntext, _ = render(nc, nm)
            cands.append((token[:-1] + "".join(nc), fixed[:-1] + ntext))
        fixed_of[token] = fixed
        res.setdefault("_cands", []).append((cands, o))

    new = md
    seen: set[str] = set()
    leftover: list[tuple[str, str]] = []
    for cands, o in sorted(res.pop("_cands", []), key=lambda t: -len(t[1]["token"])):
        for token, fixed in cands:
            if token in seen:
                break
            if token in new:
                n = new.count(token)
                new = new.replace(token, fixed)
                res["applied"] += n
                seen.add(token)
                if not quiet:
                    print(f"  {key}  x{n}")
                    print(f"      -  {token[:72]}")
                    print(f"      +  {fixed[:72]}")
                break
            if fixed in new:
                res["already"] += 1
                seen.add(token)
                break
        else:
            # Count the distinct token, not each page it recurs on: a running
            # head the markdown never kept would otherwise report once per page
            # and drown the genuine misses.
            if o["token"] not in seen:
                seen.add(o["token"])
                leftover.append((o["token"], fixed_of[o["token"]]))

    # Standalone symbols, anchored on the words either side of them. Whitespace
    # is matched loosely because marker does not preserve the PDF's spacing
    # around a symbol, and the match must be unique or it is refused.
    for o, token, fixed, bad in short_ctx:
        lft = re.sub(r"\s+", " ", o["left"]).strip()[-16:]
        rgt = re.sub(r"\s+", " ", o["right"]).strip()[:16]
        if len(lft) < 3 or len(rgt) < 2:
            res["short"] += 1
            continue
        loose = lambda s: r"\s*".join(re.escape(p) for p in s.split())
        # Match against the markup-stripped view: marker wraps a stray symbol in
        # emphasis ("(99:5*G* 1:5 Ma)") and escapes others ("\$"), so the raw
        # file does not contain the sequence the PDF shows. The index maps the
        # one character being changed back to its real offset.
        flat, idx = plain(new)
        if re.search(loose(lft) + r"\s*" + re.escape(fixed) + r"\s*" + loose(rgt), flat):
            res["already"] += 1
            continue
        pat = re.compile(loose(lft) + r"(\s*" + re.escape(bad) + r"\s*)" + loose(rgt))
        found = list(pat.finditer(flat))
        if len(found) != 1:
            res["short"] += 1
            if not quiet:
                print(f"  ? {key}: {len(found)} match(es) for standalone "
                      f"{bad!r} in {lft!r} … {rgt!r}")
            continue
        at = flat.index(bad, found[0].start(1), found[0].end(1))
        pos = idx[at]
        if new[pos] != bad:
            res["short"] += 1
            continue
        if not quiet:
            print(f"  {key}  x1 (standalone)  {new[max(0,pos-30):pos]!r} "
                  f"{bad!r} -> {fixed!r}")
        new = new[:pos] + fixed + new[pos + 1:]
        res["applied"] += 1

    # Second pass for the tokens the literal search could not find. Marker
    # linkifies bibliography entries and splits the token across the link
    # boundary, so match against a markup-stripped view and write each dash
    # back at its real offset. Edits are applied right-to-left so earlier
    # offsets stay valid; they are all single characters, so nothing shifts.
    if leftover:
        flat, idx = plain(new)
        edits = []
        for token, fixed in leftover:
            spots = [m.start() for m in re.finditer(re.escape(token), flat)]
            if not spots:
                res["unmatched"] += 1
                res.setdefault("misses", []).append(token)
                if not quiet:
                    print(f"  ? {key}: not in markdown — {token[:56]!r}")
                continue
            for p in spots:
                for k, (a, b) in enumerate(zip(token, fixed)):
                    if a != b:
                        edits.append((idx[p + k], a, b))
        for pos, a, b in sorted(set(edits), key=lambda t: -t[0]):
            if new[pos] != a:          # the view drifted; refuse rather than guess
                res["drift"] += 1
                continue
            if not quiet:
                print(f"  {key}  x1 (split by markup)  {new[max(0, pos - 30):pos]!r} "
                      f"{a!r} -> {b!r}  {new[pos + 1:pos + 20]!r}")
            new = new[:pos] + b + new[pos + 1:]
            res["applied"] += 1

    return new, res


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--key", type=key_arg, action="append")
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--audit", action="store_true", help="scope only, change nothing")
    ap.add_argument("--quiet", action="store_true")
    args = ap.parse_args()

    if args.all or args.audit:
        keys = sorted(key_from_path(p) for p in PDF_DIR.glob("*.pdf")
                      if (MD_DIR / f"{key_from_path(p)}.md").exists())
        if args.key:
            keys = [k for k in keys if k in set(args.key)]
    elif args.key:
        keys = args.key
    else:
        ap.error("need --key, --all or --audit")

    applied = already = unmatched = files = short = drift = 0
    unknown, unknown_ctx, misses = Counter(), {}, []
    for key in keys:
        r = process(key, args.apply and not args.audit, args.quiet or args.audit)
        applied += r["applied"]
        already += r["already"]
        unmatched += r["unmatched"]
        short += r["short"]
        drift += r["drift"]
        files += bool(r["applied"])
        unknown.update(r["unknown"])
        unknown_ctx.update(r["unknown_ctx"])
        misses += r.get("misses", [])
        if args.audit and (r["applied"] or r["already"]):
            print(f"  {r['applied'] + r['already']:5d}  {key}")

    verb = "repaired" if (args.apply and not args.audit) else "repairable"
    print(f"\n{applied} dash(es) {verb} over {files} file(s)"
          + (f", {already} already done" if already else ""))
    if misses:
        for label, bucket in sorted(classify(misses).items()):
            print(f"{len(bucket):5d} unrepaired — {DESCRIPTIONS[label]}")
            if args.audit:
                for t in sorted(bucket)[:8]:
                    print(f"          {t}")
    if short or drift:
        print(f"{short} token(s) too short to match safely, "
              f"{drift} where the position check refused the edit")
    if unknown:
        print("\nNOT repaired — this font's non-dash glyphs, for individual review:")
        for ch, n in unknown.most_common():
            print(f"  {ch!r} x{n}   {unknown_ctx[ch]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
