"""Full-text search terms shared by fact and namespace search.

The 'simple' configuration keeps every word as written (no stemming, no stop words), so
Vietnamese and English text match the same way. Stored columns split keys and paths on
'.' and '/' (see the search_tsv columns); queries are split the same way here.

Ranking: rows that contain every term come first (AND), then rows ranked by how many
terms they contain (OR). Each term also matches as a prefix: "deploy" finds "deployment".
"""

from __future__ import annotations

import re

from sqlalchemy import func, literal_column, or_
from sqlalchemy.sql.elements import ColumnElement

from src.models import Namespace

MAX_TERMS = 10
_TERM = re.compile(r"[^\W_]+")
_SIMPLE = literal_column("'simple'::regconfig")


def search_terms(query: str) -> list[str]:
    """Lower-cased words of the query: letters and digits only, so safe inside a tsquery."""
    terms = list(dict.fromkeys(_TERM.findall(query.lower())))[:MAX_TERMS]
    if not terms:
        raise ValueError("query has no searchable words")
    return terms


def all_terms(terms: list[str]) -> ColumnElement:
    return func.to_tsquery(_SIMPLE, " & ".join(f"{t}:*" for t in terms))


def any_term(terms: list[str]) -> ColumnElement:
    return func.to_tsquery(_SIMPLE, " | ".join(f"{t}:*" for t in terms))


def rank(tsv: ColumnElement, terms: list[str]) -> ColumnElement:
    """0..1, higher when more terms match in weightier fields (A title/key > B > C)."""
    return func.ts_rank_cd(tsv, any_term(terms), 32)


def snippet(column: ColumnElement, terms: list[str]) -> ColumnElement:
    """The passage of column around the matched terms, plain text, about 30 words."""
    return func.ts_headline(
        _SIMPLE, column, any_term(terms),
        'MaxWords=30, MinWords=12, MaxFragments=1, StartSel="", StopSel=""',
    )


def namespace_matches(terms: list[str]) -> ColumnElement:
    """A namespace matches on any term in its name or summary, or on an exact keyword."""
    return or_(
        Namespace.search_tsv.op("@@")(any_term(terms)),
        Namespace.keywords.overlap(terms),
    )
