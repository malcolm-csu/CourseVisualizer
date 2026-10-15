"""
Tests for the course-code check on visualize_courses._llm_parse_student.

The Ollama call is mocked (urllib.request.urlopen), so these are
network-free; the point is that codes the model returns are kept only when
they appear in the source text.
"""
import io
import json

import visualize_courses as vc


def test_codes_in_text_keeps_present_drops_absent():
    text = "Fall 2023  CSC115 Intro  A\nSpring 2024  MAT 191 Calc  B"
    present, absent = vc._codes_in_text(["CSC 115", "mat191", "CSC 999"], text)
    assert present == ["CSC 115", "MAT 191"]
    assert absent == ["CSC 999"]


def test_codes_in_text_rejects_non_code_strings():
    present, absent = vc._codes_in_text(["Calculus", "CSC 115"], "CSC 115 Intro A")
    assert present == ["CSC 115"]
    assert absent == ["Calculus"]


def test_codes_in_text_no_partial_number_match():
    # "CSC 11" must not be supported by "CSC 115", nor "CSC 1150" by "CSC 115".
    present, absent = vc._codes_in_text(["CSC 1150", "CSC 11"], "CSC 115 Intro A")
    assert present == []
    assert absent == ["CSC 1150", "CSC 11"]


def test_codes_in_text_accepts_other_institution_codes():
    # Community-college codes keep their own shape (Codex review finding).
    text = "Fall 2021  MATH 31  Calculus  A\nSpring 2022  ENGL 1A  Composition  B"
    present, absent = vc._codes_in_text(["MATH 31", "engl1a", "MATH 3"], text)
    assert present == ["MATH 31", "ENGL 1A"]
    assert absent == ["MATH 3"]           # "MATH 3" is not "MATH 31"


def test_codes_in_text_tolerates_padded_separators():
    text = "CSC   121  Intro  A\nCSC\t123  Intro II  B\nMAT - 191  Calc  B"
    present, absent = vc._codes_in_text(["CSC 121", "CSC 123", "MAT 191"], text)
    assert present == ["CSC 121", "CSC 123", "MAT 191"]
    assert absent == []


def test_codes_in_text_does_not_join_across_lines():
    present, absent = vc._codes_in_text(["CSC 121"], "Dept CSC\n121 units")
    assert present == []
    assert absent == ["CSC 121"]


def test_codes_in_text_dedupes_spelling_variants():
    present, _ = vc._codes_in_text(["CSC 115", "csc115", "CSC-115"], "CSC 115 Intro A")
    assert present == ["CSC 115"]


class _FakeResp(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def _mock_ollama(monkeypatch, payload):
    body = json.dumps({"message": {"content": json.dumps(payload)}}).encode()
    monkeypatch.setattr(vc.urllib.request, "urlopen", lambda req, timeout=60: _FakeResp(body))


def test_llm_parse_student_drops_hallucinated_codes(monkeypatch, capsys):
    _mock_ollama(monkeypatch, {
        "name": "Test Student", "id": "000000000", "degree": "BSCS",
        "completed": ["CSC 121", "CSC 123", "CSC 311"],
    })
    text = "Some transcript\nCSC121 Intro to CS I  A\nCSC 123 Intro to CS II  B"
    completed = vc._llm_parse_student(text)[3]
    assert completed == ["CSC 121", "CSC 123"]
    assert "dropped 1 course code(s)" in capsys.readouterr().err


def test_llm_parse_student_non_list_completed_is_empty(monkeypatch):
    _mock_ollama(monkeypatch, {"name": "", "id": "", "degree": "BSCS", "completed": "CSC 121"})
    assert vc._llm_parse_student("CSC 121 Intro A")[3] == []
