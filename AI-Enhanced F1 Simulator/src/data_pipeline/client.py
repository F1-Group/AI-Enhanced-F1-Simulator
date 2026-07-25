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
        self.has_passed_line = False
        self.controller = Controller(handler)
        self._status = None
        self.status = GameStatus.CONNECTING

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
        # Allows socket port reuse to prevent "Address already in use" errors when resources aren't freed
        self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.socket.settimeout(TIME_OUT)

    def _handshake(self):
        initmsg='SCR(init -90.0 -75.0 -50.0 -35.0 -20.0 -15.0 -10.0 -5.0 -1.0 0.0 1.0 5.0 10.0 15.0 20.0 35.0 50.0 75.0 90.0)'
        
        MAX_HANDSHAKE_WAIT = 10.0
        start_time = time.time()

        print(f'[Client] Connecting to TORCS on {HOST}:{PORT}...')

        while (time.time() - start_time) < MAX_HANDSHAKE_WAIT:
            try:
                self.socket.sendto(initmsg.encode(), (HOST, PORT))
                response, _ = self.socket.recvfrom(4096)

                if b'***identified***' in response:
                    print('[Client] Handshake successful!')
                    self.status = GameStatus.RACING
                    return
            except socket.timeout:
                print("[Client Warning] No response from TORCS, retrying handshake...")
                time.sleep(0.2)
            except Exception as e:
                print(f"[Client Error] Handshake socket error: {e}")
                break

        self.status = GameStatus.ERROR
        print("[Client Error] Failed to connect to TORCS (Handshake Timeout)")

    def _build_action(self, state: dict) -> str:
        commands = [
            f"(accel {state['accel']:.3f})",
            f"(brake {state['brake']:.3f})",
            f"(steer {state['steer']:.3f})",
            f"(gear {state['gear']})",
            f"(focus 360)",
            f"(meta 0)"
        ]
        return ' '.join(commands)
 
    def _loop(self):
        # Clear any remaining UDP buffer data from the handshake stage
        self.socket.setblocking(False)
        try:
            while True:
                self.socket.recvfrom(4096)
        except BlockingIOError:
            pass

        self.socket.settimeout(TIME_OUT)

        last_successful_packet_time = time.time()
        self.has_passed_line = False

        while self.status == GameStatus.RACING:
            try:
                raw_data, _ = self.socket.recvfrom(4096)

                now = time.time()
                delta = now - last_successful_packet_time
                last_successful_packet_time = now

                raw_data = raw_data.decode('utf-8')

                if "***shutdown***" in raw_data or "***restart***" in raw_data:
                    print("[Client] Race ended by TORCS signal.")
                    self.status = GameStatus.FINISHED
                    break

                cleaned_packet = clean_my_data(raw_data)
                speed = cleaned_packet.get('speed_kmh', 0)

                self.handler.update()

                self.controller.update_accel(delta, speed)
                self.controller.update_brake(delta)
                self.controller.update_steer(delta, speed)
                
                state = self.handler.state

                current_lap_time = cleaned_packet.get('lap_time', 0.0)
                current_lap_dist = cleaned_packet.get('lap_distance', 0.0)

                if current_lap_time < 0.0:
                    action_msg = self._build_action(state)
                    self.socket.sendto(action_msg.encode(), (HOST, PORT))
                    continue

                if not self.has_passed_line and current_lap_dist > 5000.0:
                    cleaned_packet['lap_distance'] = 0.0

                if 0.0 < current_lap_dist < 100.0:
                    self.has_passed_line = True

                cleaned_packet['throttle'] = state['accel']
                cleaned_packet['brake'] = state['brake']
                cleaned_packet['steer'] = state['steer']

                self.cache.update_telemetry(cleaned_packet)
                self.logger.log_row(cleaned_packet)
                
                action_msg = self._build_action(state)
                self.socket.sendto(action_msg.encode(), (HOST, PORT))

            except socket.timeout:
                idle_duration = time.time() - last_successful_packet_time
                print(f"[Client Warning] UDP Socket timeout, no packet for {idle_duration:.1f}s...")

                if idle_duration >= 5.0:
                    print("[Client Error] Connection Lost: TORCS stopped sending data for >5 seconds.")
                    if hasattr(self, 'cache') and self.cache:
                        self.cache.update_telemetry(None)
                    self.status = GameStatus.ERROR
                    break
                else:
                    continue

    def _clean_up(self):
        if hasattr(self, "logger") and self.logger:
            try:
                self.logger.close()
                print("[Client] The logger has been safely closed.")
            except Exception as e:
                print(f"[Client Error] Error closing logger: {e}")

        if hasattr(self, "handler") and self.handler:
            try:
                self.handler.stop()
            except Exception as e:
                print(f"[Client Error] Error stopping handler: {e}")

    def start(self):
        self.status = GameStatus.CONNECTING

        try:
            self._create_socket()
            self._handshake()
            
            if self.status == GameStatus.RACING:
                self.handler.start()
                self.logger.create_file()
                self._loop()
            else:
                print("[Client] Handshake failed, skipping race loop.")

        except KeyboardInterrupt:
            print("[Client] Keyboard interrupt. Lost connection to TORCS.")
            self.status = GameStatus.ERROR
        except Exception as e:
            print(f"[Client Exception] Unexpected error in Client: {e}")
            self.status = GameStatus.ERROR
        finally:
            self._clean_up()
            if self.socket:
                try:
                    self.socket.close()
                    self.socket = None
                    print("[Client] Socket safely closed.")
                except Exception as e:
                    print(f"[Client Error] Error closing socket: {e}")

    def stop(self):
        if self.status not in (GameStatus.FINISHED, GameStatus.ERROR):
            self.status = GameStatus.FINISHED
            
        print(f"[Client] Stopping... current status: {self.status}")
        self._clean_up()
        
        if hasattr(self, "socket") and self.socket:
            try:
                self.socket.close()
                self.socket = None
                print("[Client] Socket safely closed.")
            except Exception as e:
                print(f"[Client Warning] Error closing socket: {e}")