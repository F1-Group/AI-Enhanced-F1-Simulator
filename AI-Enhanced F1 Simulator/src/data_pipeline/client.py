import socket
from parser import clean_my_data
from cache import cache

HOST = '127.0.0.1'
PORT = 3001
TIME_OUT = 3.0
MAX_RETRIES = 3

class Client:
    def __init__(self, handler, logger):
        self.handler = handler
        self.logger = logger
        self.socket = None
        self.connected = False
    
    def _create_socket(self):
        self.socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.socket.settimeout(TIME_OUT)

    def _handshake(self):
        initmsg='SCR(init -45 -19 -12 -7 -4 -2.5 -1.7 -1 -.5 0 .5 1 1.7 2.5 4 7 12 19 45)'
        retries = 0
        
        while retries < MAX_RETRIES:
            print(f'Connecting to TORCS on {HOST}:{PORT}...')
            self.socket.sendto(initmsg.encode(), (HOST,PORT))
            try:
                response, _ = self.socket.recvfrom(4096)
                if b'***identified***' in response:
                    print('Handshake successful')
                    self.connected = True
                    return
            except TimeoutError:
                retries += 1
                print(f'No response, retrying ({retries}/{MAX_RETRIES})...')
            except KeyboardInterrupt:
                print("Keyboard interrupt. Lost connection to TORCS.")

        print('Failed to connect to TORCS')

    def _build_action(self, state: dict) -> str:
        return (
            f"(accel {state['accel']:.3f})"
            f"(brake {state['brake']:.3f})"
            f"(steer {state['steer']:.3f})"
            f"(gear {state['gear']})"
            f"(focus 0)"
            f"(meta 0)"
        )
 
    def _loop(self):
        retries = 0
        while self.connected:
            try:
                raw_data, _ = self.socket.recvfrom(4096)
                self.handler.poll()
                raw_data = raw_data.decode('utf-8')

                # Check for end of race
                if '***shutdown***' in raw_data or '***restart***' in raw_data:
                    print('Race ended')
                    self.connected = False
                    break

                cleaned_packet = clean_my_data(raw_data)
                cache.write(cleaned_packet)
                self.logger.log_row(cleaned_packet)
                # Read human input
                state = self.handler.state
                
                # Send action back to TORCS
                action_msg = self._build_action(state)
                self.socket.sendto(action_msg.encode(), (HOST, PORT))

                retries = 0

            except TimeoutError:
                retries += 1
                print(f'Timeout ({retries}/{MAX_RETRIES})...')
                if retries >= MAX_RETRIES:
                    print('Lost connection to TORCS')
                    self.connected = False
            except KeyboardInterrupt:
                print("Keyboard interrupt. Lost connection to TORCS.")
                self.connected = False

    def start(self):
        self._create_socket()
        self._handshake()
        if self.connected:
            self._loop()
        self.socket.close()
        self.logger.close()