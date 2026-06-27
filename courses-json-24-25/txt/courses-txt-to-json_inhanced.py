import re
import json

def is_header(line):
    # Recognizes catalog section headers, page numbers, blank lines.
    header_patterns = [
        r'^\d+\s+[A-Za-z ]+\([A-Z]{2,4}\)',  # '172 Africana Studies (AFS)'
        r'^202\d-\d\d University Catalog',
        r'^\s*$'
    ]
    return any(re.match(p, line) for p in header_patterns)

def extract_courses_from_block(block):
    """
    Splits a block that might contain multiple courses into one dict per course.
    """
    # Regex for the beginning of a course in the block
    course_pat = re.compile(
        r'([A-Z]{2,4})\s+(\d{3}[A-Z]?)\.\s+(.*?)\s+\(([\d\-]+)[ -]?[Uu]nits?\)', re.DOTALL)
    matches = list(course_pat.finditer(block))
    courses = []

    for i, m in enumerate(matches):
        # Determine the end of this course (start of next course, or end of block)
        start = m.end()
        end = matches[i+1].start() if (i+1) < len(matches) else len(block)
        desc_block = block[start:end].strip()
        prereq = None
        offered = []
        repeatable = False
        cross_listed = None

        # Extract fields from desc_block
        prereq_match = re.search(r'Prerequisite[s]*:?\s*(.*?)(?:\.|$)', desc_block, re.IGNORECASE)
        if prereq_match:
            prereq = prereq_match.group(1).strip()
            desc_block = re.sub(r'Prerequisite[s]*:?\s*.*?(?:\.|$)', '', desc_block, flags=re.IGNORECASE).strip()

        offered_match = re.search(r'Offered\s*([A-Za-z ,]*)\.?', desc_block, re.IGNORECASE)
        if offered_match:
            terms = [t.strip() for t in offered_match.group(1).split(',') if t.strip()]
            offered = terms
            desc_block = re.sub(r'Offered\s*[A-Za-z ,]*\.?', '', desc_block, flags=re.IGNORECASE).strip()

        if re.search(r'(Repeatable (?:for credit|course))', desc_block, re.IGNORECASE):
            repeatable = True
            desc_block = re.sub(r'(Repeatable (?:for credit|course))', '', desc_block, flags=re.IGNORECASE).strip()

        cross_listed_match = re.search(r'(Cross-listed[^.]+)\.', desc_block, re.IGNORECASE)
        if cross_listed_match:
            cross_listed = cross_listed_match.group(1).strip()
            desc_block = re.sub(r'(Cross-listed[^.]+)\.', '', desc_block, flags=re.IGNORECASE).strip()

        # Units: can be a single number or a range
        units_text = m.group(4)
        units = units_text if '-' in units_text else int(units_text)

        courses.append({
            "department": m.group(1),
            "course_number": m.group(2),
            "title": m.group(3).strip(),
            "units": units,
            "prerequisites": prereq or None,
            "offered": offered,
            "repeatable": repeatable,
            "cross_listed": cross_listed,
            "description": desc_block.strip()
        })
    return courses

def parse_course_catalog(filename):
    courses = []
    with open(filename, 'r', encoding='utf-8') as f:
        lines = [l.rstrip('\n') for l in f]

    course_start_pat = re.compile(r'^([A-Z]{2,4})\s+(\d{3}[A-Z]?)\.\s+')
    block = ""
    in_block = False

    for line in lines + [""]:  # Sentinel to flush last block
        if is_header(line):
            continue
        if course_start_pat.match(line) and in_block:
            # Process the previous block (could be multi-course)
            courses += extract_courses_from_block(block)
            block = line
        else:
            block += ("\n" + line) if block else line
            in_block = True
    # Final flush
    if block.strip():
        courses += extract_courses_from_block(block)

    return courses

# --- MAIN ---
filename = "course.txt"
catalog_json = parse_course_catalog(filename)

with open("courses_output_lossless.json", "w", encoding="utf-8") as out:
    json.dump(catalog_json, out, indent=2, ensure_ascii=False)

print(f"Extracted {len(catalog_json)} courses to courses_output_lossless.json")

