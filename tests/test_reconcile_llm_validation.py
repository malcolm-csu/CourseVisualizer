"""
Unit tests for reconcile.py's LLM-output validation (validate_llm_sections).

The regex extraction is the baseline; the Ollama LLM may only ADD rows the
regex missed, and only when the PDF text visibly supports them. All inputs
here are synthetic text — no network, no real student data.
"""
import reconcile as rc

_TEXT = (
    # First line is above every section/table header — the regex never reads
    # it, so only the LLM would report this row.
    "Transfer work: MAT 191 Calculus I 4.00 Spring 2023 B\n"
    "Lower Division Requirements\n"
    "Course Description Units When Grade Status\n"
    "CSC 121 Intro to CS I 3.00 Fall 2023 A\n"
    "CSC 123 Intro to CS II 3.00 Spring 2024 B+\n"
    "CSC 311 Data Structures 3.00 Fall, Spring\n"
    "Courses Not Used\n"
    "Course Description Units When Grade Status\n"
    "PHY 101 Physics 3.00 Fall 2022 C"
)


def _regex():
    return rc._regex_parse_sections(_TEXT)


def _llm(*rows):
    sections = {}
    for code, grade, sec in rows:
        sections.setdefault(sec, []).append(
            {"code": code, "grade": grade, "term": "", "section": sec})
    return sections


def _codes(sections, sec=None):
    return sorted((r["code"], r["grade"]) for s, rows in sections.items()
                  if sec is None or s == sec for r in rows)


def test_regex_baseline_is_what_the_fixture_expects():
    assert _codes(_regex(), "CS_LD") == [("CSC 121", "A"), ("CSC 123", "B+"), ("CSC 311", "")]
    assert _codes(_regex(), "Not_Used") == [("PHY 101", "C")]


def test_llm_agreeing_with_regex_is_confirmed_not_duplicated():
    merged, report = rc.validate_llm_sections(
        _llm(("CSC 121", "A", "CS_LD")), _regex(), _TEXT)
    assert _codes(merged) == _codes(_regex())
    assert report["confirmed"] == ["CSC 121"]


def test_hallucinated_code_is_rejected():
    merged, report = rc.validate_llm_sections(
        _llm(("CSC 499", "A", "CS_UD")), _regex(), _TEXT)
    assert "CSC 499" not in {c for c, _ in _codes(merged)}
    assert report["rejected_code_not_in_pdf"] == ["CSC 499"]


def test_llm_cannot_override_regex_grade():
    # Regex sees CSC 311 with no grade (eligible/in progress); the LLM
    # claiming a passing grade must not turn it into a completed course.
    merged, report = rc.validate_llm_sections(
        _llm(("CSC 311", "A", "CS_LD")), _regex(), _TEXT)
    assert ("CSC 311", "") in _codes(merged)
    assert ("CSC 311", "A") not in _codes(merged)
    assert report["grade_conflicts"] == [{"code": "CSC 311", "llm": "A", "regex": [""]}]


def test_llm_cannot_move_course_out_of_not_used():
    merged, report = rc.validate_llm_sections(
        _llm(("PHY 101", "C", "CS_Elective")), _regex(), _TEXT)
    assert _codes(merged, "CS_Elective") == []
    assert report["section_conflicts"][0]["code"] == "PHY 101"


def test_llm_only_row_with_grade_on_its_row_is_accepted():
    merged, report = rc.validate_llm_sections(
        _llm(("MAT 191", "B", "CS_LD")), _regex(), _TEXT)
    assert ("MAT 191", "B") in _codes(merged, "CS_LD")
    assert report["accepted_new"] == ["MAT 191"]


def test_llm_only_row_with_wrong_grade_is_rejected():
    merged, report = rc.validate_llm_sections(
        _llm(("MAT 191", "A", "CS_LD")), _regex(), _TEXT)
    assert "MAT 191" not in {c for c, _ in _codes(merged)}
    assert report["rejected_grade_unverified"] == [{"code": "MAT 191", "grade": "A"}]


def test_llm_code_spacing_and_case_normalized():
    merged, _report = rc.validate_llm_sections(
        _llm(("mat191", "b", "CS_LD")), _regex(), _TEXT)
    assert ("MAT 191", "B") in _codes(merged)


def test_no_llm_output_leaves_regex_untouched():
    merged, report = rc.validate_llm_sections(None, _regex(), _TEXT)
    assert _codes(merged) == _codes(_regex())
    assert all(not v for v in report.values())


# ---------------------------------------------------------------------------
# _grade_on_row
# ---------------------------------------------------------------------------

def test_grade_must_follow_units_column():
    # "A" in the title ("A Survey") must not verify grade A.
    text = "CSC 401 A Survey of Algorithms 3.00 Fall 2024"
    assert not rc._grade_on_row("CSC 401", "A", text)


def test_grade_bounded_by_next_course_on_same_line():
    # B belongs to CSC 221, not to the ungraded CSC 121 before it.
    text = "CSC 121 Intro 3.00 Fall, Spring CSC 221 Next 3.00 Spring 2024 B"
    assert not rc._grade_on_row("CSC 121", "B", text)
    assert rc._grade_on_row("CSC 221", "B", text)


def test_grade_glued_onto_next_code_verifies():
    text = "CSC 121 Intro 3.00 Fall 2023 ACSC 221 Next 3.00 Spring 2024 B"
    assert rc._grade_on_row("CSC 121", "A", text)


def test_plus_minus_grades_are_distinct():
    text = "CSC 121 Intro 3.00 Fall 2023 B+"
    assert rc._grade_on_row("CSC 121", "B+", text)
    assert not rc._grade_on_row("CSC 121", "B", text)


# Codex review: a fabricated grade must not verify against the first letter
# of the next course's code, or against a longer grade that starts with it.

def test_grade_not_verified_by_next_course_prefix():
    text = "CSC 121 Intro 3.00 Fall, Spring CSC 221 Next 3.00 Spring 2024 B"
    assert not rc._grade_on_row("CSC 121", "C", text)


def test_grade_not_verified_by_longer_grade_token():
    text = "CSC 121 Intro 3.00 Fall 2024 CR"
    assert not rc._grade_on_row("CSC 121", "C", text)
    assert rc._grade_on_row("CSC 121", "CR", text)


def test_grade_c_glued_onto_csc_verifies():
    # "CCSC 221" is grade C glued onto CSC 221 — the hardest glued case.
    text = "CSC 121 Intro 3.00 Fall 2023 CCSC 221 Next 3.00 Spring 2024 B"
    assert rc._grade_on_row("CSC 121", "C", text)
    assert rc._grade_on_row("CSC 221", "B", text)


def test_two_letter_grade_glued_onto_next_code_verifies():
    text = "CSC 121 Intro 3.00 Fall 2023 CRCSC 221 Next 3.00 Spring 2024 B"
    assert rc._grade_on_row("CSC 121", "CR", text)
    assert not rc._grade_on_row("CSC 121", "C", text)


def test_glued_occurrence_requires_known_prefix():
    # "SC 221" is not a code in this text, even though it sits after a "C".
    text = "CSC 121 Intro 3.00 Fall 2023 CCSC 221 Next 3.00 Spring 2024 B"
    assert rc._code_occurrences("CSC 221", text)
    assert not rc._code_occurrences("SC 221", text)


# Codex review: compact codes ("CSC221") must bound a row just like spaced ones.

def test_grade_not_borrowed_across_compact_next_code():
    text = "MAT 191 Calculus 4.00 Spring 2023 CSC221 Next 3.00 Fall 2024 A"
    assert not rc._grade_on_row("MAT 191", "A", text)


def test_grade_not_borrowed_across_unrecognized_next_row():
    # Even if the next row's code isn't recognized at all, its units column
    # marks a new row.
    text = "MAT 191 Calculus 4.00 Spring 2023 zz9 Next 3.00 Fall 2024 A"
    assert not rc._grade_on_row("MAT 191", "A", text)


def test_compact_glued_grade_verifies():
    # CSC appears unglued elsewhere in the document (as in every real audit),
    # so "BCSC221" is recognized as grade B + CSC 221.
    text = ("CSC 115 Programming 3.00 Fall 2022 A\n"
            "MAT 191 Calculus 4.00 Spring 2023 BCSC221 Next 3.00 Fall 2024 A")
    assert rc._grade_on_row("MAT 191", "B", text)


def test_glued_grade_on_never_unglued_prefix_fails_closed():
    # If a prefix never appears on its own, "BCSC" can't be told apart from a
    # real "BCSC" prefix — the grade is rejected (safe), never invented.
    text = "MAT 191 Calculus 4.00 Spring 2023 BCSC221 Next 3.00 Fall 2024 A"
    assert not rc._grade_on_row("MAT 191", "B", text)


# Codex review: malformed LLM JSON must be rejected, never crash extraction.

def test_malformed_llm_rows_rejected_not_raised():
    llm = {
        "CS_LD": [
            {"code": "CSC 121", "grade": 4},          # non-string grade
            {"code": 121, "grade": "A"},              # non-string code
            "CSC 123",                                # not a dict
            {"code": "MAT 191", "grade": None},       # None grade = ungraded, fine
        ],
        "CS_UD": "not a list",
    }
    merged, report = rc.validate_llm_sections(llm, _regex(), _TEXT)
    assert len(report["rejected_malformed"]) == 3
    assert ("MAT 191", "") in _codes(merged)
    assert ("CSC 121", "A") in _codes(merged)   # regex row untouched


def test_validation_failure_falls_back_to_regex(monkeypatch):
    class _Page:
        def extract_text(self):
            return _TEXT

    class _Reader:
        pages = (_Page(),)

    def _boom(*a, **k):
        raise RuntimeError("bad LLM output")

    monkeypatch.setattr(rc.pypdf, "PdfReader", lambda path: _Reader())
    monkeypatch.setattr(rc, "_llm_parse_sections", lambda text: {"CS_LD": [{"code": "CSC 121"}]})
    monkeypatch.setattr(rc, "validate_llm_sections", _boom)
    *_, sections, report = rc.parse_peoplesoft_pdf_detailed("unused.pdf")
    assert report == {"mode": "regex_only"}
    assert _codes(sections) == _codes(_regex())


# Codex review: the grade must come from this row's grade column, not from a
# later row's title when that row's code shape isn't recognized.

def test_grade_not_verified_by_next_row_title():
    text = "MAT 191 Calculus 4.00 Spring 2023\nENGL 1A A Survey of Literature 3.00 Fall 2024 B"
    assert not rc._grade_on_row("MAT 191", "A", text)
    assert not rc._grade_on_row("MAT 191", "B", text)


def test_grade_must_be_on_same_line_as_term():
    text = "MAT 191 Calculus 4.00 Spring 2023\nA"
    assert not rc._grade_on_row("MAT 191", "A", text)


def test_header_glued_onto_first_row_still_located():
    # Real pypdf output: the table header runs straight into the first row.
    text = ("CSC 115 Programming 3.00 Fall 2022 A\n"
            "Course Description Units When GradeStatusCSC 321 Prog Lang 3.00 Fall 2024 A")
    assert rc._grade_on_row("CSC 321", "A", text)
    assert not rc._code_occurrences("SC 321", text)


def test_llm_only_transfer_grade_verifies():
    text = ("CSC 115 Programming 3.00 Fall 2022 A\n"
            "MAT 191 Calculus I 5.00 Fall 2022 T")
    assert rc._grade_on_row("MAT 191", "T", text)
