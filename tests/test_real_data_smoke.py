"""
Regression smoke test against real (anonymized-at-runtime) student exports.

`test_data/` holds real Navigate360/PeopleSoft exports from actual students —
it is real PII and is gitignored, so it only exists on machines where someone
has manually populated it. This whole module is skipped when the directory
is absent (e.g. CI, a fresh clone).

The goal here isn't to assert exact parse results for any one file (the
files are messy, real-world advisor notes with no fixed schema) — it's to
catch two classes of regression cheaply, across the *entire* local corpus,
every time the parser changes:
  1. `parse_student_file` throwing on some real-world input it used to
     handle (or silently returning the wrong types).
  2. A structural regression that tanks how much the structured (non-LLM)
     parsers can extract, corpus-wide.

`_llm_parse_student` is monkeypatched to a stub that always returns None,
so this never touches the network — the whole point is a fast, offline,
deterministic check of the structured parsing path (Navigate360, tabular,
plain-text detectors + `_normalize_degree`), independent of Ollama being
reachable.
"""
import glob
import hashlib
import os

import pytest

import visualize_courses as vc

TEST_DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "test_data")

pytestmark = pytest.mark.skipif(
    not os.path.isdir(TEST_DATA_DIR),
    reason="test_data/ not present on this machine (gitignored real student data)",
)

_FILES = sorted(glob.glob(os.path.join(TEST_DATA_DIR, "*.txt"))) if os.path.isdir(TEST_DATA_DIR) else []


def _anon_id(path):
    # Never let a real student's name land in a test name / pytest -v output.
    return hashlib.sha1(os.path.basename(path).encode()).hexdigest()[:10]


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    monkeypatch.setattr(vc, "_llm_parse_student", lambda text: None)


@pytest.mark.parametrize("path", _FILES, ids=[_anon_id(p) for p in _FILES])
def test_parse_student_file_does_not_crash(path):
    name, sid, degree, completed = vc.parse_student_file(path)
    assert isinstance(name, str)
    assert isinstance(sid, str)
    assert isinstance(degree, str)
    assert isinstance(completed, list)
    assert all(isinstance(c, str) for c in completed)


def test_corpus_wide_extraction_has_not_regressed():
    """
    Coarse tripwire: total completed-course entries the structured parsers
    pull out of the whole corpus shouldn't collapse. Not a precise number —
    just cheap insurance against a regex/detector regression silently
    zeroing out extraction across many files at once. Recorded baseline
    (2026-07-19): 1276 entries across 120 files, 88 files with >=1 course.
    """
    total_completed = 0
    files_with_courses = 0
    for path in _FILES:
        _, _, _, completed = vc.parse_student_file(path)
        total_completed += len(completed)
        if completed:
            files_with_courses += 1

    assert total_completed >= 1000
    assert files_with_courses >= 70


def test_degree_detection_coverage_has_not_regressed():
    """
    Tripwire for the bare-degree-line fallback added to _parse_navigate:
    most real exports in this corpus carry the degree as an unlabeled
    line (e.g. "BSIT", "BACT HS") rather than a "Degree:" label. Recorded
    baseline (2026-07-19): 80/120 files resolve name+id+degree without
    the LLM fallback.
    """
    fully_resolved = 0
    for path in _FILES:
        name, sid, degree, _ = vc.parse_student_file(path)
        if name and sid and degree:
            fully_resolved += 1

    assert fully_resolved >= 65
