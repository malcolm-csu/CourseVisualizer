import json
import re

def load_json(filename):
    with open(filename, 'r', encoding='utf-8') as f:
        return json.load(f)

def extract_course_codes(requirements_json):
    # Traverse all degrees, all lists, and grab course codes like "CSC 321", "ITC 300", etc.
    codes = set()
    def extract(obj):
        if isinstance(obj, dict):
            for v in obj.values():
                extract(v)
        elif isinstance(obj, list):
            for item in obj:
                extract(item)
        elif isinstance(obj, str):
            # Match "ABC 123" or "ABC 123A", not "3 units" or other stuff
            matches = re.findall(r'\b([A-Z]{2,4}\s?\d{3}[A-Z]?)\b', obj)
            for m in matches:
                codes.add(m.replace(' ', ''))
    extract(requirements_json)
    return codes

def normalize_code(code):
    # Remove spaces for matching ("CSC 321" -> "CSC321")
    return code.replace(' ', '').upper()

# --- MAIN ---
reqs = load_json('CSCTIT_24-25.json')
all_courses = load_json('courses.json')

required_codes = set(normalize_code(code) for code in extract_course_codes(reqs))
filtered_courses = [
    c for c in all_courses
    if normalize_code(f"{c.get('department','')}{c.get('course_number','')}") in required_codes
]

with open("required_courses_only.json", "w", encoding="utf-8") as out:
    json.dump(filtered_courses, out, indent=2, ensure_ascii=False)

print(f"Exported {len(filtered_courses)} required courses to required_courses_only.json")

