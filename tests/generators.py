"""
Synthetic student-file generator, for testing without touching real PII.

Two things live here:
  - `plausible_completed()` walks a real degree catalog's prerequisite graph
    (reusing `can_take_course` from visualize_courses.py, so "plausible" means
    "actually achievable under the real prereq rules", not just random codes)
    to build a believable partial transcript.
  - `EDGE_CASES` is a fixed library of deliberately awkward student-file texts
    (missing degree, duplicate courses, unicode names, whitespace chaos, ...)
    that real advisor-typed files tend to produce sooner or later.

Both are consumed by gen_test_student.py (CLI, writes files to disk for
manual `./run.sh` testing) and tests/test_generated_students_fuzz.py
(pytest, feeds them straight into the parser + graph pipeline in-memory).
"""
import random

from visualize_courses import can_take_course

FIRST_NAMES = [
    "Jane", "John", "Maria", "James", "Linda", "Robert", "Patricia", "Michael",
    "Jennifer", "David", "Elizabeth", "William", "Susan", "Richard", "Karen",
    "Thomas", "Nancy", "Charles", "Lisa", "Daniel",
]
LAST_NAMES = [
    "Smith", "Johnson", "Garcia", "Rodriguez", "Martinez", "Hernandez",
    "Lopez", "Gonzalez", "Perez", "Sanchez", "Ramirez", "Torres", "Flores",
    "Rivera", "Gomez", "Diaz", "Cruz", "Morales", "Ortiz", "Gutierrez",
]

DEGREE_CODES = [
    "BSCS", "BSIT", "BAITG", "BAITHS", "BAITP",
    "MinorCS", "MSCSDSN", "MSCSSE", "CertIT",
]
# MinorIT.json is documented as broken (contains color data, not a course
# catalog — see CLAUDE.md Known Issues) and is deliberately excluded here;
# generating against it would just re-report a known, already-tracked bug.


def random_name(rng):
    return f"{rng.choice(FIRST_NAMES)} {rng.choice(LAST_NAMES)}"


def random_student_id(rng):
    return str(rng.randint(100000000, 999999999))


def plausible_completed(courses, rng, target_fraction=0.4):
    """Return a list of course codes achievable under the real prereq graph.

    Repeatedly scans for any not-yet-completed course whose prereqs/coreqs
    (via can_take_course, the same function the real app uses) are already
    satisfied, and randomly decides whether to "take" it — so runs vary in
    size/shape without ever producing an impossible transcript.
    """
    completed = []
    completed_set = set()
    remaining = list(courses.keys())
    target = max(1, int(len(remaining) * target_fraction))

    progress = True
    while len(completed_set) < target and progress:
        progress = False
        rng.shuffle(remaining)
        for code in remaining:
            if code in completed_set or len(completed_set) >= target:
                continue
            if can_take_course(code, courses, completed_set) and rng.random() < 0.6:
                completed_set.add(code)
                completed.append(code)
                progress = True
    rng.shuffle(completed)
    return completed


def render_student_text(name, sid, degree, completed, comments=None):
    """Render the documented plain-text student file format."""
    lines = []
    if name is not None:
        lines.append(f"Name: {name}")
    if sid is not None:
        lines.append(f"ID: {sid}")
    if degree is not None:
        lines.append(f"Degree: {degree}")
    lines.append("")
    for c in comments or []:
        lines.append(f"# {c}")
    for code in completed:
        lines.append(code)
    return "\n".join(lines) + "\n"


def generate_plausible_file(rng, degree=None, target_fraction=None):
    """Return (filename_hint, text) for one random-but-achievable student file."""
    from visualize_courses import load_catalog
    import os

    degree = degree or rng.choice(DEGREE_CODES)
    target_fraction = target_fraction if target_fraction is not None else rng.uniform(0.1, 0.8)
    catalog_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                                "courses-json-24-25")
    courses, *_ = load_catalog(catalog_dir, degree)
    completed = plausible_completed(courses, rng, target_fraction)
    name = random_name(rng)
    sid = random_student_id(rng)
    text = render_student_text(name, sid, degree, completed)
    hint = f"{sid}_{degree}_synthetic.txt"
    return hint, text


# ---------------------------------------------------------------------------
# Edge cases — each a (description, raw_text) pair. Deliberately awkward,
# because real advisor-typed/copy-pasted files eventually look like this.
# ---------------------------------------------------------------------------

def _edge_cases():
    cases = {}

    cases["missing_degree"] = (
        "No Degree: line at all — should fail with the documented "
        "'degree not found' error, not crash.",
        "Name: Jane Smith\nID: 888123456\nCSC 115\nCSC 121\n",
    )

    cases["empty_file"] = (
        "Completely empty input.",
        "",
    )

    cases["whitespace_only"] = (
        "Only blank lines and spaces, no content.",
        "\n   \n\t\n\n",
    )

    cases["no_completed_courses"] = (
        "Valid identity, zero courses listed.",
        "Name: Jane Smith\nID: 888123456\nDegree: BSCS\n",
    )

    cases["missing_name_and_id"] = (
        "Only a degree and course list, no Name:/ID: headers.",
        "Degree: BSCS\nCSC 115\nCSC 121\nMAT 153\n",
    )

    cases["duplicate_courses"] = (
        "The same course listed multiple times.",
        "Name: Jane Smith\nID: 888123456\nDegree: BSCS\n"
        "CSC 115\nCSC 115\nCSC 121\nCSC 115\n",
    )

    cases["unknown_courses"] = (
        "Course codes that don't exist in any catalog — should warn and "
        "be dropped, not crash.",
        "Name: Jane Smith\nID: 888123456\nDegree: BSCS\n"
        "XYZ 999\nCSC 115\nFAKE 101\n",
    )

    cases["comment_heavy"] = (
        "Comment lines interleaved with real course lines.",
        "# Advising notes below\nName: Jane Smith\n# transferred from CC\n"
        "ID: 888123456\nDegree: BSCS\n# lower division\nCSC 115\n"
        "# still needs calc\nMAT 153\n# done\n",
    )

    cases["messy_whitespace"] = (
        "Tabs, trailing spaces, blank lines, CRLF endings mixed in.",
        "Name:   Jane Smith  \r\nID:\t888123456\r\n\r\nDegree: BSCS   \r\n"
        "\r\n   CSC 115   \r\n\tMAT 153\r\n\r\n\r\nCSC 121\r\n",
    )

    cases["lowercase_and_glued_codes"] = (
        "Lowercase and no-space-between-letters-and-digits course codes — "
        "the plain-text parser does NOT normalize these (only the "
        "Navigate360/tabular parsers do via _course_code), so these should "
        "surface as 'unknown, not in catalog' warnings rather than "
        "silently matching, and must not crash.",
        "Name: Jane Smith\nID: 888123456\nDegree: BSCS\n"
        "csc115\nCSC121\n csc 115 \n",
    )

    cases["unicode_name"] = (
        "Accented / non-ASCII characters in the name.",
        "Name: José García-Muñoz\nID: 888123456\nDegree: BSCS\nCSC 115\nMAT 153\n",
    )

    cases["degree_only_no_identity"] = (
        "Degree line present but truly nothing else identifying the student.",
        "Degree: BSCS\n",
    )

    cases["huge_completed_list"] = (
        "Placeholder — filled in per-degree at generation time with every "
        "course code in that degree's catalog, to stress-test a fully "
        "completed transcript (needed courses should end up empty).",
        None,
    )

    cases["unrecognized_header_lines"] = (
        "A stray 'Key: value' style line that isn't Name:/ID:/Degree: — "
        "the parser has no special case for this, so it falls through and "
        "gets treated (and then rejected) as a bogus course code line.",
        "Name: Jane Smith\nID: 888123456\nDegree: BSCS\n"
        "Advisor: Dr. Lee\nTerm: Fall 2026\nCSC 115\n",
    )

    cases["degree_lowercase"] = (
        "Degree code given in lowercase — main() upper()s CLI overrides but "
        "the plain-text parser already upper()s the Degree: line itself, so "
        "this should resolve fine.",
        "Name: Jane Smith\nID: 888123456\ndegree: bscs\nCSC 115\n",
    )

    return cases


EDGE_CASES = _edge_cases()


def render_huge_completed_case(degree, courses):
    """Fill in the 'huge_completed_list' edge case for a specific degree."""
    text = render_student_text("Jane Smith", "888123456", degree, sorted(courses.keys()))
    return text
