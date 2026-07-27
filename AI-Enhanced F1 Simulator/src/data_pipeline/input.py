import multiprocessing as mp

def _pynput_listener_process(event_queue):
    # Runs as a separate background process to handle pynput keyboard listening.
    # Completely isolates it from the main Tkinter thread to 
    # avoid multithreading conflicts and crashes.
    from pynput import keyboard

    press_state = {
        'up': False,
        'down': False,
        'left': False,
        'right': False,
        'shift_up': False,
        'shift_down': False,
    }

    def on_press(key):
        try:
            if key == keyboard.Key.up:
                press_state['up'] = True
            elif key == keyboard.Key.down:
                press_state['down'] = True
            elif key == keyboard.Key.left:
                press_state['left'] = True
            elif key == keyboard.Key.right:
                press_state['right'] = True
            elif hasattr(key, 'char') and key.char is not None:
                char = key.char.lower()
                if char == 'a':
                    press_state['shift_up'] = True
                elif char == 'z':
                    press_state['shift_down'] = True
            event_queue.put(press_state.copy())
        except Exception:
            pass

    def on_release(key):
        try:
            if key == keyboard.Key.up:
                press_state['up'] = False
            elif key == keyboard.Key.down:
                press_state['down'] = False
            elif key == keyboard.Key.left:
                press_state['left'] = False
            elif key == keyboard.Key.right:
                press_state['right'] = False
            elif hasattr(key, 'char') and key.char is not None:
                char = key.char.lower()
                if char == 'a':
                    press_state['shift_up'] = False
                elif char == 'z':
                    press_state['shift_down'] = False
            event_queue.put(press_state.copy())
        except Exception:
            pass

    with keyboard.Listener(on_press=on_press, on_release=on_release) as listener:
        listener.join()


class InputHandler:

    GEAR_MAX = 6
    GEAR_MIN = -1
    
    def __init__(self):
        self.state = {
            'accel': 0.0, # 0.0 ~ 1.0
            'brake': 0.0, # 0.0 ~ 1.0
            'steer': 0.0, # -1.0 ~ 1.0
            'gear':  1,   # -1 ~ 6
        }

        self.accel_pressed = False
        self.brake_pressed = False
        self.steer_left_pressed = False
        self.steer_right_pressed = False

        self._running = False
        self._process = None
        self._queue = mp.Queue()

    def shift_up(self):
        if self.state['gear'] < self.GEAR_MAX:
            self.state['gear'] += 1

    def shift_down(self):
        if self.state['gear'] > self.GEAR_MIN:
            self.state['gear'] -= 1

    def start(self):
        if not self._running:
            self._running = True
            self._process = mp.Process(
                target=_pynput_listener_process, 
                args=(self._queue,), 
                daemon=True
            )
            self._process.start()
            print("[InputHandler] Multiprocessing pynput listener started successfully.")

    def stop(self):
        self._running = False
        if self._process is not None and self._process.is_alive():
            self._process.terminate()
            print("[InputHandler] Multiprocessing pynput listener process stopped.")

    def update(self):
        while not self._queue.empty():
            try:
                msg = self._queue.get_nowait()

                self.accel_pressed = msg['up']
                self.brake_pressed = msg['down']
                self.steer_left_pressed = msg['left']
                self.steer_right_pressed = msg['right']

                if msg['shift_up']:
                    self.shift_up()
                if msg['shift_down']:
                    self.shift_down()

            except Exception:
                break

        return self.state

    def get_action(self):
        return self.update()