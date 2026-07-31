import socket
from parser import clean_my_data
from logger import CSVLogger

HOST = '127.0.0.1'
PORT = 3002
TIME_OUT = 20.0

class Client:
    def __init__(self):
        self.socket = None
        self.logger = CSVLogger()
        self.has_passed_line = False

    def _create_socket(self):
        self.socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.socket.bind((HOST, PORT))
        self.socket.settimeout(TIME_OUT)
        print("Waiting for data at port 3002...")
 
    def _loop(self):
        try:
            while True:
                raw_data, _ = self.socket.recvfrom(4096)
                raw_data = raw_data.decode('utf-8')

                cleaned_packet = clean_my_data(raw_data)
                current_lap_time = cleaned_packet.get('lap_time', 0.0)
                current_lap_dist = cleaned_packet.get('lap_distance', 0.0)

                if current_lap_time < 0.0:
                    continue

                if not self.has_passed_line and current_lap_dist > 5000.0:
                    cleaned_packet['lap_distance'] = 0.0

                if 0.0 < current_lap_dist < 100.0:
                    self.has_passed_line = True

                self.logger.log_row(cleaned_packet)

        except socket.timeout:
            print("Lost connection.")
        except KeyboardInterrupt:
            print("Keyboard interrupt.")
        finally:
            self._clean_up()


    def _clean_up(self):
        if hasattr(self, "logger") and self.logger:
            try:
                self.logger.close()
                print("The logger has been safely closed.")
            except Exception as e:
                print(f"Unexpected error occurred while closing the logger: {e}")
        if hasattr(self, "socket") and self.socket:
                self.socket.close()
                print("The socket connection has been safely released.")


if __name__ == '__main__':
    client = Client()
    client._create_socket()
    client._loop()


