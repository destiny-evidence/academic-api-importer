"""
Tests for ``query_to_indented_text`` from ``academic_apis.grammars.scopus``.

Every expected block is written as a ``|``-prefixed string (see ``_expected``), so the
significant leading spaces of the rendered output survive inside the source's own
indentation. The rules covered here:

- one line per term; every ``(...)`` of the source is kept, nesting is indentation
- a field code renders as ``NAME (``, the body one level deeper, then ``)``
- the operator (AND, OR, AND NOT, W/3, ...) introduces the line it belongs to; before
  an opening parenthesis it sits directly in front of it (``AND (``)
- single-line operands are aligned behind the widest operator of their list, the first
  (operator-less) operand included
- field names, Boolean keywords and proximity operators come out upper-case
- invalid queries raise ``QuerySyntaxError``

Coverage by behaviour:
- Field calls & term shapes (7): simple calls, hyphenated/multi-word field codes, wildcards, "loose phrases", {exact phrases}, punctuated terms
- Boolean operators (4): AND, OR, AND NOT placed at the start of the line they introduce, incl. chains mixing field calls and comparisons
- Stand-alone NOT (3): before a field, a group, and a comparison
- Proximity & implicit AND (4): W/n, PRE/n, chained proximity, adjacent terms
- Date comparisons (9): PUBYEAR with AFT/BEF/IS/</>/=, REFPUBYEAR IS, LOAD-DATE AFT, and comparisons inside a group
- Reference searches (4): REF(...) with sub-fields, REFPUBYEAR, AND NOT, nested groups
- Parenthesised groups (4): doubled parentheses, group operands, group inside a field
- Column alignment (5): operands aligned after the widest operator of their list (e.g. AND NOT padding), including operator-less and implicit-AND first operands
- Normalisation (5): lowercase field names, operators, comparisons and proximity codes come out upper-cased; surrounding/newline whitespace ignored
- Error propagation (12): empty input, unbalanced parentheses, dangling operator, unknown/nested field codes, bare terms, two-digit year, NOT over a conjunction — all raise QuerySyntaxError
"""

from __future__ import annotations

import textwrap

import pytest

from academic_apis.grammars.scopus import QuerySyntaxError, query_to_indented_text


def _expected(block: str) -> str:
    """Read an expected output block: dedented, with each line's ``|`` removed."""
    return "\n".join(
        line[1:] for line in textwrap.dedent(block).strip("\n").splitlines()
    )


# --------------------------------------------------------------------------------------
# simple field calls and the shapes a term may have
# --------------------------------------------------------------------------------------

FIELD_CASES: dict[str, tuple[str, str]] = {
    "single-term": (
        "TITLE(heart)",
        """
        |TITLE (
        |  heart
        |)""",
    ),
    "hyphenated-field": (
        "AU-ID(12345678900)",
        """
        |AU-ID (
        |  12345678900
        |)""",
    ),
    "multi-word-field": (
        "TITLE-ABS-KEY(climate)",
        """
        |TITLE-ABS-KEY (
        |  climate
        |)""",
    ),
    "wildcard": (
        "TITLE(climat*)",
        """
        |TITLE (
        |  climat*
        |)""",
    ),
    "loose-phrase": (
        'TITLE("heart attack")',
        """
        |TITLE (
        |  "heart attack"
        |)""",
    ),
    "exact-phrase": (
        "TITLE({heart attack})",
        """
        |TITLE (
        |  {heart attack}
        |)""",
    ),
    "punctuated-term": (
        "TITLE(10.1000/abc)",
        """
        |TITLE (
        |  10.1000/abc
        |)""",
    ),
}


@pytest.mark.parametrize(("query", "expected"), FIELD_CASES.values(), ids=FIELD_CASES)
def test_renders_a_field_call_with_one_term_per_line(query: str, expected: str) -> None:
    assert query_to_indented_text(query) == _expected(expected)


# --------------------------------------------------------------------------------------
# Boolean operators between field calls at the top level
# --------------------------------------------------------------------------------------

BOOLEAN_CASES: dict[str, tuple[str, str]] = {
    "and": (
        "TITLE(heart) AND TITLE(attack)",
        """
        |TITLE (
        |  heart
        |)
        |AND TITLE (
        |  attack
        |)""",
    ),
    "or": (
        "TITLE(heart) OR TITLE(attack)",
        """
        |TITLE (
        |  heart
        |)
        |OR TITLE (
        |  attack
        |)""",
    ),
    "and-not": (
        "TITLE(heart) AND NOT TITLE(attack)",
        """
        |TITLE (
        |  heart
        |)
        |AND NOT TITLE (
        |  attack
        |)""",
    ),
    "chain-with-comparison": (
        "TITLE-ABS-KEY(climate change) AND AUTHLASTNAME(smith) AND PUBYEAR AFT 2019",
        """
        |TITLE-ABS-KEY (
        |  climate
        |  change
        |)
        |AND AUTHLASTNAME (
        |  smith
        |)
        |AND PUBYEAR AFT 2019""",
    ),
}


@pytest.mark.parametrize(
    ("query", "expected"), BOOLEAN_CASES.values(), ids=BOOLEAN_CASES
)
def test_renders_boolean_operators_in_front_of_their_operand(
    query: str, expected: str
) -> None:
    assert query_to_indented_text(query) == _expected(expected)


# --------------------------------------------------------------------------------------
# stand-alone NOT (top level only, one operand)
# --------------------------------------------------------------------------------------

NEGATION_CASES: dict[str, tuple[str, str]] = {
    "not-field": (
        "NOT TITLE(heart)",
        """
        |NOT TITLE (
        |  heart
        |)""",
    ),
    "not-group": (
        "NOT (TITLE(a) OR TITLE(b))",
        """
        |NOT (
        |  TITLE (
        |    a
        |  )
        |  OR TITLE (
        |    b
        |  )
        |)""",
    ),
    "not-comparison": (
        "NOT PUBYEAR AFT 1994",
        "|NOT PUBYEAR AFT 1994",
    ),
}


@pytest.mark.parametrize(
    ("query", "expected"), NEGATION_CASES.values(), ids=NEGATION_CASES
)
def test_renders_a_leading_not(query: str, expected: str) -> None:
    assert query_to_indented_text(query) == _expected(expected)


# --------------------------------------------------------------------------------------
# proximity operators and implicit AND (adjacent terms) inside a field
# --------------------------------------------------------------------------------------

PROXIMITY_CASES: dict[str, tuple[str, str]] = {
    "w-n": (
        "TITLE(climate W/3 change)",
        """
        |TITLE (
        |      climate
        |  W/3 change
        |)""",
    ),
    "pre-n": (
        "TITLE(change PRE/2 climate)",
        """
        |TITLE (
        |        change
        |  PRE/2 climate
        |)""",
    ),
    "chained": (
        "TITLE(a W/3 b PRE/2 c)",
        """
        |TITLE (
        |        a
        |  W/3   b
        |  PRE/2 c
        |)""",
    ),
    "implicit-and": (
        "TITLE(prion disease)",
        """
        |TITLE (
        |  prion
        |  disease
        |)""",
    ),
}


@pytest.mark.parametrize(
    ("query", "expected"), PROXIMITY_CASES.values(), ids=PROXIMITY_CASES
)
def test_renders_proximity_and_implicit_and(query: str, expected: str) -> None:
    assert query_to_indented_text(query) == _expected(expected)


# --------------------------------------------------------------------------------------
# PUBYEAR / REFPUBYEAR / LOAD-DATE comparisons
# --------------------------------------------------------------------------------------

COMPARISON_CASES: dict[str, tuple[str, str]] = {
    "pubyear-aft": ("PUBYEAR AFT 1994", "|PUBYEAR AFT 1994"),
    "pubyear-bef": ("PUBYEAR BEF 2000", "|PUBYEAR BEF 2000"),
    "pubyear-is": ("PUBYEAR IS 2000", "|PUBYEAR IS 2000"),
    "pubyear-lt": ("PUBYEAR < 2000", "|PUBYEAR < 2000"),
    "pubyear-gt": ("PUBYEAR > 2000", "|PUBYEAR > 2000"),
    "pubyear-eq": ("PUBYEAR = 2000", "|PUBYEAR = 2000"),
    "refpubyear-is": ("REFPUBYEAR IS 1859", "|REFPUBYEAR IS 1859"),
    "loaddate-aft": ("LOAD-DATE AFT 20190107", "|LOAD-DATE AFT 20190107"),
    "joined-by-or": (
        "AUTH(a) AND (PUBYEAR AFT 2000 OR LOAD-DATE BEF 20190107)",
        """
        |AUTH (
        |  a
        |)
        |AND (
        |     PUBYEAR AFT 2000
        |  OR LOAD-DATE BEF 20190107
        |)""",
    ),
}


@pytest.mark.parametrize(
    ("query", "expected"), COMPARISON_CASES.values(), ids=COMPARISON_CASES
)
def test_renders_date_comparisons(query: str, expected: str) -> None:
    assert query_to_indented_text(query) == _expected(expected)


# --------------------------------------------------------------------------------------
# REF(...) with its sub-fields and REFPUBYEAR
# --------------------------------------------------------------------------------------

REF_CASES: dict[str, tuple[str, str]] = {
    "sub-field-and-year": (
        "REF(REFAUTH(darwin) AND REFPUBYEAR IS 1859)",
        """
        |REF (
        |  REFAUTH (
        |    darwin
        |  )
        |  AND REFPUBYEAR IS 1859
        |)""",
    ),
    "nested-sub-fields": (
        "REF(WEBSITE(x))",
        """
        |REF (
        |  WEBSITE (
        |    x
        |  )
        |)""",
    ),
    "and-not": (
        "REF(REFAUTH(a) AND REFTITLE(b) AND NOT REFARTNUM(7))",
        """
        |REF (
        |  REFAUTH (
        |    a
        |  )
        |  AND REFTITLE (
        |    b
        |  )
        |  AND NOT REFARTNUM (
        |    7
        |  )
        |)""",
    ),
    "group-inside-ref": (
        "REF((REFAUTH(a) OR REFAUTH(b)) AND REFPUBYEAR IS 1859)",
        """
        |REF (
        |  (
        |    REFAUTH (
        |      a
        |    )
        |    OR REFAUTH (
        |      b
        |    )
        |  )
        |  AND REFPUBYEAR IS 1859
        |)""",
    ),
}


@pytest.mark.parametrize(("query", "expected"), REF_CASES.values(), ids=REF_CASES)
def test_renders_reference_searches(query: str, expected: str) -> None:
    assert query_to_indented_text(query) == _expected(expected)


# --------------------------------------------------------------------------------------
# parenthesised groups: kept in the output, nesting shown as indentation
# --------------------------------------------------------------------------------------

GROUP_CASES: dict[str, tuple[str, str]] = {
    "doubled-parentheses": (
        "((TITLE(a)))",
        """
        |(
        |  (
        |    TITLE (
        |      a
        |    )
        |  )
        |)""",
    ),
    "group-then-and": (
        "(TITLE(a) OR TITLE(b)) AND TITLE(c)",
        """
        |(
        |  TITLE (
        |    a
        |  )
        |  OR TITLE (
        |    b
        |  )
        |)
        |AND TITLE (
        |  c
        |)""",
    ),
    "nested-groups": (
        "TITLE(a) AND (TITLE(b) OR (TITLE(c) AND NOT TITLE(d)))",
        """
        |TITLE (
        |  a
        |)
        |AND (
        |  TITLE (
        |    b
        |  )
        |  OR (
        |    TITLE (
        |      c
        |    )
        |    AND NOT TITLE (
        |      d
        |    )
        |  )
        |)""",
    ),
    "group-inside-field": (
        "TITLE(foo AND (bar OR baz) AND qux)",
        """
        |TITLE (
        |      foo
        |  AND (
        |       bar
        |    OR baz
        |  )
        |  AND qux
        |)""",
    ),
}


@pytest.mark.parametrize(("query", "expected"), GROUP_CASES.values(), ids=GROUP_CASES)
def test_renders_parenthesised_groups(query: str, expected: str) -> None:
    assert query_to_indented_text(query) == _expected(expected)


# --------------------------------------------------------------------------------------
# column alignment after the widest operator of a list
# --------------------------------------------------------------------------------------

ALIGNMENT_CASES: dict[str, tuple[str, str]] = {
    "and-and-not": (
        "TITLE(a AND b AND NOT c)",
        """
        |TITLE (
        |          a
        |  AND     b
        |  AND NOT c
        |)""",
    ),
    "and-not-then-or": (
        "TITLE(x AND NOT y OR z)",
        """
        |TITLE (
        |          x
        |  AND NOT y
        |  OR      z
        |)""",
    ),
    "implicit-and-padded-too": (
        "TITLE(a b AND c)",
        """
        |TITLE (
        |      a
        |      b
        |  AND c
        |)""",
    ),
    "widest-is-or": (
        "TITLE(a OR b OR c)",
        """
        |TITLE (
        |     a
        |  OR b
        |  OR c
        |)""",
    ),
    "or-inside-and-group": (
        "TITLE-ABS-KEY(marine AND (biogeochem* OR biogeochecm*))",
        """
        |TITLE-ABS-KEY (
        |      marine
        |  AND (
        |       biogeochem*
        |    OR biogeochecm*
        |  )
        |)""",
    ),
}


@pytest.mark.parametrize(
    ("query", "expected"), ALIGNMENT_CASES.values(), ids=ALIGNMENT_CASES
)
def test_aligns_operands_behind_the_widest_operator(query: str, expected: str) -> None:
    assert query_to_indented_text(query) == _expected(expected)


# --------------------------------------------------------------------------------------
# case normalisation and whitespace tolerance
# --------------------------------------------------------------------------------------

NORMALISATION_CASES: dict[str, tuple[str, str]] = {
    "lowercase-field": (
        "title(heart)",
        """
        |TITLE (
        |  heart
        |)""",
    ),
    "lowercase-operator": (
        "TITLE(a) or title(b)",
        """
        |TITLE (
        |  a
        |)
        |OR TITLE (
        |  b
        |)""",
    ),
    "lowercase-comparison": (
        "pubyear aft 1994",
        "|PUBYEAR AFT 1994",
    ),
    "lowercase-proximity": (
        "TITLE(a w/3 b)",
        """
        |TITLE (
        |      a
        |  W/3 b
        |)""",
    ),
}


@pytest.mark.parametrize(
    ("query", "expected"), NORMALISATION_CASES.values(), ids=NORMALISATION_CASES
)
def test_normalises_case_of_codes_operators_and_keywords(
    query: str, expected: str
) -> None:
    assert query_to_indented_text(query) == _expected(expected)


def test_ignores_surrounding_and_in_between_whitespace() -> None:
    assert query_to_indented_text(
        "   TITLE(a)\n  OR TITLE(b)  "
    ) == query_to_indented_text("TITLE(a) OR TITLE(b)")


# --------------------------------------------------------------------------------------
# invalid queries
# --------------------------------------------------------------------------------------

INVALID_QUERIES: dict[str, str] = {
    "empty": "",
    "whitespace-only": "   ",
    "unclosed-parenthesis": "TITLE(a",
    "too-many-closing-parentheses": "TITLE(a))",
    "dangling-operator": "TITLE(a) AND",
    "bare-term-at-top-level": '"heart"',
    "bare-term-in-group": "TITLE(a) AND (b)",
    "unknown-field-code": "FAKEFIELD(x)",
    "nested-field-codes": "TITLE(ABS(a))",
    "missing-operator": "TITLE(a) PUBYEAR AFT 2000",
    "two-digit-year": "PUBYEAR AFT 94",
    "not-over-a-conjunction": "NOT TITLE(a) AND TITLE(b)",
}


@pytest.mark.parametrize("query", INVALID_QUERIES.values(), ids=INVALID_QUERIES)
def test_raises_a_syntax_error_for_invalid_queries(query: str) -> None:
    with pytest.raises(QuerySyntaxError):
        query_to_indented_text(query)
