import threading
from pynput import keyboard
import pygame

def detect_input():
    pygame.init()
    pygame.joystick.init()
    if pygame.joystick.get_count() > 0:
        print("Gamepad detected")
        return GamepadInput()
    else:
        print("No gamepad detected, using keyboard")
        return KeyboardInput()


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

    def shift_up(self):
        if self.state['gear'] < self.GEAR_MAX:
            self.state['gear'] += 1

    def shift_down(self):
        if self.state['gear'] > self.GEAR_MIN:
            self.state['gear'] -= 1

    def _listen(self):
        pass

    def start(self):
        self._running = True
        # Background thread that continuously listens for input and updates state
        self._thread = threading.Thread(target=self._listen, daemon=True)
        self._thread.start()

    def stop(self):
        self._running = False
        if self._thread is not None:
            # Wait until the thread terminates
            self._thread.join()
        pass

    def poll(self):
        pass


class KeyboardInput(InputHandler):

    def __init__(self):
        super().__init__()

    def _listen(self):
        with keyboard.Listener(on_press=self._on_press, on_release=self._on_release) as listener:
            while self._running:
                pass
            listener.stop()

    def _on_press(self, key):
        try:
            if key == keyboard.Key.up:
                self.state['accel'] = 1.0
            elif key == keyboard.Key.down:
                self.state['gear'] = -1
                self.state['accel'] = 1.0
            elif key == keyboard.Key.space:
                self.state['brake'] = 1.0
            elif key == keyboard.Key.right:
                self.state['steer'] = -1.0
            elif key == keyboard.Key.left:
                self.state['steer'] = 1.0
            elif key.char.lower() == 'a':
                self.shift_up()
            elif key.char.lower() == 'z':
                self.shift_down()
        except AttributeError:
            pass

    def _on_release(self, key):
        try:
            if key == keyboard.Key.up:
                self.state['accel'] = 0.0
            elif key == keyboard.Key.down:
                self.state['gear'] = 1
                self.state['accel'] = 0.0
            elif key == keyboard.Key.space:
                self.state['brake'] = 0.0
            elif key in (keyboard.Key.left, keyboard.Key.right):
                self.state['steer'] = 0.0
        except AttributeError:
            pass

class GamepadInput(InputHandler):

    def __init__(self):
        super().__init__()
        self.joystick = pygame.joystick.Joystick(0)
        self.joystick.init()
        self._prev_buttons = {1: False, 2: False, 9: False}

    def poll(self):
        # Must be called from the main thread on Mac
        pygame.event.pump()

        # Right stick X axis → steer (-1.0~ 1.0)
        self.state['steer'] = -self.joystick.get_axis(0)

        # ZL (Axis 4) → accel (0.0 ~ 1.0)
        self.state['accel'] = (self.joystick.get_axis(4) + 1) / 2

        # ZR (Axis 5) → brake (0.0 ~ 1.0)
        self.state['brake'] = (self.joystick.get_axis(5) + 1) / 2

        # L (Button 9) → reverse (gear = -1), release → back to 1st gear
        btn9_pressed = bool(self.joystick.get_button(9))
        if btn9_pressed:
            self.state['gear'] = -1
            self.state['accel'] = 1.0
        elif not btn9_pressed and self._prev_buttons[9]:
            self.state['gear'] = 1
        self._prev_buttons[9] = btn9_pressed

        # X (Button 2) → shift up (trigger once on press)
        btn2_pressed = bool(self.joystick.get_button(2))
        if btn2_pressed and not self._prev_buttons[2]:
            self.shift_up()
        self._prev_buttons[2] = btn2_pressed

        # B (Button 1) → shift down (trigger once on press)
        btn1_pressed = bool(self.joystick.get_button(1))
        if btn1_pressed and not self._prev_buttons[1]:
            self.shift_down()
        self._prev_buttons[1] = btn1_pressed