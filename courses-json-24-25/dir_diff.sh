
#!/bin/bash

# Usage: ./dir_diff.sh /path/to/other/dir

if [ $# -ne 1 ]; then
    echo "Usage: $0 <directory>"
    exit 1
fi

otherdir="$1"

if [ ! -d "$otherdir" ]; then
    echo "Error: '$otherdir' is not a directory"
    exit 1
fi

# Compare recursively, ignoring hidden .git noise
colordiff -r . "$otherdir"

