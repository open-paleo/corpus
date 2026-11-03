"""Searching the index: what someone types, the filters, and the result page.

Plain queries are rewritten so that nothing typed is read as FTS5 syntax:
every word and every "quoted phrase" becomes an FTS5 string, which FTS5 then
splits with the index's own tokenizer. `T. rex`, `Late-Cretaceous`,
`MPC-D 100/1` and `O'Connor` all search for their words instead of failing as
syntax errors or column filters. A trailing `*` keeps prefix search. Syntax
mode passes the query through unchanged, for NEAR, OR and column filters.
"""
from __future__ import annotations

import html
import json
import re
import sqlite3
from dataclasses import dataclass

# snippet() markers: Unicode noncharacters, which no text holds (control
# characters and private-use characters both occur in damaged text layers).
MARK_OPEN, MARK_CLOSE = "﷐", "﷑"
PER_PAGE = 50
SORTS = {
    "relevance": "rank",
    "year": "CAST(year AS INTEGER), citation_key",
    "year-desc": "CAST(year AS INTEGER) DESC, citation_key",
}

_TOKEN = re.compile(r'"([^"]*)"|(\S+)')
_WORD = re.compile(r"\w")


class QueryError(ValueError):
    """A query the index cannot run; the message is shown to the user."""


def plain_query(text: str) -> str:
    """The FTS5 query for words and "quoted phrases", with no other syntax."""
    parts = []
    for m in _TOKEN.finditer(text):
        phrase, word = m.group(1), m.group(2)
        if phrase is not None:
            if _WORD.search(phrase):
                parts.append('"' + phrase.replace('"', '""') + '"')
            continue
        prefix = len(word) > 1 and word.endswith("*")
        word = word.rstrip("*").replace('"', "")
        if _WORD.search(word):        # a token of punctuation alone matches nothing
            parts.append('"' + word + '"' + ("*" if prefix else ""))
    return " ".join(parts)


@dataclass
class Filters:
    genus: str = ""
    clade: str = ""
    year_from: int | None = None
    year_to: int | None = None
    tool: str = ""
    translated: bool = False
    with_supplements: frozenset[str] | None = None   # parent keys, when filtering on them

    def where(self) -> tuple[list[str], list]:
        sql, params = [], []
        # genera and clades hold "A, B, C": match one whole name, case-sensitively
        for col, name in (("genera", self.genus), ("clades", self.clade)):
            if name:
                sql.append(f"instr(', ' || {col} || ', ', ?) > 0")
                params.append(f", {name}, ")
        if self.year_from is not None:
            sql.append("year != '' AND CAST(year AS INTEGER) >= ?")
            params.append(self.year_from)
        if self.year_to is not None:
            sql.append("year != '' AND CAST(year AS INTEGER) <= ?")
            params.append(self.year_to)
        if self.tool:
            sql.append("tool = ?")
            params.append(self.tool)
        if self.translated:
            sql.append("translated = '1'")
        if self.with_supplements is not None:
            sql.append("citation_key IN (SELECT value FROM json_each(?))")
            params.append(json.dumps(sorted(self.with_supplements)))
        return sql, params


def snippet_html(s: str | None) -> str:
    """A snippet, HTML-escaped, with its match markers turned into <mark>.

    Markdown emphasis marks are dropped: a snippet is a fragment, so its
    asterisks and underscores often have no partner to render against.
    """
    s = re.sub(r"\*+|(?<!\w)_+|_+(?!\w)", "", s or "")
    return html.escape(s).replace(MARK_OPEN, "<mark>").replace(MARK_CLOSE, "</mark>")


def search(con: sqlite3.Connection, text: str, filters: Filters, *, syntax: bool = False,
           sort: str = "relevance", page: int = 1) -> dict:
    """One page of results, with the total number of matches."""
    match = text.strip() if syntax else plain_query(text)
    where, params = filters.where()
    if match:
        where.insert(0, "papers MATCH ?")
        params.insert(0, match)
    elif text.strip() and not syntax:
        raise QueryError("The query has no words to search for.")
    if not where:
        return {"total": 0, "page": 1, "per_page": PER_PAGE, "hits": []}
    if sort not in SORTS:
        raise QueryError(f"Unknown sort {sort!r}.")
    order = SORTS[sort] if (match or sort != "relevance") else SORTS["year"]
    snip = (f"snippet(papers, 7, '{MARK_OPEN}', '{MARK_CLOSE}', '…', 28)"
            if match else "''")
    clause = " AND ".join(where)
    page = max(1, page)
    try:
        total = con.execute(f"SELECT count(*) FROM papers WHERE {clause}", params).fetchone()[0]
        rows = con.execute(
            f"SELECT citation_key, year, title, authors, journal, genera, clades, tool, "
            f"translated, {snip} FROM papers WHERE {clause} ORDER BY {order} LIMIT ? OFFSET ?",
            [*params, PER_PAGE, (page - 1) * PER_PAGE]).fetchall()
    except sqlite3.OperationalError as e:
        raise QueryError(_explain(str(e), syntax)) from None
    hits = [{"key": r[0], "year": r[1], "title": r[2], "authors": r[3], "venue": r[4],
             "genera": r[5], "clades": r[6], "tool": r[7], "translated": r[8] == "1",
             "snippet": snippet_html(r[9])} for r in rows]
    return {"total": total, "page": page, "per_page": PER_PAGE, "hits": hits}


def _explain(message: str, syntax: bool) -> str:
    if not syntax:
        return f"The search failed: {message}."
    if message.startswith("no such column"):
        return (f"Search syntax: {message}. A word followed by a colon names a column "
                "(title, authors, journal, genera, clades, body); quote it to search for it.")
    return f"Search syntax: {message}. Turn off search syntax to search for the words as typed."
