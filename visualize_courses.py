"""
Academic advising course visualizer.

Usage:
    python visualize_courses.py <student_file> [--catalog-dir DIR] [--open]
                                [--name NAME] [--id ID] [--degree DEGREE]

Accepted student file formats (auto-detected)
──────────────────────────────────────────────
1. Plain text:   Name:/ID:/Degree: headers + one course code per line
2. JSON:         {"name":..., "id":..., "degree":..., "completed":[...]}
3. Navigate360:  pipe-delimited export from EAB Navigate360
4. PeopleSoft:   degree audit PDF (requires pypdf)
5. Any format:   Ollama LLM fallback when structured parsers find no courses/degree

Catalog directory defaults to courses-json-24-25/ next to this script.
Outputs: {ID}_{DEGREE}_advising.html  (or student_{DEGREE}_advising.html)
"""

import argparse
import json
import os
import re
import subprocess
import sys
import urllib.request
import webbrowser
from datetime import date

import networkx as nx
from pyvis.network import Network

OLLAMA_BASE_URL = os.environ.get('OLLAMA_BASE_URL', 'http://100.123.42.33:11434')
OLLAMA_MODEL    = os.environ.get('OLLAMA_MODEL',    'qwen2.5:7b-instruct-q4_K_M')


# ---------------------------------------------------------------------------
# Student file parsing
# ---------------------------------------------------------------------------

# Matches a completed Navigate360 course line:
#   "3 CSC453|LEC Data Management  A-"
#   units  code|type  title  grade
_NAV_LINE  = re.compile(r'^(\d+)\s+([A-Z]+\d+[A-Z0-9]*)\|[A-Z]+\s+.+?\s{2,}(\S+)\s*$')
_NAV_FUTURE = re.compile(r'^--\s')          # future / enrolled
_NAV_SKIP_GRADES = {'', '-', 'NC', 'E', 'W', 'WU', 'I', 'RD', 'RP', 'F'}

# Navigate360 header: two consecutive lines identify the degree.
# (program keyword, degree-level keyword, degree code)
# Listed longest-match first; first match wins.
_NAV_DEGREE_PAIRS = [
    ('information technology', 'bachelor',    'BSIT'),
    ('computer science',       'bachelor',    'BSCS'),
    ('computer science',       'master',      None),      # ambiguous: MSCSDSN vs MSCSSE
    ('computer technology',    'certificate', 'CERTIT'),
    ('computer technology',    'minor',       'MINORIT'),
    ('computer technology',    'bachelor',    'BAITG'),   # General track default
    ('computer science',       'minor',       'MINORCS'),
]

def _nav_degree_from_lines(prev_low, curr_low):
    """Return degree code when two consecutive header lines match a known program+level pair."""
    for prog, level, code in _NAV_DEGREE_PAIRS:
        if prog in prev_low and level in curr_low:
            return code
    return ''

def _is_navigate_format(lines):
    """Return True if the file looks like a Navigate360 export."""
    return any(_NAV_LINE.match(l.strip()) for l in lines)

def _course_code(raw):
    """'CSC453' → 'CSC 453'"""
    m = re.match(r'^([A-Za-z]+)(\d+\w*)$', raw)
    return f"{m.group(1).upper()} {m.group(2)}" if m else raw.upper()

def _parse_navigate(lines):
    """Parse Navigate360 export; return (name, sid, degree, completed)."""
    name = sid = degree = ''
    completed = []

    # Pre-scan header: pick up Navigate360's unlabelled identity block
    #   "Computer Science"   ← program name (no key: prefix)
    #   "Bachelor of Science"
    #   "Coll Natural & Behav Science"
    #   "Student ID"         ← literal keyword
    #   "213240835"          ← bare digits
    stripped = [l.strip() for l in lines]
    prev_non_blank = ''
    for i, s in enumerate(stripped):
        if not s:
            continue
        low = s.lower()
        # Key:value labels (advisor-prepended or explicit in file) — highest priority
        if low.startswith('name:') and not name:
            name = s.split(':', 1)[1].strip()
        elif low.startswith('id:') and not sid:
            sid = s.split(':', 1)[1].strip()
        elif low.startswith('degree:') and not degree:
            degree = s.split(':', 1)[1].strip().upper()
        # Navigate360 "Student ID" patterns — independent of the above
        if not sid:
            # "Student ID: 212062723"  (inline, web-paste format)
            m_id = re.match(r'^student\s+id[:\s]+(\d{6,12})\s*$', low)
            if m_id:
                sid = m_id.group(1)
            # "Student ID" / "212062723"  (two-line export format)
            elif low == 'student id':
                for nxt in stripped[i + 1:]:
                    if nxt:
                        if re.match(r'^\d{6,12}$', nxt):
                            sid = nxt
                        break
        # Degree from consecutive program+level lines
        if not degree and prev_non_blank:
            code = _nav_degree_from_lines(prev_non_blank, low)
            if code:
                degree = code
        prev_non_blank = low

    # Main loop: collect completed courses
    for line in stripped:
        if not line or line.startswith('#'):
            continue
        if _NAV_FUTURE.match(line):
            continue
        m = _NAV_LINE.match(line)
        if not m:
            continue
        units, code, grade = m.group(1), m.group(2), m.group(3)
        if int(units) == 0:
            continue                        # lab/activity duplicate
        if grade in _NAV_SKIP_GRADES:
            continue                        # no credit, in-progress, etc.
        completed.append(_course_code(code))
    return name, sid, degree, completed

def _extract_text(path):
    """Return raw text from a file — uses pypdf for PDFs, plain read otherwise."""
    if path.lower().endswith('.pdf'):
        try:
            import pypdf
            reader = pypdf.PdfReader(path)
            return '\n'.join(p.extract_text() or '' for p in reader.pages)
        except Exception as e:
            sys.exit(f'ERROR: could not read PDF ({e}). Install pypdf: pip install pypdf')
    with open(path, 'r', errors='replace') as f:
        return f.read()


_KNOWN_DEGREES = {'BSCS','BSIT','BAITG','BAITHS','BAITP','MINORCS','MINORIT',
                  'MSCSDSN','MSCSSE','CERTIT'}

_LLM_STUDENT_PROMPT = """\
Extract student information from the text below.
Return ONLY a JSON object with these fields:
  "name":      student full name, or ""
  "id":        student ID number as a string, or ""
  "degree":    degree code — one of BSCS BSIT BAITG BAITHS BAITP MinorCS MinorIT \
MSCSDSN MSCSSE CertIT — or "" if unknown
  "completed": list of course codes the student has EARNED CREDIT for (exclude \
in-progress, withdrawn W/WU, no-credit NC, incomplete I). \
Course codes look like "CSC 115", "MAT 191", "CTC 228".

No prose. No markdown. JSON only.

Text:
"""

def _llm_parse_student(text):
    """Ask Ollama to extract student info from arbitrary text. Returns tuple or None."""
    payload = json.dumps({
        'model':    OLLAMA_MODEL,
        'messages': [{'role': 'user', 'content': _LLM_STUDENT_PROMPT + text[:8000]}],
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
        content = re.sub(r'^```(?:json)?\s*', '', content, flags=re.M)
        content = re.sub(r'\s*```\s*$', '', content, flags=re.M).strip()
        d = json.loads(content)
        name      = str(d.get('name', '') or '')
        sid       = str(d.get('id',   '') or '')
        degree    = str(d.get('degree','') or '').strip().upper()
        completed = [str(c).strip() for c in d.get('completed', []) if str(c).strip()]
        print(f'  [Ollama] parsed: {name or "(no name)"}, degree={degree or "?"}, '
              f'{len(completed)} courses', file=sys.stderr)
        return name, sid, degree, completed
    except Exception as e:
        print(f'  [Ollama] student parse failed: {e}', file=sys.stderr)
        return None


def parse_student_file(path):
    """Return (name, student_id, degree, completed_courses) from any supported format."""
    raw = _extract_text(path).strip()

    # JSON
    try:
        data = json.loads(raw)
        return (data.get('name', ''),
                str(data.get('id', '')),
                data.get('degree', '').strip().upper(),
                [c.strip() for c in data.get('completed', []) if c.strip()])
    except json.JSONDecodeError:
        pass

    lines = raw.splitlines()

    # Navigate360 export
    if _is_navigate_format(lines):
        result = _parse_navigate(lines)
        # If Navigate found courses but no degree, try LLM to fill the gap
        name, sid, degree, completed = result
        if completed and not degree:
            llm = _llm_parse_student(raw)
            if llm:
                degree = llm[2] or degree
                name   = name or llm[0]
                sid    = sid  or llm[1]
        return name, sid, degree, completed

    # Plain text (Name/ID/Degree headers + one course per line)
    name = sid = degree = ''
    completed = []
    for line in lines:
        line = line.strip()
        if not line or line.startswith('#'):
            continue
        low = line.lower()
        if low.startswith('name:'):
            name = line.split(':', 1)[1].strip()
        elif low.startswith('id:'):
            sid = line.split(':', 1)[1].strip()
        elif low.startswith('degree:'):
            degree = line.split(':', 1)[1].strip().upper()
        else:
            completed.append(line)

    # LLM fallback: try when no degree found or no courses found with substantial text
    needs_llm = (not degree) or (not completed and len(raw) > 200)
    if needs_llm:
        print('  [Ollama] structured parse incomplete — trying LLM...', file=sys.stderr)
        llm = _llm_parse_student(raw)
        if llm:
            llm_name, llm_sid, llm_degree, llm_completed = llm
            name      = name      or llm_name
            sid       = sid       or llm_sid
            degree    = degree    or llm_degree
            completed = completed or llm_completed

    return name, sid, degree, completed


# ---------------------------------------------------------------------------
# Catalog loading
# ---------------------------------------------------------------------------

def load_json_optional(path, default):
    if os.path.exists(path):
        with open(path) as f:
            return json.load(f)
    return default


def load_catalog(catalog_dir, degree):
    catalog_path = os.path.join(catalog_dir, f"{degree}.json")
    if not os.path.exists(catalog_path):
        sys.exit(f"ERROR: catalog not found: {catalog_path}\n"
                 f"Available: {', '.join(f for f in os.listdir(catalog_dir) if f.endswith('.json') and '_' not in f)}")
    with open(catalog_path) as f:
        courses = json.load(f)

    electives    = load_json_optional(os.path.join(catalog_dir, f"{degree}_electives.json"), {})
    unit_reqs    = load_json_optional(os.path.join(catalog_dir, f"{degree}_unitRequirements.json"),
                                      {"LOWER DIVISION REQUIREMENTS": 40,
                                       "UPPER DIVISION REQUIREMENTS": 36,
                                       "NOTES": "Minimum 18 upper division units must be taken in residence at CSUDH."})
    colors       = load_json_optional(os.path.join(catalog_dir, f"{degree}_colors.json"), None)
    # equivalents: {code: [equiv1, equiv2, ...]} — courses interchangeable for degree requirements
    equivalents  = load_json_optional(os.path.join(catalog_dir, f"{degree}_equivalents.json"), {})
    return courses, electives, unit_reqs, colors, equivalents


def expand_with_equivalents(completed, equivalents):
    """Return a set that includes each completed course plus its equivalents."""
    expanded = set(completed)
    for c in completed:
        for equiv in equivalents.get(c, []):
            expanded.add(equiv)
    return expanded


# ---------------------------------------------------------------------------
# Default colors (matches BSCS HTML)
# ---------------------------------------------------------------------------

DEFAULT_COLORS = {
    "completed":                     "#ffcc00",
    "can_take_now":                  "#ff6600",   # vivid orange — required + prereqs met
    "optional_available":            "#b8b8b8",   # soft gray — optional/elective, prereqs met
    "Lower Division":                "#008000",
    "Lower Division Required":       "#33cc33",
    "Lower Division Core":           "#33cc33",
    "Non Credit":                    "#90EE90",
    "Upper Division":                "#66b3ff",
    "Upper Division Core":           "aqua",
    "Upper Division Required":       "aqua",
    "Elective":                      "#66b3ff",
    "Graduate":                      "blueviolet",
    "Graduate Elective":             "purple",
    "Graduate Core":                 "violet",
    "Graduate Required":             "violet",
    "Completed":                     "#ffd700",
    "Lower Division Muted":          "#909090",
    "Lower Division Required Muted": "#909090",
    "Lower Division Core Muted":     "#909090",
    "Non Credit Muted":              "#909090",
    "Upper Division Muted":          "#909090",
    "Upper Division Core Muted":     "#909090",
    "Upper Division Required Muted": "#909090",
    "Graduate Muted":                "#909090",
    "Graduate Elective Muted":       "#909090",
    "Graduate Core Muted":           "#909090",
    "Graduate Required Muted":       "#909090",
}


# ---------------------------------------------------------------------------
# Graph + node building
# ---------------------------------------------------------------------------

def can_take_course(course, courses, completed):
    if course not in courses:
        return False   # bare node from an out-of-catalog prerequisite
    prereqs   = courses[course].get("prerequisites", [])
    coreqs    = courses[course].get("corequisites", [])
    for p in prereqs:
        if isinstance(p, list):
            if not any(sp in completed for sp in p):
                return False
        elif p not in completed:
            return False
    return all(c in completed for c in coreqs)


def calculate_needed_courses(courses, completed, electives, equivalents=None):
    eff = expand_with_equivalents(completed, equivalents or {})

    def completable(course):
        for p in courses[course].get("prerequisites", []):
            if isinstance(p, list):
                if not any(sp in eff for sp in p):
                    return False
            elif p not in eff:
                return False
        return all(c in eff for c in courses[course].get("corequisites", []))

    needed = []
    elective_count = {}
    for course in courses:
        if course in eff:
            continue
        if completable(course) or courses[course].get("level", "") == "elective":
            needed.append(course)
            lvl = courses[course].get("level", "")
            if lvl == "elective":
                cat = course.split()[0] + " " + course.split()[1][0] + "xx"
                elective_count[cat] = elective_count.get(cat, 0) + 1

    for cat, req in electives.items():
        while elective_count.get(cat, 0) < req:
            needed.append(cat)
            elective_count[cat] = elective_count.get(cat, 0) + 1

    return needed


def build_network(courses, completed, colors, equivalents=None, needed=None):
    G = nx.DiGraph()

    for course, details in courses.items():
        dept = course.split()[0]
        label = f"{course}\n{details['title']}\n{details['level']}\n({details['units']} Units)\n{details.get('notes','')}"
        G.add_node(course, label=label, description=details.get('description', ''),
                   level=details['level'], department=dept)
        for prereq in details.get("prerequisites", []):
            if isinstance(prereq, list):
                for sp in prereq:
                    G.add_edge(sp, course, style='dashed')
            else:
                G.add_edge(prereq, course)
        for coreq in details.get("corequisites", []):
            G.add_edge(course, coreq)

    # Starting positions: group by dept, sort within dept
    depts = sorted(set(c.split()[0] for c in courses))
    positions = {}
    for x, dept in enumerate(depts):
        dept_courses = sorted(c for c in courses if c.startswith(dept))
        for y, course in enumerate(dept_courses):
            positions[course] = (x, -y)

    net = Network(height="100%", width="100%", directed=True, notebook=False)
    net.from_nx(G)
    net.barnes_hut(gravity=-12000, central_gravity=0.05, spring_length=1000,
                   spring_strength=0.004, damping=0.18, overlap=1)

    completed_set = set(completed)
    equiv_satisfied = set()
    if equivalents:
        for c in completed:
            for e in equivalents.get(c, []):
                if e not in completed_set:
                    equiv_satisfied.add(e)
    expanded = completed_set | equiv_satisfied

    needed_set = set(needed) if needed else set()
    for node in net.nodes:
        nid   = node['id']
        level = G.nodes[nid].get('level', 'Unknown')
        node['size']  = 25
        node['font']  = {'size': 28}
        node['title'] = node['label']
        node['label'] = nid
        node['x'], node['y'] = positions.get(nid, (0, 0))
        node['level']      = level
        node['department'] = G.nodes[nid].get('department', nid.split()[0] if ' ' in nid else nid)

        if nid in completed_set:
            node['color'] = colors.get('completed', 'orange')
        elif nid in equiv_satisfied:
            node['color'] = 'navajowhite'
        elif can_take_course(nid, courses, expanded):
            if nid in needed_set:
                node['color'] = colors.get('can_take_now', '#ff6600')
                node['size']  = 32
            else:
                node['color'] = colors.get('optional_available', '#b8b8b8')
        else:
            node['color'] = colors.get(level + ' Muted', 'silver')

    return net


# ---------------------------------------------------------------------------
# HTML generation
# ---------------------------------------------------------------------------

HTML_TEMPLATE = """\
<!DOCTYPE html>
<html>
<head>
    <title>{title}</title>
    <script src="https://unpkg.com/vis-network@9.1.0/dist/vis-network.min.js"></script>
    <style>
        * {{ box-sizing: border-box; }}
        body {{ font-family: sans-serif; margin: 6px; }}
        #advisor-header {{ background: #f0f4ff; border: 1px solid #ccd; padding: 8px 14px;
                           border-radius: 6px; margin-bottom: 6px; font-size: 15px; }}
        #advisor-header strong {{ font-size: 17px; }}
        #main-layout {{ display: flex; gap: 8px; align-items: flex-start; }}
        #graph-col {{ flex: 1 1 0; min-width: 0; }}
        #mynetwork {{ width: 100%; height: 80vh; border: 2px solid #ccc; border-radius: 4px;
                      resize: vertical; overflow: hidden; }}
        #sidebar {{ width: 310px; flex-shrink: 0; font-size: 13px; }}
        #info-panels h3 {{ margin: 8px 0 2px; font-size: 13px; }}
        #info-panels ul {{ margin: 0 0 6px; padding-left: 16px; }}
        .button-container {{ margin-top: 6px; display: flex; flex-wrap: wrap; gap: 4px; }}
        .button-container button {{ padding: 4px 10px; cursor: pointer; }}
        #tooltip {{ position: absolute; display: none; border: 1px solid #333; padding: 6px 10px;
                    border-radius: 4px; background: rgba(255,255,255,0.95);
                    box-shadow: 2px 2px 8px rgba(0,0,0,0.2); font-size: 13px; z-index: 1000; }}
        #legend {{ display: flex; flex-direction: column; gap: 5px; margin-top: 10px; font-size: 13px; }}
        .leg-item {{ display: flex; align-items: center; gap: 6px; }}
        .leg-dot  {{ width: 14px; height: 14px; border-radius: 50%; border: 1px solid #888;
                     flex-shrink: 0; }}
    </style>
</head>
<body>

<div id="advisor-header">
    <strong>{student_name}</strong>
    {id_line}
    &nbsp;|&nbsp; Degree: <strong>{degree}</strong>
    &nbsp;|&nbsp; Generated: {gen_date}
</div>

<div id="main-layout">
  <div id="graph-col">
    <div id="mynetwork"></div>
    <div class="button-container">
      <button onclick="showLowerDivision()">Lower Division</button>
      <button onclick="showUpperDivision()">Upper Division</button>
      <button onclick="showGraduate()">Graduate</button>
      <button onclick="showAllCourses()">Show All</button>
      <button onclick="showNeededOnly()" style="background:#ffe0e0;">Needed Only</button>
    </div>
  </div>
  <div id="sidebar">
    <div id="legend">
      <strong style="font-size:13px;">Legend</strong>
    </div>
    <div id="info-panels">
      <h3>Completed Courses</h3>
      <ul id="completedCoursesList"></ul>
      <h3>Elective Requirements</h3>
      <ul id="neededCoursesList"></ul>
      <h3>Unit Requirements</h3>
      <ul id="unitRequirementsList"></ul>
    </div>
  </div>
</div>

<div id="tooltip"></div>

<script>
var courses              = {courses_js};
var colors               = {colors_js};
var completedCourses     = new Set({completed_js});
var electiveRequirements = {electives_js};
var unitRequirements     = {unit_reqs_js};
var needed_courses       = {needed_js};
var totalUnitsCompleted  = {total_units};
var equivalents          = {equivalents_js};

var nodes = new vis.DataSet({nodes_js});
var edges = new vis.DataSet({edges_js});

var container = document.getElementById('mynetwork');
var network = new vis.Network(container, {{nodes: nodes, edges: edges}}, {{
    physics: {{
        enabled: true,
        solver: 'barnesHut',
        barnesHut: {{
            gravitationalConstant: -12000,
            centralGravity: 0.05,
            springLength: 700,
            springConstant: 0.04,
            damping: 0.18,
            avoidOverlap: 0.5
        }},
        stabilization: {{enabled: true, iterations: 600, fit: true}}
    }},
    interaction: {{hover: true, zoomView: true, dragView: true}},
    nodes: {{font: {{size: 32}}}}
}});

network.once('stabilizationIterationsDone', function () {{
    network.setOptions({{physics: {{enabled: false}}}});
    network.fit();
}});

// Store original colors so we can restore after un-completing a course
var originalColors = {{}};
nodes.get().forEach(function(n) {{ originalColors[n.id] = n.color; }});

// Store original font color for show/hide by division
nodes.get().forEach(function(n) {{
    nodes.update({{id: n.id, origFontColor: (n.font && n.font.color) || null}});
}});

// ── Prerequisite logic ──────────────────────────────────────────────────────

function effectivelyCompleted(course) {{
    if (completedCourses.has(course)) return true;
    var equivs = equivalents[course] || [];
    for (var i = 0; i < equivs.length; i++) {{
        if (completedCourses.has(equivs[i])) return true;
    }}
    return false;
}}

function canTakeCourse(course) {{
    if (!courses[course]) return false;
    var prereqs  = courses[course]["prerequisites"] || [];
    var coreqs   = courses[course]["corequisites"]  || [];
    var ok = prereqs.every(function(p) {{
        return Array.isArray(p)
            ? p.some(function(sp) {{ return effectivelyCompleted(sp); }})
            : effectivelyCompleted(p);
    }});
    return ok && coreqs.every(function(c) {{ return effectivelyCompleted(c); }});
}}

function hasCompletedEquivalent(course) {{
    // True if this course is satisfied via an equivalent from the equivalents map
    var equivs = equivalents[course] || [];
    for (var i = 0; i < equivs.length; i++) {{
        if (completedCourses.has(equivs[i])) return true;
    }}
    // Also check OR-prerequisite satisfaction (original behavior)
    for (var other in courses) {{
        var ps = courses[other].prerequisites || [];
        for (var j = 0; j < ps.length; j++) {{
            var p = ps[j];
            if (Array.isArray(p) && p.indexOf(course) >= 0
                    && p.some(function(sp) {{ return completedCourses.has(sp); }}))
                return true;
        }}
    }}
    return false;
}}

function isStrictPrerequisiteForAll(course) {{
    for (var other in courses) {{
        var ps = courses[other].prerequisites || [];
        for (var i = 0; i < ps.length; i++) {{
            if (!Array.isArray(ps[i]) && ps[i] === course) return true;
        }}
    }}
    return false;
}}

function isStrictPrerequisite(prereq, courseSet) {{
    for (var other in courseSet) {{
        var ps = courses[other] ? (courses[other].prerequisites || []) : [];
        for (var i = 0; i < ps.length; i++) {{
            if (!Array.isArray(ps[i]) && ps[i] === prereq) return true;
        }}
    }}
    return false;
}}

function coursesNeededToBeTaken(courses, completedCourses) {{
    var needed = new Set();
    var REQUIRED = ["Lower Division Required","Lower Division Core",
                    "Upper Division Required","Upper Division Core",
                    "Graduate Core","Graduate Required"];
    for (var c in courses) {{
        if (effectivelyCompleted(c)) continue;
        if (REQUIRED.indexOf(courses[c].level) < 0) continue;
        // Skip if an equivalent course is already in the needed set
        // (e.g. don't show both MAT 361 and CSC 471 — pick the first encountered)
        var equivs = equivalents[c] || [];
        if (equivs.some(function(e) {{ return needed.has(e); }})) continue;
        needed.add(c);
    }}
    return needed;
}}

// ── Node coloring ───────────────────────────────────────────────────────────

function updateNodesColors() {{
    var neededSet = coursesNeededToBeTaken(courses, completedCourses);
    nodes.get().forEach(function(node) {{
        var nid = node.id, level = node.level, color, size;
        if (completedCourses.has(nid)) {{
            color = colors["completed"]; size = 25;
        }} else if (effectivelyCompleted(nid)) {{
            color = 'navajowhite'; size = 25;
        }} else if (canTakeCourse(nid)) {{
            if (neededSet.has(nid)) {{
                color = colors["can_take_now"] || '#ff6600'; size = 32;
            }} else if (hasCompletedEquivalent(nid) && !isStrictPrerequisiteForAll(nid)) {{
                color = 'navajowhite'; size = 25;
            }} else {{
                color = colors["optional_available"] || '#b8b8b8'; size = 25;
            }}
        }} else {{
            color = colors[level + ' Muted'] || 'grey'; size = 25;
        }}
        nodes.update({{id: nid, color: color, size: size}});
    }});
    // Re-apply dashes on OR edges
    edges.get().forEach(function(e) {{
        if (e.style === 'dashed') edges.update({{id: e.id, dashes: true}});
    }});
}}

// ── Division filters ────────────────────────────────────────────────────────

var ShowWhat = "ALL";

function showSomeNodes(validLevels) {{
    updateNodesColors();
    nodes.get().forEach(function(node) {{
        var visible = validLevels.indexOf(node.level) >= 0;
        nodes.update({{
            id:    node.id,
            color: visible ? node.color : 'whitesmoke',
            font:  {{color: visible ? (node.origFontColor || '#343434') : 'rgba(0,0,0,0)'}}
        }});
    }});
    edges.get().forEach(function(e) {{
        var fromOK = validLevels.indexOf(nodes.get(e.from).level) >= 0;
        var toOK   = validLevels.indexOf(nodes.get(e.to).level)   >= 0;
        edges.update({{id: e.id, hidden: (!fromOK && toOK)}});
    }});
}}

function showLowerDivision() {{
    ShowWhat = "LOW";
    showSomeNodes(["non-credit","Lower Division Required","Lower Division Core","Lower Division"]);
}}
function showUpperDivision() {{
    ShowWhat = "UP";
    showSomeNodes(["Upper Division Required","Upper Division Core","Upper Division"]);
}}
function showGraduate() {{
    ShowWhat = "GRAD";
    showSomeNodes(["Graduate Core","Graduate Required","Graduate"]);
}}
function showAllCourses() {{
    ShowWhat = "ALL";
    showSomeNodes(["non-credit","Lower Division Required","Lower Division Core","Lower Division",
                   "Upper Division Required","Upper Division Core","Upper Division",
                   "Graduate Core","Graduate Required","Graduate","Elective"]);
}}

function showNeededOnly() {{
    ShowWhat = "NEEDED";
    updateNodesColors();
    var neededSet = coursesNeededToBeTaken(courses, completedCourses);
    nodes.get().forEach(function(node) {{
        var visible = neededSet.has(node.id);
        nodes.update({{
            id:    node.id,
            color: visible ? node.color : 'whitesmoke',
            font:  {{color: visible ? (node.origFontColor || '#343434') : 'rgba(0,0,0,0)'}}
        }});
    }});
    edges.get().forEach(function(e) {{
        var fromVisible = neededSet.has(e.from);
        var toVisible   = neededSet.has(e.to);
        edges.update({{id: e.id, hidden: !(fromVisible || toVisible)}});
    }});
}}

// ── Click handler ───────────────────────────────────────────────────────────

network.on("click", function(params) {{
    if (!params.nodes.length) return;
    var id   = params.nodes[0];
    var node = nodes.get(id);

    if (node.color === colors["completed"]) {{
        completedCourses.delete(id);
        nodes.update({{id: id, color: originalColors[id]}});
        totalUnitsCompleted -= (courses[id].units || 0);
    }} else if (canTakeCourse(id)) {{
        completedCourses.add(id);
        nodes.update({{id: id, color: colors["completed"]}});
        totalUnitsCompleted += (courses[id].units || 0);
    }} else {{
        var missing = new Set();
        (courses[id].prerequisites || []).forEach(function(p) {{
            if (Array.isArray(p)) p.forEach(function(sp) {{ if (!completedCourses.has(sp)) missing.add(sp); }});
            else if (!completedCourses.has(p)) missing.add(p);
        }});
        (courses[id].corequisites || []).forEach(function(c) {{ if (!completedCourses.has(c)) missing.add(c); }});
        alert("Missing prerequisite(s): " + Array.from(missing).join(", "));
    }}

    updateNodesColors();
    updateCompletedCoursesDisplay();
    updateNeededCoursesDisplay();
    if (ShowWhat === "LOW")    showLowerDivision();
    if (ShowWhat === "UP")     showUpperDivision();
    if (ShowWhat === "GRAD")   showGraduate();
    if (ShowWhat === "NEEDED") showNeededOnly();
}});

// ── Tooltip ─────────────────────────────────────────────────────────────────

network.on("hoverNode", function(params) {{
    var node    = nodes.get(params.node);
    var tooltip = document.getElementById("tooltip");
    Object.assign(tooltip.style, {{
        display: "block",
        left: (params.pointer.DOM.x + 28) + "px",
        top:  (params.pointer.DOM.y + 80) + "px"
    }});
    tooltip.innerHTML = "<strong>" + node.title + "</strong><br>"
        + "Dept: " + node.department + "<br>Level: " + node.level;
}});
network.on("blurNode",  function() {{ document.getElementById("tooltip").style.display = "none"; }});

// ── Info panels ─────────────────────────────────────────────────────────────

function updateCompletedCoursesDisplay() {{
    var list = document.getElementById("completedCoursesList");
    list.innerHTML = '';
    var li = document.createElement("li");
    li.textContent = "Completed (" + totalUnitsCompleted + " units): "
        + Array.from(completedCourses).join(", ");
    list.appendChild(li);
    li = document.createElement("li");
    li.textContent = "Still needed: " + Array.from(coursesNeededToBeTaken(courses, completedCourses)).join(", ");
    list.appendChild(li);
}}

function updateNeededCoursesDisplay() {{
    var electList = document.getElementById("neededCoursesList");
    electList.innerHTML = '';
    var content = 'Elective Requirements: ';
    for (var key in electiveRequirements) {{
        if (electiveRequirements.hasOwnProperty(key))
            for (var i = 0; i < electiveRequirements[key]; i++)
                content += key + ", ";
    }}
    var li = document.createElement("li");
    li.textContent = content.replace(/, $/, '');
    electList.appendChild(li);

    var unitList = document.getElementById("unitRequirementsList");
    unitList.innerHTML = '';
    for (var k in unitRequirements) {{
        li = document.createElement("li");
        li.textContent = k + ": " + unitRequirements[k];
        unitList.appendChild(li);
    }}
}}

// ── Init ─────────────────────────────────────────────────────────────────────
updateNodesColors();
updateCompletedCoursesDisplay();
updateNeededCoursesDisplay();

// ── Legend ───────────────────────────────────────────────────────────────────
(function() {{
    var leg = document.getElementById('legend');
    function addEntry(color, label, bold) {{
        var item = document.createElement('div');
        item.className = 'leg-item';
        var dot = document.createElement('div');
        dot.className = 'leg-dot';
        dot.style.background = color;
        var txt = document.createElement('span');
        txt.textContent = label;
        if (bold) txt.style.fontWeight = 'bold';
        item.appendChild(dot);
        item.appendChild(txt);
        leg.appendChild(item);
    }}
    addEntry(colors['completed']         || '#ffcc00',  'Completed');
    addEntry('navajowhite',                             'Satisfied via equivalent');
    addEntry(colors['can_take_now']      || '#ff6600',  'Take next — required & ready', true);
    addEntry(colors['optional_available']|| '#b8b8b8',  'Can take — optional/elective');
    // Muted groups: one entry per distinct color per division tier
    var mutedGroups = [
        {{ tier: 'Lower Division',
           keys: ['Lower Division Muted','Lower Division Required Muted',
                  'Lower Division Core Muted','Non Credit Muted'],
           label: 'Lower Div — prereqs not met' }},
        {{ tier: 'Upper Division',
           keys: ['Upper Division Muted','Upper Division Required Muted',
                  'Upper Division Core Muted','Elective Muted'],
           label: 'Upper Div — prereqs not met' }},
        {{ tier: 'Graduate',
           keys: ['Graduate Muted','Graduate Core Muted',
                  'Graduate Required Muted','Graduate Elective Muted'],
           label: 'Graduate — prereqs not met' }},
    ];
    var seenColors = {{}};
    mutedGroups.forEach(function(g) {{
        var c = null;
        for (var i = 0; i < g.keys.length; i++) {{
            if (colors[g.keys[i]]) {{ c = colors[g.keys[i]]; break; }}
        }}
        if (c && !seenColors[c]) {{ seenColors[c] = true; addEntry(c, g.label); }}
    }});
}})();
</script>
</body>
</html>
"""


def slug(s):
    return re.sub(r'[^a-zA-Z0-9_-]', '_', s).strip('_')


def main():
    script_dir = os.path.dirname(os.path.abspath(__file__))
    default_catalog = os.path.join(script_dir, 'courses-json-24-25')

    parser = argparse.ArgumentParser(description="Generate advising visualization for a student")
    parser.add_argument("student_file", help="Student text, JSON, or Navigate360 export file")
    parser.add_argument("--catalog-dir", default=default_catalog,
                        help=f"Directory containing degree JSON catalogs (default: {default_catalog})")
    parser.add_argument("--open", action="store_true", dest="open_browser",
                        help="Open generated HTML in browser")
    parser.add_argument("--name",   default='', help="Override student name")
    parser.add_argument("--id",     default='', help="Override student ID")
    parser.add_argument("--degree", default='', help="Override degree code (e.g. BSCS)")
    args = parser.parse_args()

    # --- Parse student ---
    name, sid, degree, completed = parse_student_file(args.student_file)
    # CLI overrides win
    if args.name:   name   = args.name
    if args.id:     sid    = args.id
    if args.degree: degree = args.degree.upper()

    if not degree:
        sys.exit("ERROR: degree not found in file — add 'Degree: BSCS' header or use --degree BSCS")
    print(f"Student : {name or '(no name)'}")
    print(f"ID      : {sid  or '(no ID)'}")
    print(f"Degree  : {degree}")
    print(f"Completed ({len(completed)}): {', '.join(completed)}")

    # --- Load catalog ---
    courses, electives, unit_reqs, colors, equivalents = load_catalog(args.catalog_dir, degree)
    if colors is None:
        colors = DEFAULT_COLORS

    # Validate completed courses against catalog
    unknown = [c for c in completed if c not in courses]
    if unknown:
        print(f"WARNING: not in catalog, ignored: {', '.join(unknown)}")
    completed = [c for c in completed if c in courses]

    # --- Compute needed courses + total units ---
    needed      = calculate_needed_courses(courses, completed, electives, equivalents)
    total_units = sum(courses[c].get("units", 0) for c in completed
                      if isinstance(courses[c].get("units"), (int, float)))

    print(f"Total units completed : {total_units}")
    print(f"Courses still needed  : {', '.join(needed)}")

    # --- Build network ---
    net = build_network(courses, completed, colors, equivalents, needed)

    # --- Output filename ---
    prefix = sid or slug(name) or "student"
    output_filename = f"{prefix}_{degree}_advising.html"

    # --- Student header strings ---
    student_name_str = name or "Student"
    id_line          = f"&nbsp;|&nbsp; ID: {sid}" if sid else ""
    title_str        = f"{student_name_str} — {degree} Advising"
    gen_date_str     = date.today().isoformat()

    # --- Render HTML ---
    html = HTML_TEMPLATE.format(
        title          = title_str,
        student_name   = student_name_str,
        id_line        = id_line,
        degree         = degree,
        gen_date       = gen_date_str,
        courses_js     = json.dumps(courses),
        colors_js      = json.dumps(colors),
        completed_js   = json.dumps(completed),
        electives_js   = json.dumps(electives),
        unit_reqs_js   = json.dumps(unit_reqs),
        needed_js      = json.dumps(needed),
        total_units    = total_units,
        equivalents_js = json.dumps(equivalents),
        nodes_js       = json.dumps(net.nodes),
        edges_js       = json.dumps(list(net.edges)),
    )

    with open(output_filename, 'w') as f:
        f.write(html)

    print(f"Output  : {output_filename}")

    if args.open_browser:
        webbrowser.open(f"file://{os.path.abspath(output_filename)}")


if __name__ == '__main__':
    main()
