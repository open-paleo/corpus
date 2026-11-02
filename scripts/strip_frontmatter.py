#!/usr/bin/env python3
# corpus-command: clean frontmatter | publisher cover boilerplate
"""Strip journal/aggregator front-matter boilerplate from markdown/*.md.

The target is the "relatively useless" cover/terms text that PDF exporters
prepend: Taylor & Francis "This article was downloaded by…" (old download-
stamp) and the newer T&F "tandfonline masthead" (journal-name heading +
subtitle + "ISSN … Journal homepage: tandfonline.com/loi" line), BioOne
"…is a nonprofit…", JSTOR "Your use of the JSTOR archive…", plus the
associated registration/terms paragraphs. It also handles Elsevier
accepted-manuscript exports: the cover block ("# Accepted Manuscript",
PII, "To appear in", Received/Revised/Accepted Date) and the recurring
"ACCEPTED MANUSCRIPT" diagonal page watermark that marker glues to the
start of every page's first body line (this one is stripped globally, not
just in the head, since it recurs throughout the document). It also
removes the BHL (Biodiversity Heritage Library) attribution page —
"# Biodiversity Heritage Library", "View This Item Online", "Holding
Institution", "Copyright & Reuse", the closing BHL blurb — which BHL
partpdf/item exports prepend or append. That block is removed as a head-
or tail-anchored contiguous run (gap-tolerant over interspersed
institution/citation lines), gated on a strong attribution marker so a
reference that merely cites a biodiversitylibrary.org URL is left alone.
Finally it removes the EBSCOhost delivery notice ("Copyright of <journal>
is the property of <publisher> and its content may not be copied or
emailed to multiple sites…"), which EBSCO appends as a single trailing
block after the reference list.

## Why paragraph-level, not range-delete

In T&F exports the *useful* lines (real title, authors, "To cite this
article") sit BETWEEN two junk zones — a header block (download stamp +
Informa registration + journal-name heading) and a trailer block (PLEASE
SCROLL DOWN + terms). A naive "delete from START to END" would eat the
title. So instead we split the file into blank-line-separated blocks and,
**only within the front-matter zone** (first HEAD_LIMIT chars), drop any
block whose text matches a specific boilerplate signature. Everything
else — including the title, authors, citation lines, and the entire
article body — is preserved verbatim.

## Safety

- Removals are bounded to the first HEAD_LIMIT chars (never touches body).
- Signatures are specific boilerplate phrasing; real prose won't match.
- Two *structural* patterns (the "# **Journal of …**" heading and a bare
  "## REFERENCES" heading) are gated on a publisher hallmark being present
  and, for REFERENCES, on sitting in the first REF_HEADING_LIMIT chars —
  so a genuine References section deeper in the paper is never touched.
- A file is skipped (with a warning) if stripping would leave < MIN_KEEP
  chars or < MIN_KEEP_FRAC of the original — guards against nuking a
  cover-page-only stub.
- Atomic writes. `.original.md` files are never scanned.

## Usage

  # dry run (default): report what WOULD be removed, write nothing
  ./.runtime/venv/bin/python scripts/strip_frontmatter.py
  # apply
  ./.runtime/venv/bin/python scripts/strip_frontmatter.py --apply
  # limit / target specific keys
  ./.runtime/venv/bin/python scripts/strip_frontmatter.py --key allain2000a --key bonaparte1976a
"""

from __future__ import annotations

import argparse
import re
from corpus.config import MD_DIR
from corpus.keys import key_arg
from corpus.io import atomic_write_text

HEAD_LIMIT = 4000        # only blocks starting within this many chars are eligible
REF_HEADING_LIMIT = 1500  # a bare "REFERENCES" heading is junk only this early
MIN_KEEP = 200           # refuse to strip a file down below this many chars
MIN_KEEP_FRAC = 0.30     # …or below this fraction of its original length

# Phrase signatures — a head-zone block matching any of these is dropped.
# Case-insensitive. Kept deliberately specific to boilerplate wording.
PHRASE_JUNK = [
    # --- Taylor & Francis / Informa cover + terms ---
    r"this article was downloaded by",
    r"^on:\s.+\bat:\s*\d",                    # "On: 16 April 2013, At: 13:35 …"
    r"publisher:\s*taylor\s*&?\s*francis",
    r"informa ltd registered",
    r"registered office",
    r"mortimer (house|street)",
    r"publication details,?\s*including instructions",
    r"please scroll down for article",
    r"full terms and conditions of use",
    r"this article may be used for research, teaching",
    r"the publisher (does not give|shall not be liable|makes no representation)",
    r"terms\s*&?\s*conditions of access and use can be found",
    r"version of record first published",
    # --- T&F cover, vision-OCR'd variant: the download-stats / nav strip.
    # marker reads this page off the text layer and yields the "masthead"
    # form (handled by strip_tf_masthead); vision instead transcribes the
    # rendered nav widgets as body paragraphs, so they need their own
    # patterns. Anchored + head-bounded (HEAD_LIMIT), so a reference-list
    # "Published online" late in the file is untouched. The "Published online:"
    # line itself is NOT here: Nature and others print a genuine
    # "Published online: 12 February 2025" under Received/Accepted. It is
    # dropped only when the head also carries another T&F nav widget
    # (TF_NAV_HEAD_RE, checked in block_is_junk).
    r"^submit your article to this journal",
    r"^article views:\s*\d",
    r"^view related articles",
    r"^citing articles:\s*\d",
    r"^view crossmark data",
    # --- BioOne (both the old cover and the newer "BioOne Digital Library") ---
    r"bioone.{0,60}is an? (innovative )?nonprofit",
    r"bioone.{0,40}sees sustainable scholarly publishing",
    r"the bioone digital library.{0,80}provides worldwide",
    r"your use of this pdf, the bioone",
    r"usage of bioone( digital library)? content is strictly limited",
    r"commercial inquiries or rights and permissions",
    # --- JSTOR ---
    r"jstor is a not-for-profit",
    r"your use of the jstor archive",
    r"linked references are available on jstor",
    r"collaborating with jstor to digitize",
    r"this content downloaded from",
    r"all use subject to https://about\.jstor\.org",
    # --- accepted-manuscript disclaimer prose. Elsevier's standard wording,
    # also reused verbatim by other publishers (e.g. Revista de la Asociación
    # Geológica Argentina, in Spanish). The ELSEVIER_JUNK_RE block below cuts
    # the PII/Reference/dates lines but never this paragraph, so it needs its
    # own entry — ungated, because non-Elsevier journals carry it without the
    # Elsevier hallmark. Real front matter above it (journal name, title,
    # authors, "Please cite this article as:" / "Citar este artículo como:")
    # is preserved as usual. ---
    r"este es un archivo pdf de un manuscrito sin editar",
    r"this is a pdf file of (?:an? |the )?(?:unedited )?manuscript",
    # --- broken URL-continuation fragments left by the exporter, e.g.
    #     "[%3B2](http://www.bioone.org/…)" on its own line ---
    r"^\[%[0-9a-f]{2}[^\]]*\]\(https?://",
    # --- Palaeontologia Electronica copyright/licence block. PE puts this
    # AFTER the abstract, affiliations and keywords rather than as a head
    # prefix, so it is a paragraph denylist job, not a prefix cut. Each
    # pattern is anchored at a line start so the real "Final citation:" /
    # bare-citation paragraph survives — on some papers that citation line
    # ENDS with the palaeo-electronica.org URL, which is exactly why the URL
    # pattern below must match only a paragraph that STARTS with it (i.e. the
    # standalone continuation line), never one that merely contains it. The
    # ZooBank LSID heading is deliberately kept: on a new-taxon paper it is
    # nomenclatural data, not boilerplate. ---
    r"^copyright:\s*(?:©\s*)?[a-z]+\s+\d{4}\s+society of vertebrate paleontology",
    r"^this is an open access article distributed under the terms of the creative commons",
    r"^palaeo-electronica\.org/content/\S*\s*$",
    # --- PeerJ page-1 sidebar. Like the PE block above this is NOT a head
    # prefix: PeerJ runs it down the left margin of page 1, so marker emits it
    # as a run of short paragraphs sitting AFTER the title, authors and
    # abstract. Paragraph denylist, each anchored at a line start and
    # head-bounded by the caller, so the same vocabulary inside a reference
    # ("… PeerJ 3:e1691 https://doi.org/10.7717/peerj.1691") is untouched —
    # 301 files cite a peerj DOI, only 58 carry this sidebar.
    #
    # The DOI line is pinned to PeerJ's 10.7717 prefix rather than a generic
    # DOI, and the submitted/accepted/published line requires all three in
    # order. Deliberately NOT included: the "Corresponding author <name>
    # <mailto:…>" line. It matches 63 paragraphs but some belong to non-PeerJ
    # papers where it is the article's own author metadata (alcober2010a),
    # and dropping a corresponding author buys nothing.
    #
    # Science Advances reuses the submitted/accepted/published wording but
    # places it near the END of the paper, past HEAD_LIMIT, so it is left
    # alone — as is one PeerJ file (cincotta2019a) whose long abstract pushes
    # the sidebar 11 chars past the limit. Raising HEAD_LIMIT globally to
    # catch it would loosen every other handler. ---
    r"^submitted \d+ \w+ \d{4} +accepted .+ +published \d",
    r"^\[academic editor\]\(https?://peerj\.com",
    r"^\[?additional information and\]?.{0,60}declarations can be found on",
    r"^doi \*{0,2}\[10\.7717/",
    r"^copyright\s+(?:©\s*)?(?:19|20)\d\d\s+\S[^\n]{0,60}$",
    r"^\[distributed under\]\(https?://creativecommons",
    r"^\*\*open access\*\*$",
    # --- Elsevier AUTHOR-COPY cover ("Author's personal copy"): a page 1 of
    # sharing-policy prose that Elsevier prepends to the copy furnished to the
    # author, plus a heading repeating on the article's first page. Anchored
    # to the cover's own sentences, head-bounded by the caller.
    # (sweetman2010z; the only instance in the corpus when added.) ---
    r"^\*{0,2}this article appeared in a journal published by elsevier",
    r"^\*{0,2}other uses, including reproduction and distribution, or selling or licensing",
    r"^\*{0,2}in most cases authors are permitted to post their version of the article",
    r"^\*{0,2}<?https?://www\.elsevier\.com/copyright>?\*{0,2}\s*$",
    r"^#*\s*\*{0,2}author.s personal copy\*{0,2}\s*$",
]
PHRASE_RE = [re.compile(p, re.IGNORECASE | re.MULTILINE) for p in PHRASE_JUNK]
PUBLISHED_ONLINE_RE = re.compile(r"^published online:\s*\d", re.IGNORECASE | re.MULTILINE)
TF_NAV_HEAD_RE = re.compile(
    r"^(submit your article to this journal|article views:\s*\d|view related articles|citing articles:\s*\d|view crossmark data)",
    re.IGNORECASE | re.MULTILINE)

JOURNAL_HEADING_RE = re.compile(r"^#+\s*\*\*Journal of [^\n*]+\*\*\s*$",
                                re.IGNORECASE)
REF_HEADING_RE = re.compile(r"^#+\s*REFERENCES\s*$", re.IGNORECASE)

# Elsevier accepted-manuscript cover lines (gated on ELSEVIER_HALLMARK). The
# real title, authors, DOI, and "Please cite this article as" citation are
# NOT listed here — they're kept.
ACCEPTED_MS_HEADING_RE = re.compile(
    r"^#+\s*(?:Accepted Manuscript|Journal Pre[- ]?proofs?)\s*$", re.IGNORECASE)
ELSEVIER_JUNK_RE = [re.compile(p, re.IGNORECASE) for p in [
    r"^PII:\s*\S",
    r"^Reference:\s+\S",
    r"^To appear in:\s",
    r"^(received|revised|accepted) date:\s",
]]

# The Elsevier "Journal Pre-proof" page footer. Same role as the ACCEPTED
# MANUSCRIPT watermark on the pre-proof track, but marker reads it as a
# *suffix*: it lands at the end of the last paragraph of every page, glued to
# real prose ("...across different regions. Journal Pre-proof"). Also strip it
# when it stands alone on a line. Gated on ELSEVIER_HALLMARK; the phrase never
# occurs in real prose, so no title-case exception is needed the way the
# all-caps watermark needs one. The lookbehind excludes '#' so the cover's own
# "# Journal Pre-proof" H1 is left for ACCEPTED_MS_HEADING_RE to remove whole —
# without it this rule would eat the text and strand a bare '#'.
PREPROOF_FOOTER_RE = re.compile(
    r"(?:(?<=[^\s#])[ \t]+|^[ \t]*)Journal Pre[- ]?proofs?[ \t]*$",
    re.IGNORECASE | re.MULTILINE)

# --- Elsevier "Journal Pre-proof" ROTATED page watermark, prose form ---
# The same watermark also runs vertically up the page edge, and on a page with
# no table marker shreds it into one letter per PROSE line, glued to the front
# of the real text:
#     f o lambeosaurines and its postulated close relationship with …
#     o becomes restricted to the upper Maastrichtian Talarn Formation.
#     r p Our osteohistological analysis indicates that …
# Read downward the leading letters spell "…f o o r p l a r u o J" — "Journal
# Pre-proof" upside down, with letters marker dropped along the way.
#
# Shape alone CANNOT gate this. A leading single letter is also how an
# affiliation superscript comes through ("a Institut Català de Paleontologia")
# and how an ordinary sentence beginning with the article "A" comes through
# ("A relatively high-resolution time framework…"), and both appear in
# fondevilla2019z. Stripping on shape would delete real text.
#
# The discriminator is the same one the table rules use: the VERTICAL SEQUENCE.
# Watermark letters chain down consecutive lines and spell the phrase; a real
# leading letter is isolated. So candidates are grouped into chains (gap of at
# most MAX_GAP lines), and a chain is cut only when it is at least MIN_CHAIN
# lines long AND its letters are a subsequence of the phrase — subsequence, not
# substring, because marker drops letters mid-run ("FOORPLARUOJ" is
# "FOORPERPLANRUOJ" with the E, P and N missing). The length gate is what
# spares the three-line affiliation block, whose letters would pass the
# subsequence test on their own.
#
# NOT handled: the same watermark landing at the END of a line. Only 7 of them
# occur in the worked example against 52 leading ones, and a trailing "a" or
# "n" is indistinguishable from prose ("materials reveal a") — the yield does
# not justify the risk, so they are left in place.
PREPROOF_PHRASE = "JOURNALPREPROOF"
PREPROOF_LEAD_RE = re.compile(r"^((?:[A-Za-z][ \t])+)(?=[A-Za-z])")
PREPROOF_MIN_CHAIN = 5
PREPROOF_MAX_GAP = 4


def _is_preproof_run(letters: str) -> bool:
    """True if `letters` is a subsequence of the phrase, in either direction.

    The rotation reverses reading order, so both directions are tried. Two
    cycles of the phrase are allowed so a chain that wraps past one page's
    watermark into the next still matches.
    """
    if len(letters) < PREPROOF_MIN_CHAIN:
        return False
    upper = letters.upper()
    for phrase in (PREPROOF_PHRASE, PREPROOF_PHRASE[::-1]):
        haystack = iter(phrase * 2)
        if all(ch in haystack for ch in upper):
            return True
    return False


def strip_preproof_rotated(text: str) -> tuple[str, int]:
    """Cut shredded rotated "Journal Pre-proof" letters off prose line starts.

    Returns (new_text, n_lines_cut). Gated as described above; a file with no
    qualifying chain comes back untouched.
    """
    lines = text.split("\n")
    candidates = []
    for index, line in enumerate(lines):
        match = PREPROOF_LEAD_RE.match(line)
        if match:
            candidates.append((index, match))

    cut: set[int] = set()
    chain: list[tuple[int, re.Match]] = []

    def flush(current: list[tuple[int, re.Match]]) -> None:
        if len(current) < PREPROOF_MIN_CHAIN:
            return
        letters = "".join(m.group(1).replace(" ", "").replace("\t", "")
                          for _, m in current)
        if _is_preproof_run(letters):
            cut.update(index for index, _ in current)

    for entry in candidates:
        if chain and entry[0] - chain[-1][0] <= PREPROOF_MAX_GAP:
            chain.append(entry)
        else:
            flush(chain)
            chain = [entry]
    flush(chain)

    for index, match in candidates:
        if index in cut:
            lines[index] = lines[index][match.end(1):]
    return "\n".join(lines), len(cut)


# The Elsevier "ACCEPTED MANUSCRIPT" diagonal page watermark: marker OCRs it
# and glues it to the first text line of *every* page, so it recurs deep in
# the body — handled globally, not head-bounded. Case-SENSITIVE (all-caps) so
# it never touches the title-case "Accepted Manuscript" heading or prose.
WATERMARK_LINE_RE = re.compile(
    r"^[ \t]*(?:ACCEPTED MANUSCRIPT|MANUSCRIPT ACCEPTED)[ \t]*$\n?", re.MULTILINE)
# The watermark can also land right after a markdown list marker ("- ", "* ",
# "1. ") or blockquote in the References / figure-caption lists — capture and
# preserve that marker, drop only the watermark.
WATERMARK_PREFIX_RE = re.compile(
    r"^([ \t]*(?:[-*>][ \t]+|\d+\.[ \t]+)?)"
    r"(?:ACCEPTED MANUSCRIPT|MANUSCRIPT ACCEPTED)[ \t]+", re.MULTILINE)
# When the page the watermark lands on starts with a table, marker puts it
# inside a *cell* instead of on its own line — either alone
# ("| ACCEPTED MANUSCRIPT |") or glued to the cell's real value by a <br>
# ("| ACCEPTED MANUSCRIPT<br>Rachis |"). Anchored on the cell's opening pipe so
# only the watermark goes and the real value stays. Self-gating (applied even
# when the Elsevier head hallmark is absent): an all-caps watermark sitting in
# a table cell is its own evidence, and a file whose cover was stripped by an
# earlier pass no longer trips the head gate.
WATERMARK_CELL_RE = re.compile(
    r"(\|[ \t]*)(?:ACCEPTED MANUSCRIPT|MANUSCRIPT ACCEPTED)(?:<br>)?[ \t]*")
# Sometimes marker shreds the *rotated* watermark instead of reading it as a
# phrase: the letters land one per table row, so a data table comes back as
#   | LL | 125.7 ± 2.6 Ma | 40Ar/39Ar, whole rock | T<br>Zhu et al., 2007 |
#   | LL | 126.4 ± 0.5 Ma | RI<br>40Ar/39Ar, groundmass | Chang et al., 2009 |
#   | A  | E<br>T<br>P<br>E<br>C<br>C | U<br>N<br>A<br>M<br>D |            |
# i.e. stray capitals glued onto real cell values by a <br>, plus a trailing
# row that is nothing but shredded letters. WATERMARK_CELL_RE can't see these
# — there is no readable "ACCEPTED MANUSCRIPT" left to anchor on. Both patterns
# are gated on the file-level Elsevier hallmark (unlike WATERMARK_CELL_RE,
# which is self-gating) and the letters are restricted to those occurring in
# ACCEPTED / MANUSCRIPT, so a real one-letter column value survives.
WATERMARK_LETTERS = "ACDEIMNPRSTU"
# Letter membership alone is not enough to tell a shredded watermark from a
# shredded *rotated header*: "| R<br>A<br>D<br>I<br>U<br>S |" is the word RADIUS
# in a bone-measurement table and every one of its letters is in the set above.
# The real test is whether the run spells a contiguous piece of the watermark
# phrase — in either direction, since the rotation reverses the reading order
# ("NAMDET" is "TEDMAN" backwards). RADIUS fails both ways; ETPECC does not.
WATERMARK_PHRASE = "ACCEPTEDMANUSCRIPT"


def _is_watermark_run(letters: str) -> bool:
    """True if `letters` is a contiguous slice of ACCEPTEDMANUSCRIPT."""
    if not letters:
        return False
    return letters in WATERMARK_PHRASE or letters[::-1] in WATERMARK_PHRASE
# The trailing `(?![^|\n]*<br>)` is what keeps this off legitimate content.
# Marker also shreds *rotated column headers* into <br>-joined pieces, and they
# look identical at the front: "| C<br>ha<br>in<br>Xu<br>..." is "Characters in
# Xunmenglong", "| T<br>i<br>b<br>ia<br>..." is "Tibia". Stripping their first
# letter silently corrupts real text. The discriminator is what FOLLOWS: a
# watermark letter is glued onto one ordinary value ("T<br>Zhu et al., 2007"),
# so the rest of the cell has no further <br>, while a shredded header is <br>s
# all the way to the closing pipe. The `+` still absorbs stacked watermark
# letters ("S<br>U<br>-") because each one is a single capital from the set.
WATERMARK_FRAGMENT_CELL_RE = re.compile(
    rf"(\|[ \t]*)((?:[{WATERMARK_LETTERS}]{{1,2}}<br>)+)(?=[^|\n])(?![^|\n]*<br>)")
# A cell that is *nothing but* stacked letters ("| N<br>A<br>M<br>D<br>E<br>T |")
# — the watermark ran down a column that has no value on those rows. Emptied
# rather than left as a stray trailing capital. Two letters minimum, so a real
# one-letter cell (the "X" / "A" presence markers these character tables use)
# is never touched.
WATERMARK_ONLY_CELL_RE = re.compile(
    rf"(\|[ \t]*)([{WATERMARK_LETTERS}](?:<br>[{WATERMARK_LETTERS}])+)[ \t]*(?=\|)")
# A row whose cells are all either empty or runs of single capitals joined by
# <br>. The extra "must contain a <br>" check lives in strip_file: a row of bare
# one-letter cells could be real data, but a <br>-joined letter run inside a
# cell is only ever a shredded vertical watermark.
# The per-cell group is ATOMIC. Without (?>...) the two [ \t]* around the
# optional letter-run can split a space run between them in exponentially many
# ways, so a row that starts with "|", contains a long run of spaces and never
# reaches a closing "|" backtracks forever. Real case: dias2020a (an Elsevier
# Journal Pre-proof) has table rows with 48-108 consecutive spaces and hung the
# whole corpus sweep at 100% CPU with no output.
WATERMARK_FRAGMENT_ROW_RE = re.compile(
    rf"^\|(?>(?:[ \t]*(?:[{WATERMARK_LETTERS}](?:<br>[{WATERMARK_LETTERS}])*)?"
    rf"[ \t]*\|))+[ \t]*$\n?", re.MULTILINE)

# The Royal Society per-page download stamp, which marker glues onto the end
# of a page's last body line, often mid-sentence:
#   "...projects Downloaded from royalsocietypublishing.org/rsos/article-pdf/
#    doi/10.1098/rsos.201676/994268/rsos.201676.pdf"
# optionally followed by "by guest on 26 September 2026". Removed globally;
# the host plus the article-pdf/doi/....pdf path is its own gate, so a
# royalsocietypublishing.org URL cited in prose is untouched.
ROYALSOC_STAMP_RE = re.compile(
    r"[ \t]*Downloaded from royalsocietypublishing\.org/\S*?article-pdf/doi/\S+?\.pdf"
    r"(?:[ \t]+by guest on \d{1,2} \w+ \d{4})?", re.IGNORECASE)

# The Cambridge Journals per-page download stamp:
#   <http://journals.cambridge.org> Downloaded: 11 Mar 2015 IP address: 1.2.3.4
# Like the Elsevier watermark this is a *page* artifact, so it lands mid-body
# and is removed globally rather than head-bounded. The pattern is its own gate
# -- it requires the host, "Downloaded:" and "IP address:" all on one line, so
# the Cambridge masthead ("http://journals.cambridge.org/SYP"), the "Link to
# this article:" line and in-prose supplementary URLs are all left alone.
CAMBRIDGE_STAMP_RE = re.compile(
    r"^[ \t]*<?https?://journals\.cambridge\.org>?[ \t]*"
    r"Downloaded:.*?IP address:[ \t]*[\d.]+[ \t]*$\n?",
    re.IGNORECASE | re.MULTILINE)

# File-level hallmarks that gate the structural / publisher-specific patterns.
TF_HALLMARK = re.compile(r"this article was downloaded by|informa ltd registered",
                         re.IGNORECASE)
JSTOR_HALLMARK = re.compile(r"jstor", re.IGNORECASE)
ELSEVIER_HALLMARK = re.compile(
    r"^#+\s*Accepted Manuscript\s*$|^PII:\s*S|please cite this article as:",
    re.IGNORECASE | re.MULTILINE)

# --- BHL (Biodiversity Heritage Library) attribution page ---
# BHL partpdf/item exports prepend or append a full attribution page. It sits
# at the head OR tail (never mid-article), so it's removed as a head-/tail-
# anchored contiguous run of BHL blocks — not head-bounded like the covers.
# GATE on a *strong* marker so a reference that merely cites a
# biodiversitylibrary.org/page URL (with none of these markers) is left alone.
BHL_STRONG_HALLMARK = re.compile(
    r"^#+\s*Biodiversity Heritage Library\s*$"
    r"|view this item online"
    r"|holding institution"
    r"|this document was created from content at",
    re.IGNORECASE | re.MULTILINE)


_BHL_CITATION_RE = re.compile(r"^[A-Z][\w.\-']+,?\s+[A-Z].{0,140}?\b(?:1[89]\d\d|20\d\d)\b")


def _is_bhl_block(b: str) -> bool:
    """True if a block is a BHL attribution line (or a separator/blank-page
    block that glues a run together). Tolerates `#`/`*` heading prefixes."""
    bl = b.strip().lower()
    if bl in ("", "---"):
        return True
    if bl == "biodiversity heritage library":          # sponsor value
        return True
    if re.match(r"[#*\s]*biodiversity heritage library\s*$", bl):
        return True
    # vision OCR renders the BHL logo/wordmark as a figure placeholder
    if re.match(r"\[figure:[^\]]*biodiversity heritage library[^\]]*\]$", bl):
        return True
    if "view this item online" in bl:
        return True
    if re.match(r"[#*\s]*(holding institution|sponsored by|copyright & reuse)", bl):
        return True
    if re.match(r"[#*\s]*(copyright status|rights holder|page\(s\)|permalink|doi)\s*[:*]", bl):
        return True
    if re.match(r"[#*\s]*(license|rights)\s*[:*]*\s*<?https?://", bl):
        return True
    if "this document was created from content at" in bl:
        return True
    if re.match(r"this (file|document) was generated", bl):
        return True
    if re.match(r"generated\s+\d", bl):
        return True
    if "this page intentionally left blank" in bl:
        return True
    if "biodiversitylibrary.org" in bl and len(bl) < 220:  # short (labelled) URL
        return True
    return False


# Newer Taylor & Francis "tandfonline masthead" cover (distinct from the old
# "This article was downloaded by" download-stamp): a journal-name H1 + a
# subtitle + an "ISSN … Journal homepage: <…tandfonline.com/loi/…>" line, all
# sitting as a contiguous prefix BEFORE the real article title. The homepage
# line is the terminal, unambiguous masthead element — remove blocks[0..it].
# Matches both the older "…/loi/<code>" and newer "…/journals/<code>" homepage
# URLs, and tolerates the URL being wrapped in a markdown link or <…>.
TF_MASTHEAD_RE = re.compile(
    r"journal homepage:.*tandfonline\.com/(?:loi|journals)", re.IGNORECASE)


def strip_tf_masthead(text: str) -> tuple[str, list[str]]:
    """Remove the T&F tandfonline masthead prefix (journal name + subtitle +
    ISSN/homepage line) through the homepage line. Returns (new_text, removed)."""
    if not TF_MASTHEAD_RE.search(text[:HEAD_LIMIT]):
        return text, []
    blocks = text.split("\n\n")
    end, off = -1, 0
    for i, b in enumerate(blocks):
        if off > HEAD_LIMIT:
            break
        if TF_MASTHEAD_RE.search(b):
            end = i
            break
        off += len(b) + 2
    if end < 0:
        return text, []
    removed = [" ".join(b.split())[:90] for b in blocks[:end + 1] if b.strip()]
    new_text = "\n\n".join(blocks[end + 1:]).lstrip("\n")
    if new_text and not new_text.endswith("\n"):
        new_text += "\n"
    return new_text, removed


# Royal Society (Proc. R. Soc. B / Phil. Trans.) cover sheet: a full-page
# template prepended by royalsocietypublishing.org PDF exports — a (scrambled)
# title, authors, "Proc. R. Soc. B YYYY VOL … first published online", then
# Supplementary data / References / Article cited in / Subject collections /
# Email alerting service nav, all with royalsocietypublishing.org links. The
# real article follows. Removed as a prefix: cut through the "sign up in the
# box" (Email alerting service) block, then drop residual leading nav blocks.
RS_GATE = re.compile(r"royalsocietypublishing\.org", re.IGNORECASE)
RS_COVMARK = re.compile(r"sign up in the box|email alerting service", re.IGNORECASE)
RS_SIGNUP = re.compile(r"sign up in the box", re.IGNORECASE)
RS_RESIDUAL = re.compile(
    r"royalsocietypublishing|journals\.royalsoc|to subscribe to|citations to advance"
    r"|advance online|cite this article|#\s*research|^doi:|^published online"
    r"|^\**\s*proc\.\s*r\.\s*soc|^\**\s*phil\.\s*trans|this journal is"
    r"|©\s*\d{4}\s*the royal society|^-+$|^[a-z]{1,4}$", re.IGNORECASE)


# GSA (gsapubs.org, HighWire) cover sheet: "Email alerting services / Subscribe
# / Permission request" links, the scrambled "Opinions presented in this
# publication..." notice, "Notes" and a copyright line. It sits at the HEAD of
# GSA Bulletin downloads but at the TAIL of some Geology/Memoirs ones, so it is
# located by its gate block rather than by position. The title / authors /
# "Journal YEAR;VOL;PAGES" / doi lines just before the gate are the article's
# citation and are kept. The run is extended only over blocks matching
# GSA_COVER_RE (including the short wrap fragments marker splits off).
GSA_GATE = re.compile(r"^\*\*E-?mail alerting services\*\*.*gsapubs\.org", re.IGNORECASE)
GSA_COVER_RE = [re.compile(p, re.IGNORECASE) for p in [
    r"^\*\*(E-?mail alerting services|Subscribe|Permission request|Notes)\*\*",
    r"to contact GSA\.?$",
    r"^\[?(https?://)?www\.geosociety\.org/pubs/copyrt",
    r"^\[?htm#gsa\]",
    r"Opinions presented in this publication",
    r"forums for the presentation of diverse opinions",
    r"regardless of their race, citizenship",
    r"^Copyright ©.{0,20}Geological Society of America",
    r"^America © \d{4} Geological Society of$",
    r"^receive free e-mail alerts when new( articles)?$",
    r"^(articles )?cite this article$",
    r"^to subscribe to .{0,60}$",
    r"^America (Bulletin|Memoirs|Special Papers)$",
    r"^Notes$",
]]
# AAPG Datapages per-page stamp, personalised with the downloading account's
# name. Removed globally; the full two-sentence form is its own gate.
AAPG_STAMP_RE = re.compile(
    r"[ \t]*This document was prepared for the exclusive use of [^.\n]{1,80}\.\s*"
    r"Unauthorized distribution is strictly prohibited\.[ \t]*")
# Wiley Online Library per-page download footer, personalised with the
# downloading account's name or institution (sometimes neither):
#   20562802, 2021, 1, Downloaded from https://onlinelibrary.wiley.com/doi/10...
#   by <reader>, Wiley Online Library on [09/10/2026]. See the Terms and
#   Conditions (...) on Wiley Online Library for rules of use; OA articles are
#   governed by the applicable Creative Commons License
# marker keeps it as one line of its own. Removed globally; the whole line,
# from the optional ISSN/year/issue prefix to "Creative Commons License", is
# its own gate, so prose quoting a Wiley URL is left alone.
WILEY_STAMP_RE = re.compile(
    r"^[ \t]*(?:[\dX]{8}, \d{4}, [^,\n]{1,10}, )?"
    r"Downloaded from https://onlinelibrary\.wiley\.com/doi/\S+ (?:by [^\n]{1,200}?)?,? ?"
    r"Wiley Online Library on \[\d{2}/\d{2}/\d{4}\]\. See the Terms and Conditions "
    r"\(https://onlinelibrary\.wiley\.com/terms-and-conditions\) on Wiley Online Library "
    r"for rules of use; OA articles are governed by the applicable Creative Commons "
    r"License\.?[ \t]*$\n?",
    re.MULTILINE)
# Per-page GSA download stamp, removed globally (self-gating on the host).
GSA_STAMP_RE = re.compile(
    r"[ \t]*Downloaded from \[?[\w.]*gsapubs\.org/?\]?(?:\([^)\s]*\))?"
    r" on [A-Z][a-z]+ \d{1,2}, \d{4}")


def strip_gsa(text: str) -> tuple[str, list[str]]:
    """Remove the GSA/HighWire cover run wherever it sits. Returns
    (new_text, removed_block_previews)."""
    blocks = text.split("\n\n")
    idx = next((i for i, b in enumerate(blocks) if GSA_GATE.search(b.strip())), None)
    if idx is None:
        return text, []
    end = idx + 1
    while end < len(blocks):
        b = blocks[end].strip()
        if b == "" or any(r.search(b) for r in GSA_COVER_RE):
            end += 1
        else:
            break
    removed = [b for b in blocks[idx:end] if b.strip()]
    new_text = "\n\n".join(blocks[:idx] + blocks[end:]).lstrip("\n")
    if new_text and not new_text.endswith("\n"):
        new_text += "\n"
    return new_text, [" ".join(b.split())[:90] for b in removed]


# HAL (hal.science / hal-*.archives-ouvertes.fr) deposit cover: "HAL Id: ..."
# heading, an optional URL line, "Submitted on DATE", then the English and
# French archive blurbs. The title, authors and "To cite this version:" block
# before it are kept. Gated on the English blurb; only the run from the HAL Id
# heading through the French blurb is removed, and only near the head.
HAL_GATE = re.compile(r"^\*\*HAL\*\* is a multi-disciplinary open access archive")
HAL_COVER_RE = [re.compile(p) for p in [
    r"^#*\s*\**HAL Id:",
    r"^\**<?https?://[^\s>]*(hal\.science|archives-ouvertes\.fr)[^\s>]*>?\**$",
    r"^Submitted on \d{1,2} \w+ \d{4}$",
    r"^\*\*HAL\*\* is a multi-disciplinary open access archive",
    r"^L[’']archive ouverte pluridisciplinaire \*\*HAL\*\*",
]]


def strip_hal(text: str) -> tuple[str, list[str]]:
    """Remove the HAL deposit cover run. Returns (new_text, removed_previews)."""
    blocks = text.split("\n\n")
    gate = next((i for i, b in enumerate(blocks[:40]) if HAL_GATE.search(b.strip())), None)
    if gate is None:
        return text, []
    is_cover = lambda b: any(r.search(b.strip()) for r in HAL_COVER_RE)
    lo = gate
    while lo > 0 and is_cover(blocks[lo - 1]):
        lo -= 1
    hi = gate + 1
    while hi < len(blocks) and is_cover(blocks[hi]):
        hi += 1
    removed = [b for b in blocks[lo:hi] if b.strip()]
    new_text = "\n\n".join(blocks[:lo] + blocks[hi:]).lstrip("\n")
    if new_text and not new_text.endswith("\n"):
        new_text += "\n"
    return new_text, [" ".join(b.split())[:90] for b in removed]


# New Mexico Geological Society (nmgs.nmt.edu) guidebook download cover: "This
# is one of many related papers...", the "Annual NMGS Fall Field Conference
# Guidebooks" blurb, "Free Downloads" and "Copyright Information". The society
# link, "Downloaded from:", title, authors, DOI and "in: <guidebook>" lines
# before it are kept. Gated on the blurb; only the contiguous run around it is
# removed, including the "---" page rules and the "intentionally left blank"
# facing page that vision OCR keeps.
NMGS_GATE = re.compile(r"^Every fall since 1950, the New Mexico Geological Society")
NMGS_COVER_RE = [re.compile(p) for p in [
    r"^\*This is one of many related papers that were included in the \d{4} NMGS",
    r"^#*\s*\**Annual NMGS Fall Field Conference Guidebooks\**$",
    r"^Every fall since 1950, the New Mexico Geological Society",
    r"^#*\s*\**Free Downloads\**$",
    r"^NMGS has decided to make peer-reviewed papers",
    r"^#*\s*\**Copyright Information\**$",
    r"^Publications of the New Mexico Geological Society, printed and electronic",
    r"^One printed copy of any materials from the NMGS website",
    r"^\*This page is intentionally left blank to maintain order of facing pages\.\*$",
    r"^---$",
]]


def strip_nmgs(text: str) -> tuple[str, list[str]]:
    """Remove the NMGS guidebook download cover. Returns (new_text, removed_previews)."""
    blocks = text.split("\n\n")
    gate = next((i for i, b in enumerate(blocks[:40]) if NMGS_GATE.search(b.strip())), None)
    if gate is None:
        return text, []
    is_cover = lambda b: any(r.search(b.strip()) for r in NMGS_COVER_RE)
    lo = gate
    while lo > 0 and is_cover(blocks[lo - 1]):
        lo -= 1
    hi = gate + 1
    while hi < len(blocks) and is_cover(blocks[hi]):
        hi += 1
    removed = [b for b in blocks[lo:hi] if b.strip() and b.strip() != "---"]
    new_text = "\n\n".join(blocks[:lo] + blocks[hi:]).lstrip("\n")
    if new_text and not new_text.endswith("\n"):
        new_text += "\n"
    return new_text, [" ".join(b.split())[:90] for b in removed]


# ETH Library E-Periodica (e-periodica.ch) download cover: a citation block
# (Zeitschrift / Herausgeber / Band / Heft / Artikel / Autor / DOI), then the
# terms of use in German, French and English (older downloads add the
# Haftungsausschluss / Responsabilité / Liability set), a "Download PDF: date"
# stamp and an "ETH-Bibliothek Zürich, E-Periodica" line. The citation block is
# kept; the terms, stamp and library line are removed. Gated on the library
# line or e-periodica URL plus a terms heading, both within the first 40
# blocks. A terms heading takes the paragraph after it with it.
EPERIODICA_GATE = re.compile(r"e-periodica\.ch|ETH-Bibliothek Z\u00fcrich, E-Periodica", re.IGNORECASE)
EPERIODICA_HEADING = re.compile(
    r"^#+\s*\**\s*(Nutzungsbedingungen|Conditions d.utilisation|Terms of use|"
    r"Haftungsausschluss|Haftung|Responsabilit\u00e9|Limitation de responsabilit\u00e9|Liability)\s*\**\s*$",
    re.IGNORECASE)
EPERIODICA_LINE = re.compile(
    r"^\**\s*(Download PDF:\**\s*[\d.]+|ETH-Bibliothek Z\u00fcrich, E-Periodica, https?://www\.e-periodica\.ch/?)\s*\**$",
    re.IGNORECASE)


def strip_eperiodica(text: str) -> tuple[str, list[str]]:
    """Remove the E-Periodica terms-of-use cover. Returns (new_text, removed_previews)."""
    blocks = text.split("\n\n")
    head = blocks[:40]
    if not (any(EPERIODICA_GATE.search(b) for b in head)
            and any(EPERIODICA_HEADING.match(b.strip()) for b in head)):
        return text, []
    drop: set[int] = set()
    for i, b in enumerate(head):
        t = b.strip()
        if EPERIODICA_HEADING.match(t):
            drop.add(i)
            if i + 1 < len(head) and not head[i + 1].lstrip().startswith("#"):
                drop.add(i + 1)
        elif EPERIODICA_LINE.match(t):
            drop.add(i)
    removed = [blocks[i] for i in sorted(drop) if blocks[i].strip()]
    new_text = "\n\n".join(b for i, b in enumerate(blocks) if i not in drop).lstrip("\n")
    if new_text and not new_text.endswith("\n"):
        new_text += "\n"
    return new_text, [" ".join(b.split())[:90] for b in removed]


def strip_royalsociety(text: str) -> tuple[str, list[str]]:
    """Remove the royalsocietypublishing.org cover sheet. Returns
    (new_text, removed_block_previews)."""
    head = text[:HEAD_LIMIT]
    if not (RS_GATE.search(head) and RS_COVMARK.search(head)):
        return text, []
    blocks = text.split("\n\n")
    idx = next((i for i, b in enumerate(blocks) if RS_SIGNUP.search(b)), None)
    if idx is None:
        return text, []
    removed = [b for b in blocks[:idx + 1] if b.strip()]
    rest = blocks[idx + 1:]
    while rest:
        b = rest[0].strip()
        if (b == "" or RS_RESIDUAL.search(b)
                or (("royalsocietypublishing" in b.lower() or "http" in b.lower())
                    and len(b) < 160)):
            if rest[0].strip():
                removed.append(rest[0])
            rest.pop(0)
        else:
            break
    new_text = "\n\n".join(rest).lstrip("\n")
    if new_text and not new_text.endswith("\n"):
        new_text += "\n"
    return new_text, [" ".join(b.split())[:90] for b in removed]


# UMI / ProQuest microfilm dissertation cover. Prepended by UMI/ProQuest
# microfilm scans of dissertations: an "INFORMATION TO USERS" microfilm-quality
# boilerplate page, then a "NOTE TO USERS / This reproduction is the best copy
# available / UMI" block. The real title page follows (and, further down, a
# SEPARATE "UMI Number / UMI Microform / Copyright … Bell & Howell" cluster that
# sits *after* the title — we deliberately do NOT touch that, to avoid eating the
# title/author/year). Removed as a head prefix through the terminal
# "best copy available" / "NOTE TO USERS" block plus a trailing standalone "UMI".
UMI_GATE = re.compile(r"information to users", re.IGNORECASE)
UMI_HALLMARK = re.compile(
    r"reproduced from the microfilm master|bell\s*&\s*howell|umi films the text"
    r"|this reproduction is the best copy available", re.IGNORECASE)
# Terminal anchors appear ONLY in the leading cover, never in the later
# copyright cluster — so anchoring on them keeps the real title page safe.
UMI_TERMINAL = re.compile(r"best copy available|note to users", re.IGNORECASE)
UMI_RESIDUAL = re.compile(
    r"^#*\**\s*umi\\?\*?\s*\**$|bell\s*&\s*howell|^-+$", re.IGNORECASE)


# UMI/ProQuest notice sitting AFTER the title page (shipman2004z): "UMI Number:
# NNNN", "INFORMATION TO USERS" and its two paragraphs, "UMI Microform NNNN",
# the ProQuest copyright line and its Ann Arbor address. strip_umi() handles
# only a notice BEFORE the title; this one is gated on the "UMI Number:" line
# and removes the contiguous run only while every block matches.
UMI_POST_GATE = re.compile(r"^UMI Number:\s*\d+$")
UMI_POST_RE = [re.compile(p, re.IGNORECASE) for p in [
    r"^UMI Number:\s*\d+$",
    r"^#*\s*\**INFORMATION TO USERS\**$",
    r"^The quality of this reproduction is dependent upon",
    r"^In the unlikely event that the author did not send a complete manuscript",
    r"^UMI Microform\s*\d+$",
    r"^Copyright \d{4} by (ProQuest|Bell & Howell)",
    r"^>?\s*(ProQuest|Bell & Howell) Information and Learning Company\b.*Ann Arbor",
]]


def strip_umi_post_title(text: str) -> tuple[str, list[str]]:
    blocks = text.split("\n\n")
    head_end, off = 0, 0
    for i, b in enumerate(blocks):           # only look in the first ~6 KB
        off += len(b) + 2
        head_end = i
        if off > 6000:
            break
    gate = next((i for i in range(head_end + 1) if UMI_POST_GATE.match(blocks[i].strip())), None)
    if gate is None:
        return text, []
    end = gate
    while end < len(blocks) and (not blocks[end].strip()
                                 or any(r.search(blocks[end].strip()) for r in UMI_POST_RE)):
        end += 1
    removed = [b for b in blocks[gate:end] if b.strip()]
    new_text = "\n\n".join(blocks[:gate] + blocks[end:])
    return new_text, [" ".join(b.split())[:90] for b in removed]


def strip_umi(text: str) -> tuple[str, list[str]]:
    """Remove the UMI/ProQuest microfilm dissertation cover as a head prefix.
    Returns (new_text, removed_block_previews)."""
    head = text[:HEAD_LIMIT]
    if not (UMI_GATE.search(head) and UMI_HALLMARK.search(head)):
        return text, []
    blocks = text.split("\n\n")
    idx, off = None, 0
    for i, b in enumerate(blocks):
        if off > HEAD_LIMIT:
            break
        if UMI_TERMINAL.search(b.strip()):
            idx = i                      # last terminal within the head zone
        off += len(b) + 2
    if idx is None:
        return text, []
    removed = [b for b in blocks[:idx + 1] if b.strip()]
    rest = blocks[idx + 1:]
    while rest:                          # consume trailing standalone "UMI" nav
        b = rest[0].strip()
        if b == "" or (UMI_RESIDUAL.search(b) and len(b) < 160):
            if rest[0].strip():
                removed.append(rest[0])
            rest.pop(0)
        else:
            break
    new_text = "\n\n".join(rest).lstrip("\n")
    if new_text and not new_text.endswith("\n"):
        new_text += "\n"
    return new_text, [" ".join(b.split())[:90] for b in removed]


# ZORA (Zurich Open Repository and Archive) deposit cover. Prepended by
# zora.uzh.ch to every deposit: repository masthead + address, "Year:", the
# cover title/authors, DOI, "Posted at the Zurich Open Repository … Published
# Version", a CC-BY license block, and an "Originally published at:" citation
# (+ a trailing "DOI:" line). The real article — a "RESEARCH ARTICLE" head or
# the repeated title — follows. Removed as a head prefix through the "Originally
# published at" citation, stopping at the first heading (the article start).
ZORA_GATE = re.compile(r"zurich open repository and archive", re.IGNORECASE)
ZORA_TERMINAL = re.compile(r"originally published at", re.IGNORECASE)
ZORA_RESIDUAL = re.compile(r"^doi:|doi\.org|^-+$", re.IGNORECASE)


def strip_zora(text: str) -> tuple[str, list[str]]:
    """Remove the ZORA (zora.uzh.ch) deposit cover as a head prefix.
    Returns (new_text, removed_block_previews)."""
    head = text[:HEAD_LIMIT]
    if not ZORA_GATE.search(head):
        return text, []
    blocks = text.split("\n\n")
    idx, off = None, 0
    for i, b in enumerate(blocks):
        if off > HEAD_LIMIT:
            break
        if ZORA_TERMINAL.search(b):
            idx = i                       # "Originally published at:" block
        off += len(b) + 2
    if idx is None:
        return text, []
    removed = [b for b in blocks[:idx + 1] if b.strip()]
    rest = blocks[idx + 1:]
    while rest:                           # consume trailing citation + DOI lines
        b = rest[0].strip()
        if b.startswith("#"):             # article heading reached — stop
            break
        if b == "" or (ZORA_RESIDUAL.search(b) and len(b) < 600):
            if rest[0].strip():
                removed.append(rest[0])
            rest.pop(0)
        else:
            break
    new_text = "\n\n".join(rest).lstrip("\n")
    if new_text and not new_text.endswith("\n"):
        new_text += "\n"
    return new_text, [" ".join(b.split())[:90] for b in removed]


# UAB DDD (Dipòsit Digital de Documents, ddd.uab.cat — Universitat Autònoma de
# Barcelona institutional repository) accepted-version cover. Structurally the
# same class as ZORA: a deposit banner ("This is the accepted version of the
# journal article:"), the cover citation (authors + «title» + journal + DOI),
# an "This version is available at <https://ddd.uab.cat/record/…>" line, and a
# closing "under the terms of the license" line. The real article — the
# repeated title as a heading — follows. Removed as a head prefix through the
# license/available-at anchor, stopping at the first heading (the article
# start). Gated on the ddd.uab.cat domain so a paper that merely cites a
# ddd.uab.cat URL in its body is untouched (gate is head-bounded).
DDD_GATE = re.compile(r"ddd\.uab\.cat", re.IGNORECASE)
DDD_TERMINAL = re.compile(r"ddd\.uab\.cat|under the terms of the licen[cs]e",
                          re.IGNORECASE)
DDD_RESIDUAL = re.compile(r"under the terms of the licen[cs]e|available at|^-+$",
                          re.IGNORECASE)


def strip_ddd(text: str) -> tuple[str, list[str]]:
    """Remove the UAB DDD (ddd.uab.cat) deposit cover as a head prefix.
    Returns (new_text, removed_block_previews)."""
    head = text[:HEAD_LIMIT]
    if not DDD_GATE.search(head):
        return text, []
    blocks = text.split("\n\n")
    idx, off = None, 0
    for i, b in enumerate(blocks):
        if off > HEAD_LIMIT:
            break
        if DDD_TERMINAL.search(b):
            idx = i                       # latest cover anchor (available-at / license)
        off += len(b) + 2
    if idx is None:
        return text, []
    removed = [b for b in blocks[:idx + 1] if b.strip()]
    rest = blocks[idx + 1:]
    while rest:                           # consume trailing license residue
        b = rest[0].strip()
        if b.startswith("#"):             # article heading reached — stop
            break
        if b == "" or (DDD_RESIDUAL.search(b) and len(b) < 300):
            if rest[0].strip():
                removed.append(rest[0])
            rest.pop(0)
        else:
            break
    new_text = "\n\n".join(rest).lstrip("\n")
    if new_text and not new_text.endswith("\n"):
        new_text += "\n"
    return new_text, [" ".join(b.split())[:90] for b in removed]


# eScholarship (escholarship.org, University of California) deposit cover.
# Same class as ZORA/DDD, but rendered as a run of labelled headings:
# "# **UC Berkeley**" / "# **PaleoBios**" / "# **Title**" + title / "# **Permalink**"
# + escholarship.org/uc/item URL / "# **Journal**" / "# **ISSN**" / "# **Authors**" /
# "# **Publication Date**" / "# **DOI**" / "# **Copyright Information**" + copyright
# text, closing with "Peer reviewed" and the "eScholarship.org Powered by the
# California Digital Library / University of California" footer. The journal's own
# cover/masthead page follows and is real published content, so the cut stops at the
# footer. Removed as a head prefix through the last cover anchor; gated on the
# escholarship.org domain in the head so a body that merely cites an eScholarship
# URL is untouched.
ESCH_GATE = re.compile(r"escholarship\.org", re.IGNORECASE)
ESCH_TERMINAL = re.compile(
    r"^#*\s*\**\s*peer reviewed\s*\**\s*$|escholarship\.org/terms"
    r"|escholarship\.org\s+powered by",
    re.IGNORECASE | re.MULTILINE)
ESCH_RESIDUAL = re.compile(
    r"escholarship\.org\s+powered by|powered by the california digital library"
    r"|^#*\s*\**\s*university of california\s*\**\s*$|^-+$",
    re.IGNORECASE | re.MULTILINE)
ESCH_MAX_ANCHOR = 600        # cover anchors are short lines, not body paragraphs


def strip_escholarship(text: str) -> tuple[str, list[str]]:
    """Remove the eScholarship (escholarship.org) deposit cover as a head prefix.
    Returns (new_text, removed_block_previews)."""
    head = text[:HEAD_LIMIT]
    if not ESCH_GATE.search(head):
        return text, []
    blocks = text.split("\n\n")
    idx, off = None, 0
    for i, b in enumerate(blocks):
        if off > HEAD_LIMIT:
            break
        if len(b) < ESCH_MAX_ANCHOR and ESCH_TERMINAL.search(b):
            idx = i                       # latest cover anchor (peer-reviewed / terms)
        off += len(b) + 2
    if idx is None:
        return text, []
    removed = [b for b in blocks[:idx + 1] if b.strip()]
    rest = blocks[idx + 1:]
    while rest:                           # consume the CDL footer lines
        b = rest[0].strip()
        if b == "" or (ESCH_RESIDUAL.search(b) and len(b) < 200):
            if rest[0].strip():
                removed.append(rest[0])
            rest.pop(0)
        else:
            break
    new_text = "\n\n".join(rest).lstrip("\n")
    if new_text and not new_text.endswith("\n"):
        new_text += "\n"
    return new_text, [" ".join(b.split())[:90] for b in removed]


# Yale Peabody Museum / EliScholar archive cover. The museum's retrospective
# digitisation prepends a series description to every Bulletin and Postilla
# scan: a series-name heading, a paragraph on what the series published and
# when it ceased, a "available from BioOne Complete" line, a note that the
# archives live on EliScholar, a Creative Commons licence line, and the
# museum's postal address. The real title follows. Removed as a head prefix
# through the last cover anchor, gated on the elischolar.library.yale.edu
# domain so a paper that merely cites an EliScholar URL is untouched.
PEABODY_GATE = re.compile(r"elischolar", re.IGNORECASE)
PEABODY_TERMINAL = re.compile(
    r"P\.?\s*O\.?\s*Box\s*208118|peabody\.yale\.edu"
    r"|creativecommons\.org/licenses|elischolar\.library\.yale\.edu",
    re.IGNORECASE)
PEABODY_RESIDUAL = re.compile(
    r"^new haven|^usa\b|creative commons|^-+$", re.IGNORECASE)


def strip_peabody(text: str) -> tuple[str, list[str]]:
    """Remove the Yale Peabody / EliScholar archive cover as a head prefix.
    Returns (new_text, removed_block_previews)."""
    head = text[:HEAD_LIMIT]
    if not PEABODY_GATE.search(head):
        return text, []
    blocks = text.split("\n\n")
    # The FIRST anchor, then extend over the contiguous cover run — not the
    # last anchor in the head zone. A facsimile carries the monograph's own
    # colophon further down ("Publications Office, Peabody Museum … P.O. Box
    # 208118"), which is real published front matter; anchoring on the last
    # match swallowed the title and author with it.
    idx, off = None, 0
    for i, b in enumerate(blocks):
        if off > HEAD_LIMIT:
            break
        if PEABODY_TERMINAL.search(b):
            idx = i
            break
        off += len(b) + 2
    if idx is None:
        return text, []
    while idx + 1 < len(blocks):
        nxt = blocks[idx + 1].strip()
        if not nxt or (len(nxt) < 400 and (PEABODY_TERMINAL.search(nxt)
                                           or PEABODY_RESIDUAL.search(nxt))):
            idx += 1
        else:
            break
    removed = [b for b in blocks[:idx + 1] if b.strip()]
    rest = blocks[idx + 1:]
    while rest:                           # consume address / licence residue
        b = rest[0].strip()
        if b.startswith("#"):             # article heading reached — stop
            break
        if b == "" or (PEABODY_RESIDUAL.search(b) and len(b) < 200):
            if rest[0].strip():
                removed.append(rest[0])
            rest.pop(0)
        else:
            break
    new_text = "\n\n".join(rest).lstrip("\n")
    if new_text and not new_text.endswith("\n"):
        new_text += "\n"
    return new_text, [" ".join(b.split())[:90] for b in removed]


# --- Elsevier / ScienceDirect PUBLISHED-version masthead ---
# Distinct from the accepted-manuscript cover (ELSEVIER_HALLMARK): the
# published PDF opens with a "Contents lists available at ScienceDirect"
# line, the journal-name heading, and a "journal homepage: …" line, sitting
# as a contiguous prefix before the real article title. Often preceded by a
# running-head link ("[Journal name xxx (xxxx) xxx](https://doi.org/10.1016/…)"),
# and on vision-OCR'd copies followed by a "[FIGURE: … cover …]" description
# of the journal's cover thumbnail.
#
# The homepage line is the terminal, unambiguous masthead element, so the cut
# runs anchor..homepage. Variants tolerated: heading level 0/##/####, the older
# "SciVerse ScienceDirect" branding, the URL bare or in a markdown link,
# marker's letterspaced "j ou rnal homepa ge:" mangling, and Comptes Rendus
# Palevol's bare "<www.sciencedirect.com>" in place of the homepage line.
# If no homepage line turns up within SD_LOOKAHEAD blocks, only the anchor
# block is cut — never a run reaching blindly into the title.
SD_ANCHOR_RE = re.compile(
    r"contents lists available at\s*(?:\[\s*)?(?:sciverse\s*)?(?:\[\s*)?sciencedirect",
    re.IGNORECASE)
SD_HOMEPAGE_RE = re.compile(
    r"j\s?ou\s?rnal\s+homepa\s?ge\s*:|^\s*<?www\.sciencedirect\.com>?\s*$",
    re.IGNORECASE | re.MULTILINE)
SD_RUNHEAD_RE = re.compile(r"^\[[^\]]{10,220}\]\([^)]*\)$")
SD_RUNHEAD_MARK = re.compile(r"doi\.org|\bxxx\b|\(\s*(?:19|20)\d\d\s*\)",
                             re.IGNORECASE)
SD_COVERFIG_RE = re.compile(r"^\[FIGURE:[^\]]*\bcover\b", re.IGNORECASE)
SD_LOOKAHEAD = 3         # journal heading (+ a stray line) before the homepage
SD_MAX_INTERMEDIATE = 200  # a journal-name heading is short; a title run is not


# Marker sometimes DROPS the "Contents lists available at ScienceDirect"
# anchor entirely. Elsevier's page-1 layout puts the masthead in a box whose
# text lands LAST in the page content stream; marker reorders it to the head
# of the document but keeps only the journal-name heading and the homepage
# line, leaving the anchor-gated cut above with nothing to match on. The
# headless form is therefore: block 0 is a short journal-name heading, and an
# Elsevier/ScienceDirect homepage line follows within SD_LOOKAHEAD blocks.
#
# Gated harder than the anchored form, since the anchor is what normally makes
# the cut safe: the run must start at the very first block (a masthead marker
# hoisted mid-document is some other artifact, not this one), and the homepage
# block must name an Elsevier domain — "journal homepage:" alone also belongs
# to the tandfonline masthead, which has its own handler.
SD_HEADLESS_DOMAIN_RE = re.compile(
    r"elsevier\.com|sciencedirect\.com", re.IGNORECASE)


def _sciencedirect_headless_span(blocks: list[str]) -> tuple[int, int] | None:
    """Locate the anchorless masthead run, or None. Returns (start, end)."""
    if not blocks or not blocks[0].strip():
        return None
    head = blocks[0].strip()
    # Block 0 must be a short heading — the journal name, not a title run.
    if not head.startswith("#") or len(head) > SD_MAX_INTERMEDIATE:
        return None
    if SD_HOMEPAGE_RE.search(head):
        return None
    for j in range(1, min(1 + SD_LOOKAHEAD, len(blocks))):
        b = blocks[j]
        if SD_HOMEPAGE_RE.search(b) and SD_HEADLESS_DOMAIN_RE.search(b):
            return (0, j)
        if len(b) > SD_MAX_INTERMEDIATE:
            break
    return None


def strip_sciencedirect(text: str) -> tuple[str, list[str]]:
    """Remove the ScienceDirect published-version masthead prefix.
    Returns (new_text, removed_block_previews)."""
    if not SD_ANCHOR_RE.search(text[:HEAD_LIMIT]):
        blocks = text.split("\n\n")
        span = _sciencedirect_headless_span(blocks)
        if span is None:
            return text, []
        start, end = span
        removed = [" ".join(b.split())[:90]
                   for b in blocks[start:end + 1] if b.strip()]
        new_text = "\n\n".join(blocks[end + 1:]).lstrip("\n")
        if new_text and not new_text.endswith("\n"):
            new_text += "\n"
        return new_text, removed
    blocks = text.split("\n\n")
    start, off = -1, 0
    for i, b in enumerate(blocks):
        if off > HEAD_LIMIT:
            break
        if SD_ANCHOR_RE.search(b):
            start = i
            break
        off += len(b) + 2
    if start < 0:
        return text, []

    # Forward: run to the homepage line, refusing to cross a long block.
    end = start
    for j in range(start, min(start + SD_LOOKAHEAD + 1, len(blocks))):
        if SD_HOMEPAGE_RE.search(blocks[j]):
            end = j
            break
        if j > start and len(blocks[j]) > SD_MAX_INTERMEDIATE:
            break
    # A vision-transcribed cover thumbnail sits just past the homepage line.
    if end + 1 < len(blocks) and SD_COVERFIG_RE.search(blocks[end + 1].strip()):
        end += 1
    # Backward: the running-head link block immediately above the anchor.
    if start > 0:
        prev = blocks[start - 1].strip()
        if (SD_RUNHEAD_RE.match(prev) and SD_RUNHEAD_MARK.search(prev)
                and len(prev) <= 250):
            start -= 1

    removed = [" ".join(b.split())[:90] for b in blocks[start:end + 1] if b.strip()]
    new_text = "\n\n".join(blocks[:start] + blocks[end + 1:]).lstrip("\n")
    if new_text and not new_text.endswith("\n"):
        new_text += "\n"
    return new_text, removed


# --- Ameghiniana "Also appearing in this issue" table of contents ---
# The Ameghiniana / Asociación Paleontológica Argentina cover sheet ends with
# an "Also appearing in this issue:" heading and 2-4 one-line teasers for the
# OTHER articles in that issue. Left in place these are the worst kind of
# boilerplate: they are real, well-formed palaeontology titles about entirely
# different taxa, so they land in the FTS body and make a paper answer searches
# it has nothing to do with (gallina2015a advertising a graptolite-trilobite
# correlation and a Pleistocene legume flora).
#
# Removed as the anchor block plus the contiguous teaser run. A teaser is one
# short single line, optionally under a marker-guessed heading level, ending in
# a period. The run stops at the first block that is not one — in practice the
# article's own title (no terminal period) or an affiliation block (leading
# "**"/<sup>) — so the cut cannot reach into the article.
#
# NOT removed: the cover's duplicated title/author/"To cite this article"
# block. That is the paper's own metadata, it pollutes nothing, and which of
# the two title copies is the real one differs between the Ameghiniana-only
# and the BioOne-wrapped forms of this cover.
AMGH_TOC_RE = re.compile(r"also appearing in this issue", re.IGNORECASE)
AMGH_MAX_TEASERS = 5
AMGH_MAX_TEASER_LEN = 250


def _is_amgh_teaser(block: str) -> bool:
    b = block.strip()
    if not b or "\n" in b or len(b) > AMGH_MAX_TEASER_LEN:
        return False
    if b.startswith("**") or "<sup>" in b:
        return False
    return b.lstrip("#").strip().endswith(".")


# --- thefreelibrary.com article rendering ---------------------------------
# The Free Library reprints journal articles as web pages, and is sometimes the
# only digitised copy of an older paper (parris1992a, Texas J. Sci. 1992). Its
# HTML-to-PDF rendering wraps the real article in site furniture: a search-nav
# table and a breadcrumb at the head, a nav/terms run at the tail, and -- the
# part a head-bounded denylist cannot reach -- large Google ad blocks spliced
# INTO the body between paragraphs, several KB past HEAD_LIMIT.
#
# So this cut is global, like the ACCEPTED MANUSCRIPT watermark, and gated on
# the site's own domain appearing in the file, so a paper that merely cites a
# thefreelibrary.com URL in its references is untouched.
#
# Kept: title, abstract, body, references, byline, affiliation and the
# journal's own copyright statement -- everything that is the article.
FREELIB_HALLMARK = re.compile(r"thefreelibrary\.com", re.IGNORECASE)
FREELIB_JUNK = [
    r"googleadservices\.com|doubleclick\.net",      # ad blocks, wherever they sit
    r"^\|\s*Periodicals",                            # search-nav table
    r"^The Free \[Library\]\(",                      # breadcrumb (head and tail)
    r"^#+\s*\[Link/Page\]\(javascript:",            # citation toggle widget
    r"^\\?\*{6,}\\?\*?\s*$",                       # "**********" rule
    r"^\*\*Please bookmark with social media",
    r"^#+\s*Advertisement\.\s*Bad banner\?",
    r"^Publications by Name Publications by Date Authors Literature",
    r"^\[Terms\]\(https?://www\.thefreelibrary\.com/terms",
]
FREELIB_RE = [re.compile(p, re.IGNORECASE | re.MULTILINE) for p in FREELIB_JUNK]


def strip_freelibrary(text: str) -> tuple[str, list[str]]:
    """Remove thefreelibrary.com site furniture from a reprinted article."""
    if not FREELIB_HALLMARK.search(text):
        return text, []
    blocks = text.split("\n\n")
    kept, removed = [], []
    for b in blocks:
        bs = b.strip()
        if bs and any(rx.search(bs) for rx in FREELIB_RE):
            removed.append(" ".join(bs.split())[:90])
            continue
        kept.append(b)
    new_text = "\n\n".join(kept).lstrip("\n")
    if new_text and not new_text.endswith("\n"):
        new_text += "\n"
    return new_text, removed


def strip_ameghiniana_toc(text: str) -> tuple[str, list[str]]:
    """Remove the Ameghiniana issue table of contents (other articles' titles).
    Returns (new_text, removed_block_previews)."""
    if not AMGH_TOC_RE.search(text[:HEAD_LIMIT]):
        return text, []
    blocks = text.split("\n\n")
    start, off = -1, 0
    for i, b in enumerate(blocks):
        if off > HEAD_LIMIT:
            break
        if AMGH_TOC_RE.search(b):
            start = i
            break
        off += len(b) + 2
    if start < 0:
        return text, []
    end = start
    for j in range(start + 1, min(start + 1 + AMGH_MAX_TEASERS, len(blocks))):
        if not _is_amgh_teaser(blocks[j]):
            break
        end = j
    removed = [" ".join(b.split())[:90] for b in blocks[start:end + 1] if b.strip()]
    new_text = "\n\n".join(blocks[:start] + blocks[end + 1:]).lstrip("\n")
    if new_text and not new_text.endswith("\n"):
        new_text += "\n"
    return new_text, removed


# --- EBSCOhost delivery notice ---
# EBSCO appends a single copyright/terms block after the reference list:
# "Copyright of <journal> is the property of <publisher> and its content may
# not be copied or emailed to multiple sites or posted to a listserv without
# the copyright holder's express written permission. However, users may print,
# download, or email articles for individual use."
# Tail-anchored only (it never appears mid-article), so a body paragraph that
# happens to quote the phrase is untouched.
EBSCO_HALLMARK = re.compile(
    r"copied or emailed to multiple sites or posted to a listserv",
    re.IGNORECASE)
EBSCO_TAIL_SCAN = 4          # how many trailing blocks to consider


def strip_ebsco(text: str) -> tuple[str, list[str]]:
    """Remove the tail-anchored EBSCOhost delivery notice.
    Returns (new_text, removed_block_previews)."""
    if not EBSCO_HALLMARK.search(text):
        return text, []
    blocks = text.split("\n\n")
    # Locate the notice within the last few blocks only.
    idx = None
    for i in range(len(blocks) - 1, max(-1, len(blocks) - 1 - EBSCO_TAIL_SCAN), -1):
        if EBSCO_HALLMARK.search(blocks[i]):
            idx = i
            break
    if idx is None:
        return text, []
    removed = [b for b in blocks[idx:] if b.strip()]
    kept = blocks[:idx]
    # Consume the separator / blank residue the notice was preceded by.
    while kept and (not kept[-1].strip() or kept[-1].strip() == "---"):
        kept.pop()
    new_text = "\n\n".join(kept).rstrip("\n")
    if new_text:
        new_text += "\n"
    return new_text, [" ".join(b.split())[:90] for b in removed]


def strip_bhl(text: str) -> tuple[str, list[str]]:
    """Remove head-/tail-anchored BHL attribution runs. Gap-tolerant so that
    interspersed institution-name / citation / DOI blocks don't break the run.
    Returns (new_text, removed_block_previews)."""
    if not BHL_STRONG_HALLMARK.search(text):
        return text, []
    blocks = text.split("\n\n")
    n = len(blocks)
    bhl = [_is_bhl_block(b) for b in blocks]
    strong = [bool(BHL_STRONG_HALLMARK.search(b)) for b in blocks]
    remove = [False] * n
    GAP = 2  # tolerate up to this many consecutive non-attribution "bridge" blocks

    def edge(seed: int, step: int) -> int:
        """Farthest attribution block reachable from seed in direction step."""
        last, consec, j = seed, 0, seed + step
        while 0 <= j < n:
            if bhl[j] or strong[j]:
                last, consec = j, 0
            elif blocks[j].strip() in ("", "---"):
                pass  # neutral separator, doesn't count against the gap budget
            else:
                consec += 1
                if consec > GAP:
                    break
            j += step
        return last

    for i in range(n):
        if not strong[i]:
            continue
        lo, hi = edge(i, -1), edge(i, +1)
        # grab the attribution's leading citation line (BHL puts it first)
        if lo - 1 >= 0 and blocks[lo].strip() not in ("", "---") \
                and _BHL_CITATION_RE.match(blocks[lo - 1].strip()) \
                and len(blocks[lo - 1]) < 360:
            lo -= 1
        if lo <= 1 or hi >= n - 2:            # only head- or tail-anchored runs
            for k in range(lo, hi + 1):
                remove[k] = True

    kept, removed = [], []
    for idx, b in enumerate(blocks):
        if remove[idx] and b.strip():
            removed.append(" ".join(b.split())[:90])
        elif not remove[idx]:
            kept.append(b)
    new_text = "\n\n".join(kept).strip("\n")
    if new_text:
        new_text += "\n"
    return new_text, removed


# --- T&F cover nav strip rendered as a markdown TABLE ---
# The T&F cover's download-stats / nav widgets ("Published online", "Submit
# your article to this journal", "Article views: N", "View related articles",
# "View Crossmark data", "Citing articles: N View citing articles"). Those
# phrases are already in PHRASE_JUNK, but anchored with ^ — and marker often
# lays the widgets out as a markdown table, so each line starts "| " and the
# anchors miss. Loosening the anchors is the wrong fix: one stray nav cell
# would then condemn a whole table of real content. Instead require the ENTIRE
# block to be nav — every non-empty line either a table separator or a row
# whose every cell is nav vocabulary. Head-bounded by the caller.
TF_NAV_CELL_RE = re.compile(
    r"^(?:published online:?\s*\d.*"
    r"|submit your article to this journal"
    r"|article views:?\s*[\d,]+"
    r"|view related articles"
    r"|view crossmark data"
    r"|citing articles:?\s*\d+(?:\s+view citing articles)?"
    r"|view citing articles"
    r"|full terms\s*&?\s*conditions of access and use can be found at.*"
    r"|https?://\S+)$", re.IGNORECASE)

TF_NAV_SEP_RE = re.compile(r"^\|?[\s:|-]+\|?$")

# At least one of these must appear or the block is not a T&F nav strip.
TF_NAV_STRONG = re.compile(
    r"submit your article to this journal"
    r"|view crossmark data"
    r"|citing articles:?\s*\d"
    r"|article views:?\s*\d", re.IGNORECASE)

# Nav cells pick up widget glyphs from the rendered page (☑ 🔍 🗗 ↗ ⧉ …) and
# trailing punctuation; drop non-alphanumeric decoration before matching.
_NAV_DECOR_RE = re.compile(r"^[^\w]+|[^\w)\]]+$")


def _is_tf_nav_block(block: str) -> bool:
    b = block.strip()
    if not b or not TF_NAV_STRONG.search(b):
        return False
    for line in b.splitlines():
        line = line.strip()
        if not line or TF_NAV_SEP_RE.match(line):
            continue
        if "|" in line:
            cells = [c.strip() for c in line.strip("|").split("|")]
        else:
            cells = [line]
        for cell in cells:
            cell = _NAV_DECOR_RE.sub("", cell).strip()
            if cell and not TF_NAV_CELL_RE.match(cell):
                return False
    return True


def block_is_junk(block: str, offset: int, tf: bool, jstor: bool,
                  elsevier: bool, tf_nav: bool = False) -> bool:
    b = block.strip()
    if not b:
        return False
    for rx in PHRASE_RE:
        if rx.search(b):
            return True
    if tf_nav and PUBLISHED_ONLINE_RE.search(b):
        return True
    if _is_tf_nav_block(b):
        return True
    if tf and JOURNAL_HEADING_RE.match(b):
        return True
    if jstor and offset < REF_HEADING_LIMIT and REF_HEADING_RE.match(b):
        return True
    if elsevier:
        if ACCEPTED_MS_HEADING_RE.match(b):
            return True
        if any(rx.search(b) for rx in ELSEVIER_JUNK_RE):
            return True
    return False


def strip_file(text: str) -> tuple[str, list[str], int]:
    """Return (new_text, removed_block_previews, n_watermark_removals)."""
    tf = bool(TF_HALLMARK.search(text[:HEAD_LIMIT]))
    jstor = bool(JSTOR_HALLMARK.search(text[:HEAD_LIMIT]))
    elsevier = bool(ELSEVIER_HALLMARK.search(text[:HEAD_LIMIT]))

    # Global (not head-bounded) Elsevier "ACCEPTED MANUSCRIPT" watermark strip,
    # gated on the file clearly being an Elsevier accepted manuscript.
    n_watermark = 0
    if elsevier:
        text, n1 = WATERMARK_LINE_RE.subn("", text)
        text, n2 = WATERMARK_PREFIX_RE.subn(r"\1", text)
        n_watermark = n1 + n2
        # Shredded rotated watermark. Junk rows first — otherwise the per-cell
        # rule eats their leading letter and leaves the rest of the run behind.
        n3 = n4 = n5 = 0

        def _drop_junk_row(m: re.Match) -> str:
            nonlocal n3
            row = m.group(0)
            if "<br>" not in row:
                return row
            letters = "".join(row.replace("<br>", "").split()).replace("|", "")
            if not _is_watermark_run(letters):
                return row
            n3 += 1
            return ""

        def _drop_only_cell(m: re.Match) -> str:
            nonlocal n4
            if not _is_watermark_run(m.group(2).replace("<br>", "")):
                return m.group(0)
            n4 += 1
            return m.group(1)

        def _drop_cell_prefix(m: re.Match) -> str:
            nonlocal n5
            if not _is_watermark_run(m.group(2).replace("<br>", "")):
                return m.group(0)
            n5 += 1
            return m.group(1)

        text = WATERMARK_FRAGMENT_ROW_RE.sub(_drop_junk_row, text)
        text = WATERMARK_ONLY_CELL_RE.sub(_drop_only_cell, text)
        text = WATERMARK_FRAGMENT_CELL_RE.sub(_drop_cell_prefix, text)
        n_watermark += n3 + n4 + n5

    # In-cell watermark (self-gating, see the regex).
    text, n_cell = WATERMARK_CELL_RE.subn(r"\1", text)
    n_watermark += n_cell

    # Elsevier "Journal Pre-proof" page footer (self-gating, see the regex).
    # Deliberately NOT under the `elsevier` head gate: once an earlier pass has
    # cut the PII / "# Journal Pre-proof" cover, the hallmark no longer fires,
    # but the per-page footers are still glued to every page's last paragraph
    # (soto2024a). The phrase ending a line is its own evidence.
    text, n_pp = PREPROOF_FOOTER_RE.subn("", text)
    n_watermark += n_pp

    # Rotated "Journal Pre-proof" watermark shredded down prose line starts.
    # Self-gating on the vertical letter sequence (see strip_preproof_rotated),
    # so it is deliberately outside the `elsevier` head gate like the footer.
    text, n_rot = strip_preproof_rotated(text)
    n_watermark += n_rot

    # Cambridge Journals per-page download stamp (self-gating, see the regex).
    text, n_cam = CAMBRIDGE_STAMP_RE.subn("", text)
    n_watermark += n_cam

    # Royal Society per-page download stamp (self-gating, see the regex).
    text, n_rs = ROYALSOC_STAMP_RE.subn("", text)
    n_watermark += n_rs

    # GSA per-page download stamp (self-gating, see the regex).
    text, n_gsa = GSA_STAMP_RE.subn("", text)
    n_watermark += n_gsa

    # AAPG Datapages personalised stamp (self-gating, see the regex).
    text, n_aapg = AAPG_STAMP_RE.subn("", text)
    n_watermark += n_aapg

    # Wiley Online Library personalised footer (self-gating, see the regex).
    text, n_wiley = WILEY_STAMP_RE.subn("", text)
    n_watermark += n_wiley

    # BHL attribution page (head-/tail-anchored, gated on a strong marker).
    text, bhl_removed = strip_bhl(text)
    # T&F tandfonline masthead prefix.
    text, tf_removed = strip_tf_masthead(text)
    # Royal Society (royalsocietypublishing.org) cover sheet.
    text, rs_removed = strip_royalsociety(text)
    # GSA (gsapubs.org) cover sheet, head or tail.
    text, gsa_removed = strip_gsa(text)
    # HAL deposit cover (hal.science / archives-ouvertes.fr).
    text, hal_removed = strip_hal(text)
    # NMGS (nmgs.nmt.edu) guidebook download cover.
    text, nmgs_removed = strip_nmgs(text)
    # ETH Library E-Periodica (e-periodica.ch) terms-of-use cover.
    text, eper_removed = strip_eperiodica(text)
    # UMI / ProQuest microfilm dissertation cover prefix.
    text, umi_removed = strip_umi(text)
    text, umi_post = strip_umi_post_title(text)
    umi_removed = umi_removed + umi_post
    # ZORA (zora.uzh.ch) deposit cover prefix.
    text, zora_removed = strip_zora(text)
    # UAB DDD (ddd.uab.cat) deposit cover prefix.
    text, ddd_removed = strip_ddd(text)
    # eScholarship (escholarship.org) deposit cover prefix.
    text, esch_removed = strip_escholarship(text)
    # Yale Peabody / EliScholar archive cover prefix.
    text, peabody_removed = strip_peabody(text)
    # Elsevier/ScienceDirect published-version masthead prefix.
    text, sd_removed = strip_sciencedirect(text)
    # Ameghiniana issue table of contents (other articles' titles).
    text, amgh_removed = strip_ameghiniana_toc(text)
    # EBSCOhost delivery notice (tail-anchored).
    text, ebsco_removed = strip_ebsco(text)
    # thefreelibrary.com site furniture (global: ads sit inside the body).
    text, freelib_removed = strip_freelibrary(text)

    tf_nav = bool(TF_NAV_HEAD_RE.search(text[:HEAD_LIMIT]))
    blocks = text.split("\n\n")
    kept: list[str] = []
    removed: list[str] = []
    offset = 0
    for blk in blocks:
        eligible = offset < HEAD_LIMIT
        if eligible and block_is_junk(blk, offset, tf, jstor, elsevier, tf_nav):
            preview = " ".join(blk.split())[:90]
            removed.append(preview)
        else:
            kept.append(blk)
        offset += len(blk) + 2  # account for the "\n\n" separator

    new_text = "\n\n".join(kept).lstrip("\n")
    if new_text and not new_text.endswith("\n"):
        new_text += "\n"
    return (new_text,
            bhl_removed + tf_removed + rs_removed + gsa_removed + hal_removed + nmgs_removed + eper_removed + umi_removed + zora_removed
            + ddd_removed + esch_removed + peabody_removed + sd_removed + amgh_removed
            + freelib_removed
            + ebsco_removed
            + removed,
            n_watermark)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true",
                    help="write changes (default is dry-run)")
    ap.add_argument("--key", type=key_arg, action="append", default=[],
                    help="limit to specific key(s)")
    ap.add_argument("--show", type=int, default=4,
                    help="how many sample files to show removed blocks for")
    args = ap.parse_args()

    if args.key:
        files = [MD_DIR / f"{k}.md" for k in args.key]
    else:
        files = [f for f in sorted(MD_DIR.glob("*.md"))
                 if not f.name.endswith(".original.md")]

    changed = 0
    skipped_guard = []
    total_removed_blocks = 0
    total_removed_bytes = 0
    total_watermarks = 0
    samples = []

    for f in files:
        if not f.exists():
            print(f"  ! {f.name} not found"); continue
        orig = f.read_text(errors="ignore", encoding="utf-8")
        new, removed, n_wm = strip_file(orig)
        if not removed and not n_wm:
            continue
        # safety guard
        if len(new.strip()) < MIN_KEEP or len(new) < MIN_KEEP_FRAC * len(orig):
            skipped_guard.append((f.name, len(orig), len(new), len(removed)))
            continue
        changed += 1
        total_removed_blocks += len(removed)
        total_removed_bytes += len(orig) - len(new)
        total_watermarks += n_wm
        if len(samples) < args.show:
            samples.append((f.name, len(orig) - len(new), removed, n_wm))
        if args.apply:
            atomic_write_text(f, new)

    mode = "APPLIED" if args.apply else "DRY RUN (no files written)"
    print(f"=== strip_frontmatter — {mode} ===")
    print(f"files scanned:          {len(files)}")
    print(f"files with boilerplate: {changed}")
    print(f"blocks removed:         {total_removed_blocks}")
    print(f"watermark lines cut:    {total_watermarks}")
    print(f"bytes removed:          {total_removed_bytes:,}")
    if skipped_guard:
        print(f"\n⚠ skipped by safety guard ({len(skipped_guard)}) "
              f"— would strip too much, inspect by hand:")
        for name, o, n, nb in skipped_guard:
            print(f"    {name:32s} {o:6d} -> {n:6d} chars ({nb} junk blocks)")
    if samples:
        print(f"\n--- sample removed blocks (first {len(samples)} files) ---")
        for name, nb, removed, n_wm in samples:
            wm = f", {n_wm} watermark lines" if n_wm else ""
            print(f"\n  {name}  (-{nb} bytes, {len(removed)} blocks{wm}):")
            for prev in removed:
                print(f"      ✂ {prev}")
    if not args.apply and changed:
        print("\nRe-run with --apply to write. Then re-index:")
        print("  ./.runtime/venv/bin/python scripts/indexer.py")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
