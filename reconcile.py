#!/usr/bin/env python3
"""
Reconcile a Navigate360 course-history export with a PeopleSoft degree audit PDF.

Flags three types of discrepancies:
  grade_mismatch   — both sources list the course but with different grades
  navigate_only    — completed in Navigate but absent from PS major requirements
                     (often means PS put it in "Courses Not Used")
  peoplesoft_only  — PS shows it via exception/transfer but Navigate doesn't list it

Usage:
    python reconcile.py <navigate.txt> <peoplesoft.pdf>
                        [--degree BSCS] [--name NAME] [--id ID]
                        [--overrides FILE]
                        [--out FILE]  # default: {id}_{degree}_reconciled.json
                        [--open]      # run visualizer on reconciled output
"""

import argparse
import json
import os
import re
import subprocess
import sys
import urllib.request

try:
    import pypdf
except ImportError:
    sys.exit("ERROR: pypdf not installed. Run: pip3 install pypdf")


def _course_code(raw):
    """'CSC453' → 'CSC 453'"""
    m = re.match(r'^([A-Za-z]+)(\d+\w*)$', raw)
    return f"{m.group(1).upper()} {m.group(2)}" if m else raw.upper()


# ---------------------------------------------------------------------------
# Navigate360 detailed parser
# ---------------------------------------------------------------------------

_NAV_LINE_RE = re.compile(
    r'^(\d+)\s+([A-Z]+\d+[A-Z0-9]*)\|([A-Z]+)\s+(.+?)\s{2,}(\S+)\s*$'
)
_NAV_FUTURE_RE = re.compile(
    r'^--\s+\(?\d+\)?\s+([A-Z]+\d+[A-Z0-9]*)\|([A-Z]+)\s+(.+?)\s{2,}(\S+)\s*$'
)
_TERM_RE = re.compile(r'^(?:Fall|Spring|Summer|Winter)\s+\d{4}\s*$')
_SKIP_LINES = {'term at a glance', 'credits:', 'credit comp', 'term gpa', 'cum gpa',
               'academic standing', 'high school', 'early assessment', 'pre-enrollment'}

# Grades that mean "did not earn credit"
_BAD_GRADES = {'NC', 'W', 'WU', 'I', 'RD', 'RP', 'F', '-', ''}

# parse_peoplesoft_pdf() section tags that count toward the major (excludes
# GE, Minor, and Not_Used)
_MAJOR_SECTIONS = {'CS_LD', 'CS_UD', 'CS_Elective', 'CS_Major'}

def parse_navigate_detail(path):
    """Return (name, sid, degree, records).

    records: list of dicts with keys:
        code, title, units (int), grade, term, future (bool)
    """
    with open(path) as f:
        lines = f.read().splitlines()

    name = sid = degree = ''
    current_term = ''
    records = []

    for line in lines:
        s = line.strip()
        if not s or s.startswith('#'):
            continue
        low = s.lower()
        if any(low.startswith(p) for p in _SKIP_LINES):
            continue
        if low.startswith('name:'):
            name = s.split(':', 1)[1].strip(); continue
        if low.startswith('id:'):
            sid  = s.split(':', 1)[1].strip(); continue
        if low.startswith('degree:'):
            degree = s.split(':', 1)[1].strip().upper(); continue
        if _TERM_RE.match(s):
            current_term = s.strip(); continue

        # Future / enrolled line
        m = _NAV_FUTURE_RE.match(s)
        if m:
            records.append({
                'code': _course_code(m.group(1)),
                'type': m.group(2),
                'title': m.group(3),
                'units': 0,
                'grade': m.group(4),
                'term': current_term,
                'future': True,
            })
            continue

        m = _NAV_LINE_RE.match(s)
        if not m:
            continue
        units_str, code_raw, sec_type, title, grade = (
            m.group(1), m.group(2), m.group(3), m.group(4), m.group(5)
        )
        records.append({
            'code': _course_code(code_raw),
            'type': sec_type,
            'title': title,
            'units': int(units_str),
            'grade': grade,
            'term': current_term,
            'future': False,
        })

    return name, sid, degree, records


# ---------------------------------------------------------------------------
# PeopleSoft PDF parser
# ---------------------------------------------------------------------------

_TABLE_HDR = re.compile(r'Course\s+Description\s+Units\s+When\s+Grade')
_COURSE_ROW = re.compile(
    r'([A-Z]{2,5} \d{3}[A-Z0-9]*)\s+'         # course code
    r'(.+?)\s+'                               # title
    r'(\d+\.\d{2})\s+'                       # units
    r'((?:Fall|Spring|Summer|Winter)(?:,\s*(?:Fall|Spring|Summer|Winter))*\s*\d{0,4}'
    r'|Infrequently offered|[A-Z][a-z]+ \d{4})'  # specific term, or a generic "offered in" list
    r'(?:\s+([A-DF][+-]?|NC|CR|W[U]?|I|RD|RP))?'  # optional grade
)
_SECTION_MAP = {
    # Computer Science
    'Computer Science major':            'CS_Major',
    'Computer Science Lower Division':   'CS_LD',
    'LD Requirements':                   'CS_LD',
    'Computer Science Upper Division':   'CS_UD',
    'UD Requirements':                   'CS_UD',
    'Computer Science Elective':         'CS_Elective',
    # Computer Technology / Information Technology (BA/BACT/BAIT)
    'BA Computer Technology':            'CS_Major',
    'BA Information Technology':         'CS_Major',
    'BACT General Track':                'CS_Major',
    'BACT Homeland':                     'CS_Major',
    'BACT Programming':                  'CS_Major',
    'BAIT General Track':                'CS_Major',
    'BAIT Homeland':                     'CS_Major',
    'BAIT Programming':                  'CS_Major',
    'Lower Division Requirements':       'CS_LD',
    'Upper Division Requirements':       'CS_UD',
    'Lower Division Electives':          'CS_Elective',
    'Upper Division Electives':          'CS_Elective',
    'Major Elective':                    'CS_Elective',
    # Minor / Not Used — must come after major entries so major wins if same line
    'Minor in ':                         'Minor',
    'Courses Not Used':                  'Not_Used',
}
_GE_MARKERS = {
    'Undergraduate General Education', 'General Education',
    'Overall Graduation Requirement', '120 unit', 'Minimum residence',
    'CSUDH 2.00 GPA', 'US History',
    'Lifelong Learning', 'Ethnic Studies', 'Social Science',
    'Arts and Humanities', 'Scientific Inquiry',
}


OLLAMA_BASE_URL = os.environ.get('OLLAMA_BASE_URL', 'http://100.123.42.33:11434')
OLLAMA_MODEL    = os.environ.get('OLLAMA_MODEL',    'qwen2.5:7b-instruct-q4_K_M')

_PLAN_MAP = [
    (r'Plan:\s*Computer Tech(?:nology)?[:\s]+General',    'BAITG'),
    (r'Plan:\s*Computer Tech(?:nology)?[:\s]+Homeland',   'BAITHS'),
    (r'Plan:\s*Computer Tech(?:nology)?[:\s]+Prog',       'BAITP'),
    (r'Plan:\s*Information Tech(?:nology)?[:\s]+General', 'BAITG'),
    (r'Plan:\s*Information Tech(?:nology)?[:\s]+Homeland','BAITHS'),
    (r'Plan:\s*Information Tech(?:nology)?[:\s]+Prog',    'BAITP'),
    (r'Plan:\s*CS.*?Data\s*Sci',                          'MSCSDSN'),
    (r'Plan:\s*CS.*?Software\s*Eng',                      'MSCSSE'),
    (r'Plan:\s*Information Tech(?:nology)?[:\s]+Minor',   'MinorIT'),
    (r'Plan:\s*Computer Science[:\s]+Minor',              'MinorCS'),
    (r'Plan:\s*Cert(?:ificate)?.*Information Tech',       'CertIT'),
    # Bare "Plan: Information Technology" with no track suffix — real
    # PeopleSoft exports sometimes omit General/Homeland/Prog entirely
    # (the term glues directly onto "Technology" with no separator, e.g.
    # "Information TechnologyFall 2025"). Must stay last: any of the
    # track-specific patterns above should win if they also matched.
    (r'Plan:\s*Information Tech(?:nology)?',              'BSIT'),
]

_LLM_PROMPT = """\
You are parsing a PeopleSoft degree audit PDF for a university student.
Extract ONLY courses from the MAJOR requirements sections.
Exclude: General Education, Minor requirements, and "Courses Not Used".

Major sections to include:
- Lower Division Requirements  → section "CS_LD"
- Upper Division Requirements  → section "CS_UD"
- Lower Division Electives     → section "CS_Elective"
- Upper Division Electives     → section "CS_Elective"
- Any other major elective/requirement sections → section "CS_Elective"

For each course output a JSON object with:
  "code":    course code exactly as written, e.g. "CSC 101"
  "grade":   letter grade string, or "" if in-progress or planned
  "term":    term string e.g. "Fall 2024", or ""
  "section": one of "CS_LD", "CS_UD", "CS_Elective"

Return ONLY a JSON array of these objects. No prose, no markdown fences.

PDF text:
"""


def _llm_parse_sections(full_text):
    """Ask Ollama to extract major-course sections from raw PDF text.
    Returns sections dict (same shape as _regex_parse_sections) or None on failure.
    """
    payload = json.dumps({
        'model':    OLLAMA_MODEL,
        'messages': [{'role': 'user', 'content': _LLM_PROMPT + full_text}],
        'stream':   False,
    }).encode()
    try:
        req = urllib.request.Request(
            f'{OLLAMA_BASE_URL}/api/chat',
            data=payload,
            headers={'Content-Type': 'application/json'},
            method='POST',
        )
        with urllib.request.urlopen(req, timeout=60) as resp:
            data = json.loads(resp.read())
        content = data.get('message', {}).get('content', '').strip()
        # Strip markdown code fences if present
        content = re.sub(r'^```(?:json)?\s*', '', content, flags=re.M)
        content = re.sub(r'\s*```\s*$', '', content, flags=re.M).strip()
        courses = json.loads(content)
        if not isinstance(courses, list):
            return None
        sections = {}
        valid = {'CS_LD', 'CS_UD', 'CS_Elective', 'CS_Major'}
        for c in courses:
            code = c.get('code', '').strip()
            if not re.match(r'^[A-Z]{2,5} \d{3}', code):
                continue
            sec = c.get('section', 'CS_LD')
            if sec not in valid:
                sec = 'CS_LD'
            sections.setdefault(sec, []).append({
                'code':    code,
                'title':   '',
                'units':   0.0,
                'term':    c.get('term', ''),
                'grade':   c.get('grade', ''),
                'section': sec,
            })
        return sections if sections else None
    except Exception as e:
        print(f'  [Ollama] section parse failed: {e}', file=sys.stderr)
        return None


def _regex_parse_sections(full_text):
    """Regex-based major-section extractor (fallback). Returns sections dict."""
    sections = {}
    current_section = None
    in_table = False
    for line in full_text.splitlines():
        s = line.strip()
        if not s:
            continue
        if any(marker in s for marker in _GE_MARKERS):
            current_section = 'GE'
            in_table = False
            continue
        for marker, tag in _SECTION_MAP.items():
            if marker in s:
                current_section = tag
                in_table = False
                break
        if _TABLE_HDR.search(s):
            in_table = True
            # Don't `continue` here — pypdf sometimes glues the header onto
            # the preceding sentence with no line break before it, but
            # never glues a real course row onto the header text itself,
            # so falling through to the row scan below is harmless and
            # covers layouts where a row does end up on the same line.
        if s.startswith('View All') or s.startswith('First') or s == 'Return':
            in_table = False
            continue
        if in_table and current_section:
            # A single extracted "line" can hold multiple course rows
            # crammed together with no delimiter beyond whitespace (pypdf
            # drops the line breaks between some table rows), so scan for
            # every match rather than assuming one row per line.
            for m in _COURSE_ROW.finditer(s):
                code, title, units, term, grade = (
                    m.group(1), m.group(2), m.group(3), m.group(4), m.group(5) or ''
                )
                sections.setdefault(current_section, []).append({
                    'code':    code,
                    'title':   title.strip(),
                    'units':   float(units),
                    'term':    term,
                    'grade':   grade,
                    'section': current_section,
                })
    return sections


def parse_peoplesoft_pdf(path):
    """Return (name, sid, degree, catalog_year, sections).

    sections: dict mapping section_name → list of course record dicts
        Each record: code, title, units, term, grade, section
    Tries Ollama LLM for section extraction first; falls back to regex.
    """
    reader = pypdf.PdfReader(path)
    pages_text = [page.extract_text() or '' for page in reader.pages]
    full_text = '\n'.join(pages_text)

    # Identity extraction — always regex (fast, reliable)
    name = sid = degree = catalog_year = ''
    for line in full_text.splitlines():
        s = line.strip()
        m = re.match(r'^ID(.+?)\s+(\d{9})\s*$', s)
        if m:
            name = m.group(1).strip()
            sid  = m.group(2)
        m = re.search(r'Bachelor of Science in Computer Science.*?(\d{4}-\d{4})', s)
        if m:
            degree = 'BSCS'
            catalog_year = m.group(1)
        if re.search(r'Plan:\s*Computer Science\b', s) and not degree:
            degree = 'BSCS'
        for pattern, code in _PLAN_MAP:
            if re.search(pattern, s, re.I):
                degree = code
                m2 = re.search(r'(\d{4}-\d{4})', s)
                if m2:
                    catalog_year = m2.group(1)
                break

    # Section extraction — LLM primary, regex fallback
    print('  [PDF] trying Ollama section extraction...', file=sys.stderr)
    sections = _llm_parse_sections(full_text)
    if sections:
        print(f'  [PDF] Ollama OK — {sum(len(v) for v in sections.values())} courses', file=sys.stderr)
    else:
        print('  [PDF] falling back to regex section extraction', file=sys.stderr)
        sections = _regex_parse_sections(full_text)

    return name, sid, degree, catalog_year, sections


def completed_from_sections(sections):
    """Flatten a parse_peoplesoft_pdf() sections dict into a flat list of
    passing-grade course codes from major sections only (excludes GE,
    Minor, Not_Used, and in-progress/blank-grade/no-credit rows)."""
    completed = []
    for section, rows in sections.items():
        if section not in _MAJOR_SECTIONS:
            continue
        for r in rows:
            grade = r['grade']
            if grade and grade not in _BAD_GRADES:
                completed.append(r['code'])
    return completed


# ---------------------------------------------------------------------------
# Overrides file
# ---------------------------------------------------------------------------

def load_overrides(path):
    """Return {'completed': set, 'skip': set} from an overrides file.

    Format:
        completed: CSC 115    # force-count as completed
        skip: MAT 001         # exclude even if appears done
    """
    result = {'completed': set(), 'skip': set()}
    if not path or not os.path.exists(path):
        return result
    with open(path) as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith('#'):
                continue
            m = re.match(r'^(completed|skip):\s*([A-Z]+ \d+\S*)', line, re.I)
            if m:
                result[m.group(1).lower()].add(m.group(2).upper())
    return result


# ---------------------------------------------------------------------------
# Reconcile
# ---------------------------------------------------------------------------

def reconcile(nav_records, ps_sections, overrides):
    """Return (completed_codes, in_progress_codes, discrepancies)."""

    # Navigate completed: non-zero units, non-bad grade, non-future, non-LAB/ACT
    nav_done   = {}  # code → record (best grade if repeated)
    nav_failed = {}  # code → record (NC/W etc.)
    nav_future = set()

    for r in nav_records:
        code = r['code']
        if r['future']:
            nav_future.add(code)
            continue
        if r['units'] == 0:
            continue
        if r['type'] in ('LAB', 'ACT'):
            continue
        grade = r['grade']
        if grade in _BAD_GRADES:
            nav_failed[code] = r
            continue
        if grade == 'NC':
            nav_failed[code] = r
            continue
        nav_done[code] = r  # last record wins (most recent attempt)

    # PeopleSoft completed in major sections (excludes GE, Not_Used)
    ps_done     = {}   # code → record
    ps_not_used = {}   # code → record
    ps_future   = {}   # code → record (blank grade, not in Not_Used)

    for section, rows in ps_sections.items():
        for r in rows:
            code = r['code']
            if section == 'Not_Used':
                ps_not_used[code] = r
            elif section in _MAJOR_SECTIONS:
                grade = r['grade']
                if grade and grade not in _BAD_GRADES and grade != 'NC':
                    ps_done[code] = r
                elif not grade:
                    ps_future[code] = r

    # Apply overrides
    for code in overrides['completed']:
        nav_done.setdefault(code, {'code': code, 'grade': 'OVR', 'term': 'Override',
                                   'title': '', 'units': 3, 'type': '', 'future': False})
    nav_done = {k: v for k, v in nav_done.items() if k not in overrides['skip']}

    # Build discrepancy list
    discrepancies = []

    all_codes = sorted(set(nav_done) | set(ps_done))
    for code in all_codes:
        in_nav = code in nav_done
        in_ps  = code in ps_done

        if in_nav and in_ps:
            ng = nav_done[code]['grade']
            pg = ps_done[code]['grade']
            if ng != pg:
                discrepancies.append({
                    'type':       'grade_mismatch',
                    'code':       code,
                    'navigate':   ng,
                    'peoplesoft': pg,
                })
        elif in_nav and not in_ps:
            # Only flag when PS explicitly excludes the course (Courses Not Used),
            # or when PS shows it as future/in-progress but Navigate marks it done.
            # PS collapsing a completed requirement section is normal — not a discrepancy.
            if code in ps_not_used:
                pg = ps_not_used[code]['grade']
                discrepancies.append({
                    'type':       'navigate_only',
                    'code':       code,
                    'navigate':   nav_done[code]['grade'],
                    'peoplesoft': pg or '—',
                    'note':       'PS placed in "Courses Not Used" — may not count toward major',
                })
            elif code in ps_future:
                discrepancies.append({
                    'type':       'navigate_only',
                    'code':       code,
                    'navigate':   nav_done[code]['grade'],
                    'peoplesoft': '(in progress per PS)',
                    'note':       'Navigate shows completed; PS audit not yet updated',
                })
        elif in_ps and not in_nav:
            discrepancies.append({
                'type':       'peoplesoft_only',
                'code':       code,
                'navigate':   '—',
                'peoplesoft': ps_done[code]['grade'],
                'note':       'transfer credit or exception not in Navigate',
            })

    completed  = sorted(set(nav_done) | set(ps_done))
    in_progress = sorted((nav_future | set(ps_future)) - set(completed))

    return completed, in_progress, discrepancies


# ---------------------------------------------------------------------------
# Report
# ---------------------------------------------------------------------------

_DISC_LABELS = {
    'grade_mismatch':  'GRADE MISMATCH',
    'navigate_only':   'NAV ONLY (not in PS major reqs)',
    'peoplesoft_only': 'PS ONLY  (not in Navigate)',
}

def print_report(name, sid, degree, catalog_year, completed, in_progress, discrepancies):
    w = 70
    print('=' * w)
    print(f'  STUDENT RECONCILIATION REPORT')
    print('=' * w)
    print(f'  Name        : {name}')
    print(f'  ID          : {sid}')
    print(f'  Degree      : {degree}')
    if catalog_year:
        print(f'  Catalog     : {catalog_year}')
    print(f'  Completed   : {len(completed)} courses')
    print(f'  In-progress : {", ".join(in_progress) if in_progress else "none"}')
    print()

    if not discrepancies:
        print('  No discrepancies found — sources agree.')
    else:
        print(f'  DISCREPANCIES ({len(discrepancies)}):')
        print()
        for d in discrepancies:
            label = _DISC_LABELS.get(d['type'], d['type'])
            print(f'  [{label}]')
            print(f'    Course     : {d["code"]}')
            print(f'    Navigate   : {d["navigate"]}')
            print(f'    PeopleSoft : {d["peoplesoft"]}')
            if 'note' in d:
                print(f'    Note       : {d["note"]}')
            print()
        print('  To override, create an overrides file:')
        print('    completed: CSC 115   # force-include')
        print('    skip: CSC 115        # force-exclude')
        print('  Then re-run: reconcile.py ... --overrides overrides.txt')

    print()
    print('  Completed courses:')
    cols = 5
    padded = [f'{c:<12}' for c in completed]
    for i in range(0, len(padded), cols):
        print('    ' + ''.join(padded[i:i+cols]))
    print('=' * w)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def _is_pdf(path):
    try:
        with open(path, 'rb') as f:
            return f.read(4) == b'%PDF'
    except Exception:
        return False


def main():
    script_dir = os.path.dirname(os.path.abspath(__file__))

    ap = argparse.ArgumentParser(
        description='Reconcile Navigate360 + PeopleSoft for advising. '
                    'Pass one or two files in any order; PDFs are auto-detected.')
    ap.add_argument('files', nargs='*', metavar='FILE',
                    help='Navigate360 text export and/or PeopleSoft degree audit PDF '
                         '(0–2 files, any order)')
    ap.add_argument('--degree',      default='', help='Override degree code (e.g. BSCS)')
    ap.add_argument('--name',        default='', help='Override student name')
    ap.add_argument('--id',          default='', help='Override student ID')
    ap.add_argument('--catalog-dir', default='',
                    help='Path to catalog JSONs (default: courses-json-24-25/ next to this script)')
    ap.add_argument('--overrides',   default='', metavar='FILE',
                    help='Overrides file (completed: COURSE / skip: COURSE)')
    ap.add_argument('--out',         default='', metavar='FILE',
                    help='Output JSON for visualizer (default: {id}_{degree}_reconciled.json)')
    ap.add_argument('--open', action='store_true', dest='open_browser',
                    help='Run visualizer and open result in browser')
    args = ap.parse_args()

    if not args.files:
        ap.error('Provide at least one file: a Navigate360 export and/or a PeopleSoft PDF.')

    # Auto-detect which file is which
    nav_path = ps_path = None
    for f in args.files:
        if _is_pdf(f):
            ps_path = f
        else:
            nav_path = f

    # --- Parse Navigate (if provided) ---
    nav_name = nav_sid = nav_degree = ''
    nav_records = []
    if nav_path:
        print(f'Parsing Navigate360 : {nav_path}')
        nav_name, nav_sid, nav_degree, nav_records = parse_navigate_detail(nav_path)
    else:
        print('Navigate360 file   : not provided — using PDF only')

    # --- Parse PeopleSoft (if provided) ---
    ps_name = ps_sid = ps_degree = catalog_year = ''
    ps_sections = {}
    if ps_path:
        print(f'Parsing PeopleSoft  : {ps_path}')
        ps_name, ps_sid, ps_degree, catalog_year, ps_sections = parse_peoplesoft_pdf(ps_path)
    else:
        print('PeopleSoft PDF      : not provided — using Navigate only')

    # --- Resolve identity (CLI > PS > Navigate) ---
    name   = args.name   or ps_name   or nav_name
    sid    = args.id     or ps_sid    or nav_sid
    degree = (args.degree or ps_degree or nav_degree).upper()

    # Fallback for degree when neither source supplied it: scan the raw
    # Navigate360 text for a "Plan:" line via the same _PLAN_MAP patterns
    # used for PeopleSoft PDFs. (This used to also call _llm_parse_sections
    # on source_text first — that's prompt-engineered for PeopleSoft PDF
    # table layouts, not Navigate360 exports, and its result was discarded
    # unused either way, so it was just a wasted/misleading Ollama round
    # trip. Removed rather than wired up, since degree detection here has
    # always actually been this regex scan, not LLM-based.)
    if not degree:
        source_text = ''
        if nav_path:
            with open(nav_path, errors='replace') as f:
                source_text = f.read()
        for line in source_text.splitlines():
            for pattern, code in _PLAN_MAP:
                if re.search(pattern, line, re.I):
                    degree = code
                    break
            if degree:
                break
        if not degree:
            sys.exit('ERROR: degree not found — use --degree BSCS (or similar)')

    # --- Load catalog ---
    script_dir  = os.path.dirname(os.path.abspath(__file__))
    catalog_dir = args.catalog_dir or os.path.join(script_dir, 'courses-json-24-25')
    catalog_path = os.path.join(catalog_dir, f'{degree}.json')
    catalog_codes: set = set()
    if os.path.exists(catalog_path):
        with open(catalog_path) as f:
            catalog_codes = set(json.load(f).keys())
    else:
        print(f'WARNING: catalog not found at {catalog_path} — all discrepancies will be shown')

    # --- Overrides ---
    overrides = load_overrides(args.overrides)

    # --- Reconcile (or single-source summary) ---
    if nav_path and ps_path:
        # Full reconcile
        completed, in_progress, discrepancies = reconcile(nav_records, ps_sections, overrides)
        if catalog_codes:
            discrepancies = [d for d in discrepancies if d['code'] in catalog_codes]
    elif ps_path:
        # PDF only — use PS major courses as completed list; no cross-check possible
        _major = {'CS_LD', 'CS_UD', 'CS_Elective', 'CS_Major'}
        _bad   = {'', '-', 'NC', 'W', 'WU', 'I', 'RD', 'RP', 'E'}
        completed   = sorted(r['code'] for sec, rows in ps_sections.items()
                             if sec in _major
                             for r in rows if r['grade'] and r['grade'] not in _bad)
        in_progress = sorted(r['code'] for sec, rows in ps_sections.items()
                             if sec in _major
                             for r in rows if not r['grade'])
        discrepancies = []
        print('  (PDF only — no discrepancy check)')
    else:
        # Navigate only — derive completed from nav_records
        _bad = {'', '-', 'NC', 'E', 'W', 'WU', 'I', 'RD', 'RP'}
        nav_done   = {r['code']: r for r in nav_records
                      if not r['future'] and r['units'] > 0
                      and r['grade'] not in _bad
                      and r.get('type') not in ('LAB', 'ACT')}
        nav_future = {r['code'] for r in nav_records if r['future']}
        for code in overrides['completed']:
            nav_done.setdefault(code, {'code': code, 'grade': 'OVR'})
        nav_done = {k: v for k, v in nav_done.items() if k not in overrides['skip']}
        completed   = sorted(nav_done)
        in_progress = sorted(nav_future - set(completed))
        discrepancies = []
        print('  (Navigate only — no discrepancy check)')

    # --- Report ---
    print()
    print_report(name, sid, degree, catalog_year, completed, in_progress, discrepancies)

    # --- Write reconciled JSON ---
    slug = sid or re.sub(r'\W+', '_', name.lower()) or 'student'
    source_for_dir = nav_path or ps_path
    out_path = args.out or os.path.join(
        os.path.dirname(os.path.abspath(source_for_dir)),
        f'{slug}_{degree}_reconciled.json'
    )
    payload = {
        'name':      name,
        'id':        sid,
        'degree':    degree,
        'catalog':   catalog_year,
        'completed': completed,
        'in_progress': in_progress,
        'discrepancies': discrepancies,
    }
    with open(out_path, 'w') as f:
        json.dump(payload, f, indent=2)
    print(f'\nWrote reconciled file: {out_path}')

    # --- Run visualizer ---
    if args.open_browser:
        viz = os.path.join(script_dir, 'visualize_courses.py')
        pythonpath = os.path.join(script_dir, 'lib', 'python3.13', 'site-packages')
        env = os.environ.copy()
        env['PYTHONPATH'] = pythonpath
        subprocess.run([sys.executable, viz, out_path, '--open'], env=env)


if __name__ == '__main__':
    main()
