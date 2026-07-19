# Course Visualizer — Academic Advising Tool

[![Tests](https://github.com/malcolm-csu/CourseVisualizer/actions/workflows/tests.yml/badge.svg)](https://github.com/malcolm-csu/CourseVisualizer/actions/workflows/tests.yml)

Generates an interactive HTML prerequisite map for a student, showing which courses are complete, which are available to take next, and which are still locked.

## Quick Start

```bash
./run.sh student.txt --open
```

## Student File Formats

**Text** (easiest to type):
```
Name: Jane Smith
ID: 888123456
Degree: BSCS
MAT 153
CSC 115
CSC 121
CSC 123
MAT 191
```

**JSON** (for programmatic use):
```json
{
  "name": "Jane Smith",
  "id": "888123456",
  "degree": "BSCS",
  "completed": ["MAT 153", "CSC 115", "CSC 121", "CSC 123", "MAT 191"]
}
```

Lines starting with `#` are ignored in text files.

## Supported Degrees

| Code | Program |
|------|---------|
| `BSCS` | B.S. Computer Science |
| `BSIT` | B.S. Information Technology |
| `BAITHS` | B.A. Information Technology – Homeland Security |
| `BAITG` | B.A. Information Technology – General |
| `BAITP` | B.A. Information Technology – Programming |
| `MinorCS` | Minor in Computer Science |
| `MinorIT` | Minor in Information Technology |
| `MSCSDSN` | M.S. Computer Science – Data Science |
| `MSCSSE` | M.S. Computer Science – Software Engineering |
| `CertIT` | Certificate in Information Technology |

## Output

Generates `{ID}_{DEGREE}_advising.html` in the current directory.

### Node Colors

| Color | Meaning |
|-------|---------|
| Gold | Completed |
| Bright green / blue / purple | Can take now (prereqs met) |
| Navajowhite | Prereq met via equivalent course |
| Dark green / blue / purple | Locked (prereqs not yet met) |

Dashed edges = OR prerequisite (either course satisfies it).

### Interactions

- **Click a node** — mark complete (gold) or un-mark it
- **Filter buttons** — show Lower Division / Upper Division / Graduate / All
- **Hover** — tooltip with course title and level
- Bottom panels show completed units, courses still needed, elective and unit requirements

## CLI Options

```
./run.sh <student_file> [--catalog-dir DIR] [--open]

  student_file     Text or JSON file describing the student
  --catalog-dir    Path to degree JSON catalogs (default: courses-json-24-25/)
  --open           Open the HTML in the default browser after generation
```

## Files

```
CourseVisualizer/
  run.sh                      # wrapper script
  visualize_courses.py        # main script
  courses-json-24-25/         # degree catalog JSONs + sidecar files
    BSCS.json
    BSCS_electives.json
    BSCS_unitRequirements.json
    BSCS_colors.json          # optional — falls back to built-in colors
    ...
  old/                        # previous versions and reference files
```

## Catalog Sidecar Files

Each degree can have optional sidecar files alongside `{DEGREE}.json`:

| File | Purpose |
|------|---------|
| `{DEGREE}_electives.json` | Required elective slots, e.g. `{"CSC 4xx": 3}` |
| `{DEGREE}_unitRequirements.json` | Unit totals and notes |
| `{DEGREE}_colors.json` | Override node colors |
