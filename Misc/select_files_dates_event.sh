#!/bin/bash

# Ensure correct number of arguments
if [ "$#" -ne 2 ]; then
  echo "Usage: $0 <start_date> <end_date>"
  echo "Date format: YYYY-MM-DD"
  exit 1
fi

START_DATE=$1
END_DATE=$2

# Validate date format
if ! date -d "$START_DATE" >/dev/null 2>&1 || ! date -d "$END_DATE" >/dev/null 2>&1; then
  echo "Error: Invalid date format. Use YYYY-MM-DD."
  exit 1
fi

# Ensure start_date is before end_date
if [[ "$START_DATE" > "$END_DATE" ]]; then
  echo "Error: start_date must be before or equal to end_date."
  exit 1
fi

# Extract year from start_date (assumes all dates are in the same year)
YEAR=$(echo "$START_DATE" | cut -d'-' -f1)

# Define the input and output file names
INPUT_FILE="data_tracking/era5_daily_col_z500_${YEAR}.nc"
OUTPUT_FILE="extracted_${START_DATE}_to_${END_DATE}.nc"

echo "Extracting data from $START_DATE to $END_DATE"
echo "  Year: $YEAR"
echo "  Input file: $INPUT_FILE"
echo "  Output file: $OUTPUT_FILE"

# Run the CDO command to extract the date range
cdo seldate,${START_DATE},${END_DATE} "$INPUT_FILE" "$OUTPUT_FILE"

echo "Extraction complete."
