"""
query_to_indent.py

Parses a Scopus "advanced search" style boolean query -- field codes such
as TITLE(...), TITLE-ABS-KEY(...), AU-ID(...); the boolean operators OR,
AND, AND NOT; the proximity operators W/n and PRE/n; the date/number
comparison operators BEF, AFT, IS (e.g. PUBYEAR AFT 1994); loose phrases
in "double quotes", exact phrases in {curly braces}; and wildcarded terms
such as climat* -- and rewrites it as an indented plain-text file: one
term per line, with every parenthesis from the original query kept in
the output and the nesting of sub-queries expressed as indentation, and
the operator (AND, OR, AND NOT, W/3, ...) placed at the start of the line
it introduces, directly in front of the opening parenthesis where
relevant.

The recognised operators and field codes follow Elsevier's "How can I
best use the Advanced search?" support page (a_id/11365), last updated
August 15, 2024.

Requires the 'lark' parsing library:
    pip install lark

Usage:
    python query_to_indent.py input.txt -o output.txt
    python query_to_indent.py input.txt          # prints to stdout
    cat input.txt | python query_to_indent.py     # reads stdin
    python query_to_indent.py input.txt --strict  # error on unknown field codes
"""

from __future__ import annotations

import sys
import typing
from typing import Annotated

import typer
from lark import Lark, Transformer

if typing.TYPE_CHECKING:
    from pathlib import Path

# --------------------------------------------------------------------------
# Scopus field codes (per the documentation)
# --------------------------------------------------------------------------
#
# This is used only for optional validation (see --strict); the grammar
# itself accepts any identifier as a field name so that new/undocumented
# field codes still parse.

FIELD_CODES = {
    # Textual Content
    "ABS",
    "ALL",
    "TITLE",
    "TITLE-ABS",
    "TITLE-ABS-KEY",
    "TITLE-ABS-KEY-AUTH",
    # Affiliations
    "AFFIL",
    "AFFILCITY",
    "AFFILCOUNTRY",
    "AF-ID",
    "AFFILORG",
    # Authors
    "AUTH",
    "AUTHCOLLAB",
    "AUTHFIRST",
    "AU-ID",
    "AUTHLASTNAME",
    "AUTHOR-NAME",
    "ORCID",
    # Biological Entities
    "SEQBANK",
    "SEQNUMBER",
    # Chemical Entities
    "CASREGNUMBER",
    "CHEM",
    "CHEMNAME",
    # Conferences
    "CONF",
    "CONFLOC",
    "CONFNAME",
    "CONFSPONSORS",
    # Document
    "OA",
    "INDEX",
    "DOCTYPE",
    "DOI",
    "EID",
    "FIRSTAUTH",
    "PAGEFIRST",
    "LANGUAGE",
    "PAGELAST",
    "LOAD-DATE",
    "PAGES",
    # Editors
    "EDITOR",
    "EDFIRST",
    "EDLASTNAME",
    # Funding
    "FUND-ALL",
    "FUND-SPONSOR",
    "FUND-NO",
    "FUND-ACR",
    # Keywords
    "AUTHKEY",
    "INDEXTERMS",
    "KEY",
    "MANUFACTURER",
    "TRADENAME",
    # Publication
    "ARTNUM",
    "BOOKPUB",
    "CODEN",
    "PUBDATETXT",
    "EISSN",
    "EXACTSRCTITLE",
    "ISBN",
    "ISSN",
    "ISSNP",
    "PMID",
    "ISSUE",
    "VOLUME",
    "SRCID",
    "SRCTITLE",
    "SRCTYPE",
    "PUBYEAR",
    # References
    "REF",
    "REFARTNUM",
    "REFAUTH",
    "REFPAGEFIRST",
    "REFPAGE",
    "REFPUBYEAR",
    "REFSRCTITLE",
    "REFTITLE",
    "WEBSITE",
    # Subject areas
    "SUBJAREA",
}

# Fields documented as supporting the BEF / AFT / IS comparison operators
# (used without parentheses, e.g. "PUBYEAR AFT 1994").
DATE_COMPARE_FIELDS = {"PUBYEAR", "REFPUBYEAR", "LOAD-DATE"}

# --------------------------------------------------------------------------
# Grammar
# --------------------------------------------------------------------------
#
# - IDENT covers bare terms, including wildcards (*, ?) and internal
#   hyphens (climat*, wom?n, CO2, N2O, greenhouse-gas), as well as field
#   codes (TITLE, TITLE-ABS-KEY, AU-ID, ...) -- any identifier is accepted
#   as a field name so undocumented/future field codes still parse; the
#   FIELD_CODES set above is only used for optional --strict validation.
# - ESCAPED_STRING covers loose phrases in double quotes ("nitrous
#   oxide"); CURLY_STRING covers exact phrases in curly braces
#   ({heart attack}).
# - Boolean operators: OR, AND, and the compound exclusion operator
#   AND NOT (Scopus does not treat a bare NOT as an operator).
# - Proximity operators: any <letters>/<number> token, which covers both
#   W/n (unordered) and PRE/n (ordered), e.g. W/3, W/15, PRE/5.
# - Date/number comparison, used without parentheses, e.g.
#   PUBYEAR AFT 1994, LOAD-DATE AFT 20190107, REFPUBYEAR IS 1994.
# - A field call is a bare identifier immediately followed by "(" ... ")",
#   e.g. TITLE-ABS-KEY( ... ).
# - A plain "(" expr ")" group becomes its own "group" node (rather than
#   being discarded), so that every pair of parentheses present in the
#   original query -- including redundant/doubled ones such as
#   "( ( a OR b ) )" -- is reproduced in the output.

GRAMMAR = r"""
    start: expr

    ?expr: item (op item)*

    op: AND NOT   -> op_and_not
      | AND       -> op_and
      | OR        -> op_or
      | PROX      -> op_prox

    ?item: field_call
         | field_compare
         | group
         | CURLY_STRING
         | ESCAPED_STRING
         | IDENT

    field_call: IDENT "(" expr ")"
    field_compare: IDENT DATEOP IDENT
    group: "(" expr ")"

    AND.2: "AND"
    OR.2: "OR"
    NOT.2: "NOT"
    DATEOP.2: "BEF" | "AFT" | "IS"
    PROX.2: /[A-Za-z]+\/\d+/

    IDENT: /[A-Za-z0-9][A-Za-z0-9_*?-]*/
    CURLY_STRING: /\{[^{}]*\}/

    %import common.ESCAPED_STRING
    %import common.WS
    %ignore WS
"""


class FieldCall:
    """Represents FIELD( sub-expression ), e.g. TITLE( ... )."""

    __slots__ = ("expr", "name")

    def __init__(
        self,
        name: str,
        expr: list[tuple[str | None, object]] | object,
    ) -> None:
        self.name = name
        self.expr = expr

    def __repr__(self) -> str:
        return f"FieldCall({self.name!r}, {self.expr!r})"


class Group:
    """Represents a parenthesised sub-expression: ( sub-expression )."""

    __slots__ = ("expr",)

    def __init__(self, expr: list[tuple[str | None, object]] | object) -> None:
        self.expr = expr

    def __repr__(self) -> str:
        return f"Group({self.expr!r})"


class DateCompare:
    """Represents FIELD OP VALUE without parentheses, e.g. PUBYEAR AFT 1994."""

    __slots__ = ("field", "op", "value")

    def __init__(self, field: str, op: str, value: str) -> None:
        self.field = field
        self.op = op
        self.value = value

    def __repr__(self) -> str:
        return f"DateCompare({self.field!r}, {self.op!r}, {self.value!r})"


class UnknownFieldCodeError(ValueError):
    """Raised in --strict mode when a field code isn't in FIELD_CODES."""


class QueryTransformer(Transformer):
    """
    Turns the Lark parse tree into a small AST made of plain Python
    objects:

    - a leaf term is a Token (str-like) -- an IDENT, an ESCAPED_STRING
      ("loose phrase"), or a CURLY_STRING ({exact phrase})
    - a FieldCall(name, expr) node represents FIELD( expr )
    - a DateCompare(field, op, value) node represents FIELD OP VALUE
      without parentheses, e.g. PUBYEAR AFT 1994
    - a Group(expr) node represents a parenthesised sub-expression ( expr )
      that is not attached to a field name
    - a list of (operator_or_None, operand) pairs represents a sequence of
      terms/sub-expressions joined by OR / AND / AND NOT / W/n / PRE/n
      etc. The first pair always has operator None. This list only ever
      appears as the "expr" carried inside a FieldCall or a Group, or as
      the whole document when the query has no enclosing parentheses at
      all.
    """

    def __init__(self, strict: bool = False) -> None:
        super().__init__()
        self.strict = strict

    def expr(self, children: list[object | str]) -> list[tuple[str | None, object]]:
        # children alternates: item, op, item, op, item, ...
        pairs: list[tuple[str | None, object]] = [(None, children[0])]
        i = 1
        while i < len(children):
            operator: str = str(children[i])
            operand: object = children[i + 1]
            pairs.append((operator, operand))
            i += 2
        return pairs

    # -- operators --------------------------------------------------------
    # Each branch of the `op` rule is transformed into a plain operator
    # string, so `expr` above can just str() whatever it finds there.

    @typing.override
    def op_and_not(self, children: object | None) -> str:
        return "AND NOT"

    @typing.override
    def op_and(self, children: object | None) -> str:
        return "AND"

    @typing.override
    def op_or(self, children: object | None) -> str:
        return "OR"

    def op_prox(self, children: list[object]) -> str:
        return str(children[0])

    # -- items --------------------------------------------------------

    def field_call(self, children: list[object]) -> FieldCall:
        name_token, inner_expr = children
        name = str(name_token)
        self._check_field_code(name)
        return FieldCall(name, inner_expr)

    def field_compare(self, children: list[object]) -> DateCompare:
        field_token, op_token, value_token = children
        field = str(field_token)
        self._check_field_code(field)
        return DateCompare(field, str(op_token), str(value_token))

    def group(self, children: list[object]) -> Group:
        (inner_expr,) = children
        return Group(inner_expr)

    def start(self, children: list[object]) -> object:
        return children[0]

    def _check_field_code(self, name: str) -> None:
        if name.upper() not in FIELD_CODES:
            message = (
                f"'{name}' is not a documented Scopus field code "
                f"(see FIELD_CODES in this script)."
            )
            if self.strict:
                raise UnknownFieldCodeError(message)
            print(f"Warning: {message}", file=sys.stderr)  # noqa: T201


_parser = Lark(GRAMMAR, parser="lalr", maybe_placeholders=False)


def parse_query(
    text: str,
    strict: bool = False,
) -> list[tuple[str | None, object]] | object:
    """Parse a query string and return the AST root (see QueryTransformer)."""
    tree = _parser.parse(text)
    return QueryTransformer(strict=strict).transform(tree)


# --------------------------------------------------------------------------
# AST -> indented text
# --------------------------------------------------------------------------

INDENT_UNIT = "  "  # two spaces per nesting level


def _print_operand(
    op: str | None,
    operand: object,
    level: int,
    lines: list[str],
) -> None:
    """Print a single (operator, operand) pair at the given indent level."""
    pad = INDENT_UNIT * level
    prefix = f"{op} " if op else ""

    if isinstance(operand, FieldCall):
        # FIELD ( ... ) : opening parenthesis stays attached to the field
        # name (and to the operator, if any), closing parenthesis gets its
        # own row lined up with the field name.
        lines.append(f"{pad}{prefix}{operand.name} (")
        _print_body(operand.expr, level + 1, lines)
        lines.append(f"{pad})")

    elif isinstance(operand, Group):
        # ( ... ) : opening parenthesis on its own row (preceded by the
        # operator, if any), closing parenthesis lined up with it.
        lines.append(f"{pad}{prefix}(")
        _print_body(operand.expr, level + 1, lines)
        lines.append(f"{pad})")

    elif isinstance(operand, DateCompare):
        # FIELD OP VALUE (no parentheses), e.g. PUBYEAR AFT 1994.
        lines.append(f"{pad}{prefix}{operand.field} {operand.op} {operand.value}")

    else:
        # Leaf term: an IDENT or ESCAPED_STRING token.
        lines.append(f"{pad}{prefix}{operand}")


def _print_body(node: list[tuple[str, object]], level: int, lines: list[str]) -> None:
    """
    Print the contents of a FieldCall/Group/top-level expression. `node`
    is either a list of (operator, operand) pairs, or -- when the
    sub-expression contained only a single, operator-less item -- that
    item directly (a leaf, a FieldCall, or a Group).
    """
    if isinstance(node, list):
        for op, operand in node:
            _print_operand(op, operand, level, lines)
    else:
        _print_operand(None, node, level, lines)


def render(root: list[tuple[str | None, object]] | object) -> str:
    """Render an AST root (from parse_query) to an indented string."""
    lines = []
    _print_body(root, 0, lines)
    return "\n".join(lines)


def query_to_indented_text(query_text: str, strict: bool = False) -> str:
    return render(parse_query(query_text, strict=strict))


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------


def reformat_query(
    source: Annotated[Path, typer.Argument(help="input file")],
    target: Annotated[
        Path | None, typer.Option("-o", help="output file (default: stdout)")
    ] = None,
    strict: Annotated[
        bool,
        typer.Option(
            help="error out on field codes not in the documented FIELD_CODES set"
        ),
    ] = True,
) -> None:
    with source.open(encoding="utf-8") as f:
        query_text = f.read()

    result = query_to_indented_text(query_text, strict=strict)

    if target:
        with target.open("w", encoding="utf-8") as f:
            f.write(result + "\n")
    else:
        print(result)  # noqa: T201


if __name__ == "__main__":
    typer.run(reformat_query)
