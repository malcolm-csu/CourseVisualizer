"""
Normalize any supported student transcript into the JSON/text format that
visualize_courses.py expects.

Accepts everything parse_student_file() already understands:
  - JSON          {"name":..., "id":..., "degree":..., "completed":[...]}
  - Navigate360   pipe-delimited export
  - Tabular       advisor-note / PeopleSoft copy-paste ("CODE  Title  Grade")
  - Plain text    Name:/ID:/Degree: headers + one course per line
  - PDF           any of the above, extracted via pypdf
  - Anything else falls back to the Ollama LLM (--force-llm to always use it)

PDF inputs are tried against reconcile.py's dedicated PeopleSoft-degree-audit
parser first (parse_peoplesoft_pdf) — that's a structurally different report
layout ("Plan:", per-requirement course tables) that parse_student_file's
generic detectors don't recognize at all, so without this a PeopleSoft audit
PDF would silently fall through to the plain-text line-by-line parser and
produce garbage "completed courses" out of page furniture like "New Window"
and "ReturnPrint Report". Only falls back to parse_student_file if the
PeopleSoft parser finds neither a degree nor any completed course — e.g. a
Navigate360-exported PDF, or a non-CSUDH transcript PDF.

This does NOT remap course codes between institutions — a community
college transcript comes through with its OWN course codes (e.g.
"MATH 100"), not CSUDH equivalents. Record transfer-credit mappings via a
{DEGREE}_equivalents.json sidecar, or edit the output file directly.

Usage:
    python ingest_student.py transcript.pdf --out student.json
    python ingest_student.py "test_data/Alex Bravo.txt" --out alex.json --format text
    python ingest_student.py community_college_transcript.pdf --force-llm --degree BSCS
"""
import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from visualize_courses import parse_student_file, _extract_text, _llm_parse_student  # noqa: E402


def _try_peoplesoft_pdf(path):
    """Try reconcile.py's PeopleSoft-degree-audit-specific PDF parser.
    Returns (name, sid, degree, completed) if it found a degree or at
    least one completed course, else None (so the caller can fall back to
    the generic parser — this PDF probably isn't a PeopleSoft audit).

    Imported lazily (only when actually handling a .pdf) so JSON/text
    ingestion keeps working in an environment without pypdf — reconcile.py
    hard sys.exit()s at import time if pypdf is missing, which would
    otherwise abort ingest_student.py before it even parses --input_file.
    """
    from reconcile import parse_peoplesoft_pdf, completed_from_sections
    name, sid, degree, _catalog_year, sections = parse_peoplesoft_pdf(path)
    completed = completed_from_sections(sections)
    if degree or completed:
        return name, sid, degree, completed
    return None


def render_text(name, sid, degree, completed):
    lines = []
    if name:
        lines.append(f"Name: {name}")
    if sid:
        lines.append(f"ID: {sid}")
    if degree:
        lines.append(f"Degree: {degree}")
    lines.append("")
    lines.extend(completed)
    return "\n".join(lines) + "\n"


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                  formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("input_file", help="Any supported transcript: .txt, .json, or .pdf")
    ap.add_argument("--out", default="",
                     help="Output path (default: derived from student ID + degree)")
    ap.add_argument("--format", choices=["json", "text"], default="json",
                     help="Output format (default: json)")
    ap.add_argument("--name", default="", help="Override detected name")
    ap.add_argument("--id", default="", help="Override detected student ID")
    ap.add_argument("--degree", default="", help="Override/force degree code (e.g. BSCS)")
    ap.add_argument("--force-llm", action="store_true",
                     help="Skip structured format detection and go straight to the Ollama "
                          "LLM — use this for transcripts from other schools/systems (e.g. "
                          "a community college transcript) that won't match the "
                          "CSUDH-specific Navigate360/tabular patterns and would otherwise "
                          "just fall through to plain-text line-by-line parsing.")
    args = ap.parse_args()

    if args.force_llm:
        raw = _extract_text(args.input_file).strip()
        llm = _llm_parse_student(raw)
        if not llm:
            sys.exit("ERROR: LLM parse failed (Ollama unreachable, or returned unusable "
                      "output). Check OLLAMA_BASE_URL, or omit --force-llm to try "
                      "structured parsing first.")
        name, sid, degree, completed = llm
    elif args.input_file.lower().endswith(".pdf"):
        result = _try_peoplesoft_pdf(args.input_file)
        if result:
            print("  [ingest] detected a PeopleSoft-style degree audit PDF", file=sys.stderr)
            name, sid, degree, completed = result
        else:
            name, sid, degree, completed = parse_student_file(args.input_file)
    else:
        name, sid, degree, completed = parse_student_file(args.input_file)

    if args.name:
        name = args.name
    if args.id:
        sid = args.id
    if args.degree:
        degree = args.degree.upper()

    print(f"Name      : {name or '(none)'}")
    print(f"ID        : {sid or '(none)'}")
    print(f"Degree    : {degree or '(none — pass --degree to set one)'}")
    print(f"Completed : {len(completed)} course(s)")
    if not degree:
        print("WARNING: no degree detected — the output will need --degree when passed "
              "to run.sh, or edit the file directly.", file=sys.stderr)

    out_path = args.out
    if not out_path:
        prefix = sid or "student"
        suffix = "json" if args.format == "json" else "txt"
        out_path = f"{prefix}_{degree or 'UNKNOWN'}.{suffix}"

    if args.format == "json":
        data = {"name": name, "id": sid, "degree": degree, "completed": completed}
        with open(out_path, "w") as f:
            json.dump(data, f, indent=2)
    else:
        with open(out_path, "w") as f:
            f.write(render_text(name, sid, degree, completed))

    print(f"\nWrote {out_path}")
    print(f"Try: ./run.sh {out_path} --open")


if __name__ == "__main__":
    main()
