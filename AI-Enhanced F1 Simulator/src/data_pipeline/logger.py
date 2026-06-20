import csv
import os
from datetime import datetime

class CSVLogger:
    def __init__(self, output_dir="data"):
        self.output_dir = output_dir
        self.writer = None
        self.csv_file = None
        self.row_count = 0
        
        # 自動建立 data 資料夾（如果不存在的話）
        os.makedirs(self.output_dir, exist_ok=True)
        
        # 用當下的時間來動態命名，例如 telemetry_20260620_170530.csv
        current_time = datetime.now().strftime("%Y%m%d_%H%M%S")
        self.filename = os.path.join(self.output_dir, f"telemetry_{current_time}.csv")
        
        # 開啟檔案準備寫入（newline='' 避免 Windows 系統多出空行）
        self.csv_file = open(self.filename, mode='w', newline='', encoding='utf-8')
        print(f"📊 CSV 紀錄器已就緒，檔案將儲存至：{self.filename}")

    def log_row(self, data_dict):
        """接收清洗後的字典資料，並即時寫入 CSV 的一列"""
        if not data_dict:
            return
            
        # 第一次寫入資料時，自動抓取字典的 keys 當作第一行的欄位標題（Header）
        if self.writer is None:
            fieldnames = list(data_dict.keys())
            self.writer = csv.DictWriter(self.csv_file, fieldnames=fieldnames)
            self.writer.writeheader()
            
        # 寫入這幀的數據
        self.writer.writerow(data_dict)
        self.row_count += 1
        
        # 在終端機同一行更新 log，既有即時感又不會洗板
        print(f" [LOG] 錄製中... 已寫入 {self.row_count} 筆 (時間軸: {data_dict.get('curLapTime', 0)} 秒)", end="\r")

    def close(self):
        """安全關閉 CSV 檔案"""
        if self.csv_file:
            self.csv_file.close()
            print(f"\n💾 數據已成功安全儲存！")
            print(f"📂 檔案絕對路徑：{os.path.abspath(self.filename)}")