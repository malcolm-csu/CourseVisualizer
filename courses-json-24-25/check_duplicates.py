import json
import sys
from collections import Counter

def check_duplicates(filename):
    try:
        # Load JSON data from the provided file
        with open(filename, 'r') as file:
            data = json.load(file)

        # Check for duplicates based on data structure
        if isinstance(data, list):
            if all(isinstance(item, dict) for item in data):
                # For a list of dictionaries, check for duplicate dictionaries
                # based on all key-value pairs
                items_as_strings = [json.dumps(item, sort_keys=True) for item in data]
                duplicates = [item for item, count in Counter(items_as_strings).items() if count > 1]
                if duplicates:
                    print("Duplicate dictionaries found:")
                    for duplicate in duplicates:
                        print(json.loads(duplicate))
                else:
                    print("No duplicate dictionaries found.")
            else:
                # For a simple list, check for duplicate values
                duplicates = [item for item, count in Counter(data).items() if count > 1]
                if duplicates:
                    print("Duplicate values found:", duplicates)
                else:
                    print("No duplicate values found.")

        elif isinstance(data, dict):
            # For a dictionary, check each list under a key for duplicates if it's a list
            has_duplicates = False
            for key, value in data.items():
                if isinstance(value, list):
                    if all(isinstance(item, dict) for item in value):
                        # Check for duplicate dictionaries within lists in dictionary values
                        items_as_strings = [json.dumps(item, sort_keys=True) for item in value]
                        duplicates = [item for item, count in Counter(items_as_strings).items() if count > 1]
                        if duplicates:
                            has_duplicates = True
                            print(f"Duplicate dictionaries found in list under key '{key}':")
                            for duplicate in duplicates:
                                print(json.loads(duplicate))
                    else:
                        # Check for duplicate values within lists in dictionary values
                        duplicates = [item for item, count in Counter(value).items() if count > 1]
                        if duplicates:
                            has_duplicates = True
                            print(f"Duplicate values found in list under key '{key}':", duplicates)

            if not has_duplicates:
                print("No duplicates found in any dictionary lists.")

        else:
            print("Unsupported JSON structure. Only lists or dictionaries are supported.")

    except json.JSONDecodeError as e:
        print(f"Error loading JSON: {e}")

if __name__ == "__main__":
    # Check if the user provided a filename
    if len(sys.argv) < 2:
        print("Please provide the JSON file name as an argument.")
    else:
        filename = sys.argv[1]
        check_duplicates(filename)

