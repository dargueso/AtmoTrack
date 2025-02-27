for file in extracted_*.nc; do
    # Remove the "extracted_" prefix and ".nc" suffix to isolate the date
    date=${file#extracted_}
    date=${date%.nc}
    
    # Pass the date to your Python script
    python plot_z500_t850_DANAS.py "$date"
done
