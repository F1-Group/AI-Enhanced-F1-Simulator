import csv
import os
from datetime import datetime

class CSVLogger:
    def __init__(self, output_dir = "data"):
        self.output_dir = output_dir
        self.writer = None
        self.csv_file = None
        self.row_count = 0
        
        # Automatically create the data directory if it does not exist
        os.makedirs(self.output_dir, exist_ok = True)
        
        # Dynamically name the file using the current timestamp, e.g., telemetry_20260620_170530.csv
        current_time = datetime.now().strftime("%Y%m%d_%H%M%S")
        self.filename = os.path.join(self.output_dir, f"telemetry_{current_time}.csv")
        
        # Open the file for writing (newline='' prevents extra blank lines on Windows)
        self.csv_file = open(self.filename, mode = 'w', newline = '', encoding = 'utf-8')
        print(f"CSV Logger is ready. Absolute file path: {os.path.abspath(self.filename)}")

    def log_row(self, data_dict):
        """Receive cleaned dictionary data and write it as a row into the CSV in real-time."""
        if not data_dict:
            return
            
        # Automatically extract dictionary keys as the header row upon the first data entry
        if self.writer is None:
            fieldnames = list(data_dict.keys())
            self.writer = csv.DictWriter(self.csv_file, fieldnames = fieldnames)
            self.writer.writeheader()
            
        # Write the data for the current frame
        self.writer.writerow(data_dict)
        self.row_count += 1
        
        # Update log on the same terminal line for real-time feedback without cluttering the screen
        print(f" [LOG] Recording... Rows written: {self.row_count} (Timeline: {data_dict.get('curLapTime', 0)}s)", end = "\r")

    def close(self):
        """Safely close the CSV file.""" 
        if self.csv_file:
            self.csv_file.close()
            print(f"\n Data saved successfully and safely!")
            print(f"Absolute file path: {os.path.abspath(self.filename)}")