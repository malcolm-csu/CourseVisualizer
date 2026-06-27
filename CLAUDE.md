# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What This Is

Academic advising tool for CSUDH. Takes a student's completed course list, loads a degree catalog, and generates a self-contained interactive HTML prerequisite map showing which courses are done, available, and locked.

## Running

```bash
# Generate advising HTML for a student
./run.sh student.txt --open

# Reconcile Navigate360 export with PeopleSoft PDF (auto-detects degree)
./reconcile.sh navigate.txt audit.pdf --open
```

Both wrapper scripts set `PYTHONPATH` to `lib/python3.13/site-packages/` (the embedded venv). Dependencies: `networkx`, `pyvis`, `pypdf`. Run directly with `python3 visualize_courses.py ...` only if you set `PYTHONPATH` yourself.

Direct invocation with overrides:
```bash
./run.sh student.txt --catalog-dir courses-json-24-25/ --degree BSCS --open
./reconcile.sh nav.txt audit.pdf --overrides overrides.txt --out result.json --open
OLLAMA_BASE_URL=http://... OLLAMA_MODEL=llama3:8b ./reconcile.sh nav.txt audit.pdf
```

## Architecture

**`visualize_courses.py`** — single-file pipeline:
1. `parse_student_file()` — detects and parses text, JSON, or Navigate360 pipe-delimited format
2. `load_catalog()` — loads `{DEGREE}.json` + optional sidecars from catalog dir
3. `build_network()` — creates a `pyvis` graph; colors nodes by completion/availability status
4. Embeds graph data + full JavaScript logic into `HTML_TEMPLATE`, writes `{ID}_{DEGREE}_advising.html`

**`reconcile.py`** — compares Navigate360 + PeopleSoft PDF, writes reconciled JSON, optionally calls visualizer:
1. `parse_navigate_detail()` — parses Navigate360 text export (term-grouped, pipe-delimited)
2. `parse_peoplesoft_pdf()` — extracts identity via regex; tries **Ollama LLM** for section extraction, falls back to `_regex_parse_sections()` if Ollama is unreachable
3. `reconcile()` — flags `grade_mismatch`, `navigate_only`, `peoplesoft_only`; applies overrides file
4. Outputs `{id}_{degree}_reconciled.json` (valid input for `visualize_courses.py`)

**Generated HTML** — fully self-contained; no server needed. Embeds `vis-network` from CDN. All prerequisite logic is duplicated in JavaScript for client-side interactivity (click to mark complete, filter by division, etc.).

## Reconcile: Ollama LLM Integration

`reconcile.py` uses Ollama as the primary PDF section extractor — more robust than regex across degree formats:

```python
OLLAMA_BASE_URL = os.environ.get('OLLAMA_BASE_URL', 'http://100.123.42.33:11434')
OLLAMA_MODEL    = os.environ.get('OLLAMA_MODEL',    'qwen2.5:7b-instruct-q4_K_M')
```

`_llm_parse_sections(full_text)` — POSTs raw PDF text to `/api/chat`, asks for JSON array of `{code, grade, term, section}`. Returns `None` on any failure, triggering `_regex_parse_sections()` fallback. Identity (name/ID/degree) is always extracted by regex regardless.

## PeopleSoft PDF Section Detection (regex fallback)

`_SECTION_MAP` maps PDF header strings to internal section tags (`CS_LD`, `CS_UD`, `CS_Elective`, `CS_Major`, `Not_Used`, `Minor`). Only courses in `_major_sections = {'CS_LD', 'CS_UD', 'CS_Elective', 'CS_Major'}` are compared for discrepancies. Degree is auto-detected from `Plan:` lines in the PDF; known mappings:

| PDF Plan text | Degree code |
|---|---|
| `Computer Tech: General` | `BAITG` |
| `Computer Tech: Homeland` | `BAITHS` |
| `Computer Tech: Prog` | `BAITP` |
| `Computer Science` | `BSCS` |
| `CS … Data Sci` | `MSCSDSN` |
| `CS … Software Eng` | `MSCSSE` |

## Catalog Format

`courses-json-24-25/{DEGREE}.json` — dict keyed by course code:
```json
{
  "CSC 121": {
    "title": "...", "units": 3,
    "prerequisites": ["CSC 115", ["MAT 153", "MAT 191"]],
    "corequisites": [],
    "level": "Lower Division Required",
    "description": "...", "notes": "...", "offered": "Fall Spring"
  }
}
```

`prerequisites` items are either a string (AND) or a list of strings (OR — any one suffices). Dashed edges in the graph represent OR prerequisites.

Optional sidecar files per degree:
- `{DEGREE}_electives.json` — `{"CSC 4xx": 3}` (required elective slot counts)
- `{DEGREE}_unitRequirements.json` — unit totals shown in the HTML footer
- `{DEGREE}_colors.json` — override node colors (falls back to `DEFAULT_COLORS` in the script)
- `{DEGREE}_equivalents.json` — `{"CSC 471": ["MAT 361"]}` (interchangeable courses)

## Student File Formats

Three formats are auto-detected:
- **Text**: `Name:` / `ID:` / `Degree:` headers + one course code per line; `#` = comment
- **JSON**: `{"name":"...", "id":"...", "degree":"BSCS", "completed":["CSC 115", ...]}`
- **Navigate360 export**: pipe-delimited (`3 CSC453|LEC Data Mgmt  A-`); skips grades in `{NC,W,WU,I,RD,RP,-}`; future/enrolled lines start with `--`

## Node Color Semantics

Colors come from `{DEGREE}_colors.json`; falls back to `DEFAULT_COLORS` in the script. The legend in the generated HTML is fully dynamic — built from the `colors` JS object at page load, so it always matches actual rendered colors.

| State | Default color |
|-------|-------|
| Completed | `#ffcc00` gold |
| Prereq met via equivalent | `navajowhite` |
| Can take now — required | `#ff6600` orange (enlarged) |
| Can take — optional/elective | `#b8b8b8` gray |
| Lower Division locked | `#006400` dark green (per `_colors.json`) |
| Upper Division locked | `#a8bfcf` soft blue-gray (per `_colors.json`) |
| Graduate locked | `#660066` dark purple (per `_colors.json`) |

Muted colors in `{DEGREE}_colors.json` use the key pattern `"{Level} Muted"`. Keep these desaturated so locked-but-optional courses don't draw excessive attention.

## Known Issues

| Degree | Issue |
|--------|-------|
| `MinorIT` | `MinorIT.json` contains color data, not a course catalog — file needs to be recreated |
| `MinorCS` | `MinorCS_electives.json` had a stray backtick (fixed); verify elective list is complete |
| `CertIT` | `CSC 301` has prereq `CSC 121` not in the cert catalog — shows as bare node, handled gracefully |

## Catalog Utilities

`courses-json-24-25/check_json.py` — validates JSON structure  
`courses-json-24-25/check_duplicates.py` — finds duplicate course codes across files  
`courses-json-24-25/remove_duplicates.py` — deduplicates in-place
