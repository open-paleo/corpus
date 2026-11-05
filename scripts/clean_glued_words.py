#!/usr/bin/env python3
# corpus-command: clean glued-words | re-insert spaces a text layer never emitted ("KaiparowitsFormation")
"""Re-insert spaces the PDF's text layer never emitted (corpus-gated).

Some text layers drop the space character between words entirely, usually
around a capital or after an abbreviation: "KaiparowitsFormation", "theAguja",
"TheState Museum", "NorthAmericafive". marker passes the text layer through
faithfully, so the defect lands in the markdown — and it is invisible to every
byte-count check while quietly making the phrase unsearchable, since the FTS
tokenizer sees one token where there should be two.

This is the mirror image of clean_despace.py, which only ever REMOVES spaces to
repair scan OCR ("numerou s" -> "numerous"). This pass only ever ADDS them, and
never rewrites a letter, so the two are safe to run over the same corpus.

The corpus decides every split, by document frequency (corpus.lexicon), as
`corpus clean ligatures` does:

  - a word the corpus already attests is never touched, which protects German
    and Portuguese compounds (Wirbelsäule), surnames (DeMar, McIntosh) and
    taxon names;
  - both halves must be well attested on their own (>= MIN_HALF files), so
    "Kaiparowits" + "Formation" splits while a rare fragment pair does not;
  - among the candidate splits, the winner is the one maximising
    df(left) * df(right), and it must beat the runner-up by MARGIN. A split at
    a lower-to-upper boundary is weighted, that being the defect's signature.

The lexicon is built only from files that do NOT show the defect. Building it
over everything is circular: "formationis" and "areview" occur across dozens of
damaged files, so they read as real words and protect the very glue being
repaired.

Splitting recurses, so a triple glue ("theAquilanandJudithian") comes apart in
one pass. All-caps tokens (specimen and institution codes) and anything
containing a digit are skipped outright.

Usage:
    python scripts/clean_glued_words.py --key KEY          # dry-run
    python scripts/clean_glued_words.py --key KEY --apply
    python scripts/clean_glued_words.py --scan                       # census
    python scripts/clean_glued_words.py --audit                      # corpus dry-run
"""
import argparse
import re
import sys
from pathlib import Path

from corpus.config import MD_DIR

from corpus.keys import key_arg
from corpus.lexicon import document_frequencies
from corpus.pages import write_markdown


# A file's own glue count decides whether it may vouch for spellings. Without
# this the oracle is circular: "formationis" and "areview" appear in dozens of
# damaged files, so a lexicon built over everything treats them as real words
# and protects the very defect being repaired. The tell is a lower-to-upper
# transition inside a token, which is near-zero in an undamaged file and runs to
# dozens in a broken one.
GLUE_TELL = re.compile(r"[a-z]{3}[A-Z][a-z]{2}")
GLUE_TELL_MAX = 8
LEX_WORD = re.compile(r"[^\W\d_]{2,}", re.UNICODE)

MIN_WORD = 6         # shorter tokens are not worth the risk
MIN_HALF = 20        # each half must be this well attested (in files)
MIN_HALF_LEN = 3     # ...and this long, unless it is a SHORT_OK word
MARGIN = 3           # how decisively the best split must beat the runner-up
PROTECT_DF = 5       # df at which a word is presumed real and left alone. Above
                     # the noise floor of glued forms, which leak into the
                     # lexicon from other damaged files ("newgenus" sits at 2)
                     # and would otherwise protect the very defect being fixed
TOKEN = re.compile(r"[^\W\d_]+", re.UNICODE)  # Unicode: keeps "Wirbelsäule" whole
HAS_DIGIT = re.compile(r"\d")

# Genuine one- and two-letter words. Without them the only splits available for
# "Areview" and "Formationis" are the wrong ones — "Are view", "Formatio nis" —
# because the right reading needs a half below MIN_HALF_LEN.
SOLO = {"a", "i"}    # one-letter words the lexicon cannot score
SOLO_DF = 2000       # treat them as universally attested
CASE_BONUS = 8       # weight for a split at a case boundary

SHORT_OK = {"a", "i", "an", "as", "at", "be", "by", "do", "he", "if", "in",
            "is", "it", "no", "of", "on", "or", "so", "to", "up", "us", "we"}


def load_lexicon(md_dir: Path, rebuild: bool = False) -> dict[str, int]:
    """Document frequency over files that do NOT show the glue defect."""
    return document_frequencies(md_dir, "glue_lexicon",
                                lambda text: len(GLUE_TELL.findall(text)) > GLUE_TELL_MAX,
                                LEX_WORD, rebuild=rebuild)


def dfv(word: str, df: dict[str, int]) -> int:
    """Document frequency, with the one-letter words the lexicon cannot hold."""
    w = word.lower()
    return SOLO_DF if w in SOLO else df.get(w, 0)


def split_once(word: str, df: dict[str, int], depth: int = 0):
    """The best (left, right) split into two well-attested words, or None.

    Scored by df(left) * df(right) rather than by uniqueness: several splits
    are usually available and the wrong ones are made of rare fragments.
    "Formationis" offers both "Formation|is" (2000 x 2000) and "Formatio|nis"
    (a Latin stem and a fragment), and the product separates them cleanly. The
    winner must beat the runner-up by MARGIN, otherwise the word is left alone.
    """
    if len(word) < MIN_WORD or word.isupper() or HAS_DIGIT.search(word):
        return None
    if df.get(word.lower(), 0) >= PROTECT_DF:
        return None                       # the corpus knows this word as-is
    scored = []
    for k in range(1, len(word)):
        a, b = word[:k], word[k:]
        al, bl = a.lower(), b.lower()
        if len(a) < MIN_HALF_LEN and al not in SHORT_OK:
            continue
        # a one-letter RIGHT half is nearly always a Latin ending, not a word:
        # "hemispherica" is an epithet, not "hemispheric a"
        if len(b) < MIN_HALF_LEN and (bl not in SHORT_OK or len(b) == 1):
            continue
        # the lexicon indexes tokens of two letters or more, so the universal
        # one-letter words have no frequency of their own
        da, db = dfv(a, df), dfv(b, df)
        if da < MIN_HALF:
            continue
        if db < MIN_HALF:
            # the right half may be unattested yet still be two glued words:
            # "Anewenantiornithine" is A + new + enantiornithine, and without
            # this lookahead the only reading available is "Anew enantiornithine"
            if depth >= 2:
                continue
            inner = split_once(b, df, depth + 1)
            if not inner:
                continue
            # score the lookahead by its weakest part. The default must NOT be
            # SOLO_DF: defaulting every unattested fragment to a solo word's
            # weight invented "Form|ationis" (via "a"+"tionis") and let it rival
            # the true "Formation|is"
            db = min(dfv(inner[0], df), dfv(inner[1], df))
        score = da * db
        # a lower-to-upper transition is the classic glue signature
        # ("KaiparowitsFormation", "andScience"), and a title opening with a
        # lone "A"/"I" is the other ("Areview" is "A review", not "Are view")
        if b[:1].isupper() and a[-1:].islower():
            score *= CASE_BONUS
        elif len(a) == 1 and a in ("A", "I") and b[:1].islower():
            score *= CASE_BONUS
        scored.append((score, a, b))
    if not scored:
        return None
    scored.sort(reverse=True)
    if len(scored) > 1 and scored[0][0] < MARGIN * scored[1][0]:
        return None                       # two plausible readings — leave it
    return scored[0][1], scored[0][2]


def split_word(word: str, df: dict[str, int]) -> str:
    parts = split_once(word, df)
    if not parts:
        return word
    a, b = parts
    # a triple glue comes apart by recursing on the right half
    return a + " " + split_word(b, df)


def is_damaged(text: str) -> bool:
    """Does this file actually show the glue defect?

    Without this gate the pass runs on undamaged files and shreds their rare
    words instead: on a file with no tells it proposes "steinkern" ->
    "ste ink ern", "Hornerstown" -> "Horne rst own", "Berberian" ->
    "Ber ber ian". Those are real terms whose FRAGMENTS happen to be attested,
    and no per-word rule can be made tight enough to survive a whole corpus of
    them. The separation is sharp in practice: a damaged file shows dozens of
    tells, an undamaged one none. It is the same threshold that keeps a file out of the
    lexicon, so a file either vouches for spellings or gets repaired, never both.
    """
    return len(GLUE_TELL.findall(text)) > GLUE_TELL_MAX


def fix(text: str, df: dict[str, int], log: dict | None = None,
        force: bool = False):
    if not force and not is_damaged(text):
        return text, 0
    n_fix = 0

    def repl(m: re.Match) -> str:
        word = m.group(0)
        out = split_word(word, df)
        if out == word:
            return word
        nonlocal n_fix
        n_fix += 1
        if log is not None:
            k = f"{word} -> {out}"
            log[k] = log.get(k, 0) + 1
        return out

    return TOKEN.sub(repl, text), n_fix


def targets(md_dir: Path):
    return [p for p in sorted(md_dir.glob("*.md"))
            if not p.name.endswith(".original.md")]


def main() -> int:
    ap = argparse.ArgumentParser()
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--key", type=key_arg, action="append", help="citation key; repeatable")
    g.add_argument("--scan", action="store_true", help="census affected files")
    g.add_argument("--audit", action="store_true", help="corpus-wide dry run")
    g.add_argument("--all", action="store_true", help="repair every file")
    ap.add_argument("--apply", action="store_true", help="write changes")
    ap.add_argument("--limit", type=int, default=40)
    ap.add_argument("--force", action="store_true",
                    help="process a file even if it shows no glue tells")
    args = ap.parse_args()

    df = load_lexicon(MD_DIR)

    if args.scan or args.audit or args.all:
        log: dict = {}
        rows = []
        for p in targets(MD_DIR):
            src = p.read_text(encoding="utf-8", errors="replace")
            out, n = fix(src, df, log, force=args.force)
            if not n:
                continue
            rows.append((n, p.stem))
            if args.all and args.apply:
                write_markdown(p, out, old=src)
        rows.sort(reverse=True)
        print(f"files affected: {len(rows)}   splits: {sum(r[0] for r in rows)}"
              f"   distinct: {len(log)}")
        if args.scan:
            for n, k in rows[:60]:
                print(f"  {n:5d}  {k}")
        else:
            items = sorted(log.items(), key=lambda kv: (-kv[1], kv[0]))
            for k, v in items[:args.limit]:
                print(f"  {v:5d}  {k}")
            print("  ...")
            for k, v in items[-args.limit:]:
                print(f"  {v:5d}  {k}")
        if args.all and not args.apply:
            print("\n  (dry run — add --apply to write)")
        return 0

    for key in args.key:
        p = MD_DIR / f"{key}.md"
        if not p.exists():
            sys.exit(f"not found: {p}")
        src = p.read_text(encoding="utf-8")
        log = {}
        out, n = fix(src, df, log, force=args.force)
        if not n and not args.force:
            print(f"{p.name}: no glue defect ({len(GLUE_TELL.findall(src))} tells)"
                  f" — skipped; --force to override")
            continue
        print(f"{p.name}: {n} splits, {len(log)} distinct")
        for k, v in sorted(log.items(), key=lambda kv: (-kv[1], kv[0])):
            print(f"  {v:4d}  {k}")
        if args.apply and n and write_markdown(p, out, old=src):
            print("  APPLIED")
    return 0


if __name__ == "__main__":
    sys.exit(main())
