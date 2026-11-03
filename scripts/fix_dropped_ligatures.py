#!/usr/bin/env python3
# corpus-command: clean ligatures | restore f- and Th-ligatures a text layer dropped
"""Restore f-ligatures dropped by a PDF's own text layer (corpus-gated).

Many born-digital publisher PDFs (Elsevier is the worst offender; Springer,
Wiley and Taylor & Francis vintages do it too) carry a broken ToUnicode map for
the f-ligature glyphs: ff / fi / fl / ffi / ffl each decode to a bare "f". The
text layer then reads "Refned geochronology", "signifcant", "frst", "diferent",
"difcult", "briefy". marker passes that through faithfully — the defect is in
the PDF, not in the conversion — so re-running marker is a no-op, and vision
re-OCR is a $1/paper answer to a mechanical problem.

Restoration is a pure INSERTION after an existing "f" — one of {f, i, l, fi,
fl}, the four ligatures' missing tails — so like despace_ocr.py this can only
add the letters the font map swallowed, never rewrite one. Character misreads
are out of scope; those need vision re-OCR.

WHY NOT A PLAIN DICTIONARY GATE: /usr/share/dict/words (web2) is a 1913 lemma
list. It lacks "feet", "favour", "floodplain", "Garfield" and every proper noun,
so "is the token already a real word?" cannot be answered from it — a naive gate
rewrites feet->fleet, Fuentes->Fluentes, afer->affier. Instead the
corpus supplies the oracle. Files carrying none of the unambiguous break
signatures (TELLS) are "clean"; their vocabulary is by construction legitimate
spelling, and the count of distinct clean files a token appears in (its document
frequency) decides both questions:

  - PROTECT: a token clean files also use is a real spelling in SOME language,
    and is overruled only when the corpus prefers the repaired form by >=10x.
    This is what saves feet, favour, Fuentes, Kiefer and Spanish "diferentes",
    while still fixing German-looking "feld" in favour of "field".
  - ACCEPT:  a candidate repair must be a web2 headword outright, or appear in
    >=2 clean files. The corpus half admits the inflections, compounds and
    proper nouns web2 omits — identified, floodplain, fieldwork, Garfield.
    Inflection-guessing is deliberately NOT an accept path here: it would let
    Spanish "diferentes" reach "differentes" via the English stem "different".

A repair fires only when exactly one candidate is accepted; two competing
readings are ambiguous and left alone. Tokens with an interior capital
(acronyms: GRIF, BIFs, CHUFS) are skipped outright, as are tokens inside URLs.

The lexicon is cached at .runtime/cache/ligature_lexicon.json, keyed on a
corpus signature (file count + total bytes), and rebuilt automatically when the
markdown corpus changes. --rebuild-lexicon forces it.

A SMALL CORPUS CANNOT BE ITS OWN ORACLE. With a few dozen papers the clean
lexicon attests almost nothing, so most repairs are never accepted (a
two-paper corpus repairs about a third of its breaks and no "Te"). Below
MIN_CLEAN_FILES clean files the script therefore takes word frequencies from
the `wordfreq` package instead (WordFreqLexicon: Zipf frequencies in the
paper's own language and English, plus the domain vocabulary of the data
repo's public data, below), with the same PROTECT/ACCEPT structure:
a token common as written is left alone, an unknown one is repaired to a
reasonably common reading, a rare one only to a reading 10x more frequent.
--lexicon corpus|wordfreq|auto picks explicitly. wordfreq mode never reads the
system word list, so it also works where /usr/share/dict/words is missing.

Usage:
    python scripts/fix_dropped_ligatures.py --key KEY          # dry-run
    python scripts/fix_dropped_ligatures.py --key KEY --apply  # .bak + write
    python scripts/fix_dropped_ligatures.py --path some/file.md --top     # ad-hoc
    python scripts/fix_dropped_ligatures.py --scan                        # census
    python scripts/fix_dropped_ligatures.py --audit                       # corpus dry-run

Run it during intake on any born-digital paper whose markdown shows the tell
("frst", "signifcant", "identifed"), then re-index. --apply saves
markdown/{key}.md.ligature.bak first.
"""
import argparse
import json
import re
import sys
from collections import Counter
from pathlib import Path
from corpus.config import ROOT as ARCHIVE, MD_DIR
from corpus.keys import key_arg
from corpus.words import WORDS as _WORDS, wordlist_path
from corpus.io import atomic_write_text
from corpus.pages import write_markdown

CACHE = ARCHIVE / ".runtime" / "cache" / "ligature_lexicon.json"

DICT_PATH = wordlist_path()
WORDS = _WORDS   # read on first use

# The tails an f-ligature loses when its glyph decodes to a bare "f":
#   ff -> f (lost "f"), fi -> f (lost "i"), fl -> f (lost "l"),
#   ffi -> f (lost "fi"), ffl -> f (lost "fl"), ft -> f (lost "t")
# "ft" is not one of the four Latin-1 ligature codepoints, but plenty of text
# faces ligate it and the same broken ToUnicode map eats it: "shaf" for "shaft",
# "sofware", "Naturwissenschafen", "Zeitschrif". It also
# disambiguates "draf", which without it repairs to the archaic "draff" instead
# of the intended "draft" — with "t" present both readings compete and the token
# is correctly left alone.
TAILS = ("f", "i", "l", "fi", "fl", "t")

MIN_LEN = 3          # 3 is where the frequent short breaks live — "lef" (left),
                     # "fve", "fne", "fsh", "tuf"; 2-char tokens are
                     # specimen-code and abbreviation noise
DF_ACCEPT = 2        # clean-file document frequency that vouches for a repair
DF_RATIO = 10        # how decisively the corpus must prefer the repaired spelling
                     # over an original that clean files also attest
MIN_CLEAN_FILES = 500  # below this, the clean corpus is too small to be the oracle

# wordfreq thresholds, in Zipf units (log10 of occurrences per billion words;
# "the" is 7.7, "fibula" 2.6, "ossified" 2.1, a web typo like "diferent" 2.0).
Z_REAL = 3.0         # a token this common as written is a real word: leave it
Z_ACCEPT = 1.5       # an unknown token's repair must be at least this common
Z_RATIO = 1.0        # a rare token's repair must be this much more common (10x)
Z_TH = 3.0           # a Th repair must be common in English ("TH_DF_MIN")
Z_COMMON = 5.0       # a reading this common overrides another language's claim on a
                     # capitalised token ("Lef" is Dutch, but "Lef tibia" is Left)
DOC_LANGS = ("en", "es", "pt", "fr", "de", "it", "nl", "pl", "ca", "sv", "cs")
Z_DOMAIN = 3.5       # Zipf value given to a word of the domain vocabulary


def domain_vocabulary() -> set[str]:
    """Lowercase words of the data repo's public data: references.bib (author
    surnames, titles, journals) and dist/open-paleo.json (taxon names,
    descriptions, diagnostic features; CC-BY 4.0). It supplies what a general
    word list lacks: Theropoda, Thyreophora, tibiofibular, fibrolamellar,
    midshaft, Goloboff, Buffetaut. Nothing here comes from the papers. Words
    seen only once are dropped (typos in the data). Empty when neither file
    is available locally (corpus.bib finds them; nothing is downloaded here)."""
    return _domain()[0]


def domain_names() -> set[str]:
    """Lowercase genus and clade names of the data repo (the keys of
    open-paleo.json's "genera" and "clades"). Unlike its prose these are
    curated, so they may also PROTECT a token as written: "Fona herzogae"
    must not become "Fiona"."""
    return _domain()[1]


_DOMAIN_CACHE: tuple[set[str], set[str]] | None = None


def _domain() -> tuple[set[str], set[str]]:
    global _DOMAIN_CACHE
    if _DOMAIN_CACHE is not None:
        return _DOMAIN_CACHE
    from corpus.bib import BUNDLED_BIB
    from corpus.config import cache_dir, data_repo
    texts = []
    for path in (data_repo() / "dist" / "references.bib", BUNDLED_BIB,
                 cache_dir("bib") / "references.bib",
                 data_repo() / "dist" / "open-paleo.json",
                 cache_dir("bib") / "open-paleo.json"):
        if path.exists():
            texts.append(path.read_text(encoding="utf-8", errors="replace"))
    counts = Counter()
    names: set[str] = set()
    for text in texts:
        counts.update(w.lower() for w in WORD_RE.findall(text) if len(w) >= MIN_LEN)
        if text.lstrip().startswith("{"):
            try:
                export = json.loads(text)
            except ValueError:
                continue
            for kind in ("genera", "clades"):
                names |= {n.lower() for n in (export.get(kind) or {})}
    _DOMAIN_CACHE = ({w for w, n in counts.items() if n >= 2}, names)
    return _DOMAIN_CACHE

TOK = re.compile(r"[A-Za-z]+|[^A-Za-z]+")
WORD_RE = re.compile(r"[A-Za-z]{2,}")

# Unambiguous break signatures — no entry may be a real word in ANY language the
# corpus contains. A file matching one is "broken" and contributes nothing to
# the clean lexicon. Note the omission of "diferent*": it is correct Spanish and
# Portuguese, and listing it would condemn Romance-language papers as broken,
# which both poisons the lexicon and lets the repair rewrite their prose.
TELLS = re.compile(
    r"\b(signifcant\w*|identifed|specifc\w*|defned|confrm\w*|classifcation"
    r"|scientifc|frst|refned|difcult\w*|coefcient\w*|sufcient\w*"
    r"|fgure|fgures|ofset|afnit\w*|artifcial\w*|modifed|ossifed)\b",
    re.IGNORECASE)

# web2 is a lemma list — it lacks nearly every inflected form — so a bare
# membership test rejects "confirmed", "findings", "identified". These are the
# regular English inflections, applied in reverse to recover a lemma. Used for
# the PROTECT side (rejecting originals), where over-matching is the safe
# direction, and as one of the two ACCEPT paths.
SUFFIX_RULES = (
    ("ies",  ("y",)),
    ("ied",  ("y",)),
    ("iest", ("y",)),
    ("ings", ("", "e")),
    ("ing",  ("", "e")),
    ("edly", ("", "e")),
    ("ed",   ("", "e")),
    ("es",   ("", "e")),
    ("s",    ("",)),
    ("ly",   ("", "e")),
    ("ness", ("", "e")),
    ("ment", ("", "e")),
)


def strict_word(t: str) -> bool:
    return t.lower() in WORDS


def loose_word(t: str) -> bool:
    """Dictionary word or a regular inflection of one."""
    tl = t.lower()
    if tl in WORDS:
        return True
    for suf, stems in SUFFIX_RULES:
        if len(tl) > len(suf) + 2 and tl.endswith(suf):
            base = tl[: -len(suf)]
            if any(base + add in WORDS for add in stems):
                return True
    return False


# --- clean-corpus lexicon -------------------------------------------------

def corpus_signature(md_dir: Path) -> str:
    files = sorted(md_dir.glob("*.md"))
    total = sum(p.stat().st_size for p in files)
    return f"{len(files)}:{total}"


def build_lexicon(md_dir: Path) -> dict[str, int]:
    """Document frequency of each token across files with NO break signature."""
    df: Counter[str] = Counter()
    clean = broken = 0
    for p in sorted(md_dir.glob("*.md")):
        text = p.read_text(encoding="utf-8", errors="replace")
        if TELLS.search(text):
            broken += 1
            continue
        clean += 1
        df.update(set(w.lower() for w in WORD_RE.findall(text)))
    print(f"  lexicon: {clean} clean files, {broken} broken, "
          f"{len(df)} distinct tokens", file=sys.stderr)
    return dict(df)


def load_lexicon(md_dir: Path, rebuild: bool = False) -> dict[str, int]:
    sig = corpus_signature(md_dir)
    if not rebuild and CACHE.exists():
        try:
            blob = json.loads(CACHE.read_text(encoding="utf-8"))
            if blob.get("signature") == sig:
                return blob["df"]
        except (ValueError, KeyError):
            pass
    df = build_lexicon(md_dir)
    CACHE.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_text(CACHE, json.dumps({"signature": sig, "df": df}))
    return df


class WordFreqLexicon:
    """Word frequencies from the wordfreq package, for a corpus too small to
    supply its own. for_text() picks the document's language; z() is the
    highest Zipf frequency of a word in that language or English (reference
    lists mix the two)."""

    def __init__(self, langs: tuple[str, ...] = ("en",), domain: set[str] | None = None):
        from wordfreq import zipf_frequency
        self._zipf = zipf_frequency
        self.langs = langs
        self.domain = domain_vocabulary() if domain is None else domain
        self.names = domain_names() if domain is None else set()

    def for_text(self, text: str) -> "WordFreqLexicon":
        words = [w.lower() for w in WORD_RE.findall(text[:60000])][:4000]
        common = Counter(words).most_common(300)
        def score(lang: str) -> float:
            return sum(n * self._zipf(w, lang) for w, n in common)
        lang = max(DOC_LANGS, key=score) if common else "en"
        lex = WordFreqLexicon.__new__(WordFreqLexicon)
        lex._zipf, lex.domain, lex.names = self._zipf, self.domain, self.names
        lex.langs = tuple(dict.fromkeys((lang, "en")))
        return lex

    def z(self, word: str, domain: bool = True) -> float:
        """Frequency of a word in the paper's language or English; Z_DOMAIN
        for domain vocabulary when `domain`. Only candidate repairs pass
        domain=True: the data repo's descriptions carry some broken forms
        themselves ("lef", "afer", "difcult"), so the domain list may vouch
        for a repair but never protect a token as written."""
        w = word.lower()
        z = max(self._zipf(w, lang) for lang in self.langs)
        return max(z, Z_DOMAIN) if domain and w in self.domain else z

    def z_any(self, word: str) -> float:
        """Highest frequency in any DOC_LANGS language: whether a token as
        written is a real word somewhere (a German "Eifel", a French
        "Flament" in an English paper's references). No domain vocabulary."""
        w = word.lower()
        return max(self._zipf(w, lang) for lang in DOC_LANGS)


def choose_lexicon(md_dir: Path, mode: str = "auto", rebuild: bool = False):
    """The corpus lexicon (dict) or a WordFreqLexicon, per --lexicon."""
    if mode == "wordfreq":
        return WordFreqLexicon()
    df = load_lexicon(md_dir, rebuild=rebuild)
    if mode == "corpus":
        return df
    clean = clean_file_count(md_dir)
    if clean < MIN_CLEAN_FILES:
        print(f"  lexicon: only {clean} clean files (< {MIN_CLEAN_FILES}); "
              "using wordfreq", file=sys.stderr)
        return WordFreqLexicon()
    return df


def clean_file_count(md_dir: Path) -> int:
    return sum(1 for p in md_dir.glob("*.md")
               if not TELLS.search(p.read_text(encoding="utf-8", errors="replace")))


# --- repair ---------------------------------------------------------------

URLISH = re.compile(r"[/@]|\bhttps?:|\.(?:org|com|net|edu|gov|pdf|html)\b",
                    re.IGNORECASE)
INTERIOR_CAP = re.compile(r"[A-Z]")


# --- the capital "Th" ligature -------------------------------------------
# The same broken font maps also eat the capital Th ligature, decoding it to a
# bare "T": "Te Bauru Basin", "Tis assignment", "Tese elements", "Teropoda".
# It is a CAPITAL-pair glyph, so lowercase "the"/"this" come through intact —
# a broken file can have thousands of correct "the" alongside hundreds of "Te".
# Restricting to capitalised tokens is therefore not just caution, it matches
# the defect: the two lowercase candidate classes in the affected files are both
# false alarms — "tem" is Portuguese ("tem por substrato"), and "lengt" is
# "length" whose h was swallowed by a citation link ("lengt[h7](#page-7-2)"),
# where the letter is present and must not be duplicated.
#
# This family cannot ride on the corpus statistics alone. "Te" is a web2 entry
# and clean files attest it 272 times, so the loose_word guard and the 10x ratio
# both refuse it (the/te is 9.6x) — and that same 9.6 sits right next to Spanish
# "Tesis" vs "Thesis" at 9.3x, so no global threshold separates them. What
# separates them is per-document evidence: a file is admitted only if it carries
# several tells that are not words in ANY language, and then a repair fires only
# where the correct form is essentially absent from that same file (broken files
# run "Te" 49-255 against "The" 0-8).
# Same rule as TELLS: no entry may be a real word in another language. "Tese"
# is Portuguese for thesis, and admitting it as a gate signal would pull in
# all-Portuguese papers, where the repair would then rewrite sentence-initial
# "Tem" ("Tem por substrato basaltos...") into "Them".
TH_TELLS = re.compile(
    r"\b(Tis|Tose|Tey|Teir|Terefore|Teropod\w*|Tyreophor\w*|Troughout"
    r"|Tird|Tickness)\b")
TH_MIN_TELLS = 3        # files below this are not treated as Th-broken
TH_DF_MIN = 50          # the repaired form must be common across clean files
TH_DOC_FRACTION = 0.25  # ...and near-absent in THIS file relative to the break
TH_TOKEN = re.compile(r"T[a-z]{1,14}")

# "Tese"/"Tesis" are also Portuguese/Spanish for thesis and turn up in reference
# lists inside otherwise-broken English papers. The repair is worth far more
# than the collision, so instead of denying the
# words outright, skip them where the following word marks a Romance citation.
ROMANCE_NEXT = re.compile(
    r"^(de|del|da|do|la|el|en|doctoral|doutorado|mestrado|in[ée]dita?o?"
    r"|presentada|apresentada|Universidad\w*|Universidade|Facultad\w*"
    r"|Faculdade|Magister)$", re.IGNORECASE)


SENTENCE_END = re.compile(r"[.!?:;)\]]\s*$|\n")
# A separator that leaves the next word at the start of a sentence, line or
# heading (markdown emphasis and list/heading marks allowed in between).
SENTENCE_START = re.compile(r"[.!?:;\n][\s*_#>|-]*$")

# Where a broken form collides with a real word, the following word can still
# settle it. "Tree" is the case that matters: "Tree dorsal
# vertebrae" is Three, but phylogenetics writes "Tree topology", "Tree Fusing",
# "Tree Bisection-Reconnection" and "Tree Drift" (TNT search algorithms) with a
# genuine capital Tree.
_NEVER = re.compile(r"(?!)")
TH_SENSE_DENY = {
    # "Dri" and not just "Drif": marker splits the broken "Drift" around its
    # citation link, leaving "Tree Dri[f64.](...)".
    "Tree": re.compile(r"^(topolog\w*|Fusing|Bisection|Dri\w*|search\w*|Search\w*"
                       r"|space|island\w*|length\w*|file\w*|rooting|scores?)$"),
    # "Tick this box" is a reporting-summary form, not "Thick".
    "Tick": re.compile(r"^(this|the|box\w*|all|each|off)$"),
}


def repair_th_token(tok: str, df: dict[str, int], doc: Counter, doc_low: Counter,
                    prev_word: str, sep_before: str, sep_after: str, nxt: str):
    """Restore a capital Th ligature. Only called for Th-broken documents."""
    if not TH_TOKEN.fullmatch(tok):
        return None
    cand = "Th" + tok[1:]
    wf = isinstance(df, WordFreqLexicon)
    if (df.z(cand) < Z_TH) if wf else (df.get(cand.lower(), 0) < TH_DF_MIN):
        return None
    if doc.get(cand, 0) > TH_DOC_FRACTION * doc.get(tok, 0):
        return None                       # this file uses the correct form too
    if nxt and ROMANCE_NEXT.match(nxt):
        return None                       # "Tese de Doutorado", "Tesis Doctoral"

    # Mid-phrase after another capitalised word, this is a proper-noun phrase or
    # a reference-list surname, not a sentence-initial Th: "Iberian Trough",
    # "International Trust", "Chao Tan,", "Xu, X., Tan, Q.". A real break sits at
    # a sentence start, so a preceding "." (or a line break) clears the guard —
    # "...of the Bauru Basin. Te fossil content..." must still repair.
    if (prev_word[:1].isupper() and not SENTENCE_END.search(sep_before)):
        return None

    # If the broken form is itself an English word (Tree, Trough, Tin, Ten,
    # Tick, Trust, Tough), let the document's own LOWERCASE usage arbitrate.
    # Lowercase text is uncontaminated here — the ligature is a capital-pair
    # glyph — so a file that writes "tree" throughout means capital "Tree" is
    # the real word, while one that writes "thin" and never "tin" means capital
    # "Tin" is a break. This is also what protects Portuguese "Tem por
    # substrato" in a file whose lowercase vocabulary is Portuguese.
    if (df.z(tok, domain=False) >= Z_REAL) if wf else loose_word(tok):
        if doc_low.get(cand.lower(), 0) <= doc_low.get(tok.lower(), 0):
            return None
        if not sep_after[:1].isspace():
            return None               # "Ten-zawa Creek", a hyphenated name
        if nxt and TH_SENSE_DENY.get(tok, _NEVER).match(nxt):
            return None
    return cand


def repair_token(tok: str, df: dict[str, int], sentence_start: bool = False):
    """Return the repaired token, or None to leave it as-is."""
    if len(tok) < MIN_LEN or "f" not in tok.lower():
        return None
    if INTERIOR_CAP.search(tok[1:]):          # acronym / mixed caps
        return None
    if isinstance(df, WordFreqLexicon):
        return _repair_token_wordfreq(tok, df, sentence_start)
    if loose_word(tok):
        return None                           # a real English word as written
    d_orig = df.get(tok.lower(), 0)
    results = set()
    for m in re.finditer(r"[fF]", tok):
        cut = m.end()
        for tail in TAILS:
            cand = tok[:cut] + tail + tok[cut:]
            d_cand = df.get(cand.lower(), 0)
            if d_orig == 0:
                ok = strict_word(cand) or d_cand >= DF_ACCEPT
            else:
                # Clean files attest the original too, so it is a plausible
                # spelling in SOME language. Only overrule that when the corpus
                # prefers the repair by a wide margin — this is what keeps
                # "feet"/"favour"/"Fuentes"/Spanish "diferentes" intact while
                # still fixing "feld" (German "Feld") in favour of "field".
                ok = d_cand >= DF_RATIO * d_orig
            if ok:
                results.add(cand)
    if len(results) != 1:                     # 0 = no reading, >1 = ambiguous
        return None
    return results.pop()


def _repair_token_wordfreq(tok: str, lex: WordFreqLexicon, sentence_start: bool = False,
                           any_language: bool = False):
    """repair_token() with wordfreq frequencies instead of the corpus's.
    A capitalised token mid-sentence is protected by any language (names,
    places: "Eifel", "Flament"); a lowercase or sentence-initial one by the
    paper's language and English, so "ofen" is still "often" and a
    sentence-initial "Lef" (a Dutch word) still "Left". Where several readings
    pass, one 10x more frequent than the rest wins ("difficult" over
    "dificult"); otherwise the token is ambiguous and left alone."""
    if tok.lower() in lex.names:
        return None                           # a genus or clade name as written
    proper = tok[:1].isupper() and not sentence_start
    if any_language:
        # A word with non-ASCII letters inside an English paper is usually a
        # Spanish/Portuguese/French title or name: judge it in every language.
        z_orig = lex.z_any(tok)
    else:
        z_orig = lex.z_any(tok) if proper else lex.z(tok, domain=False)
    if not any_language and proper and z_orig >= Z_REAL and lex.z(tok, domain=False) < Z_ACCEPT + Z_RATIO:
        # Real only in another language. Repair to a reading that is very
        # common in this paper's own language, and to nothing rarer.
        common = {tok[:m.end()] + t + tok[m.end():] for m in re.finditer(r"[fF]", tok)
                  for t in TAILS if not (t == "i" and m.end() == len(tok))}
        common = [c for c in common if lex.z(c, domain=False) >= Z_COMMON]
        return common[0] if len(common) == 1 else None
    if z_orig >= Z_REAL:
        return None                           # common as written
    results = set()
    for m in re.finditer(r"[fF]", tok):
        cut = m.end()
        for tail in TAILS:
            if tail == "i" and cut == len(tok):
                continue    # "Markgraf" -> "markgrafi": an epithet, not a lost fi
            cand = tok[:cut] + tail + tok[cut:]
            z_cand = max(lex.z_any(cand), lex.z(cand)) if any_language else lex.z(cand)
            ok = z_cand >= Z_ACCEPT if z_orig == 0 else z_cand >= z_orig + Z_RATIO
            if ok and proper and lex.domain \
                    and cand.lower() not in lex.domain and z_cand < Z_COMMON:
                # A capitalised word mid-sentence is usually a name
                # ("Defant and Drummond", "Chafetz & Buczynski"): repair it to
                # a name the bib knows ("Bufetaut" -> "Buffetaut") or a very
                # common word, never to any English word ("Defiant").
                ok = False
            if ok:
                results.add((z_cand, cand))
    if not results:
        return None
    ranked = sorted(results, reverse=True)
    if len(ranked) > 1 and ranked[0][0] < ranked[1][0] + Z_RATIO:
        return None
    return ranked[0][1]


# Spacing and combining accents that marker leaves beside a letter ("Pa´lfy" for
# Pálfy): part of the word, though not letters, so "lfy" is a fragment.
ACCENT_MARKS = set("´`¨ˆ˜¸˚˝˘ˇ¯") | {chr(c) for c in range(0x300, 0x370)}


def _wordish(s: str) -> bool:
    return bool(s) and all(c.isalpha() or c in ACCENT_MARKS for c in s)


_WORDFREQ: WordFreqLexicon | None = None


def _shared_wordfreq() -> WordFreqLexicon:
    global _WORDFREQ
    if _WORDFREQ is None:
        _WORDFREQ = WordFreqLexicon()
    return _WORDFREQ


def _repair_fragment(toks: list[str], i: int, lex: WordFreqLexicon):
    """Repair toks[i], an ASCII fragment of a word that continues into
    neighbouring non-ASCII letters, by repairing the whole word."""
    j = i
    left = ""
    while j > 0 and _wordish(toks[j - 1]):
        j -= 1
        left = toks[j] + left
    k = i
    right = ""
    while k + 1 < len(toks) and _wordish(toks[k + 1]):
        k += 1
        right += toks[k]
    # Only the fragment's own f's are candidates: the edit must land in toks[i].
    word = left + toks[i] + right
    if INTERIOR_CAP.search(word[1:]) or len(word) < MIN_LEN:
        return None
    fixed = _repair_token_wordfreq(word, lex, any_language=True)
    if not fixed or not (fixed.startswith(left) and fixed.endswith(right)):
        return None
    inner = fixed[len(left):len(fixed) - len(right)] if right else fixed[len(left):]
    return inner if inner != toks[i] else None


def fix(text: str, df, log: dict | None = None):
    wf_mode = isinstance(df, WordFreqLexicon)
    if wf_mode:
        df = df.for_text(text)
    frag_lex = None
    toks = TOK.findall(text)
    th_on = len(TH_TELLS.findall(text)) >= TH_MIN_TELLS
    doc = Counter(t for t in toks if t[:1].isalpha()) if th_on else Counter()
    doc_low = Counter({k.lower(): 0 for k in doc})
    for k, v in doc.items():
        # wordfreq mode counts lowercase tokens only, as repair_th_token's
        # comment intends: the Th fault is capital-only, so lowercase usage is
        # the uncontaminated evidence (broken "Tis" must not outvote "this").
        # Corpus mode counts all cases.
        if not wf_mode or k[:1].islower():
            doc_low[k.lower()] += v
    n_fix = 0
    for i, t in enumerate(toks):
        if not re.fullmatch(r"[A-Za-z]+", t):
            continue
        prev = toks[i - 1] if i else ""
        nxt = toks[i + 1] if i + 1 < len(toks) else ""
        if URLISH.search(prev[-12:]) or URLISH.search(nxt[:12]):
            continue                          # inside a URL / path / filename
        if _wordish(prev[-1:]) or _wordish(nxt[:1]):
            # A fragment of a word with a non-ASCII letter ("geográ|fca",
            # "Lef|èvre"): judge the whole word, so "geográfca" becomes
            # "geográfica" and "Lefèvre" never "Leftèvre". The corpus lexicon
            # holds only ASCII fragments and cannot judge a whole word, so both
            # modes use wordfreq here.
            if frag_lex is None:
                frag_lex = df if wf_mode else _shared_wordfreq().for_text(text)
            rep = _repair_fragment(toks, i, frag_lex)
            if rep and rep != t:
                if log is not None:
                    log[(t, rep)] = log.get((t, rep), 0) + 1
                toks[i] = rep
                n_fix += 1
            continue
        # After an initial ("E. Markgraf", "J. Eifel") the next word is a
        # surname in a reference list, not a sentence start.
        start = (not i or bool(SENTENCE_START.search(prev))) and not (
            i >= 2 and re.fullmatch(r"[A-Z]", toks[i - 2]))
        rep = repair_token(t, df, sentence_start=start)
        if rep is None and th_on:
            next_word = toks[i + 2] if i + 2 < len(toks) else ""
            prev_word = toks[i - 2] if i >= 2 else ""
            rep = repair_th_token(t, df, doc, doc_low, prev_word, prev, nxt,
                                  next_word)
        if rep and rep != t:
            if log is not None:
                log[(t, rep)] = log.get((t, rep), 0) + 1
            toks[i] = rep
            n_fix += 1
    return "".join(toks), n_fix


# --- CLI ------------------------------------------------------------------

def iter_targets(md_dir: Path):
    """Corpus markdown, minus the *.original.md translation backups — those are
    the faithful record of what the source said and are not indexed."""
    return [p for p in sorted(md_dir.glob("*.md"))
            if not p.name.endswith(".original.md")]


def apply_all(md_dir: Path, df: dict[str, int], write: bool) -> int:
    log: dict = {}
    changed = total = 0
    for p in iter_targets(md_dir):
        src = p.read_text(encoding="utf-8")
        out, n = fix(src, df, log)
        if not n:
            continue
        changed += 1
        total += n
        if write:
            write_markdown(p, out)
    verb = "rewrote" if write else "would rewrite"
    print(f"{verb} {changed} files, {total} repairs, {len(log)} distinct forms")
    if not write:
        print("  (dry run — add --apply to write)")
    return 0


def scan(md_dir: Path, threshold: int) -> int:
    rows = []
    for p in iter_targets(md_dir):
        hits = len(TELLS.findall(p.read_text(encoding="utf-8", errors="replace")))
        if hits >= threshold:
            rows.append((hits, p.stem))
    rows.sort(reverse=True)
    print(f"files with >={threshold} ligature tells: {len(rows)}")
    for hits, key in rows:
        print(f"  {hits:5d}  {key}")
    return 0


def audit(md_dir: Path, df: dict[str, int], limit: int) -> int:
    log: dict = {}
    files = total = 0
    for p in iter_targets(md_dir):
        _, n = fix(p.read_text(encoding="utf-8", errors="replace"), df, log)
        if n:
            files += 1
            total += n
    print(f"corpus dry-run: {files} files, {total} repairs, {len(log)} distinct")
    items = sorted(log.items(), key=lambda x: (-x[1], x[0]))
    print(f"\n=== most frequent {limit} ===")
    for (a, b), c in items[:limit]:
        print(f"{c:6d}  {a!r} -> {b!r}")
    print(f"\n=== rarest {limit} (false positives live here) ===")
    for (a, b), c in items[-limit:]:
        print(f"{c:6d}  {a!r} -> {b!r}")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--key", type=key_arg, help="citation key -> markdown/{key}.md")
    g.add_argument("--path", help="explicit markdown path")
    g.add_argument("--scan", action="store_true", help="census files with the defect")
    g.add_argument("--audit", action="store_true", help="corpus-wide dry run")
    g.add_argument("--all", action="store_true",
                   help="repair every affected file (needs --apply to write)")
    ap.add_argument("--apply", action="store_true", help="write changes (.bak saved)")
    ap.add_argument("--top", action="store_true", help="print the repairs made")
    ap.add_argument("--threshold", type=int, default=5, help="--scan hit floor")
    ap.add_argument("--limit", type=int, default=40, help="--audit sample size")
    ap.add_argument("--rebuild-lexicon", action="store_true",
                    help="force a rebuild of the clean-corpus lexicon")
    ap.add_argument("--lexicon", choices=["auto", "corpus", "wordfreq"], default="auto",
                    help=f"word frequencies from this corpus or from wordfreq (auto: the "
                         f"corpus when it has {MIN_CLEAN_FILES}+ clean files)")
    args = ap.parse_args()

    if args.scan:
        return scan(MD_DIR, args.threshold)

    df = choose_lexicon(MD_DIR, args.lexicon, rebuild=args.rebuild_lexicon)

    if args.audit:
        return audit(MD_DIR, df, args.limit)

    if args.all:
        return apply_all(MD_DIR, df, write=args.apply)

    path = Path(args.path) if args.path else MD_DIR / f"{args.key}.md"
    if not path.exists():
        sys.exit(f"not found: {path}")
    src = path.read_text(encoding="utf-8")
    log: dict = {}
    out, total = fix(src, df, log)
    print(f"{path.name}: repairs={total}  distinct={len(log)}  "
          f"chars {len(src)}->{len(out)}")
    if args.top or not args.apply:
        for (a, b), c in sorted(log.items(), key=lambda x: (-x[1], x[0]))[:80]:
            print(f"  {c:4d}  {a!r} -> {b!r}")
    if args.apply:
        bak = path.with_suffix(path.suffix + ".ligature.bak")
        bak.write_text(src, encoding="utf-8")
        write_markdown(path, out)
        print(f"  APPLIED (backup: {bak.name})")
    else:
        print("  (dry run — re-run with --apply to write)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
