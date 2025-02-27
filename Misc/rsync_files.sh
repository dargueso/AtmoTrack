#!/bin/bash

# --- Step 1: Gather Files from YYYYMMDD Directories ---

# Create the common folder if it doesn't exist
mkdir -p allyears

# Loop over directories with names matching 8 digits (YYYYMMDD)
for dir in [0-9][0-9][0-9][0-9][0-9][0-9][0-9][0-9]; do
    if [ -d "$dir" ]; then
        echo "Moving files from directory: $dir"
        # Move all files in this directory to allyears (suppress errors if directory is empty)
        mv "$dir"/* allyears/ 2>/dev/null
    fi
done

# --- Step 2: For Each File in Extreme_events_Med, Find and Sync from allyears ---

# Create the destination folder if it doesn't exist
mkdir -p new_folder

# Loop over each file in Extreme_events_Med with the expected naming pattern
for file in Extreme_events_Med/z500_t850_*.png; do
    # Extract the basename (e.g., z500_t850_2019102212.png)
    filename=$(basename "$file")
    echo "Processing $filename..."

    # Search for the same filename in allyears using find
    found=$(find allyears -type f -name "$filename" 2>/dev/null)

    if [ -n "$found" ]; then
        echo "Found $filename in allyears. Syncing to new_folder..."
        # Use rsync to copy the file into new_folder
        rsync -av "$found" new_folder/
    else
        echo "File $filename not found in allyears."
    fi
done

