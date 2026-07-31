import csv
import os

class CSVLogger:
    def __init__(self, output_dir = "expert_data"):
        self.output_dir = output_dir
        self.filename = None
        self.writer = None
        self.csv_file = None
        self.row_count = 0
        self._create_file()

    def _create_file(self):
        # Automatically create the data directory if it does not exist
        os.makedirs(self.output_dir, exist_ok = True)

        self.filename = os.path.join(self.output_dir, "expert_data.csv")

        # Open the file for writing (newline='' prevents extra blank lines on Windows)
        self.csv_file = open(self.filename, mode = 'w', newline = '', encoding = 'utf-8')

    def log_row(self, data_dict):
        """Receive cleaned dictionary data and write it as a row into the CSV in real-time."""
        if not data_dict:
            return

        # Automatically extract dictionary keys as the header row upon the first data entry
        if self.writer is None:
            fieldnames = list(data_dict.keys())
            self.writer = csv.DictWriter(self.csv_file, fieldnames = fieldnames)
            self.writer.writeheader()

        self.writer.writerow(data_dict)
        self.csv_file.flush()
        self.row_count += 1

    def close(self):
        """Safely close the CSV file.""" 
        if self.csv_file:
            self.csv_file.close()
            if self.row_count < 50:
                if os.path.exists(self.filename):
                    os.remove(self.filename)
                print(f"Too few data, file deleted")
                return
            print(f"Data saved successfully and safely!")
            print(f"Absolute file path: {os.path.abspath(self.filename)}")