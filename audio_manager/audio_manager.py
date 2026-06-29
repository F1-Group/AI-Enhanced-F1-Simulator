import json
import queue
import threading
import time
from pathlib import Path

import pygame

DEFAULT_TEMPLATE_PATH = "mock/error_template.json"
COOLDOWN_SECONDS = 4.0

PRIORITY_ORDER = {
    "urgent": 0,
    "high": 1,
    "normal": 2,
    "low": 3,
    "slow": 4,
}


class AudioManager:
    """Priority-based audio manager for fast and slow coaching layers."""

    def __init__(self, template_path=DEFAULT_TEMPLATE_PATH, cooldown_seconds=COOLDOWN_SECONDS):
        self.project_root = Path(__file__).resolve().parent.parent
        self.template_path = self.project_root / template_path
        self.cooldown_seconds = cooldown_seconds

        pygame.mixer.init()

        self._errors = self._load_errors()
        self._sounds = {}
        self._last_played = {}

        self._audio_queue = queue.PriorityQueue()
        self._counter = 0
        self._lock = threading.Lock()
        self._running = True

        self._worker = threading.Thread(target=self._audio_loop, daemon=True)
        self._worker.start()

    def _load_errors(self):
        if not self.template_path.exists():
            raise FileNotFoundError(f"Error template not found: {self.template_path}")

        with open(self.template_path, "r", encoding="utf-8") as f:
            template = json.load(f)

        if "errors" not in template:
            raise ValueError(f"Error template missing 'errors' key: {self.template_path}")

        return {error["tag"]: error for error in template["errors"]}

    def _get_sound(self, tag):
        if tag not in self._sounds:
            audio_file = self._errors[tag]["audio_file"]
            audio_path = self.project_root / audio_file

            if not audio_path.exists():
                raise FileNotFoundError(f"Audio file not found for tag '{tag}': {audio_path}")

            self._sounds[tag] = pygame.mixer.Sound(str(audio_path))

        return self._sounds[tag]

    def _resolve_priority(self, priority):
        return PRIORITY_ORDER.get(priority, PRIORITY_ORDER["normal"])

    def _clear_queue(self):
        while not self._audio_queue.empty():
            try:
                self._audio_queue.get_nowait()
                self._audio_queue.task_done()
            except queue.Empty:
                break

    def _enqueue(self, sound, description, priority="normal", interrupt=False):
        if interrupt:
            self.stop_all()
            self._clear_queue()

        with self._lock:
            self._counter += 1
            item = (self._resolve_priority(priority), self._counter, sound, description)
            self._audio_queue.put(item)

        print(f"Queued audio: {description} | priority={priority} | interrupt={interrupt}")
        return True

    def _audio_loop(self):
        while self._running:
            try:
                _, _, sound, description = self._audio_queue.get(timeout=0.1)
            except queue.Empty:
                continue

            try:
                channel = sound.play()
                print(f"Playing audio: {description}")

                if channel:
                    while channel.get_busy() and self._running:
                        time.sleep(0.05)

            except pygame.error as error:
                print(error)
            finally:
                self._audio_queue.task_done()

    def play(self, tag, priority=None, interrupt=None):
        """Play a predefined audio clip by error tag."""
        if tag not in self._errors:
            print(f"No audio mapping found for tag: {tag}")
            return False

        error = self._errors[tag]
        priority = priority or error.get("priority", "normal")
        interrupt = interrupt if interrupt is not None else error.get("interrupt", False)

        now = time.time()
        if now - self._last_played.get(tag, 0) < self.cooldown_seconds:
            return False

        try:
            sound = self._get_sound(tag)
            self._last_played[tag] = now
            return self._enqueue(sound, tag, priority=priority, interrupt=interrupt)
        except (pygame.error, FileNotFoundError) as error:
            print(error)
            return False

    def play_sound(self, file_path, priority="slow", interrupt=False):
        """Play a Team 3 generated .wav file."""
        audio_path = Path(file_path)
        if not audio_path.is_absolute():
            audio_path = self.project_root / audio_path

        if not audio_path.exists():
            print(f"Audio file not found: {audio_path}")
            return False

        try:
            sound = pygame.mixer.Sound(str(audio_path))
            return self._enqueue(sound, str(audio_path), priority=priority, interrupt=interrupt)
        except pygame.error as error:
            print(error)
            return False

    def play_error(self, error):
        """Play from a real error JSON produced by Team 2's detection algorithm."""
        tag = error.get("tag")
        audio_file = error.get("audio_file")
        priority = error.get("priority", "normal")
        interrupt = error.get("interrupt", False)

        if tag and tag in self._errors:
            return self.play(tag, priority=priority, interrupt=interrupt)

        if audio_file:
            return self.play_sound(audio_file, priority=priority, interrupt=interrupt)

        print(f"Invalid error object, missing tag or audio_file: {error}")
        return False

    def stop_all(self):
        pygame.mixer.stop()

    def shutdown(self):
        self._running = False
        self.stop_all()
        self._clear_queue()
        pygame.mixer.quit()


if __name__ == "__main__":
    audio_manager = AudioManager()

    # Slow layer: long Granite-generated coaching audio
    audio_manager.play_sound("audio/granite_coaching_output.wav", priority="slow")
    time.sleep(1)

    # Fast layer: urgent command interrupts slow layer
    audio_manager.play("T1_late_braking", priority="urgent", interrupt=True)
    time.sleep(3)

    audio_manager.shutdown()