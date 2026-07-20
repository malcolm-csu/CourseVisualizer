# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

- Always run `pytest -x` after code changes
- Run `ruff check .` before considering a task complete (installed via `python3 -m pip install ruff`; not in the embedded venv, since it's a dev-only lint tool, not a runtime dependency of the app)
- Never mark work done with failing tests
- `ruff check .` will report pre-existing style findings (E701/E702 compact statements, E741 ambiguous `l` names) in legacy code predating this policy — only fix findings in files you're actually touching for the task at hand; don't blanket-refactor unrelated code to silence the full-repo count.

## Verification

- After completing any code change, run:
  `codex review --uncommitted "review for bugs, edge cases, missing tests"`
- Fix legitimate findings; note and skip false positives.
- Run the actual test suite regardless of Codex's opinion.

## What This Is

Academic advising tool for CSUDH. Takes a student's completed course list, loads a degree catalog, and generates a self-contained interactive HTML prerequisite map showing which courses are done, available, and locked.

## Running

```bash
# Generate advising HTML for a student
./run.sh student.txt --open

# Reconcile Navigate360 export with PeopleSoft PDF (auto-detects degree)
./reconcile.sh navigate.txt audit.pdf --open

# Normalize ANY supported transcript (txt/json/pdf, including non-CSUDH
# forms like a community college transcript) into the JSON/text format
# run.sh expects
./ingest_student.sh transcript.pdf --out student.json

# Generate synthetic (no-PII) student files for testing
./gen_test_student.sh --count 5 --out-dir synthetic_students/
./gen_test_student.sh --edge-cases --out-dir synthetic_students/
```

Both wrapper scripts set `PYTHONPATH` to `lib/python3.13/site-packages/` (the embedded venv). Dependencies: `networkx`, `pyvis`, `pypdf`. Run directly with `python3 visualize_courses.py ...` only if you set `PYTHONPATH` yourself.

## Testing

```bash
python3 -m pytest tests/ -v
```

`pytest.ini` puts both the repo root and the embedded venv (`lib/python3.13/site-packages`) on `sys.path`, so this works without manually exporting `PYTHONPATH`. `tests/test_visualize_courses.py` covers the pure parsing/graph functions in `visualize_courses.py` (format detectors, `_parse_navigate`/`_parse_tabular`, `_normalize_degree`, `can_take_course`, `calculate_needed_courses`, `expand_with_equivalents`) — every fixture supplies a degree and at least one completed course so `_llm_parse_student` never fires and tests stay network-free. There's no test coverage yet for `reconcile.py` or the HTML/JS template output; those still need manual verification (see README/CLI usage above).

`tests/test_real_data_smoke.py` runs the same offline parsing path against `./test_data/*.txt` — real (gitignored) student exports collected over time, used as a regression corpus. The whole module `skipif`s when `test_data/` doesn't exist, so it's a no-op on any machine without that data (including CI/fresh clones). It monkeypatches `_llm_parse_student` to a no-op so it never touches the network, then: (1) asserts `parse_student_file` doesn't crash on any file in the corpus, and (2) checks two corpus-wide tripwire counts (total completed-course entries extracted, and how many files resolve name+ID+degree without the LLM) against a recorded baseline — cheap insurance against a regex/detector change silently zeroing out extraction across many files at once, without asserting exact output for any single (messy, real-world) file.

**CI**: `.github/workflows/tests.yml` runs `pip install -r requirements.txt && python -m pytest tests/ -v` on every push/PR (Ubuntu, Python 3.12). `requirements.txt` exists solely for CI/anyone without the embedded venv — it's not what `run.sh`/`reconcile.sh` use day-to-day (those set `PYTHONPATH` into `lib/python3.13/site-packages/`, see above). `test_data/` doesn't exist in CI (gitignored, never checked out), so the real-data smoke test always skips there — CI only ever exercises `test_visualize_courses.py`.

**Real-world degree detection**: `_parse_navigate` now also scans the header for a *bare* degree-code line (no `Degree:` label — just `BSIT`, `BACT HS`, etc. on its own line, common in real advisor exports) before falling back to the LLM; `_normalize_degree` collapses dashes as well as whitespace so `BACT-HS` / `BACT -HS` / `BACT HS` all resolve the same way. `_parse_tabular` already had bare-line identity detection — `_parse_navigate` was the gap. On the local `test_data/` corpus this took degree/identity resolution from 0 → 80 of 120 files without needing Ollama at all.

**`reconcile.py` PeopleSoft PDF regex fallback was silently extracting 0 courses on every real audit PDF**, independent of Ollama reachability — found by running real "Student Services Center" degree-audit PDFs (in `test_data/`) through the pipeline. Root cause: pypdf's text extraction glues the table header onto the end of the preceding sentence with no line break (`"...requirement:Course Description Units When GradeStatus"`) and concatenates `Grade`/`Status` with no space (sometimes with an extra `Notes` column), so the old `^Course\s+Description\s+Units\s+When\s+Grade\s+Status` anchor never matched — `in_table` never flipped on, so the row regex was never even attempted. Fixed by:
- `_TABLE_HDR`: `re.search` instead of `re.match`, pattern truncated before the unreliable `Grade`/`Status` boundary.
- `_COURSE_ROW`: scanned with `finditer` (multiple matches per line) instead of one `.match()` per line — pypdf also crams multiple course rows from "eligible options" lists onto a single text line with only whitespace between them (e.g. `"MAT 271 ... 3.00 Fall, Spring   MAT 281 ... 3.00 Fall, Spring"`). Term field now also accepts a comma-joined list of generic offering terms (`Fall, Spring,Summer`), not just a specific `Month Year`.
- Blank-grade rows (in-progress/eligible-but-not-taken) still flow through unchanged into `reconcile()`'s existing `ps_future` bucket — the fix only had to widen *extraction*, not the completed/not-completed logic.
- `_PLAN_MAP` was missing a bare `Plan: Information Technology` pattern (no `General`/`Homeland`/`Prog` track suffix) — some real exports omit the track entirely. Added as the last fallback entry in the list (must stay last so any track-specific match above it wins first).

Verified against 5 real audit PDFs in `test_data/`: completed-course extraction went from 0/5 to 4/5 nonzero (the 5th genuinely has no passing grades in the source PDF — confirmed by grepping the full extracted text for grade tokens, not a parsing miss).

**The embedded venv (`lib/python3.13/site-packages`) was missing `pypdf` entirely**, despite both scripts importing it and the docs listing it as a dependency — a clean checkout's `./reconcile.sh some.pdf` would hard-fail with `ERROR: pypdf not installed`. Fixed by installing directly into that directory: `/usr/local/bin/python3.13 -m pip install --target=lib/python3.13/site-packages pypdf` (there's no system-wide `python3.13` + `pip` pair other than the Homebrew one at that path; the venv's own `bin/pip` is broken — its shebang still points at the project's old pre-move directory, `~/Dropbox/classes/Advising/visualize_courses`, so don't use it to install things here). Verified with `python3 -I` (ignores user site-packages) that `pypdf` now resolves from the embedded venv itself, not a leftover global install.

## Additional Tools

**`ingest_student.py`** — normalizes any supported transcript into the JSON/text format `visualize_courses.py` expects, as a standalone step separate from generating the HTML. Reuses `parse_student_file()` (JSON/Navigate360/tabular/plain-text/PDF detection + Ollama LLM fallback) rather than duplicating it. `--force-llm` skips structured detection entirely and goes straight to the LLM — for transcripts from other institutions (e.g. a community college transcript) that won't match any CSUDH-specific pattern and would otherwise just get misparsed as generic plain text. It does **not** remap course codes between institutions — output keeps whatever codes the source uses; transfer-credit mapping still goes through a `{DEGREE}_equivalents.json` sidecar or manual edit of the output.

**`gen_test_student.py`** — generates synthetic (no real PII) student files for testing, two modes:
- Plausible mode (default): walks a real degree catalog's prerequisite graph using `can_take_course` (the same function the real app uses, imported from `visualize_courses.py` — not reimplemented) to build an actually-achievable random transcript. Covers all degrees in `tests/generators.py`'s `DEGREE_CODES` except `MinorIT` (deliberately excluded — its catalog JSON is documented-broken, see Known Issues below; generating against it would just re-report that known bug).
- `--edge-cases`: writes the fixed `EDGE_CASES` library from `tests/generators.py` — missing degree, empty file, duplicate courses, unknown/out-of-catalog courses, unicode names, comment-heavy files, CRLF/tab whitespace chaos, lowercase/glued course codes, a fully-completed transcript, and more. Each entry documents what it's testing and why.

Both the plausible-generator and edge-case logic live in `tests/generators.py` so `gen_test_student.py` (CLI, writes to disk) and `tests/test_generated_students_fuzz.py` (pytest, in-memory) share one implementation. The fuzz test runs the real pipeline (`parse_student_file` → `load_catalog` → `calculate_needed_courses` → `build_network`) against every degree + every edge case on a fixed seed, network-free (`_llm_parse_student` monkeypatched to a no-op) — unlike `test_real_data_smoke.py`, this needs no external data and always runs, including in CI, since it's 100% synthetic.

Direct invocation with overrides:
```bash
./run.sh student.txt --catalog-dir courses-json-24-25/ --degree BSCS --open
./reconcile.sh nav.txt audit.pdf --overrides overrides.txt --out result.json --open
OLLAMA_BASE_URL=http://... OLLAMA_MODEL=llama3:8b ./reconcile.sh nav.txt audit.pdf
```

## Architecture

**`visualize_courses.py`** — single-file pipeline:
1. `parse_student_file()` — auto-detects and parses one of four formats, in order:
   - **JSON** — `{"name","id","degree","completed":[...]}`
   - **Navigate360** — pipe-delimited export (`_is_navigate_format` / `_parse_navigate`)
   - **Tabular** — advisor-notes / PeopleSoft copy-paste (`CODE   Title   Grade`, no pipes; `_is_tabular_format` / `_parse_tabular`)
   - **Plain text** — `Name:`/`ID:`/`Degree:` headers + one course code per line
   - PDFs are accepted directly (`_extract_text` shells out to `pypdf`) and fed through the same detectors.
   - If a format's structured parse can't find a degree or course list, it falls back to `_llm_parse_student()` (Ollama, same `OLLAMA_BASE_URL`/`OLLAMA_MODEL` env vars as `reconcile.py`) to fill in the gaps — never overrides fields the structured parser already found.
   - `_normalize_degree()` maps advisor spelling variants (`_DEGREE_ALIASES`, e.g. `BACTG` → `BAITG`) to canonical codes.
2. `load_catalog()` — loads `{DEGREE}.json` + optional sidecars from catalog dir
3. `build_network()` — creates a `pyvis` graph; colors nodes by completion/availability status
4. Embeds graph data + full JavaScript logic into `HTML_TEMPLATE`, writes `{ID}_{DEGREE}_advising.html`

Both `visualize_courses.py` and `reconcile.py` independently define `OLLAMA_BASE_URL`/`OLLAMA_MODEL` (defaulting to the same GPU box and model) — there's no shared config module, so if you change one, change both.

**`reconcile.py`** — compares Navigate360 + PeopleSoft PDF, writes reconciled JSON, optionally calls visualizer:
1. `parse_navigate_detail()` — parses Navigate360 text export (term-grouped, pipe-delimited)
2. `parse_peoplesoft_pdf()` — extracts identity via regex; tries **Ollama LLM** for section extraction, falls back to `_regex_parse_sections()` if Ollama is unreachable
3. `completed_from_sections()` — flattens a `parse_peoplesoft_pdf()` sections dict into a flat list of passing-grade course codes from major sections only (`_MAJOR_SECTIONS`), applying the same no-credit-grade filtering (`_BAD_GRADES`) as `reconcile()`. Module-level so both `reconcile()` and `ingest_student.py` share one implementation.
4. `reconcile()` — flags `grade_mismatch`, `navigate_only`, `peoplesoft_only`; applies overrides file
5. Outputs `{id}_{degree}_reconciled.json` (valid input for `visualize_courses.py`)

**Generated HTML** — fully self-contained; no server needed. Embeds `vis-network` from CDN. All prerequisite logic is duplicated in JavaScript for client-side interactivity (click to mark complete, filter by division, etc.).

## Reconcile: Ollama LLM Integration

`reconcile.py` uses Ollama as the primary PDF section extractor — more robust than regex across degree formats:

```python
OLLAMA_BASE_URL = os.environ.get('OLLAMA_BASE_URL', 'http://100.123.42.33:11434')
OLLAMA_MODEL    = os.environ.get('OLLAMA_MODEL',    'qwen2.5:7b-instruct-q4_K_M')
```

`_llm_parse_sections(full_text)` — POSTs raw PDF text to `/api/chat`, asks for JSON array of `{code, grade, term, section}`. Returns `None` on any failure, triggering `_regex_parse_sections()` fallback. Identity (name/ID/degree) is always extracted by regex regardless.

## PeopleSoft PDF Section Detection (regex fallback)

`_SECTION_MAP` maps PDF header strings to internal section tags (`CS_LD`, `CS_UD`, `CS_Elective`, `CS_Major`, `Not_Used`, `Minor`). Only courses in `_MAJOR_SECTIONS = {'CS_LD', 'CS_UD', 'CS_Elective', 'CS_Major'}` are compared for discrepancies (module-level constant, also used by `completed_from_sections()`). Degree is auto-detected from `Plan:` lines in the PDF via `_PLAN_MAP`; known mappings:

| PDF Plan text | Degree code |
|---|---|
| `Computer Science` | `BSCS` |
| `Information Technology` (bare, no track suffix) | `BSIT` |
| `Computer Tech(nology): General` / `Information Tech(nology): General` | `BAITG` |
| `Computer Tech(nology): Homeland` / `Information Tech(nology): Homeland` | `BAITHS` |
| `Computer Tech(nology): Prog` / `Information Tech(nology): Prog` | `BAITP` |
| `CS … Data Sci` | `MSCSDSN` |
| `CS … Software Eng` | `MSCSSE` |
| `Information Tech(nology): Minor` | `MinorIT` |
| `Computer Science: Minor` | `MinorCS` |
| `Cert(ificate) … Information Tech` | `CertIT` |

The bare `Information Technology` pattern must stay last in `_PLAN_MAP` (order-dependent — the loop breaks on first match, so any track-specific pattern above it should win if the PDF text also matches one of those).

`_TABLE_HDR`/`_COURSE_ROW` (course-row extraction) were previously broken against real PeopleSoft exports — see the Testing section above for the pypdf text-mangling root cause and fix.

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

Auto-detected, in this order (see `parse_student_file()` in `visualize_courses.py`):
- **JSON**: `{"name":"...", "id":"...", "degree":"BSCS", "completed":["CSC 115", ...]}`
- **Navigate360 export**: pipe-delimited (`3 CSC453|LEC Data Mgmt  A-`); skips grades in `{NC,W,WU,I,RD,RP,-}`; future/enrolled lines start with `--`
- **Tabular**: advisor-notes / PeopleSoft copy-paste, no pipes (`CSC281   Discrete Structures   B-`)
- **Text**: `Name:` / `ID:` / `Degree:` headers + one course code per line; `#` = comment
- **PDF**: any of the above, extracted via `pypdf` first
- Any format falls back to the Ollama LLM (`_llm_parse_student`) for missing fields (degree, name, ID, or course list) when the structured parser comes up short.

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
