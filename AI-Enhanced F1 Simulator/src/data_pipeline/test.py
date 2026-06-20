import socket
# 引入你寫好的數據清洗與剛剛新做的 CSV 紀錄器
from parser import clean_my_data
from logger import CSVLogger  

def start_listener():
    # 1. 初始化網路連線
    UDP_IP = "127.0.0.1"
    UDP_PORT = 3001
    
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.bind((UDP_IP, UDP_PORT))
    
    # 2. 實例化 CSV 紀錄器（這會自動建立 data/ 資料夾與新 CSV 檔）
    logger = CSVLogger()
    
    print(f"🏎️ TORCS 數據監聽器已啟動（正在監聽 Port {UDP_PORT}）...")
    print(f"💡 提示：比賽結束後，切換回這裡按 Ctrl + C 即可安全結束錄製。")
    print("-" * 50)
    
    try:
        while True:
            # 3. 接收來自 TORCS 的原始 UDP 封包
            data, addr = sock.recvfrom(1024)
            raw_packet = data.decode('utf-8')
            
            # 4. 呼叫大腦（parser.py）進行清洗
            cleaned_dict = clean_my_data(raw_packet)
            
            # 5. 呼叫紀錄器（csv_logger.py）寫入硬碟
            if cleaned_dict:
                logger.log_row(cleaned_dict)
                
    except KeyboardInterrupt:
        print(f"\n🛑 偵測到手動停止訊號（Ctrl+C）...")
    finally:
        # 6. 無論程式如何結束，最後都必須強迫檔案正確關閉封口
        logger.close()

if __name__ == "__main__":
    start_listener()