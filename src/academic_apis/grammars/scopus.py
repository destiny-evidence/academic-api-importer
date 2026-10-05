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

import difflib
import re
from pathlib import Path
from typing import Annotated

import typer
from lark import Lark, Token, Transformer
from lark.exceptions import (
    UnexpectedCharacters,
    UnexpectedEOF,
    UnexpectedInput,
    UnexpectedToken,
)

GRAMMAR = Path(__file__).with_name("scopus.lark").read_text(encoding="utf-8")

# A sequence of (operator_or_None, operand) pairs, or a single operand.
Expr = list[tuple[str | None, object]] | object


class FieldCall:
    """Represents FIELD( sub-expression ), e.g. TITLE( ... ) or REF( ... )."""

    __slots__ = ("expr", "name")

    def __init__(self, name: str, expr: Expr) -> None:
        self.name = name
        self.expr = expr

    def __repr__(self) -> str:
        return f"FieldCall({self.name!r}, {self.expr!r})"


class Group:
    """Represents a parenthesised sub-expression: ( sub-expression )."""

    __slots__ = ("expr",)

    def __init__(self, expr: Expr) -> None:
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


def _as_pairs(node: Expr) -> list[tuple[str | None, object]]:
    """Wrap a single operand as a one-element pair list."""
    return node if isinstance(node, list) else [(None, node)]


def _join(left: Expr, op: str | None, right: Expr) -> list[tuple[str | None, object]]:
    """
    Flatten a binary node into one infix sequence. This is lossless:
    every bracket in the source is kept as a Group, so re-reading the
    flat sequence with Scopus precedence (OR > AND > AND NOT) gives the
    same structure.
    """
    right_pairs = _as_pairs(right)
    _, first = right_pairs[0]
    return [*_as_pairs(left), (op, first), *right_pairs[1:]]


class QueryTransformer(Transformer[Token, object]):
    """
    Turns the Lark parse tree into a small AST of plain Python objects:

    - a leaf term is a Token: TERM, ESCAPED_STRING ("loose phrase") or
      CURLY_STRING ({exact phrase})
    - FieldCall(name, expr) for FIELD( expr ), including REF( ... ) and
      the reference sub-fields
    - DateCompare(field, op, value) for PUBYEAR / REFPUBYEAR / LOAD-DATE
    - Group(expr) for a parenthesised sub-expression
    - a list of (operator, operand) pairs for terms joined by
      OR / AND / AND NOT / W/n / PRE/n. The first pair has operator None.
      Operator None on a later pair means implicit AND (adjacent terms,
      e.g. "prion disease"). A top-level stand-alone NOT is returned as
      the one-element list [("NOT", operand)].
    """

    # -- binary operators (grammar aliases) --------------------------------

    def and_not(self, children: list[object]) -> list[tuple[str | None, object]]:
        left, right = children
        return _join(left, "AND NOT", right)

    def and_(self, children: list[object]) -> list[tuple[str | None, object]]:
        left, right = children
        return _join(left, "AND", right)

    def adj(self, children: list[object]) -> list[tuple[str | None, object]]:
        left, right = children
        return _join(left, None, right)

    def or_(self, children: list[object]) -> list[tuple[str | None, object]]:
        left, right = children
        return _join(left, "OR", right)

    def prox(self, children: list[object]) -> list[tuple[str | None, object]]:
        left, prox_token, right = children
        return _join(left, str(prox_token).upper(), right)

    # -- field calls -------------------------------------------------------

    def field_call(self, children: list[object]) -> FieldCall:
        name_token, inner_expr = children
        return FieldCall(str(name_token).upper(), inner_expr)

    def ref_sub_call(self, children: list[object]) -> FieldCall:
        name_token, inner_expr = children
        return FieldCall(str(name_token).upper(), inner_expr)

    def ref_call(self, children: list[object]) -> FieldCall:
        # The REF keyword is a filtered terminal (_REF), so only the body arrives.
        (inner_expr,) = children
        return FieldCall("REF", inner_expr)

    # -- comparisons (field keyword is filtered, so only op and value arrive) --

    def pubyear_cmp(self, children: list[object]) -> DateCompare:
        op_token, value_token = children
        return DateCompare("PUBYEAR", str(op_token).upper(), str(value_token))

    def refpubyear_cmp(self, children: list[object]) -> DateCompare:
        op_token, value_token = children
        return DateCompare("REFPUBYEAR", str(op_token).upper(), str(value_token))

    def loaddate_cmp(self, children: list[object]) -> DateCompare:
        op_token, value_token = children
        return DateCompare("LOAD-DATE", str(op_token).upper(), str(value_token))

    # -- structure ---------------------------------------------------------

    def group(self, children: list[object]) -> Group:
        (inner_expr,) = children
        return Group(inner_expr)

    def negation(self, children: list[object]) -> list[tuple[str | None, object]]:
        (operand,) = children
        return [("NOT", operand)]

    def start(self, children: list[object]) -> object:
        return children[0]


_parser = Lark(GRAMMAR, parser="lalr")


# --------------------------------------------------------------------------
# Readable syntax errors
# --------------------------------------------------------------------------


def _names_in_terminal(terminal: str) -> list[str]:
    """Read the field names out of a compiled terminal such as FIELD."""
    pattern = _parser.get_terminal(terminal).pattern.value
    raw = re.findall(r"\(\?i:((?:[^()\\]|\\.)*)\)", pattern)
    return sorted(re.sub(r"\\(.)", r"\1", name) for name in raw)


FIELD_NAMES = _names_in_terminal("FIELD")
REF_SUB_NAMES = _names_in_terminal("REF_SUBFIELD")
COMPARE_NAMES = ["PUBYEAR", "REFPUBYEAR", "LOAD-DATE"]

_COMPARE_OPS = {"BEF": "BEF", "AFT": "AFT", "IS": "IS", "LT": "<", "GT": ">", "EQ": "="}

# Order in which expectations are listed; several terminals may share a text.
_DESCRIPTIONS: list[tuple[str, str]] = [
    ("TERM", "a search term, e.g. heart or child*"),
    ("ESCAPED_STRING", 'a loose phrase, e.g. "heart attack"'),
    ("CURLY_STRING", "an exact phrase, e.g. {heart attack}"),
    ("FIELD", "a field code, e.g. TITLE-ABS-KEY(...), AUTH(...), AFFIL(...)"),
    ("_REF", "a reference search, e.g. REF(REFAUTH(darwin) AND REFPUBYEAR IS 1859)"),
    (
        "REF_SUBFIELD",
        "a reference sub-field: " + ", ".join(f"{n}(...)" for n in REF_SUB_NAMES),
    ),
    ("_PUBYEAR", "a publication-year filter, e.g. PUBYEAR AFT 2019"),
    ("_REFPUBYEAR", "a reference-year filter, e.g. REFPUBYEAR IS 1859"),
    ("_LOAD_DATE", "a load-date filter, e.g. LOAD-DATE AFT 20190107"),
    ("_NOT", "NOT (only at the very start of the query)"),
    ("LPAR", '"(" to open a group'),
    ("_AND", "a Boolean operator: AND, OR, AND NOT"),
    ("_OR", "a Boolean operator: AND, OR, AND NOT"),
    ("_AND_NOT", "a Boolean operator: AND, OR, AND NOT"),
    ("PROX", "a proximity operator, e.g. W/3 or PRE/2"),
    ("YEAR", "a four-digit year, e.g. 1994"),
    ("DATE", "a date written YYYYMMDD, e.g. 20190107"),
    ("RPAR", '")" to close the group'),
    ("$END", "the end of the query"),
]


class QuerySyntaxError(ValueError):
    """Raised by parse_query with a readable description of the problem."""

    def __init__(self, message: str, line: int | None, column: int | None) -> None:
        super().__init__(message)
        self.line = line
        self.column = column


def _describe_expected(expected: set[str]) -> list[str]:
    texts: list[str] = []
    ops = [sym for name, sym in _COMPARE_OPS.items() if name in expected]
    if ops:
        texts.append("a comparison operator: " + ", ".join(ops))
    for name, text in _DESCRIPTIONS:
        if name in expected and text not in texts:
            texts.append(text)
    known = {name for name, _ in _DESCRIPTIONS} | set(_COMPARE_OPS)
    texts.extend(sorted(name for name in expected if name not in known))
    return texts


def _hint(text: str, pos: int, expected: set[str]) -> str | None:
    """Explain the most common mistakes: misspelt or misplaced field codes."""
    word_match = re.compile(r"[A-Za-z][A-Za-z-]*").match(text, pos)
    if word_match is None:
        if "RPAR" in expected and text.count("(") > text.count(")"):
            return 'There may be a missing ")".'
        return None

    word = word_match.group()
    upper = word.upper()
    allowed: list[str] = []
    if "FIELD" in expected:
        allowed += FIELD_NAMES
    if "_REF" in expected:
        allowed.append("REF")
    if "REF_SUBFIELD" in expected:
        allowed += REF_SUB_NAMES
    if "_PUBYEAR" in expected:
        allowed.append("PUBYEAR")
    if "_REFPUBYEAR" in expected:
        allowed.append("REFPUBYEAR")
    if "_LOAD_DATE" in expected:
        allowed.append("LOAD-DATE")

    all_known = set(FIELD_NAMES) | set(REF_SUB_NAMES) | {"REF"} | set(COMPARE_NAMES)
    if upper in all_known and upper not in allowed:
        if upper in COMPARE_NAMES:
            return f"{upper} comparisons are not allowed here."
        return (
            f"{upper}(...) is not allowed here. Field codes cannot be nested; only "
            f"REF(...) may contain {', '.join(REF_SUB_NAMES)} and REFPUBYEAR."
        )

    followed_by_paren = re.match(r"\s*\(", text[word_match.end() :]) is not None
    candidates = difflib.get_close_matches(upper, allowed, n=3, cutoff=0.6)
    if candidates:
        return f'"{word}" is not a valid code here. Did you mean {" or ".join(candidates)}?'
    if followed_by_paren:
        if allowed:
            return f'"{word}" is not a known field code.'
        return f'"{word}" is followed directly by "(" but is not a field code allowed here.'
    if "$END" in expected or "RPAR" in expected:
        return "An operator (AND, OR, AND NOT, W/n, PRE/n) may be missing before this word."
    return None


def _to_syntax_error(err: UnexpectedInput, text: str) -> QuerySyntaxError:
    if isinstance(err, UnexpectedCharacters):
        expected = set(err.allowed or ())
        pos = int(err.pos_in_stream or -1)
    elif isinstance(err, UnexpectedToken):
        expected = set(err.expected or ())
        pos = int(err.token.start_pos or -1) if err.token.type != "$END" else len(text)
    else:  # UnexpectedEOF
        expected = set(getattr(err, "expected", ()) or ())
        pos = len(text)

    line: int | None = getattr(err, "line", None)
    column: int | None = getattr(err, "column", None)
    if line is None or line < 0:
        line = text.count("\n", 0, pos) + 1
        column = pos - (text.rfind("\n", 0, pos) + 1) + 1

    lines: list[str] = []
    if pos >= len(text):
        lines.append("The query ended too early.")
    else:
        lines.append(f"Syntax error at line {line}, column {column}:")
    lines.append("")
    try:
        lines.append(err.get_context(text, span=40).rstrip("\n"))
    except Exception:  # some Lark versions fail at end of input  # noqa: BLE001
        lines.append(text[-40:])
        lines.append(" " * min(len(text), 40) + "^")

    hint = _hint(text, pos, expected)
    if hint:
        lines.extend(["", hint])

    described = _describe_expected(expected)
    if described:
        lines.extend(["", "Expected one of:"])
        lines.extend(f"  - {item}" for item in described)

    return QuerySyntaxError("\n".join(lines), line, column)


def parse_query(text: str) -> Expr:
    """Parse a query string and return the AST root (see QueryTransformer)."""
    try:
        tree = _parser.parse(text)
    except (UnexpectedCharacters, UnexpectedToken, UnexpectedEOF) as err:
        raise _to_syntax_error(err, text) from None
    return QueryTransformer().transform(tree)


# --------------------------------------------------------------------------
# AST -> indented text
# --------------------------------------------------------------------------

INDENT_UNIT = "  "  # two spaces per nesting level


def _print_operand(
    op: str | None,
    operand: object,
    level: int,
    op_width: int,
    lines: list[str],
) -> None:
    """
    Print one (operator, operand) pair. Single-line operands are aligned
    in a column after the widest operator of their list, so the first
    operand lines up with the ones that follow an operator. Bracketed
    operands keep the operator directly before "(".
    """
    pad = INDENT_UNIT * level

    if isinstance(operand, (FieldCall, Group)):
        prefix = f"{op} " if op else ""
        head = f"{operand.name} (" if isinstance(operand, FieldCall) else "("
        lines.append(f"{pad}{prefix}{head}")
        _print_body(operand.expr, level + 1, lines)
        lines.append(f"{pad})")
        return

    prefix = (op or "").ljust(op_width) + " " if op_width else ""
    if isinstance(operand, DateCompare):
        lines.append(f"{pad}{prefix}{operand.field} {operand.op} {operand.value}")
    else:
        # Leaf term: TERM, ESCAPED_STRING or CURLY_STRING token.
        lines.append(f"{pad}{prefix}{operand}")


def _print_body(node: Expr, level: int, lines: list[str]) -> None:
    """Print a pair list, or a single operator-less operand."""
    pairs = _as_pairs(node)
    op_width = max((len(op) for op, _ in pairs if op), default=0)
    for op, operand in pairs:
        _print_operand(op, operand, level, op_width, lines)


def render(root: Expr) -> str:
    """Render an AST root (from parse_query) to an indented string."""
    lines: list[str] = []
    _print_body(root, 0, lines)
    return "\n".join(lines)


def query_to_indented_text(query_text: str) -> str:
    return render(parse_query(query_text))


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------


def reformat_query(
    source: Annotated[Path, typer.Argument(help="input file")],
    target: Annotated[
        Path | None, typer.Option("-o", help="output file (default: stdout)")
    ] = None,
) -> None:
    with source.open(encoding="utf-8") as f:
        query_text = f.read()

    result = query_to_indented_text(query_text)

    if target:
        with target.open("w", encoding="utf-8") as f:
            f.write(result + "\n")
    else:
        print(result)  # noqa: T201


if __name__ == "__main__":
    typer.run(reformat_query)
