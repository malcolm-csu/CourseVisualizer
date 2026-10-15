"""
Unit tests for reconcile.py's PeopleSoft regex row extraction.

Rows are read through _iter_course_rows (what _regex_parse_sections uses),
not _COURSE_ROW.finditer directly: _COURSE_ROW's optional grade can swallow
the first letter of the next row's code, and _iter_course_rows is where that
is corrected (see its docstring).

Network-free: only exercises the regex path, never _llm_parse_sections.
"""
import pytest

import reconcile as rc


def _rows(line):
    return list(rc._iter_course_rows(line, rc._known_prefixes(line)))


@pytest.mark.parametrize("term", ["Spring 2025", "Infrequently offered", "Fall, Spring"])
@pytest.mark.parametrize("next_code", ["CSC 321", "ART 101", "IS 300", "CR 101", "NC 200"])
def test_blank_grade_does_not_steal_next_course_code(term, next_code):
    line = f"MAT 271 Discrete Math 3.00 {term} {next_code} Next Course 3.00 Fall 2023 B+"
    rows = _rows(line)
    assert [r[0] for r in rows] == ["MAT 271", next_code]
    assert rows[0][4] == ""            # untaken course stays gradeless
    assert rows[1][4] == "B+"


@pytest.mark.parametrize("grade", ["A", "A-", "B+", "C", "F", "NC", "CR", "W", "WU", "I", "RD", "RP"])
def test_real_grade_still_captured(grade):
    rows = _rows(f"CSC 121 Intro to CS 3.00 Fall 2023 {grade}")
    assert rows == [("CSC 121", "Intro to CS", "3.00", "Fall 2023", grade)]


def test_real_grade_followed_by_next_row_on_same_line():
    rows = _rows("CSC 121 Intro 3.00 Fall 2023 A- CSC 221 Next 3.00 Spring 2024 C")
    assert [(r[0], r[4]) for r in rows] == [("CSC 121", "A-"), ("CSC 221", "C")]


@pytest.mark.parametrize("grade", ["A", "B", "C", "D", "F"])
def test_real_grade_glued_onto_next_code_is_kept(grade):
    # Real pypdf output sometimes drops the space between a grade and the
    # next row's code ("...Fall 2024 ACSC 321"); both the grade and the next
    # code must survive. Includes "C" + "CSC", the case most easily confused
    # with the blank-grade steal above.
    rows = _rows(f"CSC 121 Intro 3.00 Fall 2023 {grade}CSC 221 Next 3.00 Spring 2024 B")
    assert [(r[0], r[4]) for r in rows] == [("CSC 121", grade), ("CSC 221", "B")]


def test_regex_parse_sections_blank_grade_not_counted_completed():
    text = (
        "Computer Science Upper Division\n"
        "Course Description Units When GradeStatus\n"
        "MAT 271 Discrete Math 3.00 Spring 2025 CSC 321 Data Structures 3.00 Fall 2023 B+"
    )
    sections = rc._regex_parse_sections(text)
    assert sorted(rc.completed_from_sections(sections)) == ["CSC 321"]


def test_regex_parse_sections_glued_grade_counted_completed():
    text = (
        "Computer Science Upper Division\n"
        "Course Description Units When GradeStatus\n"
        "CSC 311 Data Structures 3.00 Fall 2024 ACSC 321 Prog Languages 3.00 Spring 2025"
    )
    sections = rc._regex_parse_sections(text)
    assert rc.completed_from_sections(sections) == ["CSC 311"]
    assert [r["code"] for r in sections["CS_UD"]] == ["CSC 311", "CSC 321"]


def test_blank_grade_does_not_steal_compact_next_code():
    # "C" of a compact "CSC321" must not become MAT 271's grade.
    line = "MAT 271 Discrete Math 3.00 Spring 2025 CSC321 Next 3.00 Fall 2023 B+ CSC 101 X 3.00 Fall 2022 A"
    rows = _rows(line)
    assert rows[0][0] == "MAT 271" and rows[0][4] == ""


# ---------------------------------------------------------------------------
# Transfer credit (grade T) and heading phrases inside course titles
# ---------------------------------------------------------------------------

def test_transfer_grade_t_is_captured_and_counted():
    text = (
        "Computer Science Lower Division\n"
        "Course Description Units When Grade Status\n"
        "MAT 191 Calculus I 5.00 Fall 2022 T"
    )
    sections = rc._regex_parse_sections(text)
    assert [(r["code"], r["grade"]) for r in sections["CS_LD"]] == [("MAT 191", "T")]
    assert rc.completed_from_sections(sections) == ["MAT 191"]


def test_transfer_grade_glued_onto_next_code():
    # Real pypdf output: "...Fall 2022 TENG 110 ..." = grade T + ENG 110.
    text = (
        "Computer Science Lower Division\n"
        "Course Description Units When Grade Status\n"
        "ENG 100 Writing 3.00 Fall 2021 A\n"
        "MAT 191 Calculus I 5.00 Fall 2022 TENG 110 Composition 3.00 Spring 2022 T"
    )
    rows = [(r["code"], r["grade"]) for r in rc._regex_parse_sections(text)["CS_LD"]]
    assert rows == [("ENG 100", "A"), ("MAT 191", "T"), ("ENG 110", "T")]


def test_course_title_with_heading_phrase_does_not_end_table():
    text = (
        "Computer Science Lower Division\n"
        "Course Description Units When Grade Status\n"
        "HIS 101 US History 3.00 Fall 2023 B\n"
        "MAT 191 Calculus I 5.00 Spring 2024 A"
    )
    sections = rc._regex_parse_sections(text)
    assert sorted(rc.completed_from_sections(sections)) == ["HIS 101", "MAT 191"]
    assert "GE" not in sections


def test_heading_glued_before_row_still_switches_section():
    text = (
        "Computer Science Lower Division\n"
        "Course Description Units When Grade Status\n"
        "CSC 121 Intro 3.00 Fall 2023 A\n"
        "Courses Not UsedCourse Description Units When GradeStatusPHY 120 Physics 3.00 Spring 2025 B"
    )
    sections = rc._regex_parse_sections(text)
    assert [r["code"] for r in sections["CS_LD"]] == ["CSC 121"]
    assert [r["code"] for r in sections["Not_Used"]] == ["PHY 120"]
