"""
Generate synthetic student files for testing visualize_courses.py / reconcile.py
without using real student data.

Usage:
    python gen_test_student.py --count 5 --out-dir synthetic_students/
    python gen_test_student.py --degree BSCS --count 3
    python gen_test_student.py --edge-cases --out-dir synthetic_students/
    python gen_test_student.py --seed 42 --count 5

"Plausible" files are built by walking a real degree catalog's prerequisite
graph (see tests/generators.py) so the resulting transcript is one the real
app would actually consider achievable, not just random course codes.
--edge-cases writes the fixed library of deliberately awkward files instead
(missing degree, duplicate courses, unicode names, whitespace chaos, ...).
"""
import argparse
import os
import random
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "tests"))

from generators import (  # noqa: E402
    DEGREE_CODES, EDGE_CASES, generate_plausible_file, render_huge_completed_case,
)


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                  formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--count", type=int, default=5,
                     help="Number of plausible student files to generate (default: 5)")
    ap.add_argument("--degree", default="",
                     help=f"Restrict to one degree code (default: random from {DEGREE_CODES})")
    ap.add_argument("--out-dir", default="synthetic_students",
                     help="Directory to write files into (default: synthetic_students/)")
    ap.add_argument("--seed", type=int, default=None,
                     help="Random seed, for reproducible output")
    ap.add_argument("--edge-cases", action="store_true",
                     help="Write the fixed edge-case file library instead of plausible ones")
    args = ap.parse_args()

    rng = random.Random(args.seed)
    os.makedirs(args.out_dir, exist_ok=True)
    written = []

    if args.edge_cases:
        from visualize_courses import load_catalog
        catalog_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "courses-json-24-25")
        for name, (description, text) in EDGE_CASES.items():
            if text is None:
                # huge_completed_list: fill in per-degree
                degree = args.degree or "BSCS"
                courses, *_ = load_catalog(catalog_dir, degree)
                text = render_huge_completed_case(degree, courses)
            path = os.path.join(args.out_dir, f"{name}.txt")
            with open(path, "w") as f:
                f.write(text)
            written.append((path, description))
    else:
        for _ in range(args.count):
            hint, text = generate_plausible_file(rng, degree=args.degree or None)
            path = os.path.join(args.out_dir, hint)
            with open(path, "w") as f:
                f.write(text)
            written.append((path, ""))

    for path, description in written:
        print(path + (f"  — {description}" if description else ""))
    print(f"\nWrote {len(written)} file(s) to {args.out_dir}/")
    print(f"Try: ./run.sh {written[0][0]} --open")


if __name__ == "__main__":
    main()
