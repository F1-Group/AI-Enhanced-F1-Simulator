import threading
from pynput import keyboard

class InputHandler():

    GEAR_MAX = 6
    GEAR_MIN = -1
    
    def __init__(self):
        self.state = {
            'accel': 0.0, # 0.0 ~ 1.0
            'brake': 0.0, # 0.0 ~ 1.0
            'steer': 0.0, # -1.0 ~ 1.0
            'gear':  1, # -1 ~ 6
        }
        self._thread = None
        self._running = False
        self._listener = None

        self.accel_pressed = False
        self.brake_pressed = False
        self.steer_left_pressed = False
        self.steer_right_pressed = False


    def shift_up(self):
        if self.state['gear'] < self.GEAR_MAX:
            self.state['gear'] += 1

    def shift_down(self):
        if self.state['gear'] > self.GEAR_MIN:
            self.state['gear'] -= 1

    def _listen(self):
        with keyboard.Listener(on_press=self._on_press, on_release=self._on_release) as listener:
            self._listener = listener
            listener.join()

    def start(self):
        self._running = True
        # Background thread that continuously listens for input and updates state
        self._thread = threading.Thread(target=self._listen, daemon=True)
        self._thread.start()

    def stop(self):
        self._running = False
        if self._listener is not None:
            self._listener.stop()
        if self._thread is not None:
            self._thread.join(timeout=1.0)

    def _on_press(self, key):
        try:
            if key == keyboard.Key.up:
                self.accel_pressed = True
            elif key == keyboard.Key.down:
                self.brake_pressed = True
            elif key == keyboard.Key.right:
                self.steer_right_pressed = True
            elif key == keyboard.Key.left:
                self.steer_left_pressed = True
            elif key.char.lower() == 'a':
                self.shift_up()
            elif key.char.lower() == 'z':
                self.shift_down()
        except AttributeError:
            pass

    def _on_release(self, key):
        try:
            if key == keyboard.Key.up:
                self.accel_pressed = False
            elif key == keyboard.Key.down:
                self.brake_pressed = False
            elif key == keyboard.Key.left:
                self.steer_left_pressed = False
            elif key == keyboard.Key.right:
                self.steer_right_pressed = False
        except AttributeError:
            pass