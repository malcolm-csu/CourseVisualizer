import re
import json

def is_header(line):
    # Recognizes catalog section headers, page numbers, and blank lines.
    header_patterns = [
        r'^\d+\s+[A-Za-z ]+\([A-Z]{2,4}\)',  # E.g., '172 Africana Studies (AFS)'
        r'^202\d-\d\d University Catalog',
        r'^\s*$'
    ]
    return any(re.match(p, line) for p in header_patterns)

def parse_course_catalog(filename):
    courses = []
    with open(filename, 'r', encoding='utf-8') as f:
        lines = [l.rstrip('\n') for l in f]

    # Regex for course header lines
    course_re = re.compile(r'^([A-Z]{2,4})\s+(\d{3}[A-Z]?)\.\s+(.*?)\s+\((\d+)[ -]?[Uu]nits?\)')
    prereq_re = re.compile(r'Prerequisite[s]*:?\s*(.*?)(?:\.|$)', re.IGNORECASE)
    offered_re = re.compile(r'Offered\s*([A-Za-z ,]*)\.?', re.IGNORECASE)
    repeatable_re = re.compile(r'(Repeatable (?:for credit|course))', re.IGNORECASE)
    cross_listed_re = re.compile(r'(Cross-listed[^.]+)\.', re.IGNORECASE)

    current = None
    buffer = []
    i = 0
    while i < len(lines):
        line = lines[i].strip()
        if is_header(line):
            i += 1
            continue

        m = course_re.match(line)
        if m:
            if current:
                # Parse and assign buffered info before appending
                full_desc = ' '.join(buffer).strip()
                # Pull out prereqs, offered, repeatability, cross-listing
                prereq = None
                offered = []
                repeatable = False
                cross_listed = None

                # Prereqs: Remove from desc
                prereq_match = prereq_re.search(full_desc)
                if prereq_match:
                    prereq = prereq_match.group(1).strip()
                    full_desc = prereq_re.sub('', full_desc).strip()

                # Offered: Remove from desc
                offered_match = offered_re.search(full_desc)
                if offered_match:
                    terms = [t.strip() for t in offered_match.group(1).split(',') if t.strip()]
                    offered = terms
                    full_desc = offered_re.sub('', full_desc).strip()

                # Repeatable: Remove from desc
                if repeatable_re.search(full_desc):
                    repeatable = True
                    full_desc = repeatable_re.sub('', full_desc).strip()

                # Cross-listed: Remove from desc
                cross_listed_match = cross_listed_re.search(full_desc)
                if cross_listed_match:
                    cross_listed = cross_listed_match.group(1).strip()
                    full_desc = cross_listed_re.sub('', full_desc).strip()

                current['prerequisites'] = prereq or None
                current['offered'] = offered
                current['repeatable'] = repeatable
                current['cross_listed'] = cross_listed
                current['description'] = full_desc.strip()
                courses.append(current)

            # Start new course
            current = {
                "department": m.group(1),
                "course_number": m.group(2),
                "title": m.group(3).strip(),
                "units": int(m.group(4)),
                "prerequisites": None,
                "offered": [],
                "repeatable": False,
                "cross_listed": None,
                "description": ""
            }
            buffer = []
            i += 1
            continue

        # If not a header or course start, accumulate as part of description
        if not is_header(line):
            buffer.append(line)
        i += 1

    # Append last course
    if current:
        full_desc = ' '.join(buffer).strip()
        prereq = None
        offered = []
        repeatable = False
        cross_listed = None

        prereq_match = prereq_re.search(full_desc)
        if prereq_match:
            prereq = prereq_match.group(1).strip()
            full_desc = prereq_re.sub('', full_desc).strip()

        offered_match = offered_re.search(full_desc)
        if offered_match:
            terms = [t.strip() for t in offered_match.group(1).split(',') if t.strip()]
            offered = terms
            full_desc = offered_re.sub('', full_desc).strip()

        if repeatable_re.search(full_desc):
            repeatable = True
            full_desc = repeatable_re.sub('', full_desc).strip()

        cross_listed_match = cross_listed_re.search(full_desc)
        if cross_listed_match:
            cross_listed = cross_listed_match.group(1).strip()
            full_desc = cross_listed_re.sub('', full_desc).strip()

        current['prerequisites'] = prereq or None
        current['offered'] = offered
        current['repeatable'] = repeatable
        current['cross_listed'] = cross_listed
        current['description'] = full_desc.strip()
        courses.append(current)

    return courses

# --- MAIN ---
filename = "course.txt"
catalog_json = parse_course_catalog(filename)

with open("courses_output_clean.json", "w", encoding="utf-8") as out:
    json.dump(catalog_json, out, indent=2, ensure_ascii=False)

print(f"Extracted {len(catalog_json)} courses to courses_output_clean.json")

