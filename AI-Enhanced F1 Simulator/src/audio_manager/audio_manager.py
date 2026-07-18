import json
import queue
import shutil
import subprocess
import threading
import time
from pathlib import Path

try:
    import pygame
except ImportError:
    pygame = None

DEFAULT_TEMPLATE_PATH = Path(__file__).resolve().parent.parent.parent / "mock" / "error_template.json"
COOLDOWN_SECONDS = 4.0
SPEECH_TIMEOUT_SECONDS = 30.0

BUILTIN_ALERTS = {
    "brake_now": {
        "message": "Brake now",
        "priority": "urgent",
        "interrupt": True,
    },
    "off_track": {
        "message": "You are off track",
        "priority": "high",
        "interrupt": False,
    },
}

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
        self.project_root = Path(__file__).resolve().parent.parent.parent
        template_path = Path(template_path)
        self.template_path = template_path if template_path.is_absolute() else self.project_root / template_path
        self.cooldown_seconds = cooldown_seconds
        self._pygame_available = self._init_pygame()
        self._say_command = shutil.which("say")

        self._errors = self._load_errors()
        self._sounds = {}
        self._last_played = {}

        self._audio_queue = queue.PriorityQueue()
        self._counter = 0
        self._lock = threading.Lock()
        self._running = True

        self._worker = threading.Thread(target=self._audio_loop, daemon=True)
        self._worker.start()

    def _init_pygame(self):
        if pygame is None:
            print("pygame is not installed; using speech fallback for audio alerts.")
            return False
        try:
            pygame.mixer.init()
            return True
        except pygame.error as error:
            print(f"pygame mixer unavailable; using speech fallback: {error}")
            return False

    def _load_errors(self):
        if not self.template_path.exists():
            raise FileNotFoundError(f"Error template not found: {self.template_path}")

        with open(self.template_path, "r", encoding="utf-8") as f:
            template = json.load(f)

        if "errors" not in template:
            raise ValueError(f"Error template missing 'errors' key: {self.template_path}")

        return {error["tag"]: error for error in template["errors"]}

    def _get_sound(self, tag):
        if not self._pygame_available:
            raise RuntimeError("pygame mixer is unavailable")
        if tag not in self._sounds:
            audio_file = self._errors[tag]["audio_file"]
            audio_path = self.project_root / audio_file

            if not audio_path.exists():
                raise FileNotFoundError(f"Audio file not found for tag '{tag}': {audio_path}")

            self._sounds[tag] = pygame.mixer.Sound(str(audio_path))

        return self._sounds[tag]

    def _message_for_tag(self, tag):
        if tag in BUILTIN_ALERTS:
            return BUILTIN_ALERTS[tag]["message"]
        if tag in self._errors:
            error = self._errors[tag]
            return error.get("coaching_hint") or error.get("message") or tag.replace("_", " ")
        return tag.replace("_", " ")

    def _message_for_error(self, error):
        tag = error.get("tag")
        if tag in BUILTIN_ALERTS:
            return BUILTIN_ALERTS[tag]["message"]
        return (
            error.get("coaching_hint")
            or error.get("message")
            or (tag or "Driving alert").replace("_", " ")
        )

    def _resolve_priority(self, priority):
        return PRIORITY_ORDER.get(priority, PRIORITY_ORDER["normal"])

    def _clear_queue(self):
        while not self._audio_queue.empty():
            try:
                self._audio_queue.get_nowait()
                self._audio_queue.task_done()
            except queue.Empty:
                break

    def _enqueue(self, payload, description, priority="normal", interrupt=False, mode="sound"):
        if interrupt:
            self.stop_all()
            self._clear_queue()

        with self._lock:
            self._counter += 1
            item = (self._resolve_priority(priority), self._counter, mode, payload, description)
            self._audio_queue.put(item)

        print(f"Queued audio: {description} | priority={priority} | interrupt={interrupt}")
        return True

    def _enqueue_speech(self, text, description, priority="normal", interrupt=False):
        clean_text = " ".join(str(text).split())
        if not clean_text:
            return False
        return self._enqueue(clean_text, description, priority=priority, interrupt=interrupt, mode="speech")

    def play_text(self, text, priority="slow", interrupt=False, cooldown_key=None):
        """Queue generated coaching text without creating a temporary file."""
        clean_text = " ".join(str(text).split())
        if not clean_text:
            return False
        if cooldown_key:
            now = time.monotonic()
            with self._lock:
                if now - self._last_played.get(cooldown_key, 0) < self.cooldown_seconds:
                    return False
                self._last_played[cooldown_key] = now
        return self._enqueue_speech(
            clean_text,
            cooldown_key or "generated coaching",
            priority=priority,
            interrupt=interrupt,
        )

    def _speak_text(self, text):
        if self._say_command:
            subprocess.run([self._say_command, text], timeout=SPEECH_TIMEOUT_SECONDS, check=False)
            return True
        try:
            import pyttsx3
        except ImportError:
            print(f"Speech fallback unavailable. Alert text: {text}")
            return False

        engine = pyttsx3.init()
        engine.say(text)
        engine.runAndWait()
        return True

    def _audio_loop(self):
        while self._running:
            try:
                _, _, mode, payload, description = self._audio_queue.get(timeout=0.1)
            except queue.Empty:
                continue

            try:
                if mode == "speech":
                    print(f"Speaking audio: {description}")
                    self._speak_text(payload)
                else:
                    channel = payload.play()
                    print(f"Playing audio: {description}")

                    if channel:
                        while channel.get_busy() and self._running:
                            time.sleep(0.05)

            except Exception as error:
                print(f"Audio playback failed for {description}: {error}")
            finally:
                self._audio_queue.task_done()

    def play(self, tag, priority=None, interrupt=None):
        """Play a predefined audio clip by error tag, falling back to speech."""
        mapping = self._errors.get(tag) or BUILTIN_ALERTS.get(tag)
        if mapping is None:
            mapping = {"priority": "normal", "interrupt": False, "message": self._message_for_tag(tag)}

        priority = priority or mapping.get("priority", "normal")
        interrupt = interrupt if interrupt is not None else mapping.get("interrupt", False)

        now = time.time()
        if now - self._last_played.get(tag, 0) < self.cooldown_seconds:
            return False
        self._last_played[tag] = now

        if tag in self._errors:
            try:
                sound = self._get_sound(tag)
                return self._enqueue(sound, tag, priority=priority, interrupt=interrupt)
            except Exception as error:
                print(f"Falling back to speech for {tag}: {error}")

        return self._enqueue_speech(
            mapping.get("message") or self._message_for_tag(tag),
            tag,
            priority=priority,
            interrupt=interrupt,
        )

    def play_sound(self, file_path, priority="slow", interrupt=False, fallback_text=None):
        """Play a Team 3 generated .wav file, or speak fallback text."""
        audio_path = Path(file_path)
        if not audio_path.is_absolute():
            audio_path = self.project_root / audio_path

        if self._pygame_available and audio_path.exists():
            try:
                sound = pygame.mixer.Sound(str(audio_path))
                return self._enqueue(sound, str(audio_path), priority=priority, interrupt=interrupt)
            except Exception as error:
                print(f"Audio file playback failed for {audio_path}: {error}")
        else:
            print(f"Audio file unavailable: {audio_path}")

        if fallback_text:
            return self._enqueue_speech(fallback_text, str(audio_path), priority=priority, interrupt=interrupt)
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
            # Dynamic tags (e.g. T9_late_braking) share one clip per error
            # type, so apply the cooldown on the shared audio key to stop the
            # same clip spamming back-to-back from a single report.
            cooldown_key = error.get("audio_key") or audio_file
            now = time.time()
            if now - self._last_played.get(cooldown_key, 0) < self.cooldown_seconds:
                return False
            self._last_played[cooldown_key] = now
            return self.play_sound(
                audio_file,
                priority=priority,
                interrupt=interrupt,
                fallback_text=self._message_for_error(error),
            )

        fallback_text = self._message_for_error(error)
        if fallback_text:
            cooldown_key = tag or fallback_text
            now = time.time()
            if now - self._last_played.get(cooldown_key, 0) < self.cooldown_seconds:
                return False
            self._last_played[cooldown_key] = now
            return self._enqueue_speech(fallback_text, cooldown_key, priority=priority, interrupt=interrupt)

        print(f"Invalid error object, missing tag or audio_file: {error}")
        return False

    def wait_until_idle(self, timeout=None):
        """Block until every queued clip has finished playing.

        Meant for graceful shutdown after a session ends (so the final
        coaching clips are not cut off) - never call it from a real-time
        path. Returns False if the timeout expired first.

        unfinished_tasks only reaches 0 after the worker's task_done(),
        which runs once a clip's playback has fully completed, so this also
        covers a clip that was already dequeued and is still playing.
        """
        deadline = None if timeout is None else time.time() + timeout
        while self._running:
            mixer_busy = self._pygame_available and pygame.mixer.get_busy()
            if self._audio_queue.unfinished_tasks == 0 and not mixer_busy:
                return True
            if deadline is not None and time.time() >= deadline:
                return False
            time.sleep(0.1)
        return False

    def stop_all(self):
        if self._pygame_available:
            pygame.mixer.stop()

    def shutdown(self):
        self._running = False
        self.stop_all()
        self._clear_queue()
        if self._pygame_available:
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
