"""
Fuzz test using synthetic (no-PII) student files against the real pipeline.

Unlike tests/test_real_data_smoke.py (which needs the gitignored, real-PII
test_data/ corpus and skips without it), everything here is generated on
the fly from tests/generators.py using the real catalog JSONs already
checked into courses-json-24-25/ — so this runs everywhere, including CI,
with no external data dependency.

Two batches:
  - Plausible files: generate_plausible_file() walks each real degree
    catalog's prereq graph (via can_take_course) to build an achievable
    transcript, so this exercises parse_student_file + the full
    completed/needed-course pipeline across every supported degree.
  - Edge cases: the fixed EDGE_CASES library (missing degree, duplicate
    courses, unicode names, whitespace chaos, unknown courses, ...).

A fixed seed keeps failures reproducible. _llm_parse_student is
monkeypatched to a no-op so this never touches the network — the point
is catching a crash or a silently-wrong result in the structured/graph
code, not exercising the LLM fallback (that's real-data-smoke's job when
Ollama is actually reachable).
"""
import os
import random

import pytest

import visualize_courses as vc
from generators import DEGREE_CODES, EDGE_CASES, generate_plausible_file, render_huge_completed_case

CATALOG_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                            "courses-json-24-25")

SEED = 20260719


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    monkeypatch.setattr(vc, "_llm_parse_student", lambda text: None)


def _run_pipeline(text, tmp_path, degree_override=None):
    """Parse student text and run it through the same steps main() does,
    minus HTML rendering (which is covered separately by manual `--open`
    checks, not automated here)."""
    student_file = tmp_path / "student.txt"
    student_file.write_text(text)

    name, sid, degree, completed = vc.parse_student_file(str(student_file))
    if degree_override:
        degree = degree_override
    if not degree:
        return  # matches main()'s documented "degree not found" exit path

    courses, electives, unit_reqs, colors, equivalents = vc.load_catalog(CATALOG_DIR, degree)
    if colors is None:
        colors = vc.DEFAULT_COLORS
    completed = [c for c in completed if c in courses]

    needed = vc.calculate_needed_courses(courses, completed, electives, equivalents)
    vc.build_network(courses, completed, colors, equivalents, needed)


@pytest.mark.parametrize("degree", DEGREE_CODES)
def test_plausible_transcripts_do_not_crash(degree, tmp_path):
    rng = random.Random(SEED)
    for _ in range(3):
        _hint, text = generate_plausible_file(rng, degree=degree)
        _run_pipeline(text, tmp_path)


@pytest.mark.parametrize("name", sorted(EDGE_CASES.keys()))
def test_edge_case_does_not_crash(name, tmp_path):
    _description, text = EDGE_CASES[name]
    if text is None:
        # huge_completed_list: needs a real catalog filled in
        courses, *_ = vc.load_catalog(CATALOG_DIR, "BSCS")
        text = render_huge_completed_case("BSCS", courses)
    _run_pipeline(text, tmp_path)
