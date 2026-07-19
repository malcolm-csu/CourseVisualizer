"""
Unit tests for the pure parsing/graph-logic functions in visualize_courses.py.

Deliberately excludes _llm_parse_student and anything else that hits the
Ollama endpoint over the network — those paths only run when structured
parsing comes up short, and every fixture here is written to be complete
enough (degree + at least one completed course) that the LLM fallback never
fires. If a test here starts calling out to Ollama, the fixture is missing
a degree or course line, not a bug in the fallback logic.
"""
import json

import pytest

import visualize_courses as vc


# ---------------------------------------------------------------------------
# _course_code
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("raw, expected", [
    ("CSC453", "CSC 453"),
    ("csc453", "CSC 453"),
    ("MAT191", "MAT 191"),
    ("CSC453A", "CSC 453A"),
])
def test_course_code(raw, expected):
    assert vc._course_code(raw) == expected


def test_course_code_unparseable_falls_back_to_upper():
    assert vc._course_code("???") == "???"


# ---------------------------------------------------------------------------
# Navigate360 detection + parsing
# ---------------------------------------------------------------------------

def test_is_navigate_format_true_for_pipe_delimited_line():
    lines = ["some header", "3 CSC453|LEC Data Management  A-"]
    assert vc._is_navigate_format(lines)


def test_is_navigate_format_false_for_plain_text():
    lines = ["Name: Jane Smith", "CSC 115", "CSC 121"]
    assert not vc._is_navigate_format(lines)


def test_parse_navigate_identity_and_completed():
    lines = [
        "Name: Jane Smith",
        "ID: 213240835",
        "Degree: BSCS",
        "3 CSC453|LEC Data Management  A-",
        "3 MAT191|LEC Calculus I  B+",
    ]
    name, sid, degree, completed = vc._parse_navigate(lines)
    assert name == "Jane Smith"
    assert sid == "213240835"
    assert degree == "BSCS"
    assert completed == ["CSC 453", "MAT 191"]


def test_parse_navigate_skips_bad_grades_and_future_lines():
    lines = [
        "Degree: BSCS",
        "3 CSC453|LEC Data Management  W",       # withdrawn -> skipped
        "3 CSC454|LEC Databases  NC",             # no credit -> skipped
        "-- 3 CSC500|LEC Future Course  IP",      # future/enrolled -> skipped
        "3 CSC115|LEC Intro to Programming  A",   # kept
    ]
    _, _, _, completed = vc._parse_navigate(lines)
    assert completed == ["CSC 115"]


def test_parse_navigate_skips_zero_unit_lines():
    lines = [
        "Degree: BSCS",
        "0 CSC115|LAB Intro to Programming  A",   # 0-unit lab duplicate -> skipped
        "3 CSC115|LEC Intro to Programming  A",
    ]
    _, _, _, completed = vc._parse_navigate(lines)
    assert completed == ["CSC 115"]


# ---------------------------------------------------------------------------
# Tabular (advisor-notes / PeopleSoft copy-paste) detection + parsing
# ---------------------------------------------------------------------------

def test_is_tabular_format_requires_at_least_three_matches():
    two_lines = [
        "CSC281   Discrete Structures   B-",
        "CSC115   Intro to Programming   A",
    ]
    assert not vc._is_tabular_format(two_lines)

    three_lines = two_lines + ["MAT191   Calculus I   B+"]
    assert vc._is_tabular_format(three_lines)


def test_parse_tabular_identity_and_completed():
    lines = [
        "Degree: BSCS",
        "CSC281   Discrete Structures   B-",
        "CSC115   Intro to Programming   A",
        "MAT191   Calculus I   B+",
    ]
    name, sid, degree, completed = vc._parse_tabular(lines)
    assert degree == "BSCS"
    assert set(completed) == {"CSC 281", "CSC 115", "MAT 191"}


def test_parse_tabular_skips_bad_grades():
    lines = [
        "Degree: BSCS",
        "CSC281   Discrete Structures   W",
        "CSC115   Intro to Programming   A",
        "MAT191   Calculus I   NC",
        "MAT153   Precalculus   B",
    ]
    _, _, _, completed = vc._parse_tabular(lines)
    assert set(completed) == {"CSC 115", "MAT 153"}


def test_parse_tabular_dedupes_retakes_keeping_last_seen():
    # file is newest-first per the docstring on _parse_tabular
    lines = [
        "Degree: BSCS",
        "CSC115   Intro to Programming   A",   # most recent attempt, wins
        "CSC115   Intro to Programming   C",   # older attempt
        "MAT191   Calculus I   B+",
        "MAT153   Precalculus   B",
    ]
    _, _, _, completed = vc._parse_tabular(lines)
    assert completed.count("CSC 115") == 1


# ---------------------------------------------------------------------------
# Degree alias normalization
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("raw, expected", [
    ("BACTG", "BAITG"),
    ("BACT", "BAITG"),
    ("bact general", "BAITG"),
    ("BACTHS", "BAITHS"),
    ("BACTP", "BAITP"),
    ("BSCS", "BSCS"),          # already canonical, passes through
    ("bscs", "BSCS"),          # case-insensitive
])
def test_normalize_degree(raw, expected):
    assert vc._normalize_degree(raw) == expected


# ---------------------------------------------------------------------------
# parse_student_file — JSON path (no LLM involved)
# ---------------------------------------------------------------------------

def test_parse_student_file_json(tmp_path):
    data = {
        "name": "Jane Smith",
        "id": "888123456",
        "degree": "bscs",
        "completed": ["CSC 115", "CSC 121", " MAT 153 "],
    }
    f = tmp_path / "student.json"
    f.write_text(json.dumps(data))

    name, sid, degree, completed = vc.parse_student_file(str(f))
    assert name == "Jane Smith"
    assert sid == "888123456"
    assert degree == "BSCS"
    assert completed == ["CSC 115", "CSC 121", "MAT 153"]


# ---------------------------------------------------------------------------
# Graph logic: can_take_course / calculate_needed_courses / expand_with_equivalents
# ---------------------------------------------------------------------------

@pytest.fixture
def sample_courses():
    return {
        "CSC 115": {"title": "Intro", "units": 3, "level": "Lower Division Required",
                     "prerequisites": [], "corequisites": []},
        "CSC 121": {"title": "Data Structures", "units": 3, "level": "Lower Division Required",
                     "prerequisites": ["CSC 115"], "corequisites": []},
        "CSC 205": {"title": "OR-Prereq Course", "units": 3, "level": "Upper Division",
                     "prerequisites": [["CSC 115", "MAT 153"]], "corequisites": []},
        "CSC 210": {"title": "Coreq Course", "units": 3, "level": "Upper Division",
                     "prerequisites": ["CSC 121"], "corequisites": ["CSC 211"]},
        "CSC 211": {"title": "Coreq Lab", "units": 1, "level": "Upper Division",
                     "prerequisites": [], "corequisites": []},
    }


def test_can_take_course_and_prereq(sample_courses):
    assert vc.can_take_course("CSC 121", sample_courses, {"CSC 115"})
    assert not vc.can_take_course("CSC 121", sample_courses, set())


def test_can_take_course_or_prereq_either_branch_satisfies(sample_courses):
    assert vc.can_take_course("CSC 205", sample_courses, {"CSC 115"})
    assert vc.can_take_course("CSC 205", sample_courses, {"MAT 153"})
    assert not vc.can_take_course("CSC 205", sample_courses, {"CSC 121"})


def test_can_take_course_requires_coreq(sample_courses):
    assert not vc.can_take_course("CSC 210", sample_courses, {"CSC 121"})
    assert vc.can_take_course("CSC 210", sample_courses, {"CSC 121", "CSC 211"})


def test_can_take_course_bare_node_out_of_catalog(sample_courses):
    assert not vc.can_take_course("PHIL 999", sample_courses, {"CSC 115"})


def test_calculate_needed_courses_basic(sample_courses):
    needed = vc.calculate_needed_courses(sample_courses, ["CSC 115"], electives={})
    # CSC 121 and CSC 205 become completable once CSC 115 is done; CSC 210 is not
    # (needs CSC 121), CSC 211 has no prereqs so it's always completable.
    assert "CSC 121" in needed
    assert "CSC 205" in needed
    assert "CSC 211" in needed
    assert "CSC 210" not in needed
    assert "CSC 115" not in needed  # already completed


def test_calculate_needed_courses_electives_padded_to_requirement():
    courses = {
        "CSC 400": {"title": "Elective A", "units": 3, "level": "elective",
                     "prerequisites": [], "corequisites": []},
    }
    # Requirement asks for 3 slots in the "CSC 4xx" bucket but only one real
    # elective course exists in the catalog; the remainder is padded with the
    # bucket placeholder so the advising output still shows what's owed.
    needed = vc.calculate_needed_courses(courses, [], electives={"CSC 4xx": 3})
    assert needed.count("CSC 400") == 1
    assert needed.count("CSC 4xx") == 2


def test_expand_with_equivalents():
    completed = ["CSC 471"]
    equivalents = {"CSC 471": ["MAT 361"]}
    expanded = vc.expand_with_equivalents(completed, equivalents)
    assert expanded == {"CSC 471", "MAT 361"}


def test_expand_with_equivalents_no_match_is_noop():
    expanded = vc.expand_with_equivalents(["CSC 115"], {"CSC 471": ["MAT 361"]})
    assert expanded == {"CSC 115"}
