#!/bin/bash

CSV_FILE="above400mmevents.csv"

# Ensure the CSV file exists
if [ ! -f "$CSV_FILE" ]; then
  echo "CSV file '$CSV_FILE' not found!"
  exit 1
fi

# Read the CSV file line by line, skipping the header.
tail -n +2 "$CSV_FILE" | while IFS=, read -r date depth location; do
  # Remove potential quotes from the location field
  location=$(echo "$location" | tr -d '"')

  # Extract the year from the date (assumes date format is YYYY-MM-DD)
  year=$(echo "$date" | cut -d'-' -f1)

  # Calculate one day before and one day after the date using GNU date
  start_date=$(date -I -d "$date - 1 day")
  end_date=$(date -I -d "$date + 1 day")
  
  # Define the input and output file names
  input_file="data_tracking/era5_daily_col_z500_${year}.nc"
  output_file="extracted_${date}.nc"
  
  echo "Processing date: $date"
  echo "  Year: $year"
  echo "  Date range: $start_date to $end_date"
  echo "  Input file: $input_file"
  echo "  Output file: $output_file"

  # Run the CDO command to extract the range
  cdo seldate,${start_date},${end_date} "$input_file" "$output_file"
done

