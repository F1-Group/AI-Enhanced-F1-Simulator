import socket
from data_pipeline.parser import clean_my_data
from data_pipeline.cache import GameStatus
from data_pipeline.controller import Controller
import time

HOST = '127.0.0.1'
PORT = 3001
TIME_OUT = 1.0

class Client:
    def __init__(self, handler, logger, cache):
        self.handler = handler
        self.logger = logger
        self.socket = None
        self.cache = cache
        self._status = None
        self.controller = Controller(handler)

    # Getter: Safely expose the current status for internal read-only access
    @property
    def status(self):
        return self._status

    # Setter: Intercept state changes to trigger automatic synchronization
    @status.setter
    def status(self, new_status):
        if self._status != new_status:
            self._status = new_status

            if hasattr(self, 'cache') and self.cache:
                self.cache.set_status(new_status)
    
    def _create_socket(self):
        self.socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.socket.settimeout(TIME_OUT)

    def _handshake(self):
        initmsg='SCR(init -45 -19 -12 -7 -4 -2.5 -1.7 -1 -.5 0 .5 1 1.7 2.5 4 7 12 19 45)'
        self.status = GameStatus.CONNECTING
        
        MAX_HANDSHAKE_WAIT = 120.0
        start_time = time.time()

        while (time.time() - start_time) < MAX_HANDSHAKE_WAIT:
            print(f'Connecting to TORCS on {HOST}:{PORT}...')
            self.socket.sendto(initmsg.encode(), (HOST,PORT))
            try:
                response, _ = self.socket.recvfrom(4096)

                if b'***identified***' in response:
                    print('Handshake successful')
                    self.status = GameStatus.RACING
                    return
            except socket.timeout:
                print("No response from TORCS, retrying...")
        self.status = GameStatus.ERROR
        print("Failed to connect to TORCS")

    def _build_action(self, state: dict) -> str:
        commands = [
            f"(accel {state['accel']:.3f})",
            f"(brake {state['brake']:.3f})",
            f"(steer {state['steer']:.3f})",
            f"(gear {state['gear']})",
            f"(focus 0)",
            f"(meta 0)"
        ]
        return ' '.join(commands)
 
    def _loop(self):
        self.socket.setblocking(False)
        try:
            while True:
                self.socket.recvfrom(4096)
        except BlockingIOError:
            pass

        self.socket.settimeout(TIME_OUT)
        last_time = time.time()
        has_passed_line = False
        while self.status == GameStatus.RACING:
            try:
                raw_data, _ = self.socket.recvfrom(4096)

                # Calculate delta
                now = time.time()
                delta = now - last_time
                last_time = now

                raw_data = raw_data.decode('utf-8')

                # Check for end of race
                if "***shutdown***" in raw_data or "***restart***" in raw_data:
                    print("Race ended")
                    self.status = GameStatus.FINISHED
                    break

                cleaned_packet = clean_my_data(raw_data)

                speed = cleaned_packet.get('speedX', 0)
                # Update human input state
                self.controller.update_accel(delta, speed)
                self.controller.update_brake(delta)
                self.controller.update_steer(delta, speed)
                
                # Read human input state
                state = self.handler.state

                current_lap_time = cleaned_packet.get('lap_time', 0.0)
                current_lap_dist = cleaned_packet.get('lap_distance', 0.0)

                if current_lap_time < 0.0:
                    action_msg = self._build_action(state)
                    self.socket.sendto(action_msg.encode(), (HOST, PORT))
                    last_time = time.time()
                    continue

                if not has_passed_line and current_lap_dist > 5000.0:
                    cleaned_packet['lap_distance'] = 0.0

                if current_lap_dist > 0.0 and current_lap_dist < 100.0:
                    has_passed_line = True

                self.cache.update_telemetry(cleaned_packet)

                cleaned_packet['throttle'] = state['accel']
                cleaned_packet['brake'] = state['brake']
                cleaned_packet['steer'] = state['steer']
                self.logger.log_row(cleaned_packet)
                
                # Send action back to TORCS
                action_msg = self._build_action(state)
                self.socket.sendto(action_msg.encode(), (HOST, PORT))

            except socket.timeout:
                time_since_last_packet = time.time() - last_time
                if time_since_last_packet >= 10.0:
                    print("Lost connection to TORCS.")
                    self.status = GameStatus.ERROR
                    break
                else:
                    continue
    
    def _clean_up(self):
        if hasattr(self, "logger") and self.logger:
            try:
                self.logger.close()
            except Exception as e:
                print(f"Unexpected error occurred while closing the logger: {e}")

        if hasattr(self, "handler") and self.handler:
            try:
                self.handler.stop()
            except Exception as e:
                print(f"Unexpected error occurred while stopping the handler: {e}")

    
    def start(self):
        try:
            self._create_socket()
            self._handshake()
            if self.status == GameStatus.RACING:
                self.handler.start()
                self.logger.create_file()
                self._loop()
        except KeyboardInterrupt:
            self.status = GameStatus.ERROR
            print("Keyboard interrupt. Lost connection to TORCS.")
        except Exception as e:
            self.status = GameStatus.ERROR
            print(f"Unexpected error: {e}")
        finally:
            self.stop()

    def stop(self):
        if self.status != GameStatus.FINISHED:
            self.status = GameStatus.ERROR
        print(self.status)
        self._clean_up()
        if hasattr(self, "socket") and self.socket:
                self.socket.close()
                print("The socket connection has been safely released.")


