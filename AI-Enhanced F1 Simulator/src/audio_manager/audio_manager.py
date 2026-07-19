import platform
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
    """Priority-based audio manager supporting clean cross-platform English TTS."""

    def __init__(self, cooldown_seconds=COOLDOWN_SECONDS):
        self.project_root = Path(__file__).resolve().parent.parent.parent
        self.cooldown_seconds = cooldown_seconds
        self._pygame_available = self._init_pygame()
        
        # Cross-platform environment detection
        self.os_type = platform.system()
        self.active_voice_id = None
        self._say_command = shutil.which("say")
        self.tts_engine = None

        # Environment configuration initialization
        if self.os_type == "Windows":
            try:
                import pyttsx3
                self.tts_engine = pyttsx3.init()
                voices = self.tts_engine.getProperty('voices')
                
                # Match native English voice packs (e.g., Zira, David)
                for voice in voices:
                    if "EN" in voice.id.upper() or "ENGLISH" in voice.name.upper():
                        self.active_voice_id = voice.id
                        break
                        
                if self.active_voice_id:
                    self.tts_engine.setProperty('voice', self.active_voice_id)
                else:
                    print("Windows Voice Alert: Native English TTS pack not found. Falling back to default.")
            except ImportError:
                print("Dependency Error: pyttsx3 missing. Execute 'pip install pyttsx3' on Windows.")
        # macOS
        elif self.os_type == "Darwin" and self._say_command:
            # Prioritize standard crisp English voice models
            available_mac_voices = ["Samantha", "Daniel", "Alex"]
            self.active_voice_id = "Samantha"
            try:
                result = subprocess.run([self._say_command, "-v", "?"], capture_output=True, text=True, check=False)
                for candidate in available_mac_voices:
                    if candidate in result.stdout:
                        self.active_voice_id = candidate
                        break
            except Exception:
                pass

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

    def _message_for_tag(self, tag):
        if tag in BUILTIN_ALERTS:
            return BUILTIN_ALERTS[tag]["message"]
        return tag.replace("_", " ")

    def _message_for_error(self, error):
        tag = error.get("tag")
        if tag in BUILTIN_ALERTS:
            return BUILTIN_ALERTS[tag]["message"]
        return error.get("coaching_hint") or error.get("message") or (tag or "Driving alert").replace("_", " ")

    def _resolve_priority(self, priority):
        return PRIORITY_ORDER.get(priority, PRIORITY_ORDER["normal"])

    def _clear_queue(self):
        while not self._audio_queue.empty():
            try:
                self._audio_queue.get_nowait()
                self._audio_queue.task_done()
            except queue.Empty:
                break

    # Introduce a global timestamp and expiration mechanism during Enqueue or Play.
    def _enqueue(self, payload, description, priority="normal", interrupt=False, mode="sound"):
        # Forcefully bind a creation timestamp when creating the event.
        created_time = time.monotonic()
        
        if interrupt:
            self.stop_all()
            self._clear_queue()

        with self._lock:
            self._counter += 1
            # Package created_time into the data structure.
            item = (self._resolve_priority(priority), self._counter, mode, payload, description, created_time)
            self._audio_queue.put(item)
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
        if self.os_type == "Darwin" and self._say_command:
            subprocess.run(
                [self._say_command, "-v", self.active_voice_id, text],
                timeout=SPEECH_TIMEOUT_SECONDS,
                check=False
            )
            return True
        elif self.os_type == "Windows" and self.tts_engine:
            try:
                self.tts_engine.say(text)
                self.tts_engine.runAndWait()
                return True
            except Exception as error:
                print(f"Windows TTS execution failed: {error}")
                return False
        
        print(f"Speech fallback platform unavailable. Alert text: {text}")
        return False

    def _audio_loop(self):
        while self._running:
            try:
                _, _, mode, payload, description, created_time = self._audio_queue.get(timeout=0.1)
            except queue.Empty:
                continue

            # Drop and skip the event if the system is already shut down.
            if not self._running:
                self._audio_queue.task_done()
                break

            # Drop the event if it has been in the queue for more than 1.5 seconds.
            if time.monotonic() - created_time > 1.5:
                print(f"[Timeout Dropped] {description} is too old ({time.monotonic() - created_time:.2f}s old), skipping.")
                self._audio_queue.task_done()
                continue

            try:
                if mode == "speech":
                    print(f"Speaking audio: {description}")
                    if self._running:
                        self._speak_text(payload)
                else:
                    if self._running and self._pygame_available:
                        channel = payload.play()
                        print(f"Playing audio: {description}")

                        if channel:
                            while channel.get_busy() and self._running:
                                time.sleep(0.05)
                            if not self._running:
                                self.stop_all()

            except Exception as error:
                print(f"Audio playback failed for {description}: {error}")
            finally:
                self._audio_queue.task_done()

    def play(self, tag, priority=None, interrupt=None):
        """Play a predefined audio clip or string directly by tag."""
        mapping = BUILTIN_ALERTS.get(tag)
        if mapping is None:
            mapping = {"priority": "normal", "interrupt": False, "message": self._message_for_tag(tag)}

        priority = priority or mapping.get("priority", "normal")
        interrupt = interrupt if interrupt is not None else mapping.get("interrupt", False)

        now = time.time()
        if now - self._last_played.get(tag, 0) < self.cooldown_seconds:
            return False
        self._last_played[tag] = now

        return self._enqueue_speech(
            mapping.get("message") or self._message_for_tag(tag),
            tag,
            priority=priority,
            interrupt=interrupt,
        )

    def play_sound(self, file_path, priority="slow", interrupt=False, fallback_text=None):
        """Play a WAV file, or fall back to native speech."""
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
        """Handle incoming error dictionaries cleanly without templates."""
        tag = error.get("tag")
        audio_file = error.get("audio_file")
        priority = error.get("priority", "normal")
        interrupt = error.get("interrupt", False)

        if tag and tag in BUILTIN_ALERTS:
            return self.play(tag, priority=priority, interrupt=interrupt)

        if audio_file:
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

        print(f"Invalid error packet structural footprint: {error}")
        return False

    def wait_until_idle(self, timeout=None):
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
        """Terminate audio systems immediately and scrub remaining voice jobs."""
        print("[Audio] Initiating emergency forced shutdown...")
        self._running = False
        self.stop_all()
        self._clear_queue()
        
        if self.os_type == "Darwin" and self._say_command:
            try:
                subprocess.run(["killall", "say"], capture_output=True, check=False)
                print("[Audio] Terminated active background 'say' instances.")
            except Exception as e:
                print(f"[Audio Warning] Unable to signal process shutdown: {e}")

        if self._pygame_available:
            try:
                pygame.mixer.quit()
                self._pygame_available = False
            except Exception as e:
                print(f"[Audio Warning] Error wrapping down pygame engine: {e}")
                
        print("[Audio] Audio manager successfully forced to shut up.")


if __name__ == "__main__":
    audio_manager = AudioManager()
    audio_manager.play_text("System architecture running smoothly.", priority="slow")
    time.sleep(2)
    audio_manager.play("brake_now", priority="urgent", interrupt=True)
    time.sleep(2)
    audio_manager.shutdown()