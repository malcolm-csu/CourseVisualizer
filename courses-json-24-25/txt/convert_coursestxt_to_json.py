import re
import json
from pathlib import Path

# Load the course catalog text
file_path = Path("csc_course_24-25.txt")
text = file_path.read_text()

# Pattern to match course entries
course_pattern = re.compile(
    r"(CSC\d{3}[A-Z]?)\.\s+(.*?)\.\s+\((\d+(?:-\d+)?(?: Units)?)\)\s+(.*?)"
    r"(?=(?:\n[A-Z]{3}\d{3}|$))", re.DOTALL
)

courses = {}

for match in course_pattern.finditer(text):
    code, title, units_text, body = match.groups()
    
    # Normalize and extract units
    unit_match = re.search(r"(\d+)(?:-(\d+))?", units_text)
    if unit_match:
        if unit_match.group(2):
            units = list(range(int(unit_match.group(1)), int(unit_match.group(2)) + 1))
        else:
            units = int(unit_match.group(1))
    else:
        units = None

    # Extract prerequisites from body if present
    prereq_match = re.search(r"Prerequisite[s]*: (.*?)\.", body)
    prerequisites = []
    if prereq_match:
        prereq_text = prereq_match.group(1)
        prerequisites = [p.strip() for p in re.split(r",| and ", prereq_text)]

    # Extract offering terms
    offered_match = re.search(r"Offered ([^\n.]+)", body)
    offered = offered_match.group(1).strip() if offered_match else ""

    # Clean description by removing known metadata fields
    description = re.sub(r"Prerequisite[s]*:.*?\.", "", body)
    description = re.sub(r"Offered [^\n.]+", "", description).strip()

    # Construct course entry
    courses[code.replace(" ", "")] = {
        "title": title.strip(),
        "units": units,
        "prerequisites": prerequisites,
        "offered": offered,
        "description": description
    }

# Save to JSON file
output_path = "CS_Course_from_catalog.json"
with open(output_path, "w") as f:
    json.dump(courses, f, indent=4)

output_path

