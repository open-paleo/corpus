#!/usr/bin/env python3
# corpus-command: clean transposed-accents | accents a text layer carries as a separate mark beside the letter (Neuque´n, Mus´eum)
"""
Step 2.5: repair transposed diacritics in extracted markdown —
`Neuque´n` -> `Neuquén`, `Mus´eum` -> `Muséum`, `S˜ao` -> `São`,
`Rı´o` -> `Río`, `Man˜ero` -> `Mañero`, `pal ´ eontologique` -> `paléontologique`.

Older Elsevier (and many other) PDFs encode an accented letter as a plain base
letter plus a SPACING accent glyph, so the text layer carries two characters
where there should be one. marker passes the text layer through faithfully, so
the defect lands in the markdown, silently corrupting author names (Márquez,
Rodríguez, Agnolín), place names (Neuquén, Río Negro, São Paulo) and journal
titles (Muséum, Mémoires, Société).

WHICH SIDE DOES THE MARK BELONG TO? Both orderings occur, and the answer is not
positional:

    postfix   `Neuque´n`   the mark follows its base letter   -> Neuquén
    prefix    `Mus´eum`    the mark precedes its base letter  -> Muséum

Treating every mark as postfix quietly mangles the prefix files: `Sol´e`
becomes `Solĺe`, because Unicode really does have a precomposed ĺ. A file
already damaged that way (`Rodŕıguez` for Rodríguez, `Agnoĺın` for Agnolín) is
repaired by --fix-miscomposed.

The side is decided per occurrence, by three tests in order:

  1. WHICH LETTER CAN CARRY THE MARK. In the languages this corpus contains, an
     acute sits on a vowel, a tilde on n/a/o, a cedilla on c/s. So in `Mus´eum`
     only the following "e" is a candidate and the reading is unambiguous. This
     settles about four fifths of all occurrences. Note that Polish ś/ź/ć/ń are
     deliberately NOT candidates: Polish text arrives precomposed ("kości",
     "Niedźwiedzki"), so the repair never needs to produce them, and allowing
     them would reintroduce the Sol´e -> Solĺe failure.
  2. THE FILE'S OWN MAJORITY. Where both neighbours could legally carry the mark
     (`Soci´et´e` — i and e are both vowels), the orderings settled in step 1 for
     the rest of that file cast the deciding vote. A PDF's font encoding is
     consistent within a run of text, so this is a strong signal.
  3. Otherwise the mark is left alone.

WORD JOINS: the same extraction also splits words around the mark — `pal ´
eontologique`, `Prieto-M´ [arquez`, `A´ ngel`. Removing the mark would leave
`pal éontologique`. Whether to close that gap is decided by the corpus itself:
join when the concatenation is attested in files that carry no accent damage
and the orphan fragment is not, which joins paléontologique and Ángel while
leaving `Perú and` (two real words) alone.

Usage:
  ./.runtime/venv/bin/python scripts/fix_transposed_accents.py --key KEY
  ./.runtime/venv/bin/python scripts/fix_transposed_accents.py --apply --key KEY
  ./.runtime/venv/bin/python scripts/fix_transposed_accents.py --audit
  ./.runtime/venv/bin/python scripts/fix_transposed_accents.py --apply   # sweep
  ./.runtime/venv/bin/python scripts/fix_transposed_accents.py --fix-miscomposed --apply
"""

from __future__ import annotations

import argparse
import re
import sys
import unicodedata
from collections import Counter
from pathlib import Path
from corpus.config import MD_DIR
from corpus.keys import key_arg
from corpus.lexicon import document_frequencies
from corpus.pages import write_markdown


# spacing mark in the text layer -> the combining mark NFC understands
MARKS = {
    "´": "́",   # acute
    "ˊ": "́",
    "˜": "̃",   # small tilde
    "~": "̃",   # ASCII tilde ("S~ao Paulo") — see ASCII_MARKS
    "¨": "̈",   # diaeresis
    "`": "̀",   # grave
    "ˆ": "̂",   # circumflex
    "^": "̂",   # ASCII circumflex
    "¸": "̧",   # cedilla
}

# The ASCII forms double as ordinary prose: "~" is "approximately" ("more than
# ~150 km", "ages between ~2623 Ma") and "^" is an exponent. Composing them the
# way the real spacing modifiers are composed produced "thañ150" and "betweeñ".
# They are therefore only ever composed when the letter is directly adjacent AND
# the corpus attests the resulting word — enough for "S~ao Paulo", nothing else.
ASCII_MARKS = {"~", "^"}

# Which base letters may legally carry each mark. Deliberately narrow — see the
# module docstring on Polish ś/ź/ć/ń.
BASES = {
    "́": set("aeiouyı"),      # acute
    "̀": set("aeiou"),        # grave
    "̂": set("aeiouı"),       # circumflex
    "̈": set("aeiouy"),       # diaeresis
    "̃": set("nao"),          # tilde
    "̧": set("cs"),           # cedilla
}
# Where both neighbours are legal for a tilde, "n" wins: Spanish ñ is far more
# common here than Portuguese ã/õ mid-word ("Man˜ero" is Mañero, not Manẽro).
TILDE_PREFER = "n"

# A font may render a mark it does not have with the nearest glyph it does:
# Polish ń arrives as a tilde, and the Hungarian double acute of Ősi arrives as
# a diaeresis. Where such a swap is possible the corpus picks between them —
# ősi is attested in 46 files against ösi in 26, while Höhna (9) keeps its
# diaeresis because hőhna is attested nowhere.
MARK_ALTS = {
    "̃": ("́",),           # tilde  -> acute      (Maryańska)
    "̈": ("̋",),           # umlaut -> double acute (Ősi)
}

SKIP = set("[]*\\_")          # markdown noise the extractor interleaves
GAP = SKIP | {" "}
MARK_CHARS = "".join(MARKS)
LETTER = re.compile(r"[^\W\d_]", re.UNICODE)
WORD_RE = re.compile(r"[^\W\d_]{2,}", re.UNICODE)
# a file with no stray spacing marks is clean enough to vouch for spellings
DAMAGE_RE = re.compile("[" + re.escape("´˜¨`ˆ¸") + "]")


def compose(base: str, combining: str) -> str | None:
    """Return the precomposed character, or None if Unicode has no such form."""
    if base == "ı":                 # dotless i is the extractor's base for í
        base = "i"
    out = unicodedata.normalize("NFC", base + combining)
    return out if len(out) == 1 else None


def choose_mark(text: str, i: int, base_idx: int, side: str, comb: str,
                df: dict[str, int]) -> str:
    """Some fonts render Polish ń with a tilde glyph, so "Maryan˜ska" composes
    to Maryañska where the corpus overwhelmingly writes Maryańska (111 files vs
    6). Where a tilde lands on an n, let the better-attested spelling pick the
    mark; every other mark is used as found."""
    if comb != "̃" or text[base_idx].lower() != "n":
        return comb
    alt = "́"
    here, other = compose(text[base_idx], comb), compose(text[base_idx], alt)
    if not here or not other:
        return comb
    w_here = resulting_word(text, i, base_idx, side, here).lower()
    w_other = resulting_word(text, i, base_idx, side, other).lower()
    return alt if df.get(w_other, 0) > df.get(w_here, 0) else comb


# --- corpus lexicon (accent-aware) ---------------------------------------

def load_lexicon(md_dir: Path, rebuild: bool = False) -> dict[str, int]:
    """Document frequency of each word across files with no stray spacing mark."""
    return document_frequencies(md_dir, "accent_lexicon", DAMAGE_RE.search, WORD_RE, rebuild=rebuild)


# --- per-occurrence analysis ---------------------------------------------

def neighbours(text: str, i: int):
    """Indices of the nearest real letters either side of the mark at i,
    stepping over markdown noise and single spaces."""
    j = i - 1
    while j >= 0 and text[j] in GAP:
        j -= 1
    k = i + 1
    while k < len(text) and text[k] in GAP:
        k += 1
    left = j if j >= 0 and LETTER.match(text[j]) else None
    right = k if k < len(text) and LETTER.match(text[k]) else None
    return left, right


def side_options(text: str, i: int):
    """(can_post, can_pre) — whether each neighbour may legally carry the mark."""
    comb = MARKS[text[i]]
    bases = BASES[comb]
    left, right = neighbours(text, i)
    can_post = left is not None and text[left].lower() in bases \
        and compose(text[left], comb) is not None
    can_pre = right is not None and text[right].lower() in bases \
        and compose(text[right], comb) is not None
    if comb == "̃" and can_post and can_pre:
        # tilde tiebreak: prefer n over a/o (Mañero, not Manẽro)
        if text[left].lower() == TILDE_PREFER:
            can_pre = False
        elif text[right].lower() == TILDE_PREFER:
            can_post = False
    return can_post, can_pre


def file_majority(text: str) -> str | None:
    votes = Counter()
    for i, ch in enumerate(text):
        if ch not in MARKS:
            continue
        can_post, can_pre = side_options(text, i)
        if can_post and not can_pre:
            votes["post"] += 1
        elif can_pre and not can_post:
            votes["pre"] += 1
    if not votes:
        return None
    if votes["pre"] > votes["post"]:
        return "pre"
    if votes["post"] > votes["pre"]:
        return "post"
    return None


def resulting_word(text: str, i: int, base_idx: int, side: str, composed: str) -> str:
    """The word the repair would produce, with the mark's gap closed."""
    if side == "post":
        head = word_before(text, base_idx)
        tail = word_after(text, skip_gap_index(text, i + 1))
    else:
        head = word_before(text, left_letter_end(text, i))
        tail = word_after(text, base_idx + 1)
    return head + composed + tail


def left_letter_end(text: str, i: int) -> int:
    """Index just past the last real letter before position i."""
    j = i - 1
    while j >= 0 and text[j] in GAP:
        j -= 1
    return j + 1


def attested(text: str, i: int, base_idx: int, side: str, composed: str,
             mark: str, df: dict[str, int]) -> bool:
    """Guard for repairs that reach across a space, and for the ASCII marks.

    A mark separated from its letter by a space is as often STRANDED as it is a
    split word: the extractor flushes it at the end of a run, far from where it
    belongs ("Neuquen, Patagonia, ´ Argentina" — the accent is Neuquén's). Such
    a repair is only accepted when the corpus attests the word it produces, so
    paläontologique and Ángel go through while Árgentina and Osmolská do not.
    Directly-adjacent marks need no such vote: they are unambiguous, and rare
    names (Iarochène, df=3) must not be held hostage to their own rarity.
    """
    gap = text[base_idx + 1:i] if side == "post" else text[i + 1:base_idx]
    crossed = " " in gap
    if mark in ASCII_MARKS and crossed:
        return False
    word = resulting_word(text, i, base_idx, side, composed).lower()
    if crossed:
        # a two- or three-letter result is a fragment the stranded mark landed
        # on, not a word: "és", "ão", "Má"
        if len(word) < 4:
            return False
        return df.get(word, 0) >= 2
    return mark not in ASCII_MARKS or df.get(word, 0) >= 2


def decide(text: str, i: int, majority: str | None):
    """Which neighbour carries the mark at i: 'post', 'pre', or None."""
    can_post, can_pre = side_options(text, i)
    if can_post and not can_pre:
        return "post"
    if can_pre and not can_post:
        return "pre"
    if can_post and can_pre:
        return majority           # both legal — the file's own habit decides
    return None


def structural(gap: str, wrapper: str | None) -> str:
    """The gap characters worth keeping: spaces go, and an emphasis pair that
    wrapped nothing but the mark goes with them ("Pal*´*eontologie" must not
    leave a stray "**", which would split the word for the FTS tokenizer).
    Link brackets are kept — they carry markdown structure."""
    keep = [c for c in gap if c != " "]
    if wrapper and wrapper in keep:
        keep.remove(wrapper)
    return "".join(keep)


def fix(text: str, df: dict[str, int], log: dict | None = None):
    majority = file_majority(text)
    out: list[str] = []
    i = 0
    n_fix = 0
    n = len(text)
    while i < n:
        ch = text[i]
        if ch not in MARKS:
            out.append(ch)
            i += 1
            continue
        side = decide(text, i, majority)
        if side is None:
            out.append(ch)
            i += 1
            continue

        comb = MARKS[ch]
        left, right = neighbours(text, i)
        base_idx = left if side == "post" else right
        comb = choose_mark(text, i, base_idx, side, comb, df)
        composed = compose(text[base_idx], comb)
        if composed is None or not attested(text, i, base_idx, side, composed,
                                            ch, df):
            out.append(ch)
            i += 1
            continue

        if side == "post":
            gap = text[base_idx + 1:i]
            del out[len(out) - len(gap) - 1:]          # drop base + gap
            out.append(composed)
            out.append(structural(gap, None))
            i += 1
            head = word_before(text, base_idx) + composed
            after = skip_gap_index(text, i)
            if after > i and decide_join(head, word_after(text, after), df):
                i = after                              # "A´ ngel" -> "Ángel"
        else:
            gap = text[i + 1:base_idx]
            # an emphasis char immediately before the mark that reappears in the
            # gap wrapped only the mark; drop the pair
            wrapper = text[i - 1] if i and text[i - 1] in "*_" else None
            if wrapper and wrapper in gap:
                out.pop()
            else:
                wrapper = None
            head = word_before(text, left_letter_end(text, i))
            keep = structural(gap, wrapper)
            if head and decide_join(head, composed + word_after(text, base_idx + 1), df):
                while out and out[-1] == " ":
                    out.pop()
                # re-open a link before the whole word, not mid-word, so the
                # name stays one token: "Prieto-M´ [arquez" -> "Prieto-[Márquez"
                if keep:
                    del out[len(out) - len(head):]
                    out.append(keep)
                    out.append(head)
            else:
                out.append(keep)
            out.append(composed)
            i = base_idx + 1
        if log is not None:
            log[(ch, side)] = log.get((ch, side), 0) + 1
        n_fix += 1
    return "".join(out), n_fix


def skip_gap_index(text: str, i: int) -> int:
    while i < len(text) and text[i] in GAP:
        i += 1
    return i


def word_before(text: str, idx: int) -> str:
    j = idx
    while j > 0 and LETTER.match(text[j - 1] or " "):
        j -= 1
    return text[j:idx]


def word_after(text: str, idx: int) -> str:
    k = idx
    while k < len(text) and LETTER.match(text[k]):
        k += 1
    return text[idx:k]


def decide_join(left: str, right: str, df: dict[str, int]) -> bool:
    """Join two fragments only when the corpus attests the whole and not the
    orphan — paléontologique / Ángel yes, "Perú" + "and" no."""
    if not left or not right:
        return False
    whole = (left + right).lower()
    if df.get(whole, 0) >= 2 and df.get(right.lower(), 0) < 2:
        return True
    return False


# --- stranded marks -------------------------------------------------------
# The worst extractions flush the accent at the END of a text run, arbitrarily
# far from the letter it belongs to: "Universidad ´ Maimonides" is Maimónides,
# ", ´ Av. Angel Gallardo" is Ángel, "Osmolska, H. ´" is Osmólska, "Lu, J. ¨" is
# Lü. Neither neighbour can legally carry the mark, so the main pass leaves them
# alone. Here the corpus is asked instead: place the mark on every legal letter
# of every unaccented word within a window, and keep the placement only when
# exactly ONE spelling in that window is attested by undamaged files AND is
# better attested than the word as written. Requiring uniqueness is what makes
# this safe — where two readings compete the mark stays put.
WINDOW = 60
STRANDED_DF_MIN = 2


def stranded_candidates(text: str, i: int, df: dict[str, int]):
    comb = MARKS[text[i]]
    bases = BASES[comb]
    lo, hi = max(0, i - WINDOW), min(len(text), i + WINDOW)
    out = []
    for m in WORD_RE.finditer(text, lo, hi):
        w = m.group(0)
        if any(ord(c) > 127 for c in w):
            continue                       # already carries an accent
        for k, ch in enumerate(w):
            if ch.lower() not in bases:
                continue
            best = None
            for alt in (comb,) + MARK_ALTS.get(comb, ()):
                c = compose(ch, alt)
                if not c:
                    continue
                cand = w[:k] + c + w[k + 1:]
                score = df.get(cand.lower(), 0)
                if best is None or score > best[0]:
                    best = (score, cand)
            if best and best[0] >= STRANDED_DF_MIN and \
               best[0] > df.get(w.lower(), 0):
                out.append((m.start(), m.end(), w, best[1]))
    return out


def fix_stranded(text: str, df: dict[str, int], log: dict | None = None):
    edits = []
    claimed = set()
    for i, ch in enumerate(text):
        if ch not in MARKS or ch in ASCII_MARKS or ch == "`":
            # backticks in this corpus are overwhelmingly markdown code fences
            # ("``` Figs. 2e10 ```"), and consuming one would break the fence
            continue
        # Every mark still present has already been declined by the main pass —
        # including ones with a legal neighbour whose word the corpus rejected
        # (", ´ Av. Angel Gallardo" is Ángel, not Áv) — so all of them are
        # offered to the windowed search.
        cands = stranded_candidates(text, i, df)
        if not cands or len({c[3] for c in cands}) != 1:
            continue
        start, end, word, fixed = cands[0]
        if start in claimed:
            continue                       # one accent per target word
        claimed.add(start)
        edits.append((start, end, fixed))
        edits.append((i, i + 1, ""))       # remove the stranded mark
        if log is not None:
            k = f"{word} -> {fixed}"
            log[k] = log.get(k, 0) + 1
    if not edits:
        return text, 0
    edits.sort()
    out = []
    prev = 0
    for start, end, rep in edits:
        out.append(text[prev:start])
        out.append(rep)
        prev = end
    out.append(text[prev:])
    new = "".join(out)
    # the removed mark leaves a doubled space behind
    new = re.sub(r"[ \t]{2,}", " ", new)
    return new, len(edits) // 2


# --- repair of already-miscomposed consonants ----------------------------
# The old postfix-only rule composed the mark onto whatever preceded it, and
# Unicode obliges for ŕ ĺ ḿ ṕ ǵ ḱ ẃ. The accent belonged to the vowel that
# follows: "Rodŕıguez" is Rodríguez, "Agnoĺın" is Agnolín.
MISCOMPOSED = {
    "ŕ": "r", "ĺ": "l", "ḿ": "m", "ṕ": "p",
    "ǵ": "g", "ḱ": "k", "ẃ": "w", "ẗ": "t",
}
MIS_RE = re.compile("([" + "".join(MISCOMPOSED) + "])([aeiouyıAEIOUY])")


def fix_miscomposed(text: str, log: dict | None = None):
    def repl(m):
        bad, vowel = m.group(1), m.group(2)
        moved = compose(vowel, "́")
        if moved is None:
            return m.group(0)
        if log is not None:
            k = f"{bad}{vowel} -> {MISCOMPOSED[bad]}{moved}"
            log[k] = log.get(k, 0) + 1
        return MISCOMPOSED[bad] + moved
    return MIS_RE.subn(repl, text)


# --- CLI ------------------------------------------------------------------

def targets(md_dir: Path, keys):
    if keys:
        files = [md_dir / f"{k}.md" for k in keys]
        missing = [f for f in files if not f.exists()]
        if missing:
            sys.exit(f"missing: {[m.name for m in missing]}")
        return files
    return sorted(md_dir.glob("*.md"))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--key", type=key_arg, action="append",
                    help="limit to these keys; repeatable. Omit to sweep markdown/")
    ap.add_argument("--apply", action="store_true", help="write changes")
    ap.add_argument("--audit", action="store_true",
                    help="print every distinct word-level change")
    ap.add_argument("--fix-miscomposed", action="store_true",
                    help="repair ŕı/ĺı damage left by the old postfix-only rule")
    ap.add_argument("--stranded", action="store_true",
                    help="resolve marks flushed far from their word, via the corpus")
    ap.add_argument("--rebuild-lexicon", action="store_true")
    args = ap.parse_args()

    df = load_lexicon(MD_DIR, rebuild=args.rebuild_lexicon)
    files = targets(MD_DIR, args.key)
    mode = "APPLY" if args.apply else "DRY RUN (no files written)"
    print(f"=== fix_transposed_accents — {mode} ===\n")

    changed = total = 0
    words: dict[str, int] = {}
    for f in files:
        text = f.read_text(encoding="utf-8", errors="replace")
        if args.fix_miscomposed:
            new, n = fix_miscomposed(text, words)
        elif args.stranded:
            new, n = fix_stranded(text, df, words)
        else:
            new, n = fix(text, df, None)
            if args.audit and n:
                collect_word_changes(text, new, words)
        if not n:
            continue
        changed += 1
        total += n
        if not args.audit:
            print(f"  {f.name}: {n} marks repaired")
        if args.apply:
            write_markdown(f, new, old=text)

    if args.audit or args.fix_miscomposed or args.stranded:
        print(f"--- {len(words)} distinct changes ---")
        for k, v in sorted(words.items(), key=lambda kv: (-kv[1], kv[0])):
            print(f"  {v:5d}  {k}")
    print(f"\nfiles scanned:  {len(files)}")
    print(f"files changed:  {changed}")
    print(f"marks repaired: {total}")
    if not args.apply and changed:
        print("\nRe-run with --apply to write. Then re-index:")
        print("  ./.runtime/venv/bin/python scripts/indexer.py")
    return 0


def collect_word_changes(old: str, new: str, words: dict[str, int]) -> None:
    """Word-level before/after pairs, for review."""
    ow = WORD_RE.findall(old.replace("´", "\x00"))
    a = Counter(w for w in WORD_RE.findall(new) if any(ord(c) > 127 for c in w))
    b = Counter(w for w in WORD_RE.findall(old) if any(ord(c) > 127 for c in w))
    for w, c in (a - b).items():
        words[w] = words.get(w, 0) + c


if __name__ == "__main__":
    raise SystemExit(main())
