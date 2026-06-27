

import json
import sys
import os

def remove_duplicates(filename):
    try:
        # Attempt to load JSON data from the provided file
        with open(filename, 'r') as file:
            data = json.load(file)

        # Print the type of data at the root level for troubleshooting
        print("Data type at root level:", type(data))

        # Handling based on root data type
        if isinstance(data, list):
            # For a list of dictionaries or a simple list
            if all(isinstance(item, dict) for item in data):
                # Remove duplicates in a list of dictionaries based on all key-value pairs
                unique_data = {json.dumps(item, sort_keys=True): item for item in data}.values()
                unique_data = list(unique_data)
            else:
                # For a simple list, remove duplicates directly
                unique_data = list(set(data))

        elif isinstance(data, dict):
            # If the root is a dictionary, process it based on keys
            # Here we assume we want unique values in each list stored within the dictionary
            unique_data = {}
            for key, value in data.items():
                if isinstance(value, list):
                    # Remove duplicates from each list
                    if all(isinstance(item, dict) for item in value):
                        # Remove duplicates within list of dictionaries for each key
                        unique_data[key] = list({json.dumps(item, sort_keys=True): item for item in value}.values())
                    else:
                        # For simple lists within a dictionary, remove duplicates
                        unique_data[key] = list(set(value))
                else:
                    unique_data[key] = value

        else:
            print("Unsupported JSON structure. Please ensure the JSON is a list or dictionary.")
            return

        # Save the cleaned data to a new file
        base, ext = os.path.splitext(filename)
        cleaned_filename = f"cleaned_{os.path.basename(base)}.json"
        with open(cleaned_filename, 'w') as file:
            json.dump(unique_data, file, indent=4)

        print(f"Duplicates removed. Cleaned data saved to {cleaned_filename}")

    except json.JSONDecodeError as e:
        print(f"Error loading JSON: {e}")

if __name__ == "__main__":
    # Check if the user provided a filename
    if len(sys.argv) < 2:
        print("Please provide the JSON file name as an argument.")
    else:
        filename = sys.argv[1]
        remove_duplicates(filename)

