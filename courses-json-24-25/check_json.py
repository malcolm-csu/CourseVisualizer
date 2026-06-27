import json
import sys

def validate_json(file_path):
    try:
        with open(file_path, 'r') as f:
            json.load(f)
        print(f"{file_path}: JSON is valid.")
    except json.JSONDecodeError as e:
        print(f"{file_path}: Invalid JSON - {e}")
    except FileNotFoundError:
        print(f"{file_path}: File not found.")

if __name__ == "__main__":
    if len(sys.argv) != 2:
        print("Usage: python validate_json.py <filename>")
        sys.exit(1)
    filename = sys.argv[1]
    validate_json(filename)

